"""IMAP 邮件接收（RQ-B-2-20）的归一化 / 幂等 / 隔离 / 安全测试（T-MAIL-001..004）。

每一条验收标准都对应**真的跑过**的路径：

| 验收标准 | 测试 |
|---|---|
| 归一化：同一 `SubmissionEnvelope`、同一段落库、不产生重复提交 | `test_email_attachment_produces_the_same_envelope_as_the_api_path`、`test_email_submission_writes_the_same_rows_as_the_api_path`、`test_email_content_dedups_against_an_identical_api_submission` |
| checkpoint：UID/`Message-ID` 双键幂等、不唯一即隔离、可查询、无评测 | `test_same_uid_and_uidvalidity_are_pulled_once`、`test_same_message_id_under_a_changed_uidvalidity_is_skipped`、`test_..._quarantined...`×2 |
| 安全：可执行与压缩炸弹被拒并记安全事件；无正文/附件原文 | `test_..._rejected...`×6、`test_body_and_attachment_bytes_never_reach_the_quarantine_or_the_logs` |

邮件一侧走**真实 TCP** 上的进程内 IMAP 夹具（`tests/imap_fixture.py`），客户端是 stdlib `imaplib`；
数据库一侧是真实 PostgreSQL。真实外部邮箱与 TLS **未**验证（见模块文档末尾）。
"""

from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import io
import json
import logging
import lzma
import pathlib
import struct
import tarfile
import time
import uuid
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2.auth.tokens import CourseScope, Principal
from autograder_b2.config import (
    DEFAULT_IMAP_MAX_MESSAGE_BYTES,
    ConfigurationError,
    ImapSettings,
    load_imap_settings,
)
from autograder_b2.db.schema import (
    artifacts,
    email_checkpoints,
    intake_quarantines,
    outbox_events,
    submission_files,
    submission_state_transitions,
    submissions,
)
from autograder_b2.domain.envelope import (
    ApiProvenance,
    EmailProvenance,
    EnvelopeFile,
    EnvelopeRejected,
    SubmissionEnvelope,
    is_allowed_relative_path,
)
from autograder_b2.intake import service as service_module
from autograder_b2.intake.attachments import (
    _ALLOWED_TEXT_FORMAT,
    _BIDI_CONTROL_CHARACTERS,
    MAX_MESSAGE_ID_LENGTH,
    AttachmentRejected,
    RawAttachment,
    message_id_for_storage,
    parse_message,
    plain_text_problem,
    screen_attachments,
    sniff_content,
)
from autograder_b2.intake.imap import (
    MAX_LOG_FIELD,
    MAX_LOG_LINE,
    Checkpoint,
    EmailCheckpointStore,
    EmailIntakeService,
    QuarantineStore,
    _is_checkpoint_conflict,
)
from autograder_b2.intake.matching import MappingEmailMatcher
from autograder_b2.intake.runtime import build_runtime
from autograder_b2.intake.service import (
    IntakeLimits,
    SubmissionIntakeService,
    build_submission_envelope,
    submission_digest_of,
)
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres
from tests.imap_fixture import FixtureMessage, ImapFixtureServer

pytestmark = requires_postgres

MAILBOX = "submissions"
USERNAME = "b2-intake"
PASSWORD = "fixture-secret"

#: 上限有意取小：让"超限"与"压缩炸弹"都能用几 KB 的邮件真实触发。
LIMITS = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)

STUDENT_ID = "11111111-1111-4111-8111-111111111111"
COURSE_ID = "22222222-2222-4222-8222-222222222222"
SECTION_ID = "33333333-3333-4333-8333-333333333333"
QUESTION_ID = "44444444-4444-4444-8444-444444444444"
OTHER_STUDENT_ID = "55555555-5555-4555-8555-555555555555"

SOURCE = b"print('hello from email')\n"

#: 唯一标记：用来证明正文与附件原文既没有进库、也没有进日志。
BODY_MARKER = "BODYCONTENTMARKER4f2a"
ATTACHMENT_MARKER = b"ATTACHMENTCONTENTMARKER9c1b"

#: 真实的 ELF 头 + 标记字节：名字无害（`run.sh`），内容是可执行体。
ELF_PAYLOAD = b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 8 + ATTACHMENT_MARKER + b"\x00" * 16
#: 真实的 PE：`MZ` + 0x3c 处的 PE 头偏移 + 偏移处的 `PE\0\0`。
PE_PAYLOAD = b"MZ" + b"\x00" * 0x3A + (0x40).to_bytes(4, "little") + b"PE\x00\x00" + b"\x00" * 32

RULES: dict[str, dict[str, str]] = {
    "alice@example.edu": {
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "section_id": SECTION_ID,
        "question_version_id": QUESTION_ID,
    }
}


# --- 夹具 ---------------------------------------------------------------------


#: 造一个"带约束名"的 DB 异常（用于检查 `_is_checkpoint_conflict` 只认 checkpoint 唯一约束）。
class _DiagError(Exception):
    """携带 `diag.constraint_name` 的假 psycopg 异常（只用于构造 `IntegrityError.orig`）。"""

    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.diag = SimpleNamespace(constraint_name=constraint_name)


@dataclass(frozen=True, slots=True)
class Harness:
    """接收侧的一整套真实组件（真实 PostgreSQL + 真实 CAS 根目录）。"""

    engine: Engine
    store: ContentAddressedStore
    intake: SubmissionIntakeService
    matcher: MappingEmailMatcher
    limits: IntakeLimits

    @property
    def quarantine(self) -> QuarantineStore:
        return QuarantineStore(self.engine)

    @property
    def checkpoints(self) -> EmailCheckpointStore:
        return EmailCheckpointStore(self.engine)

    def service(
        self,
        *,
        port: int = 1,
        mailbox: str = MAILBOX,
        max_message_bytes: int = DEFAULT_IMAP_MAX_MESSAGE_BYTES,
    ) -> EmailIntakeService:
        return EmailIntakeService(
            engine=self.engine,
            settings=imap_settings(
                port=port, mailbox=mailbox, max_message_bytes=max_message_bytes
            ),
            intake=self.intake,
            matcher=self.matcher,
            checkpoints=self.checkpoints,
            quarantines=self.quarantine,
        )

    def service_for(self, server: ImapFixtureServer) -> EmailIntakeService:
        return self.service(port=server.port, mailbox=server.mailbox)


def imap_settings(
    *,
    port: int,
    mailbox: str = MAILBOX,
    max_message_bytes: int = DEFAULT_IMAP_MAX_MESSAGE_BYTES,
) -> ImapSettings:
    return ImapSettings(
        host="127.0.0.1",
        port=port,
        username=USERNAME,
        password=PASSWORD,
        mailbox=mailbox,
        use_ssl=False,
        matching=RULES,
        max_messages_per_pull=20,
        max_message_bytes=max_message_bytes,
        timeout_seconds=10.0,
    )


def build_harness(
    engine: Engine,
    artifact_root: pathlib.Path,
    *,
    rules: Mapping[str, Mapping[str, str]] | None = None,
    limits: IntakeLimits = LIMITS,
) -> Harness:
    store = ContentAddressedStore(artifact_root, engine)
    return Harness(
        engine=engine,
        store=store,
        intake=SubmissionIntakeService(engine, store, limits),
        matcher=MappingEmailMatcher(RULES if rules is None else rules),
        limits=limits,
    )


def jwks_document() -> dict[str, object]:
    """一份**真实可用**的 Ed25519 JWKS（形态与 B4 一致；这里只为让 `build_runtime` 装配通过）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    def encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    public = Ed25519PrivateKey.generate().public_key()
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": encode(hashlib.sha256(raw).digest()),
                "x": encode(raw),
                "use": "sig",
                "alg": "EdDSA",
            }
        ]
    }


@pytest.fixture()
def harness(engine: Engine, artifact_root: pathlib.Path) -> Harness:
    return build_harness(engine, artifact_root)


def build_message(
    *,
    message_id: str | None = "<message-1@example.edu>",
    sender: str = "alice@example.edu",
    body: str = f"please grade this. {BODY_MARKER}",
    attachments: Sequence[tuple[str, bytes]] = (("main.py", SOURCE),),
) -> bytes:
    """构造一封真实的 MIME 邮件（附件一律用 base64 传输编码，与真实邮件客户端一致）。"""
    from email.message import EmailMessage as MIMEMessage

    message = MIMEMessage()
    message["From"] = sender
    message["To"] = f"{MAILBOX}@example.edu"
    if message_id is not None:
        message["Message-ID"] = message_id
    message["Subject"] = "submission"
    message.set_content(body)
    for name, payload in attachments:
        message.add_attachment(
            payload, maintype="application", subtype="octet-stream", filename=name
        )
    return message.as_bytes()


def api_payload(
    files: Sequence[tuple[str, bytes]] = (("main.py", SOURCE),),
    *,
    idempotency_key: str = "api-key-1",
    submission_id: str | None = None,
    student_id: str = STUDENT_ID,
) -> dict[str, object]:
    """与 `tests/test_intake_api.py` 同形态的契约载荷。"""
    return {
        "submission_id": submission_id or str(uuid.uuid4()),
        "student_id": student_id,
        "course_id": COURSE_ID,
        "section_id": SECTION_ID,
        "question_version_id": QUESTION_ID,
        "idempotency_key": idempotency_key,
        "files": [
            {
                "path": path,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
            for path, content in files
        ],
    }


def student_principal(student_id: str = STUDENT_ID) -> Principal:
    return Principal(
        user_id=student_id,
        roles=("student",),
        scopes=(CourseScope(course_id=COURSE_ID, section_id=SECTION_ID),),
        token_id=str(uuid.uuid4()),
    )


def count(engine: Engine, table: sa.Table) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one())


def rows(engine: Engine, statement: Any) -> list[Any]:
    with engine.connect() as connection:
        return list(connection.execute(statement).fetchall())


def submission_row(engine: Engine, submission_id: str) -> Mapping[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.select(submissions).where(submissions.c.submission_id == uuid.UUID(submission_id))
            )
            .mappings()
            .one()
        )
    return dict(row)


def transition_rows(engine: Engine, submission_id: str) -> list[Mapping[str, Any]]:
    with engine.connect() as connection:
        found = (
            connection.execute(
                sa.select(submission_state_transitions)
                .where(submission_state_transitions.c.submission_id == uuid.UUID(submission_id))
                .order_by(submission_state_transitions.c.sequence)
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in found]


def dump_table(engine: Engine, table: sa.Table) -> str:
    """把整张表（全部列）序列化成文本，供"某段文字是否出现在库里"的扫描使用。"""
    with engine.connect() as connection:
        found = connection.execute(sa.select(table)).mappings().all()
    return json.dumps([dict(row) for row in found], default=str, ensure_ascii=False)


def zip_bomb(*, declared_bytes: int) -> bytes:
    """构造一个**真实的** ZIP 炸弹：文件很小，声明的未压缩体积极大（全零高度可压缩）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.bin", b"\x00" * declared_bytes)
    return buffer.getvalue()


def small_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "hello\n")
    return buffer.getvalue()


