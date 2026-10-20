"""Audit records for authentication, authorization and management operations.

Scope comes from `docs/architecture/boundaries.md` §4: the actor, the target, a digest of
the before/after values, the time and the request identifier. The *values* are not
stored -- only a digest and the names of the fields that changed -- so an audit trail
never becomes a second, unredacted copy of the data it describes.

Denials are audited too. A `403` with no audit record would make "who tried?"
unanswerable, which is the question the record exists to answer.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection

from .db.tables import audit_records
from .logging import current_correlation_id, current_request_id, get_logger

logger = get_logger("audit")

OUTCOME_SUCCESS = "success"
OUTCOME_DENIED = "denied"
OUTCOME_FAILURE = "failure"
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_FAILURE)

#: Action names are a flat, greppable vocabulary. They are not an error-code system:
#: errors come from `errors.py` (HTTP status semantics), and these describe what a
#: principal did.
ACTION_SESSION_CREATED = "auth.session.created"
ACTION_SESSION_REFRESHED = "auth.session.refreshed"
ACTION_SESSION_DELETED = "auth.session.deleted"
ACTION_LOGIN_DENIED = "auth.login.denied"
ACTION_LOGIN_THROTTLED = "auth.login.throttled"
ACTION_SESSION_REUSE_DETECTED = "auth.session.reuse_detected"
ACTION_REGISTRATION_CODE_SENT = "auth.registration.code_sent"
ACTION_REGISTRATION_CREATED = "auth.registration.created"
ACTION_REGISTRATION_REJECTED = "auth.registration.rejected"
ACTION_ALLOWLIST_REPLACED = "registration.allowlist.replaced"
ACTION_AUTHORIZATION_DENIED = "authz.denied"
ACTION_USER_CREATED = "user.created"
ACTION_USER_UPDATED = "user.updated"
ACTION_USER_DEACTIVATED = "user.deactivated"
ACTION_COURSE_CREATED = "course.created"
ACTION_SECTION_CREATED = "section.created"
ACTION_ENROLLMENT_UPDATED = "enrollment.updated"
ACTION_ENROLLMENT_DELETED = "enrollment.deleted"
ACTION_AUDIT_QUERIED = "audit.queried"
ACTION_ROSTER_IMPORT_CREATED = "roster.import.created"
ACTION_ROSTER_IMPORT_MAPPING_SET = "roster.import.mapping_set"
ACTION_ROSTER_IMPORT_COMMITTED = "roster.import.committed"
ACTION_ROSTER_IMPORT_FAILED = "roster.import.failed"
ACTION_REGISTRY_READ = "registry.read"


@dataclass(frozen=True)
class AuditActor:
    """Who performed the operation. Roles are a snapshot, so later changes stay traceable."""

    user_id: uuid.UUID | None
    roles: tuple[str, ...] = ()

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


SYSTEM_ACTOR = AuditActor(user_id=None, roles=())


def change_digest(before: Mapping[str, object], after: Mapping[str, object]) -> str:
    """SHA-256 over the changed fields, hashed per side.

    The digest proves *that* a change happened and is reproducible for verification,
    while the plaintext values -- which for a roster are student identity fields --
    never enter the audit table.
    """
    changed = {}
    for field in sorted(set(before) | set(after)):
        old = before.get(field)
        new = after.get(field)
        if old == new:
            continue
        changed[field] = {
            "before": _hash_value(old),
            "after": _hash_value(new),
        }
    payload = json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def changed_fields(before: Mapping[str, object], after: Mapping[str, object]) -> list[str]:
    """Field names that differ; safe to store because names are not data."""
    return sorted(
        field for field in set(before) | set(after) if before.get(field) != after.get(field)
    )


def _hash_value(value: object) -> str | None:
    if value is None:
        return None
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class AuditLog:
    """Writes audit rows on a caller-supplied connection.

    Taking the connection rather than creating one lets a management operation commit its
    business change and its audit record in the same transaction: an operation that
    commits without a trail, or a trail without the operation, is not expressible.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def record(
        self,
        *,
        action: str,
        outcome: str,
        actor: AuditActor = SYSTEM_ACTOR,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        course_id: uuid.UUID | None = None,
        section_id: uuid.UUID | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        digest: str | None = None,
        detail: str | None = None,
        occurred_at: datetime | None = None,
    ) -> uuid.UUID:
        if outcome not in OUTCOMES:  # pragma: no cover - guarded by callers and the schema
            raise ValueError(f"unsupported audit outcome: {outcome!r}")
        # The correlation identifiers default to the in-flight request rather than to None:
        # a record that cannot be joined to the request that produced it answers "what
        # happened" but not "show me everything that happened together".
        if request_id is None:
            request_id = current_request_id()
        if correlation_id is None:
            correlation_id = current_correlation_id()
        audit_id = uuid.uuid4()
        self._connection.execute(
            audit_records.insert().values(
                audit_id=audit_id,
                occurred_at=(occurred_at or datetime.now(UTC)).replace(microsecond=0),
                actor_user_id=actor.user_id,
                actor_roles=list(actor.roles),
                action=action,
                outcome=outcome,
                target_type=target_type,
                target_id=target_id,
                course_id=course_id,
                section_id=section_id,
                request_id=request_id,
                correlation_id=correlation_id,
                change_digest=digest,
                detail=detail,
            )
        )
        # The log line carries identifiers only; the audit row is the record of substance.
        logger.info(
            "audit.record",
            extra={
                "event": "audit.record",
                "action": action,
                "outcome": outcome,
                "target_type": target_type,
                "target_id": str(target_id) if target_id else None,
                "course_id": str(course_id) if course_id else None,
                "audit_id": str(audit_id),
            },
        )
        return audit_id


def field_digest(*names: str) -> str:
    """Digest over a set of field names, for operations that have no before/after pair."""
    joined: Iterable[str] = names
    payload = json.dumps(sorted(joined), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
