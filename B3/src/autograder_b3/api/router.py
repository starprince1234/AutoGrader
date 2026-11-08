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
from fastapi.responses import JSONResponse

from ..auth import require_authenticated, require_service
from ..domain import (
    AssignmentView,
    archive_version,
    authoring_assistance,
    create_assignment,
    create_question,
    delete_version,
    discard_generation,
    generation_capabilities,
    get_assignment_detail,
    get_question,
    get_version,
    list_assignment_versions,
    list_questions,
    load_asset_for_delivery,
    load_editable_draft_content,
    load_evaluation_package,
    load_fixture_contents,
    load_learning_context,
    load_reference_answer,
    publish_version,
    question_diagnostics,
    question_statistics,
    question_tag_coverage,
    read_generation,
    retry_assignment_notification,
    run_batch_operation,
    record_reference_attestation,
    request_generation,
    update_question,
    upload_fixture,
    upload_stem_asset,
)
from ..domain.tags import validate_tags
from ..dto.internal import (
    AssetUploadInputModel,
    AssignmentAudience,
    AssignmentCreateInputModel,
    AuthoringAssistanceRequestModel,
    FixtureUploadInputModel,
    QuestionDraftGenerationRequestModel,
    QuestionDraftInputModel,
    QuestionVersionBatchInputModel,
    ReferenceAttestationInputModel,
)
from ..dto.public import (
    AssignmentPublic,
    FixtureUploadResult,
    StemAssetUploadResult,
)
from ..errors import ServiceUnavailable, UnprocessableEntity
from ..logging import get_logger, log_event
from .deps import B3Dependencies, RequestContext, get_dependencies, request_context
from .pagination import clamp_limit, decode_cursor, encode_cursor
from .serializers import (
    assignment_to_public,
    attestation_to_public,
    authoring_assistance_to_public,
    coverage_to_public,
    diagnostics_to_public,
    learning_context_to_public,
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
    q: str | None = None,
) -> dict:
    context = _context(request)
    dependencies = _dependencies(request)
    if language is not None and language not in ("python", "bash"):
        raise UnprocessableEntity("`language` must be 'python' or 'bash'")
    # 片段检索（CHG-0030）：空白等于"不搜索"，超长则拒绝——不能让一个超长 LIKE 打到库里。
    search = (q or "").strip()
    if len(search) > 200:
        raise UnprocessableEntity("`q` accepts at most 200 characters")
    search = search or None
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
        query=search,
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

    Which assets: a **teacher** sees the latest version's, which is the content the row
    describes (title and stem come from the question's current state). Those include the
    assets that are not bound to any version yet, because ``publish`` is what binds them --
    without that, a draft with an uploaded image reported "no image" in the list. A
    **student** keeps seeing only the published version's bound assets.
    """
    from ..infra.repositories import QuestionBankRepository

    with dependencies.database.app_session() as session:
        bank = QuestionBankRepository(session)
        versions = bank.list_versions(question.question_id)
        if not versions:
            return [], []
        published = next((item for item in versions if item.status == "published"), None)
        is_staff = context.principal is not None and context.principal.has_role(
            "teacher", "admin"
        )
        target = versions[0] if is_staff else published
        assets: list = []
        if target is not None:
            assets = list(bank.list_assets_for_version(target.question_version_id))
        if is_staff:
            seen = {asset.asset_id for asset in assets}
            assets.extend(
                asset
                for asset in bank.list_unbound_assets(question.question_id)
                if asset.asset_id not in seen
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
    generation_id: Annotated[
        uuid.UUID | None,
        Query(description="AI-FLOW-01A candidate this draft was edited from, if any"),
    ] = None,
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
        # Same fact about `execution`: an omitted key keeps the stored declaration, while an
        # explicit `null` resets it to per_case. Only the transport can tell them apart.
        execution_supplied="execution" in payload.model_fields_set,
        generation_id=generation_id,
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
        # Absent means "keep the stored execution declaration"; explicit `null` resets it.
        execution_supplied="execution" in payload.model_fields_set,
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


# --------------------------------------------------------------------------
# AI draft generation (AI-FLOW-01A)
# --------------------------------------------------------------------------


@router.get("/api/v1/question-draft-generations/capabilities")
def generation_capabilities_endpoint(
    request: Request,
    course_id: Annotated[uuid.UUID, Query(description="Course whose bank the draft is for")],
) -> dict:
    """Provider availability, the deployment's input limits, and the caller's own budget.

    Declared **before** the parameterised sibling: a literal segment under a collection that
    also has a ``{generation_id}`` child is otherwise parsed as an id and answered with a
    validation error (the same trap ``questions/coverage`` documents).

    Nothing here is a secret: no provider URL, no credential, and no other teacher's usage.
    The limits come from this deployment's configuration so a client can show the number the
    server will actually enforce instead of a constant that happens to be right somewhere.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    capabilities = generation_capabilities(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        course_id=course_id,
        now=dependencies.now,
    )
    return capabilities.to_wire()


@router.post("/api/v1/question-draft-generations")
def create_generation_endpoint(
    request: Request,
    payload: QuestionDraftGenerationRequestModel,
    course_id: Annotated[uuid.UUID, Query(description="Course the draft is being written for")],
) -> Response:
    """Turn a teaching objective into an editable candidate (``RQ-T-10``, AI-FLOW-01A).

    Writes a **candidate**, never a question: no ``question``, ``question_version`` or
    ``test_case`` is created here, so the bank is unchanged until the teacher edits the
    candidate and saves it through ``createQuestion`` with ``generation_id``.

    The response contains a proposed answer and proposed hidden cases, so it is
    teacher-only and does not go through the public serializer. Every refusal happens in a
    fixed order -- role/scope, input, provider, budget -- so an unauthorized caller cannot
    learn this deployment's model state from a status code.

    ``200`` rather than ``201``: the body is the **outcome** of an invocation, and two of the
    four outcomes (``unavailable`` and, for a failed parse, ``failed``) persist nothing at
    all. Declaring "Created" for a request that created nothing would be a status code that
    contradicts the body; one code with a discriminated ``status`` keeps the client's read
    honest and gives it a single shape to handle.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    generation = request_generation(
        dependencies.database,
        dependencies.settings,
        principal=context.principal,
        course_id=course_id,
        payload=payload,
        provider=dependencies.draft_provider,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.generation"),
        "question.draft.generated",
        request_id=context.request_id,
        course_id=str(course_id),
        generation_id=(
            None if generation.generation_id is None else str(generation.generation_id)
        ),
        outcome=generation.status,
        attempt_count=len(generation.warnings),
        status_code=200,
    )
    return JSONResponse(status_code=200, content=generation.to_wire())


@router.get("/api/v1/question-draft-generations/{generation_id}")
def get_generation_endpoint(request: Request, generation_id: uuid.UUID) -> Response:
    """Restore a candidate after a refresh.

    This restores the **original** candidate, never the teacher's unsaved edits: those live
    in the page and are lost on leave, which the client must warn about before navigating.
    A record that expired, was discarded, or belongs to someone else is refused -- an id is
    an identifier, not a credential.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    generation = read_generation(
        dependencies.database,
        principal=context.principal,
        generation_id=generation_id,
        now=dependencies.now,
    )
    return JSONResponse(content=generation.to_wire())


@router.delete("/api/v1/question-draft-generations/{generation_id}", status_code=204)
def delete_generation_endpoint(request: Request, generation_id: uuid.UUID) -> Response:
    """Discard a candidate: content gone, de-identified ledger row kept.

    The only operation that removes an unaccepted proposal. Closing the preview panel must
    not do this, so it is a separate, explicit action.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    discard_generation(
        dependencies.database,
        principal=context.principal,
        generation_id=generation_id,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.generation"),
        "question.draft.discarded",
        request_id=context.request_id,
        generation_id=str(generation_id),
        status_code=204,
    )
    return Response(status_code=204)


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


@router.get("/api/v1/question-versions/{question_version_id}/learning-context")
def learning_context_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    """INTERNAL. The public half of one published version, for B2's learning hints.

    Service-to-service only (``AI-FLOW-01B``): B2 builds hints from a student's own completed
    run and needs to know what the exercise teaches, not how it is judged. ``require_service``
    is the first thing that happens, before the version is even looked up, so a teacher or a
    student learns only that this operation is restricted.

    The response is a separate schema with five fields -- title, stem, language, tags,
    adapter kind. It deliberately carries no reference answer, no case, no expected output
    and no fixture: a model that knew the expected output could repeat it into a hint a
    student reads, which would turn the hint feature into a leak.

    A draft is refused with ``409`` rather than served: an evaluation run can only reference
    a published version, so "hints for an unpublished version" is not a state the consumer
    can legitimately be in.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    learning_context = load_learning_context(
        dependencies.database,
        principal=context.principal,
        question_version_id=question_version_id,
    )
    log_event(
        get_logger("api.internal"),
        "question.learning-context.served",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        status_code=200,
    )
    return serialize_public(learning_context_to_public(learning_context))


@router.post("/api/v1/question-versions/batch-operations")
def run_batch_operation_endpoint(
    request: Request, payload: QuestionVersionBatchInputModel
) -> Response:
    """Delete or publish many draft versions in one request (``CHG-0029``).

    Declared **before** the ``{question_version_id}`` routes so the literal segment wins
    the match. Every item is attempted independently and reported independently; the
    response is always ``200`` with per-item outcomes, because "3 of 12 refused" is a
    result a teacher has to be able to read, not an error to guess at.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    # 批量操作逐项吞掉业务错误（"3 项没成、9 项成功"是正常结果），所以"未认证"必须在这里
    # 就挡住：否则匿名调用会拿到 200 + 每项 `forbidden`，把一个鉴权问题伪装成业务结论。
    require_authenticated(context.principal)
    outcomes = run_batch_operation(
        dependencies.database,
        principal=context.principal,
        operation=payload.operation,
        question_version_ids=payload.question_version_ids,
        now=dependencies.now,
        correlation_id=context.correlation_id,
    )
    log_event(
        get_logger("api.versions"),
        "question.version.batch_served",
        request_id=context.request_id,
        operation=payload.operation,
        total=len(outcomes),
        applied=sum(1 for item in outcomes if item.applied),
        status_code=200,
    )
    return JSONResponse(
        status_code=200,
        content={"results": [item.to_wire() for item in outcomes]},
    )


@router.delete("/api/v1/question-versions/{question_version_id}", status_code=204)
def delete_question_version_endpoint(
    request: Request, question_version_id: uuid.UUID
) -> Response:
    """Delete a draft version (and its question, when that was the last one).

    The single-item counterpart of the batch below: same rules, same refusals. A version
    that has been published is never deleted here - it is archived - because its content
    is what past evidence was produced from.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    delete_version(
        dependencies.database,
        principal=context.principal,
        question_version_id=question_version_id,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.versions"),
        "question.version.draft_deleted",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        status_code=204,
    )
    return Response(status_code=204)


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
            question_version_id=fixture.question_version_id,
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


@router.get("/api/v1/question-versions/{question_version_id}/fixture-contents")
def fixture_contents_endpoint(
    request: Request,
    question_version_id: uuid.UUID,
    mount: str = "input",
) -> dict:
    """INTERNAL. Input-fixture bytes for an executed version (design baseline §5).

    Service-to-service only, and ``require_service`` runs before the version is looked up (and
    before ``mount`` is even interpreted, which is why it is a plain string here rather than a
    ``Literal``: the enum is validated by the domain function *after* the caller's credential
    has been established). The response deliberately serves ``mount=input`` only:
    ``expected_output`` comparison needs the digest the package already declares, and a second
    byte channel would widen protected content in flight for no benefit. The bundle is bounded
    (1 MiB/item, 4 MiB total) and refused rather than truncated.

    The log line carries the version, the item count, the total size and the status code, and
    nothing that identifies a path's content.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    require_service(context.principal)
    bundle = load_fixture_contents(
        dependencies.database, question_version_id=question_version_id, mount=mount
    )
    log_event(
        get_logger("api.internal"),
        "fixture.contents.served",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        item_count=len(bundle.items),
        total_size=sum(item.size_bytes for item in bundle.items),
        status_code=200,
    )
    # Deliberately NOT serialize_public: this is protected content on the internal channel.
    return bundle.to_wire()


@router.get("/api/v1/question-versions/{question_version_id}/draft-content")
def draft_content_endpoint(request: Request, question_version_id: uuid.UUID) -> dict:
    """A version's own reference answer and cases, for the authoring page.

    Teacher-only (see ``load_editable_draft_content``): the author has to be able to *correct*
    an existing case after a failed verification, which is impossible from a response that never
    carries protected content back. A **published** version is readable too, because
    「另存为新版本」 must open on the full existing content rather than an empty case list;
    students are still refused before any content is read, and nothing here can mutate a
    published version.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    content = load_editable_draft_content(
        dependencies.database,
        question_version_id=question_version_id,
        principal=context.principal,
    )
    log_event(
        get_logger("api.question_versions"),
        "draft.content.served",
        request_id=context.request_id,
        question_version_id=str(question_version_id),
        case_count=len(content.cases),
        status_code=200,
    )
    return content.to_wire()


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


def _assignment_public(view: AssignmentView) -> AssignmentPublic:
    """Project one assignment view; the ordered question list is passed explicitly."""
    return assignment_to_public(view.assignment, list(view.question_version_ids))


@router.get("/api/v1/assignments")
def list_assignments_endpoint(
    request: Request,
    cursor: str | None = None,
    limit: int | None = None,
    course_id: uuid.UUID | None = None,
) -> dict:
    """Page of assignments the caller may read, keyset-paginated by ``(created_at, id)``.

    An assignment is course-level (v0.32.0), so the reader filter is a course set: the
    teacher's token scope, or -- for a student -- B4's live enrollments, applied **in SQL**
    so a page is never shortened by a post-filter. The cursor is decoded here and passed
    down: a list that accepted ``cursor`` and then ignored it would return the first page on
    every request while advertising a ``next_cursor``, a silent infinite loop for a paging
    client.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    resolved_limit = clamp_limit(limit)
    after = decode_cursor(cursor) if cursor else None
    views, has_more = list_assignment_versions(
        dependencies.database,
        principal=context.principal,
        course_id=course_id,
        limit=resolved_limit,
        directory=dependencies.b4_authorization,
        after=after,
    )
    items = [serialize_public(_assignment_public(view)) for view in views]
    next_cursor = None
    if has_more and views:
        last = views[-1].assignment
        next_cursor = encode_cursor(last.created_at, last.assignment_id)
    log_event(
        get_logger("api.assignments"),
        "assignments.listed",
        request_id=context.request_id,
        status_code=200,
        attempt_count=len(items),
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/api/v1/assignments", status_code=201)
def create_assignment_endpoint(request: Request, payload: AssignmentCreateInputModel) -> dict:
    """Create a course-level assignment from published versions of that course's questions.

    The assignment applies to every section/class/student of the course. Publishing also
    hands the audience to B4; that hand-off is a separate idempotent step after the write
    commits, so a B4 failure never rolls the assignment back -- it shows up as
    ``notification_state = unavailable`` on the returned body, and a teacher can retry.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    view = create_assignment(
        dependencies.database,
        principal=context.principal,
        payload=payload,
        notifier=dependencies.b4_notifier,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.assignments"),
        "assignment.created",
        request_id=context.request_id,
        assignment_id=str(view.assignment.assignment_id),
        course_id=str(view.assignment.course_id),
        status_code=201,
    )
    return serialize_public(_assignment_public(view))


@router.get("/api/v1/assignments/{assignment_id}")
def get_assignment_endpoint(request: Request, assignment_id: uuid.UUID) -> dict:
    """Read one assignment inside the caller's authorization; the detail page must not scan."""
    context = _context(request)
    dependencies = _dependencies(request)
    view = get_assignment_detail(
        dependencies.database,
        principal=context.principal,
        assignment_id=assignment_id,
        directory=dependencies.b4_authorization,
    )
    return serialize_public(_assignment_public(view))


@router.post("/api/v1/assignments/{assignment_id}/notifications")
def schedule_assignment_notifications_endpoint(
    request: Request, assignment_id: uuid.UUID
) -> dict:
    """Schedule (or retry) one assignment's publication notification (teacher/admin).

    Idempotent by assignment on B4's side: a repeat never duplicates recipients or re-sends
    a message, so calling this again is safe. The response is the assignment with its
    updated ``notification_state``; per-recipient delivery status lives in B4, not here --
    scheduling is not delivery.

    A B4 rejection with a declared status (``404``/``409``/``422``) is mapped back onto the
    same status: that answer is about this request, so hiding it behind ``200 unavailable``
    would present a permanently broken retry as a transient one. A transport/5xx failure
    answers ``200`` with ``notification_state = unavailable`` instead, because the
    assignment itself is fine and retrying is the fix.
    """
    context = _context(request)
    dependencies = _dependencies(request)
    view = retry_assignment_notification(
        dependencies.database,
        principal=context.principal,
        assignment_id=assignment_id,
        notifier=dependencies.b4_notifier,
        now=dependencies.now,
    )
    log_event(
        get_logger("api.assignments"),
        "assignment.notification.retried",
        request_id=context.request_id,
        assignment_id=str(assignment_id),
        course_id=str(view.assignment.course_id),
        status_code=200,
    )
    return serialize_public(_assignment_public(view))


@router.get("/api/v1/assignment-audiences/{assignment_id}")
def get_internal_assignment_endpoint(request: Request, assignment_id: uuid.UUID) -> dict:
    """INTERNAL. The assignment audience B2 needs to admit or refuse a submission.

    ``serviceToken`` only. Two ordering decisions:

    * an **unconfigured** service credential is ``503``, checked before the caller's
      header is even interpreted: an endpoint that silently accepted everyone because the
      deployment forgot the secret would be the worst possible failure mode;
    * the credential is verified before the assignment is looked up, so a caller without
      the service token learns nothing about which ids exist.

    The response is the internal DTO's own wire form (like the reference-answer channel);
    it deliberately omits timestamps and never carries a student/roster fact -- since
    v0.32.0 the audience is course-level and only B4 is authoritative for current
    enrollment, so B2 combines this answer with its own B4 query.
    """
    dependencies = _dependencies(request)
    if dependencies.service_verifier is None:
        raise ServiceUnavailable(
            "the service credential for internal endpoints is not configured"
        )
    context = _context(request)
    require_service(context.principal)
    view = get_assignment_detail(
        dependencies.database,
        principal=context.principal,
        assignment_id=assignment_id,
        directory=dependencies.b4_authorization,
    )
    internal = AssignmentAudience(
        assignment_id=view.assignment.assignment_id,
        course_id=view.assignment.course_id,
        title=view.assignment.title,
        status=view.assignment.status,
        question_version_ids=view.question_version_ids,
    )
    log_event(
        get_logger("api.internal"),
        "assignment.audience.served",
        request_id=context.request_id,
        assignment_id=str(assignment_id),
        attempt_count=len(view.question_version_ids),
        status_code=200,
    )
    return internal.to_wire()
