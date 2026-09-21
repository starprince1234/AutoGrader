"""Runtime configuration for B4, with the two startup gates required by the security model.

`RQ-B-4-10` / G-SEC-07 require that the application refuses to run as a database
superuser, and `MAIN/docs/architecture/security.md` §6 requires that a weak default
password is rejected outside local development. Both are enforced here, at the point
where settings become an engine, so a misconfiguration fails the process instead of
silently running with more privilege than intended.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from autograder_platform.contracts import deployment_root
from sqlalchemy import Engine, text

#: Placeholder passwords that are obviously unusable in production (`.env.example`).
WEAK_PASSWORDS = frozenset(
    {
        "change-me",
        "change-me-local-only",
        "changeme",
        "password",
        "postgres",
        "secret",
        "",
    }
)

DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 900
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 14 * 24 * 3600
DEFAULT_ROSTER_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOGIN_FAILURE_WINDOW_SECONDS = 300
DEFAULT_LOGIN_FAILURE_LIMIT = 5


class ConfigurationError(RuntimeError):
    """Raised when the environment cannot safely boot the module."""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive, got {value}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable view of the environment B4 needs."""

    environment: str
    database_url: str
    nats_url: str
    jwt_private_key_file: Path | None
    jwt_issuer: str
    jwt_audience: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    roster_upload_max_bytes: int
    login_failure_window_seconds: int
    login_failure_limit: int
    module_root: Path
    refresh_cookie_name: str
    refresh_cookie_secure: bool
    allow_weak_password: bool

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> Settings:
        source = dict(os.environ if environ is None else environ)
        environment = (source.get("B4_ENV") or "development").strip().lower()
        database_url = (source.get("DATABASE_URL") or "").strip()
        if not database_url:
            raise ConfigurationError("DATABASE_URL is required (least-privilege application role)")
        key_file = (source.get("JWT_PRIVATE_KEY_FILE") or "").strip()
        settings = cls(
            environment=environment,
            database_url=database_url,
            nats_url=(source.get("NATS_URL") or "nats://localhost:4222").strip(),
            jwt_private_key_file=Path(key_file) if key_file else None,
            jwt_issuer=(source.get("B4_JWT_ISSUER") or "autograder-b4").strip(),
            jwt_audience=(source.get("B4_JWT_AUDIENCE") or "autograder").strip(),
            access_token_ttl_seconds=_env_int(
                "B4_ACCESS_TOKEN_TTL_SECONDS", DEFAULT_ACCESS_TOKEN_TTL_SECONDS
            ),
            refresh_token_ttl_seconds=_env_int(
                "B4_REFRESH_TOKEN_TTL_SECONDS", DEFAULT_REFRESH_TOKEN_TTL_SECONDS
            ),
            roster_upload_max_bytes=_env_int(
                "B4_ROSTER_UPLOAD_MAX_BYTES", DEFAULT_ROSTER_UPLOAD_MAX_BYTES
            ),
            login_failure_window_seconds=_env_int(
                "B4_LOGIN_FAILURE_WINDOW_SECONDS", DEFAULT_LOGIN_FAILURE_WINDOW_SECONDS
            ),
            login_failure_limit=_env_int("B4_LOGIN_FAILURE_LIMIT", DEFAULT_LOGIN_FAILURE_LIMIT),
            module_root=module_root_from_environment(source),
            refresh_cookie_name=(
                source.get("B4_REFRESH_COOKIE_NAME") or "autograder_refresh"
            ).strip(),
            refresh_cookie_secure=_env_bool("B4_REFRESH_COOKIE_SECURE", True),
            allow_weak_password=_env_bool("B4_ALLOW_WEAK_PASSWORD", environment != "production"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Reject configurations that would break a hard rule."""
        if self.is_production:
            if self.jwt_private_key_file is None:
                raise ConfigurationError("production requires JWT_PRIVATE_KEY_FILE")
            if self.allow_weak_password:
                raise ConfigurationError("production must not allow weak default passwords")
        password = _password_from_url(self.database_url)
        if (
            password is not None
            and password.lower() in WEAK_PASSWORDS
            and not self.allow_weak_password
        ):
            raise ConfigurationError(
                "DATABASE_URL carries a weak default password; set a real password or run "
                "with B4_ENV=development"
            )
        if (
            self.is_production
            and self.jwt_private_key_file is not None
            and not self.jwt_private_key_file.is_file()
        ):
            raise ConfigurationError(
                f"JWT_PRIVATE_KEY_FILE does not exist: {self.jwt_private_key_file}"
            )


def _password_from_url(database_url: str) -> str | None:
    """Extract the password from a SQLAlchemy URL without logging the URL."""
    from sqlalchemy.engine import make_url

    try:
        return make_url(database_url).password
    except Exception:  # noqa: BLE001 - malformed URLs surface later as connection errors
        return None


def _default_module_root() -> str:
    # B4 owns the variable name (`B4_MODULE_ROOT`) but not the derivation: the SDK resolves the
    # same fact for B2 and B5, and going through it is what keeps one answer instead of three.
    # Deriving from `__file__` alone assumed a source checkout -- it is also the fallback when
    # the deployment root genuinely cannot be identified, so the error a caller then reports
    # names a path a reader can compare against reality.
    resolved = deployment_root()
    if resolved is not None:
        return str(resolved)
    # src/autograder_b4/config.py -> src/autograder_b4 -> src -> B4 -> repository root
    return str(Path(__file__).resolve().parents[3])


def module_root_from_environment(environ: Mapping[str, str] | None = None) -> Path:
    """The repository root holding `B*/module.yaml`, without requiring a database DSN.

    `Settings` needs `DATABASE_URL`, but the migration linter reads files only and must be
    runnable on a machine with no PostgreSQL at all, so the resolution lives here and both
    callers share it instead of each having its own idea of the root.
    """
    source = os.environ if environ is None else environ
    return Path((source.get("B4_MODULE_ROOT") or "").strip() or _default_module_root()).resolve()


def assert_not_superuser(engine: Engine) -> str:
    """Refuse to serve from a superuser connection (G-SEC-07).

    Returns the role name so callers can log it; raises `ConfigurationError` when the
    connected role is a superuser, can create roles/databases, or bypasses row level
    security. The check is a catalog query, not a configuration claim.
    """
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, "
                "pg_has_role(current_user, 'pg_read_all_data', 'MEMBER') "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).fetchone()
    if row is None:  # pragma: no cover - current_user always exists
        raise ConfigurationError("could not determine the connected database role")
    role, is_super, can_create_db, can_create_role, bypass_rls, read_all = row
    offending = [
        name
        for name, flag in (
            ("rolsuper", is_super),
            ("rolcreatedb", can_create_db),
            ("rolcreaterole", can_create_role),
            ("rolbypassrls", bypass_rls),
            ("pg_read_all_data", read_all),
        )
        if flag
    ]
    if offending:
        raise ConfigurationError(
            f"refusing to start: database role {role!r} is over-privileged "
            f"({', '.join(offending)}); the application must connect as the least-privilege "
            "role created by `python -m autograder_b4.ops.provision`"
        )
    return str(role)
