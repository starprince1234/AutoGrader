"""Whitelist validation for the consumed event (privacy boundary).

``MAIN/docs/api/events.md`` §3.4 fixes both sides of the boundary:

* **allowed** - de-identified values only: identifiers, pass/fail, score,
  failure category, duration, digests, timestamps;
* **forbidden** - source code (or any digest of it), reference answers, hidden
  cases, expected outputs, student name/number/e-mail/``student_id``.

The allowed set below is the field-level reading of that list. Anything outside it
- including a field B3 simply does not know - is rejected with
:class:`~autograder_b3.errors.EventRejected`, logged, and left unacked so it
reaches the DLQ rather than being silently dropped.

There is one deliberate consequence worth naming: because ``student_id`` is
forbidden, B3 cannot compute "distinct students". The frozen ``QuestionStatistics``
DTO has no field for it either, so attempt counts are the only head-count
statistic B3 publishes (``CHG-B3-0003``).
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any
from collections.abc import Mapping
from uuid import UUID

from ..dto.internal import EvaluationCompletedEvent
from ..errors import EventRejected
from .envelope import (
    CONSUMER_EVENT_TYPE,
    ENVELOPE_KEYS,
    parse_rfc3339,
    parse_uuid,
)

#: The complete set of payload fields B3 will accept (events.md §3.4).
ALLOWED_PAYLOAD_FIELDS: frozenset[str] = frozenset(
    {
        "submission_id",
        "evaluation_run_id",
        "question_version_id",
        "course_id",
        "section_id",
        "is_pass",
        "score",
        "max_score",
        "score_bucket",
        "result_digest",
        "failed_category",
        "duration_ms",
        "occurred_at",
    }
)

#: Named explicitly so the rejection message can say *why*, and so the privacy
#: tests can assert that each of these is refused rather than merely unknown.
FORBIDDEN_PAYLOAD_FIELDS: frozenset[str] = frozenset(
    {
        "student_id",
        "student_name",
        "student_number",
        "email",
        "source_code",
        "source",
        "code",
        "files",
        "submission_content",
        "reference_answer",
        "reference_answers",
        "hidden_cases",
        "test_cases",
        "expected_output",
        "expected_stdout",
        "expected_stderr",
        "stdout",
        "stderr",
        "cases",
        "fixtures",
        "token",
        "authorization",
    }
)

MAX_STRING_FIELD = 200


def validate_envelope(envelope: Mapping[str, Any]) -> tuple[Any, ...]:
    """Structural validation of the envelope, before any field is interpreted."""
    if not isinstance(envelope, Mapping):
        raise EventRejected("event envelope must be an object")
    unknown = set(envelope) - set(ENVELOPE_KEYS)
    if unknown:
        raise EventRejected(
            "event envelope carries unknown field(s): " + ", ".join(sorted(unknown))
        )
    for required in ("event_id", "event_type", "schema_version", "occurred_at", "correlation_id", "producer", "payload"):
        if required not in envelope:
            raise EventRejected(f"event envelope is missing {required}", field=required)

    event_id = parse_uuid(envelope["event_id"], field="event_id")
    correlation_id = parse_uuid(envelope["correlation_id"], field="correlation_id")
    causation_id = parse_uuid(envelope.get("causation_id"), field="causation_id", required=False)
    occurred_at = parse_rfc3339(envelope["occurred_at"], field="occurred_at")

    event_type = envelope["event_type"]
    if event_type != CONSUMER_EVENT_TYPE:
        raise EventRejected(
            f"B3 only consumes {CONSUMER_EVENT_TYPE}, received {event_type!r}",
            field="event_type",
        )
    producer = envelope["producer"]
    if producer != "B2":
        raise EventRejected(
            f"{CONSUMER_EVENT_TYPE} must be produced by B2, received {producer!r}",
            field="producer",
        )
    schema_version = envelope["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise EventRejected("schema_version must be an integer >= 1", field="schema_version")
    payload = envelope["payload"]
    if not isinstance(payload, Mapping):
        raise EventRejected("payload must be an object", field="payload")
    return event_id, correlation_id, causation_id, occurred_at, schema_version, payload


def validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Field-whitelist validation; returns the accepted de-identified payload."""
    rejected = sorted(set(payload) & FORBIDDEN_PAYLOAD_FIELDS)
    if rejected:
        raise EventRejected(
            "payload carries forbidden field(s): " + ", ".join(rejected), field=rejected[0]
        )
    unknown = sorted(set(payload) - ALLOWED_PAYLOAD_FIELDS)
    if unknown:
        raise EventRejected(
            "payload carries field(s) outside the de-identified whitelist: "
            + ", ".join(unknown),
            field=unknown[0],
        )
    return dict(payload)


