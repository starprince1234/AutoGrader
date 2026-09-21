# B1 冷启动路径（30 分钟内可执行）

目标：让一个不了解项目的人 30 分钟内完成"读三文件 → 装依赖 → 跑门禁 → 说出契约版本 → 认领工作包"。

## 第 1 步：读三个文件（约 10 分钟）

顺序读：

1. `../../README.md`（本模块入口）
2. `../../AGENTS.md`（硬规则）
3. `../requirements/traceability.md`（7 条 RQ 一览）

**成功标志**：能一句话说出"B1 是什么、拥有什么、不拥有什么、契约版本是多少"。

## 第 2 步：装依赖（约 5 分钟）

```powershell
corepack enable
pnpm install
```

**成功标志**：`pnpm install` 退出码 0，`node_modules/` 生成或更新。

## 第 3 步：跑通门禁（约 10 分钟）

```powershell
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

**成功标志**：四个命令退出码 0；`dist/index.html` 生成；Vitest 输出 `Test Files … passed`。
（一条命令跑全部：`pnpm verify`。契约类型生成用 `pnpm api:generate`，漂移门禁是 `pnpm api:check`。）

> **尚未执行**：端到端（Playwright）与 `pnpm dev`——需要真实 B2/B3/B4 与浏览器会话；见
> `../phases/active/phase-01-business-ui.md` 的"未执行项"。

## 第 4 步：说出契约版本（约 2 分钟）

打开 `../../contracts.lock`，读出 `contract_version`（当前 `0.11.0`）与 `digest`；再打开 `../../module.yaml` 确认 `requires`（它的 `contract_version` 必须与锁一致）。

**成功标志**：能说出"契约 0.11.0，B1 依赖 host/b4-auth/b2-submissions/b3-questions，消费 B4/B3/B2 接口、订阅两个评测事件"。

## 第 5 步：认领一个工作包（约 3 分钟）

打开 `workpackages.md`，在认领表对应行填上自己的名字与预计完成时间，并在 `../phases/active/` 登记"开始某项工作包"。

**成功标志**：认领表中有你的名字，且你明确知道该工作包的验收标准（`../requirements/acceptance.md`）与共享文件登记规则（`workpackages.md` 并行前提）。

## 冷启动验收（合计）

- 已读三文件 ✓
- `pnpm typecheck`、`pnpm build` 通过 ✓（或明确记录未执行原因）
- 能说出契约版本 ✓
- 认领表有记录 ✓
