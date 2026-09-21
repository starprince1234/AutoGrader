# Phase 01 平台能力实现（当前阶段）

## 目标与范围

把 `RQ-B-4-10` / `RQ-B-4-20` / `RQ-B-4-30` / `RQ-B-4-40` / `RQ-B-4-50` / `RQ-B-4-99` 从
`骨架`/`待实现` 推进到**有真实环境证据的实现**：数据库与最小权限、平台表迁移、认证与
JWKS、角色与课程作用域授权、课程/课序/选课、名单导入管道、NATS JetStream 与 outbox、
运行期模块注册表、结构化脱敏日志。

范围外（本阶段不做，保持 active）：B2/B3 的表与业务、学生代码执行、评分。

## 输入与决策

- 输入：`../../MAIN/contracts/**`（设计期唯一权威）、`../../MAIN/docs/adr/ADR-0003`、本模块
  `../adr/ADR-0001..0003`、`../database/table-standards.md`。
- 决策落点：
  - **角色与课程作用域**：`b4.roles` 同时表达平台级角色（`course_id IS NULL`）与课程级授予
    （`course_id` 非空）；`admin` 只能平台级（DB CHECK 强制）。`createCourse` 为创建者写入课程级
    `teacher` 授予，使其立刻拥有自己所建课程的作用域。
  - **刷新凭据**：不透明 256 位随机串，服务端只存 SHA-256；轮换后重用旧凭据 → 撤销整个
    family（失窃检测）。
  - **名单导入 staging**：解析结果落在 `b4.roster_import_rows`（暂存），`b4.enrollments` 在确认前
    零写入。
  - **DLQ**：使用 JetStream 自身的 advisory 主题（`MAX_DELIVERIES` / `MSG_TERMINATED`），不新增
    domain subject。

## 完成条件

1. **数据库**：`b4` schema 16 张表由 Alembic 迁移建立；最小权限角色只能访问 `b4`；应用拒绝超级用户。
2. **认证**：Ed25519 短期 JWT + JWKS；刷新凭据仅 `HttpOnly`/`Secure`/`SameSite` cookie；Argon2id 存储。
3. **授权**：四类角色 + 课程作用域全部服务端判定；拒绝写审计。
4. **名单导入**：PDF/XLSX 解析→映射→校验→预览→确认→原子写入；不确定映射明确停下；被拒行不丢。
5. **消息与可观测**：stream/durable consumer/DLQ/重放；outbox 不丢事件；JSON 日志脱敏。
6. **注册表**：返回模块名/契约版本/provides/requires/routes/events/schema/migration_head/health；漂移启动失败。

## 实际变更

### 新增源码（`src/autograder_b4/`）

| 模块 | 内容 |
|---|---|
| `config.py` | 配置校验：生产环境拒绝弱口令与缺失密钥；`assert_not_superuser()` |
| `logging.py` | JSON 日志 + 强制脱敏 + `request_id`/`correlation_id` 上下文 |
| `errors.py` | RFC 9457 ProblemDetails |
| `audit.py` | 审计写入（动作词表、变更摘要、上下文默认值） |
| `authorization.py` | `Principal` 解析与三层判定（认证/角色/作用域） |
| `registry.py` | 运行期注册表、契约摘要镜像、三方一致性校验 |
| `db/tables.py`、`db/engine.py` | SQLAlchemy Core 表定义、引擎与依赖探针 |
| `security/{passwords,keys,tokens,refresh}.py` | Argon2id、Ed25519、JWT、刷新 cookie |
| `messaging/{envelope,jetstream,broker,outbox,consumer}.py` | 事件信封、拓扑供给、连接与探针、outbox 中继、inbox 去重 / DLQ / 重放 |
| `roster/{table,mapping,parsers,validation,service}.py` | 名单导入管道与状态机 |
| `api/*` | 认证、用户、课程/课序/选课、名单、审计、注册表端点 |
| `ops/*` | 角色/schema 供给、密钥生成、消息运维、角色授予、表结构规范检查、停机演练 |

### 迁移

| revision | 内容 |
|---|---|
| `20260915_0001` | 确保 `b4` schema 存在（改为存在性守卫，见下） |
| `20260915_0002` | 15 张平台表 + 索引/约束 + 应用角色授权 |

