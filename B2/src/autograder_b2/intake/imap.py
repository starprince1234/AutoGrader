"""IMAP 接收适配器：拉邮件 → 附件归一化 → 交给**与 API 完全相同**的校验/存储路径。

分层（每一层只做一件事，便于单独验证）：

| 层 | 职责 |
|---|---|
| `ImapMailboxClient` | 只做 IMAP4rev1 协议交互（stdlib `imaplib`；无第三方依赖、无 shell、无子进程） |
| `EmailCheckpointStore` / `QuarantineStore` | `b2.email_checkpoints` 与 `b2.intake_quarantines` 的读写 |
| `EmailIntakeService` | 把一封邮件变成 1..N 个 `SubmissionEnvelope`，再交给 `SubmissionIntakeService.persist` |

**邮件不再有固定目标/匹配规则**（设计 §1.5、§6.4 第 10 步）：作业归属只来自正文里的一次性
核验码所对应的挑战（`intake.email_verification`）。因此：

1. 没有核验码的邮件 → 隔离 `email_verification_rejected`，detail 说明必须携带课程页面申请的
   一次性核验码（不再有 `MappingEmailMatcher` / `B2_IMAP_MATCHING_JSON` / `B2_IMAP_TARGET_JSON`）。
2. 一封邮件携带 N 个附件 → N 条提交（共用同一封邮件的验证记录），全部走同一个 worker。
3. 附件名映射：每个附件必须是 `N.txt`（N 为作业里的 1 基题号，设计 §1.4），越界/非法名 →
   可解释拒绝并在挑战上记 `attachment_mapping_invalid`（不消费挑战）。
4. 正文必须是五行机器可读格式（`intake.email_body`）：缺项 `email_body_missing_field`；
   作业编号/姓名/学号与挑战、B4 身份目录不符 → `email_body_mismatch`。

硬约束（`docs/architecture/boundaries.md` + 任务书）：

1. **邮件的一切输入都可伪造**：正文与附件原文**从不**落库、**从不**进日志。日志与隔离记录里只
   允许出现 mailbox 标签、UIDVALIDITY/UID、`Message-ID`（它是消息标识符，不是内容）、附件**文件名**
   与字节数、原因码——连发件人地址都不写（超范围 PII）。**唯一的例外是主题头**（迁移 0016）：
   复核列表要靠它让老师认出邮件；入口只保留可打印字符、折叠空白并截断到 200 字
   （`attachments._subject_for_storage`），它不参与匹配、幂等或授权判定，正文/附件仍不落库；
2. **不唯一即隔离、系统故障即重试**：学生侧可解释的拒绝进隔离区；身份目录/作业目录/数据库/
   对象存储不可用 **让本轮拉取失败**（不写 checkpoint、不隔离），下一轮重试——绝不把平台故障
   记成学生格式错误（设计 §1.6、§6.5）；
3. **双键幂等**：`(mailbox, uidvalidity, uid)` 与 `(mailbox, message_id)` 任一命中即跳过；
   `processed` 的 checkpoint 与提交**同事务**写入（存在提交 ⇒ 必存在 checkpoint），
   `quarantined`/`rejected` 的 checkpoint 与隔离记录同事务写入。一封多提交的邮件 processed 时
   `submission_id=NULL`（没有单一提交 id），题目映射在 `email_verification_submissions`；
4. **可恢复的两段式**：`try_consume_received` 先提交"邮件事实"，随后逐题 `persist`；中途失败
   不写 checkpoint，下一轮 resume 既有 verification、按幂等键重放已落库提交并补齐剩余题目，
   全部成功后才写 `processed_at` 与 checkpoint。

**授权从哪里来**：邮件入口没有认证头、也没有调用者身份，因此它**不**走 API 那套 `Principal` 判定。
这里的授权依据是两件服务端事实：能读到这个邮箱（凭据只来自部署配置）＋ 这封邮件携带的核验码
属于发件账号且未过期/未被别的邮件消费。

凭据只从配置来（构造参数，非隐式环境读取）。**绝不**给 `imaplib` 打开 `debug`：它会把发出的
命令原文（含 `LOGIN` 口令）打到 stderr——这是最现实的凭据泄漏路径之一。
"""

from __future__ import annotations

import contextlib
import imaplib
import logging
import re
import ssl
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

import sqlalchemy as sa
from sqlalchemy import Connection, Engine, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from autograder_b2.config import ImapSettings
from autograder_b2.db.schema import (
    email_checkpoints,
    email_verification_submissions,
    intake_messages,
    intake_quarantines,
)
from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.domain.envelope import EmailProvenance
from autograder_b2.intake.attachments import (
    AttachmentRejected,
    EmailIntakeReason,
    EmailMessage,
    ParsedAttachment,
    parse_message,
    screen_attachments,
)
from autograder_b2.intake.email_body import (
    EmailBody,
    missing_fields_detail,
    normalize_assignment_id,
    normalize_person_text,
    parse_email_body,
)
from autograder_b2.intake.email_verification import (
    ATTACHMENT_MAPPING_INVALID,
    EMAIL_BODY_MISMATCH,
    EMAIL_BODY_MISSING_FIELD,
    ChallengeInspection,
    ConsumedVerification,
    EmailVerificationService,
    IntakeStateStore,
    VerificationRejected,
    contains_verification_code,
    extract_challenge_codes,
    redact_challenge_codes,
)
from autograder_b2.intake.matching import MatchingUnavailable, ScopeContext
from autograder_b2.intake.service import (
    IntakeLimits,
    SubmissionIntakeService,
    build_submission_envelope,
)
from autograder_b2.integration.assignments import (
    AssignmentAudience,
    AssignmentDirectory,
    AssignmentUnavailable,
)

logger = logging.getLogger("autograder_b2.intake.email")
#: 安全事件专用 logger：运维可以只订阅它，且它的字段集合被 `tests/test_email_intake.py` 逐项断言。
security_logger = logging.getLogger("autograder_b2.intake.security")

#: checkpoint 状态：与 `ck_email_checkpoints_outcome` 一一对应。
CheckpointStatus = Literal["processed", "quarantined", "rejected"]
#: 单封邮件的处理结论（`skipped` 只表示"这次没做事"，`replayed` 表示命中既有提交的幂等键）。
MessageStatus = Literal["processed", "replayed", "skipped", "quarantined", "rejected", "failed"]

#: 与附件闸门无关、由本模块判定的原因码。
CHECKPOINT_EXISTS = "checkpoint_exists"
FETCH_FAILED = "fetch_failed"
MISSING_MESSAGE_ID = "missing_message_id"
MESSAGE_TOO_LARGE = "message_too_large"
#: 一次性核验码路径的隔离原因码（设计 §6.4；与 `attachments.EmailIntakeReason` 同域）。
EMAIL_VERIFICATION_REJECTED = "email_verification_rejected"
EMAIL_SENDER_MISMATCH = "email_sender_mismatch"
EMAIL_VERIFICATION_EXPIRED = "email_verification_expired"

#: 附件名 → 作业题号（1 基，设计 §1.4/§6.4 第 4 步）：`1.txt`、`2.txt`…… 不按顺序猜。
ORDINAL_ATTACHMENT_PATTERN = re.compile(r"^([1-9][0-9]*)\.txt$")

