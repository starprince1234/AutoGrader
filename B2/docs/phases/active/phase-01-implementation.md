# Phase 01 实现（B2）

> 本文件是 **Phase 01（实现阶段）的进行中记录**：真实命令、真实结果、以及与实现同期的缺口登记。
> 按 [../../../MAIN/docs/collaboration/module-doc-spec.md](../../../../MAIN/docs/collaboration/module-doc-spec.md)
> 的铁律二/铁律三：`已完成` 必须有阶段证据指向本文件；**未通过的验证、外部依赖阻塞、跳过的工作项
> 都留在这里**，不隐藏、不降级描述。逐 RQ 的追踪表刷新（[../../requirements/traceability.md](../../requirements/traceability.md)）
> 在 L1-5 收尾时把"证据位置"列指向本文件的对应小节。

## 目标与范围

按 [../../../MAIN/docs/workstreams/B2.md](../../../../MAIN/docs/workstreams/B2.md) 与 [../../requirements/scope.md](../../requirements/scope.md)
交付 B2 的**实现阶段**：提交接收（API + 邮件）、内容寻址存储、静态分析、安全判定、沙箱执行与编排、
证据与报告、成绩查询与导出、事件 outbox/inbox。**不含**：题目/参考答案/隐藏用例（B3 所有）、身份与课程
（B4 所有）、UI（B1 所有）；不改 `MAIN/contracts/**`、不改 `contracts.lock`、不导入其他模块代码。

## 输入与决策

- 权威输入：`MAIN/contracts/**`（openapi/asyncapi/schemas，`0.1.0`）、`MAIN/docs/workstreams/B2.md`、
  9 条 `RQ-B-2-*`（[../../requirements/acceptance.md](../../requirements/acceptance.md)）。
- 本模块决策：静态分析工具（[../adr/ADR-0001](../../adr/ADR-0001-static-analysis-tooling.md)）、
  沙箱策略（[../adr/ADR-0002](../../adr/ADR-0002-sandbox-execution-policy.md)）、
  内容寻址存储（[../adr/ADR-0003](../../adr/ADR-0003-content-addressed-submission-storage.md)）、
  提交信封来源列与幂等（[../../database/schema.md](../../database/schema.md)、[../../api/events.md](../../api/events.md) 第 3 节；迁移 `20260915_0008_b2_submission_provenance.py`。**注**：此处原指 `ADR-0004`，该 ADR 从未落盘，见缺口第 34 条）、
  运行/提交双状态机与终态语义（[../../architecture/models.md](../../architecture/models.md)；终态与 `terminal_reason` 的配对约束见迁移 `20260915_0009`）。**注**：此处原指 `ADR-0007`，该 ADR 从未落盘，见缺口第 34 条。
- 契约未被偷改（每次复核自证）：`contract_digest()` == `contracts.lock.digest` ==
  `de8f683496da78438a5cd1e6a12fc2aa30b871c63573ffb49e5e0abe5cc4b617`（18 个文件）；
  迁移三者一致：`alembic heads` → `20260915_0009 (head)` == `module.yaml: migration_head` ==
  `db/schema.py EXPECTED_SCHEMA_REVISION`。

## 完成条件

1. 9 条 `RQ-B-2-*` 各有可运行实现 + 真实依赖测试（PostgreSQL 17 与 NATS JetStream 均为真实实例，
   不用 mock 充当交付；沙箱为真实 nsjail，Windows 上不可验收的部分走容器化验收并如实标注）。
2. 本模块门禁可复现：`uv run ruff check .`、`uv run pytest`、`uv run alembic upgrade head`
   （离线 `--sql` 全链亦可）。
3. 缺口（含契约偏差与平台限制）在本文件登记，并在 [../../api/events.md](../../api/events.md)、
   [../../architecture/interfaces.md](../../architecture/interfaces.md) 等对应文档留可检索记录。

## 实际变更

分层与文件清单见 [../../architecture/components.md](../../architecture/components.md) 与
[../../architecture/interfaces.md](../../architecture/interfaces.md)；此处只记"哪些能力已落地"：

| 能力 | 主要落点 | 用例目录 |
|---|---|---|
| 提交接收（API） | `src/autograder_b2/api/**`、`intake/service.py` | `T-RECV-*`（[../../testing/cases.md](../../testing/cases.md)） |
| 提交接收（IMAP） | `intake/imap.py`、`intake/attachments.py` | `T-MAIL-*` |
| 内容寻址存储 | `storage/**`（CAS + 路径守卫 + 保留/清理） | `T-STORE-*`、`T-INT-008..010` |
| 静态分析 | `static/**`（Python `ast` + Bash `bash -n`）、`internal bash` 规则 | `T-STAT-*` |
| 安全判定 | `security/**`（规则集 + 结论三态 + 阻断门） | `T-SEC-*`、`T-INT-011` |
| 沙箱执行 | `runner/**`（nsjail 策略、超时进程树清理、资源账本） | `T-RUN-*`、`T-INT-007`、`tests/linux/**` |
| 评测编排与状态迁移 | `evaluation/orchestrator.py`、`evaluation/store.py`、`domain/states.py` | `T-RUN-005`、`T-PERSIST-*` |
| 证据与报告 | `evaluation/evidence.py`、`evaluation/report*.py` | `T-REPORT-*` |
| 成绩查询与导出 | `grades/export.py`、`api/grades_routes.py` | `T-PERSIST-003/004` |
| 事件 outbox/inbox | `events/**` | `T-EVT-001..021` |
| 结构化日志与脱敏 | `logging/**`；阶段日志在 `evaluation/orchestrator.py`；依赖故障日志在 `events/{nats,dispatcher,consumer}.py`、`api/**`、`intake/api.py` | `T-LOG-001..004`、`T-EVT-011/012/014/017/020` |

## 验证记录

> 均为如实记录，**不得伪报通过**。环境：Windows 10 + PowerShell，Python 3.12.3；
> 真实 PostgreSQL 17（Docker `autograder-b2-postgres`，127.0.0.1:**55433**）与真实 NATS JetStream
> （`autograder-b2-nats`，客户端 **54223** / monitor 58222）。命令前置：
> `$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"`，
> `$env:PYTHONIOENCODING="utf-8"`（GBK 控制台下打印非 GBK 字符会 UnicodeEncodeError）。

| 命令 / 检查 | 结果 | 未执行项及原因 |
|---|---|---|
| `uv run ruff check .` | `All checks passed!` | — |
| `uv run pytest -q`（全量） | 见下方"最近一次全量" | — |
| `uv run pytest` 降级配置（无 DB、无 broker） | `tests/test_events_outbox.py` → `9 passed, 16 skipped`；`tests/test_events_probe_review.py` → `3 passed, 12 skipped`（纯逻辑断言真跑，其中两条专门验证"断言有没有牙"） | 需要真实依赖的用例 skip 并写明原因（不伪造依赖） |
| `uv run pytest` 降级配置（有 DB、无 broker） | 两模块合计 → `19 passed, 21 skipped`（skip 集恰为需 broker 的 21 条：outbox 12 + 探针 9；**不得出现 failed**） | 同上 |
| `uv run alembic upgrade head` | 通过（head `20260915_0009`） | — |
| `uv run alembic upgrade head --sql`（离线全链） | 通过（**408 非空行 / 515 物理行**；迁移 docstring 保持 GBK 可编码） | — |
| 宿主驱动模式验收（`AUTOGRADER_B2_NSJAIL_VIA_DOCKER=1`，**文档推荐入口**） | `1 passed, 14 skipped in 25.02s`；容器内矩阵 `matrix_items=14 missing=[] not_passed={}`（含 `I1`/`I2` 两个集成项） | — |
| Linux/nsjail 验收（容器内） | 见 [../../../tests/linux/README.md](../../../tests/linux/README.md)：**两套**都被实跑 —— 隔离矩阵 `13 passed, 1 skipped, 1 xfailed`（该 `xfailed` 是其中的 `5a2`：本内核 `memory.max` 记账但不杀，`strict=False`，XPASS 即表示限制消失；含集成项 `I2` 实测 `--log` 的流分离）；真实 runner 验收 `5 passed, 1 skipped`（合法提交 `passed/exit 0`（`duration_ms` 为易变读数：各轮实测 313/332/387ms）、五个恶意样本各自被拦、超时零残留、真实编排两次同 `result_digest`、学生伪造沙箱侧归因失败且从沙箱内写日志被拒 `errno=13`） | Windows 主机无法验收 Linux 沙箱（清单与恢复操作见"风险与遗留项"第 28 条，状态 `active`） |
| 真实 IMAP/TLS | **未执行** | 无可用 IMAP 服务器，仅夹具；恢复条件：提供可连的 IMAP 端点 |
| 跨进程并发上限（沙箱全局闸门） | **未执行** | 本机无法构造受控的多进程竞争；恢复条件：独立压测环境 |
| MAIN 侧 worker/scheduler 驱动 `evaluate` / `dispatch` | **未执行** | 宿主侧工作项，不在本模块 |

**最近一次全量**（本小类"文档同步与证据归档"重跑，命令与上方一致，**用 `uv` 真实执行**）：

```text
uv --version                  → uv 0.10.2 (a788db7e5 2026-02-10)     # AGENTS.md 旧的"uv 未验证"告诫已过期
uv run ruff check .           → All checks passed!
uv run pytest -q -p no:warnings
                              → 1134 passed, 9 skipped, 1 xfailed in 159.86s   （2026-09-16）
                              （独立验证者同一命令复跑：1134 passed, 9 skipped, 1 xfailed in 159.46s）
uv run alembic upgrade head   → 无输出（已在 head，幂等）；uv run alembic current → 20260915_0009 (head)
uv run pytest tests/linux/test_nsjail_acceptance.py（宿主驱动模式，见上方表）
                              → 1 passed, 14 skipped in 25.02s；容器内矩阵 matrix_items=14 missing=[]
```

