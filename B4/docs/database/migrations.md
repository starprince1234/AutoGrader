# B4 Alembic 迁移约定

## 独立版本表

- 每模块独立版本表：B4 使用 `alembic_version_b4`，且落在 `b4` schema（`env.py` 已配置
  `version_table="alembic_version_b4"`、`version_table_schema="b4"`）。
- 好处：与 B2/B3 的版本表互不干扰，四模块可在同一 PostgreSQL 实例各自迁移。

## 命名与粒度

- revision 命名：`YYYYMMDD_NNNN_<short_slug>`（如 `20260915_0001_b4_schema_baseline`）。
- **一次迁移只做一件事**：建表、加列、改约束各自成文件；不把多件事塞进一个迁移。
- 可回滚性：每个 `upgrade()` 尽量配套 `downgrade()`；确不可回滚（如数据清空类）必须在迁移 docstring
  说明原因。

## 迁移清单

| revision | down_revision | 内容 | 可回滚 |
|---|---|---|---|
| `20260915_0001` | `None` | 确保 `b4` schema 存在（存在性守卫，见下） | 需要 schema owner（`DROP SCHEMA b4`） |
| `20260915_0002` | `20260915_0001` | 15 张平台表 + 索引/唯一/外键/CHECK + 向应用角色授权 | 是（按依赖逆序 `DROP TABLE`） |
| `20260915_0002`（head） | — | `module.yaml: database.migration_head = "20260915_0002"` | — |

## 当前 head 与 `module.yaml` 一致性

- `module.yaml: database.migration_head = "20260915_0002"`，与 `alembic_version_b4` 实测值一致。
- 自动校验（`tests/test_database.py`）：
  - `test_migration_head_matches_module_manifest` —— 数据库 head == `module.yaml`；
  - `test_migration_head_matches_the_migration_files` —— 数据库 head == 迁移文件的唯一 head。
  两条一起才能排除"改了 manifest 冒充一致"。
- MAIN 侧另有 `check_local_configuration_consistency()` 校验 `module.yaml` 与迁移文件 head，本模块
  保持通过。

## 为什么迁移 0001 是"存在性守卫"而不是裸 `CREATE SCHEMA`

`alembic/env.py` 的 DDL 由**最小权限应用角色**执行。PostgreSQL 在执行 `CREATE SCHEMA` 时**先**校验
database 级 `CREATE` 权限，**再**看 `IF NOT EXISTS`：因此 `CREATE SCHEMA IF NOT EXISTS b4` 在 schema
已存在时依然会对应用角色报 `permission denied for database`。

职责划分因此明确为：

- **schema 创建属于供给步骤**（管理员连接，`python -m autograder_b4.ops.provision`）；
- **表创建属于迁移**（应用角色）。

0001 只在 schema 确实缺失时创建，并会在缺失时以清晰的权限错误失败——这正是"必须先供给"的正确提示。
实测：空库 + 先供给 + 应用角色执行 `upgrade head` 成功（见 `../phases/active/phase-01-platform-implementation.md`）。

## 生成与执行命令

```powershell
# 执行到 head（DATABASE_URL 指向应用角色）
uv run alembic upgrade head
# 回滚一步
uv run alembic downgrade -1
# 查看当前版本
uv run alembic current
# 生成新迁移（本模块 target_metadata=None，DDL 手写；见下）
uv run alembic revision -m "add x"
```

> 本项目**不使用** `--autogenerate`：`target_metadata` 为 `None`，物理表结构以手写、自包含的迁移为准。
> 应用侧的 SQLAlchemy 表定义（`src/autograder_b4/db/tables.py`）由
> `tests/test_schema_standard.py::test_application_metadata_matches_the_migrated_database`
> 与**真实库**逐列比对，两边无法悄悄漂移。

## 约束

- 迁移内**不**出现跨 schema 引用；`b4` 迁移只操作 `b4` 对象。
- 迁移不得含手工业务数据（种子数据用独立脚本，不在迁移里塞 PII/口令）。
- 提交前 `uv run alembic upgrade head` 在空库可执行，且 `alembic_version_b4` 落在 `b4`。
- `env.py` 在迁移前用 `set_config('b4.application_role', …)` 把应用角色名传给迁移，0002 据此对新表授权；
  该调用后有**显式 commit**——否则 Alembic 会把连接视为"外部事务"而不提交，迁移会静默整体回滚。
