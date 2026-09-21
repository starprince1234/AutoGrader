"""孤儿内容回收测试（T-STORE-004）：只删无引用内容、写审计、被引用内容不删。

ADR-0003 把本路径列为高危，故测试重点在**误删防护**与**失败可见**：
- 被提交引用的内容在回收后必须仍然可读（含内容文件）；
- 元数据写入失败留下的散落内容（"先内容后元数据"的中间态）必须能被回收；
- 认不出的文件不删只记（不装作认识）；删除失败必须出现在审计里，不得静默；
- 判定的主体是**引用关系**；时间只以"保留窗口"（`B2_ARTIFACT_RETENTION_DAYS`，默认 0 天）的形式
  推迟回收——窗口内保留、窗口外回收，两个方向都要有断言，否则"忽略保留配置"不会被发现。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.db.schema import artifact_gc_audits, artifact_gc_runs, artifacts
from autograder_b2.domain.clock import utc_now
from autograder_b2.storage import ContentAddressedStore, collect_orphans, referenced_artifact_ids
from autograder_b2.storage.cas import ArtifactContentMissingError
from autograder_b2.storage.paths import absolute_path_under, storage_path_for
from tests.conftest import requires_postgres
from tests.test_storage_cas import SOURCE_A, SOURCE_B, _artifact_count, _insert_submission

pytestmark = requires_postgres


def _audits(engine: Engine, run_id: str) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(artifact_gc_audits.c.content_sha256, artifact_gc_audits.c.outcome)
            .where(artifact_gc_audits.c.gc_run_id == run_id)
            .order_by(artifact_gc_audits.c.content_sha256)
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def _run_row(engine: Engine, run_id: str) -> object:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(artifact_gc_runs).where(artifact_gc_runs.c.gc_run_id == run_id)
        ).one()


class _MetadataFailingEngine:
    """故障注入：内容能落盘，但元数据写入必失败（模拟 ADR-0003 的"先内容后元数据"中间态）。"""

    def __init__(self, real: Engine) -> None:
        self._real = real

    def begin(self) -> object:
        raise sa.exc.OperationalError("simulated metadata write failure", None, Exception("boom"))

    def connect(self) -> object:
        return self._real.connect()


# --- 相一：有元数据、无引用 ---------------------------------------------------------------


def test_referenced_content_is_never_collected(engine: Engine, artifact_root: Path) -> None:
    """核心防误删：被引用的内容在回收后必须仍然存在且可读。"""
    store = ContentAddressedStore(artifact_root, engine)
    submission_id = _insert_submission(engine)
    stored = store.put_submission_files(submission_id, [("main.py", SOURCE_A)])
    digest = stored[0].content_sha256

    report = collect_orphans(store)

    assert report.scanned_artifacts == 0, "被引用的内容不应进入候选集"
    assert report.removed_artifacts == 0
    assert _artifact_count(engine) == 1
    assert store.read(digest) == SOURCE_A, "被引用内容必须仍可读"
    assert absolute_path_under(artifact_root, digest).is_file(), "被引用内容文件不得被删除"
    row = _run_row(engine, report.gc_run_id)
    assert (row.scanned_artifacts, row.removed_artifacts) == (0, 0)


def test_orphan_content_is_collected_with_audit(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    orphan = store.put(SOURCE_B)
    assert _artifact_count(engine) == 1

    report = collect_orphans(store)

    assert report.scanned_artifacts == 1
    assert report.removed_artifacts == 1
    assert report.file_removal_failures == 0
    assert _artifact_count(engine) == 0
    assert not absolute_path_under(artifact_root, orphan.content_sha256).exists()
    assert _audits(engine, report.gc_run_id) == [(orphan.content_sha256, "removed")]
    assert report.scanned_files == 0, "相一已删掉内容文件，相二不应再看到它"


def test_mixed_set_only_orphans_are_removed(engine: Engine, artifact_root: Path) -> None:
    """同一次回收里：有引用的留着、无引用的删掉——一次运行同时证明两边。"""
    store = ContentAddressedStore(artifact_root, engine)
    submission_id = _insert_submission(engine)
    referenced = store.put_submission_files(submission_id, [("main.py", SOURCE_A)])
    orphan = store.put(SOURCE_B)

    report = collect_orphans(store)

    assert (report.scanned_artifacts, report.removed_artifacts) == (1, 1)
    assert _artifact_count(engine) == 1
    assert store.read(referenced[0].content_sha256) == SOURCE_A
    assert _audits(engine, report.gc_run_id) == [(orphan.content_sha256, "removed")]


def test_collection_is_reference_based_not_time_based(engine: Engine, artifact_root: Path) -> None:
    """默认保留窗口是 `0` 天：引用一消失就可回收——"刚写入"不构成豁免，判定主体是引用关系。"""
    store = ContentAddressedStore(artifact_root, engine)
    fresh_orphan = store.put(b"fresh orphan content")
    report = collect_orphans(store)
    assert report.removed_artifacts == 1
    assert _audits(engine, report.gc_run_id) == [(fresh_orphan.content_sha256, "removed")]


# --- 保留策略可配置（RQ-B-2-30 验收 5） ---------------------------------------------------


def _backdate_artifact(engine: Engine, digest: str, *, days: int) -> None:
    """把一条 artifact 行的 `created_at` 往前挪（模拟"引用消失已经很久"）。"""
    with engine.begin() as connection:
        connection.execute(
            sa.update(artifacts)
            .where(artifacts.c.content_sha256 == digest)
            .values(created_at=utc_now() - timedelta(days=days))
        )


def _backdate_file(path: pathlib.Path, *, days: int) -> None:
    """把内容文件的 mtime 往前挪（相二没有元数据行，年龄只看文件自己的 mtime）。"""
    moment = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (moment, moment))


def test_configured_retention_window_defers_reclamation(
    engine: Engine, artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`B2_ARTIFACT_RETENTION_DAYS` 真的被回收器读到：窗口内的无引用内容既不回收也不写审计。

    删学生作业不可逆，因此这条测试**两个方向都要成立**：窗口内必须保留（本用例），过了窗口必须
    照常回收（`test_content_older_than_the_window_is_reclaimed`）。忽略保留配置的实现会让本用例失败。
    """
    monkeypatch.setenv("B2_ARTIFACT_RETENTION_DAYS", "30")
    store = ContentAddressedStore(artifact_root, engine)
    submission_id = _insert_submission(engine)
    referenced = store.put_submission_files(submission_id, [("main.py", SOURCE_A)])
    orphan = store.put(SOURCE_B)

    report = collect_orphans(store)

    assert report.scanned_artifacts == 0, "窗口内的内容连候选集都不该进"
    assert report.removed_artifacts == 0
    assert report.removed_stray_files == 0
    assert _audits(engine, report.gc_run_id) == [], "没有发生清理操作就不该有审计行"
    assert _artifact_count(engine) == 2
    assert store.read(orphan.content_sha256) == SOURCE_B, "窗口内的孤儿必须仍可读"
    # 被引用的内容与保留窗口无关：任何窗口下都不是候选人。
    assert store.read(referenced[0].content_sha256) == SOURCE_A
    assert absolute_path_under(artifact_root, orphan.content_sha256).is_file()


