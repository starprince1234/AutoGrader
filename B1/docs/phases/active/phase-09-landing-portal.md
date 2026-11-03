# Phase 09 教师端/学生端导览页与入口隔离

本文件记录本轮的真实改动、命令与未验证项。

## 需求变化

上一轮（phase-06）按任务书删除了独立首页，`/` 直接进入 `/assignments`。用户随后明确要求
"进入网站第一眼看到两栏：左教师端、右学生端"，并强调两端功能不能混。本轮据此把 `/` 改为
**导览页**；phase-06 中"根路径直接进作业页"的行为被本次需求取代，不是回退。

## 页面行为

- `/` 对匿名与已登录用户都显示两栏，不跳转、不先要求登录。
- 页面不显示页脚提示、欢迎语、版本号或开发者文案；底部只保留两栏卡片本身。
- 页头登录按钮与品牌文字在同一水平线上（按钮内部垂直居中），不再出现文字偏高。
- 左栏教师端只列：作业管理、题库、班级成绩、课程与名单、复核工作区；
  右栏学生端只列：我的作业、我的成绩。两栏使用独立的数据数组，学生端不出现题库/课程/复核。
- 未登录点击入口：**不离开导览页**，在当前页打开登录浮窗；登录成功后直达所点功能。
- 从教师端入口用学生账号登录（或反过来）时，浮窗在导航前拒绝：撤销刚建立的会话、
  清空密码并提示「该账号没有教师端/学生端权限」，不进入错误一端的页面。
- 进入系统后右上角不设下拉菜单：直接显示用户名，旁边就是「退出登录」按钮。
- 点击「退出登录」先回到导览页再清会话，不再退回独立登录页。
- 独立登录页已删除：`/login` 只作为旧链接/收藏的兼容地址，带原查询串重定向回 `/`，
  由导览页打开登录浮窗。
- 注册成功、密码重置成功和匿名深链都回到 `/`，分别由 `registered`、`reset`、`redirect`
  查询串触发浮窗；浮窗里的成功提示与深链目标保持不变。
- 管理员多出的「用户管理」作为普通入口放进侧栏，不再藏在账户菜单里。
- 最近登录过（refresh cookie 仍有效）：AppShell 恢复会话后，导览页显示已登录身份，
  点击本端入口直接进入，不额外登录。
- 已登录但角色不匹配：该端入口禁用并说明原因，**不会用教师账号打开学生视图**。
- 匿名用户登录后若角色与所点入口不符：AppShell 显示"当前账号不能进入该入口"，
  提供返回导览或退出并切换账号；刷新同一路径仍会被拦截。

## 入口端校验（为什么需要 sessionStorage）

只靠 `?redirect=` 回到目标页无法阻止"教师账号从学生入口登录后看到教师视图"。因此点击
导览页入口时，把 `{portal, path}` 作为一次性意图写入 `sessionStorage`（不含用户名、角色或
令牌），AppShell 在目标路径校验：

- 角色匹配 → 清掉意图，正常渲染；
- 角色不匹配 → **保留**意图并显示说明页，刷新不绕过；
- 导航到其它路径 → 旧意图路径不匹配，自动丢弃；
- 回到导览页 → 清掉旧意图，重新选择。

学生端只接受纯学生账号：同时持有教师/助教/管理员角色的账号一律走教师端，避免页面按教师
角色渲染却从学生入口进入。

## 改动文件

| 文件 | 作用 |
|---|---|
| `src/features/home/LandingPage.tsx` | 两栏导览页、功能列表、登录浮窗（含查询串触发的回跳/成功态）、登录记忆、退出登录 |
| `src/features/auth/LoginForm.tsx` | 浮窗与回跳入口共用的登录表单（校验、错误分类、密码可见性） |
| `src/auth/portal.ts` | 入口端类型、一次性意图存储/读取、角色匹配规则 |
| `src/app/AppShell.tsx` | `/` 公开导览分支、匿名深链回导览、入口端校验、角色不符说明页、退出回导览 |
| `src/app/AccountMenu.tsx`、`src/app/navigation.ts` | 右上角用户名 + 直接退出；管理员用户管理进入侧栏 |
| `src/app/router.tsx` | `/` 承载导览与认证查询串；删除独立登录页路由，`/login` 改为重定向 |
| `src/ui/components.tsx` | `Modal` 支持紧凑尺寸与初始焦点 |
| `src/i18n/index.ts`、`src/styles.css` | 导览页/登录浮窗/账户区文案与样式 |
| `src/app/AppShell.test.tsx`、`src/features/home/LandingPage.test.tsx`、`src/features/auth/LoginForm.test.tsx`、`src/auth/portal.test.ts`、`src/app/navigation.test.ts` | 根入口、浮窗登录、登录页删除后的回跳/成功态、两栏隔离、登录记忆、错端拦截与意图存储测试 |

## 真实执行

| 命令 | 结果 |
|---|---|
| `node node_modules/typescript/bin/tsc -b`（B1） | 通过 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0`（B1） | 通过（0 error / 0 warning） |
| `node node_modules/vitest/vitest.mjs run`（B1 全量） | **51 个测试文件 / 456 个用例全部通过**（exit 0；含上游合入用例） |
| `node node_modules/vite/bin/vite.js build`（B1） | 通过 |
| 线上入口复核（提交 `20ccdc2`） | `https://www.91idc.me/app/` HTTP 200，页面引用 `/app/assets/index-CtScbMTo.js` 与 `/app/assets/index-Dh050zIm.css`；bundle 含「教师端登录」「学生端登录」「account__logout」，**不含** `account-menu` 与 `LoginPage`；Vercel 部署状态 `success`；`https://api.91idc.me/health/ready` 四个模块均 healthy |

契约由上游合并升到 `0.24.0`（含课程/名单、人工评分与用户管理相关接口）；B1 已重新生成
`src/api/generated/*` 并通过 `api:check`。本功能的浮窗登录与账户区不改变契约。

## 未验证

- 1440 px 的登录浮窗与页头已用本地构建截图复核；768 / 390 px、深色模式与触摸操作未验收。
- 未使用真实教师/学生账号走"导览 → 浮窗登录 → 回目标页"的完整线上闭环；该路径由
  LandingPage + AppShell + LoginForm 的集成测试组合覆盖。线上只确认了构建产物，未做真实账号的
  浏览器点击复核。
- 未对生产部署后的导览页做视觉复核（部署结果以推送后的 Vercel 状态为准）。

## 回滚

删除 `LandingPage`、`portal.ts` 与本文档，把 `/` 的路由恢复为重定向到 `/assignments`，
并移除 AppShell 的入口端校验与说明页；不涉及后端或数据库回滚。
