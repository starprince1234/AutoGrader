# B4 消息中间件运维手册（JetStream stream/consumer/DLQ/配额）

> ✅ 本手册的拓扑已由 `python -m autograder_b4.ops.messaging provision` 在真实 NATS 2.10 JetStream
> （宿主端口 `54222`）创建，停机/恢复演练已执行；命令与结果见
> `../phases/active/phase-01-platform-implementation.md`。

## 0. 前置

- NATS 经 compose 运行：`docker compose up -d nats`（在 `../../MAIN` 目录）；宿主端口 `54222`、监控 `8222`。
- 事件契约权威：`../../MAIN/contracts/asyncapi/events.yaml`；B4 仅发布
  `autograder.v1.roster.import.completed`，业务订阅为空。

## 1. 拓扑供给

```powershell
cd ../B4
uv run python -m autograder_b4.ops.messaging provision
uv run python -m autograder_b4.ops.messaging health
```

实测输出：

```text
consumer:B4_DLQ/B4_DLQ_CONSUMER: created
consumer:ROSTER_IMPORT/B4_ROSTER_CONSUMER: created
stream:B4_DLQ: created
stream:ROSTER_IMPORT: created
```

供给是**幂等**的：已存在的 stream/consumer 在配置一致时返回 `unchanged`，不一致时 `updated`。

| 对象 | 配置 |
|---|---|
| stream `DOMAIN_EVENTS` | 契约里除 `roster.import.completed` 外的**全部** subject（`submission.accepted`、`evaluation.started`、`evaluation.progress`、`evaluation.completed`、`evaluation.failed`、`question.version.published`）；retention `limits`；discard `old`；storage `file`；`max_bytes=201326592`（192 MB）；`allow_direct=true`；**无 B4 consumer** |
| stream `ROSTER_IMPORT` | subjects `["autograder.v1.roster.import.completed"]`；`max_bytes=33554432`（32 MB） |
| durable `B4_ROSTER_CONSUMER` | ack `explicit`；deliver `all`；`filter_subject` = 上述 subject；`max_deliver=5`；`ack_wait=30s`；`max_ack_pending=64` |
| stream `B4_DLQ` | subjects `["$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.>", "$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.>"]`；`max_bytes=33554432`（32 MB） |
| durable `B4_DLQ_CONSUMER` | filter 同时覆盖上述两个前缀（限定到 `ROSTER_IMPORT`/`B4_ROSTER_CONSUMER`）；`max_deliver=1` |

> `201326592 + 33554432 + 33554432 = 268435456` = 0.25 GB，正是
> `../../MAIN/docs/architecture/resource-budget.md` §3 的 JetStream 账本行。**账本行约束的是本模块全部
> JetStream 存储之和**：此前声明的 256 MB + 32 MB = 288 MB 已经超出该行。
>
> 分配依据：共享域事件流拿大头（192 MB）——它承载**六个** subject，是各模块之间唯一的事件通道，某个
> 消费者落后时不能开始丢别人的事件；`ROSTER_IMPORT` 只承载 B4 自己的一个 subject、且 durable 会及时把
> 事件落进 `b4.inbox_events`，小缓冲足够（32 MB）；DLQ 保持 32 MB（advisory 每条数百字节）。
> `ROSTER_IMPORT_MAX_BYTES` 由账本行减去另外两条**推导**，三者不可能各自漂移。
>
> 为什么共享流不用 `autograder.v1.>`：**一个 subject 只能绑定一条 stream**，通配符会与
> `ROSTER_IMPORT` 已持有的 subject 冲突。共享流因此枚举契约里其余的地址，并由
> `tests/test_messaging.py::test_the_shared_domain_stream_binds_every_contract_subject_exactly_once`
> 读取 `MAIN/contracts/asyncapi/events.yaml` 断言"每个地址恰好绑定一次"。
>
> 这条 stream 缺失时的现象（2026-09-19 平台实测）：B2 的 outbox 15 行全部
> `NoStreamResponseError: nats: no response from stream`、`published_at` 恒为空、B3 的 `event_inbox` 恒为 0。
> 供给后同样 15 行一次性投递成功（见 `../phases/active/phase-03-ops-lint-and-ledger.md`）。
>
> **供给会更新既有 stream 的配置**：`provision` 是声明式 reconcile（`ensure_stream` 比较 subjects/
> `max_bytes`/retention），因此把 `ROSTER_IMPORT` 的配额从 256 MB 改成 32 MB 会在下一次 `provision` 时同步到
> broker。这是有意的：声明是唯一事实源，broker 必须被收敛到声明上。

## 2. 投递与记账

```powershell
# 把 outbox 中的待发事件发出去（可反复执行；已发布的行不会被重发）
uv run python -m autograder_b4.ops.messaging relay
# 消费一批已投递事件（inbox 去重；重复投递只产生一次业务效果）
uv run python -m autograder_b4.ops.messaging consume --rounds 3
```

- **at-least-once**：`ack explicit`；消费方按 `(consumer, event_id)` 唯一键去重
  （`b4.inbox_events`）。
- **outbox**：事件与业务变更**同一事务**写入 `b4.outbox_events`；relay 逐行发布并记录
  `attempts`/`last_error`。发布失败时行保留、`published_at` 保持 NULL。
- `relay` 与 `consume` 是**独立进程**，与请求服务进程分离——同步 API 不等待 broker。

## 3. DLQ：查询与重放

死信来自 JetStream 自身的 advisory（不自造 `…dlq` domain subject）：

