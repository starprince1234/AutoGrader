"""多题邮件提交：核验码/邮件事实按**作业**绑定，一邮件多题映射（CHG-0032 / 规格 §6.2）。

## 为什么需要它

作业模型下"一封邮件携带 N 个附件 → 产生 N 行提交"：核验码不再绑定单个题目版本，而绑定作业
（`assignment_id`）；一封邮件的事实（发件人、Message-ID、mailbox/UID）仍然只有一条，但它变成的
提交有多条、每道题一条。因此：

1. `b2.email_verification_challenges`：增加 `assignment_id`，`question_version_id` 改为可空
   （历史行保留原值；新行只填 assignment_id）。`ck_..._target` 保证至少有一个目标；
   `ck_..._rejection` 的取值域增加作业正文/附件映射三类原因码。
2. `b2.email_verifications`：增加 `assignment_id` 与 `processed_at`（"这封邮件是否已经完整变成
   全部题目"，与 `received_at` 分开：收件是事实，处理完成是进度），`question_version_id` 改为可空
   （一邮件多题时题目在映射表里；历史行保留原值）。
3. 新表 `b2.email_verification_submissions`：一封邮件 × 一道题 → 一份提交的映射
   （`attachment_name` 记录该题对应的附件名，`<n>.txt`）。
4. `b2.email_checkpoints` 的 `ck_email_checkpoints_outcome` 放宽：`processed` 的
   `submission_id` 允许为 NULL（一邮件多提交时 checkpoint 不指向单份提交；它的事实由
   `email_verification_submissions` 表达）。

## 历史数据

- 所有新增列都**可空且不回填**：本迁移之前收到的邮件是"一邮件一题"形态，它们的
  `question_version_id` / `submission_id` 是真实事实，原样保留；
- `question_version_id` 放开 NOT NULL 不改变既有行的值。

## 回滚（**dev-only 有损**，在此明确写出）

`downgrade()` 要恢复两个 `question_version_id NOT NULL` 与旧 outcome CHECK，而新形态的行在这些约束下
不可能存在（它们的题目在映射表、checkpoint 不指向单份提交）。因此回滚会**删除**新形态的行：

- `email_verification_submissions` 整表删除；
- `email_verifications` 中 `assignment_id IS NOT NULL OR question_version_id IS NULL` 的行；
- `email_verification_challenges` 中同类行（删挑战会级联删除它的 verification 行）；
- `email_checkpoints` 中 `status='processed' AND submission_id IS NULL` 的行（旧 CHECK 不允许）。

这是"回到旧结构"的代价：被删行属于作业模型下的新形态数据，旧结构没有地方表达它们。回滚是开发/演练
操作，生产不做（正常发布保留业务数据，见根 AGENTS.md）。**历史行（旧形态）不受影响**。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

from alembic import op

revision = "20261107_0022"
down_revision = "20261107_0021"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TS = TIMESTAMP(timezone=True, precision=3)
NOW = "date_trunc('milliseconds', now())"

CHALLENGES = "email_verification_challenges"
VERIFICATIONS = "email_verifications"
VERIFICATION_SUBMISSIONS = "email_verification_submissions"
CHECKPOINTS = "email_checkpoints"

_OLD_CHECKPOINT_OUTCOME = (
    "(status = 'processed' AND submission_id IS NOT NULL AND reason_code IS NULL)"
    " OR (status IN ('quarantined','rejected') AND submission_id IS NULL AND reason_code IS NOT NULL)"
)
_NEW_CHECKPOINT_OUTCOME = (
    "(status = 'processed' AND reason_code IS NULL)"
    " OR (status IN ('quarantined','rejected') AND submission_id IS NULL AND reason_code IS NOT NULL)"
)
_REJECTION_CODES = (
    "email_verification_rejected",
    "email_sender_mismatch",
    "email_verification_expired",
    "email_body_missing_field",
    "email_body_mismatch",
    "attachment_mapping_invalid",
)
_OLD_REJECTION_CODES = (
    "email_verification_rejected",
    "email_sender_mismatch",
    "email_verification_expired",
)


def _rejection_check(codes: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{code}'" for code in codes)
    return f"last_rejection_code IS NULL OR last_rejection_code IN ({quoted})"


def upgrade() -> None:
    # --- 核验码：目标从"题目"变为"作业"，旧目标列放开可空 ---------------------------------
    op.add_column(
        CHALLENGES, sa.Column("assignment_id", sa.Uuid(), nullable=True), schema=SCHEMA
    )
    op.alter_column(
        CHALLENGES, "question_version_id", existing_type=sa.Uuid(), nullable=True, schema=SCHEMA
    )
    op.create_check_constraint(
        "ck_email_verification_challenges_target",
        CHALLENGES,
        "assignment_id IS NOT NULL OR question_version_id IS NOT NULL",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_email_verification_challenges_rejection", CHALLENGES, type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        "ck_email_verification_challenges_rejection",
        CHALLENGES,
        _rejection_check(_REJECTION_CODES),
        schema=SCHEMA,
    )

    # --- 邮件事实：作业归属 + 处理完成时刻；题目列放开可空 --------------------------------
    op.add_column(
        VERIFICATIONS, sa.Column("assignment_id", sa.Uuid(), nullable=True), schema=SCHEMA
    )
    op.add_column(VERIFICATIONS, sa.Column("processed_at", TS, nullable=True), schema=SCHEMA)
    op.alter_column(
        VERIFICATIONS, "question_version_id", existing_type=sa.Uuid(), nullable=True, schema=SCHEMA
    )

    # --- 一邮件 × 一题 → 一份提交的映射 ---------------------------------------------------
    op.create_table(
        VERIFICATION_SUBMISSIONS,
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_name", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text(NOW)),
        sa.PrimaryKeyConstraint(
            "verification_id",
            "question_version_id",
            name="pk_email_verification_submissions",
        ),
        sa.UniqueConstraint(
            "submission_id", name="uq_email_verification_submissions_submission_id"
        ),
        sa.CheckConstraint(
            "char_length(attachment_name) BETWEEN 1 AND 200",
            name="ck_email_verification_submissions_attachment_name",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_verification_submissions_question_version_id",
        VERIFICATION_SUBMISSIONS,
        ["question_version_id"],
        schema=SCHEMA,
    )

    # --- checkpoint：processed 时不再要求指向单份提交 -------------------------------------
    op.drop_constraint("ck_email_checkpoints_outcome", CHECKPOINTS, type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_email_checkpoints_outcome", CHECKPOINTS, _NEW_CHECKPOINT_OUTCOME, schema=SCHEMA
    )

    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{VERIFICATION_SUBMISSIONS} IS "
        "'一封真实邮件 × 一道题 → 一份提交的映射（attachment_name 是该题的 <n>.txt）'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{VERIFICATIONS}.processed_at IS "
        "'这封邮件的全部题目是否都已变成提交（收件是 received_at，处理完成是 processed_at）'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{CHALLENGES}.assignment_id IS "
        "'核验码绑定的作业（CHG-0032）；历史行保留 question_version_id、本列为 NULL'"
    )


def downgrade() -> None:
    """回到"一邮件一题"结构；新形态数据无法在旧结构表达，故**有损**删除（见模块文档）。"""
    op.drop_table(VERIFICATION_SUBMISSIONS, schema=SCHEMA)

    # 新形态的邮件事实与模式在旧结构里无处安放：先删（挑战删除会级联删除其 verification 行）。
    op.execute(
        f"DELETE FROM {SCHEMA}.{CHALLENGES}"
        " WHERE assignment_id IS NOT NULL OR question_version_id IS NULL"
    )
    op.execute(
        f"DELETE FROM {SCHEMA}.{VERIFICATIONS}"
        " WHERE assignment_id IS NOT NULL OR question_version_id IS NULL"
    )

    op.drop_constraint("ck_email_checkpoints_outcome", CHECKPOINTS, type_="check", schema=SCHEMA)
    op.execute(
        f"DELETE FROM {SCHEMA}.{CHECKPOINTS}"
        " WHERE status = 'processed' AND submission_id IS NULL"
    )
    op.create_check_constraint(
        "ck_email_checkpoints_outcome", CHECKPOINTS, _OLD_CHECKPOINT_OUTCOME, schema=SCHEMA
    )

    op.drop_column(VERIFICATIONS, "processed_at", schema=SCHEMA)
    op.drop_column(VERIFICATIONS, "assignment_id", schema=SCHEMA)
    op.alter_column(
        VERIFICATIONS, "question_version_id", existing_type=sa.Uuid(), nullable=False, schema=SCHEMA
    )

    op.drop_constraint(
        "ck_email_verification_challenges_rejection", CHALLENGES, type_="check", schema=SCHEMA
    )
    # 新原因码在旧结构里没有对应取值：dev-only 有损回滚把它们清回 NULL（约束允许 NULL），
    # 否则在已产生新拒绝码的库上重建旧 CHECK 会因存量行失败。生产不执行 downgrade。
    new_codes = tuple(code for code in _REJECTION_CODES if code not in _OLD_REJECTION_CODES)
    quoted_new = ", ".join(f"'{code}'" for code in new_codes)
    op.execute(
        f"UPDATE {SCHEMA}.{CHALLENGES} SET last_rejection_code = NULL"
        f" WHERE last_rejection_code IN ({quoted_new})"
    )
    op.create_check_constraint(
        "ck_email_verification_challenges_rejection",
        CHALLENGES,
        _rejection_check(_OLD_REJECTION_CODES),
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_email_verification_challenges_target", CHALLENGES, type_="check", schema=SCHEMA
    )
    op.drop_column(CHALLENGES, "assignment_id", schema=SCHEMA)
    op.alter_column(
        CHALLENGES, "question_version_id", existing_type=sa.Uuid(), nullable=False, schema=SCHEMA
    )
