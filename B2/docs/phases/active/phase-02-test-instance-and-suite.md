# Phase 02 测试实例与套件实跑（当前阶段）

## 目标与范围

Phase 01 的验证记录里，`uv run pytest` 的读数是"1134 passed"，但那是在**临时手工创建的容器**
（`autograder-b2-postgres` / `autograder-b2-nats`）上跑出来的，而那些容器既不在任何 compose
文件里、也没有启动命令被记录。本轮把测试实例写进文件、把套件真实跑一遍。

范围外：`src/**` 的业务逻辑、契约（本轮未改 `MAIN/contracts/**` 与 `contracts.lock`）。

## 完成条件

1. 测试实例由仓库里的文件定义，不再依赖"某次 `docker run` 的记忆"。
2. 套件在隔离实例上真实跑完，读数写进本文件。
3. 因契约演进而过期的断言被更新，而不是被绕过。

## 实际变更

| 文件 | 变更 |
|---|---|
| `compose.yaml`（新增） | 模块本地 PostgreSQL `55433` + NATS `54223`（监控 `58222`）；命名与端口沿用 `docs/testing/strategy.md`、`docs/phases/active/reviews/*` 里已经用了多轮的 `autograder-b2-postgres` / `autograder-b2-nats` 参数 |
| `infra/postgres/init.sql`（新增） | 只做 `REVOKE CREATE ON SCHEMA public FROM PUBLIC`。此处**不**创建 `autograder_b2`：套件的临时库在同一个会话里建好、迁移、DROP，没有长期服务连它们；最小权限角色由平台 bootstrap 建立 |
| `docs/runbooks/local-development.md` | 第 4 节拆成"集成目标 55432/54222"与"测试实例 55433/54223"，并写明测试命令前置的两个环境变量 |
| `tests/test_submission_query.py` | `test_view_shape_and_no_file_content_leaks` 的键集合补上 `evaluation_run_id` |

### 为什么 `evaluation_run_id` 是契约要求（不是放宽断言）

`SubmissionView.evaluation_run_id` 是**可空**字段，本轮由 MAIN 加入契约（见
`MAIN/contracts/openapi/b2.yaml`）：运行行在受理**之后**才创建，而冻结的提交流程状态机里
`failed` 只能从 `running` 到达，因此"受理后、运行前"的基础设施故障会让
`submissions.state` 停在 `validated`，终态落在**运行**上。客户端如果只按 `state` 判断，
就没有任何东西可以订阅 SSE 进度。B1 因此改为轮询 `GET /api/v1/submissions/{id}` 直到该字段出现
（`B1/src/features/submission/useSubmission.ts::useEvaluationRunId`）。
键集合断言必须跟着契约走——这是"契约先行"的正常代价，不是为了让测试变绿而放宽。

## 验证（真实执行）

```powershell
docker compose -p b2 up -d
$env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"
$env:B2_TEST_NATS_URL     = "nats://127.0.0.1:54223"
uv run pytest -q
```

```text
1 failed, 1133 passed, 9 skipped, 1 xfailed, 2 warnings in 309.09s (0:05:09)
FAILED tests/test_submission_query.py::test_view_shape_and_no_file_content_leaks
```

修正该断言后**全量复跑**：

```text
uv run pytest -q
1134 passed, 9 skipped, 1 xfailed, 2 warnings in 298.90s (0:04:58)   exit 0
```

## 未完成 / 阻塞

- 无 `阻塞` 项。
- 动态评测（nsjail）仍**未在 Linux 容器里跑**：Windows 下沙箱阶段如实返回
  `infrastructure_error`（ADR-0004 / SEC-20 不允许降级为裸进程）。因此"真实 `completed` + 分数"
  这条路径在本环境仍不可得，集成环境里成绩行的 `state` 是 `failed`、`score` 为 `null`——
  这是**如实上报**，不是缺陷。
- 9 个 skip 与 1 个 xfail 与本轮改动无关（依赖缺失时明确 skip 并给出原因，不伪报通过）。

---

# 第二批：三处缺陷修复 + 一处诚实更正（2026-09-18）

范围：`RQ-B-2-70` 验收 2/5、事件 outbox 的**投递链路**（`RQ-B-2-10` 验收 5 / `RQ-B-3-30` 的上游）、
`RQ-B-2-30` 验收 5（保留策略）。`src/**`、`tests/**`、`docs/**` 均属本模块；**未改** `MAIN/contracts/**`、
`contracts.lock`、`module.yaml`、`alembic/**`、`pyproject.toml`。

## 1. 运行期安全违规不再产生分数（E9）

**根因**：`classify_case_outcome` 对 seccomp 拒绝与沙箱自有日志违规返回用例级
`failure_category="security_blocked"`，而编排层的 `_assert_student_outcome` **只**升格
`infrastructure_error` ⇒ 违规用例被当成"学生这条没做对"，评测继续走到 `completed` 并**给出分数**，
运行期违规也没有任何落库落点。这与 `RQ-B-2-70` 验收 2 原文、`architecture/interfaces.md` §4.4/§5.4、
`architecture/models.md` §1 三方矛盾，而 `traceability.md` 却记着 `已完成`。

