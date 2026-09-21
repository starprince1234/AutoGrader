"""Publish / archive / delete and the completeness gate (``RQ-B-3-20``).

Publish is the moment a version becomes immutable:

1. completeness is validated - non-empty stem, at least one case, a reference
   answer, a legal adapter declaration whose fixture digests match stored content;
2. ``content_digest`` is computed over the whole content and frozen;
3. a reference-answer attestation whose ``content_digest`` equals that recomputed
   digest must exist - B3 cannot run a reference answer, so it refuses to publish
   content no verifier has vouched for (``domain/attestations.py``);
4. the evaluation package is compiled from exactly that content and its
   ``package_digest`` frozen alongside;
5. an outbox row is written **in the same transaction** as the status change
   (ADR-0006 §5), carrying identifiers only.

Because (4) is deterministic, the internal package endpoint can recompile later
and compare digests: any post-publish change to protected content is detected
instead of being served.

The content loader lives in :mod:`autograder_b3.domain.version_content` so the
attestation path can hash a draft (which has no ``published_at`` yet) through exactly
the same function; two digest implementations would be two truths.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ..auth import Principal, require_course_write
from ..compile.compiler import compile_evaluation_package
from ..dto.internal import PublishedVersionSnapshot
from ..errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    ProtectedContentMissing,
)
from ..events.envelope import build_question_version_published
from ..infra.db import Database
from ..infra.models import QuestionVersion
from ..infra.repositories import QuestionBankRepository
from ..logging import get_logger, log_event
from .attestations import require_matching_attestation
from .version_content import load_version_content

SUBJECT_QUESTION_VERSION_PUBLISHED = "autograder.v1.question.version.published"


def load_published_snapshot(
    session, version: QuestionVersion
) -> tuple[PublishedVersionSnapshot, str]:
    """Read protected content and produce the compile inputs for a **published** version.

    Returns ``(snapshot, content_digest)``. Reads run on the protected role. ``created_at`` is
    the version's ``published_at``: the package describes a frozen artifact, and that is when
    it was frozen.
    """
    if version.published_at is None:
        raise Conflict("this version has never been published")
    snapshot = load_version_content(session, version).snapshot_for(
        created_at=version.published_at
    )
    return snapshot, snapshot.content_digest


def load_draft_snapshot(
    session, version: QuestionVersion
) -> tuple[PublishedVersionSnapshot, str]:
    """Compile inputs for a version that is **not** published (``CHG-0028``).

    The publish gate needs a verification attestation first and an attestation is accepted
    only for a draft, so the executable inputs have to be obtainable while the version is
    still a draft. The package is therefore a snapshot of the draft's *current* content and
    changes whenever the draft changes.

    ``created_at`` is the version content's own timestamp (``updated_at``, which every content
    write bumps) and never ``now()``: the same draft fetched twice has to compile to the same
    package, and a wall-clock timestamp would make it drift on every read even though nothing
    changed. The reference answer is not required here -- a package does not carry it, and its
    own endpoint reports a missing answer in its own terms (see
    :func:`autograder_b3.domain.version_content.load_version_content`).
    """
    snapshot = load_version_content(
        session, version, require_reference_answer=False
    ).snapshot_for(created_at=version.updated_at)
    return snapshot, snapshot.content_digest


def publish_version(
    database: Database,
    *,
    principal: Principal | None,
    question_version_id: uuid.UUID,
    now: datetime,
    correlation_id: uuid.UUID,
) -> tuple[QuestionVersion, str, str]:
    """Publish a draft version; returns ``(version, content_digest, package_digest)``."""
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")
        question = bank.get_question(version.question_id)
        if question is None:  # pragma: no cover - FK guarantees the parent
            raise NotFound("question not found")
        actor = require_course_write(principal, question.course_id)

        if version.status != "draft":
            raise InvalidStateTransition(
                f"only a draft version can be published (current status: {version.status})"
            )

        content = load_version_content(session, version)
        manifest = content.manifest
        cases = content.cases
        fixtures = content.fixtures
        if not version.stem_markdown.strip():
            raise ProtectedContentMissing("publish rejected: the version has an empty stem")

        snapshot = content.snapshot_for(created_at=now)
        content_digest = snapshot.content_digest
        # The gate that makes a question's quality a platform guarantee rather than a
        # script somebody remembered to run. The digest is the one B3 just recomputed,
        # never a value supplied by the caller.
        require_matching_attestation(
            session,
            question_version_id=version.question_version_id,
            content_digest=content_digest,
        )
        package = compile_evaluation_package(
            snapshot, manifest, cases, fixtures, created_at=now
        )

        bank.mark_published(
            version,
            content_digest=content_digest,
            package_digest=package.package_digest,
            published_at=now,
        )
        bank.bind_assets_to_version(version.question_id, version.question_version_id)
        bank.set_question_status(question, "published", now)

        envelope = build_question_version_published(
            question_id=version.question_id,
            question_version_id=version.question_version_id,
            course_id=question.course_id,
            content_digest=content_digest,
            published_at=now,
            occurred_at=now,
            correlation_id=correlation_id,
        )
        bank.enqueue_event(
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            schema_version=envelope.schema_version,
            occurred_at=envelope.occurred_at,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            payload=dict(envelope.payload),
            now=now,
        )
        session.flush()
        log_event(
            get_logger("domain.versions"),
            "question.version.published",
            question_id=str(version.question_id),
            question_version_id=str(version.question_version_id),
            course_id=str(question.course_id),
            content_digest=content_digest,
            package_digest=package.package_digest,
            actor_role=",".join(sorted(actor.roles)),
            adapter_kind=manifest.kind,
        )
        session.expunge(version)
        return version, content_digest, package.package_digest


def archive_version(
    database: Database,
    *,
    principal: Principal | None,
    question_version_id: uuid.UUID,
    now: datetime,
) -> QuestionVersion:
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")
        question = bank.get_question(version.question_id)
        if question is None:  # pragma: no cover
            raise NotFound("question not found")
        require_course_write(principal, question.course_id)
        if version.status != "published":
            raise InvalidStateTransition(
                f"only a published version can be archived (current status: {version.status})"
            )
        bank.mark_archived(version, now)
        remaining = [
            item
            for item in bank.list_versions(version.question_id)
            if item.status == "published" and item.question_version_id != version.question_version_id
        ]
        if not remaining:
            bank.set_question_status(question, "archived", now)
        log_event(
            get_logger("domain.versions"),
            "question.version.archived",
            question_version_id=str(version.question_version_id),
            question_id=str(version.question_id),
        )
        session.flush()
        session.expunge(version)
        return version


def delete_version(
    database: Database,
    *,
    principal: Principal | None,
    question_version_id: uuid.UUID,
) -> None:
    """Delete a draft version, or refuse for anything that has been published.

    The requirement is "a published version referenced by historical evaluations
    cannot be deleted (409), only archived". B3 keeps that guarantee unconditionally
    for published/archived versions: their content is the basis of past evidence,
    and B3 has no way to know whether an evaluation referenced it (evaluations
    belong to B2 and reading ``b2`` is forbidden). Refusing is therefore the only
    safe reading, and the ``question_versions`` trigger enforces it in the database
    as well. There is no HTTP DELETE operation in the frozen contract; this
    function is the domain-level entry point used by tests and by future routes.
    """
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
            raise Conflict(
                "a published version cannot be deleted; archive it instead"
            )
        bank.delete_draft_version(version)
        log_event(
            get_logger("domain.versions"),
            "question.version.draft_deleted",
            question_version_id=str(question_version_id),
        )
