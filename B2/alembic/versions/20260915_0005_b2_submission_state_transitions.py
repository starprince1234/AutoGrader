"""b2 提交状态迁移史（RQ-B-2-60：每次迁移都有记录，失败迁移保留错误类别与脱敏原因）。

为什么单独建表而不是在 `submissions` 里存 JSON：迁移史要**可查询、可审计、可断言**（例如
"静态阻断路径不得出现 `running` 记录"），JSON 列做不到这些；而且它天然只追加，不应被更新。

外键 `submission_id → submissions` 用 `ON DELETE CASCADE`（同一 schema 内，遵守 B4 表规范）；
`reason` 必须脱敏（不存源码片段、绝对路径、内部错误细节）。

**为什么需要 `sequence`**：时间戳只到毫秒，同一事务里连续两次迁移会共享同一 `occurred_at`，
仅按时间排序会让历史顺序退化为随机（实测：全量跑时 `received→validated` 与 `null→received` 会翻位）。
迁移史的顺序是语义的一部分，必须由显式序号保证，而不是指望时间戳足够细。

可回滚：`downgrade()` 删除本表。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0005"
down_revision = "20260915_0004"
branch_labels = None
depends_on = None

SCHEMA = "b2"
_STATES = "'received','validated','queued','running','completed','rejected','failed'"


def upgrade() -> None:
    op.create_table(
        "submission_state_transitions",
        sa.Column("submission_state_transition_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_submission_state_transitions_sequence_positive"),
        sa.CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_STATES})",
            name="ck_submission_state_transitions_from_state",
        ),
        sa.CheckConstraint(f"to_state IN ({_STATES})", name="ck_submission_state_transitions_to_state"),
        sa.CheckConstraint(
            "from_state IS NULL OR from_state <> to_state",
            name="ck_submission_state_transitions_distinct_states",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR char_length(reason) <= 500",
            name="ck_submission_state_transitions_reason_length",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_submission_state_transitions_submission_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "submission_id",
            "sequence",
            name="uq_submission_state_transitions_submission_id_sequence",
        ),
        sa.PrimaryKeyConstraint(
            "submission_state_transition_id", name="pk_submission_state_transitions"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_submission_state_transitions_submission_id_occurred_at",
        "submission_state_transitions",
        ["submission_id", "occurred_at"],
        schema=SCHEMA,
    )

    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.submission_state_transitions IS "
        "'提交状态迁移史：只追加；sequence 保证顺序确定性，reason 必须脱敏（RQ-B-2-60）'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_submission_state_transitions_submission_id_occurred_at",
        table_name="submission_state_transitions",
        schema=SCHEMA,
    )
    op.drop_table("submission_state_transitions", schema=SCHEMA)
