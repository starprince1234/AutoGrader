# B2 数据库（表意图、约束、生命周期）

> DDL 权威在 `../../alembic/versions/**`。**本文件不抄 DDL**，只写表意图、关键约束与生命周期。表命名/主键/时间/命名规范见 [../../../MAIN/docs/database/ownership.md](../../../MAIN/docs/database/ownership.md) 与 RQ-B-4-30。

## 归属声明

B2 只使用 `b2` schema。禁止跨 schema 外键/SQL/视图/ORM；运行 login role `autograder_b2` 仅拥有 `b2` 权限。

## 表清单

> **2026-09-16 按实现刷新**：本表此前只列 6 张**单数名**的表（`submission`/`artifact`/…），
> 与实现不符——实际库里有 **17 张复数表**（`src/autograder_b2/db/schema.py` 的 `sa.Table` 对象），
> 由迁移 `0002`–`0006` 创建（下方"来源"列）。缺表的清单等于漏掉一半 schema，故整表重写；
> 仍然**不抄 DDL**（DDL 权威在 `alembic/versions/**`），只写意图、关键约束意图与生命周期。

| 表 | 来源 | 意图 | 关键约束意图 | 生命周期（谁创建 / 谁清理） |
|---|---|---|---|---|
| `submissions` | 0002（0008 补 provenance） | 一次代码提交的元数据（归属、题目绑定、状态、幂等键、`correlation_id`、入口 provenance） | `idempotency_key` + 作用域唯一；状态 ∈ 锁定状态机；`question_version_id` 只传 UUID 不建 FK；`ck_submissions_provenance_shape` 保证"api 行无邮件身份 / 邮件行无请求标识且四要素齐全" | B2 接收层创建；**没有清理路径**（提交与成绩证据是长期数据，B2 不删除提交行） |
| `artifacts` | 0002 | 内容寻址存储的文件内容条目 | 内容摘要唯一（SHA-256 去重）；服务端生成路径，客户端不可指定 | B2 存储层创建；孤儿回收任务清理（只清**无引用**且已过保留窗口的条目，见下节） |
| `submission_files` | 0002 | 提交内每个声明文件（路径 → 内容摘要） | 路径经白名单校验（`..`/绝对路径/反斜杠一律拒）；每文件一条 | 随 `submissions` 创建；**随提交级联删除**，而 B2 没有删除提交的路径（故实际不消失） |
| `email_checkpoints` | 0002 | IMAP 收取检查点（幂等） | UID/UIDVALIDITY 或 `Message-ID` 唯一；终态含 `processed`/`rejected` | B2 邮件适配器创建；**没有清理路径**（幂等键必须长期可查） |
| `intake_quarantines` | 0002（0013 补人工归属三列） | 无法唯一匹配 / 含危险附件的邮件隔离区；**人工归属的落点**（`CHG-0014`） | 记录原因码；不存正文与附件原文；`ck_intake_quarantines_resolution` 强制 `resolved = (resolved_student_id IS NOT NULL)`（"已归属 ⇔ 有归属对象"，`resolution_note` 可选故不参与）；`resolution_note` 结构上限 1000（契约的 500 由服务层判定） | B2 邮件适配器创建；人工归属由 `intake.quarantine` 的 `resolve`（唯一写入者，只写这一行）；**仍然没有清理路径**（隔离记录等待人工处置） |
| `artifact_gc_runs` | 0003 | 一次孤儿回收运行的计数与结束时间 | 每次回收一行，便于对账 | GC 任务创建；**没有清理路径**（回收审计是对账依据） |
| `artifact_gc_audits` | 0003 | 回收审计（摘要、动作、结果） | 只删**无引用**内容；删除动作必须留审计 | GC 任务创建；审计行不随内容删除而消失，也没有清理路径 |
| `outbox_events` | 0004（0009 补 `insert_seq`） | 出站事件 outbox（先写 outbox 再发，at-least-once） | `(subject, deduplication_key)` 唯一；`correlation_id` NOT NULL；`insert_seq` 提供**发布顺序**不变量；DLQ 不静默 | 写入方同事务创建；派发成功后标 `published_at`，超限进 DLQ；**没有清理路径**（已发布行保留作投递账本） |
| `inbox_events` | 0006 | 入站事件去重（消费幂等） | 按 `event_id` 去重；重复投递只生效一次 | 消费方创建；**没有清理路径**（去重记录必须长期可查） |
| `submission_state_transitions` | 0005 | 提交状态迁移史 | 每次迁移有记录；失败迁移保留错误类别与脱敏原因 | 状态变更时创建；**随提交级联删除**，而 B2 没有删除提交的路径 |
| `evaluation_runs` | 0006（0007/0009 收紧 CHECK，0011 补 `security_status`） | 一次评测运行（五项绑定摘要、状态、**三态安全结论快照**） | 五项绑定摘要必填；`evaluation_run_id` 由 B2 生成；`ck_evaluation_runs_terminal_consistency` 强制 `state`↔终态**配对**（0009）；`security_status ∈ {found,not_found,not_checked}` 或 NULL（NULL＝没走到安全判定阶段，**不是** `not_found`） | B2 入队时创建；**没有清理路径**（运行是成绩证据的根） |
| `evaluation_run_state_transitions` | 0006 | 运行状态迁移史（含终态原因） | 每次迁移有记录；与 `state` 配对约束一致 | 状态变更时创建；随 run 级联删除，而 B2 没有删除 run 的路径 |
| `progress_events` | 0006（0009 收紧帧约束） | SSE 进度帧（`sequence` 单调） | `ck_progress_events_terminal_frames` 强制终态帧的 state↔reason 配对；`sequence` 唯一递增 | 编排层写；随 run 级联删除，而 B2 没有删除 run 的路径 |
| `case_evidence` | 0006 | 逐用例证据（输入/实际(截断)/期望/判定/失败类别/摘要） | 每 (run, ordinal) 唯一；摘要可重放比对 | 运行期写；随 run 级联删除，而 B2 没有删除 run 的路径 |
| `evaluation_results` | 0006 | 汇总结果（分数、通过与否、`result_digest`） | 每 run 一行；分数可追溯（合计校验）；`result_digest` 可重放 | 报告器写；随 run 级联删除，而 B2 没有删除 run 的路径 |
| `static_findings` | 0006 | 静态检查发现（规则 ID/severity/位置） | 每条规则必须在规则目录内登记；区分"未发现"与"未检查" | 静态阶段写；随 run 级联删除，而 B2 没有删除 run 的路径 |
| `security_findings` | 0006 | 安全性发现（静态判定结论 + **执行期**沙箱违规，`detected_in ∈ {static, runtime}`） | 区分"未发现"与"未检查"；严重度与阻断判定一致；`char_length(message) <= 1000` | 安全判定与编排层（执行期违规）写；随 run 级联删除，而 B2 没有删除 run 的路径 |
| `evaluation_commentaries` | 0010（0014 加参考分四列） | **教学评语轨**（`RQ-T-20` / `ADR-0009`）：一次运行一条模型评语 + 状态 + provenance（含 `input_scope` 白名单）；**0014 起**另含模型的顾问性参考分（`RQ-T-30` / `ADR-0014`）：`advisory_score` / `advisory_max_score` / `advisory_rationale` / `advisory_confidence` | 每 run 唯一（重新生成＝覆盖）；`ck_evaluation_commentaries_shape` 强制状态与字段配对（`pending` 无文本无时间且无参考分 / `ready` 有文本有时间、参考分**四列同空或同有** / `failed` 无文本有原因有时间且无参考分）；`input_scope` 必须是非空 JSON 数组（白名单必须可审计）；`ck_evaluation_commentaries_advisory_bounds` 把 `0 <= score <= max_score` 且 `max_score > 0` 钉在存储层（**越界值写不进来**，而不是被截断入库） | 评语生成时写；**删除本表不影响可重放性**——没有任何表引用它，评测链路也不读它 |

