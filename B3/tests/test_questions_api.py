"""TC-QST-01/02/03: the public surface never carries protected content.

The central assertion is **field absence**, not "field is empty" (acceptance 3-10-1):
a public response that grows a ``reference_answer`` key next year must fail this
test even if the value is ``null``. The hidden expectations are also searched for as
raw substrings in the response body, so an accidental echo through a docstring or a
nested object is caught too.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from autograder_b3.api.serializers import (
    authoring_assistance_to_public,
    serialize_public,
)
from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES

from .conftest import COURSE_A, COURSE_B, COURSE_C, draft_payload
from .support import PNG_BYTES, b64, http_attestation, http_publish

B3_ROOT = Path(__file__).resolve().parents[1]

REFERENCE_ANSWER = "#!/usr/bin/env bash\nwc -l < /dev/stdin\n"
#: A second language's answer, so "the version's declared language" is provable rather than
#: assumed (``CHG-0023``): with one stored answer, "return the only row there is" would look
#: exactly like "return the answer for the declared language".
PYTHON_ANSWER = "print('the python one')\n"
HIDDEN_STDOUT = "0\n"
HIDDEN_CASE_NAME = "hidden edge case: empty input"
#: The literal segment that must be declared before ``/api/v1/questions/{question_id}``.
COVERAGE = "/api/v1/questions/coverage"

PROTECTED_KEYS = {
    "reference_answer",
    "reference_answers",
    "hidden_cases",
    "test_cases",
    "expected_output",
    "expected_stdout",
    "expected_stderr",
    "expected_exit_code",
    "fixture_content",
    "fixtures",
    "source_code",
    "student_id",
}


def collect_keys(value, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            found |= collect_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= collect_keys(item)
    return found


@pytest.fixture
def published_question(client, auth_headers):
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=REFERENCE_ANSWER),
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 201, response.text
    question = response.json()
    version_id = _first_version(client, auth_headers, question["question_id"])
    publish = http_publish(client, version_id)
    assert publish.status_code == 200, publish.text
    return question, publish.json()


def _first_version(client, auth_headers, question_id) -> str:
    from sqlalchemy import select

    from autograder_b3.infra.models import QuestionVersion

    dependencies = client.app.state.b3_dependencies
    with dependencies.database.app_session() as session:
        rows = session.execute(
            select(QuestionVersion.question_version_id).where(
                QuestionVersion.question_id == uuid.UUID(str(question_id))
            )
        ).scalars()
        return str(next(iter(rows)))


# --- TC-QST-01: no protected field in a public response ------------------


def test_list_questions_has_no_protected_field(client, auth_headers, published_question):
    response = client.get(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        headers=auth_headers("student-a"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    keys = collect_keys(body)
    assert keys & PROTECTED_KEYS == set(), f"protected key leaked: {keys & PROTECTED_KEYS}"
    assert REFERENCE_ANSWER not in response.text
    assert HIDDEN_CASE_NAME not in response.text
    assert body["items"], "the published question should be listed"


def test_question_detail_has_no_protected_field(client, auth_headers, published_question):
    question, _version = published_question
    response = client.get(
        f"/api/v1/questions/{question['question_id']}", headers=auth_headers("student-a")
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "question_id",
        "course_id",
        "title",
        "stem_markdown",
        "stem_assets",
        "language",
        "status",
        "tags",
        # The version history is part of the public detail DTO: without it a client cannot
        # address a specific version, and B1's version picker had to be fed by hand.
        "versions",
        "created_at",
        "updated_at",
    }
    assert collect_keys(body) & PROTECTED_KEYS == set()
    assert REFERENCE_ANSWER not in response.text
    assert HIDDEN_STDOUT not in response.text


def test_question_version_dto_is_public_only(client, auth_headers, published_question):
    _question, version = published_question
    response = client.get(
        f"/api/v1/question-versions/{version['question_version_id']}",
        headers=auth_headers("student-a"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "question_version_id",
        "question_id",
        "version",
        "status",
        "language",
        "adapter_kind",
        "content_digest",
        # CHG-0013: why this version exists, and what changed. Both are deliberately
        # content-free - the reason is text the author wrote, the diff is counts and
        # digests - which is what makes them safe on the public version DTO.
        "revision_note",
        "revision_diff",
        "published_at",
        "max_files",
        "max_total_bytes",
        # The frozen stem belongs to the version, not to the question: rendering the
        # question's *current* stem under a historical version heading is the failure mode
        # this field exists to prevent.
        "stem_markdown",
        "stem_assets",
    }
    assert body["content_digest"] and len(body["content_digest"]) == 64
    # A first version has nothing to diff against; `null`, not an object of falses.
    assert body["revision_diff"] is None


def test_public_dto_vocabulary_is_disjoint_from_the_protected_one():
    from autograder_b3.dto.public import PUBLIC_DTO_TYPES

    for model in PUBLIC_DTO_TYPES:
        assert set(model.model_fields) & PROTECTED_FIELD_NAMES == set()


# --- authentication and scope --------------------------------------------


def test_unauthenticated_request_is_401(client):
    response = client.get("/api/v1/questions")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body) == {"type", "title", "status", "detail", "instance"}
    assert body["status"] == 401


def test_unknown_token_is_401(client):
    response = client.get("/api/v1/questions", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_cross_course_read_is_403_not_404(client, auth_headers, published_question):
    """TC-QST-03: no 404 masking for an authorization failure."""
    question, _version = published_question
    response = client.get(
        f"/api/v1/questions/{question['question_id']}", headers=auth_headers("teacher-b")
    )
    assert response.status_code == 403
    assert response.json()["type"].endswith("/forbidden")


def test_unknown_question_is_404_for_an_authorized_caller(client, auth_headers):
    response = client.get(f"/api/v1/questions/{uuid.uuid4()}", headers=auth_headers("teacher-a"))
    assert response.status_code == 404


def test_student_only_sees_published_questions(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(title="still a draft"),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201
    question_id = created.json()["question_id"]

    student_list = client.get(
        "/api/v1/questions", params={"course_id": str(COURSE_A)}, headers=auth_headers("student-a")
    )
    assert all(item["question_id"] != question_id for item in student_list.json()["items"])

    student_detail = client.get(
        f"/api/v1/questions/{question_id}", headers=auth_headers("student-a")
    )
    assert student_detail.status_code == 403

    teacher_detail = client.get(
        f"/api/v1/questions/{question_id}", headers=auth_headers("teacher-a")
    )
    assert teacher_detail.status_code == 200


def test_student_cannot_write(client, auth_headers):
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("student-a"),
    )
    assert response.status_code == 403


def test_create_question_in_another_course_is_forbidden(client, auth_headers):
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_B)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 403


# --- stem validation -----------------------------------------------------


@pytest.mark.parametrize(
    "stem",
    [
        "Count the lines<script>alert(1)</script>",
        "<iframe src='http://evil'></iframe>",
        "[click](javascript:alert(1))",
        "<style>body{display:none}</style>",
    ],
)
def test_stem_with_executable_markup_is_rejected(client, auth_headers, stem):
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(stem=stem),
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422, response.text


# --- TC-QST-02: controlled stem graphics ---------------------------------


@pytest.fixture
def question_with_asset(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]
    upload = client.post(
        f"/api/v1/questions/{question_id}/assets",
        json={
            "content_type": "image/png",
            "content_base64": b64(PNG_BYTES),
            "alt_text": "a 1x1 diagram",
        },
        headers=auth_headers("teacher-a"),
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert len(body["content_sha256"]) == 64
    assert "content_base64" not in body
    return question_id, body


def test_asset_requires_authorization(client, auth_headers, question_with_asset):
    question_id, asset = question_with_asset
    response = client.get(
        f"/api/v1/questions/{question_id}/assets/{asset['asset_id']}",
        headers=auth_headers("teacher-b"),
    )
    assert response.status_code == 403


def test_unpublished_asset_is_not_served_to_students(client, auth_headers, question_with_asset):
    question_id, asset = question_with_asset
    response = client.get(
        f"/api/v1/questions/{question_id}/assets/{asset['asset_id']}",
        headers=auth_headers("student-a"),
    )
    assert response.status_code == 403


def test_published_asset_is_served_with_hardening_headers(client, auth_headers, question_with_asset):
    question_id, asset = question_with_asset
    version_id = _first_version(client, auth_headers, question_id)
    publish = http_publish(client, version_id)
    assert publish.status_code == 200, publish.text

    response = client.get(
        f"/api/v1/questions/{question_id}/assets/{asset['asset_id']}",
        headers=auth_headers("student-a"),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["etag"] == f'"{asset["content_sha256"]}"'
    assert response.content == PNG_BYTES


def test_asset_content_type_allowlist_refuses_svg(client, auth_headers, question_with_asset):
    question_id, _asset = question_with_asset
    response = client.post(
        f"/api/v1/questions/{question_id}/assets",
        json={"content_type": "image/svg+xml", "content_base64": b64(b"<svg/>")},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422
    assert "SVG" in response.json()["detail"]


def test_asset_bytes_must_match_the_declared_type(client, auth_headers, question_with_asset):
    question_id, _asset = question_with_asset
    response = client.post(
        f"/api/v1/questions/{question_id}/assets",
        json={"content_type": "image/png", "content_base64": b64(b"not a png at all")},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422


def test_asset_upload_is_refused_once_published(client, auth_headers, question_with_asset):
    question_id, _asset = question_with_asset
    version_id = _first_version(client, auth_headers, question_id)
    http_publish(client, version_id)
    response = client.post(
        f"/api/v1/questions/{question_id}/assets",
        json={"content_type": "image/png", "content_base64": b64(PNG_BYTES)},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 409


def test_stem_assets_appear_in_the_public_dto_after_publish(
    client, auth_headers, question_with_asset
):
    question_id, asset = question_with_asset
    version_id = _first_version(client, auth_headers, question_id)
    http_publish(client, version_id)
    response = client.get(
        f"/api/v1/questions/{question_id}", headers=auth_headers("student-a")
    )
    assert response.status_code == 200
    assets = response.json()["stem_assets"]
    assert [item["asset_id"] for item in assets] == [asset["asset_id"]]
    assert set(assets[0]) == {"asset_id", "content_type", "alt_text"}


# --- reference-answer attestations (CHG-0008) ---------------------------


def test_record_reference_attestation_requires_a_service_token(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    version_id = _first_version(client, auth_headers, created.json()["question_id"])
    detail = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    ).json()

    body = {
        "content_digest": detail["content_digest"],
        "verifier": "MAIN/scripts/verify_question_bank.py",
        "cases_total": 2,
        "cases_passed": 2,
    }
    assert (
        client.post(
            f"/api/v1/question-versions/{version_id}/attestations",
            json=body,
            headers=auth_headers("teacher-a"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/question-versions/{version_id}/attestations",
            json=body,
            headers=auth_headers("student-a"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/question-versions/{version_id}/attestations", json=body
        ).status_code
        == 401
    )


def test_attestation_response_carries_no_protected_content(client, auth_headers):
    """The public attestation shape is a fact, counts and a digest - nothing else."""
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=REFERENCE_ANSWER),
        headers=auth_headers("teacher-a"),
    )
    version_id = _first_version(client, auth_headers, created.json()["question_id"])
    body = http_attestation(client, version_id)

    assert set(body) == {
        "attestation_id",
        "question_version_id",
        "content_digest",
        "verifier",
        "cases_total",
        "cases_passed",
        "verified_at",
    }
    assert collect_keys(body) & PROTECTED_KEYS == set()
    assert REFERENCE_ANSWER not in json.dumps(body)
    assert HIDDEN_CASE_NAME not in json.dumps(body)


def test_publish_without_an_attestation_is_422_and_names_the_fix(client, auth_headers):
    """The refusal has to point at the remedy that *exists now*.

    It used to name the bank-verifier script and the attestation endpoint -- the path
    `CHG-0019`/`ADR-0015` replaced with the teacher-side "verify and publish" action. A
    teacher blocked at publish was being sent to find an operator and a script instead of the
    button in front of them, so the third assertion below is the one that matters: the legacy
    pointer must not come back.
    """
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    version_id = _first_version(client, auth_headers, created.json()["question_id"])
    response = client.post(
        f"/api/v1/question-versions/{version_id}/publish", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"].endswith("/reference-attestation-missing")
    detail = problem["detail"]
    # The information that was right before is kept: which content is unverified (by digest --
    # the message never named the version id) and what invalidates an attestation.
    digest = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    ).json()["content_digest"]
    assert digest in detail
    assert "invalidates the attestation" in detail
    # The remedy named is the current one, and the superseded pointer is gone.
    assert "verify and publish" in detail
    assert "verify_question_bank" not in detail


def test_attestation_for_a_different_digest_is_refused(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    version_id = _first_version(client, auth_headers, created.json()["question_id"])
    response = client.post(
        f"/api/v1/question-versions/{version_id}/attestations",
        json={
            "content_digest": "f" * 64,
            "verifier": "MAIN/scripts/verify_question_bank.py",
            "cases_total": 2,
            "cases_passed": 2,
        },
        headers={"Authorization": "Bearer test-service-token"},
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/attestation-mismatch")


def test_editing_the_draft_invalidates_the_attestation(client, auth_headers):
    """The digest is the binding: change the content and the old voucher is void."""
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]
    version_id = _first_version(client, auth_headers, question_id)
    http_attestation(client, version_id)

    edited = client.patch(
        f"/api/v1/questions/{question_id}",
        json=draft_payload(stem="A materially different stem."),
        headers=auth_headers("teacher-a"),
    )
    assert edited.status_code == 200

    refused = client.post(
        f"/api/v1/question-versions/{version_id}/publish", headers=auth_headers("teacher-a")
    )
    assert refused.status_code == 422
    assert refused.json()["type"].endswith("/reference-attestation-missing")

    # Re-verifying the new content restores the ability to publish: no override flag,
    # no compatibility path - the only way forward is a voucher for the current bytes.
    assert http_publish(client, version_id).status_code == 200


def test_attestation_is_only_accepted_for_a_draft(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    version_id = _first_version(client, auth_headers, created.json()["question_id"])
    published = http_publish(client, version_id)
    assert published.status_code == 200
    digest = published.json()["content_digest"]

    response = client.post(
        f"/api/v1/question-versions/{version_id}/attestations",
        json={
            "content_digest": digest,
            "verifier": "MAIN/scripts/verify_question_bank.py",
            "cases_total": 2,
            "cases_passed": 2,
        },
        headers={"Authorization": "Bearer test-service-token"},
    )
    assert response.status_code == 409
    assert response.json()["type"].endswith("/invalid-state-transition")


def test_attestation_case_count_must_describe_this_version(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    version_id = _first_version(client, auth_headers, created.json()["question_id"])
    detail = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    ).json()
    response = client.post(
        f"/api/v1/question-versions/{version_id}/attestations",
        json={
            "content_digest": detail["content_digest"],
            "verifier": "MAIN/scripts/verify_question_bank.py",
            "cases_total": 7,
            "cases_passed": 7,
        },
        headers={"Authorization": "Bearer test-service-token"},
    )
    assert response.status_code == 409


def test_attestation_for_an_unknown_version_is_404(client):
    response = client.post(
        f"/api/v1/question-versions/{uuid.uuid4()}/attestations",
        json={
            "content_digest": "a" * 64,
            "verifier": "MAIN/scripts/verify_question_bank.py",
            "cases_total": 1,
            "cases_passed": 1,
        },
        headers={"Authorization": "Bearer test-service-token"},
    )
    assert response.status_code == 404


# --- tag write path (CHG-0008) ------------------------------------------


def _tags_of(client, question_id) -> list[str]:
    response = client.get(
        f"/api/v1/questions/{question_id}", headers={"Authorization": "Bearer teacher-a"}
    )
    assert response.status_code == 200, response.text
    return response.json()["tags"]


def test_tags_round_trip_through_create_and_update(client, auth_headers):
    payload = draft_payload()
    payload["tags"] = ["text-tools", "processes"]
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]
    assert created.json()["tags"] == ["text-tools", "processes"]
    assert _tags_of(client, question_id) == ["text-tools", "processes"]

    edited = dict(payload)
    edited["tags"] = ["text-tools"]
    updated = client.patch(
        f"/api/v1/questions/{question_id}", json=edited, headers=auth_headers("teacher-a")
    )
    assert updated.status_code == 200, updated.text
    assert _tags_of(client, question_id) == ["text-tools"]


def test_question_without_tags_reports_an_empty_list(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201
    assert _tags_of(client, created.json()["question_id"]) == []


@pytest.mark.parametrize(
    "tags",
    [
        ["Text-Tools"],          # uppercase is not the declared form
        ["text tools"],          # a space is not a separator
        ["text_tools"],          # an underscore is not the declared separator
        ["-leading-hyphen"],     # must start with a letter or digit
        [""],                    # empty tag
        ["a" * 41],              # longer than the contract's maxLength
        ["dup", "dup"],          # uniqueItems is declared
        ["tag"] * 21,            # maxItems is declared
        "text-tools",            # an array, not a string
    ],
)
def test_invalid_tags_are_rejected_not_rewritten(client, auth_headers, tags):
    payload = draft_payload()
    payload["tags"] = tags
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["status"] == 422


def test_tag_validation_is_exactly_the_declared_pattern(client, auth_headers):
    """The rule is the contract's pattern and count - no extra B3 opinion.

    Recorded because "normalise to the declared form" is easy to over-read: B3 does not
    decide that a trailing hyphen is untidy, that two tags are synonyms, or that a
    Chinese topic name is wrong. Only what the contract declares is enforced.
    """
    payload = draft_payload()
    payload["tags"] = ["trailing-", "进程调度"]
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422  # non-ASCII is outside the declared pattern

    payload["tags"] = ["trailing-"]
    accepted = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=auth_headers("teacher-a"),
    )
    assert accepted.status_code == 201
    assert accepted.json()["tags"] == ["trailing-"]


# --- authoring assistance (CHG-0011, RQ-T-10) ---------------------------


def _created_question(client, auth_headers) -> str:
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=REFERENCE_ANSWER),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    return created.json()["question_id"]


def test_authoring_assistance_is_off_by_default_and_names_the_missing_configuration(
    client, auth_headers
):
    """ADR-0009 decision 4: off unless a deployment turns it on."""
    question_id = _created_question(client, auth_headers)
    response = client.post(
        f"/api/v1/questions/{question_id}/authoring-assistance",
        json={"topics": ["quality"]},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"].endswith("/authoring-assistance-unavailable")
    assert "B3_AUTHORING_PROVIDER_URL" in problem["detail"]
    assert "B3_AUTHORING_MODEL" in problem["detail"]


def test_authoring_assistance_requires_a_write_capable_role(client, auth_headers):
    question_id = _created_question(client, auth_headers)
    body = {"topics": ["comparison_policy"]}
    assert (
        client.post(
            f"/api/v1/questions/{question_id}/authoring-assistance",
            json=body,
            headers=auth_headers("student-a"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/questions/{question_id}/authoring-assistance", json=body
        ).status_code
        == 401
    )


def test_authoring_assistance_topic_enum_is_closed(client, auth_headers):
    question_id = _created_question(client, auth_headers)
    for topics in ([], ["quality", "quality"], ["anything-else"], ["quality"] * 7):
        response = client.post(
            f"/api/v1/questions/{question_id}/authoring-assistance",
            json={"topics": topics},
            headers=auth_headers("teacher-a"),
        )
        assert response.status_code in (409, 422), (topics, response.text)


def test_authoring_assistance_writes_nothing(client, auth_headers):
    """CHG-0011 verification 4: asking for advice must not change the question."""
    question_id = _created_question(client, auth_headers)
    before = client.get(
        f"/api/v1/question-versions/{_first_version(client, auth_headers, question_id)}",
        headers=auth_headers("teacher-a"),
    ).json()
    client.post(
        f"/api/v1/questions/{question_id}/authoring-assistance",
        json={"topics": ["quality", "wording"]},
        headers=auth_headers("teacher-a"),
    )
    after = client.get(
        f"/api/v1/question-versions/{_first_version(client, auth_headers, question_id)}",
        headers=auth_headers("teacher-a"),
    ).json()
    assert before == after


def test_authoring_payload_carries_no_protected_content_and_no_content_change(
    client, auth_headers
):
    """The outgoing view is public stem plus case *shape*, and nothing else."""
    from autograder_b3.assistance import Suggestion, assert_no_protected_keys
    from autograder_b3.domain.assistance import authoring_assistance
    from autograder_b3.dto.internal import PROTECTED_FIELD_NAMES

    question_id = _created_question(client, auth_headers)
    seen: dict = {}

    class RecordingProvider:
        name = "test-provider"
        model = "test-model"

        def advise(self, *, payload, topics, notes):
            seen["payload"] = payload
            seen["topics"] = topics
            return tuple(
                Suggestion(topic=topic, message=f"advisory about {topic}") for topic in topics
            )

    dependencies = client.app.state.b3_dependencies
    settings = type(dependencies.settings)(
        database_url=dependencies.settings.database_url,
        protected_database_url=dependencies.settings.protected_database_url,
        service_token=dependencies.settings.service_token,
        jwks_url=None,
        consumer_name="b3-authoring-test",
        authoring_provider_url="http://provider.invalid/v1/chat/completions",
        authoring_model="test-model",
    )
    from autograder_b3.auth import Principal

    result = authoring_assistance(
        dependencies.database,
        settings,
        principal=Principal(
            subject="teacher", roles=frozenset({"teacher"}), course_ids=frozenset({COURSE_A})
        ),
        question_id=uuid.UUID(question_id),
        topics=("quality", "comparison_policy"),
        notes=None,
        provider=RecordingProvider(),
    )
    payload = seen["payload"]
    assert set(payload) == {
        "title",
        "stem_markdown",
        "language",
        "adapter_kind",
        "comparison",
        "case_count",
        "case_ordinals",
        "case_weights",
        "max_files",
        "max_total_bytes",
        "topics",
        "notes",
    }
    # Structural, not a promise: the vocabulary check runs on the way out.
    assert_no_protected_keys(payload)
    assert not (set(payload) & PROTECTED_FIELD_NAMES)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert REFERENCE_ANSWER not in serialized
    assert HIDDEN_STDOUT not in serialized
    assert HIDDEN_CASE_NAME not in serialized
    assert payload["case_count"] == 2

    assert [item.topic for item in result.suggestions] == ["quality", "comparison_policy"]
    assert result.provenance is not None
    assert result.provenance.model == "test-model"
    assert result.disclaimers and all(item for item in result.disclaimers)

    dto = authoring_assistance_to_public(uuid.UUID(question_id), result)
    wire = serialize_public(dto)
    assert set(wire) == {
        "question_id",
        "status",
        "suggestions",
        "provenance",
        "disclaimers",
    }
    assert wire["status"] == "ready"
    assert wire["suggestions"][0]["confidence"] == "advisory"
    assert not (collect_keys(wire) & PROTECTED_KEYS)


@pytest.mark.parametrize(
    "response",
    [
        {"suggestions": [{"topic": "grades", "message": "try harder"}]},
        {"suggestions": [{"topic": "quality", "message": "reference_answer looks short"}]},
        {"suggestions": [{"topic": "quality", "message": "line one\nline two"}]},
        {"suggestions": [{"topic": "quality", "message": "   "}]},
        {"suggestions": "not a list"},
        {"nothing": True},
    ],
)
def test_a_provider_response_outside_the_declared_shape_is_refused(response):
    """A widened enum or an echoed block is refused, not relayed as "advisory"."""
    from autograder_b3.assistance import (
        AuthoringProviderError,
        parse_suggestions,
    )

    with pytest.raises(AuthoringProviderError):
        parse_suggestions(response, topics=("quality", "comparison_policy"))


def test_the_prompt_surface_is_enumerable():
    from autograder_b3.assistance import TOPICS, build_prompt

    prompt = build_prompt(payload={"title": "t", "stem_markdown": "s"}, topics=TOPICS)
    for topic in TOPICS:
        assert f"[{topic}]" in prompt
    assert "do not ask for them" in prompt


# --- publishing through HTTP --------------------------------------------


def test_draft_edit_then_publish_then_conflict(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]

    edited = client.patch(
        f"/api/v1/questions/{question_id}",
        json=draft_payload(stem="An edited stem while still a draft."),
        headers=auth_headers("teacher-a"),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["stem_markdown"].startswith("An edited stem")

    version_id = _first_version(client, auth_headers, question_id)
    published = http_publish(client, version_id)
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    conflict = client.patch(
        f"/api/v1/questions/{question_id}",
        json=draft_payload(stem="trying to edit a frozen version"),
        headers=auth_headers("teacher-a"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["type"].endswith("/immutable-question-version")

    branched = client.patch(
        f"/api/v1/questions/{question_id}",
        params={"as_new_version": "true"},
        json=draft_payload(stem="Version two content."),
        headers=auth_headers("teacher-a"),
    )
    assert branched.status_code == 200, branched.text

    original = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    )
    assert original.json()["status"] == "published"
    assert original.json()["content_digest"] == published.json()["content_digest"]


def test_publish_a_draft_without_a_reference_answer_is_422(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=None),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]
    version_id = _first_version(client, auth_headers, question_id)
    response = client.post(
        f"/api/v1/question-versions/{version_id}/publish", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/protected-content-missing")


def test_archive_then_publish_is_conflict(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]
    version_id = _first_version(client, auth_headers, question_id)
    assert http_publish(client, version_id).status_code == 200
    archived = client.post(
        f"/api/v1/question-versions/{version_id}/archive", headers=auth_headers("teacher-a")
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    again = client.post(
        f"/api/v1/question-versions/{version_id}/archive", headers=auth_headers("teacher-a")
    )
    assert again.status_code == 409


# --- internal package endpoint ------------------------------------------


def test_evaluation_package_requires_service_credentials(client, auth_headers, published_question):
    _question, version = published_question
    for token in ("teacher-a", "student-a", "assistant-a"):
        response = client.get(
            f"/api/v1/question-versions/{version['question_version_id']}/evaluation-package",
            headers=auth_headers(token),
        )
        assert response.status_code == 403, token
    assert (
        client.get(
            f"/api/v1/question-versions/{version['question_version_id']}/evaluation-package"
        ).status_code
        == 401
    )


def test_evaluation_package_is_served_to_services_and_carries_hidden_cases(
    client, service_headers, published_question
):
    _question, version = published_question
    response = client.get(
        f"/api/v1/question-versions/{version['question_version_id']}/evaluation-package",
        headers=service_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["package_digest"] == version["content_digest"] or len(body["package_digest"]) == 64
    assert body["adapter"]["kind"] == "stdin"
    hidden = [case for case in body["tests"] if case["visible_to_student"] is False]
    assert hidden and hidden[0]["expected_stdout"] == HIDDEN_STDOUT
    # The reference answer is NOT part of a package: the frozen schema has no field
    # for it, and acceptance 3-10-2 fails the response if it leaks.
    assert "reference_answer" not in body
    assert REFERENCE_ANSWER not in response.text


def test_evaluation_package_for_a_draft_is_served(client, service_headers, auth_headers):
    """CHG-0028 §9.1: a draft gets its package, because the publish gate needs it first.

    Before `CHG-0028` this was a `409` and the whole publish path was impossible: `publish`
    requires an attestation, an attestation is accepted only for a draft, and the executable
    inputs were only reachable after publication -- two rules that were individually right and
    jointly impossible. The package is now a snapshot of the draft's *current* content.
    """
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]
    version_id = _first_version(client, auth_headers, question_id)

    response = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package", headers=service_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question_version_id"] == version_id
    assert body["adapter"]["kind"] == "stdin"
    assert len(body["package_digest"]) == 64
    assert [case["ordinal"] for case in body["tests"]] == [0, 1]
    # Still protected content, still service-only: the refusal is not relaxed by serving drafts.
    assert (
        client.get(
            f"/api/v1/question-versions/{version_id}/evaluation-package",
            headers=auth_headers("teacher-a"),
        ).status_code
        == 403
    )


def test_a_draft_package_reports_the_content_timestamp_and_is_stable(
    client, service_headers, auth_headers, owner_engine
):
    """CHG-0028 §9.2: `created_at` is the content's timestamp, not `now()`.

    Two assertions, and each catches a different wrong implementation:

    * moving the **application clock** without touching the content must **not** move
      `created_at` -- a `now()` implementation would move it (the test clock is injected, so
      this is exact rather than timing-dependent);
    * editing the content **must** move it -- a hard-coded or creation-time constant would not.

    The value is also compared exactly with the version row's own content timestamp, so
    "the content's timestamp" is pinned to the field it means rather than to any monotone
    value.
    """
    from sqlalchemy import select

    from autograder_b3.infra.models import QuestionVersion

    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]
    version_id = _first_version(client, auth_headers, question_id)

    def fetch() -> dict:
        response = client.get(
            f"/api/v1/question-versions/{version_id}/evaluation-package",
            headers=service_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    def stored_timestamp() -> datetime:
        with owner_engine.connect() as connection:
            return connection.execute(
                select(QuestionVersion.updated_at).where(
                    QuestionVersion.question_version_id == uuid.UUID(version_id)
                )
            ).scalar_one()

    first = fetch()
    assert datetime.fromisoformat(first["created_at"].replace("Z", "+00:00")) == (
        stored_timestamp()
    )
    assert first["language"] == "python"

    # Time passes for the application but the content does not move.
    client.app.state.b3_dependencies.clock.advance(90)
    unchanged = fetch()
    assert unchanged["created_at"] == first["created_at"]
    assert unchanged["package_digest"] == first["package_digest"]

    edited = client.patch(
        f"/api/v1/questions/{question_id}",
        json=draft_payload(stem="A moved stem."),
        headers=auth_headers("teacher-a"),
    )
    assert edited.status_code == 200, edited.text
    moved = fetch()
    assert datetime.fromisoformat(moved["created_at"].replace("Z", "+00:00")) == (
        stored_timestamp()
    )
    assert moved["created_at"] != first["created_at"]
    assert moved["package_digest"] != first["package_digest"]


def test_a_draft_can_be_verified_and_published_without_publishing_it_first(
    client, auth_headers, service_headers
):
    """CHG-0028 §9.5: the two inputs a verifier needs, then attest, then publish.

    This is the flow `CHG-0019` exists for and which `CHG-0028` made possible: with the
    publish gate requiring an attestation and an attestation accepted only for a draft, the
    executable inputs had to become reachable while the version is still a draft. The two
    inputs are fetched here exactly as B2 fetches them (package + reference answer, both on the
    service token), and the publish that follows is the one the teacher was previously unable
    to reach.

    What is **not** executed here is the reference answer itself: that needs the Linux sandbox
    B2 owns and this environment does not have. So the step "run the answer against the cases"
    is represented by recording the attestation, which is the only thing B3 can observe.
    """
    question_id, version_id = _draft_with_answer(
        client, auth_headers, reference_answer=REFERENCE_ANSWER
    )

    package = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package", headers=service_headers
    )
    assert package.status_code == 200, package.text
    assert package.json()["question_version_id"] == version_id

    answer = client.get(_reference_answer_path(version_id), headers=service_headers)
    assert answer.status_code == 200, answer.text
    assert answer.json()["reference_answer"] == REFERENCE_ANSWER
    # The two deliveries describe the same content: the digest the verifier attests is the one
    # the package was compiled from.
    assert answer.json()["content_digest"] == client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    ).json()["content_digest"]

    attestation = http_attestation(client, version_id, cases=2)
    assert attestation["content_digest"] == answer.json()["content_digest"]

    published = client.post(
        f"/api/v1/question-versions/{version_id}/publish", headers=auth_headers("teacher-a")
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    # And the published version still serves the same package: the draft path did not become
    # a second code path with a different output.
    after = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package", headers=service_headers
    )
    assert after.status_code == 200, after.text
    assert after.json()["package_digest"] == package.json()["package_digest"]


def test_a_version_with_no_test_cases_has_no_compilable_content(
    client, service_headers, auth_headers
):
    """CHG-0028 §9.4: the new, and now only, meaning of `409` on this operation.

    `cases` became optional in `CHG-0017`, so a draft with no test cases is reachable. It has
    nothing to execute, which is what `409` says here now -- not "this is a draft".
    """
    body = draft_payload()
    del body["cases"]
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=body,
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    version_id = _first_version(client, auth_headers, created.json()["question_id"])

    response = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package", headers=service_headers
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["type"].endswith("/package-content-missing")
    assert "no compilable content" in body["detail"]


def test_the_published_path_still_compares_the_recorded_package_digest(
    client, service_headers, auth_headers, owner_engine, published_question
):
    """CHG-0028 §9.3, the most important one: serving drafts did not drop the old check.

    A published version's package is still compared against the digest recorded at publish
    time. The comparison is exercised the only way it can be: by making the stored content
    differ from what was recorded. The guards are disabled for exactly one statement (the
    immutability triggers would refuse the change, which is itself the other half of the
    protection) and re-enabled immediately.
    """
    from sqlalchemy import select, text

    from autograder_b3.infra.models import QuestionVersion

    _question, version = published_question
    version_id = version["question_version_id"]

    with owner_engine.connect() as connection:
        recorded = connection.execute(
            select(QuestionVersion.package_digest).where(
                QuestionVersion.question_version_id == uuid.UUID(version_id)
            )
        ).scalar_one()
    assert recorded is not None

    served = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package", headers=service_headers
    )
    assert served.status_code == 200, served.text
    # The published path returns exactly the recorded artifact, which is why comparing the
    # two means anything.
    assert served.json()["package_digest"] == recorded

    with owner_engine.connect() as connection:
        connection.execute(
            text("ALTER TABLE b3.test_cases DISABLE TRIGGER trg_test_cases_guard")
        )
        try:
            changed = connection.execute(
                text(
                    "UPDATE b3.test_cases SET expected_stdout = 'tampered' "
                    "WHERE question_version_id = :vid AND ordinal = 0"
                ),
                {"vid": version_id},
            )
            assert changed.rowcount == 1
        finally:
            connection.execute(
                text("ALTER TABLE b3.test_cases ENABLE TRIGGER trg_test_cases_guard")
            )

    tampered = client.get(
        f"/api/v1/question-versions/{version_id}/evaluation-package", headers=service_headers
    )
    assert tampered.status_code == 409, tampered.text
    assert tampered.json()["detail"].startswith("the compiled package no longer matches")


# --- CHG-0023: the reference-answer channel ------------------------------
#
# `CHG-0019` asks B2 to execute a version's reference answer, and until this endpoint existed
# nothing in the contract carried the answer to B2: the package deliberately has no such
# field, and no public DTO may echo it. This is the delivery path, and the tests below are
# `CHG-0023` §8's seven verification items plus one state the CHG did not name.


def _reference_answer_path(question_version_id) -> str:
    return f"/api/v1/question-versions/{question_version_id}/reference-answer"


def _declared_reference_answer_keys() -> set[str]:
    """Read ``ReferenceAnswer``'s declared key set out of the contract itself.

    Same reason as the coverage and revision-diff key assertions: comparing the response
    against a constant in this repository proves it is self-consistent, not that it matches
    what a client was promised.
    """
    contract = (B3_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b3.yaml").read_text(
        encoding="utf-8"
    )
    section = contract.split("\n    ReferenceAnswer:\n", 1)[1].split(
        "\n    AdapterManifest:\n", 1
    )[0]
    properties = section.split("      properties:\n", 1)[1]
    return set(re.findall(r"^ {8}([a-z0-9_]+):", properties, flags=re.MULTILINE))


def _draft_with_answer(client, auth_headers, **payload_kwargs) -> tuple[str, str]:
    """Create a draft that carries a reference answer; return ``(question_id, version_id)``."""
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(**payload_kwargs),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]
    return question_id, _first_version(client, auth_headers, question_id)


def test_reference_answer_is_served_to_services_for_the_declared_language(
    client, auth_headers, service_headers
):
    """CHG-0023 §8.1 and §8.6: the answer for the *declared* language, and five keys only.

    The fixture stores two answers on one version - a python one and a bash one - and then
    leaves the version declaring bash. With a single answer, "return the answer stored for
    the declared language" and "return the only row there is" would look identical, and the
    second one would hand B2 something the version does not declare.
    """
    question_id, version_id = _draft_with_answer(
        client, auth_headers, language="python", reference_answer=PYTHON_ANSWER
    )
    switched = client.patch(
        f"/api/v1/questions/{question_id}",
        json=draft_payload(language="bash", reference_answer=REFERENCE_ANSWER),
        headers=auth_headers("teacher-a"),
    )
    assert switched.status_code == 200, switched.text

    declared = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    ).json()
    assert declared["language"] == "bash"

    response = client.get(_reference_answer_path(version_id), headers=service_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _declared_reference_answer_keys()
    assert body["reference_answer"] == REFERENCE_ANSWER
    assert PYTHON_ANSWER not in response.text
    assert body["language"] == "bash" == declared["language"]
    assert body["question_version_id"] == version_id
    assert body["adapter_kind"] == declared["adapter_kind"] == "stdin"
    assert re.fullmatch(r"[0-9a-f]{64}", body["content_digest"])
    # Nothing else travels with it: the hidden cases and the fixtures already have their own
    # channels, and this endpoint exists to add one input rather than to become a firehose.
    for absent in (
        "tests",
        "cases",
        "test_cases",
        "fixtures",
        "expected_stdout",
        "expected_stderr",
        "expected_exit_code",
        "package_digest",
        "stdin",
    ):
        assert absent not in body, absent
    assert HIDDEN_CASE_NAME not in response.text
    assert HIDDEN_STDOUT not in response.text


def test_reference_answer_is_never_readable_by_a_user_token(
    client, auth_headers, service_headers, published_question
):
    """CHG-0023 §8.2, the core assertion: teachers and students must not read it at all.

    A teacher meets the reference answer on the authoring write path, never here, and a
    student meets it nowhere. The refusal is checked for a token that *does* have scope on
    the question as well as for foreign ones, and the body is searched for the answer so a
    `403` that leaked content would still fail.
    """
    _question, version = published_question
    path = _reference_answer_path(version["question_version_id"])

    for token in ("teacher-a", "assistant-a", "student-a", "teacher-b"):
        refused = client.get(path, headers=auth_headers(token))
        assert refused.status_code == 403, token
        assert refused.json()["type"].endswith("/forbidden")
        assert REFERENCE_ANSWER not in refused.text, token

    assert client.get(path).status_code == 401
    assert (
        client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401
    )

    # The same request with the service token succeeds, so the refusals above are the
    # authorization rule rather than a broken endpoint.
    served = client.get(path, headers=service_headers)
    assert served.status_code == 200, served.text
    assert served.json()["reference_answer"] == REFERENCE_ANSWER


def test_a_version_without_a_reference_answer_is_409_not_404(
    client, auth_headers, service_headers
):
    """CHG-0023 §8.3 / CHG-0026 §8.1: the version exists; its *state* makes verification
    impossible.

    `404` would tell the caller the version does not exist, which is false, and `422` would
    blame a request that was perfectly well formed. The version's own detail endpoint is read
    first, to show the resource really is there.
    """
    _question_id, version_id = _draft_with_answer(client, auth_headers, reference_answer=None)

    exists = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    )
    assert exists.status_code == 200

    response = client.get(_reference_answer_path(version_id), headers=service_headers)
    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"].endswith("/reference-answer-missing")
    assert set(body) == {"type", "title", "status", "detail", "instance"}
    detail = body["detail"]
    assert "reference answer" in detail and "python" in detail
    # CHG-0026 §8.2: the two ways a version can be unverifiable have to be distinguishable
    # from the detail alone, so this one must not read like the "no content digest" case.
    # `test_a_version_with_no_test_cases_cannot_supply_its_answer` asserts the mirror image.
    assert "digest" not in detail
    assert "test case" not in detail


def test_an_unknown_version_has_no_reference_answer_to_serve(client, service_headers):
    """CHG-0023 §8.4: `404` and `409` stay distinguishable in both directions."""
    response = client.get(
        _reference_answer_path(uuid.uuid4()), headers=service_headers
    )
    assert response.status_code == 404, response.text
    assert response.json()["type"].endswith("/not-found")


def test_a_version_with_no_test_cases_cannot_supply_its_answer(
    client, auth_headers, service_headers
):
    """The second way a version can be unverifiable, kept at `409` by `CHG-0026` §2.

    `cases` became optional (`CHG-0017`), so a draft can carry a reference answer and no test
    cases. It then has no `content_digest` - there is nothing to hash - and the digest is part
    of this response precisely because the answer that gets executed and the content that
    gets attested have to be the same content. Returning an answer with no digest would let
    B2 attest a value that cannot exist, and returning `200` would only push the failure to
    the package fetch or the attestation, where it is harder to attribute. So `409` stays,
    with a detail that names *this* missing piece (`CHG-0026` §8.2: the two details must be
    distinguishable, which is what the mirror assertions below pin down).
    """
    body = draft_payload(reference_answer=REFERENCE_ANSWER)
    del body["cases"]
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=body,
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    version_id = _first_version(client, auth_headers, created.json()["question_id"])

    response = client.get(_reference_answer_path(version_id), headers=service_headers)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    # The missing piece is named: the digest. The message deliberately does *not* claim a cause
    # for it -- "a version needs at least one test case first" used to be the ending, and the
    # integration bank holds drafts that have cases and an answer with a NULL digest, so that
    # sentence was false on every row this state can actually fire on.
    assert "digest" in detail
    assert "test case" not in detail
    # The mirror of the assertion in `test_a_version_without_a_reference_answer_is_409_not_404`:
    # this message must not be mistakable for "there is no reference answer at all".
    assert "no reference answer" not in detail


def test_the_reference_answer_digest_is_the_one_the_attestation_binds(
    client, auth_headers, service_headers
):
    """CHG-0023 §8.5: one digest, byte for byte, across the answer and the attestation.

    This is what makes "the answer that was executed" and "the content that was attested" the
    same content instead of two numbers that happen to agree. It is checked before and after
    publication, because B2's real order is verify, attest, publish - and the value has to
    survive the freeze.
    """
    _question_id, version_id = _draft_with_answer(
        client, auth_headers, reference_answer=REFERENCE_ANSWER
    )

    shown = client.get(
        f"/api/v1/question-versions/{version_id}", headers=auth_headers("teacher-a")
    ).json()
    attestation = http_attestation(client, version_id, cases=2)
    served = client.get(_reference_answer_path(version_id), headers=service_headers).json()

    assert served["content_digest"] == attestation["content_digest"] == shown["content_digest"]

    published = client.post(
        f"/api/v1/question-versions/{version_id}/publish", headers=auth_headers("teacher-a")
    )
    assert published.status_code == 200, published.text
    after = client.get(_reference_answer_path(version_id), headers=service_headers).json()
    assert after["content_digest"] == served["content_digest"]
    assert after["reference_answer"] == REFERENCE_ANSWER
    assert after["language"] == served["language"]


# --- assignments ---------------------------------------------------------


def test_assignment_create_and_list(client, auth_headers, published_question):
    _question, version = published_question
    response = client.post(
        "/api/v1/assignments",
        json={
            "course_id": str(COURSE_A),
            "section_id": "33333333-3333-4333-8333-333333333333",
            "title": "Week 1",
            "question_version_ids": [version["question_version_id"]],
        },
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["question_version_ids"] == [version["question_version_id"]]
    assert collect_keys(body) & PROTECTED_KEYS == set()

    listing = client.get(
        "/api/v1/assignments", params={"course_id": str(COURSE_A)}, headers=auth_headers("teacher-a")
    )
    assert listing.status_code == 200
    assert any(item["assignment_id"] == body["assignment_id"] for item in listing.json()["items"])


def test_assignment_with_a_draft_version_is_conflict(client, auth_headers):
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(),
        headers=auth_headers("teacher-a"),
    )
    question_id = created.json()["question_id"]
    version_id = _first_version(client, auth_headers, question_id)
    response = client.post(
        "/api/v1/assignments",
        json={
            "course_id": str(COURSE_A),
            "section_id": "33333333-3333-4333-8333-333333333333",
            "title": "Draft reference",
            "question_version_ids": [version_id],
        },
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 409


def test_assignment_detail_reads_one_assignment_by_id(client, auth_headers, published_question):
    _question, version = published_question
    created = client.post(
        "/api/v1/assignments",
        json={
            "course_id": str(COURSE_A),
            "section_id": "33333333-3333-4333-8333-333333333333",
            "title": "Detail read",
            "question_version_ids": [version["question_version_id"]],
        },
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    detail = client.get(f"/api/v1/assignments/{assignment_id}", headers=auth_headers("teacher-a"))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["assignment_id"] == assignment_id
    assert body["course_id"] == str(COURSE_A)
    assert body["question_version_ids"] == [version["question_version_id"]]
    assert collect_keys(body) & PROTECTED_KEYS == set()


def test_assignment_detail_outside_the_course_scope_is_forbidden(
    client, auth_headers, published_question
):
    _question, version = published_question
    created = client.post(
        "/api/v1/assignments",
        json={
            "course_id": str(COURSE_A),
            "section_id": "33333333-3333-4333-8333-333333333333",
            "title": "Out of scope",
            "question_version_ids": [version["question_version_id"]],
        },
        headers=auth_headers("teacher-a"),
    )
    assignment_id = created.json()["assignment_id"]

    # teacher-b only holds COURSE_B: the record exists, the caller may not read it.
    denied = client.get(f"/api/v1/assignments/{assignment_id}", headers=auth_headers("teacher-b"))
    assert denied.status_code == 403
    assert denied.headers["content-type"].startswith("application/problem+json")


def test_assignment_detail_for_an_unknown_id_is_not_found(client, auth_headers):
    response = client.get(
        f"/api/v1/assignments/{uuid.uuid4()}", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 404


# --- validation ----------------------------------------------------------


def test_invalid_body_is_422_problem_details(client, auth_headers):
    response = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json={"title": "", "stem_markdown": "", "language": "java", "adapter": {"kind": "stdin"}},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert set(response.json()) == {"type", "title", "status", "detail", "instance"}


def test_list_questions_rejects_an_unknown_language(client, auth_headers):
    response = client.get(
        "/api/v1/questions", params={"language": "java"}, headers=auth_headers("teacher-a")
    )
    assert response.status_code == 422


def test_cursor_pagination_is_keyset(client, auth_headers):
    for index in range(3):
        client.post(
            "/api/v1/questions",
            params={"course_id": str(COURSE_A)},
            json=draft_payload(title=f"paged {index}"),
            headers=auth_headers("teacher-a"),
        )
    first = client.get(
        "/api/v1/questions", params={"limit": 2}, headers=auth_headers("teacher-a")
    )
    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"]

    second = client.get(
        "/api/v1/questions",
        params={"limit": 2, "cursor": body["next_cursor"]},
        headers=auth_headers("teacher-a"),
    )
    assert second.status_code == 200
    overlap = {item["question_id"] for item in second.json()["items"]} & {
        item["question_id"] for item in body["items"]
    }
    assert overlap == set()

    bad = client.get("/api/v1/questions", params={"cursor": "!!!"}, headers=auth_headers("teacher-a"))
    assert bad.status_code == 422


def test_request_id_is_echoed(client, auth_headers):
    response = client.get(
        "/api/v1/questions",
        headers={**auth_headers("teacher-a"), "X-Request-ID": "abc-123"},
    )
    assert response.headers["x-request-id"] == "abc-123"


def test_a_freshly_created_draft_is_invisible_to_the_default_list(client, auth_headers):
    """The container sequence, pinned: POST -> 201, detail -> 200, default list -> [].

    Reported as a suspected defect ("a list endpoint omits the row its own POST just
    created"), reproduced here, and traced to the contract's own default rather than to
    the CHG-0013 ``tags`` parameter:

    * the row really is stored, and the *accessible* way to see it (``published_only=false``,
      which is also what a student's query resolves to) returns it;
    * ``GET /api/v1/questions?status=draft`` is **not** a valid request shape -- there is no
      ``status`` parameter on this operation, so FastAPI ignores the unknown key and the
      caller silently receives the default set (empty), which is exactly how the report's
      "same with status=draft" observation arises;
    * the detail path is role-aware (staff may read a draft, a student may not), while this
      list default is not.

    The assertion below therefore records the *current, contract-declared* behaviour rather
    than blessing it: it fails if someone changes the default silently in either direction,
    and :func:`test_the_draft_list_default_is_the_contract_default` states the authoring
    hazard explicitly so a future change has to be a deliberate contract decision.
    """
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(title=f"fresh draft {uuid.uuid4()}"),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]

    detail = client.get(f"/api/v1/questions/{question_id}", headers=auth_headers("teacher-a"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "draft"

    def listed(**params) -> set[str]:
        response = client.get(
            "/api/v1/questions", params=params, headers=auth_headers("teacher-a")
        )
        assert response.status_code == 200, response.text
        return {item["question_id"] for item in response.json()["items"]}

    # The default hides it: `published_only` defaults to true (b3.yaml, listQuestions).
    assert question_id not in listed()
    assert question_id not in listed(course_id=str(COURSE_A))
    # `status` is not a declared parameter here, so it is ignored rather than honoured --
    # the same empty page, which is what made the original report look inexplicable.
    assert question_id not in listed(status="draft")
    # The row is there and reachable through the parameter that the contract does declare.
    assert question_id in listed(published_only="false")
    assert question_id in listed(course_id=str(COURSE_A), published_only="false")


def test_the_draft_list_default_is_the_contract_default(client, auth_headers):
    """Records *why* a teacher's own new draft is missing from the list they open first.

    Not a bug in B3: ``published_only: {default: true}`` is declared by the contract
    (``CHG-0013`` did not touch it; the ``tags`` filter is orthogonal and was ruled out by
    :func:`test_a_freshly_created_draft_is_invisible_to_the_default_list`). It is, however,
    a silent-wrong-answer shape worth stating plainly: a teacher who creates a question and
    then lists the bank sees an empty (or filtered) page, and the response carries no signal
    that a default excluded their own draft -- only ``published_only=false`` reveals it.

    If that is judged wrong for the authoring flow, the fix belongs in the contract (either
    the default, or a role-aware rule mirroring the detail path), not in a silent change
    here. Recorded in ``docs/api/contract.md``.
    """
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(title=f"draft for the default question {uuid.uuid4()}"),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]

    # What a student sees and what a teacher sees with no parameters are the same set:
    # both resolve to published-only (`list_questions` forces it for non-staff).
    staff_default = client.get("/api/v1/questions", headers=auth_headers("teacher-a")).json()
    student_default = client.get("/api/v1/questions", headers=auth_headers("student-a")).json()
    assert {item["question_id"] for item in staff_default["items"]} == {
        item["question_id"] for item in student_default["items"]
    }
    assert question_id not in {item["question_id"] for item in staff_default["items"]}

    # A student, unlike staff, cannot reach the draft through the parameter either.
    student_drafts = client.get(
        "/api/v1/questions",
        params={"published_only": "false"},
        headers=auth_headers("student-a"),
    )
    assert student_drafts.status_code == 200
    assert question_id not in {item["question_id"] for item in student_drafts.json()["items"]}
    # ...while the detail path applies the role rule and refuses (403, not a silent 404).
    refused = client.get(
        f"/api/v1/questions/{question_id}", headers=auth_headers("student-a")
    )
    assert refused.status_code == 403


# --- CHG-0013: the quality surfaces --------------------------------------
#
# Three capabilities existed and were tested but had no usable surface; a teacher could
# not reach any of them. These tests are about the *surface*: the contract path exists,
# the response shape is the declared one, and the semantics are the declared ones.


def _published_question_with(
    client,
    auth_headers,
    *,
    tags: list[str] | None = None,
    cases: list[dict] | None = None,
    comparison: dict | None = None,
) -> str:
    payload = draft_payload(
        reference_answer=REFERENCE_ANSWER, cases=cases, comparison=comparison
    )
    if tags is not None:
        payload["tags"] = tags
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=payload,
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    question_id = created.json()["question_id"]
    # The attestation case count has to describe this version, and the writer knows it.
    publish = http_publish(
        client,
        _first_version(client, auth_headers, question_id),
        cases=len(payload["cases"]),
    )
    assert publish.status_code == 200, publish.text
    return question_id


def test_diagnostics_are_reachable_and_carry_no_expected_output(client, auth_headers):
    """The rules existed with no read path; this is the read path.

    The clean expected output is deliberately reported: the response must not be a
    paraphrase of the hidden cases it was computed from.
    """
    question_id = _published_question_with(client, auth_headers)
    response = client.get(
        f"/api/v1/questions/{question_id}/diagnostics", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"question_id", "findings"}
    assert body["question_id"] == question_id
    assert all(
        set(item) == {"rule_id", "severity", "message", "ordinal"} for item in body["findings"]
    )
    # Nothing that exists only inside the protected store may appear. (A bare "0" is not a
    # usable probe - every UUID contains one - so the probe is the whole case name and the
    # reference answer, which is where a leak would actually show up.)
    assert HIDDEN_CASE_NAME not in response.text
    assert REFERENCE_ANSWER not in response.text


def test_diagnostics_report_the_content_rules_with_declared_severities(client, auth_headers):
    """CMP-001/002/003, over HTTP, with the severities the contract declares.

    The reference answer and the publish gate are irrelevant here - B3 executes nothing,
    and what is diagnosed is the *declaration*: an expected output with CRLF, one with
    non-ASCII text, one with trailing blanks under `exact`.
    """
    cases = [
        {
            "name": "crlf",
            "ordinal": 0,
            "weight": 1,
            "visible_to_student": False,
            "stdin": "a\n",
            "args": [],
            "expected_stdout": "3\r\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
        {
            "name": "non-ascii",
            "ordinal": 1,
            "weight": 1,
            "visible_to_student": False,
            "stdin": "a\n",
            "args": [],
            "expected_stdout": "café\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
        {
            "name": "trailing blanks",
            "ordinal": 2,
            "weight": 1,
            "visible_to_student": False,
            "stdin": "a\n",
            "args": [],
            "expected_stdout": "3   \n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
    ]
    question_id = _published_question_with(
        client,
        auth_headers,
        cases=cases,
        comparison={"stdout": "exact", "stderr": "ignored", "numeric_tolerance": None},
    )

    response = client.get(
        f"/api/v1/questions/{question_id}/diagnostics", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 200, response.text
    findings = response.json()["findings"]
    by_rule: dict[str, list[dict]] = {}
    for item in findings:
        by_rule.setdefault(item["rule_id"], []).append(item)

    assert set(by_rule) == {"CMP-001", "CMP-002", "CMP-003"}
    assert {item["severity"] for item in by_rule["CMP-001"]} == {"error"}
    assert {item["severity"] for item in by_rule["CMP-002"]} == {"warning"}
    assert {item["severity"] for item in by_rule["CMP-003"]} == {"info"}
    # Every severity is one the contract declares.
    assert {item["severity"] for item in findings} <= {"error", "warning", "info"}
    # Case-specific findings name the case by ordinal, never by name or content. `exact`
    # against an expected output that ends in a newline makes CMP-003 fire on every case
    # here, which is the rule behaving as declared rather than a test artefact.
    assert [item["ordinal"] for item in by_rule["CMP-001"]] == [0]
    assert [item["ordinal"] for item in by_rule["CMP-002"]] == [1]
    assert sorted(item["ordinal"] for item in by_rule["CMP-003"]) == [0, 1, 2]
    assert all(item["message"] for item in findings)
    assert HIDDEN_CASE_NAME not in response.text
    assert "café" not in response.text


def test_diagnostics_report_a_tolerance_that_was_never_declared(client, auth_headers):
    """CMP-004 is diagnosable on a draft, where the policy is still being written.

    The question is deliberately not published: the point of an authoring-time finding is
    that it arrives *before* the version is frozen, and a teacher can act on it. (This is
    also why the endpoint selects the latest draft when nothing is published.)
    """
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(
            reference_answer=REFERENCE_ANSWER,
            comparison={
                "stdout": "numeric_tolerance",
                "stderr": "ignored",
                "numeric_tolerance": None,
            },
        ),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text

    response = client.get(
        f"/api/v1/questions/{created.json()['question_id']}/diagnostics",
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["findings"] == [
        {
            "rule_id": "CMP-004",
            "severity": "error",
            "message": (
                "comparison.stdout is 'numeric_tolerance' but "
                "comparison.numeric_tolerance is not set, so no tolerance applies"
            ),
            "ordinal": None,
        }
    ]


def test_diagnostics_require_the_authoring_role_and_course_scope(
    client, auth_headers, published_question
):
    question, _version = published_question
    path = f"/api/v1/questions/{question['question_id']}/diagnostics"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth_headers("student-a")).status_code == 403
    assert client.get(path, headers=auth_headers("teacher-b")).status_code == 403
    assert client.get(path, headers=auth_headers("assistant-a")).status_code == 200
    assert (
        client.get(f"/api/v1/questions/{uuid.uuid4()}/diagnostics", headers=auth_headers("teacher-a"))
        .status_code
        == 404
    )


def test_an_empty_diagnostics_list_means_checked_and_clean(client, auth_headers):
    """The three-state discipline, asserted rather than asserted-about.

    ``findings: []`` is a *result*: the endpoint ran the rules over a version it read in
    the same request, so a report exists exactly when it was computed. It cannot be the
    fingerprint of "not checked" - a question with no version is ``404``, an unauthorized
    caller gets ``401``/``403``, and there is no cached or persisted report whose absence
    could masquerade as a clean bill of health.
    """
    question_id = _published_question_with(client, auth_headers)
    response = client.get(
        f"/api/v1/questions/{question_id}/diagnostics", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 200
    assert response.json() == {"question_id": question_id, "findings": []}

    # A question that was never published is still diagnosable (the teacher may be
    # authoring it), so "clean" is not an artefact of the publish step either.
    draft_question = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=REFERENCE_ANSWER),
        headers=auth_headers("teacher-a"),
    ).json()["question_id"]
    draft_reponse = client.get(
        f"/api/v1/questions/{draft_question}/diagnostics", headers=auth_headers("teacher-a")
    )
    assert draft_reponse.status_code == 200
    assert draft_reponse.json()["findings"] == []


def test_question_list_filters_by_tags_with_all_of_semantics(client, auth_headers):
    """CHG-0013: asking for two tags means *both*, so a coverage view can narrow.

    The assertion is set equality against the intersection of the two single-tag
    queries - "fewer rows" would also hold for an any-of filter that silently dropped
    one of the tags, or for a filter that matched nothing at all.
    """
    both = _published_question_with(client, auth_headers, tags=["processes", "paging"])
    only_processes = _published_question_with(client, auth_headers, tags=["processes"])
    only_paging = _published_question_with(client, auth_headers, tags=["paging"])
    neither = _published_question_with(client, auth_headers, tags=["text-tools"])

    def listed(**params) -> set[str]:
        response = client.get(
            "/api/v1/questions", params=params, headers=auth_headers("teacher-a")
        )
        assert response.status_code == 200, response.text
        return {item["question_id"] for item in response.json()["items"]}

    processes = listed(tags="processes")
    paging = listed(tags="paging")
    # The premise: neither single-tag set is a subset of the other, so an intersection,
    # a union and a plain "fewer rows" answer are three different sets here. Without this
    # the equality below could hold vacuously for a filter that ignores one tag.
    assert both in processes and both in paging
    assert only_processes in processes and only_processes not in paging
    assert only_paging in paging and only_paging not in processes
    assert processes != paging

    intersection = listed(tags=["processes", "paging"])
    assert intersection == processes & paging
    assert intersection == {both}
    assert neither not in intersection
    # All-of, stated in the other direction too: the multi-tag answer is strictly smaller
    # than either single-tag answer here, and is not their union.
    assert intersection < processes and intersection < paging
    assert intersection != processes | paging

    # A tag nobody carries selects nothing - an empty page, not an unfiltered one.
    assert listed(tags="nobody-has-this") == set()


@pytest.mark.parametrize(
    "tags",
    [
        ["Text-Tools"],   # uppercase is not the declared form
        ["text tools"],   # a space is not a separator
        [""],             # empty tag
        ["a" * 41],       # longer than the contract's maxLength
        ["dup", "dup"],   # uniqueItems is declared
        ["tag"] * 21,     # maxItems is declared
    ],
)
def test_invalid_query_tags_are_rejected_not_ignored(client, auth_headers, tags):
    """An unvalidated filter would return the unfiltered page and look like "no match"."""
    response = client.get(
        "/api/v1/questions", params={"tags": tags}, headers=auth_headers("teacher-a")
    )
    assert response.status_code == 422, response.text
    assert response.json()["status"] == 422


def test_revision_note_and_diff_round_trip_through_http(client, auth_headers):
    """The write path and the read path of the revision loop, over the wire."""
    question_id = _published_question_with(client, auth_headers)
    version_one = _first_version(client, auth_headers, question_id)

    first = client.get(
        f"/api/v1/question-versions/{version_one}", headers=auth_headers("teacher-a")
    ).json()
    assert first["revision_note"] is None
    assert first["revision_diff"] is None

    revised = client.patch(
        f"/api/v1/questions/{question_id}",
        params={"as_new_version": "true"},
        json=draft_payload(
            reference_answer=REFERENCE_ANSWER, stem="The deadline example was wrong."
        )
        | {"revision_note": "the deadline example was wrong"},
        headers=auth_headers("teacher-a"),
    )
    assert revised.status_code == 200, revised.text

    latest = client.get(
        f"/api/v1/questions/{question_id}", headers=auth_headers("teacher-a")
    ).json()["versions"][0]
    version_two = client.get(
        f"/api/v1/question-versions/{latest['question_version_id']}",
        headers=auth_headers("teacher-a"),
    ).json()
    assert version_two["revision_note"] == "the deadline example was wrong"
    diff = version_two["revision_diff"]
    assert diff is not None
    assert set(diff) == {
        "case_count_before",
        "case_count_after",
        "case_set_changed",
        "weights_changed",
        "fixtures_changed",
        "stem_length_before",
        "stem_length_after",
        "adapter_kind_changed",
        "size_limits_changed",
        "reference_answer_digest_before",
        "reference_answer_digest_after",
    }
    assert diff["stem_length_before"] != diff["stem_length_after"]
    assert diff["case_set_changed"] is False
    assert diff["case_count_before"] == diff["case_count_after"]
    # Digests, never the answer; and no protected vocabulary anywhere in the body.
    assert len(diff["reference_answer_digest_after"]) == 64
    assert REFERENCE_ANSWER not in json.dumps(version_two)
    assert collect_keys(version_two) & PROTECTED_KEYS == set()


def test_a_revision_note_without_as_new_version_is_refused(
    client, auth_headers, published_question
):
    """Honest refusal instead of accepting a field and dropping it.

    Two shapes, both ``422``: a note sent with no ``as_new_version`` (the write would
    record no version, so there is no reason to store), and a note sent when creating a
    question (the first version is not a revision).
    """
    question, _version = published_question
    without_flag = client.patch(
        f"/api/v1/questions/{question['question_id']}",
        json=draft_payload(reference_answer=REFERENCE_ANSWER)
        | {"revision_note": "changing my mind"},
        headers=auth_headers("teacher-a"),
    )
    assert without_flag.status_code == 422, without_flag.text
    assert without_flag.json()["type"].endswith("/revision-note-misuse")

    # An explicit `null` is still the caller sending the field, and is refused too: the
    # two are distinguishable on the wire and treating them alike would be a guess.
    explicit_null = client.patch(
        f"/api/v1/questions/{question['question_id']}",
        json=draft_payload(reference_answer=REFERENCE_ANSWER) | {"revision_note": None},
        headers=auth_headers("teacher-a"),
    )
    assert explicit_null.status_code == 422

    on_create = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(reference_answer=REFERENCE_ANSWER)
        | {"revision_note": "no version to explain"},
        headers=auth_headers("teacher-a"),
    )
    assert on_create.status_code == 422, on_create.text

    over_long = client.patch(
        f"/api/v1/questions/{question['question_id']}",
        params={"as_new_version": "true"},
        json=draft_payload(reference_answer=REFERENCE_ANSWER) | {"revision_note": "x" * 501},
        headers=auth_headers("teacher-a"),
    )
    assert over_long.status_code == 422


# --- CHG-0020: tag coverage (RQ-B-3-20) ----------------------------------
#
# `tags` could be written (CHG-0008) and searched (CHG-0013), but their *distribution* had
# no outlet: `CursorPage` carries no total, so "does this topic have two questions or
# twenty" could only be answered by walking the bank by hand. These tests are about the
# surface: the declared path exists and is not swallowed by the parameterised sibling, the
# response is the declared shape, and the declared reading of the numbers holds.


def _declared_coverage_keys() -> tuple[set[str], set[str]]:
    """Read ``QuestionTagCoverage``'s declared key sets out of the contract itself.

    Same reason as ``test_versions.declared_revision_diff_properties``: comparing the
    response against a constant in this repository proves it is self-consistent, not that
    it matches what a client was promised. The top-level keys sit at 8 spaces under
    ``properties:`` and the nested item's at 14, so the two sets come out separately; a
    key added on either side alone fails the comparison below.
    """
    contract = (B3_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b3.yaml").read_text(
        encoding="utf-8"
    )
    section = contract.split("\n    QuestionTagCoverage:\n", 1)[1].split(
        "\n    QuestionPublic:\n", 1
    )[0]
    properties = section.split("      properties:\n", 1)[1]
    item = properties.split("            properties:\n", 1)[1]
    return (
        set(re.findall(r"^ {8}([a-z0-9_]+):", properties, flags=re.MULTILINE)),
        set(re.findall(r"^ {14}([a-z0-9_]+):", item, flags=re.MULTILINE)),
    )


def _coverage(client, auth_headers, token: str = "teacher-a", **params) -> dict:
    params.setdefault("course_id", str(COURSE_A))
    response = client.get(COVERAGE, params=params, headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def _tag_counts(body: dict) -> dict[str, int]:
    return {item["tag"]: item["question_count"] for item in body["tags"]}


def _new_tag(prefix: str) -> str:
    """A tag only this test's questions carry, so another test's rows cannot be counted in."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _draft_in(client, auth_headers, *, token: str, course_id, tags: list[str] | None = None) -> str:
    """Create a draft in an arbitrary course over HTTP.

    Coverage counts questions, not versions, so a draft is enough for the aggregation
    tests; publishing would only add the attestation round trip.
    """
    payload = draft_payload(title=f"coverage fixture {uuid.uuid4()}")
    if tags is not None:
        payload["tags"] = tags
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(course_id)},
        json=payload,
        headers=auth_headers(token),
    )
    assert created.status_code == 201, created.text
    return created.json()["question_id"]


