"""评语轨（`RQ-T-20`）与 AI 评分参考（`RQ-T-30`）的验收测试：白名单是**被测量的**，
分数独立是**被重放的**，参考分不是分是**被断言的**。

本文件回答这些"绿套件本身证明不了"的问题：

1. **白名单是结构性的**：`evidence_paths(collect_evidence(...))` 必须**逐字段等于** `INPUT_SCOPE`；
   源码/期望输出所在的那几列**根本不在查询里**（并且我们证明那些列确实存在——排除是刻意的，
   不是因为列不存在）；评语包的 import 闭包与"能拿到源码的模块"无交集，且把那些访问器换成
   会抛异常的替身之后，生成照样成功。
2. **删掉评语不移动分数**：清空 `b2.evaluation_commentaries` 后重放同一提交，分数、`result_digest`
   与逐项证据逐字相同；并且把评语生成换成"一调用就抛异常"的替身，评测链路照常跑完。
3. **默认关闭 ⇒ 没有出网调用**：把传输层换成"一调用就抛异常"的替身，未配置时 `GET` 返回
   `unavailable`、`POST` 返回 `409`，两个端点都不触发传输层。
4. **重新生成不改分数**、provider 失败只影响这一条评语。
5. **参考分不是分**（`RQ-T-30` / `CHG-0018` §7）：`evaluation-report` schema 拒收带 `advisory_score`
   的实例；`/grades`、导出、事件 payload 与库里的报告都没有它；参考分存在或被删除都不改变重放出的
   分数、逐项证据与 `result_digest`。
6. **满分来自证据、越界即失败**：`max_score` 等于 `b2.case_evidence.weight` 之和（模型连给都不许给）；
   `score` 为负或大于满分时整次生成 `failed`，**不是**被截断到合法区间。
7. **协议就是协议**：响应不是约定 JSON 对象时 `status: failed`，**不退化**成"把整段响应当评语"。

纯结构断言（不依赖数据库）与需要真实 PostgreSQL 的断言分开放：前者不挂 `requires_postgres`，
因此在没有库的环境里也**真的在跑**，而不是整文件被 skip。
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import inspect
import json
import pathlib
import re
import sys
import uuid
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
from sqlalchemy import Engine, text

from autograder_b2.api.errors import CONTENT_TYPE, install_problem_handlers
from autograder_b2.api.evaluation_routes import build_evaluation_router
from autograder_b2.api.grades_routes import build_grades_router
from autograder_b2.commentary import (
    CONFIDENCES,
    FORBIDDEN_EVIDENCE_MODULES,
    INPUT_SCOPE,
    INPUT_SCOPE_PROJECTION,
    PROMPT_VERSION,
    CommentaryProviderError,
    CommentaryService,
    build_provider,
    collect_evidence,
    evidence_paths,
    parse_generation,
)
from autograder_b2.commentary.prompt import DISCLAIMERS
from autograder_b2.commentary.provider import CommentaryProviderConfig, UrllibTransport
from autograder_b2.commentary.scope import (
    CASE_COLUMNS,
    SECURITY_COLUMNS,
    STATIC_COLUMNS,
)
from autograder_b2.db.schema import (
    EXPECTED_SCHEMA_REVISION,
    case_evidence,
    evaluation_commentaries,
    evaluation_results,
    evaluation_runs,
    metadata,
    outbox_events,
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
)
from autograder_b2.evaluation.report import report_schema_errors
from autograder_b2.runtime import B2Runtime, build_b2_runtime
from tests.conftest import requires_postgres, run_alembic

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]
B2_SRC = B2_ROOT / "src"
CONTRACT_OPENAPI = B2_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b2.yaml"

ISSUER = "autograder-b4"
AUDIENCE = "autograder"
BASE = datetime(2026, 9, 18, 9, 0, 0, tzinfo=UTC)

COMMENTARY = "/api/v1/evaluation-runs/{run}/commentary"

#: 学生源码里的标记串：**任何**评语输入、prompt、响应里都不得出现。
SOURCE_MARKER = "COMMENTARY_SECRET_SOURCE_MARKER"
#: 期望输出（隐藏用例）的标记串：同上。
EXPECTED_MARKER = "COMMENTARY_HIDDEN_EXPECTED_MARKER"
#: 逐用例证据的摘录/消息里的标记串：这些列**不在白名单里**，因此也不得出现在输入与 prompt 里。
EXCERPT_MARKER = "COMMENTARY_EXCERPT_MARKER"
MESSAGE_MARKER = "COMMENTARY_FINDING_MESSAGE_MARKER"
#: 参考分 `rationale` 里的标记串：它只能出现在评语端点的响应里（`RQ-T-30` 验收 1）。
ADVISORY_MARKER = "ADVISORY_RATIONALE_MARKER_只在参考分里"

#: 两个用例的权重之和（`_package_view()`），即证据里的满分（`RQ-T-30` 验收 5）。
EVIDENCE_MAX_SCORE = 10.0

CLEAN_SOURCE = f'print("hello")\n# {SOURCE_MARKER}\n'

_CASE_ONE = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-commentary-case-one"))
_CASE_TWO = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-commentary-case-two"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- 纯结构断言（不需要数据库，因此不挂 skip 标记） -----------------------------------------


def test_input_scope_matches_the_columns_the_queries_select() -> None:
    """白名单（**字面量声明**）必须与查询列清单的投影一致：任一侧改动而另一侧没跟上都会失败。

    这条是"字段级 SELECT"的机器含义：白名单是给人审阅的声明，查询列是真正读的东西，
    两者不许各自漂移。
    """
    assert set(INPUT_SCOPE) == set(INPUT_SCOPE_PROJECTION)
    assert list(INPUT_SCOPE) == list(INPUT_SCOPE_PROJECTION)
    assert len(INPUT_SCOPE) == len(set(INPUT_SCOPE)), "白名单不得有重复项"


def test_no_protected_column_is_named_in_the_whitelist() -> None:
    """受保护/内容类字段不得出现在白名单里（按名字逐条排除，而不是"我们记得别读"）。"""
    forbidden = {
        "case_evidence.actual_output_excerpt",
        "case_evidence.expected_output_excerpt",
        "case_evidence.message",
        "case_evidence.stdout_digest",
        "case_evidence.stderr_digest",
        "case_evidence.input_digest",
        "case_evidence.case_id",
        "static_findings.message",
        "security_findings.message",
        "run.submission_id",
        "run.package_digest",
        "run.terminal_reason",
    }
    assert set(INPUT_SCOPE) & forbidden == set()
    # 反向事实：这些列**确实存在**于被读的表里（排除是刻意的，不是因为列不存在）。
    for column in ("actual_output_excerpt", "expected_output_excerpt", "message"):
        assert column in case_evidence.c, f"case_evidence.{column} 不存在 ⇒ 本断言失去意义"


def test_columns_that_carry_content_are_absent_from_the_selected_lists() -> None:
    assert "message" not in CASE_COLUMNS + STATIC_COLUMNS + SECURITY_COLUMNS
    assert "actual_output_excerpt" not in CASE_COLUMNS
    assert "expected_output_excerpt" not in CASE_COLUMNS


def test_commentary_table_carries_only_the_advisory_score_columns() -> None:
    """契约"评语不是分"在存储层的表达（`CHG-0018` 之后依然成立）。

    表里现在**确实**有分数形状的列（`RQ-T-30` 引入），但只有 `advisory_*` 那一组：分数轨的
    `score` / `max_score` / `is_pass` / `result_digest` / evidence 一个都不在这里。
    "参考分不是分"因此不是措辞，而是**列名层面**的事实。
    """
    columns = set(evaluation_commentaries.c.keys())
    assert {"status", "text", "provider", "model", "prompt_version", "input_scope"} <= columns
    assert {
        "advisory_score",
        "advisory_max_score",
        "advisory_rationale",
        "advisory_confidence",
    } <= columns
    assert columns & {"score", "max_score", "is_pass", "result_digest", "evidence"} == set(), (
        "评语表里出现了权威分数轨的列名"
    )


def test_advisory_protocol_helpers_reject_shape_violations_without_a_database() -> None:
    """解析器的形状判定（纯结构，不需要库）：约定形状通过，任何违例都抛错而不是"尽力而为"。"""
    from autograder_b2.commentary import CommentaryResponseError

    ok = parse_generation(
        json.dumps(
            {
                "commentary": "评语正文。",
                "advisory_score": {"score": 3.5, "rationale": "依据。", "confidence": "low"},
            },
            ensure_ascii=False,
        )
    )
    assert ok.text == "评语正文。"
    assert ok.advisory is not None
    assert (str(ok.advisory.score), ok.advisory.confidence) == ("3.5", "low")
    # 只有 commentary、或参考分为 null，都是契约允许的结局。
    assert parse_generation('{"commentary": "只有评语。"}').advisory is None
    assert parse_generation('{"commentary": "只有评语。", "advisory_score": null}').advisory is None

    bad = [
        "这不是 JSON",
        "[1, 2, 3]",
        '{"commentary": "评语。", "score": 9}',  # 平级 score：契约刻意不存在的形状
        (
            '{"commentary": "评语。", "advisory_score": {"score": 1, "rationale": "r",'
            ' "confidence": "low", "max_score": 10}}'
        ),  # 模型不许给满分
        (
            '{"commentary": "评语。", "advisory_score": {"score": true, "rationale": "r",'
            ' "confidence": "low"}}'
        ),  # bool 是 int 的子类
        (
            '{"commentary": "评语。", "advisory_score": {"score": NaN, "rationale": "r",'
            ' "confidence": "low"}}'
        ),
        (
            '{"commentary": "评语。", "advisory_score": {"score": 1, "rationale": "r",'
            ' "confidence": "certain"}}'
        ),
        '{"commentary": "评语。", "advisory_score": {"score": 1, "confidence": "low"}}',
        '{"commentary": "", "advisory_score": null}',
        '{"commentary": null, "advisory_score": null}',
    ]
    for document in bad:
        with pytest.raises(CommentaryResponseError):
            parse_generation(document)
    assert CONFIDENCES == ("low", "medium", "high")


def _imported_module_names(module_name: str) -> set[str]:
    """模块（含包内子模块）的 import 闭包：只算 `autograder_b2.*` 里的名字。"""
    root = sys.modules[module_name]
    pending = [getattr(root, "__file__", None)]
    seen_files: set[pathlib.Path] = set()
    closure: set[str] = {module_name}
    while pending:
        target = pending.pop()
        if target is None:
            continue
        path = pathlib.Path(target)
        if path in seen_files or not path.is_file():
            continue
        seen_files.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
            for name in names:
                if not name.startswith("autograder_b2"):
                    continue
                closure.add(name)
                # `from X import NAME` 里的 NAME 可能不是模块（常量/类/函数）：那些跳过即可。
                try:
                    imported = importlib.util.find_spec(name) if name not in sys.modules else None
                except (ModuleNotFoundError, AttributeError, ValueError):
                    continue
                if imported is not None and imported.origin:
                    pending.append(imported.origin)
                elif name in sys.modules:
                    pending.append(getattr(sys.modules[name], "__file__", None))
    return closure


def test_commentary_import_closure_excludes_every_source_accessor() -> None:
    """生成路径**在依赖方向上**就拿不到源码/期望输出（不是靠"记得别调用"）。"""
    import autograder_b2.commentary  # noqa: F401 - 触发导入，闭包才完整

    closure = _imported_module_names("autograder_b2.commentary")
    reachable = {name for name in closure if name in FORBIDDEN_EVIDENCE_MODULES}
    # 子模块形态（`from autograder_b2.storage import cas`）也要挡住。
    for forbidden in FORBIDDEN_EVIDENCE_MODULES:
        assert forbidden not in closure, f"评语包可达受禁模块：{forbidden}"
    assert reachable == set()


def test_generate_signature_takes_no_source_like_input() -> None:
    """生成入口的参数里没有工作区/文件/包/信封这类"能拿到内容"的口子。"""
    parameters = set(inspect.signature(CommentaryService.generate).parameters)
    assert parameters == {"self", "evaluation_run_id", "run_state"}


# --- 真实 PostgreSQL 的夹具 ----------------------------------------------------------------


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
    course: str
    section: str
    student: str
    teacher: str
    other_course: str
    other_section: str

    def student_token(self, signer: _Signer) -> str:
        return signer.token(
            user_id=self.student,
            roles=("student",),
            scope=[{"course_id": self.course, "section_id": self.section}],
        )

    def teacher_token(self, signer: _Signer) -> str:
        return signer.token(
            user_id=self.teacher, roles=("teacher",), scope=[{"course_id": self.course}]
        )

    def outsider_token(self, signer: _Signer) -> str:
        return signer.token(
            user_id=str(uuid.uuid4()),
            roles=("student",),
            scope=[{"course_id": self.other_course, "section_id": self.other_section}],
        )


@pytest.fixture(scope="module")
def signer() -> _Signer:
    return _Signer()


@pytest.fixture()
def world() -> _World:
    return _World(
        course=str(uuid.uuid4()),
        section=str(uuid.uuid4()),
        student=str(uuid.uuid4()),
        teacher=str(uuid.uuid4()),
        other_course=str(uuid.uuid4()),
        other_section=str(uuid.uuid4()),
    )


@pytest.fixture(scope="module")
def database_url(fresh_database_url: str) -> str:
    result = run_alembic(fresh_database_url, "upgrade", EXPECTED_SCHEMA_REVISION)
    assert result.returncode == 0, f"upgrade 失败: {result.stderr[-1500:]}"
    return fresh_database_url


@pytest.fixture()
def engine(database_url: str) -> Iterator[Engine]:
    instance = sa.create_engine(database_url)
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


def _dsn(engine: Engine) -> str:
    """测试库的完整 DSN（**必须** `hide_password=False`：默认渲染会把口令换成 `***`，
    于是运行时装配出的引擎会以 `***` 去连库并得到"密码认证失败"——一个与本次改动无关的假失败）。"""
    return engine.url.render_as_string(hide_password=False)


def _settings(
    database_url: str, signer: _Signer, *, provider: bool = False, **extra: str
) -> dict[str, str]:
    settings = {
        "DATABASE_URL": database_url,
        "B2_ARTIFACT_ROOT": str(pathlib.Path(".pytest-tmp") / "commentary-artifacts"),
        "B2_WORKSPACE_ROOT": str(pathlib.Path(".pytest-tmp") / "commentary-workspaces"),
        "B2_B3_BASE_URL": "https://b3.test.invalid",
        "B2_B3_AUTHORIZATION": "Bearer test-service-credential",
        "B2_JWKS_JSON": json.dumps(signer.jwks),
    }
    if provider:
        settings.update(
            {
                "B2_COMMENTARY_PROVIDER": "test-provider",
                "B2_COMMENTARY_BASE_URL": "https://commentary.test.invalid/v1",
                "B2_COMMENTARY_MODEL": "test-model",
                "B2_COMMENTARY_API_KEY": "test-credential",
            }
        )
    settings.update(extra)
    return settings


def _client(runtime: B2Runtime) -> TestClient:
    """走与宿主相同的注册路径（评测 + 成绩两个路由族；本文件不需要接收入口）。"""
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_evaluation_router(runtime))
    app.include_router(build_grades_router(runtime))
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_submission(engine: Engine, world: _World, *, state: str = "queued") -> tuple[str, str]:
    identifier = uuid.uuid4()
    question = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            submissions.insert().values(
                submission_id=identifier,
                student_id=uuid.UUID(world.student),
                course_id=uuid.UUID(world.course),
                section_id=uuid.UUID(world.section),
                question_version_id=uuid.UUID(question),
                intake="api",
                idempotency_key=f"key-{identifier}",
                idempotency_scope="b2-commentary-test",
                submission_digest=_digest(f"submission:{identifier}"),
                state=state,
                created_at=BASE,
                updated_at=BASE,
                correlation_id=identifier,
                request_id=f"req-{identifier}",
            )
        )
    return str(identifier), question


# --- 编排替身（写入侧；静态检查仍是真实实现） ----------------------------------------------


@dataclass(frozen=True, slots=True)
class _CaseHandle:
    case_id: str
    ordinal: int
    weight: float
    visible_to_student: bool


class _PackageSource:
    def __init__(self, view: PackageView) -> None:
        self.view = view

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        return self.view


class _Security:
    def __init__(self, decision: SecurityDecision) -> None:
        self.decision = decision

    def decide(
        self, *, evaluation_run_id: str, files: Mapping[str, str], language: str
    ) -> SecurityDecision:
        return self.decision


class _Runner:
    """替身 runner：产出带标记串的证据，用来验证"这些列读不到"。"""

    def run_case(self, package: object, submission: SubmissionRunInput) -> CaseEvidence:
        case = submission.case
        case_id = str(case.case_id)  # type: ignore[attr-defined]
        hidden = case_id == _CASE_TWO
        return CaseEvidence(
            case_id=case_id,
            ordinal=int(case.ordinal),  # type: ignore[attr-defined]
            weight=float(case.weight),  # type: ignore[attr-defined]
            passed=not hidden,
            failure_category="output_mismatch" if hidden else None,
            input_digest=_digest(f"input:{case_id}"),
            stdout_digest=_digest(f"stdout:{case_id}"),
            stderr_digest=_digest(""),
            actual_output_excerpt=f"{EXCERPT_MARKER}:{case_id}",
            expected_output_excerpt=(EXPECTED_MARKER if hidden else f"visible:{case_id}"),
            exit_code=1 if hidden else 0,
            duration_ms=11,
            resource_limit_hit=None,
            sandbox_violations=(),
            message=f"{MESSAGE_MARKER}:{case_id}",
            evidence_digest=_digest(f"evidence:{case_id}"),
        )


class _Workspaces:
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


def _clean_security_decision() -> SecurityDecision:
    """无发现的结论。

    重放类用例用它，理由见 `test_replaying_a_run_with_static_findings_is_blocked`：**带静态发现的**
    提交在今天的实现里重放会撞 `security_findings` 主键（既有缺陷，与本批无关），
    因此那条路径由下面的 `xfail(strict=True)` 钉住，而不是让重放用例假装它不存在。
    """
    return SecurityDecision(
        status="not_found",
        findings=(),
        blocking=False,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="必需检查项全部执行完毕，未发现违规模式。",
    )


def _security_decision() -> SecurityDecision:
    return SecurityDecision(
        status="not_found",
        findings=(
            SecurityFinding(
                finding_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-commentary-finding")),
                category="network_attempt",
                severity="low",
                detected_in="static",
                message=f"{MESSAGE_MARKER}:security",
                rule_id="SEC-TEST-002",
            ),
        ),
        blocking=False,
        covered=("python_ast", "python_text"),
        unchecked=(),
        rationale="必需检查项全部执行完毕，未发现违规模式。",
    )


def _package_view() -> PackageView:
    return PackageView(
        package_digest=_digest("package"),
        language="python",
        cases=(
            CaseDescriptor(
                case_id=_CASE_ONE,
                ordinal=0,
                weight=6.0,
                visible_to_student=True,
                handle=_CaseHandle(_CASE_ONE, 0, 6.0, True),
            ),
            CaseDescriptor(
                case_id=_CASE_TWO,
                ordinal=1,
                weight=4.0,
                visible_to_student=False,
                handle=_CaseHandle(_CASE_TWO, 1, 4.0, False),
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


def _envelope(submission_id: str, question_version_id: str, world: _World) -> SubmissionEnvelope:
    content = CLEAN_SOURCE.encode("utf-8")
    return SubmissionEnvelope(
        submission_id=submission_id,
        student_id=world.student,
        course_id=world.course,
        section_id=world.section,
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


def _run_pipeline(engine: Engine, tmp_path: pathlib.Path, world: _World) -> tuple[str, str]:
    """跑一次真实编排（替身端口 + 真实静态检查 + 真实 PostgreSQL），返回 `(run_id, submission_id)`。"""
    submission_id, question = _seed_submission(engine, world)
    root = tmp_path / "orchestrator-workspaces"
    root.mkdir(exist_ok=True)
    orchestrator = EvaluationOrchestrator(
        engine=engine,
        package_source=_PackageSource(_package_view()),
        workspace_provider=_Workspaces(root, {"main.py": CLEAN_SOURCE.encode("utf-8")}),
        security=_Security(_security_decision()),
        runner=_Runner(),
    )
    outcome = orchestrator.run(_envelope(submission_id, question, world))
    assert outcome.state == "completed", outcome
    return outcome.evaluation_run_id, submission_id


# --- 白名单：真实取证的字段集 == 声明的白名单 ----------------------------------------------


@requires_postgres
def test_collected_evidence_reads_exactly_the_declared_whitelist(
    engine: Engine, tmp_path: pathlib.Path, world: _World
) -> None:
    """**本批最重要的断言之一**：实际读到的字段集 == `INPUT_SCOPE`，一个不多一个不少。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    with engine.connect() as connection:
        evidence = collect_evidence(connection, run_id)
    assert evidence.input_scope == INPUT_SCOPE
    assert set(evidence_paths(evidence.payload)) == set(INPUT_SCOPE)
    # 证据真的取到了东西（否则"相等"可能只是因为两边都空）。
    assert evidence.payload["run"]["cases_done"] == 2
    assert len(evidence.payload["case_evidence"]) == 2
    assert evidence.payload["static_findings"] or evidence.payload["security_findings"]
    assert evidence.payload["security_conclusion"]["status"] == "not_found"
    assert evidence.payload["score"] is not None


