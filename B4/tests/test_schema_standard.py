"""RQ-B-4-30: the schema standard is enforced by a checker, and the models match the DDL.

A rule that cannot detect a violation is documentation, not a standard, so the checker is
exercised against deliberately broken inputs as well as against the real schema -- both the
live `b4` catalog and the DDL text of every module's migrations. The second half of the file
closes the other gap: the SQLAlchemy table definitions the application queries must agree
with what the migrations actually created.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import Engine, inspect, text

from autograder_b4.db.tables import ALL_TABLES, metadata
from autograder_b4.ops.table_standards import (
    NATURAL_KEYS,
    NON_PLURAL_TABLE_NAMES,
    check_migrations,
    check_table_standards,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: Every message an archetype decision can produce. For a table whose archetype is declared
#: *and* satisfied, none of these may appear -- that is what "the rule stopped misjudging a
#: correct design" means, as opposed to "the rule was switched off".
ARCHETYPE_MESSAGES = (
    "does not end in '_id'",
    "expected uuid",
    "requires a single '<entity>_id' column",
    "table name is not plural",
    "is not digest-shaped",
    "has no 64-character digest check",
    "is not constrained to a value set",
    "the archetype requires a bounded label",
    "declared a composite natural key but",
    "the declaration and the schema must agree",
)

#: A migration that breaks one rule per statement, in raw SQL (B3/B4's style). Each comment
#: names the rule it is meant to trip, so a failure says which rule stopped working.
NON_COMPLIANT_SQL = '''
SCHEMA = "b2"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.roster_snapshot (            -- singular table name
            snapshot_id uuid PRIMARY KEY,
            created_at timestamp,                          -- timestamp without time zone
            updated_at timestamptz,                        -- no millisecond precision
            course_id uuid NOT NULL,
            marks integer,
            UNIQUE (course_id),                            -- unnamed unique constraint
            FOREIGN KEY (course_id) REFERENCES b3.courses (course_id)  -- unnamed + cross schema
        )
        """
    )
    op.execute(
        f"CREATE INDEX idx_roster_snapshot_course ON {SCHEMA}.roster_snapshot (course_id)"
    )
    op.execute(
        f"CREATE VIEW {SCHEMA}.roster_view AS SELECT * FROM b3.questions"
    )
    op.execute("CREATE TABLE b3.imported_snapshots (imported_snapshot_id uuid PRIMARY KEY)")
    op.execute(
        f"CREATE TABLE {SCHEMA}.legacy_keys (legacy_key varchar(32) PRIMARY KEY)"
    )
'''

#: The same idea in the SQLAlchemy-expression style (B2's), which the scanner has to
#: understand as DDL even though no `CREATE TABLE` text appears in the file.
NON_COMPLIANT_API = '''
import sqlalchemy as sa

SCHEMA = "b3"


