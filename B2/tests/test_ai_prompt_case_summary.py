"""AI 轨的提示词里必须出现确定性轨结论，且不得因此泄漏隐藏用例内容。"""

from __future__ import annotations

from autograder_b2.evaluation.ai_grader import build_grading_prompt


class _Reference:
    grading_prompt = "权重：用例 60% + AI 40%\n正确性 70 分，可读性 30 分。"
    stem_markdown = "读入 n，输出 n 的平方。"
    reference_answer = "n = int(input())\nprint(n * n)\n"
    reference_answers = ()
    language = "python"


class _Static:
    findings = ()
    error_count = 0
    warning_count = 0


def _prompt(case_summary: str | None) -> str:
    return build_grading_prompt(
        language="python",
        reference=_Reference(),  # type: ignore[arg-type]
        static=_Static(),  # type: ignore[arg-type]
        sources={"main.py": "print(4)\n"},
        case_summary=case_summary,
    )


def test_case_summary_is_included_as_evidence():
    prompt = _prompt("通过 7/10 个用例。\n- 用例 1：通过")
    assert "本题目测用例执行结果" in prompt
    assert "通过 7/10 个用例。" in prompt
    assert "它比你对代码风格的印象更权威" in prompt


def test_ai_only_prompt_says_nothing_about_cases():
    prompt = _prompt(None)
    assert "本题目测用例执行结果" not in prompt


def test_weights_line_survives_the_new_block():
    """权重声明必须原样留在 prompt 里：双轨合成依赖题目自己声明的那一行。"""
    prompt = _prompt("通过 3/10 个用例。")
    assert "权重：用例 60% + AI 40%" in prompt
