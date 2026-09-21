"""NATS connection management and idempotent topology provisioning.

Provisioning is a command an operator runs (`python -m autograder_b4.ops.messaging
provision`), not something that happens on the request path: creating streams during a
request would make every request depend on the broker, which is precisely what
`RQ-B-4-20` forbids.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

import nats
from nats.aio.client import Client as NatsClient
from nats.js import JetStreamContext
from nats.js.errors import NotFoundError

from ..logging import get_logger, redact_text
from .jetstream import (
    CONSUMERS,
    STREAMS,
    ConsumerSpec,
    StreamSpec,
    consumer_config,
    stream_config,
)

logger = get_logger("messaging")

CONNECT_TIMEOUT_SECONDS = 5.0
PROVISION_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BrokerHealth:
    healthy: bool
    detail: str


async def connect(
    nats_url: str, *, name: str = "autograder-b4"
) -> tuple[NatsClient, JetStreamContext]:
    """Open a JetStream-enabled connection, failing fast when the broker is unreachable."""
    client = await nats.connect(
        servers=[nats_url],
        name=name,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        max_reconnect_attempts=3,
        allow_reconnect=True,
    )
    return client, client.jetstream()


async def broker_health(nats_url: str) -> BrokerHealth:
    """Probe the broker with a real JetStream round trip (used by operator commands)."""
    try:
        client, jetstream = await connect(nats_url, name="autograder-b4-health")
    except Exception as error:  # noqa: BLE001 - any connect failure is "unavailable"
        return BrokerHealth(False, f"nats is unavailable ({type(error).__name__})")
    try:
        await jetstream.account_info()
    except Exception as error:  # noqa: BLE001 - a socket without JetStream is unusable
        return BrokerHealth(False, f"nats JetStream is unavailable ({type(error).__name__})")
    finally:
        await client.close()
    return BrokerHealth(True, "nats JetStream reachable")


def nats_reachability(nats_url: str) -> BrokerHealth:
    """Synchronous liveness probe for the readiness path.

    The probe performs a real NATS handshake rather than a TCP connect. A publish-ordered
    port check is not evidence: a container runtime's port forwarder keeps accepting
    connections after the container stops, so `socket.create_connection` succeeds against a
    broker that is not there and readiness reports a healthy dependency that is gone.

    The server speaks first -- it sends `INFO {...}` immediately on connect -- so reading
    that line is a complete and cheap proof that a NATS server (not a proxy) is answering.
    """
    from urllib.parse import urlparse

    parsed = urlparse(nats_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 4222
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS) as connection:
            connection.settimeout(CONNECT_TIMEOUT_SECONDS)
            greeting = connection.recv(256)
    except OSError as error:
        return BrokerHealth(
            False, f"nats is unreachable at {host}:{port} ({type(error).__name__})"
        )
    if not greeting.startswith(b"INFO "):
        return BrokerHealth(
            False,
            f"nats at {host}:{port} did not answer with a server greeting "
            f"({greeting[:32]!r}); the port is open but no NATS server is behind it",
        )
    return BrokerHealth(True, f"nats is reachable at {host}:{port}")


async def ensure_stream(jetstream: JetStreamContext, spec: StreamSpec) -> str:
    """Create the stream, or update it when its declarative configuration changed.

    `add_stream` and `update_stream` fold every keyword argument into the `StreamConfig`,
    so only config fields may be passed; a stray `timeout=` becomes a constructor error.
    """
    config = stream_config(spec)
    try:
        info = await jetstream.stream_info(spec.name)
    except NotFoundError:
        await jetstream.add_stream(config=config)
        logger.info(
            "messaging.stream.created",
            extra={"event": "messaging.stream.created", "stream": spec.name},
        )
        return "created"
    current = info.config
    if (
        sorted(current.subjects or []) != sorted(config.subjects)
        or current.max_bytes != config.max_bytes
        or str(current.retention) != str(config.retention)
    ):
        config.name = spec.name
        await jetstream.update_stream(config=config)
        logger.info(
            "messaging.stream.updated",
            extra={"event": "messaging.stream.updated", "stream": spec.name},
        )
        return "updated"
    return "unchanged"


async def ensure_consumer(jetstream: JetStreamContext, spec: ConsumerSpec) -> str:
    """Create the durable consumer, or update it when its configuration changed."""
    config = consumer_config(spec)
    try:
        info = await jetstream.consumer_info(spec.stream, spec.name)
    except NotFoundError:
        await jetstream.add_consumer(
            stream=spec.stream, config=config, timeout=PROVISION_TIMEOUT_SECONDS
        )
        logger.info(
            "messaging.consumer.created",
            extra={
                "event": "messaging.consumer.created",
                "stream": spec.stream,
                "consumer": spec.name,
            },
        )
        return "created"
    if info.config.max_deliver != config.max_deliver or info.config.ack_wait != config.ack_wait:
        await jetstream.add_consumer(
            stream=spec.stream, config=config, timeout=PROVISION_TIMEOUT_SECONDS
        )
        logger.info(
            "messaging.consumer.updated",
            extra={
                "event": "messaging.consumer.updated",
                "stream": spec.stream,
                "consumer": spec.name,
            },
        )
        return "updated"
    return "unchanged"


async def provision_topology(nats_url: str) -> dict[str, str]:
    """Create or reconcile every stream and durable consumer B4 owns."""
    client, jetstream = await connect(nats_url, name="autograder-b4-provision")
    results: dict[str, str] = {}
    try:
        for spec in STREAMS:
            results[f"stream:{spec.name}"] = await ensure_stream(jetstream, spec)
        for consumer in CONSUMERS:
            results[f"consumer:{consumer.stream}/{consumer.name}"] = await ensure_consumer(
                jetstream, consumer
            )
    finally:
        await client.close()
    return results


@dataclass(frozen=True)
class ConsumerUsage:
    """One durable consumer's observed delivery position."""

    name: str
    stream: str
    pending: int
    ack_pending: int
    redelivered: int


