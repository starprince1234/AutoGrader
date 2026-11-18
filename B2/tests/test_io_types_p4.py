"""Unit tests for P4: per-case fixtures, bash callable harness, and reference entry naming."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from autograder_b2.domain.evidence import CaseEvidence
from autograder_b2.evaluation.explanation import explain_case
from autograder_b2.reference.verification import ReferenceVerificationService
from autograder_b2.runner.adapters import (
    HARNESS_FILENAME_BY_LANGUAGE,
    HARNESS_RESOLUTION_MARKER,
    build_execution_plan,
    prepare_case_workspace,
    render_callable_harness,
)
from autograder_b2.runner.fixtures import FixtureContent, materialize_fixtures
from autograder_b2.runner.package import parse_evaluation_package


def _base_package_payload(*, language: str = "bash", kind: str = "file") -> dict:
    case_id_1 = str(uuid.uuid4())
    case_id_2 = str(uuid.uuid4())
    in_bytes_1 = b"case1\n"
    in_bytes_2 = b"case2\n"
    sha_1 = hashlib.sha256(in_bytes_1).hexdigest()
    sha_2 = hashlib.sha256(in_bytes_2).hexdigest()
    adapter: dict = {
        "kind": kind,
        "comparison": {"stdout": "exact", "stderr": "ignored"},
    }
    if kind == "file":
        adapter["files"] = [
            {
                "path": "shared.txt",
                "content_sha256": sha_1,
                "mount": "input",
                "student_visible": True,
            }
        ]
    elif kind == "callable":
        adapter["callable"] = {
            "module": "solution",
            "attribute": "greet",
            "arguments_json": '["world"]',
        }
    return {
        "question_version_id": str(uuid.uuid4()),
        "package_digest": "a" * 64,
        "language": language,
        "adapter": adapter,
        "tests": [
            {
                "case_id": case_id_1,
                "name": "c1",
                "ordinal": 0,
                "expected_stdout": "ok\n",
                **({"files": [{"path": "in.txt", "content_sha256": sha_1, "mount": "input"}]} if kind == "file" else {}),
            },
            {
                "case_id": case_id_2,
                "name": "c2",
                "ordinal": 1,
                "expected_stdout": "ok2\n",
                **({"files": [{"path": "in.txt", "content_sha256": sha_2, "mount": "input"}]} if kind == "file" else {}),
            },
        ],
        "security_policy": {
            "network": False,
            "read_only_root": True,
            "allow_root": False,
            "max_wall_seconds": 5,
            "max_cpu_seconds": 5,
            "max_memory_bytes": 67108864,
            "max_processes": 16,
            "max_output_bytes": 1048576,
            "max_files_created": 20,
        },
        "max_files": 20,
        "max_total_bytes": 1048576,
        "created_at": "2026-11-18T12:00:00Z",
    }


@dataclass
class _StubFixtureProvider:
    items: tuple[FixtureContent, ...]

    def fetch_inputs(self, *, question_version_id: str) -> tuple[FixtureContent, ...]:
        return self.items


def test_parse_and_materialize_per_case_fixtures(tmp_path: Path) -> None:
    payload = _base_package_payload(language="python", kind="file")
    pkg = parse_evaluation_package(payload)
    assert pkg.adapter.files[0].student_visible is True
    assert len(pkg.tests[0].files) == 1
    assert len(pkg.tests[1].files) == 1

    b1 = b"case1\n"
    b2 = b"case2\n"
    provider = _StubFixtureProvider(
        items=(
            FixtureContent("shared.txt", hashlib.sha256(b1).hexdigest(), len(b1), b1, None),
            FixtureContent("in.txt", hashlib.sha256(b1).hexdigest(), len(b1), b1, pkg.tests[0].case_id),
            FixtureContent("in.txt", hashlib.sha256(b2).hexdigest(), len(b2), b2, pkg.tests[1].case_id),
        )
    )

    ws1 = tmp_path / "case1"
    ws1.mkdir()
    assert materialize_fixtures(pkg, ws1, provider, case=pkg.tests[0]) == ()
    assert (ws1 / "shared.txt").read_bytes() == b1
    assert (ws1 / "in.txt").read_bytes() == b1

    ws2 = tmp_path / "case2"
    ws2.mkdir()
    assert materialize_fixtures(pkg, ws2, provider, case=pkg.tests[1]) == ()
    assert (ws2 / "in.txt").read_bytes() == b2


def test_bash_callable_harness_and_reference_entry_name(tmp_path: Path) -> None:
    payload = _base_package_payload(language="bash", kind="callable")
    pkg = parse_evaluation_package(payload)
    plan = build_execution_plan(
        adapter=pkg.adapter,
        case=pkg.tests[0],
        policy=pkg.security_policy,
        language=pkg.language,
    )
    assert plan.harness_name == HARNESS_FILENAME_BY_LANGUAGE["bash"] == "__b2_harness__.sh"
    assert plan.entry_name == "__b2_harness__.sh"

    script = render_callable_harness("solution", "greet", '["world"]', language="bash")
    assert "source \"$__b2_script\"" in script
    assert "declare -F greet" in script
    assert HARNESS_RESOLUTION_MARKER in script

    ws = tmp_path / "bash_ws"
    ws.mkdir()
    problems = prepare_case_workspace(
        plan,
        ws,
        entry_module="solution",
        entry_attribute="greet",
        arguments_json='["world"]',
    )
    assert problems == ()
    assert (ws / "__b2_harness__.sh").is_file()
    view = SimpleNamespace(adapter_handle=pkg.adapter, handle=pkg)
    assert ReferenceVerificationService._reference_entry_name(view, "bash") == "solution.sh"
    assert ReferenceVerificationService._reference_entry_name(view, "python") == "solution.py"


def test_explanation_covers_bash_callable_and_file_errors() -> None:
    common = dict(
        ordinal=0,
        weight=1.0,
        passed=False,
        duration_ms=10,
        input_digest="a" * 64,
        stdout_digest="b" * 64,
        stderr_digest="c" * 64,
        actual_output_excerpt=None,
        expected_output_excerpt=None,
        exit_code=3,
        resource_limit_hit=None,
        sandbox_violations=(),
        message="err",
        evidence_digest="d" * 64,
    )
    ev_import = CaseEvidence(
        case_id=str(uuid.uuid4()),
        failure_category="import_error",
        **common,
    )
    assert "source" in explain_case(ev_import)

    ev_infra = CaseEvidence(
        case_id=str(uuid.uuid4()),
        failure_category="infrastructure_error",
        **common,
    )
    assert "技术故障" in explain_case(ev_infra)
