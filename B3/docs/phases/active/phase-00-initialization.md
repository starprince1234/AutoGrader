# Phase 00 初始化（Active）

> **后续说明（2026-09-15，Phase 01）**：本文件下方的"验证记录"是 **Phase 00 当时的真实情况**（`uv` 缓存受限、命令未执行），按"阶段记录不回头改写"的原则保留原文。Phase 01 已在同一环境实测通过 `uv sync` / `ruff` / `pytest` / `alembic upgrade head`，并补齐了本文件列出的 `pytest-cov` 缺口与 `b3.yaml` 契约完整性；见 [phase-01-question-bank-and-packages.md](phase-01-question-bank-and-packages.md)。本阶段仍未归档（归档门禁见 [../archive/README.md](../archive/README.md)）。

## 目标与范围

建立 B3 模块骨架：插件注册、契约锁、空 schema 基线迁移、文档体系；**不实现任何题库业务**。清除历史遗留（旧 scoring/evidence/report）。

## 输入与决策

- 需求权威：`../../../MAIN/docs/requirements/rq-catalog.md` 第 4 节（7 条 B3 RQ）。
- 任务书：`../../../MAIN/docs/workstreams/B3.md`。
- 契约权威：`../../../MAIN/contracts/**`；锁 `../../contracts.lock`（`contract_version: 0.1.0`）。
- 决策：本阶段只保留 `骨架`（注册/健康/空路由/空 schema），所有业务能力标 `待实现`；`taskone/` 保留为非权威旧材料、默认不跟踪。

## 完成条件

1. 插件可被 `entry_point` 装载，`descriptor` 与 `module.yaml` 语义一致（除已知事件方向缺口）。
2. 空基线迁移存在且仅建 `b3` schema。
3. 文档体系按 `module-doc-spec.md` 建齐。
4. 无旧 scoring/evidence/report、无 `RQ-B-3-70`、无兼容路由。

## 实际变更

- `src/autograder_b3/plugin.py`（21 行）：`B3Module`，`router()` 返回空 `APIRouter()`，`health()` 返回 `"plugin loaded; question bank pending"`。
- `tests/test_plugin.py`：1 个用例（断言 `b3-questions` 在 provides、`b2-evaluation` 不在）。
- `alembic/versions/20260915_0001_b3_schema_baseline.py`：仅 `CREATE SCHEMA IF NOT EXISTS b3`（空基线）。
- 本阶段新增/扩写文档体系（见"文档影响检查"）。

## 验证记录（真实状态）

| 命令 / 动作 | 环境 | 结果 | 说明 |
|---|---|---|---|
| `uv sync --all-groups` | Windows 沙箱（本环境） | **未执行** | uv 无法写缓存目录 `D:\dev\caches\uv`（工作区之外），sandbox 拒绝 |
| `uv run ruff check .` | 同上 | **未执行** | 同上（依赖 uv） |
| `uv run pytest` | 同上 | **未执行** | 同上；`tests/test_plugin.py` 依赖 `autograder-platform`（`../../MAIN/sdk`）与构建产物 |
| `uv run alembic upgrade head` | 同上 | **未执行** | 同上 + 需可用 PostgreSQL（本环境未跑 Docker） |
| 文档创建 / 扩写 | 本环境 | 完成 | 只写 `*.md`，未改任何受保护文件 |

**未执行项及原因**：所有 `uv` 命令因沙箱缓存目录不可写而**未执行**；因此**不存在**任何"测试通过"或"迁移成功"的证据。骨架期声称能力仅限于"插件文件存在且结构符合描述"，运行时未验证。

## 风险与遗留项

1. **SDK 事件方向缺口**：`descriptor.events` 扁平元组只列 1 个事件，宿主无法校验 publishes/subscribes（`../../../MAIN/docs/adr/ADR-0003`）。修复需 SDK 破坏性变更。
2. ~~**OpenAPI 不完整**~~ → **已解决**：MAIN 已将 `b3.yaml` 补全为可据以实现的结构：题目列表/创建/详情/草稿更新、版本读取、`publish`/`archive`、内部 evaluation package、assignment 列表与创建、题目统计查询（`GET /api/v1/questions/{question_id}/statistics`）。统计端点无需新增路由前缀。**B3 三条泳道均无"等待契约冻结"的阻塞项。**
3. **`pytest-cov` 未声明（未解决）**：`dev` 组只有 `pytest` 与 `ruff`，`uv.lock` 亦无该包。**不在缺少 `uv` 的环境手改 pyproject**（会使 lock 失配）。修复方式：`uv add --dev "pytest-cov>=6,<7"`。详见 `../../../MAIN/docs/testing/strategy.md` 第 4.1 节。
4. `uv` 命令未验证，运行时结论需在 Linux 环境复核。

## 文档影响检查

- `../../README.md`、`../../AGENTS.md`、`../README.md` 已同步（状态、缺口、边界）。
- `../requirements/traceability.md` 状态列：7 条 RQ 中 6 条 `待实现`、`RQ-B-3-99` `骨架`。
- 本阶段**不归档**：完成条件中"运行验证"未满足（uv 未执行），不满足归档门禁。
