"""Host-level request and correlation context for cross-module log stitching."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("autograder_host_request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("autograder_host_correlation_id", default=None)


def normalize_uuid(value: object) -> str | None:
    """Return canonical lowercase hyphenated UUID string if valid, else None."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def bind_request_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> tuple[object, object]:
    return (
        _request_id.set(normalize_uuid(request_id)),
        _correlation_id.set(normalize_uuid(correlation_id)),
    )


def reset_request_context(tokens: tuple[object, object]) -> None:
    req_tok, corr_tok = tokens
    _request_id.reset(req_tok)  # type: ignore[arg-type]
    _correlation_id.reset(corr_tok)  # type: ignore[arg-type]


def current_request_id() -> str | None:
    return _request_id.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()