def parse_evaluation_completed(envelope: Mapping[str, Any]) -> EvaluationCompletedEvent:
    """Validate an ``evaluation.completed`` envelope into a typed, de-identified event."""
    (
        event_id,
        correlation_id,
        _causation_id,
        occurred_at,
        _schema_version,
        payload,
    ) = validate_envelope(envelope)
    accepted = validate_payload(payload)

    evaluation_run_id = parse_uuid(
        accepted.get("evaluation_run_id"), field="payload.evaluation_run_id"
    )
    question_version_id = parse_uuid(
        accepted.get("question_version_id"), field="payload.question_version_id"
    )
    submission_id = parse_uuid(
        accepted.get("submission_id"), field="payload.submission_id", required=False
    )
    course_id = parse_uuid(accepted.get("course_id"), field="payload.course_id", required=False)
    section_id = parse_uuid(
        accepted.get("section_id"), field="payload.section_id", required=False
    )

    is_pass = accepted.get("is_pass")
    if is_pass is not None and not isinstance(is_pass, bool):
        raise EventRejected("payload.is_pass must be a boolean", field="payload.is_pass")

    score = _optional_number(accepted.get("score"), "payload.score")
    max_score = _optional_number(accepted.get("max_score"), "payload.max_score")
    if max_score is not None and max_score <= 0:
        raise EventRejected("payload.max_score must be > 0", field="payload.max_score")

    duration_ms = accepted.get("duration_ms")
    if duration_ms is not None and (
        not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0
    ):
        raise EventRejected("payload.duration_ms must be a non-negative integer", field="payload.duration_ms")

    result_digest = accepted.get("result_digest")
    if result_digest is not None and (
        not isinstance(result_digest, str)
        or len(result_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in result_digest)
    ):
        raise EventRejected(
            "payload.result_digest must be a lowercase sha256 hex digest",
            field="payload.result_digest",
        )

    failed_category = _optional_label(accepted.get("failed_category"), "payload.failed_category", 64)
    score_bucket = _optional_label(accepted.get("score_bucket"), "payload.score_bucket", 32)

    payload_occurred_at = accepted.get("occurred_at")
    if payload_occurred_at is not None:
        parse_rfc3339(payload_occurred_at, field="payload.occurred_at")

    assert evaluation_run_id is not None and question_version_id is not None
    return EvaluationCompletedEvent(
        event_id=event_id,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        evaluation_run_id=evaluation_run_id,
        question_version_id=question_version_id,
        submission_id=submission_id,
        course_id=course_id,
        section_id=section_id,
        is_pass=is_pass,
        score=score,
        max_score=max_score,
        score_bucket=score_bucket,
        result_digest=result_digest,
        failed_category=failed_category,
        duration_ms=duration_ms,
    )


def event_payload_for_inbox(event: EvaluationCompletedEvent) -> dict[str, Any]:
    """Re-serialize the accepted de-identified payload for the inbox row.

    Replay reads these rows back, so the stored form must round-trip through the
    same whitelist (``test_inbox_payload_round_trips``).
    """
    payload: dict[str, Any] = {
        "evaluation_run_id": str(event.evaluation_run_id),
        "question_version_id": str(event.question_version_id),
        "occurred_at": _iso(event.occurred_at),
    }
    optional: dict[str, Any] = {
        "submission_id": _str_or_none(event.submission_id),
        "course_id": _str_or_none(event.course_id),
        "section_id": _str_or_none(event.section_id),
        "is_pass": event.is_pass,
        "score": event.score,
        "max_score": event.max_score,
        "score_bucket": event.score_bucket,
        "result_digest": event.result_digest,
        "failed_category": event.failed_category,
        "duration_ms": event.duration_ms,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


def event_from_inbox_row(row: Any) -> EvaluationCompletedEvent:
    """Rebuild a typed event from an ``b3.event_inbox`` row.

    The stored payload is re-validated through the same whitelist, so a row that
    somehow acquired a forbidden field cannot re-enter the aggregation on replay.
    """
    envelope = {
        "event_id": str(row.event_id),
        "event_type": row.event_type,
        "schema_version": row.schema_version,
        "occurred_at": _iso(row.occurred_at),
        "correlation_id": str(row.correlation_id),
        "causation_id": None if row.causation_id is None else str(row.causation_id),
        "producer": row.producer,
        "payload": dict(row.payload),
    }
    return parse_evaluation_completed(envelope)


def _str_or_none(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _iso(value: datetime) -> str:
    from ..logging import to_rfc3339_millis

    return to_rfc3339_millis(value.astimezone(UTC))


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventRejected(f"{field} must be a number", field=field)
    if value < 0:
        raise EventRejected(f"{field} must be >= 0", field=field)
    return float(value)


def _optional_label(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise EventRejected(f"{field} must be a string of 1..{limit} characters", field=field)
    return value
