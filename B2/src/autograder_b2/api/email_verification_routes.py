"""`email_delivery` 所有权证明的两条 HTTP 端点（设计 `assignment-workflow.md` §6.3；契约 `b2.yaml`）。

| 端点 | 语义 |
|---|---|
| `POST /api/v1/email-verification/challenges` | 已认证学生为**一个作业**申请一次性核验码（201，明文码只出现这一次） |
| `GET /api/v1/email-verification/challenges/{challenge_id}` | 只有**所有者**可读的状态反馈（pending/received/rejected(reason)/expired + 按题提交） |

分层：本模块只做 HTTP 关注点（认证头、请求体形状、状态码映射、Problem Details、时间序列化）；
签发/状态查询/消费全部在 `intake.email_verification`，认证复用 `auth.tokens`，授权复用
`auth.authorization.require_submission_identity`（令牌身份层；不新造权限模型），当前课程归属由
B4 的权威授权查询判定（`integration.b4_authorization`）；作业本身经
`EmailVerificationService.assignment_directory`（B3 internal 端点）判定。

错误码语义与同模块其它端点一致：未认证 `401`；已认证但越权/非受众/非所有者 `403`；请求体不合法
`422`；挑战或作业不存在 `404`；作业已不在 open 状态 `409`；依赖不可用（库不可达、核验服务未装配、
作业目录未配置/B3 不可达）`503`。**不用 `404` 掩盖 `403`**。

## 不得泄漏的东西

- 明文核验码只在 `201` 响应体里出现一次，**不写日志、不写事件、不写库**（库里只有 SHA-256）；
- 响应与日志都不含发件地址、主题或正文；状态里只有状态、稳定原因码、时间与提交标识。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from autograder_b2.api.errors import (
    FORBIDDEN,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    Problem,
    problem_response,
)
from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import AuthenticationError
from autograder_b2.intake.email_verification import (
    ChallengeAssignmentClosedError,
    ChallengeAssignmentNotFoundError,
    ChallengeNotFoundError,
    ChallengeRequestError,
    ChallengeStatus,
    EmailVerificationService,
)
from autograder_b2.integration.assignments import AssignmentUnavailable
from autograder_b2.integration.b4_authorization import AuthorizationUnavailable
from autograder_b2.logging.logger import log_dependency_unavailable, log_event

#: 路由层 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.autograder_b2.api.email_verification")

#: 挑战请求体的字段集合（契约 `EmailVerificationChallengeRequest`，`additionalProperties: false`）。
_CHALLENGE_REQUEST_FIELDS = ("course_id", "section_id", "assignment_id")


def build_router(service: EmailVerificationService | None) -> APIRouter:
    """构造两条核验码端点；`service=None` 表示该部署没有可用核验服务（两条端点均 503）。"""
    router = APIRouter()

    @router.post("/api/v1/email-verification/challenges", status_code=201)
    async def create_email_verification_challenge(request: Request) -> JSONResponse:
        instance = request.url.path
        if service is None or not service.available:
            return _unavailable(
                instance,
                "email verification is not available: the mail intake is not configured",
            )
        try:
            principal = service.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return problem_response(UNAUTHORIZED, instance)

        try:
            payload = await request.json()
        except ValueError:
            return _unprocessable(instance, "request body must be a JSON object")
        if not isinstance(payload, dict):
            return _unprocessable(instance, "request body must be a JSON object")
        # 契约 `additionalProperties: false`：未知字段一律拒绝（不回显取值，取值可能含内容）。
        unknown = sorted(set(payload) - set(_CHALLENGE_REQUEST_FIELDS))
        if unknown:
            return _unprocessable(instance, "request body has unknown fields: " + ", ".join(unknown))
        missing = [name for name in _CHALLENGE_REQUEST_FIELDS if name not in payload]
        if missing:
            return _unprocessable(instance, "missing fields: " + ", ".join(missing))

        try:
            # The service calls B3's internal assignment endpoint synchronously. Offload it:
            # in the composed host this process also serves that endpoint, so a blocking call
            # inside the event loop would deadlock until the client times out.
            issued = await run_in_threadpool(
                service.issue_challenge,
                principal,
                course_id=payload["course_id"],
                section_id=payload["section_id"],
                assignment_id=payload["assignment_id"],
            )
        except AuthorizationError:
            log_event(
                logger,
                "email_verification.challenge_rejected",
                level=logging.WARNING,
                fields={"outcome": "denied", "decision": "forbidden"},
            )
            return problem_response(FORBIDDEN, instance)
        except ChallengeAssignmentNotFoundError:
            return problem_response(
                Problem(
                    404,
                    "not-found",
                    "Not Found",
                    "The assignment does not exist.",
                ),
                instance,
            )
        except ChallengeAssignmentClosedError:
            return problem_response(
                Problem(
                    409,
                    "conflict",
                    "Conflict",
                    "The assignment is not open for submissions.",
                ),
                instance,
            )
        except AssignmentUnavailable as error:
            log_dependency_unavailable(
                logger,
                dependency="b3-assignments",
                error=error,
                stage="email verification challenge",
            )
            return problem_response(SERVICE_UNAVAILABLE, instance)
        except AuthorizationUnavailable as error:
            # B4 权威授权目录未配置/不可达：签不出绑定"当前归属"的挑战，503（不是 403）。
            log_dependency_unavailable(
                logger,
                dependency="b4-authorization",
                error=error,
                stage="email verification challenge",
            )
            return problem_response(SERVICE_UNAVAILABLE, instance)
        except ChallengeRequestError as error:
            return _unprocessable(instance, str(error))
        except SQLAlchemyError as error:
            log_dependency_unavailable(
                logger,
                dependency="database",
                error=error,
                stage="email verification challenge",
            )
            return problem_response(SERVICE_UNAVAILABLE, instance)

        # 只说"签发了一张码"：核验码本身、学生标识都不进日志（码是凭据，标识是超范围 PII）。
        log_event(
            logger,
            "email_verification.challenge_issued",
            fields={"outcome": "issued"},
        )
        return JSONResponse(
            status_code=201,
            content={
                "challenge_id": issued.challenge_id,
                "code": issued.code,
                "expires_at": rfc3339_millis(issued.expires_at),
                "assignment_id": issued.assignment_id,
            },
        )

    @router.get("/api/v1/email-verification/challenges/{challenge_id}")
    async def get_email_verification_challenge_status(
        challenge_id: str, request: Request
    ) -> JSONResponse:
        instance = request.url.path
        if service is None or not service.available:
            return _unavailable(
                instance,
                "email verification is not available: the mail intake is not configured",
            )
        try:
            principal = service.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return problem_response(UNAUTHORIZED, instance)

        try:
            status = await run_in_threadpool(service.get_challenge_status, principal, challenge_id)
        except ChallengeNotFoundError:
            return problem_response(
                Problem(
                    404,
                    "not-found",
                    "Not Found",
                    "The email verification challenge does not exist.",
                ),
                instance,
            )
        except AuthorizationError:
            log_event(
                logger,
                "email_verification.challenge_rejected",
                level=logging.WARNING,
                fields={"outcome": "denied", "decision": "forbidden"},
            )
            return problem_response(FORBIDDEN, instance)
        except SQLAlchemyError as error:
            log_dependency_unavailable(
                logger,
                dependency="database",
                error=error,
                stage="email verification challenge",
            )
            return problem_response(SERVICE_UNAVAILABLE, instance)

        return JSONResponse(status_code=200, content=_status_body(status))

    return router


def _status_body(status: ChallengeStatus) -> dict[str, Any]:
    """契约 `EmailVerificationChallengeStatus` 的 JSON 形状（`additionalProperties: false`）。

    `submissions` 是该邮件已生成、按题映射的提交（未收到为空数组）；契约不再有单一的
    `submission_id`——一封多附件的邮件会产生 N 条提交（设计 §1.1/§2.2）。
    """
    return {
        "challenge_id": status.challenge_id,
        "state": status.state,
        "reason_code": status.reason_code,
        "expires_at": rfc3339_millis(status.expires_at),
        "verified_at": (
            None if status.verified_at is None else rfc3339_millis(status.verified_at)
        ),
        "submissions": [
            {
                "question_version_id": item.question_version_id,
                "submission_id": item.submission_id,
            }
            for item in status.submissions
        ],
        "assignment_id": status.assignment_id,
    }


def _unavailable(instance: str, detail: str) -> JSONResponse:
    return problem_response(
        Problem(503, "service-unavailable", "Service Unavailable", detail), instance
    )


def _unprocessable(instance: str, detail: str) -> JSONResponse:
    return problem_response(
        Problem(422, "unprocessable-entity", "Unprocessable Entity", detail), instance
    )


__all__ = ["build_router"]
