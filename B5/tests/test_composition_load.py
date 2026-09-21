"""The positive half: the real loader composes the real modules, in DAG order.

`_load_modules()` is the composition host's own discovery and ordering function, called
without a single substitution. The composition it sees is whatever `autograder.modules`
entry points this environment has installed -- which is what makes the result evidence
rather than a restatement: B5 is found the same way B2, B3 and B4 are, by being installed.
"""

from __future__ import annotations

import importlib.metadata

from autograder_host.main import _load_modules


def test_the_loader_finds_b5_through_its_declared_entry_point() -> None:
    entries = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="autograder.modules")
    }
    assert entries["b5"] == "autograder_b5.plugin:create_module"


def test_the_loader_composes_every_installed_module_in_dependency_order() -> None:
    modules = _load_modules()
    assert [module.descriptor.module for module in modules] == ["B4", "B3", "B2", "B5"]


def test_every_declared_dependency_is_provided_before_its_dependent_loads() -> None:
    """The ordering property itself, not just one expected list.

    Asserting the exact order catches a regression today; asserting the invariant is what
    says *why* that order is correct, and it keeps holding when a module declares a new
    capability.
    """
    provided = {"host"}
    for module in _load_modules():
        descriptor = module.descriptor
        missing = set(descriptor.requires) - provided
        assert not missing, (
            f"{descriptor.module} was loaded before {sorted(missing)} was provided"
        )
        provided.update(descriptor.provides)


def test_b5_is_placed_by_capability_rather_than_by_name(monkeypatch) -> None:
    """`B0` sorts before every module it depends on and must still load last.

    Without this case, `["B4", "B3", "B2", "B5"]` could be produced by an alphabetical sort
    of the installed names -- which is exactly the coincidence that would make the DAG
    claim above unfalsifiable.
    """
    monkeypatch.setenv("AUTOGRADER_MODULES", "_probe_modules:depends_on_b5_conformance")
    order = [module.descriptor.module for module in _load_modules()]
    assert set(order) == {"B0", "B2", "B3", "B4", "B5"}
    assert order.index("B5") < order.index("B0")


def test_b5_is_last_because_the_report_describes_the_whole_composition() -> None:
    """B5's position is a consequence of its `requires`, not of its name."""
    modules = {module.descriptor.module: module for module in _load_modules()}
    assert set(modules["B5"].descriptor.requires) == {
        "host",
        "b4-auth",
        "b3-questions",
        "b2-submissions",
    }
    assert modules["B5"].descriptor.provides == ("b5-conformance",)