_SECURITY_EVENT = "email_intake_rejected"
#: checkpoint 的两条唯一约束名（与迁移 0002 的 DDL 一致）：只有它们代表"并发重复拉取"。
CHECKPOINT_UNIQUE_CONSTRAINTS = frozenset(
    {
        "uq_email_checkpoints_mailbox_uidvalidity_uid",
        "uq_email_checkpoints_mailbox_message_id",
    }
)
#: `intake_quarantines.detail` 的数据库 CHECK 上限。
MAX_QUARANTINE_DETAIL = 1000
#: 安全日志的**硬上限**：单个字段与整行都不允许超过它。攻击者可控文本（文件名、`Message-ID`）
#: 虽然已经过转义/截断，这里仍再兜一层——"日志行有多长"不能由外部输入决定。
#: `MAX_LOG_LINE` 约束的是**含前缀的完整日志行**（即 `record.getMessage()` 的长度），
#: 前缀 `_SECURITY_LOG_PREFIX` 计入上限，不留"前缀另算"的模糊地带。
MAX_LOG_FIELD = 200
MAX_LOG_LINE = 800
_SECURITY_LOG_PREFIX = "email_intake_security "


class ImapProtocolError(RuntimeError):
    """IMAP 交互失败（连不上、被拒、响应缺字段）。消息**只含命令名与服务器响应**，不含凭据。"""


# --- 协议客户端 ---------------------------------------------------------------


class ImapMailboxClient:
    """`imaplib` 的最小封装：连接/登录、SELECT、UID SEARCH、UID FETCH、UID STORE、LOGOUT。"""

    def __init__(self, settings: ImapSettings) -> None:
        self._settings = settings
        self._connection: imaplib.IMAP4 | None = None

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()

    def connect(self) -> None:
        """连接并登录。失败信息只带异常类型名，绝不含口令。"""
        settings = self._settings
        try:
            if settings.use_ssl:
                # 显式传入默认上下文（校验证书与主机名）：stdlib 的隐式默认**不**校验证书。
                connection = imaplib.IMAP4_SSL(
                    settings.host,
                    settings.port,
                    ssl_context=ssl.create_default_context(),
                    timeout=settings.timeout_seconds,
                )
            else:
                connection = imaplib.IMAP4(
                    settings.host, settings.port, timeout=settings.timeout_seconds
                )
            connection.login(settings.username, settings.password)
        except (imaplib.IMAP4.error, OSError) as error:
            raise ImapProtocolError(
                f"IMAP login failed for mailbox {settings.mailbox!r}: {type(error).__name__}"
            ) from None
        self._connection = connection

    def select(self) -> int:
        """SELECT 邮箱并返回 UIDVALIDITY；服务器没报就报错——**不猜**默认值（猜错就会丢幂等）。"""
        connection = self._require()
        mailbox = self._settings.mailbox
        typ, data = connection.select(mailbox)
        if typ != "OK":
            raise ImapProtocolError(f"SELECT {mailbox!r} failed: {_first_text(data)}")
        _typ, values = connection.response("UIDVALIDITY")
        raw = values[0] if values else None
        if raw is None:
            raise ImapProtocolError(f"SELECT {mailbox!r} did not report UIDVALIDITY")
        try:
            return int(raw)
        except (TypeError, ValueError) as error:
            raise ImapProtocolError("UIDVALIDITY is not an integer") from error

    def search_all_uids(self) -> tuple[int, ...]:
        """`UID SEARCH ALL`：返回全部 UID（升序，与邮箱顺序一致）。"""
        connection = self._require()
        typ, data = connection.uid("SEARCH", "ALL")
        if typ != "OK":
            raise ImapProtocolError(f"UID SEARCH ALL failed: {_first_text(data)}")
        uids: list[int] = []
        for item in data:
            if not item:
                continue
            for token in bytes(item).split():
                try:
                    uids.append(int(token))
                except ValueError as error:
                    raise ImapProtocolError("UID SEARCH returned a non-numeric UID") from error
        return tuple(sorted(uids))

    def fetch_message(self, uid: int) -> bytes:
        """`UID FETCH <uid> (RFC822)`：取回原始报文并交给解析器。"""
        connection = self._require()
        typ, data = connection.uid("FETCH", str(uid), "(RFC822)")
        if typ != "OK":
            raise ImapProtocolError(f"UID FETCH {uid} failed: {_first_text(data)}")
        payload = _extract_literal(data)
        if payload is None:
            raise ImapProtocolError(f"UID FETCH {uid} returned no message body")
        return payload

    def mark_seen(self, uid: int) -> None:
        """`UID STORE <uid> +FLAGS (\\Seen)`：处理成功后才标已读，便于人工排查。"""
        connection = self._require()
        typ, data = connection.uid("STORE", str(uid), "+FLAGS", r"(\Seen)")
        if typ != "OK":
            raise ImapProtocolError(f"UID STORE {uid} failed: {_first_text(data)}")

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            # 关闭阶段的失败不影响已处理结果；这里只保证不泄漏 socket。
            with contextlib.suppress(OSError):
                connection.shutdown()

    def _require(self) -> imaplib.IMAP4:
        if self._connection is None:
            raise ImapProtocolError("IMAP connection is not open")
        return self._connection


# --- checkpoint 与隔离区 -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Checkpoint:
    status: str
    reason_code: str | None
    submission_id: str | None
    processed_at: datetime


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """隔离记录视图：字段与 `b2.intake_quarantines` 一一对应，**不含**正文/附件原文。"""

    intake_quarantine_id: str
    intake: str
    reason_code: str
    detail: str
    mailbox: str | None
    uidvalidity: int | None
    uid: int | None
    message_id: str | None
    created_at: datetime
    #: 由核验码挑战（服务端签发事实）产生的课程/课序（成对为空或非空）。
    scope_course_id: str | None = None
    scope_section_id: str | None = None
    #: 邮件主题头（有界、无正文）；列表里帮老师认出邮件。核验路径的隔离记录故意不带主题。
    subject: str | None = None