@dataclass(frozen=True)
class StreamUsage:
    """What a stream B4 owns actually stores, as opposed to the quota it declares.

    `max_bytes` is a ceiling configured at provisioning time; `stored_bytes` is the number
    the resource ledger is about. Reporting the ceiling as if it were the occupancy is what
    `RQ-B-4-20` acceptance 5 ("occupancy is counted into the ledger") must not be satisfied
    with, so the two are separate fields.
    """

    name: str
    exists: bool
    max_bytes: int
    stored_bytes: int
    messages: int
    consumers: tuple[ConsumerUsage, ...]
    detail: str


async def collect_stream_usage(nats_url: str) -> tuple[StreamUsage, ...]:
    """Read `state.bytes` / `state.messages` and every consumer's `num_pending`.

    A stream that is missing is reported as missing rather than omitted: "the topology was
    never provisioned" and "the topology stores nothing" are different findings, and an
    operator checking the ledger needs to tell them apart.
    """
    client, jetstream = await connect(nats_url, name="autograder-b4-metrics")
    usages: list[StreamUsage] = []
    try:
        for spec in STREAMS:
            consumers: list[ConsumerUsage] = []
            try:
                info = await jetstream.stream_info(spec.name)
            except NotFoundError:
                usages.append(
                    StreamUsage(
                        name=spec.name,
                        exists=False,
                        max_bytes=spec.max_bytes,
                        stored_bytes=0,
                        messages=0,
                        consumers=(),
                        detail="stream is not provisioned; run `provision` first",
                    )
                )
                continue
            for consumer_spec in CONSUMERS:
                if consumer_spec.stream != spec.name:
                    continue
                try:
                    consumer = await jetstream.consumer_info(spec.name, consumer_spec.name)
                except NotFoundError:
                    consumers.append(
                        ConsumerUsage(
                            name=consumer_spec.name,
                            stream=spec.name,
                            pending=0,
                            ack_pending=0,
                            redelivered=0,
                        )
                    )
                    continue
                consumers.append(
                    ConsumerUsage(
                        name=consumer_spec.name,
                        stream=spec.name,
                        pending=int(consumer.num_pending),
                        ack_pending=int(consumer.num_ack_pending),
                        redelivered=int(consumer.num_redelivered),
                    )
                )
            usages.append(
                StreamUsage(
                    name=spec.name,
                    exists=True,
                    max_bytes=int(info.config.max_bytes),
                    stored_bytes=int(info.state.bytes),
                    messages=int(info.state.messages),
                    consumers=tuple(consumers),
                    detail="ok",
                )
            )
    finally:
        await client.close()
    return tuple(usages)


def safe_error(error: BaseException) -> str:
    """A short, redacted description safe to persist as `outbox_events.last_error`."""
    return redact_text(f"{type(error).__name__}: {error}")[:500]
