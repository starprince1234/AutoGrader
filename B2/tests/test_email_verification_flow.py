"""作业模型下一次性核验码与多题 `.txt` 邮件提交的端到端测试（设计 §6 / §9）。

覆盖矩阵（真实 PostgreSQL + 真实 TCP 上的进程内 IMAP 夹具 + 注入 opener 的真实目录客户端语义）：

- 挑战签发：绑定 assignment、受众校验（课程/课序/名单）、目录不可用 503 语义、作业不存在/关档；
- 多附件按题映射：一封邮件 N 个 `N.txt` → N 条同 assignment 提交；部分提交；与网页提交同结构；
- 幂等：同 UID 重复拉取、同 `Message-ID` 不同 UID、同挑战换新邮件拒绝、部分失败后 resume；
- 可解释拒绝：非 `.txt`、未知题号、正文缺字段/身份不符、发件人不符、他人码、过期码、作业目录不可用
  （本轮拉取失败、不隔离）；
- 状态查询：`submissions[]`（新契约，无单一 `submission_id`）与 `processed_at` 逻辑；
- 评测侧只读入口：`load_email_receipt` 经映射表给出 `answer_attachment_name`。

真实外部邮箱/TLS 仍未验证（与 `tests/test_email_intake.py` 同一限制）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import pathlib
import re
import socket
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from autograder_b2.api.email_verification_routes import build_router as build_verification_router
from autograder_b2.api.errors import install_problem_handlers
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import CourseScope, JwksAuthenticator, Principal
from autograder_b2.config import ImapSettings
from autograder_b2.db.schema import (
    artifacts,
    email_checkpoints,
    email_sender_bindings,
    email_verification_challenges,
    email_verification_submissions,
    email_verifications,
    intake_messages,
    intake_quarantines,
    intake_state,
    outbox_events,
    submission_files,
    submissions,
)
from autograder_b2.domain.clock import sha256_hex, utc_now
from autograder_b2.intake.attachments import parse_message
from autograder_b2.intake.email_body import (
    normalize_assignment_id,
    normalize_person_text,
    parse_email_body,
)
from autograder_b2.intake.email_rules import EmailRequirements, evaluate_email_receipt
from autograder_b2.intake.email_verification import (
    ATTACHMENT_MAPPING_INVALID,
    CHALLENGE_TTL,
    EMAIL_BODY_MISMATCH,
    EMAIL_BODY_MISSING_FIELD,
    ChallengeAssignmentClosedError,
    ChallengeAssignmentNotFoundError,
    ChallengeNotFoundError,
    ConsumedVerification,
    EmailVerificationService,
    IntakeStateStore,
    VerificationRejected,
    extract_challenge_codes,
    intake_health,
    load_email_receipt,
    redact_challenge_codes,
)
from autograder_b2.intake.imap import (
    EmailCheckpointStore,
    EmailIntakeService,
    ImapProtocolError,
    QuarantineStore,
)
from autograder_b2.intake.matching import MatchedAccount, MatchingUnavailable
from autograder_b2.intake.service import IntakeLimits, SubmissionIntakeService
from autograder_b2.integration.assignments import (
    AssignmentAudience,
    AssignmentUnavailable,
)
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres
from tests.imap_fixture import FixtureMessage, ImapFixtureServer

pytestmark = requires_postgres

MAILBOX = "verification"
USERNAME = "b2-verification"
PASSWORD = "fixture-secret"
ISSUER = "autograder-b4"
AUDIENCE = "autograder"

LIMITS = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)

STUDENT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_STUDENT_ID = "55555555-5555-4555-8555-555555555555"
COURSE_ID = "22222222-2222-4222-8222-222222222222"
SECTION_ID = "33333333-3333-4333-8333-333333333333"
ASSIGNMENT_ID = "44444444-4444-4444-8444-444444444444"
Q1 = "66666666-6666-4666-8666-666666666666"
Q2 = "77777777-7777-4777-8777-777777777777"
Q3 = "88888888-8888-4888-8888-888888888888"

STUDENT_NUMBER = "2024141460192"
STUDENT_NAME = "张三"
OTHER_STUDENT_NUMBER = "2024141460193"
OTHER_STUDENT_NAME = "李四"
SENDER = "alice@example.edu"
OTHER_SENDER = "bob@example.edu"
UNKNOWN_SENDER = "nobody@example.edu"
SOURCE = b"echo hello\n"


# --- 夹具 ---------------------------------------------------------------------


class Clock:
    """可推进的假时钟：挑战有效期与状态判定都用它，测试不需要真的等待 30 分钟。"""

    def __init__(self, moment: datetime | None = None) -> None:
        self.moment = utc_now() if moment is None else moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, delta: timedelta) -> None:
        self.moment = self.moment + delta


class FakeSenderDirectory:
    """`SenderDirectory` 的替身：端口语义被真实执行（真实 B4 端点不在此环境）。"""

    def __init__(self, accounts: Mapping[str, MatchedAccount] | None = None) -> None:
        self.accounts = {
            key.strip().lower(): value for key, value in (accounts or {}).items()
        }
        self.calls: list[str] = []
        self.unavailable = False

    def lookup_account(self, sender: str) -> MatchedAccount | None:
        self.calls.append(sender)
        if self.unavailable:
            raise MatchingUnavailable("identity lookup is down")
        return self.accounts.get(sender.strip().lower())


class FakeAssignmentDirectory:
    """`AssignmentDirectory` 的替身：注入的受众就是 B3 internal 端点会返回的事实。

    `audience=None` 表示"作业不存在"（B3 404）；`unavailable=True` 表示目录不可达（B3 5xx/网络）。
    两者都是**同一对象**上的可变状态，因此测试改的是真正被 `EmailVerificationService`/接收服务
    使用的那个目录实例，而不是往它身上外挂另一个对象的方法（那会让注入与实际调用点脱节）。
    """

    def __init__(self, audience: AssignmentAudience | None = None) -> None:
        self.audience = audience or assignment_audience()
        self.calls: list[str] = []
        self.unavailable = False

    def get(self, assignment_id: str) -> AssignmentAudience | None:
        self.calls.append(assignment_id)
        if self.unavailable:
            raise AssignmentUnavailable("assignment directory is down")
        if self.audience is None:
            return None
        if str(self.audience.assignment_id) != str(assignment_id):
            return None
        return self.audience


def assignment_audience(
    *,
    status: str = "open",
    course_id: str = COURSE_ID,
    section_ids: Sequence[str] = (SECTION_ID,),
    student_ids: Sequence[str] = (),
    question_version_ids: Sequence[str] = (Q1, Q2),
) -> AssignmentAudience:
    return AssignmentAudience(
        assignment_id=ASSIGNMENT_ID,
        course_id=course_id,
        title="unix homework",
        status=status,
        section_ids=tuple(section_ids),
        student_ids=tuple(student_ids),
        question_version_ids=tuple(question_version_ids),
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _Signer:
    """B4 形态的 Ed25519 签名者（与 `tests/test_intake_api.py` 同一做法；不 import B4 代码）。"""

    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()
        raw = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        self.kid = _b64url(hashlib.sha256(raw).digest())
        self.jwks = {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": self.kid,
                    "x": _b64url(raw),
                    "use": "sig",
                    "alg": "EdDSA",
                }
            ]
        }

    def token(
        self,
        *,
        user_id: str,
        roles: tuple[str, ...] = ("student",),
        scope: list[dict[str, str]] | None = None,
        expires_in: int = 900,
    ) -> str:
        import jwt

        issued = datetime.now(UTC).replace(microsecond=0)
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": user_id,
            "jti": str(uuid.uuid4()),
            "iat": int(issued.timestamp()),
            "nbf": int(issued.timestamp()),
            "exp": int((issued + timedelta(seconds=expires_in)).timestamp()),
            "role": list(roles),
            "scope": scope
            if scope is not None
            else [{"course_id": COURSE_ID, "section_id": SECTION_ID}],
        }
        return jwt.encode(payload, self._key, algorithm="EdDSA", headers={"kid": self.kid})


@dataclass
class Harness:
    engine: Engine
    store: ContentAddressedStore
    intake: SubmissionIntakeService
    directory: FakeSenderDirectory
    assignments: FakeAssignmentDirectory
    verification: EmailVerificationService
    signer: _Signer
    clock: Clock

    def service(self, *, port: int = 1, mailbox: str = MAILBOX) -> EmailIntakeService:
        return EmailIntakeService(
            engine=self.engine,
            settings=imap_settings(port=port, mailbox=mailbox),
            intake=self.intake,
            checkpoints=EmailCheckpointStore(self.engine),
            quarantines=QuarantineStore(self.engine),
            verification=self.verification,
            assignment_directory=self.assignments,
        )


def imap_settings(*, port: int, mailbox: str = MAILBOX) -> ImapSettings:
    return ImapSettings(
        host="127.0.0.1",
        port=port,
        username=USERNAME,
        password=PASSWORD,
        mailbox=mailbox,
        use_ssl=False,
        max_messages_per_pull=20,
        max_message_bytes=8 * 1024 * 1024,
        timeout_seconds=10.0,
    )


@pytest.fixture(scope="module")
def signer() -> _Signer:
    return _Signer()


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def harness(
    engine: Engine,
    artifact_root: pathlib.Path,
    signer: _Signer,
    clock: Clock,
    assignments: FakeAssignmentDirectory,
) -> Harness:
    store = ContentAddressedStore(artifact_root, engine)
    directory = FakeSenderDirectory(
        {
            SENDER: MatchedAccount(
                user_id=STUDENT_ID,
                student_number=STUDENT_NUMBER,
                display_name=STUDENT_NAME,
            ),
            OTHER_SENDER: MatchedAccount(
                user_id=OTHER_STUDENT_ID,
                student_number=OTHER_STUDENT_NUMBER,
                display_name=OTHER_STUDENT_NAME,
            ),
        }
    )
    authenticator = JwksAuthenticator(signer.jwks, issuer=ISSUER, audience=AUDIENCE)
    verification = EmailVerificationService(
        engine=engine,
        authenticator=authenticator,
        directory=directory,
        assignment_directory=assignments,
        now=clock,
    )
    return Harness(
        engine=engine,
        store=store,
        intake=SubmissionIntakeService(
            engine, store, LIMITS, assignment_directory=assignments
        ),
        directory=directory,
        assignments=assignments,
        verification=verification,
        signer=signer,
        clock=clock,
    )


@pytest.fixture()
def assignments() -> FakeAssignmentDirectory:
    return FakeAssignmentDirectory()


@pytest.fixture()
def api_client(harness: Harness) -> Iterator[TestClient]:
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_verification_router(harness.verification))
    with TestClient(app) as client:
        yield client


# --- 小型工具 -------------------------------------------------------------------


def student_principal(
    *,
    student_id: str = STUDENT_ID,
    course_id: str = COURSE_ID,
    section_id: str = SECTION_ID,
) -> Principal:
    return Principal(
        user_id=student_id,
        roles=("student",),
        scopes=(CourseScope(course_id=course_id, section_id=section_id),),
        token_id=str(uuid.uuid4()),
    )


def issue(
    harness: Harness,
    *,
    student_id: str = STUDENT_ID,
    course_id: str = COURSE_ID,
    section_id: str = SECTION_ID,
    assignment_id: str = ASSIGNMENT_ID,
):
    return harness.verification.issue_challenge(
        student_principal(
            student_id=student_id, course_id=course_id, section_id=section_id
        ),
        course_id=course_id,
        section_id=section_id,
        assignment_id=assignment_id,
    )


def body_text(
    code: str,
    *,
    assignment: str = ASSIGNMENT_ID,
    course: str = "第1课序",
    name: str = STUDENT_NAME,
    number: str = STUDENT_NUMBER,
) -> str:
    return (
        f"核验码：{code}\n"
        f"作业编号：{assignment}\n"
        f"课序：{course}\n"
        f"姓名：{name}\n"
        f"学号：{number}\n"
        "请老师批改，谢谢。\n"
    )


def _raw_message(
    *,
    sender: str = SENDER,
    subject: str = "submission",
    body: str = "please grade this",
    attachments: Sequence[tuple[str, bytes]] = (("1.txt", SOURCE),),
    message_id: str | None = "<message-1@example.edu>",
) -> bytes:
    from email.message import EmailMessage as MIMEMessage

    message = MIMEMessage()
    message["From"] = sender
    message["To"] = f"{MAILBOX}@example.edu"
    if message_id is not None:
        message["Message-ID"] = message_id
    message["Subject"] = subject
    message.set_content(body)
    for name, payload in attachments:
        message.add_attachment(
            payload, maintype="application", subtype="octet-stream", filename=name
        )
    return message.as_bytes()


def verification_mail(
    code: str,
    *,
    message_id: str = "<message-1@example.edu>",
    sender: str = SENDER,
    attachments: Sequence[tuple[str, bytes]] = (("1.txt", SOURCE),),
    **body_overrides: str,
) -> bytes:
    return _raw_message(
        sender=sender,
        message_id=message_id,
        subject=f"{STUDENT_NUMBER}_1",
        body=body_text(code, **body_overrides),
        attachments=attachments,
    )


def pull(harness: Harness, raws: Sequence[bytes], *, uidvalidity: int = 1):
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
    return report, server


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def table_rows(engine: Engine, table: sa.Table) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(sa.select(table)).mappings().all()]


def table_count(engine: Engine, table: sa.Table) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        )


def only_row(engine: Engine, table: sa.Table) -> dict[str, Any]:
    found = table_rows(engine, table)
    assert len(found) == 1, f"{table.name} 应恰好一行，实际 {len(found)}"
    return found[0]


def _dump(engine: Engine, table: sa.Table) -> str:
    return json.dumps(table_rows(engine, table), default=str, ensure_ascii=False)


# --- 正文解析（纯函数） ---------------------------------------------------------


def test_body_parsing_accepts_both_multiline_and_collapsed_text() -> None:
    """`body_excerpt` 会把正文折叠成单行（`attachments._body_excerpt`）；两种形态都必须解析出五行。"""
    code = "AG-ABCDEFGHJKLM"
    multiline = body_text(code)
    collapsed = " ".join(multiline.split())
    for text in (multiline, collapsed):
        body = parse_email_body(text)
        assert body.complete, (text, body.missing)
        assert body.verification_code == code
        assert body.assignment_id == ASSIGNMENT_ID
        assert body.course_sequence == "第1课序"
        assert body.name == STUDENT_NAME
        assert body.student_number == STUDENT_NUMBER


def test_body_parsing_reports_missing_fields_by_key_not_value() -> None:
    body = parse_email_body("核验码：AG-ABCDEFGHJKLM\n作业编号：" + ASSIGNMENT_ID)
    assert not body.complete
    assert set(body.missing) == {"course_sequence", "name", "student_number"}
    # 缺项明细只出现字段键名/中文标签，不回显任何取值。
    from autograder_b2.intake.email_body import missing_fields_detail

    detail = missing_fields_detail(body)
    assert "student_number" in detail and "学号" in detail
    assert ASSIGNMENT_ID not in detail and "AG-" not in detail


def test_body_parsing_normalizes_identity_text() -> None:
    assert normalize_person_text("  张  三 \t") == "张 三"
    assert normalize_person_text("   ") is None
    assert normalize_assignment_id(ASSIGNMENT_ID.upper()) == ASSIGNMENT_ID
    assert normalize_assignment_id("not-a-uuid") is None


# --- 挑战签发 -------------------------------------------------------------------


def test_issued_challenge_is_bound_to_the_assignment_and_stores_only_a_digest(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    issued = issue(harness)
    assert re.fullmatch(r"AG-[A-Z0-9]{12}", issued.code)
    assert issued.assignment_id == ASSIGNMENT_ID
    assert issued.expires_at - harness.clock() == CHALLENGE_TTL

    row = only_row(harness.engine, email_verification_challenges)
    assert row["code_sha256"] == sha256_hex(issued.code.encode("ascii"))
    assert row["assignment_id"] == uuid.UUID(ASSIGNMENT_ID)
    # 新行只绑作业：题目由附件映射决定（设计 §6.2）。
    assert row["question_version_id"] is None
    assert row["expires_at"] - row["created_at"] == CHALLENGE_TTL
    assert row["consumed_at"] is None and row["consumed_verification_id"] is None
    assert row["attempts"] == 0 and row["last_rejection_code"] is None
    # 明文核验码既不在库里（任何列），也不在日志里。
    assert issued.code not in _dump(harness.engine, email_verification_challenges)
    assert issued.code not in caplog.text


def test_challenge_audience_checks(harness: Harness) -> None:
    # 课程不符 → 403（令牌本身覆盖请求的课程/课序，作业却属于另一门课）。
    other_course = "99999999-9999-4999-8999-999999999999"
    harness.assignments.audience = assignment_audience(course_id=other_course)
    with pytest.raises(AuthorizationError):
        issue(harness)

    # 课序不在作业目标内 → 403。
    harness.assignments.audience = assignment_audience(
        section_ids=("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",)
    )
    with pytest.raises(AuthorizationError):
        issue(harness)

    # 作业收窄到名单，本人不在名单 → 403。
    harness.assignments.audience = assignment_audience(student_ids=(OTHER_STUDENT_ID,))
    with pytest.raises(AuthorizationError):
        issue(harness)

    # 本人在名单 → 可以签发。
    harness.assignments.audience = assignment_audience(student_ids=(STUDENT_ID,))
    assert issue(harness).assignment_id == ASSIGNMENT_ID
    assert table_count(harness.engine, email_verification_challenges) == 1


def test_challenge_requires_an_existing_open_assignment(harness: Harness) -> None:
    harness.assignments.audience = None  # 目录里没有这个作业（B3 404）
    with pytest.raises(ChallengeAssignmentNotFoundError):
        issue(harness)

    harness.assignments.audience = assignment_audience(status="closed")
    with pytest.raises(ChallengeAssignmentClosedError):
        issue(harness)
    assert table_count(harness.engine, email_verification_challenges) == 0


def test_issuing_requires_submission_authority_for_the_scope(harness: Harness) -> None:
    other_course = "99999999-9999-4999-8999-999999999999"
    with pytest.raises(AuthorizationError):
        harness.verification.issue_challenge(
            student_principal(course_id=COURSE_ID),
            course_id=other_course,
            section_id=SECTION_ID,
            assignment_id=ASSIGNMENT_ID,
        )
    assert table_count(harness.engine, email_verification_challenges) == 0


def test_malformed_identifiers_are_rejected_without_touching_the_database(
    harness: Harness,
) -> None:
    from autograder_b2.intake.email_verification import ChallengeRequestError

    with pytest.raises(ChallengeRequestError):
        harness.verification.issue_challenge(
            student_principal(),
            course_id="not-a-uuid",
            section_id=SECTION_ID,
            assignment_id=ASSIGNMENT_ID,
        )
    assert table_count(harness.engine, email_verification_challenges) == 0


def test_assignment_directory_outage_is_an_infrastructure_error(harness: Harness) -> None:
    harness.assignments.unavailable = True
    with pytest.raises(AssignmentUnavailable):
        issue(harness)
    assert table_count(harness.engine, email_verification_challenges) == 0


# --- 状态查询 -------------------------------------------------------------------


def test_status_is_pending_after_a_successful_pull_and_owner_only(harness: Harness) -> None:
    issued = issue(harness)
    IntakeStateStore(harness.engine).record_pull_success()

    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "pending"
    assert status.reason_code is None
    assert status.verified_at is None and status.submissions == ()
    assert status.expires_at == issued.expires_at

    with pytest.raises(AuthorizationError):
        harness.verification.get_challenge_status(
            student_principal(student_id=OTHER_STUDENT_ID), issued.challenge_id
        )
    with pytest.raises(ChallengeNotFoundError):
        harness.verification.get_challenge_status(student_principal(), str(uuid.uuid4()))
    with pytest.raises(ChallengeNotFoundError):
        harness.verification.get_challenge_status(student_principal(), "not-a-uuid")


def test_status_reports_intake_unavailable_until_a_pull_succeeds(harness: Harness) -> None:
    issued = issue(harness)
    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "rejected"
    assert status.reason_code == "intake_unavailable"

    with harness.engine.connect() as connection:
        assert intake_health(connection) == "unconfigured"
    IntakeStateStore(harness.engine).record_pull_success()
    with harness.engine.connect() as connection:
        assert intake_health(connection) == "fresh"
    assert (
        harness.verification.get_challenge_status(
            student_principal(), issued.challenge_id
        ).state
        == "pending"
    )

    # 心跳过期（>5 分钟没有成功轮）同样表达为 intake_unavailable，而不是 pending。
    with harness.engine.begin() as connection:
        connection.execute(
            sa.update(intake_state).values(
                last_successful_pull_at=harness.clock() - timedelta(minutes=10)
            )
        )
    with harness.engine.connect() as connection:
        assert intake_health(connection) == "stale"
    stale = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert stale.state == "rejected"
    assert stale.reason_code == "intake_unavailable"


# --- 正确绑定：一封多附件邮件 → 按题提交 -----------------------------------------


def test_multi_attachment_mail_creates_one_submission_per_question(harness: Harness) -> None:
    issued = issue(harness)
    raw = verification_mail(
        issued.code,
        attachments=(("1.txt", b"echo one\n"), ("2.txt", b"echo two\n")),
    )
    report, _server = pull(harness, [raw])
    assert report.count("processed") == 1, report.outcomes

    rows = {
        str(row["question_version_id"]): row
        for row in table_rows(harness.engine, submissions)
    }
    assert set(rows) == {Q1, Q2}
    for row in rows.values():
        assert row["assignment_id"] == uuid.UUID(ASSIGNMENT_ID)
        assert row["student_id"] == uuid.UUID(STUDENT_ID)
        assert row["course_id"] == uuid.UUID(COURSE_ID)
        assert row["section_id"] == uuid.UUID(SECTION_ID)
        assert row["intake"] == "email"
        assert row["state"] == "validated"
        assert row["mailbox"] == MAILBOX
        assert row["uidvalidity"] == 1 and row["uid"] == 1
        assert row["message_id"] == "<message-1@example.edu>"
        assert row["request_id"] is None
    assert table_count(harness.engine, submission_files) == 2
    assert table_count(harness.engine, artifacts) == 2
    assert table_count(harness.engine, outbox_events) == 2
    # 每条提交各自入队 `submission.accepted`（worker 消费这些事件），且事件带作业归属。
    for event in table_rows(harness.engine, outbox_events):
        payload = event["payload"]
        assert isinstance(payload, dict)
        assert str(payload["assignment_id"]) == ASSIGNMENT_ID
        assert str(payload["student_id"]) == STUDENT_ID

    verification = only_row(harness.engine, email_verifications)
    assert verification["challenge_id"] == uuid.UUID(issued.challenge_id)
    assert verification["assignment_id"] == uuid.UUID(ASSIGNMENT_ID)
    # 一封多提交的邮件没有单一 submission；权威事实是按题映射表。
    assert verification["submission_id"] is None
    assert verification["processed_at"] is not None
    assert verification["student_number"] == STUDENT_NUMBER
    assert verification["attachment_names"] == ["1.txt", "2.txt"]

    mappings = table_rows(harness.engine, email_verification_submissions)
    assert {str(row["question_version_id"]) for row in mappings} == {Q1, Q2}
    assert {row["attachment_name"] for row in mappings} == {"1.txt", "2.txt"}
    # 映射的 submission_id 必须正好是库里这两条提交（按题映射不是"随便生成了两条"）。
    assert {str(row["submission_id"]) for row in mappings} == {
        str(row["submission_id"]) for row in rows.values()
    }

    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_at"] is not None
    assert challenge["consumed_verification_id"] == verification["verification_id"]
    assert challenge["attempts"] == 1
    assert challenge["last_rejection_code"] is None

    binding = only_row(harness.engine, email_sender_bindings)
    assert binding["sender"] == SENDER

    checkpoint = only_row(harness.engine, email_checkpoints)
    assert checkpoint["status"] == "processed"
    # 设计 §6.2：一封多提交的邮件 processed 时 submission_id 为 NULL。
    assert checkpoint["submission_id"] is None
    assert table_count(harness.engine, intake_quarantines) == 0
    assert table_count(harness.engine, intake_messages) == 2

    # 正文摘录保留（老师能读出学生写了什么），但长期存储里不留明文核验码。
    excerpts = [row["body_excerpt"] for row in table_rows(harness.engine, intake_messages)]
    for excerpt in excerpts:
        assert issued.code not in excerpt
        assert "[verification-code]" in excerpt

    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "received"
    assert status.verified_at is not None
    assert status.reason_code is None
    assert {(item.question_version_id, item.submission_id) for item in status.submissions} == {
        (Q1, str(rows[Q1]["submission_id"])),
        (Q2, str(rows[Q2]["submission_id"])),
    }
    # 按附件题号排序（1.txt 在 2.txt 前），客户端据此逐题刷新状态。
    assert [item.question_version_id for item in status.submissions] == [Q1, Q2]


def test_partial_attachment_set_is_a_partial_submission(harness: Harness) -> None:
    issued = issue(harness)
    raw = verification_mail(issued.code, attachments=(("2.txt", b"echo two\n"),))
    report, _server = pull(harness, [raw])
    assert report.count("processed") == 1, report.outcomes
    rows = table_rows(harness.engine, submissions)
    assert len(rows) == 1
    assert rows[0]["question_version_id"] == uuid.UUID(Q2)
    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "received"
    assert [item.question_version_id for item in status.submissions] == [Q2]


def test_email_submission_matches_the_web_submission_shape(harness: Harness) -> None:
    """网页与邮件同一 `(assignment, question, student)` 提交结构一致（同一冻结信封类型）。"""
    from autograder_b2.integration.submission_loader import load_submission_envelope

    issued = issue(harness)
    pull(harness, [verification_mail(issued.code)])
    row = only_row(harness.engine, submissions)
    with harness.engine.connect() as connection:
        envelope = load_submission_envelope(connection, str(row["submission_id"]))
    assert envelope.assignment_id == ASSIGNMENT_ID
    assert envelope.question_version_id == Q1
    assert envelope.intake == "email"
    assert [item.path for item in envelope.files] == ["1.txt"]
    assert envelope.provenance.message_id == "<message-1@example.edu>"
    assert envelope.provenance.mailbox == MAILBOX


def test_receipt_loading_uses_the_per_question_mapping(harness: Harness) -> None:
    """评测侧只读入口：映射表给出该题的 `answer_attachment_name`，与参考验证共用规则引擎。"""
    issued = issue(harness)
    pull(
        harness,
        [verification_mail(issued.code, attachments=(("1.txt", SOURCE), ("2.txt", SOURCE)))],
    )
    rows = {str(row["question_version_id"]): row for row in table_rows(harness.engine, submissions)}
    with harness.engine.connect() as connection:
        first = load_email_receipt(connection, str(rows[Q1]["submission_id"]))
        second = load_email_receipt(connection, str(rows[Q2]["submission_id"]))
        missing = load_email_receipt(connection, str(uuid.uuid4()))
    assert first is not None and second is not None
    assert first.answer_attachment_name == "1.txt"
    assert second.answer_attachment_name == "2.txt"
    for receipt in (first, second):
        assert receipt.received is True
        assert receipt.student_number == STUDENT_NUMBER
        assert receipt.sender == SENDER
        assert receipt.account_email == SENDER
        assert receipt.account_email_verified is True
        assert receipt.sender_binding_matches is True
        outcomes = evaluate_email_receipt(receipt, EmailRequirements(attempt=1))
        assert all(outcome.passed for outcome in outcomes), outcomes
    assert missing is None


# --- 幂等：重复投递 / 同 Message-ID / 同挑战新邮件 --------------------------------


def test_duplicate_delivery_of_the_same_uid_is_skipped(harness: Harness) -> None:
    issued = issue(harness)
    raw = verification_mail(issued.code)
    pull(harness, [raw])
    server = ImapFixtureServer(
        messages=[FixtureMessage(uid=1, raw=raw)],
        uidvalidity=1,
        username=USERNAME,
        password=PASSWORD,
        mailbox=MAILBOX,
    )
    with server:
        report = harness.service(port=server.port).pull()
    assert report.count("skipped") == 1, report.outcomes
    assert table_count(harness.engine, submissions) == 1
    assert table_count(harness.engine, email_verifications) == 1
    assert table_count(harness.engine, email_verification_challenges) == 1


def test_same_message_id_under_a_new_uid_is_skipped(harness: Harness) -> None:
    issued = issue(harness)
    raw = verification_mail(issued.code)
    pull(harness, [raw])
    # 邮箱重建/重投：UID 与 UIDVALIDITY 变了，Message-ID 不变 → 双键幂等命中。
    server = ImapFixtureServer(
        messages=[FixtureMessage(uid=9, raw=raw)],
        uidvalidity=7,
        username=USERNAME,
        password=PASSWORD,
        mailbox=MAILBOX,
    )
    with server:
        report = harness.service(port=server.port).pull()
    assert report.count("skipped") == 1, report.outcomes
    assert table_count(harness.engine, submissions) == 1
    assert table_count(harness.engine, email_verifications) == 1


def test_a_new_message_with_the_same_code_is_rejected(harness: Harness) -> None:
    issued = issue(harness)
    first = verification_mail(issued.code, message_id="<first@example.edu>")
    second = verification_mail(issued.code, message_id="<second@example.edu>")
    report, _server = pull(harness, [first, second])
    assert report.count("processed") == 1, report.outcomes
    assert report.count("quarantined") == 1, report.outcomes
    assert table_count(harness.engine, submissions) == 1
    assert table_count(harness.engine, email_verifications) == 1
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_rejected"
    assert "already consumed" in quarantine["detail"]
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["attempts"] == 1, "重放不增加消费尝试计数（挑战已被消费）"


def test_a_partial_failure_resumes_without_duplicating_submissions(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二题提交失败 → 不写 checkpoint；下一轮 resume 既有一题并补齐第二题。"""
    issued = issue(harness)
    raw = verification_mail(
        issued.code, attachments=(("1.txt", b"echo one\n"), ("2.txt", b"echo two\n"))
    )
    original_persist = harness.intake.persist
    calls: list[str] = []

    def flaky_persist(envelope, files, *, on_persist=None):
        calls.append(envelope.question_version_id)
        if len(calls) == 2:
            raise RuntimeError("storage hiccup after the first question")
        return original_persist(envelope, files, on_persist=on_persist)

    harness.intake.persist = flaky_persist  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        pull(harness, [raw])
    assert table_count(harness.engine, submissions) == 1
    assert table_count(harness.engine, email_checkpoints) == 0
    verification = only_row(harness.engine, email_verifications)
    assert verification["processed_at"] is None
    # 半成品绝不报 received（那会把"没交全"说成"已收到"）。
    assert (
        harness.verification.get_challenge_status(
            student_principal(), issued.challenge_id
        ).state
        == "rejected"
    )

    harness.intake.persist = original_persist  # type: ignore[method-assign]
    report, _server = pull(harness, [raw])
    assert report.count("processed") + report.count("replayed") == 1, report.outcomes
    rows = table_rows(harness.engine, submissions)
    assert len(rows) == 2
    assert {str(row["question_version_id"]) for row in rows} == {Q1, Q2}
    assert len(table_rows(harness.engine, email_verification_submissions)) == 2
    assert only_row(harness.engine, email_checkpoints)["status"] == "processed"
    assert (
        harness.verification.get_challenge_status(
            student_principal(), issued.challenge_id
        ).state
        == "received"
    )


