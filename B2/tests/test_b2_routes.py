"""B2 六条读端点的路由测试（运行视图 / SSE / 报告 / 成绩 / 导出）。

真实 PostgreSQL（临时库迁移到**本构建声明的** `EXPECTED_SCHEMA_REVISION`）+ 真实 Ed25519 验签路径 +
真实路由装配（`B2Module.router`，与宿主一致）。评测数据的**写入**侧用真实编排器 + 明确标注的测试
替身（package/security/runner 三个端口；静态检查是真实实现），读取侧走生产装配的运行时——因此这些
用例证明的是"路由只读已持久化的结果"，而不是"路由会重算"。

覆盖：
1. 六条路由的 401 / 403（**越权必须是 403 而不是 404**）/ 503（依赖缺失，含库结构版本不符）；
2. 未完成的运行不给分数、不给报告；完成时分数与报告与库中持久化的结果逐字一致；
3. 报告的学生视图不含隐藏用例的期望输出，教师视图含；学生索要教师视图 → 403；
4. SSE 帧序单调、`Last-Event-ID` 重连不重复投递、终帧之后流结束、帧内无期望输出与源码；
5. 成绩游标分页边界、非法游标/非法过滤 → 422、越权 403、导出两种格式的表头与公式注入防线；
6. 成绩查询与导出**同口径**（分页全部行 vs XLSX/CSV 逐字段）、未完成运行 `score` 为 `null` 且带
   `state`（不是 0）、`offset` 不生效、导出字节里源码/参考答案/隐藏用例标记零命中。

**不重复已交付用例的断言**：公式注入转义本身（6 个触发字符与幂等性）由
`tests/test_grades_query_export.py` 覆盖；本文件断言的是**导出端点发出的字节与其渲染路径一致**
（见 `test_export_bytes_are_exactly_what_the_renderers_produce`），从而证明路由没有另造一套编码。
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import pathlib
import re
import uuid
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import Engine, text

from autograder_b2.api.errors import CONTENT_TYPE, install_problem_handlers
from autograder_b2.api.evaluation_routes import build_evaluation_router
from autograder_b2.api.grades_routes import build_grades_router
from autograder_b2.db.schema import (
    EXPECTED_SCHEMA_REVISION,
    evaluation_results,
    evaluation_runs,
    static_findings,
    submissions,
)
from autograder_b2.domain.envelope import ApiProvenance, EnvelopeFile, SubmissionEnvelope
from autograder_b2.domain.evidence import CaseEvidence, SubmissionRunInput
from autograder_b2.domain.security import SecurityDecision, SecurityFinding
from autograder_b2.evaluation import (
    CaseDescriptor,
    ComparisonPolicy,
    EvaluationOrchestrator,
    PackageView,
    advance_submission_state,
    append_frame,
    create_run,
    transition_run,
)
from autograder_b2.evaluation.appeal import HOST_PATH_PLACEHOLDER, SANDBOX_INTERNAL_ROOTS
from autograder_b2.evaluation.quality import QualitySummary
from autograder_b2.evaluation.report import report_digest
from autograder_b2.grades import (
    CSV_MEDIA_TYPE,
    ESCAPE_PREFIX,
    FORMULA_TRIGGERS,
    GRADE_EXPORT_COLUMNS,
    XLSX_MEDIA_TYPE,
    render_csv,
    render_xlsx,
)
from autograder_b2.plugin import B2Module
from autograder_b2.runner.policy import (
    DEFAULT_WORKSPACE_MOUNT,
    RunnerPolicy,
    policy_constraint_violations,
)
from autograder_b2.runtime import (
    HEALTH_NOT_CONFIGURED,
    HEALTH_OK,
    HEALTH_UNAVAILABLE,
    B2Runtime,
    build_b2_runtime,
)
from autograder_b2.static.rules import RULES
from tests.conftest import requires_postgres, run_alembic

pytestmark = requires_postgres

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT_OPENAPI = B2_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b2.yaml"

ISSUER = "autograder-b4"
AUDIENCE = "autograder"
#: 数据集基准时刻（UTC，毫秒精度）。
BASE = datetime(2026, 9, 16, 9, 0, 0, tzinfo=UTC)

#: 学生源码里的标记串：**任何** HTTP 响应（帧、导出、视图）里都不得出现。
SOURCE_MARKER = "SECRET_STUDENT_SOURCE_MARKER"
CLEAN_SOURCE = f'print("hello")\n# {SOURCE_MARKER}\n'
#: 隐藏用例的期望输出标记串：学生视图与 SSE 里不得出现，教师视图里必须出现。
HIDDEN_MARKER = "HIDDEN_EXPECTED_OUTPUT_MARKER"
VISIBLE_MARKER = "VISIBLE_EXPECTED_OUTPUT_MARKER"

#: 本文件**不写任何破坏性命令样本**（主机实时防护会删盘上的恶意样本）：安全阻断用测试替身构造。
_VISIBLE_CASE = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-routes-visible-case"))
_HIDDEN_CASE = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-routes-hidden-case"))

RUN_VIEW = "/api/v1/evaluation-runs/{run}"
RUN_EVENTS = "/api/v1/evaluation-runs/{run}/events"
RUN_REPORT = "/api/v1/evaluation-runs/{run}/report"
GRADES = "/api/v1/grades"
GRADES_EXPORT = "/api/v1/grades/export"

#: 本模块实现的 6 条读路径（`b2.yaml` 里除 `POST /api/v1/submissions` 外的全部）。
READ_PATHS = (RUN_VIEW, RUN_EVENTS, RUN_REPORT, GRADES, GRADES_EXPORT)
#: 需要 `{run}` 具体取值的路径（成绩两条路径不吃这个占位符）。
RUN_PATHS = (RUN_VIEW, RUN_EVENTS, RUN_REPORT)

_SCRATCH = pathlib.Path(".pytest-tmp")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- 令牌（真实 B4 形态的 Ed25519 签名，测试自己签发；不 import B4 代码） --------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _Signer:
    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()
        raw = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        self.kid = _b64url(hashlib.sha256(raw).digest())
        self.jwks = {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": self.kid,
                    "x": _b64url(raw),
                    "use": "sig",
                    "alg": "EdDSA",
                }
            ]
        }

    def token(self, *, user_id: str, roles: tuple[str, ...], scope: list[dict[str, str]]) -> str:
        import jwt

        issued = datetime.now(UTC).replace(microsecond=0)
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": user_id,
                "jti": str(uuid.uuid4()),
                "iat": int(issued.timestamp()),
                "nbf": int(issued.timestamp()),
                "exp": int((issued + timedelta(seconds=900)).timestamp()),
                "role": list(roles),
                "scope": scope,
            },
            self._key,
            algorithm="EdDSA",
            headers={"kid": self.kid},
        )


@dataclass(frozen=True)
class _World:
    """一次用例的标识世界（课程/两个课序/两个学生/教师/另一门课）。"""

    course: str
    section_a: str
    section_b: str
    student_a: str
    student_b: str
    teacher: str
    other_course: str
    other_section: str

    def student_token(self, signer: _Signer, student_id: str) -> str:
        return signer.token(
            user_id=student_id,
            roles=("student",),
            scope=[{"course_id": self.course, "section_id": self.section_a}],
        )

    def teacher_token(self, signer: _Signer) -> str:
        return signer.token(
            user_id=self.teacher, roles=("teacher",), scope=[{"course_id": self.course}]
        )

    def section_teacher_token(self, signer: _Signer) -> str:
        """只覆盖课序 A 的助教（用于验证"未指名课序不得扩大范围"）。"""
        return signer.token(
            user_id=str(uuid.uuid4()),
            roles=("assistant",),
            scope=[{"course_id": self.course, "section_id": self.section_a}],
        )

    def outsider_token(self, signer: _Signer) -> str:
        """另一门课的学生（作用域不覆盖本课程 → 403）。"""
        return signer.token(
            user_id=str(uuid.uuid4()),
            roles=("student",),
            scope=[{"course_id": self.other_course, "section_id": self.other_section}],
        )

    def admin_token(self, signer: _Signer) -> str:
        return signer.token(user_id=str(uuid.uuid4()), roles=("admin",), scope=[])


@pytest.fixture(scope="module")
def signer() -> _Signer:
    return _Signer()


@pytest.fixture()
def world() -> _World:
    return _World(
        course=str(uuid.uuid4()),
        section_a=str(uuid.uuid4()),
        section_b=str(uuid.uuid4()),
        student_a=str(uuid.uuid4()),
        student_b=str(uuid.uuid4()),
        teacher=str(uuid.uuid4()),
        other_course=str(uuid.uuid4()),
        other_section=str(uuid.uuid4()),
    )


class _Context:
    """最小宿主上下文（`ModuleContext` 只需要 `settings` 与 `health`）。"""

    def __init__(self, settings: Mapping[str, str]) -> None:
        self.settings = dict(settings)

    def health(self) -> Any:
        from autograder_platform import HealthReport

        return HealthReport("host", True, "test host")


# --- 数据库夹具：迁移到**本构建声明的** revision -------------------------------------------


@pytest.fixture(scope="module")
def b2_database_url(fresh_database_url: str) -> str:
    """把临时库迁移到 `EXPECTED_SCHEMA_REVISION`（本构建声明兼容的那个版本）。

    为什么**不是** `upgrade head`：运行时按 `EXPECTED_SCHEMA_REVISION` 判定"库结构版本是否与本
    构建相符"，端点在该版本不符时按契约返回 503。测试库必须停在这个构建期望的版本上，用例才能真的
    走到 200 路径；同时这个夹具不写死版本号——常量被推进时它自动跟随。
    """
    result = run_alembic(fresh_database_url, "upgrade", EXPECTED_SCHEMA_REVISION)
    assert result.returncode == 0, f"upgrade {EXPECTED_SCHEMA_REVISION} 失败: {result.stderr[-1500:]}"
    return fresh_database_url


@pytest.fixture()
def engine(b2_database_url: str) -> Iterator[Engine]:
    """每个用例前清空 `b2` 全部业务表（表清单从数据库派生，与共享 `engine` 夹具同一套隔离逻辑）。"""
    instance = sa.create_engine(b2_database_url)
    try:
        with instance.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'b2' AND table_type = 'BASE TABLE'"
                    "   AND table_name <> 'alembic_version_b2'"
                )
            ).fetchall()
            tables = ", ".join(f'b2."{row[0]}"' for row in rows)
            if tables:
                connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        yield instance
    finally:
        instance.dispose()


@pytest.fixture(scope="module")
def settings_source(b2_database_url: str, tmp_path_factory: pytest.TempPathFactory,
                    signer: _Signer) -> dict[str, str]:
    """完整可用的配置（工作区根与工件根是**两棵不同**的树）。"""
    root = tmp_path_factory.mktemp("b2-routes")
    return {
        "DATABASE_URL": b2_database_url,
        "B2_ARTIFACT_ROOT": str(root / "artifacts"),
        "B2_WORKSPACE_ROOT": str(root / "workspaces"),
        "B2_B3_BASE_URL": "https://b3.test.invalid",
        "B2_B3_AUTHORIZATION": "Bearer test-service-credential",
        "B2_ENVIRONMENT_TAG": "test-image@sha256:0000",
        "B2_JWKS_JSON": json.dumps(signer.jwks),
    }


@pytest.fixture(scope="module")
def context(settings_source: dict[str, str]) -> _Context:
    return _Context(settings_source)


@pytest.fixture(scope="module")
def module(context: _Context) -> B2Module:
    """模块级实例：与宿主一样，装配一次并按引用复用。"""
    return B2Module()


@pytest.fixture(scope="module")
def runtime(settings_source: dict[str, str]) -> B2Runtime:
    """供运行时级断言（健康分项、装配原因）使用；与路由服务的实例同配置、同库。"""
    return build_b2_runtime(dict(settings_source))


@pytest.fixture()
def client(module: B2Module, context: _Context, engine: Engine) -> Iterator[TestClient]:
    """走**与宿主相同**的注册路径（`B2Module.router`），因此六条路由都被真的注册了。"""
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(module.router(context))
    with TestClient(app) as test_client:
        yield test_client


def _client_for(settings: Mapping[str, str]) -> tuple[TestClient, B2Runtime]:
    """按给定配置显式装配一个运行时并注册路由（用于"依赖缺失"这类配置变体）。"""
    runtime = build_b2_runtime(dict(settings))
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_evaluation_router(runtime))
    app.include_router(build_grades_router(runtime))
    return TestClient(app), runtime


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- 提交行与信封（按库里真实的列填充，兼容迁移前后两种 `submissions` 形状） ----------------


def _column_types(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'b2' AND table_name = :table"
            ),
            {"table": table},
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def _seed_submission(
    engine: Engine,
    *,
    student_id: str,
    course_id: str,
    section_id: str,
    state: str = "queued",
    digest: str | None = None,
) -> tuple[str, str]:
    """写入一条提交行，返回 `(submission_id, question_version_id)`。

    `intake='api'` 时 provenance 五列必须全为 NULL、`request_id` 非空（迁移 0008 的约束），因此
    这里按库里**真实的列**填充；0008 之前的库上这些列不存在，跳过即可（两种形状都是真实合规的行）。
    """
    identifier = uuid.uuid4()
    question = str(uuid.uuid4())
    values: dict[str, object] = {
        "submission_id": identifier,
        "student_id": uuid.UUID(student_id),
        "course_id": uuid.UUID(course_id),
        "section_id": uuid.UUID(section_id),
        "question_version_id": uuid.UUID(question),
        "intake": "api",
        "idempotency_key": f"key-{identifier}",
        "idempotency_scope": "b2-routes-test",
        "submission_digest": digest or _digest(f"submission:{identifier}"),
        "state": state,
        "created_at": BASE,
        "updated_at": BASE,
    }
    column_types = _column_types(engine, "submissions")
    for name in ("correlation_id", "request_id"):
        if name in column_types:
            values[name] = identifier if column_types[name] == "uuid" else str(identifier)
    for name in ("mailbox", "uidvalidity", "uid", "message_id"):
        if name in column_types:
            values[name] = None
    with engine.begin() as connection:
        connection.execute(submissions.insert().values(**values))
    return str(identifier), question


# --- 编排替身（只在**写入**侧：package / security / runner 三个端口；静态检查是真的） --------


@dataclass(frozen=True, slots=True)
class _PackageCase:
    """不透明句柄的替身（编排层只把它原样传回 runner）。"""

    case_id: str
    ordinal: int
    weight: float
    visible_to_student: bool


class _PackageSource:
    def __init__(self, view: PackageView | None = None, error: Exception | None = None) -> None:
        self.view = view
        self.error = error

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        if self.error is not None:
            raise self.error
        assert self.view is not None
        return self.view


class _Security:
    """替身：回放给定结论（生产实现是 `security.decide_static` 的适配器）。"""

    def __init__(self, decision: SecurityDecision) -> None:
        self.decision = decision

    def decide(
        self, *, evaluation_run_id: str, files: Mapping[str, str], language: str
    ) -> SecurityDecision:
        return self.decision


class _Runner:
    """替身：按用例返回证据（隐藏用例带期望输出标记串，供学生视图裁剪断言使用）。"""

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        case = submission.case
        case_id = str(case.case_id)  # type: ignore[attr-defined] - 真实/替身用例对象都带该属性
        hidden = case_id == _HIDDEN_CASE
        return CaseEvidence(
            case_id=case_id,
            ordinal=int(case.ordinal),  # type: ignore[attr-defined]
            weight=float(case.weight),  # type: ignore[attr-defined]
            passed=not hidden,
            failure_category="output_mismatch" if hidden else None,
            input_digest=_digest(f"input:{case_id}"),
            stdout_digest=_digest(f"stdout:{case_id}"),
            stderr_digest=_digest(""),
            actual_output_excerpt="HIDDEN_ACTUAL" if hidden else "VISIBLE_ACTUAL",
            expected_output_excerpt=HIDDEN_MARKER if hidden else VISIBLE_MARKER,
            exit_code=1 if hidden else 0,
            duration_ms=7,
            resource_limit_hit=None,
            sandbox_violations=(),
            message="输出与期望不符（测试构造）。" if hidden else None,
            evidence_digest=_digest(f"evidence:{case_id}"),
        )


class _Workspaces:
    """本地工作区（生产实现从内容寻址存储物化；编排层只依赖 `prepare`/`release`）。"""

    def __init__(self, root: pathlib.Path, contents: Mapping[str, bytes]) -> None:
        self.root = root
        self.contents = dict(contents)
        self.prepared: list[pathlib.Path] = []

    def prepare(
        self,
        *,
        submission_id: str,
        evaluation_run_id: str,
        files: Sequence[EnvelopeFile],
        case_id: str | None = None,
    ) -> pathlib.Path:
        scope = "run" if case_id is None else f"case-{case_id[:8]}"
        workspace = self.root / f"ws-{evaluation_run_id[:8]}-{scope}-{len(self.prepared)}"
        workspace.mkdir(parents=True, exist_ok=False)
        for item in files:
            target = workspace / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.contents[item.path])
        self.prepared.append(workspace)
        return workspace

    def release(self, workspace: pathlib.Path) -> None:
        return None


def _clean_decision() -> SecurityDecision:
    return SecurityDecision(
        status="not_found",
        findings=(),
        blocking=False,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="必需检查项全部执行完毕，未发现违规模式。",
    )


def _blocking_decision() -> SecurityDecision:
    return SecurityDecision(
        status="found",
        findings=(
            SecurityFinding(
                finding_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-routes-security-finding")),
                category="destructive_command",
                severity="high",
                detected_in="static",
                message="检出破坏性命令模式（测试替身构造，不代表安全规则真实有效）。",
                rule_id="SEC-TEST-001",
            ),
        ),
        blocking=True,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="检查发现 1 项违规模式（其中 high 1 项）。已阻断动态执行。",
    )


def _package_view() -> PackageView:
    return PackageView(
        package_digest=_digest("package"),
        language="python",
        cases=(
            CaseDescriptor(
                case_id=_VISIBLE_CASE,
                ordinal=0,
                weight=6.0,
                visible_to_student=True,
                handle=_PackageCase(_VISIBLE_CASE, 0, 6.0, True),
            ),
            CaseDescriptor(
                case_id=_HIDDEN_CASE,
                ordinal=1,
                weight=4.0,
                visible_to_student=False,
                handle=_PackageCase(_HIDDEN_CASE, 1, 4.0, False),
            ),
        ),
        runner_policy_digest=_digest("policy"),
        environment_digest=_digest("environment"),
        comparison=ComparisonPolicy(stdout="exact"),
        question_version_binding=_digest("question-version"),
        interpreter_version="CPython 3.12",
        handle={"package": "test-handle"},
        adapter_handle={"adapter": "test-adapter"},
        policy_handle={"policy": "test-policy"},
    )


def _envelope(
    *,
    submission_id: str,
    question_version_id: str,
    student_id: str,
    course_id: str,
    section_id: str,
) -> SubmissionEnvelope:
    content = CLEAN_SOURCE.encode("utf-8")
    return SubmissionEnvelope(
        submission_id=submission_id,
        student_id=student_id,
        course_id=course_id,
        section_id=section_id,
        question_version_id=question_version_id,
        idempotency_key=f"key-{submission_id}",
        intake="api",
        provenance=ApiProvenance(request_id=str(uuid.uuid4())),
        correlation_id=str(uuid.uuid4()),
        files=(
            EnvelopeFile(
                path="main.py",
                content_sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                storage_ref=f"sha256/{hashlib.sha256(content).hexdigest()}",
            ),
        ),
        submission_digest=_digest(f"envelope:{submission_id}"),
        received_at=BASE,
    )


def _orchestrator(
    engine: Engine,
    tmp_path: pathlib.Path,
    *,
    source: _PackageSource | None = None,
    decision: SecurityDecision | None = None,
) -> EvaluationOrchestrator:
    """真实编排器 + 明确标注的替身端口（静态检查是真实实现，跑在真实临时工作区上）。"""
    workspace_root = tmp_path / "orchestrator-workspaces"
    workspace_root.mkdir(exist_ok=True)
    return EvaluationOrchestrator(
        engine=engine,
        package_source=source or _PackageSource(_package_view()),
        workspace_provider=_Workspaces(workspace_root, {"main.py": CLEAN_SOURCE.encode("utf-8")}),
        security=_Security(decision or _clean_decision()),
        runner=_Runner(),
    )


def _run_to_terminal(
    engine: Engine,
    tmp_path: pathlib.Path,
    world: _World,
    *,
    student_id: str | None = None,
    section_id: str | None = None,
    source: _PackageSource | None = None,
    decision: SecurityDecision | None = None,
) -> tuple[str, str, Any]:
    """建提交行 → 跑完整编排 → 返回 `(evaluation_run_id, submission_id, outcome)`。"""
    student = student_id or world.student_a
    section = section_id or world.section_a
    submission_id, question = _seed_submission(
        engine, student_id=student, course_id=world.course, section_id=section
    )
    envelope = _envelope(
        submission_id=submission_id,
        question_version_id=question,
        student_id=student,
        course_id=world.course,
        section_id=section,
    )
    outcome = _orchestrator(engine, tmp_path, source=source, decision=decision).run(envelope)
    return outcome.evaluation_run_id, submission_id, outcome


def _queued_run(engine: Engine, world: _World) -> tuple[str, str]:
    """只创建运行实体（`queued`）与一帧进度：**没有**结果行、没有终态。"""
    submission_id, question = _seed_submission(
        engine, student_id=world.student_a, course_id=world.course, section_id=world.section_a
    )
    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=submission_id,
            question_version_id=question,
            submission_digest=_digest(f"submission:{submission_id}"),
            occurred_at=BASE,
        )
        append_frame(
            connection,
            evaluation_run_id=run_id,
            event="progress",
            state="queued",
            stage="queued",
            occurred_at=BASE,
            cases_done=0,
            message="已接收提交，进入评测队列。",
        )
    return run_id, submission_id


def _run_row(engine: Engine, run_id: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(
                evaluation_runs.c.evaluation_run_id,
                evaluation_runs.c.submission_id,
                evaluation_runs.c.state,
                evaluation_runs.c.stage,
                evaluation_runs.c.sequence,
                evaluation_runs.c.cases_total,
                evaluation_runs.c.cases_done,
                evaluation_runs.c.terminal_reason,
                evaluation_runs.c.started_at,
                evaluation_runs.c.finished_at,
            ).where(evaluation_runs.c.evaluation_run_id == uuid.UUID(run_id))
        ).fetchone()


def _result_row(engine: Engine, run_id: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(evaluation_results).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()


def _assert_problem(response: Any, status: int) -> dict[str, Any]:
    """错误体一律 RFC 9457 Problem Details（形状固定，`additionalProperties: false`）。"""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    body = response.json()
    assert set(body) == {"type", "title", "status", "detail", "instance"}
    assert body["status"] == status
    serialized = json.dumps(body, ensure_ascii=False)
    for leaked in ("Traceback", "SELECT ", "psycopg", "sqlalchemy", "D:\\", "C:\\", "/home/"):
        assert leaked not in serialized
    return body


def _sse_frames(body: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append(
            {"id": int(fields["id"]), "event": fields["event"], "data": json.loads(fields["data"])}
        )
    return frames


def _read_csv(payload: bytes) -> list[list[str]]:
    import csv

    text_body = payload.decode("utf-8").lstrip("\ufeff")
    return [row for row in csv.reader(io.StringIO(text_body, newline=""))]


# --- 1. 注册的路径与契约逐条一致 -----------------------------------------------------------


#: 契约已声明、但**本模块尚未实现**的端点。
#:
#: CHG-0014 与 CHG-0015 把四条路径写进契约（隔离区复核两条 + 申诉证据包一条 + 题目质量汇总一条），
#: 它们曾长期留在这里，让"契约声明 vs 实现"的差距**显式且逐条列名**（这正是 CHG-0005 当年暴露的
#: 那类缺口）。四条已于 2026-09-20 实现（见 `docs/phases/active/phase-05-quarantine-and-quality-surfaces.md`），
#: 因此这个集合现在是**空集**——断言保留：差集一旦不为空（契约新增了没人实现的端点）立刻失败。
_PENDING_CONTRACT_PATHS: set[str] = set()


def test_registered_paths_match_the_contract(module: B2Module, context: _Context) -> None:
    """注册的路径/方法与 `b2.yaml` 逐条一致（本模块实现的那部分）。

    路径集合取自 `app.openapi()`：本仓库的 FastAPI/Starlette 版本把被包含的路由保留为惰性的
    `_IncludedRouter` 条目（`app.routes` 里看不到具体路径），而 OpenAPI 文档是真从路由树生成的，
    因此它才是"到底注册了哪些路径"的权威读数。

    **契约已声明但尚未实现的端点**（`_PENDING_CONTRACT_PATHS`）必须逐条等值：多一个少一个都会失败，
    因此"差在哪"永远是可见的，不会退化成"只要实现是契约的子集就算过"。
    """
    app = FastAPI()
    app.include_router(module.router(context))
    described = {
        path: _operations(item) for path, item in app.openapi()["paths"].items()
    }
    contract = yaml.safe_load(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    expected = {path: _operations(item) for path, item in contract["paths"].items()}

    assert set(expected) - set(described) == _PENDING_CONTRACT_PATHS, (
        "契约声明与实现之间的差集发生了变化",
        sorted(set(expected) - set(described)),
    )
    assert set(described) <= set(expected), sorted(set(described) - set(expected))
    for path, methods in described.items():
        assert methods <= expected[path], (path, methods, expected[path])
    for path, methods in expected.items():
        if path in _PENDING_CONTRACT_PATHS:
            continue
        assert methods <= described[path], (path, methods, described[path])


_HTTP_VERBS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _operations(path_item: Mapping[str, Any]) -> set[str]:
    """路径项里的 HTTP 方法集合（跳过 `parameters` 这类非方法键）。"""
    return {key.upper() for key in path_item if key.lower() in _HTTP_VERBS}


# --- 2. 未认证 401 / 越权 403 / 依赖缺失 503（六条路由逐一） --------------------------------


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_routes_reject_unauthenticated_requests(
    client: TestClient, world: _World, path: str
) -> None:
    response = client.get(path.format(run=str(uuid.uuid4())), params={"course_id": world.course})
    _assert_problem(response, 401)


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_routes_forbid_a_foreign_scope(
    client: TestClient,
    signer: _Signer,
    world: _World,
    engine: Engine,
    tmp_path: pathlib.Path,
    path: str,
) -> None:
    """越权必须是 **403 而不是 404**（本仓库明确不用 404 掩盖存在性）。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    # 评测三条路径：同课程的另一名学生读他人运行；成绩两条路径：另一门课的令牌（作用域不覆盖）。
    token = (
        world.student_token(signer, world.student_b)
        if path in RUN_PATHS
        else world.outsider_token(signer)
    )
    response = client.get(
        path.format(run=run_id), params={"course_id": world.course}, headers=_auth(token)
    )
    _assert_problem(response, 403)


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_routes_are_503_on_an_unmigrated_database(
    signer: _Signer, world: _World, unmigrated_database_url: str, path: str
) -> None:
    """库连得上但**结构未初始化/版本不符** ⇒ 503 + 可读原因（不是 500、也不是静默继续）。"""
    client, _ = _client_for(
        {
            "DATABASE_URL": unmigrated_database_url,
            "B2_ARTIFACT_ROOT": str(_SCRATCH / "unmigrated-artifacts"),
            "B2_WORKSPACE_ROOT": str(_SCRATCH / "unmigrated-workspaces"),
            "B2_B3_BASE_URL": "https://b3.test.invalid",
            "B2_B3_AUTHORIZATION": "Bearer test-service-credential",
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with client:
        response = client.get(
            path.format(run=str(uuid.uuid4())),
            params={"course_id": world.course},
            headers=_auth(world.admin_token(signer)),
        )
    body = _assert_problem(response, 503)
    assert "未初始化" in body["detail"] or "结构版本" in body["detail"], body["detail"]


def test_evaluation_routes_are_503_without_the_chain_but_grades_still_work(
    signer: _Signer, world: _World, b2_database_url: str, engine: Engine
) -> None:
    """评测链缺失只让评测路由 503；成绩路由不受影响（不把两件事的可用性绑成一件）。"""
    client, runtime = _client_for(
        {
            "DATABASE_URL": b2_database_url,
            "B2_ARTIFACT_ROOT": str(_SCRATCH / "no-chain-artifacts"),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with client:
        blocked = client.get(
            RUN_VIEW.format(run=str(uuid.uuid4())), headers=_auth(world.admin_token(signer))
        )
        body = _assert_problem(blocked, 503)
        assert "B2_B3_BASE_URL" in body["detail"] and "B2_WORKSPACE_ROOT" in body["detail"]
        grades = client.get(
            GRADES, params={"course_id": world.course}, headers=_auth(world.teacher_token(signer))
        )
    assert grades.status_code == 200, grades.text
    assert runtime.evaluator is None and runtime.grades is not None
    # 未配置 ≠ 不健康，也 ≠ 能服务：健康标志为真，但评测路由仍然 503。
    assert runtime.evaluation_not_configured is True
    assert runtime.health_summary()[0] is True


def test_nested_workspace_root_is_not_silently_accepted(
    signer: _Signer, world: _World, b2_database_url: str, engine: Engine
) -> None:
    """工作区根与工件根互相包含 ⇒ 装配期给出**可读原因**并让评测路由 503（不静默继续）。"""
    artifact_root = _SCRATCH / "nested-artifacts"
    client, runtime = _client_for(
        {
            "DATABASE_URL": b2_database_url,
            "B2_ARTIFACT_ROOT": str(artifact_root),
            "B2_WORKSPACE_ROOT": str(artifact_root / "workspaces"),
            "B2_B3_BASE_URL": "https://b3.test.invalid",
            "B2_B3_AUTHORIZATION": "Bearer test-service-credential",
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with client:
        response = client.get(
            RUN_VIEW.format(run=str(uuid.uuid4())), headers=_auth(world.admin_token(signer))
        )
    body = _assert_problem(response, 503)
    assert "B2_ARTIFACT_ROOT" in body["detail"], body["detail"]
    assert runtime.evaluator is None


def test_runtime_never_leaks_the_service_credential(
    runtime: B2Runtime, context: _Context
) -> None:
    """凭据与内部地址不得出现在 settings 的 repr 或任何健康说明里。"""
    credential = context.settings["B2_B3_AUTHORIZATION"]
    assert credential not in repr(runtime.intake.settings)
    _, detail = runtime.health_summary()
    assert credential not in detail
    assert context.settings["B2_B3_BASE_URL"] not in detail


# --- 3. 运行视图：未完成不给分数、完成时与库一致 --------------------------------------------


def test_unknown_run_is_404(client: TestClient, signer: _Signer) -> None:
    for identifier in (str(uuid.uuid4()), "not-a-uuid"):
        response = client.get(
            RUN_VIEW.format(run=identifier), headers=_auth(_admin(signer))
        )
        _assert_problem(response, 404)


def test_queued_run_view_has_no_score_and_no_report(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """未完成的运行：视图不含分数（契约里根本没有这个字段），报告 404。"""
    run_id, _ = _queued_run(engine, world)
    token = _auth(world.student_token(signer, world.student_a))

    response = client.get(RUN_VIEW.format(run=run_id), headers=token)
    assert response.status_code == 200, response.text
    body = response.json()
    # 契约 `EvaluationRunView` 的字段集合（additionalProperties: false）。
    assert set(body) == {
        "evaluation_run_id",
        "submission_id",
        "state",
        "stage",
        "sequence",
        "cases_total",
        "cases_done",
        "finished_at",
    }
    assert body["state"] == "queued" and body["stage"] == "queued"
    assert body["cases_total"] is None and body["cases_done"] == 0
    assert body["finished_at"] is None
    for absent in ("score", "is_pass", "terminal_reason", "max_score", "binding"):
        assert absent not in body, f"未完成的运行不得出现 {absent}"

    _assert_problem(client.get(RUN_REPORT.format(run=run_id), headers=token), 404)


def test_completed_run_view_matches_the_persisted_run_row(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, outcome = _run_to_terminal(engine, tmp_path, world)
    assert outcome.state == "completed", outcome
    row = _run_row(engine, run_id)
    assert row is not None

    response = client.get(
        RUN_VIEW.format(run=run_id), headers=_auth(world.student_token(signer, world.student_a))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluation_run_id"] == run_id
    assert body["submission_id"] == str(row.submission_id)
    assert body["state"] == row.state == "completed"
    assert body["stage"] == row.stage == "terminal"
    assert body["sequence"] == row.sequence
    assert body["cases_total"] == row.cases_total == 2
    assert body["cases_done"] == row.cases_done == 2
    assert body["started_at"] is not None and body["finished_at"] is not None
    assert "score" not in body and "terminal_reason" not in body


# --- 4. 报告：角色裁剪 ---------------------------------------------------------------------


def test_student_report_view_hides_hidden_case_expected_output(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """学生视图不得含隐藏用例的期望输出（也不含实际输出摘要：B2 库里没有可见性声明）。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    response = client.get(
        RUN_REPORT.format(run=run_id), headers=_auth(world.student_token(signer, world.student_a))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    serialized = json.dumps(body, ensure_ascii=False)
    assert HIDDEN_MARKER not in serialized
    assert VISIBLE_MARKER not in serialized
    assert len(body["case_evidence"]) == 2
    for item in body["case_evidence"]:
        # fail-closed：没有显式可见性声明 ⇒ 期望/实际输出摘要整体省略（契约允许二者缺席）。
        assert "expected_output_excerpt" not in item
        assert "actual_output_excerpt" not in item
        # 证据身份仍然保留：学生能看到"哪一条用例没过"与它的输出摘要。
        assert item["actual_output_digest"]
    assert body["overall"]["score"] == 6.0
    assert body["total_check"] == {"sum_of_awards": 6.0, "matches_total": True}


def test_teacher_report_view_contains_the_hidden_expected_output(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    response = client.get(
        RUN_REPORT.format(run=run_id),
        params={"view": "teacher"},
        headers=_auth(world.teacher_token(signer)),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    serialized = json.dumps(body, ensure_ascii=False)
    assert HIDDEN_MARKER in serialized and VISIBLE_MARKER in serialized
    hidden = next(item for item in body["case_evidence"] if item["case_id"] == _HIDDEN_CASE)
    assert hidden["expected_output_excerpt"] == HIDDEN_MARKER
    # 五项绑定摘要只在报告里出现（运行视图没有这些字段）。
    assert set(body["binding"]) == {
        "submission_digest",
        "question_version_binding",
        "package_digest",
        "runner_policy_digest",
        "environment_digest",
        "interpreter_version",
    }


def test_report_matches_the_persisted_result_row(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """报告与分数都来自 `b2.evaluation_results`（路由不重算）。"""
    run_id, _, outcome = _run_to_terminal(engine, tmp_path, world)
    stored = _result_row(engine, run_id)
    assert stored is not None
    attempts = (
        (world.student_token(signer, world.student_a), {}),
        (world.teacher_token(signer), {"view": "teacher"}),
        (world.admin_token(signer), {"view": "teacher"}),
    )
    for token, params in attempts:
        response = client.get(RUN_REPORT.format(run=run_id), params=params, headers=_auth(token))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["overall"]["score"] == float(stored.score)
        assert body["overall"]["max_score"] == float(stored.max_score)
        assert body["overall"]["is_pass"] == bool(stored.is_pass)
        assert body["result_digest"] == stored.result_digest
        assert body["state"] == "completed"
    assert outcome.score == Decimal(stored.score)


def test_student_cannot_request_the_teacher_view(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`view` 只是**请求**：非教师/助教/管理员索要教师视图 → 403（不静默降级）。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    response = client.get(
        RUN_REPORT.format(run=run_id),
        params={"view": "teacher"},
        headers=_auth(world.student_token(signer, world.student_a)),
    )
    _assert_problem(response, 403)


def test_unknown_report_view_is_422(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    response = client.get(
        RUN_REPORT.format(run=run_id),
        params={"view": "admin"},
        headers=_auth(world.teacher_token(signer)),
    )
    _assert_problem(response, 422)


def test_report_is_404_for_rejected_and_failed_runs(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`rejected`/`failed` 没有分数、没有报告（经 SSE 终态帧送达）。"""
    rejected_run, _, rejected = _run_to_terminal(
        engine, tmp_path, world, decision=_blocking_decision()
    )
    failed_run, _, failed = _run_to_terminal(
        engine, tmp_path, world, source=_PackageSource(error=RuntimeError("b3 unreachable"))
    )
    assert (rejected.state, rejected.terminal_reason) == ("rejected", "security_blocked")
    assert (failed.state, failed.terminal_reason) == ("failed", "infrastructure_error")
    token = _auth(_admin(signer))
    details = {}
    for run_id in (rejected_run, failed_run):
        assert _result_row(engine, run_id) is None
        response = client.get(RUN_REPORT.format(run=run_id), headers=token)
        _assert_problem(response, 404)
        details[run_id] = response.json()["detail"]
        view = client.get(RUN_VIEW.format(run=run_id), headers=token)
        assert view.status_code == 200, view.text
        assert "score" not in view.json()
    # 运行**存在**但没有报告 ≠ 运行不存在：detail 必须如实说出状态与原因，否则教师会在
    # 成绩单里看到这一行、却被接口告知"运行不存在"（成品走查实测踩到）。
    assert "does not exist" not in details[rejected_run]
    assert "state: rejected" in details[rejected_run]
    assert "security_blocked" in details[rejected_run]
    assert "state: failed" in details[failed_run]
    assert "infrastructure_error" in details[failed_run]
    unknown = client.get(RUN_REPORT.format(run=str(uuid.uuid4())), headers=token)
    _assert_problem(unknown, 404)
    assert "does not exist" in unknown.json()["detail"]  # 真不存在时仍如实说"不存在"


# --- 5. SSE -------------------------------------------------------------------------------


def test_event_stream_is_monotonic_and_ends_at_the_terminal_frame(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    stored = _result_row(engine, run_id)
    response = client.get(
        RUN_EVENTS.format(run=run_id), headers=_auth(world.student_token(signer, world.student_a))
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"

    frames = _sse_frames(response.text)
    assert frames, "SSE 必须至少产出一帧"
    sequences = [frame["id"] for frame in frames]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    assert [frame["data"]["sequence"] for frame in frames] == sequences
    assert frames[-1]["event"] == "completed"
    assert frames[-1]["data"]["state"] == "completed"
    assert frames[-1]["data"]["score"] == float(stored.score)
    assert frames[-1]["data"]["terminal_reason"] is None
    # 终帧之后流结束：没有后续帧，也没有第二个终态帧。
    terminal_events = {"completed", "rejected", "failed"}
    assert sum(1 for frame in frames if frame["event"] in terminal_events) == 1


def test_event_stream_resume_with_last_event_id_does_not_repeat(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    token = _auth(world.student_token(signer, world.student_a))
    full = _sse_frames(client.get(RUN_EVENTS.format(run=run_id), headers=token).text)
    assert len(full) >= 2, full

    resume_after = full[0]["id"]
    resumed = _sse_frames(
        client.get(
            RUN_EVENTS.format(run=run_id),
            headers={**token, "Last-Event-ID": str(resume_after)},
        ).text
    )
    assert [frame["id"] for frame in resumed] == [frame["id"] for frame in full[1:]]
    assert all(frame["id"] > resume_after for frame in resumed)


def test_event_stream_frames_never_carry_expected_output_or_source(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    body = client.get(
        RUN_EVENTS.format(run=run_id), headers=_auth(world.student_token(signer, world.student_a))
    ).text
    assert HIDDEN_MARKER not in body
    assert VISIBLE_MARKER not in body
    assert SOURCE_MARKER not in body
    for frame in _sse_frames(body):
        # 帧字段集合就是契约 `EvaluationStreamFrame`（additionalProperties: false）。
        assert set(frame["data"]) == {
            "evaluation_run_id",
            "event",
            "state",
            "stage",
            "sequence",
            "cases_total",
            "cases_done",
            "score",
            "is_pass",
            "terminal_reason",
            "message",
            "occurred_at",
        }


def test_rejected_run_stream_ends_with_a_reason_and_no_score(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world, decision=_blocking_decision())
    frames = _sse_frames(
        client.get(RUN_EVENTS.format(run=run_id), headers=_auth(_admin(signer))).text
    )
    terminal = frames[-1]
    assert terminal["event"] == "rejected"
    assert terminal["data"]["terminal_reason"] == "security_blocked"
    assert terminal["data"]["score"] is None and terminal["data"]["is_pass"] is None


def test_event_stream_rejects_a_malformed_last_event_id(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """坏的重连游标明确 422（当成"从头开始"会把整段进度重放，而调用方无从察觉）。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    response = client.get(
        RUN_EVENTS.format(run=run_id),
        headers={**_auth(_admin(signer)), "Last-Event-ID": "not-a-sequence"},
    )
    _assert_problem(response, 422)


def test_unknown_run_stream_is_404(client: TestClient, signer: _Signer) -> None:
    response = client.get(RUN_EVENTS.format(run=str(uuid.uuid4())), headers=_auth(_admin(signer)))
    _assert_problem(response, 404)


def _admin(signer: _Signer) -> str:
    return signer.token(user_id=str(uuid.uuid4()), roles=("admin",), scope=[])


# --- 6. 成绩查询 ---------------------------------------------------------------------------


def _seed_grade_rows(engine: Engine, world: _World, *, count_a: int = 1, count_b: int = 1) -> None:
    for _ in range(count_a):
        _seed_submission(
            engine, student_id=world.student_a, course_id=world.course, section_id=world.section_a
        )
    for _ in range(count_b):
        _seed_submission(
            engine, student_id=world.student_b, course_id=world.course, section_id=world.section_b
        )


def test_grades_require_authentication_and_course_id(
    client: TestClient, signer: _Signer, world: _World
) -> None:
    _assert_problem(client.get(GRADES), 401)
    _assert_problem(client.get(GRADES, headers=_auth(world.teacher_token(signer))), 422)
    _assert_problem(
        client.get(
            GRADES, params={"course_id": "not-a-uuid"}, headers=_auth(world.teacher_token(signer))
        ),
        422,
    )


def test_grades_report_unfinished_runs_with_state_instead_of_a_zero(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    _seed_submission(
        engine, student_id=world.student_a, course_id=world.course, section_id=world.section_a
    )
    response = client.get(
        GRADES, params={"course_id": world.course}, headers=_auth(world.teacher_token(signer))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert set(row) == {field for field, _ in GRADE_EXPORT_COLUMNS}
    assert row["state"] == "queued"
    assert row["score"] is None and row["max_score"] is None and row["is_pass"] is None
    assert row["evaluation_run_id"] is None
    assert row["student_number"] is None and row["display_name"] is None


def test_grades_show_the_persisted_score_of_a_completed_run(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, submission_id, _ = _run_to_terminal(engine, tmp_path, world)
    stored = _result_row(engine, run_id)
    response = client.get(
        GRADES, params={"course_id": world.course}, headers=_auth(world.teacher_token(signer))
    )
    row = next(item for item in response.json()["items"] if item["submission_id"] == submission_id)
    assert row["state"] == "completed"
    assert row["score"] == float(stored.score) == 6.0
    assert row["max_score"] == float(stored.max_score) == 10.0
    assert row["is_pass"] == bool(stored.is_pass)
    assert row["is_pass"] is False, "有一条用例未通过 ⇒ 不是通过（scoring: passed_count == 用例数）"
    assert row["evaluation_run_id"] == run_id


def test_grades_pagination_cursor_boundaries(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    _seed_grade_rows(engine, world, count_a=2, count_b=1)
    token = _auth(world.teacher_token(signer))

    first = client.get(GRADES, params={"course_id": world.course, "limit": 2}, headers=token)
    assert first.status_code == 200, first.text
    page_one = first.json()
    assert len(page_one["items"]) == 2 and page_one["next_cursor"]

    second = client.get(
        GRADES,
        params={"course_id": world.course, "limit": 2, "cursor": page_one["next_cursor"]},
        headers=token,
    )
    assert second.status_code == 200, second.text
    page_two = second.json()
    assert len(page_two["items"]) == 1 and page_two["next_cursor"] is None
    seen = [item["submission_id"] for item in page_one["items"] + page_two["items"]]
    assert len(set(seen)) == 3, "游标分页不得重复或漏行"

    # 空游标等价于"未提供"（与 section_id 的空值口径一致），不是坏游标。
    empty = client.get(
        GRADES, params={"course_id": world.course, "cursor": "", "section_id": ""}, headers=token
    )
    assert empty.status_code == 200, empty.text


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "not-a-cursor"},
        {"limit": "0"},
        {"limit": "201"},
        {"limit": "abc"},
        {"section_id": "not-a-uuid"},
        {"question_version_id": "not-a-uuid"},
        {"student_id": "not-a-uuid"},
    ],
)
def test_invalid_grade_filters_and_cursors_are_422(
    client: TestClient, signer: _Signer, world: _World, params: dict[str, str]
) -> None:
    response = client.get(
        GRADES,
        params={"course_id": world.course, **params},
        headers=_auth(world.teacher_token(signer)),
    )
    _assert_problem(response, 422)


def test_students_only_see_their_own_rows_and_cannot_widen_student_id(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    _seed_grade_rows(engine, world, count_a=1, count_b=1)
    token = _auth(world.student_token(signer, world.student_a))

    own = client.get(GRADES, params={"course_id": world.course}, headers=token)
    assert own.status_code == 200, own.text
    assert [item["student_id"] for item in own.json()["items"]] == [world.student_a]

    # 指名他人：403（**不**静默收窄成"自己的记录"——那会让越权探测看起来像正常查询）。
    _assert_problem(
        client.get(
            GRADES,
            params={"course_id": world.course, "student_id": world.student_b},
            headers=token,
        ),
        403,
    )

    named_self = client.get(
        GRADES, params={"course_id": world.course, "student_id": world.student_a}, headers=token
    )
    assert named_self.status_code == 200, named_self.text


def test_cross_course_and_cross_section_are_403(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    _seed_grade_rows(engine, world, count_a=1, count_b=1)
    # 另一门课的令牌：课程作用域不覆盖 → 403。
    _assert_problem(
        client.get(
            GRADES, params={"course_id": world.course}, headers=_auth(world.outsider_token(signer))
        ),
        403,
    )
    # 只覆盖课序 A 的助教指名课序 B → 403。
    _assert_problem(
        client.get(
            GRADES,
            params={"course_id": world.course, "section_id": world.section_b},
            headers=_auth(world.section_teacher_token(signer)),
        ),
        403,
    )


def test_section_scoped_staff_is_narrowed_when_no_section_is_named(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """未指名课序时按**已覆盖**的课序收敛（既不放行整门课，也不是 403）。"""
    _seed_grade_rows(engine, world, count_a=1, count_b=2)
    response = client.get(
        GRADES,
        params={"course_id": world.course},
        headers=_auth(world.section_teacher_token(signer)),
    )
    assert response.status_code == 200, response.text
    assert [item["section_id"] for item in response.json()["items"]] == [world.section_a]


# --- 7. 导出 -------------------------------------------------------------------------------


def test_export_headers_and_rows_match_the_contract(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    run_id, submission_id, _ = _run_to_terminal(engine, tmp_path, world)
    stored = _result_row(engine, run_id)
    token = _auth(world.teacher_token(signer))

    xlsx = client.get(GRADES_EXPORT, params={"course_id": world.course}, headers=token)
    assert xlsx.status_code == 200, xlsx.text
    assert xlsx.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    assert xlsx.headers["content-disposition"] == (
        f'attachment; filename="grades-{world.course}.xlsx"'
    )
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    headers = [header for _, header in GRADE_EXPORT_COLUMNS]
    assert [cell.value for cell in sheet[1]] == headers
    row = {headers[index]: cell.value for index, cell in enumerate(sheet[2])}
    assert row["提交ID"] == submission_id
    assert row["得分"] == float(stored.score) == 6.0

    csv_response = client.get(
        GRADES_EXPORT, params={"course_id": world.course, "format": "csv"}, headers=token
    )
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.headers["content-type"].startswith(CSV_MEDIA_TYPE)
    assert csv_response.headers["content-disposition"] == (
        f'attachment; filename="grades-{world.course}.csv"'
    )
    assert csv_response.content.startswith("\ufeff".encode()), "CSV 必须带 BOM"
    assert b"\r\n" in csv_response.content
    records = _read_csv(csv_response.content)
    assert records[0] == headers
    assert records[1][headers.index("提交ID")] == submission_id


def test_export_bytes_are_exactly_what_the_renderers_produce(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, runtime: B2Runtime,
    tmp_path: pathlib.Path,
) -> None:
    """端点发出的字节 == 服务层渲染函数的产物：路由没有另造一套编码/转义。

    这样"公式注入转义""BOM""CRLF""空值不写成 0"这些性质都只由 `grades/export.py` 决定，而它们的
    行为由 `tests/test_grades_query_export.py`（已交付用例）逐条覆盖——本用例只证明路由**原样透出**
    那一层，避免同一断言在两处漂移。（本用例也不复制那些断言的任何一个。）
    """
    _run_to_terminal(engine, tmp_path, world)
    _seed_grade_rows(engine, world, count_b=0)
    teacher_token = world.teacher_token(signer)
    authenticator = runtime.intake.authenticator
    assert authenticator is not None and runtime.grades is not None
    principal = authenticator.authenticate(f"Bearer {teacher_token}")
    rows = list(runtime.grades.iter_grades(principal=principal, course_id=world.course))
    assert len(rows) == 2

    token = _auth(teacher_token)
    for fmt, expected in (
        ("xlsx", render_xlsx(rows)),
        ("csv", render_csv(rows)),
    ):
        response = client.get(
            GRADES_EXPORT, params={"course_id": world.course, "format": fmt}, headers=token
        )
        assert response.status_code == 200, response.text
        assert response.content == expected, fmt


def test_export_never_contains_an_unescaped_formula_cell(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """真实导出里不存在"以触发字符开头却未被转义"的单元格。

    当前数据下这条是**平凡的**（`GradeRow` 的每个字段都是 UUID/枚举/数字/时间戳，两个自由文本字段
    `student_number`/`display_name` 属 B4、恒为 `None`），因此它不重复
    `tests/test_grades_query_export.py` 里那组逐触发字符的转义断言；它的作用是**守住这个不变量**：
    一旦将来某列开始带自由文本，这里会立刻抓到未转义的那种。
    """
    _run_to_terminal(engine, tmp_path, world)
    _seed_grade_rows(engine, world, count_b=1)
    response = client.get(
        GRADES_EXPORT,
        params={"course_id": world.course, "format": "csv"},
        headers=_auth(world.teacher_token(signer)),
    )
    assert response.status_code == 200, response.text
    for record in _read_csv(response.content)[1:]:
        for cell in record:
            if cell.startswith(FORMULA_TRIGGERS):
                assert cell.startswith(ESCAPE_PREFIX), cell


def test_export_rejects_an_unsupported_format(
    client: TestClient, signer: _Signer, world: _World
) -> None:
    response = client.get(
        GRADES_EXPORT,
        params={"course_id": world.course, "format": "pdf"},
        headers=_auth(world.teacher_token(signer)),
    )
    _assert_problem(response, 422)


def test_export_scopes_a_student_to_their_own_rows(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    _seed_grade_rows(engine, world, count_a=1, count_b=3)
    response = client.get(
        GRADES_EXPORT,
        params={"course_id": world.course, "format": "csv"},
        headers=_auth(world.student_token(signer, world.student_a)),
    )
    assert response.status_code == 200, response.text
    records = _read_csv(response.content)
    assert len(records) == 2, "表头 + 只有自己的那一行"
    assert world.student_b not in response.content.decode("utf-8")


# --- 6b. 成绩查询与导出：口径一致 / 越权 / 负面声明（HTTP 层） -------------------------------

#: `GradeRow` 字段 → 导出中文表头（代码里的唯一权威是 `GRADE_EXPORT_COLUMNS`）。
_GRADE_HEADER = {field: header for field, header in GRADE_EXPORT_COLUMNS}


def _grade_pages(
    client: TestClient, params: Mapping[str, Any], headers: Mapping[str, str], *, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """按游标把 `GET /api/v1/grades` 的**全部**页拉全，返回 `(行, 页数)`。

    页数上限是**测试自己的保险**：游标一旦退化（例如按毫秒截断），翻页会永不结束——有上限就变成一条
    失败断言，而不是挂死进程。
    """
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    while pages < 20:
        query: dict[str, Any] = {**params, "limit": limit}
        if cursor is not None:
            query["cursor"] = cursor
        response = client.get(GRADES, params=query, headers=dict(headers))
        assert response.status_code == 200, response.text
        body = response.json()
        items.extend(body["items"])
        cursor = body["next_cursor"]
        pages += 1
        if cursor is None:
            return items, pages
    raise AssertionError("游标分页未在上限内结束——游标可能重复返回同一批行")


def _xlsx_records(payload: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    sheet = load_workbook(io.BytesIO(payload), read_only=True).active
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    header = [str(cell) for cell in rows[0]]
    return header, [dict(zip(header, row, strict=True)) for row in rows[1:]]


def _csv_records(payload: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    rows = _read_csv(payload)
    header = rows[0]
    return header, [dict(zip(header, row, strict=True)) for row in rows[1:]]


def _iso_millis(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
    return str(value)


def _normalised_export(record: Mapping[str, Any]) -> dict[str, Any]:
    """导出解析结果 → 契约 `GradeRow` 的 JSON 形状，供与 `GET /api/v1/grades` 逐字段比对。

    空单元格（CSV 的 `""` 与 XLSX 的 `None`）统一为 `None`——**不**换算成 0。
    """
    field_of = {_GRADE_HEADER[field]: field for field in _GRADE_HEADER}
    normalised: dict[str, Any] = {}
    for header, value in record.items():
        field = field_of[header]
        if field in ("score", "max_score"):
            normalised[field] = None if value in (None, "") else float(str(value))
        elif field == "is_pass":
            normalised[field] = (
                None
                if value in (None, "")
                else (value if isinstance(value, bool) else str(value).strip().lower() == "true")
            )
        elif field == "submitted_at":
            normalised[field] = _iso_millis(value)
        else:
            normalised[field] = None if value in (None, "") else str(value)
    return normalised


def _mixed_grade_dataset(
    engine: Engine, tmp_path: pathlib.Path, world: _World
) -> dict[str, str]:
    """四种**真实**处境各一条：`completed`（有分数）、`running`、`failed`、`queued`（尚无运行）。

    写入侧全部走生产路径：`_run_to_terminal` 是真实编排器，`create_run`/`append_frame` 是真实的
    运行实体 API。因此这里出现的行形状就是线上会出现的形状（未完成的行**没有**结果行）。
    """
    completed_run, completed_submission, _ = _run_to_terminal(engine, tmp_path, world)

    running_submission, running_question = _seed_submission(
        engine, student_id=world.student_a, course_id=world.course, section_id=world.section_a
    )
    with engine.begin() as connection:
        running_run = create_run(
            connection,
            submission_id=running_submission,
            question_version_id=running_question,
            submission_digest=_digest(f"submission:{running_submission}"),
            occurred_at=BASE,
        )
        # 与编排器同一顺序：先迁移状态（运行行 + 提交行），再写进度帧。
        transition_run(
            connection,
            evaluation_run_id=running_run,
            to_state="running",
            occurred_at=BASE + timedelta(seconds=1),
            stage="dynamic_execution",
        )
        advance_submission_state(
            connection,
            submission_id=running_submission,
            to_state="running",
            occurred_at=BASE + timedelta(seconds=1),
        )
        append_frame(
            connection,
            evaluation_run_id=running_run,
            event="progress",
            state="running",
            stage="dynamic_execution",
            occurred_at=BASE + timedelta(seconds=1),
            cases_done=1,
            message="正在执行隐藏用例。",
        )

    failed_submission, failed_question = _seed_submission(
        engine, student_id=world.student_b, course_id=world.course, section_id=world.section_b
    )
    with engine.begin() as connection:
        failed_run = create_run(
            connection,
            submission_id=failed_submission,
            question_version_id=failed_question,
            submission_digest=_digest(f"submission:{failed_submission}"),
            occurred_at=BASE,
        )
        transition_run(
            connection,
            evaluation_run_id=failed_run,
            to_state="failed",
            occurred_at=BASE + timedelta(seconds=2),
            terminal_reason="infrastructure_error",
            stage="terminal",
        )
        advance_submission_state(
            connection,
            submission_id=failed_submission,
            to_state="failed",
            occurred_at=BASE + timedelta(seconds=2),
            reason="infrastructure_error",
        )
        append_frame(
            connection,
            evaluation_run_id=failed_run,
            event="failed",
            state="failed",
            stage="terminal",
            occurred_at=BASE + timedelta(seconds=2),
            terminal_reason="infrastructure_error",
            message="评测基础设施失败。",
        )

    queued_submission, _ = _seed_submission(
        engine, student_id=world.student_b, course_id=world.course, section_id=world.section_b
    )
    with engine.connect() as connection:
        completed_question = str(
            connection.execute(
                sa.select(submissions.c.question_version_id).where(
                    submissions.c.submission_id == uuid.UUID(completed_submission)
                )
            ).scalar_one()
        )
    return {
        "completed_run": completed_run,
        "completed_submission": completed_submission,
        "completed_question": completed_question,
        "running_run": running_run,
        "running_submission": running_submission,
        "failed_run": failed_run,
        "failed_submission": failed_submission,
        "queued_submission": queued_submission,
    }


def test_grades_pages_and_both_export_formats_agree_field_by_field(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """A：同一筛选条件下，`GET /api/v1/grades` 的**全部页**与 XLSX/CSV 导出逐字段一致。

    断言的是"同一口径"而不是"两处代码碰巧一致"：
    ①分页翻完（不重不漏、行序稳定）；②三种来源的行**逐字段**相等（含 `null` 的语义）；
    ③中文列名是**原始字符串**相等（不是"能编码"）。
    """
    dataset = _mixed_grade_dataset(engine, tmp_path, world)
    token = _auth(world.teacher_token(signer))
    params = {"course_id": world.course}

    rows_two, pages = _grade_pages(client, params, token, limit=2)
    assert pages == 2, "四条行、每页 2 行 ⇒ 两页"
    assert len(rows_two) == 4
    assert len({row["submission_id"] for row in rows_two}) == 4, "分页不得重复"
    rows_three, _ = _grade_pages(client, params, token, limit=3)
    assert rows_three == rows_two, "行序必须稳定（与页大小无关）"
    assert {row["submission_id"] for row in rows_two} == {
        dataset["completed_submission"],
        dataset["running_submission"],
        dataset["failed_submission"],
        dataset["queued_submission"],
    }

    xlsx = client.get(GRADES_EXPORT, params={**params, "format": "xlsx"}, headers=token)
    assert xlsx.status_code == 200, xlsx.text
    csv_response = client.get(GRADES_EXPORT, params={**params, "format": "csv"}, headers=token)
    assert csv_response.status_code == 200, csv_response.text

    xlsx_header, xlsx_rows = _xlsx_records(xlsx.content)
    csv_header, csv_rows = _csv_records(csv_response.content)
    expected_header = [header for _, header in GRADE_EXPORT_COLUMNS]
    # 中文列名逐字符相等（XLSX/CSV/代码常量三处一致）
    assert xlsx_header == csv_header == expected_header
    assert csv_header[0] == "提交ID" and csv_header[3] == "姓名"
    assert "是否通过" in csv_header and "提交时间(UTC)" in csv_header
    # CSV 的**字节**层面也是原文：BOM + UTF-8 编码的中文表头 + CRLF
    assert csv_response.content.startswith("\ufeff".encode())
    assert csv_response.content.split(b"\r\n")[0] == (
        "\ufeff" + ",".join(expected_header)
    ).encode("utf-8")

    assert [_normalised_export(record) for record in csv_rows] == rows_two
    assert [_normalised_export(record) for record in xlsx_rows] == rows_two
    assert [record[_GRADE_HEADER["submission_id"]] for record in csv_rows] == [
        row["submission_id"] for row in rows_two
    ]

    # 同一筛选口径在**收窄**后仍然一致（section 过滤同时作用于查询与导出）
    narrow = {"course_id": world.course, "section_id": world.section_a}
    narrow_rows, _ = _grade_pages(client, narrow, token, limit=1)
    assert {row["student_id"] for row in narrow_rows} == {world.student_a}
    narrow_csv = client.get(GRADES_EXPORT, params={**narrow, "format": "csv"}, headers=token)
    _, narrow_records = _csv_records(narrow_csv.content)
    assert [_normalised_export(record) for record in narrow_records] == narrow_rows
    assert len(narrow_records) == len(narrow_rows) == 2

    # 题目版本过滤同理（三个过滤参数都走同一条 `_resolve`）
    by_question = {"course_id": world.course, "question_version_id": dataset["completed_question"]}
    question_rows, _ = _grade_pages(client, by_question, token, limit=2)
    assert [row["submission_id"] for row in question_rows] == [dataset["completed_submission"]]
    question_csv = client.get(
        GRADES_EXPORT, params={**by_question, "format": "csv"}, headers=token
    )
    _, question_records = _csv_records(question_csv.content)
    assert [_normalised_export(record) for record in question_records] == question_rows


def test_empty_query_parameters_mean_not_provided(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """空串一律视为"未提供"（既有口径）：`?cursor=`/`?section_id=`/`?student_id=`/`?limit=` 都不是 422。

    `?section_id=` 若被当成"课序为空"的过滤条件，一次空查询串就会变成 0 行（而不是整门课）；
    `?cursor=` 若被当成坏游标就会变成 422。两条都是"空值 ≠ 非法"。
    """
    _seed_grade_rows(engine, world, count_a=1, count_b=1)
    token = _auth(world.teacher_token(signer))
    empty = {"cursor": "", "section_id": "", "student_id": "", "limit": ""}

    response = client.get(GRADES, params={"course_id": world.course, **empty}, headers=token)
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 2, "空 section_id 不构成过滤 ⇒ 整门课"
    assert response.json()["next_cursor"] is None

    export = client.get(
        GRADES_EXPORT,
        params={"course_id": world.course, "section_id": "", "student_id": "", "format": "csv"},
        headers=token,
    )
    assert export.status_code == 200, export.text
    _, records = _csv_records(export.content)
    assert len(records) == 2


def test_unfinished_runs_are_null_score_with_state_and_never_zero(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """C（负面声明）：未完成/失败的运行以 `state` 呈现，`score` 必须是 `null` —— **绝不**是 `0`。

    一次未跑完的评测被判成 0 分，在页面上与"考了 0 分"无法区分：这是本模块最贵的一种错。
    """
    dataset = _mixed_grade_dataset(engine, tmp_path, world)
    token = _auth(world.teacher_token(signer))
    params = {"course_id": world.course}

    response = client.get(GRADES, params=params, headers=token)
    assert response.status_code == 200, response.text
    by_submission = {row["submission_id"]: row for row in response.json()["items"]}

    for key, state in (
        ("running_submission", "running"),
        ("failed_submission", "failed"),
        ("queued_submission", "queued"),
    ):
        row = by_submission[dataset[key]]
        assert row["state"] == state
        assert row["score"] is None, f"{state} 的行不得有分数"
        assert row["score"] != 0, f"{state} 的行被写成了 0 分"
        assert row["max_score"] is None and row["is_pass"] is None
        assert row["submission_id"] in dataset.values()

    done = by_submission[dataset["completed_submission"]]
    assert done["state"] == "completed"
    assert done["score"] == 6.0, "已完成的行必须照常有分数（排除'整列皆空'的假通过）"
    assert done["evaluation_run_id"] == dataset["completed_run"]

    # 导出侧同口径：未完成 → **空单元格**，不是 `0`
    csv_response = client.get(GRADES_EXPORT, params={**params, "format": "csv"}, headers=token)
    xlsx_response = client.get(GRADES_EXPORT, params={**params, "format": "xlsx"}, headers=token)
    _, csv_rows = _csv_records(csv_response.content)
    _, xlsx_rows = _xlsx_records(xlsx_response.content)
    for key, state in (
        ("running_submission", "running"),
        ("failed_submission", "failed"),
        ("queued_submission", "queued"),
    ):
        csv_row = next(
            record for record in csv_rows if record[_GRADE_HEADER["submission_id"]] == dataset[key]
        )
        xlsx_row = next(
            record for record in xlsx_rows if record[_GRADE_HEADER["submission_id"]] == dataset[key]
        )
        assert csv_row[_GRADE_HEADER["state"]] == state
        assert csv_row[_GRADE_HEADER["score"]] == ""
        assert csv_row[_GRADE_HEADER["score"]] != "0"
        assert xlsx_row[_GRADE_HEADER["state"]] == state
        assert xlsx_row[_GRADE_HEADER["score"]] is None
        assert xlsx_row[_GRADE_HEADER["score"]] != 0
    completed_csv = next(
        record
        for record in csv_rows
        if record[_GRADE_HEADER["submission_id"]] == dataset["completed_submission"]
    )
    assert completed_csv[_GRADE_HEADER["score"]] == "6"


def test_offset_is_not_a_paging_parameter(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """负面声明：分页**只有**游标一种机制。`offset` 不得改变返回的行（既不分页、也不报错）。

    为什么只断"不生效"而不是"拒绝"：本 API 的分页参数是契约声明的 `Cursor`/`Limit`，未声明的查询
    参数被 FastAPI 忽略（同一实现里 `student_id` 是"接受但契约未声明"的既成事实，见报告）。要求
    "未声明的参数一律 422" 会是一条比契约更严的新行为，本工作项不改契约也不擅自加严。
    """
    _seed_grade_rows(engine, world, count_a=3, count_b=0)
    token = _auth(world.teacher_token(signer))
    base_params = {"course_id": world.course, "limit": 2}

    plain = client.get(GRADES, params=base_params, headers=token)
    assert plain.status_code == 200, plain.text
    with_offset = client.get(GRADES, params={**base_params, "offset": 2}, headers=token)
    assert with_offset.status_code == 200, with_offset.text
    assert with_offset.json()["items"] == plain.json()["items"], "offset 改变了返回的行"
    assert with_offset.json()["next_cursor"] == plain.json()["next_cursor"]

    # 对照：真正的翻页机制（游标）确实前进——排除"这个查询本来就只有一页"的假通过
    second = client.get(
        GRADES,
        params={**base_params, "cursor": plain.json()["next_cursor"]},
        headers=token,
    )
    assert second.status_code == 200, second.text
    first_ids = [item["submission_id"] for item in plain.json()["items"]]
    second_ids = [item["submission_id"] for item in second.json()["items"]]
    assert len(first_ids) == 2 and len(second_ids) == 1
    assert set(first_ids).isdisjoint(second_ids)


def test_students_cannot_query_or_export_another_students_grades(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """C：学生指名**他人**查询或导出 → 403（不是静默收窄成自己的记录）；指名自己 → 200。"""
    _seed_grade_rows(engine, world, count_a=1, count_b=1)
    token = _auth(world.student_token(signer, world.student_a))

    for path in (GRADES, GRADES_EXPORT):
        _assert_problem(
            client.get(
                path,
                params={"course_id": world.course, "student_id": world.student_b},
                headers=token,
            ),
            403,
        )
        own = client.get(
            path,
            params={"course_id": world.course, "student_id": world.student_a},
            headers=token,
        )
        assert own.status_code == 200, own.text
    # 导出里不得出现另一名学生的任何行
    csv_response = client.get(
        GRADES_EXPORT, params={"course_id": world.course, "format": "csv"}, headers=token
    )
    _, records = _csv_records(csv_response.content)
    assert [record[_GRADE_HEADER["student_id"]] for record in records] == [world.student_a]


def test_export_enforces_the_same_scope_rules_as_the_query(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """C：越界课序 → 403（查询与导出都拦）；覆盖范围内的教师与管理员 → 200（两条路径都通）。"""
    _seed_grade_rows(engine, world, count_a=1, count_b=2)
    assistant = _auth(world.section_teacher_token(signer))
    out_of_scope = {"course_id": world.course, "section_id": world.section_b}
    for path in (GRADES, GRADES_EXPORT):
        _assert_problem(client.get(path, params=out_of_scope, headers=assistant), 403)
        in_scope = client.get(
            path,
            params={"course_id": world.course, "section_id": world.section_a},
            headers=assistant,
        )
        assert in_scope.status_code == 200, in_scope.text
        # 未指名课序时按已覆盖课序收敛（既不放行整门课，也不是 403）
        if path == GRADES:
            narrowed = client.get(path, params={"course_id": world.course}, headers=assistant)
            assert narrowed.status_code == 200, narrowed.text
            assert [item["section_id"] for item in narrowed.json()["items"]] == [world.section_a]
        else:
            narrowed = client.get(
                path,
                params={"course_id": world.course, "format": "csv"},
                headers=assistant,
            )
            assert narrowed.status_code == 200, narrowed.text
            _, records = _csv_records(narrowed.content)
            assert [record[_GRADE_HEADER["section_id"]] for record in records] == [world.section_a]

    admin = _auth(world.admin_token(signer))
    for path in (GRADES, GRADES_EXPORT):
        response = client.get(path, params={"course_id": world.course}, headers=admin)
        assert response.status_code == 200, response.text
    # 教师点名某学生（代查）在作用域内是允许的
    teacher = _auth(world.teacher_token(signer))
    for path in (GRADES, GRADES_EXPORT):
        response = client.get(
            path, params={"course_id": world.course, "student_id": world.student_b}, headers=teacher
        )
        assert response.status_code == 200, response.text


def test_export_http_payload_never_carries_source_or_hidden_cases(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """负面声明：导出（真实 HTTP 字节）里零命中——源码片段、隐藏用例期望输出、报告字段名。

    数据集用真实编排器跑到底：学生源码含 `SOURCE_MARKER`、隐藏用例的期望输出含 `HIDDEN_MARKER`，
    二者都真的落了库（见 `test_teacher_report_view_contains_the_hidden_expected_output`）。因此这里的
    "零命中"是有效的，而不是因为数据里本来就没有。
    """
    _run_to_terminal(engine, tmp_path, world)
    token = _auth(world.teacher_token(signer))
    markers = (
        SOURCE_MARKER,
        HIDDEN_MARKER,
        VISIBLE_MARKER,
        "reference_answer",
        "expected_output_excerpt",
        "hidden_cases",
        "SECRET",
    )

    xlsx = client.get(
        GRADES_EXPORT, params={"course_id": world.course, "format": "xlsx"}, headers=token
    )
    csv_response = client.get(
        GRADES_EXPORT, params={"course_id": world.course, "format": "csv"}, headers=token
    )
    assert xlsx.status_code == 200 and csv_response.status_code == 200

    # XLSX：**解包后逐部件**扫描（内容可能落在 sheet XML 或任何其它部件里）
    with zipfile.ZipFile(io.BytesIO(xlsx.content)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    for name, blob in parts.items():
        text = blob.decode("utf-8", errors="ignore")
        for marker in markers:
            assert marker not in text, (name, marker)
    csv_text = csv_response.content.decode("utf-8-sig")
    for marker in markers:
        assert marker not in csv_text, marker

    # 列集合本身也没有"能装受保护内容"的入口
    header, _ = _csv_records(csv_response.content)
    assert header == [header for _, header in GRADE_EXPORT_COLUMNS]
    for forbidden in ("源码", "参考", "答案", "隐藏", "期望输出", "报告", "证据", "路径"):
        assert not any(forbidden in name for name in header), (forbidden, header)


# --- 8. 分项健康（三态：ok / not-configured / unavailable） --------------------------------


def test_health_state_ok_when_configured_and_usable(runtime: B2Runtime) -> None:
    """状态 1（`ok`）：配了且可用 ⇒ 计入健康。"""
    items = runtime.health_items()
    assert items["http-intake"][0] == HEALTH_OK, items["http-intake"]
    assert items["evaluation-chain"][0] == HEALTH_OK, items["evaluation-chain"]
    healthy, detail = runtime.health_summary()
    assert healthy is True, detail
    assert HEALTH_UNAVAILABLE not in detail, detail
    for name in ("http-intake", "evaluation-chain"):
        assert f"{name}: {HEALTH_OK}" in detail


def test_health_state_not_configured_does_not_make_the_module_unhealthy(
    signer: _Signer, world: _World, b2_database_url: str, engine: Engine
) -> None:
    """状态 2（`not-configured`）：只配 HTTP 入口的部署 `healthy=True`。

    这是既有验收的口径（`tests/test_intake_api.py::test_plugin_router_serves_the_endpoint` 断言
    `report.healthy is True`）：**故意**不启用邮件/评测/消息代理的部署不该被永远标红——恒假告警会
    训练运维忽略这个标志，比不报更糟。同时三件事必须成立：①未配置项**逐项出现**在 detail 里、
    ②措辞与"不可用"区分开、③路由行为**不变**（未配置 ≠ 能服务）。
    """
    client, runtime = _client_for(
        {
            "DATABASE_URL": b2_database_url,
            "B2_ARTIFACT_ROOT": str(_SCRATCH / "intake-only-artifacts"),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with client:
        items = runtime.health_items()
        healthy, detail = runtime.health_summary()
        blocked = client.get(
            RUN_VIEW.format(run=str(uuid.uuid4())), headers=_auth(_admin(signer))
        )

    assert items["http-intake"][0] == HEALTH_OK, items["http-intake"]
    for name in ("email-intake", "evaluation-chain", "message-broker"):
        assert items[name][0] == HEALTH_NOT_CONFIGURED, (name, items[name])

    assert healthy is True, detail
    assert HEALTH_UNAVAILABLE not in detail, detail
    assert detail.count(HEALTH_NOT_CONFIGURED) == 3, detail
    for name in items:  # 未配置项不得被静默省略
        assert name in detail, name

    # 健康标志表达"部署是否按当前配置正常工作"，503 表达"端点此刻能不能服务"：两者互不替代。
    _assert_problem(blocked, 503)


def test_health_state_unavailable_when_a_configured_dependency_is_broken(
    signer: _Signer, world: _World, b2_database_url: str, unmigrated_database_url: str,
    engine: Engine,
) -> None:
    """状态 3（`unavailable`）：配了却不可用 ⇒ 计入不健康。

    两个场景都是"配了（或必需项缺失）但不可用"：①工作区根与工件根相交（配了 → 装配期拒绝）；
    ②库连得上但结构版本不符/未初始化（`EXPECTED_SCHEMA_REVISION` 是 503 的判据）。
    """
    artifact_root = _SCRATCH / "broken-artifacts"
    nested_client, nested_runtime = _client_for(
        {
            "DATABASE_URL": b2_database_url,
            "B2_ARTIFACT_ROOT": str(artifact_root),
            "B2_WORKSPACE_ROOT": str(artifact_root / "workspaces"),
            "B2_B3_BASE_URL": "https://b3.test.invalid",
            "B2_B3_AUTHORIZATION": "Bearer test-service-credential",
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with nested_client:
        items = nested_runtime.health_items()
        healthy, detail = nested_runtime.health_summary()
    assert items["evaluation-chain"][0] == HEALTH_UNAVAILABLE, items["evaluation-chain"]
    assert nested_runtime.evaluation_not_configured is False
    assert healthy is False, detail
    assert f"evaluation-chain: {HEALTH_UNAVAILABLE}" in detail

    unmigrated_client, unmigrated_runtime = _client_for(
        {
            "DATABASE_URL": unmigrated_database_url,
            "B2_ARTIFACT_ROOT": str(_SCRATCH / "broken-db-artifacts"),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with unmigrated_client:
        items = unmigrated_runtime.health_items()
        healthy, detail = unmigrated_runtime.health_summary()
    assert items["http-intake"][0] == HEALTH_UNAVAILABLE, items["http-intake"]
    assert healthy is False, detail


def test_message_broker_health_distinguishes_unconfigured_from_broken(
    signer: _Signer, b2_database_url: str, engine: Engine
) -> None:
    """消息代理：没给 `NATS_URL` ⇒ 未配置（不健康说不通）；配了但账本里有 DLQ ⇒ 不可用。"""
    unconfigured_client, unconfigured = _client_for(
        {
            "DATABASE_URL": b2_database_url,
            "B2_ARTIFACT_ROOT": str(_SCRATCH / "broker-off-artifacts"),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    with unconfigured_client:
        status, detail = unconfigured.broker_health()
        healthy, _ = unconfigured.health_summary()
    assert status == HEALTH_NOT_CONFIGURED and "NATS_URL" in detail
    assert healthy is True

    configured_client, configured = _client_for(
        {
            "DATABASE_URL": b2_database_url,
            "B2_ARTIFACT_ROOT": str(_SCRATCH / "broker-on-artifacts"),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
            "NATS_URL": "nats://127.0.0.1:54223",
        }
    )
    with configured_client:
        status, detail = configured.broker_health()
        assert (status, "DLQ 0" in detail) == (HEALTH_OK, True), detail
        assert configured.outbox is not None
        _dead_letter_one_event(engine)
        status, detail = configured.broker_health()
        healthy, _ = configured.health_summary()
    assert status == HEALTH_UNAVAILABLE and "DLQ" in detail, detail
    assert healthy is False


def test_recorded_endpoint_failures_are_reported_without_flipping_readiness(
    runtime: B2Runtime,
) -> None:
    """`record_failure()` 只让 detail **如实累计**，绝不翻转 `healthy`（新发现的注释/行为矛盾的落点）。

    这三件事必须一起断言，少一条就会漏掉一种改坏法：

    ① 计数真的进了 detail（否则"计数被静默丢弃"看不出来）；
    ② 分项状态仍是 `ok`、`healthy` 仍是 `True`（否则"用它翻转标志"看不出来）；
    ③ detail 里带**"累计"与"不参与健康判定"**（这两句是这段诊断的全部价值所在：它挂在一份
    `healthy=True` 的报告里，少了任何一句都会被读成"此刻仍在坏"，而计数器从进程启动起单调不降，
    那种读法永远无法靠一次恢复消除）。

    翻转标志被否掉的**产品理由**写在 `runtime.py` 的"失败计数与健康判定的关系"：单调计数一旦用于
    翻转，实例在第一次瞬时抖动后就永久不健康——正是该文档开头拒绝的"恒假告警"。
    """
    healthy_before, detail_before = runtime.health_summary()
    assert healthy_before is True, detail_before
    assert "累计" not in detail_before, detail_before  # 没失败过就不该出现那句话

    runtime.record_failure()
    runtime.record_failure()

    items = runtime.health_items()
    healthy_after, detail_after = runtime.health_summary()
    status, item_detail = items["evaluation-chain"]

    assert runtime.failure_count == 2
    assert status == HEALTH_OK, items["evaluation-chain"]
    assert "2" in item_detail and "累计" in item_detail, item_detail
    assert "不参与健康判定" in item_detail, item_detail
    assert healthy_after is True, detail_after
    assert HEALTH_UNAVAILABLE not in detail_after, detail_after


def _dead_letter_one_event(engine: Engine) -> None:
    """写一条已进 DLQ 的 outbox 行（"配了 broker 但没投通"的账本实证）。

    事件本身用**已交付**的 `build_event` + `enqueue` 写（形状/约束由生产代码保证）；`dead_lettered_at`
    用一条 UPDATE 补上——派发器的失败记录（`attempts`/`last_error`/`dead_lettered_at`）是其内部步骤，
    没有公开入口，这里是测试构造该状态，不是第二条生产写入路径。
    """
    from autograder_b2.db.schema import outbox_events
    from autograder_b2.events.outbox import EVALUATION_FAILED, build_event, enqueue

    event = build_event(
        subject=EVALUATION_FAILED,
        payload={"evaluation_run_id": str(uuid.uuid4())},
        correlation_id=str(uuid.uuid4()),
        occurred_at=BASE,
    )
    with engine.begin() as connection:
        enqueue(connection, event, deduplication_key="b2-routes-dlq", enqueued_at=BASE)
        connection.execute(
            sa.update(outbox_events)
            .where(outbox_events.c.event_id == uuid.UUID(event.event_id))
            .values(attempts=5, last_error="EventBusUnavailable", dead_lettered_at=BASE)
        )


def test_queued_run_frames_are_emitted_without_a_fabricated_terminal_frame(
    world: _World, engine: Engine, runtime: B2Runtime
) -> None:
    """未完成的运行：帧生成器产出**已有**帧并在空闲超时后结束，绝不伪造终态帧。

    这条不通过 HTTP 走：`TestClient` 会缓冲整个响应，而"空闲 30 秒即结束"（`progress` 的默认
    `idle_timeout`）会让请求挂满整个超时。这里直接驱动运行时持有的生成器并把超时缩短到 0.05 秒；
    **HTTP 路径**的 SSE 行为由上面几条终态用例覆盖（它们带终帧，因此立即结束）。
    """
    run_id, _ = _queued_run(engine, world)
    assert runtime.evaluator is not None
    frames = list(
        runtime.evaluator.frame_stream(run_id, after=0, poll_interval=0.01, idle_timeout=0.05)
    )
    assert [frame.event for frame in frames] == ["progress"]
    assert frames[0].state == "queued" and frames[0].score is None
    # 断线重连语义在路由使用的同一入口上：`after=<最后一帧>` 不重复投递。
    assert runtime.evaluator.read_frames(run_id, after=frames[0].sequence) == []


# --- 12. CHG-0014 / CHG-0015：新端点的鉴权矩阵、只含计数与两处强制脱敏 -----------------------
#
# 契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `getAppealPack` / `getEvaluationQualitySummary`
# （两条 intake 端点见 `tests/test_intake_api.py`）。本节的四条验证逐条对应 `CHG-0015` §6 的四条
# 与 `CHG-0014` §6 的 2、3 两条。

QUALITY_SUMMARY = "/api/v1/evaluation-runs/quality-summary"
APPEAL_PACK = "/api/v1/evaluation-runs/{run}/appeal-pack"
QUARANTINE_LIST = "/api/v1/intake/quarantines"
QUARANTINE_RESOLVE = "/api/v1/intake/quarantines/{quarantine}/resolve"

#: 响应取值里"任何 UUID 形态的串"（用于证明只含计数的聚合里没有个人标识）。
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class _NewPath:
    """一条新端点的"取一次"请求构造（鉴权矩阵要对四条端点发出可比的请求）。"""

    def __init__(
        self,
        name: str,
        path: str,
        *,
        verb: str,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> None:
        self.name = name
        self.path = path
        self.verb = verb
        self.params = params or {}
        self.body = body

    def url(self, world: _World, engine: Engine) -> str:
        if "{" not in self.path:
            return self.path
        return self.path.format(run=_seed_run_id(engine, world), quarantine=str(uuid.uuid4()))

    def send(
        self, client: TestClient, world: _World, engine: Engine, headers: dict[str, str] | None
    ) -> Any:
        url = self.url(world, engine)
        if self.verb == "post":
            return client.post(url, json=self.body or {}, headers=headers)
        return client.get(url, params=self.params, headers=headers)


def _seed_run_id(engine: Engine, world: _World) -> str:
    """建一条提交与一个 `queued` 运行（只要 id 存在即可，不跑评测）。"""
    run_id, _ = _queued_run(engine, world)
    return run_id


def _new_paths(question_version_id: str) -> tuple[_NewPath, ...]:
    """四条新端点的"最小可发送请求"（鉴权判定发生在业务判定之前，因此这些取值不需要存在）。"""
    return (
        _NewPath("quarantine-list", QUARANTINE_LIST, verb="get"),
        _NewPath(
            "quarantine-resolve",
            QUARANTINE_RESOLVE,
            verb="post",
            body={"student_id": str(uuid.uuid4())},
        ),
        _NewPath("appeal-pack", APPEAL_PACK, verb="get"),
        _NewPath(
            "quality-summary",
            QUALITY_SUMMARY,
            verb="get",
            params={"question_version_id": question_version_id},
        ),
    )


def test_new_routes_reject_unauthenticated_requests(
    client: TestClient, world: _World, engine: Engine
) -> None:
    """无令牌 ⇒ 401（四条端点，逐个）。"""
    for endpoint in _new_paths(str(uuid.uuid4())):
        response = endpoint.send(client, world, engine, headers=None)
        _assert_problem(response, 401)


def test_new_routes_are_forbidden_for_students_and_foreign_scopes(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """学生（含跨课程的"局外人"）⇒ 403：四条端点都不是学生面。

    - 隔离区两条：学生不拥有"某封邮件没变成提交"这件事，凭它推断他人邮件是否存在都不该发生；
    - `appeal-pack`：同课程的**另一个**学生读他人运行 → 403（本人运行是允许的，另有专门用例）；
    - `quality-summary`：这是面向教师的聚合（"这道题值不值得留"）。
    """
    student_token = _auth(world.student_token(signer, world.student_b))
    outsider_token = _auth(world.outsider_token(signer))
    for endpoint in _new_paths(str(uuid.uuid4())):
        for token in (student_token, outsider_token):
            response = endpoint.send(client, world, engine, headers=token)
            _assert_problem(response, 403)


def test_quality_summary_is_not_swallowed_by_the_run_path_parameter(
    client: TestClient, signer: _Signer, world: _World, engine: Engine
) -> None:
    """`CHG-0015` §6 第 1 条：字面段必须先声明，否则会被 `{evaluation_run_id}` 吞掉。

    被吞掉的表现是**一个令人困惑的 404**（`quality-summary` 会被当成运行标识去解析 UUID），
    而不是 200/422。这里断言它走的是聚合路径：给出题目版本 ⇒ 200。
    """
    question = str(uuid.uuid4())
    response = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": question},
        headers=_auth(world.teacher_token(signer)),
    )
    assert response.status_code == 200, response.text
    assert response.json()["question_version_id"] == question


def test_quality_summary_returns_counts_only(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`CHG-0015` §6 第 2 条：响应**只有计数**——没有任何可回溯到个人的标识。

    三条独立判据（缺一条都可能是巧合）：
    ① 键集合逐字等于契约声明的属性集合；
    ② 键名里不出现 student/submission/source/expected 这类标识性或受保护的名字；
    ③ **取值扫描**：除 `question_version_id` 外，序列化后的响应里不出现任何 UUID 形态的串
       （学生 id、提交 id、运行 id 都会是 UUID），`case_failure_rates` 里也只有 ordinal/计数。
    另有结构性判据：`QualitySummary` 的字段里除 `question_version_id` 之外没有 `*_id`。
    """
    run_id, submission_id, _ = _run_to_terminal(engine, tmp_path, world)
    question = str(_question_version_of(engine, run_id))
    response = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": question},
        headers=_auth(world.teacher_token(signer)),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "question_version_id",
        "attempt_count",
        "failure_categories",
        "static_rule_hits",
        "case_failure_rates",
        "unmeasured_runs",
        "security_found",
        "security_not_found",
        "security_not_checked",
    }
    for key in body:
        lowered = key.lower()
        for forbidden in ("student", "submission", "source", "expected", "user"):
            assert forbidden not in lowered, f"字段名 {key!r} 不该出现在只含计数的聚合里"
    assert "evaluation_run_id" not in json.dumps(body, ensure_ascii=False)

    serialized = json.dumps(body, ensure_ascii=False)
    uuids = set(re.findall(UUID_PATTERN, serialized))
    assert uuids == {question}, f"聚合里出现了非题目版本的 UUID：{sorted(uuids)}"
    assert run_id not in serialized and submission_id not in serialized
    # 结构性判据：类型里根本没有能承载个人标识的槽位。
    identifier_fields = [
        field.name
        for field in dataclasses.fields(QualitySummary)
        if field.name.endswith("_id") and field.name != "question_version_id"
    ]
    assert identifier_fields == []


def _question_version_of(engine: Engine, run_id: str) -> uuid.UUID:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(evaluation_runs.c.question_version_id).where(
                evaluation_runs.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()


def _static_rule_counts_from_db(engine: Engine, question: str) -> dict[tuple[str, str], int]:
    """该题目版本范围内**真实的**逐规则命中数（聚合端点的对照读数）。"""
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(static_findings.c.rule_id, static_findings.c.severity, sa.func.count())
            .select_from(
                static_findings.join(
                    evaluation_runs,
                    static_findings.c.evaluation_run_id == evaluation_runs.c.evaluation_run_id,
                )
            )
            .where(evaluation_runs.c.question_version_id == uuid.UUID(question))
            .group_by(static_findings.c.rule_id, static_findings.c.severity)
        ).fetchall()
    return {(str(row[0]), str(row[1])): int(row[2]) for row in rows}


def test_quality_summary_aggregates_per_case_and_per_rule(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """聚合正确性：逐用例失败类别、逐用例失败率、逐规则命中（含**零命中**的规则）。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    question = str(_question_version_of(engine, run_id))
    # 再挂一条没跑过的运行（同一个题目版本）：`attempt_count` 必须把它算进去。
    _seed_queued_run_for_question(engine, world, question)

    body = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": question},
        headers=_auth(world.teacher_token(signer)),
    ).json()

    assert body["attempt_count"] == 2
    # 真实编排 + 替身 runner：隐藏用例（ordinal 1）失败、可见用例（ordinal 0）通过。
    assert body["case_failure_rates"] == [
        {"ordinal": 0, "attempts": 1, "failures": 0},
        {"ordinal": 1, "attempts": 1, "failures": 1},
    ]
    assert body["failure_categories"] == [{"category": "output_mismatch", "count": 1}]
    # 逐规则：以 B2 规则目录为准**逐条**列出（未命中即 0），且命中数与该范围内真实的静态明细
    # **逐条相等**——不是"看起来像"。
    rule_ids = {item["rule_id"] for item in body["static_rule_hits"]}
    assert rule_ids >= {rule.rule_id for rule in RULES}
    reported = {
        (item["rule_id"], item["severity"]): item["count"]
        for item in body["static_rule_hits"]
        if item["count"]
    }
    expected = _static_rule_counts_from_db(engine, question)
    assert reported == expected, "命中数必须与库里的静态明细逐条相等"
    # 干净源码也会命中一条 info 规则（模块顶层有可执行语句）：这正说明"零命中也要列出来"的意义。
    assert expected == {("STAT-PY-013", "info"): 1}
    assert body["security_found"] == 0
    # 真实编排跑完的那次记的是"查过、没发现"（`not_found`），而没跑过的那个连结论都没有（NULL）。
    # 两者都不是 `not_checked`——这正是三态必须分开报的意义。
    assert body["security_not_found"] == 1
    assert body["security_not_checked"] == 1


def test_quality_summary_never_merges_not_checked_into_not_found(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`CHG-0015` §6 第 3 条：只有"未检查"的题目必须报 `security_not_checked > 0` 且 `not_found == 0`。

    两个题目版本对照着测，缺任何一半都不算证明：
    ① 只挂了一条**没跑过**的运行（`security_status IS NULL` = 从未走到安全判定）⇒ `not_checked == 1`、
       `not_found == 0`——"我们没看过"绝不能被读成"我们看过且没问题"；
    ② 跑完一次真实编排（干净结论记的是 `not_found`）⇒ `not_found == 1`、`not_checked == 0`。
    并附上契约没要求、但少了就会掩盖数据异常的恒等式：三态之和 == `attempt_count`。
    """
    never_ran = str(uuid.uuid4())
    _seed_queued_run_for_question(engine, world, never_ran)
    token = _auth(world.teacher_token(signer))

    unseen = client.get(
        QUALITY_SUMMARY, params={"question_version_id": never_ran}, headers=token
    ).json()
    assert unseen["attempt_count"] == 1
    assert unseen["security_not_checked"] == 1
    assert unseen["security_not_found"] == 0
    assert unseen["security_found"] == 0

    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    checked = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": str(_question_version_of(engine, run_id))},
        headers=token,
    ).json()
    assert checked["security_not_found"] == 1
    assert checked["security_not_checked"] == 0

    for body in (unseen, checked):
        assert (
            body["security_found"] + body["security_not_found"] + body["security_not_checked"]
            == body["attempt_count"]
        ), "三态必须完整划分 attempt_count"


def test_quality_summary_makes_unmeasured_runs_visible(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`CHG-0015` §6 第 4 条：没有资源数据的运行计入 `unmeasured_runs`，不读成"没占用"。

    判据是**报告里有没有沙箱回读的峰值**（`resource` 对象里的四项之一）。两个题目版本对照：
    ① 替身 runner 没有沙箱读数（`resource` 全为 `None`）⇒ `unmeasured_runs == 1`；
    ② 把一项**真实形态**的峰值写进已落库的报告 ⇒ `unmeasured_runs == 0`。
    `wall_seconds` **不算**测量（那是 B2 自测的墙钟，不是沙箱回读的峰值）——它在两次里都有。
    """
    unmeasured_run, _, _ = _run_to_terminal(engine, tmp_path, world)
    measured_run, _, _ = _run_to_terminal(engine, tmp_path, world)
    token = _auth(world.teacher_token(signer))

    unmeasured = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": str(_question_version_of(engine, unmeasured_run))},
        headers=token,
    ).json()
    assert unmeasured["attempt_count"] == 1
    assert unmeasured["unmeasured_runs"] == 1

    _write_case_resource(engine, measured_run, {"memory_peak_bytes": 4096, "wall_seconds": 0.2})
    measured = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": str(_question_version_of(engine, measured_run))},
        headers=token,
    ).json()
    assert measured["attempt_count"] == 1
    assert measured["unmeasured_runs"] == 0, "有峰值读数的运行不该被算成未测量"

    # 反例：只有墙钟（B2 自测）而没有沙箱回读的峰值 ⇒ 仍然算未测量。
    _write_case_resource(engine, measured_run, {"wall_seconds": 0.2})
    wall_only = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": str(_question_version_of(engine, measured_run))},
        headers=token,
    ).json()
    assert wall_only["unmeasured_runs"] == 1


def test_quality_summary_time_window_is_inclusive_and_validated(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`from`/`to` 过滤（两端都含）与非法取值的 422。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    question = str(_question_version_of(engine, run_id))
    token = _auth(world.teacher_token(signer))
    now = datetime.now(UTC)
    before = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    after = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    inside = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": question, "from": before, "to": after},
        headers=token,
    )
    assert inside.status_code == 200, inside.text
    assert inside.json()["attempt_count"] == 1

    outside = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": question, "from": after},
        headers=token,
    )
    assert outside.status_code == 200, outside.text
    assert outside.json()["attempt_count"] == 0

    for params in (
        {"question_version_id": question, "from": "not-a-timestamp"},
        {"question_version_id": question, "to": "2026-09-16T09:00:00"},
        {"question_version_id": "not-a-uuid"},
        {},
    ):
        _assert_problem(client.get(QUALITY_SUMMARY, params=params, headers=token), 422)

    # 未声明的参数一律**不生效**（不给契约加隐式别名）：`from_` 不是 `from`。
    alias = client.get(
        QUALITY_SUMMARY,
        params={"question_version_id": question, "from_": after},
        headers=token,
    )
    assert alias.status_code == 200, alias.text
    assert alias.json()["attempt_count"] == 1, "`from_` 是未声明参数，必须被忽略而不是当过滤用"


def _seed_queued_run_for_question(engine: Engine, world: _World, question: str) -> str:
    """在**同一个题目版本**下再建一条提交与 `queued` 运行（用于 `attempt_count` 断言）。"""
    submission_id, _ = _seed_submission(
        engine, student_id=world.student_b, course_id=world.course, section_id=world.section_a
    )
    with engine.begin() as connection:
        connection.execute(
            submissions.update()
            .where(submissions.c.submission_id == uuid.UUID(submission_id))
            .values(question_version_id=uuid.UUID(question))
        )
        return create_run(
            connection,
            submission_id=submission_id,
            question_version_id=question,
            submission_digest=_digest(f"submission:{submission_id}"),
            occurred_at=BASE,
        )


def _write_case_resource(engine: Engine, run_id: str, resource: dict[str, object]) -> None:
    """把一项**合成**的资源读数写进已持久化的报告（测试构造"沙箱回读到了峰值"这一状态）。

    为什么不跑真实 nsjail：本机是 Windows，沙箱一律不可执行（`runner.policy` 的探测会拒绝），
    因此"真实读数"只能在 Linux 验收里产生。这里改的是**报告这一份已落库的读数**，
    被测代码（`quality` 的判据）读到的仍是数据库里的真实 JSONB。
    """
    with engine.begin() as connection:
        report = connection.execute(
            sa.select(evaluation_results.c.report).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()
        patched = json.loads(json.dumps(report))
        patched["case_evidence"][0]["resource"] = dict(resource)
        connection.execute(
            sa.update(evaluation_results)
            .where(evaluation_results.c.evaluation_run_id == uuid.UUID(run_id))
            .values(report=patched)
        )


# --- 申诉证据包（CHG-0014 §6 第 2、3 条） --------------------------------------------------


def test_appeal_pack_is_pinned_by_the_report_digest(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """教师视图的证据包：`result_digest` == 已持久化报告的摘要，并可被读者自行复算。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    stored = _result_row(engine, run_id)
    response = client.get(
        APPEAL_PACK.format(run=run_id), headers=_auth(world.teacher_token(signer))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) <= {
        "evaluation_run_id",
        "result_digest",
        "report",
        "binding",
        "runner_policy_summary",
        "replay",
        "redactions",
        "generated_at",
    }
    assert {
        "evaluation_run_id",
        "result_digest",
        "report",
        "binding",
        "replay",
        "redactions",
    } <= set(body)
    assert body["evaluation_run_id"] == run_id
    assert body["result_digest"] == stored.result_digest == stored.report["result_digest"]
    # 教师视图是完整报告 ⇒ 读者可用 `report_digest` 从包内内容复算出同一个摘要。
    assert report_digest(body["report"]) == body["result_digest"]
    # 五项绑定摘要重复在包里（契约 `binding`，`additionalProperties: false` ⇒ 不含 interpreter_version）。
    assert set(body["binding"]) == {
        "submission_digest",
        "package_digest",
        "runner_policy_digest",
        "environment_digest",
        "question_version_binding",
    }
    assert body["binding"] == {
        key: stored.report["binding"][key]
        for key in (
            "submission_digest",
            "package_digest",
            "runner_policy_digest",
            "environment_digest",
            "question_version_binding",
        )
    }
    assert body["replay"] and len(body["replay"]) <= 2000
    assert body["redactions"], "redactions 至少一项（契约 minItems: 1）"
    assert all(0 < len(entry) <= 200 for entry in body["redactions"])
    assert body["generated_at"].endswith("Z")


def test_appeal_pack_removes_host_paths_and_lists_what_it_removed(
    client: TestClient,
    signer: _Signer,
    world: _World,
    engine: Engine,
    tmp_path: pathlib.Path,
    settings_source: dict[str, str],
) -> None:
    """`CHG-0014` §6 第 2 条：包内**不含宿主绝对路径**，且 `redactions` 列出移除了什么、为什么。

    为了让这条真的有区分力，先把三种宿主路径形态写进**已持久化的报告**：部署配置里的工件根
    （路由逐字前缀剔除的那一类）、一条 Windows 盘符路径、一条 POSIX 宿主路径；并保留一条
    **沙箱内**路径作为反例——`/workspace/main.py` 是沙箱内路径，不是宿主路径，不该被抹掉。
    """
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    artifact_root = settings_source["B2_ARTIFACT_ROOT"]
    injected = {
        "artifact": f"{artifact_root}/sha256/deadbeef",
        "windows": r"C:\autograder\private\queues\abc",
        "posix": "/srv/autograder/private/queues/abc",
        "sandbox": "/workspace/main.py",
    }
    _append_to_report_text(engine, run_id, " ".join(injected.values()))

    response = client.get(
        APPEAL_PACK.format(run=run_id), headers=_auth(world.teacher_token(signer))
    )
    assert response.status_code == 200, response.text
    serialized = json.dumps(response.json(), ensure_ascii=False)
    for name in ("artifact", "windows", "posix"):
        assert injected[name] not in serialized, f"{name} 形态的宿主路径必须被移除"
    assert artifact_root not in serialized
    assert HOST_PATH_PLACEHOLDER in serialized, "占位串应当出现在被替换处"
    assert injected["sandbox"] in serialized, "沙箱内路径不是宿主路径，不该被抹掉"
    assert response.json()["redactions"], "被移除的内容必须被列出而不是暗示"


def test_appeal_pack_does_not_mutate_the_stored_report(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """脱敏返回**新对象**：库里那份报告逐字节不变（含被替换掉的宿主路径）。"""
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    host_path = r"D:\autograder\workspaces\ws-9z8y"
    _append_to_report_text(engine, run_id, host_path)
    before = json.dumps(_result_row(engine, run_id).report, ensure_ascii=False, sort_keys=True)

    assert (
        client.get(APPEAL_PACK.format(run=run_id), headers=_auth(world.teacher_token(signer))).status_code
        == 200
    )
    after = json.dumps(_result_row(engine, run_id).report, ensure_ascii=False, sort_keys=True)
    assert after == before, "证据包组装不得改写已持久化的报告"


def test_appeal_pack_student_view_never_widens_visibility(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """`CHG-0014` §6 第 3 条：学生拿到的包与报告端点的学生视图**同口径**，不含隐藏用例与期望输出。

    同时断言 `view` 语义：默认视图是 `teacher`，学生索要它一律 403（**不**静默降级）。
    """
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    student = _auth(world.student_token(signer, world.student_a))

    student_pack = client.get(
        APPEAL_PACK.format(run=run_id), params={"view": "student"}, headers=student
    )
    assert student_pack.status_code == 200, student_pack.text
    body = student_pack.json()
    serialized = json.dumps(body, ensure_ascii=False)
    assert HIDDEN_MARKER not in serialized and VISIBLE_MARKER not in serialized
    assert SOURCE_MARKER not in serialized
    for item in body["report"]["case_evidence"]:
        assert "expected_output_excerpt" not in item
        assert "actual_output_excerpt" not in item
        assert (item.get("artifacts") or {}).get("expected_sha256") is None
    assert body["result_digest"] == _result_row(engine, run_id).result_digest
    # 脱敏清单必须**说明**学生视图被收窄了，而不是静默省略。
    assert any("student" in entry for entry in body["redactions"])

    # 教师视图仍含隐藏用例的期望输出（证明上一条不是"报告本来就没有"）。
    teacher_pack = client.get(
        APPEAL_PACK.format(run=run_id), params={"view": "teacher"}, headers=_auth(world.teacher_token(signer))
    )
    assert teacher_pack.status_code == 200, teacher_pack.text
    assert HIDDEN_MARKER in json.dumps(teacher_pack.json(), ensure_ascii=False)

    # 学生索要教师视图：默认值也是 teacher ⇒ 两条都 403。
    _assert_problem(
        client.get(APPEAL_PACK.format(run=run_id), params={"view": "teacher"}, headers=student), 403
    )
    _assert_problem(client.get(APPEAL_PACK.format(run=run_id), headers=student), 403)
    # 未知视图 ⇒ 422（枚举校验在本模块手写，错误体仍是 Problem Details）。
    _assert_problem(
        client.get(
            APPEAL_PACK.format(run=run_id),
            params={"view": "admin"},
            headers=_auth(world.teacher_token(signer)),
        ),
        422,
    )


def test_appeal_pack_needs_a_report_and_is_503_on_an_unmigrated_database(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, unmigrated_database_url: str
) -> None:
    """没有报告就没有包（404 + 如实说明状态）；库结构未初始化 ⇒ 503。"""
    run_id, _ = _queued_run(engine, world)
    problem = _assert_problem(
        client.get(APPEAL_PACK.format(run=run_id), headers=_auth(world.teacher_token(signer))), 404
    )
    assert "no report" in problem["detail"], problem["detail"]

    unmigrated, _ = _client_for({"DATABASE_URL": unmigrated_database_url})
    with unmigrated:
        for url, params in (
            (APPEAL_PACK.format(run=run_id), None),
            (QUALITY_SUMMARY, {"question_version_id": str(uuid.uuid4())}),
        ):
            response = unmigrated.get(url, params=params, headers=_auth(world.admin_token(signer)))
            _assert_problem(response, 503)


def test_appeal_pack_policy_summary_is_path_free_and_matches_the_enforced_constants(
    client: TestClient, signer: _Signer, world: _World, engine: Engine, tmp_path: pathlib.Path
) -> None:
    """策略摘要不含宿主路径，且它列的常量与 `runner.policy` **实际拒绝放宽**的那几条一致。

    这条是 `evaluation.appeal` 里"不 import 执行层"这个取舍的补偿：常数是本地字面量，
    因此必须有一条测试对着策略对象验证它们没有漂移。
    """
    run_id, _, _ = _run_to_terminal(engine, tmp_path, world)
    body = client.get(
        APPEAL_PACK.format(run=run_id), headers=_auth(world.teacher_token(signer))
    ).json()
    summary = body["runner_policy_summary"]
    assert summary["host_paths"] == "excluded"
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "D:\\" not in serialized and "/workspace" not in serialized
    policy = RunnerPolicy()
    assert policy_constraint_violations(policy) == ()
    assert summary["enforced_constants"] == {
        "network": policy.network,
        "read_only_root": policy.read_only_root,
        "allow_root": policy.allow_root,
        "seccomp": policy.use_seccomp,
        "cgroup_v2": policy.use_cgroup,
    }
    assert SANDBOX_INTERNAL_ROOTS == (DEFAULT_WORKSPACE_MOUNT,)
    assert summary["runner_policy_digest"] == body["binding"]["runner_policy_digest"]


def _append_to_report_text(engine: Engine, run_id: str, extra: str) -> None:
    """把一段文本追加到已持久化报告的第一条用例解释里（构造"报告里有宿主路径"的状态）。"""
    with engine.begin() as connection:
        report = connection.execute(
            sa.select(evaluation_results.c.report).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()
        patched = json.loads(json.dumps(report))
        first = patched["case_evidence"][0]
        first["explanation"] = f"{first['explanation']} {extra}"
        connection.execute(
            sa.update(evaluation_results)
            .where(evaluation_results.c.evaluation_run_id == uuid.UUID(run_id))
            .values(report=patched)
        )

