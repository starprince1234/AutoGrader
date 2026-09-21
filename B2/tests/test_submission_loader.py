"""冻结信封**可往返**的验收：装载器 + 迁移 0008 的 provenance 列与形状约束。

为什么这个文件存在：`SubmissionEnvelope` 是 API 与邮件两条入口归一化后的唯一内部形态，而评测编排器
发事件时必须带 `submission.correlation_id`。因此"能把这行重新装成同一个冻结信封"是评测阶段的
**生产级输入路径**，不是可选功能。本文件用真实 PostgreSQL 证明三件事：

1. 写进去的信封能逐字段读回来（provenance 两种形态、correlation_id、文件清单与顺序）；
2. 装载器的自校验真的抓得住人为篡改（摘要与库里的值不符、存储引用与摘要的派生关系被破坏）；
3. 数据库 CHECK 真的拦得住"与 intake 不符的 provenance 形状"，不是只声明存在。

真实数据库来自 `tests/conftest.py` 的共享夹具（临时库 + 迁移到 head），不使用 SQLite。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2.db.schema import artifacts, submission_files, submissions
from autograder_b2.domain.envelope import ApiProvenance, EmailProvenance, SubmissionEnvelope
from autograder_b2.intake.service import (
    IntakeLimits,
    SubmissionIntakeService,
    build_submission_envelope,
)
from autograder_b2.integration.submission_loader import (
    SubmissionDigestMismatch,
    SubmissionInconsistent,
    SubmissionNotFound,
    load_submission_envelope,
)
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres

LIMITS = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)

#: 路径顺序与 `sorted(paths)` 一致——用于"逐字段相等"的严格往返断言。
FILES_SORTED = (("main.py", b"print('main')\n"), ("src/util.py", b"print('util')\n"))
#: 路径顺序与 `sorted(paths)` **相反**——用于证明装载顺序由确定规则派生，而不是沿用写入顺序。
FILES_UNSORTED = (("src/util.py", b"print('util')\n"), ("main.py", b"print('main')\n"))

_INSERT_CHECKPOINT = (
    "INSERT INTO b2.email_checkpoints (email_checkpoint_id, mailbox, uidvalidity, uid, message_id,"
    " status, reason_code, submission_id, processed_at)"
    " VALUES (gen_random_uuid(), :mailbox, :uidvalidity, :uid, :message_id, 'processed', NULL,"
    " :sid, now())"
)


@pytest.fixture()
def store(engine: Engine, artifact_root: Path) -> ContentAddressedStore:
    return ContentAddressedStore(artifact_root, engine)


@pytest.fixture()
def service(engine: Engine, store: ContentAddressedStore) -> SubmissionIntakeService:
    return SubmissionIntakeService(engine, store, LIMITS)


def _identity() -> dict[str, str]:
    return {
        "submission_id": str(uuid.uuid4()),
        "student_id": str(uuid.uuid4()),
        "course_id": str(uuid.uuid4()),
        "section_id": str(uuid.uuid4()),
        "question_version_id": str(uuid.uuid4()),
        "idempotency_key": f"key-{uuid.uuid4().hex}",
    }


def _api_envelope(files: tuple[tuple[str, bytes], ...] = FILES_SORTED) -> SubmissionEnvelope:
    """API 入口形态的信封（`request_id` 与 `correlation_id` 同值，与 `service.accept` 一致）。"""
    correlation_id = str(uuid.uuid4())
    return build_submission_envelope(
        **_identity(),
        intake="api",
        provenance=ApiProvenance(request_id=correlation_id),
        correlation_id=correlation_id,
        files=files,
    )


def _email_envelope(
    files: tuple[tuple[str, bytes], ...] = FILES_SORTED,
) -> SubmissionEnvelope:
    """邮件入口形态的信封（provenance 四要素来自邮件身份，与 `intake/imap.py` 的构造一致）。"""
    return build_submission_envelope(
        **_identity(),
        intake="email",
        provenance=EmailProvenance(
            mailbox="grader-inbox",
            uidvalidity=991,
            uid=7,
            message_id="<7.abcdef@example.edu>",
        ),
        correlation_id=str(uuid.uuid4()),
        files=files,
    )


def _persist(
    service: SubmissionIntakeService,
    envelope: SubmissionEnvelope,
    files: tuple[tuple[str, bytes], ...],
    on_persist: Callable[[Connection, str], None] | None = None,
) -> None:
    result = service.persist(envelope, files, on_persist=on_persist)
    assert result.idempotent_replay is False
    assert result.submission_id == envelope.submission_id


def _load(engine: Engine, submission_id: str) -> SubmissionEnvelope:
    with engine.connect() as connection:
        return load_submission_envelope(connection, submission_id)


def _row(engine: Engine, submission_id: str) -> sa.Row:
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(
                submissions.c.correlation_id,
                submissions.c.request_id,
                submissions.c.mailbox,
                submissions.c.uidvalidity,
                submissions.c.uid,
                submissions.c.message_id,
            ).where(submissions.c.submission_id == uuid.UUID(submission_id))
        ).one()
    return row


# --- 概念 1：信封可往返（两种 provenance 形态） -------------------------------------------


def test_api_envelope_round_trips_field_by_field(
    engine: Engine, service: SubmissionIntakeService
) -> None:
    """API 来源：装载出的信封与写入的信封**逐字段相等**（冻结 dataclass 的相等即全部字段）。"""
    envelope = _api_envelope()
    _persist(service, envelope, FILES_SORTED)

    loaded = _load(engine, envelope.submission_id)

    assert loaded == envelope, "装载出的信封必须与写入的信封完全相同（含 files 顺序）"
    assert loaded.correlation_id == envelope.correlation_id
    assert isinstance(loaded.provenance, ApiProvenance)
    assert loaded.provenance.request_id == envelope.correlation_id
    # 库里落的是显式列，不是"只有 API 行才有的空列"：
    api_row = _row(engine, envelope.submission_id)
    assert str(api_row.correlation_id) == envelope.correlation_id
    assert api_row.request_id == envelope.correlation_id
    assert (api_row.mailbox, api_row.uidvalidity, api_row.uid, api_row.message_id) == (
        None,
        None,
        None,
        None,
    )


def test_email_envelope_round_trips_field_by_field(
    engine: Engine, service: SubmissionIntakeService
) -> None:
    """邮件来源：provenance 四要素（mailbox/uidvalidity/uid/message_id）忠实往返。"""
    envelope = _email_envelope()
    assert isinstance(envelope.provenance, EmailProvenance)
    _persist(service, envelope, FILES_SORTED)

    loaded = _load(engine, envelope.submission_id)

    assert loaded == envelope
    assert isinstance(loaded.provenance, EmailProvenance)
    assert loaded.provenance == envelope.provenance
    email_row = _row(engine, envelope.submission_id)
    assert email_row.request_id is None
    assert (email_row.mailbox, email_row.uidvalidity, email_row.uid, email_row.message_id) == (
        envelope.provenance.mailbox,
        envelope.provenance.uidvalidity,
        envelope.provenance.uid,
        envelope.provenance.message_id,
    )


def test_email_provenance_is_backed_by_a_processed_checkpoint(
    engine: Engine, service: SubmissionIntakeService
) -> None:
    """邮件行的 provenance 与同事务写入的 `processed` checkpoint 是同一份事实（可审计）。"""
    envelope = _email_envelope()
    assert isinstance(envelope.provenance, EmailProvenance)

    def on_persist(connection: Connection, submission_id: str) -> None:
        connection.execute(
            sa.text(_INSERT_CHECKPOINT),
            {
                "mailbox": envelope.provenance.mailbox,
                "uidvalidity": envelope.provenance.uidvalidity,
                "uid": envelope.provenance.uid,
                "message_id": envelope.provenance.message_id,
                "sid": submission_id,
            },
        )

    _persist(service, envelope, FILES_SORTED, on_persist=on_persist)

    with engine.connect() as connection:
        checkpoint = connection.execute(
            sa.text(
                "SELECT mailbox, uidvalidity, uid, message_id FROM b2.email_checkpoints"
                " WHERE submission_id = :sid AND status = 'processed'"
            ),
            {"sid": uuid.UUID(envelope.submission_id)},
        ).one()
    loaded = _load(engine, envelope.submission_id)
    assert isinstance(loaded.provenance, EmailProvenance)
    assert (
        checkpoint.mailbox,
        checkpoint.uidvalidity,
        checkpoint.uid,
        checkpoint.message_id,
    ) == (
        loaded.provenance.mailbox,
        loaded.provenance.uidvalidity,
        loaded.provenance.uid,
        loaded.provenance.message_id,
    )


def test_received_at_is_created_at_and_files_are_path_sorted(
    engine: Engine, service: SubmissionIntakeService
) -> None:
    """装载顺序由确定规则派生（path 升序），`received_at` 与 `created_at` 是同一个事实。"""
    envelope = _email_envelope(files=FILES_UNSORTED)
    _persist(service, envelope, FILES_UNSORTED)
    assert [item.path for item in envelope.files] == ["src/util.py", "main.py"]

    first = _load(engine, envelope.submission_id)
    second = _load(engine, envelope.submission_id)

    assert [item.path for item in first.files] == ["main.py", "src/util.py"]
    assert first.files == second.files, "同一行数据必须每次装出同一个信封（可重放）"
    assert first.submission_digest == envelope.submission_digest, (
        "摘要与文件顺序无关：改了写入顺序，摘要必须不变"
    )
    with engine.connect() as connection:
        created_at = connection.execute(
            sa.select(submissions.c.created_at).where(
                submissions.c.submission_id == uuid.UUID(envelope.submission_id)
            )
        ).scalar_one()
    assert first.received_at == created_at == envelope.received_at


# --- 概念 2：装载器的自校验真的抓得住篡改 -------------------------------------------------


def test_tampered_submission_digest_is_caught(
    engine: Engine, service: SubmissionIntakeService
) -> None:
    """库里摘要被改成别的值：装载必须失败，而不是"装出一个看起来能评测的信封"。"""
    envelope = _api_envelope()
    _persist(service, envelope, FILES_SORTED)
    with engine.begin() as connection:
        connection.execute(
            sa.update(submissions)
            .where(submissions.c.submission_id == uuid.UUID(envelope.submission_id))
            .values(submission_digest="f" * 64)
        )

    with pytest.raises(SubmissionDigestMismatch):
        _load(engine, envelope.submission_id)


def test_extra_file_row_is_caught_by_the_digest_check(
    engine: Engine, service: SubmissionIntakeService, store: ContentAddressedStore
) -> None:
    """文件清单被塞进一行：摘要重算必然不符（"同输入同分"的前提是文件清单不被悄悄改动）。"""
    envelope = _api_envelope()
    _persist(service, envelope, FILES_SORTED)
    extra = store.put(b"print('extra')\n")
    with engine.begin() as connection:
        connection.execute(
            sa.insert(submission_files).values(
                submission_id=uuid.UUID(envelope.submission_id),
                path="extra.py",
                artifact_id=uuid.UUID(str(extra.artifact_id)),
                created_at=envelope.received_at,
            )
        )

    with pytest.raises(SubmissionDigestMismatch):
        _load(engine, envelope.submission_id)


def test_storage_reference_not_derived_from_digest_is_caught(
    engine: Engine, service: SubmissionIntakeService
) -> None:
    """`artifacts.storage_path` 与 `content_sha256` 的派生关系被破坏：装载必须失败。"""
    envelope = _api_envelope()
    _persist(service, envelope, FILES_SORTED)
    digest = hashlib.sha256(FILES_SORTED[0][1]).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            sa.update(artifacts)
            .where(artifacts.c.content_sha256 == digest)
            .values(storage_path=f"zz/zz/{digest}")
        )

    with pytest.raises(SubmissionInconsistent):
        _load(engine, envelope.submission_id)


def test_unknown_or_malformed_submission_raises_not_found(engine: Engine) -> None:
    """找不到就是找不到：抛明确异常而不返回 `None`（`None` 会让调用方把"装载失败"当"没有"）。"""
    with engine.connect() as connection:
        with pytest.raises(SubmissionNotFound):
            load_submission_envelope(connection, str(uuid.uuid4()))
        with pytest.raises(SubmissionNotFound):
            load_submission_envelope(connection, "not-a-uuid")


# --- 概念 3：provenance 形状约束真的拦得住 -------------------------------------------------


def _insert_submission(engine: Engine, **columns: object) -> None:
    """按信封形态直插一行（列值全部走绑定参数，不做字符串拼接）。"""
    values: dict[str, object] = {
        "submission_id": uuid.uuid4(),
        "student_id": uuid.uuid4(),
        "course_id": uuid.uuid4(),
        "section_id": uuid.uuid4(),
        "question_version_id": uuid.uuid4(),
        "intake": "api",
        "idempotency_key": f"key-{uuid.uuid4().hex}",
        "idempotency_scope": f"scope-{uuid.uuid4().hex}",
        "submission_digest": "a" * 64,
        "state": "validated",
        "created_at": sa.func.now(),
        "updated_at": sa.func.now(),
        "correlation_id": uuid.uuid4(),
        "request_id": "req-1",
    }
    values.update(columns)
    with engine.begin() as connection:
        connection.execute(sa.insert(submissions).values(**values))


def test_api_row_with_email_provenance_is_rejected(engine: Engine) -> None:
    """`intake='api'` 的行带邮件身份：CHECK 必须拒绝（不是只声明存在）。"""
    with pytest.raises(IntegrityError):
        _insert_submission(engine, mailbox="grader-inbox", uidvalidity=1, uid=1, message_id="<a@b>")


def test_email_row_with_request_id_is_rejected(engine: Engine) -> None:
    """`intake='email'` 的行带 `request_id`：CHECK 必须拒绝。"""
    with pytest.raises(IntegrityError):
        _insert_submission(
            engine,
            intake="email",
            request_id="req-1",
            mailbox="grader-inbox",
            uidvalidity=1,
            uid=1,
            message_id="<a@b>",
        )


def test_email_row_without_message_id_is_rejected(engine: Engine) -> None:
    """邮件行缺 `Message-ID`：CHECK 必须拒绝。

    依据：冻结类型 `EmailProvenance.message_id: str` 非可选，而邮件入口在
    `intake/imap.py` 第 528 行就把没有 Message-ID 的来件拒掉了（走隔离、不产生提交），
    因此"邮件行有 message_id"是入口已经强制的行为，而不是本约束的新假设。
    """
    with pytest.raises(IntegrityError):
        _insert_submission(
            engine,
            intake="email",
            request_id=None,
            mailbox="grader-inbox",
            uidvalidity=1,
            uid=1,
            message_id=None,
        )


def test_missing_correlation_id_is_rejected(engine: Engine) -> None:
    """`correlation_id` 是 `NOT NULL`：少了它评测阶段就没有可用的关联标识。"""
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO b2.submissions (submission_id, student_id, course_id, section_id,"
                " question_version_id, intake, idempotency_key, idempotency_scope,"
                " submission_digest, state, created_at, updated_at, request_id)"
                " VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),"
                " gen_random_uuid(), gen_random_uuid(), 'api', :key, :scope, :digest,"
                " 'validated', now(), now(), 'req-1')"
            ),
            {
                "key": f"key-{uuid.uuid4().hex}",
                "scope": f"scope-{uuid.uuid4().hex}",
                "digest": "a" * 64,
            },
        )


def test_correctly_shaped_rows_are_accepted(engine: Engine) -> None:
    """两种合法形状都能插入（否则约束就是过严而非精准）。"""
    _insert_submission(engine)
    _insert_submission(
        engine,
        intake="email",
        request_id=None,
        mailbox="grader-inbox",
        uidvalidity=1,
        uid=1,
        message_id="<a@b>",
    )
    with engine.connect() as connection:
        total = connection.execute(sa.select(sa.func.count()).select_from(submissions)).scalar_one()
    assert total == 2
