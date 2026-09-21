# Phase 03 运维可追溯、配额计量、规范执法与死配置清理（当前阶段）

## 目标与范围

本阶段处理审计报告（`MAIN/.runtime/innovation/B4.md`）的四项工作项，并接住本阶段期间 MAIN 契约流程
推进过程中落在 B4 上的要求（版本号以 `contracts.lock` / `module.yaml` 为准，本文不写死）：

| 工作项 | 内容 |
|---|---|
| E12 | JetStream 配额进入资源账本 + 真实占用测量路径 |
| E13 | 迁移执行 / 备份 / 健康检查结果的结构化记录（`RQ-B-4-99` 验收 4） |
| E8 | 三个死配置的判定与处置 |
| A3 | 表结构规范对**全仓迁移 DDL 文本**生效（B4 拥有规范却无法执法） |
| 平台缺口 | **共享域事件流缺失**：broker 上只有 `ROSTER_IMPORT`/`B4_DLQ`，任何模块都发不出事件（ADR-0006 的解耦设计未接线） |
| 契约 | `EnrollmentPublic.student_id`（CHG-0007）、文档级 `security`（CHG-0006）、模块名 pattern（CHG-0009/ADR-0013）、同名 entry point 必须响亮失败（ADR-0013 决策 5） |

范围外：B2/B3 的迁移与实现（B4 只报告违规，不修改）；`MAIN/contracts/**` 与 `contracts.lock`
（只读，本阶段未改）。

## 完成条件

1. 声明的 JetStream 配额 ≤ 账本行 0.25 GB，且断言在"配额被删/被改小"时**会失败**；
2. 有可执行的**实测**占用读取路径，并被集成测试调用；
3. 迁移、备份、健康检查各有结构化发射点，健康路径不产生写放大，日志不含 DSN/口令；
4. 三个死配置按需求判定后删除（不留兼容层、不留"看起来有"的开关）；
5. 规范 linter 覆盖全部模块的迁移文本，且**检查器自身被测**（故意违规必须被发现）；
6. 契约变更落地（`student_id` 返回、pattern 打开、同名 entry point 拒绝）；
7. 全套件在**模块本地实例**（PostgreSQL 55435 / NATS 54224）上真实转绿，数字写进本文件。

## 决策与依据

### 1. JetStream 配额（`RQ-B-4-20` 验收 5）

- 账本行 `resource-budget.md` §3"JetStream 持久 ≤ 0.25 GB"约束的是**本模块全部 JetStream 存储之和**，
  不是单条 stream 的上限。原声明 `256 MB + 32 MB = 288 MB` 已经超出该行。
- 最终分配（三条 stream，合计 = 账本行 268435456 B）：

| stream | 配额 | 依据 |
|---|---|---|
| `DOMAIN_EVENTS` | 192 MB（201326592） | 承载契约里六个 subject 的共享通道；某个消费者落后时不能开始丢别人的事件（§6） |
| `ROSTER_IMPORT` | 32 MB（33554432） | 只承载 B4 自己的一个 subject，durable 会及时落进 `b4.inbox_events`，小缓冲足够 |
| `B4_DLQ` | 32 MB（33554432） | JetStream advisory 每条数百字节，32 MB 可容数千条且保留"可查、可重放"的能力 |

  `ROSTER_IMPORT_MAX_BYTES` 由账本行减去另外两条**推导**而非手写，三者不可能各自漂移。
- 断言（`tests/test_messaging.py::test_jetstream_storage_stays_inside_the_resource_ledger`）：
  每条 stream `max_bytes > 0`（旧断言允许某条为 0）+ 合计 ≤ 账本行 + 三条配额之和 == 实测总量。
  旧的 `total <= 268435456 * 2` 已删除——它放行到 0.5 GB，把配额改成 0 也能通过。
- 测量路径：`ops.messaging metrics` 读 broker 自报的 `state.bytes` / `state.messages` 与每个 durable 的
  `num_pending` / `num_ack_pending`，打印占账本行比例；stream 缺失或实测超限即非零退出。
  集成测试 `test_jetstream_storage_is_measured_not_assumed` relay 一条真实事件后断言
  `stored_bytes > 0` 且 ≤ 配额——把"配置了配额"变成"测量了占用"。

