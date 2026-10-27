"""Public response serialization - the single chokepoint (ADR-0004 §4).

Every public response body is produced here. Two properties are enforced, not
documented:

1. **Whitelist projection.** The wire object's key set is exactly the DTO's field
   set. Nothing extra can slip out, and a model field that is somehow renamed
   cannot silently change the contract.
2. **Protected vocabulary check.** Any key matching
   ``dto.internal.PROTECTED_FIELD_NAMES`` raises before serialization, so a
   mistake becomes a loud ``500`` in tests rather than a leak in production.

Routers must never return an ORM object or an internal DTO directly; the contract
test asserts field *absence* on real responses (TC-QST-01).
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any
from collections.abc import Mapping
from uuid import UUID

from pydantic import BaseModel

from ..assistance import AssistanceResult
from ..dto.internal import PROTECTED_FIELD_NAMES
from ..dto.public import (
    AssignmentPublic,
    AuthoringAssistancePublic,
    AuthoringProvenancePublic,
    AuthoringSuggestionPublic,
    ComparisonDiagnosticsPublic,
    ComparisonFindingPublic,
    QuestionPublic,
    QuestionStatisticsPublic,
    QuestionTagCoveragePublic,
    QuestionVersionPublic,
    QuestionVersionSummary,
    QuestionLearningContextPublic,
    ReferenceAttestationPublic,
    RevisionDiffPublic,
    StemAssetPublic,
    TagCountPublic,
)
from ..domain.catalog import TagCoverage
from ..domain.tags import tags_from_storage
from ..errors import B3Error
from ..logging import to_rfc3339_millis
from ..stats.service import StatisticsResult


class SerializationViolation(B3Error):
    """A protected field reached a public serializer. Always a defect."""

    status = 500
    title = "Internal Server Error"
    slug = "public-serialization-violation"


def to_wire(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {name: to_wire(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return to_rfc3339_millis(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_wire(item) for item in value]
    return value


def assert_no_protected_keys(payload: Any, *, path: str = "$") -> None:
    if isinstance(payload, Mapping):
        for key, item in payload.items():
            if str(key).lower() in PROTECTED_FIELD_NAMES:
                raise SerializationViolation(
                    f"protected field {path}.{key} reached a public serializer"
                )
            assert_no_protected_keys(item, path=f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            assert_no_protected_keys(item, path=f"{path}[{index}]")


def serialize_public(dto: BaseModel) -> dict[str, Any]:
    """Whitelist-project ``dto`` onto the wire and prove nothing protected escaped."""
    expected = set(type(dto).model_fields)
    payload = to_wire(dto)
    unexpected = set(payload) - expected
    if unexpected:
        raise SerializationViolation(
            "public serializer produced field(s) outside the DTO: "
            + ", ".join(sorted(unexpected))
        )
    assert_no_protected_keys(payload)
    return payload


# --- ORM/result -> public DTO --------------------------------------------


def question_to_public(
    row: Any,
    assets: list[Any] | None = None,
    versions: list[Any] | None = None,
) -> QuestionPublic:
    return QuestionPublic(
        question_id=row.question_id,
        course_id=row.course_id,
        title=row.title,
        stem_markdown=row.stem_markdown,
        stem_assets=tuple(
            StemAssetPublic(
                asset_id=asset.asset_id,
                content_type=asset.content_type,
                alt_text=asset.alt_text,
            )
            for asset in (assets or [])
        ),
        language=row.language,
        status=row.status,
        # Read through the validator, not straight off the row: a tag that violates the
        # declared form is a defect and must fail loudly here rather than reach a public
        # response, where a client would have no way to tell it was wrong.
        tags=tags_from_storage(row.tags),
        versions=tuple(
            QuestionVersionSummary(
                question_version_id=item.question_version_id,
                version=item.version,
                status=item.status,
                published_at=item.published_at,
            )
            for item in (versions or [])
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def version_to_public(row: Any, assets: list[Any] | None = None) -> QuestionVersionPublic:
    manifest = row.adapter_manifest or {}
    kind = manifest.get("kind")
    if kind not in ("stdin", "file", "callable"):
        # A stored manifest is validated when written; this guard keeps an
        # unexpected value out of the public enum rather than emitting junk.
        raise SerializationViolation("stored adapter kind is not a contract value")
    return QuestionVersionPublic(
        question_version_id=row.question_version_id,
        question_id=row.question_id,
        version=row.version,
        status=row.status,
        language=row.language,
        adapter_kind=kind,
        stem_markdown=row.stem_markdown,
        stem_assets=tuple(
            StemAssetPublic(
                asset_id=asset.asset_id,
                content_type=asset.content_type,
                alt_text=asset.alt_text,
            )
            for asset in (assets or [])
        ),
        content_digest=row.content_digest,
        revision_note=row.revision_note,
        revision_diff=(
            None if row.revision_diff is None else RevisionDiffPublic(**row.revision_diff)
        ),
        published_at=row.published_at,
        max_files=row.max_files,
        max_total_bytes=row.max_total_bytes,
    )


def diagnostics_to_public(question_id: UUID, report: Any) -> ComparisonDiagnosticsPublic:
    """Project a diagnostics report. Rule ids, severities and readable text only.

    ``Finding.detail`` is written as a description of the declaration that is wrong,
    never as a quotation of it, which is what keeps the response safe next to content the
    caller may not read. The field is projected explicitly rather than by ``**asdict`` so
    a future field on the internal record cannot become a response key by accident.
    """
    return ComparisonDiagnosticsPublic(
        question_id=question_id,
        findings=tuple(
            ComparisonFindingPublic(
                rule_id=item.rule_id,
                severity=item.severity,
                message=item.detail,
                ordinal=item.case_ordinal,
            )
            for item in report.findings
        ),
    )


def coverage_to_public(result: TagCoverage) -> QuestionTagCoveragePublic:
    """Project a tag distribution. Counts and tag strings, and nothing else exists here.

    The projection is explicit rather than derived from the result object, so a field
    added to :class:`TagCoverage` cannot become a response key by accident -- which is the
    property that keeps this response a distribution instead of a way to walk the bank.
    """
    return QuestionTagCoveragePublic(
        course_id=result.course_id,
        total_questions=result.total_questions,
        tagged_questions=result.tagged_questions,
        untagged_questions=result.untagged_questions,
        tags=tuple(
            TagCountPublic(tag=tag, question_count=count) for tag, count in result.tag_counts
        ),
    )


def learning_context_to_public(context: Any) -> QuestionLearningContextPublic:
    """Project the public half of a version for B2's hint generation (``AI-FLOW-01B``).

    Explicit field by field rather than ``**vars``: a field added to the domain record must
    not become a response key by accident, which is exactly the mistake that would widen what
    a model gets to see.
    """
    return QuestionLearningContextPublic(
        question_version_id=context.question_version_id,
        title=context.title,
        stem_markdown=context.stem_markdown,
        language=context.language,
        tags=context.tags,
        adapter_kind=context.adapter_kind,
    )


def statistics_to_public(result: StatisticsResult) -> QuestionStatisticsPublic:    return QuestionStatisticsPublic(
        question_id=result.question_id,
        attempt_count=result.attempt_count,
        pass_rate=result.pass_rate,
        question_version_id=result.question_version_id,
        score_distribution=dict(result.score_distribution),
        failure_category_distribution=dict(result.failure_category_distribution),
        computed_at=result.computed_at,
    )


def attestation_to_public(record: Any) -> ReferenceAttestationPublic:
    """Project a recorded attestation onto its public shape.

    The record's ``notes`` field is deliberately not projected: it is free text a
    verifier wrote for operators, and the contract's public shape has no place for it.
    """
    return ReferenceAttestationPublic(
        attestation_id=record.attestation_id,
        question_version_id=record.question_version_id,
        content_digest=record.content_digest,
        verifier=record.verifier,
        cases_total=record.cases_total,
        cases_passed=record.cases_passed,
        verified_at=record.verified_at,
    )


def authoring_assistance_to_public(
    question_id: UUID, result: AssistanceResult
) -> AuthoringAssistancePublic:
    """Project authoring advice. Model output, never question content."""
    return AuthoringAssistancePublic(
        question_id=question_id,
        status="ready",
        suggestions=tuple(
            AuthoringSuggestionPublic(
                topic=item.topic, message=item.message, confidence=item.confidence
            )
            for item in result.suggestions
        ),
        provenance=(
            None
            if result.provenance is None
            else AuthoringProvenancePublic(
                provider=result.provenance.provider,
                model=result.provenance.model,
                prompt_version=result.provenance.prompt_version,
            )
        ),
        disclaimers=result.disclaimers,
    )


def assignment_to_public(row: Any, version_ids: list[UUID]) -> AssignmentPublic:
    return AssignmentPublic(
        assignment_id=row.assignment_id,
        course_id=row.course_id,
        section_id=row.section_id,
        title=row.title,
        status=row.status,
        due_at=row.due_at,
        question_version_ids=tuple(version_ids),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
