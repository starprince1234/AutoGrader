"""evaluation package 解析与校验：契约形状 JSON → 内部不可变对象。

契约权威是 `MAIN/contracts/schemas/evaluation-package.schema.json`（字段定义**不在此复制**）；
本模块只做 runner 执行所必需的严格解析，并把每一类不合规映射为稳定 `reason` + 明确明细。

**拒绝语义**：不合规包抛 `PackageRejected`（`PackageUnavailableError` 的子类，因此编排层按
`interfaces.md` 第 5.4 节"package 不可获取 / 不可解析 → `failed` + `infrastructure_error`"处理即可）。
这里**不**做"取交集"式放宽——例如包里写 `network: true` 不是"降级为禁网"，而是**整包拒绝**。

**Unix 作业扩展（设计文档 `MAIN/docs/architecture/unix-homework-evaluation.md` §2）**：顶层可选
`execution`（缺省 = `{"mode": "per_case"}`，字节级兼容旧包）与用例级 `segment` / `file_checks` /
`ssh_check` / `email_check`。模式与字段的互斥/必需规则在 `_validate_execution` 里逐条拒绝；稳定原因码
为 `invalid_execution` / `invalid_segment` / `invalid_file_check` / `invalid_ssh_check` /
`invalid_email_check`。受保护值（SSH 口令、期望内容摘要）只存在于本对象内，绝不进 repr/日志/公开 DTO。

资源账本边界（`MAIN/docs/workstreams/B2.md` 第 8 节）与契约 `maximum` 在 `runner.policy` 中定义为常量。
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
ExecutionMode = Literal["per_case", "single_run", "ssh_session", "email_delivery"]
SshCheck = Literal["service_ready", "auth_succeeded", "session_closed"]
EmailCheck = Literal["email_received", "subject", "sender", "attachment"]

EXECUTION_MODES: tuple[ExecutionMode, ...] = (
    "per_case",
    "single_run",
    "ssh_session",
    "email_delivery",
)
SSH_CHECKS: tuple[SshCheck, ...] = ("service_ready", "auth_succeeded", "session_closed")
EMAIL_CHECKS: tuple[EmailCheck, ...] = ("email_received", "subject", "sender", "attachment")

#: `execution.segment_delimiter` 的长度范围与形态（设计文档 §2）。
MIN_SEGMENT_DELIMITER_LENGTH = 8
MAX_SEGMENT_DELIMITER_LENGTH = 120

#: SSH 口令只存在于受保护 package 内：长度上限只是"不把无界字符串带进内存"的护栏。
MAX_SSH_PASSWORD_LENGTH = 128
#: SSH 登录/会话超时的允许范围（秒）。与契约 `security_policy.max_wall_seconds` 上限同一量级。
MIN_SSH_TIMEOUT_SECONDS = 0.1
MAX_SSH_TIMEOUT_SECONDS = 60.0
#: email_delivery 的 `attempt` 允许范围（契约 1..99）。
MIN_EMAIL_ATTEMPT = 1
MAX_EMAIL_ATTEMPT = 99

#: 稳定原因码：`email`/`ssh` 的模板只允许这两个占位符（由 B2 用学生/课程事实填充）。
_EMAIL_TEMPLATE_PLACEHOLDERS = {"student_number", "attempt"}

#: `execution.email` / `execution.ssh` 中带契约默认值的可选字段（缺省即用默认值，不是拒绝）。
DEFAULT_EMAIL_SUBJECT_TEMPLATE = "{student_number}_{attempt}"
DEFAULT_EMAIL_ATTACHMENT_TEMPLATE = "{student_number}.txt"
DEFAULT_SSH_LOGIN_TIMEOUT_SECONDS = 10.0
DEFAULT_SSH_SESSION_TIMEOUT_SECONDS = 10.0
#: `execution.ssh` 的必填字段；两个 timeout 有默认值，不在其中。
_SSH_REQUIRED_FIELDS = ("host", "port", "username", "password")

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
    "invalid_execution",
    "invalid_segment",
    "invalid_file_check",
    "invalid_ssh_check",
    "invalid_email_check",
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
    "segment",
    "file_checks",
    "ssh_check",
    "email_check",
}
_EXECUTION_FIELDS = {"mode", "segment_delimiter", "email", "ssh"}
_EMAIL_FIELDS = {"attempt", "subject_template", "required_attachment_template"}
_SSH_FIELDS = {
    "host",
    "port",
    "username",
    "password",
    "login_timeout_seconds",
    "session_timeout_seconds",
}
_FILE_CHECK_FIELDS = {"path", "content_sha256", "mode"}
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
#: 权限位文本：四位八进制（设计文档示例 `"0744"`）。实际值同形格式化后逐字比较。
_FILE_MODE_RE = re.compile(r"^0[0-7]{3}$")
#: SSH 练习账号名（POSIX 用户名的保守子集；不含空格/斜杠/控制字符）。
_SSH_USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
#: 可打印 ASCII（含空格）。行尾空白另行拒绝（"独占一行的固定标记"）。
_PRINTABLE_ASCII_RE = re.compile(r"^[\x20-\x7e]+$")
_SSH_HOST = "127.0.0.1"


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
class PackageFileCheck:
    """运行后对工作区文件的一项检查（设计文档 §3.4）。

    `content_sha256` 是**受保护**的期望值（只存在于 package）；`mode` 是四位八进制文本
    （例如 `"0744"`），与进程 umask 无关。两项至少声明一项（由解析期校验）。
    """

    path: str
    content_sha256: str | None
    mode: str | None


@dataclass(frozen=True, slots=True)
class PackageEmailConfig:
    """`email_delivery` 的投递配置（设计文档 §2/§3.5）。"""

    attempt: int
    subject_template: str
    required_attachment_template: str


@dataclass(frozen=True, slots=True)
class PackageSshConfig:
    """`ssh_session` 的练习账号配置（设计文档 §2/§3.3）。

    `password` 是**受保护**值：`repr=False` 保证它不进任何 repr/调试输出；任何摘要也都不得
    把它作为输入（见 `runner.run_cases` 的 `input_digest` 构造）。
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    login_timeout_seconds: float = 10.0
    session_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class PackageExecution:
    """package 顶层可选的 `execution` 声明；缺省 = `{"mode": "per_case"}`（现有行为）。"""

    mode: ExecutionMode = "per_case"
    segment_delimiter: str | None = None
    email: PackageEmailConfig | None = None
    ssh: PackageSshConfig | None = None


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
    """一个隐藏用例：输入、期望输出、退出码与权重。

    新增的四个字段均为可选且带缺省值（缺省 = 旧行为），语义见设计文档 §2：
    `segment`（single_run 的第几段）、`file_checks`（运行后文件检查）、
    `ssh_check`/`email_check`（ssh_session / email_delivery 的判定项）。
    """

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
    segment: int | None = None
    file_checks: tuple[PackageFileCheck, ...] = ()
    ssh_check: SshCheck | None = None
    email_check: EmailCheck | None = None


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
    #: 顶层 `execution` 声明；缺省为 `PackageExecution()`（= per_case）。追加在最后且带默认值，
    #: 因此旧构造点（解析器之外的测试/工具）不需要改。
    execution: PackageExecution = field(default_factory=PackageExecution)

    def case_by_id(self, case_id: str) -> PackageCase | None:
        """按 `case_id` 取用例（找不到返回 None，由调用方判定）。"""
        for case in self.tests:
            if case.case_id == case_id:
                return case
        return None


