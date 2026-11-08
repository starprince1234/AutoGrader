"""学习提示的**响应解析与安全输出闸门**（AI-FLOW-01B）。

模型被要求输出一个 JSON 对象：`{"hints": [{"level", "title", "text"}, ...]}`。解析只认核心结构：

- `hints` 必须是 1–3 个对象的数组（契约 `hint_steps` 的边界）；
- `level` 只能取 `knowledge` / `direction` / `self_check`，且必须**按此顺序严格递增**——
  提示的意义就是"先想方向，再给线索"，乱序返回等于把答案提前；
- `title` / `text` 非空且不超部署预算；
- **未知的额外字段被忽略**（而不是让整次生成失败）：契约明确"安全的额外字段可忽略，不因非核心
  扩展字段导致整次生成失败"。这一点与评语轨的严格形状不同，是有意的。

## 安全输出闸门不是关键词黑名单

`guard_hint_steps` 检查的是**结构性质**，不是"出现某个词就拒绝"：

1. 不允许"完整可提交程序"的形状：围栏代码块里出现多行函数/脚本定义（`def` + `return`、
   `#!/` + 可执行行）→ 整组拒绝。短伪代码（例如 `for x in ...: 累加`）是允许的。
2. 不允许声称本次运行的结论（例如"你这次得了 80 分"）：提示不得成为第二个分数来源。
3. 不允许序列化的隐藏测试/期望输出标记（`expected_output`、`hidden case`、`参考答案` 等）：
   这些字段**从未**进入模型输入，出现即说明输出越界。

关键词只是**辅助线索**，真正的边界是"模型从来看不到这些东西"（`commentary.scope.INPUT_SCOPE`
+ 不读 CAS/runner/package）。因此这里宁可放过一句奇怪的措辞，也不假装关键词能证明安全。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: 允许的提示层级（顺序即"知识点 → 排查方向 → 自检思路"的渐进顺序）。
HINT_LEVELS: tuple[str, ...] = ("knowledge", "direction", "self_check")

#: 与契约 `hint_steps.maxItems` 一致。
MAX_STEPS = 3

#: 单条提示的**绝对**上限（部署预算可以更小，但不能更大）。
ABSOLUTE_TITLE_LIMIT = 200
ABSOLUTE_TEXT_LIMIT = 4000

#: 围栏代码块的判定（``` 或 ~~~）。
_FENCE = re.compile(r"^\s*(```|~~~)", re.MULTILINE)
#: "完整程序"的形状线索（与围栏块内的行数一起判定，单独出现不构成拒绝）。
_CODE_DEFINITION = re.compile(
    r"(^\s*(def|class|function|func|fn)\s+\w+|^\s*#!|^\s*import\s+\w+|^\s*from\s+\w+\s+import)",
    re.MULTILINE,
)
_CODE_BODY = re.compile(
    r"(^\s*(return|print|echo|printf|sys\.stdout|console\.log)\b|^\s*\w+\s*=\s*\w+\()",
    re.MULTILINE,
)
#: 声称本次运行结论的措辞（提示不得成为第二个分数来源）。
_SCORE_CLAIM = re.compile(
    r"(你(这|本)次?得了|最终得分|你的分数是|满分是|score\s*[:=]\s*\d)",
    re.IGNORECASE,
)
#: 序列化的隐藏用例/期望输出/参考解标记（它们从未进入模型输入）。
#: 中文的“参考答案/期望输出”在正常提示里也可能作为提醒语出现（例如“不要直接抄参考答案”），
#: 不作为硬拒绝词；真正的边界是这些内容从未进入输入。英文序列化标记仍保留。
_FORBIDDEN_MARKERS = (
    "expected_output",
    "hidden_case",
    "hidden case",
    "reference_answer",
    "reference answer",
    "expected stdout",
)


class HintResponseError(RuntimeError):
    """模型响应不符合核心结构，或触碰安全输出闸门 → 把这一行标为 `failed`。

    消息只描述**缺陷类别**，不回显模型内容：失败原因会落库并在 GET 中可见，而响应正文可能包含
    题目证据的复述。
    """


class UnsafeHintOutput(HintResponseError):
    """输出可能泄露完整答案/隐藏值，或声称评测结论 → 整组拒绝（`status: failed`）。"""


@dataclass(frozen=True, slots=True)
class HintStep:
    """一条提示（契约 `hint_steps[]` 的形状：`level` / `title` / `text`）。"""

    level: str
    title: str
    text: str

    def as_contract(self) -> dict[str, str]:
        return {"level": self.level, "title": self.title, "text": self.text}


def parse_hint_steps(
    raw: str, *, title_limit: int, text_limit: int
) -> tuple[HintStep, ...]:
    """解析模型响应 → 1–3 条有序提示；任何核心结构缺陷都抛 `HintResponseError`。

    `title_limit` / `text_limit` 来自部署配置（`config.LearningHintsSettings`），并被收窄到
    `ABSOLUTE_*` 上限之内：调用方传一个更大的值不会让输出无界。
    """
    document = _load(raw)
    if not isinstance(document, Mapping):
        raise HintResponseError("模型响应不是 JSON 对象")
    raw_steps = document.get("hints")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise HintResponseError("模型响应缺少非空的 hints 数组")
    if len(raw_steps) > MAX_STEPS:
        raise HintResponseError(f"模型响应返回了超过 {MAX_STEPS} 条提示")

    effective_title_limit = min(max(1, title_limit), ABSOLUTE_TITLE_LIMIT)
    effective_text_limit = min(max(1, text_limit), ABSOLUTE_TEXT_LIMIT)

    steps: list[HintStep] = []
    previous = -1
    for item in raw_steps:
        if not isinstance(item, Mapping):
            raise HintResponseError("hints 的元素必须是 JSON 对象")
        level = item.get("level")
        if not isinstance(level, str) or level not in HINT_LEVELS:
            raise HintResponseError(
                "hints[].level 必须是 knowledge/direction/self_check 之一"
            )
        index = HINT_LEVELS.index(level)
        if index <= previous:
            raise HintResponseError("hints 的层级必须按知识点→排查方向→自检思路的顺序严格递增")
        previous = index
        steps.append(
            HintStep(
                level=level,
                title=_text(item.get("title"), "title", effective_title_limit),
                text=_text(item.get("text"), "text", effective_text_limit),
            )
        )
    guard_hint_steps(steps)
    return tuple(steps)


def guard_hint_steps(steps: tuple[HintStep, ...]) -> None:
    """安全输出闸门：完整程序形状、分数断言、隐藏字段标记 → 整组拒绝。"""
    for step in steps:
        lowered = step.text.lower()
        for marker in _FORBIDDEN_MARKERS:
            if marker in lowered:
                raise UnsafeHintOutput(
                    "提示包含隐藏用例/期望输出/参考答案的标记，整组拒绝"
                )
        if _SCORE_CLAIM.search(step.text):
            raise UnsafeHintOutput("提示声称本次评测的分数或结论，整组拒绝")
        if _is_complete_program(step.text):
            raise UnsafeHintOutput("提示包含可完整提交的程序形状，整组拒绝")


def _is_complete_program(text: str) -> bool:
    """围栏块内同时出现"定义"与"函数体/输出"的多行代码 → 视为完整可实现答案。

    允许短伪代码：例如 2 行 `for i in xs:` + `total += i` 既没有定义也没有输出语句。
    """
    blocks = _fenced_blocks(text)
    for block in blocks:
        non_empty = [line for line in block.splitlines() if line.strip()]
        if len(non_empty) < 3:
            continue
        if _CODE_DEFINITION.search(block) and _CODE_BODY.search(block):
            return True
    return False


def _fenced_blocks(text: str) -> list[str]:
    """按围栏行切出不完整也照常处理的代码块内容（不依赖闭合围栏）。"""
    lines = text.splitlines()
    blocks: list[str] = []
    current: list[str] | None = None
    for line in lines:
        if _FENCE.match(line):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _load(raw: str) -> Any:
    text = raw.strip()
    # 有些 OpenAI 兼容模型即使被要求“不要 Markdown”，仍会把 JSON 包在 ```json 围栏里。
    # 这里只剥掉最外层围栏/前后解释，再交给严格 JSON 解析；结构仍由下面的字段校验兜底。
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise HintResponseError("模型响应不是合法 JSON") from error


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise HintResponseError(f"模型响应缺少字符串字段 {field}")
    normalized = normalize_text(value)
    if not normalized:
        raise HintResponseError(f"模型响应的 {field} 为空")
    if len(normalized) > limit:
        raise HintResponseError(
            f"模型响应的 {field} 超过部署上限 {limit} 字符（不截断，判为失败）"
        )
    return normalized


def normalize_text(text: str) -> str:
    """去掉控制字符（保留换行/制表符）并修剪首尾空白；不改变语句内容。"""
    cleaned = "".join(ch if (ch.isprintable() or ch in "\n\t") else " " for ch in text)
    return cleaned.strip()


__all__ = [
    "ABSOLUTE_TEXT_LIMIT",
    "ABSOLUTE_TITLE_LIMIT",
    "HINT_LEVELS",
    "MAX_STEPS",
    "HintResponseError",
    "HintStep",
    "UnsafeHintOutput",
    "guard_hint_steps",
    "normalize_text",
    "parse_hint_steps",
]
