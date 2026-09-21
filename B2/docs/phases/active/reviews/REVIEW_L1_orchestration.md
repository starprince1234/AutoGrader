# B2「评测编排与状态迁移」三条验收标准 · 独立复核记录

复核者：独立复核 agent（冷视角、对抗性）。判据为三条**原文**，逐条单独判定。
环境：Windows + PowerShell，Python 3.12.3，真实 PostgreSQL 17.11（容器 `autograder-b2-postgres`，端口 55433），
`$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"`。
仓库：`D:\VScodeProjects\AutoGrader\B2`（git 无提交，工作树全为未跟踪）。
探针（我自己写的，未改动 `src/**` 与既有测试）：`tests/test_orch_probe_review.py`；
完整原始输出：`.pytest-tmp/b2-orch-review/probe-output.txt`。

基线（证明既有用例真的在跑、没有被 skip 掩盖）：

```powershell
uv run pytest tests/test_evaluation_pipeline.py -q --no-header -p no:cacheprovider
# 40 passed in 11.64s        （无 s / 无 skip）
uv run pytest tests/test_b2_routes.py -q --no-header -p no:cacheprovider -k "event_stream or rejected_run_stream"
# 5 passed, 53 deselected, 2 warnings in 4.33s   （SSE 路由级用例，无 skip）
uv run pytest tests/test_orch_probe_review.py -q -s --no-header -p no:cacheprovider
# 3 passed in 5.09s
```

---

## 标准 1 —— 判定 **FAIL**

> 原文：状态机测试：非法迁移被拒；静态阻断路径为 validated→rejected 且迁移史无 running 记录。

### 子句 A「非法迁移被拒」：成立

命令与真实输出（探针 `标准1(a)`）：

```
标准1 RUN_TRANSITIONS = {'queued': ('running', 'rejected', 'failed'), 'running': ('completed', 'rejected', 'failed'), 'completed': (), 'rejected': (), 'failed': ()}
标准1 运行状态机里是否存在 'validated' 状态 = False
标准1(a) 起点: 行数=2 行={'state': 'running', ...} 史(raw)=[(None, 'queued'), ('queued', 'running')] 史(reader)=[(None, 'queued'), ('queued', 'running')]
标准1(a) running 上的非法尝试 = ["running->queued:IllegalRunTransition(运行迁移 'running' -> 'queued' 不在锁定的状态机内)", "running->running:IllegalRunTransition(...)", 'running->rejected:TerminalReasonMismatch(rejected 必须携带 terminal_reason（取值受契约枚举限制）)']
标准1(a) 行数 before=2 after=2 行={'state': 'running', ...}
标准1(a) completed->running:IllegalRunTransition(运行迁移 'completed' -> 'running' 不在锁定的状态机内)
标准1(a) 终态回退 行数 before=3 after=3 行={'state': 'completed', ...}
标准1(a) 纯校验器 = ['completed->running:IllegalRunTransition', 'rejected->queued:IllegalRunTransition', 'failed->running:IllegalRunTransition']
```

- **写入前拒绝已实测**：非法迁移后 `b2.evaluation_run_state_transitions` 行数 before=2/after=2、before=3/after=3，
  运行行快照逐字段不变。
- 读取函数与原始 SQL 一致（`run_history` == raw SQL）。
- 一处**精度提示**（不构成子句失败）：`running -> rejected`（缺 `terminal_reason`）抛的是
  `TerminalReasonMismatch` 而非 `IllegalRunTransition`——`store.transition_run` 的校验顺序是
  「终态原因 → 行锁 → 状态机」（`src/autograder_b2/evaluation/store.py:386-393`）。
  即"非法迁移**一律**抛 `IllegalRunTransition`"并不严格成立（但都不落库）。

### 子句 B「迁移史无 running 记录」：成立（运行级）

```
标准1(b) outcome = rejected static_blocked | runner 调用 = 0
标准1(b) 运行迁移史(raw) = [(None, 'queued'), ('queued', 'rejected')]
标准1(b) 帧 = [(1, 'progress'), (2, 'progress'), (3, 'progress'), (4, 'progress'), (5, 'rejected')]
标准1(b) 运行迁移史含 running = False
```

### 子句 C「静态阻断路径为 validated→rejected」：**不成立**

