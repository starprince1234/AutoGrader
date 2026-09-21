"""运行、迁移史、逐用例证据与结果的持久化（编排层唯一的写库出口）。

职责边界：本模块只读写 `b2` schema 的表映像（`src/autograder_b2/db/schema.py`，**只使用、不修改**），
不持有业务判定（判定在 `orchestrator`/`scoring`/`report`）。

三条硬规则在这里落地：

1. **运行状态机**：`queued -> {running, rejected, failed}`、`running -> {completed, rejected, failed}`。
   允许 `queued` 直达终态，因为静态/安全阻断发生在进入沙箱之前；而没有运行实体就无法通过 SSE 发出
   `rejected` 终态帧。`transition_run` 在**写入前**用 `require_run_transition` 校验，非法迁移（例如
   `completed -> running`）直接抛 `IllegalRunTransition`，绝不落库。
2. **终态一致性**：终态必须有 `finished_at` 与 `terminal_reason`（`rejected`/`failed`），而 `completed`
   的 `terminal_reason` 必须是 `None`——语义与约束自**迁移 0007** 起一致（0006 曾把失败原因强加给成功态，
   使真实的 `completed` 落不了库）。`progress.schema_conflict` 这个兜底仍保留：库结构与契约不一致时
   如实归类，不写假值。
3. **报告可读 ⇒ 结果已持久化**：报告只从 `evaluation_results` 行读出（`read_report`），本模块**不提供**
   任何"重新生成报告"的读取路径。
4. **提交状态机与运行同步**：`advance_submission_state` 是评测侧推进 `b2.submissions.state` 的**唯一**
   出口（与 `domain.transitions.record_transitions` 同事务），使契约 `SubmissionTransitions` 的
   "静态高危必须在**不进入沙箱**的情况下被拒（`validated -> rejected`）"成为可核验的事实。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Connection

from autograder_b2.db.schema import (
    case_evidence,
    evaluation_results,
    evaluation_run_state_transitions,
    evaluation_runs,
    security_findings,
    static_findings,
    submissions,
)
from autograder_b2.domain import states
from autograder_b2.domain.clock import is_digest
from autograder_b2.domain.evidence import CaseEvidence
from autograder_b2.domain.security import SecurityDecision, SecurityFinding
from autograder_b2.domain.static_result import StaticAnalysisResult
from autograder_b2.domain.transitions import record_transitions
from autograder_b2.evaluation.progress import (
    RUN_STATES,
    STAGES,
    TERMINAL_REASONS_BY_STATE,
    redact_message,
)

#: 运行终态。
RUN_TERMINAL_STATES = ("completed", "rejected", "failed")

#: 三态安全结论的取值域（契约 `security_conclusion.status`）；迁移 0011 的 CHECK 同取值域。
SECURITY_STATUSES = ("found", "not_found", "not_checked")

#: 运行状态机（取值集合由本工作项锁定，见模块 docstring）。
RUN_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    "queued": ("running", "rejected", "failed"),
    "running": ("completed", "rejected", "failed"),
    "completed": (),
    "rejected": (),
    "failed": (),
}

#: 迁移史 reason 的 CHECK 上限。
REASON_LIMIT = 500


class IllegalRunTransition(ValueError):
    """不在锁定状态机内的运行迁移（含终态出发的任何迁移）。"""


class TerminalReasonMismatch(ValueError):
    """终态原因与目标状态不匹配（终态必填且限于枚举；非终态必须为空）。"""


class RunNotFoundError(LookupError):
    """运行不存在。"""


class ResultAlreadyPersisted(RuntimeError):
    """同一运行已有结果行（报告与结果一一对应，重复写是缺陷）。"""


def is_terminal_state(state: str) -> bool:
    return state in RUN_TERMINAL_STATES


def can_transition_run(from_state: str | None, to_state: str) -> bool:
    """`from_state is None` 表示运行实体创建（首次进入状态机，只能是 `queued`）。"""
    if from_state is None:
        return to_state == "queued"
    return to_state in RUN_TRANSITIONS.get(from_state, ())


def require_run_transition(from_state: str | None, to_state: str) -> None:
    """迁移校验器：不合法即抛 `IllegalRunTransition`（**写入前**拒绝）。"""
    if to_state not in RUN_STATES:
        raise IllegalRunTransition(f"未知运行状态：{to_state!r}")
    if not can_transition_run(from_state, to_state):
        raise IllegalRunTransition(f"运行迁移 {from_state!r} -> {to_state!r} 不在锁定的状态机内")


def _require_terminal_reason(to_state: str, terminal_reason: str | None) -> None:
    """终态原因的合法性（**契约语义**，不是数据库 CHECK 的复述）。

    契约（`EvaluationStreamFrame.terminal_reason`）：`static_blocked`/`security_blocked` 伴随
    `rejected`，`infrastructure_error`/`internal_error` 伴随 `failed`；**`completed` 没有失败原因**，
    它的 `terminal_reason` 必须为空——"成功完成"不是四种失败原因之一。

    迁移 **0009** 起数据库约束与本函数同语义，且**强制配对**（不只是"枚举成员 + 非空"）：
    `rejected` 只能带阻断类原因、`failed` 只能带技术类原因。此前只校验枚举成员，于是
    `rejected + infrastructure_error` 这类自相矛盾的行可以落库——状态说"被拒绝"，原因却指向"技术故障"，
    下游（SSE 帧、统计、运维告警）无法判断到底发生了什么。**不合法即拒，不猜、也不改写**。
    """
    if to_state == "completed":
        if terminal_reason is not None:
            raise TerminalReasonMismatch(
                "completed 不是失败：terminal_reason 必须为空（不得用失败原因冒充成功完成）"
            )
        return
    if is_terminal_state(to_state):
        allowed = TERMINAL_REASONS_BY_STATE.get(to_state, ())
        if terminal_reason not in allowed:
            raise TerminalReasonMismatch(
                f"{to_state} 必须携带与它配对的原因（允许：{'/'.join(allowed)}），"
                f"实得 {terminal_reason!r}"
            )
    elif terminal_reason is not None:
        raise TerminalReasonMismatch(f"{to_state} 是非终态，terminal_reason 必须为空")


@dataclass(frozen=True, slots=True)
class RunRow:
    evaluation_run_id: str
    submission_id: str
    state: str
    stage: str
    sequence: int
    cases_total: int | None
    cases_done: int
    terminal_reason: str | None
    submission_digest: str
    question_version_binding: str | None
    package_digest: str | None
    runner_policy_digest: str | None
    environment_digest: str | None
    question_version_id: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    #: 三态安全结论快照（迁移 0011）。`None` = 本次运行没有走到安全判定阶段——**没有结论**，
    #: 不是 `not_found`：从"零条 security_findings"推不出这两态的区别，所以这里不回填、不猜测。
    security_status: str | None = None


@dataclass(frozen=True, slots=True)
class RunTransitionRecord:
    sequence: int
    from_state: str | None
    to_state: str
    reason: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CaseEvidenceRecord:
    """逐用例证据的读回形态（含入库的 `awarded`，供报告逐分追溯）。"""

    case_id: str
    ordinal: int
    weight: Decimal
    passed: bool
    failure_category: str | None
    awarded: Decimal
    input_digest: str
    stdout_digest: str
    stderr_digest: str
    actual_output_excerpt: str | None
    expected_output_excerpt: str | None
    exit_code: int | None
    duration_ms: int | None
    resource_limit_hit: str | None
    message: str | None
    evidence_digest: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StaticFindingRecord:
    rule_id: str
    severity: str
    file: str
    line: int | None
    #: 契约字段名是 `column`，表列名是 `column_number`（`column` 是 PostgreSQL 保留字）。
    column: int | None
    message: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class SecurityFindingRecord:
    finding_id: str
    category: str
    severity: str
    detected_in: str
    rule_id: str | None
    message: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class StoredResult:
    evaluation_run_id: str
    score: Decimal
    max_score: Decimal
    is_pass: bool
    result_digest: str
    report: Mapping[str, object]
    persisted_at: datetime


# --- 运行 -----------------------------------------------------------------------------------


def create_run(
    connection: Connection,
    *,
    submission_id: str,
    question_version_id: str,
    submission_digest: str,
    occurred_at: datetime,
) -> str:
    """创建运行实体并写下第一行迁移史（`None -> queued`，sequence 从 1 开始）。

    前置条件：`b2.submissions` 中已存在该提交（外键保证引用完整）。
    """
    if not is_digest(submission_digest):
        raise ValueError("submission_digest 必须是 64 位小写十六进制摘要")
    identifier = uuid.uuid4()
    connection.execute(
        sa.insert(evaluation_runs).values(
            evaluation_run_id=identifier,
            submission_id=uuid.UUID(str(submission_id)),
            state="queued",
            stage="queued",
            sequence=0,
            cases_total=None,
            cases_done=0,
            terminal_reason=None,
            submission_digest=submission_digest,
            question_version_binding=None,
            package_digest=None,
            runner_policy_digest=None,
            environment_digest=None,
            question_version_id=uuid.UUID(str(question_version_id)),
            started_at=None,
            finished_at=None,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
    )
    require_run_transition(None, "queued")
    connection.execute(
        sa.insert(evaluation_run_state_transitions).values(
            evaluation_run_state_transition_id=uuid.uuid4(),
            evaluation_run_id=identifier,
            sequence=1,
            from_state=None,
            to_state="queued",
            reason=None,
            occurred_at=occurred_at,
        )
    )
    return str(identifier)


def load_run(connection: Connection, evaluation_run_id: str) -> RunRow | None:
    """读取运行行（不存在返回 `None`，由调用方决定 404 语义）。"""
    row = connection.execute(
        sa.select(evaluation_runs).where(
            evaluation_runs.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id))
        )
    ).fetchone()
    if row is None:
        return None
    return RunRow(
        evaluation_run_id=str(row.evaluation_run_id),
        submission_id=str(row.submission_id),
        state=row.state,
        stage=row.stage,
        sequence=int(row.sequence),
        cases_total=row.cases_total,
        cases_done=int(row.cases_done),
        terminal_reason=row.terminal_reason,
        submission_digest=row.submission_digest,
        question_version_binding=row.question_version_binding,
        package_digest=row.package_digest,
        runner_policy_digest=row.runner_policy_digest,
        environment_digest=row.environment_digest,
        question_version_id=str(row.question_version_id),
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        security_status=row.security_status,
    )


def advance_stage(
    connection: Connection,
    *,
    evaluation_run_id: str,
    stage: str,
    occurred_at: datetime,
) -> None:
    """**逐阶段持久化**：把运行推进到指定阶段（阶段是 SSE 的可观测进度，不能只在内存里推）。"""
    if stage not in STAGES:
        raise ValueError(f"未知阶段：{stage!r}")
    identifier = _lock_run(connection, evaluation_run_id)
    connection.execute(
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .values(stage=stage, updated_at=occurred_at)
    )


def update_counts(
    connection: Connection,
    *,
    evaluation_run_id: str,
    cases_total: int | None,
    cases_done: int,
    occurred_at: datetime,
) -> None:
    """更新用例计数（`cases_done <= cases_total`，与迁移 0006 的 CHECK 同语义）。"""
    if cases_done < 0:
        raise ValueError("cases_done 必须非负")
    if cases_total is not None and cases_done > cases_total:
        raise ValueError("cases_done 不得大于 cases_total")
    identifier = _lock_run(connection, evaluation_run_id)
    connection.execute(
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .values(cases_total=cases_total, cases_done=cases_done, updated_at=occurred_at)
    )


def bind_digests(
    connection: Connection,
    *,
    evaluation_run_id: str,
    package_digest: str,
    runner_policy_digest: str,
    environment_digest: str,
    question_version_binding: str | None,
    occurred_at: datetime,
) -> None:
    """写入五项绑定摘要中的四项（提交摘要创建时已写）——可重放性的记录基础。"""
    for name, value in (
        ("package_digest", package_digest),
        ("runner_policy_digest", runner_policy_digest),
        ("environment_digest", environment_digest),
    ):
        if not is_digest(value):
            raise ValueError(f"{name} 必须是 64 位小写十六进制摘要")
    if question_version_binding is not None and not is_digest(question_version_binding):
        raise ValueError("question_version_binding 必须是摘要或 None")
    identifier = _lock_run(connection, evaluation_run_id)
    connection.execute(
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .values(
            package_digest=package_digest,
            runner_policy_digest=runner_policy_digest,
            environment_digest=environment_digest,
            question_version_binding=question_version_binding,
            updated_at=occurred_at,
        )
    )


def transition_run(
    connection: Connection,
    *,
    evaluation_run_id: str,
    to_state: str,
    occurred_at: datetime,
    reason: str | None = None,
    terminal_reason: str | None = None,
    stage: str | None = None,
) -> RunTransitionRecord:
    """迁移运行状态：**先校验、再写历史、最后更新运行行**（全部在调用方事务内）。

    返回本次迁移的历史行（`sequence` 从 1 起、每次 +1，由本函数统一分配，调用方无法给出乱序序号）。
    """
    _require_terminal_reason(to_state, terminal_reason)
    identifier = _lock_run(connection, evaluation_run_id)
    current = connection.execute(
        sa.select(evaluation_runs.c.state).where(
            evaluation_runs.c.evaluation_run_id == identifier
        )
    ).scalar_one()
    require_run_transition(current, to_state)
    current_sequence = connection.execute(
        sa.select(
            sa.func.coalesce(sa.func.max(evaluation_run_state_transitions.c.sequence), 0)
        ).where(evaluation_run_state_transitions.c.evaluation_run_id == identifier)
    ).scalar_one()
    sequence = int(current_sequence) + 1
    safe_reason = redact_message(reason, limit=REASON_LIMIT)
    connection.execute(
        sa.insert(evaluation_run_state_transitions).values(
            evaluation_run_state_transition_id=uuid.uuid4(),
            evaluation_run_id=identifier,
            sequence=sequence,
            from_state=current,
            to_state=to_state,
            reason=safe_reason,
            occurred_at=occurred_at,
        )
    )
    values: dict[str, object] = {
        "state": to_state,
        "terminal_reason": terminal_reason,
        "updated_at": occurred_at,
        "finished_at": occurred_at if is_terminal_state(to_state) else None,
    }
    if stage is None:
        values["stage"] = "terminal" if is_terminal_state(to_state) else current_stage_of(
            connection, evaluation_run_id
        )
    else:
        if stage not in STAGES:
            raise ValueError(f"未知阶段：{stage!r}")
        values["stage"] = stage
    if to_state == "running":
        values["started_at"] = occurred_at
    connection.execute(
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .values(**values)
    )
    return RunTransitionRecord(
        sequence=sequence,
        from_state=current,
        to_state=to_state,
        reason=safe_reason,
        occurred_at=occurred_at,
    )


def current_stage_of(connection: Connection, evaluation_run_id: str) -> str:
    """当前阶段（用于"迁移但不改阶段"的写入，避免把 stage 覆盖成 NULL）。"""
    return str(
        connection.execute(
            sa.select(evaluation_runs.c.stage).where(
                evaluation_runs.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id))
            )
        ).scalar_one()
    )


def advance_submission_state(
    connection: Connection,
    *,
    submission_id: str,
    to_state: str,
    occurred_at: datetime,
    reason: str | None = None,
) -> str | None:
    """推进**提交**状态（评测侧唯一出口）：状态行与迁移史在同一事务内更新。

    为什么评测侧必须写它：契约 `SubmissionTransitions` 明文要求"静态高危必须在**不进入沙箱**的
    情况下被拒（`validated -> rejected`）"，`MAIN/docs/architecture/models.md` §1 把提交状态机列为
    不可修改的公共契约。此前评测链路只维护**运行**状态机，提交永远停在 `validated`，于是
    `GET /api/v1/submissions/{id}` 的 `state_history` 与运行的真实结局长期不一致（学生看到"已校验"
    而实际已被拒绝或已完成）。两套状态机各管自己的取值域：运行没有 `validated`，提交没有
    `terminal`/`queued` 之外的阶段概念。

    返回新的提交状态；**当前状态不允许该迁移时返回 `None` 且不做任何写入**——例如对已 `completed`
    的提交重跑：终态无出边，此时不得为了"看起来同步"而编造一条非法迁移（状态史自相矛盾比不同步更坏）。

    **重复评测的策略（显式声明，避免读者把下面这条当成缺陷）**：本函数让提交状态反映**首次**评测
    周期。契约的提交状态机是穷举的且终态无出边（`completed`/`rejected`/`failed` 之后没有任何迁移），
    因此**第二次及以后**的运行无法在提交级表达——那时提交停留在首次结局，而运行各自维护自己的
    `state`/终态（`b2.evaluation_runs` 对 `submission_id` 只有普通索引，并未声明 1:1 唯一性）。
    成绩视图按"有运行则取最新运行状态"回落（`grades.query`），因此**成绩不受影响**；受影响的只有
    `GET /api/v1/submissions/{id}` 的 `state_history`：它讲的是首次评测的故事。
    这条需要 MAIN 定契约：**若同一提交允许重复评测，提交状态机需要显式的"新一轮"表达**（终态加出边
    或另立字段），否则"运行 1:1 同步提交"这条要求只在首次评测成立。
    """
    identifier = uuid.UUID(str(submission_id))
    current = connection.execute(
        sa.select(submissions.c.state).where(submissions.c.submission_id == identifier)
    ).scalar_one_or_none()
    if current is None:
        raise ValueError("unknown submission for state advance")
    if current == to_state:
        return current
    if not states.can_transition(current, to_state):
        return None
    connection.execute(
        sa.update(submissions)
        .where(submissions.c.submission_id == identifier)
        .values(state=to_state, updated_at=occurred_at)
    )
    record_transitions(
        connection,
        submission_id=str(identifier),
        transitions=((str(current), to_state, reason),),
        occurred_at=occurred_at,
    )
    return to_state


def run_history(connection: Connection, evaluation_run_id: str) -> list[RunTransitionRecord]:
    """按**显式序号**读取迁移史（不按时间戳排序：同事务的两条迁移会共享毫秒时间戳）。"""
    rows = connection.execute(
        sa.select(evaluation_run_state_transitions)
        .where(
            evaluation_run_state_transitions.c.evaluation_run_id
            == uuid.UUID(str(evaluation_run_id))
        )
        .order_by(evaluation_run_state_transitions.c.sequence)
    ).fetchall()
    return [
        RunTransitionRecord(
            sequence=int(row.sequence),
            from_state=row.from_state,
            to_state=row.to_state,
            reason=row.reason,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


# --- 明细与证据 -----------------------------------------------------------------------------


def record_static_findings(
    connection: Connection,
    *,
    evaluation_run_id: str,
    result: StaticAnalysisResult,
    occurred_at: datetime,
) -> int:
    """写入静态明细（契约字段 `column` 映射到列 `column_number`）。"""
    identifier = uuid.UUID(str(evaluation_run_id))
    for finding in result.findings:
        connection.execute(
            sa.insert(static_findings).values(
                static_finding_id=uuid.uuid4(),
                evaluation_run_id=identifier,
                rule_id=finding.rule_id,
                severity=finding.severity,
                file=finding.file,
                line=finding.line,
                column_number=finding.column,
                message=finding.message,
                blocking=finding.blocking,
                occurred_at=occurred_at,
            )
        )
    return len(result.findings)


def record_security_findings(
    connection: Connection,
    *,
    evaluation_run_id: str,
    decision: SecurityDecision,
    occurred_at: datetime,
) -> int:
    """写入安全发现（静态与执行期同表；`blocking` 取自结论的阻断位）。"""
    identifier = uuid.UUID(str(evaluation_run_id))
    for finding in decision.findings:
        connection.execute(
            sa.insert(security_findings).values(
                security_finding_id=uuid.UUID(finding.finding_id),
                evaluation_run_id=identifier,
                category=finding.category,
                severity=finding.severity,
                detected_in=finding.detected_in,
                rule_id=finding.rule_id,
                message=finding.message,
                blocking=decision.blocking,
                occurred_at=occurred_at,
            )
        )
    return len(decision.findings)


def record_runtime_security_findings(
    connection: Connection,
    *,
    evaluation_run_id: str,
    case_ordinal: int,
    findings: Sequence[SecurityFinding],
    occurred_at: datetime,
) -> int:
    """写入**执行期**沙箱违规（`detected_in='runtime'`）——运行期违规的落库落点。

    为什么必须有这个函数：静态判定走 `record_security_findings`，而运行期违规此前**没有任何落库
    落点**（登记的缺口 G-L1-7）——真实沙箱里捕获到的 `runner.seccomp` 只活在测试进程内存与
    `evidence_digest` 里，运行结束后不可查。`security_findings` 的 `detected_in` CHECK 本来就是
    `('static','runtime')`，即该表从一开始就是为这两类发现设计的。

    **主键派生**：`finding_id` 由内容派生（同一违规永远得到同一标识），跨运行做不了主键，因此这里按
    `(运行, 用例序号, finding_id)` 派生 UUID——同一次运行的同一违规重复落库会冲突（不双写），
    而不同运行之间不会互相撞车。

    `blocking` 逐条按严重度判定（与静态侧 `decision.blocking and severity == "high"` 同语义）：
    只有 high 违规才是本次被拒绝的原因；medium/low 的发现照常记录但不声称它阻断了运行。
    """
    identifier = uuid.UUID(str(evaluation_run_id))
    rows: list[dict[str, object]] = []
    for finding in findings:
        if finding.detected_in != "runtime":
            raise ValueError("执行期发现的落库入口只接受 detected_in == 'runtime' 的发现")
        rows.append(
            {
                "security_finding_id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"b2-runtime-finding:{identifier}:{case_ordinal}:{finding.finding_id}",
                ),
                "evaluation_run_id": identifier,
                "category": finding.category,
                "severity": finding.severity,
                "detected_in": finding.detected_in,
                "rule_id": finding.rule_id,
                "message": finding.message,
                "blocking": finding.severity == "high",
                "occurred_at": occurred_at,
            }
        )
    if not rows:
        return 0
    connection.execute(sa.insert(security_findings), rows)
    return len(rows)


def record_case_evidence(
    connection: Connection,
    *,
    evaluation_run_id: str,
    evidence: CaseEvidence,
    awarded: Decimal,
    occurred_at: datetime,
) -> None:
    """写入一条逐用例证据（`(run, ordinal)` 唯一：同一序重复写是缺陷，由约束拒绝）。"""
    if awarded < 0:
        raise ValueError("awarded 必须非负")
    connection.execute(
        sa.insert(case_evidence).values(
            case_evidence_id=uuid.uuid4(),
            evaluation_run_id=uuid.UUID(str(evaluation_run_id)),
            case_id=uuid.UUID(str(evidence.case_id)),
            ordinal=evidence.ordinal,
            weight=Decimal(str(evidence.weight)),
            passed=evidence.passed,
            failure_category=evidence.failure_category,
            awarded=awarded,
            input_digest=evidence.input_digest,
            stdout_digest=evidence.stdout_digest,
            stderr_digest=evidence.stderr_digest,
            actual_output_excerpt=evidence.actual_output_excerpt,
            expected_output_excerpt=evidence.expected_output_excerpt,
            exit_code=evidence.exit_code,
            duration_ms=evidence.duration_ms,
            resource_limit_hit=evidence.resource_limit_hit,
            message=evidence.message,
            evidence_digest=evidence.evidence_digest,
            occurred_at=occurred_at,
        )
    )


def record_security_conclusion(
    connection: Connection,
    *,
    evaluation_run_id: str,
    status: str,
    occurred_at: datetime,
) -> None:
    """写入**三态安全结论**快照（`found`/`not_found`/`not_checked`）。

    三态必须原样落库：`not_checked` 绝不能被折算成 `not_found`（"没跑"不是"安全"）。
    这一列是评语轨白名单里"三态安全结论"的**唯一**存储落点——从 `security_findings` 的行数
    推不出这两态的区别，而报告 JSON 里虽然有一份结论，却同时承载隐藏用例的期望输出，
    不能作为评语生成的输入来源（见迁移 0011 的说明）。
    """
    if status not in SECURITY_STATUSES:
        raise ValueError(f"未知安全结论：{status!r}")
    identifier = _lock_run(connection, evaluation_run_id)
    connection.execute(
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .values(security_status=status, updated_at=occurred_at)
    )


def load_case_evidence(
    connection: Connection, evaluation_run_id: str
) -> list[CaseEvidenceRecord]:
    """按 `ordinal` 升序读回逐用例证据。"""
    rows = connection.execute(
        sa.select(case_evidence)
        .where(case_evidence.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id)))
        .order_by(case_evidence.c.ordinal)
    ).fetchall()
    return [
        CaseEvidenceRecord(
            case_id=str(row.case_id),
            ordinal=int(row.ordinal),
            weight=Decimal(row.weight),
            passed=bool(row.passed),
            failure_category=row.failure_category,
            awarded=Decimal(row.awarded),
            input_digest=row.input_digest,
            stdout_digest=row.stdout_digest,
            stderr_digest=row.stderr_digest,
            actual_output_excerpt=row.actual_output_excerpt,
            expected_output_excerpt=row.expected_output_excerpt,
            exit_code=row.exit_code,
            duration_ms=row.duration_ms,
            resource_limit_hit=row.resource_limit_hit,
            message=row.message,
            evidence_digest=row.evidence_digest,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


def load_static_findings(
    connection: Connection, evaluation_run_id: str
) -> list[StaticFindingRecord]:
    """读回静态明细（`column_number` → `column`，列名差异只在这一层做）。"""
    rows = connection.execute(
        sa.select(static_findings)
        .where(static_findings.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id)))
        .order_by(static_findings.c.rule_id, static_findings.c.file, static_findings.c.line)
    ).fetchall()
    return [
        StaticFindingRecord(
            rule_id=row.rule_id,
            severity=row.severity,
            file=row.file,
            line=row.line,
            column=row.column_number,
            message=row.message,
            blocking=bool(row.blocking),
        )
        for row in rows
    ]


def load_security_findings(
    connection: Connection, evaluation_run_id: str
) -> list[SecurityFindingRecord]:
    rows = connection.execute(
        sa.select(security_findings)
        .where(security_findings.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id)))
        .order_by(security_findings.c.security_finding_id)
    ).fetchall()
    return [
        SecurityFindingRecord(
            finding_id=str(row.security_finding_id),
            category=row.category,
            severity=row.severity,
            detected_in=row.detected_in,
            rule_id=row.rule_id,
            message=row.message,
            blocking=bool(row.blocking),
        )
        for row in rows
    ]


# --- 结果与报告 -----------------------------------------------------------------------------


def persist_result(
    connection: Connection,
    *,
    evaluation_run_id: str,
    score: Decimal,
    max_score: Decimal,
    is_pass: bool,
    result_digest: str,
    report: Mapping[str, object],
    persisted_at: datetime,
) -> None:
    """写入结果行 + **完整报告 JSON**（与状态迁移同事务，见 `orchestrator`）。

    报告与结果同表同事务 ⇒ "报告可读"必然意味着"结果已持久化"（`RQ-B-2-60` 验收 #5）。
    """
    if not is_digest(result_digest):
        raise ValueError("result_digest 必须是 64 位小写十六进制摘要")
    identifier = uuid.UUID(str(evaluation_run_id))
    existing = connection.execute(
        sa.select(evaluation_results.c.evaluation_run_id).where(
            evaluation_results.c.evaluation_run_id == identifier
        )
    ).fetchone()
    if existing is not None:
        raise ResultAlreadyPersisted(f"{evaluation_run_id}: 结果行已存在")
    connection.execute(
        sa.insert(evaluation_results).values(
            evaluation_run_id=identifier,
            score=score,
            max_score=max_score,
            is_pass=is_pass,
            result_digest=result_digest,
            report=dict(report),
            persisted_at=persisted_at,
        )
    )


def load_result(connection: Connection, evaluation_run_id: str) -> StoredResult | None:
    row = connection.execute(
        sa.select(evaluation_results).where(
            evaluation_results.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id))
        )
    ).fetchone()
    if row is None:
        return None
    return StoredResult(
        evaluation_run_id=str(row.evaluation_run_id),
        score=Decimal(row.score),
        max_score=Decimal(row.max_score),
        is_pass=bool(row.is_pass),
        result_digest=row.result_digest,
        report=row.report,
        persisted_at=row.persisted_at,
    )


def read_report(connection: Connection, evaluation_run_id: str) -> Mapping[str, object] | None:
    """读取报告：**只从已持久化的结果行读**（没有结果行就没有报告，绝不当场重算）。"""
    stored = load_result(connection, evaluation_run_id)
    return None if stored is None else stored.report


def _lock_run(connection: Connection, evaluation_run_id: str) -> uuid.UUID:
    try:
        identifier = uuid.UUID(str(evaluation_run_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RunNotFoundError(f"非法运行标识：{evaluation_run_id!r}") from exc
    locked = connection.execute(
        sa.select(evaluation_runs.c.evaluation_run_id)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .with_for_update()
    ).fetchone()
    if locked is None:
        raise RunNotFoundError(f"运行不存在：{evaluation_run_id}")
    return identifier
