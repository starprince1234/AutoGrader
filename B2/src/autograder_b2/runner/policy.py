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
import posixpath
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

#: 作业以 namespace 内的 nobody 身份运行。
NSJAIL_SANDBOX_UID = "65534"
NSJAIL_SANDBOX_GID = "65534"

#: 沙箱 profile（B2 内部执行形态；**不来自** package 的 `security_policy`，只由 package 的
#: `execution.mode` / 用例 `file_checks` 声明在 runner 内部派生）。
#:
#: - `default`：现有普通题行为（jailed uid 65534、keep_caps false、无 lo、基础 seccomp）；
#: - `workspace_prep`：声明了 `file_checks` 的用例使用（jailed uid 0-in-userns + keep_caps，
#:   由 B2 固定准备程序把工作区交还给被囚身份（重建文件 inode）后降权到 65534 再执行学生脚本）；
#: - `ssh_session`：SSH 练习环境（jailed uid 0-in-userns + keep_caps + lo + SSH 专项 seccomp，
#:   由 B2 固定驱动起真实 sshd）。
SANDBOX_PROFILE_DEFAULT = "default"
SANDBOX_PROFILE_WORKSPACE_PREP = "workspace_prep"
SANDBOX_PROFILE_SSH = "ssh_session"
SANDBOX_PROFILES: tuple[str, ...] = (
    SANDBOX_PROFILE_DEFAULT,
    SANDBOX_PROFILE_WORKSPACE_PREP,
    SANDBOX_PROFILE_SSH,
)

#: workspace_prep 准备程序与被囚身份的固定约定（准备程序内部逐字校验这两个值）。
WORKSPACE_PREP_UID = 65534
WORKSPACE_PREP_GID = 65534
WORKSPACE_PREP_SCRIPT_NAME = "__b2_prepare__.py"
PREPARE_FAILURE_EXIT = 4
PREPARE_FAILURE_MARKER = "B2_PREPARE_ERROR"

