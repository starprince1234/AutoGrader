"""HTTP routes.

Path set = the frozen contract (``MAIN/contracts/openapi/b3.yaml``) plus three
operations B3 needs and the contract lacks: stem-graphic upload/fetch and fixture
upload. They stay **inside** the three route prefixes ``module.yaml`` already
declares, so no new prefix is introduced; the gap is recorded as ``CHG-B3-0002``
and no other module should depend on them until MAIN ratifies them.

``CHG-0013`` closed the other direction of the same problem - implemented capabilities
with no declared surface (comparison diagnostics, ``revision_note``/``revision_diff``,
tag retrieval) - so those are contract paths now and are not listed as gaps.

Every response is produced by ``api.serializers``; every failure is an RFC 9457
Problem Details body from ``errors.B3Error`` (or the installed handlers).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from ..auth import require_service
from ..domain import (
    archive_version,
    authoring_assistance,
    create_assignment,
    create_question,
    get_question,
    get_version,
    list_assignment_versions,
    list_questions,
    load_asset_for_delivery,
    load_evaluation_package,
    load_reference_answer,
    publish_version,
    question_diagnostics,
    question_statistics,
    question_tag_coverage,
    record_reference_attestation,
    update_question,
    upload_fixture,
    upload_stem_asset,
)
from ..domain.tags import validate_tags
from ..dto.internal import (
    AssetUploadInputModel,
    AssignmentCreateInputModel,
    AuthoringAssistanceRequestModel,
    FixtureUploadInputModel,
    QuestionDraftInputModel,
    ReferenceAttestationInputModel,
)
from ..dto.public import (
    AssignmentPublic,
    FixtureUploadResult,
    StemAssetUploadResult,
)
from ..errors import UnprocessableEntity
from ..logging import get_logger, log_event
from .deps import B3Dependencies, RequestContext, get_dependencies, request_context
from .pagination import clamp_limit, decode_cursor, encode_cursor
from .serializers import (
    assignment_to_public,
    attestation_to_public,
    authoring_assistance_to_public,
    coverage_to_public,
    diagnostics_to_public,
    question_to_public,
    serialize_public,
    statistics_to_public,
    version_to_public,
)

router = APIRouter()


def _context(request: Request) -> RequestContext:
    return request_context(request)


def _dependencies(request: Request) -> B3Dependencies:
    return get_dependencies(request)


def _parse_timestamp(value: str | None, field: str):
    if value is None:
        return None
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnprocessableEntity(f"`{field}` must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise UnprocessableEntity(f"`{field}` must carry a UTC offset")
    return parsed


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------


@router.get("/api/v1/questions")
def list_questions_endpoint(
    request: Request,
    cursor: str | None = None,
    limit: int | None = None,
    course_id: uuid.UUID | None = None,
    language: str | None = None,
    published_only: bool = True,
    tags: Annotated[list[str] | None, Query()] = None,
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    if language is not None and language not in ("python", "bash"):
        raise UnprocessableEntity("`language` must be 'python' or 'bash'")
    # Validated with the same rule the write path uses (CHG-0013): a malformed query tag
    # would otherwise select nothing and look like "no question carries this tag".
    requested_tags = validate_tags(tags)
    resolved_limit = clamp_limit(limit)
    after = decode_cursor(cursor) if cursor else None
    rows, has_more = list_questions(
        dependencies.database,
        principal=context.principal,
        course_id=course_id,
        language=language,
        published_only=published_only,
        limit=resolved_limit,
        after=after,
        tags=requested_tags,
    )
    items = []
    for row in rows:
        assets, versions = _question_extras(dependencies, context, row)
        items.append(serialize_public(question_to_public(row, assets, versions)))
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.question_id)
    log_event(
        get_logger("api.questions"),
        "questions.listed",
        request_id=context.request_id,
        status_code=200,
        attempt_count=len(items),
    )
    return {"items": items, "next_cursor": next_cursor}


def _question_extras(
    dependencies: B3Dependencies, context: RequestContext, question
) -> tuple[list, list]:
    """Return ``(assets_of_the_displayed_version, full_version_history)``.

    One session serves both so a listing does not double-query per row. The history is
    part of the public DTO because a client otherwise has no way to obtain a
    ``question_version_id`` and therefore cannot publish (found during integration).
    """
    from ..infra.repositories import QuestionBankRepository

    with dependencies.database.app_session() as session:
        bank = QuestionBankRepository(session)
        versions = bank.list_versions(question.question_id)
        if not versions:
            return [], []
        published = next((item for item in versions if item.status == "published"), None)
        is_staff = context.principal is not None and context.principal.has_role(
            "teacher", "assistant", "admin"
        )
        target = published or (versions[0] if is_staff else None)
        assets = (
            bank.list_assets_for_version(target.question_version_id) if target is not None else []
        )
        # Detach everything the caller will read after the session closes.
        for asset in assets:
            session.expunge(asset)
        for version in versions:
            session.expunge(version)
        return assets, versions


@router.post("/api/v1/questions", status_code=201)
def create_question_endpoint(
    request: Request,
    payload: QuestionDraftInputModel,
    course_id: Annotated[uuid.UUID, Query(description="Course the question belongs to")],
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    question, version = create_question(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        course_id=course_id,
        payload=payload,
        revision_note_supplied="revision_note" in payload.model_fields_set,
        # Omission preserves (CHG-0017): only the transport can tell an absent `cases` key
        # from a present one, so that fact is read here and passed down, rather than
        # guessed from the parsed value inside the domain.
        cases_supplied="cases" in payload.model_fields_set,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.questions"),
        "question.created",
        request_id=context.request_id,
        question_id=str(question.question_id),
        question_version_id=str(version.question_version_id),
        status_code=201,
    )
    return serialize_public(question_to_public(question, [], [version]))


@router.get("/api/v1/questions/coverage")
def question_tag_coverage_endpoint(
    request: Request,
    course_id: Annotated[uuid.UUID, Query(description="Course whose bank is summarised")],
    published_only: bool = True,
) -> dict:
    """Per-tag question counts for one course (``RQ-B-3-20``, ``CHG-0020``).

    Declared **before** ``/api/v1/questions/{question_id}`` on purpose: as a literal
    sibling of a parameterised path it would otherwise be parsed as a ``question_id`` and
    answered with a validation error instead of the aggregate -- the trap B2's
    ``quality-summary`` documents.

    Counts only. The response carries no ``question_id`` list, because a distribution with
    ids attached is a way to walk the bank one question at a time rather than a way to see
    its shape. ``published_only`` defaults to ``true`` for the same reason it does on
    ``listQuestions``: a caller that forgets the parameter must see less, never more.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    coverage = question_tag_coverage(
        dependencies.database,
        principal=context.principal,
        course_id=course_id,
        published_only=published_only,
    )
    log_event(
        get_logger("api.questions"),
        "question.tag-coverage.served",
        request_id=context.request_id,
        course_id=str(course_id),
        attempt_count=coverage.total_questions,
        status_code=200,
    )
    return serialize_public(coverage_to_public(coverage))


