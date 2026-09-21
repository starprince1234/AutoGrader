"""outbox 写入端：事件与业务变更**同事务**落库，绝不在业务事务里发网络。

派发（NATS 投递、重试、DLQ）属 `WP-B2-C` 的另一小类；本模块只负责"该发的事件一定先被记下来"。
这也是 `docs/architecture/interfaces.md` I29 的落地形态：先写后发，业务提交成功而消息中间件不可用
时只影响投递时延，不影响已接受的事实。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert

from autograder_b2.db.schema import outbox_events
from autograder_b2.domain.clock import utc_now
from autograder_b2.domain.events import B2_PRODUCER, DomainEvent

SUBMISSION_ACCEPTED = "autograder.v1.submission.accepted"
EVALUATION_STARTED = "autograder.v1.evaluation.started"
EVALUATION_PROGRESS = "autograder.v1.evaluation.progress"
EVALUATION_COMPLETED = "autograder.v1.evaluation.completed"
EVALUATION_FAILED = "autograder.v1.evaluation.failed"

PUBLISHED_SUBJECTS = (
    SUBMISSION_ACCEPTED,
    EVALUATION_STARTED,
    EVALUATION_PROGRESS,
    EVALUATION_COMPLETED,
    EVALUATION_FAILED,
)


@dataclass(frozen=True, slots=True)
class EnqueuedEvent:
    event_id: str
    subject: str
    deduplicated: bool


def build_event(
    *,
    subject: str,
    payload: Mapping[str, object],
    correlation_id: str,
    causation_id: str | None = None,
    occurred_at: datetime | None = None,
    event_id: str | None = None,
) -> DomainEvent:
    """构造领域事件（producer 恒为 B2；subject 与 event_type 同名）。"""
    if subject not in PUBLISHED_SUBJECTS:
        raise ValueError(f"unpublished subject: {subject}")
    return DomainEvent(
        event_id=event_id or str(uuid.uuid4()),
        event_type=subject,
        schema_version=1,
        occurred_at=occurred_at or utc_now(),
        correlation_id=correlation_id,
        causation_id=causation_id,
        producer=B2_PRODUCER,
        subject=subject,
        payload=dict(payload),
    )


def enqueue(
    connection: Connection,
    event: DomainEvent,
    *,
    deduplication_key: str | None = None,
    enqueued_at: datetime | None = None,
) -> EnqueuedEvent:
    """在**调用方的事务**内写入 outbox；同 (subject, 去重键) 只写一条。

    返回 `deduplicated=True` 表示该事件此前已入队（幂等重放），调用方据此无需重复发布。
    """
    timestamp = enqueued_at or utc_now()
    statement = (
        pg_insert(outbox_events)
        .values(
            event_id=uuid.UUID(event.event_id),
            subject=event.subject,
            event_type=event.event_type,
            schema_version=event.schema_version,
            producer=event.producer,
            occurred_at=event.occurred_at,
            correlation_id=uuid.UUID(event.correlation_id),
            causation_id=uuid.UUID(event.causation_id) if event.causation_id else None,
            deduplication_key=deduplication_key,
            payload=event.payload,
            enqueued_at=timestamp,
            published_at=None,
            attempts=0,
            last_error=None,
            dead_lettered_at=None,
        )
        .on_conflict_do_nothing()
        .returning(outbox_events.c.event_id)
    )
    inserted = connection.execute(statement).fetchone()
    if inserted is not None:
        return EnqueuedEvent(event_id=str(inserted[0]), subject=event.subject, deduplicated=False)
    if deduplication_key is None:  # pragma: no cover - 无去重键时冲突只可能来自 event_id 重复
        raise RuntimeError("outbox insert conflicted without a deduplication key")
    existing = connection.execute(
        sa.select(outbox_events.c.event_id).where(
            sa.and_(
                outbox_events.c.subject == event.subject,
                outbox_events.c.deduplication_key == deduplication_key,
            )
        )
    ).scalar_one()
    return EnqueuedEvent(event_id=str(existing), subject=event.subject, deduplicated=True)


def submission_accepted_payload(
    *,
    submission_id: str,
    student_id: str,
    course_id: str,
    section_id: str,
    question_version_id: str,
    intake: str,
) -> dict[str, object]:
    """`submission.accepted` 的 payload：**只有标识**，任何情况下都不含源码（契约硬约束）。"""
    return {
        "submission_id": submission_id,
        "student_id": student_id,
        "course_id": course_id,
        "section_id": section_id,
        "question_version_id": question_version_id,
        "intake": intake,
    }
