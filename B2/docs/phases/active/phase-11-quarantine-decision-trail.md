# Phase 11 隔离区人工处置留痕与邮件摘要（0016）

## 动机

老师复核隔离邮件时反馈三件事：

1. 列表只有邮箱/UID，认不出是哪封邮件；
2. 只能"归属学生"，没有"驳回"出口，结构/附件类拒绝的记录永远留在待处理列表；
3. 处理完之后看不到"谁在什么时候处理的"。

对应三个缺口：没有主题（`subject`）、没有处置结果（`resolution`）、没有处理时间与操作人
（`resolved_at`/`resolved_by`）。

## 数据模型（迁移 `20260916_0016`）

| 列 | 类型 | 语义 |
|---|---|---|
| `subject` | `text` nullable | 主题头，入口侧只保留可打印字符、折叠空白、≤200 字；**不是正文** |
| `resolution` | `text` nullable | `attributed` / `dismissed`；历史行为 NULL |
| `resolved_at` | `timestamptz(3)` nullable | 人工处置时间（服务端时钟） |
| `resolved_by` | `uuid` nullable | 操作人（访问令牌 `sub`） |

约束：

- `ck_intake_quarantines_subject`：空或 1..200 字；
- `ck_intake_quarantines_decision`：空或两个枚举值；
- `ck_intake_quarantines_decision_audit`：待处理 ⇒ 处置字段全空且无学生；归属 ⇒ 有学生 +
  时间/操作人；驳回 ⇒ 无学生 + 时间/操作人；0016 之前的历史已处理行允许"有学生但审计字段为空"；
- **删除并取代** 0013 的 `ck_intake_quarantines_resolution`（`resolved = (resolved_student_id
  IS NOT NULL)`）：旧约束把"已处理"等同于"有学生"，与驳回不相容。

## 主题只作为有界标签

邮件内容仍然不落库：正文、附件字节与摘要都不写。`subject` 是唯一新增的、与内容相邻的字段，
因此：

- 校验在 `parse_message`：`message.get("Subject")` → 去控制字符 → 折叠空白 → 截断 200；
- 它只出现在已有行级授权的响应里（staff 的 scope 谓词、管理员全局）；
- 体积闸门/解析失败在拿到主题之前，因此这些行的 `subject` 为 NULL；
- 不因主题改变任何匹配、幂等或授权判定（不是键，也不参与 scope）。

这是产品决定："让老师认出邮件"需要主题；平台仍不做第二个邮件仓库。

## 驳回（`dismissIntakeQuarantine`）

- 与归属共用行锁与行级授权：先 `SELECT ... FOR UPDATE`，按**锁住的行**重判 scope，再判是否已处理；
- 与归属的关键区别：**不要求** `reason_code` 属于可归属集合。归属写学生身份，只能修"身份没推出来"；
  驳回不写身份，对结构/附件类拒绝同样成立，是清空待处理列表的出口；
- 写入一条 `UPDATE`：`resolved=true`、`resolution='dismissed'`、`resolved_student_id=NULL`、
  `resolution_note`、`resolved_at`、`resolved_by`；不建提交、不发事件、不评测；
- 已归属或已驳回的行再处置 ⇒ 409；越权 => 403；非法/不存在 => 404；体违规 => 422。

## 验证（真实 PostgreSQL）

- `tests/test_intake_api.py`：驳回可用于三类原因码、审计字段正确、零提交/事件/评测；
  归属与驳回双向 409；staff 对未关联/跨课 403 且不写入；管理员可驳回未关联行；
  请求体未知字段/超长备注/非 JSON/不存在标识分别 422/422/404。
- `tests/test_email_intake.py`：主题折叠与 200 字截断、主题不含正文标记。
- `tests/test_migrations.py`：0016 的列/约束存在、时间列 `timestamptz(3)`、upgrade/downgrade 可逆。

## 未做 / 明确边界

1. **操作人显示**：B2 只知道令牌 `sub`，没有用户名；B1 对"本人"显示"你"、对他人显示短 id，
   不跨模块拉 B4 用户目录（`/users` 是管理员接口，GET 单用户未声明）。
2. **历史行不回填**：0016 前的已处理行没有 `resolution/resolved_at/resolved_by`，界面显示"未记录"，
   不猜测归属或时间。
3. **名单成员关系仍未复核**：B2 没有 B2→B4 名单通道；驳回/归属只重查记录行 scope 与调用者权限。
4. **降级风险**：`downgrade()` 删除四列并恢复 0013 约束；若存在 `resolution='dismissed'` 的行，
   恢复旧约束会失败，因此降级前必须先处理这些行（见 `docs/database/migrations.md`）。
