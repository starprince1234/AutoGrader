"""FastAPI router for `/api/v1/ops/logs*` and `/api/v1/logs/client`."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from .auth import (
    LogPrincipal,
    OpsLogAuthError,
    require_authenticated_principal,
    require_ops_log_principal,
)
from .context import normalize_uuid
from .provision import resolve_logs_dsn
from .schema import (
    DEFAULT_RETENTION_INFO_DAYS,
    DEFAULT_RETENTION_WARN_ERROR_DAYS,
    DEFAULT_STORAGE_LIMIT_BYTES,
)
from .service import (
    LogFilter,
    export_logs_stream,
    get_summary,
    get_trace,
    ingest_client_logs,
    list_logs,
    resolve_time_bounds,
)
from .insights import generate_report


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["jsonl", "csv"]
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    producer: Literal["B1", "B2", "B3", "B4", "B5", "MAIN"] | None = None
    level: Literal["info", "warn", "error"] | None = None
    event: str | None = Field(default=None, max_length=100)
    request_id: str | None = None
    correlation_id: str | None = None
    submission_id: str | None = None
    course_id: str | None = None
    io_type: str | None = Field(default=None, max_length=32)
    q: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=2000, ge=1, le=5000)


class ClientLogItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str
    level: Literal["debug", "info", "warn", "error"]
    event: str = Field(min_length=1, max_length=100)
    request_id: str | None = None
    correlation_id: str | None = None
    course_id: str | None = None
    assignment_id: str | None = None
    submission_id: str | None = None
    stage: str | None = Field(default=None, max_length=64)


class ClientLogBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[ClientLogItem] = Field(min_length=1, max_length=50)


def _validate_uuid_param(name: str, raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    normalized = normalize_uuid(raw)
    if normalized is None:
        raise OpsLogAuthError(422, "Unprocessable Entity", f"{name} must be a valid UUID.")
    return normalized


def build_ops_logs_router(
    *,
    reader_engine: Engine | None = None,
) -> APIRouter:
    router = APIRouter(tags=["ops-logs"])

    def _get_reader_engine() -> Engine:
        nonlocal reader_engine
        if reader_engine is not None:
            return reader_engine
        dsn = resolve_logs_dsn("LOGS_READER_DATABASE_URL") or os.environ.get("PLATFORM_DATABASE_URL", "").strip()
        if not dsn:
            raise OpsLogAuthError(503, "Service Unavailable", "Log reader database is not configured.")
        reader_engine = create_engine(dsn, pool_pre_ping=True, pool_size=2, max_overflow=2, future=True)
        return reader_engine

    @router.get("/api/v1/ops/logs")
    def get_ops_logs(
        principal: LogPrincipal = Depends(require_ops_log_principal),
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = Query(default=None),
        producer: Literal["B1", "B2", "B3", "B4", "B5", "MAIN"] | None = Query(default=None),
        level: Literal["info", "warn", "error"] | None = Query(default=None),
        event: str | None = Query(default=None, max_length=100),
        request_id: str | None = Query(default=None),
        correlation_id: str | None = Query(default=None),
        submission_id: str | None = Query(default=None),
        course_id: str | None = Query(default=None),
        io_type: str | None = Query(default=None, max_length=32),
        q: str | None = Query(default=None, max_length=100),
        cursor: str | None = Query(default=None, max_length=256),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        start, end = resolve_time_bounds(from_, to)
        flt = LogFilter(
            from_dt=start,
            to_dt=end,
            producer=producer,
            level=level,
            event=event,
            request_id=_validate_uuid_param("request_id", request_id),
            correlation_id=_validate_uuid_param("correlation_id", correlation_id),
            submission_id=_validate_uuid_param("submission_id", submission_id),
            course_id=_validate_uuid_param("course_id", course_id),
            io_type=io_type,
            q=q,
        )
        return list_logs(_get_reader_engine(), principal, flt, cursor=cursor, limit=limit)

    @router.get("/api/v1/ops/logs/trace/{trace_id}")
    def get_ops_log_trace(
        trace_id: str,
        principal: LogPrincipal = Depends(require_ops_log_principal),
    ) -> dict[str, Any]:
        return get_trace(_get_reader_engine(), principal, trace_id)

    @router.get("/api/v1/ops/logs/summary")
    def get_ops_log_summary(
        request: Request,
        principal: LogPrincipal = Depends(require_ops_log_principal),
    ) -> dict[str, Any]:
        readiness: dict[str, bool] = {"B1": True, "MAIN": True}
        modules = getattr(request.app.state, "modules", None) or []
        contexts = getattr(request.app.state, "contexts", None) or {}
        for mod in modules:
            name = mod.descriptor.module
            try:
                rep = mod.health(contexts[name])
                readiness[name] = bool(rep.healthy)
            except Exception:
                readiness[name] = False
        info_days = int(os.environ.get("LOGS_RETENTION_INFO_DAYS", str(DEFAULT_RETENTION_INFO_DAYS)))
        warn_days = int(os.environ.get("LOGS_RETENTION_WARN_ERROR_DAYS", str(DEFAULT_RETENTION_WARN_ERROR_DAYS)))
        max_bytes = int(os.environ.get("LOGS_STORAGE_LIMIT_BYTES", str(DEFAULT_STORAGE_LIMIT_BYTES)))
        return get_summary(
            _get_reader_engine(),
            principal,
            module_readiness=readiness,
            retention_info_days=info_days,
            retention_warn_error_days=warn_days,
            storage_limit_bytes=max_bytes,
        )

    @router.post("/api/v1/ops/insights")
    def post_ops_insights(
        request: Request,
        principal: LogPrincipal = Depends(require_ops_log_principal),
    ) -> dict[str, Any]:
        """One button in the log center: facts in, a layered plain-language report out.

        POST because it spends money on a model call, and idempotent in effect: it reads the
        operational picture and never changes it. The caller's row scope travels into the facts,
        so a teacher's report is about their own courses and an admin's about the platform.
        """
        modules = getattr(request.app.state, "modules", None) or []
        contexts = getattr(request.app.state, "contexts", None) or {}
        return generate_report(
            reader_engine=_get_reader_engine(),
            principal=principal,
            modules=modules,
            contexts=contexts,
        )

    @router.post("/api/v1/ops/logs/exports")
    def post_ops_log_export(
        body: ExportRequest,
        principal: LogPrincipal = Depends(require_ops_log_principal),
    ) -> StreamingResponse:
        start, end = resolve_time_bounds(body.from_, body.to)
        flt = LogFilter(
            from_dt=start,
            to_dt=end,
            producer=body.producer,
            level=body.level,
            event=body.event,
            request_id=_validate_uuid_param("request_id", body.request_id),
            correlation_id=_validate_uuid_param("correlation_id", body.correlation_id),
            submission_id=_validate_uuid_param("submission_id", body.submission_id),
            course_id=_validate_uuid_param("course_id", body.course_id),
            io_type=body.io_type,
            q=body.q,
        )
        stream, media_type, filename = export_logs_stream(
            _get_reader_engine(),
            principal,
            flt,
            fmt=body.format,
            limit=body.limit,
        )
        return StreamingResponse(
            stream,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/api/v1/logs/client", status_code=202)
    def post_client_logs(
        body: ClientLogBatchRequest,
        principal: LogPrincipal = Depends(require_authenticated_principal),
    ) -> JSONResponse:
        result = ingest_client_logs(
            principal,
            [item.model_dump() for item in body.lines],
        )
        return JSONResponse(status_code=202, content=result)

    return router


def install_ops_log_exception_handler(app) -> None:
    @app.exception_handler(OpsLogAuthError)
    async def _handle_ops_log_error(request: Request, exc: OpsLogAuthError) -> JSONResponse:
        return exc.to_response(str(request.url.path))
