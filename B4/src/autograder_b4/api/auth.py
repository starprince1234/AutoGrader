"""Authentication: session creation, rotation, logout and the JWKS document.

The three properties the requirements single out are implemented here and nowhere else:

* the refresh credential exists **only** as an `HttpOnly` + `Secure` + `SameSite` cookie
  -- it is generated, hashed for storage, and written to a `Set-Cookie` header; it never
  enters a response body, a URL or a log line;
* the access token is short-lived Ed25519 and appears only in the body of the two session
  operations that mint it;
* failures are uniform. An unknown principal, a deactivated principal and a wrong
  password produce the same `401` with the same detail, and the unknown-principal path
  still performs an Argon2id verification so the three are not distinguishable by timing.

Failed attempts are counted per principal in `b4.login_attempts` and throttled, so the
login endpoint cannot be used to enumerate or brute-force accounts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, and_, delete, func, select, update

from ..audit import (
    ACTION_LOGIN_DENIED,
    ACTION_LOGIN_THROTTLED,
    ACTION_SESSION_CREATED,
    ACTION_SESSION_DELETED,
    ACTION_SESSION_REFRESHED,
    ACTION_SESSION_REUSE_DETECTED,
    OUTCOME_DENIED,
    OUTCOME_SUCCESS,
    AuditLog,
)
from ..db.tables import credentials, login_attempts, sessions, users
from ..errors import too_many_requests, unauthenticated
from ..logging import get_logger
from ..security.passwords import (
    burn_verification_time,
    hash_password,
    needs_rehash,
    verify_password,
)
from ..security.refresh import (
    cookie_attributes,
    expired_cookie,
    generate_refresh_token,
    hash_refresh_token,
)
from ..security.tokens import issue_access_token
from .dependencies import B4State, Dependencies, actor_of
from .problem import ProblemRoute

logger = get_logger("auth")

COOKIE_PATH = "/api/v1/auth"
MAX_USERNAME_LENGTH = 200
MAX_PASSWORD_LENGTH = 400


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


@dataclass(frozen=True)
class _ResolvedSession:
    session_id: uuid.UUID
    user_id: uuid.UUID
    family_id: uuid.UUID


def build_auth_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)
    settings = state.settings
    engine = state.engine

    def _principal_key(username: str) -> str:
        """Hash the username for the throttling ledger: no identifier is stored raw."""
        import hashlib

        return hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()

    def _client_key(request: Request) -> str | None:
        import hashlib

        client = request.client
        if client is None or not client.host:
            return None
        return hashlib.sha256(client.host.encode("utf-8")).hexdigest()

    def _record_attempt(
        connection: Connection, *, principal_key: str, client_key: str | None, succeeded: bool
    ) -> None:
        connection.execute(
            login_attempts.insert().values(
                attempt_id=uuid.uuid4(),
                principal_key=principal_key,
                client_key=client_key,
                succeeded=succeeded,
            )
        )

    def _prune_attempts(connection: Connection, principal_key: str) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=settings.login_failure_window_seconds * 4)
        connection.execute(
            delete(login_attempts).where(
                and_(
                    login_attempts.c.principal_key == principal_key,
                    login_attempts.c.occurred_at < cutoff,
                )
            )
        )

    def _recent_failures(connection: Connection, principal_key: str) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=settings.login_failure_window_seconds)
        return int(
            connection.execute(
                select(func.count())
                .select_from(login_attempts)
                .where(
                    and_(
                        login_attempts.c.principal_key == principal_key,
                        login_attempts.c.succeeded.is_(False),
                        login_attempts.c.occurred_at >= cutoff,
                    )
                )
            ).scalar_one()
        )

    def _set_refresh_cookie(response: Response, token: str) -> None:
        response.headers.append(
            "set-cookie",
            cookie_attributes(
                name=settings.refresh_cookie_name,
                value=token,
                max_age=settings.refresh_token_ttl_seconds,
                secure=settings.refresh_cookie_secure,
                path=COOKIE_PATH,
            ),
        )

    def _open_session(
        connection: Connection,
        *,
        user_id: uuid.UUID,
        family_id: uuid.UUID | None = None,
    ) -> tuple[_ResolvedSession, str]:
        session_id = uuid.uuid4()
        refresh_token = generate_refresh_token()
        session = _ResolvedSession(
            session_id=session_id,
            user_id=user_id,
            family_id=family_id or uuid.uuid4(),
        )
        connection.execute(
            sessions.insert().values(
                session_id=session.session_id,
                user_id=session.user_id,
                family_id=session.family_id,
                refresh_token_hash=hash_refresh_token(refresh_token),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=settings.refresh_token_ttl_seconds),
            )
        )
        return session, refresh_token

    def _scoped_roles(connection: Connection, user_id: uuid.UUID):
        from ..authorization import resolve_principal

        return resolve_principal(connection, user_id)

    @router.post("/api/v1/auth/session")
    def create_session(
        payload: SessionRequest,
        request: Request,
        response: Response,
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        principal_key = _principal_key(payload.username)
        client_key = _client_key(request)

        # The throttling decision is taken inside the transaction that records it, but the
        # error is raised *after* that transaction commits. Raising inside would roll the
        # attempt and its audit record back, so the ledger would never fill up and the
        # `429` would leave no trace of who was refused.
        throttled = False
        with engine.begin() as guard:
            _prune_attempts(guard, principal_key)
            if _recent_failures(guard, principal_key) >= settings.login_failure_limit:
                _record_attempt(
                    guard, principal_key=principal_key, client_key=client_key, succeeded=False
                )
                AuditLog(guard).record(
                    action=ACTION_LOGIN_THROTTLED,
                    outcome=OUTCOME_DENIED,
                    detail="login throttled: too many recent failures",
                )
                throttled = True
        if throttled:
            raise too_many_requests("Too many failed sign-in attempts. Try again later.")

        row = connection.execute(
            select(
                users.c.user_id,
                users.c.is_active,
                users.c.username,
                credentials.c.password_hash,
                credentials.c.hash_algorithm,
                credentials.c.credential_id,
            )
            .select_from(users.join(credentials, credentials.c.user_id == users.c.user_id))
            .where(users.c.username == payload.username.strip().lower())
        ).fetchone()

        if row is None or not row.is_active:
            # Spend the same work as a real verification so the two paths are not
            # distinguishable by timing.
            burn_verification_time(payload.password)
            with engine.begin() as guard:
                _record_attempt(
                    guard, principal_key=principal_key, client_key=client_key, succeeded=False
                )
                AuditLog(guard).record(
                    action=ACTION_LOGIN_DENIED,
                    outcome=OUTCOME_DENIED,
                    detail="credentials rejected",
                )
            raise unauthenticated()

        if not verify_password(payload.password, row.password_hash, row.hash_algorithm):
            with engine.begin() as guard:
                _record_attempt(
                    guard, principal_key=principal_key, client_key=client_key, succeeded=False
                )
                AuditLog(guard).record(
                    action=ACTION_LOGIN_DENIED,
                    outcome=OUTCOME_DENIED,
                    target_type="user",
                    target_id=row.user_id,
                    detail="credentials rejected",
                )
            raise unauthenticated()

        principal = _scoped_roles(connection, row.user_id)
        token, claims = issue_access_token(
            signing_key=state.signing_key,
            subject=str(row.user_id),
            roles=principal.all_roles,
            scope=principal.as_scopes(),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            ttl_seconds=settings.access_token_ttl_seconds,
        )
        with engine.begin() as write:
            if needs_rehash(row.password_hash, row.hash_algorithm):
                # Re-hash on successful login so weaker legacy hashes converge upward.
                refreshed = hash_password(payload.password)
                write.execute(
                    update(credentials)
                    .where(credentials.c.credential_id == row.credential_id)
                    .values(
                        password_hash=refreshed.hash,
                        hash_algorithm=refreshed.algorithm,
                        updated_at=datetime.now(UTC),
                    )
                )
            _prune_attempts(write, principal_key)
            _record_attempt(
                write, principal_key=principal_key, client_key=client_key, succeeded=True
            )
            _, refresh_token = _open_session(write, user_id=row.user_id)
            AuditLog(write).record(
                action=ACTION_SESSION_CREATED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(principal),
                target_type="user",
                target_id=row.user_id,
                detail="session created",
            )
        _set_refresh_cookie(response, refresh_token)
        logger.info(
            "auth.session.created",
            extra={"event": "auth.session.created", "user_id": str(row.user_id)},
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.access_token_ttl_seconds,
            # The token carries only `sub`, so the client needs the login name here
            # (and on refresh) to show who is signed in.
            "username": row.username,
            "roles": list(claims.roles),
            "scope": [entry.as_claim() for entry in claims.scope],
        }

    @router.post("/api/v1/auth/session/refresh")
    def refresh_session(request: Request, response: Response) -> dict[str, object]:
        presented = request.cookies.get(settings.refresh_cookie_name)
        if not presented:
            raise unauthenticated()
        digest = hash_refresh_token(presented)
        # The security response to a replayed credential *is* the revocation, so it must
        # be committed before the `401` is raised: raising inside the transaction would
        # roll the revocation back and leave the stolen family alive.
        rejection: str | None = None
        refresh_token: str | None = None
        token: str | None = None
        user_id: uuid.UUID | None = None
        username: str = ""
        with engine.begin() as connection:
            row = connection.execute(
                select(sessions).where(sessions.c.refresh_token_hash == digest)
            ).fetchone()
            if row is None:
                rejection = "unknown"
            elif row.revoked_at is not None:
                rejection = "revoked"
            elif row.rotated_at is not None:
                # A rotated credential is being presented again: either a replay or a
                # stolen copy. Revoke the whole family so neither party keeps a session.
                connection.execute(
                    update(sessions)
                    .where(
                        and_(
                            sessions.c.family_id == row.family_id,
                            sessions.c.revoked_at.is_(None),
                        )
                    )
                    .values(revoked_at=datetime.now(UTC), revoked_reason="refresh_token_reuse")
                )
                AuditLog(connection).record(
                    action=ACTION_SESSION_REUSE_DETECTED,
                    outcome=OUTCOME_DENIED,
                    target_type="session",
                    target_id=row.session_id,
                    detail="a rotated refresh credential was presented again; family revoked",
                )
                logger.warning(
                    "auth.session.reuse_detected",
                    extra={"event": "auth.session.reuse_detected", "user_id": str(row.user_id)},
                )
                rejection = "reuse"
            elif row.expires_at <= datetime.now(UTC):
                rejection = "expired"
            else:
                user_row = connection.execute(
                    select(users.c.user_id, users.c.is_active, users.c.username).where(
                        users.c.user_id == row.user_id
                    )
                ).fetchone()
                if user_row is None or not user_row.is_active:
                    rejection = "inactive"
                else:
                    successor, refresh_token = _open_session(
                        connection, user_id=row.user_id, family_id=row.family_id
                    )
                    connection.execute(
                        update(sessions)
                        .where(sessions.c.session_id == row.session_id)
                        .values(
                            rotated_at=datetime.now(UTC),
                            replaced_by=successor.session_id,
                            updated_at=datetime.now(UTC),
                        )
                    )
                    principal = _scoped_roles(connection, row.user_id)
                    token, _ = issue_access_token(
                        signing_key=state.signing_key,
                        subject=str(row.user_id),
                        roles=principal.all_roles,
                        scope=principal.as_scopes(),
                        issuer=settings.jwt_issuer,
                        audience=settings.jwt_audience,
                        ttl_seconds=settings.access_token_ttl_seconds,
                    )
                    user_id = row.user_id
                    username = user_row.username
                    AuditLog(connection).record(
                        action=ACTION_SESSION_REFRESHED,
                        outcome=OUTCOME_SUCCESS,
                        actor=actor_of(principal),
                        target_type="session",
                        target_id=successor.session_id,
                        detail="refresh credential rotated",
                    )
        if rejection is not None:
            raise unauthenticated()
        logger.info(
            "auth.session.refreshed",
            extra={"event": "auth.session.refreshed", "user_id": str(user_id)},
        )
        _set_refresh_cookie(response, refresh_token or "")
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.access_token_ttl_seconds,
            "username": username,
            "roles": list(principal.all_roles),
            "scope": [entry.as_claim() for entry in principal.as_scopes()],
        }

    @router.delete("/api/v1/auth/session/logout", status_code=204)
    def delete_session(
        request: Request,
        caller=Depends(dependencies.principal),
    ) -> Response:
        presented = request.cookies.get(settings.refresh_cookie_name)
        revoked = 0
        if presented:
            digest = hash_refresh_token(presented)
            with engine.begin() as connection:
                result = connection.execute(
                    update(sessions)
                    .where(
                        and_(
                            sessions.c.refresh_token_hash == digest,
                            sessions.c.revoked_at.is_(None),
                        )
                    )
                    .values(revoked_at=datetime.now(UTC), revoked_reason="logout")
                )
                revoked = result.rowcount
                AuditLog(connection).record(
                    action=ACTION_SESSION_DELETED,
                    outcome=OUTCOME_SUCCESS,
                    actor=actor_of(caller),
                    target_type="user",
                    target_id=caller.user_id,
                    detail=f"logout revoked {revoked} session(s)",
                )
        logger.info(
            "auth.session.deleted",
            extra={"event": "auth.session.deleted", "user_id": str(caller.user_id)},
        )
        return Response(
            status_code=204,
            headers={
                "set-cookie": expired_cookie(
                    settings.refresh_cookie_name,
                    COOKIE_PATH,
                    secure=settings.refresh_cookie_secure,
                )
            },
        )

    @router.get("/api/v1/auth/jwks")
    def get_jwks() -> dict[str, object]:
        """Public key set. Unauthenticated by design: every module verifies locally."""
        return state.signing_key.jwks()

    return router
