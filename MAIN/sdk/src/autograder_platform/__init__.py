"""Stable plugin protocol used by the MAIN composition host."""

from .contracts import (
    DEPLOYMENT_ROOT_VARIABLE,
    LOCK_FIELDS,
    contract_digest,
    declared_contract_version,
    deployment_root,
    lock_is_current,
    read_lock,
)
from .protocol import HealthReport, ModuleContext, ModuleDescriptor, PlatformModule

__all__ = [
    "DEPLOYMENT_ROOT_VARIABLE",
    "LOCK_FIELDS",
    "HealthReport",
    "ModuleContext",
    "ModuleDescriptor",
    "PlatformModule",
    "contract_digest",
    "declared_contract_version",
    "deployment_root",
    "lock_is_current",
    "read_lock",
]