# --- 可解释拒绝 -----------------------------------------------------------------


def test_a_non_txt_attachment_is_rejected(harness: Harness) -> None:
    issued = issue(harness)
    report, _server = pull(harness, [verification_mail(issued.code, attachments=(("1.py", SOURCE),))])
    assert report.count("rejected") == 1, report.outcomes
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "unsupported_attachment_type"
    assert "only .txt answer files are accepted" in quarantine["detail"]
    # 附件不合格的邮件不消费挑战（学生改好附件后可用同一码重发）。
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_at"] is None
    assert table_count(harness.engine, submissions) == 0


def test_an_unknown_question_ordinal_is_quarantined_and_recorded_on_the_challenge(
    harness: Harness,
) -> None:
    issued = issue(harness)  # 作业只有 Q1/Q2
    report, _server = pull(harness, [verification_mail(issued.code, attachments=(("3.txt", SOURCE),))])
    assert report.count("quarantined") == 1, report.outcomes
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == ATTACHMENT_MAPPING_INVALID
    assert "assignment has 2 question(s)" in quarantine["detail"]
    assert table_count(harness.engine, submissions) == 0
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_at"] is None
    assert challenge["last_rejection_code"] == ATTACHMENT_MAPPING_INVALID
    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "rejected"
    assert status.reason_code == ATTACHMENT_MAPPING_INVALID


