# 作业工作流：课程 → 课序 → 班级 → 名单、课程级作业与作业通知（v0.32.0）

本文件是「课程/课序/班级/名单 → 教师发布课程级作业 → 学生网页/邮件提交 → 教师查看成绩与答案 →
发布通知真实投递」这一轮改造的**设计基线**。字段的机器可读权威是 `MAIN/contracts/**`（契约版本
`0.32.0`）；本文件解释语义、边界与失败处理。实现与本文件冲突时，以契约 + 本文件为准。

## 1. 核心决策（不可协商）

1. **层级是 课程 → 课序（section）→ 班级（class）→ 名单行（enrollment）**。
   - `b4.courses` 一门课；`b4.sections` 一个课序；`b4.classes` 某课序下的一个班级；
     `b4.enrollments` 一行 = 「某学生在这门课的这个课序的这个班」。
   - 一个课序可含多个班级；一个课程可含多个课序。旧模型「一个班 = 一个课序」整体移除
     （`POST /api/v1/courses/{course_id}/classes` 删除）。
2. **同一学生同一课程最多一个有效名单行**。最终权威是数据库部分唯一索引
   `uq_enrollments_course_student_active ON (course_id, student_number) WHERE is_active`；
   注册绑定、名单导入、班级挂接都在事务内先预检冲突，并发下 `IntegrityError` 必须翻译成清晰的
   409，**不得 500、不得静默丢弃/选择某一行**。跨课程的同一学生合法。
3. **作业是课程级的**。`b3.assignments` 只属于一门课程，覆盖该课程**当前及以后**的全部课序/班级；
   没有 `section_ids`/`student_ids`，没有手选课序与收窄学生。发布后新增的合法学生与新增课序/班级
   仍能看到进行中的作业。
4. **受众判定以 B4 当前有效归属为权威，不以令牌 scope 快照为准**。B3/B2 在每次读/提交请求上调用
   内部端点 `GET /api/v1/internal/users/{user_id}/authorization`，用其 `enrollments` 判定课程归属；
   教师写路径仍使用令牌角色 + 课程 scope。这样「注册后立即选上课」和「移出课程立即失权」都不需要换 token。
5. **一次提交 = 一道题**。网页与邮件都只接受一个 `.txt` 答案文件，文件名为作业内 1 基题号
   （`1.txt`、`2.txt`…）。邮件必须携带一次性核验码（绑定作业；`course/section` 由服务端按权威归属
   覆盖，客户端字段不被信任），核验码与正文/发件人/附件映射校验都在收件链路完成；重复投递幂等，
   失败可解释并隔离。
6. **一次发布产生一次通知受众快照**。作业发布时 B3 同步调用 B4 内部端点按**当时的课程有效名单**
   物化收件人与状态；通知状态（`notification_state`）与投递状态是两个事实：「已安排」不等于
   「已送达」。发送由独立进程 `python -m autograder_b4.ops.notifications` 走真实 SMTP 完成，
   每人的结果落库可查，失败/重复投递与三类 skipped 原因必须可区分。
7. **不降级**。B4 authorization / B3 作业目录 / NATS / 通知端点不可用时，提交、核验码、邮件拉取、
   通知安排必须如实报基础设施错误（503 / 该轮失败重试 / `notification_state=unavailable`），
   **绝不能**记成「学生没交」「学生格式错误」「0 分」或「已送达」。
8. 不猜测、不静默修复历史数据：迁移前若已存在同一课程同一学生的多条有效名单行，迁移必须**失败并
   保留数据**（只报告计数与短 id），不得自动选择/删除；历史提交没有 `assignment_id` 的保持 NULL。

## 2. B4：领域模型、数据库与 API

### 2.1 表与约束

- `b4.users`：`student_number` 唯一，`email` 可空，`is_active` 表示账号是否可登录。
- `b4.courses`、`b4.sections(section_id, course_id, section_code, term)`。
- `b4.classes`：`class_id`，`section_id`（FK `ON DELETE CASCADE`），`class_name`，
  `source ∈ {registered_class, roster_import, manual}`，`allowlist_batch_id`（FK `ON DELETE SET NULL`），
  `created_by`，时间戳；约束 `UNIQUE (class_id, section_id)`、`UNIQUE (section_id, class_name)`、
  部分唯一 `UNIQUE (section_id, allowlist_batch_id) WHERE allowlist_batch_id IS NOT NULL`、
  `CHECK (char_length(class_name) BETWEEN 1 AND 64)`。
