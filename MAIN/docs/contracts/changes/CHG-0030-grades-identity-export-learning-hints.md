# CHG-0030 成绩身份关联、时间范围导出与学生分层提示（AI-FLOW-01B）

- **级别**：P2 语义（B2 成绩读/导出参数扩展 + 新增两条学生提示 operation + 一张新表）
- **状态**：已生效（契约）；B2/B1 实现同批落地
- **契约版本**：0.23.0 → **0.24.0**
- **依据**：`RQ-B-1-30`（成绩查询/证据下钻）、`RQ-B-1-60`（按时间范围导出、可选列）、
  `RQ-B-2-60`（导出由 B2 生成）、AI-FLOW-01B（学生本次评测的分层提示）、
  `ADR-0009`（模型输出只在独立轨）、`ADR-0014`（参考分不是分数）

## 1. 问题

三个业务缺口共用一条边界：**B2 只能读 `b2` schema，而成绩单要的姓名/学号属于 B4**。

1. `GradeRow.student_number` / `display_name` 恒为 `null`（B2 不读 `b4`），而课程验收要求成绩与
   导出包含姓名、学号。此前没有任何"授权地取回身份"的契约化路径：要么跨 schema join，
   要么由 B1 重拼导出文件——两者都违反模块边界。
2. `listGrades` / `exportGrades` 没有 `from`/`to`，也没有列选择。`RQ-B-1-60` 要求按时间范围
   导出并可配置列；页面筛选与导出因此无法证明"同口径"。
3. P10 走到终态后没有闭环：`?evaluation_run_id=` 无法把 P11 直接定位到那一次运行；
   学生也拿不到"针对本次失败证据、按层展开"的学习帮助（AI-FLOW-01B）。

## 2. 裁决

### 2.1 成绩读路径

- `listGrades` / `exportGrades` 新增 `from` / `to`（RFC 3339，含两端，作用在提交时刻）与
  `evaluation_run_id`（按运行反查提交）。**授权作用域独立生效**：他人的 run id 得到空集，而不是
  一条更宽的读路径；`from > to` 是 `422`。
- `GradeRow` 新增 `security_status`（三态安全结论，可空）。它只用于展示与**可选导出列**，
  **不**参与平均分、通过率、排序或筛选。

### 2.2 姓名/学号的关联方式（不跨 schema）

- **页面**：B1 用已授权的 `GET /sections/{section_id}/enrollments`（`EnrollmentPublic` 含
  `student_id`）按 `student_id` 关联；映射不到时显示"名单中未找到"，**不**把平台 ID 当学号。
- **导出**：B2 转交调用者自己的 bearer token 请求同一个 B4 名单接口，按 `student_id` 关联后生成
  文件。B2 不存第二份身份表、不跨 schema SQL、不新增服务身份。关联失败（未配置 / HTTP / 解析 /
  名单缺该学生）时**导出失败**（503/409），**不生成**缺姓名的"成功"文件。

### 2.3 `exportGrades.columns`

逗号分隔的可选列选择；未知字段 `422`（不静默忽略）。`RQ-B-1-60` 的必需列（学号、姓名、课程、
课序、题目版本、得分、满分、提交时间、评测 ID）**始终存在**，不能通过 `columns` 删除。
可选列：`submission_id`、`student_id`、`state`、`is_pass`、`security_status`。

### 2.4 `GET/POST /evaluation-runs/{id}/learning-hints`（新增）

- **只有 run owner 学生**可读写；教师/助教/管理员一律 `403`（刻意不提供教师逐条查看学生提示的
  接口）。归属由 submission 的 `student_id` 判定，**不接受**请求体/查询串里的 `student_id`。
- `POST` 无模型参数、无题面、无源码：provider/model 来自部署配置，公开题目上下文由 B2 用
  `run.question_version_id` 从 **B3**（`learning-context` 安全视图，成员三提供）服务端读取。
- 输入白名单 = `commentary.scope.INPUT_SCOPE`（已完成运行的结构化证据）+ 可选的公开题面/语言/
  标签/adapter kind。**不传**学生源码、参考答案、隐藏用例、期望输出、他人数据、姓名/学号/token。
- 输出为 1–3 条有序 `hint_steps`（`knowledge` → `direction` → `self_check`），经安全输出闸门
  （完整可提交程序形状 / 分数断言 / 隐藏字段标记 → 整组拒绝）。
- 状态与错误可区分：未完成 `409`、被安全拒绝 `409`、基础设施失败 `409`、无失败证据
  `200 not_applicable`、预算 `429`、provider 未配置 `503`、传输失败 `503`（行标 `failed`）、
  输出非法/越界 `200 failed`。`Idempotency-Key` 重放返回已有结果、不重复调用模型。
