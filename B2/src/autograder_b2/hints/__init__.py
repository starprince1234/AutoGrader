"""学生分层学习提示（AI-FLOW-01B）：仅 run owner 学生可读写，只依赖已落库的授权证据。

本包与 `commentary` 包**刻意分开**：

| | 教学评语（`commentary`） | 学生分层提示（`hints`） |
|---|---|---|
| 读者 | 教师/助教（学生可见性由课程策略决定） | 只有 run owner 学生 |
| 生成者 | 教师/助教 | 学生本人 |
| 存储 | `b2.evaluation_commentaries`（可覆盖） | `b2.evaluation_learning_hints`（一 run 一行，历史保留） |
| 输出 | 一段文本 + 可空 `advisory_score` | 1–3 条有序提示（无分数、无证据、无参考分） |

两条轨都复用**同一个** B2 provider 配置（不新增模型供应商、不新增 SDK），也都不进入分数轨：
分数、逐项证据、`result_digest`、成绩导出与任何事件都不含提示内容。

输入白名单沿用 `commentary.scope.INPUT_SCOPE`（成功完成的运行的结构化证据），并可**额外**读取
B3 的公开题目上下文（题面/语言/标签/adapter kind），但仅当部署配置了
`B2_LEARNING_CONTEXT_PATH` 时；未配置就只使用评测证据，`context_scope` 如实反映读了什么。
"""

from autograder_b2.hints.parsing import (
    HINT_LEVELS,
    HintResponseError,
    HintStep,
    UnsafeHintOutput,
    parse_hint_steps,
)
from autograder_b2.hints.prompt import HINTS_DISCLAIMERS, HINTS_PROMPT_VERSION
from autograder_b2.hints.service import (
    HintsBudgetExceeded,
    HintsInfrastructureFailure,
    HintsInProgress,
    HintsNotApplicable,
    HintsNotConfigured,
    HintsOutcome,
    HintsRejectedRun,
    HintsRunNotCompleted,
    HintsService,
)
from autograder_b2.hints.store import (
    HINT_STATUSES,
    LearningHintsRecord,
    load_hints,
    not_applicable_body,
    not_requested_body,
)
from autograder_b2.hints.store import (
    unavailable_body as hints_unavailable_body,
)

__all__ = [
    "HINTS_DISCLAIMERS",
    "HINTS_PROMPT_VERSION",
    "HINT_LEVELS",
    "HINT_STATUSES",
    "HintResponseError",
    "HintStep",
    "HintsBudgetExceeded",
    "HintsInProgress",
    "HintsInfrastructureFailure",
    "HintsNotApplicable",
    "HintsNotConfigured",
    "HintsOutcome",
    "HintsRejectedRun",
    "HintsRunNotCompleted",
    "HintsService",
    "LearningHintsRecord",
    "UnsafeHintOutput",
    "hints_unavailable_body",
    "load_hints",
    "not_applicable_body",
    "not_requested_body",
    "parse_hint_steps",
]