- `b4.enrollments`：`course_id NOT NULL`、`class_id NOT NULL`、`section_id`、`student_number`、
  `display_name`、`user_id`（NULL = 未注册）、`source`、`is_active`；复合外键
  `(class_id, section_id) → classes(class_id, section_id)`、`(section_id, course_id) → sections(section_id, course_id)`；
  **部分唯一 `(course_id, student_number) WHERE is_active`** 是最终权威。
- `sections` 增加 `UNIQUE (section_id, course_id)` 供复合外键。
- 迁移（B4 `20261108_0010`，一篇）：建 `classes`、加唯一键与列、回填（每个现有 section 恰好建 1 个
  班级：最近一次 committed roster import 的文件名去扩展名 → 否则唯一 allowlist batch 的班名 →
  否则 `section_code`；`source` 相应为 `roster_import`/`registered_class`/`manual`）、回填
  `enrollments.course_id/class_id` 后 `SET NOT NULL`、**前置冲突检查**、删除旧的
  `UNIQUE (section_id, student_number)` 改为部分唯一。`downgrade` 至少可逆到删新表/列（不做数据回填逆向，
  在迁移 docstring 写明）。
- 通知表（同迁移 `20261108_0010`）：`b4.assignment_notifications`（`assignment_id UNIQUE`，
  `status ∈ pending/sending/completed/partial/failed`，计数快照与时间戳）与
  `b4.assignment_notification_deliveries`（每收件人一行，`UNIQUE (notification_id, enrollment_id)`，
  `status ∈ pending/sending/sent/failed/skipped_unbound/skipped_no_contact/skipped_inactive`，
  `reason_code ∈ smtp_error/mail_not_configured/no_account/no_email/inactive_account`，
  `attempts`、`next_attempt_at`、`sent_at`）。**名单行删除后投递记录保留**（历史证据）。

### 2.2 课程/课序/班级维护

- 保留课程与课序的增删改查；删除课序级联其班级与名单（作业是课程级，不再有目标悬空）。
- `GET /api/v1/sections/{section_id}/classes`、`POST /api/v1/sections/{section_id}/classes`
  （body `ClassCreate{class_name, batch_id?}`）、`PATCH/DELETE /api/v1/classes/{class_id}`。
  - `batch_id` 存在时把该「已注册名单」（allowlist batch）成员复制为该班的 enrollment
    （`source='registered_class'`），已存在的账号按学号立即绑定；班名可覆盖，缺省取文件名去扩展名。
  - 同一 batch 重复挂到同一 section 幂等返回已有班级（不得重复插行）。
  - 任何成员已在**同一课程的另一个课序/班级**有效在册 → 409（聚合信息：冲突学生数 + 冲突班级/课序），
    整请求回滚，不写半套数据。同名班级 → 409。
- `GET /api/v1/classes/{class_id}/enrollments`（分页）。
- `GET /api/v1/courses/{course_id}/enrollments?section_id=&class_id=&cursor=&limit=`：**全课程名单**
  分页，每行含 `course_id/section_id/class_id/class_name`；教师详情必须翻完全部页，含
  `student_id=null` 的未注册行。
- `GET /api/v1/courses/{course_id}/roster-summary`：服务端 SQL 聚合的课程/课序/班级计数
  （total/bound/unbound），不接受客户端拼页累加。
- 名单导入：`POST /api/v1/roster-imports` multipart `file, course_id, section_id, class_id`；
  服务端校验 class ∈ section ∈ course，不合法 422/409；confirm 原子写入带 `course_id/class_id` 的
  enrollment。行级拒绝原因新增 `conflict_other_section`、`account_name_mismatch`。
- `EnrollmentPublic` 增加 `course_id/class_id/class_name`；`SectionPublic` 增加 `class_count`，
  删除 `class_name`（课序不再等同班级）。

### 2.3 注册与身份一致性

- 自服务注册：管理员先上传 allowlist（`姓名/学号` CSV，每个文件一个班），学生请求验证码
  （真实 SMTP 送达，缺失/失败即 503，绝不跳过验证），完成后建号并可立即登录。
- `registration.create_account` 在**同一事务**绑定全部 `user_id IS NULL AND is_active` 且学号相同的
  enrollment 行；逐行比较 `display_name`（trim 后）与注册姓名：**不一致的行不绑定**并计入
  `identity_mismatch_count`；响应返回 `bound_course_count`（按课程计）与 `identity_mismatch_count`。
  不得把不同人按学号误合并；认领错误由教师修名单，不由注册静默改写。
