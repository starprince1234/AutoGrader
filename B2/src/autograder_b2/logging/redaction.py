"""日志字段脱敏：**白名单**准入 + 自由文本模式替换 + 长度上限 + 控制字符转义。

脱敏范围**引用既有权威**，不另立一套（任务书要求）：

- `docs/architecture/boundaries.md` 第 4 节「日志脱敏范围」；
- `MAIN/docs/api/identifiers.md` 第 2.1 节「绝不出现在公开 DTO / 事件 / 日志中的标识与内容」。

机制（与 `intake/imap.py` 既有安全日志同一风格：字段上限 200、整行上限 800）：

1. **白名单准入**：只有 `ALLOWED_FIELDS` 里的字段名才会被记录，未知字段名一律丢弃——这是
   白名单而不是黑名单过滤。丢弃只计数量、**不记名字**：字段名本身也是外部可控文本。
2. **标识字段必须形状合法**：`UUID_FIELDS` 只接受 UUID（规范化成标准带连字符形式）；形状不合法
   即丢弃。于是"把源码原文塞进 `submission_id`"不可能被记录下来。
3. **自由文本**（`FREE_TEXT_FIELDS`，实际上对**所有**字符串值生效）：
   先过**形状闸门**（第 5 条），再已知模式替换（内部绝对路径、凭据、URL 权威段）→
   控制字符转义 + 代理码位替换 → 长度上限（默认 200，带 `...[truncated]` 标注）。
4. **嵌套结构一律丢弃**：白名单是**扁平**的，`{"detail": {"source": "..."}}` 不构成绕过口。
5. **自由文本的形状闸门（fail-closed）**：命中"源码形态"（`def`/`class` 定义、`return`/`import`
   语句、缩进块、赋值 + 调用…）的字符串**整段丢弃**，而不是"转义后照样记"。

做不到的部分（如实记录，不假装）：**单行散文形态的参考答案、单个标识符、一行 `print("hi")`
之外的任意文本无法与"正常原因文本"区分**。本模块因此把防线叠成三层——字段名白名单（第 1 条）、
标识形状校验（第 2 条）、源码形态闸门（第 5 条）——并明确要求调用方：自由文本只放**已知安全的短
原因短语**，绝不把源码/参考答案/期望输出搬进来。形状闸门是启发式的，代价是极少数普通原因文本
（例如以 `import`/`from` 开头的短语）会被一并丢弃；方向始终是安全的：**少记，不会多记**。
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autograder_b2.api.formatting import rfc3339_millis

#: 结构化字段值上限（与 `intake/imap.py` 的 `MAX_LOG_FIELD` 同值：两处各自兜底，互不 import）。
MAX_LOG_FIELD = 200
#: 整行渲染上限（与 `intake/imap.py` 的 `MAX_LOG_LINE` 同值）。真正的截断在 `logger.render_json_line`。
MAX_LOG_LINE = 800

#: 截断标注（保留可识别前缀，只在尾部标注）。
TRUNCATION_MARKER = "...[truncated]"
#: 模式替换的占位符。
REDACTED_PATH = "<redacted-path>"
REDACTED_CREDENTIAL = "<redacted-credential>"
REDACTED_HOST = "<redacted-host>"

#: 允许记录的字段名（白名单；`docs/architecture/boundaries.md` 第 4 节的"允许记录"清单 +
#: `intake/imap.py` 既有的安全事件字段集合 + 本模块渲染信封自身需要的字段）。
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        # 渲染信封（`timestamp`/`level`/`event`/`producer` 由 `logger.py` 合成，恒存在）
        "timestamp",
        "level",
        "event",
        "producer",
        "logger",
        "truncated",
        # 关联标识
        "request_id",
        "correlation_id",
        "causation_id",
        "event_id",
        "stage",
        # B2 允许记录的标识
        "submission_id",
        "evaluation_run_id",
        "evaluation_result_id",
        "security_finding_id",
        "artifact_id",
        "course_id",
        "section_id",
        "question_version_id",
        "assignment_id",
        # 状态 / 阶段 / 结论 / 错误类别
        "status",
        "state",
        "from_state",
        "to_state",
        "outcome",
        "result",
        "decision",
        "severity",
        "category",
        "class",
        "security_class",
        "detected_in",
        "phase",
        "dependency",
        "error_type",
        "error_category",
        # 计数 / 度量
        "count",
        "counts",
        "total",
        "attempts",
        "elapsed_ms",
        "duration_ms",
        "size_bytes",
        "file_count",
        "score",
        "max_score",
        "passed",
        "failed",
        "skipped",
        # 规则 / 用例标识（**不含**隐藏用例 ID 与期望输出：见 `FORBIDDEN_FIELD_NAMES`）
        "rule_id",
        "rule_ids",
        "case_id",
        "case_ids",
        "check_id",
        "check_ids",
        "violation_count",
        # 自由文本（长度上限 + 控制字符转义 + 已知模式替换）
        "detail",
        "message",
        "reason",
        # 脱敏自身的可观测计数
        "redacted_fields",
        "redacted_forbidden_fields",
        # `intake/imap.py` 既有安全事件字段（同仓约定，保持可渲染）
        "mailbox",
        "uidvalidity",
        "uid",
        "message_id",
        "attachment_name",
        "reason_code",
    }
)

#: 自由文本字段：整行超限时**优先丢弃**，其值受模式替换与长度上限约束。
FREE_TEXT_FIELDS: frozenset[str] = frozenset({"detail", "message", "reason"})

#: 只接受 UUID 的字段：形状不合法即丢弃（绝不做"截断后照样记"）。
UUID_FIELDS: frozenset[str] = frozenset(
    {
        "request_id",
        "correlation_id",
        "causation_id",
        "event_id",
        "submission_id",
        "evaluation_run_id",
        "evaluation_result_id",
        "security_finding_id",
        "artifact_id",
        "course_id",
        "section_id",
        "question_version_id",
        "assignment_id",
    }
)

#: 已知的"禁止出现"字段名。**不是**过滤黑名单（白名单已经在前面挡掉一切未知名字）；这里只是
#: 把这些名字单独计数，便于运维发现"有人试图记录源码/凭据/路径"。名字与取值都不会被写进日志。
FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        # 源码内容
        "source",
        "source_code",
        "source_text",
        "code",
        "content",
        "file_content",
        "file_bytes",
        "body",
        "plain_text",
        # 参考答案 / 隐藏用例
        "answer",
        "answers",
        "reference",
        "reference_answer",
        "reference_solution",
        "solution",
        "expected",
        "expected_output",
        "expected_stdout",
        "hidden",
        "hidden_case_id",
        "hidden_test_case_id",
        "hidden_tests",
        "tests",
        "stdout",
        "stderr",
        "output",
        # 内部绝对路径 / 内部主机
        "path",
        "file_path",
        "storage_path",
        "absolute_path",
        "internal_path",
        "host",
        "hostname",
        "endpoint",
        "port",
        "dsn",
        "database_url",
        # 凭据
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "api_key",
        "cookie",
        "cookies",
        "authorization",
        "credential",
        "credentials",
        "private_key",
        # 超范围 PII
        "student_name",
        "student_email",
        "email",
        "phone",
        "address",
        "national_id",
        # 堆栈
        "stack",
        "stacktrace",
        "traceback",
    }
)

#: 内部哨兵：值不可记录（结构性问题，例如嵌套 Mapping）。
_DROPPED = object()
#: 内部哨兵：值命中"禁止内容形态"（源码形态），整段丢弃并单独计数。
_DROPPED_FORBIDDEN = object()
#: 序列字段的元素个数上限（避免一个列表吃掉整行）。
_MAX_SEQUENCE_ITEMS = 16

# --- 模式 ---------------------------------------------------------------------

#: `scheme://user:pass@host/...`：整段拿掉（密码 + 内部主机一起）。
_DSN_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/@:]+:[^\s/@]+@[^\s]+")
#: `Bearer <值>` / `Basic <值>`：**必须**先于键值规则（否则 `Authorization: Bearer <jwt>` 会
#: 只吃掉 `Bearer` 而把真正的令牌留下）。
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(?P<scheme>bearer|basic)\s+[A-Za-z0-9\-._~+/=]{4,}")
#: JWT 形态（三段 base64url）。
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}")
#: **裸令牌值**（没有 `key=` 前缀也照样脱敏）：常见前缀 + 足够长度才算，避免把普通标识吃掉。
#: 补这一条的原因（实测）：失败原因/异常文本里经常直接嵌着令牌值本身，例如事件派发失败时的
#: `... exploded with <前缀>_...`，此时键值规则没有任何可命中的键名，令牌就会原样进日志与
#: `outbox_events.last_error`。左侧用 `(?<![A-Za-z0-9])` 而不是 `\b`：令牌常紧跟在下划线后
#: （`access_token_<前缀>_...`），那里 `\b` 不成立。
_TOKEN_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9_]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9_]{16,}"
    r"|xox[baprs]-[A-Za-z0-9\-_]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{30,})"
)
#: 凭据键（`key=value`、`"key": "value"`、`key: value`）。后缀容忍 `[a-z0-9_]*`，
#: 使 `access_token`/`secret_sha256` 这类带后缀的名字也能命中。
_CREDENTIAL_KEY = (
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|token|"
    r"password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey|"
    r"authorization|cookie|set[_-]?cookie|session[_-]?id|session|"
    r"signature|private[_-]?key|credential"
)
_CREDENTIAL_RE = re.compile(
    rf"(?i)\b(?P<key>{_CREDENTIAL_KEY})[a-z0-9_]*"
    rf"(?P<sep>\s*[\"']?\s*[:=]\s*)(?P<quote>[\"']?)"
    # `Bearer`/`Basic` 是认证方案名而不是凭据本身：留给上一步的整段替换处理，
    # 否则会出现 `Authorization: <redacted-credential> <redacted-credential>` 这种噪音。
    rf"(?P<value>(?!(?:bearer|basic)\b)[^\s\"',;&}}]+)(?P=quote)"
)
#: URL 权威段（内部主机名与端口不可判别，一律替换：宁可多脱敏）。
_URL_AUTHORITY_RE = re.compile(r"(?i)\b(?P<scheme>[a-z][a-z0-9+.\-]*://)[^\s/]+")
#: Windows 盘符路径。前置否定环视很关键：没有它，`https://x` 会被当成盘符路径 `s:/`。
_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"(?:[^\\/\s\"'<>|:*?\x00-\x1f]+[\\/])*[^\\/\s\"'<>|:*?\x00-\x1f]*"
)
#: UNC 路径。
_UNC_PATH_RE = re.compile(r"\\\\[^\\/\s]+[\\/][^\s]*")
#: POSIX 绝对路径（至少两段，避免把 `and/or` 这种普通文本吃掉）；再补一组常见根目录单词。
_POSIX_PATH_RE = re.compile(
    r"/(?:[\w.\-]+/)+[\w.\-]*"
    r"|/(?:srv|etc|var|usr|opt|home|root|tmp|mnt|proc|sys|dev)\b"
)
#: 内部主机名 / localhost（含端口）。
_INTERNAL_HOST_RE = re.compile(
    r"(?i)\b(?:localhost|[\w\-]+\.(?:internal|local|lan|corp|localdomain))(?::\d+)?\b"
)
#: 自由文本的**形状闸门**：命中任一模式即判定"这是代码原文，不是给运维看的原因文本"，整段丢弃。
#: 启发式，宁可误丢（少记一条自由文本）也不赌它是不是源码——方向必须是 fail-closed。
_CODE_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|\s)(?:async\s+)?(?:def|class)\s+\w+\s*[\(:]"),
    re.compile(r"\breturn\b\s+\S+"),
    re.compile(r"(?:^|\s)(?:import|from)\s+[\w.]+"),
    re.compile(r"\blambda\b[^:\n]*:"),
    re.compile(r"^\s*[\w.\[\]]+\s*=\s*(?:[\w.]+\(|[\"'\[{])"),
    re.compile(r"\)\s*:\s*$"),
    re.compile(r"\n[ \t]{2,}\S"),
)


def looks_like_code(value: str) -> bool:
    """文本是否呈"源码形态"（函数/类定义、`return`/`import` 语句、缩进块、赋值 + 调用）。"""
    return any(pattern.search(value) for pattern in _CODE_SHAPE_PATTERNS)


def sanitize_text(value: object, *, max_length: int = MAX_LOG_FIELD) -> str:
    """自由文本脱敏：模式替换 → 控制字符转义/代理码位替换 → 长度上限（带截断标注）。

    - 代理码位（如 `\\udce4`）替换成 U+FFFD：这类字符串无法 UTF-8 编码，留着会让写日志失败；
    - 控制字符（`\\n`/`\\r`/`\\t`/C0/DEL/U+2028/U+2029/U+0085）转义成 `\\\\n` 这类两字符文本，
      于是"外部输入决定日志行数"不成立；
    - 反斜杠一并转义，保证转义本身是单义的（`\\\\x41` 不会被误读成 `A`）。

    本函数**不**做源码形态判定：那是字段级闸门（`looks_like_code` + `sanitize_fields`）的职责，
    因为"整段丢弃"是字段级决定，不是文本级变换。
    """
    text = _safe_str(value)
    text = _redact_patterns(text)
    text = _escape_controls(text)
    return _truncate(text, max_length)


def truncate_text(value: str, max_length: int) -> str:
    """按上限截断并**标注**截断（保留前缀；上限含标注本身）。"""
    return _truncate(value, max_length)


def sanitize_identifier(name: str, value: object) -> str | None:
    """标识字段只接受 UUID（规范化成标准形式）；其余一律返回 `None`（调用方丢弃该字段）。

    这条校验是"把源码塞进 `submission_id`"的正面挡板：形状不合法就不记录，而不是记个截断版。
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(uuid.UUID(value.strip()))
        except (AttributeError, TypeError, ValueError):
            return None
    return None


