"""独立复核探针（复核者自写；不属于实现者交付物，复核结束后删除）。

不复用 `tests/test_events_outbox.py` 的任何断言：自建流命名、自建读回、自建 schema 校验器
（直接读 `MAIN/contracts/schemas/event-envelope.schema.json`，不经 `autograder_b2.contracts`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import pathlib
import re
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import nats
import pytest
import sqlalchemy as sa
from jsonschema import Draft202012Validator, FormatChecker
from nats.js import api
from sqlalchemy import Engine, create_engine, text

from autograder_b2.db.schema import inbox_events, outbox_events
from autograder_b2.domain.clock import utc_now
from autograder_b2.events.consumer import InboxConsumer
from autograder_b2.events.dispatcher import NatsOutboxDispatcher
from autograder_b2.events.nats import EventBusUnavailable, NatsEventBus
from autograder_b2.events.outbox import (
    EVALUATION_COMPLETED,
    EVALUATION_FAILED,
    EVALUATION_PROGRESS,
    EVALUATION_STARTED,
    PUBLISHED_SUBJECTS,
    SUBMISSION_ACCEPTED,
    build_event,
    enqueue,
)
from autograder_b2.events.payload import (
    QUESTION_VERSION_PUBLISHED,
    allowed_keys,
)

NATS_URL = os.getenv("B2_TEST_NATS_URL", "nats://localhost:54223")
UNREACHABLE = "nats://127.0.0.1:1"
UNREACHABLE_WITH_CREDENTIAL = "nats://reviewer:sekretpw@127.0.0.1:1"

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_SCHEMA = (B2_ROOT / ".." / "MAIN" / "contracts" / "schemas" / "event-envelope.schema.json")
MAIN_CONTRACTS = (B2_ROOT / ".." / "MAIN" / "contracts").resolve()

#: 复核者自带的敏感标记（与实现者测试用的字符串**故意不同**，避免"只为那串字串写死"）。
M_SRC = "REVIEWER_SRC_ZZZ_import_os_print_9d1f2c"
M_ANS = "REVIEWER_ANSWER_ZZZ_def_solution_4a7b"
M_HID = "REVIEWER_HIDDEN_ZZZ_expected_output_8e3c"
M_CRED = "REVIEWER_CRED_ZZZ_ghp_aaaabbbbccccddddeeee1111"
M_PATH = "REVIEWER_PATH_ZZZ_D:\\VScodeProjects\\AutoGrader\\B2\\var\\ans.py"
M_PII = "REVIEWER_PII_ZZZ_student@example.edu"
MARKERS = (M_SRC, M_ANS, M_HID, M_CRED, M_PATH, M_PII)

SID = "00000000-0000-4000-8000-0000000a0101"
STU = "00000000-0000-4000-8000-0000000a0102"
CRS = "00000000-0000-4000-8000-0000000a0103"
SEC = "00000000-0000-4000-8000-0000000a0104"
QVI = "00000000-0000-4000-8000-0000000a0105"
RUN = "00000000-0000-4000-8000-0000000a0004"
COR = "00000000-0000-4000-8000-0000000a0302"
HEX64 = "b" * 64

PROBE_EFFECT_TABLE = "public.b2_review_effects"


def _reachable(url: str) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 4222), 1.0):
            return True
    except OSError:
        return False


requires_jetstream = pytest.mark.skipif(
    not _reachable(NATS_URL), reason=f"需要真实 NATS JetStream：{NATS_URL} 不可达"
)
from tests.conftest import requires_postgres

# 依赖**逐用例**声明（不用模块级 `pytestmark`）：否则纯逻辑断言会被一起静默跳过——
# 复核者指出的正是这一点（曾有 15 条在缺依赖时全跳过，且其中两条专门验证"断言有没有牙"）。
db = requires_postgres
broker = requires_jetstream


# --- 独立工具 ---------------------------------------------------------------------------


def independent_validator() -> Draft202012Validator:
    """复核者自己的校验器：直接读 MAIN 的 schema 文件（不经 B2 的 contracts 模块）。"""
    schema = json.loads(MAIN_SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def independent_errors(instance: object) -> list[str]:
    return [f"{list(e.path)}: {e.message}" for e in independent_validator().iter_errors(instance)]


def scan_markers(blob: bytes | str) -> list[str]:
    text = blob.decode("utf-8", "replace") if isinstance(blob, bytes) else blob
    return [m for m in MARKERS if m in text]


def scan_internal(blob: str | None) -> list[str]:
    """内部信息特征扫描：IP、未脱敏的 URL 权威段、localhost、盘符路径、凭据原文。"""
    text = blob or ""
    found = []
    for label, pattern in (
        ("ipv4", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        ("url-authority", r"(?i)\b[a-z][a-z0-9+.\-]*://(?!<redacted-host>)[^\s]"),
        ("localhost", r"(?i)localhost"),
        ("winpath", r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
        ("credential", r"sekretpw"),
    ):
        if re.search(pattern, text):
            found.append(label)
    return found


@contextlib.asynccontextmanager
async def bus(url: str = NATS_URL, **kw) -> AsyncIterator[NatsEventBus]:
    instance = NatsEventBus(url=url, **kw)
    await instance.connect()
    try:
        yield instance
    finally:
        await instance.close()


@contextlib.asynccontextmanager
async def stream(bus_: NatsEventBus, subjects=("autograder.v1.>",)) -> AsyncIterator[str]:
    name = f"B2_REVIEW_{uuid.uuid4().hex[:10].upper()}"
    await bus_.jetstream().add_stream(name=name, subjects=list(subjects))
    try:
        yield name
    finally:
        with contextlib.suppress(Exception):
            await bus_.jetstream().delete_stream(name)


async def drain(bus_: NatsEventBus, stream_: str, subject: str, *, timeout: float = 3.0):
    """收全（直到超时/无新消息）：返回 [(raw_bytes, envelope, headers, seq), ...]。"""
    durable = f"review-{uuid.uuid4().hex[:10]}"
    sub = await bus_.jetstream().pull_subscribe(
        subject,
        durable=durable,
        stream=stream_,
        config=api.ConsumerConfig(
            deliver_policy=api.DeliverPolicy.ALL, ack_policy=api.AckPolicy.EXPLICIT
        ),
    )
    out = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msgs = await sub.fetch(10, timeout=max(0.1, deadline - time.monotonic()))
        except (TimeoutError, nats.errors.TimeoutError):
            break
        for m in msgs:
            out.append(
                (m.data, json.loads(m.data.decode()), dict(m.headers or {}), m.metadata.sequence.stream)
            )
            await m.ack()
    return out


def canonical(subject: str) -> dict:
    return {
        SUBMISSION_ACCEPTED: {
            "submission_id": SID,
            "student_id": STU,
            "course_id": CRS,
            "section_id": SEC,
            "question_version_id": QVI,
            "intake": "api",
        },
        EVALUATION_STARTED: {
            "submission_id": SID,
            "evaluation_run_id": RUN,
            "question_version_id": QVI,
            "stage": "dynamic_execution",
            "package_digest": HEX64,
            "runner_policy_digest": HEX64,
            "environment_digest": HEX64,
        },
        EVALUATION_PROGRESS: {
            "evaluation_run_id": RUN,
            "stage": "dynamic_execution",
            "sequence": 7,
            "cases_total": 5,
            "cases_done": 2,
        },
        EVALUATION_COMPLETED: {
            "submission_id": SID,
            "evaluation_run_id": RUN,
            "question_version_id": QVI,
            "score": 86,
            "max_score": 100,
            "is_pass": True,
            "result_digest": HEX64,
        },
        EVALUATION_FAILED: {
            "evaluation_run_id": RUN,
            "submission_id": SID,
            "question_version_id": QVI,
            "stage": "dynamic_execution",
            "failure_category": "timeout",
            "terminal_reason": "infrastructure_error",
        },
    }[subject]


def enq(engine: Engine, subject: str, payload=None, key: str | None = None) -> str:
    moment = utc_now()
    event = build_event(
        subject=subject,
        payload=canonical(subject) if payload is None else payload,
        correlation_id=COR,
        occurred_at=moment,
    )
    with engine.begin() as connection:
        result = enqueue(connection, event, deduplication_key=key, enqueued_at=moment)
    assert result.deduplicated is False
    return event.event_id


def row_of(engine: Engine, event_id: str):
    with engine.connect() as connection:
        return connection.execute(
            sa.select(outbox_events).where(outbox_events.c.event_id == uuid.UUID(event_id))
        ).one()


def raw_insert(engine: Engine, subject: str, payload: dict) -> str:
    """绕过写入端直接插行（模拟写入端把敏感内容写进 payload）。"""
    moment = utc_now()
    eid = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(outbox_events).values(
                event_id=eid,
                subject=subject,
                event_type=subject,
                schema_version=1,
                producer="B2",
                occurred_at=moment,
                correlation_id=uuid.UUID(COR),
                causation_id=None,
                deduplication_key=None,
                payload=payload,
                enqueued_at=moment,
                published_at=None,
                attempts=0,
                last_error=None,
                dead_lettered_at=None,
            )
        )
    return str(eid)


def review_envelope(event_id: str | None = None, event_type: str = QUESTION_VERSION_PUBLISHED) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": "2026-09-15T08:00:05.000Z",
        "correlation_id": COR,
        "causation_id": None,
        "producer": "B3",
        "payload": {"question_version_id": QVI, "course_id": CRS, "version": 3},
    }


@pytest.fixture()
def effects(upgraded_database_url: str) -> Iterator[str]:
    """复核者自建的业务效果落点（无唯一约束：重复写入会**看得见**）。"""
    engine = create_engine(upgraded_database_url)
    with engine.begin() as connection:
        connection.execute(
            text(f"CREATE TABLE IF NOT EXISTS {PROBE_EFFECT_TABLE} (event_id uuid, note text)")
        )
        connection.execute(text(f"TRUNCATE {PROBE_EFFECT_TABLE}"))
    yield PROBE_EFFECT_TABLE
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {PROBE_EFFECT_TABLE}"))
    engine.dispose()


def effect_count(engine: Engine, event_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(f"SELECT count(*) FROM {PROBE_EFFECT_TABLE} WHERE event_id = :e"),
                {"e": uuid.UUID(event_id)},
            ).scalar_one()
        )


def inbox_rows(engine: Engine, consumer: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.count())
                .select_from(inbox_events)
                .where(inbox_events.c.consumer == consumer)
            ).scalar_one()
        )


# =======================================================================================
# 标准 1：真实发布 5 类 + envelope 过 MAIN schema + payload 白名单 + 无敏感标记
# =======================================================================================


def test_review_1_contract_digest_matches_main() -> None:
    """先确认 B2 校验用的就是 MAIN 的实时 schema（无本地副本、无漂移）。"""
    digest = hashlib.sha256()
    for rel in sorted(
        p.relative_to(MAIN_CONTRACTS).as_posix()
        for p in MAIN_CONTRACTS.rglob("*")
        if p.is_file()
    ):
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update((MAIN_CONTRACTS / rel).read_bytes())
    real = digest.hexdigest()
    locked = ""
    for line in (B2_ROOT / "contracts.lock").read_text(encoding="utf-8").splitlines():
        if line.startswith("digest:"):
            locked = line.split(":", 1)[1].strip()
    print(f"\n[1] MAIN contracts digest={real}")
    print(f"[1] contracts.lock digest={locked}")
    assert real == locked
    assert MAIN_SCHEMA.is_file()


@db
@broker
def test_review_1_five_events_really_published_and_validated(engine: Engine) -> None:
    """5 类事件经**真实** outbox→JetStream 投递，逐条回读并独立断言三条。"""
    ids = {s: enq(engine, s, key=f"review:{s}") for s in PUBLISHED_SUBJECTS}

    async def scenario():
        async with bus() as b, stream(b) as st:
            report = await NatsOutboxDispatcher(engine=engine, bus=b).dispatch(limit=50)
            return report, await drain(b, st, "autograder.v1.>", timeout=3.0)

    report, got = asyncio.run(scenario())
    print(f"\n[1] dispatch report={report}")
    assert (report.selected, report.published, report.failed) == (5, 5, 0), report

    types = [env["event_type"] for _, env, _, _ in got]
    print(f"[1] 真实回读到 {len(got)} 条: {sorted(types)}")
    assert sorted(types) == sorted(PUBLISHED_SUBJECTS), "5 类事件必须全部真实出现"
    assert len(got) == 5

    for raw, env, headers, seq in got:
        subject = env["event_type"]
        # ① envelope 过 MAIN schema（复核者自己的校验器）
        my_errors = independent_errors(env)
        print(f"[1] {subject}: independent schema errors={my_errors} seq={seq}")
        assert my_errors == [], f"{subject} 未过 MAIN event-envelope schema"
        # ② payload 键集 == 白名单
        keys = set(env["payload"])
        allowed = set(allowed_keys(subject))
        print(f"[1] {subject}: payload keys={sorted(keys)} allowed={sorted(allowed)}")
        assert keys == allowed, f"{subject} payload 键集 != allowed_keys"
        # ③ 序列化字节里搜不到敏感标记（搜**真实收到的原始字节**）
        hits = scan_markers(raw)
        print(f"[1] {subject}: marker hits={hits}")
        assert hits == []
        assert headers["Nats-Msg-Id"] == ids[subject]
        assert env["producer"] == "B2"
        assert env["payload"] == canonical(subject), f"{subject} payload 值被改写"
    for subject, eid in ids.items():
        row = row_of(engine, eid)
        assert row.published_at is not None and row.dead_lettered_at is None
        assert row.attempts == 0 and row.last_error is None, f"{subject} {row.attempts}"


@db
@broker
def test_review_1_leaky_and_poisoned_payloads_never_reach_the_stream(engine: Engine) -> None:
    """证伪尝试：把 5 类敏感内容塞进 payload（未知键 / 白名单键 / 嵌套），真实流里必须一条都没有。"""
    leaky_ids = []
    for subject in PUBLISHED_SUBJECTS:
        leaky_ids.append(
            raw_insert(
                engine,
                subject,
                {
                    **canonical(subject),
                    "source_code": M_SRC,
                    "reference_answer": M_ANS,
                    "hidden_case_expected_output": M_HID,
                    "access_token": M_CRED,
                    "storage_path": M_PATH,
                    "student_email": M_PII,
                },
            )
        )
    # 白名单键被塞进自由文本（源码 / 凭据 / 路径）
    for subject, key in (
        (SUBMISSION_ACCEPTED, "submission_id"),
        (EVALUATION_STARTED, "package_digest"),
        (EVALUATION_PROGRESS, "stage"),
        (EVALUATION_COMPLETED, "score"),
        (EVALUATION_FAILED, "failure_category"),
    ):
        leaky_ids.append(raw_insert(engine, subject, {**canonical(subject), key: M_SRC}))
    # 嵌套结构藏源码
    leaky_ids.append(
        raw_insert(engine, EVALUATION_PROGRESS, {**canonical(EVALUATION_PROGRESS), "extra": {"src": M_SRC}})
    )

    async def scenario():
        async with bus() as b, stream(b) as st:
            r1 = await NatsOutboxDispatcher(engine=engine, bus=b).dispatch(limit=50)
            r2 = await NatsOutboxDispatcher(engine=engine, bus=b).dispatch(limit=50)
            return r1, r2, await drain(b, st, "autograder.v1.>", timeout=1.5)

    r1, r2, got = asyncio.run(scenario())
    print(f"\n[1-证伪] r1={r1}")
    print(f"[1-证伪] r2={r2}")
    print(f"[1-证伪] 真实流消息数={len(got)}")
    assert got == [], "任何违约 payload 都不得进入真实流"
    assert r1.published == 0 and r1.failed == 11 and r1.dead_lettered == 0, r1
    for eid in leaky_ids:
        row = row_of(engine, eid)
        assert row.published_at is None and row.attempts == 2 and row.dead_lettered_at is None
        assert scan_markers(row.last_error or "") == [], "last_error 不得回显取值"
        assert row.last_error and row.last_error.strip()
    print(f"[1-证伪] 样例 last_error={row_of(engine, leaky_ids[0]).last_error!r}")


# =======================================================================================
# 标准 2：inbox 去重（两种证伪）
# =======================================================================================


@db
@broker
def test_review_2_same_event_id_twice_one_effect(engine: Engine, effects: str) -> None:
    """证伪①：同一 envelope 连续投递两次（真流、两条不同 stream seq）→ 业务效果只发生一次。"""
    env = review_envelope()
    body = json.dumps(env, sort_keys=True).encode()
    calls: list[str] = []

    def handler(connection, received) -> None:
        calls.append(str(received["event_id"]))
        connection.execute(
            text(f"INSERT INTO {PROBE_EFFECT_TABLE} (event_id, note) VALUES (:e, 'effect')"),
            {"e": uuid.UUID(str(received["event_id"]))},
        )

    consumer = InboxConsumer(engine, handler, consumer="review-dedup")
    seqs: list[int] = []

    async def scenario():
        async with bus() as b, stream(b) as st:
            await b.publish(QUESTION_VERSION_PUBLISHED, body)
            await b.publish(QUESTION_VERSION_PUBLISHED, body)
            report = await consumer.run(
                b, durable="review-dedup", stream=st, max_messages=2, fetch_timeout=1.5
            )
            # 独立证据：两条消息确实是流里的两条不同消息（不是 JetStream Msg-Id 去重吃掉的）
            sub = await b.jetstream().pull_subscribe(
                QUESTION_VERSION_PUBLISHED,
                durable=f"review-verify-{uuid.uuid4().hex[:6]}",
                stream=st,
                config=api.ConsumerConfig(
                    deliver_policy=api.DeliverPolicy.ALL, ack_policy=api.AckPolicy.EXPLICIT
                ),
            )
            msgs = await sub.fetch(2, timeout=2.0)
            for m in msgs:
                seqs.append(m.metadata.sequence.stream)
                await m.ack()
            return report

    report = asyncio.run(scenario())
    print(f"\n[2①] report={report}")
    print(f"[2①] 流内两条消息的 stream seq={seqs}")
    print(f"[2①] handler 调用={calls}")
    print(f"[2①] 效果表行数={effect_count(engine, env['event_id'])}")
    print(f"[2①] inbox 行数={inbox_rows(engine, 'review-dedup')}")

    assert report.received == 2 and report.failed == 0, report
    assert seqs == [1, 2], "必须是流里两条真实独立消息（排除 Msg-Id 去重窗口的干扰）"
    # 独立计数证据（不依赖 duplicate 布尔）
    assert calls == [env["event_id"]], "业务效果回调只允许一次"
    assert effect_count(engine, env["event_id"]) == 1, "业务效果行数必须为 1"
    assert inbox_rows(engine, "review-dedup") == 1, "inbox 只允许一行"
    assert report.handled == 1 and report.duplicates == 1


@db
def test_review_2_concurrent_double_delivery_one_effect(engine: Engine, effects: str) -> None:
    """证伪②：**并发**同 event_id 投递（两个线程、两条连接）→ 业务效果仍只发生一次。"""
    env = review_envelope()
    barrier = threading.Barrier(2)
    calls: list[str] = []

    def handler(connection, received) -> None:
        calls.append(str(received["event_id"]))
        time.sleep(0.05)  # 拉长事务窗口，让并发冲突真的发生
        connection.execute(
            text(f"INSERT INTO {PROBE_EFFECT_TABLE} (event_id, note) VALUES (:e, 'effect')"),
            {"e": uuid.UUID(str(received["event_id"]))},
        )

    consumer = InboxConsumer(engine, handler, consumer="review-concurrent")

    def worker() -> bool:
        barrier.wait()
        return consumer.consume(env).handled

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result() for f in [pool.submit(worker), pool.submit(worker)]]

    print(f"\n[2②] handled 结果={outcomes}")
    print(f"[2②] handler 调用={calls}")
    print(f"[2②] 效果表行数={effect_count(engine, env['event_id'])}")
    print(f"[2②] inbox 行数={inbox_rows(engine, 'review-concurrent')}")
    assert sorted(outcomes) == [False, True], "并发下只能有一个真的执行业务效果"
    assert calls == [env["event_id"]]
    assert effect_count(engine, env["event_id"]) == 1
    assert inbox_rows(engine, "review-concurrent") == 1


@db
def test_review_2_distinct_event_ids_both_effect(engine: Engine, effects: str) -> None:
    """对照组：不同 event_id（同 payload）必须各产生一次效果 —— 去重按 event_id，不是按内容。"""
    calls: list[str] = []
    consumer = InboxConsumer(
        engine, lambda c, r: calls.append(str(r["event_id"])), consumer="review-control"
    )
    a, b = review_envelope(), review_envelope()
    for env in (a, b):
        outcome = consumer.consume(env)
        assert outcome.handled is True
    print(f"\n[2-对照] 两次调用={calls} inbox={inbox_rows(engine, 'review-control')}")
    assert calls == [a["event_id"], b["event_id"]]
    assert inbox_rows(engine, "review-control") == 2


@db
@broker
def test_review_2_unknown_event_type_recorded_and_ignored(engine: Engine, effects: str) -> None:
    """MAIN/docs/api/events.md §5：未识别 event_type 记录并忽略、不写 inbox、不产生业务效果、ack。"""
    foreign = review_envelope(event_type=EVALUATION_COMPLETED)
    calls: list[str] = []
    consumer = InboxConsumer(
        engine, lambda c, r: calls.append(str(r["event_type"])), consumer="review-foreign"
    )
    body = json.dumps(foreign).encode()

    async def scenario():
        async with bus() as b, stream(b) as st:
            await b.publish(QUESTION_VERSION_PUBLISHED, body)
            report = await consumer.run(
                b, durable="review-foreign", stream=st, max_messages=1, fetch_timeout=1.5
            )
            # ack 是异步发出的：轮询到稳定为止（最多 5s），避免把"在途"误判成"没 ack"。
            info = await b.jetstream().consumer_info(st, "review-foreign")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not (
                info.ack_floor.stream_seq == 1 and info.num_ack_pending == 0
            ):
                await asyncio.sleep(0.1)
                info = await b.jetstream().consumer_info(st, "review-foreign")
            # ack 之后同 durable 再取：不得有任何重投
            sub = await b.jetstream().pull_subscribe(
                QUESTION_VERSION_PUBLISHED,
                durable="review-foreign",
                stream=st,
                config=api.ConsumerConfig(
                    deliver_policy=api.DeliverPolicy.ALL, ack_policy=api.AckPolicy.EXPLICIT
                ),
            )
            with contextlib.suppress(TimeoutError, nats.errors.TimeoutError):
                again = await sub.fetch(1, timeout=1.0)
                return report, info, [m.metadata.num_delivered for m in again]
            return report, info, []

    report, info, redelivered = asyncio.run(scenario())
    print(f"\n[2§5] report={report}")
    print(
        f"[2§5] consumer_info: ack_floor={info.ack_floor.stream_seq} "
        f"num_ack_pending={info.num_ack_pending} num_redelivered={info.num_redelivered}"
    )
    print(f"[2§5] handler 调用={calls} inbox={inbox_rows(engine, 'review-foreign')}")
    print(f"[2§5] 同 durable 再取得到={redelivered}（应为空）")

    assert report.received == 1 and report.ignored == 1 and report.failed == 0
    assert report.ignored_event_types == (EVALUATION_COMPLETED,), "必须被记录下来（不是静默丢弃）"
    assert calls == [], "未识别事件不得产生业务效果"
    assert inbox_rows(engine, "review-foreign") == 0, "被忽略的事件不写 inbox"
    assert info.num_ack_pending == 0, "必须 ack（不能靠 nak 反复重投）"
    assert info.ack_floor.stream_seq == 1, "该消息已被确认（不静默丢弃也不悬挂）"
    assert redelivered == []


# =======================================================================================
# 标准 3：重试与 DLQ（真实数值）
# =======================================================================================


@db
@broker
def test_review_3_retry_dlq_and_replay(engine: Engine) -> None:
    """真实数值：attempts / published_at / dead_lettered_at / last_error + DLQ 可查询 + replay 真投递。"""
    eid = enq(engine, EVALUATION_COMPLETED, key="review:dlq")
    dispatcher = NatsOutboxDispatcher(
        engine=engine,
        bus=NatsEventBus(
            url=UNREACHABLE_WITH_CREDENTIAL,
            connect_timeout=0.5,
            reconnect_time_wait=0.1,
            max_reconnect_attempts=1,
        ),
        max_attempts=3,
    )

    async def one_round():
        return await asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0)

    reports = []
    for index in range(1, 4):
        started = time.monotonic()
        report = asyncio.run(one_round())
        elapsed = time.monotonic() - started
        row = row_of(engine, eid)
        reports.append(report)
        print(
            f"\n[3] 第{index}轮 {elapsed:.2f}s report={report}\n"
            f"[3]   attempts={row.attempts} published_at={row.published_at} "
            f"dead_lettered_at={row.dead_lettered_at}\n"
            f"[3]   last_error={row.last_error!r}"
        )
        assert row.published_at is None, "投递失败必须保留在 outbox（不得标记已发布）"
        assert row.attempts == index
        assert row.last_error and row.last_error.strip()
        assert scan_internal(row.last_error) == [], "last_error 不得含内部地址/凭据"
        assert scan_markers(row.last_error) == []
        if index < 3:
            assert row.dead_lettered_at is None
        else:
            assert row.dead_lettered_at is not None, "第 3 次（== 上限）必须进 DLQ"
            assert report.dead_lettered == 1

    # 超上限后不再自动重投
    fourth = asyncio.run(one_round())
    print(f"[3] 第4轮 report={fourth}（DLQ 行不应再被选中）")
    assert fourth.selected == 0 and fourth.published == 0

    # DLQ 可查询
    letters = dispatcher.dead_letters()
    print(f"[3] dead_letters={letters}")
    assert [item.event_id for item in letters] == [eid]
    letter = letters[0]
    assert letter.subject == EVALUATION_COMPLETED and letter.attempts == 3
    assert letter.last_error and scan_internal(letter.last_error) == []
    assert letter.dead_lettered_at is not None and letter.enqueued_at is not None
    # 不静默丢失：行与 payload 都还在
    kept = row_of(engine, eid)
    assert dict(kept.payload) == canonical(EVALUATION_COMPLETED), "DLQ 行不得丢 payload"
    # replay 只对 DLQ 行生效
    assert dispatcher.replay(eid) is True
    assert dispatcher.replay(eid) is False
    after = row_of(engine, eid)
    print(f"[3] replay 后 attempts={after.attempts} dead_lettered_at={after.dead_lettered_at}")
    assert after.dead_lettered_at is None and after.attempts == 0
    assert dispatcher.dead_letters() == ()

    async def recovery():
        async with bus() as b, stream(b) as st:
            report = await NatsOutboxDispatcher(engine=engine, bus=b).dispatch(limit=10)
            got = await drain(b, st, EVALUATION_COMPLETED, timeout=3.0)
            return report, got

    report, got = asyncio.run(recovery())
    final = row_of(engine, eid)
    print(
        f"[3] replay 后真实投递 report={report} 收到={len(got)} 条 "
        f"published_at={final.published_at} attempts={final.attempts} "
        f"last_error={final.last_error!r}"
    )
    assert (report.published, report.failed) == (1, 0), report
    assert len(got) == 1 and got[0][1]["event_id"] == eid, "replay 后必须真的投递成功"
    assert got[0][1]["payload"] == canonical(EVALUATION_COMPLETED)
    assert final.published_at is not None and final.dead_lettered_at is None
    assert reports[2].dead_lettered == 1 and reports[0].dead_lettered == 0


@db
def test_review_3_unreachable_dispatch_is_bounded(engine: Engine) -> None:
    """证伪尝试：不可达地址必须**快速失败**（不长时间重连）——硬超时兜底。"""
    enq(engine, SUBMISSION_ACCEPTED, key="review:slow")
    dispatcher = NatsOutboxDispatcher(
        engine=engine,
        bus=NatsEventBus(url=UNREACHABLE, connect_timeout=0.5, reconnect_time_wait=0.1,
                         max_reconnect_attempts=1),
        max_attempts=5,
    )
    started = time.monotonic()
    report = asyncio.run(asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0))
    elapsed = time.monotonic() - started
    bus_unavailable_seen = False
    try:
        asyncio.run(
            asyncio.wait_for(
                NatsEventBus(url=UNREACHABLE_WITH_CREDENTIAL, connect_timeout=0.5).connect(),
                timeout=25.0,
            )
        )
    except EventBusUnavailable as error:
        bus_unavailable_seen = True
        print(f"\n[3-边界] 连接异常文本={error}")
        assert "sekretpw" not in str(error), "异常文本不得带凭据"
    print(f"[3-边界] 一轮失败耗时={elapsed:.2f}s failed={report.failed}")
    print(f"[3-边界] EventBusUnavailable 被抛出={bus_unavailable_seen}")
    assert elapsed < 20.0, "不可达地址必须快速失败"
    assert report.failed == 1 and report.published == 0
    assert bus_unavailable_seen is True


# =======================================================================================
# 附加证伪：断言是否恒真 / 边界
# =======================================================================================


def test_review_a_schema_check_has_teeth() -> None:
    """证明 `envelope_errors` 不是恒真空函数：拿 MAIN 官方示例做正/负样本对照。"""
    from autograder_b2.events.payload import envelope_errors

    example = json.loads(
        (MAIN_CONTRACTS / "examples" / "event-evaluation-completed.json").read_text(encoding="utf-8")
    )
    missing = {k: v for k, v in example.items() if k != "producer"}
    extra = {**example, "future_field": "x"}
    bad_pattern = {**example, "event_type": "not-a-namespaced-type"}
    for label, sample in (
        ("official-example", example),
        ("missing-producer", missing),
        ("extra-field", extra),
        ("bad-event-type", bad_pattern),
    ):
        mine = independent_errors(sample)
        theirs = envelope_errors(sample)
        print(f"\n[A] {label}: independent={mine != []} b2_helper={theirs != []} :: {theirs}")
        assert (mine == []) == (theirs == []), f"{label}: 两侧判定不一致"
    assert envelope_errors(example) == [] and envelope_errors(missing) != []
    assert envelope_errors(extra) != [] and envelope_errors(bad_pattern) != []


def test_review_a_payload_whitelist_has_teeth() -> None:
    """证明白名单**同时是必填键集**：逐键投毒/加未知键/缺**必填**键都必须被察觉，且不得误拒可选键。

    （复核者原以本探针记录"白名单不要求键完整 ⇒ 缺键 payload 照发"的缺口；缺口已修
    （`REQUIRED_KEYS`），本探针改为**钉住修复**。口径：必填集是准许集的**子集**——除
    `evaluation.progress` 的计数键外等于全集；`cases_total`/`cases_done` 是**按阶段可缺**的计数
    （写入端 `_enter_stage(..., cases_total=None)` 会刻意省略），列为必填会让该形状永不投递。
    因此：缺必填键必须被点名拒绝；缺**可选**计数必须仍然放行。断言只收紧、不放松。）
    """
    from autograder_b2.events.payload import (
        PayloadRejected,
        allowed_keys,
        payload_rejections,
        required_keys,
    )

    wrong_verdicts: list[str] = []
    for subject in PUBLISHED_SUBJECTS:
        good = canonical(subject)
        assert payload_rejections(subject, good) == []
        allowed = set(allowed_keys(subject))
        required = set(required_keys(subject))
        assert required <= allowed, f"{subject}: 必填键必须是准许键的子集（多出 {required - allowed}）"
        if subject != EVALUATION_PROGRESS:
            assert required == allowed, f"{subject}: 除 progress 外必填集应等于准许集"
        for key in allowed_keys(subject):
            poisoned = {**good, key: M_SRC}
            reasons = payload_rejections(subject, poisoned)
            assert reasons, f"{subject}.{key} 塞进自由文本必须被拒"
            assert M_SRC not in " ".join(reasons), "拒绝原因不得回显取值"
        assert payload_rejections(subject, {**good, "src": M_SRC}), "未知键必须被拒"
        # 逐个键各缺一次：必填键必须被点名拒绝；可选键（按阶段可缺的计数）必须仍然被接受。
        for dropped in allowed_keys(subject):
            partial = {k: v for k, v in good.items() if k != dropped}
            reasons = payload_rejections(subject, partial)
            if dropped in required:
                if not reasons:
                    wrong_verdicts.append(f"{subject} 缺必填键 {dropped} → 竟被接受")
                    continue
                assert any(dropped in reason for reason in reasons), (
                    f"{subject} 缺 {dropped} 的拒绝原因必须点名该键，实得 {reasons}"
                )
            elif reasons:
                wrong_verdicts.append(f"{subject} 缺可选键 {dropped} → 竟被拒绝：{reasons}")
        print(
            f"[A2] {subject}: 允许键={sorted(allowed)} 必填键={sorted(required)} "
            "单键投毒/缺必填键全部被拒"
        )
    print(f"[A2] 判定反例={wrong_verdicts}")
    assert wrong_verdicts == [], f"缺键判定不符合口径：{wrong_verdicts}"
    with pytest.raises(PayloadRejected):
        allowed_keys("autograder.v1.roster.import.completed")


@db
@broker
def test_review_1_production_writer_payloads_must_pass_the_gate(engine: Engine) -> None:
    """生产写入端（orchestrator / intake）**真实** payload 形状必须能过派发门 —— 逐个核对。

    （复核者原以本探针记录"internal_error 不在 evaluation.failed 枚举内 ⇒ 可达行永不投递"的缺陷；
    缺陷修复后本探针**改为钉住修复**：`_finish_failure` 的每个可达终态原因都必须过门，且该行必须
    在真实 broker 上投递成功。断言只收紧、不放松。）

    payload 形状逐字取自源码调用点：
    - `src/autograder_b2/evaluation/orchestrator.py:416-424`（evaluation.started）
    - `src/autograder_b2/evaluation/orchestrator.py:816-822`（evaluation.completed）
    - `src/autograder_b2/evaluation/orchestrator.py:912-919`（evaluation.failed，failure_category=terminal_reason）
    - `src/autograder_b2/evaluation/orchestrator.py:1054-1062`（evaluation.progress）
    - `src/autograder_b2/intake/service.py:312-320`（submission.accepted）
    """
    from autograder_b2.events.payload import payload_rejections

    production = {
        SUBMISSION_ACCEPTED: {
            "submission_id": SID,
            "student_id": STU,
            "course_id": CRS,
            "section_id": SEC,
            "question_version_id": QVI,
            "intake": "api",
        },
        EVALUATION_STARTED: {
            "submission_id": SID,
            "evaluation_run_id": RUN,
            "question_version_id": QVI,
            "stage": "static_analysis",
            "package_digest": HEX64,
            "runner_policy_digest": HEX64,
            "environment_digest": HEX64,
        },
        EVALUATION_PROGRESS: {
            "evaluation_run_id": RUN,
            "stage": "static_analysis",
            "sequence": 1,
            "cases_total": 5,
            "cases_done": 0,
        },
        EVALUATION_COMPLETED: {
            "submission_id": SID,
            "evaluation_run_id": RUN,
            "question_version_id": QVI,
            "score": 86.0,
            "max_score": 100.0,
            "is_pass": True,
            "result_digest": HEX64,
        },
    }
    for subject, payload in production.items():
        reasons = payload_rejections(subject, payload)
        print(f"\n[D] 生产 payload {subject}: rejections={reasons}")
        assert reasons == [], f"{subject} 的生产 payload 被派发门拒绝 ⇒ 该事件永不投递"

    # evaluation.failed：生产端把 **terminal_reason** 直接当 failure_category 用。
    # 可达终态原因**从源码调用点推导**（不抄常量表），否则源码改了本探针会静默失守。
    orchestrator_source = (B2_ROOT / "src" / "autograder_b2" / "evaluation" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    reachable_failure_reasons: set[str] = set()
    for segment in orchestrator_source.split("self._finish_failure(")[1:]:
        matched = re.search(r'terminal_reason="([a-z_]+)"', segment)
        assert matched is not None, "无法从源码解析 _finish_failure 的 terminal_reason"
        reachable_failure_reasons.add(matched.group(1))
    print(f"[D] _finish_failure 可达终态原因（源码推导）={sorted(reachable_failure_reasons)}")

    rejected_terminals = {}
    for terminal_reason in sorted(reachable_failure_reasons | {"static_blocked", "security_blocked"}):
        payload = {
            "submission_id": SID,
            "evaluation_run_id": RUN,
            "question_version_id": QVI,
            "stage": "terminal",
            "failure_category": terminal_reason,
            "terminal_reason": terminal_reason,
        }
        reasons = payload_rejections(EVALUATION_FAILED, payload)
        rejected_terminals[terminal_reason] = reasons
        print(f"[D] evaluation.failed(terminal_reason={terminal_reason}): rejections={reasons}")

    # 标准一：**每个可达**终态原因都必须过门。原缺陷是 internal_error 不在枚举内 ⇒ 该行永不投递。
    assert "internal_error" in reachable_failure_reasons, (
        "internal_error 应为 _finish_failure 的可达终态原因（orchestrator.py:331/832/842）"
    )
    broken = [k for k in sorted(reachable_failure_reasons) if rejected_terminals[k]]
    print(f"[D] 可达终态原因里被拒的={broken}")
    assert broken == [], f"可达终态原因被派发门拒绝 ⇒ 该行永不投递、重试到上限进 DLQ：{broken}"

    # 标准二：门仍然拒绝 `static_blocked` —— 它是**运行级拒绝原因**，不是用例级失败类别
    # （MAIN/contracts/schemas/evaluation-report.schema.json 的 failure_category 枚举里没有它）。
    # `security_blocked` 则**在**该枚举内（某用例触碰沙箱即该用例的失败类别），故被接受是契约一致的，
    # 不构成"用 evaluation.failed 表达安全被拒"：表达提交级安全拒绝的正规路径是 `rejected` 终态，而
    # `_finish_rejected` **不发布任何领域事件**（拒绝经 SSE 终态帧送达）。两点都在此钉住。
    assert rejected_terminals["static_blocked"], (
        "static_blocked 是运行级拒绝原因，不应能充当 evaluation.failed 的 failure_category"
    )
    assert rejected_terminals["security_blocked"] == [], (
        "security_blocked 是用例级失败类别（report schema 枚举内有它）⇒ 门接受它是契约一致的"
    )
    rejected_body = orchestrator_source.split("def _finish_rejected(")[1].split("\n    def ")[0]
    assert "self._enqueue(" not in rejected_body, (
        "_finish_rejected 不得发布领域事件：拒绝只对提交会话有意义，经 SSE 终态帧送达"
    )

    # 后果（修复后）：该行首次真实派发即投递成功 —— 真实 DB + 真实 broker，不再进 DLQ。
    eid = raw_insert(
        engine,
        EVALUATION_FAILED,
        {
            "submission_id": SID,
            "evaluation_run_id": RUN,
            "question_version_id": QVI,
            "stage": "terminal",
            "failure_category": "internal_error",
            "terminal_reason": "internal_error",
        },
    )
    dispatcher = NatsOutboxDispatcher(
        engine=engine,
        bus=NatsEventBus(
            url=NATS_URL, connect_timeout=2.0, reconnect_time_wait=0.1, max_reconnect_attempts=1
        ),
        max_attempts=2,
    )

    async def scenario():
        async with bus() as b, stream(b) as st:
            r1 = await asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0)
            r2 = await asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0)
            return r1, r2, await drain(b, st, "autograder.v1.>", timeout=1.5)

    r1, r2, got = asyncio.run(scenario())
    row = row_of(engine, eid)
    mine = [
        envelope
        for _raw, envelope, _headers, _seq in got
        if envelope.get("event_type") == EVALUATION_FAILED
        and (envelope.get("payload") or {}).get("submission_id") == SID
        and (envelope.get("payload") or {}).get("terminal_reason") == "internal_error"
    ]
    print(f"[D] 真实派发 r1={r1}\n[D] 真实派发 r2={r2}\n[D] 流内 {len(got)} 条，本行 {len(mine)} 条")
    print(f"[D] attempts={row.attempts} published_at={row.published_at} "
          f"dead_lettered_at={row.dead_lettered_at} last_error={row.last_error!r}")
    # 本用例只插了**这一行**（`:868-880`），故"至少 1 条"没有意义：必须恰好 1 条。
    assert r1.published == 1, f"internal_error 行应当首次派发即投递成功（恰好 1 条），实得 r1={r1}"
    assert r2.published == 0, f"已发布的行不得被重复投递，实得 r2={r2}"
    assert len(mine) == 1, f"evaluation.failed(failure_category=internal_error) 未落到真实流：{got}"
    # 落流的那条必须**逐字段**是本次修的取值：failure_category 就是 internal_error（不只是 terminal_reason）。
    streamed_payload = mine[0]["payload"]
    assert streamed_payload["failure_category"] == "internal_error"
    assert streamed_payload["terminal_reason"] == "internal_error"
    assert streamed_payload["evaluation_run_id"] == RUN
    # 首次即成功 ⇒ attempts 保持 0（只有 `_record_failure` 才自增），且无 last_error / 无 DLQ。
    assert row.attempts == 0 and row.published_at is not None
    assert row.dead_lettered_at is None and row.last_error is None


@db
@broker
def test_review_3_boundary_max_attempts_one(engine: Engine) -> None:
    """边界：max_attempts=1 → 首次失败即进 DLQ；DLQ 行不被任何后续轮次重投。"""
    eid = enq(engine, EVALUATION_FAILED, key="review:boundary")
    dispatcher = NatsOutboxDispatcher(
        engine=engine,
        bus=NatsEventBus(url=UNREACHABLE, connect_timeout=0.5, reconnect_time_wait=0.1,
                         max_reconnect_attempts=1),
        max_attempts=1,
    )
    first = asyncio.run(asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0))
    row = row_of(engine, eid)
    print(f"\n[B] max_attempts=1 首轮 report={first} attempts={row.attempts} "
          f"dead_lettered_at={row.dead_lettered_at}")
    assert first.failed == 1 and first.dead_lettered == 1
    assert row.attempts == 1 and row.dead_lettered_at is not None and row.published_at is None
    again = asyncio.run(asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0))
    assert again.selected == 0, "DLQ 行不得被自动重投"
    # replay 对已发布的行不生效
    published_id = enq(engine, SUBMISSION_ACCEPTED, key="review:pub")

    async def publish_ok():
        async with bus() as b, stream(b) as st:
            r = await NatsOutboxDispatcher(engine=engine, bus=b).dispatch(limit=10)
            return r, await drain(b, st, SUBMISSION_ACCEPTED, timeout=2.0)

    report, got = asyncio.run(publish_ok())
    assert report.published == 1 and len(got) == 1
    assert dispatcher.replay(published_id) is False, "已发布的行不得被 replay 复活"
    assert dispatcher.replay(eid) is True, "DLQ 行必须可 replay"


@db
@broker
def test_review_3_foreign_subject_row_is_not_published(engine: Engine) -> None:
    """B2 不发布的 subject 行（如 B4 的 roster 事件）：不得被发布出去，且会被如实记账。"""
    eid = raw_insert(
        engine,
        "autograder.v1.roster.import.completed",
        {"roster_import_id": SID, "course_id": CRS, "section_id": SEC, "accepted_rows": 1},
    )
    dispatcher = NatsOutboxDispatcher(
        engine=engine,
        bus=NatsEventBus(
            url=NATS_URL, connect_timeout=2.0, reconnect_time_wait=0.1, max_reconnect_attempts=1
        ),
        max_attempts=2,
    )

    async def scenario():
        async with bus() as b, stream(b) as st:
            r1 = await asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0)
            r2 = await asyncio.wait_for(dispatcher.dispatch(limit=10), timeout=25.0)
            return r1, r2, await drain(b, st, "autograder.v1.>", timeout=1.5)

    r1, r2, got = asyncio.run(scenario())
    row = row_of(engine, eid)
    print(f"\n[C] r1={r1}\n[C] r2={r2}\n[C] 流内消息={len(got)} attempts={row.attempts} "
          f"dead_lettered_at={row.dead_lettered_at}\n[C] last_error={row.last_error!r}")
    assert r1.published == 0 and r2.published == 0 and got == []
    assert row.attempts == 2 and row.dead_lettered_at is not None
    assert row.last_error and scan_internal(row.last_error) == []


@broker
def test_review_3_streams_and_tables_left_clean() -> None:
    """清理核对：本次复核不得在 JetStream 留下任何探针流（并列出既有流供人工核对）。"""

    async def listing():
        async with bus() as b:
            infos = await b.jetstream().streams_info()
            return [i.config.name for i in infos]

    names = asyncio.run(listing())
    print(f"\n[清理] 现存流={names}")
    assert not [n for n in names if "REVIEW" in n or "PROBE" in n or "TEST" in n], names