- `GET /api/v1/me` 增加 `memberships: [MyMembership]`（本人当前有效课程/课序/班级；无则空数组）。
- 内部端点 `GET /api/v1/internal/users/{user_id}/authorization`（serviceToken）→
  `{user_id, platform_roles, course_grants, enrollments:[{course_id, section_id, class_id}]}`：
  只返回 ID，不含姓名/学号/邮箱；停用账号返回空角色/空选课。删除旧的
  `POST /api/v1/internal/course-targets/validate`（目标模型消失后无调用方）。

## 3. B3：课程级作业

- 迁移 `20261108_0010`（B3）：DROP `b3.assignment_sections`、`b3.assignment_students`；`assignments`
  增加 `notification_state text NOT NULL DEFAULT 'pending' CHECK in ('pending','scheduled','unavailable')`
  与 `notification_scheduled_at timestamptz NULL`。同步 `module.yaml` 迁移头与相关文档。
- 契约：`AssignmentPublic = {assignment_id, course_id, title, status, due_at, question_version_ids,
  notification_state, created_at, updated_at}`（删除 `section_ids/student_ids`）；
  `CreateAssignment = {course_id, title, due_at?, question_version_ids}`；
  internal `AssignmentAudience = {assignment_id, course_id, title, status, question_version_ids}`。
- 创建流程：课程写权限（token）→ 校验每题版本存在、属于同课程且已发布（否则 404/409）→ 事务写
  assignment + items → 提交后同步调用 B4 `POST /api/v1/internal/assignment-notifications`
  （`B3_B4_BASE_URL` + `B3_B4_SERVICE_TOKEN`，`published_at=assignment.created_at`）→ 独立事务落
  `notification_state`：成功 `scheduled`；未配置/网络/5xx/B4 4xx → `unavailable`。通知失败**不回滚**
  作业（作业已发布是事实），教师可点重试。
- 重试端点 `POST /api/v1/assignments/{assignment_id}/notifications`（teacher/admin + 课程写权限）：
  幂等调用 B4 并返回最新 `AssignmentPublic`；B4 明确 404/409/422 时同样落 `unavailable` 并把该状态码
  向上映射；网络/5xx → 200 + `unavailable`。
- 读授权：教师按课程写/读 scope；学生每次请求读取 B4 authorization 的当前 `enrollments`，列表用课程
  集合在 SQL 过滤、详情不在集合 → 403。B4 不可达 → 503（不得当 403/空列表）。列表删除 `section_id`
  查询参数，保留 `course_id`；`notification_state` 出现在所有公开 DTO 与列表中。

## 4. B2：提交、邮件与成绩按课程级受众

- 内部受众模型改为 §3；删除 `AssignmentAudience.section_ids/student_ids/is_target` 与
  `TargetValidator` 目标校验。
- 新增 B4 权威授权客户端 `integration/b4_authorization.py`：`GET {base}/api/v1/internal/users/{user_id}/authorization`，
  base/auth 复用 `B2_IDENTITY_URL` / `B2_IDENTITY_AUTHORIZATION`；不可达 → 503（不得当成无权限）。
- 网页提交 `IntakeService.accept`：
  1. 契约/限制/文件校验不变；
  2. 身份：`student_id` 必须等于 token 主体（学生）或 staff 代交；管理员豁免；令牌仍须覆盖请求
     `course_id`（section 不再参与授权）；
  3. **权威归属**：取 token 主体（代交时取被代交学生）的 B4 authorization：平台角色 teacher/admin 且
     `course_id ∈ course_grants` → staff 允许；否则 `enrollments` 必须含 `course_id`，取其
     `section_id` 作为**权威 section**（覆盖请求里的值）；无归属 → 403（不泄露作业是否存在）；
  4. 作业受众：作业存在/open/题目属于该作业（422/409 语义不变）；
  5. 答案文件 `N.txt` 规则不变。持久化使用权威 course/section/student。
- 邮件核验挑战：请求保留 `{course_id, section_id, assignment_id}` 字段，但 `section_id` 被忽略，
  挑战绑定权威 `(student, course, section, assignment)`；收件时再次按当前归属校验（被移出课程 →
  隔离为 `email_verification_rejected`；换课序 → 用当前权威 section 落库）。
- 邮件入口沿用同一 `IntakeService.persist`；同一学生的网页与邮件提交在作业/题目/课程/课序上同口径。
- 人工复核 `resolveQuarantine`：服务端校验作业的 course 与请求 course 一致，并用 B4 authorization
  校验/补全学生 section（不一致 → 422）。
