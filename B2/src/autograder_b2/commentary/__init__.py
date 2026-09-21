"""教学评语轨（`RQ-T-20` / `ADR-0009`）与 AI 评分参考（`RQ-T-30` / `ADR-0014`）：
**非确定性、独立于分数轨**的一条支线。

| 模块 | 职责 |
|---|---|
| `scope` | 输入**白名单**（安全边界）：字段级 SELECT + `INPUT_SCOPE` + 取证载荷（含满分求和） |
| `prompt` | prompt 模板与免责标注（都带版本号；只序列化白名单载荷） |
| `provider` | provider 配置的调用形态：标准库 HTTP、OpenAI 兼容、**默认关闭** |
| `parsing` | 响应的 JSON 形状判定（形状不符 ⇒ 整次生成 `failed`，**不退化成纯文本评语**） |
| `store` | `b2.evaluation_commentaries` 的读写（自己的表；删除它不影响可重放性） |
| `service` | 把"证据 → prompt → provider → 解析 → 满分填入/越界判定 → 落库"串起来 |

四条硬边界（详见各模块 docstring）：

1. 生成只读 `b2` 表里**字段级白名单**的内容，绝不读源码/参考答案/隐藏用例/期望输出；
   参考分与评语**共用同一份白名单**（`ADR-0014` 决策 4：不因为它是分数而放宽）；
2. 评语与参考分不进分数、`evaluation_results`、`case_evidence`、`result_digest`、五项绑定摘要
   或成绩导出；
3. 未配置 provider 时没有任何出网调用点（不是"调用了但忽略"——provider 对象根本不存在）；
4. `AdvisoryScore.max_score` 由证据填入，**不由模型生成**；`score` 越界即整次失败，不截断。
"""

from __future__ import annotations

from autograder_b2.commentary.parsing import (
    CONFIDENCES,
    RATIONALE_LIMIT,
    AdvisoryProposal,
    CommentaryResponseError,
    Generation,
    parse_generation,
)
from autograder_b2.commentary.prompt import DISCLAIMERS, PROMPT_VERSION, TEXT_LIMIT, build_prompt
from autograder_b2.commentary.provider import (
    CommentaryProvider,
    CommentaryProviderConfig,
    CommentaryProviderError,
    CommentaryTransport,
    HttpCommentaryProvider,
    UrllibTransport,
    build_provider,
)
from autograder_b2.commentary.scope import (
    FORBIDDEN_EVIDENCE_MODULES,
    INPUT_SCOPE,
    INPUT_SCOPE_PROJECTION,
    CommentaryEvidence,
    CommentaryEvidenceMissing,
    collect_evidence,
    evidence_paths,
)
from autograder_b2.commentary.service import (
    TERMINAL_RUN_STATES,
    AdvisoryScoreRejected,
    CommentaryOutcome,
    CommentaryService,
    commentary_settings_reason,
    resolve_advisory,
)
from autograder_b2.commentary.store import (
    COMMENTARY_STATUSES,
    AdvisoryScore,
    CommentaryNotConfigured,
    CommentaryRecord,
    CommentaryRunNotTerminal,
    load_commentary,
    unavailable_body,
)

__all__ = [
    "COMMENTARY_STATUSES",
    "CONFIDENCES",
    "DISCLAIMERS",
    "FORBIDDEN_EVIDENCE_MODULES",
    "INPUT_SCOPE",
    "INPUT_SCOPE_PROJECTION",
    "PROMPT_VERSION",
    "RATIONALE_LIMIT",
    "TERMINAL_RUN_STATES",
    "TEXT_LIMIT",
    "AdvisoryProposal",
    "AdvisoryScore",
    "AdvisoryScoreRejected",
    "CommentaryEvidence",
    "CommentaryEvidenceMissing",
    "CommentaryNotConfigured",
    "CommentaryOutcome",
    "CommentaryProvider",
    "CommentaryProviderConfig",
    "CommentaryProviderError",
    "CommentaryRecord",
    "CommentaryResponseError",
    "CommentaryRunNotTerminal",
    "CommentaryService",
    "CommentaryTransport",
    "Generation",
    "HttpCommentaryProvider",
    "UrllibTransport",
    "build_prompt",
    "build_provider",
    "collect_evidence",
    "commentary_settings_reason",
    "evidence_paths",
    "load_commentary",
    "parse_generation",
    "resolve_advisory",
    "unavailable_body",
]
