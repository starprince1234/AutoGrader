# AutoGrader B1 —— AI Agent 执行规则

面向在本仓库执行任务的 AI Agent。简短、可执行，不是百科。详情见 `docs/`。

## 开始任务前

1. 读 [`docs/README.md`](docs/README.md)（权威表 + 状态表）。
2. 读 [`../MAIN/docs/workstreams/B1.md`](../MAIN/docs/workstreams/B1.md)（任务书）。
3. 确认契约版本：`contracts.lock` 的 `contract_version` 与 `digest`；与 `../MAIN/contracts` 不一致时**先停**，走变更规程。
4. 检查 Git 状态，确认没有未提交的共享文件（路由表、API 客户端、样式变量）改动冲突。

## 我负责什么 / 不负责什么

- 我拥有：浏览器内的一切展示与交互；未提交草稿（仅浏览器本地）；前端路由与角色**可见性**控制。
- 我不拥有：权威学生/课程/成绩/题目/评测数据（属于 B4/B3/B2）；持久化存储；服务端业务逻辑。
- 我禁止：连接 PostgreSQL/NATS；导入 B2/B3/B4 代码或 ORM；自行定义 API 字段；编造 mock 成功响应充当交付；令牌写 localStorage。

## 硬规则

1. **单一权威**：API/事件/JSON Schema 字段只看 `../MAIN/contracts/**`，禁止在文档或代码中手抄字段表。
2. **不夸大**：只写 `骨架` / `待实现` / `已完成` / `阻塞` 四种状态；未跑通的命令不得写成通过。
3. **失败留在文档**：缺口、阻塞、未执行项写入 `docs/phases/active/`，不得隐藏。
4. **安全**：不执行题干脚本/内联事件（富文本必须白名单渲染）；日志与前端存储不含 token、参考答案、隐藏用例、超范围 PII。
5. **事件**：SSE 进度按 `event_id`/序号去重；断线重连不得重复展示历史进度。
6. **禁止** mock 成功响应、兼容层、跨 schema、shell 字符串执行。

## 文档同步

改代码时同步更新：`docs/requirements/traceability.md`（状态/证据）、`docs/phases/active/`（实际命令与结果）、`docs/architecture/*`（若改组件/状态机）、`docs/api/*`（若改消费关系）、`docs/testing/cases.md`（若新增/改测试）。

## 验证

必须真实跑通（均已实跑通过，结果见 `docs/phases/active/phase-01-business-ui.md`）：

```powershell
pnpm lint          # 真实 ESLint（flat config），0 error / 0 warning
pnpm typecheck     # tsc -b（含从 MAIN OpenAPI 生成的类型）
pnpm api:check     # 生成类型与 MAIN/contracts 的漂移门禁
pnpm test          # Vitest（jsdom + Testing Library）
pnpm build         # tsc -b && vite build
pnpm verify        # 一键串联上面五步
```

未执行、不得写成已通过：端到端（Playwright，需真实 B2/B3/B4）、`pnpm dev`（需浏览器会话）。

## 契约变更

发现契约与实现不一致，或需要契约里没有的字段/端点：**不**自行改 `../MAIN/contracts/**`，不偷偷改实现对齐。按 [`../MAIN/docs/contracts/change-protocol.md`](../MAIN/docs/contracts/change-protocol.md) 提交 CHG，由 MAIN 判定。
