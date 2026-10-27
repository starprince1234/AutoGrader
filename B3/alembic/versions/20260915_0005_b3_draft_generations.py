"""AI-FLOW-01A: private draft-generation records and the paid-call ledger.

Revision ID: 20260915_0005
Revises: 20260915_0004
Create Date: 2026-10-24

Two tables, because the two things they hold have different lifetimes and different
audiences:

* ``b3.question_draft_generations`` keeps one **unaccepted AI candidate**: an editable
  proposal that may contain a reference answer and proposed hidden cases. It is
  **PROTECTED** -- the ordinary question-bank role is explicitly revoked, exactly as for
  ``reference_answers``/``test_cases`` -- because a candidate answer sitting in a readable
  table would be a second, unreviewed copy of the most sensitive content in the module.
  ``proposal`` is set to NULL when a candidate is accepted or discarded; the row survives so
  the ledger can still say "this generation happened and became that version", which is what
  keeps provenance auditable after the content is gone.
* ``b3.ai_generation_usage`` is the de-identified ledger of **paid calls**: actor, course,
  provider/model/prompt version, request time, duration, token counts, status and the
  accepted version id. It has no column for a prompt, an objective, a response body or a
  candidate, so "the raw prompt is never stored" is a fact about the schema rather than a
  rule in application code.

Nothing here creates questions, versions or cases; a generation is not a question. The
immutability rules of ``question_versions`` are untouched, and no data is migrated or
rewritten: this revision only adds two tables (and their indexes).

Boundary notes: ``b3`` is the only schema referenced; the timestamp columns are
``timestamptz(3)`` and every constraint/index is named with the platform prefixes.
"""

from __future__ import annotations

from alembic import op

revision = "20260915_0005"
down_revision = "20260915_0004"
branch_labels = None
depends_on = None

SCHEMA = "b3"

PROTECTED_TABLE = "question_draft_generations"
LEDGER_TABLE = "ai_generation_usage"


def upgrade() -> None:
    _create_generations()
    _create_usage_ledger()
    _comment_tables()
    _grant_privileges()


