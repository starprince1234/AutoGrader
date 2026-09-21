# 再裁决记录 · 标准 1（打回 → 修复 → 再裁决）

复核者：独立复核 agent。本次为**再裁决**（第二次裁决，非首次）。环境同前：Windows + PowerShell，
Python 3.12.3，真实 PostgreSQL 17.11（容器 `autograder-b2-postgres`，端口 55433），
`$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55433/autograder"`。

**独立性声明**：实现者已按我的打回改了 `src/**`，并**改写了我上一轮的探针文件**
`tests/test_orch_probe_review.py`（把原来"钉住缺陷"的断言改成"钉住修复"，实现者已在消息里披露）。
因此本轮我**另写了自带的独立探针** `tests/test_orch_probe_recheck.py`（6 条，自带全部夹具，不 import 实现者测试），
本轮结论只依据：① 我自己读改动后的源码；② 我的新探针；③ 我重跑的既有文件。
上一轮探针文件（被改过的）我也照跑，仅用于交叉核对，不用作判据。

## 结论：标准1 = **PASS**（本轮改判）

| 子句 | 上一轮 | 本轮 | 依据 |
|---|---|---|---|
| 非法迁移被拒（写入前行数不变） | 成立 | 成立 | 运行级未改；上一探针 (a) 段断言仍在且全绿；新探针 ③(i)/④ |
| 迁移史无 `running` 记录 | 成立 | 成立 | 新探针 ① `运行迁移史 = [(None,'queued'),('queued','rejected')]` |
| 静态阻断路径为 `validated→rejected` | **不成立** | **成立** | 新探针 ① `提交 后 = ('rejected', [(None,'received'),('received','validated'),('validated','rejected')])` |

判据层级说明（重要）：`validated → rejected` 是**提交（submission）**状态机的取值域——
冻结契约 `MAIN/contracts/openapi/b2.yaml:220-249` 的 `SubmissionTransitions` 明文列出
`validated: [queued, rejected]`，并写明"静态分析发生在 `queued` **之前**"；`MAIN/docs/workstreams/B2.md` §8 把该取值集合列为
B2 不可修改的公共契约；`MAIN/contracts/CHANGELOG.md:44` 记载该 schema 正是为回答"静态阻断走 `validated → rejected` 短路是否合法"而新增。
本轮改动把这条迁移真正写进了提交状态机（运行级仍是 `queued→rejected`，两套取值域各自独立，同事务写入）。

## 我做的独立验证（命令 + 真实输出）

```powershell
uv run ruff check tests/test_orch_probe_recheck.py            # All checks passed!
uv run pytest tests/test_orch_probe_recheck.py -q -s --no-header -p no:cacheprovider
# 6 passed in 4.96s        （完整输出：.pytest-tmp/b2-orch-review/recheck-output.txt）
uv run pytest tests/test_orch_probe_recheck.py tests/test_orch_probe_review.py tests/test_evaluation_pipeline.py -q
# 50 passed in 23.94s     （6 我的新探针 + 3 被改过的旧探针 + 41 实现者文件，原为 40）
uv run pytest tests/test_b2_routes.py -k "event_stream or rejected_run_stream" -q
# 5 passed, 125 deselected, 2 warnings in 3.94s
uv run pytest tests/test_submission_query.py tests/test_group_acceptance_probe.py -q
# 72 passed, 2 warnings in 11.25s
```

### ① 静态阻断落到提交上（要求 ①）

```
① outcome = rejected static_blocked | runner 调用 = 0
① 运行迁移史 = [(None, 'queued'), ('queued', 'rejected')] | 帧 = [(1,'progress'),(2,'progress'),(3,'progress'),(4,'progress'),(5,'rejected')]
① 提交 前 = ('validated', [(None,'received'),('received','validated')])
① 提交 后 = ('rejected', [(None,'received'),('received','validated'),('validated','rejected')])
① 提交史含 (validated,rejected) = True
① 提交史是否出现 queued/running = False
① 提交史逐条链接合法 = True
```
断言：提交 state == `rejected`；提交史**恰为**三条且末条为 `(validated,rejected)`；无 `queued`/`running`；
每条 `from` == 前一条 `to` 且每步都在 `domain.states.SUBMISSION_TRANSITIONS` 内；运行史无 `running`；runner 零调用。

### ② 成功路径走完提交状态机（要求 ②）

```
② outcome = completed | 提交 state = completed
② 提交迁移史 = [(None,'received'),('received','validated'),('validated','queued'),('queued','running'),('running','completed')]
② 提交史逐条链接合法 = True
```

### ③ 非法迁移零写入（要求 ③，三小项）