### 2. 运维动作记录（`RQ-B-4-99` 验收 4）

| 动作 | 发射点 | 事件 | 字段 |
|---|---|---|---|
| 迁移 | `alembic/env.py::_run_migrations` | `ops.migration.applied`（INFO）/ `ops.migration.failed`（ERROR） | `from_revision` / `requested_revision` / `result_revision` / `duration_ms`（失败另带 `reason`/`error`） |
| 备份 | `src/autograder_b4/ops/backup.py`（新增） | `ops.backup.completed` / `ops.backup.failed` | `schema` / `database` / `container` / `dump`（仅文件名）/ `bytes_written` / `duration_ms`（失败另带 `exit_code`/`reason`） |
| 健康 | `api/dependencies.py::B4State._record_health_state` | `ops.health.changed`（转坏 ERROR、转好 INFO） | `healthy` / `previous_healthy` / `detail` / `previous_detail` |

- **健康只在状态变化时记录**：readiness 是被持续轮询的热路径，逐次记录等于把监控变成日志写放大，
  而运维真正要回答的是"什么时候开始不健康的"。比较-置位由 `threading.Lock` 保护，并发探针不会重复发射。
- **脱敏仍在 formatter 内**：三个发射点都用 `logging.log_operation`，脱敏规则不经过调用点，新增字段
  无法"忘记脱敏"；测试对格式化后的整行断言不含 `change-me` 与 `postgresql`。
- **备份在容器内执行 `pg_dump -Fc`**（手册原有的做法，也是 `pg_dump` 唯一保证存在的地方）：参数是数组、
  不经 shell，容器本地 socket 连接，**命令行上没有任何口令**；写完回读 `PGDMP` 魔数，失败即删除半成品，
  不让一个"看起来像备份"的文件留在磁盘上。恢复仍是人工手册流程（§5），不做成便捷参数。
- 迁移进程的日志配置：`logging.structured_module_logging()` 在**块作用域内**给 `autograder_b4` 命名空间
  挂同一个脱敏 JSON formatter（并临时关闭向 root 传播，避免与 Alembic 的 console handler 重复输出），
  退出时恢复——因为 Alembic CLI 的日志配置不属于 B4，替换 root handler 会要么吞掉
  `Running upgrade …` 要么让每条记录打印两遍。

### 3. 死配置（E8）

| 配置 | 判定 | 依据 |
|---|---|---|
| `B4_REGISTRY_ENFORCE` | **删除** | 全仓 0 读取点。注册表漂移必须让 host 启动失败（`RQ-B-4-40` AC2/AC3），是必然而非可配；给它一个 "off" 等于是隐藏旁路，与"禁止隐藏开关"直接冲突 |
| `B4_ROSTER_PREVIEW_LIMIT` | **删除** | 全仓 0 读取点。预览返回**全部**行（`accepted_rows`/`rejected_rows` 都不截断），契约 `RosterImportPreview` 无 limit 字段，且 `RQ-B-1-10` 的"被拒行不静默丢弃"本就禁止截断 |
| `B4_LOGIN_LOCKOUT_SECONDS` | **删除**（AC7 由限流满足） | 全仓 0 读取点。`api/auth.py` 已实现**按账号的滑动窗口限流**（principal 的 SHA-256、窗口 `B4_LOGIN_FAILURE_WINDOW_SECONDS`=300s、阈值 `B4_LOGIN_FAILURE_LIMIT`=5，超限 429 且先提交台账与审计再抛错）。它本身就是有界、限时、按账号的策略，即 `RQ-B-4-50` AC7 的"速率限制/锁定策略"；另加永久锁定会把拒绝服务变成攻击面（攻击者可锁死任意账号，且本系统没有自助解锁通道） |

### 4. 迁移 DDL linter（A3 / `RQ-B-4-30` AC1）

- 位置：`src/autograder_b4/ops/table_standards.py` 的静态模式；入口
  `python -m autograder_b4.ops.table_standards --scope all|catalog|migrations`。
