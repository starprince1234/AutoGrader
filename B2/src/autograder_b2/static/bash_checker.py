"""Bash 静态检查：`bash -n` **只解析不执行** + 结构化文本规则（I9，ADR-0001 第 2 条）。

启动一律用**固定参数数组**（`[<bash>, "-n", <脚本绝对路径>]`）：不拼接 shell 字符串、不经由 shell
解释、不读 stdin（`stdin=DEVNULL`，避免解释器等输入）。脚本路径由工作区根派生且已过 I2 白名单，
因此不可能把选项或命令注入到参数位上。

诚实原则（I14）：解释器不可用、无法读取工作区路径、输出无法归因到行号时，结论只能是
`not_checked` + 原因，**绝不允许**把它说成通过或什么也发现不了。语法错误行号从 stderr 的 `line N:`
片段解析；stderr 里的源码片段（以反引号开头的上下文行）与内部绝对路径**一律丢弃**（I12/I19）。
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from autograder_b2.domain.static_result import StaticFinding
from autograder_b2.static.rules import make_finding

#: 探测解释器可用性的超时（秒）。
_PROBE_TIMEOUT_SECONDS = 10.0

#: 单次 `bash -n` 的超时（秒）。只做语法解析，正常瞬间返回。
_SYNTAX_TIMEOUT_SECONDS = 15.0

#: `bash -n` 的 stderr 形态：`<任意前缀>: line N: <说明>`。
_BASH_LINE_PATTERN = re.compile(r"line (?P<line>\d+):\s*(?P<message>.*)\Z")

#: 源码上下文行的起始字符（`bash` 用反引号/引号引出出错的那一行源码）。
_CONTEXT_PREFIXES = ("`", "‘", "'", "“")

#: `curl ... | bash` 一类：下载内容直接交给 shell 解释。
_PIPE_TO_SHELL = re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:bash|sh|dash|zsh|ksh)\b")

#: fork bomb：函数名 `:` 递归自我复制并后台展开。
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{[^{}]*:\s*\|\s*:[^{}]*\}\s*;\s*:")

#: 敏感凭据文件路径。
_SENSITIVE_PATH = re.compile(r"/etc/(?:shadow|gshadow|sudoers)\b")

#: 反向 shell：网络重定向、交互式 shell 重定向、nc 执行模式。
_REVERSE_SHELL = (
    re.compile(r"/dev/tcp/"),
    re.compile(r"\bbash\s+-i\b[^;&|]*>&"),
    re.compile(r"\bnc\b[^;&|]*\s-e\s"),
)

#: `eval` 调用（行首或命令分隔符之后的命令位）。
_EVAL_CALL = re.compile(r"(?:^|[;&|(]\s*)eval\s")

#: 破坏性 `rm` 的调用起点（含可选 sudo）。
_DESTRUCTIVE_RM = re.compile(r"(?:^|[;&|(]\s*)(?:sudo\s+)?rm\s+([^;&|]*)")

#: 严格模式声明：`set -e` / `set -euo pipefail` / `set -o errexit`。
_STRICT_MODE = re.compile(r"^\s*set\s+(?:-[A-Za-z]*e[A-Za-z]*\b|-[A-Za-z]*o\s+errexit\b)")

#: `rm` 指向"根目录/家目录"的目标写法。
_ROOT_TARGETS = frozenset({"/", "/*", "~", "~/", "~/*", "$HOME", "${HOME}", "$HOME/*", "${HOME}/*"})


@dataclass(frozen=True, slots=True)
class BashTarget:
    """一个待检查脚本：工作区相对路径 + 绝对路径 + 源码文本（源码只用于 I12 自检）。"""

    relative: str
    absolute: pathlib.Path
    source: str


@dataclass(frozen=True, slots=True)
class BashSyntaxResult:
    """`bash -n` 检查的结论（`unavailable_reason` 非 None ⇒ 该检查整体未执行）。"""

    findings: tuple[StaticFinding, ...]
    checked: tuple[str, ...]        # 已交给 bash 且成功归因的文件（工作区相对路径）
    unattributed: tuple[str, ...]   # 无法归因（解释器读不到路径/输出无法解析）的文件
    unavailable_reason: str | None


def probe_bash() -> str | None:
    """探测**可用**的 bash 解释器路径；返回 `None` 表示不可用（≠"脚本没问题"）。

    只找到可执行文件还不够：`bash --version` 必须正常退出。Windows 上 `bash.exe` 可能是 WSL 启动器
    ——它本身可用，但看不到工作区的 Windows 路径，那种情形由 `check_bash_syntax` 按"无法归因"处理。
    """
    candidate = shutil.which("bash")
    if not candidate:
        return None
    try:
        completed = subprocess.run(
            [candidate, "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return candidate if completed.returncode == 0 else None


def check_bash_syntax(
    bash_path: str | None,
    targets: Sequence[BashTarget],
) -> BashSyntaxResult:
    """对每个脚本跑一次 `bash -n`，把语法错误解析成带行号的明细。

    严格归因：只要有任一脚本的输出**无法定位到行号**（例如解释器不在工作区的文件系统视图内），
    本次检查整体记为未执行（`unavailable_reason` 非 None、`findings` 为空）。理由：`CheckOutcome`
    只能表达"整体跑了/整体没跑"，声称"跑了"却漏掉某个文件的行号等于虚报覆盖；宁可如实报未检查。
    """
    if bash_path is None:
        return BashSyntaxResult(
            findings=(),
            checked=(),
            unattributed=tuple(target.relative for target in targets),
            unavailable_reason="运行环境没有可用的 bash 解释器：Bash 语法检查未执行",
        )
    if not targets:
        return BashSyntaxResult(
            findings=(),
            checked=(),
            unattributed=(),
            unavailable_reason="本次没有可交给 bash -n 的脚本：Bash 语法检查未执行",
        )

    findings: list[StaticFinding] = []
    checked: list[str] = []
    unattributed: list[str] = []
    for target in targets:
        try:
            completed = _invoke(bash_path, target.absolute)
        except (OSError, subprocess.SubprocessError):
            unattributed.append(target.relative)
            continue
        stderr = completed.stderr if isinstance(completed.stderr, str) else ""
        if completed.returncode == 0:
            checked.append(target.relative)
            continue
        parsed = _parse_bash_stderr(stderr)
        if parsed is None:
            unattributed.append(target.relative)
            continue
        checked.append(target.relative)
        findings.extend(_syntax_findings(target, parsed))

    if unattributed:
        return BashSyntaxResult(
            findings=(),
            checked=(),
            unattributed=tuple(unattributed),
            unavailable_reason=_unavailable_reason(unattributed),
        )
    return BashSyntaxResult(
        findings=tuple(findings),
        checked=tuple(checked),
        unattributed=(),
        unavailable_reason=None,
    )


def bash_structural_findings(filename: str, source: str) -> tuple[StaticFinding, ...]:
    """按规则目录做 Bash 结构化检查（启发式：匹配命令行文本，不看执行语义）。

    只对"去掉行尾注释"后的命令部分匹配，避免注释里的示例被判成命中（阻断类规则必须宁缺勿滥）。
    行号/列号取匹配位置（1-based）；`set -e` 缺失属于全文属性，按第 1 行呈现。
    """
    findings: list[StaticFinding] = []
    for index, raw_line in enumerate(source.splitlines(), start=1):
        code = _code_part(raw_line)
        if not code.strip():
            continue
        for column in _destructive_rm_columns(code):
            findings.append(
                make_finding(
                    "STAT-SH-010",
                    file=filename,
                    line=index,
                    column=column,
                    detail="递归且强制删除，目标为根目录或家目录",
                    source=source,
                )
            )
        findings.extend(_line_rule_findings(filename, source, index, code))
    if not _has_strict_mode(source):
        findings.append(
            make_finding(
                "STAT-SH-021",
                file=filename,
                line=1,
                column=None,
                detail="全文未见 set -e 一类声明",
                source=source,
            )
        )
    return tuple(findings)


def _line_rule_findings(
    filename: str,
    source: str,
    index: int,
    code: str,
) -> list[StaticFinding]:
    """一行命令上的文本规则命中（规则顺序固定，保证确定性）。"""
    findings: list[StaticFinding] = []
    for rule_id, patterns, detail in _LINE_RULES:
        for pattern in patterns:
            for match in pattern.finditer(code):
                findings.append(
                    make_finding(
                        rule_id,
                        file=filename,
                        line=index,
                        column=match.start() + 1,
                        detail=detail,
                        source=source,
                    )
                )
    return findings


#: 逐行文本规则：(规则 ID, 模式序列, 结构化说明)。
_LINE_RULES: tuple[tuple[str, tuple[re.Pattern[str], ...], str], ...] = (
    ("STAT-SH-011", (_PIPE_TO_SHELL,), "下载工具的输出被管道交给 shell 解释"),
    ("STAT-SH-012", (_FORK_BOMB,), "检测到递归自我复制并后台展开的函数"),
    ("STAT-SH-013", (_SENSITIVE_PATH,), "引用系统账号口令文件路径"),
    ("STAT-SH-014", _REVERSE_SHELL, "检测到网络重定向或交互式 shell 重定向"),
    ("STAT-SH-020", (_EVAL_CALL,), "调用 eval 执行字符串"),
)


def _invoke(bash_path: str, script: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """以固定参数数组调用 `bash -n`：不经 shell、不拼接字符串、不读 stdin、不执行脚本内容。"""
    return subprocess.run(
        [bash_path, "-n", str(script)],
        capture_output=True,
        text=True,
        timeout=_SYNTAX_TIMEOUT_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _syntax_findings(
    target: BashTarget,
    parsed: tuple[tuple[int, str], ...],
) -> list[StaticFinding]:
    return [
        make_finding(
            "STAT-SH-001",
            file=target.relative,
            line=line,
            column=None,
            detail=detail,
            source=target.source,
            redact=(str(target.absolute), str(target.absolute.parent)),
        )
        for line, detail in parsed
    ]


def _parse_bash_stderr(raw: str) -> tuple[tuple[int, str], ...] | None:
    """把 `bash -n` 的 stderr 解析成 `(行号, 说明)`；`None` 表示**无法归因**。

    - 含 `line N:` 的说明行才算归因成功（I10：语法类错误必须带行号）。
    - 以反引号/引号开头的行是"出错源码上下文"，整行丢弃（I12）。
    - 说明里反引号之后的内容（形如 `near unexpected token \\`fi'`）一并截掉，避免内联源码。
    - 不含行号的噪声行（如解释器读不到路径）使该结果无法归因。
    """
    parsed: list[tuple[int, str]] = []
    for line in raw.splitlines():
        match = _BASH_LINE_PATTERN.search(line)
        if match is None:
            continue
        message = match.group("message").strip()
        if message.startswith(_CONTEXT_PREFIXES):
            continue
        head, separator, _tail = message.partition("`")
        cleaned = (head if separator else message).strip()
        if not cleaned:
            continue
        parsed.append((int(match.group("line")), cleaned))
    return tuple(dict.fromkeys(parsed)) if parsed else None


def _unavailable_reason(unattributed: Sequence[str]) -> str:
    """无法归因时的脱敏原因（只用工作区相对路径，不含内部绝对路径与源码）。"""
    shown = "、".join(unattributed[:3])
    more = f" 等 {len(unattributed)} 个脚本" if len(unattributed) > 3 else ""
    return (
        f"bash -n 的输出无法定位到行号{more}（解释器可能不在工作区的文件系统视图内）："
        f"{shown}；Bash 语法检查未执行"
    )


def _code_part(line: str) -> str:
    """去掉行尾注释后的命令部分（`#` 之后一律视为注释；只降低命中率，不制造命中）。"""
    return line.split("#", 1)[0]


def _has_strict_mode(source: str) -> bool:
    return any(_STRICT_MODE.match(_code_part(line)) for line in source.splitlines())


def _destructive_rm_columns(code: str) -> list[int]:
    """破坏性 `rm`（同时带递归与强制，目标为根/家目录）的匹配起始列（1-based）。"""
    columns: list[int] = []
    for match in _DESTRUCTIVE_RM.finditer(code):
        if _rm_targets_root_or_home(match.group(1)):
            columns.append(match.start() + 1)
    return columns


def _rm_targets_root_or_home(argument_text: str) -> bool:
    """解析 `rm` 的参数：必须同时具备递归(r)与强制(f)语义，且目标指向根/家目录。"""
    letters: set[str] = set()
    targets: list[str] = []
    for token in argument_text.split():
        if token.startswith("--"):
            if token == "--recursive":
                letters.add("r")
            elif token == "--force":
                letters.add("f")
        elif token.startswith("-"):
            letters.update(token.lstrip("-"))
        else:
            targets.append(token)
    if not {"r", "f"} <= letters:
        return False
    return any(target in _ROOT_TARGETS for target in targets)
