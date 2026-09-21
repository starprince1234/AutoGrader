"""静态合法性检查（`RQ-B-2-40`）：规则目录、Python/Bash 检查器与总评聚合。

面向调用方的入口只有一个：`analyze_static(workspace=..., files=..., language=...)`，产出冻结类型
`autograder_b2.domain.StaticAnalysisResult`（字段与不变量见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 3 节，已冻结、不在此重定义）。

诚实原则（I14）：未实际执行的检查一律以 `not_checked` + `reason` 呈现——"未检查"永远不等于"没问题"。
"""

from __future__ import annotations

from autograder_b2.static.bash_checker import (
    BashSyntaxResult,
    BashTarget,
    bash_structural_findings,
    check_bash_syntax,
    probe_bash,
)
from autograder_b2.static.checker import analyze_static
from autograder_b2.static.python_checker import (
    parse_python,
    python_structural_findings,
    python_syntax_findings,
)
from autograder_b2.static.rules import (
    MESSAGE_LIMIT,
    RULES,
    Checker,
    Language,
    Rule,
    get_rule,
    make_finding,
    rule_catalog_digest,
    rules_for,
    sanitize_detail,
)

__all__ = [
    "MESSAGE_LIMIT",
    "RULES",
    "BashSyntaxResult",
    "BashTarget",
    "Checker",
    "Language",
    "Rule",
    "analyze_static",
    "bash_structural_findings",
    "check_bash_syntax",
    "get_rule",
    "make_finding",
    "parse_python",
    "probe_bash",
    "python_structural_findings",
    "python_syntax_findings",
    "rule_catalog_digest",
    "rules_for",
    "sanitize_detail",
]
