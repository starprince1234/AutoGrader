# B1 事件（发布 / 订阅）

事件契约权威：`../../../MAIN/contracts/asyncapi/events.yaml`。统一 envelope、at-least-once、按 `event_id` 去重（见 `../../../MAIN/docs/api/governance.md`）。本文件只写关系与语义摘要，不写字段表。

## 我发布的事件

`module.yaml` 声明 `events.publishes: []` —— **B1 不发布任何事件**。B1 是静态客户端，没有服务端进程可发布事件。

## 我订阅的事件

`module.yaml` 声明 `events.subscribes`：

- `autograder.v1.evaluation.progress`
- `autograder.v1.evaluation.completed`

**消费语义**：

- 语义摘要：评测进度（状态迁移/百分比）与评测完成（最终状态），用于驱动前端进度面板与成绩刷新。
- **运行时呈现路径**：B1 是静态客户端，不直接持有 NATS 订阅；这两个事件经 B2 的 SSE 端点（`GET /api/v1/evaluation-runs/{id}/events`）以去标识化形式呈现给浏览器。B2 保证 SSE 载荷与事件去标识化语义一致（`../../../MAIN/docs/workstreams/B1.md` 第 4 节）。
- **去重键**：`event_id`（统一 envelope）；SSE 场景按事件序号/`event_id` 去重。
- **断线重连**：从断点续传，不重复展示历史进度（RQ-B-1-50-5）。
- **不含敏感数据**：B1 消费的任何事件/SSE 载荷都**不得**包含源码、参考答案、隐藏用例、学号之外的 PII（这是契约的脱敏边界，B1 依赖它，也必须在 UI 层二次校验不渲染）。

## 幂等与顺序假设

- 事件投递 at-least-once：B1 侧（经 SSE）必须幂等消费，重复 `event_id` 直接丢弃。
- 进度事件顺序：按 `event_id`/序号单调；乱序到达时 B1 以序号为准重排或丢弃过期事件。
- 完成事件是终态：收到 `completed` 后，同 `evaluation_run_id` 的后续 progress 视为过期丢弃。

## 待确认项

1. ~~**SSE 事件类型表未成文**~~ → **已解决**：SSE 帧类型已在 `MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame` 中冻结（`event` ∈ `progress`/`completed`/`rejected`/`failed`）。注意 SSE 属于 HTTP 契约，因此定义在 OpenAPI 而非 `asyncapi/events.yaml`——**不要**去 AsyncAPI 里找它。`WP-B1-B` 可以开工。
2. ~~**终态覆盖缺口**~~ → **已解决**：B1 的全部终态经 **SSE 流**到达，不需要为 `rejected` 增加事件订阅。`rejected` 刻意**没有**领域事件，因为它只对提交会话有意义（契约中已就此写明设计含义）。B1 的 `module.yaml` 的 `subscribes` 声明描述的是它逻辑上依赖的系统事件，与 SSE 呈现是两条不同通道。
3. **方向校验缺口（仍未解决）**：ADR-0003 已记录 host 目前无法校验事件发布/订阅方向，B1 的 `subscribes` 声明暂不参与方向校验。这不是 B1 可自行修复的项。