def is_allowed_field(name: object) -> bool:
    """字段名是否在白名单内。"""
    return isinstance(name, str) and name in ALLOWED_FIELDS


@dataclass(frozen=True, slots=True)
class SanitizedPayload:
    """白名单脱敏结果：可记录的字段 + 丢弃计数（被丢弃的字段名**不**记录）。"""

    fields: dict[str, Any]
    dropped: int
    dropped_forbidden: int


def sanitize_fields(
    fields: Mapping[str, object], *, max_field: int = MAX_LOG_FIELD
) -> SanitizedPayload:
    """按白名单 + 形状校验 + 脱敏把任意 payload 收敛成"可以进日志的字段字典"。

    返回值里的 `fields` 只含白名单字段，值为 JSON 可序列化的标量或标量列表，且每个值 ≤ `max_field`。
    """
    allowed: dict[str, Any] = {}
    dropped = 0
    dropped_forbidden = 0
    for name, value in fields.items():
        if not is_allowed_field(name):
            dropped += 1
            if isinstance(name, str) and name in FORBIDDEN_FIELD_NAMES:
                dropped_forbidden += 1
            continue
        if value is None:
            # 缺失就是不写，绝不伪造空串（任务书要求）。
            allowed[name] = None
            continue
        if name in UUID_FIELDS:
            identifier = sanitize_identifier(name, value)
            if identifier is None:
                dropped += 1
                dropped_forbidden += 1
                continue
            allowed[name] = identifier
            continue
        sanitized = _sanitize_value(value, max_field=max_field)
        if sanitized is _DROPPED_FORBIDDEN:
            # 命中禁止内容形态（源码形态）：整段丢弃，并单独计数（不记名字、不记取值）。
            dropped += 1
            dropped_forbidden += 1
            continue
        if sanitized is _DROPPED:
            dropped += 1
            continue
        allowed[name] = sanitized
    return SanitizedPayload(fields=allowed, dropped=dropped, dropped_forbidden=dropped_forbidden)


