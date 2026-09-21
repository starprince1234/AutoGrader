# 定点复裁（第 2 轮）：必修复核 + 新问题巡检

- 复核者：独立组级复核者（冷视角、对抗式）。**未修改任何被测文件**；探针仍在 `.pytest-tmp/review_probe/`。
- 被复核的修复：迁移 `20260915_0009`（outbox `insert_seq` + 终态配对 CHECK）、`dispatcher`/`progress`/`store`
  的排序与配对语义、`frame_stream` 终态短路、以及 4 条低项的处置（登记/文案/用例目录）。
- 快照：本次复裁运行窗口 09:4x–10:1x（UTC），全部晚于实现者的最后写入（文件已静止）。
- 快照与时间线：实现者最后写入 = 代码 **17:41:14**（`progress.py`）、文档 **17:47:09**（`phase-01`）；
  我的复裁探针运行窗口 **17:44–17:56**，全部晚于代码冻结时刻，`probe_cases` 亦晚于文档写入
  ⇒ 全部证据针对同一冻结快照。复核期间未对被测文件做任何写入（本会话写操作只在 `.pytest-tmp/`）。
- oracle：我自己脚本打印的 `PASS/FAIL` 与 `FAILURES = [...]`，或实测数值。

---

## 0. 基线复算（自己跑）

```powershell
uv run ruff check .                 # → All checks passed!（exit 0）
uv run pytest -q -p no:warnings     # → 1127 passed, 3 skipped, 1 xfailed in 176.21s
```

与实现者自报的 `1127 passed, 3 skipped, 1 xfailed`（179.89s）**一致**（+3 条新用例；
此前基线 1124）。另单独跑四条新/改写用例 + 迁移用例：
`22 passed, 1 xfailed in 30.96s`。

---

## 1. 必修 1（事件发布顺序）→ **真正解除**

命令（约 4 min，含 30 轮真实流水线 + 200 轮同刻插入对 + 三段落库/回滚实验）：

```powershell
uv run python .pytest-tmp/review_probe/probe_repair_verify.py   # FAILURES = []（见下"新发现"）
```

### 1.1 真实流水线 30 轮（生产 orchestrator + 生产 dispatcher，假 bus）

```
A1 30 轮真实流水线：started-first 且终态在末 = 30/30
A1 最后一轮的实际投递顺序 = ['started','progress','progress','progress','progress','progress',
                            'progress','progress','completed']
[PASS] A1 30/30 轮投递顺序都是 started 首、completed 尾
```

### 1.2 原缺陷构造**直接复现**（三个信号两两对立）

构造：同一事务按 `started`→`progress` 插入；`enqueued_at` 反向（started 更晚 10s）；
运行标识挑选成"event_id 哈希指向 progress 在前"。

```
A2   subject=started   insert_seq=271 enqueued_at=12:00:10.500000 event_id=45981567-…(较大)
A2   subject=progress  insert_seq=272 enqueued_at=12:00:00.500000 event_id=120d8dfc-…(较小)
A2 **实际投递顺序** = ['started', 'progress']
[PASS] A2 插入序（started 先）胜出：event_id 哈希与 enqueued_at 都指向相反方向也无效
[PASS] A4 同一事务内 insert_seq 严格递增且互不相同 :: [271, 272]
```

旧实现下这一构造必然得到 `['progress','started']`（两个信号都指向 progress 在前）。

### 1.3 翻转率：旧规则仍 ~50%，新规则 **0**

```
A3 旧规则（同刻 ⇒ event_id）翻转率 = 10023/20000 = 50.1%
A3 新规则（insert_seq）在 200 轮同刻插入对上的翻转数 = 0
[PASS] A3a 旧规则的翻转率仍约 50%（证实缺陷根因描述）
[PASS] A3b 新规则 0 翻转（200/200 轮都按插入序投递）
```

200 轮是独立构造的真实派发（每轮 `DELETE` 后重新同刻入队一对并派发），不是单次通过。

