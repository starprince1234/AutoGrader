# B2 故障处置手册（Troubleshooting）

> 只写**真实遇到过或可确定**的问题。B2 已实现（不再是骨架阶段）：端点、业务表、runner、报告、导出均已落地并
> 有真实依赖用例，故本手册按"已实际遇到/已复现"与"设计预判"两段组织。
> **更正（2026-09-16 文档同步）**：此前标题写"当前 B2 为骨架阶段，业务运行时故障尚不存在"、并有一节
> "骨架阶段（已确认 / 可确定）"含"`router()` 返回空路由 ⇒ 业务端点未实现""健康检查 `business evaluator pending`
> ⇒ 业务评测未实现"——这些与实现相反（端点 7 条已注册、健康详情已随实现更新），已整节替换为本节。

## 已实际遇到 / 已复现（含证据）

| 症状 | 可能原因 | 处置步骤 | 如何确认已恢复 |
|---|---|---|---|
| `alembic upgrade head` 在迁移 **0009** 报 `check constraint ... is violated by some row` | 库里有 0007 语义下写进的**跨域配对**行（例如 `rejected + infrastructure_error`）：0007 的 CHECK 只要求"终态必须有原因"，0009 起强制"原因与终态配对" | 先按 [../../alembic/versions/20260915_0009_b2_outbox_insert_seq_and_terminal_pairing.py](../../alembic/versions/20260915_0009_b2_outbox_insert_seq_and_terminal_pairing.py) docstring 里的两条 `SELECT count(*)` 查出违规行并修正数据，再重跑升级。**失败是完全原子的**（版本仍 0008、无列/索引残留），可安全重试 | 两条前置检查均返回 0，且 `uv run alembic upgrade head` 成功、`alembic heads` 为 `20260915_0009` |
| 日志里出现 `messaging.broker_unavailable` 但 broker 正常 | 派发失败的**归因**要按事件名读：`events.outbox.payload_rejected` 才是"我们自己的 payload/envelope 不合契约"（例如写入端漏了必填键），`messaging.broker_unavailable` 才是依赖故障 | 先查同一 `event_id` 的 `outbox_events.last_error`：`unknown key …`/`missing key …` ⇒ payload 问题，改写入端；连接类文本 ⇒ 查 broker | 派发 `published > 0`、`dead_letters` 不再增长 |
| 一次 broker 故障刷出大量 ERROR 日志 | 派发失败**逐行**记日志（失败行 × 每次重试），本模块**不做日志节流**（节流属日志采集/告警层，避免在这里丢弃证据） | 在采集侧按事件名聚合/限流；行数与 `outbox_events` 里 `published_at IS NULL` 的行数一致才是"如实" | 采集侧聚合生效，且不丢行（`attempts`/DLQ 仍完整） |
| 容器内 `run_case` 报 `SandboxUnavailableError: nsjail: … exists+executable=False` | nsjail 装在源码默认位置（`/usr/local/bin/nsjail`）而策略声明的路径不存在 | 现在由 `resolve_nsjail_path` 按候选位置解析（探测与启动用同一路径）；确认 `probe_sandbox` 的 `reason` 里列出的候选位置 | `nsjail=… exists+executable=True` 且用例真跑通 |
| 宿主驱动验收模式报 `matrix_items=… missing=['I2 …']` | 矩阵渲染把命令列写成多行，宿主侧按行解析读不到该项（2026-09-16 已修） | 升级到含该修复的版本；`_render_matrix` 现对命令列也展平 | `AUTOGRADER_B2_NSJAIL_VIA_DOCKER=1` 下 `1 passed, 14 skipped` 且 `missing=[]` |
| `/health/ready` 的 detail 一直写"累计 N 次未预期依赖失败"、怎么等都不回到干净文案 | 那个计数是**自进程启动以来的单调累计**（见 [local-development.md](local-development.md) §6a）：它**只如实报告、不翻转 `healthy`**（翻转会让实例在第一次抖动后永久不健康），因此它不会随时间自己变干净；在无沙箱的本机上，参考解答验证的必然 `503` 本来也**不**计入（那是文档化的平台条件） | 按**同一时间窗**查 `persistence.database_unavailable` / `messaging.broker_unavailable` / `runner.sandbox_unavailable` 定位类别；只有进程重启才会把累计清零 | 真正的可用性看**分项状态**而不是那句话：四项都不含 `unavailable` 即 `healthy=true`；若那句话来自已修复的故障，重启后消失且不再累加 |
| detail 写"累计 N 次未预期依赖失败"却被读成"此刻仍在坏" | 旧措辞（"评测/成绩端点报告了 N 次未预期失败"）没有"累计"二字，也没说它不影响判定——已在 2026-09-20 的整改里改掉（同一批还改了 **9 处**调用点注释 + `events/nats.py` 1 处，它们都写着"健康检查据此报 unavailable"，与代码相反：`broker_health()` 读的是"配置 + outbox 账本"，`last_error` 不参与判定） | 升级到含该整改的版本；确认 detail 里出现"自本进程启动以来累计…不参与健康判定" | `uv run pytest tests/test_b2_routes.py -k recorded_endpoint_failures -q` 通过（真实读数见 [../phases/active/phase-09-readiness-failure-counter.md](../phases/active/phase-09-readiness-failure-counter.md)） |

## 高概率问题（设计预判，非已发生）

| 症状 | 可能原因 | 处置步骤 | 如何确认已恢复 |
|---|---|---|---|
| 提交成功但评测卡在 `queued` 不进入 `running` | runner 未消费队列 / 依赖（nsjail、NATS）不可用 | 检查 runner 消费者状态、nsjail 版本、NATS 连通性；查看结构化日志按 `correlation_id` 定位 | 评测进入 `running` 且 `evaluation.started` 已发布 |
| 评测超时后系统仍有残留子进程 | 进程树未清理 | 检查是否按 cgroup/进程组杀整树；核对 [ADR-0002](../adr/ADR-0002-sandbox-execution-policy.md) 的清理策略 | 复跑 T-RUN-003，`ps` 无残留进程 |
| 学生代码发起网络却未触发 `security_finding` | 沙箱网络未真正隔离 | 核对 nsjail 网络命名空间参数；复跑 T-SEC-003 | 外网请求失败且被记录 |
| 重复提交同一幂等键产生两次评测 | 幂等约束缺失或未按作用域 | 检查 `submission` 的唯一约束与幂等实现 | 复跑 T-RECV-004 仅一次评测 |
| 报告分数与逐项证据对不上 | 合计校验缺失 | 检查报告器合计校验；复跑 T-REPORT-002 | 每分可追溯且合计成立 |
| `alembic upgrade head` 报多 head 或多 revision 冲突 | 并行开发未串行化迁移 | 按 [../database/migrations.md](../database/migrations.md) 重排 revision 链 | `uv run alembic heads` 仅一个 head |
| 事件重复投递导致重复统计 | 消费者未按 `event_id` 去重 | 检查 inbox 去重键；核对 [../api/events.md](../api/events.md) | 重复消费不产生重复业务效果 |

## 处置原则

1. 先看结构化日志按 `correlation_id` 串联，不猜。
2. 故障与修复必须写进 `../phases/active/` 与 [../knowledge/](../knowledge/README.md)（同一根因出现两次且有稳定修复才收知识笔记）。
3. 涉及契约不一致时走 [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md)，不私改。
