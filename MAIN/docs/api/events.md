# 事件目录与语义

事件是模块间**唯一的异步连接面**。权威机器契约在 `MAIN/contracts/asyncapi/events.yaml` 与 `MAIN/contracts/schemas/`；本文件规定**语义、所有权、投递行为与禁止内容**，不复制字段表。

决策依据：`../adr/ADR-0006`（事件驱动解耦）。

---

## 1. 通用规则

| 规则 | 内容 |
|---|---|
| 中间件 | NATS JetStream（唯一），由 B4 运维（`RQ-B-4-20`） |
| 命名 | `autograder.v1.<domain>.<event>`，全小写点分 |
| 信封 | `event-envelope.schema.json`：`event_id`、`event_type`、`schema_version`、`occurred_at`、`correlation_id`、`causation_id`、`producer`、`payload` |
| 投递语义 | **at-least-once** —— 消费者**必须**幂等 |
| 发布 | **outbox**：事件与业务变更同事务写入，投递器异步发出 |
| 消费 | **inbox**：以 `(consumer, event_id)` 为唯一键去重（每个消费者各自去重） |
| 重试 | 有限次重试；超过上限进入 **DLQ**，可查询、可人工重放，**不得静默丢弃** |
| 顺序 | **不保证**跨分区顺序。消费者不得依赖到达顺序（如"完成"早于"开始"到达） |
| 去重 | `event_id` 为唯一去重键；业务侧可再用 `(domain, 业务ID, 状态)` 二次去重 |
| 关联 | `correlation_id` = 同一次业务操作；`causation_id` = 直接因果事件，**没有直接因果事件时为 `null`**（见 `../contracts/changes/CHG-0004-causation-id-null-in-production.md`） |
| 大小 | 事件是**通知**不是数据搬运：payload 只带 ID 与去标识化字段，消费者自行按需拉取 |
| 保留 | JetStream 配额 ≤0.25 GB；必须有保留策略 |

### 语义版本

`schema_version` 是**整数**（当前 `1`）。payload 结构发生**破坏性**变化时提升该整数，并保留旧消费者迁移窗口——但**不保留双轨实现**（迁移窗口内旧消费者必须尽快升级；见 `governance.md` §5）。

---

## 2. 事件清单

| Subject | 生产者 | 消费者 | 触发时机 | 状态 |
|---|---|---|---|---|
| `autograder.v1.submission.accepted` | **B2** | （暂无；供审计与后续扩展） | 提交通过校验并落库后 | 契约已锁定 |
| `autograder.v1.evaluation.started` | **B2** | B1（进度）、B4（审计） | 出队并开始评测 | 契约已锁定 |
| `autograder.v1.evaluation.progress` | **B2** | B1（进度） | 每个阶段/用例完成 | 契约已锁定 |
| `autograder.v1.evaluation.completed` | **B2** | **B3（统计）**、B1 | 报告与证据已持久化之后 | 契约已锁定 |
| `autograder.v1.evaluation.failed` | **B2** | B1、B4 | 技术失败（系统原因） | 契约已锁定 |
| `autograder.v1.question.version.published` | **B3** | **B2**（可用版本感知/缓存失效） | 版本发布成功后 | 契约已锁定 |
| `autograder.v1.roster.import.completed` | **B4** | （暂无；供审计与后续扩展） | 名单确认写入成功后 | 契约已锁定 |

> **注意**：`evaluation.rejected`（安全/静态被拒）目前**不是**独立事件，被拒结果通过提交终态 + `evaluation.failed`/SSE 呈现。若后续需要独立的 `evaluation.rejected` 事件，属于 P1 增补（见 `../contracts/change-protocol.md`），需 B3 明确其统计语义。

---

## 3. 逐个事件的语义与禁止内容

### 3.1 `submission.accepted`

- **含义**：提交已通过校验、内容已落库、已进入评测队列。
- **不是**："代码正确"或"评分完成"。**不得**被解读为任何成绩信号。
- **可携带**：`submission_id`、`student_id`、`course_id`、`section_id`、`question_version_id`、`occurred_at`、来源（`api`/`email`）。
- **禁止携带**：源码内容或摘要（摘要可以，内容不可以）、文件清单内容、学生姓名、邮件正文。

### 3.2 `evaluation.started`

- **含义**：评测已获得执行资源并开始。
- **禁止携带**：源码、包内容。

### 3.3 `evaluation.progress`

