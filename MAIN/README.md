# AutoGrader MAIN

**契约权威 + 集成宿主 + 文档基座。**

MAIN 不实现任何业务功能，也不拥有任何业务数据。它的职责是三件事：

1. **定义接口**：`contracts/` 下的 OpenAPI / AsyncAPI / JSON Schema 是四个模块之间唯一的机器可读权威。
2. **组装系统**：一个 composition host 按 capability DAG 装载 B2/B3/B4 插件；B1 独立部署到 Vercel，见 [ADR-0017](docs/adr/ADR-0017-vercel-frontend-native-api-host.md)。
3. **提供集成工具**：维护契约检查、应用组装和部署脚本；开发按影响验证，文档与历史验收记录不阻塞上线。

> 一句话：**MAIN 是微内核，不是业务服务。** 删除任一后端模块，host 会因缺失 capability 而明确失败——这正是"即插即用"的可验证形态（[ADR-0001](docs/adr/ADR-0001-plugin-composition-and-schema-ownership.md)）。

---

## 当前基线

| 项 | 值 |
|---|---|
| 契约版本 | `0.1.0` |
| 契约摘要 | `1568a98746c8363e692091dd7a21b4a9bc8b6d5e243a0820c907930bf00d4c22`（由 `checks.py` 计算；四份 `contracts.lock` 与之一致） |
| 运行时 | Python 3.12、FastAPI、PostgreSQL 17、NATS JetStream 2.10 |
| 集成形态 | composition host + B2/B3/B4 插件；B1 在 Vercel，API 经 Cloudflare Tunnel 到 Linux systemd host |
| 本地后端集成 | `docker compose up -d --build`（composition host + PostgreSQL + NATS） |
| 生产交付路径 | [production-deployment.md](docs/runbooks/production-deployment.md) |
| 阶段状态 | **Phase 01 = active**（未归档，见下方"未验证项"） |
| 已实现的业务能力 | B2/B3/B4 各自实现（状态见各模块文档）；MAIN 侧不实现业务能力 |

---

## 五个项目的关系

```text
AutoGrader/
├── MAIN/   ← 你在这里：契约、SDK、host、文档、门禁
├── B1/     静态前端（React 19 + TS + Vite）—— 无权威数据
├── B2/     评测插件 —— 分数与证据的唯一权威
├── B3/     题库插件 —— 不执行、不算分
└── B4/     平台插件 —— 身份/课程/数据库/消息/运行期注册表
```

**当前是一个 Git 仓库内的模块目录。** 各模块按自己的 README 独立开发；只有集成运行时 MAIN 才需要看到其他目录。

---

## 快速开始

### 按需专项审计（不作为日常开发或上线前置条件）

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

**期望输出**

```text
contracts, manifests and requirements: ok (<64 位十六进制摘要>)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok
```

这一条命令会校验：契约示例通过归属 schema、四个 `module.yaml` 合法且路由/schema 无冲突、**四份 `contracts.lock` 与实时摘要一致**、32 条需求编号完整（含 `RQ-B-4-99`）、四个模块的文档集合完整、全部 Markdown 为有效 UTF-8 且无死链、各模块 migration head 与本地端口和 `compose.yaml` 一致。

> 正常环境应使用 `uv run python -m autograder_host.checks`。若 `uv` 报缓存目录无法访问（`D:\dev\caches\uv` 在工作区之外，操作系统错误 5 = 权限被拒），改用上方仓库内虚拟环境的写法。**这是环境限制，不是代码缺陷。**

### 本地后端集成（容器）

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose up -d --build
```

该命令启动本地 API、PostgreSQL 与 NATS；首次启动会生成签名密钥、跑三个模块的迁移并执行 B4 角色供给。B1 另由 Vite 启动并通过 `VITE_API_BASE_URL=http://127.0.0.1:18000` 连接本地 API。生产交付见 [docs/runbooks/production-deployment.md](docs/runbooks/production-deployment.md)，本地集成细节见 [docs/runbooks/container-deployment.md](docs/runbooks/container-deployment.md)。

```powershell
# 验证
curl http://localhost:18000/health/live       # {"status":"ok"}
curl http://localhost:18000/health/ready      # ready=true，modules 列出 B2/B3/B4
```

