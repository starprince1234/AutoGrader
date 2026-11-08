"""Self-service registration: allowlist, verification code, account creation.

The suite is integration-level on purpose: registration writes three tables, two unique
indexes and a roster link in one transaction, and "the code was mailed" is only meaningful if
the row that proves it actually landed. The mail transport is the one seam replaced here -- and
it is replaced at `mailer.send_verification_code`, the module attribute the endpoint resolves at
call time, so the rest of the path (digest storage, attempt counting, single use) is real.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from autograder_b4.db import tables
from autograder_b4.security import mailer

PASSWORD = "correct horse battery staple"


class Mailbox:
    """Captures what the endpoint asked to send, and the code it asked to send."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def __call__(self, settings, *, to: str, code: str, ttl_seconds: int) -> None:
        self.sent.append({"to": to, "code": code, "ttl_seconds": ttl_seconds})

    @property
    def code(self) -> str:
        assert self.sent, "no verification mail was requested"
        return str(self.sent[-1]["code"])


@pytest.fixture
def mailbox(monkeypatch) -> Mailbox:
    box = Mailbox()
    monkeypatch.setattr(mailer, "send_verification_code", box)
    return box


def _admin(client, factory) -> dict[str, str]:
    factory.user(username="root", password=PASSWORD, roles=("admin",))
    response = client.post(
        "/api/v1/auth/session", json={"username": "root", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _upload_allowlist(client, headers, rows: list[tuple[str, str]], *, filename: str = "名单.csv"):
    content = ("姓名,学号\n" + "".join(f"{name},{number}\n" for name, number in rows)).encode("gbk")
    return client.post(
        "/api/v1/registration-allowlists",
        files={"file": (filename, content, "text/csv")},
        headers=headers,
    )


def _payload(**overrides) -> dict[str, str]:
    body = {
        "username": "alice",
        "email": "alice@example.edu.cn",
        "student_number": "2024141460472",
        "display_name": "付明煜",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------- allowlist
def test_allowlist_upload_is_gbk_tolerant_and_replaces_the_same_class(client, factory) -> None:
    """Each upload is one registered class; re-uploading the same file updates that class."""
    headers = _admin(client, factory)

    first = _upload_allowlist(
        client, headers, [("付明煜", "2024141460472"), ("黄俊杰", "2024141460192")]
    )
    assert first.status_code == 200, first.text
    batch_id = first.json()["batch_id"]
    assert first.json()["class_name"] == "名单"
    assert first.json()["row_count"] == 2
    assert first.json()["skipped_row_count"] == 0
    assert first.json()["replaced"] is False
    assert len(first.json()["content_sha256"]) == 64

    second = _upload_allowlist(client, headers, [("付明煜", "2024141460472")])
    assert second.status_code == 200, second.text
    assert second.json()["batch_id"] == batch_id, "the same file name is the same class"
    assert second.json()["replaced"] is True
    assert second.json()["row_count"] == 1

    summary = client.get("/api/v1/registration-allowlists", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["row_count"] == 1
    assert summary.json()["filename"] == "名单.csv"
    assert summary.json()["batch_id"] == batch_id


def test_several_classes_coexist_and_the_same_student_may_be_on_each(
    client, factory, mailbox
) -> None:
    """The allowlist is per imported class, not a global snapshot of the platform.

    v0.32.0 relaxed the old global `UNIQUE (student_number)`: a student who takes two courses
    is on the class list of each, and importing the second class must not silently drop them.
    """
    headers = _admin(client, factory)
    first = _upload_allowlist(
        client, headers, [("付明煜", "2024141460472")], filename="一班.csv"
    )
    second = _upload_allowlist(
        client, headers, [("付明煜", "2024141460472")], filename="二班.csv"
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["batch_id"] != second.json()["batch_id"]
    assert second.json()["row_count"] == 1

    batches = client.get("/api/v1/registration-allowlists/batches", headers=headers).json()
    classes = {item["class_name"]: item for item in batches["items"]}
    assert set(classes) == {"一班", "二班"}
    for item in classes.values():
        entries = client.get(
            f"/api/v1/registration-allowlists/batches/{item['batch_id']}/entries",
            headers=headers,
        )
        assert entries.status_code == 200
        assert [entry["student_number"] for entry in entries.json()["items"]] == [
            "2024141460472"
        ]

    # And the identity can still register: being on two class lists is not a duplicate row.
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202


def test_allowlist_upload_requires_an_administrator(client, factory) -> None:
    factory.user(username="teacher", password=PASSWORD, roles=("teacher",))
    token = client.post(
        "/api/v1/auth/session", json={"username": "teacher", "password": PASSWORD}
    ).json()["access_token"]
    response = _upload_allowlist(
        client, {"Authorization": f"Bearer {token}"}, [("付明煜", "2024141460472")]
    )
    assert response.status_code == 403


def test_allowlist_rejects_a_wrong_shape(client, factory) -> None:
    headers = _admin(client, factory)

    no_header = client.post(
        "/api/v1/registration-allowlists",
        files={"file": ("名单.csv", "付明煜,2024141460472\n".encode(), "text/csv")},
        headers=headers,
    )
    assert no_header.status_code == 422

    short_number = _upload_allowlist(client, headers, [("付明煜", "2024141")])
    assert short_number.status_code == 422
    assert "13" in short_number.json()["detail"]

    duplicated = _upload_allowlist(
        client, headers, [("付明煜", "2024141460472"), ("黄俊杰", "2024141460472")]
    )
    assert duplicated.status_code == 422

    wrong_suffix = client.post(
        "/api/v1/registration-allowlists",
        files={"file": ("名单.xlsx", b"whatever", "application/vnd.ms-excel")},
        headers=headers,
    )
    assert wrong_suffix.status_code == 422


# ---------------------------------------------------------------------------- code request
def test_code_is_sent_only_for_an_allowlisted_identity(client, factory, mailbox) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200

    accepted = client.post("/api/v1/auth/registrations/code", json=_payload())
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["expires_in_seconds"] > 0
    assert mailbox.sent[-1]["to"] == "alice@example.edu.cn"
    assert len(mailbox.code) == 6 and mailbox.code.isdigit()

    # Only a digest is stored; the code itself must not be recoverable from the row.
    with factory.engine.connect() as connection:
        row = connection.execute(select(tables.registration_codes)).fetchone()
    assert row is not None
    assert row.code_hash != mailbox.code
    assert len(row.code_hash) == 64
    assert row.attempts == 0

    mismatch = client.post(
        "/api/v1/auth/registrations/code", json=_payload(display_name="别的名字")
    )
    assert mismatch.status_code == 422
    assert "名单" in mismatch.json()["detail"]


def test_code_request_refuses_taken_identities(client, factory, mailbox) -> None:
    headers = _admin(client, factory)
    assert (
        _upload_allowlist(
            client,
            headers,
            [("付明煜", "2024141460472"), ("黄俊杰", "2024141460192"), ("胡良均", "2024141460230")],
        ).status_code
        == 200
    )

    factory.user(username="alice", password=PASSWORD)
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 409

    factory.user(username="bob", password=PASSWORD, email="bob@example.edu.cn")
    taken_email = client.post(
        "/api/v1/auth/registrations/code",
        json=_payload(username="bobby", email="Bob@Example.edu.cn "),
    )
    assert taken_email.status_code == 409

    factory.user(username="carol", password=PASSWORD, student_number="2024141460192")
    taken_number = client.post(
        "/api/v1/auth/registrations/code",
        json=_payload(
            username="carol2",
            email="carol2@example.edu.cn",
            student_number="2024141460192",
        ),
    )
    assert taken_number.status_code == 409


def test_code_request_is_rate_limited_per_mailbox(client, factory, mailbox) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200

    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202
    again = client.post("/api/v1/auth/registrations/code", json=_payload())
    assert again.status_code == 429
    assert "秒" in again.json()["detail"]


def test_missing_mail_channel_is_503_not_a_skipped_step(client, factory, monkeypatch) -> None:
    """Without a working sender the endpoint must refuse.

    Accepting the address unverified is the bug this asserts against, so the failure is
    injected at the seam the endpoint calls and the database is then checked for leftovers.
    """

    def unavailable(*args, **kwargs):
        raise mailer.MailDeliveryError("the registration mail channel is not configured")

    monkeypatch.setattr(mailer, "send_verification_code", unavailable)

    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200

    response = client.post("/api/v1/auth/registrations/code", json=_payload())
    assert response.status_code == 503
    with factory.engine.connect() as connection:
        assert connection.execute(select(tables.registration_codes)).fetchone() is None


# ---------------------------------------------------------------------------- completion
def test_registration_creates_a_usable_account_and_links_the_roster(
    client, factory, mailbox
) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200
    course_id = factory.course(code="SE-2026", title="软件工程")
    section_id = factory.section(course_id=course_id, code="01")
    enrollment_id = factory.enrollment(
        section_id=section_id, student_number="2024141460472", display_name="付明煜"
    )

    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202
    created = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": PASSWORD},
    )
    assert created.status_code == 201, created.text
    user_id = uuid.UUID(created.json()["user_id"])
    body = created.json()
    assert set(body) == {
        "user_id",
        "username",
        "bound_course_count",
        "identity_mismatch_count",
    }
    assert body["username"] == "alice"
    assert body["bound_course_count"] == 1
    assert body["identity_mismatch_count"] == 0

    with factory.engine.connect() as connection:
        user = connection.execute(
            select(tables.users).where(tables.users.c.user_id == user_id)
        ).fetchone()
        credential = connection.execute(
            select(tables.credentials).where(tables.credentials.c.user_id == user_id)
        ).fetchone()
        role = connection.execute(
            select(tables.roles.c.role).where(tables.roles.c.user_id == user_id)
        ).scalar_one()
        linked = connection.execute(
            select(tables.enrollments.c.user_id).where(
                tables.enrollments.c.enrollment_id == enrollment_id
            )
        ).scalar_one()
    assert user.created_via == "self"
    assert user.student_number == "2024141460472"
    assert user.email == "alice@example.edu.cn"
    assert credential.password_hash.startswith("$argon2id$")
    assert role == "student"
    assert linked == user_id, "the roster row must be linked during registration"

    # And the account works through the ordinary login endpoint: username + password only.
    signed_in = client.post(
        "/api/v1/auth/session", json={"username": "alice", "password": PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text

    # The code is single use: replaying it fails and creates nothing.
    replay = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": PASSWORD},
    )
    assert replay.status_code == 422


def test_a_wrong_code_counts_as_an_attempt_and_the_right_one_still_works(
    client, factory, mailbox
) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202
    wrong = f"{(int(mailbox.code) + 1) % 1_000_000:06d}"

    refused = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": wrong, "password": PASSWORD},
    )
    assert refused.status_code == 422
    with factory.engine.connect() as connection:
        assert connection.execute(select(tables.registration_codes.c.attempts)).scalar_one() == 1

    # One wrong attempt must not lock the account out: the right code still works.
    accepted = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": PASSWORD},
    )
    assert accepted.status_code == 201, accepted.text


def test_exhausted_attempts_require_a_new_code(client, factory, mailbox) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202
    wrong = f"{(int(mailbox.code) + 1) % 1_000_000:06d}"

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/registrations",
            json={"email": "alice@example.edu.cn", "code": wrong, "password": PASSWORD},
        )
        assert response.status_code == 422

    blocked = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": PASSWORD},
    )
    assert blocked.status_code == 429


def test_registration_rechecks_the_allowlist_before_creating(client, factory, mailbox) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202

    # The administrator replaces the list while the code is still valid.
    assert _upload_allowlist(client, headers, [("黄俊杰", "2024141460192")]).status_code == 200

    refused = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": PASSWORD},
    )
    assert refused.status_code == 422
    with factory.engine.connect() as connection:
        assert connection.execute(select(tables.users)).first() is not None  # root only
        assert (
            connection.execute(
                select(tables.users.c.user_id).where(tables.users.c.username == "alice")
            ).first()
            is None
        )


