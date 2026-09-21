# B2 事件（我发布 / 我订阅）

> 事件权威在 `../../../MAIN/contracts/asyncapi/events.yaml`；envelope 结构在 `../../../MAIN/contracts/schemas/event-envelope.schema.json`。**本文件不手抄字段**，只写发布/订阅关系、去重、重试与幂等。

## 1. 我发布的事件（5 个）

| subject | 触发时机 | payload 语义摘要 | 含敏感数据？ |
|---|---|---|---|
| `autograder.v1.submission.accepted` | 提交被接受并落库后 | 提交标识 + 题目版本绑定（**不含源码**） | 否 |
| `autograder.v1.evaluation.started` | 评测进入 `running` | `evaluation_run_id` + 环境摘要 | 否 |
| `autograder.v1.evaluation.progress` | 评测进行中 | 进度片段（阶段/完成度） | 否 |
| `autograder.v1.evaluation.completed` | 评测成功完成 | 分数 + 去标识化证据摘要（供 B3 统计） | 否 |
| `autograder.v1.evaluation.failed` | 评测技术性失败 | 失败类别（脱敏） | 否 |

硬性约束：任何事件 payload **不得**携带源码、参考答案、隐藏用例、凭据或超范围 PII（见 [../architecture/boundaries.md](../architecture/boundaries.md)）。

## 2. 我订阅的事件（1 个）

| subject | 消费语义 | 去重键 |
|---|---|---|
| `autograder.v1.question.version.published` | 题目新版本发布 → 缓存失效 / 标记可用 | `event_id` |

## 3. 去重键、重试、DLQ、幂等

- **投递语义**：at-least-once（NATS JetStream durable consumer）。
- **去重键**：`event_id`（事件唯一标识，见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md)）。消费者按 `event_id` 去重，重复投递不产生重复业务效果。
- **重试**：达到最大投递次数的消息进入 DLQ，可查询与人工重放（MQ 运维由 B4 负责，见 RQ-B-4-20）。
- **发布幂等**：发布走 outbox，先写 outbox 再发事件；`submission.accepted` 与幂等键绑定，同一 `idempotency_key` 不重复发布。
- **消费幂等**：`question.version.published` 重复投递只执行一次缓存失效（按 `event_id` 去重）。
- **顺序假设**：同一 `evaluation_run_id` 的 `started → progress → completed/failed` 顺序由本模块保证；跨实体不假设全局顺序。

## 4. 与 `module.yaml` / `descriptor` 的一致性（如实记录）

- `module.yaml` 声明：publishes 5 个、subscribes 1 个（见上）。
- `B2Module.descriptor.events` 是**扁平元组**（SDK 的 `ModuleDescriptor.events` 不区分方向），因此列出
  `module.yaml` 声明的**全部 6 个** subject（5 publishes + 1 subscribes）：registry 比较的是
  `publishes ∪ subscribes`（`ModuleManifest.events`）并要求两侧相等，漏一个就会让宿主启动中止。
- 状态：**集合与数量已一致**（`descriptor.events` == 6 == `module.yaml` 的 5 + 1）；仍存在的限制只是
  "扁平元组无法表达方向"，属 SDK 形态问题（MAIN ADR-0003 已记录），不构成声明缺口。
  > 历史：骨架期 `descriptor.events` 只列 2 个事件（`submission.accepted`、`evaluation.completed`），
  > 当时此处记为 `阻塞`；现已按 registry 的比较口径补全为 6 个。

## 5. 与 `MAIN/docs/api/events.md` §5 的偏差（如实记录，需 MAIN 裁定）

消费者实现清单（`MAIN/docs/api/events.md` §5）逐条对照：

