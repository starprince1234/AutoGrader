"""Internal service-to-service calls: identity lookup and course-target validation.

Three properties of the identity lookup matter more than the happy path:

1. a user session must not open it (it answers "which account has this address" for any
   address, which is what enumeration wants);
2. an unknown address is `404`, so the intake can quarantine the message instead of guessing;
3. the response carries no address back, so B2 does not end up holding a second copy of it.

The course-target validator (v0.38.0) is the other internal operation: B3 asks it, *before*
writing an assignment, whether the target sections and named students really belong to the
course. Its own statements are asserted here too -- an empty target is the whole course and
is valid, an id outside the course is a `422` whose `detail` names it (B3 shows that detail
to the teacher), a repeated id is refused rather than collapsed, and an unknown course is a
`404` rather than an empty confirmation.
"""

from __future__ import annotations

import uuid

import pytest

SERVICE_TOKEN = "test-service-token"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def student_with_email(factory):
    return factory.user(
        username="stu-qq", password="correct horse battery staple", roles=("student",)
    )


def _set_email(engine, user_id, email: str) -> None:
    from sqlalchemy import update

    from autograder_b4.db.tables import users

    with engine.begin() as connection:
        connection.execute(
            update(users).where(users.c.user_id == user_id).values(email=email)
        )


def test_lookup_returns_the_account_for_a_known_address(client, engine, student_with_email):
    _set_email(engine, student_with_email, "2024141460192@qq.com")
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": " 2024141460192@QQ.com "},
        headers={"authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "stu-qq"
    assert "email" not in body
    assert "student" in body["roles"]


def test_unknown_address_is_404(client):
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": "nobody@qq.com"},
        headers={"authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    assert response.status_code == 404


def test_a_user_session_cannot_open_it(client, engine, student_with_email, sign_in_as):
    _set_email(engine, student_with_email, "2024141460192@qq.com")
    headers = sign_in_as("stu-qq").headers
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": "2024141460192@qq.com"},
        headers=headers,
    )
    assert response.status_code == 401


def test_a_wrong_service_token_is_refused(client):
    response = client.get(
        "/api/v1/internal/identities/by-email",
        params={"email": "nobody@qq.com"},
        headers={"authorization": "Bearer not-the-token"},
    )
    assert response.status_code == 401


# ------------------------------------------------------------------- course targets


def _service_headers() -> dict[str, str]:
    return {"authorization": f"Bearer {SERVICE_TOKEN}"}


def _validate(
    client,
    *,
    course_id,
    section_ids: tuple = (),
    student_ids: tuple = (),
    headers: dict[str, str] | None = None,
):
    payload = {
        "course_id": str(course_id),
        "section_ids": [str(value) for value in section_ids],
        "student_ids": [str(value) for value in student_ids],
    }
    return client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_service_headers() if headers is None else headers,
        json=payload,
    )


@pytest.fixture
def roster(factory):
    """One course with two sections and one enrolled student in each."""
    teacher = factory.user(username="owner", password=PASSWORD, roles=("teacher",))
    course_id = factory.course(code="CS101", title="软件工程", created_by=teacher)
    section_one = factory.section(course_id=course_id, code="01", term="2026F")
    section_two = factory.section(course_id=course_id, code="02", term="2026F")
    first_student = factory.user(
        username="stu-one",
        password=PASSWORD,
        roles=("student",),
        display_name="学生一",
        student_number="202400000001",
        email="one@example.edu.cn",
    )
    second_student = factory.user(
        username="stu-two",
        password=PASSWORD,
        roles=("student",),
        display_name="学生二",
        student_number="202400000002",
        email="two@example.edu.cn",
    )
    factory.enrollment(
        section_id=section_one,
        student_number="202400000001",
        display_name="学生一",
        user_id=first_student,
    )
    factory.enrollment(
        section_id=section_two,
        student_number="202400000002",
        display_name="学生二",
        user_id=second_student,
    )
    return {
        "course_id": course_id,
        "sections": (section_one, section_two),
        "students": (first_student, second_student),
    }


@pytest.fixture
def foreign_course(factory):
    """A second course: its section and its student are outside the first course's target."""
    teacher = factory.user(username="other-owner", password=PASSWORD, roles=("teacher",))
    course_id = factory.course(code="CS102", title="编译原理", created_by=teacher)
    section_id = factory.section(course_id=course_id, code="01")
    student_id = factory.user(
        username="outside-student",
        password=PASSWORD,
        roles=("student",),
        display_name="外课学生",
        student_number="202400000009",
        email="outside@example.edu.cn",
    )
    factory.enrollment(
        section_id=section_id,
        student_number="202400000009",
        display_name="外课学生",
        user_id=student_id,
    )
    return {"course_id": course_id, "section_id": section_id, "student_id": student_id}