- **读文件 ≠ 读表**：不建立任何跨 schema 连接；结论是"迁移文本不符合规范"，不是"对方的库不符合规范"。
- 解析：AST 识别 `op.execute("<DDL>")`（`f"…{SCHEMA}…"` 会代入模块级常量）与
  `op.create_table`/`create_index`/`add_column`/`create_*_constraint`；字符串字面量、`--`/`/* */` 注释、
  `$$…$$` 体在结构扫描前遮蔽（注释不遮蔽会让尾随注释变成**下一列**的前缀，使该列逃过所有规则）。
- 规则与 catalog 模式同源（`table-standards.md` §1–§4）：表名 snake_case 且复数、列名 snake_case、
  单列 `<entity>_id` UUID 主键且约束名 `pk_<table>`、`ix_`/`uq_`/`fk_`/`ck_` 前缀、`created_at`/`updated_at`
  为 `timestamptz(3)`、其它 `*_at` 不得为裸 `timestamp`、禁止跨 schema 建表/外键/视图。
- **不会静默通过**：统计识别到的 DDL 语句数；某模块为 0 即报违规，测试另钉住全仓语句数下限。
- 检查器自身被测：合成迁移（原始 SQL 与 SQLAlchemy 两种写法）逐条断言预期违规被报出，且合规语句不被误报。

### 5. 契约变更落地

- **CHG-0007**：`listEnrollments` 每项返回 `student_id`（`b4.enrollments.user_id`，未匹配到账号为 `null`）。
  契约把 `student_number`/`display_name` 声明为非空——与迁移一致（两列均 `NOT NULL`），实现不会发 `null`，
  **无需改契约**。测试对响应键集合做精确断言（`additionalProperties: false`）。
- **CHG-0006**（文档级 `security`/`bearerAuth`）：与既有实现一致，未改代码。
- **CHG-0009 / ADR-0013**：`api/modules.py: MODULE_PATTERN` 同步为 `^B[0-9]{1,3}$`（并在注释里注明
  该形状只在契约里定义一次）；删除 `registry.py` 中**从未被引用**的闭集副本 `VALID_MODULE_NAMES`；
  测试改为按 pattern 断言并新增"`B5`/`B12`/`B999` 必须被接受"。
- **ADR-0013 决策 5**：`resolve_declared_modules` 对**重复声明名**抛 `RegistryError`（此前 `if name not in seen`
  静默保留第一个，而 host 的 dict 推导是最后一个胜出——同一输入两个相反答案）。
- `plugin.DESCRIPTOR.contract_version` 由 MAIN 的契约流程同步（B4 侧不再手写版本号断言：测试改为与
  `module.yaml` 比较，避免契约每次升版都产生假失败）。

### 6. 共享域事件流（平台级缺口，`RQ-B-4-20`）

- **缺口**：平台 broker 上只有 `ROSTER_IMPORT` 与 `B4_DLQ`，没有任何 stream 覆盖 `autograder.v1.>`；
  B2 的 outbox 因此 15 行全部 `NoStreamResponseError: nats: no response from stream`，`published_at` 恒为空，
  B3 的 `event_inbox` 恒为 0——即 ADR-0006 的解耦**设计完成但未接线**。stream 供给属 B4（`RQ-B-4-20`）。
- **为什么不用 `autograder.v1.>`**：**一个 subject 只能绑定到一条 stream**，而 `ROSTER_IMPORT` 已持有
  `autograder.v1.roster.import.completed`。共享流 `DOMAIN_EVENTS` 显式枚举契约里其余六个地址；
  一致性由测试读取 `MAIN/contracts/asyncapi/events.yaml` 的 `address` 断言"每个地址恰好绑定一次"
  （契约新增事件而这里没跟上 → 套件失败，而不是发布时才炸）。
- **谁消费**：共享流上**没有 B4 的 consumer**。哪个模块消费哪些事件、用什么 durable/ack 策略，是该模块的
  投递契约；B4 只提供流。
- **账本再分配**：见 §1 的三条 stream 分配表（共享流 192 MB / `ROSTER_IMPORT` 32 MB / DLQ 32 MB，合计 = 账本行）。
  断言相应为"每条 > 0 + 合计 ≤ 账本行 + 三条配额之和 == 实测总量"（后者保证没有哪条配额没人计数）。

