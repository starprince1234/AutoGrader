"""Shared test fixtures.

Real infrastructure only (``docs/testing/strategy.md``):

* **PostgreSQL is real.** The session fixture drops ``b3`` and runs
  ``alembic upgrade head`` from scratch, so every test run proves the migration
  works on an empty database.
* **Two real roles.** Application behaviour is exercised through the
  least-privilege ``autograder_b3_app`` connection (public reads, statistics) and
  the ``autograder_b3_internal`` connection (publish/compile). The owner connection
  is used only for migration and truncation.
* **Mocks are limited to NATS and the B4 JWKS *endpoint*.** Tokens are real JWTs
  signed with a key generated in the test; only the JWKS HTTP fetch is replaced.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

B3_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OWNER_URL = (
    "postgresql+psycopg://autograder_b3:change-me-local-only@127.0.0.1:55434/autograder"
)
DEFAULT_APP_URL = (
    "postgresql+psycopg://autograder_b3_app:change-me-app-local-only@127.0.0.1:55434/autograder"
)
DEFAULT_INTERNAL_URL = (
    "postgresql+psycopg://autograder_b3_internal:change-me-internal-local-only"
    "@127.0.0.1:55434/autograder"
)

OWNER_URL = os.environ.get("B3_TEST_OWNER_URL", DEFAULT_OWNER_URL)
APP_URL = os.environ.get("B3_TEST_APP_URL", DEFAULT_APP_URL)
INTERNAL_URL = os.environ.get("B3_TEST_INTERNAL_URL", DEFAULT_INTERNAL_URL)


def _require_postgres() -> None:
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(OWNER_URL, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as error:  # noqa: BLE001 - reported as a skip reason
        pytest.skip(
            f"PostgreSQL at {OWNER_URL.split('@')[-1]} is unreachable ({type(error).__name__}); "
            "start it with `docker compose up -d postgres` (B3/docs/runbooks/local-development.md)"
        )


@pytest.fixture(scope="session")
def postgres_owner_url() -> str:
    _require_postgres()
    return OWNER_URL


@pytest.fixture(scope="session", autouse=True)
def migrated_schema(postgres_owner_url: str) -> Iterator[None]:
    """Drop ``b3`` and migrate from zero - the migration gate, on every run."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(postgres_owner_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS b3 CASCADE"))
        connection.execute(text("CREATE SCHEMA b3"))
    engine.dispose()

    config = Config(str(B3_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(B3_ROOT / "alembic"))
    os.environ["DATABASE_URL"] = postgres_owner_url
    command.upgrade(config, "head")
    yield


@pytest.fixture
def settings():
    from autograder_b3.config import Settings

    return Settings(
        database_url=APP_URL,
        protected_database_url=INTERNAL_URL,
        service_token="test-service-token",
        jwks_url=None,
        consumer_name="b3-test-consumer",
    )


@pytest.fixture
def database(settings):
    from autograder_b3.infra.db import Database

    instance = Database(settings)
    yield instance
    instance.dispose()


@pytest.fixture
def owner_engine(postgres_owner_url):
    from sqlalchemy import create_engine

    engine = create_engine(postgres_owner_url, isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture
def clean_event_store(owner_engine) -> None:
    """Empty the event/statistics tables so replay assertions are exact.

    ``consumption_counters`` is included: it is per-delivery state too, and a test that
    reads it must start from zero rather than from whatever the previous test consumed.
    """
    from sqlalchemy import text

    with owner_engine.connect() as connection:
        connection.execute(
            text(
                "TRUNCATE b3.event_inbox, b3.event_outbox, b3.consumption_counters, "
                "b3.question_version_stats, b3.question_version_score_buckets, "
                "b3.question_version_failure_categories"
            )
        )


@pytest.fixture
def clock():
    class FixedClock:
        def __init__(self) -> None:
            self.moment = datetime(2026, 9, 15, 8, 30, 0, tzinfo=UTC)

        def __call__(self) -> datetime:
            return self.moment

        def advance(self, seconds: int) -> datetime:
            from datetime import timedelta

            self.moment = self.moment + timedelta(seconds=seconds)
            return self.moment

    return FixedClock()


# --- identities ----------------------------------------------------------

COURSE_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
COURSE_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
#: A third course, written by no other test than the tag-coverage ones. Coverage counts
#: are aggregates, so an exact expected number needs a scope nobody else adds rows to;
#: COURSE_A accumulates questions from every HTTP test in the session.
COURSE_C = uuid.UUID("55555555-5555-4555-8555-555555555555")
SECTION_A = uuid.UUID("33333333-3333-4333-8333-333333333333")
#: A second class of COURSE_A. Assignments target a *set* of sections now, so a test that
#: only ever uses one section cannot tell the set model from the old single column.
SECTION_A2 = uuid.UUID("33333333-3333-4333-8333-333333333334")
#: A class of COURSE_B, used to prove a cross-course target is refused (422).
SECTION_B = uuid.UUID("33333333-3333-4333-8333-333333333335")
#: A course and class written by **no** assignment test other than the pagination one.
#: Assignment rows are not truncated between tests, so an exact-served-set assertion needs
#: a course nobody else writes; COURSE_C is equally "quiet" but the tag-coverage tests
#: assert its exact question counts, which a published fixture question would break.
COURSE_D = uuid.UUID("77777777-7777-4777-8777-777777777777")
SECTION_D = uuid.UUID("33333333-3333-4333-8333-333333333336")
TEACHER_ID = "44444444-4444-4444-8444-444444444444"
STUDENT_A = uuid.UUID("66666666-6666-4666-8666-666666666666")
STUDENT_B = uuid.UUID("66666666-6666-4666-8666-666666666667")

#: Which course each known section belongs to. This is the identity/course module's fact,
#: served to B3 through the internal validation endpoint; the test-only opener below
#: answers from this mapping so target validation runs for real (no database, no network)
#: while the HTTP client, parsing and failure classification are the production code.
SECTION_COURSES: dict[uuid.UUID, uuid.UUID] = {
    SECTION_A: COURSE_A,
    SECTION_A2: COURSE_A,
    SECTION_B: COURSE_B,
    SECTION_D: COURSE_D,
}

#: ``student_id -> (course_id, section_id)`` for students that are actively enrolled.
#: A student not in this mapping is "unknown / not actively enrolled" to the validator.
ENROLLMENTS: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {
    STUDENT_A: (COURSE_A, SECTION_A),
    STUDENT_B: (COURSE_A, SECTION_A2),
}


@pytest.fixture
def tokens() -> dict[str, dict]:
    return {
        "teacher-a": {
            "sub": TEACHER_ID,
            "roles": ["teacher"],
            "scope": [{"course_id": str(COURSE_A), "section_id": str(SECTION_A)}],
        },
        # Same course, whole-course grant: the assignment audience model must let this
        # token read every target-section variant of COURSE_A, unlike `teacher-a`.
        "teacher-wide": {
            "sub": str(uuid.uuid4()),
            "roles": ["teacher"],
            "scope": [{"course_id": str(COURSE_A)}],
        },
        "teacher-b": {
            "sub": str(uuid.uuid4()),
            "roles": ["teacher"],
            "scope": [{"course_id": str(COURSE_B)}],
        },
        "teacher-c": {
            "sub": str(uuid.uuid4()),
            "roles": ["teacher"],
            "scope": [{"course_id": str(COURSE_C)}],
        },
        # Only used by the assignment pagination test; COURSE_D exists for it alone.
        "teacher-d": {
            "sub": str(uuid.uuid4()),
            "roles": ["teacher"],
            "scope": [{"course_id": str(COURSE_D)}],
        },
        "student-a": {
            "sub": str(STUDENT_A),
            "roles": ["student"],
            "scope": [{"course_id": str(COURSE_A), "section_id": str(SECTION_A)}],
        },
    }


@pytest.fixture
def auth_headers(tokens):
    def build(token_name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_name}"}

    return build


@pytest.fixture
def service_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-service-token"}


@pytest.fixture
def verifier(tokens):
    from autograder_b3.auth import StaticTokenVerifier

    return StaticTokenVerifier(claims_by_token=tokens)


@pytest.fixture
def publisher():
    from autograder_b3.infra.publisher import InMemoryEventPublisher

    return InMemoryEventPublisher()


# --- identity/course module stand-in (assignment target validation) ------
#
# Only the *HTTP endpoint* is replaced (same allowance as the B4 JWKS fetch): the
# request B3 builds, the production client's parsing and its failure classification all
# run for real. The answer is computed from the SECTION_COURSES/ENROLLMENTS mapping above,
# so "unknown section", "student not enrolled in the given section" and "cross-course
# student" are real decisions rather than scripted strings.


class _B4Response:
    def __init__(self, payload: dict, *, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _B4Response:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def b4_validation_answer(request) -> dict:
    """The identity/course module's ``CourseTargetValidation`` body for a request.

    Kept a free function so the client tests can call it directly (or wrap it in a
    custom opener) without duplicating the fixture mapping.
    """
    body = json.loads(bytes(request.data).decode("utf-8"))
    course_id = uuid.UUID(str(body["course_id"]))
    requested_sections = [uuid.UUID(str(value)) for value in body.get("section_ids", ())]
    requested_students = [uuid.UUID(str(value)) for value in body.get("student_ids", ())]
    sections: list[dict] = []
    unknown_sections: list[str] = []
    for section_id in requested_sections:
        if SECTION_COURSES.get(section_id) != course_id:
            unknown_sections.append(str(section_id))
            continue
        sections.append(
            {
                "section_id": str(section_id),
                "course_id": str(course_id),
                "section_code": f"S-{str(section_id)[:8]}",
                "term": None,
            }
        )
    students: list[dict] = []
    unknown_students: list[str] = []
    for student_id in requested_students:
        enrollment = ENROLLMENTS.get(student_id)
        if (
            enrollment is not None
            and enrollment[0] == course_id
            and enrollment[1] in requested_sections
        ):
            students.append(
                {"student_id": str(student_id), "section_id": str(enrollment[1])}
            )
        else:
            unknown_students.append(str(student_id))
    return {
        "course_id": str(course_id),
        "sections": sections,
        "unknown_section_ids": unknown_sections,
        "students": students,
        "unknown_student_ids": unknown_students,
    }


def b4_opener(request, timeout: float):
    """Opener that serves :func:`b4_validation_answer` without a network call."""
    return _B4Response(b4_validation_answer(request))


@pytest.fixture
def b4_target_client():
    from autograder_b3.integration.b4 import B4TargetClient

    return B4TargetClient(
        base_url="http://identity.invalid",
        service_token="test-service-token",
        timeout_seconds=1.0,
        opener=b4_opener,
    )


@pytest.fixture
def dependencies(settings, database, verifier, publisher, clock, b4_target_client):
    from autograder_b3.api.deps import B3Dependencies
    from autograder_b3.auth import ServiceTokenVerifier

    return B3Dependencies(
        settings=settings,
        database=database,
        token_verifier=verifier,
        service_verifier=ServiceTokenVerifier("test-service-token"),
        publisher=publisher,
        b4_targets=b4_target_client,
        clock=clock,
    )


@pytest.fixture
def client(dependencies):
    from fastapi.testclient import TestClient

    from autograder_b3.api.app import create_app

    app = create_app(dependencies)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --- payload builders ----------------------------------------------------


def draft_payload(
    *,
    language: str = "python",
    kind: str = "stdin",
    reference_answer: str | None = "print(3)\n",
    cases: list[dict] | None = None,
    args: list[str] | None = None,
    stdin: str | None = "a\nb\nc\n",
    expect_exit_code: int | None = 0,
    comparison: dict | None = None,
    files: list[dict] | None = None,
    callable_target: dict | None = None,
    stem: str = "Count the lines of the input.",
    title: str = "Line counter",
) -> dict:
    adapter: dict = {"kind": kind}
    if args is not None:
        adapter["args"] = args
    if stdin is not None:
        adapter["stdin"] = stdin
    if expect_exit_code is not None:
        adapter["expect_exit_code"] = expect_exit_code
    adapter["comparison"] = comparison or {
        "stdout": "ignore_trailing_whitespace",
        "stderr": "ignored",
        "numeric_tolerance": None,
    }
    if files:
        adapter["files"] = files
    if callable_target:
        adapter["callable"] = callable_target
    body = {
        "title": title,
        "stem_markdown": stem,
        "language": language,
        "adapter": adapter,
        "cases": cases
        if cases is not None
        else [
            {
                "name": "counts lines",
                "ordinal": 0,
                "weight": 1,
                "visible_to_student": True,
                "stdin": "a\nb\nc\n",
                "args": [],
                "expected_stdout": "3\n",
                "expected_stderr": None,
                "expected_exit_code": 0,
            },
            {
                "name": "hidden edge case: empty input",
                "ordinal": 1,
                "weight": 2,
                "visible_to_student": False,
                "stdin": "",
                "args": [],
                "expected_stdout": "0\n",
                "expected_stderr": None,
                "expected_exit_code": 0,
            },
        ],
    }
    if reference_answer is not None:
        body["reference_answer"] = reference_answer
    return body


def event_envelope(
    *,
    question_version_id: uuid.UUID,
    evaluation_run_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    occurred_at: str = "2026-09-15T12:00:00.000Z",
    is_pass: bool | None = True,
    score: float | None = 86,
    max_score: float | None = 100,
    failed_category: str | None = None,
    extra_payload: dict | None = None,
) -> dict:
    payload: dict = {
        "submission_id": str(uuid.uuid4()),
        "evaluation_run_id": str(evaluation_run_id or uuid.uuid4()),
        "question_version_id": str(question_version_id),
        "result_digest": "a" * 64,
    }
    if is_pass is not None:
        payload["is_pass"] = is_pass
    if score is not None:
        payload["score"] = score
    if max_score is not None:
        payload["max_score"] = max_score
    if failed_category is not None:
        payload["failed_category"] = failed_category
    if extra_payload:
        payload.update(extra_payload)
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "autograder.v1.evaluation.completed",
        "schema_version": 1,
        "occurred_at": occurred_at,
        "correlation_id": str(uuid.uuid4()),
        "causation_id": None,
        "producer": "B2",
        "payload": payload,
    }


def contract_json(relative_path: str) -> dict:
    """Load an authoritative MAIN contract document (never copied into the repo)."""
    path = B3_ROOT.parent / "MAIN" / "contracts" / relative_path
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
