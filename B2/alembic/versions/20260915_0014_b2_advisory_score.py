"""AI 评分参考的存储落点（`RQ-T-30` / `ADR-0014` / `CHG-0018`）。

## 为什么必须在**评语表里**加列，而不是开一张新表

`ADR-0014` 决策 1 把它定成 `EvaluationCommentary` 的**子对象**，`CHG-0018` §4 又明确**没有**
"只生成参考分、不生成评语"的开关：参考分与评语是**同一次生成的同一个产物**。开第二张表会立刻
造出"有参考分没有评语"（或反之）这种没有任何语义的状态组合——那正是 `CHG-0018` 拒绝开关的
同一条理由。因此一行的四个新列与 `text` 同生同死。

## 为什么不干脆把整个 `AdvisoryScore` 塞进一个 jsonb

契约的四个字段里 `max_score` 是**系统填入的事实**，另外三个是模型输出。把它们放进同一个
jsonb 会让"谁写了这个数"在存储层不可区分——而这正是本 ADR 要防的事（模型给的满分可能错，
且错了看不出来）。四列分开后，`advisory_max_score` 只由 B2 的取证代码写，写它的代码路径里
根本不存在模型输入。

## 四列与三条 CHECK 的意图

| 列 | 类型 | 语义 |
|---|---|---|
| `advisory_score` | `numeric`（可空） | 模型的建议分（`AdvisoryScore.score`） |
| `advisory_max_score` | `numeric`（可空） | 满分。**由 `b2.case_evidence.weight` 求和填入**，不由模型生成 |
| `advisory_rationale` | `text`（可空） | 依据（契约 `minLength 1 / maxLength 2000`） |
| `advisory_confidence` | `text`（可空） | `low` / `medium` / `high` |

- `ck_evaluation_commentaries_shape`（**重写**，不是新增）：状态与字段的配对必须继续成立，
  并且把四个新列纳入同一条配对规则——`pending`/`failed` 行**不得**带参考分（生成没成功），
  `ready` 行要么四个全空（模型这次没给建议，契约允许 `advisory_score: null`），要么四个全有。
  "有分没有满"或"有依据没有分"这类半截行因此落不了库。
- `ck_evaluation_commentaries_advisory_bounds`：`advisory_score BETWEEN 0 AND advisory_max_score`
  且 `advisory_max_score > 0`。这是 `ADR-0014` 决策 6 与 `RQ-T-30` 验收 6 在**存储层**的表达：
  越界的结果是"整次生成失败"，所以越界值从来不该存在——约束让"服务层的判断"与"库里的行"
  不可能分叉。**注意它不截断**：越界值是写不进去的，而不是被写成边界值。
- `ck_evaluation_commentaries_advisory_confidence` / `..._advisory_rationale`：枚举与非空长度，
  与契约同值；理由列的长度判定在服务层也做一次（契约 `maxLength`），库只兜底同一数字。

## 数据后果

纯加列：`0002`～`0013` 以来写下的每一行评语在新列上都是 `NULL`，读回来就是"这次没有参考分"，
与它们当时的真实状态一致（那些生成发生在参考分存在之前）。`downgrade()` 删列删约束并**还原
旧的 shape CHECK 文本**，行数与其余列逐字节不变——但参考分会随之丢失，因此降级后必须把
"已生成的参考分不可读"这件事写进运维记录（见 `docs/database/migrations.md`）。

## 表标准

本修订不新增时间列（故不涉及 `timestamptz(3)`），也不改主键（0012 已把主键名整成
`pk_evaluation_commentaries`）。新约束按 B4 `table-standards.md` 的命名规则写成
`ck_<table>_<subject>`。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260915_0014"
down_revision = "20260915_0013"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "evaluation_commentaries"
SHAPE_CHECK = "ck_evaluation_commentaries_shape"

#: 契约 `AdvisoryScore.confidence` 的枚举（库与解析器共用同一份字面量）。
CONFIDENCES = ("low", "medium", "high")
#: 契约 `AdvisoryScore.rationale.maxLength`。
RATIONALE_LIMIT = 2000

#: 四个参考分列的**全空/全有**配对片段（`ready` 行的两种合法形态）。
_ALL_EMPTY = (
    "advisory_score IS NULL AND advisory_max_score IS NULL"
    " AND advisory_rationale IS NULL AND advisory_confidence IS NULL"
)
_ALL_PRESENT = (
    "advisory_score IS NOT NULL AND advisory_max_score IS NOT NULL"
    " AND advisory_rationale IS NOT NULL AND advisory_confidence IS NOT NULL"
)

#: 0010 的旧文本（`downgrade()` 必须把它还原成逐字相同的一句）。
_LEGACY_SHAPE = (
    "(status = 'pending' AND text IS NULL AND failure_reason IS NULL AND generated_at IS NULL)"
    " OR (status = 'ready' AND text IS NOT NULL AND failure_reason IS NULL"
    " AND generated_at IS NOT NULL)"
    " OR (status = 'failed' AND text IS NULL AND failure_reason IS NOT NULL"
    " AND generated_at IS NOT NULL)"
)

#: 本修订的 shape 文本：旧配对 + 参考分列的配对。
_SHAPE = (
    f"(status = 'pending' AND text IS NULL AND failure_reason IS NULL"
    f" AND generated_at IS NULL AND {_ALL_EMPTY})"
    f" OR (status = 'ready' AND text IS NOT NULL AND failure_reason IS NULL"
    f" AND generated_at IS NOT NULL AND ({_ALL_EMPTY} OR {_ALL_PRESENT}))"
    f" OR (status = 'failed' AND text IS NULL AND failure_reason IS NOT NULL"
    f" AND generated_at IS NOT NULL AND {_ALL_EMPTY})"
)


def upgrade() -> None:
    """加四列 + 重写 shape CHECK + 三条参考分约束（纯加列，不改既有行）。"""
    op.add_column(TABLE, sa.Column("advisory_score", sa.Numeric(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE, sa.Column("advisory_max_score", sa.Numeric(), nullable=True), schema=SCHEMA
    )
    op.add_column(TABLE, sa.Column("advisory_rationale", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE, sa.Column("advisory_confidence", sa.Text(), nullable=True), schema=SCHEMA
    )
    op.drop_constraint(SHAPE_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.create_check_constraint(SHAPE_CHECK, TABLE, _SHAPE, schema=SCHEMA)
    op.create_check_constraint(
        "ck_evaluation_commentaries_advisory_bounds",
        TABLE,
        "advisory_score IS NULL"
        " OR (advisory_max_score > 0 AND advisory_score BETWEEN 0 AND advisory_max_score)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_evaluation_commentaries_advisory_confidence",
        TABLE,
        "advisory_confidence IS NULL"
        f" OR advisory_confidence IN ({', '.join(repr(item) for item in CONFIDENCES)})",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_evaluation_commentaries_advisory_rationale",
        TABLE,
        "advisory_rationale IS NULL"
        f" OR char_length(advisory_rationale) BETWEEN 1 AND {RATIONALE_LIMIT}",
        schema=SCHEMA,
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.advisory_score IS "
        "'模型的顾问性评分建议（RQ-T-30/ADR-0014）。它不进分数轨：分数、逐项证据、result_digest、"
        "绑定摘要、成绩导出与任何事件都不含它'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.advisory_max_score IS "
        "'满分，由 B2 从 b2.case_evidence.weight 求和填入（ADR-0014 决策 2），**不由模型生成**'"
    )


def downgrade() -> None:
    """删四条约束与四列，并把 shape CHECK 还原成 0010 的文本（**会丢失已生成的参考分**）。"""
    op.drop_constraint(
        "ck_evaluation_commentaries_advisory_rationale", TABLE, type_="check", schema=SCHEMA
    )
    op.drop_constraint(
        "ck_evaluation_commentaries_advisory_confidence", TABLE, type_="check", schema=SCHEMA
    )
    op.drop_constraint(
        "ck_evaluation_commentaries_advisory_bounds", TABLE, type_="check", schema=SCHEMA
    )
    op.drop_constraint(SHAPE_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.create_check_constraint(SHAPE_CHECK, TABLE, _LEGACY_SHAPE, schema=SCHEMA)
    op.drop_column(TABLE, "advisory_confidence", schema=SCHEMA)
    op.drop_column(TABLE, "advisory_rationale", schema=SCHEMA)
    op.drop_column(TABLE, "advisory_max_score", schema=SCHEMA)
    op.drop_column(TABLE, "advisory_score", schema=SCHEMA)
