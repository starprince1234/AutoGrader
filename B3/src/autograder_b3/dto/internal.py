"""INTERNAL DTOs - protected content, write inputs, package and consumed events.

Nothing in this module may reach a public response, an event payload, a log line
or a student-accessible store (``docs/architecture/boundaries.md`` §1/§2). It is
frozen by ADR-0004: it is the **owner of the protected vocabulary**
(``PROTECTED_FIELD_NAMES``) so that ``dto/public.py`` can be free of those names.

Reading these objects requires the protected database role
(``DATABASE_PROTECTED_URL``); the ordinary question-bank role has no grant on
``b3.reference_answers`` / ``b3.test_cases`` / ``b3.blobs`` (acceptance 3-10-3).

Two structural facts worth stating explicitly, because they follow from the
frozen contract rather than from a B3 preference:

1. ``MAIN/contracts/schemas/evaluation-package.schema.json`` has
   ``additionalProperties: false`` and **no** reference-answer field. A reference
   answer therefore never travels in a package: it is used for publish-time
   completeness validation and protected storage, and - since ``CHG-0023`` - it also
   travels over one dedicated internal endpoint (``getReferenceAnswer``) whose only
   consumer is the verification path. That channel exists because the package is
   fetched for *every* evaluation while the answer is needed only to verify one, so
   putting it in the package would widen protected content in flight and change
   ``package_digest`` for every published version.
2. ``MAIN/docs/api/events.md`` §3.4 forbids student identity in
   ``evaluation.completed``. :class:`EvaluationCompletedEvent` consequently has no
   identity field at all - the privacy boundary is a type-level fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from collections.abc import Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["python", "bash"]
AdapterKind = Literal["stdin", "file", "callable"]
FixtureMount = Literal["input", "expected_output"]
CompareMode = Literal["exact", "ignore_trailing_whitespace", "numeric_tolerance", "ignored"]
StderrCompareMode = Literal["exact", "ignore_trailing_whitespace", "ignored"]
AuthoringTopic = Literal["quality", "comparison_policy", "wording", "difficulty", "coverage"]

#: Field names that must never appear in a public DTO, an event, a log line or a
#: student-accessible store. Owned by this module (ADR-0004 §3); imported by the
#: serializer and by the contract tests.
PROTECTED_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "reference_answer",
        "reference_answers",
        "answer_key",
        "hidden_cases",
        "test_cases",
        "expected_output",
        "expected_outputs",
        "expected_stdout",
        "expected_stderr",
        "expected_exit_code",
        "fixture_content",
        "fixtures",
        "source_code",
        "student_id",
        "submitted_code",
    }
)

_STRICT = ConfigDict(extra="forbid")


# ==========================================================================
# Wire inputs (teacher writes). Protected content travels on this path only.
# ==========================================================================


class ComparisonInput(BaseModel):
    model_config = _STRICT

    stdout: CompareMode = "exact"
    stderr: StderrCompareMode = "ignored"
    #: ``allow_inf_nan=False`` is not decoration: ``nan > 0`` is false so a plain ``ge=0``
    #: lets ``nan`` through some checks, and ``inf`` passes ``ge=0`` outright. Either would
    #: reach a comparison policy where it means "never equal" or "always within tolerance".
    numeric_tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CallableTargetInput(BaseModel):
    model_config = _STRICT

    module: str = Field(min_length=1, max_length=200)
    attribute: str = Field(min_length=1, max_length=200)
    arguments_json: str | None = Field(default=None, max_length=8192)


class DeclaredFileInput(BaseModel):
    model_config = _STRICT

    path: str = Field(min_length=1, max_length=200)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mount: FixtureMount = "input"


class AdapterManifestInput(BaseModel):
    """The contract's ``AdapterManifest`` (``openapi/b3.yaml``) as a write input."""

    model_config = _STRICT

    kind: AdapterKind
    args: tuple[str, ...] = ()
    stdin: str | None = None
    expect_exit_code: int | None = None
    comparison: ComparisonInput = Field(default_factory=ComparisonInput)
    files: tuple[DeclaredFileInput, ...] = ()
    callable: CallableTargetInput | None = None


class TestCaseInputModel(BaseModel):
    """The contract's ``TestCaseInput`` (PROTECTED)."""

    model_config = _STRICT

    name: str = Field(min_length=1, max_length=200)
    ordinal: int = Field(ge=0)
    weight: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    visible_to_student: bool = False
    stdin: str | None = None
    args: tuple[str, ...] = ()
    expected_stdout: str | None = None
    expected_stderr: str | None = None
    expected_exit_code: int | None = None


