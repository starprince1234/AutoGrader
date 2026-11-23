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


def test_dag_provider_runs_stage1_then_parallel_stage2_and_builds_1_plus_10_cases(monkeypatch):
    import json
    import threading
    from autograder_b3.draft_generation import (
        ResponsesDraftGenerationProvider,
        build_generation_payload,
    )
    from autograder_b3.llm_client import LlmConfig, TokenUsage

    stage2_barrier = threading.Barrier(3, timeout=3.0)
    call_stages: list[str] = []

    def fake_complete_json(*, config, system_prompt, user_prompt, **kwargs):  # noqa: ARG001
        if "Stage 1 (Spec)" in user_prompt:
            call_stages.append("stage1")
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
                },
                TokenUsage(100, 80, 180),
            )

        # All 3 Stage 2 branches must execute concurrently.
        stage2_barrier.wait()
        if "Stage 2A" in user_prompt:
            call_stages.append("stage2_solutions")
            return (
                {
                    "reference_answers": [
                        {"label": "解法一：split 解包", "body": "a, b = map(int, input().split())\nprint(a + b)"},
                        {"label": "解法二：sum 简洁写法", "body": "print(sum(map(int, input().split())))"},
                    ],
                    "reference_answer": "print(sum(map(int, input().split())))",
                    "grading_prompt": "权重：用例 60% + AI 40%\n1. 正确使用输入解析并输出结果。",
                },
                TokenUsage(120, 90, 210),
            )
        if "public_example_and_5_basic_hidden_cases" in user_prompt:
            call_stages.append("stage2_batch1")
            return (
                {
                    "public_case": {
                        "name": "公开示例",
                        "stdin": "3 5\n",
                        "args": [],
                        "expected_stdout": "8\n",
                        "expected_exit_code": 0,
                    },
                    "hidden_cases": [
                        {"name": f"基础用例 {i}", "stdin": f"{i} {i + 1}\n", "args": [], "expected_stdout": f"{2 * i + 1}\n"}
                        for i in range(1, 6)
                    ],
                },
                TokenUsage(130, 110, 240),
            )
        assert "5_advanced_edge_hidden_cases" in user_prompt
        call_stages.append("stage2_batch2")
        return (
            {
                "hidden_cases": [
                    {"name": f"边界用例 {i}", "stdin": f"-{i} {i * 2}\n", "args": [], "expected_stdout": f"{i}\n"}
                    for i in range(1, 6)
                ]
            },
            TokenUsage(140, 120, 260),
        )

    import autograder_b3.draft_generation as dg_mod

    monkeypatch.setattr(dg_mod, "complete_json", fake_complete_json)
    provider = ResponsesDraftGenerationProvider(
        LlmConfig(base_url="https://api.xiaomimimo.com", model="mimo-v2-flash", api_key="k")
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
    assert call_stages[0] == "stage1"
    assert set(call_stages[1:]) == {"stage2_solutions", "stage2_batch1", "stage2_batch2"}
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


def test_dag_provider_repairs_short_hidden_cases_and_callable_args(monkeypatch):
    from autograder_b3.draft_generation import (
        ResponsesDraftGenerationProvider,
        build_generation_payload,
    )
    from autograder_b3.llm_client import LlmConfig, TokenUsage
    import autograder_b3.draft_generation as dg_mod

    def fake_complete_json(*, config, system_prompt, user_prompt, **kwargs):  # noqa: ARG001
        if "Stage 1 (Spec)" in user_prompt:
            return (
                {
                    "title": "两数相加函数",
                    "stem_markdown": "在 main.py 实现 add_two(a, b)。",
                    "tags": ["python", "callable"],
                    "canonical_io_rules": "接收两个字符串参数，返回整数和。",
                    "adapter": {
                        "expect_exit_code": 0,
                        "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
                        "callable": {"module": "main", "attribute": "add_two", "arguments_json": '["stale"]'},
                    },
                    "public_example": {
                        "name": "公开示例",
                        "stdin": None,
                        "args": [3, 5],
                        "expected_stdout": "8\n",
                    },
                },
                TokenUsage(10, 10, 20),
            )
        if "Stage 2A" in user_prompt:
            return (
                {
                    "reference_answers": [
                        {"label": "基础解法", "body": "def add_two(a, b): return int(a) + int(b)"},
                        {"label": "健壮解法", "body": "def add_two(a, b): return sum((int(a), int(b)))"},
                    ],
                    "reference_answer": "def add_two(a, b): return sum((int(a), int(b)))",
                    "grading_prompt": "权重：用例 60% + AI 40%\n1. 正确导出 add_two。",
                },
                TokenUsage(10, 10, 20),
            )
        if "public_example_and_5_basic_hidden_cases" in user_prompt:
            return (
                {
                    "public_case": {"name": "公开示例", "args": [3, 5], "expected_stdout": "8\n"},
                    "hidden_cases": [
                        {"name": "空白输出无效", "args": ["1", "1"], "expected_stdout": ""},
                        {"name": "重复公开用例", "args": ["3", "5"], "expected_stdout": "8\n"},
                        *[{"name": f"基础 {i}", "args": [i, 10], "expected_stdout": f"{i + 10}\n"} for i in range(4)],
                    ],
                },
                TokenUsage(10, 10, 20),
            )
        if "5_advanced_edge_hidden_cases" in user_prompt:
            return (
                {
                    "hidden_cases": [
                        {"name": f"边界 {i}", "args": [-i, 20], "expected_stdout": f"{20 - i}\n"}
                        for i in range(1, 5)
                    ]
                },
                TokenUsage(10, 10, 20),
            )
        assert "Stage 3 (Repair Missing Cases)" in user_prompt
        return (
            {
                "hidden_cases": [
                    {"name": "补齐 1", "args": ["100", "200"], "expected_stdout": "300\n"},
                    {"name": "补齐 2", "args": ["-99", "-1"], "expected_stdout": "-100\n"},
                ]
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
    outcome = normalize_proposal(
        answer.document,
        language="python",
        adapter_kind="callable",
        limits=Settings(),
        desired_reference_tiers=2,
        require_dual_track_cases=True,
    )
    assert outcome.status == "ready"
    assert outcome.proposal is not None
    # Stale arguments_json must be cleared so per-case args apply.
    assert outcome.proposal.adapter.callable_arguments_json is None
    assert len(outcome.proposal.cases) == 11
    assert outcome.proposal.cases[0].args == ("3", "5")
    assert outcome.proposal.cases[1].args == ("0", "10")
    assert (answer.usage.total_tokens) == 100
