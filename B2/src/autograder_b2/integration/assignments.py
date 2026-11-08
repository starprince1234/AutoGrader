"""Read-only access to B3's assignment (service-to-service).

说明（课程级作业，v0.32.0）：作业的受众就是**整门课程**——没有手选的课序或学生名单。
"这篇作业属于哪门课、题目是哪几道、现在还能不能交"由 B3 回答；"这个学生此刻是否属于这门课、
当前课序是什么"由 B4 的权威授权查询回答（`integration.b4_authorization`）。B2 不读 B3/B4 的
schema，因此只通过内部 HTTP 端点取事实::

    GET {B3_BASE_URL}/api/v1/assignment-audiences/{assignment_id}

``AssignmentAudience`` 只保留作业自身的四个事实：所属课程、标题、状态与**有序**题目版本。
``section_ids`` / ``student_ids`` / ``is_target`` 已随"课程级受众"整体删除——同一个学生换课序
不改变能否提交，而"是否在课程里"由 B4 的当前选课判定，不由作业携带的名单副本判定。

Failure semantics are deliberately three-valued:

* ``None`` -- the assignment does not exist (a caller-side rejection, 404 -> 422/404 upwards);
* ``AssignmentAudience`` -- it exists and was parsed;
* ``AssignmentUnavailable`` -- B3 could not be asked (network/timeout/non-200/broken body).
  This is an **infrastructure** failure: the accept path must answer 503, never "your question
  is not part of the assignment", and the mail puller must retry the whole pass instead of
  quarantining the student's mail.

The HTTP opener is injectable (same pattern as ``intake.matching.DirectoryEmailMatcher``)
so tests can exercise this client against a real ASGI app or a recorded transport without
starting a network listener. The authorization value is ``repr=False``: credentials must not
appear in reprs or logs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

#: Response cap: an assignment audience is a handful of UUIDs; anything near this limit is
#: not the endpoint we think we are talking to. Oversize responses are an infrastructure
#: failure, not a partial success.
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0

#: ``opener(url, headers, timeout) -> (status, body_bytes)`` -- identical shape to the
#: directory matcher's opener so tests can share a fake transport.
Opener = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


class AssignmentUnavailable(RuntimeError):
    """B3's assignment endpoint could not answer (configured but unreachable/broken)."""


@dataclass(frozen=True, slots=True)
class AssignmentAudience:
    """One assignment as B2 needs it for admission decisions.

    ``question_version_ids`` is ordered by the assignment's item ordinal. There is no target
    section/student list any more: a course-level assignment covers every student who is
    **currently enrolled in the course**, and that fact is owned by B4 (queried separately).
    """

    assignment_id: str
    course_id: str
    title: str
    status: str
    question_version_ids: tuple[str, ...]

    @property
    def open(self) -> bool:
        return self.status == "open"

    def contains_question(self, question_version_id: object) -> bool:
        return _canonical(question_version_id) in self.question_version_ids

    def answer_ordinal(self, question_version_id: object) -> int:
        """1-based position of the question in assignment order (used for ``N.txt``)."""
        canonical = _canonical(question_version_id)
        return self.question_version_ids.index(canonical) + 1

    def question_at(self, ordinal: int) -> str | None:
        if 1 <= ordinal <= len(self.question_version_ids):
            return self.question_version_ids[ordinal - 1]
        return None


class AssignmentDirectory(Protocol):
    """Port: assignment lookup for admission decisions."""

    def get(self, assignment_id: str) -> AssignmentAudience | None: ...


class B3AssignmentDirectory:
    """``GET /api/v1/assignment-audiences/{id}`` against B3 with a service bearer token."""

    def __init__(
        self,
        *,
        base_url: str,
        authorization: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Opener | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        if not authorization.strip():
            raise ValueError("authorization must not be empty")
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._opener = opener or _http_get

    def get(self, assignment_id: str) -> AssignmentAudience | None:
        identifier = _optional_uuid_text(assignment_id)
        if identifier is None:
            # An identifier that cannot be a UUID cannot identify an assignment. Callers
            # validate the contract first; reaching here means "not found".
            return None
        url = f"{self._base_url}/api/v1/assignment-audiences/{identifier}"
        try:
            status, body = self._opener(
                url,
                {"accept": "application/json", "authorization": self._authorization},
                self._timeout,
            )
        except OSError as error:
            raise AssignmentUnavailable("assignment directory is unreachable") from error
        if status == 404:
            return None
        if status != 200:
            raise AssignmentUnavailable(f"assignment directory answered HTTP {status}")
        if len(body) > self._max_bytes:
            raise AssignmentUnavailable("assignment directory response is too large")
        return _parse_audience(body)


def _optional_uuid_text(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _canonical(value: object) -> str:
    return _optional_uuid_text(value) or ""


def _parse_audience(body: bytes) -> AssignmentAudience:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise AssignmentUnavailable("assignment directory returned malformed JSON") from error
    if not isinstance(document, Mapping):
        raise AssignmentUnavailable("assignment directory returned a non-object body")
    try:
        assignment_id = _required_uuid(document, "assignment_id")
        course_id = _required_uuid(document, "course_id")
        title = _required_text(document, "title")
        status = _required_text(document, "status")
        question_version_ids = _uuid_tuple(document, "question_version_ids")
    except (ValueError, TypeError) as error:
        raise AssignmentUnavailable("assignment directory returned an invalid audience") from error
    if not question_version_ids:
        # 作业没有题目就不可能被实现；把它当受众会让"不可提交的作业"被静默接受。
        # 课程级受众下**没有** section/student 列表，因此这里只校验题目非空。
        raise AssignmentUnavailable("assignment directory returned an empty question list")
    return AssignmentAudience(
        assignment_id=assignment_id,
        course_id=course_id,
        title=title,
        status=status,
        question_version_ids=question_version_ids,
    )


def _required_uuid(document: Mapping[str, object], name: str) -> str:
    value = _optional_uuid_text(document.get(name))
    if value is None:
        raise ValueError(f"{name} must be a UUID")
    return value


def _required_text(document: Mapping[str, object], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _uuid_tuple(document: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = document.get(name)
    if not isinstance(value, list):
        raise TypeError(f"{name} must be an array")
    result: list[str] = []
    for item in value:
        canonical = _optional_uuid_text(item)
        if canonical is None:
            raise ValueError(f"{name} contains a non-UUID value")
        result.append(canonical)
    return tuple(result)


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except OSError as error:
        raise AssignmentUnavailable("assignment directory request failed") from error


__all__ = [
    "AssignmentAudience",
    "AssignmentDirectory",
    "AssignmentUnavailable",
    "B3AssignmentDirectory",
]
