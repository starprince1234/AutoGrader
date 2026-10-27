# Phase 06 入口与全局外壳重做（P01–P03）

> 后续变化：本文的"根入口"部分已被 `phase-09-landing-portal.md` 取代——`/` 不再重定向到
> 作业页，而是双列导览页；其余登录、注册与外壳设计仍按本文执行。

本文件记录本轮**真实执行**的改动、命令与未验证项。状态词只用 `已完成` / `部分` /
`未验证`；没跑过的命令不写成通过。范围：根入口、登录、注册，以及全站共用的
`AppShell`、导航、账户菜单、抽屉、设计变量与通用控件。

## 目标与取舍

线上页面给人的第一印象是"欢迎页 + 功能磁贴 + 当前会话卡 + 契约版本 + 课程作用域 UUID"，
管理员还看到 11 个平铺导航链接。本轮的判断标准只有一条：**用户打开网站应当直接开始工作**。
因此做了这些取舍：

1. **删除独立首页**（`src/features/home/HomePage.tsx` 整体删除）。`/` 改为 `beforeLoad`
   重定向到 `/assignments`；登录后一律进入作业页，不存在需要用户理解的"工作台"。
   同时删除 `home.*`、`app.subtitle`、`app.contract` 等只为首页/顶栏服务的文案与
   `hero/tiles/tile/eyebrow` 样式。
2. **认证页使用独立布局**（`AuthLayout`）。公开页不再渲染应用侧栏、账户菜单、退出按钮与
   契约版本；页顶只有产品名，已登录用户在跳走的那一帧还能看到「返回作业」出口。
3. **导航按角色收敛**（`src/app/navigation.ts`）：学生 2 项（作业、成绩），教师/助教/
   管理员 4 项（作业、题库、成绩、课程）；角色未知 fail-closed 只留非特权项；
   「用户管理」只在管理员账户菜单，不占侧栏。
4. **会话恢复前只渲染骨架**：`AppShell` 在 `isSessionReady()` 之前不渲染 Outlet、导航或
   身份，匿名用户不会先看到一帧受保护内容。
5. **深链回跳只接受站内路径**：`safeRedirectTarget`（`src/auth/redirect.ts`）拒绝
   `//host`、绝对 URL、反斜线及其编码变体、控制字符、`/login`、`/register` 与根路径；
   登录成功后用 `navigate({ href })` 回到原页（已用带 basepath 的测试确认最终 URL 为
   `/app/...`，不会出现 `/app/app/...`）。
6. **注册页按"身份信息 / 设置密码"分组**：单列、桌面 550px 上限；验证码与「发送验证码」
   同一行（窄屏上下排列）；只保留一条常驻帮助文字「学号需与课程名单一致。」。
   所有格式错误落在字段下；服务端 409/422 的中文 `detail` 映射到具体字段（用户名/邮箱/
   学号/验证码/密码），映射不到的保留为表单级原因并带 `request_id`。
7. **共享控件补可访问性关系**：`Field` 把 hint/error 通过 `aria-describedby` 挂到控件上，
   仅在出错时声明 `aria-invalid`；新增共享 `Drawer`（焦点进入/Escape/遮罩/焦点归还/
   背景滚动锁定），小屏导航复用它。

## 实际变更

| 能力 | 文件 | 说明 |
|---|---|---|
| 根入口 | `src/app/router.tsx` | `/` 的 index route 用 `redirect({ to: "/assignments" })`；`validateLoginSearch` 复用 `safeRedirectTarget`；`registered` 兼容查询串把 `1` 解析成数字的情况 |
| 站内回跳校验 | `src/auth/redirect.ts`、`src/auth/redirect.test.ts` | 逐条规则与编码展开（最多三层），最后用 `URL` 解析核对 origin |
| 应用外壳 | `src/app/AppShell.tsx`、`src/app/AuthLayout.tsx`、`src/app/AccountMenu.tsx` | 页头（产品名 + 账户菜单）、232px 侧栏、移动抽屉、会话骨架；退出清空 React Query 缓存并进入**不带 redirect** 的登录页 |
| 角色导航 | `src/app/navigation.ts`、`src/app/navigation.test.ts` | 学生/教师/管理员与未知角色；`isMainNavActive` 覆盖子页面且不误判同前缀词 |
| 登录 | `src/features/auth/LoginPage.tsx`、`src/features/auth/LoginPage.test.tsx` | 空字段就地报错、401 统一文案、429/5xx/网络分开、提交中只发一次；成功回原深链 |
| 注册 | `src/features/auth/RegisterPage.tsx`、`registrationValidation.ts`、`registrationErrors.ts` + 测试 | 分组表单、字段级校验、发码冷却、服务端原因映射、失败后保留身份字段并清空密码/验证码 |
| 契约类型 | `src/api/types.ts`、`src/features/auth/registrationApi.ts` | 注册请求/响应改用生成类型（移除手写 DTO）；没有修改 `MAIN/contracts/**` |
| 共享组件 | `src/ui/components.tsx`、`src/ui/components.test.tsx` | `Field` 的 `hint`/错误关联；`Drawer` 三种关闭方式与焦点归还 |
| 全局样式 | `src/styles.css` | 新外壳/认证页/抽屉/账户菜单/骨架样式；删除 hero/tiles/topbar/auth-gate 等旧规则；`@media (max-width: 767px)` 切抽屉；补 `prefers-reduced-motion` |
| 旧页面入口 | `RosterPage`、`AssignmentsPage`、`QuestionDetailPage` | 名单导入、隔离区复核、题目质量改为页面内的次级入口；不再占主导航。注册名单的独立入口已由 P14 合并进 `/admin/users` 标签，不再单独链接 |
| 测试辅助 | `src/testing/renderWithProviders.tsx`、`vitest.setup.ts` | 新增 `renderWithRouter`（需要 `Link` 的页面测试）；jsdom 的 `scrollTo` 置空 |

