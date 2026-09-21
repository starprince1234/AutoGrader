"""Linux nsjail 沙箱验收：在真实 Linux 内核上**实测**隔离与资源限制是否生效。

所属：AutoGrader B2 · 大类 5「Linux 沙箱验收」首项。
决策依据：`docs/adr/ADR-0002-sandbox-execution-policy.md`（nsjail + cgroup v2 + seccomp、
禁网、只读根、非 root、超时清理整个进程树）与
`MAIN/docs/adr/ADR-0004-sandbox-technology-selection.md`（技术选型；不可用即拒绝、绝不降级）。

**独立性**：本模块自带一份最小 nsjail 策略（`_policy_text`），不导入
`src/autograder_b2/runner/**`，因此在 runner 落地前即可证明隔离真实生效。runner 落地后的接线
由 lead 负责。唯一的例外是文件末尾显式标注的**集成项**（两个，都**只读** runner 生成的策略，
绝不修改 runner）：

- `test_i1_runner_generated_config_is_accepted_by_real_nsjail`：校准字段名与 seccomp 允许清单；
- `test_i2_nsjail_log_separates_diagnostics_from_student_streams`：证明 `build_argv` 必填的 `--log`
  确实把 nsjail 自有诊断与学生流物理分开（沙箱侧归因的可信边界由这份日志提供）。

运行方式（两种，任选其一）：

1. 原生 Linux + 已装 nsjail::

       pytest tests/linux/test_nsjail_acceptance.py -v -s

2. Windows 宿主经特权 Linux 容器（本验收实际使用的方式，见 `verify/linux-nsjail/README.md`）::

       $env:AUTOGRADER_B2_NSJAIL_VIA_DOCKER = '1'
       uv run pytest tests/linux/test_nsjail_acceptance.py -v -s

   驱动模式只用一个**固定参数数组**调用 `docker run`（`shell=False`），容器内跑同一份用例。

判定口径：每条验收结论只有三种取值——`通过` / `失败` / `无法执行`。所有判定都来自真实执行的
退出码 / 信号 / stdout / `/proc` 观测；拿不到证据一律 fail 并写明原因，**绝不**把「配置写了」
当成「限制生效」。

已知边界（**实测**得到，不是推测）：

* nsjail 3.4 的 `config.proto` 没有 stdout/stderr 大小选项，输出上限只能由读取侧强制，
  见 `test_05d`。
* `rlimit_nproc_type` 的 proto 默认值是 `SOFT`，只写 `rlimit_nproc` 不会生效，见 `_policy_text`。
* `uidmap.outside_id` 为空串表示「当前 uid」；nsjail 以 root 运行时会把 ns 内非 root 映射到
  **全局 uid 0**（假非 root），见 `test_04` 的外层 `/proc` 复核。
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

# --- 平台判定与默认 skip ---------------------------------------------------------------

_LINUX = sys.platform.startswith("linux")

#: 宿主驱动模式开关：Windows 上经 docker 调用容器内同一份验收。
_DRIVER_ENV = "AUTOGRADER_B2_NSJAIL_VIA_DOCKER"
_IMAGE_ENV = "AUTOGRADER_B2_NSJAIL_IMAGE"
_DEFAULT_IMAGE = "autograder-b2-nsjail:local"
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 驱动模式与容器内模式互斥：容器内是 Linux，永远走真实断言路径。
_DRIVER_MODE = (os.environ.get(_DRIVER_ENV) == "1") and not _LINUX

if not _LINUX and not _DRIVER_MODE:
    pytest.skip(
        "非 Linux 宿主：nsjail 依赖 Linux 命名空间与 cgroup v2，Windows 上无法执行真实隔离验收"
        "（ADR-0004「后果」节：仅 Linux 可验收）。在 Windows 上用容器跑：设 "
        f"{_DRIVER_ENV}=1 —— 见 verify/linux-nsjail/README.md。",
        allow_module_level=True,
    )

# --- 沙箱常量 ---------------------------------------------------------------------------

#: 沙箱内工作区路径（唯一可写路径）。
_SANDBOX_WORK = "/workspace"
#: ns 内 uid/gid：显式写死两侧非 0（见 `_policy_text` 与 test_04 的外层复核）。
_JAIL_UID = 99999
_JAIL_GID = 99999
#: nsjail 的 `--` 之后要执行的解释器。
_PYTHON = os.environ.get("AUTOGRADER_B2_NSJAIL_PYTHON") or "/usr/local/bin/python3"
_CGROUP_V2 = "/sys/fs/cgroup"

#: 容器本地临时根。**不**用 pytest 的 tmp_path：`tests/conftest.py` 把临时根固定在仓库内
#: （`<repo>/.pytest-tmp`），而经 Docker 挂载进容器的仓库卷是 Windows 共享卷（9p/virtiofs），
#: 其 uid 与权限语义不保证，nsjail 把它 bind 给 uid 99999 后可能无法写入。用容器本地 /tmp
#: （overlayfs）才能真实反映「工作区可写」。
_LOCAL_TMP = Path("/tmp")


# --- 证据登记（验收矩阵的机器可读来源） --------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Evidence:
    """一条验收证据：项名 / 命令 / 输出 / 判定。"""

    item: str
    command: str
    output: str
    verdict: str


_EVIDENCE: list[_Evidence] = []
_PASS = "通过"
_FAIL = "失败"
#: 「无法执行」用于环境不具备时的显式记录（模块 skip 与 fixture skip 即属此类）。
_UNAVAILABLE = "无法执行"


def _record(item: str, command: str, output: str, verdict: str) -> None:
    """登记一条证据。**先登记再断言**：用例失败时矩阵仍保留现场。"""
    _EVIDENCE.append(_Evidence(item=item, command=command, output=output, verdict=verdict))


def _one_line(text: str, limit: int = 700) -> str:
    """把多行输出压成单行并截断（只为矩阵可读；不改判定依据）。"""
    flat = " | ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _render_matrix(rows: list[_Evidence]) -> str:
    """渲染证据矩阵：**每一项恰好一个物理行**（宿主侧按行解析，见 `_parse_matrix`）。

    因此命令列也必须 `_one_line` 展平——矩阵的契约是"一行一项"，不是"字段里可以带换行"。
    历史缺陷：早期只展平了输出列，某条命令里带真实换行（I2 同时跑两种情形）时，宿主侧解析器
    就永远读不到那一项 ⇒ 驱动模式断言失败而容器内全绿（2026-09-16 独立验证实测，已修）。
    """
    lines = [
        "",
        "===== B2 Linux nsjail 验收实测矩阵 =====",
        f"判定取值：{_PASS} / {_FAIL} / {_UNAVAILABLE}",
        "| 项 | 命令 | 输出 | 判定 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        cells = (row.item, _one_line(row.command), _one_line(row.output), row.verdict)
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    lines.append("")
    return "\n".join(lines)


@pytest.fixture(scope="session", autouse=True)
def _emit_evidence_matrix() -> Iterator[None]:
    """收尾把实测矩阵打到 stdout（容器驱动模式用 `-s` 即可在日志里看到）。"""
    yield
    if _EVIDENCE:
        print(_render_matrix(_EVIDENCE))


# --- 观测工具（只读外部事实，不做任何假设） ----------------------------------------------


def _scan_proc_for_marker(marker: str, *, exclude: bytes | None = None) -> list[int]:
    """在当前 PID 命名空间里找出 cmdline 含 `marker` 的进程（残留检测的唯一依据）。

    `exclude` 用来排除**不该算作被囚进程**的进程：nsjail 自身的 cmdline 里也带着脚本路径
    （`nsjail --config ... -- python3 /workspace/<marker>.py`），若不过滤就会把 nsjail
    （uid 0）误当成被囚进程，使 test_04 的"外层 uid"核验得出错误结论（本机实测踩过）。
    """
    needle = marker.encode()
    found: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                cmdline = handle.read()
        except OSError:
            continue
        if needle not in cmdline:
            continue
        if exclude is not None and exclude in cmdline:
            continue
        found.append(int(entry))
    return sorted(found)


def _proc_uid_from_outside(pid: int) -> tuple[int, ...] | None:
    """读 `/proc/<pid>/status` 的 `Uid:` 行——**外层命名空间视角**的真实 uid。

    这是识别「假非 root」的关键：ns 内 `os.geteuid()` 非 0，可能只是该 uid 未被映射而回落到
    overflow uid(65534)，进程在全局用户命名空间里仍是 root。必须从外层 `/proc` 复核。
    """
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("Uid:"):
                    values = tuple(int(part) for part in line.split()[1:5])
                    return values if len(values) == 4 else None
    except OSError:
        return None
    return None


def _signal_name(returncode: int) -> str:
    if returncode < 0:
        try:
            return f"SIG{signal.Signals(-returncode).name.removeprefix('SIG')}"
        except ValueError:  # pragma: no cover - 未知信号号
            return f"signal{-returncode}"
    return f"exit{returncode}"


def _run(argv: list[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    """固定参数数组执行（`shell=False` 恒成立；本模块**没有**任何 shell 字符串拼接）。

    显式 `encoding="utf-8"`：本模块的矩阵与断言含中文，而 Windows 宿主默认用控制台代码页
    （GBK/cp936）解码子进程输出，会把容器/nsjail 的 UTF-8 输出解成乱码、使矩阵解析失配
    （本机实测踩过；`tests/conftest.py` 对 alembic 用同一处置）。
    """
    return subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


# --- 策略与现场 -------------------------------------------------------------------------


def _policy_text(
    work: Path,
    *,
    time_limit: int = 8,
    rlimit_as_mib: int = 128,
    cgroup_mem_max_mib: int = 64,
    cpu_seconds: int = 4,
    max_processes: int = 16,
    cgroup_cpu_ms_per_sec: int = 1000,
) -> str:
    """最小 nsjail 策略（protobuf text）。字段名取自 nsjail 3.4 `config.proto`（权威）。

    三个易错点在此显式规避（均来自**读源码/实测**，不是猜测）：

    1. `rlimit_nproc_type` 的 proto 默认值是 `SOFT`（"沿用父进程软限制"），只写
       `rlimit_nproc: N` **不会**把限制设成 N；必须同时写 `rlimit_nproc_type: VALUE`。
       `rlimit_stack_type` 同理。
    2. **uidmap 的两条约束会互相冲突，必须写成两条**（本机逐变体实测得出）：
       nsjail 把被囚进程的 uid 设成**第一条** uidmap 的 `inside_id`；同时它自己要做 mount
       namespace 的挂载，必须在新建的 user namespace 里保持 root，也就是**调用者的 uid 必须
       也被映射**。只写一条会让其中一件事失败：

       * 只写 `99999 -> 99999`：进程 uid 对了，但 nsjail 挂载失败
         （`mkdir('/tmp/nsjail.0.root//workspace'): Permission denied` → `Couldn't mount`）。
       * 只写 `0 -> 0`（或顺序颠倒）：挂载成功，但进程 uid 是 0（非 root 失效）。

       正确写法是**非 root 在前、调用者 uid 在后**：
       `uidmap { 99999->99999 }` + `uidmap { 0->0 }`。
       注意 nsjail 对 `outside_id == 0` 的那条会打
       `Process will be UID/EUID=0 in the global user namespace` 警告——那是**针对第二条
       （0->0）条目**的，不代表被囚进程的 uid；因此不能拿这条警告当"假非 root"的判据，
       test_04 才从外层 `/proc` 复核真实 uid。
    3. nsjail 3.4 **没有** stdout 大小选项，输出上限只能在读取侧强制（见 test_05d）。
    """
    return f"""\
