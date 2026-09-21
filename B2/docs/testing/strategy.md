# B2 测试策略

## 测试分层与目标

| 层 | 目标 | 必须覆盖的风险 |
|---|---|---|
| **单元** | 校验/幂等/规则/比较/脱敏等纯逻辑 | 路径穿越、SHA-256 不符、幂等键、规则 severity、比较策略、脱敏正则 |
| **契约** | 端点/事件/schema 与 MAIN 契约一致 | 请求体 schema、事件 envelope、报告 schema、错误码（`401/403/404/413/422`） |
| **集成** | 真实 PostgreSQL + NATS + 沙箱的组件协作 | 跨 schema 拒绝、事件重复消费、outbox/inbox、SSE、迁移 head |
| **端到端** | 完整评测链（登录→提交→评测→查分→导出） | 绑定摘要、可重放、导出与查询一致 |
| **安全** | 恶意样本与沙箱违规 | 静态高危模式、禁网、只读根、提权、超资源、进程树清理 |
| **资源** | 资源账本实测 | 单并发评测 ≤96 MB RSS / 0.45 vCPU；初始 1 个动态评测并发 |

## 分层原则

- **真实基础设施**：PostgreSQL **不得**用 SQLite 代替（`b2` schema、`alembic_version_b2` 版本表、最小权限角色都依赖 PostgreSQL）。
- **mock 只允许**隔离不可控外部服务：IMAP 邮箱、B3 package 服务、B4 JWKS（可用本地测试密钥替代）。
- **沙箱测试**：nsjail/cgroup v2/seccomp 依赖 Linux，**不得**用"假执行"替代；Windows 上只跑平台无关层（比较器、报告、持久化）。
- **安全回归**：恶意样本证据必须来自**真实执行**（沙箱内运行被阻断），不得用静态断言冒充。

## 门禁命令（可复制）

```powershell
uv sync --all-groups
uv run ruff check .            # lint（line-length 100 / py312）
uv run pytest                  # 全部测试
uv run pytest --cov=src/autograder_b2 --cov-report=term-missing   # 覆盖率
uv run alembic upgrade head    # 迁移验证（空库从零执行）
```

> **并发运行注意（实测教训）**：事件用例连的是**同一个真实 NATS JetStream**（`B2_TEST_NATS_URL`，
> 默认 `nats://localhost:54223`）。**同一 broker 上并行跑多份事件套件会互相抢消费/读回**，表现为
> 随机的 `EventBusUnavailable` 或读回超时（已实测：并发时出现 `8 failed`，串行立刻全绿）。
> 因此 CI 上：要么串行跑、要么给并行任务分配**独立 broker**（独立 stream 前缀不够——拉取会跨套件竞争）。
> 读回/排空用的是固定超时，对并发负载敏感；要并行就得放宽超时或改为"轮询到齐"。

## 可执行性与当前实测读数（2026-09-16 按实现刷新）

**更正**：本节此前是一张"骨架阶段尚不能执行的检查"表（写"`uv` 本环境未验证""业务端点尚未实现，`router()` 为空"
"业务表未建""runner 未实现""已存在的测试只有 `tests/test_plugin.py`"）——这些都是**骨架阶段的过期表述**，
与当前实现相反，已整节替换为本表。

| 检查 | 可执行 | 实测读数（与 phase-01"验证记录"同源） |
|---|---|---|
| `uv run ruff check .` | **是** | `All checks passed!`（`uv 0.10.2`，`uv run` 实测可用） |
| `uv run pytest -q -p no:warnings` | **是**（需真实 PostgreSQL） | `1134 passed, 9 skipped, 1 xfailed`；`skipped` 构成见 phase-01 |
| `uv run alembic upgrade head` / `current` | **是** | `20260915_0009 (head)`，与 `module.yaml`、`db/schema.py` 三处一致 |
| 契约测试（端点/事件 schema） | **是** | 端点由 `B2Module.router` 注册 7 条；报告过 MAIN `evaluation-report` schema：`report_schema_errors == []` |
| 集成测试（PostgreSQL/NATS） | **是**（真实依赖） | 真实 PostgreSQL 55433 + 真实 NATS JetStream 54223；**不得并行跑同 broker 的套件**（见上方并发注意） |
| 安全/资源测试（nsjail） | **仅 Linux/容器内** | 隔离矩阵 `13 passed, 1 skipped, 1 xfailed`；真实 runner 验收 `5 passed, 1 skipped`；宿主驱动模式 `1 passed, 14 skipped` |
| 迁移 head 验证 | **是** | 迁移链 `0001..0009` 全部落库；离线全链 `alembic upgrade head --sql` 408 非空行 |

测试规模与归属（实测口径）：`tests/` 下 **29** 个 `test_*.py` 测试模块（`tests/**/*.py` 共 33 个文件，
其余为夹具/辅助），全量收集 **1143** 项（+1 条模块级 skip = `1134 passed, 9 skipped, 1 xfailed` 的 1144）。
用例目录见 [cases.md](cases.md)；`tests/test_plugin.py` 只是插件骨架冒烟，**并非**"唯一存在的测试"。**未执行项**
（真实 IMAP/TLS、跨进程并发上限、MAIN 侧 worker/scheduler 驱动）逐条登记在
[../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md)，
不因本文写"可执行"而消失。

> **读数快照说明（本节表格里的数字是当时的读数，不要改写成最新值）**：改写会抹掉历史，也会让"哪一批
> 改变了什么"无从对照。**当前读数由 MAIN 的台账持有**：`MAIN/.runtime/evidence/B2.json`，由
> `python MAIN/scripts/test_evidence.py --all` 真实运行产生，并由
> `MAIN/host/src/autograder_host/checks.py::check_test_evidence` 按契约版本/摘要与 `exit_code` 强制。
> 本节未随之刷新的还有迁移 head（表里写的是当时值）与用例总数——**表格是快照，台账是现状**。

## 用例目录

可执行用例清单（用例 ID / 目标 RQ / 层次 / 前置 / 步骤 / 期望 / 是否存在）见 [cases.md](cases.md)。