#: SSH 驱动的固定文件名（写在工作区里；提交侧提供同名文件即拒绝执行）。
SSH_DRIVER_SCRIPT_NAME = "__b2_ssh_driver__.py"
#: 驱动/sshd 的稳定路径（宿主与沙箱内一致：它们来自只读 bind 的系统目录）。
#: 候选列表**只含 SSH profile 实际挂载的目录**（/usr/bin、/usr/sbin、/bin、/sbin）：
#: 探测说可用、沙箱里却不存在，会把"环境可用"变成谎话。
SSH_DAEMON_CANDIDATES: tuple[str, ...] = (
    "/usr/sbin/sshd",
    "/usr/bin/sshd",
)
SSH_CLIENT_CANDIDATES: tuple[str, ...] = (
    "/usr/bin/ssh",
    "/bin/ssh",
)
SSH_KEYGEN_CANDIDATES: tuple[str, ...] = (
    "/usr/bin/ssh-keygen",
    "/bin/ssh-keygen",
)

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
    ("fadvise64", 221),
    ("close_range", 436),
    ("faccessat2", 439),
    ("fanotify_init", 300),
    ("fsconfig", 431),
    ("fsmount", 432),
    ("fsopen", 430),
    ("fspick", 433),
    ("fstat", 5),
    ("io_uring_enter", 426),
    ("io_uring_register", 427),
    ("io_uring_setup", 425),
    ("lstat", 6),
    ("mount_setattr", 442),
    ("move_mount", 429),
    ("open_tree", 428),
    ("openat2", 437),
    ("pidfd_getfd", 438),
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
#: **shell 运行时**：这份清单最初只覆盖 CPython，`bash`/`dash` 会撞上未列出的调用而被 SIGSYS
#: 杀掉（`security_blocked`，线上所有 bash 题——哪怕参考答案是 `true`——都过不了校验）。
#: 因此补入 shell 启动、作业控制与"等子进程"所需的一小组调用；全部是**进程本地、不涉特权与网络**
#: 的调用，默认拒绝与显式拒绝清单都没有改动（`fork` 只用于起 cat/grep 这类子进程）。
SECCOMP_ALLOW: tuple[str, ...] = (
    "accept4",
    "access",
    "alarm",
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
    "fadvise64",
    "faccessat",
    "faccessat2",
    "fchdir",
    "fchmod",
    "fchmodat",
    "fcntl",
    "fdatasync",
    "fork",
    "fstat",
    "fstatfs",
    "fsync",
    "ftruncate",
    "futex",
    "getcwd",
    "getdents",
    "getdents64",
    "getegid",
    "geteuid",
    "getgid",
    "getgroups",
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
    "mlock",
    "mlockall",
    "mmap",
    "mprotect",
    "mremap",
    "msync",
    "munlock",
    "munlockall",
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
    "rt_sigpending",
    "rt_sigprocmask",
    "rt_sigreturn",
    "rt_sigsuspend",
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
    "setpgid",
    "setpriority",
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
    "fanotify_init",
    "fchown",
    "fchownat",
    "finit_module",
    "fsconfig",
    "fsmount",
    "fsopen",
    "fspick",
    "get_kernel_syms",
    "init_module",
    "io_uring_enter",
    "io_uring_register",
    "io_uring_setup",
    "ioperm",
    "iopl",
    "kcmp",
    "kexec_file_load",
    "kexec_load",
    "keyctl",
    "lchown",
    "listen",
    "mount",
    "mount_setattr",
    "move_mount",
    "move_pages",
    "name_to_handle_at",
    "nfsservctl",
    "open_by_handle_at",
    "open_tree",
    "perf_event_open",
    "pidfd_getfd",
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
    #: 沙箱 profile（见 `SANDBOX_PROFILES` 的说明）。它**不来自** package：package 的
    #: `security_policy` 里没有这个字段，派生只发生在 runner 内部（`run_case`/`run_cases`）。
    sandbox_profile: str = SANDBOX_PROFILE_DEFAULT

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

    **唯一例外**：`sandbox_profile` 取缺省值 `default` 时**不进入载荷**。理由是"新增可选字段的
    缺省语义必须让既有摘要保持有效"（设计文档 §1.3/§8 对 B3 `content_digest` 的同一原则）：
    普通题策略的摘要与 nsjail 配置因此逐字节不变；只有显式派生出的非默认 profile（prep/ssh）
    才改变摘要——它们本来就不存在于旧数据里。
    """
    payload: dict[str, Any] = {}
    for field in fields(policy):
        value = getattr(policy, field.name)
        if field.name == "sandbox_profile" and value == SANDBOX_PROFILE_DEFAULT:
            continue
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
    """布尔常量与开关越界清单（seccomp/cgroup 不得关闭，三个常量不得翻转，profile 必须已知）。"""
    messages: list[str] = []
    if not policy.use_seccomp:
        messages.append("seccomp cannot be disabled (ADR-0002)")
    if not policy.use_cgroup:
        messages.append("cgroup v2 cannot be disabled (ADR-0002)")
    if policy.network or not policy.read_only_root or policy.allow_root:
        messages.append("network/read_only_root/allow_root are constants and cannot be relaxed")
    if policy.sandbox_profile not in SANDBOX_PROFILES:
        messages.append(
            f"unknown sandbox_profile {policy.sandbox_profile!r}; expected one of"
            f" {', '.join(SANDBOX_PROFILES)}"
        )
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


#: `workspace_prep` profile 在基础允许清单之外新增的系统调用。
#:
#: 1. 身份切换：`setgroups/setgid/setuid` 把准备程序降权到被囚 uid。文件所有权**不**靠
#:    `chown` 取得（宿主文件系统属于初始 userns，用户命名空间内 `chown` 实测 EPERM）：
#:    准备程序降权后以被囚身份重建普通文件，因此 `chown` 仍留在默认拒绝清单里。
#: 2. xattr 读写：声明 `file_checks` 的答案常用 `vi`/`cp` 等工具写文件；Vim 写文件前会
#:    `getxattr`/`setxattr` 复制原文件的 ACL/属性（`system.posix_acl_access`）。这两个调用
#:    在基础清单里是 DEFAULT KILL，实测会把真实 `vi` 批处理打成 SIGSYS（"Bad system call"）。
#:    它们只作用于工作区内文件（被囚进程唯一可写路径），不扩大宿主面。
#:
#: 降权后 setuid 能力随 capability 清空，学生代码拿不到提权面。
WORKSPACE_PREP_SECCOMP_ALLOW_EXTRA: tuple[str, ...] = (
    "getxattr",
    "setgid",
    "setgroups",
    "setuid",
    "setxattr",
)

#: `ssh_session` profile 在基础允许清单之外新增的系统调用：
#:
#: - 本地网络（`socket/connect/bind/listen/accept/...`）：仅存在于独立网络命名空间内的 lo，
#:   没有任何外部接口（`network=false` 语义不变）；
#: - 身份切换/chroot 家族：驱动以用户命名空间 root 起 sshd、把客户端与会话降权到 subordinate uid；
#: - `setsid`/`accept` 等 sshd 会话管理所需的进程/侦听调用。
#:
#: **不得**把这份清单合进普通题（`SECCOMP_ALLOW`/`SECCOMP_DENY` 常量保持原样）。
SSH_SECCOMP_ALLOW_EXTRA: tuple[str, ...] = (
    "accept",
    "bind",
    "capset",
    "chown",
    "chroot",
    "connect",
    "fchown",
    "fchownat",
    "getpeername",
    "getsockname",
    "getsockopt",
    "lchown",
    "listen",
    "recvfrom",
    "recvmsg",
    "sendmsg",
    "sendto",
    "setgid",
    "setgroups",
    "setregid",
    "setresgid",
    "setresuid",
    "setreuid",
    "setrlimit",
    "setsid",
    "setsockopt",
    "setuid",
    "shutdown",
    "socket",
    "socketpair",
)


def workspace_prep_seccomp_policy_lines(policy: RunnerPolicy) -> tuple[str, ...]:
    """`workspace_prep` 的 kafel 策略：基础允许清单 + 准备程序降权所需的最小增量。"""
    _assert_seccomp_arch(policy)
    allow = _extended_allowlist(WORKSPACE_PREP_SECCOMP_ALLOW_EXTRA)
    deny = tuple(name for name in SECCOMP_DENY if name not in WORKSPACE_PREP_SECCOMP_ALLOW_EXTRA)
    return _seccomp_lines(allow, deny, socket_allowed=False)


def ssh_seccomp_policy_lines(policy: RunnerPolicy) -> tuple[str, ...]:
    """`ssh_session` 的 kafel 策略：基础允许清单 + 本地网络/身份切换增量。"""
    _assert_seccomp_arch(policy)
    allow = _extended_allowlist(SSH_SECCOMP_ALLOW_EXTRA)
    deny = tuple(name for name in SECCOMP_DENY if name not in SSH_SECCOMP_ALLOW_EXTRA)
    return _seccomp_lines(allow, deny, socket_allowed=True)


def _assert_seccomp_arch(policy: RunnerPolicy) -> None:
    if _current_machine() != policy.seccomp_arch:
        raise SandboxUnavailableError(
            f"policy refused: seccomp policy is calibrated for {policy.seccomp_arch},"
            f" host is {_current_machine()}"
        )


def _extended_allowlist(extra: Sequence[str]) -> tuple[str, ...]:
    """基础允许清单 + 增量（增量先排序去重再追加 ⇒ 同输入逐字节确定）。"""
    merged = list(SECCOMP_ALLOW)
    for name in sorted(set(extra)):
        if name not in merged:
            merged.append(name)
    return tuple(merged)


def _seccomp_lines(
    allow: Sequence[str], deny: Sequence[str], *, socket_allowed: bool
) -> tuple[str, ...]:
    """kafel 策略逐行渲染（与 `seccomp_policy_lines` 同一语法形态）。

    `socket_allowed=False` 时保留 `ERRNO(1) { socket }` 行：普通/准备 profile 里 libc/NSS 可能探测
    socket，返回 EPERM 让程序正常处理"服务不可用"而不是被 SIGSYS 杀死。SSH profile 里 socket
    在允许清单内，所以既无 ERRNO 行也不在 KILL 列表。
    """
    lines = [f"#define {name} {number}" for name, number in SECCOMP_X86_64_DEFINES]
    lines.append("POLICY b2_sandbox {")
    lines.append("  ALLOW {")
    lines.extend(_seccomp_chunks(allow))
    lines.append("  },")
    if not socket_allowed:
        lines.append("  ERRNO(1) { socket },")
    lines.append("  KILL {")
    # 与旧实现逐字节一致：socket 已由上面的 ERRNO 行处理，不再进 KILL 列表。
    kill_names = tuple(name for name in deny if name != "socket") if not socket_allowed else tuple(deny)
    lines.extend(_seccomp_chunks(kill_names))
    lines.append("  }")
    lines.append("}")
    lines.append("USE b2_sandbox DEFAULT KILL")
    return tuple(lines)


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
    _assert_seccomp_arch(policy)
    return _seccomp_lines(SECCOMP_ALLOW, SECCOMP_DENY, socket_allowed=False)


def _subordinate_id(kind: str, caller: int) -> int:
    """Resolve the first ID delegated to the invoking service account."""
    import pwd

    try:
        name = pwd.getpwuid(caller).pw_name
        for line in Path(f"/etc/sub{kind}").read_text(encoding="utf-8").splitlines():
            parts = line.split(":")
            if len(parts) == 3 and parts[0] in (name, str(caller)):
                start, count = int(parts[1]), int(parts[2])
                if start > 0 and count > 0:
                    return start
    except (OSError, KeyError, ValueError) as error:
        raise SandboxUnavailableError(f"subordinate {kind} mapping unavailable") from error
    raise SandboxUnavailableError(f"subordinate {kind} mapping unavailable")


def uid_maps(
    policy: RunnerPolicy, *, caller_uid: int | None = None, caller_gid: int | None = None
) -> tuple[tuple[str, str, str], ...]:
    """Map the jail user to delegated IDs and namespace root to the caller."""
    uid = _current_uid() if caller_uid is None else int(caller_uid)
    gid = _current_gid() if caller_gid is None else int(caller_gid)
    jailed_uid = int(policy.sandbox_uid) if uid == 0 else _subordinate_id("uid", uid)
    jailed_gid = int(policy.sandbox_gid) if uid == 0 else _subordinate_id("gid", uid)
    return (
        ("uidmap", policy.sandbox_uid, str(jailed_uid)),
        ("uidmap", "0", str(uid)),
        ("gidmap", policy.sandbox_gid, str(jailed_gid)),
        ("gidmap", "0", str(gid)),
    )


def profile_id_maps(
    policy: RunnerPolicy, *, caller_uid: int | None = None, caller_gid: int | None = None
) -> tuple[tuple[str, str, str], ...]:
    """`workspace_prep` / `ssh_session` 的 uid/gid 映射：首条 uidmap 使 jailed uid = 0-in-userns。

    **硬事实（生产服务器实测，OpenCloudOS 9.6 / nsjail /usr/local/bin）**：

    - nsjail 由 `uidmap` 的**第一条**决定 jailed uid（内核映射写入后 `setresuid(uids[0].inside_id)`），
      因此首条写 `0 -> caller` 即得到"用户命名空间内的 root"（宿主 uid 仍是服务账号，**不是宿主 root**）；
    - **不要**传 `--user 0` / `--group 0`：实测它们会让 newuidmap/newgidmap 失败（EINVAL/EPERM）；
    - `gidmap` **只需** `65534 -> subgid`：分到的 subgid 只有 count=1，再映射 gid 0 既无用途也会失败；
      jailed gid 由 gidmap 第一条决定，正好就是被囚的 subordinate gid；
    - 第二条 uidmap `65534 -> subuid` 让准备程序/驱动把工作区与会话降权到被囚身份。
    """
    uid = _current_uid() if caller_uid is None else int(caller_uid)
    gid = _current_gid() if caller_gid is None else int(caller_gid)
    jailed_uid = int(policy.sandbox_uid) if uid == 0 else _subordinate_id("uid", uid)
    jailed_gid = int(policy.sandbox_gid) if gid == 0 else _subordinate_id("gid", gid)
    return (
        ("uidmap", "0", str(uid)),
        ("uidmap", policy.sandbox_uid, str(jailed_uid)),
        ("gidmap", policy.sandbox_gid, str(jailed_gid)),
    )


def _write_id_map_blocks(
    lines: list[str], maps: Sequence[tuple[str, str, str]], *, use_helper: bool
) -> None:
    """把 `(block, inside, outside)` 三元组写成 nsjail 的 uidmap/gidmap 块。"""
    for block, inside, outside in maps:
        lines.append(f"{block} {{")
        lines.append(f'  inside_id: "{inside}"')
        lines.append(f'  outside_id: "{outside}"')
        lines.append("  count: 1")
        if use_helper:
            lines.append("  use_newidmap: true")
        lines.append("}")


def render_nsjail_policy_body(
    policy: RunnerPolicy,
    *,
    caller_uid: int | None = None,
    caller_gid: int | None = None,
) -> str:
    """渲染只依赖策略与调用者 uid/gid 的 nsjail 配置片段（按 profile 分派）。

    `default` 路径的输出与引入 profile 之前**逐字节相同**（普通题配置与摘要不变）；
    `workspace_prep` 复用同一骨架，只改 keep_caps/uid map 顺序/seccomp；
    `ssh_session` 的挂载依赖运行目录布局，必须走 `render_nsjail_ssh_config`，此处显式拒绝
    （否则会渲染出一份"看起来正常但缺少 SSH 挂载"的配置——静默错误比明确拒绝更糟）。
    """
    assert_policy_executable(policy)
    if policy.sandbox_profile == SANDBOX_PROFILE_DEFAULT:
        return _render_default_policy_body(
            policy, caller_uid=caller_uid, caller_gid=caller_gid
        )
    if policy.sandbox_profile == SANDBOX_PROFILE_WORKSPACE_PREP:
        return _render_workspace_prep_policy_body(
            policy, caller_uid=caller_uid, caller_gid=caller_gid
        )
    if policy.sandbox_profile == SANDBOX_PROFILE_SSH:
        raise SandboxUnavailableError(
            "ssh_session sandbox config requires the run-directory layout:"
            " use render_nsjail_ssh_config instead"
        )
    raise SandboxUnavailableError(f"unknown sandbox_profile {policy.sandbox_profile!r}")


def _render_default_policy_body(
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
    add("# The first mapping is the jail user; namespace root maps to the service user.")
    use_helper = (_current_uid() if caller_uid is None else int(caller_uid)) != 0
    for block, inside, outside in uid_maps(policy, caller_uid=caller_uid, caller_gid=caller_gid):
        add(f"{block} {{")
        add(f'  inside_id: "{inside}"')
        add(f'  outside_id: "{outside}"')
        add("  count: 1")
        if use_helper:
            add("  use_newidmap: true")
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


def _render_workspace_prep_policy_body(
    policy: RunnerPolicy, *, caller_uid: int | None = None, caller_gid: int | None = None
) -> str:
    """`workspace_prep` 的 nsjail 策略片段。

    与 default 的差异只有三处，且都服务于"准备程序 chown 工作区后降权执行学生脚本"：
    jailed uid = 0-in-userns（首条 uidmap）、`keep_caps: true`（chown/setuid/setgroups 需要
    namespace 内的 capability）、seccomp 允许清单加入准备程序所需的最小增量。
    网络/只读根/cgroup/进程树清理与 default 完全一致。
    """
    assert_policy_executable(policy)
    wall_seconds = _ceil_int(policy.max_wall_seconds)
    cpu_seconds = _ceil_int(policy.max_cpu_seconds)
    memory_mib = _ceil_int(policy.max_memory_bytes / MIB)
    output_mib = max(1, _ceil_int(policy.max_output_bytes / MIB))
    cpu_ms_per_sec = min(1000, max(1, round(cpu_seconds / wall_seconds * 1000)))
    lines: list[str] = []
    add = lines.append
    add("# AutoGrader B2 nsjail 配置（workspace_prep profile；由 RunnerPolicy 确定性生成，请勿手改）")
    add(f"# runner_policy_digest: {policy_digest(policy)}")
    add('name: "autograder-b2-workspace-prep"')
    add('description: "workspace prep: uid-0-in-userns, chown workspace, drop to jailed uid, then exec"')
    add("mode: ONCE")
    add("log_level: ERROR")
    add('hostname: "sandbox"')
    add(f'cwd: "{policy.workspace_mount}"')
    add("")
    add("# 禁网（与 default 相同）：独立网络命名空间且不启用 lo")
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
    add("# 保留 namespace 内 capability（仅准备程序需要；它降权后 capability 随 setuid 清空）")
    add("keep_caps: true")
    add("disable_no_new_privs: false")
    add("")
    add("# 首条 uidmap 决定 jailed uid：0-in-userns；第二条把被囚身份映射到 subordinate uid")
    use_helper = (_current_uid() if caller_uid is None else int(caller_uid)) != 0
    _write_id_map_blocks(
        lines,
        profile_id_maps(policy, caller_uid=caller_uid, caller_gid=caller_gid),
        use_helper=use_helper,
    )
    add("")
    add("# 墙钟 / CPU / 内存 / 进程数 / 输出 / 文件数限制（与 default 相同）")
    add(f"time_limit: {wall_seconds + NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS}")
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
    add("# 固定环境（与 default 相同）")
    add("keep_env: false")
    for name, value in sandbox_env(policy):
        add(f'envar: "{name}={value}"')
    add("")
    add("mount_proc: true")
    add("")
    add("# 只读根（工作区由 render_nsjail_config 追加为唯一 rw 挂载）")
    add(_mount_block('src: "/"', 'dst: "/"', "is_bind: true", "rw: false"))
    add("")
    add("# seccomp：基础允许清单 + 准备程序降权所需增量（socket/网络/提权仍拒绝）")
    for seccomp_line in workspace_prep_seccomp_policy_lines(policy):
        add(f'seccomp_string: "{seccomp_line}"')
    add("")
    return "\n".join(lines)


def workspace_prep_config_digest(policy: RunnerPolicy, workspace: Path) -> str:
    """`workspace_prep` 完整 nsjail 配置的规范化摘要（同输入逐字节可复现）。"""
    return sha256_hex(
        render_nsjail_config(policy, workspace).encode("utf-8")
    )


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

    `default` 与 `workspace_prep` 都走这里（body 按 profile 分派，default 输出逐字节不变）；
    `ssh_session` 的根是一个由运行目录构造的最小练习根，必须走 `render_nsjail_ssh_config`，
    这里显式拒绝——渲染一份"看起来正常但缺 SSH 挂载"的配置是静默错误。
    """
    if policy.sandbox_profile == SANDBOX_PROFILE_SSH:
        raise SandboxUnavailableError(
            "ssh_session sandbox config requires the run-directory layout:"
            " use render_nsjail_ssh_config instead"
        )
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


# --- SSH 练习环境：运行目录布局与 nsjail 配置 ------------------------------------------------


@dataclass(frozen=True, slots=True)
class SshSandboxPaths:
    """SSH profile 需要的**运行目录布局**（全部由 B2 在每次运行内创建，之后清理）。

    为什么不是"策略字段"：这些是每次运行的临时路径（tmpfs/临时文件），不是可复用配置；
    它们不进 `policy_digest`，但会出现在 nsjail 配置里（配置摘要因此按运行复现）。
    """

    practice_root: Path        # 最小练习根（同时也是沙箱的 "/"）
    empty_root: Path           # 绑定到 /var/empty 与 /usr/share/empty.sshd 的空目录
    client_root: Path          # 本地 ssh 客户端的 chroot
    passwd_path: Path          # 运行时生成的 NSS 文件（bind 覆盖 /etc/*）
    group_path: Path
    shadow_path: Path


#: 最小练习根里的固定挂载点（相对 practice_root）。
_SSH_PRACTICE_MOUNT_DIRS: tuple[str, ...] = (
    "bin",
    "dev",
    "etc",
    "home/user01",
    "lib",
    "lib64",
    "proc",
    "run",
    "sbin",
    "tmp",
    "usr/bin",
    "usr/lib",
    "usr/lib64",
    "usr/sbin",
    "usr/share/empty.sshd",
    "usr/share/terminfo",
    "var/empty",
    "workspace",
)
#: 本地 ssh 客户端 chroot 里的固定挂载点（相对 client_root）。
_SSH_CLIENT_MOUNT_DIRS: tuple[str, ...] = (
    "bin",
    "dev",
    "etc",
    "lib",
    "lib64",
    "usr/bin",
    "usr/lib",
    "usr/lib64",
)


def create_ssh_sandbox_paths(run_dir: Path) -> SshSandboxPaths:
    """在运行目录里创建 SSH profile 的运行目录布局（幂等；权限按 userns 内 root-owned 设计）。

    关键权限事实（生产实测）：运行目录属于服务账号（autograder），在 uidmap `0 -> caller` 下
    它在用户命名空间内表现为 **root-owned**；sshd 的 `safely_chroot`/privsep 目录校验要求的
    正是"root-owned 且非 group/world 可写"。因此练习根 0755、`empty_root` 0755，而被练习账号
    写入的目录（`home/user01`、`tmp`）显式 0777。所有目录权限**显式**设置，不依赖进程 umask
    （服务 unit 的 `UMask=0077` 会让 `mkdir` 产出 0700，被囚会话 uid 将无法穿越而 `exec` EACCES）。
    """
    practice = Path(run_dir) / "practice"
    empty = Path(run_dir) / "empty"
    client = practice / "client"
    practice.mkdir(parents=True, exist_ok=True)
    empty.mkdir(parents=True, exist_ok=True)
    (empty / "sshd").mkdir(exist_ok=True)
    for relative in _SSH_PRACTICE_MOUNT_DIRS:
        (practice / relative).mkdir(parents=True, exist_ok=True)
    for relative in _SSH_CLIENT_MOUNT_DIRS:
        (client / relative).mkdir(parents=True, exist_ok=True)
    # 客户端 chroot 里需要存在的文件挂载点（bind 的 destination 必须存在）。
    for name in ("null", "random", "urandom", "tty"):
        (client / "dev" / name).touch(exist_ok=True)
    (client / "etc" / "ld.so.cache").touch(exist_ok=True)
    (client / "etc" / "passwd").touch(exist_ok=True)
    (client / "etc" / "group").touch(exist_ok=True)
    paths = SshSandboxPaths(
        practice_root=practice,
        empty_root=empty,
        client_root=client,
        passwd_path=Path(run_dir) / "ssh_passwd",
        group_path=Path(run_dir) / "ssh_group",
        shadow_path=Path(run_dir) / "ssh_shadow",
    )
    # 显式权限：服务 unit 的 UMask=0077 会让 mkdir 产出 0700；而被囚会话 uid（subordinate uid）
    # 必须能穿越练习根与客户端 chroot，否则真实 ssh 客户端 `execve` 返回 EACCES（生产实测）。
    # 挂载点目录一律 0755；被练习账号写入的目录再显式放宽到 0777。
    practice.chmod(0o755)
    empty.chmod(0o755)
    (empty / "sshd").chmod(0o755)
    for base in (practice, client):
        for root, dirnames, _filenames in os.walk(base):
            for dirname in dirnames:
                (Path(root) / dirname).chmod(0o755)
    (practice / "home" / "user01").chmod(0o777)
    (practice / "tmp").chmod(0o777)
    return paths


def _ssh_mount(source: str, destination: str, *, mandatory: bool = True) -> str:
    entries = [f'src: "{source}"', f'dst: "{destination}"', "is_bind: true", "rw: false"]
    if not mandatory:
        entries.append("mandatory: false")
    return _mount_block(*entries)


def render_nsjail_ssh_config(
    policy: RunnerPolicy,
    workspace: Path,
    paths: SshSandboxPaths,
    *,
    caller_uid: int | None = None,
    caller_gid: int | None = None,
) -> str:
    """渲染 `ssh_session` profile 的完整 nsjail 配置（**根 = 最小练习根**）。

    nsjail 的挂载顺序语义（源码 `mnt_legacy.cc`）：先建一个 staging 目录，按配置顺序把 src
    （相对**外层**根解析）挂到 `staging/<dst>`，最后 pivot_root。因此可以：

    1. 先 `src: <practice_root> -> dst: "/"`，让沙箱根就是最小练习根；`/` 的宿主属主是服务账号，
       在 uidmap `0 -> caller` 下表现为 root-owned，sshd 的 `safely_chroot("/")` 因此**能通过**；
    2. 再按 dst 挂入宿主只读系统目录、每次运行的 NSS 覆盖文件、tmpfs 与可写 home/workspace；
    3. sshd 的 privsep 目录 `/var/empty`、`/usr/share/empty.sshd` 挂载**运行目录**里的空目录
       （autograder-owned ⇒ userns 内 root-owned），覆盖宿主上 root-owned 的 65534 显示问题；
       `/run/sshd` 由驱动在 tmpfs 里以 namespace root 创建。
    """
    assert_policy_executable(policy)
    if policy.sandbox_profile != SANDBOX_PROFILE_SSH:
        raise SandboxUnavailableError(
            f"render_nsjail_ssh_config requires sandbox_profile={SANDBOX_PROFILE_SSH!r}"
        )
    wall_seconds = _ceil_int(policy.max_wall_seconds)
    cpu_seconds = _ceil_int(policy.max_cpu_seconds)
    memory_mib = _ceil_int(policy.max_memory_bytes / MIB)
    output_mib = max(1, _ceil_int(policy.max_output_bytes / MIB))
    cpu_ms_per_sec = min(1000, max(1, round(cpu_seconds / wall_seconds * 1000)))
    practice = Path(paths.practice_root).as_posix()
    lines: list[str] = []
    add = lines.append
    add("# AutoGrader B2 nsjail 配置（ssh_session profile；由 RunnerPolicy + 运行目录确定性生成）")
    add(f"# runner_policy_digest: {policy_digest(policy)}")
    add('name: "autograder-b2-ssh"')
    add('description: "ssh practice sandbox: uid-0-in-userns sshd, subordinate session uid, loopback only"')
    add("mode: ONCE")
    add("log_level: ERROR")
    add('hostname: "practice"')
    add(f'cwd: "{policy.workspace_mount}"')
    add("")
    add("# 独立网络命名空间，但启用 lo：没有外部接口（network=false 语义不变）")
    add("clone_newnet: true")
    add("iface_no_lo: false")
    add("")
    add("# 其余命名空间隔离")
    add("clone_newuser: true")
    add("clone_newns: true")
    add("clone_newpid: true")
    add("clone_newipc: true")
    add("clone_newuts: true")
    add("clone_newcgroup: true")
    add("")
    add("# 保留 namespace 内 capability：驱动需要起 sshd / chroot / 降权（宿主 uid 仍是服务账号）")
    add("keep_caps: true")
    add("disable_no_new_privs: false")
    add("")
    add("# 首条 uidmap 决定 jailed uid：0-in-userns；第二条把会话/客户端降权到 subordinate uid")
    use_helper = (_current_uid() if caller_uid is None else int(caller_uid)) != 0
    _write_id_map_blocks(
        lines,
        profile_id_maps(policy, caller_uid=caller_uid, caller_gid=caller_gid),
        use_helper=use_helper,
    )
    add("")
    add("# 墙钟 / CPU / 内存 / 进程数 / 输出限制（与普通题同一份账本；sshd+会话因此有确定上界）")
    add(f"time_limit: {wall_seconds + NSJAIL_TIME_LIMIT_BACKSTOP_SECONDS}")
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
    add("# 固定环境（不继承宿主环境）")
    add("keep_env: false")
    for name, value in sandbox_env(policy):
        add(f'envar: "{name}={value}"')
    add("")
    add("mount_proc: true")
    add("")
    add("# 沙箱根 = 最小练习根（一次运行一份；宿主属主 = 服务账号 ⇒ userns 内 root-owned）")
    add(_ssh_mount(practice, "/"))
    add("")
    add("# 只读系统目录（驱动/sshd/远端 shell 的工具链；全部来自宿主只读 bind）")
    for source, destination in (
        ("/bin", "/bin"),
        ("/lib", "/lib"),
        ("/lib64", "/lib64"),
        ("/sbin", "/sbin"),
        ("/usr/bin", "/usr/bin"),
        ("/usr/sbin", "/usr/sbin"),
        ("/usr/lib", "/usr/lib"),
        ("/usr/lib64", "/usr/lib64"),
        ("/dev", "/dev"),
        ("/etc", "/etc"),
    ):
        add(_ssh_mount(source, destination))
    # terminfo 只影响终端能力的显示，缺了不该让整次评测失败（仍如实记录在配置摘要里）。
    add(_ssh_mount("/usr/share/terminfo", "/usr/share/terminfo", mandatory=False))
    add("")
    add("# 独立 devpts 实例：PTY 节点归**沙箱自己的**用户命名空间所有，sshd 才能把 tty chown")
    add("# 给会话用户（宿主 devpts 归属 init userns，非 root 服务账号没有 chown 权限）。")
    add(
        _mount_block(
            'dst: "/dev/pts"',
            'fstype: "devpts"',
            'options: "newinstance,ptmxmode=0666,mode=0620"',
            "rw: true",
        )
    )
    add("")
    add("# /etc 的 NSS 覆盖：每次运行生成的 passwd/group/shadow（autograder-owned，runtime 删除）")
    for source, destination in (
        (paths.passwd_path.as_posix(), "/etc/passwd"),
        (paths.group_path.as_posix(), "/etc/group"),
        (paths.shadow_path.as_posix(), "/etc/shadow"),
    ):
        add(_ssh_mount(source, destination))
    add("")
    add("# sshd 的 privsep 目录：用运行目录里的 autograder-owned 空目录覆盖宿主 root-owned 路径")
    empty = Path(paths.empty_root).as_posix()
    add(_ssh_mount(empty, "/var/empty", mandatory=False))
    add(_ssh_mount(empty, "/usr/share/empty.sshd", mandatory=False))
    add("")
    add("# 驱动运行时目录（tmpfs）：sshd 配置/主机密钥/日志与本地 PTY 全部在内存里，不落宿主持久位置")
    add(_mount_block('dst: "/run"', 'fstype: "tmpfs"', "rw: true", "nosuid: true", "nodev: true"))
    add(_mount_block('dst: "/tmp"', 'fstype: "tmpfs"', "rw: true", "nosuid: true", "nodev: true"))
    add("")
    add("# 练习账号可写的 home（其余根只读）")
    home = (Path(paths.practice_root) / "home").as_posix()
    add(_mount_block(f'src: "{home}"', 'dst: "/home"', "is_bind: true", "rw: true"))
    add("")
    add("# 学生提交文件（只读）：驱动程序读取学生脚本；远端会话不应写它")
    add(_mount_block(f'src: "{Path(workspace).as_posix()}"', f'dst: "{policy.workspace_mount}"',
                     "is_bind: true", "rw: false"))
    add("")
    add("# 本地 ssh 客户端的最小 chroot（客户端以 subordinate uid 运行在这里）")
    # Destination paths are **sandbox-relative**: after nsjail pivots to the practice root, the
    # client chroot must appear at `/client` inside the jail. Mounting the host-side
    # `paths.client_root` path here would put the directories under a path that does not exist
    # after pivot_root, and the driver's `chroot("/client")` would fail. The driver carries the
    # same literal (`ssh_session.SSH_CLIENT_ROOT`); the Linux acceptance test pins `/client`.
    for source, relative in (
        ("/usr/bin", "usr/bin"),
        ("/usr/lib", "usr/lib"),
        ("/usr/lib64", "usr/lib64"),
        ("/bin", "bin"),
        ("/lib", "lib"),
        ("/lib64", "lib64"),
    ):
        add(_ssh_mount(source, f"/client/{relative}", mandatory=False))
    for device in ("null", "random", "urandom", "tty"):
        add(_ssh_mount(f"/dev/{device}", f"/client/dev/{device}", mandatory=False))
    add(_ssh_mount("/etc/ld.so.cache", "/client/etc/ld.so.cache", mandatory=False))
    add(_ssh_mount(paths.passwd_path.as_posix(), "/client/etc/passwd"))
    add(_ssh_mount(paths.group_path.as_posix(), "/client/etc/group"))
    add("")
    add("# seccomp：基础允许清单 + 仅 SSH profile 的本地网络/身份切换增量（普通题清单不变）")
    for seccomp_line in ssh_seccomp_policy_lines(policy):
        add(f'seccomp_string: "{seccomp_line}"')
    add("")
    return "\n".join(lines)


def ssh_config_digest(
    policy: RunnerPolicy,
    workspace: Path,
    paths: SshSandboxPaths,
    *,
    caller_uid: int | None = None,
    caller_gid: int | None = None,
) -> str:
    """SSH profile 完整 nsjail 配置的规范化摘要（同输入逐字节可复现）。"""
    return sha256_hex(
        render_nsjail_ssh_config(
            policy, workspace, paths, caller_uid=caller_uid, caller_gid=caller_gid
        ).encode("utf-8")
    )


def resolve_ssh_binary(candidates: Sequence[str], *, is_executable: Callable[[str], bool] | None = None) -> str | None:
    """解析实际可用的 ssh 相关二进制绝对路径；都不存在时返回 `None`（调用方判不可用）。"""
    check = is_executable if is_executable is not None else _default_is_executable
    for candidate in candidates:
        if candidate and check(candidate) and not os.path.isdir(candidate):
            return candidate
    return None


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


def _default_is_writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def _unescape_mountinfo_path(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _cgroup_is_hierarchy_root(mount: str, mountinfo: str) -> bool:
    """Distinguish the kernel root cgroup from a bind-mounted delegated subtree."""
    target = posixpath.normpath(mount)
    if not posixpath.isabs(target):
        raise ValueError("cgroup mount must be absolute")
    selected: tuple[str, str] | None = None
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        filesystem = right.split()
        if not separator or len(fields) < 5 or not filesystem or filesystem[0] != "cgroup2":
            continue
        root = _unescape_mountinfo_path(fields[3])
        point = posixpath.normpath(_unescape_mountinfo_path(fields[4]))
        if (target == point or target.startswith(point.rstrip("/") + "/")) and (
            selected is None or len(point) >= len(selected[0])
        ):
            selected = (point, root)
    if selected is None:
        raise ValueError("cgroup v2 mount is absent from mountinfo")
    point, root = selected
    relative = posixpath.relpath(target, point)
    return posixpath.normpath(posixpath.join(root, relative)) == "/"


def _probe_cgroup_v2(
    mount: str, reader: Callable[[str], str], writable: Callable[[str], bool]
) -> tuple[bool, str]:
    controllers_path = posixpath.join(mount, "cgroup.controllers")
    subtree_path = posixpath.join(mount, "cgroup.subtree_control")
    procs_path = posixpath.join(mount, "cgroup.procs")
    try:
        controllers = set(reader(controllers_path).split())
        enabled = set(reader(subtree_path).split())
        processes = reader(procs_path).split()
        required = {"cpu", "memory", "pids"}
        missing = required - controllers
        if missing:
            return False, "cgroup v2 controllers unavailable: " + ", ".join(sorted(missing))
        if not all(writable(path) for path in (mount, subtree_path, procs_path)):
            return False, "cgroup v2 delegation is not writable by the service user"
        hierarchy_root = _cgroup_is_hierarchy_root(mount, reader("/proc/self/mountinfo"))
        if processes and required - enabled and not hierarchy_root:
            return False, "delegated cgroup contains processes while required controllers are disabled"
    except (OSError, ValueError) as error:
        return False, f"cgroup v2 inspection failed: {type(error).__name__}"
    return True, "cgroup v2 controllers and delegation are available"


def probe_sandbox(
    policy: RunnerPolicy,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
    path_exists: Callable[[str], bool] | None = None,
    read_text: Callable[[str], str] | None = None,
    is_executable: Callable[[str], bool] | None = None,
    is_writable: Callable[[str], bool] | None = None,
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
    writable = is_writable if is_writable is not None else _default_is_writable

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
    controllers_path = posixpath.join(policy.cgroup_v2_mount, "cgroup.controllers")
    cgroup_ok, cgroup_detail = False, f"{controllers_path} unreadable"
    if linux and exists(controllers_path):
        cgroup_ok, cgroup_detail = _probe_cgroup_v2(policy.cgroup_v2_mount, reader, writable)
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
    "PREPARE_FAILURE_EXIT",
    "PREPARE_FAILURE_MARKER",
    "SANDBOX_ENV_FIXED",
    "SANDBOX_PROFILES",
    "SANDBOX_PROFILE_DEFAULT",
    "SANDBOX_PROFILE_SSH",
    "SANDBOX_PROFILE_WORKSPACE_PREP",
    "SECCOMP_ALLOW",
    "SECCOMP_ARCH",
    "SECCOMP_DENY",
    "SECCOMP_X86_64_DEFINES",
    "SSH_CLIENT_CANDIDATES",
    "SSH_DAEMON_CANDIDATES",
    "SSH_DRIVER_SCRIPT_NAME",
    "SSH_KEYGEN_CANDIDATES",
    "SSH_SECCOMP_ALLOW_EXTRA",
    "WORKSPACE_PREP_GID",
    "WORKSPACE_PREP_SCRIPT_NAME",
    "WORKSPACE_PREP_UID",
    "PolicyBound",
    "PolicyBoundViolation",
    "RunnerPolicy",
    "SandboxAvailability",
    "SandboxCheck",
    "SshSandboxPaths",
    "assert_policy_executable",
    "assert_sandbox_available",
    "build_argv",
    "create_case_workspace",
    "create_ssh_sandbox_paths",
    "masked_mount_paths",
    "normalize_machine",
    "nsjail_config_digest",
    "policy_bound_violations",
    "policy_constraint_violations",
    "policy_digest",
    "probe_sandbox",
    "profile_id_maps",
    "render_nsjail_config",
    "render_nsjail_policy_body",
    "render_nsjail_ssh_config",
    "resolve_nsjail_path",
    "resolve_ssh_binary",
    "sandbox_env",
    "sandbox_script_path",
    "seccomp_policy_lines",
    "ssh_config_digest",
    "ssh_seccomp_policy_lines",
    "uid_maps",
    "workspace_prep_config_digest",
    "workspace_prep_seccomp_policy_lines",
]
