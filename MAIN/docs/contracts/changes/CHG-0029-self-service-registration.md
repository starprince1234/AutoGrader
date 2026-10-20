# CHG-0029 自助注册：邮箱验证码 + 名单锚点，登录仍只认用户名

- **级别**：P1 增补（新增 4 条路径与 3 张表）
- **状态**：已生效（契约）；B4 与 B1 的实现同批落地，**未部署**
- **契约版本**：0.16.0 → **0.17.0**
- **依据**：`RQ-B-4-50`（认证与会话）、用户需求（2026-10-20）：注册即可登录、不需要审核激活、邮箱绑定用户名、
  登录只需用户名 + 密码、管理员用 `姓名+学号` 的 CSV 作为注册校验依据、未登录不得进入功能界面

## 1. 问题：项目此前**没有注册**，账号只能由管理员或名单产生

`POST /api/v1/auth/session` 一直是唯一的入账通道，而账号只来自两条路：管理员 `POST /api/v1/users` 与
运维命令 `bootstrap_admin`；名单确认只把 `enrollments.user_id` 关联到**已存在**的用户
（`B4/src/autograder_b4/roster/service.py:543-577`），且 `user_id` 允许为空。

用户要求"学生自己注册、注册完就能登录"，意味着**账号来源**发生变化。它不是 B4 的内部重构：
数据库要有"允许谁注册"的数据、邮箱要有发信通道、B1 要有注册页与"未登录强制跳转"。这三件事在
0.16.0 的契约里都不存在任何形状。

### 1.1 没有审核激活，靠什么替代人工判断

需求明确"不需要审核激活，如果原系统包含这一功能就删去"。**原系统没有这个功能**（没有注册就没有审核），
所以本变更既没有删除，也没有引入 `pending/approved` 状态机。替代人的是两个数据锚点：

| 锚点 | 回答的问题 | 来源 |
|---|---|---|
| 管理员导入的 `姓名 + 学号(13 位)` 名单 | 这个身份在本期名单里吗 | 新增 `registration_allowlist` |
| 6 位邮箱验证码 | 这个人能收到这个邮箱的信吗 | 新增 SMTP 发信 + `registration_codes` |

**两者必须同时成立**，注册才能建号；通过即建号、即可登录。

### 1.2 已知残余风险（知情接受，不隐藏）

名单里**没有邮箱列**（用户明确"不添加"），因此校验的是 `(学号, 姓名)` 而不是"人"。知道同学姓名与
13 位学号的人，可以用自己的邮箱通过验证码、注册成那位同学。13 位学号在班级里是半公开信息，
所以这**挡得住随手乱填与批量注册，挡不住有心冒名**。

缓解（都不能根除）：自助注册的账号带 `users.created_via = 'self'`，管理员可一键筛出；每次注册写
`auth.registration.created` 审计；同一学号只能注册一次，被抢注后由管理员停用并要求重新注册。
要真正堵住，需在名单里增加邮箱列 —— 那是下一次需求变更。

## 2. 决策

1. **新增 4 条路径**，`createSession` 一字未改：
   - `POST /api/v1/auth/registrations/code`（`requestRegistrationCode`）：先校验用户名/邮箱/学号唯一性与
     名单归属，再发码；只存验证码的 SHA-256 摘要，摘要绑本条意图的 `code_id`；**发信失败返回 `503`，
     不降级为"跳过验证"**。生产环境缺 SMTP 变量时**进程照常启动**，但启动日志点名缺失变量、
     注册端点返回 `503`（理由见 §2.5）。
   - `POST /api/v1/auth/registrations`（`createRegistration`）：校验验证码（单次使用、10 分钟、
     错 5 次作废），在**一个事务**内建 `users`（`created_via='self'`）、`credentials`（Argon2id）、
     平台级 `student` 角色，并**回填**该学号下 `user_id IS NULL` 的 `enrollments`。
   - `POST|GET /api/v1/registration-allowlists`（管理员）：上传 CSV **全量替换**名单，保留批次记录
     （文件名 + 内容摘要 + 行数 + 上传人）。
2. **邮箱不是凭据**：不参与登录、不用于找回口令、不作为身份证明。`users.email` 获得
   `lower(btrim(email))` 的**部分唯一索引**（`email IS NOT NULL` 时唯一），语义是"绑定关系"。
3. **名单按表头名定位**（`姓名`/`学号`，兼容 `name`/`student_number`），编码 UTF-8（含 BOM）或
   GB18030；学号必须 13 位数字，文件内重复与表头缺失都直接 `422`，不静默跳过任何一行。
4. **登录侧仍然不可枚举**：未知用户、错口令、停用账号同一 `401` 且走等时 Argon2。注册侧必须告诉
   用户"用户名/邮箱/学号已被占用"，否则无法纠正 —— 这是产品必需的信息披露，用限流兜底
   （同邮箱 60 秒冷却、每小时 5 次）。

