"""提交接收（HTTP 与 IMAP 两条入口共用同一归一化服务）。

两条入口的分工固定如下，且只有一处落库实现：

- API：`intake.api` → `SubmissionIntakeService.accept` → `persist`；
- 邮件：`intake.imap.EmailIntakeService.process_raw` → `persist`。

两者都经 `intake.service.build_submission_envelope` 产出同一个冻结的 `SubmissionEnvelope`。
"""

from __future__ import annotations

from autograder_b2.intake.attachments import (
    ALLOWED_ATTACHMENT_EXTENSIONS,
    AttachmentRejected,
    EmailIntakeReason,
    RawAttachment,
    parse_message,
    screen_attachments,
)
from autograder_b2.intake.imap import (
    Checkpoint,
    EmailCheckpointStore,
    EmailIntakeService,
    ImapMailboxClient,
    ImapProtocolError,
    MessageOutcome,
    PullReport,
    QuarantineRecord,
    QuarantineStore,
)
from autograder_b2.intake.matching import (
    EmailMatcher,
    MappingEmailMatcher,
    MatchResult,
    ScopeContext,
    SubmissionTarget,
)
from autograder_b2.intake.quarantine import (
    ATTRIBUTABLE_REASON_CODES,
    IntakeQuarantinePage,
    IntakeQuarantineService,
    IntakeQuarantineView,
    InvalidQuarantineCursorError,
    InvalidQuarantineFilterError,
    QuarantineAlreadyResolvedError,
    QuarantineNotAttributableError,
    QuarantineNotFoundError,
    QuarantineRequestError,
    quarantine_page_body,
    quarantine_view_body,
)
from autograder_b2.intake.runtime import IntakeRuntime, build_runtime
from autograder_b2.intake.service import (
    STATE_VALIDATED,
    IntakeLimits,
    IntakeResult,
    SubmissionIntakeService,
    build_submission_envelope,
    submission_digest_of,
)

__all__ = [
    "ALLOWED_ATTACHMENT_EXTENSIONS",
    "ATTRIBUTABLE_REASON_CODES",
    "STATE_VALIDATED",
    "AttachmentRejected",
    "Checkpoint",
    "EmailCheckpointStore",
    "EmailIntakeReason",
    "EmailIntakeService",
    "EmailMatcher",
    "ImapMailboxClient",
    "ImapProtocolError",
    "IntakeLimits",
    "IntakeQuarantinePage",
    "IntakeQuarantineService",
    "IntakeQuarantineView",
    "IntakeResult",
    "IntakeRuntime",
    "InvalidQuarantineCursorError",
    "InvalidQuarantineFilterError",
    "MappingEmailMatcher",
    "MatchResult",
    "MessageOutcome",
    "PullReport",
    "QuarantineAlreadyResolvedError",
    "QuarantineNotAttributableError",
    "QuarantineNotFoundError",
    "QuarantineRecord",
    "QuarantineRequestError",
    "QuarantineStore",
    "RawAttachment",
    "ScopeContext",
    "SubmissionIntakeService",
    "SubmissionTarget",
    "build_runtime",
    "build_submission_envelope",
    "parse_message",
    "quarantine_page_body",
    "quarantine_view_body",
    "screen_attachments",
    "submission_digest_of",
]
