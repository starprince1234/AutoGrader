from autograder_host.main import create_app


def test_host_exposes_live_and_ready() -> None:
    routes = {route.path for route in create_app().routes if hasattr(route, "path")}
    assert "/health/live" in routes
    assert "/health/ready" in routes
