"""Public reads: questions, versions, their stem assets, and tag coverage.

Every function returns ORM rows or plain values; mapping to a public DTO happens
in ``api.serializers``. Nothing here selects from a protected table, so the whole
module runs on the least-privilege role.

Visibility rule for students: only published content is readable. A student asking
for a draft version of a question in their own course gets ``403`` (the caller has
no permission for unpublished content) rather than a ``404`` that would hide the
distinction the requirement asks us to keep (``MAIN/docs/api/identifiers.md`` §3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from ..auth import Principal, require_course_read
from ..errors import Forbidden, NotFound, Unauthenticated
from ..infra.db import Database
from ..infra.models import Question, QuestionAsset, QuestionVersion
from ..infra.repositories import QuestionBankRepository

STAFF_ROLES = frozenset({"teacher", "assistant", "admin"})


def _is_staff(principal: Principal) -> bool:
    return principal.is_service or principal.has_role(*STAFF_ROLES)


def _read_scope(
    principal: Principal | None,
    *,
    course_id: uuid.UUID | None,
    published_only: bool,
) -> tuple[frozenset[uuid.UUID] | None, bool]:
    """Resolve *what a caller may see* once, for every public read that needs it.

    ``list_questions`` and :func:`question_tag_coverage` must answer the same question
    about visibility -- a non-staff caller sees published content only, and a course
    outside the caller's scope is a ``403`` rather than an empty result. Resolving it in
    one place is what makes that a fact instead of two implementations that happen to
    agree today; a coverage count that quietly disagreed with the list would be a
    coverage count of a different bank.

    Returns the course restriction (``None`` means "no restriction", i.e. admin) and the
    effective ``published_only``, which is ``True`` regardless of the request for a
    non-staff caller: forgetting the parameter must show less, never more.
    """
    if principal is None:
        raise Unauthenticated("a bearer token is required")
    if course_id is not None:
        require_course_read(principal, course_id)
    if not _is_staff(principal):
        published_only = True
    scoped = principal.readable_courses()
    if scoped is not None:
        scoped = frozenset(scoped)
        if course_id is not None:
            scoped = frozenset({course_id}) & scoped
    return scoped, published_only


@dataclass(frozen=True)
class TagCoverage:
    """One course's tag distribution, as counts (``CHG-0020``, ``RQ-B-3-20``).

    ``tag_counts`` holds only tags at least one visible question carries, ordered by count
    descending and then by tag ascending. The counts overlap by design -- a question may
    carry up to 20 tags -- so ``tagged_questions + untagged_questions == total_questions``
    is the identity, and the sum over ``tag_counts`` is a different (also useful) number.
    """

    course_id: uuid.UUID
    total_questions: int
    tagged_questions: int
    untagged_questions: int
    tag_counts: tuple[tuple[str, int], ...]


def list_questions(
    database: Database,
    *,
    principal: Principal | None,
    course_id: uuid.UUID | None,
    language: str | None,
    published_only: bool,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
    tags: tuple[str, ...] = (),
) -> tuple[list[Question], bool]:
    scoped, published_only = _read_scope(
        principal, course_id=course_id, published_only=published_only
    )
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        rows, has_more = bank.list_questions(
            course_ids=scoped,
            course_id=None,
            language=language,
            published_only=published_only,
            limit=limit,
            after=after,
            tags=tags,
        )
        for row in rows:
            require_course_read(principal, row.course_id)
            session.expunge(row)
        return rows, has_more


def question_tag_coverage(
    database: Database,
    *,
    principal: Principal | None,
    course_id: uuid.UUID,
    published_only: bool,
) -> TagCoverage:
    """Count one course's visible bank by tag (``RQ-B-3-20``, ``CHG-0020``).

    Visibility is resolved by :func:`_read_scope` -- the same resolution ``list_questions``
    uses -- and ``course_id`` is required by the contract, so a caller always names the
    scope it is counting and an unreadable scope is a ``403`` before any count is taken.

    A bank where nothing is tagged is a *reading*, not a failure: ``tags`` comes back empty
    and every question lands in ``untagged_questions``, so "no topics recorded" stays
    distinguishable from "nothing to count".
    """
    scoped, published_only = _read_scope(
        principal, course_id=course_id, published_only=published_only
    )
    with database.app_session() as session:
        total, tagged, untagged, tag_counts = QuestionBankRepository(session).tag_coverage(
            course_ids=scoped, course_id=course_id, published_only=published_only
        )
    return TagCoverage(
        course_id=course_id,
        total_questions=total,
        tagged_questions=tagged,
        untagged_questions=untagged,
        tag_counts=tuple(tag_counts),
    )


def get_question(
    database: Database, *, principal: Principal | None, question_id: uuid.UUID
) -> Question:
    if principal is None:
        from ..errors import Unauthenticated

        raise Unauthenticated("a bearer token is required")
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_read(principal, question.course_id)
        if not _is_staff(principal) and question.status != "published":
            raise Forbidden("unpublished question content is not available to students")
        session.expunge(question)
        return question


def get_version(
    database: Database, *, principal: Principal | None, question_version_id: uuid.UUID
) -> QuestionVersion:
    if principal is None:
        from ..errors import Unauthenticated

        raise Unauthenticated("a bearer token is required")
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")
        question = bank.get_question(version.question_id)
        if question is None:  # pragma: no cover
            raise NotFound("question not found")
        require_course_read(principal, question.course_id)
        if not _is_staff(principal) and version.status != "published":
            raise Forbidden("unpublished question content is not available to students")
        session.expunge(version)
        return version


def get_question_with_versions(
    database: Database, *, principal: Principal | None, question_id: uuid.UUID
) -> tuple[Question, list[QuestionVersion]]:
    question = get_question(database, principal=principal, question_id=question_id)
    if not _is_staff(principal) and principal is not None:
        return question, []
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        versions = bank.list_versions(question_id)
        for version in versions:
            session.expunge(version)
        return question, versions


def list_stem_assets(
    database: Database, *, principal: Principal | None, question_version_id: uuid.UUID
) -> list[QuestionAsset]:
    version = get_version(
        database, principal=principal, question_version_id=question_version_id
    )
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        assets = bank.list_assets_for_version(version.question_version_id)
        for asset in assets:
            session.expunge(asset)
        return assets


def load_asset_for_delivery(
    database: Database,
    *,
    principal: Principal | None,
    question_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> QuestionAsset:
    """Controlled fetch of a stem graphic (``RQ-B-3-10`` acceptance 5).

    Authorization is checked *before* the bytes are read: the caller must be in the
    question's course scope, and a student may only fetch graphics of published
    questions. An unauthorized caller gets ``403`` - never the image.
    """
    if principal is None:
        from ..errors import Unauthenticated

        raise Unauthenticated("a bearer token is required")
    with database.app_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_read(principal, question.course_id)
        if not _is_staff(principal) and question.status != "published":
            raise Forbidden("stem graphics of unpublished questions are not available")
        asset = bank.get_asset(asset_id)
        if asset is None or asset.question_id != question_id:
            raise NotFound("asset not found")
        session.expunge(asset)
        return asset
