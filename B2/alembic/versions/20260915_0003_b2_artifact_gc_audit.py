"""b2 内容寻址孤儿回收的审计表（ADR-0003：回收只删**无引用**内容且操作必须有审计记录）。

为什么单独建表：ADR-0003 把"孤儿回收"列为**高危清理路径**（判定错误即误删证据），因此要求
每次清理都有可追溯记录。审计记录**不得**与 `artifacts` 建外键——被审计的事实恰恰是那条
artifact 行已被删除，建 FK 会让审计随证据一起消失。

孤儿有两种形态（ADR-0003 第 3/4 条），因此回收分两相，汇总列也分两组：
- **有元数据无引用**：`artifacts` 有行，但没有任何 `submission_files` 引用 →
  `scanned_artifacts` / `removed_artifacts` / `skipped_referenced`；
- **有内容无元数据**：内容文件已落盘但元数据写入失败（"先内容后元数据"的中间态）→
  `scanned_files` / `removed_stray_files` / `skipped_unrecognized`。

结果枚举 `outcome`：`removed`（已删除）/`skipped_referenced`（判定或竞态中被引用而跳过）/
`skipped_unrecognized`（文件无法被识别为本存储写出的内容，不删只记）/`file_removal_failed`
（元数据已删但内容文件删除失败，需人工关注）。

可回滚：`downgrade()` 删除明细表再删汇总表。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0003"
down_revision = "20260915_0002"
branch_labels = None
depends_on = None

SCHEMA = "b2"
_DIGEST_CHECK = "^[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "artifact_gc_runs",
        sa.Column("gc_run_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("started_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("finished_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("scanned_artifacts", sa.Integer(), nullable=False),
        sa.Column("removed_artifacts", sa.Integer(), nullable=False),
        sa.Column("skipped_referenced", sa.Integer(), nullable=False),
        sa.Column("scanned_files", sa.Integer(), nullable=False),
        sa.Column("removed_stray_files", sa.Integer(), nullable=False),
        sa.Column("skipped_unrecognized", sa.Integer(), nullable=False),
        sa.Column("file_removal_failures", sa.Integer(), nullable=False),
        sa.Column("bytes_reclaimed", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("trigger IN ('manual','scheduled')", name="ck_artifact_gc_runs_trigger"),
        sa.CheckConstraint(
            "scanned_artifacts >= 0 AND removed_artifacts >= 0 AND skipped_referenced >= 0"
            " AND scanned_files >= 0 AND removed_stray_files >= 0 AND skipped_unrecognized >= 0"
            " AND file_removal_failures >= 0 AND bytes_reclaimed >= 0",
            name="ck_artifact_gc_runs_counts_non_negative",
        ),
        # 每相里每个被扫到的对象恰好归入一个结果，故两类之和不超过各自的扫描数。
        sa.CheckConstraint(
            "removed_artifacts + skipped_referenced <= scanned_artifacts",
            name="ck_artifact_gc_runs_artifact_counts_within_scanned",
        ),
        sa.CheckConstraint(
            "removed_stray_files + skipped_unrecognized <= scanned_files",
            name="ck_artifact_gc_runs_file_counts_within_scanned",
        ),
        # 文件删除失败是"已判定该删"的子集（元数据/散落文件都算），不能与删除数相加。
        sa.CheckConstraint(
            "file_removal_failures <= removed_artifacts + removed_stray_files",
            name="ck_artifact_gc_runs_file_failures_within_removed",
        ),
        sa.PrimaryKeyConstraint("gc_run_id", name="pk_artifact_gc_runs"),
        schema=SCHEMA,
    )
    op.create_index("ix_artifact_gc_runs_started_at", "artifact_gc_runs", ["started_at"], schema=SCHEMA)

    op.create_table(
        "artifact_gc_audits",
        sa.Column("gc_audit_id", sa.Uuid(), nullable=False),
        sa.Column("gc_run_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint(f"content_sha256 ~ '{_DIGEST_CHECK}'", name="ck_artifact_gc_audits_content_sha256"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifact_gc_audits_size_bytes"),
        sa.CheckConstraint(
            "outcome IN ('removed','skipped_referenced','skipped_unrecognized','file_removal_failed')",
            name="ck_artifact_gc_audits_outcome",
        ),
        sa.CheckConstraint(
            "detail IS NULL OR char_length(detail) <= 500", name="ck_artifact_gc_audits_detail"
        ),
        sa.ForeignKeyConstraint(
            ["gc_run_id"],
            [f"{SCHEMA}.artifact_gc_runs.gc_run_id"],
            name="fk_artifact_gc_audits_gc_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("gc_audit_id", name="pk_artifact_gc_audits"),
        schema=SCHEMA,
    )
    op.create_index("ix_artifact_gc_audits_gc_run_id", "artifact_gc_audits", ["gc_run_id"], schema=SCHEMA)
    op.create_index(
        "ix_artifact_gc_audits_content_sha256", "artifact_gc_audits", ["content_sha256"], schema=SCHEMA
    )

    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.artifact_gc_runs IS "
        "'一次孤儿回收运行的汇总（两相计数/起止/触发方式）；ADR-0003 要求清理可追溯'"
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.artifact_gc_audits IS "
        "'逐条回收明细；刻意不建 artifacts 外键——被审计的事实就是那行已被删除'"
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_gc_audits_content_sha256", table_name="artifact_gc_audits", schema=SCHEMA)
    op.drop_index("ix_artifact_gc_audits_gc_run_id", table_name="artifact_gc_audits", schema=SCHEMA)
    op.drop_table("artifact_gc_audits", schema=SCHEMA)
    op.drop_index("ix_artifact_gc_runs_started_at", table_name="artifact_gc_runs", schema=SCHEMA)
    op.drop_table("artifact_gc_runs", schema=SCHEMA)
