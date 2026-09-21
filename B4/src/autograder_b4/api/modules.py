"""Runtime module registry API (RQ-B-4-40).

`GET /api/v1/modules` is the full registry and is restricted to administrators: it
enumerates every loaded module, its routes and its capabilities, which is reconnaissance
for an unauthenticated caller. `GET /api/v1/modules/{module}/health` is public and
reports only liveness, because a composition host has to be able to answer "is this
module up?" from outside without exposing the map of the system.

The registry reads the descriptors the host actually loaded (`app.state.modules`) and
compares them with each module's `module.yaml` at read time. A disagreement is reported
as an explicit failure instead of being smoothed into a confident-looking list.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..errors import not_found, service_unavailable
from ..logging import get_logger
from ..registry import (
    RegistryError,
    build_registry,
    compare_descriptor_to_manifest,
)
from .dependencies import B4State, Dependencies, audit_denial
from .problem import ProblemRoute

logger = get_logger("registry.api")

#: The module-name shape is defined once, in `MAIN/contracts/manifest.schema.json` (the
#: design-time authority, ADR-0003); this mirrors that pattern rather than inventing a
#: second spelling. `^B[1-4]$` used to stand here and closed the set, which made "a new
#: module can be added without touching MAIN" unverifiable (CHG-0009 / ADR-0013).
MODULE_PATTERN = "^B[0-9]{1,3}$"


def _loaded_modules(request: Request) -> list[object]:
    modules = getattr(request.app.state, "modules", None)
    if modules is None:
        return []
    return list(modules)


def _health_lookup(state: B4State, request: Request) -> dict[str, tuple[bool, str]]:
    """Ask each loaded module for its health through the context the host handed us."""
    context = state.context
    lookup: dict[str, tuple[bool, str]] = {}
    for module in _loaded_modules(request):
        descriptor = getattr(module, "descriptor", module)
        name = str(getattr(descriptor, "module", "?"))
        if name == "B4":
            report = state.self_health()
            lookup[name] = (report[0], report[1])
            continue
        if context is None:
            lookup[name] = (True, "health not queried")
            continue
        try:
            report = module.health(context)
            lookup[name] = (bool(report.healthy), str(report.detail))
        except Exception as error:  # noqa: BLE001 - a module that cannot report is unhealthy
            lookup[name] = (False, f"health check raised {type(error).__name__}")
    return lookup


def build_modules_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)

    @router.get("/api/v1/modules")
    def list_registered_modules(
        request: Request,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        if not caller.is_admin:
            audit_denial(
                state.engine,
                principal=caller,
                detail="the full module registry is restricted to administrators",
            )
            from ..errors import forbidden

            raise forbidden("The full module registry is visible to administrators only.")
        loaded = _loaded_modules(request)
        try:
            modules = build_registry(
                manifests=state.manifests,
                loaded=loaded,
                health_lookup=_health_lookup(state, request),
            )
        except RegistryError as error:
            # Drift discovered while serving is reported as unavailability naming the
            # module, never as a plausible-looking registry.
            logger.error("registry.drift", extra={"event": "registry.drift", "reason": str(error)})
            raise service_unavailable("module registry", str(error)) from error
        logger.info(
            "registry.read",
            extra={"event": "registry.read", "modules": [item.module for item in modules]},
        )
        return {"modules": [item.as_document() for item in modules]}

    @router.get("/api/v1/modules/{module}/health")
    def get_module_health(module: str, request: Request) -> dict[str, object]:
        loaded = _loaded_modules(request)
        described = {}
        for candidate in loaded:
            descriptor = getattr(candidate, "descriptor", candidate)
            described[str(getattr(descriptor, "module", "?"))] = candidate
        if module not in state.manifests and module not in described:
            raise not_found(f"No module named {module} is part of this composition.")
        if module not in described:
            # Present in the manifests but not loaded by this host: not a running module.
            raise not_found(f"Module {module} is declared but was not loaded by the host.")
        healthy, _detail = _health_lookup(state, request).get(module, (True, ""))
        if not healthy:
            # The public endpoint exposes liveness only; the reason stays in the
            # administrator-facing registry and in the structured log.
            raise service_unavailable(module, "the module reports itself unhealthy")
        return {"module": module, "healthy": True}

    return router


__all__ = ["MODULE_PATTERN", "build_modules_router", "compare_descriptor_to_manifest"]
