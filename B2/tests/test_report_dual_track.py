"""报告的双轨分解：`overall.score` 是合成分，`dual_track` 是它的可核对分解。

不变量：
* `dual_track.case_score` == 逐项 `awarded` 之和（`total_check.sum_of_awards`），
* `overall.score` == 用例轨比例×用例权重 + AI 分×AI 权重，
* 纯确定性运行（没有 `dual`）时 `dual_track` 为 null，形状与旧报告一致。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from autograder_b2.domain.evidence import CaseEvidence
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.domain.static_result import StaticAnalysisResult
from autograder_b2.evaluation.dual_track import combine
from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy
from autograder_b2.evaluation.report import ReportBinding, ReportInputs, build_report
from autograder_b2.evaluation.scoring import score_run
from autograder_b2.runner.run_case import evidence_digest_of

BASE = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
RUN_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-dual-run"))
SUBMISSION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-dual-submission"))
QUESTION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-dual-question"))


def _digest(name: str) -> str:
    return (name.encode().hex() * 64)[:64]


def _evidence(passed: bool, ordinal: int) -> CaseEvidence:
    return CaseEvidence(
        case_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"b2-dual-case-{ordinal}")),
        ordinal=ordinal,
        weight=10.0,
        passed=passed,
        # 判分器要求判失败必须给出失败分类：少了它，凭据自相矛盾。
        failure_category=None if passed else "output_mismatch",
        input_digest=_digest("input"),
        stdout_digest=_digest("stdout"),
        stderr_digest=_digest("stderr"),
        actual_output_excerpt="42\n",
        expected_output_excerpt="42\n",
        exit_code=0,
        duration_ms=5,
        resource_limit_hit=None,
        sandbox_violations=(),
        message="ok",
        evidence_digest="",
    )


def _static() -> StaticAnalysisResult:
    return StaticAnalysisResult(
        language="python",
        checks=(),
        findings=(),
        blocking=False,
        error_count=0,
        warning_count=0,
        info_count=0,
        result_digest=_digest("static"),
    )


def _security() -> SecurityDecision:
    return SecurityDecision(
        status="not_found",
        findings=(),
        blocking=False,
        covered=("python_ast",),
        unchecked=(),
        rationale="未发现违规模式。",
    )


def _report(*, passed_count: int, total: int, dual_present: bool):
    evidences = [
        _evidence(ordinal < passed_count, ordinal) for ordinal in range(total)
    ]
    fixed = [replace(item, evidence_digest=evidence_digest_of(item)) for item in evidences]
    cases = tuple(
        CaseDescriptor(case_id=item.case_id, ordinal=item.ordinal, weight=item.weight)
        for item in fixed
    )
    breakdown = score_run(
        cases=cases,
        evidence=fixed,
        static=_static(),
        security=_security(),
        comparison=ComparisonPolicy(stdout="exact"),
    )
    dual = None
    if dual_present:
        dual = combine(
            case_score=breakdown.score,
            case_max=breakdown.max_score,
            ai_score=Decimal(82),
            grading_prompt="权重：用例 60% + AI 40%",
        )
    report = build_report(
        ReportInputs(
            evaluation_run_id=RUN_ID,
            submission_id=SUBMISSION_ID,
            question_version_id=QUESTION_ID,
            static=_static(),
            security=_security(),
            evidence=fixed,
            breakdown=breakdown,
            binding=ReportBinding(
                submission_digest=_digest("submission"),
                package_digest=_digest("package"),
                runner_policy_digest=_digest("policy"),
                environment_digest=_digest("environment"),
            ),
            dual=None if dual is None else dual.as_dict(),
        ),
        generated_at=BASE,
    )
    return report, breakdown, dual


def test_dual_track_report_carries_the_composition():
    report, breakdown, dual = _report(passed_count=7, total=10, dual_present=True)
    overall = report["overall"]
    assert dual is not None
    # 用例轨 70 分 × 60% + AI 82 × 40% = 74.80
    assert overall["score"] == float(dual.final_score) == 74.8
    assert overall["max_score"] == 100.0
    assert overall["is_pass"] is True
    track = overall["dual_track"]
    assert track["case_score"] == float(breakdown.score)
    assert track["case_ratio"] == 70.0
    assert track["case_percent"] == 60
    assert track["ai_score"] == 82.0
    assert track["ai_percent"] == 40
    assert track["ai_available"] is True
    # 逐项之和仍等于用例轨总分：报告没有因为合成而丢失可核对性。
    assert report["total_check"]["sum_of_awards"] == float(breakdown.score)
    assert report["total_check"]["matches_total"] is True


def test_report_without_dual_keeps_the_old_shape():
    report, breakdown, _dual = _report(passed_count=10, total=10, dual_present=False)
    overall = report["overall"]
    assert overall["score"] == float(breakdown.score) == 100.0
    assert overall["dual_track"] is None


def test_dual_track_report_fails_below_the_pass_mark():
    report, _breakdown, dual = _report(passed_count=2, total=10, dual_present=True)
    # 20 分 × 60% + 82 × 40% = 12 + 32.8 = 44.80
    assert dual is not None and dual.final_score == Decimal("44.80")
    assert report["overall"]["score"] == 44.8
    assert report["overall"]["is_pass"] is False
