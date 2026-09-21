"""`CHG-0012` 三个新字段的验收：资源读数、文件证据、确定性解释——以及沙箱自证的三态。

本文件回答的问题，绿套件本身证明不了：

1. **读不到就是 `null`，不是 0**：cgroup 读数逐项可空；在合成宿主上（有 `cpu.stat`、没有
   `memory.peak`）验证"缺哪一项就哪一项为空"，并验证"宿主根本没有 cgroup v2"时字段仍不是 0。
2. **文件证据真的落进报告**：`created`/`modified`/`deleted` 的路径、内容摘要与大小，
   以及"同一份输入 ⇒ 同一份清单 ⇒ 同一 `result_digest`"（用真实的两次工作区快照证明）。
3. **解释是规则推导的**：五种失败形态各有一段可读文本；同输入同输出；且它**只含行号/路径/计数**，
   不含期望输出内容。
4. **受保护值在学生视图里被裁掉**：`artifacts.expected_sha256` 对未声明可见的用例必须置空，
   而 `actual_sha256` 与文件清单保留。
5. **沙箱自证的三态**：Windows（无 nsjail）上每一项都是 `not_checked` 且**不创建任何进程**；
   逃逸成功记 `found`、被挡住记 `not_found`、无法归因记 `not_checked`。

纯逻辑部分（cgroup 解析、解释规则、金丝雀判定）不依赖数据库，因此不挂 `requires_postgres`。
"""

from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autograder_b2.canary import (
    BLOCKED_MARKER,
    ESCAPED_MARKER,
    EXIT_FOUND_RISK,
    EXIT_NOT_CHECKED,
    EXIT_OK,
    CanaryReport,
    run_canary,
)
from autograder_b2.domain.evidence import (
    CaseArtifacts,
    CaseEvidence,
    CaseResource,
    FileChange,
)
from autograder_b2.domain.security import SecurityDecision, SecurityFinding
from autograder_b2.domain.static_result import StaticAnalysisResult
from autograder_b2.evaluation.explanation import explain_case
from autograder_b2.evaluation.report import (
    ReportBinding,
    ReportInputs,
    artifacts_payload,
    build_report,
    for_student,
    report_digest,
    resource_payload,
)
from autograder_b2.evaluation.scoring import score_run
from autograder_b2.runner.adapters import (
    EVIDENCE_HASH_BUDGET_BYTES,
    snapshot_workspace,
    workspace_changes,
)
from autograder_b2.runner.cgroup import (
    CgroupV2Peaks,
    case_cgroup_directory,
    case_resource_from_peaks,
    cgroup_v2_present,
    limit_hit_for,
    parse_cpu_stat,
    read_cgroup_v2_peaks,
)
from autograder_b2.runner.comparator import first_differing_line
from autograder_b2.runner.policy import RunnerPolicy
from autograder_b2.runner.run_case import RunFacts, evidence_digest_of

BASE = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
CASE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-evidence-details-case"))
#: 报告夹具里固定的标识：报告摘要**纳入**它们（它们是报告内容的一部分），因此夹具必须固定，
#: 否则"只改证据里的一项"这类断言会被 uuid4 的差异掩盖。
_RUN_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-evidence-details-run"))
_SUBMISSION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-evidence-details-submission"))
_QUESTION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-evidence-details-question"))


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- 1. cgroup v2 读数：合成宿主的 Linux 路径 ----------------------------------------------