```
③(i) 提交已达 completed；非法推进返回值 = ['rejected->None', 'queued->None', 'running->None', 'failed->None']
③(i) state before=completed after=completed; 史行数 before=5 after=5
③(ii) 取包失败 outcome = failed infrastructure_error
③(ii) 提交 前 = ('validated', [...])  后 = ('validated', [(None,'received'),('received','validated')])
③(iii) 沙箱不可用 outcome = failed | 提交 state = failed
③(iii) 提交迁移史 = [(None,'received'),('received','validated'),('validated','queued'),('queued','running'),('running','failed')]
```
- (i) 终态出发的推进（`advance_submission_state` 直接调用）返回 `None`，状态与史行数**均不变**（5→5）。
- (ii) 运行**前**的技术失败（取包失败）：契约无 `validated→failed`，提交**零写入**（仍 `validated`）——守卫由状态机本身给出。
- (iii) 运行**后**的技术失败：提交必须先经 `queued`/`running` 再到 `failed`，且史中**没有** `validated→failed`。
- 补充（我另加）：`⑥` 用"同一事务内先迁运行、再迁提交、然后抛错"验证两边**一起回滚**：
  `⑥ 事务中途失败后: 运行 state = queued`，提交状态与史完全未变 → `orchestrator` 的同事务写入语义成立。

### ④ 批量迁移第 2 行非法 ⇒ 一行都不写（要求 ④）

```
④ 批量迁移第 2 行非法 → 异常 = IllegalTransition('received' -> 'running' is not an allowed transition)
④ 史行数 before=2 after=2
④ 史 = [(None, 'received'), ('received', 'validated')]
```
复现方式与上一轮完全一致（调用方在事务内**吞掉异常后照常提交**）——上一轮会留下重复的 `(None,'received')` 行，本轮**零写入**。
源码核对：`src/autograder_b2/domain/transitions.py:40-41` 先整批 `require_transition`，之后才进入写循环（`:49-61`）。

### ⑤ 标准 2 / 3 是否受本次改动影响（回归）

```
⑤ 标准2[rejected] 帧=[(1,'progress')…(5,'rejected')] 严格单调 终态=['rejected'] 断线重连拼接=[1,2,3,4,5]
⑤ 标准2[failed]   帧=[(1,'progress')…(7,'failed')]   严格单调 终态=['failed']   断线重连拼接=[1,2,3,4,5,6,7]
⑤ 标准3 容量=1: runner 调用=8 外部观测峰值=1 残留 live=0
⑤ 标准3 正向对照 容量=3: runner 调用=8 外部观测峰值=3
```
（每条 run 的 `read_frames(after=pivot)` 穷举 pivot 拼接==全集；`frame_stream` 第 2 帧断开后带游标续读拼接==全集。）
**标准 2 / 3 判定不变：PASS**——本次改动的三条写入都在既有终态/动态事务内新增了提交侧的 UPDATE + INSERT，
未改帧写入顺序与闸门范围；我的独立仪器重测结果与上一轮一致。

## 残余不一致（本轮新增/仍存在；均**不**改变标准 1 的 PASS）

### R1 [标准满足但实现有问题] 提交状态是"一次性"同步：同一提交再次评测后提交状态会与最新运行不一致
```
③(iv) 第一次运行 = rejected | 提交 = rejected
③(iv) 第二次运行(同一提交) = completed | 提交 = rejected
③(iv) 提交迁移史 = [(None,'received'),('received','validated'),('validated','rejected')]
```
- 最小复现：`uv run pytest tests/test_orch_probe_recheck.py -s -k illegal_advance` 的 (iv) 段。
- 可达性证据：`b2.evaluation_runs` 对 `submission_id` **只有普通索引，没有唯一约束**
  （`alembic/versions/20260915_0006_b2_evaluation_tables.py:64,116-118,124`；该文件 `:155` 的唯一约束属于
  `evaluation_run_state_transitions(run_id, sequence)`），`src/autograder_b2/runtime.py:283-299` 的
  `evaluate(submission_id)` 也没有"已有运行则拒绝"的守卫——worker 重试/重跑即可走到该状态。
- 影响：`GET /api/v1/submissions/{id}` 的 `state_history` 会说 `rejected`，而**最新**运行是 `completed`；
  `docs/architecture/models.md:31` 的"`queued` 之后 1:1 同步"在这条路径上不成立。
- 实现者的取舍本身可辩护（终态无出边，不为同步编造非法迁移，docstring 已写明）——但它需要一个明确策略：
  要么禁止同一提交重复建运行，要么在重跑时显式表达"重新评测"（这两条都涉及设计，不在本轮我判 FAIL 的范围内）。
- 分类：**标准满足但实现有问题**（属集成/重跑语义，不属标准 1 的三条子句）。

