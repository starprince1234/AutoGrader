"""事件 outbox 发布接口：领域事件形态 + 写入/派发协议。

不变量（I29–I34）与失败语义见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 6 节。要点：

- **先写后发**：`OutboxWriter.enqueue` 必须与业务变更同事务落库，不发网络；
- **at-least-once**：去重键 `event_id`；同 `evaluation_run_id` 的
  `started → progress → completed|failed` 顺序由 B2 保证；
- **DLQ 不静默**：超过投递上限的消息进入 DLQ 并可查询/重放。

envelope 的权威结构在 `MAIN/contracts/schemas/event-envelope.schema.json`；本模块不复制其字段定义。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

#: 事件 producer 恒为 B2（契约 `event-envelope.schema.json` 的 `producer` 模式 B1–B4）。
B2_PRODUCER = "B2"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    event_type: str               # autograder.v1.*
    schema_version: int
    occurred_at: datetime         # UTC，毫秒精度
    correlation_id: str
    causation_id: str | None
    producer: str                 # 恒为 "B2"
    subject: str                  # NATS subject，与 event_type 同名
    payload: Mapping[str, object]  # 只有标识与去标识化值


@dataclass(frozen=True, slots=True)
class DispatchReport:
    selected: int
    published: int
    failed: int
    dead_lettered: int
    last_error: str | None        # 脱敏


class OutboxWriter(Protocol):
    """与业务变更同事务写入 outbox（不发网络）。"""

    def enqueue(self, session: object, event: DomainEvent) -> None: ...


class OutboxDispatcher(Protocol):
    """把未发布的行投递到 NATS；失败保留行、超上限进 DLQ。"""

    def dispatch(self, limit: int) -> DispatchReport: ...
