"""派发器：把 `b2.outbox_events` 的未发布行按**入队顺序**投递到 JetStream（I29/I30/I33）。

不变量与失败语义见 [docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 6.3/6.4 节：

- **先写后发**：本模块**只读** outbox 并把已落库的行发出去；业务事务早已提交，NATS 不可用
  只影响投递时延（业务事务不回滚，本模块也不碰业务表）；
- **at-least-once**：允许重复投递；生产端幂等由写入端的 `(subject, deduplication_key)` 唯一索引
  保证（表结构不改），发布侧再用 `Nats-Msg-Id = event_id` 请求 JetStream 去重窗口幂等；
- **顺序**：严格按 `outbox_events.insert_seq`（单调插入序号，迁移 0009）取行、按同序**串行**投递，
  绝不重排。**为什么不是 `enqueued_at`**：写入端会在**同一事务**里连续入队多条事件（例如
  `started` 与第一条 `progress`），它们的 `occurred_at`/`enqueued_at` 逐微秒相同；再用
  `event_id` 兜底排序等于交给随机标识决定，实测有 50.2% 的运行把 `progress` 排到了
  `started` 之前（组级复核发现并复现）。插入序号由数据库按插入发生次序赋值，因此
  "同一事务内先写的先发"成为**可强制**的不变量，与时间戳精度、标识生成方式都无关。
  如实说明边界：某行投递失败后本模块**不**做队头阻塞，会继续投递同批后续行（于是
  `selected == published + failed`）；失败行在**下一轮**重试，因此"失败行"的到达顺序可能晚于
  同批后续事件。跨轮的失败重试不构成重排，但会推迟该事件（详见交付说明）。
- **DLQ 不静默**：失败一律 `attempts += 1` 且写**脱敏** `last_error`，达到上限即置
  `dead_lettered_at`；DLQ 行可查询（`dead_letters`）并可人工重放（`replay`），绝不丢弃、也不只写日志。
- **失败同时留日志，且按类型分派事件名**：`PayloadRejected` / `EventEnvelopeInvalid`（我们自己的
  payload/envelope 不合契约）记 `events.outbox.payload_rejected`；其余（网络/连接类）记统一的依赖故障
  事件 `messaging.broker_unavailable`。两类都含 `event_id`/`attempts`/`error_type` 与脱敏 `reason`，
  满足"依赖不可用要有明确日志与状态"（RQ-B-2-99 场景 2-99-D），且**不**抄 `str(error)`。
- **`last_error` 只描述"还没送达"**：投递成功即清空（`_mark_published`），因此不存在"已发布却挂着
  上一次失败文本"的行；重试次数保留在 `attempts` 里。

与冻结协议的关系：`domain.events.OutboxDispatcher.dispatch(limit) -> DispatchReport` 是**同步**签名，
而 NATS 投递是异步 I/O。本实现提供 `async dispatch`（调用方自带事件循环）与 `dispatch_sync`
（**无事件循环**的入口：自建循环、投递、关连接），投递语义一致；冻结协议本身不做任何修改。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.engine import Row

from autograder_b2.db.schema import outbox_events
from autograder_b2.domain.clock import utc_now
from autograder_b2.domain.events import DispatchReport, DomainEvent
from autograder_b2.events.nats import NatsEventBus
from autograder_b2.events.payload import (
    MAX_LAST_ERROR_LENGTH,
    EventEnvelopeInvalid,
    PayloadRejected,
    envelope_bytes,
    sanitize_error,
)
from autograder_b2.logging.logger import log_dependency_unavailable, log_event

#: 派发端 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.events.outbox")

#: 自动重试的投递上限：达到即进 DLQ（人工重放会把计数清零，见 `replay`）。
DEFAULT_MAX_ATTEMPTS = 5
#: 单次 `dispatch` 的批量上限（避免一次把整张表读进内存）。
DEFAULT_BATCH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """DLQ 行投影：**含脱敏原因**与尝试次数，供查询与人工重放决策。"""

    event_id: str
    subject: str
    attempts: int
    last_error: str | None
    dead_lettered_at: datetime
    enqueued_at: datetime


def event_from_row(row: Row) -> DomainEvent:
    """把 outbox 行还原成冻结的 `DomainEvent`（只做形态还原，不做校验）。"""
    return DomainEvent(
        event_id=str(row.event_id),
        event_type=str(row.event_type),
        schema_version=int(row.schema_version),
        occurred_at=row.occurred_at,
        correlation_id=str(row.correlation_id),
        causation_id=str(row.causation_id) if row.causation_id is not None else None,
        producer=str(row.producer),
        subject=str(row.subject),
        payload=dict(row.payload),
    )


@dataclass(slots=True)
class NatsOutboxDispatcher:
    """JetStream 派发器：`b2.outbox_events` → NATS JetStream。

    每次 `dispatch` 是「取行 → 逐条发布 → 落状态」的短事务循环：**绝不**在持有数据库事务时
    等网络（否则 broker 抖动会顶住连接池）。
    """

    engine: Engine
    bus: NatsEventBus
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    batch_limit: int = DEFAULT_BATCH_LIMIT

    async def dispatch(self, limit: int | None = None) -> DispatchReport:
        """投递一批未发布行；返回如实的 `DispatchReport`（失败不抛、也不静默）。"""
        batch = self.batch_limit if limit is None else max(0, int(limit))
        if batch == 0:
            return DispatchReport(selected=0, published=0, failed=0, dead_lettered=0, last_error=None)
        rows = self.pending_rows(batch)
        published = failed = dead_lettered = 0
        last_error: str | None = None
        for row in rows:
            event_id = str(row.event_id)
            try:
                event = event_from_row(row)
                await self.bus.publish(
                    event.subject, envelope_bytes(event), msg_id=event.event_id
                )
            except Exception as error:  # noqa: BLE001 - 任何失败都留行、留痕，绝不静默跳过
                reason = sanitize_error(f"{type(error).__name__}: {error}")
                failed += 1
                last_error = reason
                # 失败必须**有日志**、不只是有状态（RQ-B-2-99 场景 2-99-D："制造沙箱启动失败 /
                # DB 不可用 / NATS 不可用 → 各有明确日志与状态，不吞异常"；`interfaces.md` §6.4 同）。
                # 按异常类型**分派事件名**：把"我们自己的 payload/envelope 不合契约"报成
                # `messaging.broker_unavailable` 会让内部缺陷去触发 broker 告警、稀释真故障。
                # 两类日志都**不抄 `str(error)`**（异常文本可能带主机名/端口/绝对路径），
                # 只记机器可读字段 + 已脱敏 `reason`；本方法**不改控制流**（照旧留行、计数、继续）。
                fields: dict[str, object] = {
                    "event_id": event_id,
                    "attempts": row.attempts + 1,
                    "error_type": type(error).__name__,
                }
                if isinstance(error, (PayloadRejected, EventEnvelopeInvalid)):
                    log_event(
                        logger,
                        "events.outbox.payload_rejected",
                        level=logging.ERROR,
                        fields={**fields, "outcome": "failed", "reason": reason},
                    )
                else:
                    log_dependency_unavailable(
                        logger,
                        dependency="message_broker",
                        error=error,
                        stage="dispatch",
                        reason=reason,
                        fields=fields,
                    )
                if self._record_failure(event_id, reason):
                    dead_lettered += 1
                continue
            self._mark_published(event_id)
            published += 1
        return DispatchReport(
            selected=len(rows),
            published=published,
            failed=failed,
            dead_lettered=dead_lettered,
            last_error=last_error,
        )

    def dispatch_sync(self, limit: int | None = None) -> DispatchReport:
        """无事件循环入口：**自建循环 → 投递 → 关连接**，投递语义与 `dispatch` 完全一致。

        **为什么必须在返回前关掉连接**：`NatsEventBus` 的连接（含它的事件回调与应答等待）绑在创建它的
        事件循环上，而本入口的循环随 `asyncio.run` 结束。把连接留到下一轮，第二轮的 `publish` 就会
        对着一个已关闭循环的传输层动作：结果是"第一轮投递成功，之后每轮都失败"——最难查的一类故障。
        关掉之后每轮自成一次完整的连接生命周期，重复调用是安全的。

        **关闭必须发生在同一个循环内**（因此是"一个协程里 try/finally"，而不是"两次 `asyncio.run`"）：
        在第二个循环里关第一个循环创建的传输层会抛 `RuntimeError: Event loop is closed`——实测踩到过。

        这一条不是可有可无的细节，而是本入口的**存在理由**：它的调用方（`autograder_b2.worker`）是一个
        同步的长期运行进程，没有常驻事件循环可挂连接。
        """

        async def dispatch_then_close() -> DispatchReport:
            try:
                return await self.dispatch(limit)
            finally:
                await self.bus.close()

        return asyncio.run(dispatch_then_close())

    def pending_rows(self, limit: int = DEFAULT_BATCH_LIMIT) -> list[Row]:
        """未发布且未进 DLQ 的行，按**入队顺序**（`insert_seq` 升序）返回。

        不用 `enqueued_at` 定序：写入端会在同一事务里连续入队多条事件，这些行的 `enqueued_at` 逐微秒
        相同，排序就会退化成 `event_id` 的随机顺序——实测 50.2% 的运行会把
        `progress` 排到 `started` 之前。`insert_seq` 由数据库按插入发生次序赋值，因此同事务内
        "先写的先发"是确定的。
        """
        statement = (
            sa.select(outbox_events)
            .where(
                sa.and_(
                    outbox_events.c.published_at.is_(None),
                    outbox_events.c.dead_lettered_at.is_(None),
                )
            )
            .order_by(outbox_events.c.insert_seq)
            .limit(limit)
        )
        with self.engine.connect() as connection:
            return list(connection.execute(statement).fetchall())

    def dead_letters(self, limit: int = DEFAULT_BATCH_LIMIT) -> tuple[DeadLetter, ...]:
        """DLQ 查询：已进 DLQ 的行（含 subject、尝试次数、脱敏原因、进 DLQ 时刻），按入队顺序。"""
        statement = (
            sa.select(outbox_events)
            .where(outbox_events.c.dead_lettered_at.is_not(None))
            .order_by(outbox_events.c.dead_lettered_at, outbox_events.c.insert_seq)
            .limit(limit)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).fetchall()
        return tuple(
            DeadLetter(
                event_id=str(row.event_id),
                subject=str(row.subject),
                attempts=int(row.attempts),
                last_error=row.last_error,
                dead_lettered_at=row.dead_lettered_at,
                enqueued_at=row.enqueued_at,
            )
            for row in rows
        )

    def replay(self, event_id: str, *, reset_attempts: bool = True) -> bool:
        """人工重放：把一条 DLQ 行放回可投递集合（清 `dead_lettered_at`，默认清零尝试计数）。

        `reset_attempts=True` 是有意的：投递上限约束的是**自动**重试；人工重放通常发生在运维
        修好故障之后，若保留已耗尽的计数，下一次失败会立刻再次进 DLQ 而毫无重试余地。
        返回 `False` 表示该行不在 DLQ（不假装重放成功）。

        保留旧的 `last_error`：重放后该行仍未送达，旧文本仍是"最近一次失败原因"；一旦投递成功
        `_mark_published` 会把它清掉。
        """
        values: dict[str, object] = {"dead_lettered_at": None}
        if reset_attempts:
            values["attempts"] = 0
        statement = (
            sa.update(outbox_events)
            .where(
                sa.and_(
                    outbox_events.c.event_id == uuid.UUID(str(event_id)),
                    outbox_events.c.dead_lettered_at.is_not(None),
                )
            )
            .values(**values)
            .returning(outbox_events.c.event_id)
        )
        with self.engine.begin() as connection:
            return connection.execute(statement).fetchone() is not None

    def _mark_published(self, event_id: str) -> None:
        """置 `published_at`（只在仍为空时写：首次发布时间不被后续重复投递覆盖），并清 `last_error`。

        `last_error` 的语义是"**这行为什么还没送达**"：已投递的行不得继续挂着上一次失败的文本，
        否则运维按行阅读时会把一条已送达的事件读成"仍在失败"。尝试次数保留在 `attempts` 里，
        送达前的历史失败次数不丢。
        """
        statement = (
            sa.update(outbox_events)
            .where(
                sa.and_(
                    outbox_events.c.event_id == uuid.UUID(event_id),
                    outbox_events.c.published_at.is_(None),
                )
            )
            .values(published_at=utc_now(), last_error=None)
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def _record_failure(self, event_id: str, reason: str) -> bool:
        """`attempts += 1` + 写脱敏 `last_error`；达到上限则置 `dead_lettered_at`。

        返回 `True` 表示**本次**把该行移入 DLQ（计数用于 `DispatchReport.dead_lettered`）。
        用单条 SQL 完成"自增 + 判上限"，避免读-改-写竞态。
        """
        text = reason[:MAX_LAST_ERROR_LENGTH]
        statement = (
            sa.update(outbox_events)
            .where(
                sa.and_(
                    outbox_events.c.event_id == uuid.UUID(event_id),
                    outbox_events.c.published_at.is_(None),
                    outbox_events.c.dead_lettered_at.is_(None),
                )
            )
            .values(
                attempts=outbox_events.c.attempts + 1,
                last_error=text,
                dead_lettered_at=sa.case(
                    (outbox_events.c.attempts + 1 >= self.max_attempts, utc_now()),
                    else_=outbox_events.c.dead_lettered_at,
                ),
            )
            .returning(outbox_events.c.attempts, outbox_events.c.dead_lettered_at)
        )
        with self.engine.begin() as connection:
            row = connection.execute(statement).fetchone()
        if row is None:  # pragma: no cover - 行已被并发派发/发布，不是失败
            return False
        return row.dead_lettered_at is not None
