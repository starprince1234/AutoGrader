"""`GET /api/v1/submissions/{submission_id}`：所有权校验与状态机视图（T-STORE-003 等）。

验收三条：
1. 学生 A 读学生 B 的提交 → **403**（不是 404、不是 200）；本人读 → 200；
2. 响应含 state/intake/files(path+digest+size)/state_history，且**不含任何文件内容**；
3. 教师按课程作用域可读，跨课程 → 403；不存在的提交 → 404。
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from autograder_b2.api.errors import install_problem_handlers
from autograder_b2.intake.api import build_router
from autograder_b2.intake.runtime import IntakeRuntime, build_runtime
from tests.conftest import requires_postgres

pytestmark = requires_postgres

ISSUER = "autograder-b4"
AUDIENCE = "autograder"
SOURCE = b"print('secret student code')\n"
POST = "/api/v1/submissions"
ENDPOINT = "/api/v1/submissions"


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


@pytest.fixture(scope="module")
def signer() -> _Signer:
    return _Signer()


@pytest.fixture(scope="module")
def runtime(
    upgraded_database_url: str, tmp_path_factory: pytest.TempPathFactory, signer: _Signer
) -> IntakeRuntime:
    return build_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(tmp_path_factory.mktemp("artifacts")),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )


@pytest.fixture()
def client(runtime: IntakeRuntime, engine: Engine) -> Iterator[TestClient]:
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(runtime))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def course() -> tuple[str, str]:
    return (str(uuid.uuid4()), str(uuid.uuid4()))


def _student_token(signer: _Signer, user_id: str, course: tuple[str, str]) -> str:
    course_id, section_id = course
    return signer.token(
        user_id=user_id, roles=("student",), scope=[{"course_id": course_id, "section_id": section_id}]
    )


def _staff_token(signer: _Signer, user_id: str, course_id: str, *, role: str = "teacher") -> str:
    return signer.token(user_id=user_id, roles=(role,), scope=[{"course_id": course_id}])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _submit(client: TestClient, signer: _Signer, student_id: str, course: tuple[str, str]) -> str:
    course_id, section_id = course
    payload = {
        "submission_id": str(uuid.uuid4()),
        "student_id": student_id,
        "course_id": course_id,
        "section_id": section_id,
        "question_version_id": str(uuid.uuid4()),
        "files": [
            {
                "path": "main.py",
                "content_sha256": hashlib.sha256(SOURCE).hexdigest(),
                "content_base64": base64.b64encode(SOURCE).decode("ascii"),
            }
        ],
        "idempotency_key": f"key-{uuid.uuid4().hex}",
    }
    response = client.post(POST, json=payload, headers=_auth(_student_token(signer, student_id, course)))
    assert response.status_code == 202, response.text
    return payload["submission_id"]


# --- 验收 1：所有权与 403 语义 -------------------------------------------------------------


def test_owner_can_read_own_submission(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    response = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_student_token(signer, student_id, course))
    )
    assert response.status_code == 200, response.text
    assert response.json()["submission_id"] == submission_id


def test_student_reading_another_students_submission_is_403_not_404(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    """越权读必须 403：既不能放行（200），也不能用 404 掩盖（契约与 identifiers.md §3）。"""
    owner = str(uuid.uuid4())
    intruder = str(uuid.uuid4())
    submission_id = _submit(client, signer, owner, course)

    response = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_student_token(signer, intruder, course))
    )

    assert response.status_code == 403, response.text
    assert response.status_code != 404 and response.status_code != 200
    assert response.json()["type"].endswith("forbidden")


def test_student_in_another_course_is_403(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    owner = str(uuid.uuid4())
    submission_id = _submit(client, signer, owner, course)
    other_course = (str(uuid.uuid4()), str(uuid.uuid4()))
    response = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_student_token(signer, owner, other_course))
    )
    assert response.status_code == 403, response.text


def test_missing_or_invalid_token_is_401(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    submission_id = _submit(client, signer, str(uuid.uuid4()), course)
    assert client.get(f"{ENDPOINT}/{submission_id}").status_code == 401
    assert client.get(
        f"{ENDPOINT}/{submission_id}", headers={"Authorization": "Bearer garbage"}
    ).status_code == 401


def test_unknown_submission_is_404(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    """已认证且有权询问但资源不存在 → 404（与"无权读"的 403 明确区分）。"""
    student_id = str(uuid.uuid4())
    response = client.get(
        f"{ENDPOINT}/{uuid.uuid4()}", headers=_auth(_student_token(signer, student_id, course))
    )
    assert response.status_code == 404
    assert response.json()["type"].endswith("not-found")


def test_malformed_submission_id_is_404(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    response = client.get(
        f"{ENDPOINT}/not-a-uuid", headers=_auth(_student_token(signer, str(uuid.uuid4()), course))
    )
    assert response.status_code == 404


# --- 验收 2：视图内容与"不含文件内容" ------------------------------------------------------


def test_view_shape_and_no_file_content_leaks(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    response = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_student_token(signer, student_id, course))
    )
    body = response.json()

    assert set(body) == {
        "submission_id",
        "student_id",
        "course_id",
        "section_id",
        "question_version_id",
        "state",
        "intake",
        "files",
        "state_history",
        # `evaluation_run_id` is nullable and is what B1 polls for: the run row is created
        # after intake, and the submission state machine deliberately cannot reach a
        # terminal state before the run exists, so a client that keys the progress stream
        # off `state` alone has nothing to subscribe to.
        "evaluation_run_id",
        "created_at",
    }
    assert body["state"] == "validated"
    assert body["intake"] == "api"
    assert body["files"] == [
        {
            "path": "main.py",
            "content_sha256": hashlib.sha256(SOURCE).hexdigest(),
            "size_bytes": len(SOURCE),
        }
    ]
    assert body["created_at"].endswith("Z")

    serialized = json.dumps(body)
    assert "secret student code" not in serialized, "视图绝不能内联源码"
    assert base64.b64encode(SOURCE).decode("ascii") not in serialized
    assert "content_base64" not in serialized and "storage_ref" not in serialized


def test_state_history_is_real_and_ordered(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    """迁移史必须来自真实写入的记录，并符合锁定状态机（received → validated）。"""
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    body = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_student_token(signer, student_id, course))
    ).json()

    history = body["state_history"]
    assert [(entry["from_state"], entry["to_state"]) for entry in history] == [
        (None, "received"),
        ("received", "validated"),
    ]
    assert all(entry["occurred_at"].endswith("Z") for entry in history)
    # 静态阶段尚未发生：不得出现 running / 终态（否则就是伪造历史）
    assert all(entry["to_state"] not in {"running", "completed", "rejected", "failed"} for entry in history)


# --- 验收 3：教师作用域与跨课程拒绝 --------------------------------------------------------


def test_teacher_in_course_scope_can_read(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    response = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_staff_token(signer, str(uuid.uuid4()), course[0]))
    )
    assert response.status_code == 200, response.text


def test_assistant_in_course_scope_can_read(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    response = client.get(
        f"{ENDPOINT}/{submission_id}",
        headers=_auth(_staff_token(signer, str(uuid.uuid4()), course[0], role="assistant")),
    )
    assert response.status_code == 200, response.text


def test_teacher_from_another_course_is_403(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    submission_id = _submit(client, signer, str(uuid.uuid4()), course)
    other_course_id = str(uuid.uuid4())
    response = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_staff_token(signer, str(uuid.uuid4()), other_course_id))
    )
    assert response.status_code == 403, response.text


def test_admin_can_read_any_submission(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    submission_id = _submit(client, signer, str(uuid.uuid4()), course)
    admin = signer.token(user_id=str(uuid.uuid4()), roles=("admin",), scope=[])
    response = client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(admin))
    assert response.status_code == 200, response.text


# --- 独立复核发现的缺陷回归（角色层缺失 / 标识大小写 / reason 回显通道） --------------------


@pytest.mark.parametrize("roles", [[], ["guest"], ["administrator"], ["ADMIN"]])
def test_token_without_recognized_role_cannot_read_even_its_own(
    client: TestClient, signer: _Signer, course: tuple[str, str], roles: list[str]
) -> None:
    """角色是独立一层：没有任何**已登记**角色的令牌连自己的提交也不能读（认证 ≠ 授权）。

    独立复核发现原实现对此返回 200（`role: []` / `role: ["guest"]` + 匹配作用域 + 自己的 user_id）。
    未知角色不授予任何权限，且判定大小写敏感。
    """
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    course_id, section_id = course
    token = signer.token(
        user_id=student_id, roles=tuple(roles), scope=[{"course_id": course_id, "section_id": section_id}]
    )
    response = client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(token))
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("forbidden")


def test_student_role_is_required_for_read_own(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    """对照组：带 `student` 角色的同一请求应当成功（证明上面的 403 来自角色判定而非其他原因）。"""
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    course_id, section_id = course
    token = signer.token(
        user_id=student_id, roles=("student",), scope=[{"course_id": course_id, "section_id": section_id}]
    )
    assert client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(token)).status_code == 200


def test_uppercase_uuid_spellings_in_token_are_canonicalised(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    """契约 `Uuid` 允许大写等合法写法；规范化后同一标识只有一种内部表示，故本人仍可读。"""
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    course_id, section_id = course
    token = signer.token(
        user_id=student_id.upper(),
        roles=("student",),
        scope=[{"course_id": course_id.upper(), "section_id": section_id.upper()}],
    )
    response = client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(token))
    assert response.status_code == 200, response.text


def test_non_uuid_subject_is_rejected(client: TestClient, signer: _Signer, course: tuple[str, str]) -> None:
    """标识不是 UUID → 401（`identifiers.md` 强制 UUID；不接受不透明字符串当主体）。"""
    submission_id = _submit(client, signer, str(uuid.uuid4()), course)
    course_id, section_id = course
    token = signer.token(
        user_id="not-a-uuid", roles=("student",), scope=[{"course_id": course_id, "section_id": section_id}]
    )
    assert client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(token)).status_code == 401


def test_state_history_reason_is_the_redacted_constant(
    client: TestClient, signer: _Signer, course: tuple[str, str]
) -> None:
    """`reason` 是**原样回显**数据库列的通道，因此必须锁住它的当前取值。

    独立复核指出：列允许 500 字符，若将来有写入方把源码/错误原文写进 `reason`，本端点会原样透出
    （契约 `b2.yaml` 明令禁止 stack trace 与源码）。这里把"当前唯一写入方写的是脱敏常量"变成断言，
    任何改动都会让本测试失败，从而强制复核。
    """
    student_id = str(uuid.uuid4())
    submission_id = _submit(client, signer, student_id, course)
    body = client.get(
        f"{ENDPOINT}/{submission_id}", headers=_auth(_student_token(signer, student_id, course))
    ).json()
    reasons = [entry["reason"] for entry in body["state_history"]]
    assert reasons == [None, "envelope validated: digests verified, content stored by digest"]
    assert "secret student code" not in json.dumps(body["state_history"])
