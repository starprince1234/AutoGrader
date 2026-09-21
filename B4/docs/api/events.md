# B4 事件（我发布 / 我订阅）

> 事件契约权威：`../../MAIN/contracts/asyncapi/events.yaml`；envelope 权威：`../../MAIN/contracts/schemas/event-envelope.schema.json`。本文件只写关系与语义摘要，不抄字段。

## 我发布的事件

| subject | 触发时机 | payload 语义摘要 | 是否含敏感数据 | 状态 |
|---|---|---|---|---|
| `autograder.v1.roster.import.completed` | 名单导入确认且**原子写入成功后**（状态机 `committed`） | 只携带 `roster_import_id`、`course_id`、`section_id` 与 `accepted_rows`/`rejected_rows` 计数 | **否**（不含 PII） | `已完成` |

- 数量：**1 个**（与 `module.yaml: events.publishes` 一致）。
- **发布路径**：写入 `b4.outbox_events`（与业务变更**同一事务**）→ 由 outbox 中继
  （`python -m autograder_b4.ops.messaging relay`）异步发布。请求路径**不**等待 broker。
- **PII 断言**：`tests/test_roster_import.py::test_confirmation_queues_exactly_one_pii_free_event`
  断言 payload 恰好等于上述键集合，且姓名/学号不出现在序列化结果中。

## 我订阅的事件

- **业务订阅：无**（`module.yaml: events.subscribes = []`）。
- **控制面消费者（非业务订阅）**：B4 运营平台的消息中间件，因此自己运行一个 durable consumer
  `B4_ROSTER_CONSUMER`（`max_deliver=5`、explicit ack）用于**投递记账 / DLQ / 重放**。它不产生业务
  副作用，也不改变契约：`module.yaml` 的 `subscribes` 保持空。
  `tests/test_messaging.py::test_the_consumer_never_subscribes_to_an_undeclared_subject` 断言这一点。
- 未来如需业务订阅（例如去标识化统计），必须先走
  `../../MAIN/docs/contracts/change-protocol.md` 增补（P1），不得私加 subject。

## 幂等与顺序假设

- **投递语义**：at-least-once（envelope 声明）。
- **去重键**：`event_id`（UUID，发布方生成）；消费方以 `b4.inbox_events (consumer, event_id)` 唯一键
  去重，重复投递不产生重复业务效果。实测见
  `tests/test_messaging.py::test_a_duplicate_delivery_produces_one_business_effect`。
- **顺序**：不对跨 subject 顺序作假设；单 subject 内的因果用 `causation_id` 表达。
- **发布一致性**：outbox 与业务写入同事务提交——业务回滚则事件不存在（
  `tests/test_roster_import.py::test_the_event_is_queued_in_the_same_transaction_as_the_rows`）。

## JetStream 拓扑（实测）

B4 拥有**拓扑**（`RQ-B-4-20`：stream/consumer 创建），各模块拥有自己的 subject、consumer 与 payload。

| stream | subjects | 配额 | consumer |
|---|---|---|---|
| `ROSTER_IMPORT` | `autograder.v1.roster.import.completed` | 33554432（32 MB） | `B4_ROSTER_CONSUMER`（投递记账/DLQ/重放的控制面） |
| `DOMAIN_EVENTS` | 契约里**其余全部** subject：`submission.accepted`、`evaluation.started`、`evaluation.progress`、`evaluation.completed`、`evaluation.failed`、`question.version.published` | 201326592（192 MB） | **无**（B4 不替别的模块决定消费语义；各模块自建 durable） |
| `B4_DLQ` | `$JS.EVENT.ADVISORY.CONSUMER.{MAX_DELIVERIES,MSG_TERMINATED}.>` | 33554432（32 MB） | `B4_DLQ_CONSUMER` |

- 为什么不是一条 `autograder.v1.>`：**一个 subject 只能绑定一条 stream**，而 `ROSTER_IMPORT` 已持有
  `autograder.v1.roster.import.completed`。因此共享 stream 显式枚举契约里其余的地址，而不是用通配符
  去撞已有的绑定。
- `tests/test_messaging.py::test_the_shared_domain_stream_binds_every_contract_subject_exactly_once`
  **读取** `MAIN/contracts/asyncapi/events.yaml` 的 `address` 列表并断言：契约里的每个地址恰好被绑定一次、
  没有重复绑定、除 JetStream 自身 advisory 命名空间外不出现契约之外的 subject。契约新增事件而这里没跟上
  时，套件失败，而不是等到发布时收到 `no response from stream`——**这正是这条 stream 缺失时的现象**：
  平台只有 `ROSTER_IMPORT`/`B4_DLQ`，任何模块都发不出事件（B2 的 outbox 15 行全部
  `NoStreamResponseError`，`published_at` 恒为空）。
- 保留/存储：三条 stream 都是 `limits` + `discard=old` + `storage=file`，配额之和 = 资源账本 §3 的
  0.25 GB 整行（见 `../runbooks/messaging-operations.md` §4）。
- 实测占用用 `python -m autograder_b4.ops.messaging metrics` 读取（`state.bytes`/`num_pending`），
  而不是复述上面的声明值。
- DLQ 目前只收集 **B4 自己那条 consumer** 的 advisory（`$JS.EVENT.ADVISORY...ROSTER_IMPORT.B4_ROSTER_CONSUMER`）；
  其他模块 consumer 的死信 advisories 不在 B4 的死信库里，属各自模块的运行期维护范围。

## DLQ 与重放

- **死信来源**：JetStream 自身的 advisory，而不是自造的 `…dlq` subject：
  - `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<stream>.<consumer>` —— broker 在 `max_deliver`
    耗尽后停止重投（**实测确认这是本环境实际发出的 subject**）；
  - `$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.<stream>.<consumer>` —— 消费方主动 `term()`。
- **DLQ stream**：`B4_DLQ` 订阅上述两个前缀，`B4_DLQ_CONSUMER` 把 advisory 落成可查询的
  `b4.dlq_messages` 行（`original_stream`、`original_sequence`、`deliveries`、reason）。
- **重放**：优先用 `dlq_messages.envelope` 里保存的原始信封重发；advisory 来源的行则按
  `original_sequence` 从 stream 取回原始消息重发。原始消息已被保留策略淘汰时**明确失败**
  （`"no longer retained"`），不假装重试成功。
- **运维命令**：`python -m autograder_b4.ops.messaging dlq list` / `dlq replay <dlq_id>`。

## 已知缺口

- `descriptor.events` 是**扁平元组**且只列 1 个事件（`("autograder.v1.roster.import.completed",)`），
  **不区分** `publishes`/`subscribes`；这是 SDK 缺口（ADR-0003 遗留风险）。因此注册表对事件做的是
  "集合包含"校验：descriptor 不得声明 manifest 未声明的事件，也不得漏掉 manifest 声明的事件；方向
  仍以 `module.yaml` 为准。修复需 SDK 破坏性变更（`events` → `publishes`/`subscribes`）并同步四模块
  与 manifest schema。
