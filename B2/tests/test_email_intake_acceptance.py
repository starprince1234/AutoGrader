"""邮件接收与隔离的**独立验收**（本文件由验收方自己装配，不复用实现者测试里的断言）。

对照盘档标准逐条验证：

1. **归一化**：邮件附件经**同一** intake 服务产出与 API 路径**结构相同**的信封，且重复拉取不产生第二次提交；
2. **checkpoint**：同 `(uidvalidity, uid)` 重复拉取不产生第二条提交；无法唯一匹配的邮件进隔离区、**可查询**、
   **无评测记录**；
3. **安全**：可执行附件与压缩炸弹被拒绝并**记安全事件**；隔离记录与日志中**无正文/附件原文**。

装配刻意与实现者的测试文件同构但**独立书写**（真实 PostgreSQL + 真实 CAS 根目录），并用
`load_submission_envelope` 反读落库结果做自校验——这样"归一化"这条不是看返回值自说自话，而是看
**库里究竟存了什么、能不能按冻结类型重建**。
"""

from __future__ import annotations

import logging
import pathlib
import uuid

import pytest
import sqlalchemy as sa

from autograder_b2.config import DEFAULT_IMAP_MAX_MESSAGE_BYTES, ImapSettings
from autograder_b2.db.schema import (
    artifacts,
    evaluation_runs,
    intake_quarantines,
    submission_files,
    submissions,
)
from autograder_b2.domain.envelope import SubmissionEnvelope
from autograder_b2.intake.imap import EmailCheckpointStore, EmailIntakeService, QuarantineStore
from autograder_b2.intake.matching import MappingEmailMatcher
from autograder_b2.intake.service import IntakeLimits, SubmissionIntakeService
from autograder_b2.integration.submission_loader import load_submission_envelope
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres

MAILBOX = "acceptance-mailbox"
STUDENT_ID = "11111111-1111-4111-8111-111111111111"
COURSE_ID = "22222222-2222-4222-8222-222222222222"
SECTION_ID = "33333333-3333-4333-8333-333333333333"
QUESTION_ID = "44444444-4444-4444-8444-444444444444"

#: 上限取小，让"超限/炸弹"能用几 KB 的邮件真实触发。
LIMITS = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)

#: 唯一标记：证明正文与附件原文既没进库也没进日志。
BODY_MARKER = "ACCEPTANCEBODYMARKER7b31"
ATTACHMENT_MARKER = b"ACCEPTANCEATTACHMENTMARKER2e94"

SOURCE = b"print('hello from the acceptance probe')\n"

RULES: dict[str, dict[str, str]] = {
    "matched@example.edu": {
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "section_id": SECTION_ID,
        "question_version_id": QUESTION_ID,
    }
}

#: 真实 ELF 头 + 标记字节（名字无害，内容是可执行体）。
ELF_PAYLOAD = b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 8 + ATTACHMENT_MARKER + b"\x00" * 16


class _Harness:
    """一套真实组件：真库 + 真 CAS + **同一个** `SubmissionIntakeService` 实例供两个入口共用。"""

    def __init__(self, engine: sa.Engine, artifact_root: pathlib.Path) -> None:
        self.engine = engine
        self.store = ContentAddressedStore(artifact_root, engine)
        self.intake = SubmissionIntakeService(engine, self.store, LIMITS)
        self.matcher = MappingEmailMatcher(RULES)
        #: 记录邮件入口交给**同一个** `SubmissionIntakeService.persist` 的信封——用来证明它走的不是
        #: 邮件侧另建的一套落库逻辑，而是与 API 入口共用的那个存储入口，且传的是同一个冻结类型。
        self.persisted: list[SubmissionEnvelope] = []

    def service(self) -> EmailIntakeService:
        return EmailIntakeService(
            engine=self.engine,
            settings=ImapSettings(
                host="127.0.0.1",
                port=1,
                username="acceptance",
                password="acceptance-secret",
                mailbox=MAILBOX,
                use_ssl=False,
                matching=RULES,
                max_messages_per_pull=20,
                max_message_bytes=DEFAULT_IMAP_MAX_MESSAGE_BYTES,
                timeout_seconds=10.0,
            ),
            intake=self._spy_persist(),
            matcher=self.matcher,
            checkpoints=EmailCheckpointStore(self.engine),
            quarantines=QuarantineStore(self.engine),
        )

    def _spy_persist(self) -> SubmissionIntakeService:
        """同一个实例，只把 `persist` 包一层：记录传入的信封对象（不改任何行为）。

        邮件入口在 `EmailIntakeService` 内构造信封后调用 `SubmissionIntakeService.persist`——这是与
        API 入口**共用**的存储入口。包 `persist`（而非 `accept`）正是因为共用点是它：API 入口多出来的
        `accept` 负责"契约载荷 → 信封"，邮件入口不可能复用那一步（输入形态根本不同）。
        """
        intake = self.intake
        original = intake.persist

        def counting_persist(envelope: SubmissionEnvelope, *args: object, **kwargs: object) -> object:
            self.persisted.append(envelope)
            return original(envelope, *args, **kwargs)  # type: ignore[arg-type]

        intake.persist = counting_persist  # type: ignore[method-assign]
        return intake

    # --- 计数与查询（一律直接查库，不看被测代码的返回值） --------------------------------

    def count(self, table: sa.Table) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one())

    def quarantine_rows(self) -> list[dict[str, object]]:
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(sa.select(intake_quarantines)).mappings().all()
            ]