### 1.4 新回归用例是否真能抓住旧缺陷（读断言，非空转）

- `tests/test_events_outbox.py:403` `test_dispatch_preserves_insertion_order`：按逻辑顺序入队
  `started/progress/completed`，`enqueued_at` **刻意与插入顺序相反**（+2s/+1s/+0s）。旧实现按
  `(enqueued_at, event_id)` 排序 ⇒ 期望的 `[started,progress,completed]` 必然不成立 ⇒ 用例会红。
- `:436` `test_same_transaction_enqueues_keep_their_order_despite_identical_timestamps`：20 轮同刻入队，
  每轮断言 `pending_rows() == [started, progress]` 且 `insert_seq` 严格递增。旧实现在同一构造上的翻转率
  实测 50.1% ⇒ 20 轮全对的概率 ≈ 2⁻²⁰。两条用例合起来既覆盖"同刻"也覆盖"时间戳反向"。

> 一处措辞不精确（不影响判据）：该 20 轮用例的每次 `enqueue_event` 各自开一个事务
> （`tests/test_events_outbox.py:272`），因此"同一事务"字样与其实际形态不符；但它要验的
> "同一时间戳、顺序仍确定"这一性质是成立的，且足以抓住旧缺陷。

**必修 1 判定：解除**（三条独立证据：30/30 真实流水线、原构造复现、200 轮 0 翻转）。

---

## 2. 必修 2（`state ↔ terminal_reason` 配对）→ **真正解除，三层都拦得住**

```
B1 store 层 rejected+infrastructure_error: raised=TerminalReasonMismatch db_row=[('running','<null>')]
[PASS] B1 store 层拒绝跨域配对且未写入（DB 仍 running / terminal_reason 为空）
B2 帧层 rejected+infrastructure_error: raised=FrameRejected
[PASS] B2 帧层拒绝跨域配对
B3 原始写入「UPDATE evaluation_runs SET state='rejected', terminal_reason='infrastructure_error'」: IntegrityError
B3 原始写入「INSERT evaluation_runs (failed + static_blocked)」: IntegrityError
B3 原始写入「INSERT progress_events (failed event + static_blocked)」: IntegrityError
B3 原始写入「INSERT progress_events (rejected event + internal_error)」: IntegrityError
[PASS] B4 合法配对「rejected+static_blocked」/「rejected+security_blocked」/「failed+internal_error」可写
[PASS] B5 跨域配对「failed+static_blocked」/「rejected+internal_error」被拒（TerminalReasonMismatch）
[PASS] C4 新约束不拒绝合法完成路径（completed 落库成功且有分数）
```

三层：`store._require_terminal_reason`（`store.py:107-134`，用 `TERMINAL_REASONS_BY_STATE`）、
`progress._validate_frame`（`progress.py:203-209`）、迁移 0009 的两条 CHECK
（`pg_get_constraintdef` 实测文本已含 `ARRAY['static_blocked','security_blocked']` /
`ARRAY['infrastructure_error','internal_error']`）。数据库层用**绕开代码的原始 SQL** 验，因此
"代码拒"与"库拒"是两条独立证据。

**"CHECK 是否过紧"——不过紧**：契约明文写了配对 ——
`../MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame.terminal_reason.description`：
> "'static_blocked' and 'security_blocked' accompany rejected; 'infrastructure_error' and
> 'internal_error' accompany failed."

即 CHECK 强制的是**契约要求**，不是超契约收紧；正例（4 种原因 × 对应状态 + completed + 非终态）全部可写。

---

## 3. 低项处置

### 低项 3（终态后重连空等）→ **合格，已修**

真实 HTTP 上重跑（同一套自写 asgi 服务器 + 原始套接字客户端）：

