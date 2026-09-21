"""User and role management API (admin only).

Every operation here writes an audit record in the same transaction as the change, and
the audit stores a digest of the before/after values plus the names of the changed fields
-- not the values themselves, which for a user record are personal data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Connection, and_, select, update

from ..audit import (
    ACTION_USER_CREATED,
    ACTION_USER_DEACTIVATED,
    ACTION_USER_UPDATED,
    OUTCOME_SUCCESS,
    AuditLog,
    change_digest,
    changed_fields,
)
from ..authorization import ROLES
from ..db.tables import credentials, users
from ..db.tables import roles as roles_table
from ..errors import bad_request, conflict, not_found, unprocessable
from ..logging import get_logger
from ..security.passwords import hash_password
from .dependencies import B4State, Dependencies, actor_of
from .pagination import clamp_limit, descending_page_filter, page_envelope
from .problem import ProblemRoute
from .timestamps import rfc3339
from .timestamps import same_instant as _same_instant

logger = get_logger("users")

MAX_USERNAME_LENGTH = 200
MAX_PASSWORD_LENGTH = 400
MAX_DISPLAY_NAME_LENGTH = 200
MAX_STUDENT_NUMBER_LENGTH = 64
MAX_EMAIL_LENGTH = 320


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH)
    student_number: str | None = Field(default=None, max_length=MAX_STUDENT_NUMBER_LENGTH)
    email: EmailStr | None = None
    roles: list[str] = Field(min_length=1)
    password: str | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    email: EmailStr | None = None
    roles: list[str] | None = None
    expected_updated_at: datetime | None = None


def _validate_roles(values: list[str]) -> list[str]:
    unknown = sorted({value for value in values if value not in ROLES})
    if unknown:
        raise unprocessable(f"unknown role(s): {', '.join(unknown)}")
    if not values:
        raise unprocessable("at least one role is required")
    return sorted(set(values))


def _now() -> datetime:
    """Current time; the column rounds it to the millisecond on store."""
    return datetime.now(UTC)


def _user_document(row, role_values: list[str]) -> dict[str, object]:
    return {
        "user_id": str(row.user_id),
        # `username` is the login identifier and the only human-recognisable handle an
        # administrator has; without it a user-management screen can list rows but
        # cannot tell who they are, and nothing can be looked up by login name.
        "username": row.username,
        "display_name": row.display_name,
        "student_number": row.student_number,
        "roles": sorted(role_values),
        "created_at": rfc3339(row.created_at),
    }


def _platform_roles(connection: Connection, user_id: uuid.UUID) -> list[str]:
    rows = connection.execute(
        select(roles_table.c.role).where(
            and_(roles_table.c.user_id == user_id, roles_table.c.course_id.is_(None))
        )
    ).fetchall()
    return [row[0] for row in rows]


def build_users_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)
    engine = state.engine

    @router.get("/api/v1/users")
    def list_users(
        caller=Depends(dependencies.administrator),
        connection: Connection = Depends(dependencies.connection),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1, le=200),
        role: str | None = Query(default=None),
    ) -> dict[str, object]:
        size = clamp_limit(limit)
        if role is not None and role not in ROLES:
            raise bad_request(f"unknown role filter: {role}")
        statement = select(users).order_by(users.c.created_at.desc(), users.c.user_id.desc())
        cursor_filter = descending_page_filter(users.c.created_at, users.c.user_id, cursor)
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        if role is not None:
            statement = statement.where(
                users.c.user_id.in_(select(roles_table.c.user_id).where(roles_table.c.role == role))
            )
        rows = connection.execute(statement.limit(size)).fetchall()
        items = [_user_document(row, _platform_roles(connection, row.user_id)) for row in rows]
        keys = [(row.created_at, row.user_id) for row in rows]
        return page_envelope(items, keys, size)

    @router.post("/api/v1/users", status_code=201)
    def create_user(
        payload: UserCreate,
        caller=Depends(dependencies.administrator),
    ) -> dict[str, object]:
        role_values = _validate_roles(payload.roles)
        username = payload.username.strip().lower()
        student_number = (payload.student_number or "").strip() or None
        email = str(payload.email).strip().lower() if payload.email else None
        user_id = uuid.uuid4()
        now = _now()
        with engine.begin() as connection:
            clash = connection.execute(
                select(users.c.user_id, users.c.username, users.c.student_number).where(
                    (users.c.username == username)
                    | (
                        users.c.student_number == student_number
                        if student_number is not None
                        else users.c.username == username
                    )
                )
            ).fetchall()
            for row in clash:
                if row.username == username:
                    raise conflict("A user with that username already exists.")
                if student_number is not None and row.student_number == student_number:
                    raise conflict("A user with that student number already exists.")
            connection.execute(
                users.insert().values(
                    user_id=user_id,
                    username=username,
                    display_name=payload.display_name.strip(),
                    student_number=student_number,
                    email=email,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            if payload.password:
                hashed = hash_password(payload.password)
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
            for role in role_values:
                connection.execute(
                    roles_table.insert().values(
                        role_id=uuid.uuid4(),
                        user_id=user_id,
                        role=role,
                        course_id=None,
                        granted_by=caller.user_id,
                        created_at=now,
                    )
                )
            AuditLog(connection).record(
                action=ACTION_USER_CREATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="user",
                target_id=user_id,
                detail=f"created user with roles {role_values}",
            )
        logger.info("user.created", extra={"event": "user.created", "target_user_id": str(user_id)})
        return {"user_id": str(user_id)}

    @router.patch("/api/v1/users/{user_id}")
    def update_user(
        user_id: uuid.UUID,
        payload: UserUpdate,
        caller=Depends(dependencies.administrator),
    ) -> dict[str, object]:
        role_values = _validate_roles(payload.roles) if payload.roles is not None else None
        now = _now()
        with engine.begin() as connection:
            row = connection.execute(select(users).where(users.c.user_id == user_id)).fetchone()
            if row is None:
                raise not_found("The user does not exist.")
            if payload.expected_updated_at is not None and not _same_instant(
                row.updated_at, payload.expected_updated_at
            ):
                raise conflict("The user was modified since it was read; re-read and retry.")
            before = {
                "display_name": row.display_name,
                "email": row.email,
                "roles": sorted(_platform_roles(connection, user_id)),
            }
            updates: dict[str, object] = {}
            if payload.display_name is not None:
                updates["display_name"] = payload.display_name.strip()
            if payload.email is not None:
                stripped = str(payload.email).strip().lower()
                updates["email"] = stripped or None  # type: ignore[assignment]
            if updates:
                updates["updated_at"] = now
                connection.execute(
                    update(users).where(users.c.user_id == user_id).values(**updates)
                )
            if role_values is not None:
                connection.execute(
                    roles_table.delete().where(
                        and_(
                            roles_table.c.user_id == user_id,
                            roles_table.c.course_id.is_(None),
                        )
                    )
                )
                for role in role_values:
                    connection.execute(
                        roles_table.insert().values(
                            role_id=uuid.uuid4(),
                            user_id=user_id,
                            role=role,
                            course_id=None,
                            granted_by=caller.user_id,
                            created_at=now,
                        )
                    )
            after = {
                "display_name": updates.get("display_name", row.display_name),
                "email": updates.get("email", row.email),
                "roles": role_values if role_values is not None else before["roles"],
            }
            fields = changed_fields(before, after)
            AuditLog(connection).record(
                action=ACTION_USER_UPDATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="user",
                target_id=user_id,
                digest=change_digest(before, after),
                detail=f"changed fields: {', '.join(fields) or 'none'}",
            )
        logger.info("user.updated", extra={"event": "user.updated", "target_user_id": str(user_id)})
        return {"user_id": str(user_id), "changed_fields": fields}

    @router.delete("/api/v1/users/{user_id}", status_code=204)
    def delete_user(
        user_id: uuid.UUID,
        caller=Depends(dependencies.administrator),
    ):
        from fastapi import Response

        now = _now()
        with engine.begin() as connection:
            row = connection.execute(select(users).where(users.c.user_id == user_id)).fetchone()
            if row is None:
                raise not_found("The user does not exist.")
            connection.execute(
                update(users)
                .where(users.c.user_id == user_id)
                .values(is_active=False, deactivated_at=now, updated_at=now)
            )
            # Deactivation must end the sessions: otherwise the account keeps working
            # until every access token and refresh credential expires on its own.
            from sqlalchemy import update as sql_update

            from ..db.tables import sessions

            connection.execute(
                sql_update(sessions)
                .where(and_(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None)))
                .values(revoked_at=now, revoked_reason="user_deactivated")
            )
            AuditLog(connection).record(
                action=ACTION_USER_DEACTIVATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="user",
                target_id=user_id,
                detail="user deactivated and sessions revoked",
            )
        logger.info(
            "user.deactivated", extra={"event": "user.deactivated", "target_user_id": str(user_id)}
        )
        return Response(status_code=204)

    return router
