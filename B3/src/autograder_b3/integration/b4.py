"""Outbound service-to-service clients for the identity/course module (B4).

Three calls are made from B3, all with the pre-shared service credential
(``B3_B4_BASE_URL`` + ``B3_B4_SERVICE_TOKEN``, a service token -- never a user token):

* ``GET /api/v1/internal/users/{user_id}/authorization`` -- the **authoritative** current
  roles and course enrollment of one account (``MAIN/contracts/openapi/b4.yaml``). The
  token's ``scope`` claim is only a snapshot taken when it was issued: a student added to a
  course after sign-in would be invisible to the token, and a removed student would keep
  access until it expires. B3 therefore asks B4 on every read that decides student access
  instead of trusting the snapshot.
* ``POST /api/v1/internal/course-targets/validate`` -- an assignment's **target** (v0.38.0:
  target sections and named students) is a list of ids B3 cannot check on its own: sections
  and enrollments are B4's facts. This call answers "do these ids belong to this course"
  *before* the assignment is written, so a forged target is a `422` rather than a stored
  audience nobody verified.
* ``POST /api/v1/internal/assignment-notifications`` -- the publication-notification
  hand-off. B3 says "this assignment was published for this course, to this target"; B4
  narrows the course roster by that target and owns delivery. The call is idempotent by
  ``assignment_id``, which is exactly what lets B3 retry a failed hand-off.

Three rules shape this module:

* **Failures are reported, never skipped.** Every transport failure, timeout, unexpected
  status or malformed body raises an error the caller turns into a documented outcome; it
  is never read as "no membership" or "no assignment". Showing a student an empty list
  because B4 is down would be an authorization downgrade dressed up as availability.
* **Only the HTTP exchange is replaceable.** ``opener`` is injectable so a test can serve a
  local response without a network call; request construction, response parsing and failure
  classification always run for real. The same allowance the B4 JWKS fetch gets.
* **Nothing here is business policy.** The clients report what B4 answered; whether that
  answer grants access, confirms a target or marks a membership unavailable is decided in
  ``domain.assignments``.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from collections.abc import Callable, Mapping
from uuid import UUID

AUTHORIZATION_PATH = "/api/v1/internal/users/{user_id}/authorization"
COURSE_TARGET_PATH = "/api/v1/internal/course-targets/validate"
NOTIFICATION_PATH = "/api/v1/internal/assignment-notifications"

#: B4 statuses that are a statement about the *assignment hand-off*, not an outage: the
#: caller maps them back onto its own response (404/409/422). Any other non-2xx answer is a
#: deployment/transport failure and stays ``unavailable``.
NOTIFICATION_REJECTION_STATUSES = frozenset({404, 409, 422})
NOTIFICATION_ACCEPTED_STATUSES = frozenset({200, 201})

#: The target validator's declared refusals: ``422`` an id outside the course, ``404`` no such
#: course. Both are about the request, so they travel back to the caller as they are; anything
#: else (including ``401`` from a rotated service token) is an outage of the check itself.
TARGET_REFUSAL_STATUSES = frozenset({404, 422})

#: ``(request, timeout_seconds) -> response``: the same shape
#: :func:`urllib.request.urlopen` has, so the default needs no adapter and a test only has
#: to return a small object that supports ``with`` and exposes ``status``/``read``.
Opener = Callable[[urllib.request.Request, float], Any]


# --- authorization directory ---------------------------------------------


class AuthorizationUnavailable(Exception):
    """The authorization directory could not produce a usable answer.

    Raised for every failure mode -- unconfigured client, non-200, network error, timeout,
    malformed JSON, an answer about a different account. There is deliberately no subtype
    per cause: the caller's outcome is the same (``503``, no authorization decision) and
    the message is what an operator needs.
    """


@dataclass(frozen=True)
class Enrollment:
    """One active student enrollment: where the account currently sits in a course."""

    course_id: UUID
    section_id: UUID
    class_id: UUID


@dataclass(frozen=True)
class UserAuthorization:
    """B4's authoritative answer for one account.

    ``enrollments`` is what decides student read access to assignments; ``platform_roles``
    and ``course_grants`` are kept because they are part of the contract's answer and make
    "this account is staff, that one is a student" inspectable without a second call, even
    though B3's teacher path still authorizes from the token (v0.32.0 decision).
    """

    user_id: UUID
    platform_roles: frozenset[str]
    course_grants: frozenset[UUID]
    enrollments: tuple[Enrollment, ...]

    @property
    def enrolled_course_ids(self) -> frozenset[UUID]:
        return frozenset(enrollment.course_id for enrollment in self.enrollments)

    def is_enrolled_in(self, course_id: UUID) -> bool:
        return any(enrollment.course_id == course_id for enrollment in self.enrollments)


class AuthorizationDirectory(Protocol):
    """The port the assignment domain depends on (implemented by :class:`B4AuthorizationClient`)."""

    def user_authorization(self, user_id: UUID) -> UserAuthorization: ...


@dataclass
class B4AuthorizationClient:
    """HTTP client for the internal current-authorization operation."""

    base_url: str
    service_token: str
    timeout_seconds: float = 10.0
    opener: Opener | None = None

    def user_authorization(self, user_id: UUID) -> UserAuthorization:
        request = self._build_request(user_id)
        open_request = self.opener or _open
        try:
            with open_request(request, self.timeout_seconds) as response:
                status = int(getattr(response, "status", 0))
                body = response.read()
        except Exception as error:  # noqa: BLE001 - every transport failure is an outage
            raise AuthorizationUnavailable(
                f"the authorization directory is unreachable ({type(error).__name__})"
            ) from error
        if status != 200:
            # A 404 is deliberately *not* read as "no memberships": an issued token's
            # subject is an existing account, so this points at a deployment mismatch
            # (wrong base URL, rotated service token). Guessing "empty" would silently
            # deny every student and call the outage an authorization decision.
            raise AuthorizationUnavailable(
                f"the authorization directory answered HTTP {status}"
            )
        return _parse_authorization(body, user_id=user_id)

    def _build_request(self, user_id: UUID) -> urllib.request.Request:
        return urllib.request.Request(
            self.base_url.rstrip("/") + AUTHORIZATION_PATH.format(user_id=user_id),
            headers={
                "Authorization": f"Bearer {self.service_token}",
                "Accept": "application/json",
            },
            method="GET",
        )


def _parse_authorization(body: bytes, *, user_id: UUID) -> UserAuthorization:
    """Parse the contract's ``InternalUserAuthorization`` body, strictly.

    Missing required keys, wrong container types and unparseable UUIDs all mean the service
    did not answer the documented shape; guessing a value would be worse than reporting the
    outage, because the guess decides which courses a student may read.
    """
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorizationUnavailable(
            "the authorization directory answered a body that is not JSON"
        ) from error
    if not isinstance(document, Mapping):
        raise AuthorizationUnavailable(
            "the authorization directory answered a body that is not an object"
        )
    try:
        echoed_user = UUID(str(document["user_id"]))
        raw_roles = document["platform_roles"]
        raw_grants = document["course_grants"]
        raw_enrollments = document["enrollments"]
    except (KeyError, TypeError, ValueError) as error:
        raise AuthorizationUnavailable(
            "the authorization directory answered an incomplete body"
        ) from error
    if echoed_user != user_id:
        raise AuthorizationUnavailable(
            "the authorization directory answered for a different account"
        )
    if not isinstance(raw_roles, list) or not all(isinstance(item, str) for item in raw_roles):
        raise AuthorizationUnavailable(
            "the authorization directory answered malformed platform roles"
        )
    if not isinstance(raw_grants, list) or not isinstance(raw_enrollments, list):
        raise AuthorizationUnavailable(
            "the authorization directory answered malformed memberships"
        )
    try:
        course_grants = frozenset(UUID(str(item)) for item in raw_grants)
        enrollments = tuple(
            Enrollment(
                course_id=UUID(str(item["course_id"])),
                section_id=UUID(str(item["section_id"])),
                class_id=UUID(str(item["class_id"])),
            )
            for item in raw_enrollments
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AuthorizationUnavailable(
            "the authorization directory answered malformed membership ids"
        ) from error
    return UserAuthorization(
        user_id=echoed_user,
        platform_roles=frozenset(raw_roles),
        course_grants=course_grants,
        enrollments=enrollments,
    )


# --- assignment target validation ----------------------------------------


class TargetRejected(Exception):
    """B4 refused the target: an id is not part of the course, or the course is gone.

    ``status_code`` is the answer's own status (``422`` for ids outside the course, ``404``
    for an unknown course), so the caller keeps the distinction instead of reporting one
    vocabulary for two different mistakes. ``detail`` is B4's Problem Details text, which
    names the offending ids: it is echoed to the teacher because those ids came from the
    request in the first place.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TargetValidationUnavailable(Exception):
    """The target could not be checked, so nothing was decided.

    Raised for an unconfigured client, a transport failure, a timeout, a service-token
    mismatch or a malformed body. The caller refuses the write (``503``) instead of storing a
    target it could not verify: an unverified target is either an audience wider than the
    teacher chose or an assignment hidden from a named student, and one retry is cheaper than
    either.
    """