@requires_postgres
def test_protected_content_never_reaches_the_evidence_or_the_prompt(
    engine: Engine, tmp_path: pathlib.Path, world: _World
) -> None:
    """把标记串种进**受保护列**，再断言它们不出现在取证载荷与 prompt 里。

    这条与白名单断言互补：那条证明"读的字段就这些"，这条证明"那些字段里就算有内容也进不来"。
    """
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    with engine.connect() as connection:
        # 夹具已把标记写进这些列；先确认它们**确实在库里**（否则本断言是恒真）。
        row = connection.execute(
            sa.select(case_evidence.c.expected_output_excerpt, case_evidence.c.message).where(
                case_evidence.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchall()
        evidence = collect_evidence(connection, run_id)
    serialized = json.dumps(evidence.as_prompt_json(), ensure_ascii=False)
    assert any(EXPECTED_MARKER in (item[0] or "") for item in row), "夹具未把标记写进受保护列"
    assert any(MESSAGE_MARKER in (item[1] or "") for item in row)
    for marker in (SOURCE_MARKER, EXPECTED_MARKER, EXCERPT_MARKER, MESSAGE_MARKER):
        assert marker not in serialized, f"受保护内容进入了评语输入：{marker}"


@requires_postgres
def test_source_accessors_are_unreachable_from_the_generation_path(
    engine: Engine, tmp_path: pathlib.Path, world: _World, signer: _Signer, monkeypatch: Any
) -> None:
    """把"能拿到源码/期望输出"的访问器全部换成会抛异常的替身，生成仍必须成功。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    from autograder_b2.integration import submission_loader
    from autograder_b2.runner import package as package_module
    from autograder_b2.storage import cas as cas_module

    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("评语生成路径触碰了受保护访问器")

    monkeypatch.setattr(cas_module.ContentAddressedStore, "read", _explode)
    monkeypatch.setattr(submission_loader, "load_submission_envelope", _explode)
    monkeypatch.setattr(package_module, "parse_evaluation_package", _explode)
    monkeypatch.setattr(UrllibTransport, "post_json", _provider_response("评语正文。"))

    runtime = build_b2_runtime(_settings(database_url=_dsn(engine), signer=signer, provider=True))
    with _client(runtime) as client:
        response = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "ready"


# --- 出网：默认关闭就是"没有调用点" --------------------------------------------------------


def _generation_body(text: str, advisory: Mapping[str, Any] | None = None) -> str:
    """约定形状的模型输出：`{"commentary": ..., "advisory_score": ...}`（`CHG-0018` §5）。"""
    document: dict[str, Any] = {"commentary": text}
    if advisory is not None:
        document["advisory_score"] = dict(advisory)
    return json.dumps(document, ensure_ascii=False)


def _proposal(
    score: Any,
    *,
    rationale: str = ADVISORY_MARKER,
    confidence: str = "medium",
) -> dict[str, Any]:
    """一个形状合法的 `advisory_score` 提议（默认带标记串，便于断言它没有外泄）。"""
    return {"score": score, "rationale": rationale, "confidence": confidence}


def _provider_response(
    text: str,
    advisory: Mapping[str, Any] | None = None,
    *,
    calls: list[dict[str, Any]] | None = None,
) -> Any:
    """一个 `UrllibTransport.post_json` 替身：返回 OpenAI 兼容响应，content 是约定的 JSON 对象。"""
    return _provider_raw(_generation_body(text, advisory), calls=calls)


def _provider_raw(content: str, *, calls: list[dict[str, Any]] | None = None) -> Any:
    """同上，但 content 原样给出（用来构造协议违规的响应：非 JSON、缺字段、自带 max_score…）。"""

    def _post(
        self: object, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]:
        if calls is not None:
            calls.append({"url": url, "payload": dict(payload), "headers": dict(headers)})
        return 200, json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}, ensure_ascii=False
        )

    return _post


@requires_postgres
def test_unconfigured_deployment_makes_no_outbound_call(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """未配置 provider：`GET` 返回 `unavailable`、`POST` 返回 `409`，且**传输层从未被调用**。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(UrllibTransport, "post_json", _provider_response("不应发生", calls=calls))

    runtime = build_b2_runtime(_settings(database_url=_dsn(engine), signer=signer))
    assert runtime.commentary is not None and runtime.commentary.configured is False
    with _client(runtime) as client:
        got = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        )
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["status"] == "unavailable"
    assert body["text"] is None and body["provenance"] is None
    assert body["disclaimers"] and set(body["disclaimers"]) <= set(DISCLAIMERS)
    # 契约刻意没有平级 score/evidence 字段：响应体里也不得出现；参考分只在子对象里。
    assert set(body) == {
        "evaluation_run_id",
        "status",
        "text",
        "advisory_score",
        "provenance",
        "disclaimers",
    }
    assert body["advisory_score"] is None
    # `RQ-T-30` 验收 7：参考分标注在**没有参考分**时仍然存在。
    assert any("参考分" in item for item in body["disclaimers"])

    assert posted.status_code == 409, posted.text
    detail = posted.json()["detail"]
    for missing in ("B2_COMMENTARY_PROVIDER", "B2_COMMENTARY_BASE_URL", "B2_COMMENTARY_MODEL"):
        assert missing in detail, detail
    assert calls == [], "未配置的部署不得产生任何出网调用"


@requires_postgres
def test_half_configured_deployment_still_makes_no_call_and_names_the_gap(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(UrllibTransport, "post_json", _provider_response("不应发生", calls=calls))
    settings = _settings(database_url=_dsn(engine), signer=signer)
    settings["B2_COMMENTARY_PROVIDER"] = "test-provider"  # 只配一项 ⇒ 半配置
    runtime = build_b2_runtime(settings)
    with _client(runtime) as client:
        got = client.get(COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer)))
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
    assert got.json()["status"] == "unavailable"
    assert posted.status_code == 409
    assert "B2_COMMENTARY_BASE_URL" in posted.json()["detail"]
    assert calls == []


