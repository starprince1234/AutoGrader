# B4 数据库 schema（表意图）

> 铁律一：**不抄 DDL**。物理表结构的唯一权威是本模块 `../alembic/versions/**`；本文只写表意图、关键约束、生命周期、敏感列标记。表已由 `20260915_0002` 建成并实测（见 `../phases/active/phase-01-platform-implementation.md`）。

## 归属声明

- 我拥有 `b4` schema 的 8 类平台实体，物理表为 **15 张业务表 + `alembic_version_b4`**。
- **B2/B3 物理表不归我**：我不代管、不读写、不迁移它们（见 `../../MAIN/docs/database/ownership.md`）。
- 我只负责实例、角色、schema、备份、健康；应用连接用最小权限角色，禁止超级用户。

## 实体 ↔ 表名对照

`../../MAIN/docs/database/ownership.md` 与 `../AGENTS.md` 用**单数实体名**描述归属；`table-standards.md` §1
要求**表名为复数 snake_case**。两者不是两套模型，而是实体名与表名的对照：

| 实体名（归属文档） | 表名（物理） | 主键列 |
|---|---|---|
| `user` | `users` | `user_id` |
| `credential` | `credentials` | `credential_id` |
| `session` | `sessions` | `session_id` |
| `role` | `roles` | `role_id` |
| `course` | `courses` | `course_id` |
| `section` | `sections` | `section_id` |
| `enrollment` | `enrollments` | `enrollment_id` |
| `audit` | `audit_records` | `audit_id` |
| — | `roster_imports` | `roster_import_id` |
| — | `roster_import_files` | `file_id` |
| — | `roster_import_rows` | `row_id` |
| — | `outbox_events` | `outbox_id` |
| — | `inbox_events` | `inbox_id` |
| — | `dlq_messages` | `dlq_id` |
| — | `login_attempts` | `attempt_id` |

主键列名由 `src/autograder_b4/ops/table_standards.py` 的 `PRIMARY_KEY_COLUMNS` **显式声明**并被自动检查：
从表名推断实体名会把 `audit_records` 推成 `audit_record_id`，那是猜而非规范。未声明的表直接判违规，
迫使新表明确选择实体名。

## 表清单

