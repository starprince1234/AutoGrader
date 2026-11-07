"""Store explicit execution declarations (Unix homework evaluation extension).

Revision ID: 20261107_0007
Revises: 20261107_0006
Create Date: 2026-11-07

Why this exists
---------------

The Unix homework rework (``MAIN/docs/architecture/unix-homework-evaluation.md`` §2/§8) makes
how a version's cases are produced an **explicit declaration** on the version instead of an
implicit per-case assumption:

* ``b3.question_versions.execution_manifest`` -- version-level mode plus its configuration
  (segment marker, email attempt/templates, SSH practice account). ``NULL`` means ``per_case``,
  which is what every version published before this migration means.
* ``b3.test_cases.execution`` -- per-case declaration
  (``segment``/``file_checks``/``ssh_check``/``email_check``). ``NULL`` means "declares
  nothing", the legacy shape.

Both columns are nullable and both are added (nothing existing is rewritten), so already
published versions keep their rows and their digests: neither column takes part in the
canonical content payload while it is NULL, which is exactly why ``content_digest`` and
``package_digest`` remain reproducible for existing content (§8's compatibility rule).

The immutability trigger is extended to cover ``execution_manifest``: it is content -- an SSH
password and the expected file digests live in it -- so a published version must refuse an
in-place change for the same reason it refuses a stem change. The function is recreated rather
than a new trigger added, because the guard is one rule with one column list.
"""

from __future__ import annotations

from alembic import op

revision = "20261107_0007"
down_revision = "20261107_0006"
branch_labels = None
depends_on = None

SCHEMA = "b3"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.question_versions ADD COLUMN execution_manifest jsonb")
    op.execute(f"ALTER TABLE {SCHEMA}.test_cases ADD COLUMN execution jsonb")
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.question_versions
            ADD CONSTRAINT ck_question_versions_execution_manifest_object
            CHECK (execution_manifest IS NULL OR jsonb_typeof(execution_manifest) = 'object')
        """
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.test_cases
            ADD CONSTRAINT ck_test_cases_execution_object
            CHECK (execution IS NULL OR jsonb_typeof(execution) = 'object')
        """
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.question_versions.execution_manifest IS "
        "'PROTECTED execution declaration (mode + SSH/email/segment configuration); "
        "NULL means the default per_case mode and is excluded from content_digest'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.test_cases.execution IS "
        "'PROTECTED per-case execution declaration (segment/file_checks/ssh_check/email_check); "
        "NULL means the case declares nothing'"
    )
    _replace_version_guard(include_execution_manifest=True)


def downgrade() -> None:
    # Restore the pre-extension guard *before* dropping the column it now names, otherwise the
    # function would reference a column that no longer exists for the window between the two
    # statements.
    _replace_version_guard(include_execution_manifest=False)
    op.execute(
        f"ALTER TABLE {SCHEMA}.test_cases DROP CONSTRAINT IF EXISTS ck_test_cases_execution_object"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.question_versions "
        "DROP CONSTRAINT IF EXISTS ck_question_versions_execution_manifest_object"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.test_cases DROP COLUMN IF EXISTS execution")
    op.execute(f"ALTER TABLE {SCHEMA}.question_versions DROP COLUMN IF EXISTS execution_manifest")


def _replace_version_guard(*, include_execution_manifest: bool) -> None:
    """Recreate ``b3.tg_question_versions_guard`` with (or without) the new column.

    The list of compared columns is the immutability contract itself; the extra clause is the
    only difference between the two versions of the function.
    """
    execution_clause = (
        "\n                   OR NEW.execution_manifest IS DISTINCT FROM OLD.execution_manifest"
        if include_execution_manifest
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.tg_question_versions_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION
                        'question version % is % and cannot be deleted', OLD.question_version_id, OLD.status
                        USING ERRCODE = '23001';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status = 'archived' THEN
                RAISE EXCEPTION
                    'archived question version % is immutable', OLD.question_version_id
                    USING ERRCODE = '23001';
            END IF;

            IF OLD.status = 'published' THEN
                IF NEW.status NOT IN ('published', 'archived') THEN
                    RAISE EXCEPTION
                        'invalid status transition % -> % on question version %',
                        OLD.status, NEW.status, OLD.question_version_id
                        USING ERRCODE = '23001';
                END IF;
                IF NEW.question_id IS DISTINCT FROM OLD.question_id
                   OR NEW.version IS DISTINCT FROM OLD.version
                   OR NEW.language IS DISTINCT FROM OLD.language
                   OR NEW.stem_markdown IS DISTINCT FROM OLD.stem_markdown
                   OR NEW.adapter_manifest IS DISTINCT FROM OLD.adapter_manifest
                   OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
                   OR NEW.package_digest IS DISTINCT FROM OLD.package_digest
                   OR NEW.max_files IS DISTINCT FROM OLD.max_files
                   OR NEW.max_total_bytes IS DISTINCT FROM OLD.max_total_bytes
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at{execution_clause} THEN
                    RAISE EXCEPTION
                        'published question version % is immutable; create a new version',
                        OLD.question_version_id
                        USING ERRCODE = '23001';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