**表数一致性自检（可复算）**：`schema.py` 的 `sa.Table` 计数 **18** == 本表行数 **18** ==
迁移 `0002`(5) + `0003`(2) + `0004`(1) + `0005`(1) + `0006`(8) + `0010`(1) 的和
（`0011`/`0014` 只加列、`0012` 只改主键名与时间列精度、`0013` 只加三列，都不建表）。

**`advisory_max_score` 的写入者只有一个**：`commentary/scope.py` 的 `collect_evidence`
（对 `b2.case_evidence.weight` 求和，与 `evaluation/scoring.py` 的 `max_score += award.weight`
同源同值）。它不是模型的输出——模型给出同名字段会让整次生成失败（`ADR-0014` 决策 2）。

**表标准（`RQ-B-4-30`，迁移 0012 整改）**：18 张表的主键约束名必须是 `pk_<table>`
（Postgres 的隐式 `<table>_pkey` 不合标准），`created_at`/`updated_at` 必须是 `timestamptz(3)`。
判据与 B4 的检查器同源，但 B2 侧有**独立读数**：`tests/test_migrations.py` 的三条用例直接查
真实库的 `pg_constraint` 与 `information_schema.columns`，并在 upgrade/downgrade/upgrade 之间断言来回可逆。
**已上报的偏差**：B4 的 `--scope migrations` 是**逐条声明**的文本扫描（不跟踪后续修订的修正），
因此那一侧的 B2 计数仍会把 0002–0010 里的原始声明算作偏差；实际结构以本节的库内读数为准，
详见 [../phases/active/phase-04-table-standard-remediation.md](../phases/active/phase-04-table-standard-remediation.md)。