def _sanitize_value(value: object, *, max_field: int) -> object:
    """把单个字段值收敛成 JSON 可序列化、已脱敏、有上限的值（不可记录时返回哨兵）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN/Infinity 不是合法 JSON：转成文本而不是让整条日志变成非法 JSON。
        if math.isfinite(value):
            return value
        return sanitize_text(repr(value), max_length=max_field)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # 字节串是**内容**而不是标识/计数（源码、附件原文都可能以 bytes 传进来）→ 整段丢弃。
        return _DROPPED_FORBIDDEN
    if isinstance(value, str):
        if looks_like_code(value):
            return _DROPPED_FORBIDDEN
        return sanitize_text(value, max_length=max_field)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return rfc3339_millis(value)
    if isinstance(value, Mapping):
        # 嵌套结构没有字段名准入可言（内层键不可控）→ 整段丢弃。
        return _DROPPED
    if isinstance(value, (list, tuple)):
        items: list[object] = []
        for item in value[:_MAX_SEQUENCE_ITEMS]:
            element = _sanitize_value(item, max_field=max_field)
            if element is _DROPPED:
                continue
            items.append(element)
        return items
    text = _safe_str(value)
    if looks_like_code(text):
        return _DROPPED_FORBIDDEN
    return sanitize_text(text, max_length=max_field)


def _redact_patterns(text: str) -> str:
    """按已知模式替换。顺序有意义：先吃掉"带凭据的整段"，再处理单个凭据与路径。"""
    redacted = _DSN_RE.sub(REDACTED_CREDENTIAL, text)
    redacted = _AUTH_SCHEME_RE.sub(
        lambda match: f"{match.group('scheme')} {REDACTED_CREDENTIAL}", redacted
    )
    redacted = _JWT_RE.sub(REDACTED_CREDENTIAL, redacted)
    redacted = _TOKEN_VALUE_RE.sub(REDACTED_CREDENTIAL, redacted)
    redacted = _CREDENTIAL_RE.sub(_credential_replacement, redacted)
    redacted = _URL_AUTHORITY_RE.sub(
        lambda match: f"{match.group('scheme')}{REDACTED_HOST}", redacted
    )
    redacted = _WINDOWS_PATH_RE.sub(REDACTED_PATH, redacted)
    redacted = _UNC_PATH_RE.sub(REDACTED_PATH, redacted)
    redacted = _POSIX_PATH_RE.sub(REDACTED_PATH, redacted)
    return _INTERNAL_HOST_RE.sub(REDACTED_HOST, redacted)


def _credential_replacement(match: re.Match[str]) -> str:
    """保留键名（运维需要知道"是哪个键"，不需要知道值），只替换取值。"""
    key = match.group("key")
    separator = match.group("sep")
    quote = match.group("quote")
    return f"{key}{separator}{quote}{REDACTED_CREDENTIAL}{quote}"


def _escape_controls(text: str) -> str:
    """控制字符转义 + 代理码位替换（保证结果一定能 UTF-8 编码，且不含换行）。"""
    pieces: list[str] = []
    for char in text:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            pieces.append("\ufffd")
        elif char == "\\":
            pieces.append("\\\\")
        elif char == "\n":
            pieces.append("\\n")
        elif char == "\r":
            pieces.append("\\r")
        elif char == "\t":
            pieces.append("\\t")
        elif code < 0x20 or code == 0x7F:
            pieces.append(f"\\x{code:02x}")
        elif code in (0x85, 0x2028, 0x2029):
            pieces.append(f"\\u{code:04x}")
        else:
            pieces.append(char)
    return "".join(pieces)


def _truncate(text: str, max_length: int) -> str:
    """截断到上限并标注（上限含标注本身）。"""
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    if max_length <= len(TRUNCATION_MARKER):
        return text[:max_length]
    return text[: max_length - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _safe_str(value: object) -> str:
    """`str()` 的兜底：对象自己的 `__str__` 抛异常时也要给出可记录文本。"""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - 渲染路径绝不能因外部对象的 `__str__` 而抛出
        # 那会丢整条日志，所以这里必须兜住一切。
        return f"<unprintable:{type(value).__name__}>"


__all__ = [
    "ALLOWED_FIELDS",
    "FORBIDDEN_FIELD_NAMES",
    "FREE_TEXT_FIELDS",
    "MAX_LOG_FIELD",
    "MAX_LOG_LINE",
    "REDACTED_CREDENTIAL",
    "REDACTED_HOST",
    "REDACTED_PATH",
    "TRUNCATION_MARKER",
    "UUID_FIELDS",
    "SanitizedPayload",
    "is_allowed_field",
    "looks_like_code",
    "sanitize_fields",
    "sanitize_identifier",
    "sanitize_text",
    "truncate_text",
]
