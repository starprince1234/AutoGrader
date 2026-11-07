# 作业工作流：多课序/指定学生目标、assignment_id 全链路、多题 .txt 网页与邮件提交

本文件是「教师布置作业 → 学生网页/邮件提交 → 教师查看提交与答案」这一轮改造的**冻结设计基线**。
字段的机器可读权威是 `MAIN/contracts/**`（契约版本 `0.31.0`，`CHG-0032`）；本文件解释语义与各模块的
实现边界。所有模块实现与本文件冲突时，以本文件 + 契约为准，并在提交前先对齐契约。

## 1. 核心决策（不可协商）

1. **一次提交 = 一道题**。作业本身不产生"整份提交行"；学生在作业工作区里逐题提交，每道题在
   `b2.submissions` 里是一行，带 `assignment_id + question_version_id`。一封邮件携带 N 个附件 →
   产生 N 行提交（共用同一封邮件的收件事实），全部进入**同一个** worker 与评测链路。
2. **目标模型**：作业属于一门课程，必须选择 1..N 个课序（`section_ids`）；可在这些课序里再勾选
   若干学生（`student_ids`）收窄受众；`student_ids` 为空 = 所选课序全体学生。**不支持"只选学生不选课序"**。
   指定学生必须是在所选课序中**有效在册**（B4 enrollment `is_active=true` 且 `user_id` 非空）的学生。
3. **assignment_id 强制贯通**：新的网页提交/邮件提交/成绩/成绩过滤/提交视图/事件/幂等作用域都必须
   带 assignment_id。历史提交没有 assignment_id 的保持 NULL，**不猜测归属、不删除、不回填**；
   它们可以继续被评测（评测只需要 question_version_id），但不出现在任何作业级视图/过滤里。
4. **每题一个 `.txt`**：网页提交与邮件附件都只接受一个 `.txt` 文本答案文件。
   文件名规则（网页与邮件一致）：作业中第 N 题（1 基序号，按 `question_version_ids` 顺序）的答案文件
   必须是 **`N.txt`**（`1.txt`、`2.txt`……）。这是唯一映射规则，不按附件顺序猜测。
5. **邮件必须用一次性核验码认证**：核验码绑定 `(student_id, course_id, section_id, assignment_id)`。
   没有核验码的邮件、发件账号与挑战所有者不一致的邮件一律进入可解释隔离；`From` 头不被信任。
   邮件正文还必须携带可机器校验的作业定位信息（见 §6.3）。旧的"固定目标 / 匹配规则表"邮件路由
   **整体移除**（不再有 `B2_IMAP_MATCHING_JSON` / `B2_IMAP_TARGET_JSON`）。
6. **不降级**：B3/B4 依赖不可用时，提交接收/核验码签发/邮件拉取必须如实报基础设施错误
   （HTTP 503 / 本轮拉取失败重试），**绝不能**把系统故障记成学生未交、0 分或格式错误。
7. **文件读取授权**：`GET /submissions/{id}/files` 与 `GET /submissions/{id}/intake-message`
   允许学生读**本人**、教师/管理员在课程（含课序）作用域内读；其它情况 403。
8. 旧的学生单题提交页（`/submit/{question_version_id}`）及其草稿 v1 键、旧的邮件固定目标匹配路径
   被作业工作区与核验码路径取代，**完整删除**，不保留兼容层。

## 2. 契约变更（MAIN/contracts，版本 0.31.0，CHG-0032）

所有 `openapi/*.yaml` 与 `asyncapi/events.yaml` 的 `info.version` 同步为 `0.31.0`；
`MAIN/contracts/CHANGELOG.md` 追加 `0.31.0 — 作业目标模型、assignment_id 全链路与多题邮件提交（CHG-0032）`；
摘要由 `python MAIN/scripts/relock_contracts.py` 计算写入五份 `contracts.lock`，并同步五份 `module.yaml`
与各 `plugin.py` 的 `contract_version`。

### 2.1 `schemas/submission-envelope.schema.json`

- `required` 增加 `assignment_id`；`properties.assignment_id = {"type":"string","format":"uuid"}`。
- `files`: `minItems: 1, maxItems: 1`；`$defs.file.path.pattern = "^[A-Za-z0-9_.-]+\\.txt$"`（单段 `.txt`）。
- `MAIN/contracts/examples/submission-envelope.json` 同步。

### 2.2 `openapi/b2.yaml`

- `POST /api/v1/submissions` 的请求体（内联 `SubmissionEnvelope`）：加 `assignment_id`（required），
  `files` 上限 1、路径模式同上。
- `SubmissionView`：新增 `assignment_id`（uuid 或 null；历史提交为 null）。
- `GradeRow`：新增 `assignment_id`（uuid 或 null，字段顺序放在 `section_id` 之后）。
- `GET /api/v1/grades` 与 `GET /api/v1/grades/export`：新增可选查询参数 `assignment_id`（uuid）。
- `POST /api/v1/email-verification/challenges`：
  - 请求 `EmailVerificationChallengeRequest = {course_id, section_id, assignment_id}`（**删除**
    `question_version_id`）。
  - 响应 `EmailVerificationChallenge` 增加 `assignment_id`。
