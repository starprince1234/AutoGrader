""":mod:`autograder_b4.ops.table_standards` -- enforcement of `docs/database/table-standards.md`.

The standard is only worth anything if a violation is *detectable*, so it is expressed as a
checker rather than as prose a reviewer is expected to apply. There are two modes, and they
apply **the same rules** to two different subjects:

* the **catalog** mode inspects a live schema -- B4's own by default, and any module's on
  request (`check_table_standards(engine, "b3")`), because the standard is B4's and the tool
  that judges it should be able to judge the modules that follow it. A schema checked without
  the owner's `<entity>_id` declaration is held to every shape rule but not to the exact
  entity name, and the report says which of the two happened; and
* the **migration** mode reads the DDL text of every module's Alembic revisions. Reading
  another module's *migration files* is not a cross-schema database read: no connection to
  their tables is opened, and the conclusion is about their migration text rather than about
  their live database.

Two limits are stated here because a count is read as a verdict:

* **it reads files, not tables** (above): a migration-mode finding means "the migration as
  written does not follow the standard", not "the live table is wrong"; and
* **it checks declarations, one statement at a time, with no cross-revision state**. It does
  not model `ALTER TABLE` (`RENAME CONSTRAINT`, `ALTER COLUMN TYPE`), `DO` blocks or `DROP`,
  so it **cannot credit a post-hoc fix**: a corrective revision that only writes `ALTER`s
  moves the live schema towards the standard while the checked-declaration count does not
  move at all. Those statements are counted separately and printed next to the checked count
  (`MigrationReport.unmodelled`) so the number cannot be mistaken for coverage, and resolving
  an `ALTER`/`DO` sequence into an end state is an explicit non-goal here.

`python -m autograder_b4.ops.table_standards` runs both; the test suite calls the same
functions, so the checks and the standard cannot drift into two different standards.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import DateTime, Engine, inspect, text

SCHEMA = "b4"
#: The module that owns `SCHEMA`; declarations for the catalog mode are keyed by module name.
MODULE = "B4"
#: Alembic's bookkeeping table, for any module (`alembic_version_b4`, `..._b3`, ...): created
#: by the migration tool, not by a module's hand.
TOOL_TABLE_PREFIX = "alembic_version"
MIGRATION_DIRECTORY = "alembic/versions"

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
INDEX_PREFIXES = ("ix_", "uq_")
FK_PREFIX = "fk_"
CHECK_PREFIX = "ck_"
PK_PREFIX = "pk_"

#: `docs/database/table-standards.md` §2 requires the primary key to be `<entity>_id`,
#: where the entity is the thing the table models. Deriving that name from the table name
#: would be a guess -- `audit_records` models the `audit` entity, not `audit_record` -- so
#: the mapping is declared. A table missing from this map fails the check, which forces a
#: new table to make the decision explicitly instead of inheriting a coincidence.
PRIMARY_KEY_COLUMNS = {
    "users": "user_id",
    "courses": "course_id",
    "sections": "section_id",
    "roles": "role_id",
    "credentials": "credential_id",
    "sessions": "session_id",
    "enrollments": "enrollment_id",
    "roster_imports": "roster_import_id",
    "roster_import_files": "file_id",
    "roster_import_rows": "row_id",
    "audit_records": "audit_id",
    "outbox_events": "outbox_id",
    "inbox_events": "inbox_id",
    "dlq_messages": "dlq_id",
    "login_attempts": "attempt_id",
    "password_reset_codes": "code_id",
}

#: Primary keys that are deliberately **not** a surrogate `<entity>_id`, which
#: `docs/database/table-standards.md` §2.1-§2.3 allow for three archetypes. Two halves are
#: required, and both are checked:
#:
#: * the table must be declared here -- a table that merely *looks* like one of the
#:   archetypes (a text primary key, a composite key, an undeclared singular name) is still
#:   reported, so nothing is exempted implicitly; and
#: * the DDL must **satisfy** the archetype's precondition -- a declaration cannot launder a
#:   table that does not follow the pattern (a digest key with no digest check, a
#:   "composite" key that is really one column, an "enum" key not constrained to a set).
CONTENT_ADDRESSED = "content_addressed"
COMPOSITE_NATURAL_KEY = "composite_natural_key"
ENUMERATED_NATURAL_KEY = "enumerated_natural_key"

#: Column-name suffixes that mark a content digest, and the shape of its check. A
#: content-addressed key must be digest-shaped **and** bounded to a digest, which is what
#: separates `blobs.content_sha256` from any other text primary key.
DIGEST_SUFFIXES = ("_sha256", "_sha512", "_digest", "_hash")
DIGEST_MARKERS = ("[0-9a-f]", "0-9a-f")
DIGEST_LENGTH = 64
#: An enumerated key is a short label, not a free-text field.
ENUM_MAX_LENGTH = 64


@dataclass(frozen=True)
class NaturalKey:
    """A declared exception to the surrogate-key rule, with the reason it exists."""

    archetype: str
    columns: tuple[str, ...]
    reason: str


NATURAL_KEYS = {
    # Keyed by `(module, table)`, not by table name: `inbox_events` exists in **both** B2
    # (`(consumer, event_id)`) and B4 (`inbox_id`), so a name-keyed declaration would both
    # misjudge B4's own table and silently exempt a differently shaped one.
    ("B2", "inbox_events"): NaturalKey(
        COMPOSITE_NATURAL_KEY,
        ("consumer", "event_id"),
        "the dedup key of an inbox is (consumer, event)",
    ),
    ("B2", "submission_files"): NaturalKey(
        COMPOSITE_NATURAL_KEY,
        ("submission_id", "path"),
        "a file is identified by (submission, path); a surrogate would allow the same path twice",
    ),
    ("B3", "blobs"): NaturalKey(
        CONTENT_ADDRESSED,
        ("content_sha256",),
        "the digest *is* the identity: dedup by content is why the table exists",
    ),
    ("B3", "consumption_counters"): NaturalKey(
        ENUMERATED_NATURAL_KEY,
        ("outcome",),
        "one row per delivery outcome, keyed by the outcome",
    ),
    ("B3", "question_version_score_buckets"): NaturalKey(
        COMPOSITE_NATURAL_KEY,
        ("question_version_id", "bucket_label"),
        "the natural uniqueness is the pair; a surrogate adds a meaningless column",
    ),
    ("B3", "question_version_failure_categories"): NaturalKey(
        COMPOSITE_NATURAL_KEY,
        ("question_version_id", "failed_category"),
        "the natural uniqueness is the pair; a surrogate adds a meaningless column",
    ),
    ("B3", "reference_attestations"): NaturalKey(
        COMPOSITE_NATURAL_KEY,
        ("question_version_id", "content_digest"),
        "an attestation is about (version, digest) and nothing else",
    ),
}

#: Table names that are correct as written: `event_inbox`/`event_outbox` are the conventional
#: names of the pattern and are not count nouns, and `evidence` is a mass noun. Declared, not
#: inferred -- any *other* singular name is still a violation, and the match is exact (module
#: *and* table), so `event_inbox_backup` inherits no exemption.
NON_PLURAL_TABLE_NAMES = {
    ("B2", "case_evidence"): "'evidence' is a mass noun; 'case_evidences' is not English",
    ("B3", "event_inbox"): "the conventional name for an idempotent consumer ledger",
    ("B3", "event_outbox"): "the conventional name for a transactional outbox",
}


# --------------------------------------------------------------------- live catalog mode


def check_table_standards(
    engine: Engine,
    schema: str = SCHEMA,
    *,
    primary_keys: Mapping[str, str] | None = None,
) -> list[str]:
    """Return every violated rule, as `table.column: message` strings.

    `schema` may be **any** module's schema, not only B4's: the standard is B4's, so the
    checker has to be able to judge the schema of the module that follows it. Which schema a
    module owns is declared in its own `module.yaml: database.schema`, and the module name is
    that schema in upper case (`b3` -> `B3`), which is how the archetype declarations below
    are keyed.

    `primary_keys` is the owning module's own `<entity>_id` declaration -- B4's is
    `PRIMARY_KEY_COLUMNS`, and a module may keep its own (`{"questions": "question_id", ...}`).
    A schema checked **without** one is still held to every shape rule (a single `<x>_id`
    uuid column, the `pk_<table>` constraint name, the declared archetypes), but the exact
    entity name cannot be asserted for a module that has not declared it. That is the
    difference between "this schema is checked" and "this schema is checked against a
    declaration", and the report says which one happened.
    """
    violations: list[str] = []
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names(schema=schema))
    if not tables:
        return [f"schema {schema} contains no tables"]
    module = _module_for_schema(schema)
    declared = primary_keys if primary_keys is not None else (
        PRIMARY_KEY_COLUMNS if schema == SCHEMA else {}
    )
    for table in tables:
        if table.startswith(TOOL_TABLE_PREFIX):
            continue
        if not SNAKE_CASE.match(table):
            violations.append(f"{table}: table name is not lower snake_case")
        natural = NATURAL_KEYS.get((module, table))
        expected_pk = declared.get(table)
        primary_key = inspector.get_pk_constraint(table, schema=schema)
        constrained = list(primary_key.get("constrained_columns") or [])
        columns = {column["name"]: column for column in inspector.get_columns(table, schema=schema)}
        checks = _check_constraints(engine, schema, table)
        if not constrained:
            violations.append(f"{table}: has no primary key")
        elif natural is not None:
            # Declared archetype: the identity is the natural key, so the surrogate rules do
            # not apply -- but the declaration has to match reality and to satisfy the
            # archetype's precondition (see `NATURAL_KEYS`).
            violations.extend(
                f"{table}: {message}"
                for message in _check_declared_natural_key(
                    table,
                    natural,
                    constrained,
                    {name: str(column["type"]) for name, column in columns.items()},
                    [definition for _name, definition in checks],
                )
            )
        elif expected_pk is None and declared:
            # The declaration is what makes the name checkable; a table without one is a
            # gap in the standard's coverage, not a passing table.
            violations.append(
                f"{table}: primary key {constrained} has no declared entity in the module's "
                "primary-key declaration; the entity name must be chosen deliberately"
            )
        elif expected_pk is None:
            violations.extend(
                f"{table}: {message}"
                for message in _primary_key_shape_messages(
                    constrained,
                    {name: str(column["type"]) for name, column in columns.items()},
                )
            )
        elif constrained != [expected_pk]:
            violations.append(
                f"{table}: primary key is {constrained}, expected ['{expected_pk}']"
            )
        elif not expected_pk.endswith("_id"):
            violations.append(f"{table}: primary key {expected_pk!r} does not end in '_id'")
        expected_pk_name = f"{PK_PREFIX}{table}"
        if primary_key.get("name") and primary_key["name"] != expected_pk_name:
            violations.append(
                f"{table}: primary key constraint is named {primary_key['name']!r}, "
                f"expected {expected_pk_name!r}"
            )

        for name, column in columns.items():
            if not SNAKE_CASE.match(name):
                violations.append(f"{table}.{name}: column name is not lower snake_case")
            # The type is inspected as a type, not as a rendered string: `TIMESTAMP` and
            # `TIMESTAMP WITH TIME ZONE` render differently enough that string matching
            # missed the plain-`timestamp` case this rule exists to catch.
            #
            # Every timestamp column is held to `timestamptz(3)` (§3/§8), and the two columns
            # the standard names are held to being a time type at all -- the migration scope
            # applies exactly the same rule, so the two entry points cannot disagree.
            column_type = column["type"]
            if isinstance(column_type, DateTime):
                if not column_type.timezone:
                    violations.append(
                        f"{table}.{name}: is timestamp without time zone; the standard "
                        "requires timestamptz(3)"
                    )
                elif column_type.precision != 3:
                    violations.append(
                        f"{table}.{name}: timestamptz precision is {column_type.precision}, "
                        "expected 3 (milliseconds)"
                    )
            elif name in _TIME_COLUMNS:
                violations.append(
                    f"{table}.{name}: is {column_type}, the standard requires timestamptz(3)"
                )
        if (
            expected_pk is not None
            and expected_pk in columns
            and str(columns[expected_pk]["type"]).lower() != "uuid"
        ):
            violations.append(
                f"{table}.{expected_pk}: primary key type is "
                f"{columns[expected_pk]['type']}, expected uuid"
            )

        for foreign_key in inspector.get_foreign_keys(table, schema=schema):
            name = foreign_key.get("name") or ""
            if not name.startswith(FK_PREFIX):
                violations.append(
                    f"{table}: foreign key {name!r} does not start with {FK_PREFIX!r}"
                )
            if foreign_key.get("referred_schema") not in (None, schema):
                violations.append(
                    f"{table}: foreign key {name} crosses into schema "
                    f"{foreign_key.get('referred_schema')!r}, which is forbidden"
                )
        for constraint in inspector.get_unique_constraints(table, schema=schema):
            name = constraint.get("name") or ""
            if not name.startswith("uq_"):
                violations.append(f"{table}: unique constraint {name!r} does not start with 'uq_'")
        for index in inspector.get_indexes(table, schema=schema):
            name = index.get("name") or ""
            if index.get("unique") and name.startswith("uq_"):
                continue
            if not name.startswith(INDEX_PREFIXES):
                violations.append(
                    f"{table}: index {name!r} does not start with 'ix_' or 'uq_'"
                )
        for check_name, _definition in checks:
            if not check_name.startswith(CHECK_PREFIX):
                violations.append(
                    f"{table}: check constraint {check_name!r} does not start with 'ck_'"
                )

    if schema == SCHEMA:
        # `public` must not become a shared table space (docs/architecture/boundaries.md).
        # This is about B4's own tables, so it is checked when B4's schema is the subject --
        # not when the tool is pointed at another module's schema.
        owned = [
            name
            for name in inspector.get_table_names(schema="public")
            if name in {f"{table}" for table in tables}
        ]
        if owned:  # pragma: no cover - would mean a shared table was created
            violations.append(f"public contains B4 tables: {owned}")
    return violations


def _check_constraints(engine: Engine, schema: str, table: str) -> list[tuple[str, str]]:
    """`(name, definition)` of every check constraint, definitions included.

    The definition is what makes the archetype preconditions checkable against a real
    database: "the digest column is constrained to 64 hex characters" is a statement about
    the constraint's expression, not about its name.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT con.conname, pg_get_constraintdef(con.oid) FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
                "WHERE con.contype = 'c' AND nsp.nspname = :schema AND rel.relname = :table"
            ),
            {"schema": schema, "table": table},
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


