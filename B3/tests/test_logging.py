"""TC-LOG-01: structured, redacted JSON logs (``RQ-B-3-99``).

Redaction is key-based, so it can be tested exhaustively: any value under a
protected key becomes ``[redacted]`` no matter who logged it or how deeply it is
nested. The tests also assert the fields the requirement names explicitly:
``producer=B3``, ``request_id`` and ``question_version_id`` when it exists.
"""

from __future__ import annotations

import io
import json
import uuid
import logging

import pytest

from autograder_b3.logging import (
    REDACTED,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    is_protected_key,
    log_event,
    redact_value,
    to_rfc3339_millis,
)

from .conftest import COURSE_A, draft_payload
from .support import http_attestation


def emit(records: list[logging.LogRecord]) -> list[dict]:
    formatter = JsonFormatter()
    redactor = RedactionFilter()
    payloads = []
    for record in records:
        redactor.filter(record)
        payloads.append(json.loads(formatter.format(record)))
    return payloads


def make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="autograder_b3.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="question.version.published",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_every_line_is_json_with_producer_b3():
    payload = emit([make_record(event="question.version.published", request_id="r-1")])[0]
    assert payload["producer"] == "B3"
    assert payload["level"] == "INFO"
    assert payload["event"] == "question.version.published"
    assert payload["request_id"] == "r-1"
    assert payload["timestamp"].endswith("Z")
    assert payload["timestamp"][-5] == "."


@pytest.mark.parametrize(
    "key",
    [
        "reference_answer",
        "expected_stdout",
        "expected_stderr",
        "expected_exit_code",
        "payload",
        "content",
        "fixtures",
        "source_code",
        "submission_code",
        "authorization",
        "access_token",
        "password",
        "statement",
        "traceback",
    ],
)
def test_protected_values_are_redacted(key):
    payload = emit([make_record(**{key: "SECRET-VALUE"})])[0]
    rendered = json.dumps(payload)
    assert "SECRET-VALUE" not in rendered
    assert "[redacted]" in rendered
    assert is_protected_key(key)


def test_nested_protected_values_are_redacted():
    payload = emit(
        [make_record(context={"case": {"expected_stdout": "0\n", "name": "ok"}})]
    )[0]
    rendered = json.dumps(payload)
    assert "0\\n" not in rendered
    assert "ok" in rendered


def test_log_event_redacts_protected_fields():
    logger = logging.getLogger("autograder_b3.redaction-test")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        log_event(
            logger,
            "question.published",
            request_id="r-9",
            question_version_id="11111111-1111-4111-8111-111111111111",
            reference_answer="print(42)",
            expected_stdout="42\n",
        )
    finally:
        logger.removeHandler(handler)

    payload = json.loads(stream.getvalue().strip())
    assert payload["producer"] == "B3"
    assert payload["request_id"] == "r-9"
    assert payload["question_version_id"] == "11111111-1111-4111-8111-111111111111"
    text = stream.getvalue()
    assert "print(42)" not in text
    assert "42\\n" not in text


def test_exception_logging_records_only_the_type_and_message():
    try:
        raise ValueError("no protected content here")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="autograder_b3.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request.failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = emit([record])[0]
    assert payload["error_type"] == "ValueError"
    assert "Traceback" not in json.dumps(payload)


def test_oversized_and_exotic_values_are_clamped():
    assert redact_value("x" * 5000).endswith("...(truncated)")
    assert redact_value(list(range(100)))[-1].startswith("...")
    deep = redact_value({"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": "SECRET"}}}}}}}})
    assert "SECRET" not in json.dumps(deep)
    assert "[redacted]" in json.dumps(deep)


def test_configure_logging_is_idempotent():
    first = configure_logging("INFO", stream=io.StringIO())
    second = configure_logging("INFO", stream=io.StringIO())
    assert first is second
    assert len([h for h in second.handlers if isinstance(h, logging.StreamHandler)]) == 1


def test_rfc3339_formatting_is_millisecond_precision():
    from datetime import UTC, datetime

    moment = datetime(2026, 9, 15, 8, 30, 0, 123456, tzinfo=UTC)
    assert to_rfc3339_millis(moment) == "2026-09-15T08:30:00.123Z"


# --- end-to-end: a real request logs no protected content ----------------