```
标准1(b) 实测静态阻断路径 = None->queued -> queued->rejected
标准1(b) 运行迁移史含 (validated,rejected) = False
标准1(b) 提交**静态阻断前** (state, 史) = ('validated', [(None, 'received'), ('received', 'validated')])
标准1(b) 提交**静态阻断后** (state, 史) = ('validated', [(None, 'received'), ('received', 'validated')])
标准1(b) 提交状态/史是否发生任何变化 = False
标准1(b) 裁决: validated->rejected 在**任何**迁移史中是否出现 = False
标准1(c) 成功路径 outcome = completed | 运行迁移史 = [(None, 'queued'), ('queued', 'running'), ('running', 'completed')]
标准1(c) 成功路径 提交 (state, 史) = ('validated', [(None, 'received'), ('received', 'validated')])
标准1 SUBMISSION_TRANSITIONS = {'received': ('validated', 'rejected'), 'validated': ('queued', 'rejected'), 'queued': ('running',), 'running': ('completed', 'rejected', 'failed'), ...}
标准1 提交状态机校验器 = ['validated->rejected:ALLOWED', 'validated->running:IllegalTransition', 'validated->queued:ALLOWED']
```

- 运行级状态机 `RUN_TRANSITIONS` **没有 `validated` 这个状态**（`'validated' in RUN_TRANSITIONS == False`），
  实际路径是 `queued -> rejected`（`create_run` 直接把运行建成 `queued`，`src/autograder_b2/evaluation/store.py:235`）。
- 提交级状态机（`src/autograder_b2/domain/states.py:28-36`）**允许** `validated -> rejected`，但**没有任何代码路径写它**：
  静态阻断前后提交行与提交迁移史**完全不变**（仍停在 `validated`），成功路径同样不变。
- 代码事实（我自行 grep 验证）：`evaluation/orchestrator.py` 全文不写 `b2.submissions.state`，也不调用
  `domain.transitions.record_transitions`；`record_transitions` 的唯一调用方是
  `src/autograder_b2/intake/service.py:303`，写的是 `(None,'received')` 与 `('received','validated')`。
  `sa.insert(submissions)` 只有 `intake/service.py:280` 一处，`sa.update(submissions)` 在 `src/**` 中**不存在**。
- 契约事实：`MAIN/docs/workstreams/B2.md` §8 把提交状态机取值集合列为**不可修改的公共契约**；
  字符串 `validated → rejected` 在仓库中只出现在 `src/autograder_b2/domain/states.py:7`（提交状态机）与
  `docs/architecture/models.md:21`；同时 `docs/architecture/models.md:31` 要求
  「`evaluation_run.status` 与 `submission.status` 在 `queued` 之后 **1:1 同步**」——该同步未实现。
- 实现者测试覆盖：`tests/test_evaluation_pipeline.py` 中 `validated` / `submission_state_transitions` **零匹配**
  （其 `test_illegal_run_transition_is_rejected_before_write` 只断言运行级 `after == before`，
  见该文件第 1582-1622 行）；路由级 `tests/test_submission_query.py:278-284` 反而把提交史钉死为
  `[(None,'received'),('received','validated')]`。

**结论**：三条子句中 A、B 成立，C 在两种读法下都不成立（运行级没有 `validated` 这个状态；提交级从未发生该迁移）。
按原文判 **FAIL**。若要判 PASS，需要（二选一）改判据措辞为"运行级 `queued→rejected`"，或实现
`validated→rejected` 的提交级写入——两者都不是复核者可以自行选择的。

---

## 标准 2 —— 判定 **PASS**

> 原文：SSE 测试：帧 sequence 严格单调、含 progress 与终态 rejected/failed，断线重连按 sequence 去重不重复。

命令与真实输出（探针 `标准2`）：

```
标准2[rejected 路径] 帧(sequence,event) = [(1, 'progress'), (2, 'progress'), (3, 'progress'), (4, 'progress'), (5, 'rejected')]
标准2[rejected 路径] 严格单调=True 连续=True 含 progress=True 终态帧=['rejected'] 个数=1
标准2[rejected 路径] read_frames 前缀+后缀 对 pivot=0..5 均等于全集（不重不漏）
标准2[rejected 路径] 断开点=[1, 2] 续读=[3, 4, 5] 拼接=[1, 2, 3, 4, 5] 全集=[1, 2, 3, 4, 5]
标准2[rejected 路径] 第二个终态帧尝试 = ['failed:TerminalFrameExists', 'rejected:TerminalFrameExists', 'completed:TerminalFrameExists']
标准2[rejected 路径] 终态帧个数 before=1 after=1
标准2[failed 路径] 帧(sequence,event) = [(1..6, 'progress'), (7, 'failed')]
标准2[failed 路径] 严格单调=True 连续=True 含 progress=True 终态帧=['failed'] 个数=1
标准2[failed 路径] 断开点=[1, 2] 续读=[3, 4, 5, 6, 7] 拼接=[1, 2, 3, 4, 5, 6, 7] 全集=[1, 2, 3, 4, 5, 6, 7]
标准2[failed 路径] 第二个终态帧尝试 = ['failed:TerminalFrameExists', 'rejected:TerminalFrameExists', 'completed:TerminalFrameExists']
标准2[failed 路径] 终态帧个数 before=1 after=1
```

