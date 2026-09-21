"""评测编排、进度帧、证据、评分与报告（`RQ-B-2-50/60` 的编排一侧 + `RQ-B-2-80`）的验收测试。

覆盖的硬规则（逐条都有断言，不是"看起来对"）：

1. 静态阻断 ⇒ `rejected + static_blocked`、迁移史**无 `running`**、runner **一次都没被调用**；
2. 安全阻断 ⇒ `rejected + security_blocked` 同理；
3. 沙箱不可用 ⇒ `failed + infrastructure_error`（不赖学生）；`score=0` 只出现在 `completed`；
4. 进度帧 `sequence` 严格单调（并发取号不重复）、终态帧恰好一个、`completed` 帧带分数、重连读取不重不漏；
5. 报告**真的**过 `MAIN` 的 `evaluation-report` schema，`total_check.matches_total is True` 且逐项之和 = 总分；
6. 同一批五项绑定摘要下重复编排 ⇒ 同逐项得分、同证据摘要（并在可行时同分数、同 `result_digest`）；
7. 学生视图省略隐藏用例的期望输出，教师视图保留；
8. 结果写入失败 ⇒ 不对外声称报告完成（无 `evaluation_results` 行即无报告）；
9. 动态执行并发峰值 ≤ 1；非法迁移在写入前被拒；outbox 事件集合正确且 payload 不含源码。

**测试替身声明（不是交付实现）**：`autograder_b2.security`（安全判定）与 `autograder_b2.runner`
（沙箱逐用例执行）由并行工作项交付，本文件用 `_FixedSecurityDecider` / `_ScriptedCaseRunner` 两个
**显式标注的替身**驱动流水线；替身只模仿它们的读取面（`case_id`/`weight`/`failure_category`），
不代替它们做任何判定，也不构成"安全/沙箱已经端到端可用"的证据。静态检查**不是替身**：静态阶段调用
已交付的 `autograder_b2.static.analyze_static`，跑在真实写盘的源码上。

**一条已修复的 schema 缺陷（本工作项实测发现）**：迁移 0006 的两条 CHECK
（`ck_evaluation_runs_terminal_consistency` / `ck_progress_events_terminal_frames`）曾要求**终态（含
`completed`）必须携带属于四种失败原因的 `terminal_reason`**，而契约要求 `completed` 的该字段为
`null`——两者不可同时满足，后果是"一次真正成功的评测无法落库"（要么写假原因，要么把成功降级成
`failed + internal_error`，两者都被禁止）。编排层当时选择**不写假值**并显式暴露冲突；迁移
`20260915_0007_b2_terminal_reason_semantics` 随后放宽了这两条约束。本文件现在同时钉住三件事：
`completed` 必须可写、`completed` 不得携带失败原因（数据库层拒绝冒充）、以及成功路径端到端可用。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from autograder_b2.contracts import EVALUATION_PACKAGE_SCHEMA, load_contract_schema
from autograder_b2.db.schema import (
    case_evidence,
    evaluation_results,
    evaluation_runs,
    outbox_events,
    progress_events,
    submissions,
)
from autograder_b2.domain.envelope import ApiProvenance, EnvelopeFile, SubmissionEnvelope
from autograder_b2.domain.evidence import (
    CaseEvidence,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.domain.security import SecurityDecision, SecurityFinding
from autograder_b2.domain.transitions import history_of as submission_history_of
from autograder_b2.domain.transitions import record_transitions
from autograder_b2.evaluation import (
    DYNAMIC_CONCURRENCY_LIMIT,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_PROGRESS,
    EVENT_REJECTED,
    FRAME_EVENTS,
    RUN_TRANSITIONS,
    STAGES,
    TERMINAL_REASONS,
    CaseAward,
    CaseDescriptor,
    ComparisonPolicy,
    DynamicExecutionGate,
    EvaluationOrchestrator,
    FrameRejected,
    IllegalRunTransition,
    PackageView,
    ProgressFrame,
    ReportBinding,
    ReportInputs,
    ScoreTotalMismatch,
    ScoringPreconditionError,
    TerminalFrameExists,
    append_frame,
    build_report,
    build_total_check,
    create_run,
    for_student,
    for_teacher,
    read_frames,
    report_digest,
    report_schema_errors,
    require_run_transition,
    run_history,
    score_run,
    transition_run,
    validate_report,
)
from autograder_b2.evaluation import ports as ports_module
from autograder_b2.evaluation import progress as progress_module
from autograder_b2.evaluation import store as store_module
from autograder_b2.events.outbox import EVALUATION_COMPLETED as SUBJECT_COMPLETED
from autograder_b2.events.outbox import EVALUATION_FAILED as SUBJECT_FAILED
from autograder_b2.events.outbox import EVALUATION_PROGRESS as SUBJECT_PROGRESS
from autograder_b2.events.outbox import EVALUATION_STARTED as SUBJECT_STARTED
from autograder_b2.static import analyze_static
from tests.conftest import requires_postgres

pytestmark = requires_postgres

#: 数据集基准时刻（UTC，毫秒精度）。
BASE = datetime(2026, 9, 15, 8, 0, 0, 100_000, tzinfo=UTC)

#: 会被写进待测源码的标记串：任何帧、任何事件 payload 里都**不得**出现它。
SOURCE_MARKER = "SECRET_STUDENT_MARKER"
#: 隐藏用例的期望输出标记串：学生视图里不得出现。
HIDDEN_EXPECTED = "HIDDEN_EXPECTED_OUTPUT_MARKER"

#: 命中阻断类静态规则（STAT-PY-030）的 Python 源码。
BLOCKING_SOURCE = 'import os\n\nos.system("rm -rf /")\n'
#: 干净源码：静态检查放行。
CLEAN_SOURCE = f'print("hello")\n# {SOURCE_MARKER}\n'

#: 报告确定性测试用的固定标识（同输入 ⇒ 同报告）。
FIXED_VISIBLE_CASE = str(uuid.UUID(int=1))
FIXED_HIDDEN_CASE = str(uuid.UUID(int=2))
FIXED_RUN = str(uuid.UUID(int=3))
FIXED_SUBMISSION = str(uuid.UUID(int=4))
FIXED_QUESTION = str(uuid.UUID(int=5))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


DIGEST_SUBMISSION = _digest("submission")
DIGEST_PACKAGE = _digest("package")
DIGEST_POLICY = _digest("policy")
DIGEST_ENV = _digest("environment")
DIGEST_QUESTION = _digest("question-version")


def _package_digest() -> str:
    return DIGEST_PACKAGE


class FrozenClock:
    """测试用时钟（编排层的时间注入点）；每次调用 +1 ms，使帧与事件可区分。"""

    def __init__(self, start: datetime = BASE) -> None:
        self._value = start

    def __call__(self) -> datetime:
        current = self._value
        self._value = self._value + timedelta(milliseconds=1)
        return current


@dataclass(frozen=True, slots=True)
class _FakePackageCase:
    """**测试替身**：模仿 runner 侧 `PackageCase` 的读取面（编排层只把它当不透明句柄）。"""

    case_id: str
    ordinal: int
    weight: float
    visible_to_student: bool = False


class _FixedPackageSource:
    """**测试替身**：返回固定 package 视图（生产装配读 B3 的 package 并解析）。"""

    def __init__(self, view: PackageView | None = None, error: Exception | None = None) -> None:
        self.view = view
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        self.calls.append((evaluation_run_id, question_version_id))
        if self.error is not None:
            raise self.error
        assert self.view is not None
        return self.view


class _FixedSecurityDecider:
    """**测试替身**：返回固定三态结论（生产实现是 `security.decide_static` 的适配器）。

    它不做任何安全判定，只把给定结论回放给编排层——因此本文件的"安全阻断"用例只证明**编排语义**
    （阻断 ⇒ rejected、不调 runner、无 running），不证明安全规则本身有效。
    """

    def __init__(
        self, decision: SecurityDecision | None = None, error: Exception | None = None
    ) -> None:
        self.decision = decision
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def decide(
        self, *, evaluation_run_id: str, files: Mapping[str, str], language: str
    ) -> SecurityDecision:
        self.calls.append(
            {"evaluation_run_id": evaluation_run_id, "files": dict(files), "language": language}
        )
        if self.error is not None:
            raise self.error
        assert self.decision is not None
        return self.decision


class _ScriptedCaseRunner:
    """**测试替身**：按脚本返回构造好的 `CaseEvidence` 并记录调用（不是交付实现）。

    它像真实 runner 一样从 `submission.case.case_id` 取用例标识、用 `submission.workspace` 作为工作区，
    因此"每用例一份输入与独立工作区""句柄原样来自 package"这两条接线约定在这里被真的验证。
    """

    def __init__(
        self,
        *,
        plan: Mapping[str, Mapping[str, Any]] | None = None,
        default_passed: bool = True,
        failure: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.plan = {str(key): dict(value) for key, value in (plan or {}).items()}
        self.default_passed = default_passed
        self.failure = failure
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        case = submission.case
        case_id = str(case.case_id)  # type: ignore[attr-defined] - 真实/替身用例对象都带该属性
        self.calls.append(
            {
                "package": package,
                "case": case,
                "adapter": submission.adapter,
                "policy": submission.policy,
                "workspace": Path(submission.workspace),
                "files": submission.files,
            }
        )
        if self.delay:
            time.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        spec = self.plan.get(case_id, {})
        passed = bool(spec.get("passed", self.default_passed))
        category = spec.get("failure_category")
        if not passed and category is None:
            category = "output_mismatch"
        actual = str(spec.get("actual_output_excerpt", f"actual-{case_id}"))
        expected = str(spec.get("expected_output_excerpt", f"expected-{case_id}"))
        return CaseEvidence(
            case_id=case_id,
            ordinal=int(case.ordinal),  # type: ignore[attr-defined]
            weight=float(case.weight),  # type: ignore[attr-defined]
            passed=passed,
            failure_category=None if passed else category,
            input_digest=_digest(f"input:{case_id}"),
            stdout_digest=_digest(actual),
            stderr_digest=_digest(""),
            actual_output_excerpt=actual,
            expected_output_excerpt=expected,
            exit_code=0 if passed else 1,
            duration_ms=int(spec.get("duration_ms", 7)),
            resource_limit_hit=None,
            sandbox_violations=tuple(spec.get("sandbox_violations", ())),
            message=None,
            evidence_digest=_digest(f"evidence:{case_id}:{passed}"),
        )


class _LocalWorkspaceProvider:
    """**测试本地实现**：把提交内容写进服务端生成的工作区目录（生产实现从 CAS 读内容）。

    运行级工作区（`case_id=None`）供静态检查与安全判定读；每个用例一份**独立**工作区（绝不复用）。
    """

    def __init__(self, root: Path, contents: Mapping[str, bytes]) -> None:
        self.root = root
        self.contents = dict(contents)
        self.prepared: list[Path] = []
        self.released: list[Path] = []

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
    ) -> Path:
        scope = "run" if case_id is None else f"case-{case_id[:8]}"
        workspace = self.root / f"ws-{evaluation_run_id[:8]}-{scope}-{len(self.prepared)}"
        workspace.mkdir(parents=True, exist_ok=False)
        for item in files:
            target = workspace / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.contents[item.path])
        self.prepared.append(workspace)
        return workspace

    def release(self, workspace: Path) -> None:
        self.released.append(workspace)


# --- 替身用的安全结论 ---------------------------------------------------------------------


def _clean_decision() -> SecurityDecision:
    """替身用的"放行"结论：必需检查全部跑过、未发现风险、没有未检查项。"""
    return SecurityDecision(
        status="not_found",
        findings=(),
        blocking=False,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="必需检查项全部执行完毕，未发现违规模式。",
    )


def _blocking_decision() -> SecurityDecision:
    """替身用的"高危阻断"结论（`detected_in=static` 必须带 `rule_id`）。"""
    finding = SecurityFinding(
        finding_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-test-security-finding")),
        category="destructive_command",
        severity="high",
        detected_in="static",
        message="检出破坏性命令模式（测试替身构造，不代表安全规则真实有效）。",
        rule_id="SEC-TEST-001",
    )
    return SecurityDecision(
        status="found",
        findings=(finding,),
        blocking=True,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="检查发现 1 项违规模式（其中 high 1 项）。已阻断动态执行。",
    )


def _unchecked_decision() -> SecurityDecision:
    """替身用的"未检查"结论（`not_checked` 绝不等于"安全"）。"""
    return SecurityDecision(
        status="not_checked",
        findings=(),
        blocking=False,
        covered=(),
        unchecked=("python_ast",),
        rationale="检查未完成（1 项必需检查未执行）：结论为「未检查」。",
    )


# --- 夹具与构造器 -------------------------------------------------------------------------


def _package_view(
    cases: Sequence[tuple[str, int, float, bool]] | None = None,
    *,
    comparison: ComparisonPolicy | None = None,
) -> PackageView:
    """构造 package 视图（句柄是测试替身对象：编排层只把它们当不透明句柄传递）。"""
    declared = list(cases or [(str(uuid.uuid4()), 0, 5.0, True)])
    descriptors = tuple(
        CaseDescriptor(
            case_id=case_id,
            ordinal=ordinal,
            weight=weight,
            visible_to_student=visible,
            handle=_FakePackageCase(
                case_id=case_id, ordinal=ordinal, weight=weight, visible_to_student=visible
            ),
        )
        for case_id, ordinal, weight, visible in declared
    )
    return PackageView(
        package_digest=_package_digest(),
        language="python",
        cases=descriptors,
        runner_policy_digest=DIGEST_POLICY,
        environment_digest=DIGEST_ENV,
        comparison=comparison or ComparisonPolicy(stdout="exact"),
        question_version_binding=DIGEST_QUESTION,
        interpreter_version="CPython 3.12",
        handle={"package": "fake-package-handle"},
        adapter_handle={"adapter": "fake-package-adapter"},
        policy_handle={"policy": "fake-package-policy"},
    )


def _correlation_for(submission_id: uuid.UUID | str) -> str:
    """由提交标识确定性派生 `correlation_id`：提交行与信封用同一个值（事件可按它 join 回提交）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"b2-evaluation-test:{submission_id}"))