def gzip_payload(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def fixture_server(
    *, messages: Sequence[FixtureMessage] = (), uidvalidity: int = 1
) -> ImapFixtureServer:
    """夹具服务器的账号/邮箱名与被测配置对齐（否则测的就是"登录失败"那条路径）。"""
    return ImapFixtureServer(
        messages=list(messages),
        uidvalidity=uidvalidity,
        username=USERNAME,
        password=PASSWORD,
        mailbox=MAILBOX,
    )


# --- 验收标准 1：附件归一化为同一信封、走同一段落库、不重复 -----------------------


def test_email_attachment_produces_the_same_envelope_as_the_api_path(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) 两条入口交给共享持久化例程的信封**结构相同**（按数据类字段逐项断言，不是转述）。

    截获方式是观察真实的调用点：包住 `SubmissionIntakeService.persist` 记录它收到的信封，
    随后仍调用**原实现**——所以这里看到的就是真正落库的那个对象。
    """
    captured: list[SubmissionEnvelope] = []
    original = SubmissionIntakeService.persist

    def spy(self: SubmissionIntakeService, envelope: SubmissionEnvelope, files: Any, **kwargs: Any):
        captured.append(envelope)
        return original(self, envelope, files, **kwargs)

    monkeypatch.setattr(SubmissionIntakeService, "persist", spy)

    harness.intake.accept(
        api_payload(), principal=student_principal(), correlation_id=str(uuid.uuid4())
    )
    outcome = harness.service().process_raw(uidvalidity=1, uid=1, raw=build_message())

    assert outcome.status == "processed", outcome.detail
    assert len(captured) == 2, "两条入口都必须经共享持久化例程"
    api_envelope, email_envelope = captured

    # ① 同一个冻结类型
    assert type(email_envelope) is SubmissionEnvelope is type(api_envelope)
    assert [field.name for field in fields(email_envelope)] == [
        field.name for field in fields(api_envelope)
    ]
    # ② 逐字段的运行期类型一致（intake/provenance 是**应当不同**的两个字段，单独断言）
    for field in fields(SubmissionEnvelope):
        if field.name in {"intake", "provenance", "files"}:
            continue
        assert type(getattr(email_envelope, field.name)) is type(
            getattr(api_envelope, field.name)
        ), f"字段 {field.name} 的运行期类型与 API 路径不同"
    # ③ 文件元素同类型同字段
    assert [field.name for field in fields(EnvelopeFile)] == ["path", "content_sha256", "size_bytes", "storage_ref"]
    assert all(isinstance(item, EnvelopeFile) for item in email_envelope.files)
    assert all(isinstance(item, EnvelopeFile) for item in api_envelope.files)
    # ④ 只有入口相关的两处不同
    assert email_envelope.intake == "email" and api_envelope.intake == "api"
    assert isinstance(email_envelope.provenance, EmailProvenance)
    assert isinstance(api_envelope.provenance, ApiProvenance)
    assert email_envelope.provenance.mailbox == MAILBOX
    assert (email_envelope.provenance.uidvalidity, email_envelope.provenance.uid) == (1, 1)
    assert email_envelope.provenance.message_id == "<message-1@example.edu>"
    # ⑤ 相同的文件路径/摘要/字节数 ⇒ 相同的绑定摘要（摘要与入口无关）
    assert email_envelope.files == api_envelope.files
    assert email_envelope.submission_digest == api_envelope.submission_digest
    assert email_envelope.submission_digest == submission_digest_of(email_envelope.files)


def assert_matches_api_rows(harness: Harness, email_id: str, api_id: str) -> None:
    """逐项断言"邮件路径落下的行"与"API 路径落下的行"同构（(b) 的判定体，被两个用例复用）。"""
    api_row = submission_row(harness.engine, api_id)
    email_row = submission_row(harness.engine, email_id)

    # 入库的值逐列一致；入口相关的两列不同（这正是"同一形态、不同来源"的含义）
    for column in (
        "student_id",
        "course_id",
        "section_id",
        "question_version_id",
        "submission_digest",
        "state",
    ):
        assert str(email_row[column]) == str(api_row[column]), column

    assert api_row["intake"] == "api" and email_row["intake"] == "email"
    assert api_row["idempotency_scope"] == email_row["idempotency_scope"] == f"{STUDENT_ID}:{QUESTION_ID}"
    assert api_row["state"] == email_row["state"] == "validated"

    # 文件行：路径 → 摘要（内容寻址）；两条入口各一行，且都指向同一个 artifact
    def file_rows(submission_id: str) -> list[tuple[Any, ...]]:
        found = rows(
            harness.engine,
            sa.select(submission_files.c.path, artifacts.c.content_sha256, artifacts.c.size_bytes)
            .select_from(
                submission_files.join(
                    artifacts, submission_files.c.artifact_id == artifacts.c.artifact_id
                )
            )
            .where(submission_files.c.submission_id == uuid.UUID(submission_id)),
        )
        return [tuple(row) for row in found]

    assert file_rows(api_id) == file_rows(email_id) == [
        ("main.py", hashlib.sha256(SOURCE).hexdigest(), len(SOURCE))
    ]

    # 状态迁移史：同样两条（received → validated），序号与原因字面量都相同
    def history(submission_id: str) -> list[tuple[Any, ...]]:
        return [
            (row["sequence"], row["from_state"], row["to_state"], row["reason"])
            for row in transition_rows(harness.engine, submission_id)
        ]

    assert history(email_id) == history(api_id)
    assert [(entry[1], entry[2]) for entry in history(email_id)] == [
        (None, "received"),
        ("received", "validated"),
    ]

    # outbox 事件：同样的 subject／producer／payload 键集，只有 intake 取值不同
    events = rows(
        harness.engine,
        sa.select(
            outbox_events.c.subject, outbox_events.c.producer, outbox_events.c.payload
        ).order_by(outbox_events.c.enqueued_at),
    )
    assert len(events) == 2
    for subject, producer, payload in events:
        assert subject == "autograder.v1.submission.accepted"
        assert producer == "B2"
        assert set(payload) == {
            "submission_id",
            "student_id",
            "course_id",
            "section_id",
            "question_version_id",
            "intake",
        }
    assert {event[2]["submission_id"] for event in events} == {email_id, api_id}
    assert {event[2]["intake"] for event in events} == {"api", "email"}


def test_email_submission_writes_the_same_rows_as_the_api_path(harness: Harness) -> None:
    """(b) 提交行 + 文件行 + 状态迁移 + outbox 事件与 API 路径逐项同构。"""
    api_result = harness.intake.accept(
        api_payload(idempotency_key="api-1"),
        principal=student_principal(),
        correlation_id=str(uuid.uuid4()),
    )
    email_outcome = harness.service().process_raw(uidvalidity=1, uid=1, raw=build_message())
    assert email_outcome.status == "processed", email_outcome.detail
    assert email_outcome.submission_id is not None

    assert_matches_api_rows(harness, email_outcome.submission_id, api_result.submission_id)
    assert count(harness.engine, artifacts) == 1, "两条入口的内容相同 ⇒ 只有一份 CAS 条目"


def test_imap_pull_normalises_to_the_same_rows_as_the_api_path(harness: Harness) -> None:
    """端到端版 (a)+(b)：真实 TCP/IMAP 拉回的真实 MIME 报文，落下的行与 API 路径同构。"""
    api_result = harness.intake.accept(
        api_payload(idempotency_key="api-e2e"),
        principal=student_principal(),
        correlation_id=str(uuid.uuid4()),
    )
    with fixture_server(
        messages=[FixtureMessage(uid=1, raw=build_message())], uidvalidity=31
    ) as server:
        report = harness.service_for(server).pull()

    assert report.count("processed") == 1
    submission_id = report.outcomes[0].submission_id
    assert submission_id is not None
    assert_matches_api_rows(harness, submission_id, api_result.submission_id)
    # 内容寻址：两条提交、两份文件元数据、**一份**内容（同一份字节）
    assert count(harness.engine, submissions) == 2
    assert count(harness.engine, submission_files) == 2
    assert count(harness.engine, artifacts) == 1
    assert harness.store.read(hashlib.sha256(SOURCE).hexdigest()) == SOURCE


def test_email_content_dedups_against_an_identical_api_submission(harness: Harness) -> None:
    """(c) 同一份内容先走 API 再走邮件：内容寻址只留一份内容，但两条提交各留一行文件。"""
    harness.intake.accept(
        api_payload(idempotency_key="api-dedup"),
        principal=student_principal(),
        correlation_id=str(uuid.uuid4()),
    )
    outcome = harness.service().process_raw(uidvalidity=1, uid=1, raw=build_message())
    assert outcome.status == "processed", outcome.detail

    assert count(harness.engine, submissions) == 2
    assert count(harness.engine, submission_files) == 2
    assert count(harness.engine, artifacts) == 1, "相同内容必须只有一份 CAS 条目"
    assert harness.store.read(hashlib.sha256(SOURCE).hexdigest()) == SOURCE


# --- 验收标准 2：checkpoint 双键幂等、隔离、不评测 --------------------------------


def test_same_uid_and_uidvalidity_are_pulled_once(harness: Harness) -> None:
    """(a) 同一 UID/UIDVALIDITY 拉两次 ⇒ 一份提交、一条 checkpoint，第二次连报文都不取。"""
    raw = build_message()
    with fixture_server(
        messages=[FixtureMessage(uid=1, raw=raw)], uidvalidity=4242
    ) as server:
        service = harness.service_for(server)
        first = service.pull()
        second = service.pull()
        connections = server.accepted_connections

    assert (first.uidvalidity, first.fetched) == (4242, 1)
    assert first.count("processed") == 1
    assert (second.fetched, second.count("skipped")) == (0, 1)
    assert second.outcomes[0].reason_code == "checkpoint_exists"
    assert second.outcomes[0].submission_id == first.outcomes[0].submission_id
    assert connections == 2, "第二次拉取必须真的又连了一次邮箱（否则这条测试什么也没证明）"

    assert count(harness.engine, submissions) == 1
    assert count(harness.engine, outbox_events) == 1
    checkpoint_rows = rows(
        harness.engine,
        sa.select(
            email_checkpoints.c.mailbox,
            email_checkpoints.c.uidvalidity,
            email_checkpoints.c.uid,
            email_checkpoints.c.status,
            email_checkpoints.c.reason_code,
            email_checkpoints.c.submission_id,
        ),
    )
    assert len(checkpoint_rows) == 1
    mailbox, uidvalidity, uid, status, reason_code, submission_id = checkpoint_rows[0]
    assert (mailbox, uidvalidity, uid) == (MAILBOX, 4242, 1)
    assert status == "processed" and reason_code is None
    assert str(submission_id) == first.outcomes[0].submission_id
    assert server.flags_of(1) == frozenset({"\\Seen"}), "处理成功后应显式标记已读"


def test_same_message_id_under_a_changed_uidvalidity_is_skipped(harness: Harness) -> None:
    """(b) 换了 UIDVALIDITY（UID 也变了）但 `Message-ID` 相同 ⇒ 仍然跳过，不产生第二份提交。"""
    message_id = "<stable-message-id@example.edu>"
    with fixture_server(
        messages=[FixtureMessage(uid=1, raw=build_message(message_id=message_id))],
        uidvalidity=1000,
    ) as first_server:
        first = harness.service_for(first_server).pull()

    # 第二个服务器：不同的 UIDVALIDITY、不同的 UID、**不同内容**，但 Message-ID 相同。
    with fixture_server(
        messages=[
            FixtureMessage(
                uid=9,
                raw=build_message(
                    message_id=message_id, attachments=(("main.py", b"print('changed')\n"),)
                ),
            )
        ],
        uidvalidity=2000,
    ) as second_server:
        second = harness.service_for(second_server).pull()

    assert first.count("processed") == 1
    assert second.fetched == 1, "UID 是新的，所以报文会被取回"
    assert second.count("processed") == 0
    assert second.outcomes[0].status == "skipped"
    assert second.outcomes[0].reason_code == "checkpoint_exists"
    assert count(harness.engine, submissions) == 1, "Message-ID 相同不得产生第二份提交"
    assert count(harness.engine, email_checkpoints) == 1


def test_message_that_matches_nothing_is_quarantined_and_queryable(harness: Harness) -> None:
    """(c) 无法唯一匹配 ⇒ 恰好一条脱敏隔离记录、零提交、零事件；且隔离记录可查询。"""
    raw = build_message(sender="stranger@example.edu", message_id="<unmatched@example.edu>")
    outcome = harness.service().process_raw(uidvalidity=77, uid=5, raw=raw)

    assert outcome.status == "quarantined"
    assert outcome.reason_code == "not_uniquely_matched"
    assert outcome.submission_id is None
    assert count(harness.engine, submissions) == 0, "未唯一匹配的邮件不得产生提交"
    assert count(harness.engine, artifacts) == 0
    assert count(harness.engine, outbox_events) == 0, "没有提交就没有 submission.accepted 事件（也无评测入口）"
    assert count(harness.engine, intake_quarantines) == 1

    recorded = harness.quarantine.list_recent(mailbox=MAILBOX)
    assert len(recorded) == 1
    entry = recorded[0]
    assert entry.intake == "email"
    assert entry.reason_code == "not_uniquely_matched"
    assert (entry.mailbox, entry.uidvalidity, entry.uid) == (MAILBOX, 77, 5)
    assert entry.message_id == "<unmatched@example.edu>"
    assert entry.detail, "隔离原因不得为空"
    assert "stranger@example.edu" not in entry.detail, "隔离明细不得写入发件人地址（超范围 PII）"
    assert entry.uidvalidity == 77 and entry.uid == 5

    # checkpoint 与隔离同事务：状态 quarantined、无提交引用、有原因码
    checkpoint = harness.checkpoints.lookup(mailbox=MAILBOX, uidvalidity=77, uid=5)
    assert checkpoint is not None
    assert checkpoint.status == "quarantined"
    assert checkpoint.submission_id is None
    assert checkpoint.reason_code == "not_uniquely_matched"

    # 重复拉取同一封：隔离记录不会变成第二条
    again = harness.service().process_raw(uidvalidity=77, uid=5, raw=raw)
    assert again.status == "skipped"
    assert count(harness.engine, intake_quarantines) == 1


def test_ambiguous_message_is_quarantined_without_guessing(
    engine: Engine, artifact_root: pathlib.Path
) -> None:
    """两条规则指向**不同身份** ⇒ 歧义 ⇒ 隔离，绝不挑一个。"""
    rules = {
        "*": {
            "student_id": STUDENT_ID,
            "course_id": COURSE_ID,
            "section_id": SECTION_ID,
            "question_version_id": QUESTION_ID,
        },
        "alice@example.edu|main.py": {
            "student_id": OTHER_STUDENT_ID,
            "course_id": COURSE_ID,
            "section_id": SECTION_ID,
            "question_version_id": QUESTION_ID,
        },
    }
    harness = build_harness(engine, artifact_root, rules=rules)
    outcome = harness.service().process_raw(
        uidvalidity=8, uid=2, raw=build_message(message_id="<ambiguous@example.edu>")
    )

    assert outcome.status == "quarantined"
    assert outcome.reason_code == "ambiguous_match"
    assert "2 distinct identities" in outcome.detail
    assert count(engine, submissions) == 0
    assert count(engine, outbox_events) == 0
    recorded = harness.quarantine.list_recent(mailbox=MAILBOX)
    assert [entry.reason_code for entry in recorded] == ["ambiguous_match"]

    # 命中两条规则但指向**同一个**身份不算歧义：没有任何猜测发生。
    same_target = {"*": RULES["alice@example.edu"], "alice@example.edu": RULES["alice@example.edu"]}
    tolerant = build_harness(engine, artifact_root, rules=same_target)
    matched = tolerant.service().process_raw(
        uidvalidity=9, uid=3, raw=build_message(message_id="<same-target@example.edu>")
    )
    assert matched.status == "processed", matched.detail


def test_concurrent_duplicate_is_skipped_instead_of_crashing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发重复：唯一约束赢，本次不崩溃、不产生第二份提交。

    为了**确定性地**复现"预检与写入之间的窗口"，这里只把 `lookup` 换成"看不见"（模拟另一个拉取者
    尚未提交），写入路径完全是真实的：真的 checkpoint 行、真的唯一约束、真的 `IntegrityError`
    分支——事务整体回滚后由提交层幂等键认出既有提交。
    """
    raw = build_message(message_id="<racy@example.edu>")
    service = harness.service()
    first = service.process_raw(uidvalidity=1, uid=1, raw=raw)
    assert first.status == "processed"

    calls = {"n": 0}

    def blind(self: EmailCheckpointStore, **kwargs: Any) -> Checkpoint | None:
        calls["n"] += 1
        return None

    monkeypatch.setattr(EmailCheckpointStore, "lookup", blind)
    second = service.process_raw(uidvalidity=1, uid=1, raw=raw)

    assert calls["n"] == 1, "process_raw 的 Message-ID 预检必须真的被绕过，否则没触发并发路径"
    assert second.status == "replayed", second.detail
    assert second.submission_id == first.submission_id, "并发重复必须认出既有提交，而不是新建一份"
    assert count(harness.engine, submissions) == 1
    assert count(harness.engine, email_checkpoints) == 1


def test_conflicting_checkpoint_rolls_back_the_whole_transaction(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """纯冲突路径（幂等键也不同）：checkpoint 唯一约束触发 ⇒ 提交行随事务一起回滚。

    触发方式：把一个**换了 Message-ID、但 UID 相同**的邮件喂给"看不见 checkpoint"的拉取者。
    此时提交层幂等键认不出既有提交，`IntegrityError` 只能冒到邮件适配器，由它按"已处理"跳过——
    这正是"唯一约束冲突绝不是崩溃"的那条分支，而回滚的干净程度由提交行计数证明。

    同时**显式钉住**"内容先于元数据"的既有取舍：第二封用的是**不同内容**，它的字节已经落了 CAS
    （内容寻址写入是独立事务），事务回滚只留下**可回收的孤儿**——没有"有元数据无内容"的坏状态，
    也没有被任何提交引用（`artifact_gc` 按引用计数回收）。
    """
    service = harness.service()
    first = service.process_raw(
        uidvalidity=2, uid=2, raw=build_message(message_id="<first@example.edu>")
    )
    assert first.status == "processed"

    orphan_payload = b"print('orphan')\n"
    orphan_digest = hashlib.sha256(orphan_payload).hexdigest()
    monkeypatch.setattr(EmailCheckpointStore, "lookup", lambda self, **kwargs: None)
    second = service.process_raw(
        uidvalidity=2,
        uid=2,
        raw=build_message(
            message_id="<second@example.edu>", attachments=(("main.py", orphan_payload),)
        ),
    )

    assert second.status == "skipped"
    assert second.reason_code == "checkpoint_exists"
    assert second.detail == "a concurrent pull recorded this message first"
    assert count(harness.engine, submissions) == 1, "冲突事务必须整体回滚，不得留下第二份提交"
    assert count(harness.engine, email_checkpoints) == 1
    # 孤儿内容：artifacts 与 CAS 文件都在（内容先落盘），但没有任何提交引用它
    assert count(harness.engine, artifacts) == 2, "内容先落 CAS：回滚只留可回收孤儿"
    assert harness.store.exists(orphan_digest) is True
    assert harness.store.read(orphan_digest) == orphan_payload
    assert count(harness.engine, submission_files) == 1, "孤儿内容不得被任何提交引用"


def test_email_checkpoint_is_written_in_the_submission_transaction(harness: Harness) -> None:
    """存在提交 ⇒ 必存在指向它的 processed checkpoint（同事务写入）。"""
    outcome = harness.service().process_raw(uidvalidity=3, uid=3, raw=build_message())
    assert outcome.status == "processed"

    every_submission = rows(harness.engine, sa.select(submissions.c.submission_id))
    linked = rows(
        harness.engine,
        sa.select(email_checkpoints.c.submission_id).where(email_checkpoints.c.status == "processed"),
    )
    assert {str(row[0]) for row in linked} == {str(row[0]) for row in every_submission}


# --- 验收标准 3：可执行 / 压缩炸弹被拒并记安全事件 --------------------------------


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        pytest.param("run.sh", ELF_PAYLOAD, "executable_attachment", id="elf-named-shell-script"),
        pytest.param("helper.py", PE_PAYLOAD, "executable_attachment", id="pe-named-python"),
        pytest.param("payload.exe", b"not really an executable", "executable_attachment", id="exe-extension"),
    ],
)
def test_executable_attachment_is_rejected_and_logged(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
    name: str,
    payload: bytes,
    expected: str,
) -> None:
    """可执行附件（按内容嗅探或按扩展名）被拒、进隔离、记结构化安全事件。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    raw = build_message(
        message_id=f"<executable-{name}@example.edu>", attachments=((name, payload),)
    )
    outcome = harness.service().process_raw(uidvalidity=11, uid=4, raw=raw)

    assert outcome.status == "rejected"
    assert outcome.reason_code == expected
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, artifacts) == 0
    assert count(harness.engine, outbox_events) == 0

    recorded = harness.quarantine.list_recent(mailbox=MAILBOX)
    assert [entry.reason_code for entry in recorded] == [expected]
    assert name in recorded[0].detail, "隔离明细应指明是哪一个附件"

    checkpoint = harness.checkpoints.lookup(mailbox=MAILBOX, uidvalidity=11, uid=4)
    assert checkpoint is not None and checkpoint.status == "rejected"
    assert checkpoint.reason_code == expected and checkpoint.submission_id is None

    security_events = [
        record for record in caplog.records if record.name == "autograder_b2.intake.security"
    ]
    assert len(security_events) == 1, "拒绝必须产生恰好一条安全事件"
    payload_fields = security_events[0].__dict__["b2_security_event"]
    assert payload_fields["event"] == "email_intake_rejected"
    assert payload_fields["reason_code"] == expected
    assert payload_fields["mailbox"] == MAILBOX
    assert (payload_fields["uidvalidity"], payload_fields["uid"]) == (11, 4)
    assert payload_fields["attachment_name"] == name
    assert payload_fields["size_bytes"] == len(payload)
    assert payload_fields["class"] in {
        "unsafe_attachment",
        "archive_container",
        "unsupported_attachment",
    }


def test_zip_bomb_is_rejected_by_its_declared_size(harness: Harness) -> None:
    """压缩炸弹：文件很小，但归档**自己声明**的展开量远超预算 ⇒ `archive_bomb`，且从未解压。"""
    bomb = zip_bomb(declared_bytes=8 * 1024 * 1024)
    assert len(bomb) < LIMITS.max_file_bytes, "炸弹文件本身必须小于单文件上限，否则测的是大小闸门"
    with zipfile.ZipFile(io.BytesIO(bomb)) as archive:
        declared = sum(item.file_size for item in archive.infolist())
    assert declared > LIMITS.max_total_bytes, "声明展开量必须超过总预算，否则不构成炸弹"

    outcome = harness.service().process_raw(
        uidvalidity=12,
        uid=6,
        raw=build_message(message_id="<bomb@example.edu>", attachments=(("homework.py", bomb),)),
    )

    assert outcome.status == "rejected"
    assert outcome.reason_code == "archive_bomb"
    assert str(declared) in outcome.detail, "明细应给出归档声明的展开量"
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, artifacts) == 0
    recorded = harness.quarantine.list_recent(mailbox=MAILBOX)
    assert [entry.reason_code for entry in recorded] == ["archive_bomb"]


def test_gzip_bomb_and_plain_gzip_are_both_rejected(harness: Harness) -> None:
    """gzip：声明展开量超限 ⇒ `archive_bomb`；普通小 gzip 也是归档容器 ⇒ `archive_attachment`。"""
    bomb = gzip_payload(b"\x00" * (8 * 1024 * 1024))
    assert len(bomb) < LIMITS.max_file_bytes, "炸弹文件本身必须小于单文件上限"
    assert int.from_bytes(bomb[-4:], "little") > LIMITS.max_total_bytes, "ISIZE 必须超过总预算"
    service = harness.service()

    bombed = service.process_raw(
        uidvalidity=13,
        uid=7,
        raw=build_message(
            message_id="<gzip-bomb@example.edu>", attachments=(("notes.txt.gz", bomb),)
        ),
    )
    assert bombed.status == "rejected"
    assert bombed.reason_code == "archive_bomb"

    plain = service.process_raw(
        uidvalidity=13,
        uid=8,
        raw=build_message(
            message_id="<gzip-plain@example.edu>",
            attachments=(("notes.txt.gz", gzip_payload(b"a short note\n")),),
        ),
    )
    assert plain.status == "rejected"
    assert plain.reason_code == "archive_attachment"
    assert count(harness.engine, submissions) == 0


def test_mislabeled_archive_named_as_python_is_rejected(harness: Harness) -> None:
    """`main.py` 的字节却是 ZIP ⇒ 按**内容**定性为归档并拒绝（声明不作数）。"""
    outcome = harness.service().process_raw(
        uidvalidity=14,
        uid=9,
        raw=build_message(
            message_id="<mislabeled@example.edu>", attachments=(("main.py", small_zip()),)
        ),
    )
    assert outcome.status == "rejected"
    assert outcome.reason_code == "archive_attachment"
    assert "zip container detected by magic bytes" in outcome.detail
    assert count(harness.engine, artifacts) == 0


def test_unsupported_attachment_type_is_rejected(harness: Harness) -> None:
    """允许列表之外的类型（此处的无扩展名文件）被拒，原因码精确。"""
    outcome = harness.service().process_raw(
        uidvalidity=15,
        uid=10,
        raw=build_message(
            message_id="<unsupported@example.edu>", attachments=(("Makefile", b"all:\n"),)
        ),
    )
    assert outcome.status == "rejected"
    assert outcome.reason_code == "unsupported_attachment_type"


def test_attachment_name_traversal_is_rejected(harness: Harness) -> None:
    """附件名是提交内的 `path`，因此与 API 路径受同一套白名单约束（`..` 一律拒绝，不改名）。"""
    outcome = harness.service().process_raw(
        uidvalidity=16,
        uid=11,
        raw=build_message(
            message_id="<traversal@example.edu>", attachments=(("../main.py", SOURCE),)
        ),
    )
    assert outcome.status == "rejected"
    assert outcome.reason_code == "attachment_name_not_allowed"
    assert count(harness.engine, submissions) == 0


def test_oversized_attachment_is_rejected_before_decoding(
    engine: Engine, artifact_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """大小闸门在**解码之前**：解码函数一次都不该被调用（压缩炸弹的内存边界就在这里）。"""
    tiny = IntakeLimits(max_files=4, max_file_bytes=64, max_total_bytes=256)
    harness = build_harness(engine, artifact_root, limits=tiny)

    calls = {"n": 0}
    real_decode = RawAttachment.decode

    def counting_decode(self: RawAttachment) -> bytes:
        calls["n"] += 1
        return real_decode(self)

    monkeypatch.setattr(RawAttachment, "decode", counting_decode)
    outcome = harness.service().process_raw(
        uidvalidity=17,
        uid=12,
        raw=build_message(
            message_id="<oversize@example.edu>", attachments=(("main.py", b"x" * 4096),)
        ),
    )

    assert outcome.status == "rejected"
    assert outcome.reason_code == "attachment_too_large"
    assert calls["n"] == 0, "超过声明上限的附件绝不能被解码"
    assert count(engine, artifacts) == 0

    # 同一封邮件在 API 路径上也是 413 语义（同一组上限）
    assert tiny.max_file_bytes == harness.intake.limits.max_file_bytes
    assert harness.intake.limits is tiny


def test_body_and_attachment_bytes_never_reach_the_quarantine_or_the_logs(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """日志与隔离记录里只有标识、文件名、字节数与原因码——正文与附件原文都不在其中。

    被**接受**的附件当然会进 CAS（那是它该去的地方）；这里扫描的是 `intake_quarantines`、
    `email_checkpoints` 与全程捕获的日志文本。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    service = harness.service()

    # ① 被拒的邮件：正文与附件正文里都埋了唯一标记
    rejected_payload = b"\x7fELF" + ATTACHMENT_MARKER
    rejected = service.process_raw(
        uidvalidity=18,
        uid=13,
        raw=build_message(
            message_id="<redaction-rejected@example.edu>",
            attachments=(("run.sh", rejected_payload),),
        ),
    )
    # ② 被接受的邮件：确认成功路径的日志同样干净
    accepted = service.process_raw(
        uidvalidity=18, uid=14, raw=build_message(message_id="<redaction-accepted@example.edu>")
    )
    assert (rejected.status, accepted.status) == ("rejected", "processed")

    needle_variants = (
        BODY_MARKER,
        ATTACHMENT_MARKER.decode("ascii"),
        base64.b64encode(ATTACHMENT_MARKER).decode("ascii"),
        base64.b64encode(rejected_payload).decode("ascii"),
    )
    log_text = caplog.text
    quarantine_dump = dump_table(harness.engine, intake_quarantines)
    checkpoint_dump = dump_table(harness.engine, email_checkpoints)
    for needle in needle_variants:
        assert needle not in quarantine_dump, f"隔离记录里出现了不该有的内容: {needle}"
        assert needle not in checkpoint_dump, f"checkpoint 里出现了不该有的内容: {needle}"
        assert needle not in log_text, f"日志里出现了不该有的内容: {needle}"

    # 反证：日志与隔离记录确实记下了**允许**的字段（否则上面的扫描可能只是因为"什么都没记"）
    assert "run.sh" in quarantine_dump
    assert "email_intake_rejected" in log_text
    assert "email_intake processed" in log_text


