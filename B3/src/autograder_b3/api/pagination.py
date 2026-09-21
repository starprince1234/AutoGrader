"""Keyset pagination.

Offset pagination is forbidden for large tables (``openapi/common.yaml`` ->
``CursorPage``), so the cursor is an opaque keyset over ``(created_at, id)``.
A malformed cursor is a ``422``: it is caller-supplied input and B3 never guesses.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, UTC

from ..errors import UnprocessableEntity

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def encode_cursor(created_at: datetime, identifier: uuid.UUID) -> str:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    payload = json.dumps(
        {"c": created_at.astimezone(UTC).isoformat(), "i": str(identifier)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw)
        created_at = datetime.fromisoformat(str(payload["c"]))
        identifier = uuid.UUID(str(payload["i"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise UnprocessableEntity("the pagination cursor is not valid") from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(UTC), identifier


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise UnprocessableEntity("limit must be >= 1")
    return min(limit, MAX_LIMIT)
