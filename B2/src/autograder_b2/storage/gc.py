"""孤儿内容回收：**只**回收无引用的内容，且每次操作都有审计记录。

ADR-0003 把本路径列为高危（判定错误即误删证据），因此四条硬约束：

1. **判定主体是引用关系，不是时间**：一条内容是孤儿 ⟺ 没有任何 `submission_files` 引用它。
   时间只以"保留窗口"的形式出现，而且**只用于推迟**回收（见第 2 条）：绝不会因为"它旧了"而删除
   一份仍被引用的内容，也绝不会因为"它新"而保留一份超出窗口的无引用内容。
2. **保留窗口可配置**（`B2_ARTIFACT_RETENTION_DAYS`，天，默认 `0`＝一失去引用即可回收）：
   引用消失后还要再保留这么久，内容才进入候选集。默认值 `0` 与历史行为逐字节相同；调大窗口只会让
   回收更保守——这个配置**不可能**让任何此前被保留的内容开始被删除。窗口内的内容**连候选集都不进**：
   它既不是"被跳过的孤儿"、也不写审计（审计记录的是"清理操作"，而这里根本没有操作）。由此有一条
   必须如实知道的观测后果：`scanned_artifacts` / `scanned_files` 统计的是**本轮够条件的候选**，
   所以 `0` 不等于"库里没有孤儿"。回收结果的两处枚举（`artifact_gc_audits.outcome` 的 CHECK 与
   `artifact_gc_runs` 的计数器）都不为本配置新增取值：本模块**不写迁移**（`module.yaml: migration_head`
   是与 MAIN 对账的共享状态）。
3. **判定与删除在同一语句内原子完成**：`DELETE ... WHERE created_at <= 截止点 AND NOT EXISTS (引用)`，
   并用 `RETURNING` 确认真的删掉了一行——从根上消除 TOCTOU 窗口。数据库外键是最后一道锁：任何并发
   产生的引用都会把删除挡下来，此时记为 `skipped_referenced` 而不是硬删。
4. **失败不静默**：删不掉的、认不出的，都写进审计（`file_removal_failed` / `skipped_unrecognized`）。

两相回收（对应 ADR-0003 第 3/4 条的两种孤儿形态）：

- **相一｜有元数据、无引用**：`artifacts` 有行但无 `submission_files` 引用 → 先删元数据行（受外键
  保护）再删内容文件。先删元数据是有意的：即使内容文件残留，也只是相二能收拾的散落文件，
  不会出现"元数据指向不存在的内容"这种坏状态。年龄看 `artifacts.created_at`。
- **相二｜有内容、无元数据**：内容文件已落盘但元数据写入失败（"先内容后元数据"的中间态）→
  只有在**文件名与文件内容摘要一致**（确认是本存储写出的内容）且**当前无元数据行**时才删；
  否则记为 `skipped_unrecognized`，只审计不删除。这一相没有元数据行可看，年龄只能看内容文件自己的
  mtime（＝这份内容落盘的时刻）。

**已知竞态（如实记录，不假装不存在）**：相二在"确认无元数据行"与"删除文件"之间，理论上可能被
并发的 `put()` 插入。本项目部署形态是单进程插件（MAIN ADR-0001）且动态评测并发为 1，窗口极小；
即便发生，后果也是**显式可见**的——`ContentAddressedStore.read()` 会抛
`ArtifactContentMissingError`（绝不返回空内容），且同内容再次提交时 `put()` 会重新落盘自愈。
"""

from __future__ import annotations

import pathlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from autograder_b2.db.schema import (
    artifact_gc_audits,
    artifact_gc_runs,
    artifacts,
    submission_files,
)
from autograder_b2.domain.clock import is_digest, sha256_hex, utc_now
from autograder_b2.storage.cas import ContentAddressedStore
from autograder_b2.storage.paths import absolute_path_under

DEFAULT_BATCH = 100

OUTCOME_REMOVED = "removed"
OUTCOME_SKIPPED_REFERENCED = "skipped_referenced"
OUTCOME_SKIPPED_UNRECOGNIZED = "skipped_unrecognized"
OUTCOME_FILE_REMOVAL_FAILED = "file_removal_failed"


