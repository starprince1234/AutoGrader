"""Least-privilege role and schema provisioning for the `b4` schema (RQ-B-4-10).

This module is the only place in B4 that uses a privileged connection. It is an
*operational* entry point (`python -m autograder_b4.ops.provision`), never part of the
request path, and it is the documented step that precedes `alembic upgrade head`.

Boundary rules enforced here (see docs/runbooks/database-operations.md):

* the provisioned role is a plain LOGIN role: NOSUPERUSER, NOCREATEDB, NOCREATEROLE,
  NOREPLICATION, NOBYPASSRLS;
* it receives privileges on `b4` only -- `b2`/`b3` privileges are explicitly revoked,
  so cross-schema access is denied by the database rather than by convention;
* identifiers and the password are passed as bound/escaped values, never concatenated
  into a command string, and no secret is ever logged.

The role is **cluster-wide**, so its password is shared by every database in the instance:
provisioning a second database must be given the same password, or existing deployments
stop authenticating. The database to grant on is therefore read from the admin DSN, not
from a second variable that could disagree with it.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

import psycopg
from psycopg import sql
from sqlalchemy import Engine, create_engine, text

DEFAULT_SCHEMA = "b4"
FORBIDDEN_SCHEMAS = ("b2", "b3")

ROLE_ATTRIBUTES = (
    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10"
)


@dataclass(frozen=True)
class ProvisionRequest:
    """Everything needed to (re)provision one module role."""

    admin_dsn: str
    role: str
    password: str
    database: str
    schema: str = DEFAULT_SCHEMA


def _statements(request: ProvisionRequest) -> list[tuple[str, sql.Composed]]:
    """Return the ordered (label, statement) pairs of the provisioning script.

    Labels are safe to log; statements contain the password and are not.
    """
    role = sql.Identifier(request.role)
    schema = sql.Identifier(request.schema)
    database = sql.Identifier(request.database)
    steps: list[tuple[str, sql.Composed]] = [
        (
            "create-or-update-role",
            sql.SQL("CREATE ROLE {role} " + ROLE_ATTRIBUTES).format(role=role),
        ),
        (
            "set-role-password",
            sql.SQL("ALTER ROLE {role} PASSWORD {password}").format(
                role=role, password=sql.Literal(request.password)
            ),
        ),
        (
            "harden-role",
            sql.SQL("ALTER ROLE {role} " + ROLE_ATTRIBUTES).format(role=role),
        ),
        ("create-schema", sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(schema=schema)),
        (
            "role-search-path",
            sql.SQL("ALTER ROLE {role} SET search_path = {schema}, pg_catalog").format(
                role=role, schema=schema
            ),
        ),
        (
            "grant-connect",
            sql.SQL("GRANT CONNECT ON DATABASE {database} TO {role}").format(
                database=database, role=role
            ),
        ),
        (
            "grant-schema-usage-and-create",
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA {schema} TO {role}").format(
                schema=schema, role=role
            ),
        ),
        (
            "grant-existing-tables",
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}"
            ).format(schema=schema, role=role),
        ),
        (
            "grant-existing-sequences",
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}").format(
                schema=schema, role=role
            ),
        ),
        (
            "grant-default-tables",
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
            ).format(schema=schema, role=role),
        ),
        (
            "grant-default-sequences",
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                "GRANT USAGE, SELECT ON SEQUENCES TO {role}"
            ).format(schema=schema, role=role),
        ),
    ]
    for forbidden in FORBIDDEN_SCHEMAS:
        steps.append(
            (
                f"revoke-{forbidden}",
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}").format(
                    schema=sql.Identifier(forbidden), role=role
                ),
            )
        )
        steps.append(
            (
                f"revoke-{forbidden}-schema",
                sql.SQL("REVOKE ALL ON SCHEMA {schema} FROM {role}").format(
                    schema=sql.Identifier(forbidden), role=role
                ),
            )
        )
    steps.append(("revoke-public-create", sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC")))
    return steps


def _role_exists(connection: psycopg.Connection, role: str) -> bool:
    row = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    return row is not None


def _schema_exists(connection: psycopg.Connection, schema: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,)
    ).fetchone()
    return row is not None


def _psycopg_connection(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg connection from a SQLAlchemy-style DSN.

    Components are passed as keyword arguments rather than a conninfo string so a
    password containing spaces, quotes or `=` cannot corrupt the connection string.
    """
    from sqlalchemy.engine import make_url

    url = make_url(dsn)
    return psycopg.connect(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        autocommit=autocommit,
    )


def provision(request: ProvisionRequest) -> list[str]:
    """Apply the provisioning script and return the labels of executed steps."""
    applied: list[str] = []
    with _psycopg_connection(request.admin_dsn, autocommit=True) as connection:
        exists = _role_exists(connection, request.role)
        for label, statement in _statements(request):
            if label == "create-or-update-role" and exists:
                applied.append("update-role-attributes")
                continue
            if label in {"revoke-b2", "revoke-b2-schema", "revoke-b3", "revoke-b3-schema"}:
                target = label.split("-")[1]
                if not _schema_exists(connection, target):
                    applied.append(f"skip-revoke-{target}-schema-absent")
                    continue
            connection.execute(statement)
            applied.append(label)
    return applied


