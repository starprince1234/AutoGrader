"""隔离区的人工完成、暂存与作废（老师复核的完整处置闭环）。

## 为什么需要这一版

0016 只记录了"归属/驳回"，但老师真正需要的是三件事：

1. **匹配用户**：选学生 + 课程 + 作业后，把原邮件的附件**真正**变成该学生的提交；
2. **暂存**：先放着，稍后再处理；
3. **标记作废**：垃圾邮件/错发邮件，关闭但不归属。

完成需要把"处置结果"链接到真实提交（`resolved_submission_id`），暂存需要独立于
`resolved` 的字段（`held`），否则"暂存"会被误当成"已处理"。

## 列

| 列 | 类型 | 语义 |
|---|---|---|
| `resolved_submission_id` | `uuid`（可空） | 匹配用户后生成的提交；只有 `resolution='completed'` 非空 |
| `held` | `boolean` 非空默认 false | 暂存标记；暂存行仍未处理，可继续匹配/作废/恢复 |
| `held_at` | `timestamptz(3)`（可空） | 暂存时间 |
| `held_by` | `uuid`（可空） | 暂存操作人 |

## 约束

- `ck_intake_quarantines_decision`：`resolution` 允许 `attributed`/`completed`/`dismissed`；
- `ck_intake_quarantines_hold`：`held` 为真时 `held_at`/`held_by` 必须非空，为假时必须为空；
- `ck_intake_quarantines_decision_audit`：待处理/暂存行不得带处置字段；`completed` 必须有学生、
  提交、时间和操作人；`attributed`（旧人工归属）没有提交；`dismissed` 没有学生和提交；
  0016 之前的历史已处理行继续允许"有学生但无处置结果"。

## 历史数据

纯加列：历史行 `held=false`、`resolved_submission_id=NULL`，不改变任何既有行。
`downgrade()` 删除四列并恢复 0016 的约束；已完成的提交本身不会被删除（它属于正常提交链）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "20261029_0019"
# 合并两支：0017/0018（人工评分、来信内容）与 `20260915_0016`（学习提示）。
# 只写 0018 会留下两个 head，`alembic upgrade head` 无法执行。
down_revision = ("20261028_0018", "20260915_0016")
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "intake_quarantines"
DECISION_CHECK = "ck_intake_quarantines_decision"
AUDIT_CHECK = "ck_intake_quarantines_decision_audit"
HOLD_CHECK = "ck_intake_quarantines_hold"

DECISION_EXPRESSION = "resolution IS NULL OR resolution IN ('attributed', 'completed', 'dismissed')"

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
    " OR (resolved = true AND resolution = 'dismissed'"
    " AND resolved_student_id IS NULL AND resolved_submission_id IS NULL"
    " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
)

HOLD_EXPRESSION = (
    "(held = false AND held_at IS NULL AND held_by IS NULL)"
    " OR (held = true AND held_at IS NOT NULL AND held_by IS NOT NULL)"
)


def upgrade() -> None:
    """加四列，替换处置约束并新增暂存约束（纯加列，不改既有行）。"""
    op.drop_constraint(AUDIT_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(DECISION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("resolved_submission_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("held", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("held_at", TIMESTAMP(timezone=True, precision=3), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("held_by", sa.Uuid(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(DECISION_CHECK, TABLE, DECISION_EXPRESSION, schema=SCHEMA)
    op.create_check_constraint(HOLD_CHECK, TABLE, HOLD_EXPRESSION, schema=SCHEMA)
    op.create_check_constraint(AUDIT_CHECK, TABLE, AUDIT_EXPRESSION, schema=SCHEMA)
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.resolved_submission_id IS "
        "'匹配用户后生成的提交；仅 resolution=completed 时非空'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.held IS "
        "'暂存：记录仍未处理，稍后再决定；与 resolved 互斥'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.held_at IS '暂存时间（服务端时钟）'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.held_by IS '暂存操作人（访问令牌 sub）'"
    )


def downgrade() -> None:
    """删除暂存/完成留痕列并恢复 0016 的约束。"""
    op.drop_constraint(AUDIT_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(HOLD_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(DECISION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_column(TABLE, "held_by", schema=SCHEMA)
    op.drop_column(TABLE, "held_at", schema=SCHEMA)
    op.drop_column(TABLE, "held", schema=SCHEMA)
    op.drop_column(TABLE, "resolved_submission_id", schema=SCHEMA)
    op.create_check_constraint(
        DECISION_CHECK,
        TABLE,
        "resolution IS NULL OR resolution IN ('attributed', 'dismissed')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        AUDIT_CHECK,
        TABLE,
        "(resolved = false AND resolution IS NULL AND resolved_at IS NULL"
        " AND resolved_by IS NULL AND resolved_student_id IS NULL)"
        " OR (resolved = true AND resolution IS NULL"
        " AND resolved_at IS NULL AND resolved_by IS NULL)"
        " OR (resolved = true AND resolution = 'attributed'"
        " AND resolved_student_id IS NOT NULL"
        " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
        " OR (resolved = true AND resolution = 'dismissed'"
        " AND resolved_student_id IS NULL"
        " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
        schema=SCHEMA,
    )