class QuestionDraftInputModel(BaseModel):
    """The contract's ``QuestionDraftInput`` - the only path that accepts answers.

    Two of its fields are protected, and omitting either one **preserves** what is stored
    rather than clearing it (``CHG-0017``). That is what lets a caller fix a title or a tag
    without first reading the hidden cases and the reference answer out of the bank and
    sending them back: the round trip that the executor/recorder split exists to avoid
    simply does not happen. The per-field notes below record how "omitted" is told apart
    from "sent" for each.
    """

    model_config = _STRICT

    title: str = Field(min_length=1, max_length=300)
    stem_markdown: str = Field(min_length=1)
    language: Language
    reference_answer: str | None = None
    adapter: AdapterManifestInput
    #: PROTECTED. Omitted keeps the version's existing cases unchanged; supplied replaces
    #: the whole set (CHG-0017 "omission preserves"). Deliberately **not** nullable and
    #: still ``minItems: 1`` when present, because the contract declares an array:
    #: ``null`` is a type error and ``[]`` breaks the minimum, so "supplied" and "empty"
    #: are mutually exclusive by construction -- which is what makes "a version that has
    #: cases can never lose all of them" a property of the wire format rather than a rule
    #: somebody has to remember. Omission is the empty tuple here and is told apart from a
    #: supplied set by ``model_fields_set`` (only the transport can see the difference),
    #: never by the value.
    cases: tuple[TestCaseInputModel, ...] = Field(default=(), min_length=1)
    max_files: int = Field(default=20, ge=1, le=100)
    max_total_bytes: int = Field(default=1024 * 1024, ge=1, le=10 * 1024 * 1024)
    #: Topic tags (CHG-0008). Shape only: normalised in ``domain/tags.py``, which is
    #: also where the decision "no controlled vocabulary" is recorded and justified.
    tags: tuple[str, ...] = ()
    #: Why this revision exists (CHG-0013). The write path refuses it unless the caller
    #: also asked for a new version, so the field cannot be accepted and then dropped.
    revision_note: str | None = Field(default=None, max_length=500)


class ReferenceAttestationInputModel(BaseModel):
    """The contract's ``ReferenceAttestationInput`` (internal, service-to-service).

    The execution happened outside B3; this is only the record. ``content_digest`` is
    cross-checked against the digest B3 computes, so it cannot be used to bind an
    attestation to content the verifier never ran.
    """

    model_config = _STRICT

    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier: str = Field(min_length=1, max_length=200)
    cases_total: int = Field(ge=1, le=1000)
    cases_passed: int = Field(ge=0, le=1000)
    verified_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class AuthoringAssistanceRequestModel(BaseModel):
    """The contract's ``AuthoringAssistanceRequest`` (``CHG-0011``).

    ``topics`` is an enum in the contract, so the prompt surface is closed: a caller
    cannot ask the model to "do anything else". ``uniqueItems`` is declared, and the
    contract's array bounds are mirrored here.
    """

    model_config = _STRICT

    topics: tuple[AuthoringTopic, ...] = Field(min_length=1, max_length=6)
    notes: str | None = Field(default=None, max_length=1000)


class AssignmentCreateInputModel(BaseModel):
    """The contract's inline create-assignment body."""

    model_config = _STRICT

    course_id: UUID
    section_id: UUID
    title: str = Field(min_length=1, max_length=200)
    due_at: datetime | None = None
    question_version_ids: tuple[UUID, ...] = Field(min_length=1)


class AssetUploadInputModel(BaseModel):
    """Stem image upload.

    Declared in the contract as `AssetUploadInput` since the CHG-B3-0002 ruling
    (`MAIN/docs/contracts/changes/CHG-B3-0002-asset-and-fixture-endpoints.md`), which
    closed the gap between `openapi/b3.yaml` and ``RQ-B-3-10`` acceptance 4/5.
    """

    model_config = _STRICT

    content_type: str
    content_base64: str
    alt_text: str | None = Field(default=None, max_length=500)


class FixtureUploadInputModel(BaseModel):
    """Fixture upload; returns the digest the client then declares in the manifest.

    Declared in the contract as `FixtureUploadInput` since the CHG-B3-0002 ruling.
    """

    model_config = _STRICT

    path: str = Field(min_length=1, max_length=200)
    mount: FixtureMount = "input"
    content_base64: str


