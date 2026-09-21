"""静态检查调度：按语言分发 + 聚合为 `StaticAnalysisResult`（形态见 interfaces 第 3 节，已冻结）。

职责边界：
- 工作区路径是本模块**唯一**的 IO 入口：每个待测路径先过 I2 白名单（`is_allowed_relative_path`），
  再断言解析后的绝对路径仍在工作区之内；不合法即按契约拒绝（`EnvelopeRejected`），不产出部分结果。
- 检查器只拿"已读到的字节/文本"，不自己碰文件系统、不启动进程（Bash 解释器由 `bash_checker` 以
  固定参数数组启动）——因此 I9"不 import/不 exec/不编译执行待测模块"由结构保证。
- 聚合遵守 I11（按 `(file, line, column, rule_id)` 排序 + 规范化摘要）、I13（`blocking` 只来自
  `status == "ran"` 的检查）、I14（未执行一律以 `not_checked` + `reason` 呈现，绝不折叠为"零 finding"）。
- 检查器自身异常 → 该检查 `not_checked` + 脱敏原因（只留异常类名）：不吞掉、不中断评测、不伪装成
  没有发现（interfaces 第 3.4 节）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
from collections.abc import Callable, Sequence

from autograder_b2.domain.envelope import (
    EnvelopeRejected,
    is_allowed_relative_path,
    redact_path_for_message,
)
from autograder_b2.domain.static_result import (
    CheckOutcome,
    CheckStatus,
    StaticAnalysisResult,
    StaticFinding,
)
from autograder_b2.static import bash_checker, python_checker
from autograder_b2.static.rules import Checker, Language, get_rule

#: 每种语言的检查顺序（固定，决定 `checks` 元组的顺序）。
_CHECKER_ORDER: dict[str, tuple[Checker, ...]] = {
    "python": ("python_ast", "structural_rules"),
    "bash": ("bash_syntax", "structural_rules"),
}

#: 一次检查步骤的返回：`(findings, status, reason)`。
_StepResult = tuple[tuple[StaticFinding, ...], CheckStatus, str | None]

#: 检查器内部异常时的脱敏原因（异常文本可能带绝对路径与源码片段，故只保留类名）。
_INTERNAL_ERROR_REASON = "检查器内部异常（{kind}）：该检查未完成，其覆盖为空"


def analyze_static(
    *,
    workspace: str | pathlib.Path,
    files: Sequence[str],
    language: Language,
) -> StaticAnalysisResult:
    """对工作区内若干**已声明文件**做静态检查并聚合。

    `files` 是 `SubmissionEnvelope.files[].path`（工作区相对路径）；本函数不使用任何其他路径来源。
    `language` 取自 package 声明，只有 `python`/`bash`（`RQ-B-0-10`）。
    """
    if language not in _CHECKER_ORDER:
        raise ValueError(f"unsupported language: {language!r}")
    root = pathlib.Path(workspace).resolve()
    targets = _validated_targets(root, files)
    payloads = _read_payloads(targets)
    if payloads is None:
        reason = f"工作区缺少已声明的待测文件（共 {len(targets)} 个）：静态检查整体未执行"
        checks = tuple(
            CheckOutcome(checker=name, status="not_checked", reason=reason, findings=0)
            for name in _CHECKER_ORDER[language]
        )
        return _assemble(language, checks, ())

    if language == "python":
        return _assemble_python(targets, payloads)
    return _assemble_bash(targets, payloads)


def _assemble_python(
    targets: tuple[tuple[str, pathlib.Path], ...],
    payloads: dict[str, bytes],
) -> StaticAnalysisResult:
    """Python：`ast` 语法检查 + 语法树结构检查（两步都无副作用）。

    覆盖口径：`CheckOutcome` 只能表达"整体跑了/整体没跑"。部分文件有语法错误时，结构检查覆盖的是
    **可解析的那部分文件**（出错文件的语法错误已单独成条）；只有**全部**文件都无法解析时才把结构
    检查整体记为 `not_checked`——宁可报未检查，也不声称检查过。
    """
    parseable: list[tuple[str, str, ast.Module]] = []

    def syntax_step() -> _StepResult:
        findings: list[StaticFinding] = []
        for relative, _ in targets:
            tree, errors = python_checker.parse_python(relative, payloads[relative])
            findings.extend(errors)
            if tree is not None:
                parseable.append(
                    (relative, payloads[relative].decode("utf-8", errors="replace"), tree)
                )
        return tuple(findings), "ran", None

    def structural_step() -> _StepResult:
        if not parseable:
            return (), "not_checked", "候选文件都存在语法错误，没有可用的语法树：结构检查未执行"
        findings: list[StaticFinding] = []
        for relative, source, tree in parseable:
            findings.extend(python_checker.python_structural_findings(relative, source, tree))
        return tuple(findings), "ran", None

    syntax_findings, syntax_outcome = _step(syntax_step, checker="python_ast")
    structural_findings, structural_outcome = _step(structural_step, checker="structural_rules")
    return _assemble(
        "python",
        (syntax_outcome, structural_outcome),
        (*syntax_findings, *structural_findings),
    )


def _assemble_bash(
    targets: tuple[tuple[str, pathlib.Path], ...],
    payloads: dict[str, bytes],
) -> StaticAnalysisResult:
    """Bash：`bash -n` 语法检查 + 文本结构检查（解释器不可用时语法检查判 `not_checked`）。"""
    sources = {
        relative: payloads[relative].decode("utf-8", errors="replace") for relative, _ in targets
    }
    syntax_targets = tuple(
        bash_checker.BashTarget(relative=relative, absolute=absolute, source=sources[relative])
        for relative, absolute in targets
    )

    def syntax_step() -> _StepResult:
        result = bash_checker.check_bash_syntax(bash_checker.probe_bash(), syntax_targets)
        if result.unavailable_reason:
            return (), "not_checked", result.unavailable_reason
        return result.findings, "ran", None

    def structural_step() -> _StepResult:
        findings: list[StaticFinding] = []
        for relative, _ in targets:
            findings.extend(bash_checker.bash_structural_findings(relative, sources[relative]))
        return tuple(findings), "ran", None

    syntax_findings, syntax_outcome = _step(syntax_step, checker="bash_syntax")
    structural_findings, structural_outcome = _step(structural_step, checker="structural_rules")
    return _assemble(
        "bash",
        (syntax_outcome, structural_outcome),
        (*syntax_findings, *structural_findings),
    )


def _step(
    run: Callable[[], _StepResult],
    *,
    checker: Checker,
) -> tuple[tuple[StaticFinding, ...], CheckOutcome]:
    """执行一个检查步骤；未执行（`not_checked`）时**必然**零明细，其发现不进总评。"""
    try:
        findings, status, reason = run()
    except Exception as exc:  # noqa: BLE001 - 检查器故障降级为未检查，而不是中断整条评测
        return (), CheckOutcome(
            checker=checker,
            status="not_checked",
            reason=_INTERNAL_ERROR_REASON.format(kind=type(exc).__name__),
            findings=0,
        )
    if status == "not_checked":
        return (), CheckOutcome(
            checker=checker,
            status="not_checked",
            reason=reason or "该检查未执行",
            findings=0,
        )
    return findings, CheckOutcome(
        checker=checker,
        status="ran",
        reason=None,
        findings=len(findings),
    )


def _validated_targets(
    root: pathlib.Path,
    files: Sequence[str],
) -> tuple[tuple[str, pathlib.Path], ...]:
    """I2 白名单 + 工作区围栏：返回按相对路径排序的 `(相对路径, 绝对路径)` 序列。

    排序使结果与调用方的文件顺序无关（I11）。任一路径不合法即整体拒绝，不做部分检查。
    """
    if not files:
        raise EnvelopeRejected("empty_fileset", "no files to check statically")
    targets: list[tuple[str, pathlib.Path]] = []
    for relative in files:
        if not is_allowed_relative_path(relative):
            raise EnvelopeRejected(
                "path_not_allowed",
                f"path rejected: {redact_path_for_message(relative)}",
            )
        absolute = (root / relative).resolve()
        if not absolute.is_relative_to(root):
            raise EnvelopeRejected(
                "path_not_allowed",
                f"path escapes workspace: {redact_path_for_message(relative)}",
            )
        targets.append((relative, absolute))
    return tuple(sorted(targets, key=lambda item: item[0]))


def _read_payloads(
    targets: tuple[tuple[str, pathlib.Path], ...],
) -> dict[str, bytes] | None:
    """读取全部待测文件的字节；任一文件不可读即返回 `None`（整体转未检查，不部分检查）。"""
    payloads: dict[str, bytes] = {}
    for relative, absolute in targets:
        try:
            payloads[relative] = absolute.read_bytes()
        except OSError:
            return None
    return payloads


def _assemble(
    language: Language,
    checks: tuple[CheckOutcome, ...],
    findings: tuple[StaticFinding, ...],
) -> StaticAnalysisResult:
    """排序、计数、阻断判定与摘要（I11/I13）。"""
    ordered = tuple(sorted(findings, key=_sort_key))
    error_count = sum(1 for item in ordered if item.severity == "error")
    warning_count = sum(1 for item in ordered if item.severity == "warning")
    info_count = sum(1 for item in ordered if item.severity == "info")
    blocking = any(item.severity == "error" and item.blocking for item in ordered)
    return StaticAnalysisResult(
        language=language,
        checks=checks,
        findings=ordered,
        blocking=blocking,
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        result_digest=_digest(language, checks, ordered, blocking),
    )


def _sort_key(finding: StaticFinding) -> tuple[str, int, int, str, str]:
    """I11 排序键：`(file, line, column, rule_id)`；无位置按 0 排在该文件最前，末尾以说明稳序。"""
    return (
        finding.file,
        finding.line if finding.line is not None else 0,
        finding.column if finding.column is not None else 0,
        finding.rule_id,
        finding.message,
    )


def _digest(
    language: Language,
    checks: tuple[CheckOutcome, ...],
    findings: tuple[StaticFinding, ...],
    blocking: bool,
) -> str:
    """结果摘要：由规范化序列计算，同输入两次运行完全相同（含检查状态，故覆盖缺口也进摘要）。"""
    payload = {
        "language": language,
        "checks": [
            {
                "checker": check.checker,
                "status": check.status,
                "reason": check.reason,
                "findings": check.findings,
            }
            for check in checks
        ],
        "findings": [
            {
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "file": finding.file,
                "line": finding.line,
                "column": finding.column,
                "message": finding.message,
                "blocking": finding.blocking,
                "checker": get_rule(finding.rule_id).checker,
            }
            for finding in findings
        ],
        "blocking": blocking,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
