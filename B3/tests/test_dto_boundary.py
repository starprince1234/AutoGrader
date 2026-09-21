"""TC-DTO-01: the public/internal DTO boundary is a file-level, assertable fact.

These tests are static (source level) plus model level. They exist because "public
DTO leakage is the highest-risk defect" (``MAIN/docs/workstreams/B3.md`` §5) and
because review is not a control - ADR-0003/ADR-0004 require mechanical proof.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

B3_ROOT = Path(__file__).resolve().parents[1]
SRC = B3_ROOT / "src" / "autograder_b3"


def read_module(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


def imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_dto_package_exports_nothing():
    """`from autograder_b3.dto import *` must yield no names (ADR-0004 §1)."""
    import types

    import autograder_b3.dto as dto

    assert dto.__all__ == ()
    leaked = [
        name
        for name, value in vars(dto).items()
        if not name.startswith("__")
        and not isinstance(value, (types.ModuleType, __import__("__future__")._Feature))
    ]
    assert leaked == [], f"dto package re-exports symbols: {leaked}"


def test_public_dto_module_never_imports_internal():
    source = read_module("dto/public.py")
    modules = imported_modules(source)
    assert not any("internal" in module for module in modules), modules
    assert not any(module.endswith("wire") for module in modules), modules


def identifiers_in(source: str) -> set[str]:
    """Every identifier used in code - string literals are excluded on purpose.

    A protected name is only a leak when it is a *field*, so this walks the AST for
    real identifiers (arguments, attributes, assignment targets, class/field names)
    and ignores quoted strings such as the mount literal ``"expected_output"``,
    which the contract itself requires.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            continue
    return found


def test_public_dto_module_has_no_protected_identifier():
    """The protected vocabulary lives in dto/internal.py, so this file is clean."""
    from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES

    offenders = sorted(identifiers_in(read_module("dto/public.py")) & PROTECTED_FIELD_NAMES)
    assert offenders == [], f"public DTO module defines protected identifier(s): {offenders}"


def test_public_models_share_no_field_with_the_protected_vocabulary():
    from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES
    from autograder_b3.dto.public import PUBLIC_DTO_TYPES

    for model in PUBLIC_DTO_TYPES:
        overlap = set(model.model_fields) & PROTECTED_FIELD_NAMES
        assert overlap == set(), f"{model.__name__} exposes protected field(s): {overlap}"


def test_public_models_forbid_unknown_fields():
    from autograder_b3.dto.public import CursorPage, QuestionPublic
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        QuestionPublic(
            question_id="11111111-1111-4111-8111-111111111111",
            course_id="11111111-1111-4111-8111-111111111111",
            title="t",
            stem_markdown="s",
            language="python",
            status="draft",
            reference_answer="leak",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        CursorPage(items=(), extra="nope")  # type: ignore[call-arg]


def test_serializer_refuses_protected_keys():
    from autograder_b3.api.serializers import SerializationViolation, assert_no_protected_keys

    with pytest.raises(SerializationViolation):
        assert_no_protected_keys({"question_id": "x", "reference_answer": "print(1)"})
    with pytest.raises(SerializationViolation):
        assert_no_protected_keys({"items": [{"expected_stdout": "3\n"}]})
    assert_no_protected_keys({"question_id": "x", "stem_assets": [{"asset_id": "y"}]})


def test_serialize_public_projects_only_dto_fields():
    from datetime import UTC, datetime
    from uuid import uuid4

    from autograder_b3.api.serializers import serialize_public
    from autograder_b3.dto.public import QuestionVersionPublic

    dto = QuestionVersionPublic(
        question_version_id=uuid4(),
        question_id=uuid4(),
        version=1,
        status="published",
        language="python",
        adapter_kind="stdin",
        content_digest="a" * 64,
        published_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        max_files=20,
        max_total_bytes=1024,
        # Required since the version DTO gained the frozen stem: a version detail response
        # that cannot render its own stem is what forced B1 to show the *current* stem
        # under a version heading, which reads as correct and is not.
        stem_markdown="第一版题干",
    )
    payload = serialize_public(dto)
    assert set(payload) == set(QuestionVersionPublic.model_fields)
    assert payload["published_at"] == "2026-09-15T08:00:00.000Z"
    assert isinstance(payload["question_version_id"], str)


def test_internal_module_owns_the_protected_vocabulary():
    from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES

    expected = {
        "reference_answer",
        "hidden_cases",
        "expected_output",
        "expected_stdout",
        "expected_stderr",
        "fixture_content",
        "source_code",
        "student_id",
    }
    assert expected <= PROTECTED_FIELD_NAMES
