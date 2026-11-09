"""Assignment publication notifications: recipients, delivery status and the sender pass.

Division of ownership (v0.32.0):

* **B3** owns assignments. When one is published it calls the internal endpoint here once;
  the call is idempotent by `assignment_id`, so a retry after a transient failure can
  neither duplicate recipients nor send a second message.
* **B4** owns the roster and the mail channel. This module snapshots the course's current
  active roster into one delivery row per student and records what actually happened.

The rule that shapes every status is: **"recorded" is not "delivered"**. A student with no
account (`skipped_unbound`), no e-mail address (`skipped_no_contact`) or a deactivated
account (`skipped_inactive`) is a delivery row with a reason -- the teacher sees the real
audience size and why somebody was not notified. `sent` is only written after the SMTP
server accepted the message; a transport failure writes `failed` with a reason code and a
bounded retry schedule.

The sender runs as its own process (`python -m autograder_b4.ops.notifications`), driven by
a systemd timer. Keeping SMTP out of the request path is what makes the endpoint fast and
the delivery resumable: a crash mid-pass leaves rows in `sending`, and the next pass
recovers stale claims before claiming new work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, and_, func, or_, select

from .db.tables import (
    assignment_notification_deliveries as deliveries,
)
from .db.tables import (
    assignment_notifications as campaigns,
)
from .db.tables import courses, enrollments, users
from .errors import not_found, unprocessable

#: Campaign status values (`b4.assignment_notifications.status`).
CAMPAIGN_PENDING = "pending"
CAMPAIGN_SENDING = "sending"
CAMPAIGN_COMPLETED = "completed"
CAMPAIGN_PARTIAL = "partial"
CAMPAIGN_FAILED = "failed"
CAMPAIGN_STATUSES = (
    CAMPAIGN_PENDING,
    CAMPAIGN_SENDING,
    CAMPAIGN_COMPLETED,
    CAMPAIGN_PARTIAL,
    CAMPAIGN_FAILED,
)

#: Campaign kinds (`b4.assignment_notifications.kind`). A publication announcement is one
#: per assignment (partial unique index); a reminder is one per teacher click, because each
#: campaign is the evidence of "I told this audience at this moment".
CAMPAIGN_KIND_PUBLICATION = "publication"
CAMPAIGN_KIND_REMINDER = "reminder"

#: Delivery status values (`b4.assignment_notification_deliveries.status`).
DELIVERY_PENDING = "pending"
DELIVERY_SENDING = "sending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"
DELIVERY_SKIPPED_UNBOUND = "skipped_unbound"
DELIVERY_SKIPPED_NO_CONTACT = "skipped_no_contact"
DELIVERY_SKIPPED_INACTIVE = "skipped_inactive"
DELIVERY_STATUSES = (
    DELIVERY_PENDING,
    DELIVERY_SENDING,
    DELIVERY_SENT,
    DELIVERY_FAILED,
    DELIVERY_SKIPPED_UNBOUND,
    DELIVERY_SKIPPED_NO_CONTACT,
    DELIVERY_SKIPPED_INACTIVE,
)

#: Why a delivery did not succeed. `smtp_error` / `mail_not_configured` are transport
#: failures and retryable; the three identity reasons are not fixable by retrying.
REASON_SMTP_ERROR = "smtp_error"
REASON_MAIL_NOT_CONFIGURED = "mail_not_configured"
REASON_NO_ACCOUNT = "no_account"
REASON_NO_EMAIL = "no_email"
REASON_INACTIVE_ACCOUNT = "inactive_account"
REASON_CODES = (
    REASON_SMTP_ERROR,
    REASON_MAIL_NOT_CONFIGURED,
    REASON_NO_ACCOUNT,
    REASON_NO_EMAIL,
    REASON_INACTIVE_ACCOUNT,
)

#: A transport failure is retried at most this many times by the sender pass. A teacher
#: retry (`retry_notification`) clears the attempt count, which is a deliberate human act.
MAX_DELIVERY_ATTEMPTS = 3
DEFAULT_SEND_LIMIT = 50
#: A claim older than this is assumed interrupted (process crash) and is recovered.
STALE_CLAIM_MINUTES = 10


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class SendReport:
    """One sender pass. Counts only -- no addresses, no names."""

    claimed: int
    sent: int
    failed: int
    skipped: int
    recovered: int

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "recovered": self.recovered,
        }


@dataclass(frozen=True)
class Materialised:
    summary: dict[str, object]
    created: bool


def _summary(connection: Connection, campaign) -> dict[str, object]:
    counts = dict.fromkeys(DELIVERY_STATUSES, 0)
    for status_value, count in connection.execute(
        select(deliveries.c.status, func.count())
        .where(deliveries.c.notification_id == campaign.notification_id)
        .group_by(deliveries.c.status)
    ).fetchall():
        counts[str(status_value)] = int(count)
    pending = counts[DELIVERY_PENDING]
    sending = counts[DELIVERY_SENDING]
    sent = counts[DELIVERY_SENT]
    failed = counts[DELIVERY_FAILED]
    return {
        "assignment_id": str(campaign.assignment_id),
        "course_id": str(campaign.course_id),
        "status": campaign.status,
        "recipient_total": int(campaign.recipient_total),
        "sent_count": sent,
        "pending_count": pending + sending,
        "failed_count": failed,
        "skipped_unbound_count": counts[DELIVERY_SKIPPED_UNBOUND],
        "skipped_no_contact_count": counts[DELIVERY_SKIPPED_NO_CONTACT],
        "skipped_inactive_count": counts[DELIVERY_SKIPPED_INACTIVE],
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
    }


def summary_for_assignment(
    connection: Connection, *, course_id: uuid.UUID, assignment_id: uuid.UUID
) -> dict[str, object] | None:
    """The campaign summary for one assignment, or `None` when it was never materialised."""
    campaign = _campaign(connection, course_id=course_id, assignment_id=assignment_id)
    if campaign is None:
        return None
    return _summary(connection, campaign)


def _campaign(
    connection: Connection,
    *,
    course_id: uuid.UUID,
    assignment_id: uuid.UUID,
    kind: str = CAMPAIGN_KIND_PUBLICATION,
):
    """The campaign of one assignment for one kind.

    Teacher reads and the retry path mean the *announcement* unless they say otherwise: a
    reminder has its own recipients and its own delivery evidence, so folding it into the
    announcement would make two different messages look like one.
    """
    return connection.execute(
        select(campaigns).where(
            and_(
                campaigns.c.assignment_id == assignment_id,
                campaigns.c.course_id == course_id,
                campaigns.c.kind == kind,
            )
        )
    ).fetchone()


def _require_campaign(
    connection: Connection,
    *,
    course_id: uuid.UUID,
    assignment_id: uuid.UUID,
    kind: str = CAMPAIGN_KIND_PUBLICATION,
):
    campaign = _campaign(
        connection, course_id=course_id, assignment_id=assignment_id, kind=kind
    )
    if campaign is None:
        raise not_found("No notification has been scheduled for this assignment.")
    return campaign


# ------------------------------------------------------------------------ materialise
def materialise(
    engine: Engine,
    *,
    assignment_id: uuid.UUID,
    course_id: uuid.UUID,
    title: str,
    due_at: datetime | None,
    published_at: datetime,
    now: datetime | None = None,
) -> Materialised:
    """Create the recipient snapshot for one published assignment (idempotent).

    Repeating the call returns the existing summary and an empty `created=False`: the
    audience is captured at publication time, so a later roster change does not silently
    turn an earlier notification into a different one.
    """
    moment = now or utcnow()
    clean_title = title.strip()
    if not clean_title:
        raise unprocessable("The assignment title must not be blank.")
    if len(clean_title) > 200:
        raise unprocessable("The assignment title must be at most 200 characters.")
    if published_at.tzinfo is None:
        raise unprocessable("published_at must carry a timezone.")

    with engine.begin() as connection:
        existing = connection.execute(
            select(campaigns).where(
                and_(
                    campaigns.c.assignment_id == assignment_id,
                    campaigns.c.kind == CAMPAIGN_KIND_PUBLICATION,
                )
            )
        ).fetchone()
        if existing is not None:
            return Materialised(_summary(connection, existing), False)
        course = connection.execute(
            select(courses.c.course_id).where(courses.c.course_id == course_id)
        ).fetchone()
        if course is None:
            raise not_found("The course does not exist.")

        rows = connection.execute(
            select(
                enrollments.c.enrollment_id,
                enrollments.c.student_number,
                enrollments.c.display_name,
                enrollments.c.section_id,
                enrollments.c.class_id,
                enrollments.c.user_id,
                users.c.email,
                users.c.is_active,
            )
            .select_from(
                enrollments.outerjoin(users, enrollments.c.user_id == users.c.user_id)
            )
            .where(
                and_(
                    enrollments.c.course_id == course_id,
                    enrollments.c.is_active.is_(True),
                )
            )
            .order_by(enrollments.c.student_number)
        ).fetchall()

        notification_id = uuid.uuid4()
        connection.execute(
            campaigns.insert().values(
                notification_id=notification_id,
                assignment_id=assignment_id,
                course_id=course_id,
                title=clean_title,
                due_at=due_at,
                published_at=published_at,
                kind=CAMPAIGN_KIND_PUBLICATION,
                status=CAMPAIGN_PENDING,
                recipient_total=len(rows),
                created_at=moment,
                updated_at=moment,
            )
        )
        if rows:
            connection.execute(
                deliveries.insert(),
                [
                    _delivery_values(
                        notification_id=notification_id,
                        row=row,
                        moment=moment,
                    )
                    for row in rows
                ],
            )
        campaign = connection.execute(
            select(campaigns).where(campaigns.c.notification_id == notification_id)
        ).fetchone()
        _refresh_campaign(connection, campaign, moment)
        campaign = connection.execute(
            select(campaigns).where(campaigns.c.notification_id == notification_id)
        ).fetchone()
        return Materialised(_summary(connection, campaign), True)


def materialise_reminder(
    engine: Engine,
    *,
    assignment_id: uuid.UUID,
    course_id: uuid.UUID,
    title: str,
    due_at: datetime | None,
    enrollment_ids: list[uuid.UUID],
    note: str | None = None,
    now: datetime | None = None,
) -> Materialised:
    """Create one reminder campaign for the given enrollments (a teacher's nudge).

    Two rules make this honest rather than convenient:

    * **the caller's recipient list is not trusted.** Every id must belong to the course's
      current active roster, otherwise the whole request is refused (``422``). Silently
      dropping an unknown id would let a stale page believe the reminder reached everyone,
      and sending to an id from another course would notify the wrong person.
    * **a reminder is not idempotent.** Each call is a new campaign: "I reminded them again
      on Tuesday" is a separate act with its own delivery evidence. That is why the
      publication announcement keeps its unique index and this path does not.

    The reachability rules are the same as the announcement's: no account, no address and a
    deactivated account are recorded as *skipped with a reason*, never as delivered.
    """
    moment = now or utcnow()
    clean_title = title.strip()
    if not clean_title:
        raise unprocessable("The assignment title must not be blank.")
    if len(clean_title) > 200:
        raise unprocessable("The assignment title must be at most 200 characters.")
    clean_note = (note or "").strip() or None
    if clean_note is not None and len(clean_note) > 500:
        raise unprocessable("The reminder note must be at most 500 characters.")
    wanted = list(dict.fromkeys(enrollment_ids))
    if not wanted:
        raise unprocessable("A reminder needs at least one recipient.")

    with engine.begin() as connection:
        course = connection.execute(
            select(courses.c.course_id).where(courses.c.course_id == course_id)
        ).fetchone()
        if course is None:
            raise not_found("The course does not exist.")

        rows = connection.execute(
            select(
                enrollments.c.enrollment_id,
                enrollments.c.student_number,
                enrollments.c.display_name,
                enrollments.c.section_id,
                enrollments.c.class_id,
                enrollments.c.user_id,
                users.c.email,
                users.c.is_active,
            )
            .select_from(
                enrollments.outerjoin(users, enrollments.c.user_id == users.c.user_id)
            )
            .where(
                and_(
                    enrollments.c.course_id == course_id,
                    enrollments.c.is_active.is_(True),
                    enrollments.c.enrollment_id.in_(wanted),
                )
            )
            .order_by(enrollments.c.student_number)
        ).fetchall()

        found = {row[0] for row in rows}
        missing = [str(value) for value in wanted if value not in found]
        if missing:
            # Refuse rather than partially send: a caller that believes it reminded 30 people
            # must not have silently reminded 27.
            raise unprocessable(
                "Some recipients are not on this course's current roster: "
                + ", ".join(sorted(missing))
            )

        notification_id = uuid.uuid4()
        connection.execute(
            campaigns.insert().values(
                notification_id=notification_id,
                assignment_id=assignment_id,
                course_id=course_id,
                title=clean_title,
                due_at=due_at,
                published_at=moment,
                kind=CAMPAIGN_KIND_REMINDER,
                note=clean_note,
                status=CAMPAIGN_PENDING,
                recipient_total=len(rows),
                created_at=moment,
                updated_at=moment,
            )
        )
        connection.execute(
            deliveries.insert(),
            [
                _delivery_values(notification_id=notification_id, row=row, moment=moment)
                for row in rows
            ],
        )
        campaign = connection.execute(
            select(campaigns).where(campaigns.c.notification_id == notification_id)
        ).fetchone()
        _refresh_campaign(connection, campaign, moment)
        campaign = connection.execute(
            select(campaigns).where(campaigns.c.notification_id == notification_id)
        ).fetchone()
        return Materialised(_summary(connection, campaign), True)


def _delivery_values(
    *, notification_id: uuid.UUID, row, moment: datetime
) -> dict[str, object]:
    """One recipient row: decide the initial status without guessing a contact address."""
    user_id = row[5]
    email = row[6]
    is_active = row[7]
    if user_id is None:
        status, reason = DELIVERY_SKIPPED_UNBOUND, REASON_NO_ACCOUNT
    elif is_active is False:
        status, reason = DELIVERY_SKIPPED_INACTIVE, REASON_INACTIVE_ACCOUNT
    elif not isinstance(email, str) or not email.strip():
        status, reason = DELIVERY_SKIPPED_NO_CONTACT, REASON_NO_EMAIL
    else:
        status, reason = DELIVERY_PENDING, None
    return {
        "delivery_id": uuid.uuid4(),
        "notification_id": notification_id,
        "enrollment_id": row[0],
        "student_number": str(row[1]),
        "display_name": str(row[2]),
        "section_id": row[3],
        "class_id": row[4],
        "user_id": user_id,
        "status": status,
        "reason_code": reason,
        "reason_detail": _reason_detail(status),
        "attempts": 0,
        "next_attempt_at": None,
        "sent_at": None,
        "created_at": moment,
        "updated_at": moment,
    }


def _reason_detail(status: str) -> str | None:
    if status == DELIVERY_SKIPPED_UNBOUND:
        return "学生尚未注册账号, 未发送"
    if status == DELIVERY_SKIPPED_NO_CONTACT:
        return "账号没有可用邮箱地址, 未发送"
    if status == DELIVERY_SKIPPED_INACTIVE:
        return "账号已停用, 未发送"
    return None


def _refresh_campaign(connection: Connection, campaign, now: datetime) -> None:
    """Recompute the campaign status from the delivery rows (never from a caller's claim)."""
    counts: dict[str, int] = {}
    for status_value, count in connection.execute(
        select(deliveries.c.status, func.count())
        .where(deliveries.c.notification_id == campaign.notification_id)
        .group_by(deliveries.c.status)
    ).fetchall():
        counts[str(status_value)] = int(count)
    pending = counts.get(DELIVERY_PENDING, 0)
    sending = counts.get(DELIVERY_SENDING, 0)
    sent = counts.get(DELIVERY_SENT, 0)
    failed = counts.get(DELIVERY_FAILED, 0)
    skipped = sum(
        counts.get(status, 0)
        for status in (
            DELIVERY_SKIPPED_UNBOUND,
            DELIVERY_SKIPPED_NO_CONTACT,
            DELIVERY_SKIPPED_INACTIVE,
        )
    )
    if pending > 0:
        status = CAMPAIGN_PENDING
    elif sending > 0:
        status = CAMPAIGN_SENDING
    elif failed > 0 and sent == 0:
        status = CAMPAIGN_FAILED
    elif (failed > 0 or skipped > 0) and sent > 0:
        status = CAMPAIGN_PARTIAL
    else:
        status = CAMPAIGN_COMPLETED
    connection.execute(
        campaigns.update()
        .where(campaigns.c.notification_id == campaign.notification_id)
        .values(status=status, updated_at=now)
    )


# ------------------------------------------------------------------------------- retry
def retry_failed(
    engine: Engine, *, course_id: uuid.UUID, assignment_id: uuid.UUID, now: datetime | None = None
) -> dict[str, object]:
    """Re-arm transport failures; skipped identity reasons are deliberately left alone."""
    moment = now or utcnow()
    with engine.begin() as connection:
        campaign = _require_campaign(
            connection, course_id=course_id, assignment_id=assignment_id
        )
        connection.execute(
            deliveries.update()
            .where(
                and_(
                    deliveries.c.notification_id == campaign.notification_id,
                    deliveries.c.status == DELIVERY_FAILED,
                )
            )
            .values(
                status=DELIVERY_PENDING,
                attempts=0,
                next_attempt_at=None,
                reason_code=None,
                reason_detail=None,
                updated_at=moment,
            )
        )
        _refresh_campaign(connection, campaign, moment)
        campaign = connection.execute(
            select(campaigns).where(campaigns.c.notification_id == campaign.notification_id)
        ).fetchone()
        return _summary(connection, campaign)


# ------------------------------------------------------------------------------ sender
def send_pass(
    engine: Engine,
    *,
    settings,
    send_email,
    limit: int = DEFAULT_SEND_LIMIT,
    now: datetime | None = None,
) -> SendReport:
    """Send one bounded batch; `send_email` is the mailer seam (injectable for tests).

    Claiming uses `FOR UPDATE SKIP LOCKED` so two timer ticks cannot send the same message.
    The claim is committed before SMTP runs; if the process dies mid-send the row stays
    `sending` and the next pass recovers it after `STALE_CLAIM_MINUTES` instead of losing
    it. Failures are recorded per row; the pass itself never raises for a delivery error.
    """
    moment = now or utcnow()
    recovered = _recover_stale_claims(engine, now=moment)
    claimed = _claim(engine, limit=limit, now=moment)
    if not claimed:
        return SendReport(claimed=0, sent=0, failed=0, skipped=0, recovered=recovered)

    course_titles = _course_titles(engine, [row.course_id for row in claimed])
    results: dict[uuid.UUID, dict[str, object]] = {}
    sent = failed = skipped = 0
    for row in claimed:
        if row.status_after_claim in (DELIVERY_SKIPPED_UNBOUND, DELIVERY_SKIPPED_NO_CONTACT):
            # Re-evaluated at claim time: the account may have been removed since publish.
            results[row.delivery_id] = {
                "status": row.status_after_claim,
                "reason_code": row.reason_after_claim,
                "reason_detail": _reason_detail(row.status_after_claim),
                "sent_at": None,
            }
            skipped += 1
            continue
        try:
            if row.kind == CAMPAIGN_KIND_REMINDER:
                send_email(
                    settings,
                    to=row.email,
                    student_name=row.display_name,
                    course_title=course_titles.get(row.course_id, ""),
                    assignment_title=row.title,
                    due_at=row.due_at,
                    note=row.note,
                )
            else:
                send_email(
                    settings,
                    to=row.email,
                    student_name=row.display_name,
                    course_title=course_titles.get(row.course_id, ""),
                    assignment_title=row.title,
                    due_at=row.due_at,
                )
        except Exception as error:  # noqa: BLE001 - translated to a status, never hidden
            reason = _failure_reason(error)
            results[row.delivery_id] = {
                "status": DELIVERY_FAILED,
                "reason_code": reason,
                "reason_detail": _failure_detail(reason),
                "sent_at": None,
            }
            failed += 1
        else:
            results[row.delivery_id] = {
                "status": DELIVERY_SENT,
                "reason_code": None,
                "reason_detail": None,
                "sent_at": moment,
            }
            sent += 1
    _record_results(engine, rows=claimed, results=results, now=moment)
    return SendReport(
        claimed=len(claimed),
        sent=sent,
        failed=failed,
        skipped=skipped,
        recovered=recovered,
    )


@dataclass(frozen=True)
class _Claimed:
    delivery_id: uuid.UUID
    notification_id: uuid.UUID
    status_after_claim: str
    reason_after_claim: str | None
    attempts: int
    student_number: str
    display_name: str
    user_id: uuid.UUID | None
    email: str | None
    course_id: uuid.UUID
    title: str
    due_at: datetime | None
    kind: str
    note: str | None


def _claim(engine: Engine, *, limit: int, now: datetime) -> list[_Claimed]:
    with engine.begin() as connection:
        rows = connection.execute(
            select(
                deliveries.c.delivery_id,
                deliveries.c.notification_id,
                deliveries.c.attempts,
                deliveries.c.student_number,
                deliveries.c.display_name,
                deliveries.c.user_id,
                users.c.email,
                users.c.is_active,
                campaigns.c.course_id,
                campaigns.c.title,
                campaigns.c.due_at,
                campaigns.c.kind,
                campaigns.c.note,
            )
            .select_from(
                deliveries.join(
                    campaigns, deliveries.c.notification_id == campaigns.c.notification_id
                ).outerjoin(users, deliveries.c.user_id == users.c.user_id)
            )
            .where(
                or_(
                    deliveries.c.status == DELIVERY_PENDING,
                    and_(
                        deliveries.c.status == DELIVERY_FAILED,
                        deliveries.c.attempts < MAX_DELIVERY_ATTEMPTS,
                        or_(
                            deliveries.c.next_attempt_at.is_(None),
                            deliveries.c.next_attempt_at <= now,
                        ),
                    ),
                )
            )
            .order_by(deliveries.c.created_at, deliveries.c.delivery_id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=deliveries)
        ).fetchall()
        for row in rows:
            connection.execute(
                deliveries.update()
                .where(deliveries.c.delivery_id == row.delivery_id)
                .values(
                    status=DELIVERY_SENDING,
                    attempts=row.attempts + 1,
                    updated_at=now,
                )
            )
    claimed: list[_Claimed] = []
    for row in rows:
        status_after, reason_after = _resolve_identity(row[5], row[6], row[7])
        claimed.append(
            _Claimed(
                delivery_id=row[0],
                notification_id=row[1],
                status_after_claim=status_after,
                reason_after_claim=reason_after,
                attempts=int(row[2]),
                student_number=str(row[3]),
                display_name=str(row[4]),
                user_id=row[5],
                email=row[6],
                course_id=row[8],
                title=row[9],
                due_at=row[10],
                kind=str(row[11]),
                note=row[12],
            )
        )
    return claimed


def _resolve_identity(
    user_id: uuid.UUID | None, email: str | None, is_active: bool | None
) -> tuple[str, str | None]:
    if user_id is None:
        return DELIVERY_SKIPPED_UNBOUND, REASON_NO_ACCOUNT
    if is_active is False:
        return DELIVERY_SKIPPED_INACTIVE, REASON_INACTIVE_ACCOUNT
    if not isinstance(email, str) or not email.strip():
        return DELIVERY_SKIPPED_NO_CONTACT, REASON_NO_EMAIL
    return DELIVERY_PENDING, None


def _recover_stale_claims(engine: Engine, *, now: datetime) -> int:
    cutoff = now - timedelta(minutes=STALE_CLAIM_MINUTES)
    with engine.begin() as connection:
        result = connection.execute(
            deliveries.update()
            .where(
                and_(
                    deliveries.c.status == DELIVERY_SENDING,
                    deliveries.c.updated_at < cutoff,
                )
            )
            .values(
                status=DELIVERY_PENDING,
                next_attempt_at=None,
                updated_at=now,
            )
            .returning(deliveries.c.delivery_id)
        )
        return len(result.fetchall())


def _course_titles(engine: Engine, course_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not course_ids:
        return {}
    with engine.connect() as connection:
        rows = connection.execute(
            select(courses.c.course_id, courses.c.title).where(
                courses.c.course_id.in_(sorted(set(course_ids), key=str))
            )
        ).fetchall()
    return {row[0]: str(row[1]) for row in rows}


def _record_results(
    engine: Engine,
    *,
    rows: list[_Claimed],
    results: dict[uuid.UUID, dict[str, object]],
    now: datetime,
) -> None:
    retry_base = now
    by_delivery = {row.delivery_id: row for row in rows}
    with engine.begin() as connection:
        touched: set[uuid.UUID] = set()
        for delivery_id, result in results.items():
            claimed = by_delivery[delivery_id]
            status = str(result["status"])
            next_attempt_at = None
            if status == DELIVERY_FAILED:
                # `claimed.attempts` is the pre-claim count, so the attempt that just failed
                # is number `claimed.attempts + 1`; backoff is 60s, 120s, 240s for the three
                # automatic attempts, and the third is the last one.
                delay = timedelta(seconds=60 * 2 ** max(0, claimed.attempts))
                next_attempt_at = retry_base + delay
            connection.execute(
                deliveries.update()
                .where(deliveries.c.delivery_id == delivery_id)
                .values(
                    status=status,
                    reason_code=result["reason_code"],
                    reason_detail=result["reason_detail"],
                    next_attempt_at=next_attempt_at,
                    sent_at=result["sent_at"],
                    updated_at=now,
                )
            )
            touched.add(claimed.notification_id)
        for notification_id in touched:
            campaign = connection.execute(
                select(campaigns).where(campaigns.c.notification_id == notification_id)
            ).fetchone()
            if campaign is not None:
                _refresh_campaign(connection, campaign, now)


def _failure_reason(error: Exception) -> str:
    from .security.mailer import MailNotConfigured

    if isinstance(error, MailNotConfigured):
        return REASON_MAIL_NOT_CONFIGURED
    return REASON_SMTP_ERROR


def _failure_detail(reason: str) -> str:
    if reason == REASON_MAIL_NOT_CONFIGURED:
        return "邮件通道未配置(SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_FROM)"
    return "SMTP 发送失败(已记录失败, 稍后按退避重试; 教师也可手动重试)"


# ------------------------------------------------------------------------ teacher reads
def campaign_for_teacher(
    connection: Connection, *, course_id: uuid.UUID, assignment_id: uuid.UUID
) -> dict[str, object]:
    campaign = _require_campaign(connection, course_id=course_id, assignment_id=assignment_id)
    return _summary(connection, campaign)


def notification_id_of(
    connection: Connection, *, course_id: uuid.UUID, assignment_id: uuid.UUID
) -> uuid.UUID:
    return _require_campaign(
        connection, course_id=course_id, assignment_id=assignment_id
    ).notification_id


def deliveries_filter(notification_id: uuid.UUID):
    return deliveries.c.notification_id == notification_id


__all__ = [
    "CAMPAIGN_KIND_PUBLICATION",
    "CAMPAIGN_KIND_REMINDER",
    "CAMPAIGN_STATUSES",
    "DELIVERY_STATUSES",
    "MAX_DELIVERY_ATTEMPTS",
    "REASON_CODES",
    "Materialised",
    "SendReport",
    "campaign_for_teacher",
    "deliveries",
    "deliveries_filter",
    "materialise",
    "materialise_reminder",
    "notification_id_of",
    "retry_failed",
    "send_pass",
    "summary_for_assignment",
]
