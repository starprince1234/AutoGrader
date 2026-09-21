"""evaluation package 解析与校验：契约形状 JSON → 内部不可变对象。

契约权威是 `MAIN/contracts/schemas/evaluation-package.schema.json`（字段定义**不在此复制**）；
本模块只做 runner 执行所必需的严格解析，并把每一类不合规映射为稳定 `reason` + 明确明细。

**拒绝语义**：不合规包抛 `PackageRejected`（`PackageUnavailableError` 的子类，因此编排层按
`interfaces.md` 第 5.4 节"package 不可获取 / 不可解析 → `failed` + `infrastructure_error`"处理即可）。
这里**不**做"取交集"式放宽——例如包里写 `network: true` 不是"降级为禁网"，而是**整包拒绝**。

资源账本边界（`MAIN/docs/workstreams/B2.md` 第 8 节）与契约 `maximum` 在 `runner.policy` 中定义为常量。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from autograder_b2.domain import PackageUnavailableError, is_allowed_relative_path
from autograder_b2.runner.policy import (
    RunnerPolicy,
    policy_bound_violations,
)

Language = Literal["python", "bash"]
AdapterKind = Literal["stdin", "file", "callable"]
FixtureMount = Literal["input", "expected_output"]
StdoutStrategy = Literal["exact", "ignore_trailing_whitespace", "numeric_tolerance", "ignored"]
StderrStrategy = Literal["exact", "ignore_trailing_whitespace", "ignored"]

STDOUT_STRATEGIES: tuple[StdoutStrategy, ...] = (
    "exact",
    "ignore_trailing_whitespace",
    "numeric_tolerance",
    "ignored",
)
STDERR_STRATEGIES: tuple[StderrStrategy, ...] = ("exact", "ignore_trailing_whitespace", "ignored")

#: 包被拒的稳定原因码。
PackageReason = Literal[
    "schema_invalid",
    "unknown_field",
    "invalid_language",
    "invalid_adapter",
    "invalid_comparison",
    "invalid_argument",
    "path_not_allowed",
    "invalid_fixture",
    "invalid_callable_entry",
    "invalid_test_case",
    "duplicate_case",
    "policy_const_violation",
    "policy_out_of_range",
    "policy_unknown_field",
    "out_of_range",
]

_REQUIRED_PACKAGE_FIELDS = (
    "question_version_id",
    "package_digest",
    "language",
    "adapter",
    "tests",
    "security_policy",
    "max_files",
    "max_total_bytes",
    "created_at",
)
_ADAPTER_FIELDS = {"kind", "args", "stdin", "expect_exit_code", "comparison", "files", "callable"}
_COMPARISON_FIELDS = {"stdout", "stderr", "numeric_tolerance"}
_FIXTURE_FIELDS = {"path", "content_sha256", "mount"}
_CALLABLE_FIELDS = {"module", "attribute", "arguments_json"}
_CASE_FIELDS = {
    "case_id",
    "name",
    "ordinal",
    "weight",
    "visible_to_student",
    "stdin",
    "args",
    "expected_stdout",
    "expected_stderr",
    "expected_exit_code",
}
_POLICY_FIELDS = {
    "network",
    "read_only_root",
    "allow_root",
    "max_memory_bytes",
    "max_cpu_seconds",
    "max_wall_seconds",
    "max_output_bytes",
    "max_processes",
    "max_files_created",
}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PackageRejected(PackageUnavailableError):
    """包内容不合规（不是"取不到"，而是"不可接受"）。`reason` 稳定、`detail` 已脱敏。"""

    def __init__(self, reason: PackageReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: PackageReason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PackageFixture:
    """package 声明的 fixture（只带内容摘要，内容由 package 之外的通道交付）。"""

    path: str
    content_sha256: str
    mount: FixtureMount


@dataclass(frozen=True, slots=True)
class PackageCallable:
    """callable 入口：模块名 + 属性名 + 参数 JSON（在沙箱内工作区解析，绝不用绝对路径/网络）。"""

    module: str
    attribute: str
    arguments_json: str | None


@dataclass(frozen=True, slots=True)
class PackageComparison:
    """比较策略：**只来自 package**（I21），提交侧无法影响。"""

    stdout: StdoutStrategy
    stderr: StderrStrategy
    numeric_tolerance: float | None


@dataclass(frozen=True, slots=True)
class PackageAdapter:
    """执行形态：`kind ∈ {stdin, file, callable}` + 该形态的参数。"""

    kind: AdapterKind
    args: tuple[str, ...]
    stdin: str | None
    expect_exit_code: int | None
    comparison: PackageComparison
    files: tuple[PackageFixture, ...]
    callable: PackageCallable | None


@dataclass(frozen=True, slots=True)
class PackageCase:
    """一个隐藏用例：输入、期望输出、退出码与权重。"""

    case_id: str
    name: str
    ordinal: int
    weight: float
    visible_to_student: bool
    stdin: str | None
    args: tuple[str, ...]
    expected_stdout: str | None
    expected_stderr: str | None
    expected_exit_code: int | None


@dataclass(frozen=True, slots=True)
class EvaluationPackage:
    """解析后的不可变 package（含策略；策略常量已在解析期校验，不存在"放宽后的策略"）。"""

    question_version_id: str
    package_digest: str
    language: Language
    adapter: PackageAdapter
    tests: tuple[PackageCase, ...]
    security_policy: RunnerPolicy
    max_files: int
    max_total_bytes: int
    created_at: str

    def case_by_id(self, case_id: str) -> PackageCase | None:
        """按 `case_id` 取用例（找不到返回 None，由调用方判定）。"""
        for case in self.tests:
            if case.case_id == case_id:
                return case
        return None


def parse_evaluation_package(payload: object) -> EvaluationPackage:
    """解析契约形状的 package JSON（`Mapping`）→ `EvaluationPackage`；不合规即 `PackageRejected`。"""
    root = _require_mapping(payload, "package")
    _reject_unknown(root, _REQUIRED_PACKAGE_FIELDS, "package")
    for name in _REQUIRED_PACKAGE_FIELDS:
        if name not in root:
            _reject("schema_invalid", f"missing required field: {name}")

    question_version_id = _require_str(root["question_version_id"], "question_version_id", 64)
    if not _UUID_RE.match(question_version_id):
        _reject("schema_invalid", "question_version_id must be a lowercase UUID")
    package_digest = _require_str(root["package_digest"], "package_digest", 64)
    if not _DIGEST_RE.match(package_digest):
        _reject("schema_invalid", "package_digest must be 64 lowercase hex characters")

    language = root["language"]
    if language not in ("python", "bash"):
        _reject("invalid_language", f"language must be python or bash, got {language!r}")

    adapter = _parse_adapter(root["adapter"])
    tests = _parse_tests(root["tests"])
    policy = _parse_security_policy(root["security_policy"])

    max_files = _require_int(root["max_files"], "max_files")
    if not 1 <= max_files <= 100:
        _reject("out_of_range", f"max_files must be 1..100, got {max_files}")
    max_total_bytes = _require_int(root["max_total_bytes"], "max_total_bytes")
    if max_total_bytes < 1:
        _reject("out_of_range", f"max_total_bytes must be >= 1, got {max_total_bytes}")
    created_at = _require_str(root["created_at"], "created_at", 64)
    _assert_datetime(created_at)

    return EvaluationPackage(
        question_version_id=question_version_id,
        package_digest=package_digest,
        language=language,
        adapter=adapter,
        tests=tests,
        security_policy=policy,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
        created_at=created_at,
    )


def _parse_adapter(payload: object) -> PackageAdapter:
    adapter = _require_mapping(payload, "adapter")
    _reject_unknown(adapter, _ADAPTER_FIELDS, "adapter")
    kind = adapter.get("kind")
    if kind not in ("stdin", "file", "callable"):
        _reject("invalid_adapter", f"adapter.kind must be stdin/file/callable, got {kind!r}")
    args = _parse_arguments(adapter.get("args", []), "adapter.args")
    stdin = _optional_str(adapter.get("stdin"), "adapter.stdin")
    expect_exit_code = _optional_int(adapter.get("expect_exit_code"), "adapter.expect_exit_code")
    comparison = _parse_comparison(adapter.get("comparison"))
    files = _parse_fixtures(adapter.get("files", []))
    callable_entry = _parse_callable(adapter.get("callable"), kind)
    return PackageAdapter(
        kind=kind,
        args=args,
        stdin=stdin,
        expect_exit_code=expect_exit_code,
        comparison=comparison,
        files=files,
        callable=callable_entry,
    )


def _parse_comparison(payload: object) -> PackageComparison:
    if payload is None:
        _reject("invalid_comparison", "adapter.comparison is required")
    comparison = _require_mapping(payload, "adapter.comparison")
    _reject_unknown(comparison, _COMPARISON_FIELDS, "adapter.comparison")
    stdout = comparison.get("stdout")
    if stdout not in STDOUT_STRATEGIES:
        _reject("invalid_comparison", f"comparison.stdout unsupported: {stdout!r}")
    stderr = comparison.get("stderr", "exact")
    if stderr not in STDERR_STRATEGIES:
        _reject("invalid_comparison", f"comparison.stderr unsupported: {stderr!r}")
    tolerance = comparison.get("numeric_tolerance")
    if tolerance is not None and (not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool)):
        _reject("invalid_comparison", "comparison.numeric_tolerance must be a number or null")
    if tolerance is not None and tolerance < 0:
        _reject("invalid_comparison", "comparison.numeric_tolerance must be >= 0")
    if stdout == "numeric_tolerance" and tolerance is None:
        _reject("invalid_comparison", "comparison.numeric_tolerance required for numeric_tolerance")
    return PackageComparison(
        stdout=stdout,
        stderr=stderr,
        numeric_tolerance=None if tolerance is None else float(tolerance),
    )


def _parse_fixtures(payload: object) -> tuple[PackageFixture, ...]:
    if not isinstance(payload, list):
        _reject("invalid_fixture", "adapter.files must be an array")
    fixtures: list[PackageFixture] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        where = f"adapter.files[{index}]"
        fixture = _require_mapping(item, where)
        _reject_unknown(fixture, _FIXTURE_FIELDS, where)
        for name in _FIXTURE_FIELDS:
            if name not in fixture:
                _reject("invalid_fixture", f"{where} missing required field: {name}")
        path = _require_str(fixture["path"], f"{where}.path", 200)
        if not is_allowed_relative_path(path):
            _reject("path_not_allowed", f"{where}.path rejected: {path!r}")
        if path in seen:
            _reject("invalid_fixture", f"duplicate fixture path: {path!r}")
        seen.add(path)
        digest = _require_str(fixture["content_sha256"], f"{where}.content_sha256", 64)
        if not _DIGEST_RE.match(digest):
            _reject("invalid_fixture", f"{where}.content_sha256 must be 64 lowercase hex characters")
        mount = fixture["mount"]
        if mount not in ("input", "expected_output"):
            _reject("invalid_fixture", f"{where}.mount must be input or expected_output, got {mount!r}")
        fixtures.append(PackageFixture(path=path, content_sha256=digest, mount=mount))
    return tuple(fixtures)


def _parse_callable(payload: object, kind: str) -> PackageCallable | None:
    if kind == "callable" and payload is None:
        _reject("invalid_callable_entry", "adapter.callable is required for kind=callable")
    if payload is None:
        return None
    where = "adapter.callable"
    entry = _require_mapping(payload, where)
    _reject_unknown(entry, _CALLABLE_FIELDS, where)
    for name in ("module", "attribute"):
        if name not in entry:
            _reject("invalid_callable_entry", f"{where} missing required field: {name}")
    module = _require_str(entry["module"], f"{where}.module", 200)
    attribute = _require_str(entry["attribute"], f"{where}.attribute", 200)
    # 只在工作区内按点分模块名解析：拒绝路径分隔符、绝对路径、`..` 段与 NUL。
    if "\\" in module or "/" in module or "\x00" in module:
        _reject("invalid_callable_entry", f"{where}.module must be a dotted module name, no path")
    for segment in module.split("."):
        if not _IDENTIFIER_RE.match(segment):
            _reject("invalid_callable_entry", f"{where}.module segment rejected: {segment!r}")
    if not _IDENTIFIER_RE.match(attribute):
        _reject("invalid_callable_entry", f"{where}.attribute rejected: {attribute!r}")
    arguments_json = _optional_str(entry.get("arguments_json"), f"{where}.arguments_json")
    if arguments_json is not None:
        if len(arguments_json) > 8192:
            _reject("invalid_callable_entry", f"{where}.arguments_json exceeds 8192 characters")
        try:
            json.loads(arguments_json)
        except json.JSONDecodeError as error:
            _reject("invalid_callable_entry", f"{where}.arguments_json is not valid JSON: {error.msg}")
    return PackageCallable(module=module, attribute=attribute, arguments_json=arguments_json)


def _parse_tests(payload: object) -> tuple[PackageCase, ...]:
    if not isinstance(payload, list) or not payload:
        _reject("invalid_test_case", "tests must be a non-empty array")
    cases: list[PackageCase] = []
    case_ids: set[str] = set()
    ordinals: set[int] = set()
    for index, item in enumerate(payload):
        where = f"tests[{index}]"
        case = _require_mapping(item, where)
        _reject_unknown(case, _CASE_FIELDS, where)
        for name in ("case_id", "name", "ordinal"):
            if name not in case:
                _reject("invalid_test_case", f"{where} missing required field: {name}")
        case_id = _require_str(case["case_id"], f"{where}.case_id", 64)
        if not _UUID_RE.match(case_id):
            _reject("invalid_test_case", f"{where}.case_id must be a lowercase UUID")
        if case_id in case_ids:
            _reject("duplicate_case", f"duplicate case_id: {case_id}")
        case_ids.add(case_id)
        name = _require_str(case["name"], f"{where}.name", 200)
        ordinal = _require_int(case["ordinal"], f"{where}.ordinal")
        if ordinal < 0:
            _reject("invalid_test_case", f"{where}.ordinal must be >= 0")
        if ordinal in ordinals:
            _reject("duplicate_case", f"duplicate ordinal: {ordinal}")
        ordinals.add(ordinal)
        weight_value = case.get("weight", 1)
        if isinstance(weight_value, bool) or not isinstance(weight_value, (int, float)):
            _reject("invalid_test_case", f"{where}.weight must be a number")
        if float(weight_value) <= 0:
            _reject("invalid_test_case", f"{where}.weight must be > 0")
        visible = case.get("visible_to_student", False)
        if not isinstance(visible, bool):
            _reject("invalid_test_case", f"{where}.visible_to_student must be a boolean")
        cases.append(
            PackageCase(
                case_id=case_id,
                name=name,
                ordinal=ordinal,
                weight=float(weight_value),
                visible_to_student=visible,
                stdin=_optional_str(case.get("stdin"), f"{where}.stdin"),
                args=_parse_arguments(case.get("args", []), f"{where}.args"),
                expected_stdout=_optional_str(case.get("expected_stdout"), f"{where}.expected_stdout"),
                expected_stderr=_optional_str(case.get("expected_stderr"), f"{where}.expected_stderr"),
                expected_exit_code=_optional_int(
                    case.get("expected_exit_code"), f"{where}.expected_exit_code"
                ),
            )
        )
    return tuple(sorted(cases, key=lambda item: item.ordinal))


def _parse_security_policy(payload: object) -> RunnerPolicy:
    """契约 `security_policy` → `RunnerPolicy`；常量违规或数值越界一律拒绝。

    `network`/`read_only_root`/`allow_root` 的契约 `const` 已经写死取值，这里再判一次**不是冗余**：
    它把"包试图放宽隔离"变成**明确拒绝**，而不是 schema 报错后被谁悄悄忽略。

    数值边界**不在此处另写一份**：先做类型校验，再交给 `policy_bound_violations` 这唯一一张边界表
    （与执行期 `assert_policy_executable` 同一函数、同一常量），因此"从 package 走"与"直接构造策略"
    的判定完全一致，不存在一处严一处松。
    """
    policy_payload = _require_mapping(payload, "security_policy")
    _reject_unknown(policy_payload, _POLICY_FIELDS, "security_policy", reason="policy_unknown_field")
    for name in _POLICY_FIELDS - {"max_files_created"}:
        if name not in policy_payload:
            _reject("schema_invalid", f"security_policy missing required field: {name}")

    if policy_payload["network"] is not False:
        _reject("policy_const_violation", "security_policy.network must be false (constant, ADR-0004)")
    if policy_payload["read_only_root"] is not True:
        _reject(
            "policy_const_violation",
            "security_policy.read_only_root must be true (constant, ADR-0004)",
        )
    if policy_payload["allow_root"] is not False:
        _reject("policy_const_violation", "security_policy.allow_root must be false (constant, ADR-0004)")

    memory = _require_int(policy_payload["max_memory_bytes"], "security_policy.max_memory_bytes")
    cpu = _require_number(policy_payload["max_cpu_seconds"], "security_policy.max_cpu_seconds")
    wall = _require_number(policy_payload["max_wall_seconds"], "security_policy.max_wall_seconds")
    output = _require_int(policy_payload["max_output_bytes"], "security_policy.max_output_bytes")
    processes = _require_int(policy_payload["max_processes"], "security_policy.max_processes")
    files_raw = policy_payload.get("max_files_created")
    max_files_created: int | None
    if files_raw is None:
        max_files_created = None
    else:
        max_files_created = _require_int(files_raw, "security_policy.max_files_created")

    candidate = RunnerPolicy(
        network=False,
        read_only_root=True,
        allow_root=False,
        max_memory_bytes=memory,
        max_cpu_seconds=cpu,
        max_wall_seconds=wall,
        max_output_bytes=output,
        max_processes=processes,
        max_files_created=max_files_created,
    )
    violations = policy_bound_violations(candidate)
    if violations:
        _reject("policy_out_of_range", violations[0].detail)
    return candidate


def _parse_arguments(payload: object, where: str) -> tuple[str, ...]:
    if not isinstance(payload, list):
        _reject("invalid_argument", f"{where} must be an array of strings")
    arguments: list[str] = []
    for index, item in enumerate(payload):
        if not isinstance(item, str):
            _reject("invalid_argument", f"{where}[{index}] must be a string")
        if "\x00" in item:
            _reject("invalid_argument", f"{where}[{index}] contains NUL")
        arguments.append(item)
    return tuple(arguments)


def _reject_unknown(
    payload: Mapping[str, Any],
    allowed: Sequence[str] | set[str],
    where: str,
    *,
    reason: PackageReason = "unknown_field",
) -> None:
    allowed_names = set(allowed)
    for key in payload:
        if key not in allowed_names:
            _reject(reason, f"{where} has unknown field: {key!r}")


def _require_mapping(payload: object, where: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        _reject("schema_invalid", f"{where} must be an object, got {type(payload).__name__}")
    for key in payload:
        if not isinstance(key, str):
            _reject("schema_invalid", f"{where} has a non-string key")
    return payload


def _require_str(value: object, where: str, limit: int) -> str:
    if not isinstance(value, str) or not value:
        _reject("schema_invalid", f"{where} must be a non-empty string")
    if len(value) > limit:
        _reject("out_of_range", f"{where} exceeds {limit} characters")
    return value


def _optional_str(value: object, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _reject("schema_invalid", f"{where} must be a string or null")
    return value


def _require_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _reject("schema_invalid", f"{where} must be an integer")
    return value


def _optional_int(value: object, where: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, where)


def _require_number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject("schema_invalid", f"{where} must be a number")
    return float(value)


def _assert_datetime(value: str) -> None:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        _reject("schema_invalid", "created_at must be an ISO-8601 date-time")


def _reject(reason: PackageReason, detail: str) -> None:
    raise PackageRejected(reason, detail)


__all__ = [
    "STDERR_STRATEGIES",
    "STDOUT_STRATEGIES",
    "AdapterKind",
    "EvaluationPackage",
    "FixtureMount",
    "Language",
    "PackageAdapter",
    "PackageCallable",
    "PackageCase",
    "PackageComparison",
    "PackageFixture",
    "PackageReason",
    "PackageRejected",
    "StderrStrategy",
    "StdoutStrategy",
    "parse_evaluation_package",
]
