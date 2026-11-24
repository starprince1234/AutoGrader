"""Pure-Python unit tests for multi-tier reference answers, grading_prompt, and simplified authoring."""

from __future__ import annotations

import uuid

from pydantic import ValidationError
import pytest

from autograder_b3.config import Settings
from autograder_b3.domain.content import compute_content_digest
from autograder_b3.domain.revisions import reference_answer_digest
from autograder_b3.domain.version_content import EditableDraftContent, default_ai_case
from autograder_b3.draft_generation import normalize_proposal
from autograder_b3.dto.internal import (
    AdapterManifestInternal,
    ExecutionManifest,
    QuestionDraftInputModel,
    ReferenceAnswerDelivery,
    ReferenceAnswerInternal,
    VersionContentSnapshot,
)


def test_draft_input_allows_omitted_adapter_and_empty_cases() -> None:
    model = QuestionDraftInputModel.model_validate(
        {
            "title": "Two Sum Logic",
            "stem_markdown": "Given a list and target, find indices.",
            "language": "python",
            "reference_answers": [
                {"label": "暴力解法 (50分)", "body": "def f(a, t): pass\n"},
                {"label": "哈希表最优 (100分)", "body": "def f(a, t): return {}\n"},
            ],
            "grading_prompt": "使用双重循环给 50 分，使用哈希表给 95-100 分。",
            "cases": [],
        }
    )
    assert model.adapter.kind == "stdin"
    assert len(model.reference_answers) == 2
    assert model.grading_prompt == "使用双重循环给 50 分，使用哈希表给 95-100 分。"
    assert model.cases == ()

    with pytest.raises(ValidationError):
        QuestionDraftInputModel.model_validate(
            {
                "title": "Bad",
                "stem_markdown": "Stem",
                "language": "python",
                "cases": None,
            }
        )


def test_content_digest_and_wire_include_tiers_and_grading_prompt() -> None:
    qid = uuid.uuid4()
    vid = uuid.uuid4()
    case = default_ai_case(vid)
    tier0 = ReferenceAnswerInternal(
        reference_answer_id=uuid.uuid4(),
        question_version_id=vid,
        language="python",
        body="print(1)\n",
        ordinal=0,
        label="暴力解法",
        grading_prompt="按复杂度评分",
    )
    tier1 = ReferenceAnswerInternal(
        reference_answer_id=uuid.uuid4(),
        question_version_id=vid,
        language="python",
        body="print(2)\n",
        ordinal=1,
        label="最优解法",
        grading_prompt="按复杂度评分",
    )

    snap_single = VersionContentSnapshot(
        question_version_id=vid,
        question_id=qid,
        version=1,
        language="python",
        stem_markdown="Solve it",
        reference_answers=(tier0,),
        manifest=AdapterManifestInternal(kind="stdin"),
        cases=(case,),
        fixtures=(),
        max_files=20,
        max_total_bytes=1024,
        execution=ExecutionManifest(),
    )
    snap_multi = VersionContentSnapshot(
        question_version_id=vid,
        question_id=qid,
        version=1,
        language="python",
        stem_markdown="Solve it",
        reference_answers=(tier0, tier1),
        manifest=AdapterManifestInternal(kind="stdin"),
        cases=(case,),
        fixtures=(),
        max_files=20,
        max_total_bytes=1024,
        execution=ExecutionManifest(),
    )

    assert compute_content_digest(snap_single) != compute_content_digest(snap_multi)
    assert reference_answer_digest((tier0,)) != reference_answer_digest((tier0, tier1))

    editable = EditableDraftContent(
        reference_answer=tier0.body,
        reference_answers=({"label": tier0.label, "body": tier0.body}, {"label": tier1.label, "body": tier1.body}),
        grading_prompt="按复杂度评分",
        cases=(case,),
        execution=ExecutionManifest(),
    ).to_wire()
    assert len(editable["reference_answers"]) == 2
    assert editable["grading_prompt"] == "按复杂度评分"

    delivery = ReferenceAnswerDelivery(
        question_version_id=vid,
        content_digest="a" * 64,
        language="python",
        adapter_kind="stdin",
        body=tier0.body,
        stem_markdown="Solve it",
        reference_answers=({"label": tier0.label, "body": tier0.body}, {"label": tier1.label, "body": tier1.body}),
        grading_prompt="按复杂度评分",
    ).to_wire()
    assert delivery["stem_markdown"] == "Solve it"
    assert len(delivery["reference_answers"]) == 2
    assert delivery["grading_prompt"] == "按复杂度评分"


