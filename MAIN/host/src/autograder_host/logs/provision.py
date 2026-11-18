"""Idempotent, non-destructive PostgreSQL provisioning for the ``logs`` schema and roles."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .schema import logs_metadata

DEFAULT_DB_ENV_FILE = Path("/var/lib/autograder/logs/db.env")

LOGS_WRITER_ROLE = "autograder_logs_writer"
LOGS_READER_ROLE = "autograder_logs_reader"
LOGS_OPS_ROLE = "autograder_logs_ops"


def _replace_dsn_credentials(base_dsn: str, username: str, password: str) -> str:
    parsed = urlsplit(base_dsn)
    host_part = parsed.hostname or "127.0.0.1"
    if parsed.port:
        host_part = f"{host_part}:{parsed.port}"
    netloc = f"{username}:{password}@{host_part}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        values[k.strip()] = v.strip().strip('"')
    return values


def resolve_logs_dsn(var_name: str, *, env_file: Path | None = None) -> str | None:
    """Resolve a logs database DSN from environment or the provisioned db.env file."""
    direct = os.environ.get(var_name, "").strip()
    if direct:
        return direct
    target = env_file or Path(os.environ.get("LOGS_DB_ENV_FILE", str(DEFAULT_DB_ENV_FILE)))
    loaded = _load_env_file(target)
    return loaded.get(var_name) or None


def _ensure_role(conn, role_name: str, password: str) -> None:
    exists = conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": role_name}
    ).scalar()
    safe_pw = password.replace("'", "''")
    if not exists:
        conn.execute(
            text(
                f'CREATE ROLE "{role_name}" LOGIN PASSWORD \'{safe_pw}\' '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10"
            )
        )
    else:
        conn.execute(
            text(
                f'ALTER ROLE "{role_name}" PASSWORD \'{safe_pw}\' '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10"
            )
        )


def provision_logs_schema(
    admin_engine: Engine,
    *,
    writer_password: str,
    reader_password: str,
    ops_password: str,
) -> None:
    """Idempotently create ``logs`` schema, tables, indexes, and least-privilege roles."""
    with admin_engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS logs"))
        logs_metadata.create_all(conn)

        _ensure_role(conn, LOGS_WRITER_ROLE, writer_password)
        _ensure_role(conn, LOGS_READER_ROLE, reader_password)
        _ensure_role(conn, LOGS_OPS_ROLE, ops_password)

        # Writer: INSERT only on logs.log_events
        conn.execute(text(f'REVOKE ALL ON SCHEMA logs FROM "{LOGS_WRITER_ROLE}"'))
        conn.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA logs FROM "{LOGS_WRITER_ROLE}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA logs TO "{LOGS_WRITER_ROLE}"'))
        conn.execute(text(f'GRANT INSERT ON logs.log_events TO "{LOGS_WRITER_ROLE}"'))
        conn.execute(text(f'GRANT USAGE, SELECT ON SEQUENCE logs.log_events_log_id_seq TO "{LOGS_WRITER_ROLE}"'))

        # Reader: SELECT only
        conn.execute(text(f'REVOKE ALL ON SCHEMA logs FROM "{LOGS_READER_ROLE}"'))
        conn.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA logs FROM "{LOGS_READER_ROLE}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA logs TO "{LOGS_READER_ROLE}"'))
        conn.execute(text(f'GRANT SELECT ON logs.log_events, logs.log_alert_state TO "{LOGS_READER_ROLE}"'))

        # Ops: retention cleanup (SELECT/DELETE) + alert state (SELECT/INSERT/UPDATE)
        conn.execute(text(f'REVOKE ALL ON SCHEMA logs FROM "{LOGS_OPS_ROLE}"'))
        conn.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA logs FROM "{LOGS_OPS_ROLE}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA logs TO "{LOGS_OPS_ROLE}"'))
        conn.execute(text(f'GRANT SELECT, DELETE ON logs.log_events TO "{LOGS_OPS_ROLE}"'))
        conn.execute(text(f'GRANT SELECT, INSERT, UPDATE ON logs.log_alert_state TO "{LOGS_OPS_ROLE}"'))


def _extract_password(dsn: str | None) -> str | None:
    if not dsn:
        return None
    return urlsplit(dsn).password


def main() -> int:
    admin_dsn = os.environ.get("PLATFORM_DATABASE_URL", "").strip()
    if not admin_dsn:
        raise SystemExit("PLATFORM_DATABASE_URL is required")

    env_file = Path(os.environ.get("LOGS_DB_ENV_FILE", str(DEFAULT_DB_ENV_FILE)))
    existing = _load_env_file(env_file)

    writer_pw = (
        os.environ.get("LOGS_WRITER_DB_PASSWORD", "").strip()
        or _extract_password(os.environ.get("LOGS_WRITER_DATABASE_URL"))
        or _extract_password(existing.get("LOGS_WRITER_DATABASE_URL"))
        or secrets.token_hex(32)
    )
    reader_pw = (
        os.environ.get("LOGS_READER_DB_PASSWORD", "").strip()
        or _extract_password(os.environ.get("LOGS_READER_DATABASE_URL"))
        or _extract_password(existing.get("LOGS_READER_DATABASE_URL"))
        or secrets.token_hex(32)
    )
    ops_pw = (
        os.environ.get("LOGS_OPS_DB_PASSWORD", "").strip()
        or _extract_password(os.environ.get("LOGS_OPS_DATABASE_URL"))
        or _extract_password(existing.get("LOGS_OPS_DATABASE_URL"))
        or secrets.token_hex(32)
    )

    engine = create_engine(admin_dsn, future=True)
    try:
        provision_logs_schema(
            engine,
            writer_password=writer_pw,
            reader_password=reader_pw,
            ops_password=ops_pw,
        )
    finally:
        engine.dispose()

    writer_dsn = _replace_dsn_credentials(admin_dsn, LOGS_WRITER_ROLE, writer_pw)
    reader_dsn = _replace_dsn_credentials(admin_dsn, LOGS_READER_ROLE, reader_pw)
    ops_dsn = _replace_dsn_credentials(admin_dsn, LOGS_OPS_ROLE, ops_pw)

    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'LOGS_WRITER_DATABASE_URL="{writer_dsn}"',
        f'LOGS_READER_DATABASE_URL="{reader_dsn}"',
        f'LOGS_OPS_DATABASE_URL="{ops_dsn}"',
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_file, 0o600)
    except OSError:
        pass
    print(f"[autograder] provisioned logs schema and roles; wrote {env_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
