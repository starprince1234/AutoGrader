"""确定性评分：分数只由"静态规则结论 + 安全结论 + 逐用例结果 + package 声明的比较策略"决定。

依据 `MAIN/docs/adr/ADR-0005-deterministic-replayable-scoring.md`：

- **不调用任何模型服务**，不依赖时间、随机数或环境变量；同样的输入必然得到同样的分数；
- **权重来自 package**（学生无法影响），逐项得分 `awarded = weight`（通过）或 `0`（未通过）；
- **逐项之和必须等于总分**：不等即缺陷，抛 `ScoreTotalMismatch` 让测试失败，**不做四舍五入**；
- **`completed` 才可能有分数**：`rejected`/`failed` 不存在分数（0 分是"学生没做对"，不是系统故障）。

金额/分数一律用 `Decimal`（由 `str(float)` 精确构造），避免二进制浮点让"合计相等"变成偶然成立。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from autograder_b2.domain.evidence import CaseEvidence
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.domain.static_result import StaticAnalysisResult
from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy

ZERO = Decimal(0)


class ScoringDefect(RuntimeError):
    """评分缺陷：不是"分数低"，而是评分依据自相矛盾（必须失败而不是被抹平）。"""


class ScoreTotalMismatch(ScoringDefect):
    """逐项得分之和与总分不等。"""


class ScoringPreconditionError(ScoringDefect):
    """不具备评分前置条件（静态/安全未放行）。"""


class EvidenceCaseMismatch(ScoringDefect):
    """证据与 package 声明的用例集合不一致（缺项/多项/权重不符）。"""


class ComparisonEvidenceMismatch(ScoringDefect):
    """用例判定与 package 声明的比较策略矛盾。"""


class TechnicalCaseFailure(ScoringDefect):
    """用例证据带 `infrastructure_error`：这是系统故障，不是学生失败，不得进入评分。"""


@dataclass(frozen=True, slots=True)
class CaseAward:
    """一条逐项得分（每分可追溯到具体用例与权重）。"""

    case_id: str
    ordinal: int
    weight: Decimal
    awarded: Decimal
    passed: bool
    failure_category: str | None


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """一次评分的完整结果（总分不是标量：它可被逐项重算）。"""

    awards: tuple[CaseAward, ...]
    score: Decimal
    max_score: Decimal
    sum_of_awards: Decimal
    matches_total: bool
    is_pass: bool
    summary: str

    @property
    def cases_total(self) -> int:
        return len(self.awards)

    @property
    def cases_passed(self) -> int:
        return sum(1 for award in self.awards if award.passed)


def decimal_of(value: float | str | Decimal) -> Decimal:
    """由十进制字面量精确构造 `Decimal`（`Decimal(str(0.1)) == Decimal("0.1")`）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ScoringDefect("布尔值不是分数")
    return Decimal(str(value))


def build_total_check(awards: Sequence[CaseAward], score: Decimal) -> tuple[Decimal, bool]:
    """合计校验：`sum_of_awards` 与 `matches_total`（契约 `total_check` 的唯一来源）。

    调用方拿到 `matches_total=False` 必须**失败**而不是继续（契约把 `matches_total` 定为 `const true`）。
    """
    total = ZERO
    for award in awards:
        total += award.awarded
    return total, total == score


def assert_totals_match(awards: Sequence[CaseAward], score: Decimal) -> Decimal:
    """合计不符即抛 `ScoreTotalMismatch`（含判据文本，便于定位是哪一项对不上）。"""
    total, matches = build_total_check(awards, score)
    if not matches:
        raise ScoreTotalMismatch(
            f"逐项得分之和 {total} 不等于总分 {score}（差 {total - score}）：这是评分缺陷，不得四舍五入"
        )
    return total


def assert_scoring_allowed(static: StaticAnalysisResult, security: SecurityDecision) -> None:
    """评分前置条件：静态与安全都必须放行。

    "未检查"（`unchecked` 非空）同样不允许评分——把未检查当成"没问题"再给分，等于用分数掩盖未知。
    """
    if static.blocking:
        raise ScoringPreconditionError("静态检查阻断的运行不产生分数（应为 rejected/static_blocked）")
    if security.blocking:
        raise ScoringPreconditionError("安全阻断的运行不产生分数（应为 rejected/security_blocked）")
    if security.unchecked:
        raise ScoringPreconditionError("存在未完成的安全检查，不得评分（未检查 ≠ 安全）")


def _evidence_by_ordinal(evidence: Sequence[CaseEvidence]) -> Mapping[int, CaseEvidence]:
    indexed: dict[int, CaseEvidence] = {}
    for item in evidence:
        if item.ordinal in indexed:
            raise EvidenceCaseMismatch(f"用例 ordinal 重复：{item.ordinal}")
        indexed[item.ordinal] = item
    return indexed


