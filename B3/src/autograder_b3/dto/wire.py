"""Wire normalization for internal artifacts (package only).

Kept out of ``dto/public.py`` on purpose: this module knows about protected
content. ``dto/internal.py`` calls it to turn an :class:`EvaluationPackage` into
the exact JSON shape frozen in
``MAIN/contracts/schemas/evaluation-package.schema.json`` (uuid strings, lowercase
sha256 hex, millisecond-precision UTC timestamps).

The output of :func:`package_to_wire` is intentionally free of Python objects so
that ``compile.canonical.compute_package_digest`` can hash it deterministically.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from uuid import UUID

from ..logging import to_rfc3339_millis
from .internal import EvaluationPackage, SecurityPolicy


def _uuid_str(value: UUID | str) -> str:
    return str(value)


def security_policy_to_wire(policy: SecurityPolicy) -> dict[str, Any]:
    return {
        "network": policy.network,
        "read_only_root": policy.read_only_root,
        "allow_root": policy.allow_root,
        "max_memory_bytes": policy.max_memory_bytes,
        "max_cpu_seconds": policy.max_cpu_seconds,
        "max_wall_seconds": policy.max_wall_seconds,
        "max_output_bytes": policy.max_output_bytes,
        "max_processes": policy.max_processes,
        "max_files_created": policy.max_files_created,
    }


def package_to_wire(package: EvaluationPackage) -> dict[str, Any]:
    return {
        "question_version_id": _uuid_str(package.question_version_id),
        "package_digest": package.package_digest,
        "language": package.language,
        "adapter": _normalize_mapping(package.adapter),
        "tests": [_normalize_mapping(entry) for entry in package.tests],
        "security_policy": security_policy_to_wire(package.security_policy),
        "max_files": package.max_files,
        "max_total_bytes": package.max_total_bytes,
        "created_at": to_rfc3339_millis(package.created_at),
    }


def _normalize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_scalar(item) for key, item in value.items()}


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return _normalize_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_scalar(item) for item in value]
    return value
