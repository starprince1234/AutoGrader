"""`run_case(package, submission) -> CaseEvidence`：沙箱内逐用例执行、比较与证据。

签名、不变量（I20–I28）与失败语义见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 5 节（**已冻结**）。
本模块负责：前置可用性判定 → 执行计划 → nsjail 固定参数数组启动 → 输出采集与截断 →
比较策略（**只来自 package**）→ 失败归类 → 证据与摘要。

不可协商项：

- **不降级**：`probe_sandbox` 判定不可执行即抛 `SandboxUnavailableError`，在**任何进程被创建之前**返回；
  非 POSIX 平台上 `PosixNsjailLauncher` 自身也拒绝启动（双重保险）。
- **不拼接 shell**：启动只用 `build_argv` 的固定数组；`shell` 显式传 `False`。
- **超时清理整棵进程树**：独立会话（`start_new_session=True`）⇒ 进程组即整棵树，
  超时后 `kill_tree` **恰好调用一次**（残留由 nsjail 自身的 cgroup 拆除与 `cgroup_pids_max` 兜底）。
- **学生侧失败返回证据、不抛异常**；沙箱不可用才抛异常。

输出采集走**文件重定向**而不是管道：nsjail 的 stdin/stdout/stderr 指向工作区**旁边**的临时文件
（该目录在沙箱内被遮蔽为只读空 tmpfs），因此读取天然有界（`rlimit_fsize` + `max_output_bytes` 双重限制），
不会把无界输出读进内存（资源账本：单并发作业 ≤96 MB RSS）。

沙箱自有诊断走**另一份文件**（`--log`）：学生流里只有学生自己的字节，沙箱侧归因只读那份日志。
理由与实测见 `build_argv` 与 `classify_case_outcome` 的说明（此前两者同写一份文件，学生可伪造归因）。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from autograder_b2.domain import (
    CaseArtifacts,
    CaseEvidence,
    CaseResource,
    FailureCategory,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SecurityCategory,
    SecurityFinding,
    SecuritySeverity,
    SubmissionRunInput,
)
from autograder_b2.domain.clock import sha256_hex
from autograder_b2.runner.adapters import (
    EXCERPT_LIMIT,
    HARNESS_RESOLUTION_MARKER,
    ExecutionPlan,
    FixtureOutcome,
    WorkspaceChanges,
    WorkspaceFile,
    build_execution_plan,
    collect_fixture_outcomes,
    created_files,
    entry_script_exists,
    excerpt_of_text,
    join_problems,
    prepare_case_workspace,
    snapshot_workspace,
    workspace_changes,
)
from autograder_b2.runner.cgroup import (
    CaseResourceSampler,
    CgroupV2Peaks,
    case_resource_from_peaks,
    limit_hit_for,
)
from autograder_b2.runner.comparator import (
    ComparisonOutcome,
    compare_stderr,
    compare_stdout,
)
from autograder_b2.runner.package import EvaluationPackage, PackageAdapter, PackageCase
from autograder_b2.runner.policy import (
    RunnerPolicy,
    build_argv,
    policy_digest,
    probe_sandbox,
    render_nsjail_config,
    sandbox_env,
)

#: 超时后等待进程树真正退出的宽限秒数（`kill_tree` 之后只用于收割，不再重复杀）。
TERM_GRACE_SECONDS = 5.0

#: 证据 `message` 上限（契约 `maxLength: 1000`）。
MESSAGE_LIMIT = 1000

#: 沙箱自有日志（`--log` 指向的那份文件）的读取上限。
#:
#: 与学生输出限额（`max_output_bytes`）**无关**：这是宿主自有诊断，单次运行量级为 KB；这里的上限
#: 只是"异常情况下也不把无界文件读进内存"的护栏，不参与学生侧判定。
#:
#: 实测（生产配置 `log_level: ERROR`，容器内真跑扣取字节）：正常运行与"内存/CPU/输出"三类真实限制下
#: 日志为 **0 字节**；只有 nsjail 自身报错（如 execve/mount 失败）才会留下 `[E]`/`[F]` 行——因此这份
#: 日志既不会把参数转储里的 `rlimit_*` 字样误当命中，也确实承载沙箱侧的错误事实。
SANDBOX_LOG_MAX_BYTES = 256 * 1024

#: 沙箱日志 → 资源限制项的标记。**只允许**用于 `RunFacts.sandbox_log_text`（见 `_scan_sandbox_log`）。
LIMIT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 可信性来自"文件学生写不进"这个结构性事实，不是行内容过滤：此前 `_scan_markers` 只做
    # `"nsjail" in line`，而学生 stdout/stderr 与 nsjail 日志同写一份文件 ⇒ 学生打印一行
    # `nsjail: out of memory` 即能伪造命中（红队实测把 `case_evidence` 落成 resource_limit）。
    # 修复后沙箱侧归因只读 `--log` 那份宿主自有日志，伪造路径消失（容器内 `test_i8_student_cannot_forge_a_sandbox_limit_attribution` 真跑钉住）。
    # 用途**仍限于**"给已判定的失败补限制项细节"：真实 CPU/内存/文件大小限制下 nsjail 自有诊断
    # 行 0 条（实测），这些标记没有被证明的真实扳机，**不得**用它决定"这是不是沙箱限制"。
    # 也不采用进程侧签名（`MemoryError`/`Cannot allocate memory`）：那同样是学生可控文本。
    ("memory", ("out of memory", "oom", "memory limit", "cgroup_mem_max")),
    ("cpu", ("cpu time limit", "rlimit_cpu", "cpu time exceeded")),
    ("processes", ("fork failed", "rlimit_nproc", "pids limit", "cannot fork")),
    ("files", ("file size limit", "rlimit_fsize")),
)

#: 沙箱日志 → 执行期安全违规的标记（同样只允许 `RunFacts.sandbox_log_text`）。
#:
#: seccomp 拒绝的**主**路径是结构化判定（SIGSYS 杀死进程，见 `classify_case_outcome`）；
#: 这里的文本标记是补充：生产配置 `log_level: ERROR` 下，只有 nsjail 自身报错才会留下这些字样。
VIOLATION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "sandbox_violation",
        ("seccomp", "bad system call", "operation not permitted", "read-only file system"),
    ),
    (
        "network_attempt",
        ("network is unreachable", "temporary failure in name resolution", "no such device"),
    ),
)

#: 执行宿主是 Linux，因此这里用 Linux 的 signal 编号常量，而不是当前平台的 `signal.SIGKILL`/`SIGSYS`
#: （Windows 的 `signal` 模块没有 SIGSYS，用 getattr 兜底会让"seccomp 杀死进程"永远无法归类）。
SIGSYS_NUMBER = 31
SIGKILL_NUMBER = 9

#: 判定"墙钟上限命中"的用时比例阈值：被杀且用时 ≥ 该比例的墙钟预算时才判超时。
#:
#: 为什么需要它：nsjail 自己的 `time_limit` 兜底若先开枪，父进程只看到一个 SIGKILL 退出码，
#: Linux 验收实测会把它误判成 `output_mismatch`（父进程的计时器没到期）。因此用"用时"这个
#: 第二信号把"跑到墙钟上限被杀"与"程序自己退出非 0"区分开。
WALL_LIMIT_HIT_RATIO = 0.9


# --- 进程树清理 -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KillReport:
    """整棵进程树的清理结果（`signalled=False` 且 `detail` 说明进程组已消失 = 视为已清理）。"""

    pid: int
    process_group: int | None
    signalled: bool
    detail: str | None


class ProcessTreeKiller(Protocol):
    """进程树杀手协议（可注入 ⇒ 平台无关地测试"超时路径清理整棵树"的逻辑）。"""

    def kill_tree(self, pid: int, process_group: int | None) -> KillReport: ...


class PosixProcessGroupKiller:
    """POSIX 进程组杀手：`killpg(pgid, SIGKILL)`。

    前提是子进程以 `start_new_session=True` 启动（独立会话 ⇒ 进程组 = 整棵树）；
    `killpg` 可注入，因此判定逻辑在任何平台上都可测。
    """

    def __init__(
        self,
        *,
        killpg: Callable[[int, int], None] | None = None,
        signal_number: int = SIGKILL_NUMBER,
    ) -> None:
        self._killpg = killpg if killpg is not None else _default_killpg
        self._signal_number = signal_number

    def kill_tree(self, pid: int, process_group: int | None) -> KillReport:
        group = process_group if process_group is not None else pid
        try:
            self._killpg(group, self._signal_number)
        except ProcessLookupError:
            return KillReport(pid, group, False, "process group already gone")
        except PermissionError:
            return KillReport(pid, group, False, "permission denied when killing process group")
        except OSError as error:
            return KillReport(pid, group, False, f"kill failed: {type(error).__name__}")
        return KillReport(pid, group, True, f"signal {self._signal_number} to process group {group}")


def _default_killpg(group: int, signal_number: int) -> None:
    os.killpg(group, signal_number)


# --- 进程启动（nsjail，固定参数数组） --------------------------------------------------------


class RunningProcess(Protocol):
    """已启动进程的最小接口：`wait` 返回退出码，超时返回 `None`。"""

    pid: int
    process_group: int | None

    def wait(self, timeout_seconds: float) -> int | None: ...
    def kill_tree(self) -> KillReport: ...


class ProcessLauncher(Protocol):
    """启动器协议（测试注入实现 ⇒ 平台无关地覆盖整条 run_case 流水线）。"""

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        stdin_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> RunningProcess: ...


class _PopenProcess:
    """`subprocess.Popen` 的薄包装（独立会话 ⇒ 进程组可整体清理）。"""

    def __init__(self, popen: Any, killer: ProcessTreeKiller) -> None:
        self._popen = popen
        self._killer = killer
        self.pid = int(popen.pid)
        self.process_group = _process_group_of(self.pid)

    def wait(self, timeout_seconds: float) -> int | None:
        try:
            return int(self._popen.wait(timeout=timeout_seconds))
        except subprocess.TimeoutExpired:
            return None

    def kill_tree(self) -> KillReport:
        return self._killer.kill_tree(self.pid, self.process_group)


def _process_group_of(pid: int) -> int | None:
    getpgid = getattr(os, "getpgid", None)
    if getpgid is None:  # pragma: no cover - 非 POSIX 平台没有 getpgid
        return None
    try:
        return int(getpgid(pid))
    except OSError:  # pragma: no cover - 进程已退出
        return None


class PosixNsjailLauncher:
    """以**固定参数数组**启动 nsjail；非 POSIX 平台一律拒绝（不降级执行）。

    `popen_factory` 可注入：测试据此断言"实参是 `list[str]`、无 shell 拼接、`shell` 非真值"。
    """

    def __init__(
        self,
        *,
        killer: ProcessTreeKiller | None = None,
        popen_factory: Callable[..., Any] | None = None,
        os_name: str | None = None,
    ) -> None:
        self._killer = killer if killer is not None else PosixProcessGroupKiller()
        self._popen_factory = popen_factory if popen_factory is not None else subprocess.Popen
        self._os_name = os.name if os_name is None else os_name

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        stdin_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> RunningProcess:
        if self._os_name != "posix":
            raise SandboxUnavailableError(
                f"nsjail requires a POSIX host (os.name={self._os_name}); refusing to execute"
            )
        if isinstance(argv, str) or not isinstance(argv, (list, tuple)):
            raise TypeError("argv must be a list of strings, never a shell string")
        for index, item in enumerate(argv):
            if not isinstance(item, str) or "\x00" in item:
                raise TypeError(f"argv[{index}] must be a NUL-free string")
        with contextlib.ExitStack() as stack:
            stdin_stream = None
            if stdin_text is not None:
                Path(stdin_path).write_bytes(stdin_text.encode("utf-8"))
                stdin_stream = stack.enter_context(Path(stdin_path).open("rb"))
            output_handle = stack.enter_context(Path(stdout_path).open("wb"))
            error_handle = stack.enter_context(Path(stderr_path).open("wb"))
            popen = self._popen_factory(
                list(argv),
                cwd=str(cwd),
                env=dict(env),
                stdin=stdin_stream if stdin_stream is not None else subprocess.DEVNULL,
                stdout=output_handle.fileno(),
                stderr=error_handle.fileno(),
                start_new_session=True,
                shell=False,
                close_fds=True,
            )
        return _PopenProcess(popen, self._killer)


def launcher_for(policy: RunnerPolicy) -> ProcessLauncher:
    """生产装配：只有 nsjail 启动器（**没有**环境变量开关可切换执行后端）。"""
    del policy  # 策略只影响 nsjail 配置文件内容，不切换后端
    return PosixNsjailLauncher()


# --- 输出采集与日志规范化 -------------------------------------------------------------------


def read_capped(path: Path, limit: int) -> tuple[bytes, bool]:
    """读取至多 `limit` 字节并报告是否被截断（避免把无界输出读进内存）。"""
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return b"", False
    if len(data) > limit:
        return data[:limit], True
    return data, False


def _scan_sandbox_log(text: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    """在**宿主自有的沙箱日志**（`RunFacts.sandbox_log_text`）上做标记匹配。

    可信性来自**结构性边界**，不是字符串启发式：这份文本由 `nsjail --log <run_dir>/<stem>.nsjail.log`
    产出，文件在工作区之外，而被囚进程只有工作区一个 rw 挂载（根是只读绑定）⇒ 学生**写不进**它，
    所以"学生打印 `nsjail: out of memory`"不再能命中（容器内 `test_i8_student_cannot_forge_a_sandbox_limit_attribution` 真跑钉住，且该用例同时
    验证从沙箱内写该路径会被拒）。

    因此这里**没有** "nsjail" 之类的行过滤：整份日志都是宿主自有诊断（实测：`--log` 下 nsjail 的
    诊断行只写这份文件，学生 stderr 里只有学生自己的字节；nsjail 自身失败时学生 stderr 为 0 字节）。
    调用方**不得**把学生流（stdout/stderr）传进来——那会把这个边界重新变成可伪造的。
    """
    for line in text.split("\n"):
        lowered = line.lower()
        for key, needles in table:
            if any(needle in lowered for needle in needles):
                return key
    return None


def _killed_by(exit_code: int | None, sig: int) -> bool:
    """是否等价于"被信号 sig 杀死"：直接负值、shell 约定的 128+sig、或 256-sig。"""
    if exit_code is None:
        return False
    return exit_code in (-sig, 128 + sig, 256 - sig)


# --- 归类与证据 -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunFacts:
    """一次执行的原始事实（归类函数的唯一输入 ⇒ 归类逻辑可独立测试）。"""

    exit_code: int | None
    timed_out: bool
    stdout_text: str
    stderr_text: str
    #: 宿主自有的沙箱日志（`nsjail --log` 写的那份文件）——**唯一**允许做"沙箱侧归因"的文本来源。
    #: 学生流（`stdout_text`/`stderr_text`）不得参与归因：它们在学生手里，任何字符串启发式都可伪造。
    sandbox_log_text: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_comparison: ComparisonOutcome | None
    stderr_comparison: ComparisonOutcome | None
    fixture_outcomes: tuple[FixtureOutcome, ...]
    limit_exceeded_files: bool
    tree_still_alive: bool
    kill_detail: str | None
    duration_ms: int | None = None
    #: 运行前后工作区差异（资源口径的计数之外，证据口径的清单）；`None` = 本次没有真的执行。
    changes: WorkspaceChanges | None = None
    #: cgroup v2 取样读数；`None` 或各项为 `None` 都表示**没有测量**（绝不是"用了 0"）。
    peaks: CgroupV2Peaks | None = None


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """归类结果：是否通过、失败类别（契约枚举）、命中的限制项、违规明细与脱敏消息。"""

    passed: bool
    failure_category: FailureCategory | None
    resource_limit_hit: str | None
    violations: tuple[SecurityFinding, ...]
    message: str
    #: 声明了却未产出的文件 / 已产出但摘要不符的文件（工作区相对路径，已排序）。
    #: 它们进证据与解释，不进 `message`（消息是给日志与人的一句话）。
    missing_outputs: tuple[str, ...] = ()
    mismatched_outputs: tuple[str, ...] = ()


def classify_case_outcome(
    facts: RunFacts, *, expected_exit_code: int | None, wall_limit_ms: int | None = None
) -> CaseOutcome:
    """把执行事实归类为契约枚举内的失败类别（学生侧失败一律**返回**，不抛异常）。

    判定顺序即优先级：超时（B2 计时器或墙钟命中）→ 文件数超限 → 输出超限 → 安全违规 →
    缺产出 → 产出不符 → import 失败 → 输出不符 → 资源/信号 → 退出码不符 → 通过。

    **归因面的边界（红队证伪 → 修复后的实测结论）**：
    1. 沙箱侧归因**只读** `RunFacts.sandbox_log_text`（`nsjail --log` 写出的宿主自有日志），
       **绝不**读学生流。此前 `_scan_markers` 只做 `"nsjail" in line` 过滤，而学生 stdout/stderr 与
       nsjail 自有日志写进**同一份文件** ⇒ 学生打印一行 `nsjail: out of memory` 即能把归类改写成
       `resource_limit/memory`（红队用真实编排层落库到 `b2.case_evidence`）。现在这份日志在工作区之外、
       被囚进程只有只读根 ⇒ **写不进**，伪造路径消失（容器内 `test_i8_student_cannot_forge_a_sandbox_limit_attribution` 真跑钉住，含"从沙箱内写该
       日志路径被拒"这一条）。
    2. 真实限制下 nsjail **自有诊断行 0 条**（CPU/内存/文件大小三类实测；stderr 只有学生自己的
       traceback）⇒ 限制标记**没有**被证明的真实扳机。因此它仍只用于给**已判定**的失败补"限制项"
       细节（`_exit_code_failure` 里命中 SIGKILL/信号死亡时填 `resource_limit_hit`），
       **不**提前到输出比较之前做归因。
    3. `HARNESS_RESOLUTION_MARKER` 仍扫学生 stderr：它是宿主入口脚本**有意**写进子进程 stderr 的标记。
       学生可以伪造它，但伪造只会把一种学生侧失败（`output_mismatch`）换成另一种学生侧失败
       （`import_error`），拿不到通过、也改不了归因主体，故不构成越权面。
    """
    violations: list[SecurityFinding] = []
    if facts.tree_still_alive:
        violations.append(
            make_finding(
                category="sandbox_violation",
                severity="high",
                message="process tree did not terminate within the grace period after SIGKILL",
                rule_id="runner.process_tree",
            )
        )
    if facts.timed_out:
        return _failure(facts, "timeout", "wall", violations)
    if _hit_wall_limit(facts, wall_limit_ms):
        return _failure(facts, "timeout", "wall", violations)
    if facts.limit_exceeded_files:
        return _failure(facts, "resource_limit", "files", violations)
    if facts.stdout_truncated or facts.stderr_truncated:
        return _failure(facts, "resource_limit", "output", violations)
    if _killed_by(facts.exit_code, SIGSYS_NUMBER):
        violations.append(
            make_finding(
                category="sandbox_violation",
                severity="high",
                message="sandbox terminated the process: denied system call (seccomp default-deny)",
                rule_id="runner.seccomp",
            )
        )
        return CaseOutcome(
            False,
            "security_blocked",
            None,
            tuple(violations),
            "sandbox terminated the process: denied system call (seccomp default-deny)",
        )
    violation_key = _scan_sandbox_log(facts.sandbox_log_text, VIOLATION_MARKERS)
    if violation_key is not None:
        violations.append(
            make_finding(
                category=violation_key,
                severity="high",
                message=f"sandbox reported a runtime violation: {violation_key}",
                rule_id="runner.sandbox_log",
            )
        )
        return CaseOutcome(
            False,
            "security_blocked",
            None,
            tuple(violations),
            f"sandbox reported a runtime violation: {violation_key}",
        )
    missing = sorted(item.path for item in facts.fixture_outcomes if item.actual_sha256 is None)
    if missing:
        return CaseOutcome(
            False,
            "missing_output",
            None,
            tuple(violations),
            f"expected output file(s) not produced: {', '.join(missing)}",
            missing_outputs=tuple(missing),
        )
    mismatched = sorted(
        item.path for item in facts.fixture_outcomes if item.actual_sha256 != item.expected_sha256
    )
    if mismatched:
        return CaseOutcome(
            False,
            "output_mismatch",
            None,
            tuple(violations),
            f"output file digest mismatch: {', '.join(mismatched)}",
            mismatched_outputs=tuple(mismatched),
        )
    if HARNESS_RESOLUTION_MARKER in facts.stderr_text:
        return CaseOutcome(
            False,
            "import_error",
            None,
            tuple(violations),
            "callable entry could not be resolved inside the workspace"
            " (module import failed or attribute is not callable)",
        )
    if facts.stdout_comparison is not None and not facts.stdout_comparison.matched:
        return CaseOutcome(False, "output_mismatch", None, tuple(violations), facts.stdout_comparison.detail)
    if facts.stderr_comparison is not None and not facts.stderr_comparison.matched:
        return CaseOutcome(False, "output_mismatch", None, tuple(violations), facts.stderr_comparison.detail)
    exit_failure = _exit_code_failure(facts, expected_exit_code)
    if exit_failure is not None:
        if exit_failure.violations:
            return exit_failure
        return CaseOutcome(
            exit_failure.passed,
            exit_failure.failure_category,
            exit_failure.resource_limit_hit,
            tuple(violations),
            exit_failure.message,
        )
    return CaseOutcome(True, None, None, tuple(violations), _pass_message(facts))


def _hit_wall_limit(facts: RunFacts, wall_limit_ms: int | None) -> bool:
    """墙钟命中判定：进程**被杀**（信号/无退出码/nsjail 异常码）且用时接近墙钟预算。

    只给"用时"这个第二信号，不看 stderr 文本（Linux 验收实测 nsjail 在此路径下不产生可解析日志）。
    """
    if wall_limit_ms is None or wall_limit_ms <= 0 or facts.duration_ms is None:
        return False
    killed = (
        facts.exit_code is None
        or facts.exit_code < 0
        or _killed_by(facts.exit_code, SIGKILL_NUMBER)
        or facts.exit_code == 255
    )
    if not killed:
        return False
    return facts.duration_ms >= int(wall_limit_ms * WALL_LIMIT_HIT_RATIO)


def _failure(
    facts: RunFacts, category: FailureCategory, hit: str | None, violations: list[SecurityFinding]
) -> CaseOutcome:
    return CaseOutcome(False, category, hit, tuple(violations), _limit_message(facts, category))


def _exit_code_failure(facts: RunFacts, expected_exit_code: int | None) -> CaseOutcome | None:
    """退出码相关的失败判定（`None` 表示退出码本身不构成失败）。"""
    if facts.exit_code is None:
        return CaseOutcome(
            False,
            "crash",
            None,
            (),
            "process produced no exit code (terminated abnormally before exit)",
        )
    if expected_exit_code is not None and facts.exit_code == expected_exit_code:
        return None
    if expected_exit_code is None and facts.exit_code == 0:
        return None
    hit = _scan_sandbox_log(facts.sandbox_log_text, LIMIT_MARKERS)
    if _killed_by(facts.exit_code, SIGKILL_NUMBER) or facts.exit_code < 0 or hit is not None:
        return CaseOutcome(
            False,
            "resource_limit",
            hit,
            (
                make_finding(
                    category="resource_limit",
                    severity="medium",
                    message=f"process was terminated by the sandbox (limit item: {hit or 'unknown'})",
                    rule_id="runner.resource_limit",
                ),
            ),
            f"process was terminated by the sandbox (limit item: {hit or 'unknown'})",
        )
    if expected_exit_code is not None:
        return CaseOutcome(
            False,
            "crash",
            None,
            (),
            f"exit code {facts.exit_code} does not match the expected {expected_exit_code}",
        )
    if facts.exit_code == 255:
        return CaseOutcome(
            False,
            "crash",
            None,
            (),
            "exit code 255: nsjail or the child terminated abnormally (limit item unknown)",
        )
    return CaseOutcome(
        False,
        "crash",
        None,
        (),
        f"process exited with an unexpected code {facts.exit_code}",
    )


def _limit_message(facts: RunFacts, category: FailureCategory) -> str:
    parts = [category, f"exit_code={facts.exit_code}", f"timed_out={facts.timed_out}"]
    if facts.stdout_truncated or facts.stderr_truncated:
        parts.append("output truncated at max_output_bytes")
    if facts.limit_exceeded_files:
        parts.append("created files exceeded max_files_created")
    if facts.kill_detail:
        parts.append(f"process tree cleanup: {facts.kill_detail}")
    return "; ".join(parts)


def _pass_message(facts: RunFacts) -> str:
    details = []
    if facts.stdout_comparison is not None:
        details.append(f"stdout {facts.stdout_comparison.detail}")
    if facts.stderr_comparison is not None:
        details.append(f"stderr {facts.stderr_comparison.detail}")
    if not details:
        details.append("no comparison declared")
    return "passed: " + "; ".join(details)


def make_finding(
    *,
    category: SecurityCategory,
    severity: SecuritySeverity,
    message: str,
    rule_id: str | None,
) -> SecurityFinding:
    """构造执行期 finding；`finding_id` 由内容派生（确定性，绝不用 uuid4）。"""
    payload = json.dumps(
        {"category": category, "severity": severity, "message": message, "rule_id": rule_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return SecurityFinding(
        finding_id=f"SF-{sha256_hex(payload.encode('utf-8'))[:32]}",
        category=category,
        severity=severity,
        detected_in="runtime",
        message=message[:MESSAGE_LIMIT],
        rule_id=rule_id,
    )


def input_digest_of(
    package: EvaluationPackage,
    submission: SubmissionRunInput,
    case: PackageCase,
    adapter: PackageAdapter,
    plan: ExecutionPlan,
    policy: RunnerPolicy,
) -> str:
    """用例输入摘要：绑定用例声明、有效输入、提交文件摘要、package 与策略摘要。"""
    files = sorted(submission.files, key=lambda item: item.path)
    payload = {
        "case_id": case.case_id,
        "ordinal": case.ordinal,
        "name": case.name,
        "adapter": adapter.kind,
        "language": package.language,
        "arguments": list(plan.arguments),
        "stdin_sha256": sha256_hex((plan.stdin_text or "").encode("utf-8")),
        "expected_stdout_sha256": sha256_hex((plan.expected_stdout or "").encode("utf-8")),
        "expected_stderr_sha256": sha256_hex((plan.expected_stderr or "").encode("utf-8")),
        "expected_exit_code": plan.expected_exit_code,
        "stdout_strategy": plan.stdout_strategy,
        "stderr_strategy": plan.stderr_strategy,
        "numeric_tolerance": plan.numeric_tolerance,
        "package_digest": package.package_digest,
        "runner_policy_digest": policy_digest(policy),
        "input_fixtures": [item.content_sha256 for item in plan.input_fixtures],
        "expected_outputs": [item.content_sha256 for item in plan.expected_outputs],
        "submission_files": [
            {"path": item.path, "content_sha256": item.content_sha256} for item in files
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_hex(canonical.encode("utf-8"))


def evidence_digest_of(evidence: CaseEvidence) -> str:
    """证据摘要：由**除 `duration_ms`、`resource` 与摘要本身**之外的全部字段规范化计算。

    两个排除项各有理由，且都不是"为了方便"：

    - `duration_ms`：墙钟测量值。纳入即等于"同输入永远得不到同摘要"，而 I25 要求的正是
      "相同五项绑定摘要 ⇒ 相同证据摘要"。
    - `resource`：cgroup 读数（内存峰值、CPU 时间、进程峰值）是**同一份输入在不同宿主状态下**
      也会变的环境读数，与 `duration_ms` 同类。纳入它会让"同绑定摘要 ⇒ 同 `result_digest`"
      在真实 Linux 上**永远不成立**——那正是 ADR-0005 第 5 条要保住的东西。它照旧进报告
      （可读、可对比限额），只是不参与"可重放性"的等式。

    反过来，`artifacts`（含逐文件内容摘要）与 `mismatch_line`/`missing_outputs`/`mismatched_outputs`
    **参与**摘要：它们是确定性事实，纳入它们让摘要对"产出文件被改动"这类差异有区分力。
    """
    payload: dict[str, Any] = {
        "case_id": evidence.case_id,
        "ordinal": evidence.ordinal,
        "weight": evidence.weight,
        "passed": evidence.passed,
        "failure_category": evidence.failure_category,
        "input_digest": evidence.input_digest,
        "stdout_digest": evidence.stdout_digest,
        "stderr_digest": evidence.stderr_digest,
        "actual_output_excerpt": evidence.actual_output_excerpt,
        "expected_output_excerpt": evidence.expected_output_excerpt,
        "exit_code": evidence.exit_code,
        "resource_limit_hit": evidence.resource_limit_hit,
        "mismatch_line": evidence.mismatch_line,
        "missing_outputs": list(evidence.missing_outputs),
        "mismatched_outputs": list(evidence.mismatched_outputs),
        "artifacts": _artifacts_payload(evidence.artifacts),
        "sandbox_violations": [
            {
                "finding_id": item.finding_id,
                "category": item.category,
                "severity": item.severity,
                "detected_in": item.detected_in,
                "message": item.message,
                "rule_id": item.rule_id,
            }
            for item in evidence.sandbox_violations
        ],
        "message": evidence.message,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_hex(canonical.encode("utf-8"))


def _artifacts_payload(artifacts: CaseArtifacts | None) -> dict[str, Any] | None:
    if artifacts is None:
        return None
    return {
        "expected_sha256": artifacts.expected_sha256,
        "actual_sha256": artifacts.actual_sha256,
        "created": [
            {
                "path": item.path,
                "content_sha256": item.content_sha256,
                "size_bytes": item.size_bytes,
            }
            for item in artifacts.created
        ],
        "modified": [
            {
                "path": item.path,
                "content_sha256": item.content_sha256,
                "size_bytes": item.size_bytes,
            }
            for item in artifacts.modified
        ],
        "deleted": [
            {
                "path": item.path,
                "content_sha256": item.content_sha256,
                "size_bytes": item.size_bytes,
            }
            for item in artifacts.deleted
        ],
        "truncated": artifacts.truncated,
    }


def redact(text: str, workspace: Path, limit: int = MESSAGE_LIMIT) -> str:
    """脱敏：去掉宿主工作区绝对路径（证据与日志不得含内部绝对路径）并截断。"""
    cleaned = text.replace(Path(workspace).as_posix(), "<workspace>")
    cleaned = cleaned.replace(str(workspace), "<workspace>")
    cleaned = "".join(ch if ch.isprintable() or ch == "\n" else "\ufffd" for ch in cleaned)
    return cleaned if len(cleaned) <= limit else cleaned[:limit]


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


# --- run_case -------------------------------------------------------------------------------


def run_case(
    package: EvaluationPackage,
    submission: SubmissionRunInput,
    *,
    probe: Callable[..., Any] | None = None,
    launcher: ProcessLauncher | None = None,
) -> CaseEvidence:
    """执行**一个**用例并返回证据。

    生产调用只用两个位置参数（冻结签名）；`probe` / `launcher` 是**仅测试可见**的可选接缝，
    默认值即生产实现（`probe_sandbox` / `PosixNsjailLauncher`）——**没有环境变量开关**。

    只有三种情形抛异常：沙箱不可用（`SandboxUnavailableError`）、package 不可用
    （`PackageUnavailableError`）、内部缺陷（`InternalEvaluationError`）。学生侧失败一律返回证据。
    """
    started = time.monotonic()
    try:
        case = resolve_case(package, submission)
        adapter = resolve_adapter(package, submission)
        policy = resolve_policy(package, submission)
        plan = build_execution_plan(
            adapter=adapter, case=case, policy=policy, language=package.language
        )
        digest_input = input_digest_of(package, submission, case, adapter, plan, policy)
        probe_function = probe_sandbox if probe is None else probe
        availability = probe_function(policy)
        if not availability.available:
            # ① 前置判定失败：此刻**没有任何进程被创建**，也绝不允许退化为裸进程执行。
            raise SandboxUnavailableError(f"sandbox unavailable: {availability.reason}")
        active_launcher = launcher_for(policy) if launcher is None else launcher
        workspace = Path(submission.workspace)
        entry = adapter.callable
        problems = prepare_case_workspace(
            plan,
            workspace,
            entry_module=entry.module if entry is not None else None,
            entry_attribute=entry.attribute if entry is not None else None,
            arguments_json=entry.arguments_json if entry is not None else None,
        )
        if problems:
            return build_evidence(
                case=case,
                outcome=CaseOutcome(False, "infrastructure_error", None, (), join_problems(problems)),
                plan=plan,
                workspace=workspace,
                digest_input=digest_input,
                stdout_text="",
                stderr_text="",
                exit_code=None,
                duration_ms=elapsed_ms(started),
            )
        if not entry_script_exists(plan, workspace):
            return build_evidence(
                case=case,
                outcome=CaseOutcome(
                    False,
                    "crash",
                    None,
                    (),
                    f"entry script is not present in the workspace: {plan.entry_name}",
                ),
                plan=plan,
                workspace=workspace,
                digest_input=digest_input,
                stdout_text="",
                stderr_text="",
                exit_code=None,
                duration_ms=elapsed_ms(started),
            )
        before = snapshot_workspace(workspace)
        facts = _execute_case(
            plan=plan, policy=policy, workspace=workspace, launcher=active_launcher, before=before
        )
        outcome = classify_case_outcome(
            facts,
            expected_exit_code=plan.expected_exit_code,
            wall_limit_ms=int(policy.max_wall_seconds * 1000),
        )
        return build_evidence(
            case=case,
            outcome=outcome,
            plan=plan,
            workspace=workspace,
            digest_input=digest_input,
            stdout_text=facts.stdout_text,
            stderr_text=facts.stderr_text,
            exit_code=facts.exit_code,
            duration_ms=elapsed_ms(started),
            fixture_outcomes=facts.fixture_outcomes,
            mismatch_line=first_mismatch_line(facts),
            changes=facts.changes,
            resource=case_resource_from_peaks(
                facts.peaks if facts.peaks is not None else CgroupV2Peaks(),
                # 墙钟用**沙箱运行时长**（限制就是对这个时长生效的），不是整个用例的耗时。
                wall_seconds=(
                    None if facts.duration_ms is None else facts.duration_ms / 1000
                ),
                limit_hit=limit_hit_for(outcome.resource_limit_hit),
            ),
        )
    except (SandboxUnavailableError, PackageUnavailableError, InternalEvaluationError):
        raise
    except Exception as error:  # 内部异常不得被吞、不得伪装成用例失败或通过
        raise InternalEvaluationError(
            f"run_case failed internally: {type(error).__name__}: {error}"
        ) from error


def first_mismatch_line(facts: RunFacts) -> int | None:
    """首个不一致行号（stdout 优先，其次 stderr）；比较通过时为 `None`。

    它是**行号**而不是内容：解释可以说"第 3 行不同"，但期望输出本身仍受保护。
    """
    for comparison in (facts.stdout_comparison, facts.stderr_comparison):
        if comparison is not None and not comparison.matched and comparison.mismatch_line is not None:
            return comparison.mismatch_line
    return None


def _execute_case(
    *,
    plan: ExecutionPlan,
    policy: RunnerPolicy,
    workspace: Path,
    launcher: ProcessLauncher,
    before: Mapping[str, WorkspaceFile],
) -> RunFacts:
    """启动沙箱、采集有界输出与**宿主自有沙箱日志**、必要时清理整棵进程树，并汇总事实。

    沙箱日志（`<stem>.nsjail.log`，由 `--log` 写入）与配置/标准流同放工作区**旁边**：该目录在沙箱内
    被遮蔽为只读空 tmpfs，因此被囚进程**既读不到也写不进**；它是"沙箱侧归因"的唯一文本来源。
    读取后即刻删除：日志可能含宿主绝对路径，不进证据、不落库（证据只留摘要与结构化结论）。
    每次启动前先 unlink：nsjail 以追加方式打开日志，清掉旧字节才能保证"本轮扫描只认本轮的字节"。
    """
    run_dir = workspace.parent
    stem = workspace.name
    config_path = run_dir / f"{stem}.nsjail.cfg"
    sandbox_log_path = run_dir / f"{stem}.nsjail.log"
    stdout_path = run_dir / f"{stem}.stdout"
    stderr_path = run_dir / f"{stem}.stderr"
    stdin_path = run_dir / f"{stem}.stdin"
    config_path.write_text(render_nsjail_config(policy, workspace), encoding="utf-8")
    _unlink_quietly(sandbox_log_path)  # nsjail 以追加方式打开日志：本轮扫描只认本轮产生的字节
    argv = build_argv(
        policy,
        config_path=config_path,
        sandbox_log_path=sandbox_log_path,
        language=plan.language,
        script=plan.script,
        arguments=plan.arguments,
    )
    timed_out = False
    kill_detail: str | None = None
    tree_still_alive = False
    exit_code: int | None = None
    peaks = CgroupV2Peaks()
    run_started = time.monotonic()
    try:
        process = launcher.start(
            argv,
            cwd=workspace,
            env=dict(sandbox_env(policy)),
            stdin_text=plan.stdin_text,
            stdin_path=stdin_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        # 取样必须**在用例运行期间**发生：nsjail 在子进程被 reap 时删除该 cgroup（见 `runner.cgroup`
        # 的模块说明），进程结束之后那份读数已经不存在了。读不到就是 None，绝不填 0。
        sampler = CaseResourceSampler(policy=policy, sandbox_pid=process.pid)
        sampler.start()
        try:
            exit_code = process.wait(policy.max_wall_seconds)
            if exit_code is None:
                timed_out = True
                kill_report = process.kill_tree()  # 恰好一次：清理整棵进程树（进程组）
                kill_detail = kill_report.detail
                exit_code = process.wait(TERM_GRACE_SECONDS)
                tree_still_alive = exit_code is None
        finally:
            peaks = sampler.stop()
        stdout_bytes, stdout_truncated = read_capped(stdout_path, policy.max_output_bytes)
        stderr_bytes, stderr_truncated = read_capped(stderr_path, policy.max_output_bytes)
        log_bytes, _ = read_capped(sandbox_log_path, SANDBOX_LOG_MAX_BYTES)
    finally:
        for path in (stdin_path, stdout_path, stderr_path, sandbox_log_path):
            _unlink_quietly(path)
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    sandbox_log_text = log_bytes.decode("utf-8", errors="replace")
    run_duration_ms = max(0, int((time.monotonic() - run_started) * 1000))
    after = snapshot_workspace(workspace)
    created = created_files(before, after)
    limit_exceeded_files = (
        policy.max_files_created is not None and len(created) > policy.max_files_created
    )
    return RunFacts(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        sandbox_log_text=sandbox_log_text,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        stdout_comparison=compare_stdout(
            stdout_text, plan.expected_stdout, plan.stdout_strategy, plan.numeric_tolerance
        ),
        stderr_comparison=compare_stderr(stderr_text, plan.expected_stderr, plan.stderr_strategy),
        fixture_outcomes=collect_fixture_outcomes(plan, workspace),
        limit_exceeded_files=limit_exceeded_files,
        tree_still_alive=tree_still_alive,
        kill_detail=kill_detail,
        duration_ms=run_duration_ms,
        changes=workspace_changes(before, after),
        peaks=peaks,
    )


def _unlink_quietly(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        return


def build_case_artifacts(
    fixture_outcomes: tuple[FixtureOutcome, ...], changes: WorkspaceChanges | None
) -> CaseArtifacts | None:
    """文件级证据（契约 `case_artifacts`）。

    `expected_sha256`/`actual_sha256` 只在用例**恰好声明一个**产出文件时填写：契约只有一个槽位，
    而用例可以声明多个产出文件——挑其中一个写进去会让读者以为"这就是那个文件"。多文件时两者为
    `None`，逐文件的实际情况仍在 `created`/`modified`/`deleted` 的清单里（附内容摘要），
    以及 `missing_outputs`/`mismatched_outputs` 指名的路径上。

    `changes is None` ⇒ 本用例**没有真的执行**（前置失败），此时返回 `None`（"没有产物"），
    而不是一个每个字段都空的对象（那会被读成"执行了但什么都没产出"）。
    """
    if changes is None:
        return None
    single = fixture_outcomes[0] if len(fixture_outcomes) == 1 else None
    return CaseArtifacts(
        expected_sha256=None if single is None else single.expected_sha256,
        actual_sha256=None if single is None else single.actual_sha256,
        created=changes.created,
        modified=changes.modified,
        deleted=changes.deleted,
        truncated=changes.truncated,
    )


def build_evidence(
    *,
    case: PackageCase,
    outcome: CaseOutcome,
    plan: ExecutionPlan,
    workspace: Path,
    digest_input: str,
    stdout_text: str,
    stderr_text: str,
    exit_code: int | None,
    duration_ms: int,
    fixture_outcomes: tuple[FixtureOutcome, ...] = (),
    mismatch_line: int | None = None,
    changes: WorkspaceChanges | None = None,
    resource: CaseResource | None = None,
) -> CaseEvidence:
    """组装 `CaseEvidence`（字段齐全、摘录截断、摘要确定性）。"""
    file_excerpt = next(
        (item.actual_excerpt for item in fixture_outcomes if item.actual_excerpt), None
    )
    excerpt_source = stdout_text or (file_excerpt or "")
    evidence = CaseEvidence(
        case_id=case.case_id,
        ordinal=case.ordinal,
        weight=case.weight,
        passed=outcome.passed,
        failure_category=outcome.failure_category,
        input_digest=digest_input,
        stdout_digest=sha256_hex(stdout_text.encode("utf-8")),
        stderr_digest=sha256_hex(stderr_text.encode("utf-8")),
        actual_output_excerpt=(
            redact(excerpt_of_text(excerpt_source), workspace, EXCERPT_LIMIT)
            if excerpt_source
            else None
        ),
        expected_output_excerpt=(
            redact(excerpt_of_text(plan.expected_stdout), workspace, EXCERPT_LIMIT)
            if plan.expected_stdout is not None
            else None
        ),
        exit_code=exit_code,
        duration_ms=duration_ms,
        resource_limit_hit=outcome.resource_limit_hit,
        sandbox_violations=outcome.violations,
        message=redact(outcome.message, workspace, MESSAGE_LIMIT) if outcome.message else None,
        mismatch_line=mismatch_line,
        missing_outputs=outcome.missing_outputs,
        mismatched_outputs=outcome.mismatched_outputs,
        resource=resource,
        artifacts=build_case_artifacts(fixture_outcomes, changes),
        evidence_digest="",
    )
    return replace(evidence, evidence_digest=evidence_digest_of(evidence))


def resolve_case(package: EvaluationPackage, submission: SubmissionRunInput) -> PackageCase:
    """用例必须来自 package（调用方无法自带一份改过的用例）。"""
    declared = package.case_by_id(submission.case.case_id)
    if declared is None:
        raise InternalEvaluationError(f"case not present in the package: {submission.case.case_id}")
    if declared != submission.case:
        raise InternalEvaluationError(
            f"case {declared.case_id} differs between package and submission input"
            " (the package is authoritative)"
        )
    return declared


def resolve_adapter(package: EvaluationPackage, submission: SubmissionRunInput) -> PackageAdapter:
    """比较策略与 adapter 参数**只来自 package**（I21）；提交侧副本不一致即内部缺陷。"""
    if submission.adapter != package.adapter:
        raise InternalEvaluationError(
            "submission adapter differs from the package adapter: the comparison policy comes"
            " from the package only (I21)"
        )
    return package.adapter


def resolve_policy(package: EvaluationPackage, submission: SubmissionRunInput) -> RunnerPolicy:
    """执行策略必须与 package 声明的策略一致（包不可放宽常量，提交侧也不可改策略）。"""
    if submission.policy != package.security_policy:
        raise InternalEvaluationError(
            "submission policy differs from the package security_policy: refusing to execute"
        )
    return package.security_policy


__all__ = [
    "LIMIT_MARKERS",
    "MESSAGE_LIMIT",
    "SANDBOX_LOG_MAX_BYTES",
    "TERM_GRACE_SECONDS",
    "VIOLATION_MARKERS",
    "CaseOutcome",
    "KillReport",
    "PosixNsjailLauncher",
    "PosixProcessGroupKiller",
    "ProcessLauncher",
    "ProcessTreeKiller",
    "RunFacts",
    "RunningProcess",
    "build_case_artifacts",
    "build_evidence",
    "classify_case_outcome",
    "elapsed_ms",
    "evidence_digest_of",
    "first_mismatch_line",
    "input_digest_of",
    "launcher_for",
    "make_finding",
    "read_capped",
    "redact",
    "resolve_adapter",
    "resolve_case",
    "resolve_policy",
    "run_case",
]
