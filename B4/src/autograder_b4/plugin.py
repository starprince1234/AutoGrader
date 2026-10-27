"""The B4 platform module: identity, courses, roster import, messaging and the registry.

Wiring order matters and is deliberate:

1. settings are validated (`DATABASE_URL` present, weak passwords refused in production);
2. the signing key is loaded -- without it no token can be issued, so failing here is
   better than serving a login endpoint that cannot work;
3. the database role is checked for over-privilege (G-SEC-07: a superuser connection
   refuses to start);
4. the runtime registry is validated against every `B*/module.yaml`, each `contracts.lock`
   and the live contract digest -- drift raises here, while the host is wiring routers, so
   the process fails to start instead of serving a registry that disagrees with the files.

The superuser check is the one step that tolerates an unreachable database: a transient
outage during a deploy must not permanently prevent the process from booting, and
readiness is what reports that state. The check still fails closed whenever the database
answers.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from autograder_platform import HealthReport, ModuleDescriptor
from fastapi import APIRouter

from .api import B4State, build_api_router
from .config import ConfigurationError, Settings, assert_not_superuser
from .db.engine import create_engine_from_settings
from .logging import configure_logging, get_logger
from .registry import RegistryError, validate_registry
from .security.keys import KeyConfigurationError, load_signing_key

logger = get_logger("plugin")

DESCRIPTOR = ModuleDescriptor(
    module="B4",
    contract_version="0.22.0",
    provides=("b4-auth", "b4-course", "postgres", "nats"),
    requires=("host",),
    routes=(
        "/api/v1/auth",
        "/api/v1/users",
        "/api/v1/courses",
        "/api/v1/sections",
        "/api/v1/enrollments",
        "/api/v1/roster-imports",
        "/api/v1/audit-records",
        "/api/v1/modules",
        "/api/v1/registration-allowlists",
    ),
    events=("autograder.v1.roster.import.completed",),
)


class B4Module:
    """The object the host's entry point returns."""

    descriptor = DESCRIPTOR

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._state: B4State | None = None

    # -- lifecycle ------------------------------------------------------------------

    def router(self, context) -> APIRouter:
        state = self._ensure_state(context)
        return build_api_router(state)

    def health(self, context) -> HealthReport:
        try:
            state = self._ensure_state(context)
        except (ConfigurationError, KeyConfigurationError, RegistryError) as error:
            return HealthReport("B4", False, f"module not initialized: {error}")
        healthy, detail = state.self_health()
        return HealthReport("B4", healthy, detail)

    # -- internals ------------------------------------------------------------------

    def _ensure_state(self, context) -> B4State:
        if self._state is not None:
            return self._state
        settings = self._settings or Settings.from_environment(_context_environ(context))
        configure_logging(logging.INFO if settings.is_production else logging.DEBUG)
        if not settings.smtp_configured:
            # A warning, not a refusal. The registration endpoints answer `503` on their own;
            # stopping the process here would take login and every other endpoint down for a
            # missing optional channel -- a worse failure than the one it tries to prevent.
            logger.warning(
                "module.registration_mail_unconfigured",
                extra={
                    "event": "module.registration_mail_unconfigured",
                    "missing": list(settings.smtp_missing_variables),
                    "effect": "self-service registration returns 503 until these are set",
                },
            )
        signing_key = load_signing_key(settings.jwt_private_key_file)
        engine = create_engine_from_settings(settings)
        _reject_superuser(engine, settings)
        manifests = validate_registry(settings.module_root, loaded=(self,))
        self._state = B4State(
            settings=settings,
            engine=engine,
            signing_key=signing_key,
            manifests=manifests,
            context=context,
        )
        logger.info(
            "module.initialized",
            extra={
                "event": "module.initialized",
                "environment": settings.environment,
                "manifests": sorted(manifests),
                "signing_kid": signing_key.kid,
            },
        )
        return self._state


def _context_environ(context) -> dict[str, str] | None:
    """Return the environment B4 should read: the host's view layered over `os.environ`.

    The composition host gives each module its own view so that B4's `DATABASE_URL`
    resolves from `B4_DATABASE_URL` while B2 and B3 keep theirs (ADR-0008). Reading
    `os.environ` directly ignores that and hands the module whatever the process-wide
    `DATABASE_URL` is -- during the first integrated start that was the platform
    superuser, and the least-privilege guard below correctly refused to boot.

    An **empty** view means "no host" (`tests/test_health.py` constructs exactly that to
    build state outside a host), and until this was spelled out it meant "the environment
    is empty": `Settings.from_environment({})` then reported `DATABASE_URL is required`
    against a perfectly valid environment.
    """
    candidate = getattr(context, "settings", None)
    if not isinstance(candidate, Mapping) or not candidate:
        return None
    return {**os.environ, **candidate}


def _reject_superuser(engine, settings: Settings) -> None:
    """Refuse to run with more database privilege than the module is entitled to."""
    from sqlalchemy.exc import SQLAlchemyError

    try:
        role = assert_not_superuser(engine)
    except SQLAlchemyError as error:
        # Unreachable database: readiness reports it, and every request will fail loudly.
        logger.error(
            "module.database_unreachable",
            extra={"event": "module.database_unreachable", "reason": type(error).__name__},
        )
        return
    logger.info(
        "module.database_role_verified",
        extra={"event": "module.database_role_verified", "role": role},
    )


def create_module(settings: Settings | None = None) -> B4Module:
    """Entry point declared in `module.yaml` and `pyproject.toml`."""
    return B4Module(settings=settings)


def module_root_of(path: Path | None = None) -> Path:
    return (path or Path(__file__).resolve().parents[3]).resolve()