## 实测（真实命令与结果）

> 全部针对**模块本地实例**（PostgreSQL 55435 / NATS 54224）与仓库内文件；未指向平台库（55432）或平台
> broker（54222）。

```text
$ uv run pytest -q                     # 见下"验证结果"
$ uv run ruff check .                  # 见下"验证结果"

$ uv run python -m autograder_b4.ops.table_standards --scope migrations
migrations: 129 DDL statements in 13 files, 62 violation(s)      # B2 26 / B3 36 / B4 0

$ uv run python -m autograder_b4.ops.table_standards --scope catalog
b4 schema conforms to docs/database/table-standards.md           # exit 0

$ uv run python -m autograder_b4.ops.messaging metrics           # 空闲（模块本地 broker）
ROSTER_IMPORT: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
  consumer ROSTER_IMPORT/B4_ROSTER_CONSUMER: pending=0 ack_pending=0 redelivered=0
DOMAIN_EVENTS: stored=0 bytes messages=0 quota=201326592 ledger_share=0.00%
B4_DLQ: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
  consumer B4_DLQ/B4_DLQ_CONSUMER: pending=0 ack_pending=0 redelivered=0
total stored=0 bytes of the 268435456 byte ledger line (0.00%)   # exit 0

# 发布真实事件后再测（测试套件 relay/发布之后）
ROSTER_IMPORT: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
DOMAIN_EVENTS: stored=380 bytes messages=5 quota=201326592 ledger_share=0.00%
total stored=380 bytes of the 268435456 byte ledger line (0.00%) # exit 0

$ uv run python -m autograder_b4.ops.backup --container b4-postgres-1 --destination <scratch>/b4-phase03.dump
{"level": "INFO", "logger": "autograder_b4.ops.backup", "message": "ops.backup.completed",
 "producer": "B4", "event": "ops.backup.completed", "schema": "b4", "database": "autograder",
 "container": "b4-postgres-1", "dump": "b4-phase03.dump", "bytes_written": 49543, "duration_ms": 656}
wrote <scratch>/b4-phase03.dump (49543 bytes) in 656 ms                    # exit 0
# 归档头 5 字节 = PGDMP（custom format）；文件属临时目录，留档后删除，不进仓库

$ uv run alembic upgrade head          # 已在 head，验证发射点
{"level": "INFO", "logger": "autograder_b4.ops.migration", "message": "ops.migration.applied",
 "producer": "B4", "event": "ops.migration.applied", "from_revision": "20260915_0002",
 "requested_revision": "20260915_0002", "result_revision": "20260915_0002", "duration_ms": 0}
# Alembic 自身的 "Running upgrade …" 仍以原格式输出，未被顶掉、未重复
```

## 验证结果（真实执行）

```text
$ cd D:\VscodeProjects\AutoGrader\B4 ; uv run pytest -q
227 passed, 2 warnings in 135.85s (0:02:15)          exit 0

$ uv run ruff check .                                # 原型补全后一并清掉先前既存的 C416
All checks passed!                                   exit 0
```

> 运行期间 MAIN 的契约批量变更（CHG-0006…0011，`0.5.0`→`0.9.0`）曾使 composition 类用例瞬时失败
> （`contracts.lock` 与 live 契约树摘要不一致 → B4 的启动门禁正确拒绝启动）。等 MAIN relock 完成后
> 同一条命令为 `223 passed`；期间 B4 未改任何契约文件。

**证据边界（诚实说明）**：`227 passed` 全部跑在**模块本地实例**（PostgreSQL 55435 / NATS 54224）上，
其中 `tests/test_registry.py::test_the_real_composition_validates`、`test_contract_digest_mirrors_what_main_locked`
与 `tests/test_host_integration.py` 三项是**对真实仓库 composition 的校验**（读真实 `B1..B5/module.yaml`、
`contracts.lock`、`MAIN/contracts` 摘要并逐模块比对），但它们仍然是在模块本地数据库/代理上运行的——
本阶段**没有**把 B4 的测试套件指向平台实例（平台库 55432 / 平台代理 54222）。
平台侧的动作只有两处、都是 B4 自己的运行期职责，且已在下文逐条留档：`ops.messaging provision`
（在平台 broker 上创建/收敛 stream）与 `ops.messaging metrics`（只读测量）。