def test_normalize_proposal_supports_tiered_answers_without_cases() -> None:
    outcome = normalize_proposal(
        {
            "title": "Unix Text Count",
            "stem_markdown": "Count unique lines.",
            "reference_answers": [
                {"label": "基础管道", "body": "sort | uniq | wc -l\n"},
                {"label": "awk 一次遍历", "body": "awk '!seen[$0]++{c++} END{print c}'\n"},
            ],
            "grading_prompt": "awk 解法给 90+，管道组合给 70。",
        },
        language="bash",
        adapter_kind="stdin",
        limits=Settings(),
    )
    assert outcome.status == "ready"
    assert outcome.warnings == ()
    assert outcome.proposal is not None
    assert len(outcome.proposal.reference_answers) == 2
    assert outcome.proposal.reference_answer == "sort | uniq | wc -l\n"
    assert outcome.proposal.grading_prompt == "awk 解法给 90+，管道组合给 70。"


def test_proposal_from_storage_preserves_reference_tiers_and_grading_prompt() -> None:
    from types import SimpleNamespace
    from autograder_b3.domain.draft_generations import _proposal_from_storage

    outcome = normalize_proposal(
        {
            "title": "1 到 100",
            "stem_markdown": "输出 1 到 100",
            "reference_answers": [
                {"label": "解法一：while 循环", "body": "i=1\nwhile i<=100:\n print(i)\n i+=1\n"},
                {"label": "解法二：for range", "body": "for i in range(1, 101):\n print(i)\n"},
            ],
            "grading_prompt": "精妙解法 90-100 分，暴力解法 70-85 分，兼顾代码简洁度。",
        },
        language="python",
        adapter_kind="stdin",
        limits=Settings(),
        desired_reference_tiers=2,
    )
    assert outcome.proposal is not None
    stored_row = SimpleNamespace(
        proposal=outcome.proposal.to_storage(),
        language="python",
        adapter_kind="stdin",
    )
    restored = _proposal_from_storage(stored_row)
    assert restored is not None
    assert len(restored.reference_answers) == 2
    assert restored.reference_answers[0].label == "解法一：while 循环"
    assert restored.reference_answers[1].label == "解法二：for range"
    assert restored.grading_prompt == "精妙解法 90-100 分，暴力解法 70-85 分，兼顾代码简洁度。"


def test_single_pass_provider_builds_the_whole_candidate_in_one_call(monkeypatch):
    """One request, one answer: no stage chaining, no repair round trip.

    The service is behind an edge proxy with a ~100 s request budget, so a generation that
    needs several sequential model calls is not a slow feature but an unreachable one (the
    teacher sees a 524 instead of a candidate). This test pins the shape that replaced it:
    exactly one call, a single attempt, and the whole candidate asked for at once.
    """
    import json

    from autograder_b3.draft_generation import (
        ResponsesDraftGenerationProvider,
        build_generation_payload,
    )
    from autograder_platform.llm_client import LlmConfig, TokenUsage
    import autograder_b3.draft_generation as dg_mod

    calls: list[dict] = []

    def fake_complete_json(*, config, system_prompt, user_prompt, **kwargs):  # noqa: ARG001
        calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, **kwargs})
        return (
            {
                "title": "整数加法",
                "stem_markdown": "读取两个整数，输出它们的和。\n\n示例：\n输入：`3 5`\n输出：`8`",
                "tags": ["python", "stdin"],
                "difficulty": "简单",
                "canonical_io_rules": "输入一行两个整数；输出一个整数加换行。",
                "adapter": {
                    "expect_exit_code": 0,
                    "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
                    "callable": None,
                },
                "public_example": {
                    "name": "公开示例",
                    "stdin": "3 5\n",
                    "args": [],
                    "expected_stdout": "8\n",
                    "expected_exit_code": 0,
                },
                "reference_answers": [
                    {"label": "解法一：split 解包", "body": "a, b = map(int, input().split())\nprint(a + b)"},
                    {"label": "解法二：sum 简洁写法", "body": "print(sum(map(int, input().split())))"},
                ],
                "reference_answer": "print(sum(map(int, input().split())))",
                "grading_prompt": "权重：用例 60% + AI 40%\n1. 正确解析输入并输出结果。",
                "hidden_cases": [
                    {
                        "name": f"隐藏用例 {i}",
                        "stdin": f"{i} {i + 1}\n",
                        "args": [],
                        "expected_stdout": f"{2 * i + 1}\n",
                        "expected_exit_code": 0,
                    }
                    for i in range(1, 11)
                ],
            },
            TokenUsage(490, 400, 890),
        )

    monkeypatch.setattr(dg_mod, "complete_json", fake_complete_json)
    provider = ResponsesDraftGenerationProvider(
        LlmConfig(
            base_url="https://api.xiaomimimo.com",
            model="mimo-v2-flash",
            api_key="k",
            timeout_seconds=90.0,
        )
    )
    payload = build_generation_payload(
        learning_objective="计算两个整数之和",
        language="python",
        adapter_kind="stdin",
        difficulty="简单",
        tags=["python"],
        desired_reference_tiers=2,
    )
    answer = provider.generate(payload=payload)

    assert len(calls) == 1
    assert calls[0]["max_attempts"] == 2
    assert calls[0]["max_output_tokens"] == 8192
    # The whole exchange (retries included) is bounded by one deadline, and it must stay inside
    # the edge's request budget so a slow provider becomes a retryable 502, never a 524.
    assert calls[0]["deadline_seconds"] <= 90.0
    assert "Single pass" in calls[0]["user_prompt"]
    assert "计算两个整数之和" in calls[0]["user_prompt"]
    assert (answer.usage.prompt_tokens, answer.usage.completion_tokens, answer.usage.total_tokens) == (
        490,
        400,
        890,
    )

    outcome = normalize_proposal(
        answer.document,
        language="python",
        adapter_kind="stdin",
        limits=Settings(),
        desired_reference_tiers=2,
        require_dual_track_cases=True,
    )
    assert outcome.status == "ready"
    assert outcome.warnings == ()
    assert outcome.proposal is not None
    assert len(outcome.proposal.cases) == 11
    assert sum(1 for c in outcome.proposal.cases if c.visible_to_student) == 1
    assert sum(1 for c in outcome.proposal.cases if not c.visible_to_student) == 10
    assert [c.ordinal for c in outcome.proposal.cases] == list(range(11))
    assert all(c.expected_stdout and c.expected_stdout.strip() for c in outcome.proposal.cases)
    assert json.loads(json.dumps(outcome.proposal.to_wire()))["cases"][0]["visible_to_student"] is True


