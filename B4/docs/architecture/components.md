# B4 组件职责

以下组件均已实现并通过真实环境测试（PostgreSQL 17 + NATS 2.10 JetStream）。每条给：职责 / 输入 /
输出 / 依赖 / 扩展点 / 状态。

## 组件清单

### 1. 插件骨架（`已完成`）— `src/autograder_b4/plugin.py`

- 职责：注册 `B4Module`、声明 `descriptor`、装配 `router()`、`health()` 汇总依赖探针；**在装配时**
  执行注册表一致性校验（漂移即启动失败）。
- 输入：host 提供的 `context`（settings、health 钩子）。
- 输出：`ModuleDescriptor`、`APIRouter`、`HealthReport`。
- 依赖：`autograder-platform` SDK；`config`/`registry`/`security`/`api`。

### 2. 身份与凭据（`已完成`）— `security/passwords.py`、`api/auth.py`

- 职责：用户与凭据校验；Argon2id 哈希与按需提升；会话与刷新凭据句柄；失败限流。
- 输入：登录请求（用户名+密码）。
- 输出：短期 JWT（access）+ 刷新凭据（仅 `HttpOnly`/`Secure`/`SameSite=Strict` cookie）。
- 依赖：`b4.users`/`credentials`/`sessions`/`login_attempts`；Ed25519 私钥。
- 扩展点：哈希算法（`hash_algorithm` 列驱动，可校验并提升 bcrypt 遗留行）。

### 3. 会话与令牌（JWKS）（`已完成`）— `security/keys.py`、`security/tokens.py`

- 职责：Ed25519 签名与验签；`kid` 由公钥指纹确定；JWKS 端点暴露公钥；本地验签所需声明
  （`iss`/`aud`/`exp`/`nbf`/`jti`/`role`/`scope`）。
- 输入：私钥文件（`JWT_PRIVATE_KEY_FILE`，不进仓库）；令牌。
- 输出：签名令牌、JWKS JSON、验签结果。
- 依赖：`cryptography`（Ed25519）、`PyJWT`（EdDSA）。

### 4. 角色与作用域授权（`已完成`）— `authorization.py`、`api/dependencies.py`

- 职责：学生/教师/助教/管理员四类角色（与 `common.yaml` 的 `Role` 枚举同源）；服务端判定；
  课程作用域解析；拒绝写审计。
- 输入：已验证令牌的 `sub` + 请求资源 `course_id`/`section_id`（角色与作用域**每请求重读库**）。
- 输出：放行 / `401` / `403` / `404`。
- 依赖：`b4.roles`、`b4.enrollments`、`b4.sections`、`b4.courses`。
- 扩展点：课程级授予的写入路径（当前：建课 + 运维命令；HTTP operation 需契约变更）。

### 5. 课程 / 课序 / 选课（`已完成`）— `api/courses.py`

- 职责：`courses`/`sections`/`enrollments` 主数据 CRUD，课程作用域约束，乐观并发（`expected_updated_at`），
  游标分页。
- 输入：教师/管理员请求。
- 输出：课程/课序/选课资源；越权 `403`；版本冲突 `409`。
- 依赖：`b4` 相关表；身份组件。

### 6. 名单导入管道（`已完成`）— `roster/**`

- 职责：解析（PDF/XLSX）→ 列映射 → 逐行校验 → 预览 → 人工确认 → 原子写入；不确定即停下；被拒行不静默丢弃。
- 输入：名单文件（`media_type` 由**魔数**判定）、列映射选择、确认指令。
- 输出：预览 DTO、确认结果、`roster.import.completed` 事件（不含 PII，同事务入 outbox）。
- 依赖：状态机（见 `models.md`）；`b4.roster_*` 表；outbox。
- 扩展点：**解析器按 `media_type` 注册**——新增文件类型不改核心管道，只加解析器与媒体类型。
- 输入防护：上传大小上限、行/列上限、单元格截断、XLSX 解压炸弹预检（zip 声明大小上限）、PDF 页数上限。

### 7. 审计（`已完成`）— `audit.py`、`api/audit.py`

- 职责：敏感操作（认证、用户/角色管理、建课/建课序、名单导入、授权拒绝）写审计记录。
- 输入：操作者、目标、变更前后**摘要**（SHA-256 + 字段名）、时间、`request_id`/`correlation_id`
  （默认取当前请求上下文）。
- 输出：`b4.audit_records`；审计查询（管理员全量，教师/助教限本课程）。
- 依赖：`b4.audit_records`；日志上下文。

### 8. DB 供给与健康（`已完成`）— `ops/provision.py`、`db/engine.py`

- 职责：PostgreSQL 最小权限角色/schema 供给、真实 readiness 探针、备份/恢复手册与演练。
- 输入：运维触发；`DATABASE_URL`（应用角色）/`B4_ADMIN_DATABASE_URL`（仅供给）。
- 输出：角色与 schema 就绪、三依赖 readiness 结果、备份/恢复产物。
- 依赖：PostgreSQL 17；权限矩阵见 `../runbooks/database-operations.md`。

### 9. JetStream 运维（`已完成`）— `messaging/**`、`ops/messaging.py`

- 职责：stream/durable consumer 创建与对账、outbox 中继、inbox 去重、DLQ 记录与重放、配额与保留。
- 输入：`asyncapi/events.yaml` 契约；outbox 待发事件；advisory。
- 输出：已发布事件、`b4.dlq_messages` 可查可重放。
- 依赖：NATS 2.10 + JetStream；`event-envelope`（去重键 `event_id`）。
- 边界：**不自造 domain subject**；DLQ 用 JetStream 自身的 advisory 主题。

### 10. 模块运行期注册表（`已完成`）— `registry.py`、`api/modules.py`

- 职责：发现已装载模块、聚合 `descriptor`、聚合健康、暴露路由/事件/能力/迁移 head 查询；与
  `module.yaml`+`descriptor`+契约摘要一致。
- 输入：host 装载的模块对象（`app.state.modules`）+ 各模块 `module.yaml` + `MAIN/contracts`。
- 输出：注册表查询端点（管理员全量、公开仅存活）。
- 依赖：SDK `descriptor`；一致性校验（漂移即失败）。
- 边界：**不修改、不重定义** MAIN 契约（ADR-0003）。

## "核心不改、扩展可加"边界

- **核心**（一次实现，之后少改）：认证验签、授权判定、名单导入状态机、审计写入、注册表一致性校验、
  outbox、脱敏日志。
- **可扩展**：解析器（按 `media_type`）、密码哈希算法、审计动作词表、注册表查询维度、
  角色/作用域取值集（冻结后经契约变更）。
- 新增扩展不修改核心状态机与校验逻辑。
