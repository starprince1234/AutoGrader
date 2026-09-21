# B3 边界（Boundaries）

本文件是 B3 的**信任边界与数据边界**，越界即缺陷。所有条目为强制约束，不属于"建议"。

> 每条边界后面附"实现与验证"：边界不是靠审阅守住的，每条都有代码位置与自动化断言（见 [../testing/cases.md](../testing/cases.md)）。

## 1. 公开 DTO vs 内部 DTO 的物理分离

- **物理分离**：公开 DTO（给 B1/学生）与内部 DTO（给 B2 的 evaluation-package、受保护内容）放在**不同模块 / 不同文件**，禁止混用同一模型类。
- **公开 DTO 永不包含**：`reference_answer`、隐藏用例、`expected_output`、fixture 原始内容、其他学生的数据、内部存储路径。
- **内部 DTO 只服务间可读**：`GET /api/v1/question-versions/{id}/evaluation-package` 需服务间认证；学生/教师凭据调用返回 `401`/`403`。
- **验证方式**：契约测试**逐字段断言公开响应中字段缺失**（不是断言值为空），而非靠代码审阅。这是最高危缺陷（`MAIN/docs/workstreams/B3.md` §5 WP-B3-A 风险）。

## 2. 受保护内容边界

| 受保护内容 | 存储 | 可读方 | 绝不出现在 |
|---|---|---|---|
| 参考答案 | 独立权限存储（数据库层或对象层） | 仅 package 编译器（内部） | 公开 DTO / 事件 / 日志 / 学生可访问存储 |
| 隐藏用例 | 同上 | 仅 package 编译器 | 同上 |
| expected output | 同上 | 仅 package 编译器 | 同上 |
| fixture 内容 | 内容寻址存储（摘要绑定） | 仅 package 编译器 | 公开 DTO；事件只携带摘要 |

- 日志与事件脱敏规则：任何日志/事件都不得含上述内容或其哈希/摘要的原始形式（事件只允许 `question_version_id`，内容按需内部拉取）。

**实现与验证**

- 存储：受保护内容只落 `b3.reference_answers` / `b3.test_suites` / `b3.test_cases` / `b3.blobs` / `b3.fixtures`；普通角色对这 5 张表 `REVOKE ALL`（`test_migrations.py::test_app_role_cannot_read_protected_tables` → PostgreSQL `permission denied`）。
- 出站：公开响应只经 `api/serializers.serialize_public`（受保护键名直接抛错）；事件 payload 键集合被精确断言（`test_events.py::test_published_event_carries_no_protected_content`）。
- 日志：`logging.py` 按 key 脱敏 13 类受保护键，异常只记类型与消息（`test_logging.py`）。
- 重放：`event_inbox` 里存的是**白名单校验后**的 payload，重放时再校验一次（`test_events.py::test_event_from_inbox_row_revalidates_the_whitelist`）。

> **契约事实（不是 B3 的选择）**：`evaluation-package.schema.json` 没有参考答案字段（`additionalProperties: false`），因此参考答案**没有任何出站路径**，仅用于发布完整性校验；教师侧查看参考答案需要 MAIN 决策新端点（`CHG-B3-0002`）。

## 3. 数据边界（我只碰 b3 schema）

- **我只读写**：`b3` schema（`module.yaml: database.schema: b3`）。
- **我禁止触碰**：`b2` schema（提交物/评测/分数/证据）、`b4` schema（用户/课程/选课/审计）；`public` 共享 ORM。
- **禁止**：跨 schema 外键、跨 schema SQL、跨 schema 视图、超级用户连接、从其他模块 ORM 导入模型。
- 迁移版本表：`alembic_version_b3`（`alembic.ini` + `env.py` 固定），独立于其他模块。

**实现与验证**

- 权限：两个最小权限角色（`_app` 只读业务表 + 统计写；`_internal` 全权用于发布/编译），应用连接 **不是** 超级用户（`test_migrations.py::test_no_superuser_connection_is_used_by_the_application`）。
- 结构：`test_migrations.py::test_no_foreign_key_leaves_the_b3_schema`（无跨 schema FK）、`::test_only_the_b3_schema_is_owned`、`test_hygiene.py::test_only_the_b3_schema_is_referenced`（源码/迁移中无跨 schema SQL/ORM）。
- `course_id`/`section_id`/`created_by` 是普通 UUID 列（无外键）：课程作用域由已验证的 token 判定，不靠数据库 join。

## 4. 隐私边界（统计输入去标识化）

- 统计**只能**消费去标识化 `evaluation.completed` 事件；**绝不**读取学生源码、提交内容、学生身份。
- 消费端对事件 payload 做**字段白名单校验**：越界字段（源码、答案、姓名、`user_id` 等）直接拒绝并记日志。
- 统计结果**不暴露**单个学生成绩给无权限用户（只给聚合值）。
- 事件去重按 `event_id`，重复投递不重复计数。

## 5. 信任边界

- **我信任**：MAIN 装载时注入的 `ModuleContext`（settings/health）；B4 签发的 JWT（本地验 `iss`/`aud`/`role`/课程作用域）；`MAIN/contracts/**` 与 `contracts.lock` 的摘要一致性。
- **我不信任**：一切外部输入（教师编辑内容、事件 payload、图片字节、adapter 声明）。全部做：作用域校验、schema/枚举校验、路径白名单、大小/数量上限、注入防护（`args` 数组、`..` 拒绝）。
- **认证/授权**：未认证 → `401`；无权限/跨课程 → `403`（不用 `404` 掩盖）；有权限但资源不存在 → `404`（`../../../MAIN/docs/api/identifiers.md` §3）。

## 6. 安全边界

- 密钥来源：`DATABASE_URL`、`NATS_URL` 来自环境变量（见 [../../.env.example](../../.env.example)）；JWT 验签用 B4 JWKS 公钥（非对称，本地只存公钥）。
- 日志脱敏范围：不打印源码、答案、隐藏用例、token、内部绝对路径、schema/表名。
- 导出限制：B3 本身不做成绩导出（属 B2）；B3 的统计查询结果不携带个人可识别信息。
