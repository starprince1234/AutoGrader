# AutoGrader B3 项目规则（给 AI Agent 的执行规则）

本文件是硬规则索引，细节见 [docs/README.md](docs/README.md) 指向的各文档。遵守 `MAIN/docs/collaboration/module-doc-spec.md` 三条铁律。

## 开始任务前

1. 读 [docs/README.md](docs/README.md)（索引 + 权威表 + 状态表）、[docs/architecture/boundaries.md](docs/architecture/boundaries.md)、[docs/collaboration/workpackages.md](docs/collaboration/workpackages.md)。
2. 读 `MAIN/docs/workstreams/B3.md`（任务书）与 `MAIN/docs/requirements/rq-catalog.md` 第 4 节。
3. 确认 [contracts.lock](contracts.lock) 的 `contract_version` 与摘要；若与 `MAIN/contracts` 实时摘要不一致，**停下**走契约变更规程，不要硬写实现。
4. 读 [docs/collaboration/frozen-interfaces.md](docs/collaboration/frozen-interfaces.md)：公开/内部 DTO 边界与 package 编译器签名已冻结，改动顺序是"改 ADR → 改代码 → 同步清单"。
5. 检查 Git 状态，确认没有未提交的共享文件冲突（`plugin.py`、`alembic/`、DTO 模块、错误码目录）。

## 我负责什么 / 不负责什么

- 我拥有：`b3` schema（question / question_version / assignment / test suite·case / fixture / 统计聚合）、evaluation package 的**生产**、去标识化统计。
- 我不拥有：提交物 / 评测运行 / 分数 / 证据（属 B2）；用户 / 课程 / 选课（属 B4）；UI（属 B1）。
- 我禁止：执行学生代码；计算或存储分数；读取 `b2`/`b4` schema；从提交内容生成统计（只能消费去标识化事件）；让受保护内容进入公开 DTO / 事件 / 日志 / 学生可访问存储。

## 硬规则

1. **单一权威**：字段/DDL/事件字段只以 `MAIN/contracts/**` 与 `alembic/versions/**` 为准，文档只写摘要 + 链接，**禁止手抄字段表**。
2. **不声称未实现**：状态词只用 `骨架`/`待实现`/`已完成`/`阻塞`，且必须与真实证据一致。业务能力已在 Phase 01 实现（见 [docs/phases/active/phase-01-question-bank-and-packages.md](docs/phases/active/phase-01-question-bank-and-packages.md)）；**跨模块未联调的部分**（B2 runner 端到端、NATS 真实消费、B4 真实 JWKS、性能基准、Linux 冷启动）必须保持未完成表述，不得因"代码写了"就标 `已完成`。
3. **受保护内容**：参考答案、隐藏用例、expected output、fixture 原始内容永不进入公开 DTO、事件、日志、学生可访问存储。
4. **数据边界**：只读写 `b3` schema；禁止跨 schema FK / SQL / ORM / 视图；禁止超级用户连接；迁移版本表 `alembic_version_b3`。
5. **事件**：订阅 `evaluation.completed` 只做去标识化统计；按 `event_id` 去重；不读取学生源码；payload 字段白名单校验。
6. **标识与时间**：UUID v4（owner 生成、创建后不可变）；时间 UTC RFC 3339 / `timestamptz`；错误码 `401`（未认证）/`403`（无权限，不用 `404` 掩盖）/`404`（资源不存在）。
7. **命名**：表复数 snake_case；索引 `ix_<table>_<cols>`；唯一约束 `uq_<table>_<cols>`；外键 `fk_<table>_<ref>`（遵循 B4 规范）。
8. **禁止**：mock 交付路径、兼容层 / 别名路由、shell 字符串拼接执行、恢复 `taskone/` 旧 scoring/evidence/report API、`RQ-B-3-70` 编号、跨模块 import。
9. **契约先行**：任何接口/事件/schema 改动先走 `MAIN/docs/contracts/change-protocol.md`，后改代码；不保留兼容层。

## 文档同步

改代码时同步更新：`docs/requirements/traceability.md`（状态列）、`docs/architecture/`（若结构变化）、`docs/database/schema.md` / `migrations.md`（若迁移变化）、`docs/testing/cases.md`（新增用例）、`docs/phases/active/`（真实验证记录）、`docs/README.md` 状态表（若状态变化）。

## 验证

```powershell
cd B3
uv run ruff check .
uv run pytest                      # 需要可达的 PostgreSQL（B3 compose 的 55434）
uv run alembic upgrade head
```

> 2026-09-15 在本环境（Windows + Docker + uv 0.10.2）**已实测**：`ruff` → `All checks passed!`；`pytest` → `282 passed`（覆盖率 90%）；`alembic upgrade head` → `20260915_0002 (head)`。Phase 00 记录的"uv 无法写缓存目录"已不复现。
>
> 跨模块门禁在 MAIN 侧执行：`cd ../MAIN && .\.venv\Scripts\python.exe -m autograder_host.checks`（校验契约摘要、文档治理与链接、迁移 head 与本地端口）。执行结果必须如实写进 `docs/phases/active/`，不得伪报通过。PostgreSQL 不得用 SQLite 代替；mock 仅限 NATS/B4 JWKS。

## 契约变更

发现契约与实现/文档不一致时：**不顺着实现改契约，也不偷偷改实现对齐**。提交 `CHG-*` 变更记录（`MAIN/docs/contracts/change-protocol.md` 第 2 步流程），由 MAIN 判定哪边是权威，再据此实现。禁止自行修改 `MAIN/contracts/**`。