def parse_evaluation_package(payload: object) -> EvaluationPackage:
    """解析契约形状的 package JSON（`Mapping`）→ `EvaluationPackage`；不合规即 `PackageRejected`。"""
    root = _require_mapping(payload, "package")
    # `_REQUIRED_PACKAGE_FIELDS` is both the required set and (before CHG-0031) the allowed
    # key set, because the package used to accept exactly its required keys. `execution` is
    # optional -- absent means per_case -- so it is allowed without being required.
    _reject_unknown(root, (*_REQUIRED_PACKAGE_FIELDS, "execution"), "package")
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
    execution = _parse_execution(root.get("execution"))
    _validate_execution(execution, adapter=adapter, language=language, cases=tests)

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
        execution=execution,
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
        segment_value = case.get("segment")
        segment: int | None
        if segment_value is None:
            segment = None
        else:
            segment = _require_int(segment_value, f"{where}.segment")
            if segment < 0:
                _reject("invalid_segment", f"{where}.segment must be >= 0, got {segment}")
        ssh_check = case.get("ssh_check")
        if ssh_check is not None and ssh_check not in SSH_CHECKS:
            _reject("invalid_ssh_check", f"{where}.ssh_check unsupported: {ssh_check!r}")
        email_check = case.get("email_check")
        if email_check is not None and email_check not in EMAIL_CHECKS:
            _reject("invalid_email_check", f"{where}.email_check unsupported: {email_check!r}")
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
                segment=segment,
                file_checks=_parse_file_checks(case.get("file_checks", []), where),
                ssh_check=ssh_check,
                email_check=email_check,
            )
        )
    return tuple(sorted(cases, key=lambda item: item.ordinal))


