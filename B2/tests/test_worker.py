"""评测 worker（`autograder_b2.worker`）：本模块**唯一**的评测与投递驱动入口。

为什么这个文件必须存在：worker 是 outbox 的**唯一**驱动者（`B2Runtime` 只装配派发器、不埋定时器，
HTTP 宿主从不调用 worker），而它此前把派发器的 `async dispatch` 当同步方法调用——协程被创建后丢弃
（`RuntimeWarning: coroutine 'NatsOutboxDispatcher.dispatch' was never awaited`），于是
`b2.outbox_events` **只进不出**：平台上实测 15 行、`published_at` 非空 0 行，B3 的 `event_inbox`
与 `question_version_stats` 因此恒为 0。所以本文件的重点是**"事件真的出得去"**，并且用真实
PostgreSQL + 真实 NATS JetStream 证明，不用替身冒充投递。

真实依赖与 `tests/test_events_outbox.py` 是同一套（直接复用它的真实 broker 工具）：数据库是
`conftest.py` 的临时库；broker 是真实 JetStream，用例自建唯一命名的流并在结束时删除；broker 不可达时
**显式 skip 并说明原因**，绝不伪造通过。
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from autograder_b2.api.errors import install_problem_handlers
from autograder_b2.db.schema import evaluation_runs, outbox_events, submissions
from autograder_b2.domain.envelope import SubmissionEnvelope
from autograder_b2.events.dispatcher import NatsOutboxDispatcher
from autograder_b2.events.nats import NatsEventBus
from autograder_b2.events.outbox import SUBMISSION_ACCEPTED
from autograder_b2.intake.api import build_router
from autograder_b2.intake.runtime import IntakeRuntime, build_runtime
from autograder_b2.worker import awaiting_evaluation, drain_outbox, run_once
from tests.conftest import requires_postgres
from tests.test_events_outbox import (
    NATS_URL,
    UNREACHABLE_NATS_URL,
    connected_bus,
    enqueue_event,
    outbox_row,
    read_back,
)
from tests.test_intake_api import (
    AUDIENCE,
    ENDPOINT,
    ISSUER,
    _auth,
    _envelope,
    _Signer,
    _student_token,
)

db = requires_postgres


def _broker_reachable() -> bool:
    """真实 TCP 探测（沿用 `test_events_outbox` 的口径：探测不依赖被探测方的 SDK）。"""
    parts = urlsplit(NATS_URL)
    try:
        with socket.create_connection((parts.hostname or "localhost", parts.port or 4222), 1.0):
            return True
    except OSError:
        return False


broker = pytest.mark.skipif(
    not _broker_reachable(),
    reason=f"需要真实 NATS JetStream：{NATS_URL} 不可达（不伪造 broker）",
)


class _StubEvaluator:
    """只记录"被要求评测哪份提交"的替身：本文件验证的是 worker 的**调度与投递**。

    它不评测、不创建运行行，因此不构成"评测链已端到端可用"的证据（那由编排层与 runner 的用例负责）；
    它存在的价值是证明 worker 真能把接收层写入的行重建成信封后交给评测链。
    """

    def __init__(self) -> None:
        self.envelopes: list[SubmissionEnvelope] = []

    def run(self, envelope: SubmissionEnvelope) -> object:
        self.envelopes.append(envelope)
        return type("Outcome", (), {"evaluation_run_id": "", "state": "stub"})()


class _WorkerRuntime:
    """`run_once` 只读三个属性（`evaluator`/`engine`/`outbox`），这里给最小的真实组合。"""

    def __init__(self, *, engine: Engine, evaluator: object, outbox: object) -> None:
        self.engine = engine
        self.evaluator = evaluator
        self.outbox = outbox


def _dispatcher(engine: Engine, *, url: str = NATS_URL) -> NatsOutboxDispatcher:
    return NatsOutboxDispatcher(engine=engine, bus=NatsEventBus(url=url))


def _submission_row(engine: Engine, submission_id: str) -> object:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(submissions).where(submissions.c.submission_id == uuid.UUID(submission_id))
        ).one()


def _published_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.count())
                .select_from(outbox_events)
                .where(outbox_events.c.published_at.is_not(None))
            ).scalar_one()
        )


def _seed_submission(engine: Engine, *, state: str, with_run: bool) -> str:
    """种一行真实形态的提交（列按库里实际存在的列填，不绕过任何约束）。"""
    identifier = uuid.uuid4()
    question = uuid.uuid4()
    moment = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            submissions.insert().values(
                submission_id=identifier,
                student_id=uuid.uuid4(),
                course_id=uuid.uuid4(),
                section_id=uuid.uuid4(),
                question_version_id=question,
                intake="api",
                idempotency_key=f"key-{identifier}",
                idempotency_scope="b2-worker-test",
                submission_digest="a" * 64,
                state=state,
                correlation_id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                mailbox=None,
                uidvalidity=None,
                uid=None,
                message_id=None,
                created_at=moment,
                updated_at=moment,
            )
        )
        if with_run:
            connection.execute(
                evaluation_runs.insert().values(
                    evaluation_run_id=uuid.uuid4(),
                    submission_id=identifier,
                    state="queued",
                    stage="queued",
                    sequence=0,
                    cases_total=None,
                    cases_done=0,
                    terminal_reason=None,
                    submission_digest="a" * 64,
                    question_version_binding=None,
                    package_digest=None,
                    runner_policy_digest=None,
                    environment_digest=None,
                    question_version_id=question,
                    started_at=None,
                    finished_at=None,
                    created_at=moment,
                    updated_at=moment,
                )
            )
    return str(identifier)


# --- 队列选择 -----------------------------------------------------------------------------


@db
def test_awaiting_evaluation_selects_accepted_submissions_without_a_run(engine: Engine) -> None:
    """候选集 = `validated`/`queued` 且**尚无**运行行（`NOT EXISTS` 让重跑幂等，不重复评测）。"""
    ready = _seed_submission(engine, state="validated", with_run=False)
    queued = _seed_submission(engine, state="queued", with_run=False)
    already = _seed_submission(engine, state="validated", with_run=True)
    running = _seed_submission(engine, state="running", with_run=False)
    done = _seed_submission(engine, state="completed", with_run=False)

    selected = awaiting_evaluation(engine, limit=10)

    assert set(selected) == {ready, queued}
    assert already not in selected and running not in selected and done not in selected


# --- outbox 投递（本文件的核心：事件必须真的出得去） ---------------------------------------


@db
@broker
def test_run_once_publishes_pending_events_and_marks_them_published(engine: Engine) -> None:
    """回归：worker 的一轮必须真的把 outbox 投出去、并把 `published_at` 写上。

    这条用例在"派发协程被创建后丢弃"的实现上失败（`published_at` 恒为空、broker 上永远没有消息），
    这正是它存在的全部理由。两端都断言：**数据库状态**（`published_at` 非空）与 **broker 上的真实
    消息**（能读回同一个 `event_id`）——只断言前者的话，"把行标成已发布但根本没发"也能通过。

    流由 `asyncio.run` 单独建/删，worker 那一轮在**没有事件循环**的同步上下文里跑——worker 是同步
    进程，在事件循环内调用它等于测一个生产里不存在的形态。
    """
    stream_name = f"B2_EVENTS_{uuid.uuid4().hex[:10].upper()}"
    evaluator = _StubEvaluator()

    async def setup_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().add_stream(name=stream_name, subjects=["autograder.v1.>"])

    async def read_stream() -> list[tuple[dict, dict]]:
        async with connected_bus() as bus:
            return await read_back(bus, stream_name, SUBMISSION_ACCEPTED, expect=1)

    async def drop_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().delete_stream(stream_name)

    asyncio.run(setup_stream())
    try:
        # 流必须**先**建好：生产里流由 B4 运维（RQ-B-4-20），这里只是复制那份前提。
        event_id = enqueue_event(engine, subject=SUBMISSION_ACCEPTED)
        evaluated = run_once(
            _WorkerRuntime(  # type: ignore[arg-type]
                engine=engine, evaluator=evaluator, outbox=_dispatcher(engine)
            )
        )
        assert outbox_row(engine, event_id).published_at is not None
        received = asyncio.run(read_stream())
    finally:
        asyncio.run(drop_stream())

    assert evaluated == 0, "本次没有待评测提交，只验证投递"
    assert len(received) == 1, "数据库说已发布，broker 上就必须真有这条消息"
    assert received[0][0]["event_id"] == event_id
    assert received[0][0]["event_type"] == SUBMISSION_ACCEPTED
    assert _published_count(engine) == 1


@db
def test_empty_outbox_does_not_touch_the_broker(engine: Engine) -> None:
    """没有待发行时**不连接** broker：`None` 表示"无事可做"，而不是"发失败了"。"""
    assert drain_outbox(_dispatcher(engine, url=UNREACHABLE_NATS_URL)) is None
    assert _published_count(engine) == 0


@db
def test_publish_failure_keeps_the_row_and_leaves_business_rows_untouched(engine: Engine) -> None:
    """broker 不可达 ⇒ 行仍留在 outbox（`published_at` 为空、尝试次数 +1），业务行**一行都不动**。

    "先写后发"的不变量：投递失败只影响投递时延，绝不回滚、也不修改 grade/submission。
    """
    submission_id = _seed_submission(engine, state="validated", with_run=False)
    before = _submission_row(engine, submission_id)
    event_id = enqueue_event(engine, subject=SUBMISSION_ACCEPTED)

    report = drain_outbox(_dispatcher(engine, url=UNREACHABLE_NATS_URL))

    assert report is not None
    assert (report.selected, report.published, report.failed) == (1, 0, 1)
    row = outbox_row(engine, event_id)
    assert row.published_at is None and row.attempts == 1 and row.dead_lettered_at is None
    after = _submission_row(engine, submission_id)
    assert (after.state, after.updated_at) == (before.state, before.updated_at)
    assert _published_count(engine) == 0


# --- HTTP 接收 → outbox → worker 投递（一条真实的端到端链路） -------------------------------


@pytest.fixture(scope="module")
def signer() -> _Signer:
    return _Signer()


@pytest.fixture(scope="module")
def intake_runtime(
    upgraded_database_url: str, tmp_path_factory: pytest.TempPathFactory, signer: _Signer
) -> IntakeRuntime:
    root = tmp_path_factory.mktemp("worker-artifacts")
    return build_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(root),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
            "B4_JWT_ISSUER": ISSUER,
            "B4_JWT_AUDIENCE": AUDIENCE,
        }
    )


@pytest.fixture()
def intake_client(intake_runtime: IntakeRuntime, engine: Engine) -> Iterator[TestClient]:
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(intake_runtime))
    with TestClient(app) as test_client:
        yield test_client


@db
@broker
def test_accepted_submission_is_evaluated_and_published_end_to_end(
    engine: Engine, intake_client: TestClient, signer: _Signer
) -> None:
    """`POST /api/v1/submissions` → outbox 一行 → worker 一轮 → **真实 broker 读回**同一个事件。

    这条链路此前从未跑通过（事件只进不出）。它同时验证 worker 的输入路径：候选提交由
    `load_submission_envelope` 从**接收层真实写入的行**里重建（摘要自校验通过），而不是夹具造的信封。
    """
    student = (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    payload = _envelope(student)
    accepted = intake_client.post(
        ENDPOINT, json=payload, headers=_auth(_student_token(signer, student))
    )
    assert accepted.status_code == 202, accepted.text

    stream_name = f"B2_EVENTS_{uuid.uuid4().hex[:10].upper()}"
    evaluator = _StubEvaluator()

    async def setup_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().add_stream(name=stream_name, subjects=["autograder.v1.>"])

    async def read_stream() -> list[tuple[dict, dict]]:
        async with connected_bus() as bus:
            return await read_back(bus, stream_name, SUBMISSION_ACCEPTED, expect=1)

    async def drop_stream() -> None:
        async with connected_bus() as bus:
            await bus.jetstream().delete_stream(stream_name)

    asyncio.run(setup_stream())
    try:
        evaluated = run_once(
            _WorkerRuntime(  # type: ignore[arg-type]
                engine=engine, evaluator=evaluator, outbox=_dispatcher(engine)
            )
        )
        received = asyncio.run(read_stream())
    finally:
        asyncio.run(drop_stream())

    assert evaluated == 1, "刚接收的提交必须进入候选集"
    assert [item[0]["payload"]["submission_id"] for item in received] == [payload["submission_id"]]
    assert _published_count(engine) == 1
    assert [envelope.submission_id for envelope in evaluator.envelopes] == [
        payload["submission_id"]
    ]
    reconstructed = evaluator.envelopes[0]
    # 装载器的自校验通过（摘要与存储引用都对得上），因此下面这些字段是从库里重建出来的事实。
    assert reconstructed.submission_digest
    assert [item.path for item in reconstructed.files] == ["main.py"]