## 保留策略（可配置；RQ-B-2-30 验收 5）

> 2026-09-18 更正：本节此前**没有内容**，而上表的生命周期列却写着"保留策略清理"——即"可配置的保留
> 策略"当时只是一句散文。现在实现与文字一致，且写清了它的**作用范围**。

| 项 | 事实 |
|---|---|
| 配置面 | 环境变量 `B2_ARTIFACT_RETENTION_DAYS`（天）；解析在 `src/autograder_b2/config.py`（`Settings.artifact_retention_days`），默认值 `0` |
| 语义 | 内容**失去全部引用**之后，至少再保留这么多天才允许回收。`0` ＝一失去引用即可回收（本模块长期行为，也是默认值）；调大只会让回收更保守 |
| 谁执行 | `src/autograder_b2/storage/gc.py` 的孤儿回收（相一按 `artifacts.created_at`、相二按内容文件的 mtime 判定），年龄条件与引用条件写在**同一条** `DELETE` 里 |
| 窗口内的内容 | **连候选集都不进**：不删除、也不写审计（审计记录的是清理操作，而这里没有操作）。因此 `artifact_gc_runs.scanned_artifacts` / `scanned_files` 统计的是**本轮够条件的候选**——`0` 不等于"库里没有孤儿" |
| 审计 | 每一次真正的删除都写 `artifact_gc_audits`（`removed` / `skipped_referenced` / `skipped_unrecognized` / `file_removal_failed`）。结果枚举与回收计数器**没有**为本配置新增取值（B2 不写迁移，`module.yaml: migration_head` 是与 MAIN 对账的共享状态） |
| 绝不删除 | 被任何 `submission_files` 引用的内容**任何窗口下都不是候选人**（引用关系是判定主体，时间只用于推迟） |
| **范围边界** | 只有内容寻址存储的**内容**受这套保留策略管理。上表中其余 16 张业务表**当前没有任何自动清理路径**（不按时间、也不按状态删除）——"保留策略清理"此前是被写成事实的意图，不是实现 |
| 测试 | `tests/test_storage_gc.py`：窗口内保留（含"没有清理操作就没有审计行"）、过窗口回收（相一与相二各一条）、以及"保留配置被忽略即失败"的反向断言 |

## 私有 / 敏感数据标记

