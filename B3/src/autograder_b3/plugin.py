"""DSH/MAIN plugin entry point.

``B3Module`` implements the platform SDK protocol (``autograder_platform``):
a descriptor, a router factory and a health report. The router is the real question
bank API - see ``api.router`` for the path set and ``docs/api/contract.md`` for its
relation to the frozen contract.

Known, recorded SDK gap (``docs/architecture/overview.md`` §已知缺口 1):
``ModuleDescriptor.events`` is a flat tuple with no way to express
publishes/subscribes. MAIN ruled (2026-09-16) that until the SDK is split, the
descriptor must list the **union** of both directions, because
``autograder_b4.registry.compare_descriptor_to_manifest`` compares it against
``module.yaml``'s publishes ∪ subscribes and treats any difference as drift that
aborts host startup. Listing only the published subject therefore made the host fail
to boot with "descriptor.events omits [...evaluation.completed]" -- a real start-up
blocker, not a cosmetic mismatch. ``module.yaml`` stays the authority for which
direction each subject belongs to.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter
from autograder_platform import HealthReport, ModuleDescriptor

from .api.app import health_detail, install_error_handlers, install_request_logging
from .api.deps import build_dependencies, set_default_dependencies
from .api.router import router as api_router
from .config import Settings
from .events.consumer import EvaluationCompletedConsumer, NatsEvaluationConsumer
from .logging import configure_logging, get_logger, log_event


class B3Module:
    descriptor = ModuleDescriptor(
        module="B3",
        contract_version="0.22.0",
        provides=("b3-questions",),
        requires=("host", "b4-course"),
        routes=(
            "/api/v1/questions",
            "/api/v1/question-versions",
            "/api/v1/question-draft-generations",
            "/api/v1/assignments",
        ),
        events=(
            # publishes (module.yaml: events.publishes)
            "autograder.v1.question.version.published",
            # subscribes (module.yaml: events.subscribes) -- see the module docstring
            # for why the flat descriptor has to carry both directions.
            "autograder.v1.evaluation.completed",
        ),
    )

    def router(self, context) -> APIRouter:
        settings = _settings_from(context)
        configure_logging(settings.log_level)
        dependencies = build_dependencies(settings)
        set_default_dependencies(dependencies)
        self._dependencies = dependencies
        log_event(
            get_logger("plugin"),
            "module.router.ready",
            outcome="question-bank",
        )
        return api_router

    def health(self, context) -> HealthReport:
        settings = _settings_from(context)
        dependencies = getattr(self, "_dependencies", None)
        if dependencies is None:
            dependencies = build_dependencies(settings)
        try:
            dependencies.database.ping()
        except Exception as error:  # noqa: BLE001 - health must report, not raise
            log_event(
                get_logger("plugin"),
                "module.health.degraded",
                level=40,
                reason=type(error).__name__,
            )
            return HealthReport("B3", False, f"database unavailable ({type(error).__name__})")
        return HealthReport("B3", True, health_detail(dependencies))

    def configure_app(self, app, context) -> None:
        """Install B3's RFC 9457 handlers (and request logging) on the host application.

        A FastAPI router cannot register exception handlers, so without this hook B3's
        domain errors escape untranslated: ``Unauthenticated`` surfaced as a bare 500 on
        the first integrated start instead of the contract's 401.
        """
        install_error_handlers(app)
        install_request_logging(app)

    # --- optional background work ----------------------------------------

    def create_event_consumer(self, context) -> EvaluationCompletedConsumer:
        """Build the statistics consumer (B4/MAIN runs it as a background task)."""
        settings = _settings_from(context)
        dependencies = getattr(self, "_dependencies", None) or build_dependencies(settings)
        return EvaluationCompletedConsumer(dependencies.database)

    def create_durable_consumer(self, context) -> NatsEvaluationConsumer | None:
        settings = _settings_from(context)
        if not settings.nats_url:
            return None
        dependencies = getattr(self, "_dependencies", None) or build_dependencies(settings)
        return NatsEvaluationConsumer(
            EvaluationCompletedConsumer(dependencies.database),
            nats_url=settings.nats_url,
            durable_name=settings.consumer_name,
        )


def _settings_from(context) -> Settings:
    """Build settings from the *host-supplied* view when there is one.

    The composition host hands each module its own settings mapping so that this
    module's `DATABASE_URL` is `B3_DATABASE_URL` (and likewise for NATS), while other
    modules keep theirs (ADR-0008). Reading `os.environ` directly would ignore that and
    silently connect as whatever the process-wide `DATABASE_URL` points at -- during the
    first integrated start that was the platform superuser, which B4's privilege guard
    rejected. A standalone B3 (no context) still reads the environment.
    """
    candidate = getattr(context, "settings", None)
    if isinstance(candidate, Settings):
        return candidate
    if isinstance(candidate, Mapping):
        return Settings.from_env(candidate)
    return Settings.from_env()


def create_module() -> B3Module:
    return B3Module()


# The host installs its own handlers; when B3's router is mounted into a host app
# these helpers are applied to that app by the host. Exported so an embedder can
# reuse exactly the same surface, and used by api.create_app in tests.
__all__ = [
    "B3Module",
    "create_module",
    "install_error_handlers",
    "install_request_logging",
]
