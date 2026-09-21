"""静态合法性检查单元测试（`RQ-B-2-40`，T-STAT-001/002/003/005）。

**纯单元，不依赖 PostgreSQL**，也不依赖真实 bash 一定可用：
- Bash 语法用例分两层——受控进程边界（monkeypatch `subprocess.run`，用 `bash -n` 的**真实 stderr 形态**）
  与"环境真能跑 bash 时"的用例；后者在本机解释器读不到工作区路径时走 `not_checked` 诚实分支。
- 断言口径以 [docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 3 节 I9–I14 为准。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import subprocess
import sys

import pytest

from autograder_b2.domain.envelope import EnvelopeRejected
from autograder_b2.domain.static_result import CheckOutcome, StaticAnalysisResult, StaticFinding
from autograder_b2.static import bash_checker, get_rule, rule_catalog_digest, rules_for
from autograder_b2.static.checker import analyze_static

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 出现在源码里的特征串：它**绝不允许**出现在任何 `message` 中（I12）。
MARKER = "SOURCE_MARKER_9F3_PLEASE_DO_NOT_LEAK"

#: 契约冻结的明细字段名（`docs/architecture/interfaces.md` 第 3 节）。
FINDING_FIELDS = (
    "rule_id",
    "severity",
    "file",
    "line",
    "column",
    "message",
    "blocking",
)

#: 干净可用作"零 error"基线的 Python（含 `__main__` 守卫，顶层无可执行语句）。
CLEAN_PYTHON = '''"""示例解：读取两个整数并输出和。"""

import sys


def main() -> None:
    """入口。"""
    values = sys.stdin.read().split()
    print(int(values[0]) + int(values[1]))


if __name__ == "__main__":
    main()
'''

#: 合法但含告警的 Python（裸 except）。
WARNED_PYTHON = '''def read_text(path):
    try:
        return open(path).read()
    except:
        return ""
'''

#: 干净且有严格模式的 Bash 脚本。
CLEAN_BASH = """#!/usr/bin/env bash
set -euo pipefail

total=0
for value in "$@"; do
  total=$((total + value))