class QuestionDraftGenerationRequestModel(BaseModel):
    """The contract's ``QuestionDraftGenerationRequest`` (AI-FLOW-01A).

    ``learning_objective`` and ``constraints`` carry **no fixed maximum here** on purpose:
    the accepted length is a deployment decision (provider context window, cost per token)
    and the contract must not freeze one environment's number into every other one. The
    configured limit is enforced in :func:`autograder_b3.draft_generation.build_generation_payload`
    and reported to clients by the capabilities read, so a client can show the real limit
    instead of guessing.

    ``language`` and ``adapter_kind`` are **teacher-chosen constraints**, not model output:
    they are what the exercise has to be runnable as, so they travel in the request and are
    echoed unchanged into the candidate. The model never widens them.
    """

    model_config = _STRICT

    learning_objective: str = Field(min_length=1)
    language: Language
    adapter_kind: AdapterKind
    difficulty: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    desired_case_count: int | None = Field(default=None, ge=1, le=100)
    constraints: str | None = None


# ==========================================================================
# Protected content and compile inputs
# ==========================================================================


@dataclass(frozen=True)
class ReferenceAnswerInternal:
    """A reference answer (``RQ-B-3-10``). Protected; never serialized outbound."""

    reference_answer_id: UUID
    question_version_id: UUID
    language: Language
    body: str


@dataclass(frozen=True)
class TestCaseInternal:
    """A test case, hidden or visible. ``visible_to_student`` defaults to hidden."""

    #: Not a pytest test class, despite the name.
    __test__ = False

    case_id: UUID
    question_version_id: UUID
    name: str
    ordinal: int
    weight: float = 1.0
    visible_to_student: bool = False
    stdin: str | None = None
    args: tuple[str, ...] = ()
    expected_stdout: str | None = None
    expected_stderr: str | None = None
    expected_exit_code: int | None = None


@dataclass(frozen=True)
class DeclaredFile:
    """A fixture as *declared* by the author: path, digest and mount."""

    path: str
    content_sha256: str
    mount: FixtureMount = "input"


@dataclass(frozen=True)
class FixtureInternal:
    """A fixture as *stored*: bound to a version, with the digest B3 computed."""

    fixture_id: UUID
    question_version_id: UUID
    path: str
    mount: FixtureMount
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ComparisonPolicy:
    stdout: CompareMode = "exact"
    stderr: StderrCompareMode = "ignored"
    numeric_tolerance: float | None = None


@dataclass(frozen=True)
class CallableTarget:
    module: str
    attribute: str
    arguments_json: str | None = None


@dataclass(frozen=True)
class AdapterManifestInternal:
    """Validated adapter manifest.

    Only ``compile.manifest.validate_adapter_manifest`` may construct one
    (ADR-0005 §4), so every consumer can rely on the invariants it enforces.
    """

    kind: AdapterKind
    args: tuple[str, ...] = ()
    stdin: str | None = None
    expect_exit_code: int | None = None
    comparison: ComparisonPolicy = field(default_factory=ComparisonPolicy)
    files: tuple[DeclaredFile, ...] = ()
    callable: CallableTarget | None = None


@dataclass(frozen=True)
class VersionContentSnapshot:
    """Everything that makes up a version's immutable content.

    Its canonical digest becomes ``content_digest``, frozen at publish time
    (ADR-0001). The digest is the only part of this object that ever leaves the
    protected store.
    """

    question_version_id: UUID
    question_id: UUID
    version: int
    language: Language
    stem_markdown: str
    reference_answers: tuple[ReferenceAnswerInternal, ...]
    manifest: AdapterManifestInternal
    cases: tuple[TestCaseInternal, ...]
    fixtures: tuple[FixtureInternal, ...]
    max_files: int
    max_total_bytes: int


@dataclass(frozen=True)
class PublishedVersionSnapshot:
    """Compile input: only what a package may legally contain.

    Deliberately *without* reference answers - see the module docstring. Since ``CHG-0028``
    this is also what a **draft** compiles from: a package is the executable input, and the
    executable inputs have to be reachable before publication, because the publish gate needs
    an attestation and an attestation is accepted only for a draft.
    """

    question_version_id: UUID
    question_id: UUID
    version: int
    language: Language
    content_digest: str
    #: The timestamp the package reports as its ``created_at``: the version's publication date
    #: for a published version, and the version content's own timestamp for a draft
    #: (``CHG-0028``). The name is historical -- the published path introduced it (ADR-0001) --
    #: and is kept because this type is on the frozen internal-DTO list, so renaming it is an
    #: ADR-level change rather than a drive-by edit.
    published_at: datetime
    max_files: int
    max_total_bytes: int
    manifest: AdapterManifestInternal


