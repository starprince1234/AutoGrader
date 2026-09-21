"""评测运行的三条读端点 + 一条 SSE 进度流（`b2.yaml` 的 `evaluation-runs` 路径族）。

契约权威：`MAIN/contracts/openapi/b2.yaml`。三条不可协商的性质，逐条对应到实现：

1. **只读已持久化的结果，绝不重算**。运行视图读 `b2.evaluation_runs` 行，报告读
   `b2.evaluation_results`（经 `EvaluationOrchestrator.read_report`，没有结果行就 `None`），
   SSE 读 `b2.progress_events`。本模块不重算分数、不重算报告、不重算安全结论——重算就等于产生
   第二个判定点，"报告可读 ⇔ 结果已持久化"这条不变量立刻失效。
2. **未完成的运行不给分数**。这不是靠"记得写 null"实现的：契约的 `EvaluationRunView`
   （`additionalProperties: false`）里**根本没有** `score`/`terminal_reason`/五项绑定摘要字段，
   本模块也就不写它们。分数与终态原因经 SSE 终态帧（`EvaluationStreamFrame.terminal_reason`）
   与 `report` 端点送达；五项绑定摘要只在报告里（契约 `binding`）。**若 B1 需要运行级分数或
   绑定摘要，那是契约变更（CHG），不是在这里加字段就能合规的。**
3. **越权是 403，不是 404**。存在性或作用域判定复用**已交付**的提交读服务
   （`intake.query.SubmissionQueryService.get`）：它把"不存在"映射成 404、"有权但越权"映射成 403，
   并同时完成角色 → 课程作用域 → 身份三层判定。这里不复制一份授权逻辑（两份判定必然漂移），
   代价是每次读多几次查询——用正确性换掉的那点开销是值得的。
4. **报告按角色裁剪**：`evaluation.report.for_student` / `for_teacher` 是唯一的裁剪实现处。
   请求参数 `view` 只是**请求**，服务端按调用者角色决定能否给教师视图：非教师/助教/管理员
   显式索要 `view=teacher` 一律 403（**不**静默降级成学生视图：静默降级会让一次越权探测看起来
   像一次正常请求）。学生视图不传 `visible_case_ids`，即 `for_student` 的 fail-closed 默认口径
   （未显式声明可见 ⇒ 隐藏）：B2 库里没有"用例是否对学生可见"这一列（它属于 B3 的 package
   声明，B2 不读 B3 的表），因此当前**任何**用例的期望/实际输出摘要都不给学生。

## SSE（`GET /api/v1/evaluation-runs/{id}/events`）

帧来源是**持久化的** `progress_events`（`evaluation.progress.frame_stream`）：服务端每
`poll_interval` 查一次 `sequence > cursor`，取到就产出、并把游标推到最后一帧的 `sequence`；
产出终态帧（`completed`/`rejected`/`failed`）后生成器结束。断线重连靠 `Last-Event-ID`
（标准头，值即上一帧的 `sequence`）：`frame_stream(after=<Last-Event-ID>)` 只取严格更大的序号，
因此**不重复、不遗漏**；帧的 `id:` 字段就是 `sequence`，浏览器 `EventSource` 会自动回传。

**多 worker 行为（如实回答）**：这是**每帧都查库**的实现，不是进程内 fan-out。因此在多 worker
部署下语义仍然正确（每个连接各自查库，任一 worker 都能看到任一 worker 写入的帧）。真实限制有两条：
①每个打开的连接每秒最多 4 次探测查询（`poll_interval=0.25`），连接数大时应当提高轮询间隔；
②`idle_timeout=30s`：30 秒内没有新帧时服务端主动结束流（客户端会带着最后一帧的 `sequence`
自动重连，因此不丢帧，但会看到一次重连）。这两个值都是 `progress` 模块的**默认参数**，本模块
不通过环境变量改它们（那会变成行为随部署变化的隐藏开关）。

帧内不额外拼任何字段：`as_contract_frame()` 的字段集就是契约 `EvaluationStreamFrame`，而 `progress`
的结构里没有承载用例输入、期望输出或源码的槽位——"帧里不会泄露隐藏用例"是结构性的，不是靠记得。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from autograder_b2.api.errors import (
    FORBIDDEN,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    Problem,
    problem_response,
)
from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import AuthenticationError, Principal
from autograder_b2.commentary.store import CommentaryRunNotTerminal, unavailable_body
from autograder_b2.domain.clock import utc_now
from autograder_b2.evaluation import ProgressFrame, RunRow, load_run
from autograder_b2.evaluation.appeal import APPEAL_VIEWS, AppealPackInputs, build_appeal_pack
from autograder_b2.evaluation.quality import (
    InvalidQualityFilterError,
    parse_question_version_id,
    parse_timestamp,
)
from autograder_b2.evaluation.report import for_student, for_teacher
from autograder_b2.intake.query import SubmissionNotFoundError
from autograder_b2.logging.logger import log_dependency_unavailable
from autograder_b2.runtime import B2Runtime

#: 路由层 logger（日志名即身份；`logger` 是白名单字段）。
logger = logging.getLogger("autograder_b2.autograder_b2.api.evaluation_routes")

#: 契约 `getEvaluationReport.view` 的取值域（默认 `student`）。
REPORT_VIEWS = ("student", "teacher")
#: 教师视图的判定角色（`Principal` 的既有判定）；管理员不受课程限制。
_TEACHER_VIEW_ROLES = ("teacher", "assistant")


def build_evaluation_router(runtime: B2Runtime) -> APIRouter:
    """评测路径族的路由；闭包持有 `runtime`（与 `intake.api.build_router` 同一形态）。

    **注册顺序是契约要求的一部分**：`/api/v1/evaluation-runs/quality-summary` 是集合下的**字面段**，
    而同一集合还有 `{evaluation_run_id}` 子路径。Starlette 按注册顺序匹配，因此字面路径必须**先**
    声明，否则 `quality-summary` 会被路径参数吞掉（表现为一个令人困惑的 404——`quality-summary`
    不是 UUID）。`CHG-0015` §3 把这条写进了契约，`test_quality_summary_is_not_swallowed_by_the_run_path_parameter`
    把它钉成测试。
    """
    router = APIRouter()

    @router.get("/api/v1/evaluation-runs/quality-summary")
    async def get_evaluation_quality_summary(
        request: Request, question_version_id: str | None = None
    ) -> Response:
        """题目质量汇总（`CHG-0015`）：**只有计数**的聚合，无任何可回溯到个人的标识。

        `from`/`to` 直接按**契约里的名字**从查询串读（`from` 是 Python 关键字，声明成形参会变成
        一个未声明的 `?from_=` 别名——那等于给契约加了一个隐式参数）；`question_version_id` 仍按
        形参声明，但**不加** FastAPI 的类型校验：非法取值的 422 由本模块统一产出（`Problem Details`）。
        """
        instance = request.url.path
        unavailable = runtime.quality_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation quality summary", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        try:
            question = parse_question_version_id(question_version_id)
            since = parse_timestamp(request.query_params.get("from"), "from")
            until = parse_timestamp(request.query_params.get("to"), "to")
        except InvalidQualityFilterError as error:
            # 契约未声明 422（它声明了 404，而 404 在这条路径上不可判定——见下），本模块按
            # `docs/api/contract.md` §4 的统一约定返回 422，并已把该缺口记入契约缺口一节。
            return problem_response(
                Problem(422, "unprocessable-entity", "Unprocessable Entity", str(error)), instance
            )

        assert runtime.quality is not None  # quality_unavailable() 通过即已装配
        try:
            summary = runtime.quality.summarize(
                principal=principal,
                question_version_id=question,
                since=since,
                until=until,
            )
        except AuthorizationError:
            return problem_response(FORBIDDEN, instance)
        except InvalidQualityFilterError as error:
            return problem_response(
                Problem(422, "unprocessable-entity", "Unprocessable Entity", str(error)), instance
            )
        except SQLAlchemyError as error:
            # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
            # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
            # 日志侧是统一的依赖故障事件。
            # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="evaluation quality summary"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)
        return JSONResponse(status_code=200, content=summary.as_contract_body())

    @router.get("/api/v1/evaluation-runs/{evaluation_run_id}")
    async def get_evaluation_run(evaluation_run_id: str, request: Request) -> Response:
        instance = request.url.path
        unavailable = runtime.evaluation_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation read", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        run = _load_run(runtime, evaluation_run_id)
        if run is None:
            return _not_found(instance)
        refused = _authorize_run(runtime, principal, run, instance)
        if refused is not None:
            return refused
        return JSONResponse(status_code=200, content=_run_view_body(run))

    @router.get("/api/v1/evaluation-runs/{evaluation_run_id}/events")
    async def stream_evaluation_events(evaluation_run_id: str, request: Request) -> Response:
        instance = request.url.path
        unavailable = runtime.evaluation_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation stream", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        run = _load_run(runtime, evaluation_run_id)
        if run is None:
            return _not_found(instance)
        refused = _authorize_run(runtime, principal, run, instance)
        if refused is not None:
            return refused

        after, malformed = _last_event_id(request)
        if malformed is not None:
            # 坏的重连游标必须明确拒绝：当成"从头开始"会把整段进度重放一遍，而调用方无从察觉。
            return problem_response(
                Problem(422, "invalid-last-event-id", "Unprocessable Entity", malformed), instance
            )

        assert runtime.evaluator is not None  # evaluation_unavailable() 通过即已装配
        return StreamingResponse(
            _sse_stream(runtime, evaluation_run_id, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/v1/evaluation-runs/{evaluation_run_id}/report")
    async def get_evaluation_report(
        evaluation_run_id: str, request: Request, view: str | None = None
    ) -> Response:
        instance = request.url.path
        unavailable = runtime.evaluation_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation report", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        requested = "student" if view is None or view == "" else view
        if requested not in REPORT_VIEWS:
            # 枚举校验手写而不是交给 FastAPI：宿主没有安装 Problem Details 处理器时，框架默认的
            # 422 响应体不是 RFC 9457（`api.errors` 的形状才是本仓库唯一对外的错误形态）。
            return problem_response(
                Problem(
                    422,
                    "unprocessable-entity",
                    "Unprocessable Entity",
                    f"view must be one of {', '.join(REPORT_VIEWS)}",
                ),
                instance,
            )

        run = _load_run(runtime, evaluation_run_id)
        if run is None:
            return _not_found(instance)
        refused = _authorize_run(runtime, principal, run, instance)
        if refused is not None:
            return refused

        if requested == "teacher" and not _may_read_teacher_view(principal):
            return problem_response(FORBIDDEN, instance)

        assert runtime.evaluator is not None
        stored = runtime.evaluator.read_report(evaluation_run_id)
        if stored is None:
            # 报告只在结果**已持久化**时存在（同一行、同一事务写入）。运行尚未完成、被拒绝或
            # 技术失败时都没有报告，也不当场重算一份出来。
            #
            # 但"运行不存在"与"运行在、只是还没有报告"必须能对用户**说清楚**：此前两者都是
            # "The evaluation run does not exist."，而教师明明在成绩单里看到这一行（例如
            # state=failed）。状态码仍按契约用 404（契约另声明了 409，是否该用它属契约侧问题，
            # 见 phase-01 缺口 G-API-1），这里只把 detail 改成如实描述。
            return _no_report(instance, run)
        body = for_teacher(stored) if requested == "teacher" else for_student(stored)
        return JSONResponse(status_code=200, content=body)

    @router.get("/api/v1/evaluation-runs/{evaluation_run_id}/appeal-pack")
    async def get_appeal_pack(
        evaluation_run_id: str, request: Request, view: str | None = None
    ) -> Response:
        """导出一个运行的**可移动证据包**（`CHG-0014`）：两处强制脱敏在 `evaluation.appeal`。

        与报告端点共用同一套判权与裁剪：`view` 只是**请求**，服务端按角色决定能否给教师视图；
        非教师/助教/管理员显式索要 `view=teacher` 一律 403（**不**静默降级成学生视图）。
        默认视图按契约是 `teacher`——因此学生必须显式要 `view=student`，否则他们的请求是
        "索要教师视图"，按同一条纪律被拒。
        """
        instance = request.url.path
        unavailable = runtime.evaluation_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation appeal pack", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        requested = "teacher" if view is None or view == "" else view
        if requested not in APPEAL_VIEWS:
            return problem_response(
                Problem(
                    422,
                    "unprocessable-entity",
                    "Unprocessable Entity",
                    f"view must be one of {', '.join(APPEAL_VIEWS)}",
                ),
                instance,
            )

        run = _load_run(runtime, evaluation_run_id)
        if run is None:
            return _not_found(instance)
        refused = _authorize_run(runtime, principal, run, instance)
        if refused is not None:
            return refused
        if requested == "teacher" and not _may_read_teacher_view(principal):
            return problem_response(FORBIDDEN, instance)

        assert runtime.evaluator is not None
        stored = runtime.evaluator.read_report(evaluation_run_id)
        if stored is None:
            # 证据包与报告同源（同一行、同一事务落库）：没有报告就没有包，也不当场造一份。
            # 契约另声明了 409；是否该用它属契约侧问题（与报告端点记的 G-API-1 同一类），
            # 因此这里与报告端点保持**同一个**状态码与说法，不自行发明第二套约定。
            return _no_report(instance, run)
        # 组装与自检都在 `build_appeal_pack`：报告裁剪（`view`）、宿主路径脱敏、契约自检失败都会
        # 抛异常——那是缺陷而不是客户端错误，因此**不吞**，由宿主记 500。
        body = build_appeal_pack(
            AppealPackInputs(
                evaluation_run_id=run.evaluation_run_id,
                report=stored,
                host_roots=_host_roots(runtime),
                view=requested,
                generated_at=utc_now(),
            )
        )
        return JSONResponse(status_code=200, content=body)

    @router.get("/api/v1/evaluation-runs/{evaluation_run_id}/commentary")
    async def get_evaluation_commentary(evaluation_run_id: str, request: Request) -> Response:
        """读教学评语（`RQ-T-20`）。未配置 provider 时 `status: unavailable`——**不是错误**。"""
        instance = request.url.path
        unavailable = runtime.commentary_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation commentary", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        run = _load_run(runtime, evaluation_run_id)
        if run is None:
            return _not_found(instance)
        refused = _authorize_run(runtime, principal, run, instance)
        if refused is not None:
            return refused

        assert runtime.commentary is not None  # commentary_unavailable() 通过即已装配
        if not runtime.commentary.configured:
            # 默认关闭：本部署不生成评语是**配置状态**，因此 200 + `unavailable`（契约明文要求）。
            return JSONResponse(
                status_code=200,
                content=unavailable_body(run.evaluation_run_id, runtime.commentary.disclaimers),
            )
        try:
            record = runtime.commentary.load(run.evaluation_run_id)
        except SQLAlchemyError as error:
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="evaluation commentary"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)
        if record is None:
            # 有 provider，但这条运行还没有评语。契约的 status 枚举里**没有**"从未生成"这一态
            # （`unavailable` 的定义是"没有配置 provider"），因此如实用 404 表达"还没有"，
            # 并在 detail 里说清楚——这与 `/report` 端点"运行存在但还没有报告"的口径一致。
            return _no_commentary(instance)
        return JSONResponse(
            status_code=200, content=record.as_contract(runtime.commentary.disclaimers)
        )

    @router.post("/api/v1/evaluation-runs/{evaluation_run_id}/commentary")
    async def request_evaluation_commentary(evaluation_run_id: str, request: Request) -> Response:
        """生成或重新生成教学评语（教师/助教专用；重新生成永远不改分数）。"""
        instance = request.url.path
        unavailable = runtime.commentary_unavailable()
        if unavailable is not None:
            return _not_available(instance, "evaluation commentary", unavailable)
        principal_or_response = _authenticate(runtime, request, instance)
        if isinstance(principal_or_response, Response):
            return principal_or_response
        principal = principal_or_response

        run = _load_run(runtime, evaluation_run_id)
        if run is None:
            return _not_found(instance)
        refused = _authorize_run(runtime, principal, run, instance)
        if refused is not None:
            return refused
        if not _may_generate_commentary(principal):
            # 生成评语是**编辑动作**（ADR-0009 决策 5：学生可见性是课程策略、默认关闭），
            # 因此"谁能请求生成"这一层就要求学生止步——不静默降级成"只能读"。
            return problem_response(FORBIDDEN, instance)

        assert runtime.commentary is not None
        if not runtime.commentary.configured:
            return problem_response(
                Problem(
                    409,
                    "conflict",
                    "Conflict",
                    f"commentary generation is not available: {runtime.commentary.not_configured_reason}",
                ),
                instance,
            )
        try:
            outcome = runtime.commentary.generate(run.evaluation_run_id, run_state=run.state)
        except CommentaryRunNotTerminal as error:
            return problem_response(
                Problem(409, "conflict", "Conflict", f"commentary cannot be generated yet: {error}"),
                instance,
            )
        except SQLAlchemyError as error:
            log_dependency_unavailable(
                logger, dependency="database", error=error, stage="evaluation commentary"
            )
            runtime.record_failure()
            return problem_response(SERVICE_UNAVAILABLE, instance)
        if not outcome.ok:
            # provider 失败：**评语轨自己失败**（该条标 failed），分数轨完全不受影响。
            # 状态码用契约声明的 503（"一个必需依赖不可用"），并指名原因——README 里的
            # `failed` 状态仍然可以通过随后的 GET 读到。
            return problem_response(
                Problem(
                    503,
                    "service-unavailable",
                    "Service Unavailable",
                    f"commentary generation failed: {outcome.failure}",
                ),
                instance,
            )
        return JSONResponse(
            status_code=202, content=outcome.record.as_contract(runtime.commentary.disclaimers)
        )

    return router


# --- 响应体 -------------------------------------------------------------------------------


def _run_view_body(run: RunRow) -> dict[str, Any]:
    """契约 `EvaluationRunView`（`additionalProperties: false`）：字段集合固定。

    `started_at` 只在非空时出现：契约里它**不在** required 里，且类型是 `Timestamp`（不可为 null），
    所以"还没开始"只能靠省略表达，不能用 `null` 冒充。`finished_at`/`cases_total` 契约允许 null。
    """
    body: dict[str, Any] = {
        "evaluation_run_id": run.evaluation_run_id,
        "submission_id": run.submission_id,
        "state": run.state,
        "stage": run.stage,
        "sequence": run.sequence,
        "cases_total": run.cases_total,
        "cases_done": run.cases_done,
        "finished_at": None if run.finished_at is None else rfc3339_millis(run.finished_at),
    }
    if run.started_at is not None:
        body["started_at"] = rfc3339_millis(run.started_at)
    return body


def encode_frame(frame: ProgressFrame) -> str:
    """一帧 SSE 文本：`id`/`event`/`data` 三行（`data` 是契约 `EvaluationStreamFrame`）。

    `data` 用紧凑 JSON（`json.dumps` 会把字符串里的换行转义掉，因此不会破坏 SSE 的"一帧多行"结构），
    不做任何字段增补——帧的字段集就是 `ProgressFrame.as_contract_frame()`。
    """
    payload = json.dumps(
        frame.as_contract_frame(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return f"id: {frame.sequence}\nevent: {frame.event}\ndata: {payload}\n\n"


def _sse_stream(runtime: B2Runtime, evaluation_run_id: str, after: int) -> Iterator[str]:
    """把 `frame_stream` 包成 SSE 文本流；库故障只结束本流，不伪造任何一帧。

    流已经开始（响应头已发）时无法再返回 Problem Details，因此库故障**结束流**并记一次失败计数：
    客户端带着最后一帧的 `sequence` 重连即可续读，重连时若依赖仍坏，会在**新**请求上拿到 503。
    """
    assert runtime.evaluator is not None
    try:
        for frame in runtime.evaluator.frame_stream(evaluation_run_id, after=after):
            yield encode_frame(frame)
    except SQLAlchemyError as error:
        # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
        # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
        # 日志侧是统一的依赖故障事件。
        # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
        log_dependency_unavailable(
            logger, dependency="database", error=error, stage="evaluation runs"
        )
        runtime.record_failure()
        return


def _last_event_id(request: Request) -> tuple[int, str | None]:
    """解析标准头 `Last-Event-ID` → `(上一帧序号, 错误说明)`；缺省为 `0`（从头读）。"""
    raw = request.headers.get("last-event-id")
    if raw is None or not raw.strip():
        return 0, None
    text = raw.strip()
    if not (text.isascii() and text.isdigit()):
        return 0, "Last-Event-ID must be a non-negative integer sequence"
    return int(text), None


# --- 认证与授权 ---------------------------------------------------------------------------


def _authenticate(runtime: B2Runtime, request: Request, instance: str) -> Principal | Response:
    """验签 → `Principal`，或 `401` 响应。"""
    authenticator = runtime.intake.authenticator
    assert authenticator is not None  # ready 为假时路由已 503
    try:
        return authenticator.authenticate(request.headers.get("authorization"))
    except AuthenticationError:
        return problem_response(UNAUTHORIZED, instance)


def _load_run(runtime: B2Runtime, evaluation_run_id: str) -> RunRow | None:
    """读运行行；标识不是 UUID 与"库里没有"同为 `None`（对外都是 404）。"""
    try:
        identifier = str(uuid.UUID(evaluation_run_id))
    except (AttributeError, TypeError, ValueError):
        return None
    engine = runtime.engine
    assert engine is not None
    with engine.connect() as connection:
        return load_run(connection, identifier)


def _authorize_run(
    runtime: B2Runtime, principal: Principal, run: RunRow, instance: str
) -> Response | None:
    """用**已交付**的提交读服务判权：越权 403、提交不存在 404；通过则 `None`。"""
    query = runtime.intake.query
    assert query is not None
    try:
        query.get(run.submission_id, principal=principal)
    except SubmissionNotFoundError:
        return _not_found(instance)
    except AuthorizationError:
        return problem_response(FORBIDDEN, instance)
    except SQLAlchemyError as error:
        # 依赖不可用**既有状态也有日志**（RQ-B-2-99 场景 2-99-D）：状态侧是 503 +
        # `record_failure()`（**只如实累计、不翻转健康判定**，理由见该方法 docstring）；
        # 日志侧是统一的依赖故障事件。
        # **不吞异常语义**：仍按契约返回 503，未预期异常照旧冒泡。
        log_dependency_unavailable(
            logger, dependency="database", error=error, stage="evaluation runs"
        )
        runtime.record_failure()
        return problem_response(SERVICE_UNAVAILABLE, instance)
    return None


def _may_read_teacher_view(principal: Principal) -> bool:
    """教师视图的资格：管理员，或持有教师/助教角色（课程作用域已由 `_authorize_run` 校验）。"""
    return principal.is_admin or bool(set(principal.roles) & set(_TEACHER_VIEW_ROLES))


def _host_roots(runtime: B2Runtime) -> tuple[str, ...]:
    """证据包脱敏要剔除的**宿主根**（部署事实，来自配置而不是猜测）。

    两个来源：工件根（CAS 所在）与工作区根（`CasWorkspaceProvider` 物化提交的目录）。评测链未装配
    时工作区根为 `None`——那不影响脱敏的正确性：`evaluation.appeal` 还会按"绝对路径形态"兜一层，
    只是少了那条"逐字前缀"判据（原因见 `evaluation.appeal` 的模块文档）。
    """
    settings = runtime.intake.settings
    roots: list[str] = []
    if settings is not None and settings.artifact_root:
        roots.append(str(settings.artifact_root))
    if runtime.workspace_root:
        roots.append(str(runtime.workspace_root))
    return tuple(roots)


def _may_generate_commentary(principal: Principal) -> bool:
    """生成评语的资格：与教师视图同一口径（教师/助教/管理员）。

    复用同一个判定而不是再写一份角色集合：两份判定必然漂移，而"谁能生成评语"与"谁能看教师视图"
    在本项目里是同一件事（评语是给教师看的编辑产物；学生的可见性由课程策略决定，不由请求决定）。
    """
    return _may_read_teacher_view(principal)


# --- 错误响应 -----------------------------------------------------------------------------


def _not_available(instance: str, what: str, reason: str) -> JSONResponse:
    return problem_response(
        Problem(
            503,
            "service-unavailable",
            "Service Unavailable",
            f"{what} is not available: {reason}",
        ),
        instance,
    )


def _not_found(instance: str) -> JSONResponse:
    return problem_response(
        Problem(404, "not-found", "Not Found", "The evaluation run does not exist."), instance
    )


def _no_report(instance: str, run: RunRow) -> JSONResponse:
    """运行**存在**但没有报告：如实说明当前状态与原因，而不是说它"不存在"。

    契约只把 404 定为"取不到报告"，因此状态码不变；变的只是 `detail` 要说真话——
    教师会在成绩单里看到这一行（`state` 见 `GradeRow`），被告知"运行不存在"会与所见矛盾。
    """
    reason = f"，终态原因 {run.terminal_reason}" if run.terminal_reason else ""
    return problem_response(
        Problem(
            404,
            "not-found",
            "Not Found",
            "The evaluation run exists but has no report yet"
            f" (state: {run.state}{reason}). A report exists only after a successful run.",
        ),
        instance,
    )


def _no_commentary(instance: str) -> JSONResponse:
    """运行存在、provider 已配置，但**还没有**生成过评语。

    契约的状态枚举里没有"从未生成"这一态（`unavailable` 的定义是"没有配置 provider"，
    `pending` 的定义是"已受理、未完成"），因此这里用契约已声明的 `404` 表达"还没有"，
    并明说这与"运行不存在"不是一回事——把"没有 provider"和"还没生成"混成一句，
    运维就无法判断该去配 provider 还是该去点生成。
    """
    return problem_response(
        Problem(
            404,
            "not-found",
            "Not Found",
            "No commentary has been generated for this evaluation run yet."
            " Generate it with POST on this path. This is not the same as a missing run.",
        ),
        instance,
    )