| advisory subject | 含义 |
|---|---|
| `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<stream>.<consumer>` | broker 在 `max_deliver` 耗尽后停止重投（**实测确认本环境实际发出的是这个**） |
| `$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.<stream>.<consumer>` | 消费方主动 `term()` |

```powershell
# 查询（含已重放的）
uv run python -m autograder_b4.ops.messaging dlq list
# 重放
uv run python -m autograder_b4.ops.messaging dlq replay <dlq_id>
```

输出示例：

```text
<dlq_id> pending stream=ROSTER_IMPORT seq=4 deliveries=5 reason=max_deliver exhausted after 5 attempts
```

重放语义：

- 若 `b4.dlq_messages.envelope` 有保存的信封（消费方主动登记的死信）→ 直接重发该信封；
- 否则按 `original_sequence` 从 stream 取回原始消息重发；
- 原始消息已被保留策略淘汰 → **明确失败**（`"… is no longer retained; replay is impossible"`），
  不假装重试成功；
- 成功后写 `replayed_at` 与 `replay_attempts`，`dlq list` 默认不再列出它。

## 4. 配额、实测占用与保留策略

```powershell
uv run python -m autograder_b4.ops.messaging metrics
```

输出（示例，平台 broker 上 15 条真实待发事件投递后）：

```text
ROSTER_IMPORT: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
  consumer ROSTER_IMPORT/B4_ROSTER_CONSUMER: pending=0 ack_pending=0 redelivered=0
DOMAIN_EVENTS: stored=9685 bytes messages=15 quota=201326592 ledger_share=0.00%
B4_DLQ: stored=0 bytes messages=0 quota=33554432 ledger_share=0.00%
  consumer B4_DLQ/B4_DLQ_CONSUMER: pending=0 ack_pending=0 redelivered=0
total stored=9685 bytes of the 268435456 byte ledger line (0.00%)
```

`metrics` 读的是 broker 自报的 `state.bytes` / `state.messages` 与每个 durable 的 `num_pending` /
`num_ack_pending`（不是 B4 声明的配额）：**"占用计入账本"必须由测量支撑，声明值只是上限**。stream
缺失或实测总量超过账本行时命令以非零退出。集成测试用同一函数断言"relay 之后 `stored_bytes > 0`
且不超过配额"（`tests/test_messaging.py::test_jetstream_storage_is_measured_not_assumed`）。

| 项 | 值 | 依据 |
|---|---|---|
| JetStream 持久（`DOMAIN_EVENTS`） | ≤ 201326592 B（192 MB） | `max_bytes`，共享事件通道 |
| JetStream 持久（`ROSTER_IMPORT`） | ≤ 33554432 B（32 MB） | `max_bytes`，单 subject 的投递缓冲 |
| JetStream 持久（`B4_DLQ`） | ≤ 33554432 B（32 MB） | 同上，advisory 量小 |
| JetStream 持久（三条合计） | ≤ 268435456 B（0.25 GB） | 资源账本 §3 的整行 |
| 保留 | `limits` + `discard=old` | 按配额淘汰最旧消息 |
| NATS 内存/CPU | ≤ 32 MB / 0.05 vCPU | compose 的 `mem_limit`/`cpus` |

> 状态说明：**配额已在集成测试中收紧为账本行本身**（此前断言放行到 0.5 GB，且允许某条 stream 为 0）；
> **实测占用**由 `metrics` 一次性读取并留档（见 `../phases/active/phase-03-ops-lint-and-ledger.md`）。
> 长时间采样（保留策略在真实流量下的淘汰行为）仍未做，属平台级资源门禁范围
> （`../../MAIN/docs/architecture/resource-budget.md` §5 规定方法，本模块不宣称）。

### 4.1 配额巡检（DLQ 长期堆积）

1. `metrics` 看两条 stream 的 `stored_bytes` 与 `B4_DLQ` 的 `pending`；
2. `pending > 0` 持续增长 → `dlq list` 查原因（`reason` 字段），按 §3 重放或按业务处置；
3. `B4_DLQ` 接近 `max_bytes` 说明死信没有被处置：`discard=old` 会淘汰最旧的死信，因此**堆积本身就是
   需要人工介入的信号**，不可当作"自动清理"。

## 5. NATS 故障下的行为（实测）

```powershell
uv run python -m autograder_b4.ops.outage_drill    # 真实 stop/start NATS 容器
```

实测结论（2026-09-16）：

| 检查 | 结果 |
|---|---|
| broker 停止后 readiness 点名 `nats` | **PASS**（`nats is unreachable at localhost:54222 (ConnectionResetError)`） |
| 停止期间同步路径仍可写 outbox（事件不丢） | **PASS**（pending 1 → 2） |
| 停止期间 relay 失败但保留待发行 | **PASS**（`NoServersError`，remaining=2） |
| 恢复后 backlog 清空、健康恢复 | **PASS**（published=2，remaining=0） |

演练脚本在 `finally` 中重新启动 broker，中断不会把 broker 留在停止状态。

## 6. 待办 / 缺口

- **长时间**占用采样（保留策略在真实流量下的淘汰行为）未做；一次性实测见 §4 与
  `../phases/active/phase-03-ops-lint-and-ledger.md`。
- DLQ 的人工处置（修复后选择 `replay` 还是丢弃）尚无操作细则；当前只提供重放（§4.1 给出巡查流程）。
- 告警阈值（consumer lag、DLQ 非空）未定义——属部署阶段。
