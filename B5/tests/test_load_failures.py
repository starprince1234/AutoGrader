"""The negative half: five compositions that must be refused, each for a named reason.

An integration audit measured these by hand before B5 existed; here they are permanent.
Every case below that goes through `_load_modules()` is driven through the **real** loader
with the **real** modules installed -- the probe is an additional declaration, not a
replacement for the composition -- so a case can only pass because the loader detected it.

The one case that is not driven through the loader (the duplicate entry-point name inside
B4's registry) says so in its own docstring rather than being quietly counted as loader
evidence.
"""

from __future__ import annotations

import pytest

from autograder_host.main import ModuleLoadError, _load_modules


def _declare(monkeypatch, *specifications: str) -> None:
    """Add module factories to the composition the loader will see.

    `AUTOGRADER_MODULES` is the documented development declaration
    (`MAIN/docs/runbooks/integration.md` §4). Nothing here changes what the loader does with
    a declaration: it appends to the installed entry points and the same validation runs.
    """
    monkeypatch.setenv("AUTOGRADER_MODULES", ",".join(specifications))


def test_a_capability_no_module_provides_fails_before_any_module_is_served(monkeypatch) -> None:
    _declare(monkeypatch, "_probe_modules:require_unprovided_capability")
    with pytest.raises(ModuleLoadError) as failure:
        _load_modules()
    message = str(failure.value)
    assert "b98-nothing-provides" in message, message
    assert "B99" in message, message


def test_a_capability_cycle_fails_instead_of_being_broken_arbitrarily(monkeypatch) -> None:
    _declare(monkeypatch, "_probe_cycle_alpha:cycle_alpha", "_probe_cycle_beta:cycle_beta")
    with pytest.raises(ModuleLoadError) as failure:
        _load_modules()
    message = str(failure.value)
    assert "B97" in message and "B96" in message, message
    assert "b96-cycle-beta" in message and "b97-cycle-alpha" in message, message


def test_two_modules_claiming_one_route_prefix_fail(monkeypatch) -> None:
    _declare(monkeypatch, "_probe_modules:claim_occupied_route")
    with pytest.raises(ModuleLoadError) as failure:
        _load_modules()
    message = str(failure.value)
    assert "duplicate module routes" in message, message
    assert "/api/v1/auth" in message, message


def test_a_descriptor_that_is_not_a_module_descriptor_fails(monkeypatch) -> None:
    _declare(monkeypatch, "_probe_modules:descriptor_is_not_a_module_descriptor")
    with pytest.raises(ModuleLoadError) as failure:
        _load_modules()
    assert "did not provide a ModuleDescriptor" in str(failure.value)


def test_two_entry_points_sharing_a_name_fail_loudly_in_the_host(monkeypatch) -> None:
    """ADR-0013 decision 5, host half.

    The loader used to resolve a duplicate name by keeping the *last* declaration while
    B4's registry kept the *first*, so the same input produced two opposite answers and a
    swapped descriptor could pass startup validation. A real module (B4) is declared twice
    here, so the refusal is about the name, not about a synthetic oddity.
    """
    _declare(
        monkeypatch,
        "autograder_b4.plugin:create_module",
        "autograder_b4.plugin:create_module",
    )
    with pytest.raises(ModuleLoadError) as failure:
        _load_modules()
    message = str(failure.value)
    assert "duplicate module entry point name" in message, message
    assert "autograder_b4.plugin" in message, message


def test_two_entry_points_sharing_a_name_fail_loudly_in_b4s_registry() -> None:
    """ADR-0013 decision 5, registry half.

    This is the one case in this file that does **not** go through `_load_modules()`: it
    calls B4's `resolve_declared_modules()` directly, because the decision has to hold in
    both places and only the host's half is loader-driven. It imports a sibling module's
    registry on purpose -- B5's production code never does -- so that the claim "both sides
    now fail" is checked rather than asserted.
    """
    from autograder_b4.registry import RegistryError, resolve_declared_modules

    duplicate = (
        "autograder_b4.plugin:create_module,autograder_b4.plugin:create_module"
    )
    with pytest.raises(RegistryError) as failure:
        resolve_declared_modules({"AUTOGRADER_MODULES": duplicate})
    assert "declared twice" in str(failure.value)