def test_content_older_than_the_window_is_reclaimed(engine: Engine, artifact_root: Path) -> None:
    """窗口之外的无引用内容照常回收——保留窗口不是"永不回收"的开关。"""
    store = ContentAddressedStore(artifact_root, engine)
    orphan = store.put(SOURCE_B)
    _backdate_artifact(engine, orphan.content_sha256, days=40)

    report = collect_orphans(store, retention_days=30)

    assert (report.scanned_artifacts, report.removed_artifacts) == (1, 1)
    assert report.file_removal_failures == 0
    assert _artifact_count(engine) == 0
    assert not absolute_path_under(artifact_root, orphan.content_sha256).exists()
    assert _audits(engine, report.gc_run_id) == [(orphan.content_sha256, "removed")]


def test_retention_window_also_defers_stray_file_removal(
    engine: Engine, artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相二（有内容、无元数据）同样受保留窗口约束，过期后照样被回收并留审计。"""
    monkeypatch.setenv("B2_ARTIFACT_RETENTION_DAYS", "30")
    store = ContentAddressedStore(artifact_root, engine)
    digest = hashlib.sha256(SOURCE_A).hexdigest()
    target = absolute_path_under(artifact_root, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SOURCE_A)

    fresh = collect_orphans(store)
    assert (fresh.scanned_files, fresh.removed_stray_files) == (1, 0)
    assert _audits(engine, fresh.gc_run_id) == []
    assert target.is_file()

    _backdate_file(target, days=40)
    aged = collect_orphans(store)
    assert (aged.scanned_files, aged.removed_stray_files) == (1, 1)
    assert not target.exists()
    assert _audits(engine, aged.gc_run_id) == [(digest, "removed")]


def test_batch_limit_bounds_one_run(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    for index in range(5):
        store.put(f"orphan-{index}".encode())
    first = collect_orphans(store, batch=2)
    assert (first.scanned_artifacts, first.removed_artifacts) == (2, 2)
    second = collect_orphans(store, batch=10)
    assert (second.scanned_artifacts, second.removed_artifacts) == (3, 3)
    assert referenced_artifact_ids(engine) == set()
    assert _artifact_count(engine) == 0


def test_gc_run_row_records_counts_and_finish_time(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    orphan = store.put(SOURCE_B)
    report = collect_orphans(store, trigger="scheduled")
    row = _run_row(engine, report.gc_run_id)
    assert row.trigger == "scheduled"
    assert row.finished_at is not None
    assert (row.scanned_artifacts, row.removed_artifacts, row.bytes_reclaimed) == (1, 1, len(SOURCE_B))
    assert orphan.content_sha256 not in referenced_artifact_ids(engine)


def test_audit_survives_artifact_row_deletion(engine: Engine, artifact_root: Path) -> None:
    """审计刻意不建 artifacts 外键：被审计的事实就是那行已被删除。"""
    store = ContentAddressedStore(artifact_root, engine)
    orphan = store.put(SOURCE_B)
    report = collect_orphans(store)
    with engine.connect() as connection:
        audited = connection.execute(
            sa.select(sa.func.count())
            .select_from(artifact_gc_audits)
            .where(artifact_gc_audits.c.gc_run_id == report.gc_run_id)
        ).scalar_one()
        left = connection.execute(
            sa.select(sa.func.count())
            .select_from(artifacts)
            .where(artifacts.c.content_sha256 == orphan.content_sha256)
        ).scalar_one()
    assert int(audited) == 1 and int(left) == 0


# --- 相二：有内容、无元数据（ADR-0003 "先内容后元数据"的中间态） ---------------------------


def test_metadata_write_failure_leaves_recoverable_orphan_content(
    engine: Engine, artifact_root: Path
) -> None:
    """故障注入：元数据写入失败 → 内容仍在盘上（孤儿），且绝不存在"有元数据无内容"。"""
    failing = ContentAddressedStore(artifact_root, _MetadataFailingEngine(engine))  # type: ignore[arg-type]
    with pytest.raises(sa.exc.OperationalError):
        failing.put(SOURCE_A)

    digest = hashlib.sha256(SOURCE_A).hexdigest()
    assert absolute_path_under(artifact_root, digest).is_file(), "内容必须先落盘（先内容后元数据）"
    assert _artifact_count(engine) == 0, "元数据写入失败不得留下 artifact 行"

    healthy = ContentAddressedStore(artifact_root, engine)
    report = collect_orphans(healthy)
    assert report.scanned_files == 1
    assert report.removed_stray_files == 1
    assert report.bytes_reclaimed == len(SOURCE_A)
    assert not absolute_path_under(artifact_root, digest).exists()
    assert _audits(engine, report.gc_run_id) == [(digest, "removed")]


def test_self_heal_after_content_loss(engine: Engine, artifact_root: Path) -> None:
    """内容丢失是显式错误（不是空内容），且同内容再次提交会重新落盘自愈。"""
    store = ContentAddressedStore(artifact_root, engine)
    stored = store.put(SOURCE_A)
    absolute_path_under(artifact_root, stored.content_sha256).unlink()
    with pytest.raises(ArtifactContentMissingError):
        store.read(stored.content_sha256)
    republished = store.put(SOURCE_A)
    assert republished.content_sha256 == stored.content_sha256
    assert store.read(stored.content_sha256) == SOURCE_A


def test_unrecognized_file_is_audited_but_not_deleted(engine: Engine, artifact_root: Path) -> None:
    """认不出的文件不删只记——不装作认识它，也不静默忽略。"""
    store = ContentAddressedStore(artifact_root, engine)
    foreign_dir = artifact_root / "ab" / "cd"
    foreign_dir.mkdir(parents=True)
    stranger = foreign_dir / "not-a-digest.txt"
    stranger.write_bytes(b"foreign content")
    leftover = artifact_root / ".tmp-leftover"
    leftover.write_bytes(b"half written temp")

    report = collect_orphans(store)

    assert report.scanned_files == 2
    assert report.removed_stray_files == 0
    assert report.skipped_unrecognized == 2
    assert stranger.is_file() and leftover.is_file(), "认不出的文件必须原样保留"
    assert {outcome for _, outcome in _audits(engine, report.gc_run_id)} == {"skipped_unrecognized"}


def test_stray_file_path_is_digest_derived(engine: Engine, artifact_root: Path) -> None:
    """散落文件的存储路径同样由摘要派生（可反查、可审计到具体文件）。"""
    store = ContentAddressedStore(artifact_root, engine)
    digest = hashlib.sha256(SOURCE_A).hexdigest()
    target = absolute_path_under(artifact_root, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SOURCE_A)
    report = collect_orphans(store)
    with engine.connect() as connection:
        path = connection.execute(
            sa.select(artifact_gc_audits.c.storage_path).where(
                artifact_gc_audits.c.gc_run_id == report.gc_run_id
            )
        ).scalar_one()
    assert path == storage_path_for(digest)


def test_file_removal_failure_is_recorded_not_swallowed(
    engine: Engine, artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内容文件删不掉时必须留下 `file_removal_failed` 审计（失败可见）。"""
    store = ContentAddressedStore(artifact_root, engine)
    orphan = store.put(SOURCE_B)

    def boom(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr("autograder_b2.storage.gc._unlink", boom)
    report = collect_orphans(store)

    assert report.removed_artifacts == 1
    assert report.file_removal_failures == 1
    assert _audits(engine, report.gc_run_id) == [(orphan.content_sha256, "file_removal_failed")]


def test_gc_run_id_is_unique_per_run(engine: Engine, artifact_root: Path) -> None:
    store = ContentAddressedStore(artifact_root, engine)
    first = collect_orphans(store)
    second = collect_orphans(store)
    assert first.gc_run_id != second.gc_run_id
    assert uuid.UUID(first.gc_run_id)
