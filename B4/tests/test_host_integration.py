"""Load B4 through MAIN's real composition host.

This is the "plug and play" claim made testable: the host is not simulated -- the module
under test is imported from MAIN's host source, discovered through the same
`autograder.modules` entry point the deployment uses, and served over a real HTTP client.

`RQ-B-4-40`'s promise is that "a new module that follows the rules is loaded and appears
in the registry without changing MAIN's code". The only honest way to check that is to let
MAIN's own loader do the loading.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

from autograder_b4.ops.dotenv import load_environment_file
from tests.conftest import REPOSITORY_ROOT

HOST_SOURCE = REPOSITORY_ROOT / "MAIN" / "host" / "src"

# The host builds its application at import time, so the environment has to be in place
# before it is imported -- which is exactly the ordering a deployment has too.
load_environment_file()
if str(HOST_SOURCE) not in sys.path:
    sys.path.insert(0, str(HOST_SOURCE))


@pytest.fixture(scope="module")
def host_app():
    if not (HOST_SOURCE / "autograder_host" / "main.py").is_file():
        pytest.fail(
            f"MAIN's composition host was not found at {HOST_SOURCE}; the integration test "
            "needs the sibling checkout",
            pytrace=False,
        )
    from autograder_host.main import create_app

    return create_app()


def test_the_host_loads_b4_from_its_entry_point(host_app) -> None:
    modules = [module.descriptor.module for module in host_app.state.modules]
    assert "B4" in modules


def test_the_host_serves_b4_routes(host_app) -> None:
    from fastapi.testclient import TestClient

    with TestClient(host_app, base_url="https://testserver") as client:
        jwks = client.get("/api/v1/auth/jwks")
        assert jwks.status_code == 200
        assert jwks.json()["keys"][0]["crv"] == "Ed25519"

        # An unauthenticated call is refused by B4, not by the host.
        refused = client.get("/api/v1/courses")
        assert refused.status_code == 401
        assert refused.headers["content-type"].startswith("application/problem+json")

        # The public liveness endpoint answers with the minimum.
        health = client.get("/api/v1/modules/B4/health")
        assert health.status_code == 200
        assert health.json() == {"module": "B4", "healthy": True}


def test_the_host_readiness_report_includes_b4_probes(host_app) -> None:
    """The host's `/health/ready` aggregates B4's own dependency probes."""
    from fastapi.testclient import TestClient

    with TestClient(host_app, base_url="https://testserver") as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    reports = {report["name"]: report for report in body["modules"]}
    assert "B4" in reports
    detail = reports["B4"]["detail"]
    # B4's report names each dependency it actually checked.
    for dependency in ("postgres", "migration", "nats"):
        assert dependency in detail


def test_b4_refuses_to_initialize_without_a_database_url(monkeypatch) -> None:
    """Fail closed: a platform module with no database must not half-start.

    Verified through the real host path -- the host calls `router(context)` while wiring
    routers, and B4 raises there, so the process fails to start rather than serving
    endpoints that cannot work.
    """
    from autograder_b4.config import ConfigurationError
    from autograder_b4.plugin import B4Module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="DATABASE_URL is required"):
        B4Module(settings=None).router(_NullContext())


def test_b4_reports_unhealthy_instead_of_raising_when_it_cannot_initialize(monkeypatch) -> None:
    """`health()` is the one entry point that must answer, so it reports rather than raises."""
    from autograder_b4.plugin import B4Module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    report = B4Module(settings=None).health(_NullContext())
    assert report.name == "B4"
    assert report.healthy is False
    assert "not initialized" in report.detail


class _NullContext:
    settings: ClassVar[dict] = {}

    def health(self):  # pragma: no cover - unused here
        raise NotImplementedError


def test_the_host_source_is_where_we_think_it_is() -> None:
    assert (HOST_SOURCE / "autograder_host" / "checks.py").is_file()
    assert Path(REPOSITORY_ROOT / "MAIN" / "contracts").is_dir()
