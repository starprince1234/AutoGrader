# CHG-B3-0003 "独立作答人数"不可计算，`is_pass` 字段名需 Confirm

- **提出方**：B3（WP-B3-C）
- **日期**：2026-09-15
- **状态**：**已裁决（MAIN，2026-09-18）：独立人数归 B2、平均分不入 B3、`is_pass` 为权威名；B3 实现不变**
- **规程**：`../../../MAIN/docs/contracts/change-protocol.md`

## 1. 观察到的差异

| 侧 | 内容 |
|---|---|
| 需求侧 | `RQ-B-3-30` 规范解释与 B3 `acceptance.md` 3-30-1 提到统计口径包含"**独立作答人数**"。 |
| 事件契约（权威） | `MAIN/docs/api/events.md` §3.4 **禁止携带** `student_id`，并已在"⚠️ 待决问题"中写明：若不含 `student_id`，B3 只能统计**作答次数**而无法统计**独立人数**，该口径由 B2 在成绩查询侧提供；同一段还写明"此决策需在 B3 实现统计时确认并在 `rq-catalog.md` 的 `RQ-B-3-30` 验收中明确"。 |
| DTO 契约（权威） | `MAIN/contracts/openapi/b3.yaml` 的 `QuestionStatistics` 只有 `attempt_count` / `pass_rate` / `score_distribution` / `failure_category_distribution`，**没有**独立人数或平均分字段，且 `additionalProperties: false`。 |

结论：三方一致地指向"B3 不算独立人数"。B3 据此实现：**不**尝试从 `submission_id` 反推人数（那是把伪名当身份用），也**不**新增字段。

## 2. 请 MAIN 裁决的问题

1. 请确认 `RQ-B-3-30` 的"独立作答人数"由 **B2 成绩查询侧**提供，并把 `rq-catalog.md` 中 `RQ-B-3-30` 的验收措辞改为"作答次数/通过率/失败类别分布"，以免后续按字面验收时误判 B3 未完成。
2. 同理，"平均分"目前也无处承载（`QuestionStatistics` 无对应字段）。是否需要 MAIN 增加字段？若不增加，B3 将维持"不计算平均分"（B3 也不存储分数原值，只存分布计数）。
3. **`is_pass` 字段名**：`evaluation.completed` 的 payload 字段名在契约中未逐字固定；B3 依据 `MAIN/contracts/schemas/evaluation-report.schema.json` 的 `outcome.is_pass` 采用了 `is_pass`。请确认该名称为 B2 发布时的权威字段名；若 B2 实际发布的是别的名字（如 `passed`），请指出——B3 的消费端按**白名单**校验，未知字段会被拒绝并进入 DLQ，而不是被静默忽略。

## 3. B3 当前实现（契约内落地）

- 消费端白名单（`src/autograder_b3/events/whitelist.py: ALLOWED_PAYLOAD_FIELDS`）只接受 `MAIN/docs/api/events.md` §3.4"允许携带"清单中的字段；`student_id` 在 `FORBIDDEN_PAYLOAD_FIELDS` 中显式列出，出现即拒收。
- 统计口径：`attempt_count`（作答次数）、`pass_rate`（仅以显式 `is_pass` 为分子分母）、`score_distribution`（`score/max_score` 的 20 分带；缺 `max_score` 时计入 `unbucketed_attempt_count` 而不是猜测）、`failure_category_distribution`。
- 未实现（因为无处承载）：独立作答人数、平均分、逐学生明细。
- `max_score` 在官方示例 `MAIN/contracts/examples/event-evaluation-completed.json` 中缺失，导致该示例的分数无法分桶（只能计入 unbucketed）。若 MAIN 认为分桶是必需口径，请在示例与文档中补 `max_score`（或 `score_bucket`）。

## 4. B3 侧的痕迹

- 实现：`src/autograder_b3/events/whitelist.py`、`src/autograder_b3/stats/buckets.py`、`src/autograder_b3/stats/service.py`。
- 测试：`tests/test_statistics.py`（去重、重放稳定、窗口查询、越界字段拒收、`is_pass` 缺失时的口径）、`tests/test_auth.py`（统计查询的角色与课程作用域）。
- 文档：`docs/api/events.md`（消费端白名单与口径）、`docs/requirements/traceability.md`（`RQ-B-3-30` 的已知限制列）。

---

## 5. MAIN 裁决（2026-09-18）

### 问题 1：独立作答人数归谁 → **归 B2 的成绩查询侧；收窄 `RQ-B-3-30` 措辞**

**理由。** 这不是分工偏好，而是由事件契约**结构性地**决定的：`MAIN/docs/api/events.md` §3.4 禁止
事件携带 `student_id`（去标识化是 B3 统计的前提）。没有 `student_id` 就没有"独立人数"这个量。
B3 若从 `submission_id` 反推人数，等于把一个伪名当成身份用——那既算不准，也绕过了去标识化的
设计意图。而 B2 拥有提交与成绩行，天然能算；这也与 CHG-B3-0003 引用段落里 events.md 早就写下的
"该口径由 B2 在成绩查询侧提供"一致。

