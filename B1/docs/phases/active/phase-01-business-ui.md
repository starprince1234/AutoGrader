# Phase 01 业务界面实现（WP-B1-A / B / C）（进行中）

## 目标与范围

把 B1 从"应用壳"推进到**可用的真实前端**：本文档覆盖三个工作包在浏览器内的全部交互——
名单导入与编辑（`RQ-B-1-10/20`）、题目渲染与作答提交（`RQ-B-1-40/50`）、成绩与导出与日志
（`RQ-B-1-30/60/99`），以及支撑它们的工具链（真实 ESLint、Vitest、从 MAIN OpenAPI 生成类型）。

**不在范围内**：任何服务端实现、任何权威数据、任何 MAIN/contracts 的修改。

## 输入与决策

- 需求：`../../../../MAIN/docs/requirements/rq-catalog.md` 第 2 节（7 条 B1 需求）。
- 契约：`../../../contracts.lock`（`contract_version: 0.1.0`，`status: baseline`）。
- 冻结清单与缺口：`../../collaboration/interfaces.md`。
- 决策：`../../adr/ADR-0001-no-authoritative-data-in-browser.md`、`../../adr/ADR-0002-contract-generated-api-client.md`。
- 契约缺口处置：**只记录、不改 MAIN**；见本文"契约缺口与阻塞"。

## 共享文件登记（并行前提）

下列文件是三条泳道共用面，本次由一人先行建立并冻结；后续改动需在此登记后再动：

| 共享文件 | 内容 | 本次动作 |
|---|---|---|
| `src/app/router.tsx`、`src/app/routeComponents.tsx` | 路由表与路由组件（守卫组合） | 新建并冻结 |
| `src/api/client.ts`、`src/api/problem.ts`、`src/api/types.ts`、`src/api/generated/**` | 契约 HTTP 客户端、错误模型、生成类型出口 | 新建并冻结（生成物由 `pnpm api:generate` 产出） |
| `src/api/directory.ts`、`src/api/opaque.ts` | 课程/课序目录与"契约缺口适配"（无 schema 响应） | 新建并冻结 |
| `src/auth/**` | 会话状态机、可见性规则、守卫 | 新建并冻结 |
| `src/logging/**` | 结构化日志与脱敏 | 新建并冻结 |
| `src/i18n/index.ts` | 文案目录（唯一字符串来源） | 新建并冻结 |
| `src/ui/components.tsx`、`src/ui/masking.ts`、`src/styles.css` | UI 原语、展示层脱敏、设计 token | 新建并冻结 |
| `src/testing/**` | HTTP 边界夹具与契约形状夹具 | 新建（**仅测试引用**，已验证生产代码无引用） |
| `package.json`、`vite.config.ts`、`eslint.config.js`、`tsconfig.json`、`scripts/**` | 工具链 | 修改 |

## 实现落点（文件 → 需求）

| RQ | 实现文件 |
|---|---|
| `RQ-B-1-10` | `src/features/roster-import/RosterImportPage.tsx`、`mapping.ts`、`useRosterImport.ts` |
| `RQ-B-1-20` | `src/features/roster/RosterPage.tsx`、`useEnrollments.ts`、`src/features/users/UsersPage.tsx`、`src/auth/visibility.ts`、`src/auth/guards.tsx` |
| `RQ-B-1-30` | `src/features/grades/GradesPage.tsx`、`GradeDrilldown.tsx`、`aggregate.ts`、`GradesChart.tsx`、`useGrades.ts` |
| `RQ-B-1-40` | `src/features/questions/QuestionListPage.tsx`、`QuestionDetailPage.tsx`、`SafeMarkdown.tsx`、`markdown/parse.ts`、`urlPolicy.ts`、`protectedFields.ts` |
| `RQ-B-1-50` | `src/features/submission/SubmitPage.tsx`、`CodeEditor.tsx`、`monaco.ts`、`progress.ts`、`useEvaluationStream.ts`、`draft.ts`、`validation.ts`、`identity.ts`、`useSubmission.ts` |
| `RQ-B-1-60` | `src/features/grades/useGrades.ts`（`downloadGradesExport`）、`src/features/grades/GradesPage.tsx` |
| `RQ-B-1-99` | `src/logging/logger.ts`、`src/logging/redaction.ts`、`src/app/ErrorBoundary.tsx`、`src/main.tsx` |

## 工具链变更（本阶段）

1. **真实 ESLint**：`lint` 由 `tsc -b` 改为 `eslint . --max-warnings 0`（flat config：`typescript-eslint` + `react-hooks` + `react-refresh`），并加入三条与硬规则一致的自定义限制：禁止 `dangerouslySetInnerHTML`；禁止把令牌/答案写入 `localStorage`；禁止导入其他模块（`B2/B3/B4/MAIN`）。
2. **Vitest 接入**：`test = vitest run`，jsdom + Testing Library，配置在 `vite.config.ts`，匹配器类型经 `src/testing/matchers.d.ts` 进入编译程序。
3. **API 类型生成**：`scripts/generate-api-types.mjs` 用 `openapi-typescript` 从 `MAIN/contracts/openapi/{common,b2,b3,b4}.yaml` 生成 `src/api/generated/*.ts`（只读契约、禁止手改）；`api:generate` 生成、`api:check` 作为漂移门禁。
4. **一条命令跑全部门禁**：`pnpm verify` = lint → typecheck → api:check → test → build。

## 真实验证记录（本次实跑）

