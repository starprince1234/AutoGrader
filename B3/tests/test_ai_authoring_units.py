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
