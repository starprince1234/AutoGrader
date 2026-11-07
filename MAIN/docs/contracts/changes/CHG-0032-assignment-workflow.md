# CHG-0032 作业目标模型、assignment_id 全链路与多题 .txt 网页/邮件提交

- **级别**：P2 语义扩展 + 破坏性字段替换（作业目标模型重定义、提交/成绩强制 `assignment_id`、
  邮件核验码改为作业维度、`email_delivery` 附件模板删除）
- **状态**：已生效（契约与实现同批落地）
- **契约版本**：0.30.0 → **0.31.0**
- **依据**：教师布置作业—学生网页/邮件提交—教师查看提交与答案的端到端要求；
  `MAIN/docs/architecture/assignment-workflow.md`（冻结设计基线）；
  `MAIN/docs/architecture/unix-homework-evaluation.md`（被本变更扩展的邮件/执行声明基线）

## 1. 问题

1. 作业只能挂到单一课序（`b3.assignments.section_id`），无法表达"同一门课的多个班级"或"在班级里
   指定若干学生"；拆成多个相似作业或前端过滤都不是准确的发布对象。
2. B2 的提交、幂等、成绩、提交视图、事件都没有 `assignment_id`。同一题目版本用于两个作业时，
   提交和状态会互相串联；教师无法回答"这个作业谁没交/谁交了几题/哪题失败"。
3. 学生端只有单题提交页，没有作业维度多题工作区；答案只能是代码文件，不能按题粘贴/上传 `.txt`。
4. 邮件收件要么依赖部署期固定目标（一次只能收一份作业），要么依赖匹配规则表；没有作业维度的
   一致性证明，也没有"一邮件多题、按题映射"的表达。

## 2. 裁决

### 2.1 作业目标模型（B3）

- `assignments` 删除 `section_id`；新增 `assignment_sections(assignment_id, section_id)` 与
  `assignment_students(assignment_id, student_id)`。一个作业**至少一个目标课序**；
  `student_ids` 为空 = 所选课序全体，非空 = 在这些课序内按名单收窄。
- 目标在创建时经 B4 internal `POST /api/v1/internal/course-targets/validate` 服务端校验
  （section 属于该课程、student 在给定课序中有效在册）；未知/停用/跨课程 → 422；
  B4 不可用 → 503，**不跳过校验**。
- 读授权：教师按课程范围（section-scoped 令牌收窄到目标课序）；学生要求课程+课序命中目标课序，
  且（收窄时）本人在名单内。列表按受众预过滤并修复 keyset 分页。
- 新增 internal `GET /api/v1/assignment-audiences/{assignment_id}`（serviceToken），向 B2 提供
  受理提交所需的最小受众（`AssignmentInternal`，7 字段，无时间戳/题目内容）。

### 2.2 提交与成绩（B2）

- `submission-envelope` 必填 `assignment_id`；`files` 1..1 且必须是单段 `.txt`。
- `submissions.assignment_id` 可空（历史行保留 NULL，不回填、不猜测、不进入作业过滤）；
  幂等作用域改为 `assignment:student:question_version`。
- 受理时校验：作业存在且 open、course/question 属于作业（422）、section/student 属于受众（403）；
  作业目录不可用 → 503（基础设施故障）。
- `GradeRow`/`SubmissionView` 增加 `assignment_id`；`listGrades`/`exportGrades` 增加
  `assignment_id` 过滤；导出增加"作业ID"必需列。
- `submission.accepted` 事件增加可选 `assignment_id`（旧 outbox 事件仍须可投递）。
- 文件/来信读端点改为：学生仅本人、教师/管理员在课程范围内；评分写端点仍仅教师/管理员。

### 2.3 多题 `.txt` 邮件（B2）

- 核验码改为作业维度：`POST /api/v1/email-verification/challenges` 请求
  `{course_id, section_id, assignment_id}`；状态响应 `submissions[]`（按题）。
- 邮件正文必须包含 `核验码 / 作业编号 / 课序 / 姓名 / 学号`；作业编号与挑战绑定值一致，
  姓名/学号与 B4 身份目录一致，课序仅要求非空（人读核对）。
- 附件只接受 `.txt`，文件名严格为作业内 1 基题号 `<n>.txt`；一封邮件可交部分题；
  每个附件生成一条 `assignment_id + question_version_id` 的提交（与网页提交同一结构、同一
  persist 路径与 worker），并按题写 `email_verification_submissions` 映射。
- 重复投递按邮箱位置/Message-ID 幂等重放；失败不写 checkpoint 可重试；非 `.txt`、未知题号、
  正文缺字段/身份不符、发件人不符、他人码、过期码均进入可解释隔离并记录挑战拒绝码。
- `email_delivery` 的 `attachment` 检查改为"该题有映射答案文件"；删除
  `required_attachment_template`（题目级附件名不再表达多题作业的映射）。
- 删除旧固定目标/匹配规则路径（`B2_IMAP_MATCHING_JSON`/`B2_IMAP_TARGET_JSON` 等）。
- 新增 B4 `GET /api/v1/me`（本人 display_name/student_number），供学生端生成邮件正文。
- 隔离复核 `complete` 改为按 assignment 与 `<n>.txt` 映射逐题创建提交。

### 2.4 前端（B1）

- 新增作业工作区：题目列表 + 题干/图片 + 每题粘贴或 `.txt` 上传 + 草稿 v2
  （assignment+version+owner）+ 逐题状态/报告；邮件提交面板展示可复制的五行正文与按题映射。
- 教师作业详情：目标受众、汇总计数（未交/部分/已交/评测中/失败，取数未完成不下结论）、
  逐人逐题矩阵、查看授权学生答案；作业创建支持多课序与名单收窄。
- 删除单题提交页与旧草稿 v1 键（整体替换，无兼容层）。

## 3. 兼容性

故意的破坏性变更：提交信封新增必填字段；作业创建/响应字段改名；邮件核验码请求字段替换；
`email_delivery` 附件模板删除。不保留别名、双写或兼容层。唯一的历史数据例外是
`assignment_id = NULL` 的旧提交：保留原样、可继续评测、不属于任何作业。

## 4. 验证

- 契约：本目录记录的 schema/OpenAPI/AsyncAPI 变更由 `relock_contracts.py` 重算摘要；
  模块锁与 `module.yaml`/`plugin.py` 版本同步到 0.31.0。
- 实现：B3/B4/B2/B1 的新验收用例；远端 CI 的 `verify`（无 DB 契约/规则）、
  `verify-frontend`（B1 lint/api:check/typecheck/test）与 `verify-database`
  （真实 PostgreSQL + NATS：B2 提交/成绩/邮件收件/迁移、B3 作业/版本/授权、B4 内部目标校验）。
- 本机按约定不运行项目测试/数据库；详细交付与未验证项见最终报告。
