"""安全判定与阻断门（`RQ-B-2-70`）：静态高危模式识别 → 三态结论 → 阻断门 → 落库。

模块分工：

- `patterns`：规则集（ID + `category` + `severity` + 中文说明 + 三个匹配面）与三个检查器；
- `decision`：三态推导、不变量自检（I15–I19）、阻断门 `allows_dynamic_execution` / `terminal_reason`；
- `findings`：`b2.security_findings` 的纯行构造与唯一写库入口。

**只使用、不修改** `autograder_b2.domain.security` 的冻结类型（`SecurityFinding` /
`SecurityDecision` / `SecurityStatus` / `SecurityCategory` / `SecuritySeverity` / `DetectedIn`）：
它们是 `autograder_b2.domain` 单点入口的对外身份，本包**不**再导出一遍（避免同一类型出现第二条
import 路径）。
"""

from __future__ import annotations

from autograder_b2.security.decision import (
    DEFAULT_CHECKS,
    REQUIRED_CHECKS,
    RUNTIME_SANDBOX_CHECK_ID,
    STATIC_LAYER_CHECK_ID,
    Analyzer,
    CheckReport,
    StaticCheck,
    allows_dynamic_execution,
    assert_decision_invariants,
    check_language,
    classify_static_message,
    conclude,
    decide_static,
    mark_check_unchecked,
    record_runtime_findings,
    required_checks,
    run_checks,
    static_layer_finding,
    terminal_reason,
)
from autograder_b2.security.findings import (
    SECURITY_FINDING_COLUMNS,
    build_security_finding_row,
    build_security_finding_rows,
    record_security_findings,
)
from autograder_b2.security.patterns import (
    AST_PROBES,
    BASH_TEXT_CHECK_ID,
    HUGE_INT_THRESHOLD,
    MESSAGE_LIMIT,
    PYTHON_AST_CHECK_ID,
    PYTHON_TEXT_CHECK_ID,
    RULES,
    RULES_BY_ID,
    CheckUnavailableError,
    Rule,
    analyze_bash_text,
    analyze_python_ast,
    analyze_python_text,
    python_code_text,
    redact_text,
    rule_table_problems,
    safe_location,
    strip_bash_comments,
)

__all__ = [
    "AST_PROBES",
    "BASH_TEXT_CHECK_ID",
    "DEFAULT_CHECKS",
    "HUGE_INT_THRESHOLD",
    "MESSAGE_LIMIT",
    "PYTHON_AST_CHECK_ID",
    "PYTHON_TEXT_CHECK_ID",
    "REQUIRED_CHECKS",
    "RULES",
    "RULES_BY_ID",
    "RUNTIME_SANDBOX_CHECK_ID",
    "SECURITY_FINDING_COLUMNS",
    "STATIC_LAYER_CHECK_ID",
    "Analyzer",
    "CheckReport",
    "CheckUnavailableError",
    "Rule",
    "StaticCheck",
    "allows_dynamic_execution",
    "analyze_bash_text",
    "analyze_python_ast",
    "analyze_python_text",
    "assert_decision_invariants",
    "build_security_finding_row",
    "build_security_finding_rows",
    "check_language",
    "classify_static_message",
    "conclude",
    "decide_static",
    "mark_check_unchecked",
    "python_code_text",
    "record_runtime_findings",
    "record_security_findings",
    "redact_text",
    "required_checks",
    "rule_table_problems",
    "run_checks",
    "safe_location",
    "static_layer_finding",
    "strip_bash_comments",
    "terminal_reason",
]
