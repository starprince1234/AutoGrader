# Phase 00 初始化（B2）

## 目标与范围

建立 B2 插件的**骨架**：entry point 注册、`B2Module` descriptor、契约锁、Alembic 基线迁移、最小测试，以及符合 [../../../MAIN/docs/collaboration/module-doc-spec.md](../../../../MAIN/docs/collaboration/module-doc-spec.md) 的文档体系。**本阶段不实现任何评测业务**（接收、静态检查、安全判定、动态执行、报告、导出均为 `待实现`）。

## 输入与决策

- 输入：`../../../MAIN/docs/requirements/rq-catalog.md` 第 3 节（9 条 B2 需求）、`../../../MAIN/docs/workstreams/B2.md`、`../../../MAIN/contracts/**`。
- 决策：静态检查工具选型（[../adr/ADR-0001](../../adr/ADR-0001-static-analysis-tooling.md)）、沙箱策略（[../adr/ADR-0002](../../adr/ADR-0002-sandbox-execution-policy.md)）、内容寻址存储（[../adr/ADR-0003](../../adr/ADR-0003-content-addressed-submission-storage.md)）。
- 契约版本 `0.1.0`，`contracts.lock.digest = 6890df08...c43`，`migration_head: baseline`。
  > **2026-09-16 时效标注**：本行是 **Phase 00 当时**的快照。当前 `migration_head` = `20260915_0009`，
  > `contracts.lock.digest` 见 [../../../contracts.lock](../../../contracts.lock)（与 `MAIN/contracts` 实时一致）。

## 完成条件

1. 插件经 entry point 可装载，`descriptor` 与 `module.yaml` 的 module/contract_version/provides/requires/routes 一致。
2. Alembic 基线迁移可创建 `b2` schema。
3. 最小测试 `T-PLUGIN-001` 存在。
4. 文档体系按 module-doc-spec 第 2 节清单齐全。

## 实际变更

- `src/autograder_b2/plugin.py`：`B2Module`（descriptor + 空 `router()` + `health()`）。
- `alembic/versions/20260915_0001_b2_schema_baseline.py`：`CREATE SCHEMA IF NOT EXISTS b2`。
- `tests/test_plugin.py`：断言 descriptor 的 module 与 contract_version。
- 本阶段**仅写文档**（`*.md`），未改 `contracts.lock`、`module.yaml`、`pyproject.toml`、`src/**`、`tests/**`、`alembic/**`。

## 验证记录

> 均为如实记录，**不得伪报通过**。

| 命令 / 检查 | 环境 | 结果 | 未执行项及原因 |
|---|---|---|---|
| `uv run ruff check .` | 本机沙箱（Windows） | **未执行** | `uv` 无法写缓存目录 `D:\dev\caches\uv`（工作区之外，沙箱限制） |
| `uv run pytest` | 本机沙箱（Windows） | **未执行** | 同上 |
| `uv run alembic upgrade head` | 本机沙箱（Windows） | **未执行** | 同上（且无 PostgreSQL 实例） |
| 源码审阅（plugin.py / module.yaml / pyproject.toml / contracts.lock / alembic） | 文件读取 | 完成 | 用于核对事实，非运行时验证 |

> **2026-09-16 更新**：上表三行"未执行"是 Phase 00 当时的真实状态（`uv` 缓存目录不可写）。
> 该阻塞此后已消除：`uv run ruff check .`、`uv run pytest`（真实 PostgreSQL 17 + 真实 NATS JetStream）、
> `uv run alembic upgrade head` 均可执行且有真实记录，见 [phase-01-implementation.md](phase-01-implementation.md)
> 的"验证记录"。本行不修改历史记录，只标注其时效。