def _column_types(engine: Engine, table: str) -> dict[str, str]:
    """`b2` schema 里某张表的列名 → 数据类型（用于适配迁移 0008 前后的两种 `submissions` 形状）。"""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'b2' AND table_name = :table"
            ),
            {"table": table},
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def _typed(value: object, data_type: str) -> object:
    if value is None:
        return None
    if data_type == "uuid":
        return uuid.UUID(str(value))
    if data_type in {"bigint", "integer", "smallint"}:
        return int(str(value))
    return str(value)


def _seed_submission(
    engine: Engine,
    *,
    digest: str = DIGEST_SUBMISSION,
) -> tuple[uuid.UUID, uuid.UUID]:
    """写入一条提交行（`evaluation_runs.submission_id` 外键的前置条件）。

    迁移 0008 之后 `b2.submissions` 还会带 `correlation_id` 与非空的 provenance 列（按 `intake` 判形状）：
    本夹具按**库里真实的列**填充（`intake='api'` ⇒ `request_id` 非空、`mailbox/uidvalidity/uid/message_id`
    为空），因此在 0008 前后两种库上插入的都是真实合规的行——不是把失败绕过去。
    """
    identifier = uuid.uuid4()
    question = uuid.uuid4()
    correlation = _correlation_for(identifier)
    values: dict[str, object] = {
        "submission_id": identifier,
        "student_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "section_id": uuid.uuid4(),
        "question_version_id": question,
        "intake": "api",
        "idempotency_key": f"key-{identifier}",
        "idempotency_scope": "b2-evaluation-test",
        "submission_digest": digest,
        # 忠实反映 intake 的产物：提交经 `received -> validated` 后交给评测。**不能**种成 `queued`——
        # 契约 `SubmissionTransitions` 规定"静态高危必须在不进入沙箱的情况下被拒"，因此静态分析发生
        # 在 `queued` **之前**；把起点种成 `queued` 会造出一个契约上无法被静态阻断的起点
        #（`queued` 只有 `running` 一条出边），从而使 `validated -> rejected` 这条判据无法被断言。
        "state": "validated",
        "created_at": BASE,
        "updated_at": BASE,
    }
    column_types = _column_types(engine, "submissions")
    for name in ("correlation_id", "request_id"):
        if name in column_types:
            values[name] = _typed(correlation if name == "correlation_id" else uuid.uuid4(),
                                  column_types[name])
    for name in ("mailbox", "uidvalidity", "uid", "message_id"):
        if name in column_types:
            values[name] = None
    with engine.begin() as connection:
        connection.execute(submissions.insert().values(**values))
        record_transitions(
            connection,
            submission_id=str(identifier),
            transitions=((None, "received", None), ("received", "validated", "envelope accepted")),
            occurred_at=BASE,
        )
    return identifier, question


def _envelope(
    *,
    submission_id: uuid.UUID,
    question_version_id: uuid.UUID,
    contents: Mapping[str, bytes],
) -> SubmissionEnvelope:
    """构造提交信封（含真实摘要；`files` 与工作区内容一一对应）。"""
    files = tuple(
        EnvelopeFile(
            path=path,
            content_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            storage_ref=f"sha256/{hashlib.sha256(content).hexdigest()}",
        )
        for path, content in contents.items()
    )
    return SubmissionEnvelope(
        submission_id=str(submission_id),
        student_id=str(uuid.uuid4()),
        course_id=str(uuid.uuid4()),
        section_id=str(uuid.uuid4()),
        question_version_id=str(question_version_id),
        idempotency_key=f"key-{submission_id}",
        intake="api",
        provenance=ApiProvenance(request_id=_correlation_for(submission_id)),
        correlation_id=_correlation_for(submission_id),
        files=files,
        submission_digest=DIGEST_SUBMISSION,
        received_at=BASE,
    )


@dataclass(frozen=True, slots=True)
class _Rig:
    """一次测试的装配：编排器 + 三个替身/本地实现 + 并发闸门。"""

    orchestrator: EvaluationOrchestrator
    packages: _FixedPackageSource
    security: _FixedSecurityDecider
    runner: _ScriptedCaseRunner
    workspaces: _LocalWorkspaceProvider
    gate: DynamicExecutionGate

    def run(self, envelope: SubmissionEnvelope) -> Any:
        return self.orchestrator.run(envelope)


def _rig(
    engine: Engine,
    tmp_path: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
    cases: Sequence[tuple[str, int, float, bool]] | None = None,
    comparison: ComparisonPolicy | None = None,
    decision: SecurityDecision | None = None,
    runner: _ScriptedCaseRunner | None = None,
    gate: DynamicExecutionGate | None = None,
    package_error: Exception | None = None,
) -> _Rig:
    """装配一个编排器（静态检查是真的；安全判定与 runner 是显式标注的替身）。"""
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(exist_ok=True)
    payload = dict(contents or {"main.py": CLEAN_SOURCE.encode("utf-8")})
    packages = _FixedPackageSource(
        view=_package_view(cases, comparison=comparison), error=package_error
    )
    security = _FixedSecurityDecider(decision or _clean_decision())
    workspaces = _LocalWorkspaceProvider(workspace_root, payload)
    active_runner = runner or _ScriptedCaseRunner()
    active_gate = gate or DynamicExecutionGate()
    orchestrator = EvaluationOrchestrator(
        engine=engine,
        package_source=packages,
        workspace_provider=workspaces,
        security=security,
        runner=active_runner,
        gate=active_gate,
        now=FrozenClock(),
    )
    return _Rig(orchestrator, packages, security, active_runner, workspaces, active_gate)


def _run_once(
    engine: Engine,
    tmp_path: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
    cases: Sequence[tuple[str, int, float, bool]] | None = None,
    decision: SecurityDecision | None = None,
    runner: _ScriptedCaseRunner | None = None,
    gate: DynamicExecutionGate | None = None,
    package_error: Exception | None = None,
) -> tuple[_Rig, Any]:
    """建提交 + 信封 + 装配，跑一次完整编排，返回 `(rig, outcome)`。"""
    submission_id, question_version_id = _seed_submission(engine)
    payload = dict(contents or {"main.py": CLEAN_SOURCE.encode("utf-8")})
    envelope = _envelope(
        submission_id=submission_id, question_version_id=question_version_id, contents=payload
    )
    rig = _rig(
        engine,
        tmp_path,
        contents=payload,
        cases=cases,
        decision=decision,
        runner=runner,
        gate=gate,
        package_error=package_error,
    )
    return rig, rig.run(envelope)


