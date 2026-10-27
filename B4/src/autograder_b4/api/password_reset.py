"""Password reset through an e-mailed verification code.

The flow has two public operations, mounted next to the session endpoints because they
share the `/api/v1/auth` prefix:

* ``POST /api/v1/auth/password-reset/code`` looks the address up and mails a six-digit
  code when -- and only when -- it belongs to an active account. The response is the same
  for every syntactically valid address, so the endpoint cannot be used to enumerate
  accounts. Only a SHA-256 digest bound to the code row is stored.
* ``POST /api/v1/auth/password-reset`` consumes that row exactly once, replaces the
  Argon2id credential, revokes every refresh session of the account, and returns ``204``.
  All code failures share one generic message for the same anti-enumeration reason.

The code is intentionally not tied to a guessable state in the URL: the e-mail is the
lookup key, exactly as it is for registration. The TTL, resend window, attempt cap and
hourly limit reuse the existing registration settings rather than introducing a second
set of limits for the same mail channel.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Connection, and_, delete, func, select, update

from .. import registration as domain
from ..audit import (
    ACTION_PASSWORD_RESET_CODE_SENT,
    ACTION_PASSWORD_RESET_COMPLETED,
    ACTION_PASSWORD_RESET_REJECTED,
    OUTCOME_DENIED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    AuditLog,
)
from ..db.tables import (
    credentials,
    login_attempts,
    password_reset_codes,
    sessions,
    users,
)
from ..errors import service_unavailable, unprocessable
from ..logging import get_logger
from ..security import mailer
from ..security.mailer import MailDeliveryError
from ..security.passwords import hash_password
from .dependencies import B4State
from .problem import ProblemRoute

logger = get_logger("password_reset.api")

CODE_PATTERN = r"^[0-9]{6}$"
MAX_EMAIL_LENGTH = 320

#: One message for every code rejection. A caller must not be able to tell "this address
#: has no account" from "the code was wrong" (or from "the code expired").
GENERIC_CODE_ERROR = "验证码不正确或已过期,请重新获取。"


class PasswordResetCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=MAX_EMAIL_LENGTH)


class PasswordResetCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=MAX_EMAIL_LENGTH)
    code: str = Field(pattern=CODE_PATTERN)
    new_password: str = Field(min_length=1, max_length=domain.MAX_PASSWORD_LENGTH)


def _find_user(connection: Connection, *, email: str):
    """The active/inactive account carrying this normalized mailbox, or `None`."""
    return connection.execute(
        select(users.c.user_id, users.c.username, users.c.is_active)
        .where(
            users.c.email.is_not(None),
            # Mirrors the partial unique index on lower(btrim(email)); the index is what
            # guarantees at most one row.
            func.lower(func.btrim(users.c.email)) == email,
        )
        .limit(1)
    ).fetchone()


def _last_code_age_seconds(connection: Connection, *, user_id: uuid.UUID, email: str) -> int | None:
    created = connection.execute(
        select(password_reset_codes.c.created_at)
        .where(
            and_(
                password_reset_codes.c.user_id == user_id,
                password_reset_codes.c.email == email,
            )
        )
        .order_by(password_reset_codes.c.created_at.desc())
        .limit(1)
    ).scalars().first()
    if created is None:
        return None
    return max(0, int((datetime.now(UTC) - created).total_seconds()))


def _recent_code_count(connection: Connection, *, email: str, within_seconds: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
    return int(
        connection.execute(
            select(func.count())
            .select_from(password_reset_codes)
            .where(
                and_(
                    password_reset_codes.c.email == email,
                    password_reset_codes.c.created_at >= cutoff,
                )
            )
        ).scalar_one()
    )


def _open_code(connection: Connection, *, user_id: uuid.UUID, email: str):
    return connection.execute(
        select(password_reset_codes)
        .where(
            and_(
                password_reset_codes.c.user_id == user_id,
                password_reset_codes.c.email == email,
                password_reset_codes.c.consumed_at.is_(None),
                password_reset_codes.c.expires_at > datetime.now(UTC),
            )
        )
        .order_by(password_reset_codes.c.created_at.desc())
        .limit(1)
    ).fetchone()


def build_password_reset_router(state: B4State) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)
    settings = state.settings
    engine = state.engine

    @router.post("/api/v1/auth/password-reset/code", status_code=202)
    def request_password_reset_code(payload: PasswordResetCodeRequest) -> dict[str, int]:
        email = domain.normalize_email(str(payload.email))

        # Refuse globally when the channel is absent, rather than accepting a request whose
        # code could never be delivered. This check runs before the account lookup, so the
        # answer does not reveal whether an address belongs to an account.
        if not settings.smtp_configured:
            raise service_unavailable(
                "the password reset mail channel",
                "SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM are not configured",
            )

        ttl_seconds = settings.registration_code_ttl_seconds
        resend_seconds = settings.registration_resend_seconds
        code: str | None = None
        code_id = uuid.uuid4()
        target_user_id: uuid.UUID | None = None

        with engine.begin() as write:
            user = _find_user(write, email=email)
            if user is not None and user.is_active:
                age = _last_code_age_seconds(
                    write, user_id=user.user_id, email=email
                )
                recent = _recent_code_count(write, email=email, within_seconds=3600)
                if (
                    (age is None or age >= resend_seconds)
                    and recent < settings.registration_email_hourly_limit
                ):
                    code = domain.generate_code()
                    target_user_id = user.user_id
                    write.execute(
                        password_reset_codes.insert().values(
                            code_id=code_id,
                            user_id=user.user_id,
                            email=email,
                            code_hash=domain.hash_code(code_id, code),
                            attempts=0,
                            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
                            consumed_at=None,
                        )
                    )

        if code is not None and target_user_id is not None:
            try:
                mailer.send_password_reset_code(
                    settings, to=email, code=code, ttl_seconds=ttl_seconds
                )
            except MailDeliveryError as error:
                # The intent row was already committed. Without a delivered code it can only
                # produce a failed confirmation, so remove it; otherwise the resend window
                # would block the caller from trying again.
                with engine.begin() as cleanup:
                    cleanup.execute(
                        delete(password_reset_codes).where(
                            password_reset_codes.c.code_id == code_id
                        )
                    )
                    AuditLog(cleanup).record(
                        action=ACTION_PASSWORD_RESET_REJECTED,
                        outcome=OUTCOME_FAILURE,
                        target_type="user",
                        target_id=target_user_id,
                        detail="password reset mail could not be delivered",
                    )
                logger.warning(
                    "password_reset.code.delivery_failed",
                    extra={"event": "password_reset.code.delivery_failed"},
                )
                raise service_unavailable(
                    "the password reset mail channel", str(error)
                ) from error

            with engine.begin() as guard:
                AuditLog(guard).record(
                    action=ACTION_PASSWORD_RESET_CODE_SENT,
                    outcome=OUTCOME_SUCCESS,
                    target_type="user",
                    target_id=target_user_id,
                    detail="password reset code issued",
                )
            logger.info(
                "password_reset.code.issued",
                extra={
                    "event": "password_reset.code.issued",
                    "user_id": str(target_user_id),
                },
            )

        # Same shape and values for known, unknown and inactive addresses.
        return {
            "expires_in_seconds": ttl_seconds,
            "resend_after_seconds": resend_seconds,
        }

    @router.post("/api/v1/auth/password-reset", status_code=204)
    def complete_password_reset(payload: PasswordResetCompleteRequest) -> Response:
        email = domain.normalize_email(str(payload.email))
        new_password = domain.validate_password(payload.new_password)
        now = datetime.now(UTC)
        rejection: str | None = None
        reset_user_id: uuid.UUID | None = None

        with engine.begin() as write:
            user = _find_user(write, email=email)
            if user is None or not user.is_active:
                rejection = "no_account"
            else:
                row = _open_code(write, user_id=user.user_id, email=email)
                if row is None:
                    rejection = "no_code"
                elif row.attempts >= settings.registration_max_attempts:
                    rejection = "attempts"
                    AuditLog(write).record(
                        action=ACTION_PASSWORD_RESET_REJECTED,
                        outcome=OUTCOME_DENIED,
                        target_type="user",
                        target_id=user.user_id,
                        detail="password reset attempts exhausted",
                    )
                elif not domain.code_matches(row.code_id, payload.code, row.code_hash):
                    write.execute(
                        update(password_reset_codes)
                        .where(password_reset_codes.c.code_id == row.code_id)
                        .values(attempts=row.attempts + 1)
                    )
                    # The increment must be committed before the error is raised; otherwise
                    # an attacker can retry the same code forever.
                    AuditLog(write).record(
                        action=ACTION_PASSWORD_RESET_REJECTED,
                        outcome=OUTCOME_DENIED,
                        target_type="user",
                        target_id=user.user_id,
                        detail="password reset code did not match",
                    )
                    rejection = "mismatch"
                else:
                    consumed = write.execute(
                        update(password_reset_codes)
                        .where(
                            password_reset_codes.c.code_id == row.code_id,
                            password_reset_codes.c.consumed_at.is_(None),
                        )
                        .values(consumed_at=now)
                    ).rowcount
                    if not consumed:
                        rejection = "no_code"
                    else:
                        hashed = hash_password(new_password)
                        updated = write.execute(
                            update(credentials)
                            .where(credentials.c.user_id == user.user_id)
                            .values(
                                password_hash=hashed.hash,
                                hash_algorithm=hashed.algorithm,
                                updated_at=now,
                            )
                        ).rowcount
                        if not updated:
                            # An account without a credential row must not acquire one
                            # through the reset path.
                            rejection = "no_credential"
                        else:
                            # Every other outstanding reset code for this account dies with
                            # the one that was just consumed.
                            write.execute(
                                update(password_reset_codes)
                                .where(
                                    and_(
                                        password_reset_codes.c.user_id == user.user_id,
                                        password_reset_codes.c.consumed_at.is_(None),
                                    )
                                )
                                .values(consumed_at=now)
                            )
                            # Refresh credentials are the durable half of a session. Access
                            # tokens already issued stay valid only until their short TTL.
                            write.execute(
                                update(sessions)
                                .where(
                                    and_(
                                        sessions.c.user_id == user.user_id,
                                        sessions.c.revoked_at.is_(None),
                                    )
                                )
                                .values(
                                    revoked_at=now,
                                    revoked_reason="password_reset",
                                )
                            )
                            # Let the owner sign in immediately: stale failed-login attempts
                            # must not keep the account throttled after a successful reset.
                            principal_key = hashlib.sha256(
                                user.username.strip().lower().encode("utf-8")
                            ).hexdigest()
                            write.execute(
                                delete(login_attempts).where(
                                    and_(
                                        login_attempts.c.principal_key == principal_key,
                                        login_attempts.c.succeeded.is_(False),
                                    )
                                )
                            )
                            AuditLog(write).record(
                                action=ACTION_PASSWORD_RESET_COMPLETED,
                                outcome=OUTCOME_SUCCESS,
                                target_type="user",
                                target_id=user.user_id,
                                detail="password replaced; all refresh sessions revoked",
                            )
                            reset_user_id = user.user_id

        if rejection is not None:
            # One generic answer for every rejection path: the caller cannot distinguish
            # "unknown address", "no open code", "wrong code" or "attempts exhausted".
            raise unprocessable(GENERIC_CODE_ERROR)

        logger.info(
            "password_reset.completed",
            extra={
                "event": "password_reset.completed",
                "user_id": str(reset_user_id),
            },
        )
        return Response(status_code=204)

    return router