**已实施的改动（需求侧）。** `MAIN/docs/requirements/rq-catalog.md` 的 `RQ-B-3-30` 验收口径改为
"作答次数 / 通过率 / 分数分布 / 失败类别分布"，并注明"独立作答人数由 B2 的成绩查询侧提供
（事件不含 `student_id`，见 events.md §3.4 与 CHG-B3-0003 裁决）"。B3 的
`docs/requirements/traceability.md` 同条 RQ 的限制列同步。

### 问题 2：平均分是否入 B3 → **不入**

`QuestionStatistics` 保持四个字段不变。B3 **不存储分数原值**，只存分桶计数——这是它作为去标识化
统计方的正确边界。要从分桶计数还原平均分只能得到近似值，而"近似平均值"比"没有平均值"更糟：
它会以权威的口吻出现在教师界面上。成绩侧的平均分由 B2 从真实成绩行计算。

### 问题 3：`is_pass` 是否为 B2 发布的权威字段名 → **不是"名字对不对"的问题：B2 此前根本不发布它**

**裁定前先核对了事实**（读 `B2/src/autograder_b2/events/payload.py` 的 `PAYLOAD_KEYS` 与
`orchestrator` 的唯一写入端）：`evaluation.completed` 的 payload 当时只有
`submission_id` / `evaluation_run_id` / `question_version_id` / `score` / `result_digest`
**五个键**——既没有 `is_pass`，也没有 `max_score`。

所以本问题的前提不成立：不是"B3 猜错了字段名"，而是 B3 的白名单里留着一个**永远不会到达**的键，
于是 `pass_rate` 恒为空、`score_distribution` 只能把每次作答都计入 `unbucketed_attempt_count`
（官方示例也缺 `max_score`，照示例实现必然如此）。一个恒为空的比率出现在教师界面上，比"没有这个
指标"更糟：它会以权威的口吻显示为 0。

**裁决：这两个键进入 `evaluation.completed` 的 payload。** 理由：

1. B2 在写入点**已经持有** `breakdown.max_score` 与 `breakdown.is_pass`（成绩行的同一份计算），
   发布它们不需要新增计算，只是把已有结论带出去；
2. 两者都**不是身份信息**，不违反 `events.md` §3.4 的去标识化约束（该条禁止的是 `student_id`）；
3. 这是让 `RQ-B-3-30` 的三项口径（通过率、分数分布）真正成立的前提，而不是可选增强。

**已实施的改动。**

| 文件 | 改动 |
|---|---|
| `B2/src/autograder_b2/events/payload.py` | `PAYLOAD_KEYS[EVALUATION_COMPLETED]` 增加 `max_score`（`_SCORE`）与 `is_pass`（新的 `BOOL_SHAPE` 规则）；`BOOL_SHAPE` 只接受真正的 `bool`（`isinstance(True, int)` 为真，因此布尔必须先判、且不接受 `1`/`"true"`） |
| `B2/src/autograder_b2/evaluation/orchestrator.py` | `completed` 入队时带上 `max_score` 与 `is_pass`（取自 `breakdown`，不重算） |
| `MAIN/contracts/asyncapi/events.yaml` + `contracts/examples/event-evaluation-completed.json` | 官方示例补上这两个键（此前照示例实现必然落进 `unbucketed`，是"官方示例自身不可分桶"的陷阱） |
| 四份 `contracts.lock` | 由 `scripts/relock_contracts.py` 重算 |
| B2 三处测试夹具 | `test_events_outbox` / `test_events_probe_review`（两处）/ `test_evaluation_pipeline` 的 completed payload 键集同步 |

**B3 侧改动：无。** 它的白名单**本来就允许**这两个键、分桶与通过率的口径也已经按它们写好——
本次裁决让那些代码第一次真正拿到数据。B3 的 `is_pass` 消费方式（白名单 + 未知字段拒收进 DLQ）
保留不变，它是正确的前向兼容策略。

### 问题 4：官方示例缺 `max_score` → **已补，同上一节**

**契约改动汇总：** `MAIN/docs/requirements/rq-catalog.md`（`RQ-B-3-30` 口径措辞）、
`asyncapi/events.yaml` 与 `examples/event-evaluation-completed.json`（补 `max_score` / `is_pass`）；
`openapi/b3.yaml` 的 `QuestionStatistics` **未动**（仍是四个字段：作答次数、通过率、分数分布、
失败类别分布）。契约摘要已变化，四份 `contracts.lock` 已由 `scripts/relock_contracts.py` 重算。

**模块侧改动：** B3 无；B2 两处源码 + 三处测试夹具，见问题 3 的表。
