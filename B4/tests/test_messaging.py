"""RQ-B-4-20: JetStream topology, the outbox, DLQ and replay -- against a real broker.

Everything here talks to the NATS server started by MAIN's compose file. The properties
under test are the ones that only a real broker can demonstrate: a durable consumer that
stops after `max_deliver`, an advisory that survives into a dead-letter store, a replayed
message, and an outbox that keeps its rows while the broker is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import nats
import pytest
from sqlalchemy import select, text

from autograder_b4.db.tables import inbox_events, outbox_events
from autograder_b4.messaging.broker import (
    broker_health,
    collect_stream_usage,
    connect,
    nats_reachability,
    provision_topology,
    safe_error,
)
from autograder_b4.messaging.consumer import (
    consume_roster_events,
    list_dlq,
    record_dlq_advisories,
    replay_dlq,
)
from autograder_b4.messaging.envelope import roster_import_completed_envelope
from autograder_b4.messaging.jetstream import (
    CONSUMER_ROSTER_IMPORT,
    DLQ_ADVISORY_SUBJECTS,
    DLQ_CONSUMER_SPEC,
    DLQ_MAX_BYTES,
    DOMAIN_EVENT_SUBJECTS,
    DOMAIN_EVENTS_MAX_BYTES,
    JETSTREAM_LEDGER_MAX_BYTES,
    ROSTER_CONSUMER_SPEC,
    ROSTER_IMPORT_MAX_BYTES,
    STREAM_DOMAIN_EVENTS,
    STREAM_ROSTER_IMPORT,
    STREAMS,
    SUBJECT_ROSTER_IMPORT_COMPLETED,
    consumer_config,
    stream_config,
)
from autograder_b4.messaging.outbox import enqueue, pending_count, publish_pending


def run(coroutine):
    return asyncio.run(coroutine)


def _envelope(**overrides):
    values = {
        "roster_import_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "section_id": uuid.uuid4(),
        "accepted_rows": 3,
        "rejected_rows": 1,
        "correlation_id": uuid.uuid4(),
    }
    values.update(overrides)
    return roster_import_completed_envelope(**values)


@pytest.fixture(scope="session", autouse=True)
def topology(settings):
    """Provision the topology once, so every test runs against the real configuration."""
    result = run(provision_topology(settings.nats_url))
    assert result["stream:ROSTER_IMPORT"] in {"created", "updated", "unchanged"}
    return result


@pytest.fixture
def clean_broker(engine, settings):
    """Empty the durable consumers so a test starts from a known delivery position."""

    async def _reset():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-reset")
        try:
            for spec in (ROSTER_CONSUMER_SPEC, DLQ_CONSUMER_SPEC):
                await jetstream.delete_consumer(spec.stream, spec.name)
            await jetstream.purge_stream(STREAM_ROSTER_IMPORT)
            await jetstream.purge_stream(DLQ_CONSUMER_SPEC.stream)
        finally:
            await client.close()
        await provision_topology(settings.nats_url)

    run(_reset())
    yield
    run(_reset())


# --------------------------------------------------------------------------- topology


def test_provisioning_creates_the_declared_topology(settings) -> None:
    """Acceptance 1: subjects and consumer settings match what the runbook declares."""

    async def _inspect():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-inspect")
        try:
            stream = await jetstream.stream_info(STREAM_ROSTER_IMPORT)
            consumer = await jetstream.consumer_info(STREAM_ROSTER_IMPORT, CONSUMER_ROSTER_IMPORT)
        finally:
            await client.close()
        return stream, consumer

    stream, consumer = run(_inspect())
    assert list(stream.config.subjects) == [SUBJECT_ROSTER_IMPORT_COMPLETED]
    assert stream.config.max_bytes == ROSTER_IMPORT_MAX_BYTES, (
        "the quota must match the allocation inside the resource ledger"
    )
    assert str(stream.config.storage) == "file"
    assert str(stream.config.retention) == "limits"
    assert str(stream.config.discard) == "old"
    assert str(consumer.config.ack_policy) == "explicit"
    assert consumer.config.max_deliver == 5
    assert consumer.config.ack_wait == 30
    assert consumer.config.filter_subject == SUBJECT_ROSTER_IMPORT_COMPLETED


def test_the_dead_letter_stream_watches_both_advisory_types(settings) -> None:
    """JetStream reports an exhausted delivery on `MAX_DELIVERIES`, not `MSG_TERMINATED`.

    The filter list is asserted directly because getting it wrong is silent: the DLQ stream
    exists, accepts nothing, and every dead letter disappears without a trace.
    """

    async def _inspect():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-dlq")
        try:
            stream = await jetstream.stream_info(DLQ_CONSUMER_SPEC.stream)
            consumer = await jetstream.consumer_info(
                DLQ_CONSUMER_SPEC.stream, DLQ_CONSUMER_SPEC.name
            )
        finally:
            await client.close()
        return stream, consumer

    stream, consumer = run(_inspect())
    subjects = list(stream.config.subjects)
    assert any("MAX_DELIVERIES" in subject for subject in subjects)
    assert any("MSG_TERMINATED" in subject for subject in subjects)
    filters = list(consumer.config.filter_subjects or [])
    assert any("MAX_DELIVERIES" in item for item in filters)
    assert any("MSG_TERMINATED" in item for item in filters)
    assert consumer.config.max_deliver == 1


def test_stream_and_consumer_configs_are_derived_from_the_specs() -> None:
    stream = stream_config(
        __import__(
            "autograder_b4.messaging.jetstream", fromlist=["ROSTER_IMPORT_STREAM"]
        ).ROSTER_IMPORT_STREAM
    )
    consumer = consumer_config(ROSTER_CONSUMER_SPEC)
    assert stream.name == STREAM_ROSTER_IMPORT
    assert stream.max_bytes == ROSTER_IMPORT_MAX_BYTES
    assert consumer.max_deliver == 5
    assert consumer.filter_subject == SUBJECT_ROSTER_IMPORT_COMPLETED


def test_broker_health_reports_a_real_broker(settings) -> None:
    assert nats_reachability(settings.nats_url).healthy
    assert run(broker_health(settings.nats_url)).healthy


def test_broker_health_reports_an_unreachable_broker() -> None:
    assert not nats_reachability("nats://127.0.0.1:1").healthy
    assert not run(broker_health("nats://127.0.0.1:1")).healthy


def test_the_probe_does_not_trust_an_open_port() -> None:
    """Regression: a container runtime's port proxy keeps accepting after the container stops.

    A bare TCP connect therefore reports a broker that is not there -- which is exactly what
    the outage drill caught. The probe has to speak enough NATS to notice the silence.
    """
    import socket
    import threading
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_and_stay_silent() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:  # pragma: no cover - listener closed first
            return
        # Accept the connection and say nothing, like a proxy with no backend.
        time.sleep(0.2)
        connection.close()

    thread = threading.Thread(target=accept_and_stay_silent, daemon=True)
    thread.start()
    try:
        status = nats_reachability(f"nats://127.0.0.1:{port}")
    finally:
        listener.close()
        thread.join(timeout=2)

    assert not status.healthy
    assert "no NATS server" in status.detail


# ----------------------------------------------------------------------------- outbox


def test_enqueue_and_relay_publish_to_the_broker(engine, settings, clean_broker) -> None:
    """Acceptance 1 and 4: the event reaches the declared subject."""
    envelope = _envelope()
    with engine.begin() as connection:
        enqueue(connection, envelope)
    assert pending_count(engine) == 1

    async def _publish():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-relay")
        try:
            return await publish_pending(engine, jetstream)
        finally:
            await client.close()

    result = run(_publish())
    assert result.published == 1 and result.failed == 0
    assert pending_count(engine) == 0

    async def _read():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-read")
        try:
            raw = await jetstream.get_last_msg(
                STREAM_ROSTER_IMPORT, SUBJECT_ROSTER_IMPORT_COMPLETED
            )
        finally:
            await client.close()
        return raw

    stored = json.loads(run(_read()).data.decode())
    assert stored["event_id"] == str(envelope.event_id)
    assert stored["producer"] == "B4"
    assert stored["payload"]["accepted_rows"] == 3


def test_an_unreachable_broker_leaves_the_event_queued(engine) -> None:
    """Acceptance 4: a broker outage must not lose the event, nor raise on the write path."""
    envelope = _envelope()
    with engine.begin() as connection:
        enqueue(connection, envelope)
    assert pending_count(engine) == 1

    async def _publish():
        _client, jetstream = await connect("nats://127.0.0.1:1", name="autograder-b4-test-dead")
        return await publish_pending(engine, jetstream)

    with pytest.raises((OSError, asyncio.TimeoutError, nats.errors.NoServersError)):
        run(_publish())
    with engine.connect() as connection:
        row = connection.execute(
            select(outbox_events.c.published_at).where(
                outbox_events.c.event_id == envelope.event_id
            )
        ).fetchone()
    assert row.published_at is None
    assert pending_count(engine) == 1


def test_a_failed_publish_records_the_attempt_and_keeps_the_row(engine, settings, monkeypatch):
    """A broker that rejects the publish must not mark the event delivered."""
    envelope = _envelope()
    with engine.begin() as connection:
        enqueue(connection, envelope)

    class RefusingJetStream:
        async def publish(self, subject, payload, **kwargs):
            raise ConnectionError("broker refuses")

    result = run(publish_pending(engine, RefusingJetStream()))
    assert result.published == 0 and result.failed == 1
    with engine.connect() as connection:
        row = connection.execute(
            select(
                outbox_events.c.attempts, outbox_events.c.last_error, outbox_events.c.published_at
            )
        ).fetchone()
    assert row.attempts == 1
    assert "broker refuses" in row.last_error
    assert row.published_at is None

    # And it still goes out once the broker is reachable again.
    async def _publish():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-retry")
        try:
            return await publish_pending(engine, jetstream)
        finally:
            await client.close()

    assert run(_publish()).published == 1


def test_the_relay_never_publishes_a_row_twice(engine, settings, clean_broker) -> None:
    """A published event is not republished by a later relay pass.

    The other direction -- a rolled-back business change leaves no queued event at all --
    is asserted in `test_roster_import.py`, where the transaction under test lives.
    """
    envelope = _envelope()
    with engine.begin() as connection:
        enqueue(connection, envelope)

    async def _publish():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-once")
        try:
            first = await publish_pending(engine, jetstream)
            second = await publish_pending(engine, jetstream)
            return first, second
        finally:
            await client.close()

    first, second = run(_publish())
    assert first.published == 1
    assert second.published == 0
    with engine.connect() as connection:
        attempts = connection.execute(select(outbox_events.c.attempts)).scalar_one()
    assert attempts == 1


# ------------------------------------------------------------------ at-least-once, DLQ


def test_a_duplicate_delivery_produces_one_business_effect(engine, settings, clean_broker) -> None:
    """Acceptance 2: the inbox unique key, not the handler, is what de-duplicates."""
    envelope = _envelope()
    raw = json.dumps(envelope.as_document()).encode()
    effects: list[str] = []

    def handler(connection, delivered):
        effects.append(str(delivered["event_id"]))

    async def _publish_twice():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-dup")
        try:
            await jetstream.publish(SUBJECT_ROSTER_IMPORT_COMPLETED, raw)
            await jetstream.publish(SUBJECT_ROSTER_IMPORT_COMPLETED, raw)
        finally:
            await client.close()

    run(_publish_twice())

    async def _consume():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-consume")
        try:
            return await consume_roster_events(engine, jetstream, handler=handler, timeout=3.0)
        finally:
            await client.close()

    outcome = run(_consume())
    assert outcome.processed == 1
    assert outcome.duplicates == 1
    assert effects == [str(envelope.event_id)]
    with engine.connect() as connection:
        rows = connection.execute(select(inbox_events.c.event_id)).fetchall()
    assert len(rows) == 1


def test_consuming_nothing_is_not_an_error(engine, settings, clean_broker) -> None:
    async def _consume():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-empty")
        try:
            return await consume_roster_events(engine, jetstream, timeout=1.0)
        finally:
            await client.close()

    outcome = run(_consume())
    assert outcome.empty
    assert outcome.processed == 0


def test_an_exhausted_delivery_lands_in_the_dead_letter_store(
    engine, settings, clean_broker
) -> None:
    """Acceptance 3: `max_deliver` exhausted -> a queryable dead letter, and a replay."""
    envelope = _envelope()
    raw = json.dumps(envelope.as_document()).encode()

    async def _exhaust():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-exhaust")
        try:
            await jetstream.publish(SUBJECT_ROSTER_IMPORT_COMPLETED, raw)
            subscription = await jetstream.pull_subscribe(
                subject=SUBJECT_ROSTER_IMPORT_COMPLETED,
                stream=STREAM_ROSTER_IMPORT,
                durable=CONSUMER_ROSTER_IMPORT,
            )
            delivered = 0
            for _ in range(8):
                try:
                    messages = await subscription.fetch(1, timeout=2)
                except Exception:  # noqa: BLE001 - no more deliveries available
                    break
                for message in messages:
                    delivered = max(delivered, message.metadata.num_delivered)
                    await message.nak()
            await subscription.unsubscribe()
            return delivered
        finally:
            await client.close()

    assert run(_exhaust()) == 5, "the consumer must stop at max_deliver"

    async def _record_and_replay():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-dlq")
        try:
            recorded = 0
            for _ in range(5):
                recorded += await record_dlq_advisories(engine, jetstream, timeout=2.0)
                if recorded:
                    break
            if not recorded:
                return 0, None
            records = list_dlq(engine)
            subject = await replay_dlq(engine, jetstream, records[0].dlq_id)
            return recorded, subject
        finally:
            await client.close()

    recorded, subject = run(_record_and_replay())
    assert recorded >= 1, "the exhaustion advisory never reached the dead-letter store"
    assert subject == SUBJECT_ROSTER_IMPORT_COMPLETED

    records = list_dlq(engine)
    assert records == [], "the replayed dead letter was not marked as replayed"
    everything = list_dlq(engine, include_replayed=True)
    assert everything[0].replayed_at is not None
    assert everything[0].original_sequence is not None
    assert "max_deliver" in everything[0].reason


def test_a_stored_dead_letter_replays_from_its_own_payload(engine, settings, clean_broker) -> None:
    """A dead letter keeps its envelope, so replay survives stream retention."""
    from autograder_b4.messaging.consumer import record_exhausted_delivery

    envelope = _envelope()
    dlq_id = run(
        record_exhausted_delivery(
            engine,
            envelope=envelope.as_document(),
            reason="poisoned message",
            delivery_count=5,
            sequence=None,
        )
    )
    records = list_dlq(engine)
    assert [record.dlq_id for record in records] == [dlq_id]
    assert records[0].reason == "poisoned message"

    async def _replay():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-replay2")
        try:
            return await replay_dlq(engine, jetstream, dlq_id)
        finally:
            await client.close()

    assert run(_replay()) == SUBJECT_ROSTER_IMPORT_COMPLETED
    assert list_dlq(engine) == []


def test_replaying_an_unknown_dead_letter_fails_loudly(engine, settings) -> None:
    async def _replay():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-missing")
        try:
            return await replay_dlq(engine, jetstream, uuid.uuid4())
        finally:
            await client.close()

    with pytest.raises(KeyError):
        run(_replay())


def test_replaying_a_purged_message_fails_loudly(engine, settings) -> None:
    """A dead letter whose original is gone must say so, not pretend to have retried."""
    dlq_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO b4.dlq_messages (dlq_id, original_stream, original_subject, "
                "original_sequence, reason, delivery_count) VALUES "
                "(:id, :stream, :subject, :seq, 'purged', 5)"
            ),
            {
                "id": dlq_id,
                "stream": STREAM_ROSTER_IMPORT,
                "subject": SUBJECT_ROSTER_IMPORT_COMPLETED,
                "seq": 999_999_999,
            },
        )

    async def _replay():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-purged")
        try:
            return await replay_dlq(engine, jetstream, dlq_id)
        finally:
            await client.close()

    with pytest.raises(ValueError, match="no longer retained"):
        run(_replay())


def test_a_malformed_message_is_neither_acked_nor_lost(engine, settings, clean_broker) -> None:
    """Not acknowledging is the only honest response to a message we cannot apply."""

    async def _send_and_consume():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-bad")
        try:
            await jetstream.publish(SUBJECT_ROSTER_IMPORT_COMPLETED, b"not json at all")
            return await consume_roster_events(engine, jetstream, timeout=3.0)
        finally:
            await client.close()

    outcome = run(_send_and_consume())
    assert outcome.failed == 1
    assert outcome.processed == 0

    # The message is still outstanding, so it will be redelivered and eventually dead-lettered.
    async def _pending():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-pending")
        try:
            info = await jetstream.consumer_info(STREAM_ROSTER_IMPORT, CONSUMER_ROSTER_IMPORT)
            return info.num_ack_pending, info.num_redelivered
        finally:
            await client.close()

    pending, redelivered = run(_pending())
    assert pending >= 1 or redelivered >= 1


# ------------------------------------------------------------------------- redaction


def test_persisted_broker_errors_are_redacted(engine) -> None:
    """An error string from a driver must not carry credentials into the outbox."""
    leaky = ConnectionError(
        "connect to postgresql+psycopg://autograder_b4:change-me@localhost:55432/autograder failed"
    )
    assert "change-me" not in safe_error(leaky)
    assert "[REDACTED]" in safe_error(leaky)


def test_jetstream_storage_stays_inside_the_resource_ledger() -> None:
    """Acceptance 5: the declared quotas are a budget, not an allowance per stream.

    The ledger line covers all of the module's JetStream storage, so the bound is on the
    total. The previous form of this assertion allowed `2 x 256 MB` and checked that 256 MB
    appeared somewhere, which accepted the 288 MB it was supposed to forbid **and** would
    have passed with a stream at zero bytes -- a disabled stream is not a configured quota.
    """
    assert all(spec.max_bytes > 0 for spec in STREAMS), list(STREAMS)
    total = sum(spec.max_bytes for spec in STREAMS)
    assert total <= JETSTREAM_LEDGER_MAX_BYTES, (
        f"declared JetStream quota is {total} bytes; the ledger line is "
        f"{JETSTREAM_LEDGER_MAX_BYTES}"
    )
    assert total == ROSTER_IMPORT_MAX_BYTES + DOMAIN_EVENTS_MAX_BYTES + DLQ_MAX_BYTES, (
        "the shared domain stream has to share the same ledger line as B4's own streams; "
        "a quota that is not part of this sum is a quota nobody counted"
    )


def test_the_shared_domain_stream_binds_every_contract_subject_exactly_once() -> None:
    """No subject may be left unbound, and none may be bound twice.

    A subject bound to two streams is rejected by the broker, and an unbound subject fails
    at publish time with `no response from stream` -- the failure that made every B2 event
    undeliverable until `DOMAIN_EVENTS` existed. The contract, not this module, is the list
    of subjects, so it is read here rather than trusted to a copy.
    """
    import yaml

    from tests.conftest import REPOSITORY_ROOT

    document = yaml.safe_load(
        (REPOSITORY_ROOT / "MAIN" / "contracts" / "asyncapi" / "events.yaml").read_text(
            encoding="utf-8"
        )
    )
    declared = {
        channel["address"]
        for channel in document["channels"].values()
        if "address" in channel
    }
    bound = [subject for spec in STREAMS for subject in spec.subjects]
    assert len(bound) == len(set(bound)), f"a subject is bound twice: {sorted(bound)}"
    # The dead-letter stream binds JetStream's own advisory namespace, which is not part of
    # the contract; everything else B4 binds must be a contract address, and vice versa.
    domain = {subject for subject in bound if subject.startswith("autograder.v1.")}
    assert {subject for subject in bound if not subject.startswith("autograder.v1.")} == set(
        DLQ_ADVISORY_SUBJECTS
    ), "only the JetStream advisory namespace may live outside `autograder.v1.`"
    assert domain == declared, (
        f"unbound contract subjects: {sorted(declared - domain)}; "
        f"subjects not in the contract: {sorted(domain - declared)}"
    )
    # B4's own event stays on B4's own stream, so the roster durable consumer keeps its
    # delivery accounting and the DLQ advisory subjects keep naming that stream.
    assert SUBJECT_ROSTER_IMPORT_COMPLETED not in DOMAIN_EVENT_SUBJECTS
    assert STREAM_DOMAIN_EVENTS in {spec.name for spec in STREAMS}


def test_provisioning_creates_the_shared_domain_stream(settings) -> None:
    """The stream every module publishes onto must exist after `provision`, with its quota."""

    async def _inspect():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-domain")
        try:
            return await jetstream.stream_info(STREAM_DOMAIN_EVENTS)
        finally:
            await client.close()

    info = run(_inspect())
    assert sorted(info.config.subjects) == sorted(DOMAIN_EVENT_SUBJECTS)
    assert info.config.max_bytes == DOMAIN_EVENTS_MAX_BYTES
    assert str(info.config.storage) == "file"
    assert str(info.config.discard) == "old"


def test_a_publish_to_a_contract_subject_lands_in_the_domain_stream(settings) -> None:
    """The end-to-end point of the stream: a module's event has somewhere to go.

    Published to an *unused* contract subject and consumed by nothing, so this proves the
    topology without fabricating a domain event another module's consumer would act on.
    """
    subject = "autograder.v1.evaluation.failed"

    async def _publish_and_locate():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-publish")
        try:
            await jetstream.publish(subject, b'{"probe": true}')
            stream = await jetstream.find_stream_name_by_subject(subject)
            message = await jetstream.get_last_msg(stream, subject)
        finally:
            await client.close()
        return stream, message

    stream, message = run(_publish_and_locate())
    assert stream == STREAM_DOMAIN_EVENTS
    assert bytes(message.data) == b'{"probe": true}'


def test_jetstream_storage_is_measured_not_assumed(engine, settings, clean_broker) -> None:
    """Acceptance 5: the ledger is checked against stored bytes, not against the quota.

    This is the measurement path `ops.messaging metrics` prints: a real event is relayed and
    the broker's own `state` is read back. A declared `max_bytes` is a ceiling, so the
    assertion that fails when a dependency is misconfigured is the one on measured storage.
    """
    with engine.begin() as connection:
        enqueue(connection, _envelope())

    async def _publish_and_measure():
        client, jetstream = await connect(settings.nats_url, name="autograder-b4-test-metrics")
        try:
            await publish_pending(engine, jetstream)
        finally:
            await client.close()
        return await collect_stream_usage(settings.nats_url)

    usages = {usage.name: usage for usage in run(_publish_and_measure())}
    assert set(usages) == {spec.name for spec in STREAMS}, usages
    roster = usages[STREAM_ROSTER_IMPORT]
    assert roster.exists and roster.detail == "ok"
    assert roster.max_bytes == ROSTER_IMPORT_MAX_BYTES
    assert roster.messages >= 1
    assert roster.stored_bytes > 0, "a relayed event must occupy storage"
    assert roster.stored_bytes <= roster.max_bytes
    assert sum(usage.stored_bytes for usage in usages.values()) <= JETSTREAM_LEDGER_MAX_BYTES

    consumers = [consumer.name for consumer in roster.consumers]
    assert consumers == [CONSUMER_ROSTER_IMPORT]
    assert roster.consumers[0].pending >= 1, "the durable consumer has an unapplied delivery"


def test_the_consumer_never_subscribes_to_an_undeclared_subject() -> None:
    """B4's durable consumer is operational; the contract's subscription list stays empty."""
    import yaml

    from tests.conftest import REPOSITORY_ROOT

    manifest = yaml.safe_load((REPOSITORY_ROOT / "B4" / "module.yaml").read_text(encoding="utf-8"))
    assert manifest["events"]["subscribes"] == []
    assert ROSTER_CONSUMER_SPEC.filter_subjects == (SUBJECT_ROSTER_IMPORT_COMPLETED,)


def test_dlq_records_expose_what_an_operator_needs(engine, settings, clean_broker) -> None:
    from autograder_b4.messaging.consumer import record_exhausted_delivery

    envelope = _envelope()
    run(
        record_exhausted_delivery(
            engine,
            envelope=envelope.as_document(),
            reason="probe",
            delivery_count=5,
            sequence=42,
        )
    )
    record = list_dlq(engine)[0]
    assert record.original_stream == STREAM_ROSTER_IMPORT
    assert record.original_sequence == 42
    assert record.event_id == envelope.event_id
    assert record.delivery_count == 5
    assert record.replayed_at is None


def test_nats_connection_names_are_distinguishable(settings) -> None:
    """Each worker connects under its own name, so `nats server report connections` is useful."""
    import inspect

    from autograder_b4.ops import messaging as ops

    source = inspect.getsource(ops)
    for name in ("relay", "consumer", "replay"):
        assert f"autograder-b4-{name}" in source


def test_envelope_timestamps_are_rfc3339_utc() -> None:
    envelope = _envelope()
    document = envelope.as_document()
    assert document["occurred_at"].endswith("Z")
    parsed = datetime.fromisoformat(document["occurred_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC)
    assert len(document["occurred_at"].split(".")[1]) == 4  # milliseconds + 'Z'


def test_nats_module_is_importable_under_the_declared_name(settings) -> None:
    async def _client():
        client = await nats.connect(settings.nats_url)
        await client.close()

    run(_client())
