# Phase 01 题库、不可变版本、三类测试框架与去标识化统计（Active）

## 目标与范围

把 Phase 00 的 21 行插件骨架落成**可运行实现**：题库与不可变题目版本（`WP-B3-A`）、三类测试框架的 evaluation package 编译（`WP-B3-B`）、去标识化统计与结构化日志（`WP-B3-C`），并补齐工具链（`pytest-cov`）。范围仍严格遵守 `docs/requirements/scope.md` 的非目标：不执行学生代码、不算分、不碰 `b2`/`b4`。

本阶段开始时的工作单：`WP-B3-A/B/C`（见 [workpackages.md](../../collaboration/workpackages.md)）。

## 第一步：B3 内部接口冻结（本阶段先做的串行点）

`workpackages.md` 把三项列为"待冻结"，冻结结果写进 B3 自己的 ADR 与清单，**未改 `MAIN/contracts/**`**：

| 冻结项 | 决策文档 | 内容 |
|---|---|---|
| public / internal DTO 边界 | `ADR-0004` + `collaboration/frozen-interfaces.md` §1 | 公开 DTO 只在 `dto/public.py`、内部 DTO 只在 `dto/internal.py`；`dto/__init__.py` 零导出；受保护词汇表归内部模块；公开响应唯一出口是 `serializers.serialize_public` |
| package 编译器函数签名 | `ADR-0005` + §2 | 单一入口 `compile_evaluation_package(...)`；`created_at` 必传且必须等于版本 `published_at`；`package_digest = sha256(canonical_json(去掉 package_digest))` |
| adapter manifest → package 转换接口 | `ADR-0005` + §3 | 统一校验入口 `validate_adapter_manifest(...)`；`AdapterCompiler` 协议 + 注册表（扩展点）；三型共用同一 package 结构 |

顺带把 `ADR-0001/0002/0003` 从 `提议` 转为 `接受`（三条决策均未使用 MAIN 未授权的扩展）。

## 实现内容（文件级）

| 层 | 文件 | 作用 |
|---|---|---|
| DTO | `dto/public.py`、`dto/internal.py`、`dto/wire.py`、`dto/__init__.py` | 公开/内部模型物理分离；wire 规范化（uuid → str、时间 → 毫秒精度 UTC） |
| 编译 | `compile/canonical.py`、`policy.py`、`validation.py`、`registry.py`、`adapters.py`、`manifest.py`、`compiler.py` | 规范化摘要、沙箱策略常量与范围校验、路径/标识符/参数防注入、adapter 注册表、三型 adapter、统一校验入口、单编译入口 |
| 领域 | `domain/content.py`、`questions.py`、`versions.py`、`packages.py`、`assets.py`、`assignments.py`、`catalog.py`、`statistics.py`、`outbox.py` | 内容摘要、草稿写、发布/归档/删除、package 重编译校验、上传、assignment、公开读、统计查询、outbox 投递 |
| 事件 | `events/envelope.py`、`whitelist.py`、`consumer.py` | envelope 构建与解析、去标识化白名单、durable consumer（含 NATS 驱动） |
| 统计 | `stats/buckets.py`、`aggregator.py`、`service.py` | 分桶、整数累加器、增量/重建/窗口三种路径 |
| 基础设施 | `infra/db.py`、`models.py`、`repositories.py`、`storage.py`、`publisher.py` | 双引擎（双角色）、15 张表的 ORM、仓储、内容寻址与上传校验、发布器（NATS + 内存替身） |
| API | `api/router.py`、`serializers.py`、`deps.py`、`pagination.py`、`app.py` | 契约端点、白名单序列化、依赖容器与请求上下文、keyset 分页、Problem Details 处理器 |
| 其他 | `auth.py`、`errors.py`、`logging.py`、`plugin.py`、`consumer_cli.py` | RS256/JWKS 验签与作用域策略、RFC 9457 错误、脱敏 JSON 日志、插件装配、独立消费者入口 |

数据库：新迁移 `alembic/versions/20260915_0002_b3_question_bank.py`（15 张业务表 + 5 个触发器 + 最小权限授权），`module.yaml: database.migration_head` 同步为 `20260915_0002`。

## 实际执行的命令与结果（原始）

环境：Windows，`uv 0.10.2`，Python 3.12（uv 管理的 3.12.12），Docker 29.8.0；本机 `5432` 被系统保留策略拒绝，MAIN 的平台 PostgreSQL 占用 `55432`，B2 占用 `55433`。