- **含义**：阶段或单用例进度推进。
- **必须携带**：单调递增 `sequence`（供前端去重排序）、阶段名或 `(已完成数/总数)`。
- **禁止携带**：用例的**期望输出**（`expected_output`）、隐藏用例输入全文、源码。
  - **理由**：进度事件可能被 B1 展示给学生。隐藏用例的输入/期望一旦进入进度事件，就等于向学生泄露题目答案。
  - **允许携带**：用例序号、`case_id`（不透明）、通过/失败（若已判定）、失败类别。
- **频率约束**：必须有节流（如每阶段一次 + 每 N 个用例一次），避免事件洪水与 SSE 压力。

### 3.4 `evaluation.completed`（**最关键的事件**）

- **含义**：评测完成，报告与逐项证据**已持久化**。
- **顺序保证（发布前提）**：**先持久化，后发事件**。不允许"事件已到但报告查不到"。
- **B3 消费此事件做统计**（`RQ-B-3-30`），因此 payload 是**隐私边界的焦点**：
  - **允许携带（去标识化）**：`evaluation_run_id`、`submission_id`（伪名：B3 无法把 submission 映射到学生，映射关系只存在于 `b2` schema）、`question_version_id`、`course_id`、`section_id`、是否通过、得分与满分（或分数区间）、`result_digest`、`failed_category`、耗时、`occurred_at`。
    - 当前官方示例见 `MAIN/contracts/examples/event-evaluation-completed.json`（含 `submission_id`、`evaluation_run_id`、`question_version_id`、`score`、`result_digest`）。
  - **禁止携带**：源码及其任何摘要、参考答案、隐藏用例、`expected_output`、学生姓名/学号/邮箱、`student_id`（**统计不需要，且属于可识别信息——用 `evaluation_run_id` 即可去重计数**）。
- **B3 的约束**：B3 **不得**通过任何其他途径获取源码或学生身份；其统计模型在类型上就不应包含这些字段。
- **去重键**：`evaluation_run_id`（同一次评测不得重复计数）。

> ⚠️ **待决问题**：`student_id` 是否允许出现在本事件中，直接影响"独立作答人数"这一统计口径（`RQ-B-3-30` 要求"题目回答次数分布"）。若不含 `student_id`，B3 只能统计**作答次数**而无法统计**独立人数**。当前决策：**不含**，即统计口径为"作答次数/通过率/失败分布"，"独立人数"由 B2 在成绩查询侧提供（B2 有权访问身份）。此决策需在 B3 实现统计时确认并在 `rq-catalog.md` 的 `RQ-B-3-30` 验收中明确。

### 3.5 `evaluation.failed`

- **含义**：**技术失败**（沙箱不可用、依赖故障、内部错误）——**不是学生的问题**。
- **必须携带**：失败类别（`infrastructure_error` 等）与可读原因摘要。
- **禁止携带**：堆栈、内部路径、SQL、凭据。
- **语义约束**：**不得**用本事件表达"学生代码不达标"（那是 `completed` + 低分）；**不得**用本事件表达"安全被拒"（那是 `rejected` 终态）。

### 3.6 `question.version.published`

- **含义**：题目版本已发布并**不可变**。
- **可携带**：`question_id`、`question_version_id`、`course_id`、内容摘要、`published_at`。
- **禁止携带**：参考答案、测试用例、`expected_output`、隐藏用例数量之外的任何用例内容。
- **消费者（B2）行为**：据此感知可用版本（缓存失效/调度准入）。**不**据此执行评测。

### 3.7 `roster.import.completed`

- **含义**：名单确认写入成功（原子）。
- **可携带**：`roster_import_id`、`course_id`、`section_id`、成功行数、拒绝行数、`occurred_at`。
- **禁止携带**：**任何 PII**——不包含学生姓名、学号、邮箱，也不包含逐行内容。
- **为什么**：名单变更可能触发下游（如通知），下游应按 ID 拉取授权范围内的数据，而不是从事件中读取 PII。

---

## 4. 禁止内容的统一清单

**任何事件 payload 中都不允许出现**：

| 禁止项 | 原因 |
|---|---|
| 源码内容 | 学生作品隐私；统计不需要 |
| 参考答案 / `expected_output` | 学术诚信 |
| 隐藏测试用例内容或输入 | 学术诚信 |
| 学生姓名 / 学号 / 邮箱 | PII |
| 密码、令牌、cookie、密钥 | 凭据 |
| 内部绝对路径、主机名、端口、schema/表名 | 内部信息泄露 |
| 堆栈跟踪、SQL 语句 | 内部信息泄露 |
| 邮件正文与附件原文 | PII 与隐私 |

