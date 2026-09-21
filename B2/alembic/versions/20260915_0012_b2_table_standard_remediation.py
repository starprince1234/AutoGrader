"""表标准整改（`RQ-B-4-30`，缺陷 E16 的 B2 份额）：主键约束命名 + 毫秒精度。

## 为什么是"再补一个修订"而不是改历史迁移

`0002`–`0011` **已经被应用到集成库**（`127.0.0.1:55432/autograder`，里面有已播种的演示数据）。
改写已应用的修订会让 `alembic_version` 与实际结构对不上（库里是旧结构，而版本号说"已经跑过新版"），
那是最难查的一类漂移。因此本修订只做**向前修正**，一行历史迁移都不动。

## 两件事，都由 B4 的表标准检查器判定（`RQ-B-4-30`）

| 类 | 数量 | 原状 | 目标 |
|---|---|---|---|
| 主键约束未命名 | 18 张表 | Postgres 隐式命名 `<表>_pkey` | `pk_<表>` |
| 时间列无毫秒精度 | 27 列（**全部**时间戳列） | `timestamptz` | `timestamptz(3)` |

复算命令（在仓库根执行，检查器属 B4）：

```powershell
uv run --directory B4 --no-sync python -m autograder_b4.ops.table_standards --scope migrations
```

## 数据后果（**不是** no-op，必须说清楚）

把 `timestamptz` 收窄成 `timestamptz(3)` 会**丢弃亚毫秒位**，而且 Postgres 是**四舍五入**而不是截断：
实测 `'…00.9999+00'::timestamptz::timestamptz(3)` 得到 `…01`（**进位到下一秒**），
`'…00.0004+00'` 得到 `…00`。因此：

- 已存在行里毫秒以下的信息会**永久改变**（最多 ±0.5 毫秒的偏移）；
- 这些列都是审计/排序用的时间戳，**不参与任何分数、摘要或绑定计算**，所以偏移不影响可重放性；
- `ALTER COLUMN … TYPE` 会重写表并重建依赖它的索引（`ix_artifacts_created_at`、
  `ix_submissions_student_id_created_at`、`ix_artifact_gc_runs_started_at`、
  `ix_intake_quarantines_created_at`、`ix_submission_state_transitions_submission_id_occurred_at` 等），
  执行期间持有 `ACCESS EXCLUSIVE` 锁。B2 的表都在 MB 量级，这是秒级操作。

**降级**会把类型还原成无显式精度的 `timestamptz`（等价于 `(6)`）——这**不会**把已经舍入的值还原，
只是重新允许更高精度。这一点在 `downgrade()` 的 docstring 里也写了，避免被读成"来回一趟什么都没变"。

## 幂等与"不假设隐式名"

两个方向都**先查后改**：主键名从 `pg_constraint` 现查（不硬编码 `<表>_pkey`），
列精度从 `information_schema.columns.datetime_precision` 现查。
因此：全新库（0002 刚建完 ⇒ 名字是隐式的）、已整改过的库、以及被回滚过一次的库上重复执行都不会失败。
主键**查不到**时**明确报错**而不是跳过——"没有主键"是另一类偏差，静默跳过等于把它藏起来。

**新库上它是真正的 no-op**：`0002`–`0011` 的声明文本已在同一批工作中整改
（主键内联命名、时间列写 `precision=3`），因此从零建到 `0011` 的库本来就合规，
`0012` 查到"已经是目标状态"后不改任何东西。这一点由
`tests/test_migrations.py::test_fresh_database_needs_no_correction_and_the_revision_is_a_true_no_op`
用**约束 OID** 证明（只比名字无法排除"删了又建"这种伪 no-op）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260915_0012"
down_revision = "20260915_0011"
branch_labels = None
depends_on = None

SCHEMA = "b2"
PK_PREFIX = "pk_"
#: Postgres 对未命名主键的隐式命名规则（降级时还原成它）。
IMPLICIT_PK_SUFFIX = "_pkey"
#: 标准要求的时间列精度。
MILLISECOND_PRECISION = 3
#: 无显式精度的 `timestamptz` 在 information_schema 里报告为 6。
DEFAULT_PRECISION = 6

#: 需要改名为主键约束 `pk_<table>` 的 18 张表（含 0010 新建的评语表）。
PRIMARY_KEY_TABLES: tuple[str, ...] = (
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
    "submission_files",
    "submission_state_transitions",
    "submissions",
)

#: 需要收窄到 `timestamptz(3)` 的**全部时间戳列**（27 个）。
#:
#: 注意这条清单在开发过程中被**规则本身**扩过一次：B4 的检查器最初只对
#: `created_at`/`updated_at` 要求毫秒精度，后来把迁移扫描对齐到 catalog 扫描（后者一直检查**每一个**
#: 时间戳列），于是同一份 schema 在两个 scope 下不再给出不同判词。本清单因此与检查器当前规则一致：
#: 只要是时间戳列，就要求 `(3)`。迁移 0012 覆盖全部 27 列，而不是最初那 7 列。
MILLISECOND_COLUMNS: tuple[tuple[str, str], ...] = (
    ("artifacts", "created_at"),
    ("submissions", "created_at"),
    ("submissions", "updated_at"),
    ("submission_files", "created_at"),
    ("email_checkpoints", "processed_at"),
    ("intake_quarantines", "created_at"),
    ("artifact_gc_runs", "started_at"),
    ("artifact_gc_runs", "finished_at"),
    ("artifact_gc_audits", "occurred_at"),
    ("outbox_events", "occurred_at"),
    ("outbox_events", "enqueued_at"),
    ("outbox_events", "published_at"),
    ("outbox_events", "dead_lettered_at"),
    ("submission_state_transitions", "occurred_at"),
    ("evaluation_runs", "created_at"),
    ("evaluation_runs", "updated_at"),
    ("evaluation_runs", "started_at"),
    ("evaluation_runs", "finished_at"),
    ("evaluation_run_state_transitions", "occurred_at"),
    ("progress_events", "occurred_at"),
    ("evaluation_results", "persisted_at"),
    ("static_findings", "occurred_at"),
    ("security_findings", "occurred_at"),
    ("case_evidence", "occurred_at"),
    ("inbox_events", "received_at"),
    ("evaluation_commentaries", "requested_at"),
    ("evaluation_commentaries", "generated_at"),
)


def _rename_primary_key(table: str, target: str) -> None:
    """把某表的主键约束改名为 `target`（幂等；名字在**执行时**由 `pg_constraint` 现查）。

    为什么写成 `DO` 块而不是"先 `SELECT` 再 `ALTER`"：本模块必须同时支持**离线模式**
    （`alembic upgrade head --sql`，此时没有连接可用，`op.get_bind()` 是假的），
    而离线模式同样不允许硬编码隐式名 `<table>_pkey`。把"查名字"放进 `DO` 块，
    两条路径就只有一份实现，且"查不到主键即报错"这条也在 SQL 里成立。
    """
    op.execute(
        sa.text(
            f"""
