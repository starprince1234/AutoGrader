"""Authoring-assistance orchestration (``RQ-T-10``).

Keeps :mod:`autograder_b3.assistance` (the model-facing boundary) separate from the
domain rules: who may ask, which version is reviewed, and what leaves the module. Reads
run on the **protected** role for one reason only - the case *shape* (how many cases,
what ordinals, what weights) lives in ``b3.test_cases``, and that shape is part of what
a model needs to judge coverage. The case inputs and expected outputs are read by the
same query and are then **left behind**: only the shape enters the payload, and
:func:`autograder_b3.assistance.build_provider_payload` asserts it.
"""

from __future__ import annotations

import uuid

from ..assistance import (
    AssistanceResult,
    AuthoringProvider,
    Provenance,
    build_provider_payload,
    provider_from_settings,
    require_enabled,
)
from ..auth import Principal, require_authoring_reader, require_course_read
from ..compile.manifest import manifest_from_stored
from ..errors import NotFound
from ..infra.db import Database
from ..infra.repositories import ProtectedContentRepository, QuestionBankRepository
from ..logging import get_logger, log_event


def authoring_assistance(
    database: Database,
    settings,
    *,
    principal: Principal | None,
    question_id: uuid.UUID,
    topics: tuple,
    notes: str | None,
    provider: AuthoringProvider | None = None,
) -> AssistanceResult:
    """Advise on the question's current draft. Writes nothing, anywhere."""
    # Authorization first: whether a model provider is configured is deployment
    # information, and it must not be probeable by a caller who is not allowed to use
    # this operation anyway.
    # Authorization before configuration. "Is a model provider configured here" is
    # deployment information, and a caller who is not allowed to use this operation at
    # all must not be able to read it out of the status code.
    actor = require_authoring_reader(principal)
    require_enabled(settings)

    with database.protected_session() as session:
        bank = QuestionBankRepository(session)
        question = bank.get_question(question_id)
        if question is None or question.deleted_at is not None:
            raise NotFound("question not found")
        require_course_read(actor, question.course_id)
        version = bank.latest_version(question_id)
        if version is None:  # pragma: no cover - every question has a version
            raise NotFound("question has no version")

        cases = ProtectedContentRepository(session).list_cases(version.question_version_id)
        manifest = manifest_from_stored(version.adapter_manifest)
        payload = build_provider_payload(
            title=question.title,
            stem_markdown=version.stem_markdown,
            language=version.language,
            adapter_kind=manifest.kind,
            comparison={
                "stdout": manifest.comparison.stdout,
                "stderr": manifest.comparison.stderr,
                "numeric_tolerance": manifest.comparison.numeric_tolerance,
            },
            case_count=len(cases),
            case_ordinals=tuple(case.ordinal for case in cases),
            case_weights=tuple(case.weight for case in cases),
            max_files=version.max_files,
            max_total_bytes=version.max_total_bytes,
            topics=topics,
            notes=notes,
        )

    resolved = provider or provider_from_settings(settings)
    if resolved is None:  # pragma: no cover - require_enabled already refused
        raise NotFound("authoring provider is not available")
    suggestions = resolved.advise(payload=payload, topics=topics, notes=notes)
    log_event(
        get_logger("domain.authoring"),
        "authoring.assistance.served",
        question_id=str(question_id),
        question_version_id=str(version.question_version_id),
        attempt_count=len(suggestions),
        outcome="ready",
    )
    return AssistanceResult(
        suggestions=suggestions,
        provenance=Provenance(provider=resolved.name, model=resolved.model),
    )