def upgrade() -> None:
    op.create_table(
        "question_stats",
        sa.Column("stat_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("question_version_id", "bucket"),
        sa.UniqueConstraint("course_id", name="unique_question_stats_course"),
        sa.ForeignKeyConstraint(
            ["course_id"], [f"{SCHEMA}.courses.course_id"], name="question_stats_course_fkey"
        ),
        sa.CheckConstraint("bucket >= 0", name="check_bucket"),
        schema=SCHEMA,
    )
    op.create_index("index_question_stats_course", "question_stats", ["course_id"], schema=SCHEMA)
    op.create_index("ix_question_stats_created_at", "question_stats", ["created_at"], schema=SCHEMA)
'''


def _write_probe_module(root: Path, module: str, schema: str, source: str) -> None:
    """A synthetic B<n> module, so the linter is tested against DDL it must reject."""
    versions = root / module / "alembic" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (root / module / "module.yaml").write_text(
        "schema_version: 1\n"
        f"module: {module}\n"
        "contract_version: 0.1.0\n"
        "database: {schema: " + schema + ", migration_head: '20260101_0001'}\n",
        encoding="utf-8",
    )
    (versions / "20260101_0001_probe.py").write_text(source, encoding="utf-8")


#: The three archetypes, written correctly, for the modules that own those tables.
#: `docs/database/table-standards.md` §2.1-§2.3 allow them; the checker must not report them.
DECLARED_ARCHETYPES_SQL = '''
def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE b3.blobs (
            content_sha256 varchar(64) NOT NULL,
            content bytea NOT NULL,
            size_bytes integer NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_blobs PRIMARY KEY (content_sha256),
            CONSTRAINT ck_blobs_content_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.consumption_counters (
            outcome varchar(32) NOT NULL,
            delivery_count bigint NOT NULL DEFAULT 0,
            CONSTRAINT pk_consumption_counters PRIMARY KEY (outcome),
            CONSTRAINT ck_consumption_counters_outcome
                CHECK (outcome IN ('accepted', 'rejected'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.question_version_score_buckets (
            question_version_id uuid NOT NULL,
            bucket_label varchar(32) NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_question_version_score_buckets
                PRIMARY KEY (question_version_id, bucket_label)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.event_inbox (
            event_id uuid NOT NULL,
            occurred_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_event_inbox PRIMARY KEY (event_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.event_outbox (
            outbox_id uuid NOT NULL,
            created_at timestamptz(3) NOT NULL,
            CONSTRAINT pk_event_outbox PRIMARY KEY (outbox_id)
        )
        """
    )
'''

#: The same archetypes in the SQLAlchemy style, for B2's tables.
DECLARED_ARCHETYPES_API = '''
import sqlalchemy as sa

SCHEMA = "b2"


def upgrade() -> None:
    op.create_table(
        "submission_files",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("submission_id", "path", name="pk_submission_files"),
        schema=SCHEMA,
    )
    op.create_table(
        "inbox_events",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("consumer", "event_id", name="pk_inbox_events"),
        schema=SCHEMA,
    )
    op.create_table(
        "case_evidence",
        sa.Column("case_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("case_evidence_id", name="pk_case_evidence"),
        schema=SCHEMA,
    )
'''

#: Lookalikes: every shape an archetype has, but **not declared**. They must stay reported --
#: an exemption obtainable by naming a column a certain way is not a standard.
UNDECLARED_LOOKALIKES = '''
def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE b2.payloads (
            digest varchar(64) NOT NULL,
            body bytea NOT NULL,
            CONSTRAINT pk_payloads PRIMARY KEY (digest),
            CONSTRAINT ck_payloads_digest CHECK (digest ~ '^[0-9a-f]{64}$')
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.snapshot_pairs (
            left_id uuid NOT NULL,
            right_id uuid NOT NULL,
            CONSTRAINT pk_snapshot_pairs PRIMARY KEY (left_id, right_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.event_inbox_backup (
            backup_id uuid NOT NULL,
            CONSTRAINT pk_event_inbox_backup PRIMARY KEY (backup_id)
        )
        """
    )
'''

#: Declared, but not actually the pattern: the declaration must not launder the table.
BROKEN_ARCHETYPE = '''
def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE b3.blobs (
            content_sha256 varchar(30) NOT NULL,
            content bytea NOT NULL,
            CONSTRAINT pk_blobs PRIMARY KEY (content_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.consumption_counters (
            outcome varchar(200) NOT NULL,
            delivery_count bigint NOT NULL DEFAULT 0,
            CONSTRAINT pk_consumption_counters PRIMARY KEY (outcome)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE b3.question_version_score_buckets (
            question_version_id uuid NOT NULL,
            CONSTRAINT pk_question_version_score_buckets PRIMARY KEY (question_version_id)
        )
        """
    )
'''

#: The same schema must get the same verdict in both scopes, in every spelling PostgreSQL
#: accepts -- including the SQLAlchemy spelling of `timestamptz(3)`, which is
#: `precision=3` (`postgresql.TIMESTAMP(timezone=True, precision=3)`). A rule that cannot be
#: satisfied in one of the two styles a migration may use is a bug in the checker.
TIME_COLUMNS_SQL = '''
import sqlalchemy as sa

SCHEMA = "b2"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE b2.time_probe (
            time_probe_id uuid NOT NULL,
            created_at timestamptz(3) NOT NULL,
            occurred_at timestamptz NOT NULL,
            published_at timestamp(3) with time zone,
            archived_at timestamp without time zone,
            CONSTRAINT pk_time_probe PRIMARY KEY (time_probe_id)
        )
        """
    )
    op.create_table(
        "api_time_probe",
        sa.Column("api_time_probe_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("api_time_probe_id", name="pk_api_time_probe"),
        schema=SCHEMA,
    )
'''

#: A corrective revision: nothing but `ALTER`s, which this scope counts but does not model.
ALTER_ONLY_REVISION = '''
def upgrade() -> None:
    op.execute("ALTER TABLE b3.questions ALTER COLUMN created_at TYPE timestamptz(3)")
    op.execute("ALTER TABLE b3.questions RENAME CONSTRAINT questions_pkey TO pk_questions")
    op.execute("DROP INDEX IF EXISTS idx_questions_status")
'''

#: A `DO $$ ... $$` block: the body is masked (it is not a declaration this scope reads), so
#: the statement is counted as unmodelled rather than checked.
DO_BLOCK_REVISION = '''
def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'pk_blobs') THEN
                ALTER TABLE b3.blobs ADD CONSTRAINT pk_blobs PRIMARY KEY (content_sha256);
            END IF;
        END
        $$;
        """
    )
'''


def test_live_schema_conforms_to_the_standard(engine: Engine) -> None:
    violations = check_table_standards(engine)
    assert violations == [], "\n".join(violations)


def test_checker_detects_a_bad_table_name(engine: Engine) -> None:
    """A checker that cannot fail proves nothing, so it is fed a violation.

    The name is quoted so PostgreSQL keeps the mixed case; unquoted, the server would fold
    it to `badname` and the violation would never exist to be found.
    """
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE b4."BadName" (id integer)'))
    try:
        violations = check_table_standards(engine)
        assert any(
            "not lower snake_case" in violation.lower() for violation in violations
        ), violations
    finally:
        with engine.begin() as connection:
            connection.execute(text('DROP TABLE b4."BadName"'))


def test_checker_detects_a_missing_primary_key(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE b4.probe_no_pk (value integer)"))
    try:
        violations = check_table_standards(engine)
        assert any("has no primary key" in violation for violation in violations), violations
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE b4.probe_no_pk"))


def test_checker_detects_a_timestamp_without_timezone(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE b4.probe_naive_time ("
                "  probe_naive_time_id uuid PRIMARY KEY,"
                "  created_at timestamp"
                ")"
            )
        )
    try:
        violations = check_table_standards(engine)
        assert any("requires timestamptz" in violation for violation in violations), violations
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE b4.probe_naive_time"))


def test_checker_detects_a_misnamed_index(engine: Engine) -> None:
    """The index lives on a probe table the suite creates, not on `b4.users`.

    Creating an index requires ownership of the table, and the application role owns
    nothing on purpose (`RQ-B-4-10`): every table belongs to the migration role. Pointing
    this at `b4.users` therefore failed with `must be owner of table users` while proving
    nothing about the checker -- the rule under test is "an index whose name is not
    `ix_<table>_<cols>` is reported", and that is independent of which table carries it.
    """
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE b4.probe_indexed (value integer PRIMARY KEY)"))
        connection.execute(text("CREATE INDEX wrong_name ON b4.probe_indexed (value)"))
    try:
        violations = check_table_standards(engine)
        assert any("wrong_name" in violation for violation in violations), violations
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE b4.probe_indexed"))


# ------------------------------------------------------- migration DDL (all modules)


def test_declared_archetypes_are_not_reported(workspace_tmp: Path) -> None:
    """A declared archetype is a legitimate design, and the checker has to say so.

    `docs/database/table-standards.md` §2.1-§2.3 allow the content-addressed, composite
    natural and enumerated keys; the rule used to report all of them (MAIN's E16). This is
    the "must not report" direction, for both DDL spellings.
    """
    _write_probe_module(workspace_tmp, "B3", "b3", DECLARED_ARCHETYPES_SQL)
    _write_probe_module(workspace_tmp, "B2", "b2", DECLARED_ARCHETYPES_API)
    report = check_migrations(workspace_tmp, modules=("B2", "B3"))
    reported = "\n".join(report.violations)
    for message in ARCHETYPE_MESSAGES:
        assert message not in reported, f"{message!r} was reported:\n{reported}"
    # The declared tables are still held to every *other* rule in the same file (the two
    # `created_at timestamptz` columns and the DateTime(timezone=True) one), so this is not
    # "the file was skipped".
    assert "without millisecond precision" in reported
    assert report.statements >= 8, report


def test_undeclared_lookalikes_are_still_reported(workspace_tmp: Path) -> None:
    """The other direction: shapes alone earn nothing.

    A digest primary key, a composite primary key and a name that resembles a declared one
    are all reported until the table is declared, so nothing is exempted implicitly.
    """
    _write_probe_module(workspace_tmp, "B2", "b2", UNDECLARED_LOOKALIKES)
    _write_probe_module(workspace_tmp, "B3", "b3", UNDECLARED_LOOKALIKES)
    report = check_migrations(workspace_tmp, modules=("B2", "B3"))
    reported = "\n".join(report.violations)
    assert "payloads: primary key column 'digest' does not end in '_id'" in reported, reported
    assert "payloads: primary key column 'digest' is varchar(64), expected uuid" in reported
    assert "snapshot_pairs: primary key is ['left_id', 'right_id']" in reported, reported
    # `event_inbox_backup` resembles a declared name; the declaration matches exactly.
    assert "event_inbox_backup: table name is not plural" in reported, reported


def test_a_declaration_cannot_launder_a_table(workspace_tmp: Path) -> None:
    """Declared but not actually the pattern: the archetype's preconditions are checked.

    Otherwise "declare it in NATURAL_KEYS" would be a way to switch the rule off, which is
    the opposite of what the declarations are for.
    """
    _write_probe_module(workspace_tmp, "B3", "b3", BROKEN_ARCHETYPE)
    report = check_migrations(workspace_tmp, modules=("B3",))
    reported = "\n".join(report.violations)
    assert "blobs: content-addressed primary key 'content_sha256'" in reported, reported
    assert "has no 64-character digest check" in reported, reported
    assert "consumption_counters: enumerated primary key 'outcome'" in reported, reported
    assert "the archetype requires a bounded label" in reported, reported
    assert "is not constrained to a value set" in reported, reported
    assert (
        "question_version_score_buckets: primary key is ['question_version_id'], but "
        "NATURAL_KEYS declares" in reported
    ), reported


def test_every_archetype_declaration_is_keyed_by_module_and_satisfied() -> None:
    for (module, table), natural in NATURAL_KEYS.items():
        assert module in {"B1", "B2", "B3", "B4", "B5"}, (module, table)
        assert natural.columns, (module, table)
        assert natural.reason, (module, table)
        assert natural.archetype in {
            "content_addressed",
            "composite_natural_key",
            "enumerated_natural_key",
        }, natural
        if natural.archetype == "composite_natural_key":
            assert len(natural.columns) >= 2, (module, table)
        else:
            assert len(natural.columns) == 1, (module, table)
    for (module, table), reason in NON_PLURAL_TABLE_NAMES.items():
        assert module in {"B1", "B2", "B3", "B4", "B5"}, (module, table)
        assert not table.endswith("s"), (module, table)
        assert reason, (module, table)


def test_migration_linter_detects_non_compliant_sql(workspace_tmp: Path) -> None:
    """The static mode must fail on DDL that breaks the standard, or it proves nothing."""
    _write_probe_module(workspace_tmp, "B2", "b2", NON_COMPLIANT_SQL)
    report = check_migrations(workspace_tmp, modules=("B2",))
    reported = "\n".join(report.violations)
    for fragment in (
        "table name is not plural",  # roster_snapshot
        "requires timestamptz(3)",  # created_at is a bare timestamp
        "without millisecond precision",  # updated_at is timestamptz, not timestamptz(3)
        "unique constraint is unnamed",
        "foreign key is unnamed",
        "cross-schema foreign keys are forbidden",  # REFERENCES b3.courses
        "does not start with 'ix_roster_snapshot_'",  # idx_roster_snapshot_course
        "cross-schema views are forbidden",  # the view reads b3.questions
        "targets schema 'b3'",  # a table created in another module's schema
        "primary key column 'legacy_key' does not end in '_id'",
        "is varchar(32), expected uuid",
    ):
        assert fragment in reported, f"{fragment!r} was not reported:\n{reported}"


def test_migration_linter_detects_non_compliant_sqlalchemy_ddl(workspace_tmp: Path) -> None:
    """B2 writes DDL through `op.create_table`, so the scanner must read that spelling."""
    _write_probe_module(workspace_tmp, "B3", "b3", NON_COMPLIANT_API)
    report = check_migrations(workspace_tmp, modules=("B3",))
    reported = "\n".join(report.violations)
    for fragment in (
        "primary key is ['question_version_id', 'bucket']",
        "does not start with 'uq_question_stats_'",  # unique_question_stats_course
        "does not start with 'fk_question_stats_'",  # question_stats_course_fkey
        "does not start with 'ck_question_stats_'",  # check_bucket
        "does not start with 'ix_question_stats_'",  # index_question_stats_course
        "without millisecond precision",  # sa.DateTime(timezone=True)
    ):
        assert fragment in reported, f"{fragment!r} was not reported:\n{reported}"
    # The compliant index in the same file must not be reported: a scanner that flags
    # everything is as useless as one that flags nothing.
    assert "ix_question_stats_created_at" not in reported


def test_migration_linter_reports_a_module_it_cannot_read(workspace_tmp: Path) -> None:
    """A module whose DDL the scanner does not recognise is a finding, not a pass."""
    _write_probe_module(workspace_tmp, "B2", "b2", "def upgrade() -> None:\n    pass\n")
    report = check_migrations(workspace_tmp, modules=("B2",))
    assert any("no DDL was recognised" in violation for violation in report.violations), report


def test_migration_linter_covers_the_repository_and_b4_conforms() -> None:
    """Run against the real migrations, and pin how much of them the scanner matched.

    The floor on `statements` is what stops a broken pattern from turning the linter into a
    silent pass; B4's own migrations must come out clean, since the catalog mode already
    proves the same schema conforms.

    The remaining violations in B2/B3 are *their* deviations and this test does not require
    them to be zero (violation count is not a completion criterion). What it does require is
    that the declared archetypes in those same files are no longer reported -- that is the
    half of the standard B4 owns.
    """
    report = check_migrations(REPOSITORY_ROOT)
    assert report.files >= 16, report
    assert report.statements >= 130, report
    own = check_migrations(REPOSITORY_ROOT, modules=("B4",))
    assert own.violations == (), "\n".join(own.violations)
    assert own.statements >= 30, own

    declared = {table for _module, table in NATURAL_KEYS} | {
        table for _module, table in NON_PLURAL_TABLE_NAMES
    }
    for violation in report.violations:
        if not any(f": {table}:" in violation for table in declared):
            continue
        for message in ARCHETYPE_MESSAGES:
            assert message not in violation, violation


def test_application_metadata_matches_the_migrated_database(engine: Engine) -> None:
    """The queries the application builds must match the schema the migration created."""
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names(schema="b4"))
    declared_tables = {table.name for table in ALL_TABLES}
    assert declared_tables <= live_tables, declared_tables - live_tables

    for table in ALL_TABLES:
        live_columns = {
            column["name"] for column in inspector.get_columns(table.name, schema="b4")
        }
        declared_columns = {column.name for column in table.columns}
        assert declared_columns == live_columns, (
            f"{table.name}: declared={sorted(declared_columns)} live={sorted(live_columns)}"
        )
        live_pk = inspector.get_pk_constraint(table.name, schema="b4")
        assert list(live_pk["constrained_columns"]) == [
            column.name for column in table.primary_key.columns
        ], table.name

    assert metadata.schema == "b4"


def test_every_foreign_key_stays_inside_b4(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in ALL_TABLES:
        for foreign_key in inspector.get_foreign_keys(table.name, schema="b4"):
            assert foreign_key.get("referred_schema") in (None, "b4"), (
                f"{table.name}.{foreign_key['name']} leaves the b4 schema"
            )


def test_uuid_primary_keys_are_generated_by_the_application(engine: Engine) -> None:
    """UUID v4 keys are produced by the owning module, so no column has a default."""
    inspector = inspect(engine)
    for table in ALL_TABLES:
        for column in inspector.get_columns(table.name, schema="b4"):
            if column["name"] in {item.name for item in table.primary_key.columns}:
                assert column.get("default") is None, (
                    f"{table.name}.{column['name']} has a database-side default; the standard "
                    "requires the owning module to generate the identifier"
                )


def test_enrollment_uniqueness_is_enforced_by_the_database(engine: Engine, factory) -> None:
    """Business uniqueness is a constraint, not a convention (table-standards.md §4)."""
    from sqlalchemy.exc import IntegrityError

    course_id = factory.course(code="CS101")
    section_id = factory.section(course_id=course_id, code="01")
    factory.enrollment(section_id=section_id, student_number="20230001")
    try:
        factory.enrollment(section_id=section_id, student_number="20230001")
    except IntegrityError:
        return
    raise AssertionError("a duplicate (section_id, student_number) was accepted")


def test_roles_reject_an_unknown_role_value(engine: Engine, factory) -> None:
    from sqlalchemy.exc import IntegrityError

    user_id = factory.user(username="probe", roles=())
    course_id = factory.course(code="CS102")
    try:
        factory.grant(user_id=user_id, course_id=course_id, role="superuser")
    except IntegrityError:
        return
    raise AssertionError("the roles table accepted a role outside the contract enum")


def test_audit_records_reject_an_unknown_outcome(engine: Engine) -> None:
    from sqlalchemy.exc import IntegrityError

    from autograder_b4.db.tables import audit_records

    try:
        with engine.begin() as connection:
            connection.execute(
                audit_records.insert().values(
                    audit_id=uuid.uuid4(),
                    action="probe",
                    outcome="whatever",
                )
            )
    except IntegrityError:
        return
    raise AssertionError("an audit outcome outside {success, denied, failure} was accepted")
