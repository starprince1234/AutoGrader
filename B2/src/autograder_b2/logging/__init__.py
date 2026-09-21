"""结构化日志与脱敏（RQ-B-2-99）：单行 JSON、关联标识贯穿、白名单式记录、长度上限。

| 模块 | 职责 |
|---|---|
| `redaction` | 白名单字段准入 + 自由文本模式替换/转义/上限（脱敏规则的**唯一**落点） |
| `context` | `traceparent`/`X-Request-ID` → `correlation_id` 规范化 + 上下文绑定传播 |
| `logger` | 单行 JSON 格式化器、安全流处理器、logger 工厂、依赖故障事件 |

范围引用 `docs/architecture/boundaries.md` 第 4 节与 `MAIN/docs/api/identifiers.md` 第 2.1 节
（不另立一套）；字段上限 200 / 整行上限 800 与 `intake/imap.py` 既有安全日志同值同风格。
"""

from __future__ import annotations

from autograder_b2.logging.context import (
    LogContext,
    bind_context,
    bind_operation,
    correlation_id_from_headers,
    current_context,
    current_correlation_id,
    new_correlation_id,
    normalize_correlation_id,
    parse_request_id,
    parse_traceparent,
)
from autograder_b2.logging.logger import (
    B2_EVENT_KEY,
    B2_SECURITY_EVENT_KEY,
    BROKER_UNAVAILABLE_EVENT,
    DATABASE_UNAVAILABLE_EVENT,
    DEFAULT_MAX_FIELD,
    DEFAULT_MAX_LINE,
    DEPENDENCY_EVENTS,
    DEPENDENCY_STATUS_UNAVAILABLE,
    GENERIC_DEPENDENCY_EVENT,
    MANDATORY_FIELDS,
    RENDER_FAILED_EVENT,
    SANDBOX_UNAVAILABLE_EVENT,
    UNSTRUCTURED_EVENT,
    JsonLogFormatter,
    SafeStreamHandler,
    build_logger,
    dependency_guard,
    log_dependency_unavailable,
    log_event,
    render_json_line,
)
from autograder_b2.logging.redaction import (
    ALLOWED_FIELDS,
    FORBIDDEN_FIELD_NAMES,
    FREE_TEXT_FIELDS,
    MAX_LOG_FIELD,
    MAX_LOG_LINE,
    REDACTED_CREDENTIAL,
    REDACTED_HOST,
    REDACTED_PATH,
    TRUNCATION_MARKER,
    UUID_FIELDS,
    SanitizedPayload,
    is_allowed_field,
    looks_like_code,
    sanitize_fields,
    sanitize_identifier,
    sanitize_text,
)

__all__ = [
    "ALLOWED_FIELDS",
    "B2_EVENT_KEY",
    "B2_SECURITY_EVENT_KEY",
    "BROKER_UNAVAILABLE_EVENT",
    "DATABASE_UNAVAILABLE_EVENT",
    "DEFAULT_MAX_FIELD",
    "DEFAULT_MAX_LINE",
    "DEPENDENCY_EVENTS",
    "DEPENDENCY_STATUS_UNAVAILABLE",
    "FORBIDDEN_FIELD_NAMES",
    "FREE_TEXT_FIELDS",
    "GENERIC_DEPENDENCY_EVENT",
    "MANDATORY_FIELDS",
    "MAX_LOG_FIELD",
    "MAX_LOG_LINE",
    "REDACTED_CREDENTIAL",
    "REDACTED_HOST",
    "REDACTED_PATH",
    "RENDER_FAILED_EVENT",
    "SANDBOX_UNAVAILABLE_EVENT",
    "TRUNCATION_MARKER",
    "UNSTRUCTURED_EVENT",
    "UUID_FIELDS",
    "JsonLogFormatter",
    "LogContext",
    "SafeStreamHandler",
    "SanitizedPayload",
    "bind_context",
    "bind_operation",
    "build_logger",
    "correlation_id_from_headers",
    "current_context",
    "current_correlation_id",
    "dependency_guard",
    "is_allowed_field",
    "log_dependency_unavailable",
    "log_event",
    "looks_like_code",
    "new_correlation_id",
    "normalize_correlation_id",
    "parse_request_id",
    "parse_traceparent",
    "render_json_line",
    "sanitize_fields",
    "sanitize_identifier",
    "sanitize_text",
]