- 两条路径（`rejected`+`static_blocked`、`failed`+`infrastructure_error`）都实测到位，帧序列严格单调（每次 +1，连续）。
- **真实断线重连语义**（不是"读两次比对"）：`frame_stream` 读到第 2 帧就 `break` + `close()`（生成器内同批
  已取出的其余帧被丢弃——这才是断线的真实形态），带最后一帧 `sequence` 续读，两边拼接**恰好等于全集且无重复**；
  另对 `read_frames(after=pivot)` 在 pivot=0..N 全部取值做了前缀+后缀==全集的穷举。
- **终态帧唯一**：对 rejected 运行与 failed 运行各尝试 3 种"第二个终态帧"（另类 failed / rejected / completed），
  全部抛 `TerminalFrameExists`，DB 终态帧计数 before=after=1。
- 路由层（真实 HTTP SSE）独立对照：`tests/test_b2_routes.py` 的 5 条 SSE 用例全绿（无 skip），
  含 `Last-Event-ID` 续读不重复、坏游标 422、拒绝终帧带原因且无分数（`src/autograder_b2/api/evaluation_routes.py:204-239`
  的 `encode_frame` 输出 `id: <sequence>`，是客户端去重的依据）。
- 证伪尝试：我特意检查了"生成器批量预读是否会在断线时丢帧"（`frame_stream` 一次 `read_frames(limit=500)`
  取一批再逐帧 yield，`src/autograder_b2/evaluation/progress.py:401-417`）——不丢，因为续读游标始终是
  **最后一帧已产出**的 sequence，重连时重新查库。这一路径被上面的 break-at-2 探针真实覆盖。

---

## 标准 3 —— 判定 **PASS**

> 原文：并发测试：并发提交时动态评测并发峰值不超过 1。

观测手段（**不看** `DynamicExecutionGate` 自己的计数器）：我自己写 `_ObservingRunner`，
在 `run_case` 进入/退出时用独立锁计数"同时在跑的 runner 调用数"并记录峰值。

```
标准3 DYNAMIC_CONCURRENCY_LIMIT = 1
标准3(A) 闸门容量=1: 外部观测峰值=1（闸门自报 peak=1, acquisitions=6）
标准3(A) 闸门容量=3: 外部观测峰值=3（闸门自报 peak=3, acquisitions=6）
标准3(B) 共享闸门(容量=1) 4 并发提交×2 用例=8 次 runner 调用: 外部观测峰值=1 闸门自报 peak=1 残留 live=0 墙钟=1.08s（串行下界 8×0.08=0.64s）
标准3(B') 同一批并发下**工作区物化**（闸门之外，prepare 注入 50ms 仪表）: 调用=12 外部观测峰值=4
标准3(C) 对照 容量=3 同样并发压力=8 次 runner 调用: 外部观测峰值=3 闸门自报 peak=3 墙钟=0.55s
标准3(D) 两个编排器各自默认闸门(gate=None, 每实例容量=1): 4 次 runner 调用: 外部观测峰值=2 墙钟=0.41s
```

- 真并发：4 个线程用 `Barrier` 同时发起提交，共享同一个 `DynamicExecutionGate`（容量 = 生产常量 1），
  每个提交 2 个用例，runner 每次 sleep 80ms → **8 次真实 runner 调用，外部观测峰值 = 1**，
  残留 `live = 0`，墙钟 1.08s ≥ 串行下界 0.64s（并发被串行化的独立佐证）。
- **正向对照（两处，证明"峰值=1"不是探针测不出）**：
  (A) 直接对闸门做 A/B：容量 1 → 外部峰值 1，容量 3（我自己构造 `DynamicExecutionGate(capacity=3)`，未改源码）→ 外部峰值 **3**；
  (C) 编排器级同压力、仅把容量换成 3 → 外部峰值 **3**。
- 已实测的两条**边界**（分类见不一致清单，不改变本条判定）：
  - **闸门只覆盖 `runner.run_case`**（`orchestrator.py:618-619`）；同一批并发下**工作区物化**在闸门外，
    外部观测峰值 = **4**（我在自己的 workspace provider 注入 50ms 仪表，12 次 prepare）。若把"动态评测并发"
    理解为整个 `dynamic_execution` 阶段（含物化），则峰值 > 1。
  - **闸门按编排器实例创建**（`orchestrator.py:249`）；两个编排器各自默认闸门时外部峰值 = **2**。
    生产装配只建一个 orchestrator（`src/autograder_b2/runtime.py:351`，`B2Runtime` 为单例），
    因此生产路径下"共享同一实例"成立；跨进程不成立（gate docstring 自述"当前部署为单进程 worker"）。

