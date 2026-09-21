"""JetStream topology: stream, durable consumer, and the dead-letter stream.

Two independent properties are required of this layer, and they pull in opposite
directions:

* the synchronous API must keep working when NATS is down, so nothing on the request
  path may await a broker; and
* an event that has been produced by a committed transaction must eventually be
  delivered, so the broker interaction moves to the outbox relay instead.

Delivery is at-least-once, `max_deliver` bounds the retries, and every message that
exhausts them is retained in a dead-letter store that can be queried and replayed --
"never dropped silently" is a property of the topology, not a promise in a document.
"""

from __future__ import annotations

from dataclasses import dataclass

from nats.js import api

from .envelope import ROSTER_IMPORT_COMPLETED

#: The stream that carries B4's domain event. Names and subjects mirror the runbook and
#: `MAIN/contracts/asyncapi/events.yaml`; B4 invents no domain subject.
STREAM_ROSTER_IMPORT = "ROSTER_IMPORT"
SUBJECT_ROSTER_IMPORT_COMPLETED = ROSTER_IMPORT_COMPLETED

#: The platform's **shared** domain-event stream. B4 owns the messaging topology
#: (`RQ-B-4-20`: "stream/consumer 创建"), so the stream every module publishes onto is
#: B4's to provision; each module still owns its subjects, its consumers and its payloads.
#:
#: It cannot simply claim `autograder.v1.>`, because a subject may be bound to exactly one
#: stream and `ROSTER_IMPORT` already holds `autograder.v1.roster.import.completed`: the
#: declaration therefore enumerates the contract's *other* subjects. `test_messaging.py`
#: reads `MAIN/contracts/asyncapi/events.yaml` and asserts the union of B4's streams binds
#: every declared address exactly once, so a subject added to the contract fails the suite
#: instead of failing at publish time with `no response from stream` -- which is exactly how
#: this stream came to be missing.
STREAM_DOMAIN_EVENTS = "DOMAIN_EVENTS"
DOMAIN_EVENT_SUBJECTS = (
    "autograder.v1.submission.accepted",
    "autograder.v1.evaluation.started",
    "autograder.v1.evaluation.progress",
    "autograder.v1.evaluation.completed",
    "autograder.v1.evaluation.failed",
    "autograder.v1.question.version.published",
)

#: B4 operates the platform's messaging, so it also runs one durable consumer over its
#: own stream. That consumer is the control plane for delivery accounting, DLQ and
#: replay -- it is not a business subscription, and `module.yaml: events.subscribes`
#: stays empty. `tests/test_registry.py` asserts that separation.
#:
#: The shared domain stream intentionally has **no** B4 consumer: which events a module
#: consumes, with which durability and ack policy, is that module's delivery contract.
CONSUMER_ROSTER_IMPORT = "B4_ROSTER_CONSUMER"
ACK_WAIT_SECONDS = 30
MAX_DELIVER = 5

#: Dead-letter stream. It carries JetStream's own advisories rather than an invented
#: `...dlq` domain subject, so no second event vocabulary enters the contract.
#:
#: Two advisory types mean "this message will not be delivered again", and both belong in
#: the dead-letter store:
#:
#: * `CONSUMER.MAX_DELIVERIES` -- the broker gave up after `max_deliver` attempts (this is
#:   the one an exhausted `B4_ROSTER_CONSUMER` actually produces; verified against the
#:   running server, not assumed);
#: * `CONSUMER.MSG_TERMINATED` -- the consumer itself called `term()`.
DLQ_STREAM = "B4_DLQ"
DLQ_ADVISORY_PREFIXES = (
    "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES",
    "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED",
)
DLQ_ADVISORY_SUBJECTS = [f"{prefix}.>" for prefix in DLQ_ADVISORY_PREFIXES]
DLQ_CONSUMER = "B4_DLQ_CONSUMER"

#: JetStream persistent storage stays inside the resource ledger (<= 0.25 GB,
#: `MAIN/docs/architecture/resource-budget.md` §3). The ledger line covers **all** of the
#: module's JetStream storage, so it is a budget for the streams together rather than an
#: allowance per stream: declaring 256 MB + 32 MB here used to total 288 MB, i.e. already
#: over the line before a single message was written.
JETSTREAM_LEDGER_MAX_BYTES = 268_435_456  # 0.25 GB, the whole ledger line

#: The line is split across the three streams B4 owns. The shared domain stream takes the
#: majority (192 MB): it carries six subjects for every module, and a consumer that falls
#: behind must not start losing other modules' events. `ROSTER_IMPORT` carries one subject
#: whose durable consumer applies it promptly into `b4.inbox_events`, so a small buffer is
#: enough (32 MB). The dead-letter store keeps 32 MB -- its entries are JetStream advisories
#: of a few hundred bytes each, so thousands of dead letters fit, while a store too small to
#: inspect or replay would defeat its purpose. `ROSTER_IMPORT_MAX_BYTES` is **derived** from
#: the other two, so the three quotas cannot drift apart from the budget by hand-editing.
DOMAIN_EVENTS_MAX_BYTES = 201_326_592  # 192 MB, the shared bus
DLQ_MAX_BYTES = 33_554_432  # 32 MB, advisories
ROSTER_IMPORT_MAX_BYTES = (
    JETSTREAM_LEDGER_MAX_BYTES - DOMAIN_EVENTS_MAX_BYTES - DLQ_MAX_BYTES
)  # 32 MB


