"""提交接收服务：信封构造 → 摘要核对 → 路径白名单 → 上限 → 幂等 → 落库 → outbox。

这是 API 与邮件两条入口**共用**的归一化路径（`docs/architecture/interfaces.md` 第 2 节）：
入口只负责把外部形态交到这里，校验/存储/幂等/事件只有这一处实现。

两条入口的分工是本模块的全部架构含义：

| 入口 | 负责 | 交给本模块 |
|---|---|---|
| `POST /api/v1/submissions` | 认证、授权、请求体契约校验、base64 解码 | 标识 + 字节 |
| IMAP 适配器 | MIME 解析、附件安全闸门、唯一匹配 | 标识 + 字节 |

两者都经 `build_submission_envelope` 产出**同一个**冻结形态，再经 `persist` 走同一段落库。
因此"邮件路径与 API 路径一致"不是靠人工对齐字段，而是**结构上没有第二条代码路径**。

顺序与不变量：

1. **先查量级再查结构**：大小类违规按契约语义返回 `413`（超大小），结构类返回 `422`；
2. **逐文件重算摘要**（I3）——绝不相信客户端声明的 `content_sha256`；
3. **路径白名单**（I2）在存储层再执行一次（契约模式放行 `..`，见 I2 实测）；
4. **非法请求零落库**：任何校验失败都发生在写内容与写元数据之前；
5. **内容先于元数据**：字节先落 CAS，随后"提交行 + 文件行 + 状态迁移 + outbox 事件"在**同一事务**提交；
6. **幂等**：`(idempotency_scope, idempotency_key)` 唯一约束兜底；重放返回既有 `submission_id`，
   且**不**重复入队 `submission.accepted`。
7. **信封可往返**：`correlation_id` 与 provenance 随提交行一起落库（迁移 `20260915_0008`），
   因此 `integration.submission_loader` 能把这一行重新组装成同一个冻结信封。少了这两样，
   评测阶段就没有生产级输入路径——它必须用 `submission.correlation_id` 发事件。
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2 import contracts
from autograder_b2.auth.authorization import require_submission_authority
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import submissions
from autograder_b2.domain import states
from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.domain.envelope import (
    ApiProvenance,
    EmailProvenance,
    EnvelopeFile,
    EnvelopeRejected,
    IntakeProvenance,
    SubmissionEnvelope,
    assert_allowed_relative_path,
    is_allowed_relative_path,
)
from autograder_b2.domain.transitions import record_transitions
from autograder_b2.events import outbox
from autograder_b2.storage.cas import ContentAddressedStore
from autograder_b2.storage.paths import storage_path_for

STATE_VALIDATED = states.VALIDATED
#: 迁移原因：可读但**不含**源码、路径或内部细节（脱敏要求）。
VALIDATED_REASON = "envelope validated: digests verified, content stored by digest"

#: 入口种类（与 `SubmissionEnvelope.intake` 同域）。
IntakeKind = Literal["api", "email"]
#: "必须与提交同事务落库的追加写入"钩子：`(connection, submission_id) -> None`。
#: 邮件入口用它把 `email_checkpoints` 与提交绑在同一事务里（有提交必有 checkpoint）。
PersistHook = Callable[[Connection, str], None]


@dataclass(frozen=True, slots=True)
class IntakeLimits:
    """接收层上限。题目级 `max_files`/`max_total_bytes` 在取得 package 后进一步收紧。"""

    max_files: int
    max_file_bytes: int
    max_total_bytes: int


@dataclass(frozen=True, slots=True)
class IntakeResult:
    submission_id: str
    state: str
    idempotent_replay: bool
    accepted_at: datetime
    evaluation_run_id: str | None = None


class SubmissionIdConflict(Exception):
    """同一 `submission_id` 被配以不同的幂等键重复使用。

    这不是信封校验失败（信封本身合法），而是资源标识冲突，故不复用 `RejectionReason` 的取值。
    契约的 `POST /api/v1/submissions` 未声明 `409`，故对外的最近语义是 `422`（业务规则拒绝）。
    """


@dataclass(frozen=True, slots=True)
class _PreparedFile:
    path: str
    content: bytes
    content_sha256: str


def build_submission_envelope(
    *,
    submission_id: str,
    student_id: str,
    course_id: str,
    section_id: str,
    question_version_id: str,
    idempotency_key: str,
    intake: IntakeKind,
    provenance: IntakeProvenance,
    correlation_id: str,
    files: Sequence[tuple[str, bytes]],
    received_at: datetime | None = None,
) -> SubmissionEnvelope:
    """把「标识 + 字节」归一化为冻结的 `SubmissionEnvelope`（**两条入口的唯一构造点**）。

    - 摘要由服务端按**实际字节**重算（I3），不接受也不转抄任何声明值；
    - 路径白名单在此执行（I2；`stage_contents` 与数据库 CHECK 是第二、第三道防线）；
    - `storage_ref` 由摘要派生（`storage.paths.storage_path_for`），与 CAS 实际落盘路径必然一致，
      `persist` 落盘后还会逐项核对一次；
    - 信封**不含字节**（I6），因此可安全进入日志与事件。
    """
    if not files:
        raise EnvelopeRejected("empty_fileset", "submission must contain at least one file")
    paths = [path for path, _ in files]
    if len(set(paths)) != len(paths):
        raise EnvelopeRejected("duplicate_path", "duplicate file path in submission")

    envelope_files: list[EnvelopeFile] = []
    for path, content in files:
        assert_allowed_relative_path(path)
        digest = sha256_hex(content)
        envelope_files.append(
            EnvelopeFile(
                path=path,
                content_sha256=digest,
                size_bytes=len(content),
                storage_ref=storage_path_for(digest),
            )
        )
    frozen = tuple(envelope_files)
    return SubmissionEnvelope(
        submission_id=submission_id,
        student_id=student_id,
        course_id=course_id,
        section_id=section_id,
        question_version_id=question_version_id,
        idempotency_key=idempotency_key,
        intake=intake,
        provenance=provenance,
        correlation_id=correlation_id,
        files=frozen,
        submission_digest=submission_digest_of(frozen),
        received_at=utc_now() if received_at is None else received_at,
    )


def _provenance_columns(provenance: IntakeProvenance) -> dict[str, object]:
    """把冻结信封的 provenance 展开为 `b2.submissions` 的**显式列**（按类型分支，不用 `getattr` 猜）。

    两种形态的列集合互斥：`ApiProvenance` 只有 `request_id`，`EmailProvenance` 只有
    `mailbox`/`uidvalidity`/`uid`/`message_id`。另一侧一律显式写 `None`（不是"不写"）：
    库里因此永远是一行形状完整的记录，"api 的行没有邮件身份"是可查询的事实而不是缺失值。

    "intake 取值必须与形态一致"这条规则**不在这里再写一遍**——它是数据库 CHECK
    `ck_submissions_provenance_shape` 的唯一权威（避免同一规则两处实现而悄悄分叉）。
    形状与 intake 不符时，INSERT 会在同一事务里以 IntegrityError 当场失败，不会静默写下四不像的元数据。
    """
    if isinstance(provenance, ApiProvenance):
        return {
            "request_id": provenance.request_id,
            "mailbox": None,
            "uidvalidity": None,
            "uid": None,
            "message_id": None,
        }
    if isinstance(provenance, EmailProvenance):
        return {
            "request_id": None,
            "mailbox": provenance.mailbox,
            "uidvalidity": provenance.uidvalidity,
            "uid": provenance.uid,
            "message_id": provenance.message_id,
        }
    # 冻结联合类型之外的形态：宁可当场失败，也不写一行 provenance 全空的提交。
    raise TypeError(f"unsupported provenance type: {type(provenance).__name__}")


class SubmissionIntakeService:
    def __init__(
        self, engine: Engine, store: ContentAddressedStore, limits: IntakeLimits
    ) -> None:
        self._engine = engine
        self._store = store
        self._limits = limits

    @property
    def limits(self) -> IntakeLimits:
        """部署上限（邮件入口的附件闸门复用同一份，避免两处各写一套数字）。"""
        return self._limits

    # --- 入口：HTTP ------------------------------------------------------------

    def accept(
        self,
        payload: Mapping[str, object],
        *,
        principal: Principal,
        correlation_id: str,
    ) -> IntakeResult:
        files = self._check_limits(payload)
        self._check_contract(payload)
        prepared = self._prepare_files(files)
        identity = self._read_envelope(payload)
        require_submission_authority(
            principal,
            course_id=identity["course_id"],
            section_id=identity["section_id"],
            student_id=identity["student_id"],
        )

        pairs = tuple((item.path, item.content) for item in prepared)
        envelope = build_submission_envelope(
            **identity,
            intake="api",
            provenance=ApiProvenance(request_id=correlation_id),
            correlation_id=correlation_id,
            files=pairs,
        )
        return self.persist(envelope, pairs)

    # --- 入口：邮件（与 API 共用同一段落库） ------------------------------------

    def persist(
        self,
        envelope: SubmissionEnvelope,
        files: Sequence[tuple[str, bytes]],
        *,
        on_persist: PersistHook | None = None,
    ) -> IntakeResult:
        """**唯一的持久化例程**：内容先落 CAS，随后提交行 + 文件行 + 状态迁移 + outbox 事件
        在同一事务提交；两条入口都只走这里。

        `on_persist(connection, submission_id)` 是"必须与提交同事务"的追加写入钩子：邮件入口
        用它写 `email_checkpoints`，使"存在提交 ⇒ 必存在 checkpoint"成为事务级事实，
        而不是两次写入之间的运气。
        """
        pairs = tuple((path, content) for path, content in files)
        # 与入口无关的**第二道路径/上限闸门**：入口的估算（base64 长度、附件声明大小）可能有偏差，
        # 真实字节数在此再判一次，因此"上限"只有一处语义。
        self._check_staged_limits(pairs)

        scope = self._idempotency_scope(envelope.student_id, envelope.question_version_id)
        existing = self._find_by_idempotency(scope, envelope.idempotency_key)
        if existing is not None:
            return existing
        if self._submission_id_exists(envelope.submission_id):
            raise SubmissionIdConflict("submission_id is already used by another submission")

        staged, envelope_files = self._store.stage_contents(pairs, now=envelope.received_at)
        if envelope_files != envelope.files:
            # 信封里的摘要/路径/字节数与真正落盘的内容不符：宁可拒绝，也不写一份"元数据与内容
            # 不一致"的提交（那会让下游按摘要取到别的东西）。
            raise EnvelopeRejected(
                "digest_mismatch", "staged content does not match the envelope files"
            )

        accepted_at = envelope.received_at
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    sa.insert(submissions).values(
                        submission_id=uuid.UUID(envelope.submission_id),
                        student_id=uuid.UUID(envelope.student_id),
                        course_id=uuid.UUID(envelope.course_id),
                        section_id=uuid.UUID(envelope.section_id),
                        question_version_id=uuid.UUID(envelope.question_version_id),
                        intake=envelope.intake,
                        idempotency_key=envelope.idempotency_key,
                        idempotency_scope=scope,
                        submission_digest=envelope.submission_digest,
                        state=STATE_VALIDATED,
                        created_at=accepted_at,
                        updated_at=accepted_at,
                        # 冻结信封的 correlation_id 与 provenance 必须落库，否则这个冻结类型不可往返：
                        # 评测阶段要拿 `submission.correlation_id` 发事件（outbox_events.correlation_id
                        # 是 NOT NULL），审计也要能回答"这份提交来自哪封邮件/哪个请求"。
                        correlation_id=uuid.UUID(envelope.correlation_id),
                        **_provenance_columns(envelope.provenance),
                    )
                )
                self._store.attach_files(
                    connection, envelope.submission_id, staged, now=accepted_at
                )
                record_transitions(
                    connection,
                    submission_id=envelope.submission_id,
                    transitions=(
                        (None, states.RECEIVED, None),
                        (states.RECEIVED, STATE_VALIDATED, VALIDATED_REASON),
                    ),
                    occurred_at=accepted_at,
                )
                outbox.enqueue(
                    connection,
                    outbox.build_event(
                        subject=outbox.SUBMISSION_ACCEPTED,
                        payload=outbox.submission_accepted_payload(
                            submission_id=envelope.submission_id,
                            student_id=envelope.student_id,
                            course_id=envelope.course_id,
                            section_id=envelope.section_id,
                            question_version_id=envelope.question_version_id,
                            intake=envelope.intake,
                        ),
                        correlation_id=envelope.correlation_id,
                        occurred_at=accepted_at,
                    ),
                    deduplication_key=f"submission.accepted:{envelope.submission_id}",
                    enqueued_at=accepted_at,
                )
                if on_persist is not None:
                    on_persist(connection, envelope.submission_id)
        except IntegrityError:
            # 并发的同幂等键（或同 UID/Message-ID 的 checkpoint）请求抢先提交：返回既有提交，
            # 不制造第二份。这里**不**把冲突当崩溃——唯一约束正是幂等的实现手段。
            replay = self._find_by_idempotency(scope, envelope.idempotency_key)
            if replay is not None:
                return replay
            if self._submission_id_exists(envelope.submission_id):
                raise SubmissionIdConflict(
                    "submission_id is already used by another submission"
                ) from None
            raise

        return IntakeResult(
            submission_id=envelope.submission_id,
            state=STATE_VALIDATED,
            idempotent_replay=False,
            accepted_at=accepted_at,
        )

    # --- 校验 ------------------------------------------------------------------

    def _check_limits(self, payload: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
        """大小类违规先判：契约把"太大"定义为 `413`，而不是结构错误 `422`。"""
        raw_files = payload.get("files")
        if not isinstance(raw_files, list):
            return ()
        if len(raw_files) > self._limits.max_files:
            raise EnvelopeRejected(
                "too_many_files", f"at most {self._limits.max_files} files per submission"
            )
        total = 0
        seen_encoded: list[int] = []
        for entry in raw_files:
            if not isinstance(entry, Mapping):
                return raw_files  # 结构问题交给契约校验报错
            encoded = entry.get("content_base64")
            if not isinstance(encoded, str):
                return raw_files
            # 先按**编码长度精确折算**解码后大小，避免为大载荷做无谓解码。
            # 不能写成 `(len(encoded) // 4) * 3`：那对 `n ≡ 1 (mod 3)` 会高估 2 字节，把**恰好等于
            # 上限**的合法文件当成"超限 2 字节"拒成 413（真实误报）。公式与
            # `intake.attachments._estimated_decoded_size` 一致：去掉 padding 后 4 字符 = 3 字节，
            # 余数字符再补 `余数 - 1` 个字节。
            decoded_size = _decoded_base64_size(encoded)
            seen_encoded.append(decoded_size)
            if decoded_size > self._limits.max_file_bytes:
                raise EnvelopeRejected(
                    "file_too_large",
                    f"a single file must not exceed {self._limits.max_file_bytes} bytes",
                )
            total += decoded_size
        if total > self._limits.max_total_bytes:
            raise EnvelopeRejected(
                "total_too_large", f"a submission must not exceed {self._limits.max_total_bytes} bytes"
            )
        return raw_files

    def _check_staged_limits(self, files: Sequence[tuple[str, bytes]]) -> None:
        """按**真实字节**判上限（两条入口的第二道、也是唯一权威的一道）。"""
        if len(files) > self._limits.max_files:
            raise EnvelopeRejected(
                "too_many_files", f"at most {self._limits.max_files} files per submission"
            )
        total = 0
        for _, content in files:
            if len(content) > self._limits.max_file_bytes:
                raise EnvelopeRejected(
                    "file_too_large",
                    f"a single file must not exceed {self._limits.max_file_bytes} bytes",
                )
            total += len(content)
        if total > self._limits.max_total_bytes:
            raise EnvelopeRejected(
                "total_too_large", f"a submission must not exceed {self._limits.max_total_bytes} bytes"
            )

    def _check_contract(self, payload: Mapping[str, object]) -> None:
        errors = contracts.contract_schema_errors(contracts.SUBMISSION_ENVELOPE_SCHEMA, payload)
        if errors:
            raise EnvelopeRejected("schema_invalid", f"envelope failed contract validation: {errors[0]}")

    def _prepare_files(self, raw_files: Sequence[Mapping[str, object]]) -> tuple[_PreparedFile, ...]:
        prepared: list[_PreparedFile] = []
        paths: set[str] = set()
        for entry in raw_files:
            path = entry.get("path")
            if not isinstance(path, str) or not is_allowed_relative_path(path):
                raise EnvelopeRejected("path_not_allowed", "file path is not permitted")
            if path in paths:
                raise EnvelopeRejected("duplicate_path", "duplicate file path in submission")
            paths.add(path)
            encoded = entry.get("content_base64")
            try:
                content = base64.b64decode(encoded, validate=True)  # type: ignore[arg-type]
            except (binascii.Error, ValueError) as error:
                raise EnvelopeRejected("schema_invalid", "content_base64 is not valid base64") from error
            digest = sha256_hex(content)
            claimed = entry.get("content_sha256")
            if digest != claimed:
                # 摘要不符一律拒绝：既不采用声明值，也不静默纠正（I3）。
                raise EnvelopeRejected("digest_mismatch", "content_sha256 does not match the content")
            prepared.append(_PreparedFile(path=path, content=content, content_sha256=digest))
        if not prepared:
            raise EnvelopeRejected("empty_fileset", "submission must contain at least one file")
        return tuple(prepared)

    @staticmethod
    def _read_envelope(payload: Mapping[str, object]) -> dict[str, str]:
        """取出归一化所需的标识字段；契约校验已保证它们存在且形态正确。"""
        keys = (
            "submission_id",
            "student_id",
            "course_id",
            "section_id",
            "question_version_id",
            "idempotency_key",
        )
        return {key: str(payload[key]) for key in keys}

    @staticmethod
    def _idempotency_scope(student_id: str, question_version_id: str) -> str:
        """作用域由服务端派生（契约口径 `(idempotency_key, scope)`）：同一个学生同一题目。

        **必须先规范化**：契约的 `Uuid` 接受大写、花括号、`urn:uuid:`、无连字符等**合法写法**
        （令牌侧 `auth/tokens._canonical_uuid` 已显式规范化）。若这里直接拼原始字符串，同一个学生
        只要换一种大小写就能得到**另一个作用域**，于是同一个幂等键会被落成两份提交——幂等形同虚设
        （实测：除大小写外完全相同的两次请求都返回 202，`b2.submissions` 落了两行）。
        两个入口的标识都经契约 `format: uuid` 校验，因此这里的解析失败属内部缺陷，让它抛。
        """
        return f"{uuid.UUID(student_id)}:{uuid.UUID(question_version_id)}"

    # --- 幂等 ------------------------------------------------------------------

    def _submission_id_exists(self, submission_id: str) -> bool:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(submissions.c.submission_id).where(
                    submissions.c.submission_id == uuid.UUID(submission_id)
                )
            ).fetchone()
        return row is not None

    def _find_by_idempotency(self, scope: str, key: str) -> IntakeResult | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(
                    submissions.c.submission_id,
                    submissions.c.state,
                    submissions.c.created_at,
                ).where(
                    sa.and_(
                        submissions.c.idempotency_scope == scope,
                        submissions.c.idempotency_key == key,
                    )
                )
            ).fetchone()
        if row is None:
            return None
        return IntakeResult(
            submission_id=str(row[0]),
            state=str(row[1]),
            idempotent_replay=True,
            accepted_at=row[2],
        )


def submission_digest_of(files: Sequence[EnvelopeFile | _PreparedFile]) -> str:
    """五项绑定摘要之一：全部文件路径 + 各文件 SHA-256（与顺序无关）。"""
    canonical = "\n".join(
        f"{path} {digest}" for path, digest in sorted((item.path, item.content_sha256) for item in files)
    )
    return sha256_hex(canonical.encode("utf-8"))


def _decoded_base64_size(encoded: str) -> int:
    """base64 文本解码后的**精确**字节数（只看长度与 padding，不真的解码）。

    与 `intake.attachments._estimated_decoded_size` 同一公式：`(len//4)*3` 对 `n ≡ 1 (mod 3)`
    会高估 2 字节，而这里的比较是 `>`，于是"恰好等于上限"的文件被误判成超限。
    """
    stripped = encoded.rstrip("=").strip()
    groups, remainder = divmod(len(stripped), 4)
    return groups * 3 + max(remainder - 1, 0)
