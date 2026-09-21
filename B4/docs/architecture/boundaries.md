# B4 边界（信任 / 数据 / 凭据 / 脱敏）

## 1. 信任边界

- **我是什么**：B4 是**身份权威**——签发令牌、定义角色、暴露 JWKS。
- **谁能调用我**：任何 HTTP 客户端（B1、MAIN、其他模块的服务间调用），但**一切外部输入不可信**（表单、文件、header、cookie）。
- **我信任什么**：
  - MAIN host 提供的 `context`（settings、health 钩子）——来自受控部署。
  - `MAIN/contracts/**` 契约内容——由 MAIN 权威维护。
- **我不信任什么**：
  - 一切客户端可控输入：密码、名单文件、`course_id`/`section_id`、刷新 cookie、令牌。
  - 前端可见性控制（B1 隐藏按钮**不构成**安全边界）。
- **关键不变量**：其他模块（B2/B3）拿到我签发的 JWT 后**本地验签**，**不回**调 B4；因此令牌必须自包含足够信息（`iss`/`aud`/`role`/课程作用域）。

## 2. 数据边界

- **我只读写 `b4` schema**（`user`/`credential`/`session`/`role`/`course`/`section`/`enrollment`/`audit`）。
- **我禁止**：跨 schema 外键 / SQL / ORM / 视图；读取或修改 `b2`/`b3` 物理表；`public` 共享 ORM。
- **我只负责实例、角色、schema、备份、健康**；B2/B3 物理表由它们自己的迁移维护（见 `../../MAIN/docs/database/ownership.md`）。
- 应用连接一律使用**最小权限 login role**，仅拥有 `b4` schema 权限；**禁止超级用户**。

## 3. 凭据边界

- **Ed25519 私钥来源**：环境变量 `JWT_PRIVATE_KEY_FILE` 指向的密钥文件（`secrets/` 目录，已在 `.gitignore` 中不提交仓库）；不硬编码、不进仓库、不进日志。
- **刷新凭据**：仅 `HttpOnly` + `Secure` + `SameSite` cookie；不出现在响应体（除登录响应的一次性访问令牌）、URL、日志、事件。
- **密码**：Argon2id 或 bcrypt 强哈希；**禁止**明文、可逆、弱哈希（MD5/SHA1）。
- **令牌**：短期（有明确过期）；不写入日志；不在 URL 中传递。

## 4. PII 与日志脱敏范围

- **日志禁止出现**：明文密码、JWT、刷新令牌、cookie 值、完整 PII（姓名+学号全量、邮箱全量、身份证号等）、内部绝对路径与主机名。
- **允许出现**：`user_id`/`course_id`/`section_id`/`roster_import_id` 等 UUID、`request_id`/`correlation_id`、脱敏后的错误类别与原因。
- **事件 payload 禁止出现 PII**：`autograder.v1.roster.import.completed` 只携带非敏感标识与统计计数。
- **审计范围**：认证、用户/角色管理、名单导入确认、授权拒绝等敏感操作，记录操作者、目标、变更前后**摘要**（非全量 PII）、时间、`request_id`。

## 5. 资源边界（记入账本）

- PostgreSQL ≤ 96 MB / 0.15 vCPU；数据 ≤ 1.5 GB。
- NATS ≤ 32 MB / 0.05 vCPU；JetStream 持久 ≤ 0.25 GB。
- 总部署 ≤ 10 GB（`../../MAIN/docs/workstreams/B4.md` 第 8 节）。
