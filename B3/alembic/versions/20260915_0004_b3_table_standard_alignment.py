"""Align B3's tables with the platform-owned table standard (``RQ-B-4-30``).

Revision ID: 20260915_0004
Revises: 20260915_0003
Create Date: 2026-09-18

The table standard is owned by the platform module that owns PostgreSQL, and it is now
checked mechanically against every module's migration text. Before this revision that
check reported 32 genuine B3 deviations in exactly two classes; the rest of B3's unusual
keys had already been ruled legitimate and are **not** touched here:

* ``blobs`` (content-addressed primary key), ``consumption_counters`` (enumerated natural
  key), ``reference_attestations`` / ``question_version_score_buckets`` /
  ``question_version_failure_categories`` (composite natural keys), ``event_inbox`` /
  ``event_outbox`` (correctly non-plural) are exempt *by declaration*. A composite key
  may stay composite, but its **constraint** must still be named ``pk_<table>`` -- so
  those tables appear below for the name only, never for the key's shape.

1. **17 primary keys were unnamed.** They were declared as column-level ``PRIMARY KEY``
   (or a bare table-level ``PRIMARY KEY (...)``), so PostgreSQL named them
   ``<table>_pkey``. The standard requires ``pk_<table>``. Only the constraint name
   changes: no column, no key shape, no index is rebuilt. (A rename rewrites only the
   ``pg_constraint`` row; it is catalog-only, not a table rewrite.)
   The companion change to ``0002``/``0003`` declares these names directly, so on a fresh
   database this step finds the wanted name already in place and does nothing.

2. **Time columns declared ``timestamptz`` without explicit millisecond precision.**
   The standard requires ``timestamptz(3)`` for **every** time column (not only
   ``created_at``/``updated_at``), which is also what the wire format emits (RFC 3339 with
   milliseconds, ``logging.to_rfc3339_millis``). All 29 such columns in ``b3`` are listed
   below; the list is the complete set, so a re-run is a no-op rather than a partial fix.

**This revision is a no-op on a database built from the current ``0002``/``0003``**, which
already declare ``pk_<table>`` names and ``timestamptz(3)``. It exists for databases that
were migrated from the **previous** text of those revisions (the seeded integration
instance carries ``<table>_pkey`` names and six-digit precision): there it performs the
repair in place, so no rebuild and no risk to seeded data. Both directions are idempotent
and name-agnostic, which is what makes one artefact serve both cases.

**Data consequence, stated plainly: this is not a no-op.** Narrowing the type discards
sub-millisecond precision on existing rows -- ``ALTER TABLE ... TYPE timestamptz(3)`` is
not a binary-coercible change, so PostgreSQL rewrites each affected table and *rounds*
the stored values to the nearest millisecond. No row is added, removed or reordered and
no value becomes unusable, but the values that were there are not bit-identical
afterwards. That is intended (millisecond is the precision this platform promises) and
it is why this is a data migration rather than a cosmetic one. The reverse direction in
``downgrade()`` cannot restore the discarded fractional digits.

Both directions are **idempotent and name-agnostic**: the current constraint name is
read from ``pg_constraint`` instead of assuming ``<table>_pkey``, and the type change is
guarded by the current precision. Re-running either on an already-converted schema does
nothing, and a fresh database (which has never been "fixed") converges to the same state.

Boundary notes: nothing outside the ``b3`` schema is created, referenced or renamed; no
key shape changes; no compatibility alias, view or surrogate key is introduced. (This
docstring deliberately does not spell another module's schema name: the migration-text
guard in ``tests/test_migrations.py`` scans raw text, prose included, and a qualified
name here would be indistinguishable from a real cross-schema statement.)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260915_0004"
down_revision = "20260915_0003"
branch_labels = None
depends_on = None

SCHEMA = "b3"

#: Tables whose primary-key constraint must be named ``pk_<table>``. Every one is in
#: class 1 above; the composite-key tables are here on purpose (the *name* is what the
#: standard asks for, the key stays composite).
PRIMARY_KEY_TABLES = (
    "questions",
    "question_versions",
    "question_assets",
    "reference_answers",
    "test_suites",
    "test_cases",
    "blobs",
    "fixtures",
    "assignments",
    "assignment_items",
    "event_inbox",
    "event_outbox",
    "question_version_stats",
    "question_version_score_buckets",
    "question_version_failure_categories",
    "reference_attestations",
    "consumption_counters",
)

#: Columns the standard requires to be ``timestamptz(3)``: **every** time column, not just
#: ``created_at`` / ``updated_at``. The standard's §3/§8 wording is "every time column", and
#: the checker was widened to match after B3 measured that the two entry points disagreed
#: (catalog mode checked every column, migration text checked only the two named ones).
#: This list is the complete set of ``timestamp with time zone`` columns in ``b3``.
PRECISION_COLUMNS = (
    ("assignment_items", "created_at"),
    ("assignments", "created_at"),
    ("assignments", "due_at"),
    ("assignments", "updated_at"),
    ("blobs", "created_at"),
    ("consumption_counters", "last_occurred_at"),
    ("event_inbox", "occurred_at"),
    ("event_inbox", "processed_at"),
    ("event_inbox", "received_at"),
    ("event_outbox", "created_at"),
    ("event_outbox", "occurred_at"),
    ("event_outbox", "published_at"),
    ("fixtures", "created_at"),
    ("question_assets", "created_at"),
    ("question_version_stats", "first_occurred_at"),
    ("question_version_stats", "last_occurred_at"),
    ("question_version_stats", "updated_at"),
    ("question_versions", "archived_at"),
    ("question_versions", "created_at"),
    ("question_versions", "published_at"),
    ("question_versions", "updated_at"),
    ("questions", "created_at"),
    ("questions", "deleted_at"),
    ("questions", "updated_at"),
    ("reference_answers", "created_at"),
    ("reference_attestations", "recorded_at"),
    ("reference_attestations", "verified_at"),
    ("test_cases", "created_at"),
    ("test_suites", "created_at"),
)

#: PostgreSQL's own name for a primary key it was not given a name for.
_DEFAULT_PK_SUFFIX = "_pkey"


def upgrade() -> None:
    for table in PRIMARY_KEY_TABLES:
        _rename_primary_key(table, f"pk_{table}")
    for table, column in PRECISION_COLUMNS:
        _set_millisecond_precision(table, column, precision=3)


def downgrade() -> None:
    # Reverse order, reverse direction: types back to bare `timestamptz`, then the
    # constraint names back to PostgreSQL's default. The discarded sub-millisecond
    # digits stay discarded -- a type widening cannot invent them back -- which the
    # module docstring records as the honest cost of this revision.
    for table, column in reversed(PRECISION_COLUMNS):
        _set_millisecond_precision(table, column, precision=None)
    for table in reversed(PRIMARY_KEY_TABLES):
        _restore_default_primary_key_name(table)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _table_exists(connection, table: str) -> bool:
    return bool(
        connection.execute(
            sa.text("SELECT to_regclass(:qualified) IS NOT NULL"),
            {"qualified": f"{SCHEMA}.{table}"},
        ).scalar()
    )


def _primary_key_name(connection, table: str) -> str | None:
    """The table's current primary-key constraint name, whatever it is."""
    return connection.execute(
        sa.text(
            "SELECT con.conname FROM pg_constraint AS con "
            "JOIN pg_class AS rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace AS nsp ON nsp.oid = rel.relnamespace "
            "WHERE nsp.nspname = :schema AND rel.relname = :table AND con.contype = 'p'"
        ),
        {"schema": SCHEMA, "table": table},
    ).scalar()


