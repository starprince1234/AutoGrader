"""隔离区的人工处置留痕与邮件摘要（老师复核界面）。

## 为什么新增这一版

老师复核隔离邮件时需要看到"这封邮件大概是什么"（邮件摘要）以及"谁在什么时候处理了它"。
原来的 `intake_quarantines` 只有 `resolved` 布尔、`resolved_student_id` 与 `resolution_note`：

- 没有主题（`subject`），列表只能显示邮箱/UID，老师无法一眼认出邮件；
- 没有处置结果（`resolution`），"归属给学生"与"驳回"无法区分；
- 没有处理时间与操作人（`resolved_at`/`resolved_by`），复核决定没有留痕。

本修订只**加列**，不动既有行。历史已处理行没有 `resolution`/`resolved_at`/`resolved_by`，
因此审计约束允许"已处理但审计字段为空"这一种遗留形态；界面如实显示"未记录"，不猜测。

## 列与约束

| 列 | 类型 | 语义 |
|---|---|---|
| `subject` | `text`（可空） | 邮件主题头，已在入口按可打印字符、空白折叠与 200 字截断；正文/附件仍不落库 |
| `resolution` | `text`（可空） | `attributed`（归属学生）或 `dismissed`（驳回） |
| `resolved_at` | `timestamptz`（可空） | 人工处置时间（服务端时钟） |
| `resolved_by` | `uuid`（可空） | 人工处置人（访问令牌 `sub`） |

- `ck_intake_quarantines_subject`：空或 1..200 字符（与契约 `maxLength: 200` 一致）。
- `ck_intake_quarantines_decision`：空或两个枚举值之一。
- `ck_intake_quarantines_decision_audit` 把处置结果与身份字段钉在一起：
  - 未处理：审计字段必须全空、`resolved_student_id` 也必须为空；
  - 归属：`resolution='attributed'`、`resolved=true`、学生非空、时间与操作人非空；
  - 驳回：`resolution='dismissed'`、`resolved=true`、学生为空、时间与操作人非空；
  - 兼容 0016 之前已归属的历史行：`resolved=true` 且审计字段为空（界面显示"未记录"）。

## 回滚

`downgrade()` 删除三个约束与四列；会丢失处置结果与留痕（`resolved` 布尔仍保留）。
降级前应导出 `resolution/resolved_at/resolved_by`，见 `docs/database/migrations.md`。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "20260916_0016"
down_revision = "20260915_0015"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "intake_quarantines"
SUBJECT_CHECK = "ck_intake_quarantines_subject"
DECISION_CHECK = "ck_intake_quarantines_decision"
AUDIT_CHECK = "ck_intake_quarantines_decision_audit"
#: 0013 的旧约束：`resolved = (resolved_student_id IS NOT NULL)`。它把"已处理"等同于"有学生"，
#: 与"驳回"不相容；本修订用 `AUDIT_CHECK` 取代它（保留历史"已处理但无处置结果"的形态）。
LEGACY_RESOLUTION_CHECK = "ck_intake_quarantines_resolution"
LEGACY_RESOLUTION_EXPRESSION = "resolved = (resolved_student_id IS NOT NULL)"

AUDIT_EXPRESSION = (
    "(resolved = false AND resolution IS NULL AND resolved_at IS NULL"
    " AND resolved_by IS NULL AND resolved_student_id IS NULL)"
    " OR (resolved = true AND resolution IS NULL"
    " AND resolved_at IS NULL AND resolved_by IS NULL)"
    " OR (resolved = true AND resolution = 'attributed'"
    " AND resolved_student_id IS NOT NULL"
    " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
    " OR (resolved = true AND resolution = 'dismissed'"
    " AND resolved_student_id IS NULL"
    " AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)"
)


def upgrade() -> None:
    """加四列与三个 CHECK，并用新的处置审计约束取代 0013 的"已处理=有学生"约束。"""
    op.drop_constraint(LEGACY_RESOLUTION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.add_column(TABLE, sa.Column("subject", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("resolution", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE,
        sa.Column("resolved_at", TIMESTAMP(timezone=True, precision=3), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("resolved_by", sa.Uuid(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        SUBJECT_CHECK,
        TABLE,
        "subject IS NULL OR char_length(subject) BETWEEN 1 AND 200",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        DECISION_CHECK,
        TABLE,
        "resolution IS NULL OR resolution IN ('attributed', 'dismissed')",
        schema=SCHEMA,
    )
    op.create_check_constraint(AUDIT_CHECK, TABLE, AUDIT_EXPRESSION, schema=SCHEMA)
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.subject IS "
        "'邮件主题头（可打印、折叠空白、最多 200 字）；正文与附件仍然不落库'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.resolution IS "
        "'人工处置结果：attributed=归属学生，dismissed=驳回；空表示未处理或历史遗留'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.resolved_at IS "
        "'人工处置时间（服务端时钟）；历史行可能为空'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.resolved_by IS "
        "'人工处置人（访问令牌 sub）；历史行可能为空'"
    )


def downgrade() -> None:
    """删三个约束与四列（**会丢失处置结果与留痕**，见模块 docstring）。"""
    op.drop_constraint(AUDIT_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(DECISION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(SUBJECT_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_column(TABLE, "resolved_by", schema=SCHEMA)
    op.drop_column(TABLE, "resolved_at", schema=SCHEMA)
    op.drop_column(TABLE, "resolution", schema=SCHEMA)
    op.drop_column(TABLE, "subject", schema=SCHEMA)
    op.create_check_constraint(
        LEGACY_RESOLUTION_CHECK,
        TABLE,
        LEGACY_RESOLUTION_EXPRESSION,
        schema=SCHEMA,
    )
