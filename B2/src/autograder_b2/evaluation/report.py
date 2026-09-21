"""报告组装：受 `MAIN/contracts/schemas/evaluation-report.schema.json` 约束的可解释报告。

要点（`RQ-B-2-80`）：

- **真的过契约校验**：`build_report` 在返回之前调用 `autograder_b2.contracts.contract_schema_errors`
  逐条校验，未通过即抛 `ReportContractError`——不是"看起来像"。
- **每分可追溯**：`case_evidence` 的每一项都带 `case_id`/`ordinal`/`weight`/`awarded`；`total_check`
  的 `sum_of_awards` 由**报告里自己写出的数字**重新加总得到，再与 `overall.score` 比对，不等即
  `ScoreTotalMismatch`（缺陷，不是舍入误差）。
- **`result_digest` 确定**：对报告内容的**稳定投影**取 SHA-256，投影规则**显式**如下（契约把该摘要定义为
  "used as the replay equality check"）：
  * **纳入**：`state`、`overall`（分数/满分/通过与否/结论文本）、`static_findings`、
    `security_conclusion`（三态结论）、`case_evidence` 的每一项（用例标识、序、权重、逐项得分、
    通过与否、失败类别、输出摘要与截断摘要、消息、`artifacts` 的逐文件摘要清单、规则推导的
    `explanation`）、`binding`（五项绑定摘要）、`total_check`、`submission_id`、`question_version_id`；
  * **排除**（每个都有一个"为什么"）：`generated_at`、逐用例 `duration_ms` 与逐用例 `resource`
    是执行时刻/宿主状态相关的读数（`runner` 也把 `duration_ms` 与 `resource` 排除在 `evidence_digest`
    之外——纳入则永远无法重放）；`result_digest` 自身（自指）；**`evaluation_run_id`** 是本次运行实例的
    标识，按定义每次运行都不同，纳入它会使 `ADR-0005` 第 5 条（同五项绑定摘要 ⇒ 同摘要）与
    "重复编排 ⇒ 同 `result_digest`"永不可能成立。
  排除项**不削弱区分力**：五项绑定摘要、分数、逐项得分与逐用例证据（含文件摘要与解释文本）全部参与摘要
  （见 `test_report_digest_discriminates_different_bindings`：任一绑定摘要或任一逐项得分不同 ⇒ 摘要不同）。
- **`resource`/`artifacts`/`explanation` 是 `CHG-0012` 的三个新字段**（都可空）：资源读数是**实测值**
  （读不到就是 null，绝不写 0）、文件证据把"算完即丢"的摘要落进报告（`RQ-B-0-30` 验收 2），
  `explanation` 是**纯函数**推导的说明文本（模型输出进不了这里——schema 是 `additionalProperties: false`，
  这是结构性事实）。
- **学生视图脱敏**：`for_student` 默认**不暴露任何用例的输入/期望输出**（fail-closed：没显式声明可见
  就等于不可见）；`for_teacher` 返回完整报告。
- **只有 `completed` 才有报告**：`rejected`/`failed` 没有分数，也不产生 `evaluation_results` 行；
  拒绝与失败经 SSE 终态帧送达（契约 `EvaluationStreamFrame`）。
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.contracts import EVALUATION_REPORT_SCHEMA, contract_schema_errors
from autograder_b2.domain.clock import is_digest
from autograder_b2.domain.evidence import (
    CaseArtifacts,
    CaseEvidence,
    CaseResource,
    FileChange,
)
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.domain.static_result import StaticAnalysisResult
from autograder_b2.evaluation.explanation import explain_case
from autograder_b2.evaluation.scoring import ScoreBreakdown, ScoreTotalMismatch, decimal_of

#: 报告的终态取值（契约 `state` 枚举）。本模块只产出 `completed`。
REPORT_STATES = ("completed", "rejected", "failed")

#: 摘要投影里被排除的字段：`generated_at`/`duration_ms` 与执行时刻有关（不改变分数与逐项证据），
#: `evaluation_run_id` 是**本次运行实例**的标识（按定义每次运行都不同）——把它算进摘要会让
#: `result_digest` 永远无法充当契约所说的 "replay equality check"（同绑定摘要重复评测必须得到同摘要）。
VOLATILE_REPORT_FIELDS = ("generated_at", "result_digest", "evaluation_run_id")
#: 逐用例条目里被排除的易变字段。
#:
#: `resource` 与 `duration_ms` 同类：cgroup 读数（内存峰值/CPU 时间/进程峰值）是**同一份输入在不同
#: 宿主状态下也会变**的环境读数。把它算进 `result_digest`，"同五项绑定摘要 ⇒ 同摘要"在真实 Linux 上
#: 就永远不成立（ADR-0005 第 5 条）。它照旧进报告、照旧可读、照旧能对着限额看，只是不参与可重放等式。
VOLATILE_CASE_FIELDS = ("duration_ms", "resource")

#: 报告规模上限（防御性：报告不得因用例过多而失控；超出即缺陷而不是静默截断）。
MAX_REPORT_CASES = 1000


class ReportRejected(ValueError):
    """报告输入不满足前置条件（非完成态、证据不成对、摘要形态不合法）。"""


class ReportContractError(RuntimeError):
    """报告未通过 MAIN 契约 schema 校验（消息为逐条错误）。"""


@dataclass(frozen=True, slots=True)
class ReportBinding:
    """五项绑定摘要（可重放性的记录基础，权威定义见 `MAIN/docs/api/identifiers.md` 第 4 节）。"""

    submission_digest: str
    package_digest: str
    runner_policy_digest: str
    environment_digest: str
    question_version_binding: str | None = None
    interpreter_version: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("submission_digest", self.submission_digest),
            ("package_digest", self.package_digest),
            ("runner_policy_digest", self.runner_policy_digest),
            ("environment_digest", self.environment_digest),
        ):
            if not is_digest(value):
                raise ReportRejected(f"{name} 必须是 64 位小写十六进制摘要")
        if self.question_version_binding is not None and not is_digest(
            self.question_version_binding
        ):
            raise ReportRejected("question_version_binding 必须是摘要或 None")

    def as_contract_binding(self) -> dict[str, object]:
        return {
            "submission_digest": self.submission_digest,
            "question_version_binding": self.question_version_binding,
            "package_digest": self.package_digest,
            "runner_policy_digest": self.runner_policy_digest,
            "environment_digest": self.environment_digest,
            "interpreter_version": self.interpreter_version,
        }


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """一份报告的全部输入（每一项都是判分依据的一部分）。"""

    evaluation_run_id: str
    submission_id: str
    question_version_id: str
    static: StaticAnalysisResult
    security: SecurityDecision
    evidence: Sequence[CaseEvidence]
    breakdown: ScoreBreakdown
    binding: ReportBinding
    state: str = "completed"
    summary: str | None = field(default=None)


def _require_digest(name: str, value: object) -> str:
    if not is_digest(value):
        raise ReportRejected(f"{name} 必须是 64 位小写十六进制摘要（证据摘要形态不合法）")
    return str(value)


def _static_findings(static: StaticAnalysisResult) -> list[dict[str, object]]:
    """静态明细：契约字段 `column` 对应表列 `column_number`（差异只在这一层做映射）。"""
    items: list[dict[str, object]] = []
    for finding in sorted(
        static.findings, key=lambda item: (item.file, item.line or 0, item.rule_id)
    ):
        items.append(
            {
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "file": finding.file,
                "line": finding.line,
                "column": finding.column,
                "message": finding.message,
                "blocking": finding.blocking,
            }
        )
    return items


def _security_conclusion(security: SecurityDecision) -> dict[str, object]:
    """安全结论三态**原样**呈现：`not_checked` 绝不折算成 `not_found`。"""
    return {
        "status": security.status,
        "findings": [
            {
                "finding_id": finding.finding_id,
                "category": finding.category,
                "severity": finding.severity,
                "detected_in": finding.detected_in,
                "message": finding.message,
            }
            for finding in security.findings
        ],
    }


def _case_items(inputs: ReportInputs) -> list[dict[str, object]]:
    """逐用例条目：证据与逐项得分必须一一对应（错位即缺陷）。

    `resource`/`artifacts`/`explanation` 是 `CHG-0012` 新增的三个（可空）字段：
    `resource` 是环境读数、`artifacts` 是文件级证据、`explanation` 是**规则推导**的说明文本。
    三者都只由**本用例的证据对象**推出——`explain_case` 是纯函数，模型输出永远进不来这里。
    """
    awards = {award.ordinal: award for award in inputs.breakdown.awards}
    items: list[dict[str, object]] = []
    seen: set[int] = set()
    for item in sorted(inputs.evidence, key=lambda entry: entry.ordinal):
        if item.ordinal in seen:
            raise ReportRejected(f"用例 ordinal 重复：{item.ordinal}")
        seen.add(item.ordinal)
        award = awards.get(item.ordinal)
        if award is None:
            raise ReportRejected(f"用例 {item.ordinal} 没有对应的逐项得分")
        if str(item.case_id) != award.case_id:
            raise ReportRejected(f"用例 {item.ordinal} 的证据与得分指向不同用例")
        items.append(
            {
                "case_id": str(item.case_id),
                "ordinal": item.ordinal,
                "weight": float(decimal_of(item.weight)),
                "awarded": float(award.awarded),
                "passed": bool(item.passed),
                "failure_category": item.failure_category,
                "actual_output_digest": _require_digest(
                    f"用例 {item.ordinal} 的 stdout_digest", item.stdout_digest
                ),
                "actual_output_excerpt": item.actual_output_excerpt,
                "expected_output_excerpt": item.expected_output_excerpt,
                "duration_ms": item.duration_ms,
                "message": item.message,
                "resource": resource_payload(item.resource),
                "artifacts": artifacts_payload(item.artifacts),
                "explanation": explain_case(item),
            }
        )
    extra = sorted(set(awards) - seen)
    if extra:
        raise ReportRejected(f"逐项得分没有对应用例证据：ordinal {extra}")
    return items


def resource_payload(resource: CaseResource | None) -> dict[str, object] | None:
    """契约 `case_resource` 的载荷；`None` = 本用例没有真的执行（不是"读数不可得"）。

    任何一项读不到就是 `None`——**绝不写 0**（见 `domain.evidence.CaseResource`）。
    """
    if resource is None:
        return None
    return {
        "memory_peak_bytes": resource.memory_peak_bytes,
        "cpu_user_seconds": resource.cpu_user_seconds,
        "cpu_system_seconds": resource.cpu_system_seconds,
        "pids_peak": resource.pids_peak,
        "wall_seconds": resource.wall_seconds,
        "limit_hit": resource.limit_hit,
    }


def artifacts_payload(artifacts: CaseArtifacts | None) -> dict[str, object] | None:
    """契约 `case_artifacts` 的载荷。

    `truncated` 是 B2 内部字段（用于解释文本），**不进契约**：报告 schema 没有这个键，
    而 `additionalProperties: false` 会拒绝它。
    """
    if artifacts is None:
        return None
    return {
        "expected_sha256": artifacts.expected_sha256,
        "actual_sha256": artifacts.actual_sha256,
        "created": [_file_change_payload(item) for item in artifacts.created],
        "modified": [_file_change_payload(item) for item in artifacts.modified],
        "deleted": [_file_change_payload(item) for item in artifacts.deleted],
    }


def _file_change_payload(change: FileChange) -> dict[str, object]:
    return {
        "path": change.path,
        "content_sha256": change.content_sha256,
        "size_bytes": change.size_bytes,
    }


def build_report(inputs: ReportInputs, *, generated_at: datetime) -> dict[str, object]:
    """组装并**校验**报告（校验不通过不返回）。"""
    if inputs.state != "completed":
        raise ReportRejected(
            f"只有 completed 才产出报告；rejected/failed 没有分数，经 SSE 终态帧送达（state={inputs.state!r}）"
        )
    if len(inputs.evidence) > MAX_REPORT_CASES:
        raise ReportRejected(f"用例数超过报告上限 {MAX_REPORT_CASES}")
    case_items = _case_items(inputs)
    # 合计校验的**独立**一路：把报告里自己写出的 awarded 重新加总，与总分逐字比对。
    emitted_total = Decimal(0)
    for item in case_items:
        emitted_total += decimal_of(str(item["awarded"]))
    if emitted_total != inputs.breakdown.score:
        raise ScoreTotalMismatch(
            f"报告逐项得分之和 {emitted_total} 不等于总分 {inputs.breakdown.score}"
        )
    report: dict[str, object] = {
        "evaluation_run_id": inputs.evaluation_run_id,
        "submission_id": inputs.submission_id,
        "question_version_id": inputs.question_version_id,
        "state": inputs.state,
        "overall": {
            "score": float(inputs.breakdown.score),
            "max_score": float(inputs.breakdown.max_score),
            "is_pass": bool(inputs.breakdown.is_pass),
            "summary": inputs.summary or inputs.breakdown.summary,
        },
        "static_findings": _static_findings(inputs.static),
        "security_conclusion": _security_conclusion(inputs.security),
        "case_evidence": case_items,
        "binding": inputs.binding.as_contract_binding(),
        "total_check": {
            "sum_of_awards": float(emitted_total),
            "matches_total": True,
        },
        "generated_at": rfc3339_millis(generated_at),
    }
    report["result_digest"] = report_digest(report)
    validate_report(report)
    return report


def report_digest(report: Mapping[str, object]) -> str:
    """报告内容摘要（稳定投影上的 SHA-256）：同输入（同五项绑定摘要）必然同摘要。"""
    payload = _digest_projection(report)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _digest_projection(report: Mapping[str, object]) -> dict[str, object]:
    projection: dict[str, object] = {}
    for key, value in report.items():
        if key in VOLATILE_REPORT_FIELDS:
            continue
        if key == "case_evidence" and isinstance(value, Sequence):
            projection[key] = [
                {
                    item_key: item_value
                    for item_key, item_value in item.items()
                    if item_key not in VOLATILE_CASE_FIELDS
                }
                for item in value
                if isinstance(item, Mapping)
            ]
            continue
        projection[key] = value
    return projection


def validate_report(report: Mapping[str, object]) -> None:
    """按 MAIN 契约校验报告；错误按路径排序返回（空 = 通过）。"""
    errors = contract_schema_errors(EVALUATION_REPORT_SCHEMA, report)
    if errors:
        raise ReportContractError(f"报告未通过 {EVALUATION_REPORT_SCHEMA} 校验：" + "; ".join(errors))


def report_schema_errors(report: Mapping[str, object]) -> list[str]:
    """契约校验错误清单（测试与诊断用；空清单表示通过）。"""
    return contract_schema_errors(EVALUATION_REPORT_SCHEMA, report)


def for_teacher(report: Mapping[str, object]) -> dict[str, object]:
    """教师视图：完整报告（含隐藏用例的期望输出摘要）。"""
    return copy.deepcopy(dict(report))


def for_student(
    report: Mapping[str, object], *, visible_case_ids: Collection[str] = ()
) -> dict[str, object]:
    """学生视图：**默认不暴露任何用例的输入与期望输出**（fail-closed）。

    `visible_case_ids` 由调用方依据 package 的 `visible_to_student` 声明给出；未列出的用例一律省略
    `expected_output_excerpt`、`actual_output_excerpt` 与 `artifacts.expected_sha256`
    （契约允许前两者为 null/缺席，`expected_sha256` 也允许为 null，因此裁剪后仍合法）。

    **`expected_sha256` 为什么必须一并裁掉**：摘要不是内容，但它是一个**可检验的预言机**——
    猜到文件内容就能用摘要验证猜对了。契约把该字段显式标为 PROTECTED，B2 库里没有"用例是否对学生
    可见"这一列（它属于 B3 的 package 声明），所以这里与摘录同口径：**没显式声明可见 = 不可见**。

    `actual_sha256` 与三张文件变更清单**保留**：它们是学生自己产出的文件的摘要与清单，
    与同样保留的 `actual_output_digest` 同类；被裁掉的是"期望侧"。

    `explanation` 也保留：它按构造只含行号、路径、计数与类别，不含期望输出内容（见
    `evaluation.explanation`），而它正是学生视图需要的"哪一行不一样"。
    """
    visible = {str(identifier) for identifier in visible_case_ids}
    view = copy.deepcopy(dict(report))
    cases = view.get("case_evidence")
    if isinstance(cases, list):
        for item in cases:
            if not isinstance(item, dict):
                continue
            if str(item.get("case_id")) in visible:
                continue
            item.pop("expected_output_excerpt", None)
            item.pop("actual_output_excerpt", None)
            artifacts = item.get("artifacts")
            if isinstance(artifacts, dict):
                artifacts["expected_sha256"] = None
    return view
