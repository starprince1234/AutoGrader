"""Internal endpoint support: the evaluation package and the reference answer.

Both are read only by B2, on the service token, and both are protected content:

* the evaluation package is *compiled* from the version content. For a **published** version
  its digest is compared with the ``package_digest`` recorded at publish time, and that check
  is what turns "protected content changed after publish" from a silent inconsistency into a
  hard ``409``. Since ``CHG-0028`` a **draft** is served too -- compiled from its current
  content and without that comparison, because a draft has no recorded digest: the publish
  gate needs an attestation first and an attestation is accepted only for a draft, so the
  executable inputs must be reachable before publication;
* the reference answer is delivered on its own endpoint (``CHG-0023``) because ``CHG-0019``
  needs B2 to *execute* it, and the package is the wrong channel for that: the package is
  fetched for every evaluation, while the answer is needed only to verify one.

Only B2 may call either; the authorization check lives in the API layer
(:func:`autograder_b3.auth.require_service`) and this module assumes it happened.
"""

from __future__ import annotations

import uuid
from typing import get_args

from ..compile.compiler import compile_evaluation_package
from ..dto.internal import AdapterKind, EvaluationPackage, ReferenceAnswerDelivery
from ..errors import (
    B3Error,
    Conflict,
    NotFound,
    PackageContentMissing,
    ReferenceAnswerMissing,
)
from ..infra.db import Database
from ..infra.repositories import ProtectedContentRepository, QuestionBankRepository
from ..logging import get_logger, log_event
from .versions import load_draft_snapshot, load_published_snapshot

#: The contract's adapter vocabulary, taken from the ``Literal`` rather than retyped so a
#: second copy of the enum cannot drift (``CHG-0023`` §3.2 reuses this vocabulary).
ADAPTER_KINDS: tuple[str, ...] = get_args(AdapterKind)


def load_evaluation_package(
    database: Database, *, question_version_id: uuid.UUID
) -> EvaluationPackage:
    """Compile a version's executable inputs, for a draft or a published version.

    ``CHG-0028``: the two states differ in exactly one respect. A **published** version is an
    immutable artifact, so the compiled digest is compared against the digest recorded at
    publish time and a mismatch is refused. A **draft** is a snapshot of the version's current
    content and has no recorded digest to compare against, so the ``package_digest`` in the
    response is simply the digest of what was just compiled: honest, and not yet bound to
    anything until that version is published.
    """
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")

        protected = ProtectedContentRepository(session)
        cases = protected.list_cases(question_version_id)
        fixtures = protected.list_fixtures(question_version_id)
        if not cases:
            # `409` about *content*, not about publication state: a version with nothing to
            # execute cannot be compiled whether it is a draft or already published.
            raise PackageContentMissing(
                "this version has no compilable content: it has no test cases yet, so there "
                "is nothing to execute"
            )

        if version.status == "draft":
            snapshot, _content_digest = load_draft_snapshot(session, version)
            package = _compile(snapshot, cases, fixtures)
            log_event(
                get_logger("domain.packages"),
                "evaluation.package.served",
                question_version_id=str(question_version_id),
                package_digest=package.package_digest,
                adapter_kind=str(package.adapter.get("kind")),
                outcome="draft",
            )
            return package

        snapshot, content_digest = load_published_snapshot(session, version)
        package = _compile(snapshot, cases, fixtures)
        if version.package_digest is None:
            raise Conflict("this version has no recorded package digest")
        if package.package_digest != version.package_digest:
            log_event(
                get_logger("domain.packages"),
                "evaluation.package.digest_mismatch",
                level=40,
                question_version_id=str(question_version_id),
                package_digest=package.package_digest,
                content_digest=content_digest,
            )
            raise Conflict(
                "the compiled package no longer matches the digest recorded at publish "
                "time; protected content changed after publication"
            )
        log_event(
            get_logger("domain.packages"),
            "evaluation.package.served",
            question_version_id=str(question_version_id),
            package_digest=package.package_digest,
            adapter_kind=str(package.adapter.get("kind")),
        )
        return package


def _compile(snapshot, cases, fixtures) -> EvaluationPackage:
    """One compile call shared by both paths, so they cannot drift apart.

    ``created_at`` comes from the snapshot, which is where each path records *when the content
    being compiled is from*: ``published_at`` for a published version, the content's own
    timestamp for a draft.
    """
    return compile_evaluation_package(
        snapshot,
        snapshot.manifest,
        cases,
        fixtures,
        created_at=snapshot.published_at,
    )


def load_reference_answer(
    database: Database, *, question_version_id: uuid.UUID
) -> ReferenceAnswerDelivery:
    """Read one version's own reference answer for the verification path (``CHG-0023``).

    Works for a **draft** on purpose: B2 verifies a version *before* it is published, which
    is the whole point of ``CHG-0019`` (the attestation it records is what publish requires).
    Nothing here looks at ``version.status``.

    The answer returned is the one stored for the language **the version declares**, not
    merely "the only answer row": a version can carry more than one language's answer (each
    write upserts its own language), and handing B2 the wrong one would make it execute
    something the version does not declare.

    Failures are deliberately split rather than collapsed:

    * ``404`` -- no such version;
    * ``409`` -- the version exists and its state makes verification impossible: no answer
      for its declared language, a blank stored answer, or no ``content_digest`` yet. The
      digest is part of this response because the attestation and the answer must describe the
      same content, so without one there is nothing for a verifier to attest.

      The message for that second state names **the missing digest** and not a cause for it.
      It used to end "a version needs at least one test case first", which reads as the reason
      -- and is false on every real instance the bank has: several seeded drafts carry cases
      *and* an answer with a NULL ``content_digest``, because no write path ever computed one
      for them (``refresh_content_digest`` runs on every API write, so this state means the row
      did not come through that path). Naming the missing piece stays true; naming a cause did
      not.

    The answer's body is never logged and never placed in an event: the log line carries the
    version, the language and the digest, which is what an operator needs to correlate a
    verification without holding the protected content.
    """
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        version = bank.get_version(question_version_id)
        if version is None:
            raise NotFound("question version not found")

        declared = (version.adapter_manifest or {}).get("kind")
        if declared not in ADAPTER_KINDS:
            # The manifest is validated when it is written, so an unknown kind here is a
            # defect in the stored row, not a request problem: fail loudly rather than
            # emit a value outside the contract's enum.
            raise B3Error("the stored adapter kind is not a contract value")

        answers = [
            answer
            for answer in ProtectedContentRepository(session).list_reference_answers(
                question_version_id
            )
            if answer.language == version.language
        ]
        if not answers or not answers[0].body.strip():
            raise ReferenceAnswerMissing(
                "this version has no reference answer for its declared language "
                f"({version.language}); there is nothing to execute"
            )
        if version.content_digest is None:
            raise ReferenceAnswerMissing(
                "this version has no content digest yet, so an answer executed against it "
                "could not be attested: the digest is what binds the answer to the content it "
                "was run against, and this version does not have one"
            )
        return ReferenceAnswerDelivery(
            question_version_id=version.question_version_id,
            content_digest=version.content_digest,
            language=version.language,  # type: ignore[arg-type]
            adapter_kind=declared,  # type: ignore[arg-type]
            body=answers[0].body,
        )