def _assert_evidence_consistent(
    case: CaseDescriptor, item: CaseEvidence, comparison: ComparisonPolicy
) -> None:
    """证据与声明的一致性（比对策略矛盾、通过与否与失败类别矛盾都算缺陷）。

    另有两类证据**不得进入评分**，它们不是"学生没做对"而是运行级结论：`infrastructure_error`
    （技术故障 ⇒ 运行必须落 `failed`）与 `security_blocked`（执行期沙箱违规 ⇒ 运行必须落
    `rejected`）。编排层本来就在评分之前把它们升格掉；这里再拦一次是为了让"升格被删掉"的回归
    **当场失败**，而不是静默给一份越权的代码打出分数。
    """
    if decimal_of(item.weight) != decimal_of(case.weight):
        raise EvidenceCaseMismatch(
            f"用例 {item.ordinal} 的权重与 package 声明不符：证据 {item.weight} / 声明 {case.weight}"
        )
    if item.passed and item.failure_category is not None:
        raise EvidenceCaseMismatch(
            f"用例 {item.ordinal} 判定通过却带失败类别 {item.failure_category!r}"
        )
    if not item.passed and item.failure_category is None:
        raise EvidenceCaseMismatch(f"用例 {item.ordinal} 判定失败却没有失败类别")
    if item.failure_category == "infrastructure_error":
        # runner 对"输入 fixture 缺失/摘要不符"返回该类别：系统故障不得被记成学生扣分。
        raise TechnicalCaseFailure(
            f"用例 {item.ordinal} 的失败类别是基础设施故障：应落到 failed，不得评分"
        )
    if item.failure_category == "security_blocked":
        # 执行期沙箱违规（seccomp 拒绝 / 沙箱自有日志报违规）是运行级**安全拒绝**。
        raise ScoringPreconditionError(
            f"用例 {item.ordinal} 是执行期沙箱违规：应落到 rejected/security_blocked，不得评分"
        )
    if item.failure_category == "output_mismatch" and (
        comparison.stdout == "ignored" and comparison.stderr == "ignored"
    ):
        raise ComparisonEvidenceMismatch(
            f"用例 {item.ordinal} 报告输出不符，但 package 声明 stdout/stderr 都比较—忽略"
        )
    if comparison.stdout == "numeric_tolerance" and comparison.numeric_tolerance is None:
        raise ComparisonEvidenceMismatch("package 声明数值容差比较但未给出 numeric_tolerance")


def score_run(
    *,
    cases: Sequence[CaseDescriptor],
    evidence: Sequence[CaseEvidence],
    static: StaticAnalysisResult,
    security: SecurityDecision,
    comparison: ComparisonPolicy,
) -> ScoreBreakdown:
    """确定性评分：`score = Σ (通过的用例权重)`；不通过记为 0（不是"不得分"，是"这条没拿到"）。"""
    assert_scoring_allowed(static=static, security=security)
    if not cases:
        raise ScoringDefect("没有用例就没有分数：package 必须至少声明一个用例")
    indexed = _evidence_by_ordinal(evidence)
    declared = {case.ordinal for case in cases}
    missing = sorted(declared - set(indexed))
    if missing:
        raise EvidenceCaseMismatch(f"缺少用例证据：ordinal {missing}")
    extra = sorted(set(indexed) - declared)
    if extra:
        raise EvidenceCaseMismatch(f"出现 package 未声明的用例证据：ordinal {extra}")

    awards: list[CaseAward] = []
    for case in sorted(cases, key=lambda item: item.ordinal):
        item = indexed[case.ordinal]
        _assert_evidence_consistent(case, item, comparison)
        weight = decimal_of(case.weight)
        awarded = weight if item.passed else ZERO
        awards.append(
            CaseAward(
                case_id=str(item.case_id),
                ordinal=item.ordinal,
                weight=weight,
                awarded=awarded,
                passed=item.passed,
                failure_category=item.failure_category,
            )
        )
    ordered = tuple(awards)
    score = ZERO
    max_score = ZERO
    for award in ordered:
        score += award.awarded
        max_score += award.weight
    sum_of_awards, matches_total = build_total_check(ordered, score)
    if not matches_total:  # pragma: no cover - build_total_check 与这里的算法同源，仅作不变式兜底
        raise ScoreTotalMismatch("逐项得分之和与总分不等")
    if score > max_score:
        raise ScoreTotalMismatch("总分不得超过满分（逐项权重之和）")
    passed_count = sum(1 for award in ordered if award.passed)
    is_pass = passed_count == len(ordered)
    return ScoreBreakdown(
        awards=ordered,
        score=score,
        max_score=max_score,
        sum_of_awards=sum_of_awards,
        matches_total=matches_total,
        is_pass=is_pass,
        summary=(
            f"共 {len(ordered)} 个用例，通过 {passed_count} 个；"
            f"得分 {score}/{max_score}（静态与安全检查均放行，判定依据为 package 声明的比较策略）。"
        ),
    )
