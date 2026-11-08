"""Shared fixtures.

The suite runs against a **real** PostgreSQL instance and a **real** NATS JetStream
server: `RQ-B-4-10`, `RQ-B-4-20` and the acceptance criteria behind them are statements
about a database and a broker, and a mocked driver would verify nothing. If either is
unreachable the suite fails with an explicit message rather than skipping, because a
silently skipped integration suite is indistinguishable from a passing one.

The application role is the least-privilege `autograder_b4` role created by the
provisioning entry point; the privileged DSN is used only for fixtures that must arrange
state the API deliberately cannot create (an admin bootstrap, a cross-schema probe).

**The suite is destructive, so it is pinned to the module-local instance.** `clean_database`
truncates every `b4.*` table before each test, `migrated` drops and re-creates the schema,
and the messaging tests purge JetStream streams by name. Pointed at the shared platform
database (55432) and broker (54222) -- which is what `B4/.env` supplies, because that file
documents the *integration* target -- a routine `uv run pytest` therefore deletes the
integration fixtures the composition host and the browser E2E run against, and also
silently drops events the running host still has to consume. The loss is indistinguishable
from "the seed never ran", which is a genuinely expensive way to lose an afternoon.

The environment is therefore set **here, at import time**, before any fixture calls
`load_environment_file()` (that loader never overwrites an existing key, so a value set
here always wins). Defaults point at `B4/compose.yaml` (PostgreSQL 55435, NATS 54224); the
`B4_TEST_*` variables override them for CI. Same split as B3's module-local database.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from autograder_platform import HealthReport
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError

from autograder_b4.config import Settings
from autograder_b4.db.engine import create_engine_from_settings
from autograder_b4.db.tables import (
    classes,
    courses,
    enrollments,
    sections,
    users,
)
from autograder_b4.db.tables import (
    roles as roles_table,
)
from autograder_b4.ops.dotenv import load_environment_file
from autograder_b4.plugin import B4Module
from autograder_b4.security.passwords import hash_password

#: Module-local instances published by `B4/compose.yaml`. Never the platform ports.
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://autograder_b4:change-me@127.0.0.1:55435/autograder"
)
DEFAULT_TEST_ADMIN_DATABASE_URL = (
    "postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55435/autograder"
)
DEFAULT_TEST_NATS_URL = "nats://127.0.0.1:54224"

#: The database container `B4/compose.yaml` publishes (`docker compose -p b4 up -d`). Used
#: by the backup test, which drives the runbook's own `pg_dump`-in-the-container procedure;
#: the container is named by the compose project, so it is stated here rather than deduced.
POSTGRES_CONTAINER = os.environ.get("B4_TEST_POSTGRES_CONTAINER", "b4-postgres-1")

# Pinned at import time, before any fixture calls `load_environment_file()` -- that loader
# never overwrites an existing key, so a value set here always wins over `B4/.env`.
os.environ["DATABASE_URL"] = os.environ.get("B4_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
os.environ["B4_ADMIN_DATABASE_URL"] = os.environ.get(
    "B4_TEST_ADMIN_DATABASE_URL", DEFAULT_TEST_ADMIN_DATABASE_URL
)
os.environ["NATS_URL"] = os.environ.get("B4_TEST_NATS_URL", DEFAULT_TEST_NATS_URL)
# Shared token for internal endpoints (B2 mail intake looks up identities).
# Pinned here so a developer machine config cannot leak in.
os.environ["B4_SERVICE_TOKEN"] = os.environ.get("B4_TEST_SERVICE_TOKEN", "test-service-token")

B4_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = B4_ROOT.parent

#: Every table B4 owns, in an order that TRUNCATE ... CASCADE accepts. The Alembic
#: version table is excluded: it records schema state, not test data.
TRUNCATE_ORDER = (
    "assignment_notification_deliveries",
    "assignment_notifications",
    "audit_records",
    "enrollments",
    "classes",
    "roster_import_rows",
    "roster_import_files",
    "roster_imports",
    "registration_codes",
    "registration_allowlist",
    "registration_allowlist_batches",
    "dlq_messages",
    "inbox_events",
    "outbox_events",
    "login_attempts",
    "password_reset_codes",
    "sessions",
    "credentials",
    "roles",
    "sections",
    "courses",
    "users",
)


@dataclass
class TestHostContext:
    """The `ModuleContext` the host hands to each module, as MAIN's host defines it."""

    settings: dict[str, str] = field(default_factory=dict)

    def health(self) -> HealthReport:
        return HealthReport("host", True, "test host")