# --- 独立复核打回后的回归用例（D-1 / D-2 / D-3 / D-4） ---------------------------
#
# 这一节的每个用例都直接复刻复核给出的最小复现；它们证明的是**根因**被修掉，而不是特例被补上：
# 闸门已从"枚举坏签名"翻转为"允许列表内的附件必须被证明是纯文本源码"。


#: 复核用来做多态容器的文本前缀（ZIP 预置它之后仍然能被 `zipfile` 打开）。
POLYGLOT_PREFIX = b"# -*- coding: utf-8 -*-\n"
#: 注入标记：放进攻击者可控的头值里，用来证明它不会出现在隔离记录或日志中。
HEADER_INJECTION_MARKER = "injectedheadermarker7d3f"


def mime_part(
    *,
    filename: str,
    payload: bytes,
    disposition: str = "attachment",
    encoding: str = "base64",
    content_type: str = "application/octet-stream",
) -> str:
    """手写一个 MIME 部件：`Content-Disposition` 与传输编码都可被测试任意指定（攻击者视角）。"""
    lines = [f"Content-Type: {content_type}"]
    if disposition:
        lines.append(f'Content-Disposition: {disposition}; filename="{filename}"')
    lines.append(f"Content-Transfer-Encoding: {encoding}")
    body = base64.b64encode(payload).decode("ascii")
    return "\r\n".join(lines) + "\r\n\r\n" + body


def build_raw_message(
    *,
    message_id: str | None,
    parts: Sequence[str],
    sender: str = "alice@example.edu",
) -> bytes:
    """手写一封 multipart 邮件（不经过 `EmailMessage` 的规范化，头值原样上线）。"""
    boundary = "b2-regression-boundary"
    lines = [
        f"From: {sender}",
        "To: submissions@example.edu",
        "Subject: submission",
        "MIME-Version: 1.0",
        f'Content-Type: multipart/mixed; boundary="{boundary}"',
    ]
    if message_id is not None:
        lines.append(f"Message-ID: {message_id}")
    raw = "\r\n".join(lines) + "\r\n\r\n"
    for part in parts:
        raw += f"--{boundary}\r\n{part}\r\n"
    raw += f"--{boundary}--\r\n"
    return raw.encode("utf-8")


def fat_mach_o(*, big_endian: bool) -> bytes:
    """结构正确的 FAT/universal Mach-O：`nfat_arch=1` + 一条指向偏移 4096 的 thin Mach-O。"""
    thin = b"\xcf\xfa\xed\xfe" + b"\x00" * 60
    order = ">" if big_endian else "<"
    magic = b"\xca\xfe\xba\xbe" if big_endian else b"\xbe\xba\xfe\xca"
    offset = 4096
    header = magic + struct.pack(f"{order}I", 1)
    arch = struct.pack(f"{order}iiIII", 0x01000007, 3, offset, len(thin), 12)
    return header + arch + b"\x00" * (offset - len(header) - len(arch)) + thin


def tar_archive() -> bytes:
    """一封真的 tar 归档（`tarfile` 写的，带 POSIX `ustar` 魔数）。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("main.py")
        info.size = len(SOURCE)
        archive.addfile(info, io.BytesIO(SOURCE))
    return buffer.getvalue()


def tar_without_magic() -> bytes:
    """v7 形态的 tar：把 `ustar` 魔数抹掉后，**任何**签名表都不会命中它。"""
    raw = bytearray(tar_archive())
    raw[257:265] = b"\x00" * 8
    return bytes(raw)


def raw_deflate_blob() -> bytes:
    """原始 DEFLATE 流：没有任何容器头，签名表**不可能**命中它。"""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(b"\x00" * (1024 * 1024)) + compressor.flush()


def assert_no_submission_effects(harness: Harness) -> None:
    """被拒的邮件不得产生提交、内容或事件（本模块无评测表，"零事件"即"零评测入口"）。"""
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, artifacts) == 0
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, submission_files) == 0


def security_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """捕获到的全部结构化安全事件的字段字典（按发生顺序）。"""
    return [
        record.__dict__["b2_security_event"]
        for record in caplog.records
        if record.name == "autograder_b2.intake.security"
    ]


def assert_single_security_event(
    caplog: pytest.LogCaptureFixture,
    *,
    reason_code: str,
    attachment_name: str,
    uidvalidity: int,
    uid: int,
) -> dict[str, Any]:
    """恰好一条结构化安全事件，且字段集合固定。"""
    events = [record for record in caplog.records if record.name == "autograder_b2.intake.security"]
    assert len(events) == 1, f"拒绝必须产生恰好一条安全事件，实际 {len(events)}"
    fields = events[0].__dict__["b2_security_event"]
    assert fields["event"] == "email_intake_rejected"
    assert fields["reason_code"] == reason_code
    assert fields["attachment_name"] == attachment_name
    assert (fields["uidvalidity"], fields["uid"]) == (uidvalidity, uid)
    assert fields["mailbox"] == MAILBOX
    return fields


def assert_absent_from_records_and_logs(
    harness: Harness, caplog: pytest.LogCaptureFixture, needles: Sequence[str]
) -> None:
    """攻击者可控文本既不在隔离/checkpoint 的任何列里，也不在日志里。"""
    quarantine_dump = dump_table(harness.engine, intake_quarantines)
    checkpoint_dump = dump_table(harness.engine, email_checkpoints)
    log_text = caplog.text
    for needle in needles:
        assert needle, "空针没有证明力"
        assert needle not in quarantine_dump, "隔离记录里出现了攻击者可控文本"
        assert needle not in checkpoint_dump, "checkpoint 里出现了攻击者可控文本"
        assert needle not in log_text, "日志里出现了攻击者可控文本"


def test_polymorphic_zip_bomb_behind_a_text_prefix_is_rejected(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """D-1 复核最小复现：`# -*- coding: utf-8 -*-` + ZIP 炸弹。

    预置前缀之后 `zipfile.is_zipfile` 依然为真——它仍然是**可打开的归档**，只是魔数不在偏移 0。
    判定必须看内容而不是偏移 0，且声明展开量要按 `zipfile` 的前缀补偿算出来（否则只能笼统地
    报"归档容器"，拿不到 `archive_bomb`）。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    bomb = zip_bomb(declared_bytes=8 * 1024 * 1024)
    payload = POLYGLOT_PREFIX + bomb
    assert zipfile.is_zipfile(io.BytesIO(payload)), "预置前缀的归档仍然是真的、能打开的归档"

    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", verdict
    assert verdict.label == "zip"
    assert verdict.bomb is True, verdict
    assert verdict.declared_size == 8 * 1024 * 1024
    assert verdict.offset == len(POLYGLOT_PREFIX), "归档起点应被报到真实偏移"

    outcome = harness.service().process_raw(
        uidvalidity=20,
        uid=21,
        raw=build_message(
            message_id="<polyglot-bomb@example.edu>", attachments=(("main.py", payload),)
        ),
    )
    assert outcome.status == "rejected"
    assert outcome.reason_code == "archive_bomb"
    assert str(8 * 1024 * 1024) in outcome.detail
    assert_absent_from_records_and_logs(
        harness, caplog, [POLYGLOT_PREFIX.decode("ascii"), base64.b64encode(payload).decode("ascii")]
    )
    assert_single_security_event(
        caplog, reason_code="archive_bomb", attachment_name="main.py", uidvalidity=20, uid=21
    )
    recorded = harness.quarantine.list_recent(mailbox=MAILBOX)
    assert [entry.reason_code for entry in recorded] == ["archive_bomb"]
    assert "at payload offset 24" in recorded[0].detail, "明细应指明归档的真实起点"
    checkpoint = harness.checkpoints.lookup(mailbox=MAILBOX, uidvalidity=20, uid=21)
    assert checkpoint is not None and checkpoint.status == "rejected"
    assert checkpoint.submission_id is None and checkpoint.reason_code == "archive_bomb"