`module.yaml: database.migration_head` 更新为 `20260915_0002`；`module.yaml: routes` 补齐为全部
8 个前缀（此前只列 3 个，与 `MAIN/contracts/openapi/b4.yaml` 的路径覆盖不一致）。

## 验证记录（真实命令 + 结果）

环境：Windows 宿主 + Docker Desktop；PostgreSQL 17（`MAIN` compose，宿主端口 `55432`）、
NATS 2.10 JetStream（宿主端口 `54222`）。应用连接角色 `autograder_b4`（最小权限）。

| # | 命令 | 结果 |
|---|---|---|
| 1 | `uv run python -m autograder_b4.ops.provision` | `provisioned role autograder_b4 on schema b4: 16 steps`；`role autograder_b4 is least privilege: no b2/b3 access, no superuser attributes` |
| 2 | `uv run python -m autograder_b4.ops.generate_key` | `wrote secrets\jwt-ed25519.pem (kid=0GXDTtrewg4wYP56KFQQgYK76WunOhpETDooK_QoRNs)` |
| 3 | `uv run alembic upgrade head`（空库 `b4_migration_probe_*`，先 provision） | `Running upgrade -> 20260915_0001`、`Running upgrade 20260915_0001 -> 20260915_0002`；`version_num = 20260915_0002`；`pg_tables` 计数 **16** |
| 4 | `uv run python -m autograder_b4.ops.table_standards` | `b4 schema conforms to docs/database/table-standards.md` |
| 5 | `uv run ruff check .` | `All checks passed!` |
| 6 | `uv run pytest` | **209 passed**（真实 PG + 真实 NATS） |
| 7 | `uv run python -m autograder_b4.ops.messaging provision` | `stream:ROSTER_IMPORT: created`、`stream:B4_DLQ: created`、`consumer:ROSTER_IMPORT/B4_ROSTER_CONSUMER: created`、`consumer:B4_DLQ/B4_DLQ_CONSUMER: created` |
| 8 | `uv run python -m autograder_b4.ops.outage_drill` | 见下（4/4 PASS） |
| 9 | MAIN 治理检查：`host/src/autograder_host/checks.py` | `contracts, manifests and requirements: ok (de8f6834…)`；`module documentation governance and links: ok`；`migration heads and local ports agree with compose: ok` |
| 10 | MAIN host 真实装载（见下） | `host loaded modules: ['B4']`；`/health/ready` → `ready=True`；JWKS/K-401/公开健康端点均按预期 |

### MAIN host 真实装载（`RQ-B-4-40` 的"即插即用"）

由 MAIN 自己的装载器（`autograder_host.main.create_app()`）经 `autograder.modules` entry point 装载 B4，
再以 HTTP 客户端访问；**不是**用测试替身模拟 host：

```text
host loaded modules: ['B4']
GET /health/ready -> 200 ready = True
  B4 detail: postgres: postgres reachable; migration: migration head 20260915_0002 matches module.yaml; nats: nats is reachable at localhost:54222
GET /api/v1/auth/jwks -> 200 kid = 0GXDTtrewg4wYP56KFQQgYK76WunOhpETDooK_QoRNs
GET /api/v1/courses (no token) -> 401 Unauthorized
GET /api/v1/modules/B4/health -> 200 {'module': 'B4', 'healthy': True}
```

同一路径下另有一条**失败即拒绝启动**的实测：不注入 `DATABASE_URL` 时 host 在装配 B4 路由器阶段抛出
`ConfigurationError: DATABASE_URL is required (least-privilege application role)`，进程不启动；
`B4Module.health()` 则返回 `healthy=false, "module not initialized: …"` 供 readiness 报告。
两条都成为回归用例（`tests/test_host_integration.py`）。

### 最小权限实测（跨 schema 拒绝）

作为 `autograder_b4` 连接 `autograder` 库执行：

