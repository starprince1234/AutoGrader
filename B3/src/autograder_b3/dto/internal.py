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

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["python", "bash"]
AdapterKind = Literal["stdin", "file", "callable"]
FixtureMount = Literal["input", "expected_output"]
#: An assignment's lifecycle state (``open`` / ``closed`` / ``archived``); the same enum the
#: public DTO carries. Restated here because the internal module must not import the public
#: one. B2 refuses a submission for anything but ``open``, so ``closed`` is a real refusal.
AssignmentStatus = Literal["open", "closed", "archived"]
CompareMode = Literal["exact", "ignore_trailing_whitespace", "numeric_tolerance", "ignored"]
StderrCompareMode = Literal["exact", "ignore_trailing_whitespace", "ignored"]
AuthoringTopic = Literal["quality", "comparison_policy", "wording", "difficulty", "coverage"]
#: How a package's cases are produced (``common.yaml`` ``ExecutionMode``). ``per_case`` is
#: the legacy default: one sandbox execution per case, which is what every version
#: published before the field existed means.
ExecutionMode = Literal["per_case", "single_run", "ssh_session", "email_delivery"]
#: Real server-side facts an ``ssh_session`` case can judge (exactly one per case).
SshCheck = Literal["service_ready", "auth_succeeded", "session_closed"]
#: Facts about the actually received message an ``email_delivery`` case can judge.
EmailCheck = Literal["email_received", "subject", "sender", "attachment"]

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
        # Execution declarations added for the Unix homeworks (SSH, email, file state,
        # segmentation). They are *configuration* for the evaluator, and may carry an SSH
        # password or the expected file digests, so they belong to the same class of
        # protected vocabulary: never a public response, event or log key.
        "execution_manifest",
        "file_checks",
        "segment_delimiter",
        "subject_template",
        "password",
        "grading_prompt",
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


class FileCheckInputModel(BaseModel):
    """One post-run file assertion (``FileCheckInput``, PROTECTED).

    ``content_sha256`` and ``mode`` are both optional and at least one must be present:
    a check that asserts nothing cannot fail, so accepting it would make the author believe
    a file was verified when the evaluator only looked for its path. ``mode`` is the contract's
    canonical four-digit octal text (``"0744"``): the evaluator reports actual permission bits
    in that same canonical form, so a three-digit declaration would never compare equal.
    """

    model_config = _STRICT

    path: str = Field(min_length=1, max_length=200)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mode: str | None = Field(default=None, pattern=r"^0[0-7]{3}$")


class ExecutionEmailInputModel(BaseModel):
    """``ExecutionEmail``: what a delivered message must satisfy (PROTECTED)."""

    model_config = _STRICT

    attempt: int = Field(ge=1, le=99)
    subject_template: str = Field(
        default="{student_number}_{attempt}", min_length=1, max_length=200
    )


class ExecutionSshInputModel(BaseModel):
    """``ExecutionSsh``: the practice service for an ``ssh_session`` version (PROTECTED).

    ``password`` is protected content: it is accepted on this authoring channel and in the
    internal evaluation package only, never on a public DTO, event or log line.
    """

    model_config = _STRICT

    host: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    #: The contract bounds both timeouts to (0, 30]; the lower bound 0.1 is shared with B2's
    #: package parser (see ``compile.manifest``), because a declaration B2 refuses must not be
    #: publishable here. The defaults are the contract's.
    login_timeout_seconds: float = Field(default=10.0, ge=0.1, le=30, allow_inf_nan=False)
    session_timeout_seconds: float = Field(default=10.0, ge=0.1, le=30, allow_inf_nan=False)


class ExecutionManifestInputModel(BaseModel):
    """The contract's ``ExecutionManifest`` as a write input.

    The model only enforces the declared *shape*; the mode-specific rules (which fields are
    required/forbidden per mode, and how the declaration lines up with the adapter and the
    cases) are enforced by ``compile.manifest.validate_execution_manifest`` so the draft
    path, the publish path and the internal package path share one implementation. Shape
    errors are a ``422`` from the transport either way.
    """

    model_config = _STRICT

    mode: ExecutionMode
    segment_delimiter: str | None = Field(
        default=None, min_length=8, max_length=120, pattern=r"^[!-~](?:[ -~]*[!-~])?$"
    )
    email: ExecutionEmailInputModel | None = None
    ssh: ExecutionSshInputModel | None = None


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
    #: Flat per-case execution declaration (segment/file_checks/ssh_check/email_check). The
    #: mode-specific rules are checked in ``compile.manifest``; these defaults make an omitted
    #: declaration mean "this case declares nothing", which is the legacy per_case shape.
    segment: int | None = Field(default=None, ge=0)
    file_checks: tuple[FileCheckInputModel, ...] = Field(default=(), max_length=20)
    ssh_check: SshCheck | None = None
    email_check: EmailCheck | None = None


