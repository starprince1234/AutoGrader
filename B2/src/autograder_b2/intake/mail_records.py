"""教师端「邮件记录」三态视图的服务层（契约 0.39.0，分类语义见 0.44.0）。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `GET /api/v1/intake/mail-records` 与
`MailRecordView`。本模块只读两张表：

- `b2.email_verifications` → `processed` 里**变成了提交**的那些：邮件携带有效一次性核验码并被
  接受，提交由映射表指向作业界面（`assignment_id`/`student_id` 是教师点进去的钥匙，记录本身
  永远不含答案）；
- `b2.intake_quarantines` → 其余全部行：`pending`（真实提交尝试、**尚未人工处置**）、
  `processed` 里**已由教师处置**的那些（打回 `rejected` / 作废 `dismissed` / 旧的手工归属
  `attributed`）、以及 `other`（垃圾与格式不合格）。

## 三态为什么这样切

`pending` 是**待办队列**：它必须是真的还要人管的东西。因此判定是"真实提交尝试 **且** 没有
人工处置记录"——一旦教师打了回或标了作废，这封信就已经被决定了，继续留在待处理里会让队列永远
排不空（这是线上报过的 bug：打回后刷新仍在待处理）。被决定的记录进入 `processed`，并且
`reason_code` 仍然回填收件时的原因，页面据此显示"为什么它没成为提交"，而不是假装它是一份提交。

分类事实在收件时写入（迁移 `20261117_0024` 的 `format_valid`/`sender_associated`/
`assignment_id`/`student_id`），读侧只按固定谓词分类：

    attempt  = format_valid IS TRUE
            OR sender_associated IS TRUE
            OR (format_valid IS NULL AND sender_associated IS NULL
                AND reason_code IN ('email_verification_expired', 'attachment_mapping_invalid'))
    settled  = resolved IS TRUE
    disposed = settled AND (resolution IS NULL OR resolution <> 'completed')
    pending  = attempt AND NOT settled
    other    = NOT attempt AND NOT settled
    processed = (变成提交的邮件) ∪ disposed

`completed` 属于 settled 但不属于 disposed：那封信确实变成了提交，已经由 `email_verifications`
列在 `processed`，再从隔离表列一次会让同一封信在同一个 tab 出现两行。

历史行（分类列出现之前写入）两列皆 NULL：其中"失败阶段可推断"的两种原因码意味着正文已经通过
格式校验（过期码/附件映射失败都发生在正文校验之后），因此算 attempt；其余历史行只能归
`other`——这是契约明文写下的历史归类规则，不是猜测。消费方必须把 `category` 当作**服务端的
答案**，不要自己重算。

## 授权：与隔离列表同一套行级课程作用域

- 提交来源的 `processed` 行按提交所属 `(course_id, section_id)` 过滤；人工处置的 `processed`
  行与 `pending`/`other` 一样按隔离记录自己的 `scope_course_id/scope_section_id`（成对为空或
  非空）过滤。
- 管理员无课程限制；staff 只看到被令牌作用域覆盖的行，`scope` 为 NULL 的行**只对管理员可见**
  （无法证明课程归属的邮件不能因为"列表请求"而泄漏给任何 staff）。
- `course_id/section_id` **只是筛选**：staff 显式传作用域之外的课程/课序 → `403`（而不是空页）；
  不带筛选时仍只看自己作用域内的行。
- 行级谓词与用户筛选一起施加在 `ORDER BY`/`LIMIT`/游标判定**之前**：分页与 `next_cursor`
  不会暗示存在被隐藏的行。

## 分页

契约 `CursorPage` 禁止 offset：三个类别分别用 keyset 游标 `(received_at|created_at, id)`、
**倒序**（契约"newest first"）。`processed` 是两个来源合并后的一页，因此两个分支都先按同一个
游标裁剪，再 `UNION ALL` 后整体排序取页——先在 Python 里各取一页再合并会把"某一来源的第一页"
当成全局第一页。游标复用 `intake.quarantine.encode_cursor/decode_cursor`：它只承载"上一页最后
一行"的位置，不承载过滤条件与身份，因此伪造游标既不能越权也不能扩大范围。
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


def _attempt_predicate() -> sa.ColumnElement[bool]:
    """"这是一次真实提交尝试"（收件时落库的分类事实，历史行按契约规则推断）。"""
    return sa.or_(
        intake_quarantines.c.format_valid.is_(True),
        intake_quarantines.c.sender_associated.is_(True),
        sa.and_(
            intake_quarantines.c.format_valid.is_(None),
            intake_quarantines.c.sender_associated.is_(None),
            intake_quarantines.c.reason_code.in_(LEGACY_PENDING_REASONS),
        ),
    )


def _settled_predicate() -> sa.ColumnElement[bool]:
    """"这一行已经有结论"：任何人工处置，`completed` 也算。

    `pending`/`other` 只列**还没有结论**的隔离行。`completed` 必须有结论——那封信确实变成了
    提交（由 `email_verifications` 列在 `processed`）；若不在这里排除，它会同时出现在 `pending`，
    于是待办队列里永远留着一条谁也处理不掉的记录。
    """
    return intake_quarantines.c.resolved.is_(True)


def _disposed_predicate() -> sa.ColumnElement[bool]:
    """"被教师决定过、且没有因此生成提交"的隔离记录——`processed` 的第二来源。

    打回（`rejected`）与作废（`dismissed`）是对这封信的**终局决定**；`attributed` 是更早的
    手工归属（记了学生，但没生成提交）；处置留痕出现之前的行只有 `resolved=true`，`resolution`
    为 NULL——它们同样是人工决定过的。

    `completed` 刻意排除：那封信确实变成了提交，已经由 `email_verifications` 列在 `processed`
    里；从隔离表再列一次会让同一封信在同一个 tab 里出现两行。

    三值逻辑说明：`resolved IS TRUE` 永不为 NULL；`resolution IS NULL OR resolution <> ...` 对
    NULL 行由前者成立。因此整个表达式不会求值为 NULL，对它取 `NOT` 是安全的。
    """
    return sa.and_(
        intake_quarantines.c.resolved.is_(True),
        sa.or_(
            intake_quarantines.c.resolution.is_(None),
            intake_quarantines.c.resolution != "completed",
        ),
    )


def _category_predicate(category: MailRecordCategory) -> sa.ColumnElement[bool]:
    """隔离表两类别互斥且覆盖全部**还没有结论**的隔离行；有结论的行只出现在 `processed`。"""
    unsettled = sa.not_(_settled_predicate())
    if category == "pending":
        return sa.and_(_attempt_predicate(), unsettled)
    return sa.and_(sa.not_(_attempt_predicate()), unsettled)


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
    """`processed` 页：**变成提交的邮件** ∪ **被教师决定过的隔离记录**，合并后取一页。

    两个来源的列形状相同（`record_id`/`subject`/`moment`/作用域/`assignment_id`/`student_id`/
    `reason_code`），因此在 SQL 里 `UNION ALL` 再统一排序取页：各自先取 `limit` 再在 Python 里
    合并会把"某一来源的第一页"当成全局第一页，游标也会错位。

    行级 scope 各自施加在自己的表上（提交按提交所属课程，人工处置按隔离记录证明的课程），
    两者都是**先过滤再排序**。

    两个来源靠 `reason_code` 区分：变成提交的那封信没有失败原因（CAST(NULL AS TEXT) → NULL），
    教师打回/作废的记录带着收件时的原因码（该列 NOT NULL），映射层据此决定显示"查看作业"
    还是"为什么没成为提交"。
    """
    submitted = (
        sa.select(
            email_verifications.c.verification_id.label("record_id"),
            email_verifications.c.subject.label("subject"),
            email_verifications.c.received_at.label("moment"),
            email_verifications.c.course_id.label("course_id"),
            email_verifications.c.section_id.label("section_id"),
            email_verifications.c.assignment_id.label("assignment_id"),
            email_verifications.c.student_id.label("student_id"),
            sa.cast(sa.null(), sa.Text).label("reason_code"),
        )
        .where(
            row_scope_predicate(
                principal,
                course_column=email_verifications.c.course_id,
                section_column=email_verifications.c.section_id,
            )
        )
    )
    if course_id is not None:
        submitted = submitted.where(
            email_verifications.c.course_id == uuid.UUID(course_id)
        )
    if section_id is not None:
        submitted = submitted.where(
            email_verifications.c.section_id == uuid.UUID(section_id)
        )
    if after is not None:
        submitted = submitted.where(
            _keyset_clause(
                after,
                moment_column=email_verifications.c.received_at,
                identifier_column=email_verifications.c.verification_id,
            )
        )

    decided = (
        sa.select(
            intake_quarantines.c.intake_quarantine_id.label("record_id"),
            intake_quarantines.c.subject.label("subject"),
            intake_quarantines.c.created_at.label("moment"),
            intake_quarantines.c.scope_course_id.label("course_id"),
            intake_quarantines.c.scope_section_id.label("section_id"),
            intake_quarantines.c.assignment_id.label("assignment_id"),
            intake_quarantines.c.student_id.label("student_id"),
            intake_quarantines.c.reason_code.label("reason_code"),
        )
        .where(_disposed_predicate())
        .where(
            row_scope_predicate(
                principal,
                course_column=intake_quarantines.c.scope_course_id,
                section_column=intake_quarantines.c.scope_section_id,
            )
        )
    )
    if course_id is not None:
        decided = decided.where(
            intake_quarantines.c.scope_course_id == uuid.UUID(course_id)
        )
    if section_id is not None:
        decided = decided.where(
            intake_quarantines.c.scope_section_id == uuid.UUID(section_id)
        )
    if after is not None:
        decided = decided.where(
            _keyset_clause(
                after,
                moment_column=intake_quarantines.c.created_at,
                identifier_column=intake_quarantines.c.intake_quarantine_id,
            )
        )

    merged = submitted.union_all(decided).subquery()
    return (
        sa.select(*merged.c)
        .order_by(merged.c.moment.desc(), merged.c.record_id.desc())
        .limit(limit)
    )


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
        intake_quarantines.c.resolved,
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
    """`processed` 页的一行（合并来源）：`reason_code` 区分两种来源。

    变成提交的那封信没有失败原因（`reason_code` 为 NULL）；教师打回/作废的信带着**收件时的
    原因码**——页面用它说明"为什么它没成为提交"，而不是把它显示成一份提交。
    """
    return MailRecordView(
        record_id=str(row.record_id),
        category="processed",
        subject=None if row.subject is None else str(row.subject),
        received_at=row.moment,
        course_id=None if row.course_id is None else str(row.course_id),
        section_id=None if row.section_id is None else str(row.section_id),
        assignment_id=None if row.assignment_id is None else str(row.assignment_id),
        student_id=None if row.student_id is None else str(row.student_id),
        reason_code=None if row.reason_code is None else str(row.reason_code),
    )


def _quarantine_view_of(row: Any) -> MailRecordView:
    """`intake_quarantines` 行（`pending`/`other` 页）→ 契约视图。"""
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

    有结论的行（`resolved=true`，含 `completed`）不可能是 `pending`：它们要么已经变成提交，
    要么已经被老师决定，由 `processed` 负责。
    """
    if bool(row.resolved):
        return False
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
