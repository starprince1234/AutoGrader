"""RQ-B-4-10 / RQ-B-4-30: least privilege, migration head, and the schema standard.

These are the checks that must be answered by the database rather than by a claim in a
document, so every assertion here runs real SQL as the real application role.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError

from autograder_b4.config import ConfigurationError, Settings, assert_not_superuser
from autograder_b4.db.engine import (
    alembic_script_head,
    current_migration_head,
    declared_migration_head,
    migration_head_status,
)
from autograder_b4.logging import RedactingJsonFormatter
from autograder_b4.ops.backup import BackupError, create_backup
from autograder_b4.ops.provision import (
    ProvisionRequest,
    provision,
    verify_role_is_least_privilege,
)

B4_ROOT = Path(__file__).resolve().parents[1]


class RecordCollector(logging.Handler):
    """Capture B4's structured records directly from the module's own logger.

    `caplog` observes what reaches the root logger, which is not what is under test here:
    the migration path deliberately keeps B4's records in its own namespace when the process
    logging configuration belongs to Alembic, so the assertions attach to the same namespace
    the emission point uses.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def events(self, name: str) -> list[logging.LogRecord]:
        return [record for record in self.records if getattr(record, "event", "") == name]


@pytest.fixture
def records():
    """Attach a collector to B4's namespace at INFO, independent of the root's level.

    Setting the level matters: a test that never builds the module has no root logger
    configuration, and the default root level would silently drop the INFO records this
    fixture exists to observe -- a passing test that observes nothing.
    """
    collector = RecordCollector()
    logger = logging.getLogger("autograder_b4")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous)


# --------------------------------------------------------------------------- privileges


def test_cross_schema_select_is_denied_by_the_database(engine: Engine) -> None:
    """RQ-B-4-10 acceptance 2: a cross-schema SELECT must fail, not merely be avoided.

    The target schema is asserted to exist first: without it the statement would fail for
    a different reason and the evidence would be worthless.
    """
    _assert_schema_exists(engine, "b2")
    with pytest.raises(ProgrammingError) as failure, engine.begin() as connection:
        connection.execute(text("SELECT count(*) FROM b2.users"))
    assert "permission denied for schema b2" in str(failure.value)


def _assert_schema_exists(engine: Engine, schema: str) -> None:
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"), {"schema": schema}
        ).fetchone()
    assert found is not None, (
        f"schema {schema} does not exist in this database, so cross-schema denial cannot "
        f"be demonstrated; create it with MAIN/infra/postgres/init.sql first"
    )


def test_cross_schema_create_is_denied_by_the_database(engine: Engine) -> None:
    _assert_schema_exists(engine, "b3")
    with pytest.raises(ProgrammingError) as failure, engine.begin() as connection:
        connection.execute(text("CREATE TABLE b3.probe_from_b4 (i integer)"))
    assert "permission denied for schema b3" in str(failure.value)


def test_cross_schema_foreign_key_cannot_be_created(engine: Engine) -> None:
    """RQ-B-4-30 acceptance 4: a cross-schema FK is rejected, with real evidence.

    The attempt fails at the privilege check for the other schema, which is the stronger
    outcome: B4 cannot even read the referenced table to point a constraint at it.
    """
    _assert_schema_exists(engine, "b2")
    with pytest.raises(ProgrammingError) as failure, engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE b4.cross_schema_probe ("
                "  id uuid PRIMARY KEY,"
                "  other uuid REFERENCES b2.users (user_id)"
                ")"
            )
        )
    assert "b2" in str(failure.value)


def test_b4_schema_itself_is_writable(engine: Engine) -> None:
    """The positive control: the same role can create and drop inside its own schema."""
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE b4.own_schema_probe (i integer)"))
        connection.execute(text("DROP TABLE b4.own_schema_probe"))


def test_provisioned_role_has_no_superuser_attributes(
    admin_engine: Engine, settings: Settings
) -> None:
    import os

    role = os.getenv("B4_DB_ROLE", "autograder_b4")
    violations = verify_role_is_least_privilege(admin_engine, role, "b4")
    assert violations == [], violations


def test_superuser_connection_is_refused_at_startup(admin_engine: Engine) -> None:
    """G-SEC-07 / acceptance 3: an over-privileged connection must stop the module."""
    with pytest.raises(ConfigurationError) as failure:
        assert_not_superuser(admin_engine)
    assert "over-privileged" in str(failure.value)


def test_application_engine_passes_the_superuser_check(engine: Engine) -> None:
    role = assert_not_superuser(engine)
    assert role == "autograder_b4"


def test_weak_default_password_is_refused_in_production() -> None:
    with pytest.raises(ConfigurationError) as failure:
        Settings.from_environment(
            {
                "B4_ENV": "production",
                "DATABASE_URL": "postgresql+psycopg://autograder_b4:change-me@localhost:55432/autograder",
                "JWT_PRIVATE_KEY_FILE": "secrets/jwt-ed25519.pem",
            }
        )
    assert "weak" in str(failure.value).lower() or "production" in str(failure.value).lower()


def test_missing_signing_key_is_refused_in_production() -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_environment(
            {"B4_ENV": "production", "DATABASE_URL": "postgresql+psycopg://u:secret@h/db"}
        )


# ------------------------------------------------------------------------ migration head


def test_migration_head_matches_module_manifest(engine: Engine, settings: Settings) -> None:
    """RQ-B-4-10 acceptance 5 / RQ-B-4-30 acceptance 3."""
    status = migration_head_status(engine, settings.module_root)
    assert status.healthy, status.detail
    assert current_migration_head(engine) == declared_migration_head(settings.module_root)


