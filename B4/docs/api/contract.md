# B4 API 契约（我提供 / 我消费）

> 铁律一：本文件**只写关系，不抄字段表**。字段、路径参数、请求/响应体的唯一权威是 `MAIN/contracts/**`；版本与摘要见本仓库 `../contracts.lock`。

## 契约权威

- OpenAPI：`../../MAIN/contracts/openapi/b4.yaml`（版本与摘要以 `../contracts.lock` 为准，本模块不写死；**已覆盖 B4 全部端点**：`createSession`、`refreshSession`、`deleteSession`、`getJwks`、`listUsers`/`createUser`/`updateUser`/`deleteUser`、`listCourses`/`createCourse`、`listSections`/`createSection`、`listEnrollments`/`updateEnrollment`/`deleteEnrollment`、`createRosterImport`/`getRosterImport`/`setRosterColumnMapping`/`confirmRosterImport`、`listAuditRecords`、`listRegisteredModules`/`getModuleHealth`）。
- 通用错误：`../../MAIN/contracts/openapi/common.yaml`（`ProblemDetails`）。
- 名单导入载荷：`../../MAIN/contracts/schemas/roster-import.schema.json`。
- 事件 envelope：`../../MAIN/contracts/schemas/event-envelope.schema.json`。
- 事件契约：`../../MAIN/contracts/asyncapi/events.yaml`。
- 锁文件：`../contracts.lock`（`contract_version` 与 `digest` 由 MAIN 的契约流程推进，本模块只读、不修改）。

> 版本沿革（本模块相关的部分，数字是各次变更当时的版本，不代表当前值）：补 `EnrollmentPublic` +
> `student_id`（CHG-0007）、声明文档级 `security` 与 `bearerAuth`（CHG-0006）、B3 写路径（CHG-0008）、
> 打开模块名 pattern（CHG-0009 / ADR-0013）、新增一致性审计模块 B5（CHG-0010）、AI 辅助端点（CHG-0011）。
> B4 侧对模块名 pattern 的响应见"复核发现 8"。

## 我提供的端点（`module.yaml: routes`）

`module.yaml: routes` 现在声明 OpenAPI 覆盖的**全部 8 个前缀**；下表列出每个 operation 的认证与作用域要求。实现状态一栏对应代码，不再混同"契约已锁定"与"实现已完成"。

| 方法+路径 | operationId | 用途 | 认证 | 作用域 | 实现状态 |
|---|---|---|---|---|---|
| `POST /api/v1/auth/session` | `createSession` | 登录 | 无需 | — | `已完成` |
| `POST /api/v1/auth/session/refresh` | `refreshSession` | 轮换刷新凭据、重发访问令牌 | 刷新 cookie | — | `已完成` |
| `DELETE /api/v1/auth/session/logout` | `deleteSession` | 注销 | 是 | — | `已完成` |
| `GET /api/v1/auth/jwks` | `getJwks` | 暴露 Ed25519 公钥 | 无需 | — | `已完成` |
| `GET /api/v1/users` | `listUsers` | 用户列表（游标分页、角色过滤） | 是 | 管理员 | `已完成` |
| `POST /api/v1/users` | `createUser` | 创建用户 | 是 | 管理员 | `已完成` |
| `PATCH /api/v1/users/{user_id}` | `updateUser` | 改资料/角色（`expected_updated_at` 乐观并发） | 是 | 管理员 | `已完成` |
| `DELETE /api/v1/users/{user_id}` | `deleteUser` | 停用（软删除）并撤销其会话 | 是 | 管理员 | `已完成` |
| `GET /api/v1/courses` | `listCourses` | 列出调用者作用域内课程 | 是 | 按作用域 | `已完成` |
| `POST /api/v1/courses` | `createCourse` | 创建课程（同时为创建者写入课程级授予） | 是 | 教师/管理员 | `已完成` |
| `GET /api/v1/courses/{course_id}/sections` | `listSections` | 课序列表 | 是 | 课程读 | `已完成` |
| `POST /api/v1/courses/{course_id}/sections` | `createSection` | 创建课序 | 是 | 课程写 | `已完成` |
| `GET /api/v1/sections/{section_id}/enrollments` | `listEnrollments` | 课序名单 | 是 | 课程读 | `已完成` |
| `PATCH /api/v1/enrollments/{enrollment_id}` | `updateEnrollment` | 编辑名单行（乐观并发） | 是 | 课程写 | `已完成` |
| `DELETE /api/v1/enrollments/{enrollment_id}` | `deleteEnrollment` | 删除名单行 | 是 | 课程写 | `已完成` |
| `POST /api/v1/roster-imports` | `createRosterImport` | 上传并解析名单（进入预览） | 是 | 课程写 | `已完成` |
| `GET /api/v1/roster-imports/{roster_import_id}` | `getRosterImport` | 读取预览与逐行结果 | 是 | 课程读 | `已完成` |
| `PUT /api/v1/roster-imports/{roster_import_id}/mapping` | `setRosterColumnMapping` | 人工确认列映射后重新校验 | 是 | 课程写 | `已完成` |
| `POST /api/v1/roster-imports/{roster_import_id}/confirm` | `confirmRosterImport` | 原子提交（幂等） | 是 | 课程写 | `已完成` |
| `GET /api/v1/audit-records` | `listAuditRecords` | 审计查询 | 是 | 管理员全量；教师/助教限本课程 | `已完成` |
| `GET /api/v1/modules` | `listRegisteredModules` | 运行期模块注册表 | 是 | 管理员 | `已完成` |
| `GET /api/v1/modules/{module}/health` | `getModuleHealth` | 单模块存活（公开、最小） | 无需 | — | `已完成` |

