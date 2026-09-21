"""Python 静态检查：`ast` **只解析不执行**（I9，ADR-0001 第 1 条）。

- 语法检查：`ast.parse(源码字节)`。`ast` 不 import、不 `exec`、不 `eval` 待测模块，因此检查过程
  不可能产生副作用；传字节让源码里的 PEP 263 编码声明自然生效，也避免"先解码再解析"这一步失败。
- 结构检查：只遍历语法树做规则匹配，不解释字符串拼接后的语义（ADR-0001 遗留风险，动态阶段兜底）。

本模块**不**做 IO、不启动进程、不 import 待测模块——所有输入都是调用方给的文件名、字节与语法树。
"""

from __future__ import annotations

import ast

from autograder_b2.domain.static_result import StaticFinding
from autograder_b2.static.rules import make_finding

#: 顶层允许出现、不视为"可执行语句"的节点（定义、导入、赋值与 pass）。
_ALLOWED_TOP_LEVEL = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Pass,
)

#: `subprocess` 中真正执行命令的入口（用于识别"交给 shell 解释执行"）。
_SUBPROCESS_ENTRIES = frozenset({"run", "call", "check_call", "check_output", "Popen"})

#: `os` 模块中天然经由 shell 的解释入口。
_SHELL_ENTRIES = frozenset({"os.system", "os.popen"})

#: 动态执行入口（结构化告警，不是阻断类）。
_DYNAMIC_EXEC_NAMES = frozenset({"eval", "exec", "builtins.eval", "builtins.exec"})

#: 敏感凭据文件路径（阻断类规则的匹配字面量）。
_SENSITIVE_PATHS = ("/etc/shadow", "/etc/gshadow", "/etc/sudoers")


def parse_python(filename: str, raw: bytes) -> tuple[ast.Module | None, tuple[StaticFinding, ...]]:
    """解析一个 Python 源文件：返回 `(语法树 | None, 语法错误明细)`。

    语法错误是**发现**而不是异常（interfaces 第 3.4 节），因此这里不抛异常，只返回明细。
    `ast` 不给行号时（极少数编码类错误）按第 1 行呈现，保证 I10"语法类错误必须带 line"。
    """
    try:
        return ast.parse(raw, filename=filename), ()
    except SyntaxError as exc:
        return None, (_syntax_finding(filename, raw, exc),)
    except ValueError as exc:
        # 源码含 NUL 等无法解析的输入：同样按语法类发现处理，不中断评测。
        return None, (_unparsable_finding(filename, raw, exc),)


def python_syntax_findings(filename: str, raw: bytes) -> tuple[StaticFinding, ...]:
    """只做语法检查的便捷入口（供测试与单文件诊断使用）。"""
    return parse_python(filename, raw)[1]