# --- 权限与状态 ----------------------------------------------------------------------------


@requires_postgres
def test_commentary_requires_authentication_and_teacher_role_for_generation(
    engine: Engine, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """401/403/404 的分工，以及"学生能读、不能生成"。用未配置 provider 的运行时跑，
    这样 200 与 403 的对照不会被"还没有评语(404)"或"没配 provider(409)"掩盖。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    runtime = build_b2_runtime(_settings(database_url=_dsn(engine), signer=signer))
    with _client(runtime) as client:
        assert client.get(COMMENTARY.format(run=run_id)).status_code == 401
        assert client.post(COMMENTARY.format(run=run_id)).status_code == 401
        # 学生可以读（本部署未启用评语 ⇒ 200 + unavailable），但不能生成（生成是编辑动作）。
        student_read = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        )
        assert student_read.status_code == 200, student_read.text
        assert student_read.json()["status"] == "unavailable"
        assert (
            client.post(
                COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
            ).status_code
            == 403
        )
        # 越权必须是 403 而不是 404。
        assert (
            client.get(
                COMMENTARY.format(run=run_id), headers=_auth(world.outsider_token(signer))
            ).status_code
            == 403
        )
        # 运行不存在 ⇒ 404（与"越权"区分开）。
        assert (
            client.get(
                COMMENTARY.format(run=str(uuid.uuid4())), headers=_auth(world.teacher_token(signer))
            ).status_code
            == 404
        )


@requires_postgres
def test_commentary_reports_pending_state_honestly_before_generation(
    engine: Engine, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """有 provider 但还没生成过：404 + 说明"还没有"，而不是 `unavailable`（那是"没配 provider"）。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        response = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    assert "no commentary has been generated" in response.json()["detail"].lower()


@requires_postgres
def test_generation_on_a_non_terminal_run_is_409(
    engine: Engine, world: _World, signer: _Signer
) -> None:
    submission_id, _question = _seed_submission(engine, world)
    from autograder_b2.evaluation import create_run

    with engine.begin() as connection:
        run_id = create_run(
            connection,
            submission_id=submission_id,
            question_version_id=_question,
            submission_digest=_digest(f"submission:{submission_id}"),
            occurred_at=BASE,
        )
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        response = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
    assert response.status_code == 409, response.text
    assert "终态" in response.json()["detail"]


# --- 生成、provenance、重新生成、失败隔离 --------------------------------------------------


@requires_postgres
def test_generation_returns_provenance_with_the_audited_input_scope(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        UrllibTransport, "post_json", _provider_response("第一版评语。", calls=calls)
    )
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
        assert posted.status_code == 202, posted.text
        body = posted.json()
        assert body["status"] == "ready" and body["text"] == "第一版评语。"
        provenance = body["provenance"]
        assert set(provenance) == {"provider", "model", "prompt_version", "input_scope"}
        assert provenance["provider"] == "test-provider"
        assert provenance["model"] == "test-model"
        # `CHG-0018` §5：prompt 从"一段文字"变成"一个 JSON 对象"，版本必须能区分两者。
        assert provenance["prompt_version"] == PROMPT_VERSION
        assert PROMPT_VERSION != "b2-commentary/1"
        # **白名单可被外部审计**：响应里列出的就是生成时实际读的字段集。
        assert list(provenance["input_scope"]) == list(INPUT_SCOPE)
        read_back = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        ).json()
        assert read_back == body
    # prompt 里只有白名单证据：受保护标记一律零命中。
    assert len(calls) == 1
    prompt = calls[0]["payload"]["messages"][0]["content"]
    for marker in (SOURCE_MARKER, EXPECTED_MARKER, EXCERPT_MARKER, MESSAGE_MARKER):
        assert marker not in prompt
    for field in ("case_evidence", "static_findings", "security_conclusion", "score"):
        assert field in prompt
    # 协议与满分纪律都写进了 prompt（不是只写在解析器里等着拒绝）。
    assert "只输出**一个 JSON 对象**" in prompt
    assert "不要给出满分" in prompt
    assert "越界会让本次生成**直接失败**" in prompt