def test_a_missing_body_field_is_quarantined_and_recorded(harness: Harness) -> None:
    issued = issue(harness)
    raw = _raw_message(
        body=(
            f"核验码：{issued.code}\n"
            f"作业编号：{ASSIGNMENT_ID}\n"
            "课序：第1课序\n"
            f"姓名：{STUDENT_NAME}\n"
        ),
        attachments=(("1.txt", SOURCE),),
        message_id="<missing-field@example.edu>",
    )
    report, _server = pull(harness, [raw])
    assert report.count("quarantined") == 1, report.outcomes
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == EMAIL_BODY_MISSING_FIELD
    assert "student_number" in quarantine["detail"]
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_at"] is None
    assert challenge["last_rejection_code"] == EMAIL_BODY_MISSING_FIELD


def test_a_mismatched_name_or_student_number_is_quarantined(harness: Harness) -> None:
    issued = issue(harness)
    for index, (raw, marker) in enumerate(
        (
            (
                verification_mail(
                    issued.code, name="李四", message_id="<name-mismatch@example.edu>"
                ),
                "姓名",
            ),
            (
                verification_mail(
                    issued.code,
                    number=OTHER_STUDENT_NUMBER,
                    message_id="<number-mismatch@example.edu>",
                ),
                "学号",
            ),
        )
    ):
        # 每次用不同的 UIDVALIDITY：同一个 (mailbox, uid) 已被前一封邮件的 checkpoint 占用。
        report, _server = pull(harness, [raw], uidvalidity=index + 1)
        assert report.count("quarantined") == 1, (marker, report.outcomes)
        quarantine = table_rows(harness.engine, intake_quarantines)[-1]
        assert quarantine["reason_code"] == EMAIL_BODY_MISMATCH
        assert marker in quarantine["detail"]
    assert table_count(harness.engine, submissions) == 0
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["last_rejection_code"] == EMAIL_BODY_MISMATCH
    assert challenge["consumed_at"] is None


