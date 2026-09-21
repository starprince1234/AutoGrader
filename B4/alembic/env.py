import logging
import os
import time
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from autograder_b4.logging import get_logger, log_operation, structured_module_logging

config = context.config
if config.config_file_name and config.get_section("loggers"):
    # `disable_existing_loggers=False` is load-bearing: `fileConfig` defaults to True and
    # would disable **every** logger that already exists in this process, including B4's
    # own. Run in-process (a test, or a script that migrates and then serves), that silently
    # stops all further B4 records -- exactly the traceability `RQ-B-4-99` requires. The
    # console handler Alembic configures is unaffected.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
config.set_main_option(
    "sqlalchemy.url", os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
)
target_metadata = None

#: B4 owns exactly one schema; the version table is per module so the three modules
#: migrate independently (docs/database/table-standards.md §7).
VERSION_TABLE = "alembic_version_b4"
SCHEMA = "b4"

#: The least-privilege role that must be able to read and write the new tables. The
#: migration grants to it when it exists, so provisioning and migration agree on the
#: privilege set instead of duplicating it.
APPLICATION_ROLE = os.getenv("B4_DB_ROLE", "autograder_b4")

#: `RQ-B-4-99` acceptance 4 requires the migration *result* to be recorded rather than
#: transcribed into a runbook by hand, so every run emits one structured record either way.
logger = get_logger("ops.migration")


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        version_table_schema=SCHEMA,
        **kwargs,
    )


def _revision_argument() -> str | None:
    """The requested destination revision, as the operator wrote it (`head`, a hash, ...).

    It lives on the `EnvironmentContext` (the `upgrade`/`downgrade` argument), not on the
    `MigrationContext`, which knows only the revisions the database actually has.
    """
    requested = context.get_revision_argument()
    if requested is None:
        return None
    if isinstance(requested, (tuple, list)):
        return ", ".join(str(item) for item in requested)
    return str(requested)


def _run_migrations() -> None:
    """Run the migrations, recording the transition whether they succeed or fail."""
    from_revision = context.get_context().get_current_revision()
    requested_revision = _revision_argument()
    started = time.monotonic()
    with structured_module_logging():
        try:
            with context.begin_transaction():
                context.run_migrations()
        except Exception as error:
            log_operation(
                logger,
                "ops.migration.failed",
                level=logging.ERROR,
                from_revision=from_revision,
                requested_revision=requested_revision,
                duration_ms=int((time.monotonic() - started) * 1000),
                # The exception *type* is the traceable part; the message can embed a DSN,
                # so it is left to the formatter's redaction rather than trusted here.
                reason=type(error).__name__,
                error=str(error),
            )
            raise
        log_operation(
            logger,
            "ops.migration.applied",
            from_revision=from_revision,
            requested_revision=requested_revision,
            result_revision=context.get_context().get_current_revision(),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def run_migrations_offline() -> None:
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    _run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(
            text("SELECT set_config('b4.application_role', :role, false)"),
            {"role": APPLICATION_ROLE},
        )
        # `set_config` opens an implicit transaction. Alembic treats a connection that
        # is already in a transaction as externally managed and will not commit it, so
        # the whole migration would be rolled back on close. Committing here hands a
        # clean connection to Alembic, which then opens and commits its own transaction.
        connection.commit()
        _configure(connection=connection)
        _run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
