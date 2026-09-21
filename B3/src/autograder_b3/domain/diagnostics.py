"""Comparison-policy diagnostics for a stored question (``CHG-0013``).

``compile/diagnostics.py`` holds the rules and is a pure function over declarations.
This module is the read path: which version is diagnosed, who may ask, and how the
result becomes a public shape.

Two constraints shape it:

* **Coverage** requires the expected outputs of hidden cases, so the read runs on the
  protected role - and it is the *only* reason it does. The findings that leave this
  module are rule ids, severities and text about the declaration; an expected output is
  never quoted, so a teacher can be shown the report next to a version whose content
  they may not otherwise read.
* **Authorization before the read**, and the same role set as authoring assistance
  (``AUTHORING_ROLES``). A student is refused for lack of the role rather than told
  which version would have been inspected.
"""

from __future__ import annotations

import uuid

from ..auth import Principal, require_authoring_reader, require_course_read
from ..compile.diagnostics import DiagnosticsReport, analyse_comparison
from ..compile.manifest import manifest_from_stored
from ..errors import NotFound
from ..infra.db import Database
from ..infra.models import QuestionVersion
from ..infra.repositories import ProtectedContentRepository, QuestionBankRepository
from ..logging import get_logger, log_event


def question_diagnostics(
    database: Database, *, principal: Principal | None, question_id: uuid.UUID
) -> tuple[uuid.UUID, DiagnosticsReport]:
    """Return ``(question_version_id, report)`` for the version a teacher is shown.

    The inspected version is the one the question detail already displays: the current
    published version when there is one, otherwise the latest draft. Diagnosing a
    different version than the caller sees would make the report unactionable - it would
    describe content nobody is looking at - so the two selections are deliberately the
    same rule rather than two rules that happen to agree today.
    """
    actor = require_authoring_reader(principal)
    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_read(actor, question.course_id)
        version = _inspected_version(bank.list_versions(question_id))
        if version is None:  # pragma: no cover - every question has a version
            raise NotFound("question has no version")

        cases = ProtectedContentRepository(session).list_cases(version.question_version_id)
        manifest = manifest_from_stored(version.adapter_manifest)
        report = analyse_comparison(manifest, cases)
        version_id = version.question_version_id

    log_event(
        get_logger("domain.diagnostics"),
        "question.diagnostics.served",
        question_id=str(question_id),
        question_version_id=str(version_id),
        attempt_count=len(report.findings),
        outcome="findings" if report.findings else "clean",
    )
    return version_id, report


def _inspected_version(versions: list[QuestionVersion]) -> QuestionVersion | None:
    """Newest first, as ``list_versions`` returns them."""
    for version in versions:
        if version.status == "published":
            return version
    return versions[0] if versions else None