def test_a_mismatched_assignment_number_is_quarantined(harness: Harness) -> None:
    issued = issue(harness)
    other_assignment = str(uuid.uuid4())
    report, _server = pull(
        harness, [verification_mail(issued.code, assignment=other_assignment)]
    )
    assert report.count("quarantined") == 1, report.outcomes
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == EMAIL_BODY_MISMATCH
    assert "作业编号" in quarantine["detail"]
    assert other_assignment not in quarantine["detail"]
    assert only_row(harness.engine, email_verification_challenges)[
        "last_rejection_code"
    ] == EMAIL_BODY_MISMATCH


def test_an_unregistered_sender_cannot_use_the_code(harness: Harness) -> None:
    issued = issue(harness)
    pull(harness, [verification_mail(issued.code, sender=UNKNOWN_SENDER)])
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_rejected"
    assert table_count(harness.engine, submissions) == 0
    assert table_count(harness.engine, email_verifications) == 0
    assert only_row(harness.engine, email_verification_challenges)["consumed_at"] is None


def test_a_code_owned_by_another_account_is_rejected_and_leaves_that_challenge_alone(
    harness: Harness,
) -> None:
    issued = issue(harness)  # 属于 STUDENT
    pull(harness, [verification_mail(issued.code, sender=OTHER_SENDER)])
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_rejected"
    assert table_count(harness.engine, submissions) == 0
    challenge = only_row(harness.engine, email_verification_challenges)
    # 不消费、也不在**别人**的挑战上记拒绝尝试。
    assert challenge["consumed_at"] is None
    assert challenge["attempts"] == 0
    assert challenge["last_rejection_code"] is None


