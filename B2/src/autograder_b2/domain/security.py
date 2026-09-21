"""`SecurityDecision`：安全结论的**三态**模型（本接口的全部意义）。

不变量（I15–I19）与失败语义见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 4 节。核心不可协商：

- `not_found` ⟺ `unchecked` 为空（所有必需检查都真的跑过）；
- `not_checked` 在任何输出中都**禁止**表述为"安全/通过"；
- `blocking` 只由"已执行检查发现 high"决定，且阻断必须发生在 runner 之前。

报告侧形态以 `MAIN/contracts/schemas/evaluation-report.schema.json` 的 `security_conclusion` 为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SecurityStatus = Literal["found", "not_found", "not_checked"]
SecurityCategory = Literal[
    "destructive_command",
    "network_attempt",
    "privilege_escalation",
    "sensitive_path_read",
    "fork_bomb",
    "resource_limit",
    "archive_bomb",
    "sandbox_violation",
]
SecuritySeverity = Literal["high", "medium", "low"]
DetectedIn = Literal["static", "runtime"]


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    finding_id: str
    category: SecurityCategory
    severity: SecuritySeverity
    detected_in: DetectedIn
    message: str              # ≤1000，脱敏
    rule_id: str | None       # detected_in == "static" 时指向静态规则


@dataclass(frozen=True, slots=True)
class SecurityDecision:
    status: SecurityStatus
    findings: tuple[SecurityFinding, ...]
    blocking: bool            # 是否阻断动态评测
    covered: tuple[str, ...]  # 实际执行过的检查项标识
    unchecked: tuple[str, ...]  # 未执行的检查项标识（不可用/未到达）
    rationale: str            # 脱敏结论文本