`skipped=9` 的**实测构成**（`-rs` 逐条打印，不是推测）：`tests/linux/test_nsjail_acceptance.py:75`
**模块级** skip 1 条（该模块在 Windows 上整个跳过，因此往里面加用例不会改变 skip 计数）、
`tests/linux/test_nsjail_runner_acceptance.py` 的 6 条（模块级 `pytestmark` ⇒ **收集后逐条 skip**）、
`tests/test_frozen_interfaces.py:154` 的 2 条（`Protocol 无字段`）。

**宿主全量里那 1 个 `xfailed` 是谁（独立验证者实测纠正，2026-09-16）**：是
`tests/test_migrations.py::test_downgrade_base_is_blocked_by_known_baseline_defect`（`strict=True`），
**不是** `5a2`——`5a2` 所在的 `tests/linux/test_nsjail_acceptance.py` 在 Windows 上是**模块级 skip**，
它不可能产生 xfail（本文件此前把这条 xfail 归错给了 `5a2`，已更正；`5a2` 的 xfail 只在**容器内**矩阵里出现，
见上方容器读数那一行）。该 xfail 记的是一个**真实已知缺陷**（基线迁移 `20260915_0001` 的 `downgrade`
执行 `DROP SCHEMA IF EXISTS b2` 无 `CASCADE` ⇒ `alembic downgrade base` 必然失败），已登记为缺口第 36 条。

历次全量：`1100` → `1101` → `1102` → `1115` → `1124` → `1127` → `1132` → **`1134 passed, 9 skipped, 1 xfailed`**
（本轮 pass 净 +2 = 新增沙箱日志分离相关 4 条 − 被分离取代的"日志前缀规范化" 2 条；skip 8 → 9 是
`tests/linux/test_nsjail_runner_acceptance.py` 新增 i8 后多一条收集后 skip——上一版这里曾把原因写成
"集成项 `I2` 在 Windows 上 skip"，**实测不成立**（I2 所在模块是模块级跳过，只计 1 条），已按实测更正）。
容器内另行实跑：隔离矩阵 `13 passed, 1 skipped, 1 xfailed`；真实 runner 验收 `5 passed, 1 skipped`。

**本小类（文档同步与证据归档）的执行记录**（真实命令 + 结果 + 未执行项）：

| 检查 | 真实命令 | 结果 | 未执行项及原因 |
|---|---|---|---|
| 三条命令 | 见上（`uv run ruff check .` / `uv run pytest -q -p no:warnings` / `uv run alembic upgrade head`） | 全绿（读数见上） | `uv sync --all-groups` **未执行**：环境已按 `uv.lock` 装好且 `uv run` 可用，本轮只跑验收标准要求的三条命令（不伪报） |
| 宿主驱动模式验收 | `$env:AUTOGRADER_B2_NSJAIL_VIA_DOCKER="1"; uv run pytest tests/linux/test_nsjail_acceptance.py -q -s` | `1 passed, 14 skipped in 25.02s`，容器内矩阵 `matrix_items=14 missing=[] not_passed={}` | — |
| 文档指针核对 | `.pytest-tmp/check_doc_pointers.py`（临时工具，逐条抽 `文件::测试名` 与裸测试名并核对定义是否存在） | 最终一轮：检查 **202** 处，三类不存在均为 **0**（本小类共修掉 5 处失效指针：`cases.md` 两处指向已改名用例、phase-01 一处、`tests/linux/README.md` 与 `verify/linux-nsjail/README.md` 各一处简写；早期一轮的计数是 185，随文档继续修改而变化——**计数以工具与口径为准，可复算**） | — |
| `cases.md` 存在性 | 逐行读实现与测试源码核对（4 个只读 subagent 分工取证 + 我复核） | **无"待建"残留**：40 行改为"存在"，其中 4 行如实写"部分覆盖"并点名**缺哪条断言**（T-RECV-004、T-SEC-002、T-SEC-005、T-REPORT-004） | — |
| 文档链接/表格核对 | `.pytest-tmp/check_doc_links.py`、`.pytest-tmp/check_md_tables.py`、`.pytest-tmp/check_doc_pointers.py` | **两个检查器的扫描范围都已改成"全仓 markdown 46 份"**（`docs/**` + `tests/**` + `verify/**` + 根目录）并**先打印范围**：相对链接 **279** 处、死链 **0**；表格 **100 张**、结构问题 **0**。（**方法论更正（两处同因）**：链接检查器起初只扫**固定清单的 16 份** ⇒ 漏掉 `phase-00` 里一条死链（独立验证者第 3 轮发现）；表格检查器起初只扫 **6 份** ⇒ 漏掉 `reviews/REVIEW_EVENTS_RECHECK.md` 表格内一处未转义 `\|`（第 4 轮发现）。两者均已改 glob，并让工具先打印扫描范围。教训：**"死链 0 / 结构问题 0"必须写明扫描范围**，否则清单外就是盲区。） | — |
| `tests/linux/**` 收集口径 | `pytest tests/linux -q --co` + `-rs` | 模块级 skip 1 条 + 收集后 skip 6 条（口径与 README/phase 的读数一致） | 本轮**经宿主驱动入口**复跑了隔离矩阵（`1 passed, 14 skipped`，矩阵 `missing=[]`）；**真实 runner 验收**（i2–i8）读数沿用本小类上一次容器内实测，本轮未重跑 |
| 独立验证（verify=subagent） | 由隔离验证者自行复跑三条命令 + 容器两套 + 逐行读文档 + 自写全仓扫描（25 条签名 × 171 个文件） | **第 1 轮**：标准 2 pass、标准 1/3 fail（xfail 归属、宿主驱动红灯、6 份旧状态文档）→ 逐条修复；**第 2 轮**：标准 1/2 pass、标准 3 fail（窄：又查出 2 份旧状态文档）→ 修复 + 缺口 39；**第 3 轮**：标准 1/2 pass、标准 3 fail（窄：F1 `onboarding.md` 3 行、F2 **我自己新引入的 1 条死链**、F3 `models.md` 无条件不变量）→ 修复 + 缺口 40 + 两个检查器改成全仓 glob；**第 4 轮**：**标准 1/2/3 全 pass（标准 3 首次）**，仅余 F-a/F-b 两条低项 → 补齐；**第 5 轮点状复核**：链接 279/0、表格 100/0、缺口号 36–40 无重复且状态如实，**F-a/F-b 闭合、无未清同项** | — |

## 风险与遗留项

> 每条都写明：已确认事实 / 阻塞或缺口原因 / 恢复验证所需操作。G-EVT-* 属「事件 outbox 与 inbox」
> 工作项；N* 是独立复核者在该工作项复核中提出的编号（记录见
> [reviews/REVIEW_EVENTS_RECHECK.md](reviews/REVIEW_EVENTS_RECHECK.md)）。

1. **G-EVT-1 inbox 主键与 `MAIN/docs/api/events.md` §5 字面不一致（契约偏差，已提 CHG）**：契约条目写
   "以 `event_id` 为主键"，实现用 `(consumer, event_id)`。已确认事实：复合键是单列键的**严格超集**
   （复核者实测反例：第二个消费者处理同一 `event_id` 时 `duplicate=False, handled=True`，单列键会把
   "没处理过的事件"判成"已处理"）。按 change-protocol 既不改契约也不偷改实现，CHG 提案见
   `MAIN/docs/contracts/changes/CHG-0002-*.md`。恢复验证：MAIN 裁定后按裁定结果同步。
2. **G-EVT-2 `evaluation.*` 事件的 `event_id` 是 UUID v5（硬清单偏差，已提 CHG）**：`MAIN/docs/api/events.md`
   §6 与 `MAIN/docs/api/identifiers.md` 要求 v4；实现用 `uuid5` 求确定性（`submission.accepted` 为 v4）。
   已确认事实：复核者实测 4 个 `evaluation.*` 事件的 `version=5`。CHG 提案见
   `MAIN/docs/contracts/changes/CHG-0001-*.md`。恢复验证：MAIN 裁定"改条文"或"改实现"后执行其一。
3. **G-EVT-3 契约没有 per-event payload schema（缺键曾可照发；B2 侧已加固，契约缺口仍在）**：契约里
   payload 只是 `{"type": "object"}`，因此"哪些键必填"无契约定义。已确认事实（复核者实测）：修复前
   `{"failure_category": "internal_error"}` 单键 payload 能过门**并真被投递**。B2 侧加固：`payload` 门
   现在**同时是必填键集**（`events.payload.REQUIRED_KEYS`）——缺必填键即拒、原因点名该键、不回显取值。
   **口径是"该键在每一次真实发布里都存在吗"，不是"示例里出现过"**：4 个 subject 的必填集 = 准许键全集
   （依据 asyncapi 官方示例的完整键集 + 无示例两个 subject 的生产调用点）；`evaluation.progress` 例外，
   必填集收窄为 `evaluation_run_id`/`sequence`/`stage`，因为 `cases_total`/`cases_done` 是**按阶段可缺**
   的计数（写入端 `_enter_stage(..., cases_total=None)` 会刻意省略，若列为必填则该形状过不了门 ⇒ 事件
   永不投递、进 DLQ；复核者用真实生产方法复现过这条链）。两种 progress 形状都必须过门，缺任一恒在键
   必须被拒（用例 T-EVT-021）。交付套件另钉三条不变量：`REQUIRED_KEYS`/`PAYLOAD_KEYS`/已发布 subject
   三者一一对应、`REQUIRED_KEYS[subject] ⊆ PAYLOAD_KEYS[subject]`（除 progress 外相等）、漏登记必填集的
   subject **fail-closed 拒绝**（不退化成"零必填"）。**已接受的取舍**：计数可选后，"本该带计数的阶段漏
   计数"不再被门拦住（属语义层，由写入端调用点决定）。契约层建议见
   `MAIN/docs/contracts/changes/CHG-0003-*.md`。
