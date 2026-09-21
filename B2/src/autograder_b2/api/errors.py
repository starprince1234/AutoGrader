"""RFC 9457 Problem Details：唯一对外错误形态（`MAIN/contracts/openapi/common.yaml`）。

硬约束：`detail` 不含堆栈、SQL、内部绝对路径或源码片段；`instance` 用请求路径或关联 ID，
**不**回显内部资源标识。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_BASE = "https://autograder.local/problems"
CONTENT_TYPE = "application/problem+json"


@dataclass(frozen=True, slots=True)
class Problem:
    """一个可对外返回的错误（状态码 + 机器可读类型 + 人类可读标题/说明）。"""

    status: int
    type_slug: str
    title: str
    detail: str

    def as_body(self, instance: str) -> dict[str, Any]:
        return {
            "type": f"{PROBLEM_BASE}/{self.type_slug}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
        }


UNAUTHORIZED = Problem(401, "unauthorized", "Unauthorized", "Authentication is required.")
FORBIDDEN = Problem(
    403, "forbidden", "Forbidden", "The caller is not permitted to perform this operation."
)
PAYLOAD_TOO_LARGE = Problem(
    413, "payload-too-large", "Payload Too Large", "The submission exceeds a size limit."
)
UNPROCESSABLE = Problem(
    422, "unprocessable-entity", "Unprocessable Entity", "The request was rejected by a business rule."
)
SERVICE_UNAVAILABLE = Problem(
    503, "service-unavailable", "Service Unavailable", "A required dependency is unavailable."
)


def problem_response(problem: Problem, instance: str) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.as_body(instance),
        media_type=CONTENT_TYPE,
    )


def install_problem_handlers(app: FastAPI) -> None:
    """把 FastAPI 默认错误体替换为 Problem Details（含请求体校验失败）。"""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 只报"字段路径 + 规则"，不回显提交的取值（取值可能含源码内容）。
        locations = sorted(
            {".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()}
        )
        detail = "request body failed validation"
        if locations:
            detail = f"{detail}: {', '.join(locations[:10])}"
        return problem_response(
            Problem(422, "unprocessable-entity", "Unprocessable Entity", detail), request.url.path
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = Problem(
            status=exc.status_code,
            type_slug=f"http-{exc.status_code}",
            title=str(exc.detail) if isinstance(exc.detail, str) else "Error",
            detail="The request could not be completed.",
        )
        return problem_response(problem, request.url.path)
