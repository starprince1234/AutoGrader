# B1 本地开发运行手册

## 环境变量

复制 `.env.example` 为 `.env`（不提交）：

| 变量 | 值 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | 本地设置为 `http://127.0.0.1:18000` | **API 源**（协议+主机），不是路径前缀：契约路径本身已含 `/api/v1`。生产构建值为 `https://api.91idc.me`。 |
| `VITE_LOG_ENDPOINT` | 缺省（空） | 可选的前端日志上报端点。未配置时日志只落本地控制台（契约没有日志端点，B1 **不发明**一个） |

> 生产前端与 API 使用不同 origin；本地 Vite 也通过 API 源直接连接 MAIN host。请求会携带
> `credentials: include`，本地浏览器使用 `127.0.0.1:5173` 访问 Vite，与 `127.0.0.1:18000` API 保持 same-site。

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
$env:VITE_API_BASE_URL = "http://127.0.0.1:18000"
pnpm dev
```

使用 `http://127.0.0.1:5173`。**本阶段未实跑**（无浏览器会话可观测）。

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

`pnpm build` 产出 `dist/`；生产由 Vercel 发布到 `https://www.91idc.me/app/`。

## 验证 readiness（集成时）

1. 启动本地 MAIN integration stack（默认 API `http://127.0.0.1:18000`）。
2. 以 `VITE_API_BASE_URL=http://127.0.0.1:18000` 运行 Vite，看到业务页面。
3. 打开浏览器 Network 面板，确认 `/api/...` 请求到 API origin、每个请求带 `X-Request-ID`。
4. 控制台应能看到结构化 JSON 日志行（含 `timestamp`/`level`/`event`/`request_id`），且**不含** token/答案。

## 常见端口/路径

| 项 | 值 |
|---|---|
| Vite dev 端口 | `5173`（默认） |
| 生产入口 | `https://www.91idc.me/app/`（Vercel） |
| API 路径 | 契约原文，如 `/api/v1/grades`（`VITE_API_BASE_URL` 只提供源） |
| 契约权威 | `../../../MAIN/contracts/**`（本地集成时） |
