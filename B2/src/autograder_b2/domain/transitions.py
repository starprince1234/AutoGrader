"""状态迁移史的写入与读取（唯一入口，避免"某处直接改 state 而不留痕"）。

`record_transitions` 在调用方的事务内追加迁移行，并在写入前用 `domain.states` 校验合法性——
非法迁移（例如 `validated → running`）在这里就被拒绝，而不是等到读报告时才发现状态史自相矛盾。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from autograder_b2.db.schema import submission_state_transitions
from autograder_b2.domain import states

Transition = tuple[str | None, str, str | None]


def record_transitions(
    connection: Connection,
    *,
    submission_id: str,
    transitions: Sequence[Transition],
    occurred_at: datetime,
) -> None:
    """按给定顺序追加迁移行（`(from_state, to_state, reason)`），序号从当前最大值继续。

    序号由这里统一分配，调用方**无法**给出乱序的 `sequence`；并发追加时唯一约束会拒绝重复序号
    （调用方重试即可），因此历史顺序不会静默错乱。

    **整批先校验、再写入**：逐行"校验一行写一行"会让"批量第 2 行非法"留下已写入的第 1 行——
    调用方若吞掉异常并提交，历史里就会出现半截迁移（实测可复现：`received→validated` 与
    重复的 `None→received` 同时存在）。校验是纯函数，先全过一遍不增加成本。
    """
    if not transitions:
        return
    for from_state, to_state, _reason in transitions:
        states.require_transition(from_state, to_state)
    identifier = uuid.UUID(submission_id)
    current = connection.execute(
        sa.select(sa.func.coalesce(sa.func.max(submission_state_transitions.c.sequence), 0)).where(
            submission_state_transitions.c.submission_id == identifier
        )
    ).scalar_one()
    next_sequence = int(current)
    for from_state, to_state, reason in transitions:
        next_sequence += 1
        connection.execute(
            sa.insert(submission_state_transitions).values(
                submission_state_transition_id=uuid.uuid4(),
                submission_id=identifier,
                sequence=next_sequence,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                occurred_at=occurred_at,
            )
        )


def history_of(connection: Connection, submission_id: str) -> list[dict[str, object]]:
    """按**显式序号**读取某个提交的迁移史（视图用；字段与契约 `StateTransition` 对齐）。

    不按时间戳排序：毫秒精度下同一事务的两条迁移会共享时间戳，按时间排序会退化为随机顺序。
    `sequence` 是内部字段，不进对外视图。
    """
    rows = connection.execute(
        sa.select(
            submission_state_transitions.c.from_state,
            submission_state_transitions.c.to_state,
            submission_state_transitions.c.occurred_at,
            submission_state_transitions.c.reason,
        )
        .where(submission_state_transitions.c.submission_id == uuid.UUID(submission_id))
        .order_by(submission_state_transitions.c.sequence)
    ).fetchall()
    return [
        {
            "from_state": row[0],
            "to_state": row[1],
            "occurred_at": row[2],
            "reason": row[3],
        }
        for row in rows
    ]
