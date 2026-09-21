"""迁移在**真实 PostgreSQL** 上的验收（T-DB-*，对应 G-DB-03/04/05/06）。

不使用 SQLite（MAIN `docs/database/ownership.md` 第 7 节明令 SQLite 结果不得用于数据库验收）。
测试在目标 PostgreSQL 上**临时建一个专用数据库**、在其上跑真实的 `alembic upgrade/downgrade` CLI、
用 `information_schema` 断言表与约束真的存在，最后删除该临时库——绝不触碰既有数据。

运行前置（真实实例，二选一提供）：
    $env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:***@localhost:55433/autograder"
未提供时本模块 skip，并给出明确原因（不伪报通过）。
"""

from __future__ import annotations

import importlib.util
import pathlib
import uuid
from collections.abc import Iterator
from types import ModuleType

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import ADMIN_URL, requires_postgres, run_alembic

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_migration_module(filename: str) -> ModuleType:
    """按**文件路径**加载 `alembic/versions/` 下的一个迁移模块（只取它声明的常量）。

    为什么不能写 `from alembic.versions import …`：`alembic/` 在本仓库是 Alembic 的**脚本目录**，
    不是 Python 包（没有 `__init__.py`），而 site-packages 里的 `alembic` 是一个**常规包**——
    按 PEP 420，命名空间目录只是"找不到常规包时的兜底"，常规包优先。实测：
    `alembic.__file__` 指向 `.venv/Lib/site-packages/alembic/__init__.py`，
    `find_spec("alembic.versions")` 为 `None` ⇒ 报 `ModuleNotFoundError`。

    反过来"给 `B2/alembic/` 补一个 `__init__.py`"会更糟：那会让 `import alembic` 在测试进程里解析到
    本仓库的脚本目录、把真正的 Alembic **遮蔽**掉（迁移文件里的 `from alembic import op` 立刻失效），
    等于把一个测试缺陷升级成产品缺陷。因此这里按路径加载，不做任何 `sys.path`/包的改动。
    """
    path = B2_ROOT / "alembic" / "versions" / filename
    assert path.is_file(), f"迁移文件不存在：{path.name}"
    spec = importlib.util.spec_from_file_location(f"_b2_tests_{path.stem}", path)
    assert spec is not None and spec.loader is not None, f"无法为 {path.name} 构造加载器"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

BUSINESS_TABLES = {
    "artifact_gc_audits",
    "artifact_gc_runs",
    "artifacts",
    "case_evidence",
    "email_checkpoints",
    "evaluation_commentaries",
    "evaluation_results",
    "evaluation_run_state_transitions",
    "evaluation_runs",
    "inbox_events",
    "intake_quarantines",
    "outbox_events",
    "progress_events",
    "security_findings",
    "static_findings",
    "submission_state_transitions",
    "submission_files",
    "submissions",
}

#: 复合主键的表（列名与顺序都要合规范，故单独断言）。
COMPOSITE_PRIMARY_KEYS = {
    "submission_files": ["submission_id", "path"],
    "inbox_events": ["consumer", "event_id"],
}

pytestmark = requires_postgres


def _alembic(url: str, *args: str):
    """以固定参数数组调用 alembic CLI（共享脚手架，无 shell 拼接）。"""
    return run_alembic(url, *args)


@pytest.fixture(scope="module")
def empty_database_url(fresh_database_url: str) -> Iterator[str]:
    """复用 `tests/conftest.py` 的临时库夹具（同一套引导逻辑，避免两处漂移）。"""
    yield fresh_database_url


def _table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'b2' AND table_type = 'BASE TABLE'"
                )
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _constraint_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints"
                    " WHERE constraint_schema = 'b2'"
                )
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _index_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'b2'")
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _cross_schema_foreign_keys(url: str) -> list[tuple[str, str]]:
    """返回引用了 `b2` 之外 schema 的外键（必须为空：禁止跨 schema FK）。"""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT tc.constraint_name, ccu.table_schema"
                    " FROM information_schema.table_constraints tc"
                    " JOIN information_schema.constraint_column_usage ccu"
                    "   ON ccu.constraint_name = tc.constraint_name"
                    "  AND ccu.constraint_schema = tc.constraint_schema"
                    " WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.constraint_schema = 'b2'"
                )
            )
            return [(row[0], row[1]) for row in rows if row[1] != "b2"]
    finally:
        engine.dispose()


