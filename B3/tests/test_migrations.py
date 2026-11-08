"""Migration and privilege gates.

These are the checks the module's own definition of done lists: the migration runs
on an empty database (the session fixture does it on every run), the Alembic head
matches ``module.yaml``, the ``b3`` schema is the only thing touched, protected
tables are unreadable by the ordinary role, and no legacy object exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from autograder_b3.infra.models import PROTECTED_TABLES

from .conftest import APP_URL, INTERNAL_URL, OWNER_URL

B3_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TABLES = {
    "questions",
    "question_versions",
    "question_assets",
    "reference_answers",
    "test_suites",
    "test_cases",
    "blobs",
    "fixtures",
    "reference_attestations",
    "assignments",
    "assignment_items",
    "event_inbox",
    "event_outbox",
    "consumption_counters",
    "question_version_stats",
    "question_version_score_buckets",
    "question_version_failure_categories",
}

LEGACY_OBJECTS = frozenset(
    {
        "evaluation_runs",
        "evaluation_run",
        "evaluations",
        "evidence",
        "evidences",
        "scoring",
        "scores",
        "score",
        "report",
        "reports",
        "evaluation_reports",
        "submission",
        "submissions",
    }
)


def test_module_yaml_head_matches_alembic_head(postgres_owner_url):
    """``module.yaml`` must name the *actual* head, whatever it is.

    This previously pinned the literal ``"20260915_0003"`` as well, so it broke on the
    next legitimate revision while proving nothing that ``declared == head`` does not
    already prove -- the same defect this file's contract-lock counterpart was fixed for.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(B3_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(B3_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()

    module_yaml = (B3_ROOT / "module.yaml").read_text(encoding="utf-8")
    declared = re.search(r'migration_head:\s*"?([0-9A-Za-z_]+)"?', module_yaml)
    assert declared is not None
    assert declared.group(1) == head


def test_alembic_version_table_is_isolated(owner_engine):
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'b3' AND table_name LIKE 'alembic_version%'"
            )
        ).scalars()
    assert list(rows) == ["alembic_version_b3"]


def test_all_business_tables_exist_in_the_b3_schema(owner_engine):
    with owner_engine.connect() as connection:
        rows = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'b3'"
                )
            ).scalars()
        )
    assert REQUIRED_TABLES <= rows


def test_no_legacy_tables_anywhere(owner_engine):
    with owner_engine.connect() as connection:
        rows = set(
            connection.execute(
                text(
                    "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                    "WHERE table_schema IN ('b3', 'public')"
                )
            ).scalars()
        )
    offenders = {name for name in rows if name.split(".")[1] in LEGACY_OBJECTS}
    assert offenders == set(), f"legacy objects present: {offenders}"


def test_only_the_b3_schema_is_owned(owner_engine):
    with owner_engine.connect() as connection:
        schemas = set(
            connection.execute(
                text("SELECT schema_name FROM information_schema.schemata")
            ).scalars()
        )
        assert "b3" in schemas
        touched = set(
            connection.execute(
                text(
                    "SELECT DISTINCT table_schema FROM information_schema.tables "
                    f"WHERE table_name IN ({', '.join(repr(name) for name in sorted(REQUIRED_TABLES))}) "
                    "AND table_schema NOT IN ('information_schema', 'pg_catalog')"
                )
            ).scalars()
        )
    assert touched == {"b3"}