@dataclass(frozen=True)
class StreamSpec:
    """A declarative description of one stream, so provisioning is reviewable."""

    name: str
    subjects: tuple[str, ...]
    description: str
    max_bytes: int


@dataclass(frozen=True)
class ConsumerSpec:
    """A declarative description of one durable consumer, so provisioning is reviewable."""

    name: str
    stream: str
    filter_subjects: tuple[str, ...]
    description: str
    max_deliver: int = MAX_DELIVER
    ack_wait_seconds: int = ACK_WAIT_SECONDS

    @property
    def filter_subject(self) -> str:
        """The single subject, for callers that subscribe to exactly one."""
        if len(self.filter_subjects) != 1:
            raise ValueError(f"{self.name} has {len(self.filter_subjects)} filter subjects")
        return self.filter_subjects[0]


ROSTER_IMPORT_STREAM = StreamSpec(
    name=STREAM_ROSTER_IMPORT,
    subjects=(SUBJECT_ROSTER_IMPORT_COMPLETED,),
    description="B4 roster import completions (RQ-B-4-20).",
    max_bytes=ROSTER_IMPORT_MAX_BYTES,
)

DOMAIN_EVENTS_STREAM = StreamSpec(
    name=STREAM_DOMAIN_EVENTS,
    subjects=DOMAIN_EVENT_SUBJECTS,
    description=(
        "The platform's shared domain events (every contract subject B4 does not bind itself)."
    ),
    max_bytes=DOMAIN_EVENTS_MAX_BYTES,
)

DLQ_STREAM_SPEC = StreamSpec(
    name=DLQ_STREAM,
    subjects=tuple(DLQ_ADVISORY_SUBJECTS),
    description="JetStream consumer terminations (max_deliver exhausted) retained for replay.",
    max_bytes=DLQ_MAX_BYTES,
)

ROSTER_CONSUMER_SPEC = ConsumerSpec(
    name=CONSUMER_ROSTER_IMPORT,
    stream=STREAM_ROSTER_IMPORT,
    filter_subjects=(SUBJECT_ROSTER_IMPORT_COMPLETED,),
    description="Durable delivery accounting for roster import completions.",
)

DLQ_CONSUMER_SPEC = ConsumerSpec(
    name=DLQ_CONSUMER,
    stream=DLQ_STREAM,
    # Both advisory prefixes, scoped to B4's own stream and consumer: a single-subject
    # filter could only carry one of them, and the dead-letter path must not be blind to
    # the other.
    filter_subjects=tuple(
        f"{prefix}.{STREAM_ROSTER_IMPORT}.{CONSUMER_ROSTER_IMPORT}"
        for prefix in DLQ_ADVISORY_PREFIXES
    ),
    description="Records exhausted deliveries into the queryable dead-letter store.",
    max_deliver=1,
    ack_wait_seconds=10,
)

STREAMS = (ROSTER_IMPORT_STREAM, DOMAIN_EVENTS_STREAM, DLQ_STREAM_SPEC)
CONSUMERS = (ROSTER_CONSUMER_SPEC, DLQ_CONSUMER_SPEC)


def stream_config(spec: StreamSpec) -> api.StreamConfig:
    """File-backed, limit-retained stream that discards the oldest messages at quota."""
    return api.StreamConfig(
        name=spec.name,
        subjects=list(spec.subjects),
        description=spec.description,
        retention=api.RetentionPolicy.LIMITS,
        storage=api.StorageType.FILE,
        discard=api.DiscardPolicy.OLD,
        max_bytes=spec.max_bytes,
        num_replicas=1,
        allow_direct=True,
    )


def consumer_config(spec: ConsumerSpec) -> api.ConsumerConfig:
    """Explicit-ack durable consumer; `max_deliver` is what makes the DLQ reachable."""
    single = len(spec.filter_subjects) == 1
    return api.ConsumerConfig(
        durable_name=spec.name,
        name=spec.name,
        description=spec.description,
        ack_policy=api.AckPolicy.EXPLICIT,
        deliver_policy=api.DeliverPolicy.ALL,
        # NATS rejects `filter_subject` alongside `filter_subjects`, so exactly one form
        # is sent for each consumer.
        filter_subject=spec.filter_subjects[0] if single else None,
        filter_subjects=None if single else list(spec.filter_subjects),
        max_deliver=spec.max_deliver,
        ack_wait=spec.ack_wait_seconds,
        max_ack_pending=64,
        num_replicas=1,
    )
