"""RQ-B-4-50: server-side authorization by role and course scope.

The point of these tests is that the decision is made by the server. Every case is a
request carrying a valid token for a principal who simply is not allowed to do the thing,
and the expected answer is `403` -- never a silent success, and never a `404` used to hide
the resource. Where the caller *is* permitted but the resource is missing, the answer must
be `404`, so the two are distinguishable.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from autograder_b4.db.tables import audit_records

PASSWORD = "correct horse battery staple"


@pytest.fixture
def world(factory):
    """A course with a teacher, an outside teacher, an assistant, a student and an admin."""
    admin = factory.user(username="admin", password=PASSWORD, roles=("admin",))
    owner = factory.user(username="owner", password=PASSWORD, roles=("teacher",))
    outsider = factory.user(username="outsider", password=PASSWORD, roles=("teacher",))
    assistant = factory.user(username="assistant", password=PASSWORD, roles=("assistant",))
    student = factory.user(
        username="student", password=PASSWORD, roles=("student",), student_number="20230001"
    )
    course_id = factory.course(code="CS101", title="Intro", created_by=owner)
    factory.grant(user_id=owner, course_id=course_id, role="teacher")
    factory.grant(user_id=assistant, course_id=course_id, role="assistant")
    section_id = factory.section(course_id=course_id, code="01", term="2026F")
    factory.enrollment(
        section_id=section_id, student_number="20230001", display_name="Student", user_id=student
    )
    other_course = factory.course(code="CS102", title="Other", created_by=outsider)
    factory.grant(user_id=outsider, course_id=other_course, role="teacher")
    return {
        "admin": admin,
        "owner": owner,
        "outsider": outsider,
        "assistant": assistant,
        "student": student,
        "course_id": course_id,
        "section_id": section_id,
        "other_course": other_course,
    }


def test_unauthenticated_request_is_401(client, world) -> None:
    assert client.get("/api/v1/courses").status_code == 401
    assert client.get(f"/api/v1/courses/{world['course_id']}/sections").status_code == 401


def test_malformed_authorization_header_is_401(client, world) -> None:
    assert client.get("/api/v1/courses", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/v1/courses", headers={"Authorization": "Bearer"}).status_code == 401


def test_student_cannot_list_platform_users(client, world, sign_in_as) -> None:
    """Acceptance 5: a normal user calling a management API is refused, and audited."""
    response = client.get("/api/v1/users", headers=sign_in_as("student").headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


def test_assistant_is_not_a_platform_administrator(client, world, sign_in_as) -> None:
    assert client.get("/api/v1/users", headers=sign_in_as("assistant").headers).status_code == 403
    assert client.post(
        "/api/v1/users",
        headers=sign_in_as("assistant").headers,
        json={"display_name": "X", "username": "x", "roles": ["student"]},
    ).status_code == 403


def test_teacher_outside_the_course_cannot_write_to_it(client, world, sign_in_as) -> None:
    """The scope check, which a hidden button in B1 would not have provided."""
    response = client.post(
        f"/api/v1/courses/{world['course_id']}/sections",
        headers=sign_in_as("outsider").headers,
        json={"section_code": "02"},
    )
    assert response.status_code == 403


def test_teacher_outside_the_course_cannot_read_its_roster(client, world, sign_in_as) -> None:
    response = client.get(
        f"/api/v1/sections/{world['section_id']}/enrollments",
        headers=sign_in_as("outsider").headers,
    )
    assert response.status_code == 403


def test_course_owner_can_write_and_read(client, world, sign_in_as) -> None:
    headers = sign_in_as("owner").headers
    created = client.post(
        f"/api/v1/courses/{world['course_id']}/sections",
        headers=headers,
        json={"section_code": "02", "term": "2026F"},
    )
    assert created.status_code == 201, created.text
    roster = client.get(
        f"/api/v1/sections/{world['section_id']}/enrollments", headers=headers
    )
    assert roster.status_code == 200, roster.text


def test_course_scoped_assistant_has_write_access(client, world, sign_in_as) -> None:
    response = client.post(
        f"/api/v1/courses/{world['course_id']}/sections",
        headers=sign_in_as("assistant").headers,
        json={"section_code": "03"},
    )
    assert response.status_code == 201, response.text


def test_enrolled_student_can_read_but_not_write(client, world, sign_in_as, factory) -> None:
    headers = sign_in_as("student").headers
    roster = client.get(
        f"/api/v1/sections/{world['section_id']}/enrollments", headers=headers
    )
    assert roster.status_code == 200, roster.text
    enrollment_id = roster.json()["items"][0]["enrollment_id"]
    write = client.patch(
        f"/api/v1/enrollments/{enrollment_id}",
        headers=headers,
        json={"display_name": "Renamed"},
    )
    assert write.status_code == 403


def test_administrator_reaches_every_course(client, world, sign_in_as) -> None:
    headers = sign_in_as("admin").headers
    assert client.get(
        f"/api/v1/sections/{world['section_id']}/enrollments", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/courses/{world['other_course']}/sections",
        headers=headers,
        json={"section_code": "99"},
    ).status_code == 201


def test_course_write_requires_teacher_or_admin_role(client, world, sign_in_as) -> None:
    """A student cannot create a course even though they are authenticated."""
    response = client.post(
        "/api/v1/courses",
        headers=sign_in_as("student").headers,
        json={"course_code": "CS999", "title": "Nope"},
    )
    assert response.status_code == 403


def test_permitted_but_absent_resource_is_404_not_403(client, world, sign_in_as) -> None:
    """`404` and `403` must stay distinguishable: the caller is allowed to know."""
    response = client.get(
        f"/api/v1/courses/{uuid.uuid4()}/sections", headers=sign_in_as("admin").headers
    )
    assert response.status_code == 404
    assert client.get(
        f"/api/v1/sections/{uuid.uuid4()}/enrollments", headers=sign_in_as("admin").headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/roster-imports/{uuid.uuid4()}", headers=sign_in_as("admin").headers
    ).status_code == 404


def test_authorization_denials_are_audited(client, world, sign_in_as, engine) -> None:
    """Acceptance 5 and 8: a refused management call leaves a record with the actor."""
    student_id = world["student"]
    client.get("/api/v1/users", headers=sign_in_as("student").headers)
    client.post(
        f"/api/v1/courses/{world['course_id']}/sections",
        headers=sign_in_as("outsider").headers,
        json={"section_code": "02"},
    )
    with engine.connect() as connection:
        rows = connection.execute(
            select(
                audit_records.c.action,
                audit_records.c.outcome,
                audit_records.c.actor_user_id,
            ).where(audit_records.c.action == "authz.denied")
        ).fetchall()
    assert rows, "no authorization denial was audited"
    assert all(row.outcome == "denied" for row in rows)
    actors = {row.actor_user_id for row in rows}
    assert student_id in actors
    assert world["outsider"] in actors


def test_enrollment_listing_is_scoped_to_the_caller(client, world, sign_in_as) -> None:
    """A student sees the roster of a section they belong to, and nothing else."""
    headers = sign_in_as("student").headers
    mine = client.get(f"/api/v1/sections/{world['section_id']}/enrollments", headers=headers)
    assert mine.status_code == 200
    assert [item["student_number"] for item in mine.json()["items"]] == ["20230001"]

    other_course = factory_course(client, world, headers)
    if other_course is not None:
        assert client.get(
            f"/api/v1/sections/{other_course}/enrollments", headers=headers
        ).status_code == 403


def factory_course(client, world, headers):
    created = client.post(
        "/api/v1/courses",
        headers=headers,
        json={"course_code": f"ZZ{uuid.uuid4().hex[:6]}", "title": "Foreign"},
    )
    if created.status_code != 201:
        return None
    section = client.post(
        f"/api/v1/courses/{created.json()['course_id']}/sections",
        headers=headers,
        json={"section_code": "01"},
    )
    return section.json()["section_id"] if section.status_code == 201 else None


def test_course_list_is_limited_to_the_callers_scope(client, world, sign_in_as) -> None:
    owner_courses = client.get("/api/v1/courses", headers=sign_in_as("owner").headers).json()
    assert [course["course_code"] for course in owner_courses["items"]] == ["CS101"]

    admin_courses = client.get("/api/v1/courses", headers=sign_in_as("admin").headers).json()
    assert {course["course_code"] for course in admin_courses["items"]} == {"CS101", "CS102"}

    student_courses = client.get("/api/v1/courses", headers=sign_in_as("student").headers).json()
    assert [course["course_code"] for course in student_courses["items"]] == ["CS101"]


def test_an_unscoped_teacher_sees_no_courses(client, factory, sign_in_as) -> None:
    factory.user(username="lone", password=PASSWORD, roles=("teacher",))
    response = client.get("/api/v1/courses", headers=sign_in_as("lone").headers)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_audit_records_are_invisible_to_students(client, world, sign_in_as) -> None:
    response = client.get("/api/v1/audit-records", headers=sign_in_as("student").headers)
    assert response.status_code == 403


def test_audit_records_are_scoped_for_teachers(client, world, sign_in_as, factory) -> None:
    """A teacher sees their own course's records, not another course's."""

    from autograder_b4.audit import ACTION_COURSE_CREATED, OUTCOME_SUCCESS, AuditActor, AuditLog

    engine = factory.engine
    with engine.begin() as connection:
        AuditLog(connection).record(
            action=ACTION_COURSE_CREATED,
            outcome=OUTCOME_SUCCESS,
            actor=AuditActor(user_id=world["owner"], roles=("teacher",)),
            target_type="course",
            target_id=world["course_id"],
            course_id=world["course_id"],
            detail="probe for the owner's course",
        )
        AuditLog(connection).record(
            action=ACTION_COURSE_CREATED,
            outcome=OUTCOME_SUCCESS,
            actor=AuditActor(user_id=world["outsider"], roles=("teacher",)),
            target_type="course",
            target_id=world["other_course"],
            course_id=world["other_course"],
            detail="probe for the other course",
        )
    owner_view = client.get("/api/v1/audit-records", headers=sign_in_as("owner").headers)
    assert owner_view.status_code == 200, owner_view.text
    # A course-scoped caller sees exactly the records of their own course: platform-level
    # records (a sign-in has no course) are not theirs to read.
    course_ids = {item["course_id"] for item in owner_view.json()["items"]}
    assert course_ids == {str(world["course_id"])}

    admin_view = client.get("/api/v1/audit-records", headers=sign_in_as("admin").headers)
    admin_course_ids = {item["course_id"] for item in admin_view.json()["items"]}
    assert {str(world["course_id"]), str(world["other_course"])} <= admin_course_ids
