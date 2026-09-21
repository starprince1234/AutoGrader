"""HTTP surface for B4, assembled into a single router the host includes."""

from __future__ import annotations

from fastapi import APIRouter

from ..messaging.broker import nats_reachability
from ..registry import ModuleManifest
from .audit import build_audit_router
from .auth import build_auth_router
from .courses import build_courses_router
from .dependencies import B4State, build_dependencies
from .modules import build_modules_router
from .roster import build_roster_router
from .users import build_users_router

__all__ = [
    "B4State",
    "ModuleManifest",
    "build_api_router",
    "build_dependencies",
    "nats_reachability",
]


def build_api_router(state: B4State) -> APIRouter:
    """Mount every B4 endpoint on one router.

    The registry is validated in `plugin.router()` before this is called, so a drift has
    already stopped startup by the time any of these paths can be reached.
    """
    dependencies = build_dependencies(state)
    router = APIRouter()
    router.include_router(build_auth_router(state, dependencies))
    router.include_router(build_users_router(state, dependencies))
    router.include_router(build_courses_router(state, dependencies))
    router.include_router(build_roster_router(state, dependencies))
    router.include_router(build_audit_router(state, dependencies))
    router.include_router(build_modules_router(state, dependencies))
    return router