## 复核发现（只读复核，未修改 `MAIN/contracts/**`）

复核对象：本文件上一版 + `../../MAIN/contracts/openapi/b4.yaml`。**operation 清单一致**：上表 22 条
与 `b4.yaml` 中 22 个 operation 逐条对应，无缺漏、无多余。其余发现如下。

### 1. 路径前缀覆盖不一致（B4 侧已修正）

`b4.yaml` 顶部写着 "Every path below is declared in B4/module.yaml `routes`."，但 `module.yaml` 当时
只列 `/api/v1/auth`、`/api/v1/courses`、`/api/v1/roster-imports` 三个前缀，而契约实际还覆盖
`/api/v1/users`、`/api/v1/sections`、`/api/v1/enrollments`、`/api/v1/audit-records`、`/api/v1/modules`。

处置：**只改 B4 自己的 `module.yaml`**，补齐为 8 个前缀（不与 B1/B2/B3 冲突，MAIN 的
`check_manifests` 路由唯一性检查通过）。契约文件未动。

### 2. `RosterImportPreview` 描述文字与属性名不一致（MAIN 侧，待 CHG）

`RosterImportPreview` 的描述写 "Rows that fail validation appear in `rejected` with a reason"，而
schema 里实际属性名是 **`rejected_rows`**（`additionalProperties: false`）。

处置：按机器可读属性名实现 `rejected_rows`；文字不一致记录在此。是否修正描述走
`../../MAIN/docs/contracts/change-protocol.md`（P3 级别：仅文字），**B4 不改契约**。

### 3. 契约缺口：解析说明无字段可放

`RosterImportPreview.additionalProperties: false`，字段固定为
`roster_import_id`/`status`/`course_id`/`section_id`/`detected_columns`/`proposed_mapping`/
`accepted_rows`/`rejected_rows`/`created_at`。因此"为什么需要人工映射"这类人类可读说明**没有字段**。

处置：不新增字段（契约禁止），改为**结构化表达**：`status = mapping_required`、
`detected_columns` 列出实际列、`proposed_mapping` 只含唯一匹配上的字段（歧义/缺失的字段**不出现**）。
服务端另在 `b4.roster_imports.parse_detail` 保留人类可读原因供运维排查。若产品需要前端直接展示原因，
需走 P1 变更补字段。

### 4. 契约缺口：课程级角色授予无 operation

`createUser`/`updateUser` 的 `roles` 是平台级 `Role` 数组，没有 `course_id`；契约中也没有"给已有课程
追加教师/助教"的 operation。而课程作用域授权（`RQ-B-4-50`）需要它。

处置：**不新增路径**（ADR-0003 禁止）。当前由两种路径产生课程级授予：
`POST /api/v1/courses` 为创建者写入课程级 `teacher` 授予；以及运维命令
`python -m autograder_b4.ops.roles grant --user-id … --course-id … --role assistant`（运维入口，
不是 HTTP 契约）。补 HTTP operation 需走 P1 变更。

### 5. `getModuleHealth` 未声明 security

`getModuleHealth` 既无 `security: []` 也无 401/403 响应，`listRegisteredModules` 则有（全量注册表需
管理员）。处置：按"公开仅存活"实现——公开端点只返回 `{"module": ..., "healthy": true}`，不返回
`detail`；不健康时返回 `503` 并点名模块；未知/未装载模块返回 `404`。全量注册表（含 routes、
capabilities、migration head）仅管理员可读。

### 6. CHG-0007：`EnrollmentPublic` 与 `student_id`（已落地）

契约 `0.2.0` 起为 `listEnrollments` 的 `200` 补上了 `EnrollmentPublic`（此前只有
`"Page of enrollments."` 一句描述），并新增 **`student_id`**（nullable）。B4 侧的处置：

- `listEnrollments` 的每一项现在返回 `student_id`（`b4.enrollments.user_id`，未匹配到账号时为 `null`）；
- 契约把 `student_number`/`display_name` 声明为**非空**，与迁移一致
  （`b4.enrollments.student_number`/`display_name` 均为 `NOT NULL`）——实现不会发出 `null`，**无需改契约**；
- `additionalProperties: false`，因此测试对响应键集合做**精确断言**（多一个少一个都失败）：
  `tests/test_users_courses.py::test_enrollment_listing_exposes_the_platform_student_id`；
