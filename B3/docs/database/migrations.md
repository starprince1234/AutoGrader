# B3 数据库迁移（Migrations）

**迁移事实源**：`alembic/versions/**`；策略以本文件为准，与 `MAIN/docs/database/ownership.md`、B4 表结构规范（`RQ-B-4-30`）一致。

## Alembic 约定

| 项 | 约定 |
|---|---|
| 版本表名 | `alembic_version_b3`（`alembic.ini` 与 `env.py` 均固定；隔离于其他模块的版本表） |
| 版本表 schema | `b3`（`env.py` 中 `version_table_schema="b3"`） |
| 迁移文件命名 | `YYYYMMDD_NNNN_<slug>.py`（如 `20260915_0002_b3_question_bank.py`） |
| 一次迁移一件事 | 强制；不混合多个不相关变更 |
| 可回滚性 | 每个迁移提供 `downgrade()`，且**已实测往返**（见下） |
| schema 归属 | 只对 `b3` schema 执行 DDL；禁止触碰 `b2`/`b4`/`public` |
| 授权 | 最小权限角色授权写在迁移里（DDL 与权限同处可评审）；角色不存在时跳过而非失败 |

## 当前 head 与一致性

| 迁移 | 内容 | down_revision |
|---|---|---|
| `20260915_0001` | 空基线：`CREATE SCHEMA IF NOT EXISTS b3` | `None` |
| `20260915_0002` | **完整业务**：15 张业务表、5 个触发器、最小权限授权 | `20260915_0001` |
| `20260915_0003` | **凭证与消费可见性**：`reference_attestations`、`consumption_counters`、`question_versions` 的 `revision_note`/`revision_diff`、`questions.tags` 三条 CHECK、两处 keyset/检索索引、新对象授权 | `20260915_0002` |
| `20260915_0004` | **表规范对齐**（`RQ-B-4-30` / E16）：17 个主键约束命名为 `pk_<table>`；15 个 `created_at`/`updated_at` 改为 `timestamptz(3)`。只改声明，不改键形状、不引入兼容层 | `20260915_0003` |

- 当前 head：`20260915_0004`，与 `module.yaml: database.migration_head` **一致**（`tests/test_migrations.py::test_module_yaml_head_matches_alembic_head` 断言；MAIN 的 `checks.py` 亦独立校验）。
  该断言此前还钉死了字面量 `"20260915_0003"`，于是下一个合法迁移必然让它失败，而它并未因此多证明任何东西；已改为只断言 `declared == head`（与 `test_contracts_lock_is_not_hand_edited` 同一处理）。
- 一致性要求：每新增迁移后，`module.yaml` 的 `migration_head` 与 Alembic head 必须一致（`RQ-B-4-30` 验收 3）。
- **`20260915_0004` 的两点如实说明**：
  1. **这是数据变更，不是 no-op**。重命名约束只重写 `pg_constraint` 一行（非表重写）；但把 `timestamptz`
     收窄为 `timestamptz(3)` 不是二进制可强转的改动，PostgreSQL 会**重写表并按毫秒四舍五入已有值**，
     亚毫秒精度就此丢失且 `downgrade` 无法恢复。已写在迁移 docstring 里。
  2. `upgrade`/`downgrade` 双向**幂等且不假设约束名**（当前名从 `pg_constraint` 读、类型变更按当前
     precision 判定），因此在已修正的库上重跑是 no-op，在全新库上同样收敛到同一状态。
  3. B3 **没有**去"修"被规范合法豁免的那些表（`blobs` 内容寻址主键、`consumption_counters` 枚举自然键、
     五个复合自然键、两个正确的非复数表名）：它们出现在迁移里只为**命名约束**，键的形状保持复合。
  4. **门禁状态（重要，未达成）**：`python -m autograder_b4.ops.table_standards --scope migrations`
     检查的是**迁移文本**，而它是纯静态扫描（只识别 `CREATE TABLE`/`CREATE INDEX`/`CREATE VIEW`，
     其文档亦自述"读文件 ≠ 读表"）。增量 `ALTER TABLE` 修正对该扫描器**不可见**——本修订被扫描为
     **0 条 DDL 语句、0 条违规**，32 条仍记在 `20260915_0002`/`0003` 的原文上。**库层面已合规**（见
     [Phase 05](../phases/active/phase-05-table-standard-alignment.md) 的真实输出），文本层面要让门禁归零
     **必须改动已应用的 revision**，超出本模块授权，已上报。

## 生成与执行命令

```powershell
cd B3

# 模块本地库（compose 自动执行 infra/postgres/init.sql 建角色）
docker compose up -d postgres
$env:DATABASE_URL = 'postgresql+psycopg://autograder_b3:change-me-local-only@127.0.0.1:55434/autograder'

# 迁移到 head（env.py 优先读 DATABASE_URL，覆盖 alembic.ini 的占位值）
uv run alembic upgrade head

# 查看当前 head
uv run alembic heads

# 回滚一步（仅当迁移可回滚）
uv run alembic downgrade -1
```

