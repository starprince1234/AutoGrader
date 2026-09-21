"""Structured JSON logging with mandatory redaction (RQ-B-4-99).

Every operational record is one JSON line carrying `producer=B4` plus the correlation
identifiers, so MAIN and the other modules can stitch a request together. Redaction is
applied inside the formatter, not at the call sites: a new log statement cannot opt out
of it by accident, which is the only way a "no credentials in logs" rule survives
contact with future code.

Redacted: passwords, access tokens, refresh credentials, cookie values, private keys,
connection strings and out-of-scope PII (e-mail, display name, student number).
Allowed: UUID identifiers, request/correlation identifiers, error categories.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

PRODUCER = "B4"

#: The namespace every B4 logger lives under (`get_logger` enforces the prefix).
MODULE_LOGGER_NAME = "autograder_b4"

REDACTED = "[REDACTED]"

#: Field names whose value is a secret or out-of-scope personal data.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "refresh_credential",
        "id_token",
        "authorization",
        "cookie",
        "set-cookie",
        "set_cookie",
        "private_key",
        "jwt",
        "dsn",
        "database_url",
        "admin_dsn",
        "sqlalchemy_url",
        "api_key",
        "client_secret",
        "email",
        "display_name",
        "student_number",
        "name",
        "filename",
    }
)

_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*")
_URL_CREDENTIALS_PATTERN = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^:/@\s]+:[^@\s]+@")

_request_id: ContextVar[str | None] = ContextVar("b4_request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("b4_correlation_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("b4_user_id", default=None)

STANDARD_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def bind_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    user_id: str | None = None,
) -> tuple[Any, Any, Any]:
    """Bind correlation identifiers for the duration of one request.

    Returns the tokens needed by :func:`reset_context`; the caller resets them in a
    `finally` block so a worker reuse cannot leak another request's identity.
    """
    return (
        _request_id.set(request_id),
        _correlation_id.set(correlation_id),
        _user_id.set(user_id),
    )


def reset_context(tokens: tuple[Any, Any, Any]) -> None:
    request_token, correlation_token, user_token = tokens
    _request_id.reset(request_token)
    _correlation_id.reset(correlation_token)
    _user_id.reset(user_token)


def current_request_id() -> str | None:
    return _request_id.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def redact_value(value: Any) -> Any:
    """Recursively replace secrets and out-of-scope PII with a placeholder."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return value


def redact_text(text: str) -> str:
    """Redact secrets embedded in a free-form string (message bodies, error text)."""
    return _redact_text(text)


def _is_sensitive_key(key: str) -> bool:
    return key.strip().lower().replace("-", "_") in {
        item.replace("-", "_") for item in SENSITIVE_KEYS
    }


def _redact_text(text: str) -> str:
    text = _JWT_PATTERN.sub(REDACTED, text)
    text = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", text)
    text = _URL_CREDENTIALS_PATTERN.sub(rf"\g<scheme>{REDACTED}:{REDACTED}@", text)
    return text


class RedactingJsonFormatter(logging.Formatter):
    """One JSON object per record, redacted, with the correlation context attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "producer": PRODUCER,
        }
        request_id = _request_id.get()
        correlation_id = _correlation_id.get()
        user_id = _user_id.get()
        if request_id:
            payload["request_id"] = request_id
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if user_id:
            payload["user_id"] = user_id
        for key, value in record.__dict__.items():
            if key in STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = REDACTED if _is_sensitive_key(key) else redact_value(value)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Install the JSON formatter on the root logger exactly once."""
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    qualified = name if name.startswith(MODULE_LOGGER_NAME) else f"{MODULE_LOGGER_NAME}.{name}"
    return logging.getLogger(qualified)


@contextmanager
def structured_module_logging(level: int | str = logging.INFO) -> Iterator[None]:
    """Make B4's records JSON-formatted in a process whose logging B4 does not own.

    `configure_logging()` takes over the root logger, which is right in a process B4 fully
    controls. The Alembic CLI is not one: `alembic.ini` installs its own handlers, and
    replacing the root's would either silence Alembic's `Running upgrade ...` lines or print
    every record twice. Attaching the same redacting formatter to B4's namespace keeps
    operation records structured -- and redacted by the formatter rather than by the caller
    -- while Alembic keeps its console output.

    The handler is installed only for the duration of the block and propagation is restored
    afterwards, so a migration run in-process (a test, a script) cannot change how the
    surrounding application logs.
    """
    module_logger = logging.getLogger(MODULE_LOGGER_NAME)
    root = logging.getLogger()
    previous_level = module_logger.level
    module_logger.setLevel(level)
    if any(
        isinstance(handler.formatter, RedactingJsonFormatter) for handler in root.handlers
    ):
        # The process already formats B4's records (the host, or the test suite): a second
        # handler would emit each record twice.
        try:
            yield
        finally:
            module_logger.setLevel(previous_level)
        return
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    previous_propagate = module_logger.propagate
    module_logger.addHandler(handler)
    # Without this the root's own handler (Alembic's plain console handler) would print the
    # same record a second time, in a different format.
    module_logger.propagate = False
    try:
        yield
    finally:
        module_logger.removeHandler(handler)
        module_logger.propagate = previous_propagate
        module_logger.setLevel(previous_level)


def log_operation(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one structured operation record (`event` is the machine-readable name)."""
    logger.log(level, event, extra={"event": event, **fields})


TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def parse_traceparent(value: str | None) -> uuid.UUID | None:
    """Extract the W3C trace id as a UUID, or None when the header is absent/invalid.

    The trace id is also the `correlation_id` used by emitted events, which the
    envelope schema constrains to a UUID, so the 32 hex digits are hyphenated here
    rather than stored in their raw form.
    """
    if not value:
        return None
    match = TRACEPARENT_PATTERN.match(value.strip().lower())
    if match is None:
        return None
    if match.group("version") == "ff" or match.group("trace_id") == "0" * 32:
        return None
    return uuid.UUID(match.group("trace_id"))


def request_identifiers(
    traceparent: str | None, request_id_header: str | None
) -> tuple[str, uuid.UUID]:
    """Resolve `(request_id, correlation_id)` for one request, generating what is absent."""
    resolved_request = (request_id_header or "").strip() or str(uuid.uuid4())
    correlation = parse_traceparent(traceparent) or uuid.uuid4()
    return resolved_request, correlation