@dataclass(frozen=True)
class SecurityPolicy:
    """Sandbox policy constants (``evaluation-package.schema.json``).

    The three boolean constants encode ADR-0004 guarantees (no network, read-only
    root, never root). Numeric limits default to the values in
    ``MAIN/contracts/examples/evaluation-package.json``.
    """

    network: bool = False
    read_only_root: bool = True
    allow_root: bool = False
    max_memory_bytes: int = 100_663_296
    max_cpu_seconds: float = 5.0
    max_wall_seconds: float = 10.0
    max_output_bytes: int = 65_536
    max_processes: int = 16
    max_files_created: int | None = 64


@dataclass(frozen=True)
class EvaluationPackage:
    """The immutable artifact consumed by B2's runner core.

    ``adapter`` and ``tests`` hold already-normalized contract shapes produced by
    the adapter compilers and the compiler core respectively; ``to_wire`` makes
    the result JSON-ready and is the input to ``compute_package_digest``.
    """

    question_version_id: UUID
    package_digest: str
    language: Language
    adapter: Mapping[str, Any]
    tests: tuple[Mapping[str, Any], ...]
    security_policy: SecurityPolicy
    max_files: int
    max_total_bytes: int
    created_at: datetime

    def to_wire(self) -> dict[str, Any]:
        from .wire import package_to_wire

        return package_to_wire(self)


@dataclass(frozen=True)
class ReferenceAnswerDelivery:
    """One version's reference answer, for the verification path (``CHG-0023``).

    This is the response of ``getReferenceAnswer``, and it lives here rather than in
    ``dto/public.py`` for two independent reasons, both structural:

    * ``dto/public.py`` may not contain protected vocabulary at all (ADR-0004), and the
      public serialization chokepoint **refuses** the key name ``reference_answer``
      outright -- the same refusal that keeps the answer away from students. So this
      projection is built here, in the module that owns that vocabulary, exactly as
      :class:`EvaluationPackage` builds its own wire form.
    * It is scoped to the *consumer*: the answer is what B2 runs to verify a version before
      publish, and the response carries **only** the five declared fields. Hidden-case
      expectations, case content and fixture bytes are deliberately absent -- they already
      have their own channels (package, and ``adapter.files`` digests), and adding them here
      would turn a narrow delivery into a general protected-content firehose.

    ``content_digest`` is the same value the version's public DTO shows and that
    ``CHG-0008``'s attestation binds, so "the answer that was executed" and "the content
    that was attested" are the same content by construction.
    """

    question_version_id: UUID
    content_digest: str
    language: Language
    adapter_kind: AdapterKind
    body: str

    def to_wire(self) -> dict[str, Any]:
        """The declared five-key shape. Written out here, not in ``dto/wire.py``.

        ``dto/wire.py`` is a wire module scanned for protected key names, because the
        evaluation package must never grow a ``reference_answer`` key. This endpoint must
        have exactly that key, so its projection belongs in the internal module that is
        allowed to name it.
        """
        return {
            "question_version_id": str(self.question_version_id),
            "content_digest": self.content_digest,
            "language": self.language,
            "adapter_kind": self.adapter_kind,
            "reference_answer": self.body,
        }


# ==========================================================================
# Consumed event (de-identified)
# ==========================================================================


@dataclass(frozen=True)
class EvaluationCompletedEvent:
    """``autograder.v1.evaluation.completed`` after whitelist validation.

    There is no student identity field and no source-code field. Attempt counts
    are therefore the only head-count statistic B3 can publish: "distinct
    students" is not computable here by contract design
    (``MAIN/docs/api/events.md`` §3.4 待决问题), and ``QuestionStatistics`` in the
    frozen OpenAPI has no field for it.
    """

    event_id: UUID
    occurred_at: datetime
    correlation_id: UUID
    evaluation_run_id: UUID
    question_version_id: UUID
    submission_id: UUID | None = None
    course_id: UUID | None = None
    section_id: UUID | None = None
    is_pass: bool | None = None
    score: float | None = None
    max_score: float | None = None
    score_bucket: str | None = None
    result_digest: str | None = None
    failed_category: str | None = None
    duration_ms: int | None = None
