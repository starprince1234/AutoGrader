"""Synthetic compositions the loader's failure modes are exercised with.

These are **not** mocks of the loader. Every factory returns a real object carrying the
SDK's own `ModuleDescriptor`, handed to the real `autograder_host.main._load_modules()`
through the documented `AUTOGRADER_MODULES` declaration (`MAIN/docs/runbooks/integration.md`
§4). The loader decides everything -- ordering, capability closure, route uniqueness,
descriptor type; a probe only *describes* a composition that has to be refused.

They live in the test tree rather than in `autograder_b5` because none of them is a module:
they are inputs to a failure, and shipping them in the package would put code in the
delivery path that no deployment should ever load.

The contract version is `0.0.0-probe` on purpose: a probe has no manifest and no lock, and
pinning it to the live contract version would make these fixtures look like they track the
contract when nothing about them does.
"""

from __future__ import annotations

from fastapi import APIRouter
from autograder_platform import HealthReport, ModuleDescriptor

PROBE_CONTRACT_VERSION = "0.0.0-probe"


class _Probe:
    def __init__(self, descriptor) -> None:
        self.descriptor = descriptor

    def router(self, context) -> APIRouter:
        return APIRouter()

    def health(self, context) -> HealthReport:
        return HealthReport(self.descriptor.module, True, "probe module")


def require_unprovided_capability() -> _Probe:
    """Requires a capability no module in this repository provides."""
    return _Probe(
        ModuleDescriptor(
            module="B99",
            contract_version=PROBE_CONTRACT_VERSION,
            provides=("b99-probe",),
            requires=("host", "b98-nothing-provides"),
            routes=("/api/v1/probe-unprovided",),
        )
    )


def claim_occupied_route() -> _Probe:
    """Claims a route prefix B4 already owns in the real composition."""
    return _Probe(
        ModuleDescriptor(
            module="B95",
            contract_version=PROBE_CONTRACT_VERSION,
            provides=("b95-probe",),
            requires=("host",),
            routes=("/api/v1/auth",),
        )
    )


class _NotAModule:
    """A loadable object whose `descriptor` is not a `ModuleDescriptor`."""

    descriptor = {"module": "B94", "contract_version": PROBE_CONTRACT_VERSION}


def descriptor_is_not_a_module_descriptor() -> _NotAModule:
    return _NotAModule()


def depends_on_b5_conformance() -> _Probe:
    """A module that sorts *before* B5 but cannot be loaded before it.

    The loader appends ready modules in name order, so a composition that happens to be
    alphabetically consistent proves nothing about the capability DAG. This probe's name
    (`B0`) sorts ahead of every module it depends on, so it can only be ordered correctly by
    reading `requires`.
    """
    return _Probe(
        ModuleDescriptor(
            module="B0",
            contract_version=PROBE_CONTRACT_VERSION,
            provides=("b0-probe",),
            requires=("host", "b5-conformance"),
            routes=("/api/v1/probe-after-b5",),
        )
    )
