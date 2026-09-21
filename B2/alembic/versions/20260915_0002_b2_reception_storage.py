"""b2 接收与存储业务表（WP-B2-A：submissions/artifacts/submission_files/email_checkpoints/intake_quarantines）。

DDL 意图（本文件是 DDL 权威，`docs/database/schema.md` 只写意图）：

- **内容寻址**：`artifacts.content_sha256` 是**去重键**（`uq_artifacts_content_sha256`）——同一内容只存一行，
  存储路径由服务端按摘要派生（`storage_path` 唯一且由 CHECK 保证非绝对路径、无 `..` 段）。
  主键按 B4 `table-standards.md` 第 2 节取 `<entity>_id` UUID（`artifact_id`）：ADR-0003 的
  "内容主键/去重键"由唯一约束承担，去重语义不变而主键形态符合规范（UUID 主键可被自动化检查校验）。
- **无重复事实**：`submission_files` 只存 `(submission_id, path, artifact_id)`——字节数与摘要的唯一来源
  是 `artifacts`，同一事实不两处存放（"禁止双写"的落地形态）。
- **幂等**：`submissions` 以 `(idempotency_scope, idempotency_key)` 唯一约束保证同一幂等键
  只产生一次提交；`idempotency_scope` 由服务端派生（标识与契约中的 `(idempotency_key, scope)` 口径一致）。
- **状态机**：`submissions.state` 用 `text + CHECK` 受控枚举，取值集合由 MAIN 锁定，不可在本表外漂移。
- **路径安全**：`submission_files.path` 的 CHECK 把 interfaces.md I2 白名单（≤200 字符、段数 ≤8、
  无 `.`/`..` 段、非绝对路径、段字符受限）下沉到数据库层，作为纵深防御的第二道。
- **邮件接收**：`email_checkpoints` 以 `(mailbox, uidvalidity, uid)` 与 `(mailbox, message_id)`
  双键保证重复拉取幂等；`intake_quarantines` 记录无法唯一匹配/被拒的来件（脱敏原因）。
- 命名遵守 B4 `table-standards.md`（复数表名、`<entity>_id` 主键、`ix_`/`uq_`/`fk_` 前缀、
  `timestamptz` UTC）；**仅 schema 内 FK**，跨模块引用只传 UUID 不建外键。
- 时间列由应用写入 UTC 毫秒值，不设 server default（保持可重放时的显式性）。

可回滚：`downgrade()` 按依赖逆序删除本迁移创建的五张表（schema 由基线迁移持有）。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0002"
down_revision = "20260915_0001"
branch_labels = None
depends_on = None

SCHEMA = "b2"

_DIGEST_CHECK = "^[0-9a-f]{64}$"
_PATH_CHECK = (
    "char_length(path) <= 200"
    " AND path ~ '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$'"
    " AND path !~ '(^|/)\\.\\.?(/|$)'"
    " AND array_length(string_to_array(path, '/'), 1) <= 8"
)
_STATES = "'received','validated','queued','running','completed','rejected','failed'"


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint(f"content_sha256 ~ '{_DIGEST_CHECK}'", name="ck_artifacts_content_sha256"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_bytes"),
        sa.CheckConstraint(
            "storage_path <> '' AND storage_path !~ '^/' AND storage_path !~ '(^|/)\\.\\.?(/|$)'",
            name="ck_artifacts_storage_path",
        ),
        sa.UniqueConstraint("content_sha256", name="uq_artifacts_content_sha256"),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_artifacts"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_artifacts_storage_path", "artifacts", ["storage_path"], unique=True, schema=SCHEMA
    )
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"], schema=SCHEMA)

    op.create_table(
        "submissions",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("intake", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("idempotency_scope", sa.Text(), nullable=False),
        sa.Column("submission_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("updated_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("intake IN ('api','email')", name="ck_submissions_intake"),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_submissions_state"),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 200", name="ck_submissions_idempotency_key"
        ),
        sa.CheckConstraint(f"submission_digest ~ '{_DIGEST_CHECK}'", name="ck_submissions_submission_digest"),
        sa.UniqueConstraint(
            "idempotency_scope", "idempotency_key", name="uq_submissions_idempotency_scope_idempotency_key"
        ),
        sa.PrimaryKeyConstraint("submission_id", name="pk_submissions"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_submissions_student_id_created_at", "submissions", ["student_id", "created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_submissions_course_id_section_id", "submissions", ["course_id", "section_id"], schema=SCHEMA
    )
    op.create_index("ix_submissions_question_version_id", "submissions", ["question_version_id"], schema=SCHEMA)
    op.create_index("ix_submissions_state", "submissions", ["state"], schema=SCHEMA)

    op.create_table(
        "submission_files",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint(_PATH_CHECK, name="ck_submission_files_path"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_submission_files_submission_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            [f"{SCHEMA}.artifacts.artifact_id"],
            name="fk_submission_files_artifact_id",
        ),
        sa.PrimaryKeyConstraint("submission_id", "path", name="pk_submission_files"),
        schema=SCHEMA,
    )
    op.create_index("ix_submission_files_artifact_id", "submission_files", ["artifact_id"], schema=SCHEMA)

    op.create_table(
        "email_checkpoints",
        sa.Column("email_checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("mailbox", sa.Text(), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("processed_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("status IN ('processed','quarantined','rejected')", name="ck_email_checkpoints_status"),
        sa.CheckConstraint(
            "(status = 'processed' AND submission_id IS NOT NULL AND reason_code IS NULL)"
            " OR (status IN ('quarantined','rejected') AND submission_id IS NULL AND reason_code IS NOT NULL)",
            name="ck_email_checkpoints_outcome",
        ),
        sa.CheckConstraint("uidvalidity >= 0 AND uid >= 0", name="ck_email_checkpoints_uid_range"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_email_checkpoints_submission_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("mailbox", "uidvalidity", "uid", name="uq_email_checkpoints_mailbox_uidvalidity_uid"),
        sa.PrimaryKeyConstraint("email_checkpoint_id", name="pk_email_checkpoints"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_email_checkpoints_mailbox_message_id",
        "email_checkpoints",
        ["mailbox", "message_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("message_id IS NOT NULL"),
    )

    op.create_table(
        "intake_quarantines",
        sa.Column("intake_quarantine_id", sa.Uuid(), nullable=False),
        sa.Column("intake", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("mailbox", sa.Text(), nullable=True),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=True),
        sa.Column("uid", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("intake IN ('api','email')", name="ck_intake_quarantines_intake"),
        sa.CheckConstraint(
            "char_length(detail) BETWEEN 1 AND 1000", name="ck_intake_quarantines_detail"
        ),
        sa.PrimaryKeyConstraint("intake_quarantine_id", name="pk_intake_quarantines"),
        schema=SCHEMA,
    )
    op.create_index("ix_intake_quarantines_created_at", "intake_quarantines", ["created_at"], schema=SCHEMA)

    for table, comment in (
        ("artifacts", "内容寻址存储条目（SHA-256 去重，路径由服务端派生）；孤儿回收只删无引用内容"),
        ("submissions", "一次代码提交的元数据（归属/题目绑定/状态/幂等键）；跨模块引用只存 UUID 不建 FK"),
        ("submission_files", "提交文件清单（路径 → 内容摘要）；path 的 CHECK 即路径白名单第二道防线"),
        ("email_checkpoints", "IMAP 收取检查点（UID 与 Message-ID 双键幂等）；不存邮件正文"),
        ("intake_quarantines", "隔离队列：无法唯一匹配或被拒的来件（原因脱敏，不含正文/附件原文）"),
    ):
        op.execute(f"COMMENT ON TABLE {SCHEMA}.{table} IS '{comment}'")


def downgrade() -> None:
    op.drop_index("ix_intake_quarantines_created_at", table_name="intake_quarantines", schema=SCHEMA)
    op.drop_table("intake_quarantines", schema=SCHEMA)

    op.drop_index("uq_email_checkpoints_mailbox_message_id", table_name="email_checkpoints", schema=SCHEMA)
    op.drop_table("email_checkpoints", schema=SCHEMA)

    op.drop_index("ix_submission_files_artifact_id", table_name="submission_files", schema=SCHEMA)
    op.drop_table("submission_files", schema=SCHEMA)

    op.drop_index("ix_submissions_state", table_name="submissions", schema=SCHEMA)
    op.drop_index("ix_submissions_question_version_id", table_name="submissions", schema=SCHEMA)
    op.drop_index("ix_submissions_course_id_section_id", table_name="submissions", schema=SCHEMA)
    op.drop_index("ix_submissions_student_id_created_at", table_name="submissions", schema=SCHEMA)
    op.drop_table("submissions", schema=SCHEMA)

    op.drop_index("ix_artifacts_created_at", table_name="artifacts", schema=SCHEMA)
    op.drop_index("uq_artifacts_storage_path", table_name="artifacts", schema=SCHEMA)
    op.drop_table("artifacts", schema=SCHEMA)