def _completed_row_accepted(engine: Engine, terminal_reason: str | None) -> bool:
    """探针：数据库是否接受这样一条 `completed` 运行行（`terminal_reason` 给定）。用完即回滚。

    迁移 0007 之后：`completed` + `terminal_reason=NULL` 必须被接受（真实完成），而 `completed` + 失败
    原因必须被拒绝（不得用失败原因冒充成功完成）。本探针把这条 schema 语义变成可断言的证据。
    """
    submission_id, question_version_id = _seed_submission(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        accepted = True
        try:
            connection.execute(
                evaluation_runs.insert().values(
                    evaluation_run_id=uuid.uuid4(),
                    submission_id=submission_id,
                    state="completed",
                    stage="terminal",
                    sequence=1,
                    cases_total=1,
                    cases_done=1,
                    terminal_reason=terminal_reason,
                    submission_digest=DIGEST_SUBMISSION,
                    question_version_id=question_version_id,
                    started_at=BASE,
                    finished_at=BASE,
                    created_at=BASE,
                    updated_at=BASE,
                )
            )
        except IntegrityError:
            accepted = False
        finally:
            transaction.rollback()
    return accepted


def _frames(engine: Engine, run_id: str) -> list[ProgressFrame]:
    with engine.connect() as connection:
        return read_frames(connection, evaluation_run_id=run_id)


def _outbox_rows(engine: Engine, run_id: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(outbox_events.c.subject, outbox_events.c.payload)
        ).fetchall()
    return [
        {"subject": row.subject, "payload": dict(row.payload)}
        for row in rows
        if str(row.payload.get("evaluation_run_id")) == run_id
    ]


def _result_row(engine: Engine, run_id: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(evaluation_results).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()


def _submission_state_facts(engine: Engine, submission_id: object) -> tuple[str, list[tuple[str, str]]]:
    """提交的当前状态与迁移史（判据"静态阻断路径为 validated→rejected"的读取处）。"""
    with engine.connect() as connection:
        state = str(
            connection.execute(
                sa.select(submissions.c.state).where(
                    submissions.c.submission_id == uuid.UUID(str(submission_id))
                )
            ).scalar_one()
        )
        steps = [
            (str(item["from_state"]), str(item["to_state"]))
            for item in submission_history_of(connection, str(submission_id))
        ]
    return state, steps


def _static_result(blocking: bool = False) -> Any:
    """真实静态检查结果（跑在真实临时工作区上）。"""
    root = Path(".pytest-tmp") / f"static-{uuid.uuid4().hex[:10]}"
    root.mkdir(parents=True, exist_ok=True)
    source = BLOCKING_SOURCE if blocking else CLEAN_SOURCE
    (root / "main.py").write_text(source, encoding="utf-8")
    return analyze_static(workspace=root, files=["main.py"], language="python")


# --- 报告夹具（确定性） -------------------------------------------------------------------


def _report_evidence(*, duration_ms: int = 11) -> tuple[CaseEvidence, CaseEvidence]:
    return (
        CaseEvidence(
            case_id=FIXED_VISIBLE_CASE,
            ordinal=0,
            weight=6.0,
            passed=True,
            failure_category=None,
            input_digest=_digest("in-0"),
            stdout_digest=_digest("out-0"),
            stderr_digest=_digest("err-0"),
            actual_output_excerpt="VISIBLE_ACTUAL",
            expected_output_excerpt="VISIBLE_EXPECTED",
            exit_code=0,
            duration_ms=duration_ms,
            resource_limit_hit=None,
            sandbox_violations=(),
            message=None,
            evidence_digest=_digest("ev-0"),
        ),
        CaseEvidence(
            case_id=FIXED_HIDDEN_CASE,
            ordinal=1,
            weight=4.0,
            passed=False,
            failure_category="output_mismatch",
            input_digest=_digest("in-1"),
            stdout_digest=_digest("out-1"),
            stderr_digest=_digest("err-1"),
            actual_output_excerpt="HIDDEN_ACTUAL",
            expected_output_excerpt=HIDDEN_EXPECTED,
            exit_code=1,
            duration_ms=duration_ms,
            resource_limit_hit=None,
            sandbox_violations=(),
            message="输出与期望不符（测试构造）。",
            evidence_digest=_digest("ev-1"),
        ),
    )


_REPORT_CASES = (
    CaseDescriptor(case_id=FIXED_VISIBLE_CASE, ordinal=0, weight=6.0, visible_to_student=True),
    CaseDescriptor(case_id=FIXED_HIDDEN_CASE, ordinal=1, weight=4.0, visible_to_student=False),
)


def _report_inputs(*, duration_ms: int = 11) -> ReportInputs:
    """同一份输入永远产生同一份报告（供可重放断言使用）。"""
    evidence = _report_evidence(duration_ms=duration_ms)
    static = _static_result()
    security = _clean_decision()
    breakdown = score_run(
        cases=_REPORT_CASES,
        evidence=evidence,
        static=static,
        security=security,
        comparison=ComparisonPolicy(stdout="exact"),
    )
    return ReportInputs(
        evaluation_run_id=FIXED_RUN,
        submission_id=FIXED_SUBMISSION,
        question_version_id=FIXED_QUESTION,
        static=static,
        security=security,
        evidence=evidence,
        breakdown=breakdown,
        binding=ReportBinding(
            submission_digest=DIGEST_SUBMISSION,
            package_digest=DIGEST_PACKAGE,
            runner_policy_digest=DIGEST_POLICY,
            environment_digest=DIGEST_ENV,
            question_version_binding=DIGEST_QUESTION,
            interpreter_version="CPython 3.12",
        ),
    )


def _report(*, generated_at: datetime = BASE, duration_ms: int = 11) -> dict[str, object]:
    return build_report(_report_inputs(duration_ms=duration_ms), generated_at=generated_at)


# --- 1. 静态阻断 --------------------------------------------------------------------------


def test_static_block_rejects_without_running_or_runner_calls(engine: Engine, tmp_path: Path) -> None:
    """T-STAT-004 / 硬规则 1：静态阻断 ⇒ rejected/static_blocked，无 running，runner 零调用。"""
    rig, outcome = _run_once(
        engine, tmp_path, contents={"main.py": BLOCKING_SOURCE.encode("utf-8")}
    )
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
        findings = store_module.load_static_findings(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.state) == ("rejected", "rejected")
    assert (row.stage, row.terminal_reason) == ("terminal", "static_blocked")
    assert [item.to_state for item in history] == ["queued", "rejected"]
    assert "running" not in [item.to_state for item in history]
    assert row.started_at is None and row.finished_at is not None
    assert rig.runner.calls == []
    assert outcome.runner_invocations == 0 and outcome.score is None
    assert any(item.blocking for item in findings)
    assert _result_row(engine, outcome.evaluation_run_id) is None

    # 提交状态机（契约 `SubmissionTransitions`）：静态高危必须落在**提交**上，且**不进入** queued/running。
    submission_state, submission_steps = _submission_state_facts(engine, row.submission_id)
    assert submission_state == "rejected", submission_state
    assert ("validated", "rejected") in submission_steps, submission_steps
    assert all(to_state not in {"queued", "running"} for _, to_state in submission_steps), (
        f"静态阻断不得让提交进入 queued/running：{submission_steps}"
    )


def test_completed_path_advances_the_submission_through_its_full_lifecycle(
    engine: Engine, tmp_path: Path
) -> None:
    """成功路径：提交走完 `validated -> queued -> running -> completed`（契约提交状态机）。"""
    rig, outcome = _run_once(engine, tmp_path)  # 默认干净源码 ⇒ 全用例通过
    assert outcome.state == "completed", outcome
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
    assert row is not None
    submission_state, submission_steps = _submission_state_facts(engine, row.submission_id)
    assert submission_state == "completed", submission_state
    assert [to_state for _, to_state in submission_steps][-3:] == ["queued", "running", "completed"], (
        submission_steps
    )
    assert rig.runner.calls, "成功路径必须真的执行过用例"


def test_static_block_terminal_frame_has_reason_and_no_score(engine: Engine, tmp_path: Path) -> None:
    """静态阻断的终态帧：`rejected` + `static_blocked`，且不得携带分数（0 分不是拒绝的表达）。"""
    _, outcome = _run_once(
        engine, tmp_path, contents={"main.py": BLOCKING_SOURCE.encode("utf-8")}
    )
    frames = _frames(engine, outcome.evaluation_run_id)
    terminal = [
        frame for frame in frames if frame.event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
    ]
    assert [frame.event for frame in terminal] == [EVENT_REJECTED]
    assert terminal[0].terminal_reason == "static_blocked"
    assert terminal[0].score is None and terminal[0].is_pass is None


def test_completed_path_is_fully_functional_on_a_freshly_migrated_database(tmp_path: Path) -> None:
    """在一个**刚迁移到 head 的独立库**上跑完整成功路径（不依赖其他用例的数据，也不做任何 DDL）。

    断言：`completed` + 满分 + 逐用例得分 + 报告**从库中读回**且过契约校验 + 恰好一个 `completed` 帧
    带分数 + `evaluation.completed` 事件入队 + 两次运行（同绑定摘要）同分数、同 `result_digest`。
    """
    from tests.conftest import run_alembic, temporary_database

    with temporary_database() as url:
        upgraded = run_alembic(url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr[-800:]
        scratch = sa.create_engine(url)
        try:
            cases = [(str(uuid.uuid4()), 0, 4.0, True), (str(uuid.uuid4()), 1, 6.0, False)]
            contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
            submission_id, question_version_id = _seed_submission(scratch)
            envelope = _envelope(
                submission_id=submission_id,
                question_version_id=question_version_id,
                contents=contents,
            )
            outcomes = []
            for _ in range(2):
                rig = _rig(scratch, tmp_path, cases=cases, contents=contents)
                outcomes.append(rig.run(envelope))
            first, second = outcomes
            assert first.state == "completed" and second.state == "completed"
            assert first.score == Decimal(10) and first.is_pass is True
            assert first.score == second.score == Decimal(10)
            assert first.result_digest == second.result_digest
            assert first.report is not None and second.report is not None
            # 报告内容的稳定投影逐字相同；只有运行实例标识与生成时刻不同。
            def _stable(report: Mapping[str, object]) -> dict[str, object]:
                return {
                    key: value
                    for key, value in report.items()
                    if key not in {"evaluation_run_id", "generated_at", "result_digest"}
                }

            assert _stable(first.report) == _stable(second.report)
            assert first.report["evaluation_run_id"] != second.report["evaluation_run_id"]
            assert report_schema_errors(first.report) == []  # 落库再读回的报告仍过契约校验
            stored = _result_row(scratch, first.evaluation_run_id)
            assert stored is not None and str(stored.result_digest) == first.result_digest
            assert Decimal(stored.score) == Decimal(10) and bool(stored.is_pass) is True
            with scratch.connect() as connection:
                row = store_module.load_run(connection, first.evaluation_run_id)
                evidence = store_module.load_case_evidence(connection, first.evaluation_run_id)
                persisted_report = store_module.read_report(connection, first.evaluation_run_id)
            assert row is not None and row.state == "completed" and row.terminal_reason is None
            assert row.finished_at is not None and row.started_at is not None
            assert [item.awarded for item in evidence] == [Decimal(4), Decimal(6)]
            assert persisted_report == first.report
            frames = _frames(scratch, first.evaluation_run_id)
            terminal = [
                frame
                for frame in frames
                if frame.event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
            ]
            assert [frame.event for frame in terminal] == [EVENT_COMPLETED]
            assert terminal[0].score == Decimal(10) and terminal[0].is_pass is True
            assert terminal[0].terminal_reason is None
            outbox = _outbox_rows(scratch, first.evaluation_run_id)
            subjects = [item["subject"] for item in outbox]
            assert SUBJECT_COMPLETED in subjects and SUBJECT_FAILED not in subjects
            completed_payload = next(
                item["payload"] for item in outbox if item["subject"] == SUBJECT_COMPLETED
            )
            assert completed_payload["score"] == 10.0
            assert completed_payload["result_digest"] == first.result_digest
            # `completed` 的 payload 不含失败类别/终态原因（没有失败可言），也不含任何源码。
            # `max_score` / `is_pass` 由 CHG-B3-0003 裁决加入：B3 的分桶需要前者、通过率需要后者，
            # 两者都不是身份信息，不违反 events.md §3.4 的去标识化要求。
            assert set(completed_payload) == {
                "submission_id",
                "evaluation_run_id",
                "question_version_id",
                "score",
                "max_score",
                "is_pass",
                "result_digest",
            }
            assert SOURCE_MARKER not in json.dumps(completed_payload, ensure_ascii=False)
        finally:
            scratch.dispose()


def test_package_unavailable_fails_before_any_sandbox_attempt(engine: Engine, tmp_path: Path) -> None:
    """评测包不可获取（B3 侧依赖失败）⇒ `failed + infrastructure_error`，一次都不碰 runner。"""
    rig, outcome = _run_once(
        engine, tmp_path, package_error=PackageUnavailableError("package not reachable")
    )
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.terminal_reason) == ("failed", "infrastructure_error")
    assert [item.to_state for item in history] == ["queued", "failed"]
    assert "running" not in [item.to_state for item in history]
    assert rig.runner.calls == [] and rig.security.calls == []
    assert _result_row(engine, outcome.evaluation_run_id) is None


def test_workspace_failure_is_a_technical_failure(engine: Engine, tmp_path: Path) -> None:
    """提交工作区不可用（CAS/写盘故障）⇒ `failed + infrastructure_error`，不算学生失败。"""
    submission_id, question_version_id = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    envelope = _envelope(
        submission_id=submission_id, question_version_id=question_version_id, contents=contents
    )
    rig = _rig(engine, tmp_path, contents=contents)

    def boom(*args: Any, **kwargs: Any) -> Path:
        raise RuntimeError("artifact missing in CAS")

    rig.workspaces.prepare = boom  # type: ignore[method-assign]
    outcome = rig.run(envelope)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.terminal_reason) == ("failed", "infrastructure_error")
    assert outcome.score is None and rig.runner.calls == []
    assert _result_row(engine, outcome.evaluation_run_id) is None


# --- 2. 安全阻断 --------------------------------------------------------------------------


def test_security_block_rejects_without_running_or_runner_calls(
    engine: Engine, tmp_path: Path
) -> None:
    """硬规则 2：安全阻断 ⇒ rejected/security_blocked，无 running，runner 零调用（替身驱动）。"""
    rig, outcome = _run_once(engine, tmp_path, decision=_blocking_decision())
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
        findings = store_module.load_security_findings(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.terminal_reason) == ("rejected", "security_blocked")
    assert [item.to_state for item in history] == ["queued", "rejected"]
    assert rig.runner.calls == []
    assert findings and findings[0].severity == "high" and findings[0].blocking
    assert _result_row(engine, outcome.evaluation_run_id) is None
    assert rig.security.calls and rig.security.calls[0]["language"] == "python"
    assert "main.py" in rig.security.calls[0]["files"]


def test_runtime_sandbox_violation_rejects_the_run_instead_of_scoring_it(
    engine: Engine, tmp_path: Path
) -> None:
    """执行期沙箱违规 ⇒ `rejected + security_blocked`：**不产生分数**，也不是技术失败。

    违规以 runner 侧证据的形态进入（真实实现里由 `classify_case_outcome` 对 seccomp 拒绝与沙箱自有
    日志违规判定为 `security_blocked`），编排层必须把它升格成**运行级**安全拒绝：
    违规明细落 `b2.security_findings`（`detected_in="runtime"`）、迁移史是 `queued → running →
    rejected`、没有结果行、没有报告、后续用例不再执行；既不能记成 `failed`（技术故障），也不能把
    违规用例算成"学生这条 0 分"。**把升格逻辑删掉本用例就会失败**——它存在的全部理由就是这个。
    """
    case_ids = [str(uuid.uuid4()) for _ in range(3)]
    cases = [(case_id, index, 5.0, True) for index, case_id in enumerate(case_ids)]
    violation = SecurityFinding(
        finding_id=f"SF-{'0' * 32}",
        category="sandbox_violation",
        severity="high",
        detected_in="runtime",
        message="sandbox terminated the process: denied system call (seccomp default-deny)",
        rule_id="runner.seccomp",
    )
    runner = _ScriptedCaseRunner(
        plan={
            case_ids[1]: {
                "passed": False,
                "failure_category": "security_blocked",
                "sandbox_violations": (violation,),
            }
        }
    )
    rig, outcome = _run_once(engine, tmp_path, cases=cases, runner=runner)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
        findings = store_module.load_security_findings(connection, outcome.evaluation_run_id)
        evidence = store_module.load_case_evidence(connection, outcome.evaluation_run_id)
        submission_state = connection.execute(
            sa.select(submissions.c.state).where(
                submissions.c.submission_id == uuid.UUID(str(row.submission_id))
            )
        ).scalar_one()

    assert row is not None
    assert (outcome.state, outcome.terminal_reason) == ("rejected", "security_blocked")
    assert (row.state, row.terminal_reason) == ("rejected", "security_blocked")
    assert [item.to_state for item in history] == ["queued", "running", "rejected"]
    assert submission_state == "rejected"

    # 不给分、不给报告：拒绝的运行没有分数可读（0 分会把"违规"读成"没做对"）。
    assert outcome.score is None and outcome.is_pass is None and outcome.report is None
    assert outcome.result_digest is None
    assert _result_row(engine, outcome.evaluation_run_id) is None
    assert rig.orchestrator.read_report(outcome.evaluation_run_id) is None

    # 违规被记成**运行期**安全发现（G-L1-7 的落库落点），而不是只活在这一帧的内存里。
    assert len(findings) == 1
    assert findings[0].detected_in == "runtime"
    assert findings[0].category == "sandbox_violation"
    assert findings[0].rule_id == "runner.seccomp"
    assert findings[0].blocking is True

    # 违规即终止：第 3 个用例不再执行；第 1 个用例已落库的证据仍在（事实不因后续拒绝而消失）。
    assert len(runner.calls) == 2
    assert outcome.runner_invocations == 2 and outcome.case_evidence_written == 1
    assert [item.ordinal for item in evidence] == [0]

    subjects = [item["subject"] for item in _outbox_rows(engine, outcome.evaluation_run_id)]
    assert SUBJECT_COMPLETED not in subjects, "安全拒绝绝不能对外发布 completed"
    assert SUBJECT_FAILED not in subjects, "安全拒绝不是技术失败（failed 事件只表达技术故障）"
    terminal = [
        frame
        for frame in _frames(engine, outcome.evaluation_run_id)
        if frame.event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
    ]
    assert [frame.event for frame in terminal] == [EVENT_REJECTED]
    assert terminal[0].terminal_reason == "security_blocked"
    assert terminal[0].score is None


def test_scoring_refuses_a_security_blocked_case_instead_of_awarding_zero() -> None:
    """第二道闸：安全违规的用例**不可能**被评成"这条 0 分"。

    编排层的升格是第一道闸；这里直接把它绕过去，验证评分层自己也会拒绝。两道闸的理由不同：
    第一道决定终态（`rejected`），第二道保证"升格被误删"不会静默变成一份**有分数的**报告。
    """
    evidence = tuple(
        replace(item, passed=False, failure_category="security_blocked")
        if item.ordinal == 0
        else item
        for item in _report_evidence()
    )
    with pytest.raises(ScoringPreconditionError):
        score_run(
            cases=_REPORT_CASES,
            evidence=evidence,
            static=_static_result(),
            security=_clean_decision(),
            comparison=ComparisonPolicy(stdout="exact"),
        )


def test_unchecked_security_fails_closed_instead_of_scoring(engine: Engine, tmp_path: Path) -> None:
    """"未检查"不等于安全：存在未执行的安全检查 ⇒ failed/infrastructure_error，绝不评分。

    三态语义的硬要求：`not_checked` 关闸且落 **failed**（技术原因），**不是** `static_blocked`
    ——"没检查"与"发现高危被拒"必须区分。
    """
    rig, outcome = _run_once(engine, tmp_path, decision=_unchecked_decision())
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
        findings = store_module.load_security_findings(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.terminal_reason) == ("failed", "infrastructure_error")
    assert outcome.terminal_reason not in {"static_blocked", "security_blocked"}
    assert outcome.score is None and rig.runner.calls == []
    assert findings == []  # 未检查不产生"发现"，更不得被表述为"未发现风险"
    assert [item.to_state for item in history] == ["queued", "failed"]
    assert "running" not in [item.to_state for item in history]
    assert _result_row(engine, outcome.evaluation_run_id) is None
    subjects = [row_["subject"] for row_ in _outbox_rows(engine, outcome.evaluation_run_id)]
    assert SUBJECT_FAILED in subjects and SUBJECT_COMPLETED not in subjects
    terminal = [
        frame
        for frame in _frames(engine, outcome.evaluation_run_id)
        if frame.event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
    ]
    assert [frame.event for frame in terminal] == [EVENT_FAILED]
    assert terminal[0].terminal_reason == "infrastructure_error"


def test_policy_over_the_resource_ledger_is_fail_closed_through_the_pipeline(
    engine: Engine, tmp_path: Path
) -> None:
    """越界策略必须 fail-closed：runner 抛 `SandboxUnavailableError` ⇒ `failed`，不算学生失败。

    接线事实（由 runner 侧确认）：数值账本上限是 fail-closed 的——越界策略在
    `assert_policy_executable`/`render_nsjail_config`/`probe_sandbox`/`build_argv`/package 解析/`run_case`
    全部被拒，且不产出任何配置字节。本用例验证**编排一侧**的处置：`SandboxUnavailableError` ⇒
    `failed + infrastructure_error`、**不写** `evaluation_results`、不发 `evaluation.completed`。
    真实 runner 的越界判定由 runner 自己的用例覆盖；这里由**替身**表达该失败类别（不是交付实现）。
    """
    runner = _ScriptedCaseRunner(
        failure=SandboxUnavailableError("policy exceeds the resource ledger")
    )
    rig, outcome = _run_once(engine, tmp_path, runner=runner)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, outcome.terminal_reason) == ("failed", "infrastructure_error")
    assert row.state == "failed" and row.state != "rejected"
    assert outcome.score is None and outcome.is_pass is None and outcome.report is None
    assert [item.to_state for item in history] == ["queued", "running", "failed"]
    assert len(runner.calls) == 1  # 只尝试了一个用例即终止，不继续跑其余用例
    assert _result_row(engine, outcome.evaluation_run_id) is None
    assert rig.orchestrator.read_report(outcome.evaluation_run_id) is None
    subjects = [item["subject"] for item in _outbox_rows(engine, outcome.evaluation_run_id)]
    assert SUBJECT_COMPLETED not in subjects and SUBJECT_FAILED in subjects
    # 失败原因脱敏：不回显内部路径/策略字节，只给固定告知文本。
    failed_payload = next(
        item["payload"]
        for item in _outbox_rows(engine, outcome.evaluation_run_id)
        if item["subject"] == SUBJECT_FAILED
    )
    assert failed_payload["terminal_reason"] == "infrastructure_error"
    assert SOURCE_MARKER not in json.dumps(failed_payload, ensure_ascii=False)


