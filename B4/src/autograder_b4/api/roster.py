"""Roster import API.

The upload is bounded before it is read, the media type is decided from magic bytes, and
every step is authorized against the target course. The response shape is the
`RosterImportPreview` declared by the contract, including `rejected_rows` with a reason
for each -- a rejected roster line is reported, never dropped.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, select

from ..authorization import resolve_principal
from ..db.tables import courses, sections
from ..errors import not_found, payload_too_large, unprocessable
from ..logging import get_logger
from ..roster import parsers
from ..roster.service import (
    ConfirmResult,
    RosterImportView,
    confirm_import,
    create_import,
    load_view,
    set_mapping,
)
from .dependencies import (
    B4State,
    Dependencies,
    actor_of,
    ensure_course_read,
    ensure_course_write,
)
from .problem import ProblemRoute

logger = get_logger("roster.api")

CHUNK_BYTES = 64 * 1024


class ColumnMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: dict[str, str]


def _row_document(row) -> dict[str, object]:
    return {"row_number": row.row_number, "values": row.values, "reason": row.reason}


def _view_document(view: RosterImportView) -> dict[str, object]:
    return {
        "roster_import_id": str(view.roster_import_id),
        "status": view.status,
        "course_id": str(view.course_id),
        "section_id": str(view.section_id),
        "detected_columns": view.detected_columns,
        "proposed_mapping": view.proposed_mapping,
        "accepted_rows": [_row_document(row) for row in view.accepted_rows],
        "rejected_rows": [_row_document(row) for row in view.rejected_rows],
        "created_at": view.created_at.astimezone()
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }


async def _read_bounded(upload: UploadFile, maximum: int) -> bytes:
    """Read at most `maximum` bytes; a larger upload is refused rather than truncated."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise payload_too_large(f"The roster exceeds the {maximum} byte upload limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def build_roster_router(state: B4State, dependencies: Dependencies) -> APIRouter:
    router = APIRouter(route_class=ProblemRoute)
    engine = state.engine

    def _resolve_target(connection: Connection, course_id: uuid.UUID, section_id: uuid.UUID):
        """Load the target section and course, or report the mismatch explicitly."""
        row = connection.execute(
            select(sections.c.section_id, sections.c.section_code, sections.c.course_id).where(
                sections.c.section_id == section_id
            )
        ).fetchone()
        if row is None or row.course_id != course_id:
            raise not_found("The section does not exist in the given course.")
        course = connection.execute(
            select(courses.c.course_code).where(courses.c.course_id == course_id)
        ).fetchone()
        if course is None:  # pragma: no cover - guaranteed by the foreign key
            raise not_found("The course does not exist.")
        return row, course.course_code

    @router.post("/api/v1/roster-imports", status_code=202)
    async def create_roster_import(
        request: Request,
        file: Annotated[UploadFile, File()],
        course_id: Annotated[uuid.UUID, Form()],
        section_id: Annotated[uuid.UUID, Form()],
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        content = await _read_bounded(file, state.settings.roster_upload_max_bytes)
        if not content:
            # An empty file is a schema-valid request that fails a business rule, which is
            # what the contract's `422` means; `400` is for a body that cannot be parsed.
            raise unprocessable("The uploaded roster is empty.")
        filename = (file.filename or "").strip()
        if not filename:
            raise unprocessable("The uploaded roster has no filename.")
        with engine.begin() as connection:
            section, course_code = _resolve_target(connection, course_id, section_id)
            caller = resolve_principal(connection, caller.user_id)
            ensure_course_write(principal=caller, course_id=course_id, dependencies=dependencies)
        view = create_import(
            engine,
            filename=filename,
            content=content,
            course_id=course_id,
            section_id=section_id,
            course_code=course_code,
            section_code=section.section_code,
            actor=actor_of(caller),
            request_id=getattr(request.state, "request_id", None),
            correlation_id=getattr(request.state, "correlation_id", None),
        )
        logger.info(
            "roster.import.parsed",
            extra={
                "event": "roster.import.parsed",
                "roster_import_id": str(view.roster_import_id),
                "status": view.status,
                "detected_columns": len(view.detected_columns),
            },
        )
        return _view_document(view)

    @router.get("/api/v1/roster-imports/{roster_import_id}")
    def get_roster_import(
        roster_import_id: uuid.UUID,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        view = load_view(engine, roster_import_id)
        ensure_course_read(principal=caller, course_id=view.course_id, dependencies=dependencies)
        return _view_document(view)

    @router.put("/api/v1/roster-imports/{roster_import_id}/mapping")
    def set_roster_column_mapping(
        roster_import_id: uuid.UUID,
        payload: ColumnMappingRequest,
        request: Request,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        view = load_view(engine, roster_import_id)
        ensure_course_write(principal=caller, course_id=view.course_id, dependencies=dependencies)
        with engine.connect() as connection:
            _, course_code = _resolve_target(connection, view.course_id, view.section_id)
            section_code = connection.execute(
                select(sections.c.section_code).where(sections.c.section_id == view.section_id)
            ).scalar_one()
        updated = set_mapping(
            engine,
            roster_import_id,
            payload.columns,
            actor=actor_of(caller),
            request_id=getattr(request.state, "request_id", None),
            correlation_id=getattr(request.state, "correlation_id", None),
            course_code=course_code,
            section_code=section_code,
        )
        return _view_document(updated)

    @router.post("/api/v1/roster-imports/{roster_import_id}/confirm", status_code=202)
    def confirm_roster_import(
        roster_import_id: uuid.UUID,
        request: Request,
        caller=Depends(dependencies.principal),
    ) -> dict[str, object]:
        view = load_view(engine, roster_import_id)
        ensure_course_write(principal=caller, course_id=view.course_id, dependencies=dependencies)
        with engine.connect() as connection:
            _, course_code = _resolve_target(connection, view.course_id, view.section_id)
            section_code = connection.execute(
                select(sections.c.section_code).where(sections.c.section_id == view.section_id)
            ).scalar_one()
        result: ConfirmResult = confirm_import(
            engine,
            roster_import_id,
            actor=actor_of(caller),
            idempotency_key=request.headers.get("Idempotency-Key"),
            request_id=getattr(request.state, "request_id", None),
            correlation_id=getattr(request.state, "correlation_id", None),
            course_code=course_code,
            section_code=section_code,
        )
        return _view_document(result.view)

    return router


__all__ = ["build_roster_router", "parsers"]
