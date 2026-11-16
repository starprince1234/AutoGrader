"""实时 AI 助教 HTTP 路由（`POST /api/v1/submissions/ai-assistant`）。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from autograder_b2.api.errors import UNAUTHORIZED, Problem, problem_response
from autograder_b2.assistant.service import (
    AssistantAccessDenied,
    AssistantNotFound,
    AssistantServiceUnavailable,
    AssistantValidationError,
    generate_draft_ai_assistance,
)
from autograder_b2.auth.tokens import AuthenticationError, Principal
from autograder_b2.logging.logger import log_event
from autograder_b2.runtime import B2Runtime

logger = logging.getLogger("autograder_b2.api.assistant_routes")


def _problem(instance: str, status: int, slug: str, title: str, detail: str) -> JSONResponse:
    return problem_response(Problem(status, slug, title, detail), instance)


def _authenticate(runtime: B2Runtime, request: Request, instance: str) -> Principal | Response:
    authenticator = runtime.intake.authenticator
    if authenticator is None:
        return _problem(
            instance,
            503,
            "service-unavailable",
            "Service Unavailable",
            runtime.intake.unavailable_reason or "认证服务尚未配置。",
        )
    try:
        return authenticator.authenticate(request.headers.get("authorization"))
    except AuthenticationError:
        return problem_response(UNAUTHORIZED, instance)


def build_assistant_router(runtime: B2Runtime) -> APIRouter:
    """挂载 `/api/v1/submissions/ai-assistant`（必须在 `{submission_id}` 之前注册）。"""
    router = APIRouter()

    @router.post("/api/v1/submissions/ai-assistant")
    async def draft_ai_assistant(request: Request) -> Response:
        instance = request.url.path
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 非法 JSON 一律 422
            return _problem(
                instance, 422, "unprocessable-entity", "Unprocessable Entity", "请求体必须是合法 JSON。"
            )
        if not isinstance(body, dict):
            return _problem(
                instance, 422, "unprocessable-entity", "Unprocessable Entity", "请求体必须是 JSON 对象。"
            )

        expected_keys = {"assignment_id", "question_version_id", "draft_code", "student_question"}
        extra_keys = set(body) - expected_keys
        if extra_keys:
            return _problem(
                instance,
                422,
                "unprocessable-entity",
                "Unprocessable Entity",
                f"请求体包含未定义字段：{', '.join(sorted(extra_keys))}。",
            )

        try:
            result = await asyncio.to_thread(
                generate_draft_ai_assistance,
                runtime,
                principal=principal,
                assignment_id=body.get("assignment_id"),
                question_version_id=body.get("question_version_id"),
                draft_code=body.get("draft_code"),
                student_question=body.get("student_question"),
            )
        except AssistantValidationError as error:
            return _problem(
                instance, 422, "unprocessable-entity", "Unprocessable Entity", str(error)
            )
        except AssistantNotFound as error:
            return _problem(instance, 404, "not-found", "Not Found", str(error))
        except AssistantAccessDenied as error:
            return _problem(instance, 403, "forbidden", "Forbidden", str(error))
        except AssistantServiceUnavailable as error:
            log_event(
                logger,
                "assistant.unavailable",
                level=logging.WARNING,
                fields={"detail": str(error)},
            )
            return _problem(
                instance, 503, "service-unavailable", "Service Unavailable", str(error)
            )

        log_event(
            logger,
            "assistant.generated",
            fields={
                "question_version_id": result.question_version_id,
                "provider": result.provider_name,
                "model": result.model_name,
            },
        )
        return JSONResponse(
            {
                "question_version_id": result.question_version_id,
                "provider_name": result.provider_name,
                "model_name": result.model_name,
                "generated_at": result.generated_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "stage_diagnosis": result.stage_diagnosis,
                "next_steps": list(result.next_steps),
                "key_concepts": list(result.key_concepts),
                "check_points": list(result.check_points),
                "disclaimers": list(result.disclaimers),
            }
        )

    return router