class EmailCheckpointStore:
    """`b2.email_checkpoints` 的读写（双键：UID 三元组 与 `Message-ID`）。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def lookup(
        self, *, mailbox: str, uidvalidity: int, uid: int, message_id: str | None = None
    ) -> Checkpoint | None:
        """两把键**都查**：同一封邮件在 UIDVALIDITY 变化后会换 UID，只查 UID 会漏。"""
        with self._engine.connect() as connection:
            return self._lookup(connection, mailbox, uidvalidity, uid, message_id)

    def record(
        self,
        connection: Connection,
        *,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        status: CheckpointStatus,
        reason_code: str | None,
        submission_id: str | None,
        now: datetime,
    ) -> None:
        """在**调用方的事务**内写 checkpoint。

        唯一约束冲突（并发重复拉取）由调用方捕获 `IntegrityError` 并按"已处理"跳过——
        唯一约束正是幂等的实现手段，不是崩溃。一封多提交的邮件 `submission_id=None`。
        """
        connection.execute(
            sa.insert(email_checkpoints).values(
                email_checkpoint_id=uuid.uuid4(),
                mailbox=mailbox,
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                status=status,
                reason_code=reason_code,
                submission_id=uuid.UUID(submission_id) if submission_id else None,
                processed_at=now,
            )
        )

    def mark_processed(
        self,
        connection: Connection,
        *,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        submission_id: str | None,
        now: datetime,
    ) -> None:
        """把已存在的 `quarantined` checkpoint 改成 `processed`（人工复核后的闭环）。

        必须在**提交持久化的同一事务**里调用：checkpoint 与提交要么一起成功，要么一起回滚。
        """
        result = connection.execute(
            sa.update(email_checkpoints)
            .where(
                sa.and_(
                    email_checkpoints.c.mailbox == mailbox,
                    email_checkpoints.c.uidvalidity == uidvalidity,
                    email_checkpoints.c.uid == uid,
                )
            )
            .values(
                status="processed",
                reason_code=None,
                submission_id=uuid.UUID(submission_id) if submission_id else None,
                processed_at=now,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("the quarantine checkpoint disappeared during completion")

    @staticmethod
    def _lookup(
        connection: Connection,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
    ) -> Checkpoint | None:
        keys = [
            sa.and_(email_checkpoints.c.uidvalidity == uidvalidity, email_checkpoints.c.uid == uid)
        ]
        if message_id is not None:
            keys.append(email_checkpoints.c.message_id == message_id)
        row = connection.execute(
            sa.select(
                email_checkpoints.c.status,
                email_checkpoints.c.reason_code,
                email_checkpoints.c.submission_id,
                email_checkpoints.c.processed_at,
            )
            .where(sa.and_(email_checkpoints.c.mailbox == mailbox, sa.or_(*keys)))
            .order_by(email_checkpoints.c.processed_at)
            .limit(1)
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(
            status=str(row[0]),
            reason_code=None if row[1] is None else str(row[1]),
            submission_id=None if row[2] is None else str(row[2]),
            processed_at=row[3],
        )


class QuarantineStore:
    """`b2.intake_quarantines` 的读写：无法唯一匹配或被拒的来件在这里**可查询**。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(
        self,
        connection: Connection,
        *,
        reason_code: str,
        detail: str,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        now: datetime,
        scope: ScopeContext | None = None,
        intake: str = "email",
        subject: str | None = None,
    ) -> str:
        """写一条隔离记录（在调用方事务内）。`detail` 已在调用方脱敏，这里只兜长度上限。

        `scope` 是**核验码挑战**（服务端签发事实）给出的课程/课序；`None` 表示这条来件
        没有任何可验证的课程/课序关联，之后只对平台管理员可见。两列由数据库 CHECK 约束成对。
        """
        identifier = uuid.uuid4()
        connection.execute(
            sa.insert(intake_quarantines).values(
                intake_quarantine_id=identifier,
                intake=intake,
                reason_code=reason_code,
                detail=_bounded_detail(detail, reason_code),
                mailbox=mailbox,
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                created_at=now,
                scope_course_id=None if scope is None else uuid.UUID(scope.course_id),
                scope_section_id=None if scope is None else uuid.UUID(scope.section_id),
                subject=subject,
            )
        )
        return str(identifier)

    def list_recent(
        self, *, mailbox: str | None = None, limit: int = 50
    ) -> tuple[QuarantineRecord, ...]:
        """按时间倒序查询隔离记录（运维查询入口；只有标识、原因码、脱敏明细与 scope）。"""
        statement = sa.select(
            intake_quarantines.c.intake_quarantine_id,
            intake_quarantines.c.intake,
            intake_quarantines.c.reason_code,
            intake_quarantines.c.detail,
            intake_quarantines.c.mailbox,
            intake_quarantines.c.uidvalidity,
            intake_quarantines.c.uid,
            intake_quarantines.c.message_id,
            intake_quarantines.c.created_at,
            intake_quarantines.c.scope_course_id,
            intake_quarantines.c.scope_section_id,
            intake_quarantines.c.subject,
        ).order_by(intake_quarantines.c.created_at.desc())
        if mailbox is not None:
            statement = statement.where(intake_quarantines.c.mailbox == mailbox)
        with self._engine.connect() as connection:
            rows = connection.execute(statement.limit(limit)).fetchall()
        return tuple(
            QuarantineRecord(
                intake_quarantine_id=str(row[0]),
                intake=str(row[1]),
                reason_code=str(row[2]),
                detail=str(row[3]),
                mailbox=None if row[4] is None else str(row[4]),
                uidvalidity=None if row[5] is None else int(row[5]),
                uid=None if row[6] is None else int(row[6]),
                message_id=None if row[7] is None else str(row[7]),
                created_at=row[8],
                scope_course_id=None if row[9] is None else str(row[9]),
                scope_section_id=None if row[10] is None else str(row[10]),
                subject=None if row[11] is None else str(row[11]),
            )
            for row in rows
        )


# --- 拉取与归一化 -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageOutcome:
    """单封邮件的结论（进 `PullReport`；`detail` 已脱敏）。

    `submission_id` 对一封多提交的邮件取**第一条**提交（只用于报告/日志的可操作性提示；
    库里的 checkpoint 不存单一提交 id）。
    """

    uid: int
    status: MessageStatus
    reason_code: str | None
    submission_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class PullReport:
    """一次拉取的报告：`fetched` 是真正下载的封数，其余按结论分类计数。"""

    mailbox: str
    uidvalidity: int
    fetched: int
    outcomes: tuple[MessageOutcome, ...]

    def count(self, status: MessageStatus) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)


@dataclass(frozen=True, slots=True)
class _MappedAttachment:
    """一个已通过安全闸门并映射到作业题号的附件（`filename` 恒为 `N.txt`）。"""

    ordinal: int
    question_version_id: str
    attachment_name: str
    content: bytes