def _write_cgroup(
    root: pathlib.Path,
    name: str,
    *,
    memory_peak: str | None = "38200000",
    cpu_stat: str | None = "usage_usec 110000\nuser_usec 90000\nsystem_usec 20000\n",
    pids_peak: str | None = "3",
) -> pathlib.Path:
    """造一个"像 cgroup v2 一样"的目录树（缺哪项就不写哪个文件）。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for filename, content in (
        ("memory.peak", memory_peak),
        ("cpu.stat", cpu_stat),
        ("pids.peak", pids_peak),
    ):
        if content is not None:
            (directory / filename).write_text(content, encoding="utf-8")
    return directory


def test_cgroup_reader_parses_peaks_and_reports_missing_files_as_none(tmp_path: pathlib.Path) -> None:
    """Linux 路径（用真实文件布局验证，不需要 nsjail）：读到就填、读不到就是 `None`。

    **不写 0**：0 会被读成"用了 0 字节"，而事实是"没测量"。
    """
    directory = _write_cgroup(tmp_path, "NSJAIL.4242")
    peaks = read_cgroup_v2_peaks(directory)
    assert peaks.memory_peak_bytes == 38200000
    assert peaks.cpu_user_seconds == pytest.approx(0.09)
    assert peaks.cpu_system_seconds == pytest.approx(0.02)
    assert peaks.pids_peak == 3
    assert peaks.measured is True

    partial = _write_cgroup(tmp_path, "NSJAIL.4243", memory_peak=None, pids_peak=None)
    partial_peaks = read_cgroup_v2_peaks(partial)
    assert partial_peaks.memory_peak_bytes is None
    assert partial_peaks.pids_peak is None
    assert partial_peaks.cpu_user_seconds == pytest.approx(0.09)
    assert partial_peaks.measured is True

    empty = _write_cgroup(
        tmp_path, "NSJAIL.4244", memory_peak=None, cpu_stat=None, pids_peak=None
    )
    nothing = read_cgroup_v2_peaks(empty)
    assert nothing == CgroupV2Peaks()
    assert nothing.measured is False


def test_cgroup_reader_never_invents_a_zero() -> None:
    """目录根本不存在（本机形态）：四个字段全是 `None`，**没有一个 0**。"""
    peaks = read_cgroup_v2_peaks(pathlib.Path(".pytest-tmp") / "no-such-cgroup")
    assert peaks == CgroupV2Peaks()
    resource = case_resource_from_peaks(peaks, wall_seconds=0.31, limit_hit=None)
    assert resource.memory_peak_bytes is None
    assert resource.cpu_user_seconds is None
    assert resource.cpu_system_seconds is None
    assert resource.pids_peak is None
    assert resource.wall_seconds == 0.31  # 墙钟由 B2 自己测量，因此总有值
    payload = resource_payload(resource)
    assert payload is not None
    assert payload["memory_peak_bytes"] is None and payload["memory_peak_bytes"] != 0


def test_cpu_stat_parser_only_reads_the_two_fields_it_claims() -> None:
    """只认 `user_usec`/`system_usec`；其它行不参与（解析不出即 `None`，不是 0）。"""
    user, system = parse_cpu_stat("usage_usec 5\nuser_usec 1500000\nsystem_usec 250000\n")
    assert (user, system) == (1.5, 0.25)
    assert parse_cpu_stat("usage_usec 5\n") == (None, None)
    assert parse_cpu_stat("user_usec not-a-number\n") == (None, None)


def test_case_cgroup_directory_uses_the_child_pid_and_requires_the_directory(
    tmp_path: pathlib.Path,
) -> None:
    """定位规则取自 nsjail 源码（`NSJAIL.<被囚子进程 pid>`）；目录不存在即 `None`（不猜）。"""
    policy = RunnerPolicy(cgroup_v2_mount=str(tmp_path))
    _write_cgroup(tmp_path, "NSJAIL.7777")
    found = case_cgroup_directory(
        policy, sandbox_pid=100, children_of=lambda pid: (7000, 7777), mount=tmp_path
    )
    assert found == tmp_path / "NSJAIL.7777"
    missing = case_cgroup_directory(
        policy, sandbox_pid=100, children_of=lambda pid: (1234,), mount=tmp_path
    )
    assert missing is None
    assert case_cgroup_directory(policy, sandbox_pid=100, children_of=lambda pid: (), mount=tmp_path) is None


def test_cgroup_presence_check_is_honest_on_a_host_without_cgroup(tmp_path: pathlib.Path) -> None:
    assert cgroup_v2_present(tmp_path) is False
    (tmp_path / "cgroup.controllers").write_text("cpu memory pids\n", encoding="utf-8")
    assert cgroup_v2_present(tmp_path) is True


def test_limit_hit_mapping_covers_the_contract_enum() -> None:
    """B2 的限制项 → 契约 `limit_hit`；`files` 没有对应取值（见 `runner.cgroup` 的说明）。"""
    assert limit_hit_for("memory") == "memory"
    assert limit_hit_for("cpu") == "cpu"
    assert limit_hit_for("wall") == "wall"
    assert limit_hit_for("processes") == "pids"
    assert limit_hit_for("output") == "output"
    assert limit_hit_for("files") is None
    assert limit_hit_for(None) is None


def test_resource_is_excluded_from_the_replay_digests() -> None:
    """资源读数是环境量：改它**不得**改变 `evidence_digest`，也不得改变 `result_digest`。"""
    evidence = _evidence(resource=CaseResource(memory_peak_bytes=1, wall_seconds=0.1))
    slower_host = _evidence(resource=CaseResource(memory_peak_bytes=99, wall_seconds=9.9))
    assert evidence_digest_of(evidence) == evidence_digest_of(slower_host)
    report_one = _report([evidence])
    report_two = _report([slower_host])
    assert report_digest(report_one) == report_digest(report_two)


# --- 2. 文件证据 ---------------------------------------------------------------------------


def test_workspace_changes_record_paths_digests_and_sizes(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # 一律写**字节**：文本模式的换行翻译（Windows 上 `\n` → `\r\n`）会让"盘上的字节"与
    # `_digest("…\n")` 对不上，而证据记的正是盘上的字节。
    (workspace / "keep.txt").write_bytes(b"same\n")
    (workspace / "edit.txt").write_bytes(b"before\n")
    (workspace / "gone.txt").write_bytes(b"delete me\n")
    before = snapshot_workspace(workspace)

    (workspace / "edit.txt").write_bytes(b"after\n")
    (workspace / "gone.txt").unlink()
    (workspace / "new.txt").write_bytes(b"created\n")
    after = snapshot_workspace(workspace)
    changes = workspace_changes(before, after)

    assert [item.path for item in changes.created] == ["new.txt"]
    assert changes.created[0].content_sha256 == _digest("created\n")
    assert changes.created[0].size_bytes == len("created\n")
    assert [item.path for item in changes.modified] == ["edit.txt"]
    assert changes.modified[0].content_sha256 == _digest("after\n")
    assert [item.path for item in changes.deleted] == ["gone.txt"]
    # 删除的文件：摘要取自**运行前**快照（内容此刻已不在盘上，事后再算是不可能的）。
    assert changes.deleted[0].content_sha256 == _digest("delete me\n")
    assert changes.truncated is False


def test_workspace_changes_skip_interpreter_bytecode(tmp_path: pathlib.Path) -> None:
    """解释器缓存不进证据清单（.pyc 头里带源文件 mtime ⇒ 纳入即毁掉可重放性）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    before = snapshot_workspace(workspace)
    cache = workspace / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-312.pyc").write_bytes(b"\x00\x01mtime-dependent")
    (workspace / "result.txt").write_text("ok\n", encoding="utf-8")
    changes = workspace_changes(before, snapshot_workspace(workspace))
    assert [item.path for item in changes.created] == ["result.txt"]