name: "autograder-b2-acceptance"
description: "independent minimal policy: prove isolation actually takes effect"
mode: ONCE
log_level: INFO
hostname: "autograder-sandbox"
cwd: "{_SANDBOX_WORK}"

# 禁网：独立 network namespace；iface_no_lo 使作业内连 lo 都不启用
clone_newnet: true
iface_no_lo: true

# 其余命名空间隔离
clone_newuser: true
clone_newns: true
clone_newpid: true
clone_newipc: true
clone_newuts: true
clone_newcgroup: true

# 丢弃 capability 并保持 NO_NEW_PRIVS 置位
keep_caps: false
disable_no_new_privs: false

# 非 root（顺序不可颠倒，理由见 docstring 第 2 条）：
#   第 1 条 -> 被囚进程的 uid/gid（两侧都是 99999，全局也不是 root）
#   第 2 条 -> 把调用者 uid/gid 0 也映射进 ns，nsjail 才能完成挂载
uidmap {{
  inside_id: "{_JAIL_UID}"
  outside_id: "{_JAIL_UID}"
  count: 1
}}
uidmap {{
  inside_id: "0"
  outside_id: "0"
  count: 1
}}
gidmap {{
  inside_id: "{_JAIL_GID}"
  outside_id: "{_JAIL_GID}"
  count: 1
}}
gidmap {{
  inside_id: "0"
  outside_id: "0"
  count: 1
}}

# 墙钟 / CPU / 地址空间 / 进程数 / 打开文件 / 文件大小
time_limit: {time_limit}
max_cpus: 1
rlimit_as: {rlimit_as_mib}
rlimit_as_type: VALUE
rlimit_core: 0
rlimit_core_type: VALUE
rlimit_cpu: {cpu_seconds}
rlimit_cpu_type: VALUE
rlimit_fsize: 8
rlimit_fsize_type: VALUE
rlimit_nofile: 64
rlimit_nofile_type: VALUE
rlimit_nproc: {max_processes}
rlimit_nproc_type: VALUE
rlimit_stack: 8
rlimit_stack_type: VALUE

# cgroup v2 限额（内存 / 进程数 / CPU 带宽）
cgroup_mem_max: {cgroup_mem_max_mib * 1024 * 1024}
cgroup_pids_max: {max_processes}
cgroup_cpu_ms_per_sec: {cgroup_cpu_ms_per_sec}
cgroupv2_mount: "{_CGROUP_V2}"
use_cgroupv2: true

# 不继承宿主环境
keep_env: false
envar: "LANG=C.UTF-8"
envar: "LC_ALL=C.UTF-8"
envar: "PATH=/usr/local/bin:/usr/bin:/bin"
envar: "PYTHONDONTWRITEBYTECODE=1"
envar: "PYTHONIOENCODING=utf-8"
envar: "HOME={_SANDBOX_WORK}"
envar: "TMPDIR={_SANDBOX_WORK}"

# /proc 在新 PID 命名空间内新挂（只读）
mount_proc: true

# 只读根：整棵根以 ro bind 进入沙箱，故 /etc、/tmp 均不可写
mount {{
  src: "/"
  dst: "/"
  is_bind: true
  rw: false
}}