def test_a_different_sender_for_a_bound_scope_is_rejected(harness: Harness) -> None:
    with harness.engine.begin() as connection:
        connection.execute(
            sa.insert(email_sender_bindings).values(
                binding_id=uuid.uuid4(),
                student_id=uuid.UUID(STUDENT_ID),
                course_id=uuid.UUID(COURSE_ID),
                section_id=uuid.UUID(SECTION_ID),
                sender="first@example.edu",
                bound_at=utc_now(),
            )
        )
    issued = issue(harness)
    pull(harness, [verification_mail(issued.code)])  # 发件人 SENDER != 已绑定地址

    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_sender_mismatch"
    assert table_count(harness.engine, submissions) == 0
    assert table_count(harness.engine, email_verifications) == 0
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["last_rejection_code"] == "email_sender_mismatch"
    assert challenge["attempts"] == 1
    assert challenge["consumed_at"] is None
    # 绑定不被后一封邮件改写。
    assert only_row(harness.engine, email_sender_bindings)["sender"] == "first@example.edu"

    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "rejected"
    assert status.reason_code == "email_sender_mismatch"


def test_an_expired_challenge_is_quarantined_with_the_expiry_reason(harness: Harness) -> None:
    issued = issue(harness)
    harness.clock.advance(CHALLENGE_TTL + timedelta(seconds=1))
    assert (
        harness.verification.get_challenge_status(
            student_principal(), issued.challenge_id
        ).state
        == "expired"
    )
    pull(harness, [verification_mail(issued.code)])
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_expired"
    assert table_count(harness.engine, submissions) == 0
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["last_rejection_code"] == "email_verification_expired"
    assert challenge["consumed_at"] is None
    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "rejected"
    assert status.reason_code == "email_verification_expired"


