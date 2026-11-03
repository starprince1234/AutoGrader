"""Internal identity lookup: the endpoint B2's mail intake resolves a sender with.

Three properties matter more than the happy path:

1. a user session must not open it (it answers "which account has this address" for any
   address, which is what enumeration wants);
2. an unknown address is `404`, so the intake can quarantine the message instead of guessing;
3. the response carries no address back, so B2 does not end up holding a second copy of it.
"""

from __future__ import annotations

import pytest

SERVICE_TOKEN = "test-service-token"


@pytest.fixture
def student_with_email(factory):
    return factory.user(
        username="stu-qq", password="correct horse battery staple", roles=("student",)
    )


def _set_email(engine, user_id, email: str) -> None:
    from sqlalchemy import update

    from autograder_b4.db.tables import users

    with engine.begin() as connection:
        connection.execute(
            update(users).where(users.c.user_id == user_id).values(email=email)
        )


def test_lookup_returns_the_account_for_a_known_address(client, engine, student_with_email):
    _set_email(engine, student_with_email.user_id, "2024141460192@qq.com")
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": " 2024141460192@QQ.com "},
        headers={"authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "stu-qq"
    assert "email" not in body
    assert "student" in body["roles"]


def test_unknown_address_is_404(client):
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": "nobody@qq.com"},
        headers={"authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    assert response.status_code == 404


def test_a_user_session_cannot_open_it(client, engine, student_with_email, sign_in_as):
    _set_email(engine, student_with_email.user_id, "2024141460192@qq.com")
    headers = sign_in_as("stu-qq").headers
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": "2024141460192@qq.com"},
        headers=headers,
    )
    assert response.status_code == 401


def test_a_wrong_service_token_is_refused(client):
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": "nobody@qq.com"},
        headers={"authorization": "Bearer not-the-token"},
    )
    assert response.status_code == 401