4. **G-EVT-4 `causation_id` 在生产路径恒为 `null`（契约不一致，已提 CHG）**：asyncapi 的
   `evaluationProgress` 官方示例给的是非空 `causation_id`，§6 要求"直接因果事件"。已确认事实：本模块
   `orchestrator.py` / `outbox.py` 的所有生产调用点都不传 `causation_id`。CHG 提案见
   `MAIN/docs/contracts/changes/CHG-0004-*.md`。恢复验证：MAIN 裁定后补因果链或改条文。
5. **G-EVT-5 消费端 lag 不可观测**：`src/**` 内没有消费端指标出口；`runtime.health_summary()` 只报 outbox
   积压与 DLQ 计数。已确认事实：MQ 运维按 [../../api/events.md](../../api/events.md) 第 3 节属 B4（RQ-B-4-20），
   lag 可经 JetStream 消费端指标观测。恢复验证：若 MAIN 判定"可观测"必须在 B2 代码内暴露，则新建工作项。
6. **G-EVT-6 无"乱序到达"专项用例**：`MAIN/docs/api/events.md` §5 要求（例：先收 `completed` 再收
   `started`）。已确认事实：B2 只订阅 1 个 subject 且语义与顺序无关（版本感知，不落 payload、不触发评测），
   乱序不产生业务效果。恢复验证：若 B2 增加订阅或引入顺序敏感处理，必须先补该用例。
7. **G-EVT-7 谁来驱动**：`InboxConsumer` 在 `src/**` 内无进程实例化；outbox 侧虽在 `B2Runtime` 装配了
   派发器，但 `src/**` 内**没有调度器**周期性调用 `dispatch`。已确认事实：两者都由宿主/运维驱动
   （MAIN 侧工作项）。**不得**把本模块读作"订阅链路已端到端运行"。恢复验证：宿主接线后补端到端用例。
8. **G-EVT-8 无显式事件大小上限**：契约未规定上限，本模块也无断言。已确认事实（实测）：envelope 体积
   298 B / 527 B 量级。恢复验证：若 MAIN 规定上限，加入门限断言。
9. **N4 `render_xlsx` 把墙钟时间写进产物 ⇒ `tests/test_b2_routes.py` 天然 flaky（✅ 已结案，2026-09-16）**：
   已确认事实（复核者实测 `docProps/core.xml` 的 `dcterms:created/modified` 每次不同、ZIP 条目 mtime 同，
   `sleep(1.3s)` 后同输入产物即不同字节），本轮全量因此出现过 `1 failed, 1099 passed`（重跑为全绿）。
   **修法（成绩查询与导出工作项）**：`grades/export.py` 的文档时间改为**从行数据派生**（这批行最新
   `submitted_at` 截到秒；空批次用固定基准 `1980-01-01`），`_write_xlsx` 不再走 openpyxl 的
   `save_workbook`（它在写盘前把 `properties.modified` 覆盖成 `datetime.now(utc)`），并由
   `_repack_deterministically` 把 ZIP 条目时间戳固定为 `(1980,1,1,0,0,0)`、`create_system=0`。
   **证据**：回归用例 `test_render_xlsx_is_byte_identical_for_the_same_rows_across_a_second_boundary`
   （跨秒两次导出逐字节相同）+ `test_repacked_xlsx_is_still_a_valid_workbook_with_the_same_visible_settings`；
   独立复核者用故障注入反证"用例有牙"（把 `_write_xlsx` 换回旧路径 → 用例失败；`docProps` 改回墙钟 →
   2 failed 连跑 3 次；ZIP 戳改本地时间 → 1 failed 连跑 3 次），并额外验证跨进程（随机 `PYTHONHASHSEED`）
   同字节、`zipfile.testzip()` 为 `None`、真实 Excel 可打开且冻结窗格/数字格式/列宽保留。
   本条的**残留边界**：XLSX 会把单元格内的 CR 规范化为 LF（`N6`），故含 CR 的值在"导出 vs 查询"之间
   不可能逐字段一致——今天不可达（两列恒 `null`），B4 接线补姓名/学号后会成为真问题，届时需按契约裁定。
10. **N6 门接受 `security_blocked` 作 `evaluation.failed.failure_category`（容许但不可达）**：该取值在
    `MAIN/contracts/schemas/evaluation-report.schema.json` 的**用例级**枚举内，故门接受它契约一致；而
    提交级"安全被拒"的正当表达是 `rejected` 终态，`_finish_rejected` **不发布领域事件**（已由源码断言钉住）。
    恢复验证：若 MAIN 收紧 §3.5 的措辞到取值域层面，按新条文调整。
11. **N7/§6 生产者清单未逐条对照**：已在 [../../api/events.md](../../api/events.md) 第 6 节补齐逐条对照表。
12. **沙箱可用性判定与平台限制**：[../../architecture/interfaces.md](../../architecture/interfaces.md) 与
    [../../../tests/linux/README.md](../../../tests/linux/README.md) 记录了 Linux 验收的适用范围与 xfail 项；
    Windows 主机上 nsjail 不可用 ⇒ **绝不降级为裸进程执行**（`SandboxUnavailableError`）。
13. **无 Worker/Scheduler 驱动（G-EVT-7 的推广）**：`B2Runtime.evaluate` 与 outbox `dispatch` 都需宿主
    周期性驱动；本模块只提供幂等入口与如实状态。
14. **G-GRD-1 成绩端点的契约侧缺口（已提 CHG-0005，四条一起交 MAIN 裁定）**：① `listGrades` 的
    `description` 要求"指名他人 `student_id` 必须 403 而非静默收窄"，但 `parameters` **未声明**
    `student_id`（`exportGrades` 声称"同 `listGrades` 的过滤语义"，同样未声明）；② 两个 operation 都
    未声明 `422`，而本模块按 `422` 实现（[../../api/contract.md](../../api/contract.md) 第 4 节本就是 422 约定）；
    ③ `/api/v1/grades` 未被 `module.yaml` 的 `routes` 覆盖（`b2.yaml` 开头"Every path below is declared
    in `B2/module.yaml` `routes`"对成绩两条路径不成立；复核者补证：宿主 registry 的重复路由保护只比对
    已声明前缀，故成绩前缀没有重复归属保护）；④ `exportGrades` 声明了 `404`，但 B2 **不拥有课程**——
    "课程不存在"与"课程无提交"在 `b2` schema 内不可区分，故实现从不返回 404（空结果 200 + 仅表头文件），
    且**不用 404 掩盖越权**（越权仍是 403）。**已确认事实**（复核者实测）：不存在的课程 admin 导出
    `200` + 仅表头 5205 B、查询 `200 items:[]`、教师 `403`、源码里 `NotFound`/404 出现 0 次。
    四条均按 change-protocol 提 CHG（不改契约、不改实现迁就），见
    `MAIN/docs/contracts/changes/CHG-0005-grades-endpoint-declarations.md`。恢复验证：MAIN 裁定后同步。
    **⑤（同日复核补充）**：未声明查询参数（含 `offset`）被框架**忽略**而非拒绝——负面声明只要求"不用
    offset 分页"，实测 `offset` 完全不生效（与不传时响应逐字节相同）、与 `cursor` 同现时 `cursor` 仍生效、
    代码里无 `OFFSET`；"严格拒绝未声明参数"属新行为，不在本工作项范围。
15. **G-GRD-2 导出全量入内存、无上限/超时（非规格违反，资源风险）**：`grades/export.py` 用 `list(...)`
    把结果集全部读进内存后渲染。已确认事实：复核者指出大课程下内存与响应时间无上界；契约与验收标准
    都未规定上限。恢复验证/修法（若需要）：加分页流式渲染或显式行数上限，并同步契约。
16. **G-GRD-3 测试基础设施缺 `pytest-timeout`**：分页"游标不前进"这类回归会让用例**挂死而非失败**
    （复核者实测：把 `_fetch_page` 改成忽略游标后被迫 kill，并因此残留一个临时库）。已确认事实：仓库
    未装 `pytest-timeout`；`pyproject.toml` 属需谨慎改动的共享文件。恢复验证：若采纳，加 `pytest-timeout`
    并对"分页/读回"类用例设超时（同时观察它与真实 broker 超时的交互）。
17. **G-GRD-4 `_document_moment` 的 1980 下界**：文档时间取 `max(1980-01-01, 各行最新 submitted_at)`；
    若某批行的提交时刻**全早于 1980**（现实中不可达），文档时间会偏晚且与空批次共享同一 `docProps`。
    已确认事实：复核者指出该边界；属理论边界。恢复验证：若允许 1980 之前的时间戳，改为"取行内最小值"
    或另设哨兵值。
18. **G-GRD-5 XLSX 把单元格内 CR 规范化为 LF（`N6`）**：xlsx 是 XML，行尾规范化不可避免，因此含 CR 的
    值在"查询 vs 导出"之间**不可能逐字段一致**。已确认事实：今天不可达（`student_number`/`display_name`
    恒 `null`，其余文本列是 UUID/枚举且有 CHECK 约束），但 B4 接线补姓名/学号后会成为真问题（实现者测试
    已书面标注）。恢复验证：届时按契约裁定（例如导出改用 `quotePrefix`，或在契约里说明 xlsx 的行尾规范化）。
