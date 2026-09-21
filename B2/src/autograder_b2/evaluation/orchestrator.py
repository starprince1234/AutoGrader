"""评测编排：阶段推进 + 运行状态迁移 + 动态执行并发闸门 + "持久化先于报告"。

流水线（每个阶段都**逐阶段持久化**到 `evaluation_runs.stage` 与 `progress_events`）：

```text
queued -> fetching_package -> static_analysis -> security_check -> dynamic_execution
       -> scoring -> reporting -> terminal
```

运行状态机（已锁定，不再设计）：`queued -> {running, rejected, failed}`、`running -> {completed,
rejected, failed}`。允许 `queued` 直达终态，因为静态/安全阻断发生在进入沙箱**之前**；没有运行实体就
无法通过 SSE 发出 `rejected` 终态帧。由此得到四条硬规则（都有测试）：

1. **静态阻断** ⇒ `rejected + static_blocked`：迁移史里**没有** `running`，runner **一次都没被调用**；
2. **安全阻断** ⇒ `rejected + security_blocked`：同上；
3. **执行期沙箱违规** ⇒ `rejected + security_blocked`：此时迁移史里**有** `running`（违规以真的执行过
   为前提），违规明细落 `b2.security_findings`（`detected_in = "runtime"`），**该运行不产生分数**，
   后续用例不再执行。它与第 1/2 条同属"你不该这么写"，**不是** `failed`：把沙箱违规记成技术故障会
   让"代码试图越权"从成绩单上消失；
4. **沙箱不可用** ⇒ `failed + infrastructure_error`（不赖学生）；`completed` 才可能有分数，`score=0`
   只可能出现在 `completed`（0 分是学生结果，不是系统失败）。

并发闸门：动态执行的初始并发上限恒为 `DYNAMIC_CONCURRENCY_LIMIT = 1`（资源账本），由
`DynamicExecutionGate` 以进程内信号量实现并记录峰值——**没有环境变量开关**，行为不随部署变化。

`rejected` **没有领域事件**（拒绝只对提交会话有意义，经 SSE 终态帧送达）；`started`/`progress`/
`completed`/`failed` 经 outbox（`events.outbox.build_event` + `enqueue`）**先写后发**，payload 只含标识、
计数、分数与摘要。outbox 的 `started` 在拿到 package 摘要之后入队，因此事件顺序天然满足
`started -> progress -> completed|failed`。

与 runner 的接线约定（由 runner 实现者确认，此处**只遵守、不另立**）：

- `SubmissionRunInput` 的 `case`/`adapter`/`policy` 必须**原样**是 package 解析结果里的对象
  （`parse_evaluation_package` 产出的用例对象 / `package.adapter` / `package.security_policy`）：runner
  做严格相等校验，自造一个"更严"的策略副本会被判为内部缺陷——收紧策略的能力属于 runner 变更，
  编排层不实现、也不假装实现。
- **每个用例一份 `SubmissionRunInput` 与一份独立工作区**（服务端生成，绝不复用上一个用例的工作区）；
  工作区里的入口脚本名由 runner 侧的固定执行约定给出（`main.py` / `main.sh`，callable harness 为
  `__b2_harness__.py`），编排层不重复定义、也不得覆盖已存在的同名资产。
- 用例级 **fixture 缺失或摘要不符**在 runner 侧返回 `failure_category=infrastructure_error` 的**证据**
  （不是异常），因为那不是学生的问题：编排层据此把运行落到 `failed`（技术故障），绝不记成学生失败。
- runner 的进程边界接缝（`probe`/`launcher`）是仅测试可见的 keyword-only 参数、默认即生产实现，
  编排层不通过环境变量切换执行后端。
- `duration_ms` 是墙钟测量值、**被排除在 `evidence_digest` 之外**（纳入则永远无法重放）：本层的报告
  摘要同样排除 `duration_ms` 与 `generated_at`，"同绑定摘要 ⇒ 同结果摘要"的断言不比对这两个字段。

编排只依赖 `ports` 里的窄协议：`PackageSource` / `WorkspaceProvider` / `SecurityDecider` / `CaseRunner`。
静态检查调用**已交付**的 `static.analyze_static`（不重写静态规则）；安全判定与沙箱执行由集成方注入
真实实现（本模块的测试用显式标注的替身驱动，见 `tests/test_evaluation_pipeline.py`）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2.domain.clock import utc_now
from autograder_b2.domain.envelope import EnvelopeFile, SubmissionEnvelope
from autograder_b2.domain.evidence import (
    CaseEvidence,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.domain.security import SecurityDecision
from autograder_b2.domain.static_result import StaticAnalysisResult
from autograder_b2.evaluation import progress, store
from autograder_b2.evaluation.ports import (
    CaseDescriptor,
    CaseRunner,
    PackageSource,
    PackageView,
    SecurityDecider,
    WorkspaceProvider,
)
from autograder_b2.evaluation.progress import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_PROGRESS,
    EVENT_REJECTED,
    ProgressFrame,
    schema_conflict,
)
from autograder_b2.evaluation.report import ReportBinding, ReportInputs, build_report
from autograder_b2.evaluation.scoring import ScoreBreakdown, score_run
from autograder_b2.events.outbox import (
    EVALUATION_COMPLETED,
    EVALUATION_FAILED,
    EVALUATION_PROGRESS,
    EVALUATION_STARTED,
    build_event,
    enqueue,
)
from autograder_b2.logging.context import bind_context, bind_operation
from autograder_b2.logging.logger import log_dependency_unavailable, log_event
from autograder_b2.static import analyze_static

#: 编排层 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.evaluation.orchestrator")

#: 动态执行的初始并发上限（资源账本：单并发作业 ≤96 MB RSS / 0.45 vCPU）。
#: **常量**而非配置项：并发上限是资源承诺，不允许被环境变量悄悄改掉。
DYNAMIC_CONCURRENCY_LIMIT = 1

#: 交给安全判定端口的单个源码文件读取上限（超出即视为工作区异常，不做截断式判定）。
SOURCE_READ_LIMIT = 1 << 20

#: 脱敏后的固定告知文本（绝不回显异常原文：其中可能含内部绝对路径）。
MESSAGE_STATIC_BLOCKED = "静态检查发现阻断级问题：评测被拒绝，未进入沙箱执行。"
MESSAGE_SECURITY_BLOCKED = "安全检查发现高危行为：评测被拒绝，未进入沙箱执行。"
MESSAGE_SANDBOX_VIOLATION = (
    "沙箱执行期发现安全违规（被囚程序触发了沙箱的拒绝判定）：本次评测被拒绝，"
    "这不是技术故障；违规明细已记入安全发现。"
)
MESSAGE_SANDBOX_UNAVAILABLE = "沙箱不可用：本次评测未能执行，这不是学生代码的问题。"
MESSAGE_PACKAGE_UNAVAILABLE = "评测包不可获取或不可解析：本次评测未能执行，不是学生代码的问题。"
MESSAGE_WORKSPACE_UNAVAILABLE = "提交工作区不可用：本次评测未能执行。"
MESSAGE_SECURITY_UNAVAILABLE = "安全判定不可用：本次评测未能执行。"
MESSAGE_SECURITY_UNCHECKED = "安全判定未完成（存在未执行的必需检查项）：不以「未检查」充当通过。"
MESSAGE_STATIC_NOT_CHECKED = (
    "静态检查未完成（存在未执行的必需检查项，整体未执行也属于此情形）："
    "不以「未检查」充当通过，评测终止。"
)
MESSAGE_CASE_TECHNICAL_FAILURE = "用例输入资产缺失或摘要不符：属技术故障，不计入学生失败。"
MESSAGE_INTERNAL_DEFECT = "评测内部缺陷：本次评测未能完成，已记录迁移原因，不计入学生成绩。"
MESSAGE_PERSIST_FAILED = "结果未成功持久化：不对外声称评测完成（报告可读等价于结果已落库）。"
MESSAGE_SCHEMA_CONFLICT = "数据库约束与契约语义冲突：拒绝写入不真实值，评测未完成。"


class OrchestrationError(RuntimeError):
    """编排层的失败基类（对外状态通过终态迁移与帧表达，不吞异常）。"""


class InfrastructureError(OrchestrationError):
    """技术性失败：沙箱/依赖/工作区不可用（`failed + infrastructure_error`）。"""


class InternalDefectError(OrchestrationError):
    """评测内部缺陷（`failed + internal_error`）。"""


class _Blocked(Exception):
    """内部控制流：静态/安全阻断（这不是"失败"，而是"拒绝"）。

    `invocations`/`evidence_written` 记录**拒绝发生时已经发生的事实**：执行期拒绝（沙箱违规）可能
    已经跑过若干个用例并落了证据，`EvaluationOutcome` 必须如实反映，而不是一律报 0。
    """

    def __init__(
        self,
        *,
        reason: str,
        message: str,
        invocations: int = 0,
        evidence_written: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.invocations = invocations
        self.evidence_written = evidence_written


class DynamicExecutionGate:
    """动态执行并发闸门（资源账本）：容量固定、记录峰值。

    用进程内 `BoundedSemaphore` 实现"同一时刻至多 N 个沙箱作业"；`peak` 是并发上界的**可断言证据**
    （测试在 N 次并发编排后断言 `peak <= 1`）。跨进程的全局占用需要数据库账本，属集成方的扩容工作
    （当前部署为单进程 worker）：此处如实标注，而不是假装已经全局生效。
    """

    def __init__(self, capacity: int = DYNAMIC_CONCURRENCY_LIMIT) -> None:
        if capacity < 1:
            raise ValueError("并发容量必须 >= 1")
        self._capacity = capacity
        self._semaphore = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._current = 0
        self._peak = 0
        self._acquisitions = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def current(self) -> int:
        with self._lock:
            return self._current

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak

    @property
    def acquisitions(self) -> int:
        with self._lock:
            return self._acquisitions

    def reset_peak(self) -> None:
        with self._lock:
            self._peak = self._current

    @contextmanager
    def hold(self) -> Iterator[None]:
        """占住一个并发额度（一个沙箱作业）；退出时必然归还。"""
        self._semaphore.acquire()
        with self._lock:
            self._current += 1
            self._acquisitions += 1
            self._peak = max(self._peak, self._current)
        try:
            yield
        finally:
            with self._lock:
                self._current -= 1
            self._semaphore.release()


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """一次编排的结果。

    `score`/`is_pass`/`result_digest`/`report` **只在**结果已持久化（`completed`）时非空——未完成的
    评测绝不以 0 分呈现，也绝不给出报告。
    """

    evaluation_run_id: str
    state: str
    stage: str
    terminal_reason: str | None
    score: Decimal | None
    is_pass: bool | None
    result_digest: str | None
    report: Mapping[str, object] | None
    runner_invocations: int
    case_evidence_written: int
    message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in store.RUN_TERMINAL_STATES


class EvaluationOrchestrator:
    """把一次已接收的提交推进到终态（`completed`/`rejected`/`failed`）。"""

    def __init__(
        self,
        *,
        engine: Engine,
        package_source: PackageSource,
        workspace_provider: WorkspaceProvider,
        security: SecurityDecider,
        runner: CaseRunner,
        gate: DynamicExecutionGate | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._packages = package_source
        self._workspaces = workspace_provider
        self._security = security
        self._runner = runner
        self._gate = DynamicExecutionGate() if gate is None else gate
        self._now = now

    # --- 只读入口（SSE/报告路由由集成方注册，这里只提供服务函数） ----------------------

    @property
    def gate(self) -> DynamicExecutionGate:
        return self._gate

    def read_report(self, evaluation_run_id: str) -> Mapping[str, object] | None:
        """读取已持久化的报告（没有 `evaluation_results` 行就返回 `None`，绝不当场重算）。"""
        with self._engine.connect() as connection:
            return store.read_report(connection, evaluation_run_id)

    def read_frames(
        self, evaluation_run_id: str, *, after: int = 0, limit: int = progress.MAX_FRAME_BATCH
    ) -> list[ProgressFrame]:
        """读取进度帧（`sequence > after`，供 SSE 路由断线重连）。"""
        with self._engine.connect() as connection:
            return progress.read_frames(
                connection, evaluation_run_id=evaluation_run_id, after=after, limit=limit
            )

    def frame_stream(
        self,
        evaluation_run_id: str,
        *,
        after: int = 0,
        poll_interval: float = 0.25,
        idle_timeout: float = 30.0,
    ) -> Iterator[ProgressFrame]:
        """SSE 帧生成器（语义与签名见 `progress.frame_stream`）。"""
        return progress.frame_stream(
            engine=self._engine,
            evaluation_run_id=evaluation_run_id,
            after=after,
            poll_interval=poll_interval,
            idle_timeout=idle_timeout,
        )

    def read_history(self, evaluation_run_id: str) -> list[store.RunTransitionRecord]:
        """读取运行迁移史（按显式序号，不按时间戳）。"""
        with self._engine.connect() as connection:
            return store.run_history(connection, evaluation_run_id)

    # --- 主流水线 ---------------------------------------------------------------------

    def run(self, submission: SubmissionEnvelope) -> EvaluationOutcome:
        """推进一次评测：创建运行实体 → 逐阶段推进 → 终态（含持久化的报告）。

        每个阶段在**同一个日志上下文**（`bind_operation` 绑定的 `correlation_id`）里推进，并用
        `bind_context(stage=...)` 只换阶段名——于是"接收 → 静态 → 安全 → 动态 → 报告"的日志都带同一个
        `correlation_id`，可凭它串起全链路（RQ-B-2-99 场景 2-99-C）。日志只写白名单字段（标识、阶段、
        计数、结论），绝不写源码/参考答案/隐藏用例/凭据/内部路径。
        """
        run_id = self._create_run(submission, self._now())
        invocations = 0
        evidence_written = 0
        workspace: Path | None = None
        with bind_operation(
            correlation_id=submission.correlation_id,
            submission_id=submission.submission_id,
            evaluation_run_id=run_id,
            stage="evaluation",
        ):
            log_event(logger, "evaluation.run_started", fields={"state": "queued"})
            try:
                with self._stage("package_fetch"):
                    view = self._fetch_package(submission, run_id)
                with self._stage("workspace_prepare"):
                    workspace = self._prepare_workspace(submission, run_id)
                with self._stage("static_analysis"):
                    static = self._run_static(submission, run_id, view, workspace)
                with self._stage("security_decision"):
                    security = self._run_security(submission, run_id, view, workspace)
                with self._stage("dynamic_execution"):
                    evidences = self._run_dynamic(submission, run_id, view)
                invocations = len(evidences)
                with self._stage("scoring", count=invocations):
                    breakdown = self._run_scoring(
                        submission, run_id, view, evidences, static, security
                    )
                evidence_written = len(evidences)
                with self._stage("reporting", count=invocations):
                    outcome = self._run_reporting(
                        submission, run_id, view, static, security, evidences, breakdown
                    )
                log_event(
                    logger,
                    "evaluation.run_completed",
                    fields={
                        "state": "completed",
                        "count": invocations,
                        "passed": outcome.is_pass,
                    },
                )
                return outcome
            except _Blocked as blocked:
                outcome = self._finish_rejected(
                    submission,
                    run_id,
                    reason=blocked.reason,
                    message=blocked.message,
                    invocations=blocked.invocations,
                    evidence_written=blocked.evidence_written,
                )
                log_event(
                    logger,
                    "evaluation.run_rejected",
                    level=logging.WARNING,
                    fields={"state": "rejected", "decision": blocked.reason},
                )
                return outcome
            except InfrastructureError as error:
                outcome = self._finish_failure(
                    submission,
                    run_id,
                    terminal_reason="infrastructure_error",
                    message=str(error),
                    invocations=invocations,
                    evidence_written=evidence_written,
                )
                log_event(
                    logger,
                    "evaluation.run_failed",
                    level=logging.ERROR,
                    fields={"state": "failed", "error_category": "infrastructure_error"},
                )
                return outcome
            except InternalDefectError as error:
                outcome = self._finish_failure(
                    submission,
                    run_id,
                    terminal_reason="internal_error",
                    message=str(error),
                    invocations=invocations,
                    evidence_written=evidence_written,
                )
                log_event(
                    logger,
                    "evaluation.run_failed",
                    level=logging.ERROR,
                    fields={"state": "failed", "error_category": "internal_error"},
                )
                return outcome
            finally:
                if workspace is not None:
                    self._workspaces.release(workspace)

    # --- 阶段实现 ---------------------------------------------------------------------

    @contextmanager
    def _stage(self, name: str, **fields: object) -> Iterator[None]:
        """把一段阶段执行包进日志上下文，并在**成功后**记一条阶段完成日志。

        阶段名进 `bind_context`（于是该阶段内的所有日志行都带 `stage`），完成日志只写白名单字段
        （阶段名 + 计数类），绝不写源码/参考答案/隐藏用例/凭据/内部路径。阶段抛异常时不记"完成"
        （失败由终态日志如实呈现），异常**原样传播**（不吞）。
        """
        with bind_context(stage=name):
            yield
            log_event(logger, "evaluation.stage_completed", fields={"stage": name, **fields})

    def _create_run(self, submission: SubmissionEnvelope, moment: datetime) -> str:
        """创建运行实体（`queued`）并写下第一帧与第一行迁移史。"""
        with self._engine.begin() as connection:
            run_id = store.create_run(
                connection,
                submission_id=submission.submission_id,
                question_version_id=submission.question_version_id,
                submission_digest=submission.submission_digest,
                occurred_at=moment,
            )
            progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state="queued",
                stage="queued",
                occurred_at=moment,
                cases_done=0,
                message="已接收提交，进入评测队列。",
            )
        return run_id

    def _fetch_package(self, submission: SubmissionEnvelope, run_id: str) -> PackageView:
        """阶段 `fetching_package`：取 package；摘要就绪后才入队 `started` 事件。"""
        moment = self._now()
        self._enter_stage(
            submission,
            run_id,
            stage="fetching_package",
            state="queued",
            moment=moment,
            message="获取评测包。",
            emit_event=False,
        )
        try:
            view = self._packages.fetch(
                evaluation_run_id=run_id, question_version_id=submission.question_version_id
            )
        except Exception as error:  # 依赖方任何异常都归类为技术失败
            raise InfrastructureError(MESSAGE_PACKAGE_UNAVAILABLE) from error
        moment = self._now()
        with self._engine.begin() as connection:
            store.bind_digests(
                connection,
                evaluation_run_id=run_id,
                package_digest=view.package_digest,
                runner_policy_digest=view.runner_policy_digest,
                environment_digest=view.environment_digest,
                question_version_binding=view.question_version_binding,
                occurred_at=moment,
            )
            store.update_counts(
                connection,
                evaluation_run_id=run_id,
                cases_total=view.cases_total,
                cases_done=0,
                occurred_at=moment,
            )
            frame = progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state="queued",
                stage="static_analysis",
                occurred_at=moment,
                cases_total=view.cases_total,
                cases_done=0,
                message="评测包就绪，开始静态检查。",
            )
            self._enqueue(
                connection,
                subject=EVALUATION_STARTED,
                submission=submission,
                occurred_at=moment,
                payload={
                    "submission_id": submission.submission_id,
                    "evaluation_run_id": run_id,
                    "question_version_id": submission.question_version_id,
                    "stage": "static_analysis",
                    "package_digest": view.package_digest,
                    "runner_policy_digest": view.runner_policy_digest,
                    "environment_digest": view.environment_digest,
                },
                deduplication_key=f"{run_id}:started",
            )
            self._enqueue_progress(
                connection, submission=submission, run_id=run_id, frame=frame, occurred_at=moment
            )
        return view

    def _prepare_workspace(
        self, submission: SubmissionEnvelope, run_id: str, *, case_id: str | None = None
    ) -> Path:
        """准备工作区：`case_id=None` 是运行级（静态/安全用），非空是该用例的独立沙箱工作区。"""
        try:
            return self._workspaces.prepare(
                submission_id=submission.submission_id,
                evaluation_run_id=run_id,
                files=submission.files,
                case_id=case_id,
            )
        except Exception as error:  # 工作区不可用是技术失败，不是学生的错
            raise InfrastructureError(MESSAGE_WORKSPACE_UNAVAILABLE) from error

    def _run_static(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
        workspace: Path,
    ) -> StaticAnalysisResult:
        """阶段 `static_analysis`：调用**已交付的** `analyze_static`（不重写静态规则）。"""
        try:
            result = analyze_static(
                workspace=workspace,
                files=[item.path for item in submission.files],
                language=view.language,
            )
        except Exception as error:  # 静态检查自身异常按内部缺陷处理
            raise InternalDefectError(MESSAGE_INTERNAL_DEFECT) from error
        moment = self._now()
        blocked = result.blocking
        # 「未检查」与「未发现」必须分开：**任何一项必需检查没跑**都不算通过（I14），
        # 而不只是"全部没跑"。此前只在 `all(not_checked)` 时才终止，于是"部分未检查"
        # （例如 worker 上没有 bash ⇒ `bash_syntax = not_checked`、`structural_rules = ran`）
        # 会一路走到计分与 `completed`——**未检查被静默当成了通过**，而层内结论与端到端结论
        # 因此互相矛盾（组级复核 L1-3 不一致 #1，本机 100% 可达）。
        unchecked = tuple(check.checker for check in result.checks if check.status == "not_checked")
        stage = "static_analysis" if (blocked or unchecked) else "security_check"
        if blocked:
            message = "静态检查发现阻断级问题。"
        elif unchecked:
            message = (
                "静态检查未完成：以下必需检查未执行（" + "、".join(unchecked) + "），"
                "不得以「未检查」充当通过，评测终止。"
            )
        else:
            message = f"静态检查完成：{result.error_count} 个错误、{result.warning_count} 个警告。"
        with self._engine.begin() as connection:
            store.record_static_findings(
                connection, evaluation_run_id=run_id, result=result, occurred_at=moment
            )
            store.advance_stage(
                connection, evaluation_run_id=run_id, stage=stage, occurred_at=moment
            )
            frame = progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state="queued",
                stage=stage,
                occurred_at=moment,
                cases_total=view.cases_total,
                cases_done=0,
                message=message,
            )
            self._enqueue_progress(
                connection, submission=submission, run_id=run_id, frame=frame, occurred_at=moment
            )
        if blocked:
            raise _Blocked(reason="static_blocked", message=MESSAGE_STATIC_BLOCKED)
        if unchecked:
            # 与安全层同样的语义：未检查 ⇒ 技术失败（`failed` + `infrastructure_error`），
            # **不是** `static_blocked`——我们没有"发现"任何东西，我们只是没能检查。
            raise InfrastructureError(MESSAGE_STATIC_NOT_CHECKED)
        return result

    def _run_security(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
        workspace: Path,
    ) -> SecurityDecision:
        """阶段 `security_check`：三态结论；阻断 ⇒ rejected，未检查 ⇒ failed（fail-closed）。"""
        sources = self._read_sources(workspace, submission.files)
        try:
            decision = self._security.decide(
                evaluation_run_id=run_id, files=sources, language=view.language
            )
        except Exception as error:  # 安全判定不可用是技术失败，绝不等于放行
            raise InfrastructureError(MESSAGE_SECURITY_UNAVAILABLE) from error
        moment = self._now()
        blocked = decision.blocking
        unchecked = bool(decision.unchecked)
        stage = "security_check" if (blocked or unchecked) else "dynamic_execution"
        with self._engine.begin() as connection:
            store.record_security_findings(
                connection, evaluation_run_id=run_id, decision=decision, occurred_at=moment
            )
            # 三态结论**原样落库**（评测链之外的消费者只有一个：评语轨的白名单输入）。
            # `not_checked` 与 `not_found` 在这里就分开，下游不必也不能从 findings 的行数去猜。
            store.record_security_conclusion(
                connection, evaluation_run_id=run_id, status=decision.status, occurred_at=moment
            )
            store.advance_stage(
                connection, evaluation_run_id=run_id, stage=stage, occurred_at=moment
            )
            frame = progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state="queued",
                stage=stage,
                occurred_at=moment,
                cases_total=view.cases_total,
                cases_done=0,
                message=f"安全检查结论：{decision.status}。",
            )
            self._enqueue_progress(
                connection, submission=submission, run_id=run_id, frame=frame, occurred_at=moment
            )
        if blocked:
            raise _Blocked(reason="security_blocked", message=MESSAGE_SECURITY_BLOCKED)
        if unchecked:
            raise InfrastructureError(MESSAGE_SECURITY_UNCHECKED)
        return decision

    def _run_dynamic(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
    ) -> tuple[CaseEvidence, ...]:
        """阶段 `dynamic_execution`：`queued -> running`，逐用例过闸门执行并即时落证据。

        提交（submission）状态机在同一事务内跟进：`validated -> queued -> running`。契约
        `SubmissionTransitions` 要求"静态高危必须在不进入沙箱的情况下被拒"，因此静态与安全检查
        **之前**提交保持 `validated`，通过之后才进入 `queued`；两套状态机各管自己的取值域。
        """
        moment = self._now()
        with self._engine.begin() as connection:
            store.transition_run(
                connection,
                evaluation_run_id=run_id,
                to_state="running",
                occurred_at=moment,
                reason="静态与安全检查通过，进入沙箱执行。",
                stage="dynamic_execution",
            )
            # 提交侧：通过静态/安全后才入队，随后立刻获得执行槽（同一事务，故两步同刻）。
            store.advance_submission_state(
                connection,
                submission_id=submission.submission_id,
                to_state="queued",
                occurred_at=moment,
                reason="静态与安全检查通过，进入评测队列。",
            )
            store.advance_submission_state(
                connection,
                submission_id=submission.submission_id,
                to_state="running",
                occurred_at=moment,
                reason="获得执行槽。",
            )
            frame = progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state="running",
                stage="dynamic_execution",
                occurred_at=moment,
                cases_total=view.cases_total,
                cases_done=0,
                message="进入沙箱执行。",
            )
            self._enqueue_progress(
                connection, submission=submission, run_id=run_id, frame=frame, occurred_at=moment
            )
        evidences: list[CaseEvidence] = []
        for done, case in enumerate(view.ordered_cases(), start=1):
            case_workspace = self._prepare_workspace(submission, run_id, case_id=case.case_id)
            try:
                evidence = self._execute_case(submission, run_id, view, case_workspace, case)
            finally:
                self._workspaces.release(case_workspace)
            # 该用例已经被调用过（无论结果如何都算一次调用）；已落库的证据数是此前完成的用例数。
            self._escalate_case_outcome(
                run_id,
                evidence,
                invocations=len(evidences) + 1,
                evidence_written=len(evidences),
            )
            self._record_evidence(submission, run_id, view, evidence, done)
            evidences.append(evidence)
        return tuple(evidences)

    def _execute_case(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
        case_workspace: Path,
        case: CaseDescriptor,
    ) -> CaseEvidence:
        """过一次闸门、执行一个用例（三个句柄原样来自 package 解析结果，每用例一份工作区）。"""
        run_input = SubmissionRunInput(
            submission_id=submission.submission_id,
            evaluation_run_id=run_id,
            correlation_id=submission.correlation_id,
            workspace=case_workspace,
            files=tuple(submission.files),
            case=case.handle,  # type: ignore[arg-type] - 不透明句柄：编排不解释 package 内部结构
            adapter=view.adapter_handle,  # type: ignore[arg-type] - 同上
            policy=view.policy_handle,  # type: ignore[arg-type] - 同上
        )
        try:
            with self._gate.hold():
                return self._runner.run_case(view.handle, run_input)
        except SandboxUnavailableError as error:
            # 依赖不可用必须**有日志**、不只是有状态（RQ-B-2-99 场景 2-99-D）：沙箱不可用会经
            # `InfrastructureError` 落到运行终态 `failed(infrastructure_error)`（状态），日志这一侧
            # 用统一的依赖故障事件 `runner.sandbox_unavailable` 补上。**不吞异常**：照旧抛 `InfrastructureError`。
            log_dependency_unavailable(
                logger,
                dependency="sandbox",
                error=error,
                stage="dynamic_execution",
            )
            raise InfrastructureError(MESSAGE_SANDBOX_UNAVAILABLE) from error
        except PackageUnavailableError as error:
            log_dependency_unavailable(
                logger,
                dependency="question_package",
                error=error,
                stage="dynamic_execution",
            )
            raise InfrastructureError(MESSAGE_PACKAGE_UNAVAILABLE) from error
        except InternalEvaluationError as error:
            raise InternalDefectError(MESSAGE_INTERNAL_DEFECT) from error

    def _escalate_case_outcome(
        self,
        run_id: str,
        evidence: CaseEvidence,
        *,
        invocations: int,
        evidence_written: int,
    ) -> None:
        """把**不属于学生成绩**的用例证据升格成运行级终态，而不是记成学生的一次失败。

        两条路径，方向相反但都不产生分数：

        - `security_blocked`（沙箱在**执行期**判定被囚程序违规：seccomp 拒绝、沙箱自有日志报违规）
          ⇒ `rejected + security_blocked`。这是"你不该这么写"，与静态高危阻断同类，**不得**降级成
          `failed`（技术故障）或"某用例 0 分"（RQ-B-2-70 验收 2）。升格前先把违规明细写进
          `b2.security_findings`（`detected_in="runtime"`）——拒绝必须留下可查的原因，否则"评测被拒"
          就只是一句话。拒绝即终止：不再执行后续用例（已经违规的代码不该继续在沙箱里跑）。
        - `infrastructure_error`（fixture 缺失/摘要不符）⇒ `failed`（技术故障）。若按学生失败处理，
          就会把系统问题记成学生扣分；该用例的证据也不写入 `case_evidence`（不留"0 分像学生没做对"
          的痕迹）。
        """
        if evidence.failure_category == "security_blocked":
            self._record_runtime_security_findings(run_id, evidence)
            raise _Blocked(
                reason="security_blocked",
                message=MESSAGE_SANDBOX_VIOLATION,
                invocations=invocations,
                evidence_written=evidence_written,
            )
        if evidence.failure_category == "infrastructure_error":
            raise InfrastructureError(MESSAGE_CASE_TECHNICAL_FAILURE)

    def _record_runtime_security_findings(self, run_id: str, evidence: CaseEvidence) -> int:
        """把执行期违规写进 `b2.security_findings`（与终态迁移分开事务：拒绝不可能因为没有明细而消失）。"""
        moment = self._now()
        with self._engine.begin() as connection:
            return store.record_runtime_security_findings(
                connection,
                evaluation_run_id=run_id,
                case_ordinal=evidence.ordinal,
                findings=evidence.sandbox_violations,
                occurred_at=moment,
            )

    def _record_evidence(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
        evidence: CaseEvidence,
        done: int,
    ) -> None:
        """逐用例证据与进度帧同事务写入（每一分都能追溯到具体用例）。"""
        moment = self._now()
        award = Decimal(str(evidence.weight)) if evidence.passed else Decimal(0)
        with self._engine.begin() as connection:
            store.record_case_evidence(
                connection,
                evaluation_run_id=run_id,
                evidence=evidence,
                awarded=award,
                occurred_at=moment,
            )
            frame = progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state="running",
                stage="dynamic_execution",
                occurred_at=moment,
                cases_total=view.cases_total,
                cases_done=done,
                message=(
                    f"用例 {done}/{view.cases_total} 完成："
                    f"{'通过' if evidence.passed else '未通过'}。"
                ),
            )
            self._enqueue_progress(
                connection, submission=submission, run_id=run_id, frame=frame, occurred_at=moment
            )

    def _run_scoring(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
        evidence: Sequence[CaseEvidence],
        static: StaticAnalysisResult,
        security: SecurityDecision,
    ) -> ScoreBreakdown:
        """阶段 `scoring`：纯确定性评分（无模型、无时间、无随机）。"""
        self._enter_stage(
            submission,
            run_id,
            stage="scoring",
            state="running",
            moment=self._now(),
            message="计算分数。",
            cases_total=view.cases_total,
            cases_done=len(evidence),
        )
        try:
            return score_run(
                cases=view.ordered_cases(),
                evidence=evidence,
                static=static,
                security=security,
                comparison=view.comparison,
            )
        except Exception as error:  # 评分缺陷必须失败，不得四舍五入
            raise InternalDefectError(MESSAGE_INTERNAL_DEFECT) from error

    def _run_reporting(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        view: PackageView,
        static: StaticAnalysisResult,
        security: SecurityDecision,
        evidence: Sequence[CaseEvidence],
        breakdown: ScoreBreakdown,
    ) -> EvaluationOutcome:
        """阶段 `reporting`：组报告 → 与结果**同事务**写入 → 迁 `completed` → 终态帧 → 事件。"""
        self._enter_stage(
            submission,
            run_id,
            stage="reporting",
            state="running",
            moment=self._now(),
            message="生成报告。",
            cases_total=view.cases_total,
            cases_done=len(evidence),
        )
        try:
            report = build_report(
                ReportInputs(
                    evaluation_run_id=run_id,
                    submission_id=submission.submission_id,
                    question_version_id=submission.question_version_id,
                    static=static,
                    security=security,
                    evidence=evidence,
                    breakdown=breakdown,
                    binding=ReportBinding(
                        submission_digest=submission.submission_digest,
                        package_digest=view.package_digest,
                        runner_policy_digest=view.runner_policy_digest,
                        environment_digest=view.environment_digest,
                        question_version_binding=view.question_version_binding,
                        interpreter_version=view.interpreter_version,
                    ),
                ),
                generated_at=self._now(),
            )
        except Exception as error:  # 报告不合契约即缺陷，不得对外声称完成
            raise InternalDefectError(MESSAGE_INTERNAL_DEFECT) from error
        digest = str(report["result_digest"])
        moment = self._now()
        try:
            with self._engine.begin() as connection:
                store.transition_run(
                    connection,
                    evaluation_run_id=run_id,
                    to_state="completed",
                    occurred_at=moment,
                    reason="全部用例执行完毕，报告与结果同事务落库。",
                    terminal_reason=None,
                )
                store.advance_submission_state(
                    connection,
                    submission_id=submission.submission_id,
                    to_state="completed",
                    occurred_at=moment,
                    reason="评测完成。",
                )
                store.persist_result(
                    connection,
                    evaluation_run_id=run_id,
                    score=breakdown.score,
                    max_score=breakdown.max_score,
                    is_pass=breakdown.is_pass,
                    result_digest=digest,
                    report=report,
                    persisted_at=moment,
                )
                progress.append_frame(
                    connection,
                    evaluation_run_id=run_id,
                    event=EVENT_COMPLETED,
                    state="completed",
                    stage="terminal",
                    occurred_at=moment,
                    cases_total=view.cases_total,
                    cases_done=len(evidence),
                    score=breakdown.score,
                    is_pass=breakdown.is_pass,
                    message="评测完成。",
                )
                self._enqueue(
                    connection,
                    subject=EVALUATION_COMPLETED,
                    submission=submission,
                    occurred_at=moment,
                    payload={
                        "submission_id": submission.submission_id,
                        "evaluation_run_id": run_id,
                        "question_version_id": submission.question_version_id,
                        "score": float(breakdown.score),
                        # B3 的去标识化统计需要这两个值：没有 `max_score` 分数无法分桶，
                        # 没有 `is_pass` 通过率恒为空（MAIN 对 CHG-B3-0003 的裁决）。
                        "max_score": float(breakdown.max_score),
                        "is_pass": bool(breakdown.is_pass),
                        "result_digest": digest,
                    },
                    deduplication_key=f"{run_id}:completed",
                )
                # 报告**从库里读回**：报告可读必然意味着结果已持久化（同事务）。
                stored = store.read_report(connection, run_id)
        except IntegrityError as error:
            conflict = schema_conflict(error)
            return self._finish_failure(
                submission,
                run_id,
                terminal_reason="internal_error",
                message=MESSAGE_SCHEMA_CONFLICT if conflict else MESSAGE_PERSIST_FAILED,
                invocations=len(evidence),
                evidence_written=len(evidence),
                cause=str(conflict) if conflict is not None else None,
            )
        except Exception:  # noqa: BLE001 - 结果写入失败 ⇒ 不得对外声称报告完成
            return self._finish_failure(
                submission,
                run_id,
                terminal_reason="internal_error",
                message=MESSAGE_PERSIST_FAILED,
                invocations=len(evidence),
                evidence_written=len(evidence),
            )
        return EvaluationOutcome(
            evaluation_run_id=run_id,
            state="completed",
            stage="terminal",
            terminal_reason=None,
            score=breakdown.score,
            is_pass=breakdown.is_pass,
            result_digest=digest,
            report=stored,
            runner_invocations=len(evidence),
            case_evidence_written=len(evidence),
        )

    # --- 终态落地 ---------------------------------------------------------------------

    def _finish_failure(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        *,
        terminal_reason: str,
        message: str,
        invocations: int,
        evidence_written: int,
        cause: str | None = None,
    ) -> EvaluationOutcome:
        """把运行落到 `failed`（技术原因）并写出 `failed` 终态帧 + `evaluation.failed` 事件。

        提交侧只在**已经进入 `running`** 时迁到 `failed`（契约冻结表里 `failed` 只能从 `running`
        到达——"技术失败以执行被尝试过为前提"）。运行前的技术失败（取包/工作区/未检查）不写提交
        状态：契约没有 `validated -> failed` 这条迁移，**不为了好看而编造一条**；此时提交的真实
        状态就是"已校验但未评测"，由运行终态与 SSE 帧表达。
        """
        moment = self._now()
        with self._engine.begin() as connection:
            store.transition_run(
                connection,
                evaluation_run_id=run_id,
                to_state="failed",
                occurred_at=moment,
                reason=message,
                terminal_reason=terminal_reason,
            )
            store.advance_submission_state(
                connection,
                submission_id=submission.submission_id,
                to_state="failed",
                occurred_at=moment,
                reason=terminal_reason,
            )
            progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_FAILED,
                state="failed",
                stage="terminal",
                occurred_at=moment,
                terminal_reason=terminal_reason,
                message=message,
            )
            self._enqueue(
                connection,
                subject=EVALUATION_FAILED,
                submission=submission,
                occurred_at=moment,
                payload={
                    "submission_id": submission.submission_id,
                    "evaluation_run_id": run_id,
                    "question_version_id": submission.question_version_id,
                    "stage": "terminal",
                    "failure_category": terminal_reason,
                    "terminal_reason": terminal_reason,
                },
                deduplication_key=f"{run_id}:failed",
            )
        return EvaluationOutcome(
            evaluation_run_id=run_id,
            state="failed",
            stage="terminal",
            terminal_reason=terminal_reason,
            score=None,
            is_pass=None,
            result_digest=None,
            report=None,
            runner_invocations=invocations,
            case_evidence_written=evidence_written,
            message=message if cause is None else f"{message}（{cause}）",
        )

    def _finish_rejected(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        *,
        reason: str,
        message: str,
        invocations: int = 0,
        evidence_written: int = 0,
    ) -> EvaluationOutcome:
        """把运行落到 `rejected`（静态阻断/安全阻断/执行期沙箱违规）并写出 `rejected` 终态帧。

        `rejected` **没有领域事件**：拒绝只对提交会话有意义，经 SSE 终态帧送达。

        提交侧同事务迁 `rejected`：静态/安全阻断发生在进入沙箱之前（`validated -> rejected`），
        执行期沙箱违规发生在执行之中（`running -> rejected`）——两条路径都是契约
        `SubmissionTransitions` 明文允许的形态。

        `invocations`/`evidence_written` 如实传递（执行期拒绝时可能已经有用例跑完并落库）；
        两者都**不构成分数**：`score`/`is_pass`/`report` 恒为 `None`。
        """
        moment = self._now()
        with self._engine.begin() as connection:
            store.transition_run(
                connection,
                evaluation_run_id=run_id,
                to_state="rejected",
                occurred_at=moment,
                reason=message,
                terminal_reason=reason,
            )
            store.advance_submission_state(
                connection,
                submission_id=submission.submission_id,
                to_state="rejected",
                occurred_at=moment,
                reason=reason,
            )
            progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_REJECTED,
                state="rejected",
                stage="terminal",
                occurred_at=moment,
                terminal_reason=reason,
                message=message,
            )
        return EvaluationOutcome(
            evaluation_run_id=run_id,
            state="rejected",
            stage="terminal",
            terminal_reason=reason,
            score=None,
            is_pass=None,
            result_digest=None,
            report=None,
            runner_invocations=invocations,
            case_evidence_written=evidence_written,
            message=message,
        )

    # --- 内部工具 ---------------------------------------------------------------------

    def _enter_stage(
        self,
        submission: SubmissionEnvelope,
        run_id: str,
        *,
        stage: str,
        state: str,
        moment: datetime,
        message: str,
        cases_total: int | None = None,
        cases_done: int | None = None,
        emit_event: bool = True,
    ) -> ProgressFrame:
        """进入一个阶段：推进运行阶段 + 写一帧（+ 可选入队 `progress` 事件，同一事务）。

        `emit_event=False` 用于 `started` **之前**的阶段（`queued`/`fetching_package`）：契约要求
        同一次运行的领域事件顺序是 `started -> progress -> completed|failed`，那两帧只走 SSE 通道。
        """
        with self._engine.begin() as connection:
            store.advance_stage(
                connection, evaluation_run_id=run_id, stage=stage, occurred_at=moment
            )
            frame = progress.append_frame(
                connection,
                evaluation_run_id=run_id,
                event=EVENT_PROGRESS,
                state=state,
                stage=stage,
                occurred_at=moment,
                cases_total=cases_total,
                cases_done=cases_done,
                message=message,
            )
            if emit_event:
                self._enqueue_progress(
                    connection,
                    submission=submission,
                    run_id=run_id,
                    frame=frame,
                    occurred_at=moment,
                )
        return frame

    def _read_sources(self, workspace: Path, files: Sequence[EnvelopeFile]) -> dict[str, str]:
        """读取待判定源码文本（有界读取；只读包络已声明的路径）。"""
        sources: dict[str, str] = {}
        for item in files:
            path = Path(workspace) / item.path
            if not path.is_file():
                raise InfrastructureError(f"工作区缺少已声明文件：{item.path}")
            if path.stat().st_size > SOURCE_READ_LIMIT:
                raise InfrastructureError(f"待判定文件超过读取上限：{item.path}")
            sources[item.path] = path.read_text(encoding="utf-8", errors="replace")
        return sources

    def _enqueue_progress(
        self,
        connection: Connection,
        *,
        submission: SubmissionEnvelope,
        run_id: str,
        frame: ProgressFrame,
        occurred_at: datetime,
    ) -> None:
        """为一帧进度入队 `evaluation.progress`（payload 只有标识、阶段与计数）。"""
        payload: dict[str, object] = {
            "evaluation_run_id": run_id,
            "stage": frame.stage,
            "sequence": frame.sequence,
        }
        if frame.cases_total is not None:
            payload["cases_total"] = frame.cases_total
        if frame.cases_done is not None:
            payload["cases_done"] = frame.cases_done
        self._enqueue(
            connection,
            subject=EVALUATION_PROGRESS,
            submission=submission,
            occurred_at=occurred_at,
            payload=payload,
            deduplication_key=f"{run_id}:progress:{frame.sequence}",
        )

    def _enqueue(
        self,
        connection: Connection,
        *,
        subject: str,
        submission: SubmissionEnvelope,
        occurred_at: datetime,
        payload: Mapping[str, object],
        deduplication_key: str,
    ) -> None:
        """outbox 先写后发（与业务变更同事务）；payload 只含标识、计数、分数与摘要。

        `event_id` 是 **UUID v4**（MAIN 对 CHG-0001 的判定：契约优先）。

        这里原本用 `uuid5(NAMESPACE_URL, f"b2-evaluation:{deduplication_key}")` 做确定性派生，
        理由是"同一运行重放得到同一标识"。那条理由不成立：重放幂等**已经**由 outbox 的
        `(subject, deduplication_key)` 唯一索引与 `Nats-Msg-Id` 去重窗口承担，v5 只是让
        "事件唯一标识"这一语义与 `MAIN/docs/api/identifiers.md`（平台标识统一 v4）相冲突，
        并给消费方留下"同一 event_id 可能是两次不同投递"的歧义。裁定见
        `MAIN/docs/contracts/changes/CHG-0001-event-id-uuid-version.md`。
        """
        event = build_event(
            subject=subject,
            payload=payload,
            correlation_id=submission.correlation_id,
            causation_id=None,
            occurred_at=occurred_at,
            event_id=str(uuid.uuid4()),
        )
        enqueue(connection, event, deduplication_key=deduplication_key, enqueued_at=occurred_at)
