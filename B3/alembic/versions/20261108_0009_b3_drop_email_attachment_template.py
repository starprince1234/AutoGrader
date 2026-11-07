"""Drop the removed per-question e-mail attachment template from stored manifests.

Revision ID: 20261108_0009
Revises: 20261107_0008
Create Date: 2026-11-08

Why this exists
---------------

``CHG-0032`` removed ``execution.email.required_attachment_template``. Under the
assignment workflow an answer file is named by the question's 1-based position inside the
assignment (``1.txt``, ``2.txt``, ...), not by a per-question template, so the property is
no longer part of the contract, B2's package parser or B3's compiler. B2's parser is strict
(``additionalProperties: false``) and rejects a package that still carries it, which would
make every ``email_delivery`` question unusable.

Rows written before this revision still hold the property in
``b3.question_versions.execution_manifest``. They must be rewritten rather than merely
ignored: ``content_digest`` is computed over the normalized declaration, so a draft whose
stored payload still contains the removed key recomputes a *different* digest than the one
the reference-answer endpoint serves, and every attestation for that draft is refused with
``409`` (the question could never be published).

Only ``email_delivery`` declarations can carry the property, so the JSONB delete is a
no-op for every other row. This migration only rewrites the stored declaration; it does
**not** recompute ``content_digest`` (that is derived content, refreshed by the deploy
reconciliation documented in the production runbook for databases that predate this
change).

Downgrade
---------

Data-losing by construction and intentionally a no-op: the template value is gone, and the
new compiler has no field to restore a default into. Inventing ``"{student_number}.txt"``
on every row would fabricate an authoring decision that nobody made.
"""

from __future__ import annotations

from alembic import op

revision = "20261108_0009"
down_revision = "20261107_0008"
branch_labels = None
depends_on = None

SCHEMA = "b3"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE {SCHEMA}.question_versions
           SET execution_manifest =
               execution_manifest #- '{{email,required_attachment_template}}'
         WHERE execution_manifest->'email' ? 'required_attachment_template'
        """
    )


def downgrade() -> None:
    # No value to restore (see the module docstring): the property was deleted from the
    # contract, so a restored row would still be unreadable by B2's strict parser.
    return