def verify_role_is_least_privilege(engine: Engine, role: str, schema: str) -> list[str]:
    """Assert the role cannot reach beyond `schema`; return a list of violations.

    The check runs against the catalog rather than trusting the provisioning script:
    a later `GRANT` by someone else must be detectable. It takes an engine so that the
    operator command and the test suite exercise exactly one implementation, and so that
    a failure is reproducible from either side.
    """
    violations: list[str] = []
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": role},
        ).fetchone()
        if row is None:
            return [f"role {role} does not exist"]
        labels = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")
        for label, value in zip(labels, row, strict=True):
            if value:
                violations.append(f"{role}.{label} is true")

        schemas = {
            row[0]
            for row in connection.execute(
                text("SELECT nspname FROM pg_namespace")
            ).fetchall()
        }
        for other in FORBIDDEN_SCHEMAS:
            # A schema that does not exist in this database cannot be reached at all, so
            # its absence is not a violation. It is also not evidence: the pytest case that
            # proves cross-schema denial asserts the schemas exist first, so the proof can
            # never quietly degrade into "there was nothing to reach".
            if other not in schemas:
                continue
            for privilege in ("USAGE", "CREATE"):
                granted = connection.execute(
                    text("SELECT has_schema_privilege(:role, :schema, :privilege)"),
                    {"role": role, "schema": other, "privilege": privilege},
                ).scalar_one()
                if granted:
                    violations.append(f"{role} has {privilege} on {other}")

        if schema not in schemas:
            violations.append(f"schema {schema} does not exist in this database")
            return violations
        for privilege in ("USAGE", "CREATE"):
            granted = connection.execute(
                text("SELECT has_schema_privilege(:role, :schema, :privilege)"),
                {"role": role, "schema": schema, "privilege": privilege},
            ).scalar_one()
            if not granted:
                violations.append(f"{role} lacks {privilege} on {schema}")
    return violations


def _request_from_environment(arguments: argparse.Namespace) -> ProvisionRequest:
    role = arguments.role or os.getenv("B4_DB_ROLE", "autograder_b4")
    password = arguments.password or os.getenv("B4_DB_PASSWORD", "")
    if not password:
        raise SystemExit("refusing to provision: no password supplied (set B4_DB_PASSWORD)")
    admin_dsn = arguments.admin_dsn or os.getenv("B4_ADMIN_DATABASE_URL", "")
    if not admin_dsn:
        raise SystemExit("refusing to provision: no admin DSN (set B4_ADMIN_DATABASE_URL)")
    # The database is taken from the connection target, never from a second variable:
    # a mismatch there silently grants CONNECT on the wrong database, which is exactly
    # the failure this function used to have.
    from sqlalchemy.engine import make_url

    database = make_url(admin_dsn).database
    if not database:
        raise SystemExit("refusing to provision: the admin DSN does not name a database")
    if arguments.database and arguments.database != database:
        raise SystemExit(
            f"refusing to provision: --database {arguments.database!r} does not match the "
            f"admin DSN target {database!r}"
        )
    return ProvisionRequest(
        admin_dsn=admin_dsn,
        role=role,
        password=password,
        database=database,
        schema=arguments.schema,
    )


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    load_environment_file()
    parser = argparse.ArgumentParser(description="Provision the least-privilege B4 database role.")
    parser.add_argument(
        "--admin-dsn", default=None, help="privileged DSN; defaults to B4_ADMIN_DATABASE_URL"
    )
    parser.add_argument("--role", default=None, help="login role name; defaults to B4_DB_ROLE")
    parser.add_argument(
        "--password", default=None, help="role password; defaults to B4_DB_PASSWORD"
    )
    parser.add_argument(
        "--database", default=None, help="must match the admin DSN target when given"
    )
    parser.add_argument(
        "--schema", default=DEFAULT_SCHEMA, help="owned schema (only b4 is supported)"
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="verify privileges without changing them"
    )
    arguments = parser.parse_args(argv)

    if arguments.schema != DEFAULT_SCHEMA:
        print(f"refusing: B4 owns only the {DEFAULT_SCHEMA} schema", file=sys.stderr)
        return 2
    request = _request_from_environment(arguments)

    if not arguments.verify_only:
        applied = provision(request)
        print(f"provisioned role {request.role} on schema {request.schema}: {len(applied)} steps")

    verification_engine = create_engine(request.admin_dsn)
    try:
        violations = verify_role_is_least_privilege(
            verification_engine, request.role, request.schema
        )
    finally:
        verification_engine.dispose()
    if violations:
        for violation in violations:
            print(f"VIOLATION: {violation}", file=sys.stderr)
        return 1
    print(f"role {request.role} is least privilege: no b2/b3 access, no superuser attributes")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
