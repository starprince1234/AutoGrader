"""Runtime configuration for B3.

Everything comes from environment variables (see ``.env.example``). Two database
URLs are required because the protected content boundary is enforced by
PostgreSQL privileges, not by convention:

* ``DATABASE_URL`` - least-privilege question-bank role. Has **no** grant on
  ``b3.reference_answers`` / ``b3.test_cases`` / ``b3.blobs`` (acceptance 3-10-3).
* ``DATABASE_PROTECTED_URL`` - role that may read those tables. Used only by the
  evaluation-package compile path.

Neither role is a superuser and neither may touch ``b2``/``b4``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from collections.abc import Mapping

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://autograder_b3_app:change-me-app-local-only@localhost:55434/autograder"
)
DEFAULT_PROTECTED_DATABASE_URL = (
    "postgresql+psycopg://autograder_b3_internal:change-me-internal-local-only@localhost:55434/autograder"
)

# Rich-text stem is rendered by B1 with a safe subset. These are the server-side
# guards that keep obviously executable markup out of the stored stem.
STEM_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "<script",
    "<iframe",
    "<object",
    "<embed",
    "javascript:",
    "<style",
)

# Only raster formats: SVG can carry script and would be served from B3's origin.
ALLOWED_ASSET_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)
ASSET_MAGIC_PREFIXES: Mapping[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    protected_database_url: str = DEFAULT_PROTECTED_DATABASE_URL
    nats_url: str | None = None
    jwks_url: str | None = None
    jwt_issuer: str = "autograder"
    jwt_audience: str = "autograder-api"
    service_token: str | None = None
    consumer_name: str = "b3-question-statistics"
    log_level: str = "INFO"

    # Limits. All external input is bounded (boundaries.md §5).
    max_stem_chars: int = 20_000
    max_asset_bytes: int = 5 * 1024 * 1024
    max_assets_per_question: int = 20
    max_fixture_bytes: int = 1024 * 1024
    max_fixtures_per_version: int = 50
    max_cases_per_version: int = 200
    max_case_output_chars: int = 65_536
    default_max_files: int = 20
    default_max_total_bytes: int = 1024 * 1024

    #: Authoring assistance (``RQ-T-10``, ADR-0009 decision 3). Absent by default:
    #: ADR-0009 decision 4 requires the model service to be **off** unless a deployment
    #: turns it on and accepts responsibility for what leaves the system.
    authoring_provider_url: str | None = None
    authoring_provider_name: str = "openai-compatible"
    authoring_model: str | None = None
    authoring_api_key: str | None = None
    authoring_timeout_seconds: float = 20.0

    extra: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        def value(name: str, default: str | None) -> str | None:
            raw = source.get(name)
            return default if raw is None or raw == "" else raw

        def seconds(name: str, default: float) -> float:
            """A positive number of seconds, or the default.

            A malformed value fails here rather than at the first model call: the provider
            timeout exists so a slow or hanging endpoint cannot hold a teacher's request, and
            a mistyped value must not silently disable that. The field itself is not new --
            ``authoring_timeout_seconds`` has always existed and defaulted to 20s -- but it was
            read from nowhere, so a reasoning model that routinely needs longer than 20s could
            not be configured at all.
            """
            raw = (source.get(name) or "").strip()
            if not raw:
                return default
            try:
                parsed = float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number of seconds, got {raw!r}") from None
            if parsed <= 0:
                raise ValueError(f"{name} must be positive, got {raw!r}")
            return parsed

        return cls(
            database_url=value("DATABASE_URL", DEFAULT_DATABASE_URL) or DEFAULT_DATABASE_URL,
            protected_database_url=value(
                "DATABASE_PROTECTED_URL", DEFAULT_PROTECTED_DATABASE_URL
            )
            or DEFAULT_PROTECTED_DATABASE_URL,
            nats_url=value("NATS_URL", None),
            jwks_url=value("B4_JWKS_URL", None),
            jwt_issuer=value("B3_JWT_ISSUER", "autograder") or "autograder",
            jwt_audience=value("B3_JWT_AUDIENCE", "autograder-api") or "autograder-api",
            service_token=value("B3_SERVICE_TOKEN", None),
            consumer_name=value("B3_CONSUMER_NAME", "b3-question-statistics")
            or "b3-question-statistics",
            log_level=value("B3_LOG_LEVEL", "INFO") or "INFO",
            authoring_provider_url=value("B3_AUTHORING_PROVIDER_URL", None),
            authoring_provider_name=value("B3_AUTHORING_PROVIDER_NAME", "openai-compatible")
            or "openai-compatible",
            authoring_model=value("B3_AUTHORING_MODEL", None),
            authoring_api_key=value("B3_AUTHORING_API_KEY", None),
            authoring_timeout_seconds=seconds("B3_AUTHORING_TIMEOUT_SECONDS", 20.0),
        )

    @property
    def event_consumption_enabled(self) -> bool:
        """Statistics consumption needs NATS; without it the sync API still works."""
        return bool(self.nats_url)

    @property
    def authoring_assistance_enabled(self) -> bool:
        """Both a provider URL and a model id are needed; either missing means off."""
        return bool(self.authoring_provider_url and self.authoring_model)

    @property
    def authoring_missing_configuration(self) -> tuple[str, ...]:
        """What a deployment must set to turn authoring assistance on, in order."""
        missing = []
        if not self.authoring_provider_url:
            missing.append("B3_AUTHORING_PROVIDER_URL")
        if not self.authoring_model:
            missing.append("B3_AUTHORING_MODEL")
        return tuple(missing)