### R2 [仅文档问题 + 声明与事实不符] `progress.py` 的 stale 冲突叙述**并未**按声明改掉
```
src/autograder_b2/evaluation/progress.py:62-65  #: 迁移 0006 要求**终态**（含 completed）必须携带属于四种**失败**原因的 terminal_reason，
                                                #: 而契约…明确说 completed 的 terminal_reason 为 null。两者不可能同时满足…
src/autograder_b2/evaluation/progress.py:66-69  SCHEMA_CONFLICT_CONSTRAINTS = {"ck_progress_events_terminal_frames": "迁移 0006 强制 completed 帧携带非空 terminal_reason", …}
src/autograder_b2/evaluation/progress.py:185-186   # 契约：… （迁移 0006 的 CHECK 与之冲突，冲突由数据库拒绝写入暴露为 SchemaConflictError…）
```
- 我上一轮点名的正是 `progress.py:62-69`；该处文字与上一轮**逐字相同**，`:185-186` 同样未改。
  更强的证据：`progress.py` 的总行数（433）以及 8 处行号锚点（`EVENT_PROGRESS`=34、`FrameRejected`=72、
  `next_sequence`=212、`append_frame`=227、`read_frames`=308、`frame_stream`=374、`_lock_run`=420、文件末行=433）
  与打回前**完全一致** ⇒ 该文件**未被修改**（若曾删除/新增行，锚点必然位移）。
  `store.py` 一侧**确实已改**：总行数 723 → 775，`:12-15` 现写"自**迁移 0007** 起一致"，并新增第 4 条硬规则说明
  `advance_submission_state` 是评测侧唯一出口。
- 事实依据：`alembic/versions/20260915_0007_b2_terminal_reason_semantics.py:38-53` 已把两条 CHECK 改成
  "`completed` 必须 NULL、`rejected`/`failed` 必须非空"，冲突不再存在，该兜底分支不可达。
- 分类：**仅文档问题**（附带：修复声明与仓库事实不完全一致，建议核实后再宣称）。

### R3 [已声明的边界，本轮不判为不满足] 闸门范围 / 实例化 / 跨进程
- 实测不变：闸门只包 `runner.run_case`（`orchestrator.py:618-619`，物化在其外，外部观测物化峰值 4）；
  `gate` 按编排器实例创建（`orchestrator.py:249`；两实例外部峰值 2）；跨进程无全局上限（docstring 自述单进程 worker）。
- 我接受"这是已文档化的扩容边界、且本小类判据只要求并发提交下动态评测峰值 ≤ 1（生产装配为单一 orchestrator）"这一界定，
  但要求集成侧**必须**保证全进程只有一个 orchestrator（`runtime.py:302-308,351` 目前确实只建一个）。

### R4 [设计取舍，非缺陷] 运行前的技术失败：提交停在 `validated`，运行是 `failed`
- 实测 `③(ii)`：契约没有 `validated→failed` 这条边，因此提交保持 `validated`。实现者 docstring
  （`orchestrator.py:875-878`）已明确声明这是有意选择（不编造迁移）。
- 影响面：`GET /submissions/{id}` 会显示 `validated` 而运行是 `failed`；`grades/query.py:16` 有"有运行则取运行状态"的回落逻辑，
  因此成绩视图不受影响。属**观察项**。

### R5 [仅文档问题，未变] 文档状态表仍然过期
- `docs/testing/cases.md:62-63` 的 `T-PERSIST-001/002` 仍为"待建"；`docs/requirements/traceability.md:12-14`
  的 `RQ-B-2-40/50/60` 仍为"待实现"。

## 对上一轮清单的逐条处置核对

| 上轮编号 | 内容 | 本轮核实 |
|---|---|---|
| #1 | 提交状态机死代码 / 未写 `validated→rejected` | **已修**（新探针 ①） |
| #2 | 运行级 `queued→rejected` 与提交级措辞冲突、无同步 | **已解决**：契约（`b2.yaml:220-249`）确认 `validated→rejected` 属提交级；提交级现已写入，两套取值域各管各的且同事务。残余 = R1（重跑语义），已给最小复现 |
| #3 | 闸门范围/实例/跨进程 | 未改；实现者声明为已文档化边界 → 记为 R3（不判不满足） |
| #4 | 批量迁移逐行校验 | **已修**（新探针 ④，零写入） |
| #5 | `store.py`/`progress.py` stale 冲突叙述 | **部分修复**：`store.py:13,114` 已改；`progress.py:62-69,185-186` **未改**（R2） |
| #6 | 文档状态表过期 | 未改（R5） |
| 夹具保真度（我未列入清单，实现者主动修） | `_seed_submission` 由 `state="queued"` 改为 `"validated"` + 真实迁移史 | 已核实（`tests/test_evaluation_pipeline.py:454-477`），且理由与契约一致（静态分析在 `queued` 之前）→ 该修正是**必要的**，否则 `validated→rejected` 这条判据在 `queued` 起点上根本无法被断言 |

## 我无法验证的部分（与前次相同，未变化）

1. 生产并发接线：仓库内 `grep "\.evaluate("` 只命中 `runtime.py:283` 的定义，worker 驱动在 B2 之外；
   "生产只存在一个 runtime/orchestrator"仅有静态证据（`runtime.py:302-308,351`）。
2. 跨进程/多 worker 的全局并发上限（gate 为进程内信号量）。
3. Linux/nsjail 真实沙箱下的资源账本（本机 Windows，runner 为替身）。
4. SSE 的真实网络掉线（我验的是生成器断线 + `Last-Event-ID` 与 TestClient 重连）。
5. R1 中"同一提交重复评测"是否是生产允许的操作：契约与文档未明确，我无法判定应为"禁止重复运行"还是"重跑即新运行"。

---

标准1=PASS 标准2=PASS 标准3=PASS
