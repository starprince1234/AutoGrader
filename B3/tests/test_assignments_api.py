"""Assignments with an explicit target and the publication-notification hand-off (v0.38.0).

These are the B3 acceptance cases for the assignment model:

* an assignment belongs to one course and carries an explicit **target** -- target sections
  and, inside them, named students; naming neither means the whole course, which is also what
  every assignment created before the field existed means (no backfill);
* a target id is checked against the identity/course module **before** the write: an id outside
  the course is ``422`` (detail names it, nothing persisted), an absent course is ``404``, an
  unconfigured validator is ``503``. Duplicates never reach that call at all;
* the confirmed target is what gets stored and echoed, and it travels with the publication
  hand-off so B4 narrows its roster snapshot with the same filter;
* a student's access is still decided from B4's **authoritative current** authorization, so a
  student of the course sees the assignment and another course's student does not;
* the list's course filter is applied in SQL (a page is never shortened by a post-filter);
* publishing hands the audience to B4: ``scheduled`` on success, ``unavailable`` on any
  failure (the assignment is not rolled back), and the teacher retry endpoint is idempotent
  and maps B4's declared rejections back to the same status;
* version references keep their old semantics (cross-course ``404``, draft ``409``,
  duplicate ids ``422``) and B2's internal audience endpoint still requires the service token.

The database and the HTTP surface are real; only the identity/course module's *HTTP
exchange* is replaced (``fake_b4`` serves the three internal operations through the
production clients).
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from autograder_b3.api.app import create_app
from autograder_b3.integration.b4 import B4AssignmentNotifier, B4AuthorizationClient

from .conftest import (
    CLASS_E,
    COURSE_A,
    COURSE_B,
    COURSE_D,
    COURSE_E,
    SECTION_A,
    SECTION_A2,
    SECTION_E,
    STUDENT_A,
    STUDENT_B,
    draft_payload,
)
from .support import http_publish

ASSIGNMENTS = "/api/v1/assignments"
INTERNAL_ASSIGNMENT = "/api/v1/assignment-audiences"

#: The clock fixture's fixed moment; the notification request echoes it as ``published_at``.
CREATED_AT = "2026-09-15T08:30:00.000Z"


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
def published_course_e_version(client, auth_headers) -> str:
    return _publish_one_version(
        client, auth_headers, token="teacher-e", course_id=COURSE_E
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
    version_ids,
    title: str = "Homework",
    due_at: str | None = None,
    target: dict | None = None,
):
    body: dict = {
        "course_id": str(course_id),
        "title": title,
        "question_version_ids": [str(version) for version in version_ids],
    }
    if due_at is not None:
        body["due_at"] = due_at
    if target is not None:
        body["target"] = target
    return client.post(ASSIGNMENTS, json=body, headers=auth_headers(token))


def _target_sections(*section_ids) -> dict:
    return {"section_ids": [str(item) for item in section_ids], "student_ids": []}


def _target_students(*student_ids) -> dict:
    return {"section_ids": [], "student_ids": [str(item) for item in student_ids]}


def _target(sections=(), students=()) -> dict:
    return {
        "section_ids": [str(item) for item in sections],
        "student_ids": [str(item) for item in students],
    }


def _target_calls(fake_b4) -> list[dict]:
    """Every target-validation request the fake identity/course module received."""
    return [
        item
        for item in fake_b4.requests
        if item["url"].endswith("/api/v1/internal/course-targets/validate")
    ]


def _client_with(dependencies, **changes) -> TestClient:
    """Build the real app on modified dependencies (same database, different wiring)."""
    app = create_app(replace(dependencies, **changes))
    return TestClient(app, raise_server_exceptions=False)


def _detail(client, auth_headers, token: str, assignment_id: str):
    return client.get(f"{ASSIGNMENTS}/{assignment_id}", headers=auth_headers(token))


def _listed_ids(client, auth_headers, token: str, **params) -> set[str]:
    response = client.get(ASSIGNMENTS, params=params, headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return {item["assignment_id"] for item in response.json()["items"]}


# --- creation: whole-course shape ----------------------------------------


def test_create_assignment_without_a_target_covers_the_whole_course(
    client, auth_headers, fake_b4, published_version
):
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        due_at="2026-11-20T23:59:00Z",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "assignment_id",
        "course_id",
        "title",
        "status",
        "due_at",
        "question_version_ids",
        "question_items",
        "notification_state",
        "target",
        "created_at",
        "updated_at",
    }
    assert body["course_id"] == str(COURSE_A)
    assert body["question_version_ids"] == [published_version]
    assert body["status"] == "open"
    # Omitting the target means the whole course -- not "unknown", and not a missing field.
    assert body["target"] == {"section_ids": [], "student_ids": []}
    assert body["notification_state"] == "scheduled"
    # Nothing to confirm, so the validator is never asked: an empty target is a fact about
    # the assignment, not about the roster.
    assert _target_calls(fake_b4) == []

    sent = fake_b4.notifications[uuid.UUID(body["assignment_id"])]
    assert sent == {
        "assignment_id": body["assignment_id"],
        "course_id": str(COURSE_A),
        "title": "Homework",
        "due_at": "2026-11-20T23:59:00.000Z",
        "published_at": CREATED_AT,
        "section_ids": [],
        "student_ids": [],
    }
    post = next(
        item
        for item in fake_b4.requests
        if item["url"].endswith("/api/v1/internal/assignment-notifications")
    )
    assert post["headers"]["authorization"] == "Bearer test-service-token"


def test_an_explicitly_empty_target_is_the_same_whole_course(
    client, auth_headers, fake_b4, published_version
):
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["target"] == {"section_ids": [], "student_ids": []}
    assert _target_calls(fake_b4) == []


def test_create_assignment_without_a_due_date_sends_no_deadline(
    client, auth_headers, fake_b4, published_version
):
    response = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert response.status_code == 201, response.text
    assert response.json()["due_at"] is None
    sent = fake_b4.notifications[uuid.UUID(response.json()["assignment_id"])]
    assert sent["due_at"] is None


# --- creation: an explicit target ----------------------------------------


def test_create_assignment_with_target_sections_confirms_and_stores_them(
    client, auth_headers, fake_b4, service_headers, published_version
):
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A2, SECTION_A),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # Echoed in sorted order: a target is a set, and the stored target is the confirmed one.
    assert body["target"] == {
        "section_ids": sorted([str(SECTION_A), str(SECTION_A2)]),
        "student_ids": [],
    }

    calls = _target_calls(fake_b4)
    assert len(calls) == 1
    assert calls[0]["headers"]["authorization"] == "Bearer test-service-token"
    assert calls[0]["body"] == {
        "course_id": str(COURSE_A),
        "section_ids": [str(SECTION_A2), str(SECTION_A)],
        "student_ids": [],
    }

    # The hand-off carries the same ids, so B4 narrows with the assignment's own target.
    sent = fake_b4.notifications[uuid.UUID(body["assignment_id"])]
    assert sent["section_ids"] == sorted([str(SECTION_A), str(SECTION_A2)])
    assert sent["student_ids"] == []

    # And what was stored is what B2 reads for admission.
    audience = client.get(
        f"{INTERNAL_ASSIGNMENT}/{body['assignment_id']}", headers=service_headers
    )
    assert audience.status_code == 200, audience.text
    assert audience.json()["section_ids"] == sorted([str(SECTION_A), str(SECTION_A2)])
    assert audience.json()["student_ids"] == []


def test_create_assignment_with_named_students_confirms_and_stores_them(
    client, auth_headers, fake_b4, published_version
):
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target(sections=(SECTION_A,), students=(STUDENT_B, STUDENT_A)),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target"]["student_ids"] == sorted([str(STUDENT_A), str(STUDENT_B)])
    assert body["target"]["section_ids"] == [str(SECTION_A)]
    sent = fake_b4.notifications[uuid.UUID(body["assignment_id"])]
    assert sent["student_ids"] == sorted([str(STUDENT_A), str(STUDENT_B)])


def test_a_target_section_of_another_course_is_unprocessable_and_writes_nothing(
    client, auth_headers, fake_b4, published_version
):
    before = _listed_ids(client, auth_headers, "teacher-a", course_id=str(COURSE_A))
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_E),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert str(SECTION_E) in detail
    assert response.json()["type"].endswith("/assignment-target-invalid")

    # The check ran before the write: no assignment appeared, and no notification was sent.
    after = _listed_ids(client, auth_headers, "teacher-a", course_id=str(COURSE_A))
    assert after == before
    assert fake_b4.notifications == {}


def test_a_target_student_outside_the_course_is_unprocessable(
    client, auth_headers, fake_b4, published_version
):
    before = _listed_ids(client, auth_headers, "teacher-a", course_id=str(COURSE_A))
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_students(uuid.uuid4()),
    )
    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("/assignment-target-invalid")
    assert _listed_ids(client, auth_headers, "teacher-a", course_id=str(COURSE_A)) == before
    assert fake_b4.notifications == {}


def test_duplicate_target_ids_are_unprocessable_before_any_remote_call(
    client, auth_headers, fake_b4, published_version
):
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A, SECTION_A),
    )
    assert response.status_code == 422, response.text
    # Shape is decided locally: a duplicate cannot become a second row in a composite key,
    # so it is refused without asking B4 anything.
    assert _target_calls(fake_b4) == []


def test_a_target_without_the_identity_module_is_refused_with_503(
    dependencies, auth_headers, published_version
):
    client = _client_with(dependencies, b4_targets=None)
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A),
    )
    assert response.status_code == 503, response.text
    assert response.json()["type"].endswith("/target-validation-unavailable")


def test_an_unreachable_target_validator_is_503_and_writes_nothing(
    dependencies, auth_headers, published_version
):
    class _Down:
        def __call__(self, request, timeout: float):  # noqa: ARG002 - the port's shape
            raise OSError("connection refused")

    from autograder_b3.integration.b4 import B4CourseTargetValidator

    client = _client_with(
        dependencies,
        b4_targets=B4CourseTargetValidator(
            base_url="http://identity.invalid",
            service_token="test-service-token",
            timeout_seconds=1.0,
            opener=_Down(),
        ),
    )
    response = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A),
    )
    assert response.status_code == 503, response.text
    assert response.json()["type"].endswith("/target-validation-unavailable")


def test_assignment_keeps_question_version_order(client, auth_headers):
    first = _publish_one_version(client, auth_headers)
    second = _publish_one_version(client, auth_headers)
    response = _create_assignment(
        client, auth_headers, version_ids=(second, first), title="Two questions"
    )
    assert response.status_code == 201, response.text
    assert response.json()["question_version_ids"] == [second, first]


# --- creation: validation -------------------------------------------------


def test_duplicate_question_version_ids_are_unprocessable(
    client, auth_headers, published_version
):
    response = _create_assignment(
        client, auth_headers, version_ids=(published_version, published_version)
    )
    assert response.status_code == 422


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
        version_ids=(published_version,),
    )
    assert response.status_code == 404


def test_student_cannot_create_an_assignment(client, auth_headers, published_version):
    response = _create_assignment(
        client, auth_headers, token="student-a", version_ids=(published_version,)
    )
    assert response.status_code == 403


# --- creation: notification failures never roll the assignment back -------


def test_create_without_a_configured_notifier_records_unavailable(
    client, dependencies, auth_headers, published_version
):
    with _client_with(dependencies, b4_notifier=None) as overridden:
        response = overridden.post(
            ASSIGNMENTS,
            json={
                "course_id": str(COURSE_A),
                "title": "no notifier",
                "question_version_ids": [published_version],
            },
            headers=auth_headers("teacher-a"),
        )
    assert response.status_code == 201, response.text
    assert response.json()["notification_state"] == "unavailable"
    # The assignment is published and readable; only the hand-off is missing.
    assignment_id = response.json()["assignment_id"]
    assert _detail(client, auth_headers, "teacher-a", assignment_id).status_code == 200


def test_create_with_an_unreachable_notifier_records_unavailable(
    client, dependencies, auth_headers, published_version
):
    def failing_opener(request, timeout):
        raise ConnectionError("connection refused")

    broken = B4AssignmentNotifier(
        base_url="http://identity.invalid",
        service_token="test-service-token",
        opener=failing_opener,
    )
    with _client_with(dependencies, b4_notifier=broken) as overridden:
        response = overridden.post(
            ASSIGNMENTS,
            json={
                "course_id": str(COURSE_A),
                "title": "notifier down",
                "question_version_ids": [published_version],
            },
            headers=auth_headers("teacher-a"),
        )
    assert response.status_code == 201, response.text
    assert response.json()["notification_state"] == "unavailable"


def test_create_records_unavailable_when_b4_rejects_the_hand_off(
    client, auth_headers, fake_b4, published_version
):
    """B4's 4xx during creation is still "unavailable", not a failed creation."""
    fake_b4.default_notification_status = 422
    response = _create_assignment(
        client, auth_headers, version_ids=(published_version,), title="rejected hand-off"
    )
    assert response.status_code == 201, response.text
    assert response.json()["notification_state"] == "unavailable"


