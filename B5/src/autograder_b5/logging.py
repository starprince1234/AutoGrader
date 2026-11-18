"""Structured JSON logging for B5 (``producer="B5"``)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

PRODUCER = "B5"
REDACTED = "[REDACTED]"
_PROTECTED_FRAGMENTS = (
    "token",
    "authorization",
    "password",
    "secret",
    "credential",
    "cookie",
    "dsn",
)
_STD_KEYS = frozenset(
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


def _is_protected(key: str) -> bool:
    norm = "".join(ch for ch in key.lower() if ch.isalnum())
    return any(frag in norm for frag in _PROTECTED_FRAGMENTS)


def _rfc3339_millis(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class JsonFormatter(logging.Formatter):
    """Render each B5 log record as a single JSON line with ``producer="B5"``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _rfc3339_millis(datetime.fromtimestamp(record.created, tz=UTC)),
            "level": record.levelname,
            "event": record.__dict__.get("event") or record.getMessage(),
            "producer": PRODUCER,
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key in _STD_KEYS or key in payload or key.startswith("_"):
                continue
            payload[key] = REDACTED if _is_protected(key) else value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO", *, stream: Any = None, force: bool = False) -> logging.Logger:
    logger = logging.getLogger("autograder_b5")
    logger.setLevel(level.upper())
    logger.propagate = False
    if force:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


def get_logger(name: str = "autograder_b5") -> logging.Logger:
    if name == "autograder_b5" or name.startswith("autograder_b5."):
        return logging.getLogger(name)
    return logging.getLogger(f"autograder_b5.{name}")
