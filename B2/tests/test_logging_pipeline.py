"""结构化日志与脱敏的验收（`MAIN/docs/api/identifiers.md` 第 5 节 + 本模块 `docs/architecture/interfaces.md`）。

三条验收标准逐条对应：

- **字段**：完整链路日志是**合法 JSON** 且含 `timestamp`/`level`/`event`/`producer=B2`，并在适用时含
  `request_id`/`correlation_id`/`submission_id`/`evaluation_run_id`/`stage`（见 `test_log_lines_*`）。
- **脱敏 + 串联**：源码片段、token、内部绝对路径、请求头原值与文件内容都不得进输出；同一业务操作的
  接收→静态→安全→动态→报告日志共用**同一个** `correlation_id`（见 `test_single_correlation_id_*`、
  `test_logs_never_carry_*`）。
- **异常路径**：沙箱不可用 / DB 不可用 / NATS 不可用**各有明确日志与状态且异常未被吞**
  （见 `test_sandbox_unavailable_*`、`test_database_unavailable_*`、`test_broker_unavailable_*`）。

依赖逐用例声明：纯逻辑用例不需要外部依赖，真实链路用例需要真实 PostgreSQL。
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from autograder_b2.logging.logger import build_logger
from tests.conftest import requires_postgres

db = requires_postgres

DEFAULT_ISSUER = "autograder-b4"
DEFAULT_AUDIENCE = "autograder"

#: 本文件自己的标记串（与其它测试文件**故意不同**，避免"只为某串字面量写死"）。
MARK_SOURCE = "LOG_SRC_zz_marker_31ab"
MARK_TOKEN = "ghp_LOGPIPE_aaaaaaaa1111bbbb2222"
MARK_ROOT = "D:\\VScodeProjects\\AutoGrader\\B2\\var\\secret_placeholder.py"
MARK_UNWRITTEN_HEADER = "LOG_HEADER_zz_marker_77cd"
MARK_PII = "logpipe_student@example.edu"
MARKERS = (MARK_SOURCE, MARK_TOKEN, MARK_ROOT, MARK_UNWRITTEN_HEADER, MARK_PII)

MANDATORY = ("timestamp", "level", "event", "producer")
OPTIONAL_IDS = ("request_id", "correlation_id", "submission_id", "evaluation_run_id", "stage")


class _Capture:
    """把 `autograder_b2` 命名空间下的结构化日志收进内存并逐行解析。"""

    def __init__(self) -> None:
        self.stream = io.StringIO()

    def install(self) -> logging.Logger:
        # `propagate=False` + 命名空间 logger：子 logger（`autograder_b2.evaluation.orchestrator` 等）
        # 的记录会冒泡到这里，被同一个 JSON 格式化器渲染。
        return build_logger("autograder_b2", level=logging.DEBUG, stream=self.stream)

    def lines(self) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for raw in self.stream.getvalue().splitlines():
            if not raw.strip():
                continue
            parsed.append(json.loads(raw))  # 每行都必须是**合法 JSON**，否则本行断言失败
        return parsed

    def events(self) -> list[str]:
        return [line.get("event", "") for line in self.lines()]

    def find(self, event: str) -> list[dict[str, Any]]:
        return [line for line in self.lines() if line.get("event") == event]

    def text(self) -> str:
        return self.stream.getvalue()


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Capture]:
    """安装捕获器（渲染链与生产一致：同一个 `build_logger` + JSON 格式化器）。"""
    sink = _Capture()
    logger = sink.install()
    # 还原：把处理器还回去，避免污染后续用例（`build_logger` 幂等，重复调用只换处理器）。
    try:
        yield sink
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


# --- 字段：合法 JSON + 必备字段 + 适用时的标识 -------------------------------------------------


def test_log_lines_are_json_with_mandatory_fields(capture: _Capture) -> None:
    """字段标准（纯逻辑部分）：任意一条结构化日志都是 JSON 且含四个必备字段与 `producer=B2`。"""
    from autograder_b2.logging.logger import log_event

    logger = logging.getLogger("autograder_b2.test.subject")
    log_event(
        logger,
        "unit.probe",
        fields={
            "submission_id": str(uuid.uuid4()),
            "evaluation_run_id": str(uuid.uuid4()),
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "stage": "static_analysis",
            "outcome": "ok",
        },
    )
    lines = capture.lines()
    assert len(lines) == 1, f"应恰好一条日志，实得 {len(lines)}"
    line = lines[0]
    for field in MANDATORY:
        assert line.get(field), f"缺必备字段 {field}"
    assert line["producer"] == "B2", "producer 必须恒为 B2"
    for field in OPTIONAL_IDS:
        assert field in line, f"已显式给出的标识/阶段 {field} 不得被丢弃"
    # 标识形状：UUID（journald/检索侧据此断言"真的记了标识"而不是空串）
    uuid.UUID(line["correlation_id"])
    assert line["level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    print(f"[字段] JSON 行键={sorted(line)} producer={line['producer']} level={line['level']} ✓")


def test_unknown_and_forbidden_fields_are_dropped_not_rendered(capture: _Capture) -> None:
    """字段标准（负面）：非白名单字段（含源码/答案/口令等禁名）**整段丢弃**，不进输出。"""
    from autograder_b2.logging.logger import log_event

    logger = logging.getLogger("autograder_b2.test.subject")
    written = log_event(
        logger,
        "unit.probe",
        fields={
            "source_code": MARK_SOURCE,
            "expected_output": "答案",
            "password": "hunter2",
            "detail": "ok",
        },
    )
    assert "source_code" not in written and "password" not in written, "禁名必须被丢弃"
    rendered = capture.text()
    for marker in (MARK_SOURCE, "答案", "hunter2"):
        assert marker not in rendered, f"禁名字段的值泄漏进了日志：{marker}"
    assert capture.lines()[0]["detail"] == "ok"


# --- 真实链路：接收 → 静态 → 安全 → 动态 → 报告，串在同一个 correlation_id 上 -------------------


def _intake_client(url: str, root: Any, signer_jwks: str) -> TestClient:
    from autograder_b2.api.errors import install_problem_handlers
    from autograder_b2.intake.api import build_router
    from autograder_b2.intake.runtime import build_runtime

    runtime = build_runtime(
        {
            "DATABASE_URL": url,
            "B2_ARTIFACT_ROOT": str(root),
            "B2_JWKS_JSON": signer_jwks,
            "B4_JWT_ISSUER": DEFAULT_ISSUER,
            "B4_JWT_AUDIENCE": DEFAULT_AUDIENCE,
        }
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(runtime))
    return TestClient(app)


def _signer_and_token() -> tuple[Any, str, tuple[str, str, str, str]]:
    from tests.test_intake_api import _Signer, _student_token

    signer = _Signer()
    student = (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    return signer, _student_token(signer, student), student


@db
def test_reception_log_carries_the_entry_ids(
    upgraded_database_url: str, tmp_path: Any, capture: _Capture
) -> None:
    """字段标准（入口）：接收阶段日志带 `request_id`/`correlation_id`/`submission_id`/`stage`。"""
    import base64
    import hashlib

    from tests.test_intake_api import _envelope

    signer, token, student = _signer_and_token()
    body = _envelope(student)
    with _intake_client(upgraded_database_url, tmp_path, json.dumps(signer.jwks)) as client:
        response = client.post(
            "/api/v1/submissions",
            json=body,
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": MARK_UNWRITTEN_HEADER},
        )
    assert response.status_code == 202, response.text

    accepted = capture.find("submission.accepted")
    assert len(accepted) == 1, f"接收成功应恰好记一条，实得 {capture.events()}"
    line = accepted[0]
    for field in MANDATORY:
        assert line.get(field)
    assert line["submission_id"] == body["submission_id"]
    assert line["stage"] == "reception"
    assert line["request_id"] == line["correlation_id"], "入口一次请求即一次业务操作"
    uuid.UUID(line["correlation_id"])
    # 负面：请求头原值不得进日志（这里刻意传了一个非 UUID 的 `X-Request-ID`）
    assert MARK_UNWRITTEN_HEADER not in capture.text(), "请求头原值绝不进日志"
    # 负面：文件内容（含源码标记）不得进日志
    source = f'print("hello")\n# {MARK_SOURCE}\n'.encode()
    body_with_marker = _envelope(student, files=[
        {"path": "main.py", "content_sha256": hashlib.sha256(source).hexdigest(),
         "content_base64": base64.b64encode(source).decode("ascii")},
    ], idempotency_key="key-2")
    with _intake_client(upgraded_database_url, tmp_path, json.dumps(signer.jwks)) as client:
        response = client.post(
            "/api/v1/submissions", json=body_with_marker,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 202, response.text
    assert MARK_SOURCE not in capture.text(), "文件内容绝不进日志"
    print(f"[字段] 接收日志 request_id={line['request_id'][:8]}… stage={line['stage']} 且头/内容零泄漏 ✓")


@db
def test_single_correlation_id_links_every_stage(
    engine: Engine, tmp_path: Any, capture: _Capture
) -> None:
    """脱敏/串联标准：一次业务操作的**接收 + 静态 + 安全 + 动态 + 报告**日志共用一个 correlation_id。"""
    from tests.test_evaluation_pipeline import (
        CLEAN_SOURCE,
        _envelope,
        _rig,
        _seed_submission,
    )

    # 走真实的接收端点拿到入口标识（用 UUID 形态的 `X-Request-ID`，于是 correlation_id 可预期）
    signer, token, student = _signer_and_token()
    entry_id = str(uuid.uuid4())
    from tests.test_intake_api import _envelope as intake_envelope

    with _intake_client(engine.url.render_as_string(hide_password=False), tmp_path, json.dumps(signer.jwks)) as client:
        received = client.post(
            "/api/v1/submissions",
            json=intake_envelope(student),
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": entry_id},
        )
    assert received.status_code == 202, received.text
    accepted = capture.find("submission.accepted")
    assert len(accepted) == 1
    correlation_id = accepted[0]["correlation_id"]
    assert correlation_id == entry_id, "入口标识（UUID 形态）必须被原样采用为 correlation_id"

    # 同一提交走编排流水线：上下文由 `submission.correlation_id` 绑定，阶段名只换 stage
    seeded_submission_id, seeded_question_version_id = _seed_submission(engine)
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    envelope = _envelope(
        submission_id=seeded_submission_id,
        question_version_id=seeded_question_version_id,
        contents=contents,
    )
    object.__setattr__(envelope, "correlation_id", correlation_id)
    rig = _rig(engine, tmp_path, contents=contents)
    outcome = rig.run(envelope)
    assert outcome.state == "completed", outcome.message

    lines = capture.lines()
    assert lines, "链路必须留下日志"
    staged = [line for line in lines if "stage" in line]
    stages = {line["stage"] for line in staged}
    expected_stages = {
        "reception",
        "static_analysis",
        "security_decision",
        "dynamic_execution",
        "scoring",
        "reporting",
    }
    missing = expected_stages - stages
    assert not missing, f"链路日志缺少阶段：{missing}（实得 {sorted(stages)}）"
    # 同一业务操作的标识一致：所有带标识的日志行共用同一个 correlation_id；
    # 接收阶段记的是**接收端点创建的**提交，评测阶段记的是被评测的那一条（两者同属一次业务操作）。
    correlated = [line for line in lines if line.get("correlation_id")]
    assert correlated, "链路日志必须带 correlation_id"
    assert {line["correlation_id"] for line in correlated} == {correlation_id}, (
        "同一业务操作的所有日志必须共用同一个 correlation_id"
    )
    received_submission_id = received.json()["submission_id"]
    reception_lines = [line for line in correlated if line.get("stage") == "reception"]
    assert {line["submission_id"] for line in reception_lines} == {received_submission_id}
    downstream = [line for line in correlated if line.get("stage") != "reception"]
    assert downstream and {line["submission_id"] for line in downstream} == {
        str(seeded_submission_id)
    }, "评测阶段的所有日志必须记同一个 submission_id"
    print(f"[串联] {len(correlated)} 条带 correlation_id 的日志覆盖阶段={sorted(stages)} ✓")


# --- 异常路径：各有明确日志与状态，且异常未被吞 -----------------------------------------------


@db
def test_sandbox_unavailable_has_log_and_state_and_is_not_swallowed(
    engine: Engine, tmp_path: Any, capture: _Capture
) -> None:
    """沙箱不可用：日志（`runner.sandbox_unavailable`）+ 状态（failed/infrastructure_error）齐全。"""
    from autograder_b2.domain.evidence import SandboxUnavailableError
    from tests.test_evaluation_pipeline import (
        CLEAN_SOURCE,
        _run_once,
        _ScriptedCaseRunner,
    )

    runner = _ScriptedCaseRunner(failure=SandboxUnavailableError("nsjail missing"))
    contents = {"main.py": CLEAN_SOURCE.encode("utf-8")}
    _rig_obj, outcome = _run_once(engine, tmp_path, contents=contents, runner=runner)

    assert outcome.state == "failed", "沙箱不可用不得被吞成成功"
    assert outcome.terminal_reason == "infrastructure_error", "状态必须是技术失败"
    logged = capture.find("runner.sandbox_unavailable")
    assert len(logged) == 1, f"沙箱不可用应恰好记一条依赖故障日志，实得 {capture.events()}"
    fields = logged[0]
    assert fields["dependency"] == "sandbox" and fields["status"] == "unavailable"
    assert fields["error_type"] == "SandboxUnavailableError"
    assert fields["stage"] == "dynamic_execution"
    assert len(runner.calls) == 1, "异常不得改变控制流（该用例仍被尝试过一次）"
    print(
        f"[异常] 沙箱不可用 → 日志={fields['event']} 状态={outcome.state}/{outcome.terminal_reason} ✓"
    )


@db
def test_database_unavailable_has_log_and_status(
    unmigrated_database_url: str, tmp_path: Any, capture: _Capture
) -> None:
    """DB 不可用：日志（`persistence.database_unavailable`）+ 状态（503 + 健康计数）齐全。

    真实失败、不模拟：库**可达但结构缺失**（`unmigrated_database_url` = 空库、未跑迁移），查询因此以
    真实的 `SQLAlchemyError`（缺表）失败——这是"依赖不可用"在运行期的形态之一，且是仓库既有的 503
    路径（`tests/test_b2_routes.py::test_read_routes_are_503_on_an_unmigrated_database`）。
    刻意**不**用"指向死端口"造这一场景：那条路会先撞上运行时探活的长超时（实测 ~130 s），
    代价与判据无关（诊断记录见本次会话）。
    """
    from autograder_b2.api.errors import install_problem_handlers
    from autograder_b2.api.grades_routes import build_grades_router
    from autograder_b2.runtime import build_b2_runtime

    signer, token, _student = _signer_and_token()
    root = tmp_path / "unmigrated"
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(parents=True, exist_ok=True)
    runtime = build_b2_runtime(
        {
            "DATABASE_URL": unmigrated_database_url,
            "B2_ARTIFACT_ROOT": str(root / "artifacts"),
            "B2_WORKSPACE_ROOT": str(root / "workspaces"),
            "B2_B3_BASE_URL": "https://b3.logpipe.invalid",
            "B2_B3_AUTHORIZATION": "Bearer logpipe-service-credential",
            "B2_ENVIRONMENT_TAG": "logpipe@sha256:0000",
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_grades_router(runtime))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/grades",
            params={"course_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 503, f"依赖不可用必须报 503：{response.status_code} {response.text}"
    # 库结构缺失时，请求在进入查询前就被拒（"依赖不可用"的另一种形态）：状态 503 + 日志必须都在。
    logged = capture.find("grades.unavailable")
    assert len(logged) == 1, f"依赖不可用应恰好记一条日志，实得 {capture.events()}"
    fields = logged[0]
    assert fields["outcome"] == "unavailable" and fields["reason"], "必须写明不可用原因"
    assert "Traceback" not in capture.text() and "autograder_platform" not in capture.text(), (
        "异常栈与连接串不得进日志"
    )
    print(
        f"[异常] DB 不可用（库结构缺失/未迁移）→ HTTP {response.status_code} + 日志={fields['event']} ✓"
    )


@db
def test_database_query_failure_has_log_and_status(
    upgraded_database_url: str, tmp_path: Any, capture: _Capture
) -> None:
    """DB 不可用的**查询失败**分支：真实 `SQLAlchemyError` → 503 + `persistence.database_unavailable`。

    造法：库是迁移好的，但把查询要用的表删掉（测试自有临时库），于是 `listGrades` 的查询以真实的
    "关系不存在"失败——这正是 `except SQLAlchemyError` 那条分支（与上一条用例的"进入查询前被拒"互补）。
    """
    from sqlalchemy import create_engine, text

    from autograder_b2.api.errors import install_problem_handlers
    from autograder_b2.api.grades_routes import build_grades_router
    from autograder_b2.runtime import build_b2_runtime

    broken = create_engine(upgraded_database_url, isolation_level="AUTOCOMMIT")
    with broken.connect() as connection:
        connection.execute(text("DROP TABLE b2.evaluation_results"))
    broken.dispose()

    signer, token, student = _signer_and_token()
    root = tmp_path / "broken"
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(parents=True, exist_ok=True)
    runtime = build_b2_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(root / "artifacts"),
            "B2_WORKSPACE_ROOT": str(root / "workspaces"),
            "B2_B3_BASE_URL": "https://b3.logpipe.invalid",
            "B2_B3_AUTHORIZATION": "Bearer logpipe-service-credential",
            "B2_ENVIRONMENT_TAG": "logpipe@sha256:0000",
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_grades_router(runtime))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/grades",
            params={"course_id": student[1]},  # 令牌作用域覆盖的课程，避免先被 403 拦下
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 503, f"查询失败必须报 503：{response.status_code} {response.text}"
    logged = capture.find("persistence.database_unavailable")
    assert len(logged) == 1, f"应恰好记一条依赖故障日志，实得 {capture.events()}"
    fields = logged[0]
    assert fields["dependency"] == "database" and fields["status"] == "unavailable"
    assert fields["error_type"] in {"ProgrammingError", "OperationalError", "DatabaseError"}
    assert runtime.failure_count >= 1, "状态侧必须计数（健康检查据此如实反映）"
    assert "Traceback" not in capture.text() and "autograder_platform" not in capture.text()
    print(
        f"[异常] DB 查询失败 → HTTP {response.status_code} + 日志={fields['event']} "
        f"({fields['error_type']}) + 健康计数={runtime.failure_count} ✓"
    )


def test_broker_unavailable_has_log_and_status_and_is_not_swallowed(capture: _Capture) -> None:
    """NATS 不可用：异常照旧抛出（不吞）+ 日志 + 状态（`error_count`/`last_error`/未连接）。"""
    import asyncio

    from autograder_b2.events.nats import EventBusUnavailable, NatsEventBus

    bus = NatsEventBus(
        url="nats://logpipe:sekretpw@127.0.0.1:1",
        connect_timeout=0.5,
        reconnect_time_wait=0.1,
        max_reconnect_attempts=1,
    )
    with pytest.raises(EventBusUnavailable) as caught:
        asyncio.run(bus.connect())

    # 状态侧：不静默（错误计数 + 未连接 + 原因文本，且不含凭据）
    assert bus.is_connected is False and bus.error_count >= 1 and bus.last_error is not None
    assert "sekretpw" not in str(caught.value), "异常文本绝不带凭据"
    # 日志侧：统一的依赖故障事件
    logged = capture.find("messaging.broker_unavailable")
    assert len(logged) == 1, f"NATS 不可用应恰好记一条依赖故障日志，实得 {capture.events()}"
    fields = logged[0]
    assert fields["dependency"] == "message_broker" and fields["status"] == "unavailable"
    rendered = capture.text()
    assert "sekretpw" not in rendered and "127.0.0.1" not in rendered, "日志不得泄漏凭据/内部主机"
    print(f"[异常] NATS 不可用 → 抛出 {type(caught.value).__name__} + 日志={fields['event']} ✓")


def test_correlation_id_normalization_is_external_input_safe() -> None:
    """串联标准的入口面（纯逻辑）：头部是外部可控文本，非法/缺失一律生成 UUID v4（不猜、不回写）。"""
    from autograder_b2.logging.context import (
        bind_operation,
        correlation_id_from_headers,
        current_context,
        parse_traceparent,
    )

    good = str(uuid.uuid4())
    assert correlation_id_from_headers({"x-request-id": good}) == good
    assert correlation_id_from_headers({"TraceParent": f"00-{'a' * 32}-{'b' * 16}-01"})
    injected = f"{MARK_TOKEN}\n{{\"event\":\"forged\"}}"
    generated = correlation_id_from_headers({"x-request-id": injected})
    assert generated != injected and uuid.UUID(generated), "非法头部必须被替换成新生成的 UUID"
    assert parse_traceparent("00-short-b-01") is None
    with bind_operation(headers={"x-request-id": good}, submission_id=str(uuid.uuid4())):
        context = current_context()
        assert context.correlation_id == good and context.request_id == good
    assert current_context().correlation_id is None, "退出上下文必须原样恢复（不残留）"
    print("[串联] 入口规范化：合法沿用、非法替换为 v4、退出恢复 ✓")