## 本阶段发现的真实缺陷

| # | 缺陷 | 影响（已实测） | 处置 |
|---|---|---|---|
| 1 | `alembic/env.py` 的 `fileConfig()` 默认 `disable_existing_loggers=True` | 进程内执行迁移（测试、脚本"迁移后继续服务"）会把**所有已存在的 B4 logger 禁用**，之后所有 B4 记录静默消失——`RQ-B-4-99` 的可追溯性在真实场景下失效。实测：迁移用例之后，备份记录一条都收不到 | 改为 `fileConfig(..., disable_existing_loggers=False)`；迁移用例之后的备份断言即是回归 |
| 2 | linter 未遮蔽 SQL 注释 | `-- 注释` 会被当成**下一列**的前缀，使 `updated_at timestamptz` 逃过精度规则（合成用例暴露） | 注释/字符串/`$$` 统一遮蔽，并有断言钉住 |
| 3 | 原型声明按**表名**键控 | `inbox_events` 在 B2（复合键）与 B4（代理键）同时存在 → 按名声明会同时误判 B4 自己的表、并错误豁免另一张。实测中 B4 的迁移先被报了一条"声明与 schema 不一致" | 改为按 `(模块, 表名)` 键控；两半（声明 + 结构前置条件）都必须成立 |

### 7. 标准原型补全（MAIN E16）：不再把正确设计判成违规

MAIN 的裁决（`../../MAIN/docs/phases/active/phase-01-integration-runtime.md` §12.2）指出：§2/§3 要求
**每一张表**都有单列 `<entity>_id UUID` 主键且"未声明直接判违规"，于是把三类合法设计误判为违规。
处置是把三类原型**写进标准**（`docs/database/table-standards.md` §2.1–§2.4），并让检查器识别它们：

| 原型 | 结构前置条件（检查器验证） | 声明位置 |
|---|---|---|
| 内容寻址 `content_addressed` | 主键是**摘要形状**的列（`_sha256`/`_sha512`/`_digest`/`_hash` 结尾）**且**有 64 位摘要检查（类型长度或 `CHECK … ~ '[0-9a-f]{64}'`） | `NATURAL_KEYS[("B3","blobs")]` |
| 枚举自然键 `enumerated_natural_key` | 单列短标签（`varchar(n)`，`n ≤ 64`）**且**被 `CHECK … IN (…)` 约束到固定取值集合 | `NATURAL_KEYS[("B3","consumption_counters")]` |
| 复合自然键 `composite_natural_key` | 主键**两列及以上**，且列与声明**逐列一致** | `NATURAL_KEYS` 中 5 张表（B2 `submission_files`/`inbox_events`；B3 `question_version_score_buckets`/`question_version_failure_categories`/`reference_attestations`） |
| 不需复数的表名 | 名字**精确等于**声明项 | `NON_PLURAL_TABLE_NAMES`：B3 `event_inbox`/`event_outbox`、B2 `case_evidence` |

三条硬性设计：

1. **声明按 `(模块, 表名)` 键控**——理由与实测见上表缺陷 3；
2. **不削弱其它规则**：主键**约束名** `pk_<table>` 仍照旧要求——未命名的那几张表**仍然被报**，属真违规；
3. **两个方向都有测试**：声明且符合 → 不得报（`test_declared_archetypes_are_not_reported`）；
   长得像但未声明 → 照旧报（`test_undeclared_lookalikes_are_still_reported`：`payloads(digest …)`、
   未声明复合键、`event_inbox_backup` 这类相近名）；声明了却不符合 → 照样报
   （`test_a_declaration_cannot_launder_a_table`）；声明自身也被检查
   （`test_every_archetype_declaration_is_keyed_by_module_and_satisfied`）。

**效果（同一命令，实测）**：

