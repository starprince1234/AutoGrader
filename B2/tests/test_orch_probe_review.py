"""独立复核探针：B2「评测编排与状态迁移」三条验收标准（**作者是复核者，不是实现者**）。

本文件不复用 `tests/test_evaluation_pipeline.py` 的任何夹具/替身，自行装配最小流水线，
以便独立证伪。三条标准的**原文**（不得软化）：

1. 状态机测试：非法迁移被拒；静态阻断路径为 validated→rejected 且迁移史无 running 记录。
2. SSE 测试：帧 sequence 严格单调、含 progress 与终态 rejected/failed，断线重连按 sequence 去重不重复。
3. 并发测试：并发提交时动态评测并发峰值不超过 1。

纪律：不修改 `src/**` 与既有测试文件；探针只读实现、只写字库探针自己造的数据。
并发观测**不依赖** `DynamicExecutionGate` 自己的计数器（外部计数器独立测量 runner 的重叠）。
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from sqlalchemy import Engine, text

from autograder_b2.db.schema import submissions
from autograder_b2.domain import states
from autograder_b2.domain.envelope import ApiProvenance, EnvelopeFile, SubmissionEnvelope
from autograder_b2.domain.evidence import CaseEvidence, SandboxUnavailableError, SubmissionRunInput
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.domain.transitions import record_transitions as record_submission_transitions
from autograder_b2.evaluation.orchestrator import (
    DYNAMIC_CONCURRENCY_LIMIT,
    DynamicExecutionGate,
    EvaluationOrchestrator,
)
from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy, PackageView
from autograder_b2.evaluation.progress import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_PROGRESS,
    EVENT_REJECTED,
    TERMINAL_EVENTS,
    TerminalFrameExists,
    append_frame,
    frame_stream,
    read_frames,
    terminal_frame,
)
from autograder_b2.evaluation.store import (
    RUN_TRANSITIONS,
    IllegalRunTransition,
    create_run,
    require_run_transition,
    run_history,
    transition_run,
)
from tests.conftest import requires_postgres

pytestmark = requires_postgres

BASE = datetime(2026, 9, 16, 12, 0, 0, 100_000, tzinfo=UTC)

#: 命中阻断类静态规则（STAT-PY-030：命令交给 shell 解释执行）的源码——**无害样本**（命令是 echo）。
BLOCKING_SOURCE = 'import subprocess\n\nsubprocess.run("echo probe", shell=True, check=False)\n'
CLEAN_SOURCE = 'print("probe")\n'


def say(*parts: object) -> None:
    print("[PROBE]", *parts, flush=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


DIGEST_SUB = _digest("probe-submission")
DIGEST_PKG = _digest("probe-package")
DIGEST_POLICY = _digest("probe-policy")
DIGEST_ENV = _digest("probe-env")


# --- 我自己的夹具（不复用实现者的） ----------------------------------------------------------


def _seed_submission(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """写一条 `intake='api'` 的合规提交行（迁移 0008 形状）。"""
    sid = uuid.uuid4()
    qid = uuid.uuid4()
    correlation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"probe:{sid}"))
    with engine.begin() as connection:
        connection.execute(
            submissions.insert().values(
                submission_id=sid,
                student_id=uuid.uuid4(),
                course_id=uuid.uuid4(),
                section_id=uuid.uuid4(),
                question_version_id=qid,
                intake="api",
                idempotency_key=f"probe-{sid}",
                idempotency_scope="orch-probe-review",
                submission_digest=DIGEST_SUB,
                state="validated",
                created_at=BASE,
                updated_at=BASE,
                correlation_id=uuid.UUID(correlation),
                request_id=correlation,
            )
        )
        # 与接收层（intake/service.py:303）写下的**同两条**迁移：提交状态机的真实起点。
        record_submission_transitions(
            connection,
            submission_id=str(sid),
            transitions=((None, states.RECEIVED, None), (states.RECEIVED, states.VALIDATED, "probe")),
            occurred_at=BASE,
        )
    return sid, qid


def _envelope(sid: uuid.UUID, qid: uuid.UUID, contents: Mapping[str, bytes]) -> SubmissionEnvelope:
    files = tuple(
        EnvelopeFile(
            path=path,
            content_sha256=hashlib.sha256(blob).hexdigest(),
            size_bytes=len(blob),
            storage_ref=f"sha256/{hashlib.sha256(blob).hexdigest()}",
        )
        for path, blob in contents.items()
    )
    correlation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"probe:{sid}"))
    return SubmissionEnvelope(
        submission_id=str(sid),
        student_id=str(uuid.uuid4()),
        course_id=str(uuid.uuid4()),
        section_id=str(uuid.uuid4()),
        question_version_id=str(qid),
        idempotency_key=f"probe-key-{sid}",
        intake="api",
        provenance=ApiProvenance(request_id=correlation),
        correlation_id=correlation,
        files=files,
        submission_digest=DIGEST_SUB,
        received_at=BASE,
    )


@dataclass(frozen=True, slots=True)
class _Handle:
    case_id: str
    ordinal: int
    weight: float
    visible_to_student: bool = False


def _package(cases: Sequence[tuple[str, int, float]]) -> PackageView:
    descriptors = tuple(
        CaseDescriptor(
            case_id=case_id,
            ordinal=ordinal,
            weight=weight,
            handle=_Handle(case_id=case_id, ordinal=ordinal, weight=weight),
        )
        for case_id, ordinal, weight in cases
    )
    return PackageView(
        package_digest=DIGEST_PKG,
        language="python",
        cases=descriptors,
        runner_policy_digest=DIGEST_POLICY,
        environment_digest=DIGEST_ENV,
        comparison=ComparisonPolicy(stdout="exact"),
        handle={"package": "probe"},
        adapter_handle={"adapter": "probe"},
        policy_handle={"policy": "probe"},
    )


class _PackageSource:
    def __init__(self, view: PackageView) -> None:
        self._view = view

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        return self._view


class _Workspaces:
    """把提交内容物化到真实目录（静态检查读真实文件）；每用例一份独立目录。

    `prepare_delay` 是复核者的**仪表**：给工作区物化加可观测耗时，用来测"闸门是否覆盖整个动态阶段"。
    """

    def __init__(self, root: Path, payload: Mapping[str, bytes], *, prepare_delay: float = 0.0) -> None:
        self._root = root
        self._payload = dict(payload)
        self._root.mkdir(parents=True, exist_ok=True)
        self._prepare_delay = prepare_delay
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.calls = 0

    def snapshot(self) -> tuple[int, int, int]:
        with self._lock:
            return self.live, self.peak, self.calls

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
    ) -> Path:
        with self._lock:
            self.live += 1
            self.calls += 1
            self.peak = max(self.peak, self.live)
        try:
            if self._prepare_delay:
                time.sleep(self._prepare_delay)
            directory = (
                self._root / f"{evaluation_run_id[:8]}-{case_id or 'run'}-{uuid.uuid4().hex[:6]}"
            )
            directory.mkdir(parents=True)
            for path, blob in self._payload.items():
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
            return directory
        finally:
            with self._lock:
                self.live -= 1

    def release(self, workspace: Path) -> None:
        shutil.rmtree(workspace, ignore_errors=True)


class _Security:
    def decide(self, *, evaluation_run_id: str, files: Mapping[str, str], language: str):
        return SecurityDecision(
            status="not_found",
            findings=(),
            blocking=False,
            covered=("python_ast", "python_text"),
            unchecked=(),
            rationale="probe: 放行",
        )


class _ObservingRunner:
    """**独立观测器**：自己数重叠中的 runner 调用（不看闸门计数器）。"""

    def __init__(self, *, delay: float = 0.0, failure: Exception | None = None) -> None:
        self.delay = delay
        self.failure = failure
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.calls = 0

    def snapshot(self) -> tuple[int, int, int]:
        with self._lock:
            return self.live, self.peak, self.calls

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        case = submission.case
        case_id = str(case.case_id)  # type: ignore[attr-defined]
        with self._lock:
            self.live += 1
            self.calls += 1
            self.peak = max(self.peak, self.live)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.failure is not None:
                raise self.failure
            return CaseEvidence(
                case_id=case_id,
                ordinal=int(case.ordinal),  # type: ignore[attr-defined]
                weight=float(case.weight),  # type: ignore[attr-defined]
                passed=True,
                failure_category=None,
                input_digest=_digest(f"in:{case_id}"),
                stdout_digest=_digest("out"),
                stderr_digest=_digest(""),
                actual_output_excerpt="probe",
                expected_output_excerpt="probe",
                exit_code=0,
                duration_ms=1,
                resource_limit_hit=None,
                sandbox_violations=(),
                message=None,
                evidence_digest=_digest(f"ev:{case_id}"),
            )
        finally:
            with self._lock:
                self.live -= 1


def _orchestrator(
    engine: Engine,
    tmp_path: Path,
    *,
    contents: Mapping[str, bytes],
    cases: Sequence[tuple[str, int, float]],
    runner: _ObservingRunner,
    gate: DynamicExecutionGate | None = None,
    provider: _Workspaces | None = None,
) -> EvaluationOrchestrator:
    return EvaluationOrchestrator(
        engine=engine,
        package_source=_PackageSource(_package(cases)),
        workspace_provider=provider
        or _Workspaces(tmp_path / f"ws-{uuid.uuid4().hex[:8]}", contents),
        security=_Security(),
        runner=runner,
        gate=gate,
    )


def _raw_history_pairs(engine: Engine, run_id: str) -> list[tuple[str | None, str]]:
    """**原始 SQL** 读运行迁移史（不经过被测读取函数）。"""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT from_state, to_state FROM b2.evaluation_run_state_transitions"
                " WHERE evaluation_run_id = :rid ORDER BY sequence"
            ),
            {"rid": uuid.UUID(run_id)},
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _run_state_row(engine: Engine, run_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT state, stage, finished_at, terminal_reason, started_at"
                " FROM b2.evaluation_runs WHERE evaluation_run_id = :rid"
            ),
            {"rid": uuid.UUID(run_id)},
        ).fetchone()
    return dict(zip(("state", "stage", "finished_at", "terminal_reason", "started_at"), row))


def _submission_facts(engine: Engine, sid: uuid.UUID) -> tuple[str, list[tuple[str | None, str]]]:
    with engine.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM b2.submissions WHERE submission_id = :sid"), {"sid": sid}
        ).scalar_one()
        rows = connection.execute(
            text(
                "SELECT from_state, to_state FROM b2.submission_state_transitions"
                " WHERE submission_id = :sid ORDER BY sequence"
            ),
            {"sid": sid},
        ).fetchall()
    return str(state), [(row[0], row[1]) for row in rows]


def _frame_rows(engine: Engine, run_id: str) -> list[tuple[int, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT sequence, event FROM b2.progress_events"
                " WHERE evaluation_run_id = :rid ORDER BY sequence"
            ),
            {"rid": uuid.UUID(run_id)},
        ).fetchall()
    return [(int(row[0]), str(row[1])) for row in rows]


def _terminal_frame_count(engine: Engine, run_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM b2.progress_events WHERE evaluation_run_id = :rid"
                    " AND event <> 'progress'"
                ),
                {"rid": uuid.UUID(run_id)},
            ).scalar_one()
        )


def _transition_row_count(engine: Engine, run_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM b2.evaluation_run_state_transitions"
                    " WHERE evaluation_run_id = :rid"
                ),
                {"rid": uuid.UUID(run_id)},
            ).scalar_one()
        )


# ============================================================================================
# 标准 1
# ============================================================================================


def test_probe1_state_machine_and_static_block_path(engine: Engine, tmp_path: Path) -> None:
    """标准 1：非法迁移被拒（写入前）；静态阻断路径 validated→rejected；迁移史无 running。"""
    say("=" * 96)
    say("标准1 RUN_TRANSITIONS =", dict(RUN_TRANSITIONS))
    say("标准1 运行状态机里是否存在 'validated' 状态 =", "validated" in RUN_TRANSITIONS)
    say("标准1 SUBMISSION_TRANSITIONS =", dict(states.SUBMISSION_TRANSITIONS))
    submission_layer: list[str] = []
    for frm, to in (("validated", "rejected"), ("validated", "running"), ("validated", "queued")):
        try:
            states.require_transition(frm, to)
            submission_layer.append(f"{frm}->{to}:ALLOWED")
        except states.IllegalTransition:
            submission_layer.append(f"{frm}->{to}:IllegalTransition")
    say("标准1 提交状态机校验器 =", submission_layer)

    # --- (a) 非法迁移在写入前被拒：before/after 行数 ---------------------------------
    sid, qid = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(sid),
            question_version_id=str(qid),
            submission_digest=DIGEST_SUB,
            occurred_at=BASE,
        )
        transition_run(connection, evaluation_run_id=run_id, to_state="running", occurred_at=BASE)
    before_count = _transition_row_count(engine, run_id)
    before_row = _run_state_row(engine, run_id)
    with engine.connect() as connection:
        reader_pairs = [(item.from_state, item.to_state) for item in run_history(connection, run_id)]
    raw_pairs = _raw_history_pairs(engine, run_id)
    say(f"标准1(a) 起点: 行数={before_count} 行={before_row} 史(raw)={raw_pairs} 史(reader)={reader_pairs}")
    assert reader_pairs == raw_pairs == [(None, "queued"), ("queued", "running")]

    attempts: list[str] = []
    for to_state, terminal_reason in (
        ("queued", None),
        ("running", None),
        ("rejected", None),  # 终态原因缺失：验证"校验顺序"是否让 IllegalRunTransition 缺席
    ):
        try:
            with engine.begin() as connection:
                transition_run(
                    connection,
                    evaluation_run_id=run_id,
                    to_state=to_state,
                    occurred_at=BASE,
                    terminal_reason=terminal_reason,
                )
            attempts.append(f"running->{to_state}:NO-RAISE")
        except IllegalRunTransition as error:
            attempts.append(f"running->{to_state}:IllegalRunTransition({error})")
        except Exception as error:  # noqa: BLE001 - 如实记录实际异常类型
            attempts.append(f"running->{to_state}:{type(error).__name__}({error})")
    after_count = _transition_row_count(engine, run_id)
    after_row = _run_state_row(engine, run_id)
    say("标准1(a) running 上的非法尝试 =", attempts)
    say(f"标准1(a) 行数 before={before_count} after={after_count} 行={after_row}")
    assert after_count == before_count == 2
    assert after_row == before_row

    # 终态出发的非法迁移
    with engine.begin() as connection:
        transition_run(
            connection,
            evaluation_run_id=run_id,
            to_state="completed",
            occurred_at=BASE,
            terminal_reason=None,
        )
    completed_count = _transition_row_count(engine, run_id)
    try:
        with engine.begin() as connection:
            transition_run(connection, evaluation_run_id=run_id, to_state="running", occurred_at=BASE)
        back_attempt = "completed->running:NO-RAISE"
    except IllegalRunTransition as error:
        back_attempt = f"completed->running:IllegalRunTransition({error})"
    completed_after = _transition_row_count(engine, run_id)
    say(f"标准1(a) {back_attempt}")
    say(
        f"标准1(a) 终态回退 行数 before={completed_count} after={completed_after}"
        f" 行={_run_state_row(engine, run_id)}"
    )
    assert completed_count == completed_after == 3

    pure = []
    for frm, to in (("completed", "running"), ("rejected", "queued"), ("failed", "running")):
        try:
            require_run_transition(frm, to)
            pure.append(f"{frm}->{to}:NO-RAISE")
        except IllegalRunTransition:
            pure.append(f"{frm}->{to}:IllegalRunTransition")
    say("标准1(a) 纯校验器 =", pure)
    assert all(item.endswith("IllegalRunTransition") for item in pure)

    # --- (b) 静态阻断：真实静态规则（STAT-PY-030）命中后走编排 ------------------------
    sid2, qid2 = _seed_submission(engine)
    contents = {"main.py": BLOCKING_SOURCE.encode("utf-8")}
    runner = _ObservingRunner()
    orchestrator = _orchestrator(
        engine, tmp_path, contents=contents, cases=[(str(uuid.uuid4()), 0, 5.0)], runner=runner
    )
    sub_before = _submission_facts(engine, sid2)
    outcome = orchestrator.run(_envelope(sid2, qid2, contents))
    run_pairs = _raw_history_pairs(engine, outcome.evaluation_run_id)
    run_row = _run_state_row(engine, outcome.evaluation_run_id)
    sub_state, sub_pairs = _submission_facts(engine, sid2)
    frames = _frame_rows(engine, outcome.evaluation_run_id)

    say("标准1(b) outcome =", outcome.state, outcome.terminal_reason, "| runner 调用 =", runner.calls)
    say("标准1(b) 运行迁移史(raw) =", run_pairs)
    say("标准1(b) 运行行 =", run_row)
    say("标准1(b) 帧 =", frames)
    say("标准1(b) 提交**静态阻断前** (state, 史) =", sub_before)
    say("标准1(b) 提交**静态阻断后** (state, 史) =", (sub_state, sub_pairs))
    say("标准1(b) 提交状态/史是否发生任何变化 =", sub_before != (sub_state, sub_pairs))
    say("标准1(b) 运行迁移史含 running =", any(to == "running" for _, to in run_pairs))
    say("标准1(b) 运行迁移史含 (validated,rejected) =", ("validated", "rejected") in run_pairs)
    say("标准1(b) 提交迁移史含 (validated,rejected) =", ("validated", "rejected") in sub_pairs)
    say("标准1(b) 实测静态阻断路径 =", " -> ".join(f"{a}->{b}" for a, b in run_pairs))
    say(
        "标准1(b) 裁决: validated->rejected 在**任何**迁移史中是否出现 =",
        ("validated", "rejected") in run_pairs or ("validated", "rejected") in sub_pairs,
    )

    assert outcome.state == "rejected" and outcome.terminal_reason == "static_blocked"
    assert runner.calls == 0
    assert "running" not in [to for _, to in run_pairs]
    # 修复后（本条原记录的是**缺陷**）：提交状态机被真正推进，且静态阻断落在提交上。
    # 契约 `SubmissionTransitions`："静态高危必须在不进入沙箱的情况下被拒" ⇒ `validated -> rejected`，
    # 且提交**不得**经过 `queued`/`running`。
    assert sub_state == "rejected", sub_state
    assert ("validated", "rejected") in sub_pairs, sub_pairs
    assert sub_before != (sub_state, sub_pairs), "编排层必须写入提交状态与迁移史"
    assert all(to not in {"queued", "running"} for _, to in sub_pairs), sub_pairs

    # --- (c) 成功路径同样走完提交状态机（原缺陷的另一面） ----------------------------
    sid3, qid3 = _seed_submission(engine)
    ok_contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    ok_orchestrator = _orchestrator(
        engine, tmp_path, contents=ok_contents, cases=[(str(uuid.uuid4()), 0, 5.0)],
        runner=_ObservingRunner(),
    )
    ok_outcome = ok_orchestrator.run(_envelope(sid3, qid3, ok_contents))
    ok_run_pairs = _raw_history_pairs(engine, ok_outcome.evaluation_run_id)
    ok_sub_state, ok_sub_pairs = _submission_facts(engine, sid3)
    say("标准1(c) 成功路径 outcome =", ok_outcome.state, "| 运行迁移史 =", ok_run_pairs)
    say("标准1(c) 成功路径 提交 (state, 史) =", (ok_sub_state, ok_sub_pairs))
    assert ok_outcome.state == "completed"
    # 修复后：提交走完 `validated -> queued -> running -> completed`。
    assert ok_sub_state == "completed", ok_sub_state
    assert [to for _, to in ok_sub_pairs][-3:] == ["queued", "running", "completed"], ok_sub_pairs

    # --- (d) 提交级批量迁移的"逐行校验 vs 批量校验"脆弱点 ---------------------------
    sid4, _qid4 = _seed_submission(engine)
    crashed = ""
    try:
        with engine.begin() as connection:
            try:
                record_submission_transitions(
                    connection,
                    submission_id=str(sid4),
                    transitions=((None, states.RECEIVED, None), ("received", "running", "非法")),
                    occurred_at=BASE,
                )
            except states.IllegalTransition as error:
                crashed = f"IllegalTransition({error})"
                # 调用方**吞掉**异常后事务照常提交：观察第一行是否已经落库
    except Exception as error:  # noqa: BLE001
        crashed = f"外层 {type(error).__name__}: {error}"
    say("标准1(d) 批量迁移第 2 行非法 →", crashed)
    say("标准1(d) 该提交行/史 =", _submission_facts(engine, sid4))
    say("标准1 探针结束")
    say("=" * 96)


# ============================================================================================
# 标准 2
# ============================================================================================


def _run_blocked(engine: Engine, tmp_path: Path) -> str:
    sid, qid = _seed_submission(engine)
    contents = {"main.py": BLOCKING_SOURCE.encode("utf-8")}
    orchestrator = _orchestrator(
        engine, tmp_path, contents=contents, cases=[(str(uuid.uuid4()), 0, 5.0)],
        runner=_ObservingRunner(),
    )
    return orchestrator.run(_envelope(sid, qid, contents)).evaluation_run_id


def _run_sandbox_down(engine: Engine, tmp_path: Path) -> str:
    sid, qid = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    orchestrator = _orchestrator(
        engine,
        tmp_path,
        contents=contents,
        cases=[(str(uuid.uuid4()), 0, 5.0), (str(uuid.uuid4()), 1, 5.0)],
        runner=_ObservingRunner(failure=SandboxUnavailableError("probe: 沙箱不可用")),
    )
    return orchestrator.run(_envelope(sid, qid, contents)).evaluation_run_id


def test_probe2_frames_monotonic_terminal_and_reconnect(engine: Engine, tmp_path: Path) -> None:
    """标准 2：严格单调 + 含 progress 与终态 rejected/failed + 断线重连不重不漏 + 终态帧唯一。"""
    say("=" * 96)
    targets = (
        ("rejected 路径", _run_blocked(engine, tmp_path)),
        ("failed 路径", _run_sandbox_down(engine, tmp_path)),
    )
    for label, run_id in targets:
        rows = _frame_rows(engine, run_id)
        sequences = [row[0] for row in rows]
        events = [row[1] for row in rows]
        say(f"标准2[{label}] 帧(sequence,event) =", rows)
        strictly_monotonic = all(b > a for a, b in pairwise(sequences))
        terminal_events = [event for event in events if event in TERMINAL_EVENTS]
        say(
            f"标准2[{label}] 严格单调={strictly_monotonic} 连续={sequences == list(range(1, len(sequences) + 1))}"
            f" 含 progress={EVENT_PROGRESS in events} 终态帧={terminal_events} 个数={len(terminal_events)}"
        )
        assert strictly_monotonic
        assert EVENT_PROGRESS in events
        assert len(terminal_events) == 1
        assert terminal_events[0] in (EVENT_REJECTED, EVENT_FAILED)

        with engine.connect() as connection:
            all_frames = read_frames(connection, evaluation_run_id=run_id)
            visible = terminal_frame(connection, run_id)
        full = [frame.sequence for frame in all_frames]
        assert full == sequences
        assert visible is not None and visible.sequence == full[-1]

        # 断线重连（按 sequence 去重不重复）：每个 pivot 切成 前缀 + 后缀 = 全集
        for pivot in range(len(full) + 1):
            with engine.connect() as connection:
                head = (
                    []
                    if pivot == 0
                    else read_frames(connection, evaluation_run_id=run_id, after=0, limit=pivot)
                )
                tail = read_frames(connection, evaluation_run_id=run_id, after=pivot)
            joint = [frame.sequence for frame in head] + [frame.sequence for frame in tail]
            assert joint == full, (label, pivot, joint, full)
            assert len(set(joint)) == len(joint)
        say(f"标准2[{label}] read_frames 前缀+后缀 对 pivot=0..{len(full)} 均等于全集（不重不漏）")

        # 真实断线：frame_stream 读到一半即断开（生成器 close），带最后 sequence 续读
        seen: list[int] = []
        stream = frame_stream(
            engine=engine, evaluation_run_id=run_id, after=0, poll_interval=0.01, idle_timeout=0.05
        )
        try:
            for frame in stream:
                seen.append(frame.sequence)
                if len(seen) == 2:
                    break
        finally:
            stream.close()
        resumed = [
            frame.sequence
            for frame in frame_stream(
                engine=engine,
                evaluation_run_id=run_id,
                after=seen[-1],
                poll_interval=0.01,
                idle_timeout=0.05,
            )
        ]
        say(f"标准2[{label}] 断开点={seen} 续读={resumed} 拼接={seen + resumed} 全集={full}")
        assert seen + resumed == full
        assert len(set(seen + resumed)) == len(seen + resumed)

        # 终态帧唯一：第二个终态帧必须被拒，库里终态帧个数不增加
        before_terminal = _terminal_frame_count(engine, run_id)
        attempts: list[str] = []
        for event, reason in (
            (EVENT_FAILED, "internal_error"),
            (EVENT_REJECTED, "static_blocked"),
            (EVENT_COMPLETED, None),
        ):
            try:
                with engine.begin() as connection:
                    append_frame(
                        connection,
                        evaluation_run_id=run_id,
                        event=event,
                        state="completed" if event == EVENT_COMPLETED else "failed",
                        stage="terminal",
                        occurred_at=BASE,
                        terminal_reason=reason,
                        score=1 if event == EVENT_COMPLETED else None,
                        is_pass=True if event == EVENT_COMPLETED else None,
                    )
                attempts.append(f"{event}:NO-RAISE")
            except TerminalFrameExists:
                attempts.append(f"{event}:TerminalFrameExists")
        after_terminal = _terminal_frame_count(engine, run_id)
        say(f"标准2[{label}] 第二个终态帧尝试 = {attempts}")
        say(f"标准2[{label}] 终态帧个数 before={before_terminal} after={after_terminal}")
        assert all(item.endswith("TerminalFrameExists") for item in attempts)
        assert before_terminal == after_terminal == 1
    say("标准2 探针结束")
    say("=" * 96)


# ============================================================================================
# 标准 3
# ============================================================================================


def test_probe3_concurrency_peak(engine: Engine, tmp_path: Path) -> None:
    """标准 3：并发提交时动态评测并发峰值 ≤ 1，附"观测手段有效"的正向对照。"""
    say("=" * 96)
    say("标准3 DYNAMIC_CONCURRENCY_LIMIT =", DYNAMIC_CONCURRENCY_LIMIT)

    # --- (A) 观测手段有效性对照：直接对闸门做 A/B（不改仓库源码） -------------------
    for capacity in (1, 3):
        gate = DynamicExecutionGate(capacity=capacity)
        lock = threading.Lock()
        external = {"live": 0, "peak": 0}
        barrier = threading.Barrier(6)

        def holder(
            dynamic_gate: DynamicExecutionGate = gate,
            counter_lock: threading.Lock = lock,
            counter: dict[str, int] = external,
            start: threading.Barrier = barrier,
        ) -> None:
            start.wait(timeout=10)
            with dynamic_gate.hold():
                with counter_lock:
                    counter["live"] += 1
                    counter["peak"] = max(counter["peak"], counter["live"])
                time.sleep(0.08)
                with counter_lock:
                    counter["live"] -= 1

        threads = [threading.Thread(target=holder) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        say(
            f"标准3(A) 闸门容量={capacity}: 外部观测峰值={external['peak']}"
            f"（闸门自报 peak={gate.peak}, acquisitions={gate.acquisitions}）"
        )
        if capacity == 1:
            assert external["peak"] == 1
        else:
            assert external["peak"] > 1  # 正向对照：观测手段确实能测到 > 1

    # --- (B)(C)(D) 并发提交（编排器级） + 外部观测 ---------------------------------
    def drive(
        *,
        gate: DynamicExecutionGate | None,
        shared: bool,
        workers: int,
        cases_per_run: int,
        delay: float,
        prepare_delay: float = 0.0,
    ) -> tuple[int, int, int, int, int, float, tuple[int, int, int]]:
        runner = _ObservingRunner(delay=delay)
        contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
        cases = [(str(uuid.uuid4()), index, 1.0) for index in range(cases_per_run)]
        provider = _Workspaces(
            tmp_path / f"ws-{uuid.uuid4().hex[:8]}", contents, prepare_delay=prepare_delay
        )
        root_orchestrator = _orchestrator(
            engine, tmp_path, contents=contents, cases=cases, runner=runner, gate=gate,
            provider=provider,
        )
        capacity = root_orchestrator.gate.capacity
        envelopes = [_envelope(*_seed_submission(engine), contents) for _ in range(workers)]
        barrier = threading.Barrier(workers)
        errors: list[BaseException] = []
        started = time.monotonic()

        def worker(index: int) -> None:
            try:
                barrier.wait(timeout=20)
                target = root_orchestrator if shared else _orchestrator(
                    engine, tmp_path, contents=contents, cases=cases, runner=runner
                )
                target.run(envelopes[index])
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        elapsed = time.monotonic() - started
        assert errors == [], errors
        live, peak, calls = runner.snapshot()
        return live, peak, calls, capacity, root_orchestrator.gate.peak, elapsed, provider.snapshot()

    live, peak, calls, capacity, self_peak, elapsed, ws = drive(
        gate=DynamicExecutionGate(), shared=True, workers=4, cases_per_run=2, delay=0.08,
        prepare_delay=0.05,
    )
    say(
        f"标准3(B) 共享闸门(容量={capacity}) 4 并发提交×2 用例={calls} 次 runner 调用:"
        f" 外部观测峰值={peak} 闸门自报 peak={self_peak} 残留 live={live}"
        f" 墙钟={elapsed:.2f}s（串行下界 8×0.08=0.64s）"
    )
    say(
        f"标准3(B') 同一批并发下**工作区物化**（闸门之外，prepare 注入 50ms 仪表）:"
        f" 调用={ws[2]} 外部观测峰值={ws[1]}"
    )
    assert calls == 8
    assert peak <= 1 and peak == 1
    assert elapsed >= 0.6

    _live_c, peak_c, calls_c, capacity_c, self_peak_c, elapsed_c, _ws_c = drive(
        gate=DynamicExecutionGate(capacity=3), shared=True, workers=4, cases_per_run=2, delay=0.08
    )
    say(
        f"标准3(C) 对照 容量={capacity_c} 同样并发压力={calls_c} 次 runner 调用:"
        f" 外部观测峰值={peak_c} 闸门自报 peak={self_peak_c} 墙钟={elapsed_c:.2f}s"
    )
    assert peak_c > 1

    _live_d, peak_d, calls_d, capacity_d, _self_peak_d, elapsed_d, _ws_d = drive(
        gate=None, shared=False, workers=2, cases_per_run=2, delay=0.08
    )
    say(
        f"标准3(D) 两个编排器各自默认闸门(gate=None, 每实例容量={capacity_d}):"
        f" {calls_d} 次 runner 调用: 外部观测峰值={peak_d} 墙钟={elapsed_d:.2f}s"
    )
    say("标准3 探针结束")
    say("=" * 96)
