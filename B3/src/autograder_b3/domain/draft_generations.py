"""AI-FLOW-01A orchestration: who may generate, what it costs, and what survives.

The model-facing half lives in :mod:`autograder_b3.draft_generation`; this module owns the
domain rules that a request must satisfy before and after that call:

* **authorization before configuration.** Role and course scope are checked first, so an
  unauthorized caller cannot learn whether a model provider is configured here by comparing
  status codes (``TC-AI-050``, ``TC-AI-067``).
* **a budget counted from persisted facts.** The request limit is per actor over a sliding
  window read out of the usage ledger, so switching course cannot reset it and a client-side
  counter cannot disagree with it.
* **the candidate is private and temporary.** It is stored under the deployment's retention
  window, readable only by the teacher who asked while they still hold write scope on the
  course, and removable by them at any time.
* **acceptance is a write, not a flag flip.** :func:`accept_generation` runs inside the
  question-creation transaction, so a generation is marked accepted only if a
  ``question_version`` really was written -- and the candidate content is dropped in the same
  commit.

Nothing here creates a question, a version or a case: a generation is a candidate, and the
only path into the bank remains ``create_question`` with a human-edited payload.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..auth import Principal, require_authoring_reader, require_course_write
from ..config import Settings
from ..draft_generation import (
    DRAFT_DISCLAIMERS,
    DRAFT_PROMPT_VERSION,
    DraftGenerationProvider,
    DraftGenerationProviderError,
    GenerationView,
    ProposalOutcome,
    build_generation_payload,
    normalize_proposal,
    provider_from_settings,
)
from ..dto.internal import QuestionDraftGenerationRequestModel
from ..errors import Conflict, Forbidden, NotFound, TooManyRequests, UnprocessableEntity
from ..infra.db import Database
from ..llm_client import provider_name_for
from ..logging import get_logger, log_event
from .tags import validate_tags


@dataclass(frozen=True)
class GenerationCapabilities:
    """What a client needs to render the generation form honestly (``TC-AI-053/084``).

    The limits are the **deployment's**, read from settings, so the control can show the
    number the server will actually enforce. ``requests_remaining`` is ``None`` when this
    deployment does not cap requests -- an absent budget must not be rendered as "0 left".
    """

    course_id: uuid.UUID
    provider_status: str
    provider: str | None
    model: str | None
    learning_objective_max_chars: int
    constraints_max_chars: int
    desired_case_count_max: int
    proposal_max_cases: int
    retention_seconds: int
    requests_remaining: int | None
    retry_after: datetime | None
    disclaimers: tuple[str, ...] = DRAFT_DISCLAIMERS

    def to_wire(self) -> dict:
        from ..logging import to_rfc3339_millis

        return {
            "course_id": str(self.course_id),
            "provider_status": self.provider_status,
            "provider": self.provider,
            "model": self.model,
            "learning_objective_max_chars": self.learning_objective_max_chars,
            "constraints_max_chars": self.constraints_max_chars,
            "desired_case_count_max": self.desired_case_count_max,
            "proposal_max_cases": self.proposal_max_cases,
            "retention_seconds": self.retention_seconds,
            "requests_remaining": self.requests_remaining,
            "retry_after": None if self.retry_after is None else to_rfc3339_millis(self.retry_after),
            "disclaimers": list(self.disclaimers),
        }


def _budget(settings: Settings, usages: list, *, now: datetime) -> tuple[int | None, datetime | None]:
    """``(remaining, retry_after)`` from the ledger rows inside the current window."""
    if not settings.draft_generation_budget_enabled:
        return None, None
    used = len(usages)
    if used >= settings.draft_generation_budget_requests:
        window = timedelta(seconds=settings.draft_generation_budget_window_seconds)
        retry_after = usages[0].requested_at + window
        # A clock that moved backwards, or a row written exactly at the boundary, must not
        # produce a retry time in the past: the client would retry immediately and fail again.
        return 0, max(retry_after, now + timedelta(seconds=1))
    return settings.draft_generation_budget_requests - used, None


def _window_start(settings: Settings, now: datetime) -> datetime:
    return now - timedelta(seconds=settings.draft_generation_budget_window_seconds)


def _retention_seconds(settings: Settings) -> int:
    """The configured retention, floored at one second.

    The table's CHECK requires ``expires_at > created_at``, so a deployment that set the
    retention to ``0`` (the config parser allows ``0`` because the *budget* uses it as "no
    cap") would otherwise make every generation a ``500`` at insert time. Advertising the
    same floored value keeps the reported ``expires_at`` honest.
    """
    return max(1, settings.draft_generation_retention_seconds)


def generation_capabilities(
    database: Database,
    settings: Settings,
    *,
    principal: Principal | None,
    course_id: uuid.UUID,
    now: datetime,
) -> GenerationCapabilities:
    """Report provider availability, the configured limits and the caller's own budget."""
    actor = require_course_write(principal, course_id)
    with database.protected_session() as session:
        from ..infra.repositories import DraftGenerationRepository

        usages = DraftGenerationRepository(session).usage_since(
            actor.subject, _window_start(settings, now)
        )
        remaining, retry_after = _budget(settings, usages, now=now)
    enabled = settings.draft_generation_enabled
    return GenerationCapabilities(
        course_id=course_id,
        provider_status="enabled" if enabled else "unavailable",
        # Only the *provider kind* and model id are reported: no URL, no credential, and no
        # other teacher's usage.
        provider=provider_name_for(settings.llm_base_url or "") if enabled else None,
        model=settings.llm_model if enabled else None,
        learning_objective_max_chars=settings.draft_generation_max_objective_chars,
        constraints_max_chars=settings.draft_generation_max_constraints_chars,
        desired_case_count_max=settings.draft_generation_max_desired_cases,
        proposal_max_cases=settings.draft_generation_max_proposal_cases,
        retention_seconds=_retention_seconds(settings),
        requests_remaining=remaining,
        retry_after=retry_after,
    )


def request_generation(
    database: Database,
    settings: Settings,
    *,
    principal: Principal | None,
    course_id: uuid.UUID,
    payload: QuestionDraftGenerationRequestModel,
    provider: DraftGenerationProvider | None = None,
    now: datetime,
) -> GenerationView:
    """Ask the configured provider for one candidate and store it privately.

    Order of refusals is deliberate and is the security property, not an implementation
    detail: role/scope (401/403) -> input (422) -> provider configured (else ``status:
    unavailable``) -> budget (429) -> provider call. A caller who may not use the operation
    therefore never learns whether this deployment has a model, and an unconfigured
    deployment never reaches a paid call.
    """
    actor = require_course_write(principal, course_id)
    tags = validate_tags(payload.tags)
    constraints = payload.constraints
    if constraints is not None and len(constraints) > settings.draft_generation_max_constraints_chars:
        raise UnprocessableEntity(
            "`constraints` exceeds the "
            f"{settings.draft_generation_max_constraints_chars} characters this deployment "
            "accepts; shorten it",
            field="constraints",
        )
    outbound = build_generation_payload(
        learning_objective=payload.learning_objective,
        language=payload.language,
        adapter_kind=payload.adapter_kind,
        difficulty=payload.difficulty,
        tags=tags,
        desired_case_count=payload.desired_case_count,
        constraints=constraints,
        max_objective_chars=settings.draft_generation_max_objective_chars,
    )

    # The provider is resolved here rather than only from settings so a test can inject the
    # outbound call (the same allowance the B4 JWKS fetch has) and still exercise every other
    # step for real. When nothing is configured *and* nothing was injected, this is the
    # unavailable state -- not a failure, and not a paid call.
    resolved = provider or provider_from_settings(settings)
    if resolved is None:
        # A *configuration state*: nothing is charged, nothing is stored and no request leaves
        # the process. It is reported as `status: unavailable` in the same body every other
        # outcome uses, so the editor renders one message next to the button it disables
        # instead of routing a deployment fact through an error banner. The order above still
        # matters: a caller who is not allowed to generate never reaches this line.
        return GenerationView(
            course_id=course_id,
            status="unavailable",
            proposal=None,
            warnings=(),
            provider=None,
            model=None,
            prompt_version=DRAFT_PROMPT_VERSION,
        )

    with database.protected_session() as session:
        from ..infra.repositories import DraftGenerationRepository

        usages = DraftGenerationRepository(session).usage_since(
            actor.subject, _window_start(settings, now)
        )
        remaining, retry_after = _budget(settings, usages, now=now)
    if remaining == 0 and retry_after is not None:
        seconds = max(1, int((retry_after - now).total_seconds()))
        raise TooManyRequests(
            "generation budget exhausted for this account; the window frees up in "
            f"{seconds} seconds. Switching course does not reset it.",
            headers={"Retry-After": str(seconds)},
            field="budget",
        )

    provider_name = getattr(resolved, "name", "unknown")
    model_name = getattr(resolved, "model", "unknown")

    started = time.perf_counter()
    try:
        answer = resolved.generate(payload=outbound)
    except DraftGenerationProviderError as error:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        # The paid attempt is recorded even though no candidate exists: "the model was called
        # and failed" is what the ledger is for. The error text is a classification, never
        # the provider body.
        with database.protected_session() as session:
            from ..infra.repositories import DraftGenerationRepository

            DraftGenerationRepository(session).record_usage(
                generation_id=None,
                course_id=course_id,
                owner_subject=actor.subject,
                provider=provider_name,
                model=model_name,
                prompt_version=DRAFT_PROMPT_VERSION,
                requested_at=now,
                duration_ms=elapsed_ms,
                status="failed",
                error_category="provider",
            )
        log_event(
            get_logger("domain.draft_generation"),
            "question.draft.generation_failed",
            course_id=str(course_id),
            error_category="provider",
            duration_ms=elapsed_ms,
        )
        raise error
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    outcome: ProposalOutcome = normalize_proposal(
        answer.document,
        language=payload.language,
        adapter_kind=payload.adapter_kind,
        limits=settings,
    )

    with database.protected_session() as session:
        from ..infra.repositories import DraftGenerationRepository

        repository = DraftGenerationRepository(session)
        row = repository.create_generation(
            course_id=course_id,
            owner_subject=actor.subject,
            status=outcome.status,
            language=payload.language,
            adapter_kind=payload.adapter_kind,
            provider=provider_name,
            model=model_name,
            prompt_version=DRAFT_PROMPT_VERSION,
            proposal=None if outcome.proposal is None else outcome.proposal.to_storage(),
            warnings=[
                {"field": item.field, "message": item.message, "severity": item.severity}
                for item in outcome.warnings
            ],
            created_at=now,
            expires_at=now + timedelta(seconds=_retention_seconds(settings)),
        )
        repository.record_usage(
            generation_id=row.generation_id,
            course_id=course_id,
            owner_subject=actor.subject,
            provider=provider_name,
            model=model_name,
            prompt_version=DRAFT_PROMPT_VERSION,
            requested_at=now,
            duration_ms=elapsed_ms,
            status=outcome.status,
            prompt_tokens=answer.usage.prompt_tokens,
            completion_tokens=answer.usage.completion_tokens,
            total_tokens=answer.usage.total_tokens,
        )
        view = _view(row)
        log_event(
            get_logger("domain.draft_generation"),
            "question.draft.generated",
            course_id=str(course_id),
            generation_id=str(row.generation_id),
            outcome=outcome.status,
            attempt_count=len(outcome.warnings),
            duration_ms=elapsed_ms,
        )
        return view


def _view(row) -> GenerationView:
    """Project a stored generation. The proposal comes back only while it still exists."""
    warnings = tuple(
        _warning_from_storage(item) for item in (row.warnings or []) if isinstance(item, dict)
    )
    return GenerationView(
        course_id=row.course_id,
        status=row.status,  # type: ignore[arg-type]
        proposal=_proposal_from_storage(row),
        warnings=warnings,
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        generation_id=row.generation_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
        accepted_question_version_id=row.accepted_question_version_id,
    )


def _warning_from_storage(item: dict):
    from ..draft_generation import DraftWarning

    field = item.get("field")
    message = item.get("message")
    severity = item.get("severity", "warning")
    if not isinstance(field, str) or not isinstance(message, str):
        return DraftWarning(field="generation", message="模型返回的字段问题无法读取。")
    if severity not in ("error", "warning", "info"):
        severity = "warning"
    return DraftWarning(field=field, message=message, severity=severity)


def _proposal_from_storage(row):
    """Re-read a stored candidate. Present only while the record still carries content."""
    from ..draft_generation import DraftProposal

    stored = row.proposal
    if not isinstance(stored, dict):
        return None
    return DraftProposal(
        title=str(stored.get("title") or ""),
        stem_markdown=str(stored.get("stem_markdown") or ""),
        language=str(stored.get("language") or row.language),
        adapter_kind=str(stored.get("adapter_kind") or row.adapter_kind),
        tags=tuple(str(tag) for tag in stored.get("tags") or ()),
        reference_answer=(
            stored.get("reference_answer")
            if isinstance(stored.get("reference_answer"), str)
            else None
        ),
        cases=tuple(_case_from_storage(case) for case in stored.get("cases") or ()),
        fixtures=tuple(
            _fixture_from_storage(fixture) for fixture in stored.get("fixtures") or ()
        ),
        adapter=_adapter_from_storage(stored.get("adapter")),
        examples=tuple(_example_from_storage(item) for item in stored.get("examples") or ()),
        knowledge_points=tuple(str(item) for item in stored.get("knowledge_points") or ()),
        difficulty=stored.get("difficulty") if isinstance(stored.get("difficulty"), str) else None,
        notes=stored.get("notes") if isinstance(stored.get("notes"), str) else None,
    )


def _case_from_storage(payload: object):
    from ..draft_generation import DraftCaseProposal

    data = payload if isinstance(payload, dict) else {}
    weight = data.get("weight")
    return DraftCaseProposal(
        name=str(data.get("name") or ""),
        ordinal=int(data.get("ordinal") or 0),
        weight=float(weight) if isinstance(weight, (int, float)) else None,
        visible_to_student=data.get("visible_to_student") is True,
        stdin=data.get("stdin") if isinstance(data.get("stdin"), str) else None,
        args=tuple(str(item) for item in data.get("args") or ()),
        expected_stdout=(
            data.get("expected_stdout") if isinstance(data.get("expected_stdout"), str) else None
        ),
        expected_stderr=(
            data.get("expected_stderr") if isinstance(data.get("expected_stderr"), str) else None
        ),
        expected_exit_code=(
            data.get("expected_exit_code")
            if isinstance(data.get("expected_exit_code"), int)
            else None
        ),
    )


def _fixture_from_storage(payload: object):
    from ..draft_generation import DraftFixtureProposal

    data = payload if isinstance(payload, dict) else {}
    return DraftFixtureProposal(
        path=str(data.get("path") or ""),
        mount=str(data.get("mount") or "input"),
        content_text=str(data.get("content_text") or ""),
    )


def _adapter_from_storage(payload: object):
    from ..draft_generation import DraftAdapterProposal

    data = payload if isinstance(payload, dict) else {}
    comparison = data.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    callable_target = data.get("callable")
    callable_target = callable_target if isinstance(callable_target, dict) else {}
    tolerance = comparison.get("numeric_tolerance")
    return DraftAdapterProposal(
        args=tuple(str(item) for item in data.get("args") or ()),
        stdin=data.get("stdin") if isinstance(data.get("stdin"), str) else None,
        expect_exit_code=(
            data.get("expect_exit_code") if isinstance(data.get("expect_exit_code"), int) else None
        ),
        comparison_stdout=(
            comparison.get("stdout") if isinstance(comparison.get("stdout"), str) else None
        ),
        comparison_stderr=(
            comparison.get("stderr") if isinstance(comparison.get("stderr"), str) else None
        ),
        numeric_tolerance=float(tolerance) if isinstance(tolerance, (int, float)) else None,
        callable_module=(
            callable_target.get("module") if isinstance(callable_target.get("module"), str) else None
        ),
        callable_attribute=(
            callable_target.get("attribute")
            if isinstance(callable_target.get("attribute"), str)
            else None
        ),
        callable_arguments_json=(
            callable_target.get("arguments_json")
            if isinstance(callable_target.get("arguments_json"), str)
            else None
        ),
    )


def _example_from_storage(payload: object):
    from ..draft_generation import DraftExampleProposal

    data = payload if isinstance(payload, dict) else {}
    return DraftExampleProposal(
        input_text=str(data.get("input") or ""),
        output_text=str(data.get("output") or ""),
        explanation=data.get("explanation") if isinstance(data.get("explanation"), str) else None,
    )


def _require_owner(principal: Principal | None, row) -> Principal:
    """The creator, still holding authoring scope on the course the candidate belongs to.

    ``generation_id`` is an identifier, not a credential (``TC-AI-064``): copying it to another
    teacher produces ``403`` rather than a read, and the check runs before any content is
    touched.
    """
    actor = require_authoring_reader(principal)
    if actor.subject != row.owner_subject:
        raise Forbidden("this generation belongs to another account")
    require_course_write(actor, row.course_id)
    return actor


def _load_active(database: Database, *, generation_id: uuid.UUID, now: datetime):
    """Read a generation that is still alive, purging an expired one on the way.

    Expiry is enforced on read as well as by the retention sweep, so a record whose window
    closed cannot be served just because a cleaner has not run yet. The purge happens in its
    **own** committed transaction: doing it inside the same session that then raises would
    roll the delete back, and the row would stay readable-in-principle forever while every
    read pretended to have removed it.
    """
    from ..infra.repositories import DraftGenerationRepository

    expired = False
    with database.protected_session() as session:
        row = DraftGenerationRepository(session).get_generation(generation_id)
        if row is None:
            raise NotFound("generation not found")
        if row.expires_at <= now:
            expired = True
        elif row.discarded_at is not None:
            raise NotFound("generation was discarded and cannot be read back")
        else:
            session.expunge(row)
            return row

    if expired:
        with database.protected_session() as session:
            repository = DraftGenerationRepository(session)
            stale = repository.get_generation(generation_id)
            if stale is not None:
                repository.delete_generation(stale)
        raise NotFound("generation has expired and its candidate was removed")
    raise NotFound("generation not found")  # pragma: no cover - both paths raise above


def read_generation(
    database: Database,
    *,
    principal: Principal | None,
    generation_id: uuid.UUID,
    now: datetime,
) -> GenerationView:
    """Restore a candidate after a refresh. Never restores the teacher's unsaved edits."""
    row = _load_active(database, generation_id=generation_id, now=now)
    _require_owner(principal, row)
    return _view(row)


def discard_generation(
    database: Database,
    *,
    principal: Principal | None,
    generation_id: uuid.UUID,
    now: datetime,
) -> None:
    """Remove the candidate content, keeping the de-identified ledger row (``TC-AI-082``)."""
    from ..infra.repositories import DraftGenerationRepository

    row = _load_active(database, generation_id=generation_id, now=now)
    _require_owner(principal, row)
    if row.accepted_at is not None:
        raise Conflict(
            "this candidate was already saved as a question version; "
            "there is no unaccepted proposal left to discard"
        )
    with database.protected_session() as session:
        repository = DraftGenerationRepository(session)
        stored = repository.get_generation(generation_id)
        if stored is None:  # pragma: no cover - created above in the same request
            raise NotFound("generation not found")
        repository.discard_generation(stored, now=now)
        log_event(
            get_logger("domain.draft_generation"),
            "question.draft.discarded",
            generation_id=str(generation_id),
            course_id=str(stored.course_id),
        )


def accept_generation(
    session,
    *,
    principal: Principal | None,
    generation_id: uuid.UUID,
    course_id: uuid.UUID,
    question_version_id: uuid.UUID,
    now: datetime,
) -> None:
    """Bind the candidate to the version that was just written, in the caller's transaction.

    Runs **inside** the question-creation session, so "the version exists" and "the generation
    is accepted" commit together: a failed write leaves the candidate unaccepted and the
    teacher can retry, and a successful write cannot leave a candidate readable that a second
    save could turn into a second version (``TC-AI-080``).
    """
    from ..infra.repositories import DraftGenerationRepository

    repository = DraftGenerationRepository(session)
    row = repository.get_generation(generation_id)
    if row is None:
        raise NotFound("generation not found; the candidate may have expired")
    _require_owner(principal, row)
    if row.course_id != course_id:
        raise Forbidden("this generation belongs to a different course")
    if row.status == "failed":
        raise Conflict(
            "this generation produced no editable candidate, so there is nothing to accept"
        )
    if row.accepted_at is not None:
        raise Conflict("this generation was already used to create a question version")
    if row.discarded_at is not None:
        raise NotFound("this generation was discarded")
    if row.expires_at <= now:
        raise NotFound("this generation has expired and its candidate was removed")
    repository.accept_generation(row, question_version_id=question_version_id, now=now)
    repository.link_usage_to_version(generation_id, question_version_id)
    log_event(
        get_logger("domain.draft_generation"),
        "question.draft.accepted",
        generation_id=str(generation_id),
        question_version_id=str(question_version_id),
        course_id=str(course_id),
    )
