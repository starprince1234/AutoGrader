"""Authentication and authorization boundaries.

The token verification under test is the production one: real Ed25519 (EdDSA)
signatures over a key pair generated here, in the same JWKS shape B4 publishes
(``kty: OKP`` / ``crv: Ed25519`` / ``alg: EdDSA``) and with the ``role`` claim B4
actually issues. Only the JWKS *fetch* is replaced (the allowed mock), so signature
verification, claim checks and scope parsing are exercised for real.

History worth keeping: this file used to generate an **RSA** key pair and sign RS256,
which agreed with the verifier and therefore hid the fact that B4 signs EdDSA. A test
that shares its assumption with the code under test cannot catch a wrong assumption;
integration did.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from cryptography.hazmat.primitives import serialization
from datetime import UTC, datetime

import pytest

from autograder_b3.auth import (
    JwksTokenVerifier,
    Principal,
    SERVICE_PRINCIPAL,
    StaticTokenVerifier,
    parse_bearer,
    require_course_read,
    require_course_write,
    require_service,
    require_statistics_read,
)
from autograder_b3.errors import Forbidden, Unauthenticated

from .conftest import COURSE_A, COURSE_B, SECTION_A, draft_payload
from .support import http_publish


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@pytest.fixture(scope="module")
def key_pair():
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": "test-key-1",
        "alg": "EdDSA",
        "use": "sig",
        "x": b64url(public_bytes),
    }
    return private_key, jwk


def sign(private_key, claims: dict, *, kid: str = "test-key-1", algorithm: str = "EdDSA") -> str:
    header = {"alg": algorithm, "typ": "JWT", "kid": kid}
    segments = [
        b64url(json.dumps(header, separators=(",", ":")).encode()),
        b64url(json.dumps(claims, separators=(",", ":")).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = private_key.sign(signing_input)
    return ".".join([*segments, b64url(signature)])


def claims(**overrides) -> dict:
    base = {
        "sub": str(uuid.uuid4()),
        "iss": "autograder",
        "aud": "autograder-api",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        # B4 publishes `role` (a list), not `roles`; match the issuer exactly.
        "role": ["teacher"],
        "scope": [{"course_id": str(COURSE_A), "section_id": str(SECTION_A)}],
    }
    base.update(overrides)
    return base


def verifier_with(jwk: dict) -> JwksTokenVerifier:
    return JwksTokenVerifier(
        jwks_url="https://b4.invalid/api/v1/auth/jwks",
        issuer="autograder",
        audience="autograder-api",
        fetch_jwks=lambda _url: {"keys": [jwk]},
    )


def test_valid_eddsa_token_is_accepted(key_pair):
    private_key, jwk = key_pair
    principal = verifier_with(jwk).verify(sign(private_key, claims()))
    assert principal.roles == frozenset({"teacher"})
    assert principal.course_ids == frozenset({COURSE_A})
    assert principal.section_ids == frozenset({SECTION_A})
    assert principal.is_service is False


def test_tampered_signature_is_rejected(key_pair):
    private_key, jwk = key_pair
    token = sign(private_key, claims())
    head, payload, _signature = token.split(".")
    forged = f"{head}.{payload}.{b64url(b'0' * 64)}"
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(forged)


def test_payload_tampering_after_signing_is_rejected(key_pair):
    private_key, jwk = key_pair
    token = sign(private_key, claims())
    head, _payload, signature = token.split(".")
    escalated = b64url(json.dumps(claims(role=["admin"]), separators=(",", ":")).encode())
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(f"{head}.{escalated}.{signature}")


def test_unknown_signing_key_is_rejected(key_pair):
    private_key, jwk = key_pair
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(sign(private_key, claims(), kid="another-key"))


def test_expired_token_is_rejected(key_pair):
    private_key, jwk = key_pair
    with pytest.raises(Unauthenticated) as error:
        verifier_with(jwk).verify(sign(private_key, claims(exp=int(time.time()) - 3600)))
    assert "expired" in str(error.value)


def test_wrong_issuer_or_audience_is_rejected(key_pair):
    private_key, jwk = key_pair
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(sign(private_key, claims(iss="somebody-else")))
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(sign(private_key, claims(aud="another-api")))


def test_audience_list_is_accepted(key_pair):
    private_key, jwk = key_pair
    principal = verifier_with(jwk).verify(
        sign(private_key, claims(aud=["other", "autograder-api"]))
    )
    assert principal.roles


def test_unsupported_algorithm_is_rejected(key_pair):
    private_key, jwk = key_pair
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(sign(private_key, claims(), algorithm="HS256"))


def test_malformed_tokens_are_rejected(key_pair):
    _private_key, jwk = key_pair
    verifier = verifier_with(jwk)
    for token in ("", "not-a-jwt", "a.b", "a.b.c.d", "!!!.???.###"):
        with pytest.raises(Unauthenticated):
            verifier.verify(token)


def test_unknown_role_is_rejected(key_pair):
    private_key, jwk = key_pair
    with pytest.raises(Unauthenticated):
        verifier_with(jwk).verify(sign(private_key, claims(role=["superuser"])))


def test_malformed_scope_is_rejected(key_pair):
    private_key, jwk = key_pair
    verifier = verifier_with(jwk)
    with pytest.raises(Unauthenticated):
        verifier.verify(sign(private_key, claims(scope="course-1")))
    with pytest.raises(Unauthenticated):
        verifier.verify(sign(private_key, claims(scope=[{"course_id": "not-a-uuid"}])))


def test_single_role_claim_is_accepted(key_pair):
    private_key, jwk = key_pair
    payload = claims()
    payload["role"] = "assistant"
    principal = verifier_with(jwk).verify(sign(private_key, payload))
    assert principal.roles == frozenset({"assistant"})


def test_jwks_is_cached_across_calls(key_pair):
    private_key, jwk = key_pair
    calls = []

    def fetch(url):
        calls.append(url)
        return {"keys": [jwk]}

    verifier = JwksTokenVerifier(
        jwks_url="https://b4.invalid/jwks",
        fetch_jwks=fetch,
        clock=lambda: 1000.0,
    )
    verifier.verify(sign(private_key, claims()))
    verifier.verify(sign(private_key, claims()))
    assert len(calls) == 1


# --- principal / scope policy --------------------------------------------


def teacher(course=COURSE_A) -> Principal:
    return Principal(
        subject="teacher",
        roles=frozenset({"teacher"}),
        course_ids=frozenset({course}),
        section_ids=frozenset({SECTION_A}),
    )


def test_missing_credentials_are_401():
    with pytest.raises(Unauthenticated):
        require_course_read(None, COURSE_A)
    with pytest.raises(Unauthenticated):
        require_course_write(None, COURSE_A)
    with pytest.raises(Unauthenticated):
        require_statistics_read(None, COURSE_A)


def test_cross_course_operations_are_403_not_404():
    actor = teacher(COURSE_A)
    with pytest.raises(Forbidden):
        require_course_read(actor, COURSE_B)
    with pytest.raises(Forbidden):
        require_course_write(actor, COURSE_B)
    with pytest.raises(Forbidden):
        require_statistics_read(actor, COURSE_B)


def test_students_cannot_write_or_read_statistics():
    student = Principal(
        subject="student", roles=frozenset({"student"}), course_ids=frozenset({COURSE_A})
    )
    assert require_course_read(student, COURSE_A) is student
    with pytest.raises(Forbidden):
        require_course_write(student, COURSE_A)
    with pytest.raises(Forbidden):
        require_statistics_read(student, COURSE_A)


def test_admin_scope_is_unrestricted():
    admin = Principal(subject="admin", roles=frozenset({"admin"}))
    assert admin.readable_courses() is None
    require_course_write(admin, COURSE_B)


def test_service_principal_is_required_for_the_internal_endpoint():
    with pytest.raises(Forbidden):
        require_service(teacher())
    assert require_service(SERVICE_PRINCIPAL) is SERVICE_PRINCIPAL


def test_parse_bearer_handles_scheme_case_and_blanks():
    assert parse_bearer("Bearer abc") == "abc"
    assert parse_bearer("bearer abc") == "abc"
    assert parse_bearer("Basic abc") is None
    assert parse_bearer("Bearer   ") is None
    assert parse_bearer(None) is None


def test_static_verifier_is_a_jwks_stand_in():
    token = "teacher-a"
    verifier = StaticTokenVerifier(
        claims_by_token={token: {"sub": "s", "roles": ["teacher"], "scope": []}}
    )
    assert verifier.verify(token).roles == frozenset({"teacher"})
    with pytest.raises(Unauthenticated):
        verifier.verify("unknown")


# --- through the HTTP surface --------------------------------------------


def test_statistics_requires_a_privileged_role(client, auth_headers, published_question_fixture):
    question_id, _version = published_question_fixture
    student = client.get(
        f"/api/v1/questions/{question_id}/statistics", headers=auth_headers("student-a")
    )
    assert student.status_code == 403
    assert student.json()["type"].endswith("/forbidden")

    assistant = client.get(
        f"/api/v1/questions/{question_id}/statistics", headers=auth_headers("assistant-a")
    )
    assert assistant.status_code == 200

    teacher = client.get(
        f"/api/v1/questions/{question_id}/statistics", headers=auth_headers("teacher-a")
    )
    assert teacher.status_code == 200
    payload = teacher.json()
    assert payload["attempt_count"] == 0
    assert payload["pass_rate"] == 0.0
    assert set(payload) <= {
        "question_id",
        "question_version_id",
        "attempt_count",
        "pass_rate",
        "score_distribution",
        "failure_category_distribution",
        "computed_at",
    }


def test_statistics_of_another_course_is_403(client, auth_headers, published_question_fixture):
    question_id, _version = published_question_fixture
    response = client.get(
        f"/api/v1/questions/{question_id}/statistics", headers=auth_headers("teacher-b")
    )
    assert response.status_code == 403


@pytest.fixture
def published_question_fixture(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]

    from sqlalchemy import select

    from autograder_b3.infra.models import QuestionVersion

    with client.app.state.b3_dependencies.database.app_session() as session:
        version_id = session.execute(
            select(QuestionVersion.question_version_id).where(
                QuestionVersion.question_id == uuid.UUID(question_id),
                QuestionVersion.status == "draft",
            )
        ).scalar_one()
    published = http_publish(client, version_id)
    assert published.status_code == 200, published.text
    return question_id, published.json()


def test_now_helper_formats_millisecond_timestamps():
    from autograder_b3.api.app import now_millis

    assert now_millis().endswith("Z")
    assert datetime.now(UTC).year == int(now_millis()[:4])