def test_real_tar_is_rejected_and_a_prefixed_tar_is_not_spared(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """真 tar（含 `ustar` 魔数）按归档拒绝；**预置前缀后同样按归档拒绝**。

    上一轮这里断言前缀 tar 落到 `not_plain_text`（当时只有正向判定能接住它）；本轮的"任意偏移"
    结构判定把它提升为精确的 `archive_attachment`——拒绝结论不变，原因码更准。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    service = harness.service()

    plain = tar_archive()
    assert sniff_content(plain, declared_size_limit=LIMITS.max_total_bytes).kind == "archive"
    outcome = service.process_raw(
        uidvalidity=27,
        uid=1,
        raw=build_message(message_id="<tar@example.edu>", attachments=(("main.py", plain),)),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "archive_attachment")

    prefixed = POLYGLOT_PREFIX + tar_archive()
    verdict = sniff_content(prefixed, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", verdict
    assert verdict.offset == len(POLYGLOT_PREFIX)
    outcome = service.process_raw(
        uidvalidity=27,
        uid=2,
        raw=build_message(message_id="<prefixed-tar@example.edu>", attachments=(("main.py", prefixed),)),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "archive_attachment")
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, artifacts) == 0


@pytest.mark.parametrize(
    ("label", "signature"),
    [
        pytest.param("zip", b"PK\x03\x04", id="zip"),
        pytest.param("gzip", b"\x1f\x8b", id="gzip"),
        pytest.param("compress", b"\x1f\x9d", id="compress-Z"),
        pytest.param("zstd", b"\x28\xb5\x2f\xfd", id="zstd"),
        pytest.param("lz4", b"\x04\x22\x4d\x18", id="lz4"),
        pytest.param("xz", b"\xfd7zXZ\x00", id="xz"),
        pytest.param("7z", b"7z\xbc\xaf\x27\x1c", id="7z"),
        pytest.param("rar", b"Rar!\x1a\x07\x00", id="rar"),
    ],
)
def test_container_signature_is_detected_at_a_non_zero_offset(label: str, signature: bytes) -> None:
    """D-1：容器签名在**任意偏移**都要认出来（复核列出的 GAP 的识别面）。

    说明诚实边界：这里用的是**合成头**（签名 + 填充），验证的是"识别能力"，**不**验证任何解压能力
    ——本模块从不解压，也从不落盘附件原文。

    两行参数已按第四轮复核**移出**本组，它们违反"任意偏移组只允许不可能出现在合法文本里的字节"：
    `arj`（`\\x60\\xea` 是合法 UTF-8 文本里可以出现的两字节）与 `bzip2`（10 个可打印字节，
    现在只在偏移 0 认定）。两者的真实归档仍被拒，由
    `test_arj_magic_in_legal_text_is_accepted` / `test_bzip2_magic_inside_source_is_accepted` 覆盖。
    """
    payload = POLYGLOT_PREFIX + signature + b"\x00" * 64
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", f"{label} 未被识别: {verdict}"
    assert verdict.label == label
    assert verdict.offset == len(POLYGLOT_PREFIX)


def test_unenumerated_binary_formats_are_rejected_by_the_positive_text_gate(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """D-1 根治证据：**没有**任何已知签名的压缩/归档形态同样被拒。

    这两份载荷的签名表里都没有条目（一个是原始 DEFLATE 流、一个是抹掉 `ustar` 的 v7 tar），
    因此它们**只能**被正向判定拦下——这就是"安全边界不再是枚举"的机器证明。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    service = harness.service()
    for uid, (name, payload) in enumerate(
        (("raw-deflate", raw_deflate_blob()), ("v7-tar", tar_without_magic())), start=1
    ):
        verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
        assert verdict.kind == "binary", f"{name} 不应命中任何签名表: {verdict}"
        outcome = service.process_raw(
            uidvalidity=22,
            uid=uid,
            raw=build_message(
                message_id=f"<{name}@example.edu>", attachments=(("main.py", payload),)
            ),
        )
        assert outcome.status == "rejected", f"{name}: {outcome.detail}"
        assert outcome.reason_code == "not_plain_text"
    events = security_events(caplog)
    assert len(events) == 2, "两封被拒的邮件各产生恰好一条安全事件"
    for index, event in enumerate(events, start=1):
        assert event["event"] == "email_intake_rejected"
        assert event["reason_code"] == "not_plain_text"
        assert event["attachment_name"] == "main.py"
        assert (event["uidvalidity"], event["uid"]) == (22, index)
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, artifacts) == 0


def test_plain_text_problem_reports_bounded_facts_only() -> None:
    """正向判定的问题描述只含**类别与偏移**，不含任何原始字节。"""
    assert plain_text_problem(SOURCE) is None
    assert plain_text_problem("café\n\ttab\r\n".encode()) is None, "合法 UTF-8 与 制表/换行 必须放行"
    assert plain_text_problem(b"abc\x00def") == "NUL byte at byte offset 3"
    assert plain_text_problem(b"abc\xffdef") == "invalid UTF-8 sequence at byte offset 3"
    assert plain_text_problem(b"ab\x07cd") == "control character at character offset 2"
    assert plain_text_problem(b"ab\x7fcd") == "control character at character offset 2"
    assert "Zl" in (plain_text_problem("a\u2028b".encode()) or "")
    for probe in (b"abc\x00def", b"abc\xffdef", b"ab\x07cd"):
        problem = plain_text_problem(probe)
        assert problem is not None
        assert all(character.isprintable() for character in problem)


#: 危险内容的覆盖面：**没有一个**可以被当成纯文本。合成签名（只有头 + 填充）与真实文件都列在这里，
#: 因为正向判定不看签名——签名表只决定原因码。
_HAZARD_SAMPLES: tuple[tuple[str, bytes], ...] = (
    ("zip", small_zip()),
    ("zip-behind-text-prefix", POLYGLOT_PREFIX + small_zip()),
    ("gzip", gzip_payload(b"payload\n")),
    ("gzip-behind-text-prefix", POLYGLOT_PREFIX + gzip_payload(b"payload\n")),
    ("bzip2", bz2.compress(b"payload\n")),
    ("xz", lzma.compress(b"payload\n")),
    ("tar", tar_archive()),
    ("tar-behind-text-prefix", POLYGLOT_PREFIX + tar_archive()),
    ("raw-deflate", raw_deflate_blob()),
    ("zstd-signature", b"\x28\xb5\x2f\xfd" + b"\x00" * 32),
    ("lz4-signature", b"\x04\x22\x4d\x18" + b"\x00" * 32),
    ("compress-z-signature", b"\x1f\x9d" + b"\x00" * 32),
    ("cab-signature", b"MSCF" + b"\x00" * 32),
    ("ar-deb-signature", b"!<arch>\n" + b"\x00" * 32),
    ("cpio-signature", b"070701" + b"\x00" * 32),
    ("lzh-signature", b"ab-lh5-" + b"\x00" * 32),
    ("elf", ELF_PAYLOAD),
    ("elf-behind-text-prefix", POLYGLOT_PREFIX + ELF_PAYLOAD),
    ("pe", PE_PAYLOAD),
    ("fat-mach-o", fat_mach_o(big_endian=True)),
    ("thin-mach-o", b"\xcf\xfa\xed\xfe" + b"\x00" * 60),
    ("webassembly", b"\x00asm\x01\x00\x00\x00"),
    ("nul-bytes", b"print(1)\n\x00\x00"),
    ("invalid-utf8", b"print('caf\xe9')\n"),
    ("control-character", b"print(1)\n\x0b"),
)


@pytest.mark.parametrize(
    ("label", "payload"), _HAZARD_SAMPLES, ids=[name for name, _ in _HAZARD_SAMPLES]
)
def test_hazardous_content_is_never_classified_as_text(label: str, payload: bytes) -> None:
    """覆盖面表：26 种危险/二进制内容**无一**被判成纯文本（安全边界是正向判定，不是枚举）。"""
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind != "text", f"{label} 被当成了纯文本: {verdict}"
    assert verdict.kind in {"executable", "archive", "binary"}


#: 合法源码的常见形态：**不能**被误拒（否则学生会被理由不清地隔离）。
_LEGITIMATE_TEXT_SAMPLES: tuple[tuple[str, bytes], ...] = (
    ("plain-ascii", b"print('hi')\n"),
    ("utf8-source", "café = 1\n".encode()),
    ("utf8-bom", "\ufeffprint('hi')\n".encode()),
    ("non-breaking-space", "x\u00a0= 1\n".encode()),
    ("ideographic-space", "x\u3000= 1\n".encode()),
    ("crlf-and-tabs", b"def f():\r\n\treturn 1\r\n"),
    ("markdown", b"# Title\n\n- item\n"),
    ("shell", b"#!/bin/sh\necho hi\n"),
)


@pytest.mark.parametrize(
    ("label", "payload"),
    _LEGITIMATE_TEXT_SAMPLES,
    ids=[name for name, _ in _LEGITIMATE_TEXT_SAMPLES],
)
def test_legitimate_text_sources_are_not_falsely_quarantined(
    harness: Harness, label: str, payload: bytes
) -> None:
    """正向判定不能把合法源码拒之门外（BOM、NBSP、全角空格、CRLF 都是真实存在的写法）。"""
    assert plain_text_problem(payload) is None, f"{label} 被误判为非纯文本"
    outcome = harness.service().process_raw(
        uidvalidity=28, uid=1, raw=build_message(message_id=f"<{label}@example.edu>", attachments=(("main.py", payload),))
    )
    assert outcome.status == "processed", f"{label}: {outcome.detail}"
    assert count(harness.engine, submissions) == 1


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        pytest.param("fat-mach-o-big-endian", fat_mach_o(big_endian=True), id="fat-be"),
        pytest.param("fat-mach-o-little-endian", fat_mach_o(big_endian=False), id="fat-le"),
        pytest.param("webassembly", b"\x00asm\x01\x00\x00\x00", id="wasm"),
    ],
)
def test_fat_mach_o_and_webassembly_are_rejected_as_executables(
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
    label: str,
    payload: bytes,
) -> None:
    """D-2：FAT/universal Mach-O（结构正确）与 WebAssembly 都是可执行体，必须被拒并记安全事件。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "executable", f"{label} 未被识别: {verdict}"

    outcome = harness.service().process_raw(
        uidvalidity=23,
        uid=1,
        raw=build_message(
            message_id=f"<{label}@example.edu>", attachments=(("main.py", payload),)
        ),
    )
    assert outcome.status == "rejected", outcome.detail
    assert outcome.reason_code == "executable_attachment"
    assert verdict.label in outcome.detail
    assert_absent_from_records_and_logs(
        harness, caplog, [base64.b64encode(payload).decode("ascii")]
    )
    assert_single_security_event(
        caplog, reason_code="executable_attachment", attachment_name="main.py", uidvalidity=23, uid=1
    )
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, artifacts) == 0


def test_hostile_header_value_is_not_echoed_into_quarantine_or_logs(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """D-3：攻击者可控的头值（20 万字符，且夹带正文标记）不得被回显，日志行长度必须有上限。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    hostile = "x" * 200_000 + HEADER_INJECTION_MARKER
    outcome = harness.service().process_raw(
        uidvalidity=24,
        uid=1,
        raw=build_raw_message(
            message_id="<hostile-header@example.edu>",
            parts=[mime_part(filename="main.py", payload=SOURCE, encoding=hostile)],
        ),
    )

    assert outcome.status == "rejected"
    assert outcome.reason_code == "unsupported_attachment_encoding"
    # 只报**有界事实**：字段名 + 值长度 + 期望支持集
    assert "Content-Transfer-Encoding" in outcome.detail
    assert str(len(hostile)) in outcome.detail
    assert "base64" in outcome.detail
    assert_single_security_event(
        caplog,
        reason_code="unsupported_attachment_encoding",
        attachment_name="main.py",
        uidvalidity=24,
        uid=1,
    )
    assert_absent_from_records_and_logs(
        harness, caplog, [hostile, HEADER_INJECTION_MARKER, "x" * 1000]
    )
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0

    # 日志行长度不能由外部输入决定
    for record in caplog.records:
        assert len(record.getMessage()) <= MAX_LOG_LINE, record.getMessage()[:120]
    fields = assert_single_security_event(
        caplog,
        reason_code="unsupported_attachment_encoding",
        attachment_name="main.py",
        uidvalidity=24,
        uid=1,
    )
    for value in fields.values():
        assert len(str(value)) <= MAX_LOG_FIELD, f"结构化字段未受长度上限约束: {str(value)[:80]}"


def test_message_id_storage_form_is_injective(harness: Harness) -> None:
    """D-4.1：两个**不同**的 `Message-ID` 绝不归一成同一个键（否则第二封会被静默跳过）。

    这两组输入正是"多对一归一化"会踩的坑：内部空格（旧实现会删掉它）与共享长前缀（旧实现会
    把它们截成同一个值）。
    """
    pairs = [
        ("<a b@example.edu>", "<ab@example.edu>"),
        ("<a\x01b@example.edu>", "<ab@example.edu>"),
        ("<" + "a" * 300 + "1@example.edu>", "<" + "a" * 300 + "2@example.edu>"),
    ]
    for first, second in pairs:
        assert first != second
        stored_first = message_id_for_storage(first)
        stored_second = message_id_for_storage(second)
        assert stored_first != stored_second, f"{first!r} 与 {second!r} 归一到同一个键"
        assert stored_first is not None and stored_second is not None
        # 落库形态必须可安全打印（控制字符被转义，不会劈开日志行）
        assert all(character.isprintable() for character in stored_first)
    # 短值（无需转义）原样保留，便于人工核对
    plain = "<plain-short@example.edu>"
    assert message_id_for_storage(plain) == plain

    # 端到端：只差一个内部空格的两封邮件各自成为一份提交，不是"第二封被跳过"
    service = harness.service()
    parts = [mime_part(filename="main.py", payload=SOURCE)]
    first = service.process_raw(
        uidvalidity=25, uid=1, raw=build_raw_message(message_id="<a b@example.edu>", parts=parts)
    )
    second = service.process_raw(
        uidvalidity=25, uid=2, raw=build_raw_message(message_id="<ab@example.edu>", parts=parts)
    )
    assert (first.status, second.status) == ("processed", "processed"), (first.detail, second.detail)
    assert count(harness.engine, submissions) == 2
    assert count(harness.engine, email_checkpoints) == 2
    stored = rows(harness.engine, sa.select(email_checkpoints.c.message_id))
    assert {str(row[0]) for row in stored} == {
        message_id_for_storage("<a b@example.edu>"),
        message_id_for_storage("<ab@example.edu>"),
    }


