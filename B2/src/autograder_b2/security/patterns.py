"""静态高危模式规则集（`RQ-B-2-70`）：纯文本 / `ast` 层面识别，**绝不执行待测代码**。

每条规则给出：`rule_id` + `category` + `severity` + 可读中文说明；匹配逻辑按三个**匹配面**挂载：

1. `text_patterns` —— 代码文本面：bash 源码剥掉注释；Python 源码用 `tokenize` 把注释与字符串
   字面量替换为空白（保留行结构）。因此**注释里写危险命令不会误报**。
2. `literal_patterns` —— Python 字符串**字面量**面：`os.system("rm -rf /")` 这类"载荷藏在字符串里"
   的样本靠这一面命中。本面只匹配**载荷形态**（路径、命令串），不匹配**词形态**（如 `sudo`/`eval`），
   且模块/类/函数的**文档字符串**视为文档而非载荷——教学示例不误报（见 `_string_literals`）。
3. `ast_probe` —— 结构面：只有结构能表达的事实（调用目标、循环形态、分配规模、组合行为）才用探针。

安全边界（I9）：本模块只用 `ast.parse` 与 `tokenize`（二者都不执行代码），不 import、不
`eval`/`exec`/`compile` 待测源码，不做文件 IO、不发网络。分类取值只取 `SecurityCategory` 契约枚举。

规则集自身的问题（重复 ID、悬空探针、类别/严重度越界、契约类别无规则覆盖）由
`rule_table_problems()` 报告，测试断言其为空——本模块**不**在 import 期抛错（import 期副作用越少越好）。
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from re import Pattern
from typing import get_args

from autograder_b2.domain.envelope import is_allowed_relative_path
from autograder_b2.domain.security import (
    SecurityCategory,
    SecurityFinding,
    SecuritySeverity,
)


class CheckUnavailableError(RuntimeError):
    """检查器无法完成 ⇒ 该检查项 `not_checked`（I14/I16：不得折叠为"通过"）。

    `reason` 必须是**脱敏**原因：不含源码文本、不含内部绝对路径。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: 结构探针：输入已解析的模块 AST，产出命中行号（只读树，不执行任何代码）。
AstProbe = Callable[[ast.Module], Iterable[int]]


@dataclass(frozen=True, slots=True)
class Rule:
    """一条静态高危规则：ID + 契约类别 + 严重度 + 中文说明 + 三个匹配面。"""

    rule_id: str
    category: SecurityCategory
    severity: SecuritySeverity
    description: str
    text_patterns: tuple[Pattern[str], ...] = ()
    literal_patterns: tuple[Pattern[str], ...] = ()
    ast_probe: str | None = None
    languages: tuple[str, ...] = ("python", "bash")


def _rx(*patterns: str) -> tuple[Pattern[str], ...]:
    """编译正则（多行模式：`^`/`$` 按行锚定）。"""
    return tuple(re.compile(pattern, re.MULTILINE) for pattern in patterns)


# --------------------------------------------------------------------------------------
# 模式常量（正则集中在此处，规则表只引用）
# --------------------------------------------------------------------------------------

#: 目标为根/系统目录的递归强制删除（`rm -rf /` 结构，含 `--no-preserve-root`、引号包裹）。
_DESTRUCTIVE_RM = (
    r"\brm\s+(?:-[A-Za-z]+\s+|--[A-Za-z-]+(?:=\S+)?\s+)*"
    r"[\"']?(?:/|/\*|\*|~|\$HOME|\$\{HOME\})[\"']?(?=\s|$|[;&|)])"
    r"|\brm\s+(?:-[A-Za-z]+\s+|--[A-Za-z-]+(?:=\S+)?\s+)*"
    r"[\"']?/(?:etc|usr|var|bin|sbin|lib|lib64|boot|dev|proc|sys|opt|srv|root|home)"
    r"(?=/|\s|$)"
)

#: 直接破坏块设备 / 格式化文件系统。
_DISK_WIPE = (
    r"\bmkfs(?:\.[a-z0-9]+)?\b"
    r"|\bwipefs\b"
    r"|\bdd\b[^\n]*\bof\s*=\s*[\"']?/dev/(?:sd|nvme|hd|vd|mmcblk)"
    r"|\bshred\b[^\n]*/dev/(?:sd|nvme|hd|vd)"
    r"|\bparted\b[^\n]*\bmklabel\b"
    r"|>\s*[\"']?/dev/(?:sd|nvme|hd|vd)[a-z0-9]*"
)

#: 反弹 shell（回连通道 + 交互 shell）。
_REVERSE_SHELL = (
    r"/dev/(?:tcp|udp)/"
    r"|\b(?:nc|ncat|netcat)\b[^\n]*\s-(?:e|c)\s"
    r"|\bsocat\b[^\n]*\bexec:"
    r"|\b(?:bash|sh|zsh)\s+-i\b[^\n]*(?:>&|0<&|/dev/tcp)"
    r"|\bmkfifo\b[^\n]*\bnc\b"
)

