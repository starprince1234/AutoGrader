"""一次性核验码：签发、状态查询、IMAP 消费与收件心跳（设计 §4）。

威胁模型（`MAIN/docs/architecture/unix-homework-evaluation.md` §4）：`From` 头可伪造，只按发件地址
匹配会让攻击者把提交冒充给任意账号；重放、跨用户认领与并发竞态也必须挡住。因此本模块实现五件事：

1. **账号邮箱所有权**：账号邮箱在 B4 注册流程里已通过一次性验证码证明归属，本模块不新增旁路；
   "这个地址属于哪个账号"只通过 B4 现有 internal identity 端点查询（`matching.SenderDirectory`）。
2. **一次性核验码**：已认证学生为本题目申请；高熵随机、库里只存 SHA-256、默认 30 分钟有效、
   绑定 `(student_id, course_id, section_id, question_version_id)`；明文只在签发响应里出现一次。
3. **邮件必须携带核验码**：收件端从正文摘录里提取候选码 → 哈希 → 行锁查挑战 → 校验未消费/未过期/
   所有者 == 发件账号 → 写 `email_verifications` + 首次发件地址绑定。
4. **首次发件地址绑定**：同一学生同一课程/课序的首次通过验证的地址写入
   `b2.email_sender_bindings`；之后必须一致，否则隔离 `email_sender_mismatch`。
5. **失败反馈与依赖区分**：`get_challenge_status` 返回 pending/received/rejected(reason)/expired；
   收件依赖不可用（心跳过期/未配置）表达为 `reason_code=intake_unavailable`——那是平台条件，
   绝不折算成学生答错。

## 事务与并发（两段式，按设计 §4.3/§4.5）

- `try_consume_received` 在**一个事务**里完成：`SELECT ... FOR UPDATE` 锁挑战行 → 校验 → 写
  `email_verifications`（`submission_id` 先为 NULL）→ 标记挑战已消费（`consumed_at` +
  `consumed_verification_id`）→ 首次插入发件绑定（`ON CONFLICT DO NOTHING` + 回读比对）。
- 调用方（`intake.imap`）随后用**同一个** `SubmissionIntakeService.persist` 创建提交，并在其
  `on_persist` 钩子里回填 `email_verifications.submission_id`——回填与提交同事务，因此不可能出现
  "有 verification 指向不存在的提交"。
- 竞态：挑战消费用行锁 + `email_verifications.challenge_id` 唯一约束；重复投递由
  `(mailbox, uidvalidity, uid)` 唯一约束与 `email_intake` 的 checkpoint 双键兜底。
  **数据库故障原样上抛**（它是平台故障，不是"学生邮件不合格"）；一切"学生侧可解释"的拒绝都返回
  结构化 `VerificationRejected`，不抛异常。
- **恢复边界（两段式的已知窗口）**：消费事务先提交（挑战已消费、`email_verifications` 已写、
  `submission_id` 仍为 NULL），随后 `SubmissionIntakeService.persist` 在提交事务里回填
  `submission_id`。若 persist 失败，会留下"挑战已消费 + verification 未绑定提交"的行：
  **不会**出现 verification 指向不存在的提交，但该码已不可再用（学生重新申请码即可）。
  状态查询对这种行报 `rejected + email_verification_rejected`，**不**报 received-with-null——
  后者会把"提交没生成"说成"已收到"。

## 日志纪律

日志/事件只写标识（mailbox/uidvalidity/uid）、原因码与结论；**明文核验码、邮箱地址、主题、正文
永不进日志**。`intake_messages` 里保存的正文摘录会把核验码替换成占位符（`redact_challenge_codes`）：
那是长期存储的另一个副本，核验码不该在那里留存。
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from autograder_b2.auth.authorization import AuthorizationError, require_submission_authority
from autograder_b2.auth.tokens import AuthenticationError, JwksAuthenticator, Principal
from autograder_b2.db.schema import (
    email_sender_bindings,
    email_verification_challenges,
    email_verifications,
    intake_state,
)
from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.intake.attachments import EmailMessage
from autograder_b2.intake.email_rules import EmailReceipt
from autograder_b2.intake.matching import MatchedAccount, SenderDirectory
from autograder_b2.logging.logger import log_event

logger = logging.getLogger("autograder_b2.intake.email_verification")

#: 核验码形态（与契约 `EmailVerificationChallenge.code` 的 `pattern: ^AG-[A-Z0-9]{12}$` 一致）。
CODE_PREFIX = "AG-"
CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CODE_LENGTH = 12
#: 正文摘录里找核验码：两侧不能紧挨字母数字（避免 `xAG-...` 这类误命中）。
CODE_PATTERN = re.compile(
    rf"(?<![0-9A-Za-z]){re.escape(CODE_PREFIX)}([0-9A-Za-z]{{{CODE_LENGTH}}})(?![0-9A-Za-z])",
    # Students may type the code in lower case; the canonical form is upper case and the
    # database lookup hashes the canonical form, so matching case-insensitively avoids a
    # pointless rejection without weakening the entropy of the code itself.
    re.IGNORECASE,
)
#: 单封邮件最多接受多少个候选码（正文摘录本身有 4000 字上限，这里再给查询一个界）。
MAX_CODE_CANDIDATES = 32
#: 核验码默认有效期（设计 §4.2：30 分钟）。
CHALLENGE_TTL = timedelta(minutes=30)
#: 收件心跳的最大年龄：拉取循环（默认空闲 60 s 一轮、由 systemd timer 驱动）超过 5 个周期没有成功
#: 就是"现在收不到信"，评测侧据此判 `infrastructure_error`。只调大需要迁移/配置变更，故写死常量。
HEARTBEAT_MAX_AGE = timedelta(minutes=5)
#: 签发时允许的随机码碰撞重试次数（36^12 空间，正常一次成功）。
CODE_ISSUE_ATTEMPTS = 5

#: `intake_state.last_error_code` 的取值域（有界、无 PII；未知原因归入 `pull_failed`）。
PULL_FAILED = "pull_failed"
PULL_ERROR_CODES = frozenset(
    {"imap_unavailable", "identity_unavailable", "database_unavailable", "pull_failed"}
)

#: 挑战状态（契约 `EmailVerificationChallengeStatus.state` 的取值域）。
ChallengeState = Literal["pending", "received", "rejected", "expired"]
#: 收件心跳健康度（评测侧 `infrastructure_error` 判定的输入）。
IntakeHealth = Literal["fresh", "stale", "unconfigured"]

#: 由**本模块**写入 `email_verification_challenges.last_rejection_code` 的原因码。
INTAKE_REJECTION_REASONS = frozenset(
    {"email_verification_rejected", "email_sender_mismatch", "email_verification_expired"}
)
#: 契约 `reason_code` 的**全集**（含本模块不写、由评测侧/未来路径保留的取值）。
CONTRACT_REASON_CODES = frozenset(
    {
        "subject_mismatch",
        "attachment_missing",
        "email_sender_mismatch",
        "email_verification_rejected",
        "email_verification_expired",
        "intake_unavailable",
    }
)
#: `intake_state` 单行主键（迁移 0020 的 `ck_intake_state_singleton`）。
INTAKE_STATE_SINGLETON = 1

#: 只把这两条唯一约束的冲突解释为"同一封邮件/同一挑战已被处理过"；其余完整性错误必须响亮失败。
VERIFICATION_UNIQUE_CONSTRAINTS = frozenset(
    {
        "uq_email_verifications_mailbox_uidvalidity_uid",
        "uq_email_verifications_challenge_id",
    }
)
CODE_UNIQUE_CONSTRAINT = "uq_email_verification_challenges_code_sha256"

#: 结构化日志的事件名（只出现标识、原因码与结论）。
_ISSUE_LOG_EVENT = "email_verification.issued"
_CONSUME_LOG_EVENT = "email_verification.consumed"
_REJECT_LOG_EVENT = "email_verification.rejected"


class ChallengeRequestError(ValueError):
    """挑战请求的参数不合法（标识不是 UUID）→ 对外 422。"""


class ChallengeNotFoundError(LookupError):
    """挑战不存在（标识非法与库里没有同为 404）→ 对外 404。"""


@dataclass(frozen=True, slots=True)
class IssuedChallenge:
    """签发结果。`code` 只在这一次响应里出现；库里只有它的 SHA-256。"""

    challenge_id: str
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ChallengeStatus:
    """学生可见的挑战状态（契约 `EmailVerificationChallengeStatus` 的服务层形态）。"""

    challenge_id: str
    state: ChallengeState
    reason_code: str | None
    expires_at: datetime
    verified_at: datetime | None
    submission_id: str | None


@dataclass(frozen=True, slots=True)
class ConsumedVerification:
    """一次成功的核验消费：调用方据此创建提交并回填 `submission_id`。"""

    challenge_id: str
    verification_id: str
    student_id: str
    course_id: str
    section_id: str
    question_version_id: str
    student_number: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VerificationRejected:
    """一次**学生侧**可解释的拒绝（隔离原因码 + 已脱敏明细；不抛异常）。"""

    reason_code: str
    detail: str
    #: 被拒邮件关联的课程/课序（只有能证明时才带：来自挑战行本身，不是从邮件内容猜的）。
    course_id: str | None = None
    section_id: str | None = None


ConsumptionResult = ConsumedVerification | VerificationRejected


class EmailVerificationService:
    """挑战的签发/状态查询 + IMAP 侧的消费与心跳。

    `authenticator` 只用于 HTTP 端点的认证（路由层调用 `authenticate`）；IMAP 路径不需要它。
    `directory` 是发件地址 → 账号的查询端口；缺失时核验路径无法证明所有权，会把带码邮件隔离为
    `email_verification_rejected`，并在状态里显示 `intake_unavailable`（诚实表达"这个部署没配"）。
    """

    def __init__(
        self,
        *,
        engine: Engine,
        authenticator: JwksAuthenticator | None = None,
        directory: SenderDirectory | None = None,
        now: Callable[[], datetime] = utc_now,
        challenge_ttl: timedelta = CHALLENGE_TTL,
        heartbeat_max_age: timedelta = HEARTBEAT_MAX_AGE,
    ) -> None:
        if challenge_ttl <= timedelta(0):
            raise ValueError("the challenge TTL must be positive")
        if heartbeat_max_age <= timedelta(0):
            raise ValueError("the intake heartbeat maximum age must be positive")
        self._engine = engine
        self._authenticator = authenticator
        self._directory = directory
        self._now = now
        self._challenge_ttl = challenge_ttl
        self._heartbeat_max_age = heartbeat_max_age

    # --- 只读属性 -----------------------------------------------------------------

    @property
    def available(self) -> bool:
        """HTTP 端点是否可服务（认证器已装配）。库不可用由各端点自身以 503 表达。"""
        return self._authenticator is not None

    @property
    def unavailable_reason(self) -> str | None:
        if self._authenticator is not None:
            return None
        return "email verification is not configured"

    @property
    def engine(self) -> Engine:
        return self._engine

    def authenticate(self, authorization_header: str | None) -> Principal:
        """认证头 → `Principal`；任何失败都是 `AuthenticationError`（路由层映射 401）。"""
        if self._authenticator is None:
            raise AuthenticationError("email verification is not configured")
        return self._authenticator.authenticate(authorization_header)

    # --- 挑战：签发与状态 ---------------------------------------------------------

    def issue_challenge(
        self,
        principal: Principal,
        *,
        course_id: object,
        section_id: object,
        question_version_id: object,
    ) -> IssuedChallenge:
        """为学生签发一次性核验码（不复用旧码；签发不创建提交、不改变任何成绩）。

        授权复用提交写入的既有判定：学生只能为自己签发，且令牌必须覆盖该课程/课序
        （`require_submission_authority` 的角色 + 作用域 + 本人三层语义）。
        异常：非法标识 `ChallengeRequestError`（422）；越权 `AuthorizationError`（403）；
        数据库故障 `SQLAlchemyError`（503）。
        """
        canonical_course = _canonical_uuid(course_id, "course_id")
        canonical_section = _canonical_uuid(section_id, "section_id")
        canonical_question = _canonical_uuid(question_version_id, "question_version_id")
        require_submission_authority(
            principal,
            course_id=canonical_course,
            section_id=canonical_section,
            student_id=principal.user_id,
        )

        now = self._now()
        expires_at = now + self._challenge_ttl
        for _ in range(CODE_ISSUE_ATTEMPTS):
            code = _generate_code()
            challenge_id = uuid.uuid4()
            try:
                with self._engine.begin() as connection:
                    connection.execute(
                        sa.insert(email_verification_challenges).values(
                            challenge_id=challenge_id,
                            student_id=uuid.UUID(principal.user_id),
                            course_id=uuid.UUID(canonical_course),
                            section_id=uuid.UUID(canonical_section),
                            question_version_id=uuid.UUID(canonical_question),
                            code_sha256=sha256_hex(code.encode("ascii")),
                            created_at=now,
                            expires_at=expires_at,
                            consumed_at=None,
                            consumed_verification_id=None,
                            last_rejection_code=None,
                            attempts=0,
                        )
                    )
            except IntegrityError as error:
                # 只有随机码摘要碰撞才重试；别的完整性错误（NOT NULL/CHECK/FK）必须暴露。
                if _constraint_name(error) != CODE_UNIQUE_CONSTRAINT:
                    raise
                continue
            log_event(
                logger,
                _ISSUE_LOG_EVENT,
                fields={
                    "outcome": "issued",
                    "course_id": canonical_course,
                    "section_id": canonical_section,
                    "question_version_id": canonical_question,
                },
            )
            return IssuedChallenge(
                challenge_id=str(challenge_id), code=code, expires_at=expires_at
            )
        raise RuntimeError("could not allocate a unique e-mail verification code")

    def get_challenge_status(
        self, principal: Principal, challenge_id: object
    ) -> ChallengeStatus:
        """读取挑战状态；**只有所有者**可读（其他人的挑战一律 403，不用 404 掩盖）。

        状态优先级：已消费**且已绑定提交**（received，带 `submission_id`）→ 已记录拒绝
        （rejected(reason)）→ 过期（expired）→ 收件心跳不新鲜（rejected(intake_unavailable)）→
        pending。已记录拒绝优先于过期：拒绝是更明确、更可行动的事实。

        **已消费但 `submission_id` 仍为空**（两段式消费后 persist 失败/尚未完成）**不报 received**：
        那会把半成品说成"提交已生成"。契约的四个 state 里没有更精确的取值，因此报
        `rejected + email_verification_rejected`——学生可以重新申请码。

        异常：非法/不存在标识 `ChallengeNotFoundError`（404）；非所有者 `AuthorizationError`（403）。
        """
        identifier = _challenge_identifier(challenge_id)
        now = self._now()
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(
                    email_verification_challenges.c.student_id,
                    email_verification_challenges.c.expires_at,
                    email_verification_challenges.c.consumed_at,
                    email_verification_challenges.c.last_rejection_code,
                    email_verifications.c.created_at,
                    email_verifications.c.submission_id,
                )
                .select_from(
                    email_verification_challenges.outerjoin(
                        email_verifications,
                        email_verification_challenges.c.consumed_verification_id
                        == email_verifications.c.verification_id,
                    )
                )
                .where(email_verification_challenges.c.challenge_id == identifier)
            ).fetchone()
            if row is None:
                raise ChallengeNotFoundError(str(identifier))
            if str(row[0]) != principal.user_id:
                raise AuthorizationError("this verification challenge belongs to another account")
            (expires_at, consumed_at, last_rejection_code, verified_at, submission_id) = (
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
            )
            health = intake_health(
                connection, now=now, max_age=self._heartbeat_max_age
            )

        state: ChallengeState
        reason_code: str | None = None
        if consumed_at is not None:
            if submission_id is None:
                # 恢复边界（两段式消费）：挑战已消费、邮件事实已记，但提交事务没有落地。
                # 报 received-with-null 等于谎报"提交已生成"；报 rejected 更诚实。
                state = "rejected"
                reason_code = "email_verification_rejected"
            else:
                state = "received"
        elif last_rejection_code is not None:
            state = "rejected"
            reason_code = str(last_rejection_code)
        elif expires_at <= now:
            state = "expired"
        elif health != "fresh" or self._directory is None:
            state = "rejected"
            reason_code = "intake_unavailable"
        else:
            state = "pending"
        return ChallengeStatus(
            challenge_id=str(identifier),
            state=state,
            reason_code=reason_code,
            expires_at=expires_at,
            verified_at=verified_at,
            submission_id=None if submission_id is None else str(submission_id),
        )

    # --- IMAP：发件账号解析与消费 -------------------------------------------------

    def resolve_sender(self, sender: str) -> MatchedAccount | None:
        """发件地址 → 账号；`None` = 确定没有该账号，`MatchingUnavailable` = 查不了（调用方重试）。"""
        if self._directory is None:
            return None
        return self._directory.lookup_account(sender)

    def try_consume_received(
        self,
        *,
        message: EmailMessage,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        sender: str,
        matched_account: MatchedAccount | None,
        attachment_names: Sequence[str] | None = None,
    ) -> ConsumptionResult:
        """尝试用这封邮件消费一个挑战（设计 §4.3）。

        `attachment_names` 是**已通过附件安全闸门**的文件名；缺省用报文里的原始具名部件名。
        成功时挑战被标记消费并写入 `email_verifications`（`submission_id` 由调用方在提交事务里
        回填）；失败一律返回 `VerificationRejected`（不抛"学生错"异常）。数据库故障原样上抛。
        """
        codes = extract_challenge_codes(message.body_excerpt)
        if not codes:
            return VerificationRejected(
                "email_verification_rejected",
                "the message body carries no verification code",
            )
        digests = [sha256_hex(code.encode("ascii")) for code in codes]
        try:
            with self._engine.begin() as connection:
                return self._consume_locked(
                    connection=connection,
                    digest_list=digests,
                    message=message,
                    mailbox=mailbox,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    sender=sender,
                    matched_account=matched_account,
                    attachment_names=attachment_names,
                )
        except IntegrityError as error:
            if _constraint_name(error) in VERIFICATION_UNIQUE_CONSTRAINTS:
                log_event(
                    logger,
                    _REJECT_LOG_EVENT,
                    fields={"outcome": "rejected", "reason_code": "email_verification_rejected"},
                )
                return VerificationRejected(
                    "email_verification_rejected",
                    "a verification record already exists for this mailbox position",
                )
            raise

    def record_submission(
        self, connection: Connection, verification_id: str, submission_id: str
    ) -> None:
        """在**提交事务**里回填 `email_verifications.submission_id`（原子绑定，见模块文档）。"""
        verification_uuid = uuid.UUID(verification_id)
        submission_uuid = uuid.UUID(submission_id)
        result = connection.execute(
            sa.update(email_verifications)
            .where(
                sa.and_(
                    email_verifications.c.verification_id == verification_uuid,
                    email_verifications.c.submission_id.is_(None),
                )
            )
            .values(submission_id=submission_uuid)
        )
        if result.rowcount == 1:
            return
        row = connection.execute(
            sa.select(email_verifications.c.submission_id).where(
                email_verifications.c.verification_id == verification_uuid
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("the verification row disappeared during submission binding")
        if row[0] != submission_uuid:
            raise RuntimeError("the verification row is already bound to another submission")

    # --- 内部：消费 ---------------------------------------------------------------

    def _consume_locked(
        self,
        *,
        connection: Connection,
        digest_list: Sequence[str],
        message: EmailMessage,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        sender: str,
        matched_account: MatchedAccount | None,
        attachment_names: Sequence[str] | None,
    ) -> ConsumptionResult:
        """在调用方事务内做全部判定与写入（挑战行被 `FOR UPDATE` 锁住）。"""
        now = self._now()
        challenges = email_verification_challenges
        rows = connection.execute(
            sa.select(
                challenges.c.challenge_id,
                challenges.c.student_id,
                challenges.c.course_id,
                challenges.c.section_id,
                challenges.c.question_version_id,
                challenges.c.expires_at,
                challenges.c.consumed_at,
            )
            .where(challenges.c.code_sha256.in_(tuple(digest_list)))
            .with_for_update()
        ).fetchall()
        if not rows:
            return self._rejected(
                "email_verification_rejected",
                "no verification challenge matches the code in this message",
            )
        owned = (
            tuple(
                row
                for row in rows
                if matched_account is not None
                and _same_uuid(str(row[1]), matched_account.user_id)
            )
            if matched_account is not None
            else ()
        )
        if not owned:
            # 码可能属于另一个账号：**不**在那行上记拒绝（那是别人挑战的状态）。
            return self._rejected(
                "email_verification_rejected",
                "the verification code does not belong to the sending account",
            )
        if len(owned) > 1:
            return self._rejected(
                "email_verification_rejected",
                "the message carries verification codes issued for more than one challenge",
            )
        challenge = owned[0]
        challenge_id = challenge[0]
        course_id = str(challenge[2])
        section_id = str(challenge[3])
        question_version_id = str(challenge[4])

        if challenge[6] is not None:
            return self._rejected(
                "email_verification_rejected",
                "the verification challenge was already consumed",
                course_id=course_id,
                section_id=section_id,
            )
        if challenge[5] <= now:
            self._record_rejection(connection, challenge_id, "email_verification_expired")
            return self._rejected(
                "email_verification_expired",
                "the verification challenge has expired",
                course_id=course_id,
                section_id=section_id,
            )

        normalized_sender = sender.strip().lower()
        if not normalized_sender:
            return self._rejected(
                "email_verification_rejected",
                "the message carries no sender address",
                course_id=course_id,
                section_id=section_id,
            )
        student_id = str(challenge[1])
        bound_sender = self._effective_binding(
            connection,
            student_id=student_id,
            course_id=course_id,
            section_id=section_id,
            sender=normalized_sender,
            now=now,
        )
        if bound_sender is not None and bound_sender != normalized_sender:
            self._record_rejection(connection, challenge_id, "email_sender_mismatch")
            return self._rejected(
                "email_sender_mismatch",
                "the sender address does not match the first address bound for this course",
                course_id=course_id,
                section_id=section_id,
            )

        names = (
            tuple(str(name) for name in attachment_names)
            if attachment_names is not None
            else tuple(attachment.filename for attachment in message.attachments)
        )
        verification_id = uuid.uuid4()
        connection.execute(
            sa.insert(email_verifications).values(
                verification_id=verification_id,
                challenge_id=challenge_id,
                student_id=uuid.UUID(student_id),
                course_id=uuid.UUID(course_id),
                section_id=uuid.UUID(section_id),
                question_version_id=uuid.UUID(question_version_id),
                submission_id=None,
                sender=normalized_sender,
                subject=message.subject,
                student_number=(
                    None if matched_account is None else matched_account.student_number
                ),
                message_id=message.message_id,
                mailbox=mailbox,
                uidvalidity=uidvalidity,
                uid=uid,
                attachment_names=list(names),
                received_at=now,
                created_at=now,
            )
        )
        updated = connection.execute(
            sa.update(challenges)
            .where(
                sa.and_(
                    challenges.c.challenge_id == challenge_id,
                    challenges.c.consumed_at.is_(None),
                )
            )
            .values(
                consumed_at=now,
                consumed_verification_id=verification_id,
                attempts=challenges.c.attempts + 1,
            )
        )
        if updated.rowcount != 1:
            # 行已在本事务里被锁住，走到这里说明代码与库结构不一致：响亮失败（事务回滚）。
            raise RuntimeError("the verification challenge could not be consumed atomically")
        log_event(
            logger,
            _CONSUME_LOG_EVENT,
            fields={
                "outcome": "consumed",
                "mailbox": mailbox,
                "uidvalidity": uidvalidity,
                "uid": uid,
            },
        )
        return ConsumedVerification(
            challenge_id=str(challenge_id),
            verification_id=str(verification_id),
            student_id=student_id,
            course_id=course_id,
            section_id=section_id,
            question_version_id=question_version_id,
            student_number=None if matched_account is None else matched_account.student_number,
            created_at=now,
        )

    def _effective_binding(
        self,
        connection: Connection,
        *,
        student_id: str,
        course_id: str,
        section_id: str,
        sender: str,
        now: datetime,
    ) -> str | None:
        """读取/建立首次发件地址绑定；返回**当前生效**的地址（`None` 只在异常情况下出现）。

        首次通过验证的地址用 `ON CONFLICT DO NOTHING` 插入，然后**回读**：并发下两个不同地址同时
        首次投递时，只有先提交的那个成为绑定，后一个回读到不同地址 ⇒ 调用方按
        `email_sender_mismatch` 拒绝（唯一约束 + 回读，而不是"先查后插"的竞态窗口）。
        """
        bindings = email_sender_bindings
        triple = sa.and_(
            bindings.c.student_id == uuid.UUID(student_id),
            bindings.c.course_id == uuid.UUID(course_id),
            bindings.c.section_id == uuid.UUID(section_id),
        )
        existing = connection.execute(
            sa.select(bindings.c.sender).where(triple).with_for_update()
        ).fetchone()
        if existing is not None:
            return str(existing[0])
        connection.execute(
            pg_insert(bindings)
            .values(
                binding_id=uuid.uuid4(),
                student_id=uuid.UUID(student_id),
                course_id=uuid.UUID(course_id),
                section_id=uuid.UUID(section_id),
                sender=sender,
                bound_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    bindings.c.student_id,
                    bindings.c.course_id,
                    bindings.c.section_id,
                ]
            )
        )
        stored = connection.execute(
            sa.select(bindings.c.sender).where(triple)
        ).fetchone()
        if stored is None:
            raise RuntimeError("the sender binding could not be established")
        return str(stored[0])

    @staticmethod
    def _record_rejection(
        connection: Connection, challenge_id: uuid.UUID, reason_code: str
    ) -> None:
        """把一次学生侧拒绝记在挑战上（供状态查询区分"被拒"与"还没到"）。"""
        if reason_code not in INTAKE_REJECTION_REASONS:
            raise ValueError("unknown intake rejection reason")
        connection.execute(
            sa.update(email_verification_challenges)
            .where(email_verification_challenges.c.challenge_id == challenge_id)
            .values(
                attempts=email_verification_challenges.c.attempts + 1,
                last_rejection_code=reason_code,
            )
        )
        log_event(
            logger,
            _REJECT_LOG_EVENT,
            fields={"outcome": "rejected", "reason_code": reason_code},
        )

    @staticmethod
    def _rejected(
        reason_code: str,
        detail: str,
        *,
        course_id: str | None = None,
        section_id: str | None = None,
    ) -> VerificationRejected:
        return VerificationRejected(
            reason_code=reason_code,
            detail=detail,
            course_id=course_id,
            section_id=section_id,
        )


class IntakeStateStore:
    """收件心跳（`b2.intake_state` 单行）的写入；读取见模块级 `intake_health`。

    为什么心跳与"本轮拉了哪些邮件"无关：评测要回答的是"**现在**这个部署能不能收到信"。
    只有成功完成一轮拉取才刷新 `last_successful_pull_at`；失败轮只记 `last_error_code`，
    成功时间保持不变——于是心跳变旧本身就证明"最近没有成功轮"。
    """

    def __init__(self, engine: Engine, *, now: Callable[[], datetime] = utc_now) -> None:
        self._engine = engine
        self._now = now

    def record_pull_success(self) -> None:
        moment = self._now()
        statement = pg_insert(intake_state).values(
            id=INTAKE_STATE_SINGLETON,
            last_successful_pull_at=moment,
            last_error_code=None,
            updated_at=moment,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[intake_state.c.id],
            set_={
                "last_successful_pull_at": statement.excluded.last_successful_pull_at,
                "last_error_code": None,
                "updated_at": statement.excluded.updated_at,
            },
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def record_pull_failure(self, error_code: str) -> None:
        moment = self._now()
        code = error_code if error_code in PULL_ERROR_CODES else PULL_FAILED
        statement = pg_insert(intake_state).values(
            id=INTAKE_STATE_SINGLETON,
            last_successful_pull_at=None,
            last_error_code=code,
            updated_at=moment,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[intake_state.c.id],
            set_={
                "last_error_code": statement.excluded.last_error_code,
                "updated_at": statement.excluded.updated_at,
            },
        )
        with self._engine.begin() as connection:
            connection.execute(statement)


# --- 只读入口（评测/参考验证共用） -------------------------------------------------


def load_email_receipt(connection: Connection, submission_id: str) -> EmailReceipt | None:
    """读取一份提交的真实邮件事实；`None` = 没有验证记录（邮件没收到/未被接受）。

    供评测编排的 `email_delivery` 分支使用（与参考答案验证共用 `evaluate_email_receipt`）：

    - `student_number` 取自 `email_verifications.student_number`（intake 时经 B4 internal identity
      记录；评测期 B2 不查 B4）；
    - `account_email` 取记录里的 `sender`：只有经 B4 by-email 解析成功的地址才可能产生验证行；
    - `account_email_verified` 恒为真：邮箱归属证明沿用 B4 注册流程（设计 §4.1），本特性不新增旁路；
    - `sender_binding_matches`：当前绑定行的地址是否仍等于记录里的 `sender`。

    SQLAlchemyError 原样上抛：数据库不可用不是"没有邮件"，调用方必须能区分。
    """
    try:
        identifier = uuid.UUID(str(submission_id))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("submission_id must be a UUID") from error
    verifications = email_verifications
    row = connection.execute(
        sa.select(
            verifications.c.student_number,
            verifications.c.subject,
            verifications.c.sender,
            verifications.c.attachment_names,
            verifications.c.student_id,
            verifications.c.course_id,
            verifications.c.section_id,
        ).where(verifications.c.submission_id == identifier)
    ).fetchone()
    if row is None:
        return None
    (student_number, subject, sender, raw_names, student_id, course_id, section_id) = row
    if isinstance(raw_names, (list, tuple)):
        names = tuple(str(name) for name in raw_names)
    else:
        names = ()
    stored_sender = str(sender)
    binding = connection.execute(
        sa.select(email_sender_bindings.c.sender).where(
            sa.and_(
                email_sender_bindings.c.student_id == student_id,
                email_sender_bindings.c.course_id == course_id,
                email_sender_bindings.c.section_id == section_id,
            )
        )
    ).fetchone()
    return EmailReceipt(
        student_number=None if student_number is None else str(student_number),
        subject=None if subject is None else str(subject),
        sender=stored_sender,
        account_email=stored_sender,
        attachment_names=names,
        received=True,
        account_email_verified=True,
        sender_binding_matches=(
            binding is not None and str(binding[0]) == stored_sender
        ),
    )


def intake_health(
    connection: Connection,
    *,
    now: datetime | None = None,
    max_age: timedelta = HEARTBEAT_MAX_AGE,
) -> IntakeHealth:
    """收件依赖健康度：`fresh` / `stale` / `unconfigured`（只读，不触发拉取）。

    - 没有 `intake_state` 行 ⇒ `unconfigured`（邮件入口从未运行/未配置）；
    - 有行但从未成功、或距上次成功超过 `max_age` ⇒ `stale`；
    - 否则 `fresh`。

    评测侧按设计 §3.5 使用：`load_email_receipt(...) is None` 且 `fresh` ⇒ 学生侧 `missing_output`；
    不 `fresh` ⇒ `infrastructure_error`（平台收不到信，不是学生答错）。
    """
    moment = _as_utc(utc_now() if now is None else now)
    row = connection.execute(
        sa.select(intake_state.c.last_successful_pull_at).where(
            intake_state.c.id == INTAKE_STATE_SINGLETON
        )
    ).fetchone()
    if row is None:
        return "unconfigured"
    last_successful = row[0]
    if last_successful is None:
        return "stale"
    if moment - _as_utc(last_successful) > max_age:
        return "stale"
    return "fresh"


# --- 纯工具：核验码提取/脱敏 -------------------------------------------------------


def extract_challenge_codes(text: str | None) -> tuple[str, ...]:
    """从正文摘录里提取候选核验码（大小写不敏感，规范化为大写；去重且数量有界）。

    大小写宽松是**有意**的：核验码是高熵随机串，大写化不会降低安全性，却能让手打错大小写的学生
    少失败一次。提取只做"候选"，是否真的存在该挑战由数据库按摘要判定。
    """
    if not text:
        return ()
    codes: list[str] = []
    seen: set[str] = set()
    for match in CODE_PATTERN.finditer(text):
        code = f"{CODE_PREFIX}{match.group(1).upper()}"
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        if len(codes) >= MAX_CODE_CANDIDATES:
            break
    return tuple(codes)


def contains_verification_code(text: str | None) -> bool:
    """正文摘录里是否出现"核验码形状"的 token（IMAP 据此选择核验路径）。

    返回 True 只说明**形状像**核验码。形状误报（正文里恰好写了 `AG-XXXXXXXXXXXX`）**有意**按
    核验路径处理：假码不能冒充普通邮件绕过核验；是否真的存在待处理挑战由数据库按 SHA-256 判定，
    绝不凭形状放行。真正决定"是不是核验邮件"的是 `try_consume_received`，不是本函数。
    """
    return bool(text) and CODE_PATTERN.search(str(text)) is not None


def redact_challenge_codes(text: str | None) -> str | None:
    """把核验码替换成占位符（用于长期保存的正文摘录；日志不写正文，无需此函数）。"""
    if text is None:
        return None
    return CODE_PATTERN.sub("[verification-code]", text)


# --- 内部工具 ---------------------------------------------------------------------


def _generate_code() -> str:
    """高熵一次性码：`AG-` + 12 位 `[A-Z0-9]`（36^12 ≈ 4.7e18 空间）。"""
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return f"{CODE_PREFIX}{body}"


def _canonical_uuid(value: object, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise ChallengeRequestError(f"{field} must be a UUID") from error


def _challenge_identifier(value: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ChallengeNotFoundError("malformed verification challenge id") from error


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _same_uuid(left: str, right: str) -> bool:
    """两个 UUID 文本是否指向同一标识（DB 与外部服务的大小写/花括号写法可能不同）。"""
    try:
        return uuid.UUID(left) == uuid.UUID(right)
    except (AttributeError, TypeError, ValueError):
        return left == right


def _constraint_name(error: IntegrityError) -> str | None:
    origin = getattr(error, "orig", None)
    diagnostic = getattr(origin, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


__all__ = [
    "CHALLENGE_TTL",
    "CODE_PATTERN",
    "CODE_PREFIX",
    "CONTRACT_REASON_CODES",
    "HEARTBEAT_MAX_AGE",
    "INTAKE_REJECTION_REASONS",
    "INTAKE_STATE_SINGLETON",
    "PULL_ERROR_CODES",
    "PULL_FAILED",
    "ChallengeNotFoundError",
    "ChallengeRequestError",
    "ChallengeState",
    "ChallengeStatus",
    "ConsumedVerification",
    "EmailVerificationService",
    "IntakeHealth",
    "IntakeStateStore",
    "IssuedChallenge",
    "VerificationRejected",
    "contains_verification_code",
    "extract_challenge_codes",
    "intake_health",
    "load_email_receipt",
    "redact_challenge_codes",
]
