"""Topic tags: the normalised form, and nothing more (``RQ-B-3-20``).

The contract fixes the shape -- lowercase, hyphen-separated, at most 40 characters,
at most 20 per question -- and stops there. This module therefore implements **the
pattern and the count**, not a vocabulary: there is no controlled list of allowed
tags, no synonym mapping and no auto-correction, because inventing one would silently
reject legitimate topic names ("进程调度", "text-tools") that the contract permits and
that the bank's own file grouping already uses.

Invalid input is rejected with ``422`` rather than rewritten: a tag the caller did not
ask for would make every tag-derived view quietly wrong, and the caller would have no
way to notice.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..errors import TagInvalid
from ..infra.models import MAX_TAGS, TAG_PATTERN

_TAG_RE = re.compile(TAG_PATTERN)
MAX_TAG_CHARS = 40


def validate_tags(raw: object) -> tuple[str, ...]:
    """Validate and normalise a submitted tag list.

    Accepts ``None`` or an empty sequence as "no tags". Every tag must already be in
    the declared form; order is preserved (it is what the author wrote) and duplicates
    are refused rather than collapsed, because the contract declares ``uniqueItems``.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        raise TagInvalid("`tags` must be an array of strings, not a single string", field="tags")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        raise TagInvalid("`tags` must be an array of strings", field="tags")

    tags = list(raw)
    if len(tags) > MAX_TAGS:
        raise TagInvalid(f"`tags` accepts at most {MAX_TAGS} entries", field="tags")

    seen: set[str] = set()
    validated: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            raise TagInvalid("every tag must be a string", field="tags")
        if not tag or len(tag) > MAX_TAG_CHARS or not _TAG_RE.match(tag):
            raise TagInvalid(
                f"tag {tag!r} is not in the declared form "
                f"(lowercase-with-hyphens, 1-{MAX_TAG_CHARS} characters, "
                "starting with a letter or digit)",
                field="tags",
            )
        if tag in seen:
            raise TagInvalid(f"tag {tag!r} is listed twice", field="tags")
        seen.add(tag)
        validated.append(tag)
    return tuple(validated)


def tags_from_storage(raw: object) -> tuple[str, ...]:
    """Read tags back out of ``jsonb``, refusing anything the validator never wrote.

    Stored tags are validated on write and constrained in the database, so a malformed
    value here means the row was changed behind the application's back; it is reported
    as a defect (a loud ``500``) instead of being passed on to a public response.
    """
    if raw is None:
        return ()
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        raise TagInvalid("stored tags are not an array", field="tags")
    return validate_tags(list(raw))
