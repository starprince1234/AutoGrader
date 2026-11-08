"""B4's two internal clients, without a database or a network.

The production clients build the HTTP requests, parse the contract's
``InternalUserAuthorization`` body and classify every failure. Only the opener is
replaced, so these tests exercise the real request/response handling -- the same allowance
the JWKS fetch gets in ``test_auth.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from autograder_b3.integration.b4 import (
    B4AssignmentNotifier,
    B4AuthorizationClient,
    AuthorizationUnavailable,
    NotificationRejected,
    NotificationUnavailable,
)

from .conftest import COURSE_A, COURSE_B, SECTION_A, STUDENT_A


class _Response:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _json_response(payload: dict, *, status: int = 200) -> _Response:
    return _Response(json.dumps(payload).encode("utf-8"), status=status)


def _authorization_payload(
    *,
    user_id=STUDENT_A,
    roles=("student",),
    course_grants=(),
    enrollments=((COURSE_A, SECTION_A),),
) -> dict:
    return {
        "user_id": str(user_id),
        "platform_roles": list(roles),
        "course_grants": [str(course) for course in course_grants],
        "enrollments": [
            {
                "course_id": str(course),
                "section_id": str(section),
                "class_id": str(uuid.uuid4()),
            }
            for course, section in enrollments
        ],
    }


def _authorization_client(opener, *, base_url: str = "http://identity.invalid"):
    return B4AuthorizationClient(
        base_url=base_url,
        service_token="service-token",
        timeout_seconds=2.5,
        opener=opener,
    )


def _notifier(opener, *, base_url: str = "http://identity.invalid"):
    return B4AssignmentNotifier(
        base_url=base_url,
        service_token="service-token",
        timeout_seconds=2.5,
        opener=opener,
    )


# --- authorization directory ---------------------------------------------


def test_authorization_sends_the_documented_request():
    captured: dict = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
        captured["timeout"] = timeout
        return _json_response(_authorization_payload())

    authorization = _authorization_client(
        opener, base_url="http://identity.invalid/"
    ).user_authorization(STUDENT_A)
    assert (
        captured["url"]
        == f"http://identity.invalid/api/v1/internal/users/{STUDENT_A}/authorization"
    )
    assert captured["method"] == "GET"
    assert captured["headers"]["authorization"] == "Bearer service-token"
    assert captured["timeout"] == 2.5
    assert authorization.user_id == STUDENT_A
    assert authorization.platform_roles == frozenset({"student"})
    assert authorization.enrolled_course_ids == frozenset({COURSE_A})
    assert authorization.is_enrolled_in(COURSE_A) is True
    assert authorization.is_enrolled_in(COURSE_B) is False


def test_authorization_projects_course_grants_and_enrollments():
    def opener(request, timeout):
        return _json_response(
            _authorization_payload(
                roles=("teacher",),
                course_grants=(COURSE_A, COURSE_B),
                enrollments=((COURSE_B, SECTION_A),),
            )
        )

    authorization = _authorization_client(opener).user_authorization(STUDENT_A)
    assert authorization.course_grants == frozenset({COURSE_A, COURSE_B})
    assert authorization.enrolled_course_ids == frozenset({COURSE_B})


def test_authorization_non_200_is_unavailable_not_an_empty_answer():
    """A 404 is an outage signal, never "this account has no memberships"."""
    client = _authorization_client(
        lambda request, timeout: _json_response(_authorization_payload(), status=404)
    )
    with pytest.raises(AuthorizationUnavailable):
        client.user_authorization(STUDENT_A)


def test_authorization_network_error_is_unavailable():
    def opener(request, timeout):
        raise OSError("connection refused")

    with pytest.raises(AuthorizationUnavailable):
        _authorization_client(opener).user_authorization(STUDENT_A)


def test_authorization_malformed_json_is_unavailable():
    client = _authorization_client(lambda request, timeout: _Response(b"not json at all"))
    with pytest.raises(AuthorizationUnavailable):
        client.user_authorization(STUDENT_A)


def test_authorization_incomplete_body_is_unavailable():
    payload = _authorization_payload()
    del payload["enrollments"]
    client = _authorization_client(lambda request, timeout: _json_response(payload))
    with pytest.raises(AuthorizationUnavailable):
        client.user_authorization(STUDENT_A)


def test_authorization_answer_for_a_different_account_is_unavailable():
    other = uuid.uuid4()
    client = _authorization_client(
        lambda request, timeout: _json_response(_authorization_payload(user_id=other))
    )
    with pytest.raises(AuthorizationUnavailable):
        client.user_authorization(STUDENT_A)


# --- assignment notification hand-off ------------------------------------


def _notify(client: B4AssignmentNotifier, *, assignment_id=None, due_at=None):
    return client.notify(
        assignment_id=assignment_id or uuid.uuid4(),
        course_id=COURSE_A,
        title="Week 1",
        due_at=due_at,
        published_at=datetime(2026, 11, 8, 9, 30, tzinfo=UTC),
    )


def test_notification_sends_the_documented_request():
    captured: dict = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
        captured["body"] = json.loads(bytes(request.data).decode("utf-8"))
        captured["timeout"] = timeout
        return _json_response({"status": "pending"}, status=201)

    assignment_id = uuid.uuid4()
    _notify(
        _notifier(opener, base_url="http://identity.invalid/"),
        assignment_id=assignment_id,
        due_at=datetime(2026, 11, 20, 23, 59, tzinfo=UTC),
    )
    assert captured["url"] == "http://identity.invalid/api/v1/internal/assignment-notifications"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer service-token"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"] == {
        "assignment_id": str(assignment_id),
        "course_id": str(COURSE_A),
        "title": "Week 1",
        "due_at": "2026-11-20T23:59:00.000Z",
        "published_at": "2026-11-08T09:30:00.000Z",
    }
    assert captured["timeout"] == 2.5


def test_notification_without_a_due_date_sends_null():
    captured: dict = {}

    def opener(request, timeout):
        captured["body"] = json.loads(bytes(request.data).decode("utf-8"))
        return _json_response({"status": "pending"}, status=201)

    _notify(_notifier(opener))
    assert captured["body"]["due_at"] is None


def test_notification_accepts_a_repeat_answer():
    for status in (200, 201):

        def opener(request, timeout, _status=status):
            return _json_response({}, status=_status)

        _notify(_notifier(opener))


@pytest.mark.parametrize("status", sorted((404, 409, 422)))
def test_notification_rejection_keeps_the_declared_status(status: int):
    with pytest.raises(NotificationRejected) as error:
        _notify(_notifier(lambda request, timeout: _json_response({}, status=status)))
    assert error.value.status_code == status


def test_notification_server_error_is_unavailable():
    with pytest.raises(NotificationUnavailable):
        _notify(_notifier(lambda request, timeout: _json_response({}, status=500)))


def test_notification_network_error_is_unavailable():
    def opener(request, timeout):
        raise OSError("connection refused")

    with pytest.raises(NotificationUnavailable):
        _notify(_notifier(opener))
