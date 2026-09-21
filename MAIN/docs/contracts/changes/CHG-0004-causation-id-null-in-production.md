# CHG-0004 `causation_id` 在生产路径恒为 `null`（与 §6 及 asyncapi 示例不一致）

- 级别：**P2（语义）**——"直接因果事件"该填什么、是否必须填，属字段语义。
- 提出人 / 日期：B2 / 2026-09-16
- 状态：**已生效（判定：可为 null，改条文）** / 判定日期 2026-09-18
- **契约版本**：0.1.0（未改契约：判定为改条文）

## 动机

`MAIN/docs/api/events.md` §6 第 3 条要求："`correlation_id` 取自入站请求；**`causation_id` 为直接因果
事件**"。`MAIN/contracts/asyncapi/events.yaml` 的 `evaluationProgress` 官方示例给的是**非空**
`causation_id`（指向一个 UUID），`submissionAccepted` / `evaluationCompleted` 示例则是 `null`。

已确认事实（独立复核者核对源码，非实现者自证）：B2 的**所有**生产调用点都不传 `causation_id`
（`src/autograder_b2/evaluation/orchestrator.py`、`src/autograder_b2/events/outbox.py`），因此 5 个发布
事件的 `causation_id` 恒为 `null`——包括与官方示例形态不同的 `evaluation.progress`。

两种读法都能自圆其说，所以必须由 MAIN 判定：

1. **"必须填"**：`evaluation.progress`/`completed`/`failed` 的直接因果是同一运行的 `evaluation.started`，
   `causation_id` 应指向那条事件的 `event_id`（可观测性更好：一条链能从 started 顺下来）。
2. **"可为空"**：B2 的因果链已由 `correlation_id`（同一次业务操作）+ `evaluation_run_id` +
   outbox 的 `deduplication_key` 完整表达；`causation_id` 对 B2 的事件无额外信息量，
   asyncapi 示例只是"示意可填"。

## 影响的契约文件

| 文件 | 现状 | 若判"必须填"需改 | 若判"可为空"需改 |
|---|---|---|---|
| `MAIN/docs/api/events.md` §6 第 3 条 | "causation_id 为直接因果事件" | 不改（B2 改实现） | 明确"可为 null，null 表示无直接因果事件" |
| `MAIN/contracts/asyncapi/events.yaml` | `evaluationProgress` 示例非空 | 不改 | 建议把示例改为 `null` 或补注"示例仅示意" |
| `MAIN/contracts/schemas/event-envelope.schema.json` | `causation_id` 允许 `null` | 不改 | 不改 |

## 影响的模块与具体影响

| 模块 | 影响 |
|---|---|
| **B2（发布方）** | 若判"必须填"：需要在 `_enqueue` 时把同一运行 `evaluation.started` 那行的 `event_id` 作为 `causation_id` 传入（同事务查询 outbox 即可），并加"因果链可顺查"的用例；同时与本 CHG-0001（`event_id` 版本）耦合——若改成 v4，"started 的 event_id"不再可复算，必须真的从库里取。 |
| **B3（消费方）** | 若判"必须填"，B3 可据此构建"运行 → 事件"的因果链；它目前的去重不依赖该字段。 |
| **B4** | 不涉及。 |

## 是否破坏兼容

否。`causation_id` 在 schema 里本就允许 `null`；无论裁定哪边，都不影响消费方按 `event_id` 去重。

## 示例载荷变更

```diff
  "event_type": "autograder.v1.evaluation.progress",
- "causation_id": null,                    // B2 生产现状（与官方示例不同）
+ "causation_id": "<evaluation.started 的 event_id>",   // 若判"必须填"
```

## 回滚方式

改实现方向：删掉传入的 `causation_id` 即回到现状；改文档方向：恢复措辞。两者都不涉及数据迁移。

## 备注

B2 已把该差异登记在 `B2/docs/api/events.md` §6（生产者清单逐条对照表）与
`B2/docs/phases/active/phase-01-implementation.md`（G-EVT-4），不会在裁定前静默对齐。

---

## MAIN 判定（2026-09-18）：**判"可为 null" —— 改条文**

**理由。**

1. **不制造冗余状态。** B2 事件的因果链已经由三样东西完整表达：`correlation_id`（同一次业务操作）、
   payload 里的 `evaluation_run_id`（同一个运行）、outbox 的 `deduplication_key`（同一逻辑事件）。
   再加一个 `causation_id` 指向同一运行的 `evaluation.started` 的 `event_id`，是第四份同义信息，
   而它的代价不小：见第 2 条。
2. **与 CHG-0001 的交互。** 判"必须填"就要求写入端在入队 `progress`/`completed`/`failed` 时**回查**
   同一运行 `started` 那一行的 `event_id` 并写进来。在 CHG-0001 判 v4（每次生成都是新标识）之后，
   这个值**不可复算**，只能真的从库里取——把一个纯写入路径变成"写前先读自己刚写的那行"，
   为的是填一个已经有三个等价物的字段。两项判定合起来看，"必须填"明显不划算。
3. **契约条文与实际语义对齐。** `event-envelope.schema.json` 本就允许 `causation_id` 为 `null`，
   官方示例里 `submissionAccepted` / `evaluationCompleted` 也已经是 `null`：真正不一致的只有
   `docs/api/events.md` §6 第 3 条那句"为直接因果事件"（读起来像必填），以及
   `evaluationProgress` 那一份**示意性**示例。改这两处即可，不动 schema。

**已实施的改动（文档）。**

- `MAIN/docs/api/events.md` §2「核心约定」表的 `关联` 一行：明确
  "`causation_id` = 直接因果事件；**无直接因果事件时为 `null`**"。
- `MAIN/contracts/asyncapi/events.yaml`：`evaluationProgress` 官方示例的 `causation_id` 由非空 UUID
  改为 `null`，并加注"示例只示意字段形态；B2 的生产路径不填 `causation_id`，因果链由
  `correlation_id` + `evaluation_run_id` 表达"。

**契约摘要已因 asyncapi 示例改动而变化** → 四份 `contracts.lock` 已由
`scripts/relock_contracts.py` 重算（新摘要见仓库根 `contracts.lock`，不在此复制字面值）。

**模块侧改动：无。** B2 现状即为正确形态；B3 的去重不依赖该字段。空值语义现在有明文，因此
"`causation_id` 为 null"不再是"实现没跟上契约"，而是契约规定的取值之一。