class ReferenceAnswerTierInput(BaseModel):
    """One graded reference-answer tier (e.g. brute-force vs optimal solution)."""

    model_config = _STRICT

    label: str = Field(default="标准参考答案", min_length=1, max_length=200)
    body: str = Field(min_length=1)


class QuestionDraftInputModel(BaseModel):
    """The contract's ``QuestionDraftInput`` - the only path that accepts answers.

    Protected fields follow omission-preserves semantics: omitting a protected
    field preserves what is stored rather than clearing it.
    """

    model_config = _STRICT

    title: str = Field(min_length=1, max_length=300)
    stem_markdown: str = Field(min_length=1)
    language: Language
    reference_answer: str | None = None
    reference_answers: tuple[ReferenceAnswerTierInput, ...] = ()
    grading_prompt: str | None = Field(default=None, max_length=10000)
    adapter: AdapterManifestInput = Field(
        default_factory=lambda: AdapterManifestInput(kind="stdin")
    )
    execution: ExecutionManifestInputModel | None = None
    cases: tuple[TestCaseInputModel, ...] = ()
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
    """The contract's ``CreateAssignment`` body (v0.32.0).

    An assignment is **course-level**: it belongs to exactly one course and automatically
    applies to every section (课序), class (班级) and student of that course, including the
    ones added later while it is open. The body therefore carries no target ids at all --
    a hand-picked section/student list would be a second, silently diverging answer to
    "who is this assignment for".

    ``question_version_ids`` stays ordered (item ordinal = array position, answer files are
    named by the 1-based position) and is a **set on the wire**: the contract declares
    ``uniqueItems``, so a duplicate is a ``422`` before any lookup happens rather than a
    row that fails a database unique constraint halfway through the transaction.
    """

    model_config = _STRICT

    course_id: UUID
    title: str = Field(min_length=1, max_length=200)
    due_at: datetime | None = None
    question_version_ids: tuple[UUID, ...] = Field(min_length=1, max_length=50)

    @field_validator("question_version_ids")
    @classmethod
    def _unique(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("question_version_ids must not contain duplicates")
        return value


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


class QuestionVersionBatchInputModel(BaseModel):
    """The contract's ``QuestionVersionBatchInput``.

    A batch is a **client convenience over the single-item operations**, never a second
    implementation of them: every id is attempted and reported on its own, so one rejected
    item cannot hide the fate of the rest ("delete 12 drafts" must not leave a teacher
    guessing which 11 succeeded).

    The list is bounded (``maxItems`` in the contract) because each item is a separate
    transaction, and duplicates are rejected rather than deduplicated: a client that sends
    the same version twice cannot see which of the two results it is reading.
    """

    model_config = _STRICT

    operation: Literal["delete", "publish"]
    question_version_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)

    @field_validator("question_version_ids")
    @classmethod
    def _unique(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("question_version_ids must not contain duplicates")
        return value


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
    desired_reference_tiers: int | None = Field(default=None, ge=1, le=5)
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
    ordinal: int = 0
    label: str = "标准参考答案"
    grading_prompt: str | None = None


@dataclass(frozen=True)
class FileCheck:
    """One post-run assertion on a final workspace file (PROTECTED expected values)."""

    path: str
    content_sha256: str | None = None
    mode: str | None = None


@dataclass(frozen=True)
class CaseExecution:
    """The per-case half of an execution declaration.

    Empty for the legacy shape. A case's ``segment`` is single_run-only, ``ssh_check``
    ssh_session-only, ``email_check`` email_delivery-only, and ``file_checks`` may accompany
    per_case or single_run (a mode that runs code in a workspace); the mode-specific
    admissibility is enforced by ``compile.manifest`` and mirrors B2's package parser.
    """

    segment: int | None = None
    file_checks: tuple[FileCheck, ...] = ()
    ssh_check: SshCheck | None = None
    email_check: EmailCheck | None = None


@dataclass(frozen=True)
class ExecutionEmail:
    """``email_delivery`` requirements: attempt plus the subject template the receipt must match.

    From CHG-0032 the answer attachment is not templated per question: a submission is one
    assignment question and its answer file is named by that question's 1-based position in
    the assignment, so the ``attachment`` check means "the received message mapped an answer
    file to this question". Only ``attempt`` and ``subject_template`` remain.
    """

    attempt: int
    subject_template: str = "{student_number}_{attempt}"


@dataclass(frozen=True)
class ExecutionSsh:
    """``ssh_session`` practice service. ``password`` is protected content."""

    host: str
    port: int
    username: str
    password: str
    login_timeout_seconds: float = 10.0
    session_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class ExecutionManifest:
    """A version's explicit execution declaration (``ExecutionManifest``, PROTECTED).

    ``mode="per_case"`` is the default and the meaning of a NULL stored declaration. Only a
    non-default declaration is written into the package or into the content digest, which is
    what keeps already-published versions byte- and digest-identical after this feature.
    """

    mode: ExecutionMode = "per_case"
    segment_delimiter: str | None = None
    email: ExecutionEmail | None = None
    ssh: ExecutionSsh | None = None


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
    #: Per-case execution declaration (segment/file_checks/ssh_check/email_check). The default
    #: is the empty declaration, which is the legacy ``per_case`` case shape -- and the reason
    #: content digests of already-published versions stay reproducible (see
    #: ``domain.content.content_wire``).
    execution: CaseExecution = field(default_factory=lambda: CaseExecution())


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
    #: The version-level execution declaration. The default is ``per_case`` and, like a NULL
    #: stored declaration, it is **not** part of the content digest: see
    #: ``domain.content.content_wire`` for the compatibility rule this protects.
    execution: ExecutionManifest = field(default_factory=ExecutionManifest)


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
    #: Execution declaration carried into the package. Omitted means ``per_case`` (the default,
    #: and the shape of every package compiled before the field existed).
    execution: ExecutionManifest = field(default_factory=ExecutionManifest)


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
    #: The wire form of a **non-default** execution declaration; ``None`` means ``per_case``
    #: and keeps the package byte-identical to what B3 compiled before the field existed
    #: (``wire.package_to_wire`` only emits the key when this is set).
    execution: Mapping[str, Any] | None = None

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
    stem_markdown: str = ""
    reference_answers: tuple[dict[str, str], ...] = ()
    grading_prompt: str | None = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "question_version_id": str(self.question_version_id),
            "content_digest": self.content_digest,
            "language": self.language,
            "adapter_kind": self.adapter_kind,
            "reference_answer": self.body,
        }
        if self.stem_markdown:
            payload["stem_markdown"] = self.stem_markdown
        if self.reference_answers:
            payload["reference_answers"] = [dict(item) for item in self.reference_answers]
        if self.grading_prompt is not None:
            payload["grading_prompt"] = self.grading_prompt
        return payload


