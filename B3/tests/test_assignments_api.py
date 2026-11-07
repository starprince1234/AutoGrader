"""Assignment target model and audience (``CHG-0032`` / assignment workflow §3).

These are the B3 acceptance cases for the assignment rework:

* an assignment targets 1..N sections and may narrow the audience to named students;
* target ids are validated against the identity/course module, and an unavailable
  validator refuses creation instead of accepting them on trust;
* version references keep their old semantics (cross-course ``404``, draft ``409``,
  duplicate ids ``422``);
* reads are audience-aware: teachers inside their course/section scope, students inside
  the target sections and, when the audience is narrowed, on the named list;
* the list is keyset-paginated and the cursor is actually used;
* B2's internal audience endpoint requires the service token and reports ``503`` when
  the deployment has not configured one.

The database and the HTTP surface are real; only the identity/course module's *endpoint*
is replaced (the ``b4_target_client`` fixture serves it from an in-memory mapping).
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from autograder_b3.api.app import create_app
from autograder_b3.integration.b4 import B4TargetClient

from .conftest import (
    COURSE_A,
    COURSE_B,
    COURSE_D,
    SECTION_A,
    SECTION_A2,
    SECTION_B,
    SECTION_D,
    STUDENT_A,
    STUDENT_B,
    draft_payload,
)
from .support import http_publish

ASSIGNMENTS = "/api/v1/assignments"
INTERNAL_ASSIGNMENT = "/api/v1/assignment-audiences"


def _publish_one_version(
    client,
    auth_headers,
    *,
    token: str = "teacher-a",
    course_id=COURSE_A,
) -> str:
    """Create + publish one question in ``course_id`` and return its version id."""
    headers = auth_headers(token)
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(course_id)},
        json=draft_payload(),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    detail = client.get(
        f"/api/v1/questions/{created.json()['question_id']}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    version_id = detail.json()["versions"][0]["question_version_id"]
    published = http_publish(client, version_id, headers=headers)
    assert published.status_code == 200, published.text
    return version_id


@pytest.fixture
def published_version(client, auth_headers) -> str:
    return _publish_one_version(client, auth_headers)


@pytest.fixture
def published_course_d_version(client, auth_headers) -> str:
    return _publish_one_version(
        client, auth_headers, token="teacher-d", course_id=COURSE_D
    )


@pytest.fixture
def draft_version(client, auth_headers) -> str:
    created = client.post(
        "/api/v1/questions",
        params={"course_id": str(COURSE_A)},
        json=draft_payload(title="unpublished"),
        headers=auth_headers("teacher-a"),
    )
    assert created.status_code == 201, created.text
    detail = client.get(
        f"/api/v1/questions/{created.json()['question_id']}",
        headers=auth_headers("teacher-a"),
    )
    return detail.json()["versions"][0]["question_version_id"]


def _create_assignment(
    client,
    auth_headers,
    *,
    token: str = "teacher-a",
    course_id=COURSE_A,
    section_ids=(SECTION_A,),
    student_ids=(),
    version_ids,
    title: str = "Homework",
):
    return client.post(
        ASSIGNMENTS,
        json={
            "course_id": str(course_id),
            "section_ids": [str(section) for section in section_ids],
            "student_ids": [str(student) for student in student_ids],
            "title": title,
            "question_version_ids": [str(version) for version in version_ids],
        },
        headers=auth_headers(token),
    )


def _client_with(dependencies, **changes) -> TestClient:
    """Build the real app on modified dependencies (same database, different wiring)."""
    app = create_app(replace(dependencies, **changes))
    return TestClient(app, raise_server_exceptions=False)


# --- creation: target model ----------------------------------------------


def test_assignment_targets_multiple_sections_and_narrows_students(
    client, auth_headers, published_version
):
    response = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A, SECTION_A2),
        student_ids=(STUDENT_A, STUDENT_B),
        version_ids=(published_version,),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "assignment_id",
        "course_id",
        "section_ids",
        "student_ids",
        "title",
        "status",
        "due_at",
        "question_version_ids",
        "created_at",
        "updated_at",
    }
    assert body["section_ids"] == [str(SECTION_A), str(SECTION_A2)]
    assert body["student_ids"] == [str(STUDENT_A), str(STUDENT_B)]
    assert body["question_version_ids"] == [published_version]
    assert body["status"] == "open"
    # The single-section field is gone, not deprecated: keeping it would be a second
    # answer to "who is this assignment for".
    assert "section_id" not in body


def test_assignment_can_target_exactly_one_section_without_narrowing(
    client, auth_headers, published_version
):
    response = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert response.status_code == 201, response.text
    assert response.json()["section_ids"] == [str(SECTION_A)]
    assert response.json()["student_ids"] == []


def test_assignment_keeps_question_version_order(client, auth_headers):
    first = _publish_one_version(client, auth_headers)
    second = _publish_one_version(client, auth_headers)
    response = _create_assignment(
        client, auth_headers, version_ids=(second, first), title="Two questions"
    )
    assert response.status_code == 201, response.text
    assert response.json()["question_version_ids"] == [second, first]


# --- creation: validation -------------------------------------------------


def test_unknown_section_is_unprocessable(client, auth_headers, published_version):
    response = _create_assignment(
        client, auth_headers, section_ids=(uuid.uuid4(),), version_ids=(published_version,)
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-assignment-target")
    assert response.json()["detail"].count("section target") == 1


def test_section_of_another_course_is_unprocessable(client, auth_headers, published_version):
    response = _create_assignment(
        client, auth_headers, section_ids=(SECTION_B,), version_ids=(published_version,)
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-assignment-target")


def test_student_must_be_enrolled_in_one_of_the_target_sections(
    client, auth_headers, published_version
):
    # STUDENT_B is actively enrolled in SECTION_A2, which this assignment does not target.
    response = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A,),
        student_ids=(STUDENT_A, STUDENT_B),
        version_ids=(published_version,),
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-assignment-target")
    # Counts only: a creation error must not become an enrollment lookup channel.
    assert str(STUDENT_B) not in response.text


def test_unknown_student_is_unprocessable(client, auth_headers, published_version):
    response = _create_assignment(
        client,
        auth_headers,
        student_ids=(uuid.uuid4(),),
        version_ids=(published_version,),
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-assignment-target")


def test_duplicate_target_ids_are_unprocessable(client, auth_headers, published_version):
    duplicate_sections = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A, SECTION_A),
        version_ids=(published_version,),
    )
    assert duplicate_sections.status_code == 422
    duplicate_students = _create_assignment(
        client,
        auth_headers,
        student_ids=(STUDENT_A, STUDENT_A),
        version_ids=(published_version,),
    )
    assert duplicate_students.status_code == 422
    duplicate_versions = _create_assignment(
        client, auth_headers, version_ids=(published_version, published_version)
    )
    assert duplicate_versions.status_code == 422


def test_draft_version_reference_is_conflict(client, auth_headers, draft_version):
    response = _create_assignment(client, auth_headers, version_ids=(draft_version,))
    assert response.status_code == 409
    assert response.json()["type"].endswith("/draft-assignment-reference")


def test_cross_course_version_reference_is_not_found(client, auth_headers, published_version):
    # teacher-b holds COURSE_B, so the write is authorized for that course; the referenced
    # version belongs to COURSE_A and must be reported as absent rather than leaked.
    response = _create_assignment(
        client,
        auth_headers,
        token="teacher-b",
        course_id=COURSE_B,
        section_ids=(SECTION_B,),
        version_ids=(published_version,),
    )
    assert response.status_code == 404


# --- creation: target validation availability ----------------------------


def test_create_assignment_without_a_target_validator_is_503(
    dependencies, auth_headers, published_version
):
    with _client_with(dependencies, b4_targets=None) as overridden:
        response = overridden.post(
            ASSIGNMENTS,
            json={
                "course_id": str(COURSE_A),
                "section_ids": [str(SECTION_A)],
                "title": "no validator",
                "question_version_ids": [published_version],
            },
            headers=auth_headers("teacher-a"),
        )
    assert response.status_code == 503
    assert response.json()["type"].endswith("/assignment-targets-unavailable")


def test_create_assignment_with_an_unreachable_validator_is_503(
    dependencies, auth_headers, published_version
):
    def failing_opener(request, timeout):
        raise ConnectionError("connection refused")

    broken = B4TargetClient(
        base_url="http://identity.invalid",
        service_token="test-service-token",
        opener=failing_opener,
    )
    with _client_with(dependencies, b4_targets=broken) as overridden:
        response = overridden.post(
            ASSIGNMENTS,
            json={
                "course_id": str(COURSE_A),
                "section_ids": [str(SECTION_A)],
                "title": "validator down",
                "question_version_ids": [published_version],
            },
            headers=auth_headers("teacher-a"),
        )
    assert response.status_code == 503
    assert response.json()["type"].endswith("/assignment-targets-unavailable")


# --- reads: teachers ------------------------------------------------------


def test_section_scoped_teacher_reads_only_its_own_target_sections(
    client, auth_headers, published_version
):
    visible = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A, SECTION_A2),
        version_ids=(published_version,),
        title="visible",
    )
    assert visible.status_code == 201, visible.text
    other_section = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A2,),
        version_ids=(published_version,),
        title="other section",
    )
    assert other_section.status_code == 201, other_section.text

    visible_id = visible.json()["assignment_id"]
    other_id = other_section.json()["assignment_id"]

    # teacher-a holds the (COURSE_A, SECTION_A) pair: intersecting target -> readable.
    assert (
        client.get(f"{ASSIGNMENTS}/{visible_id}", headers=auth_headers("teacher-a")).status_code
        == 200
    )
    # The record exists and the course is in scope, but no target section is in the token:
    # authenticated-but-outside-the-audience is 403 (never masked as 404).
    denied = client.get(f"{ASSIGNMENTS}/{other_id}", headers=auth_headers("teacher-a"))
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/forbidden")

    # A whole-course teacher may read both.
    assert (
        client.get(f"{ASSIGNMENTS}/{other_id}", headers=auth_headers("teacher-wide")).status_code
        == 200
    )

    listed = client.get(
        ASSIGNMENTS, params={"course_id": str(COURSE_A)}, headers=auth_headers("teacher-a")
    ).json()
    listed_ids = {item["assignment_id"] for item in listed["items"]}
    assert visible_id in listed_ids
    assert other_id not in listed_ids

    wide = client.get(
        ASSIGNMENTS, params={"course_id": str(COURSE_A)}, headers=auth_headers("teacher-wide")
    ).json()
    wide_ids = {item["assignment_id"] for item in wide["items"]}
    assert {visible_id, other_id} <= wide_ids


# --- reads: students ------------------------------------------------------


def test_student_reads_only_assignments_for_their_section_and_audience(
    client, auth_headers, published_version
):
    targeting_my_section = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A,),
        version_ids=(published_version,),
        title="my section",
    )
    targeting_other_section = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A2,),
        version_ids=(published_version,),
        title="other section",
    )
    narrowed_to_me = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A, SECTION_A2),
        student_ids=(STUDENT_A,),
        version_ids=(published_version,),
        title="narrowed to me",
    )
    narrowed_to_someone_else = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A, SECTION_A2),
        student_ids=(STUDENT_B,),
        version_ids=(published_version,),
        title="narrowed to someone else",
    )
    for response in (
        targeting_my_section,
        targeting_other_section,
        narrowed_to_me,
        narrowed_to_someone_else,
    ):
        assert response.status_code == 201, response.text

    visible_ids = {targeting_my_section.json()["assignment_id"], narrowed_to_me.json()["assignment_id"]}
    hidden_ids = {
        targeting_other_section.json()["assignment_id"],
        narrowed_to_someone_else.json()["assignment_id"],
    }

    for assignment_id in visible_ids:
        assert (
            client.get(f"{ASSIGNMENTS}/{assignment_id}", headers=auth_headers("student-a")).status_code
            == 200
        ), assignment_id
    for assignment_id in hidden_ids:
        assert (
            client.get(f"{ASSIGNMENTS}/{assignment_id}", headers=auth_headers("student-a")).status_code
            == 403
        ), assignment_id

    listed = client.get(
        ASSIGNMENTS, params={"course_id": str(COURSE_A)}, headers=auth_headers("student-a")
    )
    assert listed.status_code == 200, listed.text
    listed_ids = {item["assignment_id"] for item in listed.json()["items"]}
    assert visible_ids <= listed_ids
    assert listed_ids & hidden_ids == set()


def test_student_outside_the_target_section_is_forbidden_not_not_found(
    client, auth_headers, published_version
):
    assignment = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A2,),
        version_ids=(published_version,),
    )
    assert assignment.status_code == 201, assignment.text
    response = client.get(
        f"{ASSIGNMENTS}/{assignment.json()['assignment_id']}",
        headers=auth_headers("student-a"),
    )
    assert response.status_code == 403


# --- list: paging and authentication -------------------------------------


def test_assignment_list_requires_authentication(client):
    response = client.get(ASSIGNMENTS)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


def test_assignment_list_walks_every_page_with_the_cursor(
    client, dependencies, auth_headers, published_course_d_version
):
    """The cursor is decoded and used: page 2 must not re-serve page 1.

    Runs on COURSE_D/teacher-d because assignment rows are not truncated between tests:
    every other assignment test writes COURSE_A, so only a course nobody else writes can
    assert the served set exactly.
    """
    created_ids = []
    for index in range(3):
        response = _create_assignment(
            client,
            auth_headers,
            token="teacher-d",
            course_id=COURSE_D,
            section_ids=(SECTION_D,),
            version_ids=(published_course_d_version,),
            title=f"page {index}",
        )
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["assignment_id"])
        dependencies.clock.advance(60)

    first = client.get(
        ASSIGNMENTS,
        params={"course_id": str(COURSE_D), "limit": 2},
        headers=auth_headers("teacher-d"),
    )
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert len(first_page["items"]) == 2
    assert first_page["next_cursor"]

    second = client.get(
        ASSIGNMENTS,
        params={
            "course_id": str(COURSE_D),
            "limit": 2,
            "cursor": first_page["next_cursor"],
        },
        headers=auth_headers("teacher-d"),
    )
    assert second.status_code == 200, second.text
    second_page = second.json()
    # Without using the cursor the second call would re-serve the same first page; the
    # advertised cursor and the served page must agree.
    served = {item["assignment_id"] for item in first_page["items"]}
    assert served & {item["assignment_id"] for item in second_page["items"]} == set()
    assert second_page["next_cursor"] is None
    assert served | {item["assignment_id"] for item in second_page["items"]} == set(created_ids)


def test_assignment_list_rejects_a_malformed_cursor(client, auth_headers):
    response = client.get(
        ASSIGNMENTS,
        params={"cursor": "not-a-cursor"},
        headers=auth_headers("teacher-a"),
    )
    assert response.status_code == 422


def test_assignment_list_filters_by_target_section(
    client, auth_headers, published_version
):
    in_a = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A,),
        version_ids=(published_version,),
        title="in a",
    )
    in_a2 = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A2,),
        version_ids=(published_version,),
        title="in a2",
    )
    assert in_a.status_code == in_a2.status_code == 201

    response = client.get(
        ASSIGNMENTS,
        params={"course_id": str(COURSE_A), "section_id": str(SECTION_A)},
        headers=auth_headers("teacher-wide"),
    )
    assert response.status_code == 200, response.text
    ids = {item["assignment_id"] for item in response.json()["items"]}
    assert in_a.json()["assignment_id"] in ids
    assert in_a2.json()["assignment_id"] not in ids


# --- internal audience endpoint -----------------------------------------


def test_internal_assignment_serves_the_audience_to_a_service(
    client, service_headers, auth_headers, published_version
):
    created = _create_assignment(
        client,
        auth_headers,
        section_ids=(SECTION_A, SECTION_A2),
        student_ids=(STUDENT_A,),
        version_ids=(published_version,),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    response = client.get(f"{INTERNAL_ASSIGNMENT}/{assignment_id}", headers=service_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "assignment_id",
        "course_id",
        "title",
        "status",
        "section_ids",
        "student_ids",
        "question_version_ids",
    }
    assert body["assignment_id"] == assignment_id
    assert body["course_id"] == str(COURSE_A)
    assert body["section_ids"] == [str(SECTION_A), str(SECTION_A2)]
    assert body["student_ids"] == [str(STUDENT_A)]
    assert body["question_version_ids"] == [published_version]
    # Timestamps are not needed for admission and are deliberately absent.
    assert "created_at" not in body
    assert "updated_at" not in body


def test_internal_assignment_requires_the_service_token(
    client, auth_headers, published_version
):
    created = _create_assignment(
        client, auth_headers, version_ids=(published_version,)
    )
    assignment_id = created.json()["assignment_id"]

    unauthenticated = client.get(f"{INTERNAL_ASSIGNMENT}/{assignment_id}")
    assert unauthenticated.status_code == 401
    user_token = client.get(
        f"{INTERNAL_ASSIGNMENT}/{assignment_id}", headers=auth_headers("teacher-a")
    )
    assert user_token.status_code == 403


def test_internal_assignment_reports_an_unconfigured_service_credential(
    client, dependencies, auth_headers, published_version
):
    created = _create_assignment(
        client, auth_headers, version_ids=(published_version,)
    )
    assignment_id = created.json()["assignment_id"]
    with _client_with(dependencies, service_verifier=None) as overridden:
        response = overridden.get(
            f"{INTERNAL_ASSIGNMENT}/{assignment_id}",
            headers={"Authorization": "Bearer whatever"},
        )
    assert response.status_code == 503
    assert response.json()["type"].endswith("/service-unavailable")


def test_internal_assignment_for_an_unknown_id_is_not_found(client, service_headers):
    response = client.get(f"{INTERNAL_ASSIGNMENT}/{uuid.uuid4()}", headers=service_headers)
    assert response.status_code == 404