def test_single_pass_provider_drops_unusable_cases_instead_of_paying_for_a_repair(monkeypatch):
    """A short or malformed answer becomes `needs_review`, never another model call.

    Dropping the unusable cases is the same validation that always ran; what changed is that
    nothing is *repaired* by a second paid round trip. The teacher gets the candidate and a
    field-level warning they can act on in the editor.
    """
    from autograder_b3.draft_generation import (
        ResponsesDraftGenerationProvider,
        build_generation_payload,
    )
    from autograder_platform.llm_client import LlmConfig, TokenUsage
    import autograder_b3.draft_generation as dg_mod

    calls: list[dict] = []

    def fake_complete_json(*, config, system_prompt, user_prompt, **kwargs):  # noqa: ARG001
        calls.append({"user_prompt": user_prompt})
        return (
            {
                "title": "两数相加函数",
                "stem_markdown": "在 main.py 实现 add_two(a, b)。",
                "tags": ["python", "callable"],
                "canonical_io_rules": "接收两个字符串参数，返回整数和。",
                "adapter": {
                    "expect_exit_code": 0,
                    "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
                    "callable": {
                        "module": "main",
                        "attribute": "add_two",
                        "arguments_json": '["stale"]',
                    },
                },
                "public_example": {
                    "name": "公开示例",
                    "stdin": None,
                    "args": [3, 5],
                    "expected_stdout": "8\n",
                },
                "reference_answers": [
                    {"label": "基础解法", "body": "def add_two(a, b): return int(a) + int(b)"},
                    {"label": "健壮解法", "body": "def add_two(a, b): return sum((int(a), int(b)))"},
                ],
                "reference_answer": "def add_two(a, b): return sum((int(a), int(b)))",
                "grading_prompt": "权重：用例 60% + AI 40%\n1. 正确导出 add_two。",
                "hidden_cases": [
                    # Blank expected output: unusable, dropped.
                    {"name": "空白输出无效", "args": ["1", "1"], "expected_stdout": ""},
                    # Same input as the public example: a duplicate, dropped.
                    {"name": "重复公开用例", "args": ["3", "5"], "expected_stdout": "8\n"},
                    *[
                        {"name": f"基础 {i}", "args": [i, 10], "expected_stdout": f"{i + 10}\n"}
                        for i in range(1, 9)
                    ],
                ],
            },
            TokenUsage(10, 10, 20),
        )

    monkeypatch.setattr(dg_mod, "complete_json", fake_complete_json)
    provider = ResponsesDraftGenerationProvider(
        LlmConfig(base_url="https://api.xiaomimimo.com", model="m", api_key="k")
    )
    payload = build_generation_payload(
        learning_objective="实现加法函数",
        language="python",
        adapter_kind="callable",
        difficulty="简单",
        tags=[],
        desired_reference_tiers=2,
    )
    answer = provider.generate(payload=payload)
    assert len(calls) == 1

    outcome = normalize_proposal(
        answer.document,
        language="python",
        adapter_kind="callable",
        limits=Settings(),
        desired_reference_tiers=2,
        require_dual_track_cases=True,
    )
    # 1 public + 8 usable hidden cases: the shape is short, so the teacher must review it.
    assert outcome.status == "needs_review"
    assert outcome.proposal is not None
    assert outcome.warnings != ()
    assert len(outcome.proposal.cases) == 9
    # Stale arguments_json must be cleared so per-case args apply.
    assert outcome.proposal.adapter.callable_arguments_json is None
    assert outcome.proposal.cases[0].args == ("3", "5")
    assert answer.usage.total_tokens == 20
