"""Reference-answer attestations, consumption counters and the keyset indexes.

Revision ID: 20260915_0003
Revises: 20260915_0002
Create Date: 2026-09-18

One migration, one concern: make two facts observable that previously existed only
outside the database.

1. ``b3.reference_attestations`` -- the record that a verifier ran a version's reference
   answer against its own test cases. B3 must not execute anything, so it cannot verify
   a question itself; what it can do is refuse to publish content no verifier has
   vouched for, bound to the digest B3 recomputes itself.
2. ``b3.consumption_counters`` -- per-outcome counters for the statistics consumer. A
   rejected event is never written to ``event_inbox``, so before this table "no attempts
   yet" and "every attempt was rejected" produced the same empty database.
3. Indexes that make the two new access paths bounded: the GIN index over tag lookup
   and the composite keyset index the windowed statistics query pages through.

Also adds ``revision_note`` / ``revision_diff`` to ``question_versions``: the revision
loop needs to record *why* a second version exists, and the delta is stored structurally
(counts, digests, presence flags) so it can never carry stem text, a reference answer or
an expected output.

Boundary notes: nothing outside ``b3`` is created or referenced; the new tables carry no
cross-schema foreign key. ``reference_attestations`` is **not** protected-class -- it holds
a digest and counts, never the content they are about -- but it is writable only by the
internal role, because recomputing the digest being attested to requires reading
protection-class content.
"""

from __future__ import annotations

from alembic import op

revision = "20260915_0003"
down_revision = "20260915_0002"
branch_labels = None
depends_on = None

SHA256 = r"^[0-9a-f]{64}$"
TAG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"
MAX_TAGS = 20

TABLES = ("reference_attestations", "consumption_counters")

#: Index created here, in one place, so ``upgrade``/``downgrade`` cannot drift.
INDEXES = (
    "CREATE INDEX ix_questions_tags ON b3.questions USING gin (tags)",
    "CREATE INDEX ix_event_inbox_question_version_id_occurred_at "
    "ON b3.event_inbox (question_version_id, occurred_at, event_id)",
)


def upgrade() -> None:
    _create_reference_attestations()
    _create_consumption_counters()
    _add_revision_columns()
    _create_tag_constraints()
    for statement in INDEXES:
        op.execute(statement)
    _grant_privileges()


def downgrade() -> None:
    for name in (
        "ck_questions_tags_normalised",
        "ck_questions_tags_count",
        "ck_questions_tags_is_array",
    ):
        op.execute(f"ALTER TABLE b3.questions DROP CONSTRAINT IF EXISTS {name}")
    op.execute("DROP INDEX IF EXISTS b3.ix_event_inbox_question_version_id_occurred_at")
    op.execute("DROP INDEX IF EXISTS b3.ix_questions_tags")
    op.execute("ALTER TABLE b3.question_versions DROP COLUMN IF EXISTS revision_diff")
    op.execute("ALTER TABLE b3.question_versions DROP COLUMN IF EXISTS revision_note")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS b3.{table}")


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


