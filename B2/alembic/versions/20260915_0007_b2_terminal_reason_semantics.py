"""修正 0006 两条过度收紧的 CHECK：`completed` 是成功态，不应被迫携带终态原因。

背景（真缺陷，由编排工作项实测发现）：0006 里我写了两条约束，要求凡终态都必须有 `terminal_reason`：

- `ck_evaluation_runs_terminal_consistency`：`state IN ('completed','rejected','failed')` => `finished_at IS NOT NULL AND terminal_reason IS NOT NULL`
- `ck_progress_events_terminal_frames`：`event <> 'progress'` => `terminal_reason IS NOT NULL`

但契约 `MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame.terminal_reason` 枚举只有
`null / static_blocked / security_blocked / infrastructure_error / internal_error`，并且明确写着
`static_blocked`/`security_blocked` 伴随 `rejected`、`infrastructure_error`/`internal_error` 伴随 `failed`
——**没有"成功"取值**。因此 `completed` 只能是 `terminal_reason = NULL`。原约束与契约不可同时满足，
后果是"一次真正成功的评测无法落库"：编排层要么写假原因（不诚实，被禁止），要么把成功降级成
`failed + internal_error`（把系统故障强加给学生，同样被禁止）。

本迁移只放宽这两条，不放宽任何安全或可追溯性要求：
- `completed`：必须有 `finished_at`、**必须没有** `terminal_reason`（成功不需要失败原因）；
- `rejected`/`failed`：必须有 `finished_at` **且必须有** `terminal_reason`（拒绝/失败必须说明原因）；
- 非终态（`queued`/`running`）：不得有 `finished_at`、不得有 `terminal_reason`；
- 进度帧同理：`completed` 帧不得带失败原因，其余端子帧必须带。

其余约束（含 `ck_progress_events_completed_has_score`：`completed` 帧必须有分数）保持不变。

可回滚：`downgrade()` 恢复 0006 的原约束（注意：若库中已有 `completed` 且 `terminal_reason IS NULL`
的行，回滚会失败——这是有意为之，回滚到"成功不可表达"的状态必须先清理这类行）。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0007"
down_revision = "20260915_0006"
branch_labels = None
depends_on = None

SCHEMA = "b2"

_RUNS_FIXED = (
    "(state IN ('completed','rejected','failed') AND finished_at IS NOT NULL"
    " AND ((state = 'completed' AND terminal_reason IS NULL)"
    " OR (state <> 'completed' AND terminal_reason IS NOT NULL)))"
    " OR (state IN ('queued','running') AND finished_at IS NULL AND terminal_reason IS NULL)"
)
_RUNS_ORIGINAL = (
    "(state IN ('completed','rejected','failed') AND finished_at IS NOT NULL AND terminal_reason IS NOT NULL)"
    " OR (state IN ('queued','running') AND finished_at IS NULL AND terminal_reason IS NULL)"
)
_FRAMES_FIXED = (
    "event = 'progress'"
    " OR (event = 'completed' AND terminal_reason IS NULL)"
    " OR (event <> 'completed' AND terminal_reason IS NOT NULL)"
)
_FRAMES_ORIGINAL = "event = 'progress' OR terminal_reason IS NOT NULL"


def upgrade() -> None:
    op.drop_constraint(
        "ck_evaluation_runs_terminal_consistency", "evaluation_runs", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_evaluation_runs_terminal_consistency", "evaluation_runs", _RUNS_FIXED, schema=SCHEMA
    )
    op.drop_constraint(
        "ck_progress_events_terminal_frames", "progress_events", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_progress_events_terminal_frames", "progress_events", _FRAMES_FIXED, schema=SCHEMA
    )
    op.execute(
        f"COMMENT ON CONSTRAINT ck_evaluation_runs_terminal_consistency ON {SCHEMA}.evaluation_runs IS "
        "'completed 必须有 finished_at 且 terminal_reason 为 NULL；rejected/failed 必须两者都有'"
    )
    op.execute(
        f"COMMENT ON CONSTRAINT ck_progress_events_terminal_frames ON {SCHEMA}.progress_events IS "
        "'completed 帧不带失败原因，其余端子帧必须带'"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_evaluation_runs_terminal_consistency", "evaluation_runs", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_evaluation_runs_terminal_consistency", "evaluation_runs", _RUNS_ORIGINAL, schema=SCHEMA
    )
    op.drop_constraint(
        "ck_progress_events_terminal_frames", "progress_events", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_progress_events_terminal_frames", "progress_events", _FRAMES_ORIGINAL, schema=SCHEMA
    )
    sa.text("")  # 占位：本迁移不做数据变更，仅约束