19. **G-GRD-6 其他已记录的小边界**：转义前缀 `'` 会被 Excel 显示（OWASP 口径可接受，若要隐藏需用单元格
    `quotePrefix`）；`Content-Disposition` 只覆盖 ASCII 文件名（当前文件名由 UUID 构成）；
    Postgres 曾残留 13 个 `b2_test_*` 库（测试进程崩溃/并行运行遗留，**已清理**，清理命令与结果见本节末尾）。
20. **G-LOG-1 "DB 整体不可达"形态的日志用例未做（如实记录）**：把运行时指向**没有监听的端口**时，
    请求会先撞上运行时探活的长超时（本机实测 **~130 s**，且 `TestClient` 会把未捕获异常重新抛出），
    代价与判据无关。**改以两种真实可复现形态覆盖**"DB 不可用"：库结构缺失（进入查询前被拒 ⇒
    `grades.unavailable` + 503）与查询期缺表（真实 `SQLAlchemyError` ⇒ `persistence.database_unavailable`
    + 503 + 健康计数）。恢复验证：若需要覆盖"连接整体不可达"，得先给探活加短超时（属运行时配置改动）。
21. **G-LOG-2 依赖故障日志在"进入查询前的拒绝"路径上此前没有日志（本轮已修）**：`grades_routes._not_available`
    只返回 503（状态），不打日志。本轮补 `grades.unavailable`（含 `outcome=unavailable` 与内部原因文本，
    不含 PII）。同类路径（`evaluation_routes`/`intake/api` 的 `_not_available`）**尚未逐条补**：
    它们各自有 503 与健康分项，但**没有对应的不可用日志**——登记为遗留项，恢复验证：按同一模式补齐并加用例。
22. **G-LOG-3 日志量无节流**：失败逐条记日志（失败行 × 每次重试），节流属采集/告警层（见
    [../../runbooks/troubleshooting.md](../../runbooks/troubleshooting.md) 的两行处置）。已在 runbook 写明，
    不在代码里丢证据。
23. **G-L1-1 事件发布顺序此前不是不变量（✅ 已结案，2026-09-16 组级复核发现）**：`evaluation.started`
    与第一条 `evaluation.progress` 在**同一事务、同一 `occurred_at`** 入队 ⇒ `enqueued_at` 逐微秒相同；
    派发器按 `(enqueued_at, event_id)` 排序，而 `event_id` 是 `uuid5(去重键)`（键里含随机 run_id）⇒
    顺序由哈希决定。**实测**：20000 样本中 `progress` 排在 `started` 之前占 **50.2%**；生产写入路径 +
    生产派发器复现投递顺序 `['progress','started']`；真跑 4 轮 × 3 场景 = 12 次观测里 5 次 started-first、
    7 次 progress-first。**修法**：迁移 **0009** 给 `b2.outbox_events` 加单调插入序号 `insert_seq`
    （identity，按插入发生次序赋值）+ 索引，`NatsOutboxDispatcher.pending_rows`/`dead_letters` 改按它排序。
    **不变量因此变成"同一事务内先写的先发"**，与时间戳精度、哈希都无关（`enqueued_at` 降为记录性元数据）。
    **证据**：`tests/test_events_outbox.py::test_same_transaction_enqueues_keep_their_order_despite_identical_timestamps`
    （20 轮同一时间戳入队，每轮都断言 `[started, progress]` 且 `insert_seq` 严格递增）+
    `test_dispatch_preserves_insertion_order`（真实投递 + 真实读回，且 `enqueued_at` 刻意与插入顺序相反）。
    文档同步：`dispatcher.py` 模块 docstring 与 `docs/architecture/interfaces.md` 的顺序描述已按新不变量改写。
24. **G-L1-2 `run_case` 的真实 Linux 执行路径此前无验收证据（✅ 已结案，2026-09-16 Linux 沙箱验收小类）**：
    此前 `tests/linux` 只用**只读**方式校验 `policy` 产出的 nsjail 配置与 argv，`test_runner_core.py` 在
    Windows 上跑（走"缺 nsjail ⇒ 拒绝执行"路径），所以"`run_case` 在真实 Linux 沙箱里跑学生代码"没有证据。
    **本轮补齐**：新增 `tests/linux/test_nsjail_runner_acceptance.py`（导入**真实** `src/autograder_b2/**`，
    用生产签名 `run_case(package, submission)` + 真实 `PosixNsjailLauncher` 在真 nsjail 内执行），并在特权
    Linux 容器内实跑：**4 passed, 1 skipped**（第 24 条结案当时的读数；本小类新增集成项 `I2` 后复跑为
    `5 passed, 1 skipped`，逐字记录见 [../../../tests/linux/README.md](../../../tests/linux/README.md) §4.1），
    证据逐条如下（命令见该 README）：
    - **合法提交**（i2）：`passed=True failure_category=None exit_code=0 duration_ms=313`，实际 stdout `'42\n'`
      （真实进程执行、真实比较）。
    - **五个恶意/超限样本**（i3）：禁网 → `security_blocked` + `exit_code=159`(=SIGSYS) + 违规
      `runner.seccomp`；超时 → `timeout`/`wall`；输出洪泛 → `resource_limit`/`output`；fork bomb → 程序自身
      观测到 `FORK_BLOCKED BlockingIOError`（真实 EAGAIN）；内存申请 → 失败且非零退出（归类限制见第 26 条）。
    - **超时后无残留**（i4）：`存活 nsjail=[]`、`引用本工作区的存活进程=[]`、`僵尸=[]`（容器用 `--init` 收尸），
      且沙箱未报"进程树在宽限期后仍存活"。
    - **真实编排 + 真沙箱**（i5）：同一提交跑两次 → 两次都 `state=completed`、`score=1.0`、**同一
      `result_digest`**（结案当时记的字面值是 `3c60673b26c20600a8637ed0ab1e0b034347d058fc9b19e325bce6a1d73fd8af`；
      **该字面值随提交/包绑定与环境变化，不比字面值**——各轮实测还出现过 `a26838c9ab…ca055`，
      断言的口径是"同一提交两次之间逐字节相同"），DB 行状态
      `completed`；装配里只有"包来源/工作区物化"是替身（它们的真实路径另有集成验收），**沙箱与执行是真的**。
    - i7（沙箱不可用时拒绝执行）在容器内 `skip` 并写明原因：容器里沙箱**可用**，因此该负面路径需在缺
      nsjail 的环境验证（Windows 侧由 `tests/test_integration_adapters.py` T-INT-007 覆盖）。
    **本轮同时修掉两个真实缺陷**（见第 25、26 条）：沙箱探测找不到标准安装位置的 nsjail；限制命中被
    误归因给学生。
25. **G-L1-5 nsjail 探测只认 `/usr/bin/nsjail`，标准源码安装位置找不到（✅ 已修，高）**：
    `RunnerPolicy.nsjail_path` 默认 `/usr/bin/nsjail`，而 nsjail 从源码 `make install` 落在 `/usr/local/bin/nsjail`（本项目验收镜像即如此），
    且该字段**不来自 package 策略**（契约 `security_policy` 无此项）⇒ 正常供应的 Linux 主机上产品会**永远**
    判定沙箱不可用（fail-closed，但功能不可用）。**证据**：容器内真实 `run_case` 直接抛
    `SandboxUnavailableError: nsjail: nsjail=/usr/bin/nsjail exists+executable=False`（本小类首次接线时实测）。
    **修法**：新增 `NSJAIL_CANDIDATE_PATHS` 与 `resolve_nsjail_path()`（声明路径优先 → 候选位置回退；
    刻意不用 `PATH`，保证探测与启动看到同一绝对路径），`probe_sandbox` 与 `build_argv` 都走它；失败原因里
    列出搜索过的位置。**证据**：`tests/test_runner_core.py::TestNoDegradation` 的三条新用例（声明优先 /
    源码安装位置可解析且探测一致 / 都找不到时列出搜索位置）；修后容器内 i2 由红转绿。
26. **G-L1-6 限制命中归因：一次被红队推翻的修复（✅ 已结案，2026-09-16 修复于本小类）**：我曾把"nsjail 自有诊断行里的
    限制标记"提到输出比较**之前**，使被限制掉的进程归到 `resource_limit` 而非 `output_mismatch`，并声称
    "归因面不可伪造"。**红队真跑把该结论证伪**：
    - 旧 `_scan_markers` 的过滤只是 `"nsjail" in line.lower()`，而学生的
      stdout/stderr 与 nsjail 自有日志**写进同一份文件**⇒ 学生打印一行 `nsjail: out of memory` 即可让归类
      变成 `resource_limit/memory`；红队用真实编排层把这条伪造**落库**到 `b2.case_evidence`
      （`failure_category='resource_limit'`、`resource_limit_hit='memory'`）。
    - 该修复的正面分支在真实环境**没有扳机**：CPU/内存/文件大小三类限制下 nsjail **自有诊断行 0 条**
      （stderr 只有学生自己的 traceback）⇒ 只有可伪造的风险面、没有被证明的收益。
    **第一步处置（撤回）**：撤回提前归类分支（`classify_case_outcome` 恢复原顺序），把两个事实写进代码注释
    与 `LIMIT_MARKERS` 说明，测试改为钉住事实。
    **第二步处置（本次修复）**：把"可信边界"从**字符串启发式**换成**结构性事实**——
    `build_argv` 现在**必填** `sandbox_log_path`，形态为
    `[<nsjail>, "--config", <cfg>, "--log", <log>, "--", …]`，日志与配置同放工作区之外（校验：两者必须同目录，
    否则抛 `ValueError`）；`_execute_case` 启动前 unlink、读后即删；`RunFacts.sandbox_log_text` 成为
    **唯一**允许做沙箱侧归因的文本，`_scan_sandbox_log` 因此**去掉**了 `"nsjail" in line` 过滤（整份日志都是
    宿主自有诊断，过滤反而会漏掉真诊断）。被囚进程读不到也写不进这份日志（工作区父目录在沙箱内被遮蔽为
    只读空 tmpfs）。
    **证据（全部容器内真跑）**：① 分离本身——集成项 `I2`（`tests/linux/test_nsjail_acceptance.py`）用生产同一
    组函数跑两种真实情形：学生字节不落日志、nsjail 自身失败的 `[E]`/`[F]` 诊断不落被采集的进程流（stderr
    **0 字节**）；② 伪造不可行——`tests/linux/test_nsjail_runner_acceptance.py::test_i8_student_cannot_forge_a_sandbox_limit_attribution` 真跑：学生打印两行
    沙箱字样后归类仍是 `output_mismatch`/`resource_limit_hit=None`，且**从沙箱内往日志路径追加写入被拒**
    （`FORGED_LOG_WRITE=DENIED errno=…`）；③ 单测钉住"同一标记串写在日志里算命中、写进学生流不算"
    （`test_sandbox_diagnostics_are_read_from_the_log_file_not_the_student_stream`、
    `test_student_text_cannot_forge_a_limit_attribution`、`test_student_streams_are_recorded_verbatim_and_carry_no_sandbox_diagnostics`）。
    **残留的诚实结论**：内存耗尽在缺少沙箱侧诊断时仍无法与学生自身失败区分（容器内实测 `output_mismatch`）；
    生产配置 `log_level: ERROR` 下正常运行与三类真实限制的日志均为 **0 字节**，限制标记仍只作"已判定失败"的
    补充说明、**不得**用于归因；jail 级强制由 `test_05a_memory_limit_is_enforced` 实测证明。