#: 管道下载即执行（`curl … | bash`、`base64 -d | sh`、`sh -c "$(curl …)"`）。
_PIPE_DOWNLOAD_EXEC = (
    r"\b(?:curl|wget|aria2c|fetch)\b[^\n|]*\|\s*(?:sudo\s+)?(?:bash|sh|zsh|ksh|dash)\b"
    r"|\b(?:curl|wget)\b[^\n|]*\|\s*(?:python3?|perl|ruby|node|php|awk)\b"
    r"|\b(?:bash|sh|zsh|python3?)\b\s*(?:-c\s*)?[\"']?\$\(\s*(?:curl|wget)\b"
    r"|\b(?:eval|source|\.)\s[^\n]*\$\(\s*(?:curl|wget)\b"
    r"|\bbase64\s+(?:-d|--decode|-D)\b[^\n|]*\|\s*(?:bash|sh|zsh)\b"
)

#: 敏感路径（凭据、影子口令、宿主账号、数据库数据目录）。
_SENSITIVE_PATH = (
    r"/etc/(?:shadow|gshadow|sudoers)\b"
    r"|/etc/ssh/ssh_host_[A-Za-z0-9._-]*_key\b"
    r"|/root(?=[/\s\"']|$)"
    r"|/proc/(?:self|\d+)/(?:environ|cmdline|mem)\b"
    r"|/var/lib/postgresql\b"
    r"|\.ssh/id_(?:rsa|dsa|ecdsa|ed25519)\b"
    r"|\.aws/credentials\b"
    r"|\.pgpass\b"
)

#: 系统账号/组文件（世界可读，但属越界探测信号；仅 medium）。
_SYSTEM_ACCOUNT_FILE = r"/etc/passwd\b|/etc/group\b"

#: 提权尝试（提权命令、setuid 位、降权/改权 API、内核命名空间工具）。
#: 只挂**文本面**：`sudo` 这类词出现在字符串里通常是文档而非载荷。
_PRIVILEGE_ESCALATION = (
    r"\bsudo\b|\bdoas\b|\bpkexec\b"
    r"|\bsu\s+-"
    r"|\bsetcap\b"
    r"|\bchmod\s+(?:[ugoa]*\+s|4[0-7]{3})\b"
    r"|\bchown\s+root\b"
    r"|\b(?:os\.)?(?:setuid|seteuid|setreuid|setresuid|setgid|setegid|setresgid|setgroups)\s*\("
    r"|\bctypes\b[^\n]*\bptrace\b"
    r"|\bnsenter\b|\bunshare\b"
)

#: 以 shell **字符串**执行外部命令（违反 ADR-0002 的固定参数数组纪律）。
_SHELL_STRING_EXEC = (
    r"\bos\.(?:system|popen)\s*\("
    r"|\bshell\s*=\s*True\b"
    r"|\b(?:subprocess|commands)\.get(?:status)?output\s*\("
)