# --- 验收 1：空库 upgrade head，表与约束经 information_schema 断言存在 ----------------------


def test_upgrade_head_succeeds_on_truly_empty_database(empty_database_url: str) -> None:
    """G-DB-04：空库可从零迁移到 head。此前基线在空库上必失败（InvalidSchemaName）。"""
    result = _alembic(empty_database_url, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head 失败: {result.stderr[-2000:]}"
    assert BUSINESS_TABLES <= _table_names(empty_database_url), "业务表未全部创建"
    assert "alembic_version_b2" in _table_names(empty_database_url), "版本表缺失"


def test_created_constraints_exist(empty_database_url: str) -> None:
    _alembic(empty_database_url, "upgrade", "head")
    constraints = _constraint_names(empty_database_url)
    for expected in (
        "ck_artifacts_content_sha256",
        "ck_artifacts_size_bytes",
        "ck_artifacts_storage_path",
        "uq_artifacts_content_sha256",
        "ck_submissions_intake",
        "ck_submissions_state",
        "ck_submissions_idempotency_key",
        "ck_submissions_submission_digest",
        "uq_submissions_idempotency_scope_idempotency_key",
        "ck_submission_files_path",
        "fk_submission_files_submission_id",
        "fk_submission_files_artifact_id",
        "ck_email_checkpoints_status",
        "ck_email_checkpoints_outcome",
        "uq_email_checkpoints_mailbox_uidvalidity_uid",
        "fk_email_checkpoints_submission_id",
        "ck_intake_quarantines_intake",
        "ck_intake_quarantines_detail",
        "ck_artifact_gc_runs_trigger",
        "ck_artifact_gc_runs_counts_non_negative",
        "ck_artifact_gc_runs_artifact_counts_within_scanned",
        "ck_artifact_gc_runs_file_counts_within_scanned",
        "ck_artifact_gc_runs_file_failures_within_removed",
        "ck_artifact_gc_audits_content_sha256",
        "ck_artifact_gc_audits_size_bytes",
        "ck_artifact_gc_audits_outcome",
        "ck_artifact_gc_audits_detail",
        "fk_artifact_gc_audits_gc_run_id",
        "ck_submission_state_transitions_sequence_positive",
        "ck_submission_state_transitions_from_state",
        "ck_submission_state_transitions_to_state",
        "ck_submission_state_transitions_distinct_states",
        "ck_submission_state_transitions_reason_length",
        "uq_submission_state_transitions_submission_id_sequence",
        "fk_submission_state_transitions_submission_id",
    ):
        assert expected in constraints, f"约束缺失: {expected}"


def test_indexes_and_unique_keys_exist(empty_database_url: str) -> None:
    _alembic(empty_database_url, "upgrade", "head")
    indexes = _index_names(empty_database_url)
    for expected in (
        "uq_artifacts_storage_path",
        "ix_artifacts_created_at",
        "ix_submissions_student_id_created_at",
        "ix_submissions_course_id_section_id",
        "ix_submissions_question_version_id",
        "ix_submissions_state",
        "ix_submission_files_artifact_id",
        "uq_email_checkpoints_mailbox_message_id",
        "ix_intake_quarantines_created_at",
        "ix_artifact_gc_runs_started_at",
        "ix_artifact_gc_audits_gc_run_id",
        "ix_artifact_gc_audits_content_sha256",
        "ix_submission_state_transitions_submission_id_occurred_at",
    ):
        assert expected in indexes, f"索引/唯一键缺失: {expected}"


def test_no_cross_schema_foreign_keys(empty_database_url: str) -> None:
    """G-DB-05 的 B2 侧：本模块的表不得有跨 schema 外键。"""
    _alembic(empty_database_url, "upgrade", "head")
    assert _cross_schema_foreign_keys(empty_database_url) == []


def test_primary_keys_follow_b4_naming_standard(empty_database_url: str) -> None:
    """B4 `table-standards.md` 第 2 节：主键列必须是 `<实体单数>_id` 且为 uuid v4。"""
    _alembic(empty_database_url, "upgrade", "head")
    engine = create_engine(empty_database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT tc.table_name, kcu.column_name, c.data_type"
                    " FROM information_schema.table_constraints tc"
                    " JOIN information_schema.key_column_usage kcu"
                    "   ON kcu.constraint_name = tc.constraint_name"
                    "  AND kcu.constraint_schema = tc.constraint_schema"
                    " JOIN information_schema.columns c"
                    "   ON c.table_name = tc.table_name AND c.column_name = kcu.column_name"
                    "  AND c.table_schema = tc.constraint_schema"
                    " WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.constraint_schema = 'b2'"
                    "   AND tc.table_name <> 'alembic_version_b2'"
                    "   AND tc.table_name <> ALL(:composite_tables)"
                ),
                {"composite_tables": list(COMPOSITE_PRIMARY_KEYS)},
            )
            keys = {row[0]: (row[1], row[2]) for row in rows}
    finally:
        engine.dispose()
    expected = {
        "artifact_gc_audits": ("gc_audit_id", "uuid"),
        "artifact_gc_runs": ("gc_run_id", "uuid"),
        "artifacts": ("artifact_id", "uuid"),
        "case_evidence": ("case_evidence_id", "uuid"),
        "evaluation_commentaries": ("evaluation_commentary_id", "uuid"),
        "evaluation_results": ("evaluation_run_id", "uuid"),
        "evaluation_run_state_transitions": ("evaluation_run_state_transition_id", "uuid"),
        "evaluation_runs": ("evaluation_run_id", "uuid"),
        "outbox_events": ("event_id", "uuid"),
        "progress_events": ("progress_event_id", "uuid"),
        "security_findings": ("security_finding_id", "uuid"),
        "static_findings": ("static_finding_id", "uuid"),
        "submission_state_transitions": ("submission_state_transition_id", "uuid"),
        "submissions": ("submission_id", "uuid"),
        "email_checkpoints": ("email_checkpoint_id", "uuid"),
        "intake_quarantines": ("intake_quarantine_id", "uuid"),
    }
    assert keys == expected, f"主键形态不符合 B4 规范: {keys}"
    # 复合主键表：列名与顺序也要合规
    for table_name, wanted in COMPOSITE_PRIMARY_KEYS.items():
        engine = create_engine(empty_database_url)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT kcu.column_name"
                        " FROM information_schema.table_constraints tc"
                        " JOIN information_schema.key_column_usage kcu"
                        "   ON kcu.constraint_name = tc.constraint_name"
                        "  AND kcu.constraint_schema = tc.constraint_schema"
                        " WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.constraint_schema = 'b2'"
                        "   AND tc.table_name = :table_name"
                        " ORDER BY kcu.ordinal_position"
                    ),
                    {"table_name": table_name},
                )
                composite = [row[0] for row in rows]
        finally:
            engine.dispose()
        assert composite == wanted, f"{table_name} 复合主键不符合规范: {composite}"


