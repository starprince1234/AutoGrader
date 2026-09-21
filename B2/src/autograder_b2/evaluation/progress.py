"""进度帧：单调 `sequence` 取号、帧写入与**可断线重连**的读取器（SSE 的数据来源）。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame`（本模块只引用其取值集合，
测试逐字比对）。三条不可协商的性质：

1. **单调递增**：`sequence` 在 `b2.evaluation_runs` 行锁内取号（`SELECT ... FOR UPDATE`），并发写入
   被串行化；`UNIQUE(run, sequence)` 是第二道防线。取号与插入在同一事务内完成。
2. **终态帧唯一**：一个运行只允许一个 `completed`/`rejected`/`failed` 帧；写第二个直接拒绝。
3. **帧内无受保护内容**：帧结构里**没有**承载用例输入、期望输出或源码的字段——这不是"记得别写"，
   而是结构上写不进去；`message` 仍要脱敏（去控制字符、去内部绝对路径）并截断到 1000。

读取器 `read_frames(after=N)` 只按 `sequence > N` 取帧，因此客户端断线重连只需带上最后一帧的
`sequence`：不重复、不遗漏。`frame_stream(...)` 把同一语义包成生成器供 SSE 路由使用（**路由注册由
集成方完成**，本模块只提供服务函数）。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.db.schema import evaluation_runs, progress_events

#: 帧事件取值域（契约 `EvaluationStreamFrame.event`）。
EVENT_PROGRESS = "progress"
EVENT_COMPLETED = "completed"
EVENT_REJECTED = "rejected"
EVENT_FAILED = "failed"
FRAME_EVENTS = (EVENT_PROGRESS, EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)
TERMINAL_EVENTS = (EVENT_COMPLETED, EVENT_REJECTED, EVENT_FAILED)

#: 运行状态取值域（契约 `EvaluationStreamFrame.state` 是提交状态全集，运行只使用其中五个）。
RUN_STATES = ("queued", "running", "completed", "rejected", "failed")
#: 阶段取值域（契约 `EvaluationStreamFrame.stage`；与迁移 0006 的 CHECK 同域）。
STAGES = (
    "queued",
    "fetching_package",
    "static_analysis",
    "security_check",
    "dynamic_execution",
    "scoring",
    "reporting",
    "terminal",
)
#: 终态原因取值域（契约 `EvaluationStreamFrame.terminal_reason`）。
TERMINAL_REASONS = ("static_blocked", "security_blocked", "infrastructure_error", "internal_error")

#: 终态与其原因的**配对**（契约明文：阻断类原因伴随 `rejected`，技术类原因伴随 `failed`）。
#: 只校验"是枚举成员"是不够的：`rejected + infrastructure_error` 会让状态与原因互相矛盾，
#: 下游（SSE 帧、统计、告警）无法判断到底发生了什么。迁移 **0009** 起数据库 CHECK 同语义。
TERMINAL_REASONS_BY_STATE: Mapping[str, tuple[str, ...]] = {
    EVENT_REJECTED: ("static_blocked", "security_blocked"),
    EVENT_FAILED: ("infrastructure_error", "internal_error"),
}

#: `b2.progress_events.message` 的 CHECK 上限。
MESSAGE_LIMIT = 1000
#: 单次读取的帧数上限（重连时客户端按 `sequence` 续读，不需要一次取完历史）。
MAX_FRAME_BATCH = 500

#: 已知的"数据库约束与契约语义冲突"约束名 → 人类可读说明。
#:
#: **当前无可达冲突**：迁移 **0007** 已把这两条约束改成与契约同语义（`completed` 帧/运行必须
#: `terminal_reason IS NULL` 且带分数，`rejected`/`failed` 必须带失败原因），迁移 **0009** 进一步
#: **强制原因与终态的配对**（`rejected` 只带 `static_blocked`/`security_blocked`，`failed` 只带
#: `infrastructure_error`/`internal_error`），因此正常的成功路径与三种合法终态都能直接落库。
#: 保留这份映射与 `SchemaConflictError` 兜底，是因为"库结构与契约不一致"仍可能因
#: 部署/迁移落后而出现——那时如实归类为 schema 冲突，而**不**编造一个假的失败原因去迎合约束。
SCHEMA_CONFLICT_CONSTRAINTS: dict[str, str] = {
    "ck_progress_events_terminal_frames": "终态帧的完成/失败语义与本契约不一致（迁移 0007 对齐语义、0009 强制配对）",
    "ck_evaluation_runs_terminal_consistency": "终态的完成/失败语义与本契约不一致（迁移 0007 对齐语义、0009 强制配对）",
}


class FrameRejected(ValueError):
    """帧不满足写入前校验（事件/状态/阶段/分数/终态原因/计数/长度）。"""


class TerminalFrameExists(RuntimeError):
    """该运行已写过终态帧：终态帧必须恰好一个，第二个一律拒绝。"""


class RunMissingForFrame(LookupError):
    """帧挂在一个不存在的运行上（`evaluation_runs` 外键的前置条件不满足）。"""


class SchemaConflictError(RuntimeError):
    """数据库约束与冻结契约/语义冲突：显式失败，不用假值把冲突掩盖过去。"""


def schema_conflict(error: BaseException) -> SchemaConflictError | None:
    """把 `IntegrityError` 归类成 schema 冲突（否则返回 `None`，交由调用方按业务失败处理）。"""
    if not isinstance(error, IntegrityError):
        return None
    text = str(getattr(error, "orig", error))
    for constraint, description in SCHEMA_CONFLICT_CONSTRAINTS.items():
        if constraint in text:
            return SchemaConflictError(f"{constraint}: {description}")
    return None


def redact_message(text: str | None, *, limit: int = MESSAGE_LIMIT) -> str | None:
    """脱敏：去控制字符与内部绝对路径、折叠空白，并截断到 `limit`。

    帧与迁移史会进入学生可见的界面，因此这里宁可丢信息也不留内部路径。
    """
    if text is None:
        return None
    cleaned = "".join(ch if (ch.isprintable() or ch == " ") else "\ufffd" for ch in str(text))
    cleaned = " ".join(cleaned.split())
    for marker in ("/home/", "/var/", "/tmp/", "/srv/", "D:\\", "C:\\"):
        while marker in cleaned:
            start = cleaned.index(marker)
            end = cleaned.find(" ", start)
            end = len(cleaned) if end < 0 else end
            cleaned = f"{cleaned[:start]}<redacted-path>{cleaned[end:]}"
    return cleaned if len(cleaned) <= limit else cleaned[:limit]


@dataclass(frozen=True, slots=True)
class ProgressFrame:
    """一帧进度（与契约 `EvaluationStreamFrame` 同形，字段顺序不影响契约）。"""

    evaluation_run_id: str
    sequence: int
    event: str
    state: str
    stage: str
    occurred_at: datetime
    cases_total: int | None = None
    cases_done: int | None = None
    score: Decimal | None = None
    is_pass: bool | None = None
    terminal_reason: str | None = None
    message: str | None = None

    def as_contract_frame(self) -> dict[str, object]:
        """序列化为契约帧（SSE 的 `data`）：分数转 `float`，时间转 RFC 3339 毫秒。"""
        return {
            "evaluation_run_id": self.evaluation_run_id,
            "event": self.event,
            "state": self.state,
            "stage": self.stage,
            "sequence": self.sequence,
            "cases_total": self.cases_total,
            "cases_done": self.cases_done,
            "score": None if self.score is None else float(self.score),
            "is_pass": self.is_pass,
            "terminal_reason": self.terminal_reason,
            "message": self.message,
            "occurred_at": rfc3339_millis(self.occurred_at),
        }


def _as_score(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise FrameRejected("score 必须是数字或 None")
    try:
        score = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FrameRejected("score 必须是数字或 None") from exc
    if not score.is_finite() or score < 0:
        raise FrameRejected("score 必须是非负有限数")
    return score


def _validate_frame(
    *,
    event: str,
    state: str,
    stage: str,
    cases_total: int | None,
    cases_done: int | None,
    score: Decimal | None,
    is_pass: bool | None,
    terminal_reason: str | None,
) -> None:
    """写入**之前**的帧校验：不合规的帧绝不落库（读侧就不必再防）。"""
    if event not in FRAME_EVENTS:
        raise FrameRejected(f"未知帧事件：{event!r}")
    if state not in RUN_STATES:
        raise FrameRejected(f"未知运行状态：{state!r}")
    if stage not in STAGES:
        raise FrameRejected(f"未知阶段：{stage!r}")
    if event == EVENT_COMPLETED:
        # 契约：`completed` 帧不是失败帧，`terminal_reason` 必须为空。迁移 0007 起数据库约束与之同语义
        # （此前 0006 会拒绝它），因此真实的成功帧能正常落库；若库结构落后导致拒绝，由
        # `SchemaConflictError` 如实暴露——这里不填假值把它糊过去。
        if terminal_reason is not None:
            raise FrameRejected("completed 帧不是失败帧：terminal_reason 必须为空")
        if score is None:
            raise FrameRejected("completed 帧必须携带 score")
        if is_pass is None:
            raise FrameRejected("completed 帧必须携带 is_pass")
    elif event in TERMINAL_EVENTS:
        allowed = TERMINAL_REASONS_BY_STATE.get(event, ())
        if terminal_reason not in allowed:
            raise FrameRejected(
                f"终态帧 {event!r} 必须携带与它配对的原因（允许：{'/'.join(allowed)}），"
                f"实得 {terminal_reason!r}"
            )
    elif terminal_reason is not None:
        raise FrameRejected("非终态帧不得携带 terminal_reason")
    if event != EVENT_COMPLETED and (score is not None or is_pass is not None):
        # 契约：score/is_pass 只在 completed 帧出现；未完成的运行绝不以 0 分呈现。
        raise FrameRejected("只有 completed 帧可以携带 score/is_pass")
    if cases_total is not None and not isinstance(cases_total, int):
        raise FrameRejected("cases_total 必须是整数或 None")
    if cases_done is not None and not isinstance(cases_done, int):
        raise FrameRejected("cases_done 必须是整数或 None")
    for name, count in (("cases_total", cases_total), ("cases_done", cases_done)):
        if count is not None and count < 0:
            raise FrameRejected(f"{name} 必须非负")
    if cases_total is not None and cases_done is not None and cases_done > cases_total:
        raise FrameRejected("cases_done 不得大于 cases_total")


def next_sequence(connection: Connection, *, evaluation_run_id: str) -> int:
    """取下一个帧序号：锁住运行行后 `max(sequence) + 1`（首帧为 1）。

    并发写入同一运行时被行锁串行化，因此不会取到重复序号；`UNIQUE(run, sequence)` 仍然保留，
    用于兜住"绕过本函数直接写表"的情况。
    """
    identifier = _lock_run(connection, evaluation_run_id)
    current = connection.execute(
        sa.select(sa.func.coalesce(sa.func.max(progress_events.c.sequence), 0)).where(
            progress_events.c.evaluation_run_id == identifier
        )
    ).scalar_one()
    return int(current) + 1


def append_frame(
    connection: Connection,
    *,
    evaluation_run_id: str,
    event: str,
    state: str,
    stage: str,
    occurred_at: datetime,
    cases_total: int | None = None,
    cases_done: int | None = None,
    score: object | None = None,
    is_pass: bool | None = None,
    terminal_reason: str | None = None,
    message: str | None = None,
) -> ProgressFrame:
    """在调用方事务内追加一帧（取号 → 校验 → 插入 → 同步运行行计数）。

    校验先于任何写入。终态帧的语义（迁移 0007 起）：`completed` 帧**必须带分数且不带失败原因**
    （`terminal_reason` 为 NULL），`rejected`/`failed` 帧**必须带失败原因**——成功的运行不再被
    强制编造一个失败原因，因此真实的 `completed` 现在能落库（此前 0006 的约束会拒绝它，这也是
    `schema_conflict(...)` 这个兜底仍然存在的原因：库结构与契约不一致时如实归类，不写假值）。
    """
    identifier = _lock_run(connection, evaluation_run_id)
    resolved_score = _as_score(score)
    safe_message = redact_message(message)
    _validate_frame(
        event=event,
        state=state,
        stage=stage,
        cases_total=cases_total,
        cases_done=cases_done,
        score=resolved_score,
        is_pass=is_pass,
        terminal_reason=terminal_reason,
    )
    if event in TERMINAL_EVENTS and terminal_frame(connection, evaluation_run_id) is not None:
        raise TerminalFrameExists(f"{evaluation_run_id}: 终态帧已存在，不得重复写")
    sequence = next_sequence(connection, evaluation_run_id=evaluation_run_id)
    connection.execute(
        sa.insert(progress_events).values(
            progress_event_id=uuid.uuid4(),
            evaluation_run_id=identifier,
            sequence=sequence,
            event=event,
            state=state,
            stage=stage,
            cases_total=cases_total,
            cases_done=cases_done,
            score=resolved_score,
            is_pass=is_pass,
            terminal_reason=terminal_reason,
            message=safe_message,
            occurred_at=occurred_at,
        )
    )
    values: dict[str, object] = {"sequence": sequence, "updated_at": occurred_at}
    if cases_total is not None:
        values["cases_total"] = cases_total
    if cases_done is not None:
        values["cases_done"] = cases_done
    connection.execute(
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .values(**values)
    )
    return ProgressFrame(
        evaluation_run_id=str(identifier),
        sequence=sequence,
        event=event,
        state=state,
        stage=stage,
        occurred_at=occurred_at,
        cases_total=cases_total,
        cases_done=cases_done,
        score=resolved_score,
        is_pass=is_pass,
        terminal_reason=terminal_reason,
        message=safe_message,
    )


def read_frames(
    connection: Connection,
    *,
    evaluation_run_id: str,
    after: int = 0,
    limit: int = MAX_FRAME_BATCH,
) -> list[ProgressFrame]:
    """按 `sequence > after` 升序取帧（断线重连语义：不重复、不遗漏）。"""
    if after < 0:
        raise FrameRejected("after 必须非负")
    if limit <= 0 or limit > MAX_FRAME_BATCH:
        raise FrameRejected(f"limit 必须在 1..{MAX_FRAME_BATCH} 之间")
    rows = connection.execute(
        sa.select(progress_events)
        .where(progress_events.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id)))
        .where(progress_events.c.sequence > after)
        .order_by(progress_events.c.sequence)
        .limit(limit)
    ).fetchall()
    return [
        ProgressFrame(
            evaluation_run_id=str(row.evaluation_run_id),
            sequence=int(row.sequence),
            event=row.event,
            state=row.state,
            stage=row.stage,
            occurred_at=row.occurred_at,
            cases_total=row.cases_total,
            cases_done=row.cases_done,
            score=None if row.score is None else Decimal(row.score),
            is_pass=row.is_pass,
            terminal_reason=row.terminal_reason,
            message=row.message,
        )
        for row in rows
    ]


def terminal_frame(connection: Connection, evaluation_run_id: str) -> ProgressFrame | None:
    """取该运行的终态帧（没有则 `None`）；用于"终态帧恰好一个"的判定。"""
    rows = connection.execute(
        sa.select(progress_events)
        .where(progress_events.c.evaluation_run_id == uuid.UUID(str(evaluation_run_id)))
        .where(progress_events.c.event.in_(TERMINAL_EVENTS))
        .order_by(progress_events.c.sequence)
        .limit(1)
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return ProgressFrame(
        evaluation_run_id=str(row.evaluation_run_id),
        sequence=int(row.sequence),
        event=row.event,
        state=row.state,
        stage=row.stage,
        occurred_at=row.occurred_at,
        cases_total=row.cases_total,
        cases_done=row.cases_done,
        score=None if row.score is None else Decimal(row.score),
        is_pass=row.is_pass,
        terminal_reason=row.terminal_reason,
        message=row.message,
    )


def frame_stream(
    *,
    engine: Engine,
    evaluation_run_id: str,
    after: int = 0,
    poll_interval: float = 0.25,
    idle_timeout: float = 30.0,
    limit: int = MAX_FRAME_BATCH,
    sleep: Callable[[float], None] | None = None,
) -> Iterator[ProgressFrame]:
    """SSE 用的帧生成器（**签名由集成方在路由里调用**）：

    ```python
    for frame in frame_stream(engine=engine, evaluation_run_id=run_id, after=last_sequence):
        yield f"event: {frame.event}\\ndata: {json.dumps(frame.as_contract_frame())}\\n\\n"
    ```

    语义：从 `after` 之后开始、按 `sequence` 升序产出；每产出一帧就把游标推进到它的 `sequence`；
    一旦产出终态帧即结束（客户端因此必然看到 `completed`/`rejected`/`failed` 三者之一）。
    `idle_timeout` 是**服务端等待上限**：超时且仍无新帧即结束生成器（客户端重新连接时会带上
    最后一帧的 `sequence`，因此不会漏帧）。`sleep` 仅为测试可见的接缝，默认 `time.sleep`。

    **已在终态的运行的语义**：如果该运行**已经有**终态帧、且 `after` 已经不小于该终态帧的序号
    （即客户端在终态之后重连），生成器**立即结束**、不再空等 `idle_timeout`——否则客户端要等满
    30 秒才看到连接关闭（组级复核实测 30.44s / 0 帧），无法据"连接是否立刻结束"判断运行已终结。
    只有"终态帧还没被这个客户端读到"（`after` 小于终态帧序号）时才继续补发。
    """
    if after < 0:
        raise FrameRejected("after 必须非负")
    waiter = time.sleep if sleep is None else sleep
    cursor = after
    with engine.connect() as connection:
        terminal = terminal_frame(connection, evaluation_run_id)
    if terminal is not None and terminal.sequence <= after:
        return
    waited = 0.0
    while True:
        with engine.connect() as connection:
            frames = read_frames(
                connection, evaluation_run_id=evaluation_run_id, after=cursor, limit=limit
            )
        if frames:
            waited = 0.0
            for frame in frames:
                cursor = frame.sequence
                yield frame
                if frame.event in TERMINAL_EVENTS:
                    return
            continue
        if waited >= idle_timeout:
            return
        waiter(poll_interval)
        waited += poll_interval


def _lock_run(connection: Connection, evaluation_run_id: str) -> uuid.UUID:
    """锁住运行行并返回主键（帧序号与运行行计数的并发串行化点）。"""
    try:
        identifier = uuid.UUID(str(evaluation_run_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RunMissingForFrame(f"非法运行标识：{evaluation_run_id!r}") from exc
    locked = connection.execute(
        sa.select(evaluation_runs.c.evaluation_run_id)
        .where(evaluation_runs.c.evaluation_run_id == identifier)
        .with_for_update()
    ).fetchone()
    if locked is None:
        raise RunMissingForFrame(f"运行不存在：{evaluation_run_id}")
    return identifier