def _parse_file_checks(payload: object, where: str) -> tuple[PackageFileCheck, ...]:
    """用例级 `file_checks`（设计文档 §2/§3.4）：每项至少声明摘要或权限位之一。"""
    if payload is None:
        return ()
    if not isinstance(payload, list):
        _reject("invalid_file_check", f"{where}.file_checks must be an array")
    checks: list[PackageFileCheck] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        place = f"{where}.file_checks[{index}]"
        check = _require_mapping(item, place)
        _reject_unknown(check, _FILE_CHECK_FIELDS, place)
        if "path" not in check:
            _reject("invalid_file_check", f"{place} missing required field: path")
        path = _require_str(check["path"], f"{place}.path", 200)
        if not is_allowed_relative_path(path):
            _reject("path_not_allowed", f"{place}.path rejected: {path!r}")
        if path in seen:
            _reject("invalid_file_check", f"duplicate file_check path: {path!r}")
        seen.add(path)
        digest_value = check.get("content_sha256")
        digest: str | None
        if digest_value is None:
            digest = None
        else:
            digest = _require_str(digest_value, f"{place}.content_sha256", 64)
            if not _DIGEST_RE.match(digest):
                _reject(
                    "invalid_file_check",
                    f"{place}.content_sha256 must be 64 lowercase hex characters",
                )
        mode_value = check.get("mode")
        mode: str | None
        if mode_value is None:
            mode = None
        else:
            mode = _require_str(mode_value, f"{place}.mode", 4)
            if not _FILE_MODE_RE.match(mode):
                _reject(
                    "invalid_file_check",
                    f"{place}.mode must be four octal digits such as 0644, got {mode!r}",
                )
        if digest is None and mode is None:
            _reject(
                "invalid_file_check",
                f"{place} must declare content_sha256 or mode (an empty check proves nothing)",
            )
        checks.append(PackageFileCheck(path=path, content_sha256=digest, mode=mode))
    return tuple(checks)


def _parse_execution(payload: object) -> PackageExecution:
    """顶层可选 `execution`（缺省 = per_case）。字段名与语义以设计文档 §2 为准。"""
    if payload is None:
        return PackageExecution()
    execution = _require_mapping(payload, "execution")
    _reject_unknown(execution, _EXECUTION_FIELDS, "execution", reason="invalid_execution")
    if "mode" not in execution:
        _reject("invalid_execution", "execution.mode is required when execution is declared")
    mode = execution["mode"]
    if mode not in EXECUTION_MODES:
        _reject("invalid_execution", f"execution.mode unsupported: {mode!r}")
    delimiter_value = execution.get("segment_delimiter")
    delimiter: str | None
    if delimiter_value is None:
        delimiter = None
    else:
        delimiter = _parse_segment_delimiter(delimiter_value)
    email = _parse_email_config(execution.get("email"))
    ssh = _parse_ssh_config(execution.get("ssh"))
    return PackageExecution(mode=mode, segment_delimiter=delimiter, email=email, ssh=ssh)


