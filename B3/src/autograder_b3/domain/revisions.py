"""Structural delta between two versions of a question (``RQ-B-3-20`` acceptance 2).

``versions_per_question`` was 1.00 across the whole bank: the immutability design had
never been exercised on real data, so "a change produces a new version" was a rule
nobody could observe. The revision loop fixes the *observability* half: when a draft is
branched from a published version, B3 records what actually differs and why.

The hard constraint is that this must never become an outbound path for protected
content. Every value below is a count, a digest or a presence flag - never stem text,
never a reference answer, never an expected output. Stem and answer changes are
reported as a length or a digest, which is enough to explain a revision and useless for
reconstructing one.

The key set is exactly ``RevisionDiff`` in ``MAIN/contracts/openapi/b3.yaml``
(CHG-0013), and the stored column **is** that shape: the same structure is persisted in
``b3.question_versions.revision_diff``, read back for the teacher-facing note and
serialized onto ``QuestionVersionPublic``. A second, richer internal shape would have
been a second truth, and the projection between the two would have been the place where
protected content eventually slipped through.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

from ..compile.canonical import digest_of
from ..dto.internal import (
    AdapterManifestInternal,
    FixtureInternal,
    ReferenceAnswerInternal,
    TestCaseInternal,
)

#: The declared key set (``RevisionDiff``, ``additionalProperties: false``). Asserted by
#: ``tests/test_versions.py`` against the contract file itself, so this constant cannot
#: drift away from what a client is promised.
REVISION_DIFF_KEYS: frozenset[str] = frozenset(
    {
        "case_count_before",
        "case_count_after",
        "case_set_changed",
        "weights_changed",
        "fixtures_changed",
        "stem_length_before",
        "stem_length_after",
        "adapter_kind_changed",
        "size_limits_changed",
        "reference_answer_digest_before",
        "reference_answer_digest_after",
    }
)

#: The two size limits are one comparison, not two: moving a byte budget from
#: ``max_files`` to ``max_total_bytes`` is a change either way.
SIZE_LIMIT_FIELDS: tuple[str, ...] = ("max_files", "max_total_bytes")


def snapshot_content(
    *,
    language: str,
    manifest: AdapterManifestInternal,
    cases: Sequence[TestCaseInternal],
    fixtures: Sequence[FixtureInternal],
    reference_answers: Sequence[ReferenceAnswerInternal],
    stem_markdown: str,
    max_files: int,
    max_total_bytes: int,
) -> dict[str, Any]:
    """The diffable facts about one version's content.

    Built from a version's stored content so a diff can be computed without holding the
    content itself: what survives into the delta is a count, a length or a digest.
    """
    return {
        "language": language,
        "adapter_kind": manifest.kind,
        # ``manifest_to_storage`` output is what the adapter compilers consume, and it is
        # JSON-native; comparing the validated manifest object directly would compare
        # dataclasses whose fields are themselves dataclasses.
        "adapter": _adapter_wire(manifest),
        "case_count": len(cases),
        "case_ordinals": sorted(case.ordinal for case in cases),
        "case_weights": {case.ordinal: case.weight for case in cases},
        "fixture_digests": sorted(
            (fixture.path, fixture.mount, fixture.content_sha256) for fixture in fixtures
        ),
        "reference_answer_digest": reference_answer_digest(reference_answers),
        "stem_length": len(stem_markdown),
        "max_files": max_files,
        "max_total_bytes": max_total_bytes,
    }


def reference_answer_digest(reference_answers: Sequence[ReferenceAnswerInternal]) -> str | None:
    """Digest of the reference answers' shape and body, or ``None`` when there are none.

    The body is hashed, never stored: the digest is what lets a reader see that the
    reference answer changed without the answer leaving the protected store. An empty
    set is ``None`` rather than the digest of an empty list, so "no answer" and "an
    answer that hashes to X" stay distinguishable.
    """
    if not reference_answers:
        return None
    return digest_of(
        [
            {"language": answer.language, "body": answer.body}
            for answer in sorted(reference_answers, key=lambda item: item.language)
        ]
    )


def compute_revision_diff(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe what changed between two :func:`snapshot_content` results.

    ``previous`` is ``None`` for a version that supersedes nothing: every ``*_before``
    value is then ``null`` and every flag is ``null`` too, because "unchanged" and "not
    comparable" are different statements and a first version can only make the second.
    """
    previous = previous or {}
    return {
        **_counter("case_count", previous, current),
        **_counter("stem_length", previous, current),
        **_presence("case_set_changed", "case_ordinals", previous, current),
        **_presence("weights_changed", "case_weights", previous, current),
        **_presence("fixtures_changed", "fixture_digests", previous, current),
        **_presence("adapter_kind_changed", "adapter_kind", previous, current),
        **_presence("size_limits_changed", SIZE_LIMIT_FIELDS, previous, current),
        **_answer_digests(previous, current),
    }


def revision_note(diff: Mapping[str, Any] | None, supersedes: Any | None) -> str | None:
    """A readable "why this version exists", derived from the delta itself.

    Used when the author did not record a reason of their own. Deriving it from the
    stored delta means it cannot disagree with what the delta says, and it is capped at
    the column's 500 characters.
    """
    if diff is None or supersedes is None:
        return None
    changes: list[str] = []
    if diff["stem_length_before"] != diff["stem_length_after"]:
        changes.append("stem")
    if diff["case_count_before"] != diff["case_count_after"]:
        changes.append("test case count")
    if diff["case_set_changed"]:
        changes.append("test case set")
    if diff["weights_changed"]:
        changes.append("case weights")
    if diff["fixtures_changed"]:
        changes.append("fixtures")
    if diff["reference_answer_digest_before"] != diff["reference_answer_digest_after"]:
        changes.append("reference answer")
    if diff["adapter_kind_changed"]:
        changes.append("adapter kind")
    if diff["size_limits_changed"]:
        changes.append("size limits")
    if not changes:
        changes.append("no content change was recorded")
    return (f"supersedes {supersedes}: " + ", ".join(changes))[:500]


def _adapter_wire(manifest: AdapterManifestInternal) -> dict[str, Any]:
    from ..compile.manifest import manifest_to_storage

    return manifest_to_storage(manifest)


def _counter(name: str, previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """``<name>_before`` / ``<name>_after`` for a counted value.

    There is no ``*_changed`` key for a count in the contract: a reader compares the two
    numbers. ``before`` is ``null`` on a first version, which is how "not comparable"
    stays distinguishable from "equal".
    """
    return {
        f"{name}_before": previous.get(name),
        f"{name}_after": current[name],
    }


def _presence(
    name: str, key: str | tuple[str, ...], previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, bool | None]:
    """``<name>`` for a fact that is either the same or different - or not comparable."""
    if not previous:
        return {name: None}
    keys = (key,) if isinstance(key, str) else key
    return {name: tuple(previous.get(item) for item in keys) != tuple(current[item] for item in keys)}


def _answer_digests(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """The reference-answer pair. There is no ``*_changed`` key for it in the contract:
    a reader compares the two digests, which are the only form the answer takes here."""
    return {
        "reference_answer_digest_before": previous.get("reference_answer_digest"),
        "reference_answer_digest_after": current["reference_answer_digest"],
    }
