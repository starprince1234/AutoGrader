"""PUBLIC DTOs - the only models that may reach a student-facing surface.

Fields are authoritative in ``MAIN/contracts/openapi/b3.yaml`` (``QuestionPublic``,
``QuestionVersionPublic``, ``QuestionStatistics``) and are **not** restated in
prose here. This module is frozen by ADR-0004:

* it must never import :mod:`autograder_b3.dto.internal`;
* it must never contain the name of a protected field (the vocabulary lives in
  ``dto/internal.py: PROTECTED_FIELD_NAMES``), so "the public module has no
  protected vocabulary" is a grep/assert fact rather than a review opinion;
* every model forbids unknown fields at construction time and is immutable;
* responses leave the process only through ``api/serializers.py`` -> ``serialize_public``.

The assignment response shape is not pinned by the frozen contract (the OpenAPI
operation declares only "Page of assignments"); B3 defines the smallest useful
public shape and records the gap in ``docs/api/contract.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["python", "bash"]
QuestionStatus = Literal["draft", "published", "archived"]
AdapterKind = Literal["stdin", "file", "callable"]
AssignmentStatus = Literal["open", "archived"]
#: The model-advice surface (``CHG-0011``). An enum, not free text, so a caller cannot
#: ask for "anything else" and every supported use is a documented one.
AuthoringTopic = Literal["quality", "comparison_policy", "wording", "difficulty", "coverage"]
#: ``unavailable`` is a configuration state, not a failure.
AuthoringStatus = Literal["unavailable", "ready", "failed"]
#: The deterministic comparison-policy findings (``CHG-0013``). Three values, exactly as
#: declared; a fourth would be unserializable by the contract.
ComparisonSeverity = Literal["error", "warning", "info"]

_PUBLIC_CONFIG = ConfigDict(extra="forbid", frozen=True)


class StemAssetPublic(BaseModel):
    """Controlled reference to a stem graphic (bytes are fetched separately)."""

    model_config = _PUBLIC_CONFIG

    asset_id: UUID
    content_type: str
    alt_text: str | None = None


class RevisionDiffPublic(BaseModel):
    """Structural delta between two consecutive versions (``CHG-0013``).

    Every field is a count, a length, a digest or a presence flag, so nothing here can
    reveal protected content: the reference answer appears as a digest only, and the
    expected outputs of hidden cases do not appear at all. The class is defined before
    the version DTO that embeds it because that is the order the contract lists them in;
    ``None`` on every ``*_before``/``*_changed`` field means "first version, nothing to
    compare against", which is a different statement from "unchanged".
    """

    model_config = _PUBLIC_CONFIG

    case_count_before: int | None = Field(default=None, ge=0)
    case_count_after: int | None = Field(default=None, ge=0)
    case_set_changed: bool | None = None
    weights_changed: bool | None = None
    fixtures_changed: bool | None = None
    stem_length_before: int | None = Field(default=None, ge=0)
    stem_length_after: int | None = Field(default=None, ge=0)
    adapter_kind_changed: bool | None = None
    size_limits_changed: bool | None = None
    reference_answer_digest_before: str | None = None
    reference_answer_digest_after: str | None = None


class ComparisonFindingPublic(BaseModel):
    """One deterministic comparison-policy finding (``CHG-0013``).

    ``rule_id``, ``severity`` and readable text. No expected output is quoted, which is
    what makes the report safe to render next to a protected version -- and the reason
    this DTO is separate from the authoring-assistance one, whose entries are model
    guesses rather than rules.
    """

    model_config = _PUBLIC_CONFIG

    rule_id: str = Field(pattern=r"^CMP-[0-9]{3}$")
    severity: ComparisonSeverity
    message: str = Field(min_length=1, max_length=500)
    ordinal: int | None = Field(default=None, ge=0)


class ComparisonDiagnosticsPublic(BaseModel):
    """Findings for one question's declared comparison policy.

    An empty ``findings`` list means every rule was evaluated and none fired. There is
    no "not checked" state in this shape, and none is needed: the endpoint runs all
    rules synchronously over a version it just read, so a report exists exactly when it
    was computed. A caller that cannot get a report gets an error status instead of an
    empty list.
    """

    model_config = _PUBLIC_CONFIG

    question_id: UUID
    findings: tuple[ComparisonFindingPublic, ...] = ()


class TagCountPublic(BaseModel):
    """One tag and how many questions carry it (``CHG-0020``).

    ``question_count`` is at least 1 because a tag no question carries is **absent** from
    the list rather than present with a 0: the number answers "how many questions mention
    this topic", and a zero would be a row asserting nothing. ``tag`` carries the
    contract's declared bounds and nothing more -- the contract declares no pattern here,
    and inventing one would turn a stored value into a serialization failure instead of a
    visible one.
    """

    model_config = _PUBLIC_CONFIG

    tag: str = Field(min_length=1, max_length=40)
    question_count: int = Field(ge=1)


class QuestionTagCoveragePublic(BaseModel):
    """How one course's bank is distributed across tags (``RQ-B-3-20``, ``CHG-0020``).

    Counts and tag strings only, and that absence is the design: with a ``question_id``
    list attached, a distribution becomes a way to walk the bank one question at a time
    (the same reason ``CHG-0015``'s quality summary is counts-only).

    ``tagged_questions + untagged_questions == total_questions`` is the identity a
    consumer may assert. ``sum(tags[].question_count)`` is **not** ``total_questions``:
    a question may carry up to 20 tags, so per-tag counts overlap on purpose.
    """

    model_config = _PUBLIC_CONFIG

    course_id: UUID
    total_questions: int = Field(ge=0)
    tagged_questions: int = Field(ge=0)
    untagged_questions: int = Field(ge=0)
    tags: tuple[TagCountPublic, ...] = ()


class QuestionVersionSummary(BaseModel):
    """One entry of a question's version history.

    Exists because without it the publish flow is unreachable through the API: a client
    can create a question but cannot learn any `question_version_id`, and `publish`
    requires exactly that. Integration hit this on the first seeding run.
    """

    model_config = _PUBLIC_CONFIG

    question_version_id: UUID
    version: int
    status: QuestionStatus
    published_at: datetime | None = None


class QuestionPublic(BaseModel):
    model_config = _PUBLIC_CONFIG

    question_id: UUID
    course_id: UUID
    title: str
    stem_markdown: str
    stem_assets: tuple[StemAssetPublic, ...] = ()
    language: Language
    status: QuestionStatus
    tags: tuple[str, ...] = ()
    #: Full version history (draft + published + archived), newest first. Required for
    #: RQ-B-1-40 acceptance 5 (switching between versions) and for publishing.
    versions: tuple[QuestionVersionSummary, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QuestionVersionPublic(BaseModel):
    model_config = _PUBLIC_CONFIG

    question_version_id: UUID
    question_id: UUID
    version: int
    status: QuestionStatus
    language: Language
    adapter_kind: AdapterKind
    #: The stem of *this* version. A version is an immutable snapshot, so rendering a
    #: historical version requires its own stem -- reading the question's current stem
    #: instead would silently show the wrong content (RQ-B-1-40 acceptance 5).
    stem_markdown: str
    stem_assets: tuple[StemAssetPublic, ...] = ()
    content_digest: str | None = None
    #: Why this version exists, as recorded by the author (CHG-0013), or the reason B3
    #: derived from the delta when the author did not record one.
    revision_note: str | None = None
    #: Structural difference from the previous version. ``None`` for a first version.
    revision_diff: RevisionDiffPublic | None = None
    published_at: datetime | None = None
    max_files: int | None = None
    max_total_bytes: int | None = None


class QuestionStatisticsPublic(BaseModel):
    model_config = _PUBLIC_CONFIG

    question_id: UUID
    attempt_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    question_version_id: UUID | None = None
    score_distribution: dict[str, int] = Field(default_factory=dict)
    failure_category_distribution: dict[str, int] = Field(default_factory=dict)
    computed_at: datetime | None = None


class AssignmentPublic(BaseModel):
    model_config = _PUBLIC_CONFIG

    assignment_id: UUID
    course_id: UUID
    section_id: UUID
    title: str
    status: AssignmentStatus
    due_at: datetime | None = None
    question_version_ids: tuple[UUID, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CursorPage(BaseModel):
    model_config = _PUBLIC_CONFIG

    items: tuple[BaseModel, ...]
    next_cursor: str | None = None


class StemAssetUploadResult(BaseModel):
    """Result of uploading a stem graphic.

    B3-defined public shape: the frozen contract declares no asset operation
    (``CHG-B3-0002``). It carries a content digest, never image bytes, and never
    anything from the protected vocabulary.
    """

    model_config = _PUBLIC_CONFIG

    asset_id: UUID
    content_type: str
    content_sha256: str
    size_bytes: int
    alt_text: str | None = None


class FixtureUploadResult(BaseModel):
    """Result of uploading a fixture.

    Returns the digest B3 computed so the author can declare it in
    ``adapter.files[]``; the bytes themselves are never echoed back
    (``CHG-B3-0002``).
    """

    model_config = _PUBLIC_CONFIG

    fixture_id: UUID
    path: str
    mount: Literal["input", "expected_output"]
    content_sha256: str
    size_bytes: int


class ReferenceAttestationPublic(BaseModel):
    """A recorded reference-answer attestation (``CHG-0008``).

    The fact, the counts and the digest only. There is deliberately no reference
    answer, no hidden case and no expected output here, and no student-visible surface
    exists at all: "this question was verified" is not information a student needs, and
    every field below is already safe for a teacher to read.
    """

    model_config = _PUBLIC_CONFIG

    attestation_id: UUID
    question_version_id: UUID
    content_digest: str
    verifier: str
    cases_total: int = Field(ge=1)
    cases_passed: int = Field(ge=0)
    verified_at: datetime | None = None


class AuthoringSuggestionPublic(BaseModel):
    """One piece of authoring advice (``RQ-T-10``, ``CHG-0011``).

    ``confidence`` has exactly one value today. It exists so a future *deterministic*
    rule - which could be authoritative - stays distinguishable from model advice
    instead of being silently mixed into the same list.
    """

    model_config = _PUBLIC_CONFIG

    topic: AuthoringTopic
    message: str = Field(min_length=1, max_length=2000)
    confidence: Literal["advisory"] = "advisory"


class AuthoringProvenancePublic(BaseModel):
    """Where advice came from, so it can never be shown unattributed."""

    model_config = _PUBLIC_CONFIG

    provider: str
    model: str
    prompt_version: str


class AuthoringAssistancePublic(BaseModel):
    """Advice about a draft question. Nothing here mutates the question.

    Note what is absent, because the absence is the design (``ADR-0009`` decision 3):
    there is no field that could carry a reference answer, a hidden case or an expected
    output, and no field whose presence would let a client apply the advice. Accepting a
    suggestion means the teacher edits the draft through the update operation, so the
    audit trail records a human action.
    """

    model_config = _PUBLIC_CONFIG

    question_id: UUID
    status: AuthoringStatus
    suggestions: tuple[AuthoringSuggestionPublic, ...] = ()
    provenance: AuthoringProvenancePublic | None = None
    disclaimers: tuple[str, ...] = ()


#: Every public model. The contract test iterates this tuple.
PUBLIC_DTO_TYPES: tuple[type[BaseModel], ...] = (
    StemAssetPublic,
    RevisionDiffPublic,
    ComparisonFindingPublic,
    ComparisonDiagnosticsPublic,
    TagCountPublic,
    QuestionTagCoveragePublic,
    QuestionPublic,
    QuestionVersionPublic,
    QuestionStatisticsPublic,
    AssignmentPublic,
    StemAssetUploadResult,
    FixtureUploadResult,
    ReferenceAttestationPublic,
    AuthoringSuggestionPublic,
    AuthoringProvenancePublic,
    AuthoringAssistancePublic,
)


def public_field_names(model: type[BaseModel]) -> frozenset[str]:
    return frozenset(model.model_fields)