# ------------------------------------------------------------------- archetype preconditions


def _check_declared_natural_key(
    table: str,
    natural: NaturalKey,
    columns: list[str],
    column_types: dict[str, str],
    check_definitions: list[str],
) -> list[str]:
    """Validate a declared archetype against the schema it is declared for.

    Messages are **bare** (no `table:` prefix): the catalog mode prefixes them with the table
    and the static mode prefixes them with the file, so building the prefix here as well would
    print it twice. Returns violations both when the declaration disagrees with the schema and
    when the table does not satisfy the archetype -- the declaration is a statement about
    *this* table, so both directions have to hold.
    """
    if list(natural.columns) != columns:
        return [
            f"primary key is {columns}, but NATURAL_KEYS declares {list(natural.columns)} "
            f"({natural.archetype}); the declaration and the schema must agree"
        ]

    if natural.archetype == CONTENT_ADDRESSED:
        return _check_content_addressed(natural.columns, column_types, check_definitions)
    if natural.archetype == ENUMERATED_NATURAL_KEY:
        return _check_enumerated_key(natural.columns, column_types, check_definitions)
    if natural.archetype == COMPOSITE_NATURAL_KEY:
        # A single-column "composite" key cannot reach this point: `columns` equals the
        # declaration, and the declarations are checked for length by their own test.
        return []
    return [f"unknown primary-key archetype {natural.archetype!r}"]  # pragma: no cover


