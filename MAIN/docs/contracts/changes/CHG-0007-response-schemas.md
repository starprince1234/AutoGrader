# CHG-0007 补全响应 schema：`listAssignments` 与 `listEnrollments`（含新增 `student_id`）

- **级别**：P2（语义）；`student_id` 一项属 **P1 增补**（只加字段）
- **状态**：已生效（2026-09-18 判定）
- **提出方**：MAIN（集成审计 E15 类：门禁只校验"存在"不校验"为真"），触发者是 B1 的实现阻塞
- **契约版本**：0.1.0 → 0.2.0（与 CHG-0006 同一批次落地）

## 1. 问题

审计发现 **10 个 operation 的 `200` 只有一句 `description`、没有 schema**：

| 文件 | operation | 当时声明 |
|---|---|---|
| b3.yaml | `listAssignments` | `{description: Page of assignments.}` |
| b3.yaml | `updateQuestionDraft` / `archiveQuestionVersion` | 同上 |
| b4.yaml | `listSections` / `listEnrollments` / `listAuditRecords` | 同上 |
| b4.yaml | `updateUser` / `updateEnrollment` / `setRosterColumnMapping` / `getModuleHealth` | 同上 |

`docs/api/governance.md` 要求响应体必须由契约定义。实际后果有两条，都是真实的：

1. **客户端只能靠猜。** B1 对这几张表用"动态键"渲染——运行时读到什么列就显示什么列。
2. **有一项必需功能因此做不出来。** B1 要实现"谁还没交"（教师最真实的人工工作），
   需要把名单与成绩按同一把键连接。而：
   - `listEnrollments` 只返回 `student_number` / `display_name`，**没有任何用户 id**；
   - B2 的成绩行标识学生用的是**平台用户 id**，且 B2 的 `student_number` / `display_name`
     **永远是 null**——这是正确的：B2 只拥有 `b2` schema，按数据所有权它拿不到 B4 的姓名学号；
   - 于是两端**没有可用的连接键**。唯一含两种标识的端点是 `GET /api/v1/users`，而它仅管理员可用
     ——用它实现会变成另一个功能（管理员专用），不是原需求的实现。

   这不是"B1 没想到办法"，是**契约缺一个字段**。B1 的实现者按规程停下并上报，没有自行替代。

## 2. 变更内容

### 2.1 `b3.yaml`：新增 `AssignmentPublic`，接到 `listAssignments`

形状以**实际实现与实测响应**为准（`B3/src/autograder_b3/dto/public.py` 的 `AssignmentPublic`
与线上 `GET /api/v1/assignments` 实测一致）：
`assignment_id` / `course_id` / `section_id` / `title` / `status`（`open` \| `archived`）/
`due_at`（可空）/ `question_version_ids` / `created_at` / `updated_at`。

> 顺带说明为什么它值得单独记一笔：平台**已经创建了 8 个作业**，但契约此前没有描述过作业长什么样，
> 所以任何客户端都无法把"作业"呈现给教师或学生。这是"契约缺 schema"从"不好看"变成
> "功能做不到"的实例。

### 2.2 `b4.yaml`：新增 `EnrollmentPublic`，接到 `listEnrollments`

| 字段 | 类型 | 依据 |
|---|---|---|
| `enrollment_id` / `section_id` | Uuid，必填 | 实测响应 |
| **`student_id`** | Uuid，**可空** | **本次新增**。来源是 `b4.enrollments.user_id`；该列在 `B4/src/autograder_b4/db/tables.py:164` 是 `nullable=True`，所以名单行在尚未匹配到用户账号时确实没有它 |
| `student_number` | string，必填 | `tables.py:165` 为 `nullable=False` |
| `display_name` | string，必填 | `tables.py:166` 为 `nullable=False` |
| `is_active` | boolean，必填 | `tables.py:169` 为 `nullable=False` |
| `updated_at` | Timestamp，必填 | 实测响应 |

**为什么 `student_id` 可空是对的、而不是偷懒**：名单文件里的学号只有在
`b4.enrollments.user_id` 被匹配填充之后才对应到一个平台账号。把可空字段声明成必填会让
契约与数据库互相矛盾；声明成可空则如实表达"这一行还没有对应账号"，客户端据此把该行呈现为
"未绑定账号"而不是伪造一个 id。

## 3. 是否破坏兼容

- `AssignmentPublic` / `EnrollmentPublic`：**否**，此前无 schema，没有已发布的形状被改变。
- `student_id`：**否**，纯新增字段。旧客户端忽略它即可；新客户端据此实现"谁还没交"。

## 4. 受影响模块与需要做的改动

| 模块 | 改动 |
|---|---|
| **MAIN** | 契约与四份 `contracts.lock`（本变更）；`docs/api/*` 指向新 schema |
| **B3** | **无代码改动**：`AssignmentPublic` 已存在且形状一致；只需重新生成/核对派生物 |
| **B4** | **需要一处实现改动**：`list_enrollments` 的响应项加入 `student_id`（取 `enrollments.user_id`，可空）。字段已在 DB 里，无需迁移 |
| **B1** | 重新生成 API 类型；`C1 作业中心` 与 `C3 谁还没交` 依赖本变更 |
| **B2** | 无改动。**不要**为了连接而给成绩行填 `student_number`——它按所有权必须保持 null |

## 5. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks     # 三条门禁 + 计算新摘要
```

本变更特有的验证：**用契约自己的 schema 校验线上真实响应**（`.runtime/contract_live_check.py`
用过的一次性脚本）。已实测：

```text
assembled spec: 37 paths
  [OK] /api/v1/assignments                      (7 items)
  [OK] /api/v1/courses/{course_id}/sections     (1 items)
  [OK] /api/v1/audit-records                    (2 items)
  [OK] /api/v1/questions                        (3 items)
4/4 live responses match the contract
```

`listEnrollments` 的 schema 在 B4 补上 `student_id` 之前**会校验失败**——这正是它该有的行为：
契约描述的是目标形状，实现未跟上时应当显形，而不是把契约改成现状。

## 6. 回滚方式

回退本批次并重新运行 `scripts/relock_contracts.py`。不得只回退 `student_id` 一项而保留
新 schema（那会让 `EnrollmentPublic` 与实现不一致）。

## 7. 未包含在本变更内的部分（明确记录，避免被当成已完成）

以下 operation 仍然只有 `description`，本变更**没有**处理它们：

`updateQuestionDraft`、`archiveQuestionVersion`、`listSections`、`listAuditRecords`、
`updateUser`、`updateEnrollment`、`setRosterColumnMapping`、`getModuleHealth`。

它们的形状已探明（例如 `updateUser` 返回 `{user_id, changed_fields}`、`getModuleHealth` 返回
`{module, healthy}`），但补 schema 需要同时核对每个端点的授权语义，属独立的一次契约变更。
在此之前，B1 对这几张表继续按"未知形状"处理是**已知且被记录的**限制，不是遗漏。