- `GET /api/v1/email-verification/challenges/{challenge_id}` 的 `EmailVerificationChallengeStatus`：
  - **删除** `submission_id` 字段；
  - 新增 `submissions: [{question_version_id, submission_id}]`（该邮件已生成、按题映射的提交；未收到为空数组）。
  - `reason_code` 枚举增加：`email_body_missing_field`、`email_body_mismatch`、`attachment_mapping_invalid`。
- 邮件隔离/拒绝原因码（`intake_quarantines.reason_code` 契约枚举，若存在）增加：
  `email_body_missing_field`、`email_body_mismatch`、`attachment_mapping_invalid`。
  原有的 `email_verification_rejected`、`email_sender_mismatch`、`email_verification_expired` 保留。
- `POST /api/v1/intake/quarantines/{id}/complete` 请求体：`question_version_id` 替换为 `assignment_id`
  （保留 `student_id/course_id/section_id`）：人工把隔离邮件按该作业的附件映射补成提交。
- `GET /api/v1/submissions/{id}/files` 与 `/intake-message` 的描述改为 §1.7 的读权限语义。
- `GET /api/v1/submissions/{id}` 响应增加 `assignment_id`（nullable）。

### 2.3 `openapi/b3.yaml`

- 删除 `AssignmentPublic.section_id`；新增：
  - `section_ids: [uuid]`（1..50，unique，至少 1）；
  - `student_ids: [uuid]`（0..2000，unique；空数组 = 所选课序全体）；
  - `question_version_ids: [uuid]`（作业顺序 = ordinal 升序）；
  - `title` maxLength 从 300 收紧到 200（与 DB/输入一致）。
- `POST /api/v1/assignments` 请求体：
  `{course_id, section_ids, student_ids?, title, due_at?, question_version_ids}`；
  `section_ids`/`question_version_ids` `minItems:1 maxItems:50 uniqueItems:true`，
  `student_ids` `maxItems:2000 uniqueItems:true`（缺省 `[]`）。
- `GET /api/v1/assignments` query 保持 `cursor/limit/course_id/section_id`；`section_id` 语义改为
  "作业目标课序包含该课序"。列表必须真正按 cursor keyset 分页（现状 bug 一并修）。
- 新增 internal operation `getInternalAssignment`：
  `GET /api/v1/assignment-audiences/{assignment_id}`，`security: [{serviceToken: []}]`，
  200 → `AssignmentInternal = {assignment_id, course_id, title, status, section_ids, student_ids,
  question_version_ids}`，404 → 不存在。
- 新增 schema `AssignmentInternal`（公开 `AssignmentPublic` 不含 `created_at/updated_at`，internal 可复用其字段子集）。

### 2.4 `openapi/b4.yaml`

- 新增 internal operation `validateCourseTargets`：
  `POST /api/v1/internal/course-targets/validate`，`security: [{serviceToken: []}]`。
  - 请求 `CourseTargetValidationRequest = {course_id: uuid, section_ids: [uuid] (0..50 unique),
    student_ids: [uuid] (0..2000 unique)}`。
  - 200 → `CourseTargetValidation = {course_id, sections: [{section_id, course_id, section_code, term|null}],
    unknown_section_ids: [uuid], students: [{student_id, section_id}], unknown_student_ids: [uuid]}`。
    `sections` 只含属于该课程且存在者；`students` 只含在 **给定 section_ids 中**有效在册
    （`enrollments.is_active=true AND user_id IS NOT NULL`）的行；请求未给任何 section 时 `students=[]`。
  - 401：service token 缺失/错误；422：请求体不合法。

### 2.5 `asyncapi/events.yaml`

- `submission.accepted` payload 增加 `assignment_id`（uuid，可空，**非 required**：部署时 outbox 里
  可能仍有旧事件，派发器必须继续能投递它们）。新事件总是携带该值。

### 2.6 移除的执行声明字段

`evaluation-package.schema.json` 的 `execution.email` 删除 `required_attachment_template`
（附件名由作业题目位置统一决定，见 §1.4；`attachment` 检查的语义改为"该题有映射答案文件"）。
`openapi/b3.yaml` 的 `ExecutionEmail` schema 删除同名属性。`email_delivery` 的 `subject`/`attempt`
保持。B2/B3 的解析、编译、参考解自检、种子数据（`MAIN/seed/question-bank/sample-linux/sample-linux-homework.json`）同步。

## 3. B3：作业目标模型

### 3.1 数据库（迁移 `B3/alembic/versions/20261107_0008_b3_assignment_targets.py`，head 0007 → 0008）

- `b3.assignments`：删除列 `section_id` 与索引 `ix_assignments_section_id`。
- 新表 `b3.assignment_sections`：
  - `assignment_id uuid NOT NULL`（FK → `b3.assignments.assignment_id`，命名 `fk_assignment_sections_assignment_id`）
  - `section_id uuid NOT NULL`
  - `created_at timestamptz(3) NOT NULL`
  - `PK (assignment_id, section_id)` 命名 `pk_assignment_sections`
  - `ix_assignment_sections_section_id`
- 新表 `b3.assignment_students`：
  - `assignment_id uuid NOT NULL`（FK，`fk_assignment_students_assignment_id`）
  - `student_id uuid NOT NULL`
  - `created_at timestamptz(3) NOT NULL`
  - `PK (assignment_id, student_id)` 命名 `pk_assignment_students`
  - `ix_assignment_students_student_id`