| 语句 | 结果 |
|---|---|
| `SELECT count(*) FROM b2.users` | `ERROR: permission denied for schema b2` |
| `CREATE TABLE b3.probe(i int)` | `ERROR: permission denied for schema b3` |
| `CREATE TABLE b4.probe(i int)` | `CREATE TABLE`（正对照） |
| `CREATE TABLE b4.cross_schema_probe(other uuid REFERENCES b2.users(user_id))` | 被拒（无 `b2` 权限） |
| `SELECT current_user, rolsuper, rolcreatedb, rolcreaterole ...` | `autograder_b4 \| f \| f \| f` |

### 备份 / 恢复演练（`RQ-B-4-10` 验收 4）

按 `../runbooks/database-operations.md` 执行，命令与结果：

```text
# 1. 造数据
INSERT INTO b4.users (...) VALUES ('1111…','backup_probe_a','Probe A'), ('2222…','backup_probe_b','Probe B');
SELECT count(*) FROM b4.users;                          -- 2

# 2. 备份（仅 b4 schema）
docker compose exec -T postgres pg_dump -U autograder_platform -d autograder --schema=b4 -Fc -f /tmp/b4_20260916.dump
docker compose cp postgres:/tmp/b4_20260916.dump ./backups/
# → ./backups/b4_20260916.dump  45989 bytes

# 3. 制造丢失
DELETE FROM b4.users WHERE username LIKE 'backup_probe_%';   -- DELETE 2
SELECT count(*) … WHERE username LIKE 'backup_probe_%';      -- 0

# 4. 恢复
docker compose cp ./backups/b4_20260916.dump postgres:/tmp/b4_20260916.dump
docker compose exec -T postgres pg_restore -U autograder_platform -d autograder --schema=b4 --clean --if-exists /tmp/b4_20260916.dump

# 5. 校验
SELECT username, display_name FROM b4.users WHERE username LIKE 'backup_probe_%';
--  backup_probe_a | Probe A
--  backup_probe_b | Probe B
SELECT version_num FROM b4.alembic_version_b4;               -- 20260915_0002
```

**结论**：数据回到备份点，迁移版本一致，演练留下真实命令与结果。

### 停机演练（`RQ-B-4-20` 验收 4）

`uv run python -m autograder_b4.ops.outage_drill`（真实停止/启动 NATS 容器）：

```text
== 1. broker up: initial health
   healthy=True  detail=postgres: postgres reachable; migration: migration head 20260915_0002 matches module.yaml; nats: nats is reachable at localhost:54222
== 2. queue an event while the broker is up        outbox pending=1
== 3. stop NATS                                    -> Container main-nats-1 Stopped
== 4. health with the broker down
   healthy=False detail=…; nats: nats is unreachable at localhost:54222 (ConnectionResetError)
== 5. synchronous path with the broker down        outbox pending=2 (事件入队成功，未丢失)
== 6. relay with the broker down                   relay refused: NoServersError; remaining=2
== 7. start NATS again                             healthy=True
== 8. relay after recovery                         published=2 remaining=0
== result
   [PASS] nats failure is reported
   [PASS] events survive the outage
   [PASS] backlog drained after recovery
   [PASS] health recovered
```

