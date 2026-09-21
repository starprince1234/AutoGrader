"""RQ-B-4-99: structured JSON logs, correlation identifiers and mandatory redaction."""

from __future__ import annotations

import json
import logging
import uuid

from autograder_b4.logging import (
    REDACTED,
    RedactingJsonFormatter,
    bind_context,
    parse_traceparent,
    redact_text,
    redact_value,
    request_identifiers,
    reset_context,
)


def _record(message: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="autograder_b4.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _render(
    record: logging.LogRecord,
    *,
    request_id=None,
    correlation_id=None,
    user_id=None,
) -> dict:
    tokens = bind_context(
        request_id=request_id, correlation_id=correlation_id, user_id=user_id
    )
    try:
        return json.loads(RedactingJsonFormatter().format(record))
    finally:
        reset_context(tokens)


def test_log_line_is_json_with_the_producer_and_correlation_fields() -> None:
    payload = _render(
        _record("auth.session.created", event="auth.session.created"),
        request_id="req-1",
        correlation_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
    )
    assert payload["producer"] == "B4"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-1"
    assert payload["correlation_id"]
    assert payload["user_id"]
    assert payload["event"] == "auth.session.created"
    assert payload["message"] == "auth.session.created"


def test_log_records_are_single_line_json() -> None:
    """A multi-line record would break line-oriented log shippers."""
    rendered = RedactingJsonFormatter().format(
        _record("multi\nline\nmessage", event="probe")
    )
    assert "\n" not in rendered
    assert json.loads(rendered)["message"] == "multi\nline\nmessage"


def test_password_and_token_fields_are_redacted() -> None:
    payload = _render(
        _record(
            "login",
            password="correct horse battery staple",
            access_token="eyJhbGciOiJFZERTQSJ9.abcdefgh.ijklmnop",
            refresh_token="opaque-refresh-value",
            authorization="Bearer abcdefghijklmnop",
        )
    )
    for key in ("password", "access_token", "refresh_token", "authorization"):
        assert payload[key] == REDACTED, key


def test_pii_fields_are_redacted() -> None:
    payload = _render(
        _record(
            "roster",
            email="student@example.edu",
            display_name="张三",
            student_number="20230001",
        )
    )
    assert payload["email"] == REDACTED
    assert payload["display_name"] == REDACTED
    assert payload["student_number"] == REDACTED


def test_connection_strings_are_redacted() -> None:
    dsn = "postgresql+psycopg://autograder_b4:change-me@localhost:55432/autograder"
    payload = _render(_record("probe", database_url=dsn))
    assert payload["database_url"] == REDACTED
    # A DSN embedded in free text must not survive either.
    assert "change-me" not in redact_text(f"connecting with {dsn} failed")


def test_jwt_and_bearer_in_free_text_are_redacted() -> None:
    jwt = "eyJhbGciOiJFZERTQSJ9.cGF5bG9hZA.c2lnbmF0dXJl"
    assert jwt not in redact_text(f"token was {jwt}")
    assert "abcdefghijkl" not in redact_text("Authorization: Bearer abcdefghijklmnopqrst")


def test_nested_structures_are_redacted_recursively() -> None:
    redacted = redact_value(
        {
            "outer": {"password": "hunter2", "user_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427"},
            "items": [{"refresh_token": "opaque"}],
        }
    )
    assert redacted["outer"]["password"] == REDACTED
    assert redacted["outer"]["user_id"] == "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
    assert redacted["items"][0]["refresh_token"] == REDACTED


def test_identifiers_and_uuids_are_not_redacted() -> None:
    payload = _render(
        _record(
            "roster.import.committed",
            roster_import_id="0b0f4d5e-1c2b-4a3d-9e8f-7a6b5c4d3e2f",
            course_id="0b0f4d5e-1c2b-4a3d-9e8f-7a6b5c4d3e20",
            accepted_rows=47,
        )
    )
    assert payload["roster_import_id"].startswith("0b0f4d5e")
    assert payload["accepted_rows"] == 47


def test_binary_payload_is_described_not_dumped() -> None:
    payload = _render(_record("upload", content=b"\x00\x01\x02secret"))
    assert payload["content"] == "<9 bytes>"


def test_traceparent_becomes_the_correlation_id() -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    parsed = parse_traceparent(f"00-{trace_id}-00f067aa0ba902b7-01")
    assert str(parsed) == "4bf92f35-77b3-4da6-a3ce-929d0e0e4736"


def test_invalid_traceparent_is_ignored_rather_than_trusted() -> None:
    assert parse_traceparent("garbage") is None
    assert parse_traceparent(None) is None
    assert parse_traceparent("00-" + "0" * 32 + "-00f067aa0ba902b7-01") is None
    assert parse_traceparent("ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01") is None


def test_request_identifiers_are_generated_when_absent() -> None:
    request_id, correlation_id = request_identifiers(None, None)
    assert uuid.UUID(request_id)
    assert isinstance(correlation_id, uuid.UUID)
    given_id, given_correlation = request_identifiers(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "caller-supplied"
    )
    assert given_id == "caller-supplied"
    assert str(given_correlation) == "4bf92f35-77b3-4da6-a3ce-929d0e0e4736"


def test_credentials_never_reach_a_log_line_through_a_handler(
    client, factory, caplog
) -> None:
    """The end-to-end version: sign in and grep the captured log for the secret."""
    import logging as logging_module

    factory.user(username="logprobe", password="unique-probe-secret-value")
    with caplog.at_level(logging_module.DEBUG):
        response = client.post(
            "/api/v1/auth/session",
            json={"username": "logprobe", "password": "unique-probe-secret-value"},
        )
    assert response.status_code == 200, response.text
    refresh_token = response.cookies.get("autograder_refresh")
    access_token = response.json()["access_token"]

    captured = "\n".join(
        RedactingJsonFormatter().format(record) if isinstance(record, logging_module.LogRecord)
        else str(record)
        for record in caplog.records
    ) + caplog.text
    assert "unique-probe-secret-value" not in captured
    assert access_token not in captured
    if refresh_token:
        assert refresh_token not in captured