# 工作区：唯一可写路径（必须在只读根之后挂载，否则被 ro 语义覆盖）
mount {{
  src: "{work.as_posix()}"
  dst: "{_SANDBOX_WORK}"
  is_bind: true
  rw: true
  nosuid: true
  nodev: true
}}
"""


@dataclass(frozen=True, slots=True)
class _Jail:
    """一次验收的沙箱现场：只读根 + 独立可写工作区 + 固定策略文件。"""

    nsjail: str
    work: Path
    config: Path

    def write_script(self, name: str, body: str) -> str:
        """写入待测脚本并返回**沙箱内**路径。"""
        path = self.work / name
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
        os.chmod(path, 0o644)
        return f"{_SANDBOX_WORK}/{name}"

    def host_path(self, sandbox_script: str) -> Path:
        """把沙箱内脚本路径还原成宿主/容器内路径（对照实验不经沙箱时需要它）。"""
        prefix = f"{_SANDBOX_WORK}/"
        if not sandbox_script.startswith(prefix):
            raise ValueError(f"not a sandbox path: {sandbox_script!r}")
        return self.work / sandbox_script[len(prefix):]

    def argv(self, script: str) -> list[str]:
        """固定参数数组（ADR-0002 决策 5）：`nsjail --config <cfg> -- python3 <script>`。"""
        return [self.nsjail, "--config", str(self.config), "--", _PYTHON, script]

    def run(
        self, script: str, *, timeout: float = 120.0
    ) -> tuple[subprocess.CompletedProcess, float]:
        """跑一个脚本，返回 (进程结果, 墙钟秒数)。"""
        started = time.monotonic()
        proc = _run(self.argv(script), timeout=timeout)
        return proc, time.monotonic() - started


@pytest.fixture(scope="session")
def nsjail_bin() -> str:
    """定位 nsjail；缺失即 skip 并给出确切原因（不伪报通过）。

    本 fixture 同时是**整组真实用例的开关**：非 Linux 宿主（宿主驱动模式）上没有 nsjail，
    依赖它的用例一律 skip，只留驱动用例真正执行。
    """
    found = shutil.which("nsjail")
    if found is None:
        pytest.skip(
            "本机/本容器内找不到 nsjail 可执行文件：请用 verify/linux-nsjail/Dockerfile 构建镜像"
            "（Debian 官方 apt 源没有 nsjail 包，镜像内为源码构建），或在原生 Linux 上安装 nsjail。"
        )
    version = _run([found, "--help"], timeout=30)
    head = (version.stdout or version.stderr).strip().splitlines()[:1]
    print(f"[env] nsjail={found} help_head={head}（nsjail 无 --version，故以 --help 为证）")
    return found


@pytest.fixture(scope="session")
def jail_root() -> Iterator[Path]:
    """验收现场根目录（容器本地 /tmp，理由见 `_LOCAL_TMP` 的说明）。"""
    base = _LOCAL_TMP if _LOCAL_TMP.is_dir() else Path(tempfile.gettempdir())
    created = Path(tempfile.mkdtemp(prefix="b2-nsjail-acceptance-", dir=str(base)))
    print(f"[env] acceptance root={created}")
    try:
        yield created
    finally:
        shutil.rmtree(created, ignore_errors=True)


@pytest.fixture
def jail_factory(jail_root: Path, nsjail_bin: str) -> Callable[..., _Jail]:
    """按给定限额造一个新沙箱现场（每个用例独立工作区，互不污染）。

    目录名用 `mkdtemp` 生成而非计数器：本 fixture 是函数级作用域，计数器会在每个用例重置，
    导致重复使用同一目录名（曾实测 `FileExistsError: jail-0/work`）。
    """

    def make(**limits: object) -> _Jail:
        root = Path(tempfile.mkdtemp(prefix="jail-", dir=str(jail_root)))
        work = root / "work"
        work.mkdir()
        # 工作区要 bind 给 ns 内的 uid 99999，故必须对其可写
        os.chmod(work, 0o777)
        config = root / "policy.cfg"
        config.write_text(_policy_text(work, **limits), encoding="utf-8")  # type: ignore[arg-type]
        return _Jail(nsjail=nsjail_bin, work=work, config=config)

    return make


@pytest.fixture
def jail(jail_factory: Callable[..., _Jail]) -> _Jail:
    """默认限额的沙箱现场。"""
    return jail_factory()


# =========================================================================================
# 验收项 1：nsjail 真的启动并把一个 Python 脚本跑完（真实退出码 + stdout）
# =========================================================================================


def test_01_nsjail_starts_and_runs_python(jail: _Jail) -> None:
    """nsjail 真实启动、真实执行 Python 脚本，拿到真实退出码与 stdout。"""
    script = jail.write_script(
        "t01_hello.py",
        """
        import os
        import sys

        print("HELLO_FROM_SANDBOX pid=%d ppid=%d" % (os.getpid(), os.getppid()), flush=True)
        print("ARGV=%s" % (sys.argv[1:],), flush=True)
        sys.exit(7)
        """,
    )
    proc, elapsed = jail.run(script)
    command = " ".join(jail.argv(script))
    observed = (
        f"returncode={proc.returncode}({_signal_name(proc.returncode)}) elapsed={elapsed:.2f}s\n"
        f"stdout={proc.stdout.strip()}\nstderr={proc.stderr.strip()[-600:]}"
    )

    started = "HELLO_FROM_SANDBOX" in proc.stdout
    # nsjail 以子进程退出码作为自身退出码；这里是脚本显式 sys.exit(7)
    ok = started and proc.returncode == 7
    _record("1 nsjail 启动并跑完脚本", command, observed, _PASS if ok else _FAIL)

    assert proc.returncode != 127, f"nsjail 未能启动解释器:\n{proc.stderr}"
    assert started, f"脚本未产生 stdout，nsjail 未真正执行:\n{proc.stderr}"
    assert proc.returncode == 7, f"退出码未透传: {proc.returncode}\n{proc.stderr}"


# =========================================================================================
# 验收项 2：禁网可验证
# =========================================================================================


def test_02_network_is_disabled(jail: _Jail) -> None:
    """外部网络必须失败，且失败原因（异常类型/errno）可记录——不得静默成功。"""
    script = jail.write_script(
        "t02_network.py",
        """
        import socket

        def probe(label, fn):
            try:
                fn()
            except OSError as exc:
                code = getattr(exc, "errno", None)
                print("%s=FAIL %s errno=%s msg=%s" % (label, type(exc).__name__, code, exc))
            except Exception as exc:  # noqa: BLE001 - 记录任何异常形态，不吞
                print("%s=FAIL %s msg=%s" % (label, type(exc).__name__, exc))
            else:
                print("%s=OK 连接成功了" % label)

        probe("tcp_1.1.1.1:80",
              lambda: socket.create_connection(("1.1.1.1", 80), timeout=3).close())
        probe("dns_example.com", lambda: socket.getaddrinfo("example.com", 80))
        probe("udp_8.8.8.8:53",
              lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).connect(("8.8.8.8", 53)))
        print("IFACES=%s" % (sorted(name for _, name in socket.if_nameindex()),))
        """,
    )
    proc, elapsed = jail.run(script)
    command = " ".join(jail.argv(script))
    observed = (
        f"returncode={proc.returncode} elapsed={elapsed:.2f}s\n{proc.stdout.strip()}\n"
        f"stderr={proc.stderr.strip()[-400:]}"
    )

    tcp_failed = "tcp_1.1.1.1:80=FAIL" in proc.stdout
    dns_failed = "dns_example.com=FAIL" in proc.stdout
    udp_failed = "udp_8.8.8.8:53=FAIL" in proc.stdout
    no_success = "=OK 连接成功了" not in proc.stdout
    ok = bool(tcp_failed and dns_failed and udp_failed and no_success)

    _record("2 禁网可验证", command, observed, _PASS if ok else _FAIL)

    assert tcp_failed, f"外网 TCP 未被阻断（禁网失效）:\n{proc.stdout}"
    assert dns_failed, f"DNS 未被阻断（禁网失效）:\n{proc.stdout}"
    assert udp_failed, f"外网 UDP 未被阻断（禁网失效）:\n{proc.stdout}"
    assert no_success, f"存在成功的网络访问:\n{proc.stdout}"


# =========================================================================================
# 验收项 3：只读根
# =========================================================================================


def test_03_root_filesystem_is_read_only(jail: _Jail) -> None:
    """写 `/etc/x` 必须失败（EROFS/EACCES）；对照组证明工作区**确实**可写。

    对照组不可省：没有它，"写失败"可能只是权限处处都不对，而不是只读根生效。
    """
    script = jail.write_script(
        "t03_readonly.py",
        """
        import os

        RO = {30: "EROFS", 13: "EACCES", 1: "EPERM"}

        def attempt(label, path, data=b"x"):
            try:
                with open(path, "wb") as handle:
                    handle.write(data)
            except OSError as exc:
                print("%s=DENIED %s(%s)" % (label, RO.get(exc.errno, exc.errno), exc))
            else:
                print("%s=ALLOWED 写入成功了" % label)

        attempt("write /etc/x", "/etc/x")
        attempt("write /usr/lib/x", "/usr/lib/x")
        attempt("write /tmp/x", "/tmp/x")
        # 对照组：工作区（唯一可写路径）
        attempt("write /workspace/x", "/workspace/x", b"ok\\n")
        print("WORKSPACE_READBACK=%r" % (
            open("/workspace/x").read() if os.path.exists("/workspace/x") else None,))
        """,
    )
    proc, elapsed = jail.run(script)
    command = " ".join(jail.argv(script))
    observed = (
        f"returncode={proc.returncode} elapsed={elapsed:.2f}s\n{proc.stdout.strip()}\n"
        f"stderr={proc.stderr.strip()[-400:]}"
    )

    etc_denied = "write /etc/x=DENIED" in proc.stdout
    usr_denied = "write /usr/lib/x=DENIED" in proc.stdout
    workspace_ok = "write /workspace/x=ALLOWED" in proc.stdout
    readback = "WORKSPACE_READBACK='ok" in proc.stdout
    ok = bool(etc_denied and usr_denied and workspace_ok and readback)

    _record("3 只读根", command, observed, _PASS if ok else _FAIL)

    assert etc_denied, f"写 /etc/x 未被拒绝（只读根失效）:\n{proc.stdout}"
    assert usr_denied, f"写 /usr/lib/x 未被拒绝（只读根失效）:\n{proc.stdout}"
    assert workspace_ok, (
        f"对照组失败：工作区应可写却不可写，说明是权限问题而非只读根生效:\n{proc.stdout}"
    )
    assert readback, f"对照组失败：工作区写入内容读不回:\n{proc.stdout}"


# =========================================================================================
# 验收项 4：非 root（ns 内视图 + **外层 /proc 视角**双重核验）
# =========================================================================================


def test_04_jailed_process_is_not_root(jail: _Jail) -> None:
    """待测代码必须以非 root 运行，且**在全局用户命名空间里**也不是 root。

    只断言 ns 内 `os.geteuid() != 0` 不够：uidmap 未把 0 映射出去时，内核会回落到 overflow
    uid(65534)，看起来"非 root"，而进程在全局命名空间里仍是 uid 0——即**假**非 root。
    因此这里同时从外层 `/proc/<pid>/status` 复核真实 uid。
    """
    marker = f"B2UID{uuid.uuid4().hex[:10]}"
    script = jail.write_script(
        f"{marker}.py",
        f"""
        import os
        import time

        print("MARKER={marker}", flush=True)
        print("EUID=%d EGID=%d UID=%d GID=%d"
              % (os.geteuid(), os.getegid(), os.getuid(), os.getgid()), flush=True)
        for line in open("/proc/self/status"):
            if line.startswith(("CapEff", "CapPrm", "NoNewPrivs", "Seccomp")):
                print("STATUS %s" % line.strip(), flush=True)
        time.sleep(6.0)
        print("DONE", flush=True)
        """,
    )

    argv = jail.argv(script)
    proc = subprocess.Popen(
        argv,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    outside_uids: list[tuple[int, tuple[int, ...]]] = []
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not outside_uids:
        # 排除 nsjail 自身（其 cmdline 也含脚本路径）；只要被囚的 python 进程
        for pid in _scan_proc_for_marker(marker, exclude=b"nsjail"):
            uid = _proc_uid_from_outside(pid)
            if uid is not None:
                outside_uids.append((pid, uid))
        time.sleep(0.2)
    try:
        stdout, stderr = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:  # pragma: no cover - 收尾保护
        proc.kill()
        stdout, stderr = proc.communicate()
    command = " ".join(argv)
    observed = (
        f"inside: {stdout.strip()}\n"
        f"outside /proc status Uid: {outside_uids}\n"
        f"returncode={proc.returncode} stderr={stderr.strip()[-400:]}"
    )

    eager = f"EUID={_JAIL_UID}" in stdout
    outside_nonzero = bool(outside_uids) and all(
        uid[1][0] != 0 and uid[1][1] != 0 for uid in outside_uids
    )
    ok = eager and outside_nonzero

    _record("4 非 root", command, observed, _PASS if ok else _FAIL)

    assert eager, f"ns 内 euid 不是预期非 0 值:\n{stdout}"
    assert outside_uids, (
        "未能在外层 /proc 观测到被囚进程——无法证明其为非 root（不得仅凭 ns 内 euid 下结论）"
        f"\nstdout={stdout}\nstderr={stderr}"
    )
    assert outside_nonzero, (
        "外层命名空间看到的 uid 为 0 —— 这是**假**非 root（uidmap 默认会把 ns 内非 root 映射到"
        f"全局 root）: {outside_uids}"
    )


# =========================================================================================
# 验收项 5a：内存上限真的生效
# =========================================================================================


def test_05a_memory_limit_is_enforced(jail_factory: Callable[..., _Jail]) -> None:
    """内存上限真的生效：`rlimit_as` 必须在分配超限时挡住进程。

    判别式：`rlimit_as=128 MiB`，cgroup 放到 1 GiB（把 cgroup 排除在变量之外）。
    脚本自报分配进度，因此"在哪一步被挡住"是可核对的：必须明显低于 1 GiB 且以
    `MemoryError` 结束（而不是跑满 400×4 MiB）。

    循环上限刻意封顶（400×4 MiB = 1.6 GiB）：即使限制失效也不会打爆宿主内存（安全阀）。
    """
    body = """
    import sys

    blocks = []
    step = 16
    next_report = step
    print("MEM_START 沙箱已启动", flush=True)
    try:
        for _ in range(400):
            blocks.append(bytearray(4 * 1024 * 1024))  # 每块 4 MiB，真实驻留
            used = len(blocks) * 4
            if used >= next_report:
                print("MEM_ALLOC=%dMiB" % used, flush=True)
                next_report += step
    except MemoryError:
        print("MEM_BLOCKED MemoryError allocated=%dMiB" % (len(blocks) * 4), flush=True)
        sys.exit(4)
    except BaseException as exc:  # noqa: BLE001 - 记录任何形态
        print("MEM_BLOCKED %s allocated=%dMiB" % (type(exc).__name__, len(blocks) * 4))
        sys.exit(4)
    print("MEM_NOT_LIMITED allocated=%dMiB" % (len(blocks) * 4), flush=True)
    """

    jail = jail_factory(rlimit_as_mib=128, cgroup_mem_max_mib=1024)
    script = jail.write_script("t05a_rlimit_as.py", body)
    proc, elapsed = jail.run(script, timeout=90)
    command = " ".join(jail.argv(script))
    reached = [int(value) for value in re.findall(r"MEM_ALLOC=(\d+)MiB", proc.stdout)]
    last_alloc = max(reached) if reached else 0
    observed = (
        f"rlimit_as=128MiB cgroup=1GiB rc={proc.returncode}({_signal_name(proc.returncode)})"
        f" {elapsed:.2f}s\n已分配进度={reached}\nstdout={proc.stdout.strip()[-300:]}\n"
        f"stderr={proc.stderr.strip()[-300:]}"
    )

    started = "MEM_START" in proc.stdout
    blocked = "MEM_BLOCKED" in proc.stdout and "MEM_NOT_LIMITED" not in proc.stdout
    ok = bool(started and blocked and last_alloc < 512)

    _record("5a 内存上限生效", command, observed, _PASS if ok else _FAIL)

    assert started, f"沙箱未跑起来:\n{proc.stderr}"
    assert "MEM_NOT_LIMITED" not in proc.stdout, f"分配跑满未被挡住:\n{proc.stdout}"
    assert blocked, f"未观测到内存限制生效的证据:\n{proc.stdout}"
    assert last_alloc < 512, (
        f"被挡住的位置（{last_alloc}MiB）可疑：rlimit_as 设为 128MiB，不该走到 512MiB 以上"
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "WSL2 内核（6.18.33.2-microsoft-standard-WSL2）对 cgroup v2 memory.max **只记账不杀进程**："
        "裸 cgroup 实测 memory.peak 恰好被压在 64MiB、memory.events.max=4873，但 oom_kill=0，"
        "分配进程一路到 800MiB 正常退出。若在 mainline 内核上执行，本用例会 XPASS —— 那正是限制恢复生效的信号。"
    ),
)
def test_05a2_cgroup_v2_memory_max_kills(jail_factory: Callable[..., _Jail]) -> None:
    """cgroup v2 `memory.max` 超限必须 OOM **杀掉**进程（在支持该语义的内核上）。

    与 5a 分开的理由：5a 证明 `rlimit_as` 生效（可依赖）；本条单独检验 cgroup 的**杀进程**语义。
    本环境实测为"记账但不杀"，因此记为 `无法执行`（环境限制）并 xfail；绝不当成"cgroup 生效"。
    """
    jail = jail_factory(rlimit_as_mib=512, cgroup_mem_max_mib=64)
    script = jail.write_script(
        "t05a2_cgroup.py",
        """
        import sys

        blocks = []
        print("CGMEM_START", flush=True)
        try:
            for _ in range(200):
                blocks.append(bytearray(4 * 1024 * 1024))
        except MemoryError:
            print("CGMEM_MEMORYERROR allocated=%dMiB" % (len(blocks) * 4), flush=True)
            sys.exit(4)
        print("CGMEM_NOT_LIMITED allocated=%dMiB" % (len(blocks) * 4), flush=True)
        """,
    )
    proc, elapsed = jail.run(script, timeout=90)
    command = " ".join(jail.argv(script))
    reached = [int(value) for value in re.findall(r"=(\d+)MiB", proc.stdout)]
    last_alloc = max(reached) if reached else 0
    # cgroup OOM 杀进程的判据：被信号终结，且**不是**自己 MemoryError 退出
    killed = proc.returncode < 0 and "CGMEM_MEMORYERROR" not in proc.stdout
    observed = (
        f"rlimit_as=512MiB cgroup memory.max=64MiB rc={proc.returncode}"
        f"({_signal_name(proc.returncode)}) {elapsed:.2f}s\n已分配进度={reached}\n"
        f"stdout={proc.stdout.strip()[-300:]}\nstderr={proc.stderr.strip()[-300:]}"
    )

    _record(
        "5a2 cgroup memory.max 杀进程（环境限制）",
        command,
        observed,
        _PASS if killed else _UNAVAILABLE,
    )

    assert "CGMEM_NOT_LIMITED" not in proc.stdout, f"分配跑满未被挡住:\n{proc.stdout}"
    assert killed, (
        f"cgroup memory.max=64MiB **没有**杀掉进程：进程一路分配到 {last_alloc}MiB 才因 rlimit_as"
        f"(512MiB) 自行退出。说明该内核只记账不杀（本环境的真实限制，不得当作 cgroup 生效）"
    )


# =========================================================================================
# 验收项 5b：CPU 时间上限真的生效
# =========================================================================================


def test_05b_cpu_time_limit_is_enforced(jail_factory: Callable[..., _Jail]) -> None:
    """纯 CPU 忙循环必须被 CPU 时间上限（cpu_seconds）终结，而不是跑到墙钟上限（time_limit）。

    判别式：`cpu_seconds=3` / `time_limit=30`。若 CPU 限制未生效，进程会一直跑到 30s 墙钟，
    故 `elapsed < 20` 是干净的判别量。
    """
    jail = jail_factory(cpu_seconds=3, time_limit=30)
    script = jail.write_script(
        "t05b_cpu.py",
        """
        print("CPU_START 沙箱已启动", flush=True)
        total = 0
        while True:
            total += 1
        print("CPU_NOT_LIMITED iterations=%d" % total, flush=True)
        """,
    )
    proc, elapsed = jail.run(script, timeout=60)
    command = " ".join(jail.argv(script))
    observed = (
        f"returncode={proc.returncode}({_signal_name(proc.returncode)}) elapsed={elapsed:.2f}s\n"
        f"stdout={proc.stdout.strip()}\nstderr={proc.stderr.strip()[-400:]}"
    )

    started = "CPU_START" in proc.stdout
    not_limited = "CPU_NOT_LIMITED" in proc.stdout
    ok = bool(started and not not_limited and elapsed < 20)

    _record("5b CPU 时间上限生效", command, observed, _PASS if ok else _FAIL)

    assert started, f"沙箱未真正跑起来:\n{proc.stderr}"
    assert not not_limited, f"忙循环跑完未被 CPU 限制终结:\n{proc.stdout}"
    assert elapsed < 20, (
        f"CPU 限制疑似未生效：进程跑了 {elapsed:.2f}s（CPU 上限 3s、墙钟 30s，"
        f"说明是被墙钟而非 CPU 时间终结）"
    )


# =========================================================================================
# 验收项 5c：进程数上限挡住 fork bomb
# =========================================================================================


def test_05c_pids_limit_blocks_fork_bomb(jail_factory: Callable[..., _Jail]) -> None:
    """fork bomb 必须被进程数上限挡住（EAGAIN），并给出 ns 内进程计数作为直接证据。

    循环上限刻意封顶（200 次）：即使限制完全失效也不至于拖垮宿主（安全阀）。
    """
    limit = 16
    jail = jail_factory(max_processes=limit, time_limit=20)
    script = jail.write_script(
        "t05c_forkbomb.py",
        f"""
        import os
        import sys
        import time

        LIMIT = {limit}
        print("FORK_START 沙箱已启动", flush=True)
        children = 0
        try:
            for _ in range(200):
                pid = os.fork()
                if pid == 0:
                    time.sleep(60)
                    os._exit(0)
                children += 1
        except OSError as exc:
            procs = [entry for entry in os.listdir("/proc") if entry.isdigit()]
            print("FORK_BLOCKED children=%d errno=%d(%s) jail_proc_count=%d"
                  % (children, exc.errno, exc.strerror, len(procs)), flush=True)
            sys.exit(0)
        print("FORK_NOT_BLOCKED children=%d" % children, flush=True)
        """,
    )
    proc, elapsed = jail.run(script, timeout=90)
    command = " ".join(jail.argv(script))
    observed = (
        f"returncode={proc.returncode} elapsed={elapsed:.2f}s\n{proc.stdout.strip()}\n"
        f"stderr={proc.stderr.strip()[-400:]}"
    )

    started = "FORK_START" in proc.stdout
    blocked = "FORK_BLOCKED" in proc.stdout
    not_blocked = "FORK_NOT_BLOCKED" in proc.stdout
    ok = bool(started and blocked and not not_blocked)

    _record("5c 进程数上限挡住 fork bomb", command, observed, _PASS if ok else _FAIL)

    assert started, f"沙箱未真正跑起来:\n{proc.stderr}"
    assert not not_blocked, f"fork bomb 未被挡住:\n{proc.stdout}"
    assert blocked, (
        f"未观测到 fork 被拒绝的证据（上限={limit}）:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


# =========================================================================================
# 验收项 5d：输出上限（刷 stdout 被截断）
# =========================================================================================


def test_05d_output_limit_truncates(jail_factory: Callable[..., _Jail]) -> None:
    """无限刷 stdout 必须被截断：读取侧只取上限字节数，绝不无界缓冲。

    **如实说明**：nsjail 3.4 的 `config.proto`（逐字段核对）没有 stdout/stderr 大小选项，
    因此输出上限**只能**由读取侧强制。本用例证明这条真实可执行的路径：读满上限即停止读取，
    不把管道内容无界读进内存；作业随后由 nsjail 自身的 `time_limit` 收掉，并核对无进程残留。
    """
    cap = 4096
    jail = jail_factory(time_limit=4)
    marker = f"B2OUT{uuid.uuid4().hex[:10]}"
    script = jail.write_script(
        f"t05d_output_{marker}.py",
        f"""
        import sys

        marker = {marker!r}
        block = ("%s " % marker) * 64 + "\\n"
        while True:
            sys.stdout.write(block)
        """,
    )

    argv = jail.argv(script)
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    captured = b""
    assert proc.stdout is not None
    while len(captured) < cap:
        chunk = proc.stdout.read(cap - len(captured))
        if not chunk:
            break
        captured += chunk
    truncated = len(captured) == cap
    # 读满上限即停止读取；作业由 nsjail 的 time_limit 收掉（不靠读取侧 kill，避免残留）
    proc.wait(timeout=60)
    elapsed = time.monotonic() - started
    leftovers = _scan_proc_for_marker(marker)
    stderr = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")

    command = " ".join(argv)
    observed = (
        f"读取上限={cap}B 实收={len(captured)}B truncated={truncated} elapsed={elapsed:.2f}s\n"
        f"首 64B={captured[:64]!r}\nreturncode={proc.returncode}\n"
        f"残留进程={leftovers}\nstderr={stderr.strip()[-400:]}"
    )

    ok = truncated and not leftovers
    _record("5d 输出上限（截断）", command, observed, _PASS if ok else _FAIL)

    assert truncated, f"未能读满上限字节（收到 {len(captured)}B），截断路径未成立"
    assert not leftovers, f"输出截断后仍有残留进程: {leftovers}"


# =========================================================================================
# 验收项 5e：墙钟超时
# =========================================================================================


def test_05e_wall_clock_timeout_kills(jail_factory: Callable[..., _Jail]) -> None:
    """睡眠超时脚本必须在墙钟上限附近被终结，且不会跑完。"""
    jail = jail_factory(time_limit=3)
    script = jail.write_script(
        "t05e_wall.py",
        """
        import time

        print("SLEEP_START", flush=True)
        time.sleep(120)
        print("SLEEP_DONE 不应出现", flush=True)
        """,
    )
    proc, elapsed = jail.run(script, timeout=60)
    command = " ".join(jail.argv(script))
    observed = (
        f"returncode={proc.returncode}({_signal_name(proc.returncode)}) elapsed={elapsed:.2f}s\n"
        f"stdout={proc.stdout.strip()}\nstderr={proc.stderr.strip()[-500:]}"
    )

    started = "SLEEP_START" in proc.stdout
    completed = "SLEEP_DONE" in proc.stdout
    ok = bool(started and not completed and elapsed < 30)

    _record("5e 墙钟超时", command, observed, _PASS if ok else _FAIL)

    assert started, f"脚本未启动，无法验证超时:\n{proc.stderr}"
    assert not completed, f"超时未生效，脚本跑完了:\n{proc.stdout}"
    assert elapsed < 30, f"墙钟超时未在 30s 内生效（实际 {elapsed:.2f}s）"


# =========================================================================================
# 验收项 6：超时清理整个进程树（含"检测手段有效性"对照）
# =========================================================================================


def _tree_parent_body(marker: str) -> str:
    """父进程 fork 一个后台子进程（命令行带 marker）后挂住——用于残留检测。"""
    return f"""
    import os
    import sys
    import time

    marker = {marker!r}
    child_code = "import time; time.sleep(300)"
    pid = os.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, "-c", child_code, marker])
    print("FORKED_CHILD pid=%d marker=%s" % (pid, marker), flush=True)
    time.sleep(300)
    print("PARENT_DONE 不应出现", flush=True)
    """


def test_06_timeout_cleans_whole_process_tree(jail_factory: Callable[..., _Jail]) -> None:
    """超时后进程表里**没有残留**——用 `/proc` 在超时前后各扫一次做实证。

    包含一个**对照**：同一脚本不经 nsjail 直接跑、只杀父进程，后台子进程必须仍能被扫到。
    没有这个对照，"扫描没找到残留"可能只是检测手段根本无效。
    """
    marker = f"B2TREE{uuid.uuid4().hex[:10]}"
    jail = jail_factory(time_limit=3)
    script = jail.write_script(f"{marker}.py", _tree_parent_body(marker))

    # --- 对照：不经 nsjail，只杀父进程 → 子进程应残留（证明检测手段有效） ---
    # 注意用 host_path：`write_script` 返回的是**沙箱内**路径，在沙箱外并不存在
    # （本机实测踩过：对照脚本 fail to open file，扫描当然什么都找不到）。
    control = subprocess.Popen(
        [sys.executable, str(jail.host_path(script))],
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    control_children: list[int] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not control_children:
        control_children = [pid for pid in _scan_proc_for_marker(marker) if pid != control.pid]
        time.sleep(0.2)
    control.kill()
    control.wait(timeout=30)
    time.sleep(0.6)
    control_leftover = _scan_proc_for_marker(marker)
    for pid in control_leftover:  # 收尾，不把残留留给后续内容
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.6)

    # --- 实测：经 nsjail + time_limit=3s（排除 nsjail 自身，只看被囚进程残留） ---
    argv = jail.argv(script)
    before = _scan_proc_for_marker(marker, exclude=b"nsjail")
    proc, elapsed = jail.run(script, timeout=60)
    time.sleep(1.0)
    after = _scan_proc_for_marker(marker, exclude=b"nsjail")

    command = " ".join(argv)
    observed = (
        f"[对照·无沙箱] 杀父进程后残留={control_leftover}（证明检测手段有效）\n"
        f"[实测·nsjail] 超时前={before} 超时后={after} elapsed={elapsed:.2f}s "
        f"returncode={proc.returncode}({_signal_name(proc.returncode)})\n"
        f"stdout={proc.stdout.strip()}\nstderr={proc.stderr.strip()[-500:]}"
    )

    control_proves_detection = bool(control_leftover)
    ok = control_proves_detection and not after

    _record("6 超时清理整个进程树", command, observed, _PASS if ok else _FAIL)

    assert control_proves_detection, (
        "对照失败：不经沙箱只杀父进程后竟然没扫到后台子进程，说明残留检测手段无效，"
        f"后面「无残留」的结论不可信: {control_leftover}"
    )
    assert not after, f"超时后进程表里仍有残留（进程树未清理干净）: {after}"


# =========================================================================================
# 集成项（显式标注）：用 runner 生成的 nsjail 配置跑真 nsjail —— 只读 runner，不改它
# =========================================================================================


def test_i1_runner_generated_config_is_accepted_by_real_nsjail(
    jail_root: Path, nsjail_bin: str
) -> None:
    """【集成项】校准 `runner.policy` 生成的 nsjail 配置。

    两个待校准点：字段名是否被真 nsjail 接受；seccomp 允许清单是否足以让 CPython 3.12 启动。
    本用例**只读** runner（`render_nsjail_config` / `build_argv`），不修改它；runner 尚不可导入时
    skip。结论仅用于校准，不代表 runner 已通过验收。
    """
    source_root = str(_REPO_ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from autograder_b2.runner.policy import RunnerPolicy, build_argv, render_nsjail_config
    except Exception as exc:  # noqa: BLE001 - 报告确切导入错误，不掩盖
        pytest.skip(f"runner.policy 暂不可导入（可能仍在实现中）: {type(exc).__name__}: {exc}")

    work = jail_root / "runner-work"
    work.mkdir(parents=True)
    os.chmod(work, 0o777)
    policy = RunnerPolicy(
        nsjail_path=nsjail_bin,
        python_interpreter=_PYTHON,
        max_wall_seconds=10.0,
        max_cpu_seconds=5.0,
        max_memory_bytes=64 * 1024 * 1024,
        max_processes=16,
    )
    config = jail_root / "runner-policy.cfg"
    config.write_text(render_nsjail_config(policy, work), encoding="utf-8")

    script_file = work / "probe.py"
    body = 'import os\nprint("RUNNER_POLICY_RAN euid=%d" % os.geteuid())\n'
    script_file.write_text(body, encoding="utf-8")
    sandbox_script = f"{policy.workspace_mount}/probe.py"
    argv = build_argv(
        policy,
        config_path=config,
        sandbox_log_path=jail_root / "runner-policy.nsjail.log",
        language="python",
        script=sandbox_script,
    )

    proc = _run(argv, timeout=90)
    command = " ".join(argv)
    observed = (
        f"config_head=\n{config.read_text(encoding='utf-8')[:1500]}\n---\n"
        f"returncode={proc.returncode}({_signal_name(proc.returncode)})\n"
        f"stdout={proc.stdout.strip()}\nstderr={proc.stderr.strip()[-1200:]}"
    )

    ran = "RUNNER_POLICY_RAN" in proc.stdout
    _record("I1 runner 配置跑真 nsjail（集成项）", command, observed, _PASS if ran else _FAIL)

    assert ran, (
        "runner 生成的 nsjail 配置未能让 CPython 跑起来（字段名或 seccomp 允许清单需校准）——"
        f"详见矩阵中的 returncode 与 stderr:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_i2_nsjail_log_separates_diagnostics_from_student_streams(
    jail_root: Path, nsjail_bin: str
) -> None:
    """【集成项】`--log` 把 nsjail 自有诊断与学生流**物理分开**（沙箱侧归因的可信边界）。

    为什么这条是必需的证据：修复前 nsjail 诊断与学生 stderr 同写一份文件，`_scan_markers` 只做
    `"nsjail" in line` 过滤，学生打印一行 `nsjail: out of memory` 即可伪造沙箱侧归因（红队证伪）。
    修复后归类只读 `RunFacts.sandbox_log_text`（即 `--log` 指向的宿主自有文件）。本用例用**生产同一
    函数**（`render_nsjail_config` + `build_argv`）跑两种真实情形，证明分离本身成立：

    (a) 正常脚本：学生 stdout/stderr 只含学生自己的标记行，日志里**不含**学生字样；
    (b) nsjail 自身失败（解释器要跑的程序不存在）：致命诊断（`[E]`/`[F]`）只出现在日志里，
        被采集的进程 stderr 为 **0 字节**——即"沙箱报错"不会再混进学生的流里。

    本用例**只读** runner，不修改它。
    """
    source_root = str(_REPO_ROOT / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from autograder_b2.runner.policy import RunnerPolicy, build_argv, render_nsjail_config
    except Exception as exc:  # noqa: BLE001 - 报告确切导入错误，不掩盖
        pytest.skip(f"runner.policy 暂不可导入（可能仍在实现中）: {type(exc).__name__}: {exc}")

    work = jail_root / "log-separation-work"
    work.mkdir(parents=True)
    os.chmod(work, 0o777)
    policy = RunnerPolicy(
        nsjail_path=nsjail_bin,
        python_interpreter=_PYTHON,
        max_wall_seconds=10.0,
        max_cpu_seconds=5.0,
        max_memory_bytes=64 * 1024 * 1024,
        max_processes=16,
    )
    config = jail_root / "log-separation.cfg"
    config.write_text(render_nsjail_config(policy, work), encoding="utf-8")

    # (a) 正常脚本：学生流与日志互不串味
    student_marker = f"B2STUDENT{uuid.uuid4().hex[:10]}"
    (work / "student.py").write_text(
        "import sys\n"
        f"print({student_marker!r})\n"
        f"print('nsjail: out of memory ' + {student_marker!r}, file=sys.stderr)\n",
        encoding="utf-8",
    )
    normal_log = jail_root / "normal.nsjail.log"
    normal_argv = build_argv(
        policy,
        config_path=config,
        sandbox_log_path=normal_log,
        language="python",
        script=f"{policy.workspace_mount}/student.py",
    )
    normal = _run(normal_argv, timeout=90)
    normal_log_text = normal_log.read_text(encoding="utf-8", errors="replace") if normal_log.exists() else ""

    # (b) nsjail 自身失败：策略里的解释器不存在 ⇒ nsjail 的 execve 失败，致命诊断只进日志
    #     （不能用"脚本不存在"：那会让解释器正常启动、由**子进程**把错误写到学生流里，
    #      属正常行为，与"nsjail 自己的诊断会不会串进学生流"是两回事——实测踩过。）
    broken_policy = RunnerPolicy(
        nsjail_path=nsjail_bin,
        python_interpreter="/nonexistent/python3",
        max_wall_seconds=10.0,
        max_cpu_seconds=5.0,
        max_memory_bytes=64 * 1024 * 1024,
        max_processes=16,
    )
    fatal_log = jail_root / "fatal.nsjail.log"
    fatal_argv = build_argv(
        broken_policy,
        config_path=config,
        sandbox_log_path=fatal_log,
        language="python",
        script=f"{policy.workspace_mount}/student.py",
    )
    fatal = _run(fatal_argv, timeout=90)
    fatal_log_text = fatal_log.read_text(encoding="utf-8", errors="replace") if fatal_log.exists() else ""

    command = f"(a) {' '.join(normal_argv)}\n(b) {' '.join(fatal_argv)}"
    observed = (
        f"(a) returncode={normal.returncode} stdout={normal.stdout.strip()!r} "
        f"stderr={normal.stderr.strip()!r}\n"
        f"(a) 日志 {len(normal_log_text)}B: {normal_log_text.strip()[:400]!r}\n"
        f"(b) returncode={fatal.returncode} stdout={fatal.stdout.strip()!r} "
        f"stderr={len(fatal.stderr)}B {fatal.stderr.strip()[:200]!r}\n"
        f"(b) 日志 {len(fatal_log_text)}B: {fatal_log_text.strip()[:800]!r}"
    )

    separated = (
        student_marker in normal.stdout
        and student_marker not in normal_log_text  # 学生字节不落日志
        and fatal.stderr == ""  # nsjail 诊断不落学生流
        and ("[E]" in fatal_log_text or "[F]" in fatal_log_text)  # 诊断确实进了日志
        and fatal.returncode != 0
    )
    _record("I2 nsjail --log 分离沙箱诊断与学生流（集成项）", command, observed,
            _PASS if separated else _FAIL)

    assert student_marker in normal.stdout and student_marker not in normal_log_text, (
        "学生流与沙箱日志必须互不串味（学生字节不得出现在日志里）"
    )
    assert fatal.returncode != 0 and fatal.stderr == "", (
        f"nsjail 自身失败的诊断不得进入被采集的进程流：stderr={fatal.stderr!r}"
    )
    assert "[E]" in fatal_log_text or "[F]" in fatal_log_text, (
        f"nsjail 致命诊断必须落在 --log 日志里：{fatal_log_text[:400]!r}"
    )


# =========================================================================================
# 验收项 7：每项都有「命令 / 输出 / 判定」——矩阵完整性自检（必须最后执行）
# =========================================================================================

_REQUIRED_ITEMS = (
    "1 nsjail 启动并跑完脚本",
    "2 禁网可验证",
    "3 只读根",
    "4 非 root",
    "5a 内存上限生效",
    "5b CPU 时间上限生效",
    "5c 进程数上限挡住 fork bomb",
    "5d 输出上限（截断）",
    "5e 墙钟超时",
    "6 超时清理整个进程树",
    "I2 nsjail --log 分离沙箱诊断与学生流（集成项）",
)

#: 不是"必须通过"、而是"必须留下实测结论"的项：本环境的已知限制，判定可为 `无法执行`。
_LIMITATION_ITEMS = ("5a2 cgroup memory.max 杀进程（环境限制）",)

_ALL_ITEMS = _REQUIRED_ITEMS + _LIMITATION_ITEMS


def test_07_every_item_reports_command_output_and_verdict(nsjail_bin: str) -> None:
    """验收项 7 的自检：每一项都必须留下命令 + 输出 + 判定。

    依赖 `nsjail_bin` 是**刻意的**：没有 nsjail 就没有可验收的现场，本用例随之 skip
    （非 Linux 宿主的驱动模式即属此情形）。定义在文件末尾同样是刻意的——它要求**所有**
    验收项（含末尾的两个集成项）都已把证据写进 `_EVIDENCE`。
    """
    recorded = {row.item: row for row in _EVIDENCE}
    missing = [item for item in _ALL_ITEMS if item not in recorded]
    incomplete = [
        item
        for item in _ALL_ITEMS
        if item in recorded and not (recorded[item].command and recorded[item].output)
    ]
    verdicts = {item: recorded[item].verdict for item in _ALL_ITEMS if item in recorded}
    observed = f"missing={missing} incomplete={incomplete}\nverdicts={verdicts}"
    ok = not missing and not incomplete

    _record(
        "7 各项含命令/输出/判定",
        "pytest tests/linux/test_nsjail_acceptance.py -v -s（矩阵见 stdout 尾部）",
        observed,
        _PASS if ok else _FAIL,
    )

    assert not missing, f"以下验收项没有留下证据: {missing}"
    assert not incomplete, f"以下验收项缺命令或输出: {incomplete}"


# =========================================================================================
# 宿主驱动模式：Windows 上用一个固定参数数组调 docker，在特权容器内跑上面全部用例
# =========================================================================================


def _docker_driver_argv() -> list[str]:
    """宿主侧唯一的 docker 调用：**固定参数数组**，`shell=False`。

    `--privileged` + `--cgroupns=host` 的实测理由见 `verify/linux-nsjail/README.md`
    （缺任一项 nsjail 都无法建立 mount/cgroup）。
    """
    image = os.environ.get(_IMAGE_ENV, _DEFAULT_IMAGE)
    repo = _REPO_ROOT.as_posix()
    return [
        "docker",
        "run",
        "--rm",
        "--privileged",
        "--cgroupns=host",
        "-e",
        f"{_DRIVER_ENV}=",
        "-e",
        "PYTHONPATH=/work/src",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-v",
        f"{repo}:/work",
        "-w",
        "/work",
        image,
        "python3",
        "-m",
        "pytest",
        "tests/linux/test_nsjail_acceptance.py",
        "-v",
        "-s",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]


def _parse_matrix(stdout: str) -> dict[str, str]:
    """从容器内的 stdout 里解析实测矩阵（项名 → 判定）。

    矩阵本身就是验收产物（验收项 7），所以宿主侧直接断言它，比对整体 returncode 断言更精确：
    集成项 I1 的失败（runner 生成的 seccomp 策略编译不过，已上报）不应掩盖 1–6 项的真实结论。
    """
    found: dict[str, str] = {}
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| ") or stripped.startswith(("| 项 ", "|---")):
            continue
        # 单元格内的字面 `|` 已在渲染时转义为 `\|`（无空格），故 ` | ` 只会是列分隔符
        cells = [cell.strip() for cell in stripped.strip("|").split(" | ")]
        if len(cells) >= 4:
            found[cells[0]] = cells[-1]
    return found


@pytest.mark.skipif(
    not _DRIVER_MODE,
    reason=f"仅在宿主驱动模式运行（设 {_DRIVER_ENV}=1）；容器内是 Linux，走真实断言路径",
)
def test_docker_driver_runs_acceptance_in_privileged_linux_container() -> None:
    """Windows 宿主入口：把本文件在特权 Linux 容器里跑一遍，并按**实测矩阵**断言 1–6 项。"""
    argv = _docker_driver_argv()
    try:
        proc = _run(argv, timeout=1800)
    except FileNotFoundError:
        pytest.fail("找不到 docker CLI；请确认 Docker Desktop 已启动。")
    except subprocess.TimeoutExpired:
        pytest.fail(f"容器内验收超时（>1800s）: {' '.join(argv)}")

    matrix = _parse_matrix(proc.stdout)
    missing = [item for item in _ALL_ITEMS if item not in matrix]
    # 只有"必须通过"的项参与通过判定；已知环境限制项（_LIMITATION_ITEMS）只要求留下实测结论
    not_passed = {
        item: matrix[item] for item in _REQUIRED_ITEMS if matrix.get(item) not in (None, _PASS)
    }
    limitations = {item: matrix.get(item) for item in _LIMITATION_ITEMS}
    integration = {k: v for k, v in matrix.items() if k.startswith("I1")}

    tail = _one_line(proc.stdout[-3000:] + "\n" + proc.stderr[-1500:], limit=2500)
    observed = (
        f"container_returncode={proc.returncode}\n"
        f"matrix_items={len(matrix)} missing={missing}\n"
        f"not_passed={not_passed}\nlimitations={limitations}\nintegration={integration}\n"
        f"tail={tail}"
    )
    ok = not missing and not not_passed

    _record("D docker 驱动（容器内跑 1–6 项）", " ".join(argv), observed, _PASS if ok else _FAIL)

    assert not missing, (
        f"容器内没有产出以下验收项的矩阵行（疑似用例被跳过或收集中断）: {missing}\n"
        f"{proc.stdout[-3000:]}"
    )
    assert not not_passed, (
        f"以下验收项在容器内未判定为「{_PASS}」: {not_passed}\n{proc.stdout[-3000:]}"
    )
