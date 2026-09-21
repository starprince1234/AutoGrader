"""NATS JetStream 连接、发布与订阅封装：只做 I/O，不做业务判断，也**不吞异常**。

契约与运维边界：

- 5 个发布 subject 与 1 个订阅 subject 见 `MAIN/contracts/asyncapi/events.yaml` 与
  [docs/api/events.md](../../docs/api/events.md)；本模块不定义任何 subject 常量；
- **流的配额、保留策略与 MQ 运维由 B4 负责**（RQ-B-4-20）：本模块不建流、不改流、不删流，
  只发布与订阅。流不存在时 `publish` 抛 `EventBusUnavailable`（绝不当成"发过了"）；
- 失败原因**按源脱敏后再放进异常文本**（`redact_url` 去掉 DSN 里的凭据；`events.payload.
  sanitize_error` 在落库前再兜一层），因为异常可能被上层日志记录。

投递语义：`publish` 用 `Nats-Msg-Id = event_id` 请求 JetStream 的去重窗口幂等（发布幂等由
`b2.outbox_events` 的 `(subject, deduplication_key)` 唯一索引与 `event_id` 双重保证）；
重复投递被 JetStream 判为 duplicate 时**仍算发布成功**（这正是幂等的表现）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import nats
from nats.aio.client import Client
from nats.js import api
from nats.js.client import JetStreamContext

from autograder_b2.logging.logger import log_dependency_unavailable

#: 总线 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.events.nats")

#: 连接串环境变量名（与 `.env.example` 同名；`config.Settings.nats_url` 也从这里读）。
NATS_URL_ENV = "NATS_URL"
#: 连接默认名（NATS 侧 `name` 字段，便于 `nats server report connections` 识别）。
DEFAULT_CLIENT_NAME = "autograder-b2-events"
#: 单次连接尝试的上限（秒）。取值小是为了让"broker 不可用"尽快变成 `failed`，而不是拖住派发器。
DEFAULT_CONNECT_TIMEOUT = 2.0
#: 发布应答（PubAck）等待上限（秒）。
DEFAULT_PUBLISH_TIMEOUT = 5.0
#: 重连间隔（秒）与初始连接的最大尝试轮数。
DEFAULT_RECONNECT_TIME_WAIT = 0.5
DEFAULT_MAX_RECONNECT_ATTEMPTS = 1


class NatsConfigurationError(RuntimeError):
    """缺少 NATS 连接配置（环境缺失，不是业务失败）。"""


class EventBusUnavailable(RuntimeError):
    """连接或发布失败（broker 不可达 / 流不存在 / 应答超时）。

    异常文本已去掉凭据与主机权威段；派发器据此写**脱敏** `last_error` 并保留行（I29/I33）。
    """


def nats_url_from_env(environ: Mapping[str, str] | None = None) -> str:
    """从环境读取 NATS 连接串（`NATS_URL`）；缺失即明确报错，不做隐式默认。"""
    source = os.environ if environ is None else environ
    url = (source.get(NATS_URL_ENV) or "").strip()
    if not url:
        raise NatsConfigurationError(f"{NATS_URL_ENV} is not set")
    return url


def redact_url(url: str) -> str:
    """去掉连接串里的凭据（`nats://user:pass@host` → `nats://<redacted-credential>@host`）。

    凭据绝不进异常文本、更不进 `last_error`；主机与端口在这里保留（运维需要知道连的是谁），
    落库前由 `events.payload.sanitize_error` 再按"内部主机端口不可外泄"替换。
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    _, at_sign, host = rest.rpartition("@")
    if not at_sign:
        return url
    return f"{scheme}://<redacted-credential>@{host}"


