"""参考解答验证（`CHG-0019` / `CHG-0023` / `ADR-0015`）的验收测试。

本文件回答四个"绿套件本身证明不了"的问题：

1. **它不是分、也不是存证**：端点自己**不落库**（服务层连引擎都没有），未通过**不产生存证**，
   存证记录失败时 `attestation_id` 为 `null` 而逐用例结论照旧返回。
2. **它不返回受保护内容**：响应键集合就是契约声明的九个；参考解答正文、隐藏用例输入与期望输出
   一处都不出现（并且参考解答正文是**脱敏名单**里的字段名）。
3. **失败不被伪装**：沙箱不可用 ⇒ `503` 且零存证（Windows 上这条走**真实** runner 与真实沙箱探测）；
   上游 `404`/`409` 与 `5xx` 分别映射成 `404`/`422`/`503`；内部一致性缺陷**不**归一成"用例失败"。
4. **执行只有一条路径**：本包不 import 任何能起进程的模块，逐用例执行只经注入的 `CaseRunner` 端口
   （生产装配是 `runner.run_case`），因此 `runner/policy.py` 的策略无法被绕过。

需要真实 PostgreSQL 的用例在"不落库"那条上用到库（**恰是为了断言库里没有东西**）；
纯结构断言不挂 `requires_postgres`，在没有库的环境里也真的在跑。

**未在真实 B3 上验证**（B3 侧 `reference-answer` 端点由另一工作项实现）：本文件用**传输替身**覆盖
`GET reference-answer` 的 `200 / 404 / 409 / 5xx / 坏 JSON` 与 `POST attestations` 的
`201 / 409 / 5xx / 坏 JSON`。**另外**：`GET evaluation-package` 对**草稿**返回 `409`
（`B3/domain/packages.py` 只在版本已发布时编译包），因此"验证草稿 → 存证 → 发布"这条端到端路径
在当前 B3 上跑不通；本文件用 `409` 分支的用例把它钉成**已知阻塞**，不假装它通过了。
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import pathlib
import re
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from urllib.parse import urlparse

import pytest
import sqlalchemy as sa
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from autograder_b2.api.errors import CONTENT_TYPE, install_problem_handlers
from autograder_b2.api.reference_routes import build_reference_router
from autograder_b2.auth.authorization import (
    AuthorizationError,
    require_reference_verification_authority,
)
from autograder_b2.auth.tokens import CourseScope, Principal
from autograder_b2.db.schema import EXPECTED_SCHEMA_REVISION
from autograder_b2.domain.evidence import (
    CaseEvidence,
    PackageUnavailableError,
    SandboxUnavailableError,
)
from autograder_b2.evaluation.orchestrator import DynamicExecutionGate
from autograder_b2.evaluation.ports import PackageView
from autograder_b2.integration.adapters import B3Endpoint, HttpPackageSource
from autograder_b2.logging.redaction import FORBIDDEN_FIELD_NAMES, sanitize_fields
from autograder_b2.reference import (
    FAILURE_CATEGORIES,
    B3VerificationClient,
    ReferenceAnswerUnavailable,
    ReferenceAnswerWorkspace,
    ReferenceVerificationNotFound,
    ReferenceVerificationNotPossible,
    ReferenceVerificationService,
    ReferenceVerificationUnavailable,
)
from autograder_b2.reference.b3 import UrllibB3Transport
from autograder_b2.runner.package import parse_evaluation_package
from autograder_b2.runtime import B2Runtime, build_b2_runtime
from tests.conftest import requires_postgres, run_alembic

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]
REFERENCE_SRC = B2_ROOT / "src" / "autograder_b2" / "reference"
CONTRACT_OPENAPI = B2_ROOT.parent / "MAIN" / "contracts" / "openapi" / "b2.yaml"

ISSUER = "autograder-b4"
AUDIENCE = "autograder"
BASE = datetime(2026, 9, 20, 9, 0, 0, tzinfo=UTC)
VERIFY = "/api/v1/reference-verifications"

VERSION = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-reference-version"))
CASE_ONE = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-reference-case-one"))
CASE_TWO = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-reference-case-two"))
ATTESTATION = str(uuid.uuid5(uuid.NAMESPACE_URL, "b2-reference-attestation"))

#: 参考解答正文里的标记串：**任何**响应、日志或库里都不得出现它。
ANSWER_MARKER = "REFERENCE_ANSWER_BODY_MARKER"
ANSWER_TEXT = f'print("{ANSWER_MARKER}")\n'
#: 隐藏用例期望输出里的标记串：同上（它随 evaluation package 进来，绝不出现在响应里）。
EXPECTED_MARKER = "HIDDEN_EXPECTED_OUTPUT_MARKER"
PACKAGE_DIGEST = "b" * 64
CONTENT_DIGEST = "a" * 64


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# --- 纯结构断言（不需要数据库，因此不挂 skip 标记） -----------------------------------------


def test_response_key_set_matches_the_contract_verbatim() -> None:
    """响应键集合**逐字**等于契约 `ReferenceVerification` 的声明（不是"看起来像"）。"""
    contract = yaml.safe_load(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    schema = contract["components"]["schemas"]["ReferenceVerification"]
    keys = set(schema["required"])
    assert keys == {
        "question_version_id",
        "content_digest",
        "language",
        "adapter_kind",
        "cases_total",
        "cases_passed",
        "cases",
        "verified_at",
        "attestation_id",
    }
    assert set(schema["properties"]) == keys, "契约声明了 required 之外的属性"
    assert schema["additionalProperties"] is False
    case_schema = schema["properties"]["cases"]["items"]
    assert set(case_schema["required"]) == {"ordinal", "passed", "failure_category"}
    assert case_schema["additionalProperties"] is False
    # 参考解答正文、隐藏用例与期望输出**不在**这份 schema 里（结构性的：契约没有它们的槽位）。
    for forbidden in ("reference_answer", "expected_stdout", "test_cases", "stdin"):
        assert forbidden not in schema["properties"]
        assert forbidden not in case_schema["properties"]


def test_failure_category_vocabulary_is_the_report_vocabulary() -> None:
    """`cases[].failure_category` 复用报告的词表（`CHG-0019` §2.6：不新造一套）。"""
    contract = yaml.safe_load(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    declared = contract["components"]["schemas"]["ReferenceVerification"]["properties"]["cases"][
        "items"
    ]["properties"]["failure_category"]
    values = {item for option in declared["anyOf"] for item in option.get("enum", [])}
    assert values == set(FAILURE_CATEGORIES) == {
        "timeout",
        "crash",
        "output_mismatch",
        "resource_limit",
        "missing_output",
        "import_error",
        "security_blocked",
        "infrastructure_error",
    }
    report = json.loads(
        (CONTRACT_OPENAPI.parent.parent / "schemas" / "evaluation-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    report_values = set(
        report["properties"]["case_evidence"]["items"]["properties"]["failure_category"]["enum"]
    )
    assert values == report_values - {None}, "与 evaluation-report 的失败类别词表不一致"


def test_reference_answer_is_a_forbidden_log_field() -> None:
    """`CHG-0023` §4：参考解答正文必须是脱敏名单里的字段名，且真的会被丢掉。"""
    assert "reference_answer" in FORBIDDEN_FIELD_NAMES
    sanitized = sanitize_fields({"reference_answer": ANSWER_MARKER, "stage": "reference"})
    assert "reference_answer" not in sanitized.fields
    assert ANSWER_MARKER not in json.dumps(sanitized.fields, ensure_ascii=False)


def test_reference_package_never_spawns_a_process() -> None:
    """执行只经注入的 `CaseRunner` 端口：本包**不 import** 任何能起进程的模块。

    这是"执行必须走 `runner/policy.py`"的结构侧：不 import `subprocess`/`multiprocessing`/`ctypes`，
    本包就没有第二条执行路径可走（行为侧由 `test_each_case_goes_through_the_injected_runner` 钉住）。
    """
    forbidden = {
        "subprocess",
        "multiprocessing",
        "ctypes",
        "pty",
        "posix_spawn",
    }
    files = sorted(REFERENCE_SRC.glob("*.py"))
    assert files, "参考解答验证包不存在 ⇒ 本断言失去意义"
    imported: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported & forbidden == set(), f"参考解答验证包可达进程创建路径：{imported & forbidden}"
    assert "autograder_b2" in imported


def test_service_has_no_storage_dependency() -> None:
    """**不落库**的结构侧：服务层连引擎都拿不到，写不了任何表。"""
    import inspect

    parameters = set(inspect.signature(ReferenceVerificationService.__init__).parameters)
    assert parameters == {
        "self",
        "reference_answers",
        "packages",
        "runner",
        "workspaces",
        "gate",
        "now",
    }
    for forbidden in ("engine", "connection", "session", "store", "outbox"):
        assert forbidden not in parameters, f"服务层拿到了存储依赖：{forbidden}"


def test_authorization_helper_rejects_students_and_scope_less_staff() -> None:
    """角色 + 课程作用域两层（第三层"该题目的课程"不可判定，见记录用例）。"""
    scope = CourseScope(course_id=str(uuid.uuid4()), section_id=None)
    for rejected in (
        Principal(user_id=str(uuid.uuid4()), roles=("student",), scopes=(scope,), token_id="t"),
        Principal(user_id=str(uuid.uuid4()), roles=(), scopes=(scope,), token_id="t"),
        Principal(user_id=str(uuid.uuid4()), roles=("teacher",), scopes=(), token_id="t"),
    ):
        with pytest.raises(AuthorizationError):
            require_reference_verification_authority(rejected)
    # 通过：教师带课程作用域；管理员无作用域也可（与其余端点同口径）。
    require_reference_verification_authority(
        Principal(user_id=str(uuid.uuid4()), roles=("teacher",), scopes=(scope,), token_id="t")
    )
    require_reference_verification_authority(
        Principal(user_id=str(uuid.uuid4()), roles=("admin",), scopes=(), token_id="t")
    )


# --- 替身：B3 传输 / 取包 / runner ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Script:
    """一次脚本化的上游响应：`(status, body)` 或一个要抛出的异常。"""

    response: tuple[int, str] | Exception

    def __call__(self, *_args: Any, **_kwargs: Any) -> tuple[int, str]:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _answer_body(**overrides: Any) -> str:
    document: dict[str, Any] = {
        "question_version_id": VERSION,
        "content_digest": CONTENT_DIGEST,
        "language": "python",
        "adapter_kind": "stdin",
        "reference_answer": ANSWER_TEXT,
    }
    document.update(overrides)
    return json.dumps(document, ensure_ascii=False)


def _attestation_body(attestation_id: str = ATTESTATION) -> str:
    return json.dumps(
        {
            "attestation_id": attestation_id,
            "question_version_id": VERSION,
            "content_digest": CONTENT_DIGEST,
            "verifier": "autograder-b2/reference-verification",
            "cases_total": 2,
            "cases_passed": 2,
            "verified_at": None,
        },
        ensure_ascii=False,
    )


class _Transport:
    """`B3Transport` 替身：按脚本响应，并记录每一次调用（**不记录参考解答**）。"""

    def __init__(
        self,
        *,
        answer: tuple[int, str] | Exception | None = None,
        attestation: tuple[int, str] | Exception | None = None,
    ) -> None:
        self.answer = _Script(answer if answer is not None else (200, _answer_body()))
        self.attestation = _Script(
            attestation if attestation is not None else (201, _attestation_body())
        )
        self.gets: list[str] = []
        self.posts: list[dict[str, Any]] = []
        #: 每次调用发生在**哪个线程**：自调用死锁的回归用例要看它（见 T-REF-028）。
        self.threads: list[threading.Thread] = []

    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float, limit: int
    ) -> tuple[int, str]:
        self.gets.append(url)
        self.threads.append(threading.current_thread())
        return self.answer(url, headers, timeout, limit)

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
        limit: int,
    ) -> tuple[int, str]:
        self.posts.append({"url": url, "payload": dict(payload), "headers": dict(headers)})
        return self.attestation(url, payload, headers, timeout, limit)


def _endpoint() -> B3Endpoint:
    return B3Endpoint(
        base_url="https://b3.test.invalid", authorization="Bearer test-service-credential"
    )


PACKAGE: dict[str, Any] = {
    "question_version_id": VERSION,
    "package_digest": PACKAGE_DIGEST,
    "language": "python",
    "adapter": {
        "kind": "stdin",
        "args": [],
        "stdin": None,
        "expect_exit_code": 0,
        "comparison": {"stdout": "exact", "stderr": "ignored"},
        "files": [],
    },
    "tests": [
        {
            "case_id": CASE_ONE,
            "name": "case one",
            "ordinal": 0,
            "weight": 1,
            "visible_to_student": False,
            "stdin": "1\n",
            "args": [],
            "expected_stdout": f"1\n{EXPECTED_MARKER}\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
        {
            "case_id": CASE_TWO,
            "name": "case two",
            "ordinal": 1,
            "weight": 1,
            "visible_to_student": False,
            "stdin": "2\n",
            "args": [],
            "expected_stdout": "2\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
    ],
    "security_policy": {
        "network": False,
        "read_only_root": True,
        "allow_root": False,
        "max_memory_bytes": 33554432,
        "max_cpu_seconds": 5,
        "max_wall_seconds": 10,
        "max_output_bytes": 65536,
        "max_processes": 8,
    },
    "max_files": 10,
    "max_total_bytes": 100000,
    "created_at": "2026-09-20T00:00:00Z",
}


def _package_view(**overrides: Any) -> PackageView:
    """真实解析 + **生产映射**（`HttpPackageSource._view`）的 package 视图，不发任何请求。

    复用生产映射而不是在测试里再写一份句柄接线：`ports` 的接线约定只有一处定义，
    测试里有第二份就迟早会与它漂移（`run_case` 对三个句柄做**严格相等**校验）。
    """
    payload = {**PACKAGE, **overrides}
    return HttpPackageSource(_endpoint())._view(parse_evaluation_package(payload))


class _PackageSource:
    """`PackageSource` 替身：返回既定视图，或抛出带状态码（与上游原因）的 `PackageUnavailableError`。"""

    def __init__(
        self,
        *,
        view: PackageView | None = None,
        status_code: int | None = None,
        upstream_detail: str | None = None,
    ) -> None:
        self.view = view if view is not None else _package_view()
        self.status_code = status_code
        self.upstream_detail = upstream_detail
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, evaluation_run_id: str, question_version_id: str) -> PackageView:
        self.calls.append((evaluation_run_id, question_version_id))
        if self.status_code is not None:
            raise PackageUnavailableError(
                f"evaluation package endpoint returned status {self.status_code}",
                status_code=self.status_code,
                upstream_detail=self.upstream_detail,
            )
        return self.view


class _Runner:
    """`CaseRunner` 替身：按 ordinal 决定通过与否，并观察工作区里被物化出来的入口脚本。"""

    def __init__(
        self,
        *,
        outcomes: Mapping[int, tuple[bool, str | None]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.outcomes = dict(outcomes or {0: (True, None), 1: (True, None)})
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def run_case(self, package: object, submission: Any) -> CaseEvidence:
        ordinal = int(submission.case.ordinal)
        workspace = pathlib.Path(submission.workspace)
        files = sorted(path.name for path in workspace.iterdir()) if workspace.is_dir() else []
        entry = workspace / "main.py"
        self.calls.append(
            {
                "ordinal": ordinal,
                "package": package,
                "files": files,
                "entry_text": entry.read_text(encoding="utf-8") if entry.is_file() else None,
                "workspace": workspace,
            }
        )
        if self.error is not None:
            raise self.error
        passed, category = self.outcomes[ordinal]
        return CaseEvidence(
            case_id=str(submission.case.case_id),
            ordinal=ordinal,
            weight=float(submission.case.weight),
            passed=passed,
            failure_category=category,  # type: ignore[arg-type]
            input_digest="c" * 64,
            stdout_digest="d" * 64,
            stderr_digest="e" * 64,
            actual_output_excerpt=None,
            expected_output_excerpt=None,
            exit_code=0 if passed else 1,
            duration_ms=7,
            resource_limit_hit=None,
            sandbox_violations=(),
            message=None,
            evidence_digest="f" * 64,
        )


# --- 真实 PostgreSQL 的夹具 ----------------------------------------------------------------


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

    def foreign_teacher_token(self, signer: _Signer) -> str:
        """另一门课的教师：**本部署判定不了"该题目的课程"**，因此这名调用者会被放行（已知缺口）。"""
        return signer.token(
            user_id=str(uuid.uuid4()),
            roles=("teacher",),
            scope=[{"course_id": self.other_course}],
        )

    def scopes_less_teacher_token(self, signer: _Signer) -> str:
        return signer.token(user_id=str(uuid.uuid4()), roles=("teacher",), scope=[])


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
    """迁移到本构建声明的 revision：**唯一用途**是让"库里没有东西"这条断言可执行。"""
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


def _settings(database_url: str, signer: _Signer, workspace_root: pathlib.Path) -> dict[str, str]:
    return {
        "DATABASE_URL": database_url,
        "B2_ARTIFACT_ROOT": str(workspace_root / "artifacts"),
        "B2_WORKSPACE_ROOT": str(workspace_root / "workspaces"),
        "B2_B3_BASE_URL": "https://b3.test.invalid",
        "B2_B3_AUTHORIZATION": "Bearer test-service-credential",
        "B2_ENVIRONMENT_TAG": "test-image@sha256:0000",
        "B2_JWKS_JSON": json.dumps(signer.jwks),
    }


def _service(
    *,
    transport: _Transport,
    packages: _PackageSource | None = None,
    runner: _Runner | None = None,
    workspace_root: pathlib.Path,
    now: datetime = BASE,
) -> ReferenceVerificationService:
    """用替身装配服务层（上游全是替身；服务层本身是生产实现）。"""
    return ReferenceVerificationService(
        reference_answers=B3VerificationClient(_endpoint(), transport=transport),
        packages=packages if packages is not None else _PackageSource(),
        runner=runner if runner is not None else _Runner(),
        workspaces=ReferenceAnswerWorkspace(root=workspace_root),
        gate=DynamicExecutionGate(),
        now=lambda: now,
    )


def _client(runtime: B2Runtime) -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_reference_router(runtime))
    return TestClient(app)


def _wired(
    database_url: str,
    signer: _Signer,
    workspace_root: pathlib.Path,
    *,
    transport: _Transport | None = None,
    packages: _PackageSource | None = None,
    runner: _Runner | None = None,
    now: datetime = BASE,
) -> tuple[B2Runtime, TestClient]:
    """`build_b2_runtime` 走**真实装配**，只把三个上游端口换成替身。

    这样"装配是否真的把 B3 通道与 workspace root 接上"也被覆盖，而不是只有服务层单测。
    """
    runtime = build_b2_runtime(_settings(database_url, signer, workspace_root))
    runtime.references = _service(
        transport=transport if transport is not None else _Transport(),
        packages=packages,
        runner=runner,
        workspace_root=workspace_root / "workspaces",
        now=now,
    )
    return runtime, _client(runtime)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(client: TestClient, token: str, version: str = VERSION) -> Any:
    return client.post(VERIFY, json={"question_version_id": version}, headers=_auth(token))


# --- 鉴权 ----------------------------------------------------------------------------------


@requires_postgres
def test_unauthenticated_request_is_401(database_url: str, signer: _Signer, tmp_path: pathlib.Path
                                        ) -> None:
    _, client = _wired(database_url, signer, tmp_path)
    with client:
        response = client.post(VERIFY, json={"question_version_id": VERSION})
        assert response.status_code == 401, response.text
        assert response.headers["content-type"].startswith(CONTENT_TYPE)


@requires_postgres
def test_students_and_scope_less_staff_are_forbidden(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """学生一律 `403`；没有课程作用域的 staff 令牌同样 `403`（认证不等于授权）。"""
    _, client = _wired(database_url, signer, tmp_path)
    with client:
        student = _post(client, world.student_token(signer))
        assert student.status_code == 403, student.text
        assert student.headers["content-type"].startswith(CONTENT_TYPE)
        scope_less = _post(client, world.scopes_less_teacher_token(signer))
        assert scope_less.status_code == 403, scope_less.text


@requires_postgres
def test_course_scope_gap_is_recorded_not_hidden(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """**已知缺口，如实钉住**：另一门课的教师当前会被放行。

    契约要的是"该题目的课程写权限"，而"题目属于哪门课"是 B3 的事实（`b3.question.course_id`）：
    请求里只有 `question_version_id`，B2 调 B3 用的又是服务令牌，因此第三层判定**做不了**。
    这条用例不把它写成"已收窄"——它是缺口本身（与隔离区复核、质量汇总同一处缺口）。
    """
    _, client = _wired(database_url, signer, tmp_path)
    with client:
        response = _post(client, world.foreign_teacher_token(signer))
        assert response.status_code == 200, response.text


# --- 请求体 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"question_version_id": "%s", "extra": 1}' % VERSION.encode(),
        b"{}",
        b'{"question_version_id": 42}',
        b'{"question_version_id": "not-a-uuid"}',
    ],
)
@requires_postgres
def test_request_body_is_validated(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path, body: bytes
) -> None:
    _, client = _wired(database_url, signer, tmp_path)
    with client:
        response = client.post(
            VERIFY,
            content=body,
            headers={**_auth(world.teacher_token(signer)), "content-type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.headers["content-type"].startswith(CONTENT_TYPE)


# --- 通过路径 ------------------------------------------------------------------------------


@requires_postgres
def test_all_cases_passing_records_the_attestation(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """`cases_passed == cases_total` ⇒ 逐用例结论 + 与 `content_digest` 绑定的存证 id。"""
    transport = _Transport()
    runner = _Runner()
    _, client = _wired(database_url, signer, tmp_path, transport=transport, runner=runner)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question_version_id"] == VERSION
    assert body["content_digest"] == CONTENT_DIGEST
    assert body["language"] == "python"
    assert body["adapter_kind"] == "stdin"
    assert body["cases_total"] == 2 and body["cases_passed"] == 2
    assert body["attestation_id"] == ATTESTATION
    assert [case["ordinal"] for case in body["cases"]] == [0, 1]
    assert all(case["passed"] is True and case["failure_category"] is None for case in body["cases"])
    # `verified_at` 与送给 B3 的存证**逐字相同**（两处说的是同一时刻，不是两个时刻）。
    assert body["verified_at"] == "2026-09-20T09:00:00.000Z"
    assert len(transport.posts) == 1
    attestation = transport.posts[0]
    assert attestation["payload"]["content_digest"] == CONTENT_DIGEST
    assert attestation["payload"]["verified_at"] == body["verified_at"]
    assert attestation["payload"]["cases_total"] == 2
    assert attestation["payload"]["cases_passed"] == 2
    assert attestation["payload"]["verifier"] == "autograder-b2/reference-verification"
    # 两个入口 URL：取参考解答与记录存证共用同一个 base_url（没有第二个通道）。
    assert len(transport.gets) == 1
    assert urlparse(transport.gets[0]).netloc == "b3.test.invalid"
    assert urlparse(attestation["url"]).netloc == "b3.test.invalid"


@requires_postgres
def test_each_case_goes_through_the_injected_runner(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """逐用例执行只经 `CaseRunner` 端口；每个用例一份新工作区，里面是**参考解答正文**。"""
    runner = _Runner()
    _, client = _wired(database_url, signer, tmp_path, runner=runner)
    with client:
        assert _post(client, world.teacher_token(signer)).status_code == 200
    assert [call["ordinal"] for call in runner.calls] == [0, 1]
    assert [call["files"] for call in runner.calls] == [["main.py"], ["main.py"]]
    assert all(call["entry_text"] == ANSWER_TEXT for call in runner.calls)
    workspaces = {call["workspace"] for call in runner.calls}
    assert len(workspaces) == 2, "每个用例必须各有一份工作区（绝不复用）"
    # 用完即删：验证结束后工作区里不留任何文件（受保护正文不留在盘上）。
    for workspace in workspaces:
        assert not workspace.exists(), f"工作区未被回收：{workspace.name}"


@requires_postgres
def test_a_failed_case_produces_no_attestation(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """未通过 ⇒ **零存证**，但逐用例失败类别照实返回（`ADR-0015` 决策 4）。"""
    transport = _Transport()
    runner = _Runner(outcomes={0: (True, None), 1: (False, "output_mismatch")})
    _, client = _wired(database_url, signer, tmp_path, transport=transport, runner=runner)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cases_passed"] == 1 and body["cases_total"] == 2
    assert body["attestation_id"] is None
    assert body["cases"][1] == {"ordinal": 1, "passed": False, "failure_category": "output_mismatch"}
    assert transport.posts == [], "未通过的验证不得产生任何存证"


@requires_postgres
def test_security_blocked_case_is_reported_without_persisting(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path, engine: Engine
) -> None:
    """参考答案被沙箱判定违规：只报告类别，**不写 `security_findings`**（本端点不落库）。"""
    transport = _Transport()
    runner = _Runner(outcomes={0: (False, "security_blocked"), 1: (True, None)})
    _, client = _wired(
        database_url, signer, tmp_path, transport=transport, runner=runner
    )
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 200, response.text
    assert response.json()["cases"][0]["failure_category"] == "security_blocked"
    assert response.json()["attestation_id"] is None
    assert transport.posts == []
    with engine.connect() as connection:
        findings = connection.execute(
            text("SELECT count(*) FROM b2.security_findings")
        ).scalar_one()
    assert int(findings) == 0


@pytest.mark.parametrize(
    "attestation",
    [
        (409, '{"detail": "only a draft version accepts an attestation"}'),
        (500, "upstream exploded"),
        "network",
    ],
)
@requires_postgres
def test_attestation_recording_failure_returns_null_but_still_returns_the_cases(
    database_url: str,
    world: _World,
    signer: _Signer,
    tmp_path: pathlib.Path,
    attestation: Any,
) -> None:
    """**记录失败** ⇒ `200` + `attestation_id: null` + 逐用例结论照旧（契约明文）。"""
    script: tuple[int, str] | Exception
    if attestation == "network":
        script = ReferenceAnswerUnavailable("B3 request failed: URLError")
    else:
        script = attestation
    transport = _Transport(attestation=script)
    _, client = _wired(database_url, signer, tmp_path, transport=transport)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attestation_id"] is None
    assert body["cases_total"] == 2 and body["cases_passed"] == 2
    assert len(body["cases"]) == 2
    assert len(transport.posts) == 1, "存证请求发出过一次（失败在记录侧，不在调用侧）"


@pytest.mark.parametrize(
    "attestation", [(201, "not json"), (201, '{"question_version_id": "x"}'), (201, "[]")]
)
@requires_postgres
def test_an_unreadable_attestation_response_is_a_dependency_failure(
    database_url: str,
    world: _World,
    signer: _Signer,
    tmp_path: pathlib.Path,
    attestation: tuple[int, str],
) -> None:
    """上游说"记下了"却给不出 `attestation_id` ⇒ `503`，**不**报成 `null`。

    `attestation_id: null` 的契约含义是"B3 **没有**记录"。把"记了但读不回来"写成 `null`
    会让一次上游故障变成一句关于存证状态的假话；因此它与"记录失败"分开处理。
    """
    transport = _Transport(attestation=attestation)
    _, client = _wired(database_url, signer, tmp_path, transport=transport)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    assert response.json()["status"] == 503


# --- 依赖不可用 ----------------------------------------------------------------------------


@requires_postgres
def test_sandbox_unavailable_is_503_with_no_attestation(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """沙箱不可用 ⇒ `503`、**零存证**、请求方拿不到任何"通过"的读数。

    并断言它**不计入** `record_failure()`：那是**已被文档化的平台条件**（本机 Windows 没有 nsjail，
    `ADR-0004` 禁止降级为裸进程），不是"未预期依赖失败"。算进去会让 readiness 的累计诊断在这台机器上
    永远脏着——而"这台机器跑不了参考解答验证"已经由 503 的 detail 与服务层日志如实表达。
    """
    transport = _Transport()
    runner = _Runner(error=SandboxUnavailableError("no nsjail on this host"))
    runtime, client = _wired(database_url, signer, tmp_path, transport=transport, runner=runner)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith(CONTENT_TYPE)
    assert "sandbox" in response.json()["detail"]
    assert transport.posts == [], "沙箱不可用时不得产生存证"
    assert runtime.failure_count == 0, "已文档化的平台条件不得算作未预期依赖失败"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((404, '{"detail": "not found"}'), 404),
        ((409, '{"detail": "no reference answer"}'), 422),
        ((500, "boom"), 503),
        ((403, '{"detail": "forbidden"}'), 503),
        ("network", 503),
        ((200, "not json"), 503),
        ((200, "[]"), 503),
        ((200, '{"question_version_id": "not-a-uuid"}'), 503),
        ((200, _answer_body(content_digest="zz")), 503),
        ((200, _answer_body(language="ruby")), 503),
        ((200, _answer_body(adapter_kind="magic")), 503),
    ],
)
@requires_postgres
def test_reference_answer_upstream_failures_map_to_contract_statuses(
    database_url: str,
    world: _World,
    signer: _Signer,
    tmp_path: pathlib.Path,
    answer: Any,
    expected: int,
) -> None:
    """取参考解答的每一条失败路径都映射到契约声明的状态码，且**零存证**。"""
    script: tuple[int, str] | Exception
    if answer == "network":
        script = ReferenceAnswerUnavailable("B3 request failed: URLError")
    else:
        script = answer
    transport = _Transport(answer=script)
    _, client = _wired(database_url, signer, tmp_path, transport=transport)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == expected, response.text
    assert transport.posts == []


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(404, 404), (409, 422), (500, 503), (403, 503)],
)
@requires_postgres
def test_evaluation_package_failures_map_to_contract_statuses(
    database_url: str,
    world: _World,
    signer: _Signer,
    tmp_path: pathlib.Path,
    status_code: int,
    expected: int,
) -> None:
    """取包失败同样按状态码分流：`404` 与 `409` 是**两件不同的事**。

    `409` 正是**草稿**的形态（B3 只在版本已发布时编译包），因此这条用例同时把
    "验证草稿在当前 B3 上不可行"钉成已知阻塞，而不是让它看起来成功了。
    """
    transport = _Transport()
    packages = _PackageSource(status_code=status_code)
    _, client = _wired(database_url, signer, tmp_path, transport=transport, packages=packages)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == expected, response.text
    assert transport.posts == []
    if status_code == 409:
        assert "evaluation package" in response.json()["detail"]


@requires_postgres
def test_language_disagreement_between_upstreams_is_503(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """两处上游对"这个版本用什么语言"说法不一致 ⇒ `503`（挑一个继续会跑错解释器）。"""
    transport = _Transport()
    packages = _PackageSource(view=_package_view(language="bash"))
    runtime, client = _wired(database_url, signer, tmp_path, transport=transport, packages=packages)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 503, response.text
    assert transport.posts == []
    # 与"本平台没有沙箱"相反的半边：这是真正的**未预期**依赖故障（上游自相矛盾），必须计数——
    # 否则上面那条"不计入"的断言可能因为"路由压根不计数"而恒真。
    assert runtime.failure_count == 1, "未预期的依赖不可用必须计入累计诊断"


# --- 上游 409 的原因必须**转述**，不得自造 ---------------------------------------------------


def _problem_body(detail: str, *, status: int = 409) -> str:
    return json.dumps(
        {
            "type": f"https://autograder.local/problems/http-{status}",
            "title": "Conflict",
            "status": status,
            "detail": detail,
            "instance": "/api/v1/question-versions/x",
        }
    )


#: 真实 B3 对草稿的 `reference-answer` 说过的话（MAIN 在真实宿主上实测的原文）。
B3_NO_DIGEST_REASON = (
    "this version has no content digest yet, so an answer executed against it could not be "
    "attested: a version needs at least one test case"
)
#: 真实 B3 对草稿的 `evaluation-package` 说过的话。
B3_DRAFT_PACKAGE_REASON = "a draft version has no evaluation package; publish it first"


@requires_postgres
def test_a_reference_answer_refusal_relays_the_upstream_reason(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """**回归用例（真实宿主发现的那条）**：`409` 必须转述 B3 的原因，不得自造一句。

    曾经这里对**任何** `409` 都返回 "the question version exists but carries no reference answer
    to verify"。而实测的草稿 `409` 说的是"这个版本还没有 `content_digest`"——版本里**明明有**
    参考解答。那句话是**假的**，而且会让教师去做一件错的事（去写答案，而不是先给版本加用例）。
    """
    transport = _Transport(answer=(409, _problem_body(B3_NO_DIGEST_REASON)))
    _, client = _wired(database_url, signer, tmp_path, transport=transport)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert B3_NO_DIGEST_REASON in detail, "上游的原因没有被转述"
    for invented in ("carries no reference answer", "no reference answer to verify"):
        assert invented not in detail, f"自造了一句上游没说过的话：{invented}"
    assert transport.posts == [], "被拒的验证不得产生存证"


@requires_postgres
def test_a_reference_answer_refusal_without_a_reason_does_not_guess(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """上游没给原因时**也不猜**：只说 `409` 本身成立的事实。"""
    transport = _Transport(answer=(409, '{"title": "Conflict"}'))
    _, client = _wired(database_url, signer, tmp_path, transport=transport)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "without a reason" in detail
    assert "no reference answer" not in detail
    assert "not been published" not in detail


@requires_postgres
def test_a_package_refusal_relays_the_upstream_reason(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """取包的 `409` 同样转述 B3（"草稿没有包"与"摘要与发布时记录不符"要教师做的事不同）。"""
    transport = _Transport()
    packages = _PackageSource(status_code=409, upstream_detail=B3_DRAFT_PACKAGE_REASON)
    _, client = _wired(database_url, signer, tmp_path, transport=transport, packages=packages)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 422, response.text
    assert B3_DRAFT_PACKAGE_REASON in response.json()["detail"]
    assert transport.posts == []


# --- 不返回受保护内容 ----------------------------------------------------------------------


@requires_postgres
def test_response_carries_no_protected_content(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """响应里**没有**参考解答正文、隐藏用例输入与期望输出；且逐字过契约 schema。"""
    _, client = _wired(database_url, signer, tmp_path)
    with client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 200, response.text
    raw = response.text
    for marker in (ANSWER_MARKER, EXPECTED_MARKER):
        assert marker not in raw, f"受保护内容出现在响应里：{marker}"
    body = response.json()
    assert set(body) == {
        "question_version_id",
        "content_digest",
        "language",
        "adapter_kind",
        "cases_total",
        "cases_passed",
        "cases",
        "verified_at",
        "attestation_id",
    }
    for case in body["cases"]:
        assert set(case) == {"ordinal", "passed", "failure_category"}
    validator = _contract_validator("ReferenceVerification")
    errors = sorted(validator.iter_errors(body), key=lambda error: list(error.path))
    assert errors == [], [error.message for error in errors]


def _contract_validator(name: str) -> Any:
    """按 `b2.yaml` 的某个 schema 构造校验器（引用从 `common.yaml` 原样带过来）。"""
    import jsonschema

    openapi = yaml.safe_load(CONTRACT_OPENAPI.read_text(encoding="utf-8"))
    common = yaml.safe_load(
        (CONTRACT_OPENAPI.parent / "common.yaml").read_text(encoding="utf-8")
    )
    schemas = {**common["components"]["schemas"], **openapi["components"]["schemas"]}
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_rewrite_external_refs(openapi["components"]["schemas"][name]),
        "components": {"schemas": _rewrite_external_refs(schemas)},
    }
    return jsonschema.Draft202012Validator(document)


def _rewrite_external_refs(node: Any) -> Any:
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


# --- 不落库 --------------------------------------------------------------------------------


@requires_postgres
def test_verification_writes_nothing_to_the_database(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path, engine: Engine
) -> None:
    """契约的"**不落库**"是可执行的读数：跑完一次**全通过**的验证后 `b2` 里没有新行。"""
    _, client = _wired(database_url, signer, tmp_path)
    with client:
        assert _post(client, world.teacher_token(signer)).status_code == 200
    with engine.connect() as connection:
        tables = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'b2' AND table_type = 'BASE TABLE'"
                    "   AND table_name <> 'alembic_version_b2'"
                )
            ).fetchall()
        ]
        assert tables, "库结构缺失 ⇒ 本断言失去意义"
        for table in tables:
            count = connection.execute(text(f'SELECT count(*) FROM b2."{table}"')).scalar_one()
            assert int(count) == 0, f"验证过程向 {table} 写了 {count} 行"
    # 工作区根下也不留目录（受保护正文不驻留）。
    assert not any((tmp_path / "workspaces").glob("*"))


# --- 内部缺陷不被伪装 ----------------------------------------------------------------------


@requires_postgres
def test_an_inconsistent_evidence_row_is_a_defect_not_a_case_failure(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """`passed=True` 却带失败类别（runner 缺陷）⇒ **不归一**，由宿主记 500。

    把它悄悄改成 `failure_category: null` 会让一次内部故障看起来像一次正常的参考答案通过。
    """
    runner = _Runner(outcomes={0: (True, "output_mismatch"), 1: (True, None)})
    runtime, _ = _wired(database_url, signer, tmp_path, runner=runner)
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_reference_router(runtime))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 500, response.text


# --- 装配：共用通道与闸门 ------------------------------------------------------------------


@requires_postgres
def test_wiring_shares_the_b3_channel_and_the_sandbox_gate(
    database_url: str, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """`ADR-0015`：取包、取参考解答、记录存证走**同一个** `B3Endpoint`；并发闸门也只有**一个**。

    断言而不是"读代码看出来"：两处调用共用 base_url/authorization ⇒ 没有新增凭据或配置项；
    共用闸门 ⇒ 单并发沙箱上限对评测与验证一起生效。
    """
    runtime = build_b2_runtime(
        _settings(database_url, signer, tmp_path)
    )
    assert runtime.references is not None and runtime.evaluator is not None
    assert runtime.b3_endpoint is not None
    assert runtime.references.b3_endpoint is runtime.b3_endpoint
    assert runtime.references.b3_endpoint.base_url == "https://b3.test.invalid"
    assert runtime.references.gate is runtime.evaluator.gate
    assert runtime.references.gate.capacity == 1
    assert runtime.references_unavailable() is None


@requires_postgres
def test_runtime_without_the_chain_reports_503_for_the_verification_route(
    database_url: str, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """没配 B3 通道 ⇒ 该端点 `503` 且原因可读（不返回一个"看起来跑过"的空结果）。"""
    settings = _settings(database_url, signer, tmp_path)
    settings.pop("B2_B3_BASE_URL")
    settings.pop("B2_B3_AUTHORIZATION")
    runtime = build_b2_runtime(settings)
    assert runtime.references is None
    reason = runtime.references_unavailable()
    assert reason is not None
    with _client(runtime) as client:
        response = client.post(VERIFY, json={"question_version_id": VERSION})
    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith(CONTENT_TYPE)


@requires_postgres
def test_the_blocking_b3_call_runs_off_the_event_loop(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path
) -> None:
    """**自调用死锁的回归用例**（真实端口上发现的那个 100% 不可用缺陷）。

    机制：本端点会同步 HTTP 请求 `B2_B3_BASE_URL`，而在组合宿主里那个地址就是**正在服务本请求的
    同一个进程**。若在事件循环里直接调 `verify()`，宿主就没机会去服务那个嵌套请求 ⇒ 自我等待到
    超时（真实端口实测：`503 B3 request failed: TimeoutError`，10.03s ≈ 包超时 10s，而同一个 B3
    端点从外部调不到 1s）。因此"**阻塞调用必须离开事件循环线程**"就是那条会决定生死的性质。

    本用例把它钉成可断言的事实，而不是靠读代码：让中间件记下**处理本请求的线程**（事件循环线程），
    再让 B3 传输替身记下**它被调用的线程**——两者必须不同。用替身而不是真 HTTP，是为了让这条断言
    **确定且快**：真 HTTP 版本在回归时会挂到超时，而这里立刻失败。

    **它覆盖不到的部分**（如实说明）：真实 uvicorn + 真实自调用的端到端超时只能在真实宿主上观察，
    本地没有这条链路（B2 的 venv 里没有 uvicorn，而本模块不得新增依赖）；那部分由 MAIN 的真实端口
    验收兜住，读数记在 `docs/phases/active/phase-07-reference-verification.md`。
    """
    transport = _Transport()
    runtime = build_b2_runtime(_settings(database_url, signer, tmp_path))
    runtime.references = _service(
        transport=transport, workspace_root=tmp_path / "workspaces"
    )
    app = FastAPI()
    install_problem_handlers(app)
    loop_threads: list[threading.Thread] = []

    @app.middleware("http")
    async def _record_the_serving_thread(request: Any, call_next: Any) -> Any:
        # 中间件与本路由的协程都跑在**事件循环线程**上。
        loop_threads.append(threading.current_thread())
        return await call_next(request)

    app.include_router(build_reference_router(runtime))
    with TestClient(app) as client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 200, response.text
    assert loop_threads, "中间件没被调用 ⇒ 本断言失去意义"
    assert transport.threads, "B3 传输替身没被调用 ⇒ 本断言失去意义"
    assert len(set(transport.threads)) == 1
    assert transport.threads[0] is not loop_threads[0], (
        "阻塞的 B3 调用跑在事件循环线程上：在真实宿主上这里会自我等待到超时"
    )


# --- Windows：真实 runner + 真实沙箱探测 ----------------------------------------------------


class _FakeUrlopen:
    """最小的 `urlopen` 替身：只服务取包那一次 GET（**真实**解析链与 runner 都要跑）。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __call__(self, request: Any, timeout: float | None = None) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    @property
    def status(self) -> int:
        return 200

    def read(self, limit: int = -1) -> bytes:
        return self._body


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "本用例断言的是'无沙箱的平台'：只有 Windows 上 nsjail 必然不可用。"
        "Linux 上的真实沙箱执行属 tests/linux/ 的验收。"
    ),
)
@requires_postgres
def test_on_a_platform_without_a_sandbox_the_real_runner_returns_503(
    database_url: str, world: _World, signer: _Signer, tmp_path: pathlib.Path,
    monkeypatch: Any,
) -> None:
    """`CHG-0019` §7.6 走**真实链路**：真实取包解析 + 真实 `run_case` + 真实沙箱探测。

    只把两个 HTTP 边界换成替身（B3 传输与 `urlopen`），因此这条用例证明的是
    "生产装配本身在无沙箱平台上返回 503 且零存证"，而不是"某个替身返回了 503"。
    """
    import urllib.request

    transport = _Transport()
    package_bytes = json.dumps(PACKAGE, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", _FakeUrlopen(package_bytes))
    monkeypatch.setattr(UrllibB3Transport, "get_json", transport.get_json)
    monkeypatch.setattr(UrllibB3Transport, "post_json", transport.post_json)

    runtime = build_b2_runtime(_settings(database_url, signer, tmp_path))
    with _client(runtime) as client:
        response = _post(client, world.teacher_token(signer))
    assert response.status_code == 503, response.text
    assert "sandbox" in response.json()["detail"]
    assert transport.posts == [], "没有沙箱就不得产生存证"
    assert runtime.failure_count == 0, "同上：真实链路上的平台条件也不得计入未预期失败"


# --- 替身守卫（防止分支用例变成恒真） -------------------------------------------------------


def test_the_script_double_raises_or_returns_as_scripted() -> None:
    """替身本身可用：脚本化异常真的抛出、脚本化响应真的返回。

    没有这一条，"上游 404/409/500 各走哪条路"的分支用例可能因为替身根本没抛而恒真。
    """
    with pytest.raises(ReferenceAnswerUnavailable):
        _Script(ReferenceAnswerUnavailable("x"))("u", {}, 1.0, 10)
    assert _Script((404, "{}"))("u", {}, 1.0, 10) == (404, "{}")
    assert isinstance(ReferenceVerificationNotFound("x"), LookupError)
    assert isinstance(ReferenceVerificationNotPossible("x"), RuntimeError)
    assert isinstance(ReferenceVerificationUnavailable("x"), RuntimeError)
    assert isinstance(SandboxUnavailableError("x"), RuntimeError)
