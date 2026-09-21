# B4 测试策略

## 分层与目标

| 层 | 目标 | 覆盖风险 |
|---|---|---|
| 单元 | 纯逻辑：状态机迁移、作用域判定、脱敏规则、去重键 | 逻辑分支错误、状态机非法迁移 |
| 契约 | OpenAPI/AsyncAPI/JSON Schema 与实现一致 | 字段漂移、错误码语义、事件 subject |
| 集成 | 真实 PostgreSQL + NATS：迁移、最小权限角色、outbox/inbox、DLQ | 跨 schema 越权、at-least-once 重复、死信 |
| 端到端 | 登录→名单导入→确认→事件→B2/B3 本地验签 | 全链路能力（业务阶段完成后执行） |
| 安全 | 令牌篡改、越权 403、公式注入、日志脱敏、密码哈希 | 认证/授权绕过、PII 泄漏、注入 |
| 资源 | 内存/CPU/磁盘账本（PG ≤96MB、NATS ≤32MB、JetStream ≤0.25GB） | 账本超限 |

## 必须覆盖的风险（不是按文件机械建测试）

- 认证与授权：token 篡改被拒、过期被拒、越权 `403`（改 `course_id`/`section_id`/`user_id` 越权）、未认证 `401`。
- 密码安全：Argon2id/bcrypt 哈希、无明文/可逆、弱哈希被拒。
- 名单导入：确认前零写入、幂等确认、不可映射报错、被拒行不静默丢弃、PDF/XLSX 各一次真实导入。
- 数据边界：跨 schema 访问被拒、超级用户被禁、跨 schema FK 被拒。
- 并发：编辑冲突提示（`updated_at`/版本号），不静默覆盖。
- 注入与脱敏：CSV/XLSX 公式注入转义、日志不含明文密码/JWT/cookie/PII。
- 事件：重复消费去重（`event_id`）、NATS 故障不阻塞同步路径、outbox 不丢事件、DLQ 可查可重放。
- 注册表：与 `module.yaml`/descriptor 三方一致，漂移失败。

## 真实基础设施要求

- **PostgreSQL 不得用 SQLite 代替**（`RQ-B-4-10` 的最小权限/跨 schema 拒绝测试只能在真实 PostgreSQL 上成立）。
- NATS 集成用真实 JetStream（或 `nats-server -js` 测试实例）。
- mock 只允许隔离不可控外部服务（如外部邮件），**禁止** mock 掉 PostgreSQL/NATS 后宣称集成通过。

## 门禁命令（可复制）

```powershell
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run pytest --cov=autograder_b4 --cov-report=term-missing
```

## 执行状态

全部门禁命令已在真实环境执行通过；用例 ↔ 测试文件对照见 `cases.md`，证据见
`../phases/active/phase-01-platform-implementation.md`。

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest` | `209 passed`（真实 PostgreSQL 17 + 真实 NATS 2.10 JetStream） |
| `uv run alembic upgrade head` | 空库从零成功，head `20260915_0002` |
| `uv run python -m autograder_b4.ops.table_standards` | 无违规 |
| `uv run python -m autograder_b4.ops.outage_drill` | 4/4 PASS |

## 几条来自实践的约定

- **集成测试不在依赖缺失时静默跳过**：连不上 PostgreSQL 或 NATS 会以明确信息失败——被跳过的集成套件
  与通过的套件在报告上无法区分。
- **检查器必须被证明能失败**：表结构规范检查、注册表一致性校验都配了"故意违规"的用例
  （`tests/test_schema_standard.py`、`tests/test_registry.py`），否则无法区分"没有违规"与"没有检查"。
- **端到端要有一次真实 host 装载**：`tests/test_host_integration.py` 导入 MAIN 自己的
  `autograder_host.main.create_app()`，经 entry point 装载 B4 并通过 HTTP 访问——不是用测试替身模拟 host。
- **外部行为差异要用真实停机演练覆盖**：NATS 探针的 bug（容器停止后端口仍被转发，裸 TCP connect 报
  健康）只有在真的停掉 broker 时才暴露，因此既保留自动化用例，也保留 `ops/outage_drill.py` 演练。