def test_timestamps_are_timestamptz(empty_database_url: str) -> None:
    """B4 规范：时间列为 `timestamptz`。"""
    _alembic(empty_database_url, "upgrade", "head")
    engine = create_engine(empty_database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns"
                    " WHERE table_schema = 'b2' AND (column_name LIKE '%\\_at' OR column_name = 'processed_at')"
                )
            )
            found = {(row[0], row[1]): row[2] for row in rows}
    finally:
        engine.dispose()
    assert found, "未找到时间列"
    wrong = {key: value for key, value in found.items() if value != "timestamp with time zone"}
    assert wrong == {}, f"存在非 timestamptz 时间列: {wrong}"


# --- 验收 2：downgrade 可回滚且不残留业务表 -----------------------------------------------


def test_downgrade_to_baseline_leaves_no_business_tables(empty_database_url: str) -> None:
    """G-DB-06：回滚到基线修订后，本迁移创建的业务表不残留。

    回滚目标取基线修订 `20260915_0001`（而非 `base`）：`downgrade base` 会执行基线自己的
    `DROP SCHEMA IF EXISTS b2`，而 Alembic 版本表就在该 schema 内，必然失败——这是**基线迁移
    的既有缺陷**，不属于本迁移，且本小类明令不修改已发布基线。该缺陷由
    `test_downgrade_base_is_blocked_by_known_baseline_defect` 以 strict-xfail 机器留痕。
    """
    _alembic(empty_database_url, "upgrade", "head")
    assert BUSINESS_TABLES <= _table_names(empty_database_url)
    result = _alembic(empty_database_url, "downgrade", "20260915_0001")
    assert result.returncode == 0, f"downgrade 到基线失败: {result.stderr[-2000:]}"
    remaining = _table_names(empty_database_url)
    assert remaining & BUSINESS_TABLES == set(), f"回滚后仍残留业务表: {sorted(remaining)}"
    assert remaining == {"alembic_version_b2"}, f"回滚后应只剩版本表，实际: {sorted(remaining)}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知基线缺陷：20260915_0001 的 downgrade 执行 DROP SCHEMA IF EXISTS b2（无 CASCADE），"
        "而 b2.alembic_version_b2 依赖该 schema，故 downgrade base 必然失败 "
        "(DependentObjectsStillExist)。修法二选一：给基线加 CASCADE（改写已发布历史，需决策）"
        "或在后续迁移中处理。修好后本 xfail 会失败，提示改为普通断言。"
    ),
)
def test_downgrade_base_is_blocked_by_known_baseline_defect(empty_database_url: str) -> None:
    _alembic(empty_database_url, "upgrade", "head")
    result = _alembic(empty_database_url, "downgrade", "base")
    assert result.returncode == 0, f"downgrade base 失败: {result.stderr[-500:]}"


