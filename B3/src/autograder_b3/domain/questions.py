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
from ..compile.manifest import manifest_to_storage, validate_adapter_manifest
from ..dto.internal import (
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
from .version_content import VersionContent, load_version_content, refresh_content_digest

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
    """Whether the caller *sent* ``cases``, as opposed to omitting the field.

    Same shape as ``revision_note_supplied`` in :func:`_validate_revision_note`: only the
    transport can tell "the field is absent" from "the field holds a value", so the HTTP
    layer reads ``model_fields_set`` and passes the answer down. ``None`` therefore means
    "the caller could not say", and is read strictly from the value: an in-process caller
    that put cases in the payload gets them written, one that left them out gets the
    stored set preserved.

    The two contradictions are refused rather than resolved, because either resolution is
    a silent wrong answer about protected content: "supplied but empty" would be a way to
    clear a case set (which no path is allowed to offer), and "omitted but non-empty"
    would mean dropping cases the caller did send. Neither is reachable over HTTP --
    ``QuestionDraftInputModel`` rejects ``[]`` (``minItems: 1``) and ``null`` (its type) --
    so reaching one means a programming error in this module, and it is reported as such.
    """
    if supplied is None:
        return bool(payload.cases)
    if supplied != bool(payload.cases):
        raise ManifestInvalid(
            "`cases` was declared "
            + ("supplied but is empty" if supplied else "omitted but carries entries")
            + "; a case set is either provided in full or not provided at all",
            field="cases",
        )
    return supplied


def _cases_from_payload(
    payload: QuestionDraftInputModel, question_version_id: uuid.UUID
) -> tuple[TestCaseInternal, ...]:
    """Materialize submitted cases with owner-generated UUID v4 identifiers."""
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
            )
        )
    return tuple(cases)


def _cases_for_diff(payload: QuestionDraftInputModel) -> tuple[TestCaseInternal, ...]:
    """The submitted cases as internal records, for the revision delta only.

    The delta reads ordinals, weights and counts - never a case's inputs or expected
    output - so the placeholder identifiers here cannot influence what is stored.
    """
    return _cases_from_payload(payload, uuid.UUID(int=0))


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
) -> dict:
    """Structural delta between a published version and the draft replacing it.

    The previous content arrives already read on the protected role, because the delta has
    to know whether the reference answer and the fixture digests actually differ; only the
    resulting *counts, digests and flags* are stored, never the content they describe.

    ``cases`` is ``None`` when the caller omitted the field, and the version being branched
    then carries the **previous** cases unchanged (``CHG-0017``). The delta is computed
    against exactly that set rather than against an empty one, so it reports "the case set
    did not change" - which is what the new version will actually hold (the write path
    re-issues those cases onto it). Reporting the omission as "zero cases" would be a delta
    that contradicts the row it describes.
    """
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
            reference_answer=payload.reference_answer,
            unchanged_answers=before.snapshot.reference_answers,
        ),
    )