# --- 3. 沙箱不可用与 0 分 -----------------------------------------------------------------


def test_sandbox_unavailable_fails_with_infrastructure_error(engine: Engine, tmp_path: Path) -> None:
    """硬规则 3：沙箱不可用 ⇒ failed/infrastructure_error（不是 rejected，也不是 0 分）。"""
    runner = _ScriptedCaseRunner(failure=SandboxUnavailableError("nsjail missing"))
    rig, outcome = _run_once(engine, tmp_path, runner=runner)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        history = run_history(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.state) == ("failed", "failed")
    assert (row.terminal_reason, outcome.terminal_reason) == (
        "infrastructure_error",
        "infrastructure_error",
    )
    assert row.state != "rejected"
    assert [item.to_state for item in history] == ["queued", "running", "failed"]
    assert len(runner.calls) == 1
    assert outcome.score is None and outcome.is_pass is None
    assert outcome.report is None
    assert rig.orchestrator.read_report(outcome.evaluation_run_id) is None
    assert _result_row(engine, outcome.evaluation_run_id) is None
    terminal = [frame for frame in _frames(engine, outcome.evaluation_run_id) if frame.event == EVENT_FAILED]
    assert len(terminal) == 1 and terminal[0].terminal_reason == "infrastructure_error"
    assert terminal[0].score is None