27. **G-L1-7 运行期沙箱违规没有持久化落点（红队发现；越出本小类，如实登记）**：真跑到的 `runner.seccomp`
    违规（`CaseEvidence.sandbox_violations`）除进 `evidence_digest` 外无处落库——`case_evidence` 没有违规列，
    `security_findings` 只写静态判定，`record_runtime_findings` 没有生产调用方。已确认事实：红队在容器内真跑
    触发 `runner.seccomp` 后核对库表，违规明细不可查。恢复验证/修法（属后续工作项）：给 `case_evidence` 加
    违规明细列，或把运行期违规写进 `security_findings`，并补查询用例。
28. **Windows 不可执行项清单（状态如实保持 `active`）**：以下项在本机（Windows）**无法执行**，因此状态仍是
    `active`（未通过、也未撤销），每条都写明原因与恢复验证所需操作：

    | 项 | 无法执行的原因 | 恢复验证操作 |
    |---|---|---|
    | Linux 真实 nsjail 执行（本小类的两条 `tests/linux/**`） | nsjail 依赖 Linux 命名空间与 cgroup v2，Windows 内核不具备；本机 `probe_sandbox` 的 platform 检查为 False | 在特权 Linux 容器内跑（命令见 [../../../tests/linux/README.md](../../../tests/linux/README.md)）；已在本机 Docker 完成 |
    | `5a2` cgroup `memory.max` 杀进程 | 本机容器内核「记账但不杀」（`rlimit_as` 先命中） | 换支持该语义的内核；该用例已 xfail，XPASS 即表示限制消失 |
    | 真实 IMAP/TLS 接收 | 无可用 IMAP 服务器，仅有夹具 | 提供可连的 IMAP 端点后跑 `tests/test_email_intake*.py` |
    | 跨进程并发上限（沙箱全局闸门） | 本机无受控多进程竞争环境 | 在独立压测环境构造并发评测并核对进程/内存账本 |
    | 沙箱不可用时的"拒绝执行"负面路径 | 容器内沙箱**可用**，Windows 上的真实 `run_case` 也走拒绝路径但非同一环境 | Windows 侧已有 T-INT-007；如需容器内验证，可临时把 nsjail 移出候选路径后重跑 |

29. **G-L1-3 `state` 与 `terminal_reason` 的配对此前无强制层（✅ 已结案，2026-09-16 组级复核发现）**：
    `rejected + infrastructure_error` 这类自相矛盾的行可以落库（`store` / `progress` / 0007 的 CHECK 都只校验
    "枚举成员 + 非空"）。**修法**：迁移 **0009** 把配对写进两条 CHECK（`rejected` 只带
    `static_blocked`/`security_blocked`；`failed` 只带 `infrastructure_error`/`internal_error`），
    代码侧同一语义（`progress.TERMINAL_REASONS_BY_STATE` 是唯一定义处，`store._require_terminal_reason`
    与 `progress._validate_frame` 都用它）。**证据**：
    `tests/test_evaluation_pipeline.py::test_terminal_state_and_reason_must_be_a_contract_pair`
    （三层各验一次：代码层拒、帧层拒、绕开代码的原始 INSERT 被 CHECK 拒；并含正例）。
30. **G-L1-4 终态之后的重连此前空等 `idle_timeout`（✅ 已结案）**：运行已在终态、客户端带 `Last-Event-ID`
    重连时，旧实现会空等满 `idle_timeout`（复核实测 **30.44s**、0 帧）才关流。**修法**：
    `progress.frame_stream` 先查终态帧：若已存在且其 `sequence <= after`，立即返回（不进入等待循环）；
    只有"终态帧还没被该客户端读到"时才补发。**证据**：
    `test_frame_stream_returns_at_once_for_already_terminal_runs`（用会抛异常的 `sleep` 接缝证明没进等待循环，
    并验证 `after = 终态序号-1` 时必须补发）。
31. **G-L1-8 宿主侧 nsjail 建沙箱失败被记成学生侧 `output_mismatch`（红队第 2 轮发现，中，状态 `active`；
    越出"Linux 沙箱验收"小类，未在本轮修）**：红队实测两种**真实**基础设施失败——容器不带 `--privileged`
    （cgroup 创建失败）与 `--read-only`（`Couldn't mount '/workspace'`）——都是
    `probe_sandbox.available=True`，但归类落在 `cat='output_mismatch' exit=255 stdout=''`，而"学生代码一行未跑"。
    **根因**：`run_case._exit_code_failure` 里的 `exit_code == 255` 专用分支排在**输出比较之后**
    （`run_case.py` 的 `classify_case_outcome` 顺序：… 产出比较 → … → 退出码），因此该分支实际不可达：
    255 且标准流为空的"沙箱根本没起来"被当成"学生输出不符"。红队同时确认这**不是本轮回归**
    （探针打印"旧过滤会命中的 needle=None"，即旧代码同样如此）。
    **修法（待做）**：把结构化判定提前到输出比较之前——`exit=255` 且 stdout/stderr 双空且沙箱日志非空 ⇒
    `infrastructure_error`（`failed` + `infrastructure_error`，与学生侧失败严格区分）。
    **证据**：红队记录 `reviews/REVIEW_LINUX_SANDBOX_redteam_round2.md` §③F1 与探针 `.pytest-tmp/rt2_infra_failure.py`。
32. **G-L1-9 生产工作区不放开权限，服务以 root 运行时学生写不进任何文件（红队第 2 轮发现，中低，状态
    `active`；越出本小类，未在本轮修）**：红队 A/B 实测——生产 `WorkspaceProvider.prepare`
    （`integration/adapters.py` 的准备函数）不 chmod，目录权限 0o755；服务以 root 运行时被囚进程在外层是
    uid 65534 ⇒ 沙箱内写工作区 `WRITE_FAILED errno=13`；而 `policy.create_case_workspace` 里显式
    `os.chmod(path, 0o777)` 的那条路径在生产里**无人调用**（全仓仅测试引用）。
    **影响**：以 root 运行服务时，`file`/fixture 产出类用例全会失败（学生无法产出任何文件）；而交付的验收
    样本只写 stdout，所以这条路径**没有验收覆盖**。
    **修法（待做）**：把"沙箱内 uid 可写工作区"变成**显式不变量**（准备阶段统一设权限 + 断言），并补一个
    "学生产出文件"的验收样本（写入文件 + 摘要比对），而不是只靠 stdout。
    **证据**：红队记录 §③F2（同一次复跑里给出的 A/B 对照读数）。
33. **红队第 2 轮 F3–F7 的处置（全部为文档/断言精度问题，本轮逐条处理）**：F3（旧用例名
    "`..._outside_the_writable_workspace`"名不符实：`build_argv` 只能校验"与配置同目录"，
    "在工作区之外"是调用方不变量）→ ✅ 已改名为
    `tests/test_runner_core.py::test_sandbox_log_must_live_in_the_config_directory`，并把调用方侧不变量
    断言放进 `::test_sandbox_log_is_a_per_run_artifact`；F4（`verify/linux-nsjail/README.md`
    残留 `4 passed`）→ ✅ 已改 `5 passed`；F5（本条上游第 24 条残留 `4 passed` 且无标注）→ ✅ 已标注
    "第 24 条结案当时读数 + 复跑读数"；F6（`policy.py` 顶部把证据指向"第 24/26/27 条"，而第 27 条是
    `G-L1-7`）→ ✅ 已改为"第 24/26 条"，Windows 清单指向第 28 条；F7（边界依赖"argv 无
    `--pass_fd`/`--log_fd`"与"config+log 在工作区之外"这两处恰好为真的约定，缺显式断言）→ ✅ 已在
    `tests/test_runner_core.py` 的 argv 测试里补显式断言防静默破坏。
    另：红队建议把 i8 的 `FORGED_LOG_WRITE=DENIED` 收紧为 `errno in (13,30)` 或补 `os.access(..., W_OK) is False`；
    **本轮未改 i8**（它是红队第 2 轮已验证的产物，我不在验证后偷改被验证物），而是把红队的**独立**证据
    （errno=30 = 撞只读层而非"父目录不存在"；对照组 chmod 后 `WRITE_OK`；无继承 fd）记入
    [../../../tests/linux/README.md](../../../tests/linux/README.md) 的证据段，并在 i8 的 docstring 里写明
    "DENIED 无法区分 ENOENT/EACCES，边界结论由那三条独立证据支撑"。
