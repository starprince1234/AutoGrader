"""The governance claim: B5 is discovered, governed, and does not own a schema.

CHG-0010 §5 states these as the checks that must be *proved* by B5's landing rather than
asserted, and ADR-0013's whole point was that a new module must be governed as well as
loaded -- "silently ungoverned is more dangerous than loudly rejected". The functions below
are MAIN's own gate functions, called unchanged: this file does not re-implement the gate,
it runs it.
"""

from __future__ import annotations

from autograder_host import checks


def test_the_gate_discovers_five_modules() -> None:
    assert checks.discovered_modules() == ["B1", "B2", "B3", "B4", "B5"]


def test_b5_is_not_a_database_schema_owner() -> None:
    owners = checks.module_schema_owners()
    assert "B5" not in owners
    assert owners == ["B2", "B3", "B4"]


def test_b5s_manifest_and_runtime_descriptor_agree() -> None:
    """`check_module_descriptors()` reads the real descriptor by calling the declared factory."""
    checks.check_module_descriptors()


def test_b5s_documentation_satisfies_the_module_governance_gate() -> None:
    checks.check_module_governance()


def test_b5s_route_declaration_matches_the_contract_it_owns() -> None:
    checks.check_route_declarations()


def test_b5s_lock_is_the_live_contract_digest() -> None:
    """`check_manifests()` recomputes the digest over `MAIN/contracts/**` and compares."""
    checks.check_manifests()


def test_b5s_installed_package_matches_its_working_tree() -> None:
    """`check_module_installations()` hashes the installed package against `B5/src`.

    This is the gate that keeps every other gate honest: with a stale install, `checks.py`
    imports yesterday's bytes while the reviewer reads today's source -- which is exactly how
    a perturbation test once "passed" against a copy. B5's own dev environment installs the
    sibling modules editable for the same reason.
    """
    checks.check_module_installations()