def _parse_segment_delimiter(value: object) -> str:
    """分段标记：独占一行的固定标记（8..120 可打印 ASCII、不含行尾空白）。"""
    if not isinstance(value, str) or not value:
        _reject("invalid_execution", "execution.segment_delimiter must be a non-empty string")
    if not (
        MIN_SEGMENT_DELIMITER_LENGTH <= len(value) <= MAX_SEGMENT_DELIMITER_LENGTH
    ):
        _reject(
            "invalid_execution",
            "execution.segment_delimiter must be"
            f" {MIN_SEGMENT_DELIMITER_LENGTH}..{MAX_SEGMENT_DELIMITER_LENGTH} characters",
        )
    if not _PRINTABLE_ASCII_RE.match(value):
        _reject(
            "invalid_execution",
            "execution.segment_delimiter must contain printable ASCII characters only",
        )
    if value[-1].isspace():
        _reject(
            "invalid_execution",
            "execution.segment_delimiter must not end with whitespace (it is a whole line)",
        )
    return value


def _parse_email_config(payload: object) -> PackageEmailConfig | None:
    if payload is None:
        return None
    config = _require_mapping(payload, "execution.email")
    _reject_unknown(config, _EMAIL_FIELDS, "execution.email", reason="invalid_email_check")
    # Contract: only `attempt` is required. The two templates carry defaults in both the
    # contract schema and B3's DTO, so a package that omits them means "use the documented
    # defaults", not "reject the package".
    if "attempt" not in config:
        _reject("invalid_email_check", "execution.email missing required field: attempt")
    attempt = _require_int(config["attempt"], "execution.email.attempt")
    if not MIN_EMAIL_ATTEMPT <= attempt <= MAX_EMAIL_ATTEMPT:
        _reject(
            "invalid_email_check",
            f"execution.email.attempt must be {MIN_EMAIL_ATTEMPT}..{MAX_EMAIL_ATTEMPT},"
            f" got {attempt}",
        )
    subject_template = config.get("subject_template", DEFAULT_EMAIL_SUBJECT_TEMPLATE)
    attachment_template = config.get(
        "required_attachment_template", DEFAULT_EMAIL_ATTACHMENT_TEMPLATE
    )
    return PackageEmailConfig(
        attempt=attempt,
        subject_template=_parse_template(
            subject_template, "execution.email.subject_template"
        ),
        required_attachment_template=_parse_template(
            attachment_template,
            "execution.email.required_attachment_template",
        ),
    )


def _parse_template(value: object, where: str) -> str:
    """模板只允许 `{student_number}` / `{attempt}` 两个占位符（且必须是合法格式串）。"""
    template = _require_str(value, where, 200)
    if "\x00" in template or any(not ch.isprintable() and ch not in "\t" for ch in template):
        _reject("invalid_email_check", f"{where} contains non-printable characters")
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        _reject("invalid_email_check", f"{where} is not a valid format template")
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in _EMAIL_TEMPLATE_PLACEHOLDERS:
            _reject(
                "invalid_email_check",
                f"{where} uses an unsupported placeholder: {field_name!r}",
            )
        if format_spec or conversion:
            _reject("invalid_email_check", f"{where} must not use format specifiers")
    return template


