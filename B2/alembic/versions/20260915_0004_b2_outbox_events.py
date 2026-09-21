"""b2 事件 outbox 表（MAIN AsyncAPI：先写 outbox 再发，at-least-once，DLQ 不静默）。

为什么需要 outbox 而不是"业务提交后直接 publish"：契约要求事件**永不丢失**，而"先写库、再发
网络"之间存在崩溃窗口。outbox 把"要发布的事件"与业务变更放进**同一个数据库事务**，再由派发器
（`WP-B2-C` 的另一小类）取出投递——业务成功则事件必然存在，NATS 不可用只影响投递时延。

本表由接收层写入的第一条事件是 `autograder.v1.submission.accepted`；后续评测 4 事件复用同一张表。

列意图：
- `event_id`：契约的去重键（消费者按它做 inbox 幂等）；
- `subject` / `event_type`：NATS subject 与事件类型同名；
- `deduplication_key`：**发布幂等**——同一提交的 `submission.accepted` 不因幂等重放而重复入队；
- `payload`：JSONB，只含标识与去标识化值（不含源码/答案/隐藏用例/凭据）；
- `published_at` / `attempts` / `last_error`：投递状态；`dead_lettered_at` 非空表示已进 DLQ。

可回滚：`downgrade()` 删除本表。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260915_0004"
down_revision = "20260915_0003"
branch_labels = None
depends_on = None

SCHEMA = "b2"


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("producer", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("deduplication_key", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("enqueued_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("published_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dead_lettered_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.CheckConstraint("producer = 'B2'", name="ck_outbox_events_producer_is_b2"),
        sa.CheckConstraint("event_type LIKE 'autograder.v1.%'", name="ck_outbox_events_event_type_prefix"),
        sa.CheckConstraint("subject = event_type", name="ck_outbox_events_subject_matches_type"),
        sa.CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_non_negative"),
        sa.CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 500",
            name="ck_outbox_events_last_error_length",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR dead_lettered_at IS NULL",
            name="ck_outbox_events_not_published_and_dead_lettered",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_outbox_events"),
        schema=SCHEMA,
    )
    # 发布幂等：同一 (subject, 去重键) 只允许一条；无去重键的事件不受约束。
    op.create_index(
        "uq_outbox_events_subject_deduplication_key",
        "outbox_events",
        ["subject", "deduplication_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deduplication_key IS NOT NULL"),
    )
    # 派发器的工作队列：未发布且未进 DLQ 的行按入队顺序取。
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["enqueued_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("published_at IS NULL AND dead_lettered_at IS NULL"),
    )
    op.create_index(
        "ix_outbox_events_correlation_id", "outbox_events", ["correlation_id"], schema=SCHEMA
    )

    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.outbox_events IS "
        "'事件 outbox：与业务变更同事务写入，派发器随后投递（at-least-once，DLQ 不静默）'"
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_correlation_id", table_name="outbox_events", schema=SCHEMA)
    op.drop_index("ix_outbox_events_pending", table_name="outbox_events", schema=SCHEMA)
    op.drop_index(
        "uq_outbox_events_subject_deduplication_key", table_name="outbox_events", schema=SCHEMA
    )
    op.drop_table("outbox_events", schema=SCHEMA)
