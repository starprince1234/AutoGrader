"""B2 内部冻结接口（单点 import 入口）。

五项冻结接口的规格在 [docs/architecture/interfaces.md](../../docs/architecture/interfaces.md)：
`SubmissionEnvelope`、`StaticAnalysisResult`、`SecurityDecision`、
`run_case(package, submission) -> CaseEvidence`、outbox 发布接口。
`tests/test_frozen_interfaces.py` 以文档为基准断言本包的字段名、字面量别名与方法签名逐字一致。
"""

from __future__ import annotations

from autograder_b2.domain.envelope import (
    MAX_PATH_LENGTH,
    MAX_PATH_SEGMENTS,
    ApiProvenance,
    EmailProvenance,
    EnvelopeFile,
    EnvelopeRejected,
    IntakeProvenance,
    RejectionReason,
    SubmissionEnvelope,
    assert_allowed_relative_path,
    is_allowed_relative_path,
    redact_path_for_message,
)
from autograder_b2.domain.events import (
    B2_PRODUCER,
    DispatchReport,
    DomainEvent,
    OutboxDispatcher,
    OutboxWriter,
)
from autograder_b2.domain.evidence import (
    CaseArtifacts,
    CaseEvidence,
    CaseResource,
    CaseRunner,
    FailureCategory,
    FileChange,
    InternalEvaluationError,
    PackageUnavailableError,
    SandboxUnavailableError,
    SubmissionRunInput,
)
from autograder_b2.domain.security import (
    DetectedIn,
    SecurityCategory,
    SecurityDecision,
    SecurityFinding,
    SecuritySeverity,
    SecurityStatus,
)
from autograder_b2.domain.static_result import (
    CheckOutcome,
    CheckStatus,
    Severity,
    StaticAnalysisResult,
    StaticFinding,
)

__all__ = [
    "B2_PRODUCER",
    "MAX_PATH_LENGTH",
    "MAX_PATH_SEGMENTS",
    "ApiProvenance",
    "CaseArtifacts",
    "CaseEvidence",
    "CaseResource",
    "CaseRunner",
    "CheckOutcome",
    "CheckStatus",
    "DetectedIn",
    "DispatchReport",
    "DomainEvent",
    "EmailProvenance",
    "EnvelopeFile",
    "EnvelopeRejected",
    "FailureCategory",
    "FileChange",
    "IntakeProvenance",
    "InternalEvaluationError",
    "OutboxDispatcher",
    "OutboxWriter",
    "PackageUnavailableError",
    "RejectionReason",
    "SandboxUnavailableError",
    "SecurityCategory",
    "SecurityDecision",
    "SecurityFinding",
    "SecuritySeverity",
    "SecurityStatus",
    "Severity",
    "StaticAnalysisResult",
    "StaticFinding",
    "SubmissionEnvelope",
    "SubmissionRunInput",
    "assert_allowed_relative_path",
    "is_allowed_relative_path",
    "redact_path_for_message",
]