done
echo "$total"
"""


def _write(workspace: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    """以 LF 写入文本（不随宿主平台改成 CRLF），返回绝对路径。"""
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def _analyze(
    workspace: pathlib.Path,
    files: tuple[str, ...],
    language: str,
) -> StaticAnalysisResult:
    return analyze_static(workspace=workspace, files=files, language=language)


def _outcome(result: StaticAnalysisResult, checker: str) -> CheckOutcome:
    matches = [item for item in result.checks if item.checker == checker]
    assert len(matches) == 1, f"检查项缺失或重复: {checker}"
    return matches[0]


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stderr: str = "",
    returncode: int = 0,
) -> list[dict[str, object]]:
    """记录 **真实调用点** 传给 `subprocess.run` 的参数，并返回受控结果（不启动真进程）。"""
    calls: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):  # 复刻 subprocess.run 的调用形态
        calls.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    monkeypatch.setattr(bash_checker.subprocess, "run", fake_run)
    return calls


def _assert_no_pass_wording(result: StaticAnalysisResult) -> None:
    """I14/I16 精神：`not_checked` 的呈现里不得出现"通过/安全/合法"式表述。"""
    texts = [item.reason or "" for item in result.checks]
    texts.extend(finding.message for finding in result.findings)
    for text in texts:
        for wording in ("通过", "安全", "合法", "没发现问题"):
            assert wording not in text, f"出现可疑表述 {wording!r}: {text}"


# ---------------------------------------------------------------- Python 语法（T-STAT-001）


def test_python_syntax_error_is_reported_with_line(tmp_path: pathlib.Path) -> None:
    """语法错误必须被检出，且带行号（可得时带列号）与可读说明。"""
    _write(tmp_path, "solution.py", "def broken(:\n    return 1\n")
    result = _analyze(tmp_path, ("solution.py",), "python")

    errors = [item for item in result.findings if item.severity == "error"]
    assert len(errors) == 1
    assert errors[0].rule_id == "STAT-PY-001"
    assert errors[0].line == 1
    assert errors[0].column is not None and errors[0].column > 0
    assert "SyntaxError" in errors[0].message
    assert result.error_count == 1
    assert result.blocking is False  # 语法错误不是高危类：不阻断为"安全拒绝"

    syntax = _outcome(result, "python_ast")
    assert syntax.status == "ran" and syntax.reason is None and syntax.findings == 1


def test_valid_python_has_no_error_findings(tmp_path: pathlib.Path) -> None:
    """合法代码产出 0 个 error（且干净到连告警也没有）。"""
    _write(tmp_path, "solution.py", CLEAN_PYTHON)
    result = _analyze(tmp_path, ("solution.py",), "python")

    assert result.error_count == 0
    assert result.warning_count == 0
    assert result.info_count == 0
    assert result.findings == ()
    assert result.blocking is False
    assert all(item.status == "ran" for item in result.checks)
    assert result.language == "python"


# ------------------------------------------------------------- 明细结构（T-STAT-002）


def test_warning_finding_fields_are_complete(tmp_path: pathlib.Path) -> None:
    """每条明细含 rule_id/severity/file/line/column/message；severity 只取三值。"""
    _write(tmp_path, "solution.py", WARNED_PYTHON)
    result = _analyze(tmp_path, ("solution.py",), "python")

    warnings = [item for item in result.findings if item.severity == "warning"]
    bare_except = next(item for item in warnings if item.rule_id == "STAT-PY-010")
    assert tuple(field.name for field in dataclasses.fields(StaticFinding)) == FINDING_FIELDS
    assert bare_except.file == "solution.py"
    assert bare_except.line == 4
    assert bare_except.column == 5
    assert bare_except.message.strip()
    assert bare_except.blocking is False
    assert get_rule(bare_except.rule_id).severity == "warning"

    assert {item.severity for item in result.findings} <= {"error", "warning", "info"}
    assert result.warning_count == len(warnings)
    assert result.error_count == 0
    assert result.warning_count + result.info_count == len(result.findings)


def test_every_finding_belongs_to_a_registered_rule(tmp_path: pathlib.Path) -> None:
    """明细只能来自规则目录（未知规则 ID 即目录漂移）。"""
    _write(tmp_path, "run.sh", "rm -rf /\ncat /etc/shadow\n")
    result = _analyze(tmp_path, ("run.sh",), "bash")
    assert result.findings
    for finding in result.findings:
        rule = get_rule(finding.rule_id)
        assert rule.language == "bash"
        assert finding.severity == rule.severity
        assert finding.blocking == rule.blocking
        # 说明以规则文本开头（细节被丢弃时退化为兜底文案）
        assert finding.message.startswith(rule.message[:12]) or finding.message.startswith(
            "命中静态规则"
        )


def test_rule_catalog_is_deterministic() -> None:
    """规则目录确定性：按 ID 排序、无重复、摘要稳定。"""
    ids = [rule.rule_id for rule in rules_for("python")] + [
        rule.rule_id for rule in rules_for("bash")
    ]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert rule_catalog_digest() == rule_catalog_digest()
    assert {rule.severity for rule in rules_for("python")} <= {"error", "warning", "info"}


# ------------------------------------------------------------------ 确定性（T-STAT-003）


def test_repeated_runs_are_identical_for_python(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "solution.py", WARNED_PYTHON + "\nwhile True:\n    pass\n")
    first = _analyze(tmp_path, ("solution.py",), "python")
    second = _analyze(tmp_path, ("solution.py",), "python")
    assert first == second
    assert first.result_digest == second.result_digest
    assert first.findings  # 非空才算有效比对


def test_repeated_runs_are_identical_for_bash(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "run.sh", "set -e\neval \"echo hi\"\nrm -rf /\n")
    first = _analyze(tmp_path, ("run.sh",), "bash")
    second = _analyze(tmp_path, ("run.sh",), "bash")
    assert first == second
    assert first.result_digest == second.result_digest
    assert first.findings


_DIGEST_SCRIPT = (
    "import sys;"
    "from autograder_b2.static import analyze_static;"
    "result = analyze_static(workspace=sys.argv[1], files=(sys.argv[2],), language=sys.argv[3]);"
    "print(result.result_digest);"
    "print('|'.join(f'{f.rule_id}:{f.file}:{f.line}:{f.column}' for f in result.findings))"
)


def test_digest_is_stable_across_processes_and_hash_seeds(tmp_path: pathlib.Path) -> None:
    """跨进程 + 不同 `PYTHONHASHSEED`：摘要与明细序列完全一致（独立运行也复现）。"""
    _write(tmp_path, "solution.py", WARNED_PYTHON + "\nwhile True:\n    pass\n")
    outputs = []
    for seed in ("0", "1", "4321"):
        completed = subprocess.run(
            [sys.executable, "-c", _DIGEST_SCRIPT, str(tmp_path), "solution.py", "python"],
            capture_output=True,
            text=True,
            check=True,
            cwd=B2_ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        outputs.append(completed.stdout)
    assert len(set(outputs)) == 1, outputs
    assert MARKER not in outputs[0]


# ------------------------------------------------------------ Bash 语法（T-STAT-001）


def test_bash_syntax_error_is_reported_with_line(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bash -n` 的 stderr（真实形态）被解析成带行号的明细。"""
    script = _write(tmp_path, "run.sh", "if true; then\n  echo hi\n")
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")
    _patch_run(
        monkeypatch,
        stderr=f"{script}: line 3: syntax error: unexpected end of file\n",
        returncode=2,
    )
    result = _analyze(tmp_path, ("run.sh",), "bash")

    errors = [item for item in result.findings if item.severity == "error"]
    assert [item.rule_id for item in errors] == ["STAT-SH-001"]
    assert errors[0].line == 3
    assert errors[0].column is None  # bash 不给列号：可得时才带
    assert errors[0].file == "run.sh"
    assert result.error_count == 1

    syntax = _outcome(result, "bash_syntax")
    assert syntax.status == "ran" and syntax.reason is None and syntax.findings == 1


