"""手动评分与提交内容复核的 HTTP 管线（`b2.yaml` 的 `manual-grade`、`files`、`intake-message`）。

本模块只做 HTTP 关注点：验签、解析请求体、状态码映射、响应头。**判定**全部在
`autograder_b2.grades.manual`：角色（谁可以评分）、课程作用域、乐观锁、改分理由。路由里再写
一份规则就会有两个真相源，而这两处迟早会分叉。

错误映射与 `grades_routes` 一致：

| 服务层异常 | 状态码 |
|---|---|
| `GradingForbidden` | 403 |
| `SubmissionNotFound` | 404 |
| `ManualGradeConflict` | 409（乐观锁冲突 / 改分缺理由） |
| `InvalidManualGrade` | 422 |
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from autograder_b2.api.errors import FORBIDDEN, UNAUTHORIZED, Problem, problem_response
from autograder_b2.auth.tokens import AuthenticationError, Principal
from autograder_b2.grades.manual import (
    GradingForbidden,
    InvalidManualGrade,
    ManualGradeConflict,
    get_intake_message,
    get_manual_grade,
    list_submission_files,
    read_submission_file,
    release_manual_grade,
    set_manual_grade,
)
from autograder_b2.grades.manual import SubmissionNotFound as ManualNotFound
from autograder_b2.logging.logger import log_event
from autograder_b2.runtime import B2Runtime

logger = logging.getLogger("autograder_b2.autograder_b2.api.manual_grade_routes")

#: `GET /files` 允许的下载上限：一个附件不该把内存撑爆，且提交本身已有大小上限。
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


def _json_error(instance: str, status: int, title: str, detail: str) -> JSONResponse:
    return problem_response(Problem(status, slugify(title), title, detail, instance))


def slugify(title: str) -> str:
    return "-".join(title.lower().split())


def _authenticate(runtime: B2Runtime, request: Request, instance: str) -> Principal | Response:
    authenticator = runtime.intake.authenticator
    if authenticator is None:  # 与 grades_routes 相同：ready 为假时路由已 503
        return _json_error(instance, 503, "Service Unavailable", "Intake is not configured.")
    try:
        return authenticator.authenticate(request.headers.get("authorization"))
    except AuthenticationError:
        return problem_response(UNAUTHORIZED, instance)


def _unavailable(instance: str, reason: str) -> JSONResponse:
    log_event(
        logger,
        "manual_grade.unavailable",
        level=logging.ERROR,
        fields={"outcome": "unavailable", "reason": reason},
    )
    return _json_error(instance, 503, "Service Unavailable", reason)


def _parse_timestamp(raw: object) -> datetime:
    """`expected_updated_at` 必须是带时区的 RFC 3339；无法解析即 422（不猜时区）。"""
    if not isinstance(raw, str):
        raise InvalidManualGrade("expected_updated_at must be an RFC 3339 timestamp")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise InvalidManualGrade("expected_updated_at must carry a timezone offset")
    return parsed


def _grade_body(result) -> dict[str, object]:
    return {
        "submission_id": result.submission_id,
        "score": result.score,
        "score_source": "manual",
        "grade_status": result.grade_status,
        "comment": result.comment,
        "graded_by": result.graded_by,
        "graded_at": result.graded_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "updated_at": result.updated_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "revision": result.revision,
    }


def build_manual_grade_router(runtime: B2Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/submissions/{submission_id}/manual-grade")
    async def set_grade(submission_id: str, request: Request) -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _unavailable(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        engine = runtime.engine
        if engine is None:  # pragma: no cover - ready 为假时上面已返回 503
            return _unavailable(instance, "The database is not configured.")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 非法 JSON 就是 422
            return _json_error(instance, 422, "Unprocessable Content", "The body is not JSON.")
        if not isinstance(body, dict):
            return _json_error(instance, 422, "Unprocessable Content", "The body must be an object.")

        try:
            result = set_manual_grade(
                engine,
                submission_id=submission_id,
                score=body.get("score"),
                comment=body.get("comment"),
                reason=body.get("reason"),
                expected_updated_at=_parse_timestamp(body.get("expected_updated_at")),
                principal=principal,
            )
        except GradingForbidden as error:
            return problem_response(FORBIDDEN, instance, str(error))
        except ManualNotFound as error:
            return _json_error(instance, 404, "Not Found", str(error))
        except ManualGradeConflict as error:
            return _json_error(instance, 409, "Conflict", str(error))
        except InvalidManualGrade as error:
            return _json_error(instance, 422, "Unprocessable Content", str(error))

        log_event(
            logger,
            "manual_grade.recorded",
            fields={
                "submission_id": submission_id,
                "revision": result.revision,
                "score": result.score,
            },
        )
        return JSONResponse(_grade_body(result))

    @router.get("/api/v1/submissions/{submission_id}/manual-grade")
    async def read_grade(submission_id: str, request: Request) -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _unavailable(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response

        engine = runtime.engine
        if engine is None:  # pragma: no cover
            return _unavailable(instance, "The database is not configured.")
        try:
            result = get_manual_grade(
                engine, submission_id=submission_id, principal=principal_or_response
            )
        except GradingForbidden as error:
            return problem_response(FORBIDDEN, instance, str(error))
        except ManualNotFound as error:
            return _json_error(instance, 404, "Not Found", str(error))

        if result is None:
            # 还没评过不是错误：返回 204，客户端据此判定"首评"。
            return Response(status_code=204)
        return JSONResponse(_grade_body(result))

    @router.post("/api/v1/submissions/{submission_id}/manual-grade/release")
    async def release_grade(submission_id: str, request: Request) -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _unavailable(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response

        engine = runtime.engine
        if engine is None:  # pragma: no cover
            return _unavailable(instance, "The database is not configured.")
        try:
            release_manual_grade(
                engine, submission_id=submission_id, principal=principal_or_response
            )
        except GradingForbidden as error:
            return problem_response(FORBIDDEN, instance, str(error))
        except ManualNotFound as error:
            return _json_error(instance, 404, "Not Found", str(error))
        except ManualGradeConflict as error:
            return _json_error(instance, 409, "Conflict", str(error))

        log_event(logger, "manual_grade.released", fields={"submission_id": submission_id})
        return JSONResponse({"submission_id": submission_id, "grade_status": "awaiting_manual"})

    @router.get("/api/v1/submissions/{submission_id}/intake-message")
    async def intake_message(submission_id: str, request: Request) -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _unavailable(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response

        engine = runtime.engine
        if engine is None:  # pragma: no cover
            return _unavailable(instance, "The database is not configured.")
        try:
            message = get_intake_message(
                engine, submission_id=submission_id, principal=principal_or_response
            )
            attachment_names = list_submission_files(
                engine, submission_id=submission_id, principal=principal_or_response
            )
        except GradingForbidden as error:
            return problem_response(FORBIDDEN, instance, str(error))
        except ManualNotFound as error:
            return _json_error(instance, 404, "Not Found", str(error))

        return JSONResponse(
            {
                "subject": message.subject,
                "sender": message.sender,
                "received_at": message.received_at.isoformat(timespec="milliseconds").replace(
                    "+00:00", "Z"
                ),
                "body_excerpt": message.body_excerpt,
                "truncated": message.truncated,
                "attachment_names": attachment_names,
            }
        )

    @router.get("/api/v1/submissions/{submission_id}/files")
    async def download_file(submission_id: str, request: Request, path: str = "") -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _unavailable(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        if not path.strip():
            return _json_error(
                instance, 422, "Unprocessable Content", "A file path is required."
            )

        engine = runtime.engine
        settings = runtime.intake.settings
        if engine is None or settings is None:  # pragma: no cover
            return _unavailable(instance, "The database is not configured.")

        try:
            location, size = read_submission_file(
                engine,
                artifact_root=settings.artifact_root,
                submission_id=submission_id,
                path=path,
                principal=principal_or_response,
            )
        except GradingForbidden as error:
            return problem_response(FORBIDDEN, instance, str(error))
        except ManualNotFound as error:
            return _json_error(instance, 404, "Not Found", str(error))
        if size > MAX_DOWNLOAD_BYTES:
            return _json_error(
                instance,
                422,
                "Unprocessable Content",
                "The file is larger than the download limit.",
            )

        content = location.read_bytes()
        log_event(
            logger,
            "submission.file_downloaded",
            fields={"submission_id": submission_id, "path": path},
        )
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{Path(path).name}"',
                "Content-Length": str(len(content)),
            },
        )

    return router
