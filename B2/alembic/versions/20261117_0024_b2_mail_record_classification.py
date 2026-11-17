"""教师端「邮件记录」三态：给隔离记录补四个分类事实列（契约 0.39.0）。

## 为什么需要这一版

教师端此前的"邮件提交记录"只有隔离区列表 + 人工处置状态（`resolved`/`held`/打回/作废/
归属），19 个原因码与处置状态混在一起，看不出"这封邮件到底变成作业了没有"。契约 0.39.0
把该视图明确成**三态**（`listMailRecords` / `MailRecordView`）：已处理 / 待处理 / 其他，
三态互斥且覆盖全部邮件。分类必须在**收件当时**判定——正文与附件原文**不落库**（平台有意
不做第二个邮件仓库），事后无法从内容重算——所以分类事实随隔离记录一起写下来：

- "已处理"不需要新列：读既有的 `b2.email_verifications`（核验码消费成功 = 邮件已被接受）；
- "待处理 / 其他"由本迁移的四个事实列 + 既有的 `reason_code` 判定（谓词见
  `intake.mail_records`）：`format_valid IS TRUE OR sender_associated IS TRUE`，历史行
  （两列皆 NULL）按原因码推断。

## 列（均可空；历史行 NULL）

| 列 | 语义 |
|---|---|
| `format_valid` | 该邮件被拒时，正文五行机器格式校验是否**已经通过**（作业编号与挑战一致；发件人恰为挑战所有者时姓名/学号与账号一致）。独立于消费结果计算，因此"核验码过期"不会抹掉"正文格式正确"这个事实 |
| `sender_associated` | 发件地址是否解析为 B4 已注册（active）账号。**地址可伪造**，它只能说明"这像某个学生发的邮件"，不是作者证明 |
| `assignment_id` | 能定位挑战时，挑战绑定的作业。仅信息展示，**绝不**作授权依据 |
| `student_id` | 能定位挑战时，挑战所有者（核验码已证明身份）。仅凭发件地址关联时**不写** |

`NULL` 不是"未知而已"的中间态，而是如实记录两种来源：这条记录写于分类列存在之前，
或这封邮件在解析之前就被拒绝（体积超限/报文畸形——既没有正文可校验，也没有发件地址可解析）。
读侧对「两列皆 NULL」的历史行按失败阶段可推断的原因码归类，其余落 `other`；契约把这条
历史归类规则写明了，消费方不应自己重算。

## 索引

`ix_email_verifications_course_section_received_at`（`(course_id, section_id, received_at)`）：
"已处理"页按课程（+课序）倒序 keyset 取页（`received_at DESC, verification_id DESC`）。
没有它，按 (course, section) 过滤会退化成全表扫。

## 回滚

纯加列 + 一个索引：`downgrade()` 删索引与四列。删列会丢掉分类事实（其中一部分可从原因码
重建），与"历史行没有分类"的语义一致；不回填、不改存量行。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261117_0024"
down_revision = "20261107_0023"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "intake_quarantines"
VERIFICATIONS = "email_verifications"
INDEX = "ix_email_verifications_course_section_received_at"


def upgrade() -> None:
    """追加四个可空分类事实列（历史行为 NULL，不回填）与"已处理"列表的取页索引。"""
    op.add_column(
        TABLE,
        sa.Column("format_valid", sa.Boolean(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("sender_associated", sa.Boolean(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("student_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    # 列顺序即 DDL 追加顺序：`db.schema.intake_quarantines` 的表映像必须逐列跟上
    # （`tests/test_storage_cas.py::test_core_metadata_matches_database` 按 ordinal_position 比对）。

    op.create_index(
        INDEX,
        VERIFICATIONS,
        ["course_id", "section_id", "received_at"],
        schema=SCHEMA,
    )

    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.format_valid IS "
        "'该邮件被拒时正文五行机器格式校验是否已通过；NULL=历史行或解析前即被拒'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.sender_associated IS "
        "'发件地址是否解析为已注册账号（可伪造：只说明像学生邮件，不是作者证明）；NULL=历史行或解析前即被拒'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.assignment_id IS "
        "'能定位挑战时挑战绑定的作业：仅信息展示，绝不作授权依据；NULL=历史行或未定位到挑战'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.student_id IS "
        "'能定位挑战时挑战所有者（核验码已证明身份）；仅凭发件地址关联时不写；NULL=历史行或未定位到挑战'"
    )


def downgrade() -> None:
    """删索引与四列（有损：分类事实不可在旧结构表达；生产不做 downgrade）。"""
    op.drop_index(INDEX, table_name=VERIFICATIONS, schema=SCHEMA)
    op.drop_column(TABLE, "student_id", schema=SCHEMA)
    op.drop_column(TABLE, "assignment_id", schema=SCHEMA)
    op.drop_column(TABLE, "sender_associated", schema=SCHEMA)
    op.drop_column(TABLE, "format_valid", schema=SCHEMA)