def test_round_trip_upgrade_downgrade_upgrade(empty_database_url: str) -> None:
    """来回迁移必须可重复（迁移幂等性由 Alembic 版本表保证）。"""
    baseline = "20260915_0001"
    assert _alembic(empty_database_url, "downgrade", baseline).returncode == 0
    assert _alembic(empty_database_url, "upgrade", "head").returncode == 0
    assert _alembic(empty_database_url, "downgrade", baseline).returncode == 0
    assert _alembic(empty_database_url, "upgrade", "head").returncode == 0
    assert BUSINESS_TABLES <= _table_names(empty_database_url)


# --- 约束"真的拦得住"（不是只声明存在）-----------------------------------------------------


def _exec(url: str, sql: str, params: dict[str, object] | None = None) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _insert_artifact(url: str, artifact_id: str, digest: str = "a" * 64, path: str = "ab/cd/" + "a" * 64) -> None:
    _exec(
        url,
        "INSERT INTO b2.artifacts (artifact_id, content_sha256, size_bytes, storage_path, created_at)"
        " VALUES (:aid, :digest, 3, :path, now())",
        {"aid": artifact_id, "digest": digest, "path": path},
    )


def _insert_submission(
    url: str,
    submission_id: str,
    key: str | None = None,
    scope: str | None = None,
    state: str = "received",
) -> None:
    """插入一条合法提交；key/scope 缺省自动取唯一值（同一测试库被多个用例共用）。

    列清单含迁移 0008 新增的 `correlation_id` 与 `request_id`：`intake='api'` 的行必须
    "有 request_id、无邮件身份"（`ck_submissions_provenance_shape`），否则插进去的就不是
    一份与冻结信封一致的行，而测试会以 NotNullViolation/CHECK 失败——那是夹具的问题，不是约束的问题。
    """
    _exec(
        url,
        "INSERT INTO b2.submissions (submission_id, student_id, course_id, section_id,"
        " question_version_id, intake, idempotency_key, idempotency_scope, submission_digest,"
        " state, created_at, updated_at, correlation_id, request_id)"
        " VALUES (:sid, gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),"
        " 'api', :key, :scope, :digest, :state, now(), now(), gen_random_uuid(), :request_id)",
        {
            "sid": submission_id,
            "key": key if key is not None else f"key-{uuid.uuid4().hex}",
            "scope": scope if scope is not None else f"scope-{uuid.uuid4().hex}",
            "digest": "b" * 64,
            "state": state,
            "request_id": f"req-{uuid.uuid4().hex}",
        },
    )