- 成绩（`grades.query`/`grades.export`）：学生读者增加权威归属检查（课程不在当前 enrollments → 403）；
  行过滤仍按本人谓词；staff 保持 token scope 口径。
- 契约 `b2.yaml`：更新描述与 `EmailVerificationChallengeRequest` 语义；响应结构不变；b2 schema 无迁移。

## 5. B4：作业发布通知

- 内部端点 `POST /api/v1/internal/assignment-notifications`（serviceToken）body
  `{assignment_id, course_id, title, due_at?, published_at}`；按 `assignment_id` 幂等：首次 201
  物化受众并返回 summary，重复（同 course）200 原样返回、不重建不重发，同 id 不同 course → 409。
- 受众 = 该课程当前全部 `is_active` 名单行；逐行判定：未绑定账号 → `skipped_unbound/no_account`；
  账号停用 → `skipped_inactive/inactive_account`；无 email → `skipped_no_contact/no_email`；
  其余 `pending`。计数在 summary 中如实返回。
- 教师端点（课程 staff）：`GET /api/v1/courses/{course_id}/assignments/{assignment_id}/notifications`
  → `{summary, deliveries: CursorPage<...>}`（无记录 404）；`POST .../notifications/retry` 只把
  `failed` 重置为 `pending`（`attempts=0,next_attempt_at=now`），campaign 回 `pending`，skipped 不动。
- 发送进程 `python -m autograder_b4.ops.notifications` 单趟：取 `pending` 或
  `failed AND next_attempt_at<=now AND attempts<3`（`FOR UPDATE SKIP LOCKED`，上限 50），claim 时
  `attempts+1` 置 `sending`，用 `security.mailer` 发真实邮件：成功 `sent`；失败 `failed` +
  `reason_code='smtp_error'`（只记异常类型）+ 退避 `next_attempt_at=now+60s*2^(attempts-1)`；
  SMTP 未配置 → `failed/mail_not_configured`。每趟结束按投递行重算 campaign status：
  有 pending/sending → `pending/sending`；无 pending 且 sent>0 且 (failed>0 或 skipped>0) → `partial`；
  sent>0 且无 failed/skipped → `completed`；sent==0 且 failed>0 → `failed`；sent==0 且 failed==0 →
  `completed`（全部 skipped 由计数如实展示）。输出一行 JSON 计数（claimed/sent/failed/skipped/recovered），
  退出码 0=本趟完成、1=基础设施失败、2=配置错误。
- 邮件正文只含：学生姓名、课程名、作业名、截止时间（无则「未设置」）、提示登录平台查看提交；
  不含他人信息、token/链接凭据。
- 生产部署：`MAIN/deploy/autograder-notifications.service` + `.timer`（`OnUnitActiveSec=1min`）随发布
  安装；`deploy-release.sh` 安装并 `enable --now` timer，失败必须让发布失败（不得静默）。

## 6. B1：界面

- 作业创建只选课程/名称/截止/已发布题目；预览显示课程、覆盖课序/班级数、学生总数
  （`roster-summary`）、题目数，并说明「发布后对该课程全部课序/班级学生生效」。
- 教师作业详情：名单改为 `GET /courses/{id}/enrollments` 全课程自动翻页（保留
  `MAX_AUTO_PAGES` 截断纪律）；矩阵保留未注册（unbound）行；列显示课序/班级/通知状态；顶部计数来自
  `roster-summary`；新增「作业通知」面板（summary + deliveries、失败重试、显示 sent/pending/failed/
  skipped 与每人原因），任何「已安排」不得显示成「已送达」。
- 名单页：课程 → 课序列表（班级数/人数）→ 班级列表（班名/人数/已绑定）→ 学生；操作：新建课序、
  新建班级、把「已注册名单」中的班级导入到指定课序、重命名/删除班级；名单导入先选课程→课序→班级。
  删除旧 `useAttachRegisteredClass`（`/courses/{id}/classes`）调用。
- 学生视图：`GET /me` memberships 展示课程/课序/班级，多课程可切换；作业列表/工作区去掉
  `section_ids/student_ids` 依赖，提交请求仍从本人 membership 取 course/section。
- i18n 删除废弃 key；`pnpm typecheck`/`pnpm lint` 必须通过。

## 7. 契约、部署与 CI

- 契约版本 `0.32.0`：common/b2/b3/b4/b5/asyncapi 同步 `info.version`；
  `MAIN/contracts/CHANGELOG.md` 追加一条；摘要由 `python MAIN/scripts/relock_contracts.py` 计算并写入
  五份 lock（不手填）。