def test_publish_request_logs_are_clean(client, auth_headers, capsys):
    stream = io.StringIO()
    logger = configure_logging("INFO", stream=stream, force=True)
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer="REFERENCE-SECRET-TOKEN"),
        headers={**auth_headers("teacher-a"), "X-Request-ID": "req-log-1"},
    )
    assert response.status_code == 201
    question_id = response.json()["question_id"]
    from sqlalchemy import select

    from autograder_b3.infra.models import QuestionVersion

    with client.app.state.b3_dependencies.database.app_session() as session:
        version_id = str(
            session.execute(
                select(QuestionVersion.question_version_id).where(
                    QuestionVersion.question_id == uuid.UUID(question_id),
                    QuestionVersion.status == "draft",
                )
            ).scalar_one()
        )
    http_attestation(client, uuid.UUID(version_id))
    published = client.post(
        f"/api/v1/question-versions/{version_id}/publish",
        headers={**auth_headers("teacher-a"), "X-Request-ID": "req-log-2"},
    )
    assert published.status_code == 200
    logger.handlers[0].flush()

    output = stream.getvalue()
    assert "REFERENCE-SECRET-TOKEN" not in output
    assert "Count the lines" not in output  # hidden case name never logged
    lines = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert lines and all(line["producer"] == "B3" for line in lines)
    assert any(line.get("request_id") == "req-log-2" for line in lines)
    assert any(line.get("question_version_id") == version_id for line in lines)


def test_serving_the_reference_answer_does_not_log_it(
    client, auth_headers, service_headers, capsys
):
    """CHG-0023 §8.7: the delivery path for protected content must not log it.

    The endpoint exists so B2 can execute a version's answer; that answer is protected
    content, so the log line has to carry what an operator needs to correlate a verification
    (version, language, digest) and nothing else. Redaction is a second net here, not the
    first: the test asserts the body is absent from the raw stream, so a future log line
    that passes it under a non-protected key still fails.
    """
    stream = io.StringIO()
    logger = configure_logging("INFO", stream=stream, force=True)
    secret = "REFERENCE-ANSWER-BODY-MUST-NOT-BE-LOGGED"
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=secret),
        headers={**auth_headers("teacher-a"), "X-Request-ID": "req-ref-1"},
    )
    assert created.status_code == 201
    question_id = created.json()["question_id"]
    from sqlalchemy import select

    from autograder_b3.infra.models import QuestionVersion

    with client.app.state.b3_dependencies.database.app_session() as session:
        version_id = str(
            session.execute(
                select(QuestionVersion.question_version_id).where(
                    QuestionVersion.question_id == uuid.UUID(question_id),
                    QuestionVersion.status == "draft",
                )
            ).scalar_one()
        )

    served = client.get(
        f"/api/v1/question-versions/{version_id}/reference-answer",
        headers={**service_headers, "X-Request-ID": "req-ref-2"},
    )
    assert served.status_code == 200
    assert served.json()["reference_answer"] == secret
    logger.handlers[0].flush()

    output = stream.getvalue()
    assert secret not in output
    lines = [json.loads(line) for line in output.splitlines() if line.strip()]
    # The event did happen, so the assertion above is not vacuous.
    served_lines = [line for line in lines if line.get("event") == "reference.answer.served"]
    assert served_lines, output
    assert served_lines[0]["request_id"] == "req-ref-2"
    assert served_lines[0]["question_version_id"] == version_id
    assert served_lines[0]["language"] == "python"
    assert served_lines[0]["adapter_kind"] == "stdin"
    # The digest is logged the way this module logs every digest: the key is present so an
    # operator can see a digest was involved, and the value is redacted (``content_digest``
    # matches the ``content`` fragment). Asserting the redaction rather than a 64-character
    # string keeps the test honest about what actually reaches the stream.
    assert served_lines[0]["content_digest"] == REDACTED


def test_failures_are_logged_not_swallowed(client, auth_headers):
    stream = io.StringIO()
    logger = configure_logging("INFO", stream=stream, force=True)
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(stem="<script>alert(1)</script>"),
        headers={**auth_headers("teacher-a"), "X-Request-ID": "req-log-3"},
    )
    assert response.status_code == 422
    logger.handlers[0].flush()
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert any(line["message"] == "request.completed" for line in lines)