DO $$
DECLARE current_name text;
BEGIN
    SELECT con.conname INTO current_name
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
     WHERE nsp.nspname = '{SCHEMA}' AND rel.relname = '{table}' AND con.contype = 'p';
    IF current_name IS NULL THEN
        RAISE EXCEPTION '{SCHEMA}.{table} has no primary key constraint';
    END IF;
    IF current_name <> '{target}' THEN
        EXECUTE format('ALTER TABLE {SCHEMA}.%I RENAME CONSTRAINT %I TO %I',
                       '{table}', current_name, '{target}');
    END IF;
END $$;
"""
        )
    )


def _set_precision(table: str, column: str, precision: int) -> None:
    """把某时间列的精度设为 `precision`（幂等；当前精度在执行时现查）。"""
    type_text = "timestamptz" if precision == DEFAULT_PRECISION else f"timestamptz({precision})"
    op.execute(
        sa.text(
            f"""
DO $$
DECLARE current_precision int;
BEGIN
    SELECT datetime_precision INTO current_precision
      FROM information_schema.columns
     WHERE table_schema = '{SCHEMA}' AND table_name = '{table}' AND column_name = '{column}';
    IF current_precision IS NULL THEN
        RAISE EXCEPTION '{SCHEMA}.{table}.{column} does not exist';
    END IF;
    IF current_precision <> {precision} THEN
        EXECUTE format('ALTER TABLE {SCHEMA}.%I ALTER COLUMN %I TYPE {type_text}',
                       '{table}', '{column}');
    END IF;
END $$;
"""
        )
    )


def upgrade() -> None:
    """18 个主键改名 + 7 列收窄到毫秒精度（两步都幂等；数据后果见模块 docstring）。"""
    for table in PRIMARY_KEY_TABLES:
        _rename_primary_key(table, f"{PK_PREFIX}{table}")
    for table, column in MILLISECOND_COLUMNS:
        _set_precision(table, column, MILLISECOND_PRECISION)


def downgrade() -> None:
    """还原：主键名回到 Postgres 的隐式 `<table>_pkey`，时间列回到无显式精度的 `timestamptz`。

    **不会**把已经四舍五入到毫秒的值还原（原始亚毫秒位在 `upgrade()` 时就已经丢了），
    因此"来回一趟"得到的不是逐位相同的旧数据，只是旧**结构**。
    """
    for table in PRIMARY_KEY_TABLES:
        _rename_primary_key(table, f"{table}{IMPLICIT_PK_SUFFIX}")
    for table, column in MILLISECOND_COLUMNS:
        _set_precision(table, column, DEFAULT_PRECISION)