def test_zero_score_is_completed_not_rejected_or_failed(engine: Engine, tmp_path: Path) -> None:
    """0 分是**学生结果**：全部用例未通过也只能是 `completed`（当前库的 schema 缺陷见模块 docstring）。"""
    cases = [(str(uuid.uuid4()), 0, 4.0, True), (str(uuid.uuid4()), 1, 4.0, False)]
    runner = _ScriptedCaseRunner(default_passed=False)
    _, outcome = _run_once(engine, tmp_path, cases=cases, runner=runner)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        evidence = store_module.load_case_evidence(connection, outcome.evaluation_run_id)
    assert row is not None
    assert [item.awarded for item in evidence] == [Decimal(0), Decimal(0)]
    assert [item.failure_category for item in evidence] == ["output_mismatch", "output_mismatch"]
    # 0 分是学生结果：状态必须是 completed（不是 rejected/failed），且 is_pass 为假。
    assert (outcome.state, row.state) == ("completed", "completed")
    assert outcome.score == Decimal(0) and outcome.is_pass is False
    assert outcome.terminal_reason is None
    assert outcome.report is not None and outcome.result_digest is not None
    assert _result_row(engine, outcome.evaluation_run_id) is not None


def test_all_cases_pass_scores_full_marks_and_completes(engine: Engine, tmp_path: Path) -> None:
    """全部用例通过 ⇒ `completed` + 满分 + `is_pass=True` + 报告与结果行都就位。"""
    cases = [(str(uuid.uuid4()), 0, 4.0, True), (str(uuid.uuid4()), 1, 6.0, False)]
    _, outcome = _run_once(engine, tmp_path, cases=cases)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, row.state) == ("completed", "completed")
    assert outcome.score == Decimal(10) and outcome.is_pass is True
    assert outcome.terminal_reason is None
    assert outcome.report is not None and outcome.result_digest is not None
    assert report_schema_errors(outcome.report) == []
    assert row.package_digest == _package_digest() and row.runner_policy_digest == DIGEST_POLICY
    assert row.environment_digest == DIGEST_ENV
    assert row.question_version_binding == DIGEST_QUESTION
    assert row.started_at is not None and row.finished_at is not None
    stored = _result_row(engine, outcome.evaluation_run_id)
    assert stored is not None and str(stored.result_digest) == outcome.result_digest


def test_schema_accepts_truthful_completion_and_rejects_fake_failure_reason(engine: Engine) -> None:
    """schema 语义（迁移 0007）：真实 `completed` 可写；用失败原因冒充成功完成必被拒。"""
    assert _completed_row_accepted(engine, None) is True
    assert _completed_row_accepted(engine, "internal_error") is False
    # 帧同理：completed 帧不得带失败原因，但必须有分数（ck_progress_events_completed_has_score）。
    submission_id, question_version_id = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(submission_id),
            question_version_id=str(question_version_id),
            submission_digest=DIGEST_SUBMISSION,
            occurred_at=BASE,
        )
        frame = append_frame(
            connection,
            evaluation_run_id=run_id,
            event=EVENT_COMPLETED,
            state="completed",
            stage="terminal",
            occurred_at=BASE,
            score=7,
            is_pass=False,
            terminal_reason=None,
        )
    assert frame.event == EVENT_COMPLETED and frame.terminal_reason is None
    assert frame.score == Decimal(7) and frame.is_pass is False
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            progress_events.insert().values(
                progress_event_id=uuid.uuid4(),
                evaluation_run_id=uuid.UUID(run_id),
                sequence=99,
                event=EVENT_COMPLETED,
                state="completed",
                stage="terminal",
                score=Decimal(1),
                is_pass=True,
                terminal_reason="internal_error",
                message=None,
                occurred_at=BASE,
            )
        )


# --- 4. 进度帧 ----------------------------------------------------------------------------


def test_terminal_state_and_reason_must_be_a_contract_pair(engine: Engine) -> None:
    """迁移 0009：`state` 与其 `terminal_reason` 必须**成对**（阻断类伴 `rejected`、技术类伴 `failed`）。

    此前三层（`store` / `progress` / 0007 的 CHECK）都只校验"枚举成员 + 非空"，于是
    `rejected + infrastructure_error` 这类自相矛盾的行可以落库：状态说"被拒绝（静态/安全阻断）"，
    原因却说"技术故障"，SSE 帧、统计与告警都无法判断到底发生了什么。本用例钉住三层的配对语义。
    """
    from autograder_b2.evaluation import TerminalReasonMismatch

    submission_id, question_version_id = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(submission_id),
            question_version_id=str(question_version_id),
            submission_digest=DIGEST_SUBMISSION,
            occurred_at=BASE,
        )
        transition_run(
            connection, evaluation_run_id=run_id, to_state="running", occurred_at=BASE
        )
        # 代码层（store）：配对不符即拒，且**不**改写原因
        with pytest.raises(TerminalReasonMismatch):
            transition_run(
                connection,
                evaluation_run_id=run_id,
                to_state="rejected",
                occurred_at=BASE,
                terminal_reason="infrastructure_error",
            )
        # 正例：`failed` + 技术类原因可写
        transition_run(
            connection,
            evaluation_run_id=run_id,
            to_state="failed",
            occurred_at=BASE,
            terminal_reason="infrastructure_error",
        )

    # 代码层（progress）：帧的配对同理
    with engine.begin() as connection, pytest.raises(FrameRejected):
        append_frame(
            connection,
            evaluation_run_id=run_id,
            event=EVENT_REJECTED,
            state="rejected",
            stage="terminal",
            occurred_at=BASE,
            terminal_reason="infrastructure_error",
        )

    # 数据库层：即使绕过代码，CHECK 也必须拒绝（`rejected` 行 + 技术类原因）
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            evaluation_runs.insert().values(
                evaluation_run_id=uuid.uuid4(),
                submission_id=submission_id,
                state="rejected",
                stage="terminal",
                sequence=1,
                cases_total=None,
                cases_done=0,
                terminal_reason="infrastructure_error",
                submission_digest=DIGEST_SUBMISSION,
                question_version_id=question_version_id,
                started_at=BASE,
                finished_at=BASE,
                created_at=BASE,
                updated_at=BASE,
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            progress_events.insert().values(
                progress_event_id=uuid.uuid4(),
                evaluation_run_id=uuid.UUID(run_id),
                sequence=97,
                event=EVENT_FAILED,
                state="failed",
                stage="terminal",
                score=None,
                is_pass=None,
                terminal_reason="static_blocked",
                message=None,
                occurred_at=BASE,
            )
        )


def test_frame_stream_returns_at_once_for_already_terminal_runs(engine: Engine, tmp_path: Path) -> None:
    """SSE 重连语义：运行已在终态且客户端已读到终态帧 ⇒ 立刻结束，不空等 `idle_timeout`。

    组级复核实测过旧行为：终态之后带 `Last-Event-ID` 重连会空等满 30.44s 才关流、0 帧，
    客户端无法据"连接是否立刻结束"判断运行已终结。
    """
    _rig, outcome = _run_once(engine, tmp_path)
    frames = list(
        progress_module.frame_stream(engine=engine, evaluation_run_id=outcome.evaluation_run_id)
    )
    assert frames and frames[-1].event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
    last = frames[-1].sequence

    def _explode(_seconds: float) -> None:
        raise AssertionError("已在终态的重连不得进入等待循环")

    again = list(
        progress_module.frame_stream(
            engine=engine,
            evaluation_run_id=outcome.evaluation_run_id,
            after=last,
            sleep=_explode,
            idle_timeout=30.0,
        )
    )
    assert again == [], "客户端已读到终态帧后重连不得再产出帧"

    # 反例：客户端**还没**读到终态帧（after 小于终态序号）时必须补发，直到终态
    replay = list(
        progress_module.frame_stream(
            engine=engine,
            evaluation_run_id=outcome.evaluation_run_id,
            after=last - 1,
            sleep=_explode,
            idle_timeout=30.0,
        )
    )
    assert [frame.sequence for frame in replay] == [last], "未读到的终态帧必须补发"


def test_progress_frames_are_monotonic_with_single_terminal_frame(
    engine: Engine, tmp_path: Path
) -> None:
    """帧序号严格单调（每次 +1）、终态帧恰好一个、终态帧必带原因。"""
    cases = [(str(uuid.uuid4()), 0, 2.0, True), (str(uuid.uuid4()), 1, 2.0, True)]
    rig, outcome = _run_once(engine, tmp_path, cases=cases)
    frames = _frames(engine, outcome.evaluation_run_id)
    sequences = [frame.sequence for frame in frames]
    assert sequences == list(range(1, len(frames) + 1))
    terminal = [
        frame for frame in frames if frame.event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
    ]
    assert len(terminal) == 1
    # 终态帧必带"为什么结束"：`completed` 用 null 表达"没有失败原因"，其余终态用受枚举限制的原因。
    if terminal[0].event == EVENT_COMPLETED:
        assert terminal[0].terminal_reason is None
        assert terminal[0].score == Decimal(4) and terminal[0].is_pass is True
    else:
        assert terminal[0].terminal_reason in TERMINAL_REASONS
    assert all(frame.event in FRAME_EVENTS for frame in frames)
    assert frames[-1].stage == "terminal"
    assert [frame.stage for frame in frames][:2] == ["queued", "fetching_package"]
    assert rig.runner.calls and len(rig.runner.calls) == 2


def test_progress_frame_reconnect_read_does_not_repeat_or_skip(
    engine: Engine, tmp_path: Path
) -> None:
    """重连读取（`after=N`）不重复、不遗漏：整段读 = 前缀 + 后缀且序列连续。"""
    _, outcome = _run_once(engine, tmp_path)
    all_frames = _frames(engine, outcome.evaluation_run_id)
    assert len(all_frames) >= 4
    pivot = all_frames[2].sequence
    with engine.connect() as connection:
        tail = read_frames(connection, evaluation_run_id=outcome.evaluation_run_id, after=pivot)
        head = read_frames(connection, evaluation_run_id=outcome.evaluation_run_id, after=0, limit=pivot)
    assert [frame.sequence for frame in tail] == [
        frame.sequence for frame in all_frames if frame.sequence > pivot
    ]
    assert all(frame.sequence > pivot for frame in tail)
    assert [frame.sequence for frame in head] == [1, 2, 3]
    assert [frame.sequence for frame in head + tail] == [
        frame.sequence for frame in all_frames
    ]


