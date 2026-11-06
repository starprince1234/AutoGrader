"""Allow the accepted version of an AI generation to disappear.

Revision ID: 20261107_0006
Revises: 20260915_0005
Create Date: 2026-11-07

Why this exists
---------------

`b3.question_draft_generations.accepted_question_version_id` is declared
``ON DELETE SET NULL`` -- deleting the version is *supposed* to leave the de-identified ledger
row intact with no version. But the check constraint added with the table required

    accepted_at IS NULL OR (proposal IS NULL AND accepted_question_version_id IS NOT NULL)

so the SET NULL produced a **check violation** (SQLSTATE 23514) the moment the referenced
version was deleted. B3 maps unhandled ``DBAPIError`` to ``500`` with the detail
"the request could not be completed", which is exactly what a teacher saw when deleting an
AI-generated draft -- while hand-created drafts (no generation row) deleted fine.

The invariant that still matters for provenance is the one kept here: an accepted row has no
proposal left. Whether its version still exists is a fact about the question bank, not a rule
this ledger can hold once "delete a draft" exists.
"""

from __future__ import annotations

from alembic import op

revision = "20261107_0006"
down_revision = "20260915_0005"
branch_labels = None
depends_on = None

SCHEMA = "b3"
TABLE = "question_draft_generations"
CONSTRAINT = "ck_question_draft_generations_accepted_has_no_proposal"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.{TABLE}
            ADD CONSTRAINT {CONSTRAINT}
            CHECK (accepted_at IS NULL OR proposal IS NULL)
        """
    )


def downgrade() -> None:
    # Restoring the stricter rule fails if any accepted row has already lost its version
    # (which is precisely the state this revision allows). That is deliberate: a downgrade
    # must not silently delete ledger rows, so it refuses instead of "fixing" them.
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.{TABLE}
            ADD CONSTRAINT {CONSTRAINT}
            CHECK (
                accepted_at IS NULL
                OR (proposal IS NULL AND accepted_question_version_id IS NOT NULL)
            )
        """
    )
