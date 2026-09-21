"""`POST /api/v1/submissions`（RQ-B-2-10）：接收 → 校验 → 存储 → outbox。

分层：本模块只做 HTTP 关注点（认证头、关联 ID、状态码映射、响应体），业务判定在
`intake.service`，授权判定在 `auth.authorization`。请求体**不**用 Pydantic 模型复述契约字段
（那会产生第二份字段定义），而是直接按 MAIN `submission-envelope` schema 校验原始 JSON。

错误码语义：未认证 `401`；无权限/作用域不符 `403`（**不用** `404` 掩盖）；结构或摘要问题 `422`；
超大小 `413`；依赖不可用 `503`。

## `CHG-0014` 的两条隔离区端点（同一路由族）

`GET /api/v1/intake/quarantines` 与 `POST /api/v1/intake/quarantines/{id}/resolve` 也在这里注册：
它们服务的是**从未成为提交**的那些来件，因此与提交端点同属"接收"这一族（`module.yaml` 的
`/api/v1/intake` 前缀），但业务判定全部在 `intake.quarantine`（本模块只做 HTTP 关注点）。
两条端点的查询参数与请求体都**手工解析**：宿主没安装 `api.errors.install_problem_handlers` 时，
把校验交给 FastAPI 会产生非 RFC 9457 的 422 响应体，而"错误体一律 Problem Details"是本模块的
硬要求（与 `api.grades_routes` 同一形态）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from autograder_b2.api.errors import (
    FORBIDDEN,
    PAYLOAD_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    UNPROCESSABLE,
    Problem,
    problem_response,
)
from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import AuthenticationError
from autograder_b2.domain.envelope import EnvelopeRejected
from autograder_b2.intake.quarantine import (
    InvalidQuarantineCursorError,
    InvalidQuarantineFilterError,
    QuarantineAlreadyResolvedError,
    QuarantineNotAttributableError,
    QuarantineNotFoundError,
    QuarantineRequestError,
    parse_limit,
    parse_reason_code_filter,
    parse_resolved_filter,
    quarantine_page_body,
    quarantine_view_body,
)
from autograder_b2.intake.query import SubmissionNotFoundError, SubmissionView
from autograder_b2.intake.runtime import IntakeRuntime
from autograder_b2.intake.service import SubmissionIdConflict
from autograder_b2.logging.context import bind_context
from autograder_b2.logging.logger import log_dependency_unavailable, log_event

#: 路由层 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.autograder_b2.intake.api")

#: 大小类原因码 → 413；其余（结构/摘要/路径）→ 422。
_OVERSIZE_REASONS = frozenset({"file_too_large", "total_too_large"})
#: 请求体的硬上限（Content-Length 早退用）：文件数上限 × 单文件 base64 长度 + JSON 包装余量。
_BODY_ENVELOPE_OVERHEAD = 64 * 1024


def _expected_max_body_bytes(runtime: IntakeRuntime) -> int | None:
    settings = runtime.settings
    if settings is None:
        return None
    return settings.max_files * ((settings.max_file_bytes // 3 + 1) * 4) + _BODY_ENVELOPE_OVERHEAD


def _correlation_id(request: Request) -> str:
    """入口未携带 `X-Request-ID`/`traceparent` 时生成一个并贯穿日志→数据库→事件。

    `correlation_id` 就是"这一次业务操作"的关联标识，因此它**已经**承载了请求关联；
    不再单独保留一份 `request_id`（那会变成同一事实的两个名字）。
    """
    for header in ("x-request-id", "traceparent"):
        raw = request.headers.get(header)
        if not raw:
            continue
        candidate = raw.strip()
        if header == "traceparent":
            parts = candidate.split("-")
            if len(parts) >= 2:
                candidate = parts[1]
        try:
            return str(uuid.UUID(candidate))
        except ValueError:
            continue
    return str(uuid.uuid4())


def _accepted_body(result: Any) -> dict[str, Any]:
    """契约 `SubmissionAccepted`：字段集合固定，`additionalProperties: false`。"""
    return {
        "submission_id": result.submission_id,
        "state": result.state,
        "evaluation_run_id": result.evaluation_run_id,
        "idempotent_replay": result.idempotent_replay,
        "accepted_at": rfc3339_millis(result.accepted_at),
    }


def build_router(runtime: IntakeRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/submissions", status_code=202)
    async def create_submission(request: Request) -> Response:
        instance = request.url.path
        if not runtime.ready:
            return problem_response(
                Problem(
                    503,
                    "service-unavailable",
                    "Service Unavailable",
                    f"submission intake is not available: {runtime.unavailable_reason}",
                ),
                instance,
            )

        assert runtime.authenticator is not None and runtime.service is not None
        try:
            principal = runtime.authenticator.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return problem_response(UNAUTHORIZED, instance)

        max_body = _expected_max_body_bytes(runtime)
        declared_length = request.headers.get("content-length")
        if (
            max_body is not None
            and declared_length
            and declared_length.isdigit()
            and int(declared_length) > max_body
        ):
            # 早早拒绝超大请求体：不把整段 JSON 读进内存再判。
            return problem_response(PAYLOAD_TOO_LARGE, instance)

        try:
            payload = await request.json()
        except ValueError:
            return problem_response(
                Problem(
                    422,
                    "unprocessable-entity",
                    "Unprocessable Entity",
                    "request body must be a JSON object",
                ),
                instance,
            )
        if not isinstance(payload, dict):
            return problem_response(UNPROCESSABLE, instance)

        correlation_id = _correlation_id(request)
        # 接收阶段绑定日志上下文：`request_id`/`correlation_id` 用**同一个**入口标识（一次请求即一次
        # 业务操作），后续阶段（静态/安全/动态/报告）由编排层沿用同一个 `correlation_id`，于是全链路
        # 可凭它串联（RQ-B-2-99 场景 2-99-C）。只绑标识与阶段名，**不记录请求头原值**。
        with bind_context(
            request_id=correlation_id,
            correlation_id=correlation_id,
            stage="reception",
        ):
            try:
                result = runtime.service.accept(
                    payload,
                    principal=principal,
                    correlation_id=correlation_id,
                )
            except AuthorizationError:
                log_event(
                    logger,
                    "submission.rejected",
                    level=logging.WARNING,
                    fields={"outcome": "denied", "decision": "forbidden"},
                )
                return problem_response(FORBIDDEN, instance)
            except SubmissionIdConflict as conflict:
                return problem_response(
                    Problem(
                        status=422,
                        type_slug="submission-id-conflict",
                        title="Unprocessable Entity",
                        detail=str(conflict),
                    ),
                    instance,
                )
            except EnvelopeRejected as rejected:
                log_event(
                    logger,
                    "submission.rejected",
                    level=logging.WARNING,
                    fields={"outcome": "rejected", "reason_code": rejected.reason},
                )
                problem = (
                    PAYLOAD_TOO_LARGE if rejected.reason in _OVERSIZE_REASONS else UNPROCESSABLE
                )
                return problem_response(
                    Problem(
                        status=problem.status,
                        type_slug=rejected.reason.replace("_", "-"),
                        title=problem.title,
                        detail=rejected.detail,
                    ),
                    instance,
                )
            except SQLAlchemyError as error:
                # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
                # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
                # 日志侧是统一的依赖故障事件。
                # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
                log_dependency_unavailable(
                    logger, dependency="database", error=error, stage="submissions"
                )
                # 依赖不可用 → 503（契约声明的语义）。未预期的异常**不在此吞掉**：让它冒泡由宿主
                # 的统一错误处理器记录并返回 500，避免把"我们出错了"伪装成"依赖不可用"。
                runtime.record_failure()
                return problem_response(SERVICE_UNAVAILABLE, instance)

            log_event(
                logger,
                "submission.accepted",
                fields={
                    "submission_id": result.submission_id,
                    "outcome": "accepted",
                    "class": "api",
                },
            )
            return JSONResponse(status_code=202, content=_accepted_body(result))

    @router.get("/api/v1/submissions/{submission_id}")
    async def get_submission(submission_id: str, request: Request) -> Response:
        instance = request.url.path
        if not runtime.ready:
            return problem_response(
                Problem(
                    503,
                    "service-unavailable",
                    "Service Unavailable",
                    f"submission intake is not available: {runtime.unavailable_reason}",
                ),
                instance,
            )
        assert runtime.authenticator is not None and runtime.query is not None
        try:
            principal = runtime.authenticator.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return problem_response(UNAUTHORIZED, instance)

        try:
            view = runtime.query.get(submission_id, principal=principal)
        except SubmissionNotFoundError:
            return problem_response(
                Problem(404, "not-found", "Not Found", "The submission does not exist."), instance
            )
        except AuthorizationError:
            return problem_response(FORBIDDEN, instance)
        except SQLAlchemyError as error:
            # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
            # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
            # 日志侧是统一的依赖故障事件。
            # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="submissions"
            )
            # 库结构落后或不可用 → 依赖不可用（503），而不是把内部错误抛成 500。
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)

        return JSONResponse(status_code=200, content=_submission_view_body(view))

    @router.get("/api/v1/intake/quarantines")
    async def list_intake_quarantines(
        request: Request,
        reason_code: str | None = None,
        resolved: str | None = None,
        cursor: str | None = None,
        limit: str | None = None,
    ) -> Response:
        """列出隔离记录（`CHG-0014` 的读出口）：教师能重新找到那封"没能变成提交"的邮件。

        过滤与游标的**判定**只有一处（`intake.quarantine`）：`resolved` 的布尔语义、`limit` 的边界、
        `reason_code` 的长度与游标形态都在那里，本层只做状态码映射。
        """
        instance = request.url.path
        if not runtime.ready:
            return _not_available(instance, runtime.unavailable_reason)
        assert runtime.authenticator is not None and runtime.quarantines is not None
        try:
            principal = runtime.authenticator.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return problem_response(UNAUTHORIZED, instance)

        try:
            size = parse_limit(limit)
            wanted_reason = parse_reason_code_filter(reason_code)
            wanted_resolved = parse_resolved_filter(resolved)
        except InvalidQuarantineFilterError as error:
            return _unprocessable(instance, str(error))

        try:
            page = runtime.quarantines.list_quarantines(
                principal=principal,
                reason_code=wanted_reason,
                resolved=wanted_resolved,
                limit=size,
                cursor=cursor or None,
            )
        except AuthorizationError:
            return problem_response(FORBIDDEN, instance)
        except (InvalidQuarantineFilterError, InvalidQuarantineCursorError) as error:
            return _unprocessable(instance, str(error))
        except SQLAlchemyError as error:
            # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
            # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
            # 日志侧是统一的依赖故障事件。
            # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="intake quarantines"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)
        return JSONResponse(status_code=200, content=quarantine_page_body(page))

    @router.post("/api/v1/intake/quarantines/{intake_quarantine_id}/resolve")
    async def resolve_intake_quarantine(intake_quarantine_id: str, request: Request) -> Response:
        """把一条隔离记录归属给某个学生（`CHG-0014`）：**只记录归属，不触发评测**。

        两条不可协商的性质落在 `intake.quarantine.IntakeQuarantineService.resolve`：人工路径重跑
        当前数据下能重跑的全部校验（角色/作用域 + 内容类闸门），且全部写入只有那一条 `UPDATE`。
        """
        instance = request.url.path
        if not runtime.ready:
            return _not_available(instance, runtime.unavailable_reason)
        assert runtime.authenticator is not None and runtime.quarantines is not None
        try:
            principal = runtime.authenticator.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return problem_response(UNAUTHORIZED, instance)

        try:
            payload = await request.json()
        except ValueError:
            return _unprocessable(instance, "request body must be a JSON object")
        if not isinstance(payload, dict):
            return _unprocessable(instance, "request body must be a JSON object")
        # 契约 `additionalProperties: false`：未知字段一律拒绝（**不**回显取值，取值可能含内容）。
        unknown = sorted(set(payload) - {"student_id", "note"})
        if unknown:
            return _unprocessable(instance, "request body has unknown fields: " + ", ".join(unknown))
        if "student_id" not in payload:
            return _unprocessable(instance, "student_id is required")

        try:
            view = runtime.quarantines.resolve(
                principal=principal,
                intake_quarantine_id=intake_quarantine_id,
                student_id=payload["student_id"],
                note=payload.get("note"),
            )
        except AuthorizationError:
            log_event(
                logger,
                "intake_quarantine.rejected",
                level=logging.WARNING,
                fields={"outcome": "denied", "decision": "forbidden"},
            )
            return problem_response(FORBIDDEN, instance)
        except QuarantineRequestError as error:
            return _unprocessable(instance, str(error))
        except QuarantineNotAttributableError as error:
            log_event(
                logger,
                "intake_quarantine.rejected",
                level=logging.WARNING,
                fields={"outcome": "rejected", "reason_code": str(error)},
            )
            # 人工路径**不得**绕过自动路径的信封校验：报文本身被拒的那些记录，命名一个学生修不好它。
            return _unprocessable(
                instance,
                "this quarantined message was rejected by the automatic path"
                f" (reason_code={error}); attribution supplies an identity, it does not repair a"
                " rejected envelope",
            )
        except QuarantineNotFoundError:
            return problem_response(
                Problem(
                    404, "not-found", "Not Found", "The quarantined intake record does not exist."
                ),
                instance,
            )
        except QuarantineAlreadyResolvedError:
            return problem_response(
                Problem(
                    409,
                    "conflict",
                    "Conflict",
                    "The quarantined intake record has already been resolved.",
                ),
                instance,
            )
        except SQLAlchemyError as error:
            # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
            # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
            # 日志侧是统一的依赖故障事件。
            # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="intake quarantines"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)

        # 归属是**人的决定**：只记结论，不记 `resolution_note` 的自由文本（它可能带 PII）。
        log_event(
            logger,
            "intake_quarantine.resolved",
            fields={"outcome": "resolved", "status": "resolved"},
        )
        return JSONResponse(status_code=200, content=quarantine_view_body(view))

    return router


def _not_available(instance: str, reason: str | None) -> JSONResponse:
    """隔离区两条端点的 503（依赖未装配）：原因取自运行时，不含内部地址与凭据。

    与文件内既有两条端点的 503 同口径（同一条 `unavailable_reason`），只是抽成一个函数：
    这两条端点各有一个 `ready` 前置检查，重复那四行会把同一句话抄两遍。
    """
    return problem_response(
        Problem(
            503,
            "service-unavailable",
            "Service Unavailable",
            f"submission intake is not available: {reason}",
        ),
        instance,
    )


def _unprocessable(instance: str, detail: str) -> JSONResponse:
    return problem_response(
        Problem(422, "unprocessable-entity", "Unprocessable Entity", detail), instance
    )


def _submission_view_body(view: SubmissionView) -> dict[str, Any]:
    """契约 `SubmissionView`：字段集合固定；`files` 只含路径/摘要/字节数，绝不含内容。"""
    return {
        "submission_id": view.submission_id,
        "student_id": view.student_id,
        "course_id": view.course_id,
        "section_id": view.section_id,
        "question_version_id": view.question_version_id,
        "state": view.state,
        "intake": view.intake,
        # Lets a client reach the progress stream: it is addressed by run id, and the run
        # is created only after acceptance, so the 202 alone cannot carry it.
        "evaluation_run_id": view.evaluation_run_id,
        "files": [
            {
                "path": item.path,
                "content_sha256": item.content_sha256,
                "size_bytes": item.size_bytes,
            }
            for item in view.files
        ],
        "state_history": [
            {
                "from_state": entry.from_state,
                "to_state": entry.to_state,
                "occurred_at": rfc3339_millis(entry.occurred_at),
                "reason": entry.reason,
            }
            for entry in view.state_history
        ],
        "created_at": rfc3339_millis(view.created_at),
    }