def test_path_check_constraint_actually_rejects_traversal(empty_database_url: str) -> None:
    """契约模式放行 `..`（见 interfaces.md I2 实测），故必须证明 DB 层的白名单真的拦得住。"""
    _alembic(empty_database_url, "upgrade", "head")
    submission_id, artifact_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_submission(empty_database_url, submission_id)
    _insert_artifact(empty_database_url, artifact_id)

    def insert_file(path: str) -> None:
        _exec(
            empty_database_url,
            "INSERT INTO b2.submission_files (submission_id, path, artifact_id, created_at)"
            " VALUES (:sid, :path, :aid, now())",
            {"sid": submission_id, "path": path, "aid": artifact_id},
        )

    insert_file("main.py")  # 合法路径必须能插入（否则约束过严）
    for bad in ("../main.py", "a/../../b.py", "/etc/passwd", "a//b.py", "a\\b.py", "a/" + "/".join("b" * 1 for _ in range(9))):
        with pytest.raises(IntegrityError):
            insert_file(bad)
    nine_segments = "/".join(["d"] * 9 + ["main.py"])
    with pytest.raises(IntegrityError):
        insert_file(nine_segments)


def test_idempotency_unique_constraint_actually_enforced(empty_database_url: str) -> None:
    """同一 (作用域, 幂等键) 只能有一行——幂等是约束保证的，不是应用口头约定。"""
    _alembic(empty_database_url, "upgrade", "head")
    _insert_submission(empty_database_url, str(uuid.uuid4()), key="same", scope="scope-x")
    with pytest.raises(IntegrityError):
        _insert_submission(empty_database_url, str(uuid.uuid4()), key="same", scope="scope-x")
    # 不同作用域下同一幂等键互不影响
    _insert_submission(empty_database_url, str(uuid.uuid4()), key="same", scope="scope-y")


def test_state_and_intake_enums_actually_rejected(empty_database_url: str) -> None:
    _alembic(empty_database_url, "upgrade", "head")
    with pytest.raises(IntegrityError):
        _insert_submission(empty_database_url, str(uuid.uuid4()), state="nonsense")
    with pytest.raises(IntegrityError):
        _exec(
            empty_database_url,
            "INSERT INTO b2.submissions (submission_id, student_id, course_id, section_id,"
            " question_version_id, intake, idempotency_key, idempotency_scope, submission_digest,"
            " state, created_at, updated_at, correlation_id, request_id)"
            " VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),"
            " gen_random_uuid(), 'carrier_pigeon', 'k', 's', :digest, 'received', now(), now(),"
            " gen_random_uuid(), 'req-x')",
            {"digest": "b" * 64},
        )


def test_artifact_dedup_and_storage_path_guards(empty_database_url: str) -> None:
    """同一内容摘要只能有一行（去重）；存储路径不得绝对路径或含 `..`。"""
    _alembic(empty_database_url, "upgrade", "head")
    digest = "c" * 64
    _insert_artifact(empty_database_url, str(uuid.uuid4()), digest=digest, path="cc/dd/" + digest)
    with pytest.raises(IntegrityError):
        _insert_artifact(empty_database_url, str(uuid.uuid4()), digest=digest, path="cc/dd/other")
    with pytest.raises(IntegrityError):
        _insert_artifact(empty_database_url, str(uuid.uuid4()), digest="d" * 64, path="../escape")
    with pytest.raises(IntegrityError):
        _insert_artifact(empty_database_url, str(uuid.uuid4()), digest="e" * 64, path="/abs/path")
    with pytest.raises(IntegrityError):
        _insert_artifact(empty_database_url, str(uuid.uuid4()), digest="NOT-A-DIGEST", path="ff/gg/x")


