"""运行表增加**三态安全结论**快照列（`security_status`）。

## 为什么需要这一列

契约把"三态安全结论"列为评语轨**允许读取**的输入之一（`b2.yaml` 的 `getEvaluationCommentary`
说明第 1 条 / `RQ-T-20` 验收 1）。B2 的现状是：该结论在评测过程中由 `security.decide_static`
算出来，随后只活在**报告 JSON** 里（`security_conclusion.status`），而报告 JSON 同时承载隐藏用例的
期望输出——为了读一个 `found`/`not_found`/`not_checked` 就把整份报告载入内存，等于把受保护内容
放上评语生成路径，直接违反同一条验收的第 2 句。

从 `b2.security_findings` 的行数**推不出**这个三态：`not_found`（跑了、没发现）与
`not_checked`（根本没跑）在那里都是"零行"。把后者报成前者，正是本项目明令禁止的
"把'未检查'写成'安全'"。因此这里把结论**原样落库**，而不是让下游去猜。

## 语义

- `found` / `not_found` / `not_checked`：与契约 `security_conclusion.status` 逐字一致的取值域；
- `NULL`：这次运行**没有走到安全判定阶段**（例如取包失败）。NULL 的含义是"没有结论"，
  不是 `not_found`——下游必须如实把它当成"未检查/无结论"。

只增一列、可空、无默认值：既有的历史行保持 NULL（那时确实没有这一列的事实），不回填、不猜测。

可回滚：`downgrade()` 删除该列与约束。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0011"
down_revision = "20260915_0010"
branch_labels = None
depends_on = None

SCHEMA = "b2"


def upgrade() -> None:
    op.add_column(
        "evaluation_runs",
        sa.Column("security_status", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_evaluation_runs_security_status",
        "evaluation_runs",
        "security_status IS NULL OR security_status IN ('found','not_found','not_checked')",
        schema=SCHEMA,
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.evaluation_runs.security_status IS "
        "'三态安全结论快照（found/not_found/not_checked）：与契约 security_conclusion.status 同取值域；"
        "NULL = 本次运行没有走到安全判定阶段（没有结论，不是 not_found）'"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_evaluation_runs_security_status", "evaluation_runs", schema=SCHEMA, type_="check"
    )
    op.drop_column("evaluation_runs", "security_status", schema=SCHEMA)
