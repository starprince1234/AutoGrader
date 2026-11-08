"""Assignment publication notifications (v0.32.0).

B3 publishes an assignment and hands the facts to B4, which owns the roster and the mail
channel. The rules that carry the risk here are asserted directly against real PostgreSQL:

* **"recorded" is not "delivered"** -- an unregistered student, an account without an
  address and a deactivated account each become a delivery row with their own reason, never
  a silent omission and never a `sent` without an SMTP accept;
* **the sender pass is resumable** -- a transport failure writes `failed` with a reason, an
  attempt count and a bounded back-off; an interrupted pass is recovered; a second pass
  never sends the same message twice;
* **the snapshot is immutable** -- materialising is idempotent by `assignment_id`, so a
  retrying caller cannot duplicate the audience or re-notify the course.

The SMTP transport is the one seam replaced: it is injected into `send_pass`, exactly the
parameter the production CLI fills with `mailer.send_assignment_published`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from autograder_b4.db.tables import (
    assignment_notification_deliveries as deliveries,
)
from autograder_b4.db.tables import (
    assignment_notifications as campaigns,
)
from autograder_b4.db.tables import users
from autograder_b4.notifications import MAX_DELIVERY_ATTEMPTS, STALE_CLAIM_MINUTES, send_pass
from autograder_b4.security.mailer import MailDeliveryError, MailNotConfigured

PASSWORD = "correct horse battery staple"
SERVICE_TOKEN = "test-service-token"
PUBLISHED_AT = datetime(2026, 11, 8, 4, 0, tzinfo=UTC)

SUMMARY_KEYS = {
    "assignment_id",
    "course_id",
    "status",
    "recipient_total",
    "sent_count",
    "pending_count",
    "failed_count",
    "skipped_unbound_count",
    "skipped_no_contact_count",
    "skipped_inactive_count",
    "created_at",
    "updated_at",
}

DELIVERY_KEYS = {
    "delivery_id",
    "student_number",
    "display_name",
    "section_id",
    "class_id",
    "user_id",
    "status",
    "reason_code",
    "reason_detail",
    "attempts",
    "sent_at",
    "updated_at",
}


# ------------------------------------------------------------------------------- helpers


def _service_headers() -> dict[str, str]:
    return {"authorization": f"Bearer {SERVICE_TOKEN}"}


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _materialise(
    client,
    course_id: uuid.UUID,
    *,
    assignment_id: uuid.UUID,
    title: str = "第一次作业",
    due_at: datetime | None = None,
    published_at: datetime = PUBLISHED_AT,
):
    payload: dict[str, object] = {
        "assignment_id": str(assignment_id),
        "course_id": str(course_id),
        "title": title,
        "due_at": _iso(due_at) if due_at is not None else None,
        "published_at": _iso(published_at),
    }
    return client.post(
        "/api/v1/internal/assignment-notifications",
        headers=_service_headers(),
        json=payload,
    )


def _teacher_view(client, headers, course_id: uuid.UUID, assignment_id: uuid.UUID):
    return client.get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}/notifications",
        headers=headers,
    )


def _campaign_row(engine, assignment_id: uuid.UUID):
    with engine.connect() as connection:
        return connection.execute(
            select(campaigns).where(campaigns.c.assignment_id == assignment_id)
        ).one()


def _delivery_row(engine, assignment_id: uuid.UUID, student_number: str):
    with engine.connect() as connection:
        return connection.execute(
            select(deliveries)
            .select_from(
                deliveries.join(
                    campaigns,
                    deliveries.c.notification_id == campaigns.c.notification_id,
                )
            )
            .where(
                campaigns.c.assignment_id == assignment_id,
                deliveries.c.student_number == student_number,
            )
        ).one()


def _delivery_count(engine, assignment_id: uuid.UUID) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                select(func.count())
                .select_from(
                    deliveries.join(
                        campaigns,
                        deliveries.c.notification_id == campaigns.c.notification_id,
                    )
                )
                .where(campaigns.c.assignment_id == assignment_id)
            ).scalar_one()
        )


def _collecting_sender() -> tuple[list[dict[str, object]], object]:
    sent: list[dict[str, object]] = []

    def sender(settings, **kwargs):
        sent.append(dict(kwargs))

    return sent, sender


def _refusing_sender(error: Exception):
    def sender(settings, **kwargs):
        raise error

    return sender


@pytest.fixture
def admin_headers(factory, sign_in_as) -> dict[str, str]:
    factory.user(username="root", password=PASSWORD, roles=("admin",))
    return sign_in_as("root").headers


@pytest.fixture
def audience(factory):
    """One course whose roster has exactly one recipient per delivery outcome."""
    teacher = factory.user(username="owner", password=PASSWORD, roles=("teacher",))
    course_id = factory.course(code="CS101", title="软件工程", created_by=teacher)
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01", term="2026F")
    class_id = factory.default_class(section_id)

    pending_user = factory.user(
        username="pending",
        password=PASSWORD,
        roles=("student",),
        display_name="待发学生",
        student_number="202400000001",
        email="pending@example.edu.cn",
    )
    no_contact_user = factory.user(
        username="no-contact",
        password=PASSWORD,
        roles=("student",),
        display_name="无邮箱学生",
        student_number="202400000002",
    )
    inactive_user = factory.user(
        username="inactive",
        password=PASSWORD,
        roles=("student",),
        display_name="停用学生",
        student_number="202400000003",
        email="inactive@example.edu.cn",
        active=False,
    )
    factory.enrollment(
        section_id=section_id,
        student_number="202400000001",
        display_name="待发学生",
        user_id=pending_user,
    )
    factory.enrollment(
        section_id=section_id,
        student_number="202400000002",
        display_name="无邮箱学生",
        user_id=no_contact_user,
    )
    factory.enrollment(
        section_id=section_id,
        student_number="202400000003",
        display_name="停用学生",
        user_id=inactive_user,
    )
    factory.enrollment(
        section_id=section_id,
        student_number="202400000004",
        display_name="未注册学生",
    )
    return {
        "teacher": teacher,
        "course_id": course_id,
        "section_id": section_id,
        "class_id": class_id,
        "pending_user": pending_user,
        "no_contact_user": no_contact_user,
        "inactive_user": inactive_user,
    }


# --------------------------------------------------------------------------- materialise


def test_materialise_snapshots_the_audience_with_explicit_skipped_reasons(
    client, audience, sign_in_as, engine
) -> None:
    assignment_id = uuid.uuid4()
    response = _materialise(client, audience["course_id"], assignment_id=assignment_id)
    assert response.status_code == 201, response.text
    summary = response.json()
    assert set(summary) == SUMMARY_KEYS
    assert summary["assignment_id"] == str(assignment_id)
    assert summary["course_id"] == str(audience["course_id"])
    assert summary["status"] == "pending"
    assert summary["recipient_total"] == 4
    assert summary["pending_count"] == 1
    assert summary["sent_count"] == 0
    assert summary["failed_count"] == 0
    assert summary["skipped_unbound_count"] == 1
    assert summary["skipped_no_contact_count"] == 1
    assert summary["skipped_inactive_count"] == 1

    view = _teacher_view(client, sign_in_as("owner").headers, audience["course_id"], assignment_id)
    assert view.status_code == 200, view.text
    body = view.json()
    assert set(body) == {"summary", "deliveries"}
    assert set(body["deliveries"]) == {"items", "next_cursor"}
    assert body["deliveries"]["next_cursor"] is None
    assert body["summary"]["recipient_total"] == 4
    items = body["deliveries"]["items"]
    assert len(items) == 4
    assert all(set(item) == DELIVERY_KEYS for item in items)

    by_number = {item["student_number"]: item for item in items}
    pending = by_number["202400000001"]
    assert pending["status"] == "pending"
    assert pending["reason_code"] is None
    assert pending["user_id"] == str(audience["pending_user"])
    assert pending["attempts"] == 0
    assert pending["sent_at"] is None
    assert pending["class_id"] == str(audience["class_id"])
    assert pending["display_name"] == "待发学生"

    assert by_number["202400000002"]["status"] == "skipped_no_contact"
    assert by_number["202400000002"]["reason_code"] == "no_email"
    assert by_number["202400000003"]["status"] == "skipped_inactive"
    assert by_number["202400000003"]["reason_code"] == "inactive_account"
    assert by_number["202400000004"]["status"] == "skipped_unbound"
    assert by_number["202400000004"]["reason_code"] == "no_account"
    assert by_number["202400000004"]["user_id"] is None
    for item in (by_number["202400000002"], by_number["202400000003"], by_number["202400000004"]):
        assert item["reason_detail"]
        assert item["sent_at"] is None

    assert _delivery_count(engine, assignment_id) == 4


def test_materialise_is_idempotent_and_refuses_a_different_course(
    client, audience, sign_in_as, factory, engine
) -> None:
    assignment_id = uuid.uuid4()
    first = _materialise(client, audience["course_id"], assignment_id=assignment_id)
    assert first.status_code == 201, first.text
    created_at = first.json()["created_at"]

    # A retry (B3 retries on a transient failure) returns the existing summary: same audience,
    # same campaign, no second set of delivery rows and no re-send.
    repeated = _materialise(
        client, audience["course_id"], assignment_id=assignment_id, title="改过的标题"
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created_at"] == created_at
    assert repeated.json()["recipient_total"] == 4
    assert _delivery_count(engine, assignment_id) == 4
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(campaigns)).scalar_one() == 1

    # The same assignment id under a different course is an inconsistency, not a rename.
    other_course = factory.course(code="CS102", title="别的课程")
    refused = _materialise(client, other_course, assignment_id=assignment_id)
    assert refused.status_code == 409
    assert _delivery_count(engine, assignment_id) == 4


def test_an_unknown_course_is_404_and_a_naive_timestamp_is_422(client) -> None:
    response = _materialise(client, uuid.uuid4(), assignment_id=uuid.uuid4())
    assert response.status_code == 404

    naive = client.post(
        "/api/v1/internal/assignment-notifications",
        headers=_service_headers(),
        json={
            "assignment_id": str(uuid.uuid4()),
            "course_id": str(uuid.uuid4()),
            "title": "作业",
            "published_at": "2026-11-08T04:00:00",
        },
    )
    assert naive.status_code == 422


def test_a_campaign_with_no_reachable_recipient_is_completed_not_delivered(
    client, factory
) -> None:
    teacher = factory.user(username="owner", password=PASSWORD, roles=("teacher",))
    course_id = factory.course(code="CS101", title="软件工程", created_by=teacher)
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    factory.enrollment(section_id=section_id, student_number="202400000001", display_name="未注册")

    response = _materialise(client, course_id, assignment_id=uuid.uuid4())
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["recipient_total"] == 1
    assert summary["sent_count"] == 0
    assert summary["skipped_unbound_count"] == 1
    # Nothing was sent, but there is nothing pending either: the counts tell the story.
    assert summary["status"] == "completed"


# ---------------------------------------------------------------------- teacher access


def test_the_teacher_view_and_retry_are_staff_only(
    client, audience, sign_in_as, factory, admin_headers
) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201

    # The enrolled student has course-read scope but no write scope: staff-only means 403.
    student_headers = sign_in_as("pending").headers
    assert _teacher_view(
        client, student_headers, audience["course_id"], assignment_id
    ).status_code == 403
    assert client.post(
        f"/api/v1/courses/{audience['course_id']}/assignments/{assignment_id}/notifications/retry",
        headers=student_headers,
    ).status_code == 403

    factory.user(username="outsider", password=PASSWORD, roles=("teacher",))
    outsider_headers = sign_in_as("outsider").headers
    assert _teacher_view(
        client, outsider_headers, audience["course_id"], assignment_id
    ).status_code == 403

    # Admin reaches every course.
    assert _teacher_view(
        client, admin_headers, audience["course_id"], assignment_id
    ).status_code == 200


def test_an_unscheduled_assignment_is_404_for_the_teacher(client, audience, sign_in_as) -> None:
    headers = sign_in_as("owner").headers
    unknown = uuid.uuid4()
    assert _teacher_view(client, headers, audience["course_id"], unknown).status_code == 404
    assert client.post(
        f"/api/v1/courses/{audience['course_id']}/assignments/{unknown}/notifications/retry",
        headers=headers,
    ).status_code == 404


def test_retry_rearms_only_failed_deliveries(
    client, audience, sign_in_as, engine, settings
) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201

    failing = _refusing_sender(MailDeliveryError("the mail server refused the message"))
    report = send_pass(engine, settings=settings, send_email=failing, now=PUBLISHED_AT)
    assert report.failed == 1
    failed_row = _delivery_row(engine, assignment_id, "202400000001")
    assert failed_row.status == "failed"
    assert _campaign_row(engine, assignment_id).status == "failed"

    headers = sign_in_as("owner").headers
    retried = client.post(
        f"/api/v1/courses/{audience['course_id']}/assignments/{assignment_id}/notifications/retry",
        headers=headers,
    )
    assert retried.status_code == 200, retried.text
    summary = retried.json()
    assert summary["failed_count"] == 0
    assert summary["pending_count"] == 1
    assert summary["skipped_unbound_count"] == 1
    assert summary["status"] == "pending"

    rearmed = _delivery_row(engine, assignment_id, "202400000001")
    assert rearmed.status == "pending"
    assert rearmed.attempts == 0
    assert rearmed.reason_code is None
    assert rearmed.reason_detail is None

    # The skipped rows are not retried: retrying cannot create an account, an address or
    # reactivate one, and pretending otherwise would hide the reason.
    assert _delivery_row(engine, assignment_id, "202400000002").status == "skipped_no_contact"
    assert _delivery_row(engine, assignment_id, "202400000003").status == "skipped_inactive"
    assert _delivery_row(engine, assignment_id, "202400000004").status == "skipped_unbound"

    # A second retry with nothing failed is a no-op that still returns the summary.
    again = client.post(
        f"/api/v1/courses/{audience['course_id']}/assignments/{assignment_id}/notifications/retry",
        headers=headers,
    )
    assert again.status_code == 200, again.text
    assert again.json()["pending_count"] == 1

    # Re-armed means the next sender pass really sends it: the retry is not just a status code.
    inbox, sender = _collecting_sender()
    report = send_pass(engine, settings=settings, send_email=sender)
    assert report.sent == 1
    assert len(inbox) == 1
    assert _delivery_row(engine, assignment_id, "202400000001").status == "sent"
    assert _campaign_row(engine, assignment_id).status == "partial"


# ------------------------------------------------------------------------------- sender


def test_send_pass_marks_sent_and_completes_a_fully_reachable_audience(
    client, factory, sign_in_as, engine, settings
) -> None:
    teacher = factory.user(username="owner", password=PASSWORD, roles=("teacher",))
    course_id = factory.course(code="CS101", title="软件工程", created_by=teacher)
    factory.grant(user_id=teacher, course_id=course_id, role="teacher")
    section_id = factory.section(course_id=course_id, code="01")
    for index in (1, 2):
        account = factory.user(
            username=f"student{index}",
            password=PASSWORD,
            roles=("student",),
            display_name=f"学生{index}",
            student_number=f"20240000000{index}",
            email=f"student{index}@example.edu.cn",
        )
        factory.enrollment(
            section_id=section_id,
            student_number=f"20240000000{index}",
            display_name=f"学生{index}",
            user_id=account,
        )

    assignment_id = uuid.uuid4()
    due_at = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert _materialise(
        client, course_id, assignment_id=assignment_id, title="第二次作业", due_at=due_at
    ).status_code == 201

    inbox, sender = _collecting_sender()
    report = send_pass(engine, settings=settings, send_email=sender, now=PUBLISHED_AT)
    assert report.claimed == 2
    assert report.sent == 2
    assert report.failed == 0
    assert len(inbox) == 2
    assert {message["to"] for message in inbox} == {
        "student1@example.edu.cn",
        "student2@example.edu.cn",
    }
    assert {message["course_title"] for message in inbox} == {"软件工程"}
    assert {message["assignment_title"] for message in inbox} == {"第二次作业"}
    assert {message["due_at"] for message in inbox} == {due_at}
    # The message names only its own recipient: no second student's data travels with it.
    assert {message["student_name"] for message in inbox} == {"学生1", "学生2"}

    campaign = _campaign_row(engine, assignment_id)
    assert campaign.status == "completed"

    # A second pass finds nothing to claim: a sent message is never sent twice.
    second = send_pass(engine, settings=settings, send_email=sender, now=PUBLISHED_AT)
    assert second.claimed == 0
    assert len(inbox) == 2

    view = _teacher_view(client, sign_in_as("owner").headers, course_id, assignment_id)
    assert view.status_code == 200, view.text
    summary = view.json()["summary"]
    assert summary["status"] == "completed"
    assert summary["sent_count"] == 2
    assert summary["pending_count"] == 0


def test_send_pass_reports_partial_when_some_recipients_could_not_be_reached(
    client, audience, engine, settings
) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201

    inbox, sender = _collecting_sender()
    report = send_pass(engine, settings=settings, send_email=sender, now=PUBLISHED_AT)
    assert report.claimed == 1
    assert report.sent == 1
    assert len(inbox) == 1

    sent_row = _delivery_row(engine, assignment_id, "202400000001")
    assert sent_row.status == "sent"
    assert sent_row.sent_at is not None
    assert sent_row.attempts == 1
    assert sent_row.reason_code is None
    assert _campaign_row(engine, assignment_id).status == "partial"


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (MailDeliveryError("the mail server refused the message"), "smtp_error"),
        (
            MailNotConfigured(
                "the mail channel is not configured (SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM)"
            ),
            "mail_not_configured",
        ),
    ],
)
def test_a_transport_failure_records_its_reason(
    client, audience, engine, settings, error, expected_reason
) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201

    report = send_pass(
        engine, settings=settings, send_email=_refusing_sender(error), now=PUBLISHED_AT
    )
    assert report.claimed == 1
    assert report.failed == 1

    row = _delivery_row(engine, assignment_id, "202400000001")
    assert row.status == "failed"
    assert row.reason_code == expected_reason
    assert row.reason_detail
    assert row.sent_at is None
    assert row.attempts == 1
    # Nothing was accepted by a server, so the campaign is failed even though it is `pending`
    # nowhere: the counts name the real outcome.
    assert _campaign_row(engine, assignment_id).status == "failed"


def test_a_failed_delivery_backs_off_exponentially(client, audience, engine, settings) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201
    failing = _refusing_sender(MailDeliveryError("refused"))
    start = PUBLISHED_AT

    first = send_pass(engine, settings=settings, send_email=failing, now=start)
    assert first.claimed == 1
    row = _delivery_row(engine, assignment_id, "202400000001")
    assert row.attempts == 1
    assert row.next_attempt_at == start + timedelta(seconds=60)

    # Not due yet: the retry window is respected instead of hammering the server.
    early = send_pass(
        engine, settings=settings, send_email=failing, now=start + timedelta(seconds=30)
    )
    assert early.claimed == 0

    # Second attempt in its window: the delay doubles (60s -> 120s).
    second_moment = start + timedelta(seconds=61)
    second = send_pass(engine, settings=settings, send_email=failing, now=second_moment)
    assert second.claimed == 1
    row = _delivery_row(engine, assignment_id, "202400000001")
    assert row.attempts == 2
    assert row.next_attempt_at == second_moment + timedelta(seconds=120)


def test_the_automatic_retry_budget_is_bounded(client, audience, engine, settings) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201
    failing = _refusing_sender(MailDeliveryError("refused"))
    with engine.begin() as connection:
        connection.execute(
            update(deliveries)
            .where(deliveries.c.student_number == "202400000001")
            .values(status="failed", attempts=MAX_DELIVERY_ATTEMPTS, next_attempt_at=None)
        )

    report = send_pass(engine, settings=settings, send_email=failing, now=PUBLISHED_AT)
    assert report.claimed == 0, "the sender must stop after the automatic attempt budget"


def test_a_stale_sending_claim_is_recovered(client, audience, engine, settings) -> None:
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201
    now = PUBLISHED_AT
    with engine.begin() as connection:
        connection.execute(
            update(deliveries)
            .where(deliveries.c.student_number == "202400000001")
            .values(
                status="sending",
                attempts=1,
                updated_at=now - timedelta(minutes=STALE_CLAIM_MINUTES + 1),
            )
        )

    inbox, sender = _collecting_sender()
    report = send_pass(engine, settings=settings, send_email=sender, now=now)
    assert report.recovered == 1
    assert report.claimed == 1
    assert report.sent == 1
    assert len(inbox) == 1
    assert _delivery_row(engine, assignment_id, "202400000001").status == "sent"


def test_an_account_that_lost_its_address_is_skipped_at_send_time(
    client, audience, engine, settings
) -> None:
    """The roster snapshot is not re-read; the account state is re-evaluated at claim time."""
    assignment_id = uuid.uuid4()
    assert _materialise(
        client, audience["course_id"], assignment_id=assignment_id
    ).status_code == 201
    with engine.begin() as connection:
        connection.execute(
            update(users)
            .where(users.c.user_id == audience["pending_user"])
            .values(email=None)
        )

    inbox, sender = _collecting_sender()
    report = send_pass(engine, settings=settings, send_email=sender, now=PUBLISHED_AT)
    assert report.claimed == 1
    assert report.skipped == 1
    assert report.sent == 0
    assert inbox == []
    row = _delivery_row(engine, assignment_id, "202400000001")
    assert row.status == "skipped_no_contact"
    assert row.reason_code == "no_email"