@dataclass(frozen=True, slots=True)
class GcReport:
    """一次回收运行的汇总（与 `artifact_gc_runs` 行一一对应）。"""

    gc_run_id: str
    scanned_artifacts: int
    removed_artifacts: int
    skipped_referenced: int
    scanned_files: int
    removed_stray_files: int
    skipped_unrecognized: int
    file_removal_failures: int
    bytes_reclaimed: int


def referenced_artifact_ids(engine: Engine) -> set[str]:
    """当前被任何提交文件引用的 artifact_id（判断口径只有这一处，便于将来扩展引用来源）。"""
    with engine.connect() as connection:
        rows = connection.execute(sa.select(submission_files.c.artifact_id).distinct()).fetchall()
    return {str(row[0]) for row in rows}


def collect_orphans(
    store: ContentAddressedStore,
    *,
    batch: int = DEFAULT_BATCH,
    trigger: str = "manual",
    now: datetime | None = None,
    retention_days: int | None = None,
) -> GcReport:
    """回收一批孤儿内容（两相），返回汇总并写入审计记录。

    `retention_days=None` ⇒ 用运行时配置（`B2_ARTIFACT_RETENTION_DAYS`，默认 `0`）。显式传值只用于
    调用方需要指定窗口的场合（测试、按运维需要临时放宽）；解析规则与默认值仍只有 `config` 一处定义。
    """
    engine = store.engine
    root: pathlib.Path = store.root
    started = utc_now() if now is None else now
    if retention_days is None:
        # 函数内导入：顶层导入会成环——`config` 经 `integration` 包（`config` → `integration.bindings`
        # 会执行 `integration/__init__` → `submission_loader` → `storage/__init__` → 本模块）绕回这里，
        # 于是"先导入 `autograder_b2.intake`"的进程会在本模块撞上**部分初始化**的 `config`（实测：
        # `AttributeError`/`ImportError` 只在导入顺序不同时出现，正是最容易被漏掉的那类）。回收器每轮
        # 只调用一次，这一句的导入开销可以忽略。
        from autograder_b2.config import artifact_retention_days

        retention_days = artifact_retention_days()
    if retention_days < 0:
        raise ValueError("保留窗口不得为负（天）")
    # 截止点：**早于它**创建的内容才够条件被回收。窗口为 0 时它等于本轮开始时刻——"刚写入但已无引用"
    # 的内容照样在候选内，因为判定主体始终是引用关系；窗口>0 时它把回收整整推迟那么多天。
    cutoff = started - timedelta(days=retention_days)
    run_id = str(uuid.uuid4())
    _open_run(engine, run_id, trigger, started)

    counters = {
        "scanned_artifacts": 0,
        "removed_artifacts": 0,
        "skipped_referenced": 0,
        "scanned_files": 0,
        "removed_stray_files": 0,
        "skipped_unrecognized": 0,
        "file_removal_failures": 0,
        "bytes_reclaimed": 0,
    }
    handled = _collect_unreferenced_artifacts(
        engine, root, run_id, started, batch, counters, cutoff
    )
    _collect_stray_files(
        engine, root, run_id, started, counters, already_handled=handled, cutoff=cutoff
    )

    finished = utc_now() if now is None else now
    with engine.begin() as connection:
        connection.execute(
            sa.update(artifact_gc_runs)
            .where(artifact_gc_runs.c.gc_run_id == run_id)
            .values(finished_at=finished, **counters)
        )
    return GcReport(gc_run_id=run_id, **counters)


# --- 相一：有元数据、无引用 ---------------------------------------------------------------