```
末帧后重连：耗时=0.03s 帧数=0 clean_close=True        # 修复前：30.44s
[PASS] D1 Last-Event-ID=末帧序号 ⇒ 0 帧（不重复）
从 sequence>7 重连得到 [8, 9, 10, 11, 12]              # 中途重连补发语义未受影响
[PASS] D2 中途重连不重复不遗漏
```

实现处 `progress.py:419-422`（先查 `terminal_frame`，`terminal.sequence <= after` 即 return）。
新用例 `test_frame_stream_returns_at_once_for_already_terminal_runs` 用会抛
`AssertionError` 的 `sleep` 接缝证明"没进等待循环"，并含 `after=last-1` 必须补发的反例——
断言强度足够（读过代码）。

### 低项 4（`run_case` 真实 Linux 路径无验收证据）→ **处置可接受**

`docs/phases/active/phase-01-implementation.md:218-222` 新增 **G-L1-2**：写明
`tests/linux` 只读校验配置、`tests/test_runner_core.py` 在 Windows 上跑、因此"真实 Linux 沙箱内跑
学生代码"无端到端证据，并给出**可执行的恢复验证**（"在容器内用真实 `PosixNsjailLauncher` 跑一次
`run_case`（含超时进程树清理）并纳入 `tests/linux` 矩阵"）。
`src/autograder_b2/runner/policy.py:17-21` 的陈旧 docstring 已改写为"Linux 实测校准状态"：
说明 seccomp/cgroup 语义**已**由 Linux 验收实测校准，同时如实留下仍缺的那条路径。
判定：**可接受**（如实登记 + 可复现的恢复动作）。注意它是**未消除的覆盖缺口**，不因"标定完成"消失，
属里程碑层面接受项。

### 低项 5（phase-01「矩阵 13/13」）→ **合格**

`phase-01:74` 现为「矩阵 **12 通过 + 1「无法执行」**（`5a2` …记 xfail）；组级复核者在容器内重跑得
`12 passed, 1 skipped, 1 xfailed`」——与我实测口径一致。

### 低项 6（文档）→ **基本合格**

```
cases.md 中标『存在』的表格行数 = 69 ；可机器核对的引用数 = 47（不一致 0）   # 此前 65 / 34
未写出测试名、无法机器核对的行 = 32                                       # 此前 35
[PASS] J1 cases.md 中标『存在』且写了测试名的引用全部真实存在
```

新增/补齐：`T-RUN-007`（SSE 终态与重连，含"终态之后的重连立即结束"）、`T-EVT-002`、`T-EVT-002b`
（顺序新语义）、`T-PERSIST-002/003/004`、`T-RUN-005`。
**残留（低）**：仍有 32 行标「存在」但未写测试名（`T-INT-001..011` 等）；我此前已按内容手工核对过其中
5 条（映射正确），但机器仍无法核对。

---

## 4. 修复是否引入新问题（问题 3）

### 4.1 迁移可回滚性 → **合格**（含一处"真的回退了"的证明）

```
C1 head(0009)  insert_seq 在列清单 = True；索引含 ix_outbox_events_insert_seq
C1 head(0009)  runs CHECK  = …ARRAY['static_blocked','security_blocked']…) OR (state='failed'…)
C1 downgrade(0008) insert_seq 在列清单 = False
C1 downgrade(0008) 索引 = [ix_outbox_events_correlation_id, ix_outbox_events_pending,
                           outbox_events_pkey, uq_outbox_events_subject_deduplication_key]
C1 downgrade(0008) runs CHECK  = …(state <> 'completed' AND terminal_reason IS NOT NULL)…)   # 0007 文本
[PASS] C1a/C1b/C1c/C1d
[PASS] C1e downgrade 后旧宽松语义恢复（rejected+infrastructure_error 在 0008 可写、在 0009 被拒）
[PASS] C1f 再次 upgrade head 成功且列回归
```