def test_migration_head_matches_the_migration_files(engine: Engine) -> None:
    """The manifest agreeing with the database is not enough if the files disagree."""
    assert current_migration_head(engine) == alembic_script_head()


def test_expected_platform_tables_exist(engine: Engine) -> None:
    """RQ-B-4-30 acceptance 2: the eight entities plus the platform tables B4 owns."""
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'b4'")
        ).fetchall()
    present = {row[0] for row in rows}
    for expected in (
        "users",
        "credentials",
        "sessions",
        "roles",
        "courses",
        "sections",
        "enrollments",
        "audit_records",
        "roster_imports",
        "roster_import_rows",
        "roster_import_files",
        "outbox_events",
        "inbox_events",
        "dlq_messages",
        "login_attempts",
        "alembic_version_b4",
    ):
        assert expected in present, f"{expected} is missing"


def test_empty_database_migrates_from_zero(
    admin_engine: Engine,
    admin_dsn: str,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    records: RecordCollector,
) -> None:
    """RQ-B-4-10 acceptance 5: `alembic upgrade head` on a database with nothing in it.

    The database is created and provisioned with the privileged connection, exactly as the
    runbook prescribes, and the migration then runs as the **least-privilege application
    role** -- which is the whole point: if the app role could not migrate its own schema,
    the deployment procedure would be a fiction.

    `DATABASE_URL` is how the operator selects the target (`alembic/env.py` honours it, as
    the runbook documents), so the test drives the same knob instead of a private one. The
    migration result is asserted to be recorded as well (`RQ-B-4-99` acceptance 4): "a
    migration ran" has to be answerable from the logs, not from a runbook transcript.
    """
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    from alembic import command

    database = f"b4_migration_probe_{uuid.uuid4().hex[:8]}"
    admin_url = make_url(admin_dsn)
    maintenance_engine = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with maintenance_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    try:
        app_url = make_url(settings.database_url).set(database=database)
        # The role is cluster-wide, so the password must be the application role's own:
        # provisioning with the admin password would silently break every other database.
        provision(
            ProvisionRequest(
                admin_dsn=admin_url.set(database=database).render_as_string(hide_password=False),
                role=app_url.username or "autograder_b4",
                password=app_url.password or "",
                database=database,
                schema="b4",
            )
        )
        app_dsn = app_url.render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", app_dsn)
        command.upgrade(Config(str(B4_ROOT / "alembic.ini")), "head")

        applied = records.events("ops.migration.applied")
        assert len(applied) == 1, [record.getMessage() for record in records.records]
        assert applied[0].from_revision is None, "the database started with no revision"
        # Alembic resolves `head` before running, so the record names the revision it
        # actually targeted rather than the word the operator typed.
        assert applied[0].requested_revision == alembic_script_head()
        assert applied[0].result_revision == alembic_script_head()
        assert applied[0].duration_ms >= 0
        # The record is shipped to a log collector, so it must not carry the DSN it used.
        rendered = RedactingJsonFormatter().format(applied[0])
        assert "change-me" not in rendered
        assert "postgresql" not in rendered

        probe_engine = create_engine(app_dsn)
        try:
            assert current_migration_head(probe_engine) == alembic_script_head()
            with probe_engine.connect() as connection:
                count = connection.execute(
                    text("SELECT count(*) FROM pg_tables WHERE schemaname = 'b4'")
                ).scalar_one()
            assert count >= 16
        finally:
            probe_engine.dispose()
    finally:
        with maintenance_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        maintenance_engine.dispose()


# ------------------------------------------------------------------------------- backup


def test_backup_writes_a_dump_and_records_the_result(
    admin_dsn: str, workspace_tmp: Path, records: RecordCollector
) -> None:
    """RQ-B-4-99 acceptance 4: the backup result is recorded rather than transcribed.

    The dump is produced by the same command the runbook documents, against the module-local
    instance, and the archive is read back: an operator's log line saying "backup done" is
    worth nothing next to a file that is actually a `pg_dump` archive.
    """
    from tests.conftest import POSTGRES_CONTAINER

    destination = workspace_tmp / "b4-probe.dump"
    result = create_backup(
        container=POSTGRES_CONTAINER, admin_dsn=admin_dsn, destination=destination
    )

    assert result.bytes_written > 0
    assert result.destination.read_bytes()[:5] == b"PGDMP", "not a pg_dump custom archive"
    completed = records.events("ops.backup.completed")
    assert len(completed) == 1, [record.getMessage() for record in records.records]
    assert completed[0].schema == "b4"
    assert completed[0].bytes_written == result.bytes_written
    assert completed[0].dump == destination.name
    assert completed[0].duration_ms >= 0
    rendered = RedactingJsonFormatter().format(completed[0])
    assert "change-me" not in rendered
    assert "postgresql" not in rendered


def test_a_failed_backup_leaves_no_file_and_is_recorded(
    admin_dsn: str, workspace_tmp: Path, records: RecordCollector
) -> None:
    """A backup that did not happen must be as visible as one that did."""
    destination = workspace_tmp / "b4-missing.dump"
    with pytest.raises(BackupError):
        create_backup(
            container="b4-no-such-container", admin_dsn=admin_dsn, destination=destination
        )
    assert not destination.exists(), "a failed dump must not be left behind as a backup"
    failed = records.events("ops.backup.failed")
    assert len(failed) == 1, [record.getMessage() for record in records.records]
    assert failed[0].exit_code != 0
    assert "change-me" not in RedactingJsonFormatter().format(failed[0])