def test_valid_bash_script_has_no_error_findings(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法脚本：解释器退出码 0 ⇒ 0 error、0 warning、0 info。"""
    _write(tmp_path, "run.sh", CLEAN_BASH)
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")
    _patch_run(monkeypatch, stderr="", returncode=0)
    result = _analyze(tmp_path, ("run.sh",), "bash")

    assert result.error_count == 0
    assert result.warning_count == 0
    assert result.info_count == 0
    assert _outcome(result, "bash_syntax").status == "ran"
    assert result.blocking is False


def test_real_bash_environment_is_reported_honestly(tmp_path: pathlib.Path) -> None:
    """真实环境（不 monkeypatch）：能读工作区就必须检出，读不到就必须报 `not_checked`。"""
    _write(tmp_path, "broken.sh", "if true; then\n  echo hi\n")
    result = _analyze(tmp_path, ("broken.sh",), "bash")
    syntax = _outcome(result, "bash_syntax")
    reported = [item for item in result.findings if item.rule_id == "STAT-SH-001"]

    if syntax.status == "ran":
        assert len(reported) == 1
        assert isinstance(reported[0].line, int) and reported[0].line > 0
    else:
        assert syntax.reason, "未执行的检查必须给出原因"
        assert syntax.findings == 0
        assert reported == []
        _assert_no_pass_wording(result)


# --------------------------------------------------------- bash 不可用（诚实原则 I14）


def test_bash_unavailable_yields_not_checked_with_reason(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测不到解释器 ⇒ `not_checked` + 原因，且绝不启动任何进程。"""
    _write(tmp_path, "run.sh", 'set -e\nvalue=$(eval "echo 1")\n')
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: None)
    calls = _patch_run(monkeypatch, stderr="", returncode=0)
    result = _analyze(tmp_path, ("run.sh",), "bash")

    syntax = _outcome(result, "bash_syntax")
    assert syntax.status == "not_checked"
    assert syntax.reason and "bash" in syntax.reason and "未执行" in syntax.reason
    assert syntax.findings == 0
    assert [item for item in result.findings if item.rule_id == "STAT-SH-001"] == []
    assert calls == []
    _assert_no_pass_wording(result)
    assert result.warning_count == 1  # 结构检查仍然跑了：覆盖缺口不影响其他检查的结论


