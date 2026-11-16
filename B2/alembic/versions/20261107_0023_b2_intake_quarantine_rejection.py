"""隔离区的「打回」：记录来件发件地址，并允许 `resolution='rejected'`。

## 为什么需要这一版

契约 0.38.0 新增了 `rejectIntakeQuarantine`：老师判定「这次提交失败」，平台**同时**告诉
发件人。后一半是重点——被隔离的邮件没有产生提交、没有分数、也没有任何确认，发件人本来
什么都不知道；通知的收件人只能是**这封邮件真实的 From 地址**，所以隔离账本必须先把地址
记下来，否则「打回」只能发到一个猜出来的地方。

## 列

| 列 | 类型 | 语义 |
|---|---|---|
| `sender` | `text`（可空） | 来件的 From 地址（小写、有界，与提交绑定用的是同一形态）；历史行 `NULL` |

`sender` 为 `NULL` 不等于「没有发件人」：它表示这条记录早于本列，平台**不知道**地址，
因此打回时只能如实回答 `notification=unavailable`，而不是改寄一个看起来合理的地址。

## 约束

`ck_intake_quarantines_decision` 与 `ck_intake_quarantines_decision_audit` 都要容纳新值：

- `decision`：`resolution` 允许 `attributed`/`completed`/`dismissed`/`rejected`；
- `decision_audit`：`rejected` 与 `dismissed` 一样**不得带学生与提交**，但必须带处置时间与
  操作人。两者在数据上必须可区分——「老师丢弃了这一行」和「提交失败且已通知发件人」是
  两个不同的决定，合并会让复核页再也回答不了「学生被告知了吗」。

## 历史数据

纯加列：历史行 `sender=NULL`，`resolution` 不变。`downgrade()` 删除该列并恢复 0019 的约束；
`rejected` 行在降级后无法通过旧 CHECK，因此降级会先把它们改回 `dismissed`（保留「已处理」
的事实，不静默回到未处理）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261107_0023"
down_revision = "20261107_0022"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "intake_quarantines"
DECISION_CHECK = "ck_intake_quarantines_decision"
AUDIT_CHECK = "ck_intake_quarantines_decision_audit"

DECISION_EXPRESSION = (
    "resolution IS NULL"
    " OR resolution IN ('attributed', 'completed', 'dismissed', 'rejected')"
)

AUDIT_EXPRESSION = (
    "(resolved = false AND resolution IS NULL AND resolved_student_id IS NULL"
    " AND resolved_submission_id IS NULL AND resolved_at IS NULL AND resolved_by IS NULL)"
    " OR (resolved = true AND resolution IS NULL AND resolved_student_id IS NOT NULL)"
    " OR (resolved = true AND resolution = 'attributed'"
    " AND resolved_student_id IS NOT NULL AND resolved_submission_id IS NULL"
    " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
    " OR (resolved = true AND resolution = 'completed'"
    " AND resolved_student_id IS NOT NULL AND resolved_submission_id IS NOT NULL"
    " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
    " OR (resolved = true AND resolution IN ('dismissed', 'rejected')"
    " AND resolved_student_id IS NULL AND resolved_submission_id IS NULL"
    " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
)


def upgrade() -> None:
    """记下发件地址，并放宽两个处置约束以容纳 `rejected`。"""
    op.drop_constraint(AUDIT_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(DECISION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.add_column(TABLE, sa.Column("sender", sa.Text(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(DECISION_CHECK, TABLE, DECISION_EXPRESSION, schema=SCHEMA)
    op.create_check_constraint(AUDIT_CHECK, TABLE, AUDIT_EXPRESSION, schema=SCHEMA)
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.sender IS "
        "'来件的 From 地址：打回通知的唯一收件人；历史行 NULL 表示未记录'"
    )


def downgrade() -> None:
    """把 `rejected` 折叠回 `dismissed`，恢复 0019 的约束并删除该列。"""
    op.drop_constraint(AUDIT_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(DECISION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.execute(
        f"UPDATE {SCHEMA}.{TABLE} SET resolution = 'dismissed'"
        " WHERE resolution = 'rejected'"
    )
    op.drop_column(TABLE, "sender", schema=SCHEMA)
    op.create_check_constraint(
        DECISION_CHECK,
        TABLE,
        "resolution IS NULL OR resolution IN ('attributed', 'completed', 'dismissed')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        AUDIT_CHECK,
        TABLE,
        "(resolved = false AND resolution IS NULL AND resolved_student_id IS NULL"
        " AND resolved_submission_id IS NULL AND resolved_at IS NULL AND resolved_by IS NULL)"
        " OR (resolved = true AND resolution IS NULL AND resolved_student_id IS NOT NULL)"
        " OR (resolved = true AND resolution = 'attributed'"
        " AND resolved_student_id IS NOT NULL AND resolved_submission_id IS NULL"
        " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
        " OR (resolved = true AND resolution = 'completed'"
        " AND resolved_student_id IS NOT NULL AND resolved_submission_id IS NOT NULL"
        " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
        " OR (resolved = true AND resolution = 'dismissed'"
        " AND resolved_student_id IS NULL AND resolved_submission_id IS NULL"
        " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
        schema=SCHEMA,
    )