def python_structural_findings(
    filename: str,
    source: str,
    tree: ast.Module,
) -> tuple[StaticFinding, ...]:
    """按规则目录做结构化检查，返回逐条明细（含规则 ID/severity/文件/行/列/说明）。

    `source` 只用于 I12 的源码泄漏自检，不参与规则匹配（匹配只看语法树）。
    """
    findings: list[StaticFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(
                make_finding(
                    "STAT-PY-010",
                    file=filename,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    detail="该 except 未声明异常类型",
                    source=source,
                )
            )
        elif isinstance(node, ast.Call):
            findings.extend(_call_findings(filename, source, node))
        elif isinstance(node, ast.While) and _is_truthy_literal_loop(node):
            if not any(isinstance(inner, ast.Break) for inner in ast.walk(node)):
                findings.append(
                    make_finding(
                        "STAT-PY-012",
                        file=filename,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        detail="常量真值条件且循环体内无 break",
                        source=source,
                    )
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            hit = next((path for path in _SENSITIVE_PATHS if path in node.value), None)
            if hit is not None:
                findings.append(
                    make_finding(
                        "STAT-PY-031",
                        file=filename,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        detail="字符串常量引用系统账号口令文件路径",
                        source=source,
                    )
                )
    findings.extend(_top_level_findings(filename, source, tree))
    return tuple(findings)


def _call_findings(filename: str, source: str, node: ast.Call) -> list[StaticFinding]:
    """单个调用表达式上的规则命中。"""
    findings: list[StaticFinding] = []
    callee = _callee_name(node.func)
    if callee in _DYNAMIC_EXEC_NAMES:
        findings.append(
            make_finding(
                "STAT-PY-011",
                file=filename,
                line=node.lineno,
                column=node.col_offset + 1,
                detail=f"调用 {callee}",
                source=source,
            )
        )
    if _shell_execution(callee, node):
        findings.append(
            make_finding(
                "STAT-PY-030",
                file=filename,
                line=node.lineno,
                column=node.col_offset + 1,
                detail=f"调用 {callee} 且命令被交给 shell 解释",
                source=source,
            )
        )
    return findings


def _shell_execution(callee: str, node: ast.Call) -> bool:
    """是否"交给 shell 解释执行"：`os.system/os.popen`，或 `subprocess.*` 打开了 shell 选项。

    只认**字面量 True**（变量取值不可静态判定），宁可漏报也不误报——该规则是阻断类。
    """
    if callee in _SHELL_ENTRIES:
        return True
    tail = callee.rsplit(".", 1)[-1]
    if tail not in _SUBPROCESS_ENTRIES or callee.startswith("os."):
        return False
    return _keyword_is_true(node, "shell")


def _keyword_is_true(node: ast.Call, name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is True
    return False


def _is_truthy_literal_loop(node: ast.While) -> bool:
    return isinstance(node.test, ast.Constant) and node.test.value is True


def _top_level_findings(filename: str, source: str, tree: ast.Module) -> list[StaticFinding]:
    """顶层可执行语句（导入即副作用的风险提示，info 级）。"""
    findings: list[StaticFinding] = []
    for node in tree.body:
        if isinstance(node, _ALLOWED_TOP_LEVEL) or _is_main_guard(node) or _is_docstring(node):
            continue
        findings.append(
            make_finding(
                "STAT-PY-013",
                file=filename,
                line=node.lineno,
                column=node.col_offset + 1,
                detail=f"顶层语句类型 {type(node).__name__}",
                source=source,
            )
        )
    return findings


def _is_main_guard(node: ast.stmt) -> bool:
    """`if __name__ == "__main__":` 守卫（结构判定，不做字符串匹配）。"""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    if not isinstance(left, ast.Name) or left.id != "__name__":
        return False
    return isinstance(right, ast.Constant) and right.value == "__main__"


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _callee_name(func: ast.expr) -> str:
    """被调用者的点分名字（`eval` / `os.system` / `subprocess.run`）；不可判定时返回空串。"""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        head = _callee_name(func.value)
        return f"{head}.{func.attr}" if head else func.attr
    return ""


def _syntax_finding(filename: str, raw: bytes, exc: SyntaxError) -> StaticFinding:
    line = exc.lineno if isinstance(exc.lineno, int) and exc.lineno > 0 else 1
    column = exc.offset if isinstance(exc.offset, int) and exc.offset > 0 else None
    detail = f"{type(exc).__name__}: {exc.msg}"
    return make_finding(
        "STAT-PY-001",
        file=filename,
        line=line,
        column=column,
        detail=detail,
        source=raw.decode("utf-8", errors="replace"),
    )


def _unparsable_finding(filename: str, raw: bytes, exc: ValueError) -> StaticFinding:
    """源码无法进入语法分析（如含 NUL 字节）：按语法类错误呈现，行号按第 1 行。"""
    return make_finding(
        "STAT-PY-001",
        file=filename,
        line=1,
        column=None,
        detail=f"{type(exc).__name__}: 源码无法解析",
        source=raw.decode("utf-8", errors="replace"),
    )