def test_attachment_disposition_does_not_decide_whether_the_gate_runs(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """D-4.2：`Content-Disposition` 是攻击者可控元数据，不得决定"这个部件要不要过闸门"。

    旧实现在处置方式不是 `attachment`/`inline`/空时**静默丢弃**该部件（`form-data` 会退化成
    `no_attachments`）。现在：带文件名的部件一律收进来过闸门——合法的照收，危险的照拒。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    service = harness.service()

    accepted = service.process_raw(
        uidvalidity=26,
        uid=1,
        raw=build_raw_message(
            message_id="<form-data-text@example.edu>",
            parts=[mime_part(filename="main.py", payload=SOURCE, disposition="form-data")],
        ),
    )
    assert accepted.status == "processed", accepted.detail
    assert rows(
        harness.engine, sa.select(submission_files.c.path)
    ) == [("main.py",)], "带 form-data 处置的附件不能被静默丢掉"

    rejected = service.process_raw(
        uidvalidity=26,
        uid=2,
        raw=build_raw_message(
            message_id="<form-data-elf@example.edu>",
            parts=[mime_part(filename="main.py", payload=ELF_PAYLOAD, disposition="form-data")],
        ),
    )
    assert rejected.status == "rejected"
    assert rejected.reason_code == "executable_attachment"
    assert count(harness.engine, submissions) == 1, "只有第一封邮件成为提交"
    assert_single_security_event(
        caplog, reason_code="executable_attachment", attachment_name="main.py", uidvalidity=26, uid=2
    )


def test_malformed_and_headerless_messages_are_rejected_not_guessed(harness: Harness) -> None:
    """空报文与缺 `Message-ID` 都按"拒绝并记录"处理——**不猜**标识、也不静默丢弃。"""
    service = harness.service()
    empty = service.process_raw(uidvalidity=19, uid=15, raw=b"")
    assert (empty.status, empty.reason_code) == ("rejected", "malformed_message")

    headerless = service.process_raw(
        uidvalidity=19,
        uid=16,
        raw=build_message(message_id=None),
    )
    assert (headerless.status, headerless.reason_code) == ("rejected", "missing_message_id")

    no_attachment = service.process_raw(
        uidvalidity=19,
        uid=17,
        raw=build_message(message_id="<no-attachment@example.edu>", attachments=()),
    )
    assert (no_attachment.status, no_attachment.reason_code) == ("rejected", "no_attachments")

    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, intake_quarantines) == 3
    assert {entry.reason_code for entry in harness.quarantine.list_recent(mailbox=MAILBOX)} == {
        "malformed_message",
        "missing_message_id",
        "no_attachments",
    }


# --- 解析与闸门的直接单元断言（不经过数据库） -------------------------------------


def test_size_gate_uses_the_shared_intake_limits() -> None:
    """闸门用的就是 `IntakeLimits`（与 API 同一形态的上限值），不是另造的一组数字。"""
    limits = IntakeLimits(max_files=1, max_file_bytes=8, max_total_bytes=8)
    message = parse_message(build_message(attachments=(("main.py", b"123456789"),)))
    assert message.message_id == "<message-1@example.edu>"
    assert message.sender == "alice@example.edu"
    assert message.attachments[0].estimated_size == 9
    with pytest.raises(AttachmentRejected) as rejected:
        screen_attachments(message, limits=limits)
    assert rejected.value.reason == "attachment_too_large"

    ok = screen_attachments(message, limits=IntakeLimits(max_files=1, max_file_bytes=64, max_total_bytes=64))
    assert [item.filename for item in ok] == ["main.py"]
    assert ok[0].content == b"123456789"


def test_parsed_message_never_carries_the_body() -> None:
    """解析结果里没有正文字段的落点（结构上就不存在"顺手把正文存下来"的可能）。

    字段集是固定的五项：标识 + 具名部件 + **具名嵌套邮件名** + 计数溢出标志。后两项只带
    **文件名与布尔**，因此"不得静默丢弃嵌套邮件"与"不得物化整封邮件"都没有引入正文字段。
    """
    message = parse_message(build_message(body=f"top secret {BODY_MARKER}"))
    assert [field.name for field in fields(message)] == [
        "message_id",
        "sender",
        "attachments",
        "nested_attachments",
        "attachments_overflow",
    ]
    assert message.nested_attachments == () and message.attachments_overflow is False
    assert BODY_MARKER not in repr(message)


# --- 第二轮独立复核（P1–P8）：可打印容器、嵌套邮件、大小写重名、空文件、单射、空白、日志上界、解析期 DoS


_TAR_BLOCK = 512


def printable_tar(*, member: str = "main.py", payload: bytes = b"print(1)\n") -> bytes:
    """全长**可打印、无 NUL** 但结构合法的 tar。

    数字字段写成"七位八进制 + 空格"、100 字节 name 用空格补齐、magic 区填空格、数据块用空格补齐，
    因此它同时满足"纯文本"与"真 tar"两个条件——这正是正向判定单独挡不住的那一类。
    """
    header = bytearray(b" " * _TAR_BLOCK)
    name = member.encode("ascii")
    header[0 : len(name)] = name
    header[100:108] = b"0000644 "  # mode
    header[108:116] = b"0000000 "  # uid
    header[116:124] = b"0000000 "  # gid
    header[124:136] = f"{len(payload):011o} ".encode()  # size
    header[136:148] = b"00000000000 "  # mtime
    header[148:156] = b" " * 8  # chksum 位置先填空格再算
    header[156:157] = b"0"  # 普通文件
    header[148:156] = f"{sum(header):07o} ".encode()
    return bytes(header) + payload + b" " * (-len(payload) % _TAR_BLOCK)


def tar_member_header(*, name: str, size: int, type_flag: bytes) -> bytes:
    """同样的"可打印"头，用于拼 PAX 扩展头与成员头。"""
    header = bytearray(b" " * _TAR_BLOCK)
    encoded = name.encode("ascii")
    header[0 : len(encoded)] = encoded
    header[100:108] = b"0000644 "
    header[108:116] = b"0000000 "
    header[116:124] = b"0000000 "
    header[124:136] = f"{size:011o} ".encode()
    header[136:148] = b"00000000000 "
    header[148:156] = b" " * 8
    header[156:157] = type_flag
    header[148:156] = f"{sum(header):07o} ".encode()
    return bytes(header)


def pax_record(keyword: str, value: str) -> bytes:
    """PAX 记录（`<总长> <键>=<值>\\n`，长度含自身）。"""
    body = f"{keyword}={value}\n"
    length = len(body) + 2
    while True:
        candidate = f"{length} {body}"
        if len(candidate) == length:
            return candidate.encode()
        length = len(candidate)


def pax_sparse_bomb(*, declared: int = 1099511627776) -> bytes:
    """PAX sparse 炸弹：文件约 2 KB，却**声明**展开 1 TiB（复核给出的形态）。"""
    records = (
        pax_record("GNU.sparse.size", str(declared))
        + pax_record("GNU.sparse.map", "0,512")
        + pax_record("GNU.sparse.name", "main.py")
    )
    pax_block = records + b" " * (-len(records) % _TAR_BLOCK)
    return (
        tar_member_header(name="PaxHeaders/main.py", size=len(records), type_flag=b"x")
        + pax_block
        + tar_member_header(name="main.py", size=_TAR_BLOCK, type_flag=b"0")
        + b" " * _TAR_BLOCK
    )


def test_printable_tar_is_rejected_as_a_container(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """P1 复核最小复现：**全长可打印**（无 NUL）的 tar。

    它满足"纯文本"的全部判据，`tarfile` 也能打开它——只有结构级容器判定能看出来。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    payload = printable_tar()
    assert b"\x00" not in payload, "这条复现的前提就是无 NUL"
    assert all(0x20 <= byte < 0x7F or byte in b"\t\n\r" for byte in payload), "全长可打印"
    assert tarfile.is_tarfile(io.BytesIO(payload)), "它仍然是能被 tarfile 打开的归档"
    assert plain_text_problem(payload) is None, "正向判定单独看不出来"

    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", verdict
    assert verdict.evidence == "structure"
    assert verdict.bomb is False

    outcome = harness.service().process_raw(
        uidvalidity=31,
        uid=1,
        raw=build_message(
            message_id="<printable-tar@example.edu>", attachments=(("main.py", payload),)
        ),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "archive_attachment")
    assert "tar container detected by structure" in outcome.detail
    assert_single_security_event(
        caplog, reason_code="archive_attachment", attachment_name="main.py", uidvalidity=31, uid=1
    )
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, artifacts) == 0
    assert harness.quarantine.list_recent(mailbox=MAILBOX)[0].reason_code == "archive_attachment"


def test_printable_pax_sparse_bomb_is_rejected_as_a_bomb() -> None:
    """P1 复核最小复现之二：可打印的 PAX sparse 炸弹（声明 1 TiB，线上约 2 KB）。"""
    payload = pax_sparse_bomb()
    assert b"\x00" not in payload and plain_text_problem(payload) is None, (
        "the PAX bomb fixture must remain printable text"
    )
    assert tarfile.is_tarfile(io.BytesIO(payload)), "tarfile must recognize the sparse PAX archive"
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        member = archive.next()
        assert member is not None, "tarfile must expose the PAX member"
        assert member.size == 1099511627776, f"tarfile declared size: {member.size}"

    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", verdict
    assert verdict.bomb is True, f"sparse PAX archive should be classified as a bomb: {verdict}"
    assert verdict.declared_size == 1099511627776


def test_printable_container_behind_a_text_prefix_is_still_detected() -> None:
    """P1 + 上一轮的"任意偏移"要求：可打印 tar 加文本前缀后仍然被结构判定抓住。"""
    payload = POLYGLOT_PREFIX + printable_tar()
    assert b"\x00" not in payload
    assert tarfile.is_tarfile(io.BytesIO(payload)) is False, "前缀让 tarfile 自己打不开它"
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", verdict
    assert verdict.offset == len(POLYGLOT_PREFIX), "校验和扫描应报出真实起点"
    assert verdict.evidence == "structure"


def test_printable_zip_container_is_detected_by_structure() -> None:
    """P1 的另一半：`zipfile.is_zipfile` 对**自解压/预置前缀**形态同样返回 True，与签名判定互补。"""
    payload = POLYGLOT_PREFIX + small_zip()
    assert zipfile.is_zipfile(io.BytesIO(payload)), "zipfile 自己认得带前缀的归档"
    assert sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes).kind == "archive"


def ar_archive(*, name: str = "a.py", data: bytes = b"print(1)\n", mtime: int = 1789528951) -> bytes:
    """ar 归档（字段宽度与 GNU/`ar.exe` 一致：16/12/6/6/8/10 + ``\\`\\n``）。

    布局与本机 `ar.exe rcs` 的产物逐字段一致（mtime 由这里固定以便复现）；`bsdtar -tvf/-xf`
    可列出并解出成员。
    """
    header = (
        f"{name + '/':<16}".encode()
        + f"{mtime:<12}".encode()
        + f"{0:<6}".encode()
        + f"{0:<6}".encode()
        + f"{0o100666:<8o}".encode()
        + f"{len(data):<10}".encode()
        + b"`\n"
    )
    assert len(header) == 60, len(header)
    return b"!<arch>\n" + header + data + (b"\n" if len(data) % 2 else b"")


def test_printable_cpio_and_ar_are_detected_at_any_offset() -> None:
    """P1 同类形态：cpio（`070701`/`070707`）与 ar（`!<arch>`）的 magic 全是**可打印 ASCII**。

    结构校验按**变体各自的头形状**做（newc 110 字节十六进制 / odc 76 字节八进制 / ar 60 字节成员头），
    并且**查任意偏移**——预置前缀的可打印 cpio/ar 一样要被抓出来。
    """
    cases = (
        ("cpio", odc_cpio(terminator=False), True),
        ("ar", ar_archive(), True),
        ("cpio", newc_cpio(), False),
        ("cpio", odc_cpio(), False),
    )
    for label, payload, printable in cases:
        if printable:
            assert payload.isascii() and all(
                0x20 <= byte < 0x7F or byte in b"\t\n\r" for byte in payload
            ), f"{label} 前提：全长可打印"
            assert plain_text_problem(payload) is None, f"{label} 正向判定看不出来"
        found = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
        assert (found.kind, found.label) == ("archive", label), f"{label}: {found}"
        verdict = sniff_content(
            POLYGLOT_PREFIX + payload, declared_size_limit=LIMITS.max_total_bytes
        )
        assert verdict.kind == "archive", f"{label}: {verdict}"
        assert verdict.label == label
        assert verdict.offset == len(POLYGLOT_PREFIX)


def odc_cpio(*, name: str = "main.py", data: bytes = b"print(1)\n", terminator: bool = True) -> bytes:
    """odc cpio（`070707`）：76 字节头 = 6 magic + 7×6 八进制 + 11(mtime) + 6(namesize) + 11(filesize)。

    `terminator=False` 时名字按 `namesize` 用**空格**补齐（整份归档因此无 NUL）：布局仍合规，
    `bsdtar -xf` 照样解出 `main.py`——它是"可打印容器"这一类漏判的样本。
    """
    body = name.encode() + (b"\x00" if terminator else b"")
    header = (
        b"070707"
        + b"000000" * 2  # dev, ino
        + b"100666"  # mode
        + b"000000" * 3  # uid, gid, nlink
        + b"000000"  # rdev
        + b"00000000000"  # mtime
        + f"{len(body):06o}".encode()  # namesize
        + f"{len(data):011o}".encode()  # filesize
    )
    assert len(header) == 76, len(header)
    return header + body + data


def newc_cpio(*, name: str = "main.py", data: bytes = b"print(1)\n") -> bytes:
    """newc cpio（`070701`）：110 字节头 = 6 magic + 13 个 8 宽**十六进制**字段。

    与 odc 的形状完全不同——拿 newc 的形状去套 odc（或反之）会永远命中不了。
    """
    fields = (
        "00000000",  # ino
        "000081a4",  # mode
        "00000000",  # uid
        "00000000",  # gid
        "00000001",  # nlink
        "00000000",  # mtime
        f"{len(data):08x}",  # filesize
        "00000000",  # devmajor
        "00000000",  # devminor
        "00000000",  # rdevmajor
        "00000000",  # rdevminor
        f"{len(name) + 1:08x}",  # namesize（含结尾 NUL）
        "00000000",  # check
    )
    header = b"070701" + "".join(fields).encode("ascii")
    assert len(header) == 110, len(header)
    body = name.encode() + b"\x00" + data
    return header + body + b"\x00" * (-len(body) % 4)


def cab_archive(*, data: bytes = b"print(1)\n") -> bytes:
    """最小 CAB 头（二进制：三个保留字段为 0、声明大小落在文件内）。"""
    size = 36 + len(data)
    header = (
        b"MSCF"
        + b"\x00" * 4  # reserved1
        + size.to_bytes(4, "little")  # cbCabinet
        + b"\x00" * 4  # reserved2
        + (36).to_bytes(4, "little")  # coffFiles
        + b"\x00" * 4  # reserved3
        + b"\x03\x01\x00\x00"  # version + 两个计数
        + b"\x00" * 8
    )
    assert len(header) == 36, len(header)
    return header + data


#: 第三轮复核指出的形态：前缀里放一个**真签名字面量**（但后面没有合法结构），让"只认第一处"的
#: 实现永远看不到后面那个真归档。每种前缀都配"真容器"跑一遍，避免同类漏洞换个前缀再漏一次。
#: 全部前缀都**无 NUL**——否则整份载荷会被正向判定先行拒掉，测不到容器识别本身。
PREFIX_CASES: tuple[tuple[str, bytes], ...] = (
    ("plain-text", POLYGLOT_PREFIX),
    ("ar-magic-decoy", b"# ar format notes\n!<arch>\nthis is not a member header\n"),
    ("odc-magic-decoy", b"# cpio notes\n070707 not a header either\n"),
    ("newc-magic-decoy", b"# cpio notes\n070701 not a header either\n"),
    ("cab-magic-decoy", b"# cab notes\nMSCF not a cabinet\n"),
)