**宿主端口约定**：PostgreSQL `55432`、NATS `54222`、host `18000`。选这些端口是为了避开本机常见服务；三个端口都可用 `POSTGRES_PORT` / `NATS_PORT` / `HOST_PORT` 覆盖（同一台机器上跑第二套时必需）。

首次启动后若要登录，空库需要先建首个管理员（B4 的运维命令，幂等；口令不回显、不落日志）：

```powershell
docker compose exec host python -m autograder_b4.ops.bootstrap_admin --username admin --password '<real-password>'
```

签名密钥**不需要**手工生成：entrypoint 在首次启动时写入 `autograder_main_jwt` 卷，之后的重启会复用同一把密钥（换密钥会让所有已签发的访问令牌失效）。

### 开发路径：本机跑（改代码时）

```powershell
# 1) 安装前端依赖
cd ..\B1 ; corepack enable ; pnpm install

# 2) 安装三个后端模块（使其 entry point 可见）
cd ..\B2 ; uv sync --all-groups
cd ..\B3 ; uv sync --all-groups
cd ..\B4 ; uv sync --all-groups

# 3) 基础设施（容器）＋ 本机 host
cd ..\MAIN ; docker compose up -d postgres nats
MAIN\scripts\run_host.ps1 -Port 18000          # 需要 gitignored 的 .env.integration
```

在单独的终端启动 B1：

```powershell
cd D:\VScodeProjects\AutoGrader\B1
$env:VITE_API_BASE_URL = "http://127.0.0.1:18000"
pnpm dev
```

详见 [docs/runbooks/local-development.md](docs/runbooks/local-development.md)（开发）与 [docs/runbooks/integration.md](docs/runbooks/integration.md)（组装与即插即用验证）。

---

## 仓库结构

```text
MAIN/
├── contracts/        ★ 机器可读契约（OpenAPI / AsyncAPI / JSON Schema / 示例）
├── sdk/              Platform SDK：ModuleDescriptor / PlatformModule / ModuleContext
├── host/             composition host 与集成检查（checks.py）
├── infra/            PostgreSQL 引导（schema 创建）
├── docs/             ★ 文档基座（索引见 docs/README.md）
├── compose.yaml      统一启动（资源限额即在此声明）
├── references/       课程教材 PPT（SEPA 9e 第 1–30 章）
├── 课程项目需求列表.xlsx        ★ 需求规范基线（32 条 RQ）
├── 课程开发项目-思维导图.pdf    产品形态参考（与 XLSX 冲突处以 XLSX 为准）
└── 项目初始化与项目文档管理.md  文档治理规范来源
```

---

## 契约如何被守住

```text
MAIN/contracts/**  ──sha256（相对路径 + 字节）──▶  digest
                                                    │
        ┌───────────────────┬───────────────────────┼───────────────────┐
        ▼                   ▼                       ▼                   ▼
  B1/contracts.lock   B2/contracts.lock      B3/contracts.lock   B4/contracts.lock
        └───────────────────┴───────────┬───────────┴───────────────────┘
                                        ▼
              host checks：四份锁的版本与摘要必须等于实时摘要
```

任何契约字节的变化都会让**四方锁**同时失配，集成检查立即失败。这不是形式主义——它防止的是"某人悄悄改了接口而其他人不知道"。

**改接口的唯一合法路径**：[docs/contracts/change-protocol.md](docs/contracts/change-protocol.md)（P1–P4 分级 + 8 步流程）。契约先行，然后才是实现。

---

## 资源账本（硬约束）

课程规定后端 **1 vCPU / 512 MB / 10 GB**：

| 组件 | 内存 | CPU |
|---|---|---|
| composition host | ≤160 MB | 0.35 |
| PostgreSQL | ≤96 MB | 0.15 |
| NATS | ≤32 MB | 0.05 |
| 单并发 nsjail 作业 | ≤96 MB | 0.45 |
| **保留余量** | **≥128 MB** | — |

这套预算**反向决定了架构**：它否掉了"一模块一数据库"（4×96 MB）与 Kafka（JVM 常驻）两个方案，并限定动态评测并发为 1。详见 [docs/architecture/resource-budget.md](docs/architecture/resource-budget.md)。