#: 启动子进程 / 替换进程映像（受沙箱进程数上限约束；属信号而非违规）。
_PROCESS_SPAWN = (
    r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\s*\("
    r"|\bos\.(?:execv|execve|execvp|execvpe|spawnv|spawnl|spawnve|posix_spawn)\s*\("
    r"|\bmultiprocessing\.Process\s*\(|\bpty\.spawn\s*\("
)

#: 动态执行代码。
_DYNAMIC_EXEC = r"\b(?:eval|exec|compile)\s*\("

#: Python 侧网络调用（导入不算访问，必须出现调用）。
_NETWORK_CALL_PY = (
    r"\b(?:socket\.(?:socket|create_connection|create_server|getaddrinfo|gethostbyname)"
    r"|urllib\.request\.urlopen|urlopen"
    r"|requests\.(?:get|post|put|patch|delete|head|options|request|Session)"
    r"|httpx\.(?:get|post|put|patch|delete|Client|AsyncClient)"
    r"|http\.client\.(?:HTTPConnection|HTTPSConnection)"
    r"|ftplib\.FTP|smtplib\.SMTP|telnetlib\.Telnet"
    r"|paramiko\.(?:SSHClient|Transport)|aiohttp\.ClientSession|webbrowser\.open)"
    r"\s*\("
)

#: bash 侧网络客户端。
_NETWORK_CLIENT_BASH = (
    r"\b(?:curl|wget|nc|ncat|netcat|telnet|ssh|scp|sftp|rsync|ping|dig|nslookup|socat|traceroute)\s"
)

#: 访问宿主容器守护进程套接字（沙箱逃逸面）。
_DOCKER_SOCKET = r"/var/run/docker\.sock\b|docker\.sock\b"

#: 解压归档（未声明大小/层数上限）。
_ARCHIVE_EXTRACT = (
    r"\bextractall\s*\(|\bshutil\.unpack_archive\s*\(|\btarfile\.open\s*\("
    r"|\bzipfile\.ZipFile\s*\("
    r"|\bunzip\b|\btar\s+(?:-[A-Za-z]*x|xf|x[A-Za-z]*)\b|\b7z\s+x\b|\bcpio\s+-id\b"
)

#: 写入只读根路径（`> /etc/…`、`tee /usr/…`）。
_READONLY_WRITE = (
    r"(?:>>?|-o\s|--output[= ]|\btee\s+(?:-a\s+)?)\s*[\"']?/(?:etc|usr|bin|sbin|lib|boot)"
    r"|(?:>>?)\s*[\"']?/proc/sys"
    r"|\bch(?:mod|own)\s[^\n]*\s/(?:etc|usr|bin|boot)\b"
)

#: 经典 fork bomb：自我管道的函数定义形态（`:()` + 管道 + 后台化的三段结构）。
#: 这里**不写完整字面量**：主机实时防护会按内容删除携带完整活体样本的文件（见测试模块的样本约定）。
#: 只认"函数自我管道并后台化"这一形态；单行 `while true; do x &` 不在此列（正常守护循环会误报）。
_FORK_BOMB = (
    r":\s*\(\s*\)\s*\{[^}]*:[^}]*\|[^}]*:[^}]*&[^}]*\}"
    r"|\b([A-Za-z_]\w*)\s*\(\s*\)\s*\{[^}]*\b\1\b[^}]*\|[^}]*\b\1\b[^}]*&[^}]*\}"
)

#: 根/系统路径（用于"删除类调用目标"与"写只读根"的载荷判定）。
_ROOT_PATH = (
    r"^[\"']?(?:/|~|\$HOME|\$\{HOME\})[\"']?(?:/\*?)?$"
    r"|^[\"']?/(?:etc|usr|var|bin|sbin|lib|lib64|boot|dev|proc|sys|opt|srv|root|home)(?:/|$)"
    r"|^[\"']?(?:~|\$HOME|\$\{HOME\})/"
)

_ROOT_PATH_RE = re.compile(_ROOT_PATH)
_WRITE_MODE_RE = re.compile(r"[wax+]")
#: "像模式串"的短字面量（`w`/`wb`/`r+`/`wt`）——先认出模式串，再看是否含写标志。
_MODE_LIKE_RE = re.compile(r"[rwaxbt+]{1,3}")
_DESTRUCTIVE_RM_RE = re.compile(_DESTRUCTIVE_RM, re.MULTILINE)
_DISK_WIPE_RE = re.compile(_DISK_WIPE, re.MULTILINE)

#: 资源耗尽阈值：≥1e8 的整数常量/分配规模视为耗尽样本（正常作业不会出现该规模）。
HUGE_INT_THRESHOLD = 100_000_000


# --------------------------------------------------------------------------------------
# 结构探针（AST 面）
# --------------------------------------------------------------------------------------

_SHELL_TARGETS = frozenset(
    {
        "system",
        "popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "getoutput",
        "getstatusoutput",
        "spawnl",
        "spawnv",
        "spawnve",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execfile",
    }
)
_REMOVAL_TARGETS = frozenset({"rmtree", "remove", "removedirs", "unlink", "rmdir"})
_FORK_TARGETS = frozenset({"fork", "forkpty", "Process", "clone"})
_EXEC_TARGETS = frozenset({"eval", "exec", "compile", "execfile", "FunctionType"})
_ALLOC_TARGETS = frozenset(
    {"bytearray", "bytes", "list", "range", "str", "array", "zeros", "ones", "empty", "repeat"}
)
_WRITE_API_TARGETS = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "makedirs",
        "touch",
        "chmod",
        "chown",
        "rename",
        "replace",
        "symlink",
        "link",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
    }
)
_NETWORK_MODULES = frozenset(
    {
        "socket",
        "requests",
        "httpx",
        "urllib",
        "http",
        "aiohttp",
        "ftplib",
        "smtplib",
        "telnetlib",
        "paramiko",
    }
)
_NETWORK_STRICT_TARGETS = frozenset(
    {
        "urlopen",
        "create_connection",
        "create_server",
        "getaddrinfo",
        "gethostbyname",
        "HTTPConnection",
        "HTTPSConnection",
        "FTP",
        "SMTP",
        "Telnet",
        "SSHClient",
        "ClientSession",
    }
)
_DECODE_MODULES = frozenset({"base64", "binascii", "codecs", "zlib", "gzip", "bz2", "lzma", "marshal"})
_DECODE_TARGETS = frozenset(
    {
        "b64decode",
        "urlsafe_b64decode",
        "decodebytes",
        "b32decode",
        "b16decode",
        "a85decode",
        "b85decode",
        "fromhex",
        "unhexlify",
        "a2b_base64",
    }
)
_EXIT_TARGETS = frozenset({"exit", "_exit", "quit", "breakpoint"})


