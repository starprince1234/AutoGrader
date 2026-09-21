"""cgroup v2 逐用例资源读数（`ADR-0010` 决策 3 / `CHG-0012` 的 `resource`）。

**为什么需要它**：项目的资源账本（单并发作业 ≤96 MB RSS / 0.45 vCPU）在本次改动之前只是一组
**配置声明**——`render_nsjail_config` 写下 `cgroup_mem_max`/`cgroup_pids_max`/`cgroup_cpu_ms_per_sec`，
但从来没有人回读。本模块把"限额声明"变成"实测读数"。

**为什么每个字段独立可空，且读不到就是 `None` 而不是 0**：见 `domain.evidence.CaseResource`
的说明——"强制了但没测量"与"测出来很小"是两件不同的事。

## 一个必须写明的实测事实（决定本模块的取样方式）

nsjail 的 cgroup v2 后端**在子进程被 reap 的那一刻就把该 cgroup 删除**：
`subprocess.cc::reapProc()` → `cgroup2::finishFromParent()` → `cgroup2.cc::removeCgroup()` → `rmdir(mount + "/NSJAIL." + child_pid)`。
也就是说，**"进程退出之后再回读"在 nsjail 上必然读不到任何东西**（目录已经不存在）。
逐用例 cgroup 的路径也不是 nsjail 自身的 pid，而是**被囚子进程**的 pid（`getCgroupPath` 用的是
`clone()` 的返回值），因此本模块按 `/proc/<nsjail_pid>/task/<nsjail_pid>/children` 解析子进程 pid，
再用候选目录是否存在来确认——**不做任何"猜一个目录名"的启发式**。

结论：唯一能取到读数的窗口是**用例执行期间**。因此本模块提供一个取样器（`CaseResourceSampler`），
在沙箱进程运行期间周期性读取，并在进程结束后再做**一次**收尾读取（若目录仍在——例如换用别的后端
或上游 nsjail 改变行为，收尾读取就能拿到真正的最终峰值）。

**诚实边界（写进文档，不只写在这里）**：取样得到的是"运行期间观察到的最大值"。对 `memory.peak`
与 `pids.peak` 这类单调高水位读数，它是真实峰值的一个**下界**；`cpu.stat` 是单调累计量，同理。
取样间隔越短越接近真值，但**永远不会**因为取样而高估。这是本机（Windows）无法验证的推断，
Linux 侧验收必须复测；未复测前不得把 `resource` 当成已验收的能力。

本模块不做任何平台判断：调用方只有在 `probe_sandbox` 判定可用之后才会执行到沙箱路径。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from autograder_b2.domain.evidence import CaseResource
from autograder_b2.runner.policy import RunnerPolicy

#: 取样间隔（秒）。取值权衡：更短更接近真实峰值，但每次取样要读 3 个 cgroup 文件。
CGROUP_SAMPLE_INTERVAL_SECONDS = 0.05

#: cgroup v2 里被读取的文件名（内核接口名，不是可配置项）。
MEMORY_PEAK_FILE = "memory.peak"
CPU_STAT_FILE = "cpu.stat"
PIDS_PEAK_FILE = "pids.peak"

#: nsjail 创建逐用例 cgroup 时的目录名前缀（`cgroup2.cc::getCgroupPath`）。
NSJAIL_CGROUP_PREFIX = "NSJAIL."

#: B2 的 `resource_limit_hit` → 契约 `case_resource.limit_hit` 的映射。
#:
#: 契约枚举是 `memory|cpu|pids|wall|output`，因此 `processes → pids`；而 B2 的 `files`
#: （产出文件数超限）**没有对应取值**：它是执行后计数判定，不是"某个被强制执行的限额结束了本用例"，
#: 按契约对该字段的定义（null = 没有限额命中）如实映射为 `None`，具体限制项由
#: `case_evidence.resource_limit_hit` 与 `explanation` 表达。
LIMIT_HIT_BY_RESOURCE_LIMIT: dict[str, str | None] = {
    "memory": "memory",
    "cpu": "cpu",
    "wall": "wall",
    "processes": "pids",
    "output": "output",
    "files": None,
}


@dataclass(frozen=True, slots=True)
class CgroupV2Peaks:
    """一台宿主一次取样能读到的 cgroup v2 读数（每一项独立可空）。"""

    memory_peak_bytes: int | None = None
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None
    pids_peak: int | None = None

    def merged_with(self, other: CgroupV2Peaks) -> CgroupV2Peaks:
        """按最大值合并两次取样（`memory.peak`/`pids.peak`/`cpu.stat` 都是单调量，取最大即"观察到的峰值"）。"""
        return CgroupV2Peaks(
            memory_peak_bytes=_max_optional(self.memory_peak_bytes, other.memory_peak_bytes),
            cpu_user_seconds=_max_optional(self.cpu_user_seconds, other.cpu_user_seconds),
            cpu_system_seconds=_max_optional(self.cpu_system_seconds, other.cpu_system_seconds),
            pids_peak=_max_optional(self.pids_peak, other.pids_peak),
        )

    @property
    def measured(self) -> bool:
        """是否真的读到了至少一项（用于区分"测到很小"与"没测量"）。"""
        return any(
            value is not None
            for value in (
                self.memory_peak_bytes,
                self.cpu_user_seconds,
                self.cpu_system_seconds,
                self.pids_peak,
            )
        )


def _max_optional(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _default_read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def parse_cpu_stat(text: str) -> tuple[float | None, float | None]:
    """解析 cgroup v2 `cpu.stat` → `(user_seconds, system_seconds)`；缺项为 `None`。

    只认 `user_usec`/`system_usec` 两行：其它行（`usage_usec`、`nr_periods` 等）不是本字段要表达的
    事实，解析它们会让"读到的到底是什么"变得含糊。解析不出即 `None`（不是 0）。
    """
    microseconds: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            microseconds[parts[0]] = int(parts[1])
        except ValueError:
            continue
    user = microseconds.get("user_usec")
    system = microseconds.get("system_usec")
    return (
        None if user is None else user / 1_000_000,
        None if system is None else system / 1_000_000,
    )


def _read_int(directory: Path, name: str, reader: Callable[[Path], str]) -> int | None:
    try:
        return int(reader(directory / name).strip())
    except (OSError, ValueError):
        return None


def read_cgroup_v2_peaks(
    directory: Path, *, read_text: Callable[[Path], str] | None = None
) -> CgroupV2Peaks:
    """读取一个 cgroup 目录的峰值读数（任一文件缺失/不可读 ⇒ 该项 `None`，绝不写 0）。"""
    reader = read_text if read_text is not None else _default_read_text
    memory_peak = _read_int(directory, MEMORY_PEAK_FILE, reader)
    pids_peak = _read_int(directory, PIDS_PEAK_FILE, reader)
    try:
        cpu_text = reader(directory / CPU_STAT_FILE)
    except OSError:
        cpu_user = cpu_system = None
    else:
        cpu_user, cpu_system = parse_cpu_stat(cpu_text)
    return CgroupV2Peaks(
        memory_peak_bytes=memory_peak,
        cpu_user_seconds=cpu_user,
        cpu_system_seconds=cpu_system,
        pids_peak=pids_peak,
    )


def _default_children_of(pid: int) -> tuple[int, ...]:
    """读取 `/proc/<pid>/task/<pid>/children`（内核提供直接子进程清单）。"""
    try:
        text = Path(f"/proc/{pid}/task/{pid}/children").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return ()
    return tuple(int(item) for item in text.split() if item.isdigit())


def case_cgroup_directory(
    policy: RunnerPolicy,
    *,
    sandbox_pid: int,
    mount: Path | str | None = None,
    children_of: Callable[[int], tuple[int, ...]] | None = None,
    is_directory: Callable[[Path], bool] | None = None,
) -> Path | None:
    """定位本次用例的 cgroup 目录：`{mount}/NSJAIL.<被囚子进程 pid>`。

    依据（nsjail 源码，不是猜测）：`cgroup2.cc::getCgroupPath()` 用**被囚子进程**的 pid 拼目录名，
    而该 pid 是 nsjail `clone()` 的返回值——宿主只能从 `/proc/<nsjail_pid>/.../children` 读到它。
    对每个候选子进程 pid，**要求目录真实存在**才返回，因此不会把"别的并发作业的 cgroup"当成自己的。

    nsjail 在子进程被 reap 时删除该目录（见模块 docstring），因此本函数在用例结束后通常返回 `None`——
    那是**事实**，不是错误：`None` 一路走到 `CaseResource` 的 `None` 字段。
    """
    root = Path(mount) if mount is not None else Path(policy.cgroup_v2_mount)
    children = children_of if children_of is not None else _default_children_of
    check = is_directory if is_directory is not None else (lambda path: path.is_dir())
    for child_pid in children(sandbox_pid):
        candidate = root / f"{NSJAIL_CGROUP_PREFIX}{child_pid}"
        if check(candidate):
            return candidate
    return None


class CaseResourceSampler:
    """在沙箱进程运行期间取样 cgroup 读数，并在结束后合并成一次读数。

    用法（`runner.run_case` 内的唯一调用方）：

    ```python
    sampler = CaseResourceSampler(policy=policy, sandbox_pid=process.pid)
    sampler.start()
    try:
        ...等待/清理进程树...
    finally:
        peaks = sampler.stop()
    ```

    `stop()` 会 join 取样线程、再做**一次**收尾读取（目录还在就取到最终值），并返回合并结果。
    取样线程内的非 `OSError` 异常会被暂存并在 `stop()` 重新抛出——cgroup 读取里的意外异常属缺陷，
    不得被静默吞掉（ADR-0010 要求的 fail-soft 只覆盖"读不到"，不覆盖"读错"）。
    """

    def __init__(
        self,
        *,
        policy: RunnerPolicy,
        sandbox_pid: int,
        interval: float = CGROUP_SAMPLE_INTERVAL_SECONDS,
        locate: Callable[[], Path | None] | None = None,
        read: Callable[[Path], CgroupV2Peaks] = read_cgroup_v2_peaks,
    ) -> None:
        self._read = read
        self._interval = interval
        self._directory: Path | None = None
        self._peaks = CgroupV2Peaks()
        self._failure: BaseException | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        mount = Path(policy.cgroup_v2_mount)
        #: 没有 cgroup v2 的宿主上不启动轮询（见 `cgroup_v2_present` 的说明）。
        self._supported = cgroup_v2_present(mount) if locate is None else True
        if locate is None:

            def locate() -> Path | None:
                return case_cgroup_directory(policy, sandbox_pid=sandbox_pid, mount=mount)

        self._locate = locate
        self._thread = threading.Thread(
            target=self._sample_until_stopped, name="b2-cgroup-sampler", daemon=True
        )

    def start(self) -> None:
        if not self._supported:
            return
        self._thread.start()

    def stop(self) -> CgroupV2Peaks:
        """停止取样并返回观察到的峰值（含一次收尾读取）。"""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=self._interval * 100)
        if self._supported:
            self._sample_once()
        if self._failure is not None:
            raise self._failure
        return self._peaks

    def _sample_until_stopped(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample_once()
            except BaseException as error:  # noqa: BLE001 - 暂存后由 stop() 重新抛出
                self._failure = error
                return
            self._stop.wait(self._interval)

    def _sample_once(self) -> None:
        if self._directory is None:
            self._directory = self._locate()
            if self._directory is None:
                return
        try:
            observed = self._read(self._directory)
        except OSError:
            # 目录在两次取样之间被 nsjail 删除（子进程被 reap）是**正常路径**，不是错误。
            # 本轮观察值已经拿到，不回退、不清零。
            self._directory = None
            return
        with self._lock:
            self._peaks = self._peaks.merged_with(observed)


def case_resource_from_peaks(
    peaks: CgroupV2Peaks, *, wall_seconds: float | None, limit_hit: str | None
) -> CaseResource:
    """把取样读数与 B2 自己测得的墙钟时间合成本次用例的资源读数。

    `limit_hit` 由调用方从 B2 的 `resource_limit_hit` 映射而来（见 `LIMIT_HIT_BY_RESOURCE_LIMIT`）。
    """
    return CaseResource(
        memory_peak_bytes=peaks.memory_peak_bytes,
        cpu_user_seconds=peaks.cpu_user_seconds,
        cpu_system_seconds=peaks.cpu_system_seconds,
        pids_peak=peaks.pids_peak,
        wall_seconds=wall_seconds,
        limit_hit=limit_hit,
    )


def limit_hit_for(resource_limit_hit: str | None) -> str | None:
    """B2 限制项 → 契约 `limit_hit`；未知取值按"无强制限额命中"处理并保持 `None`。"""
    if resource_limit_hit is None:
        return None
    return LIMIT_HIT_BY_RESOURCE_LIMIT.get(resource_limit_hit)


def replace_peaks(peaks: CgroupV2Peaks, **changes: object) -> CgroupV2Peaks:
    """`CgroupV2Peaks` 的显式替换入口（测试用它构造"只缺 memory.peak"这类宿主形态）。"""
    return replace(peaks, **changes)  # type: ignore[arg-type]


def cgroup_v2_present(mount: Path | str) -> bool:
    """宿主是否真的挂了 cgroup v2（读得到 `cgroup.controllers` 即视为存在）。

    用途：取样器在**没有** cgroup v2 的宿主上根本不启动轮询线程——"读不到"这件事不需要每秒 20 次
    去确认；`resource` 的字段照旧是 `None`。
    """
    try:
        return bool(Path(mount, "cgroup.controllers").read_text(encoding="utf-8").strip())
    except OSError:
        return False


__all__ = [
    "CGROUP_SAMPLE_INTERVAL_SECONDS",
    "CPU_STAT_FILE",
    "LIMIT_HIT_BY_RESOURCE_LIMIT",
    "MEMORY_PEAK_FILE",
    "NSJAIL_CGROUP_PREFIX",
    "PIDS_PEAK_FILE",
    "CaseResourceSampler",
    "CgroupV2Peaks",
    "case_cgroup_directory",
    "case_resource_from_peaks",
    "cgroup_v2_present",
    "limit_hit_for",
    "parse_cpu_stat",
    "read_cgroup_v2_peaks",
    "replace_peaks",
]
