"""Uploads for stem graphics and fixtures.

Both uploads are refused once the question's current version is published: adding
a graphic or a fixture changes the content a past evaluation was bound to, so the
only legal path is a new version (ADR-0001).

The digest B3 stores is always the one B3 computed from the bytes. A client can
declare a digest in the manifest, but that value is only ever *compared*
(``compile.manifest.assert_fixture_binding``), never trusted.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ..auth import Principal, require_course_write
from ..config import Settings
from ..compile.validation import validate_workspace_path
from ..dto.internal import AssetUploadInputModel, FixtureInternal, FixtureUploadInputModel
from ..errors import ImmutableVersionConflict, NotFound, PayloadTooLarge
from ..infra.db import Database
from ..infra.models import QuestionAsset
from ..infra.repositories import ProtectedContentRepository, QuestionBankRepository
from ..infra.storage import (
    decode_base64,
    validated_asset_bytes,
    validated_fixture_bytes,
)
from ..logging import get_logger, log_event


def upload_stem_asset(
    database: Database,
    settings: Settings,
    *,
    principal: Principal | None,
    question_id: uuid.UUID,
    payload: AssetUploadInputModel,
    now: datetime,
) -> QuestionAsset:
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_write(principal, question.course_id)

        latest = bank.latest_version(question_id)
        if latest is None:  # pragma: no cover
            raise NotFound("question has no version")
        if latest.status != "draft":
            raise ImmutableVersionConflict(
                "this question's latest version is "
                f"{latest.status}; adding a stem graphic would change frozen content. "
                "Create a new version instead."
            )
        if bank.count_assets(question_id) >= settings.max_assets_per_question:
            raise PayloadTooLarge(
                f"a question may carry at most {settings.max_assets_per_question} assets"
            )

        content = decode_base64(payload.content_base64)
        content_type, content_sha256 = validated_asset_bytes(
            content_type=payload.content_type, content=content, settings=settings
        )
        existing = bank.existing_asset(question_id, content_sha256)
        if existing is not None:
            return _detached(session, existing)

        asset = bank.create_asset(
            question_id=question_id,
            content_type=content_type,
            content=content,
            content_sha256=content_sha256,
            alt_text=payload.alt_text,
            now=now,
        )
        log_event(
            get_logger("domain.assets"),
            "question.asset.uploaded",
            question_id=str(question_id),
            asset_id=str(asset.asset_id),
            content_digest=content_sha256,
            status_code=201,
        )
        return _detached(session, asset)


def upload_fixture(
    database: Database,
    settings: Settings,
    *,
    principal: Principal | None,
    question_version_id: uuid.UUID,
    payload: FixtureUploadInputModel,
    now: datetime,
) -> FixtureInternal:
    path = validate_workspace_path(payload.path, field="path")
    content = decode_base64(payload.content_base64)
    content_sha256, _size = validated_fixture_bytes(content=content, settings=settings)

    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")
        question = bank.get_question(version.question_id)
        if question is None:  # pragma: no cover
            raise NotFound("question not found")
        require_course_write(principal, question.course_id)
        if version.status != "draft":
            raise ImmutableVersionConflict(
                "fixtures can only be uploaded to a draft version; "
                f"this version is {version.status}. Create a new version instead."
            )

        protected = ProtectedContentRepository(session)
        row = protected.upsert_fixture(
            question_version_id=question_version_id,
            path=path,
            mount=payload.mount,
            content=content,
            content_sha256=content_sha256,
            now=now,
        )
        log_event(
            get_logger("domain.assets"),
            "question.fixture.uploaded",
            question_version_id=str(question_version_id),
            status_code=201,
        )
        return FixtureInternal(
            fixture_id=row.fixture_id,
            question_version_id=row.question_version_id,
            path=row.path,
            mount=row.mount,  # type: ignore[arg-type]
            content_sha256=row.content_sha256,
            size_bytes=row.size_bytes,
        )


def delete_fixture(
    database: Database,
    *,
    principal: Principal | None,
    question_version_id: uuid.UUID,
    path: str,
    mount: str,
) -> None:
    validated_path = validate_workspace_path(path, field="path")
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")
        question = bank.get_question(version.question_id)
        if question is None:  # pragma: no cover
            raise NotFound("question not found")
        require_course_write(principal, question.course_id)
        if version.status != "draft":
            raise ImmutableVersionConflict(
                "fixtures of a non-draft version are immutable"
            )
        ProtectedContentRepository(session).delete_fixture(question_version_id, validated_path, mount)
        log_event(
            get_logger("domain.assets"),
            "question.fixture.deleted",
            question_version_id=str(question_version_id),
        )


def _detached(session, row: QuestionAsset) -> QuestionAsset:
    session.flush()
    session.expunge(row)
    return row