def test_registration_rejects_a_short_password(client, factory, mailbox) -> None:
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202

    response = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": "short"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------- roster binding
def _register(client, mailbox) -> tuple[uuid.UUID, dict[str, object]]:
    assert client.post("/api/v1/auth/registrations/code", json=_payload()).status_code == 202
    created = client.post(
        "/api/v1/auth/registrations",
        json={"email": "alice@example.edu.cn", "code": mailbox.code, "password": PASSWORD},
    )
    assert created.status_code == 201, created.text
    return uuid.UUID(created.json()["user_id"]), created.json()


def test_registration_binds_every_course_the_student_is_on(client, factory, mailbox) -> None:
    """A student may take several courses; the account is linked to all of those rows."""
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200

    enrollment_ids = []
    for code in ("SE-2026", "CS-2026"):
        course_id = factory.course(code=code, title=f"课程 {code}")
        section_id = factory.section(course_id=course_id, code="01")
        enrollment_ids.append(
            factory.enrollment(
                section_id=section_id,
                student_number="2024141460472",
                display_name="付明煜",
            )
        )

    user_id, body = _register(client, mailbox)
    assert body["bound_course_count"] == 2
    assert body["identity_mismatch_count"] == 0

    with factory.engine.connect() as connection:
        linked = connection.execute(
            select(tables.enrollments.c.user_id).where(
                tables.enrollments.c.enrollment_id.in_(enrollment_ids)
            )
        ).scalars().all()
    assert len(linked) == 2
    assert set(linked) == {user_id}