def _collect_unreferenced_artifacts(
    engine: Engine,
    root: pathlib.Path,
    run_id: str,
    occurred_at: datetime,
    batch: int,
    counters: dict[str, int],
    cutoff: datetime,
) -> set[str]:
    """相一；返回本相已处理过的摘要集合，供相二去重（同一次运行内一个对象只记一条审计）。"""
    with engine.connect() as connection:
        candidates = connection.execute(
            sa.select(
                artifacts.c.artifact_id,
                artifacts.c.content_sha256,
                artifacts.c.size_bytes,
                artifacts.c.storage_path,
            )
            .where(~sa.exists().where(submission_files.c.artifact_id == artifacts.c.artifact_id))
            .where(artifacts.c.created_at <= cutoff)
            .order_by(artifacts.c.created_at)
            .limit(batch)
        ).fetchall()
    counters["scanned_artifacts"] = len(candidates)
    handled: set[str] = set()

    for artifact_id, digest, size_bytes, storage_path in candidates:
        digest = str(digest)
        handled.add(digest)
        outcome, detail = _remove_artifact_row(engine, str(artifact_id), run_id, cutoff)
        # 审计先写：否则下面把结果改成 file_removal_failed 时无行可改，删除失败会被静默吞掉。
        _record_audit(
            engine,
            run_id=run_id,
            digest=digest,
            storage_path=str(storage_path),
            size_bytes=int(size_bytes),
            outcome=outcome,
            detail=detail,
            occurred_at=occurred_at,
        )
        if outcome == OUTCOME_REMOVED:
            counters["removed_artifacts"] += 1
            counters["bytes_reclaimed"] += int(size_bytes)
            if not _unlink(root, digest):
                counters["file_removal_failures"] += 1
                _mark_file_removal_failed(engine, run_id, digest)
        elif outcome == OUTCOME_SKIPPED_REFERENCED:
            counters["skipped_referenced"] += 1
    return handled


def _remove_artifact_row(
    engine: Engine, artifact_id: str, run_id: str, cutoff: datetime
) -> tuple[str, str | None]:
    """原子地"判定 + 删除"一条无引用且已过保留期的 artifact；外键再兜一层。

    年龄条件与引用条件写在**同一条** DELETE 里：判定与删除之间没有窗口，因此不会出现"按旧状态判定、
    按新状态删除"。
    """
    try:
        with engine.begin() as connection:
            deleted = connection.execute(
                sa.delete(artifacts)
                .where(
                    artifacts.c.artifact_id == artifact_id,
                    artifacts.c.created_at <= cutoff,
                    ~sa.exists().where(submission_files.c.artifact_id == artifacts.c.artifact_id),
                )
                .returning(artifacts.c.artifact_id)
            ).fetchone()
    except IntegrityError:
        return OUTCOME_SKIPPED_REFERENCED, "foreign key blocked deletion (concurrent reference)"
    if deleted is None:
        detail = (
            "not eligible at deletion time: referenced, or created after the retention"
            f" cutoff (run {run_id})"
        )
        return OUTCOME_SKIPPED_REFERENCED, detail
    return OUTCOME_REMOVED, None


# --- 相二：有内容、无元数据 ---------------------------------------------------------------


def _collect_stray_files(
    engine: Engine,
    root: pathlib.Path,
    run_id: str,
    occurred_at: datetime,
    counters: dict[str, int],
    *,
    already_handled: set[str],
    cutoff: datetime,
) -> None:
    """相二：回收"有内容、无元数据"的散落文件。

    四点判定纪律：**有元数据的文件不是孤儿**（那是健康条目，安静跳过、不计入任何结果）；
    **窗口内的内容不是候选人**（还没到回收期，与相一同一条规则）；**本相一已处理过的摘要不再重复
    记账**（同一次运行内一个对象只留一条审计）；**认不出的文件只记不删**。
    """
    files = [path for path in sorted(root.rglob("*")) if path.is_file()]
    counters["scanned_files"] = len(files)
    for path in files:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest = sha256_hex(content)
        size_bytes = len(content)
        if digest in already_handled:
            continue
        if not (is_digest(path.name) and path.name == digest):
            counters["skipped_unrecognized"] += 1
            _record_audit(
                engine,
                run_id=run_id,
                digest=digest,
                storage_path=relative,
                size_bytes=size_bytes,
                outcome=OUTCOME_SKIPPED_UNRECOGNIZED,
                detail="file is not a verifiable content-addressed entry",
                occurred_at=occurred_at,
            )
            continue
        if _metadata_exists(engine, digest):
            continue  # 健康条目（内容 + 元数据都在），不是孤儿
        if _within_retention(path, cutoff):
            # 还没到回收期：既不删也不审计（审计记录的是清理操作，这里没有操作）。
            continue
        _record_audit(
            engine,
            run_id=run_id,
            digest=digest,
            storage_path=relative,
            size_bytes=size_bytes,
            outcome=OUTCOME_REMOVED,
            detail="stray content without metadata (metadata write failed)",
            occurred_at=occurred_at,
        )
        try:
            path.unlink()
        except OSError:
            counters["file_removal_failures"] += 1
            _mark_file_removal_failed(engine, run_id, digest, expected_path=relative)
            continue
        counters["removed_stray_files"] += 1
        counters["bytes_reclaimed"] += size_bytes


