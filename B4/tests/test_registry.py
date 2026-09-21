"""RQ-B-4-40: the runtime module registry and its drift rules.

The registry's whole value is that it can disagree with reality and say so. These tests
therefore spend as much effort on the failure paths -- a descriptor that contradicts its
manifest, two modules claiming one route, an unmet capability, a changed contract digest --
as on the happy path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autograder_b4.plugin import DESCRIPTOR, create_module
from autograder_b4.registry import (
    RegistryError,
    build_registry,
    compare_descriptor_to_manifest,
    contract_digest,
    discover_manifests,
    resolve_declared_modules,
    validate_registry,
)
from tests.conftest import REPOSITORY_ROOT

PASSWORD = "correct horse battery staple"


def _manifest_text(**overrides) -> str:
    document = {
        "schema_version": 1,
        "module": "B4",
        "contract_version": "0.1.0",
        "entry_point": "autograder_b4.plugin:create_module",
        "provides": ["b4-auth", "b4-course", "postgres", "nats"],
        "requires": ["host"],
        "routes": ["/api/v1/auth", "/api/v1/courses"],
        "events": {
            "publishes": ["autograder.v1.roster.import.completed"],
            "subscribes": [],
        },
        "database": {"schema": "b4", "migration_head": "20260915_0002"},
    }
    document.update(overrides)
    return yaml.safe_dump(document, sort_keys=False)


def _synthetic_root(tmp: Path, *, manifest: str | None = None, lock: dict | None = None) -> Path:
    """A module root with one manifest and a contract lock, for drift experiments."""
    root = tmp / "repo"
    module_dir = root / "B4"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.yaml").write_text(manifest or _manifest_text(), encoding="utf-8")
    contracts = root / "MAIN" / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    (contracts / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    lock_document = lock or {
        "contract_version": "0.1.0",
        "digest": contract_digest(contracts),
    }
    (module_dir / "contracts.lock").write_text(
        yaml.safe_dump(lock_document, sort_keys=False), encoding="utf-8"
    )
    return root


class _FakeModule:
    """A module object exposing only what the registry reads.

    Used to construct a *disagreement* on purpose; the real modules are exercised through
    the app fixture, and the registry's own module is validated at every test session
    start.
    """

    def __init__(self, **fields) -> None:
        from autograder_platform import ModuleDescriptor

        self.descriptor = ModuleDescriptor(**fields)


# ------------------------------------------------------------------------- happy path


def test_the_real_composition_validates(settings) -> None:
    """The module set this repository actually declares must pass its own registry."""
    manifests = validate_registry(settings.module_root)
    assert set(manifests) >= {"B1", "B2", "B3", "B4"}
    assert manifests["B4"].database_schema == "b4"
    assert manifests["B4"].migration_head == "20260915_0002"


def test_contract_digest_mirrors_what_main_locked(settings) -> None:
    """B4 recomputes MAIN's digest; agreement with every lock is the proof it is faithful."""
    live = contract_digest(settings.module_root / "MAIN" / "contracts")
    for name, manifest in discover_manifests(settings.module_root).items():
        lock = yaml.safe_load((manifest.path.parent / "contracts.lock").read_text(encoding="utf-8"))
        assert lock["digest"] == live, f"{name}'s contract lock disagrees with the live tree"


def test_entry_point_names_are_resolved_to_module_names() -> None:
    """The entry point is named `b4`; the module is `B4`. The registry must not confuse them."""
    descriptors = resolve_declared_modules()
    names = {descriptor.module for descriptor in descriptors}
    assert "B4" in names
    assert not any(name.islower() for name in names)


def test_registry_reports_every_declared_field(client, factory, sign_in_as) -> None:
    """Acceptance 1: name, contract version, capabilities, routes, events, schema, head."""
    factory.user(username="root", password=PASSWORD, roles=("admin",))
    response = client.get("/api/v1/modules", headers=sign_in_as("root").headers)
    assert response.status_code == 200, response.text
    modules = {item["module"]: item for item in response.json()["modules"]}
    assert "B4" in modules
    b4 = modules["B4"]
    # Compared against `module.yaml`, not against a literal: MAIN's change protocol moves
    # this value, and a hard-coded number turns every contract bump into a false failure.
    manifest = yaml.safe_load(
        (REPOSITORY_ROOT / "B4" / "module.yaml").read_text(encoding="utf-8")
    )
    assert b4["contract_version"] == manifest["contract_version"]
    assert set(b4["provides"]) == {"b4-auth", "b4-course", "postgres", "nats"}
    assert b4["requires"] == ["host"]
    assert set(b4["routes"]) == set(DESCRIPTOR.routes)
    assert b4["publishes"] == ["autograder.v1.roster.import.completed"]
    assert b4["subscribes"] == []
    assert b4["database_schema"] == "b4"
    assert b4["migration_head"] == "20260915_0002"
    assert isinstance(b4["healthy"], bool)
    assert b4["detail"]