### 2.5 为什么"缺发件人"不阻止启动

最初的实现让 `config.validate()` 在生产缺 `SMTP_*` 时抛 `ConfigurationError`。**这个取舍是错的**，
第一次部署就把它暴露出来：整个后端在 `create_app()` 阶段崩溃，登录、课程、评测、成绩**全部不可用**，
而缺的只是一个尚未上线功能的发信通道。

现在的行为：

| 场景 | 行为 |
|---|---|
| 生产缺 SMTP | 进程**正常启动**；日志 `module.registration_mail_unconfigured` 列出缺失变量名（不含值） |
| 调用 `requestRegistrationCode` | `503`，`detail` 点名邮件通道不可用；**不建号、不发码、不占用限流配额** |
| 其余端点 | 不受影响 |

判据是一致的：**"不能跳过验证"是硬约束**（由端点拒绝来兑现），**"必须能启动"也是硬约束**。
把前者实现成后者，等于用一个可选依赖换掉整个平台。

## 3. 变更文件

- `openapi/b4.yaml`：上述 4 条路径；`openapi/{b2,b3,b4,b5,common}.yaml` 与 `asyncapi/events.yaml`
  同步 `info.version`
- `CHANGELOG.md`：0.17.0 条目
- 五份 `module.yaml` 的 `contract_version`、五个模块描述符的同一字面量、五份 `contracts.lock`
  （版本 + 摘要，由 `contract_digest()` 计算）
- `B4/module.yaml`：`routes` 增加 `/api/v1/registration-allowlists`；`migration_head` → `20261020_0003`

## 4. 受影响模块

| 模块 | 需要做什么 |
|---|---|
| **B4** | 迁移 `20261020_0003`（3 表 + `users.created_via` + 邮箱唯一索引）、`security/mailer.py` 发信、`registration.py` 领域逻辑、`api/registrations.py` 端点、`config.py` SMTP 与注册参数、4 个审计动作 |
| **B1** | `/register` 注册页（含"发送验证码"与倒计时）、`/admin/registration-allowlist` 管理页、登录页"去注册"与 `?redirect=` 回跳、`AppShell` **未登录强制跳 `/login`**（恢复会话完成前不跳，跳转期间不渲染页面） |
| **B2 / B3 / B5** | **无改动**：登录请求体、令牌声明、JWKS 形状都没变，本地验签不受影响 |

## 5. 是否破坏兼容

**否**（全部是新增路径与新增表）。唯一需要写明的一处收紧是：`users.email` 的**部分唯一索引**让
"同一邮箱注册两个账号"从可行变成 `409`。迁移在建索引前会统计存量重复，有重复就**明确失败**并
要求先治理，不会替操作者挑一行保留。

## 6. 迁移方式

一次性切换，不保留兼容层。`alembic upgrade head` 增加 `20261020_0003`；`email` 保持可空
（存量用户不强制补邮箱），列上**不加** `NOT NULL`。

## 7. 验证

**已执行（本机，2026-10-20）**：

- `ruff check MAIN/host MAIN/sdk MAIN/deploy/check-readiness.py B2/src B3/src B4/src B5/src`
  （ruff 0.16.7，与 CI 同一版本）→ **All checks passed**
- `node scripts/generate-api-types.mjs` → 重新生成 `B1/src/api/generated/*.ts`；`--check` → 与契约一致
- `tsc -b` → 通过；`eslint . --max-warnings 0` → 通过；`vitest run` → **265 passed / 32 files**
- `vite build` → 通过（仅 chunk 体积警告）
- `pytest MAIN/host/tests -q` → **7 passed**
- `Settings.from_environment` 冒烟（2026-10-20）：生产缺 SMTP **可构造**（`smtp_configured=False`、
  缺失变量恰为那四个）；配齐后 `smtp_configured=True`，且 `465 + tls →` 隐式 TLS、
  `587 + starttls →` STARTTLS 升级
- `checks.py` 中不依赖已安装模块的门禁：契约示例、严格解析、共享词表、manifests、需求基线、
  决策引用、变更记录、路由声明、文档治理与死链、替换字符、本地配置一致性 → 全部通过；
  摘要 `2061d47e…`，五份锁一致

**未执行**：

- `pytest B4/tests`：本机没有 PostgreSQL 与 B4 虚拟环境。新增的 `test_registration.py`（12 例）
  **尚未跑过一次**
- `check_module_descriptors`：需要 `uv sync --all-groups --extra modules`，本机无 `uv`
- `alembic upgrade head`：同上

## 8. 回滚方式

还原本批契约与版本号 → 删除 `B4/src/autograder_b4/{registration.py,api/registrations.py,security/mailer.py}`
与 B1 的注册页、管理页、`AppShell` 跳转 → `alembic downgrade 20260915_0002`（会删 3 张表、
邮箱索引与 `created_via` 列）→ 重跑 `python MAIN/scripts/relock_contracts.py`，版本回到 0.16.0。
