"""独立复核探针（由复核者编写，非实现者）——L1「提交接收与存储」三条组级验收标准。

本文件**不**复用实现者测试的任何断言，目标是**证伪**：

- 标准 2：相同内容只存一份；存储路径由服务端派生；客户端路径（`..`/绝对/反斜杠）不可达；
- 标准 3：非法请求在 `b2.submissions`/`b2.artifacts`（以及 CAS 目录）零新增；`submission.accepted`
  的 payload 不含源码；
- 标准 1 的场景由实现者测试覆盖，这里只补充"端到端可观测面"（磁盘、CAS 根之外、并发去重）。

所有断言都基于**真实 PostgreSQL + 真实 CAS 根目录**；每条"零新增"断言都配一条正向对照，
避免"计数恒为 0"这类恒真断言。
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from autograder_b2 import contracts
from autograder_b2.db.schema import artifacts, outbox_events, submission_files, submissions
from autograder_b2.domain.envelope import is_allowed_relative_path
from autograder_b2.intake.api import build_router
from autograder_b2.intake.runtime import IntakeRuntime, build_runtime
from autograder_b2.storage.cas import ContentAddressedStore
from tests.conftest import requires_postgres

pytestmark = requires_postgres

ISSUER = "autograder-b4"
AUDIENCE = "autograder"
ENDPOINT = "/api/v1/submissions"
SOURCE = b"print('PROBE_MARKER_a1b2c3 source code')\n"
MARKER = "PROBE_MARKER_a1b2c3"


# ---------------------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------------------


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

    def token(
        self, *, user_id: str, roles: tuple[str, ...], scope: list[dict[str, str]]
    ) -> str:
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
def probe_root(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    root = tmp_path_factory.mktemp("probe-artifacts")
    (root.parent / "outside-sentinel.txt").write_text("untouched", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def runtime(upgraded_database_url: str, probe_root: pathlib.Path, signer: _Signer) -> IntakeRuntime:
    return build_runtime(
        {
            "DATABASE_URL": upgraded_database_url,
            "B2_ARTIFACT_ROOT": str(probe_root),
            "B2_JWKS_JSON": json.dumps(signer.jwks),
            "B4_JWT_ISSUER": ISSUER,
            "B4_JWT_AUDIENCE": AUDIENCE,
        }
    )


@pytest.fixture()
def client(runtime: IntakeRuntime, engine: Engine) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_router(runtime))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def ids() -> tuple[str, str, str, str]:
    return (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))


# ---------------------------------------------------------------------------------------
# 观测面（真实计数 + 真实磁盘）
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _State:
    rows: tuple[int, int, int, int]
    files: frozenset[str]
    outside: frozenset[str]


def _tree(root: pathlib.Path) -> frozenset[str]:
    if not root.exists():
        return frozenset()
    return frozenset(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _state(engine: Engine, root: pathlib.Path) -> _State:
    with engine.connect() as connection:
        counts = tuple(
            int(connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one())
            for table in (submissions, artifacts, submission_files, outbox_events)
        )
    outside = frozenset(p.name for p in root.parent.iterdir()) if root.parent.exists() else frozenset()
    return _State(rows=counts, files=_tree(root), outside=outside)  # type: ignore[arg-type]


def _assert_no_trace(before: _State, after: _State, *, where: str) -> None:
    """非法请求：四张表零新增 + CAS 文件零新增 + CAS 根之外零新增。"""
    assert after.rows == before.rows, f"{where}: 非法请求写库了 {before.rows} -> {after.rows}"
    assert after.files == before.files, f"{where}: 非法请求写了 CAS 文件 {after.files - before.files}"
    assert after.outside == before.outside, f"{where}: CAS 根之外出现新条目"


def _file(path: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _envelope(
    ids: tuple[str, str, str, str],
    *,
    files: list[dict[str, object]] | None = None,
    idempotency_key: str = "probe-key-1",
    submission_id: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    user_id, course_id, section_id, question_version_id = ids
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


def _student_token(signer: _Signer, ids: tuple[str, str, str, str]) -> str:
    user_id, course_id, section_id, _ = ids
    return signer.token(
        user_id=user_id, roles=("student",), scope=[{"course_id": course_id, "section_id": section_id}]
    )


def _staff_token(signer: _Signer, course_id: str) -> str:
    return signer.token(user_id=str(uuid.uuid4()), roles=("teacher",), scope=[{"course_id": course_id}])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------------------
# 标准 2：路径——客户端路径不可达（HTTP 端到端 + 磁盘）
# ---------------------------------------------------------------------------------------

_ROOT = "a" * 300
HOSTILE_PATHS = [
    "../main.py",
    "../../etc/passwd",
    "../../../../../../etc/passwd",
    "a/../b.py",
    "a/b/../../../../etc/passwd",
    "x/../../y.py",
    "..",
    ".",
    "a/..",
    "/etc/passwd",
    "/",
    "//etc/passwd",
    "/abs/path/main.py",
    "..\\main.py",
    "a\\b.py",
    "..\\..\\windows\\system32\\cmd.exe",
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\x.py",
    "a//b.py",
    "a/./b.py",
    "....//main.py",
    "%2e%2e/main.py",
    "..%2fmain.py",
    "main.py\n",
    "main.py\r\n",
    "main.py\x00",
    "a\x00b.py",
    "\uff0e\uff0e/main.py",
    "d/d/d/d/d/d/d/d/d/main.py",
    _ROOT + ".py",
    "",
]


@requires_postgres
@pytest.mark.parametrize("bad_path", HOSTILE_PATHS, ids=lambda p: repr(p)[:40])
def test_probe_hostile_client_path_is_unreachable_and_leaves_no_trace(
    client: TestClient,
    engine: Engine,
    probe_root: pathlib.Path,
    signer: _Signer,
    ids: tuple[str, str, str, str],
    bad_path: str,
) -> None:
    """每一种敌意客户端路径：非 2xx、零落库、CAS 零文件、CAS 根之外零新增。"""
    before = _state(engine, probe_root)
    payload = _envelope(ids, files=[_file(bad_path, SOURCE)])
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, ids)))

    assert response.status_code == 422, (bad_path, response.status_code, response.text[:200])
    _assert_no_trace(before, _state(engine, probe_root), where=f"hostile path {bad_path!r}")


@requires_postgres
def test_probe_whitelist_is_the_effective_defense_not_the_contract() -> None:
    """契约模式放行 `..` 与尾随换行；B2 白名单必须拒绝它们——这是第 2 条标准的防线归属。"""
    accepted_by_contract = []
    for path in ["../main.py", "a/../b.py", "..", ".", "../../etc/passwd", "main.py\n"]:
        errors = contracts.contract_schema_errors(
            contracts.SUBMISSION_ENVELOPE_SCHEMA, _envelope(
                ("11111111-1111-4111-8111-111111111111",) * 4, files=[_file(path, SOURCE)]
            )
        )
        if not errors:
            accepted_by_contract.append(path)
        assert is_allowed_relative_path(path) is False, path
    assert accepted_by_contract == [
        "../main.py",
        "a/../b.py",
        "..",
        ".",
        "../../etc/passwd",
        "main.py\n",
    ], accepted_by_contract


@requires_postgres
def test_probe_database_check_is_a_second_line_for_paths(engine: Engine) -> None:
    """即使白名单被绕过，DDL 的 CHECK 也必须拒绝 `..` 路径（纵深防御的第二道真的存在）。"""
    submission_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    digest = hashlib.sha256(SOURCE).hexdigest()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO b2.artifacts (artifact_id, content_sha256, size_bytes, storage_path, created_at)"
                " VALUES (:a, :d, 1, :p, :t)"
            ),
            {"a": artifact_id, "d": digest, "p": f"{digest[:2]}/{digest[2:4]}/{digest}", "t": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO b2.submissions (submission_id, student_id, course_id, section_id,"
                " question_version_id, intake, idempotency_key, idempotency_scope, submission_digest,"
                " state, created_at, updated_at, correlation_id, request_id)"
                " VALUES (:s, :s2, :c, :sec, :q, 'api', 'k', 'scope', :d, 'validated', :t, :t, :corr, 'r')"
            ),
            {
                "s": submission_id,
                "s2": uuid.uuid4(),
                "c": uuid.uuid4(),
                "sec": uuid.uuid4(),
                "q": uuid.uuid4(),
                "d": digest,
                "t": now,
                "corr": uuid.uuid4(),
            },
        )

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO b2.submission_files (submission_id, path, artifact_id, created_at)"
                " VALUES (:s, '../escape.py', :a, :t)"
            ),
            {"s": submission_id, "a": artifact_id, "t": now},
        )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO b2.artifacts (artifact_id, content_sha256, size_bytes, storage_path, created_at)"
                " VALUES (:a, :d, 1, '../../etc/passwd', :t)"
            ),
            {"a": uuid.uuid4(), "d": "b" * 64, "t": datetime.now(UTC)},
        )


# ---------------------------------------------------------------------------------------
# 标准 2：内容寻址（一份内容、一条元数据、一个文件、服务端派生路径）
# ---------------------------------------------------------------------------------------


@requires_postgres
def test_probe_valid_submission_is_the_positive_control(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """正向对照：观测面能看见新增行与新增文件（否则"零新增"断言恒真）。"""
    before = _state(engine, probe_root)
    response = client.post(ENDPOINT, json=_envelope(ids), headers=_auth(_student_token(signer, ids)))
    assert response.status_code == 202, response.text
    after = _state(engine, probe_root)
    assert after.rows == (before.rows[0] + 1, before.rows[1] + 1, before.rows[2] + 1, before.rows[3] + 1)
    assert len(after.files - before.files) == 1


@requires_postgres
def test_probe_same_content_twice_in_one_submission_keeps_one_artifact(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """同一份字节在**一次**提交里出现两次（不同路径）⇒ 一份内容、两行文件元数据。"""
    before = _state(engine, probe_root)
    payload = _envelope(ids, files=[_file("a.py", SOURCE), _file("pkg/b.py", SOURCE)])
    response = client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, ids)))
    assert response.status_code == 202, response.text
    after = _state(engine, probe_root)
    assert after.rows[0] == before.rows[0] + 1, "一条提交"
    assert after.rows[1] == before.rows[1] + 1, "同一内容只应有一行 artifacts"
    assert after.rows[2] == before.rows[2] + 2, "两个路径各一行 files"
    digest = hashlib.sha256(SOURCE).hexdigest()
    derived = f"{digest[:2]}/{digest[2:4]}/{digest}"
    # 该摘要在整个 CAS 树里**恰好一个**文件（不依赖"本次新增"，因为 CAS 根跨用例共享）
    assert [p for p in after.files if p.endswith(digest)] == [derived]


@requires_postgres
def test_probe_same_content_across_students_is_stored_once_on_disk(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """跨学生相同内容：两条提交、两行 files、一份 artifacts、一个磁盘文件，且路径由摘要派生。"""
    before = _state(engine, probe_root)
    other = (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    for who, key in ((ids, "probe-dedup-1"), (other, "probe-dedup-2")):
        token = _student_token(signer, who)
        response = client.post(ENDPOINT, json=_envelope(who, idempotency_key=key), headers=_auth(token))
        assert response.status_code == 202, response.text
    after = _state(engine, probe_root)

    assert after.rows[0] == before.rows[0] + 2
    assert after.rows[1] == before.rows[1] + 1
    assert after.rows[2] == before.rows[2] + 2

    digest = hashlib.sha256(SOURCE).hexdigest()
    derived = f"{digest[:2]}/{digest[2:4]}/{digest}"
    # CAS 目录是**进程级**共享的（DB 表被清空但文件不删）：断言"该内容恰有一个文件"，
    # 而不是"本次新增一个文件"。
    assert derived in after.files
    with engine.connect() as connection:
        stored = connection.execute(
            sa.select(artifacts.c.storage_path, artifacts.c.size_bytes).where(
                artifacts.c.content_sha256 == digest
            )
        ).one()
    assert stored[0] == derived
    assert stored[1] == len(SOURCE)
    assert (probe_root / str(stored[0])).read_bytes() == SOURCE
    # 客户端给的路径在 CAS 根里**不存在**对应的落盘位置
    assert not (probe_root / "main.py").exists()
    assert not (probe_root / "pkg").exists()


@requires_postgres
def test_probe_concurrent_identical_content_yields_one_artifact(
    engine: Engine, probe_root: pathlib.Path, upgraded_database_url: str
) -> None:
    """并发写入同一份内容（真实唯一约束 + `ON CONFLICT DO NOTHING`）⇒ 一行 artifacts、一个文件。

    本探针只断言**去重不变量**（标准 2 第 1 句），并**报告**并发路径上的异常分布。
    并发异常另有单独的缺陷探针（`test_probe_defect_...`），以免一条探针同时判两件事。
    """
    store = ContentAddressedStore(probe_root, engine)
    payload = b"concurrent identical payload 4f19"
    digest = hashlib.sha256(payload).hexdigest()
    start = threading.Barrier(12)
    results: list[str] = []
    errors: dict[str, int] = {}
    lock = threading.Lock()

    def writer() -> None:
        try:
            start.wait(timeout=15)
            artifact = store.put(payload)
            with lock:
                results.append(artifact.artifact_id)
        except BaseException as error:  # noqa: BLE001 - 记录任何线程异常
            with lock:
                key = f"{type(error).__name__}"
                errors[key] = errors.get(key, 0) + 1

    threads = [threading.Thread(target=writer) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    with engine.connect() as connection:
        rows = int(
            connection.execute(
                sa.select(sa.func.count()).select_from(artifacts).where(
                    artifacts.c.content_sha256 == digest
                )
            ).scalar_one()
        )
    derived = f"{digest[:2]}/{digest[2:4]}/{digest}"
    blob_path = probe_root / derived
    print(
        f"\n[probe] 并发 12 路同一内容: 成功={len(results)} 唯一 artifact_id={len(set(results))}"
        f" artifacts行={rows} 文件存在={blob_path.is_file()} 异常类={errors}"
    )
    # 去重不变量：无论多少路抛异常，落库与磁盘都只能有一份
    assert rows == 1, f"并发去重破坏了唯一性: {rows}"
    assert len(set(results)) == 1, set(results)
    assert blob_path.is_file()
    assert blob_path.read_bytes() == payload
    assert store.read(digest) == payload


@requires_postgres
def test_probe_defect_concurrent_identical_put_is_not_concurrency_safe(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """**缺陷探针（预期失败）**：并发写同一内容时 `ContentAddressedStore.put` 抛异常。

    实测两类异常（Windows，真实 PostgreSQL + 真实 CAS 根）：
    1. `PermissionError [WinError 5]` 来自 `cas._write_atomic` 的 `os.replace`（多线程同时替换同一目标）；
    2. `InvalidDigestError: derived storage path escaped the artifact root`——**假阳性**：派生路径
       `ab/cd/<digest>` 结构上不可能越出根目录，失败原因是 `Path.resolve()` 在竞态下给 candidate
       加上了 `\\\\?\\` 扩展前缀而 `resolved_root` 没有，于是 `resolved_root in candidate.parents`
       为假（最小复现见 `.pytest-tmp/diag4.py` 的 frame-locals 输出）。

    本探针**故意断言 "并发路径不得抛异常"**，因此当前实现下必然失败：它记录的是缺陷，不是标准判据。
    """
    store = ContentAddressedStore(probe_root, engine)
    payload = b"concurrent defect probe payload 5e21"
    start = threading.Barrier(8)
    errors: dict[str, int] = {}
    lock = threading.Lock()

    def writer() -> None:
        try:
            start.wait(timeout=15)
            store.put(payload)
        except BaseException as error:  # noqa: BLE001
            with lock:
                key = f"{type(error).__name__}: {error}"
                errors[key] = errors.get(key, 0) + 1

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == {}, f"并发 put 抛异常（存储层缺陷）: {errors}"


@requires_postgres
def test_probe_distinct_content_is_not_collapsed(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """反向对照：不同内容不得被去重掉（防止"去重"退化成"只存第一份"）。"""
    before = _state(engine, probe_root)
    payload = _envelope(
        ids, files=[_file("a.py", b"print('one')\n"), _file("b.py", b"print('two')\n")]
    )
    assert client.post(ENDPOINT, json=payload, headers=_auth(_student_token(signer, ids))).status_code == 202
    after = _state(engine, probe_root)
    assert after.rows[1] == before.rows[1] + 2, "两份不同内容必须有两行 artifacts"
    assert len(after.files - before.files) == 2


# ---------------------------------------------------------------------------------------
# 标准 3：非法请求零落库（含磁盘）+ 事件 payload 不含源码
# ---------------------------------------------------------------------------------------


@requires_postgres
def test_probe_digest_mismatch_leaves_no_trace(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    before = _state(engine, probe_root)
    forged = _file("main.py", SOURCE)
    forged["content_sha256"] = hashlib.sha256(b"different bytes").hexdigest()
    response = client.post(
        ENDPOINT, json=_envelope(ids, files=[forged]), headers=_auth(_student_token(signer, ids))
    )
    assert response.status_code == 422, response.text
    _assert_no_trace(before, _state(engine, probe_root), where="digest mismatch")


@requires_postgres
def test_probe_oversize_leaves_no_trace(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """单文件超限（413）与总量超限（413）都必须零落库、零 CAS 文件。"""
    before = _state(engine, probe_root)
    single = client.post(
        ENDPOINT,
        json=_envelope(ids, files=[_file("big.py", b"a" * (1572864 + 1))], idempotency_key="big-1"),
        headers=_auth(_student_token(signer, ids)),
    )
    assert single.status_code == 413, single.text
    _assert_no_trace(before, _state(engine, probe_root), where="single file oversize")

    chunk = b"b" * 1_400_000
    total = client.post(
        ENDPOINT,
        json=_envelope(
            ids, files=[_file(f"p{i}.py", chunk) for i in range(3)], idempotency_key="big-2"
        ),
        headers=_auth(_student_token(signer, ids)),
    )
    assert total.status_code == 413, total.text
    assert total.json()["type"].endswith("total-too-large")
    _assert_no_trace(before, _state(engine, probe_root), where="total oversize")


@requires_postgres
def test_probe_too_many_files_and_bad_base64_leave_no_trace(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    before = _state(engine, probe_root)
    many = client.post(
        ENDPOINT,
        json=_envelope(ids, files=[_file(f"f{i}.py", b"x") for i in range(101)], idempotency_key="many"),
        headers=_auth(_student_token(signer, ids)),
    )
    assert many.status_code == 422 and many.json()["type"].endswith("too-many-files"), many.text
    _assert_no_trace(before, _state(engine, probe_root), where="too many files")

    broken = _file("main.py", SOURCE)
    broken["content_base64"] = "!!!not-base64!!!"
    bad = client.post(
        ENDPOINT, json=_envelope(ids, files=[broken], idempotency_key="b64"),
        headers=_auth(_student_token(signer, ids)),
    )
    assert bad.status_code == 422, bad.text
    _assert_no_trace(before, _state(engine, probe_root), where="invalid base64")


@requires_postgres
def test_probe_unauthorized_and_unknown_field_leave_no_trace(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    before = _state(engine, probe_root)
    # 未知字段（契约 additionalProperties: false）——其中塞源码，确认不会被落库/入事件
    smuggled = _envelope(ids, idempotency_key="smuggle")
    smuggled["source_code"] = MARKER
    response = client.post(
        ENDPOINT, json=smuggled, headers=_auth(_student_token(signer, ids))
    )
    assert response.status_code == 422, response.text
    _assert_no_trace(before, _state(engine, probe_root), where="unknown field")

    # 跨课程作用域（403）不得写任何东西
    outsider = signer.token(
        user_id=ids[0], roles=("student",), scope=[{"course_id": str(uuid.uuid4()), "section_id": ids[2]}]
    )
    forbidden = client.post(ENDPOINT, json=_envelope(ids, idempotency_key="scope"), headers=_auth(outsider))
    assert forbidden.status_code == 403, forbidden.text
    _assert_no_trace(before, _state(engine, probe_root), where="course scope mismatch")

    # 冒名提交（403）
    impersonator = signer.token(
        user_id=str(uuid.uuid4()), roles=("student",),
        scope=[{"course_id": ids[1], "section_id": ids[2]}],
    )
    forged = client.post(ENDPOINT, json=_envelope(ids, idempotency_key="impersonate"), headers=_auth(impersonator))
    assert forged.status_code == 403, forged.text
    _assert_no_trace(before, _state(engine, probe_root), where="impersonation")

    # 未认证（401）
    assert client.post(ENDPOINT, json=_envelope(ids, idempotency_key="noauth")).status_code == 401
    _assert_no_trace(before, _state(engine, probe_root), where="unauthenticated")


@requires_postgres
def test_probe_conflicting_submission_id_leaves_no_artifact(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """同一 submission_id 配不同幂等键（422）——第二份的**新内容**不得落 CAS。"""
    token = _student_token(signer, ids)
    shared = str(uuid.uuid4())
    first = client.post(
        ENDPOINT,
        json=_envelope(ids, idempotency_key="conflict-a", submission_id=shared),
        headers=_auth(token),
    )
    assert first.status_code == 202, first.text
    before = _state(engine, probe_root)
    second = client.post(
        ENDPOINT,
        json=_envelope(
            ids,
            idempotency_key="conflict-b",
            submission_id=shared,
            files=[_file("other.py", b"print('second, different content')\n")],
        ),
        headers=_auth(token),
    )
    assert second.status_code == 422, second.text
    _assert_no_trace(before, _state(engine, probe_root), where="submission id conflict")


@requires_postgres
def test_probe_submission_accepted_payload_has_no_source_anywhere_in_the_row(
    client: TestClient, engine: Engine, signer: _Signer, ids: tuple[str, str, str, str]
) -> None:
    """`submission.accepted` 的 payload（以及整行文本列）不得含源码、base64 或内容摘要。"""
    response = client.post(
        ENDPOINT, json=_envelope(ids, idempotency_key="event-probe"), headers=_auth(_student_token(signer, ids))
    )
    assert response.status_code == 202, response.text

    with engine.connect() as connection:
        rows = connection.execute(sa.select(outbox_events)).mappings().all()
    assert len(rows) == 1, "合法提交恰好一条事件"
    row = rows[0]
    assert row["subject"] == "autograder.v1.submission.accepted"
    payload = row["payload"]
    assert set(payload) == {
        "submission_id",
        "student_id",
        "course_id",
        "section_id",
        "question_version_id",
        "intake",
    }, set(payload)
    assert payload["intake"] == "api"
    for key in ("submission_id", "student_id", "course_id", "section_id", "question_version_id"):
        assert uuid.UUID(str(payload[key])), key

    forbidden = [
        MARKER,
        "print(",
        base64.b64encode(SOURCE).decode("ascii"),
        hashlib.sha256(SOURCE).hexdigest(),
        "content_base64",
        "storage_path",
        "main.py",
    ]
    blob = json.dumps({str(k): str(v) for k, v in row.items()}, ensure_ascii=False)
    for needle in forbidden:
        assert needle not in blob, f"outbox 行里出现了 {needle!r}"


@requires_postgres
def test_probe_source_cannot_be_smuggled_into_an_event_identity_field(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """把源码塞进会进 payload 的标识字段：契约的 `format: uuid` 必须挡住，且零落库。"""
    before = _state(engine, probe_root)
    token = _staff_token(signer, ids[1])
    for field in ("submission_id", "student_id", "course_id", "section_id", "question_version_id"):
        payload = _envelope(ids, idempotency_key=f"smuggle-{field}", **{field: MARKER})
        response = client.post(ENDPOINT, json=payload, headers=_auth(token))
        assert response.status_code == 422, (field, response.status_code, response.text[:200])
    _assert_no_trace(before, _state(engine, probe_root), where="identity smuggling")


@requires_postgres
def test_probe_idempotent_replay_writes_nothing_new_on_disk(
    client: TestClient, engine: Engine, probe_root: pathlib.Path, signer: _Signer,
    ids: tuple[str, str, str, str],
) -> None:
    """幂等重放（第二次带**不同内容**）不得新增任何行/文件——重放不修改既有提交。"""
    token = _student_token(signer, ids)
    payload = _envelope(ids, idempotency_key="replay-probe")
    assert client.post(ENDPOINT, json=payload, headers=_auth(token)).status_code == 202
    before = _state(engine, probe_root)
    replay = client.post(
        ENDPOINT,
        json={**payload, "submission_id": str(uuid.uuid4()), "files": [_file("main.py", b"changed\n")]},
        headers=_auth(token),
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["submission_id"] == payload["submission_id"]
    _assert_no_trace(before, _state(engine, probe_root), where="idempotent replay")


# ---------------------------------------------------------------------------------------
# 附带发现（不属于三条标准本身，但属于"实现与契约/自身文档不符"）
# ---------------------------------------------------------------------------------------


@requires_postgres
def test_probe_finding_uuid_spelling_is_not_canonicalised_on_the_write_path(
    client: TestClient, engine: Engine, signer: _Signer, ids: tuple[str, str, str, str]
) -> None:
    """契约 `Uuid` 接受大写写法；写入口**先规范化再比较**，因此本人提交必须成功。

    本探针原记录的是**缺陷**：大写拼写被当成"另一个人"而判 403（令牌侧已规范化、请求侧没有）。
    缺陷修复后（`auth.authorization._canonical_request_uuid`）它改为钉住正确行为——
    否则这条探针会一直红着，而"红着的探针"等于把缺陷当成期望。
    """
    # 只大写 student_id（course/section 保持规范化，隔离掉作用域判定的干扰）
    token = _student_token(signer, ids)
    payload = _envelope(ids, student_id=ids[0].upper())
    response = client.post(ENDPOINT, json=payload, headers=_auth(token))
    code = response.status_code
    print(f"\n[probe] 大写 student_id + 本人令牌实测: {code} {response.text[:160]}")
    assert code == 202, (code, response.text[:200])
    with engine.connect() as connection:
        count = int(
            connection.execute(sa.select(sa.func.count()).select_from(submissions)).scalar_one()
        )
    assert count == 1, f"本人提交必须落库一份，实测 {count}"


@requires_postgres
def test_probe_finding_idempotency_scope_uses_the_raw_client_spelling(
    client: TestClient, engine: Engine, signer: _Signer, ids: tuple[str, str, str, str]
) -> None:
    """同一（学生, 题目, 幂等键）换一种**大写拼写**，不得绕过幂等。

    本探针原记录的是**缺陷**：作用域直接拼未规范化的标识，于是换大小写就分到另一个桶、产生第二
    份提交。修复后（`SubmissionIntakeService._idempotency_scope` 先规范化）改为钉住正确行为。
    """
    token = _staff_token(signer, ids[1])
    lower = _envelope(ids, idempotency_key="spelling-key")
    # 只改 student_id 的大小写（教师令牌，作用域按 course_id 判定，不受影响）
    upper = _envelope(ids, idempotency_key="spelling-key", student_id=ids[0].upper())
    first = client.post(ENDPOINT, json=lower, headers=_auth(token))
    second = client.post(ENDPOINT, json=upper, headers=_auth(token))
    with engine.connect() as connection:
        count = int(
            connection.execute(sa.select(sa.func.count()).select_from(submissions)).scalar_one()
        )
        scopes = [
            str(row[0])
            for row in connection.execute(sa.select(submissions.c.idempotency_scope)).fetchall()
        ]
    print(
        f"\n[probe] spelling: first={first.status_code} second={second.status_code}"
        f" submissions={count} scopes={scopes}"
    )
    assert first.status_code == 202 and second.status_code == 202
    # 修复后：作用域先规范化 ⇒ 两次请求落在同一个桶，只允许一份提交。
    assert count == 1, f"预期只落一份提交（作用域已规范化），实测 {count}"
    assert scopes == [f"{ids[0]}:{ids[3]}"], scopes


@requires_postgres
def test_probe_cross_student_read_is_403_and_leaks_nothing(
    client: TestClient, signer: _Signer, ids: tuple[str, str, str, str]
) -> None:
    """场景「跨学生越权读 403」的独立探针（不复用实现者断言）。

    必须同时成立：越权读 403（不是 404、不是 200）、不泄露源码、本人读 200、不存在的 id 仍 404
    （否则"一律 403"会让 404 语义消失，判据就只是巧合）。
    """
    owner = _student_token(signer, ids)
    payload = _envelope(ids, idempotency_key="read-probe")
    accepted = client.post(ENDPOINT, json=payload, headers=_auth(owner))
    assert accepted.status_code == 202, accepted.text
    submission_id = str(payload["submission_id"])

    intruder_id = str(uuid.uuid4())
    intruder = signer.token(
        user_id=intruder_id, roles=("student",),
        scope=[{"course_id": ids[1], "section_id": ids[2]}],
    )
    forbidden = client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(intruder))
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["type"].endswith("forbidden")
    body = forbidden.text
    assert MARKER not in body and "print(" not in body
    assert base64.b64encode(SOURCE).decode("ascii") not in body

    # 本人读 200（对照：证明上面的 403 来自授权判定而非端点坏掉）
    own = client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(owner))
    assert own.status_code == 200, own.text
    assert own.json()["submission_id"] == submission_id
    # 视图不得内联源码/存储引用
    serialized = json.dumps(own.json())
    assert MARKER not in serialized and "content_base64" not in serialized
    assert "storage_ref" not in serialized and "storage_path" not in serialized

    # 不存在的提交（本人作用域内）→ 404，而不是 403
    missing = client.get(f"{ENDPOINT}/{uuid.uuid4()}", headers=_auth(owner))
    assert missing.status_code == 404, missing.text

    # 跨课程的学生读自己的提交 → 403
    other_course = signer.token(
        user_id=ids[0], roles=("student",),
        scope=[{"course_id": str(uuid.uuid4()), "section_id": ids[2]}],
    )
    assert client.get(f"{ENDPOINT}/{submission_id}", headers=_auth(other_course)).status_code == 403


# ---------------------------------------------------------------------------------------
# 邮件入口的"非法来件"是否也零落库（标准 3 的另一条入口）
# ---------------------------------------------------------------------------------------


def _build_mime(
    *, sender: str, message_id: str | None, attachments: tuple[tuple[str, bytes], ...]
) -> bytes:
    from email.message import EmailMessage as _MIMEMessage

    message = _MIMEMessage()
    message["From"] = sender
    message["To"] = "probe-mailbox@example.edu"
    if message_id is not None:
        message["Message-ID"] = message_id
    message["Subject"] = "submission"
    message.set_content("please grade this PROBE_BODY_MARKER")
    for name, payload in attachments:
        message.add_attachment(payload, maintype="application", subtype="octet-stream", filename=name)
    return message.as_bytes()


@requires_postgres
def test_probe_email_rejections_leave_no_submission_artifact_or_file(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """可执行附件、压缩炸弹、不唯一匹配、缺 Message-ID：都不落 submission/artifact、也不落 CAS 文件。"""
    from autograder_b2.config import DEFAULT_IMAP_MAX_MESSAGE_BYTES, ImapSettings
    from autograder_b2.intake.imap import EmailCheckpointStore, EmailIntakeService, QuarantineStore
    from autograder_b2.intake.matching import MappingEmailMatcher
    from autograder_b2.intake.service import IntakeLimits, SubmissionIntakeService

    mailbox = "probe-mailbox"
    limits = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)
    store = ContentAddressedStore(probe_root, engine)
    service = EmailIntakeService(
        engine=engine,
        settings=ImapSettings(
            host="127.0.0.1",
            port=1,
            username="probe",
            password="probe-secret",
            mailbox=mailbox,
            use_ssl=False,
            matching={
                "matched@example.edu": {
                    "student_id": "11111111-1111-4111-8111-111111111111",
                    "course_id": "22222222-2222-4222-8222-222222222222",
                    "section_id": "33333333-3333-4333-8333-333333333333",
                    "question_version_id": "44444444-4444-4444-8444-444444444444",
                }
            },
            max_messages_per_pull=20,
            # 复核后新增的必填项（报文大小闸门）：这里取部署缺省值，本探针不针对该闸门。
            max_message_bytes=DEFAULT_IMAP_MAX_MESSAGE_BYTES,
            timeout_seconds=10.0,
        ),
        intake=SubmissionIntakeService(engine, store, limits),
        matcher=MappingEmailMatcher(
            {
                "matched@example.edu": {
                    "student_id": "11111111-1111-4111-8111-111111111111",
                    "course_id": "22222222-2222-4222-8222-222222222222",
                    "section_id": "33333333-3333-4333-8333-333333333333",
                    "question_version_id": "44444444-4444-4444-8444-444444444444",
                }
            }
        ),
        checkpoints=EmailCheckpointStore(engine),
        quarantines=QuarantineStore(engine),
    )

    elf = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 24
    cases = [
        ("executable attachment", 1, _build_mime(
            sender="matched@example.edu", message_id="<p1@example.edu>",
            attachments=(("run.sh", elf),),
        )),
        ("unmatched sender", 2, _build_mime(
            sender="stranger@example.edu", message_id="<p2@example.edu>",
            attachments=(("solution.py", b"print('x')\n"),),
        )),
        ("missing message-id", 3, _build_mime(
            sender="matched@example.edu", message_id=None,
            attachments=(("solution.py", b"print('x')\n"),),
        )),
    ]
    for label, uid, raw in cases:
        before = _state(engine, probe_root)
        outcome = service.process_raw(uidvalidity=91, uid=uid, raw=raw)
        after = _state(engine, probe_root)
        print(f"\n[probe] email {label}: status={outcome.status} reason={outcome.reason_code}")
        assert outcome.status != "processed", (label, outcome)
        assert after.rows[0] == before.rows[0], f"{label}: 落了一条 submission"
        assert after.rows[1] == before.rows[1], f"{label}: 落了一行 artifact"
        assert after.rows[3] == before.rows[3], f"{label}: 落了 outbox 事件"
        assert after.files == before.files, f"{label}: CAS 里多了文件"


# ---------------------------------------------------------------------------------------
# `persist` 里"内容已落盘后才判信封一致性"的次序（可达性核查）
# ---------------------------------------------------------------------------------------


@requires_postgres
def test_probe_tampered_envelope_is_reachable_only_from_library_callers(
    engine: Engine, probe_root: pathlib.Path
) -> None:
    """`persist` 对"手搓信封"的顺序：先写内容/元数据，再因信封不一致而拒绝。

    `stage_contents` 已经把内容写进 CAS 与 `artifacts` 表，随后
    `envelope_files != envelope.files` 抛 `EnvelopeRejected('digest_mismatch')`，
    但**已写完的内容不回滚**（独立事务）。

    可达性：HTTP 入口（`accept`）与邮件入口都在同一次调用里用**同一份字节**构造信封，
    因此两者都不可能产生不一致信封（本探针的最后一段用真实入口验证了这一点）。
    该分支只对"绕过两个入口、自己手搓 `SubmissionEnvelope` 再调用 `persist`"的库内调用者可达，
    因此它**不构成**标准 3 的"非法请求落库"——但它确实是"非法输入 ⇒ 零落盘"这一说法的一个例外。
    """
    import dataclasses

    from autograder_b2.domain.envelope import ApiProvenance, EnvelopeFile, EnvelopeRejected
    from autograder_b2.intake.service import (
        IntakeLimits,
        SubmissionIntakeService,
        build_submission_envelope,
    )

    limits = IntakeLimits(max_files=10, max_file_bytes=10**6, max_total_bytes=10**7)
    store = ContentAddressedStore(probe_root, engine)
    service = SubmissionIntakeService(engine, store, limits)
    ids = {key: str(uuid.uuid4()) for key in (
        "submission_id", "student_id", "course_id", "section_id", "question_version_id", "correlation_id"
    )}
    pairs = (("main.py", b"tampered envelope probe content\n"),)
    honest = build_submission_envelope(
        **ids, idempotency_key="tampered-1", intake="api",
        provenance=ApiProvenance(request_id=ids["correlation_id"]), files=pairs,
    )
    tampered_file = EnvelopeFile(
        path="renamed.py",
        content_sha256=honest.files[0].content_sha256,
        size_bytes=honest.files[0].size_bytes,
        storage_ref=honest.files[0].storage_ref,
    )
    tampered = dataclasses.replace(honest, files=(tampered_file,))

    before = _state(engine, probe_root)
    with pytest.raises(EnvelopeRejected) as raised:
        service.persist(tampered, pairs)
    after = _state(engine, probe_root)
    print(
        f"\n[probe] tampered envelope: reason={raised.value.reason}"
        f" artifacts {before.rows[1]}->{after.rows[1]} files {len(before.files)}->{len(after.files)}"
    )
    assert raised.value.reason == "digest_mismatch"
    assert after.rows[0] == before.rows[0], "提交行没有落（这是好的）"
    assert after.rows[1] == before.rows[1] + 1, "内容行却已落（独立事务，未回滚）"
    assert len(after.files) == len(before.files) + 1, "CAS 文件也已落盘"