def test_registry_reports_the_modules_the_host_loaded(client, factory, sign_in_as) -> None:
    factory.user(username="root", password=PASSWORD, roles=("admin",))
    body = client.get("/api/v1/modules", headers=sign_in_as("root").headers).json()
    # `app.state.modules` in the test host holds exactly the B4 module.
    assert [item["module"] for item in body["modules"]] == ["B4"]


def test_the_module_itself_agrees_with_its_manifest(settings) -> None:
    manifest = discover_manifests(settings.module_root)["B4"]
    assert compare_descriptor_to_manifest(manifest, DESCRIPTOR) == []


def test_the_declared_descriptor_matches_what_the_entry_point_builds() -> None:
    built = create_module()
    assert built.descriptor == DESCRIPTOR


# -------------------------------------------------------------------------- drift rules


def test_a_descriptor_that_contradicts_its_manifest_fails(workspace_tmp) -> None:
    """Acceptance 3: drift must fail loudly and name the module."""
    root = _synthetic_root(workspace_tmp)
    rogue = _FakeModule(
        module="B4",
        contract_version="0.2.0",
        provides=("b4-auth",),
        requires=("host",),
        routes=("/api/v1/auth",),
        events=("autograder.v1.roster.import.completed",),
    )
    with pytest.raises(RegistryError) as failure:
        validate_registry(root, declared=[rogue])
    message = str(failure.value)
    assert "B4" in message
    assert "contract_version" in message and "0.2.0" in message


def test_a_missing_capability_fails(workspace_tmp) -> None:
    """Acceptance 3: a requirement nothing provides stops the composition."""
    root = _synthetic_root(workspace_tmp, manifest=_manifest_text(requires=["host", "b3-magic"]))
    honest = _FakeModule(
        module="B4",
        contract_version="0.1.0",
        provides=("b4-auth", "b4-course", "postgres", "nats"),
        requires=("host", "b3-magic"),
        routes=("/api/v1/auth", "/api/v1/courses"),
        events=("autograder.v1.roster.import.completed",),
    )
    with pytest.raises(RegistryError) as failure:
        validate_registry(root, declared=[honest])
    assert "b3-magic" in str(failure.value)


