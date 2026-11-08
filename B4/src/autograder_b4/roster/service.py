"""The roster import state machine: parse -> map -> validate -> preview -> confirm -> write.

The pipeline is the one `RQ-B-1-10` describes, and the two properties that matter most
are enforced structurally rather than by convention:

* **zero writes before confirmation** -- parsed rows live in `b4.roster_import_rows`,
  which is staging. `b4.enrollments` is not touched by create, read or mapping; only
  `confirm` writes it. `tests/test_roster_import.py` asserts the enrollment count is
  still zero after every pre-confirmation step.
* **atomic confirmation** -- the enrollment upserts, the state change, the audit record
  and the outbox event all run on one connection inside one transaction, so a failure
  part-way leaves neither half-written rows nor an event for a change that did not happen.

States are the enum declared in `MAIN/contracts/openapi/b4.yaml`
(`uploaded`, `parsed`, `mapping_required`, `validating`, `previewed`, `committed`,
`rejected`, `failed_parse`). Each one this service persists is a state the process can
actually be interrupted in.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from ..audit import (
    ACTION_ROSTER_IMPORT_COMMITTED,
    ACTION_ROSTER_IMPORT_CREATED,
    ACTION_ROSTER_IMPORT_FAILED,
    ACTION_ROSTER_IMPORT_MAPPING_SET,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    AuditActor,
    AuditLog,
    change_digest,
)
from ..classes import (
    account_name_mismatches,
    active_enrollment_conflicts,
    bound_accounts,
    translate_integrity_error,
)
from ..db.tables import (
    enrollments,
    roster_import_files,
    roster_import_rows,
    roster_imports,
)
from ..errors import conflict, not_found, unprocessable
from ..logging import get_logger
from ..messaging.envelope import roster_import_completed_envelope
from ..messaging.outbox import enqueue
from .mapping import (
    MappingError,
    propose_mapping,
    validate_submitted_mapping,
)
from .parsers import UnsupportedDocument, detect_media_type, parse_roster
from .table import (
    FIELD_DISPLAY_NAME,
    FIELD_STUDENT_NUMBER,
    REQUIRED_FIELDS,
    RosterParseError,
    RosterTable,
)
from .validation import (
    STATUS_ACCEPTED,
    ValidatedRow,
    validate_rows,
)
from .validation import STATUS_REJECTED as ROW_STATUS_REJECTED

logger = get_logger("roster")

#: Import states, matching the enum declared in MAIN/contracts/openapi/b4.yaml.
STATUS_UPLOADED = "uploaded"
STATUS_MAPPING_REQUIRED = "mapping_required"
STATUS_VALIDATING = "validating"
STATUS_PREVIEWED = "previewed"
STATUS_COMMITTED = "committed"
STATUS_REJECTED = "rejected"
STATUS_FAILED_PARSE = "failed_parse"

#: States from which a mapping may still be supplied or corrected.
EDITABLE_STATUSES = (STATUS_UPLOADED, STATUS_MAPPING_REQUIRED, STATUS_VALIDATING, STATUS_PREVIEWED)


@dataclass(frozen=True)
class RowPreview:
    row_number: int
    values: dict[str, str]
    reason: str | None


@dataclass(frozen=True)
class RosterImportView:
    """Everything the `RosterImportPreview` contract shape needs, plus internal counts."""

    roster_import_id: uuid.UUID
    status: str
    course_id: uuid.UUID
    section_id: uuid.UUID
    class_id: uuid.UUID
    filename: str
    media_type: str
    detected_columns: list[str]
    proposed_mapping: dict[str, str]
    confirmed_mapping: dict[str, str] | None
    accepted_rows: list[RowPreview]
    rejected_rows: list[RowPreview]
    accepted_count: int
    rejected_count: int
    parse_detail: str | None
    created_at: datetime


@dataclass(frozen=True)
class ConfirmResult:
    view: RosterImportView
    already_committed: bool
    written: int


def _actor(values: dict[str, Any]) -> AuditActor:
    return AuditActor(
        user_id=values.get("actor_user_id"), roles=tuple(values.get("actor_roles") or ())
    )


def create_import(
    engine: Engine,
    *,
    filename: str,
    content: bytes,
    course_id: uuid.UUID,
    section_id: uuid.UUID,
    class_id: uuid.UUID,
    course_code: str,
    section_code: str,
    actor: AuditActor,
    request_id: str | None,
    correlation_id: str | None,
) -> RosterImportView:
    """Persist the artifact, parse it, and stage a preview. Never writes an enrollment."""
    try:
        media_type = detect_media_type(filename, content)
    except UnsupportedDocument as error:
        # An unsupported document is a rejected request (422), not a server failure: no
        # row is written, so nothing has to be undone.
        raise unprocessable(str(error)) from error
    digest = hashlib.sha256(content).hexdigest()
    roster_import_id = uuid.uuid4()

    # The artifact is committed before parsing so that a crash during a hostile document
    # leaves an auditable 'uploaded' row instead of losing the upload entirely.
    with engine.begin() as connection:
        connection.execute(
            roster_imports.insert().values(
                roster_import_id=roster_import_id,
                course_id=course_id,
                section_id=section_id,
                class_id=class_id,
                status=STATUS_UPLOADED,
                filename=filename,
                media_type=media_type,
                content_sha256=digest,
                size_bytes=len(content),
                detected_columns=[],
                proposed_mapping={},
                created_by=actor.user_id,
            )
        )
        connection.execute(
            roster_import_files.insert().values(
                file_id=uuid.uuid4(), roster_import_id=roster_import_id, content=content
            )
        )
        AuditLog(connection).record(
            action=ACTION_ROSTER_IMPORT_CREATED,
            outcome=OUTCOME_SUCCESS,
            actor=actor,
            target_type="roster_import",
            target_id=roster_import_id,
            course_id=course_id,
            section_id=section_id,
            request_id=request_id,
            correlation_id=correlation_id,
            detail=f"uploaded {media_type} artifact of {len(content)} bytes",
        )

    try:
        table = parse_roster(content, media_type)
    except RosterParseError as error:
        with engine.begin() as connection:
            connection.execute(
                update(roster_imports)
                .where(roster_imports.c.roster_import_id == roster_import_id)
                .values(
                    status=STATUS_FAILED_PARSE, parse_detail=str(error)[:500], updated_at=_now()
                )
            )
            AuditLog(connection).record(
                action=ACTION_ROSTER_IMPORT_FAILED,
                outcome=OUTCOME_FAILURE,
                actor=actor,
                target_type="roster_import",
                target_id=roster_import_id,
                course_id=course_id,
                section_id=section_id,
                request_id=request_id,
                correlation_id=correlation_id,
                detail="parse failed",
            )
        raise unprocessable(f"The roster could not be parsed: {error}") from error

    proposal = propose_mapping(table)
    with engine.begin() as connection:
        if proposal.is_complete:
            counted = _stage_rows(
                connection,
                roster_import_id=roster_import_id,
                table=table,
                mapping=proposal.mapping,
                course_code=course_code,
                section_code=section_code,
            )
            connection.execute(
                update(roster_imports)
                .where(roster_imports.c.roster_import_id == roster_import_id)
                .values(
                    status=STATUS_PREVIEWED,
                    detected_columns=list(table.columns),
                    proposed_mapping=proposal.mapping,
                    parse_detail=_parse_detail(table),
                    accepted_count=counted.accepted,
                    rejected_count=counted.rejected,
                    updated_at=_now(),
                )
            )
        else:
            connection.execute(
                update(roster_imports)
                .where(roster_imports.c.roster_import_id == roster_import_id)
                .values(
                    status=STATUS_MAPPING_REQUIRED,
                    detected_columns=list(table.columns),
                    proposed_mapping=proposal.mapping,
                    parse_detail=proposal.detail or _parse_detail(table),
                    accepted_count=0,
                    rejected_count=0,
                    updated_at=_now(),
                )
            )
    return load_view(engine, roster_import_id)


def load_view(engine: Engine, roster_import_id: uuid.UUID) -> RosterImportView:
    with engine.connect() as connection:
        return _view(connection, roster_import_id)


def _view(connection: Connection, roster_import_id: uuid.UUID) -> RosterImportView:
    row = _load_row(connection, roster_import_id)
    staged = connection.execute(
        select(roster_import_rows)
        .where(roster_import_rows.c.roster_import_id == roster_import_id)
        .order_by(roster_import_rows.c.row_number)
    ).fetchall()
    accepted = [
        RowPreview(item.row_number, dict(item.row_values), item.reason)
        for item in staged
        if item.status == STATUS_ACCEPTED
    ]
    rejected = [
        RowPreview(item.row_number, dict(item.row_values), item.reason)
        for item in staged
        if item.status == STATUS_REJECTED
    ]
    return RosterImportView(
        roster_import_id=row.roster_import_id,
        status=row.status,
        course_id=row.course_id,
        section_id=row.section_id,
        class_id=row.class_id,
        filename=row.filename,
        media_type=row.media_type,
        detected_columns=list(row.detected_columns or []),
        proposed_mapping=dict(row.proposed_mapping or {}),
        confirmed_mapping=dict(row.confirmed_mapping) if row.confirmed_mapping else None,
        accepted_rows=accepted,
        rejected_rows=rejected,
        accepted_count=row.accepted_count,
        rejected_count=row.rejected_count,
        parse_detail=row.parse_detail,
        created_at=row.created_at,
    )


def _load_row(connection: Connection, roster_import_id: uuid.UUID):
    row = connection.execute(
        select(roster_imports).where(roster_imports.c.roster_import_id == roster_import_id)
    ).fetchone()
    if row is None:
        raise not_found("The roster import does not exist.")
    return row


def set_mapping(
    engine: Engine,
    roster_import_id: uuid.UUID,
    mapping: dict[str, str],
    *,
    actor: AuditActor,
    request_id: str | None,
    correlation_id: str | None,
    course_code: str,
    section_code: str,
) -> RosterImportView:
    """Record a human decision about the columns and re-validate every row."""
    with engine.begin() as connection:
        row = _load_row(connection, roster_import_id)
        if row.status == STATUS_COMMITTED:
            raise conflict("This roster import has already been committed.")
        if row.status not in EDITABLE_STATUSES:
            raise conflict(f"A column mapping cannot be set while the import is {row.status}.")
        # 'validating' is committed on its own so an interrupted re-validation is visible
        # as such rather than looking like a preview that never had rows.
        connection.execute(
            update(roster_imports)
            .where(roster_imports.c.roster_import_id == roster_import_id)
            .values(status=STATUS_VALIDATING, updated_at=_now())
        )

    table = _load_table(engine, roster_import_id)
    try:
        clean = validate_submitted_mapping(mapping, table)
    except MappingError as error:
        with engine.begin() as connection:
            connection.execute(
                update(roster_imports)
                .where(roster_imports.c.roster_import_id == roster_import_id)
                .values(status=STATUS_MAPPING_REQUIRED, updated_at=_now())
            )
        raise unprocessable(str(error)) from error

    with engine.begin() as connection:
        counted = _stage_rows(
            connection,
            roster_import_id=roster_import_id,
            table=table,
            mapping=clean,
            course_code=course_code,
            section_code=section_code,
        )
        row = _load_row(connection, roster_import_id)
        connection.execute(
            update(roster_imports)
            .where(roster_imports.c.roster_import_id == roster_import_id)
            .values(
                status=STATUS_PREVIEWED,
                confirmed_mapping=clean,
                parse_detail=_parse_detail(table),
                accepted_count=counted.accepted,
                rejected_count=counted.rejected,
                updated_at=_now(),
            )
        )
        AuditLog(connection).record(
            action=ACTION_ROSTER_IMPORT_MAPPING_SET,
            outcome=OUTCOME_SUCCESS,
            actor=actor,
            target_type="roster_import",
            target_id=roster_import_id,
            course_id=row.course_id,
            section_id=row.section_id,
            request_id=request_id,
            correlation_id=correlation_id,
            digest=change_digest(row.proposed_mapping or {}, clean),
            detail=(
                f"mapped {len(clean)} field(s); "
                f"accepted={counted.accepted} rejected={counted.rejected}"
            ),
        )
    return load_view(engine, roster_import_id)


def confirm_import(
    engine: Engine,
    roster_import_id: uuid.UUID,
    *,
    actor: AuditActor,
    idempotency_key: str | None,
    request_id: str | None,
    correlation_id: str | None,
    course_code: str,
    section_code: str,
) -> ConfirmResult:
    """Write the accepted rows atomically, or report why nothing was written.

    Repeating the confirmation is not an error: the import is already `committed` and the
    same result is returned without a second write. That is what "idempotent" means here
    -- the second call cannot duplicate a row, because it does not write at all.
    """
    with engine.begin() as connection:
        row = _load_row(connection, roster_import_id)
        if row.status == STATUS_COMMITTED:
            if idempotency_key and row.idempotency_key and row.idempotency_key != idempotency_key:
                raise conflict("This roster import was committed with a different idempotency key.")
            return ConfirmResult(_view(connection, roster_import_id), True, 0)
        if row.status != STATUS_PREVIEWED:
            raise conflict(
                f"The roster import is {row.status}; only a previewed import can be committed."
            )
        if idempotency_key:
            clash = connection.execute(
                select(roster_imports.c.roster_import_id).where(
                    and_(
                        roster_imports.c.idempotency_key == idempotency_key,
                        roster_imports.c.roster_import_id != roster_import_id,
                    )
                )
            ).fetchone()
            if clash is not None:
                raise conflict("This idempotency key was already used by another roster import.")

        content = _load_content_from(connection, roster_import_id)
        digest = hashlib.sha256(content).hexdigest()
        if digest != row.content_sha256:
            raise conflict(
                "The stored roster artifact does not match its recorded digest; refusing to commit."
            )

        table = parse_roster(content, row.media_type)
        mapping = dict(row.confirmed_mapping or row.proposed_mapping or {})
        missing = [field for field in REQUIRED_FIELDS if not mapping.get(field)]
        if missing:
            raise conflict("The column mapping is incomplete: " + ", ".join(sorted(missing)))
        validated = validate_rows(
            table, mapping, course_code=course_code, section_code=section_code
        )
        if row.class_id is None:
            raise conflict(
                "This roster import has no target class; re-upload it after choosing a class."
            )
        validated = _apply_binding_constraints(
            connection,
            validated=validated,
            course_id=row.course_id,
            class_id=row.class_id,
        )
        accepted = [item for item in validated if item.accepted]
        rejected = [item for item in validated if not item.accepted]
        _replace_rows(connection, roster_import_id, validated)

        if not accepted:
            # The terminal `rejected` state and its audit record are written here, and the
            # `422` is raised *after* the transaction commits: raising inside would roll
            # both back and leave the import looking previewed with no trace of the attempt.
            connection.execute(
                update(roster_imports)
                .where(roster_imports.c.roster_import_id == roster_import_id)
                .values(
                    status=STATUS_REJECTED,
                    accepted_count=0,
                    rejected_count=len(rejected),
                    idempotency_key=idempotency_key,
                    confirmed_mapping=mapping,
                    updated_at=_now(),
                )
            )
            AuditLog(connection).record(
                action=ACTION_ROSTER_IMPORT_FAILED,
                outcome=OUTCOME_FAILURE,
                actor=actor,
                target_type="roster_import",
                target_id=roster_import_id,
                course_id=row.course_id,
                section_id=row.section_id,
                request_id=request_id,
                correlation_id=correlation_id,
                detail=f"every row was rejected ({len(rejected)} rows); nothing was written",
            )
            rejected_all = True
        else:
            rejected_all = False
            try:
                written = _upsert_enrollments(
                    connection,
                    roster_import_id=roster_import_id,
                    course_id=row.course_id,
                    section_id=row.section_id,
                    class_id=row.class_id,
                    accepted=accepted,
                )
            except IntegrityError as error:
                # The one-active-enrollment index rejecting a concurrent confirm: report a
                # clear 409 and roll the whole import back instead of a 500 or a silent pick.
                raise translate_integrity_error(error) from error
            committed_at = _now()
            connection.execute(
                update(roster_imports)
                .where(roster_imports.c.roster_import_id == roster_import_id)
                .values(
                    status=STATUS_COMMITTED,
                    accepted_count=len(accepted),
                    rejected_count=len(rejected),
                    committed_at=committed_at,
                    confirmed_mapping=mapping,
                    idempotency_key=idempotency_key,
                    updated_at=committed_at,
                )
            )
            AuditLog(connection).record(
                action=ACTION_ROSTER_IMPORT_COMMITTED,
                outcome=OUTCOME_SUCCESS,
                actor=actor,
                target_type="roster_import",
                target_id=roster_import_id,
                course_id=row.course_id,
                section_id=row.section_id,
                request_id=request_id,
                correlation_id=correlation_id,
                detail=(
                    f"committed {len(accepted)} row(s), rejected {len(rejected)}; "
                    f"{written} enrollment(s) written"
                ),
            )
            # Same transaction as the rows: the event exists exactly when the change does.
            enqueue(
                connection,
                roster_import_completed_envelope(
                    roster_import_id=roster_import_id,
                    course_id=row.course_id,
                    section_id=row.section_id,
                    accepted_rows=len(accepted),
                    rejected_rows=len(rejected),
                    correlation_id=_correlation_uuid(correlation_id),
                    occurred_at=committed_at,
                ),
            )
            logger.info(
                "roster.import.committed",
                extra={
                    "event": "roster.import.committed",
                    "roster_import_id": str(roster_import_id),
                    "section_id": str(row.section_id),
                    "accepted_rows": len(accepted),
                    "rejected_rows": len(rejected),
                },
            )
            result = ConfirmResult(_view(connection, roster_import_id), False, written)
    if rejected_all:
        raise unprocessable(
            "Every roster row was rejected, so no enrollment was written; "
            "correct the rows and re-confirm."
        )
    return result


def _apply_binding_constraints(
    connection: Connection,
    *,
    validated: list[ValidatedRow],
    course_id: uuid.UUID,
    class_id: uuid.UUID,
) -> list[ValidatedRow]:
    """Turn rows that would violate the one-active-section rule into rejected rows.

    This happens at confirmation time, not at parse time, because the answer depends on what
    is already in `b4.enrollments`. A row that is already active in **the same class** is a
    normal re-import (an updated list for that class) and stays accepted; a row active in
    another section/class of the same course is rejected with a reason the preview shows,
    and an existing account whose name disagrees is rejected rather than merged.
    """
    accepted = [item for item in validated if item.accepted]
    if not accepted:
        return validated
    numbers = [item.values[FIELD_STUDENT_NUMBER] for item in accepted]
    conflicts = {
        item.student_number: item
        for item in active_enrollment_conflicts(
            connection, course_id=course_id, student_numbers=numbers
        )
    }
    mismatched = set(
        account_name_mismatches(
            connection,
            rows=[
                (item.values[FIELD_STUDENT_NUMBER], item.values[FIELD_DISPLAY_NAME])
                for item in accepted
            ],
        )
    )
    result: list[ValidatedRow] = []
    for item in validated:
        if not item.accepted:
            result.append(item)
            continue
        number = item.values[FIELD_STUDENT_NUMBER]
        existing = conflicts.get(number)
        if existing is not None and existing.class_id != str(class_id):
            result.append(
                ValidatedRow(
                    item.row_number,
                    ROW_STATUS_REJECTED,
                    item.values,
                    "conflict_other_section: 该学生已在本课程的其他课序/班级有效在册"
                    "(同一课程只能属于一个有效课序; 请先从原班级移出)",
                )
            )
            continue
        if number in mismatched:
            result.append(
                ValidatedRow(
                    item.row_number,
                    ROW_STATUS_REJECTED,
                    item.values,
                    "account_name_mismatch: 已有账号的姓名与名单不一致, 未绑定(请先核对)",
                )
            )
            continue
        result.append(item)
    return result


def _upsert_enrollments(
    connection: Connection,
    *,
    roster_import_id: uuid.UUID,
    course_id: uuid.UUID,
    section_id: uuid.UUID,
    class_id: uuid.UUID,
    accepted: list,
) -> int:
    """Insert the accepted rows into the class they were imported for.

    The conflict target is the **course-level** partial unique index, not the old
    `(section_id, student_number)` one: that is the rule ("one active section per course")
    and it is what makes a concurrent confirmation fail loudly instead of creating a second
    active section. Re-importing the same class updates the existing row in place.
    """
    numbers = [item.values[FIELD_STUDENT_NUMBER] for item in accepted]
    linked = bound_accounts(connection, student_numbers=numbers)
    now = _now()
    written = 0
    for item in accepted:
        number = item.values[FIELD_STUDENT_NUMBER]
        statement = (
            pg_insert(enrollments)
            .values(
                enrollment_id=uuid.uuid4(),
                course_id=course_id,
                section_id=section_id,
                class_id=class_id,
                user_id=linked.get(number),
                student_number=number,
                display_name=item.values[FIELD_DISPLAY_NAME],
                source="roster_import",
                roster_import_id=roster_import_id,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["course_id", "student_number"],
                # Must match the migration's predicate text (`WHERE is_active`); writing
                # `is_active IS true` here is not the same predicate to PostgreSQL and makes
                # it reject the inference.
                index_where=enrollments.c.is_active,
                set_={
                    "display_name": item.values[FIELD_DISPLAY_NAME],
                    "user_id": linked.get(number),
                    "roster_import_id": roster_import_id,
                    "is_active": True,
                    "updated_at": now,
                },
                # A concurrent transaction may have inserted this student into a *different*
                # class of the same course between our pre-check and this statement. Moving
                # that row silently would violate "one active section per course" and hide a
                # conflict from the teacher, so only the same class may be updated; anything
                # else returns no row and is reported as a conflict below.
                where=enrollments.c.class_id == class_id,
            )
            # `RETURNING` rather than `rowcount`: psycopg3 reports -1 for inserts through
            # SQLAlchemy, so counting affected rows would report a negative write count.
            .returning(enrollments.c.enrollment_id)
        )
        if connection.execute(statement).fetchone() is not None:
            written += 1
        else:
            raise conflict(
                "同一课程中该学生已于并发导入中被写入另一个课序/班级; "
                "本次导入已回滚, 不会静默改写既有归属."
            )
    return written


@dataclass(frozen=True)
class _Counts:
    accepted: int
    rejected: int


def _stage_rows(
    connection: Connection,
    *,
    roster_import_id: uuid.UUID,
    table: RosterTable,
    mapping: dict[str, str],
    course_code: str,
    section_code: str,
) -> _Counts:
    validated = validate_rows(table, mapping, course_code=course_code, section_code=section_code)
    _replace_rows(connection, roster_import_id, validated)
    return _Counts(
        accepted=sum(1 for item in validated if item.accepted),
        rejected=sum(1 for item in validated if not item.accepted),
    )


def _replace_rows(connection: Connection, roster_import_id: uuid.UUID, validated: list) -> None:
    connection.execute(
        roster_import_rows.delete().where(roster_import_rows.c.roster_import_id == roster_import_id)
    )
    if not validated:
        return
    connection.execute(
        roster_import_rows.insert(),
        [
            {
                "row_id": uuid.uuid4(),
                "roster_import_id": roster_import_id,
                "row_number": item.row_number,
                "status": item.status,
                "row_values": item.values,
                "reason": item.reason,
            }
            for item in validated
        ],
    )


def _load_content(engine: Engine, roster_import_id: uuid.UUID) -> bytes:
    with engine.connect() as connection:
        return _load_content_from(connection, roster_import_id)


def _load_content_from(connection: Connection, roster_import_id: uuid.UUID) -> bytes:
    row = connection.execute(
        select(roster_import_files.c.content).where(
            roster_import_files.c.roster_import_id == roster_import_id
        )
    ).fetchone()
    if row is None:
        raise not_found("The stored roster artifact is missing.")
    return bytes(row[0])


def _load_table(engine: Engine, roster_import_id: uuid.UUID) -> RosterTable:
    with engine.connect() as connection:
        row = _load_row(connection, roster_import_id)
        content = _load_content_from(connection, roster_import_id)
        if hashlib.sha256(content).hexdigest() != row.content_sha256:
            raise conflict("The stored roster artifact does not match its recorded digest.")
    return parse_roster(content, row.media_type)


def _parse_detail(table: RosterTable) -> str | None:
    parts = [table.detail, *table.notes]
    text = "; ".join(part for part in parts if part)
    return text[:500] or None


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _correlation_uuid(value: str | None) -> uuid.UUID:
    """The envelope constrains `correlation_id` to a UUID; synthesise one if absent."""
    if value:
        try:
            return uuid.UUID(str(value))
        except ValueError:
            pass
    return uuid.uuid4()


__all__ = [
    "STATUS_COMMITTED",
    "STATUS_FAILED_PARSE",
    "STATUS_MAPPING_REQUIRED",
    "STATUS_PREVIEWED",
    "STATUS_REJECTED",
    "STATUS_UPLOADED",
    "STATUS_VALIDATING",
    "ConfirmResult",
    "RosterImportView",
    "RowPreview",
    "confirm_import",
    "create_import",
    "load_view",
    "set_mapping",
]
