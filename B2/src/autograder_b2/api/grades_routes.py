"""成绩查询与导出（`b2.yaml` 的 `grades` 路径族，2 条端点）。

服务层是**已交付**的 `grades.GradeQueryService` / `grades.GradesExportService`：本模块只做 HTTP
关注点（认证头、查询参数解析、状态码映射、响应头），不重算任何分数、不另写 SQL、不做第二套过滤
语义。契约口径三条：

1. **未完成 ≠ 0 分**：`GradeRow.score/max_score/is_pass` 只在运行 `completed` 且结果行存在时非空，
   其余情况由 `state` 说明处境（服务层已如此，路由只是原样透出）。
2. **越权是 403 而不是收窄**：`grades.query.require_grade_authority` 是唯一的判定点；
   学生指名他人 `student_id` → 403，教师在未覆盖的课序上查询 → 403，管理员不受课程限制。
3. **导出与查询同口径**：`GradesExportService` 复用 `GradeQueryService.iter_grades` 的同一游标
   遍历，因此"导出看到的行"与"翻页看到的行"是同一批（结构性一致，不是两处代码碰巧一致）。

## 声明缺口已由 CHG-0005 关闭（MAIN 判定：补声明，不改实现）

三处"行为存在但契约没声明"的缺口已由 MAIN 按"新增已存在行为的声明"补入
`MAIN/contracts/openapi/b2.yaml`，本模块的实现**未变**（它本来就按行为实现）：

- `listGrades` / `exportGrades` 的 `parameters` 补上 `student_id`；
- 两个 operation 的 `responses` 补上 `422`（`InvalidFilterError`/`InvalidCursorError`/
  `UnsupportedExportFormat` → 422 的映射要求它）；
- `exportGrades` 删除 `404`：B2 不拥有课程实体，"课程不存在"与"课程不存在提交"在 `b2`
  schema 内不可区分，实现从不返回 404（空结果返回 200 + 仅表头文件）。

判定依据与完整影响面见 `MAIN/docs/contracts/changes/CHG-0005-grades-endpoint-declarations.md`。

## 为什么查询参数手工解析

`limit`/`format`/`course_id` 都声明成 `str | None` 再由本模块判定：宿主（MAIN composition host）
没有安装 `api.errors.install_problem_handlers`，若把类型校验交给 FastAPI，它的默认 422 响应体
不是 RFC 9457——而"错误体一律 Problem Details"是本模块的硬要求，不能依赖宿主装配。业务规则的
判定仍只有一处（服务层的 `InvalidFilterError`/`UnsupportedExportFormat`）。

`?cursor=` / `?section_id=` 这种空值一律视为"未提供"（与 `grades.query._optional_uuid` 的口径一致）：
`cursor=""` 若当成"坏游标"就会把一次空查询串变成 422。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from autograder_b2.api.errors import (
    FORBIDDEN,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    Problem,
    problem_response,
)
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import AuthenticationError, Principal
from autograder_b2.grades import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidCursorError,
    InvalidFilterError,
    UnsupportedExportFormat,
    grade_page_body,
)
from autograder_b2.logging.logger import log_dependency_unavailable, log_event
from autograder_b2.runtime import B2Runtime

#: 路由层 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.autograder_b2.api.grades_routes")

#: 契约 `exportGrades.format` 的默认值（服务层的 `EXPORT_FORMATS` 是取值域的权威）。
DEFAULT_EXPORT_FORMAT = "xlsx"


def build_grades_router(runtime: B2Runtime) -> APIRouter:
    """成绩路径族的路由；闭包持有 `runtime`（与 `intake.api.build_router` 同一形态）。"""
    router = APIRouter()

    @router.get("/api/v1/grades")
    async def list_grades(
        request: Request,
        course_id: str | None = None,
        section_id: str | None = None,
        question_version_id: str | None = None,
        student_id: str | None = None,
        cursor: str | None = None,
        limit: str | None = None,
    ) -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _not_available(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        if not course_id:
            return _unprocessable(instance, "course_id is required")
        try:
            size = _parse_limit(limit)
        except InvalidFilterError as error:
            return _unprocessable(instance, str(error))

        assert runtime.grades is not None  # grades_unavailable() 通过即已装配
        try:
            page = runtime.grades.list_grades(
                principal=principal,
                course_id=course_id,
                section_id=section_id,
                question_version_id=question_version_id,
                student_id=student_id,
                limit=size,
                cursor=cursor or None,
            )
        except AuthorizationError:
            return problem_response(FORBIDDEN, instance)
        except (InvalidFilterError, InvalidCursorError) as error:
            # 过滤与游标的校验**只有**服务层这一处；这里只做状态码映射。
            return _unprocessable(instance, str(error))
        except SQLAlchemyError as error:
            # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
            # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
            # 日志侧是统一的依赖故障事件。
            # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="grades"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)
        return JSONResponse(status_code=200, content=grade_page_body(page))

    @router.get("/api/v1/grades/export")
    async def export_grades(
        request: Request,
        course_id: str | None = None,
        section_id: str | None = None,
        question_version_id: str | None = None,
        student_id: str | None = None,
        format: str | None = None,
    ) -> Response:
        instance = request.url.path
        unavailable = runtime.grades_unavailable()
        if unavailable is not None:
            return _not_available(instance, unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        if not course_id:
            return _unprocessable(instance, "course_id is required")
        requested = DEFAULT_EXPORT_FORMAT if not format else format

        assert runtime.exporter is not None
        try:
            file = runtime.exporter.export(
                principal=principal,
                course_id=course_id,
                section_id=section_id,
                question_version_id=question_version_id,
                student_id=student_id,
                fmt=requested,
            )
        except AuthorizationError:
            return problem_response(FORBIDDEN, instance)
        except UnsupportedExportFormat as error:
            return _unprocessable(instance, str(error))
        except (InvalidFilterError, InvalidCursorError) as error:
            return _unprocessable(instance, str(error))
        except SQLAlchemyError as error:
            # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
            # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
            # 日志侧是统一的依赖故障事件。
            # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="grades"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)

        # 文件名与媒体类型都来自 `ExportFile`（服务层已经决定），路由不另造一套。
        return Response(
            content=file.content,
            media_type=file.media_type,
            headers={"Content-Disposition": file.content_disposition},
        )

    return router


def _authenticate(runtime: B2Runtime, request: Request, instance: str) -> Principal | Response:
    """验签 → `Principal`，或 `401` 响应。

    与 `intake.api` 一样，每个路由模块自己持有这几行 HTTP 管线；**判定**（角色/作用域/身份）
    仍然只有服务层一处，不存在第二份授权逻辑。
    """
    authenticator = runtime.intake.authenticator
    assert authenticator is not None  # ready 为假时路由已 503
    try:
        return authenticator.authenticate(request.headers.get("authorization"))
    except AuthenticationError:
        return problem_response(UNAUTHORIZED, instance)


def _parse_limit(raw: str | None) -> int:
    """`limit` 解析：只有"不是整数"在这里拦，**取值范围**由服务层的 `InvalidFilterError` 判定。"""
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        return int(raw)
    except ValueError as error:
        raise InvalidFilterError(f"limit must be an integer between 1 and {MAX_LIMIT}") from error


def _not_available(instance: str, reason: str) -> JSONResponse:
    """依赖未装配/不可用时的 503：**状态之外还要有日志**（RQ-B-2-99 场景 2-99-D）。

    这条路径不是"查询失败"，而是请求在进入查询前就被拒（例如库结构落后或依赖未装配）。状态侧是 503 +
    健康分项；日志侧用本模块自己的事件名，并把原因（内部文本，不含 PII）放进自由文本槽。
    """
    log_event(
        logger,
        "grades.unavailable",
        level=logging.ERROR,
        fields={"outcome": "unavailable", "reason": reason},
    )
    return problem_response(
        Problem(
            503,
            "service-unavailable",
            "Service Unavailable",
            f"grade query is not available: {reason}",
        ),
        instance,
    )


def _unprocessable(instance: str, detail: str) -> JSONResponse:
    return problem_response(
        Problem(422, "unprocessable-entity", "Unprocessable Entity", detail), instance
    )