- 新表按 B3 迁移惯例给 `autograder_b3_app`（SELECT）与 `autograder_b3_internal`（SELECT/INSERT）授权。
- downgrade：删新表、恢复 `section_id`（dev-only 有损：按 `min(section_id)` 回填后恢复 NOT NULL 与索引；
  migration docstring 明确写出这一点）。
- 同步 `B3/module.yaml: database.migration_head`、`B3/tests/test_migrations.py::REQUIRED_TABLES`
  与 head 断言。

### 3.2 领域/仓储

- `Assignment` ORM 删除 `section_id`；新增 `AssignmentSection` / `AssignmentStudent` 模型。
- 仓储：`create_assignment(course_id, title, due_at, created_by, now)`；`add_assignment_section`、
  `add_assignment_student`、`list_assignment_sections`、`list_assignment_students`；
  `list_assignments(...)` 支持 `section_id` 目标过滤与学生受众过滤（见 3.4），并真正使用 cursor `after`。
- `create_assignment(payload)`：
  1. `require_course_write`（teacher/admin；服务身份通过）。
  2. 版本校验与现状一致：不存在 → 404；跨课程 → 404（伪装不存在）；非 published → 409（`DraftAssignmentReference`）；
     `question_version_ids` 重复 → 422；`section_ids`/`student_ids` 重复 → 422（pydantic 层加 `uniqueItems` 校验）。
  3. 通过 B4 校验目标：`section_ids` 全部属于该课程（unknown → 422 `invalid-assignment-target`）；
     `student_ids` 必须是在**任一给定 section** 中有效在册的学生（unknown → 422 同一 problem type，
     detail 列出无效 id 数量，不泄漏学生信息）。B4 不可用 → 503（`AssignmentTargetsUnavailable`）。
  4. 同一事务写 assignments + items（ordinal 0..n-1）+ assignment_sections + assignment_students。
- 读授权：
  - 新增 `require_assignment_read(principal, assignment, section_ids, student_ids)`：
    admin 放行；教师要求 `require_course_read(course)` 且（若令牌只有 section pair）其任一 pair 命中目标
    课序；学生要求令牌覆盖 course+section，该 section ∈ 目标课序，且（`student_ids` 为空 或 `subject` ∈ 名单）。
    不满足 → 403；资源不存在 → 404（顺序保持：存在性检查后授权）。
  - `GET /assignments/{id}` 返回 `AssignmentPublic`（含 section_ids/student_ids/question_version_ids），
    列表逐行做同样的读者过滤（学生看不到不相干作业，教师按课程范围）。

### 3.3 API

- 路由与 DTO 按 §2.3；新增 internal 路由：
  - 服务令牌校验复用 `ServiceTokenVerifier`（`B3_SERVICE_TOKEN`）；未配置时端点返回 503 而不是放行。
  - 响应 `AssignmentInternal`；不存在 404。
- `B3/src/autograder_b3/integration/b4.py`（新）：
  - `B4TargetClient(base_url, service_token, timeout_seconds=10, opener=...)`；
    `validate_targets(course_id, section_ids, student_ids) -> TargetValidation`；
    HTTP 非 200 / 网络错误 / JSON 畸形 → `TargetsUnavailable`。
  - `TargetValidation` 含 `valid_section_ids: frozenset[UUID]`、`unknown_section_ids`、
    `valid_student_ids`、`unknown_student_ids`。
  - 配置：`B3_B4_BASE_URL`、`B3_B4_SERVICE_TOKEN`（Settings 增加字段；未配置时创建作业/校验目标
    返回 503，**不得跳过校验**）。部署 env 由 MAIN/deploy/write-runtime-env.mjs 注入。
  - 路由层依赖注入：测试通过 opener 注入；领域函数接受 `targets: TargetValidator` 端口。
- 删除 `require_section_write` 的单 section 用法（保留课程级写校验；section 级授权由目标校验 +
  read 判定承担）。旧函数可保留给内部调用但不得再作为作业创建的唯一门禁。

## 4. B4：内部目标校验端点

- `B4/src/autograder_b4/api/internal.py` 增加 `POST /api/v1/internal/course-targets/validate`：
  - 认证：现有 `require_service_token` 依赖（serviceToken）。
  - 请求体解析/校验：course_id 必填 UUID；section_ids/student_ids 为 UUID 数组（数量上限见契约），
    否则 422。
  - 查询：`sections` where course_id 且 section_id in 请求；`enrollments` where section_id in 请求
    且 is_active 且 user_id not null；`students` 去重 (student_id, section_id)。
  - 响应字段严格按契约；不返回姓名/学号（B4 内部端点最小暴露）。
- 测试：`B4/tests/test_internal_identities.py` 或新文件覆盖成功/未知 section/未知或停用学生/
  跨课程 section/错 service token/非法 body；确保用户会话 token 打不开（401）。
- B4 无数据库迁移。

## 5. B2：assignment_id 全链路（网页提交/成绩/文件授权）

### 5.1 迁移（B2，按顺序）

1. `20261107_0021_b2_assignment_submissions.py`（down_revision `20261107_0020`）：
   - `b2.submissions.assignment_id uuid NULL`（不设 NOT NULL、不回填、不猜历史归属）；
   - 索引 `ix_submissions_assignment_id`。
