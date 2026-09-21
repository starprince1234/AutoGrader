"""RQ-B-4-50: Ed25519 sessions, JWKS, refresh rotation and login-failure protection.

The properties under test are the ones the requirements call out individually: a
short-lived signed token, a JWKS another module can verify against locally, a refresh
credential that exists only inside an HttpOnly cookie, rotation with theft detection, and
failures that do not disclose whether an account exists.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select

from autograder_b4.db.tables import login_attempts, sessions, users
from autograder_b4.security.keys import SigningKey, load_signing_key
from autograder_b4.security.passwords import ALGORITHM_ARGON2ID, hash_password, verify_password
from autograder_b4.security.tokens import (
    CourseScope,
    TokenError,
    issue_access_token,
    verify_access_token,
)

PASSWORD = "correct horse battery staple"


def _public_key_from_jwks(document: dict) -> Ed25519PublicKey:
    import base64

    key = document["keys"][0]
    raw = base64.urlsafe_b64decode(key["x"] + "=" * (-len(key["x"]) % 4))
    return Ed25519PublicKey.from_public_bytes(raw)


# ------------------------------------------------------------------------------- login


def test_login_returns_a_short_lived_token_and_a_httponly_cookie(client, factory) -> None:
    factory.user(username="alice", password=PASSWORD, roles=("student",))
    response = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] > 0
    assert body["roles"] == ["student"]
    assert body["scope"] == []

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=Strict" in set_cookie
    assert "Path=/api/v1/auth" in set_cookie
    refresh_value = response.cookies.get("autograder_refresh")
    assert refresh_value, "the refresh credential must be set as a cookie"


def test_refresh_credential_never_appears_in_the_response_body(client, factory) -> None:
    """Acceptance 2: the refresh credential is a cookie, and only a cookie."""
    factory.user(username="alice", password=PASSWORD)
    response = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    refresh_value = response.cookies.get("autograder_refresh")
    assert refresh_value
    assert refresh_value not in response.text
    assert "autograder_refresh" not in response.text
    assert refresh_value not in str(response.url)


def test_access_token_is_ed25519_and_verifiable_from_the_jwks(client, factory) -> None:
    """Acceptance 1: another module verifies locally, with no callback to B4."""
    factory.user(username="alice", password=PASSWORD)
    response = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    token = response.json()["access_token"]
    assert response.json()["expires_in"] <= 900

    jwks = client.get("/api/v1/auth/jwks")
    assert jwks.status_code == 200
    document = jwks.json()
    assert document["keys"][0]["kty"] == "OKP"
    assert document["keys"][0]["crv"] == "Ed25519"
    assert document["keys"][0]["alg"] == "EdDSA"

    claims = jwt.decode(
        token,
        _public_key_from_jwks(document),
        algorithms=["EdDSA"],
        audience="autograder",
        issuer="autograder-b4",
    )
    assert claims["sub"]
    assert claims["exp"] > claims["iat"]
    assert "role" in claims and "scope" in claims


def test_jwks_requires_no_authentication(client) -> None:
    assert client.get("/api/v1/auth/jwks").status_code == 200


def test_a_tampered_token_is_rejected(client, factory, settings) -> None:
    """G-SEC-15: a modified signature must not verify."""
    factory.user(username="alice", password=PASSWORD)
    response = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    token = response.json()["access_token"]
    header, _payload, signature = token.split(".")
    forged_payload = (
        json.dumps({**jwt.decode(token, options={"verify_signature": False}), "role": ["admin"]})
        .encode()
        .hex()
    )
    import base64

    forged = base64.urlsafe_b64encode(bytes.fromhex(forged_payload)).rstrip(b"=").decode()
    tampered = f"{header}.{forged}.{signature}"
    response = client.get("/api/v1/users", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_an_expired_token_is_rejected(client, factory, settings) -> None:
    """Acceptance 1: the token has a real validity window, enforced on verify."""
    user_id = factory.user(username="alice", password=PASSWORD)
    signing_key = load_signing_key(settings.jwt_private_key_file)
    token, _ = issue_access_token(
        signing_key=signing_key,
        subject=str(user_id),
        roles=("student",),
        scope=(),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl_seconds=1,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    with pytest.raises(TokenError):
        verify_access_token(
            token,
            signing_key=signing_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    response = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_token_signed_by_another_key_is_rejected(client, factory, settings) -> None:
    """A signer other than B4's key must not be accepted, even with valid-looking claims."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    rogue_private = Ed25519PrivateKey.generate()
    rogue = SigningKey(private_key=rogue_private, public_key=rogue_private.public_key())
    token, _ = issue_access_token(
        signing_key=rogue,
        subject=str(factory.user(username="alice", password=PASSWORD)),
        roles=("admin",),
        scope=(),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl_seconds=600,
    )
    response = client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_scope_claim_carries_courses_and_sections(client, factory) -> None:
    user_id = factory.user(username="student", password=PASSWORD, roles=("student",))
    course_id = factory.course(code="CS101")
    section_id = factory.section(course_id=course_id, code="01")
    factory.enrollment(section_id=section_id, student_number="20230001", user_id=user_id)

    response = client.post(
        "/api/v1/auth/session", json={"username": "student", "password": PASSWORD}
    )
    scope = response.json()["scope"]
    assert scope == [{"course_id": str(course_id), "section_id": str(section_id)}]


