# Phase 04 表标准整改（缺陷 E16 的 B2 份额）

- **来源**：`MAIN/docs/innovation/inventory.md` 缺陷 **E16**（"必修缺陷"）。表标准归 B4（`RQ-B-4-30`）。
- **授权范围**：本工作项**获授权**改 `alembic/**` 与 `module.yaml` 的 `database.migration_head`
  （其余批次禁用这两处）。
- **未改**：`0002`–`0011` 的任何一行（它们已应用到集成库 `127.0.0.1:55432/autograder`，
  且库里已有播种演示数据；改写已应用的修订会让 `alembic_version` 与实际结构反向漂移）。
  未动 `MAIN/**`、`B3/**`（B3 的 32 条偏差属 B3）。

## 改了什么

| 文件 | 变更 |
|---|---|
| `alembic/versions/20260915_0012_b2_table_standard_remediation.py`（新增） | 18 个主键约束改名为 `pk_<table>`；7 个 `created_at`/`updated_at` 列收窄到 `timestamptz(3)`。两个方向都**先查后改**、可重复执行 |
| `module.yaml` | `database.migration_head` → `20260915_0012` |
| `src/autograder_b2/db/schema.py` | `EXPECTED_SCHEMA_REVISION` → `20260915_0012` |
| `tests/test_migrations.py` | 三条新的持久闸（见下） |

### 两个实现上的取舍（都是被真实失败逼出来的，不是风格）

1. **名字与精度在 SQL 执行时现查，不硬编码 `<table>_pkey`**：因此"全新库（隐式名）／已整改库／
   刚被回滚过的库"三种起点都能跑。主键**查不到就 `RAISE EXCEPTION`**，不静默跳过——
   "没有主键"是另一类偏差，跳过等于把它藏起来。
2. **写成 `DO $$ … $$` 块，而不是"先 `SELECT` 再 `ALTER`"**：本模块必须支持离线模式
   （`alembic upgrade head --sql`）。离线模式没有可用连接（`op.get_bind()` 是假的），
   先查后改在实测中直接 `AttributeError: 'NoneType' object has no attribute 'scalar_one_or_none'`。
   `DO` 块让在线与离线共用同一份实现，并顺带保留了"执行时现查"。

### 数据后果（**不是** no-op，已在迁移 docstring 与 `docs/database/migrations.md` 写明）

`timestamptz` → `timestamptz(3)` **丢弃亚毫秒位**，且 Postgres 是**四舍五入**而不是截断——
实测（本机测试实例，纯 `SELECT` 探针）：

```text
'2026-01-01 00:00:00.9999+00'::timestamptz::timestamptz(3) -> 2026-01-01 00:00:01+00   （进位到下一秒）
'2026-01-01 00:00:00.0004+00'::timestamptz::timestamptz(3) -> 2026-01-01 00:00:00+00
```

这 7 列都是审计/排序时间戳，**不参与分数、`result_digest` 或五项绑定摘要**，因此偏移不影响可重放性。
`ALTER COLUMN … TYPE` 会重写这些表并重建依赖索引（`ix_artifacts_created_at`、
`ix_submissions_student_id_created_at`、`ix_artifact_gc_runs_started_at`、
`ix_intake_quarantines_created_at`、`ix_submission_state_transitions_submission_id_occurred_at` 等），
执行期间持有 `ACCESS EXCLUSIVE` 锁；B2 的表都是 MB 量级，实测是秒级操作。

## 验证（真实执行）

### 1. B4 的表标准检查器（迁移文本扫描）

```powershell
uv run --directory B4 --no-sync python -m autograder_b4.ops.table_standards --scope migrations
```

```text
migrations: 137 DDL statements in 18 files, 57 violation(s)
```

用检查器自己的 API 逐模块统计（不经控制台折行，数字精确）：

```text
files 18 statements 137 violations 57
B2 25 B3 32
```

**这 25 条仍然全部落在 `0002`–`0010`，`0012` 贡献 0 条**。原因不是修复没生效，而是
`--scope migrations` 是**逐条声明的文本扫描**（`check_migrations` → `scan_migration`，
只识别 `create_table`/`execute`/`create_index`/`add_column`，**不跟踪后续修订的修正**，
`DO` 块也不计入 DDL 语句——加固前后都是 `137 statements`）。
因此"B2 计数归零"只能靠**改写 `0002`–`0010` 的声明文本**达成，而那正是本工作项被明确禁止的动作。
**已上报并请决策**（三个选项：授权改声明文本 / 扩展检查器 / 为 b2 增加 live-schema 检查），
本模块不自行选择。

### 2. 真实库内的读数（B2 侧独立证据）

`tests/test_migrations.py` 新增三条闸，全部通过：

- `test_primary_key_constraints_are_named_pk_table`：查真实库的 `pg_constraint`，18 张表的主键名必须等于 `pk_<table>`；
- `test_created_and_updated_columns_have_millisecond_precision`：`created_at`/`updated_at` 必须是
  `timestamp with time zone` 且 `datetime_precision = 3`；
- `test_standard_remediation_round_trip_is_reversible_and_idempotent`：`upgrade head` 两次、
  `downgrade -1` 两次、再 `upgrade head`，并在降级后断言主键名回到 `<table>_pkey`、精度回到 6。

### 3. 迁移往返（**只在 B2 测试实例 55433 上执行**，未触碰 55432）

```powershell
cd B2
$env:DATABASE_URL = "postgresql+psycopg://autograder_platform:***@127.0.0.1:55433/autograder"
uv run alembic upgrade head        # -> 20260915_0012 (head)
uv run alembic downgrade -1        # -> 20260915_0011
uv run alembic upgrade head        # -> 20260915_0012 (head)
uv run alembic heads               # -> 20260915_0012 (head)
```

配合库内探针（同一实例，只读查询）：

| 时点 | 主键 | `created_at`/`updated_at` 精度 |
|---|---|---|
| `0012`（head） | 18/18 为 `pk_<table>` | `Counter({'3': 7})` |
| `0011`（`downgrade -1` 之后） | 18/18 回到 `<table>_pkey` | `Counter({'6': 7})` |
| `0012`（再次 `upgrade head`） | 18/18 为 `pk_<table>` | `Counter({'3': 7})` |

即：**可逆**不是"有一个 `downgrade()` 函数"的声明，而是实测的两种结构状态。

## 未做 / 未验收

- **B2 在 `--scope migrations` 下的计数未归零**（25 条，全部在历史声明里）。原因与三个可选处置见上，
  已上报请决策；本模块**没有**通过改写已应用修订来"买"一个绿色读数。
- **集成库（55432）未执行本迁移**：按指令由协调方在播种环境上单独执行。
  注意执行前该库停在 `0011`（`EXPECTED_SCHEMA_REVISION` 已是 `0012`），
  期间 B2 的评测/成绩/评语路由会按设计返回 503。
- **B3 的 32 条偏差**未动（属 B3）。