- `alembic.ini` 的 `sqlalchemy.url` 指向**平台库**（`localhost:55432`，与 `MAIN/compose.yaml` 一致，MAIN 门禁会校验端口）；真实口令/账号用环境变量 `DATABASE_URL` 覆盖。平台库上的 `b3` schema 由 MAIN 的 `infra/postgres/init.sql` 创建，其最小权限角色由 B4 供给。
- 测试套件不依赖上述手工步骤：会话夹具会 `DROP SCHEMA b3` 后调用 `alembic upgrade head`（走 `tests/conftest.py` 的模块本地库 DSN，可用 `B3_TEST_OWNER_URL` 等环境变量覆盖），因此"空库从零迁移"每次测试运行都在被验证。

## 迁移策略要点

1. **schema 与业务表分离**：`0001` 只建 schema；`0002` 建全部业务表（题目族 / 受保护内容 / fixture / 作业 / 事件 / 统计）；`0003` 只加"凭证 + 消费可见性"这一件事所需的表、列、约束与索引。
2. **不可变约束落到数据库**：`question_versions` 触发器拒绝已发布内容原地改写、拒绝非草稿删除、只允许 `published → archived` 状态迁移；`reference_answers`/`test_suites`/`test_cases`/`fixtures` 触发器在其所属版本非草稿时拒绝一切写操作。统一使用 SQLSTATE `23001`，API 层映射为 `409`。
   - `0003` 新增列 `revision_note`/`revision_diff` **不加入**触发器的不可变字段清单：它们是"为什么有第二版"的元数据，不是版本内容；`content_digest` 仍在清单内，所以发布后内容摘要不可被改写。
3. **级联**：草稿删除沿 `question_versions → reference_answers / test_suites → test_cases / fixtures` 级联；`assignment_items` 保持 RESTRICT（已发布版本本就不可删）；`reference_attestations` 随版本 `ON DELETE CASCADE`（版本不存在时凭证无意义）。
4. **受保护内容独立权限**：`REVOKE ALL` 五个受保护表，普通角色只能通过服务间 package 端点间接取得评测所需内容。
   - `reference_attestations` **刻意不列入受保护表**：它只有摘要与计数，没有它所描述的内容；普通角色可 `SELECT`，只有内部角色可写（写它需要读受保护内容来重算摘要）。
   - `consumption_counters` 由消费器（普通角色）读写。
5. **禁止手工改库**：所有表结构变更必须有迁移文件（`RQ-B-4-30` 验收 5）。
6. **禁止跨 schema FK**：迁移中不产生指向 `b2`/`b4` 的外键；`course_id`/`section_id`/`created_by` 为普通 UUID 列（`tests/test_migrations.py::test_no_foreign_key_leaves_the_b3_schema` 断言）。
7. **命名**：索引 `ix_<table>_<cols>`、唯一约束 `uq_<table>_<cols>`、外键 `fk_<table>_<ref>`、CHECK `ck_<table>_<rule>`；主键沿用 PostgreSQL 默认名（`test_indexes_follow_the_naming_convention` / `test_constraint_names_follow_the_convention` 断言）。
8. **标签形态也是存储约束**：`0003` 给 `questions.tags` 加三条 CHECK（是数组、每项匹配契约正则、条数 ≤ 20），使"规范化标签"不只是调用方约定；域层已先以 `422` 拒绝非法输入，因此违反约束意味着缺陷而非用户输入。
   - 该 CHECK 用 `jsonb_path_exists(... like_regex ...)` 表达，因为正则只对整个 jsonb 的文本表示（`jsonb ~`，`Array[...]` 前缀）匹配，逐元素判断必须走 JSONPath。

## 审计结果（真实性证据）

- 时间基线 **2026-09-18**（Phase 03）：
  - `uv run alembic upgrade head`：`Running upgrade 20260915_0002 -> 20260915_0003`（exit 0）。
  - `uv run alembic heads` / `uv run alembic current`：均为 `20260915_0003 (head)`。
  - `uv run pytest -q`：`331 passed`（会话夹具每次运行都 drop `b3` 后从零迁移，因此"空库从零迁移"是**持续验证**的结论；`test_migration_downgrade_then_upgrade_round_trips` 覆盖回滚往返）。
- 时间基线 **2026-09-15**（Phase 01，保留原记录）：`upgrade head` → `20260915_0001`、`20260915_0002`；容器内 `psql` 核对 `b3` 下 16 张表（15 业务 + 版本表）、5 个触发器、3 个角色；MAIN 的 `checks.py`：`migration heads and local ports agree with compose: ok`。

## 清理要求（MAIN 门禁）

- 迁移中**不得存在**旧 scoring / evidence / report 相关表或模型，也不得出现 `RQ-B-3-70` 编号或任何兼容别名。MAIN 的 `check_deprecated_b3_artifacts()` 会扫描 `src/`、`alembic/`、`module.yaml`（含 `__pycache__` 字节码）；本阶段曾因迁移 docstring 与陈旧 `.pyc` 触发该门禁，已修正（见 [Phase 01 记录](../phases/active/phase-01-question-bank-and-packages.md)）。
- `tests/test_migrations.py::test_no_legacy_tables_anywhere` 与 `tests/test_hygiene.py::test_no_legacy_identifier_anywhere` 在 B3 内部做同类断言。