```text
# 补全原型前
migrations: 137 DDL statements in 16 files, 69 violation(s)
  35 主键约束未命名 / 22 timestamptz 未声明 (3) / 12 误判（5 复合键 + 3 非复数 + 2 非 uuid + 2 非 _id）

# 补全原型后
migrations: 137 DDL statements in 16 files, 57 violation(s)
  35 primary key is unnamed; the standard requires 'pk_<table>'
  22 declares timestamptz without millisecond precision; the standard requires timestamptz(3)
# 原型相关残留：0 条（对输出过滤 not plural / is [ / expected uuid / does not end / digest /
#   value set / bounded label / declaration and the schema 全部为 0）
```

即：**12 条误判全部消失，57 条真违规一条未减**；`--scope catalog` 对真实 `b4` schema 仍为 0 条。

## 平台侧动作与端到端证据（2026-09-19）

B4 在平台实例上执行的两条命令（都是 B4 自己的运行期职责；未运行别人的测试套件、未循环 worker）：

```text
# 1) 供给：在平台 broker 上创建共享流并收敛既有 stream 的配额
$ $env:NATS_URL='nats://127.0.0.1:54222' ; uv run python -m autograder_b4.ops.messaging provision
{"event": "messaging.stream.created", "stream": "DOMAIN_EVENTS"}
{"event": "messaging.stream.updated", "stream": "ROSTER_IMPORT"}
{"event": "messaging.stream.updated", "stream": "B4_DLQ"}
consumer:B4_DLQ/B4_DLQ_CONSUMER: unchanged
consumer:ROSTER_IMPORT/B4_ROSTER_CONSUMER: unchanged
stream:B4_DLQ: updated      stream:DOMAIN_EVENTS: created      stream:ROSTER_IMPORT: updated
exit 0

# 2) 实测占用（只读）
$ uv run python -m autograder_b4.ops.messaging metrics
ROSTER_IMPORT: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
DOMAIN_EVENTS: stored=9685 bytes messages=15 quota=201326592 ledger_share=0.00%
B4_DLQ: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
total stored=9685 bytes of the 268435456 byte ledger line (0.00%)   # exit 0
```

投递 B2 的积压（**一次**，用 B2 自己的派发器；不跑评测、不循环）：

```text
$ cd ../B2 ; $env:DATABASE_URL='…autograder_b2@localhost:55432/autograder' ; $env:NATS_URL='nats://localhost:54222'
  $env:B2_ARTIFACT_ROOT='<B4 scratch>' ; $env:B2_JWKS_JSON=(B4 的公开 JWKS，取自 /api/v1/auth/jwks)
  uv run python -c "… build_b2_runtime(os.environ); drain_outbox(rt.outbox) …"
outbox wired: True | broker_reason: None
drain report: DispatchReport(selected=15, published=15, failed=0, dead_lettered=0, last_error=None)
pending now: []
```

投递前后（平台库只读查询，`docker exec main-postgres-1 psql`）：

| 指标 | 投递前 | 投递后 |
|---|---|---|
| `b2.outbox_events` 总行 | 15 | 15 |
| `published_at is not null` | **0** | **15** |
| `published_at is null` | 15 | 0 |
| `dead_lettered_at is not null` | 0 | 0 |
| `max(attempts)` | 1 | 1（没有重试循环） |
| `b3.event_inbox` | 0 | **0** |

**结论（分清已证与未证）**：

- **已证**：共享流存在且被声明式供给；发布路径真的通——15 条**真实**事件从 B2 的 outbox 发出并落在
  `DOMAIN_EVENTS`（`messages=15`、`stored=9685`，由 B4 的 `metrics` 从 broker 读回），`published_at` 由 0 变 15，
  没有死信、没有 `attempts` 增长。
- **未证**：B3 的 `event_inbox` 仍为 0。原因是**消费端还没有东西可消费**：这 15 条是 `submission.accepted`，
  B3 消费的是 `evaluation.completed`；平台上也没有任何模块的 durable consumer 绑在 `DOMAIN_EVENTS` 上
  （`jsz?consumers=true` 只列出 B4 的两条）。要让它动起来需要 B2 的评测 worker
  （`B2_B3_BASE_URL` / `B2_B3_AUTHORIZATION` / `B2_WORKSPACE_ROOT` + Linux nsjail）与 B3 的
  `python -m autograder_b3.consumer_cli` 常驻——**属各自模块的运行期，B4 不伪造事件去让计数器动**。

