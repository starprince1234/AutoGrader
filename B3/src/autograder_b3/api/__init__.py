"""API layer: router, dependencies, serializers, error handling."""

from __future__ import annotations

from .app import create_app, health_detail
from .deps import B3Dependencies, build_dependencies, get_dependencies, request_context
from .router import router
from .serializers import (
    SerializationViolation,
    assert_no_protected_keys,
    question_to_public,
    serialize_public,
    statistics_to_public,
    version_to_public,
)

__all__ = [
    "B3Dependencies",
    "SerializationViolation",
    "assert_no_protected_keys",
    "build_dependencies",
    "create_app",
    "get_dependencies",
    "health_detail",
    "question_to_public",
    "request_context",
    "router",
    "serialize_public",
    "statistics_to_public",
    "version_to_public",
]
