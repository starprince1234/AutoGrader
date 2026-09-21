"""沙箱策略：`RunnerPolicy`、规范化摘要、nsjail 配置生成、可用性前置判定。

决策依据：[docs/adr/ADR-0002-sandbox-execution-policy.md](../../docs/adr/ADR-0002-sandbox-execution-policy.md)
（nsjail + cgroup v2 + seccomp、禁网、只读根、非 root、进程树清理）与
`MAIN/docs/adr/ADR-0004-sandbox-technology-selection.md`（技术选型；**不可用时拒绝执行、绝不降级**）。
契约字段定义见 `MAIN/contracts/schemas/evaluation-package.schema.json` 的 `security_policy`（引用，不复制）。

本模块的不可协商项（违反即缺陷，不是配置自由度）：

1. **常量不可放宽**：`network=false` / `read_only_root=true` / `allow_root=false` 是常量；
   package 声明违规值一律**拒绝**（见 `runner.package`），绝不"取交集"式默默采纳。
2. **不可用即拒绝**：nsjail / cgroup v2 / 非特权用户命名空间任一缺失 ⇒ 判定不可执行，
   `run_case` 抛 `SandboxUnavailableError`，**绝不**退化为裸进程执行。
3. **固定参数数组**：`build_argv` 只返回 `list[str]`，任何内容都不进入 shell 字符串。
4. **确定性**：`policy_digest` 与 `render_nsjail_*` 都是纯函数，同输入逐字节相同。

**Linux 实测校准状态**：seccomp（kafel）语句与 cgroup v2 语义**已**由 Linux 验收工作项在真实内核上实测，
本模块据此修正过（见 `tests/linux/README.md` 与 `tests/linux/test_nsjail_acceptance.py` 的矩阵：
13 项通过、`5a2`（cgroup `memory.max` 杀进程）在本环境记"无法执行"）。
**`run_case` 的真实 Linux 执行路径已验收**：`tests/linux/test_nsjail_runner_acceptance.py` 在特权容器内
用生产签名跑真 nsjail（合法提交通过、五个恶意样本被拦并归类、超时零残留、真实编排两次同 `result_digest`、
学生伪造沙箱侧归因失败且从沙箱内写不了 `--log` 那份日志），本轮实测 `5 passed, 1 skipped`；
证据与限制见 `docs/phases/active/phase-01-implementation.md` 第 24/26 条。
仍未覆盖：容器内"沙箱不可用 ⇒ 拒绝执行"（容器里沙箱可用）与跨进程并发上限（见该文件第 28 条，状态 `active`）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from autograder_b2.domain import SandboxUnavailableError
from autograder_b2.domain.clock import sha256_hex

MIB = 1024 * 1024

#: 资源账本硬上限（`MAIN/docs/architecture/resource-budget.md` 第 1–2 节 +
#: `MAIN/docs/workstreams/B2.md` 第 8 节：单并发 nsjail 作业 ≤96 MB / 0.45 vCPU，后端 1 vCPU、
#: 初始只允许 1 个动态评测并发；契约 `security_policy` 的 `maximum` 与之同源）。
LEDGER_MAX_MEMORY_BYTES = 96 * MIB
LEDGER_MAX_CPU_PER_JOB = 0.45
LEDGER_MAX_CPUS_PER_JOB = 1

#: 契约 `security_policy` 的数值边界（`evaluation-package.schema.json`；引用，不复制字段表）。
MIN_MEMORY_BYTES = 16 * MIB
MAX_MEMORY_BYTES = LEDGER_MAX_MEMORY_BYTES  # 与账本同一数字：契约 maximum == 96 MB
MAX_CPU_SECONDS = 30.0
MAX_WALL_SECONDS = 60.0
MIN_OUTPUT_BYTES = 1024
MAX_OUTPUT_BYTES = 1 * MIB
MIN_PROCESSES = 1
MAX_PROCESSES = 64
MAX_FILES_CREATED = 10000

#: B2 自有的数值常量（契约**不含**这些字段，故由 B2 单点定死并随策略进摘要）。
MIN_OPEN_FILES = 4  # stdin/stdout/stderr + 至少一个工作 fd
MAX_OPEN_FILES = 1024  # 宿主 FD 预算（需在 Linux 验收时按宿主 limits 校准）
MIN_STACK_MIB = 1

#: nsjail 内的工作区消失点（沙箱内路径），与宿主工作区一对一双向绑定。
DEFAULT_WORKSPACE_MOUNT = "/workspace"

#: ns 内的 uid/gid：宿主当前用户 ↔ ns 内 nobody(65534)。**绝不**映射 inside_id "0"（allow_root=false）。
NSJAIL_SANDBOX_UID = "65534"
NSJAIL_SANDBOX_GID = "65534"

#: 沙箱内固定环境（`keep_env: false` 与之配套：不继承宿主环境，避免宿主路径/凭据进入沙箱）。
SANDBOX_ENV_FIXED: tuple[tuple[str, str], ...] = (
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("PATH", "/usr/local/bin:/usr/bin:/bin"),
    ("PYTHONDONTWRITEBYTECODE", "1"),
    ("PYTHONHASHSEED", "0"),
    ("PYTHONIOENCODING", "utf-8"),
)

_LANGUAGE_FIELD = {"python": "python_interpreter", "bash": "bash_interpreter"}
_SCRIPT_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")

#: kafel（nsjail 捆绑的 seccomp 策略编译器）**不认识**的系统调用名 → 用 `#define NAME <x86_64 编号>`
#: 补上（kafel README 的做法）。编号**取自内核头** `/usr/include/x86_64-linux-gnu/asm/unistd_64.h`
#: （Linux 验收容器内实测提取），不是凭记忆写的；表与内核头不一致即seccomp 语义错误。
#:
#: 两个**绝不能**放进本表的名字：
#: - `futimens`：x86_64 没有这个系统调用（glibc 的 futimens() 走 `utimensat`），
#: - `socketcall`：i386 专属；在 x86_64 上编号 102 是 **getuid**，定义它会把 getuid 当成 socketcall 杀掉。
SECCOMP_X86_64_DEFINES: tuple[tuple[str, int], ...] = (
    ("clone3", 435),
    ("close_range", 436),
    ("faccessat2", 439),
    ("fstat", 5),
    ("lstat", 6),
    ("openat2", 437),
    ("rseq", 334),
    ("sendfile", 40),
    ("stat", 4),
    ("statx", 332),
    ("umount2", 166),
    ("uname", 63),
)

#: 产生本表的 CPU 架构。`SECCOMP_X86_64_DEFINES` 的编号是架构相关的，因此非本架构上**必须拒绝执行**
#: （否则过滤器会放行/杀掉错误的系统调用——静默破坏 seccomp，比明确不完整更糟）。
SECCOMP_ARCH = "x86_64"

#: seccomp 允许清单：CPython 3.12 与 bash 的常规运行所需系统调用。
#: 由 Linux 验收容器内 `strace -f` 实测的工作负载系统调用集合（CPython 启动 + 标准库 + 文件 IO +
#: 线程 + 子进程 + 时间/随机/hash）反推，并取其为子集后人工收窄。
#: **强度边界**：缺项会让正常程序以 SIGSYS 被杀（表现为 `security_blocked`）；
#: 仅覆盖已实测的工作负载，未实测路径仍可能缺项（诚实记录见交付报告）。
SECCOMP_ALLOW: tuple[str, ...] = (
    "accept4",
    "access",
    "arch_prctl",
    "brk",
    "capget",
    "chdir",
    "chmod",
    "clock_getres",
    "clock_gettime",
    "clock_nanosleep",
    "clone",
    "clone3",
    "close",
    "close_range",
    "copy_file_range",
    "dup",
    "dup2",
    "dup3",
    "epoll_create1",
    "epoll_ctl",
    "epoll_pwait",
    "epoll_wait",
    "eventfd2",
    "execve",
    "execveat",
    "exit",
    "exit_group",
    "faccessat",
    "faccessat2",
    "fchdir",
    "fchmod",
    "fchmodat",
    "fdatasync",
    "fcntl",
    "fstat",
    "fstatfs",
    "fsync",
    "ftruncate",
    "futex",
    "getcwd",
    "getdents64",
    "getegid",
    "geteuid",
    "getgid",
    "getpeername",
    "getpgid",
    "getpgrp",
    "getpid",
    "getppid",
    "getrandom",
    "getresgid",
    "getresuid",
    "getrlimit",
    "getrusage",
    "getsid",
    "getsockname",
    "gettid",
    "gettimeofday",
    "getuid",
    "ioctl",
    "kill",
    "lseek",
    "lstat",
    "madvise",
    "membarrier",
    "mincore",
    "mkdir",
    "mkdirat",
    "mmap",
    "mprotect",
    "mremap",
    "msync",
    "munmap",
    "nanosleep",
    "newfstatat",
    "open",
    "openat",
    "openat2",
    "pipe",
    "pipe2",
    "poll",
    "ppoll",
    "prctl",
    "pread64",
    "prlimit64",
    "pselect6",
    "pwrite64",
    "read",
    "readlink",
    "readlinkat",
    "readv",
    "rename",
    "renameat",
    "renameat2",
    "restart_syscall",
    "rseq",
    "rt_sigaction",
    "rt_sigprocmask",
    "rt_sigreturn",
    "rt_sigtimedwait",
    "sched_getaffinity",
    "sched_getparam",
    "sched_getscheduler",
    "sched_yield",
    "select",
    "sendfile",
    "set_robust_list",
    "set_tid_address",
    "setitimer",
    "sigaltstack",
    "splice",
    "stat",
    "statfs",
    "statx",
    "symlink",
    "symlinkat",
    "sysinfo",
    "tgkill",
    "time",
    "timerfd_create",
    "timerfd_settime",
    "times",
    "truncate",
    "umask",
    "uname",
    "unlink",
    "unlinkat",
    "utime",
    "utimensat",
    "utimes",
    "vfork",
    "wait4",
    "waitid",
    "write",
    "writev",
)

#: 显式拒绝清单（默认拒绝已覆盖，此处列出是为了让"联网/提权/沙箱逃逸"意图可审阅、可测试）。
#: 注意：**不含 `socketcall`**（i386 专属；x86_64 上编号 102 与 getuid 冲突，见上方说明）。
SECCOMP_DENY: tuple[str, ...] = (
    "acct",
    "add_key",
    "adjtimex",
    "bpf",
    "capset",
    "chown",
    "chroot",
    "clock_adjtime",
    "clock_settime",
    "connect",
    "create_module",
    "delete_module",
    "fchown",
    "fchownat",
    "finit_module",
    "get_kernel_syms",
    "init_module",
    "ioperm",
    "iopl",
    "kcmp",
    "kexec_file_load",
    "kexec_load",
    "keyctl",
    "lchown",
    "listen",
    "mount",
    "move_pages",
    "name_to_handle_at",
    "nfsservctl",
    "open_by_handle_at",
    "perf_event_open",
    "pivot_root",
    "process_vm_readv",
    "process_vm_writev",
    "ptrace",
    "quotactl",
    "reboot",
    "recvfrom",
    "recvmmsg",
    "recvmsg",
    "request_key",
    "seccomp",
    "sendmmsg",
    "sendmsg",
    "sendto",
    "setdomainname",
    "setfsgid",
    "setfsuid",
    "setgid",
    "sethostname",
    "setns",
    "setregid",
    "setresgid",
    "setresuid",
    "setreuid",
    "settimeofday",
    "setuid",
    "shutdown",
    "socket",
    "socketpair",
    "swapoff",
    "swapon",
    "syslog",
    "tuxcall",
    "umount2",
    "unshare",
    "userfaultfd",
    "vhangup",
)


#: nsjail `time_limit` 相对策略墙钟上限的**兜底余量**（秒）。
#:
#: 墙钟上限由 B2 自己强制（`run_case` 的计时器 + 进程组清理），nsjail 的 `time_limit` 只是兜底：
#: 若两者取同一数值，nsjail 会先开枪（Linux 验收实测把超时误判成 `output_mismatch`，因为
#: 父进程看到的只是一个 SIGKILL 退出码，而不是自己的计时器到期）。留出余量后语义是确定的：
#: B2 负责超时判定与整棵树清理，nsjail 只保证"父进程万一卡住也不会永远跑下去"。
NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS = 5


def sandbox_env(policy: RunnerPolicy) -> tuple[tuple[str, str], ...]:
    """沙箱内环境变量（按名字排序 ⇒ 配置渲染确定性）。

    `HOME`/`TMPDIR` 指向工作区：工作区是唯一可写路径，若指向别处程序将直接失败而不是静默写出沙箱。
    """
    values = dict(SANDBOX_ENV_FIXED)
    values["HOME"] = policy.workspace_mount
    values["TMPDIR"] = policy.workspace_mount
    return tuple(sorted(values.items()))


@dataclass(frozen=True, slots=True)
class RunnerPolicy:
    """沙箱策略：契约 `security_policy` 的字段 + B2 拥有的执行细节。

    前 9 个字段对应契约 `security_policy`（`network`/`read_only_root`/`allow_root` 是常量，
    package 声明违规值即拒绝）；其余字段是 B2 的执行细节（解释器、nsjail 路径、工作区模板、
    seccomp/cgroup 开关常量），**不来自 package**，因此 package 无法借它们放宽隔离。
    """

    # —— 契约 security_policy ——
    network: bool = False
    read_only_root: bool = True
    allow_root: bool = False
    max_memory_bytes: int = LEDGER_MAX_MEMORY_BYTES
    max_cpu_seconds: float = 10.0
    max_wall_seconds: float = 20.0
    max_output_bytes: int = 64 * 1024
    max_processes: int = 8
    max_files_created: int | None = 256

    # —— B2 拥有的执行细节 ——
    python_interpreter: str = "/usr/bin/python3"
    bash_interpreter: str = "/bin/bash"
    nsjail_path: str = "/usr/bin/nsjail"
    sandbox_uid: str = NSJAIL_SANDBOX_UID
    sandbox_gid: str = NSJAIL_SANDBOX_GID
    seccomp_arch: str = SECCOMP_ARCH
    workspace_mount: str = DEFAULT_WORKSPACE_MOUNT
    workspace_template: str = "b2-case-"
    cgroup_v2_mount: str = "/sys/fs/cgroup"
    use_seccomp: bool = True
    use_cgroup: bool = True
    max_cpus: int = 1
    max_open_files: int = 64
    stack_mib: int = 8
    masked_paths: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        """规范化策略摘要（等同 `policy_digest(self)`；绑定摘要之一）。"""
        return policy_digest(self)

    def interpreter_for(self, language: str) -> str:
        """按语言取解释器路径（只来自策略，调用方无法指定任意可执行文件）。"""
        attribute = _LANGUAGE_FIELD.get(language)
        if attribute is None:
            raise ValueError(f"unsupported language: {language!r}")
        return str(getattr(self, attribute))


def policy_digest(policy: RunnerPolicy) -> str:
    """策略的规范化摘要：**全部字段** → 排序键 JSON（紧凑分隔符）→ SHA-256。

    同策略恒同摘要；改动任一字段即改摘要（`tests/test_runner_core.py` 逐字段断言）。
    """
    payload: dict[str, Any] = {}
    for field in fields(policy):
        value = getattr(policy, field.name)
        payload[field.name] = list(value) if isinstance(value, tuple) else value
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_hex(canonical.encode("utf-8"))


#: nsjail 的**候选安装位置**（声明路径之外的兜底搜索顺序）。
#:
#: 为什么需要兜底：`RunnerPolicy.nsjail_path` 的默认值只是一个**声明**，而 nsjail 从源码
#: `make install` 会落到 `/usr/local/bin/nsjail`（本项目 `verify/linux-nsjail/Dockerfile` 即如此），
#: 只有发行版包才可能在 `/usr/bin`。该字段又**不来自 package 策略**（契约的 `security_policy`
#: 里没有它），所以生产路径必须自己找得到二进制——否则"正常供应的 Linux 主机上永远判定沙箱不可用"
#: （fail-closed，但功能不可用；本缺陷由 Linux 真实 runner 验收发现）。
#:
#: 刻意**不用** `PATH` 查找：探测与启动必须看到同一个绝对路径（探针说有、启动时却找不到，
#: 或反之，都会让"可用性判定"变成谎话），而 `PATH` 会随调用环境变化。
NSJAIL_CANDIDATE_PATHS: tuple[str, ...] = (
    "/usr/local/bin/nsjail",
    "/usr/bin/nsjail",
    "/usr/local/sbin/nsjail",
    "/usr/sbin/nsjail",
    "/sbin/nsjail",
    "/opt/nsjail/bin/nsjail",
)


def _nsjail_search_order(policy: RunnerPolicy) -> tuple[str, ...]:
    """实际搜索顺序：策略声明的路径优先，其后是候选位置（去重、保持顺序）。"""
    order = [policy.nsjail_path]
    order.extend(path for path in NSJAIL_CANDIDATE_PATHS if path != policy.nsjail_path)
    return tuple(order)


def resolve_nsjail_path(
    policy: RunnerPolicy, *, is_executable: Callable[[str], bool] | None = None
) -> str | None:
    """解析**实际可用**的 nsjail 绝对路径；都不存在/不可执行时返回 `None`。

    探测（`probe_sandbox`）与启动（`build_argv`）都走这里，因此两者**永远一致**：
    探针说可用 ⇒ 启动拿到的是同一个路径。`is_executable` 可注入，便于在任意平台上测判定逻辑。

    **目录不算**：`os.access(dir, X_OK)` 对目录也为真，若不额外排除，探测会报"可用"而启动时报
    `PermissionError`（在 `run_case` 里会变成内部缺陷而不是"沙箱不可用"）——红队实测过这条缝隙。
    """
    check = is_executable if is_executable is not None else _default_is_executable
    for candidate in _nsjail_search_order(policy):
        if candidate and check(candidate) and not os.path.isdir(candidate):
            return candidate
    return None



# --- 账本边界：数值不变量与布尔常量同样 fail-closed -----------------------------------------

#: 数值边界的权威出处（**引用，不复制第二份数字**）：契约逐字段上限 + B2 资源账本。
CONTRACT_POLICY_AUTHORITY = (
    "MAIN/contracts/schemas/evaluation-package.schema.json"
    "#/properties/security_policy（maximum / exclusiveMinimum）"
)
LEDGER_AUTHORITY = (
    "MAIN/docs/architecture/resource-budget.md 第 1–2 节 + MAIN/docs/workstreams/B2.md 第 8 节"
    "（单并发 nsjail 作业 ≤96 MB / 0.45 vCPU、后端 1 vCPU、初始 1 个动态评测并发）"
)
B2_POLICY_AUTHORITY = (
    "B2 自有执行常量（contract 不含该字段）：runner.policy 单点定义，"
    "需在 Linux 验收时按宿主 limits 校准"
)


@dataclass(frozen=True, slots=True)
class PolicyBound:
    """一个数值字段的账本边界（`authority` = 权威出处；`nullable` = 允许 None 表示不限）。"""

    field: str
    minimum: float | None
    maximum: float | None
    exclusive_minimum: bool
    authority: str
    nullable: bool = False


#: **唯一**的数值边界表：`runner.package` 的解析校验与执行期守卫都读这里（不存在第二份数字）。
POLICY_BOUNDS: tuple[PolicyBound, ...] = (
    PolicyBound("max_memory_bytes", MIN_MEMORY_BYTES, MAX_MEMORY_BYTES, False, LEDGER_AUTHORITY),
    PolicyBound("max_cpu_seconds", 0, MAX_CPU_SECONDS, True, LEDGER_AUTHORITY),
    PolicyBound("max_wall_seconds", 0, MAX_WALL_SECONDS, True, LEDGER_AUTHORITY),
    PolicyBound("max_output_bytes", MIN_OUTPUT_BYTES, MAX_OUTPUT_BYTES, False,
                CONTRACT_POLICY_AUTHORITY),
    PolicyBound("max_processes", MIN_PROCESSES, MAX_PROCESSES, False, CONTRACT_POLICY_AUTHORITY),
    PolicyBound("max_files_created", 0, MAX_FILES_CREATED, False, CONTRACT_POLICY_AUTHORITY,
                nullable=True),
    PolicyBound("max_open_files", MIN_OPEN_FILES, MAX_OPEN_FILES, False, B2_POLICY_AUTHORITY),
    PolicyBound("stack_mib", MIN_STACK_MIB, None, False, LEDGER_AUTHORITY),
    PolicyBound("max_cpus", LEDGER_MAX_CPUS_PER_JOB, LEDGER_MAX_CPUS_PER_JOB, False,
                LEDGER_AUTHORITY),
)


@dataclass(frozen=True, slots=True)
class PolicyBoundViolation:
    """一条数值越界。

    `reason` 取值：`above_maximum` / `below_minimum` / `not_a_number` /
    `inconsistent`（关系式不一致，如栈上限超内存预算）。
    """

    field: str
    value: object
    reason: str
    detail: str
    authority: str


def policy_bound_violations(policy: RunnerPolicy) -> tuple[PolicyBoundViolation, ...]:
    """数值账本越界清单（空 = 全部在账本内）。

    **同一函数同时服务两处**：`runner.package` 的 package 解析校验（越界即拒绝整包）与
    执行期 `assert_policy_executable`（越界即拒绝执行）。因此不存在"从 package 走安全、
    直接构造就能绕过"的差异。
    """
    violations: list[PolicyBoundViolation] = []
    for bound in POLICY_BOUNDS:
        value = getattr(policy, bound.field, None)
        if value is None:
            if not bound.nullable:
                violations.append(
                    PolicyBoundViolation(
                        bound.field,
                        value,
                        "not_a_number",
                        f"{bound.field} must be a number, got None ({bound.authority})",
                        bound.authority,
                    )
                )
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            violations.append(
                PolicyBoundViolation(
                    bound.field,
                    value,
                    "not_a_number",
                    f"{bound.field} must be a number, got {type(value).__name__}"
                    f" ({bound.authority})",
                    bound.authority,
                )
            )
            continue
        number = float(value)
        if bound.maximum is not None and number > float(bound.maximum):
            violations.append(
                PolicyBoundViolation(
                    bound.field,
                    value,
                    "above_maximum",
                    f"{bound.field}={value!r} exceeds the ledger maximum {bound.maximum!r}"
                    f" ({bound.authority})",
                    bound.authority,
                )
            )
            continue
        if bound.minimum is not None:
            below = number <= float(bound.minimum) if bound.exclusive_minimum else number < float(
                bound.minimum
            )
            if below:
                comparator = "must be greater than" if bound.exclusive_minimum else "must be at least"
                violations.append(
                    PolicyBoundViolation(
                        bound.field,
                        value,
                        "below_minimum",
                        f"{bound.field}={value!r} {comparator} {bound.minimum!r}"
                        f" ({bound.authority})",
                        bound.authority,
                    )
                )
    violations.extend(_relational_violations(policy))
    return tuple(violations)


def _relational_violations(policy: RunnerPolicy) -> list[PolicyBoundViolation]:
    """关系式不变量：栈上限必须落在该作业的内存预算内（栈占用同属这份预算）。"""
    stack_mib = policy.stack_mib
    memory = policy.max_memory_bytes
    if isinstance(stack_mib, bool) or not isinstance(stack_mib, int):
        return []
    if isinstance(memory, bool) or not isinstance(memory, int):
        return []
    if stack_mib * MIB <= memory:
        return []
    return [
        PolicyBoundViolation(
            "stack_mib",
            stack_mib,
            "inconsistent",
            f"stack_mib={stack_mib} is inconsistent with max_memory_bytes={memory}:"
            f" the stack limit must stay inside the memory budget ({LEDGER_AUTHORITY})",
            LEDGER_AUTHORITY,
        )
    ]


def policy_constraint_violations(policy: RunnerPolicy) -> tuple[str, ...]:
    """布尔常量与开关越界清单（seccomp/cgroup 不得关闭，三个常量不得翻转）。"""
    messages: list[str] = []
    if not policy.use_seccomp:
        messages.append("seccomp cannot be disabled (ADR-0002)")
    if not policy.use_cgroup:
        messages.append("cgroup v2 cannot be disabled (ADR-0002)")
    if policy.network or not policy.read_only_root or policy.allow_root:
        messages.append("network/read_only_root/allow_root are constants and cannot be relaxed")
    return tuple(messages)


def assert_policy_executable(policy: RunnerPolicy) -> None:
    """执行期硬守：布尔常量 / seccomp-cgroup 开关 / **数值账本上限**，任一越界即拒绝执行。

    异常类型选 `SandboxUnavailableError` 的理由：越界策略属"不可执行"（配置/基础设施缺陷），
    不是学生侧失败、也不是安全违规；与布尔常量走**同一条**拒绝路径，编排层据此置 `failed` +
    `infrastructure_error`，绝不放宽后照跑（不降级）。
    """
    reasons = [
        *policy_constraint_violations(policy),
        *(violation.detail for violation in policy_bound_violations(policy)),
    ]
    if reasons:
        raise SandboxUnavailableError("policy refused: " + "; ".join(reasons))


def sandbox_script_path(policy: RunnerPolicy, relative_name: str) -> str:
    """把工作区相对名转成沙箱内脚本路径（`/workspace/<name>`），并做形态校验。"""
    segments = relative_name.split("/")
    if not segments or any(not _SCRIPT_SEGMENT.match(seg) or seg in {".", ".."} for seg in segments):
        raise ValueError(f"invalid sandbox script name: {relative_name!r}")
    return f"{policy.workspace_mount.rstrip('/')}/{relative_name}"


def build_argv(
    policy: RunnerPolicy,
    *,
    config_path: Path,
    sandbox_log_path: Path,
    language: str,
    script: str,
    arguments: Sequence[str] = (),
) -> list[str]:
    """构造 nsjail 启动**固定参数数组**（绝不拼接 shell 字符串、绝不用 `shell=True`）。

    形态固定为
    `[<nsjail>, "--config", <cfg>, "--log", <log>, "--", <interpreter>, <script>, *arguments]`：

    - 解释器**只能**取自策略（`policy.interpreter_for(language)`），调用方无法注入任意可执行文件；
    - `script` 必须是沙箱内工作区路径（`/workspace/...`，无 `..`/反斜杠/NUL）；
    - `arguments` 来自 package 声明，逐元素原样成为 argv 元素——含 shell 元字符也保持字面量；
    - `sandbox_log_path` **必填**：nsjail 自有诊断（含运行期违规）只写这份日志，**不写**学生流。
      这是归因面的**结构性边界**——日志是宿主自有文件、位于工作区之外，被囚进程在只读根下无法写它，
      因此"学生打印 `nsjail: ...` 字样"再也不能伪造归类（替代了此前"`"nsjail" in line` 字符串过滤"
      那种可伪造的启发式）。实测（容器内 `nsjail --log` 探针）：日志里是 nsjail 的诊断行，学生 stderr
      里只有学生自己的字节；nsjail 自身失败（如 execve 失败）也只写日志、学生 stderr 为 0 字节。
      与该日志无关但必须保持的不变量：日志与配置同目录（都在工作区之外），见下面的校验。
    """
    assert_policy_executable(policy)
    interpreter = policy.interpreter_for(language)
    if not interpreter.startswith("/") or "\\" in interpreter:
        raise ValueError(f"interpreter must be an absolute POSIX path: {interpreter!r}")
    prefix = f"{policy.workspace_mount.rstrip('/')}/"
    if not script.startswith(prefix):
        raise ValueError(f"script must live under {policy.workspace_mount}: {script!r}")
    sandbox_script_path(policy, script[len(prefix) :])
    config_dir = Path(config_path).parent
    if Path(sandbox_log_path).parent != config_dir:
        # 工作区是唯一的 rw 挂载；日志若落到工作区里就重新变成学生可写的伪造面。
        raise ValueError(
            "sandbox log must live in the same host directory as the nsjail config"
            f" (outside the writable workspace mount): {sandbox_log_path!r}"
        )
    argv = [
        resolve_nsjail_path(policy) or policy.nsjail_path,
        "--config",
        str(config_path),
        "--log",
        str(sandbox_log_path),
        "--",
        interpreter,
        script,
    ]
    for index, argument in enumerate(arguments):
        if not isinstance(argument, str):
            raise TypeError(f"argument[{index}] must be str, got {type(argument).__name__}")
        if "\x00" in argument:
            raise ValueError(f"argument[{index}] contains NUL")
        argv.append(argument)
    return argv


def create_case_workspace(policy: RunnerPolicy, root: Path) -> Path:
    """按策略的 `workspace_template` 创建**本用例独立**临时工作区（I22：不复用其他用例的工作区）。

    目录权限显式设为 `0o777`：被囚进程在**外层**是非 root（见 `uid_maps`），
    默认 `mkdtemp` 的 `0o700` 会让它连自己的唯一可写工作区都写不进去。
    """
    root.mkdir(parents=True, exist_ok=True)
    created = tempfile.mkdtemp(prefix=policy.workspace_template, dir=str(root))
    path = Path(created)
    try:
        os.chmod(path, 0o777)
    except OSError:  # pragma: no cover - 非 POSIX 平台无此语义，权限位由平台决定
        pass
    return path


# --- nsjail 配置生成（protobuf text；字段名取自 nsjail 官方 config.proto） -------------------


def _mount_block(*entries: str) -> str:
    return "mount {\n" + "\n".join(f"  {entry}" for entry in entries) + "\n}"


def _seccomp_chunks(names: Sequence[str], per_line: int = 6) -> list[str]:
    """按固定条数换行拼接系统调用名。

    kafel 的语法要求：条目之间用逗号分隔，**最后一条之后不得有逗号**
    （验收实测 `unexpected '}', expecting IDENTIFIER or SYSCALL`）。因此只有非末行才带尾逗号。
    """
    chunks: list[str] = []
    for start in range(0, len(names), per_line):
        group = names[start : start + per_line]
        is_last_chunk = start + per_line >= len(names)
        chunks.append("    " + ", ".join(group) + ("" if is_last_chunk else ","))
    return chunks


def seccomp_policy_lines(policy: RunnerPolicy) -> tuple[str, ...]:
    """kafel seccomp 策略的**逐行**形态（每行一条 `seccomp_string`，nsjail 按行拼接）。

    形态依据 Linux 验收实测（真 nsjail 3.4 + kafel）修正，三条语法要点：

    1. `DEFAULT KILL` **不能**写在 `POLICY { … }` 块内（实测 `2:3: syntax error, unexpected DEFAULT`）；
       默认动作写在**文件级** `USE <policy> DEFAULT KILL`；
    2. `ALLOW { … }` 与 `KILL { … }` 两个 action block 之间**必须有逗号**（实测 `unexpected KILL`）；
    3. 最后一个系统调用之后**不得有尾逗号**（见 `_seccomp_chunks`）。

    kafel 不认识的系统调用名先用 `#define NAME <x86_64 编号>` 补齐（`SECCOMP_X86_64_DEFINES`）。
    """
    assert_policy_executable(policy)
    if _current_machine() != policy.seccomp_arch:
        raise SandboxUnavailableError(
            f"policy refused: seccomp policy is calibrated for {policy.seccomp_arch},"
            f" host is {_current_machine()}"
        )
    lines = [f"#define {name} {number}" for name, number in SECCOMP_X86_64_DEFINES]
    lines.append("POLICY b2_default_deny {")
    lines.append("  ALLOW {")
    lines.extend(_seccomp_chunks(SECCOMP_ALLOW))
    lines.append("  },")
    lines.append("  KILL {")
    lines.extend(_seccomp_chunks(SECCOMP_DENY))
    lines.append("  }")
    lines.append("}")
    lines.append("USE b2_default_deny DEFAULT KILL")
    return tuple(lines)


def uid_maps(
    policy: RunnerPolicy, *, caller_uid: int | None = None, caller_gid: int | None = None
) -> tuple[tuple[str, str, str], ...]:
    """返回 (block, inside_id, outside_id) 三元组序列。

    两条的**顺序与取值都不可改**（Linux 验收逐变体实测）：

    1. 第一条决定被囚进程的 uid/gid（`nsjail` 取**第一条** `uidmap` 的 `inside_id`）；它的
       `outside_id` 必须是**外层真实存在的非 root uid**，否则就是"假非 root"（ns 内 euid 非 0、
       全局仍是 root）。调用者非 root 时只能映射自己的 uid；调用者是 root 时可映射任意外层 uid，
       取 `policy.sandbox_uid`（65534 / nobody）——两层都不是 root。
    2. 第二条把**调用者的 uid/gid** 映射进来，使 nsjail 在新 user namespace 里仍是 root，
       否则挂载失败：`Couldn't mount '/workspace'`。它不会让被囚进程变成 root（uid 由第一条决定），
       但 nsjail 会为此打印 `Process will be UID/EUID=0 in the global user namespace` 警告——
       那是针对本条目的，**不能**据此判断"假非 root"（判据是从 ns 外读 `/proc/<pid>/status`）。
    """
    uid = _current_uid() if caller_uid is None else int(caller_uid)
    gid = _current_gid() if caller_gid is None else int(caller_gid)
    jailed_outside = uid if uid != 0 else int(policy.sandbox_uid)
    return (
        ("uidmap", policy.sandbox_uid, str(jailed_outside)),
        ("uidmap", "0", str(uid)),
        ("gidmap", policy.sandbox_gid, str(jailed_outside)),
        ("gidmap", "0", str(gid)),
    )


def render_nsjail_policy_body(
    policy: RunnerPolicy, *, caller_uid: int | None = None, caller_gid: int | None = None
) -> str:
    """渲染**只依赖策略与调用者 uid/gid** 的 nsjail 配置片段（不含工作区相关挂载行）。

    同一 (策略, 调用者 uid/gid) 两次调用 ⇒ **逐字节相同**；工作区相关行由 `render_nsjail_config` 追加。
    调用者 uid/gid 属于**部署事实**（不是策略），故不进 `policy_digest`，但会出现在配置里
    （uidmap 无法凭空指定外层 uid）。
    """
    assert_policy_executable(policy)
    wall_seconds = _ceil_int(policy.max_wall_seconds)
    cpu_seconds = _ceil_int(policy.max_cpu_seconds)
    memory_mib = _ceil_int(policy.max_memory_bytes / MIB)
    output_mib = max(1, _ceil_int(policy.max_output_bytes / MIB))
    cpu_ms_per_sec = min(1000, max(1, round(cpu_seconds / wall_seconds * 1000)))
    lines: list[str] = []
    add = lines.append
    add("# AutoGrader B2 nsjail 配置（由 RunnerPolicy 确定性生成，请勿手改）")
    add(f"# runner_policy_digest: {policy_digest(policy)}")
    add('name: "autograder-b2-case"')
    add('description: "per-case sandbox: no network, read-only root, non-root, cgroup v2 + seccomp"')
    add("mode: ONCE")
    add("log_level: ERROR")
    add('hostname: "sandbox"')
    add(f'cwd: "{policy.workspace_mount}"')
    add("")
    add("# 禁网（ADR-0002 第 2 条）：独立网络命名空间，且不启用 lo —— 作业内不存在任何网络接口")
    add("clone_newnet: true")
    add("iface_no_lo: true")
    add("")
    add("# 其余命名空间隔离（宿主 PID/IPC/UTS/cgroup 视图不可见）")
    add("clone_newuser: true")
    add("clone_newns: true")
    add("clone_newpid: true")
    add("clone_newipc: true")
    add("clone_newuts: true")
    add("clone_newcgroup: true")
    add("")
    add("# 降权：不保留任何 capability；NO_NEW_PRIVS 置位，且显式禁止其被关闭（allow_root=false）")
    add("keep_caps: false")
    add("disable_no_new_privs: false")
    add("")
    add("# 非 root：**两条** uidmap/gidmap，顺序不可颠倒（Linux 验收逐变体实测）")
    add("#   第 1 条 = 被囚进程的 uid/gid（外层也是非 root）；第 2 条 = 让 nsjail 在 ns 内保持 root")
    add("#   以完成 mount（只写第 1 条会 Couldn't mount；只写第 2 条则被囚进程 uid=0）")
    for block, inside, outside in uid_maps(policy, caller_uid=caller_uid, caller_gid=caller_gid):
        add(f"{block} {{")
        add(f'  inside_id: "{inside}"')
        add(f'  outside_id: "{outside}"')
        add("  count: 1")
        add("}")
    add("")
    add("# 墙钟 / CPU / 内存 / 进程数 / 输出 / 文件数限制（rlimit + cgroup v2）")
    add("# time_limit 含兜底余量：墙钟由 B2 计时器强制，nsjail 只兜底（见 NSJAIL_TIME_LIMIT_BACKSTOP）")
    add(f"time_limit: {wall_seconds + NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS}")
    add("# 每个 rlimit 都必须显式写 *_type: VALUE —— proto 默认值对 nproc/stack 是 SOFT，")
    add("# 只写数值**不会生效**（Linux 验收实测：rlimit_nproc_type 缺失时上限形同虚设）")
    add(f"max_cpus: {policy.max_cpus}")
    add(f"rlimit_as: {memory_mib}")
    add("rlimit_as_type: VALUE")
    add("rlimit_core: 0")
    add("rlimit_core_type: VALUE")
    add(f"rlimit_cpu: {cpu_seconds}")
    add("rlimit_cpu_type: VALUE")
    add(f"rlimit_fsize: {output_mib}")
    add("rlimit_fsize_type: VALUE")
    add(f"rlimit_nofile: {policy.max_open_files}")
    add("rlimit_nofile_type: VALUE")
    add(f"rlimit_nproc: {policy.max_processes}")
    add("rlimit_nproc_type: VALUE")
    add(f"rlimit_stack: {policy.stack_mib}")
    add("rlimit_stack_type: VALUE")
    add(f"cgroup_mem_max: {policy.max_memory_bytes}")
    add(f"cgroup_pids_max: {policy.max_processes}")
    add(f"cgroup_cpu_ms_per_sec: {cpu_ms_per_sec}")
    add(f'cgroupv2_mount: "{policy.cgroup_v2_mount}"')
    add("use_cgroupv2: true")
    add("")
    add("# 固定环境：不继承宿主环境（keep_env=false），避免宿主路径与凭据进入沙箱")
    add("keep_env: false")
    for name, value in sandbox_env(policy):
        add(f'envar: "{name}={value}"')
    add("")
    add("# /proc 在独立 PID 命名空间内新挂（只读；未挂载可写 /tmp：工作区是唯一可写路径）")
    add("mount_proc: true")
    add("")
    add("# 只读根（rw: false）")
    add(_mount_block('src: "/"', 'dst: "/"', "is_bind: true", "rw: false"))
    add("")
    add("# seccomp：默认拒绝 + 允许清单；联网/提权/沙箱逃逸类系统调用显式 KILL")
    for seccomp_line in seccomp_policy_lines(policy):
        add(f'seccomp_string: "{seccomp_line}"')
    add("")
    return "\n".join(lines)


def masked_mount_paths(policy: RunnerPolicy, workspace: Path) -> tuple[str, ...]:
    """需要在沙箱内遮蔽的宿主路径（排序去重 ⇒ 确定性）。

    只读根绑定会把**宿主根**整棵带进沙箱，因此两个来源必须遮蔽：

    1. 策略显式声明的 `masked_paths`（部署相关：服务端配置、凭据目录等）；
    2. **本工作区的父目录**——否则同一评测运行的**其他学生工作区**在只读根下可读。

    工作区自身以 `rw: true` 单独挂到 `policy.workspace_mount`（挂载顺序在后），因此遮蔽父目录
    不会遮住工作区本身。根目录、遮蔽解释器所在的父目录一律跳过（否则沙箱无法运行）。
    """
    candidates = {path for path in policy.masked_paths}
    parent = Path(workspace).parent
    parent_posix = parent.as_posix()
    if parent_posix not in {"", "/"}:
        interpreters = {policy.python_interpreter, policy.bash_interpreter, policy.nsjail_path}
        if not any(item == parent_posix or item.startswith(f"{parent_posix}/") for item in interpreters):
            candidates.add(parent_posix)
    return tuple(sorted(candidates))


def render_nsjail_config(
    policy: RunnerPolicy,
    workspace: Path,
    *,
    caller_uid: int | None = None,
    caller_gid: int | None = None,
) -> str:
    """渲染完整 nsjail 配置：策略片段 + 工作区挂载 + 遮蔽挂载。

    配置内容 = f(策略, 工作区路径, 调用者 uid/gid)：**同一组输入 ⇒ 逐字节相同**；
    仅工作区变化时，差异只出现在工作区挂载与遮蔽行（见测试）。
    """
    body = render_nsjail_policy_body(policy, caller_uid=caller_uid, caller_gid=caller_gid)
    lines = [body]
    add = lines.append
    add("# 工作区：唯一可写路径（rw: true），挂载在遮蔽之后以保证生效")
    add(_mount_block(f'src: "{Path(workspace).as_posix()}"', f'dst: "{policy.workspace_mount}"',
                     "is_bind: true", "rw: true"))
    for masked in masked_mount_paths(policy, workspace):
        add("")
        add(f"# 遮蔽宿主路径 {masked}（只读空 tmpfs 覆盖；mandatory=false 以容忍不存在）")
        add(_mount_block(f'dst: "{masked}"', 'fstype: "tmpfs"', "rw: false",
                         "nosuid: true", "nodev: true", "noexec: true", "mandatory: false"))
    add("")
    return "\n".join(lines)


def nsjail_config_digest(policy: RunnerPolicy, workspace: Path) -> str:
    """配置内容摘要（供"配置未被偷改"与可重放性核验）。"""
    return sha256_hex(render_nsjail_config(policy, workspace).encode("utf-8"))


def _ceil_int(value: float) -> int:
    return int(value) if float(value).is_integer() else int(value) + 1


# --- 可用性前置判定 -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SandboxCheck:
    """单项前置检查结果。`detail` 已脱敏（不含宿主绝对路径以外的敏感内容）。"""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SandboxAvailability:
    """可用性判定：任一项不满足即 `available=False`，其 `reason` 可直接进入失败原因（脱敏）。"""

    available: bool
    checks: tuple[SandboxCheck, ...]
    reason: str | None

    def failed(self) -> tuple[SandboxCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)


def _default_path_exists(path: str) -> bool:
    return Path(path).exists()


def _default_read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace").strip()


def _default_is_executable(path: str) -> bool:
    return Path(path).exists() and os.access(path, os.X_OK)


def probe_sandbox(
    policy: RunnerPolicy,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
    path_exists: Callable[[str], bool] | None = None,
    read_text: Callable[[str], str] | None = None,
    is_executable: Callable[[str], bool] | None = None,
) -> SandboxAvailability:
    """探测沙箱可执行性：平台 / CPU 架构 / nsjail 可执行文件 / cgroup v2 / 非特权用户命名空间。

    探测点都是可注入的纯函数，因此判定逻辑在**任何平台**上都可测试；
    默认实现只做 stat/read，**不创建任何进程**（"不可执行时不启动任何子进程"由此保证）。

    `cpu_arch` 不是形式主义：seccomp 的 `#define` 编号是架构相关的，架构不符时过滤器会
    放行/杀掉错误的系统调用（静默破坏隔离），因此必须 fail-closed。
    """
    assert_policy_executable(policy)
    platform_value = platform_name if platform_name is not None else _platform_name()
    machine_value = normalize_machine(machine) if machine is not None else _current_machine()
    exists = path_exists if path_exists is not None else _default_path_exists
    reader = read_text if read_text is not None else _default_read_text
    executable = is_executable if is_executable is not None else _default_is_executable

    checks: list[SandboxCheck] = []
    linux = platform_value.startswith("linux")
    checks.append(
        SandboxCheck(
            "platform",
            linux,
            f"platform={platform_value}"
            + ("" if linux else "（nsjail 仅 Linux 可用；本平台一律判定不可执行，绝不降级）"),
        )
    )
    arch_ok = machine_value == policy.seccomp_arch
    checks.append(
        SandboxCheck(
            "cpu_arch",
            arch_ok,
            f"machine={machine_value} seccomp_arch={policy.seccomp_arch} match={arch_ok}",
        )
    )
    nsjail_resolved = resolve_nsjail_path(policy, is_executable=executable)
    nsjail_ok = linux and nsjail_resolved is not None
    nsjail_detail = (
        f"nsjail={nsjail_resolved} exists+executable=True"
        if nsjail_ok
        else (
            f"nsjail={policy.nsjail_path} exists+executable=False"
            f"（已按候选位置查找：{', '.join(_nsjail_search_order(policy))}；"
            "请把 nsjail 装到其中之一，或按部署事实指定策略的 nsjail_path）"
        )
    )
    checks.append(SandboxCheck("nsjail", nsjail_ok, nsjail_detail))
    controllers_path = f"{policy.cgroup_v2_mount.rstrip('/')}/cgroup.controllers"
    cgroup_ok = False
    cgroup_detail = f"{controllers_path} unreadable"
    if linux and exists(controllers_path):
        try:
            controllers = reader(controllers_path)
        except OSError as error:  # pragma: no cover - 依赖宿主权限，注入式测试已覆盖判定分支
            cgroup_detail = f"{controllers_path} read failed: {type(error).__name__}"
        else:
            cgroup_ok = bool(controllers)
            cgroup_detail = f"{controllers_path} controllers={controllers!r}"
    checks.append(SandboxCheck("cgroup_v2", cgroup_ok, cgroup_detail))
    userns_ok, userns_detail = _probe_user_namespace(linux, exists, reader)
    checks.append(SandboxCheck("user_namespace", userns_ok, userns_detail))

    failed = [check for check in checks if not check.ok]
    reason = None if not failed else "; ".join(f"{check.name}: {check.detail}" for check in failed)
    return SandboxAvailability(available=not failed, checks=tuple(checks), reason=reason)


def assert_sandbox_available(policy: RunnerPolicy, **probe_kwargs: Any) -> SandboxAvailability:
    """不可执行即抛 `SandboxUnavailableError`（**拒绝执行**的唯一合法表达）。"""
    availability = probe_sandbox(policy, **probe_kwargs)
    if not availability.available:
        raise SandboxUnavailableError(f"sandbox unavailable: {availability.reason}")
    return availability


def _probe_user_namespace(
    linux: bool,
    exists: Callable[[str], bool],
    read_text: Callable[[str], str],
) -> tuple[bool, str]:
    if not linux:
        return False, "platform is not linux"
    clone_path = "/proc/sys/kernel/unprivileged_userns_clone"
    apparmor_path = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
    notes: list[str] = []
    if exists(clone_path):
        try:
            value = read_text(clone_path)
        except OSError as error:  # pragma: no cover - 同 cgroup 分支
            return False, f"{clone_path} read failed: {type(error).__name__}"
        notes.append(f"{clone_path}={value}")
        if value != "1":
            return False, "; ".join(notes)
    if exists(apparmor_path):
        try:
            restrict = read_text(apparmor_path)
        except OSError as error:  # pragma: no cover - 同 cgroup 分支
            return False, f"{apparmor_path} read failed: {type(error).__name__}"
        notes.append(f"{apparmor_path}={restrict}")
        if restrict != "0":
            return False, "; ".join(notes)
    if not exists("/proc/self/ns/user"):
        notes.append("/proc/self/ns/user missing")
        return False, "; ".join(notes)
    notes.append("unprivileged user namespaces available")
    return True, "; ".join(notes)


def _platform_name() -> str:
    return sys.platform


def _current_machine() -> str:
    return normalize_machine(_platform_machine())


def _current_uid() -> int:
    """当前进程 uid（非 POSIX 平台没有该概念，取 0；此类平台的配置永远不会被执行）。"""
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else 0


def _current_gid() -> int:
    getgid = getattr(os, "getgid", None)
    return int(getgid()) if getgid is not None else 0


def _platform_machine() -> str:
    import platform as _platform

    return _platform.machine()


def normalize_machine(value: str) -> str:
    """把各平台对同一 CPU 架构的不同叫法归一（Windows 报 `AMD64`，Linux 报 `x86_64`）。"""
    lowered = value.strip().lower()
    aliases = {
        "amd64": SECCOMP_ARCH,
        "x86_64": SECCOMP_ARCH,
        "x64": SECCOMP_ARCH,
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    return aliases.get(lowered, lowered)


__all__ = [
    "B2_POLICY_AUTHORITY",
    "CONTRACT_POLICY_AUTHORITY",
    "DEFAULT_WORKSPACE_MOUNT",
    "LEDGER_AUTHORITY",
    "LEDGER_MAX_CPUS_PER_JOB",
    "LEDGER_MAX_CPU_PER_JOB",
    "LEDGER_MAX_MEMORY_BYTES",
    "MAX_CPU_SECONDS",
    "MAX_FILES_CREATED",
    "MAX_MEMORY_BYTES",
    "MAX_OPEN_FILES",
    "MAX_OUTPUT_BYTES",
    "MAX_PROCESSES",
    "MAX_WALL_SECONDS",
    "MIB",
    "MIN_MEMORY_BYTES",
    "MIN_OPEN_FILES",
    "MIN_OUTPUT_BYTES",
    "MIN_PROCESSES",
    "MIN_STACK_MIB",
    "NSJAIL_CANDIDATE_PATHS",
    "NSJAIL_SANDBOX_GID",
    "NSJAIL_SANDBOX_UID",
    "POLICY_BOUNDS",
    "SANDBOX_ENV_FIXED",
    "SECCOMP_ALLOW",
    "SECCOMP_ARCH",
    "SECCOMP_DENY",
    "SECCOMP_X86_64_DEFINES",
    "PolicyBound",
    "PolicyBoundViolation",
    "RunnerPolicy",
    "SandboxAvailability",
    "SandboxCheck",
    "assert_policy_executable",
    "assert_sandbox_available",
    "build_argv",
    "create_case_workspace",
    "masked_mount_paths",
    "normalize_machine",
    "nsjail_config_digest",
    "policy_bound_violations",
    "policy_constraint_violations",
    "policy_digest",
    "probe_sandbox",
    "render_nsjail_config",
    "render_nsjail_policy_body",
    "resolve_nsjail_path",
    "sandbox_env",
    "sandbox_script_path",
    "seccomp_policy_lines",
    "uid_maps",
]