def _parse_ssh_config(payload: object) -> PackageSshConfig | None:
    if payload is None:
        return None
    config = _require_mapping(payload, "execution.ssh")
    _reject_unknown(config, _SSH_FIELDS, "execution.ssh", reason="invalid_ssh_check")
    for name in _SSH_REQUIRED_FIELDS:
        if name not in config:
            _reject("invalid_ssh_check", f"execution.ssh missing required field: {name}")
    host = config["host"]
    if host != _SSH_HOST:
        # 练习环境只在作业自身的回环接口上监听；任何其他地址都属于越界配置。
        _reject("invalid_ssh_check", f"execution.ssh.host must be {_SSH_HOST}, got {host!r}")
    port = _require_int(config["port"], "execution.ssh.port")
    if not 1 <= port <= 65535:
        _reject("invalid_ssh_check", f"execution.ssh.port must be 1..65535, got {port}")
    username = config["username"]
    if not isinstance(username, str) or not _SSH_USERNAME_RE.match(username):
        _reject("invalid_ssh_check", "execution.ssh.username is not a valid POSIX user name")
    password = config["password"]
    if not isinstance(password, str) or not password:
        _reject("invalid_ssh_check", "execution.ssh.password must be a non-empty string")
    if len(password) > MAX_SSH_PASSWORD_LENGTH:
        _reject(
            "invalid_ssh_check",
            f"execution.ssh.password exceeds {MAX_SSH_PASSWORD_LENGTH} characters",
        )
    if any(not ch.isprintable() for ch in password):
        _reject("invalid_ssh_check", "execution.ssh.password contains non-printable characters")
    # The two timeouts carry contract defaults (10s); an omitted value means the default.
    login_timeout = _require_timeout(
        config.get("login_timeout_seconds", DEFAULT_SSH_LOGIN_TIMEOUT_SECONDS),
        "execution.ssh.login_timeout_seconds",
    )
    session_timeout = _require_timeout(
        config.get("session_timeout_seconds", DEFAULT_SSH_SESSION_TIMEOUT_SECONDS),
        "execution.ssh.session_timeout_seconds",
    )
    return PackageSshConfig(
        host=_SSH_HOST,
        port=port,
        username=username,
        password=password,
        login_timeout_seconds=login_timeout,
        session_timeout_seconds=session_timeout,
    )


def _require_timeout(value: object, where: str) -> float:
    timeout = _require_number(value, where)
    if not MIN_SSH_TIMEOUT_SECONDS <= timeout <= MAX_SSH_TIMEOUT_SECONDS:
        _reject(
            "invalid_ssh_check",
            f"{where} must be {MIN_SSH_TIMEOUT_SECONDS}..{MAX_SSH_TIMEOUT_SECONDS} seconds,"
            f" got {timeout}",
        )
    return timeout


