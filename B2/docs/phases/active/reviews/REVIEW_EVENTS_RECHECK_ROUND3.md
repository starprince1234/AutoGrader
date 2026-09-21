# 复核记录（第三轮·定点复裁）：三条必办 + N12/N13 + 两条低优先项的处置

- 复核者：独立复核者（只读实现）。前两轮记录：`.pytest-tmp/REVIEW_EVENTS_RECHECK.md`、`.pytest-tmp/REVIEW_EVENTS_RECHECK_ROUND2.md`
- 本轮探针：`.pytest-tmp/reviewer_probe_round3.py`（**38/38 PASS**）
- 真实依赖：PostgreSQL 55433 + NATS JetStream 54223（未启停容器）

## 0. 干净串行基线（本轮的权威数字）

**先说一条自己的教训**：本轮我先并发跑了"全量后台 + 两模块前台"，得到 `8 failed, 31 passed` 与 `1 failed, 1101 passed` —— 两次都**是我自己并发的锅**（同一 broker 上的竞争），不是实现问题。串行重跑后：

```powershell
# 串行（无其他任务在跑）
uv run pytest tests/test_events_outbox.py tests/test_events_probe_review.py -q   # 39 passed  （连跑两次均 39 passed）
uv run pytest -q -p no:warnings                                                 # 1102 passed, 3 skipped, 1 xfailed
uv run ruff check .                                                             # All checks passed!
uv run pytest "tests/test_events_probe_review.py::test_review_1_..._gate" -q    # 1 passed ×3（单独跑 3/3）
# 降级 A：无 DB、无 broker
tests/test_events_outbox.py        → 9 passed, 15 skipped
tests/test_events_probe_review.py  → 3 passed, 12 skipped
# 降级 B：有 DB、broker 不可达
两模块合计                          → 18 passed, 21 skipped   （**0 failed**，与修正后的 phase 记录一致）
```

→ **用户本轮报告的全部数字（39 passed / 1102 passed / 9-15 / 3-12 / 18-21）与我的串行实测逐项一致。**

## 1. ①N11 —— PASS
AST 提取：`test_consumer_failure_is_logged_even_without_an_error_hook` 现为 **`db+broker`**（此前 `db`）。
降级 B（有 DB、无 broker）实测 **`18 passed, 21 skipped`、无 failed** —— 上一轮那条"合法降级配置下 suite 变红"已消除。
outbox 模块标注分布：**12 × `db+broker` / 3 × `db` / 5 × 纯逻辑函数**（24 个用例，含 1 个 ×5 参数化）。

## 2. ②phase-01 假数字 —— PASS
`docs/phases/active/phase-01-implementation.md:70`（`9 passed, 15 skipped` + 探针 `3 passed, 12 skipped`）、`:71`（两模块合计 `18 passed, 21 skipped`，并写明"skip 集恰为需 broker 的 21 条：outbox 12 + 探针 9；**不得出现 failed**"）、`:83`（`1102 passed, 3 skipped, 1 xfailed`）**与我的串行实测逐字一致**；`:86-87` 写明 1100→1101→1102 的增量来源（T-EVT-020 / T-EVT-021）。

## 3. ③REQUIRED_KEYS 口径收窄 + 覆盖断言 —— PASS（收窄成立、双向断言成立、旧地雷已端到端排除）

代码（`src/autograder_b2/events/payload.py:211-215`、`:237-246`）：`REQUIRED_KEYS` 由 `PAYLOAD_KEYS` 派生后对 `EVALUATION_PROGRESS` 覆盖为三个恒在键；`required_keys()` 自己做 subject 校验。

我的独立判定（P1.x）：
- progress 必填 = `{evaluation_run_id, sequence, stage}`、准许仍 5 键（P1.1/P1.2）；
- **全 5 个 subject 都满足 `required ⊆ allowed`**，且**除 progress 外 `required == allowed`**（P1.3/P1.4 —— 即"没有把必填放宽到别的 subject"）；
- `REQUIRED_KEYS` 恰好覆盖全部发布 subject（P1.5）；
- 逐 subject 逐键各缺一次：**必填键 → 被点名拒绝；可选键 → 仍被接受**（P1.6，全 subject 零反例）；
- 计数**存在时仍受形态校验**（`cases_total="five"` 被拒），**显式 `null` 也被拒**（P1.7/P1.8）⇒ "未知"只能靠"省略"表达，不存在 `None` 混入的缝隙；
- 未知 key / 未知 subject 仍被拒且不回显取值（P1.9/P1.10）。

**旧地雷端到端排除（本轮最关键的一项，P2）** —— 我用**真实生产方法**造出上一轮被判死的形状：
```
EvaluationOrchestrator._enqueue_progress(..., frame=ProgressFrame(cases_total=None, cases_done=None))
→ 落库 payload keys = ['evaluation_run_id','sequence','stage']      （真实写入端产物）
→ payload_rejections == []                                        （过门）
→ dispatch = DispatchReport(selected=1, published=1, failed=0, dead_lettered=0, last_error=None)
→ row: attempts=0, published_at=2026-09-16 07:55:52.586+00, dead_lettered_at=None
→ 真实流内 1 条 365 B，subject=autograder.v1.evaluation.progress，过 MAIN envelope schema，
   payload = {"evaluation_run_id": "...", "sequence": 77, "stage": "dynamic_execution"}
```
带计数的 5 键形状同样通过且完整（P2.6）。⇒ 上一轮"永不投递→DLQ"的裂缝**已真正闭合，不是只在单测里放行**。

