"""教师端「邮件记录」三态视图的服务层（契约 0.39.0 的 `listMailRecords`）。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `GET /api/v1/intake/mail-records` 与
`MailRecordView`。本模块只读两张表：

- `b2.email_verifications` → `processed`：邮件携带有效一次性核验码并被接受，提交由映射表
  指向作业界面（`assignment_id`/`student_id` 是教师点进去的钥匙，记录本身永远不含答案）；
- `b2.intake_quarantines` → `pending` / `other`：没变成提交的来件，分类事实在收件时写入。

## 三态为什么在写侧判定、读侧只做映射

邮件正文与附件原文**不落库**（平台有意不做第二个邮件仓库），因此"这封邮件是不是一次真实的
提交尝试"只能在收件当时从正文/发件账号/核验码得出。迁移 `20261117_0024` 把这几个事实写进
隔离记录（`format_valid`/`sender_associated`/`assignment_id`/`student_id`），读侧只按固定谓词
把**互斥且覆盖全部行**的两个类别切开：

    pending = format_valid IS TRUE
           OR sender_associated IS TRUE
           OR (format_valid IS NULL AND sender_associated IS NULL
               AND reason_code IN ('email_verification_expired', 'attachment_mapping_invalid'))
    other   = NOT pending

历史行（分类列出现之前写入）两列皆 NULL：其中"失败阶段可推断"的两种原因码意味着正文已经通过
格式校验（过期码/附件映射失败都发生在正文校验之后），因此归 `pending`；其余历史行只能归
`other`——这是契约明文写下的历史归类规则，不是猜测。消费方必须把 `category` 当作**服务端的
答案**，不要自己重算。

## 授权：与隔离列表同一套行级课程作用域

- `processed` 行按提交所属 `(course_id, section_id)` 过滤；`pending`/`other` 按隔离记录自己的
  `scope_course_id/scope_section_id`（成对为空或非空）过滤。
- 管理员无课程限制；staff 只看到被令牌作用域覆盖的行，`scope` 为 NULL 的行**只对管理员可见**
  （无法证明课程归属的邮件不能因为"列表请求"而泄漏给任何 staff）。
- `course_id/section_id` **只是筛选**：staff 显式传作用域之外的课程/课序 → `403`（而不是空页）；
  不带筛选时仍只看自己作用域内的行。
- 行级谓词与用户筛选一起施加在 `ORDER BY`/`LIMIT`/游标判定**之前**：分页与 `next_cursor`
  不会暗示存在被隐藏的行。

## 分页

契约 `CursorPage` 禁止 offset：两个类别分别用 keyset 游标 `(received_at|created_at, id)`、
**倒序**（契约"newest first"）。游标复用 `intake.quarantine.encode_cursor/decode_cursor`——
它只承载"上一页最后一行"的位置，不承载过滤条件与身份，因此伪造游标既不能越权也不能扩大范围。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import require_intake_quarantine_authority
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import email_verifications, intake_quarantines
from autograder_b2.intake.quarantine import (
    InvalidQuarantineFilterError,
    decode_cursor,
    encode_cursor,
    parse_course_filter,
    parse_limit,
    parse_section_filter,
    require_query_scope_authority,
    row_scope_predicate,
)

#: 契约 `MailRecordView.category` 的封闭取值；三个状态互斥且覆盖全部记录。
MailRecordCategory = Literal["processed", "pending", "other"]
CATEGORIES: tuple[MailRecordCategory, ...] = ("processed", "pending", "other")

#: 分类列出现之前写入的隔离记录里，**失败阶段可推断正文已通过格式校验**的原因码：
#: 核验码过期与附件映射失败都发生在正文五行校验之后。其余历史原因码归 `other`。
LEGACY_PENDING_REASONS: tuple[str, ...] = (
    "email_verification_expired",
    "attachment_mapping_invalid",
)


@dataclass(frozen=True, slots=True)
class MailRecordView:
    """契约 `MailRecordView` 的一行：**摘要**，不含正文/附件内容/发件地址/答案。

    `course_id/section_id` 是行级授权使用的课程归属（processed 来自提交，pending/other 来自
    隔离记录的 `scope_*`）；`assignment_id`/`student_id` 只在服务端能证明时非空——它们仅用于
    让教师从作业界面打开提交，**绝不**作为授权依据。
    """

    record_id: str
    category: MailRecordCategory
    subject: str | None
    received_at: datetime
    course_id: str | None
    section_id: str | None
    assignment_id: str | None
    student_id: str | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class MailRecordPage:
    """契约 `CursorPage` + `MailRecordView[]`：一页行 + 下一页游标（`None` 表示没有下一页）。"""

    items: tuple[MailRecordView, ...]
    next_cursor: str | None


def mail_record_view_body(view: MailRecordView) -> dict[str, Any]:
    """契约 `MailRecordView` 的 JSON 形状（`additionalProperties: false`，键逐字一致）。"""
    return {
        "record_id": view.record_id,
        "category": view.category,
        "subject": view.subject,
        "received_at": rfc3339_millis(view.received_at),
        "course_id": view.course_id,
        "section_id": view.section_id,
        "assignment_id": view.assignment_id,
        "student_id": view.student_id,
        "reason_code": view.reason_code,
    }


def mail_record_page_body(page: MailRecordPage) -> dict[str, Any]:
    """契约 `CursorPage` 的 JSON 形状：`{items, next_cursor}`。"""
    return {
        "items": [mail_record_view_body(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


def parse_category(raw: str | None) -> MailRecordCategory:
    """解析 `?category=`：必填，只接受三个状态之一；缺失/非法 → 422。

    契约把它声明为必填且**不给默认值**：页面上的三个 tab 就是该视图的全部，静默回落成
    "列出全部"会让前端以为拿到的是某一个状态，那正是这次要消除的混乱。
    """
    if raw is None or raw == "":
        raise InvalidQuarantineFilterError(
            "category is required and must be one of processed, pending, other"
        )
    if raw not in CATEGORIES:
        raise InvalidQuarantineFilterError(
            "category must be one of processed, pending, other"
        )
    return cast("MailRecordCategory", raw)


class MailRecordService:
    """三个列表的读路径（`listMailRecords`）：只读、无副作用、不触发任何评测。

    实例不持有调用者身份：`principal` 逐次传入，行级可见性每次按令牌重新翻译成 SQL 谓词。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_records(
        self,
        *,
        principal: Principal,
        category: str | None,
        course_id: str | None = None,
        section_id: str | None = None,
        limit: str | None = None,
        cursor: str | None = None,
    ) -> MailRecordPage:
        """按时间倒序取一页**调用者可见**的邮件记录（先按类别，再按课程作用域）。

        异常：`AuthorizationError` → 路由层 403；`InvalidQuarantineFilterError` /
        `InvalidQuarantineCursorError` → 422。`next_cursor` 非空即表示**还有下一页**。
        """
        require_intake_quarantine_authority(principal)
        wanted_category = parse_category(category)
        size = parse_limit(limit)
        wanted_course = parse_course_filter(course_id)
        wanted_section = parse_section_filter(section_id)
        if wanted_section is not None and wanted_course is None:
            raise InvalidQuarantineFilterError("section_id requires course_id")
        require_query_scope_authority(principal, wanted_course, wanted_section)
        after = None if cursor is None else decode_cursor(cursor)

        with self._engine.connect() as connection:
            if wanted_category == "processed":
                rows = connection.execute(
                    _processed_statement(
                        principal=principal,
                        course_id=wanted_course,
                        section_id=wanted_section,
                        after=after,
                        limit=size + 1,
                    )
                ).fetchall()
            else:
                rows = connection.execute(
                    _quarantine_statement(
                        principal=principal,
                        category=wanted_category,
                        course_id=wanted_course,
                        section_id=wanted_section,
                        after=after,
                        limit=size + 1,
                    )
                ).fetchall()

        items = tuple(
            (
                _processed_view_of(row)
                if wanted_category == "processed"
                else _quarantine_view_of(row)
            )
            for row in rows[:size]
        )
        next_cursor: str | None = None
        if len(rows) > size and items:
            last = items[-1]
            next_cursor = encode_cursor(
                recorded_at=last.received_at, intake_quarantine_id=last.record_id
            )
        return MailRecordPage(items=items, next_cursor=next_cursor)


