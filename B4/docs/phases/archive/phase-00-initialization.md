# Phase 00 初始化（当前阶段）

## 目标与范围

交付 B4 骨架（插件注册 + `descriptor` + 契约锁 + 空基线迁移 + 最小测试）+ **完整文档体系**（本阶段文档交付）。业务能力不在此阶段。

## 输入与决策

- 输入：`../../MAIN/docs/workstreams/B4.md`、`../../MAIN/docs/requirements/rq-catalog.md` 第 5 节、`../../MAIN/docs/adr/ADR-0001/0002/0003`、`../../MAIN/contracts/**`。
- 决策：`RQ-B-4-40` 按 ADR-0003 切分（设计期 MAIN / 运行期 B4）；本模块三条 ADR（`../adr/`）已记为 `提议`。

## 完成条件

1. 插件可被 MAIN host 装载（entry point 注册，`descriptor` 有效）。—— 代码层面已就位，**装载未在本环境验证**。
2. `contracts.lock` 与 MAIN 摘要一致。—— 由 MAIN 校验，本模块不改动。
3. 文档体系按 `module-doc-spec.md` 齐全。—— 本次完成。
4. 无 mock、无未实现能力冒充。—— 全部业务能力标注 `待实现`/`骨架`。

## 实际变更

- 已有代码（未改动）：`src/autograder_b4/plugin.py`（20 行骨架）、`alembic/versions/20260915_0001_b4_schema_baseline.py`（建 `b4` schema）、`tests/test_plugin.py`（descriptor 断言）。
- 本次仅**扩写/新建文档**（`*.md`），未改 `contracts.lock` / `module.yaml` / `pyproject.toml` / `src/**` / `tests/**` / `alembic/**`。

## 验证记录（真实命令 + 结果 + 未执行项）

| 命令/验证 | 环境 | 结果 | 说明 |
|---|---|---|---|
| `uv sync --all-groups` | 本机沙箱 | **未执行** | `uv` 缓存目录 `D:\dev\caches\uv` 在工作区之外，沙箱拒绝写入 |
| `uv run ruff check .` | 本机沙箱 | **未执行** | 同上（依赖 `uv` 与已同步环境） |
| `uv run pytest` | 本机沙箱 | **未执行** | 同上 |
| `uv run alembic upgrade head` | 本机沙箱 | **未执行** | 需可用的 PostgreSQL，本环境不保证有 Docker/psql |
| `docker compose up -d` | 本机沙箱 | **未执行** | 不保证有 Docker |
| 代码审查 `plugin.py` | 人工 | 已核对 | `descriptor` 扁平 `events` 元组只列 1 事件（与 `module.yaml` 的 publishes/subscribes 不一致，见下） |

**未执行原因**：本机沙箱下 `uv` 无法写其缓存目录（`D:\dev\caches\uv` 在工作区之外），且不保证有 Docker / psql / nats 可用。**不伪报通过**；上述命令须在完整工具链环境执行后回填本表。

## 风险与遗留项

1. `descriptor.events` 扁平元组 vs `module.yaml` publishes/subscribes（SDK 缺口，ADR-0003 遗留风险）——待 MAIN 破坏性变更修复。
2. `../../MAIN/infra/postgres/init.sql` 无角色供给——`RQ-B-4-10` 缺口。
3. 备份/恢复演练未执行——`RQ-B-4-10` 验收未达成。
4. `module.yaml: migration_head=baseline` 与迁移 revision `20260915_0001` 命名口径待统一。
5. `.env.example`/`alembic.ini` 端口 `5432` vs 本地宿主 `55432` 待统一。

## 文档影响检查

- 已新建/扩写 `docs/**` 全部文件并登记进 `docs/README.md` 索引。
- `docs/requirements/traceability.md` 覆盖全部 **6** 条 RQ（含 `RQ-B-4-99`）。
- 状态词仅使用 `骨架`/`待实现`/`已完成`/`阻塞`。
