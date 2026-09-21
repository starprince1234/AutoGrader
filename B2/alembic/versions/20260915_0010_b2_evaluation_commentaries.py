"""评语轨的存储表（`RQ-T-20` / `ADR-0009` 决策 2）。

为什么是一张**独立的表**，而不是复用 `evaluation_results` 或报告 JSON 的一个字段：

1. `ADR-0009` 决策 2 要求评语"有自己的存储位置与自己的 API 表达"。塞进报告 JSON 会立刻违反
   同一条裁决的另一半——报告受 `additionalProperties: false` 约束，且报告是**可重放**的物证，
   而评语是**非确定性**的模型输出；两者混在一份文档里，`result_digest` 就再也不可能稳定。
2. 用户指令里的验收方式直接依赖它：**删掉这张表，重放同一提交必须得到完全相同的分数与逐项证据**。
   本表没有任何列被其他表引用，评测链路也不读它，"删除不影响可重放性"因此是结构性的。
3. 表里**没有分数、没有证据**：只有文本、状态与 provenance。契约 `EvaluationCommentary`
   "无 `score` 字段、无 `evidence` 字段"的刻意设计，在存储层是同一条边界。

约束的意图（不是形式主义）：

- `uq_evaluation_commentaries_run`：一次运行最多一条评语。重新生成是**覆盖**——若允许多行，
  "读哪一条"就需要一条规则，而那条规则迟早会被读成"判分依据的版本"。
- `ck_evaluation_commentaries_shape`：状态与字段必须成对（`pending` 无文本无时间；
  `ready` 有文本有时间；`failed` 无文本、有原因、有尝试时间）。**不允许**"有文本却标 failed"
  或"failed 却不说原因"这类自相矛盾的行。
- `ck_evaluation_commentaries_scope`：`input_scope` 必须是非空 JSON 数组——白名单**必须**可审计，
  空清单等于把"读了什么"这句话收回。
- `fk_evaluation_commentaries_evaluation_run_id`：评语属于运行。运行行不可删（成绩依据），
  因此这条外键只是完整性，不是清理路径。

可回滚：`downgrade()` 删除本表。回滚会让 `GET/POST .../commentary` 的两个端点失去存储位置，
因此必须同步撤回契约里的两个端点与四个 schema（见 `ADR-0009` 的回滚说明），不能只删表。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0010"
down_revision = "20260915_0009"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "evaluation_commentaries"

#: 状态与字段的配对（与 `commentary.store` 的写入路径同语义）。
_SHAPE = (
    "(status = 'pending' AND text IS NULL AND failure_reason IS NULL AND generated_at IS NULL)"
    " OR (status = 'ready' AND text IS NOT NULL AND failure_reason IS NULL"
    " AND generated_at IS NOT NULL)"
    " OR (status = 'failed' AND text IS NULL AND failure_reason IS NOT NULL"
    " AND generated_at IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("evaluation_commentary_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_scope", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("generated_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_evaluation_commentaries_evaluation_run_id",
        ),
        sa.UniqueConstraint(
            "evaluation_run_id", name="uq_evaluation_commentaries_evaluation_run_id"
        ),
        sa.PrimaryKeyConstraint(
            "evaluation_commentary_id", name="pk_evaluation_commentaries"
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','failed')",
            name="ck_evaluation_commentaries_status",
        ),
        sa.CheckConstraint(_SHAPE, name="ck_evaluation_commentaries_shape"),
        sa.CheckConstraint(
            "length(provider) BETWEEN 1 AND 100"
            " AND length(model) BETWEEN 1 AND 100"
            " AND length(prompt_version) BETWEEN 1 AND 100",
            name="ck_evaluation_commentaries_provenance_lengths",
        ),
        sa.CheckConstraint(
            # 契约 `text.maxLength: 8000`；空文本同样不允许（"有评语"却什么都没说）。
            "text IS NULL OR length(text) BETWEEN 1 AND 8000",
            name="ck_evaluation_commentaries_text_length",
        ),
        sa.CheckConstraint(
            "failure_reason IS NULL OR length(failure_reason) BETWEEN 1 AND 500",
            name="ck_evaluation_commentaries_failure_reason_length",
        ),
        sa.CheckConstraint(
            # 白名单必须可审计：空数组等于"读了什么"这句话被收回。
            "jsonb_typeof(input_scope) = 'array' AND jsonb_array_length(input_scope) >= 1",
            name="ck_evaluation_commentaries_scope_is_array",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_evaluation_commentaries_status", TABLE, ["status"], schema=SCHEMA
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{TABLE} IS "
        "'教学评语轨（RQ-T-20/ADR-0009）：非确定性模型输出，独立于分数轨。"
        "没有任何其他表引用它，评测链路也不读它——删除本表不影响可重放性'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.input_scope IS "
        "'生成时实际读取的字段清单（白名单），随 provenance 返回以便外部审计'"
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_commentaries_status", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
