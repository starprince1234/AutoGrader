# B1 测试策略

## 现状（已实跑，如实记录）

`package.json` 的门禁脚本与真实结果：

| 命令 | 内容 | 实跑结果 |
|---|---|---|
| `pnpm lint` | `eslint . --max-warnings 0`（flat config + typescript-eslint + react-hooks + react-refresh + 3 条自定义限制） | **通过**（0 error / 0 warning） |
| `pnpm typecheck` | `tsc -b --pretty false`（含从 MAIN OpenAPI 生成的类型） | **通过** |
| `pnpm api:check` | 生成类型与 `MAIN/contracts` 的漂移门禁 | **通过**（"API types match MAIN/contracts."） |
| `pnpm test` | `vitest run`（jsdom + Testing Library） | **通过**：23 文件 / 133 用例 |
| `pnpm build` | `tsc -b && vite build` | **通过**（产物 `dist/`） |
| `pnpm verify` | 上述五步串联 | **通过** |

## 分层与现状

| 层 | 目标 | 工具 | 当前状态 |
|---|---|---|---|
| 静态检查 | 类型正确、无手写契约类型漂移 | `tsc -b` | 可执行（含生成类型） |
| 代码规范 | 统一风格、禁用危险 API（innerHTML、令牌入本地存储、跨模块导入） | ESLint | 可执行 |
| 单元测试 | 纯函数：白名单解析、URL 判定、校验规则、SSE 去重、草稿序列化、汇总口径、日志脱敏 | Vitest | 可执行 |
| 组件测试 | 页面/组件交互与可见性、并发冲突、下钻、导出触发、SSE 终态 | Vitest + Testing Library | 可执行 |
| 契约对齐 | 生成的类型与契约一致 | `pnpm api:check` + typecheck | 可执行 |
| 端到端（集成） | 登录→导入→发布→提交→评测→查分→导出 | Playwright | **未执行**：B2/B3/B4 仍是骨架，没有可连的真实后端 |

## 每层必须覆盖的风险（不是按文件机械建测试）

1. **静态检查**：契约类型生成后任何手写字段类型都要被 `tsc` 报错。
2. **富文本**：XSS（脚本/内联事件/`javascript:` 链接被拦截）、图片与链接 URL 白名单。
3. **提交**：幂等键生成与复用、超限前端拦截、路径与摘要构造、状态机展示（未完成不显示 0 分）。
4. **SSE**：`event_id` 与 `sequence` 去重、断线重连不重复展示、终态后丢弃过期 progress。
5. **权限可见性**：学生不可见编辑/导出/名单入口（注意：这只是体验，不是安全；"后端是否拒绝"归 B4/B2 契约测试），角色未知时 fail-closed。
6. **草稿**：断网恢复、提交后清除、草稿≠已提交、存储中无令牌。
7. **日志**：结构化、含 `request_id`、不含 token/答案/超范围 PII；上报失败不阻塞主流程。

## 使用真实基础设施的要求

- B1 无数据库，**不存在**"用 SQLite 代替 PostgreSQL"的问题。
- 单元/组件测试用**契约测试夹具**驱动（`acceptance.md` 的前置条件允许夹具作为后端），
  夹具只隔离不可控的 HTTP 边界与 jsdom 缺失的浏览器能力（Monaco/ECharts 加载器以依赖注入替换，
  并同时测试加载失败时的降级分支）。**禁止**用 mock 成功响应充当"运行时功能已验证"的交付证明。
- 端到端测试须连接真实 B4/B3/B2（或 MAIN host 装配的集成环境），业务阶段完成后执行。

## 门禁命令（可复制）

```powershell
pnpm lint
pnpm typecheck
pnpm api:check
pnpm test
pnpm build
pnpm verify   # 一键串联
```

## 夹具与生成物的边界

| 目录 | 性质 | 规则 |
|---|---|---|
| `src/api/generated/**` | 从 `MAIN/contracts/openapi/*.yaml` 生成 | **禁止手改**；改契约后跑 `pnpm api:generate`；`pnpm api:check` 拦截漂移 |
| `src/testing/**` | HTTP 边界与契约形状夹具 | 只允许被 `*.test.ts(x)` 引用；不作为交付业务实现 |
