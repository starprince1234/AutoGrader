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


def test_compile_package_and_content_digest_include_visibility_and_case_files() -> None:
    from autograder_b3.compile.compiler import compile_evaluation_package
    from autograder_b3.domain.content import content_wire
    from autograder_b3.dto.internal import (
        AdapterManifestInternal,
        ComparisonPolicy,
        DeclaredFile,
        ExecutionManifest,
        FixtureInternal,
        PublishedVersionSnapshot,
        ReferenceAnswerInternal,
        TestCaseInternal,
    )

    now = datetime.now(UTC)
    qid = uuid.uuid4()
    vid = uuid.uuid4()
    cid = uuid.uuid4()
    sha_in = "1" * 64
    sha_case = "2" * 64
    sha_out = "3" * 64

    manifest = AdapterManifestInternal(
        kind="file",
        comparison=ComparisonPolicy(),
        files=(
            DeclaredFile(path="sample.txt", content_sha256=sha_in, mount="input"),
            DeclaredFile(path="out.txt", content_sha256=sha_out, mount="expected_output"),
        ),
    )
    case = TestCaseInternal(
        case_id=cid,
        question_version_id=vid,
        name="case-0",
        ordinal=0,
        weight=1.0,
        visible_to_student=True,
    )
    fixtures = (
        FixtureInternal(
            fixture_id=uuid.uuid4(),
            question_version_id=vid,
            path="sample.txt",
            mount="input",
            content_sha256=sha_in,
            size_bytes=5,
            student_visible=True,
        ),
        FixtureInternal(
            fixture_id=uuid.uuid4(),
            question_version_id=vid,
            path="out.txt",
            mount="expected_output",
            content_sha256=sha_out,
            size_bytes=5,
            student_visible=False,
        ),
        FixtureInternal(
            fixture_id=uuid.uuid4(),
            question_version_id=vid,
            path="case_in.txt",
            mount="input",
            content_sha256=sha_case,
            size_bytes=7,
            student_visible=False,
            test_case_id=cid,
        ),
    )
    snapshot = PublishedVersionSnapshot(
        question_id=qid,
        question_version_id=vid,
        version=1,
        language="python",
        manifest=manifest,
        execution=ExecutionManifest(),
        max_files=20,
        max_total_bytes=1024,
        content_digest="0" * 64,
        published_at=now,
    )
    pkg = compile_evaluation_package(snapshot, manifest, (case,), fixtures, created_at=now)
    wire = pkg.to_wire()
    assert wire["adapter"]["files"][0]["student_visible"] is True
    assert "student_visible" not in wire["adapter"]["files"][1]
    assert wire["tests"][0]["files"] == [
        {"path": "case_in.txt", "content_sha256": sha_case, "mount": "input"}
    ]
    from autograder_b3.domain.content import VersionContentSnapshot

    vc = VersionContentSnapshot(
        question_id=qid,
        question_version_id=vid,
        version=1,
        language="python",
        stem_markdown="Read sample.txt",
        manifest=manifest,
        execution=ExecutionManifest(),
        cases=(case,),
        fixtures=fixtures,
        reference_answers=(
            ReferenceAnswerInternal(
                reference_answer_id=uuid.uuid4(),
                question_version_id=vid,
                language="python",
                body="print(1)",
            ),
        ),
        max_files=20,
        max_total_bytes=1024,
    )
    cw = content_wire(vc)
    assert any(f.get("student_visible") is True for f in cw["fixtures"])
    assert any(f.get("test_case_id") == str(cid) for f in cw["fixtures"])


def test_assignment_serializer_includes_question_items_with_io_type() -> None:
    from autograder_b3.api.serializers import assignment_to_public
    from autograder_b3.dto.public import AssignmentQuestionItemPublic

    now = datetime.now(UTC)
    aid = uuid.uuid4()
    cid = uuid.uuid4()
    vid = uuid.uuid4()
    qid = uuid.uuid4()
    row = SimpleNamespace(
        assignment_id=aid,
        course_id=cid,
        title="Sample Assignment",
        status="open",
        due_at=None,
        notification_state="scheduled",
        created_at=now,
        updated_at=now,
    )
    items = [
        AssignmentQuestionItemPublic(
            question_version_id=vid,
            question_id=qid,
            title="File Task",
            language="bash",
            io_type="file",
        )
    ]
    pub = serialize_public(assignment_to_public(row, [vid], ((), ()), items))
    assert pub["question_items"][0]["io_type"] == "file"
    assert pub["question_items"][0]["language"] == "bash"
