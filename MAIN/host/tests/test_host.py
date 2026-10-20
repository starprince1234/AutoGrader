import importlib.util
import importlib.metadata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autograder_platform import HealthReport, ModuleDescriptor


@pytest.fixture
def host(monkeypatch):
    # Test host wiring without constructing database-backed business plugins at import.
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [])
    monkeypatch.delenv("AUTOGRADER_MODULES", raising=False)
    from autograder_host import main

    return main


def test_host_exposes_live_and_ready(host) -> None:
    routes = {route.path for route in host.create_app().routes if hasattr(route, "path")}
    assert "/health/live" in routes
    assert "/health/ready" in routes


@pytest.mark.parametrize(
    "unhealthy,missing",
    [(None, None), ("B3", None), (None, "B4"), ("B5", None), (None, "B5")],
)
def test_deployment_readiness_matches_host_response(host, monkeypatch, unhealthy, missing) -> None:
    class Module:
        def __init__(self, name):
            self.descriptor = ModuleDescriptor(name, "test")

        def router(self, context):
            from fastapi import APIRouter

            return APIRouter()

        def health(self, context):
            return HealthReport(self.descriptor.module, self.descriptor.module != unhealthy)

    monkeypatch.setattr(host, "_load_modules", lambda: [
        Module(name) for name in ("B2", "B3", "B4", "B5") if name != missing
    ])
    script = Path(__file__).resolve().parents[2] / "deploy" / "check-readiness.py"
    spec = importlib.util.spec_from_file_location("deployment_readiness", script)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    payload = TestClient(host.create_app()).get("/health/ready").json()
    assert checker.is_ready(payload) is (
        unhealthy is None and missing in (None, "B5")
    )


def test_deployment_rejects_empty_module_list() -> None:
    script = Path(__file__).resolve().parents[2] / "deploy" / "check-readiness.py"
    spec = importlib.util.spec_from_file_location("deployment_readiness", script)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.is_ready({"ready": True, "modules": []}) is False
