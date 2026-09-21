"""Repository hygiene gates (module rules, enforced mechanically).

Everything here is a "must not exist" rule from ``AGENTS.md`` §硬规则 8 and the
workstream DoD. Grep-based checks are cheap and they catch the exact regressions the
project has been burned by (legacy scoring/evidence/report revival, shell string
execution, cross-module imports).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

B3_ROOT = Path(__file__).resolve().parents[1]
SRC = B3_ROOT / "src"
TESTS = B3_ROOT / "tests"


def python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def source_texts(root: Path) -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in python_files(root)}


ALL_SOURCES = {**source_texts(SRC), **source_texts(TESTS)}

#: Modules that produce wire output (public responses and event payloads). The
#: protected vocabulary must not appear in them even as a string literal.
WIRE_MODULES = (
    "autograder_b3/dto/public.py",
    "autograder_b3/dto/wire.py",
    "autograder_b3/api/serializers.py",
    "autograder_b3/api/router.py",
    "autograder_b3/api/app.py",
    "autograder_b3/events/envelope.py",
)

#: Scanners that must list the forbidden tokens in order to check for them.
SCANNER_FILES = {"test_hygiene.py", "test_migrations.py"}


@pytest.mark.parametrize("path", sorted(source_texts(SRC)), ids=lambda p: p.name)
def test_no_shell_execution_anywhere_in_the_module(path: Path):
    """B3 never executes anything - least of all student code."""
    text = path.read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "os.system",
        "os.popen",
        "os.exec",
        "shell=True",
        "pty.spawn",
        "asyncio.create_subprocess",
    )
    for token in forbidden:
        assert token not in text, f"{path.name} references {token}"


def test_no_dynamic_code_evaluation():
    for path, text in source_texts(SRC).items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {"eval", "exec", "compile"}:
                raise AssertionError(f"{path.name} uses {node.id}()")
            if isinstance(node, ast.Attribute) and node.attr in {"import_module", "__import__"}:
                raise AssertionError(f"{path.name} performs dynamic imports")


def test_no_cross_module_imports():
    for path, text in source_texts(SRC).items():
        modules = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            assert not module.startswith("autograder_b2"), f"{path.name} imports {module}"
            assert not module.startswith("autograder_b4"), f"{path.name} imports {module}"
            assert not module.startswith("autograder_b1"), f"{path.name} imports {module}"


def test_no_legacy_identifier_anywhere():
    """Scans the delivery path; the scanner file itself lists the tokens by design."""
    forbidden = (
        "RQ-B-3-70",
        "evaluation_runs",
        "scoring_service",
        "evidence_service",
        "report_api",
    )
    for path, text in {**source_texts(SRC), **source_texts(TESTS)}.items():
        if path.name in SCANNER_FILES:
            continue
        for token in forbidden:
            assert token not in text, f"{path.name} mentions {token}"


def test_no_compatibility_or_alias_routes():
    from autograder_b3.api.router import router

    seen = set()
    for route in router.routes:
        key = (route.path, frozenset(getattr(route, "methods", ()) or ()))
        assert key not in seen, f"duplicate route: {route.path}"
        seen.add(key)
        assert "/v2/" not in route.path
        assert "/legacy" not in route.path
        assert not route.path.endswith("/"), f"alias-style trailing slash: {route.path}"


def test_only_the_b3_schema_is_referenced():
    """No SQL or ORM reference may name another module's schema."""
    pattern = re.compile(
        r"(REFERENCES|JOIN|FROM|INTO|UPDATE)\s+(b2|b4|public)\.",
        re.IGNORECASE,
    )
    orm_pattern = re.compile(r"ForeignKey\(\s*[\"'](b2|b4|public)\.")
    offenders = []
    for path, text in source_texts(SRC).items():
        if pattern.search(text) or orm_pattern.search(text):
            offenders.append(path.name)
    migration_sources = source_texts(B3_ROOT / "alembic")
    for path, text in migration_sources.items():
        if pattern.search(text) or orm_pattern.search(text):
            offenders.append(path.name)
    assert offenders == [], f"cross-schema SQL/ORM references: {offenders}"


def test_alembic_env_pins_the_b3_version_table():
    text = (B3_ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert 'version_table="alembic_version_b3"' in text
    assert 'version_table_schema="b3"' in text
    assert "target_metadata = None" in text


def test_protected_vocabulary_is_absent_from_wire_producing_modules():
    """The internal layer may name protected fields; the wire layer may not.

    ``dto/internal.py``, the repositories and the compile layer *must* handle
    reference answers and hidden cases - that is their job. What must never happen
    is a protected name becoming a response key or an event payload key, so this
    inspects dict keys and keyword argument names in the modules that build wire
    output. Contract *values* are allowed: ``mount: Literal["input",
    "expected_output"]`` is the contract's own enum, not a leak.
    """
    from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES

    offenders = []
    for relative in WIRE_MODULES:
        path = SRC / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and key.value in PROTECTED_FIELD_NAMES:
                        offenders.append((relative, key.value))
            elif isinstance(node, ast.keyword) and node.arg in PROTECTED_FIELD_NAMES:
                offenders.append((relative, node.arg))
    assert offenders == [], f"protected name used as a wire key: {offenders}"


def test_no_todo_or_not_implemented_markers_in_the_delivery_path():
    for path, text in source_texts(SRC).items():
        assert "NotImplementedError" not in text, path.name
        assert "FIXME" not in text, path.name
        assert "TODO" not in text, path.name


def test_taskone_stays_untracked_research_material():
    """`taskone/` is legacy research material: present on disk, never a source."""
    gitignore = (B3_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "taskone/" in gitignore
    assert not (SRC / "taskone").exists()
    for path, text in source_texts(SRC).items():
        assert "taskone" not in text, path.name
    assert not (B3_ROOT / "pyproject.toml").read_text(encoding="utf-8").count("taskone")


def test_pyproject_declares_the_test_toolchain():
    import tomllib

    data = tomllib.loads((B3_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = " ".join(data["dependency-groups"]["dev"])
    assert "pytest-cov" in dev
    assert "jsonschema" in dev
    assert "httpx" in dev
    assert any("pytest" in item for item in data["dependency-groups"]["dev"])


def test_contracts_lock_is_not_hand_edited():
    """The lock must carry a real digest and agree with the contract it points at.

    This previously asserted the literal ``0.1.0``, so it broke the moment MAIN bumped
    the contract -- while proving nothing about the lock's *content*. It now reads the
    version out of the contract the lock points at, which is the only property that
    matters: a lock pinning a different version than ``openapi/b3.yaml`` declares is
    exactly the drift this file exists to catch.
    """
    lock = (B3_ROOT / "contracts.lock").read_text(encoding="utf-8")
    assert re.search(r"contract_version: \d+\.\d+\.\d+", lock)
    assert re.search(r"digest: [0-9a-f]{64}", lock)
    assert "status:" in lock

    contract = B3_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b3.yaml"
    if not contract.exists():  # pragma: no cover - B3 expects to sit beside MAIN
        pytest.skip("MAIN/contracts is not present in this checkout")
    declared = re.search(r"^\s*version: (\d+\.\d+\.\d+)$", contract.read_text(encoding="utf-8"), re.M)
    assert declared is not None
    assert f"contract_version: {declared.group(1)}" in lock


def test_module_yaml_declares_the_two_directions():
    module_yaml = (B3_ROOT / "module.yaml").read_text(encoding="utf-8")
    assert "publishes: [autograder.v1.question.version.published]" in module_yaml
    assert "subscribes: [autograder.v1.evaluation.completed]" in module_yaml
    assert "schema: b3" in module_yaml
