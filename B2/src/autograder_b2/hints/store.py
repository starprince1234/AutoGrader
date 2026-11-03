"""学习提示的存储（`b2.evaluation_learning_hints`，迁移 0015）。

三条与评语轨不同的性质，代码里都成立：

1. **一 run 一行**（`uq_evaluation_learning_hints_evaluation_run_id`）：成功的提示是终局，
   同一 run 的再次 POST 不会把它覆盖成半截数据；学生重新提交会得到**新的 run**，旧 run 的提示
   原样留在历史里（`TC-HINT-091`）。
2. **归属落库**：`student_id` 是写入时的 run owner，不是请求参数。读取时路由层再按同一 owner 判定
   一次——"这一行属于谁"因此不需要一条按行变化的策略规则。
3. **没有分数列**：表里没有 `score`/`is_pass`/`result_digest`/`case_evidence`。提示永远不能成为
   分数依据，这一点在存储层就是结构性的。

`unavailable`（本部署未配置 provider）**不落库**：它是配置事实，`GET` 现场表达。`not_requested`
同样不落库：没有生成过就没有行。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert

from autograder_b2.db.schema import evaluation_learning_hints
from autograder_b2.hints.parsing import HintStep

#: 落库状态（契约 `LearningHintsResponse.status` 去掉 `unavailable` 与 `not_requested`：
#: 前者是配置事实、后者是"没有行"，都不是落库状态）。
HINT_STATUSES: tuple[str, ...] = ("pending", "ready", "failed")

#: 失败类别上限（表列 CHECK 与写入侧共用同一数字）。
FAILURE_CATEGORY_LIMIT = 100
IDEMPOTENCY_KEY_LIMIT = 200


class HintsStorageError(RuntimeError):
    """存储层发现不一致的行（例如 JSON 形状被写坏）——明确失败，不用空数据掩盖。"""


@dataclass(frozen=True, slots=True)
class LearningHintsRecord:
    """一行学习提示（读回形态）。"""

    evaluation_learning_hint_id: str
    evaluation_run_id: str
    student_id: str
    status: str
    steps: tuple[HintStep, ...]
    provider: str
    model: str
    prompt_version: str
    context_scope: tuple[str, ...]
    idempotency_key: str
    failure_category: str | None
    requested_at: datetime
    generated_at: datetime | None

    def as_contract(self, disclaimers: Sequence[str]) -> dict[str, Any]:
        """契约 `LearningHintsResponse` 载荷（**不含** score/evidence/raw model output）。"""
        return {
            "evaluation_run_id": self.evaluation_run_id,
            "generation_id": self.evaluation_learning_hint_id,
            "status": self.status,
            "hint_steps": [step.as_contract() for step in self.steps],
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "context_scope": list(self.context_scope),
            "failure_category": self.failure_category,
            "disclaimers": list(disclaimers),
        }


def _empty_body(
    evaluation_run_id: str, status: str, disclaimers: Sequence[str]
) -> dict[str, Any]:
    return {
        "evaluation_run_id": evaluation_run_id,
        "generation_id": None,
        "status": status,
        "hint_steps": [],
        "provider": None,
        "model": None,
        "prompt_version": None,
        "context_scope": [],
        "failure_category": None,
        "disclaimers": list(disclaimers),
    }


def unavailable_body(evaluation_run_id: str, disclaimers: Sequence[str]) -> dict[str, Any]:
    """本部署没有 provider：正常答复（不是错误），已生成的旧提示仍可 GET（见 `load_hints`）。"""
    return _empty_body(evaluation_run_id, "unavailable", disclaimers)


def not_requested_body(evaluation_run_id: str, disclaimers: Sequence[str]) -> dict[str, Any]:
    """provider 可用但这条运行还没有生成过提示。"""
    return _empty_body(evaluation_run_id, "not_requested", disclaimers)


def not_applicable_body(evaluation_run_id: str, disclaimers: Sequence[str]) -> dict[str, Any]:
    """已完成但**没有可讲的失败证据**：返回正常答复而不是虚构原因（`TC-HINT-087`）。"""
    return _empty_body(evaluation_run_id, "not_applicable", disclaimers)


def load_hints(connection: Connection, evaluation_run_id: str) -> LearningHintsRecord | None:
    row = connection.execute(
        sa.select(evaluation_learning_hints).where(
            evaluation_learning_hints.c.evaluation_run_id == _uuid(evaluation_run_id)
        )
    ).fetchone()
    if row is None:
        return None
    scope = row.context_scope
    if not isinstance(scope, list):
        raise HintsStorageError("evaluation_learning_hints.context_scope 不是 JSON 数组")
    steps = _steps_from_json(row.hint_steps)
    return LearningHintsRecord(
        evaluation_learning_hint_id=str(row.evaluation_learning_hint_id),
        evaluation_run_id=str(row.evaluation_run_id),
        student_id=str(row.student_id),
        status=row.status,
        steps=steps,
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        context_scope=tuple(str(item) for item in scope),
        idempotency_key=row.idempotency_key,
        failure_category=row.failure_category,
        requested_at=row.requested_at,
        generated_at=row.generated_at,
    )


def _steps_from_json(value: Any) -> tuple[HintStep, ...]:
    if not isinstance(value, list):
        raise HintsStorageError("evaluation_learning_hints.hint_steps 不是 JSON 数组")
    steps: list[HintStep] = []
    for item in value:
        if not isinstance(item, dict):
            raise HintsStorageError("evaluation_learning_hints.hint_steps 的元素不是对象")
        level, title, text = item.get("level"), item.get("title"), item.get("text")
        if not all(isinstance(part, str) for part in (level, title, text)):
            raise HintsStorageError("evaluation_learning_hints.hint_steps 字段缺失")
        steps.append(HintStep(level=level, title=title, text=text))
    return tuple(steps)


def begin_generation(
    connection: Connection,
    *,
    evaluation_run_id: str,
    student_id: str,
    provider: str,
    model: str,
    prompt_version: str,
    context_scope: Sequence[str],
    idempotency_key: str,
    request_id: str | None,
    occurred_at: datetime,
) -> None:
    """把该运行的提示置为 `pending`（显式重试即覆盖上一行）。

    先落 `pending` 再调用 provider：并发的 `GET` 看到的是"已受理、未完成"这个**真实**状态，
    而不是"什么都没有"；进程中途消失留下的 `pending` 也正是当时的事实。
    """
    values: dict[str, object] = {
        "student_id": _uuid(student_id),
        "status": "pending",
        "hint_steps": [],
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "context_scope": list(context_scope),
        "idempotency_key": idempotency_key[:IDEMPOTENCY_KEY_LIMIT],
        "failure_category": None,
        "request_id": request_id,
        "requested_at": occurred_at,
        "generated_at": None,
    }
    statement = pg_insert(evaluation_learning_hints).values(
        evaluation_learning_hint_id=uuid.uuid4(),
        evaluation_run_id=_uuid(evaluation_run_id),
        **values,
    )
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=[evaluation_learning_hints.c.evaluation_run_id],
            set_={name: statement.excluded[name] for name in values},
        )
    )


def finish_generation(
    connection: Connection,
    *,
    evaluation_run_id: str,
    steps: Sequence[HintStep],
    occurred_at: datetime,
) -> None:
    """置为 `ready`：写入 1–3 条提示与生成时刻（表约束拦住 0 条或超过 3 条）。"""
    if not steps:
        raise ValueError("ready 状态必须至少包含一条提示")
    connection.execute(
        sa.update(evaluation_learning_hints)
        .where(evaluation_learning_hints.c.evaluation_run_id == _uuid(evaluation_run_id))
        .values(
            status="ready",
            hint_steps=[step.as_contract() for step in steps],
            failure_category=None,
            generated_at=occurred_at,
        )
    )


def fail_generation(
    connection: Connection,
    *,
    evaluation_run_id: str,
    failure_category: str,
    occurred_at: datetime,
) -> None:
    """置为 `failed`（**只**保存脱敏类别；不保存模型原文、不保存 prompt）。"""
    connection.execute(
        sa.update(evaluation_learning_hints)
        .where(evaluation_learning_hints.c.evaluation_run_id == _uuid(evaluation_run_id))
        .values(
            status="failed",
            hint_steps=[],
            failure_category=failure_category[:FAILURE_CATEGORY_LIMIT],
            generated_at=occurred_at,
        )
    )


def count_generations(connection: Connection, *, student_id: str, since: datetime) -> int:
    """某学生在窗口内**发起过**的生成次数（含失败）：预算按 authenticated student 计数。"""
    return int(
        connection.execute(
            sa.select(sa.func.count())
            .select_from(evaluation_learning_hints)
            .where(
                sa.and_(
                    evaluation_learning_hints.c.student_id == _uuid(student_id),
                    evaluation_learning_hints.c.requested_at >= since,
                )
            )
        ).scalar_one()
    )


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


__all__ = [
    "FAILURE_CATEGORY_LIMIT",
    "HINT_STATUSES",
    "IDEMPOTENCY_KEY_LIMIT",
    "HintsStorageError",
    "LearningHintsRecord",
    "begin_generation",
    "count_generations",
    "fail_generation",
    "finish_generation",
    "load_hints",
    "not_applicable_body",
    "not_requested_body",
    "unavailable_body",
]
