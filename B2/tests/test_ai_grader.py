"""Unit tests for unified AI logic grading, multi-tier reference context, and AI scoring."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest

from autograder_b2.commentary.provider import CommentaryProviderConfig, CommentaryProviderError
from autograder_b2.domain.evidence import CaseEvidence
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.domain.static_result import CheckOutcome, StaticAnalysisResult, StaticFinding
from autograder_b2.evaluation.ai_grader import (
    DEFAULT_GRADING_PROMPT,
    AiGradingError,
    AiLogicGrader,
    build_grading_prompt,
    parse_ai_grade_response,
)
from autograder_b2.evaluation.explanation import explain_case
from autograder_b2.evaluation.ports import CaseDescriptor
from autograder_b2.evaluation.scoring import ScoringPreconditionError, score_ai_run
from autograder_b2.reference.b3 import (
    ReferenceAnswerNotFound,
    ReferenceAnswerUnavailable,
    ReferenceAnswerView,
    _parse_answer,
)

VERSION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-grader-question-version"))
DIGEST = "a" * 64
CASE_1 = str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-grader-case-1"))
CASE_2 = str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-grader-case-2"))


def _clean_static(*, blocking: bool = False, findings: tuple[StaticFinding, ...] = ()) -> StaticAnalysisResult:
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings if item.severity == "warning")
    return StaticAnalysisResult(
        language="python",
        checks=(CheckOutcome(checker="python_ast", status="ran", reason=None, findings=len(findings)),),
        findings=findings,
        blocking=blocking,
        error_count=errors,
        warning_count=warnings,
        info_count=0,
        result_digest="b" * 64,
    )


def _clean_security(*, blocking: bool = False, unchecked: tuple[str, ...] = ()) -> SecurityDecision:
    return SecurityDecision(
        status="not_checked" if unchecked else ("found" if blocking else "not_found"),
        findings=(),
        blocking=blocking,
        covered=() if unchecked else ("static_rules",),
        unchecked=unchecked,
        rationale="ok" if not blocking and not unchecked else "blocked",
    )


@dataclass
class _StubReferenceProvider:
    view: ReferenceAnswerView | Exception

    def fetch_reference_answer(self, question_version_id: str) -> ReferenceAnswerView:
        assert question_version_id == VERSION_ID
        if isinstance(self.view, Exception):
            raise self.view
        return self.view


class _StubCommentaryProvider:
    def __init__(self, response: str | Exception) -> None:
        self._response = response
        self.prompts: list[str] = []
        self.config = CommentaryProviderConfig(
            provider="openai-compat",
            base_url="https://example.invalid/v1",
            model="gpt-test",
            api_key="test-key",
        )

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_parse_answer_supports_multi_tier_and_backward_compat() -> None:
    rich_body = json.dumps(
        {
            "question_version_id": VERSION_ID,
            "content_digest": DIGEST,
            "language": "python",
            "adapter_kind": "stdin",
            "reference_answer": "def solve(nums): return sorted(nums)",
            "stem_markdown": "实现高效排序或两数之和",
            "reference_answers": [
                {"label": "暴力解法 (50分基准)", "body": "for i in nums:\n  pass"},
                {"label": "哈希优化 (80分基准)", "body": "seen = {}\n"},
            ],
            "grading_prompt": "使用双层循环最高 55 分，哈希表 O(n) 起步 85 分。",
        }
    )
    rich = _parse_answer(rich_body, expected_version=VERSION_ID)
    assert rich.stem_markdown == "实现高效排序或两数之和"
    assert len(rich.reference_answers) == 2
    assert rich.reference_answers[0]["label"] == "暴力解法 (50分基准)"
    assert rich.grading_prompt == "使用双层循环最高 55 分，哈希表 O(n) 起步 85 分。"

    minimal_body = json.dumps(
        {
            "question_version_id": VERSION_ID,
            "content_digest": DIGEST,
            "language": "bash",
            "adapter_kind": "stdin",
            "reference_answer": "ls -la | wc -l",
        }
    )
    minimal = _parse_answer(minimal_body, expected_version=VERSION_ID)
    assert minimal.stem_markdown == ""
    assert minimal.reference_answers == ({"label": "标准参考答案", "body": "ls -la | wc -l"},)
    assert minimal.grading_prompt is None


def test_build_grading_prompt_includes_tiers_rubric_and_static_findings() -> None:
    reference = ReferenceAnswerView(
        question_version_id=VERSION_ID,
        content_digest=DIGEST,
        language="python",
        adapter_kind="stdin",
        reference_answer="print(1)",
        stem_markdown="统计日志中出现次数最多的 IP。",
        reference_answers=(
            {"label": "暴力计数 (50分基准)", "body": "count = {}"},
            {"label": "Counter 优化 (85分基准)", "body": "from collections import Counter"},
        ),
        grading_prompt="优先看逻辑正确性与冗余代码扣分。",
    )
    static = _clean_static(
        blocking=True,
        findings=(
            StaticFinding(
                rule_id="PY_SYNTAX",
                severity="error",
                file="main.py",
                line=3,
                column=1,
                message="expected ':'",
                blocking=True,
            ),
        ),
    )
    prompt = build_grading_prompt(
        language="python",
        reference=reference,
        static=static,
        sources={"main.py": "def solve()\n  pass"},
    )
    assert "统计日志中出现次数最多的 IP" in prompt
    assert "暴力计数 (50分基准)" in prompt
    assert "Counter 优化 (85分基准)" in prompt
    assert "优先看逻辑正确性与冗余代码扣分。" in prompt
    assert "[ERROR][PY_SYNTAX] main.py:3: expected ':'" in prompt
    assert "def solve()" in prompt

    fallback_prompt = build_grading_prompt(
        language="python",
        reference=ReferenceAnswerView(
            question_version_id=VERSION_ID,
            content_digest=DIGEST,
            language="python",
            adapter_kind="stdin",
            reference_answer="print(42)",
        ),
        static=_clean_static(),
        sources={"main.py": "print(42)"},
    )
    assert DEFAULT_GRADING_PROMPT in fallback_prompt
    assert "标准参考答案" in fallback_prompt


def test_parse_ai_grade_response_clamps_and_validates() -> None:
    fenced = """```json
    {
      "score": 84.567,
      "summary": "采用了线性哈希解法，结构清晰但缺少空数组防护。",
      "strengths": ["时间复杂度 O(n)"],
      "issues": ["未处理空输入"],
      "suggestions": ["补充边界检查"]
    }
    ```"""
    parsed = parse_ai_grade_response(fenced)
    assert parsed.score == Decimal("84.57")
    assert "【综合评价】" in parsed.formatted_feedback
    assert "时间复杂度 O(n)" in parsed.formatted_feedback

    high = parse_ai_grade_response('{"score": 150, "summary": "too high"}')
    assert high.score == Decimal("100.00")
    low = parse_ai_grade_response('{"score": -12.3, "summary": "too low"}')
    assert low.score == Decimal("0.00")

    for bad_payload in (
        "not json at all",
        '{"summary": "missing score"}',
        '{"score": true}',
        '{"score": NaN}',
        '{"score": "abc"}',
        '["not", "an", "object"]',
    ):
        with pytest.raises(AiGradingError):
            parse_ai_grade_response(bad_payload)


def test_score_ai_run_allows_static_blocking_and_enforces_security() -> None:
    static_blocking = _clean_static(
        blocking=True,
        findings=(
            StaticFinding(
                rule_id="PY_SYNTAX",
                severity="error",
                file="main.py",
                line=1,
                column=1,
                message="invalid syntax",
                blocking=True,
            ),
        ),
    )
    cases = (
        CaseDescriptor(case_id=CASE_1, ordinal=0, weight=100.0, visible_to_student=True),
    )
    breakdown = score_ai_run(
        cases=cases,
        raw_score=Decimal("78.5"),
        static=static_blocking,
        security=_clean_security(),
        summary_note="逻辑基本完整，存在一处语法笔误",
    )
    assert breakdown.score == Decimal("78.50")
    assert breakdown.max_score == Decimal("100.00")
    assert breakdown.sum_of_awards == Decimal("78.50")
    assert breakdown.matches_total is True
    assert breakdown.is_pass is True
    assert "静态检查提示 1 个语法/规则错误" in breakdown.summary

    split_cases = (
        CaseDescriptor(case_id=CASE_1, ordinal=0, weight=40.0, visible_to_student=True),
        CaseDescriptor(case_id=CASE_2, ordinal=1, weight=60.0, visible_to_student=True),
    )
    split_breakdown = score_ai_run(
        cases=split_cases,
        raw_score="55.33",
        static=_clean_static(),
        security=_clean_security(),
    )
    assert split_breakdown.score == Decimal("55.33")
    assert split_breakdown.is_pass is False
    assert split_breakdown.sum_of_awards == Decimal("55.33")
    assert split_breakdown.matches_total is True

    with pytest.raises(ScoringPreconditionError):
        score_ai_run(
            cases=cases,
            raw_score=80,
            static=_clean_static(),
            security=_clean_security(blocking=True),
        )
    with pytest.raises(ScoringPreconditionError):
        score_ai_run(
            cases=cases,
            raw_score=80,
            static=_clean_static(),
            security=_clean_security(unchecked=("static_rules",)),
        )


def test_ai_logic_grader_and_explanation() -> None:
    reference = ReferenceAnswerView(
        question_version_id=VERSION_ID,
        content_digest=DIGEST,
        language="bash",
        adapter_kind="stdin",
        reference_answer="find /var/log -name '*.log' | wc -l",
        stem_markdown="统计日志目录中 .log 文件数量。",
        reference_answers=({"label": "标准 Shell 管道解法 (85分基准)", "body": "find /var/log -name '*.log' | wc -l"},),
        grading_prompt="注意不要解析 ls 输出。",
    )
    provider = _StubCommentaryProvider(
        json.dumps(
            {
                "score": 88,
                "summary": "正确使用 find 配合 wc -l 完成统计。",
                "strengths": ["避免了对 ls 输出的脆弱解析"],
                "issues": [],
                "suggestions": ["可加 -type f 排除目录干扰"],
            }
        )
    )
    grader = AiLogicGrader(
        reference_answers=_StubReferenceProvider(reference),
        provider=provider,
    )
    result = grader.grade(
        question_version_id=VERSION_ID,
        language="bash",
        static=_clean_static(),
        sources={"solution.sh": "find /var/log -name '*.log' | wc -l"},
    )
    assert result.score == Decimal("88.00")
    assert result.provider == "openai-compat"
    assert result.model == "gpt-test"
    assert "避免了对 ls 输出的脆弱解析" in result.formatted_feedback
    assert len(provider.prompts) == 1

    evidence = CaseEvidence(
        case_id=CASE_1,
        ordinal=0,
        weight=100.0,
        passed=True,
        failure_category=None,
        input_digest=DIGEST,
        stdout_digest=DIGEST,
        stderr_digest=DIGEST,
        actual_output_excerpt=None,
        expected_output_excerpt=None,
        exit_code=0,
        duration_ms=10,
        resource_limit_hit=None,
        sandbox_violations=(),
        message="AI 逻辑评分：88.00/100（gpt-test）。",
        evidence_digest=DIGEST,
    )
    assert explain_case(evidence) == "AI 逻辑与代码质量评测完成（详见 AI 评测反馈）。"

    for upstream_err in (
        ReferenceAnswerUnavailable("b3 down"),
        ReferenceAnswerNotFound("missing"),
    ):
        failing_grader = AiLogicGrader(
            reference_answers=_StubReferenceProvider(upstream_err),
            provider=provider,
        )
        with pytest.raises(AiGradingError):
            failing_grader.grade(
                question_version_id=VERSION_ID,
                language="bash",
                static=_clean_static(),
                sources={"solution.sh": "echo 1"},
            )

    provider_err_grader = AiLogicGrader(
        reference_answers=_StubReferenceProvider(reference),
        provider=_StubCommentaryProvider(CommentaryProviderError("timeout")),
    )
    with pytest.raises(AiGradingError):
        provider_err_grader.grade(
            question_version_id=VERSION_ID,
            language="bash",
            static=_clean_static(),
            sources={"solution.sh": "echo 1"},
        )