- 存储：新表 `b2.evaluation_learning_hints`（迁移 `20260915_0016`）。一 run 一行；
  **没有** score/evidence 列；`failed` 只存脱敏类别与 request id。`unavailable` / `not_requested`
  不落库。`GET` 在 provider 关闭后仍可读取既有结果。

## 3. 影响的契约文件

- `openapi/b2.yaml`：两个 grades operation 的参数；`GradeRow.security_status`；
  新增 `LearningHintStep`、`LearningHintsResponse` 与两条 learning-hints operation。
- 五个 `openapi/*.yaml` 与 `asyncapi/events.yaml` 的 `info.version` 同步升至 `0.23.0`
  （契约规则：所有文件同版本）。

## 4. 影响的模块

| 模块 | 需要做什么 |
|---|---|
| **B2** | 成绩过滤（from/to/run id）、`security_status`、导出列选择与 B4 名单关联（新增 `B2_B4_BASE_URL`）；新增 `hints` 包与两条路由；迁移 `20260915_0016`；`module.yaml` 迁移头同步 |
| **B1** | P10 题干/双栏/草稿确认/终态闭环；P11 时间范围、题目选择器、身份关联、完整数据才出汇总、导出 Drawer（格式/时间/列）、`?evaluation_run_id=` 深链、学习提示卡 |
| **B3** | **无改动**：`0.18.0` 已提供 `GET /question-versions/{id}/learning-context`（`QuestionLearningContext`，只回公开题面/语言/标签/adapter kind）。B2 消费者直接使用该 operation；不可用时只使用评测证据 |
| **B4** | **无改动**：`listEnrollments` 已含 `student_id`/`student_number`/`display_name`（`CHG-0007`） |

## 5. 安全与隐私检查

- 新增的导出字段只有姓名/学号（B4 已授权名单）与三态安全结论；**没有**源码、参考答案、隐藏用例、
  期望输出或跨课程数据。公式注入转义沿用现有导出层（六个前导字符）。
- 学习提示不含分数与证据；`context_scope` 可审计；题面作为**数据**进入 prompt（不是 system 指令），
  并有结构化输出闸门。日志只记录 actor/run/provider/model/prompt version/context scope/status/
  failure category，**不记录** prompt 或模型响应原文。
- 跨 schema：B2 仍只读写 `b2`；姓名/学号经 B4 HTTP 契约取得，不做跨 schema SQL、不存身份副本。

## 6. 资源影响

- 一张新表 + 一个索引（`student_id, requested_at`，用于预算计数）；迁移只建表，不改既有表。
- 导出多出 1–N 次 B4 名单请求（每个出现的课序一次，含分页），由调用者 token 授权。

## 7. 需求一致性

落实 `RQ-B-1-30`、`RQ-B-1-60`、`RQ-B-2-60` 与 AI-FLOW-01B；不新增 `RQ-*`。权威分数、逐项证据、
`result_digest`、导出汇总规则与评测沙箱路径**均未改变**。

## 8. 是否破坏兼容

**向后兼容**（新增可选参数、新增字段、新增 operation）。两点需要注意：

- `GET /grades` 的 `GradeRow` 多了 `security_status`：严格消费者（`additionalProperties: false`
  的生成类型）需要重新生成类型；契约层面是**加字段**，不是改字段。
- `exportGrades` 的默认行为在**有行时**需要一个可用的 B4 名单与调用者 token；未配置时 503 而不是
  输出缺姓名文件——这是**有意的行为收紧**，已写入 operation 描述。

## 9. 验证（实施后执行）

1. `MAIN`：`uv run python -m autograder_host.checks`（契约解析、锁一致性由工具计算摘要）。
2. `B1`：`pnpm lint`、`pnpm typecheck`、`pnpm api:check`、定向 Vitest（submit/grades/commentary/hints）。
3. `B2`：`uv run ruff check src/autograder_b2 tests`、`uv run pytest`（含新提示轨与成绩导出用例；
   **真实 PostgreSQL 用例在无库环境 skip，必须如实记录**）。
4. 真实沙箱（Linux + nsjail）与真实 B3/B4 联调**不在本轮环境内**，不得声称已通过；
   B3 `learning-context` 的实调需要在部署中配置 `B2_LEARNING_CONTEXT_PATH`（默认使用契约路径）。

## 10. 摘要

由 `checks.py` 计算（不在本文复制字面值）。

## 11. 回滚方式

回滚本批契约与四个模块的 lock，删除 `b2.evaluation_learning_hints` 迁移（`downgrade` 只删本表），
并撤回两条 learning-hints operation 与 grades 新参数。已生成的提示行随表删除；权威评测记录不受影响。