@pytest.fixture
def workspace_tmp():
    """A scratch directory inside the repository.

    `tmp_path` resolves to the system temporary directory, which is outside the workspace
    and therefore not a location the suite may write to; this fixture keeps scratch files
    where the project can create and clean them up.
    """
    import shutil

    root = B4_ROOT / ".pytest-tmp" / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def settings() -> Settings:
    load_environment_file()
    return Settings.from_environment()


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Engine:
    engine = create_engine_from_settings(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        pytest.fail(
            f"PostgreSQL is not reachable with DATABASE_URL; the suite requires a real "
            f"database ({type(error).__name__}). Start it with "
            f"`docker compose up -d postgres` in MAIN/ and run the provisioning step.",
            pytrace=False,
        )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def admin_dsn(settings: Settings) -> str:
    import os

    dsn = os.getenv("B4_ADMIN_DATABASE_URL", "")
    if not dsn:
        pytest.fail(
            "B4_ADMIN_DATABASE_URL is required for the provisioning fixtures", pytrace=False
        )
    return dsn


@pytest.fixture(scope="session")
def admin_engine(admin_dsn: str) -> Engine:
    engine = create_engine(admin_dsn)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_test_instance(admin_engine: Engine) -> None:
    """Refuse to run against an unmigrated test instance, with a runnable fix.

    The suite does not migrate for you -- `test_empty_database_migrates_from_zero` owns
    the "from zero" gate and builds its own throwaway database to prove it. What this
    guard buys is the difference between a first run that says *what to do* and one that
    dies inside a fixture with `relation "b4.audit_records" does not exist` and no hint
    that `alembic upgrade head` was the missing step.
    """
    from sqlalchemy import inspect

    if not inspect(admin_engine).has_table("users", schema="b4"):
        pytest.fail(
            "the test instance has no `b4` schema; migrate it first:\n"
            "  docker compose -f B4/compose.yaml -p b4 up -d\n"
            "  $env:DATABASE_URL = "
            "'postgresql+psycopg://autograder_platform:change-me-local-only"
            "@127.0.0.1:55435/autograder'\n"
            "  uv run alembic upgrade head\n"
            "  uv run python -m autograder_b4.ops.provision",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def clean_database(admin_engine: Engine):
    """Start every test from an empty `b4` schema, with no leftover probe tables.

    B4's own tables only: the fixture never touches `b2`/`b3`, which is also what keeps a
    test run from disturbing another module's data. Probe tables created by the
    table-standard tests are dropped as well, so a test that fails before its own cleanup
    cannot poison the next one.

    The privileged DSN does the truncating because TRUNCATE is not -- and should not be --
    part of the application role's grant set: `autograder_b4` gets SELECT/INSERT/UPDATE/
    DELETE so a leaked request handler cannot wipe a table. Arranging fixture state is
    exactly the case that DSN exists for.
    """
    import os

    from sqlalchemy import inspect

    if os.getenv("B4_KEEP_TEST_DATA") == "1":  # pragma: no cover - debugging aid
        yield
        return
    with admin_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE " + ", ".join(f"b4.{name}" for name in TRUNCATE_ORDER) + " CASCADE")
        )
    inspector = inspect(admin_engine)
    known = set(TRUNCATE_ORDER) | {"alembic_version_b4"}
    for table in inspector.get_table_names(schema="b4"):
        if table not in known:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP TABLE IF EXISTS b4."{table}" CASCADE'))
    yield


@pytest.fixture(scope="session")
def module(settings: Settings) -> B4Module:
    """One B4 module instance for the whole session.

    Building it exercises startup validation (registry drift, contract digest, signing
    key), so a drift fails the suite the same way it would fail the host.
    """
    return B4Module(settings=settings)


@pytest.fixture
def app(module: B4Module) -> FastAPI:
    application = FastAPI(title="B4 test host")
    context = TestHostContext(settings={})
    application.state.modules = [module]
    application.state.context = context
    application.include_router(module.router(context))
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """A client that speaks HTTPS.

    The refresh credential is `Secure` by policy, and an HTTP client legitimately refuses
    to send such a cookie. Serving the suite over `https://testserver` keeps the production
    attribute mandatory instead of weakening it for the tests.
    """
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


class Factory:
    """Arranges state through direct SQL.

    Used only for preconditions the contract deliberately cannot create -- an
    administrator, a course-scoped grant, a cross-schema probe. Everything that *is* an
    API operation is exercised as one in the tests.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def user(
        self,
        *,
        username: str,
        password: str = "correct horse battery staple",
        roles: tuple[str, ...] = ("student",),
        display_name: str | None = None,
        student_number: str | None = None,
        email: str | None = None,
        active: bool = True,
    ) -> uuid.UUID:
        user_id = uuid.uuid4()
        now = datetime.now(UTC).replace(microsecond=0)
        with self.engine.begin() as connection:
            connection.execute(
                users.insert().values(
                    user_id=user_id,
                    username=username.lower(),
                    display_name=display_name or username,
                    student_number=student_number,
                    email=email,
                    is_active=active,
                    created_at=now,
                    updated_at=now,
                )
            )
            from autograder_b4.db.tables import credentials

            hashed = hash_password(password)
            connection.execute(
                credentials.insert().values(
                    credential_id=uuid.uuid4(),
                    user_id=user_id,
                    password_hash=hashed.hash,
                    hash_algorithm=hashed.algorithm,
                    created_at=now,
                    updated_at=now,
                )
            )
            for role in roles:
                connection.execute(
                    roles_table.insert().values(
                        role_id=uuid.uuid4(),
                        user_id=user_id,
                        role=role,
                        course_id=None,
                        created_at=now,
                    )
                )
        return user_id

    def course(self, *, code: str, title: str = "Course", created_by: uuid.UUID | None = None):
        course_id = uuid.uuid4()
        now = datetime.now(UTC).replace(microsecond=0)
        with self.engine.begin() as connection:
            connection.execute(
                courses.insert().values(
                    course_id=course_id,
                    course_code=code,
                    title=title,
                    created_by=created_by,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        return course_id

    def section(
        self,
        *,
        course_id: uuid.UUID,
        code: str,
        term: str | None = None,
        created_by=None,
        class_name: str | None = None,
    ) -> uuid.UUID:
        """Create a section and its first class, returning the section id.

        v0.32.0 made a class its own entity below the section: an enrollment names
        course, section *and* class, so a section without a class cannot hold a roster row.
        Creating one default class here keeps every existing fixture working while
        `class_` / `default_class` expose the new level explicitly.
        """
        section_id = uuid.uuid4()
        now = datetime.now(UTC).replace(microsecond=0)
        with self.engine.begin() as connection:
            connection.execute(
                sections.insert().values(
                    section_id=section_id,
                    course_id=course_id,
                    section_code=code,
                    term=term,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._insert_class(
                connection,
                section_id=section_id,
                class_name=class_name or f"{code}班",
                source="manual",
                created_by=created_by,
                now=now,
            )
        return section_id

    def _insert_class(
        self,
        connection,
        *,
        section_id: uuid.UUID,
        class_name: str,
        source: str = "manual",
        batch_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> uuid.UUID:
        class_id = uuid.uuid4()
        moment = now or datetime.now(UTC).replace(microsecond=0)
        connection.execute(
            classes.insert().values(
                class_id=class_id,
                section_id=section_id,
                class_name=class_name,
                source=source,
                allowlist_batch_id=batch_id,
                created_by=created_by,
                created_at=moment,
                updated_at=moment,
            )
        )
        return class_id

    def class_(
        self,
        *,
        section_id: uuid.UUID,
        class_name: str,
        source: str = "manual",
        batch_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Insert one class directly (arranging preconditions the API would normally make)."""
        with self.engine.begin() as connection:
            return self._insert_class(
                connection,
                section_id=section_id,
                class_name=class_name,
                source=source,
                batch_id=batch_id,
                created_by=created_by,
            )

    def default_class(self, section_id: uuid.UUID) -> uuid.UUID:
        """The single class of a section created through `section` (or the API)."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(classes.c.class_id).where(classes.c.section_id == section_id)
            ).fetchall()
        if len(rows) != 1:
            raise AssertionError(
                f"section {section_id} has {len(rows)} classes; pass class_id explicitly"
            )
        return rows[0][0]

    def _class_for_enrollment(
        self, section_id: uuid.UUID | None, class_id: uuid.UUID | None
    ) -> uuid.UUID:
        """Resolve the class an enrollment belongs to, creating a default one if needed."""
        if class_id is not None:
            with self.engine.connect() as connection:
                row = connection.execute(
                    select(classes.c.class_id, classes.c.section_id).where(
                        classes.c.class_id == class_id
                    )
                ).fetchone()
            if row is None:
                raise AssertionError(f"class {class_id} does not exist")
            if section_id is not None and row.section_id != section_id:
                raise AssertionError(
                    f"class {class_id} belongs to section {row.section_id}, not {section_id}"
                )
            return class_id
        if section_id is None:
            raise AssertionError("factory.enrollment needs section_id or class_id")
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(classes.c.class_id).where(classes.c.section_id == section_id)
            ).fetchall()
            if len(rows) == 1:
                return rows[0][0]
            if len(rows) > 1:
                raise AssertionError(
                    f"section {section_id} has {len(rows)} classes; pass class_id explicitly"
                )
            section_code = connection.execute(
                select(sections.c.section_code).where(sections.c.section_id == section_id)
            ).scalar_one()
            return self._insert_class(
                connection, section_id=section_id, class_name=f"{section_code}班"
            )

    def grant(self, *, user_id: uuid.UUID, course_id: uuid.UUID, role: str = "teacher") -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        with self.engine.begin() as connection:
            connection.execute(
                roles_table.insert().values(
                    role_id=uuid.uuid4(),
                    user_id=user_id,
                    role=role,
                    course_id=course_id,
                    created_at=now,
                )
            )

    def enrollment(
        self,
        *,
        section_id: uuid.UUID | None = None,
        class_id: uuid.UUID | None = None,
        student_number: str,
        display_name: str = "Student",
        user_id: uuid.UUID | None = None,
        source: str = "manual",
        is_active: bool = True,
    ) -> uuid.UUID:
        """Insert one roster row; course/section/class are derived from the class.

        The class defaults to the section's only class (creating one if a test created the
        section through the API), because v0.32.0 requires an enrollment to name all three
        levels and the old `factory.enrollment(section_id=...)` calls must keep working.
        """
        resolved_class = self._class_for_enrollment(section_id, class_id)
        enrollment_id = uuid.uuid4()
        now = datetime.now(UTC).replace(microsecond=0)
        with self.engine.begin() as connection:
            row = connection.execute(
                select(classes.c.section_id).where(classes.c.class_id == resolved_class)
            ).one()
            actual_section = row.section_id
            if section_id is not None and section_id != actual_section:
                raise AssertionError(
                    f"class {resolved_class} belongs to section {actual_section}, "
                    f"not {section_id}"
                )
            course_id = connection.execute(
                select(sections.c.course_id).where(sections.c.section_id == actual_section)
            ).scalar_one()
            connection.execute(
                enrollments.insert().values(
                    enrollment_id=enrollment_id,
                    course_id=course_id,
                    section_id=actual_section,
                    class_id=resolved_class,
                    user_id=user_id,
                    student_number=student_number,
                    display_name=display_name,
                    source=source,
                    roster_import_id=None,
                    is_active=is_active,
                    created_at=now,
                    updated_at=now,
                )
            )
        return enrollment_id

    def count(self, table) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(text("count(*)")).select_from(table)).scalar_one())

    def enrollment_count(self) -> int:
        return self.count(enrollments)


@pytest.fixture
def factory(engine: Engine) -> Factory:
    return Factory(engine)


@dataclass(frozen=True)
class Session:
    """A signed-in caller: its token, id and cookies."""

    user_id: uuid.UUID
    username: str
    access_token: str
    refresh_token: str | None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


def sign_in(client: TestClient, username: str, password: str) -> Session:
    response = client.post(
        "/api/v1/auth/session", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    cookies = response.cookies
    return Session(
        user_id=uuid.UUID(_subject_of(body["access_token"])),
        username=username,
        access_token=body["access_token"],
        refresh_token=cookies.get("autograder_refresh"),
    )


def _subject_of(token: str) -> str:
    import jwt

    claims = jwt.decode(token, options={"verify_signature": False})
    return str(claims["sub"])


@pytest.fixture
def sign_in_as(client: TestClient):
    def _sign_in(username: str, password: str = "correct horse battery staple") -> Session:
        return sign_in(client, username, password)

    return _sign_in
