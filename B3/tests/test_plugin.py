"""Plugin surface: descriptor, router, health, and the recorded SDK gap."""

from __future__ import annotations

import re
from pathlib import Path

from autograder_b3.plugin import B3Module, create_module, install_error_handlers

B3_ROOT = Path(__file__).resolve().parents[1]


def test_descriptor_owns_question_capability_only():
    module = create_module()
    assert module.descriptor.module == "B3"
    assert "b3-questions" in module.descriptor.provides
    assert "b2-evaluation" not in module.descriptor.provides
    assert "b2-submissions" not in module.descriptor.provides


def test_descriptor_routes_match_module_yaml():
    module_yaml = (B3_ROOT / "module.yaml").read_text(encoding="utf-8")
    declared = re.search(r"routes:\s*\[([^\]]+)\]", module_yaml)
    assert declared is not None
    routes = tuple(item.strip() for item in declared.group(1).split(","))
    assert create_module().descriptor.routes == routes


def test_descriptor_events_match_the_module_declaration_for_publishes():
    """`descriptor.events` carries **both** directions, as B2's already does.

    The SDK's `ModuleDescriptor` is a flat tuple with no room for a direction, so a module
    that consumes events has to merge the subscribe side in; `module.yaml` remains the
    authoritative, directional record. This assertion previously pinned publishes only,
    which meant B3's own descriptor silently omitted `evaluation.completed` even though
    `events.consumer` subscribes to it.
    """
    module_yaml = (B3_ROOT / "module.yaml").read_text(encoding="utf-8")
    publishes = re.search(r"publishes:\s*\[([^\]]+)\]", module_yaml)
    subscribes = re.search(r"subscribes:\s*\[([^\]]+)\]", module_yaml)
    assert publishes is not None
    assert subscribes is not None
    expected = tuple(
        item.strip() for item in publishes.group(1).split(",")
    ) + tuple(item.strip() for item in subscribes.group(1).split(","))
    assert create_module().descriptor.events == expected


def test_router_exposes_the_declared_prefixes():
    from autograder_b3.api.router import router

    paths = {route.path for route in router.routes}
    for prefix in (
        "/api/v1/questions",
        "/api/v1/question-versions",
        "/api/v1/assignments",
    ):
        assert any(path == prefix or path.startswith(f"{prefix}/") for path in paths), prefix


def test_every_route_path_is_under_a_declared_prefix():
    from autograder_b3.api.router import router

    declared = create_module().descriptor.routes
    for route in router.routes:
        assert any(
            route.path == prefix or route.path.startswith(f"{prefix}/") for prefix in declared
        ), route.path


def test_plugin_health_reports_database_state(monkeypatch):
    from autograder_platform import ModuleContext

    module = B3Module()

    class Context:
        settings = None

    context: ModuleContext = Context()  # type: ignore[assignment]
    report = module.health(context)
    # Either the database answers (healthy) or the report explains why not; both
    # outcomes must be truthful rather than a hard-coded "ok".
    assert report.name == "B3"
    if report.healthy:
        assert "token_verifier" in report.detail
    else:
        assert "database unavailable" in report.detail


def test_router_factory_installs_dependencies():
    from autograder_b3.api.deps import get_dependencies

    class Context:
        settings = None

    module = B3Module()
    api_router = module.router(Context())
    assert api_router.routes
    assert module.create_event_consumer(Context()) is not None
    assert callable(install_error_handlers)
    assert get_dependencies is not None


def test_sdk_descriptor_cannot_express_event_direction():
    """The documented gap: `events` is a flat tuple, so module.yaml stays authoritative."""
    descriptor_fields = B3Module.descriptor.__dataclass_fields__
    assert "events" in descriptor_fields
    assert "publishes" not in descriptor_fields
    assert "subscribes" not in descriptor_fields
