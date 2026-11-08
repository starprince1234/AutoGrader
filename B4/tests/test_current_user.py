"""`GET /api/v1/me` (`CHG-0032` / v0.32.0): the caller's own account facts.

The endpoint exists so a student can see their own identity (the mail body needs the name and
student number) and -- since v0.32.0 -- **which courses, 课序 and 班级** they are in, without a
staff-only roster call. The boundaries the tests pin: authentication is required, the caller
can only ever read themselves, and the memberships are the caller's *active* enrollments with
no other student's data.
"""

from __future__ import annotations

import uuid

from sqlalchemy import update

from autograder_b4.db.tables import enrollments

PASSWORD = "correct horse battery staple"


def _student(factory, username: str, number: str) -> uuid.UUID:
    return factory.user(
        username=username,
        password=PASSWORD,
        roles=("student",),
        display_name=f"Name {number}",
        student_number=number,
    )


def test_the_caller_reads_their_own_row(client, factory, sign_in_as):
    user_id = _student(factory, "me-one", "20249001")
    headers = sign_in_as("me-one").headers
    response = client.get("/api/v1/me", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["username"] == "me-one"
    assert body["display_name"] == "Name 20249001"
    assert body["student_number"] == "20249001"
    assert "student" in body["roles"]
    assert "email" not in response.text
    # `CurrentUser` is `additionalProperties: false`: the exact key set is the contract.
    assert set(body) == {
        "user_id",
        "username",
        "display_name",
        "student_number",
        "roles",
        "memberships",
    }
    assert body["memberships"] == []


def test_another_account_sees_its_own_details_not_someone_elses(client, factory, sign_in_as):
    _student(factory, "me-two", "20249002")
    _student(factory, "me-three", "20249003")
    response = client.get("/api/v1/me", headers=sign_in_as("me-two").headers)
    body = response.json()
    assert body["username"] == "me-two"
    assert body["student_number"] == "20249002"
    assert "20249003" not in response.text


def test_authentication_is_required(client):
    assert client.get("/api/v1/me").status_code == 401


def test_memberships_list_every_active_course_with_section_and_class(
    client, factory, sign_in_as
) -> None:
    """A student taking two courses sees both, each with its 课序 and 班级."""
    user_id = _student(factory, "me-courses", "20249010")
    first_course = factory.course(code="SE-2026", title="Alpha")
    first_section = factory.section(course_id=first_course, code="01", term="2026F")
    first_class = factory.default_class(first_section)
    factory.enrollment(
        section_id=first_section,
        student_number="20249010",
        display_name="Name 20249010",
        user_id=user_id,
    )
    second_course = factory.course(code="CS-2026", title="Beta")
    second_section = factory.section(course_id=second_course, code="02", term="2027S")
    second_class = factory.default_class(second_section)
    factory.enrollment(
        section_id=second_section,
        student_number="20249010",
        display_name="Name 20249010",
        user_id=user_id,
    )

    body = client.get("/api/v1/me", headers=sign_in_as("me-courses").headers).json()
    memberships = body["memberships"]
    assert len(memberships) == 2
    assert all(
        set(item)
        == {
            "course_id",
            "course_code",
            "course_title",
            "section_id",
            "section_code",
            "term",
            "class_id",
            "class_name",
        }
        for item in memberships
    )
    by_course = {item["course_id"]: item for item in memberships}
    assert by_course[str(first_course)] == {
        "course_id": str(first_course),
        "course_code": "SE-2026",
        "course_title": "Alpha",
        "section_id": str(first_section),
        "section_code": "01",
        "term": "2026F",
        "class_id": str(first_class),
        "class_name": "01班",
    }
    assert by_course[str(second_course)]["class_id"] == str(second_class)
    assert by_course[str(second_course)]["class_name"] == "02班"


def test_an_inactive_enrollment_is_not_a_membership(client, factory, sign_in_as, engine) -> None:
    user_id = _student(factory, "me-inactive", "20249011")
    course_id = factory.course(code="SE-2026", title="Alpha")
    section_id = factory.section(course_id=course_id, code="01")
    enrollment_id = factory.enrollment(
        section_id=section_id,
        student_number="20249011",
        display_name="Name 20249011",
        user_id=user_id,
    )
    with engine.begin() as connection:
        connection.execute(
            update(enrollments)
            .where(enrollments.c.enrollment_id == enrollment_id)
            .values(is_active=False)
        )

    body = client.get("/api/v1/me", headers=sign_in_as("me-inactive").headers).json()
    assert body["memberships"] == []


def test_memberships_never_contain_another_students_data(client, factory, sign_in_as) -> None:
    mine = _student(factory, "me-mine", "20249012")
    _student(factory, "me-other", "20249013")
    course_id = factory.course(code="SE-2026", title="Alpha")
    section_id = factory.section(course_id=course_id, code="01")
    factory.enrollment(
        section_id=section_id,
        student_number="20249012",
        display_name="Name 20249012",
        user_id=mine,
    )
    factory.enrollment(
        section_id=section_id,
        student_number="20249013",
        display_name="Name 20249013",
    )

    body = client.get("/api/v1/me", headers=sign_in_as("me-mine").headers).json()
    assert [item["course_id"] for item in body["memberships"]] == [str(course_id)]
    assert "20249013" not in str(body)
    assert "Name 20249013" not in str(body)
