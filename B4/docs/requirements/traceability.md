# B4 需求追踪表

需求权威：`../../MAIN/docs/requirements/rq-catalog.md` 第 5 节。B4 共 **6 条**：`RQ-B-4-10` / `RQ-B-4-20` / `RQ-B-4-30` / `RQ-B-4-40` / `RQ-B-4-50` / `RQ-B-4-99`，**一条不漏（含 `RQ-B-4-99`）**。

状态以**真实环境证据**为准（见 `../phases/active/phase-01-platform-implementation.md`）：PostgreSQL 17 与
NATS 2.10 JetStream 实机运行，应用连接最小权限角色。

| RQ | 需求摘要 | 本模块实现方式 | 关联工作包 | 关联测试 | 状态 | 证据位置 |
|---|---|---|---|---|---|---|
| `RQ-B-4-10` | 维护数据库正常运行 | PostgreSQL 实例可用、最小权限角色/schema 供给（`ops/provision.py`）、真实 readiness 探针（postgres/migration/nats）、备份入口 + 运行手册 + 恢复演练、禁止超级用户 | `WP-B4-A` | `T-DB-01`…`T-DB-05`、`T-DB-10` | `已完成` | `../phases/active/phase-01-platform-implementation.md`、`../runbooks/database-operations.md` |
| `RQ-B-4-20` | 按需维护消息中间件 | JetStream 拓扑供给：**共享 `DOMAIN_EVENTS`**（契约里除 B4 自己那个 subject 外的全部地址）+ `ROSTER_IMPORT` + durable consumer + `B4_DLQ`（advisory）+ `dlq_messages` 可查可重放；outbox 与业务同事务；同步路径不依赖 broker；三条 stream 配额合计 = 账本行 0.25 GB，并有**实测**占用命令 | `WP-B4-C` | `T-MSG-01`…`T-MSG-07` | `已完成` | `../runbooks/messaging-operations.md`、`../api/events.md` |
| `RQ-B-4-30` | 定义数据库表结构（规范 + b4 平台表） | 规范成文并**自动校验**（真实 catalog + 全仓迁移 DDL 文本两种模式）；15 张平台表迁移；head 与 `module.yaml` 双通道校验；跨 schema FK 与 SELECT/CREATE 实测被拒 | `WP-B4-A` | `T-DB-05`…`T-DB-07`、`T-DB-09` | `已完成`（规范对 B2/B3 的执法发现 62 条违规，由各模块修复） | `../database/table-standards.md`、`../database/schema.md`、`../database/migrations.md` |
| `RQ-B-4-40` | 定义管理模块间 API 调用接口（运行期注册表） | 运行期注册表 `GET /api/v1/modules`（模块名/契约版本/provides/requires/routes/publishes/subscribes/schema/migration_head/health）；三方一致性校验在**启动时**执行，漂移即失败；契约摘要镜像 MAIN 算法；模块名按契约 pattern（`^B[0-9]{1,3}$`）校验，同名 entry point 重复即失败 | `WP-B4-B` | `T-REG-01`…`T-REG-05` | `已完成` | `../architecture/components.md`、`../api/contract.md` |
| `RQ-B-4-50` | 用户管理 API | Ed25519 短期 JWT + JWKS；刷新凭据仅 HttpOnly/Secure/SameSite cookie（轮换 + 重用检测）；Argon2id；四类角色 + 课程作用域全部服务端判定；用户/课程/课序/选课/名单导入/审计端点；敏感操作与拒绝入审计；登录失败滑动窗口限流（即"速率限制/锁定策略"，见下注） | `WP-B4-B` | `T-AUTH-01`…`T-AUTH-07`、`T-ROSTER-01`…`T-ROSTER-06`、`T-ENR-01` | `已完成` | `../api/contract.md`、`../architecture/boundaries.md` |
| `RQ-B-4-99` | 生成并记录运行日志 | 结构化 JSON 日志 + 强制脱敏（写入口即脱敏，调用点无法绕过）；`producer=B4`、`request_id`/`correlation_id`/`user_id`；认证失败/权限拒绝/DB/NATS 异常有明确级别；**迁移执行、备份、健康检查结果各有发射点** | `WP-B4-C` | `T-LOG-01`…`T-LOG-05` | `已完成` | `src/autograder_b4/logging.py`、`tests/test_logging.py`、`tests/test_database.py`、`tests/test_health.py` |

