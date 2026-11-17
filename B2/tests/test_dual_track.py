"""双轨评分的纯函数单测：权重解析、合成算术、AI 缺失时的退化。

这里不碰数据库与沙箱——`dual_track` 是纯函数模块，它的正确性不该依赖外部设施。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from autograder_b2.evaluation.dual_track import (
    DEFAULT_AI_PERCENT,
    DEFAULT_CASE_PERCENT,
    combine,
    parse_declared_weights,
)


def test_declared_weights_are_read_from_the_rubric_line():
    prompt = "评分细则：\n1. 正确性 70 分\n权重：用例 60% + AI 40%\n2. 可读性 10 分\n"
    assert parse_declared_weights(prompt) == (60, 40)


def test_weights_are_normalized_when_they_do_not_sum_to_one_hundred():
    assert parse_declared_weights("权重：用例 70% + AI 70%") == (50, 50)
    assert parse_declared_weights("权重：用例 80% + AI 20%") == (80, 20)


def test_missing_or_unrelated_percentages_fall_back_to_the_default():
    assert parse_declared_weights(None) == (DEFAULT_CASE_PERCENT, DEFAULT_AI_PERCENT)
    assert parse_declared_weights("") == (DEFAULT_CASE_PERCENT, DEFAULT_AI_PERCENT)
    # 只有正确性占比、没有权重行：不能把 70% 当成权重。
    assert parse_declared_weights("正确性占 70%，可读性占 30%") == (
        DEFAULT_CASE_PERCENT,
        DEFAULT_AI_PERCENT,
    )


def test_combine_weights_both_tracks():
    result = combine(
        case_score=Decimal(7),
        case_max=Decimal(10),
        ai_score=Decimal(82),
        grading_prompt="权重：用例 60% + AI 40%",
    )
    # 70 * 0.6 + 82 * 0.4 = 42 + 32.8 = 74.8
    assert result.case_ratio == Decimal("70.00")
    assert result.final_score == Decimal("74.80")
    assert result.is_pass is True
    assert result.ai_available is True
    assert "用例轨 70.00%" in result.rationale()


def test_combine_fails_below_the_pass_mark():
    result = combine(
        case_score=Decimal(4),
        case_max=Decimal(10),
        ai_score=Decimal(30),
        grading_prompt="权重：用例 60% + AI 40%",
    )
    # 40 * 0.6 + 30 * 0.4 = 24 + 12 = 36
    assert result.final_score == Decimal("36.00")
    assert result.is_pass is False


def test_missing_ai_score_gives_the_case_track_the_whole_weight():
    """AI 挂了不能把学生判 0：用例轨独占，并在分解里写明 AI 未参与。"""
    result = combine(
        case_score=Decimal(9),
        case_max=Decimal(10),
        ai_score=None,
        grading_prompt="权重：用例 50% + AI 50%",
    )
    assert result.ai_available is False
    assert result.final_score == Decimal("90.00")
    assert result.is_pass is True
    assert "AI 评分未参与" in result.rationale()
    assert result.ai_percent == 50


def test_no_cases_hands_the_weight_to_the_ai_track():
    result = combine(case_score=Decimal(0), case_max=Decimal(0), ai_score=Decimal(88))
    assert result.case_percent == 0
    assert result.ai_percent == 100
    assert result.final_score == Decimal("88.00")


def test_no_cases_and_no_ai_is_zero_rather_than_an_error():
    result = combine(case_score=Decimal(0), case_max=Decimal(0), ai_score=None)
    assert result.final_score == Decimal("0.00")
    assert result.is_pass is False


@pytest.mark.parametrize("case_percent, ai_percent", [(0, 100), (100, 0), (33, 67)])
def test_weights_are_preserved_when_they_sum_to_one_hundred(case_percent, ai_percent):
    prompt = f"权重：用例 {case_percent}% + AI {ai_percent}%"
    assert parse_declared_weights(prompt) == (case_percent, ai_percent)