#: 含 NUL 的前缀（tar 的魔数必然含 NUL，因此它只能以这种形态当"诱饵"）：整份载荷不再算纯文本，
#: 会被正向判定拒掉——与容器识别无关，但同样必须"被拒"，所以单独钉一条。
NUL_PREFIX = b"# note\x00\nustar\x0000 not a header\n"


@pytest.mark.parametrize(("label", "prefix"), PREFIX_CASES, ids=[name for name, _ in PREFIX_CASES])
def test_real_ar_behind_a_decoy_ar_magic_is_rejected(
    harness: Harness, label: str, prefix: bytes
) -> None:
    """第三轮复核的 HIGH 漏判回归：前缀里的 `!<arch>\\n` 诱饵**不得**掩盖后面的真 ar。

    ar 全长可打印、无 NUL，因此躲得过正向判定；只有"遍历所有出现位置 + 逐个校验成员头"才拦得住。
    断言落在端到端（真库 + 真 CAS 的提交数/隔离数/事件数）。
    """
    payload = prefix + ar_archive()
    assert b"\x00" not in payload, "前提：整份载荷无 NUL（正因如此才躲得过正向判定）"

    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", f"{label}: {verdict}"
    assert verdict.label == "ar"
    assert verdict.offset == len(prefix), "必须落在真归档的起点，而不是诱饵"

    outcome = harness.service().process_raw(
        uidvalidity=41,
        uid=1,
        raw=build_message(
            message_id=f"<{label}@example.edu>", attachments=(("solution.py", payload),)
        ),
    )
    assert outcome.status == "rejected", f"{label}: {outcome.detail}"
    assert outcome.reason_code == "archive_attachment"
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, artifacts) == 0
    assert count(harness.engine, outbox_events) == 0
    assert harness.quarantine.list_recent(mailbox=MAILBOX)[0].reason_code == "archive_attachment"


@pytest.mark.parametrize(("label", "prefix"), PREFIX_CASES, ids=[name for name, _ in PREFIX_CASES])
def test_real_odc_cpio_is_detected_at_any_offset(
    harness: Harness, label: str, prefix: bytes
) -> None:
    """真 odc（`070707`，76 字节头）必须按**它自己的**头形状被认出来，且不受前缀影响。

    真实 odc 的成员名以 NUL 结尾，有些变体整体不是纯文本；这里断言的是"被认成容器"
    （`label == "cpio"` 而不是落到 `not_plain_text`），端到端只要"被拒"即可。
    """
    assert sniff_content(odc_cpio(), declared_size_limit=LIMITS.max_total_bytes).label == "cpio"

    payload = prefix + odc_cpio()
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", f"{label}: {verdict}"
    assert verdict.label == "cpio"

    outcome = harness.service().process_raw(
        uidvalidity=42,
        uid=1,
        raw=build_message(
            message_id=f"<odc-{label}@example.edu>", attachments=(("solution.py", payload),)
        ),
    )
    assert outcome.status == "rejected", f"{label}: {outcome.detail}"
    assert outcome.reason_code in {"archive_attachment", "archive_bomb"}
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0


@pytest.mark.parametrize(
    ("label", "prefix"),
    [
        pytest.param("plain-text", POLYGLOT_PREFIX, id="plain-text"),
        pytest.param("odc-magic-decoy", b"# cpio notes\n070707 not a header\n", id="odc-decoy"),
        pytest.param("newc-magic-decoy", b"# cpio notes\n070701 not a header\n", id="newc-decoy"),
    ],
)
def test_printable_nul_free_odc_behind_a_decoy_is_rejected(
    harness: Harness, label: str, prefix: bytes
) -> None:
    """可打印的 odc 变体（名字按 `namesize` 用空格补齐 ⇒ 无 NUL）也必须被拒。

    `bsdtar` 能列出并解出这个变体（`-xf` 得到 `main.py`），因此它算真归档；它没有 NUL，
    正向判定放行，只有结构校验接得住。
    """
    payload = prefix + odc_cpio(terminator=False)
    assert b"\x00" not in payload
    assert plain_text_problem(payload) is None, "正向判定看不出来"

    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind == "archive", f"{label}: {verdict}"

    outcome = harness.service().process_raw(
        uidvalidity=43,
        uid=1,
        raw=build_message(
            message_id=f"<odc-nul-free-{label}@example.edu>",
            attachments=(("solution.py", payload),),
        ),
    )
    assert outcome.status == "rejected", f"{label}: {outcome.detail}"
    assert outcome.reason_code == "archive_attachment"
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, intake_quarantines) == 1


def test_printable_container_magic_decoys_do_not_mask_the_real_container() -> None:
    """把"只认第一处"一次性钉住：ar / cpio(newc,odc) / CAB 的诱饵前缀都不得掩盖真容器。

    tar 不在此列：它的魔数必然含 NUL，因此"tar 魔数诱饵"本身就会让整份载荷不再算纯文本
    （由 `test_nul_containing_prefix_is_rejected_too` 覆盖）；tar 容器本身由校验和扫描兜住。
    """
    cases = (
        ("ar", ar_archive(), b"!<arch>\nnot a member\n"),
        ("cpio", newc_cpio(), b"070701not-a-header"),
        ("cpio", odc_cpio(terminator=False), b"070707not-a-header"),
        ("cab", cab_archive(), b"MSCFnot-a-cabinet"),
    )
    for label, container, decoy in cases:
        payload = POLYGLOT_PREFIX + decoy + b"\n" + container
        verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
        assert verdict.kind == "archive", f"{label}: {verdict}"
        assert verdict.offset == len(POLYGLOT_PREFIX) + len(decoy) + 1, f"{label} 起点不对"


@pytest.mark.parametrize(
    "container_name", ["ar", "printable-tar", "odc-nul-free"], ids=["ar", "tar", "odc"]
)
def test_nul_containing_prefix_is_rejected_too(harness: Harness, container_name: str) -> None:
    """含 NUL 的前缀（例如 `ustar\\0` 诱饵）同样必须"被拒"——只是拒它的那一层不同。"""
    containers = {
        "ar": ar_archive(),
        "printable-tar": printable_tar(),
        "odc-nul-free": odc_cpio(terminator=False),
    }
    payload = NUL_PREFIX + containers[container_name]
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert verdict.kind in {"archive", "binary"}, f"{container_name}: {verdict}"

    outcome = harness.service().process_raw(
        uidvalidity=45,
        uid=1,
        raw=build_message(
            message_id=f"<nul-prefix-{container_name}@example.edu>",
            attachments=(("solution.py", payload),),
        ),
    )
    assert outcome.status == "rejected", f"{container_name}: {outcome.detail}"
    assert outcome.reason_code in {"archive_attachment", "archive_bomb", "not_plain_text"}
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0


def test_submission_row_carries_provenance_for_both_entries(harness: Harness) -> None:
    """迁移 0008 的契约：提交行必须带 `correlation_id` + 按 `intake` 取值判形状的显式 provenance。

    这条同时也是"冻结信封的 provenance 真的落了库"的证据——在此之前 `EmailProvenance` 只是
    构造出来就被丢掉，没有任何地方能回答"这份提交来自哪封邮件"。
    """
    api_correlation = str(uuid.uuid4())
    api_result = harness.intake.accept(
        api_payload(idempotency_key="api-provenance"),
        principal=student_principal(),
        correlation_id=api_correlation,
    )
    email_outcome = harness.service().process_raw(uidvalidity=1, uid=1, raw=build_message())
    assert email_outcome.status == "processed", email_outcome.detail
    assert email_outcome.submission_id is not None

    api_row = submission_row(harness.engine, api_result.submission_id)
    assert str(api_row["correlation_id"]) == api_correlation
    assert api_row["request_id"] == api_correlation
    assert api_row["mailbox"] is None
    assert api_row["uidvalidity"] is None and api_row["uid"] is None
    assert api_row["message_id"] is None

    email_row = submission_row(harness.engine, email_outcome.submission_id)
    assert email_row["request_id"] is None
    assert email_row["mailbox"] == MAILBOX
    assert (email_row["uidvalidity"], email_row["uid"]) == (1, 1)
    assert email_row["message_id"] == "<message-1@example.edu>"
    # correlation_id 是每封邮件各自的服务端 UUID（I7），并且是 NOT NULL
    assert uuid.UUID(str(email_row["correlation_id"]))
    assert str(email_row["correlation_id"]) != api_correlation