| §5 条目 | 本模块实现 | 状态 |
|---|---|---|
| 声明 durable consumer，绑定确切 subject | `InboxConsumer`（durable = `b2-question-version-published`） | 满足 |
| inbox 表以 `event_id` 为主键，插入冲突即视为已处理 | 主键是 **`(consumer, event_id)` 复合键**，冲突同样即视为已处理（`on_conflict_do_nothing`） | **偏差**（见下） |
| 业务去重键二次校验 | **N/A（本消费者没有业务效果）**：`question.version.published` 只做版本感知/缓存失效，不落 payload、不触发评测；"二次校验"的落点是把业务效果与 inbox 行写在**同一事务**里的 `handler` 契约（失败一起回滚） | N/A（见右侧说明，不写成"满足"） |
| 未识别字段容忍 | 消费侧容忍未知字段（发布侧仍严格，见 `events.payload`） | 满足 |
| 未识别 `event_type` 记录并忽略（不静默丢弃无日志） | 记一条结构化日志（`events.inbox.unrecognized_event_type`）后忽略：ack、不写 inbox、无业务效果 | 满足（T-EVT-017） |
| 处理失败可重试；超上限进 DLQ 并记录 | `nak` 重投；JetStream 的 `max_deliver` 与 outbox 侧 DLQ 均不静默；**没传 `on_error` 钩子时也记一条结构化日志**（`events.inbox.handler_failed`），不把"有没有日志"交给调用方 | 满足（T-EVT-008/009/020） |
| 处理逻辑内不读取其他模块 schema | 只读契约 envelope schema，不读 B3/B4 的 schema | 满足 |
| 消费延迟（lag）可观测 | **未在 B2 代码内暴露**：Broker 侧运维由 B4 负责（本文件 §3 已注明 RQ-B-4-20），lag 经 JetStream 消费端指标观测 | **缺口**（见下） |
| 测试含"重复消费同一事件"用例 | T-EVT-005、T-EVT-016 | 满足 |
| 测试含"乱序到达"用例 | 未单独建用例：本消费者对 `question.version.published` 的处理与顺序无关（只做版本感知、不落 payload、不触发评测），乱序不产生业务效果 | **缺口**（见下） |

- **偏差（inbox 主键）**：契约条目写"以 `event_id` 为主键"，实现用 `(consumer, event_id)`。语义上
  后者是前者的**严格超集**：单一消费者时"复合键冲突"与"`event_id` 冲突"等价，而多消费者时前者会让
  B 消费者撞上 A 消费者的去重记录（把没处理过的事件判成"已处理"）——独立复核者用真实目录与两个消费者
  实测了这一反例。按
  [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md)
  **不顺着实现改契约、也不偷偷改实现对齐**：已提契约变更提案
  `MAIN/docs/contracts/changes/CHG-0002-inbox-primary-key-wording.md`，由 MAIN 裁定哪边是权威。
- **缺口（lag 可观测）**：B2 代码内没有消费端指标出口；MQ 运维按本文件 §3 属 B4（RQ-B-4-20），
  lag 由 JetStream 消费端指标观测。若 MAIN 判定"可观测"必须在 B2 代码内暴露，需要新工作项。
- **缺口（乱序用例）**：§5 要求"乱序到达"用例（例：先收到 `completed` 再收到 `started`）。B2 订阅的
  subject 只有一个、且语义与顺序无关，故未建；跨实体乱序假设见 §3"顺序假设"。
- **缺口（订阅端接线）**：`InboxConsumer` 目前**只作为库存在**——`src/` 内没有进程实例化它并跑拉取
  循环，工作项"谁来驱动"仍留在宿主/运维边界，**不得**读作"订阅链路已端到端运行"。
  **（2026-09-18 更正）发布侧已有驱动**：outbox 由 `autograder_b2.worker` 排空（此前该循环把派发器的
  `async dispatch` 当同步方法调用，协程被丢弃 ⇒ 事件只进不出、`published_at` 恒为空、B3 的
  `event_inbox` 与统计恒为 0）。因此现在的事实是：**发布链路通了，但前提是 worker 在运行**
  （宿主不调用它；`GET /health` 的 `message-broker` 分项会报未发布积压与 DLQ）。启动方式与重试节奏见
  [../runbooks/local-development.md](../runbooks/local-development.md) §5b，证据见
  `tests/test_worker.py` 与 `tests/test_events_outbox.py::test_dispatch_sync_can_be_called_repeatedly_on_the_same_dispatcher`。
- **容许但不可达（`security_blocked`）**：payload 门接受 `security_blocked` 作
  `evaluation.failed.failure_category`——它是**用例级**失败类别（`evaluation-report.schema.json` 的枚举
  内有它，某用例触碰沙箱即该用例的失败类别）。契约 §3.5 禁止的是**用该事件表达提交级的"安全被拒"**，
  而那条路径由 `rejected` 终态承担且 `_finish_rejected` 不发布领域事件（由源码断言钉住）。因此这是
  "容许而非可达"，不是缺陷；若 MAIN 把 §3.5 的措辞收紧到取值域层面，按新条文调整。

## 6. 与 `MAIN/docs/api/events.md` §6 生产者实现清单的逐条对照

