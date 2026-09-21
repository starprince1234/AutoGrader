# B2 本地开发运行手册

> 本手册命令**已在本环境实测可用**（`uv 0.10.2`，`uv run ...` 实跑）。真实执行读数与**未执行项**
> （例如 `uv sync --all-groups` 本轮未跑）见 [../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md)
> 的"验证记录"，不得伪报通过。**更正**：此前写"`uv` 命令尚未在本环境验证（沙箱无法写 `uv` 缓存目录）"，已过期。

## 1. 环境变量

复制 [../../.env.example](../../.env.example) 为 `.env` 并填入真实值：

| 变量 | 用途 | 示例 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg://autograder_b2:change-me@localhost:5432/autograder` |
| `NATS_URL` | NATS 地址 | `nats://localhost:4222` |
| `NSJAIL_PATH` | nsjail 可执行路径 | `nsjail`（Linux） |

B2 自身的设置项（解析在 `src/autograder_b2/config.py`；未列出的项都有明确默认值，没有隐藏开关）：

| 变量 | 用途 | 默认 |
|---|---|---|
| `B2_ARTIFACT_ROOT` | 内容寻址存储根（必填） | 无（缺则整个 B2 不可用） |
| `B2_MAX_FILES` / `B2_MAX_FILE_BYTES` / `B2_MAX_TOTAL_BYTES` | 提交规模上限 | `100` / `1572864` / `4194304` |
| `B2_ARTIFACT_RETENTION_DAYS` | 内容寻址存储的**保留窗口**（天）：内容失去全部引用后至少再保留这么久才允许回收。`0` ＝一失去引用即可回收；调大只会更保守（见 [../database/schema.md](../database/schema.md) 的"保留策略"节） | `0` |
| `B2_B3_BASE_URL` / `B2_B3_AUTHORIZATION` / `B2_WORKSPACE_ROOT` | 评测链（取包端点 + 服务间凭据 + 沙箱工作区根，三者缺一即评测链不可用） | 无 |
| `B2_ENVIRONMENT_TAG` | 绑定摘要里的环境标签 | `unset` |
| `B2_JWKS_JSON` / `B2_JWKS_PATH` | B4 公钥集（缺则无法验签 ⇒ 全部路由 503） | 无 |
| `B2_IMAP_*` | 邮件入口（整组可缺省，缺即该入口不可用） | 见 `config.load_imap_settings` |

> 集成环境（MAIN host）用 `B2_` 前缀给每个模块自己的配置视图（ADR-0008），因此独立运行 worker 时
> 要把 host 的 `B2_DATABASE_URL` / `B2_NATS_URL` 按**无前缀**名字喂给它，见第 5b 节。

## 2. 安装依赖

```powershell
# PowerShell（Windows 开发）
uv sync --all-groups
```

```bash
# Linux（验收环境）
uv sync --all-groups
```

### 2b. SDK 变了但 `uv sync` 说"没事"时（会把基线数字搞错，实测）

`autograder-platform` 是**路径依赖**（`pyproject.toml` 的 `[tool.uv.sources]` → `../MAIN/sdk`），
版本恒为 `0.1.0`。**上游文件改了而版本号没变时，`uv run` / `uv sync` 都会认为依赖已满足、不重装**，
于是 `.venv/lib/site-packages/autograder_platform` 里留着旧副本。实测过一次后果：MAIN 给 SDK 加了
`contracts.py`，而 B2 的副本还停在旧版本，`pytest` 在**收集期**就报

```text
ModuleNotFoundError: No module named 'autograder_platform.contracts'
```

任何测试都跑不起来（不是某条用例失败，是收集失败）。修复：

```powershell
uv sync --all-groups --reinstall-package autograder-platform
```

**因此**：报基线数字时请注明"SDK 是否已刷新"；看到"整批测试收集失败"先查这一条，而不是怀疑代码。

## 3. 迁移

```powershell
uv run alembic upgrade head    # 应用迁移到 head（当前 head = 20260915_0013）
uv run alembic heads           # 查看 head，应与 module.yaml: migration_head 一致
```

## 4. 启动依赖服务

### 4a. 集成目标（共享平台实例，在 `../../MAIN` 目录）

```powershell
docker compose up -d postgres nats
```