def _create_reference_attestations() -> None:
    op.execute(
        f"""
        CREATE TABLE b3.reference_attestations (
            question_version_id uuid NOT NULL,
            content_digest varchar(64) NOT NULL,
            attestation_id uuid NOT NULL,
            verifier varchar(200) NOT NULL,
            cases_total integer NOT NULL,
            cases_passed integer NOT NULL,
            verified_at timestamptz(3) NOT NULL,
            notes text,
            recorded_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_reference_attestations PRIMARY KEY (question_version_id, content_digest),
            CONSTRAINT fk_reference_attestations_question_version_id
                FOREIGN KEY (question_version_id)
                REFERENCES b3.question_versions (question_version_id) ON DELETE CASCADE,
            CONSTRAINT ck_reference_attestations_content_digest
                CHECK (content_digest ~ '{SHA256}'),
            CONSTRAINT ck_reference_attestations_verifier_length
                CHECK (length(verifier) BETWEEN 1 AND 200),
            CONSTRAINT ck_reference_attestations_cases_total
                CHECK (cases_total BETWEEN 1 AND 1000),
            CONSTRAINT ck_reference_attestations_cases_passed
                CHECK (cases_passed BETWEEN 0 AND cases_total)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_reference_attestations_attestation_id "
        "ON b3.reference_attestations (attestation_id)"
    )
    op.execute(
        "CREATE INDEX ix_reference_attestations_question_version_id "
        "ON b3.reference_attestations (question_version_id)"
    )
    op.execute(
        "COMMENT ON TABLE b3.reference_attestations IS "
        "'A verifier ran the reference answer against the cases; digest-bound to the "
        "content B3 hashes itself. Counts and digest only -- no answer, no expectation'"
    )


def _create_consumption_counters() -> None:
    op.execute(
        """
        CREATE TABLE b3.consumption_counters (
            outcome varchar(32),
            CONSTRAINT pk_consumption_counters PRIMARY KEY (outcome),
            delivery_count bigint NOT NULL DEFAULT 0,
            last_occurred_at timestamptz(3),
            CONSTRAINT ck_consumption_counters_outcome
                CHECK (outcome IN ('accepted', 'duplicate_event', 'duplicate_run', 'rejected')),
            CONSTRAINT ck_consumption_counters_delivery_count
                CHECK (delivery_count >= 0)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE b3.consumption_counters IS "
        "'Per-delivery outcome counters; distinguishes ''nothing to consume'' from "
        "''events are being rejected''. Counters and timestamps only, no payload'"
    )


def _add_revision_columns() -> None:
    op.execute("ALTER TABLE b3.question_versions ADD COLUMN revision_note varchar(500)")
    op.execute("ALTER TABLE b3.question_versions ADD COLUMN revision_diff jsonb")
    op.execute(
        """
        ALTER TABLE b3.question_versions
            ADD CONSTRAINT ck_question_versions_revision_note_length
            CHECK (revision_note IS NULL OR length(revision_note) BETWEEN 1 AND 500)
        """
    )
    op.execute(
        """
        ALTER TABLE b3.question_versions
            ADD CONSTRAINT ck_question_versions_revision_diff_object
            CHECK (revision_diff IS NULL OR jsonb_typeof(revision_diff) = 'object')
        """
    )

def _create_tag_constraints() -> None:
    """Make the normalised tag form a storage guarantee, not a caller convention.

    ``tags`` already existed as a ``jsonb`` column that nothing validated, so any
    writer could store an arbitrarily shaped value. The constraints mirror the
    contract pattern exactly; the domain validator rejects an invalid tag with ``422``
    before it ever reaches here, so a violation of these is a defect, not user input.
    """
    op.execute(
        """
        ALTER TABLE b3.questions
            ADD CONSTRAINT ck_questions_tags_is_array
            CHECK (jsonb_typeof(tags) = 'array')
        """
    )
    op.execute(
        f"""
        ALTER TABLE b3.questions
            ADD CONSTRAINT ck_questions_tags_normalised
            CHECK (NOT jsonb_path_exists(
                tags,
                '$[*] ? (@.type() != "string" || !(@ like_regex "{TAG_PATTERN}"))'
            ))
        """
    )
    op.execute(
        f"""
        ALTER TABLE b3.questions
            ADD CONSTRAINT ck_questions_tags_count
            CHECK (jsonb_array_length(tags) <= {MAX_TAGS})
        """
    )


# --------------------------------------------------------------------------
# privileges
# --------------------------------------------------------------------------


def _grant_privileges() -> None:
    """Extend the two roles' privileges to the objects this revision adds.

    Same shape as the previous migration: roles come from ``infra/postgres/init.sql``
    on the module-local database and from B4 on the platform one, so a missing role is
    skipped rather than failing the migration.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_app') THEN
                GRANT SELECT ON b3.reference_attestations TO autograder_b3_app;
                GRANT SELECT, INSERT, UPDATE, DELETE ON b3.consumption_counters
                    TO autograder_b3_app;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'autograder_b3_internal') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON b3.reference_attestations
                    TO autograder_b3_internal;
                GRANT SELECT, INSERT, UPDATE, DELETE ON b3.consumption_counters
                    TO autograder_b3_internal;
            END IF;
        END
        $$
        """
    )