@dataclass(frozen=True)
class ConfirmedTarget:
    """The target B4 confirmed, echoed in the stable (sorted) order it answered with.

    Empty tuples mean "not narrowed on that axis", which is also what an assignment created
    before the target existed looks like. This is what B3 writes down, so the stored target is
    the confirmed one rather than the request's spelling of it.
    """

    section_ids: tuple[UUID, ...] = ()
    student_ids: tuple[UUID, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.section_ids and not self.student_ids


class CourseTargetValidator(Protocol):
    """The port the assignment domain depends on (implemented by the B4 client below)."""

    def validate_course_target(
        self,
        *,
        course_id: UUID,
        section_ids: tuple[UUID, ...],
        student_ids: tuple[UUID, ...],
    ) -> ConfirmedTarget: ...


@dataclass
class B4CourseTargetValidator:
    """HTTP client for the internal course-target validation operation."""

    base_url: str
    service_token: str
    timeout_seconds: float = 10.0
    opener: Opener | None = None

    def validate_course_target(
        self,
        *,
        course_id: UUID,
        section_ids: tuple[UUID, ...],
        student_ids: tuple[UUID, ...],
    ) -> ConfirmedTarget:
        request = self._build_request(
            course_id=course_id, section_ids=section_ids, student_ids=student_ids
        )
        open_request = self.opener or _open
        try:
            with open_request(request, self.timeout_seconds) as response:
                status = int(getattr(response, "status", 0))
                body = response.read()
        except Exception as error:  # noqa: BLE001 - every transport failure is an outage
            raise TargetValidationUnavailable(
                f"the course target validator is unreachable ({type(error).__name__})"
            ) from error
        if status == 200:
            return _parse_confirmed_target(body, course_id=course_id)
        if status in TARGET_REFUSAL_STATUSES:
            raise TargetRejected(status, _problem_detail(body) or f"HTTP {status}")
        raise TargetValidationUnavailable(
            f"the course target validator answered HTTP {status}"
        )

    def _build_request(
        self,
        *,
        course_id: UUID,
        section_ids: tuple[UUID, ...],
        student_ids: tuple[UUID, ...],
    ) -> urllib.request.Request:
        payload = {
            "course_id": str(course_id),
            "section_ids": [str(item) for item in section_ids],
            "student_ids": [str(item) for item in student_ids],
        }
        return urllib.request.Request(
            self.base_url.rstrip("/") + COURSE_TARGET_PATH,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.service_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )


def _parse_confirmed_target(body: bytes, *, course_id: UUID) -> ConfirmedTarget:
    """Parse the contract's ``CourseTargetValidation`` body, strictly.

    A missing key, a wrong container type, an unparseable id or an answer about a different
    course all mean the service did not answer the documented shape. Guessing `()` would read
    as "whole course" -- the widest possible audience -- so this is an outage instead.
    """
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TargetValidationUnavailable(
            "the course target validator answered a body that is not JSON"
        ) from error
    if not isinstance(document, Mapping):
        raise TargetValidationUnavailable(
            "the course target validator answered a body that is not an object"
        )
    try:
        echoed_course = UUID(str(document["course_id"]))
        raw_sections = document["section_ids"]
        raw_students = document["student_ids"]
    except (KeyError, TypeError, ValueError) as error:
        raise TargetValidationUnavailable(
            "the course target validator answered an incomplete body"
        ) from error
    if echoed_course != course_id:
        raise TargetValidationUnavailable(
            "the course target validator answered for a different course"
        )
    if not isinstance(raw_sections, list) or not isinstance(raw_students, list):
        raise TargetValidationUnavailable(
            "the course target validator answered malformed target ids"
        )
    try:
        section_ids = tuple(UUID(str(item)) for item in raw_sections)
        student_ids = tuple(UUID(str(item)) for item in raw_students)
    except ValueError as error:
        raise TargetValidationUnavailable(
            "the course target validator answered an unparseable target id"
        ) from error
    return ConfirmedTarget(section_ids=section_ids, student_ids=student_ids)


def _problem_detail(body: bytes) -> str | None:
    """B4's Problem Details ``detail``, when it answered one; never a guess."""
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, Mapping):
        return None
    detail = document.get("detail")
    return detail if isinstance(detail, str) and detail.strip() else None


