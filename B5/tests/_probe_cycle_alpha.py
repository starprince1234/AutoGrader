"""One half of a capability cycle.

The two halves live in **separate modules** on purpose. `AUTOGRADER_MODULES` names a
declaration by its module path, and the loader treats that name as the module's identity --
so two factories reached through one module path are one name declared twice, and the
duplicate-name check (ADR-0013 decision 5) fires before the cycle is ever examined. The
first version of this fixture did exactly that and would have "passed" as a test of the
wrong failure. Splitting them is what makes the cycle the thing under test.
"""

from __future__ import annotations

from fastapi import APIRouter
from autograder_platform import HealthReport, ModuleDescriptor

from _probe_modules import PROBE_CONTRACT_VERSION


class CycleAlpha:
    descriptor = ModuleDescriptor(
        module="B97",
        contract_version=PROBE_CONTRACT_VERSION,
        provides=("b97-cycle-alpha",),
        requires=("host", "b96-cycle-beta"),
        routes=("/api/v1/probe-cycle-alpha",),
    )

    def router(self, context) -> APIRouter:
        return APIRouter()

    def health(self, context) -> HealthReport:
        return HealthReport("B97", True, "cycle probe")


def cycle_alpha() -> CycleAlpha:
    return CycleAlpha()
