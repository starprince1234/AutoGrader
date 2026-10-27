"""隔离区的行级课程范围（P13 的跨课程隔离修复）。

## 为什么必须新增修订

`GET /api/v1/intake/quarantines` 原先只能判定"staff 令牌是否带任意课程作用域"，
因为 `b2.intake_quarantines` 没有任何可用来逐行比对课程/课序的列。结果是**另一门课的教师
也能读到本部署的全部隔离记录**——这是已登记的越权缺口（`tests/test_intake_api.py` 曾把
它钉成"已知缺口"）。P13 要求把授权做到行级，因此必须先让"这条记录属于哪门课/哪个课序"
成为可持久化、可查询的事实。

该事实的**唯一可信来源**是邮件匹配规则本身：每条规则都显式声明 `course_id/section_id`，
它们是运营配置而不是可伪造的邮件内容。B2 只在"全部命中规则候选的 `(course_id, section_id)`
完全一致"时写入这对值；`not_matched`、跨课程/跨课序的 `ambiguous`、以及附件/结构类拒绝
一律写 NULL。**不从 `mailbox`、发件人、附件名或请求参数推测**（那样等于伪造授权依据）。

## 两列与那条 CHECK

| 列 | 类型 | 语义 |
|---|---|---|
| `scope_course_id` | `uuid`（可空） | 匹配候选共识给出的课程 |
| `scope_section_id` | `uuid`（可空） | 匹配候选共识给出的课序 |

CHECK `ck_intake_quarantines_scope` 把"成对存在"钉成单一事实：
`(scope_course_id IS NULL) = (scope_section_id IS NULL)`。少了它，就能落一条"有课程没有课序"
的记录——而授权判定与展示都假设两者要么都有、要么都没有。

索引 `ix_intake_quarantines_scope_created_at` 服务"先按 scope 过滤、再按时间倒序取一页"的
查询形状：数据过滤必须发生在排序、limit 与游标计算**之前**。

## 数据后果：历史行保持 NULL（保守授权）

**这是纯加列，不改动任何既有行。** 0002 以来写下的隔离记录没有、也**无法证明**课程归属
（自动匹配正是没得出身份才隔离的），因此迁移把它们留成 NULL，不做任何回填或猜测。新语义下
NULL scope 只对平台管理员可见/可处置——这是**收紧**，不是丢失数据：行数与其余列逐字节不变。

`downgrade()` 删索引、约束与两列。降级后这些行的课程关联会丢失（授权退回旧的"任意 scope
即可见"行为），因此降级必须写进运维记录（见 `docs/database/migrations.md`）。

## 表标准

主键仍是 `pk_intake_quarantines`（0012 已改名），本修订不新增时间列，故不涉及 `timestamptz(3)`。
约束与索引按 B4 `table-standards.md` 的命名规则写成 `ck_<table>_<subject>` /
`ix_<table>_<columns>`。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260915_0015"
down_revision = "20260915_0014"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "intake_quarantines"
SCOPE_CHECK = "ck_intake_quarantines_scope"
SCOPE_INDEX = "ix_intake_quarantines_scope_created_at"


def upgrade() -> None:
    """加两列 + 成对 CHECK + 查询索引（纯加列，不改既有行）。"""
    op.add_column(
        TABLE,
        sa.Column("scope_course_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("scope_section_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        SCOPE_CHECK,
        TABLE,
        "(scope_course_id IS NULL) = (scope_section_id IS NULL)",
        schema=SCHEMA,
    )
    op.create_index(
        SCOPE_INDEX,
        TABLE,
        ["scope_course_id", "scope_section_id", "created_at"],
        schema=SCHEMA,
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.scope_course_id IS "
        "'匹配规则候选共识给出的课程；NULL 表示无法证明课程归属（仅管理员可见）'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.scope_section_id IS "
        "'匹配规则候选共识给出的课序；与 scope_course_id 成对为空或非空'"
    )


def downgrade() -> None:
    """删索引、约束与两列（**会丢失课程关联**，见模块 docstring 的数据后果）。"""
    op.drop_index(SCOPE_INDEX, table_name=TABLE, schema=SCHEMA)
    op.drop_constraint(SCOPE_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_column(TABLE, "scope_section_id", schema=SCHEMA)
    op.drop_column(TABLE, "scope_course_id", schema=SCHEMA)
