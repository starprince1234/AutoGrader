"""`StaticAnalysisResult`：静态合法性检查的结构化结论（含"未检查"显式化）。

字段、不变量（I9–I14）与失败语义见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 3 节。本模块只落地形态：
检查器实现属 `WP-B2-B`，报告侧形态以 `MAIN/contracts/schemas/evaluation-report.schema.json` 为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: 单条明细的严重度。与契约为 `static_findings[].severity` 同一取值集合。
Severity = Literal["error", "warning", "info"]

#: I14：未实际执行的检查必须以 `not_checked` 显式呈现，禁止折叠为"零 finding = 通过"。
CheckStatus = Literal["ran", "not_checked"]


@dataclass(frozen=True, slots=True)
class StaticFinding:
    rule_id: str
    severity: Severity
    file: str
    line: int | None
    column: int | None
    message: str        # ≤1000，规则说明文本；绝不含源码行
    blocking: bool      # 该规则是否属阻断类


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    checker: Literal["python_ast", "bash_syntax", "structural_rules"]
    status: CheckStatus
    reason: str | None  # status == "not_checked" 时必填（脱敏原因）
    findings: int


@dataclass(frozen=True, slots=True)
class StaticAnalysisResult:
    language: Literal["python", "bash"]
    checks: tuple[CheckOutcome, ...]
    findings: tuple[StaticFinding, ...]
    blocking: bool
    error_count: int
    warning_count: int
    info_count: int
    result_digest: str
