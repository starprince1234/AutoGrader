"""Course -> section (课序) -> class (班级) and the course-wide roster (v0.32.0).

The class level is what changed the platform's roster model: several registered classes now
share one 课序 instead of each class creating a section of its own. Two properties matter
more than the happy path and are asserted repeatedly here:

* **the database is the authority** -- "one active enrollment per student per course" is a
  partial unique index, a class always belongs to the section its enrollments name (composite
  foreign keys), and a request that would break either rule is refused with a clear `409`
  after the whole transaction rolls back, never with a silent pick or half-written data;
* **a page is not the audience** -- the course-wide roster read is paged to completeness and
  the summary counts come from SQL aggregation over every active enrollment, including the
  students who have not registered yet.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from autograder_b4.classes import translate_integrity_error
from autograder_b4.db.tables import classes, enrollments
from autograder_b4.errors import ProblemError

PASSWORD = "correct horse battery staple"

CLASS_KEYS = {
    "class_id",
    "course_id",
    "section_id",
    "class_name",
    "source",
    "allowlist_batch_id",
    "enrollment_count",
    "bound_count",
    "created_at",
}

ENROLLMENT_KEYS = {
    "enrollment_id",
    "course_id",
    "section_id",
    "class_id",
    "class_name",
    "student_id",
    "student_number",
    "display_name",
    "is_active",
    "updated_at",
}


def _admin_headers(factory, sign_in_as) -> dict[str, str]:
    factory.user(username="root", password=PASSWORD, roles=("admin",))
    return sign_in_as("root").headers


def _teacher(factory) -> uuid.UUID:
    return factory.user(username="teacher", password=PASSWORD, roles=("teacher",))


def _course(factory, teacher: uuid.UUID) -> uuid.UUID:
    course_id = factory.course(code="SE-2026", title="软件工程")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    return course_id


def _create_section(client, headers, course_id, code: str, term: str | None = None) -> uuid.UUID:
    response = client.post(
        f"/api/v1/courses/{course_id}/sections",
        headers=headers,
        json={"section_code": code, "term": term},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["section_id"])


def _upload_allowlist(
    client, admin_headers, filename: str, rows: list[tuple[str, str]]
) -> str:
    """Upload one registered class; the file name becomes the class name."""
    content = ("姓名,学号\n" + "".join(f"{name},{number}\n" for name, number in rows)).encode(
        "utf-8"
    )
    response = client.post(
        "/api/v1/registration-allowlists",
        headers=admin_headers,
        files={"file": (filename, content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()["batch_id"]


def _create_class(client, headers, section_id, *, class_name: str, batch_id: str | None = None):
    payload: dict[str, object] = {"class_name": class_name}
    if batch_id is not None:
        payload["batch_id"] = batch_id
    return client.post(
        f"/api/v1/sections/{section_id}/classes", headers=headers, json=payload
    )


def _classes_of(client, headers, section_id) -> list[dict[str, object]]:
    response = client.get(f"/api/v1/sections/{section_id}/classes", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _active_rows(engine, *, course_id: uuid.UUID, student_number: str) -> list:
    with engine.connect() as connection:
        return connection.execute(
            select(enrollments.c.enrollment_id, enrollments.c.class_id, enrollments.c.section_id)
            .where(
                enrollments.c.course_id == course_id,
                enrollments.c.student_number == student_number,
                enrollments.c.is_active.is_(True),
            )
            .order_by(enrollments.c.enrollment_id)
        ).fetchall()


# --------------------------------------------------------------- attach registered classes


def test_two_registered_classes_attach_to_the_same_section(
    client, factory, sign_in_as
) -> None:
    """A 课序 holds several 班级: attaching a batch never creates a section of its own."""
    admin_headers = _admin_headers(factory, sign_in_as)
    teacher = _teacher(factory)
    teacher_headers = sign_in_as("teacher").headers
    course_id = _course(factory, teacher)
    section_id = _create_section(client, teacher_headers, course_id, "01", "2026F")

    first_batch = _upload_allowlist(
        client, admin_headers, "一班.csv", [("张三", "2024000000001"), ("李四", "2024000000002")]
    )
    second_batch = _upload_allowlist(client, admin_headers, "二班.csv", [("王五", "2024000000003")])

    first = _create_class(
        client, teacher_headers, section_id, class_name="一班", batch_id=first_batch
    )
    assert first.status_code == 201, first.text
    first_class = first.json()
    assert set(first_class) == CLASS_KEYS
    assert first_class["section_id"] == str(section_id)
    assert first_class["course_id"] == str(course_id)
    assert first_class["source"] == "registered_class"
    assert first_class["allowlist_batch_id"] == first_batch
    assert first_class["enrollment_count"] == 2
    assert first_class["bound_count"] == 0

    second = _create_class(
        client, teacher_headers, section_id, class_name="二班", batch_id=second_batch
    )
    assert second.status_code == 201, second.text
    assert second.json()["enrollment_count"] == 1

    listed = {item["class_name"]: item for item in _classes_of(client, teacher_headers, section_id)}
    assert set(listed) == {"一班", "二班"}
    assert {item["section_id"] for item in listed.values()} == {str(section_id)}

    section_rows = client.get(
        f"/api/v1/courses/{course_id}/sections", headers=teacher_headers
    ).json()["items"]
    assert section_rows[0]["class_count"] == 2
    assert section_rows[0]["enrollment_count"] == 3

    # Re-attaching the same batch to the same section is idempotent: it returns the existing
    # class and neither duplicates the class row nor its roster rows.
    again = _create_class(
        client, teacher_headers, section_id, class_name="一班", batch_id=first_batch
    )
    assert again.status_code in (200, 201), again.text
    assert again.json()["class_id"] == first_class["class_id"]
    assert len(_classes_of(client, teacher_headers, section_id)) == 2
    assert factory.enrollment_count() == 3

    # The class roster carries all three levels, which is what a B1 row needs to render.
    roster = client.get(
        f"/api/v1/classes/{first_class['class_id']}/enrollments", headers=teacher_headers
    )
    assert roster.status_code == 200, roster.text
    items = roster.json()["items"]
    assert {item["student_number"] for item in items} == {"2024000000001", "2024000000002"}
    assert {item["class_id"] for item in items} == {first_class["class_id"]}
    assert {item["class_name"] for item in items} == {"一班"}
    assert {item["course_id"] for item in items} == {str(course_id)}
    assert {item["section_id"] for item in items} == {str(section_id)}
    assert all(set(item) == ENROLLMENT_KEYS for item in items)


def test_attaching_a_registered_class_links_existing_accounts(client, factory, sign_in_as) -> None:
    """An account that already exists for a student number is linked at attach time."""
    admin_headers = _admin_headers(factory, sign_in_as)
    teacher = _teacher(factory)
    teacher_headers = sign_in_as("teacher").headers
    course_id = _course(factory, teacher)
    section_id = _create_section(client, teacher_headers, course_id, "01")
    batch = _upload_allowlist(
        client, admin_headers, "一班.csv", [("张三", "2024000000001"), ("李四", "2024000000002")]
    )
    student = factory.user(
        username="stu1",
        password=PASSWORD,
        roles=("student",),
        display_name="张三",
        student_number="2024000000001",
    )

    created = _create_class(
        client, teacher_headers, section_id, class_name="一班", batch_id=batch
    )
    assert created.status_code == 201, created.text
    assert created.json()["enrollment_count"] == 2
    assert created.json()["bound_count"] == 1

    roster = client.get(
        f"/api/v1/classes/{created.json()['class_id']}/enrollments", headers=teacher_headers
    ).json()["items"]
    by_number = {item["student_number"]: item for item in roster}
    assert by_number["2024000000001"]["student_id"] == str(student)
    assert by_number["2024000000002"]["student_id"] is None


# ------------------------------------------------------------------------- class lifecycle


def test_manual_class_crud_and_its_roster_cascade(client, factory, sign_in_as) -> None:
    teacher = _teacher(factory)
    teacher_headers = sign_in_as("teacher").headers
    course_id = _course(factory, teacher)
    section_id = _create_section(client, teacher_headers, course_id, "01")

    created = _create_class(client, teacher_headers, section_id, class_name="实验班")
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == CLASS_KEYS
    assert body["source"] == "manual"
    assert body["allowlist_batch_id"] is None
    assert body["enrollment_count"] == 0

    sibling = _create_class(client, teacher_headers, section_id, class_name="试验班")
    assert sibling.status_code == 201, sibling.text

    renamed = client.patch(
        f"/api/v1/classes/{body['class_id']}",
        headers=teacher_headers,
        json={"class_name": "实验一班"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["class_name"] == "实验一班"
    assert set(renamed.json()) == CLASS_KEYS

    clash = client.patch(
        f"/api/v1/classes/{body['class_id']}",
        headers=teacher_headers,
        json={"class_name": "试验班"},
    )
    assert clash.status_code == 409

    # A class carries roster rows; deleting it removes them with it.
    enrollment_id = factory.enrollment(
        class_id=uuid.UUID(body["class_id"]),
        student_number="2024000000009",
        display_name="临时学生",
    )
    deleted = client.delete(f"/api/v1/classes/{body['class_id']}", headers=teacher_headers)
    assert deleted.status_code == 204, deleted.text
    assert [item["class_name"] for item in _classes_of(client, teacher_headers, section_id)] == [
        "试验班"
    ]
    with factory.engine.connect() as connection:
        remaining = connection.execute(
            select(enrollments.c.enrollment_id).where(
                enrollments.c.enrollment_id == enrollment_id
            )
        ).fetchone()
    assert remaining is None

    assert client.get(
        f"/api/v1/classes/{body['class_id']}/enrollments", headers=teacher_headers
    ).status_code == 404


def test_class_writes_require_course_write_scope(client, factory, sign_in_as) -> None:
    teacher = _teacher(factory)
    teacher_headers = sign_in_as("teacher").headers
    factory.user(username="outsider", password=PASSWORD, roles=("teacher",))
    student = factory.user(
        username="student",
        password=PASSWORD,
        roles=("student",),
        student_number="2024000000011",
    )
    course_id = _course(factory, teacher)
    section_id = _create_section(client, teacher_headers, course_id, "01")
    factory.enrollment(
        section_id=section_id,
        student_number="2024000000011",
        display_name="学生",
        user_id=student,
    )
    class_id = factory.default_class(section_id)

    student_headers = sign_in_as("student").headers
    outsider_headers = sign_in_as("outsider").headers

    # An enrolled student may read the class list (course-read scope) but not change it.
    assert client.get(
        f"/api/v1/sections/{section_id}/classes", headers=student_headers
    ).status_code == 200
    assert (
        _create_class(client, student_headers, section_id, class_name="学生班").status_code
        == 403
    )
    assert client.patch(
        f"/api/v1/classes/{class_id}", headers=student_headers, json={"class_name": "改名"}
    ).status_code == 403
    assert client.delete(f"/api/v1/classes/{class_id}", headers=student_headers).status_code == 403

    # A teacher without a grant on this course cannot even read it.
    assert client.get(
        f"/api/v1/sections/{section_id}/classes", headers=outsider_headers
    ).status_code == 403

    # A missing section is 404 for a caller who is allowed to know (admin).
    admin_headers = _admin_headers(factory, sign_in_as)
    assert client.get(
        f"/api/v1/sections/{uuid.uuid4()}/classes", headers=admin_headers
    ).status_code == 404


# ------------------------------------------------------ one active section per course


def test_a_student_active_in_another_class_blocks_the_whole_attach(
    client, factory, sign_in_as, engine
) -> None:
    """409, aggregate detail, and *no* half-written class or roster row."""
    admin_headers = _admin_headers(factory, sign_in_as)
    teacher = _teacher(factory)
    teacher_headers = sign_in_as("teacher").headers
    course_id = _course(factory, teacher)
    first_section = _create_section(client, teacher_headers, course_id, "01")
    second_section = _create_section(client, teacher_headers, course_id, "02")

    first_batch = _upload_allowlist(
        client, admin_headers, "一班.csv", [("张三", "2024000000001"), ("李四", "2024000000002")]
    )
    attached = _create_class(
        client, teacher_headers, first_section, class_name="一班", batch_id=first_batch
    )
    assert attached.status_code == 201, attached.text
    first_class_id = attached.json()["class_id"]

    # The second registered class contains 张三, who is already active in section 01.
    overlapping = _upload_allowlist(
        client, admin_headers, "二班.csv", [("张三", "2024000000001"), ("王五", "2024000000003")]
    )
    refused = _create_class(
        client, teacher_headers, second_section, class_name="二班", batch_id=overlapping
    )
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "冲突学生 1 人" in detail
    assert "01/一班" in detail

    # Nothing half-written: no class in the second section, the original enrollment intact,
    # and the innocent student of the refused batch was not enrolled either.
    assert _classes_of(client, teacher_headers, second_section) == []
    assert factory.enrollment_count() == 2
    original = _active_rows(engine, course_id=course_id, student_number="2024000000001")
    assert len(original) == 1
    assert str(original[0].class_id) == first_class_id
    assert _active_rows(engine, course_id=course_id, student_number="2024000000003") == []

    # The same course may hold one class per section as long as the students differ.
    other_batch = _upload_allowlist(client, admin_headers, "三班.csv", [("赵六", "2024000000004")])
    legal = _create_class(
        client, teacher_headers, second_section, class_name="三班", batch_id=other_batch
    )
    assert legal.status_code == 201, legal.text

    summary = client.get(
        f"/api/v1/courses/{course_id}/roster-summary", headers=teacher_headers
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["student_total"] == 3
    assert body["class_count"] == 2
    assert body["section_count"] == 2


def test_a_duplicate_active_enrollment_is_rejected_by_the_database_and_translated(
    engine, factory
) -> None:
    """The partial unique index is the final authority; the translation is a clear 409.

    A concurrent request cannot be staged deterministically from a test, so the conflict is
    constructed directly: the database produces the real `IntegrityError`, and the production
    translation function is asked to turn it into the response a racing request must receive.
    """
    course_id = factory.course(code="CS101")
    first_section = factory.section(course_id=course_id, code="01")
    second_section = factory.section(course_id=course_id, code="02")

    factory.enrollment(section_id=first_section, student_number="2024000000001")
    with pytest.raises(IntegrityError) as raised:
        factory.enrollment(section_id=second_section, student_number="2024000000001")

    error = raised.value
    assert "uq_enrollments_course_id_student_number_active" in str(error.orig)
    translated = translate_integrity_error(error)
    assert isinstance(translated, ProblemError)
    assert translated.status == 409
    assert "并发" in translated.detail
    assert "不会静默" in translated.detail

    # The database kept exactly the first row: no silent switch to the second class.
    rows = _active_rows(engine, course_id=course_id, student_number="2024000000001")
    assert len(rows) == 1
    assert str(rows[0].section_id) == str(first_section)


def test_a_duplicate_class_name_is_rejected_by_the_database(factory) -> None:
    """`UNIQUE (section_id, class_name)` is a database rule, not a handler convention."""
    course_id = factory.course(code="CS101")
    section_id = factory.section(course_id=course_id, code="01")
    existing = factory.default_class(section_id)
    with factory.engine.connect() as connection:
        existing_name = connection.execute(
            select(classes.c.class_name).where(classes.c.class_id == existing)
        ).scalar_one()

    with pytest.raises(IntegrityError) as raised:
        factory.class_(section_id=section_id, class_name=existing_name)

    assert "uq_classes_section_id_class_name" in str(raised.value.orig)
    translated = translate_integrity_error(raised.value)
    assert isinstance(translated, ProblemError)
    assert translated.status == 409


# --------------------------------------------------------------------- course-wide reads


def test_course_enrollments_page_to_completion(client, factory, sign_in_as) -> None:
    """Three-plus pages must lose nobody: the page is not the audience."""
    teacher = _teacher(factory)
    teacher_headers = sign_in_as("teacher").headers
    course_id = _course(factory, teacher)
    first_section = factory.section(course_id=course_id, code="01")
    second_section = factory.section(course_id=course_id, code="02")
    first_class = factory.default_class(first_section)
    extra_class = factory.class_(section_id=first_section, class_name="二班")

    created: list[uuid.UUID] = []
    for index in range(4):
        created.append(
            factory.enrollment(
                class_id=first_class,
                student_number=f"2024000000{index:03d}",
                display_name=f"学生{index}",
            )
        )
    for index in range(4, 6):
        created.append(
            factory.enrollment(
                class_id=extra_class,
                student_number=f"2024000000{index:03d}",
                display_name=f"学生{index}",
            )
        )
    created.append(
        factory.enrollment(
            section_id=second_section,
            student_number="2024000000006",
            display_name="学生6",
        )
    )

    collected: list[dict[str, object]] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(
            f"/api/v1/courses/{course_id}/enrollments", headers=teacher_headers, params=params
        )
        assert response.status_code == 200, response.text
        pages += 1
        body = response.json()
        collected.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10, "pagination did not terminate"

    assert pages >= 4
    assert len(collected) == 7
    assert len({item["enrollment_id"] for item in collected}) == 7
    assert {item["enrollment_id"] for item in collected} == {str(item) for item in created}
    assert all(set(item) == ENROLLMENT_KEYS for item in collected)
    assert {item["class_name"] for item in collected} == {"01班", "二班", "02班"}

    # The same completeness holds for the filtered reads a teacher UI uses.
    by_section = client.get(
        f"/api/v1/courses/{course_id}/enrollments",
        headers=teacher_headers,
        params={"section_id": str(first_section), "limit": 200},
    ).json()["items"]
    assert len(by_section) == 6
    assert {item["section_id"] for item in by_section} == {str(first_section)}

    by_class = client.get(
        f"/api/v1/courses/{course_id}/enrollments",
        headers=teacher_headers,
        params={"class_id": str(extra_class), "limit": 200},
    ).json()["items"]
    assert {item["class_id"] for item in by_class} == {str(extra_class)}
    assert len(by_class) == 2


def test_roster_summary_counts_match_sql_aggregation(
    client, factory, sign_in_as, engine
) -> None:
    """Every number is a SQL aggregate over active enrollments (unbound included)."""
    admin_headers = _admin_headers(factory, sign_in_as)
    teacher = _teacher(factory)
    course_id = _course(factory, teacher)
    first_section = factory.section(course_id=course_id, code="01", term="2026F")
    second_section = factory.section(course_id=course_id, code="02")
    first_class = factory.default_class(first_section)
    second_class = factory.class_(section_id=first_section, class_name="二班")

    bound_one = factory.user(
        username="bound-one", password=PASSWORD, roles=("student",), display_name="已绑定一"
    )
    bound_two = factory.user(
        username="bound-two", password=PASSWORD, roles=("student",), display_name="已绑定二"
    )
    factory.enrollment(
        class_id=first_class,
        student_number="2024000000001",
        display_name="已绑定一",
        user_id=bound_one,
    )
    factory.enrollment(
        class_id=first_class,
        student_number="2024000000002",
        display_name="未绑定一",
    )
    factory.enrollment(
        class_id=second_class,
        student_number="2024000000003",
        display_name="未绑定二",
    )
    factory.enrollment(
        section_id=second_section,
        student_number="2024000000004",
        display_name="已绑定二",
        user_id=bound_two,
    )
    inactive_id = factory.enrollment(
        section_id=second_section,
        student_number="2024000000005",
        display_name="已失效",
    )
    with engine.begin() as connection:
        connection.execute(
            update(enrollments)
            .where(enrollments.c.enrollment_id == inactive_id)
            .values(is_active=False)
        )

    response = client.get(f"/api/v1/courses/{course_id}/roster-summary", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    with engine.connect() as connection:
        totals = connection.execute(
            select(
                func.count().label("total"),
                func.count(enrollments.c.user_id).label("bound"),
            ).where(
                enrollments.c.course_id == course_id, enrollments.c.is_active.is_(True)
            )
        ).one()
        per_class = connection.execute(
            select(
                enrollments.c.section_id,
                enrollments.c.class_id,
                func.count(),
                func.count(enrollments.c.user_id),
            )
            .where(
                enrollments.c.course_id == course_id, enrollments.c.is_active.is_(True)
            )
            .group_by(enrollments.c.section_id, enrollments.c.class_id)
        ).fetchall()

    assert body["course_id"] == str(course_id)
    assert body["section_count"] == 2
    assert body["class_count"] == 3
    assert body["student_total"] == int(totals.total)
    assert body["bound_count"] == int(totals.bound)
    assert body["unbound_count"] == body["student_total"] - body["bound_count"]
    assert body["student_total"] == 4, "the inactive enrollment must be excluded"

    expected = {
        (str(row[0]), str(row[1])): (int(row[2]), int(row[3])) for row in per_class
    }
    for section in body["sections"]:
        for klass in section["classes"]:
            total, bound = expected[(section["section_id"], klass["class_id"])]
            assert klass["student_total"] == total
            assert klass["bound_count"] == bound
            assert klass["unbound_count"] == total - bound
        assert section["class_count"] == len(section["classes"])
        assert section["student_total"] == sum(item["student_total"] for item in section["classes"])
        assert section["bound_count"] == sum(item["bound_count"] for item in section["classes"])