def test_an_empty_target_means_the_whole_course(client, roster) -> None:
    # An empty target is a real target -- the shape of every assignment created before the
    # field existed -- so it is confirmed and echoed, never refused.
    response = _validate(client, course_id=roster["course_id"])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "course_id": str(roster["course_id"]),
        "section_ids": [],
        "student_ids": [],
    }

    # Omitting both keys is the same statement as sending two empty arrays.
    omitted = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_service_headers(),
        json={"course_id": str(roster["course_id"])},
    )
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["section_ids"] == []
    assert omitted.json()["student_ids"] == []


def test_a_valid_target_is_confirmed_and_echoed_sorted(client, roster) -> None:
    first_section, second_section = roster["sections"]
    first_student, second_student = roster["students"]
    response = _validate(
        client,
        course_id=roster["course_id"],
        section_ids=(second_section, first_section),
        student_ids=(second_student, first_student),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"course_id", "section_ids", "student_ids"}
    assert body["course_id"] == str(roster["course_id"])
    # A target is a set: the answer is sorted rather than in request order, so a caller cannot
    # mistake the order it sent for meaning.
    assert body["section_ids"] == sorted([str(first_section), str(second_section)])
    assert body["student_ids"] == sorted([str(first_student), str(second_student)])


def test_a_section_from_another_course_is_422_and_is_named(
    client, roster, foreign_course
) -> None:
    response = _validate(
        client,
        course_id=roster["course_id"],
        section_ids=(foreign_course["section_id"],),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert str(foreign_course["section_id"]) in detail
    assert "section(s)" in detail
    # The ids that do belong to the course are not blamed.
    assert str(roster["sections"][0]) not in detail


def test_a_student_outside_the_course_is_422_and_is_named(
    client, roster, foreign_course
) -> None:
    response = _validate(
        client,
        course_id=roster["course_id"],
        student_ids=(foreign_course["student_id"],),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert str(foreign_course["student_id"]) in detail
    assert "student(s)" in detail


def test_both_bad_axes_are_named_in_one_refusal(client, roster, foreign_course) -> None:
    response = _validate(
        client,
        course_id=roster["course_id"],
        section_ids=(foreign_course["section_id"],),
        student_ids=(foreign_course["student_id"],),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    # One refusal, both pickers named: the teacher fixes both, not the first one reported.
    assert str(foreign_course["section_id"]) in detail
    assert str(foreign_course["student_id"]) in detail
    assert detail.index("section(s)") < detail.index("student(s)")


def test_a_student_without_an_active_enrollment_is_422(client, roster, factory) -> None:
    """The target filters the *current active* roster, not its history."""
    withdrawn = factory.user(
        username="stu-withdrawn",
        password=PASSWORD,
        roles=("student",),
        display_name="退课学生",
        student_number="202400000003",
        email="withdrawn@example.edu.cn",
    )
    factory.enrollment(
        section_id=roster["sections"][0],
        student_number="202400000003",
        display_name="退课学生",
        user_id=withdrawn,
        is_active=False,
    )
    response = _validate(client, course_id=roster["course_id"], student_ids=(withdrawn,))
    assert response.status_code == 422, response.text
    assert str(withdrawn) in response.json()["detail"]


def test_an_unknown_course_is_404_even_when_the_target_is_bogus(client) -> None:
    response = _validate(
        client,
        course_id=uuid.uuid4(),
        section_ids=(uuid.uuid4(),),
        student_ids=(uuid.uuid4(),),
    )
    assert response.status_code == 404
    # "No such course" and "these ids are not part of it" are different statements; merging
    # them would leave a caller unable to tell a deleted course from a bad picker.
    assert "course" in response.json()["detail"].lower()


def test_the_target_validator_requires_the_service_token(client, roster, sign_in_as) -> None:
    assert _validate(client, course_id=roster["course_id"], headers={}).status_code == 401
    assert (
        _validate(
            client,
            course_id=roster["course_id"],
            headers={"authorization": "Bearer not-the-token"},
        ).status_code
        == 401
    )
    # A signed-in student session is not a service credential.
    student_headers = sign_in_as("stu-one").headers
    assert (
        _validate(client, course_id=roster["course_id"], headers=student_headers).status_code
        == 401
    )


def test_a_repeated_target_id_is_422(client, roster) -> None:
    # The contract declares `uniqueItems`; collapsing the duplicate silently would let the
    # caller believe it targeted something it did not.
    section_id = roster["sections"][0]
    response = _validate(
        client,
        course_id=roster["course_id"],
        section_ids=(section_id, section_id),
    )
    assert response.status_code == 422, response.text
    assert str(section_id) in response.json()["detail"]


def test_an_unknown_field_is_refused(client, roster) -> None:
    response = client.post(
        "/api/v1/internal/course-targets/validate",
        headers=_service_headers(),
        json={
            "course_id": str(roster["course_id"]),
            "class_ids": [str(uuid.uuid4())],
        },
    )
    assert response.status_code == 422
