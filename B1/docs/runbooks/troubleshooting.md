# B1 排障手册

> 原则：只收录**可确定**或**真实遇到过**的问题，不编造。

## 症状 → 原因 → 处置 → 确认恢复

### 1. 页面空白或只有加载/错误状态

- 可能原因：未登录（受保护路由显示登录提示）；或 MAIN host 未运行导致所有 API 请求失败。
- 处置：确认 host 是否提供 `/api`；页面上的错误横幅会给出可读原因与 `request_id`，用它去后端日志核对。
- 确认恢复：登录后能看到按角色过滤的导航；Network 面板中 `/api/v1/...` 返回 2xx。

### 2. `pnpm install` 报 `ERR_PNPM_EPERM`（写 pnpm store 被拒）

- 真实遇到：受限文件沙箱下，pnpm 的共享 store（本机为 `D:\dev\caches\pnpm\v10`）在工作区外，写入被拒绝。
- 处置：放宽文件策略后重新安装；**不要**把 store 改到工作区内——已安装的 `node_modules` 会因 store 位置变化而要求全量重装（`ERR_PNPM_UNEXPECTED_STORE`）。
- 确认恢复：`pnpm install` 退出码 0，`pnpm typecheck` 通过。

### 3. `pnpm api:generate` 报 `Unsupported schema format, expected 'openapi: 3.x'`

- 真实遇到：`openapi-typescript` 在 Windows 反斜杠路径下会误判文档格式。
- 处置：生成脚本用 `pathToFileURL()` 传 `file://` URL（见 `scripts/generate-api-types.mjs`）。
- 确认恢复：脚本输出 `generated src\api\generated\*.ts`，`pnpm api:check` 通过。

### 4. `pnpm typecheck` / `pnpm build` 报"找不到模块"或类型错误

- 可能原因：依赖未安装；或引入了手写契约字段类型（违反 ADR-0002）；或改了契约但未重新生成类型。
- 处置：`pnpm install` → `pnpm api:generate` → `pnpm typecheck`；手写字段类型改为引用 `src/api/types.ts` 的别名。
- 确认恢复：`pnpm typecheck` 退出码 0，`pnpm api:check` 通过。

### 5. 组件测试里 Monaco / ECharts 相关报错（`clearRect`、worker 相关）

- 原因：jsdom 没有 canvas / Web Worker，这两个库无法在测试环境真实初始化。
- 处置：组件已支持依赖注入——测试传 `editorLoader` / `chartLoader` 桩件（同时覆盖加载失败时的降级分支）。生产路径仍使用真实 Monaco/ECharts。
- 确认恢复：`pnpm test` 全绿，且**未跳过**任何断言。

### 6. 集成时 `/app` 404 或 `/api` 请求 404/502

- 可能原因：`pnpm build` 未执行、`dist/` 缺失（ADR-0007：`dist` 不存在时 host 不挂载 `/app`），或 MAIN host 未装配 B4/B3/B2 插件导致 `/api` 无路由。
- 处置：先 `pnpm build` 确认 `dist/index.html` 存在；再确认 host 已装配对应插件。
- 确认恢复：`/app` 返回页面；`/api/v1/...` 返回 Problem Details 而非网关错误。

### 7. 契约摘要漂移（集成门禁失败）

- 可能原因：`../../../MAIN/contracts/**` 被改动但 `contracts.lock` 未同步。
- 处置：**不要**手改 lock。按 `../../../MAIN/docs/contracts/change-protocol.md` 由 MAIN 重算摘要并同步四个模块；B1 侧随后跑 `pnpm api:generate`。
- 确认恢复：MAIN `checks.py` 通过，`contracts.lock` 摘要一致，`pnpm api:check` 通过。

### 8. 前端日志/页面出现 token 或答案内容

- 可能原因：日志器或展示层误把敏感内容写出。
- 处置：立即停止该路径；`src/logging/redaction.ts` 是**拒绝清单**（命中即整体替换），新增字段应加入该清单；展示层用 `src/ui/masking.ts`。
- 确认恢复：`pnpm test` 中 `logger.test.ts` 通过；日志与 DOM 中不再出现 token/答案/参考答案/PII。

## 已知遗留（非"故障"但需处理）

- **端到端未执行**：B2/B3/B4 仍是骨架，无可连后端；`pnpm dev` 也未实跑（无浏览器会话）。
- **构建体积警告**：Monaco 的 `editor.main` 约 3.7 MB，超过 Vite 默认 500 kB 阈值；未做按语言裁剪（原因见 `../phases/active/phase-01-business-ui.md`）。
- **契约缺口 3/4/5**（仍有若干 200 响应无 schema、成绩无汇总端点、无"当前用户资料"端点）：见
  `../collaboration/interfaces.md`，需 MAIN 判定 CHG。缺口 1（题目图形端点）、缺口 2（版本题干字段）
  与缺口 3 的 `listEnrollments`/`listAssignments` 部分**已关闭**。
- **等待 B4 落地**：「谁还没交」与作业中心的「提交情况」都依赖 `EnrollmentPublic.student_id`
  （CHG-0007 已声明）；B4 尚未返回该字段时这些人显示"未绑定账号"且不计入已提交/未提交的判定，
  这是预期的中间状态（**不**用 admin-only 的 `/api/v1/users` 绕过）。
- **名单确认的幂等键有一个已知边界**：键按"导入任务"复用（同一次确认流程内的重试带同一个键，
  见 `RosterImportPage.test.tsx`），但**预览与确认之间整页刷新**会重新生成键——键只在组件内
  （`useRef`）持有，没有写进 `localStorage`/`sessionStorage`，因为那会扩大 ADR-0007 允许的本地
  存储面（仅"未提交草稿"）。后果：若 B4 把"对已提交的导入再次确认"当成第二次写入，刷新后重试
  这条路径仍可能重复写入。**已请 MAIN 向 B4 确认**；在确认之前，若线上出现重复写入，先按
  `roster_imports` 列表判断该导入是否已 `committed`（B1 侧的确认按钮在 `committed` 状态下不渲染）。
- **自动翻页有上限**：判定类视图（谁还没交 / 提交情况 / 我的作业）会把分页取完，但上限 40 页
  （8000 行，`src/api/pagination.ts`）。到上限时页面按"未加载完"处理（只显示加载提示、不显示判定），
  这是**刻意的**：游标异常时不允许把浏览器拖进无限请求。看到这种状态先查服务端的 `next_cursor`。
- **ADR-0007 与 `.env.example` 的取值差异**：ADR-0007 举例 `VITE_API_BASE_URL=/api`，而契约路径已含 `/api/v1`；B1 把该变量定义为"源"，详见 `../api/contract.md`。