def _rename_primary_key(table: str, wanted: str) -> None:
    """Name the primary key ``wanted``, or do nothing if it already is.

    ``pg_constraint`` is consulted rather than assuming ``<table>_pkey``: on a database
    that already carries the standard name (or any other) the assumption would turn a
    no-op into an error, and this migration must be safe to re-run.
    """
    connection = op.get_bind()
    if not _table_exists(connection, table):
        return
    current = _primary_key_name(connection, table)
    if current is None or current == wanted:
        return
    op.execute(f'ALTER TABLE {SCHEMA}.{table} RENAME CONSTRAINT "{current}" TO "{wanted}"')


def _restore_default_primary_key_name(table: str) -> None:
    """Give the primary key back PostgreSQL's default name (``<table>_pkey``)."""
    connection = op.get_bind()
    if not _table_exists(connection, table):
        return
    current = _primary_key_name(connection, table)
    default = f"{table}{_DEFAULT_PK_SUFFIX}"
    if current is None or current == default:
        return
    op.execute(f'ALTER TABLE {SCHEMA}.{table} RENAME CONSTRAINT "{current}" TO "{default}"')


def _set_millisecond_precision(table: str, column: str, *, precision: int | None) -> None:
    """Set (or clear) the declared fractional-second precision of one timestamp column.

    Guarded on the value that is actually declared, so running the same direction twice
    is a no-op rather than an error -- which matters because ``upgrade`` is also reached
    on a fresh database and because ``downgrade`` is exercised by the round-trip test.
    """
    connection = op.get_bind()
    if not _table_exists(connection, table):
        return
    current = connection.execute(
        sa.text(
            "SELECT datetime_precision FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
        ),
        {"schema": SCHEMA, "table": table, "column": column},
    ).scalar()
    if current is None:
        # The column does not exist. Every one of them is created by an earlier revision,
        # so this is unreachable on a linear history; returning keeps a re-run safe.
        return
    if precision is None:
        if current == 6:
            return
        wanted = "timestamp with time zone"
    else:
        if current == precision:
            return
        wanted = f"timestamp({precision}) with time zone"
    # The column name comes from this module's own tuple, never from input, and is
    # quoted for the same reason every other identifier here is.
    op.execute(
        f'ALTER TABLE {SCHEMA}.{table} ALTER COLUMN "{column}" TYPE {wanted}'
    )
