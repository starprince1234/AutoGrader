"""复核者的**再裁决**探针：B2「评测编排与状态迁移」标准 1（独立于实现者测试，也独立于被改过的前一探针）。

前一探针 `tests/test_orch_probe_review.py` 已被实现者改写（把"钉住缺陷"的断言改成"钉住修复"），
因此本文件**自带全部夹具**重新独立取证；不读实现者的测试，也不复用其断言。

覆盖：
① 静态阻断 → 提交 `rejected`、提交史含 `(validated,rejected)`、**不含** `queued`/`running`（且运行史无 running）；
② 成功路径 → 提交走完 `validated→queued→running→completed`（逐条核对 from/to 链接与合法性）；
③ `advance_submission_state` 非法迁移**零写入**（终态出发、以及运行前技术失败时不得写 `validated→failed`）；
④ 批量迁移第 2 行非法 → **一行都不写**；
⑤ 标准 2 / 3 在本次改动后是否仍然成立（用我自己的仪器重测，不引用实现者断言）。
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
    DynamicExecutionGate,
    EvaluationOrchestrator,
)
from autograder_b2.evaluation.ports import CaseDescriptor, ComparisonPolicy, PackageView
from autograder_b2.evaluation.progress import (
    EVENT_PROGRESS,
    TERMINAL_EVENTS,
    frame_stream,
    read_frames,
)
from autograder_b2.evaluation.store import advance_submission_state
from tests.conftest import requires_postgres

pytestmark = requires_postgres

BASE = datetime(2026, 9, 16, 14, 0, 0, 100_000, tzinfo=UTC)
#: 命中阻断类静态规则 STAT-PY-030（命令交给 shell 解释）的**无害**源码。
BLOCKING_SOURCE = 'import subprocess\n\nsubprocess.run("echo probe", shell=True, check=False)\n'
CLEAN_SOURCE = 'print("probe")\n'


def say(*parts: object) -> None:
    print("[RECHECK]", *parts, flush=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


DIGEST_SUB = _digest("recheck-submission")


# --- 夹具（自带，不用实现者的） ---------------------------------------------------------------


def _seed_submission(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """忠实反映 intake 的产物：`state='validated'` + 迁移史 `(None,received),(received,validated)`。"""
    sid = uuid.uuid4()
    qid = uuid.uuid4()
    correlation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"recheck:{sid}"))
    with engine.begin() as connection:
        connection.execute(
            submissions.insert().values(
                submission_id=sid,
                student_id=uuid.uuid4(),
                course_id=uuid.uuid4(),
                section_id=uuid.uuid4(),
                question_version_id=qid,
                intake="api",
                idempotency_key=f"recheck-{sid}",
                idempotency_scope="orch-recheck",
                submission_digest=DIGEST_SUB,
                state="validated",
                created_at=BASE,
                updated_at=BASE,
                correlation_id=uuid.UUID(correlation),
                request_id=correlation,
            )
        )
        record_submission_transitions(
            connection,
            submission_id=str(sid),
            transitions=((None, "received", None), ("received", "validated", "envelope accepted")),
            occurred_at=BASE,
        )
    return sid, qid


def _envelope(sid: uuid.UUID, qid: uuid.UUID, contents: Mapping[str, bytes]) -> SubmissionEnvelope:
    correlation = str(uuid.uuid5(uuid.NAMESPACE_URL, f"recheck:{sid}"))
    return SubmissionEnvelope(
        submission_id=str(sid),
        student_id=str(uuid.uuid4()),
        course_id=str(uuid.uuid4()),
        section_id=str(uuid.uuid4()),
        question_version_id=str(qid),
        idempotency_key=f"recheck-key-{sid}",
        intake="api",
        provenance=ApiProvenance(request_id=correlation),
        correlation_id=correlation,
        files=tuple(
            EnvelopeFile(
                path=path,
                content_sha256=hashlib.sha256(blob).hexdigest(),
                size_bytes=len(blob),
                storage_ref=f"sha256/{hashlib.sha256(blob).hexdigest()}",
            )
            for path, blob in contents.items()
        ),
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
    return PackageView(
        package_digest=_digest("pkg"),
        language="python",
        cases=tuple(
            CaseDescriptor(
                case_id=case_id,
                ordinal=ordinal,
                weight=weight,
                handle=_Handle(case_id=case_id, ordinal=ordinal, weight=weight),
            )
            for case_id, ordinal, weight in cases
        ),
        runner_policy_digest=_digest("policy"),
        environment_digest=_digest("env"),
        comparison=ComparisonPolicy(stdout="exact"),
        handle={"package": "probe"},
        adapter_handle={"adapter": "probe"},
        policy_handle={"policy": "probe"},
    )


class _PackageSource:
    def __init__(self, view: PackageView, *, error: Exception | None = None) -> None:
        self._view = view
        self._error = error

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        if self._error is not None:
            raise self._error
        return self._view


class _Workspaces:
    def __init__(self, root: Path, payload: Mapping[str, bytes]) -> None:
        self._root = root
        self._payload = dict(payload)
        self._root.mkdir(parents=True, exist_ok=True)

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
    ) -> Path:
        directory = self._root / f"{evaluation_run_id[:8]}-{case_id or 'run'}-{uuid.uuid4().hex[:6]}"
        directory.mkdir(parents=True)
        for path, blob in self._payload.items():
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        return directory

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
    """外部并发观测器（不看闸门计数器）。"""

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
        case_id = str(submission.case.case_id)  # type: ignore[attr-defined]
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
                ordinal=int(submission.case.ordinal),  # type: ignore[attr-defined]
                weight=float(submission.case.weight),  # type: ignore[attr-defined]
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
    package_error: Exception | None = None,
) -> EvaluationOrchestrator:
    return EvaluationOrchestrator(
        engine=engine,
        package_source=_PackageSource(_package(cases), error=package_error),
        workspace_provider=_Workspaces(tmp_path / f"ws-{uuid.uuid4().hex[:8]}", contents),
        security=_Security(),
        runner=runner,
        gate=gate,
    )


# --- 原始 SQL 读取（不经过被测读取函数） ------------------------------------------------------


def _sub_state(engine: Engine, sid: uuid.UUID) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT state FROM b2.submissions WHERE submission_id = :sid"), {"sid": sid}
            ).scalar_one()
        )


def _sub_history(engine: Engine, sid: uuid.UUID) -> list[tuple[str | None, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT from_state, to_state FROM b2.submission_state_transitions"
                " WHERE submission_id = :sid ORDER BY sequence"
            ),
            {"sid": sid},
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _sub_history_count(engine: Engine, sid: uuid.UUID) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT count(*) FROM b2.submission_state_transitions WHERE submission_id = :sid"
                ),
                {"sid": sid},
            ).scalar_one()
        )


def _run_history(engine: Engine, run_id: str) -> list[tuple[str | None, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT from_state, to_state FROM b2.evaluation_run_state_transitions"
                " WHERE evaluation_run_id = :rid ORDER BY sequence"
            ),
            {"rid": uuid.UUID(run_id)},
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


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


def _chain_ok(pairs: Sequence[tuple[str | None, str]]) -> bool:
    """from/to 逐条链接（首条 from 为 None 且指向 received），且每一步都在冻结的提交状态机取值域内。"""
    previous: str | None = None
    for index, (frm, to) in enumerate(pairs):
        if index == 0:
            if frm is not None or to != states.RECEIVED:
                return False
            previous = to
            continue
        if frm != previous:
            return False
        if to not in states.SUBMISSION_TRANSITIONS.get(str(frm), ()):
            return False
        previous = to
    return True


# ============================================================================================
# ① 静态阻断落到提交上
# ============================================================================================


def test_recheck_static_block_lands_on_submission(engine: Engine, tmp_path: Path) -> None:
    say("=" * 92)
    sid, qid = _seed_submission(engine)
    contents = {"main.py": BLOCKING_SOURCE.encode("utf-8")}
    runner = _ObservingRunner()
    orchestrator = _orchestrator(
        engine, tmp_path, contents=contents, cases=[(str(uuid.uuid4()), 0, 5.0)], runner=runner
    )
    before = (_sub_state(engine, sid), _sub_history(engine, sid))
    outcome = orchestrator.run(_envelope(sid, qid, contents))
    after = (_sub_state(engine, sid), _sub_history(engine, sid))
    run_pairs = _run_history(engine, outcome.evaluation_run_id)
    frames = _frame_rows(engine, outcome.evaluation_run_id)

    say("① outcome =", outcome.state, outcome.terminal_reason, "| runner 调用 =", runner.calls)
    say("① 运行迁移史 =", run_pairs, "| 帧 =", frames)
    say("① 提交 前 =", before)
    say("① 提交 后 =", after)
    say("① 提交史含 (validated,rejected) =", ("validated", "rejected") in after[1])
    say("① 提交史是否出现 queued/running =", any(to in {"queued", "running"} for _, to in after[1]))
    say("① 提交史逐条链接合法 =", _chain_ok(after[1]))

    assert outcome.state == "rejected" and outcome.terminal_reason == "static_blocked"
    assert runner.calls == 0
    assert after[0] == "rejected"
    assert after[1] == [
        (None, "received"),
        ("received", "validated"),
        ("validated", "rejected"),
    ]
    assert all(to not in {"queued", "running"} for _, to in after[1])
    assert _chain_ok(after[1])
    assert "running" not in [to for _, to in run_pairs]
    assert run_pairs == [(None, "queued"), ("queued", "rejected")]
    assert [event for _, event in frames].count("progress") >= 1
    assert [event for _, event in frames][-1] == "rejected"
    say("① 裁决: 静态阻断路径在**提交级**表达为 validated->rejected（成立）")
    say("=" * 92)


# ============================================================================================
# ② 成功路径走完提交状态机
# ============================================================================================


def test_recheck_success_full_lifecycle(engine: Engine, tmp_path: Path) -> None:
    sid, qid = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    orchestrator = _orchestrator(
        engine,
        tmp_path,
        contents=contents,
        cases=[(str(uuid.uuid4()), 0, 4.0), (str(uuid.uuid4()), 1, 6.0)],
        runner=_ObservingRunner(),
    )
    outcome = orchestrator.run(_envelope(sid, qid, contents))
    state = _sub_state(engine, sid)
    pairs = _sub_history(engine, sid)
    say("② outcome =", outcome.state, "| 提交 state =", state)
    say("② 提交迁移史 =", pairs)
    say("② 提交史逐条链接合法 =", _chain_ok(pairs))
    assert outcome.state == "completed"
    assert state == "completed"
    assert [to for _, to in pairs] == ["received", "validated", "queued", "running", "completed"]
    assert _chain_ok(pairs)
    say("② 裁决: 成功路径提交走完 validated->queued->running->completed（成立）")


# ============================================================================================
# ③ 非法迁移零写入
# ============================================================================================


def test_recheck_illegal_advance_is_zero_write(engine: Engine, tmp_path: Path) -> None:
    say("=" * 92)
    # (i) 终态出发的任何推进：直接调用 advance_submission_state，必须 None 且零写入
    sid, qid = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    ok = _orchestrator(
        engine, tmp_path, contents=contents, cases=[(str(uuid.uuid4()), 0, 5.0)],
        runner=_ObservingRunner(),
    )
    ok.run(_envelope(sid, qid, contents))
    state_before = _sub_state(engine, sid)
    count_before = _sub_history_count(engine, sid)
    history_before = _sub_history(engine, sid)
    returns: list[str] = []
    for target in ("rejected", "queued", "running", "failed"):
        with engine.begin() as connection:
            result = advance_submission_state(
                connection, submission_id=str(sid), to_state=target, occurred_at=BASE, reason="probe"
            )
        returns.append(f"{target}->{result!r}")
    say("③(i) 提交已达 completed；非法推进返回值 =", returns)
    say(
        f"③(i) state before={state_before} after={_sub_state(engine, sid)};"
        f" 史行数 before={count_before} after={_sub_history_count(engine, sid)}"
    )
    assert all(item.endswith("None") for item in returns)
    assert _sub_state(engine, sid) == state_before == "completed"
    assert _sub_history_count(engine, sid) == count_before
    assert _sub_history(engine, sid) == history_before

    # (ii) 运行**前**的技术失败（取包失败）：契约无 validated->failed，提交必须零写入
    sid2, qid2 = _seed_submission(engine)
    failing = _orchestrator(
        engine,
        tmp_path,
        contents=contents,
        cases=[(str(uuid.uuid4()), 0, 5.0)],
        runner=_ObservingRunner(),
        package_error=RuntimeError("probe: package unavailable"),
    )
    before2 = (_sub_state(engine, sid2), _sub_history(engine, sid2))
    outcome2 = failing.run(_envelope(sid2, qid2, contents))
    after2 = (_sub_state(engine, sid2), _sub_history(engine, sid2))
    say("③(ii) 取包失败 outcome =", outcome2.state, outcome2.terminal_reason)
    say("③(ii) 提交 前 =", before2, " 后 =", after2)
    assert outcome2.state == "failed"
    assert after2 == before2 == (
        "validated",
        [(None, "received"), ("received", "validated")],
    )

    # (iii) 运行**后**的技术失败：提交必须先经过 running，再 ->failed
    sid3, qid3 = _seed_submission(engine)
    down = _orchestrator(
        engine,
        tmp_path,
        contents=contents,
        cases=[(str(uuid.uuid4()), 0, 5.0)],
        runner=_ObservingRunner(failure=SandboxUnavailableError("probe: 沙箱不可用")),
    )
    outcome3 = down.run(_envelope(sid3, qid3, contents))
    pairs3 = _sub_history(engine, sid3)
    say("③(iii) 沙箱不可用 outcome =", outcome3.state, "| 提交 state =", _sub_state(engine, sid3))
    say("③(iii) 提交迁移史 =", pairs3)
    assert outcome3.state == "failed"
    assert _sub_state(engine, sid3) == "failed"
    assert ("validated", "queued") in pairs3 and ("queued", "running") in pairs3
    assert ("running", "failed") in pairs3
    assert ("validated", "failed") not in pairs3
    assert _chain_ok(pairs3)

    # (iv) 观察项：对**已终态**的提交再跑一次评测（运行可重复创建），提交侧不再前进
    sid4, qid4 = _seed_submission(engine)
    block_orch = _orchestrator(
        engine, tmp_path, contents={"main.py": BLOCKING_SOURCE.encode("utf-8")},
        cases=[(str(uuid.uuid4()), 0, 5.0)], runner=_ObservingRunner(),
    )
    first = block_orch.run(_envelope(sid4, qid4, {"main.py": BLOCKING_SOURCE.encode("utf-8")}))
    state_after_first = _sub_state(engine, sid4)
    second_orch = _orchestrator(
        engine, tmp_path, contents={"main.py": CLEAN_SOURCE.encode("utf-8")},
        cases=[(str(uuid.uuid4()), 0, 5.0)], runner=_ObservingRunner(),
    )
    second = second_orch.run(_envelope(sid4, qid4, {"main.py": CLEAN_SOURCE.encode("utf-8")}))
    say("③(iv) 第一次运行 =", first.state, "| 提交 =", state_after_first)
    say("③(iv) 第二次运行(同一提交) =", second.state, "| 提交 =", _sub_state(engine, sid4))
    say("③(iv) 提交迁移史 =", _sub_history(engine, sid4))
    assert state_after_first == "rejected"
    assert second.state == "completed"
    say("③(iv) 观察项: 同一提交的**最新**运行是 completed，而提交状态仍是 rejected（一次性同步）")
    say("=" * 92)


# ============================================================================================
# ④ 批量迁移第 2 行非法 ⇒ 一行都不写
# ============================================================================================


def test_recheck_batch_illegal_second_row_writes_nothing(engine: Engine) -> None:
    sid, _qid = _seed_submission(engine)
    count_before = _sub_history_count(engine, sid)
    history_before = _sub_history(engine, sid)
    caught: str | None = None
    # 调用方**吞掉**异常并让事务照常提交 —— 这正是"逐行校验逐行写"会留下半截历史的场景
    with engine.begin() as connection:
        try:
            record_submission_transitions(
                connection,
                submission_id=str(sid),
                transitions=((None, "received", None), ("received", "running", "probe 非法")),
                occurred_at=BASE,
            )
        except states.IllegalTransition as error:
            caught = f"IllegalTransition({error})"
    count_after = _sub_history_count(engine, sid)
    say("④ 批量迁移第 2 行非法 → 异常 =", caught)
    say(f"④ 史行数 before={count_before} after={count_after}")
    say("④ 史 =", _sub_history(engine, sid))
    assert caught is not None
    assert count_after == count_before == 2
    assert _sub_history(engine, sid) == history_before


# ============================================================================================
# ⑥ 运行侧与提交侧同事务（任一侧失败⇒两边都不落库）
# ============================================================================================


def test_recheck_run_and_submission_state_advance_are_one_transaction(
    engine: Engine, tmp_path: Path
) -> None:
    import pytest

    from autograder_b2.evaluation.store import create_run, load_run, transition_run

    sid, qid = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(sid),
            question_version_id=str(qid),
            submission_digest=DIGEST_SUB,
            occurred_at=BASE,
        )
    sub_before = (_sub_state(engine, sid), _sub_history(engine, sid))
    with pytest.raises(RuntimeError), engine.begin() as connection:
        transition_run(
            connection,
            evaluation_run_id=run_id,
            to_state="rejected",
            occurred_at=BASE,
            reason="probe",
            terminal_reason="static_blocked",
        )
        advance_submission_state(
            connection,
            submission_id=str(sid),
            to_state="rejected",
            occurred_at=BASE,
            reason="static_blocked",
        )
        raise RuntimeError("probe: 事务中途失败")
    with engine.connect() as connection:
        run_row = load_run(connection, run_id)
    sub_after = (_sub_state(engine, sid), _sub_history(engine, sid))
    say("⑥ 事务中途失败后: 运行 state =", None if run_row is None else run_row.state)
    say("⑥ 提交 前 =", sub_before, " 后 =", sub_after)
    assert run_row is not None and run_row.state == "queued"
    assert run_row.terminal_reason is None and run_row.finished_at is None
    assert sub_after == sub_before == ("validated", [(None, "received"), ("received", "validated")])


# ============================================================================================
# ⑤ 标准 2 / 3 回归（我自己的仪器，改动后重测）
# ============================================================================================


def test_recheck_standards_2_and_3_still_hold(engine: Engine, tmp_path: Path) -> None:
    say("=" * 92)
    # 标准 2：rejected / failed 两条路径的帧
    sid, qid = _seed_submission(engine)
    blocked = _orchestrator(
        engine, tmp_path, contents={"main.py": BLOCKING_SOURCE.encode("utf-8")},
        cases=[(str(uuid.uuid4()), 0, 5.0)], runner=_ObservingRunner(),
    )
    rejected_run = blocked.run(
        _envelope(sid, qid, {"main.py": BLOCKING_SOURCE.encode("utf-8")})
    ).evaluation_run_id
    sid2, qid2 = _seed_submission(engine)
    down = _orchestrator(
        engine, tmp_path, contents={"main.py": CLEAN_SOURCE.encode("utf-8")},
        cases=[(str(uuid.uuid4()), 0, 5.0)],
        runner=_ObservingRunner(failure=SandboxUnavailableError("probe")),
    )
    failed_run = down.run(_envelope(sid2, qid2, {"main.py": CLEAN_SOURCE.encode("utf-8")})).evaluation_run_id

    for label, run_id in (("rejected", rejected_run), ("failed", failed_run)):
        rows = _frame_rows(engine, run_id)
        sequences = [row[0] for row in rows]
        events = [row[1] for row in rows]
        terminal = [event for event in events if event in TERMINAL_EVENTS]
        with engine.connect() as connection:
            full = [frame.sequence for frame in read_frames(connection, evaluation_run_id=run_id)]
        assert all(b > a for a, b in pairwise(sequences))
        assert EVENT_PROGRESS in events
        assert len(terminal) == 1 and terminal[0] in {"rejected", "failed"}
        assert full == sequences
        for pivot in range(len(full) + 1):
            with engine.connect() as connection:
                head = (
                    []
                    if pivot == 0
                    else read_frames(connection, evaluation_run_id=run_id, after=0, limit=pivot)
                )
                tail = read_frames(connection, evaluation_run_id=run_id, after=pivot)
            joint = [f.sequence for f in head] + [f.sequence for f in tail]
            assert joint == full and len(set(joint)) == len(joint)
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
                engine=engine, evaluation_run_id=run_id, after=seen[-1],
                poll_interval=0.01, idle_timeout=0.05,
            )
        ]
        assert seen + resumed == full
        say(f"⑤ 标准2[{label}] 帧={rows} 严格单调 终态={terminal} 断线重连拼接={seen + resumed}")

    # 标准 3：共享闸门（容量 1）外部观测峰值 ≤ 1 + 容量 3 正向对照
    def drive(capacity: int) -> tuple[int, int, int]:
        gate = DynamicExecutionGate(capacity=capacity)
        runner = _ObservingRunner(delay=0.08)
        contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
        cases = [(str(uuid.uuid4()), index, 1.0) for index in range(2)]
        shared = _orchestrator(
            engine, tmp_path, contents=contents, cases=cases, runner=runner, gate=gate
        )
        envelopes = [
            _envelope(*_seed_submission(engine), contents) for _ in range(4)
        ]
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                barrier.wait(timeout=20)
                shared.run(envelopes[index])
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert errors == [], errors
        live, peak, calls = runner.snapshot()
        return live, peak, calls

    live, peak, calls = drive(1)
    say(f"⑤ 标准3 容量=1: runner 调用={calls} 外部观测峰值={peak} 残留 live={live}")
    assert calls == 8 and peak <= 1 and peak == 1
    _live_c, peak_c, calls_c = drive(3)
    say(f"⑤ 标准3 正向对照 容量=3: runner 调用={calls_c} 外部观测峰值={peak_c}")
    assert peak_c > 1
    say("=" * 92)
