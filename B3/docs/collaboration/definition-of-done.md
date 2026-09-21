# B3 完成定义（Definition of Done）

"一项需求算完成"的检查清单。每条给出**可核验动作**；任何一条不满足都不得标 `已完成`。

当前状态（2026-09-18，[Phase 04](../phases/active/phase-04-quality-surfaces.md)）：**除末节"仍然未满足的 DoD 项"外，其余条目全部满足**；末节列出的是跨模块项与一项未达标的性能验收（`RQ-B-3-30` 验收 5）。证据见该阶段记录（Phase 01 的原始证据见 [../phases/active/phase-01-question-bank-and-packages.md](../phases/active/phase-01-question-bank-and-packages.md)）。

> 本文件在 2026-09-18 做过一次**陈旧判据修正**：若干核验行写的是各自当时的事实，其后证据变了而文字没变。
> 修正只改"判据为什么成立/不成立"，**没有**把任何一条从"未满足"改成"满足"——特别是
> `RQ-B-3-30` 验收 5 至今仍未达标（窗口路径 23.8 s）。理由与实测见
> [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)。

## 代码

- [x] 有可运行实现（非 mock、非测试收集器、非静态占位）。
  - 核验：`uv run pytest -q` 全绿，例数与实测输出见 [../testing/cases.md](../testing/cases.md) 的"当前测试实现"表（该表随每次阶段记录更新；此处不复述数字，以免再次陈旧）。覆盖率未在最近一轮重跑，故不引用历史百分比。
- [x] 无 mock 残留（mock 仅限 NATS / B4 JWKS 等不可控外部服务）。
  - 核验：`tests/test_hygiene.py` 逐文件断言 src 无 `subprocess`/`os.system`/动态执行；测试替身仅 `InMemoryEventPublisher`（NATS）与 `StaticTokenVerifier`/注入的 `fetch_jwks`（B4 JWKS 端点），数据库/ORM/Alembic/路由/序列化器一律真实。

## 测试

- [x] 对应用例在 [../testing/cases.md](../testing/cases.md) 有登记且为"存在"。
  - 核验：用例目录中每条 TC 均为 `存在`（条数以该文件的目录表为准，不复述）；`traceability.md` 每条 RQ 的"关联测试"列指向真实文件与函数。
- [x] 涉及受保护内容的必须有自动化断言（受保护字段**键缺失**，非"值为空"）。
  - 核验：`test_questions_api.py::test_question_detail_has_no_protected_field`（键集合精确断言 + 响应原文子串搜索）、`test_dto_boundary.py`、`test_events.py::test_published_event_carries_no_protected_content`。

## 迁移

- [x] 涉及表结构变更的有迁移文件；`uv run alembic upgrade head` 在空库从零执行通过。
  - 核验：`tests/conftest.py` 的会话夹具每次运行都 `DROP SCHEMA b3` 后 `alembic upgrade head`；`tests/test_migrations.py::test_migration_downgrade_then_upgrade_round_trips` 验证往返。
- [x] Alembic head 与 `module.yaml: database.migration_head` 一致。
  - 核验：`tests/test_migrations.py::test_module_yaml_head_matches_alembic_head`；MAIN 的 `checks.py` 独立复核（`migration heads and local ports agree with compose: ok`）。
- [x] 无跨 schema FK / SQL / 视图；无旧评分/证据/报表对象。
  - 核验：`test_migrations.py::test_no_foreign_key_leaves_the_b3_schema`、`::test_no_cross_schema_statement_in_the_migration`、`::test_no_legacy_tables_anywhere`；`test_hygiene.py::test_only_the_b3_schema_is_referenced`。

## 文档

- [x] 更新 [../requirements/traceability.md](../requirements/traceability.md) 状态列（→`已完成`，含"已知限制"列）。
- [x] 更新 [../README.md](../README.md) 状态表（+ 新增文档进索引，含 `contracts/changes/` 与 `frozen-interfaces.md`）。
- [x] 更新 [../phases/active/](../phases/active/) 阶段记录的验证记录（真实命令 + 结果 + 未验证项）。
- [x] 契约/接口变化时更新 [../api/contract.md](../api/contract.md) / [../api/events.md](../api/events.md)（只写关系，不抄字段）。