- 该字段是 B1"谁还没交"视图唯一可用的连接键：B2 的成绩行只带平台用户 id，而 B2 的
  `student_number`/`display_name` 合法地恒为 `null`（它只拥有 `b2`）。

### 7. CHG-0006：文档级 `security` 与 `bearerAuth`（无需 B4 改代码）

- `b4.yaml` 增加文档级 `security: [{bearerAuth: []}]`，并把 `createSession`/`refreshSession`/`getJwks`
  声明为 `security: []`；`common.yaml` 声明 `bearerAuth`/`serviceToken`（CHG-0006）。这与 B4 既有实现
  （除登录/刷新/JWKS 外一律要 Bearer，匿名 `GET /api/v1/modules` → `401`）一致，**未改代码**。

### 8. CHG-0009 / ADR-0013：模块名 pattern 打开（B4 已改；契约自身仍有一处未跟上）

`MAIN/contracts/manifest.schema.json` 把 `module` 的 `^B[1-4]$` 改为 `^B[0-9]{1,3}$`
（`database.schema` 改为 `^(none|b[0-9]{1,3})$`）。B4 侧处置：

- `src/autograder_b4/api/modules.py: MODULE_PATTERN` 同步为 `^B[0-9]{1,3}$`，并在注释里写明该形状
  **只在契约里定义一次**（ADR-0003：设计期权威属 MAIN）；
- 删除了 `registry.py` 中**从未被引用**的 `VALID_MODULE_NAMES = ("B1","B2","B3","B4")`——它是同一个
  封闭集合的第二份副本，且已是死代码；
- 测试不再钉死封闭集合，改为按 pattern 断言，并新增"`B5`/`B12`/`B999` 必须被接受、`B1234`/`b5` 必须被拒绝"的用例。

> **契约内部不一致（MAIN 侧，待处理）**：`b4.yaml` 的 `getModuleHealth` 路径参数（第 545 行）与
> `RegisteredModule.module`（第 653 行）**仍是** `pattern: '^B[1-4]$'`。即：一个第五模块的 `module.yaml`
> 现在能通过 manifest schema，但它出现在 B4 注册表响应里就违反 OpenAPI 的声明。B4 不修改契约，
> 此处只报告；在 MAIN 同步该 pattern 之前，B4 的 `MODULE_PATTERN` 比 OpenAPI 更宽。

## 运维入口（CLI，不是 HTTP 契约）

新增/变更的模块间**调用面**只有 HTTP 与事件；以下是运维入口，不构成契约面（ADR-0003）：

| 命令 | 用途 |
|---|---|
| `python -m autograder_b4.ops.provision [--verify-only]` | 最小权限角色与 `b4` schema 供给 |
| `python -m autograder_b4.ops.generate_key` / `bootstrap_admin` / `roles grant\|revoke` | 密钥、首个管理员、课程级授予 |
| `python -m autograder_b4.ops.messaging provision\|relay\|consume\|dlq list\|dlq replay\|health` | JetStream 拓扑（含共享 `DOMAIN_EVENTS`）、outbox 中继、消费、DLQ；`provision` 是声明式 reconcile，会把 broker 上的 stream 收敛到声明（含配额） |
| `python -m autograder_b4.ops.messaging metrics` | **新增**：读 broker 实测占用并与资源账本比对（非零退出=超限/stream 缺失） |
| `python -m autograder_b4.ops.table_standards [--scope all\|catalog\|migrations]` | 表结构规范：真实 `b4` catalog + **全仓迁移 DDL 文本** |
| `python -m autograder_b4.ops.backup --container <name> [--destination <path>]` | **新增**：容器内 `pg_dump -Fc` 备份 `b4` 并写入结构化记录 |

## 我消费的端点 / 事件

- **端点**：当前**无**（B4 不调用 B2/B3/MAIN 的业务端点；只读取 MAIN 契约与 SDK `descriptor`）。
- **事件**：`module.yaml: events.subscribes = []`，当前**不订阅**任何业务事件。B4 另外运行一个
  durable consumer 用于投递记账 / DLQ / 重放（控制面），不构成业务订阅，见 `events.md`。

## 错误与通用约定

- 错误格式：RFC 9457 `ProblemDetails`（`type`/`title`/`status`/`detail`/`instance`，见 common.yaml）；
  `type` 取值集中在 `src/autograder_b4/errors.py`。
- 未认证 → `401`；已认证但无权限 → `403`（**不要**用 `404` 掩盖）；有权限但资源不存在 → `404`。
- 分页：`CursorPage` 信封（`items` + `next_cursor`），游标为 `(created_at, id)`，禁止 offset 分页。
- 时间 UTC RFC 3339 毫秒精度；标识 UUID v4；请求关联 `traceparent` / `X-Request-ID`。
- 响应体不含栈、SQL、内部路径；`detail` 由 B4 撰写，不含凭据与超范围 PII。