@dataclass(slots=True)
class NatsEventBus:
    """JetStream 事件总线（连接 + 发布 + 拉取订阅）。

    典型用法（一个事件循环内）：`await bus.connect()` → `bus.publish(...)` / `bus.pull_subscribe(...)`
    → `await bus.close()`。连接失败不抛出构造期异常，由调用方在 `publish`/`pull_subscribe` 处看到
    `EventBusUnavailable`——派发器因此可以把"投递失败"如实计入 `DispatchReport.failed`。
    """

    url: str
    client_name: str = DEFAULT_CLIENT_NAME
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    publish_timeout: float = DEFAULT_PUBLISH_TIMEOUT
    reconnect_time_wait: float = DEFAULT_RECONNECT_TIME_WAIT
    max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS
    _client: Client | None = field(default=None, repr=False)
    _jetstream: JetStreamContext | None = field(default=None, repr=False)
    _errors: list[str] = field(default_factory=list, repr=False)

    async def connect(self) -> None:
        """建立连接（幂等：已连接则直接返回）。

        连接期错误由 `error_cb` **记录**而不是丢弃（`last_error` 可查），初始连接失败则抛
        `EventBusUnavailable`——绝不退化为"静默不可用"。失败后不保留半连接状态：下一次
        `publish`/`pull_subscribe` 会重新尝试连接（派发器因而每轮都能如实报出真实失败原因）。
        """

        async def _record(error: Exception) -> None:
            self._errors.append(f"{type(error).__name__}")

        if self._client is not None and self._client.is_connected:
            return
        try:
            client = await nats.connect(
                self.url,
                name=self.client_name,
                connect_timeout=self.connect_timeout,
                reconnect_time_wait=self.reconnect_time_wait,
                max_reconnect_attempts=self.max_reconnect_attempts,
                error_cb=_record,
            )
        except Exception as error:
            self._client = None
            self._jetstream = None
            # 依赖不可用**有日志也有状态**（RQ-B-2-99 场景 2-99-D）：状态侧是**本对象**的
            # `error_count` 与 `last_error`（供诊断与测试读取；readiness 读的是"配置 + outbox 账本"，
            # 见 `B2Runtime.broker_health` —— 只有 DLQ 非空才算 unavailable），日志侧是统一的依赖故障
            # 事件 `messaging.broker_unavailable`。**不吞异常**：照旧抛 `EventBusUnavailable`。
            log_dependency_unavailable(
                logger,
                dependency="message_broker",
                error=error,
                stage="connect",
            )
            raise EventBusUnavailable(
                f"connect failed on {redact_url(self.url)}: {type(error).__name__}: {error}"
            ) from error
        self._client = client
        self._jetstream = client.jetstream()

    async def _ensure_jetstream(self) -> JetStreamContext:
        """取 JetStream 上下文；未连接时**惰性连接**（失败原因如实上抛）。"""
        if self._jetstream is None:
            await self.connect()
        if self._jetstream is None:  # pragma: no cover - connect 成功必然赋值
            raise EventBusUnavailable(f"not connected: {redact_url(self.url)}")
        return self._jetstream

    async def close(self) -> None:
        """关闭连接（不 drain：drain 会在订阅未 flush 时超时并抛错，掩盖真正的失败）。"""
        if self._client is not None:
            await self._client.close()
        self._client = None
        self._jetstream = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def last_error(self) -> str | None:
        """最近一次连接/回调错误的**类型名**（不含 URL 与凭据；空表示没有错误）。"""
        return self._errors[-1] if self._errors else None

    @property
    def error_count(self) -> int:
        return len(self._errors)

    def jetstream(self) -> JetStreamContext:
        """返回原始 JetStream 上下文（供测试与运维探测；生产不在此建流/改流）。"""
        if self._jetstream is None:
            raise EventBusUnavailable(f"not connected: {redact_url(self.url)}")
        return self._jetstream

    async def publish(self, subject: str, payload: bytes, *, msg_id: str | None = None) -> api.PubAck:
        """发布一条消息到 JetStream 并等待应答；任何失败都抛 `EventBusUnavailable`。"""
        jetstream = await self._ensure_jetstream()
        headers: dict[str, Any] | None = None
        if msg_id is not None:
            headers = {api.Header.MSG_ID: msg_id}
        try:
            return await jetstream.publish(
                subject, payload, timeout=self.publish_timeout, headers=headers
            )
        except Exception as error:
            raise EventBusUnavailable(
                f"publish failed for {subject} on {redact_url(self.url)}: "
                f"{type(error).__name__}: {error}"
            ) from error

    async def pull_subscribe(
        self,
        subject: str,
        *,
        durable: str,
        stream: str | None = None,
        deliver_policy: api.DeliverPolicy = api.DeliverPolicy.ALL,
        ack_wait: float = 30.0,
        max_deliver: int = 5,
    ) -> JetStreamContext.PullSubscription:
        """建立 durable 拉取订阅（显式 ack：只有处理成功才 `ack`）。"""
        jetstream = await self._ensure_jetstream()
        config = api.ConsumerConfig(
            deliver_policy=deliver_policy,
            ack_policy=api.AckPolicy.EXPLICIT,
            ack_wait=ack_wait,
            max_deliver=max_deliver,
        )
        try:
            return await jetstream.pull_subscribe(
                subject, durable=durable, stream=stream, config=config
            )
        except Exception as error:
            raise EventBusUnavailable(
                f"subscribe failed for {subject} on {redact_url(self.url)}: "
                f"{type(error).__name__}: {error}"
            ) from error
