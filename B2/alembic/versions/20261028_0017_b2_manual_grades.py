"""Manual grading: a teacher's own score, and the state that says "not graded yet".

Revision ID: 20261028_0017
Revises: 20260916_0016

Why this is a separate store rather than a nullable column on an evaluation run: the
deterministic score is *recomputable* -- same submission, same cases, same number, always.
A teacher's score is a judgement: it has an author, a time, a reason and a history. Putting
it in the same row as the machine score would make "where did this number come from?"
unanswerable the first time someone asks, and the export unverifiable.

`submissions.grade_status` carries the three states the product needs:

* `deterministic` -- the default: the machine score is the grade (nothing changed for the
  existing flow, including every API-submitted assignment);
* `awaiting_manual` -- a submission that can only be graded by hand (mail intake of the Unix
  homework). **Not graded yet**, so it must not be counted as "not submitted";
* `graded` -- a manual score exists and is what the student sees.

`comment` is nullable on purpose (the product asks for an optional remark); the *history*
row's `reason` is NOT NULL, because a score change without a reason is exactly the change that
cannot be defended later.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261028_0017"
down_revision = "20260916_0016"
branch_labels = None
depends_on = None

SCHEMA = "b2"

TIMESTAMPTZ = "timestamptz(3)"
NOW = "date_trunc('milliseconds', now())"

GRADE_STATUSES = "('deterministic', 'awaiting_manual', 'graded')"
AWAITING = "('awaiting_manual')"


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column(
            "grade_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'deterministic'"),
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_submissions_grade_status",
        "submissions",
        f"grade_status IN {GRADE_STATUSES}",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_submissions_grade_status", "submissions", ["grade_status"], schema=SCHEMA
    )

    op.create_table(
        "manual_grades",
        sa.Column("manual_grade_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Numeric(5, 1), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("graded_by", sa.Uuid(), nullable=False),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(NOW)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(NOW)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_manual_grades_score"),
        sa.CheckConstraint("revision >= 1", name="ck_manual_grades_revision"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_manual_grades_submission_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("manual_grade_id", name="pk_manual_grades"),
        sa.UniqueConstraint("submission_id", name="uq_manual_grades_submission_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "manual_grade_history",
        sa.Column("history_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("previous_score", sa.Numeric(5, 1), nullable=True),
        sa.Column("score", sa.Numeric(5, 1), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("graded_by", sa.Uuid(), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text(NOW)
        ),
        sa.CheckConstraint(
            "previous_score IS NULL OR (previous_score >= 0 AND previous_score <= 100)",
            name="ck_manual_grade_history_previous_score",
        ),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_manual_grade_history_score"),
        sa.CheckConstraint(
            "char_length(btrim(reason)) >= 1", name="ck_manual_grade_history_reason"
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_manual_grade_history_submission_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("history_id", name="pk_manual_grade_history"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_manual_grade_history_submission_id_recorded_at",
        "manual_grade_history",
        ["submission_id", "recorded_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Dropping the tables loses teacher-authored grades, so the downgrade only removes the
    # *state* column: the tables stay so nothing recorded by a human is silently discarded.
    op.drop_index("ix_submissions_grade_status", table_name="submissions", schema=SCHEMA)
    op.drop_constraint("ck_submissions_grade_status", "submissions", schema=SCHEMA)
    op.drop_column("submissions", "grade_status", schema=SCHEMA)
