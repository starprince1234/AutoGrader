"""Question aggregation writes (``RQ-B-3-10``, ``RQ-B-3-20``).

A question is one stem plus protected content bound to an immutable version. Two
write paths exist:

* :func:`create_question` - a new question together with its first draft version;
* :func:`update_question` - edit the current draft, or (with
  ``as_new_version=True``) branch a fresh draft from a published one. Branching is
  where the revision loop lives: the structural delta against the version it
  supersedes is computed and stored (``domain/revisions.py``), so "why is there a
  second version" is answerable from data instead of from memory.

Editing a published version in place is refused with ``409`` and the caller is
told to create a new version. The refusal is enforced three times over: here, by
the ``question_versions`` trigger in the migration, and by the protected-content
triggers - so a future code path that bypasses this module still cannot mutate a
published version.

Draft writes run on the **protected** role because they carry reference answers
and hidden cases. Public reads never do.

**Omission preserves (``CHG-0017``).** Both protected fields of a draft write are
optional, and leaving one out means "I am not saying anything about this field", not
"clear it": an omitted ``cases`` array leaves the stored cases exactly as they are, and an
omitted or blank ``reference_answer`` leaves the stored answer in place. This is what makes
a title-only or tag-only edit possible without the hidden cases and the reference answer
leaving the question bank at all. There is deliberately no way to *clear* a case set: a
version with no cases cannot be evaluated, so it would be a state the rest of the system
has to treat as broken rather than as empty. Whether a field was sent is decided by the
transport (``model_fields_set``) and passed down, exactly as ``revision_note`` already is.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from collections.abc import Sequence

from sqlalchemy.exc import DBAPIError

from ..auth import Principal, require_course_write
from ..config import Settings
from ..compile.manifest import (
    execution_from_stored,
    execution_manifest_to_storage,
    manifest_to_storage,
    validate_adapter_manifest,
    validate_case_execution,
    validate_case_executions,
    validate_execution_manifest,
)
from ..dto.internal import (
    ExecutionManifest,
    QuestionDraftInputModel,
    ReferenceAnswerInternal,
    TestCaseInternal,
)
from ..errors import (
    ImmutableVersionConflict,
    ManifestInvalid,
    NotFound,
    RevisionNoteMisuse,
)
from ..infra.db import Database
from ..infra.models import Question, QuestionVersion
from ..infra.repositories import ProtectedContentRepository, QuestionBankRepository
from ..logging import get_logger, log_event
from .content import validate_stem_markdown
from .draft_generations import accept_generation
from .revisions import compute_revision_diff, revision_note, snapshot_content
from .tags import validate_tags
from .version_content import (
    VersionContent,
    default_ai_case,
    load_version_content,
    refresh_content_digest,
)

IMMUTABLE_SQLSTATE = "23001"
#: ``QuestionDraftInput.revision_note`` is ``maxLength: 500`` in the contract; the column
#: and its CHECK say the same. Validated here so an over-long note is a ``422`` naming
#: the field rather than a database error.
MAX_REVISION_NOTE_CHARS = 500


def _validate_ordinals(payload: QuestionDraftInputModel) -> None:
    """Ordinals must be unique within a submitted set. An omitted set is trivially fine."""
    ordinals = [submitted.ordinal for submitted in payload.cases]
    if len(set(ordinals)) != len(ordinals):
        raise ManifestInvalid("test case ordinals must be unique", field="cases")


def _cases_supplied(payload: QuestionDraftInputModel, supplied: bool | None) -> bool:
    """Whether non-empty ``cases`` were supplied (empty/omitted triggers default AI case on create)."""
    if supplied is None:
        return bool(payload.cases)
    if not supplied and payload.cases:
        raise ManifestInvalid(
            "`cases` was declared omitted but carries entries",
            field="cases",
        )
    return bool(payload.cases)


def _execution_from_payload(
    payload: QuestionDraftInputModel, *, supplied: bool | None
) -> ExecutionManifest | None:
    """The execution declaration the caller supplied, or ``None`` when omitted.

    "Omitted" and "explicitly null" are different statements and only the transport can tell
    them apart (``model_fields_set``): omission keeps the version's stored declaration, while
    an explicit ``null`` resets the version to ``per_case`` -- the only way to express that,
    because the default has no non-null wire form. ``None`` here therefore means "the caller
    could not say", and is read strictly from the value, exactly like ``cases``.
    """
    if supplied is None:
        supplied = payload.execution is not None
    if not supplied:
        return None
    return validate_execution_manifest(payload.execution)


def _cases_from_payload(
    payload: QuestionDraftInputModel, question_version_id: uuid.UUID, *, mode: str
) -> tuple[TestCaseInternal, ...]:
    """Materialize submitted cases with owner-generated UUID v4 identifiers.

    Each case's execution fields are validated against ``mode`` here, so an author learns
    immediately that (say) a per-case question cannot carry ``segment`` rather than at
    publish time.
    """
    cases: list[TestCaseInternal] = []
    for submitted in payload.cases:
        cases.append(
            TestCaseInternal(
                case_id=uuid.uuid4(),
                question_version_id=question_version_id,
                name=submitted.name,
                ordinal=submitted.ordinal,
                weight=submitted.weight,
                visible_to_student=submitted.visible_to_student,
                stdin=submitted.stdin,
                args=tuple(submitted.args),
                expected_stdout=submitted.expected_stdout,
                expected_stderr=submitted.expected_stderr,
                expected_exit_code=submitted.expected_exit_code,
                execution=validate_case_execution(submitted, mode=mode),
            )
        )
    return tuple(cases)


def _cases_for_diff(
    payload: QuestionDraftInputModel, *, mode: str
) -> tuple[TestCaseInternal, ...]:
    """The submitted cases as internal records, for the revision delta only.

    The delta reads ordinals, weights and counts - never a case's inputs or expected
    output - so the placeholder identifiers here cannot influence what is stored.
    """
    return _cases_from_payload(payload, uuid.UUID(int=0), mode=mode)


def _reissue_case(case: TestCaseInternal, question_version_id: uuid.UUID) -> TestCaseInternal:
    """One stored case, re-issued to a new version with a fresh owner-generated id.

    Case rows are identified by ``test_case_id`` and belong to exactly one version, so "the
    same cases on the new version" means new rows carrying the same content - never the same
    identifiers. The version the cases came from keeps its own rows, untouched and frozen.
    """
    return replace(case, case_id=uuid.uuid4(), question_version_id=question_version_id)


def _revision_diff_against(
    before: VersionContent,
    *,
    payload: QuestionDraftInputModel,
    cases: Sequence[TestCaseInternal] | None,
    grading_prompt_supplied: bool | None = None,
) -> dict:
    """Structural delta between a published version and the draft replacing it."""
    return compute_revision_diff(
        snapshot_content(
            language=before.snapshot.language,
            manifest=before.manifest,
            cases=before.cases,
            fixtures=before.fixtures,
            reference_answers=before.snapshot.reference_answers,
            stem_markdown=before.snapshot.stem_markdown,
            max_files=before.snapshot.max_files,
            max_total_bytes=before.snapshot.max_total_bytes,
        ),
        _submitted_content(
            payload,
            cases=before.cases if cases is None else cases,
            unchanged_answers=before.snapshot.reference_answers,
            grading_prompt_supplied=grading_prompt_supplied,
        ),
    )


def _submitted_content(
    payload: QuestionDraftInputModel,
    *,
    cases: Sequence[TestCaseInternal],
    unchanged_answers: Sequence[ReferenceAnswerInternal] = (),
    grading_prompt_supplied: bool | None = None,
) -> dict:
    """The submitted draft as diffable facts."""
    manifest = validate_adapter_manifest(payload.adapter)
    has_explicit_gp = grading_prompt_supplied is True or (
        grading_prompt_supplied is None and payload.grading_prompt is not None
    )
    normalized_gp = (
        (payload.grading_prompt or "").strip() or None if has_explicit_gp else None
    )
    tiers = _submitted_reference_tiers(payload)
    if tiers:
        gp = (
            normalized_gp
            if has_explicit_gp
            else next(
                (
                    item.grading_prompt
                    for item in unchanged_answers
                    if item.grading_prompt is not None
                ),
                None,
            )
        )
        answers = _payload_answers(payload, uuid.UUID(int=0), grading_prompt=gp)
    elif has_explicit_gp:
        answers = tuple(
            replace(answer, grading_prompt=normalized_gp)
            for answer in unchanged_answers
        )
    else:
        answers = tuple(unchanged_answers)
    return snapshot_content(
        language=payload.language,
        manifest=manifest,
        cases=cases,
        fixtures=(),
        reference_answers=answers,
        stem_markdown=payload.stem_markdown,
        max_files=payload.max_files,
        max_total_bytes=payload.max_total_bytes,
    )


def _validate_revision_note(
    payload: QuestionDraftInputModel,
    *,
    supplied: bool | None,
    as_new_version: bool,
) -> None:
    """``revision_note`` is only meaningful on the write that supersedes a version.

    ``supplied`` is the distinction that matters, and only the transport can make it: an
    omitted field and an explicit ``null`` are different statements, and a parsed model
    cannot tell them apart on its own (``model_fields_set`` can, but that is pydantic
    internals leaking into the domain). ``None`` therefore means "the caller could not
    say", and is read as "a note is present", which is the strict reading: an in-process
    caller that put a note in the payload gets the same ``422`` as an HTTP caller would,
    instead of the field being silently ignored.
    """
    if supplied is None:
        supplied = payload.revision_note is not None
    if not supplied:
        return
    if not as_new_version:
        raise RevisionNoteMisuse(
            "`revision_note` records why a version exists, so it is only accepted with "
            "`as_new_version=true`; an in-place draft edit creates no version to explain"
        )
    note = payload.revision_note
    if note is None:
        return
    if not note.strip() or len(note) > MAX_REVISION_NOTE_CHARS:
        raise RevisionNoteMisuse(
            "`revision_note` must be 1 to "
            f"{MAX_REVISION_NOTE_CHARS} characters, or null"
        )


def _submitted_reference_tiers(
    payload: QuestionDraftInputModel,
) -> tuple[tuple[str, str], ...]:
    """Resolve multi-tier reference answers, falling back to single ``reference_answer``."""
    tiers = tuple(
        (tier.label.strip() or f"解法 {index + 1}", tier.body)
        for index, tier in enumerate(payload.reference_answers)
        if tier.body.strip()
    )
    if tiers:
        return tiers
    if payload.reference_answer is not None and payload.reference_answer.strip():
        return (("标准参考答案", payload.reference_answer),)
    return ()


def _payload_answers(
    payload: QuestionDraftInputModel,
    question_version_id: uuid.UUID,
    *,
    grading_prompt: str | None,
) -> tuple[ReferenceAnswerInternal, ...]:
    tiers = _submitted_reference_tiers(payload)
    return tuple(
        ReferenceAnswerInternal(
            reference_answer_id=uuid.UUID(int=0),
            question_version_id=question_version_id,
            language=payload.language,
            body=body,
            ordinal=ordinal,
            label=label,
            grading_prompt=grading_prompt,
        )
        for ordinal, (label, body) in enumerate(tiers)
    )


def _write_protected_content(
    repository: ProtectedContentRepository,
    *,
    version_id: uuid.UUID,
    payload: QuestionDraftInputModel,
    cases: Sequence[TestCaseInternal],
    cases_supplied: bool,
    grading_prompt_supplied: bool | None = None,
    inherited: VersionContent | None = None,
    now: datetime,
) -> None:
    """Write protected answers, grading_prompt, and cases, honouring omission-preserves."""
    has_explicit_gp = grading_prompt_supplied is True or (
        grading_prompt_supplied is None and payload.grading_prompt is not None
    )
    normalized_gp = (
        (payload.grading_prompt or "").strip() or None if has_explicit_gp else None
    )
    tiers = _submitted_reference_tiers(payload)
    if tiers:
        if has_explicit_gp:
            effective_gp = normalized_gp
        elif inherited is not None:
            effective_gp = next(
                (
                    item.grading_prompt
                    for item in inherited.snapshot.reference_answers
                    if item.grading_prompt is not None
                ),
                None,
            )
        else:
            existing = repository.list_reference_answers(version_id)
            effective_gp = next(
                (item.grading_prompt for item in existing if item.grading_prompt is not None),
                None,
            )
        repository.replace_reference_answers(
            question_version_id=version_id,
            language=payload.language,
            items=tiers,
            grading_prompt=effective_gp,
            now=now,
        )
    elif inherited is not None:
        if inherited.snapshot.reference_answers:
            effective_gp = (
                normalized_gp
                if has_explicit_gp
                else next(
                    (
                        item.grading_prompt
                        for item in inherited.snapshot.reference_answers
                        if item.grading_prompt is not None
                    ),
                    None,
                )
            )
            repository.replace_reference_answers(
                question_version_id=version_id,
                language=inherited.snapshot.reference_answers[0].language,
                items=[
                    (previous.label, previous.body)
                    for previous in inherited.snapshot.reference_answers
                ],
                grading_prompt=effective_gp,
                now=now,
            )
    elif has_explicit_gp:
        existing = repository.list_reference_answers(version_id)
        if existing:
            repository.replace_reference_answers(
                question_version_id=version_id,
                language=payload.language,
                items=[(item.label, item.body) for item in existing],
                grading_prompt=normalized_gp,
                now=now,
            )

    if cases_supplied:
        written = cases
    elif inherited is not None:
        written = tuple(_reissue_case(case, version_id) for case in inherited.cases)
    else:
        return
    suite_id = repository.ensure_test_suite(version_id, now=now)
    repository.replace_cases(
        question_version_id=version_id, suite_id=suite_id, cases=written, now=now
    )


def create_question(
    database: Database,
    settings: Settings,
    *,
    principal: Principal | None,
    course_id: uuid.UUID,
    payload: QuestionDraftInputModel,
    revision_note_supplied: bool | None = None,
    cases_supplied: bool | None = None,
    execution_supplied: bool | None = None,
    grading_prompt_supplied: bool | None = None,
    generation_id: uuid.UUID | None = None,
    now: datetime,
) -> tuple[Question, QuestionVersion]:
    """Create a question and its first draft version.

    A first version has no earlier cases to preserve, so omitting ``cases`` here creates
    a draft that carries none: the question exists, and ``publish`` refuses it (a version
    without cases cannot be evaluated) until they are supplied. That is the only way to
    reach a version with no cases, and it is a starting state rather than a transition -
    no write can take a version that has cases down to zero (``CHG-0017`` §3).

    ``generation_id`` is the AI-FLOW-01A acceptance link. It is **not** a shortcut: the
    payload above is the teacher's own edited content and is validated exactly as any other
    write. The id only says "this draft came from that candidate", which is recorded on the
    generation row -- and the candidate content is removed -- **in this transaction**, so a
    generation can never be marked accepted for a version that was rolled back.
    """
    actor = require_course_write(principal, course_id)
    # The first version supersedes nothing, so it can carry neither a note nor a diff;
    # a note sent here would be a reason for a version that is not a revision.
    _validate_revision_note(
        payload, supplied=revision_note_supplied, as_new_version=False
    )
    validate_stem_markdown(payload.stem_markdown, max_chars=settings.max_stem_chars)
    manifest = validate_adapter_manifest(payload.adapter)
    tags = validate_tags(payload.tags)
    cases_provided = _cases_supplied(payload, cases_supplied)
    _validate_ordinals(payload)
    # A first version supersedes nothing, so an omitted declaration simply means `per_case`;
    # a supplied one is validated against the submitted cases before anything is written.
    declared_execution = _execution_from_payload(payload, supplied=execution_supplied)
    execution = declared_execution if declared_execution is not None else ExecutionManifest()
    submitted_cases = _cases_from_payload(
        payload, uuid.UUID(int=0), mode=execution.mode
    )
    validate_case_executions(
        submitted_cases,
        execution=execution,
        adapter_kind=manifest.kind,
        language=payload.language,
    )

    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.create_question(
            course_id=course_id,
            title=payload.title,
            stem_markdown=payload.stem_markdown,
            language=payload.language,
            status="draft",
            tags=tags,
            created_by=_actor_id(actor),
            now=now,
        )
        version = bank.create_version(
            question_id=question.question_id,
            version=1,
            language=payload.language,
            stem_markdown=payload.stem_markdown,
            adapter_manifest=manifest_to_storage(manifest),
            max_files=payload.max_files,
            max_total_bytes=payload.max_total_bytes,
            execution_manifest=execution_manifest_to_storage(execution),
            created_by=_actor_id(actor),
            now=now,
        )
        cases = (
            _cases_from_payload(payload, version.question_version_id, mode=execution.mode)
            if cases_provided
            else (default_ai_case(version.question_version_id, case_id=uuid.uuid4()),)
        )
        _write_protected_content(
            ProtectedContentRepository(session),
            version_id=version.question_version_id,
            payload=payload,
            cases=cases,
            cases_supplied=True,
            grading_prompt_supplied=grading_prompt_supplied,
            now=now,
        )
        refresh_content_digest(session, version)
        if generation_id is not None:
            # Same transaction, on purpose: either the version and the acceptance both
            # exist, or neither does. The candidate content is dropped inside the same
            # commit, so an accepted generation cannot be re-read and saved twice.
            accept_generation(
                session,
                principal=principal,
                generation_id=generation_id,
                course_id=course_id,
                question_version_id=version.question_version_id,
                now=now,
            )
        log_event(
            get_logger("domain.questions"),
            "question.created",
            question_id=str(question.question_id),
            question_version_id=str(version.question_version_id),
            course_id=str(course_id),
            adapter_kind=manifest.kind,
            generation_id=None if generation_id is None else str(generation_id),
        )
        session.flush()
        session.expunge(question)
        session.expunge(version)
        return question, version


def update_question(
    database: Database,
    settings: Settings,
    *,
    principal: Principal | None,
    question_id: uuid.UUID,
    payload: QuestionDraftInputModel,
    as_new_version: bool = False,
    revision_note_supplied: bool | None = None,
    cases_supplied: bool | None = None,
    execution_supplied: bool | None = None,
    grading_prompt_supplied: bool | None = None,
    now: datetime,
) -> tuple[Question, QuestionVersion]:
    """Edit the current draft, or branch a new draft version (never edit in place).

    ``revision_note_supplied`` says whether the caller actually sent the field; the HTTP
    layer reads that from the parsed body (``model_fields_set``) because only there is the
    distinction between "omitted" and "explicit null" still available. It is what lets
    ``422`` mean "you sent a reason for a write that records none" instead of that being
    indistinguishable from an omitted field.

    ``cases_supplied`` is the same fact about ``cases`` and carries the same consequence
    (``CHG-0017``): an omitted set leaves the version's stored cases untouched, so an edit
    that only renames a question or re-tags it never has to carry the hidden cases - or the
    reference answer - out of the bank and back.

    ``execution_supplied`` is the same fact for ``execution``, with one extra case the other
    fields do not have: an explicit ``null`` means "reset to ``per_case``", while an omitted
    key means "keep the stored declaration". Either way the effective declaration is validated
    against the effective case set (supplied or preserved) before anything is written.
    """
    _validate_revision_note(
        payload, supplied=revision_note_supplied, as_new_version=as_new_version
    )
    validate_stem_markdown(payload.stem_markdown, max_chars=settings.max_stem_chars)
    manifest = validate_adapter_manifest(payload.adapter)
    tags = validate_tags(payload.tags)
    cases_provided = _cases_supplied(payload, cases_supplied)
    _validate_ordinals(payload)
    declared_execution = _execution_from_payload(payload, supplied=execution_supplied)

    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        protected = ProtectedContentRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            # The caller may still be outside the course scope; scope wins over
            # existence so a cross-course probe cannot distinguish the two.
            raise NotFound("question not found")
        actor = require_course_write(principal, question.course_id)

        latest = bank.latest_version(question_id)
        if latest is None:  # pragma: no cover - every question has a version
            raise NotFound("question has no version")

        revision_diff = None
        note = None
        #: The content a new version inherits an omitted protected field from. ``None`` for an
        #: in-place edit, where the version's own rows are simply left alone.
        inherited: VersionContent | None = None
        if latest.status in ("published", "archived"):
            if not as_new_version:
                raise ImmutableVersionConflict(
                    "this question's latest version is "
                    f"{latest.status} and immutable; resubmit with as_new_version=true "
                    "to create a new draft version instead of editing in place"
                )
            # One protected read serves two needs: the delta's "before" side, and the
            # content that a field omitted from this write is inherited from (below).
            previous_content = load_version_content(session, latest)
            inherited = previous_content
            # An omitted declaration keeps the previous version's; an explicit null resets.
            execution = (
                declared_execution
                if declared_execution is not None
                else previous_content.execution
            )
            submitted_cases = (
                _cases_for_diff(payload, mode=execution.mode) if cases_provided else None
            )
            effective_cases = (
                submitted_cases if submitted_cases is not None else previous_content.cases
            )
            validate_case_executions(
                effective_cases,
                execution=execution,
                adapter_kind=manifest.kind,
                language=payload.language,
            )
            revision_diff = _revision_diff_against(
                previous_content,
                payload=payload,
                cases=submitted_cases,
                grading_prompt_supplied=grading_prompt_supplied,
            )
            # An author's own reason wins; the derived note is the fallback so a reader
            # is never shown two versions with no explanation at all.
            note = payload.revision_note or revision_note(
                revision_diff, latest.question_version_id
            )
            target = bank.create_version(
                question_id=question_id,
                version=bank.next_version_number(question_id),
                language=payload.language,
                stem_markdown=payload.stem_markdown,
                adapter_manifest=manifest_to_storage(manifest),
                max_files=payload.max_files,
                max_total_bytes=payload.max_total_bytes,
                revision_diff=revision_diff,
                revision_note=note,
                execution_manifest=execution_manifest_to_storage(execution),
                created_by=_actor_id(actor),
                now=now,
            )
        else:
            target = latest
            # In-place edits preserve the stored declaration unless one was supplied; the
            # effective case set is the stored set when `cases` was omitted, because the
            # declaration has to be validated against the cases it will actually judge.
            execution = (
                declared_execution
                if declared_execution is not None
                else execution_from_stored(latest.execution_manifest)
            )
            effective_cases = (
                _cases_from_payload(payload, uuid.UUID(int=0), mode=execution.mode)
                if cases_provided
                else tuple(protected.list_cases(latest.question_version_id))
                or (default_ai_case(latest.question_version_id),)
            )
            validate_case_executions(
                effective_cases,
                execution=execution,
                adapter_kind=manifest.kind,
                language=payload.language,
            )
            bank.update_version_content(
                target,
                language=payload.language,
                stem_markdown=payload.stem_markdown,
                adapter_manifest=manifest_to_storage(manifest),
                max_files=payload.max_files,
                max_total_bytes=payload.max_total_bytes,
                execution_manifest=execution_manifest_to_storage(execution),
                now=now,
            )

        bank.update_question_content(
            question,
            title=payload.title,
            stem_markdown=payload.stem_markdown,
            language=payload.language,
            tags=tags,
            now=now,
        )
        cases = _cases_from_payload(payload, target.question_version_id, mode=execution.mode)
        _write_protected_content(
            protected,
            version_id=target.question_version_id,
            payload=payload,
            cases=cases,
            cases_supplied=cases_provided,
            grading_prompt_supplied=grading_prompt_supplied,
            inherited=inherited,
            now=now,
        )
        # Editing the draft changes its digest, which is what invalidates any earlier
        # attestation: publish compares the attestation against this recomputed value.
        refresh_content_digest(session, target)
        log_event(
            get_logger("domain.questions"),
            "question.draft.updated",
            question_id=str(question_id),
            question_version_id=str(target.question_version_id),
            course_id=str(question.course_id),
            adapter_kind=manifest.kind,
            outcome="new_version" if target is not latest else "in_place",
        )
        session.flush()
        session.expunge(question)
        session.expunge(target)
        return question, target


def _actor_id(actor: Principal) -> uuid.UUID | None:
    try:
        return uuid.UUID(actor.subject)
    except (ValueError, AttributeError, TypeError):
        return None


def is_immutability_violation(error: DBAPIError) -> bool:
    """True when PostgreSQL refused an in-place edit of frozen content."""
    return getattr(getattr(error, "orig", None), "sqlstate", None) == IMMUTABLE_SQLSTATE


def immutable_conflict_from(error: DBAPIError) -> ImmutableVersionConflict:
    return ImmutableVersionConflict(
        "published question versions are immutable; create a new version instead"
    )
