# 运行手册：本地开发

本手册面向**第一次在本机跑起来**的开发者。所有命令可复制执行。平台分为两种：**Windows（开发与契约检查）** 与 **Linux（完整验收）**。

> **只想把系统跑起来**（交付、演示、验收）请直接用容器路径：[`container-deployment.md`](container-deployment.md)。一条
> `docker compose up -d --build`，不需要本机 Python/Node，也不需要任何未跟踪的 env 文件。本手册描述的是**改代码**时的开发路径。

配套：`container-deployment.md`（容器交付）、`integration.md`（组装运行）、`linux-acceptance.md`（Linux 专属验收）、`troubleshooting.md`（故障处置）。

---

## 0. 前置条件

| 工具 | 版本 | 用途 | 检查 |
|---|---|---|---|
| Python | **3.12.x**（`>=3.12,<3.13`） | 后端模块与 host | `python --version` |
| uv | 最新 | Python 依赖与虚拟环境 | `uv --version` |
| Node.js | **24** | 前端构建 | `node --version` |
| pnpm | 通过 corepack | 前端依赖 | `corepack enable` 后 `pnpm --version` |
| Docker + Compose v2 | 最新 | PostgreSQL / NATS | `docker compose version` |
| Git | 最新 | 版本控制 | `git --version` |
| Linux 专属：nsjail + cgroup v2 | — | 沙箱评测 | 见 `linux-acceptance.md` |

> **端口约定**（避免与本机既有服务冲突）：PostgreSQL 宿主 **55432**、NATS 宿主 **54222**、host **18000**、NATS 监控 **8222**（容器内）。

---

## 1. 目录与"五个独立项目"的关系

```text
AutoGrader/
├── MAIN/   契约权威 + composition host + 平台 SDK + 文档基座
├── B1/     静态前端（React/TypeScript）
├── B2/     评测插件（Python）
├── B3/     题库插件（Python）
└── B4/     平台插件（Python）
```

**五个目录是五个独立的 Git 项目**，不是五个子目录包。开发时各自 `cd` 进去按自己的 README 操作；**只有集成运行时** MAIN 才需要看到其他目录。

---

## 2. Windows 开发流程

### 2.1 MAIN：契约与门禁检查（最先做，最短路径）

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN

# 正常环境
uv sync --all-groups --extra modules
uv run python -m autograder_host.checks
uv run pytest
```

**若 `uv` 报缓存目录无法访问**（本机沙箱下 `D:\dev\caches\uv` 在工作区之外，会出现 `failed to open file ... os error 5`，等价于权限被拒），使用仓库内既有虚拟环境执行等价检查：

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

**成功标志**（当前应看到）：

```text
contracts, manifests and requirements: ok (<64 位十六进制摘要>)
module documentation governance and links: ok
```

该命令同时校验：契约示例通过归属 schema、四个 `module.yaml` 合法无冲突、四份 `contracts.lock` 与实时摘要一致、32 条需求编号完整、各模块文档集合完整、文档 UTF-8 与链接有效。

### 2.2 各后端模块（B2 / B3 / B4）

```powershell
cd D:\VScodeProjects\AutoGrader\B2      # 或 B3、B4
uv sync --all-groups --extra modules
uv run ruff check .
uv run pytest
```

数据库迁移需要可用的 PostgreSQL：

```powershell
uv run alembic upgrade head
```

> ⚠️ 迁移需要 `DATABASE_URL`。参照本模块 `.env.example` 取变量名，**不要**把真实口令写进仓库。

**当前状态**：各模块只有插件骨架，测试为最小插件测试。迁移为空的基线迁移。

### 2.3 B1 前端

```powershell
cd D:\VScodeProjects\AutoGrader\B1
corepack enable
pnpm install
pnpm typecheck
pnpm build
```

**成功标志**：`dist/` 目录生成，含 `index.html`。

> ⚠️ **已知缺口**：`pnpm lint` 目前只是 `tsc -b`（没有 ESLint）；`pnpm test` **不存在**（没有 vitest / 组件测试）。这两项是本阶段待补的工具链，**不得**在文档或汇报中写成已通过。

本地开发时用 Vite dev server：

```powershell
$env:VITE_API_BASE_URL = "http://127.0.0.1:18000"
pnpm dev
```

Vite 浏览器请求直接连接 MAIN API origin；`AUTOGRADER_CORS_ORIGINS` 默认允许本地 Vite 来源。生产 API origin 由 `B1/.env.production` 配置。

### 2.4 启动基础设施

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose up -d postgres nats
docker compose ps
```

**成功标志**：`postgres` 显示 `healthy`；`nats` 运行中。

验证：

```powershell
# PostgreSQL（容器内自检）
docker compose exec postgres pg_isready -U autograder_platform -d autograder

# NATS 监控端口
curl http://localhost:8222/varz
```

