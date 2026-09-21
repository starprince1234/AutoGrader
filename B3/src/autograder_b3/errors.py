"""Error taxonomy for B3.

Two families live here:

* :class:`B3Error` subclasses map onto RFC 9457 Problem Details bodies
  (``MAIN/contracts/openapi/common.yaml`` -> ``ProblemDetails``:
  ``type``/``title``/``status``/``detail``/``instance``, nothing else).
* Non-HTTP control-flow signals used by the event consumer
  (:class:`EventPayloadRejected`, :class:`DuplicateEvent`) which never reach a
  response body.

Status semantics are non-negotiable (``MAIN/docs/api/identifiers.md`` §3 and
``docs/architecture/boundaries.md`` §5): ``401`` unauthenticated, ``403``
authenticated but out of scope (never masked as ``404``), ``404`` authorized but
absent, ``409`` state conflict, ``422`` business-rule rejection.

``detail`` strings are written for operators and teachers and therefore must
never contain protected content: reference answers, hidden case expectations,
fixture bodies or student source code.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping

PROBLEM_TYPE_BASE = "https://autograder.local/problems/"


class B3Error(Exception):
    """Base class for every B3 failure that has a defined surface."""

    status: int = 500
    title: str = "Internal Server Error"
    slug: str = "internal"

    def __init__(
        self,
        detail: str,
        *,
        headers: Mapping[str, str] | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.headers: dict[str, str] = dict(headers or {})
        #: Offending input field, for logs and operators. Deliberately **not** part
        #: of the Problem Details body (``additionalProperties: false``).
        self.field = field

    @property
    def problem_type(self) -> str:
        return f"{PROBLEM_TYPE_BASE}{self.slug}"

    def to_problem(self, instance: str) -> dict[str, Any]:
        return {
            "type": self.problem_type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
        }


class Unauthenticated(B3Error):
    status = 401
    title = "Unauthorized"
    slug = "unauthorized"


class Forbidden(B3Error):
    status = 403
    title = "Forbidden"
    slug = "forbidden"


class NotFound(B3Error):
    status = 404
    title = "Not Found"
    slug = "not-found"


class Conflict(B3Error):
    status = 409
    title = "Conflict"
    slug = "conflict"


class UnprocessableEntity(B3Error):
    status = 422
    title = "Unprocessable Entity"
    slug = "unprocessable-entity"


class PayloadTooLarge(B3Error):
    status = 413
    title = "Payload Too Large"
    slug = "payload-too-large"


class ServiceUnavailable(B3Error):
    status = 503
    title = "Service Unavailable"
    slug = "service-unavailable"


# --- Domain specific conflicts (all 409) ---------------------------------


class ImmutableVersionConflict(Conflict):
    """A published/archived version was edited in place (ADR-0001).

    The only legal way to change content is to create a new version.
    """

    slug = "immutable-question-version"


class VersionInUse(Conflict):
    """A published version referenced by historical evaluations cannot be deleted."""

    slug = "question-version-in-use"


class DraftAssignmentReference(Conflict):
    """An assignment may only reference published versions."""

    slug = "draft-assignment-reference"


class AttestationMismatch(Conflict):
    """A declared verification digest does not match the content B3 hashed itself.

    Same principle as the fixture binding (``CHG-B3-0002``): a digest supplied by a
    caller is only ever cross-checked, never trusted, so an attestation can never be
    carried over to content the verifier did not run.
    """

    slug = "attestation-mismatch"


class InvalidStateTransition(Conflict):
    slug = "invalid-state-transition"


class ReferenceAnswerMissing(Conflict):
    """The version exists but cannot be verified (``CHG-0023``).

    ``CHG-0019`` asks B2 to execute a version's reference answer, and this is the delivery
    path that makes that possible. When there is nothing to deliver the answer is ``409``
    rather than ``404``: the version is there, and it is its *state* that makes verification
    impossible. Collapsing the two would tell a client "this version does not exist" when it
    does (``MAIN/docs/api/identifiers.md`` §3), and ``422`` would blame a request that was
    perfectly well formed.

    Two states share this one slug and are told apart by their detail:

    * no reference answer is stored for the language the version declares;
    * the version has no ``content_digest`` yet (it carries no test cases), so there is no
      digest for a verifier to attest -- the digest is part of the response precisely because
      "the answer that was executed" and "the content that was attested" must be the same
      content rather than two values that happen to agree.
    """

    slug = "reference-answer-missing"


class PackageContentMissing(Conflict):
    """There is nothing to compile into an evaluation package (``CHG-0028``).

    ``getEvaluationPackage`` serves drafts as well as published versions: the publish gate
    requires a verification attestation first, and an attestation is accepted only for a
    draft, so the executable inputs must be obtainable **while the version is still a draft**.
    Refusing drafts there is what made the whole publish path impossible.

    ``409`` on that operation therefore means "this version has no **compilable** content" --
    a version with no test cases at all, or one whose declared fixtures have no stored bytes
    yet -- and deliberately does **not** mean "this is a draft". The resource exists; its state
    is what makes the operation impossible, which is the same distinction
    :class:`ReferenceAnswerMissing` draws on the neighbouring endpoint.
    """

    slug = "package-content-missing"


# --- Validation failures (all 422) ----------------------------------------


class ManifestInvalid(UnprocessableEntity):
    """Adapter manifest (or the case/fixture set it describes) is not usable."""

    slug = "adapter-manifest-invalid"


class PathNotAllowed(ManifestInvalid):
    """A declared path escapes the sandbox workspace (absolute path or '..')."""

    slug = "path-not-allowed"


class ProtectedContentMissing(UnprocessableEntity):
    """A published version must carry a reference answer and at least one case."""

    slug = "protected-content-missing"


class MissingReferenceAttestation(UnprocessableEntity):
    """No attestation matches the content about to be published.

    Publication is refused rather than warned about: a question whose reference answer
    was never shown to produce its expected output must not reach a student. The
    message names the fix (record an attestation) instead of only stating the rule.
    """

    slug = "reference-attestation-missing"


class TagInvalid(UnprocessableEntity):
    """A tag is not in the normalised form, or the tag list is unusable.

    Rejected, never silently rewritten: a tag the caller did not ask for would make
    every tag-derived view (filtering, coverage) quietly wrong.
    """

    slug = "tag-invalid"


class StemInvalid(UnprocessableEntity):
    slug = "stem-invalid"


class RevisionNoteMisuse(UnprocessableEntity):
    """``revision_note`` was submitted without ``as_new_version=true``.

    The contract ties the note to the superseding write: it is the *reason a version
    exists*, and a write that creates no version has no such reason to record. Refused
    rather than ignored, for the same reason an invalid tag is refused rather than
    rewritten -- accepting a value and then dropping it makes the audit trail say
    something the author did not (``CHG-0013`` -- 修订说明只在 ``as_new_version=true``
    时有意义).
    """

    slug = "revision-note-misuse"


class AssetRejected(UnprocessableEntity):
    slug = "asset-rejected"


class StatisticsQueryInvalid(UnprocessableEntity):
    slug = "statistics-query-invalid"


class StatisticsWindowTooLarge(UnprocessableEntity):
    """A windowed statistics query would scan more than the bounded maximum.

    The window query recomputes from the event stream, so it is bounded on purpose:
    the page size caps memory and this cap makes "the window is too wide to answer
    honestly" a stated refusal instead of a request that never returns.
    """

    slug = "statistics-window-too-large"


# --- Event pipeline signals (never rendered as HTTP) ----------------------


class EventRejected(Exception):
    """The event must not be applied and must not be acked.

    Raised when the envelope is malformed, the producer is not B2, or the
    payload carries a field outside the de-identified whitelist. The consumer
    leaves the message unacked so JetStream retries and finally routes it to the
    DLQ; it is never silently dropped (ADR-0006 §6).
    """

    def __init__(self, reason: str, *, field: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field
