# B4 冷启动路径（30 分钟）

目标：一个不了解项目的人，30 分钟内读三份文件 → 装依赖 → 跑通门禁 → 说出契约版本 → 认领一个工作包。

## 第 1 步：读三份文件（15 分钟）

1. `../../MAIN/docs/workstreams/B4.md`（B4 任务书、边界、泳道）——成功标志：能说出 B4 拥有什么/不拥有什么。
2. `../../MAIN/docs/requirements/rq-catalog.md` 第 5 节（6 条需求）——成功标志：能背出 6 个编号（含 `RQ-B-4-99`）。
3. `../../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md`——成功标志：能说出"设计期归 MAIN、运行期归 B4"。

## 第 2 步：装依赖（5 分钟）

```powershell
uv sync --all-groups
```

成功标志：命令成功退出，无缓存/权限报错。（本机沙箱可能失败，见 `../runbooks/troubleshooting.md` 第 2 条。）

## 第 3 步：跑通门禁（5 分钟）

```powershell
uv run ruff check .
uv run pytest
```

成功标志：ruff 无违规、pytest 通过（当前仅 `T-PLUGIN-01` 一条）。

## 第 4 步：说出契约版本（2 分钟）

打开 `../contracts.lock`，说出 `contract_version` 与 `digest`。成功标志：版本与 MAIN 实时摘要一致。

## 第 5 步：认领一个工作包（3 分钟）

打开 `../collaboration/workpackages.md`，选一个 `WP-B4-A/B/C` 下的工作包，在认领表填上你的名字。成功标志：认领表有一行非空，且你清楚该包的验收标准。

## 完成标志

五项都达成即冷启动通过；未通过项记录到 `../phases/active/`。