2. `20261107_0022_b2_email_assignment_intake.py`（down_revision 0021）：见 §6。
- 同步 `B2/src/autograder_b2/db/schema.py`（列追加在末尾、顺序与 DDL 一致）、
  `EXPECTED_SCHEMA_REVISION`、`B2/module.yaml: database.migration_head`、`B2/tests/test_migrations.py`。

### 5.2 冻结信封

- `domain/envelope.py`：`SubmissionEnvelope` 增加 `assignment_id: str | None`。
  - `build_submission_envelope(...)` 增加必填关键字 `assignment_id: str`（新写入路径）。
  - 服务端 `submission_id_exists`/幂等/摘要语义不变。
- `integration/submission_loader.py`：`_SUBMISSION_COLUMNS` 增加 `submissions.c.assignment_id`；
  NULL → `SubmissionEnvelope.assignment_id=None`（历史提交）；装载自校验不变。
- `intake/service.py`：
  - `_read_envelope` 读取 `assignment_id`；
  - `_check_contract` 之前/之后（顺序见下）做作业校验：
    1. `require_submission_authority`（现有角色/课程/本人）；
    2. `assignment_directory.get(assignment_id)`；**未配置/不可达 → `AssignmentDirectoryUnavailable` → 路由 503**；
    3. 不存在 / `status != open` → `EnvelopeRejected("assignment_not_found")` / `("assignment_closed")`；
    4. `assignment.course_id != payload.course_id` 或 `question_version_id ∉ question_version_ids`
       → `EnvelopeRejected("assignment_mismatch")`；
    5. `section_id ∉ assignment.section_ids` → `AuthorizationError`（403）；
    6. `student_ids` 非空且 `student_id ∉ student_ids` → `AuthorizationError`（403）。
  - 文件规则：恰好 1 个文件且文件名匹配 `^[A-Za-z0-9_.-]+\.txt$`（大小写敏感的 `.txt`）；
    多文件 → `EnvelopeRejected("invalid_file_count")`；非 `.txt` → `EnvelopeRejected("file_type_not_allowed")`。
    该判定在 `_prepare_files`/`persist` 的真实字节闸门处同样执行（两条入口都走）。
  - 幂等作用域改为 `f"{assignment}:{student}:{question_version}"`（三个 UUID 都规范化；历史行的旧作用域
    保持原样，与新作用域不冲突）。
  - `RejectionReason` 增加 `assignment_not_found`、`assignment_closed`、`assignment_mismatch`、
    `invalid_file_count`、`file_type_not_allowed`；`B2/docs/architecture/interfaces.md` §2.4 同步
    （`tests/test_frozen_interfaces.py` 会解析该文档）。
- `integration/assignments.py`（**Lead 先写好**，B2 两个工作流共用）：
  - `AssignmentAudience`（assignment_id/course_id/title/status/section_ids/student_ids/question_version_ids）；
  - `AssignmentUnavailable`；
  - `AssignmentDirectory` 协议：`get(assignment_id: str) -> AssignmentAudience | None`；
  - `B3AssignmentDirectory(base_url, authorization, timeout_seconds, opener)`：GET
    `/api/v1/assignment-audiences/{id}`，404→None，其它非 200/网络/畸形→`AssignmentUnavailable`。
  - `B2Runtime.assignment_directory` 装配（由 b2-core 在 `runtime.py` 完成）。
- `intake/query.py`：`SubmissionView` 增加 `assignment_id: str | None`；`intake/api.py::_submission_view_body`
  输出该字段。
- `events/payload.py`：`submission.accepted` 的 `PAYLOAD_KEYS` 增加 `assignment_id`（**不加入
  REQUIRED_KEYS**，保证升级瞬间 outbox 里旧事件可投递）；`events/outbox.py::submission_accepted_payload`
  增加 assignment_id 参数并写入。`MAIN/contracts/asyncapi/events.yaml` 同步。
- 成绩 `grades/query.py`：
  - `GradeRow` 增加 `assignment_id: str | None`（字段顺序紧跟 `section_id`）；
  - `_Filters`/`_resolve`/`_statement` 支持 `assignment_id` 过滤（uuid、可选）；
  - `list_grades`/`iter_grades` 增加参数；`grade_row_body` 输出；`security_status` 不变；
  - `grades/export.py` 列定义加入 `作业ID`（必需列，位置在 `课序` 后），`_row` 映射
    `row.assignment_id or ""`；测试同步。
- `api/grades_routes.py`：解析 `assignment_id` 查询参数并下推。
- 文件/来信读授权：`grades/manual.py` 增加 `require_submission_read(principal, course_id, section_id, student_id)`
  （三层：已登记角色 → 令牌覆盖 course/section → 学生仅本人 / 教师在该课程范围），
  `read_submission_file`、`list_submission_files`、`get_intake_message` 使用它；
  `set_manual_grade`/`release`/`get_manual_grade` 仍使用教师/管理员专用 `_load_submission`
  （评分是写动作，学生不得调用）。`auth/authorization.py` 可放共享判定函数。
- 现有历史行：`submissions.assignment_id IS NULL` 的提交继续可评测/可查成绩（按 question_version 过滤），
  但 `?assignment_id=` 过滤不返回它们；`GradeRow.assignment_id=None`。
- `B2/plugin.py`/`contracts.py` 无需结构变化（版本号由 Lead 统一改）。

