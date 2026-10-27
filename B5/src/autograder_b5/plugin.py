"""The plugin entry point the composition host loads.

`B5Module` is the whole runtime contract B5 owes MAIN: a `ModuleDescriptor`, a router
factory, and a health report (SDK: `autograder_platform.protocol`). There is no
module-to-module import anywhere in this package and no import of `autograder_host`; the
only thing B5 knows about its neighbours is what the host puts on
`app.state.modules` -- their SDK descriptors.

The descriptor is a class attribute, built without touching the filesystem or the network,
because `autograder_host.checks.check_module_descriptors()` reads it by calling
`create_module()` and comparing it against `module.yaml` on every gate run. Anything that
could fail there would turn a manifest check into an environment check.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter
from autograder_platform import HealthReport, ModuleDescriptor

from .api import install_error_handlers, router as conformance_router
from .config import Settings, current_settings, set_settings
from .contracts import contract_paths
from .errors import B5Error


class B5Module:
    descriptor = ModuleDescriptor(
        module="B5",
        contract_version="0.19.0",
        provides=("b5-conformance",),
        # `b4-auth` because the contract's endpoint is bearer-protected and B5 verifies the
        # token locally. `b2-submissions` / `b3-questions` because the report is *about* the
        # loaded composition: with those modules absent, every path they own would be
        # reported as unserved, which is a wrong answer rather than a smaller one.
        requires=("host", "b2-submissions", "b3-questions", "b4-auth"),
        routes=("/api/v1/platform",),
        # B5 neither publishes nor subscribes: a report is produced on request and nothing
        # is derived from it. Empty is the true value, not a placeholder.
        events=(),
    )

    def router(self, context) -> APIRouter:
        set_settings(_settings_from(context))
        return conformance_router

    def health(self, context) -> HealthReport:
        """Readiness for a module with no database and no message client.

        B5's only dependency is MAIN's contract tree: if it cannot be read, the module can
        answer nothing, and saying so here is more useful than a hard-coded healthy report.
        No network call is made -- the issuer's JWKS is fetched when a request needs it.
        """
        settings = current_settings()
        try:
            declared = contract_paths(settings.contracts_dir)
        except (B5Error, OSError) as error:
            return HealthReport(
                "B5", False, f"contract tree unreadable ({type(error).__name__})"
            )
        return HealthReport(
            "B5", True, f"{len(declared)} contract paths under {settings.contracts_dir}"
        )

    def configure_app(self, app, context) -> None:
        """Install B5's Problem Details handler on the host application."""
        install_error_handlers(app)


def _settings_from(context) -> Settings:
    """Build settings from the host-supplied view when there is one.

    The composition host hands each module its own settings mapping so that this module's
    `CONTRACTS_DIR` is `B5_CONTRACTS_DIR` while other modules keep theirs (ADR-0008).
    Reading `os.environ` directly would ignore that; a standalone B5 (no context) still
    reads the environment.
    """
    candidate = getattr(context, "settings", None)
    if isinstance(candidate, Mapping):
        return Settings.from_env(candidate)
    return Settings.from_env()


def create_module() -> B5Module:
    return B5Module()


__all__ = ["B5Module", "create_module", "install_error_handlers"]