---

## 不一致清单

### 1. [标准不满足] 提交状态机在 `validated` 之后是死代码：静态阻断未写 `validated→rejected`
- 现象：静态阻断后 `b2.submissions.state` 仍为 `validated`，`b2.submission_state_transitions` 无 `rejected` 行；
  成功路径同样停在 `validated`（从不 `queued/running/completed`）。`docs/architecture/models.md:31`
  要求的 run↔submission 1:1 同步未实现。
- 最小复现：`uv run pytest tests/test_orch_probe_review.py -s -k probe1`（输出见上，`标准1(b)`/`标准1(c)`）。
- 文件行号：`src/autograder_b2/evaluation/orchestrator.py:896-935`（`_finish_rejected` 只写运行级状态）；
  `src/autograder_b2/intake/service.py:280-311`（`submissions.state` 唯一写入点 + `record_transitions` 唯一调用点）；
  `src/autograder_b2/domain/states.py:28-36`（允许 `validated→rejected`）；
  `src/autograder_b2/domain/transitions.py:22-56`；`MAIN/docs/workstreams/B2.md` §8；`docs/architecture/models.md:21,31`。
- 影响：`GET /api/v1/submissions/{id}` 的 `state_history` 对学生/教师**永久停在** `received→validated`，
  与运行的真实结局（rejected/failed/completed）不一致；B1 无法据提交状态展示"已被拒绝"。
- 分类：**标准不满足**（标准 1 子句 C）。

### 2. [标准满足但实现有问题] 运行级状态机用 `queued→rejected` 表达静态阻断，与冻结的提交状态机措辞冲突
- 现象：判据/文档说要 `validated→rejected`，实现（内部机器）走 `queued→rejected`；两套状态机都叫 `rejected/running`，
  但完全没有映射关系，也无人负责同步。
- 最小复现：`标准1(b) 实测静态阻断路径 = None->queued -> queued->rejected`。
- 文件行号：`src/autograder_b2/evaluation/store.py:50-59`（`RUN_TERMINAL_STATES`/`RUN_TRANSITIONS`）、
  `store.py:9-11` 模块 docstring（自称允许 `queued` 直达终态的原因）；`src/autograder_b2/domain/states.py:4-8`；
  `docs/architecture/models.md:21` vs `docs/architecture/interfaces.md:192`（后者说"把**评测**置 rejected"——文档自身措辞不一致）。
- 分类：**标准满足但实现有问题**（是第 1 条不一致的根因）。

### 3. [标准满足但实现有问题] 并发闸门的覆盖范围与实例化边界
- 现象 (a)：`DynamicExecutionGate` 只包住 `runner.run_case`，工作区物化在闸门外；实测同一批并发下物化峰值 = 4。
- 现象 (b)：`gate` 默认按实例创建，两个编排器各自默认闸门时实测 runner 峰值 = 2；跨进程无全局上限。
- 最小复现：`标准3(B')` / `标准3(D)`。
- 文件行号：`src/autograder_b2/evaluation/orchestrator.py:148-153`（docstring 自述进程内/单进程 worker）、
  `orchestrator.py:249`（`DynamicExecutionGate()` 每实例新建）、`orchestrator.py:586-596`（物化在 `_execute_case` 之外）、
  `orchestrator.py:618-619`（闸门范围）。
- 分类：**标准满足但实现有问题**（标准 3 在"共享单一闸门实例 + 只算沙箱作业"的读法下成立）。

### 4. [标准满足但实现有问题（次要）] 提交级批量迁移是逐行校验，不是批量校验
- 现象：`record_transitions` 在循环内**逐行**校验后立即插入；若批量中第 2 行非法，第 1 行已写入。
  调用方若在事务内吞掉异常并提交，会留下半截历史。实测（我构造的吞异常场景）：
  `标准1(d) 批量迁移第 2 行非法 → IllegalTransition('received' -> 'running' is not an allowed transition)`
  `标准1(d) 该提交行/史 = ('validated', [(None, 'received'), ('received', 'validated'), (None, 'received')])`
  （出现重复的 `(None,'received')` 行）
