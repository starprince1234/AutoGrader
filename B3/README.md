# AutoGrader B3 — 题库与测试包插件

B3 是 AutoGrader 的**题库与测试包（evaluation package）插件**。它按课程维护题目（题干、参考答案、测试用例）、不可变题目版本、assignment（作业集合）、三类测试框架（stdin / file / callable）的 package 编译，以及基于去标识化事件的答题统计。

- **当前状态：`已完成`**（B3 侧 7 条责任需求全部实现并有测试；见 [docs/README.md](docs/README.md) 状态表）。
- 一句话边界：**B3 不执行学生代码、不计算分数、不拥有评测证据**。执行、安全判定、评分与证据属于 B2。
- 当前阶段：`Phase 04 质量能力的出口（CHG-0013 落地）`（见 [docs/phases/active/phase-04-quality-surfaces.md](docs/phases/active/phase-04-quality-surfaces.md)）。
- **未完成项（不得视为已完成）**：B2 runner 端到端联调、NATS durable consumer 真实运行、B4 真实 JWKS、10 万事件量级性能基准、本轮三个新出口在**真实宿主**上的端到端调用，以及三条待 MAIN 裁决的契约缺口（[CHG-B3-0001](docs/contracts/changes/CHG-B3-0001-file-comparison-granularity.md) / [CHG-B3-0002](docs/contracts/changes/CHG-B3-0002-asset-and-fixture-endpoints.md) / [CHG-B3-0003](docs/contracts/changes/CHG-B3-0003-distinct-student-statistic.md)）。

## 已实现能力 / 非目标

**已实现（每项都有对应测试，见 [docs/testing/cases.md](docs/testing/cases.md)）**

- **题目聚合**（`RQ-B-3-10`）：富文本题干 + 受控图片资源（内容摘要 + 安全响应头）、参考答案（受保护存储）、测试用例（隐藏/可见）。
- **不可变版本与发布**（`RQ-B-3-20`）：`draft → published → archived`；已发布内容原地修改返回 `409`，数据库触发器（SQLSTATE `23001`）二次拦截；发布前完整性校验；assignment 只能引用已发布版本。
- **质量出口**（`CHG-0013`）：比较策略诊断端点（`CMP-001..004` 确定性规则，**不回显任何期望输出**）；版本 DTO 的 `revision_note`/`revision_diff`（键集合 = 契约 `RevisionDiff`，仅计数/长度/摘要/状态位）与写路径（无 `as_new_version` 即 `422`）；题目列表按标签**全部满足**检索。
- **三类 adapter 与 package 编译**（`RQ-B-3-40/50/60`）：stdin/argv（args 为数组、逐字保留）、file I/O（fixture 内容寻址 + 摘要绑定 + 路径限定）、callable（点分标识符入口 + JSON 参数）；三型共用同一 package 结构与唯一编译入口，确定性 `package_digest`。
- **去标识化统计**（`RQ-B-3-30`）：订阅 `evaluation.completed`，durable consumer + `event_inbox` 去重（`event_id` 主键、`evaluation_run_id` 唯一键），整数预聚合，支持从事件重放重建；payload 字段白名单，越界字段拒收且不 ack（走 DLQ）。
- **结构化脱敏日志**（`RQ-B-3-99`）：单行 JSON，`producer=B3`，按 key 脱敏（13 类受保护键），失败不静默。
- **公开/内部 DTO 物理分离**：`dto/public.py` 与 `dto/internal.py`，公开响应唯一出口 `serializers.serialize_public`；受保护内容在数据库层由独立角色隔离（普通角色 `permission denied`）。
- **契约门禁**：本模块 `uv run ruff check .`、`uv run pytest`（362 例）、`uv run alembic upgrade head`，以及 MAIN 的 `python -m autograder_host.checks`。

**明确非目标（越界即缺陷）**

- 不执行学生代码、不运行沙箱、不做静态/动态评测（属 B2）。
- 不计算或存储分数、不生成评测报告/成绩导出（属 B2）。
- 不拥有身份/课程/选课/审计（属 B4）；不提供前端（属 B1）。
- 不读取 `b2` / `b4` schema；不导入其他模块的 ORM；禁止跨 schema 外键 / SQL / 视图。
- 不让参考答案 / 隐藏用例 / expected output 出现在公开 DTO、事件、日志或学生可访问存储。
- 不恢复旧的 scoring / evidence / report API、旧评分表模型、`RQ-B-3-70` 编号或任何兼容路由。

## 在系统中的位置

```text
                 ┌──────────────────────────────────────────────┐
                 │  MAIN：组合 host（设计期契约权威 + 运行期装载） │
                 └──────────────────────────────────────────────┘
   ┌─────────────┐  ┌──────────────┐   ┌──────────────────┐   ┌──────────────┐
   │ B1 前端      │  │ B2 评测引擎   │   │ B3 题库/测试包     │   │ B4 平台      │
   │ (TS/React)  │  │ (沙箱/评分)   │   │ (本模块)          │   │ (身份/运维)  │
   └─────────────┘  └──────┬───────┘   └────────┬─────────┘   └──────┬───────┘
                           │  evaluation-package │                   │
                           │  (B3 → B2，内部接口) │                   │ JWT/JWKS
                           │  evaluation.completed│                  │ course 作用域
                           │  (B2 → B3，去标识化)  │                  │
                           └──────────────────────┴───────────────────┘
```