def test_workspace_change_lists_are_bounded_and_flag_truncation(tmp_path: pathlib.Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    before = snapshot_workspace(workspace)
    for index in range(5):
        (workspace / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
    changes = workspace_changes(before, snapshot_workspace(workspace), limit=3)
    assert len(changes.created) == 3
    assert changes.truncated is True


def test_hash_budget_marks_unhashed_files_instead_of_lying(tmp_path: pathlib.Path) -> None:
    """超出摘要预算的文件：大小照记、摘要为 `None`（"没测"而不是"测出来是空"）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a" * 10, encoding="utf-8")
    (workspace / "b.txt").write_text("b" * 10, encoding="utf-8")
    snapshot = snapshot_workspace(workspace, hash_budget_bytes=10)
    assert snapshot["a.txt"].content_sha256 == _digest("a" * 10)
    assert snapshot["b.txt"].content_sha256 is None
    assert snapshot["b.txt"].size_bytes == 10
    assert EVIDENCE_HASH_BUDGET_BYTES > 10  # 预算常量是给生产用的，不是这里的小值


def test_artifacts_payload_and_student_view_protection() -> None:
    """`expected_sha256` 受保护（学生视图置空），`actual_sha256` 与清单保留。"""
    artifacts = CaseArtifacts(
        expected_sha256=_digest("expected"),
        actual_sha256=_digest("actual"),
        created=(FileChange("out.txt", _digest("actual"), 6),),
        modified=(),
        deleted=(),
    )
    payload = artifacts_payload(artifacts)
    assert payload is not None
    assert payload["expected_sha256"] == _digest("expected")
    assert "truncated" not in payload, "B2 内部字段不得出现在契约载荷里"

    report = _report([_evidence(artifacts=artifacts)])
    student = for_student(report)
    item = student["case_evidence"][0]
    assert item["artifacts"]["expected_sha256"] is None
    assert item["artifacts"]["actual_sha256"] == _digest("actual")
    assert item["artifacts"]["created"] == payload["created"]
    teacher_item = report["case_evidence"][0]
    assert teacher_item["artifacts"]["expected_sha256"] == _digest("expected")


def test_artifacts_are_absent_when_the_case_never_executed() -> None:
    """没有真的执行 ⇒ `resource`/`artifacts` 为 `None`（"没有产物"，不是"产物为空"）。"""
    report = _report([_evidence(resource=None, artifacts=None)])
    item = report["case_evidence"][0]
    assert item["resource"] is None and item["artifacts"] is None


# --- 3. 确定性解释 -------------------------------------------------------------------------


def test_explanation_covers_every_required_failure_shape() -> None:
    """输出不符（**指名首个分歧行**）、非零退出、超时、缺产出文件、安全阻断，五种都要有话说。"""
    mismatch = explain_case(
        _evidence(failure_category="output_mismatch", passed=False, mismatch_line=3)
    )
    assert "第 3 行" in mismatch and "不一致" in mismatch

    crash = explain_case(_evidence(failure_category="crash", passed=False, exit_code=1))
    assert "非零退出码 1" in crash

    timeout = explain_case(_evidence(failure_category="timeout", passed=False, exit_code=None))
    assert "超时" in timeout and "墙钟上限" in timeout

    missing = explain_case(
        _evidence(
            failure_category="missing_output",
            passed=False,
            missing_outputs=("out.txt",),
        )
    )
    assert "out.txt" in missing and "未产出" in missing

    blocked = explain_case(
        _evidence(
            failure_category="security_blocked",
            passed=False,
            violations=(
                SecurityFinding(
                    finding_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-explain-finding")),
                    category="network_attempt",
                    severity="high",
                    detected_in="runtime",
                    message="sandbox reported a runtime violation: network_attempt",
                    rule_id="runner.sandbox_log",
                ),
            ),
        )
    )
    assert "安全策略阻断" in blocked and "联网尝试" in blocked

    passed = explain_case(_evidence())
    assert "通过" in passed


def test_explanation_is_deterministic_and_content_free() -> None:
    """同输入同输出；且文本里只有行号/路径/计数，没有期望输出的内容。"""
    secret = "SECRET_EXPECTED_LINE_CONTENT"
    evidence = _evidence(
        failure_category="output_mismatch",
        passed=False,
        mismatch_line=2,
        expected_output_excerpt=secret,
        actual_output_excerpt=secret,
    )
    first = explain_case(evidence)
    second = explain_case(evidence)
    assert first == second
    assert secret not in first
    # 解释进摘要 ⇒ 必须由稳定字段推出（不得含墙钟/资源读数）。
    assert "秒" not in first


def test_first_differing_line_is_one_based_and_handles_length_differences() -> None:
    assert first_differing_line("a\nb\n", "a\nb\n") is None
    assert first_differing_line("a\nX\n", "a\nb\n") == 2
    assert first_differing_line("a\n", "a\nb\n") == 2
    assert first_differing_line("", "a\n") == 1
    # 忽略行尾空白的策略下，只有真正的差异才算差异。
    assert first_differing_line("a  \n", "a\n", normalize=True) is None


# --- 4. 沙箱自证（canary）------------------------------------------------------------------


def test_canary_on_a_host_without_nsjail_reports_not_checked_and_runs_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """Windows/无 nsjail 宿主：每一项都是 `not_checked`，且**没有任何进程被创建**。"""
    launched: list[object] = []

    class _Launcher:
        def start(self, *args: object, **kwargs: object) -> object:
            launched.append(args)
            raise AssertionError("沙箱不可用时不得启动任何进程")

    report = run_canary(RunnerPolicy(), launcher=_Launcher(), work_root=tmp_path)
    assert report.sandbox.available is False
    assert {item.status for item in report.items} == {"not_checked"}
    assert report.verdict == "not_checked"
    assert report.exit_code == EXIT_NOT_CHECKED
    assert launched == []
    payload = report.as_json(RunnerPolicy())
    statement = payload["statement"]
    assert isinstance(statement, str)
    assert "安全" in statement and "未检查" in statement
    assert "未被证明" in statement
    for item in payload["items"]:
        assert item["status"] == "not_checked"
        assert "沙箱不可用" in item["detail"]
    assert not (tmp_path / "canary-00-host_file_read").exists(), "沙箱不可用时不创建工作区"


def test_canary_verdicts_map_to_the_three_state_semantics() -> None:
    """三态判定：逃逸成功 ⇒ `found`；被挡住 ⇒ `not_found`；无法归因 ⇒ `not_checked`。"""
    from autograder_b2.canary import _judge

    probe = _probe()
    escaped = _judge(probe, _facts(stdout=f"{ESCAPED_MARKER}:host_file_read", exit_code=0))
    assert escaped.status == "found"
    blocked = _judge(
        probe, _facts(stdout=f"{BLOCKED_MARKER}: read denied (FileNotFoundError)", exit_code=0)
    )
    assert blocked.status == "not_found"
    killed = _judge(probe, _facts(stdout="", exit_code=-31))
    assert killed.status == "not_found" and "31" in killed.detail
    unattributable = _judge(probe, _facts(stdout="", exit_code=1))
    assert unattributable.status == "not_checked"
    assert "无法归因" in unattributable.detail


def test_canary_exit_codes_never_render_not_checked_as_success() -> None:
    from autograder_b2.canary import CanaryItem

    sandbox = run_canary(RunnerPolicy()).sandbox
    found = CanaryReport(
        sandbox=sandbox,
        items=(CanaryItem("p", "d", "e", "found", "x"),),
    )
    assert found.exit_code == EXIT_FOUND_RISK
    unchecked = CanaryReport(
        sandbox=sandbox,
        items=(
            CanaryItem("p", "d", "e", "not_found", "x"),
            CanaryItem("q", "d", "e", "not_checked", "y"),
        ),
    )
    assert unchecked.exit_code == EXIT_NOT_CHECKED
    clean = CanaryReport(sandbox=sandbox, items=(CanaryItem("p", "d", "e", "not_found", "x"),))
    assert clean.exit_code == EXIT_OK
    assert "不是形式化保证" in str(clean.as_json(RunnerPolicy())["statement"])


def test_canary_sources_are_syntactically_valid_and_carry_both_markers() -> None:
    """金丝雀程序本身必须能被解析，且两个标记都在（否则判定会永远落在"未检查"）。"""
    import ast

    from autograder_b2.canary import _canary_probes

    probes = _canary_probes(RunnerPolicy())
    assert len(probes) == 6
    for probe in probes:
        ast.parse(probe.source)
        assert ESCAPED_MARKER in probe.source and BLOCKED_MARKER in probe.source
    assert {probe.probe_id for probe in probes} == {
        "host_file_read",
        "network",
        "privilege_escalation",
        "read_only_root",
        "memory_limit",
        "pids_limit",
    }


# --- 夹具：合成证据与报告 ------------------------------------------------------------------


def _evidence(
    *,
    passed: bool = True,
    failure_category: str | None = None,
    exit_code: int | None = 0,
    mismatch_line: int | None = None,
    missing_outputs: tuple[str, ...] = (),
    mismatched_outputs: tuple[str, ...] = (),
    violations: tuple[SecurityFinding, ...] = (),
    resource: CaseResource | None = None,
    artifacts: CaseArtifacts | None = None,
    expected_output_excerpt: str | None = "42\n",
    actual_output_excerpt: str | None = "42\n",
) -> CaseEvidence:
    return CaseEvidence(
        case_id=CASE_ID,
        ordinal=0,
        weight=10.0,
        passed=passed,
        failure_category=failure_category,  # type: ignore[arg-type]
        input_digest=_digest("input"),
        stdout_digest=_digest("stdout"),
        stderr_digest=_digest("stderr"),
        actual_output_excerpt=actual_output_excerpt,
        expected_output_excerpt=expected_output_excerpt,
        exit_code=exit_code,
        duration_ms=12,
        resource_limit_hit=None,
        sandbox_violations=violations,
        message="evidence message",
        mismatch_line=mismatch_line,
        missing_outputs=missing_outputs,
        mismatched_outputs=mismatched_outputs,
        resource=resource,
        artifacts=artifacts,
        evidence_digest="",
    )


def _report(evidences: list[CaseEvidence]) -> Mapping[str, object]:
    """由证据组装一份报告（标识固定，因此"只改证据里的某一项"就能对着摘要看差异）。"""
    from dataclasses import replace

    from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy

    fixed = [replace(item, evidence_digest=evidence_digest_of(item)) for item in evidences]
    cases = tuple(
        CaseDescriptor(case_id=item.case_id, ordinal=item.ordinal, weight=item.weight)
        for item in fixed
    )
    breakdown = score_run(
        cases=cases,
        evidence=fixed,
        static=_static(),
        security=_security(),
        comparison=ComparisonPolicy(stdout="exact"),
    )
    return build_report(
        ReportInputs(
            evaluation_run_id=_RUN_ID,
            submission_id=_SUBMISSION_ID,
            question_version_id=_QUESTION_ID,
            static=_static(),
            security=_security(),
            evidence=fixed,
            breakdown=breakdown,
            binding=ReportBinding(
                submission_digest=_digest("submission"),
                package_digest=_digest("package"),
                runner_policy_digest=_digest("policy"),
                environment_digest=_digest("environment"),
            ),
        ),
        generated_at=BASE,
    )


def _static() -> StaticAnalysisResult:
    return StaticAnalysisResult(
        language="python",
        checks=(),
        findings=(),
        blocking=False,
        error_count=0,
        warning_count=0,
        info_count=0,
        result_digest=_digest("static"),
    )


def _security() -> SecurityDecision:
    return SecurityDecision(
        status="not_found",
        findings=(),
        blocking=False,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="未发现违规模式。",
    )


def _facts(*, stdout: str, exit_code: int | None) -> RunFacts:
    return RunFacts(
        exit_code=exit_code,
        timed_out=False,
        stdout_text=stdout,
        stderr_text="",
        sandbox_log_text="",
        stdout_truncated=False,
        stderr_truncated=False,
        stdout_comparison=None,
        stderr_comparison=None,
        fixture_outcomes=(),
        limit_exceeded_files=False,
        tree_still_alive=False,
        kill_detail=None,
    )


def _probe():  # type: ignore[no-untyped-def]
    from autograder_b2.canary import _canary_probes

    return _canary_probes(RunnerPolicy())[0]


def test_score_breakdown_of_the_report_fixture_is_consistent() -> None:
    """夹具自检：报告真的产出且合计校验成立（否则上面的断言可能是在比较两份空报告）。"""
    report = _report([_evidence()])
    assert report["total_check"] == {"sum_of_awards": 10.0, "matches_total": True}
    assert json.dumps(report, ensure_ascii=False)
    assert report["overall"]["score"] == 10.0
    assert Decimal(str(report["overall"]["score"])) == Decimal(10)