def test_bash_invocation_failure_yields_not_checked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解释器在启动时消失（OSError）⇒ `not_checked` + 脱敏原因，不伪装成没问题。"""
    _write(tmp_path, "run.sh", CLEAN_BASH)
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")

    def boom(*args, **kwargs):  # 模拟解释器在启动时消失
        raise FileNotFoundError("interpreter vanished")

    monkeypatch.setattr(bash_checker.subprocess, "run", boom)
    result = _analyze(tmp_path, ("run.sh",), "bash")

    syntax = _outcome(result, "bash_syntax")
    assert syntax.status == "not_checked"
    assert syntax.reason and "未执行" in syntax.reason
    assert syntax.findings == 0
    assert str(tmp_path) not in syntax.reason
    _assert_no_pass_wording(result)


def test_unattributable_bash_output_is_not_checked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stderr 有错但无行号（如解释器读不到工作区路径）⇒ 不得编造语法错误，只能报未检查。"""
    script = _write(tmp_path, "run.sh", CLEAN_BASH)
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")
    _patch_run(
        monkeypatch,
        stderr=f"/bin/bash: {script}: No such file or directory\n",
        returncode=127,
    )
    result = _analyze(tmp_path, ("run.sh",), "bash")

    syntax = _outcome(result, "bash_syntax")
    assert syntax.status == "not_checked"
    assert syntax.reason and "未执行" in syntax.reason
    assert [item for item in result.findings if item.rule_id == "STAT-SH-001"] == []
    assert result.error_count == 0
    _assert_no_pass_wording(result)


def test_missing_workspace_file_makes_checks_not_checked(tmp_path: pathlib.Path) -> None:
    """工作区缺已声明文件 ⇒ 两个检查都 `not_checked`（不部分检查、不假装干净）。"""
    _write(tmp_path, "solution.py", CLEAN_PYTHON)
    result = _analyze(tmp_path, ("solution.py", "absent.py"), "python")
    assert result.findings == ()
    assert all(item.status == "not_checked" and item.reason for item in result.checks)
    _assert_no_pass_wording(result)


