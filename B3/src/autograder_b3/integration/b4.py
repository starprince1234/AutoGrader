"""Outbound service-to-service client: assignment target validation.

B3 stores the target section/student ids of an assignment, but the identity and course
data that make those ids meaningful live in the platform's identity module. Creating an
assignment therefore asks that module's internal ``POST /api/v1/internal/course-targets/validate``
operation whether the chosen sections belong to the course and whether the chosen students
are actively enrolled in them (``MAIN/docs/architecture/assignment-workflow.md`` §2.4/§3.2).

Two rules shape this module:

* **Failures are reported, never skipped.** A non-200 answer, a network error, a timeout
  or a malformed body all become :class:`TargetsUnavailable`, which the domain turns into
  ``503``. Accepting the ids on the caller's word would let a teacher target another
  course's class, or a student who is not enrolled -- exactly the failure this
  server-side check exists to prevent.
* **Only the HTTP endpoint is replaceable.** ``opener`` is injectable so a test can serve
  a local response without a network call; the request construction, response parsing and
  failure classification always run for real.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from collections.abc import Callable, Mapping, Sequence
from uuid import UUID

VALIDATE_PATH = "/api/v1/internal/course-targets/validate"

#: ``(request, timeout_seconds) -> response``: the same shape :func:`urllib.request.urlopen`
#: has, so the default needs no adapter and a test only has to return a small object that
#: supports ``with`` and exposes ``status``/``read``.
Opener = Callable[[urllib.request.Request, float], Any]


class TargetsUnavailable(Exception):
    """The target validation service could not produce a usable answer.

    Raised for every failure mode -- unconfigured client, non-200, network error,
    timeout, malformed JSON, an answer about a different course. There is deliberately
    no subtype per cause: the caller's outcome is the same (the assignment is not
    created) and the message is what an operator needs.
    """


@dataclass(frozen=True)
class TargetValidation:
    """Which requested target ids the identity/course module acknowledges.

    ``valid_*`` are the acknowledged ids; ``unknown_*`` are the rejected ones as reported
    by that module. The domain uses the ``valid_*`` sets to decide ``422``/``201``; the
    ``unknown_*`` lists are kept so an error message can count the rejected targets
    without the module having to be re-queried.
    """

    valid_section_ids: frozenset[UUID]
    unknown_section_ids: tuple[UUID, ...]
    valid_student_ids: frozenset[UUID]
    unknown_student_ids: tuple[UUID, ...]


class TargetValidator(Protocol):
    """The port the assignment domain depends on (implemented by :class:`B4TargetClient`)."""

    def validate_targets(
        self,
        *,
        course_id: UUID,
        section_ids: Sequence[UUID],
        student_ids: Sequence[UUID],
    ) -> TargetValidation: ...


@dataclass
class B4TargetClient:
    """HTTP client for the internal course-target validation operation."""

    base_url: str
    service_token: str
    timeout_seconds: float = 10.0
    opener: Opener | None = None

    def validate_targets(
        self,
        *,
        course_id: UUID,
        section_ids: Sequence[UUID],
        student_ids: Sequence[UUID],
    ) -> TargetValidation:
        request = self._build_request(course_id, section_ids, student_ids)
        open_request = self.opener or _open
        try:
            with open_request(request, self.timeout_seconds) as response:
                status = int(getattr(response, "status", 0))
                body = response.read()
        except Exception as error:  # noqa: BLE001 - every transport failure is an outage
            raise TargetsUnavailable(
                f"the assignment target validator is unreachable ({type(error).__name__})"
            ) from error
        if status != 200:
            raise TargetsUnavailable(
                f"the assignment target validator answered HTTP {status}"
            )
        return _parse_response(body, course_id=course_id)

    def _build_request(
        self,
        course_id: UUID,
        section_ids: Sequence[UUID],
        student_ids: Sequence[UUID],
    ) -> urllib.request.Request:
        payload = {
            "course_id": str(course_id),
            "section_ids": [str(item) for item in section_ids],
            "student_ids": [str(item) for item in student_ids],
        }
        return urllib.request.Request(
            self.base_url.rstrip("/") + VALIDATE_PATH,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.service_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - configured service URL


def _parse_response(body: bytes, *, course_id: UUID) -> TargetValidation:
    """Parse the contract's ``CourseTargetValidation`` body, strictly.

    Missing required keys, wrong container types and unparseable UUIDs all mean the
    service did not answer the documented shape; guessing a value would be worse than
    reporting the outage, because the guess decides who may submit.
    """
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TargetsUnavailable(
            "the assignment target validator answered a body that is not JSON"
        ) from error
    if not isinstance(document, Mapping):
        raise TargetsUnavailable(
            "the assignment target validator answered a body that is not an object"
        )
    try:
        echoed_course = UUID(str(document["course_id"]))
        raw_sections = document["sections"]
        raw_students = document["students"]
        raw_unknown_sections = document["unknown_section_ids"]
        raw_unknown_students = document["unknown_student_ids"]
    except (KeyError, TypeError, ValueError) as error:
        raise TargetsUnavailable(
            "the assignment target validator answered an incomplete body"
        ) from error
    if echoed_course != course_id:
        raise TargetsUnavailable(
            "the assignment target validator answered for a different course"
        )
    if not all(
        isinstance(value, list)
        for value in (raw_sections, raw_students, raw_unknown_sections, raw_unknown_students)
    ):
        raise TargetsUnavailable(
            "the assignment target validator answered malformed target lists"
        )
    try:
        valid_sections = frozenset(UUID(str(item["section_id"])) for item in raw_sections)
        valid_students = frozenset(UUID(str(item["student_id"])) for item in raw_students)
        unknown_sections = tuple(UUID(str(item)) for item in raw_unknown_sections)
        unknown_students = tuple(UUID(str(item)) for item in raw_unknown_students)
    except (KeyError, TypeError, ValueError) as error:
        raise TargetsUnavailable(
            "the assignment target validator answered malformed target ids"
        ) from error
    return TargetValidation(
        valid_section_ids=valid_sections,
        unknown_section_ids=unknown_sections,
        valid_student_ids=valid_students,
        unknown_student_ids=unknown_students,
    )
