"""Operator entry point for course-scoped role grants.

`b4.roles` distinguishes a platform role (course_id IS NULL) from a course-scoped one.
Course creation records the creator's grant, and enrollment records a student's scope;
granting an *additional* teacher or assistant to an existing course has no operation in
`MAIN/contracts/openapi/b4.yaml` today.

Rather than invent a path -- which ADR-0003 forbids -- B4 exposes the grant as an
operator action, so the authorization model is operable and testable now, and the missing
HTTP operation can be added later through the contract change protocol.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..authorization import COURSE_STAFF_ROLES, ROLES
from ..config import ConfigurationError, Settings
from ..db.engine import create_engine_from_settings
from ..db.tables import courses, roles, users
from ..logging import configure_logging, get_logger

logger = get_logger("ops.roles")


def grant_course_role(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    role: str,
    granted_by: uuid.UUID | None,
) -> bool:
    """Idempotently grant a course-scoped role. Returns True when a row was created."""
    from datetime import UTC, datetime

    engine = create_engine_from_settings(settings)
    with engine.begin() as connection:
        if connection.execute(
            select(users.c.user_id).where(users.c.user_id == user_id)
        ).fetchone() is None:
            raise LookupError(f"user {user_id} does not exist")
        if connection.execute(
            select(courses.c.course_id).where(courses.c.course_id == course_id)
        ).fetchone() is None:
            raise LookupError(f"course {course_id} does not exist")
        result = connection.execute(
            pg_insert(roles)
            .values(
                role_id=uuid.uuid4(),
                user_id=user_id,
                role=role,
                course_id=course_id,
                granted_by=granted_by,
                created_at=datetime.now(UTC).replace(microsecond=0),
            )
            .on_conflict_do_nothing(index_elements=["user_id", "role", "course_id"])
            .returning(roles.c.role_id)
        )
        # `fetchone()` decides this, not `rowcount`: psycopg3 reports -1 for inserts.
        created = result.fetchone() is not None
    logger.info(
        "ops.role.granted",
        extra={
            "event": "ops.role.granted",
            "role": role,
            "course_id": str(course_id),
            "user_id": str(user_id),
            # Not "created": that key is reserved on LogRecord (the record timestamp), so
            # `logging` raises `Attempt to overwrite 'created' in LogRecord` and the whole
            # operator command fails. Integration hit exactly that on the first grant.
            "grant_created": created,
        },
    )
    return created


def revoke_course_role(
    settings: Settings, *, user_id: uuid.UUID, course_id: uuid.UUID, role: str
) -> int:
    from sqlalchemy import and_, delete

    engine = create_engine_from_settings(settings)
    with engine.begin() as connection:
        result = connection.execute(
            delete(roles).where(
                and_(
                    roles.c.user_id == user_id,
                    roles.c.course_id == course_id,
                    roles.c.role == role,
                )
            )
        )
    return result.rowcount


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    configure_logging()
    load_environment_file()
    parser = argparse.ArgumentParser(description="Grant or revoke a course-scoped B4 role.")
    parser.add_argument("action", choices=["grant", "revoke"])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--role", required=True, choices=sorted(COURSE_STAFF_ROLES))
    parser.add_argument("--granted-by", default=None)
    arguments = parser.parse_args(argv)

    if arguments.role not in ROLES:  # pragma: no cover - argparse constrains this
        print(f"error: unknown role {arguments.role}", file=sys.stderr)
        return 2
    try:
        settings = Settings.from_environment()
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        if arguments.action == "grant":
            created = grant_course_role(
                settings,
                user_id=uuid.UUID(arguments.user_id),
                course_id=uuid.UUID(arguments.course_id),
                role=arguments.role,
                granted_by=uuid.UUID(arguments.granted_by) if arguments.granted_by else None,
            )
            print("granted" if created else "already granted")
            return 0
        removed = revoke_course_role(
            settings,
            user_id=uuid.UUID(arguments.user_id),
            course_id=uuid.UUID(arguments.course_id),
            role=arguments.role,
        )
        print(f"revoked {removed} grant(s)")
        return 0
    except LookupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