## 6. B2：多题 .txt 邮件提交

### 6.1 目标流程

学生（已认证、在目标课序/受众内）为**一个作业**申请一次性核验码 → 从账号绑定邮箱发一封邮件：

- 正文（可机器解析，标签用中/英文冒号均可）：
  ```
  核验码：AG-XXXXXXXXXXXX
  作业编号：<assignment_id 的 UUID 文本>
  课序：<课程序号/名称，人工核对用，必填非空>
  姓名：<账号 display_name>
  学号：<账号 student_number>
  ```
- 附件：1..N 个 `.txt`，文件名严格 `1.txt`/`2.txt`/…（作业题号，1 基）。允许只交部分题
  （部分提交）；不允许未知题号、重复题号、非 `.txt`。

收件 → 校验核验码/发件账号/正文/附件映射 → 一题一条提交（assignment_id+question_version_id），
共用同一封邮件的验证记录，全部进入现有 worker。重复投递幂等；失败可解释并隔离。

### 6.2 迁移 `20261107_0022_b2_email_assignment_intake.py`

- `b2.email_verification_challenges`：
  - 增加 `assignment_id uuid NULL`；
  - `question_version_id` 改为 NULL（历史行保留；新行只填 assignment_id）；
  - CHECK `ck_email_verification_challenges_target`：`assignment_id IS NOT NULL OR question_version_id IS NOT NULL`；
  - `ck_email_verification_challenges_rejection` 允许值增加：`email_body_missing_field`、
    `email_body_mismatch`、`attachment_mapping_invalid`。
- `b2.email_verifications`：
  - 增加 `assignment_id uuid NULL`、`processed_at timestamptz(3) NULL`；
  - `question_version_id` 改为 NULL；
  - 既有唯一约束保持（challenge_id 唯一、submission_id 唯一、mailbox/uidvalidity/uid 唯一）。
- 新表 `b2.email_verification_submissions`：
  - `verification_id uuid NOT NULL`、`question_version_id uuid NOT NULL`、`submission_id uuid NOT NULL`、
    `attachment_name text NOT NULL`（CHECK `char_length BETWEEN 1 AND 200`）、`created_at timestamptz(3)`；
  - `PK (verification_id, question_version_id)`；`UNIQUE (submission_id)`；`ix_..._question_version_id`。
- `b2.email_checkpoints`：约束 `ck_email_checkpoints_outcome` 改为
  `(status='processed' AND reason_code IS NULL)
   OR (status IN ('quarantined','rejected') AND submission_id IS NULL AND reason_code IS NOT NULL)`
  （一封多提交的邮件 processed 时 submission_id 为 NULL）。
- `db/schema.py` 同步；`EXPECTED_SCHEMA_REVISION=20261107_0022`。

### 6.3 核验码端点（B2 `api/email_verification_routes.py` + `intake/email_verification.py`）

- `POST /api/v1/email-verification/challenges`：
  - 认证学生 → `require_submission_authority`（本人 + course/section 覆盖）；
  - 取作业（`assignment_directory`）：不可用 → 503；不存在/已归档 → 404/409；
    `course_id != assignment.course_id` → 403；`section_id ∉ assignment.section_ids` → 403；
    `student_ids` 非空且本人不在名单 → 403；
  - 写挑战 `(student, course, section, assignment_id)`，30 分钟 TTL；响应含明文码（只出现一次）与 assignment_id。
- `GET .../{challenge_id}`：owner-only；`submissions` 从 `email_verification_submissions` 读
  （历史行回退到 email_verifications 的旧列）；`state` 判定：已消费且 `processed_at` 非空 → received；
  已消费未 processed → rejected(email_verification_rejected)；记录过拒绝 → rejected(reason)；
  过期 → expired；心跳不新鲜/目录未配置 → rejected(intake_unavailable)；否则 pending。

### 6.4 IMAP 收件（`intake/imap.py` / `email_verification.py` / 新 `intake/email_body.py`）

处理顺序（一封邮件）：

1. 大小闸门 / MIME 解析 / 缺 Message-ID 拒绝（现状不变）。
2. checkpoint 双键命中 → skipped。
3. `screen_attachments`：**只允许 `.txt`**（`ALLOWED_ATTACHMENT_EXTENSIONS={".txt"}`，其余扩展名
   `unsupported_attachment_type`，消息文案写明只接受 `.txt`）；仍然执行现有恶意内容/大小/重名/纯文本闸门。
4. 附件文件名映射校验：每个附件名匹配 `^([1-9][0-9]*)\.txt$`，题号 ≤ 作业题数；否则
   quarantine/rejected `attachment_mapping_invalid`。此步需要作业题数 → 取作业（见 6.5）。
   失败时若正文含可归属的核验码，记录该挑战的 `last_rejection_code` 供学生看到原因（不消费挑战）。
5. 解析正文：必须含 核验码/作业编号/课序/姓名/学号；缺项 `email_body_missing_field`；
   `作业编号` 规范化后必须 == 挑战 assignment_id；`姓名`/`学号` 必须等于 B4 身份端点的
   `display_name`/`student_number`（去首尾空白、折叠内部空白后精确比较）；不符 → `email_body_mismatch`。
   `课序` 只要求非空（B2 无法验证展示名；身份由核验码+账号证明）。错误同样记录挑战拒绝并隔离。