34. **G-DOC-1 阶段文档引用了两个从未落盘的 ADR（本小类文档同步时发现，低，状态 `active`）**：
    `phase-01` 的"输入与决策"里把"提交信封来源列与幂等"记在 B2 `ADR-0004-submission-provenance-and-correlation.md`、
    把"运行/提交双状态机与终态语义"记在 B2 `ADR-0007-run-and-submission-state-machines.md`，但
    `docs/adr/` 实际只有 `ADR-0001..0003`（[../../adr/README.md](../../adr/README.md) 的索引也只列这三条），
    MAIN 侧同编号 ADR 是另外的主题（`ADR-0004-sandbox-technology-selection`、`ADR-0007-b1-static-client-provided-by-host`）
    ⇒ 这两条链接**指向不存在的文件**（`check_doc_links` 报死链）。
    **本轮处置**：不假装 ADR 存在，把两处引用改为**真正落地的权威位置**
    （来源列/幂等 → [../../database/schema.md](../../database/schema.md) + [../../api/events.md](../../api/events.md) 第 3 节
    + 迁移 `20260915_0008_b2_submission_provenance.py`；双状态机/终态语义 →
    [../../architecture/models.md](../../architecture/models.md) + 迁移 `20260915_0009`），并在原处标注"该 ADR 从未落盘"。
    **修法（待定）**：要么补写这两份 ADR（如果 MAIN 的文档规范要求"实现期决策必须有 ADR"），要么把这个决策
    正式登记为"不写 ADR、以落地文档为准"。本小类的任务是文档同步，**不擅自新建 ADR**，故留缺口给后续工作项决断。
35. **G-DOC-2 文档相对链接深度错误（本小类文档同步时发现，低，✅ 已修）**：`phase-01`（位于
    `docs/phases/active/`，比 `docs/*.md` 深一层）有 **24 处**链接少写一层 `../`（`../requirements/...`、
    `../api/...`、`../architecture/...`、`../testing/...`、`../runbooks/...`、`../README.md`），另有 5 处
    指向 `tests/linux/README.md` 时写成 `../../tests/...`（应为 `../../../tests/...`）；`docs/README.md`
    则有 **4 处**多写一层（`../../tests/...` 应为 `../tests/...`）。全部为相对路径深度错误，链接文本与 href 同错。
    **修法**：用逐条声明期望次数的脚本做精确替换（次数不符即中止不写），共 33 处；
    核对工具 `.pytest-tmp/check_doc_links.py`（临时工具，不进仓库）复跑——**扫描范围=全仓 markdown 46 份
    （`docs/**` + `tests/**` + `verify/**` + 根目录）**：当次 **279** 处相对链接、死链 **0**。
    （该工具起初只扫固定清单的 16 份 ⇒ **第 3 轮复验时被独立验证者用全仓扫描抓到 1 条清单外死链**
    ——`phase-00` 里我自己新写的 `../../contracts.lock`，已修为 `../../../contracts.lock`；
    见上方"验证记录"的方法论更正。）
36. **G-L1-10 基线迁移的 `downgrade base` 必然失败（独立验证者发现其未被登记，中，状态 `active`；
    此前只记在复核记录里、且被我在 phase-01 里错误归因）**：全量套件里**唯一**的 xfail 标记是
    `tests/test_migrations.py::test_downgrade_base_is_blocked_by_known_baseline_defect`（`strict=True`），
    原因写在测试里：基线迁移 `20260915_0001` 的 downgrade 执行 `DROP SCHEMA IF EXISTS b2`（**无 `CASCADE`**）
    ⇒ 只要 `b2` 里已有对象，`alembic downgrade base` 就必然 `DependentObjectsStillExist`。
    **已确认事实**：`uv run pytest tests/test_migrations.py -q -rxX` → `18 passed, 1 xfailed`；
    `uv run pytest -m xfail --collect-only` → 全仓仅 1 条。
    **影响**：降级/回滚路径不可用（本阶段不依赖回滚，故未阻塞交付），但这是一条**真实产品缺陷**，不该只存在于测试标记里。
    **修法（待做）**：把 0001 的 downgrade 改为 `DROP SCHEMA IF EXISTS b2 CASCADE`（或先删对象再删 schema），
    并去掉该 xfail（`strict=True` 会在修好后报 XPASS，正好当扳机）。
    **证据**：`tests/test_migrations.py` 的 xfail 声明；复核记录
    [reviews/REVIEW_L1_reception_storage.md](reviews/REVIEW_L1_reception_storage.md)（复核者当时注明"仅登记"）。
37. **G-DOC-3 宿主驱动模式验收此前必然失败且从未被观察（独立验证者发现，高，✅ 已修）**：本小类新增集成项
    `I2` 时，把两条真实情形的命令拼成了一个**含真实换行**的字符串（`tests/linux/test_nsjail_acceptance.py`
    的 I2 `_record(...)`），而 `_render_matrix` 只对**输出**列调 `_one_line`、**没有**展平命令列 ⇒
    容器输出的 I2 行跨两个物理行；宿主侧 `_parse_matrix` 是**按行**解析且要求 ≥4 列 ⇒ I2 永远解析不到 ⇒
    `test_docker_driver_runs_acceptance_in_privileged_linux_container` 的 `assert not missing` 必然失败。
    **为什么没被早发现**：容器内跑是绿的（`13 passed, 1 skipped, 1 xfailed`），而宿主驱动模式我在本小类里
    **只跑了容器内那层、没跑驱动入口**（`phase-01` 当时还写着"容器内实跑不在本轮"）——红灯因此从未被观察到。
    **修法**：`_render_matrix` 对命令列也调 `_one_line`，并在 docstring 里写明"一行一项"是矩阵契约。
    **证据（修后实测）**：`$env:AUTOGRADER_B2_NSJAIL_VIA_DOCKER="1"; uv run pytest
    tests/linux/test_nsjail_acceptance.py -q -s` → **`1 passed, 14 skipped in 25.02s`**，
    矩阵 `matrix_items=14 missing=[] not_passed={}`；独立验证者首轮实测的失败形态
    （`1 failed, 14 skipped`、`missing=['I2 …']`）记在其报告里。
38. **G-DOC-4 六份文档仍写与实现相反的旧状态（独立验证者发现，中，✅ 已修）**：本小类自称"文档状态已按实现
    刷新"，但以下文件与实现相矛盾、且自 2026-09-15 起未更新：
    `docs/database/migrations.md`（只列基线 0001、写 `migration_head: baseline`、写业务表"尚未创建"）、
    `docs/architecture/models.md`（运行状态机标"待实现"）、`docs/testing/strategy.md`（整节"骨架阶段尚不能执行的检查"，
    含"uv 未验证""业务端点尚未实现""已存在的测试只有 `tests/test_plugin.py`"）、
    `docs/runbooks/troubleshooting.md`（"当前 B2 为骨架阶段"、`router()` 空路由等行）、
    `docs/collaboration/definition-of-done.md`（`descriptor.events` 仍记 `阻塞` 未解除）、
    `docs/phases/active/phase-00-initialization.md`（RQ 状态仍写 `骨架`/`待实现`）。
    **修法**：逐份按实现改写，并给出可核验的读数/测试指针（见各文件当前内容）；
    另外 `phase-01` 自身两处数字也一并更正：复核记录份数"8 份"→**12 份**、"`b2_*` 残留为 0"→
    实测曾残留红队第 2 轮的 `b2_rt2_e8ce86af`（**已 DROP**，现 `b2_test_*`=0）。
    **第 2 轮复验又查出两份同类文件（同一缺口的追加）**：`docs/runbooks/local-development.md`
    （"`uv` 尚未在本环境验证""当前仅建 b2 schema""当前仅 T-PLUGIN-001""当前未注册真实路由"）与
    仓库首页 `README.md`（"状态：骨架""health 占位文案""无业务表""空 APIRouter""`descriptor.events` 待修"
    "`migration_head: baseline`"等 9 处）——均已按实现改写；这说明**"扫一遍旧状态"不能只扫 `docs/**`**，
    根目录与 runbook 同样要扫（教训记此）。
    **第 3 轮复验再补 1 份（本缺口合计 9 份）**：`docs/collaboration/onboarding.md`（`docs/README.md` 索引的
    新人入口）仍有 3 行 live 旧状态——"在本沙箱执行会失败"、`pytest`"当前仅 `T-PLUGIN-001`"、
    `migration_head`"当前 `baseline`"；独立验证者用 `uv cache dir` + `uv sync --all-groups --dry-run`
    （`Would make no changes`）反证了第一条。三行均已按实测改写并标注"已被反证/已过期"。
    **教训（本缺口第二次同类）**：它的"已修"范围被验证者扩大了两次 ⇒ "扫旧状态"不能靠固定清单，
    必须**全仓 glob + 写明扫描范围**（与缺口 35 的链接检查器是同一个方法论问题）。
    另：本轮还顺手修掉归档复核记录 `reviews/REVIEW_EVENTS_RECHECK.md` 一处**表格内的未转义 `|`**
    （`grep \`lag|metric\`` ⇒ `lag\|metric`，机械转义、语义不变；它的存在说明表格检查器当初也只扫了 6 份文件）。