# --- publication notifications -------------------------------------------


class NotificationUnavailable(Exception):
    """The notification hand-off could not be completed.

    Raised for an unconfigured client, a network error, a timeout, a 5xx answer or any
    status that is not a statement from B4 about the request. The assignment is **not**
    rolled back by the caller: it was published, and the truthful state is
    ``notification_state = unavailable`` so a teacher can retry.
    """


class NotificationRejected(Exception):
    """B4 explicitly refused the notification request with a client-facing status.

    ``status_code`` is one of :data:`NOTIFICATION_REJECTION_STATUSES` (404 assignment
    unknown to B4, 409 conflict, 422 unprocessable body). The caller maps it onto its own
    response instead of reporting "unavailable", because a retry would fail identically.
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(
            f"the notification service refused the request with HTTP {status_code}"
        )
        self.status_code = status_code


class AssignmentNotifier(Protocol):
    """The port the assignment domain depends on (implemented by :class:`B4AssignmentNotifier`)."""

    def notify(
        self,
        *,
        assignment_id: UUID,
        course_id: UUID,
        title: str,
        due_at: datetime | None,
        published_at: datetime,
        section_ids: tuple[UUID, ...] = (),
        student_ids: tuple[UUID, ...] = (),
    ) -> None: ...


@dataclass
class B4AssignmentNotifier:
    """HTTP client for the internal assignment-notification hand-off."""

    base_url: str
    service_token: str
    timeout_seconds: float = 10.0
    opener: Opener | None = None

    def notify(
        self,
        *,
        assignment_id: UUID,
        course_id: UUID,
        title: str,
        due_at: datetime | None,
        published_at: datetime,
        section_ids: tuple[UUID, ...] = (),
        student_ids: tuple[UUID, ...] = (),
    ) -> None:
        request = self._build_request(
            assignment_id=assignment_id,
            course_id=course_id,
            title=title,
            due_at=due_at,
            published_at=published_at,
            section_ids=section_ids,
            student_ids=student_ids,
        )
        open_request = self.opener or _open
        try:
            with open_request(request, self.timeout_seconds) as response:
                status = int(getattr(response, "status", 0))
                # The body is not needed: success is B4 acknowledging the hand-off, and
                # delivery result (sent/failed per recipient) belongs to B4's own API.
                response.read()
        except Exception as error:  # noqa: BLE001 - every transport failure is an outage
            raise NotificationUnavailable(
                f"the notification service is unreachable ({type(error).__name__})"
            ) from error
        if status in NOTIFICATION_ACCEPTED_STATUSES:
            return
        if status in NOTIFICATION_REJECTION_STATUSES:
            raise NotificationRejected(status)
        raise NotificationUnavailable(f"the notification service answered HTTP {status}")

    def _build_request(
        self,
        *,
        assignment_id: UUID,
        course_id: UUID,
        title: str,
        due_at: datetime | None,
        published_at: datetime,
        section_ids: tuple[UUID, ...],
        student_ids: tuple[UUID, ...],
    ) -> urllib.request.Request:
        payload = {
            "assignment_id": str(assignment_id),
            "course_id": str(course_id),
            "title": title,
            "due_at": None if due_at is None else _wire_timestamp(due_at),
            "published_at": _wire_timestamp(published_at),
            # The target travels with the hand-off so B4 narrows its roster snapshot with the
            # same filter the submission path uses. Sending a resolved student list instead
            # would need a second copy of the roster in B3 -- the copy that then drifts.
            "section_ids": [str(item) for item in section_ids],
            "student_ids": [str(item) for item in student_ids],
        }
        return urllib.request.Request(
            self.base_url.rstrip("/") + NOTIFICATION_PATH,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.service_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )


def _wire_timestamp(value: datetime) -> str:
    """UTC RFC 3339 with millisecond precision, the contract's timestamp shape."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - configured service URL