| # | 命令 | 结果 |
|---|---|---|
| 1 | `uv sync --all-groups` | `Resolved 28 packages` / `Audited 28 packages`（exit 0） |
| 2 | `uv add --dev "pytest-cov>=6,<7"` | `+ coverage==7.16.1`、`+ pytest-cov==6.3.0`（exit 0）——**补上了 Phase 00 记录的 `pytest-cov` 缺口** |
| 3 | `uv add --dev "jsonschema>=4.23,<5"` | `+ jsonschema==4.26.0` 等 5 个包（契约测试直接校验 MAIN schema，不手抄字段表） |
| 4 | `uv add "cryptography>=43,<47"` | `+ cryptography==46.0.7`（RS256/JWKS 真实验签所需；`MAIN/contracts/openapi/b4.yaml` 要求本地验签） |
| 5 | `uv add --dev "httpx>=0.28,<1"` | `+ httpx==0.28.1`（FastAPI `TestClient` 的真实 HTTP 断言） |
| 6 | `docker compose up -d postgres`（B3 自己 compose，宿主 55434） | `Container b3-postgres-1 Started`，`0.0.0.0:55434->5432/tcp`（5432 直连被本机拒绝，故改端口） |
| 7 | `uv run alembic upgrade head`（`DATABASE_URL` 指向 55434 owner） | `Running upgrade -> 20260915_0001`、`Running upgrade 20260915_0001 -> 20260915_0002`（exit 0） |
| 8 | `uv run alembic heads` | `20260915_0002 (head)` |
| 9 | `psql`（容器内）核对 | `b3` 下 16 张表（15 业务 + `alembic_version_b3`）、5 个触发器、3 个角色（`autograder_b3` / `_app` / `_internal`） |
| 10 | `uv run ruff check .` | `All checks passed!`（exit 0） |
| 11 | `uv run pytest -q --cov=autograder_b3 --cov-report=term` | **`282 passed`**，`TOTAL 3182 stmts / 320 miss / 90%`（exit 0） |
| 12 | `cd MAIN && .\.venv\Scripts\python.exe -m autograder_host.checks` | `contracts, manifests and requirements: ok (de8f68…)`、`module documentation governance and links: ok`、`migration heads and local ports agree with compose: ok`（exit 0） |
| 13 | `psql -c "DROP SCHEMA IF EXISTS b3 CASCADE"` + `CREATE SCHEMA b3` → `uv run alembic upgrade head` | `drop cascades to 18 other objects`（16 表 + 2 函数）→ `Running upgrade -> 20260915_0001`、`-> 20260915_0002`（exit 0）——**空库从零迁移的显式验证**（除测试夹具每次运行的隐式验证之外） |

> 第 12 项的摘要与 `contracts.lock` 的 `digest` 一致，可证本阶段**未改动 `MAIN/contracts/**`**。

## 过程中被门禁拦下并修正的两件事（如实记录）

1. **MAIN 的"废弃 B3 评分产物"门禁**在首次执行 `checks.py` 时失败两次：
   - `B3/alembic/versions/20260915_0002_b3_question_bank.py: deprecated B3 scoring artifact resurfaced (evaluation_runs)` —— 我在迁移 docstring 里写了"不存在旧 `evaluation_runs`"，而检查器对**实现面**（src/alembic/module.yaml）要求该词**零出现**。改为不含该词的中性表述。
   - 随后同一门禁在 `alembic/versions/__pycache__/*.pyc` 上再次失败（Phase 00 的旧字节码 + 本次新编译的字节码含有该词）。清除 `src/`、`alembic/`、`tests/` 下的 `__pycache__` 后通过。这正是检查器 docstring 里记录过的历史事故，本次复现并修正。
2. **MAIN 的"本地端口一致性"门禁**要求各模块 `.env.example` 与 `alembic.ini` 的 PostgreSQL/NATS 宿主端口与 `MAIN/compose.yaml` 一致（55432 / 54222）。我此前把 B3 的值写成 55434，被判为配置漂移。修正为指向平台端口，并在文件中说明：B3 自己的 `compose.yaml`（55434）是**模块本地库**，测试套件用它以免 drop 平台数据库的 `b3` schema。

## 真实测试结果（按文件）