def test_email_checkpoint_outcome_constraint_enforced(empty_database_url: str) -> None:
    """processed 必须带 submission_id；quarantined/rejected 必须带原因且不得带提交。"""
    _alembic(empty_database_url, "upgrade", "head")
    submission_id = str(uuid.uuid4())
    _insert_submission(empty_database_url, submission_id)

    def insert_checkpoint(status: str, submission: str | None, reason: str | None, uid: int) -> None:
        _exec(
            empty_database_url,
            "INSERT INTO b2.email_checkpoints (email_checkpoint_id, mailbox, uidvalidity, uid,"
            " message_id, status, reason_code, submission_id, processed_at)"
            " VALUES (gen_random_uuid(), :mailbox, 1, :uid, :mid, :status, :reason, :sid, now())",
            {
                "mailbox": f"inbox-{uuid.uuid4().hex}",
                "uid": uid,
                "mid": f"<m{uid}@example>",
                "status": status,
                "reason": reason,
                "sid": submission,
            },
        )

    insert_checkpoint("processed", submission_id, None, 1)
    insert_checkpoint("quarantined", None, "no_unique_match", 2)
    insert_checkpoint("rejected", None, "archive_bomb", 3)
    with pytest.raises(IntegrityError):
        insert_checkpoint("processed", None, None, 4)
    with pytest.raises(IntegrityError):
        insert_checkpoint("quarantined", submission_id, "no_unique_match", 5)
    with pytest.raises(IntegrityError):
        insert_checkpoint("rejected", None, None, 6)


def test_email_checkpoint_uid_and_message_id_uniqueness(empty_database_url: str) -> None:
    _alembic(empty_database_url, "upgrade", "head")
    submission_id = str(uuid.uuid4())
    _insert_submission(empty_database_url, submission_id)

    def insert(uid: int, mid: str | None, uidvalidity: int = 1) -> None:
        _exec(
            empty_database_url,
            "INSERT INTO b2.email_checkpoints (email_checkpoint_id, mailbox, uidvalidity, uid,"
            " message_id, status, reason_code, submission_id, processed_at)"
            " VALUES (gen_random_uuid(), 'inbox', :uv, :uid, :mid, 'processed', NULL, :sid, now())",
            {"uv": uidvalidity, "uid": uid, "mid": mid, "sid": submission_id},
        )

    insert(10, "<a@example>")
    with pytest.raises(IntegrityError):  # 同 UIDVALIDITY+UID 重复拉取
        insert(10, "<b@example>")
    with pytest.raises(IntegrityError):  # 同 Message-ID、不同 UID（重投递）
        insert(11, "<a@example>")
    with pytest.raises(IntegrityError):  # UIDVALIDITY 变化也挡：邮箱重建不得让同一封邮件被二次处理
        insert(10, "<a@example>", uidvalidity=2)
    insert(12, None)  # 无 Message-ID 时不受部分唯一索引约束
    insert(13, None)


# --- 验收 3：表标准（RQ-B-4-30 / 缺陷 E16）-------------------------------------------------


def test_primary_key_constraints_are_named_pk_table(empty_database_url: str) -> None:
    """每张表的主键约束必须叫 `pk_<table>`（Postgres 的隐式 `<table>_pkey` 不合标准）。

    这是 B4 的表标准检查器（`autograder_b4.ops.table_standards --scope migrations`）在 B2 侧的
    **持久回归闸**：检查器扫迁移文本，本用例扫**真实库**，两者判据同源、互为独立读数。
    """
    _alembic(empty_database_url, "upgrade", "head")
    engine = create_engine(empty_database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT rel.relname, con.conname FROM pg_constraint con"
                    " JOIN pg_class rel ON rel.oid = con.conrelid"
                    " JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace"
                    " WHERE nsp.nspname = 'b2' AND con.contype = 'p'"
                    "   AND rel.relname <> 'alembic_version_b2'"
                )
            ).fetchall()
    finally:
        engine.dispose()
    names = {row[0]: row[1] for row in rows}
    assert set(names) == BUSINESS_TABLES, "主键集合与业务表集合不一致"
    wrong = {table: name for table, name in names.items() if name != f"pk_{table}"}
    assert wrong == {}, f"主键约束命名不合标准: {wrong}"


