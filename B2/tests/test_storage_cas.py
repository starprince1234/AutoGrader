"""内容寻址存储与路径白名单测试（T-STORE-*）。

覆盖本小类三条验收：
1. 相同内容两次写入只产生一份内容，两条元数据指向同一摘要；
2. 路径白名单在**存储边界**真的拒穿越/绝对/反斜杠/控制字符/超长/超深；
3. 孤儿回收只删无引用内容、写审计记录、被引用内容不删。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2.db.schema import artifacts, metadata, submission_files, submissions
from autograder_b2.domain.clock import sha256_hex
from autograder_b2.domain.envelope import EnvelopeRejected
from autograder_b2.storage import (
    ArtifactContentMissingError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ContentAddressedStore,
    InvalidDigestError,
    absolute_path_under,
    storage_path_for,
)
from tests.conftest import requires_postgres

pytestmark = requires_postgres

SOURCE_A = b"print('hello')\n"
SOURCE_B = b"print('world')\n"


def _insert_submission(engine: Engine) -> str:
    """建一行 `api` 来源的提交。

    `correlation_id` 与 `request_id`（迁移 0008）必须一起写：`intake='api'` 的行由
    `ck_submissions_provenance_shape` 要求"有 request_id、无邮件身份"，而 `correlation_id` 是
    NOT NULL——本夹具构造的是与冻结信封一致的行。
    """
    submission_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(submissions).values(
                submission_id=submission_id,
                student_id=uuid.uuid4(),
                course_id=uuid.uuid4(),
                section_id=uuid.uuid4(),
                question_version_id=uuid.uuid4(),
                intake="api",
                idempotency_key=f"key-{uuid.uuid4().hex}",
                idempotency_scope=f"scope-{uuid.uuid4().hex}",
                submission_digest="b" * 64,
                state="received",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
                correlation_id=uuid.uuid4(),
                request_id=f"req-{uuid.uuid4().hex}",
            )
        )
    return str(submission_id)


def _artifact_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one())


# --- 概念 1：SHA-256 去重 ----------------------------------------------------------------


def test_same_content_written_twice_stores_one_artifact(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    first = store.put(SOURCE_A)
    second = store.put(SOURCE_A)

    assert first.content_sha256 == second.content_sha256 == sha256_hex(SOURCE_A)
    assert first.artifact_id == second.artifact_id, "同一内容必须复用同一条元数据"
    assert first.deduplicated is False and second.deduplicated is True
    assert _artifact_count(engine) == 1, "同一内容只允许一份 artifacts 行"

    files_on_disk = [path for path in artifact_root.rglob("*") if path.is_file()]
    assert len(files_on_disk) == 1, f"同一内容只允许一份内容文件，实际 {files_on_disk}"


def test_two_submissions_share_one_artifact_but_keep_two_metadata_rows(
    engine: Engine, artifact_root: Path
) -> None:
    """验收锚点：两条元数据各自指向同一摘要，而内容只有一份。"""
    store = ContentAddressedStore(artifact_root, engine)
    first_submission = _insert_submission(engine)
    second_submission = _insert_submission(engine)

    first_files = store.put_submission_files(first_submission, [("main.py", SOURCE_A)])
    second_files = store.put_submission_files(second_submission, [("solution.py", SOURCE_A)])

    assert first_files[0].content_sha256 == second_files[0].content_sha256
    assert first_files[0].path != second_files[0].path
    assert _artifact_count(engine) == 1

    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(submission_files.c.submission_id, submission_files.c.artifact_id)
        ).fetchall()
    assert len(rows) == 2, "两条提交文件元数据都必须存在"
    assert len({str(row[1]) for row in rows}) == 1, "两条元数据必须指向同一 artifact"


def test_distinct_content_is_stored_separately(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    store.put(SOURCE_A)
    store.put(SOURCE_B)
    assert _artifact_count(engine) == 2


def test_read_round_trip_and_integrity(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    stored = store.put(SOURCE_A)
    assert store.read(stored.content_sha256) == SOURCE_A
    assert store.exists(stored.content_sha256) is True
    assert store.exists(sha256_hex(SOURCE_B)) is False

    # 内容被外部篡改 → 读取必须报完整性问题，而不是返回坏内容
    absolute_path_under(artifact_root, stored.content_sha256).write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        store.read(stored.content_sha256)

    absolute_path_under(artifact_root, stored.content_sha256).unlink()
    with pytest.raises(ArtifactContentMissingError):
        store.read(stored.content_sha256)


def test_read_unknown_digest_raises(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    with pytest.raises(ArtifactNotFoundError):
        store.read("f" * 64)
    with pytest.raises(ArtifactNotFoundError):
        store.read("not-a-digest")


# --- 概念 2：服务端路径派生 + 路径白名单在存储边界生效 ------------------------------------


def test_storage_path_is_derived_from_digest_only() -> None:
    digest = "abcdef" + "0" * 58
    assert storage_path_for(digest) == f"ab/cd/{digest}"
    assert not storage_path_for(digest).startswith("/")
    assert ".." not in storage_path_for(digest)
    with pytest.raises(InvalidDigestError):
        storage_path_for("../etc/passwd")
    with pytest.raises(InvalidDigestError):
        storage_path_for("A" * 64)


def test_derived_path_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    resolved = absolute_path_under(root, "a" * 64)
    assert str(resolved).startswith(str(root.resolve()))
    with pytest.raises(InvalidDigestError):
        absolute_path_under(root, "../../../etc/passwd")


@pytest.mark.parametrize(
    "bad_path",
    [
        "../main.py",
        "../../etc/passwd",
        "a/../../b.py",
        "/etc/passwd",
        "..\\main.py",
        "a\\b.py",
        "",
        "a//b.py",
        "a\x00b.py",
        "x" * 201,
        "/".join(["d"] * 9 + ["main.py"]),
        "./main.py",
    ],
)
def test_storage_rejects_illegal_client_paths(
    engine: Engine, artifact_root: Path, bad_path: str
) -> None:
    """客户端路径在存储边界被拒：连内容都不应落盘。"""
    store = ContentAddressedStore(artifact_root, engine)
    submission_id = _insert_submission(engine)
    with pytest.raises(EnvelopeRejected) as excinfo:
        store.put_submission_files(submission_id, [(bad_path, SOURCE_A)])
    assert excinfo.value.reason == "path_not_allowed"
    assert _artifact_count(engine) == 0, "被拒的提交不得留下任何内容"
    assert not any(path.is_file() for path in artifact_root.rglob("*"))


@pytest.mark.parametrize("good_path", ["main.py", "src/main.py", "a-b_c.d.py", "solution/part_1.py"])
def test_storage_accepts_legitimate_paths(engine: Engine, artifact_root: Path, good_path: str) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    submission_id = _insert_submission(engine)
    stored = store.put_submission_files(submission_id, [(good_path, SOURCE_A)])
    assert [item.path for item in stored] == [good_path]
    assert stored[0].storage_ref == storage_path_for(stored[0].content_sha256), "存储引用必须是服务端派生值"


def test_storage_rejects_empty_and_duplicate_paths(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    submission_id = _insert_submission(engine)
    with pytest.raises(EnvelopeRejected) as empty:
        store.put_submission_files(submission_id, [])
    assert empty.value.reason == "empty_fileset"
    with pytest.raises(EnvelopeRejected) as duplicate:
        store.put_submission_files(submission_id, [("a.py", SOURCE_A), ("a.py", SOURCE_B)])
    assert duplicate.value.reason == "duplicate_path"


def test_storage_rejects_unknown_submission(engine: Engine, artifact_root: Path) -> None:
    """外键保证提交文件不会挂到不存在的提交上。"""
    store = ContentAddressedStore(artifact_root, engine)
    with pytest.raises(IntegrityError):
        store.put_submission_files(str(uuid.uuid4()), [("main.py", SOURCE_A)])


def test_core_metadata_matches_database(engine: Engine) -> None:
    """映像与 DDL 防漂移：Core 表/列必须与真实库逐一对齐（避免两处定义悄悄分叉）。"""
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'b2' ORDER BY table_name, ordinal_position"
            )
        ).fetchall()
    in_database: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        if table_name == "alembic_version_b2":
            continue
        in_database.setdefault(table_name, []).append(column_name)
    in_metadata = {
        table.name: [column.name for column in table.columns]
        for table in metadata.tables.values()
        if table.schema == "b2"
    }
    assert in_metadata == in_database, "SQLAlchemy 映像与数据库 DDL 不一致"


def test_concurrent_identical_puts_succeed_and_store_one_artifact(
    engine: Engine, artifact_root: Path
) -> None:
    """并发写**同一内容**：全部成功、恰好一行元数据、一个内容文件。

    这是组级复核 #1 的回归：修复前 `put` 的"目标不存在 ⇒ 写并发就撞车"窗口里，一部分写入者会抛
    `PermissionError [WinError 5]`（`os.replace`）或 `InvalidDigestError`（路径守卫在并发下用
    `Path.resolve()` 误判越界）。两者在接收路径上都会冒泡成失败——而"两个人同时交相同内容"本该是
    一次成功的去重。
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = ContentAddressedStore(artifact_root, engine)
    workers = 12
    barrier = Barrier(workers)
    content = b"identical bytes from every writer\n"

    def writer() -> str:
        barrier.wait(timeout=30)
        return store.put(content).content_sha256

    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = [future.result(timeout=60) for future in [pool.submit(writer) for _ in range(workers)]]

    assert len(set(digests)) == 1, f"同一内容必须得到同一摘要：{set(digests)}"
    with engine.connect() as connection:
        rows = int(connection.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one())
    assert rows == 1, f"同内容只允许一行元数据，实际 {rows}"

    content_files = [path for path in artifact_root.rglob("*") if path.is_file()]
    assert len(content_files) == 1, f"同内容只允许一个内容文件，实际 {[str(p) for p in content_files]}"
    assert content_files[0].read_bytes() == content


def test_derived_path_guard_is_lexical_and_does_not_depend_on_the_filesystem(
    tmp_path: Path,
) -> None:
    """路径守卫必须**纯词法**：根目录尚不存在时也要给出同一结论。

    修复前它用 `Path.resolve()` 做包含判定，而 `resolve()` 的结果会随磁盘状态（目录是否刚被建出来、
    `\\?\\` 前缀是否被剥掉）变化——那是并发下的误判来源。这里用"根目录不存在"这一确定性差异把
    "不依赖文件系统"钉住：两种目录状态必须给出同一个答案。
    """
    existing = tmp_path / "artifacts"
    existing.mkdir()
    missing = tmp_path / "not-created-yet"

    digest = "b" * 64
    from_existing = absolute_path_under(existing, digest)
    from_missing = absolute_path_under(missing, digest)

    assert from_existing.name == from_missing.name == digest
    # 派生形态是 `ab/cd/<摘要>`（三段），所以往上三层就是各自的根目录。
    assert from_existing.parents[2] == existing
    assert from_missing.parents[2] == missing
    assert str(from_existing).startswith(str(existing))
