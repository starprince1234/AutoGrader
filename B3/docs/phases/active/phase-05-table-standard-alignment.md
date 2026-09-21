# Phase 05 表规范对齐（E16，当前阶段）

> 本阶段**只做一件事**：把 B3 的物理表对齐 B4 拥有并已机械化的表规范（`RQ-B-4-30`）。
> **结论先说：库已合规，静态门禁未归零**，原因与本模块的授权边界有关，见
> [未达成项](#未达成项静态门禁未归零需-main-裁决)。不把"库对了"写成"门禁过了"。

## 目标与范围

B4 的表规范检查器（修正了 12 条误判后）报出 **57 条**真实违规，其中 **32 条属 B3**，恰好两类：

| 类 | 数量 | 内容 |
|---|---|---|
| 未命名主键约束 | 17 | 声明为列级/表级 `PRIMARY KEY`，PostgreSQL 命名为 `<table>_pkey`；规范要求 `pk_<table>` |
| `timestamptz` 未声明毫秒精度 | 15 | 规范要求 `timestamptz(3)`（也是线上格式发出的精度） |

**明确不动**的那些（B4 已按声明豁免，且有结构性前置条件校验）：`blobs`（内容寻址主键）、
`consumption_counters`（枚举自然键）、`reference_attestations` / `question_version_score_buckets` /
`question_version_failure_categories`（复合自然键）、`event_inbox` / `event_outbox`（非复数表名正确）。
复合键**可以继续复合**，但它的**约束**仍须叫 `pk_<table>`——因此这些表出现在迁移里只为命名，键形状未动。

## 输入与决策

| # | 输入 / 现象 | 决策 |
|---|---|---|
| 1 | 集成库 `127.0.0.1:55432` 已迁移且含种子数据（103 个已发布版本、106 道题、377 个用例） | **不改动已应用的 revision**，新增 `20260915_0004` 增量修正；`module.yaml: migration_head` 同步 |
| 2 | 重命名主键约束会不会牵动外键或索引 | 不会。重命名只重写 `pg_constraint` 一行；`fk_fixtures_content_sha256` 引用的是被引用**列**，不是 `blobs` 的主键**名**。已在 55434 实测通过（否则该外键会报"no unique constraint matching"） |
| 3 | 收窄 `timestamptz(3)` 是不是纯声明改动 | **不是**。该类型变更非二进制可强转，PostgreSQL 重写表并把已有值**按毫秒四舍五入**，亚毫秒精度丢失，`downgrade` 无法恢复。已在迁移 docstring 与 [../../database/migrations.md](../../database/migrations.md) 写明 |
| 4 | 约束当前叫什么名字不可假设 | `upgrade`/`downgrade` 都从 `pg_constraint` 读当前名，类型变更按当前 precision 判定：双向**幂等**，在已修正的库与全新库上都收敛到同一状态 |
| 5 | 只改这两个类会不会漏 | 只改检查器报出的列（`created_at`/`updated_at`）与表；`published_at`/`due_at`/`occurred_at` 等保持服务端默认 |

## 完成条件与结果

| # | 完成条件 | 结果 |
|---|---|---|
| 1 | 新增一个修正 revision，`module.yaml` head 同步 | **达成**（`20260915_0004`） |
| 2 | 17 个主键约束名为 `pk_<table>`，15 列改为 `timestamptz(3)`，复合键仍复合 | **达成**（库内实测，见下） |
| 3 | `upgrade`/`downgrade` 双向幂等且不假设约束名 | **达成**（`downgrade -1` → `upgrade head` 往返实测） |
| 4 | 数据后果如实写明 | **达成**（docstring + migrations.md） |
| 5 | 门禁 `--scope migrations` 中 B3 计数为 0 | **未达成** —— 见下 |

## 验证（真实执行）

```powershell
# 1) 迁移（只打模块本地库 55434，未触碰 55432 集成库）
cd B3
$env:DATABASE_URL = "postgresql+psycopg://autograder_b3:change-me-local-only@127.0.0.1:55434/autograder"
uv run alembic upgrade head        # Running upgrade 20260915_0003 -> 20260915_0004（exit 0）
uv run alembic current             # 20260915_0004 (head)
uv run alembic downgrade -1        # Running downgrade 20260915_0004 -> 20260915_0003
uv run alembic upgrade head        # Running upgrade  20260915_0003 -> 20260915_0004
uv run alembic upgrade head        # 无输出 = 已在 head，重复执行安全

# 2) 库内事实
#    17 个主键约束全部名为 pk_<table>；created_at/updated_at 中 precision = 3 的有 15 列
#    （另有一次 downgrade 后复查：名字回到 <table>_pkey、precision 回到 6，再 upgrade 复原）

# 3) 套件与静态检查
uv run ruff check .                # All checks passed!
uv run pytest -q                   # 364 passed, 6 warnings

# 4) 表规范门禁
cd ..
uv run --directory B4 --no-sync python -m autograder_b4.ops.table_standards --scope migrations
#    migrations: 137 DDL statements in 17 files, 57 violation(s)   ← B3 仍是 32
```

### 库内实测（修复前后）

| 检查 | 修复前 | 修复后 |
|---|---|---|
| 主键约束不是 `pk_<table>` 的 | 17 | **0** |
| `created_at`/`updated_at` 且 `datetime_precision = 3` 的列数 | 0 | **15** |
| 跨 schema 外键 | 0 | 0（`fk_fixtures_content_sha256` 仍指向 `b3.blobs`，重命名未影响它） |

## 未达成项：静态门禁未归零（需 MAIN 裁决）

**事实**：`--scope migrations` 的 B3 计数**仍是 32**，本次改动**没有让它减少一条**。

**原因（已实测，不是推测）**：该检查器是**迁移文本**的静态扫描器，只识别 `CREATE TABLE` /
`CREATE INDEX` / `CREATE VIEW` 三种语句；其自身文档写明"**读文件 ≠ 读表**，结论是'B2/B3 的迁移文本
不符合规范'，不是'B2/B3 的库不符合规范'"。它对 `ALTER TABLE` 完全没有建模：

```
scan_migration(20260915_0004) -> ddl statements: 0, violations: ()
_CREATE_TABLE / _CREATE_INDEX / _CREATE_VIEW 在该文件中的匹配数：0 / 0 / 0
文件中 ALTER TABLE 出现 4 次、RENAME CONSTRAINT 2 次：扫描器一个都没看到
```

因此 32 条违规仍归因于 `20260915_0002`/`0003` 的**原始 CREATE 文本**。**增量修正对文本扫描器不可见**，
"新增一个修正 revision"与"该门禁报 0"在原理上无法同时成立。

**已证明可行的替代路径（未执行，因超出授权）**：把这两处**声明**在原始 revision 文本里写成合规形式即可让
B3 归零。在一个临时副本上实测（仓库文件未被改动）：

```text
20260915_0002_b3_question_bank.py       : 30 -> 0 violation(s)
20260915_0003_b3_attestation_and_consumption.py :  2 -> 0 violation(s)
```

改动只有两处、且纯声明：列级 `PRIMARY KEY` → 保持列名在行首的 `CONSTRAINT pk_<table> PRIMARY KEY (<col>)`；
裸 `PRIMARY KEY (` 前加约束名；`created_at`/`updated_at` 加 `(3)`。
（另有一个解析陷阱值得记下：把命名约束**另起一行**放在列之前，扫描器会把 `CONSTRAINT` 当成列名，
从而让 `blobs`/`consumption_counters` 的**原型校验**失败——所以名字必须与列同行。）

**为什么没做**：任务边界明确要求"不改已应用的 revision"。而且这条路径有一个仅剩的、**非 B3 所有**的
副作用：迁移文本与已应用 schema 之间会出现**注释级差异**（同版本号、同最终 schema、`alembic_version`
不错位），但 `alembic history` 已无记录。若 MAIN 接受该差异或愿意重跑集成库迁移，这是达到门禁 0 的
唯一已知路径；否则需要 B4 让检查器能"顺着 ALTER 序列解出最终状态"——那是 B4 的代码，不是 B3 的。

**另外两条属 B4 的观察**（顺着本次核验发现，B3 未改）：

1. `check_table_standards()`（catalog 模式）要求主键出现在 `PRIMARY_KEY_COLUMNS` 里、按 B4 自己的表名
   注册，因此**不能直接用来看 `b3` schema**（对 b3 会报大量"entity 未声明"）。想验证"真实 b3 库合规"
   需要一个按 `module.yaml: database.schema` 驱动的 catalog 入口。
2. catalog 模式检查**所有**时间列，而 migration 模式只检查 `created_at`/`updated_at`（其 `_TIME_COLUMNS`）。
   以 b3 为例，catalog 模式还会报 `published_at`/`archived_at`/`verified_at`/`recorded_at`/`deleted_at`/
   `last_occurred_at`/`occurred_at` 等精度问题，而 migration 模式对同样的列保持沉默。两个模式口径不同，
   同一份迁移在两边会得到不同条数，建议 B4 统一或写明差异。

## 附：改动文件

| 文件 | 改动 |
|---|---|
| `alembic/versions/20260915_0004_b3_table_standard_alignment.py` | 新增：17 个主键约束命名 + 15 列 `timestamptz(3)`；双向幂等且不假设约束名；docstring 写明数据后果与豁免边界 |
| `module.yaml` | `migration_head: "20260915_0004"` |
| `tests/test_migrations.py` | `test_module_yaml_head_matches_alembic_head` 去掉字面量 head 钉死（它会让下一个合法迁移必然失败，且未多证明任何东西） |
| `docs/database/migrations.md` | 新迁移条目、数据后果、门禁状态 |
| `docs/database/schema.md` | 主键命名与时间精度 |
| `docs/phases/active/phase-05-table-standard-alignment.md` | 本文件 |
| 未改动 | `20260915_0001`~`0003`（已应用）、`MAIN/contracts/**`、`contracts.lock`、`src/**` |

## 未做 / 未验证

- **门禁 B3 计数归零**：未达成，原因与可行路径见上。
- **集成库（55432）未跑本迁移**：按任务边界由 MAIN 负责应用；本模块只在 55434 上验证。
- **catalog 模式对 b3 的验证**：B4 侧缺入口（见上），因此"真实 b3 库合规"目前由直接 SQL 断言
  （`pg_constraint` 名称、`datetime_precision`）证明，而非由 B4 的检查器证明。
