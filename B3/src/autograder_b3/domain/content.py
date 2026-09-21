"""Version content: canonical snapshot, content digest and protected-content writes.

``content_digest`` is the digest of a version's **entire** content - stem,
reference answers, adapter manifest, hidden cases and fixture digests. It is
computed at publish time and frozen (ADR-0001). Only the digest leaves the
protected store; the content it covers never does.
"""

from __future__ import annotations

import uuid
from typing import Any
from collections.abc import Sequence

from ..compile.canonical import digest_of
from ..compile.manifest import manifest_to_storage
from ..dto.internal import (
    AdapterManifestInternal,
    ReferenceAnswerInternal,
    TestCaseInternal,
    VersionContentSnapshot,
)
from ..errors import StemInvalid

STEM_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "<script",
    "<iframe",
    "<object",
    "<embed",
    "<style",
    "javascript:",
)


def validate_stem_markdown(stem: str, *, max_chars: int) -> str:
    """Reject an empty stem or one carrying executable markup.

    The contract puts safe rendering on the client ("embedded scripts and inline
    handlers are rejected by the client"); this is the server-side half of that
    promise, so a stored stem can never be an XSS payload for a different client.
    """
    if not isinstance(stem, str) or not stem.strip():
        raise StemInvalid("the question stem must not be empty")
    if len(stem) > max_chars:
        raise StemInvalid(f"the question stem exceeds {max_chars} characters")
    lowered = stem.lower()
    for pattern in STEM_FORBIDDEN_PATTERNS:
        if pattern in lowered:
            raise StemInvalid(
                "the question stem must be a safe rich-text subset; "
                f"embedded '{pattern.strip('<>')}' is not allowed"
            )
    return stem


def content_wire(snapshot: VersionContentSnapshot) -> dict[str, Any]:
    """Canonical, JSON-ready representation whose digest is ``content_digest``."""
    return {
        "question_version_id": str(snapshot.question_version_id),
        "question_id": str(snapshot.question_id),
        "version": snapshot.version,
        "language": snapshot.language,
        "stem_markdown": snapshot.stem_markdown,
        "reference_answers": [
            {"language": answer.language, "body": answer.body}
            for answer in sorted(snapshot.reference_answers, key=lambda item: item.language)
        ],
        "adapter": manifest_to_storage(snapshot.manifest),
        "cases": [
            {
                "case_id": str(case.case_id),
                "name": case.name,
                "ordinal": case.ordinal,
                "weight": case.weight,
                "visible_to_student": case.visible_to_student,
                "stdin": case.stdin,
                "args": list(case.args),
                "expected_stdout": case.expected_stdout,
                "expected_stderr": case.expected_stderr,
                "expected_exit_code": case.expected_exit_code,
            }
            for case in sorted(snapshot.cases, key=lambda item: item.ordinal)
        ],
        "fixtures": [
            {
                "path": fixture.path,
                "mount": fixture.mount,
                "content_sha256": fixture.content_sha256,
                "size_bytes": fixture.size_bytes,
            }
            for fixture in sorted(snapshot.fixtures, key=lambda item: (item.path, item.mount))
        ],
        "max_files": snapshot.max_files,
        "max_total_bytes": snapshot.max_total_bytes,
    }


def compute_content_digest(snapshot: VersionContentSnapshot) -> str:
    return digest_of(content_wire(snapshot))


def build_snapshot(
    *,
    question_version_id: uuid.UUID,
    question_id: uuid.UUID,
    version: int,
    language: str,
    stem_markdown: str,
    reference_answers: Sequence[ReferenceAnswerInternal],
    manifest: AdapterManifestInternal,
    cases: Sequence[TestCaseInternal],
    fixtures: Sequence[Any],
    max_files: int,
    max_total_bytes: int,
) -> VersionContentSnapshot:
    return VersionContentSnapshot(
        question_version_id=question_version_id,
        question_id=question_id,
        version=version,
        language=language,  # type: ignore[arg-type]
        stem_markdown=stem_markdown,
        reference_answers=tuple(reference_answers),
        manifest=manifest,
        cases=tuple(cases),
        fixtures=tuple(fixtures),
        max_files=max_files,
        max_total_bytes=max_total_bytes,
    )