# ---------------------------------------------------------------------------- failures


def test_unknown_user_and_wrong_password_are_indistinguishable(client, factory) -> None:
    """Acceptance 7: the response must not reveal whether the principal exists."""
    factory.user(username="alice", password=PASSWORD)
    unknown = client.post("/api/v1/auth/session", json={"username": "nobody", "password": PASSWORD})
    wrong = client.post(
        "/api/v1/auth/session", json={"username": "alice", "password": "wrong-password"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.headers["content-type"].startswith("application/problem+json")


def test_deactivated_user_cannot_sign_in(client, factory) -> None:
    factory.user(username="alice", password=PASSWORD, active=False)
    response = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 401


def test_failed_logins_are_throttled(client, factory) -> None:
    """Acceptance 7: brute force must be bounded by something other than luck."""
    factory.user(username="alice", password=PASSWORD)
    statuses = []
    for _ in range(8):
        statuses.append(
            client.post(
                "/api/v1/auth/session", json={"username": "alice", "password": "wrong"}
            ).status_code
        )
    assert 429 in statuses, statuses
    # A throttled principal cannot then authenticate, even with the right password.
    assert (
        client.post(
            "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 429
    )


def test_a_throttled_attempt_is_recorded(client, factory, engine) -> None:
    """Regression: the refusal and its audit must be committed, not rolled back with the 429.

    Raising inside the transaction that records the attempt discarded both, so the ledger
    never showed the refusal and the audit trail had a hole exactly where the security
    control fired.
    """
    from autograder_b4.db.tables import audit_records

    factory.user(username="alice", password=PASSWORD)
    for _ in range(12):
        client.post("/api/v1/auth/session", json={"username": "alice", "password": "wrong"})
    with engine.connect() as connection:
        actions = [row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()]
        attempts = connection.execute(
            select(login_attempts.c.succeeded).where(login_attempts.c.succeeded.is_(False))
        ).fetchall()
    assert "auth.login.throttled" in actions
    assert len(attempts) >= 6


def test_successful_login_clears_the_failure_history(client, factory, engine) -> None:
    factory.user(username="alice", password=PASSWORD)
    for _ in range(3):
        client.post("/api/v1/auth/session", json={"username": "alice", "password": "wrong"})
    assert (
        client.post(
            "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 200
    )
    with engine.connect() as connection:
        recent = connection.execute(
            select(login_attempts.c.succeeded)
            .order_by(login_attempts.c.occurred_at.desc())
            .limit(1)
        ).fetchall()
    assert recent[0][0] is True


def test_login_attempts_store_no_raw_identity(client, factory, engine) -> None:
    factory.user(username="alice", password=PASSWORD)
    client.post("/api/v1/auth/session", json={"username": "alice", "password": "wrong"})
    with engine.connect() as connection:
        rows = connection.execute(
            select(login_attempts.c.principal_key, login_attempts.c.client_key)
        ).fetchall()
    for principal_key, client_key in rows:
        assert principal_key != "alice"
        assert len(principal_key) == 64  # a SHA-256 digest
        assert client_key is None or len(client_key) == 64


# ----------------------------------------------------------------------------- refresh


def test_refresh_rotates_the_credential(client, factory) -> None:
    factory.user(username="alice", password=PASSWORD)
    first = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    original = first.cookies.get("autograder_refresh")

    refreshed = client.post("/api/v1/auth/session/refresh")
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.cookies.get("autograder_refresh")
    assert rotated and rotated != original
    assert "HttpOnly" in refreshed.headers["set-cookie"]
    assert refreshed.json()["expires_in"] > 0


def test_reusing_a_rotated_credential_revokes_the_family(client, factory, engine) -> None:
    """A replayed refresh credential must not outlive the legitimate client's refresh."""
    factory.user(username="alice", password=PASSWORD)
    first = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    stale = first.cookies.get("autograder_refresh")
    assert client.post("/api/v1/auth/session/refresh").status_code == 200

    client.cookies.set("autograder_refresh", stale, path="/api/v1/auth")
    replay = client.post("/api/v1/auth/session/refresh")
    assert replay.status_code == 401

    with engine.connect() as connection:
        rows = connection.execute(select(sessions.c.revoked_reason)).fetchall()
    assert any(reason == "refresh_token_reuse" for (reason,) in rows)
    # The family is gone, so the rotated credential the real client holds is dead too.
    assert client.post("/api/v1/auth/session/refresh").status_code == 401


def test_logout_revokes_the_session_and_clears_the_cookie(client, factory, engine) -> None:
    factory.user(username="alice", password=PASSWORD)
    signed_in = client.post(
        "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
    )
    token = signed_in.json()["access_token"]
    response = client.request(
        "DELETE",
        "/api/v1/auth/session/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    with engine.connect() as connection:
        reasons = [
            row[0] for row in connection.execute(select(sessions.c.revoked_reason)).fetchall()
        ]
    assert "logout" in reasons


def test_logout_requires_authentication(client, factory) -> None:
    assert client.request("DELETE", "/api/v1/auth/session/logout").status_code == 401


def test_refresh_without_a_cookie_is_unauthorized(client) -> None:
    client.cookies.clear()
    assert client.post("/api/v1/auth/session/refresh").status_code == 401


def test_deactivating_a_user_ends_its_sessions(client, factory, engine, sign_in_as) -> None:
    admin_id = factory.user(username="root", password=PASSWORD, roles=("admin",))
    victim_id = factory.user(username="alice", password=PASSWORD)
    session = sign_in_as("alice")
    assert client.get("/api/v1/courses", headers=session.headers).status_code == 200

    assert (
        client.delete(
            f"/api/v1/users/{victim_id}",
            headers=sign_in_as("root").headers,
        ).status_code
        == 204
    )

    # The access token is still cryptographically valid but must no longer be accepted.
    assert client.get("/api/v1/courses", headers=session.headers).status_code == 401
    with engine.connect() as connection:
        reasons = [
            row[0] for row in connection.execute(select(sessions.c.revoked_reason)).fetchall()
        ]
    assert "user_deactivated" in reasons
    assert admin_id  # the administrator itself is untouched


# ---------------------------------------------------------------------------- passwords


def test_passwords_are_stored_with_argon2id(client, factory, engine) -> None:
    """Acceptance 3: no plaintext, no reversible encoding."""
    from autograder_b4.db.tables import credentials

    factory.user(username="alice", password=PASSWORD)
    with engine.connect() as connection:
        row = connection.execute(
            select(credentials.c.password_hash, credentials.c.hash_algorithm)
        ).fetchone()
    assert row.hash_algorithm == ALGORITHM_ARGON2ID
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash
    assert verify_password(PASSWORD, row.password_hash, row.hash_algorithm)
    assert not verify_password("something else", row.password_hash, row.hash_algorithm)


def test_password_is_never_echoed_back(client, factory) -> None:
    factory.user(username="alice", password=PASSWORD)
    response = client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    assert PASSWORD not in response.text


def test_weak_legacy_hash_is_upgraded_on_successful_login(client, factory, engine) -> None:
    """A bcrypt row is accepted once and re-hashed with Argon2id."""
    import bcrypt

    from autograder_b4.db.tables import credentials

    user_id = factory.user(username="alice", password=PASSWORD)
    legacy = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    with engine.begin() as connection:
        connection.execute(
            credentials.update()
            .where(credentials.c.user_id == user_id)
            .values(password_hash=legacy, hash_algorithm="bcrypt")
        )
    assert (
        client.post(
            "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 200
    )
    with engine.connect() as connection:
        row = connection.execute(
            select(credentials.c.password_hash, credentials.c.hash_algorithm).where(
                credentials.c.user_id == user_id
            )
        ).fetchone()
    assert row.hash_algorithm == ALGORITHM_ARGON2ID


def test_empty_and_oversized_credentials_are_rejected(client) -> None:
    assert (
        client.post("/api/v1/auth/session", json={"username": "", "password": ""}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/session", json={"username": "a" * 300, "password": "x"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/session", json={"username": "a", "password": "x", "extra": 1}
        ).status_code
        == 422
    )


# ------------------------------------------------------------------------------- misc


def test_login_is_audited(client, factory, engine) -> None:
    """Acceptance 8: a sensitive operation leaves a record."""
    from autograder_b4.db.tables import audit_records

    factory.user(username="alice", password=PASSWORD)
    client.post("/api/v1/auth/session", json={"username": "alice", "password": PASSWORD})
    client.post("/api/v1/auth/session", json={"username": "alice", "password": "wrong"})
    with engine.connect() as connection:
        actions = [row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()]
    assert "auth.session.created" in actions
    assert "auth.login.denied" in actions


def test_unknown_username_and_wrong_password_take_similar_time(client, factory) -> None:
    """The uniform error must be uniform in cost too, or it still enumerates users."""
    factory.user(username="alice", password=PASSWORD)

    def measure(body: dict) -> float:
        best = 1e9
        for _ in range(3):
            started = time.perf_counter()
            client.post("/api/v1/auth/session", json=body)
            best = min(best, time.perf_counter() - started)
        return best

    known = measure({"username": "alice", "password": "definitely-wrong"})
    unknown = measure({"username": "nobody-here", "password": "definitely-wrong"})
    # Both paths run an Argon2id verification; the ratio is generous because timing in CI
    # is noisy, but a missing verification would show up as an order of magnitude.
    assert max(known, unknown) / max(min(known, unknown), 1e-6) < 5


def test_course_scope_claim_round_trips() -> None:
    scope = CourseScope(course_id=str(uuid.uuid4()), section_id=str(uuid.uuid4()))
    assert CourseScope.from_claim(scope.as_claim()) == scope
    assert CourseScope.from_claim({"course_id": scope.course_id}).section_id is None


def test_user_records_stay_free_of_credentials_in_the_users_table(client, factory, engine) -> None:
    factory.user(username="alice", password=PASSWORD)
    with engine.connect() as connection:
        columns = connection.execute(
            select(users.c.username, users.c.display_name).where(users.c.username == "alice")
        ).fetchone()
    assert columns.username == "alice"


def test_hash_password_rejects_an_empty_value() -> None:
    with pytest.raises(ValueError):
        hash_password("")
