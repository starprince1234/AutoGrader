"""评语轨的 prompt 模板与免责标注（`RQ-T-20` 验收 3/4 / `RQ-T-30` 验收 7）。

prompt 与免责文本都带**版本号**：provider/model 换了要看得出来，prompt 换了同样要看得出来
（`CommentaryProvenance.prompt_version`）。模板只做一件事——把白名单载荷序列化进去，
并在文字上再钉一次"只准引用给定字段"。**不做**任何"顺手把题目信息补进去"的动作：
那会绕开 `scope.INPUT_SCOPE` 这个安全边界。

## 为什么版本从 `b2-commentary/1` 推进到 `/2`（`CHG-0018` §5）

生成从"输出一段文字"变成"输出**一个 JSON 对象**"（`text` + 可空的 `advisory_score`）。
这是同一份证据上的**协议变更**：老版本模型的自由文本在新解析器下会被判为
"响应不是合法 JSON" ⇒ `status: failed`。因此历史行上的 `prompt_version` 必须能区分
"这句话是在哪种协议下产生的"，否则 1 版的行看起来像是 2 版解析器的失败，而它当时是成功的。

## `advisory_score` 的三条纪律（写进 prompt，也写在解析器与表约束里）

1. **不要产出 `max_score`**。满分由系统按 `case_evidence` 的 `weight` 之和填入——它是可复算的
   事实，不是判断（`ADR-0014` 决策 2）。模型多给一个键会让整次生成失败，而不是被忽略。
2. **不要为了迁就边界而改数**。`score` 为负或大于满分会让整次生成**失败**，而不是被截断
   （`ADR-0014` 决策 6）：截断会把"模型说了什么"悄悄改成"我们允许它说什么"。
3. **证据不足以判断时给 `null`**，不要编一个数。契约明确允许 `advisory_score` 为 null。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: prompt 模板版本（进 provenance；改模板必须改它）。
#: `/1` = 纯文本评语；`/2` = JSON 对象（`text` + `advisory_score`，`CHG-0018`）。
PROMPT_VERSION = "b2-commentary/2"

#: 每条评语都要原样展示的规范性标注（契约 `disclaimers`：minItems 1、每条 ≤200 字符）。
#:
#: 第一句是 `ADR-0009` 决策 2 要求的"显式标注"；第二句说明它与分数轨的关系——把它写清楚是必要的：
#: 讲不清这件事，评语就会被当成判分依据，而 ADR-0005 否决"LLM 评语"的理由正是这个。
#: 第三句是 `ADR-0014` 决策 5 要求的**分数参考**标注：`advisory_score` 为 null 时它**仍然存在**
#: （契约原话："A client that renders `advisory_score` without them is presenting a model's
#: suggestion as a grade."）——因此它不能挂在参考分对象上，只能挂在这一组恒定标注里。
DISCLAIMERS: tuple[str, ...] = (
    "本内容由模型生成，是非确定性的，不参与判分，也不作为申诉依据。",
    "分数与逐项证据与本评语无关：删除或重新生成本评语不会改变任何分数。",
    "参考分是模型的顾问性建议，既不是成绩也不是评分依据；满分由系统按证据填入，不由模型生成。",
)

#: 模型输出的 `commentary` 字符上限（契约 `EvaluationCommentary.text.maxLength`）。
TEXT_LIMIT = 8000

#: 模型输出的 JSON 顶层键（**一个不多一个不少**：多给的键会让整次生成失败）。
COMMENTARY_KEY = "commentary"
ADVISORY_KEY = "advisory_score"

_SYSTEM_PROMPT = """你是一名课程助教。请根据评测系统已经落库的结构化证据，写一段面向学生的中文评语，\
并在证据支持的时候另给一个**顾问性**的分数建议。

硬性要求：
1. 只能引用下面 JSON 里出现的字段。不要推测未给出的信息，尤其不要猜测学生的代码长什么样。
2. 只输出**一个 JSON 对象**，不要输出任何解释、前后缀或 Markdown 代码围栏。对象的键**只能有**\
下面这两个：
   - "commentary"：字符串，200-400 字的中文段落，不要使用 Markdown 标题或列表符号。
   - "advisory_score"：对象或 null。
3. "advisory_score" 对象里**只能有**这三个键：
   - "score"：数字。你的建议分。
   - "rationale"：字符串，说明这个建议基于证据里的什么（不超过 2000 字）。
   - "confidence"：字符串，只能是 "low"、"medium"、"high" 之一。
4. **不要给出满分**。满分等于 case_evidence 里各用例 weight 之和，由系统填入，不是你的事；\
多给一个键会让本次生成直接失败。
5. "score" 必须落在 0 到满分之间（含两端）。越界会让本次生成**直接失败**，\
不要为了迁就边界而修改数值，也不要截断或四舍五入到边界。
6. 证据不足以判断时，把 "advisory_score" 设为 null —— 不要编一个数。证据里没有失败用例时，\
不要声称学生某处做错了。
7. 不要直接给出题目的完整答案；指出问题所在与排查方向即可。不确定就说不确定。

字段含义：case_evidence 是逐用例结论（passed/failure_category/weight/awarded），
static_findings 是静态规则命中，security_conclusion.status 是安全结论三态
（found=发现违规 / not_found=未发现 / not_checked=未执行该项检查，三者含义不同，不要混为一谈），
score 是分数构成（没有分数行时为 null，表示这次运行没有产生分数）。

输出示例（只是形状，不要照抄内容）：
{"commentary": "……", "advisory_score": {"score": 4, "rationale": "……", "confidence": "medium"}}"""


def build_prompt(evidence: Mapping[str, Any]) -> str:
    """把白名单载荷渲染成 prompt（同载荷 ⇒ 同 prompt ⇒ 可复核、可回归）。"""
    payload = json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True, indent=2)
    return f"{_SYSTEM_PROMPT}\n\n结构化证据：\n{payload}\n"


__all__ = [
    "ADVISORY_KEY",
    "COMMENTARY_KEY",
    "DISCLAIMERS",
    "PROMPT_VERSION",
    "TEXT_LIMIT",
    "build_prompt",
]