def _check_content_addressed(
    columns: tuple[str, ...],
    column_types: dict[str, str],
    check_definitions: list[str],
) -> list[str]:
    """A digest key must look like a digest *and* be constrained like one (§2.1)."""
    column = columns[0]
    violations: list[str] = []
    if not column.endswith(DIGEST_SUFFIXES):
        violations.append(
            f"content-addressed primary key {column!r} is not digest-shaped "
            f"(one of {list(DIGEST_SUFFIXES)})"
        )
    type_text = (column_types.get(column) or "").lower()
    bounded = bool(re.search(rf"\(\s*{DIGEST_LENGTH}\s*\)", type_text))
    digest_check = any(
        str(DIGEST_LENGTH) in definition
        and any(marker in definition for marker in DIGEST_MARKERS)
        and column in definition
        for definition in check_definitions
    )
    if not (bounded or digest_check):
        violations.append(
            f"content-addressed primary key {column!r} has no {DIGEST_LENGTH}-character "
            "digest check; the archetype requires the key to be bounded to a digest"
        )
    return violations


def _check_enumerated_key(
    columns: tuple[str, ...],
    column_types: dict[str, str],
    check_definitions: list[str],
) -> list[str]:
    """An enumerated key must be a short label constrained to a fixed value set (§2.2)."""
    column = columns[0]
    violations: list[str] = []
    type_text = (column_types.get(column) or "").lower()
    length = re.search(r"\(\s*(\d+)\s*\)", type_text)
    if length is None or int(length.group(1)) > ENUM_MAX_LENGTH:
        violations.append(
            f"enumerated primary key {column!r} is {type_text or 'untyped'}; the "
            f"archetype requires a bounded label of at most {ENUM_MAX_LENGTH} characters"
        )
    if not any(
        column in definition and re.search(r"\bIN\s*\(", definition, re.IGNORECASE)
        for definition in check_definitions
    ):
        violations.append(
            f"enumerated primary key {column!r} is not constrained to a value set; "
            "the archetype requires a CHECK ... IN (...) on the key"
        )
    return violations


# ------------------------------------------------------------------ migration DDL mode

#: Statements the static scanner understands. A migration that writes DDL some other way is
#: counted as zero statements, and a module whose migrations yield zero statements is
#: reported: silently matching nothing is the one failure mode a linter must not have.
#:
#: `<expr>` is the placeholder `_string_of` substitutes for a name it could not resolve.
#: Matching it keeps the *statement* visible (so its table is still checked) while the
#: schema comparison is skipped, instead of dropping the statement silently.
_NAME = r"(?:[A-Za-z_][\w$]*|<expr>)"
_QUALIFIED = rf"(?:{_NAME}(?:\.{_NAME})?)"
_CREATE_TABLE = re.compile(
    rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{_QUALIFIED})\s*\(",
    re.IGNORECASE,
)
_CREATE_INDEX = re.compile(
    rf"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"(?P<name>{_NAME})\s+ON\s+(?P<table>{_QUALIFIED})",
    re.IGNORECASE,
)
_CREATE_VIEW = re.compile(
    rf"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?P<name>{_QUALIFIED})",
    re.IGNORECASE,
)
_SELECT_SOURCE = re.compile(
    rf"\b(?:FROM|JOIN)\s+(?P<name>{_QUALIFIED})", re.IGNORECASE
)
_CONSTRAINT = re.compile(
    r"^CONSTRAINT\s+(?P<name>\S+)\s+"
    r"(?P<kind>PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK|EXCLUDE)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_UNNAMED_CONSTRAINT = re.compile(
    r"^(?P<kind>PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_REFERENCES = re.compile(
    r"REFERENCES\s+(?P<name>[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)", re.IGNORECASE
)
_TYPE_MODIFIER = re.compile(
    r"\s+(?=NOT\b|NULL\b|DEFAULT\b|PRIMARY\b|UNIQUE\b|REFERENCES\b|CHECK\b|CONSTRAINT\b"
    r"|GENERATED\b|COLLATE\b)",
    re.IGNORECASE,
)
#: Every spelling PostgreSQL accepts for a timestamp column. Matching one spelling only is
#: how a rule acquires both false positives (`timestamp(3) with time zone` blamed for having
#: no time zone) and false negatives (`timestamp(3) with time zone` escaping the precision
#: rule), so all of them are normalised in one place.
_TIMESTAMP_SHAPE = re.compile(
    r"^(?P<base>timestamp|timestamptz)"
    r"\s*(?:\(\s*(?P<precision>\d+)\s*\))?"
    r"(?P<zone>\s+with(?:out)?\s+time\s+zone)?$",
    re.IGNORECASE,
)
_TIME_COLUMNS = ("created_at", "updated_at")