@requires_postgres
def test_regeneration_overwrites_and_never_touches_the_score(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    with engine.connect() as connection:
        before = connection.execute(
            sa.select(evaluation_results).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()
    bodies = iter(
        [
            _generation_body("第一版评语。", _proposal(4)),
            _generation_body("第二版评语（重新生成）。", _proposal(8, rationale="第二版依据。")),
        ]
    )

    def _post(*args: Any, **kwargs: Any) -> tuple[int, str]:
        return 200, json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": next(bodies)}}]},
            ensure_ascii=False,
        )

    monkeypatch.setattr(UrllibTransport, "post_json", _post)
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        first = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        ).json()
        second = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        ).json()
    assert first["text"] == "第一版评语。"
    assert second["text"] == "第二版评语（重新生成）。"
    # 参考分同样被**覆盖**（不是追加、也不是保留上一版）：`RQ-T-30` 验收 9。
    assert first["advisory_score"]["score"] == 4.0
    assert second["advisory_score"]["score"] == 8.0
    assert second["advisory_score"]["rationale"] == "第二版依据。"
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(sa.func.count())
            .select_from(evaluation_commentaries)
            .where(evaluation_commentaries.c.evaluation_run_id == uuid.UUID(run_id))
        ).scalar_one()
        after = connection.execute(
            sa.select(evaluation_results).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()
    assert int(rows) == 1, "重新生成是覆盖，不是追加"
    assert after is not None and before is not None
    assert (after.score, after.result_digest) == (before.score, before.result_digest)


@requires_postgres
def test_provider_failure_marks_only_the_commentary(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    with engine.connect() as connection:
        before = connection.execute(
            sa.select(evaluation_results.c.score, evaluation_results.c.result_digest).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()

    def _fail(*args: Any, **kwargs: Any) -> tuple[int, str]:
        return 502, "upstream exploded"

    monkeypatch.setattr(UrllibTransport, "post_json", _fail)
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
        read_back = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        )
    assert posted.status_code == 503, posted.text
    assert "HTTP 502" in posted.json()["detail"]
    body = read_back.json()
    assert body["status"] == "failed" and body["text"] is None
    with engine.connect() as connection:
        after = connection.execute(
            sa.select(evaluation_results.c.score, evaluation_results.c.result_digest).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()
    assert after == before, "provider 失败不得改变分数轨"


# --- 分数独立：删表重放 + 生成路径不在评测链路里 --------------------------------------------


@requires_postgres
def test_deleting_commentary_leaves_the_score_and_per_item_evidence_identical(
    engine: Engine, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """评语**存在**或**被删掉**都不改变分数、`result_digest` 与逐项证据。

    三步都做，缺一不可：

    1. 跑第一次（run A）；
    2. **在评语行仍在的情况下**重放第二次（run B）并与 A 比——这一步抓的是"报告/持久化路径
       按**提交**（而不是按运行）关联了评语"这类错误：那样 B 会把 A 的评语吃进摘要，
       `result_digest` 立刻与 A 不同；
    3. 清空评语表后再跑第三次（run C），A/B/C 三者仍然逐字相同——这一步才是用户要求的
       "删掉评语轨不影响可重放性"。

    因为这类污染最可能的形态是"摘要里多了一段文本"，所以比对里**包含 `result_digest`**：
    它涵盖 `overall`（含 `summary`）、逐项证据、绑定摘要与合计校验。
    """
    submission_id, question = _seed_submission(engine, world)
    root = tmp_path / "replay-workspaces"
    root.mkdir(exist_ok=True)

    def _run_once() -> Any:
        orchestrator = EvaluationOrchestrator(
            engine=engine,
            package_source=_PackageSource(_package_view()),
            workspace_provider=_Workspaces(root, {"main.py": CLEAN_SOURCE.encode("utf-8")}),
            # 无静态发现：带发现的提交今天**无法**重放（既有缺陷，见下面那条 xfail），
            # 若在这里带上发现，本用例就会变成"验证一个跑不通的场景"。
            security=_Security(_clean_security_decision()),
            runner=_Runner(),
        )
        return orchestrator.run(_envelope(submission_id, question, world))

    first = _run_once()
    # 让第一次运行**真的有**一条评语（直接落库：这里要验的是"表内容不影响分数"，不是生成本身）。
    # 带上参考分四列：`RQ-T-30` 验收 3 要求删掉评语表后重放同一个提交仍然逐字相同，
    # 而"评语表里的分数形状的内容"正是最容易被误读进 `result_digest` 的东西。
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO b2.evaluation_commentaries"
                " (evaluation_commentary_id, evaluation_run_id, status, text, provider, model,"
                "  prompt_version, input_scope, requested_at, generated_at, advisory_score,"
                "  advisory_max_score, advisory_rationale, advisory_confidence)"
                " VALUES (gen_random_uuid(), :run, 'ready', '一条评语', 'p', 'm', 'v',"
                "  CAST(:scope AS jsonb), now(), now(), 9, 10, :rationale, 'high')"
            ),
            {
                "run": first.evaluation_run_id,
                "scope": json.dumps(list(INPUT_SCOPE)),
                "rationale": ADVISORY_MARKER,
            },
        )
    second = _run_once()  # 评语行仍在：任何"按提交关联评语"的实现都会在这里露馅
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM b2.evaluation_commentaries"))
        remaining = connection.execute(
            sa.select(sa.func.count()).select_from(evaluation_commentaries)
        ).scalar_one()
        run_row = connection.execute(
            sa.select(evaluation_runs.c.state, evaluation_runs.c.submission_digest).where(
                evaluation_runs.c.evaluation_run_id == uuid.UUID(first.evaluation_run_id)
            )
        ).fetchone()
    assert int(remaining) == 0

    third = _run_once()
    assert run_row is not None
    for earlier, later in ((first, second), (first, third), (second, third)):
        assert (earlier.score, earlier.result_digest) == (later.score, later.result_digest)
        assert earlier.report is not None and later.report is not None
        assert earlier.report["overall"] == later.report["overall"]
        assert earlier.report["case_evidence"] == later.report["case_evidence"]
        assert earlier.report["binding"] == later.report["binding"]
        assert earlier.report["total_check"] == later.report["total_check"]
        assert report_case_evidence_digests(earlier.report) == report_case_evidence_digests(
            later.report
        )


def report_case_evidence_digests(report: Mapping[str, object]) -> list[object]:
    """逐项证据的稳定读数（用于跨运行比对；顺序即 ordinal 顺序）。"""
    items = report["case_evidence"]
    assert isinstance(items, list)
    return [
        {key: value for key, value in item.items() if key not in {"duration_ms", "resource"}}
        for item in items
    ]


@requires_postgres
@pytest.mark.xfail(
    strict=True,
    reason=(
        "既有缺陷（与本批无关，本批只把它钉住并上报）：`b2.security_findings` 的主键是"
        " finding 自身的内容派生 uuid（security/patterns.py 的 uuid5(规则,文件,行)），"
        "因此**同一提交第二次评测**必然在 `security_findings_pkey` 上撞唯一约束，异常从"
        " `EvaluationOrchestrator.run` 里直接冒出去（不是终态、也不是失败事件）。"
        "修法二选一：静态发现的主键改成'每次运行一行'（如 uuid5(run, finding)），"
        "或写入侧改成 ON CONFLICT DO NOTHING——前者改变'安全发现只有一份'的语义，"
        "后者会让第二个 run 看起来没有任何发现。**属需决策的改动**，故不在本批自行修改。"
    ),
)
def test_replaying_a_run_with_static_findings_is_blocked(
    engine: Engine, world: _World, tmp_path: pathlib.Path
) -> None:
    """带静态安全发现的提交重放第二次：期望**不再**撞主键（今天会撞，故本用例 xfail）。"""
    submission_id, question = _seed_submission(engine, world)
    root = tmp_path / "finding-replay-workspaces"
    root.mkdir(exist_ok=True)

    def _run_once() -> Any:
        orchestrator = EvaluationOrchestrator(
            engine=engine,
            package_source=_PackageSource(_package_view()),
            workspace_provider=_Workspaces(root, {"main.py": CLEAN_SOURCE.encode("utf-8")}),
            security=_Security(_security_decision()),
            runner=_Runner(),
        )
        return orchestrator.run(_envelope(submission_id, question, world))

    first = _run_once()
    second = _run_once()
    assert first.score == second.score


@requires_postgres
def test_evaluation_pipeline_never_calls_the_commentary_track(
    engine: Engine, world: _World, tmp_path: pathlib.Path, monkeypatch: Any
) -> None:
    """把生成入口换成"一调用就抛异常"的替身：评测链路必须照常跑完（依赖方向上的独立性）。"""
    submission_id, question = _seed_submission(engine, world)
    root = tmp_path / "independent-workspaces"
    root.mkdir(exist_ok=True)

    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("评测链路调用了评语轨")

    monkeypatch.setattr(CommentaryService, "generate", _explode)
    orchestrator = EvaluationOrchestrator(
        engine=engine,
        package_source=_PackageSource(_package_view()),
        workspace_provider=_Workspaces(root, {"main.py": CLEAN_SOURCE.encode("utf-8")}),
        security=_Security(_security_decision()),
        runner=_Runner(),
    )
    outcome = orchestrator.run(_envelope(submission_id, question, world))
    assert outcome.state == "completed"
    assert outcome.score == Decimal(6)
    assert outcome.report is not None
    with engine.connect() as connection:
        comment_count = connection.execute(
            sa.select(sa.func.count()).select_from(evaluation_commentaries)
        ).scalar_one()
    assert int(comment_count) == 0


@requires_postgres
def test_report_never_carries_a_commentary_value(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """生成评语与参考分之后，报告 / 结果行 / 导出 / 事件里都不得出现它们
    （含 `additionalProperties` 之外的位置）。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    marker = "COMMENTARY_TEXT_MARKER_只在评语表里"
    body = _generation_body(marker, _proposal(3.5, rationale=ADVISORY_MARKER))
    monkeypatch.setattr(UrllibTransport, "post_json", _provider_raw(body))
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        generated = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
        assert generated.status_code == 202, generated.text
        assert generated.json()["advisory_score"]["score"] == 3.5
        report = client.get(
            f"/api/v1/evaluation-runs/{run_id}/report",
            params={"view": "teacher"},
            headers=_auth(world.teacher_token(signer)),
        ).json()
        grades = client.get(
            "/api/v1/grades",
            params={"course_id": world.course},
            headers=_auth(world.teacher_token(signer)),
        )
        export = client.get(
            "/api/v1/grades/export",
            params={"course_id": world.course, "format": "csv"},
            headers=_auth(world.teacher_token(signer)),
        )
    assert grades.status_code == 200, grades.text
    assert export.status_code == 200, export.text
    for surface in (json.dumps(report, ensure_ascii=False), json.dumps(grades.json(), ensure_ascii=False)):
        assert marker not in surface, "评语文本进了报告或成绩查询"
        assert ADVISORY_MARKER not in surface, "参考分依据进了报告或成绩查询"
        assert "advisory" not in surface, "参考分键名进了报告或成绩查询"
    csv = export.content.decode("utf-8-sig")
    assert marker not in csv and ADVISORY_MARKER not in csv
    assert "advisory" not in csv
    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(evaluation_results.c.report).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()
        events = connection.execute(sa.select(outbox_events.c.payload)).fetchall()
    assert marker not in json.dumps(stored, ensure_ascii=False)
    assert ADVISORY_MARKER not in json.dumps(stored, ensure_ascii=False)
    assert events, "夹具没有产生任何事件 ⇒ 事件侧的断言将失去意义"
    for (payload,) in events:
        encoded = json.dumps(payload, ensure_ascii=False)
        assert marker not in encoded, "评语文本进了事件 payload"
        assert "advisory" not in encoded, "参考分进了事件 payload"


# --- 契约一致性（端点形状） ----------------------------------------------------------------


def _rewrite_external_refs(node: Any) -> Any:
    """把 `./common.yaml#/components/schemas/X` 改写成 `#/components/schemas/X`。

    校验器是把契约片段拼成一份自包含文档来跑的（见 `_commentary_validator`），因此外部引用必须
    指回同一份文档里的同名 schema——被引用的 schema **逐字取自契约文件**，不是重写。
    """
    if isinstance(node, dict):
        return {
            key: (
                re.sub(r"^\./common\.yaml#", "#", value)
                if key == "$ref" and isinstance(value, str)
                else _rewrite_external_refs(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_external_refs(item) for item in node]
    return node


def _commentary_validator() -> Any:
    """按 `b2.yaml` 的 `EvaluationCommentary` 构造校验器（引用从 `common.yaml` 原样带过来）。"""
    import jsonschema

    openapi = yaml.safe_load(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    common = yaml.safe_load(
        (CONTRACT_OPENAPI.parent / "common.yaml").read_text(encoding="utf-8")
    )
    schemas = {**common["components"]["schemas"], **openapi["components"]["schemas"]}
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_rewrite_external_refs(openapi["components"]["schemas"]["EvaluationCommentary"]),
        "components": {"schemas": _rewrite_external_refs(schemas)},
    }
    return jsonschema.Draft202012Validator(document)


@requires_postgres
def test_commentary_responses_validate_against_the_contract_schema(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """响应体逐字过契约 schema（不是"看起来像"）：**含参考分的那一份也要过**。"""
    validator = _commentary_validator()
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    monkeypatch.setattr(
        UrllibTransport,
        "post_json",
        _provider_response("评语正文。", _proposal(6, rationale="依据是三个用例里两个超时。")),
    )
    configured = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    plain = build_b2_runtime(_settings(database_url=_dsn(engine), signer=signer))
    with _client(configured) as client:
        ready = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        ).json()
    with _client(plain) as client:
        unavailable = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        ).json()
    # 带参考分的那一份：路径必须是 `advisory_score.score`（子对象，不是平级数字）。
    assert ready["advisory_score"] == {
        "score": 6.0,
        "max_score": EVIDENCE_MAX_SCORE,
        "rationale": "依据是三个用例里两个超时。",
        "confidence": "medium",
    }
    for body in (ready, unavailable):
        errors = sorted(validator.iter_errors(body), key=lambda error: list(error.path))
        assert errors == [], [error.message for error in errors]
    contract = yaml.safe_load(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    provenance = contract["components"]["schemas"]["CommentaryProvenance"]
    assert provenance["required"] == ["provider", "model", "prompt_version", "input_scope"]
    # 刻意的结构缺口：契约里**没有**平级 score / evidence 字段，实现也不许加。
    properties = contract["components"]["schemas"]["EvaluationCommentary"]["properties"]
    assert "score" not in properties and "evidence" not in properties
    # `RQ-T-30` 验收 2：它只能作为子对象被读到。
    assert "advisory_score" in properties
    advisory = contract["components"]["schemas"]["AdvisoryScore"]
    assert advisory["required"] == ["score", "max_score", "rationale", "confidence"]
    assert advisory["properties"]["max_score"]["exclusiveMinimum"] == 0


def test_provider_config_is_repr_safe() -> None:
    """凭据不得出现在配置对象的 repr 里（与 IMAP 口令、B3 凭据同一条规则）。"""
    config = CommentaryProviderConfig(
        provider="p", base_url="https://x.invalid/v1", model="m", api_key="super-secret"
    )
    assert "super-secret" not in repr(config)
    assert config.endpoint == "https://x.invalid/v1/chat/completions"
    provider = build_provider(config)
    assert provider.config.provider == "p"


def test_provider_error_never_echoes_the_response_body() -> None:
    """provider 失败的原因里不得回显响应正文（它可能回显请求=证据）。"""
    config = CommentaryProviderConfig(
        provider="p", base_url="https://x.invalid/v1", model="m", api_key="k"
    )

    class _Transport:
        def post_json(
            self,
            url: str,
            payload: Mapping[str, Any],
            headers: Mapping[str, str],
            timeout: float,
        ) -> tuple[int, str]:
            return 500, "PROVIDER_BODY_SHOULD_NOT_BE_ECHOED"

    provider = build_provider(config, transport=_Transport())
    with pytest.raises(CommentaryProviderError) as error:
        provider.complete("prompt")
    assert "PROVIDER_BODY_SHOULD_NOT_BE_ECHOED" not in str(error.value)
    assert "HTTP 500" in str(error.value)


# =============================================================================================
# AI 评分参考（`RQ-T-30` / `ADR-0014` / `CHG-0018` §7 的五条实现侧验证）
# =============================================================================================


def test_no_table_other_than_the_commentary_can_carry_an_advisory_value() -> None:
    """**§7.1 的结构侧**：`advisory` 形状的列只存在于评语表（纯结构断言，不需要数据库）。

    `ADR-0014` 决策 3 列出的六处（`evaluation_results`、`case_evidence`、报告、绑定摘要、
    成绩端点、事件）在存储层对应的是"这些表连一个 advisory 列都没有"。事件 payload 是 jsonb，
    由 `test_report_never_carries_a_commentary_value` 扫描实际内容来覆盖。
    """
    carriers = {
        table.name: sorted(column.name for column in table.columns if "advisory" in column.name)
        for table in metadata.tables.values()
    }
    carriers = {name: columns for name, columns in carriers.items() if columns}
    assert set(carriers) == {"evaluation_commentaries"}, carriers
    assert carriers["evaluation_commentaries"] == [
        "advisory_confidence",
        "advisory_max_score",
        "advisory_rationale",
        "advisory_score",
    ]


@requires_postgres
def test_advisory_score_is_rejected_by_the_evaluation_report_schema(
    engine: Engine, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """**§7.1 的 schema 侧**：真实报告过校验；给它加上 `advisory_score` 后必须被拒。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    runtime = build_b2_runtime(_settings(database_url=_dsn(engine), signer=signer))
    with _client(runtime) as client:
        report = client.get(
            f"/api/v1/evaluation-runs/{run_id}/report",
            params={"view": "teacher"},
            headers=_auth(world.teacher_token(signer)),
        ).json()
    assert report_schema_errors(report) == [], "夹具报告本身就不合契约 ⇒ 本断言失去意义"
    assert "advisory_score" not in report
    for injected in (
        {"advisory_score": {"score": 9, "max_score": 10, "rationale": "r", "confidence": "high"}},
        {"advisory_score": None},
    ):
        mutated = {**report, **injected}
        assert report_schema_errors(mutated) != [], "报告 schema 接受了 advisory_score"
    # 落库的那一份报告同样没有它。
    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(evaluation_results.c.report).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()
    assert "advisory" not in json.dumps(stored, ensure_ascii=False)


@requires_postgres
def test_advisory_max_score_is_filled_from_the_recorded_case_weights(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """**§7.5**：`max_score` 等于证据里各用例权重之和，**且**等于分数轨自己的满分。

    模型只提供 `score` / `rationale` / `confidence`——这一点由
    `test_a_model_supplied_max_score_is_refused_rather_than_used` 从反面钉住：
    模型给的满分根本没有进入数据的路径。
    """
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        UrllibTransport,
        "post_json",
        _provider_response("评语正文。", _proposal(7.5), calls=calls),
    )
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
    assert posted.status_code == 202, posted.text
    with engine.connect() as connection:
        weights = connection.execute(
            sa.select(case_evidence.c.weight)
            .where(case_evidence.c.evaluation_run_id == uuid.UUID(run_id))
            .order_by(case_evidence.c.ordinal)
        ).fetchall()
        stored_max = connection.execute(
            sa.select(evaluation_commentaries.c.advisory_max_score).where(
                evaluation_commentaries.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()
        track_max = connection.execute(
            sa.select(evaluation_results.c.max_score).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).scalar_one()
    evidence_total = sum((Decimal(str(row[0])) for row in weights), Decimal(0))
    assert [float(row[0]) for row in weights] == [6.0, 4.0], "夹具权重变了 ⇒ 预期值要跟着改"
    assert evidence_total == Decimal(10)
    assert posted.json()["advisory_score"]["max_score"] == float(evidence_total)
    assert Decimal(stored_max) == evidence_total, "落库的满分不是证据里的权重之和"
    assert Decimal(track_max) == evidence_total, "参考分的满分与分数轨的满分不一致"
    assert len(calls) == 1
    assert "不要给出满分" in calls[0]["payload"]["messages"][0]["content"]


@requires_postgres
def test_a_model_supplied_max_score_is_refused_rather_than_used(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """**§7.5 的反面**：模型自带 `max_score` 时整次生成失败——它不是被忽略、更不是被采用。

    这是"满分不是模型生成的"这句话的可执行形式：实现里不存在从模型 JSON 读满分的分支，
    因此多给一个键的响应只能得到 `failed`。
    """
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    proposal = {**_proposal(9), "max_score": 999}
    monkeypatch.setattr(
        UrllibTransport, "post_json", _provider_raw(_generation_body("评语正文。", proposal))
    )
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
        read_back = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        ).json()
    assert posted.status_code == 503, posted.text
    assert "advisory_score" in posted.json()["detail"]
    assert read_back["status"] == "failed"
    assert read_back["advisory_score"] is None
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(
                evaluation_commentaries.c.advisory_score,
                evaluation_commentaries.c.advisory_max_score,
            ).where(evaluation_commentaries.c.evaluation_run_id == uuid.UUID(run_id))
        ).fetchone()
    assert row is not None and (row[0], row[1]) == (None, None), "模型的满分进了存储"


@pytest.mark.parametrize("score", [-0.5, -1, 10.001, 11, 999])
@requires_postgres
def test_out_of_range_advisory_score_fails_the_generation_instead_of_clamping(
    engine: Engine,
    world: _World,
    signer: _Signer,
    monkeypatch: Any,
    tmp_path: pathlib.Path,
    score: float,
) -> None:
    """**§7.4**：`score` 越界 ⇒ 整次生成 `failed`，**不是**被截断到 `[0, max_score]`。

    断言里同时检查"越界值没有被写成边界值"：`failed` 行的参考分四列全空，
    因此既没有 10（上界）、也没有 0（下界）被悄悄写进去。
    """
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    with engine.connect() as connection:
        before = connection.execute(
            sa.select(evaluation_results).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()
    monkeypatch.setattr(
        UrllibTransport,
        "post_json",
        _provider_response("评语正文。", _proposal(score, rationale=ADVISORY_MARKER)),
    )
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
        read_back = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        ).json()
    assert posted.status_code == 503, posted.text
    detail = posted.json()["detail"]
    assert "越界" in detail and "不做截断" in detail, detail
    assert read_back["status"] == "failed"
    assert read_back["text"] is None and read_back["advisory_score"] is None
    # 越界的那次生成没有把任何参考分写进任何地方。
    assert ADVISORY_MARKER not in json.dumps(read_back, ensure_ascii=False)
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(
                evaluation_commentaries.c.status,
                evaluation_commentaries.c.advisory_score,
                evaluation_commentaries.c.advisory_max_score,
                evaluation_commentaries.c.advisory_rationale,
                evaluation_commentaries.c.advisory_confidence,
            ).where(evaluation_commentaries.c.evaluation_run_id == uuid.UUID(run_id))
        ).fetchone()
        after = connection.execute(
            sa.select(evaluation_results).where(
                evaluation_results.c.evaluation_run_id == uuid.UUID(run_id)
            )
        ).fetchone()
    assert row is not None
    assert row[0] == "failed"
    assert tuple(row[1:]) == (None, None, None, None), "越界的参考分被截断后写进了库"
    # 分数轨一个字节都没动（越界只影响这一条评语）。
    assert after == before


MALFORMED_MARKER = "MALFORMED_RESPONSE_MARKER_不得变成评语"


@pytest.mark.parametrize(
    "content",
    [
        # 旧协议（`b2-commentary/1`）的纯文本：必须失败，而不是"那就当评语用"。
        f"{MALFORMED_MARKER}：这是一段纯文本评语。",
        "[1, 2, 3]",
        '{"advisory_score": null}',
        f'{{"commentary": "{MALFORMED_MARKER}", "score": 5}}',
        (
            f'{{"commentary": "{MALFORMED_MARKER}", "advisory_score": {{"score": NaN,'
            ' "rationale": "r", "confidence": "low"}}}'
        ),
        (
            f'{{"commentary": "{MALFORMED_MARKER}", "advisory_score": {{"score": 5,'
            ' "rationale": "r", "confidence": "certain"}}}'
        ),
    ],
)
@requires_postgres
def test_malformed_model_response_is_a_failure_not_a_text_fallback(
    engine: Engine,
    world: _World,
    signer: _Signer,
    monkeypatch: Any,
    tmp_path: pathlib.Path,
    content: str,
) -> None:
    """**`CHG-0018` §5**：响应形状不符 ⇒ `failed`；**不得**退化成"把整段响应当评语"。"""
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    monkeypatch.setattr(UrllibTransport, "post_json", _provider_raw(content))
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        posted = client.post(
            COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
        )
        read_back = client.get(
            COMMENTARY.format(run=run_id), headers=_auth(world.student_token(signer))
        ).json()
    assert posted.status_code == 503, posted.text
    assert read_back["status"] == "failed"
    assert read_back["text"] is None, "格式错误的响应被当成了评语（静默降级）"
    assert read_back["advisory_score"] is None
    # 连失败原因里也不回显模型内容（它可能复述证据）。
    assert MALFORMED_MARKER not in json.dumps(read_back, ensure_ascii=False)
    assert MALFORMED_MARKER not in posted.json()["detail"]


@requires_postgres
def test_advisory_disclaimer_is_present_even_when_no_advisory_score_was_produced(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """**§7.7**：`disclaimers` 必须含一条针对分数参考的说明，**参考分为 null 时仍在**。

    两次生成都不产生参考分（一次不给该键、一次明确给 null），标注都必须出现——
    "客户端渲染参考分却不渲染标注"正是 `ADR-0014` 决策 5 要挡的事。
    """
    run_id, _ = _run_pipeline(engine, tmp_path, world)
    bodies = iter(
        ['{"commentary": "只有评语。"}', '{"commentary": "只有评语。", "advisory_score": null}']
    )

    def _post(*args: Any, **kwargs: Any) -> tuple[int, str]:
        return 200, json.dumps(
            {"choices": [{"message": {"content": next(bodies)}}]}, ensure_ascii=False
        )

    monkeypatch.setattr(UrllibTransport, "post_json", _post)
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        for _ in range(2):
            body = client.post(
                COMMENTARY.format(run=run_id), headers=_auth(world.teacher_token(signer))
            ).json()
            assert body["status"] == "ready" and body["advisory_score"] is None
            assert body["disclaimers"], "disclaimers 不得为空（契约 minItems 1）"
            assert any("参考分" in item and "不是成绩" in item for item in body["disclaimers"]), (
                body["disclaimers"]
            )
            assert set(body["disclaimers"]) <= set(DISCLAIMERS)


@requires_postgres
def test_advisory_score_row_does_not_change_the_replayed_score_or_digest(
    engine: Engine, world: _World, signer: _Signer, monkeypatch: Any, tmp_path: pathlib.Path
) -> None:
    """**§7.2**：一次**真实生成**出参考分的运行，删掉评语表全部内容后重放同一提交，
    分数、逐项证据、绑定摘要、合计校验与 `result_digest` 全部逐字相同。

    三个读数各有分工：第一次（有参考分行）与第二次（同一提交、参考分行仍在）比，抓的是
    "持久化路径按**提交**而不是按运行关联了评语"；清空评语表后的第三次才是用户要的
    "删掉评语轨不影响可重放性"。
    """
    submission_id, question = _seed_submission(engine, world)
    root = tmp_path / "advisory-replay-workspaces"
    root.mkdir(exist_ok=True)

    def _run_once() -> Any:
        orchestrator = EvaluationOrchestrator(
            engine=engine,
            package_source=_PackageSource(_package_view()),
            workspace_provider=_Workspaces(root, {"main.py": CLEAN_SOURCE.encode("utf-8")}),
            security=_Security(_clean_security_decision()),
            runner=_Runner(),
        )
        return orchestrator.run(_envelope(submission_id, question, world))

    first = _run_once()
    monkeypatch.setattr(
        UrllibTransport,
        "post_json",
        _provider_response("评语正文。", _proposal(9, rationale=ADVISORY_MARKER)),
    )
    runtime = build_b2_runtime(
        _settings(database_url=_dsn(engine), signer=signer, provider=True)
    )
    with _client(runtime) as client:
        generated = client.post(
            COMMENTARY.format(run=first.evaluation_run_id),
            headers=_auth(world.teacher_token(signer)),
        )
    assert generated.status_code == 202, generated.text
    assert generated.json()["advisory_score"]["max_score"] == EVIDENCE_MAX_SCORE

    second = _run_once()  # 参考分行仍在：任何"按提交关联评语"的实现都会在这里露馅
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM b2.evaluation_commentaries"))
        remaining = connection.execute(
            sa.select(sa.func.count()).select_from(evaluation_commentaries)
        ).scalar_one()
    assert int(remaining) == 0
    third = _run_once()

    for earlier, later in ((first, second), (first, third), (second, third)):
        assert (earlier.score, earlier.result_digest) == (later.score, later.result_digest)
        assert earlier.report is not None and later.report is not None
        assert earlier.report["overall"] == later.report["overall"]
        assert earlier.report["case_evidence"] == later.report["case_evidence"]
        assert earlier.report["binding"] == later.report["binding"]
        assert earlier.report["total_check"] == later.report["total_check"]
        assert report_case_evidence_digests(earlier.report) == report_case_evidence_digests(
            later.report
        )
        assert ADVISORY_MARKER not in json.dumps(later.report, ensure_ascii=False)
