"""一次性邮件核验码的端到端测试：签发、状态、IMAP 消费、发件绑定、心跳与隔离。

验收矩阵对应 `MAIN/docs/architecture/unix-homework-evaluation.md` §10 第 1 条：
正确绑定、伪造发件人/错误核验码拒绝、重放、并发消费只成功一次、过期、依赖故障、
隔离原因码、提交与 verification 同事务绑定。

真实依赖：真实 PostgreSQL（临时库 + 迁移到 head）、真实 TCP 上的进程内 IMAP 夹具
（`tests/imap_fixture.py`）、真实 Ed25519 令牌验签路径。真实外部邮箱/TLS 仍未验证
（与 `tests/test_email_intake.py` 同一限制）。
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

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
from autograder_b2.intake.email_rules import EmailRequirements, evaluate_email_receipt
from autograder_b2.intake.email_verification import (
    CHALLENGE_TTL,
    ChallengeNotFoundError,
    ChallengeRequestError,
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
from autograder_b2.intake.matching import (
    MappingEmailMatcher,
    MatchedAccount,
    MatchingUnavailable,
)
from autograder_b2.intake.service import IntakeLimits, SubmissionIntakeService
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
QUESTION_ID = "44444444-4444-4444-8444-444444444444"
OTHER_QUESTION_ID = "66666666-6666-4666-8666-666666666666"

STUDENT_NUMBER = "2024141460192"
SENDER = "alice@example.edu"
OTHER_SENDER = "bob@example.edu"
UNKNOWN_SENDER = "nobody@example.edu"
SOURCE = b"print('hello from verification mail')\n"

TARGET = {
    "student_id": STUDENT_ID,
    "course_id": COURSE_ID,
    "section_id": SECTION_ID,
    "question_version_id": QUESTION_ID,
}
RULES: dict[str, dict[str, str]] = {SENDER: dict(TARGET)}


# --- 夹具 ---------------------------------------------------------------------


class _DiagError(Exception):
    """携带 `diag.constraint_name` 的假 psycopg 异常（只用于构造 `IntegrityError.orig`）。"""

    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.diag = SimpleNamespace(constraint_name=constraint_name)


class Clock:
    """可推进的假时钟：挑战有效期与状态判定都用它，测试不需要真的等待 30 分钟。"""

    def __init__(self, moment: datetime | None = None) -> None:
        self.moment = utc_now() if moment is None else moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, delta: timedelta) -> None:
        self.moment = self.moment + delta


class FakeDirectory:
    """`SenderDirectory` 的替身：真实 B4 端点在测试里不可达，但**端口语义**被真实执行。"""

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
    directory: FakeDirectory
    verification: EmailVerificationService
    signer: _Signer
    clock: Clock

    def service(self, *, port: int = 1, mailbox: str = MAILBOX) -> EmailIntakeService:
        return EmailIntakeService(
            engine=self.engine,
            settings=imap_settings(port=port, mailbox=mailbox),
            intake=self.intake,
            matcher=MappingEmailMatcher(RULES),
            checkpoints=EmailCheckpointStore(self.engine),
            quarantines=QuarantineStore(self.engine),
            verification=self.verification,
        )


def imap_settings(*, port: int, mailbox: str = MAILBOX) -> ImapSettings:
    return ImapSettings(
        host="127.0.0.1",
        port=port,
        username=USERNAME,
        password=PASSWORD,
        mailbox=mailbox,
        use_ssl=False,
        matching=RULES,
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
    engine: Engine, artifact_root: pathlib.Path, signer: _Signer, clock: Clock
) -> Harness:
    store = ContentAddressedStore(artifact_root, engine)
    directory = FakeDirectory(
        {
            SENDER: MatchedAccount(user_id=STUDENT_ID, student_number=STUDENT_NUMBER),
            OTHER_SENDER: MatchedAccount(
                user_id=OTHER_STUDENT_ID, student_number="2024141460193"
            ),
        }
    )
    authenticator = JwksAuthenticator(signer.jwks, issuer=ISSUER, audience=AUDIENCE)
    verification = EmailVerificationService(
        engine=engine,
        authenticator=authenticator,
        directory=directory,
        now=clock,
    )
    return Harness(
        engine=engine,
        store=store,
        intake=SubmissionIntakeService(engine, store, LIMITS),
        directory=directory,
        verification=verification,
        signer=signer,
        clock=clock,
    )


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
    question_version_id: str = QUESTION_ID,
):
    return harness.verification.issue_challenge(
        student_principal(
            student_id=student_id, course_id=course_id, section_id=section_id
        ),
        course_id=course_id,
        section_id=section_id,
        question_version_id=question_version_id,
    )


def _raw_message(
    *,
    sender: str = SENDER,
    subject: str = "submission",
    body: str = "please grade this",
    attachments: Sequence[tuple[str, bytes]] = (("main.py", SOURCE),),
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


def verification_mail(code: str, **overrides: Any) -> bytes:
    """一封"把核验码写进正文、附件与主题都符合本题要求"的邮件。"""
    values: dict[str, Any] = {
        "subject": f"{STUDENT_NUMBER}_1",
        "body": f"verification code: {code}\nhere is my homework",
        "attachments": ((f"{STUDENT_NUMBER}.txt", SOURCE),),
    }
    values.update(overrides)
    return _raw_message(**values)


def pull(
    harness: Harness, raws: Sequence[bytes], *, uidvalidity: int = 1
):
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


def pull_from_server(harness: Harness, server: ImapFixtureServer):
    with server:
        return harness.service(port=server.port).pull()


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


# --- 挑战签发 -------------------------------------------------------------------


def test_issued_challenge_is_returned_once_and_stored_only_as_a_digest(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    issued = issue(harness)
    assert re.fullmatch(r"AG-[A-Z0-9]{12}", issued.code)
    assert issued.expires_at - harness.clock() == CHALLENGE_TTL

    row = only_row(harness.engine, email_verification_challenges)
    assert row["code_sha256"] == sha256_hex(issued.code.encode("ascii"))
    assert row["student_id"] == uuid.UUID(STUDENT_ID)
    assert row["course_id"] == uuid.UUID(COURSE_ID)
    assert row["section_id"] == uuid.UUID(SECTION_ID)
    assert row["question_version_id"] == uuid.UUID(QUESTION_ID)
    assert row["expires_at"] - row["created_at"] == CHALLENGE_TTL
    assert row["consumed_at"] is None and row["consumed_verification_id"] is None
    assert row["attempts"] == 0 and row["last_rejection_code"] is None
    # 明文核验码既不在库里（任何列），也不在日志里。
    assert issued.code not in _dump(harness.engine, email_verification_challenges)
    assert issued.code not in caplog.text


def test_each_issued_challenge_gets_a_different_code(harness: Harness) -> None:
    first = issue(harness)
    second = issue(harness)
    assert first.code != second.code
    assert first.challenge_id != second.challenge_id
    assert table_count(harness.engine, email_verification_challenges) == 2


def test_issuing_requires_submission_authority_for_the_requested_scope(harness: Harness) -> None:
    other_course = "77777777-7777-4777-8777-777777777777"
    with pytest.raises(AuthorizationError):
        harness.verification.issue_challenge(
            student_principal(course_id=COURSE_ID),
            course_id=other_course,
            section_id=SECTION_ID,
            question_version_id=QUESTION_ID,
        )
    assert table_count(harness.engine, email_verification_challenges) == 0


def test_malformed_identifiers_are_rejected_without_touching_the_database(
    harness: Harness,
) -> None:
    with pytest.raises(ChallengeRequestError):
        harness.verification.issue_challenge(
            student_principal(),
            course_id="not-a-uuid",
            section_id=SECTION_ID,
            question_version_id=QUESTION_ID,
        )
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
    assert status.verified_at is None and status.submission_id is None
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


# --- 正确绑定：邮件 → 挑战 → 提交 -------------------------------------------------


def test_valid_code_becomes_a_submission_bound_to_the_verification(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    issued = issue(harness)
    raw = verification_mail(issued.code)
    report, _server = pull(harness, [raw])
    assert report.count("processed") == 1, report.outcomes

    submission = only_row(harness.engine, submissions)
    assert submission["student_id"] == uuid.UUID(STUDENT_ID)
    assert submission["course_id"] == uuid.UUID(COURSE_ID)
    assert submission["section_id"] == uuid.UUID(SECTION_ID)
    assert submission["question_version_id"] == uuid.UUID(QUESTION_ID)
    assert submission["intake"] == "email"
    assert submission["state"] == "validated"
    assert submission["mailbox"] == MAILBOX
    assert submission["uidvalidity"] == 1
    assert submission["uid"] == 1
    assert submission["message_id"] == "<message-1@example.edu>"
    assert submission["request_id"] is None

    verification = only_row(harness.engine, email_verifications)
    assert verification["challenge_id"] == uuid.UUID(issued.challenge_id)
    assert verification["submission_id"] == submission["submission_id"]
    assert verification["student_id"] == uuid.UUID(STUDENT_ID)
    assert verification["sender"] == SENDER
    assert verification["student_number"] == STUDENT_NUMBER
    assert verification["subject"] == f"{STUDENT_NUMBER}_1"
    assert verification["attachment_names"] == [f"{STUDENT_NUMBER}.txt"]
    assert verification["mailbox"] == MAILBOX
    assert verification["uidvalidity"] == 1 and verification["uid"] == 1
    assert verification["message_id"] == "<message-1@example.edu>"

    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_at"] is not None
    assert challenge["consumed_verification_id"] == verification["verification_id"]
    assert challenge["attempts"] == 1
    assert challenge["last_rejection_code"] is None

    binding = only_row(harness.engine, email_sender_bindings)
    assert binding["student_id"] == uuid.UUID(STUDENT_ID)
    assert binding["course_id"] == uuid.UUID(COURSE_ID)
    assert binding["section_id"] == uuid.UUID(SECTION_ID)
    assert binding["sender"] == SENDER

    checkpoint = only_row(harness.engine, email_checkpoints)
    assert checkpoint["status"] == "processed"
    assert checkpoint["submission_id"] == submission["submission_id"]
    assert checkpoint["uid"] == 1

    assert table_count(harness.engine, submission_files) == 1
    assert table_count(harness.engine, artifacts) == 1
    assert table_count(harness.engine, intake_quarantines) == 0
    assert table_count(harness.engine, outbox_events) == 1
    assert table_count(harness.engine, intake_messages) == 1

    # 正文摘录保留（老师能读出学生写了什么），但**长期存储里不留明文核验码**。
    excerpt = only_row(harness.engine, intake_messages)["body_excerpt"]
    assert issued.code not in excerpt
    assert "[verification-code]" in excerpt
    assert issued.code not in caplog.text

    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    assert status.state == "received"
    assert status.submission_id == str(submission["submission_id"])
    assert status.verified_at is not None
    assert status.reason_code is None


def test_receipt_loading_feeds_the_shared_rule_engine(harness: Harness) -> None:
    """评测侧只读入口：真实事实 → 与参考答案验证**同一个**规则引擎 → 四条全过。"""
    issued = issue(harness)
    pull(harness, [verification_mail(issued.code)])
    submission = only_row(harness.engine, submissions)

    with harness.engine.connect() as connection:
        receipt = load_email_receipt(connection, str(submission["submission_id"]))
    assert receipt is not None
    assert receipt.received is True
    assert receipt.student_number == STUDENT_NUMBER
    assert receipt.sender == SENDER
    assert receipt.account_email == SENDER
    assert receipt.account_email_verified is True
    assert receipt.sender_binding_matches is True
    assert receipt.attachment_names == (f"{STUDENT_NUMBER}.txt",)

    outcomes = evaluate_email_receipt(receipt, EmailRequirements(attempt=1))
    assert all(outcome.passed for outcome in outcomes), outcomes
    rendered = "\n".join(
        f"{outcome.message}\n{outcome.detail!r}" for outcome in outcomes
    )
    assert SENDER not in rendered and STUDENT_NUMBER not in rendered

    # 没有验证记录的提交 → None（调用方据此结合心跳判 missing_output / infrastructure_error）。
    with harness.engine.connect() as connection:
        assert load_email_receipt(connection, str(uuid.uuid4())) is None


def test_a_subject_outside_the_package_template_is_judged_by_the_same_rules(
    harness: Harness,
) -> None:
    """收件端不判 package 模板（那在评审期才知道）；规则引擎在评测期给出 output_mismatch。"""
    issued = issue(harness)
    pull(
        harness,
        [verification_mail(issued.code, subject="not the required subject")],
    )
    submission = only_row(harness.engine, submissions)
    with harness.engine.connect() as connection:
        receipt = load_email_receipt(connection, str(submission["submission_id"]))
    assert receipt is not None
    outcomes = evaluate_email_receipt(receipt, EmailRequirements(attempt=1))
    failed = {outcome.check: outcome.category for outcome in outcomes if not outcome.passed}
    assert failed == {"subject": "output_mismatch"}


# --- 拒绝：错误码 / 伪造发件人 / 绑定不符 ------------------------------------------


def test_unknown_code_is_quarantined_and_consumes_nothing(harness: Harness) -> None:
    issued = issue(harness)
    unknown_code = "AG-000000000000"
    assert unknown_code != issued.code
    report, _server = pull(harness, [verification_mail(unknown_code)])
    assert report.count("quarantined") == 1, report.outcomes

    quarantine = only_row(harness.engine, intake_quarantines)
    assert quarantine["reason_code"] == "email_verification_rejected"
    # 核验路径的隔离记录不带主题（主题可能含学号/姓名）。
    assert quarantine["subject"] is None
    assert SENDER not in json.dumps(quarantine, default=str)

    assert table_count(harness.engine, submissions) == 0
    assert table_count(harness.engine, email_verifications) == 0
    challenge = only_row(harness.engine, email_verification_challenges)
    assert challenge["consumed_at"] is None
    assert challenge["attempts"] == 0


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
    # OTHER_SENDER 解析到另一个账号：码不属于发件账号。
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


# --- 重放与并发 -----------------------------------------------------------------


def test_replayed_code_is_quarantined_and_never_creates_a_second_submission(
    harness: Harness,
) -> None:
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
    assert challenge["consumed_at"] is not None
    assert challenge["attempts"] == 1, "重放不增加消费尝试计数（挑战已被消费）"


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
    report = pull_from_server(harness, server)
    assert report.count("skipped") == 1, report.outcomes
    assert table_count(harness.engine, submissions) == 1
    assert table_count(harness.engine, email_verifications) == 1
    assert table_count(harness.engine, email_verification_challenges) == 1


def test_concurrent_consumption_succeeds_exactly_once(harness: Harness) -> None:
    issued = issue(harness)
    account = MatchedAccount(user_id=STUDENT_ID, student_number=STUDENT_NUMBER)
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
            attachment_names=(f"{STUDENT_NUMBER}.txt",),
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


def test_two_challenges_are_consumed_independently(harness: Harness) -> None:
    first = issue(harness)
    second = issue(harness, student_id=OTHER_STUDENT_ID, question_version_id=OTHER_QUESTION_ID)
    pull(harness, [verification_mail(first.code)])

    challenges = {
        str(row["challenge_id"]): row
        for row in table_rows(harness.engine, email_verification_challenges)
    }
    assert challenges[first.challenge_id]["consumed_at"] is not None
    assert challenges[second.challenge_id]["consumed_at"] is None
    assert challenges[second.challenge_id]["attempts"] == 0
    assert table_count(harness.engine, email_verifications) == 1


# --- 依赖故障 -------------------------------------------------------------------


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


# --- 原子性：提交与 verification 绑定 --------------------------------------------


def test_submission_binding_rolls_back_together_with_the_submission(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """绑定必须与提交同事务：提交事务回滚时不得留下"verification 指向不存在的提交"。"""
    issued = issue(harness)

    def blind_record(self: EmailCheckpointStore, connection: Any, **kwargs: Any) -> None:
        raise IntegrityError(
            "INSERT INTO b2.email_checkpoints",
            {},
            _DiagError("uq_email_checkpoints_mailbox_uidvalidity_uid"),
        )

    monkeypatch.setattr(EmailCheckpointStore, "record", blind_record)
    report, _server = pull(harness, [verification_mail(issued.code)])
    assert report.count("skipped") == 1, report.outcomes

    assert table_count(harness.engine, submissions) == 0
    assert table_count(harness.engine, email_checkpoints) == 0
    assert table_count(harness.engine, outbox_events) == 0
    assert table_count(harness.engine, intake_messages) == 0
    verification = only_row(harness.engine, email_verifications)
    assert verification["submission_id"] is None, "提交没落库，绑定就必须回滚"
    # 挑战消费早于提交（两段式）：这是设计记录在案的恢复边界——学生可重新申请一个码。
    assert only_row(harness.engine, email_verification_challenges)["consumed_at"] is not None
    status = harness.verification.get_challenge_status(
        student_principal(), issued.challenge_id
    )
    # 半成品绝不报 received（那会把"提交没生成"说成"已收到"）：报 rejected + 通用原因码。
    assert status.state == "rejected"
    assert status.reason_code == "email_verification_rejected"
    assert status.submission_id is None
    assert status.verified_at is not None, "邮件事实确实已记录，只是提交没落地"


# --- 既有语义不变 -----------------------------------------------------------------


def test_mail_without_a_code_keeps_the_existing_matching_behavior(harness: Harness) -> None:
    raw = _raw_message(
        body="please grade this. no code here",
        attachments=(("main.py", SOURCE),),
    )
    report, _server = pull(harness, [raw])
    assert report.count("processed") == 1, report.outcomes
    submission = only_row(harness.engine, submissions)
    assert submission["student_id"] == uuid.UUID(STUDENT_ID)
    assert submission["intake"] == "email"
    assert table_count(harness.engine, email_verifications) == 0
    assert table_count(harness.engine, email_sender_bindings) == 0
    assert table_count(harness.engine, intake_quarantines) == 0


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
        "question_version_id": QUESTION_ID,
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
    assert set(body) == {"challenge_id", "code", "expires_at"}
    assert re.fullmatch(r"AG-[A-Z0-9]{12}", body["code"])
    assert body["expires_at"].endswith("Z")
    row = only_row(harness.engine, email_verification_challenges)
    assert row["code_sha256"] == sha256_hex(body["code"].encode("ascii"))


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


def test_status_endpoint_is_owner_only_and_never_echoes_the_code(
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
    assert pending.json()["state"] == "pending"
    assert pending.json()["reason_code"] is None
    assert pending.json()["submission_id"] is None
    assert issued.code not in pending.text

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
