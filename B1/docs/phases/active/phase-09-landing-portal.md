# Phase 09 教师端/学生端导览页与入口隔离

本文件记录本轮的真实改动、命令与未验证项。

## 需求变化

上一轮（phase-06）按任务书删除了独立首页，`/` 直接进入 `/assignments`。用户随后明确要求
"进入网站第一眼看到两栏：左教师端、右学生端"，并强调两端功能不能混。本轮据此把 `/` 改为
**导览页**；phase-06 中"根路径直接进作业页"的行为被本次需求取代，不是回退。

## 页面行为

- `/` 对匿名与已登录用户都显示两栏，不跳转、不先要求登录。
- 页面不显示页脚提示、欢迎语、版本号或开发者文案；底部只保留两栏卡片本身。
- 左栏教师端只列：作业管理、题库、班级成绩、课程与名单、复核工作区；
  右栏学生端只列：我的作业、我的成绩。两栏使用独立的数据数组，学生端不出现题库/课程/复核。
- 未登录点击入口：记录"入口端 + 目标路径"后进入登录；登录成功回到目标页。
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
| `src/features/home/LandingPage.tsx` | 两栏导览页、功能列表、登录记忆展示、退出登录 |
| `src/auth/portal.ts` | 入口端类型、一次性意图存储/读取、角色匹配规则 |
| `src/app/AppShell.tsx` | `/` 公开导览分支、入口端校验、角色不符说明页、切换账号 |
| `src/app/router.tsx` | `/` 从重定向改为 `LandingPage` |
| `src/i18n/index.ts`、`src/styles.css` | 导览页文案与两栏/窄屏样式 |
| `src/app/AppShell.test.tsx`、`src/features/home/LandingPage.test.tsx`、`src/auth/portal.test.ts` | 根入口、两栏隔离、登录记忆、错端拦截与意图存储测试 |

## 真实执行

| 命令 | 结果 |
|---|---|
| `node node_modules/typescript/bin/tsc -b`（B1） | 通过 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0`（B1） | 通过（0 error / 0 warning） |
| `node node_modules/vitest/vitest.mjs run`（B1 全量） | **48 个测试文件 / 412 个用例全部通过**（exit 0） |
| `node node_modules/vite/bin/vite.js build`（B1） | 通过 |
| 线上入口复核（提交 `b30e2b9`） | `https://www.91idc.me/app/` HTTP 200，页面引用 `/app/assets/index-CIBPK-9p.js`；该 bundle 含「教学管理」「学习任务」「当前账号没有教师端权限」与 `portal-mismatch`；`https://api.91idc.me/health/ready` 四个模块均 healthy，B4 迁移头 `20261027_0005` |

契约没有变化（纯前端），`contracts.lock` 与后端无需更新。

## 未验证

- 1440 / 768 / 390 px 的真实浏览器截图、深色模式与触摸操作未验收。
- 未使用真实教师/学生账号走"导览 → 登录 → 回目标页"的完整线上闭环；该路径由
  LandingPage + AppShell + LoginPage 的集成测试组合覆盖。线上只确认了构建产物包含导览页，
  未做真实账号的浏览器点击复核。
- 未对生产部署后的导览页做视觉复核（部署结果以推送后的 Vercel 状态为准）。

## 回滚

删除 `LandingPage`、`portal.ts` 与本文档，把 `/` 的路由恢复为重定向到 `/assignments`，
并移除 AppShell 的入口端校验与说明页；不涉及后端或数据库回滚。