| 命令 | 结果 | 说明 |
|---|---|---|
| `pnpm lint` | **通过**（退出码 0，0 error / 0 warning） | `eslint . --max-warnings 0` |
| `pnpm typecheck` | **通过**（退出码 0） | `tsc -b --pretty false`，含生成的契约类型 |
| `pnpm api:check` | **通过**："API types match MAIN/contracts." | 生成物与当前契约逐字节一致 |
| `pnpm test` | **通过**：`Test Files 17 passed (17)`、`Tests 92 passed (92)`，耗时约 15s（**本阶段结束时的读数**；当前读数见 `phase-02-apple-redesign-and-e2e.md` §6 与 `../testing/cases.md`） | 单元 + 组件 + 契约边界测试 |
| `pnpm build` | **通过**（退出码 0） | `tsc -b && vite build`，产物 `dist/`（`dist/index.html` 368 B） |
| `pnpm verify` | **通过**（退出码 0） | 上述五步串联实跑 |

补充说明（如实记录）：

- `pnpm install` 需要写 pnpm 的共享 store（`D:\dev\caches\pnpm\v10`）；在受限文件沙箱下会报
  `ERR_PNPM_EPERM`。本次以放宽文件策略后完成安装，**未修改任何依赖版本语义**。
- 构建有一条 **chunk 体积警告**（Monaco 的 `editor.main` 约 3.7 MB，Vite 默认阈值 500 kB）。
  这是**已知未处理项**：本次不做按语言裁剪（`editor.api` + 语言 contribution）的改造，因为
  无法在本环境用浏览器实测裁剪后的编辑器行为；留待有真实运行环境时处理。
- 未执行：`pnpm dev`（需要浏览器会话，本环境无法观测）；端到端（Playwright）——B2/B3/B4
  仍是骨架，没有可连的真实后端。

## 契约缺口与阻塞（**不改 MAIN**）

复核 `MAIN/contracts/**` 后确认的缺口，全部记录在 `../../collaboration/interfaces.md`
"已知契约缺口"，本阶段**没有**修改 `MAIN/contracts/**`，也没有用猜字段的方式绕过：

| # | 缺口 | B1 的处置 | 影响 |
|---|---|---|---|
| 1 | 题目图形**无下载端点**（`stem_assets` 只给 `asset_id`/`content_type`） | 只放行受控 URL；无端点时显示"资源不可得"，不伪造地址 | `RQ-B-1-40` 验收 2 阻塞 |
| 2 | `QuestionVersionPublic` 无题干字段、`QuestionPublic` 无版本列表 | 实现"选版本→重取→按版本缓存"的机制；契约未给题干时显式提示，不用当前题干冒充历史版本 | `RQ-B-1-40` 验收 5 阻塞 |
| 3 | `listSections`/`listEnrollments`/`listAssignments`/`listAuditRecords`/`getModuleHealth` 的 `200` **无 schema** | 按服务端返回键动态渲染 + 只依赖契约路径标识符；缺标识符时显式报错 | 名单/课序/作业集合只能"不透明消费" |
| 4 | 成绩**无汇总端点** | 页面提供"基于已加载 N 行"的本地统计并如实标注口径 | `RQ-B-1-30` 验收 1 只能部分满足 |
| 5 | **无"当前用户资料"端点**：会话响应不含 `user_id/student_id`，而提交信封**必填** `student_id` | 先取令牌 `sub`，再取本人成绩行的 `student_id`；两者都拿不到时**禁用提交**并说明原因 | 正常路径可用；缺声明时提交阻塞 |

> 以上缺口按 `../../../../MAIN/docs/contracts/change-protocol.md` 应由 MAIN 判定是否收编为 CHG；
> B1 侧只提供证据与影响面。

## 安全自检（对照 AGENTS.md 硬规则 4/5）

- 富文本：自有解析器产出结构化节点，**无 `innerHTML` 路径**；ESLint 规则禁止 `dangerouslySetInnerHTML`（XSS 用例见 `../../testing/cases.md` TC-B1-017/018）。
- 令牌：仅存内存（`src/auth/sessionStore.ts`）；测试断言 `localStorage`/`sessionStorage` 为空。
- 日志：字段级拒绝清单脱敏（token/refresh/answer/reference/hidden/expected/content/base64/email…），并做 PII 文本脱敏；测试覆盖畸形结构与上报失败。
- 公开 DTO：渲染前扫描 `reference_answer`/`hidden_cases`/`expected_output`，命中即拒绝渲染并抛错。
- 后端权威：所有被隐藏的入口都对应服务端 403，页面把 403 原样展示；前端可见性仅在 `src/auth/visibility.ts` 且 fail-closed。

## 未执行项与遗留

1. **端到端（真实后端）未执行**：B2/B3/B4 尚无实现，无法跑"登录→导入→提交→评测→查分→导出"。
2. **Monaco/ECharts 的真实浏览器行为未实测**：本环境无浏览器会话；测试通过注入加载器与降级分支覆盖交互逻辑。集成测试需在真实浏览器确认。
3. **`pnpm dev` 未执行**（同上）。
4. **构建 chunk 体积警告**未处理（见上）。
5. **缺口的 CHG 未提交**：需 MAIN 判定（B1 无权修改契约）。

## 文档影响检查

- 本次同步：`README.md`（状态表）、`requirements/traceability.md`、`testing/strategy.md`、`testing/cases.md`、`api/contract.md`、`collaboration/interfaces.md`、`collaboration/workpackages.md`、`collaboration/onboarding.md`、`architecture/models.md`、`database/schema.md`、`runbooks/local-development.md`、`AGENTS.md`、`.env.example`、本文件。
- 归档门禁：本阶段**尚不满足**归档条件（端到端与真实浏览器验证未执行），**不归档**。
- 上一阶段：`phase-00-initialization.md`（初始化，已完成其可执行部分）。
