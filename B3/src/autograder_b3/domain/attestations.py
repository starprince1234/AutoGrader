"""Reference-answer attestations: record the fact, enforce it at publish.

B3 does not execute anything (``AGENTS.md``: 我禁止：执行学生代码 -- and the same
restraint covers reference answers), so it cannot establish that a question's reference
answer produces the expected output. What it can do is refuse to let a question reach a
student unless an external verifier recorded that it does, bound to the exact content
the verifier claims to have run.

The binding is the whole point, and it is the same principle as the fixture binding
(``CHG-B3-0002``) and ``assert_fixture_binding``: a digest supplied by a caller is only
ever **cross-checked** against a digest B3 computes from the content itself. That makes
the attestation unforgeable for different content and makes an edit invalidate it
automatically, because the edit changes the digest.

There is deliberately no override flag, no "skip verification" switch and no legacy
path: the requirement is that an unverified question cannot be published, and a flag
that restores the old behaviour would delete the requirement.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from ..auth import require_service
from ..errors import (
    AttestationMismatch,
    InvalidStateTransition,
    MissingReferenceAttestation,
    NotFound,
    UnprocessableEntity,
)
from ..infra.db import Database
from ..infra.models import ReferenceAttestation
from ..infra.repositories import QuestionBankRepository
from ..logging import get_logger, log_event
from .version_content import load_version_content, refresh_content_digest


@dataclass(frozen=True)
class RecordedAttestation:
    """What the caller and the operator get back. No content, only the claim."""

    attestation_id: uuid.UUID
    question_version_id: uuid.UUID
    content_digest: str
    verifier: str
    cases_total: int
    cases_passed: int
    verified_at: datetime
    recorded_at: datetime


def record_reference_attestation(
    database: Database,
    *,
    principal,
    question_version_id: uuid.UUID,
    content_digest: str,
    verifier: str,
    cases_total: int,
    cases_passed: int,
    verified_at: datetime | None,
    notes: str | None,
    now: datetime,
) -> RecordedAttestation:
    """Record a verifier's claim, or refuse it with the reason it cannot be true."""
    require_service(principal)
    if cases_passed > cases_total:
        raise UnprocessableEntity(
            "`cases_passed` cannot exceed `cases_total` "
            f"({cases_passed} > {cases_total})",
            field="cases_passed",
        )

    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")
        if version.status != "draft":
            raise InvalidStateTransition(
                "only a draft version accepts an attestation "
                f"(current status: {version.status}); its content is already frozen, so "
                "a new attestation could not change what students receive"
            )

        content = load_version_content(session, version)
        computed = content.content_digest
        if content_digest != computed:
            raise AttestationMismatch(
                "the declared content digest does not match this version's content; "
                "re-run the verifier against the current content",
                field="content_digest",
            )
        # The verifier could only have learned this digest by reading it from the API,
        # so make sure the row agrees with the value that was just cross-checked.
        refresh_content_digest(session, version)
        if cases_total != len(content.cases):
            raise AttestationMismatch(
                f"the attestation declares {cases_total} case(s) but the version has "
                f"{len(content.cases)}; the claim does not describe this version",
                field="cases_total",
            )

        # One row per (version, content): re-recording the same verification replaces the
        # note, it does not accumulate duplicates.
        existing = session.get(ReferenceAttestation, (question_version_id, content_digest))
        recorded_at = verified_at or now
        if existing is None:
            row = ReferenceAttestation(
                question_version_id=question_version_id,
                content_digest=content_digest,
                attestation_id=uuid.uuid4(),
                verifier=verifier,
                cases_total=cases_total,
                cases_passed=cases_passed,
                verified_at=recorded_at,
                notes=notes,
                recorded_at=now,
            )
            session.add(row)
        else:
            existing.verifier = verifier
            existing.cases_total = cases_total
            existing.cases_passed = cases_passed
            existing.verified_at = recorded_at
            existing.notes = notes
            existing.recorded_at = now
            row = existing
        session.flush()
        log_event(
            get_logger("domain.attestations"),
            "reference.attestation.recorded",
            question_version_id=str(question_version_id),
            content_digest=content_digest,
            attempt_count=cases_total,
            outcome="accepted" if cases_passed == cases_total else "partial",
        )
        result = RecordedAttestation(
            attestation_id=row.attestation_id,
            question_version_id=row.question_version_id,
            content_digest=row.content_digest,
            verifier=row.verifier,
            cases_total=row.cases_total,
            cases_passed=row.cases_passed,
            verified_at=row.verified_at,
            recorded_at=row.recorded_at,
        )
        session.expunge(row)
        return result


def require_matching_attestation(
    session, *, question_version_id: uuid.UUID, content_digest: str
) -> ReferenceAttestation:
    """The publish gate: an attestation for *this* digest, or a refusal naming the fix.

    Called with the digest B3 recomputed from the stored content, never with one the
    caller supplied, so a stale attestation cannot be presented as current.

    The refusal names the **current** remedy, and that wording is load-bearing. It used to
    send the reader to the bank-verifier script and to the attestation endpoint -- the path
    ``CHG-0019``/``ADR-0015`` replaced. At the one moment a teacher is blocked, that told them
    to go find an operator and a script instead of using the "verify and publish" action that
    already exists in the authoring flow: the mechanism had changed and the pointer to it had
    not. What is preserved is the information that was right before: the digest, and the fact
    that editing content invalidates the attestation.
    """
    row = session.get(ReferenceAttestation, (question_version_id, content_digest))
    if row is None:
        raise MissingReferenceAttestation(
            "publish rejected: no reference-answer attestation matches this content "
            f"(content_digest {content_digest}). This version has not been verified. "
            "Verification runs in the authoring flow: the teacher-facing \"verify and "
            "publish\" action (or the platform's reference-verification endpoint) has the "
            "runner execute the reference answer against this version's cases and records "
            "the result before publishing. Editing content after verification invalidates "
            "the attestation, so verify the content you are about to publish.",
            field="content_digest",
        )
    return row