- B3 → B2：提供 `evaluation-package`（内部接口，含 adapter 类型、测试输入、比较规则、fixture 引用、内容摘要）。**这是 B2↔B3 最高风险耦合点**，以 `MAIN/contracts` 为唯一权威。
- B2 → B3：消费去标识化的 `autograder.v1.evaluation.completed` 事件做统计（**绝不读取学生源码**）。
- B3 从不导入 B1/B2/B4 的代码，只经 HTTP / 事件 / 平台 SDK 交互。

## 契约与权威

- 契约权威：`../MAIN/contracts/`（OpenAPI / AsyncAPI / JSON Schema）；版本与摘要见 [contracts.lock](contracts.lock)。
- 模块清单：[module.yaml](module.yaml)（`module: B3`、`database.schema: b3`、`migration_head: 20260915_0003`）。
- 错误与关联约定：RFC 9457 Problem Details；时间 UTC RFC 3339（毫秒精度）；标识 UUID v4（owner 生成）；传播 `traceparent` / `X-Request-ID`。
- 契约缺口一律以 `CHG-B3-*` 上报（[docs/contracts/changes/](docs/contracts/changes/)），**不修改 `MAIN/contracts/**`**。
- 文档三铁律：单一权威（不抄字段表/DDL）、不声称未实现、失败与缺口必须留档——见 [docs/README.md](docs/README.md)。

## 前置条件

- Python `>=3.12,<3.13`；包管理 `uv`；数据库 PostgreSQL 17；消息中间件 NATS 2.10（仅统计消费需要）。
- 依赖 `autograder-platform==0.1.0`（来自 `../MAIN/sdk`，见 `pyproject.toml` 的 `tool.uv.sources`）。
- 环境变量见 [.env.example](.env.example)：`DATABASE_URL`（最小权限角色）、`DATABASE_PROTECTED_URL`（受保护内容角色）、`B3_SERVICE_TOKEN`、`NATS_URL`、`B4_JWKS_URL`。

## 安装 / 迁移 / 启动 / 验证

```powershell
cd B3
uv sync --all-groups

# 模块本地库（宿主端口 55434；平台库是 55432，见 docs/runbooks/local-development.md）
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://autograder_b3:change-me-local-only@localhost:55434/autograder"
uv run alembic upgrade head

uv run ruff check .
uv run pytest --cov=autograder_b3
```

- 最新一轮实测（本环境，2026-09-18）：`ruff` → `All checks passed!`；`pytest` → `362 passed`；
  `alembic upgrade head` → 无新迁移（head 仍为 `20260915_0003`）。原始输出见
  [Phase 04 记录](docs/phases/active/phase-04-quality-surfaces.md)；Phase 01 的 `282 passed / 90%` 是当时的真实数字，按"阶段记录不回头改写"保留。
- B3 的同步 API 由 MAIN composition host 按 `entry_point` 装载（无独立进程）；异步部分（统计消费 + outbox 投递）可独立运行：`uv run python -m autograder_b3.consumer_cli`（重建统计加 `--rebuild`）。

## 用户 / 开发者入口

- API 前缀：`/api/v1/questions`、`/api/v1/question-versions`、`/api/v1/assignments`（以 [module.yaml](module.yaml) 与 `MAIN/contracts/openapi/b3.yaml` 为准；逐端点状态见 [docs/api/contract.md](docs/api/contract.md)）。
- 内部接口：`/api/v1/question-versions/{question_version_id}/evaluation-package`（仅 B2 服务间可访问）。

## 文档入口

从 [docs/README.md](docs/README.md) 开始 —— 它是 B3 文档的唯一索引（权威表 + 状态表）。

## 已知限制（如实）

1. **契约缺口 3 条**（不阻塞已实现范围，但需 MAIN 裁决）：file adapter 的"文本差异"比较粒度不可表达（CHG-B3-0001）；图片上传/读取、fixture 上传与创建题目的 `course_id` 参数在 `b3.yaml` 中未成文（CHG-B3-0002）；"独立作答人数"不可计算、`is_pass` 字段名待确认（CHG-B3-0003）。
2. `descriptor.events` 是**扁平元组**且只列了 1 个事件（`question.version.published`），不区分 publishes/subscribes；`module.yaml` 另声明订阅 `evaluation.completed`。这是 SDK 已知缺口（`../MAIN/docs/adr/ADR-0003`），宿主目前无法校验事件方向；B3 以 `module.yaml` 为准，且不发明第二套约定。
3. **跨模块联调未做**：B2 runner 实际执行、NATS JetStream 真实消费与 DLQ、B4 真实 JWKS 端点均未在本环境验证（见 [docs/testing/strategy.md](docs/testing/strategy.md) 未覆盖清单）。`CHG-0013` 的三个新出口同样**只在 `TestClient` + 真实 PostgreSQL 上验证过**，未经真实宿主端到端调用。
4. 统计的性能措辞（10 万事件量级可用）**部分不达标**：窗口路径实测 23.8 s，见 [Phase 03](docs/phases/active/phase-03-quality-attestation-and-bounded-statistics.md)。
5. ~~**契约内部不一致 1 处（已上报）**~~ → **`CHG-0016` §5 已驳回**：B3 曾报告 `ComparisonDiagnostics.severity` 的契约 description 与 enum 不一致；契约里不存在这句话，真实经过是**实现偏离契约**（多出一个未声明的 `note`），已改实现对齐。
6. ~~`PATCH /api/v1/questions/{id}` 的响应体与契约不符~~ → **`CHG-0016` 已裁决（契约 0.11.0）**：MAIN 判定契约是错的一侧并改为 `QuestionPublic`；**B3 无需改代码**。
7. `taskone/` 为旧研究材料，非权威、非运行时、默认不跟踪（见 [docs/research/README.md](docs/research/README.md)）。
