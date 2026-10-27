"""`POST /api/v1/submissions` 的契约 / 认证 / 幂等测试（T-RECV-*）。

真实 PostgreSQL（临时库 + 迁移到 head）+ 真实 Ed25519 验签路径：测试**自己按 B4 的令牌形态**
签发令牌（`B4/src/autograder_b4/security/tokens.py` 的 claim 集与 JWKS 结构），因此验签代码是被
真正执行的，而不是被替身绕过的。
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from autograder_b2.api.errors import install_problem_handlers
from autograder_b2.db.schema import (
    artifacts,
    intake_quarantines,
    outbox_events,
    submission_files,
    submissions,
)
from autograder_b2.intake.api import build_router
from autograder_b2.intake.imap import QuarantineStore
from autograder_b2.intake.matching import ScopeContext
from autograder_b2.intake.runtime import IntakeRuntime, build_runtime
from tests.conftest import requires_postgres, run_alembic

pytestmark = requires_postgres

ISSUER = "autograder-b4"
AUDIENCE = "autograder"
SOURCE = b"print('hello')\n"
ENDPOINT = "/api/v1/submissions"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _Signer:
    """B4 形态的 Ed25519 签名者（测试夹具；不 import B4 代码）。"""

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

    def token(
        self,
        *,
        user_id: str,
        roles: tuple[str, ...],
        scope: list[dict[str, str]],
        expires_in: int = 900,
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
    ) -> str:
        import jwt

        issued = datetime.now(UTC).replace(microsecond=0)
        payload = {
            "iss": issuer,
            "aud": audience,
            "sub": user_id,
            "jti": str(uuid.uuid4()),
            "iat": int(issued.timestamp()),
            "nbf": int(issued.timestamp()),
            "exp": int((issued + timedelta(seconds=expires_in)).timestamp()),
            "role": list(roles),
            "scope": scope,
        }
        return jwt.encode(
            payload, self._key, algorithm="EdDSA", headers={"kid": self.kid}
        )


@pytest.fixture(scope="module")
def signer() -> _Signer:
    return _Signer()


@pytest.fixture(scope="module")
def runtime(
    upgraded_database_url: str, tmp_path_factory: pytest.TempPathFactory, signer: _Signer
) -> IntakeRuntime:
    root = tmp_path_factory.mktemp("artifacts")
    return build_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(root),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
            "B4_JWT_ISSUER": ISSUER,
            "B4_JWT_AUDIENCE": AUDIENCE,
        }
    )


@pytest.fixture()
def client(runtime: IntakeRuntime, engine: Engine) -> Iterator[TestClient]:
    """每个用例前清空业务表（与共享 `engine` 夹具同一套隔离逻辑）。"""
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(runtime))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def student() -> tuple[str, str, str, str]:
    """(user_id, course_id, section_id, question_version_id) —— 每个用例一套全新标识。"""
    return (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))


def _envelope(
    student: tuple[str, str, str, str],
    *,
    files: list[dict[str, object]] | None = None,
    idempotency_key: str = "key-1",
    submission_id: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    user_id, course_id, section_id, question_version_id = student
    payload: dict[str, object] = {
        "submission_id": submission_id or str(uuid.uuid4()),
        "student_id": user_id,
        "course_id": course_id,
        "section_id": section_id,
        "question_version_id": question_version_id,
        "files": files if files is not None else [_file("main.py", SOURCE)],
        "idempotency_key": idempotency_key,
    }
    payload.update(overrides)
    return payload


def _file(path: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _student_token(signer: _Signer, student: tuple[str, str, str, str]) -> str:
    user_id, course_id, section_id, _ = student
    return signer.token(
        user_id=user_id,
        roles=("student",),
        scope=[{"course_id": course_id, "section_id": section_id}],
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _counts(engine: Engine) -> tuple[int, int, int, int]:
    with engine.connect() as connection:
        return (
            int(connection.execute(sa.select(sa.func.count()).select_from(submissions)).scalar_one()),
            int(connection.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one()),
            int(
                connection.execute(
                    sa.select(sa.func.count()).select_from(submission_files)
                ).scalar_one()
            ),
            int(connection.execute(sa.select(sa.func.count()).select_from(outbox_events)).scalar_one()),
        )


# --- 契约：结构 ---------------------------------------------------------------------------


def test_valid_submission_is_accepted(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    payload = _envelope(student)
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, student)))

    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {
        "submission_id",
        "state",
        "evaluation_run_id",
        "idempotent_replay",
        "accepted_at",
    }
    assert body["submission_id"] == payload["submission_id"]
    assert body["state"] == "validated"
    assert body["idempotent_replay"] is False
    assert body["evaluation_run_id"] is None
    assert body["accepted_at"].endswith("Z")
    assert _counts(engine) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.pop("student_id"), id="missing-required"),
        pytest.param(lambda p: p.pop("files"), id="missing-files"),
        pytest.param(lambda p: p.pop("idempotency_key"), id="missing-idempotency-key"),
        pytest.param(lambda p: p.update({"files": "not-an-array"}), id="files-wrong-type"),
        pytest.param(lambda p: p.update({"submission_id": "not-a-uuid"}), id="bad-uuid"),
        pytest.param(lambda p: p.update({"source_code": "print(1)"}), id="unknown-field"),
        pytest.param(lambda p: p.update({"idempotency_key": ""}), id="empty-idempotency-key"),
        pytest.param(lambda p: p.update({"files": []}), id="empty-fileset"),
    ],
)
def test_structurally_invalid_envelope_is_422_and_writes_nothing(
    client: TestClient,
    engine: Engine,
    signer: _Signer,
    student: tuple[str, str, str, str],
    mutate: object,
) -> None:
    payload = _envelope(student)
    mutate(payload)  # type: ignore[operator]
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, student)))

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 422 and body["type"].startswith("https://autograder.local/problems/")
    assert _counts(engine) == (0, 0, 0, 0), "非法请求不得落库"


@pytest.mark.parametrize(
    "bad_path",
    ["../main.py", "../../etc/passwd", "/etc/passwd", "..\\main.py", "a//b.py", "./main.py"],
)
def test_illegal_path_is_422_and_writes_nothing(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str], bad_path: str
) -> None:
    """路径穿越/非法路径一律 422 且零落库。

    谁拦下的取决于形态：绝对路径与反斜杠被契约 `path` 模式拦下（`schema-invalid`），而 `..`
    段**能通过契约模式**（见 interfaces.md I2 实测），必须由 B2 白名单拦下（`path-not-allowed`）。
    两者都发生在写内容之前，故数据库零新增行。
    """
    payload = _envelope(student, files=[_file(bad_path, SOURCE)])
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, student)))

    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith(("path-not-allowed", "schema-invalid"))
    assert _counts(engine) == (0, 0, 0, 0)


def test_path_traversal_specifically_reports_path_not_allowed(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """`..` 段必须由 B2 白名单拒绝（契约模式放行它），且理由码精确。"""
    payload = _envelope(student, files=[_file("../main.py", SOURCE)])
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, student)))
    assert response.status_code == 422
    assert response.json()["type"].endswith("path-not-allowed")
    assert _counts(engine) == (0, 0, 0, 0)


def test_digest_mismatch_is_422_and_writes_nothing(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    forged = _file("main.py", SOURCE)
    forged["content_sha256"] = hashlib.sha256(b"something else").hexdigest()
    payload = _envelope(student, files=[forged])
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, student)))

    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("digest-mismatch")
    assert _counts(engine) == (0, 0, 0, 0)


def test_invalid_base64_is_422(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    broken = _file("main.py", SOURCE)
    broken["content_base64"] = "!!!not-base64!!!"
    response = client.post(
        ENDPOINT,
        json=_envelope(student, files=[broken]),
        headers=_auth(_student_token(signer, student)),
    )
    assert response.status_code == 422
    assert _counts(engine) == (0, 0, 0, 0)


def test_non_json_body_is_422(client: TestClient, signer: _Signer, student: tuple[str, str, str, str]) -> None:
    response = client.post(
        ENDPOINT,
        content=b"not json",
        headers={**_auth(_student_token(signer, student)), "content-type": "application/json"},
    )
    assert response.status_code == 422


# --- 契约：限额 ---------------------------------------------------------------------------


def test_too_many_files_is_422(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    files = [_file(f"f{index}.py", b"x") for index in range(101)]
    response = client.post(
        ENDPOINT,
        json=_envelope(student, files=files),
        headers=_auth(_student_token(signer, student)),
    )
    assert response.status_code == 422, response.text
    assert response.json()["type"].endswith("too-many-files")
    assert _counts(engine) == (0, 0, 0, 0)


def test_oversized_single_file_is_413(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    big = b"a" * (1572864 + 1)
    response = client.post(
        ENDPOINT,
        json=_envelope(student, files=[_file("big.py", big)]),
        headers=_auth(_student_token(signer, student)),
    )
    assert response.status_code == 413, response.text
    assert _counts(engine) == (0, 0, 0, 0)


def test_oversized_total_is_413(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    chunk = b"b" * 1_200_000
    files = [_file(f"part{index}.py", chunk) for index in range(4)]
    response = client.post(
        ENDPOINT,
        json=_envelope(student, files=files),
        headers=_auth(_student_token(signer, student)),
    )
    assert response.status_code == 413, response.text
    assert response.json()["type"].endswith("total-too-large")
    assert _counts(engine) == (0, 0, 0, 0)


def test_declared_content_length_over_cap_is_413(
    upgraded_database_url: str,
    tmp_path_factory: pytest.TempPathFactory,
    signer: _Signer,
    student: tuple[str, str, str, str],
) -> None:
    """请求体在解析前就被上限拦下（用极小上限把这条早退路径变成可实测的）。"""
    tiny = build_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(tmp_path_factory.mktemp("tiny-artifacts")),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
            "B2_MAX_FILES": "1",
            "B2_MAX_FILE_BYTES": "16",
        }
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(tiny))
    oversized_body = b"{" + b" " * 70_000 + b"}"
    with TestClient(app) as tiny_client:
        response = tiny_client.post(
            ENDPOINT,
            content=oversized_body,
            headers={
                **_auth(_student_token(signer, student)),
                "content-type": "application/json",
            },
        )
    assert response.status_code == 413, response.text


# --- 认证与作用域 -------------------------------------------------------------------------


def test_missing_token_is_401(client: TestClient, engine: Engine, student: tuple[str, str, str, str]) -> None:
    response = client.post(ENDPOINT, json=_envelope(student))
    assert response.status_code == 401
    assert response.json()["type"].endswith("unauthorized")
    assert _counts(engine) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "header",
    [
        pytest.param("", id="empty-header"),
        pytest.param("Bearer", id="no-credentials"),
        pytest.param("Basic dXNlcjpwYXNz", id="wrong-scheme"),
        pytest.param("Bearer not.a.jwt", id="malformed-token"),
        pytest.param("Bearer eyJhbGciOiJFZERTQSJ9.e30.", id="empty-signature"),
    ],
)
def test_malformed_authorization_is_401(
    client: TestClient, signer: _Signer, student: tuple[str, str, str, str], header: str
) -> None:
    response = client.post(
        ENDPOINT, json=_envelope(student), headers={"Authorization": header} if header else {}
    )
    assert response.status_code == 401, response.text


def test_expired_token_is_401(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    user_id, course_id, section_id, _ = student
    token = signer.token(
        user_id=user_id,
        roles=("student",),
        scope=[{"course_id": course_id, "section_id": section_id}],
        expires_in=-60,
    )
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 401
    assert _counts(engine) == (0, 0, 0, 0)


def test_foreign_issuer_is_401(
    client: TestClient, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    user_id, course_id, section_id, _ = student
    token = signer.token(
        user_id=user_id,
        roles=("student",),
        scope=[{"course_id": course_id, "section_id": section_id}],
        issuer="someone-else",
    )
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 401


def test_token_signed_by_unknown_key_is_401(
    client: TestClient, student: tuple[str, str, str, str]
) -> None:
    impostor = _Signer()
    user_id, course_id, section_id, _ = student
    token = impostor.token(
        user_id=user_id, roles=("student",), scope=[{"course_id": course_id, "section_id": section_id}]
    )
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 401


def test_course_scope_mismatch_is_403_not_404(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """作用域不符必须 403，且**不得**用 404 掩盖（契约与 identifiers.md 第 3 节）。"""
    user_id, _, section_id, _ = student
    token = signer.token(
        user_id=user_id,
        roles=("student",),
        scope=[{"course_id": str(uuid.uuid4()), "section_id": section_id}],
    )
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("forbidden")
    assert _counts(engine) == (0, 0, 0, 0)


def test_student_cannot_submit_as_another_student(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    _, course_id, section_id, _ = student
    impersonator = str(uuid.uuid4())
    token = signer.token(
        user_id=impersonator,
        roles=("student",),
        scope=[{"course_id": course_id, "section_id": section_id}],
    )
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 403, response.text
    assert _counts(engine) == (0, 0, 0, 0)


def test_teacher_with_course_scope_may_submit_for_student(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    _, course_id, _, _ = student
    teacher = str(uuid.uuid4())
    token = signer.token(user_id=teacher, roles=("teacher",), scope=[{"course_id": course_id}])
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 202, response.text
    assert _counts(engine)[0] == 1


def test_section_mismatch_within_course_is_403(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    user_id, course_id, _, _ = student
    token = signer.token(
        user_id=user_id,
        roles=("student",),
        scope=[{"course_id": course_id, "section_id": str(uuid.uuid4())}],
    )
    response = client.post(ENDPOINT, json=_envelope(student), headers=_auth(token))
    assert response.status_code == 403
    assert _counts(engine) == (0, 0, 0, 0)


# --- 幂等 ---------------------------------------------------------------------------------


def test_repeated_idempotency_key_returns_same_submission_without_second_write(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    token = _student_token(signer, student)
    payload = _envelope(student, idempotency_key="same-key")

    first = client.post(ENDPOINT, json=payload, headers=_auth(token))
    second = client.post(ENDPOINT, json={**payload, "submission_id": str(uuid.uuid4())}, headers=_auth(token))

    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert first.json()["submission_id"] == second.json()["submission_id"] == payload["submission_id"]
    assert second.json()["accepted_at"] == first.json()["accepted_at"]
    # 提交 / 内容 / 文件 / 事件都只应有一份
    assert _counts(engine) == (1, 1, 1, 1)


def test_different_idempotency_keys_create_two_submissions(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    token = _student_token(signer, student)
    first = client.post(ENDPOINT, json=_envelope(student, idempotency_key="k1"), headers=_auth(token))
    second = client.post(ENDPOINT, json=_envelope(student, idempotency_key="k2"), headers=_auth(token))
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["submission_id"] != second.json()["submission_id"]
    # 相同内容按 SHA-256 去重：两条提交、两份文件元数据、但只有一份内容
    assert _counts(engine) == (2, 1, 2, 2)


def test_reused_submission_id_with_different_key_is_rejected(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """同一 `submission_id` 配不同幂等键：契约未声明 409，故按业务规则拒绝为 422。"""
    token = _student_token(signer, student)
    shared_id = str(uuid.uuid4())
    first = client.post(
        ENDPOINT, json=_envelope(student, idempotency_key="a", submission_id=shared_id), headers=_auth(token)
    )
    second = client.post(
        ENDPOINT, json=_envelope(student, idempotency_key="b", submission_id=shared_id), headers=_auth(token)
    )
    assert first.status_code == 202
    assert second.status_code == 422, second.text
    assert _counts(engine)[0] == 1


# --- 事件（outbox）------------------------------------------------------------------------


def test_submission_accepted_event_has_no_source_code(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    payload = _envelope(student)
    client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, student)))

    with engine.connect() as connection:
        row = connection.execute(
            sa.select(outbox_events.c.subject, outbox_events.c.payload, outbox_events.c.producer)
        ).one()
    subject, event_payload, producer = row
    assert subject == "autograder.v1.submission.accepted"
    assert producer == "B2"
    assert set(event_payload) == {
        "submission_id",
        "student_id",
        "course_id",
        "section_id",
        "question_version_id",
        "intake",
    }
    serialized = json.dumps(event_payload)
    assert "print('hello')" not in serialized, "事件绝不能携带源码"
    assert "content_base64" not in serialized
    assert base64.b64encode(SOURCE).decode("ascii") not in serialized


def test_idempotent_replay_does_not_enqueue_second_event(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    token = _student_token(signer, student)
    payload = _envelope(student, idempotency_key="dedup-key")
    client.post(ENDPOINT, json=payload, headers=_auth(token))
    client.post(ENDPOINT, json=payload, headers=_auth(token))
    with engine.connect() as connection:
        events = int(connection.execute(sa.select(sa.func.count()).select_from(outbox_events)).scalar_one())
    assert events == 1


def test_files_are_stored_content_addressable(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    client.post(ENDPOINT, json=_envelope(student), headers=_auth(_student_token(signer, student)))
    with engine.connect() as connection:
        digest, path, size = connection.execute(
            sa.select(artifacts.c.content_sha256, artifacts.c.storage_path, artifacts.c.size_bytes)
        ).one()
    assert digest == hashlib.sha256(SOURCE).hexdigest()
    assert path == f"{digest[:2]}/{digest[2:4]}/{digest}", "存储路径必须由摘要派生"
    assert size == len(SOURCE)


# --- 可用性（配置缺失时明确不可用，不静默放行）------------------------------------------


def test_missing_configuration_reports_503(
    upgraded_database_url: str, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    runtime = build_runtime({"B2_JWKS_JSON": json.dumps(signer.jwks)})
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(runtime))
    with TestClient(app) as no_config:
        response = no_config.post(ENDPOINT, json=_envelope(student))
    assert response.status_code == 503
    assert runtime.ready is False
    healthy, detail = runtime.health()
    assert healthy is False and "DATABASE_URL" in detail


def test_runtime_health_reports_database(
    upgraded_database_url: str, tmp_path: pathlib.Path, signer: _Signer
) -> None:
    runtime = build_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(tmp_path),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    healthy, detail = runtime.health()
    assert healthy is True, detail


def test_runtime_health_is_false_when_schema_is_not_migrated(
    unmigrated_database_url: str, tmp_path: pathlib.Path, signer: _Signer
) -> None:
    """连通但**未迁移**的库必须报告不可用（独立复核发现：原实现只看 `SELECT 1` 会谎报健康）。

    用函数级空库：模块级的 `fresh_database_url` 在本模块里早已被别的用例迁到 head。
    """
    runtime = build_runtime(
        {
            "DATABASE_URL": unmigrated_database_url,
            "B2_ARTIFACT_ROOT": str(tmp_path),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    healthy, detail = runtime.health()
    assert healthy is False
    assert "initialised" in detail, detail


@pytest.fixture(scope="module")
def reached_database_url(upgraded_database_url: str) -> str:
    """确保迁移已到 head（health 探测需要可连的库）。"""
    result = run_alembic(upgraded_database_url, "upgrade", "head")
    assert result.returncode == 0
    return upgraded_database_url


def test_plugin_router_serves_the_endpoint(
    upgraded_database_url: str,
    tmp_path: pathlib.Path,
    signer: _Signer,
    engine: Engine,
    student: tuple[str, str, str, str],
) -> None:
    """经**插件入口**（`B2Module.router`）挂载后端点确实可用。

    DoD 要求 `router()` 中注册了对应端点；这里走的就是宿主装配路径：`router(context)` →
    `include_router` → 真实请求。同时验证 `health()` 反映就绪。
    """
    from types import SimpleNamespace

    from autograder_b2.plugin import B2Module

    context = SimpleNamespace(
        settings={
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(tmp_path),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    module = B2Module()
    # 刻意**不**断言 `module.router(context).routes` 里存在带 `.path` 的路由对象：
    # FastAPI 0.141 起 `include_router` 不再把被包含的路由拍平进 `routes`，而是放一个
    # `fastapi.routing._IncludedRouter` 占位对象（该对象**没有** `.path` 属性）。旧写法
    # （`{route.path for route in ...routes}`）在新版本上恒为空集，于是测试会因为**库的内部形状**
    # 变化而报"未注册端点"——而端点其实是注册的。是否真的注册，由下面的**真实请求**判定：
    # 未注册会得到 404，而这里必须得到 202/401。
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(module.router(context))
    with TestClient(app) as plugin_client:
        accepted = plugin_client.post(
            ENDPOINT, json=_envelope(student), headers=_auth(_student_token(signer, student))
        )
        unauthorized = plugin_client.post(ENDPOINT, json=_envelope(student))
    assert accepted.status_code == 202, accepted.text
    assert unauthorized.status_code == 401
    report = module.health(context)
    assert report.healthy is True, report.detail
    assert report.name == "B2"


# --- 标识写法规范化（组级复核 #2/#3 的回归） ----------------------------------------------


def test_uppercase_uuid_spellings_in_the_request_are_accepted(
    client: TestClient,
    engine: Engine,
    signer: _Signer,
    student: tuple[str, str, str, str],
) -> None:
    """契约的 `Uuid` 接受大写写法，授权判定必须**规范化后再比较**。

    修复前：令牌侧已规范化而请求侧原样比较，于是"本人提交自己的作业"被判 403——
    一份完全合规的请求被误拒（组级复核 #3）。
    """
    user_id, course_id, section_id, question_version_id = student
    payload = _envelope(student)
    payload["student_id"] = user_id.upper()
    payload["course_id"] = course_id.upper()
    payload["section_id"] = section_id.upper()
    payload["question_version_id"] = question_version_id.upper()

    response = client.post(
        ENDPOINT, json=payload, headers=_auth(_student_token(signer, student))
    )

    assert response.status_code == 202, response.text
    assert _counts(engine) == (1, 1, 1, 1)


def test_uppercase_spelling_does_not_bypass_idempotency(
    client: TestClient,
    engine: Engine,
    signer: _Signer,
    student: tuple[str, str, str, str],
) -> None:
    """除标识大小写外完全相同的两次请求必须是**同一份**提交。

    修复前：幂等作用域直接拼未规范化的标识，换一种大小写就得到另一个作用域，同一个幂等键落成
    两份提交——幂等形同虚设（组级复核 #2）。这里两次用**不同** `submission_id`、同一
    `idempotency_key`，只有标识写法不同，因此差别只可能来自作用域的比较。
    """
    user_id, _course_id, _section_id, question_version_id = student
    first = _envelope(student, idempotency_key="same-key")
    second = _envelope(student, idempotency_key="same-key")
    second["student_id"] = user_id.upper()
    second["question_version_id"] = question_version_id.upper()

    headers = _auth(_student_token(signer, student))
    accepted = client.post(ENDPOINT, json=first, headers=headers)
    replayed = client.post(ENDPOINT, json=second, headers=headers)

    assert accepted.status_code == 202, accepted.text
    assert replayed.status_code == 202, replayed.text
    assert replayed.json()["idempotent_replay"] is True, replayed.text
    assert replayed.json()["submission_id"] == accepted.json()["submission_id"]
    assert _counts(engine) == (1, 1, 1, 1), "只允许落一份提交、一份事件"


# --- CHG-0014：隔离区复核两条端点（读出口 + 人工归属） --------------------------------------
#
# 契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `listIntakeQuarantines` / `resolveIntakeQuarantine`
# 与 `IntakeQuarantineView`。四条验证对应 `CHG-0014` §6 的第 1、4 两条与"归属不触发评测"。

QUARANTINES = "/api/v1/intake/quarantines"
RESOLVE = "/api/v1/intake/quarantines/{quarantine}/resolve"
#: 只有这两种原因码能被人工归属（见 `intake.quarantine`）。
MATCH_FAILURES = ("not_uniquely_matched", "ambiguous_match")


def _staff_token(signer: _Signer, student: tuple[str, str, str, str]) -> str:
    _, course_id, _, _ = student
    return signer.token(
        user_id=str(uuid.uuid4()), roles=("teacher",), scope=[{"course_id": course_id}]
    )


def _section_staff_token(signer: _Signer, *, course_id: str, section_id: str) -> str:
    """只覆盖**一个课序**的 staff 令牌：用来验证课序级的行过滤。"""
    return signer.token(
        user_id=str(uuid.uuid4()),
        roles=("teacher",),
        scope=[{"course_id": course_id, "section_id": section_id}],
    )


def _admin_token(signer: _Signer) -> str:
    """平台管理员：不受课程限制，是未关联记录的唯一处置者。"""
    return signer.token(user_id=str(uuid.uuid4()), roles=("admin",), scope=[])


def _scopeless_staff_token(signer: _Signer) -> str:
    """带 staff 角色但**没有任何课程作用域**的令牌（"不在任何课程作用域内"）。"""
    return signer.token(user_id=str(uuid.uuid4()), roles=("teacher",), scope=[])


def _foreign_token(signer: _Signer) -> str:
    """**另一门课**的教师（作用域不覆盖本用例的课程）。"""
    return signer.token(
        user_id=str(uuid.uuid4()),
        roles=("teacher",),
        scope=[{"course_id": str(uuid.uuid4())}],
    )


def _seed_quarantine(
    engine: Engine,
    *,
    reason_code: str = "not_uniquely_matched",
    message_id: str | None = None,
    mailbox: str = "submissions@example.edu",
    uid: int = 7,
    detail: str = "no rule matched (rules=3)",
    recorded_at: datetime | None = None,
    scope_course_id: str | None = None,
    scope_section_id: str | None = None,
) -> str:
    """用**生产写入路径**（`intake.imap.QuarantineStore`）落一条隔离记录，返回它的 id。

    为什么不用手写 INSERT：这条测试要证明"邮件入口写下的记录能被读出来"，因此写入必须走
    `QuarantineStore.record`（与 `EmailIntakeService` 用的是同一个对象）。`scope_*` 不传即
    "没有可验证的课程关联"，与 `not_matched`/跨 scope ambiguous 的真实写入一致。
    """
    scope = None
    if scope_course_id is not None and scope_section_id is not None:
        scope = ScopeContext(course_id=scope_course_id, section_id=scope_section_id)
    store = QuarantineStore(engine)
    with engine.begin() as connection:
        return store.record(
            connection,
            reason_code=reason_code,
            detail=detail,
            mailbox=mailbox,
            uidvalidity=1,
            uid=uid,
            message_id=message_id or f"<{uuid.uuid4()}@example.edu>",
            now=recorded_at or datetime.now(UTC),
            scope=scope,
        )


def _quarantine_row(engine: Engine, quarantine_id: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(intake_quarantines).where(
                intake_quarantines.c.intake_quarantine_id == uuid.UUID(quarantine_id)
            )
        ).fetchone()


def _resolved_state(engine: Engine, quarantine_id: str) -> tuple[bool, str | None, str | None]:
    row = _quarantine_row(engine, quarantine_id)
    assert row is not None
    return (
        bool(row.resolved),
        None if row.resolved_student_id is None else str(row.resolved_student_id),
        row.resolution_note,
    )


def _evaluation_footprint(engine: Engine) -> tuple[int, int, int]:
    """`(提交数, outbox 事件数, 评测运行数)`：归属**不得**产生其中任何一个。"""
    from autograder_b2.db.schema import evaluation_runs, outbox_events

    with engine.connect() as connection:
        return (
            int(connection.execute(sa.select(sa.func.count()).select_from(submissions)).scalar_one()),
            int(connection.execute(sa.select(sa.func.count()).select_from(outbox_events)).scalar_one()),
            int(
                connection.execute(
                    sa.select(sa.func.count()).select_from(evaluation_runs)
                ).scalar_one()
            ),
        )


def test_quarantine_list_rejects_unauthenticated_and_non_staff(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """无令牌 ⇒ 401；学生 ⇒ 403；带角色但没有任何课程作用域的 staff 令牌 ⇒ 403（不在作用域内）。"""
    _seed_quarantine(engine)

    assert client.get(QUARANTINES).status_code == 401
    forbidden = (
        _student_token(signer, student),
        _scopeless_staff_token(signer),
    )
    for token in forbidden:
        response = client.get(QUARANTINES, headers=_auth(token))
        assert response.status_code == 403, response.text
        assert response.headers["content-type"].startswith("application/problem+json")


def test_quarantine_list_exposes_what_the_email_path_recorded(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """`CHG-0014` §6 第 4 条：未匹配的邮件**可被找到**，字段与写入侧一一对应，且不含邮件内容。"""
    message_id = f"<{uuid.uuid4()}@example.edu>"
    quarantine_id = _seed_quarantine(
        engine,
        reason_code="not_uniquely_matched",
        message_id=message_id,
        uid=11,
        scope_course_id=student[1],
        scope_section_id=student[2],
    )
    response = client.get(QUARANTINES, headers=_auth(_staff_token(signer, student)))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item) == {
        "intake_quarantine_id",
        "intake",
        "reason_code",
        "detail",
        "mailbox",
        "message_id",
        "uid",
        "recorded_at",
        "resolved",
        "resolved_student_id",
        "resolution_note",
        "scope_course_id",
        "scope_section_id",
    }
    assert item["intake_quarantine_id"] == quarantine_id
    assert item["intake"] == "email"
    assert item["reason_code"] == "not_uniquely_matched"
    assert item["message_id"] == message_id
    assert item["uid"] == 11
    assert item["mailbox"] == "submissions@example.edu"
    assert item["resolved"] is False
    assert item["resolved_student_id"] is None and item["resolution_note"] is None
    assert item["recorded_at"].endswith("Z")
    # scope 来自写入侧（匹配规则共识），读取侧只做映射：两个字段必须如实带出。
    assert item["scope_course_id"] == student[1]
    assert item["scope_section_id"] == student[2]
    # 契约明说这条路径**不是第二个邮件仓库**：响应里不得出现正文/附件内容的字段。
    assert not {"body", "content", "attachments", "source"} & set(item)


def test_quarantine_list_filters_by_reason_and_resolved_state(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """`reason_code` 与 `resolved` 两个过滤都真的生效（含"不过滤 = 全都要"的口径）。"""
    token = _auth(_staff_token(signer, student))
    scope = {"scope_course_id": student[1], "scope_section_id": student[2]}
    unresolved = _seed_quarantine(engine, reason_code="not_uniquely_matched", uid=1, **scope)
    resolved = _seed_quarantine(engine, reason_code="ambiguous_match", uid=2, **scope)
    other = _seed_quarantine(engine, reason_code="executable_attachment", uid=3, **scope)

    def listed(**params: str) -> set[str]:
        response = client.get(QUARANTINES, params=params, headers=token)
        assert response.status_code == 200, response.text
        return {item["intake_quarantine_id"] for item in response.json()["items"]}

    # 契约没有给 `resolved` 声明默认值（同一份文件里的 `Limit`/`view` 都显式写了默认值），
    # 因此**不传参数 = 不加过滤**；"未处理"只是契约描述里的提示。
    assert listed() == {unresolved, resolved, other}
    assert listed(resolved="false") == {unresolved, resolved, other}
    assert listed(reason_code="ambiguous_match") == {resolved}
    assert listed(reason_code="nothing_matches_this") == set()

    # 归属之后同一个 `resolved` 过滤才能区分出"已处理"。
    client.post(
        RESOLVE.format(quarantine=resolved),
        json={"student_id": str(uuid.uuid4())},
        headers=token,
    )
    assert listed(resolved="true") == {resolved}
    assert listed(resolved="false") == {unresolved, other}
    assert listed(resolved="TRUE") == {resolved}, "布尔取值大小写不敏感"

    # 非法过滤 ⇒ 422（错误体仍是 Problem Details）。
    for params in ({"resolved": "yes"}, {"reason_code": "x" * 65}):
        assert client.get(QUARANTINES, params=params, headers=token).status_code == 422


def test_quarantine_list_paginates_newest_first_without_gaps(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """游标分页：**倒序**（契约要求 newest first）、不重不漏、坏游标是 422 而不是"回到第一页"。"""
    base = datetime(2026, 9, 16, 9, 0, 0, tzinfo=UTC)
    identifiers = [
        _seed_quarantine(
            engine,
            uid=index + 1,
            recorded_at=base + timedelta(minutes=index),
            scope_course_id=student[1],
            scope_section_id=student[2],
        )
        for index in range(3)
    ]
    token = _auth(_staff_token(signer, student))

    first = client.get(QUARANTINES, params={"limit": 2}, headers=token).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    assert [item["intake_quarantine_id"] for item in first["items"]] == identifiers[::-1][:2], (
        "必须按记录时间倒序（最新的在前）"
    )
    second = client.get(
        QUARANTINES, params={"limit": 2, "cursor": first["next_cursor"]}, headers=token
    ).json()
    assert [item["intake_quarantine_id"] for item in second["items"]] == [identifiers[0]]
    assert second["next_cursor"] is None

    for params in ({"cursor": "not-a-cursor"}, {"limit": "0"}, {"limit": "201"}):
        response = client.get(QUARANTINES, params=params, headers=token)
        assert response.status_code == 422, response.text


def test_quarantine_resolve_records_the_attribution_without_evaluating(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """`CHG-0014` §2.2 第 2 条：归属**只记录**，不建提交、不发事件、不跑评测。"""
    quarantine_id = _seed_quarantine(
        engine, scope_course_id=student[1], scope_section_id=student[2]
    )
    attributed = str(uuid.uuid4())
    before = _evaluation_footprint(engine)

    response = client.post(
        RESOLVE.format(quarantine=quarantine_id),
        json={"student_id": attributed, "note": "学生本人来信确认了这封信"},
        headers=_auth(_staff_token(signer, student)),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved"] is True
    assert body["resolved_student_id"] == attributed
    assert body["resolution_note"] == "学生本人来信确认了这封信"
    assert body["intake_quarantine_id"] == quarantine_id
    assert _resolved_state(engine, quarantine_id) == (
        True,
        attributed,
        "学生本人来信确认了这封信",
    )
    assert _evaluation_footprint(engine) == before == (0, 0, 0), (
        "归属不得产生提交、事件或评测运行"
    )


def test_quarantine_resolve_reruns_the_checks_the_manual_path_must_not_skip(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """`CHG-0014` §6 第 1 条：人工路径**不得**变成绕过校验的后门。

    两种原因码都能被归属（"匹配器不确定"），而**报文本身**被自动路径拒绝的那些（体积超限、
    无 `Message-ID`、可执行附件、压缩炸弹……）一律 422——命名一个学生修不好被拒的信封。
    归属在这些情况下**一个字段都不写**（`resolved` 仍为 false）。
    """
    token = _auth(_staff_token(signer, student))
    scope = {"scope_course_id": student[1], "scope_section_id": student[2]}

    for index, reason_code in enumerate(MATCH_FAILURES):
        quarantined = _seed_quarantine(engine, reason_code=reason_code, uid=index + 1, **scope)
        response = client.post(
            RESOLVE.format(quarantine=quarantined),
            json={"student_id": str(uuid.uuid4())},
            headers=token,
        )
        assert response.status_code == 200, (reason_code, response.text)

    rejected = _seed_quarantine(engine, reason_code="executable_attachment", uid=99, **scope)
    refusal = client.post(
        RESOLVE.format(quarantine=rejected),
        json={"student_id": str(uuid.uuid4())},
        headers=token,
    )
    assert refusal.status_code == 422, refusal.text
    assert "executable_attachment" in refusal.json()["detail"], refusal.json()
    assert _resolved_state(engine, rejected) == (False, None, None), "被拒的归属不得留下任何处置"


def test_quarantine_resolve_is_conflict_for_an_already_resolved_record(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """第二次归属 ⇒ 409：**不覆盖**前一个人的决定（归属是人的决定，不是可随意改写的字段）。"""
    quarantine_id = _seed_quarantine(
        engine, scope_course_id=student[1], scope_section_id=student[2]
    )
    first_student = str(uuid.uuid4())
    token = _auth(_staff_token(signer, student))
    first = client.post(
        RESOLVE.format(quarantine=quarantine_id),
        json={"student_id": first_student, "note": "第一次归属"},
        headers=token,
    )
    assert first.status_code == 200, first.text

    second = client.post(
        RESOLVE.format(quarantine=quarantine_id),
        json={"student_id": str(uuid.uuid4()), "note": "第二次归属"},
        headers=token,
    )
    assert second.status_code == 409, second.text
    assert _resolved_state(engine, quarantine_id) == (True, first_student, "第一次归属")


def test_quarantine_resolve_validates_the_body_and_the_identifier(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """请求体与路径标识的校验：未知字段/缺 `student_id`/非 UUID/超长 note ⇒ 422；未知记录 ⇒ 404。"""
    quarantine_id = _seed_quarantine(
        engine, scope_course_id=student[1], scope_section_id=student[2]
    )
    token = _auth(_staff_token(signer, student))
    valid = {"student_id": str(uuid.uuid4())}

    for payload in (
        {},
        {"student_id": "not-a-uuid"},
        {"student_id": valid["student_id"], "note": "x" * 501},
        {"student_id": valid["student_id"], "reason_code": "not_uniquely_matched"},
    ):
        response = client.post(
            RESOLVE.format(quarantine=quarantine_id), json=payload, headers=token
        )
        assert response.status_code == 422, (payload, response.text)

    assert (
        client.post(
            RESOLVE.format(quarantine=str(uuid.uuid4())), json=valid, headers=token
        ).status_code
        == 404
    )
    assert (
        client.post(RESOLVE.format(quarantine="not-a-uuid"), json=valid, headers=token).status_code
        == 404
    )
    # 无令牌 / 学生：401 与 403 的区分不能因为"这条记录存在"而变。
    assert (
        client.post(RESOLVE.format(quarantine=quarantine_id), json=valid).status_code == 401
    )
    assert (
        client.post(
            RESOLVE.format(quarantine=quarantine_id),
            json=valid,
            headers=_auth(_student_token(signer, student)),
        ).status_code
        == 403
    )
    assert _resolved_state(engine, quarantine_id) == (False, None, None)


def test_quarantine_list_respects_the_contract_length_bounds(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """契约的长度上限是**结构**约束（`detail`/`resolution_note` ≤ 500），必须真的成立。

    库列的 `detail` 上限是 1000（0002 的 CHECK），契约视图的上限是 500：视图侧以契约为准
    （截断到 500，见 `intake.quarantine` 的模块文档），否则 B1 生成的客户端会因 schema 校验失败而
    拒绝一份**完全合法**的响应。归属备注的上限则在写入前判定（501 字 ⇒ 422）。
    """
    token = _auth(_staff_token(signer, student))
    long_detail = "x" * 700
    quarantined = _seed_quarantine(
        engine,
        detail=long_detail,
        uid=42,
        scope_course_id=student[1],
        scope_section_id=student[2],
    )
    assert len(_quarantine_row(engine, quarantined).detail) == 700, "库列允许到 1000"

    body = client.get(QUARANTINES, headers=token).json()
    item = next(entry for entry in body["items"] if entry["intake_quarantine_id"] == quarantined)
    assert len(item["detail"]) == 500, "视图侧的 detail 必须落在契约的 maxLength 内"

    # 归属备注：500 字可接受，501 字被拒（且在写入前）。
    accepted = client.post(
        RESOLVE.format(quarantine=quarantined),
        json={"student_id": str(uuid.uuid4()), "note": "y" * 500},
        headers=token,
    )
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["resolution_note"]) == 500


def test_quarantine_routes_are_503_on_an_unmigrated_database(
    signer: _Signer,
    student: tuple[str, str, str, str],
    unmigrated_database_url: str,
    tmp_path: pathlib.Path,
) -> None:
    """库结构未初始化 ⇒ 503 + 可读原因（不是 500、也不是静默返回空页）。"""
    from autograder_b2.intake.runtime import build_runtime

    runtime = build_runtime(
        {
            "DATABASE_URL": unmigrated_database_url,
            "B2_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
        }
    )
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(build_router(runtime))
    token = _auth(_staff_token(signer, student))
    with TestClient(app) as unmigrated:
        listing = unmigrated.get(QUARANTINES, headers=token)
        resolving = unmigrated.post(
            RESOLVE.format(quarantine=str(uuid.uuid4())),
            json={"student_id": str(uuid.uuid4())},
            headers=token,
        )
    for response in (listing, resolving):
        assert response.status_code == 503, response.text
        assert response.headers["content-type"].startswith("application/problem+json")


def test_quarantine_list_is_filtered_by_row_scope_before_pagination(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """P13 的强制验收：staff 只看到作用域内的行，且过滤发生在分页/游标之前。

    三类记录：本课/本课序、另一门课、未关联。staff 令牌只覆盖本课序：
    - 不带 query：只返回本课序那一行（未关联行**不可见**）；
    - 带本课/本课序：同一行；
    - 带另一门课：403（query 不是授权依据，越界必须失败而不是空列表）；
    - 管理员：三类全见，未关联行带 `scope_*=null`。
    """
    base = datetime(2026, 9, 17, 9, 0, 0, tzinfo=UTC)
    mine = _seed_quarantine(
        engine, uid=1, recorded_at=base, scope_course_id=student[1], scope_section_id=student[2]
    )
    foreign_course = str(uuid.uuid4())
    foreign = _seed_quarantine(
        engine,
        uid=2,
        recorded_at=base + timedelta(minutes=1),
        scope_course_id=foreign_course,
        scope_section_id=str(uuid.uuid4()),
    )
    unscoped = _seed_quarantine(engine, uid=3, recorded_at=base + timedelta(minutes=2))

    staff = _auth(_section_staff_token(signer, course_id=student[1], section_id=student[2]))

    def listed(token: str, **params: str) -> set[str]:
        response = client.get(QUARANTINES, params=params, headers=token)
        assert response.status_code == 200, response.text
        return {item["intake_quarantine_id"] for item in response.json()["items"]}

    assert listed(staff) == {mine}, "staff 既看不到别课行，也看不到未关联行"
    assert listed(staff, course_id=student[1], section_id=student[2]) == {mine}
    # 分页先过滤：limit=1 时第一页仍是本课序那一行（不是先取全局最新再过滤）。
    page = client.get(QUARANTINES, params={"limit": 1}, headers=staff).json()
    assert [item["intake_quarantine_id"] for item in page["items"]] == [mine]
    assert page["next_cursor"] is None, "可见集合只有一行，不应出现下一页"

    forbidden = client.get(
        QUARANTINES,
        params={"course_id": foreign_course, "section_id": str(uuid.uuid4())},
        headers=staff,
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.headers["content-type"].startswith("application/problem+json")

    admin = _auth(_admin_token(signer))
    assert listed(admin) == {mine, foreign, unscoped}
    assert listed(admin, course_id=student[1], section_id=student[2]) == {mine}
    admin_items = client.get(QUARANTINES, headers=admin).json()["items"]
    by_id = {item["intake_quarantine_id"]: item for item in admin_items}
    assert by_id[unscoped]["scope_course_id"] is None
    assert by_id[unscoped]["scope_section_id"] is None
    assert by_id[mine]["scope_course_id"] == student[1]


def test_quarantine_list_query_scope_validation(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """`course_id/section_id` 的请求校验：非 UUID 422、`section_id` 必须伴随 `course_id`。"""
    _seed_quarantine(
        engine, scope_course_id=student[1], scope_section_id=student[2], uid=1
    )
    token = _auth(_staff_token(signer, student))
    for params in (
        {"course_id": "not-a-uuid"},
        {"section_id": "not-a-uuid", "course_id": student[1]},
        {"section_id": student[2]},
    ):
        response = client.get(QUARANTINES, params=params, headers=token)
        assert response.status_code == 422, (params, response.text)
        assert response.headers["content-type"].startswith("application/problem+json")


def test_quarantine_staff_cannot_resolve_unscoped_or_foreign_records(
    client: TestClient, engine: Engine, signer: _Signer, student: tuple[str, str, str, str]
) -> None:
    """行级授权同样作用于归属：未关联/跨课记录对 staff 一律 403，且**一个字段都不写**。

    这也钉住判定顺序：staff 对无权限记录不能通过"已处理"（409）或"不可归属"（422）的差异
    探测它的状态——授权先于状态判定。
    """
    unscoped = _seed_quarantine(engine, uid=1)
    foreign_course = str(uuid.uuid4())
    foreign = _seed_quarantine(
        engine,
        uid=2,
        scope_course_id=foreign_course,
        scope_section_id=str(uuid.uuid4()),
    )
    # 越权记录即使已经处理过，也只应得到 403（不泄露"已处理"这一事实）。
    unscoped_resolved = _seed_quarantine(engine, uid=3)
    with engine.begin() as connection:
        connection.execute(
            sa.update(intake_quarantines)
            .where(
                intake_quarantines.c.intake_quarantine_id == uuid.UUID(unscoped_resolved)
            )
            .values(resolved=True, resolved_student_id=uuid.uuid4())
        )

    staff = _auth(_staff_token(signer, student))
    for quarantine_id in (unscoped, foreign, unscoped_resolved):
        response = client.post(
            RESOLVE.format(quarantine=quarantine_id),
            json={"student_id": str(uuid.uuid4())},
            headers=staff,
        )
        assert response.status_code == 403, (quarantine_id, response.text)

    # 越权归属不得改变任何记录的归属状态：先前已处理的那条保持原样，其余仍未处理。
    assert _resolved_state(engine, unscoped)[0] is False
    assert _resolved_state(engine, foreign)[0] is False
    assert _resolved_state(engine, unscoped_resolved)[0] is True


def test_quarantine_admin_may_resolve_unscoped_record(
    client: TestClient, engine: Engine, signer: _Signer
) -> None:
    """无课程关联的记录**只能**由平台管理员处置（本轮的安全要求）。

    归属体仍然只有 `student_id`/`note`：管理员在界面上选择的课程/课序只用于查找候选，
    不回写为原始 scope，也不改变"未关联"这一事实。
    """
    quarantine_id = _seed_quarantine(engine, uid=1)
    attributed = str(uuid.uuid4())
    response = client.post(
        RESOLVE.format(quarantine=quarantine_id),
        json={"student_id": attributed, "note": "管理员核对后归属"},
        headers=_auth(_admin_token(signer)),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved"] is True
    assert body["resolved_student_id"] == attributed
    assert body["scope_course_id"] is None, "归属不凭空补出课程关联"
    assert body["scope_section_id"] is None
    assert _resolved_state(engine, quarantine_id) == (
        True,
        attributed,
        "管理员核对后归属",
    )
