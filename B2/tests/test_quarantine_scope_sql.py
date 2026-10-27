"""行级 scope 谓词的 SQL 形状（P13 的授权关键点）。

不需要数据库：用 PostgreSQL 方言**编译** `_page_statement`，断言：

- staff 的 WHERE 段包含 `scope_course_id/scope_section_id` 的条件（NULL scope 行因此不可见）；
- 过滤条件出现在 `LIMIT` 之前（SQL 语义上先过滤再分页；游标条件同段）；
- 管理员没有 scope 谓词（全局视图），但用户筛选仍然生效；
- 令牌里的课序级作用域要求记录课序精确相等，课程级作用域覆盖该课程全部课序。

真实数据库上的行级隔离由 `tests/test_intake_api.py` 的跨 scope 矩阵覆盖；这里钉住的是
"谓词真的被拼进 SQL 且顺序正确"，防止有人把过滤挪到 Python 侧（那样分页与游标就先看到了
未授权行）。
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from autograder_b2.auth.tokens import CourseScope, Principal
from autograder_b2.intake.quarantine import _page_statement

COURSE_A = "11111111-1111-4111-8111-111111111111"
COURSE_B = "22222222-2222-4222-8222-222222222222"
SECTION_1 = "33333333-3333-4333-8333-333333333333"
SECTION_2 = "44444444-4444-4444-8444-444444444444"


def _principal(roles: tuple[str, ...], scopes: tuple[CourseScope, ...]) -> Principal:
    return Principal(
        user_id="99999999-9999-4999-8999-999999999999",
        roles=roles,
        scopes=scopes,
        token_id="token",
    )


def _sql(principal: Principal, *, course_id: str | None = None) -> str:
    statement = _page_statement(
        principal=principal,
        course_id=course_id,
        section_id=None,
        reason_code=None,
        resolved=False,
        after=None,
        limit=51,
    )
    return str(statement.compile(dialect=postgresql.dialect()))


def test_staff_predicate_filters_null_scope_and_row_scope() -> None:
    staff = _principal(("teacher",), (CourseScope(course_id=COURSE_A, section_id=SECTION_1),))
    sql = _sql(staff)

    where = sql.split("WHERE", 1)[1].split("LIMIT", 1)[0]
    assert "scope_course_id" in where, "staff 必须按行 scope 过滤"
    assert "scope_course_id IS NOT NULL" in where, "NULL scope 行不得进入 staff 结果"
    assert "scope_section_id" in where
    assert sql.index("WHERE") < sql.index("LIMIT"), "过滤必须发生在分页之前"


def test_course_level_scope_covers_any_section() -> None:
    staff = _principal(("teacher",), (CourseScope(course_id=COURSE_A),))
    where = _sql(staff).split("WHERE", 1)[1].split("LIMIT", 1)[0]

    assert "scope_course_id" in where
    # 课程级作用域不附加课序相等条件（与 Principal.covers_course 的语义一致）。
    assert "scope_section_id =" not in where


def test_admin_has_no_scope_predicate_but_keeps_filters() -> None:
    admin = _principal(("admin",), ())
    sql = _sql(admin, course_id=COURSE_A)

    where = sql.split("WHERE", 1)[1].split("LIMIT", 1)[0]
    assert "scope_course_id IS NOT NULL" not in where, "管理员必须能看到未关联行"
    assert "scope_course_id" in where, "管理员显式课程筛选仍然生效"
    assert sql.index("WHERE") < sql.index("LIMIT")


def test_multiple_staff_scopes_are_unioned_with_or() -> None:
    staff = _principal(
        ("teacher",),
        (
            CourseScope(course_id=COURSE_A, section_id=SECTION_1),
            CourseScope(course_id=COURSE_B, section_id=SECTION_2),
        ),
    )
    where = _sql(staff).split("WHERE", 1)[1].split("LIMIT", 1)[0]

    assert " OR " in where.upper(), "多个作用域必须并集，而不是只认第一个"
    assert where.count("scope_course_id") >= 2