# --- SQL ----------------------------------------------------------------------------------


def _pending_predicate() -> sa.ColumnElement[bool]:
    """`pending` 的判定（读侧唯一一处）；`other` 是它的补集，因此两类别互斥且覆盖全部行。"""
    return sa.or_(
        intake_quarantines.c.format_valid.is_(True),
        intake_quarantines.c.sender_associated.is_(True),
        sa.and_(
            intake_quarantines.c.format_valid.is_(None),
            intake_quarantines.c.sender_associated.is_(None),
            intake_quarantines.c.reason_code.in_(LEGACY_PENDING_REASONS),
        ),
    )


def _category_predicate(category: MailRecordCategory) -> sa.ColumnElement[bool]:
    pending = _pending_predicate()
    return pending if category == "pending" else sa.not_(pending)


def _keyset_clause(
    after: tuple[datetime, uuid.UUID],
    *,
    moment_column: Any,
    identifier_column: Any,
) -> sa.ColumnElement[bool]:
    """keyset "严格后继"：`(时间, id) < (上一页最后一行)`（倒序取页的下一页）。"""
    moment, identifier = after
    return sa.or_(
        moment_column < moment,
        sa.and_(moment_column == moment, identifier_column < identifier),
    )


def _processed_statement(
    *,
    principal: Principal,
    course_id: str | None,
    section_id: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> sa.Select[Any]:
    """`processed` 页：**先过滤**（行级 scope + 用户筛选 + 游标）再排序取页。"""
    statement = sa.select(
        email_verifications.c.verification_id,
        email_verifications.c.subject,
        email_verifications.c.received_at,
        email_verifications.c.course_id,
        email_verifications.c.section_id,
        email_verifications.c.assignment_id,
        email_verifications.c.student_id,
    ).where(
        row_scope_predicate(
            principal,
            course_column=email_verifications.c.course_id,
            section_column=email_verifications.c.section_id,
        )
    )
    if course_id is not None:
        statement = statement.where(email_verifications.c.course_id == uuid.UUID(course_id))
    if section_id is not None:
        statement = statement.where(email_verifications.c.section_id == uuid.UUID(section_id))
    if after is not None:
        statement = statement.where(
            _keyset_clause(
                after,
                moment_column=email_verifications.c.received_at,
                identifier_column=email_verifications.c.verification_id,
            )
        )
    return statement.order_by(
        email_verifications.c.received_at.desc(),
        email_verifications.c.verification_id.desc(),
    ).limit(limit)


def _quarantine_statement(
    *,
    principal: Principal,
    category: MailRecordCategory,
    course_id: str | None,
    section_id: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> sa.Select[Any]:
    """`pending`/`other` 页：类别谓词与行级 scope **都在排序/limit/游标之前**施加。"""
    statement = sa.select(
        intake_quarantines.c.intake_quarantine_id,
        intake_quarantines.c.subject,
        intake_quarantines.c.created_at,
        intake_quarantines.c.scope_course_id,
        intake_quarantines.c.scope_section_id,
        intake_quarantines.c.assignment_id,
        intake_quarantines.c.student_id,
        intake_quarantines.c.reason_code,
        # 分类事实列不直接进响应，但映射层用它给 `category` 贴标签并与 SQL 谓词互相自检。
        intake_quarantines.c.format_valid,
        intake_quarantines.c.sender_associated,
    ).where(
        sa.and_(
            _category_predicate(category),
            row_scope_predicate(
                principal,
                course_column=intake_quarantines.c.scope_course_id,
                section_column=intake_quarantines.c.scope_section_id,
            ),
        )
    )
    if course_id is not None:
        statement = statement.where(
            intake_quarantines.c.scope_course_id == uuid.UUID(course_id)
        )
    if section_id is not None:
        statement = statement.where(
            intake_quarantines.c.scope_section_id == uuid.UUID(section_id)
        )
    if after is not None:
        statement = statement.where(
            _keyset_clause(
                after,
                moment_column=intake_quarantines.c.created_at,
                identifier_column=intake_quarantines.c.intake_quarantine_id,
            )
        )
    return statement.order_by(
        intake_quarantines.c.created_at.desc(),
        intake_quarantines.c.intake_quarantine_id.desc(),
    ).limit(limit)


def _processed_view_of(row: Any) -> MailRecordView:
    """`email_verifications` 行 → 契约视图。`reason_code` 恒为 NULL：它没有被拒。"""
    return MailRecordView(
        record_id=str(row.verification_id),
        category="processed",
        subject=None if row.subject is None else str(row.subject),
        received_at=row.received_at,
        course_id=str(row.course_id),
        section_id=str(row.section_id),
        assignment_id=None if row.assignment_id is None else str(row.assignment_id),
        student_id=str(row.student_id),
        reason_code=None,
    )


def _quarantine_view_of(row: Any) -> MailRecordView:
    """`intake_quarantines` 行 → 契约视图；`category` 由调用方按查询的类别贴标签。"""
    return MailRecordView(
        record_id=str(row.intake_quarantine_id),
        category="pending" if _row_is_pending(row) else "other",
        subject=None if row.subject is None else str(row.subject),
        received_at=row.created_at,
        course_id=None if row.scope_course_id is None else str(row.scope_course_id),
        section_id=None if row.scope_section_id is None else str(row.scope_section_id),
        assignment_id=None if row.assignment_id is None else str(row.assignment_id),
        student_id=None if row.student_id is None else str(row.student_id),
        reason_code=str(row.reason_code),
    )


def _row_is_pending(row: Any) -> bool:
    """视图标签与 SQL 谓词必须给出同一答案的**兜底自检**（两者都只读同一组列）。

    这里重复一遍谓词只是为了在映射层不可能与筛选层不一致；真实分页始终由 SQL 谓词决定，
    因此即使这里写错，查询也不会把一行放进错误的页——只会让该行的 `category` 字段自相矛盾。
    """
    if row.format_valid is True or row.sender_associated is True:
        return True
    return (
        row.format_valid is None
        and row.sender_associated is None
        and str(row.reason_code) in LEGACY_PENDING_REASONS
    )


__all__ = [
    "CATEGORIES",
    "LEGACY_PENDING_REASONS",
    "MailRecordCategory",
    "MailRecordPage",
    "MailRecordService",
    "MailRecordView",
    "mail_record_page_body",
    "mail_record_view_body",
    "parse_category",
]