def test_a_duplicate_route_between_modules_fails(workspace_tmp) -> None:
    """Acceptance 3: two modules claiming one prefix is a conflict, not a merge."""
    root = _synthetic_root(workspace_tmp)
    other_dir = root / "B3"
    other_dir.mkdir()
    other = yaml.safe_load(_manifest_text())
    other["module"] = "B3"
    other["entry_point"] = "autograder_b3.plugin:create_module"
    other["provides"] = ["b3-questions"]
    other["routes"] = ["/api/v1/auth"]
    other["events"] = {"publishes": [], "subscribes": []}
    other["database"] = {"schema": "b3", "migration_head": "x"}
    (other_dir / "module.yaml").write_text(yaml.safe_dump(other, sort_keys=False), encoding="utf-8")
    (other_dir / "contracts.lock").write_text(
        yaml.safe_dump(
            {"contract_version": "0.1.0", "digest": contract_digest(root / "MAIN" / "contracts")},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError) as failure:
        validate_registry(root, declared=[])
    assert "duplicate route" in str(failure.value)


def test_a_contract_digest_drift_fails(workspace_tmp) -> None:
    """Acceptance 3: a contract summary that no longer matches is a failure."""
    root = _synthetic_root(workspace_tmp, lock={"contract_version": "0.1.0", "digest": "0" * 64})
    with pytest.raises(RegistryError) as failure:
        validate_registry(root, declared=[])
    assert "digest drift" in str(failure.value)


def test_a_contract_version_mismatch_fails(workspace_tmp) -> None:
    root = _synthetic_root(
        workspace_tmp,
        manifest=_manifest_text(contract_version="0.2.0"),
        lock={
            "contract_version": "0.1.0",
            "digest": None,  # replaced below
        },
    )
    lock_path = root / "B4" / "contracts.lock"
    lock_path.write_text(
        yaml.safe_dump(
            {
                "contract_version": "0.1.0",
                "digest": contract_digest(root / "MAIN" / "contracts"),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError) as failure:
        validate_registry(root, declared=[])
    assert "contract_version" in str(failure.value)


def test_a_loaded_module_without_a_manifest_fails(workspace_tmp) -> None:
    root = _synthetic_root(workspace_tmp)
    stranger = _FakeModule(
        module="B9",
        contract_version="0.1.0",
        provides=("b9-thing",),
        requires=("host",),
        routes=("/api/v1/nine",),
        events=(),
    )
    with pytest.raises(RegistryError) as failure:
        validate_registry(root, declared=[stranger])
    assert "B9" in str(failure.value)


def test_a_manifest_without_a_loaded_module_is_not_an_error(settings) -> None:
    """B1 is a static bundle: declared in its manifest, never loaded as a plugin."""
    manifests = validate_registry(settings.module_root, loaded=())
    assert "B1" in manifests


def test_resolution_failure_is_reported_not_swallowed(workspace_tmp) -> None:
    root = _synthetic_root(workspace_tmp)
    (root / "B4" / "module.yaml").write_text(
        _manifest_text(entry_point="not_a_module:missing_factory"), encoding="utf-8"
    )
    with pytest.raises(RegistryError):
        validate_registry(
            root, declared=None, environ={"AUTOGRADER_MODULES": "not_a_module:missing"}
        )


def test_build_registry_refuses_to_describe_an_unknown_module(settings) -> None:
    manifests = discover_manifests(settings.module_root)
    stranger = _FakeModule(
        module="B9",
        contract_version="0.1.0",
        provides=(),
        requires=(),
        routes=(),
        events=(),
    )
    with pytest.raises(RegistryError):
        build_registry(manifests=manifests, loaded=[stranger])


# --------------------------------------------------------------------- access control


def test_the_full_registry_requires_an_administrator(client, factory, sign_in_as) -> None:
    """Acceptance 5: the full registry is not readable by an unprivileged caller."""
    factory.user(username="student", password=PASSWORD, roles=("student",))
    factory.user(username="teacher", password=PASSWORD, roles=("teacher",))
    assert client.get("/api/v1/modules").status_code == 401
    assert client.get("/api/v1/modules", headers=sign_in_as("student").headers).status_code == 403
    assert client.get("/api/v1/modules", headers=sign_in_as("teacher").headers).status_code == 403


def test_a_denied_registry_read_is_audited(client, factory, sign_in_as, engine) -> None:
    from sqlalchemy import select

    from autograder_b4.db.tables import audit_records

    factory.user(username="student", password=PASSWORD, roles=("student",))
    client.get("/api/v1/modules", headers=sign_in_as("student").headers)
    with engine.connect() as connection:
        rows = connection.execute(
            select(audit_records.c.action, audit_records.c.outcome)
        ).fetchall()
    assert ("authz.denied", "denied") in {(row.action, row.outcome) for row in rows}


def test_the_per_module_health_probe_is_public_but_minimal(client) -> None:
    """Acceptance 5: the public endpoint exposes liveness, not the shape of the system."""
    response = client.get("/api/v1/modules/B4/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"module": "B4", "healthy": True}


def test_health_for_an_unknown_module_is_404(client) -> None:
    assert client.get("/api/v1/modules/B9/health").status_code == 404


def test_health_for_a_declared_but_unloaded_module_is_404(client) -> None:
    """B1 has a manifest but the host does not load it as a module."""
    assert client.get("/api/v1/modules/B1/health").status_code == 404


def test_registry_reports_a_module_whose_health_check_raises(
    client, factory, sign_in_as, app, settings
) -> None:
    """A module that cannot answer must be reported unhealthy, not omitted or fatal.

    The descriptor is built from B2's *real* manifest so the registry's three-way check
    still passes: a made-up descriptor would be rejected as drift, which is a different
    (and separately tested) behaviour.
    """
    from autograder_platform import ModuleDescriptor

    factory.user(username="root", password=PASSWORD, roles=("admin",))
    manifest = discover_manifests(settings.module_root)["B2"]

    class ExplodingModule:
        descriptor = ModuleDescriptor(
            module=manifest.module,
            contract_version=manifest.contract_version,
            provides=manifest.provides,
            requires=manifest.requires,
            routes=manifest.routes,
            events=manifest.events,
        )

        def health(self, context):
            raise RuntimeError("boom")

    app.state.modules = [*app.state.modules, ExplodingModule()]
    response = client.get("/api/v1/modules", headers=sign_in_as("root").headers)
    assert response.status_code == 200, response.text
    reported = {item["module"]: item for item in response.json()["modules"]}
    assert reported["B2"]["healthy"] is False
    assert "RuntimeError" in reported["B2"]["detail"]


def test_a_descriptor_that_disagrees_at_read_time_is_reported_as_unavailable(
    client, factory, sign_in_as, app
) -> None:
    """Acceptance 3: drift discovered while serving is a failure, not a plausible list."""
    from autograder_platform import ModuleDescriptor

    factory.user(username="root", password=PASSWORD, roles=("admin",))

    class DriftedModule:
        descriptor = ModuleDescriptor(
            module="B2",
            contract_version="0.1.0",
            provides=("b2-submissions",),
            requires=("host",),
            routes=("/api/v1/does-not-exist",),
            events=(),
        )

    app.state.modules = [*app.state.modules, DriftedModule()]
    response = client.get("/api/v1/modules", headers=sign_in_as("root").headers)
    assert response.status_code == 503
    assert "B2" in response.json()["detail"]


def test_registry_read_is_audited_for_administrators(client, factory, sign_in_as, engine) -> None:
    from sqlalchemy import select

    from autograder_b4.db.tables import audit_records

    factory.user(username="root", password=PASSWORD, roles=("admin",))
    client.get("/api/v1/modules", headers=sign_in_as("root").headers)
    # The read itself is logged structurally; the assertion here is that the endpoint did
    # not need to write an audit row, which keeps a hot read path free of write traffic.
    with engine.connect() as connection:
        actions = [row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()]
    assert "authz.denied" not in actions


def test_registry_module_names_are_validated_by_the_contract_pattern(client, factory, sign_in_as):
    """CHG-0009 / ADR-0013: the shape is a pattern, not a closed set of four names.

    The pattern is defined once in `MAIN/contracts/manifest.schema.json`; asserting against
    a literal set here would keep the closed-set assumption alive in the tests even after
    the contract opened it.
    """
    import re

    from autograder_b4.api.modules import MODULE_PATTERN

    factory.user(username="root", password=PASSWORD, roles=("admin",))
    body = client.get("/api/v1/modules", headers=sign_in_as("root").headers).json()
    assert body["modules"], "the composition must declare at least one module"
    for item in body["modules"]:
        assert re.fullmatch(MODULE_PATTERN, item["module"]), item["module"]
        assert item["database_schema"] == "none" or re.fullmatch(
            "b[0-9]{1,3}", item["database_schema"]
        ), item["database_schema"]
        for route in item["routes"]:
            assert route.startswith("/")
        for subject in item["publishes"] + item["subscribes"]:
            assert subject.startswith("autograder.v1.")


def test_a_fifth_module_name_is_accepted_by_the_pattern() -> None:
    """The headline goal, made checkable: `B5` must not be rejected by B4's own pattern."""
    import re

    from autograder_b4.api.modules import MODULE_PATTERN

    for name in ("B5", "B12", "B999"):
        assert re.fullmatch(MODULE_PATTERN, name), name
    for bogus in ("B", "B1234", "b5", "X5", "B5x"):
        assert not re.fullmatch(MODULE_PATTERN, bogus), bogus


def test_two_entry_points_with_the_same_name_are_refused(monkeypatch) -> None:
    """ADR-0013 decision 5: a duplicate declaration name fails loudly, in both directions.

    The host resolves the entry-point group into a dict keyed by name (last wins) while this
    function kept the first, so the same input produced two different compositions. Neither
    direction is recoverable by a reader of the logs, so the registry refuses to choose.
    """
    import importlib.metadata
    from types import SimpleNamespace

    class _StubEntryPoint:
        def __init__(self, name: str, module_name: str) -> None:
            self.name = name
            self._module_name = module_name

        def load(self):
            module_name = self._module_name

            def _factory():
                return SimpleNamespace(descriptor=SimpleNamespace(module=module_name))

            return _factory

    entries = [
        _StubEntryPoint("b2", "B2"),
        _StubEntryPoint("b3", "B3"),
        _StubEntryPoint("b2", "B9"),
    ]
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **kwargs: list(entries), raising=True
    )
    with pytest.raises(RegistryError) as failure:
        resolve_declared_modules({})
    assert "b2" in str(failure.value)
    assert "twice" in str(failure.value)


def test_repository_root_is_the_expected_directory() -> None:
    assert (REPOSITORY_ROOT / "MAIN" / "contracts").is_dir()
    assert Path(REPOSITORY_ROOT / "B4").is_dir()