| 测试文件 | 例数 | 覆盖的风险 |
|---|---|---|
| `tests/test_adapters.py` | 40 | 三型 adapter、路径穿越（10 例）、注入参数逐字保留、callable 入口约束、扩展点、确定性摘要、MAIN schema 实时校验 |
| `tests/test_auth.py` | 23 | 真实 RS256 验签（篡改/过期/错误 iss-aud/未知 kid/非 RS256/畸形）、作用域 401/403、统计角色门槛 |
| `tests/test_consumer_cli.py` | 2 | 重建入口、缺 NATS 时明确报错 |
| `tests/test_dto_boundary.py` | 8 | 公开/内部分离、零导出、无受保护词汇、未知字段拒绝 |
| `tests/test_events.py` | 13 | envelope 契约、payload 去标识化、outbox 投递与失败重试、ack/不 ack 语义 |
| `tests/test_hygiene.py` | 61 | 无 shell/子进程/动态执行/跨模块 import、命名规范、无旧编号、`taskone` 非来源 |
| `tests/test_logging.py` | 23 | `producer=B3`、13 类受保护键脱敏、异常只记类型、失败不静默 |
| `tests/test_migrations.py` | 17 | 空库迁移、head 与 `module.yaml` 一致、回滚往返、无跨 schema FK、权限拒绝、触发器存在、命名规范 |
| `tests/test_plugin.py` | 8 | descriptor 与 `module.yaml` 一致、全部路由落在声明前缀内、health 讲真话 |
| `tests/test_questions_api.py` | 34 | **公开响应字段缺失断言**、图片 403 与安全响应头、跨课程 403、草案/发布/冲突、内部 package 端点认证 |
| `tests/test_statistics.py` | 29 | 去重（`event_id` + `evaluation_run_id`）、重放（正序/倒序）、窗口查询、越界字段拒收、口径正确性 |
| `tests/test_versions.py` | 24 | 版本不可变（应用层 + 触发器）、发布完整性、事件载荷、assignment 约束、三型示例发布 |

会话夹具每次运行都会 `DROP SCHEMA b3` 并 `alembic upgrade head`，因此"迁移可从零执行"是**每次测试运行都在验证**的事实，而不是一次性结论。

## 未完成 / 未验证项（不得当作已完成）

| 项 | 状态 | 说明 |
|---|---|---|
| B2 runner 实际执行（端到端证据链） | **未验证** | 属 B2 的 `RQ-B-2-50`；B3 侧提供 package 与摘要，联调需跨模块环境。acceptance 中标注为 ⚠ B2 联调（3-40-3 / 3-50-3 / 3-60-3） |
| NATS JetStream 真实消费（durable consumer、DLQ） | **未验证** | 本环境无 NATS 服务；`NatsEvaluationConsumer.handle_message` 的 ack/不 ack 语义与拒收路径已测，但 broker 行为需 B4 提供后联调 |
| B4 真实 JWKS 端点 | **未验证** | 测试用本地生成的 RSA 密钥对 + 注入的 JWKS 抓取（允许的替换）；`b4.yaml` 的端点形状已按契约实现 |
| file adapter 的"文本差异"比较粒度 | **契约受限** | 冻结 schema 不可表达 → `CHG-B3-0001` |
| 图片/fixture 端点与创建题目的 `course_id` | **未成文** | 已实现且落在既有前缀内 → `CHG-B3-0002` |
| "独立作答人数"与平均分 | **契约受限** | 事件禁止 `student_id`，DTO 无字段 → `CHG-B3-0003` |
| Linux 冷启动验收（nsjail / cgroup v2 相关） | **未执行** | 属 MAIN 的 Linux 验收流程；B3 不依赖沙箱，但整机冷启动未在本阶段执行 |

## 文档影响检查

- 已同步：`docs/README.md`（状态表 + 文档索引）、`requirements/traceability.md`、`requirements/acceptance.md`、`testing/cases.md`、`testing/strategy.md`、`database/schema.md`、`database/migrations.md`、`api/contract.md`、`api/events.md`、`architecture/*`、`runbooks/local-development.md`、`collaboration/workpackages.md`、`collaboration/interfaces.md`、`adr/README.md`、根 `README.md`、`AGENTS.md`（骨架期表述）。
- 新增文档：`adr/ADR-0004`、`adr/ADR-0005`、`collaboration/frozen-interfaces.md`、`contracts/changes/CHG-B3-0001..0003`。
- 本阶段**不归档**：B2/NATS/B4 联调与 MAIN 对三条 `CHG-*` 的裁决尚未完成，归档门禁未满足。