def test_checker_internal_failure_is_reported_as_not_checked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检查器自身异常 ⇒ `not_checked` + 脱敏原因（只留异常类名），不中断评测、不伪装成干净。"""
    _write(tmp_path, "run.sh", CLEAN_BASH)
    _write(tmp_path, "solution.py", CLEAN_PYTHON)

    def boom(*args, **kwargs):  # 模拟检查器内部故障（含内部绝对路径，验证脱敏）
        raise RuntimeError(f"internal failure at {tmp_path}")

    monkeypatch.setattr(bash_checker, "check_bash_syntax", boom)
    bash_result = _analyze(tmp_path, ("run.sh",), "bash")
    bash_syntax = _outcome(bash_result, "bash_syntax")
    assert bash_syntax.status == "not_checked"
    assert bash_syntax.reason and "RuntimeError" in bash_syntax.reason
    assert str(tmp_path) not in bash_syntax.reason
    assert bash_syntax.findings == 0
    assert _outcome(bash_result, "structural_rules").status == "ran"

    from autograder_b2.static import python_checker

    monkeypatch.setattr(python_checker, "parse_python", boom)
    python_result = _analyze(tmp_path, ("solution.py",), "python")
    python_ast = _outcome(python_result, "python_ast")
    assert python_ast.status == "not_checked"
    assert python_ast.reason and "RuntimeError" in python_ast.reason
    assert python_ast.findings == 0
    # 语法步失败 ⇒ 没有语法树 ⇒ 结构检查也必须如实报未执行
    assert _outcome(python_result, "structural_rules").status == "not_checked"
    assert python_result.blocking is False


# ------------------------------------------------- 固定参数数组（无 shell 字符串）


def test_bash_is_invoked_with_fixed_argv_array(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """断言真实调用点：argv 列表、逐项独立、`shell` 未开启、源码没被拼进命令行。"""
    script = _write(tmp_path, "run.sh", "if true; then\n  echo hi\nfi\n")
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")
    calls = _patch_run(
        monkeypatch,
        stderr=f"{script}: line 3: syntax error near unexpected token\n",
        returncode=2,
    )
    _analyze(tmp_path, ("run.sh",), "bash")

    assert len(calls) == 1
    argv = calls[0]["argv"]
    kwargs = calls[0]["kwargs"]
    assert isinstance(argv, (list, tuple))
    assert all(isinstance(item, str) for item in argv)
    argv_items = list(argv)
    assert argv_items[0] == "/usr/bin/bash"
    assert argv_items[1] == "-n"
    assert pathlib.Path(argv_items[2]).name == "run.sh"
    assert kwargs.get("shell", False) is False
    assert "command" not in kwargs
    joined = " ".join(argv_items)
    for metacharacter in (";", "|", "&", "$(", "`", ">"):
        assert metacharacter not in joined
    assert "echo hi" not in joined


def test_probe_bash_uses_argv_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测本身也是 argv 调用（不拼接、不经 shell）。"""
    monkeypatch.setattr(bash_checker.shutil, "which", lambda name: "/usr/bin/bash")
    calls = _patch_run(monkeypatch, returncode=0)
    assert bash_checker.probe_bash() == "/usr/bin/bash"
    assert list(calls[0]["argv"]) == ["/usr/bin/bash", "--version"]
    assert calls[0]["kwargs"].get("shell", False) is False


def test_probe_bash_returns_none_when_interpreter_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bash_checker.shutil, "which", lambda name: None)
    assert bash_checker.probe_bash() is None


def test_static_sources_never_run_shell_strings() -> None:
    """源码级证据：调用点代码里不出现 shell 开关或字符串执行。"""
    for relative in (
        "src/autograder_b2/static/bash_checker.py",
        "src/autograder_b2/static/checker.py",
    ):
        text = (B2_ROOT / relative).read_text(encoding="utf-8")
        assert "shell=True" not in text
        assert "shell = True" not in text
        assert "os.system(" not in text
        assert "Popen(" not in text


def test_python_checker_never_executes_code() -> None:
    """I9 源码级证据：Python 检查器不含 exec/eval/import/compile 调用。"""
    text = (B2_ROOT / "src/autograder_b2/static/python_checker.py").read_text(encoding="utf-8")
    for forbidden in ("exec(", "eval(", "__import__", "importlib", "compile("):
        assert forbidden not in text


# ------------------------------------------------------- 无 import 副作用（T-STAT-005）


SIDE_EFFECT_MODULE = '''"""顶层副作用的示例模块：静态检查绝不允许执行它。"""

import pathlib

pathlib.Path(__file__).with_name("PWNED_SIDE_EFFECT.txt").write_text("executed")
'''


def test_static_check_does_not_execute_module_side_effects(tmp_path: pathlib.Path) -> None:
    """喂一个"顶层就写文件"的模块：检查过程不得产生该副作用。"""
    _write(tmp_path, "solution.py", SIDE_EFFECT_MODULE)
    result = _analyze(tmp_path, ("solution.py",), "python")

    assert not (tmp_path / "PWNED_SIDE_EFFECT.txt").exists()
    assert not list(tmp_path.glob("PWNED*"))
    # 反向证据：确实**检查过**（顶层可执行语句被登记为 info），而不是"没跑所以没副作用"
    assert [item.rule_id for item in result.findings if item.rule_id == "STAT-PY-013"]
    assert result.error_count == 0
    assert _outcome(result, "python_ast").status == "ran"