def test_no_foreign_key_leaves_the_b3_schema(owner_engine):
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.conname, ns.nspname AS referencing, fns.nspname AS referenced
                  FROM pg_constraint c
                  JOIN pg_class t ON t.oid = c.conrelid
                  JOIN pg_namespace ns ON ns.oid = t.relnamespace
                  JOIN pg_class ft ON ft.oid = c.confrelid
                  JOIN pg_namespace fns ON fns.oid = ft.relnamespace
                 WHERE c.contype = 'f' AND ns.nspname = 'b3'
                """
            )
        ).all()
    offenders = [(row.conname, row.referenced) for row in rows if row.referenced != "b3"]
    assert offenders == [], f"cross-schema foreign keys: {offenders}"
    assert rows, "expected at least one intra-schema foreign key"


def test_no_cross_schema_statement_in_the_migration():
    """Every revision, not just the one that happened to exist when this was written."""
    for path in sorted((B3_ROOT / "alembic" / "versions").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for schema in ("b2.", "b4."):
            assert schema not in source.replace("b2 schema", ""), (path.name, schema)
        assert "public." not in source, path.name


def test_no_superuser_connection_is_used_by_the_application(database):
    with database.app_session() as session:
        is_superuser = session.execute(
            text("SELECT current_setting('is_superuser')")
        ).scalar_one()
    assert is_superuser == "off"


def test_app_role_cannot_read_protected_tables(database):
    """acceptance 3-10-3, at the database rather than in application code."""
    with database.app_session() as session:
        for table in PROTECTED_TABLES:
            with pytest.raises(ProgrammingError) as error:
                session.execute(text(f"SELECT * FROM b3.{table} LIMIT 1"))  # noqa: S608 - fixed names
            assert "permission denied" in str(error.value).lower()
            session.rollback()


def test_app_role_cannot_write_the_question_bank(database):
    with pytest.raises(ProgrammingError):
        with database.app_session() as session:
            session.execute(text("DELETE FROM b3.questions"))
    with database.app_session() as session:
        session.rollback()


def test_internal_role_can_read_protected_tables(database):
    with database.protected_session() as session:
        for table in PROTECTED_TABLES:
            session.execute(text(f"SELECT * FROM b3.{table} LIMIT 1"))  # noqa: S608 - fixed names


def test_protected_tables_carry_a_comment(owner_engine):
    with owner_engine.connect() as connection:
        rows = dict(
            connection.execute(
                text(
                    "SELECT relname, obj_description(oid, 'pg_class') FROM pg_class "
                    "WHERE relnamespace = 'b3'::regnamespace AND relname = ANY(:names)"
                ),
                {"names": ["reference_answers", "test_cases", "blobs"]},
            ).all()
        )
    assert set(rows) == {"reference_answers", "test_cases", "blobs"}
    for name, comment in rows.items():
        assert comment and "PROTECTED" in comment, name


def test_immutability_triggers_are_installed(owner_engine):
    with owner_engine.connect() as connection:
        rows = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgrelid = ANY(ARRAY['b3.question_versions'::regclass, "
                    "'b3.reference_answers'::regclass, 'b3.test_suites'::regclass, "
                    "'b3.test_cases'::regclass, 'b3.fixtures'::regclass])"
                )
            ).scalars()
        )
    assert rows == {
        "trg_question_versions_guard",
        "trg_reference_answers_guard",
        "trg_test_suites_guard",
        "trg_test_cases_guard",
        "trg_fixtures_guard",
    }


def test_execution_columns_are_nullable_jsonb_objects(owner_engine):
    """Migration 0007: the two execution columns exist, are nullable and are type-guarded.

    ``NULL`` is load-bearing for both: it means ``per_case`` (version) / "declares nothing"
    (case), and it is what keeps already-published content digests reproducible. The
    object-type CHECKs are the database half of "non-null must be a legal object".
    """
    with owner_engine.connect() as connection:
        columns = connection.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'b3' AND table_name IN "
                "('question_versions', 'test_cases') AND column_name IN "
                "('execution_manifest', 'execution')"
            )
        ).all()
        constraints = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = 'b3'::regnamespace "
                    "AND conname IN ("
                    "'ck_question_versions_execution_manifest_object', "
                    "'ck_test_cases_execution_object')"
                )
            )
        }
    declared = {
        (row.table_name, row.column_name): (row.data_type, row.is_nullable)
        for row in columns
    }
    assert declared == {
        ("question_versions", "execution_manifest"): ("jsonb", "YES"),
        ("test_cases", "execution"): ("jsonb", "YES"),
    }
    assert constraints == {
        "ck_question_versions_execution_manifest_object",
        "ck_test_cases_execution_object",
    }


def test_indexes_follow_the_naming_convention(owner_engine):
    """Secondary indexes use ``ix_``/``uq_``; primary keys stay PostgreSQL-named."""
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'b3' "
                "AND indexname NOT LIKE 'pg_%'"
            )
        ).scalars()
    bad = [
        name
        for name in rows
        if not name.startswith(("ix_", "uq_", "pk_"))
        and not name.endswith("_pkey")
        and not name.startswith("alembic_version")
    ]
    assert bad == [], f"indexes without ix_/uq_ prefix: {bad}"


def test_constraint_names_follow_the_convention(owner_engine):
    """``fk_``/``uq_``/``ck_`` are explicit; primary keys stay PostgreSQL-named."""
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.conname, c.contype
                  FROM pg_constraint c
                  JOIN pg_namespace n ON n.oid = c.connamespace
                 WHERE n.nspname = 'b3'
                """
            )
        ).all()
    for name, kind in rows:
        prefix = {"f": "fk_", "u": "uq_", "c": "ck_"}.get(kind)
        if prefix is None:
            continue
        assert name.startswith(prefix), f"{name} should start with {prefix}"


