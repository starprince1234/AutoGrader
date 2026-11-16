"""Add multi-tier ordinal/label and grading_prompt to reference_answers."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261114_0013"
down_revision = "20261108_0012"
branch_labels = None
depends_on = None

SCHEMA = "b3"


def upgrade() -> None:
    op.add_column(
        "reference_answers",
        sa.Column(
            "ordinal",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "reference_answers",
        sa.Column(
            "label",
            sa.String(length=200),
            nullable=False,
            server_default=sa.text("'标准参考答案'"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "reference_answers",
        sa.Column("grading_prompt", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_reference_answers_question_version_id_language",
        "reference_answers",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_reference_answers_question_version_id_ordinal",
        "reference_answers",
        ["question_version_id", "ordinal"],
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_reference_answers_ordinal_non_negative",
        "reference_answers",
        "ordinal >= 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_reference_answers_label_not_empty",
        "reference_answers",
        "length(label) >= 1",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_reference_answers_label_not_empty",
        "reference_answers",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_reference_answers_ordinal_non_negative",
        "reference_answers",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "uq_reference_answers_question_version_id_ordinal",
        "reference_answers",
        schema=SCHEMA,
        type_="unique",
    )
    op.execute(
        sa.text(
            "DELETE FROM b3.reference_answers WHERE ordinal > 0"
        )
    )
    op.create_unique_constraint(
        "uq_reference_answers_question_version_id_language",
        "reference_answers",
        ["question_version_id", "language"],
        schema=SCHEMA,
    )
    op.drop_column("reference_answers", "grading_prompt", schema=SCHEMA)
    op.drop_column("reference_answers", "label", schema=SCHEMA)
    op.drop_column("reference_answers", "ordinal", schema=SCHEMA)
