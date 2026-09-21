# CHG-0001 `evaluation.*` 事件的 `event_id` 版本：v5 实现 vs v4 要求

- 级别：**P2（语义）**——`event_id` 的生成方式语义；若 MAIN 判定必须 v4，则需要"改实现"而非改契约，
  故不是破坏性契约变更，但涉及跨模块可观测语义（同一运行的重复入队是否得到同一标识）。
- 提出人 / 日期：B2（模块侧发现，独立复核者实测确认） / 2026-09-16
- 状态：**已生效（判定：v4，改实现）** / 判定日期 2026-09-18
- **契约版本**：0.1.0（未改契约：判定为改实现）

## 动机

`MAIN/docs/api/events.md` §6（生产者实现清单）第 2 条要求"`event_id` 为 UUID v4，生成一次、永不重发时
改变"；`MAIN/docs/api/identifiers.md` 规定平台标识类型为 UUID v4（`uuid.uuid4()`）。而 B2 的实现里，
**4 个 `evaluation.*` 事件**（`started` / `progress` / `completed` / `failed`）的 `event_id` 由
`uuid5(NAMESPACE_URL, f"b2-evaluation:{deduplication_key}")` 生成（`src/autograder_b2/evaluation/orchestrator.py`），
即 **UUID v5**（确定性、可复算）；只有 `submission.accepted` 走 `uuid4`
（`src/autograder_b2/events/outbox.py`）。

已确认事实（独立复核者实测，非实现者自证）：4 个 `evaluation.*` 事件的 `uuid.UUID(...).version == 5`。

这不是"顺手改一下"：两侧都有正当理由，必须由 MAIN 判定哪边是权威——

- **契约侧（v4）**：v4 是"事件唯一标识"的通行语义；`identifiers.md` 把平台标识统一为 v4，B3 若按
  "同一 `event_id` ⇒ 同一事件"做统计，v5 会让"同一运行的第二次入队"与第一次共享标识（虽然那不常见）。
- **实现侧（v5）**：确定性标识让"同一运行重复入队"天然幂等于一行，并与 `Nats-Msg-Id` 的去重窗口
  叠加；但这层幂等**已经**由 outbox 的 `(subject, deduplication_key)` 唯一索引承担（I29/I30），
  因此实现侧的理由并不构成"必须 v5"。

## 影响的契约文件

| 文件 | 现状 | 若判 v4 需改 | 若判 v5 需改 |
|---|---|---|---|
| `MAIN/docs/api/events.md` §6 第 2 条 | 要求 v4 | 不改 | 改为"v4 或确定性 v5（同一去重键恒同）" |
| `MAIN/docs/api/identifiers.md` | 平台标识统一 v4 | 不改 | 增补事件标识的例外条款 |
| `MAIN/contracts/schemas/event-envelope.schema.json` | `event_id` 只校验 `format: uuid`（不区分版本） | 不改（可选：收紧为 v4 pattern） | 不改 |
| `MAIN/contracts/asyncapi/events.yaml` | 无版本约束 | 不改 | 不改 |

## 影响的模块与具体影响

| 模块 | 影响 |
|---|---|
| **B2（发布方）** | 若判 v4：把 `uuid5` 换成 `uuid4`（一处），幂等仍由 outbox 唯一索引 + `Nats-Msg-Id` 承担；需重跑事件用例（T-EVT-001..020）与迁移无关。若判 v5：B2 只需在 `docs/api/events.md` 登记，无代码改动。 |
| **B3（消费方）** | 只按 `event_id` 去重（inbox 语义），两种版本都不影响其正确性；若 B3 曾依赖"v4 ⇒ 每次发布都是新标识"，需确认。 |
| **B1 / B4** | 不消费这 4 个事件，无影响。 |

## 是否破坏兼容

否。UUID 的版本位不影响任何消费方按 `event_id` 去重；无论裁定哪边，都不需要一次性迁移或双写。

## 示例载荷变更

```diff
- "event_id": "9f1c2c0e-...-5b0d7e2c4a11"   # v5：同一 evaluation_run_id + dedup key 恒同
+ "event_id": "3b7d1f04-...-8c2e5a90d7f3"   # v4：每次入队新生成
```

## 回滚方式

改实现方向（v4）是一处函数替换，回滚即换回 `uuid5`；改文档方向（v5）回滚即恢复条文。两者都不动
数据、不动迁移、不动 `contracts/**` 内容（因此不涉及摘要重算，除非 MAIN 选择收紧 schema）。

---

## MAIN 判定（2026-09-18）：**判 v4 —— 改实现**

**理由（按权重）。**

1. **契约是权威，且这条规则有实质内容。** `docs/api/identifiers.md` 把平台标识统一为 UUID v4，不是
   风格偏好：v4 的语义正是"每次生成都是新的唯一标识"。`event_id` 若为 v5，"同一 `event_id`"就不再
   蕴含"同一事件实例"，消费方无法从标识本身判断两次投递是不是同一个事件——这个歧义会一直留在
   契约里。
2. **实现侧的理由不成立。** 提案自己已经写明：重放幂等**已经**由 outbox 的
   `(subject, deduplication_key)` 唯一索引与 `Nats-Msg-Id` 去重窗口承担。确定性 `event_id` 是在
   已有更强机制之上再加一层同义保障，代价是上面那条语义歧义。
3. **不对称性。** 判 v4 只需 B2 改一处函数；判 v5 要在 `identifiers.md` 里为事件标识开一个例外条款，
   并向所有未来消费方解释"为什么这个平台的标识有时不是 v4"。同等的正确性下，选不需要例外的那个。

**已实施的改动（B2 侧，一处代码 + 两处注释）。**

- `src/autograder_b2/evaluation/orchestrator.py`：`uuid5(NAMESPACE_URL, f"b2-evaluation:{dedup}")`
  → `uuid4()`；docstring 写明"重放幂等由 outbox 唯一索引承担，与标识生成方式无关"。
- `src/autograder_b2/events/dispatcher.py`：两处历史注释里"排序退化成 uuid5 的哈希"改为"退化成随机
  标识"——定序仍然只依赖 `insert_seq`，结论未变，但不再引用已不存在的实现细节。

**契约文件改动：无。** `docs/api/events.md` §6 第 2 条本来就要求 v4，此前只是实现没跟上；
`contracts/**` 未改，摘要未变。

**验证（真实执行）。**

```text
uv run ruff check .                                   → All checks passed!
uv run pytest -q tests/test_events_outbox.py tests/test_events_probe_review.py \
                 tests/test_evaluation_pipeline.py     → 83 passed in 63.69s
```

集成环境的**实测对照**（同一个库，改前 / 改后）：

```text
-- 改前（B2 worker 旧进程）
autograder.v1.evaluation.failed      | 7ae52728-e260-5a98-98fa-9ceeaec76d4b | 5
autograder.v1.submission.accepted    | dff36b9d-572e-4c3f-8250-c89afc90052b | 4
-- 重启 worker（载入 v4 实现）后提交一次
autograder.v1.evaluation.failed      | 31b64dbb-6e10-4a39-b993-170ed98079f2 | 4
autograder.v1.submission.accepted    | a792c341-330c-464f-8590-702bb33c2164 | 4
```

（`substr(event_id::text, 15, 1)` 即 UUID 的版本半字节。）

**消费方影响：无。** B3 只按 `event_id` 去重，两种版本都不影响其正确性；B1/B4 不消费这 4 个事件。
不需要一次性迁移，不需要双写，不需要兼容层。
