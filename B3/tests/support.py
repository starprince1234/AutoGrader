"""Helpers shared by the integration tests.

Two styles are used deliberately:

* **HTTP** for anything whose contract is a response body (field absence, status
  codes, headers) - proof must be about the real wire bytes;
* **domain** for publish/immutability/statistics rules that must hold regardless of
  the transport, and for states an HTTP client cannot legitimately reach.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

from autograder_b3.auth import Principal
from autograder_b3.dto.internal import QuestionDraftInputModel
from autograder_b3.infra.models import Question, QuestionVersion

from .conftest import COURSE_A, SECTION_A, TEACHER_ID


def teacher_principal(
    *,
    subject: str = TEACHER_ID,
    course_ids: tuple[uuid.UUID, ...] = (COURSE_A,),
    section_ids: tuple[uuid.UUID, ...] = (SECTION_A,),
) -> Principal:
    return Principal(
        subject=subject,
        roles=frozenset({"teacher"}),
        course_ids=frozenset(course_ids),
        section_ids=frozenset(section_ids),
    )


def student_principal(
    *, course_ids: tuple[uuid.UUID, ...] = (COURSE_A,), section_ids: tuple[uuid.UUID, ...] = (SECTION_A,)
) -> Principal:
    return Principal(
        subject=str(uuid.uuid4()),
        roles=frozenset({"student"}),
        course_ids=frozenset(course_ids),
        section_ids=frozenset(section_ids),
    )


def draft(payload: dict) -> QuestionDraftInputModel:
    return QuestionDraftInputModel.model_validate(payload)


def create_question(
    database,
    settings,
    *,
    principal: Principal,
    course_id: uuid.UUID = COURSE_A,
    payload: dict,
    cases_supplied: bool | None = None,
    now: datetime | None = None,
) -> tuple[Question, QuestionVersion]:
    """Create a question through the domain entry point.

    ``cases_supplied`` mirrors the domain parameter and defaults to ``None`` ("derive it
    from the payload"), which is what a caller that built a payload dict means anyway;
    tests about ``CHG-0017`` pass it explicitly to say "the key was absent".
    """
    from autograder_b3.domain.questions import create_question as domain_create

    return domain_create(
        database,
        settings,
        principal=principal,
        course_id=course_id,
        payload=draft(payload),
        cases_supplied=cases_supplied,
        now=now or datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )


def attest(
    database,
    *,
    question_version_id: uuid.UUID,
    verifier: str = "tests/support.py",
    cases_total: int | None = None,
    cases_passed: int | None = None,
    content_digest: str | None = None,
    now: datetime | None = None,
):
    """Record the attestation ``publish`` now requires.

    Mirrors what a real verifier does: run the reference answer against every case, then
    report the digest of the content that was run. ``cases_total`` defaults to the
    version's real case count, because the recorder cross-checks it - an attestation
    that describes a different case set is refused (``409``).
    """
    from autograder_b3.auth import SERVICE_PRINCIPAL
    from autograder_b3.domain.attestations import record_reference_attestation
    from autograder_b3.domain.version_content import load_version_content
    from autograder_b3.infra.repositories import QuestionBankRepository

    with database.protected_session() as session:
        version = QuestionBankRepository(session).get_version(question_version_id)
        assert version is not None
        content = load_version_content(session, version)
        digest = content_digest or content.content_digest
        total = cases_total if cases_total is not None else len(content.cases)
        passed = cases_passed if cases_passed is not None else total

    return record_reference_attestation(
        database,
        principal=SERVICE_PRINCIPAL,
        question_version_id=question_version_id,
        content_digest=digest,
        verifier=verifier,
        cases_total=total,
        cases_passed=passed,
        verified_at=None,
        notes=None,
        now=now or datetime(2026, 9, 15, 8, 4, tzinfo=UTC),
    )


def publish(
    database,
    *,
    principal: Principal,
    question_version_id: uuid.UUID,
    now=None,
    attestation: bool = True,
):
    """Publish through the domain entry point.

    ``attestation=True`` (the default) records a matching attestation first, because
    publication now requires one. Tests that exercise the refusal pass ``False``.
    """
    from autograder_b3.domain.versions import publish_version

    if attestation:
        attest(database, question_version_id=question_version_id, now=now)
    return publish_version(
        database,
        principal=principal,
        question_version_id=question_version_id,
        now=now or datetime(2026, 9, 15, 8, 5, tzinfo=UTC),
        correlation_id=uuid.uuid4(),
    )


def create_published_question(
    database,
    settings,
    *,
    principal: Principal | None = None,
    course_id: uuid.UUID = COURSE_A,
    payload: dict,
    now: datetime | None = None,
) -> tuple[Question, QuestionVersion]:
    actor = principal or teacher_principal()
    question, version = create_question(
        database, settings, principal=actor, course_id=course_id, payload=payload, now=now
    )
    published, _content_digest, _package_digest = publish(
        database,
        principal=actor,
        question_version_id=version.question_version_id,
        now=(now.replace(minute=5) if now else None),
    )
    return question, published


TEACHER_A_HEADERS = {"Authorization": "Bearer teacher-a"}
SERVICE_HEADERS = {"Authorization": "Bearer test-service-token"}


def http_attestation(client, question_version_id: uuid.UUID, *, cases: int = 2) -> dict:
    """Read the version's digest over HTTP, then record a matching attestation.

    Used by the HTTP publish tests: the digest has to come from the API because the
    verifier is an external process, and the case count has to match because the
    recorder cross-checks it.
    """
    detail = client.get(
        f"/api/v1/question-versions/{question_version_id}", headers=TEACHER_A_HEADERS
    )
    assert detail.status_code == 200, detail.text
    response = client.post(
        f"/api/v1/question-versions/{question_version_id}/attestations",
        json={
            "content_digest": detail.json()["content_digest"],
            "verifier": "tests/support.py",
            "cases_total": cases,
            "cases_passed": cases,
        },
        headers=SERVICE_HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


def http_publish(client, question_version_id: uuid.UUID, *, cases: int = 2):
    """Record an attestation, then publish - the two steps a real caller performs."""
    http_attestation(client, question_version_id, cases=cases)
    return client.post(
        f"/api/v1/question-versions/{question_version_id}/publish",
        headers=TEACHER_A_HEADERS,
    )


def b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