| 位置 | 是否受保护 | 说明 |
|---|---|---|
| `artifacts` 内容（文件字节） | **受保护** | 仅学生本人 / 授权角色可读，原始内容不进入日志/事件/导出 |
| `evaluation_results` 分数与 `case_evidence` 证据摘要 | 部分受保护 | 学生仅见本人；教师见课程作用域内；隐藏用例的期望输出对学生视图裁剪；`case_evidence[].artifacts.expected_sha256` 是**可检验的预言机**，因此按同一口径对未声明可见的用例置空 |
| `evaluation_commentaries.text` | 非受保护（模型输出） | 生成输入只有已落库的结构化证据（`input_scope` 可审计）；它不进分数轨、不进导出；删除整表不影响可重放性 |
| `evaluation_commentaries.advisory_*`（四列） | 非受保护（模型输出 + 系统填入的满分） | 模型给的是 `score`/`rationale`/`confidence`；`advisory_max_score` 由系统按证据权重求和填入。**它不是成绩**：不进 `evaluation_results`、`case_evidence`、`result_digest`、绑定摘要、报告、`/grades`、导出或任何事件（有断言；见 [../testing/cases.md](../testing/cases.md) 的 T-ADV-001/002） |
| `security_findings` / `static_findings` 详情 | **受保护** | 对外仅脱敏结论，不暴露沙箱内部路径/违规细节；**评语生成的字段级 SELECT 不读这两张表的 `message` 列** |
| 状态迁移史的原因字段（`submission_state_transitions`/`evaluation_run_state_transitions`） | **受保护** | 必须脱敏（不存源码片段、绝对路径） |
| `email_checkpoints` / `intake_quarantines` 邮件元数据 | 受保护 | 不存邮件正文/附件原文 |

## 写入顺序与一致性

- 内容寻址：**先写内容（artifact）、后写元数据（submission）**；元数据失败产生的孤儿内容可被清理任务回收（决策见 [../adr/ADR-0003-content-addressed-submission-storage.md](../adr/ADR-0003-content-addressed-submission-storage.md)）。
- 报告生成的先决条件是结果已持久化（避免"报告已生成"假象，RQ-B-2-60）。
- **信封可往返**（迁移 `20260915_0008`）：`correlation_id` 与 provenance 随提交行一起落库，因此
  `integration.submission_loader` 能把这一行重新装成同一个冻结 `SubmissionEnvelope`（装载时重算
  `submission_digest` 并校验 `storage_ref` 的派生关系，不一致即拒绝）。缺了这两样，评测阶段就没有
  生产级输入路径——它必须用 `submission.correlation_id` 发事件（`outbox_events.correlation_id` 是
  NOT NULL）。约束意图：`ck_submissions_provenance_shape` 保证"api 的行没有邮件身份、邮件行没有请求
  标识且四要素齐全"，即"来自哪封邮件/哪个请求"只可能有一种答案。
- 0008 之前落库的历史行：邮件行按 `email_checkpoints` 里 `processed` 的同源事实回填；
  `correlation_id`（以及 api 行的 `request_id`）当初没有被持久化，只能回填为该行 `submission_id`
  作**占位值**——迁移 docstring 里明确写了这一点，不声称它等于当初的请求标识。

## 关系概述（不含 FK，只传 UUID）

```text
submissions ─┬─ submission_files（1..n，路径 → 内容摘要）
             ├─ artifacts（0..n，内容寻址，多提交共享）
             ├─ submission_state_transitions（1..n，迁移史）
             └─ evaluation_runs ─┬─ evaluation_run_state_transitions（1..n）
                                 ├─ progress_events（0..n，SSE 帧）
                                 ├─ case_evidence（1..n，逐用例证据）
                                 ├─ evaluation_results（1）
                                 ├─ static_findings（0..n）
                                 ├─ security_findings（0..n）
                                 └─ evaluation_commentaries（0..1，评语；**不被任何表引用**）
outbox_events / inbox_events：事件两侧（发布顺序 / 消费去重），与上表无 FK 关系
artifact_gc_runs ─ artifact_gc_audits（1..n，孤儿回收与审计）
email_checkpoints / intake_quarantines：邮件入口检查点与隔离区
```

跨模块引用（`question_version_id`、`course_id`、`section_id`、`student_id`）**只存 UUID**，不建跨 schema 外键；需要更多信息时按 ID 拉取或由事件 payload 携带。**该约束有测试**：`tests/test_migrations.py::test_no_cross_schema_foreign_keys`（真实 PostgreSQL 上建库到 head 后扫外键，须为空）。
