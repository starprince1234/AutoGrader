"""评语轨的存储（`b2.evaluation_commentaries`，迁移 0010 + 0014）。

**它有自己的表，这一点是设计的一部分**（`ADR-0009` 决策 2：评语有自己的存储位置与自己的 API 表达）。
由此得到两条可核验的性质，而不是口头承诺：

1. **删除这张表不影响可重放性**：没有任何其他表引用它，评测链路（编排、评分、报告、导出）
   也不读它。`tests/test_commentary_track.py` 的"清空评语表后重放同一提交"用例是对这条的实测。
2. **权威分数不在它里面**：表里只有文本、状态、provenance，以及**模型的顾问性参考分**
   （`advisory_*` 四列，迁移 0014）。分数轨的 `score` / `is_pass` / `result_digest` /
   `case_evidence` / 绑定摘要一个都不在这里；参考分与它们同名不同物，且**没有任何列被其他表引用**
   ——"它不是分"因此在存储层可断言，而不只是措辞。契约 `EvaluationCommentary` 的
   `additionalProperties: false` 与它的"无 `score`/`evidence` 字段"是同一条设计在两个层面的表达。

一次运行最多一行（`uq_evaluation_commentaries_run`）：重新生成是**覆盖**，不是追加——
否则"读评语"就需要一条"哪条才算数"的规则，而那种规则迟早会被读成"评分依据的版本"。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert

from autograder_b2.db.schema import evaluation_commentaries

#: 评语状态（契约 `EvaluationCommentary.status` 去掉 `unavailable`：那是"没有 provider"这个
#: **配置事实**，不是落库状态——库里不该有一行说"本部署没配模型"）。
COMMENTARY_STATUSES: tuple[str, ...] = ("pending", "ready", "failed")

#: 失败原因的字符上限（表列 CHECK 与写入侧共用同一数字）。
FAILURE_REASON_LIMIT = 500


class CommentaryNotConfigured(RuntimeError):
    """本部署没有配置模型服务（`ADR-0009` 决策 4 的默认状态）。

    `detail` 里列出**缺哪些配置项**，让运维知道该设什么，而不是看到一句笼统的失败。
    """


class CommentaryRunNotTerminal(RuntimeError):
    """运行还没有到达终态：此刻没有"这一次评测"的证据可讲。"""


@dataclass(frozen=True, slots=True)
class AdvisoryScore:
    """落库形态的 `AdvisoryScore`（`RQ-T-30` / `ADR-0014`）。

    `max_score` 与另外三个字段**来源不同**：`score`/`rationale`/`confidence` 来自模型，
    `max_score` 由 B2 从 `case_evidence.weight` 求和填入。类型把这件事写死在这里而不是靠注释：
    构造它的服务层先算出满分，模型输入在这条路径上根本不存在。
    """

    score: Decimal
    max_score: Decimal
    rationale: str
    confidence: str

    def as_contract(self) -> dict[str, object]:
        """契约 `AdvisoryScore` 载荷（数字过一遍 `float`：`JSONResponse` 不认 `Decimal`）。"""
        return {
            "score": float(self.score),
            "max_score": float(self.max_score),
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class CommentaryRecord:
    """一行评语（读回形态）。

    **为什么每条记录都带 provenance**：库里的一行只在**发生过一次生成尝试**时才存在
    （`pending`/`ready`/`failed`），而一次尝试必然有 provider/model/prompt/输入白名单。
    "没有 provider"这个状态**不落库**（它是配置事实，由 `unavailable_body` 现场表达）。
    因此 `failed` 也能指名是哪家 provider 失败的——契约里没有别的字段能承载这件事，
    而 `input_scope` 因此始终可审计。

    `advisory` 只在 `ready` 行上可能非空（表约束保证四列同生同死）；`pending`/`failed` 行恒为
    `None`——生成没成功就没有参考分可谈。
    """

    evaluation_run_id: str
    status: str
    text: str | None
    advisory: AdvisoryScore | None
    provider: str
    model: str
    prompt_version: str
    input_scope: tuple[str, ...]
    failure_reason: str | None
    requested_at: datetime
    generated_at: datetime | None

    def as_contract(self, disclaimers: Sequence[str]) -> dict[str, object]:
        """契约 `EvaluationCommentary` 载荷（字段集合由契约锁定，这里一个不多一个不少）。"""
        return {
            "evaluation_run_id": self.evaluation_run_id,
            "status": self.status,
            "text": self.text,
            "advisory_score": None if self.advisory is None else self.advisory.as_contract(),
            "provenance": {
                "provider": self.provider,
                "model": self.model,
                "prompt_version": self.prompt_version,
                "input_scope": list(self.input_scope),
            },
            "disclaimers": list(disclaimers),
        }


def unavailable_body(evaluation_run_id: str, disclaimers: Sequence[str]) -> dict[str, object]:
    """`status: unavailable` 的载荷：没有 provider 时的正常答复（契约明确这不是错误）。"""
    return {
        "evaluation_run_id": evaluation_run_id,
        "status": "unavailable",
        "text": None,
        "advisory_score": None,
        "provenance": None,
        "disclaimers": list(disclaimers),
    }


def load_commentary(connection: Connection, evaluation_run_id: str) -> CommentaryRecord | None:
    row = connection.execute(
        sa.select(evaluation_commentaries).where(
            evaluation_commentaries.c.evaluation_run_id == _uuid(evaluation_run_id)
        )
    ).fetchone()
    if row is None:
        return None
    scope = row.input_scope
    if not isinstance(scope, list):
        # 库行被写坏（列是 jsonb 数组）——明确失败，不用"空白名单"掩盖。
        raise TypeError("evaluation_commentaries.input_scope 不是 JSON 数组")
    return CommentaryRecord(
        evaluation_run_id=str(row.evaluation_run_id),
        status=row.status,
        text=row.text,
        advisory=_advisory_from_row(row),
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        input_scope=tuple(str(item) for item in scope),
        failure_reason=row.failure_reason,
        requested_at=row.requested_at,
        generated_at=row.generated_at,
    )


def _advisory_from_row(row: sa.Row[object]) -> AdvisoryScore | None:
    """四列 → `AdvisoryScore`（全空即 `None`；半截行由表约束挡住，走到这里就是缺陷）。"""
    values = (row.advisory_score, row.advisory_max_score, row.advisory_rationale, row.advisory_confidence)
    if all(item is None for item in values):
        return None
    if any(item is None for item in values):
        raise TypeError("evaluation_commentaries 的 advisory_* 四列不完整（表约束应当拦住这种行）")
    return AdvisoryScore(
        score=Decimal(row.advisory_score),
        max_score=Decimal(row.advisory_max_score),
        rationale=str(row.advisory_rationale),
        confidence=str(row.advisory_confidence),
    )


def begin_generation(
    connection: Connection,
    *,
    evaluation_run_id: str,
    provider: str,
    model: str,
    prompt_version: str,
    input_scope: Sequence[str],
    occurred_at: datetime,
) -> None:
    """把该运行的评语置为 `pending`（重新生成即覆盖上一行）。

    先落 `pending` 再调用 provider，有两个好处：①一次并发的 `GET` 看到的是
    "已受理、未完成"这个**真实**状态，而不是"什么都没有"；②进程若在生成中途消失，
    留下的行仍是 `pending`——那正是当时的事实（`ADR-0009`：重新生成不改变任何分数）。

    四个 `advisory_*` 列在这里被显式清空：重新生成会**丢弃上一次的参考分**，直到新的一次
    成功为止——否则 `pending` 行会带着上一版的模型建议，而读的人无从知道它属于哪一次生成。
    """
    values: dict[str, object] = {
        "status": "pending",
        "text": None,
        "advisory_score": None,
        "advisory_max_score": None,
        "advisory_rationale": None,
        "advisory_confidence": None,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "input_scope": list(input_scope),
        "failure_reason": None,
        "requested_at": occurred_at,
        "generated_at": None,
    }
    statement = pg_insert(evaluation_commentaries).values(
        evaluation_commentary_id=uuid.uuid4(),
        evaluation_run_id=_uuid(evaluation_run_id),
        **values,
    )
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=[evaluation_commentaries.c.evaluation_run_id],
            set_={
                name: statement.excluded[name]
                # `evaluation_commentary_id` 不在更新列里：覆盖语义是"这一行被重写"，
                # 不是"换一个新标识"——后者会让任何持有旧标识的引用悄悄失效。
                for name in values
            },
        )
    )


def finish_generation(
    connection: Connection,
    *,
    evaluation_run_id: str,
    text: str,
    advisory: AdvisoryScore | None,
    occurred_at: datetime,
) -> None:
    """把该运行的评语置为 `ready`（文本 + 可空参考分 + 生成时刻）。

    `advisory is None` 是**契约允许的正常结局**（模型这次没给建议 / 明确给了 null），
    不是失败：此时四列全为 `NULL`，`GET` 读回 `advisory_score: null`。
    """
    connection.execute(
        sa.update(evaluation_commentaries)
        .where(evaluation_commentaries.c.evaluation_run_id == _uuid(evaluation_run_id))
        .values(
            status="ready",
            text=text,
            advisory_score=None if advisory is None else advisory.score,
            advisory_max_score=None if advisory is None else advisory.max_score,
            advisory_rationale=None if advisory is None else advisory.rationale,
            advisory_confidence=None if advisory is None else advisory.confidence,
            failure_reason=None,
            generated_at=occurred_at,
        )
    )


def fail_generation(
    connection: Connection, *, evaluation_run_id: str, reason: str, occurred_at: datetime
) -> None:
    """把该运行的评语置为 `failed`（脱敏原因，仅供运维排查；分数轨不受影响）。

    参考分四列一并清空：`failed` 的定义就是"这次生成没有成功"，因此没有可展示的参考分——
    保留一份来自失败尝试的建议，会让 `status: failed` 与一个看起来正常的分数并排出现。
    """
    connection.execute(
        sa.update(evaluation_commentaries)
        .where(evaluation_commentaries.c.evaluation_run_id == _uuid(evaluation_run_id))
        .values(
            status="failed",
            text=None,
            advisory_score=None,
            advisory_max_score=None,
            advisory_rationale=None,
            advisory_confidence=None,
            failure_reason=reason[:FAILURE_REASON_LIMIT],
            generated_at=occurred_at,
        )
    )


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


__all__ = [
    "COMMENTARY_STATUSES",
    "FAILURE_REASON_LIMIT",
    "AdvisoryScore",
    "CommentaryNotConfigured",
    "CommentaryRecord",
    "CommentaryRunNotTerminal",
    "begin_generation",
    "fail_generation",
    "finish_generation",
    "load_commentary",
    "unavailable_body",
]
