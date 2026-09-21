"""Event transport.

``EventPublisher`` is the port B3 publishes through. The outbox is drained by
``domain.outbox``; the NATS implementation lives here so that tests can substitute
an in-memory publisher without mocking business logic (allowed mock: NATS, per
``docs/testing/strategy.md``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from collections.abc import Mapping

from ..logging import get_logger, log_event


class EventPublisher(Protocol):
    async def publish(self, subject: str, envelope: Mapping[str, Any]) -> None: ...

    async def close(self) -> None: ...


@dataclass
class InMemoryEventPublisher:
    """Test double for NATS. Records what would have been published."""

    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    closed: bool = False

    async def publish(self, subject: str, envelope: Mapping[str, Any]) -> None:
        self.published.append((subject, json.loads(json.dumps(envelope))))

    async def close(self) -> None:
        self.closed = True

    def subjects(self) -> list[str]:
        return [subject for subject, _ in self.published]


class NatsEventPublisher:
    """JetStream publisher (B4 operates the NATS server, ``RQ-B-4-20``)."""

    def __init__(self, nats_url: str) -> None:
        self.nats_url = nats_url
        self._client: Any = None

    async def _connect(self) -> Any:
        if self._client is None:
            import nats  # imported lazily: NATS is optional for the sync API path

            self._client = await nats.connect(self.nats_url)
        return self._client

    async def publish(self, subject: str, envelope: Mapping[str, Any]) -> None:
        client = await self._connect()
        await client.publish(subject, json.dumps(envelope, separators=(",", ":")).encode("utf-8"))
        await client.flush()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


def log_publish_failure(subject: str, event_id: str, error: Exception) -> None:
    log_event(
        get_logger("infra.publisher"),
        "event.publish.failed",
        level=40,
        subject=subject,
        event_id=event_id,
        error_type=type(error).__name__,
    )
