"""邮件接收与隔离的**独立验收**（本文件由验收方自己装配，不复用实现者测试里的断言）。

对照盘档标准逐条验证：

1. **归一化**：一封携带 N 个 `N.txt` 的邮件经**同一** intake 服务产出与 API 路径**结构相同**的
   N 份信封（`assignment_id` + 每题目一个文件），重复拉取不产生第二次提交；
2. **checkpoint**：同 `(uidvalidity, uid)` / 同 `Message-ID` 的重复投递不产生第二条提交；
   附件名不映射到题目、非 `.txt`、可执行附件 → 可解释隔离，**无评测记录**；
3. **安全**：可执行附件被拒绝并**记安全事件**；隔离记录与日志中**无正文/附件原文**。

装配刻意与实现者的测试文件同构但**独立书写**（真实 PostgreSQL + 真实 CAS 根目录 + 真实 TCP 上的
进程内 IMAP 夹具），并用 `load_submission_envelope` 反读落库结果做自校验——这样"归一化"这条
不是看返回值自说自话，而是看**库里究竟存了什么、能不能按冻结类型重建**。
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
    email_verification_challenges,
    email_verification_submissions,
    email_verifications,
    evaluation_runs,
    intake_quarantines,
    submission_files,
    submissions,
)
from autograder_b2.domain.envelope import SubmissionEnvelope
from autograder_b2.intake.email_verification import EmailVerificationService
from autograder_b2.intake.imap import EmailCheckpointStore, EmailIntakeService, QuarantineStore
from autograder_b2.intake.matching import MatchedAccount
from autograder_b2.intake.service import IntakeLimits, SubmissionIntakeService
from autograder_b2.integration.assignments import AssignmentAudience
from autograder_b2.integration.submission_loader import load_submission_envelope
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres
from tests.imap_fixture import FixtureMessage, ImapFixtureServer

pytestmark = requires_postgres

MAILBOX = "acceptance-mailbox"
USERNAME = "acceptance"
PASSWORD = "acceptance-secret"
STUDENT_ID = "11111111-1111-4111-8111-111111111111"
COURSE_ID = "22222222-2222-4222-8222-222222222222"
SECTION_ID = "33333333-3333-4333-8333-333333333333"
ASSIGNMENT_ID = "44444444-4444-4444-8444-444444444444"
Q1 = "55555555-5555-4555-8555-555555555555"
Q2 = "66666666-6666-4666-8666-666666666666"
STUDENT_NUMBER = "2024141460192"
STUDENT_NAME = "张三"
SENDER = "matched@example.edu"

#: 上限取小，让"超限/炸弹"能用几 KB 的邮件真实触发。
LIMITS = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)

#: 唯一标记：证明正文与附件原文既没进库也没进日志。
BODY_MARKER = "ACCEPTANCEBODYMARKER7b31"
ATTACHMENT_MARKER = b"ACCEPTANCEATTACHMENTMARKER2e94"

SOURCE_ONE = b"echo one\n"
SOURCE_TWO = b"echo two\n"

#: 真实 ELF 头 + 标记字节（文件名无害，内容是可执行体）。
ELF_PAYLOAD = b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 8 + ATTACHMENT_MARKER + b"\x00" * 16


class _Directory:
    """发件地址 → 账号（端口语义被真实执行；真实 B4 端点不在此环境）。"""

    def lookup_account(self, sender: str) -> MatchedAccount | None:
        if sender.strip().lower() != SENDER:
            return None
        return MatchedAccount(
            user_id=STUDENT_ID,
            student_number=STUDENT_NUMBER,
            display_name=STUDENT_NAME,
        )


class _Assignments:
    """作业受众目录（B3 internal 端点的替身；受众就是作业事实）。"""

    def __init__(self) -> None:
        self.audience = AssignmentAudience(
            assignment_id=ASSIGNMENT_ID,
            course_id=COURSE_ID,
            title="unix homework",
            status="open",
            section_ids=(SECTION_ID,),
            student_ids=(),
            question_version_ids=(Q1, Q2),
        )

    def get(self, assignment_id: str) -> AssignmentAudience | None:
        if str(assignment_id) != str(self.audience.assignment_id):
            return None
        return self.audience


class _Harness:
    """一套真实组件：真库 + 真 CAS + 与 API 入口**同一个** `SubmissionIntakeService`。"""

    def __init__(self, engine: sa.Engine, artifact_root: pathlib.Path) -> None:
        self.engine = engine
        self.store = ContentAddressedStore(artifact_root, engine)
        self.directory = _Assignments()
        self.intake = SubmissionIntakeService(
            engine, self.store, LIMITS, assignment_directory=self.directory
        )
        self.verification = EmailVerificationService(
            engine=engine, directory=_Directory(), assignment_directory=self.directory
        )
        #: 记录邮件入口交给**同一个** `SubmissionIntakeService.persist` 的信封——用来证明它走的不是
        #: 邮件侧另建的一套落库逻辑，而是与 API 入口共用的那个存储入口，且传的是同一个冻结类型。
        self.persisted: list[SubmissionEnvelope] = []
        original = self.intake.persist

        def counting_persist(envelope, files, *, on_persist=None):
            self.persisted.append(envelope)
            return original(envelope, files, on_persist=on_persist)

        self.intake.persist = counting_persist  # type: ignore[method-assign]

    def service(self, *, port: int = 1) -> EmailIntakeService:
        return EmailIntakeService(
            engine=self.engine,
            settings=ImapSettings(
                host="127.0.0.1",
                port=port,
                username=USERNAME,
                password=PASSWORD,
                mailbox=MAILBOX,
                use_ssl=False,
                max_messages_per_pull=20,
                max_message_bytes=DEFAULT_IMAP_MAX_MESSAGE_BYTES,
                timeout_seconds=10.0,
            ),
            intake=self.intake,
            checkpoints=EmailCheckpointStore(self.engine),
            quarantines=QuarantineStore(self.engine),
            verification=self.verification,
            assignment_directory=self.directory,
        )

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


def issue_code(harness: _Harness) -> str:
    from autograder_b2.auth.tokens import CourseScope, Principal

    principal = Principal(
        user_id=STUDENT_ID,
        roles=("student",),
        scopes=(CourseScope(course_id=COURSE_ID, section_id=SECTION_ID),),
        token_id=str(uuid.uuid4()),
    )
    return harness.verification.issue_challenge(
        principal,
        course_id=COURSE_ID,
        section_id=SECTION_ID,
        assignment_id=ASSIGNMENT_ID,
    ).code


def body_text(code: str) -> str:
    return (
        f"核验码：{code}\n"
        f"作业编号：{ASSIGNMENT_ID}\n"
        "课序：第1课序\n"
        f"姓名：{STUDENT_NAME}\n"
        f"学号：{STUDENT_NUMBER}\n"
        f"please grade this. {BODY_MARKER}\n"
    )


def build_message(
    *,
    code: str,
    message_id: str | None = "<acceptance-1@example.edu>",
    sender: str = SENDER,
    attachments: tuple[tuple[str, bytes], ...] = (
        ("1.txt", SOURCE_ONE),
        ("2.txt", SOURCE_TWO),
    ),
) -> bytes:
    """一封真实 MIME 邮件（附件用 base64 传输编码，与真实客户端一致）。"""
    from email.message import EmailMessage as MIMEMessage

    message = MIMEMessage()
    message["From"] = sender
    message["To"] = f"{MAILBOX}@example.edu"
    if message_id is not None:
        message["Message-ID"] = message_id
    message["Subject"] = f"{STUDENT_NUMBER}_1"
    message.set_content(body_text(code))
    for name, payload in attachments:
        message.add_attachment(
            payload, maintype="application", subtype="octet-stream", filename=name
        )
    return message.as_bytes()


def pull(harness: _Harness, raws: list[bytes], *, uidvalidity: int = 1):
    messages = [FixtureMessage(uid=index + 1, raw=raw) for index, raw in enumerate(raws)]
    server = ImapFixtureServer(
        messages=messages,
        uidvalidity=uidvalidity,
        username=USERNAME,
        password=PASSWORD,
        mailbox=MAILBOX,
    )
    with server:
        report = harness.service(port=server.port).pull()
    return report


# --------------------------------------------------------------------------------------
# 标准 1：归一化（同一 intake 服务 + 可反读重建 + 不产生重复提交）
# --------------------------------------------------------------------------------------


@requires_postgres
def test_multi_question_email_normalises_through_the_same_intake_service(
    harness: _Harness,
) -> None:
    code = issue_code(harness)
    report = pull(harness, [build_message(code=code)])
    assert report.count("processed") == 1, report.outcomes

    # 走的是**同一个**存储入口，且传入的是**冻结类型**的信封（而不是邮件侧另建的一套落库逻辑）。
    assert len(harness.persisted) == 2
    assert all(isinstance(item, SubmissionEnvelope) for item in harness.persisted)
    assert {item.assignment_id for item in harness.persisted} == {ASSIGNMENT_ID}
    assert {item.question_version_id for item in harness.persisted} == {Q1, Q2}
    assert sorted(item.files[0].path for item in harness.persisted) == ["1.txt", "2.txt"]

    assert harness.count(submissions) == 2
    assert harness.count(submission_files) == 2
    assert harness.count(artifacts) == 2
    assert harness.count(evaluation_runs) == 0

    # 反读：库里存的东西必须能按冻结类型逐字段重建，且摘要自校验通过（这是"结构相同"的硬证据）。
    with harness.engine.connect() as connection:
        rows = connection.execute(sa.select(submissions.c.submission_id)).scalars().all()
        envelopes = [load_submission_envelope(connection, str(row)) for row in rows]
    for envelope in envelopes:
        assert envelope.student_id == STUDENT_ID
        assert envelope.course_id == COURSE_ID
        assert envelope.section_id == SECTION_ID
        assert envelope.assignment_id == ASSIGNMENT_ID
        assert envelope.intake == "email"
        assert envelope.provenance.message_id == "<acceptance-1@example.edu>"
        assert envelope.provenance.mailbox == MAILBOX
    assert harness.count(email_verifications) == 1
    assert harness.count(email_verification_submissions) == 2


@requires_postgres
def test_repeated_pull_of_the_same_uid_does_not_create_a_second_submission(
    harness: _Harness,
) -> None:
    code = issue_code(harness)
    raw = build_message(code=code)
    first = pull(harness, [raw])
    second = pull(harness, [raw])

    assert first.count("processed") == 1
    assert second.count("skipped") == 1  # 重复拉取必须被 checkpoint 挡住
    assert harness.count(submissions) == 2
    assert len(harness.persisted) == 2


@requires_postgres
def test_same_message_id_under_a_new_uid_is_also_not_a_second_submission(
    harness: _Harness,
) -> None:
    """双键幂等：换了 UID 但 `Message-ID` 相同（邮箱重建/重投）也不得产生第二份提交。"""
    code = issue_code(harness)
    raw = build_message(code=code)
    first = pull(harness, [raw])
    second = pull(harness, [raw], uidvalidity=9)
    assert first.count("processed") == 1
    assert second.count("skipped") == 1
    assert harness.count(submissions) == 2


# --------------------------------------------------------------------------------------
# 标准 2：无法映射/不安全 ⇒ 隔离可查询、无评测记录
# --------------------------------------------------------------------------------------


@requires_postgres
def test_non_txt_attachment_is_rejected_and_never_evaluated(harness: _Harness) -> None:
    code = issue_code(harness)
    report = pull(harness, [build_message(code=code, attachments=(("1.py", SOURCE_ONE),))])
    assert report.count("rejected") == 1, report.outcomes
    assert harness.count(submissions) == 0
    assert harness.count(evaluation_runs) == 0
    rows = harness.quarantine_rows()
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "unsupported_attachment_type"
    assert "only .txt answer files are accepted" in str(rows[0]["detail"])


@requires_postgres
def test_unknown_question_number_is_quarantined_and_never_a_submission(
    harness: _Harness,
) -> None:
    code = issue_code(harness)
    report = pull(harness, [build_message(code=code, attachments=(("3.txt", SOURCE_ONE),))])
    assert report.count("quarantined") == 1, report.outcomes
    assert harness.count(submissions) == 0
    assert harness.count(evaluation_runs) == 0
    rows = harness.quarantine_rows()
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "attachment_mapping_invalid"
    # 挑战没被消费、并在状态上留下可解释的拒绝原因。
    with harness.engine.connect() as connection:
        stored = connection.execute(sa.select(email_verification_challenges)).mappings().one()
    assert stored["consumed_at"] is None
    assert stored["last_rejection_code"] == "attachment_mapping_invalid"


# --------------------------------------------------------------------------------------
# 标准 3：可执行附件被拒 + 安全事件；无原文泄漏
# --------------------------------------------------------------------------------------


@requires_postgres
def test_executable_attachment_is_rejected_and_records_a_security_event(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    code = issue_code(harness)
    # 安全事件经结构化日志输出，用 caplog 抓取本次调用的全部记录。
    with caplog.at_level(logging.WARNING, logger="autograder_b2.intake.security"):
        report = pull(
            harness,
            [build_message(code=code, attachments=(("1.txt", ELF_PAYLOAD),))],
        )
        log_text = caplog.text

    assert report.count("rejected") == 1, report.outcomes
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
def test_quarantine_never_contains_the_body_or_attachment_bytes(harness: _Harness) -> None:
    code = issue_code(harness)
    pull(harness, [build_message(code=code, attachments=(("9.txt", SOURCE_ONE),))])
    rows = harness.quarantine_rows()
    assert len(rows) == 1
    blob = " ".join(str(value) for value in rows[0].values())
    assert BODY_MARKER not in blob
    assert ATTACHMENT_MARKER.decode() not in blob
    assert SOURCE_ONE.decode().strip() not in blob


@requires_postgres
def test_missing_message_id_is_quarantined_never_a_submission(harness: _Harness) -> None:
    """无 `Message-ID` 的邮件无法建立双键幂等 ⇒ 隔离，而不是"凑一个值"落库。"""
    code = issue_code(harness)
    report = pull(harness, [build_message(code=code, message_id=None)])
    assert report.count("rejected") == 1, report.outcomes
    assert harness.count(submissions) == 0
    assert harness.count(evaluation_runs) == 0
    assert len(harness.quarantine_rows()) == 1


@requires_postgres
def test_accepted_email_submission_gets_a_uuid_identity(harness: _Harness) -> None:
    """标识必须是服务端生成的 UUID（不接受客户端/邮件头里的自由文本作为标识）。"""
    code = issue_code(harness)
    pull(harness, [build_message(code=code, attachments=(("1.txt", SOURCE_ONE),))])
    with harness.engine.connect() as connection:
        row = connection.execute(sa.select(submissions)).mappings().one()
    assert uuid.UUID(str(row["submission_id"]))
    assert uuid.UUID(str(row["correlation_id"]))
    assert row["state"] == "validated"
