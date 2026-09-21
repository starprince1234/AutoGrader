# B4 本地开发运行手册

> ✅ 本手册的命令**已在本环境真实执行**（Windows + Docker Desktop；PostgreSQL 17、NATS 2.10 JetStream），
> 结果见 `../phases/active/phase-01-platform-implementation.md`。

## 1. 环境变量（复制 `.env.example`）

```powershell
Copy-Item .env.example .env
```

| 变量 | 说明 | 本地占位值 |
|---|---|---|
| `B4_ENV` | `development` / `production` | `development` |
| `DATABASE_URL` | 应用连接串（**最小权限角色**，宿主端口 `55432`） | `postgresql+psycopg://autograder_b4:change-me@localhost:55432/autograder` |
| `B4_ADMIN_DATABASE_URL` | 管理员连接串，**仅供给/备份/恢复使用** | `…autograder_platform:change-me-local-only@localhost:55432/autograder` |
| `B4_DB_ROLE` / `B4_DB_PASSWORD` | 供给时创建/更新的角色与口令 | `autograder_b4` / `change-me` |
| `NATS_URL` | NATS 客户端地址（宿主端口 `54222`） | `nats://localhost:54222` |
| `JWT_PRIVATE_KEY_FILE` | Ed25519 私钥路径（不提交仓库） | `secrets/jwt-ed25519.pem` |
| `B4_ACCESS_TOKEN_TTL_SECONDS` | 访问令牌有效期 | `900` |
| `B4_REFRESH_TOKEN_TTL_SECONDS` | 刷新凭据有效期 | `1209600` |
| `B4_ROSTER_UPLOAD_MAX_BYTES` | 名单上传上限 | `5242880` |
| `B4_MODULE_ROOT` | 部署根：含 `MAIN/contracts` 与各 `B*/module.yaml` 的目录（注册表校验用） | 经 SDK 的 `deployment_root()` 解析：读本变量本身，并**校验**该目录下确有 `MAIN/contracts`；只有从源码树导入且本变量未设时才回退到相对路径推导 |

> `B4_MODULE_ROOT` 这个名字现在被**三个模块共用**（B4 的注册表、B2 的契约读取、B5 的一致性审计
> 都指向同一个目录），因此它是**部署事实**而不是 B4 的私有配置。此前 B2/B5 各自用
> `Path(__file__).parents[N]` 推导，在 `site-packages` 形态下指向虚拟环境内部 —— B2 因此在真实
> 部署里读不到任何契约 schema、B5 一直报 `contract tree unreadable`。详见
> `../../../MAIN/docs/phases/active/phase-02-ai-activation-and-integration-repair.md` §2.1。

> `.env` 由运维入口与测试加载（`ops/dotenv.py`）；**已存在的环境变量优先**，不会被 `.env` 覆盖。
> 部署时由 host 注入真实环境变量（见 `../../MAIN/compose.yaml`）。

## 2. 安装依赖

```powershell
uv sync --all-groups
```

## 3. 启动依赖服务

### 3a. 集成目标（共享平台实例，在 `../../MAIN` 目录）

```powershell
docker compose up -d postgres nats
docker compose ps
```

- PostgreSQL 宿主端口 `55432`，库 `autograder`，管理员 `autograder_platform`。
- NATS 宿主端口 `54222`（JetStream `-js`），监控 `8222`。

### 3b. 测试实例（模块本地，端口不同，本目录）

```powershell
docker compose -p b4 up -d
```

- PostgreSQL 宿主端口 **55435**，NATS 宿主端口 **54224**（监控 `58224`）。
- **测试必须跑在这里。** B4 的套件在**每个用例前**清空 `b4.*`（`tests/conftest.py::clean_database`），
  并对共享库做同样的事会删掉构成集成环境的种子数据——现象与"种子没跑过"无法区分。实测代价见
  `../phases/active/phase-02-test-instance-isolation.md` 缺陷 1。
- 隔离实例的 `b2`/`b3` schema **故意建成空的且不授权**：跨 schema 拒绝只有在 schema 真实存在时
  才证明得了东西（否则测试会因为 "schema 不存在" 而通过，什么也没证明）。
- `tests/conftest.py` 已经把这些地址钉死为默认值；`B4_TEST_DATABASE_URL` /
  `B4_TEST_ADMIN_DATABASE_URL` / `B4_TEST_NATS_URL` 可覆盖（CI 用）。

## 4. 供给角色与迁移（在 B4 目录）

### 4a. 集成目标（`55432`）

