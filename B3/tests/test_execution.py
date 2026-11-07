"""Execution declarations: validation matrix, package and content-digest compatibility.

This file covers the two properties that make the Unix-homework extension safe to add to an
existing bank:

* **compatibility** -- a version that declares nothing (``per_case``, no per-case fields)
  compiles to exactly the package it used to, and hashes to exactly the content digest it used
  to. That is asserted against the *legacy* canonical payloads spelled out in the tests, not
  against "it still works": the point is that a digest frozen before this feature is still
  reproducible.
* **rejection** -- every mode/field contradiction the design baseline names is refused by
  ``compile.manifest`` with a ``ManifestInvalid`` (the ``422`` the API surfaces), so an
  unusable package cannot be published and B2 is not handed something it has to guess about.

No database is needed here; the HTTP-shaped consequences live in ``test_execution_api.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from autograder_b3.compile import (
    compile_evaluation_package,
    compute_package_digest,
    validate_adapter_manifest,
)
from autograder_b3.compile.canonical import digest_of
from autograder_b3.compile.manifest import (
    case_execution_from_stored,
    case_execution_payload,
    case_execution_to_storage,
    execution_manifest_to_storage,
    is_default_execution,
    validate_case_execution,
    validate_case_executions,
    validate_execution_manifest,
)
from autograder_b3.domain.content import build_snapshot, compute_content_digest, content_wire
from autograder_b3.dto.internal import (
    CaseExecution,
    DeclaredFile,
    ExecutionManifest,
    FileCheck,
    PublishedVersionSnapshot,
    TestCaseInternal,
)
from autograder_b3.errors import ManifestInvalid

from .conftest import contract_json
from .examples import (
    EXPECTED_FIXTURE_SHA,
    FILE_MANIFEST,
    INPUT_FIXTURE_SHA,
    build_fixtures,
)

PUBLISHED_AT = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
#: A whole-line marker, 8+ printable characters, no edge whitespace.
DELIMITER = "@@@-section-@@@"
SSH_PASSWORD = "correct horse battery staple"
SSH_CONFIG = {
    "host": "127.0.0.1",
    "port": 22,
    "username": "user01",
    "password": SSH_PASSWORD,
    "login_timeout_seconds": 10,
    "session_timeout_seconds": 10,
}
EMAIL_CONFIG = {"attempt": 1}


def _stdin_manifest():
    return validate_adapter_manifest(
        {"kind": "stdin", "stdin": "a\n", "expect_exit_code": 0}
    )


def _case(version_id, ordinal, *, execution=None, expected_stdout=None):
    return TestCaseInternal(
        case_id=uuid.uuid4(),
        question_version_id=version_id,
        name=f"case {ordinal}",
        ordinal=ordinal,
        expected_stdout=expected_stdout,
        execution=execution or CaseExecution(),
    )


def _snapshot(version_id, manifest, *, execution=None, language="python"):
    return PublishedVersionSnapshot(
        question_version_id=version_id,
        question_id=uuid.uuid4(),
        version=1,
        language=language,
        content_digest="b" * 64,
        published_at=PUBLISHED_AT,
        max_files=20,
        max_total_bytes=1 << 20,
        manifest=manifest,
        execution=execution or ExecutionManifest(),
    )


def _compile(version_id, manifest, cases, *, execution=None, fixtures=(), language="python"):
    return compile_evaluation_package(
        _snapshot(version_id, manifest, execution=execution, language=language),
        manifest,
        cases,
        fixtures,
        created_at=PUBLISHED_AT,
    )


def _validate_against_contract(package) -> None:
    import jsonschema

    schema = contract_json("schemas/evaluation-package.schema.json")
    jsonschema.validate(package.to_wire(), schema)


# ==========================================================================
# Compatibility: a default declaration is invisible on the wire
# ==========================================================================


def test_a_default_package_carries_no_execution_keys():
    version_id = uuid.uuid4()
    manifest = _stdin_manifest()
    cases = (_case(version_id, 0, expected_stdout="3\n"),)
    package = _compile(version_id, manifest, cases)
    wire = package.to_wire()

    assert set(wire) == {
        "question_version_id",
        "package_digest",
        "language",
        "adapter",
        "tests",
        "security_policy",
        "max_files",
        "max_total_bytes",
        "created_at",
    }
    assert "execution" not in wire
    for entry in wire["tests"]:
        assert {"segment", "file_checks", "ssh_check", "email_check"} & set(entry) == set()
    # The digest is the digest of exactly the bytes above; nothing hidden was added.
    assert compute_package_digest(wire) == package.package_digest
    _validate_against_contract(package)


def test_a_default_content_digest_is_the_legacy_payload():
    """The compatibility rule, asserted against the *pre-feature* canonical payload.

    ``content_wire`` used to return exactly the mapping spelled out below. If an
    ``execution`` key (or any per-case execution field) were added unconditionally, this
    digest -- and therefore every frozen ``content_digest`` in the bank -- would change.
    """
    version_id = uuid.uuid4()
    manifest = _stdin_manifest()
    case_id = uuid.uuid4()
    case = TestCaseInternal(
        case_id=case_id,
        question_version_id=version_id,
        name="three lines",
        ordinal=0,
        weight=2.0,
        visible_to_student=False,
        stdin="a\nb\nc\n",
        args=("--mode", "count"),
        expected_stdout="3\n",
        expected_stderr=None,
        expected_exit_code=0,
    )
    snapshot = build_snapshot(
        question_version_id=version_id,
        question_id=uuid.uuid4(),
        version=1,
        language="python",
        stem_markdown="Count the lines.",
        reference_answers=(),
        manifest=manifest,
        cases=(case,),
        fixtures=(),
        max_files=20,
        max_total_bytes=1 << 20,
    )
    wire = content_wire(snapshot)
    legacy = {
        "question_version_id": str(version_id),
        "question_id": str(snapshot.question_id),
        "version": 1,
        "language": "python",
        "stem_markdown": "Count the lines.",
        "reference_answers": [],
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": "a\n",
            "expect_exit_code": 0,
            "comparison": {"stdout": "exact", "stderr": "ignored", "numeric_tolerance": None},
            "files": [],
        },
        "cases": [
            {
                "case_id": str(case_id),
                "name": "three lines",
                "ordinal": 0,
                "weight": 2.0,
                "visible_to_student": False,
                "stdin": "a\nb\nc\n",
                "args": ["--mode", "count"],
                "expected_stdout": "3\n",
                "expected_stderr": None,
                "expected_exit_code": 0,
            }
        ],
        "fixtures": [],
        "max_files": 20,
        "max_total_bytes": 1 << 20,
    }
    assert wire == legacy
    assert "execution" not in wire
    assert compute_content_digest(snapshot) == digest_of(legacy)


def test_an_explicit_per_case_declaration_normalizes_away():
    execution = validate_execution_manifest({"mode": "per_case"})
    assert is_default_execution(execution) is True
    assert execution_manifest_to_storage(execution) is None
    assert execution_manifest_to_storage(ExecutionManifest()) is None


def test_a_declared_mode_adds_exactly_the_execution_keys():
    version_id = uuid.uuid4()
    manifest = _stdin_manifest()
    execution = validate_execution_manifest(
        {"mode": "single_run", "segment_delimiter": DELIMITER}
    )
    cases = (
        _case(
            version_id,
            0,
            expected_stdout="one\n",
            execution=CaseExecution(segment=0),
        ),
        _case(
            version_id,
            1,
            expected_stdout="two\n",
            execution=CaseExecution(segment=1),
        ),
    )
    package = _compile(version_id, manifest, cases, execution=execution)
    wire = package.to_wire()
    assert wire["execution"] == {
        "mode": "single_run",
        "segment_delimiter": DELIMITER,
    }
    assert [entry["segment"] for entry in wire["tests"]] == [0, 1]
    assert "file_checks" not in wire["tests"][0]
    _validate_against_contract(package)

    # The content digest gains the same declaration, so "what the runner executes" and "what
    # the digest covers" are the same fact.
    snapshot = build_snapshot(
        question_version_id=version_id,
        question_id=uuid.uuid4(),
        version=1,
        language="python",
        stem_markdown="stem",
        reference_answers=(),
        manifest=manifest,
        cases=cases,
        fixtures=(),
        max_files=20,
        max_total_bytes=1 << 20,
        execution=execution,
    )
    content = content_wire(snapshot)
    assert content["execution"] == {"mode": "single_run", "segment_delimiter": DELIMITER}
    assert [entry["segment"] for entry in content["cases"]] == [0, 1]


def test_a_non_default_declaration_changes_the_content_digest():
    version_id = uuid.uuid4()
    manifest = _stdin_manifest()
    execution = validate_execution_manifest({"mode": "ssh_session", "ssh": SSH_CONFIG})
    case = _case(
        version_id,
        0,
        execution=CaseExecution(ssh_check="service_ready"),
    )
    default_snapshot = build_snapshot(
        question_version_id=version_id,
        question_id=uuid.uuid4(),
        version=1,
        language="bash",
        stem_markdown="stem",
        reference_answers=(),
        manifest=manifest,
        cases=(case,),
        fixtures=(),
        max_files=20,
        max_total_bytes=1 << 20,
    )
    declared_snapshot = build_snapshot(
        question_version_id=version_id,
        question_id=default_snapshot.question_id,
        version=1,
        language="bash",
        stem_markdown="stem",
        reference_answers=(),
        manifest=manifest,
        cases=(case,),
        fixtures=(),
        max_files=20,
        max_total_bytes=1 << 20,
        execution=execution,
    )
    assert compute_content_digest(default_snapshot) != compute_content_digest(declared_snapshot)


# ==========================================================================
# Rejection matrix
# ==========================================================================


def _apply_declaration(raw, case_specs, *, adapter_kind="stdin", language="bash"):
    """Validate a declaration and its case set; raises on any contradiction."""
    execution = validate_execution_manifest(raw)
    version_id = uuid.uuid4()
    cases = tuple(
        _case(
            version_id,
            spec.get("ordinal", index),
            expected_stdout=spec.get("expected_stdout"),
            execution=validate_case_execution(spec.get("execution"), mode=execution.mode),
        )
        for index, spec in enumerate(case_specs)
    )
    validate_case_executions(
        cases,
        execution=execution,
        adapter_kind=adapter_kind,
        language=language,
    )


REJECTED_DECLARATIONS = [
    # --- version-level contradictions ------------------------------------
    pytest.param(
        {"mode": "per_case", "segment_delimiter": DELIMITER}, [], {}, id="per_case-delimiter"
    ),
    pytest.param({"mode": "per_case", "email": EMAIL_CONFIG}, [], {}, id="per_case-email"),
    pytest.param(
        {"mode": "per_case", "ssh": SSH_CONFIG}, [], {}, id="per_case-ssh"
    ),
    pytest.param(
        {"mode": "single_run", "email": EMAIL_CONFIG},
        [{"execution": {"segment": 0}, "expected_stdout": "x\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-email",
    ),
    pytest.param(
        {"mode": "single_run", "ssh": SSH_CONFIG},
        [{"execution": {"segment": 0}, "expected_stdout": "x\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-ssh",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG, "segment_delimiter": DELIMITER},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh_session-delimiter",
    ),
    pytest.param(
        {"mode": "ssh_session"},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh_session-missing-config",
    ),
    pytest.param(
        {"mode": "email_delivery"},
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email_delivery-missing-config",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG, "ssh": SSH_CONFIG},
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email_delivery-with-ssh",
    ),
    pytest.param({"mode": "not_a_mode"}, [], {}, id="unknown-mode"),
    pytest.param({}, [], {}, id="missing-mode"),
    # --- SSH configuration -------------------------------------------------
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "host": "10.0.0.1"}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-host-not-loopback",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "port": 0}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-port-zero",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "port": 65536}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-port-too-large",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "username": "Root"}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-username-invalid",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "password": ""}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-password-empty",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "login_timeout_seconds": 0}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-timeout-zero",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "login_timeout_seconds": 0.05}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-timeout-below-b2-minimum",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "password": "pass\tword"}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-password-non-printable",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": {**SSH_CONFIG, "session_timeout_seconds": 31}},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-timeout-too-large",
    ),
    # --- email configuration ----------------------------------------------
    pytest.param(
        {"mode": "email_delivery", "email": {"attempt": 0}},
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-attempt-zero",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": {"attempt": 100}},
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-attempt-too-large",
    ),
    pytest.param(
        {
            "mode": "email_delivery",
            "email": {"attempt": 1, "subject_template": "line1\nline2"},
        },
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-subject-multiline",
    ),
    pytest.param(
        {
            "mode": "email_delivery",
            "email": {"attempt": 1, "subject_template": "{student_id}_{attempt}"},
        },
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-subject-unknown-placeholder",
    ),
    pytest.param(
        {
            "mode": "email_delivery",
            "email": {"attempt": 1, "subject_template": "{attempt}_{attempt}"},
        },
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-subject-repeated-placeholder",
    ),
    # --- segment delimiter format -----------------------------------------
    pytest.param(
        {"mode": "single_run", "segment_delimiter": "short"},
        [{"execution": {"segment": 0}, "expected_stdout": "x\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="delimiter-too-short",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": " " + "x" * 8},
        [{"execution": {"segment": 0}, "expected_stdout": "x\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="delimiter-leading-space",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": "x" * 8 + " "},
        [{"execution": {"segment": 0}, "expected_stdout": "x\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="delimiter-trailing-space",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": "@" * 5 + "é" + "@" * 3},
        [{"execution": {"segment": 0}, "expected_stdout": "x\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="delimiter-non-ascii",
    ),
    # --- single_run case rules --------------------------------------------
    pytest.param(
        {"mode": "single_run"},
        [{"execution": {"segment": 0}, "expected_stdout": "a\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-missing-delimiter",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "a.txt", "mode": "0644"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-delimiter-without-segments",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [
            {"execution": {"segment": 0}, "expected_stdout": "a\n"},
            {"execution": {"segment": 2}, "expected_stdout": "b\n"},
        ],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-incomplete-segments",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [
            {"execution": {"segment": 0}, "expected_stdout": "a\n"},
            {"execution": {"segment": 0}, "expected_stdout": "b\n"},
        ],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-duplicate-segment",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [
            {
                "execution": {
                    "segment": 0,
                    "file_checks": [{"path": "a.txt", "mode": "0644"}],
                }
            }
        ],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-both-segment-and-file-checks",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="single_run-neither-segment-nor-file-checks",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"segment": 0}, "expected_stdout": "a\n"}],
        {"adapter_kind": "callable", "language": "python"},
        id="single_run-callable-adapter",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"segment": 0}, "expected_stdout": "a\n"}],
        {"adapter_kind": "file", "language": "python"},
        id="single_run-file-adapter-segments",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"segment": 0}, "expected_stdout": DELIMITER + "\n"}],
        {"adapter_kind": "stdin", "language": "python"},
        id="delimiter-inside-expected-output",
    ),
    # --- ssh_session case rules ------------------------------------------
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [{"execution": {}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh_case-without-check",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [{"execution": {"ssh_check": "service_ready", "segment": 0}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh_case-with-segment",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [{"execution": {"ssh_check": "service_ready", "email_check": "sender"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh_case-with-email-check",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [
            {
                "execution": {
                    "ssh_check": "service_ready",
                    "file_checks": [{"path": "a.txt", "mode": "0644"}],
                }
            }
        ],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh_case-with-file-checks",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [
            {"execution": {"ssh_check": "service_ready"}},
            {"execution": {"ssh_check": "service_ready"}},
        ],
        {"adapter_kind": "stdin", "language": "bash"},
        id="ssh-duplicate-check",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "file", "language": "bash"},
        id="ssh-file-adapter",
    ),
    pytest.param(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="ssh-python-language",
    ),
    # --- email_delivery case rules ---------------------------------------
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG},
        [{"execution": {}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-case-without-check",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG},
        [{"execution": {"email_check": "email_received", "segment": 0}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-case-with-segment",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG},
        [{"execution": {"email_check": "email_received", "ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-case-with-ssh-check",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG},
        [{"execution": {"email_check": "email_received", "file_checks": [{"path": "a", "mode": "0644"}]}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-case-with-file-checks",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG},
        [
            {"execution": {"email_check": "sender"}},
            {"execution": {"email_check": "sender"}},
        ],
        {"adapter_kind": "stdin", "language": "bash"},
        id="email-duplicate-check",
    ),
    pytest.param(
        {"mode": "email_delivery", "email": EMAIL_CONFIG},
        [{"execution": {"email_check": "email_received"}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="email-python-language",
    ),
    # --- per-case shapes (mode-independent) ------------------------------
    pytest.param(
        {"mode": "per_case"},
        [{"execution": {"segment": 0}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="per_case-case-with-segment",
    ),
    pytest.param(
        {"mode": "per_case"},
        [{"execution": {"ssh_check": "service_ready"}}],
        {"adapter_kind": "stdin", "language": "bash"},
        id="per_case-case-with-ssh-check",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "../escape.txt", "mode": "0644"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-path-traversal",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "a.txt", "mode": "0644", "content_sha256": "XYZ"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-digest-invalid",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "a.txt", "mode": "999"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-mode-invalid",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "a.txt", "mode": "644"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-mode-three-digit",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "a.txt", "mode": "1744"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-mode-not-leading-zero",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [{"execution": {"file_checks": [{"path": "a.txt"}]}}],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-asserts-nothing",
    ),
    pytest.param(
        {"mode": "single_run", "segment_delimiter": DELIMITER},
        [
            {
                "execution": {
                    "file_checks": [
                        {"path": "a.txt", "mode": "0644"},
                        {"path": "a.txt", "mode": "0600"},
                    ]
                }
            }
        ],
        {"adapter_kind": "stdin", "language": "python"},
        id="file-check-duplicate-path",
    ),
]


@pytest.mark.parametrize(("raw", "case_specs", "context"), REJECTED_DECLARATIONS)
def test_execution_declaration_rejections(raw, case_specs, context):
    with pytest.raises(ManifestInvalid):
        _apply_declaration(raw, case_specs, **context)


# ==========================================================================
# Accepted declarations (the matrix above must not be over-restrictive)
# ==========================================================================


def test_ssh_session_declaration_is_accepted():
    execution = _apply_declaration_accept(
        {"mode": "ssh_session", "ssh": SSH_CONFIG},
        [
            {"execution": {"ssh_check": "service_ready"}},
            {"execution": {"ssh_check": "auth_succeeded"}},
            {"execution": {"ssh_check": "session_closed"}},
        ],
        adapter_kind="stdin",
        language="bash",
    )
    assert execution.mode == "ssh_session"
    assert execution.ssh is not None
    assert execution.ssh.password == SSH_PASSWORD
    assert execution.ssh.login_timeout_seconds == 10.0


def test_email_delivery_declaration_is_accepted_and_defaults_are_filled():
    execution = validate_execution_manifest({"mode": "email_delivery", "email": {"attempt": 2}})
    assert execution.email is not None
    assert execution.email.attempt == 2
    assert execution.email.subject_template == "{student_number}_{attempt}"
    # CHG-0032 removed the per-question attachment template: the answer file is named by
    # the question's position in the assignment, not declared here.
    assert not hasattr(execution.email, "required_attachment_template")


def test_email_delivery_storage_never_carries_the_removed_attachment_template():
    """The package/digest payload must not grow back the removed key.

    B2's package parser is strict (``additionalProperties: false``) and refuses a package
    with ``required_attachment_template``; emitting it again would make every
    ``email_delivery`` question unpublishable and un-evaluable.
    """
    execution = validate_execution_manifest({"mode": "email_delivery", "email": {"attempt": 1}})
    assert execution_manifest_to_storage(execution) == {
        "mode": "email_delivery",
        "email": {"attempt": 1, "subject_template": "{student_number}_{attempt}"},
    }


def test_single_run_file_question_with_file_checks_is_accepted():
    version_id = uuid.uuid4()
    execution = validate_execution_manifest({"mode": "single_run"})
    declared = tuple(
        DeclaredFile(
            path=item["path"],
            content_sha256=item["content_sha256"],
            mount=item["mount"],
        )
        for item in FILE_MANIFEST["files"]
    )
    fixtures = build_fixtures(version_id, declared)
    manifest = validate_adapter_manifest(FILE_MANIFEST, cases=(), fixtures=fixtures)
    cases = (
        _case(
            version_id,
            0,
            execution=CaseExecution(
                file_checks=(
                    FileCheck(path="out/report.txt", content_sha256=EXPECTED_FIXTURE_SHA),
                )
            ),
        ),
        _case(
            version_id,
            1,
            execution=CaseExecution(
                file_checks=(FileCheck(path="data/input.csv", mode="0644"),)
            ),
        ),
    )
    validate_case_executions(
        cases, execution=execution, adapter_kind="file", language="python"
    )
    package = _compile(version_id, manifest, cases, execution=execution, fixtures=fixtures)
    _validate_against_contract(package)
    checks = package.to_wire()["tests"][0]["file_checks"]
    assert checks == [
        {
            "path": "out/report.txt",
            "content_sha256": EXPECTED_FIXTURE_SHA,
            "mode": None,
        }
    ]
    assert package.to_wire()["tests"][1]["file_checks"][0]["path"] == "data/input.csv"


def test_stdin_question_with_declared_files_is_compiled_into_the_package():
    """The legacy omission: `stdin` adapters dropped declared fixtures from the package.

    The contract allows `files` for every kind, and a fixture digest without a delivery path
    is exactly what the runner cannot materialise (design baseline §5).
    """
    version_id = uuid.uuid4()
    declared = (
        DeclaredFile(path="data/input.csv", content_sha256=INPUT_FIXTURE_SHA, mount="input"),
    )
    fixtures = build_fixtures(version_id, declared)
    manifest = validate_adapter_manifest(
        {"kind": "stdin", "files": [{"path": "data/input.csv", "content_sha256": INPUT_FIXTURE_SHA, "mount": "input"}]},
        cases=(),
        fixtures=fixtures,
    )
    cases = (_case(version_id, 0, expected_stdout="ok\n"),)
    package = _compile(version_id, manifest, cases, fixtures=fixtures)
    files = package.to_wire()["adapter"]["files"]
    assert files == [
        {"path": "data/input.csv", "content_sha256": INPUT_FIXTURE_SHA, "mount": "input"}
    ]
    _validate_against_contract(package)


# ==========================================================================
# Storage round trip
# ==========================================================================


def test_case_execution_storage_round_trips_through_the_canonical_shape():
    segment_case = validate_case_execution({"segment": 3}, mode="single_run")
    assert case_execution_to_storage(segment_case) == {"segment": 3}
    assert case_execution_from_stored({"segment": 3}) == segment_case

    file_case = validate_case_execution(
        {"file_checks": [{"path": "a.txt", "mode": "0744"}]},
        mode="single_run",
    )
    stored = case_execution_to_storage(file_case)
    assert stored == {
        "file_checks": [{"path": "a.txt", "content_sha256": None, "mode": "0744"}]
    }
    assert case_execution_from_stored(stored) == file_case
    assert case_execution_payload(file_case) == stored


def test_an_empty_case_declaration_is_stored_as_null():
    empty = validate_case_execution({}, mode="per_case")
    assert empty == CaseExecution()
    assert case_execution_to_storage(empty) is None
    assert case_execution_from_stored(None) == CaseExecution()


# ==========================================================================
# Helper: an accepting variant of the matrix applicator
# ==========================================================================


def _apply_declaration_accept(raw, case_specs, *, adapter_kind, language):
    execution = validate_execution_manifest(raw)
    version_id = uuid.uuid4()
    cases = tuple(
        _case(
            version_id,
            spec.get("ordinal", index),
            expected_stdout=spec.get("expected_stdout"),
            execution=validate_case_execution(spec.get("execution"), mode=execution.mode),
        )
        for index, spec in enumerate(case_specs)
    )
    validate_case_executions(
        cases,
        execution=execution,
        adapter_kind=adapter_kind,
        language=language,
    )
    return execution
