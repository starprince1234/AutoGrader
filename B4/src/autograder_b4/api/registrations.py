"""Self-service registration endpoints and the administrator's allowlist import.

The division of labour is deliberate:

* ``POST /api/v1/auth/registrations/code`` proves nothing yet -- it validates the claimed
  identity against the allowlist, then mails a code. Storing the intent (and only a digest of
  the code) is what lets the confirmation step create exactly the account that was verified.
* ``POST /api/v1/auth/registrations`` verifies that code and creates the account in one
  transaction. There is no approval state: once this returns ``201`` the user can sign in.
* ``/api/v1/registration-allowlists`` is the administrator's half: the allowlist is the
  identity anchor, so only an administrator may replace it, and every replacement is audited.

Failure handling follows the module's existing rule: a decision is committed *before* the
error is raised, so a refused code still increments the attempt counter and still leaves an
audit record. Raising inside the transaction would roll both back and let an attacker retry
forever with the same code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Connection, delete, func, select, update
from sqlalchemy.exc import IntegrityError

from .. import registration as domain
from ..audit import (
    ACTION_ALLOWLIST_REPLACED,
    ACTION_REGISTRATION_CODE_SENT,
    ACTION_REGISTRATION_CREATED,
    ACTION_REGISTRATION_REJECTED,
    OUTCOME_DENIED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    AuditLog,
)
from ..db.tables import registration_codes, users
from ..errors import (
    conflict,
    payload_too_large,
    service_unavailable,
    too_many_requests,
    unprocessable,
)
from ..logging import get_logger
from ..security import mailer
from ..security.mailer import MailDeliveryError
from .dependencies import B4State, Dependencies, actor_of
from .problem import ProblemRoute
from .timestamps import rfc3339

logger = get_logger("registration.api")

MAX_UPLOAD_CHUNK = 64 * 1024
CODE_PATTERN = r"^[0-9]{6}$"


class RegistrationCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    email: EmailStr
    student_number: str = Field(min_length=13, max_length=13)
    display_name: str = Field(min_length=1, max_length=domain.MAX_DISPLAY_NAME_LENGTH)


class RegistrationCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    code: str = Field(pattern=CODE_PATTERN)
    password: str = Field(min_length=1, max_length=domain.MAX_PASSWORD_LENGTH)


def _username_taken(connection: Connection, username: str) -> bool:
    return (
        connection.execute(select(users.c.user_id).where(users.c.username == username)).fetchone()
        is not None
    )


def _email_taken(connection: Connection, email: str) -> bool:
    return (
        connection.execute(
            select(users.c.user_id).where(
                users.c.email.is_not(None),
                # Application-side check mirrors the partial unique index; the index is what
                # actually wins a race, this only turns the common case into a readable 409.
                func.lower(func.btrim(users.c.email)) == email,
            )
        ).fetchone()
        is not None
    )


def _student_number_taken(connection: Connection, student_number: str) -> bool:
    return (
        connection.execute(
            select(users.c.user_id).where(users.c.student_number == student_number)
        ).fetchone()
        is not None
    )


async def _read_bounded(upload: UploadFile, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(MAX_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise payload_too_large(f"名单文件超过 {maximum} 字节上限。")
        chunks.append(chunk)
    return b"".join(chunks)


def build_registration_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)
    engine = state.engine
    settings = state.settings

    @router.post("/api/v1/auth/registrations/code", status_code=202)
    def request_registration_code(
        payload: RegistrationCodeRequest,
        request: Request,
    ) -> dict[str, object]:
        username = domain.validate_username(payload.username)
        email = domain.normalize_email(str(payload.email))
        student_number = domain.validate_student_number(payload.student_number)
        display_name = domain.validate_display_name(payload.display_name)

        rejection: str | None = None
        wait_seconds: int | None = None
        code_id = uuid.uuid4()
        code: str | None = None

        with engine.begin() as write:
            if _username_taken(write, username):
                rejection = "username"
            elif _email_taken(write, email):
                rejection = "email"
            elif _student_number_taken(write, student_number):
                rejection = "student_number"
            else:
                entry = domain.allowlist_entry(
                    write, student_number=student_number, display_name=display_name
                )
                if entry is None:
                    rejection = "allowlist"

            if rejection is None:
                age = domain.last_code_age_seconds(write, email=email)
                if age is not None and age < settings.registration_resend_seconds:
                    rejection = "cooldown"
                    wait_seconds = settings.registration_resend_seconds - age
                elif (
                    domain.recent_code_count(write, email=email, within_seconds=3600)
                    >= settings.registration_email_hourly_limit
                ):
                    rejection = "hourly"
                    wait_seconds = 3600

            if rejection is None:
                code = domain.generate_code()
                write.execute(
                    registration_codes.insert().values(
                        code_id=code_id,
                        email=email,
                        username=username,
                        student_number=student_number,
                        display_name=display_name,
                        code_hash=domain.hash_code(code_id, code),
                        attempts=0,
                        expires_at=datetime.now(UTC)
                        + timedelta(seconds=settings.registration_code_ttl_seconds),
                    )
                )

        if rejection is not None:
            with engine.begin() as guard:
                AuditLog(guard).record(
                    action=ACTION_REGISTRATION_REJECTED,
                    outcome=OUTCOME_DENIED,
                    target_type="registration",
                    detail=f"code request refused at the {rejection} check",
                )
            if rejection == "cooldown":
                raise too_many_requests(
                    f"请 {max(1, wait_seconds or 1)} 秒后再获取验证码。"
                )
            if rejection == "hourly":
                raise too_many_requests("该邮箱一小时内获取验证码的次数已达上限,请稍后再试。")
            if rejection == "username":
                raise conflict("该用户名已被使用。")
            if rejection == "email":
                raise conflict("该邮箱已被其他账号绑定。")
            if rejection == "student_number":
                raise conflict("该学号已被注册。")
            raise unprocessable(
                "姓名或学号不在注册名单中。若名单刚更新或信息有误,请联系课程管理员。"
            )

        if code is None:  # pragma: no cover - the guarded branch above always sets it
            raise unprocessable("验证码未能生成,请重试。")
        try:
            mailer.send_verification_code(
                settings, to=email, code=code, ttl_seconds=settings.registration_code_ttl_seconds
            )
        except MailDeliveryError as error:
            # The intent row was already committed; without a delivered code it can only produce
            # a failed confirmation, so remove it and report the channel as unavailable.
            with engine.begin() as cleanup:
                cleanup.execute(
                    delete(registration_codes).where(registration_codes.c.code_id == code_id)
                )
                AuditLog(cleanup).record(
                    action=ACTION_REGISTRATION_REJECTED,
                    outcome=OUTCOME_FAILURE,
                    target_type="registration",
                    detail="verification mail could not be delivered",
                )
            raise service_unavailable("the registration mail channel", str(error)) from error

        with engine.begin() as guard:
            AuditLog(guard).record(
                action=ACTION_REGISTRATION_CODE_SENT,
                outcome=OUTCOME_SUCCESS,
                target_type="registration",
                target_id=code_id,
                detail="verification code issued",
            )
        logger.info(
            "registration.code.issued",
            extra={
                "event": "registration.code.issued",
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return {
            "expires_in_seconds": settings.registration_code_ttl_seconds,
            "resend_after_seconds": settings.registration_resend_seconds,
        }

    @router.post("/api/v1/auth/registrations", status_code=201)
    def create_registration(payload: RegistrationCompleteRequest) -> dict[str, object]:
        email = domain.normalize_email(str(payload.email))
        password = domain.validate_password(payload.password)

        rejection: str | None = None
        created_user_id: uuid.UUID | None = None
        created_username: str | None = None
        duplicating_field: str | None = None

        try:
            with engine.begin() as write:
                row = domain.find_open_code(write, email=email)
                if row is None:
                    rejection = "no_code"
                elif row.attempts >= settings.registration_max_attempts:
                    rejection = "attempts"
                elif not domain.code_matches(row.code_id, payload.code, row.code_hash):
                    # Committed before the raise: an attempt that is rolled back is not an attempt.
                    write.execute(
                        update(registration_codes)
                        .where(registration_codes.c.code_id == row.code_id)
                        .values(attempts=row.attempts + 1)
                    )
                    AuditLog(write).record(
                        action=ACTION_REGISTRATION_REJECTED,
                        outcome=OUTCOME_DENIED,
                        target_type="registration",
                        target_id=row.code_id,
                        detail="verification code did not match",
                    )
                    rejection = "mismatch"
                else:
                    consumed = write.execute(
                        update(registration_codes)
                        .where(
                            registration_codes.c.code_id == row.code_id,
                            registration_codes.c.consumed_at.is_(None),
                        )
                        .values(consumed_at=datetime.now(UTC))
                    ).rowcount
                    if not consumed:
                        rejection = "no_code"
                    else:
                        # The world may have changed since the code was issued (another account
                        # took the username, the allowlist was replaced, the student number was
                        # registered). Re-check rather than trusting a ten-minute-old decision.
                        entry = domain.allowlist_entry(
                            write,
                            student_number=row.student_number,
                            display_name=row.display_name,
                        )
                        if entry is None:
                            rejection = "allowlist"
                        elif _username_taken(write, row.username):
                            duplicating_field = "username"
                        elif _email_taken(write, email):
                            duplicating_field = "email"
                        elif _student_number_taken(write, row.student_number):
                            duplicating_field = "student_number"
                        else:
                            created_user_id = domain.create_account(
                                write,
                                username=row.username,
                                email=email,
                                student_number=row.student_number,
                                display_name=row.display_name,
                                password=password,
                            )
                            created_username = row.username
                            AuditLog(write).record(
                                action=ACTION_REGISTRATION_CREATED,
                                outcome=OUTCOME_SUCCESS,
                                target_type="user",
                                target_id=created_user_id,
                                detail="self-service registration completed",
                            )
        except IntegrityError as error:
            # The unique indexes are the real arbiter of a race; translate instead of leaking SQL.
            raise conflict("该用户名、邮箱或学号已被占用。") from error

        if rejection == "no_code":
            raise unprocessable("验证码无效或已过期,请重新获取。")
        if rejection == "attempts":
            raise too_many_requests("验证码尝试次数过多,请重新获取。")
        if rejection == "mismatch":
            raise unprocessable("验证码不正确。")
        if rejection == "allowlist":
            raise unprocessable("该身份已不在注册名单中,请联系课程管理员。")
        if duplicating_field is not None:
            detail = {
                "username": "该用户名已被使用。",
                "email": "该邮箱已被其他账号绑定。",
                "student_number": "该学号已被注册。",
            }[duplicating_field]
            raise conflict(detail)

        if created_user_id is None or created_username is None:  # pragma: no cover
            raise unprocessable("注册未完成,请重试。")
        logger.info(
            "registration.created",
            extra={"event": "registration.created", "user_id": str(created_user_id)},
        )
        return {"user_id": str(created_user_id), "username": created_username}

    @router.post("/api/v1/registration-allowlists")
    async def replace_registration_allowlist(
        file: Annotated[UploadFile, File()],
        caller=Depends(dependencies.administrator),
    ) -> dict[str, object]:
        filename = (file.filename or "").strip()
        if not filename.lower().endswith(".csv"):
            raise unprocessable("注册名单必须是 .csv 文件。")
        content = await _read_bounded(file, settings.registration_allowlist_max_bytes)
        try:
            rows = domain.parse_allowlist_csv(content)
        except domain.AllowlistError as error:
            raise unprocessable(str(error)) from error

        with engine.begin() as write:
            result = domain.replace_allowlist(
                write,
                filename=filename,
                content=content,
                rows=rows,
                uploaded_by=caller.user_id,
            )
            AuditLog(write).record(
                action=ACTION_ALLOWLIST_REPLACED,
                outcome=OUTCOME_SUCCESS,
                actor=actor_of(caller),
                target_type="registration_allowlist",
                target_id=result["batch_id"],
                detail=(
                    f"rows={result['row_count']} replaced={result['replaced_row_count']} "
                    f"sha256={result['content_sha256'][:12]}"
                ),
            )
        logger.info(
            "registration.allowlist.replaced",
            extra={
                "event": "registration.allowlist.replaced",
                "row_count": result["row_count"],
                "replaced_row_count": result["replaced_row_count"],
            },
        )
        return {
            "batch_id": str(result["batch_id"]),
            "row_count": result["row_count"],
            "replaced_row_count": result["replaced_row_count"],
            "content_sha256": result["content_sha256"],
        }

    @router.get("/api/v1/registration-allowlists")
    def get_registration_allowlist(
        caller=Depends(dependencies.administrator),
        connection: Connection = Depends(dependencies.connection),
    ) -> dict[str, object]:
        batch = domain.active_batch(connection)
        if batch is None:
            return {"batch_id": None, "filename": None, "row_count": 0, "uploaded_at": None}
        return {
            "batch_id": str(batch["batch_id"]),
            "filename": batch["filename"],
            "row_count": batch["row_count"],
            "uploaded_at": rfc3339(batch["uploaded_at"]),
        }

    return router