**改动**：`evaluation/orchestrator.py`（`_assert_student_outcome` → `_escalate_case_outcome`：违规 ⇒
落运行期发现 + `_Blocked(security_blocked)` ⇒ `running → rejected`；`_Blocked`/`_finish_rejected` 如实
携带已发生的调用与证据计数）、`evaluation/store.py`（新增 `record_runtime_security_findings`：按
`(run, 用例序号, finding_id)` 派生主键写 `b2.security_findings`，`detected_in="runtime"`，
`blocking = severity == "high"` —— G-L1-7 的落库落点）、`evaluation/scoring.py`（第二道闸：带
`security_blocked` 的证据**不得评分**，升格被删掉时当场失败而不是打出分数）。

**证据**（本机可执行，真实 PostgreSQL）：
`tests/test_evaluation_pipeline.py::test_runtime_sandbox_violation_rejects_the_run_instead_of_scoring_it`
—— `(state, terminal_reason) == ("rejected", "security_blocked")`、迁移史 `queued → running → rejected`、
提交状态 `rejected`、`score/is_pass/report/result_digest` 全为 `None`、无 `evaluation_results` 行、
`b2.security_findings` 一条 `runtime/high/blocking/runner.seccomp`、第 3 个用例未执行
（`len(runner.calls) == 2`）、outbox 无 `completed` 且无 `failed`；并有
`::test_scoring_refuses_a_security_blocked_case_instead_of_awarding_zero`。

**反证（用例有牙）**：把升格逻辑临时关掉后该用例**失败**（实测得到
`('failed', 'internal_error') != ('rejected', 'security_blocked')`）——即"删掉升格"不可能静默变成一份
有分数的报告（第二道闸把它变成显式内部缺陷）。验证后已还原。

**未验收（如实记）**：真实 nsjail 里"写只读路径/提权"被捕获为运行期事件仍无断言（仅 Linux 容器；
目前真跑到的运行期违规只有禁网 → `runner.seccomp`）。

## 2. outbox 从未发布过任何事件（死集成路径）

**根因**：`worker.run_once` 把派发器的 `async dispatch` 当同步方法调用——协程被创建后丢弃
（`RuntimeWarning: coroutine 'NatsOutboxDispatcher.dispatch' was never awaited`），既没有投递、也没有
失败记录；而 worker 是该模块**唯一**的派发行（宿主不调用它、`B2Runtime` 不埋定时器）。

**改动**：`worker.py`（新增 `drain_outbox`：空 outbox 不连 broker，否则走派发器的同步入口；
`run_once` 改用它）、`events/dispatcher.py`（`dispatch_sync` 在**同一个**自建循环内"投递 → 关连接"：
连接绑在事件循环上，留到下一轮就是"第 2 轮起永远发不出去"；两次 `asyncio.run` 关连接会撞
`RuntimeError: Event loop is closed`，实测踩到过）。

**证据**：`tests/test_worker.py`（真实 PostgreSQL + 真实 JetStream）——
`::test_run_once_publishes_pending_events_and_marks_them_published`（`published_at` 非空**且** broker 上
能读回同一 `event_id`）、`::test_accepted_submission_is_evaluated_and_published_end_to_end`
（真实 HTTP 接收 → outbox → worker 一轮 → 读回；并证明 worker 的输入路径能从不含夹具的库行重建信封）、
`::test_empty_outbox_does_not_touch_the_broker`、`::test_publish_failure_keeps_the_row_and_leaves_business_rows_untouched`；
`tests/test_events_outbox.py::test_dispatch_sync_can_be_called_repeatedly_on_the_same_dispatcher`
（同一 dispatcher 连投两轮）。**反证**：把投递改回"创建协程后丢弃"后两条 worker 用例**失败**并复现
原文的 `RuntimeWarning`；验证后已还原。

**平台实跑（真实执行，2026-09-18）**：按 `.env.integration` + ADR-0008 的模块视图
（`B2_DATABASE_URL → DATABASE_URL`、`B2_NATS_URL → NATS_URL`）执行
`uv run python -m autograder_b2.worker --once`：

```text
platform b2.outbox_events: 15 行 / published_at 非空 0 行 / DLQ 0 行   （运行前）
运行后：每行 attempts=1，last_error =
  "EventBusUnavailable: publish failed for autograder.v1.submission.accepted on nats://<redacted-host>
   NoStreamResponseError: nats: no response from stream"
```

即"投递真的发生了"，但**平台 broker 上没有承载领域事件的流**：只读查 JetStream 监控
（`docker exec main-nats-1 wget -qO- http://127.0.0.1:8222/jsz?streams=true`）得到两个流
`ROSTER_IMPORT` 与 `B4_DLQ`，均不匹配 `autograder.v1.>`。**建流是 B4 的职责**（`RQ-B-4-20`；
`events/nats.py` 明写"本模块不建流、不改流、不删流"），因此本模块**没有**在平台上造流，也没有把行
标成已发布。⇒ `published_at` 在平台上仍为 0 行，**原因已从"没人发"变成"没有流"**，后者需 B4/MAIN 处置
（`B4/src/autograder_b4/messaging/jetstream.py` 的 `STREAMS` 里没有第三个流，配额账本 0.25 GB 需一并
重算）。**注意**：平台 broker 未就绪期间不要反复跑 worker——每轮 `attempts += 1`，5 轮即进 DLQ
（可由 `NatsOutboxDispatcher.replay` 人工重放）。