> **探针自身的踩坑记录（如实写出）**：第一版探针用 `alembic upgrade 20260915_0008` 模拟回滚，
> 但 `upgrade <更早 revision>` 在已到 head 的库上是 **no-op**，于是误报"C1c/C1d/C1e 失败"。
> 修正为 `alembic downgrade 20260915_0008` 后全部 PASS。**错误在探针，不在实现。**

### 4.2 存量行升级 → **合格**

```
C2 升级前 3 行（含同刻两行）
C2   legacy-0 insert_seq=1 / legacy-1 insert_seq=2 / legacy-2 insert_seq=3
[PASS] C2a 存量行全部补齐 insert_seq 且互不相同、严格递增
[PASS] C2b 存量行的相对顺序 = 物理插入顺序（与 enqueued_at 无关，符合新语义）
```

边界（如实记录，可接受）：升级前**存量行之间的**相对顺序由物理顺序决定，不再等于 `enqueued_at`
顺序；对"尚未投递的历史行"是一次语义变化，但 outbox 是瞬态队列且新语义更贴近真实入队次序。

### 4.3 **升级遇到"配对不合法"历史行** → 失败但完全原子（低，新发现）

0007 语义下跨域配对行**是可写的**（这正是必修 2 的由来），因此存在一条真实升级路径。实测：

```powershell
uv run python .pytest-tmp/review_probe/probe_migration_risk.py   # FAILURES = []
```

```
N0 前提：0008 上已写入跨域配对行 1 条；版本 = 20260915_0008
N1 `alembic upgrade head` 返回码 = 1
   IntegrityError: (psycopg.errors.CheckViolation) check constraint
   "ck_evaluation_runs_terminal_consistency" of relation "evaluation_runs" is violated by some row
N2 失败后版本号 = 20260915_0008（= 升级前）；insert_seq 在列清单 = False；
   索引回到 4 条（无 ix_outbox_events_insert_seq）；两条 CHECK 仍为 0007 语义
[PASS] N1  升级如实失败（不静默跳过、不放宽约束）
[PASS] N1b 错误文本点出「存在违反约束的行」而非只有 exit code
[PASS] N2  升级失败后 schema **完全回滚**（无半应用）
[PASS] N3  修复存量行后可正常升级到 0009（rc=0，version=20260915_0009）
```

结论：**不是缺陷**（fail-closed + 原子 + 可恢复 + 诊断可操作），但属于**可操作性缺口**：
迁移 docstring 只写了"回滚不会失败"，没写"升级遇到不合法历史行会失败"，也没有升级前置检查 SQL。
建议：把实现者手工跑过的那条前置查询（`runs_bad` / `frames_bad` 计数）写进迁移 docstring 或 runbook；
若希望零停机，可考虑 `NOT VALID` + 显式 `VALIDATE CONSTRAINT` 两步（当前选择"直接失败"也符合本模块
一贯的 fail-closed 口径）。

### 4.4 `insert_seq` 与既有写入路径 / 其它查询 / 测试的兼容 → **合格**

- `events/outbox.py:enqueue()` 用 `pg_insert(...).values(...)` **不显式给** `insert_seq` ⇒ identity 默认值
  生效（30 轮真实流水线 + 全部探针 + 全量 1127 用例均通过）。
- `pending_rows()`（`ORDER BY insert_seq`）、`dead_letters()`（`ORDER BY (dead_lettered_at, insert_seq)`）
  实测可用；`dead_letters()` 返回 0 行不报错（新列不破坏投影）。
- `tests/test_migrations.py:243` 只是主键列映射（`"outbox_events": ("event_id","uuid")`），**不含全列清单**
  ⇒ 新列不影响它；`EXPECTED_SCHEMA_REVISION` 已升到 `20260915_0009`（`db/schema.py:19`）。
- 测试夹具 `TRUNCATE ... RESTART IDENTITY`（`tests/conftest.py:121`）对 identity 列语义正确（全量通过）。
- 全量回归 `1127 passed, 3 skipped, 1 xfailed`；我另重跑 `probe_replay` / `probe_sse` / `probe_export` /
  `probe_sandbox` / `probe_cases` 五个旧探针，**全部 `FAILURES = []`**（无回归）。