def test_a_mail_without_a_code_is_quarantined_with_an_actionable_reason(
    harness: Harness,
) -> None:
    raw = _raw_message(
        body="please grade this. no code here",
        attachments=(("1.txt", SOURCE),),
    )
    report, _server = pull(harness, [raw])
    assert report.count("quarantined") == 1, report.outcomes
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_rejected"
    assert "verification code" in quarantine["detail"]
    assert table_count(harness.engine, submissions) == 0


# --- 依赖故障与作业状态 ---------------------------------------------------------


def test_identity_lookup_outage_fails_the_pull_and_writes_a_heartbeat(harness: Harness) -> None:
    issued = issue(harness)
    harness.directory.unavailable = True
    with pytest.raises(MatchingUnavailable):
        pull(harness, [verification_mail(issued.code)])
    # 故障不伪装成隔离：没有任何隔离记录、没有消费。
    assert table_count(harness.engine, intake_quarantines) == 0
    assert table_count(harness.engine, email_verifications) == 0
    assert only_row(harness.engine, email_verification_challenges)["consumed_at"] is None
    state = only_row(harness.engine, intake_state)
    assert state["last_error_code"] == "identity_unavailable"
    assert state["last_successful_pull_at"] is None
    with harness.engine.connect() as connection:
        assert intake_health(connection) == "stale"