def _submitted_content(
    payload: QuestionDraftInputModel,
    *,
    cases: Sequence[TestCaseInternal],
    reference_answer: str | None,
    unchanged_answers: Sequence[ReferenceAnswerInternal] = (),
) -> dict:
    """The submitted draft as diffable facts.

    Both protected fields follow one rule - what the draft does **not** say, the version
    keeps - and the delta has to apply the same rule the write path does, or it reports a
    change nobody made. An answer that is absent **or blank** therefore leaves the stored
    answers in force (a blank string is not a way to remove the answer, ``CHG-0017`` §3),
    and is passed through unchanged rather than hashed as an empty body. When the draft
    carries no case set, the caller has already substituted the previous cases, which is why
    ``cases`` is never empty merely because the field was omitted. The answer travels only
    as language plus body, and only into a digest; no expected output is read at all,
    because the delta is about the declaration, not about what a student produces.
    """
    manifest = validate_adapter_manifest(payload.adapter)
    if reference_answer is not None and reference_answer.strip():
        answers: tuple[ReferenceAnswerInternal, ...] = (
            _payload_answer(payload, uuid.UUID(int=0)),
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


def _payload_answer(
    payload: QuestionDraftInputModel, question_version_id: uuid.UUID
) -> ReferenceAnswerInternal:
    """The submitted answer as an internal record, used for its language and length."""
    return ReferenceAnswerInternal(
        reference_answer_id=uuid.UUID(int=0),
        question_version_id=question_version_id,
        language=payload.language,
        body=payload.reference_answer or "",
    )


def _write_protected_content(
    repository: ProtectedContentRepository,
    *,
    version_id: uuid.UUID,
    payload: QuestionDraftInputModel,
    cases: Sequence[TestCaseInternal],
    cases_supplied: bool,
    inherited: VersionContent | None = None,
    now: datetime,
) -> None:
    """Write the protected half of a draft, honouring omission (``CHG-0017``).

    ``reference_answer``: omitted **or blank** keeps the stored answer; a blank string is
    not a way to remove it either.

    ``cases``: only a *supplied* set is written, and it replaces the whole set (the
    repository deletes the suite's rows and inserts the submitted ones, so nothing
    accumulates). An omitted set means "this version keeps the cases it already has", so
    this function does not delete, rewrite or even load them. (The digest refresh that
    follows does read the version's content, because it has to hash it; what never happens
    is that content travelling out to the caller and back.) There is deliberately no path
    that leaves a version with no cases: omitting preserves, and supplying requires at
    least one, so the two together cannot reduce a case set to zero. A brand-new question
    that omits cases is the one way to reach a version without them, and ``publish``
    refuses it until cases are supplied.

    ``inherited`` is the content of the version this write supersedes, and is passed **only**
    when the write creates a new version. There, an omitted field cannot mean "leave the rows
    alone": the rows belong to the previous version, which is frozen, so the new draft would
    start empty and a title correction would silently destroy the question's cases. Instead
    the omitted field is re-issued onto the new version - same content, new rows, new
    owner-generated identifiers. An in-place edit passes ``None``, because there the smaller
    operation is the right one: nothing is written, so the content digest (and any
    attestation bound to it) survives a rename untouched.
    """
    answer = payload.reference_answer
    if answer is not None and answer.strip():
        repository.upsert_reference_answer(
            question_version_id=version_id,
            language=payload.language,
            body=answer,
            now=now,
        )
    elif inherited is not None:
        for previous in inherited.snapshot.reference_answers:
            repository.upsert_reference_answer(
                question_version_id=version_id,
                language=previous.language,
                body=previous.body,
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
            created_by=_actor_id(actor),
            now=now,
        )
        cases = _cases_from_payload(payload, version.question_version_id)
        _write_protected_content(
            ProtectedContentRepository(session),
            version_id=version.question_version_id,
            payload=payload,
            cases=cases,
            cases_supplied=cases_provided,
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
    """
    _validate_revision_note(
        payload, supplied=revision_note_supplied, as_new_version=as_new_version
    )
    validate_stem_markdown(payload.stem_markdown, max_chars=settings.max_stem_chars)
    manifest = validate_adapter_manifest(payload.adapter)
    tags = validate_tags(payload.tags)
    cases_provided = _cases_supplied(payload, cases_supplied)
    _validate_ordinals(payload)

    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
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
            revision_diff = _revision_diff_against(
                previous_content,
                payload=payload,
                # `None` says "the caller omitted the set"; the delta then compares the
                # previous cases against themselves, which is what the new version holds.
                cases=_cases_for_diff(payload) if cases_provided else None,
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
                created_by=_actor_id(actor),
                now=now,
            )
        else:
            target = latest
            bank.update_version_content(
                target,
                language=payload.language,
                stem_markdown=payload.stem_markdown,
                adapter_manifest=manifest_to_storage(manifest),
                max_files=payload.max_files,
                max_total_bytes=payload.max_total_bytes,
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
        cases = _cases_from_payload(payload, target.question_version_id)
        _write_protected_content(
            ProtectedContentRepository(session),
            version_id=target.question_version_id,
            payload=payload,
            cases=cases,
            cases_supplied=cases_provided,
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
