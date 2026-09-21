"""隔离区人工归属落点（`CHG-0014` 的 `resolveIntakeQuarantine`）。

## 为什么必须新增修订

契约要求 `POST /api/v1/intake/quarantines/{id}/resolve` 在记录归属后返回 `IntakeQuarantineView`，
而该 schema 的 `resolved` / `resolved_student_id` / `resolution_note` 三项**在 `0002` 建表时不存在**：
`b2.intake_quarantines` 从建成起只写不读，没有任何"处理状态"（见 `CHG-0014` §1.1 的描述）。
没有这三列，`resolve` 就只能谎报 `resolved=false`，或者把归属塞进 `detail` 这种自由文本里——
两者都会让"这封信已经被谁认领过"变成不可查询的事实。

`0002` 已应用在集成库上（`127.0.0.1:55432/autograder`，库内有演示数据），因此本修订**只向前加列**，
一行历史迁移都不改。

## 三列与那条 CHECK

| 列 | 类型 | 语义 |
|---|---|---|
| `resolved` | `boolean NOT NULL DEFAULT false` | 是否已人工归属 |
| `resolved_student_id` | `uuid`（可空） | 被归属到的平台用户 id（`CHG-0007` 起名单用的同一个键） |
| `resolution_note` | `text`（可空） | 归属理由（可选，≤500，契约 `maxLength`） |

CHECK `ck_intake_quarantines_resolution` 把"已归属 ⇔ 有归属对象"钉成**单一事实**：
`resolved = (resolved_student_id IS NOT NULL)`。少了它，"resolved=true 但没有学生"这种行可以落库，
而列表端点会把它显示成一条已处理的记录——处理给了谁却查不到。`resolution_note` 不参与该约束：
契约把 `note` 定为可选。

`resolution_note` 的长度上限（500）与契约 `IntakeQuarantineView.resolution_note.maxLength` 同值；
数据库只兜底 `char_length <= 1000`（与既有的 `ck_intake_quarantines_detail` 同一上限），
500 的判定在服务层（`intake.quarantine`）——**不在这里另立一个与契约不同的数字**：
契约一旦调整 maxLength，改的是服务层与契约，不是这条结构兜底。

## 数据后果

纯加列，**不改动任何既有行**：`resolved` 的默认值 `false` 让 0002 以来写下的每一条隔离记录
都如实读成"未处理"（这正是它们当时的真实状态），另外两列为 `NULL`。
`downgrade()` 删列删约束，行数与其余列逐字节不变——但**归属决定会随之丢失**，
因此降级后必须把"隔离记录重新变成不可辨认"这件事写进运维记录（见 `docs/database/migrations.md`）。

## 表标准

主键仍是 `pk_intake_quarantines`（0012 已改名），本修订不新增时间列，故不涉及 `timestamptz(3)`。
新约束按 B4 `table-standards.md` 的命名规则写成 `ck_<table>_<subject>`。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260915_0013"
down_revision = "20260915_0012"
branch_labels = None
depends_on = None

SCHEMA = "b2"
TABLE = "intake_quarantines"
RESOLUTION_CHECK = "ck_intake_quarantines_resolution"
NOTE_CHECK = "ck_intake_quarantines_resolution_note"
#: 结构兜底上限（与既有 `ck_intake_quarantines_detail` 同值）；契约的 500 由服务层判定。
NOTE_LIMIT = 1000


def upgrade() -> None:
    """加三列 + 两条 CHECK（纯加列，不改既有行）。"""
    op.add_column(
        TABLE,
        sa.Column(
            "resolved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("resolved_student_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("resolution_note", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        RESOLUTION_CHECK,
        TABLE,
        "resolved = (resolved_student_id IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        NOTE_CHECK,
        TABLE,
        f"resolution_note IS NULL OR char_length(resolution_note) BETWEEN 1 AND {NOTE_LIMIT}",
        schema=SCHEMA,
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.resolved IS "
        "'是否已人工归属（CHG-0014）；归属只记录身份，不触发重新评测'"
    )
    op.execute(
        f"COMMENT ON COLUMN {SCHEMA}.{TABLE}.resolved_student_id IS "
        "'被归属到的平台用户 id（B4 的 sub；B2 不建跨 schema 外键）'"
    )


def downgrade() -> None:
    """删约束与三列（**会丢失已记录的归属**，见模块 docstring 的数据后果）。"""
    op.drop_constraint(NOTE_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_constraint(RESOLUTION_CHECK, TABLE, type_="check", schema=SCHEMA)
    op.drop_column(TABLE, "resolution_note", schema=SCHEMA)
    op.drop_column(TABLE, "resolved_student_id", schema=SCHEMA)
    op.drop_column(TABLE, "resolved", schema=SCHEMA)