def _validate_execution(
    execution: PackageExecution,
    *,
    adapter: PackageAdapter,
    language: Language,
    cases: tuple[PackageCase, ...],
) -> None:
    """模式 × 字段的互斥/必需规则（设计文档 §2；B2 解析期与 B3 编译期双端一致）。

    这里的每一条拒绝都对应"这种声明无法被真实、可复现地判定"——不是风格偏好。
    """
    mode = execution.mode
    if mode == "per_case":
        _reject_mode_fields(
            execution, mode, forbidden={"segment_delimiter", "email", "ssh"}
        )
        for case in cases:
            if case.segment is not None:
                _reject("invalid_execution", "per_case forbids case-level segment")
            if case.ssh_check is not None:
                _reject("invalid_execution", "per_case forbids case-level ssh_check")
            if case.email_check is not None:
                _reject("invalid_execution", "per_case forbids case-level email_check")
        return

    if mode == "single_run":
        _reject_mode_fields(execution, mode, forbidden={"email", "ssh"})
        # 设计文档 §2：single_run 允许 stdin/file（第 6 题是 file adapter + 一次执行 + file_checks）；
        # 但只要存在 segment 用例，比较的就是程序 stdout，因此 kind 必须是 stdin。
        if adapter.kind not in ("stdin", "file"):
            _reject(
                "invalid_execution",
                "single_run requires adapter.kind in {stdin, file}, got"
                f" {adapter.kind!r}",
            )
        segment_cases = [case for case in cases if case.segment is not None]
        for case in cases:
            if case.ssh_check is not None:
                _reject("invalid_execution", "single_run forbids case-level ssh_check")
            if case.email_check is not None:
                _reject("invalid_execution", "single_run forbids case-level email_check")
            declares_segment = case.segment is not None
            declares_files = bool(case.file_checks)
            if declares_segment == declares_files:
                _reject(
                    "invalid_execution",
                    f"single_run case {case.case_id} must declare exactly one of"
                    " segment or non-empty file_checks",
                )
        if segment_cases:
            if adapter.kind != "stdin":
                _reject(
                    "invalid_execution",
                    "single_run requires adapter.kind == stdin when a case declares segment"
                    " (segments compare the program's stdout)",
                )
            if execution.segment_delimiter is None:
                _reject(
                    "invalid_execution",
                    "single_run requires execution.segment_delimiter when a case declares segment",
                )
            values = sorted(case.segment for case in segment_cases if case.segment is not None)
            if values != list(range(len(values))):
                _reject(
                    "invalid_segment",
                    "single_run segment values must be exactly 0..N-1 without duplicates",
                )
        elif execution.segment_delimiter is not None:
            _reject(
                "invalid_execution",
                "execution.segment_delimiter is declared but no case declares segment",
            )
        return

    if mode == "ssh_session":
        _reject_mode_fields(execution, mode, forbidden={"segment_delimiter", "email"})
        if execution.ssh is None:
            _reject("invalid_execution", "ssh_session requires execution.ssh")
        if adapter.kind != "stdin" or language != "bash":
            _reject(
                "invalid_execution",
                "ssh_session requires adapter.kind == stdin and language == bash",
            )
        seen: set[str] = set()
        for case in cases:
            if case.segment is not None or case.file_checks:
                _reject(
                    "invalid_execution",
                    "ssh_session forbids case-level segment/file_checks",
                )
            if case.email_check is not None:
                _reject("invalid_execution", "ssh_session forbids case-level email_check")
            if case.ssh_check is None:
                _reject(
                    "invalid_ssh_check",
                    f"ssh_session case {case.case_id} must declare exactly one ssh_check",
                )
            if case.ssh_check in seen:
                _reject("invalid_ssh_check", f"duplicate ssh_check: {case.ssh_check}")
            seen.add(case.ssh_check)
        return

    # email_delivery
    _reject_mode_fields(execution, mode, forbidden={"segment_delimiter", "ssh"})
    if execution.email is None:
        _reject("invalid_execution", "email_delivery requires execution.email")
    if adapter.kind != "stdin" or language != "bash":
        _reject(
            "invalid_execution",
            "email_delivery requires adapter.kind == stdin and language == bash",
        )
    seen_email: set[str] = set()
    for case in cases:
        if case.segment is not None or case.file_checks:
            _reject(
                "invalid_execution",
                "email_delivery forbids case-level segment/file_checks",
            )
        if case.ssh_check is not None:
            _reject("invalid_execution", "email_delivery forbids case-level ssh_check")
        if case.email_check is None:
            _reject(
                "invalid_email_check",
                f"email_delivery case {case.case_id} must declare exactly one email_check",
            )
        if case.email_check in seen_email:
            _reject("invalid_email_check", f"duplicate email_check: {case.email_check}")
        seen_email.add(case.email_check)


def _reject_mode_fields(
    execution: PackageExecution, mode: str, *, forbidden: set[str]
) -> None:
    """模式不使用的字段必须**缺席**：静默忽略会让 package 声明与实际行为不一致。"""
    for name in sorted(forbidden):
        if getattr(execution, name) is not None:
            _reject(
                "invalid_execution",
                f"execution.{name} is not used by execution.mode={mode}",
            )


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
    "EMAIL_CHECKS",
    "EXECUTION_MODES",
    "MAX_EMAIL_ATTEMPT",
    "MAX_SEGMENT_DELIMITER_LENGTH",
    "MAX_SSH_PASSWORD_LENGTH",
    "MAX_SSH_TIMEOUT_SECONDS",
    "MIN_EMAIL_ATTEMPT",
    "MIN_SEGMENT_DELIMITER_LENGTH",
    "MIN_SSH_TIMEOUT_SECONDS",
    "SSH_CHECKS",
    "STDERR_STRATEGIES",
    "STDOUT_STRATEGIES",
    "AdapterKind",
    "EmailCheck",
    "EvaluationPackage",
    "ExecutionMode",
    "FixtureMount",
    "Language",
    "PackageAdapter",
    "PackageCallable",
    "PackageCase",
    "PackageComparison",
    "PackageEmailConfig",
    "PackageExecution",
    "PackageFileCheck",
    "PackageFixture",
    "PackageReason",
    "PackageRejected",
    "PackageSshConfig",
    "SshCheck",
    "StderrStrategy",
    "StdoutStrategy",
    "parse_evaluation_package",
]
