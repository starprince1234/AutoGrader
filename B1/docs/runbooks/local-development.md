# B1 本地开发运行手册

## 环境变量

复制 `.env.example` 为 `.env`（不提交）：

| 变量 | 值 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | 缺省（空） | **API 源**（协议+主机），不是路径前缀：契约路径本身已含 `/api/v1`。缺省=同源（MAIN host 同时提供 `/app` 与 `/api`，见 ADR-0007）。仅当 host 在另一个源时才填，如 `http://127.0.0.1:8000` |
| `VITE_LOG_ENDPOINT` | 缺省（空） | 可选的前端日志上报端点。未配置时日志只落本地控制台（契约没有日志端点，B1 **不发明**一个） |

> **与 ADR-0007 的差异（需 MAIN 判定）**：ADR-0007 举例 `VITE_API_BASE_URL=/api`，而契约路径本身以
> `/api/v1` 开头——把 `/api` 当前缀会得到 `/api/api/v1/...`。B1 因此把该变量定义为"源"而非"前缀"，
> 并在 `docs/collaboration/interfaces.md` 记录了这处文档不一致。**未修改** MAIN 的任何文件。

> 若 MAIN host 尚未在本机运行，页面会显示鉴权/加载失败或空列表状态（**不吞错误**）；这是预期行为，不是故障。

## 安装依赖

Windows PowerShell 与 Linux 相同：

```powershell
corepack enable
pnpm install
```

> 实跑记录：本环境首次 `pnpm install` 因 pnpm 共享 store 位于工作区外而被文件沙箱拒绝
> （`ERR_PNPM_EPERM`），放宽文件策略后安装成功（详见 `../phases/active/phase-01-business-ui.md`）。
> 仓库已锁定 `pnpm-lock.yaml`。

## 启动开发服务器

```powershell
pnpm dev
```

默认地址 `http://localhost:5173`（以 `pnpm dev` 实际输出为准）。**本阶段未实跑**（无浏览器会话可观测）。

## 门禁命令（可复制，全部已实跑）

```powershell
pnpm lint          # 真实 ESLint（flat config），0 error / 0 warning
pnpm typecheck     # tsc -b，含从 MAIN OpenAPI 生成的类型
pnpm api:check     # 生成类型与 MAIN/contracts 是否漂移
pnpm test          # Vitest（jsdom + Testing Library）
pnpm build         # tsc -b && vite build → dist/
pnpm verify        # 依次跑上面五步
```

契约类型生成（改了 `MAIN/contracts/**` 之后必须跑）：

```powershell
pnpm api:generate  # 重新生成 src/api/generated/*.ts（禁止手改生成物）
```

`pnpm build` 产出 `dist/`，由 MAIN host 在 `/app` 静态提供。

## 验证 readiness（集成时）

1. `pnpm build` 成功且 `dist/index.html` 存在。
2. 启动 MAIN host 后访问 `/app`，看到业务页面。
3. 打开浏览器 Network 面板，确认 `/api/...` 请求同源、每个请求带 `X-Request-ID`。
4. 控制台应能看到结构化 JSON 日志行（含 `timestamp`/`level`/`event`/`request_id`），且**不含** token/答案。

## 常见端口/路径

| 项 | 值 |
|---|---|
| Vite dev 端口 | `5173`（默认） |
| 生产入口 | `dist/index.html`，MAIN host `/app` |
| API 路径 | 契约原文，如 `/api/v1/grades`（`VITE_API_BASE_URL` 只提供源） |
| 契约权威 | `../../../MAIN/contracts/**`（本地集成时） |