## 契约一致性

- [x] 不修改 `MAIN/contracts/**`；`contracts.lock` 未手工改动。
  - 核验：版本与摘要以 [../../contracts.lock](../../contracts.lock) 为准（本文件**不复述**摘要字面值——历史上这里写过一次，随后契约变更使其立刻变成一句错误的话）；`tests/test_hygiene.py::test_contracts_lock_is_not_hand_edited` 断言锁的版本与 `MAIN/contracts/openapi/b3.yaml` 声明的版本一致。
- [x] 事件/端点声明与 `module.yaml` 一致（SDK 事件方向缺口除外，且已记录）。
  - 核验：`test_plugin.py`（路由前缀、每条路由落在声明前缀内、publishes 对齐）；缺口见 `ADR-0003` 与 [../architecture/overview.md](../architecture/overview.md) 已知缺口 1。
- [x] 发现契约缺口时**先上报、不私改**：`CHG-B3-0001/0002/0003`。

## 安全与脱敏自检

- [x] 全仓搜索：公开 DTO / 事件 / 日志不含 `reference_answer` / `hidden_cases` / `expected_output` / 学生源码。
  - 核验：`test_hygiene.py::test_protected_vocabulary_is_absent_from_wire_producing_modules`（对产出 wire 的模块做 AST 断言：受保护名不得作为字典键或关键字参数）+ 契约测试 + 日志脱敏测试三重确认。
- [x] 统计只消费去标识化事件；消费端字段白名单校验（未知即拒绝）。
- [x] 受保护内容由数据库权限隔离（普通角色 `permission denied`）。
- [x] 无 `RQ-B-3-70`、无旧 scoring/evidence/report 路由与模型（含 `__pycache__` 字节码；MAIN 门禁独立复核）。

## 阶段记录

- [x] 验证记录含真实命令、环境、结果、未执行项及原因（不伪报通过）。
- [x] 满足归档门禁时才移入 `archive/`。**当前不归档**：理由是"跨模块联调未做"（下表前四项）与
  `RQ-B-3-30` 验收 5 未达标。**不是**因为 `CHG-*` 待裁决——三条 B3 主动上报的缺口（`CHG-B3-0001/0002/0003`）
  与 MAIN 侧新增的 `CHG-0013` 出口都已有裁决或已成文；`CHG-0016` 也已裁决 `PATCH` 响应体（契约判错、B3 实现本来就对）。
  等 MAIN 裁决的**没有**遗留项：曾记为"契约笔误"的 `severity` 一事已被 `CHG-0016` §5 驳回——真实的经过是实现偏离了契约，已改实现，详见 [../README.md](../README.md) 已知限制。

## 仍然未满足的 DoD 项（跨模块，必须如实保留）

| 项 | 状态 | 为什么不能由 B3 单独满足 |
|---|---|---|
| 三类 adapter 的"端到端证据链" | 未满足（⚠ B2 联调） | 需要 B2 runner 实际执行；B3 只产出 package 与摘要 |
| 事件消费的真实 durable consumer / DLQ | 未满足 | 需要 B4 提供 NATS JetStream |
| B4 真实 JWKS 端点验签 | 未满足 | 需要 B4 服务；验签逻辑本身已用真实 RS256 测过 |
| 统计在 10 万事件量级可用 | 未满足（**基准已实测，窗口路径不达标**） | 基准**已做**：预聚合路径在 10 万量级下可用，而带时间窗的路径耗时属"不可用于交互式查询"，故不满足的是**性能结论**，不再是"缺证据"。补救（窗口求和下推 SQL、或按时间桶预聚合）不需要改契约。实测方法与原始输出见 [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)；面向教师/运维的结论摘要见 [../README.md](../README.md) 与 [../architecture/overview.md](../architecture/overview.md)。本表不复述数值 |
| 冷启动验收（Linux） | 未满足 | 属 MAIN 的 Linux 验收流程 |
