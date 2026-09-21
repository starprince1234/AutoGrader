"""Canonical JSON and digests (ADR-0005 §3).

Rule (frozen): ``json.dumps(value, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)`` encoded as UTF-8.

B2 does **not** reimplement this rule: it reads `package_digest` from the package B3 compiles and
never recomputes it, so there is no second implementation to keep in step (this paragraph used to
claim otherwise, which was wrong).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from collections.abc import Mapping, Sequence

#: Keys excluded from a package digest.
#:
#: ``package_digest`` because a digest cannot cover itself, and ``created_at`` because it is
#: **metadata, not content** -- the contract's own words for this field are "canonical digest of
#: the package content" (`evaluation-package.schema.json`), and B2's `result_digest` already keeps
#: an explicit volatile-field list (`VOLATILE_REPORT_FIELDS`) that excludes its timestamp. One
#: fact, one treatment.
#:
#: **Do not read this as the explanation of any past invalidation.** It was briefly believed that
#: including `created_at` broke every published version when migration `0004` narrowed the column
#: to `timestamptz(3)`; measurement disproved it -- `to_wire()` renders timestamps to
#: milliseconds, so a sub-millisecond difference never reaches the digest. The versions that
#: stopped matching had a different cause: a code change between publish times altered the
#: compiled output, so recompiling older versions no longer reproduced their recorded digest.
#: See `docs/phases/active/phase-06-package-digest-scope.md` §2–§3.
DIGEST_EXCLUDED_KEYS: tuple[str, ...] = ("package_digest", "created_at")


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic UTF-8 encoding of ``value``."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_of(value: Any) -> str:
    """Canonical digest of an arbitrary JSON-able value."""
    return sha256_hex(canonical_json_bytes(value))


def digest_without(
    value: Mapping[str, Any], excluded_keys: Sequence[str] = DIGEST_EXCLUDED_KEYS
) -> str:
    """Digest of ``value`` with the metadata keys removed (used for ``package_digest``)."""
    pruned = {key: item for key, item in value.items() if key not in excluded_keys}
    return digest_of(pruned)
