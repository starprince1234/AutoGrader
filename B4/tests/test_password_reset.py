"""Password reset by e-mailed verification code.

Integration-level, like the registration suite: the mail transport is the only seam replaced.
The code digest, attempt accounting, single use, credential replacement and refresh-session
revocation all run against the real database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from autograder_b4.db import tables
from autograder_b4.security import mailer

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a-new-correct-horse-battery"


class Mailbox:
    """Captures what the endpoint asked to send, and the code it asked to send."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def __call__(self, settings, *, to: str, code: str, ttl_seconds: int) -> None:
        self.sent.append({"to": to, "code": code, "ttl_seconds": ttl_seconds})

    @property
    def code(self) -> str:
        assert self.sent, "no password reset mail was requested"
        return str(self.sent[-1]["code"])


@pytest.fixture
def mailbox(monkeypatch) -> Mailbox:
    box = Mailbox()
    monkeypatch.setattr(mailer, "send_password_reset_code", box)
    return box


def _known_user(factory, **overrides):
    payload = {
        "username": "alice",
        "password": PASSWORD,
        "email": "alice@example.edu.cn",
    }
    payload.update(overrides)
    return factory.user(**payload)


def _request_code(client, email: str = "alice@example.edu.cn"):
    return client.post("/api/v1/auth/password-reset/code", json={"email": email})


def _complete(client, email: str, code: str, password: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/auth/password-reset",
        json={"email": email, "code": code, "new_password": password},
    )


def test_code_is_only_mailed_for_an_active_registered_address(client, factory, mailbox) -> None:
    _known_user(factory)

    accepted = _request_code(client, " Alice@Example.edu.cn ")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["expires_in_seconds"] > 0
    assert accepted.json()["resend_after_seconds"] > 0
    assert mailbox.sent[-1]["to"] == "alice@example.edu.cn"
    assert len(mailbox.code) == 6 and mailbox.code.isdigit()

    before = len(mailbox.sent)
    unknown = _request_code(client, "nobody@example.edu.cn")
    assert unknown.status_code == 202
    assert unknown.json() == accepted.json()
    assert len(mailbox.sent) == before

    factory.user(username="inactive", email="inactive@example.edu.cn", active=False)
    inactive = _request_code(client, "inactive@example.edu.cn")
    assert inactive.status_code == 202
    assert inactive.json() == accepted.json()
    assert len(mailbox.sent) == before


def test_resend_window_returns_the_same_shape_without_a_second_mail(
    client, factory, mailbox
) -> None:
    _known_user(factory)

    assert _request_code(client).status_code == 202
    again = _request_code(client)
    assert again.status_code == 202
    assert again.json()["resend_after_seconds"] > 0
    assert len(mailbox.sent) == 1


def test_complete_reset_changes_the_password_and_revokes_sessions(
    client, factory, mailbox
) -> None:
    _known_user(factory)
    signed_in = client.post(
        "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text

    assert _request_code(client).status_code == 202
    reset = _complete(client, "alice@example.edu.cn", mailbox.code)
    assert reset.status_code == 204, reset.text

    # The refresh credential issued before the reset is revoked, not merely unsupported.
    assert client.post("/api/v1/auth/session/refresh").status_code == 401

    assert (
        client.post(
            "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/session",
            json={"username": "alice", "password": NEW_PASSWORD},
        ).status_code
        == 200
    )


def test_wrong_code_is_generic_and_counts_an_attempt(client, factory, mailbox) -> None:
    _known_user(factory)
    assert _request_code(client).status_code == 202

    wrong = _complete(client, "alice@example.edu.cn", "000000")
    unknown = _complete(client, "nobody@example.edu.cn", "000000")
    assert wrong.status_code == 422
    assert unknown.status_code == 422
    # The caller cannot distinguish "wrong code on an existing account" from
    # "no account at all", and the attempt is still recorded for the real row.
    assert wrong.json()["detail"] == unknown.json()["detail"]

    with factory.engine.connect() as connection:
        row = connection.execute(select(tables.password_reset_codes)).fetchone()
    assert row is not None
    assert row.attempts == 1


def test_attempts_are_capped_without_leaking_the_reason(client, factory, mailbox) -> None:
    _known_user(factory)
    assert _request_code(client).status_code == 202

    for _ in range(5):
        assert _complete(client, "alice@example.edu.cn", "000000").status_code == 422

    exhausted = _complete(client, "alice@example.edu.cn", mailbox.code)
    assert exhausted.status_code == 422

    # The correct code no longer works: the account must request a new one.
    with factory.engine.connect() as connection:
        row = connection.execute(select(tables.password_reset_codes)).fetchone()
    assert row is not None and row.consumed_at is None
    assert row.attempts >= 5


def test_mail_failure_leaves_no_usable_code(client, factory, mailbox, monkeypatch) -> None:
    _known_user(factory)

    def unavailable(settings, *, to: str, code: str, ttl_seconds: int) -> None:
        raise mailer.MailDeliveryError("the password reset mail channel is unavailable")

    monkeypatch.setattr(mailer, "send_password_reset_code", unavailable)
    response = _request_code(client)
    assert response.status_code == 503
    with factory.engine.connect() as connection:
        assert connection.execute(select(tables.password_reset_codes)).fetchone() is None


def test_complete_without_an_open_code_is_rejected(client, factory) -> None:
    _known_user(factory)

    missing = _complete(client, "alice@example.edu.cn", "123456")
    unknown = _complete(client, "nobody@example.edu.cn", "123456")
    assert missing.status_code == 422
    assert unknown.status_code == 422
    assert missing.json()["detail"] == unknown.json()["detail"]


def test_short_new_password_is_rejected_before_the_code_is_consumed(
    client, factory, mailbox
) -> None:
    _known_user(factory)
    assert _request_code(client).status_code == 202

    rejected = _complete(client, "alice@example.edu.cn", mailbox.code, password="short")
    assert rejected.status_code == 422

    accepted = _complete(client, "alice@example.edu.cn", mailbox.code)
    assert accepted.status_code == 204, accepted.text
