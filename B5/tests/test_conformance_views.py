"""The comparison itself, on compositions built for the purpose.

The real composition is consistent, so on its own it cannot show that the comparison would
notice if it were not. These cases construct the three views directly and assert that each
kind of difference appears for its own reason -- and, just as importantly, that a difference
which is *not* one (a host route outside the contract, a full path under a declared prefix)
is not reported.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from autograder_platform import ModuleDescriptor

from autograder_b5.conformance import (
    CONTRACT_PATH_UNSERVED,
    DESCRIPTOR_ROUTE_UNDECLARED,
    build_report,
)

#: A descriptor built by hand has no manifest and no lock; see `_probe_modules` for why it
#: must not borrow the live contract version.
PROBE_CONTRACT_VERSION = "0.0.0-probe"


def _contracts(tmp_path: Path, documents: dict[str, list[str]]) -> Path:
    directory = tmp_path / "openapi"
    directory.mkdir(parents=True, exist_ok=True)
    for name, declared in documents.items():
        payload = {
            "openapi": "3.1.0",
            "info": {"title": name, "version": "0.0.0"},
            "paths": {path: {} for path in declared},
        }
        (directory / name).write_text(yaml.safe_dump(payload), encoding="utf-8")
    return tmp_path


def _module(name: str, routes: tuple[str, ...]) -> ModuleDescriptor:
    return ModuleDescriptor(
        module=name,
        contract_version=PROBE_CONTRACT_VERSION,
        provides=(),
        requires=(),
        routes=routes,
    )


def test_a_contract_path_no_module_serves_is_reported_with_its_source(tmp_path) -> None:
    contracts = _contracts(tmp_path, {"probe.yaml": ["/api/v1/questions"]})
    report = build_report(contracts, [_module("B3", ("/api/v1/assignments",))], [])
    assert report.consistent is False
    assert [(item.kind, item.detail) for item in report.differences] == [
        (
            CONTRACT_PATH_UNSERVED,
            "probe.yaml declares /api/v1/questions, which no loaded module's declared "
            "route covers",
        ),
        (
            DESCRIPTOR_ROUTE_UNDECLARED,
            "B3 declares route /api/v1/assignments, but no path in MAIN/contracts/openapi "
            "declares it",
        ),
    ]


def test_a_module_route_the_contract_does_not_describe_is_reported(tmp_path) -> None:
    contracts = _contracts(tmp_path, {"probe.yaml": ["/api/v1/questions"]})
    report = build_report(
        contracts, [_module("B3", ("/api/v1/questions", "/api/v1/extra"))], []
    )
    assert [item.kind for item in report.differences] == [DESCRIPTOR_ROUTE_UNDECLARED]
    assert "/api/v1/extra" in report.differences[0].detail


def test_a_host_route_outside_the_contract_is_not_a_difference(tmp_path) -> None:
    """`/health/*` is the host's, deliberately outside the contract (ADR-0007, checks.py).

    It matters in the other direction too: a contract path the *host* serves is served, so
    it is not reported as unserved even though no module declares it.
    """
    contracts = _contracts(tmp_path, {"probe.yaml": ["/health/ready"]})
    served = build_report(contracts, [], ["/health/ready", "/health/live"])
    assert served.consistent is True
    assert served.differences == []

    unserved = build_report(contracts, [], ["/health/live"])
    assert [item.kind for item in unserved.differences] == [CONTRACT_PATH_UNSERVED]


def test_a_full_path_under_a_declared_prefix_is_not_a_difference(tmp_path) -> None:
    """The trap the literal comparison fell into: a contract path is longer than a prefix."""
    contracts = _contracts(
        tmp_path, {"probe.yaml": ["/api/v1/auth/session", "/api/v1/auth/jwks"]}
    )
    report = build_report(contracts, [_module("B4", ("/api/v1/auth",))], [])
    assert report.consistent is True
    assert report.contract_path_count == 2


def test_the_report_counts_the_contract_rather_than_the_declarations(tmp_path) -> None:
    contracts = _contracts(
        tmp_path, {"one.yaml": ["/api/v1/a", "/api/v1/b"], "two.yaml": ["/api/v1/c"]}
    )
    report = build_report(contracts, [_module("B9", ("/api/v1",))], [])
    assert report.contract_path_count == 3
    assert report.loaded_modules == ["B9"]
    assert report.consistent is True