6. `try_consume_received`（行锁 + 唯一约束）：
   - 找到码 → 校验所有者 == 发件账号 → 未过期 → 首封发件地址绑定（现有规则）；
   - 插入 `email_verifications(assignment_id, attachment_names, sender, subject, student_number,
     message_id, mailbox/uidvalidity/uid, received_at)`，`processed_at=NULL`；
   - 若挑战已被**同一 mailbox/uidvalidity/uid 或同一 message_id+发件人**消费 → 返回 resume 结果
     （幂等重放，不重复写 verification）；被其它消息消费 → 拒绝（`email_verification_rejected`）。
7. 对每个映射附件：`build_submission_envelope(submission_id=uuid4, assignment_id, student_id,
   course_id, section_id, question_version_id, idempotency_key=<email 消息键 + ":" + question_version_id>,
   provenance=EmailProvenance, files=((f"{ordinal}.txt", bytes),))` → 现有 `persist`，在
   `on_persist` 钩子里写 `email_verification_submissions`（verification_id/question/submission/
   attachment_name）与 `intake_messages`（正文摘录、同一邮件可多行、code 已脱敏）。
   `persist` 命中幂等重放时补齐缺失的 mapping（单独幂等 INSERT）。
8. 全部成功后：同一事务把 `email_verifications.processed_at` 置为 now、checkpoint 置 processed
   （submission_id=NULL）；任一步失败 → 收件结果 `failed`，不写 checkpoint，下一轮重试会 resume
   已存在的 verification 并补齐剩余题目（已落库的提交按幂等键重放，不产生重复）。
9. 重复投递：同 UID 由 checkpoint 跳过；同 Message-ID 不同 UID 命中 verification 唯一键 → 幂等重放；
   同一挑战换新邮件 → 拒绝并隔离（可解释）。
10. 删除旧的固定目标/匹配规则路径：`MappingEmailMatcher`、`B2_IMAP_MATCHING_JSON`、`B2_IMAP_TARGET_JSON`
    及相关测试/部署 env 整体移除；`DirectoryEmailMatcher` 只保留 `lookup_account`（identity 目录查询）。
    无核验码的邮件 → quarantine/rejected `email_verification_rejected`，detail 说明必须携带课程页面
    申请的一次性核验码。

### 6.5 作业依赖与失败语义

- 邮件路径需要作业受众（section_ids/student_ids/title/question_version_ids），通过
  `AssignmentDirectory` 获取：
  - 依赖不可用（502/超时/畸形 JSON/未配置）→ **本轮拉取失败**（抛异常，不写 checkpoint、不隔离），
    由 pull 心跳记录错误并重试；绝不把系统故障记成学生格式错误。
  - 作业不存在（404）→ 拒绝该邮件 `email_verification_rejected`（挑战已失效）。
- 评测侧 `email_delivery`：
  - `load_email_receipt(connection, submission_id)` 改为经 `email_verification_submissions` 找到该题
    对应的 verification；`EmailReceipt.answer_attachment_name` = 映射的附件名（历史行回退旧列时可为
    None）；`EmailRequirements` 删除 `required_attachment_template`；`_attachment_outcome` 改为
    "该题存在映射答案文件则通过，否则 `output_mismatch`"。
  - 参考解自检的合成回执 JSON：删除 `attachment_names`，新增 `answer_attachment_name`（对应题有答案
    文件时为文件名，否则 null）；`reference/verification.py` 的字段集合校验同步；
    `MAIN/seed/question-bank/sample-linux/sample-linux-homework.json` 中第 1 题参考答案同步。
  - `subject`/`sender`/`email_received` 判定与依赖不可用 → `infrastructure_error` 语义不变。
- 人工隔离复核 `POST /intake/quarantines/{id}/complete`：请求体改为
  `{student_id, course_id, section_id, assignment_id, note?}`；校验教师对该隔离行可见（现有逻辑）+
  学生在作业受众内 + 作业属于该课程；取原邮件、按附件题号映射逐题创建提交（与自动路径同一
  `build_submission_envelope` + `persist`），全部成功后把隔离行标 completed、写 checkpoint、
  `resolved_submission_id` 取第一条（无映射则 422）。仍不触发"仅记归属不产生提交"的 `resolve` 语义变化。

## 7. B1：作业工作区与教师提交视图

### 7.1 类型与 hooks

- 重新生成 `B1/src/api/generated/*`（`pnpm api:generate`，不可手改）。
- `B1/src/api/types.ts` 别名随契约更新。
- `useAssignments`：`AssignmentFilter.sectionId?` 保留（查询参数仍单值），创建体改
  `{course_id, section_ids, student_ids?, title, due_at?, question_version_ids}`。
- `useGrades`/`useAllGrades`：增加 `assignmentId?: string` 过滤并透传；保持"未取全不得下结论"的
  `complete/truncated/loading` 语义。
- 新增 `features/assignments/useAssignmentRoster.ts`：按 assignment.section_ids 并行拉取各课序
  名单（现有 `useEnrollments(sectionId)`/`useQueries` 组合），合并；若 `student_ids` 非空则过滤；
  暴露 `{students, isPending, complete, truncated, error}`。