> **注意**：`infra/postgres/init.sql` 创建 `b2`/`b3`/`b4` 三个 schema、四个最小权限登录角色，并只授予 **schema 级** `USAGE`。**表级**授权来自各模块迁移（b3 自己发；b2/b4 由 `init.sql` 的默认权限与 `python -m autograder_b4.ops.provision` 覆盖），因此"跨 schema 访问被拒"要在迁移跑完之后才能验证。容器路径会自动完成这一串（见 `container-deployment.md` §2）；Windows 路径需要手工按顺序执行。

### 2.5 启动 composition host

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose up -d --build host
```

或本地直接跑（需已安装三个模块）：

```powershell
uv run uvicorn autograder_host.main:app --host 127.0.0.1 --port 18000
```

**成功标志**：

```powershell
curl http://localhost:18000/health/live     # {"status":"ok"}
curl http://localhost:18000/health/ready    # ready=true，modules 列出已装载模块
```

### 2.6 端到端本地检查清单

1. `.venv\Scripts\python.exe -m autograder_host.checks` 通过。
2. `docker compose ps` 中 `postgres` 为 healthy。
3. `/health/live` 返回 `ok`。
4. `/health/ready` 返回 `ready=true` 并列出 **B2、B3、B4**。
5. 设置 B1 `VITE_API_BASE_URL=http://127.0.0.1:18000`，通过 Vite 在 `http://127.0.0.1:5173` 运行前端，保持本地 API cookie 的 same-site 语义。
6. 浏览器访问 Vite 页面，确认 API 请求通过本地 host 且 CORS 放行开发来源。

> 第 4 项**当前尚未验证**（见 `../testing/acceptance.md` §2.3）。若 `/health/ready` 的 `modules` 为空，说明没有模块被装载 —— 见 `troubleshooting.md`。

---

## 3. Linux 验收流程（摘要）

完整步骤见 `linux-acceptance.md`。最小路径：

```bash
cd MAIN
uv sync --all-groups --extra modules
uv run python -m autograder_host.checks
docker compose up -d
curl -s localhost:18000/health/ready
# 安全与资源门禁需要 cgroup v2 + nsjail，见 linux-acceptance.md
```

---

## 4. 环境变量

所有变量只列名字；取值见各项目 `.env.example`，**占位值不可用于生产**。

| 项目 | 变量 | 用途 |
|---|---|---|
| MAIN | `DATABASE_URL` | host 连接 PostgreSQL |
| MAIN | `NATS_URL` | host 连接 JetStream |
| MAIN | `AUTOGRADER_MODULES` | 以 `module:factory` 形式补充插件（逗号分隔），用于未安装为 entry point 的开发场景 |
| MAIN | `AUTOGRADER_CORS_ORIGINS` | 可访问本地 API 的前端来源 |
| B1 | `VITE_API_BASE_URL` | API origin（本地 `http://127.0.0.1:18000`；生产 `https://api.91idc.me`） |
| B2/B3/B4 | 见各自 `.env.example` | 数据库连接、NATS、服务凭据等 |

**硬规则**：密钥只来自环境或密钥文件；`.env` 必须被 gitignore；启动时若检测到弱默认口令用于生产模式应**拒绝启动**；任何凭据不得进入日志、事件、导出或仓库。

---

## 5. 端口冲突处置

| 现象 | 原因 | 处置 |
|---|---|---|
| `55432` 被占用 | 本机已有 PostgreSQL | 改宿主端口映射（同时改 `DATABASE_URL` 的端口） |
| `54222` 被占用 | 本机已有 NATS | 改映射并同步 `NATS_URL` |
| `18000` 被占用 | 已有 host 或旧进程 | 停止旧进程，或改端口映射 |
| 旧数据卷冲突 | 存在历史卷 | 使用**本系统命名**的卷；`prodction_postgres_data` 等旧卷**不属于新系统**，未经明确授权**不得**读取、迁移或删除 |

---

## 6. 停止与清理

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose down            # 停止容器，保留卷
docker compose down -v         # 停止并删除本系统的卷（会丢数据，谨慎）
```

**每次评测结束后必须确认**：沙箱临时工作区已清理、无残留沙箱进程。残留进程会污染后续评测（见 `troubleshooting.md`）。

---

## 7. 本手册未验证的部分（如实声明）

| 项 | 状态 |
|---|---|
| `uv sync` / `uv run` 全流程 | **未在本机验证**（uv 缓存目录不可写） |
| `docker compose up` | **未执行** |
| `/health/ready` 与 `/app` | **未执行** |
| PostgreSQL 迁移（`alembic upgrade head`） | **未执行** |
| 各模块 ruff / pytest | **未执行** |
| B1 `pnpm install` / `build` | **未执行**（`node_modules` 与 `dist` 存在，但未在本轮重新构建） |
| nsjail 与 cgroup v2 相关步骤 | **未执行**（需 Linux） |

这些必须在功能阶段用真实执行结果替换。**不得**以"按文档应该能跑通"代替验证。
