"""参考解答验证轨（`RQ-T-10` 的可用性补全 / `CHG-0019` + `CHG-0023` / `ADR-0015`）。

| 模块 | 职责 |
|---|---|
| `b3` | B3 服务通道上的两个调用：取参考解答（`CHG-0023`）与记录存证（`CHG-0008`） |
| `workspace` | 把受保护正文物化成**一次性**工作区（逐用例一份 / 一次批量一份），用完即删 |
| `verification` | 编排：取参考解答 → 取评测包 → 按 `execution.mode` 分派（逐用例 / 一次批量 / 合成邮件回执规则检查）→ 只在全通过时请 B3 记录存证 |

三条不可协商的性质（详见 `verification` 的模块 docstring）：

1. **执行只走 runner 的学生同一入口**：`per_case` 用 `runner.run_case`、`single_run`/`ssh_session`
   用 `runner.run_cases`（生产装配是 `RunnerCaseRunner`）：`runner/policy.py` 的 seccomp / cgroup /
   禁网策略照旧生效。另起一条执行路径等于在发布门禁上开一个不受沙箱约束的面。
2. **不落库**：本包不碰存储引擎、不写任何表、不发任何事件。存证是 B3 的记录，B2 不存第二份。
3. **不返回受保护内容**：参考解答正文、隐藏用例输入与期望输出都不进响应（粒度同报告的
   `view=teacher`，不是更多）。参考解答正文在 `ReferenceAnswerView` 里是 `repr=False`。
   `email_delivery` 的参考答案是**受保护的合成邮件回执 JSON**，与真实 IMAP 收件事实无关；
   本包不产生"真实邮件已验证"的声明。

按角色而不是按课程判定：`docs/api/contract.md` 记着这条已知缺口（"题目属于哪门课"是 B3 的事实，
B2 不读它的 schema、也不复制一份映射）。
"""

from __future__ import annotations

from autograder_b2.reference.b3 import (
    ADAPTER_KINDS,
    ATTESTATIONS_PATH,
    LANGUAGES,
    REFERENCE_ANSWER_PATH,
    VERIFIER_LABEL,
    B3Transport,
    B3VerificationClient,
    ReferenceAnswerMissing,
    ReferenceAnswerNotFound,
    ReferenceAnswerUnavailable,
    ReferenceAnswerView,
    UrllibB3Transport,
)
from autograder_b2.reference.verification import (
    FAILURE_CATEGORIES,
    ReferenceSandboxUnavailable,
    ReferenceVerificationDefect,
    ReferenceVerificationNotFound,
    ReferenceVerificationNotPossible,
    ReferenceVerificationResult,
    ReferenceVerificationService,
    ReferenceVerificationUnavailable,
    VerificationCase,
)
from autograder_b2.reference.workspace import ReferenceAnswerWorkspace

__all__ = [
    "ADAPTER_KINDS",
    "ATTESTATIONS_PATH",
    "FAILURE_CATEGORIES",
    "LANGUAGES",
    "REFERENCE_ANSWER_PATH",
    "VERIFIER_LABEL",
    "B3Transport",
    "B3VerificationClient",
    "ReferenceAnswerMissing",
    "ReferenceAnswerNotFound",
    "ReferenceAnswerUnavailable",
    "ReferenceAnswerView",
    "ReferenceAnswerWorkspace",
    "ReferenceSandboxUnavailable",
    "ReferenceVerificationDefect",
    "ReferenceVerificationNotFound",
    "ReferenceVerificationNotPossible",
    "ReferenceVerificationResult",
    "ReferenceVerificationService",
    "ReferenceVerificationUnavailable",
    "UrllibB3Transport",
    "VerificationCase",
]
