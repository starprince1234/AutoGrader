"""评语轨的服务层：把白名单证据 → prompt → provider → 解析 → 落库串起来（`POST`/`GET` 的实现体）。

四条性质在这里落地（对应 `RQ-T-20` 与 `RQ-T-30` 的验收）：

- **不碰分数轨**：本模块只写 `b2.evaluation_commentaries`，只读 `scope.INPUT_SCOPE` 里的列。
  它没有 `EvaluationOrchestrator`、没有 runner、没有报告生成器的引用——"评语与参考分不影响分数"
  因此在依赖方向上就成立，而不是靠"记得别写"。
- **失败只标记这一条评语**：provider 异常、**响应形状不符**（`parsing`）与**参考分越界**在这里
  被归类为 `FAILED` 状态 + 一句脱敏原因；调用方（路由）据此返回 503 并说明原因，评测运行的
  状态与分数一个字节都不动。
- **默认关闭**：`configured=False` 时**没有 provider 对象**，`generate` 直接抛
  `CommentaryNotConfigured`，不存在任何出网调用点。
- **满分不由模型决定**：`max_score` 唯一的来源是 `CommentaryEvidence.case_weight_total`；
  模型给的任何同名字段在 `parsing` 阶段就让整次生成失败（`ADR-0014` 决策 2）。

`provider` 注入是**测试可见的接缝**（生产装配在 `runtime` 里由配置构造），与 `runner.run_case`
的 `probe`/`launcher` 同一形态——没有环境变量开关。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine

from autograder_b2.commentary import store
from autograder_b2.commentary.parsing import (
    AdvisoryProposal,
    CommentaryResponseError,
    parse_generation,
)
from autograder_b2.commentary.prompt import DISCLAIMERS, PROMPT_VERSION, build_prompt
from autograder_b2.commentary.provider import CommentaryProvider, CommentaryProviderError
from autograder_b2.commentary.scope import collect_evidence
from autograder_b2.domain.clock import utc_now

#: 评语轨 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.commentary.service")

#: 允许生成评语的运行状态：只有终态才有"这一次评测"的证据可讲（运行中会得到一份会过期的评语）。
TERMINAL_RUN_STATES = ("completed", "rejected", "failed")


class AdvisoryScoreRejected(RuntimeError):
    """模型提议的参考分**不能被接受**，因此整次生成判为失败（`ADR-0014` 决策 6）。

    两种情形共用这一类，但消息必须能分开读：

    1. **越界**：`score < 0` 或 `score > max_score`。**不截断**——截断会把"模型说了什么"
       悄悄改成"我们允许它说什么"。
    2. **没有可用的满分**：证据里没有任何用例权重（和为 0），而契约要求 `max_score > 0`，
       因此这次提议无法被填成一个合法的 `AdvisoryScore`。
    """


@dataclass(frozen=True, slots=True)
class CommentaryOutcome:
    """一次生成尝试的结果：落库后的记录 + 是否成功。"""

    record: store.CommentaryRecord
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


class CommentaryService:
    """评语轨的运行期装配（引擎 + 可选 provider）。

    `provider is None` 就是"本部署不生成评语"（默认状态）：此时 `generate` 直接拒绝，
    既没有 provider 对象也没有可调用的传输层。
    """

    def __init__(
        self,
        *,
        engine: Engine,
        provider: CommentaryProvider | None,
        not_configured_reason: str | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._provider = provider
        self._not_configured_reason = not_configured_reason
        self._now = now

    @property
    def configured(self) -> bool:
        return self._provider is not None

    @property
    def not_configured_reason(self) -> str:
        return self._not_configured_reason or "未配置模型服务"

    @property
    def disclaimers(self) -> tuple[str, ...]:
        """契约要求随每次答复原样返回的规范性标注。"""
        return DISCLAIMERS

    def load(self, evaluation_run_id: str) -> store.CommentaryRecord | None:
        with self._engine.connect() as connection:
            return store.load_commentary(connection, evaluation_run_id)

    def generate(self, evaluation_run_id: str, *, run_state: str) -> CommentaryOutcome:
        """生成或**重新生成**（覆盖），并返回落库后的记录。

        调用方已判权并且已确认运行存在；这里只做"能不能生成"与"生成"。

        步骤有意分开：取证 → prompt → provider → **解析** → **边界判定** → 落库。
        任一步失败都只把这一条评语标成 `failed`（连带清空四列参考分），
        因为"生成失败"与"生成了一份可以展示的建议"是两件不能混同的事。
        """
        if self._provider is None:
            raise store.CommentaryNotConfigured(self.not_configured_reason)
        if run_state not in TERMINAL_RUN_STATES:
            raise store.CommentaryRunNotTerminal(
                f"运行尚未到达终态（当前 {run_state}）：此时没有可讲的评测证据"
            )
        with self._engine.begin() as connection:
            evidence = collect_evidence(connection, evaluation_run_id)
            store.begin_generation(
                connection,
                evaluation_run_id=evaluation_run_id,
                provider=self._provider.config.provider,
                model=self._provider.config.model,
                prompt_version=PROMPT_VERSION,
                input_scope=evidence.input_scope,
                occurred_at=self._now(),
            )
        prompt = build_prompt(evidence.as_prompt_json())
        try:
            generation = parse_generation(self._provider.complete(prompt))
            advisory = resolve_advisory(
                generation.advisory, max_score=evidence.case_weight_total
            )
        except (CommentaryProviderError, CommentaryResponseError, AdvisoryScoreRejected) as error:
            reason = str(error)
            self._finish_failed(evaluation_run_id, reason)
            return CommentaryOutcome(record=self._require_record(evaluation_run_id), failure=reason)
        except Exception as error:  # noqa: BLE001 - provider 的任何异常都只标记这一条评语
            reason = f"provider raised {type(error).__name__}"
            self._finish_failed(evaluation_run_id, reason)
            return CommentaryOutcome(record=self._require_record(evaluation_run_id), failure=reason)
        self._finish_ready(evaluation_run_id, generation.text, advisory)
        return CommentaryOutcome(record=self._require_record(evaluation_run_id))

    def _finish_ready(
        self, evaluation_run_id: str, text: str, advisory: store.AdvisoryScore | None
    ) -> None:
        with self._engine.begin() as connection:
            store.finish_generation(
                connection,
                evaluation_run_id=evaluation_run_id,
                text=text,
                advisory=advisory,
                occurred_at=self._now(),
            )

    def _finish_failed(self, evaluation_run_id: str, reason: str) -> None:
        with self._engine.begin() as connection:
            store.fail_generation(
                connection,
                evaluation_run_id=evaluation_run_id,
                reason=reason,
                occurred_at=self._now(),
            )

    def _require_record(self, evaluation_run_id: str) -> store.CommentaryRecord:
        record = self.load(evaluation_run_id)
        if record is None:  # pragma: no cover - 刚刚写过同一运行的行
            raise RuntimeError("评语行在写入后读不回来（存储缺陷）")
        return record


def resolve_advisory(
    proposal: AdvisoryProposal | None, *, max_score: Decimal
) -> store.AdvisoryScore | None:
    """把模型提议补成落库形态的 `AdvisoryScore`（**满分由这里填入**）。

    `proposal is None` ⇒ `None`（模型这次没给建议，契约允许）。此时**不看** `max_score`：
    "没有参考分"与"满分是多少"无关。
    """
    if proposal is None:
        return None
    if max_score <= 0:
        raise AdvisoryScoreRejected(
            "证据里没有可用的满分（用例权重之和为 0），无法填入契约要求的 max_score（> 0）"
        )
    if proposal.score < 0 or proposal.score > max_score:
        raise AdvisoryScoreRejected(
            f"模型给出的参考分越界：score={_number(proposal.score)}"
            f" 不在 [0, {_number(max_score)}] 内；整次生成判为失败，不做截断"
        )
    return store.AdvisoryScore(
        score=proposal.score,
        max_score=max_score,
        rationale=proposal.rationale,
        confidence=proposal.confidence,
    )


def _number(value: Decimal) -> str:
    """`Decimal` → 不带指数、不带多余零的短字符串（只用于失败原因里报数）。"""
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f")


def commentary_settings_reason(missing: tuple[str, ...]) -> str:
    """缺配置时给运维读的一句话（指名缺哪些变量；不回显任何取值）。"""
    if not missing:
        return "评语轨未配置"
    return "评语轨未配置：需要 " + "、".join(missing)


__all__ = [
    "TERMINAL_RUN_STATES",
    "AdvisoryScoreRejected",
    "CommentaryOutcome",
    "CommentaryService",
    "commentary_settings_reason",
    "resolve_advisory",
]