39. **G-L1-11 真实沙箱的"资源账本"从未实测（独立验证者建议登记，低，状态 `active`）**：
    `docs/runbooks/local-development.md` 把"资源账本实测（≤96 MB RSS / 0.45 vCPU）"列为 Linux 侧**必做**，
    而 `reviews/REVIEW_L1_orchestration.md` 与 `_RECHECK` 都明说"真实沙箱进程的资源账本未测"；
    `phase-01` 的未执行/缺口清单里此前没有这一条（最接近的是第 28 条的"跨进程并发上限"）。
    **已确认事实**：它不是 `RQ-B-2-50` 的验收判据（`requirements/acceptance.md` 的 2-50-A..F 六个场景无此项），
    也没有任何文档把它写成通过 ⇒ **不构成隐藏失败**，但既然基线 `downgrade` 缺陷都登记了，这条也应登记。
    **修法（待做）**：在特权容器内跑单并发/多并发作业，按 `docker stats` 或 cgroup `memory.peak`/`cpu.stat`
    记录真实 RSS 与 CPU 占用，与 MAIN 资源账本对账后写入 phase-01 与本文件。
40. **G-L1-12 提交级状态无法表达"第二次及以后的运行"，与文档无条件不变量不符（独立验证者第 3 轮发现，
    中低，状态 `active`）**：组级复核早已点名并记为残余项 **R1**
    （[reviews/REVIEW_L1_orchestration_RECHECK.md](reviews/REVIEW_L1_orchestration_RECHECK.md) 第 103-116 行原文：
    "`docs/architecture/models.md:31` 的『`queued` 之后 1:1 同步』在这条路径上不成立"），
    但**此前只活在复核记录里**：`docs/architecture/models.md` 无任何限定，本缺口清单里也没有 R1/重复评测
    的任何记录（独立验证者 grep 过）——与首轮"基线 `downgrade` 缺陷只活在测试标记里"是同一类。
    **已确认事实（实现自述）**：`src/autograder_b2/evaluation/store.py` 的终态同步函数 docstring 明写
    "本函数让提交状态反映**首次**评测周期…**第二次及以后**的运行无法在提交级表达"（写入点仅在
    `states.can_transition` 为真时同步 `submissions` + 记迁移史）。运行侧历史仍完整落在
    `b2.evaluation_run_state_transitions`，不丢失。
    **本轮处置**：`docs/architecture/models.md` 该行加**限定**（1:1 只对首次评测周期成立，并指向本条），
    不再无条件宣称该不变量。
    **修法（待定，需 MAIN 判定契约）**：重复评测在提交级应表达为"状态不变 + 历史追加"，还是新增
    `re-evaluating` 类状态/`latest_run_id` 指针——属跨模块契约问题，B2 **不擅自改**；MAIN 定后再同步实现与文档。
    **证据**：`REVIEW_L1_orchestration_RECHECK.md` 的 R1；`store.py` 的 docstring 与 `can_transition` 守卫。
41. **G-DOC-5 `docs/database/schema.md` 的表清单只有 6 张单数名表、漏 11 张（组收官自查发现，中，
    ✅ 已修；**归属小类：文档同步与证据归档**）**：本小类任务清单里明确点名要同步 `database/schema.md`
    （"表意图"），但我当时只刷新了 `docs/database/migrations.md`，**漏了 schema.md** ⇒ 该文件仍写着
    设计期的单数名（`submission`/`artifact`/`evaluation_run`/`evaluation_result`/`security_finding`/`email_checkpoint`），
    与实现不符：实际 `src/autograder_b2/db/schema.py` 有 **17 张复数表**（迁移 `0002`–`0006` 创建），
    文档**漏掉 11 张**（`submission_files`、`intake_quarantines`、`artifact_gc_runs`/`artifact_gc_audits`、
    `outbox_events`、`inbox_events`、`submission_state_transitions`、`evaluation_run_state_transitions`、
    `progress_events`、`case_evidence`、`static_findings`）。
    **为什么小类内没发现**：我做的"文档与实现一致"扫描只覆盖了**文档之间/文档与代码注释**，没有做
    "文档里点名的**标识符**是否在代码里存在"这类**反向**核对（表名是最典型的标识符）。
    **修法**：整表重写为 17 张真实表（含来源迁移、意图、约束意图、生命周期），并加"表数一致性自检：
    `schema.py` 计数 17 == 本表行数 17 == 各迁移 `create_table` 之和"，工具
    `.pytest-tmp/list_table_migrations.py`（临时工具，不进仓库）可复算；敏感数据标记与关系图同步改用真实表名。
    **教训**：一致性核对要**双向**——既要"文档里的说法能否在实现里找到"，也要"实现里的标识符是否都在文档里出现"。
42. **G-API-1 报告端点：契约声明了 `409`，实现从不使用；且"运行不存在"与"运行在但没报告"曾用同一句话（成品走查发现，
    中，半修：detail 已修 / 状态码待 MAIN 判定）**：`MAIN/contracts/openapi/b2.yaml` 的
    `/api/v1/evaluation-runs/{evaluation_run_id}/report` 声明 `200/401/403/404/409`，而实现只在
    "结果行不存在"时返回 `404`，**从不返回 409**；两种语义不同的情形（运行不存在 / 运行存在但无报告：
    `queued`/`running`/`rejected`/`failed`）此前共用同一句 `"The evaluation run does not exist."`。
    **终验走查实测踩到**：教师走"评测失败"路径时拿到 `404 + "does not exist."`，可他在成绩单里**看得到**
    这一行（`GradeRow.state="failed"`, `score=null`）——接口与用户所见自相矛盾。
    **本轮处置（不改契约、不改状态码）**：新增 `_no_report(instance, run)`，`detail` 改为如实描述
    （"运行存在但没有报告（state: failed，终态原因 infrastructure_error）；报告只在成功运行后产生"），
    **仍为 404**；真不存在时保持原句。回归断言补在既有用例
    `tests/test_b2_routes.py::test_report_is_404_for_rejected_and_failed_runs`（含"未知 run 仍说不存在"的反例）。
    **待 MAIN 判定的契约侧问题（CHG 候选）**：契约里的 `409` 是否就用于"运行存在但尚无报告"？
    若是，实现应对齐到 `409`（那是**契约语义**问题，按 `change-protocol.md` 交 MAIN 判，B2 不擅自改契约、
    也不偷偷把状态码改掉）。**归属**：报告/API 面属「动态评测与报告导出」大类。
43. **G-DELIVERY-1 仓库零 commit（终验发现的交付面事实，状态 `active`，需用户决定）**：`git log` 报
    `does not have any commits yet`，全部文件 untracked（`git status` 列出 `docs/`、`src/`、`tests/`、
    `alembic/`…）。**影响**：外部评审者拿不到 diff/历史，无法区分"本轮改了什么"、也无法用 git 复现某个读数对应的版本；
    独立验证者与红队都因此声明"无法考古历史读数"。**处置**：按 `AGENTS.md`（未经明确授权不提交）**我没有 commit**，
    仅登记事实与建议：由模块 owner 授权后做首次提交（或由 MAIN 统一决定基线提交时机）。
44. **G-DELIVERY-2 验证工具位于 gitignored 的 `.pytest-tmp/`，文档引用它们但陌生人拿不到（终验发现，低，状态 `active`）**：
    `phase-01` 的"验证记录"引用了 `check_doc_links.py` / `check_md_tables.py` / `check_doc_pointers.py` /
    `list_table_migrations.py` 等（用于"链接/表格/指针/表数"这几项**可复算**结论），但它们在 `.pytest-tmp/`（gitignored），
    克隆后的仓库里**不存在** ⇒ 引用可读、不可复算。**处置**：文档里已逐处标注"临时工具，不进仓库"（不伪装成可复算），
    本轮不擅自把它们塞进仓库（那属于新增交付物，需 owner 决定）；**建议**：若要长期保留这几项核对能力，
    把三个检查器移入 `verify/`（与 `verify/linux-nsjail/` 同类，随仓库分发），届时把 phase-01 的引用改指新路径。

**本工作项（结构化日志与脱敏）的验收证据**：

- 三条验收标准逐条对应 `tests/test_logging_pipeline.py` 的 9 条用例（字段完整性 + 白名单丢弃 + 五入口脱敏 +
  单一 `correlation_id` 串联六阶段 + 沙箱/DB（两种形态）/NATS 三条依赖故障各有日志与状态）。
- **本轮实际改动**（都是"把已有能力接到链路上"，不改变任何业务语义）：
  1. `evaluation/orchestrator.py`：`run()` 用 `bind_operation(correlation_id=submission.correlation_id, …)`
     绑定业务操作，并用新增的 `_stage(name)` 把 7 个阶段包进 `bind_context(stage=…)` 且**每阶段成功后记一条**
     `evaluation.stage_completed`（只写阶段名与计数）；终态另记 `evaluation.run_started/completed/rejected/failed`；
     沙箱不可用与题目包不可用各记一条 `log_dependency_unavailable`（`runner.sandbox_unavailable` /
     `question_package`）**并照旧抛出**（不吞）。
  2. `events/nats.py`：连接失败记 `messaging.broker_unavailable` 后照旧抛 `EventBusUnavailable`（状态侧
     `error_count`/`last_error` 不变）。
  3. 三处路由文件（`api/evaluation_routes.py`、`api/grades_routes.py`、`intake/api.py`）的 6 个
     `except SQLAlchemyError` 分支补 `persistence.database_unavailable`；`intake/api.py` 的接收路由绑定
     日志上下文并记 `submission.accepted`/`submission.rejected`（含 `stage=reception`）；
     `grades_routes._not_available` 补 `grades.unavailable`。