def test_coverage_route_precedes_the_question_path_parameter(client, auth_headers):
    """`CHG-0020` §2.1: the literal segment must be declared before `{question_id}`.

    Swallowed, it is parsed as a `question_id` and answered with a validation `422` (or a
    confusing `404`) instead of the aggregate -- the trap B2's `quality-summary` documents.
    Asserted both structurally (the declaration order in the router) and behaviourally,
    because only the second one proves FastAPI actually dispatches there.
    """
    from autograder_b3.api.router import router

    paths = [route.path for route in router.routes]
    assert paths.index(COVERAGE) < paths.index("/api/v1/questions/{question_id}")

    body = _coverage(client, auth_headers, course_id=COURSE_A)
    assert body["course_id"] == str(COURSE_A)  # the scope is echoed back

    # The parameterised sibling is not shadowed by the literal route: an unknown id is
    # still answered by the question handler (404), not by the aggregate.
    detail = client.get(
        f"/api/v1/questions/{uuid.uuid4()}", headers=auth_headers("teacher-a")
    )
    assert detail.status_code == 404


def test_coverage_requires_authentication_and_a_course_scope(client, auth_headers):
    """The contract declares `401` and `422` here, and `course_id` is required.

    A coverage read with no scope would have to guess which bank to count, so the missing
    parameter is refused rather than defaulted.

    Precedence is *recorded* rather than assumed: a request missing the required scope
    parameter is refused by request validation (`422`) even when it also carries no token,
    because FastAPI validates the endpoint signature before its body runs -- so the handler,
    and therefore the token check, never sees that request. Nothing is exposed by this: the
    parameter set is public. `401` is therefore asserted with the scope supplied, so "no
    token" is proven to be refused on its own rather than only alongside a bad shape.
    """
    scoped = {"course_id": str(COURSE_A)}
    assert client.get(COVERAGE, params=scoped).status_code == 401
    assert (
        client.get(
            COVERAGE, params=scoped, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )

    missing_scope = client.get(COVERAGE, headers=auth_headers("teacher-a"))
    assert missing_scope.status_code == 422, missing_scope.text
    assert missing_scope.json()["type"].endswith("/unprocessable-entity")
    assert client.get(COVERAGE).status_code == 422


def test_coverage_is_forbidden_across_courses_and_students_only_count_published(
    client, auth_headers
):
    """`CHG-0020` §8.2: cross-course is `403`; a student's counts stop at published content.

    The draft is created with a tag of its own so "the student did not count it" is visible
    as an absent tag rather than as a total that happens to be smaller.
    """
    published_tag, draft_tag = _new_tag("pub"), _new_tag("draft")
    _published_question_with(client, auth_headers, tags=[published_tag])
    _draft_in(client, auth_headers, token="teacher-a", course_id=COURSE_A, tags=[draft_tag])

    for token, course_id in (("teacher-a", COURSE_B), ("teacher-b", COURSE_A)):
        refused = client.get(
            COVERAGE, params={"course_id": str(course_id)}, headers=auth_headers(token)
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["type"].endswith("/forbidden")

    teacher_default = _tag_counts(_coverage(client, auth_headers, course_id=COURSE_A))
    assert teacher_default[published_tag] == 1
    assert draft_tag not in teacher_default  # published_only defaults to true

    student_default = _tag_counts(
        _coverage(client, auth_headers, token="student-a", course_id=COURSE_A)
    )
    assert student_default[published_tag] == 1
    assert draft_tag not in student_default

    # A student cannot reach the draft through the parameter either: non-staff callers are
    # forced to published-only, exactly as `list_questions` forces it.
    student_all = _tag_counts(
        _coverage(client, auth_headers, token="student-a", course_id=COURSE_A, published_only="false")
    )
    assert draft_tag not in student_all
    # ...while the teacher's explicit request does count it, so the difference above is the
    # role rule and not a dropped parameter.
    teacher_all = _tag_counts(
        _coverage(client, auth_headers, course_id=COURSE_A, published_only="false")
    )
    assert teacher_all[draft_tag] == 1


def test_coverage_agrees_with_the_list_visibility_rule(client, auth_headers):
    """`CHG-0020` §8.3: `published_only=false` counts drafts, and counts *the same* ones.

    Agreement is checked per tag against `listQuestions` rather than against a constant, so
    it fails if either side moves alone: the tag on the draft has to appear in both, with
    the same count, under both parameter values.
    """
    published_tag, draft_tag = _new_tag("agree-pub"), _new_tag("agree-draft")
    _published_question_with(client, auth_headers, tags=[published_tag])
    _draft_in(client, auth_headers, token="teacher-a", course_id=COURSE_A, tags=[draft_tag])

    def listed(tag: str, **params) -> int:
        response = client.get(
            "/api/v1/questions",
            params={**params, "tags": tag},
            headers=auth_headers("teacher-a"),
        )
        assert response.status_code == 200, response.text
        return len(response.json()["items"])

    for params in ({}, {"published_only": "false"}):
        counts = _tag_counts(_coverage(client, auth_headers, course_id=COURSE_A, **params))
        assert counts[published_tag] == listed(published_tag, **params)
        assert counts.get(draft_tag, 0) == listed(draft_tag, **params)

    # The premise, stated so the loop above cannot pass on two zeros: the draft is counted
    # when asked for and not otherwise, on both sides.
    assert listed(draft_tag) == 0
    assert listed(draft_tag, published_only="false") == 1


def test_coverage_identity_is_tagged_plus_untagged_and_the_tag_sum_is_larger(
    client, auth_headers
):
    """`CHG-0020` §8.1: `tagged + untagged == total`, and the naive identity is false.

    COURSE_B is written by no other test in the session, so these are absolute counts
    rather than deltas. The fixture is built so the two candidate identities disagree: two
    questions carry *both* tags, so the per-tag sum is 4 while the bank holds 3 questions,
    one of them untagged. `sum(tags[].question_count) + untagged == total` is therefore
    explicitly asserted to fail, which is what `QuestionTagCoverage`'s description warns a
    consumer not to assume.
    """
    first_tag, second_tag = _new_tag("overlap-a"), _new_tag("overlap-b")
    _draft_in(client, auth_headers, token="teacher-b", course_id=COURSE_B,
              tags=[first_tag, second_tag])
    _draft_in(client, auth_headers, token="teacher-b", course_id=COURSE_B,
              tags=[first_tag, second_tag])
    _draft_in(client, auth_headers, token="teacher-b", course_id=COURSE_B)

    body = _coverage(
        client, auth_headers, token="teacher-b", course_id=COURSE_B, published_only="false"
    )
    counts = _tag_counts(body)

    assert body["total_questions"] == 3
    assert body["tagged_questions"] == 2
    assert body["untagged_questions"] == 1
    # The identity the contract declares as checkable.
    assert body["tagged_questions"] + body["untagged_questions"] == body["total_questions"]

    # The identity that is *not* true, stated in both directions.
    overlap = counts[first_tag] + counts[second_tag]
    assert (counts[first_tag], counts[second_tag]) == (2, 2)
    assert overlap == 4 != body["total_questions"]
    assert overlap + body["untagged_questions"] != body["total_questions"]

    # A course whose questions are all drafts reads as empty under the default, which is a
    # result and not an error.
    default = _coverage(client, auth_headers, token="teacher-b", course_id=COURSE_B)
    assert (default["total_questions"], default["tagged_questions"], default["tags"]) == (0, 0, [])


def test_coverage_response_carries_counts_only(client, auth_headers):
    """`CHG-0020` §8.4: no `question_id`, no student id -- not as a key, not as a value.

    Four independent criteria, because any one alone can pass by coincidence: the key set
    is exactly the contract's declared one (read from the contract file); the nested item's
    key set is exactly `{tag, question_count}`; no key anywhere ends in `_id` except the
    echoed `course_id` scope; and the id of a question this test just created does not
    appear anywhere in the serialized body. With ids attached the response would stop being
    a distribution and become a way to walk the bank one question at a time.
    """
    tag = _new_tag("counts")
    question_id = _draft_in(
        client, auth_headers, token="teacher-a", course_id=COURSE_A, tags=[tag]
    )

    body = _coverage(client, auth_headers, course_id=COURSE_A, published_only="false")
    top_level, item_keys = _declared_coverage_keys()
    assert set(body) == top_level
    assert body["tags"], "the fixture question is tagged, so its tag must be listed"
    assert all(set(item) == item_keys for item in body["tags"])

    keys = collect_keys(body)
    assert {key for key in keys if key.endswith("_id")} == {"course_id"}
    assert PROTECTED_KEYS & keys == set()
    assert question_id not in json.dumps(body)
    # A UUID-shaped value other than the echoed scope would mean some identifier came back;
    # the question ids are exactly what must not be reconstructable from this response.
    without_scope = json.dumps({key: value for key, value in body.items() if key != "course_id"})
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", without_scope
    )


def test_a_bank_where_no_question_is_tagged_reports_untagged_not_an_error(client, auth_headers):
    """`CHG-0020` §8.5: an untagged bank is a *reading*, not a failure.

    COURSE_C is written by no other test, so the counts are exact. A shape that reported
    only tagged buckets would answer this bank and an empty one identically; reporting
    `untagged_questions` separately is what keeps "no topics recorded" distinguishable from
    "nothing to count" (the same discipline as `unmeasured_runs`).
    """
    _draft_in(client, auth_headers, token="teacher-c", course_id=COURSE_C)
    _draft_in(client, auth_headers, token="teacher-c", course_id=COURSE_C)

    body = _coverage(
        client, auth_headers, token="teacher-c", course_id=COURSE_C, published_only="false"
    )
    assert body["tags"] == []
    assert body["total_questions"] == 2
    assert body["tagged_questions"] == 0
    assert body["untagged_questions"] == body["total_questions"] == 2

    # The default view hides both drafts, and the empty bank is equally a reading.
    default = _coverage(client, auth_headers, token="teacher-c", course_id=COURSE_C)
    assert default["tags"] == []
    assert (default["total_questions"], default["untagged_questions"]) == (0, 0)


# --- CHG-0017: omission preserves, over the wire -------------------------
#
# The contract has always declared `cases` and `reference_answer` optional, while the
# implementation required `cases` -- so every edit, including a title-only one, had to carry
# the hidden cases and the reference answer out of the bank and back. These tests are the
# wire half of the fix; `tests/test_versions.py` holds the field-by-field comparisons
# against the protected store, which is where "nothing was rewritten" is actually provable.


def _protected_content(client, question_version_id) -> dict:
    """Read one version's protected rows straight from the store.

    A public DTO deliberately cannot show cases or the reference answer, so proving they
    survived an edit requires the internal role - the same connection the publish path
    uses. Case rows are compared **without** their ``test_case_id``: a new version can only
    be given its own rows, so identifiers must differ there by construction, while
    everything a reader would ever see must not. (That the superseded version's rows are
    not touched is a separate guarantee - PostgreSQL refuses it, ``TC-VER-01``.)
    """
    from autograder_b3.infra.repositories import ProtectedContentRepository

    dependencies = client.app.state.b3_dependencies
    with dependencies.database.protected_session() as session:
        protected = ProtectedContentRepository(session)
        cases = protected.list_cases(uuid.UUID(str(question_version_id)))
        answers = protected.list_reference_answers(uuid.UUID(str(question_version_id)))
        return {
            "cases": [
                {
                    "name": case.name,
                    "ordinal": case.ordinal,
                    "weight": case.weight,
                    "visible_to_student": case.visible_to_student,
                    "stdin": case.stdin,
                    "args": list(case.args),
                    "expected_stdout": case.expected_stdout,
                    "expected_stderr": case.expected_stderr,
                    "expected_exit_code": case.expected_exit_code,
                }
                for case in cases
            ],
            "answers": [
                {"language": answer.language, "body": answer.body}
                for answer in answers
            ],
        }


def _latest_version_id(client, auth_headers, question_id) -> str:
    response = client.get(
        f"/api/v1/questions/{question_id}", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 200, response.text
    return response.json()["versions"][0]["question_version_id"]


def test_a_correction_that_omits_both_protected_fields_is_accepted(client, auth_headers):
    """CHG-0017 §5.1/§5.2/§5.5 at the wire level: 200, and the protected rows are identical.

    The body carries the four public required fields plus a new title and tag; `cases` and
    `reference_answer` are **absent** (not empty, not null). Before CHG-0017 this was a
    `422` naming `cases`, which forced every caller to read hidden cases and the reference
    answer out of the bank first.
    """
    question_id = _published_question_with(client, auth_headers, tags=["uncorrected"])
    previous_version = _latest_version_id(client, auth_headers, question_id)
    before = _protected_content(client, previous_version)

    body = draft_payload(title="Corrected title", reference_answer=None)
    del body["cases"]
    assert "cases" not in body and "reference_answer" not in body
    body["tags"] = ["unix"]

    response = client.patch(
        f"/api/v1/questions/{question_id}",
        params={"as_new_version": "true"},
        json=body,
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Corrected title"
    assert response.json()["tags"] == ["unix"]

    new_version = _latest_version_id(client, auth_headers, question_id)
    assert new_version != previous_version
    # The version that was branched holds the *same* protected content: same case set, same
    # reference answer. The reference answer arriving at all is the part that makes the new
    # draft publishable without the caller ever having sent it.
    assert _protected_content(client, new_version) == before
    assert before["answers"] and before["cases"]

    # And the version DTO says the same thing structurally, which is what a teacher sees:
    # the case set and the reference answer are reported as unchanged, not as emptied.
    version = client.get(
        f"/api/v1/question-versions/{new_version}", headers=auth_headers("teacher-a")
    ).json()
    diff = version["revision_diff"]
    assert diff["case_count_before"] == diff["case_count_after"] == 2
    assert diff["case_set_changed"] is False
    assert diff["weights_changed"] is False
    assert diff["reference_answer_digest_before"] == diff["reference_answer_digest_after"]


def test_null_and_empty_case_sets_are_refused_without_writing_anything(client, auth_headers):
    """CHG-0017 §5.4, plus the reverse assertion: no path clears a case set.

    Omission preserves and a supplied set must hold at least one case, so the only way to
    ask for "no cases" is a shape the contract does not declare: `[]` breaks `minItems: 1`
    and `null` is not an array. Both are refused **before** anything is written, which is
    why the stored rows are compared afterwards rather than the status code alone.
    """
    question_id = _published_question_with(client, auth_headers)
    version_before = _latest_version_id(client, auth_headers, question_id)
    before = _protected_content(client, version_before)

    for rejected in ([], None):
        body = draft_payload(title="Trying to clear the cases", reference_answer=None)
        body["cases"] = rejected
        response = client.patch(
            f"/api/v1/questions/{question_id}",
            params={"as_new_version": "true"},
            json=body,
            headers=auth_headers("teacher-a"),
        )
        assert response.status_code == 422, response.text
        assert response.json()["status"] == 422

    # No version was created and the stored set is untouched.
    assert _latest_version_id(client, auth_headers, question_id) == version_before
    assert _protected_content(client, version_before) == before

    # The same shapes are refused on the first write, so a version cannot be created with
    # `[]`/`null` either, and an omitted set on creation is a draft that cannot publish -
    # asserted in `tests/test_versions.py`.
    for rejected in ([], None):
        created = client.post(
            "/api/v1/questions",
            params={"course_id": str(COURSE_A)},
            json=draft_payload() | {"cases": rejected},
            headers=auth_headers("teacher-a"),
        )
        assert created.status_code == 422, created.text


def test_a_body_that_omits_the_required_public_fields_is_still_refused(client, auth_headers):
    """The boundary CHG-0017 did **not** move, recorded so it is not mistaken for a bug.

    `QuestionDraftInput` still declares `title`, `stem_markdown`, `language` and `adapter`
    as required: they are public content, cheap to resend, and adding "omission preserves"
    to them would be a different change with its own contract record. So `{"tags": [...]}`
    alone remains a `422` -- and the refusal no longer mentions `cases`, which is exactly
    how CHG-0017 shows up in the message.
    """
    question_id = _published_question_with(client, auth_headers, tags=["before"])

    response = client.patch(
        f"/api/v1/questions/{question_id}",
        params={"as_new_version": "true"},
        json={"tags": ["unix"]},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    for field in ("body.title", "body.stem_markdown", "body.language", "body.adapter"):
        assert field in detail, detail
    assert "body.cases" not in detail, detail
