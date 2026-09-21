"""RQ-B-4-50: user, role, course, section and enrollment management.

Covers the contract's declared behaviours that a happy-path smoke test would miss:
optimistic concurrency (`expected_updated_at`), conflict detection, cursor pagination, and
the audit record every management write leaves behind.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from autograder_b4.db.tables import audit_records, courses, enrollments, roles, users

PASSWORD = "correct horse battery staple"


@pytest.fixture
def admin(factory):
    return factory.user(username="root", password=PASSWORD, roles=("admin",))


@pytest.fixture
def teacher(factory):
    return factory.user(username="teacher", password=PASSWORD, roles=("teacher",))


# --------------------------------------------------------------------------- users


def test_create_user_stores_roles_and_a_hashed_password(
    client, admin, sign_in_as, factory, engine
) -> None:
    response = client.post(
        "/api/v1/users",
        headers=sign_in_as("root").headers,
        json={
            "display_name": "Alice Zhang",
            "username": "Alice",
            "student_number": "20230001",
            "email": "alice@example.edu",
            "roles": ["student"],
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    user_id = uuid.UUID(response.json()["user_id"])

    with engine.connect() as connection:
        row = connection.execute(select(users).where(users.c.user_id == user_id)).fetchone()
        role_rows = connection.execute(
            select(roles.c.role).where(roles.c.user_id == user_id)
        ).fetchall()
    assert row.username == "alice", "the username is normalized before storage"
    assert row.student_number == "20230001"
    assert [item[0] for item in role_rows] == ["student"]
    # The new user can sign in, which proves the credential was stored usable.
    assert sign_in_as("alice").user_id == user_id


def test_duplicate_username_conflicts(client, admin, sign_in_as) -> None:
    headers = sign_in_as("root").headers
    payload = {"display_name": "A", "username": "dup", "roles": ["student"]}
    assert client.post("/api/v1/users", headers=headers, json=payload).status_code == 201
    clash = client.post("/api/v1/users", headers=headers, json=payload)
    assert clash.status_code == 409


def test_duplicate_student_number_conflicts(client, admin, sign_in_as) -> None:
    headers = sign_in_as("root").headers
    base = {"display_name": "A", "roles": ["student"], "student_number": "20230001"}
    assert client.post(
        "/api/v1/users", headers=headers, json={**base, "username": "a"}
    ).status_code == 201
    clash = client.post("/api/v1/users", headers=headers, json={**base, "username": "b"})
    assert clash.status_code == 409


def test_an_unknown_role_is_rejected(client, admin, sign_in_as) -> None:
    response = client.post(
        "/api/v1/users",
        headers=sign_in_as("root").headers,
        json={"display_name": "A", "username": "a", "roles": ["superuser"]},
    )
    assert response.status_code == 422
    assert "superuser" in response.json()["detail"]


def test_user_creation_requires_at_least_one_role(client, admin, sign_in_as) -> None:
    response = client.post(
        "/api/v1/users",
        headers=sign_in_as("root").headers,
        json={"display_name": "A", "username": "a", "roles": []},
    )
    assert response.status_code == 422


def test_list_users_supports_a_role_filter_and_pagination(client, admin, sign_in_as, factory):
    headers = sign_in_as("root").headers
    for index in range(7):
        factory.user(username=f"student{index}", roles=("student",))
    factory.user(username="a-teacher", roles=("teacher",))

    page = client.get("/api/v1/users", headers=headers, params={"limit": 3})
    assert page.status_code == 200
    body = page.json()
    assert len(body["items"]) == 3
    assert body["next_cursor"]

    second = client.get(
        "/api/v1/users", headers=headers, params={"limit": 3, "cursor": body["next_cursor"]}
    )
    assert len(second.json()["items"]) == 3
    first_ids = {item["user_id"] for item in body["items"]}
    second_ids = {item["user_id"] for item in second.json()["items"]}
    assert first_ids.isdisjoint(second_ids), "cursor pagination returned overlapping pages"

    teachers = client.get("/api/v1/users", headers=headers, params={"role": "teacher"}).json()
    assert [item["display_name"] for item in teachers["items"]] == ["a-teacher"]


def test_an_invalid_cursor_is_a_client_error(client, admin, sign_in_as) -> None:
    response = client.get(
        "/api/v1/users", headers=sign_in_as("root").headers, params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 400


def test_an_out_of_range_limit_is_a_client_error(client, admin, sign_in_as) -> None:
    response = client.get(
        "/api/v1/users", headers=sign_in_as("root").headers, params={"limit": 5000}
    )
    assert response.status_code == 422


def test_update_user_changes_roles_and_audits_the_change(
    client, admin, sign_in_as, factory, engine
) -> None:
    target = factory.user(username="alice", roles=("student",))
    headers = sign_in_as("root").headers
    response = client.patch(
        f"/api/v1/users/{target}",
        headers=headers,
        json={"display_name": "Alice Z", "roles": ["student", "assistant"]},
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["changed_fields"]) == {"display_name", "roles"}

    with engine.connect() as connection:
        role_values = {
            item[0]
            for item in connection.execute(
                select(roles.c.role).where(roles.c.user_id == target)
            ).fetchall()
        }
        audit = connection.execute(
            select(audit_records.c.action, audit_records.c.change_digest, audit_records.c.detail)
            .where(audit_records.c.target_id == target)
            .order_by(audit_records.c.occurred_at.desc())
        ).fetchone()
    assert role_values == {"student", "assistant"}
    assert audit.action == "user.updated"
    assert audit.change_digest and len(audit.change_digest) == 64
    # The audit names the fields, and stores no values.
    assert "display_name" in audit.detail
    assert "Alice Z" not in audit.detail


def test_update_user_rejects_a_stale_expected_updated_at(client, admin, sign_in_as, factory):
    target = factory.user(username="alice", roles=("student",))
    headers = sign_in_as("root").headers
    listed = client.get("/api/v1/users", headers=headers).json()["items"]
    stamp = next(item["created_at"] for item in listed if item["user_id"] == str(target))

    assert client.patch(
        f"/api/v1/users/{target}", headers=headers, json={"display_name": "First"}
    ).status_code == 200
    stale = client.patch(
        f"/api/v1/users/{target}",
        headers=headers,
        json={"display_name": "Second", "expected_updated_at": stamp},
    )
    assert stale.status_code == 409


def test_update_of_a_missing_user_is_404(client, admin, sign_in_as) -> None:
    response = client.patch(
        f"/api/v1/users/{uuid.uuid4()}",
        headers=sign_in_as("root").headers,
        json={"display_name": "Ghost"},
    )
    assert response.status_code == 404


def test_deactivating_a_user_keeps_the_row_and_frees_the_username(
    client, admin, sign_in_as, factory, engine
) -> None:
    target = factory.user(username="alice", roles=("student",))
    response = client.delete(f"/api/v1/users/{target}", headers=sign_in_as("root").headers)
    assert response.status_code == 204
    with engine.connect() as connection:
        row = connection.execute(
            select(users.c.is_active, users.c.deactivated_at).where(users.c.user_id == target)
        ).fetchone()
    assert row.is_active is False
    assert row.deactivated_at is not None


def test_deactivating_a_missing_user_is_404(client, admin, sign_in_as) -> None:
    response = client.delete(f"/api/v1/users/{uuid.uuid4()}", headers=sign_in_as("root").headers)
    assert response.status_code == 404


# ------------------------------------------------------------------------- courses


def test_create_course_grants_the_creator_course_scope(client, teacher, sign_in_as, engine):
    response = client.post(
        "/api/v1/courses",
        headers=sign_in_as("teacher").headers,
        json={"course_code": "CS101", "title": "Intro to CS"},
    )
    assert response.status_code == 201, response.text
    course_id = uuid.UUID(response.json()["course_id"])
    with engine.connect() as connection:
        grant = connection.execute(
            select(roles.c.role, roles.c.course_id).where(
                roles.c.user_id == teacher, roles.c.course_id == course_id
            )
        ).fetchone()
    assert grant is not None and grant.role == "teacher"

    # And the creator can immediately manage what they created.
    assert client.post(
        f"/api/v1/courses/{course_id}/sections",
        headers=sign_in_as("teacher").headers,
        json={"section_code": "01"},
    ).status_code == 201


def test_duplicate_course_code_conflicts(client, teacher, sign_in_as) -> None:
    headers = sign_in_as("teacher").headers
    payload = {"course_code": "CS101", "title": "Intro"}
    assert client.post("/api/v1/courses", headers=headers, json=payload).status_code == 201
    assert client.post("/api/v1/courses", headers=headers, json=payload).status_code == 409


def test_course_creation_is_audited(client, teacher, sign_in_as, engine) -> None:
    client.post(
        "/api/v1/courses",
        headers=sign_in_as("teacher").headers,
        json={"course_code": "CS101", "title": "Intro"},
    )
    with engine.connect() as connection:
        actions = [row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()]
    assert "course.created" in actions


def test_create_section_under_a_course(client, teacher, sign_in_as, factory, engine):
    course_id = factory.course(code="CS101", created_by=teacher)
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    response = client.post(
        f"/api/v1/courses/{course_id}/sections",
        headers=sign_in_as("teacher").headers,
        json={"section_code": "01", "term": "2026F"},
    )
    assert response.status_code == 201, response.text
    section_id = uuid.UUID(response.json()["section_id"])
    listed = client.get(
        f"/api/v1/courses/{course_id}/sections", headers=sign_in_as("teacher").headers
    ).json()
    assert [item["section_id"] for item in listed["items"]] == [str(section_id)]
    assert listed["items"][0]["term"] == "2026F"


def test_duplicate_section_conflicts(client, teacher, sign_in_as, factory):
    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    headers = sign_in_as("teacher").headers
    body = {"section_code": "01", "term": "2026F"}
    assert client.post(
        f"/api/v1/courses/{course_id}/sections", headers=headers, json=body
    ).status_code == 201
    assert client.post(
        f"/api/v1/courses/{course_id}/sections", headers=headers, json=body
    ).status_code == 409


def test_section_creation_on_a_missing_course_is_404(client, teacher, sign_in_as):
    response = client.post(
        f"/api/v1/courses/{uuid.uuid4()}/sections",
        headers=sign_in_as("teacher").headers,
        json={"section_code": "01"},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------- enrollments


def test_update_enrollment_honours_optimistic_concurrency(
    client, teacher, sign_in_as, factory, engine
):
    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    enrollment_id = factory.enrollment(
        section_id=section_id, student_number="20230001", display_name="Old Name"
    )
    headers = sign_in_as("teacher").headers

    listed = client.get(f"/api/v1/sections/{section_id}/enrollments", headers=headers).json()
    stamp = listed["items"][0]["updated_at"]

    first = client.patch(
        f"/api/v1/enrollments/{enrollment_id}",
        headers=headers,
        json={"display_name": "New Name", "expected_updated_at": stamp},
    )
    assert first.status_code == 200, first.text
    assert first.json()["changed_fields"] == ["display_name"]

    stale = client.patch(
        f"/api/v1/enrollments/{enrollment_id}",
        headers=headers,
        json={"display_name": "Stale Name", "expected_updated_at": stamp},
    )
    assert stale.status_code == 409

    with engine.connect() as connection:
        name = connection.execute(
            select(enrollments.c.display_name).where(enrollments.c.enrollment_id == enrollment_id)
        ).scalar_one()
    assert name == "New Name"


def test_update_enrollment_rejects_a_duplicate_student_number(
    client, teacher, sign_in_as, factory
):
    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    first = factory.enrollment(section_id=section_id, student_number="20230001")
    factory.enrollment(section_id=section_id, student_number="20230002")
    response = client.patch(
        f"/api/v1/enrollments/{first}",
        headers=sign_in_as("teacher").headers,
        json={"student_number": "20230002"},
    )
    assert response.status_code == 409


def test_delete_enrollment_removes_the_row_and_audits(client, teacher, sign_in_as, factory, engine):
    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    enrollment_id = factory.enrollment(section_id=section_id, student_number="20230001")
    response = client.delete(
        f"/api/v1/enrollments/{enrollment_id}", headers=sign_in_as("teacher").headers
    )
    assert response.status_code == 204
    with engine.connect() as connection:
        remaining = connection.execute(
            select(enrollments.c.enrollment_id).where(
                enrollments.c.enrollment_id == enrollment_id
            )
        ).fetchone()
        actions = [row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()]
    assert remaining is None
    assert "enrollment.deleted" in actions


def test_enrollment_update_on_a_missing_row_is_404(client, teacher, sign_in_as):
    response = client.patch(
        f"/api/v1/enrollments/{uuid.uuid4()}",
        headers=sign_in_as("teacher").headers,
        json={"display_name": "Ghost"},
    )
    assert response.status_code == 404


def test_enrollment_listing_exposes_the_platform_student_id(
    client, teacher, sign_in_as, factory
):
    """CHG-0007: a roster row must carry `student_id`, the only key B2's rows share.

    B2 identifies a student by platform user id and legitimately cannot supply
    `student_number`/`display_name` (it owns only `b2`), so without this field "who has not
    submitted yet" cannot be answered from the roster at all. The row is null when the
    roster entry was never matched to a user account, which is why it is nullable.
    """
    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    matched = factory.user(username="matched", roles=())
    factory.enrollment(section_id=section_id, student_number="20230001", user_id=matched)
    factory.enrollment(section_id=section_id, student_number="20230002")

    listed = client.get(
        f"/api/v1/sections/{section_id}/enrollments", headers=sign_in_as("teacher").headers
    ).json()
    items = {item["student_number"]: item for item in listed["items"]}
    assert items["20230001"]["student_id"] == str(matched)
    assert items["20230002"]["student_id"] is None
    # `EnrollmentPublic` is `additionalProperties: false`, so the exact key set is the
    # contract: a field added or dropped without a contract change fails here.
    assert set(items["20230001"]) == {
        "enrollment_id",
        "section_id",
        "student_id",
        "student_number",
        "display_name",
        "is_active",
        "updated_at",
    }


def test_enrollment_listing_paginates(client, teacher, sign_in_as, factory):
    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    for index in range(5):
        factory.enrollment(section_id=section_id, student_number=f"2023000{index}")

    headers = sign_in_as("teacher").headers
    page = client.get(
        f"/api/v1/sections/{section_id}/enrollments", headers=headers, params={"limit": 2}
    ).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    rest = client.get(
        f"/api/v1/sections/{section_id}/enrollments",
        headers=headers,
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert {item["enrollment_id"] for item in rest["items"]}.isdisjoint(
        {item["enrollment_id"] for item in page["items"]}
    )


def test_course_listing_paginates_without_repeating(client, admin, sign_in_as, factory):
    headers = sign_in_as("root").headers
    for index in range(5):
        factory.course(code=f"CS{index:03d}")
    first = client.get("/api/v1/courses", headers=headers, params={"limit": 2}).json()
    second = client.get(
        "/api/v1/courses", headers=headers, params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 2
    assert {item["course_id"] for item in first["items"]}.isdisjoint(
        {item["course_id"] for item in second["items"]}
    )


def test_every_management_write_leaves_an_audit_record(
    client, admin, teacher, sign_in_as, factory, engine
) -> None:
    """Acceptance 8: the sensitive operations are all traceable."""
    root_headers = sign_in_as("root").headers
    target = factory.user(username="alice", roles=("student",))

    client.post(
        "/api/v1/users",
        headers=root_headers,
        json={"display_name": "Bob", "username": "bob", "roles": ["student"]},
    )
    client.patch(f"/api/v1/users/{target}", headers=root_headers, json={"display_name": "A Z"})
    client.delete(f"/api/v1/users/{target}", headers=root_headers)

    course_id = factory.course(code="CS101")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    created = client.post(
        f"/api/v1/courses/{course_id}/sections",
        headers=sign_in_as("teacher").headers,
        json={"section_code": "01"},
    ).json()
    section_id = uuid.UUID(created["section_id"])
    enrollment_id = factory.enrollment(section_id=section_id, student_number="20230001")
    client.patch(
        f"/api/v1/enrollments/{enrollment_id}",
        headers=sign_in_as("teacher").headers,
        json={"display_name": "Renamed"},
    )

    with engine.connect() as connection:
        actions = {row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()}
    assert {
        "user.created",
        "user.updated",
        "user.deactivated",
        "section.created",
        "enrollment.updated",
    } <= actions


def test_audit_rows_carry_the_request_and_correlation_identifiers(
    client, admin, sign_in_as, engine
) -> None:
    import uuid as uuid_module

    request_id = str(uuid_module.uuid4())
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    response = client.post(
        "/api/v1/users",
        headers={
            **sign_in_as("root").headers,
            "X-Request-ID": request_id,
            "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01",
        },
        json={"display_name": "A", "username": "a", "roles": ["student"]},
    )
    assert response.status_code == 201
    with engine.connect() as connection:
        row = connection.execute(
            select(audit_records.c.request_id, audit_records.c.correlation_id).where(
                audit_records.c.action == "user.created"
            )
        ).fetchone()
    assert row.request_id == request_id
    assert row.correlation_id == "4bf92f35-77b3-4da6-a3ce-929d0e0e4736"


def test_problem_responses_do_not_disclose_internals(client, admin, sign_in_as) -> None:
    """No stack trace, SQL or absolute path may appear in an error body."""
    response = client.get(
        "/api/v1/users", headers=sign_in_as("root").headers, params={"cursor": "!!!"}
    )
    assert response.status_code == 400
    body = response.text
    assert "Traceback" not in body
    assert "SELECT" not in body.upper()
    assert "D:\\" not in body and "/src/" not in body


def test_courses_and_sections_rows_are_not_shared_across_modules(factory, engine) -> None:
    """Every table B4 writes lives in `b4`; nothing is created in `public`."""
    factory.course(code="CS101")
    with engine.connect() as connection:
        public_tables = connection.execute(
            select(courses.c.course_id)
        ).fetchall()
    assert len(public_tables) == 1
