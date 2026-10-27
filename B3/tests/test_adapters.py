"""TC-ADP-01/02/03/04: the three adapters and the frozen package contract.

The three example questions in :mod:`tests.examples` are real manifests, compiled
through the single entry point and validated against
``MAIN/contracts/schemas/evaluation-package.schema.json`` - the contract file is
loaded from MAIN at test time, never copied into B3.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from autograder_b3.compile import (
    compile_evaluation_package,
    compute_package_digest,
    registered_adapter_kinds,
    register_builtin_adapters,
    unregister_adapter_compiler,
    validate_adapter_manifest,
)
from autograder_b3.compile.registry import register_adapter_compiler
from autograder_b3.dto.internal import (
    DeclaredFile,
    FixtureInternal,
    PublishedVersionSnapshot,
)
from autograder_b3.errors import ManifestInvalid, PathNotAllowed

from .conftest import contract_json
from .examples import (
    CALLABLE_MANIFEST,
    FILE_MANIFEST,
    STDIN_BASH_EXAMPLE,
    STDIN_MANIFEST,
    build_cases,
    build_fixtures,
)

PUBLISHED_AT = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def snapshot(
    version_id: uuid.UUID,
    manifest,
    *,
    max_files: int = 20,
    max_total_bytes: int = 1 << 20,
    published_at: datetime = PUBLISHED_AT,
):
    return PublishedVersionSnapshot(
        question_version_id=version_id,
        question_id=uuid.uuid4(),
        version=1,
        language="python",
        content_digest="b" * 64,
        published_at=published_at,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        manifest=manifest,
    )


def validate_against_contract(package) -> None:
    import jsonschema

    schema = contract_json("schemas/evaluation-package.schema.json")
    jsonschema.validate(package.to_wire(), schema)


# --- TC-ADP-01: stdin / argv --------------------------------------------


# --- comparison-policy fitness diagnostics (deterministic, no model) ----


def diagnostics_for(manifest: dict, outputs: list[str | None]):
    """Run the fitness rules over a manifest and a set of expected stdout values."""
    from autograder_b3.compile.diagnostics import Rule, analyse_comparison, rule_by_id

    from .examples import build_cases

    # A legal stdin adapter must declare at least one exercise; the diagnostics only
    # care about the comparison policy, so the exercise itself is boilerplate.
    validated = validate_adapter_manifest(
        {"stdin": "a\n", "args": [], **manifest, "expect_exit_code": 0}
    )
    rows = [
        {
            "name": "case",
            "ordinal": index,
            "expected_stdout": output,
            "expected_exit_code": 0,
        }
        for index, output in enumerate(outputs)
    ]
    cases = build_cases(uuid.uuid4(), rows)
    report = analyse_comparison(validated, cases)
    assert all(isinstance(rule_by_id(item.rule_id), Rule) for item in report.findings)
    return report


def test_a_crlf_expected_output_is_an_error():
    """Traceable to a real bank failure: Windows CRLF against an LF-canonical harness."""
    manifest = {"kind": "stdin", "comparison": {"stdout": "ignore_trailing_whitespace"}}
    report = diagnostics_for(manifest, ["3\r\n", "0\n"])
    assert report.passed is False
    crlf = [item for item in report.findings if item.rule_id == "CMP-001"]
    assert len(crlf) == 1
    assert crlf[0].case_ordinal == 0
    assert crlf[0].severity == "error"


def test_a_non_ascii_expected_output_is_reported_as_a_warning():
    """Traceable to a real bank failure: GBK-vs-UTF-8 stdin decoding produced mojibake."""
    manifest = {"kind": "stdin", "comparison": {"stdout": "exact"}}
    report = diagnostics_for(manifest, ["café\n", "plain\n"])
    encoding = [item for item in report.findings if item.rule_id == "CMP-002"]
    assert len(encoding) == 1
    assert encoding[0].case_ordinal == 0
    assert encoding[0].severity == "warning"
    # A warning never blocks a publish on its own.
    assert report.passed is True


def test_trailing_whitespace_under_exact_comparison_is_informational():
    manifest = {"kind": "stdin", "comparison": {"stdout": "exact"}}
    report = diagnostics_for(manifest, ["3   \n", "0"])
    notes = [item for item in report.findings if item.rule_id == "CMP-003"]
    assert [item.case_ordinal for item in notes] == [0]
    # `info`, not `note`: the contract's `severity` enum is error|warning|info, and a
    # finding whose severity is not a declared value could not be serialized honestly.
    assert notes[0].severity == "info"
    assert report.passed is True


def test_numeric_tolerance_without_a_value_is_an_error():
    manifest = {"kind": "stdin", "comparison": {"stdout": "numeric_tolerance"}}
    report = diagnostics_for(manifest, ["3.14\n"])
    assert [item.rule_id for item in report.errors] == ["CMP-004"]
    assert report.passed is False


def test_a_well_formed_policy_produces_no_findings():
    manifest = {
        "kind": "stdin",
        "comparison": {
            "stdout": "ignore_trailing_whitespace",
            "stderr": "ignored",
            "numeric_tolerance": None,
        },
    }
    report = diagnostics_for(manifest, ["3\n", "0\n"])
    assert report.findings == ()
    assert report.passed is True
    assert report.cases_inspected == 2
    assert report.adapter_kind == "stdin"


def test_every_rule_declares_the_failure_it_prevents():
    from autograder_b3.compile.diagnostics import RULES

    assert len(RULES) == 4
    for rule in RULES:
        assert rule.rule_id.startswith("CMP-")
        # Exactly the declared enum; a fifth word would be unrepresentable in the
        # `ComparisonDiagnostics` response (CHG-0013).
        assert rule.severity in ("error", "warning", "info")
        assert rule.title and rule.rationale
        # A rule that cannot say which real failure it prevents does not ship.
        assert len(rule.rationale) > 60


# --- publishing through the API ------------------------------------------


def test_stdin_adapter_compiles_a_contract_valid_package():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = build_cases(version_id, STDIN_BASH_EXAMPLE["cases"])
    package = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    validate_against_contract(package)
    wire = package.to_wire()
    assert wire["adapter"]["kind"] == "stdin"
    assert wire["adapter"]["args"] == ["--mode", "count"]
    assert wire["adapter"]["stdin"] == "a\nb\nc\n"
    assert wire["adapter"]["expect_exit_code"] == 0
    assert wire["adapter"]["comparison"]["stdout"] == "ignore_trailing_whitespace"
    assert wire["tests"][1]["visible_to_student"] is False
    assert wire["tests"][1]["expected_stdout"] == "0\n"


def test_stdin_adapter_keeps_shell_metacharacters_literal():
    """acceptance 3-40-2: args are an array, never a shell string."""
    hostile = ["; rm -rf /", "$(whoami)", "| tee /etc/passwd", "`id`", "&& echo pwned", "a b"]
    manifest = validate_adapter_manifest({"kind": "stdin", "args": hostile, "stdin": ""})
    version_id = uuid.uuid4()
    cases = build_cases(version_id, STDIN_BASH_EXAMPLE["cases"])
    package = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    assert package.to_wire()["adapter"]["args"] == hostile


def test_stdin_adapter_rejects_a_shell_string_instead_of_an_array():
    with pytest.raises(ManifestInvalid) as error:
        validate_adapter_manifest({"kind": "stdin", "args": "python main.py --x"})
    assert "array" in str(error.value)


def test_stdin_adapter_requires_some_input_or_exit_code():
    with pytest.raises(ManifestInvalid):
        validate_adapter_manifest({"kind": "stdin"})


# --- TC-ADP-02: file I/O --------------------------------------------------


def test_file_adapter_binds_fixture_digests_into_the_package():
    version_id = uuid.uuid4()
    declared = tuple(
        DeclaredFile(path=item["path"], content_sha256=item["content_sha256"], mount=item["mount"])
        for item in FILE_MANIFEST["files"]
    )
    fixtures = build_fixtures(version_id, declared)
    manifest = validate_adapter_manifest(FILE_MANIFEST, cases=(), fixtures=fixtures)
    cases = build_cases(version_id, [{"name": "process", "ordinal": 0, "expected_stdout": "ok\n"}])
    package = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, fixtures, created_at=PUBLISHED_AT
    )
    validate_against_contract(package)
    files = package.to_wire()["adapter"]["files"]
    assert {entry["mount"] for entry in files} == {"input", "expected_output"}
    for entry in files:
        assert entry["content_sha256"] in {item.content_sha256 for item in fixtures}


def test_file_adapter_requires_an_input_fixture_at_compile_time():
    """A file question may be *drafted* without fixtures, but never compiled without them.

    The requirement used to live in ``validate``, which runs on every draft write. That made
    the real authoring order impossible: fixture bytes are uploaded to
    ``POST /question-versions/{id}/fixtures``, which needs the ``question_version_id`` that
    only exists *after* the draft is saved, so the first save was refused for missing the
    very fixture the upload it enables would have supplied. The check therefore belongs to
    package compilation, which runs on publish and on verification -- exactly the moments an
    incomplete file question must not slip through.
    """
    draft_manifest = validate_adapter_manifest({"kind": "file", "comparison": {"stdout": "exact"}})
    assert draft_manifest.kind == "file"
    assert draft_manifest.files == ()

    version_id = uuid.uuid4()
    cases = build_cases(version_id, [{"name": "process", "ordinal": 0, "expected_stdout": "ok\n"}])
    with pytest.raises(ManifestInvalid) as error:
        compile_evaluation_package(
            snapshot(version_id, draft_manifest),
            draft_manifest,
            cases,
            (),
            created_at=PUBLISHED_AT,
        )
    assert "input fixture" in str(error.value)


def test_file_adapter_declared_digest_must_match_stored_content():
    version_id = uuid.uuid4()
    fixtures = build_fixtures(version_id, (DeclaredFile(path="data/in.txt", content_sha256="c" * 64),))
    lying = {"kind": "file", "files": [{"path": "data/in.txt", "content_sha256": "d" * 64, "mount": "input"}]}
    with pytest.raises(ManifestInvalid) as error:
        validate_adapter_manifest(lying, fixtures=fixtures)
    assert "digest" in str(error.value)


def test_file_adapter_rejects_undeclared_stored_fixtures():
    version_id = uuid.uuid4()
    fixtures = (
        FixtureInternal(uuid.uuid4(), version_id, "data/in.txt", "input", "c" * 64, 4),
        FixtureInternal(uuid.uuid4(), version_id, "data/extra.txt", "input", "d" * 64, 4),
    )
    only_one = {"kind": "file", "files": [{"path": "data/in.txt", "content_sha256": "c" * 64, "mount": "input"}]}
    with pytest.raises(ManifestInvalid) as error:
        validate_adapter_manifest(only_one, fixtures=fixtures)
    assert "missing from the manifest" in str(error.value)


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../outside.txt",
        "data/../../escape.txt",
        "C:/windows/system32",
        "data\\windows",
        "~/secrets",
        "data//double.txt",
        "data/./here.txt",
        "",
        "a" * 201,
    ],
)
def test_workspace_paths_reject_traversal_and_absolute_paths(path):
    """acceptance 3-50-2: absolute paths and '..' are refused."""
    manifest = {"kind": "file", "files": [{"path": path, "content_sha256": "c" * 64, "mount": "input"}]}
    with pytest.raises((PathNotAllowed, ManifestInvalid)):
        validate_adapter_manifest(manifest)


def test_duplicate_fixture_declaration_is_rejected():
    manifest = {
        "kind": "file",
        "files": [
            {"path": "data/in.txt", "content_sha256": "c" * 64, "mount": "input"},
            {"path": "data/in.txt", "content_sha256": "d" * 64, "mount": "input"},
        ],
    }
    with pytest.raises(ManifestInvalid):
        validate_adapter_manifest(manifest)


# --- TC-ADP-03: callable --------------------------------------------------


def test_callable_adapter_compiles_and_confines_the_import_target():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(CALLABLE_MANIFEST)
    cases = build_cases(
        version_id,
        [
            {
                "name": "adds two numbers",
                "ordinal": 0,
                "expected_stdout": "5\n",
                "args": [],
            }
        ],
    )
    package = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    validate_against_contract(package)
    section = package.to_wire()["adapter"]
    assert section["callable"]["module"] == "solution.arithmetic"
    assert section["callable"]["attribute"] == "add"
    assert section["callable"]["arguments_json"] == "[1, 4]"


@pytest.mark.parametrize(
    "target",
    [
        {"module": "/etc/passwd", "attribute": "x"},
        {"module": "../../outside", "attribute": "x"},
        {"module": "http://example.com/mod", "attribute": "x"},
        {"module": "solution.arithmetic", "attribute": "add; os.system('id')"},
        {"module": "solution..arithmetic", "attribute": "add"},
        {"module": "solution.arithmetic", "attribute": ""},
    ],
)
def test_callable_target_must_be_a_workspace_import(target):
    """acceptance 3-60-2: no arbitrary path and no network loading."""
    with pytest.raises(ManifestInvalid):
        validate_adapter_manifest({"kind": "callable", "callable": target})


def test_callable_arguments_json_must_be_json():
    with pytest.raises(ManifestInvalid):
        validate_adapter_manifest(
            {"kind": "callable", "callable": {"module": "m", "attribute": "f", "arguments_json": "os.system(1)"}}
        )
    manifest = validate_adapter_manifest(
        {"kind": "callable", "callable": {"module": "m", "attribute": "f", "arguments_json": '{"a": 1}'}}
    )
    assert manifest.callable is not None and manifest.callable.arguments_json == '{"a": 1}'


def test_callable_adapter_requires_an_entry_point():
    with pytest.raises(ManifestInvalid):
        validate_adapter_manifest({"kind": "callable", "stdin": "x"})


# --- TC-ADP-04: shared structure, single entry point, extension point -----


def test_all_three_adapters_produce_the_same_package_shape():
    """acceptance 3-60-4: one package structure, three adapter semantics."""
    version_id = uuid.uuid4()
    cases = build_cases(version_id, [{"name": "c", "ordinal": 0, "expected_stdout": "x\n"}])
    shapes = {}
    for raw in (STDIN_MANIFEST, CALLABLE_MANIFEST):
        manifest = validate_adapter_manifest(raw)
        package = compile_evaluation_package(
            snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
        )
        shapes[manifest.kind] = set(package.to_wire())

    fixtures = build_fixtures(
        version_id,
        tuple(
            DeclaredFile(path=item["path"], content_sha256=item["content_sha256"], mount=item["mount"])
            for item in FILE_MANIFEST["files"]
        ),
    )
    file_manifest = validate_adapter_manifest(FILE_MANIFEST, fixtures=fixtures)
    file_package = compile_evaluation_package(
        snapshot(version_id, file_manifest), file_manifest, cases, fixtures, created_at=PUBLISHED_AT
    )
    shapes["file"] = set(file_package.to_wire())

    assert shapes["stdin"] == shapes["file"] == shapes["callable"]
    assert set(registered_adapter_kinds()) >= {"stdin", "file", "callable"}


def test_compiler_core_dispatches_through_the_registry_only():
    """Adding a question type must not require touching the compiler core."""
    import ast

    from autograder_b3.compile import compiler as compiler_module

    source = (compiler_module.__file__ or "")
    assert source
    tree = ast.parse(open(source, encoding="utf-8").read())

    # No branch may compare against a concrete adapter kind.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for operand in operands:
            if isinstance(operand, ast.Constant) and operand.value in {"stdin", "file", "callable"}:
                raise AssertionError("the compiler core compares against an adapter kind")

    # A new kind registered at runtime is used by the same entry point.
    class StubAdapter:
        kind = "stdin"

        def __init__(self) -> None:
            self.validated = False

        def validate(self, manifest, *, cases, fixtures) -> None:
            self.validated = True

        def to_package_section(self, manifest, *, cases, fixtures):
            return {"kind": "stdin", "pinned": "stub"}

    stub = StubAdapter()
    register_adapter_compiler(stub)
    try:
        version_id = uuid.uuid4()
        manifest = validate_adapter_manifest(STDIN_MANIFEST)
        cases = build_cases(version_id, [{"name": "c", "ordinal": 0, "expected_stdout": "x\n"}])
        package = compile_evaluation_package(
            snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
        )
        assert stub.validated is True
        assert package.adapter["pinned"] == "stub"
    finally:
        unregister_adapter_compiler("stdin")
        register_builtin_adapters()


def test_unknown_adapter_kind_is_refused():
    with pytest.raises(ManifestInvalid) as error:
        validate_adapter_manifest({"kind": "network", "stdin": "x"})
    assert "not supported" in str(error.value)


def test_language_enum_is_restricted_to_the_contract_values():
    """RQ-B-0-10: python and bash only; a free-form language string is refused."""
    from pydantic import ValidationError

    from autograder_b3.dto.internal import QuestionDraftInputModel

    base = {
        "title": "t",
        "stem_markdown": "s",
        "adapter": {"kind": "stdin", "stdin": "x"},
        "cases": [{"name": "c", "ordinal": 0}],
    }
    assert QuestionDraftInputModel(**base, language="bash").language == "bash"
    with pytest.raises(ValidationError):
        QuestionDraftInputModel(**base, language="java")


# --- determinism ----------------------------------------------------------


def test_same_content_compiles_to_the_same_package_digest():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = build_cases(version_id, STDIN_BASH_EXAMPLE["cases"])
    first = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    second = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    assert first.package_digest == second.package_digest
    assert compute_package_digest(first.to_wire()) == first.package_digest


def test_the_package_digest_does_not_depend_on_created_at():
    """``created_at`` is metadata, not content -- the contract says the digest covers "the
    package content", and B2's own `result_digest` already keeps a volatile-field list
    (`VOLATILE_REPORT_FIELDS`) that excludes its timestamp.

    The two values below differ at **millisecond** granularity on purpose. `to_wire()`
    renders timestamps to milliseconds, so a sub-millisecond difference (`123_456` vs
    `123_000`) serialises identically and a test built on it would pass under either rule --
    which is exactly the mistake this guard made in its first version.
    """
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = build_cases(version_id, STDIN_BASH_EXAMPLE["cases"])

    first_at = PUBLISHED_AT.replace(microsecond=123_000)
    second_at = PUBLISHED_AT.replace(microsecond=124_000)
    assert first_at != second_at

    first = compile_evaluation_package(
        snapshot(version_id, manifest, published_at=first_at),
        manifest,
        cases,
        (),
        created_at=first_at,
    )
    second = compile_evaluation_package(
        snapshot(version_id, manifest, published_at=second_at),
        manifest,
        cases,
        (),
        created_at=second_at,
    )
    assert first.package_digest == second.package_digest
    # The metadata still differs; only the digest ignores it.
    assert first.created_at != second.created_at


def test_changing_content_changes_the_package_digest():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = build_cases(version_id, STDIN_BASH_EXAMPLE["cases"])
    baseline = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    changed = build_cases(
        version_id,
        [{"name": "c", "ordinal": 0, "expected_stdout": "different\n"}],
    )
    other = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, changed, (), created_at=PUBLISHED_AT
    )
    assert baseline.package_digest != other.package_digest


def test_created_at_must_equal_the_version_published_at():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = build_cases(version_id, STDIN_BASH_EXAMPLE["cases"])
    with pytest.raises(ManifestInvalid):
        compile_evaluation_package(
            snapshot(version_id, manifest),
            manifest,
            cases,
            (),
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def test_package_rejects_cases_from_another_version():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    foreign = build_cases(uuid.uuid4(), [{"name": "c", "ordinal": 0}])
    with pytest.raises(ManifestInvalid):
        compile_evaluation_package(
            snapshot(version_id, manifest), manifest, foreign, (), created_at=PUBLISHED_AT
        )


def test_package_requires_at_least_one_case():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    with pytest.raises(ManifestInvalid):
        compile_evaluation_package(
            snapshot(version_id, manifest), manifest, (), (), created_at=PUBLISHED_AT
        )


def test_manifest_kind_must_match_the_published_version():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = build_cases(version_id, [{"name": "c", "ordinal": 0}])
    mismatched = snapshot(version_id, validate_adapter_manifest(CALLABLE_MANIFEST))
    with pytest.raises(ManifestInvalid):
        compile_evaluation_package(
            mismatched, manifest, cases, (), created_at=PUBLISHED_AT
        )


def test_security_policy_constants_cannot_be_opted_out_of():
    from autograder_b3.compile import build_policy

    policy = build_policy()
    assert policy.network is False
    assert policy.read_only_root is True
    assert policy.allow_root is False
    with pytest.raises(ManifestInvalid):
        build_policy(max_memory_bytes=1)


def test_tests_are_ordered_by_ordinal_regardless_of_input_order():
    version_id = uuid.uuid4()
    manifest = validate_adapter_manifest(STDIN_MANIFEST)
    cases = list(build_cases(version_id, STDIN_BASH_EXAMPLE["cases"]))
    shuffled = tuple(reversed(cases))
    ordered = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, cases, (), created_at=PUBLISHED_AT
    )
    other = compile_evaluation_package(
        snapshot(version_id, manifest), manifest, shuffled, (), created_at=PUBLISHED_AT
    )
    assert ordered.package_digest == other.package_digest
    assert [entry["ordinal"] for entry in ordered.to_wire()["tests"]] == [0, 1]