> `RQ-B-4-50` 验收 7 的"速率限制**/锁定**策略"由**限流**一半满足：`api/auth.py` 按 principal 的
> SHA-256 做滑动窗口（窗口 `B4_LOGIN_FAILURE_WINDOW_SECONDS`=300s、阈值 `B4_LOGIN_FAILURE_LIMIT`=5），
> 超限即 `429` 且**先提交限流台账与审计再抛错**（`tests/test_auth.py::test_failed_logins_are_throttled`、
> `::test_a_throttled_attempt_is_recorded`）。它本身就是**有界、限时、按账号**的策略；因此不存在第二套
> "锁定"实现：永久锁定会把拒绝服务变成攻击面（攻击者用错误口令锁死任意账号，且本系统没有自助解锁
> 通道）。原先的 `B4_LOGIN_LOCKOUT_SECONDS` 是从未有任何读取点的死配置，已删除（见下"本轮清理"）。

## 本轮清理（死配置，2026-09-19）

| 配置 | 判定 | 依据 |
|---|---|---|
| `B4_REGISTRY_ENFORCE` | **删除**（行为必然而非可配） | 全仓无读取点；注册表漂移必须让 host 启动失败（`RQ-B-4-40` AC2/AC3，`plugin.py` 装载时 `validate_registry` 直接抛错）。给它一个 "off" 开关等于给一条隐藏旁路，与"禁止隐藏开关"冲突 |
| `B4_ROSTER_PREVIEW_LIMIT` | **删除**（已被别处实现取代） | 全仓无读取点；预览返回**全部**行（`accepted_rows`/`rejected_rows` 均不截断），契约 `RosterImportPreview` 没有 limit 字段，且"被拒行不静默丢弃"（`RQ-B-1-10`）本就禁止截断 |
| `B4_LOGIN_LOCKOUT_SECONDS` | **删除**（需求由限流满足） | 全仓无读取点；见上注——滑动窗口限流即 AC7 的"速率限制/锁定策略"，另加永久锁定会引入 DoS 与"无自助解锁"问题 |

## 未完成的残余项（明确记录，不宣称完成）

| 项 | 所属 RQ | 状态 | 说明 |
|---|---|---|---|
| JetStream 占用**长时间**采样 | `RQ-B-4-20` | `待实现` | 三条 stream 的配额合计已等于账本行 268435456 B，断言会因超限或某 stream 为 0 而失败；占用可由 `ops.messaging metrics` 实测（一次性实测见 `../phases/active/phase-03-ops-lint-and-ledger.md`）；**保留策略在真实流量下的长期淘汰行为**未采样，属平台级资源门禁（`resource-budget.md` §5） |
| B2/B3 迁移文本的 62 条违规 | `RQ-B-4-30` | `待实现`（非 B4 文件） | 新 linter 的实测结果：B2 26 条、B3 36 条（明细见 phase-03）。结论是"**迁移 DDL 文本**不符合规范"，不是"对方的库不符合规范"（B4 只读 `b4` catalog，对真实 `b4` schema 为 0 违规）。B4 只报告，不修改其他模块的迁移 |
| 共享域事件流的**消费**端 | `RQ-B-4-20` | `待实现`（非 B4 代码） | B4 已供给 `DOMAIN_EVENTS` 并实测 15 条真实事件投递成功（`stored=9685 bytes messages=15`）。但 `b3.event_inbox` 仍为 0：那 15 条是 `submission.accepted`，而 B3 消费 `evaluation.completed`，且平台上还没有任何模块的 durable consumer 绑到 `DOMAIN_EVENTS`。需要 B2 的评测 worker（`B2_B3_BASE_URL`/`B2_B3_AUTHORIZATION`/`B2_WORKSPACE_ROOT` + Linux nsjail）与 B3 的 `consumer_cli` 常驻，属各自模块的运行期 |
| `MAIN/contracts/openapi/b4.yaml` 的模块名 pattern 未随 CHG-0009 更新 | — | `待实现`（MAIN 侧） | `manifest.schema.json` 已改为 `^B[0-9]{1,3}$`，但 `b4.yaml` 的 `getModuleHealth` 路径参数与 `RegisteredModule.module` 仍是 `^B[1-4]$`；第五模块能通过 manifest 校验却违反注册表响应声明。B4 不修改契约（见 `../api/contract.md` 复核发现 8） |
| 初始管理员引导 | `RQ-B-4-50` | `待实现` | 当前由运维命令 + 测试夹具写入首个管理员，尚无独立的引导脚本与手册 |
| 契约缺口：课程级角色授予 operation | `RQ-B-4-50` | `待实现` | 契约无此 operation，B4 不新增路径，改由 `ops/roles.py` 运维命令承担；补齐需 MAIN 走 P1 变更（见 `../api/contract.md` 复核发现 4） |
| 契约缺口：导入解析说明字段 | `RQ-B-4-50` | `待实现` | `RosterImportPreview` 为 `additionalProperties: false`，人类可读的"为什么需要人工映射"无字段可放；当前以结构化字段表达（见 `../api/contract.md` 复核发现 3） |

> 状态词仅使用 `骨架` / `待实现` / `已完成` / `阻塞`；本表无 `阻塞` 项。
