"""Engine construction and the dependency probes the readiness endpoint reports on.

`RQ-B-4-10` acceptance requires the probe to be *real*: stopping PostgreSQL must change
the answer, and the detail must name the dependency. A probe that returns a cached
"healthy" would satisfy a screenshot and fail the requirement, so every call performs a
round trip and the failure detail is derived from the driver error rather than a
constant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from ..config import Settings

#: Bounded so the module cannot exhaust the database's connection slots; the role
#: itself is provisioned with CONNECTION LIMIT 10 as a second, independent ceiling.
POOL_SIZE = 5
MAX_OVERFLOW = 2

#: A readiness probe that can block indefinitely is worse than one that reports failure:
#: the endpoint would hang instead of telling an operator that the database is gone.
CONNECT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class DependencyStatus:
    """One dependency's observed state, as reported by health endpoints."""

    name: str
    healthy: bool
    detail: str


def create_engine_from_settings(settings: Settings) -> Engine:
    """Create the application engine. Never logs or embeds the DSN in an error."""
    return create_engine(
        settings.database_url,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def database_health(engine: Engine) -> DependencyStatus:
    """Round-trip probe: the answer changes when PostgreSQL actually goes away."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        return DependencyStatus("postgres", False, f"postgres unavailable: {_safe_error(error)}")
    except OSError as error:
        return DependencyStatus("postgres", False, f"postgres unavailable: {type(error).__name__}")
    return DependencyStatus("postgres", True, "postgres reachable")


def _safe_error(error: BaseException) -> str:
    """Describe a driver error without leaking a DSN, credentials or a stack trace."""
    message = str(getattr(error, "orig", error)).strip()
    message = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^@\s]+@", "", message)
    lowered = message.lower()
    for marker in ("password", "authentication failed", "role ", "database "):
        if marker in lowered:
            return f"{type(error).__name__} (connection rejected)"
    return f"{type(error).__name__}: {message.splitlines()[0][:200]}"


def current_migration_head(engine: Engine) -> str | None:
    """The revision recorded in `b4.alembic_version_b4`, or None before the first migration."""
    with engine.connect() as connection:
        exists = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'b4' AND table_name = 'alembic_version_b4'"
            )
        ).fetchone()
        if exists is None:
            return None
        row = connection.execute(text("SELECT version_num FROM b4.alembic_version_b4")).fetchone()
    return None if row is None else str(row[0])


def declared_migration_head(module_root: Path) -> str:
    """`module.yaml: database.migration_head` for B4 -- the value the head must equal."""
    manifest = yaml.safe_load((module_root / "B4" / "module.yaml").read_text(encoding="utf-8"))
    return str(manifest["database"]["migration_head"])


def migration_head_status(engine: Engine, module_root: Path) -> DependencyStatus:
    """Compare the live Alembic head with the manifest, naming the drift when it differs."""
    try:
        actual = current_migration_head(engine)
    except SQLAlchemyError as error:
        return DependencyStatus(
            "migration", False, f"migration head unreadable: {_safe_error(error)}"
        )
    declared = declared_migration_head(module_root)
    if actual != declared:
        return DependencyStatus(
            "migration",
            False,
            f"migration head drift: database has {actual or 'no revision'}, "
            f"B4/module.yaml declares {declared}",
        )
    return DependencyStatus("migration", True, f"migration head {actual} matches module.yaml")


@lru_cache(maxsize=1)
def alembic_script_head() -> str:
    """The head computed from the migration files themselves.

    Used by the health report and by tests so that "database head == manifest" cannot be
    satisfied by editing the manifest.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one Alembic head, found {sorted(heads)}")
    return str(heads[0])
