"""Timestamp formatting and comparison at the precision the schema actually stores.

`docs/database/table-standards.md` §3 fixes timestamps at `timestamptz` with millisecond
precision, and the API contract serialises UTC RFC 3339 with milliseconds. Both the
round-trip format and the optimistic-concurrency comparison therefore have to agree with
that precision:

* formatting at a finer precision than the column holds produces values that never compare
  equal to what a client echoes back; and
* comparing at a *coarser* precision (whole seconds, say) makes two edits inside the same
  second indistinguishable, which defeats `expected_updated_at` entirely.

Keeping the two functions adjacent is what stops them drifting apart.
"""

from __future__ import annotations

from datetime import UTC, datetime

MILLISECOND = 1000


def rfc3339(moment: datetime) -> str:
    """UTC RFC 3339 with millisecond precision, e.g. `2026-09-15T08:30:00.000Z`."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_millis(moment: datetime) -> datetime:
    """Truncate to millisecond precision, matching what PostgreSQL stores."""
    aware = moment.astimezone(UTC)
    return aware.replace(microsecond=(aware.microsecond // MILLISECOND) * MILLISECOND)


def same_instant(left: datetime, right: datetime) -> bool:
    """True when two instants are the same at millisecond precision."""
    return to_millis(left) == to_millis(right)
