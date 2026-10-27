"""分层学习提示的服务层：资权之外的**业务判定**（能不能生成）+ 生成 + 落库。

路由层负责 HTTP 与身份/对象可见性；本模块负责其余判定，且每一条都对应一个可区分的失败：

| 情形 | 异常 | 对外 |
|---|---|---|
| 本部署没有 provider | `HintsNotConfigured` | `POST` 503；`GET` 200 `unavailable` |
| 运行不是 `completed` | `HintsRunNotCompleted` | 409（不调用 provider） |
| 运行被安全拒绝 / 有安全结论 `found` | `HintsRejectedRun` | 409（不给"绕过检查"式提示） |
| 终态是基础设施/内部错误 | `HintsInfrastructureFailure` | 409（不把系统错误归因于学生） |
| 已完成但没有可讲的失败证据 | `HintsNotApplicable` | 200 `not_applicable`（不调用 provider） |
| 学生达到部署预算 | `HintsBudgetExceeded` | 429（不调用 provider） |
| 同一 run 正在生成 | `HintsInProgress` | 409 |
| provider 失败 / 输出非法 / 输出越界 | 不抛给调用方：该行标 `failed` | 503（provider）或 200 `failed`（输出问题） |

## 幂等

`Idempotency-Key` + 一 run 一行：

- 相同 key 且已 `ready` → 返回同一行，**不**调用 provider；
- 不同 key 但该 run 已 `ready` → 仍返回已有行（一次 run 的成功提示是终局，不被"重试"改写）；
- 已 `failed` → 允许显式重试（新的 key 或同 key 都行：失败不是结果）；
- `pending` 且未过期 → 409（另一次请求正在进行）；过期（> 5 分钟）视为上次中断，允许重试。

## 预算计数口径

按**该学生窗口内已有提示行的 run 数**计数。一次提交最多产生一个 run，因此这个数天然被"学生
在这段时间里提交了几次"上界；同一 run 的重试不重复计入。客户端不维护配额，服务端计数是唯一口径。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import Engine

from autograder_b2.commentary.provider import CommentaryProvider, CommentaryProviderError
from autograder_b2.commentary.scope import CommentaryEvidence, collect_evidence
from autograder_b2.config import LearningHintsSettings
from autograder_b2.domain.clock import utc_now
from autograder_b2.hints import store
from autograder_b2.hints.context import B3LearningContextClient, QuestionContext
from autograder_b2.hints.parsing import (
    HintResponseError,
    UnsafeHintOutput,
    parse_hint_steps,
)
from autograder_b2.hints.prompt import HINTS_PROMPT_VERSION, build_hints_prompt

if TYPE_CHECKING:
    from autograder_b2.evaluation.store import RunRow

#: 提示轨 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.hints.service")

#: `pending` 行的过期阈值：超过它就视为"上一次请求中断了"，允许显式重试。
PENDING_STALE_AFTER = timedelta(minutes=5)

#: 失败类别（落库的脱敏类别；不含模型原文）。
FAILURE_PROVIDER = "provider_error"
FAILURE_INVALID_OUTPUT = "invalid_model_output"
FAILURE_UNSAFE_OUTPUT = "unsafe_output"


class HintsNotConfigured(RuntimeError):
    """本部署没有配置 B2 provider（与评语轨共用同一配置）。"""


class HintsRunNotCompleted(RuntimeError):
    """运行尚未到达 `completed`：此刻没有"这一次完整评测"的证据。"""


class HintsRejectedRun(RuntimeError):
    """运行被安全规则拒绝（或有 `found` 的安全结论）：不生成"如何绕过"的提示。"""


class HintsInfrastructureFailure(RuntimeError):
    """终态原因是基础设施/内部错误：系统问题不归因于学生，也不生成学习提示。"""


class HintsNotApplicable(RuntimeError):
    """已完成，但没有可讲的确定性失败证据（不要虚构失败原因）。"""


class HintsBudgetExceeded(RuntimeError):
    """该学生在部署窗口内的提示生成次数已达上限。"""


class HintsInProgress(RuntimeError):
    """同一 run 的另一次生成正在进行（或刚刚中断，尚未过期）。"""


@dataclass(frozen=True, slots=True)
class HintsOutcome:
    """一次生成尝试的结果：落库记录 + 失败类别（`None` = 成功）+ 是否幂等重放。"""

    record: store.LearningHintsRecord
    failure_category: str | None = None
    replayed: bool = False

    @property
    def ok(self) -> bool:
        return self.failure_category is None


class HintsService:
    """运行期装配（引擎 + provider + 部署预算 + 可选的 B3 公开上下文客户端）。"""

    def __init__(
        self,
        *,
        engine: Engine,
        provider: CommentaryProvider | None,
        not_configured_reason: str | None,
        settings: LearningHintsSettings,
        context: B3LearningContextClient | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._provider = provider
        self._not_configured_reason = not_configured_reason
        self._settings = settings
        self._context = context
        self._now = now

    @property
    def configured(self) -> bool:
        return self._provider is not None

    @property
    def not_configured_reason(self) -> str:
        return self._not_configured_reason or "未配置模型服务"

    @property
    def disclaimers(self) -> tuple[str, ...]:
        from autograder_b2.hints.prompt import HINTS_DISCLAIMERS

        return HINTS_DISCLAIMERS

    def load(self, evaluation_run_id: str) -> store.LearningHintsRecord | None:
        with self._engine.connect() as connection:
            return store.load_hints(connection, evaluation_run_id)

    def generate(
        self,
        *,
        run: RunRow,
        student_id: str,
        idempotency_key: str,
        request_id: str | None,
    ) -> HintsOutcome:
        """生成（或幂等返回）该 run 的学习提示；调用方已完成身份与对象可见性判定。"""
        if self._provider is None:
            raise HintsNotConfigured(self.not_configured_reason)
        self._require_generatable(run)

        now = self._now()
        with self._engine.connect() as connection:
            existing = store.load_hints(connection, run.evaluation_run_id)
            if existing is not None:
                replayed = self._replay_if_terminal(existing, idempotency_key, now=now)
                if replayed is not None:
                    return replayed
            evidence = collect_evidence(connection, run.evaluation_run_id)
            self._require_failure_evidence(evidence, run)
            window_start = now - timedelta(hours=self._settings.window_hours)
            used = store.count_generations(
                connection, student_id=student_id, since=window_start
            )
            if used >= self._settings.max_generations_per_window:
                raise HintsBudgetExceeded(
                    f"learning-hint generation budget exhausted ({used}"
                    f"/{self._settings.max_generations_per_window} within"
                    f" {self._settings.window_hours}h)"
                )

        context = self._fetch_context(run)
        context_scope = (*evidence.input_scope, *(context.scope_paths() if context else ()))
        with self._engine.begin() as connection:
            store.begin_generation(
                connection,
                evaluation_run_id=run.evaluation_run_id,
                student_id=student_id,
                provider=self._provider.config.provider,
                model=self._provider.config.model,
                prompt_version=HINTS_PROMPT_VERSION,
                context_scope=context_scope,
                idempotency_key=idempotency_key,
                request_id=request_id,
                occurred_at=now,
            )

        prompt = build_hints_prompt(
            evidence.as_prompt_json(), None if context is None else context.as_prompt_json()
        )
        try:
            raw = self._provider.complete(prompt)
            steps = parse_hint_steps(
                raw,
                title_limit=self._settings.title_limit,
                text_limit=self._settings.text_limit,
            )
        except UnsafeHintOutput as error:
            return self._fail(run.evaluation_run_id, FAILURE_UNSAFE_OUTPUT, error)
        except HintResponseError as error:
            return self._fail(run.evaluation_run_id, FAILURE_INVALID_OUTPUT, error)
        except CommentaryProviderError as error:
            return self._fail(run.evaluation_run_id, FAILURE_PROVIDER, error)
        except Exception as error:  # noqa: BLE001 - provider 的任何异常都只标记这一轨
            return self._fail(run.evaluation_run_id, FAILURE_PROVIDER, error)

        with self._engine.begin() as connection:
            store.finish_generation(
                connection,
                evaluation_run_id=run.evaluation_run_id,
                steps=steps,
                occurred_at=self._now(),
            )
        record = self._require_record(run.evaluation_run_id)
        return HintsOutcome(record=record)

    def _replay_if_terminal(
        self, existing: store.LearningHintsRecord, key: str, *, now: datetime
    ) -> HintsOutcome | None:
        """终局/进行中的既有行如何回应本次请求（`None` = 允许重新生成）。"""
        if existing.status == "ready":
            # 成功结果对该 run 是终局：无论 key 是否相同都返回同一行，不重复调用 provider。
            return HintsOutcome(record=existing, replayed=True)
        if existing.status == "failed" and existing.idempotency_key == key:
            # 同一 key 的重放返回**已有失败**（客户端可据此显示"暂时无法生成"），不偷偷再花一次调用。
            return HintsOutcome(
                record=existing, failure_category=existing.failure_category, replayed=True
            )
        if existing.status == "pending":
            if now - existing.requested_at < PENDING_STALE_AFTER:
                raise HintsInProgress("another hint generation for this run is in progress")
            return None  # 过期：视为上次中断，允许重试
        return None

    @staticmethod
    def _require_generatable(run: RunRow) -> None:
        if run.terminal_reason in ("infrastructure_error", "internal_error"):
            raise HintsInfrastructureFailure(
                f"this evaluation ended with {run.terminal_reason}; hints are unavailable"
            )
        if run.state == "rejected" or run.security_status == "found":
            raise HintsRejectedRun(
                "this evaluation was rejected by security rules; hints cannot suggest a workaround"
            )
        if run.state != "completed":
            raise HintsRunNotCompleted(
                f"evaluation is not completed yet (state: {run.state})"
            )

    @staticmethod
    def _require_failure_evidence(evidence: CommentaryEvidence, run: RunRow) -> None:
        """必须有**确定性**的失败证据可讲；空证据不强行生成（`TC-HINT-087`）。"""
        payload = evidence.payload
        cases = payload.get("case_evidence") or []
        failures = [item for item in cases if not item.get("passed")]
        static = payload.get("static_findings") or []
        blocking = [item for item in static if item.get("blocking")]
        if not failures and not blocking:
            raise HintsNotApplicable(
                "this completed run has no failed case and no blocking static finding to"
                " explain; refusing to invent a failure reason"
            )

    def _fetch_context(self, run: RunRow) -> QuestionContext | None:
        """读取公开题目上下文；未配置或不可用都只是"少读一节"，不重试、不影响生成。"""
        if self._context is None:
            return None
        try:
            context = self._context.fetch(run.question_version_id)
        except Exception as error:  # noqa: BLE001 - 上下文是可选输入，任何失败都不影响提示轨
            logger.warning(
                "learning-hints context unavailable: %s", type(error).__name__
            )
            return None
        if context is not None and context.truncated:
            logger.info("learning-hints context stem truncated to the input budget")
        return context

    def _fail(
        self, evaluation_run_id: str, failure_category: str, error: Exception
    ) -> HintsOutcome:
        # 只记录**类别**：不写模型原文、不写 prompt、不写证据（失败原因进入运维日志时同样脱敏）。
        logger.warning(
            "learning-hints generation failed: category=%s exception=%s",
            failure_category,
            type(error).__name__,
        )
        with self._engine.begin() as connection:
            store.fail_generation(
                connection,
                evaluation_run_id=evaluation_run_id,
                failure_category=failure_category,
                occurred_at=self._now(),
            )
        return HintsOutcome(
            record=self._require_record(evaluation_run_id),
            failure_category=failure_category,
        )

    def _require_record(self, evaluation_run_id: str) -> store.LearningHintsRecord:
        record = self.load(evaluation_run_id)
        if record is None:  # pragma: no cover - 刚刚写过同一运行的行
            raise RuntimeError("学习提示行在写入后读不回来（存储缺陷）")
        return record


__all__ = [
    "FAILURE_INVALID_OUTPUT",
    "FAILURE_PROVIDER",
    "FAILURE_UNSAFE_OUTPUT",
    "PENDING_STALE_AFTER",
    "HintsBudgetExceeded",
    "HintsInProgress",
    "HintsInfrastructureFailure",
    "HintsNotApplicable",
    "HintsNotConfigured",
    "HintsOutcome",
    "HintsRejectedRun",
    "HintsRunNotCompleted",
    "HintsService",
]