**探针覆盖断言（`tests/test_events_probe_review.py:785-818`）已改为双向**：`required <= allowed`（:791）、非 progress 的 `required == allowed`（:792-793）、缺必填键必须点名拒绝（:804-810）、**缺可选键必须仍被接受，否则记为反例并失败**（:811-812、:818）⇒ 两个方向都能抓。实现侧 `T-EVT-021`（`test_events_outbox.py:1160-1200`，纯逻辑）钉住两种形状 + 三个恒在键逐个缺失 + progress 必填/准许集合。**未见放宽。**

### ③ 的两条"非必办"残留（建议，不阻塞）
1. **护栏位置（低）**：`required ⊆ allowed`、非 progress 的 `required == allowed`、`REQUIRED_KEYS` 覆盖全部 subject —— 这三条不变量目前**只在复核探针里断言**（`test_events_probe_review.py:790-793`），而该文件自述"复核结束后删除"；实现侧用例只钉了 progress 的具体集合（`test_events_outbox.py:1179`）。同时 `payload_rejections` 用的是 `REQUIRED_KEYS.get(subject, ())`（`payload.py:269`）——**新增 subject 却忘记登记必填集会静默变成"零必填"（fail-open）**。当前无此缺口（P1.5 已验证覆盖完整），建议把这三行搬进 T-EVT-021，让护栏留在交付套件里。
2. **已接受的取舍（低）**：计数改为可选后，"某个本该带计数的阶段漏了计数"不再被门拦住（属语义层，非结构层）；T-EVT-021 钉住了两种合法形状，但未断言"哪些阶段必须带计数"。方向正确，记为取舍即可。

## 4. ④告警误分类 —— PASS
`dispatcher.py:132-141` 按异常类型分派：`PayloadRejected`/`EventEnvelopeInvalid` → `log_event("events.outbox.payload_rejected")`；其余 → `log_dependency_unavailable(...)`。实测（P3）：
- 门拒绝：**恰好 1 条**，`{"level":"ERROR","event":"events.outbox.payload_rejected","error_type":"PayloadRejected","outcome":"failed","event_id":"...","attempts":1,"reason":"PayloadRejected: ... expected one of: ..."}`，**不含 `dependency`/`status`**（字段集合 = attempts/error_type/event/event_id/level/logger/outcome/producer/reason/timestamp），无 `MARKER`/令牌/路径/`Traceback`；控制流未变（`failed=1`、行保留未发布）（P3a.1–P3a.6）。
- broker 故障：**恰好 1 条** `messaging.broker_unavailable`，`dependency=message_broker`/`status=unavailable`/`error_type=EventBusUnavailable`，无凭据/主机泄漏；控制流未变（P3b.1–P3b.4）。
- 回归：消费者失败日志仍是恰好 1 条 `events.inbox.handler_failed` 且已脱敏（P3c.1）。
- runbook 两行（`docs/runbooks/troubleshooting.md:24-25`）已写"按事件名归因"与"逐行记日志、本模块不节流（节流属采集/告警层）"。

### 关于"要不要把 `payload_rejected` 写进 `docs/api/events.md`"
**不必**塞进 `§5/§6` 表格：那两张表是 `MAIN/docs/api/events.md` §5/§6 的**逐条对照表**，条目由 MAIN 清单固定，加内部日志事件名会让"对照"失真。
**但建议一行**：`docs/architecture/interfaces.md` §6.4 的失败语义表（`:383`）正是"日志与 readiness 如实反映"的登记处，现只写"日志…如实反映"而未点名事件 —— 在那行的"行为"列补上两个日志事件名（`messaging.broker_unavailable` / `events.outbox.payload_rejected`）即可，读者能在权威表里检索到。属可选（runbook + cases.md 已覆盖），不阻塞。

## 5. ⑤N12 —— PASS
`tests/linux/README.md` 新建（**4132 B**，首行 `# Linux nsjail 验收（tests/linux/）`），含验收项清单、两种运行方式（原生 / Windows 经特权容器，含 `docker build … -t autograder-b2-nsjail:local`）、与主套件的关系、`5a2` xfail 项与实测边界。四处引用（`docs/README.md:40`、`docs/testing/cases.md:154`、`phase-01:74`、`phase-01:88`）经解析**全部指向存在的文件**（P4.5–P4.7）。

## 6. ⑥N13 —— PASS
`MAIN/docs/contracts/change-protocol.md` §5 表不再出现"（暂无）"，并列出 CHG-0001..0004（P4.8/P4.9）；与 `changes/README.md` 索引一致。

## 7. 本轮另一条观察（低；属测试健壮性，非实现缺陷）
事件测试的 broker 读回/排空断言对**并发 broker 负载**敏感：我并发跑全量+两模块时出现 `8 failed, 31 passed` 与 `1 failed, 1101 passed`，全部串行后 `39 passed`（×2）与 `1102 passed`。涉及的读回用固定超时（探针 `drain(..., timeout=1.5)`）。建议 CI 不要与其他使用同一 broker 的套件并行；如要并行，把读回超时放宽或改为轮询到齐。

## 8. 结论
①②③④⑤⑥ **全部解除/合格**；③ 的收窄与双向断言**成立**，且未在别处留下"必填太严"（除 progress 外的 subject 仍 required == allowed，计数存在时仍校验形态、显式 null 仍被拒）。仅剩 2 条低优先建议（护栏搬进交付套件 / 并行 CI 注意事项）+ 1 条可选的 `interfaces.md` §6.4 补名 —— **均不阻塞**。
