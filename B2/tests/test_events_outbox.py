"""事件 outbox 派发与 inbox 去重的**真实**验收（T-EVT-001..017）。

不用 mock 冒充交付：

- 数据库是 `conftest.py` 的真实 PostgreSQL 17 临时库（模块级迁移一次、用例间清空 `b2` 全表）；
- broker 是**真实 NATS JetStream**：发布走 `js.publish`（等 PubAck），读回走真实 durable pull
  consumer；每个用例自建一个唯一命名的流并在结束时删除——**流的生产配额与保留策略由 B4 负责**
  （RQ-B-4-20），测试建流只是为了让真实投递有落点；
- broker 不可达时**显式 skip 并给出原因**，绝不伪造通过；"NATS 不可用"的路径用**真实不可达地址**
  （`nats://127.0.0.1:1`）产生真实连接失败，而不是替身对象。

唯一一处故障注入是 `_FailingBus`（脱敏路径用例）：它只用来把"异常文本里带凭据与内部绝对路径"
喂给派发器，验证 `last_error` 落库前确实被脱敏——发布成功路径一律走真实 broker。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from urllib.parse import urlsplit

import nats
import pytest
import sqlalchemy as sa
from nats.js import api
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from autograder_b2.db.schema import artifacts, inbox_events, outbox_events
from autograder_b2.domain.clock import utc_now
from autograder_b2.domain.events import DispatchReport
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
    submission_accepted_payload,
)
from autograder_b2.events.payload import (
    PAYLOAD_KEYS,
    QUESTION_VERSION_PUBLISHED,
    REQUIRED_KEYS,
    EventEnvelopeInvalid,
    PayloadRejected,
    allowed_keys,
    build_envelope,
    envelope_bytes,
    envelope_errors,
    payload_rejections,
    required_keys,
    sanitize_error,
)
from autograder_b2.logging.logger import B2_EVENT_KEY
from tests.conftest import requires_postgres

#: 真实 NATS 地址（容器端口 54223）。不可达时相关用例 skip，绝不伪造 broker。
NATS_URL = os.getenv("B2_TEST_NATS_URL", "nats://localhost:54223")
#: 真实**不可达**地址：用于产生真实的连接失败（不启动/不停止任何容器）。
UNREACHABLE_NATS_URL = "nats://127.0.0.1:1"
#: 带凭据的不可达地址：验证 `last_error` 不含口令。
UNREACHABLE_NATS_URL_WITH_CREDENTIALS = "nats://autograder:secretpw@127.0.0.1:1"
#: 测试流的 subject 过滤（覆盖 B2 的 5 个发布 subject 与 1 个订阅 subject）。
STREAM_SUBJECTS = ("autograder.v1.>",)

#: 业务效果的落点（测试自建、模块结束即删；不在 `b2` product schema 内，也不进迁移）。
PROBE_TABLE = "public.b2_event_effect_probe"

T0 = "00000000-0000-4000-8000-000000000001"
DIGEST = "a" * 64

SUBMISSION_ID = "00000000-0000-4000-8000-000000000101"
STUDENT_ID = "00000000-0000-4000-8000-000000000102"
COURSE_ID = "00000000-0000-4000-8000-000000000103"
SECTION_ID = "00000000-0000-4000-8000-000000000104"
QUESTION_VERSION_ID = "00000000-0000-4000-8000-000000000105"
RUN_ID = "00000000-0000-4000-8000-000000000004"
CORRELATION_ID = "00000000-0000-4000-8000-000000000302"

#: 5 类敏感内容的特征标记：任何一条出现在序列化结果/落库文本里都算泄漏。
MARKER_SOURCE = "MARKER_SOURCE_CODE_print(42)_9f3a"
MARKER_ANSWER = "MARKER_REFERENCE_ANSWER_7b2d"
MARKER_HIDDEN = "MARKER_HIDDEN_CASE_EXPECTED_OUTPUT_5c4e"
MARKER_CREDENTIAL = "MARKER_TOKEN_ghp_2c4d5e6f7a8b9c0d"
MARKER_PATH = "MARKER_PATH_D:\\VscodeProjects\\AutoGrader\\B2\\var\\secret.py"
MARKER_PII = "MARKER_PII_student.name@example.edu"
MARKERS = (
    MARKER_SOURCE,
    MARKER_ANSWER,
    MARKER_HIDDEN,
    MARKER_CREDENTIAL,
    MARKER_PATH,
    MARKER_PII,
)


def _broker_reachable(url: str) -> bool:
    """真实 TCP 探测（不用 broker SDK：探测本身也不该依赖被探测方可用）。"""
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    port = parts.port or 4222
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


requires_jetstream = pytest.mark.skipif(
    not _broker_reachable(NATS_URL),
    reason=f"需要真实 NATS JetStream：{NATS_URL} 不可达（不伪造 broker）",
)
# 依赖**逐用例**声明，不用模块级 `pytestmark`：模块级跳过会把"不需要数据库/broker 的纯逻辑断言"
# 也一起静默跳过（真实教训：`evaluation.failed` 的枚举门与 payload 脱敏断言在没起 broker 的机器上
# 一条都不跑，却看起来"全绿"）。`db` = 需要真实 PostgreSQL；`broker` = 需要真实 JetStream。
db = requires_postgres
broker = requires_jetstream


# --- 夹具与工具 -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def effect_probe(upgraded_database_url: str) -> Iterator[str]:
    """真实 PostgreSQL 里的"业务效果"落点：证明业务效果与 inbox 去重**同事务**。"""
    engine = create_engine(upgraded_database_url)
    with engine.begin() as connection:
        connection.execute(
            text(f"CREATE TABLE IF NOT EXISTS {PROBE_TABLE} (event_id uuid PRIMARY KEY, note text)")
        )
    yield PROBE_TABLE
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {PROBE_TABLE}"))
    engine.dispose()


@contextlib.asynccontextmanager
async def connected_bus(url: str = NATS_URL) -> AsyncIterator[NatsEventBus]:
    """真实连接的真实总线（用例结束即关闭）。"""
    bus = NatsEventBus(url=url, connect_timeout=2.0, reconnect_time_wait=0.1)
    await bus.connect()
    try:
        yield bus
    finally:
        await bus.close()


@contextlib.asynccontextmanager
async def ephemeral_stream(bus: NatsEventBus, subjects: tuple[str, ...] = STREAM_SUBJECTS) -> str:
    """唯一命名的真实流：用完即删（生产流的配额/保留策略由 B4 负责，不在此定义）。"""
    name = f"B2_EVENTS_{uuid.uuid4().hex[:10].upper()}"
    await bus.jetstream().add_stream(name=name, subjects=list(subjects))
    try:
        yield name
    finally:
        await bus.jetstream().delete_stream(name)


async def read_back(
    bus: NatsEventBus,
    stream: str,
    subject: str,
    *,
    expect: int = 1,
    timeout: float = 3.0,
) -> list[tuple[dict, dict]]:
    """从真实流里用一次性 durable consumer 读回消息：返回 `(envelope, headers)`。"""
    durable = f"b2test-{uuid.uuid4().hex[:10]}"
    subscription = await bus.jetstream().pull_subscribe(
        subject,
        durable=durable,
        stream=stream,
        config=api.ConsumerConfig(
            deliver_policy=api.DeliverPolicy.ALL, ack_policy=api.AckPolicy.EXPLICIT
        ),
    )
    collected: list[tuple[dict, dict]] = []
    deadline = time.monotonic() + timeout
    while len(collected) < expect:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            messages = await subscription.fetch(expect - len(collected), timeout=remaining)
        except (TimeoutError, nats.errors.TimeoutError):
            break
        for message in messages:
            collected.append((json.loads(message.data.decode("utf-8")), dict(message.headers or {})))
            await message.ack()
    return collected


def canonical_payload(subject: str) -> dict[str, object]:
    """5 个发布 subject 的**规范 payload**（键集合来源见 `events.payload.PAYLOAD_KEY_SOURCES`）。"""
    if subject == SUBMISSION_ACCEPTED:
        return submission_accepted_payload(
            submission_id=SUBMISSION_ID,
            student_id=STUDENT_ID,
            course_id=COURSE_ID,
            section_id=SECTION_ID,
            question_version_id=QUESTION_VERSION_ID,
            intake="api",
        )
    if subject == EVALUATION_STARTED:
        return {
            "submission_id": SUBMISSION_ID,
            "evaluation_run_id": RUN_ID,
            "question_version_id": QUESTION_VERSION_ID,
            "stage": "dynamic_execution",
            "package_digest": DIGEST,
            "runner_policy_digest": DIGEST,
            "environment_digest": DIGEST,
        }
    if subject == EVALUATION_PROGRESS:
        return {
            "evaluation_run_id": RUN_ID,
            "stage": "dynamic_execution",
            "sequence": 7,
            "cases_total": 5,
            "cases_done": 2,
        }
    if subject == EVALUATION_COMPLETED:
        return {
            "submission_id": SUBMISSION_ID,
            "evaluation_run_id": RUN_ID,
            "question_version_id": QUESTION_VERSION_ID,
            "score": 86,
            # `max_score` / `is_pass` were added by the CHG-B3-0003 ruling: B3's
            # de-identified statistics cannot bucket scores without the first, and its
            # pass rate stays permanently empty without the second.
            "max_score": 100,
            "is_pass": True,
            "result_digest": DIGEST,
        }
    if subject == EVALUATION_FAILED:
        return {
            "evaluation_run_id": RUN_ID,
            "submission_id": SUBMISSION_ID,
            "question_version_id": QUESTION_VERSION_ID,
            "stage": "dynamic_execution",
            "failure_category": "timeout",
            "terminal_reason": "infrastructure_error",
        }
    raise AssertionError(f"no canonical payload for {subject}")


def enqueue_event(
    engine: Engine,
    *,
    subject: str,
    payload: dict[str, object] | None = None,
    deduplication_key: str | None = None,
    enqueued_at=None,
) -> str:
    """用**写入端**（`events.outbox`）真实入队一行，返回 `event_id`。"""
    moment = enqueued_at or utc_now()
    event = build_event(
        subject=subject,
        payload=canonical_payload(subject) if payload is None else payload,
        correlation_id=CORRELATION_ID,
        occurred_at=moment,
    )
    with engine.begin() as connection:
        result = enqueue(
            connection, event, deduplication_key=deduplication_key, enqueued_at=moment
        )
    assert result.deduplicated is False
    assert result.event_id == event.event_id
    return event.event_id


def insert_raw_row(
    engine: Engine, *, subject: str, payload: dict[str, object], enqueued_at=None
) -> str:
    """绕过写入端直接插一行（模拟"写入端 bug 把敏感内容写进 payload"），返回 `event_id`。"""
    moment = enqueued_at or utc_now()
    event_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            pg_insert(outbox_events).values(
                event_id=event_id,
                subject=subject,
                event_type=subject,
                schema_version=1,
                producer="B2",
                occurred_at=moment,
                correlation_id=uuid.UUID(CORRELATION_ID),
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
    return str(event_id)


def outbox_row(engine: Engine, event_id: str) -> object:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(outbox_events).where(outbox_events.c.event_id == uuid.UUID(event_id))
        ).one()


def inbox_count(engine: Engine, *, consumer: str | None = None) -> int:
    statement = sa.select(sa.func.count()).select_from(inbox_events)
    if consumer is not None:
        statement = statement.where(inbox_events.c.consumer == consumer)
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar_one())


def probe_count(engine: Engine, event_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(f"SELECT count(*) FROM {PROBE_TABLE} WHERE event_id = :event_id"),
                {"event_id": uuid.UUID(event_id)},
            ).scalar_one()
        )


def question_version_envelope(event_id: str | None = None) -> dict:
    """订阅侧的一条真实事件（发布方是 B3；B2 只按契约 envelope 结构校验）。"""
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": QUESTION_VERSION_PUBLISHED,
        "schema_version": 1,
        "occurred_at": "2026-09-15T08:00:05.000Z",
        "correlation_id": CORRELATION_ID,
        "causation_id": None,
        "producer": "B3",
        "payload": {
            "question_version_id": QUESTION_VERSION_ID,
            "course_id": COURSE_ID,
            "version": 3,
            "package_digest": DIGEST,
        },
    }


def failing_dispatcher(engine: Engine, url: str, *, max_attempts: int = 5) -> NatsOutboxDispatcher:
    """指向不可达地址的派发器：每次 `publish` 都发生**真实**连接失败。"""
    bus = NatsEventBus(
        url=url, connect_timeout=0.5, reconnect_time_wait=0.1, max_reconnect_attempts=1
    )
    return NatsOutboxDispatcher(engine=engine, bus=bus, max_attempts=max_attempts)


# --- T-EVT-001：真实发布 → 真实读回 ------------------------------------------------------


@db
@broker
def test_publish_to_real_jetstream_and_read_back(engine: Engine) -> None:
    """T-EVT-001：outbox 行投递到真实 JetStream，从真实订阅端读回且 envelope 过契约 schema。"""
    event_id = enqueue_event(
        engine, subject=SUBMISSION_ACCEPTED, deduplication_key=f"submission:{SUBMISSION_ID}"
    )

    async def scenario() -> tuple[DispatchReport, list[tuple[dict, dict]]]:
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            report = await NatsOutboxDispatcher(engine=engine, bus=bus).dispatch(limit=10)
            received = await read_back(bus, stream, SUBMISSION_ACCEPTED, expect=1)
            return report, received

    report, received = asyncio.run(scenario())

    assert report == DispatchReport(
        selected=1, published=1, failed=0, dead_lettered=0, last_error=None
    )
    assert len(received) == 1, "真实流里必须能读回这条消息"
    envelope, headers = received[0]
    assert envelope_errors(envelope) == [], "读回的 envelope 必须通过 event-envelope 契约校验"
    assert envelope["event_id"] == event_id
    assert envelope["producer"] == "B2"
    assert envelope["event_type"] == SUBMISSION_ACCEPTED
    assert set(envelope["payload"]) == set(allowed_keys(SUBMISSION_ACCEPTED))
    assert envelope["payload"] == canonical_payload(SUBMISSION_ACCEPTED)
    assert headers["Nats-Msg-Id"] == event_id, "发布幂等键必须是 event_id"

    row = outbox_row(engine, event_id)
    assert row.published_at is not None
    assert row.attempts == 0
    assert row.last_error is None
    assert row.dead_lettered_at is None


@db
@broker
def test_dispatch_preserves_insertion_order(engine: Engine) -> None:
    """T-EVT-002：投递顺序 = **入队顺序**（`insert_seq`），与 `enqueued_at` 的精度无关。

    改写说明（2026-09-16，组级复核发现并复现的真缺陷）：本用例原断言"按 `enqueued_at` 排序"，并**逆序
    插入**三行来证明顺序不来自插入顺序。复核实测：编排层会在**同一事务**里连续入队 `started` 与第一条
    `progress`，两行的 `enqueued_at` 逐微秒相同，排序因此退化成 `event_id`（uuid5，键里含随机 run_id）
    的哈希——20000 样本中 50.2% 把 `progress` 排到 `started` 之前。契约（I31）要求的是"同一运行的
    `started → progress → completed|failed` 顺序"，**不是**"按入队时间戳排序"，因此不变量改为**插入
    顺序**（迁移 0009 的 `insert_seq`）；本用例按新语义重写，并保留"真实投递 + 真实读回"。
    """
    base = utc_now()
    # 按**逻辑顺序**入队，但 `enqueued_at` 刻意与插入顺序相反（最坏情况：时间戳完全帮不上忙）
    enqueue_event(engine, subject=EVALUATION_STARTED, enqueued_at=base + timedelta(seconds=2))
    enqueue_event(engine, subject=EVALUATION_PROGRESS, enqueued_at=base + timedelta(seconds=1))
    enqueue_event(engine, subject=EVALUATION_COMPLETED, enqueued_at=base)

    async def scenario() -> list[tuple[dict, dict]]:
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            report = await NatsOutboxDispatcher(engine=engine, bus=bus).dispatch(limit=10)
            assert (report.selected, report.published) == (3, 3), report
            # 用**一个**通配订阅读回：顺序即真实到达顺序（不是按 subject 分别读）。
            return await read_back(bus, stream, "autograder.v1.>", expect=3, timeout=3.0)

    received = asyncio.run(scenario())

    assert [item[0]["event_type"] for item in received] == [
        EVALUATION_STARTED,
        EVALUATION_PROGRESS,
        EVALUATION_COMPLETED,
    ], "投递顺序必须等于**入队顺序**（不重排、也不按 enqueued_at 重排）"


@db
def test_same_transaction_enqueues_keep_their_order_despite_identical_timestamps(
    engine: Engine,
) -> None:
    """回归（组级复核缺陷）：**同一事务**、同一时间戳入队的事件，顺序必须由**插入序号**决定。

    这是生产写入端的真实形态（`started` 与第一条 `progress` 同事务同一 `occurred_at`）。旧实现按
    `(enqueued_at, event_id)` 排序 ⇒ 顺序由 uuid5 的哈希决定，实测约一半运行倒序（复核者 20000 样本
    得 50.1%）。这里跑多轮，每轮都用**一个**事务连写两行（与生产一致，不是两次独立提交），
    逐轮断言顺序确定——单轮通过可能只是哈希恰好站在对的一边。
    """
    rounds = 20
    for index in range(rounds):
        moment = utc_now()
        with engine.begin() as connection:
            first = enqueue(
                connection,
                build_event(
                    subject=EVALUATION_STARTED,
                    payload=canonical_payload(EVALUATION_STARTED),
                    correlation_id=CORRELATION_ID,
                    occurred_at=moment,
                ),
                enqueued_at=moment,
            )
            second = enqueue(
                connection,
                build_event(
                    subject=EVALUATION_PROGRESS,
                    payload=canonical_payload(EVALUATION_PROGRESS),
                    correlation_id=CORRELATION_ID,
                    occurred_at=moment,
                ),
                enqueued_at=moment,
            )
        first_id, second_id = str(first.event_id), str(second.event_id)
        dispatcher = NatsOutboxDispatcher(engine=engine, bus=NatsEventBus(url="nats://127.0.0.1:1"))
        rows = list(dispatcher.pending_rows(limit=50))
        ordered = [str(row.event_id) for row in rows]
        assert ordered == [first_id, second_id], (
            f"第 {index + 1} 轮顺序错乱：{ordered}（同一事务同一时间戳 ⇒ 只能靠 insert_seq）"
        )
        assert rows[0].enqueued_at == rows[1].enqueued_at, (
            "本用例的前提是两行 enqueued_at **完全相同**（否则测不到哈希兜底那条路）"
        )
        sequences = [row.insert_seq for row in rows]
        assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences), (
            "insert_seq 必须随插入顺序严格递增且互不相同"
        )
        # 清理：把这两行标记为已发布，避免影响后续轮次与其它用例
        with engine.begin() as connection:
            connection.execute(
                sa.update(outbox_events)
                .where(outbox_events.c.event_id.in_([uuid.UUID(first_id), uuid.UUID(second_id)]))
                .values(published_at=moment)
            )


# --- T-EVT-003/004：payload 结构性禁止 ---------------------------------------------------


@pytest.mark.parametrize("subject", PUBLISHED_SUBJECTS)
def test_published_payloads_carry_no_sensitive_markers(subject: str) -> None:
    """T-EVT-003（5 类事件）：规范 payload 序列化后不含任何敏感标记；泄漏 payload 一律被拒。"""
    event = build_event(
        subject=subject, payload=canonical_payload(subject), correlation_id=CORRELATION_ID
    )
    blob = envelope_bytes(event).decode("utf-8")
    for marker in MARKERS:
        assert marker not in blob
    assert set(json.loads(blob)["payload"]) == set(allowed_keys(subject))

    leaky = {
        **canonical_payload(subject),
        "source_code": MARKER_SOURCE,
        "reference_answer": MARKER_ANSWER,
        "hidden_case_expected_output": MARKER_HIDDEN,
        "access_token": MARKER_CREDENTIAL,
        "storage_path": MARKER_PATH,
        "student_email": MARKER_PII,
        "host_port": "internal-db.internal:5432",
    }
    reasons = payload_rejections(subject, leaky)
    assert reasons, "含禁止键的 payload 必须被拒"
    joined = " ".join(reasons)
    for marker in MARKERS:
        assert marker not in joined, "拒绝原因不得回显取值"
    with pytest.raises(PayloadRejected):
        build_envelope(
            build_event(subject=subject, payload=leaky, correlation_id=CORRELATION_ID)
        )

    # 标识键被塞进外部文本（源码）→ 形态校验拒绝，且原因不回显取值。
    for key in allowed_keys(subject):
        poisoned = {**canonical_payload(subject), key: MARKER_SOURCE}
        poisoned_reasons = payload_rejections(subject, poisoned)
        assert poisoned_reasons, f"{subject}.{key} 不得接受自由文本（形态校验必须拒绝）"
        assert MARKER_SOURCE not in " ".join(poisoned_reasons)


@db
@db
@broker
def test_leaky_outbox_row_is_never_published(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """T-EVT-004：写入端 bug 把敏感内容写进 payload → 派发器拒绝发布、留行、记脱敏原因。

    日志侧另有要求：这类失败是**我们自己的 payload 不合契约**，不能报成
    `messaging.broker_unavailable`（会把内部缺陷伪装成 broker 故障、稀释真故障告警）。
    """
    caplog.set_level(logging.ERROR, logger="autograder_b2.events.outbox")
    leaky = {
        **canonical_payload(EVALUATION_PROGRESS),
        "source_code": MARKER_SOURCE,
        "access_token": MARKER_CREDENTIAL,
        "storage_path": MARKER_PATH,
    }
    event_id = insert_raw_row(engine, subject=EVALUATION_PROGRESS, payload=leaky)

    async def scenario() -> tuple[DispatchReport, list[tuple[dict, dict]]]:
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            report = await NatsOutboxDispatcher(engine=engine, bus=bus).dispatch(limit=10)
            received = await read_back(
                bus, stream, EVALUATION_PROGRESS, expect=1, timeout=1.5
            )
            return report, received

    report, received = asyncio.run(scenario())

    assert report.published == 0
    assert report.failed == 1
    assert report.dead_lettered == 0
    assert report.last_error is not None and "source_code" in report.last_error
    for marker in (MARKER_SOURCE, MARKER_CREDENTIAL, MARKER_PATH):
        assert marker not in report.last_error
    assert received == [], "违约 payload 绝不允许进入真实流"

    row = outbox_row(engine, event_id)
    assert row.published_at is None
    assert row.attempts == 1
    assert row.dead_lettered_at is None
    assert row.last_error is not None
    for marker in (MARKER_SOURCE, MARKER_CREDENTIAL, MARKER_PATH):
        assert marker not in row.last_error

    # 日志必须**按类型分派**：payload 违约 → `events.outbox.payload_rejected`，
    # 不能报成 broker 故障（否则内部缺陷会触发 MQ 告警）。
    events = [
        record.__dict__[B2_EVENT_KEY]
        for record in caplog.records
        if record.name == "autograder_b2.events.outbox"
    ]
    assert len(events) == 1, f"一次 payload 违约应恰好记一条日志，实际 {len(events)} 条"
    assert events[0]["event"] == "events.outbox.payload_rejected"
    assert events[0]["fields"]["error_type"] == "PayloadRejected"
    assert events[0]["fields"]["outcome"] == "failed"
    assert "dependency" not in events[0]["fields"], "payload 违约不是依赖故障，不得带 dependency 字段"
    rendered = json.dumps(events[0]["fields"], ensure_ascii=False, default=str)
    for marker in (MARKER_SOURCE, MARKER_CREDENTIAL, MARKER_PATH):
        assert marker not in rendered, f"拒绝日志不得回显 {marker[:20]}"


# --- T-EVT-005：inbox 去重 ---------------------------------------------------------------


@db
@broker
def test_duplicate_delivery_produces_one_business_effect(engine: Engine) -> None:
    """T-EVT-005：同一 `event_id` 重复投递 → 计数回调只被调用一次、inbox 只有一行。"""
    envelope = question_version_envelope()
    effects: list[str] = []

    def handler(connection, received) -> None:
        effects.append(str(received["event_id"]))

    consumer = InboxConsumer(engine, handler, consumer="b2-test-dedup")
    raw = json.dumps(envelope, sort_keys=True).encode("utf-8")

    async def scenario():
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            # 故意发布**两条内容相同、msg-id 不同**的消息（真实 at-least-once 重复投递）。
            await bus.publish(QUESTION_VERSION_PUBLISHED, raw)
            await bus.publish(QUESTION_VERSION_PUBLISHED, raw)
            report = await consumer.run(
                bus,
                durable="b2test-dedup",
                stream=stream,
                max_messages=2,
                fetch_timeout=1.0,
            )
            return report

    report = asyncio.run(scenario())

    assert report.received == 2, "两条重复消息必须都真实到达"
    assert report.handled == 1
    assert report.duplicates == 1
    assert report.failed == 0
    assert report.errors == ()
    assert effects == [envelope["event_id"]], "业务效果只能发生一次"
    assert inbox_count(engine, consumer="b2-test-dedup") == 1


# --- T-EVT-006：重试与 NATS 恢复 ---------------------------------------------------------


@db
@broker
def test_transient_failure_keeps_row_then_recovery_publishes(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """T-EVT-006：真实不可达 NATS → 留行、attempts 递增、published_at 空；恢复后成功投递。

    依赖不可用必须**既有状态又有日志**（RQ-B-2-99 场景 2-99-D："制造沙箱启动失败 / DB 不可用 /
    NATS 不可用 → 各有明确日志与状态，不吞异常"）：这里用 caplog 证明派发失败留下了一条统一依赖
    故障日志，且日志里不含凭据/内部主机。
    """
    caplog.set_level(logging.ERROR, logger="autograder_b2.events.outbox")
    event_id = enqueue_event(engine, subject=SUBMISSION_ACCEPTED)

    report = asyncio.run(
        failing_dispatcher(engine, UNREACHABLE_NATS_URL_WITH_CREDENTIALS).dispatch(limit=10)
    )
    assert report.selected == 1
    assert report.published == 0
    assert report.failed == 1
    assert report.dead_lettered == 0
    assert report.last_error is not None and report.last_error.strip()
    assert "secretpw" not in report.last_error, "凭据绝不进 last_error"
    assert "127.0.0.1" not in report.last_error, "内部主机/端口绝不进 last_error"

    row = outbox_row(engine, event_id)
    assert row.published_at is None, "投递失败必须保留行且保持未发布"
    assert row.attempts == 1
    assert row.dead_lettered_at is None
    assert row.last_error == report.last_error

    # 状态之外还必须有日志：统一的依赖故障事件 + 机器可读字段，且**不**抄 `str(error)`。
    logged = [
        record.__dict__[B2_EVENT_KEY]
        for record in caplog.records
        if record.name == "autograder_b2.events.outbox"
    ]
    assert len(logged) == 1, f"一次派发失败应恰好记一条依赖故障日志，实际 {len(logged)} 条"
    fields = logged[0]["fields"]
    assert logged[0]["event"] == "messaging.broker_unavailable"
    assert fields["dependency"] == "message_broker" and fields["status"] == "unavailable"
    assert fields["error_type"] == "EventBusUnavailable"
    assert fields["stage"] == "dispatch"
    assert str(fields["event_id"]) == event_id and fields["attempts"] == 1
    rendered = json.dumps(fields, ensure_ascii=False, default=str)
    for forbidden in ("secretpw", "127.0.0.1", "ghp_2c4d5e6f7a8b9c0d"):
        assert forbidden not in rendered, f"依赖故障日志不得出现 {forbidden}"

    async def recovery() -> tuple[DispatchReport, list[tuple[dict, dict]]]:
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            healthy = await NatsOutboxDispatcher(engine=engine, bus=bus).dispatch(limit=10)
            received = await read_back(bus, stream, SUBMISSION_ACCEPTED, expect=1)
            return healthy, received

    healthy, received = asyncio.run(recovery())
    assert healthy == DispatchReport(
        selected=1, published=1, failed=0, dead_lettered=0, last_error=None
    )
    assert [item[0]["event_id"] for item in received] == [event_id]

    recovered = outbox_row(engine, event_id)
    assert recovered.published_at is not None
    assert recovered.attempts == 1, "成功投递不递增 attempts（只记失败次数）"
    assert recovered.dead_lettered_at is None
    # `last_error` 只描述"这行为什么还没送达"：送达后必须清空，否则运维按行阅读会把一条
    # 已投递成功的事件读成"仍在失败"（失败次数仍可从 attempts 看出）。
    assert recovered.last_error is None, "投递成功后不得残留上一次失败的文本"


@db
@broker
def test_delivery_limit_moves_row_to_queryable_dlq_and_replay_publishes(engine: Engine) -> None:
    """T-EVT-007：超过投递上限 → 进 DLQ（可查询、不再自动重投）；人工重放后真实投递成功。"""
    event_id = enqueue_event(engine, subject=EVALUATION_COMPLETED)
    dispatcher = failing_dispatcher(engine, UNREACHABLE_NATS_URL, max_attempts=2)

    first = asyncio.run(dispatcher.dispatch(limit=10))
    assert (first.failed, first.dead_lettered) == (1, 0)
    assert outbox_row(engine, event_id).dead_lettered_at is None

    second = asyncio.run(dispatcher.dispatch(limit=10))
    assert (second.selected, second.failed, second.dead_lettered) == (1, 1, 1)

    row = outbox_row(engine, event_id)
    assert row.attempts == 2
    assert row.dead_lettered_at is not None, "达到上限必须进 DLQ（不静默）"
    assert row.published_at is None
    assert row.last_error is not None and row.last_error.strip()

    dead_letters = dispatcher.dead_letters()
    assert [item.event_id for item in dead_letters] == [event_id], "DLQ 必须可查询"
    assert dead_letters[0].subject == EVALUATION_COMPLETED
    assert dead_letters[0].attempts == 2
    assert dead_letters[0].last_error and "secret" not in dead_letters[0].last_error

    third = asyncio.run(dispatcher.dispatch(limit=10))
    assert third.selected == 0, "DLQ 行不再被自动重投"

    assert dispatcher.replay(event_id) is True, "DLQ 行必须可人工重放"
    assert dispatcher.replay(event_id) is False, "不在 DLQ 的行不假装重放成功"
    replayed = outbox_row(engine, event_id)
    assert replayed.dead_lettered_at is None
    assert replayed.attempts == 0
    assert [item.event_id for item in dispatcher.dead_letters()] == []

    async def recovery() -> tuple[DispatchReport, list[tuple[dict, dict]]]:
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            report = await NatsOutboxDispatcher(engine=engine, bus=bus).dispatch(limit=10)
            received = await read_back(bus, stream, EVALUATION_COMPLETED, expect=1)
            return report, received

    report, received = asyncio.run(recovery())
    assert (report.published, report.failed, report.dead_lettered) == (1, 0, 0)
    assert [item[0]["event_id"] for item in received] == [event_id]
    final = outbox_row(engine, event_id)
    assert final.published_at is not None
    assert final.dead_lettered_at is None


# --- T-EVT-008/009/010：订阅端异常不静默 --------------------------------------------------


@db
@broker
def test_handler_failure_is_recorded_and_rolled_back(engine: Engine, effect_probe: str) -> None:
    """T-EVT-008：`run` 里 handler 抛错 → 记进 `ConsumerReport.errors`、回调 `on_error`、
    业务效果与去重记录一起回滚；同步边界 `consume` 直接抛出（不吞）。"""
    envelope = question_version_envelope()
    raw = json.dumps(envelope, sort_keys=True).encode("utf-8")
    hooks: list[tuple[str, BaseException]] = []
    calls: list[str] = []

    def failing_handler(connection, received) -> None:
        calls.append("failing")
        connection.execute(
            text(f"INSERT INTO {effect_probe} (event_id, note) VALUES (:event_id, 'effect')"),
            {"event_id": uuid.UUID(str(received["event_id"]))},
        )
        raise RuntimeError(f"cache invalidation exploded with {MARKER_CREDENTIAL}")

    consumer = InboxConsumer(
        engine,
        failing_handler,
        consumer="b2-test-failure",
        on_error=lambda reason, error: hooks.append((reason, error)),
    )

    # 同步边界的"不吞"：consume 直接抛出（不是返回一个失败结论），且事务整体回滚。
    with pytest.raises(RuntimeError):
        consumer.consume(envelope)
    assert calls == ["failing"]
    assert probe_count(engine, envelope["event_id"]) == 0, "业务效果必须随事务回滚"
    assert inbox_count(engine, consumer="b2-test-failure") == 0, "去重记录必须随事务回滚"
    calls.clear()  # 下面的 run 路径单独计数（同步路径已经计过一次）

    async def scenario():
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            await bus.publish(QUESTION_VERSION_PUBLISHED, raw)
            report = await consumer.run(
                bus, durable="b2test-failure", stream=stream, max_messages=1, fetch_timeout=1.0
            )
            # 失败之后、任何重试之前的状态：业务效果与去重记录都必须不落库。
            return report, probe_count(engine, envelope["event_id"]), inbox_count(
                engine, consumer="b2-test-failure"
            )

    report, probe_after_failure, inbox_after_failure = asyncio.run(scenario())

    assert report.received == 1
    assert report.failed == 1
    assert report.handled == 0
    assert report.errors and "RuntimeError" in report.errors[0], "失败必须被如实记录"
    assert MARKER_CREDENTIAL not in report.errors[0], "失败原因必须脱敏"
    assert len(hooks) == 1, "失败必须回调 on_error（可接日志/告警）"
    assert hooks[0][0] == report.errors[0]
    assert calls == ["failing"]
    assert probe_after_failure == 0, "业务效果必须随事务回滚"
    assert inbox_after_failure == 0, "去重记录必须随事务回滚"


@db
@broker
def test_consumer_failure_is_logged_even_without_an_error_hook(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """T-EVT-020：没传 `on_error` 钩子时，消费失败也必须留一条结构化日志（不静默）。

    契约 §5 要求"处理失败可重试；超过上限进 DLQ 并记录"，而本模块 docstring 承诺"异常不静默"。
    只有钩子时才留痕等于把"有没有日志"交给调用方——拉取循环无人读 `ConsumerReport` 时，失败就
    彻底消失。这里断言兜底日志存在、原因已脱敏、且不含 envelope 内容。
    """
    caplog.set_level(logging.ERROR, logger="autograder_b2.events.inbox")
    envelope = question_version_envelope()

    def failing_handler(connection, received) -> None:
        raise RuntimeError(f"cache invalidation exploded with {MARKER_CREDENTIAL}")

    consumer = InboxConsumer(engine, failing_handler, consumer="b2-test-silent")

    async def scenario():
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            await bus.publish(QUESTION_VERSION_PUBLISHED, json.dumps(envelope).encode("utf-8"))
            return await consumer.run(
                bus, durable="b2test-silent", stream=stream, max_messages=1, fetch_timeout=1.0
            )

    report = asyncio.run(scenario())

    assert report.failed == 1 and report.errors, "失败仍必须如实计入报告"
    logged = [
        record.__dict__[B2_EVENT_KEY]
        for record in caplog.records
        if record.name == "autograder_b2.events.inbox"
    ]
    assert len(logged) == 1, f"无钩子时失败必须留恰好一条日志，实际 {len(logged)} 条"
    entry = logged[0]
    assert entry["event"] == "events.inbox.handler_failed"
    assert entry["fields"]["outcome"] == "failed"
    assert "RuntimeError" in entry["fields"]["reason"], "日志必须点明失败类型"
    rendered = json.dumps(entry["fields"], ensure_ascii=False, default=str)
    assert MARKER_CREDENTIAL not in rendered, "兜底日志同样必须脱敏"
    assert MARKER_SOURCE not in rendered and MARKER_ANSWER not in rendered
    assert "payload" not in entry["fields"], "绝不整包记录 envelope"


@db
@broker
def test_nak_redelivers_message_and_retry_then_succeeds(
    engine: Engine, effect_probe: str
) -> None:
    """T-EVT-009：handler 失败后消息**未被 ack**（同 durable 重投、`num_delivered` 递增），
    换成功 handler 重投后 inbox 与业务效果都落库；同一 `event_id` 再投递为 no-op。"""
    envelope = question_version_envelope()
    raw = json.dumps(envelope, sort_keys=True).encode("utf-8")
    attempts: list[str] = []
    effects: list[str] = []

    def flaky_handler(connection, received) -> None:
        attempts.append("failed")
        raise RuntimeError("transient failure while invalidating cache")

    def healthy_handler(connection, received) -> None:
        effects.append(str(received["event_id"]))
        connection.execute(
            text(f"INSERT INTO {effect_probe} (event_id, note) VALUES (:event_id, 'effect')"),
            {"event_id": uuid.UUID(str(received["event_id"]))},
        )

    failing = InboxConsumer(engine, flaky_handler, consumer="b2-test-redelivery")
    healthy = InboxConsumer(engine, healthy_handler, consumer="b2-test-redelivery")

    async def scenario():
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            await bus.publish(QUESTION_VERSION_PUBLISHED, raw)
            subscription = await bus.pull_subscribe(
                QUESTION_VERSION_PUBLISHED, durable="b2test-redelivery", stream=stream
            )
            first = await subscription.fetch(1, timeout=3.0)
            with pytest.raises(RuntimeError):
                await failing.process(first[0])
            # 失败之后、重投之前：业务效果与去重记录都必须不落库（同事务回滚）。
            probe_after_failure = probe_count(engine, envelope["event_id"])
            inbox_after_failure = inbox_count(engine, consumer="b2-test-redelivery")
            redelivered = await subscription.fetch(1, timeout=3.0)
            outcome = await healthy.process(redelivered[0])
            # 同一 event_id 再投一次：inbox 去重命中 → no-op（不产生第二次业务效果）。
            await bus.publish(QUESTION_VERSION_PUBLISHED, raw)
            duplicate = await subscription.fetch(1, timeout=3.0)
            duplicate_outcome = await healthy.process(duplicate[0])
            return (
                first[0],
                redelivered[0],
                outcome,
                duplicate_outcome,
                probe_after_failure,
                inbox_after_failure,
            )

    (
        first,
        redelivered,
        outcome,
        duplicate_outcome,
        probe_after_failure,
        inbox_after_failure,
    ) = asyncio.run(scenario())

    assert attempts == ["failed"], "第一次投递必须真的失败"
    assert probe_after_failure == 0, "失败后业务效果必须回滚"
    assert inbox_after_failure == 0, "失败后去重记录必须回滚"

    assert json.loads(redelivered.data.decode("utf-8"))["event_id"] == envelope["event_id"]
    assert redelivered.metadata.num_delivered >= 2, "未 ack 的消息必须被重新投递（nak 生效）"
    assert first.metadata.num_delivered == 1

    assert outcome.handled is True and outcome.duplicate is False
    assert effects == [envelope["event_id"]]
    assert inbox_count(engine, consumer="b2-test-redelivery") == 1
    assert probe_count(engine, envelope["event_id"]) == 1

    assert duplicate_outcome.duplicate is True and duplicate_outcome.handled is False
    assert effects == [envelope["event_id"]], "重复投递不得产生第二次业务效果"
    assert probe_count(engine, envelope["event_id"]) == 1


@db
def test_consumer_rejects_non_contract_envelope(engine: Engine) -> None:
    """T-EVT-010：不通过契约 schema 的消息被拒（不写 inbox、不产生业务效果）。"""
    broken = question_version_envelope()
    del broken["producer"]  # 契约 required 字段缺失
    assert envelope_errors(broken) != []

    def handler(connection, received) -> None:  # pragma: no cover - 不应被调用
        raise AssertionError("违约 envelope 不得进入业务处理")

    consumer = InboxConsumer(engine, handler, consumer="b2-test-invalid")
    with pytest.raises(EventEnvelopeInvalid):
        consumer.consume(broken)
    assert inbox_count(engine, consumer="b2-test-invalid") == 0


@db
@broker
def test_consumer_tolerates_unknown_fields_and_ignores_unknown_event_type(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """T-EVT-017（`MAIN/docs/api/events.md` §5）：未知字段容忍；未识别 `event_type` 记录并忽略。

    "记录"按契约原文（§5："不静默丢弃无日志"）必须是**日志**，不能只在返回值里体现：
    这里用 caplog 证明恰好留下一条结构化日志，且内容就是那个未识别的类型。
    """
    forward = {**question_version_envelope(), "future_field": "added-by-a-newer-producer"}
    assert envelope_errors(forward) != [], "发布侧严格：契约 additionalProperties=false"
    foreign = {
        **question_version_envelope(),
        "event_type": EVALUATION_COMPLETED,
        "payload": canonical_payload(EVALUATION_COMPLETED),
    }
    effects: list[str] = []
    consumer = InboxConsumer(
        engine,
        lambda connection, received: effects.append(str(received["event_type"])),
        consumer="b2-test-forward",
    )
    caplog.set_level(logging.WARNING, logger="autograder_b2.events.inbox")

    async def scenario():
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            # 两条都发到本消费者订阅的 subject 上（第二条是"生产者发错事件类型"的真实场景）。
            await bus.publish(QUESTION_VERSION_PUBLISHED, json.dumps(forward).encode("utf-8"))
            await bus.publish(QUESTION_VERSION_PUBLISHED, json.dumps(foreign).encode("utf-8"))
            return await consumer.run(
                bus, durable="b2test-forward", stream=stream, max_messages=2, fetch_timeout=1.0
            )

    report = asyncio.run(scenario())

    assert report.received == 2
    assert report.handled == 1, "未知字段不得导致消息被丢弃"
    assert report.ignored == 1 and report.ignored_event_types == (EVALUATION_COMPLETED,)
    assert report.failed == 0
    assert effects == [QUESTION_VERSION_PUBLISHED], "未识别 event_type 不得触发业务效果"
    assert inbox_count(engine, consumer="b2-test-forward") == 1, "被忽略的事件不写 inbox"

    # "记录并忽略"里的"记录"：恰好一条结构化日志，且带上那个未识别的类型（未识别类型是外部
    # 输入，只能进 `detail` 这个受长度上限 + 模式替换约束的自由文本槽，绝不整包记录 envelope）。
    logged = [
        record.__dict__[B2_EVENT_KEY]
        for record in caplog.records
        if record.name == "autograder_b2.events.inbox"
    ]
    assert len(logged) == 1, f"未识别 event_type 必须恰好记一条日志，实际 {len(logged)} 条"
    entry = logged[0]
    assert entry["event"] == "events.inbox.unrecognized_event_type"
    assert entry["fields"]["detail"] == EVALUATION_COMPLETED, "日志必须能指认被忽略的类型"
    assert entry["fields"]["outcome"] == "ignored"
    assert entry["fields"]["event_id"] == foreign["event_id"]


def test_sanitize_error_removes_credentials_and_internal_paths() -> None:
    """T-EVT-011：脱敏的真实输入/输出（与故障注入用例互补）。"""
    raw = (
        "publish failed: nats://autograder:secretpw@10.2.3.4:4222 "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij "
        "at D:\\VscodeProjects\\AutoGrader\\B2\\src\\secret.py cookie=session-abc "
        "token=ghp_2c4d5e6f7a8b9c0d\nsecond line"
    )
    cleaned = sanitize_error(raw)
    assert "secretpw" not in cleaned
    assert "ghp_2c4d5e6f7a8b9c0d" not in cleaned
    assert "session-abc" not in cleaned
    assert "10.2.3.4" not in cleaned
    assert "D:\\VscodeProjects" not in cleaned
    assert "\n" not in cleaned, "外部输入不得决定行数"
    assert "<redacted-credential>" in cleaned
    assert "<redacted-path>" in cleaned
    assert len(sanitize_error("x" * 4000)) <= 500, "必须落在 outbox_events.last_error 的 CHECK 上限内"


@db
@broker
def test_enqueue_deduplication_prevents_a_second_publish(engine: Engine) -> None:
    """T-EVT-016：发布幂等——同 `(subject, deduplication_key)` 只入队一次，已发布行不再重投。"""
    moment = utc_now()
    key = f"submission:{SUBMISSION_ID}"
    first_event = build_event(
        subject=SUBMISSION_ACCEPTED,
        payload=canonical_payload(SUBMISSION_ACCEPTED),
        correlation_id=CORRELATION_ID,
        occurred_at=moment,
    )
    second_event = build_event(
        subject=SUBMISSION_ACCEPTED,
        payload=canonical_payload(SUBMISSION_ACCEPTED),
        correlation_id=CORRELATION_ID,
        occurred_at=moment,
    )
    with engine.begin() as connection:
        first = enqueue(connection, first_event, deduplication_key=key, enqueued_at=moment)
        second = enqueue(connection, second_event, deduplication_key=key, enqueued_at=moment)
    assert first.deduplicated is False
    assert second.deduplicated is True, "同一去重键不得重复入队"
    assert second.event_id == first.event_id

    async def scenario():
        async with connected_bus() as bus, ephemeral_stream(bus) as stream:
            dispatcher = NatsOutboxDispatcher(engine=engine, bus=bus)
            once = await dispatcher.dispatch(limit=10)
            twice = await dispatcher.dispatch(limit=10)
            # 期待 2 条却在超时后只拿到 1 条 —— 即"没有第二条消息"。
            received = await read_back(bus, stream, SUBMISSION_ACCEPTED, expect=2, timeout=2.0)
            return once, twice, received

    once, twice, received = asyncio.run(scenario())

    assert (once.selected, once.published) == (1, 1)
    assert (twice.selected, twice.published) == (0, 0), "已发布的行不再被选中"
    assert len(received) == 1, "真实流里只应有一条消息（无重复发布）"
    assert received[0][0]["event_id"] == first.event_id


@db
def test_broker_outage_keeps_the_committed_business_transaction(engine: Engine) -> None:
    """T-EVT-015（I29）：业务变更与 outbox 同事务提交；NATS 不可用只影响投递，不回滚业务事实。

    业务行取 `b2.artifacts`（提交内容的寻址落库，与事件同属接收路径、同事务）：它**不依赖**
    `b2.submissions` 的列形状。那张表正由并行工作项扩列（`correlation_id` / provenance，迁移 0008）；
    事件侧测试不该押在另一个工作项的迁移节奏上——若写 `b2.submissions`，新列只能经
    `db/schema.py` 的表映像表达，而该映像（不由本工作项维护）此刻还没有那六列。
    """
    artifact_id = uuid.uuid4()
    moment = utc_now()
    payload = submission_accepted_payload(
        submission_id=SUBMISSION_ID,
        student_id=STUDENT_ID,
        course_id=COURSE_ID,
        section_id=SECTION_ID,
        question_version_id=QUESTION_VERSION_ID,
        intake="api",
    )
    event = build_event(
        subject=SUBMISSION_ACCEPTED,
        payload=payload,
        correlation_id=CORRELATION_ID,
        occurred_at=moment,
    )
    # 一个事务里同时写业务行与 outbox 行（写入端的真实用法）。
    with engine.begin() as connection:
        connection.execute(
            pg_insert(artifacts).values(
                artifact_id=artifact_id,
                content_sha256=DIGEST,
                size_bytes=1234,
                storage_path=f"sha256/{DIGEST[:2]}/{DIGEST}",
                created_at=moment,
            )
        )
        enqueue(connection, event, deduplication_key=f"submission:{SUBMISSION_ID}", enqueued_at=moment)

    report = asyncio.run(failing_dispatcher(engine, UNREACHABLE_NATS_URL).dispatch(limit=10))

    assert report.published == 0 and report.failed == 1
    with engine.connect() as connection:
        size_bytes = connection.execute(
            sa.select(artifacts.c.size_bytes).where(artifacts.c.artifact_id == artifact_id)
        ).scalar_one()
    assert size_bytes == 1234, "broker 不可用不得回滚已提交的业务事务"
    assert outbox_row(engine, str(event.event_id)).published_at is None


def test_bus_records_connect_errors_and_redacts_its_url() -> None:
    """T-EVT-012：真实连接失败 → 异常文本不含凭据、错误被记录（不丢弃）、状态如实为未连接。"""
    bus = NatsEventBus(
        url=UNREACHABLE_NATS_URL_WITH_CREDENTIALS,
        connect_timeout=0.5,
        reconnect_time_wait=0.1,
        max_reconnect_attempts=1,
    )
    with pytest.raises(EventBusUnavailable) as caught:
        asyncio.run(bus.connect())

    text = str(caught.value)
    assert "secretpw" not in text, "异常文本绝不带凭据"
    assert "<redacted-credential>" in text
    assert "127.0.0.1" in text, "异常里保留主机（落库前才替换为脱敏占位符）"
    assert bus.error_count >= 1, "连接期错误必须被记录而不是丢弃"
    assert bus.last_error is not None
    assert bus.is_connected is False


@db
@broker
def test_dispatch_sync_entry_point_publishes(engine: Engine) -> None:
    """T-EVT-013：无事件循环入口 `dispatch_sync` 与 `dispatch` 语义一致（真实投递 + 真实读回）。"""
    event_id = enqueue_event(engine, subject=SUBMISSION_ACCEPTED)
    stream_name = f"B2_EVENTS_{uuid.uuid4().hex[:10].upper()}"

    async def create_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().add_stream(name=stream_name, subjects=list(STREAM_SUBJECTS))

    async def read_stream() -> list[tuple[dict, dict]]:
        async with connected_bus() as bus:
            return await read_back(bus, stream_name, SUBMISSION_ACCEPTED, expect=1)

    async def drop_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().delete_stream(stream_name)

    asyncio.run(create_stream())
    try:
        # 同步入口内部自建事件循环并惰性连接（生产里给无 loop 的调用方用）。
        report = NatsOutboxDispatcher(engine=engine, bus=NatsEventBus(url=NATS_URL)).dispatch_sync(
            limit=10
        )
        received = asyncio.run(read_stream())
    finally:
        asyncio.run(drop_stream())

    assert (report.selected, report.published, report.failed) == (1, 1, 0)
    assert [item[0]["event_id"] for item in received] == [event_id]
    assert outbox_row(engine, event_id).published_at is not None


@db
@broker
def test_dispatch_sync_can_be_called_repeatedly_on_the_same_dispatcher(engine: Engine) -> None:
    """T-EVT-013（回归）：`dispatch_sync` **跨轮可重复调用**——它必须在返回前关掉自建循环的连接。

    连接（含事件回调与 PubAck 等待）绑在创建它的事件循环上，而本入口的循环随 `asyncio.run` 结束。
    若把连接留到下一轮，第 2 轮起就永远发不出去（"第 1 轮成功、之后每轮都失败"）：worker 每 2 秒调一次
    本入口，那种实现会把整个 outbox 耗进 DLQ。因此这里**真的连调两轮**：每轮一行、各自真被投递。
    """
    first = enqueue_event(engine, subject=SUBMISSION_ACCEPTED, deduplication_key="round-1")
    second = enqueue_event(engine, subject=SUBMISSION_ACCEPTED, deduplication_key="round-2")
    stream_name = f"B2_EVENTS_{uuid.uuid4().hex[:10].upper()}"
    dispatcher = NatsOutboxDispatcher(engine=engine, bus=NatsEventBus(url=NATS_URL))

    async def setup() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().add_stream(name=stream_name, subjects=list(STREAM_SUBJECTS))

    async def read_stream() -> list[tuple[dict, dict]]:
        async with connected_bus() as bus:
            return await read_back(bus, stream_name, SUBMISSION_ACCEPTED, expect=2)

    async def drop_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().delete_stream(stream_name)

    asyncio.run(setup())
    try:
        rounds = [dispatcher.dispatch_sync(limit=1), dispatcher.dispatch_sync(limit=1)]
        received = asyncio.run(read_stream())
    finally:
        asyncio.run(drop_stream())

    assert [(item.selected, item.published, item.failed) for item in rounds] == [(1, 1, 0), (1, 1, 0)]
    assert [item[0]["event_id"] for item in received] == [first, second], "顺序 = insert_seq"
    assert outbox_row(engine, first).published_at is not None
    assert outbox_row(engine, second).published_at is not None
    assert dispatcher.bus.is_connected is False, "自建循环结束后连接不得留到下一轮"


@db
def test_failing_bus_error_text_is_sanitized_before_persisting(engine: Engine) -> None:
    """T-EVT-014（故障注入）：异常文本带凭据+内部绝对路径 → 落库前必须被脱敏。"""

    class _FailingBus:
        """只用于脱敏路径的故障注入：发布必失败，异常文本故意带敏感内容。"""

        async def publish(self, subject: str, payload: bytes, *, msg_id: str | None = None):
            raise RuntimeError(
                "transport exploded for "
                f"{subject}: nats://autograder:secretpw@10.2.3.4:4222 "
                "root cause at D:\\VscodeProjects\\AutoGrader\\B2\\src\\secret.py "
                f"token=ghp_2c4d5e6f7a8b9c0d ({MARKER_SOURCE})"
            )

    event_id = enqueue_event(engine, subject=EVALUATION_FAILED)
    dispatcher = NatsOutboxDispatcher(engine=engine, bus=_FailingBus(), max_attempts=3)

    report = asyncio.run(dispatcher.dispatch(limit=10))
    row = outbox_row(engine, event_id)

    assert report.failed == 1 and report.published == 0
    for forbidden in (
        "secretpw",
        "ghp_2c4d5e6f7a8b9c0d",
        "D:\\VscodeProjects",
        "10.2.3.4",
    ):
        assert forbidden not in (report.last_error or "")
        assert forbidden not in (row.last_error or "")
    assert "<redacted-credential>" in row.last_error
    assert "<redacted-path>" in row.last_error
    assert row.published_at is None
    assert row.attempts == 1


def test_progress_payload_publishes_with_and_without_case_counts() -> None:
    """T-EVT-021：`evaluation.progress` 的**两种真实形状**都必须能发布（必填键不得与写入端冲突）。

    写入端在"还不知道用例总数"的阶段会有意**省略** `cases_total`/`cases_done`
    （`evaluation/orchestrator.py` 的 `_enter_stage(..., cases_total=None)`）。若把这两个计数也列为
    必填，该形状会过不了门 ⇒ 事件永不投递、重试到上限进 DLQ（复核者用真实生产方法复现过这条链）。
    因此：带计数（5 键）与不带计数（3 键）都必须过门；而三个**恒在**的键缺任何一个都必须被拒。
    """
    base = {
        "evaluation_run_id": str(uuid.uuid4()),
        "stage": "static_analysis",
        "sequence": 1,
    }
    with_counts = {**base, "cases_total": 5, "cases_done": 0}
    assert payload_rejections(EVALUATION_PROGRESS, with_counts) == []
    assert payload_rejections(EVALUATION_PROGRESS, base) == [], "不带计数的阶段不得被拒（否则永不投递）"

    for required in ("evaluation_run_id", "stage", "sequence"):
        partial = {k: v for k, v in with_counts.items() if k != required}
        reasons = payload_rejections(EVALUATION_PROGRESS, partial)
        assert any(required in reason for reason in reasons), (
            f"缺恒在键 {required} 必须被拒且点名，实得 {reasons}"
        )
    assert set(required_keys(EVALUATION_PROGRESS)) == {
        "evaluation_run_id",
        "stage",
        "sequence",
    }, "progress 的必填集只能是三个恒在键（计数按语义可选）"
    # 其余 subject 的必填集仍是准许键全集（含计数的那两个键仍在准许集合里，只是不必填）。
    assert set(allowed_keys(EVALUATION_PROGRESS)) == set(with_counts)

    # 不变量护栏留在**交付套件**里（不能只活在复核探针里——那个文件复核结束会删）：
    # 1) 每个已发布 subject 都必须登记必填集，否则门会静默退化（fail-open）；
    # 2) 必填集必须是准许集的子集；3) 除 progress 外两者相等（"必填太松"与"必填太严"都挡住）。
    for subject in PUBLISHED_SUBJECTS:
        allowed = set(allowed_keys(subject))
        required = set(required_keys(subject))
        assert required, f"{subject} 的必填集不得为空"
        assert required <= allowed, f"{subject} 必填集多出准许集之外的键：{required - allowed}"
        if subject != EVALUATION_PROGRESS:
            assert required == allowed, f"{subject} 必填集应等于准许集（缺 {allowed - required}）"
    assert set(REQUIRED_KEYS) == set(PAYLOAD_KEYS) == set(PUBLISHED_SUBJECTS), (
        "REQUIRED_KEYS / PAYLOAD_KEYS / 已发布 subject 三者必须一一对应（漏登记会 fail-open）"
    )

    # 漏登记路径必须 **fail-closed**：假装一个"准许键已登记、必填集没登记"的 subject。
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delitem(REQUIRED_KEYS, EVALUATION_STARTED)
        reasons = payload_rejections(
            EVALUATION_STARTED, dict(canonical_payload(EVALUATION_STARTED))
        )
        assert reasons and any("required-key rule" in reason for reason in reasons), (
            f"漏登记必填集必须被拒而不是放行，实得 {reasons}"
        )
    finally:
        monkeypatch.undo()
    assert payload_rejections(EVALUATION_STARTED, canonical_payload(EVALUATION_STARTED)) == []


def test_evaluation_failed_accepts_every_reachable_run_terminal_reason() -> None:
    """T-EVT-018：`evaluation.failed` 的 `failure_category` 必须覆盖**所有可达的运行终态原因**。

    生产的唯一写入端（`evaluation.orchestrator._finish_failure`）把运行终态原因同时作为
    `failure_category` 与 `terminal_reason` 上报。此前 `failure_category` 用的是**用例级**枚举
    （`case_evidence.failure_category` 的 CHECK），缺 `internal_error`，于是"库结构与契约冲突 /
    结果写库失败"这类技术失败的 `evaluation.failed` **永远发不出去**（重试到上限后进 DLQ）——
    而这恰是最需要告警的一类失败。`events.md` §3.5 要求该事件"必须携带失败类别"，并把"内部错误"
    与"沙箱不可用、依赖故障"并列为触发原因，故按事件语义合并取值域。
    """
    for reason in ("infrastructure_error", "internal_error"):
        payload = {
            "submission_id": str(uuid.uuid4()),
            "evaluation_run_id": str(uuid.uuid4()),
            "question_version_id": str(uuid.uuid4()),
            "stage": "terminal",
            "failure_category": reason,
            "terminal_reason": reason,
        }
        assert payload_rejections("autograder.v1.evaluation.failed", payload) == [], reason

    # 取值域仍然**是**闭合枚举：不认识的类别照旧被拒（不是把门放宽成"任意字符串"）。
    bogus = {
        "submission_id": str(uuid.uuid4()),
        "evaluation_run_id": str(uuid.uuid4()),
        "question_version_id": str(uuid.uuid4()),
        "stage": "terminal",
        "failure_category": "not_a_real_category",
        "terminal_reason": "internal_error",
    }
    assert payload_rejections("autograder.v1.evaluation.failed", bogus) != []
