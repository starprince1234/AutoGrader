# B2 冷启动路径（Onboarding）

目标：让一个不了解项目的人在 **30 分钟内**完成"读三个文件 → 装依赖 → 跑通门禁 → 说出契约版本 → 认领一个工作包"。

## 第 1 步：读三个文件（约 10 分钟）

1. [../README.md](../README.md)（本模块入口：定位、状态、位置图、已知限制）。
2. [requirements/traceability.md](../requirements/traceability.md)（9 条 RQ 一览）。
3. [collaboration/workpackages.md](workpackages.md)（3 条泳道 + 认领表）。

**成功标志**：能口头回答——B2 的"唯一职责"是什么；9 条 RQ 编号分别是哪些；3 条泳道分别对应哪些 RQ。

## 第 2 步：装依赖（约 5 分钟）

在具备写权限的环境执行：

```powershell
uv sync --all-groups
```

**成功标志**：`uv` 无报错完成；`.venv` 目录生成。

> `uv` 在本环境**实测可用**（2026-09-16：`uv 0.10.2`；`uv cache dir` = `D:\dev\caches\uv`，
> `uv sync --all-groups --dry-run` → `Would make no changes`）。**更正**：此前写"在本 AI 沙箱执行会失败
> （缓存目录在工作区外）"，已被实测反证。

## 第 3 步：跑通门禁（约 10 分钟）

```powershell
uv run ruff check .
uv run pytest
uv run alembic upgrade head
```

**成功标志**：
- `ruff` 无告警（line-length 100 / py312）。
- `pytest` 通过（**快照读数，2026-09-18：`1134 passed, 9 skipped, 1 xfailed`**，需真实 PostgreSQL）。
  **更正**：此前写"当前仅 `T-PLUGIN-001`"是骨架阶段的过期表述。
  **这两处"当前 xx"是快照，不要改写成最新值**：当前读数由 MAIN 的台账持有
  （`MAIN/.runtime/evidence/B2.json`，由 `python MAIN/scripts/test_evidence.py --all` 产生、
  `check_test_evidence` 强制）。
- `alembic upgrade head` 成功且 `uv run alembic heads` 输出与 `module.yaml: database.migration_head` 一致
  （**快照：`20260915_0009`；最新值以 `module.yaml` 为准**）。**更正**：此前写"当前 `baseline`"，已过期。

## 第 4 步：说出本模块契约版本（约 1 分钟）

打开 [../../contracts.lock](../../contracts.lock) 与 [../../module.yaml](../../module.yaml)。

**成功标志**：能回答——契约版本 `0.1.0`；`contracts.lock.digest` 前 8 位；本模块提供/消费哪些端点和事件。

## 第 5 步：认领一个工作包（约 5 分钟）

在 [workpackages.md](workpackages.md) 的认领表填入姓名与预计完成，并把对应 RQ 在 [traceability.md](../requirements/traceability.md) 状态列推进为 `in_progress` 口径（文档状态词仍只用 `骨架/待实现/已完成/阻塞`，认领进度记在工作包表）。

**成功标志**：认领表中有你的姓名；你清楚自己工作包的"冻结前提"与"验收标准"。

## 后续必读（开始动手前）

- [../../../MAIN/docs/workstreams/B2.md](../../../MAIN/docs/workstreams/B2.md)（任务书）。
- [../../../MAIN/docs/collaboration/module-doc-spec.md](../../../MAIN/docs/collaboration/module-doc-spec.md)（文档规范，三条铁律）。
- [../architecture/boundaries.md](../architecture/boundaries.md)（安全与数据边界）。
- [definition-of-done.md](definition-of-done.md)（"做完"的检查清单）。