def test_non_checkpoint_integrity_errors_are_not_swallowed(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**只有** checkpoint 唯一约束冲突才算"并发重复"；别的完整性错误必须响亮地失败。

    这条回归来自真实事故：迁移 0008 落地而 `db/schema.py` 镜像还没跟上时，提交行插入违反
    `correlation_id NOT NULL`，旧实现把任何 `IntegrityError` 都当成"并发重复"，于是每封邮件都被
    静默丢弃（`status="skipped"`、无提交、无隔离、无安全事件）。这里用一个**真实的**库层完整性
    错误复现：把 provenance 列清空 ⇒ 触发 `ck_submissions_provenance_shape`。
    """
    assert _is_checkpoint_conflict(IntegrityError("stmt", {}, _DiagError("uq_something_else"))) is False
    assert _is_checkpoint_conflict(IntegrityError("stmt", {}, Exception("boom"))) is False
    assert (
        _is_checkpoint_conflict(
            IntegrityError("stmt", {}, _DiagError("uq_email_checkpoints_mailbox_uidvalidity_uid"))
        )
        is True
    )

    monkeypatch.setattr(service_module, "_provenance_columns", lambda provenance: {})
    with pytest.raises(IntegrityError) as failure:
        harness.service().process_raw(uidvalidity=1, uid=1, raw=build_message())

    assert "ck_submissions_provenance_shape" in str(failure.value)
    assert _is_checkpoint_conflict(failure.value) is False, "CHECK 违反绝不能被当成并发重复"
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, email_checkpoints) == 0


def test_plain_text_with_container_literals_is_still_accepted(harness: Harness) -> None:
    """不得引入误报：文本恰好含 `!<arch>\\n` / `070707` / `MSCF` 字面量而其后无真归档 ⇒ 收下。"""
    samples = (
        ("ar-literal-at-0", b"!<arch>\ndocumentation of the ar format\n"),
        ("ar-literal-mid", b"# notes\n!<arch>\nstill not an archive\n"),
        ("ar-literal-at-end", b"trailing literal: !<arch>\n"),
        ("odc-literal", b"# notes\n070707 is the odc magic\n"),
        ("newc-literal", b"# notes\n070701 is the newc magic\n"),
        ("cab-literal", b"# notes\nMSCF is the cabinet magic\n"),
    )
    service = harness.service()
    for uid, (label, payload) in enumerate(samples, start=1):
        verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
        assert verdict.kind == "text", f"{label}: {verdict}"
        outcome = service.process_raw(
            uidvalidity=44,
            uid=uid,
            raw=build_message(
                message_id=f"<{label}@example.edu>", attachments=(("notes.py", payload),)
            ),
        )
        assert outcome.status == "processed", f"{label}: {outcome.detail}"

    assert count(harness.engine, submissions) == len(samples)
    assert count(harness.engine, intake_quarantines) == 0
    assert count(harness.engine, outbox_events) == len(samples)


def test_printable_tar_behind_octal_decoys_is_still_detected() -> None:
    """候选扫描**不设任意上限**：大量八进制诱饵也藏不住一个可打印 tar。

    上限会给"用诱饵把真头推到上限之外"留空间；工作量与输入长度线性，因此这里只用一个宽松的
    时间上界把"线性"这件事钉住（实测约 0.2 s，上界 10 s）。
    """
    decoys = b"0000640 " * 20_000
    payload = decoys + printable_tar()
    started = time.perf_counter()
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    elapsed = time.perf_counter() - started
    assert verdict.kind == "archive", verdict
    assert verdict.offset == len(decoys)
    assert elapsed < 10.0, f"结构判定应当与输入线性，实测 {elapsed:.2f}s"


def test_nested_message_attachment_is_not_silently_dropped(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """P2：具名 `message/rfc822` 部件既不能"算没有附件"，也不能在别的部件旁边无声消失。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    nested_part = (
        "Content-Type: message/rfc822\r\n"
        'Content-Disposition: attachment; filename="forwarded.eml"\r\n'
        "\r\n"
        "From: alice@example.edu\r\n"
        "Message-ID: <inner@example.edu>\r\n"
        "Subject: fwd\r\n"
        "\r\n"
        "inner body\r\n"
    )
    service = harness.service()

    # ① 只有它一个具名部件：不能说"没有附件"（内容不实），必须精确说是嵌套邮件
    only = service.process_raw(
        uidvalidity=32,
        uid=1,
        raw=build_raw_message(message_id="<nested-only@example.edu>", parts=[nested_part]),
    )
    assert (only.status, only.reason_code) == ("rejected", "nested_message_attachment")
    assert "forwarded.eml" in only.detail
    assert "no named attachment" not in only.detail
    assert_single_security_event(
        caplog,
        reason_code="nested_message_attachment",
        attachment_name="forwarded.eml",
        uidvalidity=32,
        uid=1,
    )

    # ② 与一个安全部件并存：整封被拒（而不是静默丢掉嵌套的那一件、照常收下另一个）
    both = service.process_raw(
        uidvalidity=32,
        uid=2,
        raw=build_raw_message(
            message_id="<nested-with-safe@example.edu>",
            parts=[mime_part(filename="main.py", payload=SOURCE), nested_part],
        ),
    )
    assert (both.status, both.reason_code) == ("rejected", "nested_message_attachment")
    assert count(harness.engine, submissions) == 0, "不得只收下安全部件而丢掉嵌套邮件"
    assert count(harness.engine, artifacts) == 0
    assert count(harness.engine, outbox_events) == 0
    assert {entry.reason_code for entry in harness.quarantine.list_recent(mailbox=MAILBOX)} == {
        "nested_message_attachment"
    }


def test_attachment_names_are_compared_case_insensitively(harness: Harness) -> None:
    """P3：`main.py` 与 `MAIN.PY` 会在大小写不敏感文件系统上互相覆盖 ⇒ 判重必须不区分大小写。"""
    outcome = harness.service().process_raw(
        uidvalidity=33,
        uid=1,
        raw=build_raw_message(
            message_id="<case-duplicate@example.edu>",
            parts=[
                mime_part(filename="main.py", payload=b"a = 1\n"),
                mime_part(filename="MAIN.PY", payload=b"b = 2\n"),
            ],
        ),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "duplicate_attachment_name")
    assert "case-insensitively" in outcome.detail
    assert count(harness.engine, submissions) == 0


def test_empty_allowlisted_attachment_is_accepted(harness: Harness) -> None:
    """P4：空的 `__init__.py` 是合法提交内容，**不得**让整封邮件失败。"""
    outcome = harness.service().process_raw(
        uidvalidity=34,
        uid=1,
        raw=build_raw_message(
            message_id="<empty-init@example.edu>",
            parts=[mime_part(filename="__init__.py", payload=b"")],
        ),
    )
    assert outcome.status == "processed", outcome.detail
    assert count(harness.engine, submissions) == 1
    stored = rows(
        harness.engine,
        sa.select(submission_files.c.path, artifacts.c.size_bytes).select_from(
            submission_files.join(artifacts, submission_files.c.artifact_id == artifacts.c.artifact_id)
        ),
    )
    assert [tuple(row) for row in stored] == [("__init__.py", 0)]

    # 同一段落库、同一语义：API 路径也接受空文件（契约的 `content_base64` 没有 minLength），
    # 因此"空 `.py` 可以提交"在两条入口上是一致的。
    api_result = harness.intake.accept(
        api_payload(files=(("__init__.py", b""),), idempotency_key="api-empty"),
        principal=student_principal(),
        correlation_id=str(uuid.uuid4()),
    )
    assert submission_row(harness.engine, api_result.submission_id)["state"] == "validated"
    assert harness.store.read(hashlib.sha256(b"").hexdigest()) == b""


def test_message_id_truncation_cannot_collide_with_a_short_value(harness: Harness) -> None:
    """P5 复核反例：长 `Message-ID` 与"等于它落库形态"的那个字符串不得塌成同一个键。"""
    long_raw = "<" + "a" * 296 + "@example.edu>"
    stored_long = message_id_for_storage(long_raw)
    assert stored_long is not None
    assert len(stored_long) > MAX_MESSAGE_ID_LENGTH, (
        "长值必须落在比上限更长的形态里，才与'原样保留'的短值集合不相交（单射的必要条件）"
    )
    assert message_id_for_storage(stored_long) != stored_long, "长形态不得自映射"

    service = harness.service()
    parts = [mime_part(filename="main.py", payload=SOURCE)]
    first = service.process_raw(
        uidvalidity=35, uid=1, raw=build_raw_message(message_id=long_raw, parts=parts)
    )
    second = service.process_raw(
        uidvalidity=35, uid=2, raw=build_raw_message(message_id=stored_long, parts=parts)
    )
    assert (first.status, second.status) == ("processed", "processed"), (first.detail, second.detail)
    assert count(harness.engine, submissions) == 2, "两封不同的邮件必须各自成一份提交"
    assert count(harness.engine, email_checkpoints) == 2


def test_form_feed_is_plain_text_but_vertical_tab_is_not(harness: Harness) -> None:
    """P6：`\\x0c` 被 Python 当空白（`compile` 通过）⇒ 放行；`\\x0b` 不被接受 ⇒ 拒绝。

    顺带证明"放宽一个空白字符"没有重新打开 P1：文本 + 容器仍然被拒。
    """
    assert plain_text_problem(b"x = 1\x0c") is None
    compile("x = 1\x0c", "<form-feed>", "exec")  # 复核的事实依据：Python 接受换页符
    assert plain_text_problem(b"x = 1\x0b") == "control character at character offset 5"

    service = harness.service()
    accepted = service.process_raw(
        uidvalidity=36,
        uid=1,
        raw=build_raw_message(
            message_id="<form-feed@example.edu>",
            parts=[mime_part(filename="main.py", payload=b"x = 1\x0c")],
        ),
    )
    assert accepted.status == "processed", accepted.detail
    rejected = service.process_raw(
        uidvalidity=36,
        uid=2,
        raw=build_raw_message(
            message_id="<vertical-tab@example.edu>",
            parts=[mime_part(filename="main.py", payload=b"x = 1\x0b")],
        ),
    )
    assert (rejected.status, rejected.reason_code) == ("rejected", "not_plain_text")

    # P1 没有被重新打开：换页符 + 可打印 tar 仍然按容器拒绝
    tunnel = b"x = 1\x0c" + printable_tar()
    assert sniff_content(tunnel, declared_size_limit=LIMITS.max_total_bytes).kind == "archive"


def test_security_log_line_includes_its_prefix_within_the_cap(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """P7：`MAX_LOG_LINE` 约束的是**含前缀的完整日志行**，不留"前缀另算"的模糊地带。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    long_name = "a" * 190 + ".sh"
    outcome = harness.service().process_raw(
        uidvalidity=37,
        uid=1,
        raw=build_message(
            message_id="<log-cap@example.edu>", attachments=((long_name, ELF_PAYLOAD),)
        ),
    )
    assert outcome.status == "rejected"
    records = [
        record for record in caplog.records if record.name == "autograder_b2.intake.security"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith("email_intake_security ")
    assert len(message) <= MAX_LOG_LINE, "含前缀的整行也必须在上限内"


def test_flood_of_tiny_parts_stops_at_the_attachment_limit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P8：具名部件在**遍历过程中**计数，超限即停——不先把整封物化再判断。"""
    parts = [mime_part(filename=f"f{index}.py", payload=b"x = 1\n") for index in range(5000)]
    raw = build_raw_message(message_id="<flood@example.edu>", parts=parts)

    visited = {"count": 0}
    real_get_filename = Message.get_filename

    def counting_get_filename(self: Message) -> str | None:
        # `_collect_parts` 每看一个部件就取一次名字，因此这个计数就是"被检查过的部件数"
        # （比给 `walk` 打桩更直接：嵌套生成器会把同一部件重复 yield，计数会翻倍）。
        visited["count"] += 1
        return real_get_filename(self)

    monkeypatch.setattr(Message, "get_filename", counting_get_filename)
    message = parse_message(raw, max_attachments=LIMITS.max_files)

    assert len(message.attachments) <= LIMITS.max_files + 1, "物化的具名部件数必须被上限界定"
    assert message.attachments_overflow is True
    assert visited["count"] <= LIMITS.max_files + 2, "遍历必须在超限时立即停止"
    monkeypatch.undo()

    outcome = harness.service().process_raw(uidvalidity=38, uid=1, raw=raw)
    assert (outcome.status, outcome.reason_code) == ("rejected", "too_many_attachments")
    assert "stopped at the limit" in outcome.detail
    assert count(harness.engine, submissions) == 0


# --- 第四轮独立复核：可打印签名的误报（合法提交被隔离）与上限 off-by-two -----------------

#: 复核给出的精确误报载荷：`\x60` 是反引号、`\xea` 是 U+A000–U+AFFF 的合法 UTF-8 首字节。
ARJ_FALSE_POSITIVE_TEXT = "See the code point \u0060\uA000 for YI SYLLABLE IT.\n".encode()
#: 复核给出的精确误报载荷：源码里的 bzip2 魔数字面量。
BZIP2_FALSE_POSITIVE_SOURCE = b"MAGIC = b'BZh91AY&SY'\n"


def test_arj_magic_in_legal_text_is_accepted(harness: Harness) -> None:
    """`\\x60\\xea` 是**合法 UTF-8 文本**里可以出现的两字节，不能当"任意偏移即定论"的签名。

    它已从该组移除：真 ARJ 的压缩体必然含 NUL/控制字节，由正向判定拒绝（原因码 `not_plain_text`）。
    """
    assert plain_text_problem(ARJ_FALSE_POSITIVE_TEXT) is None, "它确实是合法纯文本"
    verdict = sniff_content(ARJ_FALSE_POSITIVE_TEXT, declared_size_limit=LIMITS.max_total_bytes)
    assert (verdict.kind, verdict.label) == ("text", "utf-8 text")

    service = harness.service()
    accepted = service.process_raw(
        uidvalidity=51,
        uid=1,
        raw=build_message(
            message_id="<arj-text@example.edu>",
            attachments=(("notes.py", ARJ_FALSE_POSITIVE_TEXT),),
        ),
    )
    assert accepted.status == "processed", accepted.detail
    assert count(harness.engine, submissions) == 1
    assert count(harness.engine, intake_quarantines) == 0
    assert count(harness.engine, outbox_events) == 1

    # 真的 ARJ 形态（二进制体）仍然被拒——只是原因码退化为"非纯文本"
    rejected = service.process_raw(
        uidvalidity=51,
        uid=2,
        raw=build_message(
            message_id="<arj-binary@example.edu>",
            attachments=(("main.py", b"\x60\xea" + b"\x00" * 16 + b"junk"),),
        ),
    )
    assert (rejected.status, rejected.reason_code) == ("rejected", "not_plain_text")
    assert count(harness.engine, submissions) == 1, "第二封不得产生提交"


def test_bzip2_magic_inside_source_is_accepted(harness: Harness) -> None:
    """`BZh<级别>1AY&SY` 是 10 个**可打印**字节，只**在偏移 0** 才算容器。

    源码/文档里出现这串字面量（压缩原理课、格式文档）不该让学生被隔离；真 `.bz2` 仍被拒绝。
    """
    verdict = sniff_content(BZIP2_FALSE_POSITIVE_SOURCE, declared_size_limit=LIMITS.max_total_bytes)
    assert (verdict.kind, verdict.label) == ("text", "utf-8 text")
    gapped = b"BZh9 and later 1AY&SY in the same file\n"
    assert sniff_content(gapped, declared_size_limit=LIMITS.max_total_bytes).kind == "text"

    service = harness.service()
    for uid, payload in ((1, BZIP2_FALSE_POSITIVE_SOURCE), (2, gapped)):
        outcome = service.process_raw(
            uidvalidity=52,
            uid=uid,
            raw=build_message(
                message_id=f"<bzip2-text-{uid}@example.edu>", attachments=(("compress.py", payload),)
            ),
        )
        assert outcome.status == "processed", outcome.detail
    assert count(harness.engine, submissions) == 2
    assert count(harness.engine, intake_quarantines) == 0

    # 真 .bz2 仍必须被拒（它以魔数开头，且体是二进制）
    real = bz2.compress(b"payload\n")
    assert sniff_content(real, declared_size_limit=LIMITS.max_total_bytes).label == "bzip2"
    rejected = service.process_raw(
        uidvalidity=52,
        uid=3,
        raw=build_message(message_id="<real-bz2@example.edu>", attachments=(("main.py", real),)),
    )
    assert (rejected.status, rejected.reason_code) == ("rejected", "archive_attachment")
    assert count(harness.engine, submissions) == 2


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        pytest.param("octal: " + "070707" + "1" * 70, b"octal: " + b"070707" + b"1" * 70, id="odc-digit-run"),
        pytest.param(
            "odc-digit-name",
            b"070707" + b"0" * 53 + b"000001" + b"0" * 11 + b"0",
            id="odc-digit-name",
        ),
        pytest.param(
            "newc-digit-name",
            b"070701" + b"0" * 88 + b"00000001" + b"0" * 16 + b"0",
            id="newc-digit-name",
        ),
    ],
)
def test_long_digit_blocks_are_not_mistaken_for_cpio(
    harness: Harness, label: str, payload: bytes
) -> None:
    """cpio 的定宽字段可以被"长数字块"文本满足 ⇒ 名字字段必须像**文件名**而不是数字续写。

    判据（廉价、结构性）：名字字段必须落在文件内、是可打印 ASCII、且不能全由该变体的数字字母表
    组成（真 cpio 的名字是 `a.py`、`TRAILER!!!` 这类）。
    """
    assert plain_text_problem(payload) is None, f"{label} 应当是合法纯文本"
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert (verdict.kind, verdict.label) == ("text", "utf-8 text"), f"{label}: {verdict}"

    outcome = harness.service().process_raw(
        uidvalidity=53,
        uid=1,
        raw=build_message(
            message_id=f"<cpio-fp-{label}@example.edu>", attachments=(("notes.py", payload),)
        ),
    )
    assert outcome.status == "processed", f"{label}: {outcome.detail}"
    assert count(harness.engine, submissions) == 1
    assert count(harness.engine, intake_quarantines) == 0


def test_true_containers_are_still_rejected_after_the_false_positive_fixes(
    harness: Harness,
) -> None:
    """误报修复**不得**放宽真容器的拒绝：ar / tar / cpio(newc,odc) / zip 炸弹 / ELF 逐条复验。"""
    containers = {
        "ar": ar_archive(),
        "tar": tar_archive(),
        "cpio-newc": newc_cpio(),
        "cpio-odc": odc_cpio(terminator=False),
        "zip-bomb": zip_bomb(declared_bytes=8 * 1024 * 1024),
        "elf": ELF_PAYLOAD,
    }
    service = harness.service()
    for uid, (label, payload) in enumerate(containers.items(), start=1):
        verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
        assert verdict.kind in {"archive", "executable"}, f"{label}: {verdict}"
        outcome = service.process_raw(
            uidvalidity=54,
            uid=uid,
            raw=build_message(
                message_id=f"<still-rejected-{label}@example.edu>",
                attachments=(("main.py", payload),),
            ),
        )
        assert outcome.status == "rejected", f"{label}: {outcome.detail}"
        assert outcome.reason_code in {"archive_attachment", "archive_bomb", "executable_attachment"}
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, intake_quarantines) == len(containers)


@pytest.mark.parametrize("exact", [18, 19, 20], ids=["limit-mod-0", "limit-mod-1", "limit-mod-2"])
def test_file_exactly_at_the_size_limit_is_accepted(
    engine: Engine, artifact_root: pathlib.Path, exact: int
) -> None:
    """上限 off-by-two：`(len//4)*3` 对 `n ≡ 1 (mod 3)` 高估 2 字节，会把**恰好等于上限**的文件拒掉。

    三种模类都覆盖：`n == 上限` 收下、`n == 上限 + 1` 拒为 `attachment_too_large`；API 路径同理。
    """
    limits = IntakeLimits(max_files=2, max_file_bytes=exact, max_total_bytes=exact * 3)
    harness = build_harness(engine, artifact_root, limits=limits)
    service = harness.service()

    at_limit = service.process_raw(
        uidvalidity=55,
        uid=1,
        raw=build_message(
            message_id=f"<at-limit-{exact}@example.edu>",
            attachments=(("main.py", b"x" * exact),),
        ),
    )
    assert at_limit.status == "processed", f"恰好等于上限必须收下：{at_limit.detail}"
    over = service.process_raw(
        uidvalidity=55,
        uid=2,
        raw=build_message(
            message_id=f"<over-limit-{exact}@example.edu>",
            attachments=(("main.py", b"x" * (exact + 1)),),
        ),
    )
    assert over.status == "rejected", over.detail
    assert over.reason_code == "attachment_too_large"
    assert f"{exact + 1} bytes" in over.detail, over.detail

    # API 路径同一组上限：`content_base64` 的估算也必须精确（同一个公式的两处实现）
    api_ok = harness.intake.accept(
        api_payload(files=(("main.py", b"x" * exact),), idempotency_key=f"api-at-{exact}"),
        principal=student_principal(),
        correlation_id=str(uuid.uuid4()),
    )
    assert submission_row(harness.engine, api_ok.submission_id)["state"] == "validated"
    with pytest.raises(EnvelopeRejected) as rejected:
        harness.intake.accept(
            api_payload(
                files=(("main.py", b"x" * (exact + 1)),), idempotency_key=f"api-over-{exact}"
            ),
            principal=student_principal(),
            correlation_id=str(uuid.uuid4()),
        )
    assert rejected.value.reason == "file_too_large"


# --- 第五轮：零宽/双向字符、裸 MZ、尾点文件名、单封报文大小上限 -------------------------

#: Trojan Source：RLO 之后的内容在编辑器里会**反向显示**，把恶意调用伪装成无害注释。
TROJAN_SOURCE_SOURCE = "x = 1\n# \u202e) / fr- mr( \"metsys.so\u202c\n".encode()


def test_zero_width_typographic_characters_are_accepted(harness: Harness) -> None:
    """放行 6 个**排版类** Cf 字符（ZWSP/ZWNJ/ZWJ/WORD JOINER/BOM/软连字符）：它们零宽且
    **没有重排能力**，无法隐藏可执行内容，而从网页/Word 复制粘贴就会带上，拒掉等于让学生白丢一次提交。"""
    samples = {
        "zwsp": "x\u200b= 1\n".encode(),
        "zwnj": "x\u200c= 1\n".encode(),
        "zwj": "x\u200d= 1\n".encode(),
        "word-joiner": "x\u2060= 1\n".encode(),
        "bom-mid": "x\ufeff= 1\n".encode(),
        "soft-hyphen": "x\u00ad= 1\n".encode(),
    }
    service = harness.service()
    for uid, (label, payload) in enumerate(samples.items(), start=1):
        assert plain_text_problem(payload) is None, f"{label} 应当被放行"
        outcome = service.process_raw(
            uidvalidity=61,
            uid=uid,
            raw=build_message(
                message_id=f"<{label}@example.edu>", attachments=(("notes.md", payload),)
            ),
        )
        assert outcome.status == "processed", f"{label}: {outcome.detail}"
    assert count(harness.engine, submissions) == len(samples)
    assert count(harness.engine, intake_quarantines) == 0