**允许出现的**：UUID 标识符、去标识化数值与枚举（得分、通过与否、失败类别、耗时、计数）、时间戳、版本与摘要。

**摘要算不算敏感？** 提交内容的 SHA-256 **不算**源码（不可逆），但它可作为"是否交过同一份代码"的判据。因此：**只有 B2 内部与授权教师可见的接口**可以携带提交摘要；**事件中不携带**（统计不需要）。

---

## 5. 消费者的实现清单（每个订阅方都必须做到）

- [ ] 声明 durable consumer，绑定到确切 subject。
- [ ] inbox 表以 **`(consumer, event_id)` 复合键**为主键（每个消费者各自去重），插入冲突即视为已处理（幂等）。

  > 为什么不是 `event_id` 单列：同一个事件可以被**多个消费者**订阅，单列键会让第二个消费者
  > 因"第一个消费者已插入"而被判成重复，于是它的业务效果**永不发生且无日志**。这是
  > [CHG-0002](../contracts/changes/CHG-0002-inbox-primary-key-wording.md) 的判定结果（MAIN 采纳
  > 复合键，改条文而非改实现）；B2 的 `b2.inbox_events` 早已是复合键，此前只有本条清单的措辞
  > 与之不符。
- [ ] 业务去重键二次校验（如 `evaluation_run_id`）。
- [ ] 未识别字段**容忍**（前向兼容：新字段不导致崩溃）。
- [ ] 未识别的 `event_type` 记录并忽略（不崩溃、不静默丢弃无日志）。
- [ ] 处理失败可重试；超过上限进 DLQ 并记录。
- [ ] 处理逻辑内**不读取**其他模块的 schema。
- [ ] 消费延迟（lag）可观测。
- [ ] 消费逻辑的测试包含"**重复消费同一事件**"用例。
- [ ] 消费逻辑的测试包含"**乱序到达**"用例（如先收到 `completed` 再收到 `started`）。

## 6. 生产者的实现清单

- [ ] 事件与业务变更在**同一事务**写入 outbox。
- [ ] `event_id` 为 UUID v4，生成一次、永不重发时改变。
- [ ] `correlation_id` 取自入站请求；`causation_id` 为直接因果事件，**没有直接因果事件时填 `null`**（不要求"为每个事件都找出一个因果事件"：B2 的 `evaluation.progress`/`completed`/`failed` 与同一运行的 `started` 之间的关系已由 `correlation_id` + `evaluation_run_id` 表达，见 CHG-0004 判定）。
- [ ] payload 经对应 JSON Schema 校验后才发出。
- [ ] payload 通过 §4 的禁止内容自查（自动化扫描）。
- [ ] 发布失败进入重试；**不因发布失败而回滚已成功的业务操作**（投递器负责补发）。
- [ ] 事件大小有上限（仅承载 ID 与去标识化字段自然会很小）。
- [ ] `producer` 字段取值只能是 `B1`\|`B2`\|`B3`\|`B4`。

## 7. 官方示例

`MAIN/contracts/examples/event-evaluation-completed.json` 是 `evaluation.completed` 的官方示例，必须在契约变更时同步更新并通过 schema 校验。

---

## 8. 已知缺口

| 缺口 | 影响 | 计划 |
|---|---|---|
| 示例载荷未做**实例**校验 | 示例可能已经不符合 schema 而无人发现 | 在 `checks.py` 中增加"示例通过对应 schema"的校验（见 `../testing/cases.md`） |
| 无 `evaluation.rejected` 独立事件 | B3 无法区分"安全被拒"与"技术失败"的统计口径 | 若统计需要，走 P1 契约增补 |
| `ModuleDescriptor.events` 不区分发布/订阅 | 无法自动校验模块声明的事件方向 | ADR-0003 遗留风险，需 P3 级 SDK 变更 |
| 事件无 schema 注册表校验 | `schema_version` 与实际 payload 结构的对应关系靠人工维护 | 功能阶段考虑以 schema 文件路径约定 |
| 未定义 consumer lag 告警阈值 | 统计可能长期落后而无人察觉 | B4 运维阶段定义 |
| DLQ 处理流程未落地为流程 | DLQ 可能累积并占用配额 | B4 runbook 定义巡查与重放步骤 |