def test_static_check_does_not_import_declared_modules(tmp_path: pathlib.Path) -> None:
    """顶层 import 也不会真的发生：被导入模块的同目录副作用文件不产生。"""
    _write(tmp_path, "sideeffect.py", SIDE_EFFECT_MODULE)
    _write(tmp_path, "solution.py", "import sideeffect\n\nprint(sideeffect)\n")
    result = _analyze(tmp_path, ("solution.py", "sideeffect.py"), "python")

    assert not (tmp_path / "PWNED_SIDE_EFFECT.txt").exists()
    assert result.error_count == 0
    # `sideeffect.py` 自身的顶层副作用语句被登记（说明它是被解析、而非被导入）
    assert any(
        item.rule_id == "STAT-PY-013" and item.file == "sideeffect.py" for item in result.findings
    )


# ------------------------------------------------------- message 不含源码行（I12）


def test_python_messages_never_inline_source_lines(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "solution.py", f"{MARKER} = (1 +\n")
    result = _analyze(tmp_path, ("solution.py",), "python")

    assert result.findings
    assert all(MARKER not in item.message for item in result.findings)
    assert all(str(tmp_path) not in item.message for item in result.findings)


def test_bash_messages_never_inline_source_lines(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bash -n` 会同时打印语法错误行与源码上下文行：后者必须被丢弃。"""
    script = _write(tmp_path, "run.sh", f"{MARKER}=1\n")
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")
    _patch_run(
        monkeypatch,
        stderr=(
            f"{script}: line 1: syntax error near unexpected token `{MARKER}'\n"
            f"{script}: line 1: `{MARKER}=1\n"
        ),
        returncode=2,
    )
    result = _analyze(tmp_path, ("run.sh",), "bash")

    reported = [item for item in result.findings if item.rule_id == "STAT-SH-001"]
    assert len(reported) == 1 and reported[0].line == 1
    assert reported[0].message.startswith(get_rule("STAT-SH-001").message)
    assert all(MARKER not in item.message for item in result.findings)
    assert all(str(script) not in item.message for item in result.findings)


def test_bash_message_is_dropped_when_detail_embeds_source(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """异常形态：stderr 的说明里直接内联整行源码 ⇒ 整段细节被丢弃，只留规则说明。"""
    source = f"value={MARKER}\n"
    script = _write(tmp_path, "run.sh", source)
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: "/usr/bin/bash")
    _patch_run(monkeypatch, stderr=f"{script}: line 1: {source.strip()}\n", returncode=2)
    result = _analyze(tmp_path, ("run.sh",), "bash")

    syntax = [item for item in result.findings if item.rule_id == "STAT-SH-001"]
    assert len(syntax) == 1 and syntax[0].line == 1
    assert syntax[0].message == get_rule("STAT-SH-001").message  # 细节被丢弃，退回纯规则说明
    assert all(MARKER not in item.message for item in result.findings)
    assert all(str(script) not in item.message for item in result.findings)


# -------------------------------------------------- 阻断判定与聚合不变量（I13/I14）


