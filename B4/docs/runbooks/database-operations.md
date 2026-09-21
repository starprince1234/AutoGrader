# B4 数据库运维手册（备份/恢复/健康/角色与 schema 供给）

> ✅ 本手册的命令**已在本环境真实执行**（PostgreSQL 17 容器，宿主端口 `55432`），命令与结果记录在
> `../phases/active/phase-01-platform-implementation.md`。应用一律使用最小权限角色，超级用户只用于
> 供给/备份/恢复。

## 0. 前置

- PostgreSQL 经 compose 运行：在 `../../MAIN` 目录执行 `docker compose up -d postgres`。
- 容器名 `main-postgres-1`（按 `docker compose ps` 实际名调整）。
- 两个连接串分工明确：
  - **应用角色**（`DATABASE_URL`）：`autograder_b4`，只访问 `b4`；
  - **管理员**（`B4_ADMIN_DATABASE_URL`）：仅供给/备份/恢复使用，**绝不**给应用。

## 1. 角色与 schema 供给

```powershell
cd ../B4
uv run python -m autograder_b4.ops.provision            # 幂等：建/更新角色、建 schema、授权、撤销跨 schema
uv run python -m autograder_b4.ops.provision --verify-only   # 只校验
```

实测输出：

```text
provisioned role autograder_b4 on schema b4: 16 steps
role autograder_b4 is least privilege: no b2/b3 access, no superuser attributes
```

供给脚本做的事（`src/autograder_b4/ops/provision.py`）：

- 建/更新 `autograder_b4`：`LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT 10`；
- `CREATE SCHEMA IF NOT EXISTS b4`（**schema 创建属于供给，不属于迁移**，原因见 `../database/migrations.md`）；
- `GRANT CONNECT ON DATABASE …`、`GRANT USAGE, CREATE ON SCHEMA b4`、表/序列的 DML 与默认权限；
- `ALTER ROLE … SET search_path = b4, pg_catalog`；
- 显式 `REVOKE ALL ON ALL TABLES IN SCHEMA b2/b3` 与 `REVOKE ALL ON SCHEMA b2/b3`；
- `REVOKE CREATE ON SCHEMA public FROM PUBLIC`。

**注意事项**

- 角色是**集群级**对象：给第二个数据库供给时必须传入同一个应用口令，否则既有部署会认证失败。
  库名取自 admin DSN（不是第二个环境变量），避免授权到错误的库。
- 口令从 `B4_DB_PASSWORD` 或 `--password` 读取，日志与异常中不出现。

### 最小权限实测（跨 schema 拒绝）

以 `autograder_b4` 连接后：

| 语句 | 结果 |
|---|---|
| `SELECT count(*) FROM b2.users` | `ERROR: permission denied for schema b2` |
| `CREATE TABLE b3.probe(i int)` | `ERROR: permission denied for schema b3` |
| `CREATE TABLE b4.probe(i int)` | `CREATE TABLE` |
| `CREATE TABLE b4.x(other uuid REFERENCES b2.users(user_id))` | 被拒（跨 schema 外键不可能建立） |
| `SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user` | `f / f / f` |

## 2. 迁移

```powershell
$env:DATABASE_URL="postgresql+psycopg://autograder_b4:<pw>@localhost:55432/autograder"
uv run alembic upgrade head
uv run alembic current
```

实测（空库 `b4_migration_probe_*`，先供给后迁移）：`Running upgrade  -> 20260915_0001`、
`Running upgrade 20260915_0001 -> 20260915_0002`，`version_num = 20260915_0002`，`b4` 下 16 张表。

**迁移结果自动入日志**（`RQ-B-4-99` 验收 4）：`alembic/env.py` 在迁移前后各发一条结构化记录，
不再依赖人工抄录。

```text
{"level": "INFO", "logger": "autograder_b4.ops.migration", "message": "ops.migration.applied",
 "producer": "B4", "event": "ops.migration.applied", "from_revision": "20260915_0001",
 "requested_revision": "head", "result_revision": "20260915_0002", "duration_ms": 412}
```

失败时发 `ops.migration.failed`（`ERROR`，含 `reason`/`error`，异常文本按 formatter 规则脱敏）后原样
抛出，退出码仍由 Alembic 决定。记录与 Alembic 自己的 `Running upgrade …` 输出**不重复**：迁移进程里
B4 的记录只走自己的 JSON handler，Alembic 的 console handler 原样保留（`logging.structured_module_logging`）。
注意 `alembic/env.py` 现在 import `autograder_b4.logging`，因此迁移需在装好本包的环境里执行
（`uv run alembic …`，与既有手册一致）。

## 3. 健康检查

```powershell
# 容器探针
docker compose exec postgres pg_isready -U autograder_platform -d autograder

# 版本表
docker compose exec -T postgres psql -U autograder_platform -d autograder -c "SELECT * FROM b4.alembic_version_b4;"

# 表结构规范自动检查：catalog（真实 b4）+ migrations（全仓迁移文本）
uv run python -m autograder_b4.ops.table_standards                    # 两者都跑
uv run python -m autograder_b4.ops.table_standards --scope catalog     # 只查 b4 真实 schema
uv run python -m autograder_b4.ops.table_standards --scope migrations  # 只扫 B*/alembic/versions
```