def test_a_roster_row_with_a_different_name_is_counted_and_not_bound(
    client, factory, mailbox
) -> None:
    """Binding by student number alone would merge two people; the count says so instead."""
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200

    matching_course = factory.course(code="SE-2026", title="软件工程")
    matching_section = factory.section(course_id=matching_course, code="01")
    matching = factory.enrollment(
        section_id=matching_section, student_number="2024141460472", display_name="付明煜"
    )
    other_course = factory.course(code="CS-2026", title="计算机")
    other_section = factory.section(course_id=other_course, code="01")
    mismatched = factory.enrollment(
        section_id=other_section, student_number="2024141460472", display_name="别的名字"
    )

    user_id, body = _register(client, mailbox)
    assert body["bound_course_count"] == 1
    assert body["identity_mismatch_count"] == 1

    with factory.engine.connect() as connection:
        rows = {
            row.enrollment_id: row.user_id
            for row in connection.execute(
                select(tables.enrollments.c.enrollment_id, tables.enrollments.c.user_id).where(
                    tables.enrollments.c.enrollment_id.in_([matching, mismatched])
                )
            ).fetchall()
        }
    assert rows[matching] == user_id
    assert rows[mismatched] is None


def test_a_registration_before_the_roster_import_is_bound_when_the_roster_arrives(
    client, factory, mailbox, sign_in_as
) -> None:
    """Register, then import: the confirmed roster row links to the existing account."""
    headers = _admin(client, factory)
    assert _upload_allowlist(client, headers, [("付明煜", "2024141460472")]).status_code == 200
    user_id, body = _register(client, mailbox)
    assert body["bound_course_count"] == 0
    assert body["identity_mismatch_count"] == 0

    teacher = factory.user(username="teacher", password=PASSWORD, roles=("teacher",))
    teacher_headers = sign_in_as("teacher").headers
    course_id = factory.course(code="SE-2026", title="软件工程")
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section = client.post(
        f"/api/v1/courses/{course_id}/sections",
        headers=teacher_headers,
        json={"section_code": "01"},
    )
    assert section.status_code == 201, section.text
    section_id = section.json()["section_id"]
    klass = client.post(
        f"/api/v1/sections/{section_id}/classes",
        headers=teacher_headers,
        json={"class_name": "一班"},
    )
    assert klass.status_code == 201, klass.text

    content = "student_number,display_name\n2024141460472,付明煜\n".encode()
    uploaded = client.post(
        "/api/v1/roster-imports",
        headers=teacher_headers,
        files={"file": ("roster.csv", content, "text/csv")},
        data={
            "course_id": str(course_id),
            "section_id": section_id,
            "class_id": klass.json()["class_id"],
        },
    )
    assert uploaded.status_code == 202, uploaded.text
    confirmed = client.post(
        f"/api/v1/roster-imports/{uploaded.json()['roster_import_id']}/confirm",
        headers=teacher_headers,
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["status"] == "committed"

    with factory.engine.connect() as connection:
        linked = connection.execute(
            select(tables.enrollments.c.user_id).where(
                tables.enrollments.c.course_id == course_id
            )
        ).scalar_one()
    assert linked == user_id
