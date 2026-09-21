# B1 数据库边界

## 结论：无权威数据库

`module.yaml` 声明 `database.schema: none`、`database.migration_head: none`。B1 **没有**任何权威数据库、没有任何迁移、没有任何服务端表。所有权威数据归 B4（`b4`）、B3（`b3`）、B2（`b2`）schema（见 `../../../MAIN/docs/database/ownership.md`）。

## 浏览器本地能存什么

B1 是静态客户端，唯一"存储"是浏览器本地（`localStorage` / `IndexedDB` / `sessionStorage`）。**仅允许**：

| 内容 | 存储位置 | 生命周期 |
|---|---|---|
| 未提交草稿（编辑器内容 + 题目上下文） | `localStorage`，键 `b1.draft.v1.<user_id>.<question_version_id>`（实现见 `src/features/submission/draft.ts`） | 提交成功即清除；**退出登录时清空全部草稿**；也可随浏览器清理 |
| UI 偏好（主题、语言、列宽等） | `localStorage` | 用户手动清除或长期 |

> 已实现并测试：草稿键按 `question_version_id` **与用户**双重隔离（共用机器上换人登录读不到
> 上一个人的代码）、记录内的 `ownerId` 在读取时校验（不符即丢弃）、损坏/版本不匹配的草稿被丢弃
> （不做尽力恢复）、提交成功后清除、`signOut` 清空本浏览器全部 `b1.draft.v1.*` 键。
> 测试同时断言 `localStorage`/`sessionStorage` 中**不出现** token/刷新凭据。
>
> 为什么键里要带用户：`ADR-0007` 第 7 条允许本地存草稿，但没有允许把草稿留在**下一个用户**的
> 会话里。退出登录会清空草稿是**用户可见的破坏性行为**，因此作答页显式说明
> （`submit.draftClearedOnSignOut`）。

## 浏览器本地禁止存什么

- 访问令牌、刷新令牌、cookie 值（刷新凭据仅 HttpOnly cookie，JS 不可读）。
- 参考答案、隐藏测试用例、`expected_output`。
- 任何权威数据：学生/课程/课序/选课名单、成绩、题目版本、评测报告。
- 学号明文之外的 PII（如邮箱全量、身份证号）。

## 归属与越界禁令

- B1 **只使用浏览器本地存储**；**禁止**连接 PostgreSQL、NATS；**禁止**跨 schema 外键/SQL/ORM；**禁止**导入 B2/B3/B4 的任何代码或 ORM。
- 草稿中的内容若包含提交代码，也只存在于浏览器本地，只在用户显式提交时经 `POST /api/v1/submissions` 发送。

## 敏感数据标记（草稿例外）

- 草稿可能包含学生自己写的答案代码：这属于用户本人内容，仍受"仅本地"约束，不进入日志、不进入任何分析统计。
- B1 前端日志（RQ-B-1-99）**不得**记录草稿/答案/源码内容。