- 新增 `features/assignments/useAssignmentProgress.ts`：把作业 × 名单 × 成绩行聚合为
  `{perStudent: Map<studentId, {submitted, total, states}> , counts:{all,notSubmitted,partial,submitted,
  evaluating,failed}, complete}`。
  - 状态判定（每题取该生该题最新提交，按 `submitted_at` 取最新）：
    `未交`：无该题提交；`处理中`：queued/running/received/validated；`失败`：failed/rejected；
    `已完成`：completed。
  - 学生整体分类（互斥，用于汇总计数）：
    1. 无任何提交 → 未交；
    2. 有提交但题数不全 → 部分；
    3. 全部题已提交且存在处理中 → 评测中；
    4. 全部题已提交且无处理中，存在失败/拒绝 → 失败；
    5. 全部题已提交且全部 completed → 已交。
  - **加载未完成（grades/roster 任一 pending/truncated）时不得输出计数或 未交**；页面显示加载/未取全。
- 草稿（`features/submission/draft.ts` 改造）：键改为
  `b1.draft.v2.<owner>.<assignmentId>.<questionVersionId>`；值 `{ownerId, assignmentId,
  questionVersionId, content, updatedAt}`；只接受 `.txt` 文本；旧 v1 键在登出时一并清理（不迁移内容）。
- 提交（`useSubmission`/新建 `features/submission/useAssignmentSubmission.ts`）：
  `POST /api/v1/submissions` 请求体 `{assignment_id, student_id, course_id, section_id,
  question_version_id, files:[{path:"<ordinal>.txt", content_sha256, content_base64}],
  idempotency_key}`；每题一次提交；成功后按题展示处理进度与报告入口（复用 `ProgressPanel`/评测报告）。
  失败处理：系统故障（5xx/网络）显示"系统未完成，请稍后重试"，不得显示 0 分/未交。

### 7.2 页面

- 路由：`/assignments/$assignmentId` 按角色分流：
  - 学生 → 作业工作区（替换原 `/submit/$questionVersionId` 页面；删除该路由、SubmitPage 及旧测试）；
  - 教师 → 作业详情（含逐人逐题矩阵）。
- 学生工作区：
  - 顶部：作业标题、截止时间、总进度、**邮件提交**入口；
  - 左侧：题目列表（序号 N、标题、每题状态徽标：待提交/处理中/已完成/失败）；
  - 右侧当前题：题干（复用 SafeMarkdown + 受控图片）、答题区（textarea 粘贴或 `<input type=file
    accept=".txt,text/plain">` 选择一个 `.txt`；显示文件名与 `N.txt` 映射；超限/非 txt 前端先提示）；
    草稿自动保存（800ms 防抖）+ 恢复询问；按钮「提交本题」；提交后显示该题进度/结果/报告链接。
  - 邮件面板（弹窗或右侧 tab）：显示可复制的**完整格式**：正文五行模板（值取当前作业/学生的
    作业编号、课序、姓名、学号；核验码通过按钮申请后填入）、附件命名映射表
    （第 N 题：标题 → N.txt）、目标邮箱说明、从已绑定邮箱发送的提示；轮询挑战状态，
    收到后按 `submissions[]` 刷新每题状态并给出逐题结果链接；展示 pending/received/rejected/expired/
    intake_unavailable 的不同文案与原因码文案。
- 教师作业详情：
  - 概览：标题、状态、截止时间、目标课序（名称）、目标学生数/收窄名单、题目数、**汇总计数**：
    人数 / 未交 / 部分 / 已交 / 评测中 / 失败（数据未取全时显示"加载中/数据未取全"）；
  - 题目表：题号、题目标题、语言/执行方式（可保留现有版本+题目查询；N+1 允许，但要并行/独立错误态）；
  - 学生矩阵：行=受众学生（学号、姓名、课序、整体状态、完成题数、最近提交时间），可展开逐题：
    每题一行（状态、得分/满分、提交时间、评测运行链接、**查看答案**按钮）；
  - 查看答案：通过 `GET /api/v1/submissions/{submission_id}/files?path=<N.txt>` 取文本（带
    Authorization），在 `<pre>` 中显示（大小上限、转义显示、不写日志）；后端已做授权；
  - 学生名单/成绩/答案任一未取全时，不显示"未交"结论，显示加载或截断提示。
- 教师作业列表 `AssignmentsPage`：适配 `section_ids`（多值展示）、筛选与进度聚合改用
  `useAssignmentProgress`（按作业）或保留现有实现但改成按受众课序并行；至少保证不因单 section 假设
  而崩溃/误报。
- i18n：所有新增文案加到 `B1/src/i18n/index.ts` 活动区（不要在注释块里）；旧 `submit.*`/作业文案中
  被替换的键删除。i18n 无强制 lint，但新增键必须真实存在。

## 8. 部署 / CI / 脚本

- `MAIN/deploy/write-runtime-env.mjs`：B3 环境增加
  `B3_B4_BASE_URL: apiUrl`、`B3_B4_SERVICE_TOKEN: serviceToken`；
  `B3_JWT_ISSUER/AUDIENCE` 保持现状（生产已显式对齐 B4）。
- `MAIN/deploy/mail-env.mjs`：删除 `B2_IMAP_MATCHING_JSON`/`B2_IMAP_TARGET_JSON` 相关渲染；收信只需
  IMAP + `B2_IDENTITY_URL/AUTHORIZATION`（B4 内部身份目录）。`write-mail-env.mjs`/测试同步。