`--scope migrations` 读 B2/B3 的**迁移文件文本**（不是它们的库），当前报告 B2 26 条 / B3 36 条违规，
B4 自己 0 条；`--scope catalog` 对真实 `b4` schema 为 0 条。因此**日常自查用 `--scope catalog`，
规范执法用 `--scope migrations`**（详见 `../database/table-standards.md` §9）。

readiness 实现于模块内（`B4State.self_health()`，经 host 的 `/health/ready` 暴露），三项探针都是真实往返：

| 依赖 | 探针 | 不可用时 |
|---|---|---|
| `postgres` | `SELECT 1` | `healthy=false`，detail 含 `postgres unavailable: <错误类别>` |
| `migration` | 读 `b4.alembic_version_b4` 并与 `module.yaml` 比较 | detail 指明两侧值（漂移） |
| `nats` | 读取 NATS 服务端 `INFO` 问候 | detail 含 `nats is unreachable` 或 `no NATS server behind it` |

**探针结果只在状态变化时记一条日志**（`ops.health.changed`：转坏 `ERROR`、转好 `INFO`，
含 `healthy`/`previous_healthy`/`detail`）。readiness 会被持续轮询，逐次记录会把读路径变成日志写放大，
而运维要回答的问题只是"什么时候开始不健康的"。

> **为什么 NATS 探针不是裸 TCP connect**：容器运行时（Docker Desktop）的端口转发在容器停止后**仍然接受
> 连接**，TCP connect 会成功。停机演练实测过这一点，因此探针改为读取服务端问候语；回归用例见
> `tests/test_messaging.py::test_the_probe_does_not_trust_an_open_port`。

## 4. 备份

```powershell
# 运维入口：在数据库容器内执行 pg_dump，并把结果写进结构化日志（RQ-B-4-99 验收 4）
uv run python -m autograder_b4.ops.backup --container b4-postgres-1
# 指定输出路径（默认 backups/b4-<UTC 时间戳>.dump）
uv run python -m autograder_b4.ops.backup --container main-postgres-1 --destination backups/b4-20260919.dump
```

- 备份范围仅 `b4`；**不备份 B2/B3 数据**（`--schema` 只接受 `b4`，传其它值直接拒绝）。
- 目标库取自 `B4_ADMIN_DATABASE_URL` 的连接目标本身，不再读第二个可能不一致的变量；库名/角色不上日志。
- `pg_dump` 在**容器内**运行并通过容器本地 socket 连接，**命令行上没有任何口令**，也没有 shell 字符串拼接。
- 归档写完后回读前 5 字节校验 `PGDMP` 魔数：**失败即删除半成品**，不让一个"看起来像备份"的文件留在磁盘上。
- 记录字段：`schema`/`database`/`container`/`dump`（仅文件名，不带路径）/`bytes_written`/`duration_ms`；
  失败记 `ops.backup.failed`（含 `exit_code`/`reason`）。

等价的纯手工命令（容器外的 `pg_dump` 不可用时的原始形态）：

```powershell
docker compose exec -T postgres pg_dump -U autograder_platform -d autograder --schema=b4 -Fc -f /tmp/b4_<日期>.dump
docker compose cp postgres:/tmp/b4_<日期>.dump ./backups/
```

- 备份文件含 PII，不提交仓库（`backups/` 已在 `.gitignore` 范围内）。

## 5. 恢复（演练流程）

```powershell
docker compose cp ./backups/b4_<日期>.dump postgres:/tmp/b4_<日期>.dump
docker compose exec -T postgres pg_restore -U autograder_platform -d autograder --schema=b4 --clean --if-exists /tmp/b4_<日期>.dump
```

演练步骤（`RQ-B-4-10` 验收 4）：备份 → 删除 `b4` 某表部分行 → 恢复 → 校验数据回到备份点 → 记录命令与结果。

**已执行结果**（2026-09-16）：`b4.users` 中两行探针数据删除后恢复，`display_name` 原样回来，
`alembic_version_b4` 仍为 `20260915_0002`。完整命令与输出见
`../phases/active/phase-01-platform-implementation.md`。

## 6. 角色矩阵（现状）

| 角色 | 可访问 | 权限 |
|---|---|---|
| `autograder_b4` | `b4` | `USAGE`+`CREATE` on schema；表 DML；**无** `b2`/`b3` 任何权限；非超级用户 |
| `autograder_b2` / `autograder_b3` | 各自 schema | 由 B2/B3 供给（不属本模块） |
| `autograder_platform` | 全部 | 管理员：仅供给/迁移编排/备份/恢复使用 |
| 应用 | — | **禁止**超级用户；启动时校验 `rolsuper`/`rolcreatedb`/`rolcreaterole`/`rolbypassrls`/`pg_read_all_data`，命中任一即拒绝启动 |

## 7. 待办 / 缺口

- `../../MAIN/infra/postgres/init.sql` 仍只建 schema、不含角色供给；B4 的供给脚本是**独立入口**，
  不需要修改 MAIN 的文件（也不修改）。是否把角色供给并入 MAIN 的初始化脚本属 MAIN 决策。
- 备份保留策略与自动化（定时备份、异地副本）未定义——属部署阶段。
- 恢复（`pg_restore`）仍是**人工手册流程**（§5）：它是对在线实例的破坏性操作，不放在命令的便捷参数后面；
  `ops.backup` 只负责"产生备份并留下记录"。
