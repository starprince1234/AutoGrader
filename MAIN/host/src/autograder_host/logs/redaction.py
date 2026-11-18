"""Normalization and secondary whitelist redaction for the unified log center.

Guarantees:
1. Only whitelisted flat keys enter ``fields``; unknown keys are silently dropped.
2. UUID-typed top-level identifiers are strictly validated; invalid values are dropped.
3. Secret values, tokens, passwords, connection strings, source code, reference answers,
   hidden cases, fixture contents, and out-of-scope PII are replaced with ``[REDACTED]``
   or dropped.
4. Control characters are escaped to prevent log injection.
5. Serialized ``fields`` JSON is bounded to ``MAX_FIELDS_BYTES`` (2048 bytes).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .context import current_correlation_id, current_request_id, normalize_uuid

REDACTED = "[REDACTED]"
MAX_EVENT_LENGTH = 100
MAX_STAGE_LENGTH = 64
MAX_FIELD_STRING_LENGTH = 200
MAX_FIELDS_BYTES = 2048

VALID_PRODUCERS = frozenset({"B1", "B2", "B3", "B4", "B5", "MAIN"})
VALID_PROCESSES = frozenset({"host", "worker", "intake", "notifier", "browser"})

UUID_COLUMNS: tuple[str, ...] = (
    "request_id",
    "correlation_id",
    "course_id",
    "assignment_id",
    "submission_id",
    "evaluation_run_id",
    "question_version_id",
    "actor_id",
)

# Strict whitelist of metadata keys allowed inside ``fields`` JSONB.
ALLOWED_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "logger",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "elapsed_ms",
        "outcome",
        "result",
        "decision",
        "status",
        "state",
        "from_state",
        "to_state",
        "action",
        "target_type",
        "target_id",
        "section_id",
        "question_id",
        "io_type",
        "language",
        "execution_mode",
        "dependency",
        "error_type",
        "error_category",
        "reason",
        "reason_code",
        "detail",
        "message",
        "count",
        "total",
        "passed",
        "failed",
        "skipped",
        "attempts",
        "dropped_count",
        "deleted_info",
        "deleted_warn_error",
        "deleted_capacity",
        "storage_bytes",
        "row_count",
        "format",
        "consistent",
        "difference_count",
        "module_count",
        "db_sink_enabled",
        "redacted_fields",
        "redacted_forbidden_fields",
        "case_id",
        "rule_id",
        "check_id",
        "violation_count",
        "error_count",
        "package_digest",
        "content_digest",
        "adapter_kind",
        "size_bytes",
        "file_count",
    }
)

# Keys whose presence anywhere forces redaction (defense-in-depth).
FORBIDDEN_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "privatekey",
    "apikey",
    "credential",
    "dsn",
    "databaseurl",
    "sqlalchemyurl",
    "source",
    "code",
    "referenceanswer",
    "answerkey",
    "hiddencase",
    "expectedoutput",
    "expectedstdout",
    "expectedstderr",
    "fixture",
    "email",
    "studentname",
    "studentnumber",
    "displayname",
    "traceback",
    "stack",
)

FREE_TEXT_KEYS = frozenset({"detail", "message", "reason"})

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*")
_URL_CREDS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^:/@\s]+:[^@\s]+@")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SOURCE_SHAPE_RE = re.compile(
    r"(^\s*(def |class |import |from\s+\S+\s+import|#!/bin/)|\n\s{2,}\S|\breturn\s+\S+\s*;?)",
    re.MULTILINE,
)

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
        "taskName",
        "thread",
        "threadName",
    }
)


def _escape_control(text: str) -> str:
    return _CONTROL_RE.sub(
        lambda m: {
            "\n": "\\n",
            "\r": "\\r",
            "\t": "\\t",
        }.get(m.group(0), f"\\x{ord(m.group(0)):02x}"),
        text,
    )


def _normalize_placeholders(text: str) -> str:
    text = text.replace("[redacted]", REDACTED)
    text = text.replace("<redacted-credential>", REDACTED)
    return text


def sanitize_text(value: str, *, max_length: int = MAX_FIELD_STRING_LENGTH, check_source: bool = False) -> str:
    if check_source and _SOURCE_SHAPE_RE.search(value):
        return REDACTED
    cleaned = _normalize_placeholders(value)
    cleaned = _JWT_RE.sub(REDACTED, cleaned)
    cleaned = _BEARER_RE.sub(f"Bearer {REDACTED}", cleaned)
    cleaned = _URL_CREDS_RE.sub(rf"\g<scheme>{REDACTED}:{REDACTED}@", cleaned)
    cleaned = _EMAIL_RE.sub(REDACTED, cleaned)
    cleaned = _escape_control(cleaned)
    if len(cleaned) > max_length:
        suffix = "...[truncated]"
        keep = max(0, max_length - len(suffix))
        return cleaned[:keep] + suffix
    return cleaned


def _is_forbidden_key(key: str) -> bool:
    norm = "".join(ch for ch in key.lower() if ch.isalnum())
    return any(frag in norm for frag in FORBIDDEN_KEY_FRAGMENTS)


def rfc3339_millis(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_timestamp(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback or datetime.now(UTC)


def normalize_level(level: Any) -> str | None:
    """Map level to info/warn/error; return None for debug (never persisted)."""
    if isinstance(level, int):
        if level < logging.INFO:
            return None
        if level >= logging.ERROR:
            return "error"
        if level >= logging.WARNING:
            return "warn"
        return "info"
    text = str(level or "").strip().lower()
    if text == "debug":
        return None
    if text in {"warning", "warn"}:
        return "warn"
    if text in {"error", "critical", "fatal"}:
        return "error"
    if text == "info":
        return "info"
    return "info"


def infer_producer(raw_producer: Any, logger_name: str | None) -> str:
    if isinstance(raw_producer, str) and raw_producer.upper() in VALID_PRODUCERS:
        return raw_producer.upper()
    name = (logger_name or "").lower()
    if "autograder_b2" in name:
        return "B2"
    if "autograder_b3" in name:
        return "B3"
    if "autograder_b4" in name:
        return "B4"
    if "autograder_b5" in name:
        return "B5"
    if "autograder_b1" in name:
        return "B1"
    return "MAIN"


def _sanitize_field_value(key: str, value: Any) -> tuple[Any, bool]:
    """Return (sanitized_scalar_or_none, was_truncated)."""
    if value is None:
        return None, False
    if _is_forbidden_key(key):
        return REDACTED, False
    if isinstance(value, bool):
        return value, False
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return None, False
        return value, False
    if isinstance(value, str):
        sanitized = sanitize_text(
            value,
            max_length=MAX_FIELD_STRING_LENGTH,
            check_source=(key in FREE_TEXT_KEYS or key == "detail"),
        )
        return sanitized, sanitized.endswith("...[truncated]")
    # Nested dicts/lists are dropped in flat whitelist fields (except small flat lists of primitives).
    if isinstance(value, (list, tuple)):
        out = []
        for item in list(value)[:10]:
            if isinstance(item, (str, int, float, bool)):
                if isinstance(item, str):
                    out.append(sanitize_text(item, max_length=64, check_source=True))
                else:
                    out.append(item)
        return out, len(value) > 10
    return None, False


def _fit_fields(fields: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    truncated = False
    cleaned = dict(fields)
    encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= MAX_FIELDS_BYTES:
        return cleaned, False

    truncated = True
    # Drop free-text fields first, then largest values.
    for key in list(FREE_TEXT_KEYS):
        if key in cleaned:
            cleaned.pop(key, None)
            encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) <= MAX_FIELDS_BYTES:
                return cleaned, True

    while len(cleaned) > 0:
        encoded = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= MAX_FIELDS_BYTES:
            break
        largest = max(cleaned.keys(), key=lambda k: len(str(cleaned[k])))
        cleaned.pop(largest, None)
    return cleaned, truncated


def normalize_raw_dict(
    raw: Mapping[str, Any],
    *,
    process: str,
    fallback_producer: str = "MAIN",
    fallback_time: datetime | None = None,
) -> dict[str, Any] | None:
    """Normalize a raw dict (from JSON log line, client payload, or LogRecord) into unified envelope."""
    level = normalize_level(raw.get("level"))
    if level is None:
        return None

    proc = str(raw.get("process") or process).strip().lower()
    if proc not in VALID_PROCESSES:
        proc = process if process in VALID_PROCESSES else "host"

    occurred_at = parse_timestamp(raw.get("timestamp"), fallback=fallback_time)
    logger_name = raw.get("logger") if isinstance(raw.get("logger"), str) else None
    producer = infer_producer(raw.get("producer") or fallback_producer, logger_name)

    raw_event = raw.get("event") or raw.get("message") or "unstructured_log_record"
    event = sanitize_text(str(raw_event), max_length=MAX_EVENT_LENGTH, check_source=False) or "unstructured_log_record"

    envelope: dict[str, Any] = {
        "occurred_at": occurred_at,
        "timestamp": rfc3339_millis(occurred_at),
        "level": level,
        "event": event,
        "producer": producer,
        "process": proc,
    }

    # Map user_id -> actor_id when actor_id is absent.
    candidate_map = dict(raw)
    if "actor_id" not in candidate_map and "user_id" in candidate_map:
        candidate_map["actor_id"] = candidate_map["user_id"]
    if "request_id" not in candidate_map or not candidate_map.get("request_id"):
        ctx_req = current_request_id()
        if ctx_req:
            candidate_map["request_id"] = ctx_req
    if "correlation_id" not in candidate_map or not candidate_map.get("correlation_id"):
        ctx_corr = current_correlation_id()
        if ctx_corr:
            candidate_map["correlation_id"] = ctx_corr

    for col in UUID_COLUMNS:
        uid = normalize_uuid(candidate_map.get(col))
        if uid is not None:
            envelope[col] = uid

    stage = raw.get("stage")
    if isinstance(stage, str) and stage.strip():
        envelope["stage"] = sanitize_text(stage.strip(), max_length=MAX_STAGE_LENGTH)

    fields: dict[str, Any] = {}
    truncated = bool(raw.get("truncated", False))
    for key, val in raw.items():
        if key in ALLOWED_FIELD_KEYS and key not in envelope:
            sanitized, item_trunc = _sanitize_field_value(key, val)
            if sanitized is not None:
                fields[key] = sanitized
            if item_trunc:
                truncated = True

    fields, cap_trunc = _fit_fields(fields)
    if cap_trunc:
        truncated = True
    envelope["fields"] = fields
    envelope["truncated"] = truncated
    return envelope


def normalize_log_record(record: logging.LogRecord, *, process: str) -> dict[str, Any] | None:
    """Convert a Python ``logging.LogRecord`` into a normalized unified log dict."""
    level = normalize_level(record.levelno)
    if level is None:
        return None

    raw: dict[str, Any] = {}
    msg_text = record.getMessage()
    parsed_json: dict[str, Any] | None = None
    if msg_text.startswith("{") and msg_text.endswith("}"):
        try:
            loaded = json.loads(msg_text)
            if isinstance(loaded, dict):
                parsed_json = loaded
        except ValueError:
            parsed_json = None

    if parsed_json is not None:
        raw.update(parsed_json)
    else:
        raw["message"] = msg_text

    # Merge structured extras from B2/B3/B4/B5 conventions.
    b2_event = record.__dict__.get("b2_event") or record.__dict__.get("b2_security_event")
    if isinstance(b2_event, Mapping):
        for k, v in b2_event.items():
            raw.setdefault(str(k), v)

    for key, value in record.__dict__.items():
        if key in _STD_RECORD_KEYS or key.startswith("_") or key in {"b2_event", "b2_security_event"}:
            continue
        raw.setdefault(key, value)

    raw.setdefault("level", level)
    raw.setdefault("logger", record.name)
    if "event" not in raw:
        raw["event"] = record.__dict__.get("event") or (
            msg_text[:MAX_EVENT_LENGTH] if parsed_json is None and msg_text and " " not in msg_text else "unstructured_log_record"
        )

    fallback_time = datetime.fromtimestamp(record.created, tz=UTC)
    fallback_producer = infer_producer(raw.get("producer"), record.name)
    return normalize_raw_dict(
        raw,
        process=process,
        fallback_producer=fallback_producer,
        fallback_time=fallback_time,
    )