- PostgreSQL 宿主端口 **55432**，库 `autograder`，管理员 `autograder_platform`。
- NATS 宿主端口 **54222**（JetStream `-js`）。

### 4b. 测试实例（模块本地，本目录）

```powershell
docker compose -p b2 up -d
docker compose -p b2 ps
```

- PostgreSQL 宿主端口 **55433**，NATS 宿主端口 **54223**（监控 `58222`）。
- 套件需要**能 `CREATE DATABASE`** 的 DSN：`tests/conftest.py::temporary_database` 为每个测试模块
  建一个临时库、跑 Alembic、用完 DROP。因此测试用 `autograder_platform`（夹具角色），
  应用角色 `autograder_b2` 仍是 `module.yaml` 里那个最小权限角色。
- NATS 必须独立于平台 broker：事件用例会 `add_stream` / `delete_stream`，与正在运行的 host 共用
  一个 broker 会互相抢消费/读回（`docs/testing/strategy.md` 已记录该并发注意）。

测试命令前置（本轮实测使用的值）：

```powershell
$env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"
$env:B2_TEST_NATS_URL     = "nats://127.0.0.1:54223"
```

- nsjail：**仅 Linux** 需要；Windows 不启动动态执行。
- `b2` schema 由迁移创建；应用角色 `autograder_b2` 仅拥有 `b2` 权限（由 B4 运维创建角色/实例，见 [../../../MAIN/docs/database/ownership.md](../../../MAIN/docs/database/ownership.md)）。
- NATS JetStream 2.10：本模块作为发布/订阅方接入；stream/consumer 命名与 subject 遵循 [../../../MAIN/contracts/asyncapi/events.yaml](../../../MAIN/contracts/asyncapi/events.yaml)。

## 5. 启动本模块

### 5a. 作为宿主的插件（HTTP 面）

B2 是 MAIN host 的插件，**HTTP 面**无独立启动命令；由 MAIN host 通过 entry point `autograder.modules → b2` 装载。插件自身的 `health()` 返回健康报告。

### 5b. 评测 worker（推进评测 + 投递事件；本模块唯一的驱动入口）

**没有它，评测与事件都不会前进**：`POST /api/v1/submissions` 只把提交存成 `validated`，运行行由
worker 创建；outbox 也只有 worker 会排空（宿主不调用 worker，`B2Runtime` 不埋定时器）。

```powershell
# 在集成环境（host 用的是 MAIN/.env.integration 的 B2_* 前缀变量）里跑一轮：
$envFile = "..\MAIN\.env.integration"
Get-Content -Encoding utf8 $envFile | ForEach-Object {
    $line = $_.Trim(); if ($line -eq "" -or $line.StartsWith("#")) { return }
    $name, $value = $line.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
}
# ADR-0008：host 会把 B2_<X> 覆盖成 <X> 交给 B2，独立跑 worker 时手工做同一件事
$env:DATABASE_URL = $env:B2_DATABASE_URL
$env:NATS_URL     = $env:B2_NATS_URL
uv run python -m autograder_b2.worker --once        # 一轮：评一个提交 + 排空 outbox
uv run python -m autograder_b2.worker --idle-exit   # 队列空即退出（检查用）
uv run python -m autograder_b2.worker               # 常驻循环（生产形态；单 worker）
```

- 一次的推进顺序是"取提交 → 评测 → 排空 outbox"，两者在同一个循环里（本模块只有一个并发模型）。
- **`--idle-seconds`（默认 2）同时是投递重试的节奏**：broker 持续不可达时，待发行每轮 `attempts += 1`，
  达到派发器上限（`DEFAULT_MAX_ATTEMPTS = 5`）即进 DLQ（可用 `NatsOutboxDispatcher.replay` 人工重放）。
  若部署环境的 broker 可能长时间不可用，把 `--idle-seconds` 调大，别让一次抖动把积压推进 DLQ。
- 事件发布与评测**共用**同一个数据库/配置；worker 里的沙箱可用性判定与 HTTP 面完全一致
  （Windows 上没有 nsjail ⇒ 动态阶段如实 `failed + infrastructure_error`，绝不降级为裸进程）。

## 6. 验证 readiness / 测试