#: Alembic operations whose first argument is a constraint name, so the prefix rules apply
#: directly to the call rather than to SQL text.
_NAMED_CONSTRAINT_OPERATIONS = {
    "op.create_unique_constraint": "uq_",
    "op.create_foreign_key": "fk_",
    "op.create_check_constraint": "ck_",
}
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_DOLLAR_QUOTED = re.compile(r"\$(?P<tag>\w*)\$.*?\$(?P=tag)\$", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Statements this scope **recognises but does not model**: `ALTER TABLE` (including
#: `RENAME CONSTRAINT` and `ALTER COLUMN TYPE`), `DROP`, `DO` blocks, and the other `CREATE`
#: kinds. They are counted separately and reported, because the count of *checked*
#: declarations is not a verdict on the schema: a corrective revision that only writes
#: `ALTER TABLE`/`DO` moves the live schema towards the standard while the checked
#: declaration count stays put. Two modules measured exactly this (B3's corrective revision
#: reported 0 statements; B2's `DO $$ … $$` left the total unchanged).
_UNMODELLED_STATEMENT = re.compile(
    r"\b(?:ALTER\s+(?:TABLE|INDEX|VIEW|SEQUENCE|TYPE)|DROP\s+\w+|DO\b"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE|TRIGGER|SCHEMA|TYPE|SEQUENCE|EXTENSION)"
    r"|COMMENT\s+ON|GRANT\b|REVOKE\b|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MigrationScan:
    """One migration file's contribution, including how much of it was recognised."""

    statements: int
    unmodelled: int
    violations: tuple[str, ...]


@dataclass(frozen=True)
class MigrationReport:
    """The whole-repository scan.

    `statements` counts the declarations that were **checked**; `unmodelled` counts the
    statements that were **seen but not modelled** (see `_UNMODELLED_STATEMENT`). Both are
    evidence that the scan did something, and the second is the honest half: a corrective
    revision full of `ALTER TABLE` cannot move the checked count, so the count is not a
    measure of conformance.
    """

    violations: tuple[str, ...]
    files: int
    statements: int
    unmodelled: int


@dataclass(frozen=True)
class _Location:
    """Where a violation was found, so a reader can open the file and look."""

    module: str
    file_name: str
    schema: str

    def violation(self, subject: str, message: str) -> str:
        return f"{self.module}/{self.file_name}: {subject}: {message}"


def check_migrations(
    module_root: Path, *, modules: tuple[str, ...] | None = None
) -> MigrationReport:
    """Scan `B*/alembic/versions/*.py` against the standard and report what was found."""
    violations: list[str] = []
    files = 0
    statements = 0
    unmodelled = 0
    for module_directory in sorted(module_root.glob("B*")):
        if not module_directory.is_dir():
            continue
        if modules is not None and module_directory.name not in modules:
            continue
        versions = module_directory / MIGRATION_DIRECTORY
        paths = sorted(versions.glob("*.py")) if versions.is_dir() else []
        if not paths:
            continue
        schema = _declared_schema(module_directory)
        if schema is None:
            violations.append(
                f"{module_directory.name}: module.yaml declares no database.schema, so its "
                "migrations cannot be checked against it"
            )
            continue
        module_statements = 0
        module_unmodelled = 0
        for path in paths:
            scan = scan_migration(path, module=module_directory.name, schema=schema)
            files += 1
            statements += scan.statements
            unmodelled += scan.unmodelled
            module_statements += scan.statements
            module_unmodelled += scan.unmodelled
            violations.extend(scan.violations)
        if module_statements + module_unmodelled == 0:
            # A module whose migrations only write `ALTER TABLE`/`DO` is *not* unreadable;
            # the floor is on everything the scanner saw, so this cannot misfire on a
            # corrective revision.
            violations.append(
                f"{module_directory.name}: no DDL was recognised in its migrations; the "
                "scanner is not reading them"
            )
    if statements + unmodelled == 0:
        violations.append(
            "no migration DDL was recognised anywhere; the scanner matched nothing"
        )
    return MigrationReport(
        violations=tuple(violations),
        files=files,
        statements=statements,
        unmodelled=unmodelled,
    )


def scan_migration(path: Path, *, module: str, schema: str) -> MigrationScan:
    """Parse one revision and check the DDL text it contains.

    Only *declarations* are checked, per statement, with no cross-revision state: an
    `ALTER TABLE`/`DO` sequence is counted as unmodelled rather than resolved into an end
    state. That is why the checked count cannot credit a corrective revision (see
    `MigrationReport`).

    Two input hazards are handled rather than crashed on: a UTF-8 BOM (legal at the start of
    a Python source file, but not accepted by `ast.parse` on a string) is stripped by reading
    as `utf-8-sig`, and a file that cannot be parsed at all is **reported** -- a migration
    nobody can read is a finding, not a reason for the whole scan to fail.
    """
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return MigrationScan(
            statements=0,
            unmodelled=0,
            violations=(f"{module}/{path.name}: could not be parsed ({error.msg})",),
        )
    constants = _module_string_constants(tree)
    location = _Location(module=module, file_name=path.name, schema=schema)
    violations: list[str] = []
    statements = 0
    unmodelled = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node)
        if call == "op.execute":
            sql = _execute_text(node, constants)
            if sql is None:
                continue
            found, count = _check_sql(sql, location)
            violations.extend(found)
            statements += count
            unmodelled += _count_unmodelled(sql)
        elif call == "op.create_table":
            found, count = _check_create_table_call(node, constants, location)
            violations.extend(found)
            statements += count
        elif call == "op.create_index":
            found, count = _check_create_index_call(node, constants, location)
            violations.extend(found)
            statements += count
        elif call == "op.add_column":
            found = _check_add_column_call(node, constants, location)
            violations.extend(found)
            statements += 1
        elif call in _NAMED_CONSTRAINT_OPERATIONS:
            prefix = _NAMED_CONSTRAINT_OPERATIONS[call]
            name = _string_of(node.args[0], constants) if node.args else None
            if name is not None and not name.startswith(prefix):
                violations.append(
                    location.violation(
                        name, f"constraint name must start with {prefix!r} (the standard's "
                        "naming rule for the object it creates)"
                    )
                )
            statements += 1
    return MigrationScan(
        statements=statements, unmodelled=unmodelled, violations=tuple(violations)
    )


def _count_unmodelled(sql: str) -> int:
    """Count statements this scope sees but does not model (`ALTER`, `DROP`, `DO`, ...).

    Counted on the masked text so a keyword inside a string literal or a comment is not a
    statement, and reported next to the checked count so "0 statements changed" cannot be
    read as "the corrective revision did nothing".
    """
    return len(_UNMODELLED_STATEMENT.findall(_mask_literals(sql)))


# ------------------------------------------------------------------------------ SQL text


def _check_sql(sql: str, location: _Location) -> tuple[list[str], int]:
    violations: list[str] = []
    statements = 0
    masked = _mask_literals(sql)

    for match in _CREATE_TABLE.finditer(masked):
        statements += 1
        body = _parenthesised(masked, match.end() - 1)
        table_schema, table = _split_name(match.group("name"))
        violations.extend(_check_table_scope(location, table_schema, table, "CREATE TABLE"))
        violations.extend(_check_table_body(location, table, body or ""))

    for match in _CREATE_INDEX.finditer(masked):
        statements += 1
        index_name = match.group("name")
        table_schema, table = _split_name(match.group("table"))
        violations.extend(_check_table_scope(location, table_schema, table, "CREATE INDEX"))
        violations.extend(
            _check_index_name(location, index_name, table, unique=bool(match.group("unique")))
        )

    for match in _CREATE_VIEW.finditer(masked):
        statements += 1
        view_schema, view = _split_name(match.group("name"))
        violations.extend(_check_table_scope(location, view_schema, view, "CREATE VIEW"))
        # A view may only read what the module owns; the rule is about the reference, not
        # about the view's own name. The scan stops at the end of the statement so a later
        # statement's `FROM` cannot be reported against this view.
        statement = masked[match.end() : _statement_end(masked, match.end())]
        for source in _SELECT_SOURCE.finditer(statement):
            source_schema, source_table = _split_name(source.group("name"))
            if _is_unknown(source_schema) or source_schema == location.schema:
                continue
            violations.append(
                location.violation(
                    view,
                    f"view reads {source_schema}.{source_table}; cross-schema views "
                    "are forbidden",
                )
            )
    return violations, statements


def _statement_end(text: str, index: int) -> int:
    """Index of the `;` that terminates the statement starting near `index`."""
    terminator = text.find(";", index)
    return len(text) if terminator < 0 else terminator


def _check_table_body(location: _Location, table: str, body: str) -> list[str]:
    """Check one `CREATE TABLE` body.

    The primary key is validated **once**, after every item has been read: whether it is
    inline on a column, an unnamed table constraint or a named one, and whether or not the
    digest/enum check that an archetype requires appears later in the same body.
    """
    violations = _check_table_name(location, table)
    items = _top_level_items(body)
    column_types: dict[str, str] = {}
    checks: list[tuple[str, str]] = []
    primary_key: list[str] = []
    primary_key_name: str | None = None
    primary_key_declared = False
    for item in items:
        constraint = _CONSTRAINT.match(item)
        if constraint is not None:
            name = constraint.group("name")
            kind = _normalized_kind(constraint.group("kind"))
            rest = constraint.group("rest")
            if kind == "PRIMARY KEY":
                primary_key = _column_list(rest)
                primary_key_name = name
                primary_key_declared = True
                expected = f"{PK_PREFIX}{table}"
                if name != expected:
                    violations.append(
                        location.violation(
                            table,
                            f"primary key constraint is named {name!r}, expected {expected!r}",
                        )
                    )
            elif kind == "CHECK":
                checks.append((name, rest))
                if not name.startswith(f"{CHECK_PREFIX}{table}_"):
                    violations.append(
                        location.violation(
                            table,
                            f"check constraint {name!r} does not start with 'ck_{table}_'",
                        )
                    )
            else:
                violations.extend(_check_named_constraint(location, table, name, kind, rest))
            continue
        unnamed = _UNNAMED_CONSTRAINT.match(item)
        if unnamed is not None:
            kind = _normalized_kind(unnamed.group("kind"))
            rest = unnamed.group("rest")
            if kind == "PRIMARY KEY":
                primary_key = _column_list(rest)
                primary_key_name = None
                primary_key_declared = True
            else:
                if kind == "CHECK":
                    checks.append(("", rest))
                violations.extend(_check_unnamed_constraint(location, table, kind, rest))
            continue
        name, type_text, modifiers = _split_column(item)
        if name is None:
            continue
        column_types[name] = type_text or ""
        violations.extend(_check_column(location, table, name, type_text))
        if "PRIMARY KEY" in modifiers.upper():
            primary_key = [name]
            primary_key_name = None
            primary_key_declared = True

    if primary_key_declared:
        if primary_key_name is None:
            # A column-level `PRIMARY KEY` is an unnamed constraint, and PostgreSQL names it
            # `<table>_pkey`; the standard requires `pk_<table>` (table-standards.md §1).
            violations.append(
                location.violation(
                    table, f"primary key is unnamed; the standard requires 'pk_{table}'"
                )
            )
        violations.extend(
            _validate_primary_key(
                location,
                table,
                primary_key,
                column_types,
                [definition for _name, definition in checks],
            )
        )
    return violations


def _normalized_kind(kind: str) -> str:
    return " ".join(kind.upper().split())


def _validate_primary_key(
    location: _Location,
    table: str,
    columns: list[str],
    column_types: dict[str, str],
    check_definitions: list[str],
) -> list[str]:
    """The surrogate rule, or the declared archetype's preconditions when one is declared."""
    natural = NATURAL_KEYS.get((location.module, table))
    if natural is not None:
        return [
            location.violation(table, message)
            for message in _check_declared_natural_key(
                table, natural, list(columns), column_types, check_definitions
            )
        ]
    return _check_primary_key_columns(location, table, columns, column_types)


def _check_table_name(location: _Location, table: str) -> list[str]:
    if _is_unknown(table):
        return []
    violations: list[str] = []
    if not SNAKE_CASE.match(table):
        violations.append(
            location.violation(table, "table name is not lower snake_case")
        )
    elif not table.endswith("s") and (location.module, table) not in NON_PLURAL_TABLE_NAMES:
        # Declared names are correct as written (outbox/inbox are not count nouns,
        # "evidence" is a mass noun); everything else must be plural.
        violations.append(
            location.violation(
                table, "table name is not plural; the standard requires plural snake_case"
            )
        )
    return violations


def _check_table_scope(
    location: _Location, schema: str | None, table: str, statement: str
) -> list[str]:
    if schema is None or schema == location.schema or _is_unknown(schema):
        return []
    return [
        location.violation(
            table,
            f"{statement} targets schema {schema!r}; a module owns only {location.schema!r}",
        )
    ]


def _check_named_constraint(
    location: _Location, table: str, name: str, kind: str, rest: str
) -> list[str]:
    """Name rules for the constraint kinds the table body does not handle itself.

    `PRIMARY KEY` is deliberately absent: the key's *shape* is decided once per table, after
    the whole body is read, so an archetype's digest/enum check may appear after the key.
    """
    violations: list[str] = []
    normalized = _normalized_kind(kind)
    if normalized == "UNIQUE":
        if not name.startswith(f"uq_{table}_"):
            violations.append(
                location.violation(
                    table, f"unique constraint {name!r} does not start with 'uq_{table}_'"
                )
            )
    elif normalized == "CHECK":
        if not name.startswith(f"{CHECK_PREFIX}{table}_"):
            violations.append(
                location.violation(
                    table, f"check constraint {name!r} does not start with 'ck_{table}_'"
                )
            )
    elif normalized == "FOREIGN KEY":
        violations.extend(_check_foreign_key(location, table, name, rest))
    return violations


def _check_unnamed_constraint(
    location: _Location, table: str, kind: str, rest: str
) -> list[str]:
    """Name rules for unnamed constraints; the primary key's shape is checked separately."""
    normalized = _normalized_kind(kind)
    if normalized == "UNIQUE":
        return [
            location.violation(
                table, f"unique constraint is unnamed; the standard requires 'uq_{table}_<cols>'"
            )
        ]
    if normalized == "CHECK":
        return [
            location.violation(
                table, f"check constraint is unnamed; the standard requires 'ck_{table}_<rule>'"
            )
        ]
    if normalized == "FOREIGN KEY":
        return _check_foreign_key(location, table, None, rest)
    return []


def _check_foreign_key(
    location: _Location, table: str, name: str | None, rest: str
) -> list[str]:
    violations: list[str] = []
    if name is None:
        violations.append(
            location.violation(
                table, f"foreign key is unnamed; the standard requires 'fk_{table}_<ref>'"
            )
        )
    elif not name.startswith(f"{FK_PREFIX}{table}_"):
        violations.append(
            location.violation(
                table, f"foreign key {name!r} does not start with 'fk_{table}_'"
            )
        )
    reference = _REFERENCES.search(rest)
    if reference is not None:
        reference_schema, reference_table = _split_name(reference.group("name"))
        if (
            reference_schema is not None
            and not _is_unknown(reference_schema)
            and reference_schema != location.schema
        ):
            violations.append(
                location.violation(
                    table,
                    f"foreign key references {reference_schema}.{reference_table}; "
                    "cross-schema foreign keys are forbidden",
                )
            )
    return violations


def _module_for_schema(schema: str) -> str:
    """`b3` -> `B3`: the module a schema belongs to, as `module.yaml` declares the pair.

    Every manifest names the schema in lower case after its module (`B3` owns `b3`), which is
    what makes the archetype declarations addressable without reading five manifests.
    """
    return schema.upper()


def _primary_key_shape_messages(
    columns: list[str], column_types: dict[str, str]
) -> list[str]:
    """`<entity>_id uuid`: one column, named like an entity key, typed uuid.

    Bare messages, shared by both scopes: the catalog mode prefixes them with the table and
    the migration mode with the file, so the rule itself is written once. It is also what a
    schema without a declared entity map is checked against (see `check_table_standards`).
    """
    if len(columns) != 1:
        return [
            f"primary key is {columns}; the standard requires a single '<entity>_id' column"
        ]
    column = columns[0]
    violations: list[str] = []
    if not column.endswith("_id"):
        violations.append(f"primary key column {column!r} does not end in '_id'")
    type_text = column_types.get(column)
    if type_text is not None and type_text.lower() != "uuid":
        violations.append(f"primary key column {column!r} is {type_text}, expected uuid")
    return violations


def _check_primary_key_columns(
    location: _Location,
    table: str,
    columns: list[str],
    column_types: dict[str, str],
) -> list[str]:
    return [
        location.violation(table, message)
        for message in _primary_key_shape_messages(columns, column_types)
    ]


def _check_column(
    location: _Location, table: str, name: str, type_text: str | None
) -> list[str]:
    """Column-name and timestamp rules for one column.

    The timestamp rules are the standard's §3/§8: **every** time column is `timestamptz(3)`,
    not just `created_at`/`updated_at`. The catalog scope has always checked every timestamp
    column; this scope used to check precision only on the two named ones, so the same schema
    got two different verdicts depending on the entry point (B3 measured exactly that). An
    under-checking gate is worse than no gate, because it reads as coverage.
    """
    violations: list[str] = []
    if not SNAKE_CASE.match(name):
        violations.append(
            location.violation(f"{table}.{name}", "column name is not lower snake_case")
        )
    if type_text is None:
        return violations
    shape = _timestamp_shape(type_text)
    if shape is None:
        if name in _TIME_COLUMNS:
            # These two are named by the standard, so a non-time type is reported even when
            # the type is not timestamp-shaped at all (`created_at text`).
            violations.append(
                location.violation(
                    f"{table}.{name}", f"is {type_text}, the standard requires timestamptz(3)"
                )
            )
        return violations
    has_timezone, precision = shape
    if not has_timezone:
        violations.append(
            location.violation(
                f"{table}.{name}",
                f"is {type_text} without time zone; the standard requires timestamptz(3)",
            )
        )
    elif precision != 3:
        violations.append(
            location.violation(
                f"{table}.{name}",
                f"declares {type_text} without millisecond precision; the standard "
                "requires timestamptz(3)",
            )
        )
    return violations


def _timestamp_shape(type_text: str) -> tuple[bool, int | None] | None:
    """`(has_time_zone, precision)` for a timestamp spelling, or None for another type.

    Normalising here is what makes the rule independent of how a migration spells the same
    type: `timestamptz(3)`, `timestamp(3) with time zone` and `timestamp without time zone`
    all have to reach the same verdict.
    """
    match = _TIMESTAMP_SHAPE.match(type_text.strip())
    if match is None:
        return None
    base = match.group("base").lower()
    zone = (match.group("zone") or "").strip().lower()
    if base == "timestamptz":
        has_timezone = True
    elif zone.startswith("without"):
        has_timezone = False
    else:
        has_timezone = bool(zone)
    precision = match.group("precision")
    return has_timezone, int(precision) if precision else None


def _check_index_name(
    location: _Location, name: str, table: str, *, unique: bool
) -> list[str]:
    if _is_unknown(table):
        return []
    prefixes = (f"uq_{table}_",) if unique else (f"ix_{table}_", f"uq_{table}_")
    if name.startswith(prefixes):
        return []
    expected = "uq_<table>_<cols>" if unique else "ix_<table>_<cols>"
    return [
        location.violation(
            table, f"index {name!r} does not start with {prefixes[0]!r} ({expected})"
        )
    ]


# --------------------------------------------------------------- Python-API DDL (sa.*)


def _check_create_table_call(
    node: ast.Call, constants: dict[str, str], location: _Location
) -> tuple[list[str], int]:
    violations: list[str] = []
    table = _string_of(node.args[0], constants) if node.args else None
    if table is None:
        return [], 0
    schema = _string_of(_keyword(node, "schema"), constants)
    violations.extend(_check_table_scope(location, schema, table, "create_table"))
    violations.extend(_check_table_name(location, table))
    column_types: dict[str, str] = {}
    check_definitions: list[str] = []
    primary_key: list[str] | None = None
    primary_key_named = True
    for item in node.args[1:]:
        if not isinstance(item, ast.Call):
            continue
        constructor = (_call_name(item) or "").rsplit(".", 1)[-1]
        if constructor == "Column":
            name = _string_of(item.args[0], constants) if item.args else None
            if name is None:
                continue
            type_text = _type_text(item.args[1]) if len(item.args) > 1 else None
            column_types[name] = type_text or ""
            violations.extend(_check_column(location, table, name, type_text))
            if _is_true(_keyword(item, "primary_key")):
                primary_key = [name]
                primary_key_named = False
            for argument in item.args[1:]:
                if isinstance(argument, ast.Call) and (
                    _call_name(argument) or ""
                ).endswith("ForeignKey"):
                    violations.extend(
                        _check_inline_foreign_key(location, table, name, argument, constants)
                    )
        elif constructor == "PrimaryKeyConstraint":
            primary_key = _literal_arguments(item, constants)
            name = _string_of(_keyword(item, "name"), constants)
            primary_key_named = name is not None
            if name is not None and name != f"{PK_PREFIX}{table}":
                violations.append(
                    location.violation(
                        table,
                        f"primary key constraint is named {name!r}, expected "
                        f"'{PK_PREFIX}{table}'",
                    )
                )
        elif constructor == "UniqueConstraint":
            name = _string_of(_keyword(item, "name"), constants)
            if name is None:
                violations.append(
                    location.violation(
                        table,
                        f"unique constraint is unnamed; the standard requires 'uq_{table}_<cols>'",
                    )
                )
            elif not name.startswith(f"uq_{table}_"):
                violations.append(
                    location.violation(
                        table, f"unique constraint {name!r} does not start with 'uq_{table}_'"
                    )
                )
        elif constructor == "ForeignKeyConstraint":
            name = _string_of(_keyword(item, "name"), constants)
            if name is None:
                violations.append(
                    location.violation(
                        table, f"foreign key is unnamed; the standard requires 'fk_{table}_<ref>'"
                    )
                )
            elif not name.startswith(f"{FK_PREFIX}{table}_"):
                violations.append(
                    location.violation(
                        table, f"foreign key {name!r} does not start with 'fk_{table}_'"
                    )
                )
            for reference in _literal_arguments(item, constants, position=1):
                reference_schema = reference.split(".")[0] if "." in reference else None
                if (
                    reference_schema is not None
                    and not _is_unknown(reference_schema)
                    and reference_schema != location.schema
                ):
                    violations.append(
                        location.violation(
                            table,
                            f"foreign key references {reference}; cross-schema foreign "
                            "keys are forbidden",
                        )
                    )
        elif constructor == "CheckConstraint":
            expression = _string_of(item.args[0], constants) if item.args else None
            if expression is not None:
                check_definitions.append(expression)
            name = _string_of(_keyword(item, "name"), constants)
            if name is None:
                violations.append(
                    location.violation(
                        table,
                        f"check constraint is unnamed; the standard requires 'ck_{table}_<rule>'",
                    )
                )
            elif not name.startswith(f"{CHECK_PREFIX}{table}_"):
                violations.append(
                    location.violation(
                        table, f"check constraint {name!r} does not start with 'ck_{table}_'"
                    )
                )
    if primary_key is not None:
        if not primary_key_named:
            violations.append(
                location.violation(
                    table, f"primary key is unnamed; the standard requires 'pk_{table}'"
                )
            )
        violations.extend(
            _validate_primary_key(location, table, primary_key, column_types, check_definitions)
        )
    return violations, 1


def _check_inline_foreign_key(
    location: _Location,
    table: str,
    column: str,
    node: ast.Call,
    constants: dict[str, str],
) -> list[str]:
    violations = [
        location.violation(
            f"{table}.{column}",
            f"declares an unnamed foreign key; the standard requires 'fk_{table}_<ref>'",
        )
    ]
    if not node.args:
        return violations
    reference = _string_of(node.args[0], constants)
    if reference is None:
        return violations
    reference_schema = reference.split(".")[0] if "." in reference else None
    if (
        reference_schema is not None
        and not _is_unknown(reference_schema)
        and reference_schema != location.schema
    ):
        violations.append(
            location.violation(
                table,
                f"foreign key references {reference}; cross-schema foreign keys are forbidden",
            )
        )
    return violations


def _check_create_index_call(
    node: ast.Call, constants: dict[str, str], location: _Location
) -> tuple[list[str], int]:
    name = _string_of(node.args[0], constants) if node.args else None
    table = _string_of(node.args[1], constants) if len(node.args) > 1 else None
    if name is None or table is None:
        return [], 0
    schema = _string_of(_keyword(node, "schema"), constants)
    violations = _check_table_scope(location, schema, table, "create_index")
    unique = _is_true(_keyword(node, "unique"))
    violations.extend(_check_index_name(location, name, table, unique=unique))
    return violations, 1


def _check_add_column_call(
    node: ast.Call, constants: dict[str, str], location: _Location
) -> list[str]:
    table = _string_of(node.args[0], constants) if node.args else None
    if table is None or len(node.args) < 2:
        return []
    column = node.args[1]
    if not isinstance(column, ast.Call):
        return []
    name = _string_of(column.args[0], constants) if column.args else None
    if name is None:
        return []
    type_text = _type_text(column.args[1]) if len(column.args) > 1 else None
    return _check_column(location, table, name, type_text)


# ------------------------------------------------------------------------------ AST bits


def _call_name(node: ast.Call) -> str | None:
    parts: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "literal"` assignments, so `f"CREATE TABLE {SCHEMA}.x"` reads.

    Migrations spell the schema as a constant (`SCHEMA = "b4"`); resolving those here keeps
    the scanner from having to guess which schema an unresolvable placeholder meant.
    """
    constants: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        value = statement.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    return constants


def _execute_text(node: ast.Call, constants: dict[str, str]) -> str | None:
    """The SQL of `op.execute(...)`, including the `op.execute(sa.text("..."))` spelling."""
    if not node.args:
        return None
    argument = node.args[0]
    if isinstance(argument, ast.Call) and (_call_name(argument) or "").endswith(".text"):
        if not argument.args:
            return None
        argument = argument.args[0]
    return _string_of(argument, constants)


def _string_of(node: ast.expr | None, constants: dict[str, str]) -> str | None:
    """Render a string expression, substituting module constants where they are known.

    Unresolvable f-string placeholders become `<expr>` rather than an empty string, so a
    name built at runtime is visibly unknown instead of silently matching nothing.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, "<expr>")
    if isinstance(node, ast.JoinedStr):
        rendered = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                rendered.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                rendered.append(_string_of(value.value, constants) or "<expr>")
        return "".join(rendered)
    return None


def _literal_arguments(
    node: ast.Call, constants: dict[str, str], position: int = 0
) -> list[str]:
    values: list[str] = []
    collected = 0
    for index, argument in enumerate(node.args):
        if index < position:
            continue
        if isinstance(argument, (ast.List, ast.Tuple)):
            for element in argument.elts:
                rendered = _string_of(element, constants)
                if rendered is not None:
                    values.append(rendered)
            collected += 1
            break
        rendered = _string_of(argument, constants)
        if rendered is None:
            break
        values.append(rendered)
        collected += 1
    return values if collected else []


def _type_text(node: ast.expr | None) -> str | None:
    """Normalise a SQLAlchemy type expression to the SQL spelling the rules are written in."""
    if node is None:
        return None
    if isinstance(node, ast.Call):
        name = (_call_name(node) or "").rsplit(".", 1)[-1].lower()
        if name in {"datetime", "timestamp"}:
            # `precision=` is how the SQLAlchemy spelling expresses `timestamptz(3)`
            # (`postgresql.TIMESTAMP(timezone=True, precision=3)`); without it the rule would
            # be unsatisfiable in that style, which is a bug in the checker, not in the DDL.
            timezone = _keyword(node, "timezone")
            base = "timestamptz" if _is_true(timezone) else "timestamp"
            precision = _keyword(node, "precision")
            if isinstance(precision, ast.Constant) and isinstance(precision.value, int):
                return f"{base}({precision.value})"
            return base
        if name in {"uuid", "uuidtype"}:
            return "uuid"
        return f"{name}()"
    if isinstance(node, ast.Name):
        return node.id.lower()
    return None


def _parenthesised(text: str, index: int) -> str | None:
    """The text inside the parenthesis group that opens at `index`."""
    if index >= len(text) or text[index] != "(":
        return None
    depth = 0
    for position in range(index, len(text)):
        character = text[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return text[index + 1 : position]
    return None


def _top_level_items(body: str) -> list[str]:
    items: list[str] = []
    depth = 0
    current: list[str] = []
    for character in body:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        if character == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return [item for item in items if item]


def _split_column(item: str) -> tuple[str | None, str | None, str]:
    """`name type [modifiers]` -> `(name, type, modifiers)`."""
    parts = item.split(None, 1)
    if len(parts) != 2:
        return None, None, item
    name, remainder = parts
    if not re.match(r"^[A-Za-z_]\w*$", name):
        return None, None, item
    chunks = _TYPE_MODIFIER.split(remainder, maxsplit=1)
    type_text = chunks[0].strip()
    modifiers = chunks[1] if len(chunks) > 1 else ""
    return name, type_text or None, modifiers


def _column_list(rest: str) -> list[str]:
    """The columns of `(a, b)` in a constraint definition."""
    opening = rest.find("(")
    if opening < 0:
        return []
    body = _parenthesised(rest, opening)
    if body is None:
        return []
    return [item.strip().strip('"') for item in body.split(",") if item.strip()]


def _split_name(name: str) -> tuple[str | None, str]:
    """`schema.table` -> `(schema, table)`; an unqualified name has no schema."""
    cleaned = name.replace('"', "")
    if "." in cleaned:
        schema, _, table = cleaned.partition(".")
        return schema, table
    return None, cleaned


def _is_unknown(name: str | None) -> bool:
    """Whether a rendered name is the placeholder for something the scanner cannot resolve."""
    return name is None or name == "<expr>" or "<expr>" in name


def _mask_literals(sql: str) -> str:
    """Blank comments, string literals and dollar-quoted bodies, preserving length.

    The rules are about names and types, while an expression such as `~ '^(a|b)$'` inside a
    CHECK can unbalance the parenthesis scan that finds the end of a table definition. A
    trailing `-- comment` would otherwise be read as part of the *next* column definition,
    hiding the column it names from every rule (that is how `updated_at timestamptz` went
    unchecked until the regression test caught it).
    """
    masked = _STRING_LITERAL.sub(
        lambda match: match.group()[0] + " " * (len(match.group()) - 2) + match.group()[-1],
        sql,
    )
    masked = _DOLLAR_QUOTED.sub(lambda match: " " * len(match.group()), masked)
    masked = _LINE_COMMENT.sub(lambda match: " " * len(match.group()), masked)
    return _BLOCK_COMMENT.sub(lambda match: " " * len(match.group()), masked)


def _declared_schema(module_directory: Path) -> str | None:
    """`module.yaml: database.schema` for a module directory, or None when it declares none."""
    manifest = module_directory / "module.yaml"
    if not manifest.is_file():
        return None
    document = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    database = document.get("database") or {}
    schema = database.get("schema")
    return str(schema) if schema else None


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    load_environment_file()
    parser = argparse.ArgumentParser(
        description="Check docs/database/table-standards.md: live catalog and migration DDL."
    )
    parser.add_argument(
        "--scope",
        choices=("all", "catalog", "migrations"),
        default="all",
        help="catalog = a live schema; migrations = every B*/alembic/versions/*.py",
    )
    parser.add_argument(
        "--schema",
        default=SCHEMA,
        help=(
            "schema for the catalog scope (default b4); any module's schema may be checked, "
            "named as that module's module.yaml: database.schema declares it"
        ),
    )
    arguments = parser.parse_args(argv)

    from ..config import ConfigurationError, Settings, module_root_from_environment

    violations: list[str] = []
    if arguments.scope in ("all", "catalog"):
        from ..db.engine import create_engine_from_settings

        try:
            engine = create_engine_from_settings(Settings.from_environment())
        except ConfigurationError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        try:
            catalog = check_table_standards(engine, arguments.schema)
        finally:
            engine.dispose()
        print(
            f"catalog ({arguments.schema}): {len(catalog)} violation(s) "
            f"[{'declared entity names' if arguments.schema == SCHEMA else 'shape rules only'}]"
        )
        violations.extend(catalog)
    if arguments.scope in ("all", "migrations"):
        report = check_migrations(module_root_from_environment())
        print(
            f"migrations: {report.statements} checked DDL statements in {report.files} files "
            f"(+{report.unmodelled} seen but not modelled: ALTER/DROP/DO/...), "
            f"{len(report.violations)} violation(s)"
        )
        violations.extend(report.violations)

    for violation in violations:
        print(f"VIOLATION: {violation}", file=sys.stderr)
    if violations:
        return 1
    if arguments.scope in ("all", "catalog"):
        print(f"schema {arguments.schema} conforms to docs/database/table-standards.md")
    if arguments.scope == "migrations":
        print("every module's migration DDL conforms to docs/database/table-standards.md")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