- **两遍法证据**：主链另写对抗探针 `.pytest-tmp/lead_logging_probe.py`（3/3 通过），把标记塞进
  **文件内容 / 幂等键 / `traceparent` / `X-Request-ID` / 异常消息**五个入口后逐行 dump 真实日志，
  标记零命中；并核对沙箱与 broker 两条故障的"日志 + 状态 + 不吞异常"。
  实测输出（节选）：`[1 字段] 1 行全部是合法 JSON；接收行键=[class, correlation_id, event, level, logger,
  outcome, producer, request_id, stage, submission_id, timestamp]`；
  `[3 异常] 新增日志事件=['evaluation.run_started', 'evaluation.stage_completed'×4,
  'runner.sandbox_unavailable', 'evaluation.run_failed', 'messaging.broker_unavailable']`。
- **如实标注的边界**：中文/文本内容不进日志靠**字段白名单**（不是事后扫描）；"DB 整体不可达"形态见 G-LOG-1；
  进入查询前的拒绝路径尚未逐条补日志见 G-LOG-2。

**环境清理（如实记录）**：`b2_test_*` 残留库 13 个已删除（它们在测试进程中本应 `DROP ... WITH (FORCE)`，
崩溃/并行运行会留下），当时确认 `b2_*` 残留为 0。
**后续更正（2026-09-16，独立验证者实测）**：该结论一度不再成立——红队第 2 轮的临时库
`b2_rt2_e8ce86af` 当时仍在，现已 `DROP`；当前实测 `b2_test_*`=0 且 `b2_*`=0（只剩
`autograder` / `postgres` / `template*`）。**教训**：这类"当时为真"的清理结论必须带时间戳，
否则下一轮复算就会自相矛盾。

**本工作项（成绩查询与导出）的验收证据**：

- 三条验收标准（A 导出口径一致 / B 公式注入 / C 越权与未完成）由**两方独立**验证：实现者的真实依赖用例
  （`tests/test_grades_query_export.py` 39 条 + `tests/test_b2_routes.py` 65 条）与**主链独立探针**
  （`.pytest-tmp/lead_grades_probe.py`，自建临时库 + 自签 JWT + 真实路由，9/9 通过）。
- 独立复核者（verify=subagent，与开发隔离）复核记录：[reviews/REVIEW_GRADES_RECHECK.md](reviews/REVIEW_GRADES_RECHECK.md)。
  其自写探针 196/196 not-FAIL、并发探针 12/12；A/B/C 三条判 **PASS**；对确定性修复做了**证伪尝试**
  （跨秒、空批次、单行、跨进程、真实 HTTP）；用故障注入反证既有测试"有牙"（拆掉转义 → 10 failed，
  未完成记 0 分 → 5 failed，`docProps` 改墙钟 → 2 failed×3，ZIP 戳改本地时间 → 1 failed×3，
  分页丢行 → 5 failed，学生不收敛 → 6 failed）；并用**真实 Excel（COM 只读打开）**核验了
  `HasFormula=False`、中文姓名完好、未完成行得分为空、真实 0 分行仍为 0。
- **如实标注的未证实项**：HTTP 层的中文**值**结构上不可达（两列恒 `null`），中文值证据在渲染层 + 真实 Excel；
  公式注入的防线在渲染层（DB 无可承载触发字符的列），端到端链条为"渲染层字节 + HTTP 字节 == 渲染器字节
  + 整包 `<f>` 扫描"；并发（导出期间新提交）由复核者的 4 条探针补充覆盖（实现者侧无用例）。

**本大类（动态评测与报告导出）的组级验收**：三条组级标准由**独立 subagent** 逐条复核（记录
`reviews/REVIEW_GROUP_L1_recheck.md` + 复裁 `reviews/REVIEW_GROUP_L1_recheck_verdict2.md`）：
① 重放与可追溯 **PASS**（同五项绑定摘要重放 3 次同分同 `result_digest`；逐项之和 == 总分且可手工复算；
用自己的校验器过 MAIN schema 0 errors）；② 沙箱与降级 **PASS**（容器内真 nsjail `12 passed, 1 skipped,
1 xfailed`——复核时的读数；本小类新增集成项 `I2` 后复跑为 `13 passed, 1 skipped, 1 xfailed`；
Windows 上真 `run_case` 抛 `SandboxUnavailableError` 且审计钩子记录**零进程创建**）；
③ SSE/导出/不泄漏 **PASS**（真实 TCP 上 `sequence` 1..12 严格单调、终态唯一且在最后；42 个恶意单元格
0 未转义、XLSX 0 公式单元格；帧/日志/事件/导出四处扫描标记零命中）。
复核发现的两条必修缺陷（发布顺序仅一半运行成立、`state↔terminal_reason` 配对无强制）**已修复并复裁解除**
（G-L1-1、G-L1-3＝第 23、28 条）；低项 3 已修（G-L1-4），低项 4 已在本小类结案（G-L1-2＝第 24 条），低项 5/6 已修。

**复核后可选清理（已完成，低收益但消除了"占位/文案过期"）**：迁移 0009 的 `ix_outbox_events_insert_seq`
从普通索引改为**与查询同形的 partial 索引**（`WHERE published_at IS NULL AND dead_lettered_at IS NULL`），
规划器改为 `Index Scan using ix_outbox_events_insert_seq`（不再需要 `Sort Key: insert_seq`）；删除
0009 里遗留的 `sa.text("")` 占位语句；`progress.SCHEMA_CONFLICT_CONSTRAINTS` 的说明补上 0009 的配对语义；
迁移 docstring 与 [../../runbooks/troubleshooting.md](../../runbooks/troubleshooting.md) 补上**升级前置检查**
（若库里存在 0007 语义下的跨域配对行，升级会原子失败：版本仍 0008、无残留，修数据后重试即可）。


## 文档影响检查

- 本文件登记进索引：[../../README.md](../../README.md)（文档地图 + 状态表）。
- 缺口登记位置：本文件（根因与恢复条件）+ [../../api/events.md](../../api/events.md) 第 5/6 节（事件侧逐条对照与偏差）
  + [../../api/contract.md](../../api/contract.md)（成绩端点四条契约侧缺口）+ 契约变更提案
  `MAIN/docs/contracts/changes/CHG-0001..0005`（均"提议，待 MAIN 判定"）。
- 复核记录归档：`docs/phases/active/reviews/`（**截至本小类共 12 份**，逐份列名以免再对不上：
  `REVIEW_L1_reception_storage.md`、`REVIEW_L1_static_security.md`、`REVIEW_L1_orchestration.md`、
  `REVIEW_L1_orchestration_RECHECK.md`、`REVIEW_GROUP_L1_recheck.md`、`REVIEW_GROUP_L1_recheck_verdict2.md`、
  `REVIEW_GRADES_RECHECK.md`、`REVIEW_EVENTS_RECHECK.md`、`REVIEW_EVENTS_RECHECK_ROUND2.md`、
  `REVIEW_EVENTS_RECHECK_ROUND3.md`、`REVIEW_LINUX_SANDBOX_redteam.md`、
  `REVIEW_LINUX_SANDBOX_redteam_round2.md`）。**更正**：此处此前写"共 8 份"，实际 12 份（`docs/README.md`
  一直是 12 份并列名，phase-01 未随之刷新；由本小类的独立验证者发现）。
- 追踪表 [../../requirements/traceability.md](../../requirements/traceability.md) 已于 2026-09-16 **逐行刷新**
  （9 行均指向真实测试与阶段证据；未验收项在"未执行/遗留项"里列明，不因 `已完成` 而消失）。
- 用例目录 [../../testing/cases.md](../../testing/cases.md) 已登记 `T-EVT-001..021`（事件）、`B2-GRD-01..12`（成绩）、
  `T-LOG-001..004`（日志），并已按本小类的逐行核对把**全部"待建"抹掉**（40 行：36 行"存在" + 4 行"部分覆盖"）。
- 状态表 [../../README.md](../../README.md) 的能力状态同步为 `已完成`，并保留未验收项的指针。
- 迁移 head 从 `20260915_0008` 升到 `20260915_0009`（新增 `outbox_events.insert_seq` 与两条配对 CHECK），
  `module.yaml: migration_head`、`db/schema.py: EXPECTED_SCHEMA_REVISION`、本文件与 [../../README.md](../../README.md)
  已同步；离线全链与真实升级结果见"验证记录"。

## 2026-10-19 CI Linux runner 验收镜像路径修正

GitHub Actions 的最新发布验证在 B2 真实 nsjail 用例中 5 项失败、1309 项通过；合法程序、禁网/超时样本和
红队日志样本均没有得到预期输出。用同一 Dockerfile 在本机重建 CI 验收镜像后，根因已确认：B2 runner 的固定
解释器路径是 `/usr/bin/python3`，而 `python:3.12-slim-bookworm` 镜像仅提供 `/usr/local/bin/python3`；该镜像
没有 `/usr/bin/python3`，nsjail 因此无法 exec 学生程序，runner 把空输出照实判为不匹配。

修正是在 Linux 验收镜像内把 `/usr/bin/python3` 链接到基础镜像自带的 Python 3.12，并在构建时检查该路径可执行。
生产 runner 的固定接口与策略未改。验证读数：

```text
修复前当前镜像：/usr/bin/python3 missing；/usr/local/bin/python3 Python 3.12.15
修复后镜像：/usr/bin/python3 -> /usr/local/bin/python3；Python 3.12.14
test_i2_legal_submission_passes_inside_the_real_sandbox          PASS
test_i3_limit_and_hostile_samples_are_classified                PASS
test_i4_timeout_leaves_no_process_tree_residue                  PASS
test_i8_student_cannot_forge_a_sandbox_limit_attribution        PASS
4 passed in 11.81s
```

**未执行**：I5 真实数据库编排用例与 B2 完整套件；由本次推送触发的 GitHub Actions 使用真实 PostgreSQL/NATS 执行，
未通过时部署 job 不会运行。最终结果以该工作流记录为准。
