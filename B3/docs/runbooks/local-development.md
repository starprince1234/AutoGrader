# B3 本地开发（Local Development）

> 状态：以下命令**已在本环境（Windows + Docker 29.8 + uv 0.10.2）实际执行**，结果见 [Phase 01 记录](../phases/active/phase-01-question-bank-and-packages.md)。Phase 00 曾记录的"`uv` 无法写缓存目录"在本环境下已不复现（`uv sync` / `uv add` / `uv run` 均正常）。

## 0. 两套数据库，别搞混

| 用途 | 端口 | 谁提供 | 角色 |
|---|---|---|---|
| **平台集成库**（`.env.example` / `alembic.ini` 指向它） | `localhost:55432` | MAIN 的 `compose.yaml`（`infra/postgres/init.sql` 已预建 `b2`/`b3`/`b4` schema） | `autograder_platform`（迁移）；最小权限角色由 B4 供给 |
| **模块本地库**（测试套件默认） | `localhost:55434` | B3 自己的 [compose.yaml](../../compose.yaml)，自动执行 `infra/postgres/init.sql` 建两个最小权限角色 | `autograder_b3`（owner）/ `_app` / `_internal` |

为什么分开：B3 的测试会 **`DROP SCHEMA b3` 后从零迁移**，那绝不能在共享的平台库上做。MAIN 的 `checks.py` 要求 `.env.example` / `alembic.ini` 的端口与 `MAIN/compose.yaml` 一致，因此那两个文件写 55432，而 `compose.yaml` 用 55434——这不是不一致，是"集成目标 vs 隔离测试库"的分工。

> 本机 `5432` 被系统保留策略拒绝（`bind: An attempt was made to access a socket in a way forbidden by its permissions`），所以不要指望默认端口。

## 1. 环境变量

见 [../../.env.example](../../.env.example)。最小集合：`DATABASE_URL`（最小权限）、`DATABASE_PROTECTED_URL`（受保护内容）、`B3_SERVICE_TOKEN`（服务间认证）、`NATS_URL`（仅统计消费需要）、`B4_JWKS_URL`（本地验签）。

## 2. 安装依赖

```powershell
cd B3
uv sync --all-groups
```

`dev` 组包含 `pytest`、`pytest-cov`、`ruff`、`jsonschema`（契约测试实时校验 MAIN schema）、`httpx`（真实 HTTP 测试）；运行时含 `cryptography`（RS256/JWKS 验签）。

## 3. 启动 PostgreSQL

**模块本地库（推荐用于开发与测试）**

```powershell
cd B3
docker compose up -d postgres
docker compose ps          # 期望：0.0.0.0:55434->5432/tcp, healthy
```

`infra/postgres/init.sql` 会自动创建 `autograder_b3_app` / `autograder_b3_internal` 两个登录角色（普通角色对受保护表无任何授权）。

**平台集成库**

```powershell
cd ../MAIN
docker compose up -d postgres
```

## 4. 迁移

```powershell
cd B3
$env:DATABASE_URL = "postgresql+psycopg://autograder_b3:change-me-local-only@localhost:55434/autograder"
uv run alembic upgrade head      # -> 20260915_0001 -> 20260915_0002
uv run alembic heads             # -> 20260915_0002 (head)
```

Linux 对应：

```bash
export DATABASE_URL="postgresql+psycopg://autograder_b3:change-me-local-only@localhost:55434/autograder"
uv run alembic upgrade head
```

平台库上迁移时用 `autograder_platform` 账号；此时迁移里的授权块会因角色不存在而**跳过**（不报错）。

## 5. 启动本模块

B3 的同步 API 由 MAIN composition host 按 `entry_point` 装载，无独立进程。本地冒烟：

```powershell
uv run python -c "from autograder_b3.plugin import create_module; m=create_module(); print(m.health(None))"
```

`health()` 会真实 `SELECT 1`：数据库不可用时返回 `healthy=False` 并说明原因，而不是硬编码 `True`。

异步部分（统计消费 + outbox 投递）可独立运行：

```powershell
uv run python -m autograder_b3.consumer_cli              # 需要 NATS_URL
uv run python -m autograder_b3.consumer_cli --rebuild     # 清表后从 inbox 重放重建统计，然后退出
```

## 6. readiness

- 插件健康：`health()` 的 `detail` 含 token verifier 与 publisher 的装配情况，**不含**任何密钥。
- 数据库：`docker compose exec postgres pg_isready -U autograder_b3 -d autograder`。
- NATS：未配置 `NATS_URL` 时统计消费不启动（同步 API 路径照常工作），`consumer_cli` 会明确报错而非静默降级。

## 7. 跑测试与静态检查

```powershell
uv run ruff check .
uv run pytest --cov=autograder_b3 --cov-report=term
```

测试需要一个可达的 PostgreSQL；默认用模块本地库（55434），不可达时**跳过并给出启动命令**（不会假装通过）。可用环境变量覆盖：`B3_TEST_OWNER_URL` / `B3_TEST_APP_URL` / `B3_TEST_INTERNAL_URL`。

跨模块门禁（在 MAIN 侧执行）：

```powershell
cd ../MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

## 8. 常见端口 / 路径

| 项 | 值 |
|---|---|
| 平台 PostgreSQL | `localhost:55432`（MAIN compose） |
| 模块本地 PostgreSQL | `localhost:55434`（B3 compose，含最小权限角色） |
| NATS | `localhost:54222`（MAIN compose） |
| API 前缀 | `/api/v1/questions`、`/api/v1/question-versions`、`/api/v1/assignments` |

## 9. 验证记录摘要

**Phase 01 基线（2026-09-15，本环境实测）**

| 命令 | 结果 |
|---|---|
| `uv sync --all-groups` | `Resolved 28 packages` / `Audited 28 packages` |
| `uv add --dev "pytest-cov>=6,<7"` | `+ pytest-cov==6.3.0`、`+ coverage==7.16.1` |
| `docker compose up -d postgres` | `b3-postgres-1` 起，`0.0.0.0:55434->5432/tcp` |
| `uv run alembic upgrade head` | `-> 20260915_0001`、`20260915_0001 -> 20260915_0002`（exit 0） |
| `uv run alembic heads` | `20260915_0002 (head)` |
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest --cov=autograder_b3` | `282 passed`，`TOTAL 90%` |
| MAIN `python -m autograder_host.checks` | 三行 `ok`（契约摘要 / 文档治理 / 迁移 head 与端口） |

**当前分支（2026-09-20，[Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md) 实测）**

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest` | `393 passed, 6 warnings in 53.93s` |
| `uv run alembic upgrade head` | 无新迁移；`alembic heads` / `current` → `20260915_0004 (head)` |

> 完整原始记录见 [../phases/active/phase-10-draft-package-and-current-pointers.md](../phases/active/phase-10-draft-package-and-current-pointers.md) §3（此前各阶段见其同名记录）
> 与 [../phases/active/phase-04-quality-surfaces.md](../phases/active/phase-04-quality-surfaces.md)；故障排查见 [troubleshooting.md](troubleshooting.md)。
