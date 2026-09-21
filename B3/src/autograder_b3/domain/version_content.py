"""Version content -> digest input, shared by publish and attestation.

Split out of ``domain/versions.py`` because both the publish gate and the attestation
recorder must hash a version's content, and a second digest implementation would be a
second truth. Keeping it here also keeps the import graph acyclic: ``attestations``
depends on this module, and ``versions`` depends on both.

The digest covers the **entire** content - stem, reference answers, adapter manifest,
hidden cases and fixture digests. It is recomputed here, never accepted from a caller.
"""

from __future__ import annotations

from datetime import datetime

from ..compile.manifest import manifest_from_stored
from ..domain.content import build_snapshot, compute_content_digest
from ..dto.internal import PublishedVersionSnapshot, VersionContentSnapshot
from ..errors import ProtectedContentMissing
from ..infra.models import QuestionVersion
from ..infra.repositories import ProtectedContentRepository


class VersionContent:
    """A version's canonical content plus the compile-input view of it."""

    __slots__ = ("snapshot",)

    def __init__(self, snapshot: VersionContentSnapshot) -> None:
        self.snapshot = snapshot

    @property
    def content_digest(self) -> str:
        return compute_content_digest(self.snapshot)

    @property
    def manifest(self):
        return self.snapshot.manifest

    @property
    def cases(self):
        return self.snapshot.cases

    @property
    def fixtures(self):
        return self.snapshot.fixtures

    def snapshot_for(self, *, created_at: datetime) -> PublishedVersionSnapshot:
        """Compile-input snapshot carrying an explicit package ``created_at``.

        The field it fills is called ``published_at`` because the published path is where it
        was introduced (ADR-0001). ``CHG-0028`` made drafts compile too, and there the value is
        the version content's own timestamp rather than a publication date, so the *parameter*
        is named for what it means. The field keeps its name deliberately:
        ``PublishedVersionSnapshot`` is on the frozen internal-DTO list
        (``docs/collaboration/frozen-interfaces.md``), so renaming it is an ADR-level change,
        not a drive-by edit.
        """
        snapshot = self.snapshot
        return PublishedVersionSnapshot(
            question_version_id=snapshot.question_version_id,
            question_id=snapshot.question_id,
            version=snapshot.version,
            language=snapshot.language,
            content_digest=compute_content_digest(snapshot),
            published_at=created_at,
            max_files=snapshot.max_files,
            max_total_bytes=snapshot.max_total_bytes,
            manifest=snapshot.manifest,
        )


def load_version_content(
    session, version: QuestionVersion, *, require_reference_answer: bool = True
) -> VersionContent:
    """Read a version's protected content and build its digest input.

    Raises :class:`ProtectedContentMissing` when the version is not publishable, so a digest
    is never computed over content that could not be published anyway.

    ``require_reference_answer=False`` is for the **package** path (``CHG-0028``). A package is
    the *executable* input -- cases, adapter, comparison policy, sandbox policy -- and it never
    carries the reference answer, which travels on its own endpoint (``CHG-0023``) with its own
    ``409`` for "this version has no answer". Demanding the answer here would report "no
    package" for a state whose real problem is "no answer", and would make one delivery depend
    on the other. The paths that genuinely must have the answer -- ``publish`` and the
    attestation recorder -- keep the default.
    """
    protected = ProtectedContentRepository(session)
    cases = protected.list_cases(version.question_version_id)
    fixtures = protected.list_fixtures(version.question_version_id)
    reference_answers = protected.list_reference_answers(version.question_version_id)
    manifest = manifest_from_stored(
        version.adapter_manifest, cases=cases, fixtures=fixtures
    )

    if not cases:
        raise ProtectedContentMissing("a publishable version needs at least one test case")
    if require_reference_answer and not any(
        answer.body.strip() for answer in reference_answers
    ):
        raise ProtectedContentMissing(
            "a publishable version needs a reference answer for its language"
        )

    return VersionContent(
        build_snapshot(
            question_version_id=version.question_version_id,
            question_id=version.question_id,
            version=version.version,
            language=version.language,
            stem_markdown=version.stem_markdown,
            reference_answers=reference_answers,
            manifest=manifest,
            cases=cases,
            fixtures=fixtures,
            max_files=version.max_files,
            max_total_bytes=version.max_total_bytes,
        )
    )


def refresh_content_digest(session, version: QuestionVersion) -> str | None:
    """Persist the digest of a **draft** version's current content.

    Two requirements meet here, and neither works without the other:

    * an external verifier must be able to learn *which* digest to attest to, and the
      digest covers content it cannot see (hidden cases, their owner-generated ids), so
      it cannot compute the value itself -- ``QuestionVersionPublic.content_digest`` is
      the only place it can read it;
    * ``publish`` refuses content whose digest has no attestation, which a verifier can
      only satisfy if the digest it was shown is the same one publish recomputes.

    A published version is never touched: its content is frozen, so the digest it
    already carries is still correct, and the immutability trigger would refuse the
    write anyway. Returns the digest, or ``None`` when the draft is not yet complete
    enough to hash (no case or no reference answer) -- an incomplete draft stays
    digest-less rather than acquiring a digest that means nothing.
    """
    if version.status != "draft":  # pragma: no cover - callers only refresh drafts
        return version.content_digest
    try:
        digest = load_version_content(session, version).content_digest
    except ProtectedContentMissing:
        version.content_digest = None
        session.flush()
        return None
    version.content_digest = digest
    session.flush()
    return digest
