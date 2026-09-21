"""The other half of the capability cycle; see `_probe_cycle_alpha` for why they are split."""

from __future__ import annotations

from fastapi import APIRouter
from autograder_platform import HealthReport, ModuleDescriptor

from _probe_modules import PROBE_CONTRACT_VERSION


class CycleBeta:
    descriptor = ModuleDescriptor(
        module="B96",
        contract_version=PROBE_CONTRACT_VERSION,
        provides=("b96-cycle-beta",),
        requires=("host", "b97-cycle-alpha"),
        routes=("/api/v1/probe-cycle-beta",),
    )

    def router(self, context) -> APIRouter:
        return APIRouter()

    def health(self, context) -> HealthReport:
        return HealthReport("B96", True, "cycle probe")


def cycle_beta() -> CycleBeta:
    return CycleBeta()