def test_frame_stream_stops_at_terminal_frame(engine: Engine, tmp_path: Path) -> None:
    """`frame_stream` 从 `after` 起按序产出、遇终态帧即结束（SSE 语义），重连续读不重复。"""
    rig, outcome = _run_once(engine, tmp_path)
    streamed = list(
        rig.orchestrator.frame_stream(outcome.evaluation_run_id, poll_interval=0.01, idle_timeout=0.05)
    )
    assert streamed and streamed[-1].event in (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
    assert [frame.sequence for frame in streamed] == list(range(1, len(streamed) + 1))
    resumed = list(
        rig.orchestrator.frame_stream(
            outcome.evaluation_run_id,
            after=streamed[0].sequence,
            poll_interval=0.01,
            idle_timeout=0.05,
        )
    )
    assert resumed == streamed[1:]


def test_frame_writer_rejects_illegal_frames(engine: Engine) -> None:
    """帧写入前校验：非法事件/状态/阶段、progress 带分数、终态缺原因、重复终态帧都被拒。"""
    submission_id, question_version_id = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(submission_id),
            question_version_id=str(question_version_id),
            submission_digest=DIGEST_SUBMISSION,
            occurred_at=BASE,
        )

    def write(**overrides: Any) -> ProgressFrame:
        payload: dict[str, Any] = {
            "evaluation_run_id": run_id,
            "event": EVENT_PROGRESS,
            "state": "queued",
            "stage": "queued",
            "occurred_at": BASE,
            "cases_done": 0,
        }
        payload.update(overrides)
        with engine.begin() as connection:
            return append_frame(connection, **payload)

    with pytest.raises(FrameRejected):
        write(event="bogus")
    with pytest.raises(FrameRejected):
        write(state="received")
    with pytest.raises(FrameRejected):
        write(stage="nowhere")
    with pytest.raises(FrameRejected):
        write(score=1)
    with pytest.raises(FrameRejected):
        write(event=EVENT_FAILED, terminal_reason=None)
    with pytest.raises(FrameRejected):
        write(event=EVENT_COMPLETED, terminal_reason="infrastructure_error", score=1)
    with pytest.raises(FrameRejected):
        write(cases_total=1, cases_done=2)
    frame = write(message="x" * 1500)
    assert frame.sequence == 1
    assert frame.message is not None and len(frame.message) == 1000
    with engine.begin() as connection:
        append_frame(
            connection,
            evaluation_run_id=run_id,
            event=EVENT_REJECTED,
            state="rejected",
            stage="terminal",
            occurred_at=BASE,
            terminal_reason="static_blocked",
        )
    with pytest.raises(TerminalFrameExists), engine.begin() as connection:
        append_frame(
            connection,
            evaluation_run_id=run_id,
            event=EVENT_FAILED,
            state="failed",
            stage="terminal",
            occurred_at=BASE,
            terminal_reason="internal_error",
        )


def test_frame_message_redaction_hides_internal_paths() -> None:
    """帧 `message` 必须脱敏：内部绝对路径被替换为占位符。"""
    redacted = progress_module.redact_message("/home/grader/autograder/workspace/main.py failed")
    assert redacted is not None and "/home/" not in redacted and "<redacted-path>" in redacted
    assert progress_module.redact_message(None) is None


def test_concurrent_frame_allocation_never_repeats_a_sequence(engine: Engine) -> None:
    """并发取号：8 个线程同时追加帧，序号必须恰好是 1..8（无重复、无空洞）。"""
    submission_id, question_version_id = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(submission_id),
            question_version_id=str(question_version_id),
            submission_digest=DIGEST_SUBMISSION,
            occurred_at=BASE,
        )
    workers = 8
    errors: list[BaseException] = []
    barrier = threading.Barrier(workers)

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            with engine.begin() as connection:
                append_frame(
                    connection,
                    evaluation_run_id=run_id,
                    event=EVENT_PROGRESS,
                    state="queued",
                    stage="queued",
                    occurred_at=BASE,
                    cases_done=0,
                    message=f"worker {index}",
                )
        except BaseException as error:  # noqa: BLE001 - 线程内异常必须回传主线程断言
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    with engine.connect() as connection:
        sequences = (
            connection.execute(
                sa.select(progress_events.c.sequence)
                .where(progress_events.c.evaluation_run_id == uuid.UUID(run_id))
                .order_by(progress_events.c.sequence)
            )
            .scalars()
            .all()
        )
    assert sorted(sequences) == list(range(1, workers + 1))
    assert len(set(sequences)) == workers


def test_frames_never_carry_source_and_match_contract_shape(engine: Engine, tmp_path: Path) -> None:
    """帧内不得含源码：待测源码标记串不出现在任何帧字段里；帧字段集合与契约一致。"""
    _, outcome = _run_once(engine, tmp_path)
    frames = _frames(engine, outcome.evaluation_run_id)
    assert frames
    payloads = [frame.as_contract_frame() for frame in frames]
    blob = json.dumps(payloads, ensure_ascii=False)
    assert SOURCE_MARKER not in blob
    assert "print(" not in blob
    assert set(payloads[0]) == {
        "evaluation_run_id",
        "event",
        "state",
        "stage",
        "sequence",
        "cases_total",
        "cases_done",
        "score",
        "is_pass",
        "terminal_reason",
        "message",
        "occurred_at",
    }


# --- 5. 报告 ------------------------------------------------------------------------------


def test_report_passes_main_contract_and_total_check() -> None:
    """T-REPORT-001/005：报告**真的**过 MAIN 契约校验，且合计校验成立（逐项之和 = 总分）。"""
    report = _report()
    assert report_schema_errors(report) == []
    validate_report(report)
    totals = report["total_check"]
    assert isinstance(totals, dict) and totals["matches_total"] is True
    overall = report["overall"]
    assert isinstance(overall, dict)
    awards = [Decimal(str(item["awarded"])) for item in report["case_evidence"]]  # type: ignore[union-attr]
    assert sum(awards) == Decimal(str(overall["score"]))
    assert totals["sum_of_awards"] == overall["score"]
    assert Decimal(str(overall["max_score"])) == Decimal(10)
    assert overall["score"] == 6.0 and overall["is_pass"] is False
    assert report["state"] == "completed"
    assert report["binding"]["package_digest"] == DIGEST_PACKAGE  # type: ignore[index]


def test_report_total_check_refuses_a_mismatched_total() -> None:
    """合计不符必须**失败**（不是四舍五入）：报告侧独立加总与总分不符即抛 `ScoreTotalMismatch`。"""
    awards = (
        CaseAward(
            case_id=FIXED_VISIBLE_CASE,
            ordinal=0,
            weight=Decimal(6),
            awarded=Decimal(6),
            passed=True,
            failure_category=None,
        ),
    )
    total, matches = build_total_check(awards, Decimal("5.5"))
    assert total == Decimal(6) and matches is False
    inputs = _report_inputs()
    tampered = replace(inputs.breakdown, score=inputs.breakdown.score + Decimal(1))
    with pytest.raises(ScoreTotalMismatch):
        build_report(replace(inputs, breakdown=tampered), generated_at=BASE)


def test_report_digest_is_deterministic_for_same_inputs() -> None:
    """`result_digest` 对同输入确定；换 `generated_at`/`duration_ms` 也不变（可重放判据）。"""
    first = _report()
    second = _report()
    assert first == second
    assert first["result_digest"] == second["result_digest"]
    later = _report(generated_at=BASE + timedelta(hours=3))
    assert later["generated_at"] != first["generated_at"]
    assert later["result_digest"] == first["result_digest"]
    slower = _report(duration_ms=999)
    assert slower["case_evidence"][0]["duration_ms"] == 999  # type: ignore[index]
    assert slower["result_digest"] == first["result_digest"]
    # 摘要确实覆盖分数内容：改一个逐项得分，摘要必须变。
    tampered = json.loads(json.dumps(first))
    tampered["case_evidence"][0]["awarded"] = 1.0
    assert report_digest(tampered) != first["result_digest"]


def test_report_rejected_for_non_completed_state() -> None:
    """只有 `completed` 产出报告：`rejected`/`failed` 没有分数，也不该假装有报告。"""
    from autograder_b2.evaluation import ReportRejected

    with pytest.raises(ReportRejected):
        build_report(replace(_report_inputs(), state="rejected"), generated_at=BASE)


def test_report_digest_discriminates_different_bindings() -> None:
    """摘要必须保住区分力：同绑定摘要 ⇒ 同摘要；任一绑定摘要或任一逐项得分不同 ⇒ 摘要不同。

    这条是"排除 `evaluation_run_id` 等易变字段"的对照面：排得太多会让摘要失去区分力，这里把它钉住。
    """
    baseline = _report()
    assert _report()["result_digest"] == baseline["result_digest"]  # 同输入（含同五项绑定摘要）

    def _with_binding(**overrides: str) -> dict[str, object]:
        inputs = _report_inputs()
        binding = replace(inputs.binding, **overrides)
        return build_report(replace(inputs, binding=binding), generated_at=BASE)

    assert _with_binding(submission_digest=_digest("another-submission"))["result_digest"] != (
        baseline["result_digest"]
    )
    assert _with_binding(package_digest=_digest("another-package"))["result_digest"] != (
        baseline["result_digest"]
    )
    assert _with_binding(runner_policy_digest=_digest("another-policy"))["result_digest"] != (
        baseline["result_digest"]
    )
    assert _with_binding(environment_digest=_digest("another-environment"))["result_digest"] != (
        baseline["result_digest"]
    )
    assert _with_binding(question_version_binding=_digest("another-question"))["result_digest"] != (
        baseline["result_digest"]
    )
    # 逐项得分改变 ⇒ 摘要改变（分数与证据都在投影里）。
    tampered = json.loads(json.dumps(baseline))
    tampered["case_evidence"][1]["passed"] = True
    tampered["case_evidence"][1]["awarded"] = 4.0
    assert report_digest(tampered) != baseline["result_digest"]
    # 静态安全结论文本改变 ⇒ 摘要改变。
    tampered_again = json.loads(json.dumps(baseline))
    tampered_again["security_conclusion"]["status"] = "not_checked"
    assert report_digest(tampered_again) != baseline["result_digest"]