- `.github/workflows/deploy-backend.yml`：
  - `verify`（无 DB）：ruff（含 acceptance 脚本与新增生产代码）、acceptance 脚本
    `py_compile`、通知 timer 单元文件与安装脚本引用检查、契约 lock/版本检查、host/module DB-free
    测试不变；
  - `verify-database`（真实 PostgreSQL + NATS）：B2/B3/B4 受影响测试套件（B4 覆盖 classes/roster、
    notification 状态机、registration binding、internal identity/authorization 等）；
  - 验收 job：真实 PostgreSQL，迁移后启动 composed host；host 的 `SMTP_*` 指向验收脚本在
    `ACCEPTANCE_SMTP_PORT` 上启动的真实 TCP SMTP sink（`SMTP_TLS_MODE=none`），migrate、IMAP fixture
    与邮件拉取 CLI 照旧；随后运行 `MAIN/scripts/acceptance_assignment_workflow.py`。
- 验收脚本覆盖（设计 §6）：一门课程两个课序、每个课序两个班级、另一门课共享同一学生；先导入后注册与
  先注册后导入；同课程第二课序/班级 409 且数据不变；并发 attach/import 不破坏唯一有效归属；
  一次发布全课序/班级可见、他课不可见且读取/提交 403；发布后新增学生/课序可见；教师详情含未注册行且
  分页完整；移出课程后读取/提交/核验码/成绩及时 403；网页与邮件同口径；通知全受众 + 三类 skipped +
  真实失败/重试/重复投递可区分；数据库唯一约束与旧冲突拒绝路径（迁移前置检查本身由 B4 迁移测试覆盖）。
- 未在本地执行 build/pytest/compose/部署（用户指示）：本地只做静态检查（B1 typecheck、各包 ruff、
  acceptance 脚本 py_compile、契约 relock --check），其余由 push main 后的 CI + 自动部署 + 线上验收
  覆盖。

## 8. 验收矩阵（远端 CI / 真实 PostgreSQL）

| 主题 | 断言 |
|---|---|
| 层级与冲突 | 课程/课序/班级/名单 CRUD；同课程第二课序/班级 409；并发 attach/import 无 5xx 且唯一有效行恰好一条；数据库部分唯一索引与直接重复 INSERT 拒绝 |
| 注册绑定 | allowlist + 真实 SMTP 验证码；先导入后注册绑定等待行；先注册后导入立即绑定；姓名不一致不绑定且计数如实；`/me` memberships |
| 课程级作业 | 创建无 section/student 目标；发布后全课序/班级学生可见；他课学生列表不可见、详情/提交/核验码 403；发布后新增学生与新 section/class 可见；B4 不可达 503 |
| 名单与汇总 | 全课程名单分页翻完、含未注册行；`roster-summary` 与分页一致；section/class、unbound 计数 |
| 提交与邮件 | 网页与邮件同 assignment/题号/权威 course+section；请求伪造 section 被权威值覆盖；移出后 web/邮件/成绩 403；答案文件读权限 |
| 通知 | 全有效受众物化；三类 skipped 原因；真实 SMTP 送达数与 summary/deliveries 一致；真实失败→failed/smtp_error；retry 只重置 failed；重复单趟与重复内部端点幂等、不重复投递 |
| 迁移/部署 | B4 迁移回填与前置冲突检查（B4 迁移测试）；通知 timer 单元文件存在且发布脚本安装；线上部署后 timer 实际运行 |

## 9. 所有权

| 范围 | 所有者 |
|---|---|
| `MAIN/contracts/**`、契约锁与版本、`MAIN/deploy/**`、本文件与 MAIN 文档 | Lead（MAIN） |
| `B4/**`（身份、课程/课序/班级/名单、注册绑定、内部授权与通知、迁移、测试） | Lead |
| `B3/**`（课程级作业、通知触发状态、B4 授权客户端；迁移与测试） | B3 |
| `B2/**`（提交/邮件/成绩的课程级权威归属；邮件与测试） | B2 |
| `B1/**`（作业创建/教师详情/名单页/学生视图；生成类型） | B1 |
| `MAIN/scripts/acceptance_assignment_workflow.py`、`.github/workflows/deploy-backend.yml` | MAIN 验收与文档实现者 |

跨范围改动先说明；共享文件（契约、迁移头、版本号）由对应所有者统一收口。**不提交、不 push、不建分支**
由 Lead 执行。