def test_assignment_directory_outage_fails_the_pull_without_quarantining(
    harness: Harness,
) -> None:
    """设计 §6.5：作业目录不可用 → 本轮拉取失败（不写 checkpoint、不隔离），绝不记成学生格式错误。"""
    issued = issue(harness)
    harness.assignments.unavailable = True
    with pytest.raises(AssignmentUnavailable):
        pull(harness, [verification_mail(issued.code)])
    assert table_count(harness.engine, intake_quarantines) == 0
    assert table_count(harness.engine, email_verifications) == 0
    assert table_count(harness.engine, email_checkpoints) == 0
    assert only_row(harness.engine, intake_state)["last_error_code"] == "assignment_unavailable"

    # 依赖恢复后同一封邮件可以被正常处理（没有 checkpoint 挡住）。
    harness.assignments.unavailable = False
    report, _server = pull(harness, [verification_mail(issued.code)])
    assert report.count("processed") == 1, report.outcomes
    assert table_count(harness.engine, submissions) == 1


def test_a_closed_assignment_is_rejected_when_the_mail_arrives(harness: Harness) -> None:
    issued = issue(harness)
    harness.assignments.audience = assignment_audience(status="closed")
    report, _server = pull(harness, [verification_mail(issued.code)])
    assert report.count("quarantined") == 1, report.outcomes
    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_rejected"
    assert "no longer open" in quarantine["detail"]
    assert table_count(harness.engine, submissions) == 0


def test_failed_pull_records_the_error_code_without_advancing_success_time(
    harness: Harness,
) -> None:
    IntakeStateStore(harness.engine).record_pull_success()
    success_at = only_row(harness.engine, intake_state)["last_successful_pull_at"]
    with pytest.raises(ImapProtocolError):
        harness.service(port=free_port()).pull()
    state = only_row(harness.engine, intake_state)
    assert state["last_error_code"] == "imap_unavailable"
    assert state["last_successful_pull_at"] == success_at, "失败轮不得刷新成功时间"


# --- 原子性与并发 ---------------------------------------------------------------


def test_submission_mapping_rolls_back_together_with_the_submission(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """映射必须与提交同事务：提交事务回滚时不得留下"映射指向不存在的提交"。"""
    from types import SimpleNamespace

    from sqlalchemy.exc import IntegrityError

    issued = issue(harness)

    def blind_insert(connection, **kwargs):  # type: ignore[no-untyped-def]
        raise IntegrityError(
            "INSERT INTO b2.email_verification_submissions",
            {},
            SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_other")),
        )

    monkeypatch.setattr(
        "autograder_b2.intake.imap._insert_mapping", blind_insert
    )
    with pytest.raises(IntegrityError):
        pull(harness, [verification_mail(issued.code)])
    assert table_count(harness.engine, submissions) == 0
    assert table_count(harness.engine, email_verification_submissions) == 0
    assert table_count(harness.engine, email_checkpoints) == 0
    # 邮件事实已记录、但还没处理完：状态不报 received。
    verification = only_row(harness.engine, email_verifications)
    assert verification["processed_at"] is None
    assert (
        harness.verification.get_challenge_status(
            student_principal(), issued.challenge_id
        ).state
        == "rejected"
    )


def test_concurrent_consumption_succeeds_exactly_once(harness: Harness) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    issued = issue(harness)
    account = MatchedAccount(
        user_id=STUDENT_ID, student_number=STUDENT_NUMBER, display_name=STUDENT_NAME
    )
    first = parse_message(verification_mail(issued.code, message_id="<one@example.edu>"))
    second = parse_message(verification_mail(issued.code, message_id="<two@example.edu>"))
    barrier = Barrier(2)

    def worker(index: int) -> ConsumedVerification | VerificationRejected:
        message = first if index == 0 else second
        barrier.wait(timeout=30)
        return harness.verification.try_consume_received(
            message=message,
            mailbox=MAILBOX,
            uidvalidity=1,
            uid=index + 1,
            sender=SENDER,
            matched_account=account,
            attachment_names=("1.txt",),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, 0), pool.submit(worker, 1)]
        results = [future.result(timeout=60) for future in futures]

    consumed = [item for item in results if isinstance(item, ConsumedVerification)]
    rejected = [item for item in results if isinstance(item, VerificationRejected)]
    assert len(consumed) == 1, results
    assert len(rejected) == 1, results
    assert rejected[0].reason_code == "email_verification_rejected"
    verification = only_row(harness.engine, email_verifications)
    assert verification["challenge_id"] == uuid.UUID(issued.challenge_id)
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_verification_id"] == verification["verification_id"]
    assert table_count(harness.engine, email_verifications) == 1


# --- 纯工具 ---------------------------------------------------------------------


def test_code_extraction_is_case_insensitive_bounded_and_redactable() -> None:
    code = "AG-ABCDEFGHJKLM"
    assert extract_challenge_codes(f"code: {code.lower()}") == (code,)
    assert extract_challenge_codes(f"x{code}") == ()
    assert extract_challenge_codes(f"{code}x") == ()
    assert extract_challenge_codes("no code") == ()
    assert extract_challenge_codes(None) == ()
    assert redact_challenge_codes(f"code: {code}") == "code: [verification-code]"
    assert redact_challenge_codes(None) is None


# --- HTTP 路由 ------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _challenge_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "course_id": COURSE_ID,
        "section_id": SECTION_ID,
        "assignment_id": ASSIGNMENT_ID,
    }
    payload.update(overrides)
    return payload