class EmailIntakeService:
    """邮件入口：拉取 + 归一化 + 幂等 + 隔离。业务校验/存储全部委托给 `SubmissionIntakeService`。

    `verification` 是一次性核验码消费者（设计 §6）：装配后，正文携带核验码的邮件走**核验路径**
    （挑战所有者 == 发件账号 → 同一条 `persist` 创建/重放每道题的提交）；未装配时所有邮件都被
    可解释地隔离（没有核验码就无法推出作业归属）。无论是否装配，每轮拉取都会写
    `b2.intake_state` 心跳：那是评测判定"收件依赖是否可用"的只读事实。
    """

    def __init__(
        self,
        *,
        engine: Engine,
        settings: ImapSettings,
        intake: SubmissionIntakeService,
        checkpoints: EmailCheckpointStore,
        quarantines: QuarantineStore,
        client_factory: type[ImapMailboxClient] = ImapMailboxClient,
        verification: EmailVerificationService | None = None,
        assignment_directory: AssignmentDirectory | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._intake = intake
        self._checkpoints = checkpoints
        self._quarantines = quarantines
        self._client_factory = client_factory
        self._verification = verification
        self._assignment_directory = (
            assignment_directory
            if assignment_directory is not None
            else (None if verification is None else verification.assignment_directory)
        )
        self._intake_state = IntakeStateStore(engine)

    @property
    def limits(self) -> IntakeLimits:
        return self._intake.limits

    @property
    def settings(self) -> ImapSettings:
        return self._settings

    @property
    def quarantines(self) -> QuarantineStore:
        return self._quarantines

    @property
    def checkpoints(self) -> EmailCheckpointStore:
        return self._checkpoints

    @property
    def verification(self) -> EmailVerificationService | None:
        return self._verification

    @property
    def assignment_directory(self) -> AssignmentDirectory | None:
        return self._assignment_directory

    # --- 拉取 ------------------------------------------------------------------

    def pull(self) -> PullReport:
        """连一次邮箱，按 UID 升序处理到上限为止。返回逐封结论（可断言的凭据）。

        心跳语义：一轮**成功完成**才刷新 `last_successful_pull_at`；失败轮只往前写
        `last_error_code`，成功时间保持不变。心跳写入失败不改变拉取结论（它只会让评测判
        `infrastructure_error`——方向是安全的，绝不会把一个学生误判成答错）。
        """
        try:
            report = self._pull_once()
        except Exception as error:
            self._record_pull_heartbeat(_pull_failure_code(error))
            raise
        self._record_pull_heartbeat(None)
        return report

    def _pull_once(self) -> PullReport:
        """执行一次真实拉取；异常原样上抛给 `pull` 记心跳。"""
        settings = self._settings
        outcomes: list[MessageOutcome] = []
        fetched = 0
        with self._client_factory(settings) as client:
            uidvalidity = client.select()
            uids = client.search_all_uids()
            if len(uids) > settings.max_messages_per_pull:
                logger.warning(
                    "email_intake mailbox=%s pending=%d processing=%d",
                    settings.mailbox,
                    len(uids),
                    settings.max_messages_per_pull,
                )
            for uid in uids[: settings.max_messages_per_pull]:
                checkpoint = self._checkpoints.lookup(
                    mailbox=settings.mailbox, uidvalidity=uidvalidity, uid=uid
                )
                if checkpoint is not None:
                    outcomes.append(
                        MessageOutcome(
                            uid=uid,
                            status="skipped",
                            reason_code=CHECKPOINT_EXISTS,
                            submission_id=checkpoint.submission_id,
                            detail=f"checkpoint status={checkpoint.status}",
                        )
                    )
                    continue
                fetched += 1
                try:
                    raw = client.fetch_message(uid)
                except ImapProtocolError as error:
                    outcomes.append(
                        MessageOutcome(
                            uid=uid,
                            status="failed",
                            reason_code=FETCH_FAILED,
                            submission_id=None,
                            detail=str(error),
                        )
                    )
                    continue
                outcome = self.process_raw(uidvalidity=uidvalidity, uid=uid, raw=raw)
                if outcome.status in {"processed", "replayed"}:
                    try:
                        client.mark_seen(uid)
                    except ImapProtocolError as error:
                        # 标记已读失败不影响已完成的提交：checkpoint 已经落库，不会重复处理。
                        logger.warning(
                            "email_intake mark_seen_failed mailbox=%s uid=%d detail=%s",
                            settings.mailbox,
                            uid,
                            error,
                        )
                outcomes.append(outcome)
        return PullReport(
            mailbox=settings.mailbox,
            uidvalidity=uidvalidity,
            fetched=fetched,
            outcomes=tuple(outcomes),
        )

    # --- 人工完成（隔离复核） ---------------------------------------------------

    def fetch_raw(self, *, uidvalidity: int, uid: int) -> bytes:
        """按隔离记录里的邮箱位置取回原报文；UIDVALIDITY 不一致即拒绝（UID 已不可信）。"""
        with self._client_factory(self._settings) as client:
            current = client.select()
            if current != uidvalidity:
                raise ImapProtocolError(
                    "mailbox UIDVALIDITY changed; the original message can no longer be located"
                )
            return client.fetch_message(uid)

    def ingest_raw_for_assignment(
        self,
        *,
        raw: bytes,
        student_id: str,
        course_id: str,
        section_id: str,
        assignment_id: str,
        audience: AssignmentAudience,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
    ) -> tuple[str, ...]:
        """把人工选定身份的原始邮件按附件题号映射逐题创建提交（与自动路径同一 persist）。

        返回生成的提交 id（按题号升序）；附件不合规抛 `AttachmentRejected`（调用方映射 422）。
        **不**写 verification/映射表：人工路径没有核验码挑战，本就不存在"这封邮件属于哪个
        挑战"的事实；它只负责"把原邮件补成提交"。
        """
        message = parse_message(raw, max_attachments=self.limits.max_files)
        if message_id is not None and message.message_id != message_id:
            raise AttachmentRejected(
                "message_mismatch",
                "the message at this mailbox position is not the recorded message",
            )
        accepted = screen_attachments(message, limits=self.limits)
        mapped = _mapped_attachments(accepted, audience)
        submission_ids: list[str] = []
        for item in mapped:
            files = ((item.attachment_name, item.content),)
            envelope = build_submission_envelope(
                submission_id=str(uuid.uuid4()),
                assignment_id=assignment_id,
                student_id=student_id,
                course_id=course_id,
                section_id=section_id,
                question_version_id=item.question_version_id,
                idempotency_key=email_question_idempotency_key(
                    mailbox=mailbox,
                    message_id=message.message_id,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    question_version_id=item.question_version_id,
                ),
                intake="email",
                provenance=EmailProvenance(
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    message_id=message_id or message.message_id or "",
                ),
                correlation_id=str(uuid.uuid4()),
                files=files,
            )
            result = self._intake.persist(
                envelope,
                files,
                on_persist=lambda connection, created: _persist_manual_message(
                    connection,
                    created=created,
                    message=message,
                    checkpoints=self._checkpoints,
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                ),
            )
            submission_ids.append(result.submission_id)
        return tuple(submission_ids)

    def mark_checkpoint_processed(
        self,
        connection: Connection,
        *,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        submission_id: str | None,
        now: datetime,
    ) -> None:
        self._checkpoints.mark_processed(
            connection,
            mailbox=mailbox,
            uidvalidity=uidvalidity,
            uid=uid,
            submission_id=submission_id,
            now=now,
        )

    # --- 归一化 ----------------------------------------------------------------

    def process_raw(self, *, uidvalidity: int, uid: int, raw: bytes) -> MessageOutcome:
        """把一封原始报文处理到底：大小闸门 → 解析 → 安全闸门 → 核验码路径 → 同一段持久化。

        这是"邮件路径与 API 路径一致"的执行点：本方法**不**写提交、**不**写文件、**不**发事件，
        只构造 `SubmissionEnvelope` 并调用与 API 共用的 `persist`。

        **大小闸门在解析之前**，判据是 `len(raw)`（真实字节长度，不看任何外部声明）。
        超限即 `rejected / message_too_large`：进隔离区 + 安全日志，**不截断、不解析**。
        """
        mailbox = self._settings.mailbox
        limit = self._settings.max_message_bytes
        if len(raw) > limit:
            return self._reject(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=None,
                rejection=AttachmentRejected(
                    MESSAGE_TOO_LARGE,
                    f"message is {len(raw)} bytes, above the {limit} byte limit",
                ),
            )
        try:
            message = parse_message(raw, max_attachments=self.limits.max_files)
        except AttachmentRejected as rejected:
            return self._reject(
                uidvalidity=uidvalidity, uid=uid, message_id=None, rejection=rejected
            )

        message_id = message.message_id
        if message_id is None:
            # 缺 `Message-ID` 就没有第二把幂等钥匙：UIDVALIDITY 一变，同一封邮件会被当成新邮件。
            return self._reject(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=None,
                rejection=AttachmentRejected(
                    MISSING_MESSAGE_ID,
                    "the message carries no Message-ID header;"
                    " the double-key checkpoint cannot be formed",
                ),
                subject=message.subject,
            )

        existing = self._checkpoints.lookup(
            mailbox=mailbox, uidvalidity=uidvalidity, uid=uid, message_id=message_id
        )
        if existing is not None:
            return MessageOutcome(
                uid=uid,
                status="skipped",
                reason_code=CHECKPOINT_EXISTS,
                submission_id=existing.submission_id,
                detail=f"checkpoint status={existing.status}",
            )

        try:
            accepted = screen_attachments(message, limits=self.limits)
        except AttachmentRejected as rejected:
            return self._reject(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                rejection=rejected,
                subject=message.subject,
            )

        # 设计 §6.4 第 10 步：没有核验码的邮件不再有"固定目标/匹配规则"可路由，可解释地隔离。
        if self._verification is None or not contains_verification_code(message.body_excerpt):
            return self._quarantine(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                reason_code=EMAIL_VERIFICATION_REJECTED,
                detail=(
                    "the message carries no one-time verification code; request one from the"
                    " assignment workspace and put it in the message body"
                ),
                scope=None,
            )
        return self._process_verification(
            uidvalidity=uidvalidity, uid=uid, message=message, accepted=accepted
        )

    # --- 一次性核验码路径 -------------------------------------------------------

    def _process_verification(
        self,
        *,
        uidvalidity: int,
        uid: int,
        message: EmailMessage,
        accepted: tuple[ParsedAttachment, ...],
    ) -> MessageOutcome:
        """正文携带核验码的邮件：证据链只认"挑战所有者 == 发件账号"。

        顺序是有意的：**附件安全闸门先跑**（附件不合格的邮件不应消费任何挑战）；发件账号解析
        不可用时**本轮拉取失败重试**（绝不把故障记成隔离）；正文/映射校验不通过时在挑战上记
        `last_rejection_code` 并隔离、**不消费挑战**（学生改好后可用同一码重发）。
        """
        verification = self._verification
        assert verification is not None  # 调用方已判空
        mailbox = self._settings.mailbox
        message_id = message.message_id
        assert message_id is not None  # process_raw 已在缺 Message-ID 时拒绝

        # 查不了（令牌错误/服务不可达）不是"没这个账号"：让本轮拉取失败，下次重试。
        account = verification.resolve_sender(message.sender)
        if account is None:
            return self._quarantine(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                reason_code=EMAIL_VERIFICATION_REJECTED,
                detail="the sender address is not registered to any account",
                scope=None,
            )

        inspection = verification.inspect_challenge(
            extract_challenge_codes(message.body_excerpt), matched_account=account
        )
        if isinstance(inspection, VerificationRejected):
            return self._quarantine_from_rejection(
                uidvalidity=uidvalidity, uid=uid, message_id=message_id, rejected=inspection
            )
        scope = ScopeContext(
            course_id=inspection.course_id, section_id=inspection.section_id
        )

        # 已消费的挑战：先判"幂等 resume vs 另一封邮件"。resume 是重试路径，后续校验失败时
        # **不**改写已收到挑战的状态（record_rejection 只写未消费行）。
        resumed: ConsumedVerification | None = None
        if inspection.consumed_at is not None:
            consumed = self._consume(
                verification, message=message, mailbox=mailbox,
                uidvalidity=uidvalidity, uid=uid, account=account, accepted=accepted,
            )
            if isinstance(consumed, VerificationRejected):
                return self._quarantine_from_rejection(
                    uidvalidity=uidvalidity, uid=uid, message_id=message_id, rejected=consumed
                )
            resumed = consumed
        elif inspection.expired:
            # 过期不是"格式错误"：走同一条消费判定，它会记录 expired 原因并拒绝。
            consumed = self._consume(
                verification, message=message, mailbox=mailbox,
                uidvalidity=uidvalidity, uid=uid, account=account, accepted=accepted,
            )
            if isinstance(consumed, VerificationRejected):
                return self._quarantine_from_rejection(
                    uidvalidity=uidvalidity, uid=uid, message_id=message_id, rejected=consumed
                )
            resumed = consumed

        problem = _body_problem(
            parse_email_body(message.body_excerpt), inspection=inspection, account=account
        )
        if problem is not None:
            reason_code, detail = problem
            verification.record_rejection(inspection.challenge_id, reason_code)
            return self._quarantine(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                reason_code=reason_code,
                detail=detail,
                scope=scope,
            )

        audience = self._load_audience(inspection)
        if isinstance(audience, VerificationRejected):
            verification.record_rejection(inspection.challenge_id, audience.reason_code)
            return self._quarantine_from_rejection(
                uidvalidity=uidvalidity, uid=uid, message_id=message_id, rejected=audience
            )
        try:
            mapped = _mapped_attachments(accepted, audience)
        except AttachmentRejected as rejected:
            verification.record_rejection(inspection.challenge_id, ATTACHMENT_MAPPING_INVALID)
            return self._quarantine(
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                reason_code=ATTACHMENT_MAPPING_INVALID,
                detail=rejected.detail,
                scope=scope,
            )

        if resumed is None:
            consumed = self._consume(
                verification, message=message, mailbox=mailbox,
                uidvalidity=uidvalidity, uid=uid, account=account, accepted=accepted,
            )
            if isinstance(consumed, VerificationRejected):
                return self._quarantine_from_rejection(
                    uidvalidity=uidvalidity, uid=uid, message_id=message_id, rejected=consumed
                )
            resumed = consumed
        return self._persist_submissions(
            consumed=resumed,
            mapped=mapped,
            message=message,
            uidvalidity=uidvalidity,
            uid=uid,
        )

    def _consume(
        self,
        verification: EmailVerificationService,
        *,
        message: EmailMessage,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        account: object,
        accepted: tuple[ParsedAttachment, ...],
    ) -> ConsumedVerification | VerificationRejected:
        return verification.try_consume_received(
            message=message,
            mailbox=mailbox,
            uidvalidity=uidvalidity,
            uid=uid,
            sender=message.sender,
            matched_account=account,  # type: ignore[arg-type] - MatchedAccount
            attachment_names=tuple(item.filename for item in accepted),
        )

    def _load_audience(
        self, inspection: ChallengeInspection
    ) -> AssignmentAudience | VerificationRejected:
        """取作业受众；目录不可用 `AssignmentUnavailable` 原样上抛 → 本轮拉取失败重试。"""
        directory = self._assignment_directory
        if directory is None:
            raise AssignmentUnavailable("assignment directory is not configured")
        audience = directory.get(inspection.assignment_id)
        if audience is None:
            return VerificationRejected(
                "email_verification_rejected",
                "the assignment this code was issued for no longer exists",
                course_id=inspection.course_id,
                section_id=inspection.section_id,
            )
        if not audience.open:
            return VerificationRejected(
                "email_verification_rejected",
                "the assignment this code was issued for is no longer open",
                course_id=inspection.course_id,
                section_id=inspection.section_id,
            )
        if audience.course_id != inspection.course_id or not audience.is_target(
            section_id=inspection.section_id, student_id=inspection.student_id
        ):
            return VerificationRejected(
                "email_verification_rejected",
                "the sender is no longer in the assignment audience",
                course_id=inspection.course_id,
                section_id=inspection.section_id,
            )
        return audience

    def _persist_submissions(
        self,
        *,
        consumed: ConsumedVerification,
        mapped: tuple[_MappedAttachment, ...],
        message: EmailMessage,
        uidvalidity: int,
        uid: int,
    ) -> MessageOutcome:
        """逐题走同一个 `persist`；全部成功后才写 `processed_at` 与 processed checkpoint。

        任一步失败**不写 checkpoint**（异常原样上抛，本轮拉取失败）：下一轮 resume 既有
        verification，已落库的题目按幂等键重放、缺失的补齐，不产生重复提交。
        """
        mailbox = self._settings.mailbox
        message_id = message.message_id
        assert message_id is not None
        submission_ids: list[str] = []
        replayed: list[bool] = []
        for item in mapped:
            files = ((item.attachment_name, item.content),)
            envelope = build_submission_envelope(
                submission_id=str(uuid.uuid4()),
                assignment_id=consumed.assignment_id,
                student_id=consumed.student_id,
                course_id=consumed.course_id,
                section_id=consumed.section_id,
                question_version_id=item.question_version_id,
                idempotency_key=email_question_idempotency_key(
                    mailbox=mailbox,
                    message_id=message.message_id,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    question_version_id=item.question_version_id,
                ),
                intake="email",
                provenance=EmailProvenance(
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    message_id=message_id,
                ),
                correlation_id=str(uuid.uuid4()),
                files=files,
            )
            result = self._intake.persist(
                envelope,
                files,
                on_persist=lambda connection, created, item=item: (
                    _persist_verification_message(
                        connection,
                        created=created,
                        message=message,
                        verification_id=consumed.verification_id,
                        attachment_name=item.attachment_name,
                        question_version_id=item.question_version_id,
                    )
                ),
            )
            if result.idempotent_replay:
                # 重放不会调用 `on_persist`：补齐"提交 ⇒ 映射"这半条事实（幂等 INSERT）。
                self._ensure_mapping(consumed.verification_id, item, result.submission_id)
            submission_ids.append(result.submission_id)
            replayed.append(result.idempotent_replay)

        self._finish_verification(
            verification_id=consumed.verification_id,
            mailbox=mailbox,
            uidvalidity=uidvalidity,
            uid=uid,
            message_id=message_id,
        )
        status: MessageStatus = "replayed" if replayed and all(replayed) else "processed"
        logger.info(
            "email_intake verification_processed mailbox=%s uidvalidity=%d uid=%d"
            " message_id=%s submissions=%d",
            mailbox,
            uidvalidity,
            uid,
            message_id,
            len(submission_ids),
        )
        return MessageOutcome(
            uid=uid,
            status=status,
            reason_code=None,
            submission_id=submission_ids[0] if submission_ids else None,
            detail=(
                f"verification consumed; {len(submission_ids)} per-question submission(s)"
                " created through the shared intake service"
            ),
        )

    def _ensure_mapping(
        self, verification_id: str, item: _MappedAttachment, submission_id: str
    ) -> None:
        """幂等重放时补齐 `email_verification_submissions`（与其它提交事务独立）。"""
        with self._engine.begin() as connection:
            _insert_mapping(
                connection,
                verification_id=verification_id,
                question_version_id=item.question_version_id,
                submission_id=submission_id,
                attachment_name=item.attachment_name,
            )

    def _finish_verification(
        self,
        *,
        verification_id: str,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        message_id: str,
    ) -> None:
        """全部题目提交成功：同一事务写 `processed_at` 与 processed checkpoint。

        并发重复拉取导致 checkpoint 唯一约束冲突时，提交事实已完整，只需补写 `processed_at`
        （另一个进程已写了 checkpoint）——不把并发当失败。
        """
        verification = self._verification
        assert verification is not None
        now = utc_now()
        try:
            with self._engine.begin() as connection:
                verification.mark_processed(connection, verification_id, now)
                self._checkpoints.record(
                    connection,
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    message_id=message_id,
                    status="processed",
                    reason_code=None,
                    submission_id=None,
                    now=now,
                )
        except IntegrityError as error:
            if not _is_checkpoint_conflict(error):
                raise
            with self._engine.begin() as connection:
                verification.mark_processed(connection, verification_id, now)

    def _record_pull_heartbeat(self, error_code: str | None) -> None:
        """写收件心跳（成功=刷新成功时间；失败=只记原因码）。写失败只记日志，不改变拉取结论。"""
        try:
            if error_code is None:
                self._intake_state.record_pull_success()
            else:
                self._intake_state.record_pull_failure(error_code)
        except SQLAlchemyError:
            # 心跳写不进去只可能是库故障；本轮结论已经确定，把"心跳旧了"留给评测侧判基础设施错误。
            logger.warning(
                "email_intake heartbeat_write_failed mailbox=%s", self._settings.mailbox
            )

    # --- 内部 ------------------------------------------------------------------

    def _quarantine_from_rejection(
        self,
        *,
        uidvalidity: int,
        uid: int,
        message_id: str,
        rejected: VerificationRejected,
    ) -> MessageOutcome:
        scope = (
            ScopeContext(course_id=rejected.course_id, section_id=rejected.section_id)
            if rejected.course_id is not None and rejected.section_id is not None
            else None
        )
        return self._quarantine(
            uidvalidity=uidvalidity,
            uid=uid,
            message_id=message_id,
            reason_code=rejected.reason_code,
            detail=rejected.detail,
            scope=scope,
        )

    def _reject(
        self,
        *,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        rejection: AttachmentRejected,
        subject: str | None = None,
    ) -> MessageOutcome:
        """安全/结构拒绝：隔离记录 + checkpoint 同事务，然后记一条结构化安全日志。"""
        outcome = self._record_outcome(
            status="rejected",
            uidvalidity=uidvalidity,
            uid=uid,
            message_id=message_id,
            reason_code=rejection.reason,
            detail=rejection.detail,
            subject=subject,
        )
        if outcome.status == "rejected":
            # 日志有意放在事务**之后**：日志说"已拒绝"时，隔离记录一定已经落库。
            self._log_security_event(
                reason_code=rejection.reason,
                uidvalidity=uidvalidity,
                uid=uid,
                message_id=message_id,
                attachment_name=rejection.attachment_name,
                size_bytes=rejection.size_bytes,
                detail=rejection.detail,
            )
        return outcome

    def _quarantine(
        self,
        *,
        uidvalidity: int,
        uid: int,
        message_id: str,
        reason_code: EmailIntakeReason,
        detail: str,
        scope: ScopeContext | None,
        subject: str | None = None,
    ) -> MessageOutcome:
        """无法唯一匹配：进隔离区、不产生提交、也**不因此**产生任何评测。"""
        outcome = self._record_outcome(
            status="quarantined",
            uidvalidity=uidvalidity,
            uid=uid,
            message_id=message_id,
            reason_code=reason_code,
            detail=detail,
            scope=scope,
            subject=subject,
        )
        if outcome.status == "quarantined":
            logger.info(
                "email_intake quarantined mailbox=%s uidvalidity=%d uid=%d message_id=%s reason=%s",
                self._settings.mailbox,
                uidvalidity,
                uid,
                message_id,
                reason_code,
            )
        return outcome

    def _record_outcome(
        self,
        *,
        status: Literal["quarantined", "rejected"],
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        reason_code: str,
        detail: str,
        scope: ScopeContext | None = None,
        subject: str | None = None,
    ) -> MessageOutcome:
        mailbox = self._settings.mailbox
        now = utc_now()
        try:
            with self._engine.begin() as connection:
                self._quarantines.record(
                    connection,
                    reason_code=reason_code,
                    detail=detail,
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    message_id=message_id,
                    now=now,
                    scope=scope,
                    subject=subject,
                )
                self._checkpoints.record(
                    connection,
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    message_id=message_id,
                    status=status,
                    reason_code=reason_code,
                    submission_id=None,
                    now=now,
                )
        except IntegrityError as error:
            # 同一 UID/Message-ID 已被并发处理：隔离记录与 checkpoint 一起回滚（不留半条），
            # 本次按"已处理"跳过。**只对 checkpoint 唯一约束**这样解释（见 `_is_checkpoint_conflict`）。
            if not _is_checkpoint_conflict(error):
                raise
            return self._already_recorded(uid=uid)
        return MessageOutcome(
            uid=uid,
            status=status,
            reason_code=reason_code,
            submission_id=None,
            detail=detail,
        )

    @staticmethod
    def _already_recorded(*, uid: int) -> MessageOutcome:
        return MessageOutcome(
            uid=uid,
            status="skipped",
            reason_code=CHECKPOINT_EXISTS,
            submission_id=None,
            detail="a concurrent pull recorded this message first",
        )

    def _log_security_event(
        self,
        *,
        reason_code: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        attachment_name: str | None,
        size_bytes: int | None,
        detail: str,
    ) -> None:
        """结构化安全日志：字段集合固定，只含标识、文件名、字节数与原因码，且**长度有上限**。

        上限有两层：每个字段先按 `MAX_LOG_FIELD` 截断（字符串字段），整行再按 `MAX_LOG_LINE`
        截断——**前缀计入**整行上限，因此 `record.getMessage()` 的长度恒 ≤ `MAX_LOG_LINE`。
        `extra` 里存的是**同样已截断**的值，因此结构化消费者与文本消费者看到的一样多。
        """
        fields: dict[str, object] = {
            "event": _SECURITY_EVENT,
            "class": _security_class(reason_code),
            "reason_code": reason_code,
            "mailbox": self._settings.mailbox,
            "uidvalidity": uidvalidity,
            "uid": uid,
            "message_id": _bounded_log_text(message_id),
            "attachment_name": _bounded_log_text(attachment_name),
            "size_bytes": size_bytes,
            "detail": _bounded_log_text(detail),
        }
        rendered = _SECURITY_LOG_PREFIX + " ".join(
            f"{name}={value}" for name, value in fields.items()
        )
        security_logger.warning("%s", rendered[:MAX_LOG_LINE], extra={"b2_security_event": fields})


def email_idempotency_key(*, mailbox: str, message_id: str, uidvalidity: int, uid: int) -> str:
    """由邮件身份派生幂等键（≤200 字符，满足 `ck_submissions_idempotency_key`）。

    用 `Message-ID` 的**摘要**而不是原文：幂等键会进 `submissions` 行与日志，而邮件头长度不可控；
    摘要保留了唯一需要的性质——"同一封邮件 ⇒ 同一个键"。
    """
    if message_id:
        return f"email:{mailbox}:{sha256_hex(message_id.encode('utf-8'))[:32]}"
    return f"email:{mailbox}:{uidvalidity}:{uid}"


def email_question_idempotency_key(
    *, mailbox: str, message_id: str | None, uidvalidity: int, uid: int, question_version_id: str
) -> str:
    """一题一个幂等键：邮件身份 + 题目版本（一封 N 附件的邮件 → N 条互不相同的提交）。"""
    base = email_idempotency_key(
        mailbox=mailbox, message_id=message_id or "", uidvalidity=uidvalidity, uid=uid
    )
    return f"{base}:{question_version_id}"


# --- 纯校验工具 ---------------------------------------------------------------


def _body_problem(
    body: EmailBody,
    *,
    inspection: ChallengeInspection,
    account: object,
) -> tuple[str, str] | None:
    """正文五行的机器校验；返回 `(原因码, 已脱敏明细)`，通过返回 `None`。

    - 缺项 → `email_body_missing_field`；
    - `作业编号` 不是 UUID 或不等于挑战绑定的作业 → `email_body_mismatch`；
    - `姓名`/`学号` 与 B4 身份目录的 `display_name`/`student_number` 不一致
      （去首尾空白、折叠内部空白后精确比较）→ `email_body_mismatch`；
    - `课序` 只要求非空（已在缺项里判定）：B2 无法验证展示名，身份由核验码 + 账号事实证明。

    明细只出现字段键名与固定说明，**绝不回显正文取值**（那可能含 PII）。
    """
    if body.missing:
        return EMAIL_BODY_MISSING_FIELD, missing_fields_detail(body)
    declared_assignment = normalize_assignment_id(body.assignment_id)
    if declared_assignment is None:
        return EMAIL_BODY_MISMATCH, "the declared 作业编号 is not a UUID"
    if declared_assignment != inspection.assignment_id:
        return (
            EMAIL_BODY_MISMATCH,
            "the declared 作业编号 does not match the assignment this code was issued for",
        )
    display_name = getattr(account, "display_name", None)
    student_number = getattr(account, "student_number", None)
    if (
        display_name is None
        or normalize_person_text(body.name) != normalize_person_text(str(display_name))
    ):
        return (
            EMAIL_BODY_MISMATCH,
            "the declared 姓名 does not match the account's registered display name",
        )
    if (
        student_number is None
        or normalize_person_text(body.student_number)
        != normalize_person_text(str(student_number))
    ):
        return (
            EMAIL_BODY_MISMATCH,
            "the declared 学号 does not match the account's registered student number",
        )
    return None


def _mapped_attachments(
    accepted: Sequence[ParsedAttachment], audience: AssignmentAudience
) -> tuple[_MappedAttachment, ...]:
    """附件名 → 作业题号（`^([1-9][0-9]*)\\.txt$`，1 基；越界/非法名即整封拒绝）。

    只认文件名，不按附件顺序猜（设计 §1.4）。重复题号在同一批里不可能出现（闸门已按
    大小写不敏感判重），这里仍再判一次，避免将来闸门改动后静默映射两次。
    """
    mapped: list[_MappedAttachment] = []
    seen: set[int] = set()
    total = len(audience.question_version_ids)
    for attachment in accepted:
        match = ORDINAL_ATTACHMENT_PATTERN.fullmatch(attachment.filename)
        if match is None:
            raise AttachmentRejected(
                ATTACHMENT_MAPPING_INVALID,
                f"attachment {_shown(attachment.filename)} does not map to a question;"
                " each answer file must be named <question number>.txt (1.txt, 2.txt, ...)",
                attachment_name=_shown(attachment.filename),
            )
        ordinal = int(match.group(1))
        question_version_id = audience.question_at(ordinal)
        if question_version_id is None:
            raise AttachmentRejected(
                ATTACHMENT_MAPPING_INVALID,
                f"attachment {_shown(attachment.filename)} names question {ordinal},"
                f" but the assignment has {total} question(s)",
                attachment_name=_shown(attachment.filename),
            )
        if ordinal in seen:
            raise AttachmentRejected(
                ATTACHMENT_MAPPING_INVALID,
                f"attachment {_shown(attachment.filename)} repeats question {ordinal}",
                attachment_name=_shown(attachment.filename),
            )
        seen.add(ordinal)
        mapped.append(
            _MappedAttachment(
                ordinal=ordinal,
                question_version_id=question_version_id,
                attachment_name=attachment.filename,
                content=attachment.content,
            )
        )
    return tuple(sorted(mapped, key=lambda item: item.ordinal))


def _shown(filename: str) -> str:
    """错误明细里的文件名：去掉不可打印字符并截断（有界、无内容）。"""
    cleaned = "".join(character if character.isprintable() else "\ufffd" for character in filename)
    return cleaned[:120]


def _security_class(reason_code: str) -> str:
    """入口自有的安全分类（只用于日志标注）。"""
    if reason_code == "executable_attachment":
        return "unsafe_attachment"
    if reason_code in {"archive_bomb", "archive_attachment"}:
        return "archive_container"
    return "unsupported_attachment"


def _bounded_detail(detail: str, reason_code: str) -> str:
    """隔离明细的兜底：非空且 ≤1000 字符（数据库 CHECK 是权威，这里只是不把异常留给调用方）。"""
    text = detail.strip() or reason_code
    return text[:MAX_QUARANTINE_DETAIL]


def _bounded_log_text(value: str | None) -> str:
    """日志字段的兜底截断（`None` → 空串，便于 key=value 形态稳定解析）。"""
    if not value:
        return ""
    return value[:MAX_LOG_FIELD]


def _is_checkpoint_conflict(error: IntegrityError) -> bool:
    """该完整性错误是否来自 checkpoint 的**唯一约束**。

    判据是 PostgreSQL 报出的约束名（`diag.constraint_name`），而不是"任何 IntegrityError"：
    "唯一约束冲突 = 并发重复"这个等式只在约束真的是 checkpoint 唯一键时才成立。别的完整性错误
    （NOT NULL、CHECK、外键）意味着**我们自己的代码或库结构与代码不一致**，那时候静默跳过等于
    把一封邮件悄悄丢掉，必须让它冒到调用方去（拉取报告会把它记为失败，日志里有完整堆栈）。
    """
    origin = getattr(error, "orig", None)
    diagnostic = getattr(origin, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    return constraint in CHECKPOINT_UNIQUE_CONSTRAINTS


def _extract_literal(data: Sequence[object]) -> bytes | None:
    """从 `imaplib` 的响应里取出字面量（`(头部片段, 字节)` 元组的第二项）。"""
    for item in data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _first_text(data: Sequence[object]) -> str:
    """响应的可读摘要（用于异常消息；只含服务器文本，不含凭据与邮件内容）。"""
    for item in data:
        if isinstance(item, (bytes, bytearray)):
            return bytes(item).decode("ascii", "replace")[:200]
    return "no server response"


# 公开入口（`intake/__init__.py` 从这里再导出）。
__all__ = [
    "CHECKPOINT_EXISTS",
    "EMAIL_SENDER_MISMATCH",
    "EMAIL_VERIFICATION_EXPIRED",
    "EMAIL_VERIFICATION_REJECTED",
    "ORDINAL_ATTACHMENT_PATTERN",
    "Checkpoint",
    "CheckpointStatus",
    "EmailCheckpointStore",
    "EmailIntakeService",
    "ImapMailboxClient",
    "ImapProtocolError",
    "MessageOutcome",
    "MessageStatus",
    "PullReport",
    "QuarantineRecord",
    "QuarantineStore",
    "email_idempotency_key",
    "email_question_idempotency_key",
    "security_logger",
]


def _pull_failure_code(error: BaseException) -> str:
    """把一轮拉取的失败归类为有界原因码（写 `b2.intake_state.last_error_code`；无 PII）。"""
    if isinstance(error, ImapProtocolError):
        return "imap_unavailable"
    if isinstance(error, MatchingUnavailable):
        return "identity_unavailable"
    if isinstance(error, AssignmentUnavailable):
        return "assignment_unavailable"
    if isinstance(error, SQLAlchemyError):
        return "database_unavailable"
    return "pull_failed"


def _persist_verification_message(
    connection: Connection,
    *,
    created: str,
    message: object,
    verification_id: str,
    attachment_name: str,
    question_version_id: str,
) -> None:
    """核验路径的 `on_persist` 钩子：按题映射 + 正文摘录，与提交**同事务**。

    "存在提交 ⇒ 必存在它的按题映射"因此是事务级事实，而不是两次写入之间的运气；一封邮件
    多题时 `email_verifications.submission_id` 保持 NULL（没有单一提交），映射表才是权威。
    """
    _insert_mapping(
        connection,
        verification_id=verification_id,
        question_version_id=question_version_id,
        submission_id=created,
        attachment_name=attachment_name,
    )
    excerpt = getattr(message, "body_excerpt", None)
    if excerpt:
        connection.execute(
            insert(intake_messages).values(
                intake_message_id=str(uuid.uuid4()),
                submission_id=created,
                subject=getattr(message, "subject", None),
                sender=message.sender,
                # 长期存储的摘录里不留明文核验码（它不是提交内容的一部分）。
                body_excerpt=redact_challenge_codes(excerpt),
                truncated=bool(getattr(message, "body_truncated", False)),
                received_at=utc_now(),
            )
        )


def _insert_mapping(
    connection: Connection,
    *,
    verification_id: str,
    question_version_id: str,
    submission_id: str,
    attachment_name: str,
) -> None:
    """幂等插入按题映射（`(verification_id, question_version_id)` 主键 + `submission_id` 唯一）。

    重试/重放时同一题可能被插入多次：`ON CONFLICT DO NOTHING` 让第二次成为无操作；若已有行指向
    **另一个**提交，那是幂等键失效（同一封邮件同一题应只产生一个提交），由唯一约束与幂等键双重兜底。
    """
    connection.execute(
        pg_insert(email_verification_submissions)
        .values(
            verification_id=uuid.UUID(verification_id),
            question_version_id=uuid.UUID(question_version_id),
            submission_id=uuid.UUID(submission_id),
            attachment_name=attachment_name,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(
            index_elements=[
                email_verification_submissions.c.verification_id,
                email_verification_submissions.c.question_version_id,
            ]
        )
    )


def _persist_manual_message(
    connection: Connection,
    *,
    created: str,
    message: object,
    checkpoints: EmailCheckpointStore,
    mailbox: str,
    uidvalidity: int,
    uid: int,
) -> None:
    """人工复核时：把已存在的 quarantined checkpoint 改为 processed，并写下正文摘录。

    不能复用自动路径的 checkpoint INSERT：该邮件已有一条 `quarantined` checkpoint（唯一约束会
    冲突）。人工复核是"改变同一封邮件的处置结果"，不是"重新收一次信"。
    """
    checkpoints.mark_processed(
        connection,
        mailbox=mailbox,
        uidvalidity=uidvalidity,
        uid=uid,
        submission_id=created,
        now=utc_now(),
    )
    excerpt = getattr(message, "body_excerpt", None)
    if excerpt:
        connection.execute(
            insert(intake_messages).values(
                intake_message_id=str(uuid.uuid4()),
                submission_id=created,
                subject=getattr(message, "subject", None),
                sender=message.sender,
                body_excerpt=excerpt,
                truncated=bool(getattr(message, "body_truncated", False)),
                received_at=utc_now(),
            )
        )
