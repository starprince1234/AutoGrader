# CHG-0003 每个事件 payload 的必填键（当前契约里 payload 无 schema）

- 级别：**P1（增补）**——新增"每事件 payload 的结构"（不删不改已有字段；若 MAIN 选择只写文档则是 P4）。
- 提出人 / 日期：B2 / 2026-09-16
- 状态：**已采纳，实施排期在下一轮契约变更**（判定日期 2026-09-18）
- **契约版本**：0.1.0（未改；实施时升 MINOR）

## 动机

`MAIN/contracts/asyncapi/events.yaml` 的每条 message **只 `$ref` envelope**，
`MAIN/contracts/schemas/event-envelope.schema.json` 里 `payload` 只是 `{"type": "object"}`。
于是契约层面**没有** per-event payload 的结构定义，"哪些键必填、取值形态是什么"没有权威来源。

已确认事实（独立复核者实测，非实现者自证）：修复前，B2 的 payload 门只做"白名单"，
`{"failure_category": "internal_error"}` 这样的**残缺 payload（只剩 1 个键）能过门并真被投递**
（真实 broker 流内可见、体积 298 B、仍通过 envelope schema）。后果不是安全事件，而是**静默的功能失配**：
写入端漏一个键不会有任何告警，而 B3 的统计按 `evaluation_run_id` 等键聚合时拿到的是缺字段事件。

B2 侧已自行加固（见下"B2 侧已做的处理"），但"必填键"目前只有 B2 一家之言——契约是唯一权威，
因此提 CHG 请 MAIN 决定是否把每事件 payload 的结构写进契约（并同步官方示例）。

## 影响的契约文件

| 文件 | 现状 | 建议 |
|---|---|---|
| `MAIN/contracts/asyncapi/events.yaml` | 每条 message 只 ref envelope；`submissionAccepted`/`evaluationProgress`/`evaluationCompleted` 有示例，`evaluationStarted`/`evaluationFailed` **无示例** | 为 5 个 B2 发布事件各写 `payload` 的键集合与形态（或至少给 `evaluationStarted`/`evaluationFailed` 补官方示例） |
| `MAIN/contracts/schemas/*.json` | 无 per-event payload schema | 可选：新增 `event-payloads.schema.json`（每 subject 一段 `properties`/`required`），并让 envelope 的 `payload` 用 `oneOf`/按 `event_type` 分派（若 MAIN 认为成本可接受） |
| `MAIN/contracts/examples/*.json` | 只有 `evaluation.completed` 一例 | 补其余 4 个事件的官方示例（并纳入 `checks.py` 的示例校验） |

## 影响的模块与具体影响

| 模块 | 影响 |
|---|---|
| **B2（发布方）** | 无破坏性改动：已按"准许键集 = 必填键集"实现（`events.payload.REQUIRED_KEYS`），键集取自 asyncapi 官方示例的完整键集 + 无示例两个 subject 的生产调用点。若 MAIN 定义了 payload schema，B2 改为按 schema 校验（多一层 schema 校验，白名单门保留为结构性禁止）。 |
| **B3（消费方）** | 从"契约只保证 payload 是 object"变成"保证这些键存在"⇒ 统计实现可去掉防御性 `None` 分支（或至少可断言）。 |
| **B4** | 不涉及（B4 发布的是 `roster.import.completed`，其示例已在 asyncapi 内）。 |

## 是否破坏兼容

否。为 payload 增加**必填约束**在契约层是"语义收紧"：已按官方示例实现的发布方不受影响（B2 的五个生产
payload 逐键核对过）；但若 MAIN 决定把约束写入 `contracts/**`，需要**版本递增**（至少 MINOR）并按
协议重算四份 `contracts.lock`。

## 示例载荷变更

```diff
  "payload": {
    "evaluation_run_id": "…",
    "stage": "dynamic_execution",
    "sequence": 7,
    "cases_total": 5,
    "cases_done": 2
-   // 目前契约不禁止删掉其中任意一个键后照发
  }
```

## 回滚方式

若 MAIN 判定"payload 结构不入契约"，则本 CHG 关闭；B2 侧已有的必填校验作为模块内部加固保留
（它只约束 B2 自己的写入端，不影响任何消费方）。

## B2 侧已做的处理（不改契约、不掩盖缺口）

`REQUIRED_KEYS` = 该 subject 的准许键全集，缺任一键即拒；拒绝原因**点名缺失键、不回显取值**。
用例：`tests/test_events_outbox.py`（T-EVT-003/004）、`tests/test_events_probe_review.py`
（`test_review_a_payload_whitelist_has_teeth` 逐 subject × 逐键各缺一次，全部必须被拒）。

---

## MAIN 判定（2026-09-18）：**采纳 —— 每事件 payload 的必填键写入契约**

**理由。** `payload: {"type": "object"}` 等于没有契约：消费方拿到的结构保证是"它是一个对象"。
复核者实测的后果是**静默的功能失配**——`{"failure_category": "internal_error"}` 这种只剩一个键的
payload 能过门并真被投递，写入端漏键无告警，而按 `evaluation_run_id` 聚合的统计拿到的是缺字段事件。
这类错误不会在任何一个模块的测试里暴露，因为在补齐契约之前"缺键"并不违规。

**与 CHG-0001 的区别**：CHG-0001 判"改实现"，因为它要求实现去符合一条**已经存在**的规则；本项是
契约本身**没有**这条规则，因此必须写进契约（并因此在契约层是语义收紧：发布方需要满足更强约束）。

**实施计划（下一轮契约变更，尚未执行——此处如实记录，不声称已完成）。**

1. 新增 `contracts/schemas/event-payloads.schema.json`：每个 subject 一段
   `properties` + `required`，键集取 asyncapi 官方示例的完整键集（无示例的两个 subject 取生产
   调用点的键集）。取值形态按现有示例收紧（`sequence` 为非负整数、`stage` 为枚举、
   `score`/`max_score` 为 number 等）。
2. `contracts/schemas/event-envelope.schema.json` 的 `payload` 由 `{"type":"object"}` 改为按
   `event_type` 分派（`if/then` 或 `oneOf` + `const`）；保持"未识别字段容忍"（`additionalProperties`
   不为 false），因为前向兼容是 §5 清单的既有要求。
3. `contracts/examples/` 补 `evaluation.started` 与 `evaluation.failed` 两份官方示例（当前各只有
   1 份 `evaluation.completed`），并纳入 `checks.py` 的示例实例校验。
4. 四份 `contracts.lock` 由 `scripts/relock_contracts.py` 重算；`contract_version` 递增 MINOR。
5. B2 侧把 `REQUIRED_KEYS` 白名单门保留为结构性禁止，并**增加**一层按 schema 的校验（两层都要，
   白名单挡住 schema 之外的键，schema 保证必填键与形态）。B3 可据此去掉防御性 `None` 分支。

**在实施完成前，B2 的 `REQUIRED_KEYS` 是唯一的必填键来源——这是模块内部加固，不是契约保证。**
本项未完成前，任何"payload 结构由契约保证"的表述都是不成立的。
