"""B2 组合根：把接收运行时、评测链、成绩服务与 outbox 派发器装在一起，并**分项**报告就绪状态。

为什么是这一层（而不是 `plugin.py`，也不是 `integration/`）：`plugin.py` 只做宿主适配（descriptor、
路由装配、健康报告的形状），装配策略（哪些依赖必需、缺一个依赖时谁变不可用）属于业务决策；
`integration/` 里的 `adapters.py` / `bindings.py` / `submission_loader.py` 各有明确的单一职责
（端口生产实现 / 绑定摘要公式 / 信封装载），装配它们的是**消费者**，不是它们自己。

## 不可用的呈现方式（与 `IntakeRuntime` 同一套作法）

配置不完整**不阻止宿主启动**（其它模块仍应可用），但对应端点必须 `503` 且原因可读：

| 故障 | 谁不可用 | 原因所在 |
|---|---|---|
| 无 `DATABASE_URL`/`B2_ARTIFACT_ROOT`/JWKS | 全部路由 | `IntakeRuntime.unavailable_reason` |
| 无 `B2_IMAP_*` | 仅邮件入口 | `IntakeRuntime.email_unavailable_reason` |
| 无 B3 端点/凭据、无工作区根、工作区根与 CAS 根相交 | 仅评测读取路由 | `evaluation_reason` |
| 无 `B2_COMMENTARY_*`（默认） | 仅评语轨的 `POST`（`GET` 照常 200 + `unavailable`） | `commentary_reason` |
| 库结构版本与本构建不符 | 评测读取路由、成绩路由与评语路由 | `database_reason()`（每次请求探测） |
| 无 `NATS_URL` | 不影响任何 HTTP 路由 | `broker_reason` |

**绝不用假 package、假分数或裸进程执行来"让端点能用"**：任何一个必需依赖缺失都只产生"不可用 +
原因"，不产生替代实现。

## 四个分项健康（**三态**，不把"未配置"与"不可用"混为一谈）

`health_items()` 给出 **HTTP 入口 / 邮件入口 / 评测链 / 消息代理** 四项各自的状态与说明：

| 状态 | 含义 | 计入不健康？ |
|---|---|---|
| `ok` | 已配置且可用 | 否 |
| `not-configured` | 该项的部署变量**根本没给**（有意的部署选择，例如只跑接收与成绩的实例） | **否** |
| `unavailable` | 配了但不可用（配错/连不上/库结构不符/工作区根非法/DLQ 非空） | **是** |

`health_summary()` 的聚合规则就是 `healthy = 不存在 unavailable 项`。为什么"未配置"不算不健康：
一个**故意**不启用邮件或评测的部署会因此永远上报不健康，而恒假告警会训练运维忽略这个标志——比不报
更糟；而且 `healthy=False` 并不比 detail 里的分项信息多传达任何东西。反过来，"配了 B3 地址却不可用"
必须算不健康，否则 readiness 会在端点 503 时报"健康"，而那正是 `intake/runtime.py` 明确列为不可接受
的谎报。

**边界判断（我自己划的，若 lead 想改只需一句话）**：`not-configured` 只用于**可选的入口/能力**
（邮件入口、评测链、消息代理）。B2 自身的必需项（`DATABASE_URL` / `B2_ARTIFACT_ROOT` / JWKS）缺失是
`unavailable`：那不是"故意关掉某个能力"，而是这个模块根本无法服务，此时报健康就是上面那种谎报。
另外"半配置"（例如配了 `B2_B3_BASE_URL` 没配凭据、配了 `B2_IMAP_HOST` 没配 `B2_IMAP_MAILBOX`）同样是
`unavailable`——它是漏配，不是选择。

`health()` 的两半语义必须分清：**健康标志表达"部署是否按当前配置正常工作"，路由 503 表达"端点此刻能
不能服务"**。因此"评测链未配置"的实例 `healthy=True`，但评测路由照旧 503 + 可读原因（未配置 ≠ 能服务）。

## 失败计数与健康判定的关系（**只如实报告，不翻转标志**）

`record_failure()` 累计的是"这些路由**未预期**的依赖故障"（库不可达、库结构落后、上游通道不通）。
它与 `healthy` 的关系是一条**单向**规则，两边都写在这里，避免两处注释各说一套：

- 计数非零**不**把分项状态改成 `unavailable`、**不**改变 `healthy`；
- 计数非零**只**让该分项的 detail 多一句"自本进程启动以来累计 N 次未预期失败"，措辞里必须带
  "累计"与"不参与健康判定"，否则这句诊断会被读成"此刻仍在坏"。

为什么不翻转（这是刻意的，不是漏了）：计数**从进程启动起单调递增、永不重置**，因此一旦用它翻转
标志，这台实例在第一次瞬时抖动之后就**永久**不健康——恢复也不再变绿。那正是本文档开头拒绝的
"恒假告警"，它比不报更糟。就绪判定回答的是"现在能不能服务"，而"曾经失败过几次"回答不了这个问题；
真正的可用性由每次探测与每次请求自己回答（`database_reason()`、各路由的 503）。

**计数集合里不含已文档化的平台条件**：本平台没有沙箱（`ADR-0004` 禁止降级为裸进程）时参考解答验证
必然 `503`，那件事由 503 的 detail 与 `runner.sandbox_unavailable` 日志如实反映，**不**计入本计数器
（见 `reference.verification.ReferenceSandboxUnavailable`）——否则这台机器的这句诊断永远脏着，
运维会学会忽略它。

已交付的 `IntakeRuntime` 的原因文本是英文，本模块新增的原因文本是中文（面向运维/教师可读）；
统一诊断语言是 lead 的收口项，`intake/**` 由他人并行交付，本模块不改。

## 库结构版本为什么每次请求都探测

契约要求"库结构版本不符 ⇒ 该端点 503"。只在启动时探测一次，就无法覆盖"启动后有人迁移/回滚"；
探测成本是一次 `SELECT 1` 加一次单行查询，相对这些端点自身的工作量可以忽略，换来的是该断言**在
任何时刻都成立**，而不是"启动那一刻成立"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from autograder_b2.commentary import CommentaryService, build_provider
from autograder_b2.commentary.provider import CommentaryProviderConfig
from autograder_b2.config import (
    ConfigurationError,
    EvaluationChainNotConfigured,
    evaluation_chain_settings,
    load_commentary_settings,
)
from autograder_b2.db.schema import EXPECTED_SCHEMA_REVISION, outbox_events
from autograder_b2.evaluation import EvaluationOrchestrator, EvaluationOutcome
from autograder_b2.evaluation.orchestrator import DynamicExecutionGate
from autograder_b2.evaluation.quality import EvaluationQualityService
from autograder_b2.events.dispatcher import NatsOutboxDispatcher
from autograder_b2.events.nats import NatsEventBus
from autograder_b2.grades import GradeQueryService, GradesExportService
from autograder_b2.intake.runtime import IntakeRuntime, build_runtime
from autograder_b2.integration.adapters import (
    B3Endpoint,
    CasWorkspaceProvider,
    HttpPackageSource,
    RunnerCaseRunner,
    StaticSecurityDecider,
)
from autograder_b2.integration.bindings import UNSET_ENVIRONMENT_TAG
from autograder_b2.integration.submission_loader import load_submission_envelope
from autograder_b2.reference import (
    B3VerificationClient,
    ReferenceAnswerWorkspace,
    ReferenceVerificationService,
)
from autograder_b2.storage.cas import ContentAddressedStore

#: 分项健康的三态。用稳定的英文记号（而不是中文措辞）是为了让"未配置"与"不可用"在诊断串里
#: **可区分、可检索**，且不随说明文本的语言变化。
HEALTH_OK = "ok"
HEALTH_NOT_CONFIGURED = "not-configured"
HEALTH_UNAVAILABLE = "unavailable"
HEALTH_STATUSES = (HEALTH_OK, HEALTH_NOT_CONFIGURED, HEALTH_UNAVAILABLE)


class EvaluationUnavailable(RuntimeError):
    """评测链不可用（缺取包端点/凭据、缺工作区根、库结构不符）——对外是 503，不是"降级成功"。"""


@dataclass
class B2Runtime:
    """惰性装配的 B2 运行时；各项不可用时原因可读（不含内部地址、端口与凭据）。"""

    intake: IntakeRuntime
    evaluator: EvaluationOrchestrator | None = None
    evaluation_reason: str | None = None
    #: 评测链是"完全未配置"（有意的部署选择）还是"配了却不可用"——健康状态据此区分三态。
    evaluation_not_configured: bool = False
    grades: GradeQueryService | None = None
    exporter: GradesExportService | None = None
    #: 题目质量汇总（`CHG-0015`）：与成绩一样**只依赖数据库与认证器**，因此取包端点没配好不该
    #: 让教师看不到已有运行的聚合。
    quality: EvaluationQualityService | None = None
    #: 工作区根（宿主绝对路径）。存在的唯一理由是**证据包脱敏**要按配置把它从导出里剔除
    #: （`evaluation.appeal`）；它与 `environment_tag` 同属"装配时从评测链取回的部署事实"。
    workspace_root: str | None = None
    #: 评语轨（`RQ-T-20`）：**独立于评测链**——它只读已落库的 b2 证据，因此取包端点没配好不该
    #: 让教师看不到已有运行的评语。默认 `provider=None`（没有模型服务）。
    commentary: CommentaryService | None = None
    commentary_reason: str | None = None
    #: 参考解答验证（`CHG-0019`）：与评测链**共用**取包端点与并发闸门（`ADR-0015`：
    #: 不新增凭据、不新增跨模块机制）。它不读库，因此库结构版本不是它的前置。
    references: ReferenceVerificationService | None = None
    references_reason: str | None = None
    #: B3 服务通道的定位（与取包、取参考解答、记录存证同一个对象；`authorization` 为 `repr=False`）。
    #: 存在的理由之一是让"两处调用共用同一个通道"成为**可断言**的事实，而不是只能读代码看出来。
    b3_endpoint: B3Endpoint | None = None
    outbox: NatsOutboxDispatcher | None = None
    broker_reason: str | None = None
    #: 同理：没给 `NATS_URL` 是"未配置"，配了但派发不通是"不可用"。
    broker_not_configured: bool = False
    environment_tag: str = UNSET_ENVIRONMENT_TAG
    _failures: int = field(default=0, init=False)

    # --- 供路由层使用 -----------------------------------------------------------------

    @property
    def engine(self) -> Engine | None:
        """与接收层**共用**一个引擎：第二个引擎就是第二个连接池，只会让容量账本更难算。"""
        return self.intake.engine

    def record_failure(self) -> None:
        """记录一次**未预期**的端点依赖失败（供健康状态的 detail 如实报告；不含敏感细节）。

        两件事必须一起成立，见模块 docstring "失败计数与健康判定的关系"：

        - 它**不**翻转任何分项状态、**不**改变 `healthy`（单调计数一旦用于翻转，实例会在第一次抖动
          后永久不健康——那是"恒假告警"）；
        - 已文档化的平台条件（本平台没有沙箱）**不**走这里：那是"这台机器上该能力不可用"，
          由 503 的 detail 与依赖故障日志表达。

        与 `IntakeRuntime.record_failure` 分开计数：接收入口的失败数写进 "intake endpoint
        reported N failure(s)"，把评测/成绩路由的失败算进去会让那句话不再成立。
        """
        self._failures += 1

    @property
    def failure_count(self) -> int:
        return self._failures

    def database_reason(self) -> str | None:
        """库可用性 + 库结构版本（`None` = 可用）。两项探测**分开报原因**。"""
        engine = self.intake.engine
        if engine is None:
            return "数据库未配置"
        with engine.connect() as connection:
            try:
                connection.execute(text("SELECT 1"))
            except SQLAlchemyError:
                return "数据库不可达"
            try:
                revision = connection.execute(
                    text("SELECT version_num FROM b2.alembic_version_b2")
                ).scalar_one_or_none()
            except SQLAlchemyError:
                return "数据库结构未初始化（缺 b2 迁移版本表）"
        if revision is None:
            return "数据库结构未初始化（迁移版本为空）"
        if str(revision) != EXPECTED_SCHEMA_REVISION:
            return f"数据库结构版本与本构建不符（本构建期望 {EXPECTED_SCHEMA_REVISION}）"
        return None

    def evaluation_unavailable(self) -> str | None:
        """评测读取路由（运行视图 / SSE / 报告）的 503 原因；`None` = 可用。"""
        if not self.intake.ready:
            # 这三个端点要先认证，认证器与库配置都属于接收运行时。
            return self.intake.unavailable_reason or "接收运行时未装配"
        if self.evaluator is None:
            return self.evaluation_reason or "评测链未装配"
        return self.database_reason()

    def grades_unavailable(self) -> str | None:
        """成绩路由的 503 原因；`None` = 可用。

        成绩只依赖数据库（与认证器），**不**依赖评测链：取包端点没配好不应该让教师看不了已有成绩。
        """
        if not self.intake.ready:
            return self.intake.unavailable_reason or "接收运行时未装配"
        if self.grades is None or self.exporter is None:
            return "成绩服务未装配"
        return self.database_reason()

    def quality_unavailable(self) -> str | None:
        """质量汇总路由的 503 原因；`None` = 可用。

        与成绩同口径：它只读 `b2` 自己的表，**不**依赖评测链。把评测链当作前置会让"取包端点没配好"
        表现为"质量数据不可用"，而那份数据明明在库里。
        """
        if not self.intake.ready:
            return self.intake.unavailable_reason or "接收运行时未装配"
        if self.quality is None:
            return "质量汇总服务未装配"
        return self.database_reason()

    def commentary_unavailable(self) -> str | None:
        """评语路由的 503 原因；`None` = 可用。

        与成绩一样，评语**只**依赖数据库与认证器：没有 provider 不是"端点不可用"，
        而是"这个部署不生成评语"（`GET` 会照常返回 `status: unavailable`）。
        """
        if not self.intake.ready:
            return self.intake.unavailable_reason or "接收运行时未装配"
        if self.commentary is None:
            return self.commentary_reason or "评语轨未装配"
        return self.database_reason()

    def references_unavailable(self) -> str | None:
        """参考解答验证路由的 503 原因；`None` = 可用。

        **刻意不检查库结构版本**：本路径不读库（不落库是它的契约），把"迁移落后"当成它的前置
        会让一个只依赖 B3 通道与沙箱的功能因为无关的库状态而不可用。它需要的是认证器
        （与接收运行时同一份配置）与 B3 通道。
        """
        if not self.intake.ready:
            return self.intake.unavailable_reason or "接收运行时未装配"
        if self.references is None:
            return self.references_reason or "参考解答验证未装配"
        return None

    # --- 健康状态 ---------------------------------------------------------------------

    def health_items(self) -> dict[str, tuple[str, str]]:
        """四个独立事实的状态（三态之一 + 说明），逐项报告、不合并成一句。"""
        return {
            "http-intake": self.http_intake_health(),
            "email-intake": self.email_intake_health(),
            "evaluation-chain": self.evaluation_health(),
            "message-broker": self.broker_health(),
        }

    def health_summary(self) -> tuple[bool, str]:
        """模块级 `(healthy, detail)`：`healthy = 不存在 unavailable 项`。

        `detail` 逐项列出四个分项，且**区分** `not-configured`（未配置）与 `unavailable`（配了却
        不可用）——两项都不会被省略，也不会把"未配置"写成可用。
        """
        items = self.health_items()
        healthy = all(status != HEALTH_UNAVAILABLE for status, _ in items.values())
        detail = "; ".join(
            f"{name}: {status} - {text}" for name, (status, text) in items.items()
        )
        return healthy, detail

    def http_intake_health(self) -> tuple[str, str]:
        """HTTP 提交入口：B2 自身的必需项（库、工件根、JWKS）→ 库连通性 → 库结构版本。

        必需项**缺一个**都算 `unavailable` 而不是 `not-configured`：那不是"故意关掉某个能力"，
        而是这个模块根本无法服务——此时代码库里每一个端点都会 503，readiness 若报健康就是
        `intake/runtime.py` 明确列为不可接受的那种谎报。
        """
        if not self.intake.ready:
            return HEALTH_UNAVAILABLE, self.intake.unavailable_reason or "接收运行时未装配"
        healthy, detail = self.intake.health()
        return (HEALTH_OK if healthy else HEALTH_UNAVAILABLE), detail

    def email_intake_health(self) -> tuple[str, str]:
        """邮件入口：`B2_IMAP_*` 一个都没给 ⇒ 未配置（不计入不健康）；漏配 ⇒ 不可用。"""
        available, detail = self.intake.email_health()
        if available:
            return HEALTH_OK, detail
        if self.intake.email_unavailable_reason:
            # "半配置"（配了 HOST 没配 MAILBOX 之类）是漏配，不是有意的部署选择。
            return HEALTH_UNAVAILABLE, detail
        return HEALTH_NOT_CONFIGURED, detail

    def evaluation_health(self) -> tuple[str, str]:
        """评测链：装配 + 库结构版本（与路由的 503 判定**同一套**判据）。"""
        if not self.intake.ready:
            return HEALTH_UNAVAILABLE, "接收运行时未装配（认证器与数据库配置同属评测链的必需依赖）"
        if self.evaluator is None:
            if self.evaluation_not_configured:
                return HEALTH_NOT_CONFIGURED, self.evaluation_reason or "评测链未配置"
            return HEALTH_UNAVAILABLE, self.evaluation_reason or "评测链未装配"
        reason = self.database_reason()
        if reason is not None:
            return HEALTH_UNAVAILABLE, reason
        if self._failures:
            # 措辞必须自带"累计"与"不影响判定"两件事：这句话出现在一条 `healthy=True` 的报告里，
            # 少了任何一件都会被读成"此刻仍在坏"（见模块 docstring 的那节）。
            return (
                HEALTH_OK,
                (
                    f"评测链可用；自本进程启动以来累计 {self._failures} 次未预期依赖失败"
                    "（单调累计、仅诊断：不参与健康判定）"
                ),
            )
        return HEALTH_OK, "评测链可用（取包端点、工作区根与库结构版本均已就绪）"

    def broker_health(self) -> tuple[str, str]:
        """消息派发：**配置 + outbox 账本**，不含连通性。

        连通性不在同步探测内：`NatsEventBus` 的连接是惰性且异步的，同步健康探测只能报"配没配"与
        "outbox 积压/DLQ 多少"；真实连通性由派发器每次投递时的 `EventBusUnavailable` 如实反映
        （先写后发：broker 不可用只推迟投递，不改变已接受的事实）。DLQ 非空即 `unavailable`——
        那是"配了但没投通"的实证。
        """
        if self.outbox is None:
            status = (
                HEALTH_NOT_CONFIGURED if self.broker_not_configured else HEALTH_UNAVAILABLE
            )
            return status, self.broker_reason or "消息代理未配置（NATS_URL）"
        engine = self.intake.engine
        if engine is None:
            return HEALTH_UNAVAILABLE, "outbox 不可读（数据库未配置）"
        try:
            with engine.connect() as connection:
                pending = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(outbox_events)
                    .where(
                        sa.and_(
                            outbox_events.c.published_at.is_(None),
                            outbox_events.c.dead_lettered_at.is_(None),
                        )
                    )
                ).scalar_one()
                dead = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(outbox_events)
                    .where(outbox_events.c.dead_lettered_at.is_not(None))
                ).scalar_one()
        except SQLAlchemyError:
            return HEALTH_UNAVAILABLE, "outbox 不可读（数据库不可达或结构未初始化）"
        if dead:
            return HEALTH_UNAVAILABLE, f"消息派发有 {int(dead)} 条进入 DLQ，未发布 {int(pending)} 条"
        return HEALTH_OK, f"消息派发已装配（未发布 {int(pending)} 条，DLQ 0 条）"

    # --- worker 入口 -------------------------------------------------------------------

    def evaluate(self, submission_id: str) -> EvaluationOutcome:
        """从 `b2` 表重建冻结信封并推进一次评测（**HTTP 路径不调用它**）。

        `load_submission_envelope` 自带摘要自校验：库里的行被写坏时这里明确失败，而不是拿一份
        被改过的输入去跑评测。`SubmissionNotFound` / `SubmissionDigestMismatch` 原样上抛——
        它们是 worker 的调度问题，不是 HTTP 语义。
        """
        if self.evaluator is None:
            raise EvaluationUnavailable(self.evaluation_reason or "评测链未装配")
        reason = self.database_reason()
        if reason is not None:
            raise EvaluationUnavailable(reason)
        engine = self.intake.engine
        assert engine is not None  # database_reason() 通过即意味着引擎存在
        with engine.connect() as connection:
            envelope = load_submission_envelope(connection, submission_id)
        return self.evaluator.run(envelope)


def build_b2_runtime(settings_source: Mapping[str, str]) -> B2Runtime:
    """按环境装配；任何配置问题都转成**对应入口**的可读不可用原因，绝不抛给宿主。"""
    runtime = B2Runtime(intake=build_runtime(settings_source))
    _wire_database_services(runtime)
    _wire_commentary(runtime, settings_source)
    _wire_evaluation_chain(runtime)
    _wire_outbox_dispatcher(runtime)
    return runtime


def _wire_commentary(runtime: B2Runtime, settings_source: Mapping[str, str]) -> None:
    """装配评语轨：读 `B2_COMMENTARY_*`（与其它配置同一个来源映射）→ provider（**默认没有**）。

    关键点：没有配置时**不构造任何 provider 对象**。因此"未配置 ⇒ 没有出网调用"不是靠调用点的
    条件判断维持的，而是这个对象根本不存在——`tests/test_commentary_track.py` 用会抛异常的传输替身
    对这条做了断言。配置问题（缺项/非法）同样收敛成 `provider=None` + 可读原因，
    由 `GET` 的 `unavailable` 与 `POST` 的 `409` 表达。
    """
    engine = runtime.intake.engine
    if engine is None:
        return
    settings, reason = load_commentary_settings(settings_source)
    provider = None
    if settings is not None:
        provider = build_provider(
            CommentaryProviderConfig(
                provider=settings.provider,
                base_url=settings.base_url,
                model=settings.model,
                api_key=settings.api_key,
                timeout_seconds=settings.timeout_seconds,
            )
        )
    else:
        provider = None
    runtime.commentary = CommentaryService(
        engine=engine, provider=provider, not_configured_reason=reason
    )
    runtime.commentary_reason = reason


def _wire_database_services(runtime: B2Runtime) -> None:
    """成绩查询与导出、题目质量汇总（只依赖数据库，与评测链无关）。"""
    engine = runtime.intake.engine
    if engine is None:
        return
    runtime.grades = GradeQueryService(engine)
    runtime.exporter = GradesExportService(engine)
    runtime.quality = EvaluationQualityService(engine)


def _wire_evaluation_chain(runtime: B2Runtime) -> None:
    """装配评测链（取包端点 + 工作区 + 安全判定 + runner）+ 参考解答验证（`CHG-0019`）。

    安全判定与 runner 用的是**已交付**的生产实现（`security.decide_static` 与 `runner.run_case`
    的适配器）；这里不提供任何替身、也不通过环境变量切换执行后端。

    参考解答验证与评测链**共用**两样东西，`ADR-0015` 的"不新增凭据/机制"因此是可断言的事实：
    同一个 `B3Endpoint` 对象（取包、取参考解答、记录存证三处调用都从它取 `base_url` 与
    `authorization`），以及同一个 `DynamicExecutionGate`（单并发沙箱上限对两条路径一起生效）。
    """
    settings = runtime.intake.settings
    engine = runtime.intake.engine
    if settings is None or engine is None:
        runtime.evaluation_reason = runtime.intake.unavailable_reason or "配置不可读，评测链无法装配"
        runtime.references_reason = runtime.evaluation_reason
        return
    try:
        chain = evaluation_chain_settings(settings)
        workspaces = CasWorkspaceProvider(
            store=ContentAddressedStore(Path(settings.artifact_root), engine),
            workspace_root=chain.workspace_root,
        )
        endpoint = B3Endpoint(
            base_url=chain.b3_base_url, authorization=chain.b3_authorization
        )
        source = HttpPackageSource(endpoint, environment_tag=chain.environment_tag)
    except EvaluationChainNotConfigured as error:
        # 一个相关变量都没给：这是"有意的部署选择"，健康状态报 not-configured（但路由照旧 503）。
        # 参考解答验证要的是同一条 B3 通道与同一个工作区根，因此它同样不可用，原因同句。
        runtime.evaluation_reason = str(error)
        runtime.references_reason = str(error)
        runtime.evaluation_not_configured = True
        return
    except (ConfigurationError, ValueError) as error:
        # 半配置/非法配置，以及 `B3Endpoint` 与 `CasWorkspaceProvider` 用 `ValueError` 表达的
        # "非法装配"：都属于"配了却不可用"，不是可以继续的配置漂移。
        runtime.evaluation_reason = str(error)
        runtime.references_reason = str(error)
        return
    gate = DynamicExecutionGate()
    runtime.evaluator = EvaluationOrchestrator(
        engine=engine,
        package_source=source,
        workspace_provider=workspaces,
        security=StaticSecurityDecider(),
        runner=RunnerCaseRunner(),
        gate=gate,
    )
    runtime.references = ReferenceVerificationService(
        reference_answers=B3VerificationClient(endpoint),
        packages=source,
        runner=RunnerCaseRunner(),
        workspaces=ReferenceAnswerWorkspace(root=chain.workspace_root),
        gate=gate,
    )
    runtime.b3_endpoint = endpoint
    runtime.environment_tag = chain.environment_tag
    runtime.workspace_root = chain.workspace_root


def _wire_outbox_dispatcher(runtime: B2Runtime) -> None:
    """装配 outbox 派发器（只装配、不排程：何时派发是 worker/daemon 的事，本模块不埋定时器）。

    `NatsEventBus` 的连接是惰性的（`connect()` 显式调用），因此这里不会在装配期发起网络请求；
    broker 不可达在第一次投递时才变成 `EventBusUnavailable`，并由派发器按 I29/I33 记入 DLQ 账本。
    """
    settings = runtime.intake.settings
    engine = runtime.intake.engine
    if settings is None or engine is None:
        runtime.broker_reason = runtime.intake.unavailable_reason or "配置不可读，派发器无法装配"
        return
    if not settings.nats_url:
        runtime.broker_reason = "消息代理未配置（NATS_URL）"
        runtime.broker_not_configured = True
        return
    runtime.outbox = NatsOutboxDispatcher(
        engine=engine, bus=NatsEventBus(url=str(settings.nats_url))
    )
