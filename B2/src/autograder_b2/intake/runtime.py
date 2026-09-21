"""接收层的运行时装配：把部署配置解析成可用的服务，且**缺失即明确不可用**。

宿主（MAIN composition host）调用 `router()` 时只给一个 `ModuleContext`（settings 字典）。
配置不完整时**不让宿主启动失败**（其它模块仍应可用），但本模块的端点必须返回 `503` 并在
`health()` 中如实反映——宁可明确不可用，也不静默降级（例如"不验签也放行"）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from autograder_b2.auth.tokens import JwksAuthenticator
from autograder_b2.config import (
    ConfigurationError,
    Settings,
    load_imap_settings,
    load_jwks,
    settings_from_env,
)
from autograder_b2.db.engine import create_db_engine
from autograder_b2.db.schema import EXPECTED_SCHEMA_REVISION
from autograder_b2.intake.imap import EmailCheckpointStore, EmailIntakeService, QuarantineStore
from autograder_b2.intake.matching import MappingEmailMatcher
from autograder_b2.intake.quarantine import IntakeQuarantineService
from autograder_b2.intake.query import SubmissionQueryService
from autograder_b2.intake.service import IntakeLimits, SubmissionIntakeService
from autograder_b2.storage.cas import ContentAddressedStore


@dataclass
class IntakeRuntime:
    """惰性装配的接收运行时；`ready` 为假时端点返回 503 且原因可读（不含内部路径/端口）。"""

    settings: Settings | None = None
    authenticator: JwksAuthenticator | None = None
    service: SubmissionIntakeService | None = None
    query: SubmissionQueryService | None = None
    #: 隔离区复核（`CHG-0014`）：只依赖库与认证器，与邮件入口**是否配置无关**——
    #: 读到已写下的隔离记录不需要连邮箱（见 `intake.quarantine` 的"重跑哪些校验"一节）。
    quarantines: IntakeQuarantineService | None = None
    unavailable_reason: str | None = None
    engine: Engine | None = None
    email: EmailIntakeService | None = None
    email_unavailable_reason: str | None = None
    _failures: int = field(default=0, init=False)

    @property
    def ready(self) -> bool:
        return (
            self.settings is not None
            and self.authenticator is not None
            and self.service is not None
            and self.query is not None
            and self.quarantines is not None
            and self.unavailable_reason is None
        )

    def record_failure(self) -> None:
        """Record one **unexpected** endpoint dependency failure (no sensitive detail).

        It is diagnostic only: it never flips the readiness flag. A counter that only ever grows
        cannot be used to flip readiness — the instance would stay unhealthy forever after the
        first transient blip, and a permanently false alarm teaches operators to ignore the flag
        (see `autograder_b2.runtime`'s "失败计数与健康判定的关系" for the full ruling). Documented
        platform conditions (no sandbox on this host) are reported through the 503 detail and the
        dependency log instead of this counter.
        """
        self._failures += 1

    @property
    def failure_count(self) -> int:
        return self._failures

    def health(self) -> tuple[bool, str]:
        """就绪与说明：连通性**与库结构版本**都要成立；详情不含连接串与内部主机信息。

        只看 `SELECT 1` 是不够的：库连得上但迁移落后时，受权读请求会因为表不存在而 503/500，
        而 readiness 却报告健康——那是对运维的谎报（实测过：落后 3 个版本的库上健康返回 True）。
        """
        if not self.ready:
            return False, self.unavailable_reason or "intake runtime is not configured"
        assert self.engine is not None
        # 两项探测**分开**报原因：连不上与"连得上但没迁移"是不同故障，混成一句会误导运维。
        with self.engine.connect() as connection:
            try:
                connection.execute(text("SELECT 1"))
            except SQLAlchemyError:
                return False, "database is not reachable"
            try:
                revision = connection.execute(
                    text("SELECT version_num FROM b2.alembic_version_b2")
                ).scalar_one_or_none()
            except SQLAlchemyError:
                return False, "database schema is not initialised"
        if revision is None:
            return False, "database schema is not initialised"
        if str(revision) != EXPECTED_SCHEMA_REVISION:
            return False, "database schema revision does not match this build"
        if self._failures:
            # Same wording rule as `B2Runtime.evaluation_health`: this sentence rides a report whose
            # `healthy` flag is still true, so it must say "cumulative" and "does not affect
            # readiness" — otherwise it reads as "still failing right now".
            return True, (
                f"submission intake is available; {self._failures} unexpected dependency failure(s) "
                "since this process started (cumulative, diagnostic only: readiness is unaffected)"
            )
        return True, "submission intake is available"

    def email_health(self) -> tuple[bool, str]:
        """邮件入口的就绪状态（与 HTTP 入口**分开**报告）。

        为什么不并进 `health()`：HTTP 入口就绪与"邮箱配好了"是两件独立的事实，混成一句话会让
        运维在任一故障下都读不出到底哪个入口坏了；而邮件入口不可用**不**应该让 HTTP 入口报 503。
        """
        if self.email is not None:
            return True, f"email intake is available for mailbox {self.email.settings.mailbox}"
        if self.email_unavailable_reason:
            return False, f"email intake is not available: {self.email_unavailable_reason}"
        return False, "email intake is not configured"


def build_runtime(settings_source: Mapping[str, str]) -> IntakeRuntime:
    """按环境装配；任何配置问题都转成可读的不可用原因，而不是抛给宿主。"""
    runtime = IntakeRuntime()
    try:
        settings = settings_from_env(settings_source)
        jwks = load_jwks(settings_source)
    except ConfigurationError as error:
        runtime.unavailable_reason = str(error)
        return runtime

    engine = create_db_engine(settings.database_url)
    runtime.settings = settings
    runtime.engine = engine
    runtime.authenticator = JwksAuthenticator(
        jwks, issuer=settings.jwt_issuer, audience=settings.jwt_audience
    )
    limits = IntakeLimits(
        max_files=settings.max_files,
        max_file_bytes=settings.max_file_bytes,
        max_total_bytes=settings.max_total_bytes,
    )
    runtime.service = SubmissionIntakeService(
        engine, ContentAddressedStore(settings.artifact_root, engine), limits
    )
    runtime.query = SubmissionQueryService(engine)
    runtime.quarantines = IntakeQuarantineService(engine)
    _wire_email_intake(runtime, settings_source, engine)
    return runtime


def _wire_email_intake(
    runtime: IntakeRuntime,
    settings_source: Mapping[str, str],
    engine: Engine,
) -> None:
    """装配邮件入口；配置问题只让**邮件**入口不可用，绝不连累 HTTP 入口。

    "未配置"（无任何 `B2_IMAP_*`）与"配置错误"因此被分开呈现：前者是 `email_health()` 的
    "not configured"，后者带上具体缺失项。两种情况下都不存在"拿猜出来的参数去连邮箱"的路径。
    附件闸门的上限也不是另配的一份：`EmailIntakeService.limits` 直接取自
    `SubmissionIntakeService`，因此邮件路径与 API 路径共用同一组数字。
    """
    try:
        imap = load_imap_settings(settings_source)
        matcher = MappingEmailMatcher(imap.matching) if imap is not None else None
    except ConfigurationError as error:
        runtime.email_unavailable_reason = str(error)
        return
    if imap is None or matcher is None:
        return

    assert runtime.service is not None
    runtime.email = EmailIntakeService(
        engine=engine,
        settings=imap,
        intake=runtime.service,
        matcher=matcher,
        checkpoints=EmailCheckpointStore(engine),
        quarantines=QuarantineStore(engine),
    )