@pytest.mark.parametrize(
    ("name", "character"),
    [
        pytest.param("ALM", "\u061c", id="U+061C-ALM"),
        pytest.param("LRE", "\u202a", id="U+202A-LRE"),
        pytest.param("RLE", "\u202b", id="U+202B-RLE"),
        pytest.param("PDF", "\u202c", id="U+202C-PDF"),
        pytest.param("LRO", "\u202d", id="U+202D-LRO"),
        pytest.param("RLO", "\u202e", id="U+202E-RLO"),
        pytest.param("LRI", "\u2066", id="U+2066-LRI"),
        pytest.param("RLI", "\u2067", id="U+2067-RLI"),
        pytest.param("FSI", "\u2068", id="U+2068-FSI"),
        pytest.param("PDI", "\u2069", id="U+2069-PDI"),
    ],
)
def test_bidirectional_control_characters_are_rejected(
    harness: Harness, name: str, character: str
) -> None:
    """**必须拒绝**双向控制字符（Trojan Source）：放行那 6 个排版字符的前提是这 10 个不被放过。"""
    payload = f"x = 1  # {character}hidden\n".encode()
    problem = plain_text_problem(payload)
    assert problem is not None and "bidirectional" in problem, f"{name}: {problem}"

    outcome = harness.service().process_raw(
        uidvalidity=62,
        uid=1,
        raw=build_message(
            message_id=f"<bidi-{name}@example.edu>", attachments=(("main.py", payload),)
        ),
    )
    assert outcome.status == "rejected", f"{name}: {outcome.detail}"
    assert outcome.reason_code == "not_plain_text"
    assert "bidirectional" in outcome.detail and "Trojan Source" in outcome.detail
    assert count(harness.engine, submissions) == 0


def test_trojan_source_payload_is_rejected(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """Trojan Source 的典型载荷：RLO 把 `os.system("rm -rf /")` 显示成无害注释。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    outcome = harness.service().process_raw(
        uidvalidity=63,
        uid=1,
        raw=build_message(
            message_id="<trojan-source@example.edu>",
            attachments=(("main.py", TROJAN_SOURCE_SOURCE),),
        ),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "not_plain_text")
    assert "bidirectional" in outcome.detail
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, outbox_events) == 0
    assert_single_security_event(
        caplog, reason_code="not_plain_text", attachment_name="main.py", uidvalidity=63, uid=1
    )


def test_bidi_and_typographic_character_sets_are_exactly_these() -> None:
    """把两个集合钉死：放行的排版字符恰好 6 个、拒绝的双向控制字符恰好 10 个。

    `_BIDI_CONTROL_CHARACTERS` 就是 **Unicode 双向格式控制字符族的完整 10 个**：ALM `U+061C`
    （隐式方向标记）与显式嵌套控制 LRE/RLE/PDF/LRO/RLO `U+202A–U+202E`、LRI/RLI/FSI/PDI
    `U+2066–U+2069`。两边都逐字符钉住，避免"数量说法"与"实际集合"漂移。"""
    assert _ALLOWED_TEXT_FORMAT == frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
    assert len(_ALLOWED_TEXT_FORMAT) == 6
    assert _BIDI_CONTROL_CHARACTERS == frozenset(
        "\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    )
    assert len(_BIDI_CONTROL_CHARACTERS) == 10
    assert not (_ALLOWED_TEXT_FORMAT & _BIDI_CONTROL_CHARACTERS), "两个集合不得相交"


def test_bare_mz_prefix_in_printable_source_is_accepted(harness: Harness) -> None:
    """裸 `MZ`（2 个可打印字节）+ 整体是合法纯文本 ⇒ 按文本处理（不再误拒）。"""
    payload = b"MZ = 'this file starts with the DOS signature letters'\n"
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert (verdict.kind, verdict.label) == ("text", "utf-8 text")

    outcome = harness.service().process_raw(
        uidvalidity=64,
        uid=1,
        raw=build_message(
            message_id="<bare-mz-text@example.edu>", attachments=(("notes.py", payload),)
        ),
    )
    assert outcome.status == "processed", outcome.detail
    assert count(harness.engine, submissions) == 1
    assert count(harness.engine, intake_quarantines) == 0


def test_dos_mz_executable_is_still_reported_as_executable(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """裸 `MZ` + 整体**不是**纯文本 ⇒ 原因码仍是 `executable_attachment`（安全事件里看得见）。

    这条是口径的关键：真 DOS 可执行体含 NUL/控制字节，若把它降级成 `not_plain_text`，
    一整类攻击就会在审计日志里消失。
    """
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    payload = b"MZ" + b"\x00" * 0x3C + b"\x00" * 16 + b"\x0e\x1f\xba\x0e\x00\xb4\x09\xcd\x21"
    verdict = sniff_content(payload, declared_size_limit=LIMITS.max_total_bytes)
    assert (verdict.kind, verdict.label) == ("executable", "mz")

    outcome = harness.service().process_raw(
        uidvalidity=65,
        uid=1,
        raw=build_message(
            message_id="<dos-mz@example.edu>", attachments=(("main.py", payload),)
        ),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "executable_attachment")
    assert "mz" in outcome.detail
    assert_single_security_event(
        caplog, reason_code="executable_attachment", attachment_name="main.py", uidvalidity=65, uid=1
    )
    assert count(harness.engine, submissions) == 0


def test_trailing_dot_filename_is_rejected_by_design(harness: Harness) -> None:
    """`main.py.` **有意拒绝**（不是缺陷）：Windows 会剥掉路径末尾的点与空格，
    于是"声明的路径"与"磁盘上的实际文件名"会不一致（`main.py.` 落盘成 `main.py`），
    既会让入口脚本的名字解析错位，也可能被用来绕过路径判定。"""
    assert is_allowed_relative_path("main.py."), "它通过 I2 白名单，被扩展名判定拦下"
    outcome = harness.service().process_raw(
        uidvalidity=66,
        uid=1,
        raw=build_message(
            message_id="<trailing-dot@example.edu>", attachments=(("main.py.", SOURCE),)
        ),
    )
    assert (outcome.status, outcome.reason_code) == ("rejected", "unsupported_attachment_type")
    assert "main.py." in outcome.detail
    assert count(harness.engine, submissions) == 0


def test_raw_message_size_limit_boundary(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """单封报文大小闸门：判据是 `len(raw)`（真实字节长度），**恰好等于上限**照常处理。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    raw = build_message(message_id="<size-boundary@example.edu>")
    exact = len(raw)

    accepted = harness.service(max_message_bytes=exact).process_raw(
        uidvalidity=67, uid=1, raw=raw
    )
    assert accepted.status == "processed", accepted.detail

    rejected = harness.service(max_message_bytes=exact - 1).process_raw(
        uidvalidity=67, uid=2, raw=raw
    )
    assert (rejected.status, rejected.reason_code) == ("rejected", "message_too_large")
    assert f"{exact} bytes" in rejected.detail and f"{exact - 1} byte limit" in rejected.detail
    assert count(harness.engine, submissions) == 1, "超限报文不得产生提交"
    assert count(harness.engine, email_checkpoints) == 2, "两条结论都已记 checkpoint"
    assert count(harness.engine, intake_quarantines) == 1
    assert_single_security_event(
        caplog, reason_code="message_too_large", attachment_name="", uidvalidity=67, uid=2
    )


def test_oversized_raw_message_is_not_parsed_and_leaks_nothing(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """超限报文的路径必须是"先判大小、**不解析**"：不落内容、不留正文/附件原文。"""
    caplog.set_level(logging.DEBUG, logger="autograder_b2")
    payload = ATTACHMENT_MARKER + b"\n" * 4096
    raw = build_message(
        message_id="<oversized@example.edu>",
        body=f"please grade this. {BODY_MARKER}",
        attachments=(("main.py", payload),),
    )
    assert len(raw) > 2048

    outcome = harness.service(max_message_bytes=2048).process_raw(uidvalidity=68, uid=1, raw=raw)
    assert (outcome.status, outcome.reason_code) == ("rejected", "message_too_large")
    assert count(harness.engine, submissions) == 0
    assert count(harness.engine, artifacts) == 0, "超限报文不得留下任何内容"
    assert count(harness.engine, outbox_events) == 0
    assert count(harness.engine, intake_quarantines) == 1
    assert_absent_from_records_and_logs(
        harness,
        caplog,
        [
            BODY_MARKER,
            ATTACHMENT_MARKER.decode("ascii"),
            base64.b64encode(payload).decode("ascii"),
        ],
    )


def test_message_size_limit_configuration_follows_the_existing_style() -> None:
    """`B2_IMAP_MAX_MESSAGE_BYTES`：缺省 8 MiB（依据写在 `config.py` 的常量注释里），
    非法值明确报错——不加"开关式"隐藏语义。"""
    base = {
        "B2_IMAP_HOST": "mail.example.edu",
        "B2_IMAP_USERNAME": USERNAME,
        "B2_IMAP_PASSWORD": PASSWORD,
        "B2_IMAP_MAILBOX": MAILBOX,
        "B2_IMAP_MATCHING_JSON": json.dumps(RULES),
    }
    assert load_imap_settings(base).max_message_bytes == DEFAULT_IMAP_MAX_MESSAGE_BYTES
    assert DEFAULT_IMAP_MAX_MESSAGE_BYTES == 8 * 1024 * 1024, "缺省值口径：8 MiB"
    tuned = load_imap_settings({**base, "B2_IMAP_MAX_MESSAGE_BYTES": "1024"})
    assert tuned.max_message_bytes == 1024
    for bad in ("0", "-1", "not-a-number"):
        with pytest.raises(ConfigurationError):
            load_imap_settings({**base, "B2_IMAP_MAX_MESSAGE_BYTES": bad})


# --- 配置：未配置 vs 配置错误（"缺失即明确错误"，且不连累 HTTP 入口） --------------


def test_imap_settings_are_explicit_or_absent() -> None:
    assert load_imap_settings({}) is None, "完全未配置必须是明确的 None，而不是猜出来的默认值"

    with pytest.raises(ConfigurationError) as missing_host:
        load_imap_settings({"B2_IMAP_USERNAME": USERNAME})
    assert "B2_IMAP_HOST is required" in str(missing_host.value)

    with pytest.raises(ConfigurationError) as missing_username:
        load_imap_settings({"B2_IMAP_HOST": "mail.example.edu"})
    assert "B2_IMAP_USERNAME is required" in str(missing_username.value)

    with pytest.raises(ConfigurationError) as missing_mailbox:
        load_imap_settings({"B2_IMAP_HOST": "mail.example.edu", "B2_IMAP_USERNAME": USERNAME})
    assert "B2_IMAP_MAILBOX is required" in str(missing_mailbox.value)

    with pytest.raises(ConfigurationError) as missing_password:
        load_imap_settings(
            {
                "B2_IMAP_HOST": "mail.example.edu",
                "B2_IMAP_USERNAME": USERNAME,
                "B2_IMAP_MAILBOX": MAILBOX,
            }
        )
    assert "no IMAP password configured" in str(missing_password.value)

    with pytest.raises(ConfigurationError) as missing_rules:
        load_imap_settings(
            {
                "B2_IMAP_HOST": "mail.example.edu",
                "B2_IMAP_USERNAME": USERNAME,
                "B2_IMAP_PASSWORD": PASSWORD,
                "B2_IMAP_MAILBOX": MAILBOX,
            }
        )
    assert "no matching rules configured" in str(missing_rules.value)


def test_password_never_appears_in_the_settings_repr() -> None:
    """配置对象的 `repr` 不含口令：凭据最容易从"打印一下配置"泄漏出去。"""
    settings = imap_settings(port=143)
    assert PASSWORD not in repr(settings)
    assert "password=" not in repr(settings)


def test_password_may_come_from_a_file(tmp_path: pathlib.Path) -> None:
    secret = tmp_path / "imap.pass"
    secret.write_text(f"{PASSWORD}\n", encoding="utf-8")
    settings = load_imap_settings(
        {
            "B2_IMAP_HOST": "mail.example.edu",
            "B2_IMAP_USERNAME": USERNAME,
            "B2_IMAP_PASSWORD_FILE": str(secret),
            "B2_IMAP_MAILBOX": MAILBOX,
            "B2_IMAP_MATCHING_JSON": json.dumps(RULES),
        }
    )
    assert settings is not None
    assert settings.password == PASSWORD, "文件读取必须去掉结尾换行，但不改动口令本身"
    assert settings.port == 143 and settings.use_ssl is False
    assert settings.matching == RULES


@pytest.fixture(scope="module")
def email_runtime_source(upgraded_database_url: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return {
        "DATABASE_URL": upgraded_database_url,
        "B2_ARTIFACT_ROOT": str(tmp_path_factory.mktemp("email-runtime")),
        "B2_JWKS_JSON": json.dumps(jwks_document()),
    }


def test_runtime_keeps_the_http_entry_available_without_imap(
    email_runtime_source: dict[str, str],
) -> None:
    """未配置 IMAP：HTTP 入口照常就绪，邮件入口明确报告"未配置"。"""
    runtime = build_runtime(email_runtime_source)
    assert runtime.ready is True
    assert runtime.email is None
    assert runtime.email_health() == (False, "email intake is not configured")


def test_runtime_isolates_a_broken_imap_configuration(
    email_runtime_source: dict[str, str],
) -> None:
    """IMAP 配置写错了：只让**邮件**入口不可用并给出缺失项，HTTP 入口不受影响。"""
    runtime = build_runtime({**email_runtime_source, "B2_IMAP_HOST": "mail.example.edu"})
    assert runtime.ready is True, "邮件配置错误不得让 HTTP 入口整体不可用"
    assert runtime.email is None
    ready, detail = runtime.email_health()
    assert ready is False and "B2_IMAP_USERNAME" in detail


def test_runtime_wires_the_email_entry_when_configured(
    email_runtime_source: dict[str, str],
) -> None:
    """配置齐全：邮件入口真的装配出来，并与 HTTP 入口共享同一份上限。"""
    runtime = build_runtime(
        {
            **email_runtime_source,
            "B2_IMAP_HOST": "127.0.0.1",
            "B2_IMAP_PORT": "143",
            "B2_IMAP_USERNAME": USERNAME,
            "B2_IMAP_PASSWORD": PASSWORD,
            "B2_IMAP_MAILBOX": MAILBOX,
            "B2_IMAP_MATCHING_JSON": json.dumps(RULES),
        }
    )
    assert runtime.email is not None and runtime.service is not None
    assert runtime.email.limits is runtime.service.limits
    ready, detail = runtime.email_health()
    assert ready is True and MAILBOX in detail


def test_public_factory_builds_the_same_envelope_for_both_entries() -> None:
    """构造点只有一处：同一组字节在两个入口得到同一个绑定摘要。"""
    files = (("main.py", SOURCE),)
    api = build_submission_envelope(
        submission_id=str(uuid.uuid4()),
        student_id=STUDENT_ID,
        course_id=COURSE_ID,
        section_id=SECTION_ID,
        question_version_id=QUESTION_ID,
        idempotency_key="k",
        intake="api",
        provenance=ApiProvenance(request_id=str(uuid.uuid4())),
        correlation_id=str(uuid.uuid4()),
        files=files,
    )
    email = build_submission_envelope(
        submission_id=str(uuid.uuid4()),
        student_id=STUDENT_ID,
        course_id=COURSE_ID,
        section_id=SECTION_ID,
        question_version_id=QUESTION_ID,
        idempotency_key="k",
        intake="email",
        provenance=EmailProvenance(
            mailbox=MAILBOX, uidvalidity=1, uid=1, message_id="<m@example.edu>"
        ),
        correlation_id=str(uuid.uuid4()),
        files=files,
    )
    assert api.files == email.files
    assert api.submission_digest == email.submission_digest
