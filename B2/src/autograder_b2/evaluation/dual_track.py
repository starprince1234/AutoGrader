"""双轨评分：把"测试用例通过情况"和"AI 逻辑评分"按题目自带的权重合成一个最终分。

## 为什么要这个模块

过去一次评测只有一条评分来源：要么沙箱逐用例判分（确定性），要么 AI 逻辑评分（单一模型
打分）。两种情况各自都"看起来有分数"，但学生拿到的分无法区分"代码真的跑对了"与"模型觉得
写得不错"。这里把两条轨道的结论合并，并**把权重写进题目的评分提示词**，让每道题自己决定
两条轨的分量。

## 权重的声明格式

题目在「AI 评分提示词与细则」（B3 的 `grading_prompt`）里用一行声明权重：

```
权重：用例 60% + AI 40%
```

`parse_declared_weights` 只认这一行的两个百分数，别的文字不影响它；两个数不等 100 时按比例
归一到 100（把余数给 AI 轨，使两者之和恒为 100）；解析不到时用 `DEFAULT_*` 的 60/40。

## 合成规则（写死在这里，报告与前端只读结论）

- 用例轨：`case_ratio = case_score / case_max * 100`（`case_max` 为 0 时该轨不参与）。
- AI 轨：`ai_score` 是 0~100 的连续分。
- 最终分：`case_ratio * case_percent/100 + ai_score * ai_percent/100`，四舍五入到两位小数。
- **AI 不可用时**（模型未配置、调用失败、越界被拒）：`ai_available = False`，用例轨按 100%
  计分并在结论里明说"AI 未参与"——绝不因为评测服务故障把学生判 0 分。
- 通过线沿用 60 分。

金额/分数一律 `Decimal`（由 `str()` 精确构造），避免二进制浮点把"合计相等"变成偶然成立。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: 题目没有声明权重时的默认值。用例轨优先：确定性证据比模型判断更难伪造。
DEFAULT_CASE_PERCENT = 60
DEFAULT_AI_PERCENT = 40

#: 通过线（与既有 AI 逻辑评分的"60 分及以上为通过"一致）。
PASS_MARK = Decimal(60)

#: 两位小数，四舍五入（与 B2 其余金额口径一致）。
_TWO_PLACES = Decimal("0.01")

_CASE_PATTERN = re.compile(r"用例\D{0,6}(\d{1,3})\s*%")
_AI_PATTERN = re.compile(r"AI\D{0,6}(\d{1,3})\s*%", re.IGNORECASE)


def parse_declared_weights(grading_prompt: str | None) -> tuple[int, int]:
    """从评分提示词里读出 ``(用例权重, AI 权重)``，读不到就用默认 60/40。

    只认 "用例 N%" 与 "AI M%" 两个片段：题目的细则正文通常很长，用整段文本做匹配会让一次
    无关的百分比（例如"正确性占 70%"）被误当成权重。
    """
    if not grading_prompt:
        return DEFAULT_CASE_PERCENT, DEFAULT_AI_PERCENT
    case_match = _CASE_PATTERN.search(grading_prompt)
    ai_match = _AI_PATTERN.search(grading_prompt)
    if case_match is None or ai_match is None:
        return DEFAULT_CASE_PERCENT, DEFAULT_AI_PERCENT
    case_percent = int(case_match.group(1))
    ai_percent = int(ai_match.group(1))
    return _normalize(case_percent, ai_percent)


def _normalize(case_percent: int, ai_percent: int) -> tuple[int, int]:
    """把两个权重归一到和为 100；余数给 AI 轨，保证 ``case + ai == 100`` 恒成立。"""
    total = case_percent + ai_percent
    if total <= 0:
        return DEFAULT_CASE_PERCENT, DEFAULT_AI_PERCENT
    normalized_case = int(
        (Decimal(case_percent) * 100 / Decimal(total)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    normalized_case = max(0, min(100, normalized_case))
    return normalized_case, 100 - normalized_case


@dataclass(frozen=True, slots=True)
class DualTrackScore:
    """一次双轨评分的结论：两条轨各自的原始结果 + 合成后的最终分。"""

    case_score: Decimal
    case_max: Decimal
    case_ratio: Decimal
    case_percent: int
    ai_score: Decimal | None
    ai_percent: int
    ai_available: bool
    final_score: Decimal
    is_pass: bool

    def as_dict(self) -> dict[str, object]:
        """结构化形态：给报告/事件/日志共用的 JSON 数（不是字符串）。

        `final_score`/`is_pass` 也在这里，方便调用方一次拿全；报告组装时会把它们提到
        `overall` 上、只把分解字段留在 `dual_track` 里（契约如此）。
        """
        return {
            "case_score": float(self.case_score),
            "case_max": float(self.case_max),
            "case_ratio": float(self.case_ratio),
            "case_percent": self.case_percent,
            "ai_score": None if self.ai_score is None else float(self.ai_score),
            "ai_percent": self.ai_percent,
            "ai_available": self.ai_available,
            "final_score": float(self.final_score),
            "is_pass": self.is_pass,
        }

    def rationale(self) -> str:
        """给学生/教师看的一句话分解：不涉及模型内部推理，只报算式结果。"""
        if not self.ai_available:
            return (
                f"AI 评分未参与本次评测（权重 {self.ai_percent}% 并入用例轨）："
                f"用例得分 {self.case_ratio}%（{self.case_score}/{self.case_max}），"
                f"最终分 {self.final_score}。"
            )
        return (
            f"用例轨 {self.case_ratio}%（{self.case_score}/{self.case_max}）× "
            f"{self.case_percent}% + AI 轨 {self.ai_score} × {self.ai_percent}% = "
            f"{self.final_score}。"
        )


def combine(
    *,
    case_score: Decimal,
    case_max: Decimal,
    ai_score: Decimal | None,
    grading_prompt: str | None = None,
) -> DualTrackScore:
    """把两条轨的分数合成为最终分。

    ``case_max`` 为 0（题目没有任何用例权重，通常是历史遗留题）时用例轨不参与，权重整体归到
    AI 轨——这是唯一能给出有意义分数的合成方式，报告里由 ``case_ratio`` 为 0 与
    ``ai_percent=100`` 体现。
    """
    case_percent, ai_percent = parse_declared_weights(grading_prompt)
    ratio = Decimal(0)
    if case_max > 0:
        ratio = (case_score / case_max * 100).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    else:
        case_percent, ai_percent = 0, 100

    if ai_score is None:
        # AI 不可用：用例轨独占 100%，不让服务故障变成 0 分。
        final = ratio if case_max > 0 else Decimal(0)
        return DualTrackScore(
            case_score=case_score,
            case_max=case_max,
            case_ratio=ratio,
            case_percent=case_percent,
            ai_score=None,
            ai_percent=ai_percent,
            ai_available=False,
            final_score=final.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP),
            is_pass=final >= PASS_MARK,
        )

    weighted = (ratio * Decimal(case_percent) + ai_score * Decimal(ai_percent)) / Decimal(100)
    final = weighted.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return DualTrackScore(
        case_score=case_score,
        case_max=case_max,
        case_ratio=ratio,
        case_percent=case_percent,
        ai_score=ai_score,
        ai_percent=ai_percent,
        ai_available=True,
        final_score=final,
        is_pass=final >= PASS_MARK,
    )


__all__ = [
    "DEFAULT_AI_PERCENT",
    "DEFAULT_CASE_PERCENT",
    "PASS_MARK",
    "DualTrackScore",
    "combine",
    "parse_declared_weights",
]
