"""Keep the message a submission arrived in, so a teacher can read what the student sent.

Revision ID: 20261028_0018
Revises: 20261028_0017

Why store a *bounded excerpt* rather than the message: the grader needs enough context to
understand the answer (and to see "I attached 2024141460192.txt"), while storing whole
mailboxes would turn the platform into an archive of unrelated correspondence it has no
mandate to keep. `body_excerpt` is capped at 4000 characters by CHECK, contact details are
redacted before insert, and `truncated` records that the excerpt is not the whole message so a
reader never mistakes it for one.

A submission created through the API has no row here; that is the "uploaded, not mailed in"
case, and the API reports it as 404 rather than an empty object.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261028_0018"
down_revision = "20261028_0017"
branch_labels = None
depends_on = None

SCHEMA = "b2"

TIMESTAMPTZ = "timestamptz(3)"
NOW = "date_trunc('milliseconds', now())"

#: The contract caps the excerpt at 4000 characters; the database enforces the same number so
#: a truncation bug cannot quietly store more than the API promises to return.
MAX_EXCERPT = 4000


def upgrade() -> None:
    op.create_table(
        "intake_messages",
        sa.Column("intake_message_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("body_excerpt", sa.Text(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(NOW)
        ),
        sa.CheckConstraint(
            f"char_length(body_excerpt) <= {MAX_EXCERPT}",
            name="ck_intake_messages_body_excerpt",
        ),
        sa.CheckConstraint(
            "char_length(sender) >= 3", name="ck_intake_messages_sender"
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_intake_messages_submission_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("intake_message_id", name="pk_intake_messages"),
        sa.UniqueConstraint("submission_id", name="uq_intake_messages_submission_id"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("intake_messages", schema=SCHEMA)
