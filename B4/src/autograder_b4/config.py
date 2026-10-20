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

#: Self-service registration. The code is short-lived because it is the only proof that the
#: mailbox belongs to the claimant; the resend cooldown keeps one form from becoming a
#: mail-bombing tool, and the hourly limits bound the cost of a burst of registrations.
DEFAULT_REGISTRATION_CODE_TTL_SECONDS = 600
DEFAULT_REGISTRATION_RESEND_SECONDS = 60
DEFAULT_REGISTRATION_MAX_ATTEMPTS = 5
DEFAULT_REGISTRATION_EMAIL_HOURLY_LIMIT = 5
DEFAULT_REGISTRATION_CLIENT_HOURLY_LIMIT = 30
DEFAULT_REGISTRATION_ALLOWLIST_MAX_BYTES = 2 * 1024 * 1024

#: SMTP defaults match the deployment's environment: 465 is implicit TLS (`SMTPS`), which is
#: what a `tls` mode plus port 465 means. `starttls` belongs to 587.
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_TLS_MODE = "tls"
DEFAULT_SMTP_TIMEOUT_SECONDS = 60


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


def _env_str(name: str) -> str:
    raw = os.getenv(name)
    return "" if raw is None else raw.strip()


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
    # Self-service registration and the mail channel it needs. `smtp_*` are read from the
    # same environment Doppler injects; an unconfigured channel is a request-time `503`
    # (`registration code cannot be delivered`), never a silent skip of the verification.
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_from_name: str
    smtp_tls_mode: str
    smtp_timeout_seconds: int
    registration_code_ttl_seconds: int
    registration_resend_seconds: int
    registration_max_attempts: int
    registration_email_hourly_limit: int
    registration_client_hourly_limit: int
    registration_allowlist_max_bytes: int

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def smtp_configured(self) -> bool:
        """Everything needed to deliver a message is present."""
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.smtp_from)

    @property
    def smtp_uses_implicit_tls(self) -> bool:
        """`465`/`smtps` speak TLS from the first byte; STARTTLS is a different handshake."""
        mode = self.smtp_tls_mode.lower()
        if mode in {"ssl", "smtps", "implicit"}:
            return True
        if mode == "tls":
            # `tls` on 465 means implicit TLS (the deployment's own configuration); on any
            # other port it means the STARTTLS upgrade. Guessing the other way would make a
            # correct configuration fail with an opaque handshake error.
            return self.smtp_port == 465
        return False

    @property
    def smtp_uses_starttls(self) -> bool:
        return self.smtp_tls_mode.lower() in {"starttls", "tls"} and not self.smtp_uses_implicit_tls

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
            smtp_host=_env_str("SMTP_HOST"),
            smtp_port=_env_int("SMTP_PORT", DEFAULT_SMTP_PORT),
            smtp_user=_env_str("SMTP_USER"),
            smtp_password=_env_str("SMTP_PASS"),
            smtp_from=_env_str("SMTP_FROM"),
            smtp_from_name=_env_str("SMTP_FROM_NAME") or "AutoGrader",
            smtp_tls_mode=_env_str("SMTP_TLS_MODE").lower() or DEFAULT_SMTP_TLS_MODE,
            smtp_timeout_seconds=_env_int("SMTP_TIMEOUT_SECONDS", DEFAULT_SMTP_TIMEOUT_SECONDS),
            registration_code_ttl_seconds=_env_int(
                "B4_REGISTRATION_CODE_TTL_SECONDS", DEFAULT_REGISTRATION_CODE_TTL_SECONDS
            ),
            registration_resend_seconds=_env_int(
                "B4_REGISTRATION_RESEND_SECONDS", DEFAULT_REGISTRATION_RESEND_SECONDS
            ),
            registration_max_attempts=_env_int(
                "B4_REGISTRATION_MAX_ATTEMPTS", DEFAULT_REGISTRATION_MAX_ATTEMPTS
            ),
            registration_email_hourly_limit=_env_int(
                "B4_REGISTRATION_EMAIL_HOURLY_LIMIT", DEFAULT_REGISTRATION_EMAIL_HOURLY_LIMIT
            ),
            registration_client_hourly_limit=_env_int(
                "B4_REGISTRATION_CLIENT_HOURLY_LIMIT", DEFAULT_REGISTRATION_CLIENT_HOURLY_LIMIT
            ),
            registration_allowlist_max_bytes=_env_int(
                "B4_REGISTRATION_ALLOWLIST_MAX_BYTES", DEFAULT_REGISTRATION_ALLOWLIST_MAX_BYTES
            ),
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
            if not self.smtp_configured:
                missing = [
                    name
                    for name, value in (
                        ("SMTP_HOST", self.smtp_host),
                        ("SMTP_USER", self.smtp_user),
                        ("SMTP_PASS", self.smtp_password),
                        ("SMTP_FROM", self.smtp_from),
                    )
                    if not value
                ]
                raise ConfigurationError(
                    "production requires the self-service registration mail channel; missing "
                    f"{', '.join(missing)}. Registration verifies the mailbox, so a deployment "
                    "without a sender would silently accept unverified addresses."
                )
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
