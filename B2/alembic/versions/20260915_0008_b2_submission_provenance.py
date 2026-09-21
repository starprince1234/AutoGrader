"""持久化冻结信封的 correlation_id 与 intake provenance（`b2.submissions` 新增 6 列）。

背景（真缺陷，由编排工作项实测发现）：冻结类型 `SubmissionEnvelope`（`domain/envelope.py`）声明了
`correlation_id` 与 `provenance`（`ApiProvenance(request_id)` 或
`EmailProvenance(mailbox, uidvalidity, uid, message_id)`），但 `b2.submissions` **两样都没存**。
后果不是理论问题：

1. **评测阶段没有生产级输入路径**：评测编排器发事件时必须用 `submission.correlation_id`
   （`outbox_events.correlation_id` 是 `NOT NULL`），而库里拿不回这个值，也就没有任何函数能从库里
   重建出这个冻结类型；
2. **审计无法回答"这份提交来自哪封邮件/哪个请求"**：邮件入口的 check point 设计本来就是为了可追溯，
   而 checkpoint 里的事实（mailbox/UIDVALIDITY/UID）从未与提交行绑在一起；
3. 冻结信封因此**不可往返**：写进去的信息读不回来，"接收与存储"相对它自己声明的接口是不完整的。

本迁移新增的列（**显式列，不用 JSONB 大对象**：按 intake 取值只有两种形状，显式列才能被 CHECK 约束住，
也才能在库层面回答"api 的行不该有 mailbox"）：

- `correlation_id uuid NOT NULL`：全链路关联标识（此前只用于给 outbox 的 `submission.accepted` 盖章）；
- provenance 五列：`request_id text NULL`（api）、`mailbox text NULL`、`uidvalidity bigint NULL`、
  `uid bigint NULL`、`message_id text NULL`（email）。

约束 `ck_submissions_provenance_shape` 要求按 `intake` 取值**恰好一种形状**：

- `intake = 'api'` => `request_id IS NOT NULL`，且 `mailbox`/`uidvalidity`/`uid`/`message_id` **全为 NULL**；
- `intake = 'email'` => `request_id IS NULL`，且 `mailbox`/`uidvalidity`/`uid`/`message_id` **均非 NULL**。

`message_id` 之所以也要求非 NULL（依据）:冻结类型写的是 `EmailProvenance.message_id: str`（非可选），
而邮件入口在 `intake/imap.py` 第 528 行就把"没有 Message-ID"的来件拒绝掉了（原因码
`missing_message_id`，走隔离而不是产生提交），`intake/attachments.py:message_id_for_storage` 又把
一切空值折成 `None`。因此**能到达** `EmailProvenance(...)` 构造点（`intake/imap.py` 第 592 行的唯一
构造点）的 message_id 必然是非 NULL 的非空字符串（原样头值；超长则折成"前缀 + 原值摘要"）。
"processed 的 checkpoint 一定有 Message-ID"不是本迁移的假设，而是入口已经强制的行为。
`b2.email_checkpoints.message_id` 仍可为 NULL（那里的 NULL 属于被拒/隔离的来件，它们不产生提交）。

回填策略（表里可能已有数据，故必须先把列填满，再加 `NOT NULL` 与 CHECK）：

- **邮件来源的行**：从 `b2.email_checkpoints` 里 `status = 'processed'` 的行**忠实回填**
  mailbox/uidvalidity/uid/message_id（一条 `UPDATE ... FROM`）。processed 的 checkpoint 必定带
  `submission_id`（`ck_email_checkpoints_outcome` 保证），所以这是可追溯的真实来源，不是推测。
- **API 来源的行，以及任何无法忠实还原的 `correlation_id`**：回填为该行自己的 `submission_id`。
  这是**占位值，不声称它等于当初的请求标识**——那个标识当初根本没有被持久化（写入路径只把它盖在
  outbox 事件上，见 `intake/service.py` 第 284 行）。API 入口当时
  `ApiProvenance.request_id` 与 `correlation_id` 就是同一个值（同文件第 199 行），所以两列填同一个
  占位值：本迁移不制造"两个不同且都像真值"的假标识。
- **无法忠实回填的邮件来源行**（没有 processed checkpoint、或该 checkpoint 缺
  mailbox/uidvalidity/uid/message_id、或一封提交绑了多条 processed checkpoint 而来源不确定）：
  **不猜、不造**，迁移当场以明确错误中止。回填与 DDL 在同一事务里，中止即全部回滚，不留半成品。
  造一个假邮箱或假 UID 会让"审计可追溯"变成谎话，那比迁移失败更糟。

可回滚：`downgrade()` 按依赖逆序先删 CHECK、再删这六列；这六列是本迁移独占的，回滚不丢其他迁移的数据
（回滚会丢掉本迁移回填/新写入的 correlation_id 与 provenance，这是"回到不可往返状态"的代价，属预期）。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260915_0008"
down_revision = "20260915_0007"
branch_labels = None
depends_on = None

SCHEMA = "b2"

#: provenance 形状约束：按 intake 取值恰好一种形状（api 无邮件身份，email 无请求标识）。
_PROVENANCE_SHAPE = (
    "(intake = 'api' AND request_id IS NOT NULL"
    " AND mailbox IS NULL AND uidvalidity IS NULL AND uid IS NULL AND message_id IS NULL)"
    " OR (intake = 'email' AND request_id IS NULL"
    " AND mailbox IS NOT NULL AND uidvalidity IS NOT NULL AND uid IS NOT NULL"
    " AND message_id IS NOT NULL)"
)

#: 邮件来源行的忠实回填（一条 UPDATE ... FROM）。
_BACKFILL_EMAIL_PROVENANCE = (
    f"UPDATE {SCHEMA}.submissions AS s"
    " SET mailbox = c.mailbox, uidvalidity = c.uidvalidity, uid = c.uid, message_id = c.message_id"
    f" FROM {SCHEMA}.email_checkpoints AS c"
    " WHERE c.submission_id = s.submission_id AND c.status = 'processed' AND s.intake = 'email'"
)

#: correlation_id 的占位回填：当初没被持久化，任何"还原"都是编造，故用 submission_id 占位（见 docstring）。
_BACKFILL_CORRELATION_PLACEHOLDER = (
    f"UPDATE {SCHEMA}.submissions SET correlation_id = submission_id WHERE correlation_id IS NULL"
)

#: API 来源行的 request_id 占位回填：当时 request_id 与 correlation_id 是同一个值（service.py 第 199 行），
#: 因此只能填同一个占位值；既不编造"像请求标识"的值，也不留 NULL（否则 CHECK 加不上）。
_BACKFILL_API_REQUEST_ID = (
    f"UPDATE {SCHEMA}.submissions SET request_id = correlation_id"
    " WHERE intake = 'api' AND request_id IS NULL"
)

#: 回填前置校验：每一条邮件来源的提交行都必须恰好有**一条** processed checkpoint，且四要素齐全。
#: 不满足即中止（不猜、不造）。`count(c.submission_id) <> 1` 同时覆盖"没有 checkpoint"与
#: "多条 checkpoint（来源不确定）"两种情形。
_GUARD_EMAIL_BACKFILL = (
    f"DO $b2_provenance$ DECLARE unresolved bigint; BEGIN"
    " SELECT count(*) INTO unresolved FROM ("
    f" SELECT s.submission_id FROM {SCHEMA}.submissions s"
    f" LEFT JOIN {SCHEMA}.email_checkpoints c"
    " ON c.submission_id = s.submission_id AND c.status = 'processed'"
    " WHERE s.intake = 'email' GROUP BY s.submission_id"
    " HAVING count(c.submission_id) <> 1"
    " OR bool_or(c.mailbox IS NULL OR c.uidvalidity IS NULL OR c.uid IS NULL OR c.message_id IS NULL)"
    ") AS unresolved_rows;"
    " IF unresolved > 0 THEN RAISE EXCEPTION USING MESSAGE ="
    " 'b2.submissions provenance backfill - email submission rows have no faithful source in"
    " b2.email_checkpoints (email rows with no processed checkpoint, an ambiguous one, or a"
    " checkpoint missing mailbox/uidvalidity/uid/message_id). Refusing to fabricate provenance;"
    " count ' || unresolved || '. Resolve these rows before upgrading.';"
    " END IF; END $b2_provenance$"
)


def upgrade() -> None:
    # 先加可空列，回填后再收紧为 NOT NULL：表里可能已有数据，直接加 NOT NULL 会失败。
    op.add_column("submissions", sa.Column("correlation_id", sa.Uuid(), nullable=True), schema=SCHEMA)
    op.add_column("submissions", sa.Column("request_id", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("submissions", sa.Column("mailbox", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column("submissions", sa.Column("uidvalidity", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("submissions", sa.Column("uid", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("submissions", sa.Column("message_id", sa.Text(), nullable=True), schema=SCHEMA)

    # 回填前先证明"忠实回填"确实可行；不可行就中止（同一事务，全部回滚）。
    op.execute(_GUARD_EMAIL_BACKFILL)

    op.execute(_BACKFILL_EMAIL_PROVENANCE)
    op.execute(_BACKFILL_CORRELATION_PLACEHOLDER)
    op.execute(_BACKFILL_API_REQUEST_ID)

    op.alter_column("submissions", "correlation_id", nullable=False, schema=SCHEMA)
    op.create_check_constraint(
        "ck_submissions_provenance_shape", "submissions", _PROVENANCE_SHAPE, schema=SCHEMA
    )
    op.execute(
        f"COMMENT ON CONSTRAINT ck_submissions_provenance_shape ON {SCHEMA}.submissions IS "
        "'按 intake 取值恰好一种 provenance 形状：api 只有 request_id，email 只有"
        " mailbox/uidvalidity/uid/message_id'"
    )


def downgrade() -> None:
    # 依赖逆序：CHECK 引用全部六列，先删它，再按加入顺序的逆序删列。
    op.drop_constraint("ck_submissions_provenance_shape", "submissions", schema=SCHEMA, type_="check")
    op.drop_column("submissions", "message_id", schema=SCHEMA)
    op.drop_column("submissions", "uid", schema=SCHEMA)
    op.drop_column("submissions", "uidvalidity", schema=SCHEMA)
    op.drop_column("submissions", "mailbox", schema=SCHEMA)
    op.drop_column("submissions", "request_id", schema=SCHEMA)
    op.drop_column("submissions", "correlation_id", schema=SCHEMA)
