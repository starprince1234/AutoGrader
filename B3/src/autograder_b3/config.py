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

    #: The model provider, as the deployment injects it: `LLM_BASE_URL`, `LLM_MODEL_NAME`,
    #: `LLM_APIKEY` (and optionally `LLM_TIMEOUT_SECONDS`). Absent by default: ADR-0009
    #: decision 4 requires the model service to be **off** unless a deployment turns it on and
    #: accepts responsibility for what leaves the system. One credential for both AI features
    #: (structured draft generation and prose advice) -- a second one would be a second place
    #: where a key can leak and a second thing an operator has to remember.
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    #: A teacher waits on this synchronously, and the public edge closes the connection at
    #: around 100s, so the ceiling is well under that rather than "as long as it takes".
    llm_timeout_seconds: float = 60.0

    #: AI-FLOW-01A structured draft generation. The **provider** is not configured
    #: separately: generation rides on the ``LLM_*`` block above, so a deployment has one
    #: model credential and one place to turn model access off (``ADR-0009`` decision 4).
    #: What is separate is the *input and retention policy*, because a generation stores a
    #: candidate answer and candidate hidden cases -- content the advice path never
    #: keeps -- so its limits and its lifetime are deployment decisions of their own.
    draft_generation_max_objective_chars: int = 4_000
    draft_generation_max_constraints_chars: int = 2_000
    draft_generation_max_desired_cases: int = 25
    draft_generation_max_proposal_cases: int = 50
    draft_generation_max_text_chars: int = 65_536
    draft_generation_retention_seconds: int = 86_400
    #: Cost guard. Counted per actor over a sliding window from the usage ledger, so it
    #: is enforced by persisted facts rather than by a client-side counter, and it is
    #: configurable because the honest number depends on the provider quota a deployment
    #: actually pays for. ``0`` means "no request budget is enforced here".
    draft_generation_budget_requests: int = 20
    draft_generation_budget_window_seconds: int = 3_600

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
            a mistyped value must not silently disable that. ``LLM_TIMEOUT_SECONDS`` is
            optional -- most deployments never set it and take the default.
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

        def count(name: str, default: int) -> int:
            """A non-negative integer, or the default.

            Same reasoning as :func:`seconds`: a malformed limit fails at start-up rather
            than silently becoming "no limit". ``0`` is a legal value for the request
            budget (it means "this deployment does not cap requests here") and is refused
            for the size limits, where a zero-objective limit could not accept any input.
            """
            raw = (source.get(name) or "").strip()
            if not raw:
                return default
            try:
                parsed = int(raw)
            except ValueError:
                raise ValueError(f"{name} must be an integer, got {raw!r}") from None
            if parsed < 0:
                raise ValueError(f"{name} must be non-negative, got {raw!r}")
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
            llm_base_url=value("LLM_BASE_URL", None),
            llm_model=value("LLM_MODEL_NAME", None),
            llm_api_key=value("LLM_APIKEY", None),
            llm_timeout_seconds=seconds("LLM_TIMEOUT_SECONDS", 60.0),
            draft_generation_max_objective_chars=count(
                "B3_DRAFT_GENERATION_MAX_OBJECTIVE_CHARS", 4_000
            ),
            draft_generation_max_constraints_chars=count(
                "B3_DRAFT_GENERATION_MAX_CONSTRAINTS_CHARS", 2_000
            ),
            draft_generation_max_desired_cases=count(
                "B3_DRAFT_GENERATION_MAX_DESIRED_CASES", 25
            ),
            draft_generation_max_proposal_cases=count(
                "B3_DRAFT_GENERATION_MAX_PROPOSAL_CASES", 50
            ),
            draft_generation_max_text_chars=count(
                "B3_DRAFT_GENERATION_MAX_TEXT_CHARS", 65_536
            ),
            draft_generation_retention_seconds=count(
                "B3_DRAFT_GENERATION_RETENTION_SECONDS", 86_400
            ),
            draft_generation_budget_requests=count(
                "B3_DRAFT_GENERATION_BUDGET_REQUESTS", 20
            ),
            draft_generation_budget_window_seconds=count(
                "B3_DRAFT_GENERATION_BUDGET_WINDOW_SECONDS", 3_600
            ),
        )

    @property
    def event_consumption_enabled(self) -> bool:
        """Statistics consumption needs NATS; without it the sync API still works."""
        return bool(self.nats_url)

    @property
    def llm_configured(self) -> bool:
        """All three values are needed for a real call: base URL, model id and credential.

        A base URL plus a model id but no key would only produce a guaranteed 401 and a
        teacher-facing "生成失败", which reads like a bug rather than a missing setting.
        """
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @property
    def llm_missing_configuration(self) -> tuple[str, ...]:
        """What a deployment must set to turn the model features on, in order."""
        missing = []
        if not self.llm_base_url:
            missing.append("LLM_BASE_URL")
        if not self.llm_model:
            missing.append("LLM_MODEL_NAME")
        if not self.llm_api_key:
            missing.append("LLM_APIKEY")
        return tuple(missing)

    @property
    def draft_generation_enabled(self) -> bool:
        """Structured draft generation rides on the same provider as the advice path.

        Deliberately **not** its own credential: one deployment, one model account, one
        switch to turn model access off. A second credential would mean a second place
        where a key can leak and a second thing an operator has to remember.
        """
        return self.llm_configured

    @property
    def draft_generation_budget_enabled(self) -> bool:
        """``0`` requests means no request budget is enforced by B3 itself."""
        return self.draft_generation_budget_requests > 0 and (
            self.draft_generation_budget_window_seconds > 0
        )