@pytest.fixture()
def harness(engine: sa.Engine, artifact_root: pathlib.Path) -> _Harness:
    return _Harness(engine, artifact_root)


def build_message(
    *,
    message_id: str | None = "<acceptance-1@example.edu>",
    sender: str = "matched@example.edu",
    body: str = f"please grade this. {BODY_MARKER}",
    attachments: tuple[tuple[str, bytes], ...] = (("solution.py", SOURCE),),
) -> bytes:
    """一封真实 MIME 邮件（附件用 base64 传输编码，与真实客户端一致）。"""
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


# --------------------------------------------------------------------------------------
# 标准 1：归一化（同一 intake 服务 + 可反读重建 + 不产生重复提交）
# --------------------------------------------------------------------------------------


@requires_postgres
def test_email_attachment_normalises_through_the_same_intake_service(harness: _Harness) -> None:
    service = harness.service()
    outcome = service.process_raw(uidvalidity=7, uid=11, raw=build_message())

    assert outcome.status == "processed", outcome
    # 走的是**同一个**存储入口，且传入的是**冻结类型**的信封（而不是邮件侧另建的一套落库逻辑）。
    assert len(harness.persisted) == 1
    assert isinstance(harness.persisted[0], SubmissionEnvelope)
    assert harness.persisted[0].submission_id == outcome.submission_id
    assert harness.count(submissions) == 1
    assert harness.count(submission_files) == 1
    assert harness.count(artifacts) == 1

    # 反读：库里存的东西必须能按冻结类型逐字段重建，且摘要自校验通过（这是"结构相同"的硬证据）。
    with harness.engine.connect() as connection:
        submission_id = connection.execute(sa.select(submissions.c.submission_id)).scalar_one()
        envelope = load_submission_envelope(connection, str(submission_id))

    assert envelope.student_id == STUDENT_ID
    assert envelope.course_id == COURSE_ID
    assert envelope.section_id == SECTION_ID
    assert envelope.question_version_id == QUESTION_ID
    assert envelope.intake == "email"
    assert [item.path for item in envelope.files] == ["solution.py"]
    assert envelope.files[0].size_bytes == len(SOURCE)
    assert envelope.correlation_id
    # 邮件来源的信封必须带邮件身份（与 API 来源的 request_id 互斥，由库层 CHECK 保证）。
    assert envelope.provenance.message_id == "<acceptance-1@example.edu>"
    assert envelope.provenance.mailbox == MAILBOX


@requires_postgres
def test_repeated_pull_of_the_same_uid_does_not_create_a_second_submission(
    harness: _Harness,
) -> None:
    service = harness.service()
    raw = build_message()
    first = service.process_raw(uidvalidity=7, uid=11, raw=raw)
    second = service.process_raw(uidvalidity=7, uid=11, raw=raw)

    assert first.status == "processed"
    assert second.status != "processed"  # 重复拉取必须被 checkpoint 挡住，不得产生第二次提交
    assert harness.count(submissions) == 1
    assert harness.count(submission_files) == 1
    assert len(harness.persisted) == 1


@requires_postgres
def test_same_message_id_under_a_new_uid_is_also_not_a_second_submission(
    harness: _Harness,
) -> None:
    """双键幂等：换了 UID 但 `Message-ID` 相同（邮箱重建/重投）也不得产生第二份提交。"""
    service = harness.service()
    first = service.process_raw(uidvalidity=7, uid=11, raw=build_message())
    second = service.process_raw(uidvalidity=9, uid=99, raw=build_message())
    assert first.status == "processed"
    assert second.status != "processed"
    assert harness.count(submissions) == 1


# --------------------------------------------------------------------------------------
# 标准 2：无法唯一匹配 ⇒ 隔离区可查询、无评测记录
# --------------------------------------------------------------------------------------