- `MAIN/deploy/mail-env.test.mjs`、`write-runtime-env` 相关测试同步（如存在）。
- `MAIN/scripts/seed_integration.py`、`import_question_bank.py` 等构造 `POST /assignments` 的脚本改用
  `section_ids`；`MAIN/seed/question-bank/sample-linux/sample-linux-homework.json` 的合成回执字段按 §6.5 更新。
- `.github/workflows/deploy-backend.yml`：
  - `verify`（无 DB）加入新的 DB-free 单测（B2 作业目录客户端、邮件正文解析、契约一致性等）；
  - 新增 `verify-database` job（ubuntu-22.04，`services: postgres:17`，真实 PostgreSQL）：
    初始化 schema/角色（用 `MAIN/infra/postgres` 脚本或 compose），运行 B2/B3/B4 的受影响的真实 DB
    测试套件（含新验收用例），超时 40 分钟；
  - `verify-frontend` 扩大为 `pnpm lint && pnpm api:check && pnpm test`（timeout 20 分钟）；
  - 分支上 `workflow_dispatch`（operation=deploy）只运行 verify 系列（deploy job 的 refs/heads/main
    条件保障不部署）。
- `MAIN/scripts/production-prepare.sh` 等 env 白名单：加入 `B3_B4_BASE_URL`、`B3_B4_SERVICE_TOKEN`。
- 文档：`MAIN/docs/architecture/unix-homework-evaluation.md` §3.5/§4 更新（附件映射、核验码绑定作业）；
  本文件为新的设计基线。

## 9. 测试矩阵（远端 CI / 隔离 Linux + 真实 PostgreSQL）

- B3：多课序+收窄学生创建；未知 section/学生 → 422；跨课程版本 → 404；草稿 → 409；重复 id → 422；
  学生/教师读授权（目标内 200、目标外 403、无权限 403、不存在 404）；internal 端点 service token；
  列表 cursor 分页；迁移 up/down。
- B4：internal 目标校验成功/未知/停用/跨课程/非法 body/错 token。
- B2（真实 PostgreSQL）：
  - 网页提交：合法（多题作业逐题）；非目标学生/课序 403；题目不属于作业 422；作业不存在/关闭 422；
    非 `.txt` 422；多文件/空文件 422；同题两个作业互不串联（幂等作用域/成绩过滤/提交视图）；
    作业目录不可用 503；历史 NULL assignment 行保留且不进入作业过滤；学生/教师文件读取授权。
  - 成绩：`?assignment_id=` 过滤、分页、GradeRow.assignment_id、导出含作业列、未完成不显示 0 分。
  - 邮件（真实 DB + 真实 TCP IMAP fixture + 真实 B4/B3 目录客户端经注入 opener）：
    多附件按题映射创建 N 条同 assignment 提交；网页与邮件同一 `(assignment, question, student)`
    提交结构一致；部分提交；重复投递幂等；同 Message-ID 幂等；新邮件重复码拒绝；
    非 `.txt`、未知题号、正文缺字段/身份不符、发件人不符、他人码、过期码 → 隔离/拒绝且
    学生状态可解释；作业目录不可用 → 拉取失败不隔离；worker 能消费这些提交。
  - 参考解/投递规则：删除附件模板后的规则单测、合成回执形状。
- B1（Vitest）：composer 多课序+指定学生与请求体；列表/详情适配；教师矩阵计数与"加载未完成不显示未交"、
  展开查看答案请求；学生工作区多题切换、粘贴/上传 `.txt`、草稿键、提交请求体（assignment_id + N.txt）、
  逐题状态；邮件面板模板与映射展示、状态轮询。删除旧 SubmitPage 测试。
- 契约：`B2/tests/test_contract_conformance.py` 锁摘要、字段一致性；B1 `pnpm api:check`。

## 10. 文件所有权（并行实现时唯一写入者）

| 工作流 | 唯一写入范围 |
|---|---|
| Lead | `MAIN/**`（contracts、deploy、docs、scripts）、`.github/workflows/**`、`B4/**`、`B2/src/autograder_b2/integration/assignments.py`（先行接口）、各模块 `module.yaml: contract_version` 与 `plugin.py` 版本号的统一收口 |

| b3-backend | `B3/**` |
| b2-core | `B2/src/autograder_b2/{db/**,alembic/**,domain/**,auth/**,grades/**,events/**,api/grades_routes.py,api/manual_grade_routes.py,intake/service.py,intake/query.py,config.py,runtime.py,worker.py}`、`B2/module.yaml: database`、`B2/tests/` 中非邮件文件、`B2/docs/**` |
| b2-email | `B2/src/autograder_b2/intake/{email_verification.py,imap.py,attachments.py,email_rules.py,email_body.py,matching.py,quarantine.py,pull.py,api.py,runtime.py}`、`api/email_verification_routes.py`、`reference/verification.py`、`B2/tests/` 中邮件/隔离/解析文件 |
| b1-frontend | `B1/**`（生成类型由该工作流在契约冻结后运行 `pnpm api:generate`） |

冲突规则：跨范围改动先 `send_message` 说明；共享文件（`B2/db/schema.py`、迁移、`config.py`、
`runtime.py`、`intake/service.py`）只由 b2-core 写，b2-email 提出请求；契约只由 Lead 写。
