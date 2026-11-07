"""Internal course-target validation (`CHG-0032`).

B3 owns assignments but not sections/enrollments; it asks this endpoint before creating an
assignment so an invalid target cannot be stored on the client's word. The tests pin the
three properties that matter:

1. only the pre-shared service credential opens it — a user session is refused;
2. "valid" means *this course's section* and *an active enrollment in one of the requested
   sections*, not merely "some id that exists";
3. the response exposes ids and section labels only, never student names or numbers.
"""

from __future__ import annotations

import uuid

from sqlalchemy import update

from autograder_b4.db.tables import enrollments

SERVICE_TOKEN = "test-service-token"


def _headers(token: str = SERVICE_TOKEN) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _student(factory, number: str) -> uuid.UUID:
    return factory.user(
        username=f"stu-{number}",
        password="correct horse battery staple",
        roles=("student",),
        display_name=f"Student {number}",
        student_number=number,
    )


def test_valid_sections_and_enrolled_students_are_reported(client, factory):
    course_id = factory.course(code="CS101", title="Intro")
    section_a = factory.section(course_id=course_id, code="1")
    section_b = factory.section(course_id=course_id, code="2")
    student_a = _student(factory, "20240001")
    student_b = _student(factory, "20240002")
    factory.enrollment(section_id=section_a, student_number="20240001", user_id=student_a)
    factory.enrollment(section_id=section_b, student_number="20240002", user_id=student_b)

    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_headers(),
        json={
            "course_id": str(course_id),
            "section_ids": [str(section_a), str(section_b)],
            "student_ids": [str(student_a), str(student_b)],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["course_id"] == str(course_id)
    assert {item["section_id"] for item in body["sections"]} == {str(section_a), str(section_b)}
    assert body["unknown_section_ids"] == []
    assert {(item["student_id"], item["section_id"]) for item in body["students"]} == {
        (str(student_a), str(section_a)),
        (str(student_b), str(section_b)),
    }
    assert body["unknown_student_ids"] == []
    # Identity data beyond ids must not travel on this internal channel.
    assert "student_number" not in response.text
    assert "display_name" not in response.text


def test_unknown_section_ids_and_students_are_reported_separately(client, factory):
    course_id = factory.course(code="CS102", title="Intro")
    other_course = factory.course(code="CS999", title="Other")
    section = factory.section(course_id=course_id, code="1")
    foreign_section = factory.section(course_id=other_course, code="1")
    enrolled = _student(factory, "20240011")
    outsider = _student(factory, "20240012")
    factory.enrollment(section_id=section, student_number="20240011", user_id=enrolled)

    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_headers(),
        json={
            "course_id": str(course_id),
            "section_ids": [str(section), str(foreign_section)],
            "student_ids": [str(enrolled), str(outsider)],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["section_id"] for item in body["sections"]] == [str(section)]
    assert body["unknown_section_ids"] == [str(foreign_section)]
    assert body["students"] == [{"student_id": str(enrolled), "section_id": str(section)}]
    assert body["unknown_student_ids"] == [str(outsider)]


def test_inactive_enrollment_is_not_a_valid_target(client, factory, engine):
    course_id = factory.course(code="CS103", title="Intro")
    section = factory.section(course_id=course_id, code="1")
    student = _student(factory, "20240021")
    enrollment_id = factory.enrollment(
        section_id=section, student_number="20240021", user_id=student
    )
    with engine.begin() as connection:
        connection.execute(
            update(enrollments)
            .where(enrollments.c.enrollment_id == enrollment_id)
            .values(is_active=False)
        )

    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_headers(),
        json={
            "course_id": str(course_id),
            "section_ids": [str(section)],
            "student_ids": [str(student)],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["students"] == []
    assert response.json()["unknown_student_ids"] == [str(student)]


def test_empty_lists_are_valid_and_validate_nothing(client, factory):
    course_id = factory.course(code="CS104", title="Intro")
    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_headers(),
        json={"course_id": str(course_id), "section_ids": [], "student_ids": []},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "course_id": str(course_id),
        "sections": [],
        "unknown_section_ids": [],
        "students": [],
        "unknown_student_ids": [],
    }


def test_a_user_session_cannot_open_it(client, factory, sign_in_as):
    student = _student(factory, "20240031")
    headers = sign_in_as("stu-20240031").headers
    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=headers,
        json={
            "course_id": str(uuid.uuid4()),
            "section_ids": [],
            "student_ids": [str(student)],
        },
    )
    assert response.status_code == 401


def test_a_wrong_service_token_is_refused(client):
    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_headers("not-the-token"),
        json={"course_id": str(uuid.uuid4()), "section_ids": [], "student_ids": []},
    )
    assert response.status_code == 401


def test_an_invalid_body_is_422(client):
    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_headers(),
        json={"course_id": "not-a-uuid", "section_ids": ["also-bad"], "student_ids": []},
    )
    assert response.status_code == 422
