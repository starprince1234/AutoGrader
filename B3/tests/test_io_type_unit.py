"""Unit tests for P2 io_type resolution, matrix validation, revision diff, and public serialization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from autograder_b3.api.serializers import (
    coverage_to_public,
    learning_context_to_public,
    question_to_public,
    serialize_public,
    version_to_public,
)
from autograder_b3.compile.manifest import (
    resolve_question_io_type,
    validate_course_language_io_type,
)
from autograder_b3.domain.catalog import LearningContext, TagCoverage
from autograder_b3.domain.revisions import REVISION_DIFF_KEYS, compute_revision_diff
from autograder_b3.errors import ManifestInvalid


def test_resolve_question_io_type_defaults_and_rejects_mismatch() -> None:
    assert resolve_question_io_type(None, "stdin") == "stdin"
    assert resolve_question_io_type("file", "file") == "file"
    assert resolve_question_io_type("callable", "callable") == "callable"

    with pytest.raises(ManifestInvalid):
        resolve_question_io_type("file", "stdin")


def test_validate_course_language_io_type_matrix() -> None:
    for io_type in ("stdin", "file", "callable"):
        validate_course_language_io_type(language="python", io_type=io_type, course_code="08")
        validate_course_language_io_type(language="bash", io_type=io_type, course_code="07")

    with pytest.raises(ManifestInvalid):
        validate_course_language_io_type(language="bash", io_type="stdin", course_code="08")

    with pytest.raises(ManifestInvalid):
        validate_course_language_io_type(language="python", io_type="callable", course_code="07")

    with pytest.raises(ManifestInvalid):
        validate_course_language_io_type(language="python", io_type="unknown")


def test_revision_diff_excludes_adapter_kind_changed() -> None:
    assert "adapter_kind_changed" not in REVISION_DIFF_KEYS
    prev = {
        "case_count": 11,
        "stem_length": 10,
        "case_ordinals": [0, 1],
        "case_weights": {0: 1.0, 1: 1.0},
        "fixture_digests": [],
        "max_files": 20,
        "max_total_bytes": 1024,
        "reference_answer_digest": "a" * 64,
    }
    diff = compute_revision_diff(prev, prev)
    assert set(diff.keys()) == REVISION_DIFF_KEYS
    assert "adapter_kind_changed" not in diff


def test_public_serializers_include_io_type_and_strip_legacy_diff_key() -> None:
    now = datetime.now(UTC)
    q_row = SimpleNamespace(
        question_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        title="Sample",
        stem_markdown="Read file",
        language="python",
        io_type="file",
        status="published",
        tags=["io"],
        created_at=now,
        updated_at=now,
    )
    q_pub = serialize_public(question_to_public(q_row))
    assert q_pub["io_type"] == "file"

    v_row = SimpleNamespace(
        question_version_id=uuid.uuid4(),
        question_id=q_row.question_id,
        version=1,
        status="published",
        language="python",
        adapter_manifest={
            "kind": "callable",
            "callable": {"module": "main", "attribute": "solve"},
            "files": [{"path": "out.txt", "content_sha256": "b" * 64, "mount": "expected_output"}],
        },
        execution_manifest={"mode": "per_case"},
        stem_markdown="Implement solve",
        content_digest="c" * 64,
        revision_note=None,
        revision_diff={"adapter_kind_changed": False, "stem_length_before": 1, "stem_length_after": 2},
        published_at=now,
        max_files=20,
        max_total_bytes=1024,
    )
    v_pub = serialize_public(version_to_public(v_row))
    assert v_pub["io_type"] == "callable"
    assert v_pub["io_spec"]["callable_module"] == "main"
    assert v_pub["io_spec"]["callable_attribute"] == "solve"
    assert v_pub["io_spec"]["expected_output_paths"] == ["out.txt"]
    assert "adapter_kind_changed" not in v_pub["revision_diff"]

    cov = TagCoverage(
        course_id=q_row.course_id,
        total_questions=3,
        tagged_questions=2,
        untagged_questions=1,
        tag_counts=(("io", 2),),
        by_io_type=(("stdin", 1), ("file", 1), ("callable", 1)),
    )
    cov_pub = serialize_public(coverage_to_public(cov))
    assert cov_pub["by_io_type"] == [
        {"io_type": "stdin", "question_count": 1},
        {"io_type": "file", "question_count": 1},
        {"io_type": "callable", "question_count": 1},
    ]

    lc = LearningContext(
        question_version_id=v_row.question_version_id,
        title="Sample",
        stem_markdown="Solve",
        language="python",
        tags=("io",),
        io_type="callable",
        adapter_kind="callable",
    )
    lc_pub = serialize_public(learning_context_to_public(lc))
    assert lc_pub["io_type"] == "callable"