## B1 提问：重复确认已提交的名单导入是否二次写入

**答案：服务端幂等，不会二次写入。** 依据是 `confirm_import` 的实际分支，而不是意图：

- `src/autograder_b4/roster/service.py:397-400`：若该导入已是 `committed`，**直接返回同一结果**
  （`already_committed=True, written=0`），**不执行任何写语句**（无 UPDATE、无 upsert、无新审计行）；
- 409 的两种情形也都不写入：呈现的 key 与已存 key 不同（`:398-399`，`committed with a different idempotency key`）、
  或状态不是 `previewed`（`:401-404`）；
- 写入路径在 `:465-486`：`_upsert_enrollments` 用 `(section_id, student_number)` 唯一键 + `ON CONFLICT DO UPDATE`，
  即使真的并发重放也不会产生重复选课行；
- 回归用例：`tests/test_roster_import.py:145-155`（同 key 两次确认 → 两次 202、`status=committed`、
  `enrollments` 仍为 1 行）、`:158-166`（已 committed 后换 key → 409）。

对 B1 的实际含义：**不带 key 或带同一个 key 重发都是安全的空操作**（刷新页面后重试即可）；
只有"同一个 import 用了一个**新的** key"才会拿到 409——那是"已经提交过、key 不一致"的提示，
不是失败。若 B1 每次尝试都新生成 key，建议把 409 当作"已提交"处理（重新 `GET` 该 import 并展示已提交状态），
或者对同一个 `roster_import_id` 复用同一个 key。

## 未完成 / 阻塞

- 无 `阻塞` 项（本阶段全部完成条件已满足）。
- `MAIN/contracts/**` 与四份 `contracts.lock` 本阶段**未改动**（无 CHG，版本号由 MAIN 的契约流程推进）。
- B2/B3 迁移文本的 **57 条真违规**归属各自模块：B4 只报告，不修改（见下表）。措辞上这是
  "**迁移 DDL 文本**不符合规范"，不是"对方的库不符合规范"——B4 只读得到 `b4` 的 catalog，
  对真实 `b4` schema 的检查为 0 违规。原型补全（§7）之前是 69 条，其中 12 条是对正确设计的误判，
  已随 §2.1–§2.4 消失；**剩下的 57 条一条未减**。
- `b3.event_inbox` 仍为 0：共享流已供给、B2 的 15 条事件已投递成功，但消费端需要 B2 的评测 worker
  与 B3 的 `consumer_cli` 常驻（属各自模块运行期，见上文"平台侧动作"）。
- 长时间 JetStream 占用采样仍未做（属平台级资源门禁，`resource-budget.md` §5 规定方法）。

### B2/B3 迁移违规明细（`--scope migrations`，本模块不修改）

补全原型后仍是 **57 条，全部属于两类真违规**（原型相关的 0 条）：

| 类别 | 条数 | 说明 |
|---|---|---|
| 主键约束未命名（要求 `pk_<table>`） | 35 | 含 5 张复合键表——它们的**键形**是合法的（原型），但**约束名**仍不合规，属真违规 |
| `timestamptz` 未声明毫秒精度（要求 `(3)`） | 22 | `created_at`/`updated_at` 未声明精度 |

按文件分布（137 条 DDL / 16 个文件）：

| 文件 | 条数 |
|---|---|
| `B2/20260915_0002_b2_reception_storage.py` | 10 |
| `B2/20260915_0003_b2_artifact_gc_audit.py` | 2 |
| `B2/20260915_0004_b2_outbox_events.py` | 1 |
| `B2/20260915_0005_b2_submission_state_transitions.py` | 1 |
| `B2/20260915_0006_b2_evaluation_tables.py` | 10 |
| `B2/20260915_0010_b2_evaluation_commentaries.py` | 1 |
| `B3/20260915_0002_b3_question_bank.py` | 30 |
| `B3/20260915_0003_b3_attestation_and_consumption.py` | 2 |

> 完整的逐条输出可用 `uv run python -m autograder_b4.ops.table_standards --scope migrations` 复现
> （stderr 上的 `VIOLATION: <模块>/<文件>: <对象>: <原因>`）。
