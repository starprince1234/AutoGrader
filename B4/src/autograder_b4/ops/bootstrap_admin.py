"""Operator entry point: create the first administrator.

Why this exists: every other user is created through `POST /api/v1/users`, and that
endpoint requires an administrator. With an empty database there is therefore no way in
-- the initial grant cannot come from the API it protects. This op closes that
bootstrapping gap; B4's own traceability notes used to list it as `待实现`.

Design choices, and why:

* Idempotent. Re-running with the same username is a no-op that reports what already
  exists, so a deploy script can call it unconditionally.
* Password from `--password` or `B4_BOOTSTRAP_ADMIN_PASSWORD`. Never printed, never
  logged, and refused when empty. The plaintext is not retained after hashing.
* Argon2id via `security.passwords`, the same code path the login endpoint verifies
  with, so a bootstrapped credential is not a special case.
* Platform role (`course_id IS NULL`) with `admin`, matching `authorization.ROLES`.
* Runs on the least-privilege `autograder_b4` role: it writes only the `b4` tables it
  owns, so bootstrapping does not require (or grant) elevated database access.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authorization import ROLES
from ..config import ConfigurationError, Settings
from ..db.engine import create_engine_from_settings
from ..db.tables import credentials, roles, users
from ..logging import configure_logging, get_logger
from ..security.passwords import hash_password

logger = get_logger("ops.bootstrap_admin")

DEFAULT_ROLE = "admin"


def bootstrap_admin(
    settings: Settings,
    *,
    username: str,
    password: str,
    display_name: str,
    role: str = DEFAULT_ROLE,
) -> tuple[uuid.UUID, bool]:
    """Ensure an administrator exists. Returns ``(user_id, created)``."""
    username = username.strip().lower()
    if not username:
        raise SystemExit("refusing to bootstrap: empty username")
    if not password:
        raise SystemExit("refusing to bootstrap: no password supplied")
    if role not in ROLES:
        raise SystemExit(f"refusing to bootstrap: unknown role {role!r}")
    if role != DEFAULT_ROLE:
        raise SystemExit("refusing to bootstrap: the first user must be an admin")

    engine = create_engine_from_settings(settings)
    try:
        with Session(engine) as session:
            existing = session.execute(
                select(users.c.user_id).where(users.c.username == username)
            ).scalar_one_or_none()
            if existing is not None:
                has_credential = session.execute(
                    select(credentials.c.credential_id).where(
                        credentials.c.user_id == existing
                    )
                ).scalar_one_or_none()
                if has_credential is None:
                    _attach_credential(session, existing, password)
                    session.commit()
                    logger.info(
                        "bootstrap.credential_added",
                        extra={"event": "bootstrap.credential_added", "username": username},
                    )
                return existing, False

            now = datetime.now(UTC)
            user_id = uuid.uuid4()
            session.execute(
                users.insert().values(
                    user_id=user_id,
                    username=username,
                    display_name=display_name.strip() or username,
                    student_number=None,
                    email=None,
                    is_active=True,
                    deactivated_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            _attach_credential(session, user_id, password)
            session.execute(
                roles.insert().values(
                    role_id=uuid.uuid4(),
                    user_id=user_id,
                    role=role,
                    course_id=None,
                    granted_by=None,
                    created_at=now,
                )
            )
            session.commit()
            logger.info(
                "bootstrap.admin_created",
                extra={
                    "event": "bootstrap.admin_created",
                    "username": username,
                    "role": role,
                    "user_id": str(user_id),
                },
            )
            return user_id, True
    finally:
        engine.dispose()


def _attach_credential(session: Session, user_id: uuid.UUID, password: str) -> None:
    hashed = hash_password(password)
    now = datetime.now(UTC)
    session.execute(
        credentials.insert().values(
            credential_id=uuid.uuid4(),
            user_id=user_id,
            password_hash=hashed.hash,
            hash_algorithm=hashed.algorithm,
            created_at=now,
            updated_at=now,
        )
    )


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    load_environment_file()
    parser = argparse.ArgumentParser(description="Create the first B4 administrator.")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--display-name", default="Platform Administrator")
    parser.add_argument("--password", default=None)
    arguments = parser.parse_args(argv)

    import os

    password = arguments.password or os.getenv("B4_BOOTSTRAP_ADMIN_PASSWORD", "")
    try:
        settings = Settings.from_environment()
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    configure_logging(20)
    user_id, created = bootstrap_admin(
        settings,
        username=arguments.username,
        password=password,
        display_name=arguments.display_name,
    )
    verb = "created" if created else "already present"
    print(f"administrator {arguments.username!r} {verb} (user_id={user_id})")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
