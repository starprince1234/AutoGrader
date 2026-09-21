# AutoGrader B4 —— 平台底座模块

B4 是 AutoGrader 的**平台底座插件**：负责身份与凭据、角色与作用域授权、课程/课序/选课主数据、名单导入处理、审计，以及 PostgreSQL 与 NATS JetStream 的**运行期维护**和**模块运行期注册表**。

**当前状态：`骨架`**。代码只有插件注册、`descriptor`、空路由与健康检查（`health()` 恒返回 `"plugin loaded; platform services pending"`），**没有任何业务能力可运行**。

## 已实现能力（骨架）

- 插件装载：`entry_point = autograder_b4.plugin:create_module`，注册 `B4Module`。
- `descriptor`：声明 `module=B4`、`contract_version=0.1.0`、`provides`、`requires`、`routes`、`events`。
- 健康检查：`health()` 返回 `HealthReport("B4", True, "plugin loaded; platform services pending")`。
- 空基线迁移：`alembic/versions/20260915_0001_b4_schema_baseline.py` 仅 `CREATE SCHEMA IF NOT EXISTS b4`。
- 最小单元测试：`tests/test_plugin.py` 断言 `descriptor.module == "B4"` 且 `provides` 含 `b4-auth`。

## 明确非目标

- **不**拥有 B2/B3 的业务物理表（submission、evaluation、question 等）及其表结构。
- **不**是 `MAIN/contracts/**` 的作者；不修改、不重定义 OpenAPI/AsyncAPI/JSON Schema。
- **不**执行学生代码、**不**计算分数、**不**拥有题目/评测/成绩数据。
- **不**以超级用户运行应用；**不**做跨 schema 外键/视图/共享 ORM。

## 系统位置

```text
B1 静态 UI ── HTTP/SSE ── MAIN composition host
                             ├── B4 身份/课程/名单/审计 ── PostgreSQL b4 schema
                             ├── B3 题库 ──────────────── PostgreSQL b3
                             ├── B2 评测 ──────────────── PostgreSQL b2 ── nsjail
                             └── NATS JetStream（事件）
```

B4 是**身份权威**：签发令牌、暴露 JWKS；其他模块**本地验签，不回**调 B4。

## 前置条件

- Python `>=3.12,<3.13`；包管理 `uv`。
- PostgreSQL 17（本地开发经 compose 宿主端口 `55432`）、NATS 2.10 + JetStream（宿主端口 `54222`）。
- 环境变量见 `.env.example`（仅变量名 + 安全占位值，真值不提交仓库）。

## 安装 / 迁移 / 启动 / 验证

> ⚠️ 以下命令**尚未在本环境执行**：本机沙箱下 `uv` 无法写其缓存目录（`D:\dev\caches\uv` 在工作区之外），且不保证有 Docker / psql / nats 可用。请先在有完整工具链的环境中运行，并把真实结果回填到 `docs/phases/active/phase-00-initialization.md`，**不得伪报通过**。

Windows（PowerShell）：

```powershell
uv sync --all-groups
uv run ruff check .
uv run pytest
# 依赖服务（在 MAIN 目录）：
# docker compose up -d
# 迁移（连接串以 DATABASE_URL 环境变量覆盖宿主端口 55432）：
uv run alembic upgrade head
```

Linux（验收环境）：

```bash
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run alembic upgrade head
```

## 入口

- 模块装载入口：`autograder_b4.plugin:create_module`（entry point 组 `autograder.modules`，见 `pyproject.toml`）。
- API 前缀（`module.yaml: routes`）：`/api/v1/auth`、`/api/v1/courses`、`/api/v1/roster-imports`（当前无任何已注册业务路由，`router()` 返回空 `APIRouter()`）。

## 文档入口

- 唯一索引与状态表：`docs/README.md`。
- 冷启动路径（30 分钟）：`docs/collaboration/onboarding.md`。

## 已知限制与缺口

- `descriptor.events` 是**扁平元组**且只列 1 个事件，未区分 `publishes`/`subscribes`（SDK 已知缺口，见 `../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md`）。
- `MAIN/infra/postgres/init.sql` 目前只建 `b2`/`b3`/`b4` 三个 schema，**尚无最小权限角色供给**。
- 备份/恢复演练**尚未执行**（`RQ-B-4-10` 要求一次真实演练）。
- `.env.example` 与 `alembic.ini` 默认端口为 `5432`（容器内端口），本地经 compose 应使用宿主端口 `55432`，需以 `DATABASE_URL` 覆盖，二者尚未统一。
- 无认证、无用户/课程/名单/审计/注册表实现，无结构化日志（`RQ-B-4-99` 仅骨架）。