| §6 条目 | 本模块实现 | 状态 |
|---|---|---|
| 事件与业务变更在**同一事务**写入 outbox | 写入端只在调用方事务里 `enqueue`（业务行 + outbox 行同提交） | 满足（T-EVT-015） |
| `event_id` 为 UUID v4，生成一次、永不重发时改变 | 全部 subject（含 4 个 `evaluation.*`）均走 `uuid4`：`_enqueue` 按 `CHG-0001` 的裁定（契约优先）改为每次入队现生成，幂等由 `(subject, deduplication_key)` 唯一索引与 `Nats-Msg-Id` 承担 | 满足 |
| `correlation_id` 取自入站请求；`causation_id` 为直接因果事件 | `correlation_id` 来自入站请求（API 请求标识 / 邮件检查点），经 `submissions.correlation_id` 贯穿；**`causation_id` 在所有生产调用点均为 `null`** | **偏差**（见下） |
| payload 经对应 JSON Schema 校验后才发出 | 契约里 payload 只是 `{"type": "object"}`（无 per-event payload schema），故实际校验的是 envelope schema + 本模块的结构性门 | **偏差/缺口**（见下） |
| payload 通过 §4 的禁止内容自查（自动化扫描） | 以**结构性白名单**替代事后扫描：键集合固定、取值形态固定（UUID/摘要/计数/枚举），不存在自由文本键；越界键与形态一律拒绝 | 满足（T-EVT-003/004） |
| 发布失败进入重试；**不因发布失败而回滚已成功的业务操作** | 失败留行 + `attempts += 1` + DLQ，业务事务早已提交且不被回滚 | 满足（T-EVT-006/007/015） |
| 事件大小有上限 | 契约未规定上限，本模块无显式断言；实测 envelope 为 298 B / 527 B 量级 | **缺口**（见下） |
| `producer` 只能是 `B1`\|`B2`\|`B3`\|`B4` | 三重约束：模块常量（`B2_PRODUCER`）+ envelope schema 的 `pattern` + 表 CHECK `ck_outbox_events_producer_is_b2`（实测非 B2 连插都插不进） | 满足 |

- **偏差（`event_id` 版本，✅ 已按 MAIN 裁定收口）**：4 个 `evaluation.*` 事件曾用
  `uuid5(NAMESPACE_URL, f"b2-evaluation:{dedup}")` 取确定性标识；`CHG-0001` 裁定契约（v4）优先，
  实现已改为 `uuid4`（幂等本就由 `(subject, deduplication_key)` 唯一索引与 `Nats-Msg-Id` 承担，
  不需要确定性标识）。此行的旧描述"用 uuid5"是过期残留，2026-09-18 更正。
- **偏差（`causation_id`）**：所有生产调用点都不传（恒 `null`），而 asyncapi 的 `evaluationProgress`
  官方示例给的是非空值、§6 要求"直接因果事件"。已提
  `MAIN/docs/contracts/changes/CHG-0004-causation-id-null-in-production.md`。
- **偏差/缺口（payload 无契约 schema ⇒ 必填键无契约定义）**：契约没有 per-event payload schema，
  于是"哪些键必填"没有权威定义。本模块的处理：payload 门**同时是必填键集**
  （`events.payload.REQUIRED_KEYS`），缺必填键即拒、原因点名该键、不回显取值——修复前
  `{"failure_category": "internal_error"}` 这类残缺 payload 能过门并**真被投递**（复核者实测）。
  **口径是"该键在每一次真实发布里都存在吗"**：4 个 subject 的必填集 = 准许键全集（依据 asyncapi
  官方示例的完整键集 + 无示例两个 subject 的生产调用点）；`evaluation.progress` 例外，必填集为
  `evaluation_run_id`/`sequence`/`stage`，因为 `cases_total`/`cases_done` 是**按阶段可缺**的计数
  （写入端 `_enter_stage(..., cases_total=None)` 会刻意省略；列为必填会让该形状永不投递、进 DLQ）。
  两种 progress 形状都必须过门（T-EVT-021）。契约层建议见
  `MAIN/docs/contracts/changes/CHG-0003-event-payload-required-keys.md`。
  **已接受的取舍（如实记录）**：计数改为可选后，"**本该**带计数的阶段漏了计数"不再被门拦住——那是
  语义层约束（哪些阶段必须带计数由写入端 `_enter_stage` 的调用点决定），结构性门只能挡住"恒在键缺失"。
  交付套件里的护栏：`REQUIRED_KEYS` / `PAYLOAD_KEYS` / 已发布 subject 三者必须一一对应，且
  `REQUIRED_KEYS[subject] ⊆ PAYLOAD_KEYS[subject]`（除 progress 外相等）；漏登记必填集的 subject 会被
  **fail-closed 拒绝**（不是退化成"零必填"）。
- **缺口（事件大小上限）**：见 phase 记录第 8 条（G-EVT-8）。

## 7. 事件链关联

- 使用 `correlation_id`（同一次业务操作贯穿）+ `causation_id`（直接因果），二者不可混用。
- 入口请求未携带 `traceparent`/`X-Request-ID` 时，入口生成并贯穿日志 → 事件（作为 `correlation_id`）。
