"""学生分层学习提示的存储落点（AI-FLOW-01B / 成员四）。

## 为什么是一张**新表**，而不是复用 `evaluation_commentaries`

评语（`RQ-T-20`/`RQ-T-30`）与学生分层提示是**两个不同读者、不同权限、不同生命周期**的产物：

- 评语由教师/助教生成并按课程策略决定学生可否看见；提示**只能由 run owner 学生**生成与读取，
  且刻意**没有**教师逐条查看学生提示的接口；
- 评语一行可以被重新生成覆盖（教师编辑动作）；提示与**一次具体的 `evaluation_run`** 绑定，
  学生重新提交会产生新的 run，旧 run 的提示必须原样保留在历史里（`TC-HINT-091`）。

把两者塞进同一张表会让"谁能读这一行"取决于一张按行变化的策略表，而权限判定应当只依赖
**明确的归属列**。因此本表独立，且 `student_id` 是**落库的归属事实**（不是从请求参数读来的）：
提示属于谁，在行里写着。

## 表里刻意**没有**的东西

没有 `score`、没有 `is_pass`、没有 `result_digest`、没有 `case_evidence`、没有源码、没有
参考答案、没有隐藏用例、没有期望输出。提示永远不能成为分数依据（`LearningHintsResponse`
的契约形状里也没有这些字段），因此把它们写进来只会制造一条"看起来像成绩证据"的旁路。

## 状态与字段的配对（`ck_evaluation_learning_hints_shape`）

| status | hint_steps | failure_category | generated_at |
|---|---|---|---|
| `pending` | `[]` | NULL | NULL |
| `ready` | 1–3 条 JSON 对象 | NULL | NOT NULL |
| `failed` | `[]` | NOT NULL（脱敏类别） | NOT NULL |

`unavailable`（本部署没有 provider）**不落库**：它是配置事实，由 `GET` 现场表达——与评语轨
`unavailable_body` 同一口径。库里因此不存在"一行说本部署没配模型"这种会过期的记录。

## 幂等

`uq_evaluation_learning_hints_evaluation_run_id`：一次运行最多一行。同一 `Idempotency-Key`
重放返回该行、不重复调用 provider；`ready` 行是终局，重新请求不会被一次"重试"悄悄改成半截数据。
学生显式重试（`failed` 之后）才允许覆盖同一行，因而"同一 run 只有一条提示"仍是结构性事实。

## 表标准

时间列全部 `timestamptz(3)`（B4 `table-standards.md`，与迁移 0012 的整改口径一致）；
主键 `pk_evaluation_learning_hints`；约束命名 `ck_/uq_/fk_<table>_<subject>`。
`downgrade()` 只删本表：不改动 `evaluation_results` / `evaluation_commentaries` /
`progress_events` 的任何行。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260915_0015"
down_revision = "20260915_0014"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "evaluation_learning_hints"

#: 提示步骤 JSON 的上限（契约 `hint_steps.maxItems: 3`）。
MAX_STEPS = 3

#: 状态与字段的配对（服务层写入路径同语义；`ready` 的条数另由 JSON 长度约束兜底）。
_SHAPE = (
    "(status = 'pending' AND hint_steps = '[]'::jsonb"
    " AND failure_category IS NULL AND generated_at IS NULL)"
    " OR (status = 'ready' AND jsonb_array_length(hint_steps) BETWEEN 1 AND 3"
    " AND failure_category IS NULL AND generated_at IS NOT NULL)"
    " OR (status = 'failed' AND hint_steps = '[]'::jsonb"
    " AND failure_category IS NOT NULL AND generated_at IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("evaluation_learning_hint_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "hint_steps",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("context_scope", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("failure_category", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            postgresql.TIMESTAMP(timezone=True, precision=3),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_evaluation_learning_hints_evaluation_run_id",
        ),
        sa.PrimaryKeyConstraint(
            "evaluation_learning_hint_id", name="pk_evaluation_learning_hints"
        ),
        sa.UniqueConstraint(
            "evaluation_run_id",
            name="uq_evaluation_learning_hints_evaluation_run_id",
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','failed')",
            name="ck_evaluation_learning_hints_status",
        ),
        sa.CheckConstraint(_SHAPE, name="ck_evaluation_learning_hints_shape"),
        sa.CheckConstraint(
            "length(provider) BETWEEN 1 AND 100"
            " AND length(model) BETWEEN 1 AND 100"
            " AND length(prompt_version) BETWEEN 1 AND 100",
            name="ck_evaluation_learning_hints_provenance_lengths",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 200",
            name="ck_evaluation_learning_hints_idempotency_key_length",
        ),
        sa.CheckConstraint(
            "failure_category IS NULL OR length(failure_category) BETWEEN 1 AND 100",
            name="ck_evaluation_learning_hints_failure_category_length",
        ),
        sa.CheckConstraint(
            # 上下文范围同样必须可审计：空数组等于"读了什么"这句话被收回。
            "jsonb_typeof(context_scope) = 'array' AND jsonb_array_length(context_scope) >= 1",
            name="ck_evaluation_learning_hints_context_scope_is_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(hint_steps) = 'array'",
            name="ck_evaluation_learning_hints_steps_is_array",
        ),
        schema=SCHEMA,
    )
    # 预算/频率计数的唯一查询形态：某学生在一个时间窗内请求过多少次。
    op.create_index(
        "ix_evaluation_learning_hints_student_requested_at",
        TABLE,
        ["student_id", "requested_at"],
        schema=SCHEMA,
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{TABLE} IS "
        "'学生分层学习提示（AI-FLOW-01B）：与一次 evaluation_run 绑定，仅 run owner 可读写。"
        "不含分数、逐项证据、源码、参考答案或隐藏用例；删除本表不影响权威分数与报告'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.hint_steps IS "
        "'1-3 条有序提示（level/title/text）；逐层展开只是学习节奏控制，不是安全边界'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.context_scope IS "
        "'生成时实际使用的证据/公开题目上下文字段清单，随 provenance 返回以便外部审计'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evaluation_learning_hints_student_requested_at", table_name=TABLE, schema=SCHEMA
    )
    op.drop_table(TABLE, schema=SCHEMA)
