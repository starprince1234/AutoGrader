from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True)
class HealthReport:
    name: str
    healthy: bool
    detail: str = ""


class ModuleContext(Protocol):
    """Capabilities exposed by the host; modules must not reach into other modules."""

    settings: Any

    def health(self) -> HealthReport: ...


@dataclass(frozen=True)
class ModuleDescriptor:
    module: str
    contract_version: str
    provides: tuple[str, ...] = field(default_factory=tuple)
    requires: tuple[str, ...] = field(default_factory=tuple)
    routes: tuple[str, ...] = field(default_factory=tuple)
    events: tuple[str, ...] = field(default_factory=tuple)


class PlatformModule(Protocol):
    descriptor: ModuleDescriptor

    def router(self, context: ModuleContext) -> APIRouter: ...

    def health(self, context: ModuleContext) -> HealthReport: ...

    def configure_app(self, app: FastAPI, context: ModuleContext) -> None:
        """Optional. Install app-level behaviour this module needs.

        A FastAPI *router* cannot register exception handlers or middleware -- only the
        application can. Without this hook a module has no way to translate its own
        domain errors, so they surface as bare 500s: B3's ``Unauthenticated`` did exactly
        that on the first integrated start (``GET /api/v1/questions`` without a bearer
        token returned 500 instead of 401).

        The host calls this once per module, after including its router, for modules that
        define it. It is declared here so the hook is discoverable; the host uses
        ``getattr`` and therefore still loads a module that omits it.

        Interaction to be aware of: when several modules register a handler for the same
        exception type (``RequestValidationError``), the last registration wins for the
        whole app. That is tolerable only because the contract requires every module to
        emit RFC 9457 Problem Details, so the competing handlers produce the same
        envelope. A module needing a *different* body for a shared exception type must
        not install it here.
        """
        ...

