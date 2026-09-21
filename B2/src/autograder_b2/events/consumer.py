"""订阅端：消费 `autograder.v1.question.version.published`，按 `event_id` 用 inbox 去重。

契约关系见 [docs/api/events.md](../../docs/api/events.md) 第 2 节（订阅 subject、去重键 `event_id`）、
`MAIN/contracts/asyncapi/events.yaml`（at-least-once + inbox 去重）与 `MAIN/docs/api/events.md`
第 3.6 节 / 第 5 节（消费者行为与实现清单）。机制：

- **幂等端**：业务效果与 `b2.inbox_events` 行写在**同一个事务**里。处理失败 → 两者一起回滚，
  消息 `nak` 后重投仍能重新执行；重复投递命中主键 `(consumer, event_id)` → no-op（I30）。
- **异常不静默**：`consume` 直接抛出（不吞）；`run` 逐条记录到 `ConsumerReport.errors`，调用
  `on_error`（若给了钩子），**没给钩子时至少记一条结构化日志** `events.inbox.handler_failed`，
  然后 **nak**（`term` 只用于"重投也不会变好"的畸形/违约消息），绝不静默 ack。
- **本事件不是取包通道**：`question.version.published` 只用于**版本感知（缓存失效 / 调度准入）**，
  **不得**据此执行评测（`MAIN/docs/api/events.md` 第 3.6 节）。本模块因此不落 payload、不取
  evaluation package、不触发任何评测：inbox 只存 `(consumer, event_id, subject, received_at)`
  四列，业务效果完全由调用方通过 `handler` 决定（且只能用"标识 + 本地缓存操作"）。
- **前向兼容**：未识别字段容忍（第 5 节）；未识别的 `event_type` **记录并忽略**（ack、不写 inbox、
  不产生业务效果），既不崩溃也不静默丢弃——"记录"是一条结构化日志（`MAIN/docs/api/events.md`
  第 5 节原文要求"不静默丢弃无日志"），不是只在返回值里体现。日志只写**白名单字段**：事件名
  `events.inbox.unrecognized_event_type`、`event_id`（UUID 形状校验）、`outcome=ignored`，以及
  `detail`（自由文本槽：截断 + 控制字符转义 + 已知敏感模式替换）承载那个未识别的字符串。
  未识别的 `event_type` 是**外部输入**，因此绝不整包记录 envelope。
- **契约校验**：收到的 envelope 先过 `event-envelope.schema.json`（消费侧容忍未知字段，
  其余约束照旧；发布侧严格，见 `events.payload`）。本模块**不**对**收到**的 payload 做键白名单
  ——那是发布方的义务，B3 的 payload 键集合不属于 B2（B2 只坚持契约 envelope 结构 + §4 的
  输出侧自查）。

handler 契约：`handler(connection, envelope)`，`connection` 是当前事务的连接。业务效果**必须**用这个
连接写库，否则"业务效果与 inbox 去重同事务"这条不变量就不成立（`run` 无法代为实现）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import nats
from nats.aio.msg import Msg
from nats.js.api import DeliverPolicy
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert

from autograder_b2.db.schema import inbox_events
from autograder_b2.domain.clock import utc_now
from autograder_b2.events.nats import NatsEventBus
from autograder_b2.events.payload import (
    QUESTION_VERSION_PUBLISHED,
    EventEnvelopeInvalid,
    assert_received_envelope_valid,
    sanitize_error,
)
from autograder_b2.logging.logger import log_event

#: 订阅端 logger：日志名即身份（`logger` 是白名单字段，消费者标识恒为 `DEFAULT_CONSUMER`）。
logger = logging.getLogger("autograder_b2.events.inbox")

#: durable consumer 名（B2 侧订阅者标识；也用作 `inbox_events.consumer` 列的值）。
DEFAULT_CONSUMER = "b2-question-version-published"
#: 拉取超时（秒）与单批消息数上限。
DEFAULT_FETCH_TIMEOUT = 2.0
DEFAULT_BATCH = 10

#: 业务效果回调：`(connection, envelope) -> None`，必须使用传入的连接写库（同事务）。
EventHandler = Callable[[Connection, Mapping[str, Any]], None]
#: 失败回调：`(sanitized_reason, exception) -> None`，用于把失败接进日志/告警。
ErrorHook = Callable[[str, BaseException], None]


class MessageRejected(Exception):
    """消息本身不可处理（非法 JSON / envelope 不通过契约）：重投不会变好，故 `term`。"""


@dataclass(frozen=True, slots=True)
class InboxOutcome:
    """单条消息的处理结论。

    - `duplicate=True`：inbox 去重命中（no-op，不产生第二次业务效果）；
    - `handled=True`：本次真的执行了业务效果；
    - `ignored=True`：`event_type` 不属于本消费者订阅的 subject（记录并忽略，不写 inbox）。
    """

    event_id: str
    event_type: str
    duplicate: bool
    handled: bool
    ignored: bool = False


@dataclass(frozen=True, slots=True)
class ConsumerReport:
    """一次 `run` 的真实计数（`errors` 已脱敏；有失败必然非空，不空转）。

    `ignored` / `ignored_event_types`：订阅 subject 上出现了**未识别的 `event_type`**——
    按 `MAIN/docs/api/events.md` 第 5 节"记录并忽略"（ack、不写 inbox、不产生业务效果）。
    """

    received: int
    handled: int
    duplicates: int
    ignored: int
    failed: int
    errors: tuple[str, ...]
    ignored_event_types: tuple[str, ...]


class InboxConsumer:
    """inbox 去重消费者：同步的 `consume`（事务边界）+ 异步的 `process`/`run`（ack 语义）。"""

    def __init__(
        self,
        engine: Engine,
        handler: EventHandler,
        *,
        consumer: str = DEFAULT_CONSUMER,
        subject: str = QUESTION_VERSION_PUBLISHED,
        on_error: ErrorHook | None = None,
    ) -> None:
        self._engine = engine
        self._handler = handler
        self._consumer = consumer
        self._subject = subject
        self._on_error = on_error

    @property
    def consumer(self) -> str:
        return self._consumer

    @property
    def subject(self) -> str:
        return self._subject

    def consume(self, envelope: Mapping[str, Any]) -> InboxOutcome:
        """幂等处理一条 envelope：去重命中即 no-op；否则在**同一事务**内写 inbox + 业务效果。

        未识别的 `event_type` 不写 inbox、不执行 handler，返回 `ignored=True`，并**记一条结构化
        日志**（契约"记录并忽略"里的"记录"，见模块 docstring）。业务效果抛出的异常会原样传出
        （不吞），事务回滚 ⇒ inbox 行不留痕 ⇒ 重投可重新执行。
        """
        assert_received_envelope_valid(envelope)
        event_id = self._event_id(envelope)
        event_type = str(envelope.get("event_type") or "")
        if not self.recognizes(envelope):
            # 契约要求"记录并忽略"（MAIN/docs/api/events.md 第 5 节："不静默丢弃无日志"）：
            # 返回值之外必须留下可检索的日志痕迹。只记白名单字段，未识别的类型进 `detail` 槽。
            log_event(
                logger,
                "events.inbox.unrecognized_event_type",
                level=logging.WARNING,
                fields={
                    "event_id": str(event_id),
                    "outcome": "ignored",
                    "detail": event_type,
                },
            )
            return InboxOutcome(
                event_id=str(event_id),
                event_type=event_type,
                duplicate=False,
                handled=False,
                ignored=True,
            )
        with self._engine.begin() as connection:
            statement = (
                pg_insert(inbox_events)
                .values(
                    consumer=self._consumer,
                    event_id=event_id,
                    subject=event_type or self._subject,
                    received_at=utc_now(),
                )
                .on_conflict_do_nothing(index_elements=["consumer", "event_id"])
                .returning(inbox_events.c.event_id)
            )
            inserted = connection.execute(statement).fetchone()
            if inserted is None:
                return InboxOutcome(
                    event_id=str(event_id),
                    event_type=event_type,
                    duplicate=True,
                    handled=False,
                )
            self._handler(connection, envelope)
        return InboxOutcome(
            event_id=str(event_id), event_type=event_type, duplicate=False, handled=True
        )

    def recognizes(self, envelope: Mapping[str, Any]) -> bool:
        """`event_type` 是否就是本消费者订阅的 subject（契约：二者同名）。"""
        return str(envelope.get("event_type")) == self._subject

    async def process(self, message: Msg) -> InboxOutcome:
        """处理一条 JetStream 消息：成功 `ack`；失败 `nak`（畸形/违约消息 `term`）。

        异常**重新抛出**（调用方继续记录或上抛）——本方法绝不把失败吞成"处理过了"。
        """
        try:
            envelope = self._decode(message)
        except MessageRejected:
            await message.term()
            raise
        try:
            outcome = self.consume(envelope)
        except Exception:
            await message.nak()
            raise
        await message.ack()
        return outcome

    async def run(
        self,
        bus: NatsEventBus,
        *,
        durable: str | None = None,
        stream: str | None = None,
        max_messages: int | None = None,
        batch: int = DEFAULT_BATCH,
        fetch_timeout: float = DEFAULT_FETCH_TIMEOUT,
        raise_on_error: bool = False,
        deliver_policy: DeliverPolicy = DeliverPolicy.ALL,
        ack_wait: float = 30.0,
        max_deliver: int = 5,
    ) -> ConsumerReport:
        """拉取循环：逐条处理并如实计数；失败记录 + `nak`（不 ack），可选上抛。

        `max_messages=None` 表示一直拉到被取消（生产形态）；测试用具体条数避免无界循环。
        """
        subscription = await bus.pull_subscribe(
            self._subject,
            durable=durable or self._consumer,
            stream=stream,
            deliver_policy=deliver_policy,
            ack_wait=ack_wait,
            max_deliver=max_deliver,
        )
        received = handled = duplicates = ignored = failed = 0
        errors: list[str] = []
        ignored_types: list[str] = []
        while max_messages is None or received < max_messages:
            want = batch if max_messages is None else min(batch, max_messages - received)
            try:
                messages = await subscription.fetch(want, timeout=fetch_timeout)
            except (TimeoutError, nats.errors.TimeoutError):
                break  # 队列已空：本次运行结束（不是失败，也不伪造计数）
            for message in messages:
                received += 1
                try:
                    outcome = await self.process(message)
                except BaseException as error:
                    failed += 1
                    self._record_error(error, errors)
                    if raise_on_error:
                        raise
                else:
                    if outcome.handled:
                        handled += 1
                    if outcome.duplicate:
                        duplicates += 1
                    if outcome.ignored:
                        ignored += 1
                        ignored_types.append(outcome.event_type)
        return ConsumerReport(
            received=received,
            handled=handled,
            duplicates=duplicates,
            ignored=ignored,
            failed=failed,
            errors=tuple(errors),
            ignored_event_types=tuple(ignored_types),
        )

    def _decode(self, message: Msg) -> Mapping[str, Any]:
        """解析消息体：非法 JSON / 非对象 / envelope 违约即 `MessageRejected`（原因已脱敏）。"""
        try:
            envelope = json.loads(message.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MessageRejected(
                sanitize_error(f"undecodable payload on {message.subject}: {type(error).__name__}")
            ) from error
        if not isinstance(envelope, dict):
            raise MessageRejected(sanitize_error(f"payload is not an object on {message.subject}"))
        try:
            assert_received_envelope_valid(envelope)
        except EventEnvelopeInvalid as error:
            raise MessageRejected(
                sanitize_error(f"envelope invalid on {message.subject}: {error}")
            ) from error
        return envelope

    @staticmethod
    def _event_id(envelope: Mapping[str, Any]) -> uuid.UUID:
        """去重键 `event_id`（契约 `format: uuid`）；缺失或不合法即拒（不猜、不生成）。"""
        try:
            return uuid.UUID(str(envelope.get("event_id")))
        except (TypeError, ValueError) as error:
            raise MessageRejected(sanitize_error("event_id is missing or not a uuid")) from error

    def _record_error(self, error: BaseException, errors: list[str]) -> str:
        """把失败记进 `errors`（脱敏）并**留痕**；返回脱敏原因。

        `on_error` 是调用方钩子（接告警/指标）。**没有钩子时也给一条结构化日志**：否则
        "处理失败"就只活在返回值里，一旦拉取循环无人读 `ConsumerReport` 就等于静默吞掉——
        本模块 docstring 承诺的"异常不静默"要求日志兜底。日志只写白名单字段（脱敏 `reason`），
        绝不带 envelope 内容。
        """
        if isinstance(error, (MessageRejected, EventEnvelopeInvalid)):
            reason = sanitize_error(str(error))
        else:
            reason = sanitize_error(f"{type(error).__name__}: {error}")
        errors.append(reason)
        if self._on_error is not None:
            self._on_error(reason, error)
        else:
            log_event(
                logger,
                "events.inbox.handler_failed",
                level=logging.ERROR,
                fields={"outcome": "failed", "reason": reason},
            )
        return reason
