# Phase 08 找回密码与密码可见性

本文件记录本轮**真实执行**的改动、命令与未验证项。状态词只用 `已完成` / `部分` / `未验证`。
范围：登录页「忘记密码？」、`/forgot-password` 页面、B4 密码重置接口与迁移，以及登录密码框
显示/隐藏按钮的修复。

## 目标与取舍

用户此前忘记密码只能让管理员重建账号：登录页文档明确写着"没有忘记密码入口，除非后端新增
真实重置流程"。本轮补上完整闭环，同时修复一个登录体验缺陷：浏览器原生密码"小眼睛"在输入框
失焦后会消失，登录失败后用户无法再核对刚输入的密码。

关键取舍：

1. **两条公开接口，不泄露账号是否存在**：发码 `202` 对任意合法邮箱返回同一结构；只有启用中账号
   会收到邮件。完成接口对未知邮箱、无可用码、错误码、尝试耗尽统一返回同一个 `422` detail。
2. **验证码只存摘要**：新表 `b4.password_reset_codes` 只保存绑 `code_id` 的 SHA-256 摘要，
   明文验证码与口令不写数据库、日志或审计。
3. **成功后撤销全部 refresh 会话**：密码是账号的恢复凭据，改密必须让旧会话失效；已签发的
   access token 只在剩余短 TTL 内有效（JWT 无状态）。同时清理失败登录计数，避免改密后仍被限流。
4. **复用注册邮件通道的 TTL/重发/小时上限**：不为同一条 SMTP 通道再引入一套限额；SMTP 缺失或
   投递失败返回 `503`，并删除刚写入的意图行，避免冷却窗口阻塞重试。
5. **密码显示按钮由页面自己维护**：不再依赖浏览器原生的失焦隐藏行为；按钮状态在登录失败后
   保持，图标为内联 SVG，按钮有可读的 `aria-label` 与 `aria-pressed`。

## 契约与后端

- `MAIN/contracts/openapi/b4.yaml` 新增：
  - `POST /api/v1/auth/password-reset/code`（`requestPasswordResetCode`）
  - `POST /api/v1/auth/password-reset`（`completePasswordReset`）
- 契约版本 `0.20.0` → `0.22.0`（`0.21.0` 已被上游 P13/CSV 批次占用）；六份契约文件的 `info.version`、五个模块的 `contract_version`
  与五份 `contracts.lock` 同步，摘要由 canonical `contract_digest()` 计算为
  `22bbbfab886d5f198c2f9caf3bc0f23440755f059fb7fd43bd6acba53db700f9`。
- B4 迁移 `20261027_0005_password_reset.py`：新表 + 用户外键 + 摘要/归一化邮箱/尝试次数 CHECK +
  查询索引 + 应用角色授权；`module.yaml` 的 `migration_head` 推进到 `20261027_0005`。
- B4 API `api/password_reset.py`：发码、完成重置、审计动作、会话撤销；邮件模板
  `send_password_reset_code`；审计常量 `auth.password_reset.*`。
- B4 `tests/test_password_reset.py`：覆盖只给启用账号发码、未知/停用邮箱不泄露、重发窗口、
  改密后刷新会话失效、错误码统一、尝试上限、SMTP 失败零残留、短密码不消费验证码。

## 前端

- `src/features/auth/ForgotPasswordPage.tsx` + `/forgot-password` 路由（公开页）：
  「验证邮箱」与「设置新密码」两个区块；邮箱 + 发送验证码同一行；验证码冷却；字段级错误；
  成功后回登录页并显示「密码已重置，请使用新密码登录。」。
- `src/features/auth/passwordResetApi.ts`：两个公开调用，类型来自生成类型。
- `LoginPage.tsx`：
  - 增加「忘记密码？」入口；
  - 密码框增加页面自维护的显示/隐藏按钮（`Field` 新增 `trailing` 插槽），失败后状态不丢；
  - `?reset=done` 显示重置成功提示。
- `src/ui/components.tsx`：`Field` 支持行内 `trailing` 控件；`src/styles.css` 增加
  `.field__control` / `.field__action`，并隐藏 Edge 原生 `::-ms-reveal`，避免两个图标重叠。
- 新增 `LoginPage` 显示/隐藏与忘记入口测试、`ForgotPasswordPage` 六个流程测试。

## 真实执行的命令与结果

| 命令 | 结果 |
|---|---|
| `node node_modules/typescript/bin/tsc -b --pretty false`（B1） | 通过 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0`（B1） | 通过（0 error / 0 warning） |
| `node node_modules/vitest/vitest.mjs run`（B1 全量） | **46 个测试文件 / 398 个用例全部通过**（exit 0） |
| `node scripts/generate-api-types.mjs --check`（B1） | 通过（生成物与 `MAIN/contracts` 一致） |
| `node node_modules/vite/bin/vite.js build`（B1） | 通过（只有既有 >500kB chunk 警告） |
| `uv run --project MAIN python -m autograder_host.checks` | 通过（契约、清单、迁移头、文档链接与端口一致性） |
| `uv tool run ruff check B4/src B4/tests/test_password_reset.py B4/alembic/versions/20261027_0005_password_reset.py` | 通过 |

**未执行**：

- B4 `pytest`：本机没有 PostgreSQL（55435）与 NATS（54224），B4 测试套件要求真实实例；因此
  新接口的数据库集成测试、迁移 `upgrade`/`downgrade` 均为**未验证**（代码与测试已写）。
- 真实 SMTP：没有发送真实找回密码邮件，也没有在真实浏览器中完成"收码 → 改密 → 登录"闭环。
- 线上部署与生产账号重置：只验证了本地构建与契约门禁，未部署、未对生产账号执行改密。
- 1440 / 768 / 390 三档视口截图与深色模式视觉验收：未执行。

## 顺带修正

`B2/docs/phases/active/phase-10-quarantine-row-scope.md` 的一处相对链接少了一层 `../`，
会让 MAIN 的文档链接门禁失败；已改为 `../../database/migrations.md`。这是让本轮契约门禁
可执行的最小修正，不涉及 B2 业务逻辑。

## 回滚

还原本轮契约文本与版本号、回退 B4 迁移 `20261027_0005`、移除 B1 的 `/forgot-password` 页面与
登录入口，然后重跑 `scripts/relock_contracts.py`；版本回到 `0.20.0`。
