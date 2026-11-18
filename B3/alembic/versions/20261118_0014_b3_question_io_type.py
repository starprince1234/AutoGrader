"""Add immutable io_type column to b3.questions with backfill and reconciliation."""

from __future__ import annotations

import json
import sqlalchemy as sa
from alembic import op

revision = "20261118_0014"
down_revision = "20261114_0013"
branch_labels = None
depends_on = None

SCHEMA = "b3"
VALID_IO_TYPES = ("stdin", "file", "callable")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("questions", schema=SCHEMA)}

    if "type" in columns and "io_type" not in columns:
        # P0 rule: if an existing `type` column is present in DB, reuse it as `io_type`
        # instead of adding a second column.
        op.alter_column(
            "questions",
            "type",
            new_column_name="io_type",
            existing_type=sa.String(length=16),
            schema=SCHEMA,
        )
    elif "io_type" not in columns:
        op.add_column(
            "questions",
            sa.Column("io_type", sa.String(length=16), nullable=True),
            schema=SCHEMA,
        )

    before_count = bind.execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.questions")).scalar_one()

    # Backfill from the latest version's adapter_manifest['kind'] in Python so both
    # jsonb objects and double-encoded jsonb strings are handled uniformly.
    version_rows = bind.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (question_id)
                question_id::text AS qid_text,
                adapter_manifest
            FROM {SCHEMA}.question_versions
            ORDER BY question_id, version DESC
            """
        )
    ).fetchall()
    updated_count = 0
    for row in version_rows:
        manifest = row.adapter_manifest
        while isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except json.JSONDecodeError:
                manifest = None
                break
        kind = manifest.get("kind") if isinstance(manifest, dict) else None
        if kind in VALID_IO_TYPES:
            res = bind.execute(
                sa.text(
                    f"""
                    UPDATE {SCHEMA}.questions
                    SET io_type = :kind
                    WHERE question_id = CAST(:qid AS uuid)
                      AND (io_type IS NULL OR btrim(io_type) = '')
                    """
                ),
                {"kind": kind, "qid": row.qid_text},
            )
            updated_count += res.rowcount or 0

    after_count = bind.execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.questions")).scalar_one()
    if before_count != after_count:
        raise RuntimeError(
            f"b3.questions row count mismatch during io_type migration: "
            f"before={before_count}, after={after_count}"
        )

    bad_rows = bind.execute(
        sa.text(
            f"""
            SELECT question_id, title, io_type
            FROM {SCHEMA}.questions
            WHERE io_type IS NULL OR io_type NOT IN ('stdin', 'file', 'callable')
            ORDER BY created_at, question_id
            """
        )
    ).fetchall()
    if bad_rows:
        sample_manifest = (
            f"version_rows={len(version_rows)}, updated={updated_count}, "
            f"sample={repr(version_rows[0].adapter_manifest)[:120] if version_rows else 'empty'}"
        )
        summary = "; ".join(
            f"{row.question_id} ({row.title!r}): io_type={row.io_type!r}" for row in bad_rows[:20]
        )
        raise RuntimeError(
            f"Aborting migration: {len(bad_rows)} question(s) have missing or invalid io_type "
            f"({sample_manifest}): {summary}"
        )

    op.alter_column(
        "questions",
        "io_type",
        existing_type=sa.String(length=16),
        nullable=False,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_questions_io_type",
        "questions",
        "io_type IN ('stdin', 'file', 'callable')",
        schema=SCHEMA,
    )
    op.create_index("ix_questions_io_type", "questions", ["io_type"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_questions_io_type", table_name="questions", schema=SCHEMA)
    op.drop_constraint("ck_questions_io_type", "questions", schema=SCHEMA, type_="check")
    op.drop_column("questions", "io_type", schema=SCHEMA)