@dataclass(frozen=True)
class FixtureContentItem:
    """One input fixture's protected bytes plus the digest B3 computed from them.

    ``content_base64`` is the only place in B3 where fixture bytes leave the database
    deliberately, and the delivery is scoped to ``mount=input`` (see
    :class:`FixtureContentBundle`).
    """

    path: str
    mount: FixtureMount
    content_sha256: str
    size_bytes: int
    content_base64: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mount": self.mount,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "content_base64": self.content_base64,
        }


@dataclass(frozen=True)
class FixtureContentBundle:
    """The ``FixtureContentBundle`` response of ``getVersionFixtureContents`` (PROTECTED).

    Deliberately only ``mount=input``: an ``expected_output`` comparison needs the digest the
    package already carries, so a read channel for those bytes would widen protected content
    in flight for no benefit. The bundle is bounded by the delivery function that builds it
    (per-item and total caps, refused rather than truncated).
    """

    question_version_id: UUID
    items: tuple[FixtureContentItem, ...]

    def to_wire(self) -> dict[str, Any]:
        return {
            "question_version_id": str(self.question_version_id),
            "items": [item.to_wire() for item in self.items],
        }


@dataclass(frozen=True)
class AssignmentAudience:
    """The assignment audience served to the evaluation/submission module (serviceToken).

    Since v0.32.0 an assignment is **course-level**, so "who is it for" is exactly
    "everyone currently enrolled in ``course_id``". That membership is deliberately **not**
    asserted here: only B4 is authoritative for a student's current enrollment, and B2 asks
    B4 separately when admitting a submission. This response is the minimum B2 needs from
    B3: identity, status and the ordered question list. Timestamps are not needed for
    admission, so they are not exposed -- unlike the public
    :class:`~autograder_b3.dto.public.AssignmentPublic`, which renders them.
    """

    assignment_id: UUID
    course_id: UUID
    title: str
    status: AssignmentStatus
    question_version_ids: tuple[UUID, ...]
    #: 目标课序与指定学生（迁移 0012）。空元组 = 全课程 / 目标课序全体。
    #: B2 用它和 B4 的在读名单求交后决定"谁能提交"；B3 不持有名单事实，只给目标。
    section_ids: tuple[UUID, ...] = ()
    student_ids: tuple[UUID, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            "assignment_id": str(self.assignment_id),
            "course_id": str(self.course_id),
            "title": self.title,
            "status": self.status,
            "question_version_ids": [str(item) for item in self.question_version_ids],
            # 新增键：B2 更新前按名读取、忽略未知键，因此这一步不改变现有行为。
            "section_ids": [str(item) for item in self.section_ids],
            "student_ids": [str(item) for item in self.student_ids],
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