def test_create_challenge_endpoint_returns_the_code_once(
    api_client: TestClient, harness: Harness
) -> None:
    token = harness.signer.token(user_id=STUDENT_ID)
    response = api_client.post(
        "/api/v1/email-verification/challenges",
        json=_challenge_payload(),
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"challenge_id", "code", "expires_at", "assignment_id"}
    assert body["assignment_id"] == ASSIGNMENT_ID
    assert re.fullmatch(r"AG-[A-Z0-9]{12}", body["code"])
    assert body["expires_at"].endswith("Z")
    row = only_row(harness.engine, email_verification_challenges)
    assert row["code_sha256"] == sha256_hex(body["code"].encode("ascii"))
    assert row["assignment_id"] == uuid.UUID(ASSIGNMENT_ID)


def test_create_challenge_endpoint_enforces_auth_scope_and_body(
    api_client: TestClient, harness: Harness
) -> None:
    endpoint = "/api/v1/email-verification/challenges"
    assert api_client.post(endpoint, json=_challenge_payload()).status_code == 401

    uncovered = harness.signer.token(
        user_id=STUDENT_ID,
        scope=[{"course_id": "88888888-8888-4888-8888-888888888888"}],
    )
    assert (
        api_client.post(
            endpoint, json=_challenge_payload(), headers=_auth(uncovered)
        ).status_code
        == 403
    )

    malformed = harness.signer.token(user_id=STUDENT_ID)
    assert (
        api_client.post(
            endpoint,
            json=_challenge_payload(course_id="not-a-uuid"),
            headers=_auth(malformed),
        ).status_code
        == 422
    )
    assert (
        api_client.post(
            endpoint,
            json={**_challenge_payload(), "unknown": "x"},
            headers=_auth(malformed),
        ).status_code
        == 422
    )
    missing = {key: value for key, value in _challenge_payload().items() if key != "section_id"}
    assert (
        api_client.post(endpoint, json=missing, headers=_auth(malformed)).status_code == 422
    )
    assert (
        api_client.post(endpoint, json=["not-an-object"], headers=_auth(malformed)).status_code
        == 422
    )
    assert table_count(harness.engine, email_verification_challenges) == 0


def test_create_challenge_endpoint_maps_assignment_states(
    api_client: TestClient, harness: Harness
) -> None:
    token = harness.signer.token(user_id=STUDENT_ID)
    endpoint = "/api/v1/email-verification/challenges"

    harness.assignments.audience = None  # 不存在 → 404（不是 403/503）
    assert (
        api_client.post(endpoint, json=_challenge_payload(), headers=_auth(token)).status_code
        == 404
    )

    harness.assignments.audience = assignment_audience(status="archived")  # 非 open → 409
    assert (
        api_client.post(endpoint, json=_challenge_payload(), headers=_auth(token)).status_code
        == 409
    )

    harness.assignments.audience = assignment_audience(student_ids=(OTHER_STUDENT_ID,))
    assert (
        api_client.post(endpoint, json=_challenge_payload(), headers=_auth(token)).status_code
        == 403
    )

    # 目录不可达是**基础设施**故障，必须 503；此时作业受众本身仍可读，只是目录服务挂了。
    harness.assignments.audience = assignment_audience()
    harness.assignments.unavailable = True
    assert (
        api_client.post(endpoint, json=_challenge_payload(), headers=_auth(token)).status_code
        == 503
    )


def test_status_endpoint_is_owner_only_and_lists_per_question_submissions(
    api_client: TestClient, harness: Harness
) -> None:
    issued = issue(harness)
    token = harness.signer.token(user_id=STUDENT_ID)
    endpoint = f"/api/v1/email-verification/challenges/{issued.challenge_id}"

    # 还没有成功拉取 → intake_unavailable（平台条件，不是"学生没交"）。
    unavailable = api_client.get(endpoint, headers=_auth(token))
    assert unavailable.status_code == 200
    assert unavailable.json()["state"] == "rejected"
    assert unavailable.json()["reason_code"] == "intake_unavailable"

    IntakeStateStore(harness.engine).record_pull_success()
    pending = api_client.get(endpoint, headers=_auth(token))
    assert pending.status_code == 200
    body = pending.json()
    assert body["state"] == "pending"
    assert body["reason_code"] is None
    assert body["submissions"] == []
    # 新契约删掉了单一 `submission_id`（一封多附件 = N 条提交）。
    assert "submission_id" not in body
    assert issued.code not in pending.text

    pull(
        harness,
        [verification_mail(issued.code, attachments=(("1.txt", SOURCE), ("2.txt", SOURCE)))],
    )
    received = api_client.get(endpoint, headers=_auth(token)).json()
    assert received["state"] == "received"
    assert len(received["submissions"]) == 2
    assert {item["question_version_id"] for item in received["submissions"]} == {Q1, Q2}
    assert all(item["submission_id"] for item in received["submissions"])

    other = harness.signer.token(user_id=OTHER_STUDENT_ID)
    assert api_client.get(endpoint, headers=_auth(other)).status_code == 403
    assert api_client.get(endpoint).status_code == 401
    assert (
        api_client.get(
            f"/api/v1/email-verification/challenges/{uuid.uuid4()}", headers=_auth(token)
        ).status_code
        == 404
    )
    assert (
        api_client.get(
            "/api/v1/email-verification/challenges/not-a-uuid", headers=_auth(token)
        ).status_code
        == 404
    )


def test_router_returns_503_when_the_service_is_absent() -> None:
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_verification_router(None))
    with TestClient(app) as client:
        create = client.post("/api/v1/email-verification/challenges", json=_challenge_payload())
        read = client.get(f"/api/v1/email-verification/challenges/{uuid.uuid4()}")
    assert create.status_code == 503
    assert read.status_code == 503


def test_database_outage_maps_to_503(
    harness: Harness, unmigrated_database_url: str
) -> None:
    from sqlalchemy import create_engine

    broken_engine = create_engine(unmigrated_database_url)
    broken = EmailVerificationService(
        engine=broken_engine,
        authenticator=JwksAuthenticator(harness.signer.jwks, issuer=ISSUER, audience=AUDIENCE),
        assignment_directory=harness.assignments,
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_verification_router(broken))
    token = harness.signer.token(user_id=STUDENT_ID)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/email-verification/challenges",
                json=_challenge_payload(),
                headers=_auth(token),
            )
    finally:
        broken_engine.dispose()
    assert response.status_code == 503
