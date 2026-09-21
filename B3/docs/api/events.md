# B3 事件（Events）

事件 envelope 结构以 `../../../MAIN/contracts/schemas/event-envelope.schema.json` 为准；subject 以 `../../../MAIN/contracts/asyncapi/events.yaml` 为准。**投递语义为 at-least-once，消费者必须按 `event_id` 去重。**

状态（2026-09-15）：两向均已实现。发布侧走 outbox（与业务变更同事务），消费侧走 durable consumer + inbox 去重；真实事件经 `jsonschema` 对 MAIN 的 envelope schema 校验（`tests/test_events.py` 13 例）。

## 我发布的事件（1 个）

| 属性 | 值 |
|---|---|
| subject | `autograder.v1.question.version.published` |
| 触发时机 | 题目版本 `publish` 完整性校验通过、不可变版本落库、package 编译成功后（**同一事务**写 `b3.event_outbox`） |
| payload 语义 | 仅含标识与摘要：`question_id`、`question_version_id`、`course_id`、`content_digest`、`published_at`（键集合被测试**精确断言**） |
| 是否含敏感数据 | **否**（强制；发布前校验 + 测试断言答案正文/期望输出/用例名均不出现在序列化后的 payload 中） |
| 投递 | `domain/outbox.py: drain_outbox` 从 outbox 取待发行并投递；失败**保留待重试**并记 `last_error`/`attempts`，绝不静默丢弃 |

- 消费方（需按需内部拉取内容）：B2 可据此感知新版本，但实际 package 内容经 `evaluation-package` 内部接口拉取，事件不携带内容。

## 我订阅的事件（1 个）

| 属性 | 值 |
|---|---|
| subject | `autograder.v1.evaluation.completed` |
| 发布方 | B2（消费端校验 `producer == "B2"`，其它来源一律拒收） |
| 消费语义 | 去标识化答题统计输入（作答次数、通过率、得分分布、失败类别分布） |
| 去重键 | `event_id`（`b3.event_inbox` 主键）；`evaluation_run_id`（业务唯一键，`uq_event_inbox_evaluation_run_id`） |
| 重试策略 | at-least-once；JetStream durable consumer（`B3_CONSUMER_NAME`，默认 `b3-question-statistics`）；**重复投递要 ack**（否则会无限重投），隐私越界/畸形消息**不 ack** → 重投 → DLQ |
| DLQ 策略 | 达最大投递次数进入 DLQ，可查询与人工重放（NATS 运维属 B4，`RQ-B-4-20`） |

### 消费端字段白名单（隐私边界的落地）

- **允许**（`events/whitelist.py: ALLOWED_PAYLOAD_FIELDS`，逐条对应 `MAIN/docs/api/events.md` §3.4 的"允许携带（去标识化）"）：`evaluation_run_id`、`submission_id`、`question_version_id`、`course_id`、`section_id`、`is_pass`、`score`、`max_score`、`score_bucket`、`result_digest`、`failed_category`、`duration_ms`、`occurred_at`。
- **禁止**（显式列出以便给出可读拒绝原因，`FORBIDDEN_PAYLOAD_FIELDS`）：`student_id`、`student_name`、`student_number`、`email`、`source_code`、`source`、`code`、`files`、`submission_content`、`reference_answer(s)`、`hidden_cases`、`test_cases`、`expected_output`、`expected_stdout/stderr`、`stdout`、`stderr`、`cases`、`fixtures`、`token`、`authorization`。
- **不在白名单内的一切字段**（含未知字段）同样被拒——"未知即拒绝"，不是"未知即忽略"。
- 拒收行为：写 ERROR 日志（含原因与**字段名**，不含字段值）、**不 ack**、不入库、不计数。
- 入库的 `payload` 只保存白名单校验后的字段；重放时**再校验一次**（`event_from_inbox_row`），因此即使库中行被外部篡改也无法把违规字段带进聚合。

### 统计口径（实现级）

- `attempt_count`：去重后的作答次数。
- `pass_rate`：仅以**显式** `is_pass` 为分子分母；无任何已知结果时为 `0.0`（`pass_known_count = 0`，测试断言该语义）。
- `score_distribution`：`score / max_score` 的 20 分带（`0-19`/`20-39`/`40-59`/`60-79`/`80-100`）；事件自带 `score_bucket` 时以其为准；缺 `max_score` 时计入 `unbucketed_attempt_count` 而**不猜测**。
- `failure_category_distribution`：`failed_category` 计数。
- **不提供**：独立作答人数、平均分、任何逐学生明细（事件禁止 `student_id`，DTO 无对应字段 → [CHG-B3-0003](../contracts/changes/CHG-B3-0003-distinct-student-statistic.md)）。B3 **不**从 `submission_id` 反推人数——那是把伪名当身份用。
- 时间窗查询（`from`/`to`）从 `event_inbox` 按 `occurred_at` **keyset 分页**（每页 1000 行，硬上限 100 万行）重算，与全量预聚合路径**结果一致**（测试断言）。超上限返回 `422 stats-window-too-large`，**不静默截断**。
- **消费健康**：每次投递（含**从不进 inbox** 的拒收）都写入 `b3.consumption_counters`。四态 `idle` / `consuming` / `degraded` / `unknown` 由 `plugin.health()` 并入 `HealthReport.detail`（B4 readiness 透出），`python -m autograder_b3.consumer_cli --health` 输出 JSON 并以退出码表达状态。

## 幂等与顺序假设

- **幂等**：重复投递同一 `event_id` 不产生重复业务效果；同一 `evaluation_run_id` 换 `event_id` 也不重复计数。
- **顺序**：聚合对顺序不敏感（全部为整数计数、无浮点累加器），`tests/test_statistics.py::test_replay_out_of_order_produces_the_same_numbers` 用**倒序重放**证明。
- **可重放**：`python -m autograder_b3.consumer_cli --rebuild`（或 `EvaluationCompletedConsumer.rebuild()`）清空统计表后从 inbox 按 `occurred_at` 重放，得到相同数值。

## 已知缺口

1. `B3Module.descriptor.events` 是扁平元组且**不区分方向**（它承载 `module.yaml` 的 publishes ∪ subscribes，两个事件都在其中）；`module.yaml` 才是带方向的权威。宿主目前无法校验事件方向（SDK 已知缺口，`../../../MAIN/docs/adr/ADR-0003`）。B3 的处理：**以 `module.yaml` 为准**，不发明第二套约定（那会变成兼容层），并在 `docs/architecture/overview.md` 与本文件如实记录。
2. `is_pass` 字段名**已裁决为权威名**（`CHG-B3-0003`）：`MAIN/contracts/examples/event-evaluation-completed.json` 现含 `score` / `max_score` / `is_pass`，B3 的聚合按 `is_pass` 计通过率、按 `score` + `max_score` 分桶。若 B2 实际发布其它名字，B3 会**拒收并进 DLQ**，不会静默漏统计。
3. **健康面只有"状态"，没有"曾经跑过"的证明**：`b3.consumption_counters` 能区分空闲与拒收，但本环境没有 NATS，`event_inbox = 0` 仍是真实环境里的当前状态——它**不构成链路联调的证据**。
4. 窗口统计的性能：10 万事件量级已实测，预聚合路径 0.044 s，窗口路径 23.8 s（**未达标**），见 [../phases/active/phase-03-quality-attestation-and-bounded-statistics.md](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)。
