# Phase 00 初始化（已完成其可执行部分，由 Phase 01 接续）

> **接续**：业务界面与工具链在 `phase-01-business-ui.md` 中实现并实跑验证；
> 本文件保留初始化阶段的决策与当时的"未执行"记录（不得据此声称能力已实现）。

## 目标与范围

建立 B1 前端项目骨架：React 19 + Vite 7 应用壳、契约锁、模块清单、文档治理；**不实现任何业务能力**。

## 输入与决策

- 需求：`../../../../MAIN/docs/requirements/rq-catalog.md` 第 2 节（7 条 B1 需求）。
- 任务书：`../../../../MAIN/docs/workstreams/B1.md`。
- 契约：`../../../contracts.lock`（`contract_version: 0.1.0`，`status: baseline`，digest 见该文件）。
- 技术栈：TS 5.7 / React 19 / Vite 7 / @tanstack/react-query 5 / @tanstack/react-router 1 / monaco-editor 0.55 / echarts 6 / pnpm / Node 24（`../../../package.json`）。
- 决策：无权威数据（`../../adr/ADR-0001-no-authoritative-data-in-browser.md`）、契约生成类型（`../../adr/ADR-0002-contract-generated-api-client.md`）。

## 完成条件

1. 应用壳可 `pnpm dev` / `pnpm build`（待首次实跑记录）。
2. `module.yaml` 与 `contracts.lock` 与 MAIN 一致。
3. 文档体系按 `../../../../MAIN/docs/collaboration/module-doc-spec.md` 第 2 节齐全。
4. 无 mock 成功响应、无手抄契约字段表、无虚假"已完成"。

## 实际变更

- 已存在：`package.json`、`tsconfig.json`、`vite.config.ts`、`index.html`、`.env.example`、`.gitignore`、`src/main.tsx`（15 行占位 App）、`src/styles.css`、`module.yaml`、`contracts.lock`。
- 本阶段（文档治理）：按 spec 扩写/新建 `docs/**` 全套文档与根 `README.md`/`AGENTS.md`（本次任务）。
- **未修改**：`contracts.lock`、`module.yaml`、`package.json`、`src/**`、任何 `../../../../MAIN/**` 文件。

## 验证记录

- **未执行任何构建/安装命令**（任务约束：不得执行 `pnpm install` / `pnpm build` / `uv`）。
- 已确认（文件层面，代码审阅）：`src/main.tsx` 为 15 行占位 App；`package.json` 无测试框架、无真实 ESLint（`lint` = `tsc -b`）；`module.yaml` 的 `routes: []`、`publishes: []`、`subscribes: [progress, completed]`、`database.schema: none`。
- 观察到仓库 `node_modules/` 存在（依赖曾安装），但**未验证其完整性**。
- 待首次实跑命令（补录结果）：`pnpm typecheck`、`pnpm build`、`pnpm dev`。

## 风险与遗留项

1. **测试与 lint 缺口**：无 vitest，无真实 ESLint，`lint` 脚本只是 typecheck。→ 本阶段补齐，或明确记录未补齐原因（workstream DoD 第 2 条）。
2. ~~**契约基线不完整**~~ → **已解决**：MAIN 已将基线契约收敛为完整可据以实现的结构，B1 消费的**全部**端点均已成文（含 `/api/v1/courses` 系列、名单 mapping/confirm、`/question-versions`、`/assignments`、成绩查询与导出、JWKS/刷新/退出、评测状态快照）。**三条泳道均无"等待冻结"的阻塞项**。→ 明细见 `../../api/contract.md` 与 `../../collaboration/interfaces.md`。
3. ~~**SSE 事件类型表未成文**~~ → **已解决**：SSE 帧类型已在 `MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame` 中冻结（SSE 属 HTTP 契约，故在 OpenAPI 而非 AsyncAPI）。
4. ~~**终态覆盖缺口**~~ → **已解决**：B1 的全部终态经 SSE 流到达；`rejected` 刻意没有领域事件（只对提交会话有意义），B1 无需为其增加订阅。
5. **路由骨架争抢**：`src/main.tsx` 尚无路由/角色守卫，三泳道会争抢同一文件。→ 由一人先建路由骨架并冻结文件结构。
6. **ADR-0003 已知缺口（未解决，非 B1 可修复）**：host 暂无法校验事件发布/订阅方向。
7. **API 类型生成未接入**：B1 必须从 MAIN OpenAPI 生成类型，当前尚未配置生成流程（见 `../../adr/ADR-0002-contract-generated-api-client.md`）。
   → **已在 Phase 01 解决**：`scripts/generate-api-types.mjs` + `pnpm api:generate` / `pnpm api:check`（实跑通过）。

## 文档影响检查

- 本阶段文档变更：`docs/README.md`（权威表+状态表）、`docs/requirements/*`、`docs/architecture/*`、`docs/api/*`、`docs/database/*`、`docs/testing/*`、`docs/adr/*`、`docs/runbooks/*`、`docs/collaboration/*`、`docs/knowledge/*`。
- 归档门禁：本阶段完成条件尚未全部满足（缺实跑验证），**不归档**。