def downgrade() -> None:
    # Reverse order of creation. Both tables hold only generation records, never question
    # content, so dropping them cannot invalidate a published version, an attestation or an
    # assignment reference.
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{LEDGER_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{PROTECTED_TABLE}")


def _create_generations() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.{PROTECTED_TABLE} (
            generation_id uuid,
            CONSTRAINT pk_{PROTECTED_TABLE} PRIMARY KEY (generation_id),
            course_id uuid NOT NULL,
            owner_subject varchar(200) NOT NULL,
            status varchar(16) NOT NULL,
            language varchar(16) NOT NULL,
            adapter_kind varchar(16) NOT NULL,
            provider varchar(100) NOT NULL,
            model varchar(100) NOT NULL,
            prompt_version varchar(100) NOT NULL,
            proposal jsonb,
            warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz(3) NOT NULL,
            expires_at timestamptz(3) NOT NULL,
            accepted_question_version_id uuid,
            accepted_at timestamptz(3),
            discarded_at timestamptz(3),
            CONSTRAINT fk_{PROTECTED_TABLE}_accepted_question_version_id
                FOREIGN KEY (accepted_question_version_id)
                REFERENCES {SCHEMA}.question_versions (question_version_id)
                ON DELETE SET NULL,
            CONSTRAINT ck_{PROTECTED_TABLE}_status
                CHECK (status IN ('ready', 'needs_review', 'failed')),
            CONSTRAINT ck_{PROTECTED_TABLE}_language
                CHECK (language IN ('python', 'bash')),
            CONSTRAINT ck_{PROTECTED_TABLE}_adapter_kind
                CHECK (adapter_kind IN ('stdin', 'file', 'callable')),
            CONSTRAINT ck_{PROTECTED_TABLE}_owner_subject
                CHECK (length(owner_subject) BETWEEN 1 AND 200),
            CONSTRAINT ck_{PROTECTED_TABLE}_warnings_array
                CHECK (jsonb_typeof(warnings) = 'array'),
            CONSTRAINT ck_{PROTECTED_TABLE}_proposal_object
                CHECK (proposal IS NULL OR jsonb_typeof(proposal) = 'object'),
            CONSTRAINT ck_{PROTECTED_TABLE}_discarded_has_no_proposal
                CHECK (discarded_at IS NULL OR proposal IS NULL),
            CONSTRAINT ck_{PROTECTED_TABLE}_accepted_has_no_proposal
                CHECK (
                    accepted_at IS NULL
                    OR (proposal IS NULL AND accepted_question_version_id IS NOT NULL)
                ),
            CONSTRAINT ck_{PROTECTED_TABLE}_expiry
                CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_{PROTECTED_TABLE}_owner_subject "
        f"ON {SCHEMA}.{PROTECTED_TABLE} (owner_subject, created_at)"
    )
    op.execute(
        f"CREATE INDEX ix_{PROTECTED_TABLE}_course_id "
        f"ON {SCHEMA}.{PROTECTED_TABLE} (course_id)"
    )
    op.execute(
        f"CREATE INDEX ix_{PROTECTED_TABLE}_expires_at "
        f"ON {SCHEMA}.{PROTECTED_TABLE} (expires_at)"
    )


def _create_usage_ledger() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.{LEDGER_TABLE} (
            usage_id uuid,
            CONSTRAINT pk_{LEDGER_TABLE} PRIMARY KEY (usage_id),
            generation_id uuid,
            course_id uuid NOT NULL,
            owner_subject varchar(200) NOT NULL,
            provider varchar(100) NOT NULL,
            model varchar(100) NOT NULL,
            prompt_version varchar(100) NOT NULL,
            requested_at timestamptz(3) NOT NULL,
            duration_ms integer NOT NULL,
            status varchar(16) NOT NULL,
            error_category varchar(64),
            prompt_tokens integer,
            completion_tokens integer,
            total_tokens integer,
            accepted_question_version_id uuid,
            CONSTRAINT ck_{LEDGER_TABLE}_status
                CHECK (status IN ('ready', 'needs_review', 'failed')),
            CONSTRAINT ck_{LEDGER_TABLE}_owner_subject
                CHECK (length(owner_subject) BETWEEN 1 AND 200),
            CONSTRAINT ck_{LEDGER_TABLE}_duration_ms CHECK (duration_ms >= 0),
            CONSTRAINT ck_{LEDGER_TABLE}_prompt_tokens
                CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
            CONSTRAINT ck_{LEDGER_TABLE}_completion_tokens
                CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
            CONSTRAINT ck_{LEDGER_TABLE}_total_tokens
                CHECK (total_tokens IS NULL OR total_tokens >= 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_{LEDGER_TABLE}_owner_subject "
        f"ON {SCHEMA}.{LEDGER_TABLE} (owner_subject, requested_at)"
    )
    op.execute(
        f"CREATE INDEX ix_{LEDGER_TABLE}_generation_id "
        f"ON {SCHEMA}.{LEDGER_TABLE} (generation_id)"
    )


def _comment_tables() -> None:
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{PROTECTED_TABLE} IS "
        "'PROTECTED: unaccepted AI candidate (may contain a reference answer and proposed "
        "hidden cases); internal role only, never a public DTO'"
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{LEDGER_TABLE} IS "
        "'De-identified paid-call ledger: identifiers, counts and status only; no prompt, "
        "no response body, no candidate'"
    )


def _grant_privileges() -> None:
    """Least privilege for the two application roles, when they exist.

    ``0002`` installed ``ALTER DEFAULT PRIVILEGES ... GRANT SELECT`` for the app role, so a
    newly created table would otherwise be readable by it without anyone deciding that. The
    explicit ``REVOKE`` below is therefore load-bearing, not cosmetic: it is what keeps an
    unaccepted candidate out of the least-privilege role.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
                REVOKE ALL ON {SCHEMA}.{PROTECTED_TABLE} FROM autograder_b3_app;
                GRANT SELECT ON {SCHEMA}.{LEDGER_TABLE} TO autograder_b3_app;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_internal') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    {SCHEMA}.{PROTECTED_TABLE},
                    {SCHEMA}.{LEDGER_TABLE}
                    TO autograder_b3_internal;
            END IF;
        END
        $$
        """
    )