@requires_postgres
def test_unmatched_sender_is_quarantined_queryable_and_never_evaluated(harness: _Harness) -> None:
    service = harness.service()
    outcome = service.process_raw(
        uidvalidity=7, uid=13, raw=build_message(sender="stranger@example.edu")
    )

    assert outcome.status not in {"processed"}, outcome
    assert harness.count(submissions) == 0  # 不得伪造学生身份落库
    assert harness.count(evaluation_runs) == 0  # 不得留下任何评测记录
    rows = harness.quarantine_rows()
    assert len(rows) == 1
    row = rows[0]
    # 隔离记录必须**可查询**且带可读的脱敏原因。
    assert row["reason_code"]
    assert str(row["reason_code"]).strip()
    assert row["intake"] == "email"
    assert row["mailbox"] == MAILBOX
    assert row["uid"] == 13

    # 脱敏：隔离记录的任何文本列都不得出现正文或附件原文。
    blob = " ".join(str(value) for value in row.values())
    assert BODY_MARKER not in blob
    assert ATTACHMENT_MARKER.decode() not in blob
    assert "print('hello" not in blob


# --------------------------------------------------------------------------------------
# 标准 3：可执行附件 / 压缩炸弹被拒 + 安全事件；无原文泄漏
# --------------------------------------------------------------------------------------


@requires_postgres
def test_executable_attachment_is_rejected_and_records_a_security_event(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    service = harness.service()
    # 安全事件经结构化日志输出，用 caplog 抓取本次调用的全部记录。
    with caplog.at_level(logging.WARNING, logger="autograder_b2.intake.security"):
        outcome = service.process_raw(
            uidvalidity=7, uid=14, raw=build_message(attachments=(("run.sh", ELF_PAYLOAD),))
        )
        log_text = caplog.text

    assert outcome.status == "rejected", outcome
    assert harness.count(submissions) == 0  # 被拒的提交绝不落库
    assert harness.count(evaluation_runs) == 0
    rows = harness.quarantine_rows()
    assert len(rows) == 1
    assert "executable" in str(rows[0]["reason_code"]) or "security" in str(rows[0]["reason_code"])

    # 安全事件必须被记下来（否则"被拒"这件事在运维侧不可见），且带上可读的原因。
    assert "email_intake_security" in log_text
    assert "unsafe_attachment" in log_text
    assert "executable_attachment" in log_text
    # 日志与隔离记录里都不得出现附件原文或正文原文。
    assert ATTACHMENT_MARKER.decode() not in log_text
    assert BODY_MARKER not in log_text
    assert ATTACHMENT_MARKER.decode() not in " ".join(str(v) for v in rows[0].values())


@requires_postgres
def test_archive_bomb_attachment_is_rejected(harness: _Harness) -> None:
    """压缩炸弹：声明的展开量远超预算 ⇒ 拒绝（读归档自报的长度，绝不真的解压）。"""
    service = harness.service()
    # 手写一个 zip：本地头 + 中央目录 + EOCD，声明未压缩大小远大于 LIMITS.max_total_bytes。
    declared = 64 * 1024 * 1024
    name = b"payload.bin"
    local = (
        b"PK\x03\x04"
        + (20).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")  # crc32（本判定不看）
        + declared.to_bytes(4, "little")  # compressed size
        + declared.to_bytes(4, "little")  # uncompressed size
        + len(name).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + name
    )
    central = (
        b"PK\x01\x02"
        + (20).to_bytes(2, "little")
        + (20).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + declared.to_bytes(4, "little")
        + declared.to_bytes(4, "little")
        + len(name).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + name
    )
    eocd = (
        b"PK\x05\x06"
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + len(central).to_bytes(4, "little")
        + len(local).to_bytes(4, "little")
        + (0).to_bytes(2, "little")
    )
    bomb = local + central + eocd

    outcome = service.process_raw(
        uidvalidity=7, uid=15, raw=build_message(attachments=(("data.zip", bomb),))
    )

    assert outcome.status == "rejected", outcome
    assert harness.count(submissions) == 0
    assert harness.count(evaluation_runs) == 0
    rows = harness.quarantine_rows()
    assert len(rows) == 1, rows
    assert "bomb" in str(rows[0]["reason_code"]) or "archive" in str(rows[0]["reason_code"])


@requires_postgres
def test_missing_message_id_is_quarantined_never_a_submission(harness: _Harness) -> None:
    """无 `Message-ID` 的邮件无法建立双键幂等 ⇒ 隔离，而不是"凑一个值"落库。"""
    service = harness.service()
    outcome = service.process_raw(uidvalidity=7, uid=16, raw=build_message(message_id=None))
    assert outcome.status != "processed"
    assert harness.count(submissions) == 0
    assert harness.count(evaluation_runs) == 0
    assert len(harness.quarantine_rows()) == 1


@requires_postgres
def test_accepted_email_submission_gets_a_uuid_identity(harness: _Harness) -> None:
    """标识必须是服务端生成的 UUID（不接受客户端/邮件头里的自由文本作为标识）。"""
    service = harness.service()
    service.process_raw(uidvalidity=7, uid=17, raw=build_message())
    with harness.engine.connect() as connection:
        row = connection.execute(sa.select(submissions)).mappings().one()
    assert uuid.UUID(str(row["submission_id"]))
    assert uuid.UUID(str(row["correlation_id"]))
    assert row["state"] == "validated"