- 最小复现：`uv run pytest tests/test_orch_probe_review.py -s -k probe1`（`标准1(d)`）。
- 文件行号：`src/autograder_b2/domain/transitions.py:43-56`（校验与插入在同一次循环迭代内）。
- 影响：当前唯一调用方（`intake/service.py:303`）不受影响，运行级 `store.transition_run` 是"先校验后写"无此问题；
  属潜在脆弱点。
- 分类：**标准满足但实现有问题**（次要）。

### 5. [仅文档问题] `store.py`/`progress.py` 仍称"迁移 0006 的 CHECK 与契约冲突"，但 0007 已放宽
- 现象：`progress.py:62-69` 的 `SCHEMA_CONFLICT_CONSTRAINTS` 注释与 `store.py:12-14`、`store.py:100-108`
  仍在描述"终态一律要求非空 `terminal_reason`"，而 `alembic/versions/20260915_0007_b2_terminal_reason_semantics.py`
  已把两条 CHECK 改成"`completed` 必须为 NULL、`rejected/failed` 必须非空"。该兜底分支实际不可达。
- 文件行号：`src/autograder_b2/evaluation/progress.py:62-69`；`src/autograder_b2/evaluation/store.py:12-14,100-108`；
  `alembic/versions/20260915_0007_b2_terminal_reason_semantics.py:38-53`。
- 分类：**仅文档问题**（行为正确：`completed` 无原因可落库，我实测的 `1(c)` 成功路径即依赖此）。

### 6. [仅文档问题] 测试/需求状态表过期
- `docs/testing/cases.md:62-63` 的 `T-PERSIST-001/002` 标注仍为"待建"，但 `tests/test_evaluation_pipeline.py`
  已有对应用例；`docs/requirements/traceability.md:12-14` 中 `RQ-B-2-40/50/60` 状态仍为"待实现"。
- 分类：**仅文档问题**。

### 既有断言的"打中要害"评估（供参考，不计入上述清单）
- 标准 1：`test_illegal_run_transition_is_rejected_before_write` 的 `after == before` 确实打中"写入前拒绝"，
  但**完全不覆盖** `validated→rejected` 子句（该文件对 `validated` 零匹配）。
- 标准 2：帧类断言（单调、单一终态、`after=` 续读、`frame_stream` 遇终帧结束）打中要害，且用的是真实库帧，
  不是替身产物；终态帧唯一性只试了 1 种重复（我补到 3 种）。
- 标准 3：`test_dynamic_execution_gate_limits_peak_concurrency_to_one:1571` 的 `assert gate.peak <= 1`
  **单独看有恒真风险**（若闸门未被使用或根本没发生重叠，它照样通过）；它靠 `capacity == 1` 与
  `acquisitions == 8` 兜住"闸门被使用"，但不兜"确实存在重叠压力"，也没有独立观测。我用 (A)/(C) 两处正向对照补上了。

---

## 我无法验证的部分

1. **生产并发接线**：仓库内没有任何代码调用 `B2Runtime.evaluate`（worker 入口，`src/autograder_b2/runtime.py:283`；
   `grep "\.evaluate("` 只命中定义），并发提交的驱动方在 B2 之外。我只能证到"共享单一 gate 实例时峰值 ≤ 1"，
   无法证明生产宿主确实只持有一个 runtime/orchestrator 实例（静态证据仅 `runtime.py:302-308`、`runtime.py:351`）。
2. **跨进程 / 多 worker 的全局并发上限**：`DynamicExecutionGate` 是进程内信号量，多进程下峰值可 > 1；
   我未起多进程编排器实测（属 docstring 明示的"集成方扩容工作"）。
3. **Linux/nsjail 真实沙箱**：当前宿主是 Windows，真实 runner 不执行（既有测试与我的探针都用 runner 替身），
   因此"动态评测并发峰值 ≤ 1"是在**编排层 + 替身 runner** 上验证的；真实沙箱进程的资源账本（≤96 MB/0.45 vCPU）未测。
4. **SSE 的真实网络中断**：我验的是生成器层面断线 + `Last-Event-ID` 续读，以及 TestClient 的 HTTP SSE 重连；
   未做真实长连接掉线/代理中断。
5. **提交级状态机"应由谁写"**：我只能证明"没有任何代码路径写 `validated→rejected`"，
   不能证明契约要求 B2 写还是集成方写（判据按 `MAIN/docs/workstreams/B2.md` §8 的"B2 不可修改公共契约"归 B2）。
6. **安全判定与沙箱执行的替身**：两条标准都不依赖它们**做判定**（替身只返回构造好的结论/证据），
   但"真实安全判定端到端可用"不在本次复核范围。

---

标准1=FAIL 标准2=PASS 标准3=PASS
