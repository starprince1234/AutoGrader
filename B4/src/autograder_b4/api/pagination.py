"""Cursor pagination.

`MAIN/contracts/openapi/common.yaml` declares a `CursorPage` envelope and states that
offset pagination is forbidden for large tables: an offset shifts under concurrent
writes and silently skips or repeats rows. The cursor here is the sort key of the last
row returned -- `(created_at, id)` -- so the next page continues from a stable position
regardless of what was inserted meanwhile.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, or_

from ..errors import bad_request

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.astimezone(UTC).isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")


def decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp, _, identifier = raw.partition("|")
        return datetime.fromisoformat(timestamp), uuid.UUID(identifier)
    except (binascii.Error, ValueError, UnicodeDecodeError) as error:
        # A malformed cursor is a client error, not a server error, and it must not be
        # silently ignored: ignoring it would return page one and loop the caller.
        raise bad_request("The pagination cursor is not valid.") from error


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1 or limit > MAX_LIMIT:
        raise bad_request(f"limit must be between 1 and {MAX_LIMIT}.")
    return limit


def descending_page_filter(
    created_at_column: ColumnElement, id_column: ColumnElement, cursor: str | None
) -> ColumnElement | None:
    """`(created_at, id) < cursor` for a `created_at DESC, id DESC` ordering."""
    if not cursor:
        return None
    timestamp, identifier = decode_cursor(cursor)
    return or_(
        created_at_column < timestamp,
        and_(created_at_column == timestamp, id_column < identifier),
    )


def page_envelope(
    items: list[dict],
    keys: list[tuple[datetime, uuid.UUID]],
    limit: int,
) -> dict[str, object]:
    """Build the `CursorPage` envelope; `next_cursor` is absent when the page is short.

    `keys[i]` must be the `(created_at, id)` sort key of `items[i]`; passing them
    explicitly keeps the cursor tied to the actual ordering rather than to a field the
    document happens to expose.
    """
    if len(items) != len(keys):  # pragma: no cover - internal invariant
        raise ValueError("items and keys must be the same length")
    next_cursor = None
    if items and len(items) == limit:
        last_created_at, last_id = keys[-1]
        next_cursor = encode_cursor(last_created_at, last_id)
    return {"items": items, "next_cursor": next_cursor}
