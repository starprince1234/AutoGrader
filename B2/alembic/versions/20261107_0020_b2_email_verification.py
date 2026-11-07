"""一次性邮件核验码：挑战、邮件事实、首次发件绑定与收件心跳（设计 §4）。

## 为什么是这四张表

`email_delivery` 题目不执行代码，评测只认"真实收到的邮件"。因此需要一条**可审计的所有权证明链**：

1. `email_verification_challenges`：已认证学生为本题目申请的一次性码。库里**只存 SHA-256**
   （`char(64)` 唯一），读库无法还原核验码；绑定 `(student_id, course_id, section_id,
   question_version_id)`，默认 30 分钟有效（`expires_at`，`CHECK expires_at > created_at`）。
   `consumed_at`/`consumed_verification_id` 成对出现（`ck_..._consumption`），把"消费"做成
   一个原子的、可断言的状态；`attempts`/`last_rejection_code` 记录被拒尝试，供状态查询区分
   "被拒"与"还没到"（契约 `reason_code` 里 `subject_mismatch`/`attachment_missing` 是保留值，
   不在本表写入；本表只写收件侧判定的三个原因）。
2. `email_verifications`：一次**真实 IMAP 拉取**产生的邮件事实（发件地址、主题、附件名、
   message-id、mailbox/uidvalidity/uid）与它变成的提交。`challenge_id` 唯一 ⇒ 一个挑战只能被
   消费一次；`submission_id` 唯一 ⇒ 一份提交只来自一封真实邮件；`(mailbox, uidvalidity, uid)`
   唯一 ⇒ 重复投递不能产生第二条记录。
3. `email_sender_bindings`：首次通过验证的地址绑定，`(student_id, course_id, section_id)` 唯一。
   之后同一学生同一课程/课序必须使用同一地址，否则隔离 `email_sender_mismatch`
   （设计 §4.4）。地址在应用层已规范化小写，库层再钉一次（`sender = lower(sender)`）。
4. `intake_state`：收件拉取心跳的**单行**表（`id = 1` 由 CHECK 钉住）。评测据此区分"邮件没到"
   （学生侧 `missing_output`）与"收件依赖不可用"（`infrastructure_error`）。

## 历史数据

纯新增表：既有行不受影响；`downgrade()` 按外键依赖倒序删除。

另修正两处**已发布迁移的遗留缺陷**（不改 0017/0018 本身）：`b2.intake_messages`（0018）与
`b2.manual_grades`/`b2.manual_grade_history`（0017）的时间列建表时用的是默认精度（precision 6），
与 B2 的"每个时间戳列都是 `timestamptz(3)`"表标准不符（`tests/test_migrations.py` 的精度回归闸
因此一直亮着）。本迁移把这些列**收窄到毫秒精度**：不删除行/列、不改可空性与默认值；既有行的时间
本来就是毫秒级（写入默认用 `date_trunc('milliseconds', now())`），因此没有可见的数据变化。
`downgrade()` 恢复默认精度。

## 与提交的事务关系

挑战消费与 `email_verifications` 写入在同一个事务（行锁 + `challenge_id` 唯一约束）；
`email_verifications.submission_id` 由 `SubmissionIntakeService.persist` 的 `on_persist` 钩子在
**提交事务**里回填。因此不会出现"verification 指向不存在的提交"，也不会出现"提交没有来信"。
两段式之间的恢复边界见 `intake/email_verification.py` 的模块文档。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

revision = "20261107_0020"
down_revision = "20261029_0019"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TS = TIMESTAMP(timezone=True, precision=3)
NOW = "date_trunc('milliseconds', now())"

CHALLENGES = "email_verification_challenges"
VERIFICATIONS = "email_verifications"
BINDINGS = "email_sender_bindings"
STATE = "intake_state"

CHALLENGE_CONSUMED_FK = "fk_email_verification_challenges_consumed_verification_id"


def upgrade() -> None:
    """建四张表、索引与约束，并把 0017/0018 留下的时间列收窄到毫秒精度。"""
    op.create_table(
        CHALLENGES,
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("code_sha256", sa.CHAR(64), nullable=False),
        sa.Column(
            "created_at", TS, nullable=False, server_default=sa.text(NOW)
        ),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("consumed_at", TS, nullable=True),
        sa.Column("consumed_verification_id", sa.Uuid(), nullable=True),
        sa.Column("last_rejection_code", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("challenge_id", name="pk_email_verification_challenges"),
        sa.UniqueConstraint(
            "code_sha256", name="uq_email_verification_challenges_code_sha256"
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_email_verification_challenges_window"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_email_verification_challenges_attempts"),
        sa.CheckConstraint(
            "(consumed_at IS NULL AND consumed_verification_id IS NULL)"
            " OR (consumed_at IS NOT NULL AND consumed_verification_id IS NOT NULL)",
            name="ck_email_verification_challenges_consumption",
        ),
        sa.CheckConstraint(
            "last_rejection_code IS NULL"
            " OR last_rejection_code IN ('email_verification_rejected',"
            " 'email_sender_mismatch', 'email_verification_expired')",
            name="ck_email_verification_challenges_rejection",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_verification_challenges_student_question",
        CHALLENGES,
        ["student_id", "question_version_id"],
        schema=SCHEMA,
    )

    op.create_table(
        VERIFICATIONS,
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("student_number", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("mailbox", sa.Text(), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("attachment_names", JSONB(), nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column(
            "created_at", TS, nullable=False, server_default=sa.text(NOW)
        ),
        sa.PrimaryKeyConstraint("verification_id", name="pk_email_verifications"),
        sa.UniqueConstraint(
            "challenge_id", name="uq_email_verifications_challenge_id"
        ),
        sa.UniqueConstraint(
            "submission_id", name="uq_email_verifications_submission_id"
        ),
        sa.UniqueConstraint(
            "mailbox",
            "uidvalidity",
            "uid",
            name="uq_email_verifications_mailbox_uidvalidity_uid",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attachment_names) = 'array'",
            name="ck_email_verifications_attachment_names",
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            [f"{SCHEMA}.{CHALLENGES}.challenge_id"],
            name="fk_email_verifications_challenge_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_email_verifications_submission_id",
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_email_verifications_student_id",
        VERIFICATIONS,
        ["student_id"],
        schema=SCHEMA,
    )
    # 挑战行回指 verification（"已消费"的凭据）。两表互相引用，因此这条外键在两张表都建好之后
    # 再加；两行都在**同一事务**里写入，约束在任何时刻都成立。
    op.create_foreign_key(
        CHALLENGE_CONSUMED_FK,
        CHALLENGES,
        VERIFICATIONS,
        ["consumed_verification_id"],
        ["verification_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_table(
        BINDINGS,
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("bound_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("binding_id", name="pk_email_sender_bindings"),
        sa.UniqueConstraint(
            "student_id",
            "course_id",
            "section_id",
            name="uq_email_sender_bindings_scope",
        ),
        sa.CheckConstraint(
            "sender = lower(sender) AND char_length(sender) BETWEEN 3 AND 320",
            name="ck_email_sender_bindings_sender",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        STATE,
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("last_successful_pull_at", TS, nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_intake_state"),
        sa.CheckConstraint("id = 1", name="ck_intake_state_singleton"),
        schema=SCHEMA,
    )

    # 0017/0018 遗留的时间精度修正（见模块文档）：只收窄精度，不动行/列/可空性/默认值。
    # 0017（人工评分）与 0018（来信摘录）建表时用的是 `sa.DateTime(timezone=True)`（precision 6），
    # 而 B2 的表标准是 `timestamptz(3)`；`tests/test_migrations.py` 的精度回归闸因此一直亮着。
    # 这些列的数据本来就是毫秒级（默认值用 `date_trunc('milliseconds', now())`），收窄无可见变化。
    for table_name, columns in (
        ("intake_messages", ("received_at", "created_at")),
        ("manual_grades", ("graded_at", "updated_at")),
        ("manual_grade_history", ("recorded_at",)),
    ):
        for column in columns:
            op.alter_column(
                table_name,
                column,
                type_=TS,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
                schema=SCHEMA,
            )

    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{CHALLENGES} IS "
        "'一次性邮件核验码（只存 SHA-256；绑定学生/课程/课序/题目版本；30 分钟有效）'"
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{VERIFICATIONS} IS "
        "'真实 IMAP 邮件事实与它变成的提交（challenge_id/submission_id 唯一；不存正文）'"
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{BINDINGS} IS "
        "'首次通过验证的发件地址（同一学生同一课程/课序只一行）'"
    )
    op.execute(
        f"COMMENT ON TABLE {SCHEMA}.{STATE} IS '收件拉取心跳（单行 id=1；评测据此判基础设施错误）'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{VERIFICATIONS}.student_number IS "
        "'intake 时经 B4 internal identity 得到的已核验账号学号（可为空；不来自邮件内容）'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{CHALLENGES}.last_rejection_code IS "
        "'学生侧拒绝原因（供状态查询）；subject_mismatch/attachment_missing 为契约保留值'"
    )


def downgrade() -> None:
    """按外键依赖倒序删除四张表，并把 0017/0018 的时间列恢复为默认精度。"""
    # 先恢复 0017/0018 的时间列精度（`manual_grades`/`manual_grade_history` 是 0017 的表、
    # `intake_messages` 是 0018 的表，本迁移被回滚时它们仍然存在）。
    for table_name, columns in (
        ("intake_messages", ("received_at", "created_at")),
        ("manual_grades", ("graded_at", "updated_at")),
        ("manual_grade_history", ("recorded_at",)),
    ):
        for column in columns:
            op.alter_column(
                table_name,
                column,
                type_=sa.DateTime(timezone=True),
                existing_type=TS,
                existing_nullable=False,
                schema=SCHEMA,
            )
    op.drop_constraint(CHALLENGE_CONSUMED_FK, CHALLENGES, type_="foreignkey", schema=SCHEMA)
    op.drop_table(VERIFICATIONS, schema=SCHEMA)
    op.drop_table(CHALLENGES, schema=SCHEMA)
    op.drop_table(BINDINGS, schema=SCHEMA)
    op.drop_table(STATE, schema=SCHEMA)