# --- notification retry ---------------------------------------------------


def test_notification_retry_is_idempotent(client, auth_headers, fake_b4, published_version):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    path = f"{ASSIGNMENTS}/{assignment_id}/notifications"

    first = client.post(path, headers=auth_headers("teacher-a"))
    assert first.status_code == 200, first.text
    assert first.json()["notification_state"] == "scheduled"
    second = client.post(path, headers=auth_headers("teacher-a"))
    assert second.status_code == 200, second.text
    assert second.json()["notification_state"] == "scheduled"
    # B4 is the idempotent side; B3 keeps handing over the same facts, never new ones.
    assert fake_b4.notifications[uuid.UUID(assignment_id)]["assignment_id"] == assignment_id


def test_an_unavailable_assignment_can_be_retried_to_scheduled(
    client, dependencies, auth_headers, fake_b4, published_version
):
    with _client_with(dependencies, b4_notifier=None) as overridden:
        created = overridden.post(
            ASSIGNMENTS,
            json={
                "course_id": str(COURSE_A),
                "title": "retry me",
                "question_version_ids": [published_version],
            },
            headers=auth_headers("teacher-a"),
        )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    assert created.json()["notification_state"] == "unavailable"

    retried = client.post(
        f"{ASSIGNMENTS}/{assignment_id}/notifications", headers=auth_headers("teacher-a")
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["assignment_id"] == assignment_id
    assert retried.json()["notification_state"] == "scheduled"
    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert detail.json()["notification_state"] == "scheduled"


@pytest.mark.parametrize("status", [404, 409, 422])
def test_notification_retry_maps_b4_rejections_to_the_same_status(
    client, auth_headers, fake_b4, published_version, status: int
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    fake_b4.notification_status[uuid.UUID(assignment_id)] = status

    response = client.post(
        f"{ASSIGNMENTS}/{assignment_id}/notifications", headers=auth_headers("teacher-a")
    )
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    # The persisted state tells the truth: the hand-off did not succeed.
    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert detail.status_code == 200
    assert detail.json()["notification_state"] == "unavailable"


def test_notification_retry_requires_course_write_permission(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]
    path = f"{ASSIGNMENTS}/{assignment_id}/notifications"

    assert client.post(path).status_code == 401
    assert client.post(path, headers=auth_headers("student-a")).status_code == 403
    # The assignment exists but belongs to COURSE_A; teacher-b has only COURSE_B.
    assert client.post(path, headers=auth_headers("teacher-b")).status_code == 403
    assert client.post(path, headers=auth_headers("teacher-a")).status_code == 200


def test_notification_retry_for_an_unknown_assignment_is_not_found(client, auth_headers):
    response = client.post(
        f"{ASSIGNMENTS}/{uuid.uuid4()}/notifications", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 404


# --- reads: students -----------------------------------------------------


def test_every_section_of_the_course_sees_the_assignment(
    client, auth_headers, published_version
):
    """Two students in two different sections/classes of COURSE_A both see it."""
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    for token in ("student-a", "student-b"):
        detail = _detail(client, auth_headers, token, assignment_id)
        assert detail.status_code == 200, (token, detail.text)
        assert detail.json()["assignment_id"] == assignment_id
        listed = _listed_ids(client, auth_headers, token)
        assert assignment_id in listed, token


def test_a_student_of_another_course_does_not_see_it(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]

    denied = _detail(client, auth_headers, "student-c", assignment_id)
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/forbidden")
    assert assignment_id not in _listed_ids(client, auth_headers, "student-c")


def test_removing_the_enrollment_revokes_access_immediately(
    client, auth_headers, fake_b4, published_version
):
    """The authoritative directory is asked per request, so removal needs no token refresh."""
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]

    assert _detail(client, auth_headers, "student-a", assignment_id).status_code == 200
    assert assignment_id in _listed_ids(client, auth_headers, "student-a")

    fake_b4.enrollments.pop(STUDENT_A)

    denied = _detail(client, auth_headers, "student-a", assignment_id)
    assert denied.status_code == 403
    listing = client.get(ASSIGNMENTS, headers=auth_headers("student-a"))
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"] == []


def test_student_list_filters_by_the_authoritative_courses_in_sql(
    client, auth_headers, fake_b4, clock, published_course_e_version
):
    """The page must be filtered in SQL, not after fetching it.

    COURSE_E is written by this test only. The visible assignment is created first, then
    three newer assignments are created in COURSE_B, which this student is not enrolled in.
    ``limit=1`` without a ``course_id`` filter must therefore return the COURSE_E
    assignment: a post-filtered page would serve a COURSE_B row first and end up empty.
    """
    fake_b4.enrollments[STUDENT_A] = (COURSE_E, SECTION_E, CLASS_E)
    visible = _create_assignment(
        client,
        auth_headers,
        token="teacher-e",
        course_id=COURSE_E,
        version_ids=(published_course_e_version,),
        title="visible",
    )
    assert visible.status_code == 201, visible.text
    visible_id = visible.json()["assignment_id"]

    clock.advance(60)
    course_b_version = _publish_one_version(
        client, auth_headers, token="teacher-b", course_id=COURSE_B
    )
    hidden_ids = set()
    for index in range(3):
        hidden = _create_assignment(
            client,
            auth_headers,
            token="teacher-b",
            course_id=COURSE_B,
            version_ids=(course_b_version,),
            title=f"hidden {index}",
        )
        assert hidden.status_code == 201, hidden.text
        hidden_ids.add(hidden.json()["assignment_id"])
        clock.advance(60)

    listing = client.get(ASSIGNMENTS, params={"limit": 1}, headers=auth_headers("student-a"))
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert [item["assignment_id"] for item in body["items"]] == [visible_id]
    assert body["next_cursor"] is None
    assert hidden_ids & {item["assignment_id"] for item in body["items"]} == set()


def test_student_reads_fail_closed_when_the_directory_is_unusable(
    client, dependencies, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]

    def failing_opener(request, timeout):
        raise ConnectionError("identity service down")

    broken = B4AuthorizationClient(
        base_url="http://identity.invalid",
        service_token="test-service-token",
        opener=failing_opener,
    )
    with _client_with(dependencies, b4_authorization=broken) as overridden:
        detail = overridden.get(
            f"{ASSIGNMENTS}/{assignment_id}", headers=auth_headers("student-a")
        )
        listed = overridden.get(ASSIGNMENTS, headers=auth_headers("student-a"))
        # A teacher does not depend on the directory and keeps working.
        teacher = overridden.get(
            f"{ASSIGNMENTS}/{assignment_id}", headers=auth_headers("teacher-a")
        )
    assert detail.status_code == 503
    assert detail.json()["type"].endswith("/authorization-unavailable")
    assert listed.status_code == 503
    assert teacher.status_code == 200

    with _client_with(dependencies, b4_authorization=None) as overridden:
        unconfigured = overridden.get(ASSIGNMENTS, headers=auth_headers("student-a"))
    assert unconfigured.status_code == 503


# --- reads: students are narrowed by the target --------------------------


def test_a_student_in_a_target_section_sees_the_assignment(
    client, auth_headers, published_version
):
    """STUDENT_A sits in SECTION_A (the fake directory's authoritative enrollment)."""
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    assert assignment_id in _listed_ids(client, auth_headers, "student-a")
    assert _detail(client, auth_headers, "student-a", assignment_id).status_code == 200


def test_a_student_outside_the_target_sections_does_not_see_the_assignment(
    client, auth_headers, published_version
):
    """STUDENT_B sits in SECTION_A2, so a SECTION_A assignment is not theirs."""
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    assert assignment_id not in _listed_ids(client, auth_headers, "student-b")
    denied = _detail(client, auth_headers, "student-b", assignment_id)
    # 403 rather than 404: the student is enrolled in the course, so the row is not a secret --
    # what refuses them is the target, and saying so is the honest answer.
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/forbidden")


def test_a_named_student_sees_the_assignment_and_classmates_do_not(
    client, auth_headers, published_version
):
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_students(STUDENT_A),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    assert assignment_id in _listed_ids(client, auth_headers, "student-a")
    assert assignment_id not in _listed_ids(client, auth_headers, "student-b")
    assert _detail(client, auth_headers, "student-b", assignment_id).status_code == 403


def test_a_student_sees_only_their_own_id_in_the_target(
    client, auth_headers, published_version
):
    """The teacher reads the whole target; a classmate's id is not a student-visible fact."""
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target(sections=(SECTION_A, SECTION_A2), students=(STUDENT_A, STUDENT_B)),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    teacher = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert teacher.status_code == 200, teacher.text
    assert teacher.json()["target"]["student_ids"] == sorted([str(STUDENT_A), str(STUDENT_B)])

    student = _detail(client, auth_headers, "student-a", assignment_id)
    assert student.status_code == 200, student.text
    assert student.json()["target"]["student_ids"] == [str(STUDENT_A)]
    # Sections are course structure, not people: they stay whole for every reader.
    assert student.json()["target"]["section_ids"] == sorted([str(SECTION_A), str(SECTION_A2)])

    listed = client.get(
        ASSIGNMENTS, params={"course_id": str(COURSE_A)}, headers=auth_headers("student-a")
    )
    assert listed.status_code == 200, listed.text
    item = next(
        row for row in listed.json()["items"] if row["assignment_id"] == assignment_id
    )
    assert item["target"]["student_ids"] == [str(STUDENT_A)]


def test_a_whole_course_assignment_is_still_visible_to_every_section(
    client, auth_headers, published_version
):
    """The regression that matters: no target must not mean "nobody"."""
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    assert assignment_id in _listed_ids(client, auth_headers, "student-a")
    assert assignment_id in _listed_ids(client, auth_headers, "student-b")


# --- reads: teachers -----------------------------------------------------


def test_teacher_reads_assignments_within_the_token_course_scope(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]

    # A section-scoped teacher token still grants the whole course: the target narrows who
    # the assignment is *for*, not what a teacher may read.
    assert _detail(client, auth_headers, "teacher-a", assignment_id).status_code == 200
    assert assignment_id in _listed_ids(client, auth_headers, "teacher-a")

    denied = _detail(client, auth_headers, "teacher-b", assignment_id)
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/forbidden")
    assert assignment_id not in _listed_ids(client, auth_headers, "teacher-b")

    assert assignment_id in _listed_ids(client, auth_headers, "teacher-wide")


# --- reads: the target on every projection -------------------------------


def test_detail_and_list_carry_the_stored_target(
    client, auth_headers, published_version
):
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target(sections=(SECTION_A, SECTION_A2), students=(STUDENT_A,)),
        title="targeted",
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    expected = {
        "section_ids": sorted([str(SECTION_A), str(SECTION_A2)]),
        "student_ids": [str(STUDENT_A)],
    }

    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert detail.status_code == 200, detail.text
    assert detail.json()["target"] == expected

    page = client.get(
        ASSIGNMENTS,
        params={"course_id": str(COURSE_A), "limit": 200},
        headers=auth_headers("teacher-a"),
    )
    assert page.status_code == 200, page.text
    listed = {
        item["assignment_id"]: item["target"] for item in page.json()["items"]
    }
    assert listed[assignment_id] == expected


def test_a_whole_course_assignment_reads_back_an_empty_target(
    client, auth_headers, published_version
):
    """Historical rows are exactly this shape: no target rows, and no invented ids."""
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert detail.json()["target"] == {"section_ids": [], "student_ids": []}
    page = client.get(
        ASSIGNMENTS,
        params={"course_id": str(COURSE_A), "limit": 200},
        headers=auth_headers("teacher-a"),
    )
    listed = {item["assignment_id"]: item["target"] for item in page.json()["items"]}
    assert listed[assignment_id] == {"section_ids": [], "student_ids": []}


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
    every other assignment test writes COURSE_A/B/E, so only a course nobody else writes
    can assert the served set exactly.
    """
    created_ids = []
    for index in range(3):
        response = _create_assignment(
            client,
            auth_headers,
            token="teacher-d",
            course_id=COURSE_D,
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


def test_assignment_list_filter_by_course(client, auth_headers, published_version):
    in_a = _create_assignment(
        client, auth_headers, version_ids=(published_version,), title="in a"
    )
    assert in_a.status_code == 201, in_a.text

    response = client.get(
        ASSIGNMENTS,
        params={"course_id": str(COURSE_D)},
        headers=auth_headers("teacher-wide"),
    )
    assert response.status_code == 200, response.text
    ids = {item["assignment_id"] for item in response.json()["items"]}
    assert in_a.json()["assignment_id"] not in ids


# --- internal audience endpoint -----------------------------------------


def test_internal_assignment_serves_the_audience_to_a_service(
    client, service_headers, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
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
        "question_version_ids",
        "section_ids",
        "student_ids",
    }
    assert body["assignment_id"] == assignment_id
    assert body["course_id"] == str(COURSE_A)
    assert body["status"] == "open"
    assert body["question_version_ids"] == [published_version]
    # Who is enrolled is still not asserted here (B4 is the only authority for it), but the
    # target is: B2 intersects it with that enrollment, and both empty means the whole course.
    assert body["section_ids"] == []
    assert body["student_ids"] == []
    # Timestamps are not needed for admission.
    assert "created_at" not in body
    assert "updated_at" not in body


def test_internal_assignment_serves_a_narrowed_target(
    client, service_headers, auth_headers, published_version
):
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target(sections=(SECTION_A,), students=(STUDENT_A,)),
    )
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]

    response = client.get(f"{INTERNAL_ASSIGNMENT}/{assignment_id}", headers=service_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["section_ids"] == [str(SECTION_A)]
    assert body["student_ids"] == [str(STUDENT_A)]


def test_internal_assignment_target_matches_the_public_read(
    client, service_headers, auth_headers, published_version
):
    created = _create_assignment(
        client,
        auth_headers,
        version_ids=(published_version,),
        target=_target_sections(SECTION_A2),
    )
    assignment_id = created.json()["assignment_id"]
    audience = client.get(f"{INTERNAL_ASSIGNMENT}/{assignment_id}", headers=service_headers)
    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert audience.status_code == 200, audience.text
    assert detail.status_code == 200, detail.text
    # One stored target, two readers: a mismatch here would mean the teacher is shown one
    # audience while B2 admits another.
    assert audience.json()["section_ids"] == detail.json()["target"]["section_ids"]
    assert audience.json()["student_ids"] == detail.json()["target"]["student_ids"]


def test_internal_assignment_requires_the_service_token(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
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
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
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


# --- misc invariants ------------------------------------------------------


def test_assignment_list_items_carry_the_notification_state(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]
    response = client.get(
        ASSIGNMENTS, params={"course_id": str(COURSE_A)}, headers=auth_headers("teacher-a")
    )
    assert response.status_code == 200, response.text
    item = next(
        item for item in response.json()["items"] if item["assignment_id"] == assignment_id
    )
    assert item["notification_state"] == "scheduled"


# --- manual end: `closed` is an admission change, not a display flag ---------


def test_close_assignment_ends_it_server_side(
    client, auth_headers, service_headers, published_version
):
    """``closed`` must reach the internal audience too: B2 admits only ``open``.

    The point of this test is not "a status changed". It is that the *same* fact B2 reads
    when accepting a submission (``GET /api/v1/assignment-audiences/{id}``) reports the end,
    which is what makes the teacher's button a real refusal instead of a hidden one.
    """
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assert created.status_code == 201, created.text
    assignment_id = created.json()["assignment_id"]
    assert created.json()["status"] == "open"

    closed = client.post(
        f"{ASSIGNMENTS}/{assignment_id}/close", headers=auth_headers("teacher-a")
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    # Closing must not drop the question list the detail page renders.
    assert closed.json()["question_version_ids"] == [str(published_version)]

    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "closed"

    internal = client.get(
        f"{INTERNAL_ASSIGNMENT}/{assignment_id}", headers=service_headers
    )
    assert internal.status_code == 200, internal.text
    assert internal.json()["status"] == "closed"


def test_close_assignment_is_idempotent_and_moves_updated_at(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]

    first = client.post(
        f"{ASSIGNMENTS}/{assignment_id}/close", headers=auth_headers("teacher-a")
    )
    second = client.post(
        f"{ASSIGNMENTS}/{assignment_id}/close", headers=auth_headers("teacher-a")
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "closed"
    # A repeat is a no-op on the row, not a new transition.
    assert second.json()["updated_at"] == first.json()["updated_at"]
    assert second.json()["assignment_id"] == first.json()["assignment_id"]


def test_close_assignment_outside_the_callers_course_is_forbidden(
    client, auth_headers, published_version
):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]

    # teacher-d is authorized for COURSE_D only; COURSE_A is outside that scope.
    response = client.post(
        f"{ASSIGNMENTS}/{assignment_id}/close", headers=auth_headers("teacher-d")
    )
    assert response.status_code == 403
    # The refused call must not have changed anything.
    detail = _detail(client, auth_headers, "teacher-a", assignment_id)
    assert detail.json()["status"] == "open"


def test_close_unknown_assignment_is_not_found(client, auth_headers):
    response = client.post(
        f"{ASSIGNMENTS}/{uuid.uuid4()}/close", headers=auth_headers("teacher-a")
    )
    assert response.status_code == 404


def test_closed_assignment_still_reads_and_lists(client, auth_headers, published_version):
    created = _create_assignment(client, auth_headers, version_ids=(published_version,))
    assignment_id = created.json()["assignment_id"]
    client.post(f"{ASSIGNMENTS}/{assignment_id}/close", headers=auth_headers("teacher-a"))

    listed = client.get(
        ASSIGNMENTS, params={"course_id": str(COURSE_A)}, headers=auth_headers("teacher-a")
    )
    assert listed.status_code == 200, listed.text
    item = next(
        row for row in listed.json()["items"] if row["assignment_id"] == assignment_id
    )
    assert item["status"] == "closed"
