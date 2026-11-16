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
import uuid

from ..compile.manifest import (
    case_execution_payload,
    execution_from_stored,
    execution_manifest_to_storage,
    manifest_from_stored,
    validate_case_executions,
)
from ..domain.content import build_snapshot, compute_content_digest
from ..dto.internal import PublishedVersionSnapshot, TestCaseInternal, VersionContentSnapshot
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
    def execution(self):
        return self.snapshot.execution

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
            execution=snapshot.execution,
        )


def default_ai_case(
    question_version_id: uuid.UUID, *, case_id: uuid.UUID | None = None
) -> TestCaseInternal:
    """Synthetic 100-point case for AI logic grading when no explicit test cases exist."""
    return TestCaseInternal(
        case_id=uuid.UUID(int=0) if case_id is None else case_id,
        question_version_id=question_version_id,
        name="AI 逻辑与代码质量评测",
        ordinal=0,
        weight=100.0,
        visible_to_student=True,
    )


class EditableDraftContent:
    """A draft's own reference answers, grading prompt and cases, for the authoring edit page.

    Protected content that the *author* of a draft must be able to correct. Only a teacher
    holding write scope on the version's course can load this.
    """

    __slots__ = (
        "reference_answer",
        "reference_answers",
        "grading_prompt",
        "cases",
        "execution",
    )

    def __init__(
        self,
        *,
        reference_answer: str | None,
        cases,
        execution,
        reference_answers: tuple[dict[str, str], ...] = (),
        grading_prompt: str | None = None,
    ) -> None:
        self.reference_answer = reference_answer
        self.reference_answers = tuple(reference_answers)
        self.grading_prompt = grading_prompt
        self.cases = tuple(cases)
        self.execution = execution

    def to_wire(self) -> dict:
        """The ``QuestionDraftContent`` shape."""
        return {
            "reference_answer": self.reference_answer,
            "reference_answers": [dict(item) for item in self.reference_answers],
            "grading_prompt": self.grading_prompt,
            "execution": execution_manifest_to_storage(self.execution)
            or {"mode": "per_case"},
            "cases": [
                {
                    "name": case.name,
                    "ordinal": case.ordinal,
                    "weight": case.weight,
                    "visible_to_student": case.visible_to_student,
                    "stdin": case.stdin,
                    "args": list(case.args),
                    "expected_stdout": case.expected_stdout,
                    "expected_stderr": case.expected_stderr,
                    "expected_exit_code": case.expected_exit_code,
                    **case_execution_payload(case.execution),
                }
                for case in self.cases
            ],
        }


# (deploy retrigger) 
def load_editable_draft_content(database, *, question_version_id, principal) -> EditableDraftContent:
    """Load a version's own answer + grading prompt + cases for its author."""
    from ..auth import require_course_write
    from ..errors import Conflict, NotFound
    from ..infra.models import Question

    with database.protected_session() as session:
        version = session.get(QuestionVersion, question_version_id)
        if version is None:
            raise NotFound("question version not found")
        question = session.get(Question, version.question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_write(principal, question.course_id)
        if version.status not in ("draft", "published"):
            raise Conflict("only a draft or a published version can be read back for editing")
        content = load_version_content(session, version, require_reference_answer=False)
        answers = content.snapshot.reference_answers
        grading_prompt = next(
            (answer.grading_prompt for answer in answers if answer.grading_prompt is not None),
            None,
        )
        return EditableDraftContent(
            reference_answer=answers[0].body if answers else None,
            reference_answers=tuple(
                {"label": answer.label, "body": answer.body} for answer in answers
            ),
            grading_prompt=grading_prompt,
            cases=content.snapshot.cases,
            execution=content.execution,
        )


def load_version_content(
    session, version: QuestionVersion, *, require_reference_answer: bool = True
) -> VersionContent:
    """Read a version's protected content and build its digest input."""
    protected = ProtectedContentRepository(session)
    cases = protected.list_cases(version.question_version_id)
    if not cases:
        cases = [default_ai_case(version.question_version_id)]
    fixtures = protected.list_fixtures(version.question_version_id)
    reference_answers = protected.list_reference_answers(version.question_version_id)
    manifest = manifest_from_stored(
        version.adapter_manifest, cases=cases, fixtures=fixtures
    )
    execution = execution_from_stored(version.execution_manifest)
    validate_case_executions(
        cases,
        execution=execution,
        adapter_kind=manifest.kind,
        language=version.language,
    )

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
            execution=execution,
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
