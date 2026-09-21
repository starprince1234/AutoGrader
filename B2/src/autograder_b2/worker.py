"""Evaluation worker: the component that actually advances accepted submissions.

Why this module has to exist: `EvaluationOrchestrator.run()` is synchronous and
`runtime._wire_outbox_dispatcher` says outright that scheduling is "the worker/daemon's
business -- this module plants no timer". The host serves HTTP only, so without a
worker stdin submissions are accepted, stored, and then **sit in `validated` forever**:
no run row, no progress stream, no report, no grade. That was the state of the composed
system at integration time -- the accept path worked and nothing downstream did.

Scope, kept deliberately narrow and consistent with the rest of B2:

* one submission at a time. `ADR-0004` and the resource ledger allow exactly one
  concurrent sandbox, and `EvaluationOrchestrator` already enforces a capacity of one;
  this loop does not add a second concurrency model on top.
* the orchestrator is reused unchanged, so a worker run and a test run take the same
  code path (stages, digests, evidence, report).
* the outbox is drained by the same loop, because "evaluate then publish" is a single
  unit of progress in this system and splitting them would leave events sitting in the
  outbox whenever only one of the two loops runs.
* no claim table, no lease, no distributed locking: the candidate query only selects
  submissions that have **no** evaluation run at all, and `create_run` is inside the
  orchestrator's own transaction. Two workers started by accident would duplicate work;
  the deployment is documented as single-worker, and pretending to solve multi-worker
  coordination here would be a compatibility layer for a case that does not exist.

This file is also the module's **only** outbox driver (G-EVT-7): `B2Runtime` assembles a
`NatsOutboxDispatcher` but plants no timer, and the HTTP host never calls this loop. An
event therefore leaves `b2.outbox_events` only while a worker process is running -- that
is an operational fact, not a detail, so it is documented in
docs/runbooks/local-development.md rather than left implicit.

Usage (documented in docs/runbooks/local-development.md):

    uv run python -m autograder_b2.worker            # loop until interrupted
    uv run python -m autograder_b2.worker --once     # one pass, for checks
    uv run python -m autograder_b2.worker --idle-exit  # exit when the queue is empty
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.db.schema import evaluation_runs, submissions
from autograder_b2.domain.events import DispatchReport
from autograder_b2.events.dispatcher import NatsOutboxDispatcher
from autograder_b2.integration.submission_loader import load_submission_envelope
from autograder_b2.logging import build_logger, log_event
from autograder_b2.runtime import B2Runtime, build_b2_runtime

logger = build_logger("worker")

DEFAULT_IDLE_SECONDS = 2.0

#: 单轮投递的行数上限（与派发器的默认批量一致：一轮不把整张 outbox 读进内存）。
OUTBOX_DRAIN_LIMIT = 50


def awaiting_evaluation(engine: Engine, *, limit: int = 1) -> list[str]:
    """Submission ids that are ready for evaluation and have no run yet.

    "Ready" means the intake state machine has accepted it (`validated` or `queued`).
    The `NOT EXISTS` clause is what makes the pass idempotent: once the orchestrator has
    created a run, the submission leaves the candidate set even if the worker dies before
    finishing, so a restart resumes rather than re-evaluating. Ordering by `created_at`
    keeps the queue FIFO, which is what a single-slot evaluator should do.
    """
    has_run = sa.select(sa.literal(1)).where(
        evaluation_runs.c.submission_id == submissions.c.submission_id
    )
    statement = (
        sa.select(submissions.c.submission_id)
        .where(submissions.c.state.in_(("validated", "queued")))
        .where(~sa.exists(has_run))
        .order_by(submissions.c.created_at)
        .limit(limit)
    )
    with engine.connect() as connection:
        return [str(row[0]) for row in connection.execute(statement)]


def drain_outbox(outbox: NatsOutboxDispatcher) -> DispatchReport | None:
    """投递一轮 outbox；`None` 表示没有待发行（此时**不触碰 broker**）。

    走派发器的**同步入口** `dispatch_sync`：本进程没有常驻事件循环（编排器是同步的），而该入口正是
    为这种调用方写的（自建循环 → 投递 → 关连接）。**此前这里漏了等待**：协程对象被创建后丢弃
    （`RuntimeWarning: coroutine 'NatsOutboxDispatcher.dispatch' was never awaited`），于是 outbox
    只进不出、`published_at` 永远为空、B3 的统计链一个事件都收不到。

    先查一次 `pending_rows` 再决定是否连接：空轮不建连接，既省掉无意义的握手，也避免在"没有事件要发"
    时对着不可达的 broker 刷失败日志。`pending_rows` 与 `dispatch` 都用 `insert_seq` 定序，因此这一步
    不改变投递顺序（顺序由真正取行时再次确定）。
    """
    if not outbox.pending_rows(1):
        return None
    return outbox.dispatch_sync(limit=OUTBOX_DRAIN_LIMIT)


def run_once(runtime: B2Runtime, *, limit: int = 1) -> int:
    """Evaluate up to `limit` awaiting submissions. Returns how many were evaluated."""
    if runtime.evaluator is None:
        raise RuntimeError(
            "the evaluation chain is not configured: set B2_B3_BASE_URL, "
            "B2_B3_AUTHORIZATION and B2_WORKSPACE_ROOT before starting the worker"
        )
    engine = runtime.engine
    if engine is None:
        raise RuntimeError("the database is not configured: set DATABASE_URL")
    evaluated = 0
    for submission_id in awaiting_evaluation(engine, limit=limit):
        with engine.connect() as connection:
            envelope = load_submission_envelope(connection, submission_id)
        log_event(
            logger,
            "worker.evaluating",
            submission_id=submission_id,
            question_version_id=str(envelope.question_version_id),
        )
        outcome = runtime.evaluator.run(envelope)
        evaluated += 1
        log_event(
            logger,
            "worker.evaluated",
            submission_id=submission_id,
            evaluation_run_id=str(getattr(outcome, "evaluation_run_id", "") or ""),
            state=str(getattr(outcome, "state", "") or ""),
        )
    if runtime.outbox is not None:
        report = drain_outbox(runtime.outbox)
        if report is not None and report.published:
            log_event(logger, "worker.outbox_drained", published=report.published)
    return evaluated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B2 evaluation worker.")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument(
        "--idle-exit",
        action="store_true",
        help="exit as soon as a pass finds nothing to do (handy for checks)",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=DEFAULT_IDLE_SECONDS,
        help="sleep between empty passes",
    )
    arguments = parser.parse_args(argv)

    runtime = build_b2_runtime(os.environ)
    if runtime.evaluator is None:
        print(f"evaluation chain unavailable: {runtime.evaluation_reason}", file=sys.stderr)
        return 2

    try:
        while True:
            evaluated = run_once(runtime)
            if arguments.once:
                return 0
            if evaluated == 0:
                if arguments.idle_exit:
                    return 0
                time.sleep(arguments.idle_seconds)
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        log_event(logger, "worker.stopped")
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