def test_blocking_comes_only_from_executed_checks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bash -n` 未执行时，结构检查的阻断命中仍然有效，但未执行的检查零明细。"""
    _write(tmp_path, "run.sh", "rm -rf /\n")
    monkeypatch.setattr(bash_checker, "probe_bash", lambda: None)
    _patch_run(monkeypatch, stderr="", returncode=0)
    result = _analyze(tmp_path, ("run.sh",), "bash")

    blocking_findings = [item for item in result.findings if item.blocking]
    assert [item.rule_id for item in blocking_findings] == ["STAT-SH-010"]
    assert result.blocking is True
    assert _outcome(result, "bash_syntax").status == "not_checked"
    assert _outcome(result, "bash_syntax").findings == 0
    assert _outcome(result, "structural_rules").status == "ran"
    for outcome in result.checks:
        if outcome.status == "not_checked":
            assert outcome.reason
        else:
            assert outcome.reason is None


def test_python_structural_rules_run_without_syntax_errors(tmp_path: pathlib.Path) -> None:
    """`os.system` 与敏感路径字面量属阻断类（ADR-0001 第 4 条），命中即阻断。"""
    _write(
        tmp_path,
        "solution.py",
        'import os\n\nos.system("ls")\n\ndata = open("/etc/shadow").read()\nprint(data)\n',
    )
    result = _analyze(tmp_path, ("solution.py",), "python")

    assert [item.rule_id for item in result.findings if item.blocking] == [
        "STAT-PY-030",
        "STAT-PY-031",
    ]
    assert result.blocking is True
    assert result.error_count == 2


def test_structural_check_is_not_checked_without_syntax_tree(tmp_path: pathlib.Path) -> None:
    """唯一文件有语法错误 ⇒ 结构检查无语法树可用，必须 `not_checked`（不假装零 finding 通过）。"""
    _write(tmp_path, "solution.py", f"{MARKER} = (1 +\n")
    result = _analyze(tmp_path, ("solution.py",), "python")

    structural = _outcome(result, "structural_rules")
    assert structural.status == "not_checked"
    assert structural.reason and structural.findings == 0
    assert all(not item.blocking for item in result.findings)
    _assert_no_pass_wording(result)


def test_checks_and_counts_are_consistent(tmp_path: pathlib.Path) -> None:
    """`checks` 顺序固定、计数自洽、明细按 (file, line, column, rule_id) 排序。"""
    _write(tmp_path, "b.py", WARNED_PYTHON)
    _write(tmp_path, "a.py", 'import os\n\nos.system("ls")\n')
    result = _analyze(tmp_path, ("b.py", "a.py"), "python")

    assert [item.checker for item in result.checks] == ["python_ast", "structural_rules"]
    assert sum(item.findings for item in result.checks) == len(result.findings)
    keys = [
        (item.file, item.line or 0, item.column or 0, item.rule_id) for item in result.findings
    ]
    assert keys == sorted(keys)
    assert result.error_count == 1 and result.warning_count == 1
    assert result.language == "python"


# ------------------------------------------------------------------ 输入围栏（I2）


@pytest.mark.parametrize("hostile", ["../escape.py", "/etc/passwd", "a/../../b.py", "", "a\\b.py"])
def test_path_outside_whitelist_is_rejected(tmp_path: pathlib.Path, hostile: str) -> None:
    _write(tmp_path, "solution.py", CLEAN_PYTHON)
    with pytest.raises(EnvelopeRejected) as caught:
        _analyze(tmp_path, ("solution.py", hostile), "python")
    assert caught.value.reason == "path_not_allowed"


def test_empty_fileset_is_rejected(tmp_path: pathlib.Path) -> None:
    with pytest.raises(EnvelopeRejected) as caught:
        _analyze(tmp_path, (), "python")
    assert caught.value.reason == "empty_fileset"


def test_unsupported_language_is_rejected(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "solution.py", CLEAN_PYTHON)
    with pytest.raises(ValueError):
        _analyze(tmp_path, ("solution.py",), "ruby")


def test_language_and_checker_names_match_contract(tmp_path: pathlib.Path) -> None:
    """语言枚举只有 python/bash；检查器名字只在契约声明的三个之内。"""
    _write(tmp_path, "solution.py", CLEAN_PYTHON)
    result = _analyze(tmp_path, ("solution.py",), "python")
    assert result.language in ("python", "bash")
    assert {item.checker for item in result.checks} <= {
        "python_ast",
        "bash_syntax",
        "structural_rules",
    }
    assert all(item.status in ("ran", "not_checked") for item in result.checks)