### 4.5 CHECK 是否过紧导致合法写入被拒 → **不过紧**（见 §2 末）。

### 4.6 **新索引在两种实测形态下都未被使用**（低·设计观察，非缺陷）

```powershell
uv run python .pytest-tmp/review_probe/probe_index_plan.py
```

```
形态 A（20000 行，仅 50 行待投递）：Index Scan using ix_outbox_events_pending … +
                                    Sort Key: insert_seq（quicksort 50kB）；0.081 ms
形态 B（20000 行全部待投递）：       Index Scan using ix_outbox_events_pending … +
                                    Sort Key: insert_seq（top-N heapsort 126kB）；4.212 ms
形态 A/B：含 Sort = True；用 ix_outbox_events_insert_seq = False；用 ix_outbox_events_pending = True
索引定义：ix_outbox_events_pending = btree(enqueued_at) WHERE published_at IS NULL AND dead_lettered_at IS NULL
          ix_outbox_events_insert_seq = btree(insert_seq)         # 无 WHERE
```

即：planner 仍用 0004 的 **partial** 索引做过滤、再对命中集按 `insert_seq` 排序；0009 新加的
**普通**（非 partial）索引在两种形态下都未被采用。成本实测可忽略（积压 20000 行时 4.2 ms），
因此**不是缺陷**；建议（收益低、可选）：把该索引改为 partial
`(insert_seq) WHERE published_at IS NULL AND dead_lettered_at IS NULL`，使过滤与排序由同一个索引服务，
并顺带更新 `0004` 里"派发器的工作队列：…按入队顺序取"的索引注释（现已半陈旧）。

### 4.7 三处极小的事（不阻塞，供顺手清理）

1. 迁移 `20260915_0009…py:121` `sa.text("")` 是**死语句**（无副作用、ruff 不报）。
2. `progress.py:60` `TERMINAL_REASONS_BY_STATE` 的字典首行折行异常
   （`{    EVENT_REJECTED: (...)` 与赋值同行；ruff 未启用该格式检查，故不报）。
3. `progress.py:76-77` `SCHEMA_CONFLICT_CONSTRAINTS` 的两条描述仍只写"（迁移 0007 已对齐）"，
   未提 0009 的配对语义（该文本会出现在 `EvaluationOutcome.message` 里）。

---

## 5. 复裁结论

| 项 | 判定 | 关键证据 |
|---|---|---|
| 必修 1 事件发布顺序 | **解除** | 30/30 真实流水线顺序正确；原构造（enqueued_at 与哈希双反向）投递 `['started','progress']`；旧规则 50.1% vs 新规则 200 轮 **0** 翻转 |
| 必修 2 终态配对 | **解除** | store/帧/CHECK 三层各自拦截（含绕开代码的原始 SQL）；正例可写；契约 `b2.yaml` 明文要求配对 ⇒ 不过紧 |
| 低 3 终态后重连 | 合格（已修） | 30.44 s → **0.03 s**、0 帧、干净关闭；中途重连 `[8..12]` 不变 |
| 低 4 Linux 执行路径缺口 | 可接受（登记 G-L1-2 + 修正陈旧 docstring） | phase-01:218-222 含恢复验证动作；policy.py:17-21 已改写 |
| 低 5 矩阵口径 | 合格 | phase-01:74 = 12 通过 + 1 无法执行 |
| 低 6 用例目录 | 基本合格 | 可核对引用 34→47（0 不一致）；未写测试名 35→32（仍残留，低） |
| 新引入问题 | **无功能性问题** | 回滚合格、存量行升级合格、写入路径兼容、CHECK 不过紧；仅 3 处极小文案/死语句 + 1 条索引设计观察 + 1 条迁移可操作性缺口（均已给出可选改法） |
