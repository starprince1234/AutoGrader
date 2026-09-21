"""成绩查询服务（`GET /api/v1/grades` 的**服务层**；路由注册由 lead 统一完成）。

本模块只提供可被路由直接调用的服务对象与响应体映射，不含 HTTP 关注点（认证头、状态码、
Problem Details 由路由层决定）。越权判定复用 `auth.authorization.AuthorizationError`，因此路由层
既有的 `except AuthorizationError -> 403` 无需新增分支，也不会用 404 掩盖。

行口径以契约 `MAIN/contracts/openapi/b2.yaml` 的 `GradeRow` 为准（字段名与顺序照抄契约，
不在此复述整张表）：

- `b2.submissions` → `submission_id`/`student_id`/`course_id`/`section_id`/
  `question_version_id`/`submitted_at`(=`created_at`)；
- 该提交**最新一次**评测运行（`sequence` 降序，再按 `created_at`、`evaluation_run_id` 降序定序）
  → `evaluation_run_id`/`state`；
- `b2.evaluation_results` → `score`/`max_score`/`is_pass`。

**`state` 的取值来源**：有评测运行时取该运行的状态，尚无运行时回落到提交状态（`received`/
`validated` 只可能来自提交）。两种取值都在契约 `GradeRow.state` 的枚举内，且这样处理同时满足
两条要求：①"未完成的**运行**"如实上报自己的状态；②重跑（同一提交出现更高 `sequence` 的运行）
时行不会被上一轮的终态掩盖。

**未完成 ≠ 0 分**：`score`/`max_score`/`is_pass` **只在**运行状态为 `completed` 且
`evaluation_results` 行确实存在时才非空；`queued`/`running`/`rejected`/`failed` 一律为 `null`，
由 `state` 说明当前处境。0 分是**学生结果**，不是"还没跑"：把未完成渲染成 0 会让 B1 页面把
"排队中"显示成"考了 0 分"，而这个错误无法从页面上看出来。

**`student_number` / `display_name` 恒为 `null`**：这两个字段属 B4（`b4.users` / `b4.enrollments`），
B2 只拥有 `b2` schema、**不读 `b4` schema**（`AGENTS.md` 硬规则 2）。因此本服务不自造、不从别处
拼凑，也不伪造占位值——契约允许二者为 `null`，字段照常出现以保持 `GradeRow` 形状不变。
（书面观察：一旦有人把 B4 的姓名/学号拼进成绩行，那就是跨 schema 读取；正确做法是由 B4 提供
批量查询或由网关层补全，而不是在 B2 里 join。）
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import evaluation_results, evaluation_runs, submissions
from autograder_b2.domain.states import COMPLETED

#: 契约 `Limit` 的默认值与边界（`common.yaml`）。
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: 导出内部游标遍历的页大小（不暴露给调用方）。
DEFAULT_BATCH_SIZE = 500
_CURSOR_VERSION = 1


class InvalidCursorError(ValueError):
    """游标不是本服务签发的形态 → 对外 422。

    不把坏游标当作"第一页"静默处理：那会让"翻页"变成"回到开头"，调用方无法察觉。
    """


class InvalidFilterError(ValueError):
    """过滤参数不合契约（非 UUID、`limit` 越界）→ 对外 422。"""


@dataclass(frozen=True, slots=True)
class GradeRow:
    """契约 `GradeRow` 的一行（字段名与顺序以契约为准）。"""

    submission_id: str
    student_id: str
    student_number: str | None
    display_name: str | None
    course_id: str
    section_id: str
    question_version_id: str
    state: str
    score: float | None
    max_score: float | None
    is_pass: bool | None
    submitted_at: datetime
    evaluation_run_id: str | None


@dataclass(frozen=True, slots=True)
class GradePage:
    """契约 `CursorPage` + `GradeRow[]`：一页行 + 下一页游标（`None` 表示已到末尾）。"""

    items: tuple[GradeRow, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _Filters:
    """已校验、**已按调用者作用域收敛**的过滤条件（授权判定的产物）。"""

    course_id: uuid.UUID
    sections: tuple[uuid.UUID, ...] | None
    question_version_id: uuid.UUID | None
    student_id: str | None


def encode_cursor(*, submitted_at: datetime, submission_id: str) -> str:
    """把"上一页最后一行"编码为不透明游标。

    用 **ISO 8601 全精度**（微秒位照原样写）而不是对外的毫秒 RFC 3339 时间：游标是
    `(created_at, submission_id)` 的严格后继判定，**再截断一次**会把同一毫秒内尚未越过的行判成
    已越过（重复返回）或反之（跳过）。库里是 `timestamptz(3)`（迁移 0012 的表标准，毫秒精度，
    写入时四舍五入），因此游标承载的精度**不低于**列精度，往返无损、判定唯一。
    对外时间仍是毫秒 RFC 3339（`api.formatting`），两种精度用途不同，不共用。
    """
    moment = (
        submitted_at.astimezone(UTC) if submitted_at.tzinfo else submitted_at.replace(tzinfo=UTC)
    )
    payload = json.dumps(
        {"v": _CURSOR_VERSION, "at": moment.isoformat(), "id": str(submission_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """解析游标 → `(created_at, submission_id)`；形态非法一律抛 `InvalidCursorError`。

    游标**只**承载"上一页最后一行的位置"，不承载过滤条件、也不承载身份：每次请求的过滤条件与授权
    都从请求参数与令牌重新推导。因此伪造游标只能改变自己这一页的起点，既不能越权、也不能扩大范围
    （作用域谓词在每一页的 SQL 里都在）。
    """
    try:
        # 编码时去掉了 base64 的 `=` 填充（游标出现在 URL 查询串里），解码前补回。
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError) as error:
        raise InvalidCursorError("cursor is not a valid base64url JSON token") from error
    if not isinstance(payload, dict):
        raise InvalidCursorError("cursor payload must be a JSON object")
    version = payload.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or version != _CURSOR_VERSION:
        raise InvalidCursorError("unsupported cursor version")
    moment, identifier = payload.get("at"), payload.get("id")
    if not isinstance(moment, str) or not isinstance(identifier, str):
        raise InvalidCursorError("cursor is missing its position")
    try:
        parsed = datetime.fromisoformat(moment)
    except ValueError as error:
        raise InvalidCursorError("cursor position is not a timestamp") from error
    if parsed.tzinfo is None:
        raise InvalidCursorError("cursor position must carry a timezone")
    try:
        return parsed.astimezone(UTC), uuid.UUID(identifier)
    except ValueError as error:
        raise InvalidCursorError("cursor position is not a submission id") from error


def covered_sections(principal: Principal, course_id: str) -> tuple[str, ...] | None:
    """调用者在课程内的**课序上界**：`None` = 课程全域；否则只允许这些课序。

    为什么必须有这一层（本工作项的安全要点）：`CourseScope.covers(course, None)` 对"已限定课序"
    的令牌返回 `True`——它把 `section_id is None` 读作"未指名课序"，于是放行。对**按记录判定**的
    端点无害（课序来自记录本身，必然是具体值，`intake.query` 即属此类）；但成绩查询的 `section_id`
    是**过滤参数**，`None` 意味着"整门课"。少这一层，一个只覆盖课序 S1 的助教用 `?course_id=C`
    （不传 `section_id`）就能拿到整门课的成绩——越权而非收窄。
    """
    if principal.is_admin:
        return None
    covered: list[str] = []
    for scope in principal.scopes:
        if scope.course_id != course_id:
            continue
        if scope.section_id is None:
            return None
        covered.append(scope.section_id)
    return tuple(sorted(set(covered)))


def require_grade_authority(
    principal: Principal,
    *,
    course_id: str,
    section_id: str | None,
    student_id: str | None,
) -> str | None:
    """成绩读权限判定；返回**生效的** `student_id` 过滤（`None` = 不按学生过滤）。

    顺序有意如此（与 `intake.query._require_read_authority` 同构：角色 → 作用域 → 身份），
    `identifiers.md` §3 要求三层同时成立：

    1. 无任何**已登记**角色 → 403（认证 ≠ 授权，连自己的成绩也不能读）；
    2. 未覆盖请求的 `course_id`（或显式指名的 `section_id`）→ 403；管理员不受课程限制；
    3. 指名了 `student_id`：
       - 教师/助教/管理员可以（在其课程作用域内代查）；
       - **学生只能指名自己**，指名他人 → 403。**不**静默收窄成"自己的记录"：静默收窄会让一次
         越权探测看起来像一次正常查询，调用方与审计都无法察觉；
    4. 未指名 `student_id` 时：学生**收敛**为自己的记录（"学生只能看自己的记录"），教师/助教
       看整个作用域。这与第 3 条不矛盾——第 3 条禁的是"显式索要他人数据"，本条的收敛是默认口径。
    """
    if not principal.has_recognized_role:
        raise AuthorizationError("the token carries no recognized platform role")
    if not principal.is_admin and not principal.covers_course(course_id, section_id):
        raise AuthorizationError("course scope does not cover the requested course or section")
    if student_id is not None:
        if principal.is_admin or principal.is_course_staff:
            return student_id
        if student_id == principal.user_id:
            return student_id
        raise AuthorizationError("a student may only read their own grade records")
    if principal.is_admin or principal.is_course_staff:
        return None
    return principal.user_id


def grade_row_body(row: GradeRow) -> dict[str, Any]:
    """契约 `GradeRow` 的 JSON 形状（字段集合固定，`additionalProperties: false`）。

    路由层应直接返回本函数的产物，不要另行拼字段：契约映射只应存在一处。时间用毫秒 RFC 3339
    （`identifiers.md` 第 1 节）。
    """
    return {
        "submission_id": row.submission_id,
        "student_id": row.student_id,
        "student_number": row.student_number,
        "display_name": row.display_name,
        "course_id": row.course_id,
        "section_id": row.section_id,
        "question_version_id": row.question_version_id,
        "state": row.state,
        "score": row.score,
        "max_score": row.max_score,
        "is_pass": row.is_pass,
        "submitted_at": rfc3339_millis(row.submitted_at),
        "evaluation_run_id": row.evaluation_run_id,
    }


def grade_page_body(page: GradePage) -> dict[str, Any]:
    """契约 `CursorPage` 的 JSON 形状：`{items, next_cursor}`。"""
    return {
        "items": [grade_row_body(row) for row in page.items],
        "next_cursor": page.next_cursor,
    }


class GradeQueryService:
    """成绩查询（`listGrades` 的服务层）。

    实例不持有调用者身份：`principal` 逐次传入，作用域与身份判定每次重算（令牌过期/改权后立即生效，
    不存在"实例缓存了上一个调用者的作用域"的风险）。分页为**游标（keyset）分页**，绝不使用
    `OFFSET`：`OFFSET` 在校验期间新提交到达时会重复/跳行，且随页号线性变慢。
    """

    def __init__(self, engine: Engine, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self._engine = engine
        self._batch_size = _validated_batch_size(batch_size)

    def list_grades(
        self,
        *,
        principal: Principal,
        course_id: str,
        section_id: str | None = None,
        question_version_id: str | None = None,
        student_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> GradePage:
        """查询一页成绩行。

        参数：`course_id` 必填；`section_id`/`question_version_id`/`student_id` 可选过滤；
        `limit` ∈ [1, 200]（契约 `Limit`）；`cursor` 为上一页的 `next_cursor`（首页传 `None`）。

        异常：`AuthorizationError` → 路由层 403；`InvalidFilterError`/`InvalidCursorError` → 422。
        `next_cursor` 非空即表示**还有下一页**（不是"可能还有"）。
        """
        size = _validated_limit(limit)
        filters = self._resolve(
            principal=principal,
            course_id=course_id,
            section_id=section_id,
            question_version_id=question_version_id,
            student_id=student_id,
        )
        after = None if cursor is None else decode_cursor(cursor)
        with self._engine.connect() as connection:
            items, next_cursor = _fetch_page(connection, filters=filters, after=after, size=size)
        return GradePage(items=items, next_cursor=next_cursor)

    def iter_grades(
        self,
        *,
        principal: Principal,
        course_id: str,
        section_id: str | None = None,
        question_version_id: str | None = None,
        student_id: str | None = None,
        batch_size: int | None = None,
    ) -> Iterator[GradeRow]:
        """按游标逐页遍历**全部**匹配行（导出的唯一取数入口）。

        授权与过滤校验在**调用时**即完成（不是等到迭代才抛），因此路由层可以用同一段 `try/except`
        统一映射 403/422。遍历在同一条连接上单向前进；导出因此与查询共用同一套 SQL 与同一套游标
        语义——"导出口径 = 查询口径"是结构性的，而不是两处代码碰巧一致。
        """
        size = self._batch_size if batch_size is None else _validated_batch_size(batch_size)
        filters = self._resolve(
            principal=principal,
            course_id=course_id,
            section_id=section_id,
            question_version_id=question_version_id,
            student_id=student_id,
        )
        return self._walk(filters, size)

    def _walk(self, filters: _Filters, size: int) -> Iterator[GradeRow]:
        with self._engine.connect() as connection:
            after: tuple[datetime, uuid.UUID] | None = None
            while True:
                items, next_cursor = _fetch_page(
                    connection, filters=filters, after=after, size=size
                )
                yield from items
                if next_cursor is None:
                    return
                after = decode_cursor(next_cursor)

    @staticmethod
    def _resolve(
        *,
        principal: Principal,
        course_id: str,
        section_id: str | None,
        question_version_id: str | None,
        student_id: str | None,
    ) -> _Filters:
        course = _require_uuid(course_id, "course_id")
        section = _optional_uuid(section_id, "section_id")
        question = _optional_uuid(question_version_id, "question_version_id")
        requested_student = _optional_uuid(student_id, "student_id")
        effective_student = require_grade_authority(
            principal,
            course_id=str(course),
            section_id=None if section is None else str(section),
            student_id=None if requested_student is None else str(requested_student),
        )
        covered = covered_sections(principal, str(course))
        if covered is None:
            sections = None if section is None else (section,)
        else:
            allowed = tuple(uuid.UUID(item) for item in covered)
            if section is not None and section not in allowed:
                raise AuthorizationError("course scope does not cover the requested section")
            sections = allowed if section is None else (section,)
        return _Filters(
            course_id=course,
            sections=sections,
            question_version_id=question,
            student_id=effective_student,
        )


def _fetch_page(
    connection: sa.Connection,
    *,
    filters: _Filters,
    after: tuple[datetime, uuid.UUID] | None,
    size: int,
) -> tuple[tuple[GradeRow, ...], str | None]:
    """取一页：多取一行用来判定"还有下一页"，返回的 `next_cursor` 只由**已返回**的最后一行派生。"""
    records = connection.execute(
        _statement(filters=filters, after=after, limit=size + 1)
    ).fetchall()
    rows = tuple(_row_from_record(record) for record in records[:size])
    next_cursor: str | None = None
    if len(records) > size and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            submitted_at=last.submitted_at, submission_id=last.submission_id
        )
    return rows, next_cursor


def _statement(
    *, filters: _Filters, after: tuple[datetime, uuid.UUID] | None, limit: int
) -> sa.Select[Any]:
    """构造页面查询：提交 ⋈ LATERAL 最新运行 ⋈ 结果，按 `(created_at, submission_id)` 升序。

    定序为什么是"提交时刻升序 + `submission_id` 兜底"：`created_at` 允许同值（批量导入、同一事务），
    只用它做游标会漏行或重复；补上主键即构成**全序**，keyset 判定才有唯一解。升序还使导出期间的
    新提交落在游标之后（既不重复也不错位）。
    """
    latest_run = (
        sa.select(evaluation_runs.c.evaluation_run_id, evaluation_runs.c.state)
        .where(evaluation_runs.c.submission_id == submissions.c.submission_id)
        .order_by(
            evaluation_runs.c.sequence.desc(),
            evaluation_runs.c.created_at.desc(),
            evaluation_runs.c.evaluation_run_id.desc(),
        )
        .limit(1)
        .lateral("latest_run")
    )
    statement = (
        sa.select(
            submissions.c.submission_id,
            submissions.c.student_id,
            submissions.c.course_id,
            submissions.c.section_id,
            submissions.c.question_version_id,
            submissions.c.state.label("submission_state"),
            submissions.c.created_at,
            latest_run.c.evaluation_run_id,
            latest_run.c.state.label("run_state"),
            evaluation_results.c.score,
            evaluation_results.c.max_score,
            evaluation_results.c.is_pass,
        )
        .select_from(
            submissions.outerjoin(latest_run, sa.true()).outerjoin(
                evaluation_results,
                evaluation_results.c.evaluation_run_id == latest_run.c.evaluation_run_id,
            )
        )
        .where(submissions.c.course_id == filters.course_id)
        .order_by(submissions.c.created_at.asc(), submissions.c.submission_id.asc())
        .limit(limit)
    )
    if filters.sections is not None:
        statement = statement.where(submissions.c.section_id.in_(filters.sections))
    if filters.question_version_id is not None:
        statement = statement.where(
            submissions.c.question_version_id == filters.question_version_id
        )
    if filters.student_id is not None:
        statement = statement.where(submissions.c.student_id == uuid.UUID(filters.student_id))
    if after is not None:
        moment, identifier = after
        statement = statement.where(
            sa.or_(
                submissions.c.created_at > moment,
                sa.and_(
                    submissions.c.created_at == moment,
                    submissions.c.submission_id > identifier,
                ),
            )
        )
    return statement


def _row_from_record(record: Any) -> GradeRow:
    """记录 → 契约行；分数只在"运行已 `completed` 且结果已持久化"时出现（否则一律 `None`）。"""
    run_state = record.run_state
    state = str(run_state) if run_state is not None else str(record.submission_state)
    run_id = record.evaluation_run_id
    score: float | None = None
    max_score: float | None = None
    is_pass: bool | None = None
    if state == COMPLETED and run_id is not None and record.score is not None:
        score = float(record.score)
        max_score = None if record.max_score is None else float(record.max_score)
        is_pass = None if record.is_pass is None else bool(record.is_pass)
    return GradeRow(
        submission_id=str(record.submission_id),
        student_id=str(record.student_id),
        student_number=None,
        display_name=None,
        course_id=str(record.course_id),
        section_id=str(record.section_id),
        question_version_id=str(record.question_version_id),
        state=state,
        score=score,
        max_score=max_score,
        is_pass=is_pass,
        submitted_at=record.created_at,
        evaluation_run_id=None if run_id is None else str(run_id),
    )


def _require_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidFilterError(f"{field} must be a UUID") from error


def _optional_uuid(value: str | None, field: str) -> uuid.UUID | None:
    """可选过滤：`None` 与空串都视为"未提供"（查询串里 `?section_id=` 不构成过滤）。"""
    if value is None or value == "":
        return None
    return _require_uuid(value, field)


def _validated_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise InvalidFilterError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    return limit


def _validated_batch_size(batch_size: int) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise InvalidFilterError("batch_size must be a positive integer")
    return batch_size