def test_student_view_hides_hidden_case_outputs() -> None:
    """T-REPORT-004：隐藏用例的期望/实际输出摘要从学生视图省略，教师视图保留。"""
    report = _report()
    student = for_student(report, visible_case_ids=(FIXED_VISIBLE_CASE,))
    teacher = for_teacher(report)
    hidden_student = next(
        item for item in student["case_evidence"] if item["case_id"] == FIXED_HIDDEN_CASE  # type: ignore[union-attr]
    )
    assert "expected_output_excerpt" not in hidden_student
    assert "actual_output_excerpt" not in hidden_student
    assert HIDDEN_EXPECTED not in json.dumps(student, ensure_ascii=False)
    visible_student = next(
        item for item in student["case_evidence"] if item["case_id"] == FIXED_VISIBLE_CASE  # type: ignore[union-attr]
    )
    assert visible_student["expected_output_excerpt"] == "VISIBLE_EXPECTED"
    hidden_teacher = next(
        item for item in teacher["case_evidence"] if item["case_id"] == FIXED_HIDDEN_CASE  # type: ignore[union-attr]
    )
    assert hidden_teacher["expected_output_excerpt"] == HIDDEN_EXPECTED
    # 默认（不给可见清单）是 fail-closed：一个用例的期望输出都不暴露。
    fail_closed = for_student(report)
    assert all("expected_output_excerpt" not in item for item in fail_closed["case_evidence"])  # type: ignore[union-attr]
    # 裁剪是深拷贝，不改原报告；裁剪后的报告仍过契约校验。
    assert report["case_evidence"][1]["expected_output_excerpt"] == HIDDEN_EXPECTED  # type: ignore[index]
    validate_report(student)
    validate_report(teacher)


# --- 6. 可重放 ----------------------------------------------------------------------------


def test_replay_same_bindings_same_awards_and_evidence(engine: Engine, tmp_path: Path) -> None:
    """T-REPORT-003 / T-RUN-005：同一批绑定摘要重复编排 ⇒ 同逐项得分、同证据摘要（同结果摘要）。"""
    cases = [(str(uuid.uuid4()), 0, 3.0, True), (str(uuid.uuid4()), 1, 7.0, False)]
    runner = _ScriptedCaseRunner(plan={cases[1][0]: {"passed": False}})
    submission_id, question_version_id = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    envelope = _envelope(
        submission_id=submission_id, question_version_id=question_version_id, contents=contents
    )
    observed: list[dict[str, Any]] = []
    for _ in range(2):
        rig = _rig(engine, tmp_path, cases=cases, contents=contents, runner=runner)
        outcome = rig.run(envelope)
        with engine.connect() as connection:
            row = store_module.load_run(connection, outcome.evaluation_run_id)
            rows = store_module.load_case_evidence(connection, outcome.evaluation_run_id)
        assert row is not None
        observed.append(
            {
                "outcome": outcome,
                "binding": (
                    row.submission_digest,
                    row.question_version_binding,
                    row.package_digest,
                    row.runner_policy_digest,
                    row.environment_digest,
                ),
                "awards": [item.awarded for item in rows],
                # `duration_ms` 被 runner 排除在 evidence_digest 之外（墙钟值），这里只比对摘要本身。
                "evidence_digests": [item.evidence_digest for item in rows],
                "ordinals": [item.ordinal for item in rows],
            }
        )
    first, second = observed
    assert first["binding"] == second["binding"]
    assert first["awards"] == second["awards"] == [Decimal(3), Decimal(0)]
    assert first["evidence_digests"] == second["evidence_digests"]
    assert first["ordinals"] == second["ordinals"] == [0, 1]
    # 同绑定摘要 ⇒ 同分数、同结果摘要、同逐项证据（`ADR-0005` 第 5 条）。
    assert first["outcome"].state == second["outcome"].state == "completed"
    assert first["outcome"].score == second["outcome"].score == Decimal(3)
    assert first["outcome"].result_digest == second["outcome"].result_digest
    assert first["outcome"].is_pass is False and second["outcome"].is_pass is False
    assert first["outcome"].report is not None and second["outcome"].report is not None
    first_cases = first["outcome"].report["case_evidence"]
    second_cases = second["outcome"].report["case_evidence"]
    assert first_cases == second_cases  # 逐项证据（含权重、得分、失败类别）逐字相同


# --- 7. 报告持久化 ------------------------------------------------------------------------


