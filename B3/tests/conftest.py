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
TEACHER_ID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def tokens() -> dict[str, dict]:
    return {
        "teacher-a": {
            "sub": TEACHER_ID,
            "roles": ["teacher"],
            "scope": [{"course_id": str(COURSE_A), "section_id": str(SECTION_A)}],
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
        "student-a": {
            "sub": str(uuid.uuid4()),
            "roles": ["student"],
            "scope": [{"course_id": str(COURSE_A), "section_id": str(SECTION_A)}],
        },
        "assistant-a": {
            "sub": str(uuid.uuid4()),
            "roles": ["assistant"],
            "scope": [{"course_id": str(COURSE_A)}],
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


@pytest.fixture
def dependencies(settings, database, verifier, publisher, clock):
    from autograder_b3.api.deps import B3Dependencies
    from autograder_b3.auth import ServiceTokenVerifier

    return B3Dependencies(
        settings=settings,
        database=database,
        token_verifier=verifier,
        service_verifier=ServiceTokenVerifier("test-service-token"),
        publisher=publisher,
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