@router.get("/api/v1/questions/{question_id}")
def get_question_endpoint(request: Request, question_id: uuid.UUID) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    question = get_question(
        dependencies.database, principal=context.principal, question_id=question_id
    )
    assets, versions = _question_extras(dependencies, context, question)
    return serialize_public(question_to_public(question, assets, versions))


@router.patch("/api/v1/questions/{question_id}")
def update_question_endpoint(
    request: Request,
    question_id: uuid.UUID,
    payload: QuestionDraftInputModel,
    as_new_version: bool = False,
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    question, version = update_question(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        question_id=question_id,
        payload=payload,
        as_new_version=as_new_version,
        # Presence, not truthiness: `revision_note: null` is still the caller stating
        # something about the write, and the write path answers `422` rather than
        # ignoring a field it cannot honour.
        revision_note_supplied="revision_note" in payload.model_fields_set,
        # Same fact about `cases` (CHG-0017): absent means "keep the stored cases", so a
        # caller that only renames a question never has to send hidden cases back.
        cases_supplied="cases" in payload.model_fields_set,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.questions"),
        "question.draft.saved",
        request_id=context.request_id,
        question_id=str(question_id),
        question_version_id=str(version.question_version_id),
        status_code=200,
    )
    return serialize_public(question_to_public(question, [], [version]))


@router.post("/api/v1/questions/{question_id}/authoring-assistance")
def authoring_assistance_endpoint(
    request: Request, question_id: uuid.UUID, payload: AuthoringAssistanceRequestModel
) -> dict:
    """Model advice about a draft (``RQ-T-10``).

    Writes nothing: there is no code path from here into the question bank, and the
    response shape has no field that could carry protected content. With no provider
    configured the refusal names the missing configuration instead of pretending to
    answer.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    result = authoring_assistance(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        question_id=question_id,
        topics=tuple(payload.topics),
        notes=payload.notes,
    )
    log_event(
        get_logger("api.questions"),
        "authoring.assistance.served",
        request_id=context.request_id,
        question_id=str(question_id),
        status_code=200,
    )
    return serialize_public(authoring_assistance_to_public(question_id, result))


@router.get("/api/v1/questions/{question_id}/diagnostics")
def question_diagnostics_endpoint(request: Request, question_id: uuid.UUID) -> dict:
    """Deterministic comparison-policy findings for the version a teacher is shown.

    The findings are rules, not advice, and they quote no expected output - which is why
    this can be rendered next to a version whose content the caller never reads. An empty
    list is a result: every rule ran and none fired.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    version_id, report = question_diagnostics(
        dependencies.database, principal=context.principal, question_id=question_id
    )
    log_event(
        get_logger("api.questions"),
        "question.diagnostics.served",
        request_id=context.request_id,
        question_id=str(question_id),
        question_version_id=str(version_id),
        attempt_count=len(report.findings),
        status_code=200,
    )
    return serialize_public(diagnostics_to_public(question_id, report))


@router.get("/api/v1/questions/{question_id}/statistics")
def question_statistics_endpoint(
    request: Request,
    question_id: uuid.UUID,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    question_version_id: uuid.UUID | None = None,
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    result = question_statistics(
        dependencies.database,
        principal=context.principal,
        question_id=question_id,
        from_at=_parse_timestamp(from_, "from"),
        to_at=_parse_timestamp(to, "to"),
        question_version_id=question_version_id,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.statistics"),
        "statistics.served",
        request_id=context.request_id,
        question_id=str(question_id),
        attempt_count=result.attempt_count,
        status_code=200,
    )
    return serialize_public(statistics_to_public(result))


@router.post("/api/v1/questions/{question_id}/assets", status_code=201)
def upload_asset_endpoint(
    request: Request, question_id: uuid.UUID, payload: AssetUploadInputModel
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    asset = upload_stem_asset(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        question_id=question_id,
        payload=payload,
        now=dependencies.now,
    )
    return serialize_public(
        StemAssetUploadResult(
            asset_id=asset.asset_id,
            content_type=asset.content_type,
            content_sha256=asset.content_sha256,
            size_bytes=asset.size_bytes,
            alt_text=asset.alt_text,
        )
    )


@router.get("/api/v1/questions/{question_id}/assets/{asset_id}")
def get_asset_endpoint(request: Request, question_id: uuid.UUID, asset_id: uuid.UUID) -> Response:
    context = _context(request)
    dependencies = _dependencies(request)
    asset = load_asset_for_delivery(
        dependencies.database,
        principal=context.principal,
        question_id=question_id,
        asset_id=asset_id,
    )
    log_event(
        get_logger("api.assets"),
        "asset.served",
        request_id=context.request_id,
        question_id=str(question_id),
        status_code=200,
    )
    return Response(
        content=asset.content,
        media_type=asset.content_type,
        headers=_asset_headers(asset.content_sha256),
    )


def _asset_headers(content_sha256: str) -> dict[str, str]:
    """Headers that keep a stored graphic from ever acting as a document."""
    return {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline",
        "Cache-Control": "private, max-age=60",
        "ETag": f'"{content_sha256}"',
    }


# --------------------------------------------------------------------------
# Question versions
# --------------------------------------------------------------------------


@router.get("/api/v1/question-versions/{question_version_id}")
def get_version_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    version = get_version(
        dependencies.database,
        principal=context.principal,
        question_version_id=question_version_id,
    )
    return serialize_public(version_to_public(version, _assets_of_version(dependencies, version)))


def _assets_of_version(dependencies: B3Dependencies, version) -> list:
    """Graphics attached to one specific version (not the question's current ones)."""
    from ..infra.repositories import QuestionBankRepository

    with dependencies.database.app_session() as session:
        bank = QuestionBankRepository(session)
        assets = bank.list_assets_for_version(version.question_version_id)
        for asset in assets:
            session.expunge(asset)
        return assets


@router.post("/api/v1/question-versions/{question_version_id}/publish")
def publish_version_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    version, _content_digest, _package_digest = publish_version(
        dependencies.database,
        principal=context.principal,
        question_version_id=question_version_id,
        now=dependencies.now,
        correlation_id=context.correlation_id,
    )
    log_event(
        get_logger("api.versions"),
        "question.version.published",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        status_code=200,
    )
    return serialize_public(version_to_public(version))


@router.post("/api/v1/question-versions/{question_version_id}/archive")
def archive_version_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    version = archive_version(
        dependencies.database,
        principal=context.principal,
        question_version_id=question_version_id,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.versions"),
        "question.version.archived",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        status_code=200,
    )
    return serialize_public(version_to_public(version))


@router.post("/api/v1/question-versions/{question_version_id}/fixtures", status_code=201)
def upload_fixture_endpoint(
    request: Request, question_version_id: uuid.UUID, payload: FixtureUploadInputModel
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    fixture = upload_fixture(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        question_version_id=question_version_id,
        payload=payload,
        now=dependencies.now,
    )
    return serialize_public(
        FixtureUploadResult(
            fixture_id=fixture.fixture_id,
            path=fixture.path,
            mount=fixture.mount,
            content_sha256=fixture.content_sha256,
            size_bytes=fixture.size_bytes,
        )
    )


@router.post(
    "/api/v1/question-versions/{question_version_id}/attestations", status_code=201
)
def record_reference_attestation_endpoint(
    request: Request, question_version_id: uuid.UUID, payload: ReferenceAttestationInputModel
) -> dict:
    """INTERNAL. Records a verifier's claim; B3 executes nothing.

    Service authentication is required because there is no user on whose behalf a
    verifier reports, and because the declared digest is cross-checked against content
    B3 hashes itself - which means the handler reads reference answers and reads them
    on the protected role.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    require_service(context.principal)
    attestation = record_reference_attestation(
        dependencies.database,
        principal=context.principal,
        question_version_id=question_version_id,
        content_digest=payload.content_digest,
        verifier=payload.verifier,
        cases_total=payload.cases_total,
        cases_passed=payload.cases_passed,
        verified_at=payload.verified_at,
        notes=payload.notes,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.internal"),
        "reference.attestation.recorded",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        content_digest=attestation.content_digest,
        status_code=201,
    )
    return serialize_public(attestation_to_public(attestation))


@router.get("/api/v1/question-versions/{question_version_id}/evaluation-package")
def evaluation_package_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    """INTERNAL. Service-to-service only; carries hidden cases and expected outputs."""
    context = _context(request)
    dependencies = _dependencies(request)
    require_service(context.principal)
    package = load_evaluation_package(
        dependencies.database, question_version_id=question_version_id
    )
    log_event(
        get_logger("api.internal"),
        "evaluation.package.served",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        package_digest=package.package_digest,
        status_code=200,
    )
    # Deliberately NOT serialize_public: this is the internal DTO's own wire form.
    return package.to_wire()


@router.get("/api/v1/question-versions/{question_version_id}/reference-answer")
def reference_answer_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    """INTERNAL. The version's own reference answer, for B2's verification path (``CHG-0023``).

    Protected content, ``serviceToken`` only -- the same scheme the evaluation package uses,
    so no new credential and no new trust relationship. A teacher or a student must never
    read this: the authoring write path is where a teacher encounters the answer, and a
    student encounters it nowhere at all. ``require_service`` is therefore the first thing
    this handler does, before any content is touched, so a refused caller learns only that
    the endpoint is restricted.

    The response is the internal DTO's own wire form rather than the public serializer,
    which refuses the key name ``reference_answer`` by design. Its five declared keys are the
    whole of what travels: no hidden-case expectations, no case content, no fixture bytes,
    and the answer is not written to the log line either.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    require_service(context.principal)
    delivery = load_reference_answer(
        dependencies.database, question_version_id=question_version_id
    )
    log_event(
        get_logger("api.internal"),
        "reference.answer.served",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        content_digest=delivery.content_digest,
        language=delivery.language,
        adapter_kind=delivery.adapter_kind,
        status_code=200,
    )
    return delivery.to_wire()


# --------------------------------------------------------------------------
# Assignments
# --------------------------------------------------------------------------


@router.get("/api/v1/assignments")
def list_assignments_endpoint(
    request: Request,
    cursor: str | None = None,
    limit: int | None = None,
    course_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    resolved_limit = clamp_limit(limit)
    rows, has_more = list_assignment_versions(
        dependencies.database,
        principal=context.principal,
        course_id=course_id,
        section_id=section_id,
        limit=resolved_limit,
    )
    items = [
        serialize_public(assignment_to_public(row, version_ids)) for row, version_ids in rows
    ]
    next_cursor = None
    if has_more and rows:
        last = rows[-1][0]
        next_cursor = encode_cursor(last.created_at, last.assignment_id)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/api/v1/assignments", status_code=201)
def create_assignment_endpoint(request: Request, payload: AssignmentCreateInputModel) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    assignment, items = create_assignment(
        dependencies.database, principal=context.principal, payload=payload, now=dependencies.now
    )
    log_event(
        get_logger("api.assignments"),
        "assignment.created",
        request_id=context.request_id,
        assignment_id=str(assignment.assignment_id),
        course_id=str(assignment.course_id),
        status_code=201,
    )
    result: AssignmentPublic = assignment_to_public(
        assignment, [item.question_version_id for item in items]
    )
    return serialize_public(result)
