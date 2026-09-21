"""Structured, redacted JSON logging (``RQ-B-3-99``).

Guarantees:

* every line is a single JSON object carrying ``producer="B3"``;
* ``request_id`` and (when known) ``question_version_id`` are present, so a
  failure is traceable across HTTP -> domain -> package compile -> event;
* values under a protected key - reference answers, hidden case expectations,
  fixture bodies, submitted source, credentials, SQL - are replaced by
  ``"[redacted]"`` **before** serialization, so a careless ``extra=`` cannot
  leak protected content.

Redaction is key-based rather than content-based: B3 never has to guess whether
a string "looks like" an answer. The rule is therefore auditable (the denylist
below) and is asserted by ``tests/test_logging.py``.

Use :func:`log_event` for business logging instead of ``logger.info("...")`` with
interpolated protected values: it takes structured fields and applies the same
redaction to them.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, UTC
from typing import Any
from collections.abc import Mapping, MutableMapping

PRODUCER = "B3"
REDACTED = "[redacted]"
MAX_STRING_CHARS = 512
MAX_SEQUENCE_ITEMS = 20

# Normalized (lowercase, non-alphanumeric stripped) key fragments whose values
# must never reach a log line.
PROTECTED_LOG_KEY_FRAGMENTS: tuple[str, ...] = (
    "referenceanswer",
    "referenceanswers",
    "answerkey",
    "hiddencase",
    "hiddencases",
    "expectedoutput",
    "expectedoutputs",
    "expectedstdout",
    "expectedstderr",
    "expectedexit",
    "fixturecontent",
    "fixtures",
    "fixture",
    "sourcecode",
    "source",
    "code",
    "payload",
    "body",
    "content",
    "raw",
    "bytes",
    "studenti",
    "studentid",
    "studentname",
    "studentnumber",
    "email",
    "token",
    "accesstoken",
    "authorization",
    "cookie",
    "password",
    "secret",
    "credential",
    "apikey",
    "privatekey",
    "sql",
    "statement",
    "stack",
    "traceback",
)


def _normalize(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def is_protected_key(key: str) -> bool:
    normalized = _normalize(key)
    return any(fragment in normalized for fragment in PROTECTED_LOG_KEY_FRAGMENTS)


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace protected content and clamp oversized values."""
    if _depth > 6:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if is_protected_key(str(key)) else redact_value(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        truncated = len(items) > MAX_SEQUENCE_ITEMS
        rendered = [redact_value(item, _depth=_depth + 1) for item in items[:MAX_SEQUENCE_ITEMS]]
        if truncated:
            rendered.append(f"...({len(items)} items)")
        return rendered
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return value[:MAX_STRING_CHARS] + "...(truncated)"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return to_rfc3339_millis(value)
    return str(value)


def to_rfc3339_millis(moment: datetime) -> str:
    """UTC RFC 3339 with millisecond precision (MAIN/docs/api/identifiers.md)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


# Keys the formatter lifts to the top level of the JSON object as-is.
CONTEXT_KEYS: tuple[str, ...] = (
    "request_id",
    "correlation_id",
    "trace_id",
    "module",
    "event",
    "event_id",
    "evaluation_run_id",
    "question_id",
    "question_version_id",
    "assignment_id",
    "course_id",
    "actor_id",
    "actor_role",
    "status_code",
    "duration_ms",
    "outcome",
    "reason",
    "field",
    "adapter_kind",
    "package_digest",
    "content_digest",
    "attempt_count",
    "rejected_count",
)


class RedactionFilter(logging.Filter):
    """Scrubs protected keys from ``LogRecord`` extras before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib name
        for key, value in list(record.__dict__.items()):
            if key in _STD_RECORD_KEYS or key.startswith("_"):
                continue
            if is_protected_key(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = redact_value(value)
        return True


_STD_RECORD_KEYS = frozenset(
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
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line; ``producer`` is always ``B3``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": to_rfc3339_millis(datetime.fromtimestamp(record.created, tz=UTC)),
            "level": record.levelname,
            "producer": PRODUCER,
            "logger": record.name,
            "message": redact_log_message(record.getMessage()),
        }
        for key in CONTEXT_KEYS:
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        for key, value in record.__dict__.items():
            if key in payload or key in _STD_RECORD_KEYS or key.startswith("_"):
                continue
            if key in ("msg", "message", "asctime"):
                continue
            payload[key] = REDACTED if is_protected_key(key) else redact_value(value)
        if record.exc_info:
            # Exception *type* only: tracebacks carry internal paths and SQL.
            payload["error_type"] = getattr(record.exc_info[0], "__name__", "Exception")
            payload["error_message"] = redact_log_message(str(record.exc_info[1]))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def redact_log_message(message: str) -> str:
    if len(message) > MAX_STRING_CHARS * 4:
        return message[: MAX_STRING_CHARS * 4] + "...(truncated)"
    return message


def configure_logging(
    level: str = "INFO", *, stream: Any = None, force: bool = False
) -> logging.Logger:
    """Install the JSON handler on the ``autograder_b3`` logger tree.

    Idempotent by default; ``force=True`` replaces existing handlers, which is what
    tests use to capture output for one case without leaking between cases.
    """
    logger = logging.getLogger("autograder_b3")
    logger.setLevel(level.upper())
    logger.propagate = False
    if force:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(RedactionFilter())
        logger.addHandler(handler)
    return logger


def get_logger(name: str = "autograder_b3") -> logging.Logger:
    if name == "autograder_b3" or name.startswith("autograder_b3."):
        return logging.getLogger(name)
    return logging.getLogger(f"autograder_b3.{name}")


def log_event(
    logger: logging.Logger,
    event: str,
    /,
    *,
    level: int = logging.INFO,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Emit a structured business log line with redaction applied to ``fields``."""
    safe: MutableMapping[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        safe[key] = REDACTED if is_protected_key(key) else redact_value(value)
    logger.log(level, event, extra={"event": event, **safe}, exc_info=exc_info)
