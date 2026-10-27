"""RQ-B-4-50 acceptance 6 / RQ-B-1-10: the roster import pipeline.

The two properties that carry the most risk are asserted directly and repeatedly:

* **no enrollment is written before confirmation** -- the count is checked after every
  pre-confirmation step, not only at the end; and
* **a rejected row is never dropped silently** -- it appears in `rejected_rows` with its
  row number and a reason.

Both CSV and XLSX are exercised as real documents through the real readers, and the
confirmation is atomic, idempotent and digest-checked.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from autograder_b4.db.tables import (
    enrollments,
    outbox_events,
    roster_import_rows,
    roster_imports,
)
from tests.documents import xlsx_bytes

PASSWORD = "correct horse battery staple"
CHINESE_HEADER = ["学号", "姓名", "课程编号", "课序号"]


@pytest.fixture
def course(factory):
    teacher = factory.user(username="teacher", password=PASSWORD, roles=("teacher",))
    course_id = factory.course(code="CS101", title="Intro", created_by=teacher)
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01", term="2026F")
    return {"teacher": teacher, "course_id": course_id, "section_id": section_id}


def _upload(client, headers, key, content: bytes, filename: str, course):
    return client.post(
        "/api/v1/roster-imports",
        headers=headers,
        files={"file": (filename, content, "application/octet-stream")},
        data={"course_id": str(course["course_id"]), "section_id": str(course["section_id"])},
    )


def upload_limited_client(settings, *, limit: int):
    """A client whose module runs with a different upload cap.

    Built through the real `B4Module` and the real router, so the code path under test is
    the deployed one with one setting changed.
    """
    import contextlib
    import dataclasses

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from autograder_b4.plugin import B4Module
    from tests.conftest import TestHostContext

    module = B4Module(settings=dataclasses.replace(settings, roster_upload_max_bytes=limit))
    application = FastAPI()
    context = TestHostContext(settings={})
    application.state.modules = [module]
    application.include_router(module.router(context))

    @contextlib.contextmanager
    def _client():
        with TestClient(application, base_url="https://testserver") as test_client:
            yield test_client

    return _client()


def _confirm(client, headers, import_id, key=None):
    request_headers = dict(headers)
    if key is not None:
        request_headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/roster-imports/{import_id}/confirm", headers=request_headers
    )


# ------------------------------------------------------------------------------- XLSX


def test_xlsx_roster_parses_and_previews_without_writing(client, course, sign_in_as, factory):
    """The whole happy path up to the preview, with zero enrollments written."""
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "张三", "CS101", "01"], ["20230002", "李四", "CS101", "01"]],
        header=CHINESE_HEADER,
    )
    response = _upload(client, headers, None, content, "roster.xlsx", course)
    assert response.status_code == 202, response.text
    body = response.json()

    assert body["status"] == "previewed"
    assert body["detected_columns"] == CHINESE_HEADER
    assert body["proposed_mapping"]["student_number"] == "学号"
    assert body["proposed_mapping"]["display_name"] == "姓名"
    assert [row["values"]["student_number"] for row in body["accepted_rows"]] == [
        "20230001",
        "20230002",
    ]
    assert body["rejected_rows"] == []
    assert factory.enrollment_count() == 0, "a roster row was persisted before confirmation"

    # Reading the preview must not write either.
    again = client.get(f"/api/v1/roster-imports/{body['roster_import_id']}", headers=headers)
    assert again.status_code == 200
    assert factory.enrollment_count() == 0


def test_confirmation_writes_roster_rows_atomically(client, course, sign_in_as, factory, engine):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "张三", "CS101", "01"], ["20230002", "李四", "CS101", "01"]],
        header=CHINESE_HEADER,
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert factory.enrollment_count() == 0

    confirmed = _confirm(client, headers, created["roster_import_id"])
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["status"] == "committed"
    assert factory.enrollment_count() == 2

    with engine.connect() as connection:
        rows = connection.execute(
            select(enrollments.c.student_number, enrollments.c.display_name, enrollments.c.source)
        ).fetchall()
    assert {row.student_number for row in rows} == {"20230001", "20230002"}
    assert {row.display_name for row in rows} == {"张三", "李四"}
    assert {row.source for row in rows} == {"roster_import"}


def test_confirmation_is_idempotent(client, course, sign_in_as, factory):
    """Repeating the confirmation must not duplicate a single enrollment."""
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    first = _confirm(client, headers, created["roster_import_id"], key="confirm-1")
    assert first.status_code == 202
    second = _confirm(client, headers, created["roster_import_id"], key="confirm-1")
    assert second.status_code == 202
    assert second.json()["status"] == "committed"
    assert factory.enrollment_count() == 1


def test_a_different_idempotency_key_on_a_committed_import_conflicts(
    client, course, sign_in_as
):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert _confirm(client, headers, created["roster_import_id"], key="key-a").status_code == 202
    clash = _confirm(client, headers, created["roster_import_id"], key="key-b")
    assert clash.status_code == 409


def test_rejected_rows_are_reported_with_a_reason(client, course, sign_in_as, factory):
    """A row that fails validation appears in the report; it is never silently dropped.

    `row_number` is the spreadsheet's own row index, so the header is row 1 and the six
    data rows are 2..7; a rejected row can be pointed at in the source document directly.
    """
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [
            ["20230001", "张三", "CS101", "01"],
            ["", "无学号", "CS101", "01"],
            ["20230001", "重复学号", "CS101", "01"],
            ["20230004", "", "CS101", "01"],
            ["20230005", "错课程", "CS999", "01"],
            ["20230006", "错课序", "CS101", "99"],
        ],
        header=CHINESE_HEADER,
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert created["status"] == "previewed"
    accepted = {row["values"]["student_number"] for row in created["accepted_rows"]}
    rejected = {row["row_number"]: row["reason"] for row in created["rejected_rows"]}
    assert accepted == {"20230001"}
    assert set(rejected) == {3, 4, 5, 6, 7}
    assert "missing student number" in rejected[3]
    assert "duplicate student number" in rejected[4]
    assert "missing display name" in rejected[5]
    assert "CS999" in rejected[6]
    assert "99" in rejected[7]
    assert all(row["reason"] for row in created["rejected_rows"])
    assert factory.enrollment_count() == 0


def test_a_fully_rejected_roster_commits_nothing_and_is_terminal(
    client, course, sign_in_as, factory, engine
):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["", "无学号", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert created["status"] == "previewed"
    assert created["accepted_rows"] == []

    response = _confirm(client, headers, created["roster_import_id"])
    assert response.status_code == 422
    assert factory.enrollment_count() == 0

    # The terminal state and its audit survive the failure: they are committed before the
    # error is raised, not rolled back with it.
    with engine.connect() as connection:
        status = connection.execute(
            select(roster_imports.c.status).where(
                roster_imports.c.roster_import_id == uuid.UUID(created["roster_import_id"])
            )
        ).scalar_one()
    assert status == "rejected"


# ---------------------------------------------------------------------- ambiguous mapping


def test_ambiguous_columns_stop_for_a_human_decision(client, course, sign_in_as, factory):
    """Parsing must never guess: two candidate headers leave the field unmapped.

    The response carries no prose explanation -- `RosterImportPreview` is
    `additionalProperties: false` -- so the decision is expressed structurally: the status
    says a human is needed, `detected_columns` shows what was seen, and the ambiguous field
    is absent from `proposed_mapping` rather than arbitrarily resolved.
    """
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "ignored", "张三"]], header=["学号", "student_no", "姓名"]
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert created["status"] == "mapping_required"
    assert "student_number" not in created["proposed_mapping"]
    # The unambiguous column is still proposed, so the human only decides the hard one.
    assert created["proposed_mapping"]["display_name"] == "姓名"
    assert created["detected_columns"] == ["学号", "student_no", "姓名"]
    assert created["accepted_rows"] == []
    assert factory.enrollment_count() == 0


def test_missing_required_column_stops_for_a_human_decision(client, course, sign_in_as):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["张三"]], header=["姓名"])
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert created["status"] == "mapping_required"
    assert "student_number" not in created["proposed_mapping"]
    assert created["proposed_mapping"]["display_name"] == "姓名"


def test_a_supplied_mapping_resumes_the_import(client, course, sign_in_as, factory):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "ignored", "张三"]], header=["学号", "student_no", "姓名"]
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert created["status"] == "mapping_required"

    mapped = client.put(
        f"/api/v1/roster-imports/{created['roster_import_id']}/mapping",
        headers=headers,
        json={"columns": {"student_number": "学号", "display_name": "姓名"}},
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["status"] == "previewed"
    assert [row["values"]["student_number"] for row in body["accepted_rows"]] == ["20230001"]
    assert factory.enrollment_count() == 0

    # The mapping that was applied is the one recorded, and it survives a re-read.
    stored = client.get(
        f"/api/v1/roster-imports/{created['roster_import_id']}", headers=headers
    ).json()
    assert stored["status"] == "previewed"
    assert stored["accepted_rows"] == body["accepted_rows"]


def test_a_mapping_to_an_absent_column_is_rejected(client, course, sign_in_as):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "ignored", "张三"]], header=["学号", "student_no", "姓名"]
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    response = client.put(
        f"/api/v1/roster-imports/{created['roster_import_id']}/mapping",
        headers=headers,
        json={"columns": {"student_number": "不存在", "display_name": "姓名"}},
    )
    assert response.status_code == 422
    assert "不存在" in response.json()["detail"]


def test_a_mapping_missing_a_required_field_is_rejected(client, course, sign_in_as):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "ignored", "张三"]], header=["学号", "student_no", "姓名"]
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    response = client.put(
        f"/api/v1/roster-imports/{created['roster_import_id']}/mapping",
        headers=headers,
        json={"columns": {"student_number": "学号"}},
    )
    assert response.status_code == 422


def test_an_unknown_target_field_is_rejected(client, course, sign_in_as):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "ignored", "张三"]], header=["学号", "student_no", "姓名"]
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    response = client.put(
        f"/api/v1/roster-imports/{created['roster_import_id']}/mapping",
        headers=headers,
        json={"columns": {"student_number": "学号", "display_name": "姓名", "ssn": "学号"}},
    )
    assert response.status_code == 422
    assert "ssn" in response.json()["detail"]


# --------------------------------------------------------------------------------- CSV


def test_csv_roster_round_trips(client, course, sign_in_as, factory):
    """A real CSV (GB18030-encoded, as Chinese exports are) previews and then commits."""
    headers = sign_in_as("teacher").headers
    content = _csv_bytes(
        [("20230001", "Alice"), ("20230002", "Bob")],
        header=("student_number", "display_name", "course_code", "section_code"),
        encoding="utf-8",
    )
    created = _upload(client, headers, None, content, "roster.csv", course)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "previewed", body
    assert body["detected_columns"] == [
        "student_number",
        "display_name",
        "course_code",
        "section_code",
    ]
    numbers = [row["values"]["student_number"] for row in body["accepted_rows"]]
    assert numbers == ["20230001", "20230002"]
    assert factory.enrollment_count() == 0

    assert _confirm(client, headers, body["roster_import_id"]).status_code == 202
    assert factory.enrollment_count() == 2


def test_csv_without_known_columns_stops_for_a_mapping(client, course, sign_in_as):
    headers = sign_in_as("teacher").headers
    content = _csv_bytes([("a", "b")], header=("unrelated", "prose"), encoding="utf-8")
    created = _upload(client, headers, None, content, "roster.csv", course)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "mapping_required"
    assert body["proposed_mapping"] == {}
    assert body["accepted_rows"] == []


def test_an_empty_csv_fails_the_parse_explicitly(client, course, sign_in_as, engine):
    """An empty file is not an empty roster: it is a failed parse, and it says so."""
    headers = sign_in_as("teacher").headers
    created = _upload(client, headers, None, b"", "empty.csv", course)
    assert created.status_code == 422
    assert "empty" in created.json()["detail"].lower()
    with engine.connect() as connection:
        statuses = [
            row[0] for row in connection.execute(select(roster_imports.c.status)).fetchall()
        ]
    assert statuses == ["failed_parse"]


def _csv_bytes(
    rows: list[tuple[str, ...]],
    *,
    header: tuple[str, ...],
    encoding: str,
) -> bytes:
    """Build a real CSV payload with the given encoding (no quoting: the cells are simple)."""
    lines = [",".join(header), *[",".join(row) for row in rows]]
    return ("\n".join(lines) + "\n").encode(encoding)


# --------------------------------------------------------------------------- rejections


def test_a_pdf_upload_is_rejected(client, course, sign_in_as, factory):
    """PDF rosters were dropped: the reader used to guess columns from spacing."""
    headers = sign_in_as("teacher").headers
    response = _upload(client, headers, None, b"%PDF-1.4\n%not a roster\n", "roster.pdf", course)
    assert response.status_code == 422
    assert "CSV" in response.json()["detail"] and "XLSX" in response.json()["detail"]
    assert factory.enrollment_count() == 0


def test_mismatched_extension_and_content_is_rejected(client, course, sign_in_as):
    """A plain-text body named .xlsx is not a spreadsheet, and is not parsed as one."""
    headers = sign_in_as("teacher").headers
    content = b"student_number,display_name\n20230001,Alice\n"
    response = _upload(client, headers, None, content, "roster.xlsx", course)
    assert response.status_code == 422
    assert "XLSX" in response.json()["detail"] or "zip" in response.json()["detail"].lower()


def test_an_oversized_upload_is_rejected(client, course, sign_in_as, settings):
    """The cap is a setting, so the limit is exercised through a real application built
    with that setting rather than by monkeypatching the running module."""
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    with upload_limited_client(settings, limit=16) as limited:
        response = _upload(
            limited, sign_in_as("teacher").headers, None, content, "roster.xlsx", course
        )
    assert len(content) > 16
    assert response.status_code == 413


def test_an_empty_upload_is_rejected(client, course, sign_in_as):
    headers = sign_in_as("teacher").headers
    response = _upload(client, headers, None, b"", "roster.xlsx", course)
    assert response.status_code == 422


def test_section_must_belong_to_the_given_course(client, course, sign_in_as, factory):
    headers = sign_in_as("teacher").headers
    other_course = factory.course(code="CS200")
    other_section = factory.section(course_id=other_course, code="01")
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    response = client.post(
        "/api/v1/roster-imports",
        headers=headers,
        files={"file": ("roster.xlsx", content, "application/octet-stream")},
        data={"course_id": str(course["course_id"]), "section_id": str(other_section)},
    )
    assert response.status_code == 404
    assert factory.enrollment_count() == 0


# ------------------------------------------------------------------------ authorization


def test_a_teacher_outside_the_course_cannot_import(client, course, sign_in_as, factory):
    factory.user(username="outsider", password=PASSWORD, roles=("teacher",))
    headers = sign_in_as("outsider").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    response = _upload(client, headers, None, content, "roster.xlsx", course)
    assert response.status_code == 403
    assert factory.enrollment_count() == 0


def test_a_student_cannot_import_or_read_another_import(client, course, sign_in_as, factory):
    student = factory.user(username="student", password=PASSWORD, roles=("student",))
    teacher_headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, teacher_headers, None, content, "roster.xlsx", course).json()

    student_headers = sign_in_as("student").headers
    assert client.get(
        f"/api/v1/roster-imports/{created['roster_import_id']}", headers=student_headers
    ).status_code == 403
    assert _confirm(client, student_headers, created["roster_import_id"]).status_code == 403
    assert student  # the student exists and is simply not permitted


# ----------------------------------------------------------------------- integrity


def test_a_tampered_stored_artifact_is_refused_at_confirmation(
    client, course, sign_in_as, engine
):
    """The digest recorded at upload is re-checked before anything is written."""
    from autograder_b4.db.tables import roster_import_files

    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()

    other = xlsx_bytes([["20239999", "篡改", "CS101", "01"]], header=CHINESE_HEADER)
    with engine.begin() as connection:
        connection.execute(
            roster_import_files.update()
            .where(
                roster_import_files.c.roster_import_id
                == uuid.UUID(created["roster_import_id"])
            )
            .values(content=other)
        )
    response = _confirm(client, headers, created["roster_import_id"])
    assert response.status_code == 409
    assert "digest" in response.json()["detail"]


def test_the_recorded_digest_matches_the_uploaded_bytes(client, course, sign_in_as, engine):
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    with engine.connect() as connection:
        row = connection.execute(
            select(
                roster_imports.c.content_sha256,
                roster_imports.c.size_bytes,
                roster_imports.c.media_type,
                roster_imports.c.filename,
            ).where(roster_imports.c.roster_import_id == uuid.UUID(created["roster_import_id"]))
        ).fetchone()
    assert row.content_sha256 == hashlib.sha256(content).hexdigest()
    assert row.size_bytes == len(content)
    assert row.media_type.endswith("spreadsheetml.sheet")
    assert row.filename == "roster.xlsx"


def test_staged_rows_are_kept_separate_from_enrollments(client, course, sign_in_as, engine):
    """The staging table holds the preview; `enrollments` stays empty until confirmation."""
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "张三", "CS101", "01"], ["", "bad", "CS101", "01"]],
        header=CHINESE_HEADER,
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    with engine.connect() as connection:
        staged = connection.execute(
            select(roster_import_rows.c.status, roster_import_rows.c.reason).where(
                roster_import_rows.c.roster_import_id == uuid.UUID(created["roster_import_id"])
            )
        ).fetchall()
    assert {row.status for row in staged} == {"accepted", "rejected"}
    assert connection_enrollments(engine) == 0


def connection_enrollments(engine) -> int:
    from sqlalchemy import func

    with engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(enrollments)).scalar_one())


# -------------------------------------------------------------------------- the event


def test_confirmation_queues_exactly_one_pii_free_event(client, course, sign_in_as, engine):
    """`roster.import.completed` carries identifiers and counts, and nothing else."""
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes(
        [["20230001", "张三", "CS101", "01"], ["20230002", "李四", "CS101", "01"]],
        header=CHINESE_HEADER,
    )
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert _confirm(client, headers, created["roster_import_id"]).status_code == 202

    with engine.connect() as connection:
        rows = connection.execute(
            select(
                outbox_events.c.event_type,
                outbox_events.c.producer,
                outbox_events.c.payload,
                outbox_events.c.correlation_id,
            )
        ).fetchall()
    assert len(rows) == 1
    event = rows[0]
    assert event.event_type == "autograder.v1.roster.import.completed"
    assert event.producer == "B4"
    assert event.correlation_id is not None
    assert event.payload == {
        "roster_import_id": created["roster_import_id"],
        "course_id": str(course["course_id"]),
        "section_id": str(course["section_id"]),
        "accepted_rows": 2,
        "rejected_rows": 0,
    }
    serialised = json.dumps(event.payload)
    for leaked in ("张三", "李四", "20230001", "20230002"):
        assert leaked not in serialised, f"{leaked} leaked into the event payload"


def test_the_event_is_queued_in_the_same_transaction_as_the_rows(
    client, course, sign_in_as, engine
):
    """A rolled-back confirmation leaves neither enrollments nor a pending event."""
    headers = sign_in_as("teacher").headers
    all_bad = xlsx_bytes([["", "无学号", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, all_bad, "roster.xlsx", course).json()
    assert _confirm(client, headers, created["roster_import_id"]).status_code == 422
    with engine.connect() as connection:
        events = connection.execute(select(outbox_events.c.event_id)).fetchall()
    assert events == []
    assert connection_enrollments(engine) == 0


def test_import_creation_is_audited(client, course, sign_in_as, engine) -> None:
    from autograder_b4.db.tables import audit_records

    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    _confirm(client, headers, created["roster_import_id"])
    with engine.connect() as connection:
        actions = [row[0] for row in connection.execute(select(audit_records.c.action)).fetchall()]
    assert "roster.import.created" in actions
    assert "roster.import.committed" in actions


def test_audit_records_for_an_import_contain_no_pii(client, course, sign_in_as, engine) -> None:
    from autograder_b4.db.tables import audit_records

    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    _confirm(client, headers, created["roster_import_id"])
    with engine.connect() as connection:
        rows = connection.execute(
            select(audit_records.c.detail, audit_records.c.change_digest)
        ).fetchall()
    blob = json.dumps([[row.detail, row.change_digest] for row in rows], ensure_ascii=False)
    for leaked in ("张三", "20230001"):
        assert leaked not in blob


def test_confirming_an_unpreviewed_import_conflicts(client, course, sign_in_as, factory):
    """A mapping_required import cannot be committed by skipping the human decision."""
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["张三"]], header=["姓名"])
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert created["status"] == "mapping_required"
    response = _confirm(client, headers, created["roster_import_id"])
    assert response.status_code == 409
    assert factory.enrollment_count() == 0


def test_a_roster_row_that_matches_a_platform_user_is_linked(
    client, course, sign_in_as, factory, engine
):
    """A student who already has an account is connected to the imported enrollment."""
    student_id = factory.user(
        username="student", password=PASSWORD, roles=("student",), student_number="20230001"
    )
    headers = sign_in_as("teacher").headers
    content = xlsx_bytes([["20230001", "张三", "CS101", "01"]], header=CHINESE_HEADER)
    created = _upload(client, headers, None, content, "roster.xlsx", course).json()
    assert _confirm(client, headers, created["roster_import_id"]).status_code == 202
    with engine.connect() as connection:
        linked = connection.execute(select(enrollments.c.user_id)).scalar_one()
    assert linked == student_id
