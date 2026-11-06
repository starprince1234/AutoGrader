# Phase 12 隔离区匹配用户、暂存与标记作废（迁移 0019）

## 动机

老师复核隔离邮件时需要的不是"记录一个决定"，而是把邮件真正变成学生的提交：

1. **匹配用户**：选学生 + 课程 + 作业（题目版本），把原邮件的附件归入该学生的提交；
2. **暂存**：先放着，稍后再处理；
3. **标记作废**：垃圾邮件/错发邮件，关闭但不归属。

0016 只有 `resolution=attributed|dismissed`，既不能生成提交，也不能表达"暂存"。

## 数据模型（`20261029_0019`）

| 列 | 语义 |
|---|---|
| `resolved_submission_id` | `completed` 时指向真实生成的提交；其余为空 |
| `held` | 暂存标记；仍未处理，可恢复/继续匹配/作废 |
| `held_at` / `held_by` | 暂存时间与操作人 |

约束：

- `resolution ∈ {attributed, completed, dismissed}`；
- `ck_intake_quarantines_hold`：`held=true` 必须有时间与操作人；`held=false` 两者必须为空；
- `decision_audit`：待处理/暂存行不得带处置字段；`completed` 必须有学生、提交、时间、操作人；
  `attributed`（legacy）无提交；`dismissed` 无学生、无提交。

## 匹配用户（`completeIntakeQuarantine`）

隔离记录按设计**不存附件字节**，因此完成路径必须回邮箱取原邮件：

1. 行锁内重判 scope；目标课程必须在 staff scope 内；记录已有 scope 时目标必须一致；
2. 按记录里的 `mailbox/uidvalidity/uid` 取原始报文；UIDVALIDITY 变化/取不到 ⇒ 409；
3. 重跑同一套附件闸门（不通过 ⇒ 422）；
4. 以选中的 `student_id/course_id/section_id/question_version_id` 构造提交，走与自动收信相同的
   `persist`；`email_checkpoints` 在同一提交事务内从 `quarantined` 改为 `processed`；
5. 隔离行写 `resolution=completed` + `resolved_submission_id`。

前置条件：本部署必须配置邮件入口（`B2_IMAP_*`）；没有配置时返回 503，**不从缓存/客户端补附件**。

## 暂存与作废

- `hold`：只改 `held/held_at/held_by`，不动 `resolved`；已处理 409。
- `dismiss`：`resolution=dismissed`，无学生、无提交；任何原因码都可作废。
- 三个动作共用行锁 + 行级 scope 重判；重复/冲突返回 409。

## 验证（本机真实 PostgreSQL）

- `tests/test_intake_api.py`：hold 记录操作人/时间、恢复、已处理 409、越权 403；complete 无邮件入口
  503、缺字段/未知字段/非法 UUID 422、越界目标 403；用 fake email 验证完成编排写入
  `resolution=completed` 与 `resolved_submission_id`。
- `tests/test_email_intake.py`：邮件解析/附件/主题/正文摘录（145 用例）。
- `tests/test_migrations.py`：0019 列/约束、时间精度；顺带修正 `20261028_0017` 的 downgrade
  （原实现保留 `manual_grades`，使基线往返被外键阻断）。

## 演示数据

`B2/scripts/seed_demo_quarantines.py` 可写入 2 条演示待处理记录（身份无法确定 / 没有附件）。
它要求 `--confirm-demo-write`，只插 `intake_quarantines`，不建提交；**匹配用户仍要求原邮件在邮箱里**。