def test_course_level_assignments_replace_the_target_tables(owner_engine):
    """Migration 0010: the hand-picked target model is gone; the hand-off state is in.

    v0.32.0 makes an assignment course-level: keeping ``assignment_sections`` /
    ``assignment_students`` would leave a second answer to "who is this for", and keeping
    ``assignments.section_id`` would leave a third. The notification columns are the only
    new state -- delivery lives in B4.
    """
    with owner_engine.connect() as connection:
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'b3'"
                )
            ).scalars()
        )
        columns = {
            row.column_name: (row.data_type, row.is_nullable, row.column_default)
            for row in connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'b3' AND table_name = 'assignments'"
                )
            )
        }
        constraints = {
            row.conname
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = 'b3'::regnamespace AND conname IN ("
                    "'ck_assignments_notification_state')"
                )
            )
        }
        indexes = set(
            connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'b3'")
            ).scalars()
        )
    assert {"assignment_sections", "assignment_students"} & tables == set()
    assert "section_id" not in columns
    assert columns["notification_state"] == ("text", "NO", "'pending'::text")
    assert columns["notification_scheduled_at"] == (
        "timestamp with time zone",
        "YES",
        None,
    )
    assert constraints == {"ck_assignments_notification_state"}
    assert "ix_assignment_sections_section_id" not in indexes
    assert "ix_assignment_students_student_id" not in indexes
    assert "ix_assignments_course_id" in indexes


def test_notification_state_only_moves_through_the_declared_values(owner_engine):
    """The CHECK constraint is the database half of the closed enum."""
    with pytest.raises(DBAPIError):
        with owner_engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO b3.assignments (assignment_id, course_id, title, status, "
                    "notification_state, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), gen_random_uuid(), 'bad state', 'open', "
                    "'delivered', now(), now())"
                )
            )


def test_app_role_reads_but_cannot_change_the_notification_state(database):
    """The hand-off runs on the internal role; the ordinary role only reads assignments."""
    with database.app_session() as session:
        session.execute(text("SELECT * FROM b3.assignments LIMIT 1"))

    with pytest.raises(ProgrammingError) as error:
        with database.app_session() as session:
            session.execute(text("UPDATE b3.assignments SET notification_state = 'unavailable'"))
    assert "permission denied" in str(error.value).lower()
    with database.app_session() as session:
        session.rollback()


def test_migration_downgrade_then_upgrade_round_trips(postgres_owner_url):
    """The migration is reversible, which the repository rules require."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(B3_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(B3_ROOT / "alembic"))
    engine = create_engine(postgres_owner_url, isolation_level="AUTOCOMMIT")
    try:
        command.downgrade(config, "20260915_0001")
        with engine.connect() as connection:
            remaining = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'b3'"
                    )
                ).scalars()
            )
        assert not (remaining & REQUIRED_TABLES)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            restored = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'b3'"
                    )
                ).scalars()
            )
        assert REQUIRED_TABLES <= restored
    finally:
        engine.dispose()


def test_role_urls_are_distinct_and_not_superusers():
    assert APP_URL != INTERNAL_URL != OWNER_URL
    for url in (APP_URL, INTERNAL_URL):
        assert "autograder_b3_app" in url or "autograder_b3_internal" in url
