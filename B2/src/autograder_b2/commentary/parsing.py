"""评语生成的**响应解析**：模型的一个 JSON 对象 → (`text`, 可选的 `advisory_score` 建议)。

## 为什么解析要单独成一步，而不是"取不到 JSON 就把整段当评语"

`CHG-0018` §5 把这条写死了：生成从"输出一段文字"变成"输出一个 JSON 对象"，**解析失败归为
`status: failed`，不得退化成把整段响应当评语**。理由不是洁癖：那种退化会把一次**格式错误**
变成一次**静默降级**——读者拿到的是一段看起来正常的评语，而它其实是破坏了协议的响应；
更糟的是同一份响应里本应存在的 `advisory_score` 会无声消失，而"这次没有参考分"与
"模型没按协议回答"在库里长得一模一样。

因此本模块只有两种结局：解析出契约允许的形状，或者抛 `CommentaryResponseError`
（由服务层落成 `failed` + 一句**不含模型内容**的原因）。

## 不接受的东西（每一条都有对应的失败）

| 输入 | 结局 | 原因 |
|---|---|---|
| 非 JSON / 非对象 / `NaN`、`Infinity` | 失败 | 不是契约允许的载荷；`NaN` 还会让区间比较全部为假——**必须挡在比较之前** |
| 缺 `commentary`、或它不是字符串、或归一后为空 | 失败 | 没有评语就没有这次生成 |
| `commentary` 超过 8000 字符 | 失败 | 契约 `maxLength`；**截断**同样是把"模型说了什么"改成"我们允许它说什么" |
| 顶层出现 `commentary` / `advisory_score` 之外的键 | 失败 | **包括模型自带的 `max_score`**：满分由 B2 从证据填入（`ADR-0014` 决策 2），实现里不存在读模型满分的代码路径，多给的键必须让它失败而不是被忽略 |
| `advisory_score` 既不是对象也不是 null | 失败 | 形状非法 |
| `advisory_score` 缺任一键、多任一键 | 失败 | 同上 |
| `score` 不是数字（含布尔）、非有限 | 失败 | `true` 在 Python 里是 `int`，不显式挡住就会被读成 1 分 |
| `rationale` 空或超过 2000 字符 | 失败 | 契约长度 |
| `confidence` 不在 `low`/`medium`/`high` | 失败 | 契约枚举 |

**区间判定（`0 <= score <= max_score`）不在这里**：它需要证据里的满分，属于服务层的
`AdvisoryBoundary`（见 `commentary/service.py`）。本模块只回答"模型说了什么"。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from autograder_b2.commentary.prompt import (
    ADVISORY_KEY,
    COMMENTARY_KEY,
    TEXT_LIMIT,
)

#: 契约 `AdvisoryScore.confidence` 的枚举（与迁移 0014 的 CHECK 同值）。
CONFIDENCES: tuple[str, ...] = ("low", "medium", "high")

#: 契约 `AdvisoryScore.rationale.maxLength`。
RATIONALE_LIMIT = 2000

#: 顶层与参考分对象各自**必须恰好**是这些键。
_TOP_LEVEL_KEYS = frozenset({COMMENTARY_KEY, ADVISORY_KEY})
_ADVISORY_KEYS = frozenset({"score", "rationale", "confidence"})


class CommentaryResponseError(RuntimeError):
    """模型响应不符合约定的 JSON 形状（`CHG-0018` §5：整次生成判为 `failed`）。

    消息只描述**形状缺陷**（哪个键缺失/类型不对/超长），**不回显任何模型内容**：
    失败原因会落库并出现在 `503` 的 detail 里，而响应正文可能包含证据的复述。
    """


@dataclass(frozen=True, slots=True)
class AdvisoryProposal:
    """模型给出的参考分建议（**不含 `max_score`**：那是系统填入的事实）。"""

    score: Decimal
    rationale: str
    confidence: str


@dataclass(frozen=True, slots=True)
class Generation:
    """一次成功解析的生成结果：评语正文 + 可空的参考分建议。"""

    text: str
    advisory: AdvisoryProposal | None


def parse_generation(raw: str) -> Generation:
    """把模型响应解析成 `Generation`；任何形状缺陷都抛 `CommentaryResponseError`。"""
    document = _load(raw)
    if not isinstance(document, Mapping):
        raise CommentaryResponseError("模型响应不是 JSON 对象")
    unknown = set(document) - _TOP_LEVEL_KEYS
    if unknown:
        # 不回显键名：那是模型内容，而失败原因会进库与 HTTP detail。
        raise CommentaryResponseError("模型响应含未声明的顶层字段（只允许 commentary 与 advisory_score）")

    text = _text(document.get(COMMENTARY_KEY))
    if ADVISORY_KEY not in document:
        return Generation(text=text, advisory=None)
    return Generation(text=text, advisory=_advisory(document[ADVISORY_KEY]))


def normalize_text(text: str) -> str:
    """去掉控制字符（保留换行与制表符）并修剪首尾空白。

    评语与依据都是给人看的文本：控制字符没有语义，只会在客户端里造成显示事故。这是**显示安全**
    层面的归一，不改变模型表达的语句内容（也因此不影响"模型说了什么"的可追溯性）。
    """
    cleaned = "".join(ch if (ch.isprintable() or ch in "\n\t") else " " for ch in text)
    return cleaned.strip()


def _load(raw: str) -> Any:
    try:
        # `parse_float=Decimal`：JSON 里的数字直接进位精确十进制，避免 float 的二进制表示
        # 混进与证据比较的路径（满分也是 Decimal）。
        # `parse_constant` 拒绝 NaN/Infinity：它们会让区间比较全部为假（NaN < 0 是 False），
        # 从而绕过越界判定——必须在解析阶段就挡住。
        return json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, InvalidOperation) as error:
        raise CommentaryResponseError("模型响应不是合法 JSON") from error


def _reject_constant(name: str) -> Any:
    raise ValueError(f"model returned the non-finite JSON constant {name}")


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise CommentaryResponseError("模型响应缺少字符串字段 commentary")
    normalized = normalize_text(value)
    if not normalized:
        raise CommentaryResponseError("模型响应的 commentary 为空")
    if len(normalized) > TEXT_LIMIT:
        raise CommentaryResponseError(
            f"模型响应的 commentary 超过契约上限 {TEXT_LIMIT} 字符（不截断，判为失败）"
        )
    return normalized


def _advisory(value: Any) -> AdvisoryProposal | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CommentaryResponseError("模型响应的 advisory_score 不是对象也不是 null")
    unknown = set(value) - _ADVISORY_KEYS
    if unknown:
        raise CommentaryResponseError(
            "模型响应的 advisory_score 含未声明的字段（只允许 score、rationale、confidence）"
        )
    missing = sorted(_ADVISORY_KEYS - set(value))
    if missing:
        raise CommentaryResponseError("模型响应的 advisory_score 缺少字段：" + "、".join(missing))
    return AdvisoryProposal(
        score=_score(value["score"]),
        rationale=_rationale(value["rationale"]),
        confidence=_confidence(value["confidence"]),
    )


def _score(value: Any) -> Decimal:
    # `bool` 是 `int` 的子类：不显式挡住，`"score": true` 会被读成 1 分。
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise CommentaryResponseError("模型响应的 advisory_score.score 不是数字")
    score = Decimal(value)
    if not score.is_finite():
        raise CommentaryResponseError("模型响应的 advisory_score.score 不是有限数")
    return score


def _rationale(value: Any) -> str:
    if not isinstance(value, str):
        raise CommentaryResponseError("模型响应的 advisory_score.rationale 不是字符串")
    normalized = normalize_text(value)
    if not normalized:
        raise CommentaryResponseError("模型响应的 advisory_score.rationale 为空")
    if len(normalized) > RATIONALE_LIMIT:
        raise CommentaryResponseError(
            f"模型响应的 advisory_score.rationale 超过契约上限 {RATIONALE_LIMIT} 字符"
        )
    return normalized


def _confidence(value: Any) -> str:
    if not isinstance(value, str) or value not in CONFIDENCES:
        raise CommentaryResponseError(
            "模型响应的 advisory_score.confidence 不在允许取值内（low/medium/high）"
        )
    return value


__all__ = [
    "CONFIDENCES",
    "RATIONALE_LIMIT",
    "AdvisoryProposal",
    "CommentaryResponseError",
    "Generation",
    "normalize_text",
    "parse_generation",
]
