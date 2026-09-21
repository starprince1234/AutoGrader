# B4 文档索引（唯一入口）

只从这里开始。两张表：**权威表**（事实去哪找）+ **状态表**（能力现在是什么状态）。

## 权威表

| 主题 | 权威文件 | 作用 |
|---|---|---|
| 需求编号/原文/验收 | `../../MAIN/docs/requirements/rq-catalog.md` 第 5 节 | 6 条 B4 需求唯一来源 |
| 任务书/泳道 | `../../MAIN/docs/workstreams/B4.md` | WP-B4-A/B/C 边界 |
| HTTP/事件/JSON 契约 | `../../MAIN/contracts/**` | 字段与路径唯一权威（本模块不抄） |
| 契约版本与摘要 | `../contracts.lock` | 引用，不写死字面值 |
| 路由/事件/能力声明 | `../module.yaml` | 机器可读清单 |
| 物理表结构 | `../alembic/versions/**` | DDL 以迁移为准 |
| 跨模块边界与不可逆决策 | `../../MAIN/docs/adr/**` | 引用编号（ADR-0001/0002/0003） |
| 标识/作用域/角色 | `../../MAIN/docs/api/identifiers.md` | user_id 等归属与作用域规则 |
| 数据所有权 | `../../MAIN/docs/database/ownership.md` | schema owner 目录 |
| 契约变更规程 | `../../MAIN/docs/contracts/change-protocol.md` | 变更流程与硬性禁止 |
| 阶段证据 | `phases/active/`、`phases/archive/` | 真实命令与结果 |
| 本模块决策 | `adr/` | B4 自己的 ADR |

## 状态表

| 文档/能力 | 状态 | 最后验证方式 |
|---|---|---|
| 插件注册 / descriptor / 健康 | `已完成` | `uv run pytest tests/test_health.py tests/test_registry.py` |
| 数据库实例 / 最小权限角色 / schema 供给 | `已完成` | `uv run python -m autograder_b4.ops.provision`；跨 schema 拒绝实测 |
| 基线 + 平台表迁移（`b4`，16 张表） | `已完成` | 空库 `uv run alembic upgrade head`；head 与 `module.yaml` 一致 |
| 表结构规范自动检查（catalog + 全仓迁移 DDL） | `已完成` | `uv run python -m autograder_b4.ops.table_standards --scope catalog\|migrations` |
| 备份 / 恢复演练 | `已完成` | `uv run python -m autograder_b4.ops.backup --container <name>`；恢复演练见 phase-01 |
| 认证 / JWT / JWKS / 刷新 cookie | `已完成` | `uv run pytest tests/test_auth.py` |
| 角色与课程作用域授权 / 审计 | `已完成` | `uv run pytest tests/test_authorization.py` |
| 用户 / 课程 / 课序 / 选课 API | `已完成` | `uv run pytest tests/test_users_courses.py` |
| 名单导入（PDF/XLSX → 确认 → 原子写入） | `已完成` | `uv run pytest tests/test_roster_import.py` |
| 运行期模块注册表 + 漂移失败 | `已完成` | `uv run pytest tests/test_registry.py` |
| JetStream stream/consumer/DLQ/重放 + outbox | `已完成` | `uv run pytest tests/test_messaging.py`；`ops.outage_drill` |
| 共享域事件流 `DOMAIN_EVENTS`（契约全部 subject 的绑定） | `已完成` | `uv run python -m autograder_b4.ops.messaging provision`；平台实测 15/15 事件投递成功，见 phase-03 |
| 结构化 JSON 日志 + 脱敏 | `已完成` | `uv run pytest tests/test_logging.py` |
| 运维动作记录（迁移 / 备份 / 健康检查） | `已完成` | `uv run pytest tests/test_database.py tests/test_health.py`；见 `phases/active/phase-03-ops-lint-and-ledger.md` |
| `RQ-B-4-10` / `RQ-B-4-30` / `RQ-B-4-40` / `RQ-B-4-50` / `RQ-B-4-99` | `已完成` | 见 `requirements/traceability.md` |
| `RQ-B-4-20` | `已完成`（长期占用采样为残余项） | 见 `requirements/traceability.md` 残余项 |
| JetStream 配额实测占用计量 | `已完成`（一次性实测） | `uv run python -m autograder_b4.ops.messaging metrics`；长期采样属平台资源门禁 |
| 初始管理员引导脚本 | `已完成` | `uv run python -m autograder_b4.ops.bootstrap_admin`（幂等、Argon2id、口令不回显）；见 `phases/active/phase-02-test-instance-isolation.md` |
| 测试实例隔离（模块本地 PostgreSQL 55435 / NATS 54224） | `已完成` | `docker compose -p b4 up -d`；见 `phases/active/phase-02-test-instance-isolation.md` |

> 规则：不在本索引中的正式文档视为治理缺陷；新增/移动/归档文档必须同步本索引。