## 3. 保留策略可配置（`RQ-B-2-30` 验收 5）

**根因**：验收 5"保留策略可配置"只活在散文里——`storage/gc.py` 只按引用关系判定，全仓没有任何
retention 配置项。

**改动**：`config.py`（`B2_ARTIFACT_RETENTION_DAYS`，默认 `0` = 一失去引用即可回收；
`Settings.artifact_retention_days`；单独访问器 `artifact_retention_days()`，解析规则只定义一次）、
`storage/gc.py`（窗口作为**附加的推迟条件**进同一条原子 `DELETE`；相一看 `artifacts.created_at`、
相二看内容文件 mtime；窗口内的内容连候选集都不进、不删也不写审计）。

**两个刻意的取舍（如实记）**：①**默认 `0` 与历史行为逐字节相同**——提高窗口只会更保守，
不可能让任何此前被保留的内容开始被删除；②**不写迁移**：窗口内的内容不进候选集，因此不为审计枚举
（`ck_artifact_gc_audits_outcome`）与 `artifact_gc_runs` 计数器新增取值（`module.yaml: migration_head`
是与 MAIN 对账的共享状态）。**实测踩到的边界**：文件 mtime 是微秒精度而 `utc_now()` 截断到毫秒，
直接比较会让"刚写入的散落文件"比本轮开始时刻还晚（6 次里 5 次）⇒ 默认窗口会被悄悄变成"永不回收
散落文件"；相二因此先按同一条毫秒网格截断再比较。

**证据**：`tests/test_storage_gc.py`：`::test_configured_retention_window_defers_reclamation`
（环境变量生效：不删、不写审计、被引用内容仍可读）、`::test_content_older_than_the_window_is_reclaimed`
（过窗口照常回收 ⇒ 忽略保留配置即失败）、`::test_retention_window_also_defers_stray_file_removal`
（相二按 mtime）、`::test_collection_is_reference_based_not_time_based`（默认窗口 0）。

## 4. `RQ-B-2-70` 验收 5"证据来自真实执行"的诚实更正

**结论**：**不是实现错，是措辞夸大**，且夸大的那一份在**本模块自己的文档**里。
MAIN 目录原文只要求"存在恶意样本回归用例集，且全部被阻断"（未提执行）——因此**不需要改 MAIN 的
需求目录**。B2 侧 `requirements/acceptance.md` 的 2-70-E 与 `testing/cases.md` 的 T-SEC-005 曾写
"证据来自真实执行"，而 23 条静态语料是 regex + `ast` **真的跑在样本上**判定、并按设计**不执行**
（`runner calls == 0` 正是 `RQ-B-2-40` 验收 4 与 ADR-0001 要验证的行为）。已按"两段样本、两段含义"
改写：静态段＝真实运行的分析（不执行样本），运行期段＝真实 nsjail 内执行（**仅 Linux 容器**）。
实现未改——**不发明**设计上刻意的静态分析。

## 与外部并发变更的交叉（不是本批引入，但影响读数）

验证期间 MAIN 的契约 relock 正在并发推进（`MAIN/contracts/**`、四份 `contracts.lock`、
`B2/module.yaml`、`src/autograder_b2/plugin.py` 的 `contract_version`：`0.1.0 → 0.4.0 → 0.5.0`，
时间戳 23:10–23:20）。它把本模块**两个把版本号写死的断言**变成假失败，已按"钉不变量而不是钉某一刻"
改正（两处现在都与 `module.yaml` 比对：`tests/test_contract_conformance.py::test_contracts_lock_matches_live_digest`、
`tests/test_plugin.py::test_descriptor_is_contract_bound`）。本模块**没有**修改被 relock 的文件。

## 验证（真实执行，本批最终读数）

```powershell
uv run ruff check .                     # All checks passed!
uv run pytest -q                        # 见下（2026-09-18，隔离实例 55433/54223）
```

```text
1145 passed, 9 skipped, 1 xfailed, 2 warnings in 325.64s (0:05:25)   exit 0
```

- 9 个 skip：`tests/linux/**` 的真实 nsjail 验收（Windows 上无 nsjail，明确 skip 并给出原因）；
  1 个 xfail：`5a2` cgroup v2 `memory.max`（本内核记账但不杀）。
- **本机可执行**：编排层运行期违规升格、outbox 投递（真实 broker）、保留策略、契约/插件一致性。
- **仅 Linux 容器可执行**（本批未复跑，如实记）：真实 nsjail 内的执行与归类、"写只读路径/提权"的运行期
  捕获、资源限制梯队的实测。