def _call_dotted(func: ast.expr) -> str:
    """调用点的点号名字（`subprocess.run` / `os.system`；解析不出时返回空串）。"""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_dotted(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _target_hit(dotted: str, names: frozenset[str]) -> bool:
    """名字命中：全名或**末段**命中（容忍别名与局部变量，如 `import subprocess as sp`）。"""
    if not dotted:
        return False
    if dotted in names:
        return True
    return dotted.rsplit(".", 1)[-1] in names


def _literal_strings(args: Iterable[ast.expr]) -> tuple[str, ...]:
    """收集子树里的字符串字面量（含 f-string 的常量片段）。"""
    values: list[str] = []
    for arg in args:
        for node in ast.walk(arg):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.append(node.value)
    return tuple(values)


def _literal_int_value(node: ast.expr) -> int | None:
    """在不执行代码的前提下求小规模整数常量表达式的值（字面量、取负、有界幂）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _literal_int_value(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        base = _literal_int_value(node.left)
        exponent = _literal_int_value(node.right)
        if base is not None and exponent is not None and 0 <= exponent <= 64 and abs(base) <= 10**6:
            return base**exponent
    return None


def _is_huge(node: ast.expr) -> bool:
    value = _literal_int_value(node)
    return value is not None and abs(value) >= HUGE_INT_THRESHOLD


def _looks_like_network(dotted: str) -> bool:
    """是否像网络调用：根名是网络模块，或末段是无歧义的网络 API。"""
    if not dotted:
        return False
    if _target_hit(dotted, _NETWORK_STRICT_TARGETS):
        return True
    return dotted.split(".", 1)[0] in _NETWORK_MODULES


def _looks_like_decode(node: ast.Call, dotted: str) -> bool:
    """是否像解码/反序列化（`decode("rot13")` 这类带编码参数的解码也算）。"""
    if dotted.split(".", 1)[0] in _DECODE_MODULES:
        return True
    if _target_hit(dotted, _DECODE_TARGETS):
        return True
    if _target_hit(dotted, frozenset({"decode", "decompress"})):
        return any(value in {"rot13", "base64", "hex", "zip"} for value in _literal_strings(node.args))
    return False


def _is_extraction_call(dotted: str) -> bool:
    if _target_hit(dotted, frozenset({"extractall", "extract", "unpack_archive"})):
        return True
    return dotted in {"tarfile.open", "zipfile.ZipFile"}


def _probe_destructive_call(tree: ast.Module) -> Iterable[int]:
    """命令串（含 `["rm", "-rf", "/"]` 数组形式）或删除类调用指向根/系统路径。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _call_dotted(node.func)
        joined = " ".join(_literal_strings(node.args))
        if (
            joined
            and _target_hit(dotted, _SHELL_TARGETS)
            and (_DESTRUCTIVE_RM_RE.search(joined) or _DISK_WIPE_RE.search(joined))
        ):
            hits.append(node.lineno)
            continue
        if _target_hit(dotted, _REMOVAL_TARGETS) and any(
            _ROOT_PATH_RE.match(value) for value in _literal_strings(node.args)
        ):
            hits.append(node.lineno)
    return hits


def _probe_readonly_write(tree: ast.Module) -> Iterable[int]:
    """写只读根路径：`open(根路径, "w")` 与写类 API 携带根路径字面量。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _call_dotted(node.func)
        literals = _literal_strings(node.args)
        if not any(_ROOT_PATH_RE.match(value) for value in literals):
            continue
        if _target_hit(dotted, frozenset({"open", "io.open"})):
            modes = [value for value in literals if _MODE_LIKE_RE.fullmatch(value)]
            if any(_WRITE_MODE_RE.search(mode) for mode in modes):
                hits.append(node.lineno)
        elif _target_hit(dotted, _WRITE_API_TARGETS):
            hits.append(node.lineno)
    return hits


def _probe_fork_bomb(tree: ast.Module) -> Iterable[int]:
    """循环体内 fork / 起进程 ⇒ 无界进程增长。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.While)):
            continue
        targets = {_call_dotted(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
        if any(_target_hit(target, _FORK_TARGETS) for target in targets if target):
            hits.append(node.lineno)
    return hits


def _probe_reverse_shell(tree: ast.Module) -> Iterable[int]:
    """组合信号：建连 + `dup2` 重定向 + 起 shell ⇒ 反弹 shell。"""
    calls = [call for call in ast.walk(tree) if isinstance(call, ast.Call)]
    sockets = [call.lineno for call in calls if _target_hit(_call_dotted(call.func), frozenset({"socket"}))]
    if not sockets:
        return ()
    dup2 = any(_target_hit(_call_dotted(call.func), frozenset({"dup2"})) for call in calls)
    spawn = any(
        _target_hit(_call_dotted(call.func), _SHELL_TARGETS | frozenset({"spawn", "system"}))
        for call in calls
    )
    return (min(sockets),) if dup2 and spawn else ()


def _probe_download_exec(tree: ast.Module) -> Iterable[int]:
    """组合信号：下载调用的结果直接进 `eval`/`exec`/`compile`。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _target_hit(_call_dotted(node.func), _EXEC_TARGETS):
            continue
        for arg in node.args:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Call) and _looks_like_network(_call_dotted(inner.func)):
                    hits.append(node.lineno)
                    break
    return hits


def _probe_obfuscated_payload(tree: ast.Module) -> Iterable[int]:
    """组合信号：解码/反序列化的结果直接进 `eval`/`exec`/`compile`。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _target_hit(_call_dotted(node.func), _EXEC_TARGETS):
            continue
        for arg in node.args:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Call) and _looks_like_decode(inner, _call_dotted(inner.func)):
                    hits.append(node.lineno)
                    break
    return hits


def _probe_resource_exhaustion(tree: ast.Module) -> Iterable[int]:
    """超大分配规模（`[0] * 10**9`、`bytearray(10**12)`、`range(10**9)`）。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        huge_multiply = (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mult)
            and (_is_huge(node.left) or _is_huge(node.right))
        )
        huge_allocation = (
            isinstance(node, ast.Call)
            and _target_hit(_call_dotted(node.func), _ALLOC_TARGETS)
            and any(_is_huge(arg) for arg in node.args)
        )
        if huge_multiply or huge_allocation:
            hits.append(node.lineno)
    return hits


def _is_always_true(test: ast.expr) -> bool:
    """`while True:` / `while 1:` 这类恒真条件（不把非空字符串条件算进来）。"""
    if not isinstance(test, ast.Constant):
        return False
    if test.value is True:
        return True
    return isinstance(test.value, int) and not isinstance(test.value, bool) and test.value != 0


def _loop_terminates(node: ast.While) -> bool:
    """循环自身（不含嵌套循环/函数体）内是否有 break/return/raise/退出调用。"""
    pending = list(node.body)
    while pending:
        stmt = pending.pop()
        if isinstance(stmt, (ast.Break, ast.Return, ast.Raise)):
            return True
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and _target_hit(_call_dotted(stmt.value.func), _EXIT_TARGETS)
        ):
            return True
        if isinstance(stmt, (ast.If, ast.Try, ast.With)):
            for field in ("body", "orelse", "finalbody"):
                pending.extend(getattr(stmt, field, []) or [])
    return False


def _probe_unbounded_loop(tree: ast.Module) -> Iterable[int]:
    """`while True:` 且循环自身无出口 ⇒ 无界循环（沙箱墙钟兜底）。"""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.While) and _is_always_true(node.test) and not _loop_terminates(node)
    ]


def _probe_archive_bomb(tree: ast.Module) -> Iterable[int]:
    """循环内反复解压，或解压结果再解压 ⇒ 压缩炸弹形态。"""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            calls = (child for child in ast.walk(node) if isinstance(child, ast.Call))
            if any(_is_extraction_call(_call_dotted(call.func)) for call in calls):
                hits.append(node.lineno)
        elif isinstance(node, ast.Call) and _is_extraction_call(_call_dotted(node.func)):
            nested = (
                inner
                for arg in node.args
                for inner in ast.walk(arg)
                if isinstance(inner, ast.Call) and _is_extraction_call(_call_dotted(inner.func))
            )
            if any(True for _ in nested):
                hits.append(node.lineno)
    return hits


#: 探针注册表：`Rule.ast_probe` 只能引用这里的名字（悬空引用由 `rule_table_problems()` 报出）。
AST_PROBES: Mapping[str, AstProbe] = {
    "destructive_call": _probe_destructive_call,
    "readonly_write": _probe_readonly_write,
    "fork_bomb": _probe_fork_bomb,
    "reverse_shell": _probe_reverse_shell,
    "download_exec": _probe_download_exec,
    "obfuscated_payload": _probe_obfuscated_payload,
    "resource_exhaustion": _probe_resource_exhaustion,
    "unbounded_loop": _probe_unbounded_loop,
    "archive_bomb": _probe_archive_bomb,
}


# --------------------------------------------------------------------------------------
# 规则表
# --------------------------------------------------------------------------------------

#: 载荷形态（路径/命令串）：文本面与字面量面都匹配。
_PAYLOAD_TEXT = _rx(_DESTRUCTIVE_RM)
_PAYLOAD_LITERAL = _rx(_DESTRUCTIVE_RM)
_WIPE_TEXT = _rx(_DISK_WIPE)
_WIPE_LITERAL = _rx(_DISK_WIPE)
_REVERSE_TEXT = _rx(_REVERSE_SHELL)
_REVERSE_LITERAL = _rx(_REVERSE_SHELL)
_PIPE_TEXT = _rx(_PIPE_DOWNLOAD_EXEC)
_PIPE_LITERAL = _rx(_PIPE_DOWNLOAD_EXEC)
_SENSITIVE_TEXT = _rx(_SENSITIVE_PATH)
_SENSITIVE_LITERAL = _rx(_SENSITIVE_PATH)
_ACCOUNT_TEXT = _rx(_SYSTEM_ACCOUNT_FILE)
_ACCOUNT_LITERAL = _rx(_SYSTEM_ACCOUNT_FILE)
_DOCKER_TEXT = _rx(_DOCKER_SOCKET)
_DOCKER_LITERAL = _rx(_DOCKER_SOCKET)

RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="SEC-DESTRUCTIVE-CMD",
        category="destructive_command",
        severity="high",
        description="破坏性命令：递归强制删除根目录或系统目录",
        text_patterns=_PAYLOAD_TEXT,
        literal_patterns=_PAYLOAD_LITERAL,
        ast_probe="destructive_call",
    ),
    Rule(
        rule_id="SEC-DISK-WIPE",
        category="destructive_command",
        severity="high",
        description="破坏性命令：格式化文件系统或直接改写块设备",
        text_patterns=_WIPE_TEXT,
        literal_patterns=_WIPE_LITERAL,
    ),
    Rule(
        rule_id="SEC-REVERSE-SHELL",
        category="network_attempt",
        severity="high",
        description="反弹 shell：建立回连通道并把交互 shell 交给对端",
        text_patterns=_REVERSE_TEXT,
        literal_patterns=_REVERSE_LITERAL,
        ast_probe="reverse_shell",
    ),
    Rule(
        rule_id="SEC-PIPE-DOWNLOAD-EXEC",
        category="network_attempt",
        severity="high",
        description="管道下载即执行：把远端内容直接交给解释器",
        text_patterns=_PIPE_TEXT,
        literal_patterns=_PIPE_LITERAL,
    ),
    Rule(
        rule_id="SEC-DOWNLOAD-EXEC",
        category="network_attempt",
        severity="high",
        description="下载内容直接动态执行：网络响应进 eval/exec/compile",
        ast_probe="download_exec",
    ),
    Rule(
        rule_id="SEC-SENSITIVE-PATH-READ",
        category="sensitive_path_read",
        severity="high",
        description="读取敏感路径：凭据、影子口令或数据库数据目录",
        text_patterns=_SENSITIVE_TEXT,
        literal_patterns=_SENSITIVE_LITERAL,
    ),
    Rule(
        rule_id="SEC-SYSTEM-ACCOUNT-READ",
        category="sensitive_path_read",
        severity="medium",
        description="读取系统账号/组文件（越界探测信号）",
        text_patterns=_ACCOUNT_TEXT,
        literal_patterns=_ACCOUNT_LITERAL,
    ),
    Rule(
        rule_id="SEC-FORK-BOMB",
        category="fork_bomb",
        severity="high",
        description="无限 fork：自我管道的函数或循环内无界起进程",
        text_patterns=_rx(_FORK_BOMB),
        ast_probe="fork_bomb",
    ),
    Rule(
        rule_id="SEC-PRIVILEGE-ESCALATION",
        category="privilege_escalation",
        severity="high",
        description="提权尝试：提权命令、setuid 位或降权/改权 API",
        text_patterns=_rx(_PRIVILEGE_ESCALATION),
    ),
    Rule(
        rule_id="SEC-SHELL-STRING-EXEC",
        category="sandbox_violation",
        severity="medium",
        description="以 shell 字符串执行外部命令（沙箱要求固定参数数组）",
        text_patterns=_rx(_SHELL_STRING_EXEC),
        languages=("python",),
    ),
    Rule(
        rule_id="SEC-PROCESS-SPAWN",
        category="resource_limit",
        severity="low",
        description="启动子进程或替换进程映像（受沙箱进程数上限约束）",
        text_patterns=_rx(_PROCESS_SPAWN),
        languages=("python",),
    ),
    Rule(
        rule_id="SEC-DYNAMIC-EXEC",
        category="sandbox_violation",
        severity="medium",
        description="动态执行代码：eval/exec/compile",
        text_patterns=_rx(_DYNAMIC_EXEC),
        languages=("python",),
    ),
    Rule(
        rule_id="SEC-OBFUSCATED-PAYLOAD",
        category="sandbox_violation",
        severity="high",
        description="混淆载荷：解码/反序列化结果直接动态执行",
        ast_probe="obfuscated_payload",
    ),
    Rule(
        rule_id="SEC-NETWORK-CALL",
        category="network_attempt",
        severity="high",
        description="网络访问调用：套接字或 HTTP/FTP/SMTP 客户端发起连接",
        text_patterns=_rx(_NETWORK_CALL_PY),
        languages=("python",),
    ),
    Rule(
        rule_id="SEC-NETWORK-CLIENT",
        category="network_attempt",
        severity="high",
        description="网络访问命令：命令行客户端发起外部连接",
        text_patterns=_rx(_NETWORK_CLIENT_BASH),
        languages=("bash",),
    ),
    Rule(
        rule_id="SEC-DOCKER-SOCKET",
        category="sandbox_violation",
        severity="high",
        description="访问宿主容器守护进程套接字（沙箱逃逸面）",
        text_patterns=_DOCKER_TEXT,
        literal_patterns=_DOCKER_LITERAL,
    ),
    Rule(
        rule_id="SEC-ARCHIVE-EXTRACT",
        category="archive_bomb",
        severity="medium",
        description="解压归档（未声明大小与层数上限时的解压面）",
        text_patterns=_rx(_ARCHIVE_EXTRACT),
        languages=("python",),
    ),
    Rule(
        rule_id="SEC-ARCHIVE-BOMB",
        category="archive_bomb",
        severity="high",
        description="压缩炸弹形态：循环内反复解压或解压结果再解压",
        ast_probe="archive_bomb",
    ),
    Rule(
        rule_id="SEC-RESOURCE-EXHAUSTION",
        category="resource_limit",
        severity="high",
        description="资源耗尽形态：申请超大内存或超大迭代规模",
        ast_probe="resource_exhaustion",
    ),
    Rule(
        rule_id="SEC-UNBOUNDED-LOOP",
        category="resource_limit",
        severity="medium",
        description="无界循环：条件恒真且循环自身无出口",
        ast_probe="unbounded_loop",
    ),
    Rule(
        rule_id="SEC-READONLY-WRITE",
        category="sandbox_violation",
        severity="high",
        description="写入只读根路径：向系统目录或块设备写入",
        text_patterns=_rx(_READONLY_WRITE),
        ast_probe="readonly_write",
    ),
)

RULES_BY_ID: Mapping[str, Rule] = {rule.rule_id: rule for rule in RULES}


def rule_table_problems() -> tuple[str, ...]:
    """规则表自检：返回问题清单（空元组 = 无问题）。"""
    problems: list[str] = []
    seen: set[str] = set()
    categories = get_args(SecurityCategory)
    severities = get_args(SecuritySeverity)
    for rule in RULES:
        if rule.rule_id in seen:
            problems.append(f"规则 ID 重复：{rule.rule_id}")
        seen.add(rule.rule_id)
        if not rule.rule_id.startswith("SEC-"):
            problems.append(f"规则 ID 前缀不合规：{rule.rule_id}")
        if not rule.description:
            problems.append(f"规则缺少中文说明：{rule.rule_id}")
        if not (rule.text_patterns or rule.literal_patterns or rule.ast_probe):
            problems.append(f"规则没有任何匹配面：{rule.rule_id}")
        if rule.ast_probe is not None and rule.ast_probe not in AST_PROBES:
            problems.append(f"结构探针未注册：{rule.rule_id} -> {rule.ast_probe}")
        if rule.category not in categories:
            problems.append(f"类别越界：{rule.rule_id} -> {rule.category}")
        if rule.severity not in severities:
            problems.append(f"严重度越界：{rule.rule_id} -> {rule.severity}")
        for language in rule.languages:
            if language not in ("python", "bash"):
                problems.append(f"语言越界：{rule.rule_id} -> {language}")
    for category in categories:
        if not any(rule.category == category for rule in RULES):
            problems.append(f"契约类别无规则覆盖：{category}")
    return tuple(problems)


# --------------------------------------------------------------------------------------
# 脱敏与文本匹配面
# --------------------------------------------------------------------------------------

#: finding 文本上限（`MAIN/contracts/schemas/evaluation-report.schema.json` 的 `message`）。
MESSAGE_LIMIT = 1000

_FINDING_ID_NAMESPACE = uuid.UUID("6f1a0f5c-2b7e-4c3a-9f10-5d0e7c2b4a11")


def redact_text(value: str, limit: int = MESSAGE_LIMIT) -> str:
    """脱敏：去控制字符、折叠空白并截断（findings 与结论文本一律走这里）。"""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in value)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 1]}…"


def safe_location(file_name: str) -> str:
    """只放行白名单内的**工作区相对路径**；其余一律替换为占位符（I19：无内部路径）。"""
    return file_name if is_allowed_relative_path(file_name) else "<提交文件>"


def python_code_text(source: str) -> str:
    """Python 源码的**代码文本**：注释与字符串字面量置空，保留行结构。

    词法分析失败（未闭合字符串等）⇒ `CheckUnavailableError`：该检查项按 `not_checked` 呈现，
    绝不退化成"在可能失真的文本上继续断言"。
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, UnicodeDecodeError) as exc:
        raise CheckUnavailableError(
            f"源码无法完成词法分析（{type(exc).__name__}），文本检查未执行"
        ) from None
    lines = [list(line) for line in source.splitlines(keepends=True)]
    for token in tokens:
        if token.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (start_row, start_col), (end_row, end_col) = token.start, token.end
        for row in range(start_row, end_row + 1):
            index = row - 1
            if index < 0 or index >= len(lines):
                continue
            line = lines[index]
            begin = start_col if row == start_row else 0
            end = end_col if row == end_row else len(line)
            for column in range(begin, min(end, len(line))):
                if line[column] not in "\r\n":
                    line[column] = " "
    return "".join("".join(line) for line in lines)