def test_every_timestamp_column_has_millisecond_precision(empty_database_url: str) -> None:
    """**每一个**时间戳列都必须是 `timestamptz(3)`。

    判据与 B4 检查器当前的迁移扫描一致：它最初只约束 `created_at`/`updated_at`，后来对齐到
    catalog 扫描（后者一直检查每一个时间戳列）。本用例扫真实库，因此两个 scope 的判词不会分叉。
    """
    _alembic(empty_database_url, "upgrade", "head")
    engine = create_engine(empty_database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type, datetime_precision"
                    " FROM information_schema.columns"
                    " WHERE table_schema = 'b2'"
                    "   AND data_type LIKE 'timestamp%'"
                    "   AND table_name <> 'alembic_version_b2'"
                )
            ).fetchall()
    finally:
        engine.dispose()
    assert rows, "未找到任何时间戳列（本断言将失去意义）"
    wrong = {
        (row[0], row[1]): (row[2], row[3])
        for row in rows
        if row[2] != "timestamp with time zone" or row[3] != 3
    }
    assert wrong == {}, f"时间列精度不合标准: {wrong}"
    # 覆盖数与迁移 0012 的清单一致（两边都改了才不会只修一半）。
    remediation = _load_migration_module("20260915_0012_b2_table_standard_remediation.py")

    expected = {f"{table}.{column}" for table, column in remediation.MILLISECOND_COLUMNS}
    assert {f"{row[0]}.{row[1]}" for row in rows} == expected


def _standard_snapshot(url: str) -> tuple[dict[str, tuple[str, int]], dict[str, int]]:
    """表标准的**可对照读数**：`{表: (主键约束名, 约束 OID)}` + `{(表.列): 精度}`。

    带上 OID 是关键：只比名字无法区分"0012 什么都没做"与"0012 把约束删了又建了一遍"——
    后者会换一个新 OID，而前者不会。
    """
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            pk_rows = connection.execute(
                text(
                    "SELECT rel.relname, con.conname, con.oid FROM pg_constraint con"
                    " JOIN pg_class rel ON rel.oid = con.conrelid"
                    " JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace"
                    " WHERE nsp.nspname = 'b2' AND con.contype = 'p'"
                    "   AND rel.relname <> 'alembic_version_b2'"
                )
            ).fetchall()
            precision_rows = connection.execute(
                text(
                    "SELECT table_name || '.' || column_name, datetime_precision"
                    " FROM information_schema.columns"
                    " WHERE table_schema = 'b2' AND column_name IN ('created_at','updated_at')"
                )
            ).fetchall()
    finally:
        engine.dispose()
    return (
        {row[0]: (row[1], int(row[2])) for row in pk_rows},
        {row[0]: int(row[1]) for row in precision_rows},
    )


def test_fresh_database_needs_no_correction_and_the_revision_is_a_true_no_op() -> None:
    """**新库上的 no-op 证明**（不是"没报错"，是"什么都没改"）：

    1. 用**已整改过声明文本**的原始修订建到 `20260915_0011`（停在修正者之前）；
    2. 此刻库本身就应合规 ⇒ 证明文本编辑本身是对的（不是靠 0012 兜住的）；
    3. 再 `upgrade head`（跑 0012），断言主键名、**约束 OID** 与列精度逐项不变。

    第 3 步的 OID 比对排除了"删了又建"这种伪 no-op。
    """
    from tests.conftest import temporary_database

    with temporary_database() as url:
        result = _alembic(url, "upgrade", "20260915_0011")
        assert result.returncode == 0, result.stderr[-800:]
        before = _standard_snapshot(url)
        names, precisions = before
        assert set(names) == BUSINESS_TABLES, "表集合与业务表不一致"
        conformant = {table: name for table, (name, _oid) in names.items() if name == f"pk_{table}"}
        assert len(conformant) == len(names), f"新库上主键名仍不合标准: {names}"
        assert set(precisions.values()) == {3}, f"新库上时间列精度仍不合标准: {precisions}"

        result = _alembic(url, "upgrade", "head")
        assert result.returncode == 0, result.stderr[-800:]
        assert _standard_snapshot(url) == before, (
            "0012 在已经合规的新库上改动了结构（或重建了主键约束）：它应当是完全的 no-op"
        )


