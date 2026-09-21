# B3 测试策略（Strategy）

## 测试分层与各自目标

| 层次 | 目标 | 覆盖的核心风险 | 当前实现 |
|---|---|---|---|
| 静态 | 边界与规则的机械断言（不靠审阅） | 受保护词汇进入公开模块、shell/子进程执行、跨模块 import、旧编号复活、命名漂移 | `test_dto_boundary.py`、`test_hygiene.py`（61 例，逐文件） |
| 单元 | 校验规则、adapter manifest、编译确定性、分桶、脱敏 | 路径/注入防护；摘要漂移；日志泄漏 | `test_adapters.py`、`test_logging.py` |
| 契约 | 公开响应不含受保护字段；package/event 通过 MAIN schema；Problem Details 形状 | **最高危**：公开/内部泄漏；B2↔B3 package 漂移 | `test_questions_api.py`、`test_events.py`、`test_adapters.py`（`jsonschema` 实时加载 MAIN 契约文件，不手抄） |
| 集成 | 真实 PostgreSQL 迁移 + 落库 + 事件消费 + 统计聚合 + 权限 | 迁移可空库执行；去重；聚合正确；越权读取 | `test_versions.py`、`test_statistics.py`、`test_migrations.py` |
| 安全 | 受保护内容权限、图片越权、路径穿越、注入、JWT/作用域、日志脱敏 | 学术诚信；隐私边界；认证 | `test_migrations.py`（权限）、`test_auth.py`（真实 RS256）、`test_questions_api.py`（403/响应头） |
| 资源 | 统计在 10 万事件量级可用（分页 + 预聚合） | 可用性退化 | **未做压力实测**：结构上具备预聚合表 + `occurred_at` 索引 + keyset 分页，但**没有真实数据量基准**，不声称已满足 |

**关键原则**：测试按**风险**建，不按文件机械建。最高优先级是"公开响应不含参考答案/隐藏用例"的自动化断言（`MAIN/docs/workstreams/B3.md` §5）。

## 真实基础设施要求

- **PostgreSQL 不得用 SQLite 代替**：迁移、schema 隔离、触发器、行级权限都在真实 PG 上验证。会话夹具每次运行都会 `DROP SCHEMA b3` 后 `alembic upgrade head`，因此"空库从零迁移"是每次都在被验证的事实。
- **三个真实角色**：应用行为分别通过最小权限角色（`autograder_b3_app`，公开读 + 统计消费）与内部角色（`autograder_b3_internal`，发布/编译）执行；owner 只用于迁移与测试夹具的 TRUNCATE。
- **mock 只允许隔离不可控外部服务**（NATS、B4 JWKS 端点）：
  - JWKS：测试**本地生成 RSA 密钥对并真实签名**，只替换"抓取 JWKS 文档"这一步（`JwksTokenVerifier(fetch_jwks=...)`）；验签、过期、iss/aud、kid、算法全部走生产代码。
  - NATS：用内存 publisher 与假消息对象，但 `handle_message`（ack/不 ack 决策）是生产代码。
- 数据库、ORM、Alembic、FastAPI 路由、序列化器一律真实，无替身。

## 门禁命令（本环境已实测）

```powershell
cd B3
uv run ruff check .                            # All checks passed!
uv run pytest                                  # 393 passed（Phase 10 实测）
uv run pytest --cov=autograder_b3              # 覆盖率按当前分支实测，不写死历史值
uv run alembic upgrade head                    # 20260915_0004 (head)
```

跨模块门禁（在 MAIN 侧执行，校验契约摘要、文档治理、迁移 head 与端口一致性）：

```powershell
cd ../MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

## 测试基础设施

| 组件 | 位置 | 说明 |
|---|---|---|
| 会话夹具（迁移） | `tests/conftest.py: migrated_schema` | drop `b3` → `alembic upgrade head`；不可达时 skip 并给出启动命令 |
| 双角色数据库 | `tests/conftest.py: settings/database` | `B3_TEST_APP_URL` / `B3_TEST_INTERNAL_URL` / `B3_TEST_OWNER_URL` 可覆盖，默认 55434 |
| 身份 | `tests/conftest.py: tokens/auth_headers` | 教师（两个课程，用于跨课程 403）、学生、助教 |
| 真实示例题目 | `tests/examples.py` | 三型 adapter 的真实 manifest；fixture 摘要在测试期由真实字节算出 |
| 载荷构造器 | `tests/conftest.py: draft_payload/event_envelope` | 统一构造写请求与去标识化事件 |
| 契约加载 | `tests/conftest.py: contract_json` | 直接从 `../MAIN/contracts/**` 读取权威 schema |

## 门禁通过判据（当前状态）

1. `ruff check .` 零告警 —— **满足**。
2. 全部测试通过，且存在"公开响应不含受保护字段"的自动化断言 —— **满足**（断言键缺失 + 响应原文子串搜索）。
3. `b3` 迁移可在空库从零执行；head 与 `module.yaml` 一致 —— **满足**（每次测试运行 + MAIN `checks.py`）。
4. 全仓搜索无 `RQ-B-3-70`、旧 scoring/evidence/report 路由与模型 —— **满足**（`test_hygiene.py` + MAIN `checks.py`；过程中曾被 MAIN 门禁拦下两次，见 Phase 01 记录）。
5. 无 mock 残留（mock 仅限 NATS/B4 JWKS）—— **满足**。

## 尚未覆盖（如实，不得写成已通过）

| 未覆盖项 | 原因 | 影响 |
|---|---|---|
| NATS JetStream 真实 durable consumer / DLQ 行为 | 本环境无 NATS 服务（B4 运维） | 消费语义已测；broker 重投与 DLQ 路由未验证 |
| B4 真实 JWKS 端点与真实签发的 token | 需 B4 服务 | 验签逻辑真实，端点形状按契约实现 |
| B2 runner 实际执行（端到端证据链） | 跨模块，属 B2 的 `RQ-B-2-50` | acceptance 中 3 项标注 ⚠ B2 联调 |
| 10 万事件量级性能基准 | 未构造数据集 | 不声称满足 `RQ-B-3-30` 的性能措辞 |
| Linux 冷启动验收 | 属 MAIN 的 Linux 流程 | B3 不依赖沙箱，但整机冷启动未执行 |