def _metadata_exists(engine: Engine, digest: str) -> bool:
    with engine.connect() as connection:
        row = connection.execute(
            sa.select(artifacts.c.artifact_id).where(artifacts.c.content_sha256 == digest)
        ).fetchone()
    return row is not None


def _within_retention(path: pathlib.Path, cutoff: datetime) -> bool:
    """相二的内容文件是否仍在保留窗口内（这一相没有元数据行，年龄只能看文件自己的 mtime）。

    mtime 就是这份内容落盘的时刻。判定只会**推迟**删除：宿主时钟异常让 mtime 偏晚时，结果是保留更久，
    而不是提前删除——单向的保守方向是刻意的。

    **必须与 `utc_now()` 同精度（毫秒截断）后再比**：文件时间戳是微秒精度，而本轮开始时刻被
    `domain.clock.utc_now()` 截断到毫秒，于是"刚写入的内容"的 mtime 常常比 `cutoff` 晚几百微秒
    （本机实测 6 次里 5 次）。直接比较会把默认窗口 `0` 悄悄变成"永不回收散落文件"——默认行为必须与
    历史逐字节相同，所以这里比较的是同一条毫秒网格上的时刻。
    """
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    truncated = mtime.replace(microsecond=(mtime.microsecond // 1000) * 1000)
    return truncated > cutoff


# --- 共用小工具 ---------------------------------------------------------------------------


def _open_run(engine: Engine, run_id: str, trigger: str, started: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.insert(artifact_gc_runs).values(
                gc_run_id=run_id,
                trigger=trigger,
                started_at=started,
                finished_at=None,
                scanned_artifacts=0,
                removed_artifacts=0,
                skipped_referenced=0,
                scanned_files=0,
                removed_stray_files=0,
                skipped_unrecognized=0,
                file_removal_failures=0,
                bytes_reclaimed=0,
            )
        )


def _unlink(root: pathlib.Path, digest: str) -> bool:
    """删除内容文件；失败返回 False（不吞掉，由审计记录暴露）。"""
    try:
        absolute_path_under(root, digest).unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _record_audit(
    engine: Engine,
    *,
    run_id: str,
    digest: str,
    storage_path: str,
    size_bytes: int,
    outcome: str,
    detail: str | None,
    occurred_at: datetime,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.insert(artifact_gc_audits).values(
                gc_audit_id=uuid.uuid4(),
                gc_run_id=run_id,
                content_sha256=digest,
                storage_path=storage_path,
                size_bytes=size_bytes,
                outcome=outcome,
                detail=detail,
                occurred_at=occurred_at,
            )
        )


def _mark_file_removal_failed(
    engine: Engine, run_id: str, digest: str, *, expected_path: str | None = None
) -> None:
    """内容文件删除失败：把该条审计从 removed 改成 file_removal_failed（失败不得静默）。"""
    conditions = [
        artifact_gc_audits.c.gc_run_id == run_id,
        artifact_gc_audits.c.content_sha256 == digest,
        artifact_gc_audits.c.outcome == OUTCOME_REMOVED,
    ]
    if expected_path is not None:
        conditions.append(artifact_gc_audits.c.storage_path == expected_path)
    with engine.begin() as connection:
        connection.execute(
            sa.update(artifact_gc_audits)
            .where(*conditions)
            .values(
                outcome=OUTCOME_FILE_REMOVAL_FAILED,
                detail="content file could not be removed",
            )
        )