| 表 | 用途 | 关键约束 | 生命周期 |
|---|---|---|---|
| `users` | 平台用户 | `user_id` UUID 主键；`username` 唯一（小写规范化）；`student_number` 唯一（可空） | 管理员创建或名单导入时匹配；停用为软删除（`is_active=false` + `deactivated_at`），不物理删除 |
| `credentials` | 登录凭据 | 一行一用户（`uq_credentials_user_id`）；`hash_algorithm ∈ {argon2id, bcrypt}`；只存 PHC 串 | 随用户创建；登录成功时按需提升为 Argon2id |
| `sessions` | 会话/刷新凭据句柄 | `refresh_token_hash` 唯一且为 SHA-256（CHECK 正则）；`family_id` 聚合轮换链；`replaced_by` 自引用 | 登录创建；刷新轮换；注销/过期/重用检测时撤销 |
| `roles` | 角色授予 | `role ∈ {student, teacher, assistant, admin}`；`(user_id, role, course_id)` 唯一（`NULLS NOT DISTINCT`）；`admin` 必须 `course_id IS NULL` | 平台角色由管理员授予；课程级授予由建课或运维命令写入 |
| `courses` | 课程主数据 | `course_id` UUID 主键；`course_code` 唯一 | 教师/管理员创建；`is_active` 软删除 |
| `sections` | 课序（课程下的开班） | `section_id` UUID 主键；`(course_id, section_code, term)` 唯一（`NULLS NOT DISTINCT`）；FK 指向 `courses`（同 schema） | 课程写权限者创建 |
| `enrollments` | 选课关系（学生 × 课序） | `enrollment_id` UUID 主键；`(section_id, student_number)` 唯一；`source ∈ {roster_import, manual}`；FK 指向 `sections`/`users`/`roster_imports` | 名单导入确认或人工维护；可置 `is_active=false` |
| `audit_records` | 敏感操作审计 | `audit_id` UUID 主键；`occurred_at` timestamptz；`outcome ∈ {success, denied, failure}`；`change_digest` 为 SHA-256（CHECK 正则） | 只追加，不修改不删除 |
| `roster_imports` | 导入状态机 + 预览元数据 | `status` 为契约枚举；`content_sha256` 为 SHA-256；`committed` 与 `committed_at` 必须同时成立（CHECK）；`idempotency_key` 部分唯一索引 | 上传创建；确认后终态 |
| `roster_import_files` | 上传产物字节 | 一行一导入（`uq_roster_import_files_roster_import_id`）；上传大小受 `B4_ROSTER_UPLOAD_MAX_BYTES` 限制 | 随导入；`ON DELETE CASCADE` |
| `roster_import_rows` | **暂存**的逐行解析结果 | `(roster_import_id, row_number)` 唯一；`status ∈ {accepted, rejected}`；`rejected` ⟺ `reason` 非空（CHECK） | 每次校验重写；**确认前的 roster 数据只存在于此表** |
| `outbox_events` | 待发事件 | `event_id` 唯一；`event_type` 必须匹配 `^autograder\.v1\.`；`producer` 匹配 `^B[1-4]$` | 与业务变更同事务插入；中继发布后置 `published_at` |
| `inbox_events` | 已消费投递账本（去重） | `(consumer, event_id)` 唯一 | 首次投递插入；重复投递命中唯一键 |
| `dlq_messages` | 死信存储 | `(original_stream, original_sequence)` 唯一（`NULLS NOT DISTINCT`）；`envelope` 可空（advisory 来源为空） | 记录后可按 `replayed_at` 标记重放 |
| `login_attempts` | 登录失败限流账本 | `principal_key`/`client_key` 均为 SHA-256（CHECK 正则），**不存原始用户名/IP** | 每次登录写一行；窗口外记录被清理 |

## 角色—用户关系

- `roles` 一张表同时表达两种授予：
  - **平台级**（`course_id IS NULL`）：`admin` 只可能出现在这一侧；
  - **课程级**（`course_id` 非空）：`teacher` / `assistant`，由 `createCourse`（创建者）或运维命令
    `python -m autograder_b4.ops.roles grant` 写入。
- 学生的课程可见性来自 `enrollments`（而非 `roles`）。
- 取值集合（学生/教师/助教/管理员）为冻结串行点，与
  `../../MAIN/contracts/openapi/common.yaml` 的 `Role` 枚举一致；新增取值走契约变更。

## 私有 / 敏感列标记

| 表 | 敏感内容 | 处理要求 |
|---|---|---|
| `credentials` | 密码哈希 | 永不进 API DTO、日志、事件；只比对，不还原 |
| `sessions` | 刷新凭据摘要 | 只进 `HttpOnly`/`Secure`/`SameSite` cookie；不进响应体、日志 |
| `login_attempts` | 主体/客户端标识 | 只存 SHA-256，不存明文用户名或 IP |
| `users` | 姓名、学号、邮箱等 PII | 公开 DTO 最小化；日志脱敏；仅授权者可读 |
| `roster_import_rows`、`roster_import_files` | 名单原始行与文件字节 | **确认前**只存于此，不进 `enrollments`；`RosterRowPreview` 只返回给授权调用者 |
| `audit_records` | 变更前后**摘要** | 只存 SHA-256 摘要与字段名，不存值 |

## 禁止事项（重申）

- 禁止跨 schema 外键 / SQL / ORM / 视图；禁止 `public` 共享 ORM；禁止超级用户跑应用。
- 所有表结构变更必须走迁移（见 `migrations.md`），禁止手工改库。
- 自动检查：`uv run python -m autograder_b4.ops.table_standards`（命名、主键、timestamptz、跨 schema FK）。
