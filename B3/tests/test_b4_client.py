"""The identity/course target-validation client, without a database or a network.

The production client builds the HTTP request, parses the contract's
``CourseTargetValidation`` body and classifies every failure as "unavailable". Only the
opener is replaced, so these tests exercise the real request/response handling -- the
same allowance the JWKS fetch gets in ``test_auth.py``.
"""

from __future__ import annotations

import json
import uuid

import pytest

from autograder_b3.integration.b4 import B4TargetClient, TargetsUnavailable

from .conftest import COURSE_A, COURSE_B, SECTION_A, SECTION_A2, SECTION_B, STUDENT_A


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


def _payload(
    *,
    course_id=COURSE_A,
    sections=(),
    students=(),
    unknown_sections=(),
    unknown_students=(),
) -> dict:
    return {
        "course_id": str(course_id),
        "sections": [
            {
                "section_id": str(section),
                "course_id": str(course_id),
                "section_code": f"S{index}",
                "term": None,
            }
            for index, section in enumerate(sections, start=1)
        ],
        "unknown_section_ids": [str(section) for section in unknown_sections],
        "students": [
            {"student_id": str(student), "section_id": str(section)}
            for student, section in students
        ],
        "unknown_student_ids": [str(student) for student in unknown_students],
    }


def _client(opener, *, base_url: str = "http://identity.invalid", token: str = "service-token"):
    return B4TargetClient(
        base_url=base_url,
        service_token=token,
        timeout_seconds=2.5,
        opener=opener,
    )


def test_validate_targets_sends_the_documented_request():
    captured: dict = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
        captured["body"] = json.loads(bytes(request.data).decode("utf-8"))
        captured["timeout"] = timeout
        return _json_response(_payload(sections=(SECTION_A,)))

    validation = _client(opener, base_url="http://identity.invalid/").validate_targets(
        course_id=COURSE_A,
        section_ids=(SECTION_A,),
        student_ids=(STUDENT_A,),
    )
    assert captured["url"] == "http://identity.invalid/api/v1/internal/course-targets/validate"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer service-token"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"] == {
        "course_id": str(COURSE_A),
        "section_ids": [str(SECTION_A)],
        "student_ids": [str(STUDENT_A)],
    }
    assert captured["timeout"] == 2.5
    assert validation.valid_section_ids == frozenset({SECTION_A})


def test_validate_targets_projects_valid_and_unknown_ids():
    student_be = uuid.uuid4()

    def opener(request, timeout):
        return _json_response(
            _payload(
                sections=(SECTION_A, SECTION_A2),
                students=((STUDENT_A, SECTION_A),),
                unknown_sections=(SECTION_B,),
                unknown_students=(student_be,),
            )
        )

    validation = _client(opener).validate_targets(
        course_id=COURSE_A,
        section_ids=(SECTION_A, SECTION_A2, SECTION_B),
        student_ids=(STUDENT_A, student_be),
    )
    assert validation.valid_section_ids == frozenset({SECTION_A, SECTION_A2})
    assert validation.unknown_section_ids == (SECTION_B,)
    assert validation.valid_student_ids == frozenset({STUDENT_A})
    assert validation.unknown_student_ids == (student_be,)


def test_non_200_response_is_unavailable():
    client = _client(lambda request, timeout: _json_response(_payload(), status=500))
    with pytest.raises(TargetsUnavailable):
        client.validate_targets(course_id=COURSE_A, section_ids=(SECTION_A,), student_ids=())


def test_network_error_is_unavailable():
    def opener(request, timeout):
        raise OSError("connection refused")

    with pytest.raises(TargetsUnavailable):
        _client(opener).validate_targets(
            course_id=COURSE_A, section_ids=(SECTION_A,), student_ids=()
        )


def test_malformed_json_is_unavailable():
    client = _client(lambda request, timeout: _Response(b"not json at all"))
    with pytest.raises(TargetsUnavailable):
        client.validate_targets(course_id=COURSE_A, section_ids=(SECTION_A,), student_ids=())


def test_incomplete_response_is_unavailable():
    payload = _payload(sections=(SECTION_A,))
    del payload["students"]
    client = _client(lambda request, timeout: _json_response(payload))
    with pytest.raises(TargetsUnavailable):
        client.validate_targets(course_id=COURSE_A, section_ids=(SECTION_A,), student_ids=())


def test_answer_for_a_different_course_is_unavailable():
    client = _client(lambda request, timeout: _json_response(_payload(course_id=COURSE_B)))
    with pytest.raises(TargetsUnavailable):
        client.validate_targets(course_id=COURSE_A, section_ids=(SECTION_A,), student_ids=())