def test_standard_remediation_round_trip_is_reversible_and_idempotent(
    empty_database_url: str,
) -> None:
    """0012 的 `upgrade`/`downgrade` 都能重复执行，且来回一趟回到**旧结构**（不是旧数据）。

    "重复执行不失败"是幂等的机器含义：两个方向都先查后改（主键名现查、列精度现查），
    因此全新库、已整改库、被回滚过的库上都不会因为"已经是目标状态"而报错。

    **回滚目标写成修订号而不是 `-1`**（2026-09-20 更正）：`-1` 的含义是"相对当前 head 退一步"，
    因此"退两步就回到 0012 之前"只在 head 恰好是 0013 时成立——0014 一加进来，两次 `-1` 只到 0012，
    断言读到的就是**已整改**的结构（观察到 `pk_*` 而不是 `*_pkey`）。本用例要验证的是
    "0012 的 downgrade 把结构还原成它之前的样子"，与它上面还有几个修订无关，故显式指名 `0011`。
    """
    _alembic(empty_database_url, "upgrade", "head")
    for _ in range(2):  # 重复 upgrade：第二次必须是 no-op
        result = _alembic(empty_database_url, "upgrade", "head")
        assert result.returncode == 0, result.stderr[-800:]
    for _ in range(2):  # 重复回滚到 0011：第二次同样是 no-op（已经在那里了）
        result = _alembic(empty_database_url, "downgrade", "20260915_0011")
        assert result.returncode == 0, result.stderr[-800:]
    engine = create_engine(empty_database_url)
    try:
        with engine.connect() as connection:
            pk_names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT con.conname FROM pg_constraint con"
                        " JOIN pg_class rel ON rel.oid = con.conrelid"
                        " JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace"
                        " WHERE nsp.nspname = 'b2' AND con.contype = 'p'"
                        "   AND rel.relname <> 'alembic_version_b2'"
                    )
                ).fetchall()
            }
            precisions = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT datetime_precision FROM information_schema.columns"
                        " WHERE table_schema = 'b2'"
                        "   AND column_name IN ('created_at','updated_at')"
                    )
                ).fetchall()
            }
    finally:
        engine.dispose()
    assert all(name.endswith("_pkey") for name in pk_names), pk_names
    assert precisions == {6}, precisions  # 回到无显式精度的 timestamptz
    result = _alembic(empty_database_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr[-800:]


# --- 验收 4：alembic heads 与 module.yaml: database.migration_head 一致 -------------------


def test_single_head_matches_module_manifest() -> None:
    """G-DB-03：恰好一个 head，且与 `module.yaml: database.migration_head` 一致。"""
    result = _alembic(ADMIN_URL or "", "heads")
    assert result.returncode == 0, result.stderr[-1000:]
    heads = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    assert len(heads) == 1, f"应恰好一个 head，实际 {heads}"
    manifest = yaml.safe_load((B2_ROOT / "module.yaml").read_text(encoding="utf-8"))
    assert heads[0] == manifest["database"]["migration_head"]


def test_expected_schema_revision_matches_head_and_manifest() -> None:
    """三方一致：代码常量 `EXPECTED_SCHEMA_REVISION` == Alembic head == `module.yaml`。

    这个常量被 readiness 探测用来判断"库结构与代码是否匹配"（`intake/runtime.py::health`），
    一旦漂移，运维会看到"健康"而受权读请求全线 503——所以必须由测试钉住。
    """
    from autograder_b2.db.schema import EXPECTED_SCHEMA_REVISION

    result = _alembic(ADMIN_URL or "", "heads")
    heads = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    manifest = yaml.safe_load((B2_ROOT / "module.yaml").read_text(encoding="utf-8"))
    assert EXPECTED_SCHEMA_REVISION == heads[0] == manifest["database"]["migration_head"]


def test_history_is_linear_and_reachable_from_baseline() -> None:
    result = _alembic(ADMIN_URL or "", "history")
    assert result.returncode == 0, result.stderr[-1000:]
    assert "20260915_0002" in result.stdout
    assert "20260915_0001" in result.stdout


def test_offline_sql_mode_also_creates_schema_first() -> None:
    """离线模式（`--sql`）输出中，CREATE SCHEMA 必须出现在建版本表之前。"""
    result = _alembic(ADMIN_URL or "", "upgrade", "head", "--sql")
    assert result.returncode == 0, result.stderr[-1000:]
    sql = result.stdout
    assert "CREATE SCHEMA IF NOT EXISTS b2" in sql
    assert sql.index("CREATE SCHEMA IF NOT EXISTS b2") < sql.index("alembic_version_b2")