def strip_bash_comments(source: str) -> str:
    """剥掉 bash 注释（近似：不处理 `${VAR#pat}` 这类参数展开里的 `#`）。"""
    return "".join(_strip_bash_comment_line(line) for line in source.splitlines(keepends=True))


def _strip_bash_comment_line(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&("):
            head = line[:index].rstrip()
            return f"{head}\n" if line.endswith("\n") else head
    return line


def _string_literals(tree: ast.Module) -> tuple[tuple[str, int], ...]:
    """字符串字面量（含 f-string 常量片段），**排除文档字符串**（文档不是载荷）。"""
    docstrings = _docstring_nodes(tree)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            found.append((node.value, node.lineno))
    return tuple(found)


def _docstring_nodes(tree: ast.Module) -> frozenset[int]:
    bodies = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, bodies):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return frozenset(found)


def _text_hits(text: str, language: str) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for rule in RULES:
        if language not in rule.languages:
            continue
        for pattern in rule.text_patterns:
            for match in pattern.finditer(text):
                hits.append((rule.rule_id, text.count("\n", 0, match.start()) + 1))
    return hits


def _to_findings(file_name: str, hits: Iterable[tuple[str, int]]) -> tuple[SecurityFinding, ...]:
    """命中 → 脱敏 finding（ID 由 `uuid5(规则, 文件, 行)` 确定性派生，同输入同结果）。"""
    unique = sorted({(rule_id, line) for rule_id, line in hits if rule_id in RULES_BY_ID})
    location = safe_location(file_name)
    findings: list[SecurityFinding] = []
    for rule_id, line in unique:
        rule = RULES_BY_ID[rule_id]
        message = redact_text(f"{rule.description}（规则 {rule_id}，位置 {location}:{line}）")
        findings.append(
            SecurityFinding(
                finding_id=str(uuid.uuid5(_FINDING_ID_NAMESPACE, f"{rule_id}|{file_name}|{line}")),
                category=rule.category,
                severity=rule.severity,
                detected_in="static",
                message=message,
                rule_id=rule.rule_id,
            )
        )
    return tuple(findings)


# --------------------------------------------------------------------------------------
# 三个检查器（检查项标识见 decision.REQUIRED_CHECKS）
# --------------------------------------------------------------------------------------

#: Python AST + 字面量面检查器。
PYTHON_AST_CHECK_ID = "static:python_ast"

#: Python 代码文本面检查器（注释与字符串字面量已置空）。
PYTHON_TEXT_CHECK_ID = "static:python_text"

#: bash 文本面检查器（注释已剥离）。
BASH_TEXT_CHECK_ID = "static:bash_text"


def _syntax_reason(exc: BaseException) -> str:
    line = getattr(exc, "lineno", None)
    where = f"第 {line} 行" if isinstance(line, int) else "位置未知"
    return f"源码 {where} 无法完成语法解析（{type(exc).__name__}），结构检查未执行"


def analyze_python_ast(file_name: str, source: str) -> tuple[SecurityFinding, ...]:
    """Python 结构面：`ast.parse` + 探针 + 字符串字面量规则。**只解析，不执行**。"""
    try:
        tree = ast.parse(source, filename="<submission>")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise CheckUnavailableError(_syntax_reason(exc)) from None
    hits: list[tuple[str, int]] = []
    for rule in RULES:
        if rule.ast_probe is None or "python" not in rule.languages:
            continue
        for line in AST_PROBES[rule.ast_probe](tree):
            hits.append((rule.rule_id, int(line)))
    literals = _string_literals(tree)
    for rule in RULES:
        if "python" not in rule.languages or not rule.literal_patterns:
            continue
        for pattern in rule.literal_patterns:
            for value, line in literals:
                if pattern.search(value):
                    hits.append((rule.rule_id, line))
    return _to_findings(file_name, hits)


def analyze_python_text(file_name: str, source: str) -> tuple[SecurityFinding, ...]:
    """Python 文本面：注释与字符串字面量置空后的代码文本。"""
    return _to_findings(file_name, _text_hits(python_code_text(source), "python"))


def analyze_bash_text(file_name: str, source: str) -> tuple[SecurityFinding, ...]:
    """bash 文本面：注释剥离后的源码（近似；运行期沙箱仍是独立防线）。"""
    return _to_findings(file_name, _text_hits(strip_bash_comments(source), "bash"))