```powershell
uv run ruff check .            # lint（line-length 100 / py312）
uv run pytest                  # 全量（需真实 PostgreSQL + NATS，见 4b 的环境变量）
uv run pytest --cov=src/autograder_b2 --cov-report=term-missing
uv run alembic upgrade head    # 迁移验证
```

> **读数快照说明**：下面这些数字是**当时的读数**，不要改写成最新值（改写会抹掉历史）。
> **当前读数由 MAIN 的台账持有**：`MAIN/.runtime/evidence/B2.json`（由
> `python MAIN/scripts/test_evidence.py --all` 真实运行产生，由
> `MAIN/host/src/autograder_host/checks.py::check_test_evidence` 按契约版本/摘要强制）。

本轮真实读数（2026-09-18 第二批修复后，隔离实例 55433/54223）：`1145 passed, 9 skipped, 1 xfailed`，exit 0
（同日前一批为 `1134 passed, 9 skipped, 1 xfailed`）。中途出现过 2 次假失败：MAIN 的契约 relock 与本次
验证并发推进，把两个"把版本号写死在断言里"的用例变成假失败，已改成与 `module.yaml` 比对。
详见 [../phases/active/phase-02-test-instance-and-suite.md](../phases/active/phase-02-test-instance-and-suite.md)。

### 6a. readiness 里那句"累计 N 次未预期依赖失败"是什么意思

`/health/ready` 的模块 detail 由 `B2Module.health` → `B2Runtime.health_summary()` 给出。其中的失败计数
**只如实累计、不参与健康判定**：计数非零时分项仍是 `ok`、`healthy` 仍是 `true`，只多一句
"自本进程启动以来累计 N 次未预期依赖失败（单调累计、仅诊断：不参与健康判定）"。

- **为什么不用它翻转 `healthy`**：计数从进程启动起**单调不降、永不重置**，一旦用它翻转，实例在第一次
  瞬时抖动之后就**永久**不健康（恢复也不再变绿）——那是恒假告警，比不报更糟。就绪判定回答的是
  "现在能不能服务"，由每次探测（`database_reason()`）与每次请求自己回答。
- **哪些不计入**：已文档化的平台条件。本机（Windows）没有 nsjail ⇒ 参考解答验证必然 `503`，这件事由该
  `503` 的 detail 与 `runner.sandbox_unavailable` 日志表达，**不计入**计数器（否则这台机器的诊断永远脏着）。
- **哪些计入**：未预期的依赖故障——库不可达/结构版本不符、B3 通道不通、上游两处声明自相矛盾等。
- **因此**看到那句话时的处置顺序：按**同一时间窗**查 `persistence.database_unavailable` /
  `messaging.broker_unavailable` / `runner.sandbox_unavailable`，先定位是哪一类，再决定要不要动。

完整规则（含"为什么不翻转"的产品理由）见 `src/autograder_b2/runtime.py` 模块 docstring 的
"失败计数与健康判定的关系"；回归用例
`tests/test_b2_routes.py::test_recorded_endpoint_failures_are_reported_without_flipping_readiness`、
`tests/test_reference_verification.py::test_sandbox_unavailable_is_503_with_no_attestation` 与
`::test_language_disagreement_between_upstreams_is_503`（后者是"未预期故障必须计数"的反向半边）。

## 7. 常见端口 / 路径说明

| 项 | 值 | 说明 |
|---|---|---|
| 模块 API 前缀 | `/api/v1/submissions`、`/api/v1/evaluation-runs`、`/api/v1/grades` | 由 MAIN host 组合；**7 条端点已真实注册**（`plugin.py` 拼 3 个路由族），清单见 [../api/contract.md](../api/contract.md) |
| 数据库 schema | `b2` | 版本表 `alembic_version_b2` |
| 迁移目录 | `../../alembic/` | `script_location` 指向此处 |
| 事件 subject | `autograder.v1.*` | 见 [../api/events.md](../api/events.md) |

## 8. Windows 与 Linux 差异

| 项 | Windows（开发） | Linux（验收） |
|---|---|---|
| 动态执行 | 不执行（无 nsjail/cgroup v2） | 执行（nsjail + cgroup v2 + seccomp） |
| 契约/单元测试 | 可运行 | 可运行 |
| 资源账本实测 | 不做 | 必做（≤96 MB RSS / 0.45 vCPU） |
