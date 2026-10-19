"""The AutoGrader composition host: discovery, capability ordering, and API wiring.

## Why module settings are namespaced

All three backend modules read the *same* generic variable names -- `DATABASE_URL`,
`NATS_URL` -- because each is an independently runnable service. Loaded into one
process, a single shared mapping would hand every module the same database role, and
the per-module least-privilege roles that ADR-0001 relies on ("cross-schema access is
denied by the database, not by convention") would silently collapse into one role.

The host therefore gives each module its **own settings view**: every global variable,
plus any `<MODULE>_`-prefixed variable re-exposed under its unprefixed name. So
`B2_DATABASE_URL` becomes `DATABASE_URL` for B2 only, `B3_DATABASE_URL` for B3 only, and
so on. Modules keep reading plain names and stay independently runnable; the composition
host owns composition. The convention, the alternatives that were rejected (in particular
`setdefault`, which let the platform superuser DSN reach every module) and the empty-view
edge case are recorded in `MAIN/docs/adr/ADR-0008-module-settings-view.md`.
"""

from __future__ import annotations

import importlib.metadata
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from autograder_platform import HealthReport, ModuleContext, ModuleDescriptor, PlatformModule

_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_CORS_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "idempotency-key",
    "last-event-id",
    "traceparent",
    "x-request-id",
]
_CORS_EXPOSE_HEADERS = ["content-disposition", "x-request-id"]


def _cors_origins(environ: Mapping[str, str]) -> list[str]:
    raw = environ.get("AUTOGRADER_CORS_ORIGINS", "")
    origins = list(dict.fromkeys(origin.strip() for origin in raw.split(",") if origin.strip()))
    if not origins and environ.get("B4_ENV", "").strip().lower() == "production":
        raise ValueError("AUTOGRADER_CORS_ORIGINS is required in production")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"invalid CORS origin: {origin!r}")
    return origins


def module_settings(environ: Mapping[str, str], module: str) -> dict[str, str]:
    """Return `environ` with this module's prefixed variables overriding the generic ones.

    `B2_DATABASE_URL` becomes `DATABASE_URL` **for B2 only**; B3 and B4 keep their own.
    The prefixed value must win, not merely fill a gap: with `setdefault` the global
    `DATABASE_URL` (the platform superuser, used for migrations) stayed visible to every
    module, and B4 refused to boot because it detected an over-privileged role -- the
    guard was right and the resolution was wrong. A module with no prefixed variable
    still falls back to the unprefixed global, so single-module deployments keep working.
    """
    settings = dict(environ)
    prefix = f"{module.upper()}_"
    for key, value in environ.items():
        if key.startswith(prefix) and len(key) > len(prefix):
            settings[key[len(prefix) :]] = value
    return settings


@dataclass(frozen=True)
class HostContext(ModuleContext):
    """The context handed to one module. `settings` is already module-scoped."""

    settings: dict[str, str]
    module: str = "host"

    def health(self) -> HealthReport:
        return HealthReport(self.module, True, "composition host is running")


class ModuleLoadError(RuntimeError):
    pass


class _ConfiguredEntryPoint:
    def __init__(self, name: str, module_name: str, function_name: str) -> None:
        self.name = name
        self.module_name = module_name
        self.function_name = function_name

    def load(self):
        module = __import__(self.module_name, fromlist=[self.function_name])
        return getattr(module, self.function_name)


def _load_modules() -> list[PlatformModule]:
    modules: list[PlatformModule] = []
    entries = list(importlib.metadata.entry_points(group="autograder.modules"))
    configured = os.getenv("AUTOGRADER_MODULES", "")
    if configured:
        for spec in configured.split(","):
            module_name, function_name = spec.strip().split(":", 1)
            entries.append(_ConfiguredEntryPoint(module_name, module_name, function_name))
    seen_entries: dict[str, object] = {}
    for entry in entries:
        # Two entry points sharing a name used to resolve *silently* and in opposite
        # directions: this loop kept the last one (dict comprehension), while B4's registry
        # kept the first (`if name not in seen`). An audit scenario showed a duplicate entry
        # point replacing the real B2 descriptor with its own requires/provides/routes and
        # passing startup validation. ADR-0013 decision 5: a duplicate is a loud failure.
        if entry.name in seen_entries:
            raise ModuleLoadError(
                f"duplicate module entry point name {entry.name!r}: declared by both "
                f"{seen_entries[entry.name]!r} and {getattr(entry, 'value', entry)!r}. "
                "Entry point names identify a module, so two of them cannot share one."
            )
        seen_entries[entry.name] = getattr(entry, "value", entry)
        factory = entry.load()
        module = factory()
        if not isinstance(module.descriptor, ModuleDescriptor):
            raise ModuleLoadError(f"{entry.name} did not provide a ModuleDescriptor")
        modules.append(module)
    pending = {module.descriptor.module: module for module in modules}
    ordered: list[PlatformModule] = []
    provided: set[str] = {"host"}
    while pending:
        ready = [module for module in pending.values() if set(module.descriptor.requires) <= provided]
        if not ready:
            missing = {
                name: sorted(set(module.descriptor.requires) - provided)
                for name, module in pending.items()
            }
            raise ModuleLoadError(
                f"module capability dependency cycle or missing capability: {missing}"
            )
        for module in sorted(ready, key=lambda item: item.descriptor.module):
            ordered.append(module)
            provided.update(module.descriptor.provides)
            pending.pop(module.descriptor.module)
    seen_routes: set[str] = set()
    for module in ordered:
        overlap = seen_routes.intersection(module.descriptor.routes)
        if overlap:
            raise ModuleLoadError(f"duplicate module routes: {sorted(overlap)}")
        seen_routes.update(module.descriptor.routes)
    return ordered


def create_app() -> FastAPI:
    environ = dict(os.environ)
    app = FastAPI(title="AutoGrader MAIN Composition Host", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(environ),
        allow_credentials=True,
        allow_methods=_CORS_METHODS,
        allow_headers=_CORS_HEADERS,
        expose_headers=_CORS_EXPOSE_HEADERS,
    )
    modules = _load_modules()
    contexts: dict[str, HostContext] = {
        module.descriptor.module: HostContext(
            settings=module_settings(environ, module.descriptor.module),
            module=module.descriptor.module,
        )
        for module in modules
    }
    app.state.modules = modules
    app.state.contexts = contexts
    for module in modules:
        app.include_router(module.router(contexts[module.descriptor.module]))

    # App-level hooks run after every router is included. Order is the capability order,
    # so it is deterministic; see PlatformModule.configure_app for why a shared exception
    # type is tolerable here (all modules must emit RFC 9457).
    for module in modules:
        configure = getattr(module, "configure_app", None)
        if callable(configure):
            configure(app, contexts[module.descriptor.module])

    @app.get("/health/live", tags=["platform"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["platform"])
    def ready() -> dict[str, object]:
        reports = [module.health(contexts[module.descriptor.module]) for module in modules]
        return {
            "ready": all(report.healthy for report in reports),
            "modules": [report.__dict__ for report in reports],
        }

    return app


app = create_app()