def test_report_is_not_claimed_when_result_write_fails(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-PERSIST-005：制造结果写入失败 ⇒ 不对外声称报告完成（无结果行 ⇒ 无报告）。"""
    submission_id, question_version_id = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    envelope = _envelope(
        submission_id=submission_id, question_version_id=question_version_id, contents=contents
    )
    rig = _rig(engine, tmp_path, contents=contents)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated database write failure")

    monkeypatch.setattr(store_module, "persist_result", boom)
    outcome = rig.run(envelope)
    assert outcome.state == "failed"
    assert outcome.terminal_reason == "internal_error"
    assert outcome.report is None and outcome.result_digest is None
    assert _result_row(engine, outcome.evaluation_run_id) is None
    assert rig.orchestrator.read_report(outcome.evaluation_run_id) is None
    frames = _frames(engine, outcome.evaluation_run_id)
    assert [frame.event for frame in frames if frame.event != EVENT_PROGRESS] == [EVENT_FAILED]
    subjects = [row["subject"] for row in _outbox_rows(engine, outcome.evaluation_run_id)]
    assert SUBJECT_COMPLETED not in subjects
    assert SUBJECT_FAILED in subjects


# --- 8. 并发闸门 --------------------------------------------------------------------------


def test_dynamic_execution_gate_limits_peak_concurrency_to_one(
    engine: Engine, tmp_path: Path
) -> None:
    """并发闸门：4 次并发编排各跑 2 个用例，沙箱作业并发峰值必须 ≤ 1。"""
    gate = DynamicExecutionGate()
    runner = _ScriptedCaseRunner(delay=0.05)
    cases = [(str(uuid.uuid4()), 0, 1.0, True), (str(uuid.uuid4()), 1, 1.0, True)]
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    rigs: list[_Rig] = []
    envelopes: list[SubmissionEnvelope] = []
    for _ in range(4):
        submission_id, question_version_id = _seed_submission(engine)
        envelopes.append(
            _envelope(
                submission_id=submission_id,
                question_version_id=question_version_id,
                contents=contents,
            )
        )
        rigs.append(_rig(engine, tmp_path, cases=cases, runner=runner, gate=gate, contents=contents))
    errors: list[BaseException] = []
    states: list[str] = []

    def worker(index: int) -> None:
        try:
            states.append(rigs[index].run(envelopes[index]).state)
        except BaseException as error:  # noqa: BLE001 - 线程内异常回传主线程断言
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert len(states) == 4
    assert gate.capacity == DYNAMIC_CONCURRENCY_LIMIT == 1
    assert gate.peak <= 1
    assert gate.current == 0
    assert gate.acquisitions == 8
    assert len(runner.calls) == 8
    workspaces = {call["workspace"] for call in runner.calls}
    assert len(workspaces) == 8


# --- 9. 迁移、证据与接线 ------------------------------------------------------------------


def test_illegal_run_transition_is_rejected_before_write(engine: Engine) -> None:
    """T-PERSIST-002：非法迁移在写入前被拒（`completed -> running` 等），历史不被污染。"""
    assert require_run_transition("queued", "failed") is None
    with pytest.raises(IllegalRunTransition):
        require_run_transition("completed", "running")
    with pytest.raises(IllegalRunTransition):
        require_run_transition("rejected", "queued")
    with pytest.raises(IllegalRunTransition):
        require_run_transition("failed", "running")
    with pytest.raises(IllegalRunTransition):
        require_run_transition(None, "running")

    submission_id, question_version_id = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(submission_id),
            question_version_id=str(question_version_id),
            submission_digest=DIGEST_SUBMISSION,
            occurred_at=BASE,
        )
        transition_run(
            connection,
            evaluation_run_id=run_id,
            to_state="running",
            occurred_at=BASE,
            reason="进入执行",
            stage="dynamic_execution",
        )
        before = run_history(connection, run_id)
    with pytest.raises(IllegalRunTransition), engine.begin() as connection:
        transition_run(
            connection, evaluation_run_id=run_id, to_state="queued", occurred_at=BASE
        )
    with engine.connect() as connection:
        after = run_history(connection, run_id)
        row = store_module.load_run(connection, run_id)
    assert [item.to_state for item in before] == ["queued", "running"]
    assert after == before
    assert [item.sequence for item in after] == [1, 2]
    assert row is not None and row.state == "running"


def test_run_transition_history_is_contiguous_and_reason_redacted(engine: Engine) -> None:
    """迁移史 `sequence` 从 1 起每次 +1；`reason` 已脱敏（内部路径被替换）。"""
    submission_id, question_version_id = _seed_submission(engine)
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=str(submission_id),
            question_version_id=str(question_version_id),
            submission_digest=DIGEST_SUBMISSION,
            occurred_at=BASE,
        )
        transition_run(
            connection,
            evaluation_run_id=run_id,
            to_state="running",
            occurred_at=BASE,
            reason="工作区 /home/grader/ws/case-1 就绪",
            stage="dynamic_execution",
        )
        transition_run(
            connection,
            evaluation_run_id=run_id,
            to_state="failed",
            occurred_at=BASE,
            reason="沙箱不可用",
            terminal_reason="infrastructure_error",
        )
    with engine.connect() as connection:
        history = run_history(connection, run_id)
        row = store_module.load_run(connection, run_id)
    assert [item.sequence for item in history] == [1, 2, 3]
    assert [item.to_state for item in history] == ["queued", "running", "failed"]
    assert history[1].reason is not None and "/home/" not in history[1].reason
    assert "<redacted-path>" in history[1].reason
    assert row is not None and row.terminal_reason == "infrastructure_error"
    assert row.finished_at is not None


def test_case_evidence_is_persisted_per_ordinal_and_unique(engine: Engine, tmp_path: Path) -> None:
    """逐用例证据落库且 `(run, ordinal)` 唯一（重复序被约束拒绝）。"""
    cases = [(str(uuid.uuid4()), 0, 2.5, True), (str(uuid.uuid4()), 1, 7.5, True)]
    rig, outcome = _run_once(engine, tmp_path, cases=cases)
    with engine.connect() as connection:
        rows = store_module.load_case_evidence(connection, outcome.evaluation_run_id)
    assert [item.ordinal for item in rows] == [0, 1]
    assert [item.weight for item in rows] == [Decimal("2.5"), Decimal("7.5")]
    assert [item.awarded for item in rows] == [Decimal("2.5"), Decimal("7.5")]
    assert all(item.passed and item.failure_category is None for item in rows)
    assert all(item.input_digest and item.stdout_digest and item.evidence_digest for item in rows)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            case_evidence.insert().values(
                case_evidence_id=uuid.uuid4(),
                evaluation_run_id=uuid.UUID(outcome.evaluation_run_id),
                case_id=uuid.UUID(rows[0].case_id),
                ordinal=0,
                weight=Decimal(1),
                passed=True,
                failure_category=None,
                awarded=Decimal(1),
                input_digest=_digest("i"),
                stdout_digest=_digest("o"),
                stderr_digest=_digest("e"),
                actual_output_excerpt=None,
                expected_output_excerpt=None,
                exit_code=0,
                duration_ms=1,
                resource_limit_hit=None,
                message=None,
                evidence_digest=_digest("d"),
                occurred_at=BASE,
            )
        )
    assert rig.runner.calls


def test_per_case_input_and_workspace_are_isolated(engine: Engine, tmp_path: Path) -> None:
    """接线约定：每用例一份 `SubmissionRunInput`、一份独立工作区，句柄原样来自 package。"""
    cases = [(str(uuid.uuid4()), 0, 1.0, True), (str(uuid.uuid4()), 1, 1.0, True)]
    runner = _ScriptedCaseRunner()
    rig, outcome = _run_once(engine, tmp_path, cases=cases, runner=runner)
    assert outcome is not None
    assert len(runner.calls) == 2
    workspaces = {call["workspace"] for call in runner.calls}
    assert len(workspaces) == 2
    assert set(rig.workspaces.prepared) >= workspaces
    assert set(rig.workspaces.released) >= workspaces
    assert [str(call["case"].case_id) for call in runner.calls] == [case[0] for case in cases]
    view = rig.packages.view
    assert view is not None
    assert all(call["policy"] is view.policy_handle for call in runner.calls)
    assert all(call["adapter"] is view.adapter_handle for call in runner.calls)
    assert all(call["package"] is view.handle for call in runner.calls)
    assert {id(call["case"]) for call in runner.calls} == {id(case.handle) for case in view.cases}


def test_case_level_fixture_failure_is_a_system_failure_not_a_student_failure(
    engine: Engine, tmp_path: Path
) -> None:
    """接线约定：runner 返回 `infrastructure_error` 证据（fixture 缺失）⇒ 技术故障，不算学生失败。"""
    first_case = (str(uuid.uuid4()), 0, 5.0, True)
    broken_case = (str(uuid.uuid4()), 1, 5.0, True)
    runner = _ScriptedCaseRunner(
        plan={broken_case[0]: {"passed": False, "failure_category": "infrastructure_error"}}
    )
    rig, outcome = _run_once(engine, tmp_path, cases=[first_case, broken_case], runner=runner)
    with engine.connect() as connection:
        row = store_module.load_run(connection, outcome.evaluation_run_id)
        evidence = store_module.load_case_evidence(connection, outcome.evaluation_run_id)
    assert row is not None
    assert (outcome.state, outcome.terminal_reason) == ("failed", "infrastructure_error")
    assert outcome.score is None and outcome.is_pass is None
    # 技术故障的用例不落"0 分像学生没做对"的证据；此前正常完成的用例证据仍在。
    assert [item.ordinal for item in evidence] == [0]
    assert evidence[0].awarded == Decimal(5)
    assert len(runner.calls) == 2
    assert rig.orchestrator.read_report(outcome.evaluation_run_id) is None


# --- 10. outbox 事件 ----------------------------------------------------------------------


def test_outbox_events_only_carry_identifiers_counts_and_digests(
    engine: Engine, tmp_path: Path
) -> None:
    """事件集合：`started`/`progress`/`completed|failed`；payload 只含标识/计数/摘要，无源码。"""
    _, outcome = _run_once(engine, tmp_path)
    rows = _outbox_rows(engine, outcome.evaluation_run_id)
    subjects = [row["subject"] for row in rows]
    assert subjects.count(SUBJECT_STARTED) == 1
    assert SUBJECT_PROGRESS in subjects
    assert "autograder.v1.evaluation.rejected" not in subjects
    assert not any(subject.endswith("rejected") for subject in subjects)
    blob = json.dumps([row["payload"] for row in rows], ensure_ascii=False)
    assert SOURCE_MARKER not in blob
    assert "print(" not in blob
    assert "main.py" not in blob
    progress_payloads = [row["payload"] for row in rows if row["subject"] == SUBJECT_PROGRESS]
    assert progress_payloads
    assert all(
        set(payload) <= {"evaluation_run_id", "stage", "sequence", "cases_total", "cases_done"}
        for payload in progress_payloads
    )
    sequences = sorted(int(payload["sequence"]) for payload in progress_payloads)
    # started → progress：最小的 progress 序号对应 static_analysis 帧（queued/fetching 只走 SSE 帧）。
    first_progress = min(progress_payloads, key=lambda payload: int(payload["sequence"]))
    assert first_progress["stage"] == "static_analysis"
    assert sequences == list(range(3, 3 + len(sequences)))
    started = next(row for row in rows if row["subject"] == SUBJECT_STARTED)
    assert started["payload"]["package_digest"] == _package_digest()
    assert started["payload"]["stage"] == "static_analysis"


def test_rejected_run_enqueues_no_terminal_domain_event(engine: Engine, tmp_path: Path) -> None:
    """`rejected` 没有领域事件：只有 started/progress，终态经 SSE 帧送达。"""
    rig, outcome = _run_once(
        engine, tmp_path, contents={"main.py": BLOCKING_SOURCE.encode("utf-8")}
    )
    subjects = [row["subject"] for row in _outbox_rows(engine, outcome.evaluation_run_id)]
    assert SUBJECT_STARTED in subjects
    assert SUBJECT_COMPLETED not in subjects
    assert SUBJECT_FAILED not in subjects
    assert rig.runner.calls == []


def test_terminal_failure_enqueues_failed_event_without_source(engine: Engine, tmp_path: Path) -> None:
    """`failed` 运行必发 `evaluation.failed`（带脱敏失败类别），payload 不含源码。"""
    runner = _ScriptedCaseRunner(failure=SandboxUnavailableError("probe failed"))
    _, outcome = _run_once(engine, tmp_path, runner=runner)
    rows = _outbox_rows(engine, outcome.evaluation_run_id)
    failed = [row for row in rows if row["subject"] == SUBJECT_FAILED]
    assert len(failed) == 1
    payload = failed[0]["payload"]
    assert payload["terminal_reason"] == "infrastructure_error"
    assert payload["failure_category"] == "infrastructure_error"
    assert payload["stage"] == "terminal"
    assert SOURCE_MARKER not in json.dumps(payload, ensure_ascii=False)
    assert set(payload) <= {
        "submission_id",
        "evaluation_run_id",
        "question_version_id",
        "stage",
        "failure_category",
        "terminal_reason",
    }


# --- 11. 契约取值集合一致性 ----------------------------------------------------------------


def test_value_sets_match_main_contracts() -> None:
    """取值集合逐字来自契约：帧事件/状态/阶段/终态原因、比较策略、语言、运行状态机。"""
    contracts_root = Path(__file__).resolve().parents[2] / "MAIN" / "contracts"
    openapi = yaml.safe_load(
        (contracts_root / "openapi" / "b2.yaml").read_text(encoding="utf-8")
    )
    frame = openapi["components"]["schemas"]["EvaluationStreamFrame"]
    assert set(frame["properties"]["event"]["enum"]) == set(FRAME_EVENTS)
    assert set(frame["properties"]["stage"]["enum"]) == set(STAGES)
    assert set(frame["properties"]["terminal_reason"]["enum"]) - {None} == set(TERMINAL_REASONS)
    assert set(frame["properties"]["state"]["enum"]) >= {
        "queued",
        "running",
        "completed",
        "rejected",
        "failed",
    }
    package_schema = load_contract_schema(EVALUATION_PACKAGE_SCHEMA)
    comparison = package_schema["properties"]["adapter"]["properties"]["comparison"]["properties"]
    assert set(comparison["stdout"]["enum"]) == set(ports_module.STDOUT_STRATEGIES)
    assert set(comparison["stderr"]["enum"]) == set(ports_module.STDERR_STRATEGIES)
    assert set(package_schema["properties"]["language"]["enum"]) == set(ports_module.LANGUAGES)
    transitions = openapi["components"]["schemas"]["SubmissionTransitions"]
    assert set(transitions["properties"]["transitions"]["example"]) >= {"queued", "running"}


def test_ports_reject_contract_violations() -> None:
    """端口层面的契约前置条件在**调用实现之前**就被拒绝。"""
    with pytest.raises(ports_module.PortContractError):
        ComparisonPolicy(stdout="fuzzy")
    with pytest.raises(ports_module.PortContractError):
        ComparisonPolicy(stdout="exact", stderr="fuzzy")
    with pytest.raises(ports_module.PortContractError):
        CaseDescriptor(case_id=str(uuid.uuid4()), ordinal=0, weight=0)
    with pytest.raises(ports_module.PortContractError):
        CaseDescriptor(case_id="not-a-uuid", ordinal=0, weight=1)
    with pytest.raises(ports_module.PortContractError):
        _package_view([(str(uuid.uuid4()), 0, 1.0, True), (str(uuid.uuid4()), 0, 1.0, True)])
    with pytest.raises(ports_module.PortContractError):
        PackageView(
            package_digest="not-a-digest",
            language="python",
            cases=(CaseDescriptor(case_id=str(uuid.uuid4()), ordinal=0, weight=1.0),),
            runner_policy_digest=DIGEST_POLICY,
            environment_digest=DIGEST_ENV,
        )
    with pytest.raises(ports_module.PortContractError):
        PackageView(
            package_digest=DIGEST_PACKAGE,
            language="ruby",
            cases=(CaseDescriptor(case_id=str(uuid.uuid4()), ordinal=0, weight=1.0),),
            runner_policy_digest=DIGEST_POLICY,
            environment_digest=DIGEST_ENV,
        )


def test_locked_constants_do_not_drift() -> None:
    """锁定常量：动态并发上限 = 1、阶段集合、运行状态机（没有环境变量开关）。"""
    assert DYNAMIC_CONCURRENCY_LIMIT == 1
    assert STAGES[-1] == "terminal"
    assert set(RUN_TRANSITIONS) == {"queued", "running", "completed", "rejected", "failed"}
    assert RUN_TRANSITIONS["queued"] == ("running", "rejected", "failed")
    assert RUN_TRANSITIONS["running"] == ("completed", "rejected", "failed")
    assert RUN_TRANSITIONS["completed"] == ()
    assert set(TERMINAL_REASONS) == {
        "static_blocked",
        "security_blocked",
        "infrastructure_error",
        "internal_error",
    }


def test_orchestration_depends_only_on_ports_not_on_runner_or_security(engine: Engine) -> None:
    """结构断言：import 本包**不得**把 runner/security 的实现拉进来（否则"窄协议"只是装饰）。

    静态检查是**已交付且被直接调用**的依赖（`autograder_b2.static`），因此它必须在；安全判定与沙箱
    执行必须只在集成时注入——用独立进程导入来判定，避免被本测试文件自身的 import 干扰。
    """
    import subprocess
    import sys

    del engine
    probe = (
        "import sys, autograder_b2.evaluation;"
        "print('runner' if 'autograder_b2.runner' in sys.modules else 'no-runner');"
        "print('security' if 'autograder_b2.security' in sys.modules else 'no-security');"
        "print('static' if 'autograder_b2.static' in sys.modules else 'no-static')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert completed.returncode == 0, completed.stderr[-500:]
    lines = completed.stdout.split()
    assert lines == ["no-runner", "no-security", "static"]


@pytest.fixture(autouse=True)
def _workspace_root() -> Iterator[None]:
    """工作区根目录固定在工作区内（本环境的系统临时目录不可用）。"""
    Path(".pytest-tmp").mkdir(exist_ok=True)
    yield
