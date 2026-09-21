# B3 冷启动路径（Onboarding）

目标：让一个不了解项目的人在 **30 分钟内**完成"读 → 装 → 跑 → 说 → 认领"。每一步有**成功标志**。

## 第 1 步：读三个文件（约 10 分钟）

1. 本模块根 [../../README.md](../../README.md) —— 定位、边界、状态。
2. [../README.md](../README.md) —— 权威表 + 状态表（唯一索引）。
3. [../architecture/boundaries.md](../architecture/boundaries.md) —— 你能碰什么、不能碰什么。
4. [frozen-interfaces.md](frozen-interfaces.md) —— 已冻结的接口（公开/内部 DTO 边界、编译器签名）与改动顺序。

**成功标志**：能一句话说出"B3 拥有题库/版本/测试包/去标识化统计；不执行学生代码、不评分、不拥有证据"，并说出受保护内容的三个去处禁令。

## 第 2 步：装依赖（约 5 分钟）

```powershell
cd B3
uv sync --all-groups
```

**成功标志**：`uv sync` 无错误，`.venv` 生成（`dev` 组含 pytest / pytest-cov / ruff / jsonschema / httpx）。
> Phase 00 记录的"uv 缓存受限"在本环境已不复现；若再遇到，见 [../runbooks/troubleshooting.md](../runbooks/troubleshooting.md) 第 1 条。

## 第 3 步：跑通门禁（约 10 分钟）

```powershell
cd B3
docker compose up -d postgres          # 模块本地库，宿主 55434（含最小权限角色）
uv run ruff check .
uv run pytest --cov=autograder_b3
uv run alembic upgrade head
```

**成功标志**（当前分支，2026-09-20 [Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md) 实测）：`ruff` → `All checks passed!`；`pytest` → `393 passed`；迁移到 `20260915_0004 (head)`。
（未起 PostgreSQL 时测试会**跳过并告知启动命令**，不会假装通过。历史阶段的例数见各自 phases 记录，不在此累加。）

跨模块门禁（可选但推荐）：

```powershell
cd ../MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

**成功标志**：三行 `ok`（契约摘要 / 文档治理与链接 / 迁移 head 与端口）。

## 第 4 步：说出本模块契约版本

打开 [../../contracts.lock](../../contracts.lock)。

**成功标志**：能说出 `contract_version: 0.1.0`，并说明其摘要与 `../../../MAIN/contracts/` 的绑定关系（MAIN `checks.py` 会独立复核）。同时能说出 3 条 `CHG-B3-*` 缺口的存在与含义。

## 第 5 步：认领一个工作包

读 [workpackages.md](workpackages.md)。三个工作包在 Phase 01 已完成（[认领表](workpackages.md) 已登记）；新工作应从下列**已知未完成项**中选：

1. B2 runner 端到端联调（三型 adapter 的证据链）。
2. NATS durable consumer / DLQ 真实行为联调（需 B4 提供 NATS）。
3. 统计性能基准（10 万事件量级）。
4. 跟进三条 `CHG-B3-*` 的 MAIN 裁决并按结论调整实现。

**成功标志**：在认领表登记姓名、工作包与预计完成时间，并确认理解该工作包的冻结前提与验收标准（[../requirements/acceptance.md](../requirements/acceptance.md)）。

## 冷启动通过判据

五步全部完成 = 冷启动验收通过。这是 `MAIN/docs/workstreams/B3.md` §7 DoD 第 9 条。
