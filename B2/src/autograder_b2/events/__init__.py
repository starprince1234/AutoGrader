"""B2 事件（outbox 写入；派发与消费在 `WP-B2-C` 内实现）。"""

from __future__ import annotations

from autograder_b2.events.outbox import (
    EVALUATION_COMPLETED,
    EVALUATION_FAILED,
    EVALUATION_PROGRESS,
    EVALUATION_STARTED,
    PUBLISHED_SUBJECTS,
    SUBMISSION_ACCEPTED,
    EnqueuedEvent,
    build_event,
    enqueue,
    submission_accepted_payload,
)

__all__ = [
    "EVALUATION_COMPLETED",
    "EVALUATION_FAILED",
    "EVALUATION_PROGRESS",
    "EVALUATION_STARTED",
    "PUBLISHED_SUBJECTS",
    "SUBMISSION_ACCEPTED",
    "EnqueuedEvent",
    "build_event",
    "enqueue",
    "submission_accepted_payload",
]
