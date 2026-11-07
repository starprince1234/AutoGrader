"""作业维度接入提交：`b2.submissions.assignment_id`（可空）+ 按作业过滤的索引（CHG-0032）。

## 为什么加这一列

一次提交 = 一道题；而"这道题属于哪个作业"是 B3 的事实、不是 B2 的事实。B2 只保存收到的
`assignment_id`（与 `course_id`/`section_id`/`question_version_id` 同一性质：**标识**，不做跨 schema
外键），用于：接收时校验作业受众、作业级成绩过滤、提交视图与 `submission.accepted` 事件。

## 为什么是 NULL 且不回填

历史提交（本迁移之前接收的行）**没有**作业归属。任何"按题目反推作业"或"按时间猜"的回填都是编造
事实：同一题目版本可以被多个作业引用，历史行也没有留下可用以判定的收件上下文。规格（§1.3）明确
要求"不猜测归属、不删除、不回填"：这些行继续可评测（评测只需要 `question_version_id`）、可查成绩
（按题目过滤），但**不出现在**任何作业级视图/过滤里。因此本迁移：

- 不回填任何一行；
- 不加 `NOT NULL`（历史 NULL 是**如实**的数据状态，不是待修复的缺陷）；
- 不删任何行。

`ix_submissions_assignment_id` 支撑作业级过滤（`WHERE assignment_id = ?`），并与既有的
`ix_submissions_course_id_section_id` / `ix_submissions_question_version_id` 一样是普通 btree 索引。

## 回滚

`downgrade()` 删索引与列。丢掉的是本迁移新增的作业归属（历史行本来就是 NULL，回滚对它们无影响）；
这是一次结构回退，不涉及"把已写入的作业归属改写回 NULL"之外的额外数据操作——`DROP COLUMN` 即
删除该列的全部值。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261107_0021"
down_revision = "20261107_0020"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "submissions"
INDEX = "ix_submissions_assignment_id"


def upgrade() -> None:
    # 列追加在**末尾**：`db/schema.py` 的映像按同一顺序追加，`test_storage_cas` 逐列按
    # `ordinal_position` 比对，两者必须一致。
    op.add_column(
        TABLE, sa.Column("assignment_id", sa.Uuid(), nullable=True), schema=SCHEMA
    )
    op.create_index(INDEX, TABLE, ["assignment_id"], schema=SCHEMA)
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.assignment_id IS "
        "'该提交回答的作业（B3 的事实；B2 只存标识，不做跨 schema 外键）。历史提交为 NULL："
        "不猜测归属、不回填，因此不出现在任何作业级过滤里'"
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE, schema=SCHEMA)
    op.drop_column(TABLE, "assignment_id", schema=SCHEMA)