## 过程中发现并修复的缺陷（真实缺陷，非文档问题）

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| 1 | `alembic/env.py` 里 `set_config` 开了隐式事务，Alembic 视为外部事务而不提交 | 迁移静默整体回滚：命令 exit 0、库中无表 | `set_config` 后显式 `commit()` |
| 2 | `CREATE SCHEMA IF NOT EXISTS b4` 在最小权限角色下失败（PostgreSQL 先校验 database 级 `CREATE`） | 应用角色无法执行 `upgrade head` | 迁移 0001 改为存在性守卫；schema 由供给步骤创建 |
| 3 | psycopg3 + SQLAlchemy 下 `INSERT … ON CONFLICT` 的 `rowcount` 恒为 `-1` | inbox 去重把**每次**投递都判为重复（业务效果永不执行）；enrollment 写入计数为负 | 全部改用 `RETURNING` + `fetchone()` 判定 |
| 4 | 在写事务内 `raise`（登录限流、刷新重用撤销、名单全拒终态） | 限流拒绝与撤销**被回滚**：审计留白、family 未被撤销、状态未落 `rejected` | 先提交、后抛错 |
| 5 | `expected_updated_at` 比较与写入都截断到整秒 | 同一秒内的第二次写入绕过乐观并发，静默覆盖 | 统一到毫秒（`api/timestamps.py`），并集中一处实现 |
| 6 | readiness 的 NATS 探针用裸 TCP connect | Docker 端口转发在容器停止后仍接受连接 → **依赖已死却报健康** | 改为读取 NATS 服务端 `INFO` 问候（真实握手）；`tests/test_messaging.py` 加回归用例 |
| 7 | `UnsupportedDocument` 为 frozen dataclass，且路由器未捕获 | 非 PDF/XLSX 上传抛 `FrozenInstanceError` → 500 而非 422 | 改为普通异常类；服务层转换为 `422` |
| 8 | 注册表把 entry point **名字**（`b4`）当作模块名（`B4`）比对 | 启动校验必然失败 | 解析 entry point 为真实 `descriptor`，按 `descriptor.module` 比对 |
| 9 | 供给脚本用独立的 `B4_DB_NAME` 决定要授权的库 | 授权到错误的库 → 应用连接被拒；且用管理员口令覆盖了集群级角色口令 | 库名取自 admin DSN；口令显式要求应用角色口令 |
| 10 | 名单行号按"已收行数"编号 | 中间空行会导致行号与源文件错位 | 使用电子表格真实行号 |
| 11 | 审计未带 `request_id`/`correlation_id`（调用处读一个不存在的属性） | 审计无法与请求串联 | `AuditLog.record` 默认取当前请求上下文 |
| 12 | 表结构检查器用"表名去 s"推断主键列名 | `audit_records` 等表误报；检查器本身不可信 | 改为显式声明 `PRIMARY_KEY_COLUMNS`，未声明的表直接判违规 |

## 读只契约复核（`docs/api/contract.md` ↔ `MAIN/contracts/openapi/b4.yaml`）

结论：**未修改 `MAIN/contracts/**` 或 `contracts.lock`**。发现与处置见
`../api/contract.md`「复核发现」一节，摘要：

1. operation 清单一致（22 个 operation，逐条核对）。
2. `b4.yaml` 顶部声明"每个 path 都在 B4 `module.yaml: routes` 中声明"，而 `module.yaml` 当时只列
   3 个前缀 —— **B4 侧修正**为 8 个前缀。
3. `RosterImportPreview` 的描述文字说被拒行出现在 `rejected`，实际属性名是 `rejected_rows` ——
   MAIN 侧文字不一致，B4 按机器可读属性实现 `rejected_rows`，并记录待走 CHG。
4. `RosterImportPreview.additionalProperties: false` 导致"解析无法解释"这类人类可读说明无字段可放
   —— 记录为契约缺口（当前以 `status` + `detected_columns` + `proposed_mapping` 结构化表达）。
5. 契约中没有"给已有课程追加教师/助教"的 operation —— 授权模型需要它；B4 未新增路径，改用运维
   命令 `python -m autograder_b4.ops.roles grant`，并记录为需走 P1 变更的缺口。
6. `getModuleHealth` 未声明 security 与 401/403 —— 按"公开仅存活"实现（只返回 `module`/`healthy`）。

## 未完成 / 阻塞

- 无 `阻塞` 项。
- 仍为 `待实现` 的部分（不声称完成）：
  - `RQ-B-4-20` 的**配额实测**（0.25 GB 为配置值，未做长时间压测计量）；
  - ~~种子数据/初始管理员引导流程~~ → 初始管理员引导已实现（`ops/bootstrap_admin.py`，见
    [phase-02](phase-02-test-instance-isolation.md)）；种子数据由 MAIN 的
    `scripts/seed_integration.py` 承担，不属 B4；
  - 契约缺口 3/4/5 需 MAIN 走变更规程后才能消除（见上）。

## 文档影响检查

- 本阶段同步更新：`../README.md`、`../api/contract.md`、`../api/events.md`、
  `../database/schema.md`、`../database/migrations.md`、`../database/table-standards.md`、
  `../requirements/traceability.md`、`../architecture/models.md`、`../architecture/components.md`、
  `../testing/cases.md`、`../runbooks/database-operations.md`、`../runbooks/messaging-operations.md`、
  `../collaboration/interfaces.md`。
- 状态词仅使用 `骨架` / `待实现` / `已完成` / `阻塞`。