---

## 文档入口

从 [docs/README.md](docs/README.md) 进入。几条常用路径：

| 我想…… | 读这个 |
|---|---|
| 5 分钟了解项目 | [docs/requirements/prd.md](docs/requirements/prd.md) |
| 看 32 条需求的逐条验收 | [docs/requirements/rq-catalog.md](docs/requirements/rq-catalog.md) |
| 理解"为什么这样设计" | [docs/adr/README.md](docs/adr/README.md) |
| 开始做某个模块 | [docs/collaboration/module-doc-spec.md](docs/collaboration/module-doc-spec.md) → [docs/workstreams/](docs/workstreams/B1.md) |
| 改接口 | [docs/contracts/change-protocol.md](docs/contracts/change-protocol.md) |
| 排故障 | [docs/runbooks/troubleshooting.md](docs/runbooks/troubleshooting.md) |
| 看课程知识落在哪里 | [docs/knowledge-matrix.md](docs/knowledge-matrix.md) |

---

## 明确非目标

- 不支持 C / C++ / Java 等其他语言（只支持 Python 与 Bash，`RQ-B-0-10`）。
- 不做多租户、高可用、跨区域部署（资源账本不允许）。
- **LLM 不参与成绩与反馈**——评分必须确定性、可重放（[ADR-0005](docs/adr/ADR-0005-deterministic-replayable-scoring.md)）。
- 不保留任何兼容层：旧字段别名、双写、开关、降级路径、旧路由一律不留。
- 不创建远端、CI、PR 流程（除非明确要求）。

---

## 已知限制与未验证项（如实声明）

**本初始化阶段未执行**下列验证，因此**不得**声称对应能力已通过：

| 未验证项 | 原因 |
|---|---|
| `uv sync` / `uv run` 全流程 | 已实测可用（`uv 0.10.2`）；MAIN 侧需带 `--extra modules`，否则 host 装载不到任何模块 |
| 各模块 `ruff` / `pytest` / `alembic upgrade head` | 同上；且需要可用的 PostgreSQL |
| host 装载 B2/B3/B4、`/health/ready` | 需启动容器与安装模块 |
| B1 `pnpm install` / `build`（本轮） | 未重新执行构建 |
| B1 `pnpm lint` / `pnpm test` | **工具链不存在**：`lint` 仅为 `tsc -b`；无 ESLint 与 vitest |
| Linux 安全门禁（16 项）与资源实测 | **必须在 Linux + cgroup v2 + nsjail 环境** |

**结构性已知缺口**（已记录，未隐藏）：

| 缺口 | 影响 | 记录位置 |
|---|---|---|
| `B1/module.yaml` 的 `entry_point: static:dist` 不被 host 消费；B1 不在 capability DAG 中 | B1 缺失时 host 不会失败 | [ADR-0007](docs/adr/ADR-0007-b1-static-client-provided-by-host.md) |
| `ModuleDescriptor.events` 是扁平元组，不区分 publishes/subscribes | 无法校验事件发布/订阅方向 | [ADR-0003](docs/adr/ADR-0003-api-authority-and-runtime-registry.md) |
| `evaluation.rejected` 没有领域事件 | B3 的"作答次数分布"统计不到被拒的作答 | [docs/api/events.md](docs/api/events.md) §8 |
| `infra/postgres/init.sql` 只建 schema，无最小权限角色 | "跨 schema 访问被拒"目前无法验证 | [docs/database/ownership.md](docs/database/ownership.md) §8 |
| B3 的 `dev` 组缺 `pytest-cov`（B2/B4 有） | 覆盖率报告只在 B2/B4 可用 | [docs/testing/strategy.md](docs/testing/strategy.md) §4.1 |
| 沙箱在容器中运行可能需要放宽权限 | 与最小权限原则存在张力 | [ADR-0004](docs/adr/ADR-0004-sandbox-technology-selection.md) |

阶段状态、逐项验证记录与处置过程见 [docs/testing/acceptance.md](docs/testing/acceptance.md) 与 [docs/phases/active/phase-00-initialization.md](docs/phases/active/phase-00-initialization.md)。