```powershell
uv run python -m autograder_b4.ops.provision      # 建/更新最小权限角色 + schema + 授权
uv run python -m autograder_b4.ops.generate_key   # 生成 Ed25519 私钥（首次）
uv run alembic upgrade head
uv run alembic current
```

- 顺序不能颠倒：**先供给、再迁移**（迁移由最小权限角色执行，schema 由供给创建）。
- 版本表 `alembic_version_b4` 落在 `b4` schema；head 应与 `module.yaml: database.migration_head` 一致。

### 4b. 测试实例（`55435`，跑套件前做一次）

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55435/autograder'
uv run alembic upgrade head
uv run python -m autograder_b4.ops.provision
Remove-Item Env:DATABASE_URL      # 让 conftest 的钉住值生效
```

漏掉这一步时套件会以 `pytest.fail` 明确告诉你该跑什么（`migrated_test_instance` 守卫），
而不是在夹具里抛出裸 `relation "b4.audit_records" does not exist`。

### 4c. 首个管理员（空库时唯一入口）

```powershell
$env:B4_BOOTSTRAP_ADMIN_PASSWORD = '<local-only>'
uv run python -m autograder_b4.ops.bootstrap_admin --username admin --display-name "平台管理员"
```

幂等；口令不回显、不入日志；其余用户仍由 `POST /api/v1/users` 创建（该端点需要管理员）。

## 5. 事件拓扑、健康与运维动作

```powershell
uv run python -m autograder_b4.ops.messaging provision
uv run python -m autograder_b4.ops.messaging health
uv run python -m autograder_b4.ops.messaging metrics        # 实测 JetStream 占用 vs 资源账本
uv run python -m autograder_b4.ops.table_standards --scope catalog     # 真实 b4 schema
uv run python -m autograder_b4.ops.table_standards --scope migrations  # 全仓迁移 DDL 文本
uv run python -m autograder_b4.ops.backup --container b4-postgres-1    # 备份 b4 并留下结构化记录
```

- `table_standards` 不带 `--scope` 时两种模式都跑；`--scope migrations` 会报出 B2/B3 的既有违规，
  因此**日常自查请用 `--scope catalog`**（详见 `../database/table-standards.md` §9）。
- 迁移（`uv run alembic upgrade head`）与备份、readiness 变化都会发出结构化记录（`RQ-B-4-99` 验收 4）。

## 6. 启动本模块 / 验证 readiness

- B4 由 MAIN composition host 装载（host 端口 `18000`），不单独启动进程。
- readiness 由 host 的 `/health/ready` 聚合，B4 的 `health()` 真实探测 **postgres / migration head / nats**
  三项，detail 逐项点名。
- 公开存活端点：`GET /api/v1/modules/B4/health`；全量注册表 `GET /api/v1/modules` 需管理员令牌。

## 7. 跑测试

```powershell
uv run ruff check .
uv run pytest                                   # 需要真实 PostgreSQL 与 NATS（模块本地实例，见 3b/4b）
uv run pytest --cov=autograder_b4 --cov-report=term-missing
```

> 测试**不会**在依赖缺失时静默跳过：连不上 PostgreSQL 或 NATS 会以明确信息失败。

真实结果（2026-09-18，本环境）：`209 passed, 2 warnings in 130.25s`，exit 0。

## 8. 端口 / 路径速查

| 对象 | 地址 |
|---|---|
| MAIN host | `http://localhost:18000` |
| PostgreSQL（集成） | `localhost:55432`（容器内 `5432`） |
| NATS（集成） | `localhost:54222`（容器内 `4222`，监控 `8222`） |
| PostgreSQL（B4 测试实例） | `localhost:55435`（容器内 `5432`） |
| NATS（B4 测试实例） | `localhost:54224`（容器内 `4222`，监控 `58224`） |
| B4 API 前缀 | `/api/v1/auth`、`/api/v1/users`、`/api/v1/courses`、`/api/v1/sections`、`/api/v1/enrollments`、`/api/v1/roster-imports`、`/api/v1/audit-records`、`/api/v1/modules` |

> 平台库是 `55432`、B3 的模块本地库是 `55434`、B4 的是 `55435`、B2 的是 `55433`；
> NATS 平台 `54222`、B2 测试 `54223`、B4 测试 `54224`。混用会被当成"库没起"。MAIN 的
> `checks.py` 只要求 `.env.example` / `alembic.ini` 与 `MAIN/compose.yaml` 一致（那两个文件写
> 平台端口），模块本地端口只在 `compose.yaml` 与测试默认值里出现——这不是不一致，是
> "集成目标 vs 隔离测试实例"的分工。