**保留** `src/features/roster-import/**`、`src/features/intake/**`、`src/features/quality/**` 与对应路由：
合并后的 `/roster`、`/review` 由其他成员负责，本轮**没有删除**这些页面或测试；它们的入口从主导航移到
所属页面（课程 → 名单导入、作业 → 隔离区复核、题目详情 → 题目质量）。

本分支已 rebase 到 `origin/main`（含 P14 的 7 个提交）。合并时按"谁的页面谁负责"处理冲突：
`/admin/users?tab=users|allowlist`、`AdminUsersPage`、`RegistrationAllowlistPanel` 与用户管理相关
i18n 采用上游版本（注册名单独立页面与路由在上游已删除，本地的次级链接也随之撤掉）；
`AppShell` 与 `RegisterPage` 采用本轮的完整重做版本（上游对这两处的改动分别只是删除一个旧导航项
与调整注册字段顺序，其功能意图已由本版本覆盖）；`router.tsx`/`routeComponents.tsx` 同时保留
根入口重定向、`notFound`、安全回跳与 P14 的标签查询串。

## 真实执行的命令与结果

在 `B1/` 下用仓库依赖直接执行（本机 `pnpm` 由 DSH runner 以管道启动时被沙箱拒绝，
改用 `node node_modules/...` 等价命令；依赖已按 `pnpm-lock.yaml` 安装）：

| 命令 | 结果 |
|---|---|
| `node node_modules/typescript/bin/tsc -b --pretty false` | 通过（exit 0） |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | 通过（0 error / 0 warning） |
| `node node_modules/vitest/vitest.mjs run` | **42 个测试文件 / 360 个用例全部通过**（exit 0，rebase 后的合并结果） |
| `node node_modules/vite/bin/vite.js build` | 通过（`✓ built`，只有既有的 >500kB chunk 警告） |
| `node scripts/generate-api-types.mjs --check` | 生成物与 `MAIN/contracts` 一致；但**在 CRLF 工作区直接运行会误报漂移**（脚本按 LF 生成后与 CRLF 文件逐字比较）。先把生成文件临时转成 LF 再跑得到 `API types match MAIN/contracts.`，随后恢复 CRLF |

定向测试集（本轮新增/修改，均通过）：`src/auth/redirect.test.ts`、`src/app/navigation.test.ts`、
`src/app/AppShell.test.tsx`、`src/app/routerBasepath.test.ts`、
`src/features/auth/LoginPage.test.tsx`、`src/features/auth/RegisterPage.test.tsx`、
`src/features/auth/registrationValidation.test.ts`、`src/features/auth/registrationErrors.test.ts`、
`src/ui/components.test.tsx`、`src/features/roster/RosterPage.test.tsx`、
`src/features/users/UsersPage.test.tsx`。

## 未验证项

- **没有真实浏览器视觉验收**：本轮环境没有浏览器自动化，1440/768/390px 截图、深色模式、
  软键盘遮挡与真实触控未复核；这三档视口当前只有 CSS 规则与组件测试，不等于看过页面。
- **没有对着运行中的 B4 走登录/发码/注册**：验证码邮件、名单内外、429 冷却、密码策略与
  `registered` 提示均只由 HTTP 边界桩验证。真实账号、真实邮件与真实名单归属未实测。
- **教师/助教账号不可用**：角色导航由单元测试（`navigation.test.ts`）与合成会话验证，
  没有用真实教师/助教账号登录线上站点。
- **没有部署**：本轮未构建后发布到线上，也未做线上回归。
- 旧路由合并到 `/review`、`/admin/users` 的页面整合属于其他成员的交付，本轮只提供入口，
  未验证合并后的 tab 行为。