**已验证事实（源码审阅，非运行；均为 Phase 00 当时快照）**：
- `B2Module.descriptor.events` 是扁平元组，只列 `submission.accepted`、`evaluation.completed` 两个事件，**少于** `module.yaml` 声明的 5 个 publishes（缺口见下）。
- `router()` 返回空 `APIRouter()`；`health()` 返回 `"plugin loaded; business evaluator pending"`。
- 基线迁移仅 `CREATE SCHEMA IF NOT EXISTS b2`。
  > **2026-09-16 时效标注（这三条均已不再成立）**：`descriptor.events` 已补全为 6 个 subject（见下方风险项 1 的更新）；
  > `router()` 现注册 7 条端点（`plugin.py` 拼 3 个路由族）、`health()` 委托 `runtime.health_summary()` 返回真实健康详情；
  > 迁移链已到 `20260915_0009`，业务表全部创建。三条的当时性由本标注限定，不改写历史记录。

## 风险与遗留项

1. **`descriptor.events` 与 `module.yaml` 不一致（`阻塞`）**：SDK 的 `ModuleDescriptor.events` 为扁平元组、不区分 publishes/subscribes（[../../../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md](../../../../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md) 已记录）。恢复验证所需操作：SDK 破坏性变更 `events` → `publishes`/`subscribes`，同步四个模块与 manifest schema，再更新 B2 descriptor。
   > **2026-09-16 更新（本条已消解）**：`descriptor.events` 已按 registry 的比较口径补全为 `module.yaml` 声明的全部 6 个 subject（5 publishes + 1 subscribes），并由 `tests/test_plugin.py::test_descriptor_events_equal_module_yaml_publishes_and_subscribes`（T-EVT-019）钉住。剩余限制只是"扁平元组无法表达方向"，属 SDK 形态问题，不阻塞。详见 [../api/events.md](../../api/events.md) 第 4 节。
2. **nsjail 仅 Linux 可验收**：动态执行与资源账本实测无法在 Windows 完成；先交付平台无关部分（比较器、报告、持久化）。
   > **2026-09-16 更新**：平台无关部分已交付，且 Linux 侧已用**特权容器**完成验收
   > （隔离矩阵 `13 passed, 1 skipped, 1 xfailed`、真实 runner 验收 `5 passed, 1 skipped`、
   > 宿主驱动模式 `1 passed, 14 skipped`），见 [../../../tests/linux/README.md](../../../tests/linux/README.md)。
3. **`uv` 命令本环境未验证**：缓存目录在工作区外；需在授权环境重新执行并补记录。
   > **2026-09-16 更新（本条已消解）**：`uv` 实测可用（`uv 0.10.2`），三条命令已用 `uv run ...` 真实执行并留档，
   > 见 [phase-01-implementation.md](phase-01-implementation.md) 的"验证记录"。
4. **业务表未建**：`submission`/`artifact`/`evaluation_run`/`evaluation_result`/`security_finding`/`email_checkpoint` 均为 `待实现`。
   > **2026-09-16 更新（本条已消解）**：迁移链已到 `20260915_0009`，上述业务表全部创建并有真实 PostgreSQL 用例，
   > 见 [../../database/migrations.md](../../database/migrations.md)。

## 文档影响检查

- 本阶段新增/扩写文档已全部登记进 [../README.md](../../../README.md) 索引（权威表 + 状态表）。
- 追踪表 [../requirements/traceability.md](../../requirements/traceability.md) 覆盖 9 条 RQ，状态与本文一致（`RQ-B-2-99` 为 `骨架`，其余 `待实现`）。
  > **2026-09-16 更新（本条已失效）**：上面这句只对 **Phase 00 当时**成立。追踪表的状态列已于实现阶段
  > **逐行刷新**（9 条 RQ 全部 `已完成`，每条都指向真实测试与阶段证据），并以
  > [phase-01-implementation.md](phase-01-implementation.md) 为证据来源；未验收项在该文件的
  > "验证记录 / 未执行项"与"风险与遗留项"里列明，**不因 `已完成` 而消失**。
