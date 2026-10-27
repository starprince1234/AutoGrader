# Phase 04 找回密码验证码与凭据重置

本文件记录 B4 侧本轮的真实改动与未验证项。

## 范围

- 新增 `b4.password_reset_codes`（迁移 `20261027_0005`）与两条公开认证操作：
  - `POST /api/v1/auth/password-reset/code`（`requestPasswordResetCode`）
  - `POST /api/v1/auth/password-reset`（`completePasswordReset`）
- 契约版本 `0.20.0` → `0.22.0`（`0.21.0` 已被上游 P13/CSV 批次占用），六份契约文件、五个模块版本与锁同步。

## 关键设计

1. **不枚举账号**：发码对任意合法邮箱返回同一 `202` 结构与数值；只有启用中的账号会收到邮件。
   完成接口把所有验证码失败统一成一个 `422` detail。
2. **只存摘要**：`code_hash` 是绑 `code_id` 的 SHA-256，明文验证码与口令不落库、不进日志、不进审计。
3. **一次性与限流**：复用注册邮件通道的 10 分钟 TTL、重发窗口、5 次尝试上限与小时配额；
   错码在事务提交后返回，保证尝试次数不会被回滚。
4. **改密即撤销会话**：成功后更新 Argon2id 凭据，撤销该账号全部 refresh 会话，清理失败登录计数；
   已签发的 access token 在剩余短 TTL 内仍有效（JWT 无状态）。
5. **邮件失败零残留**：SMTP 未配置或投递失败返回 `503`，删除刚写入的意图行，避免冷却窗口
   阻塞用户重试。

## 实际执行

| 命令 | 结果 |
|---|---|
| `uv tool run ruff check B4/src B4/tests/test_password_reset.py B4/tests/conftest.py B4/alembic/versions/20261027_0005_password_reset.py` | 通过 |
| `uv run --project MAIN python -m autograder_host.checks` | 通过（契约、清单、迁移头、文档链接与端口一致性） |
| B4 `pytest` | **未执行**：本机没有 PostgreSQL（55435）与 NATS（54224），B4 套件要求真实实例 |
| 真实迁移 `upgrade`/`downgrade` | **未执行**（同上） |
| 真实 SMTP 发码/改密 | **未验证** |

`tests/test_password_reset.py` 已覆盖：只给启用账号发码、未知/停用邮箱不泄露、重发窗口、
改密后 refresh 会话失效、错误码统一且计数、尝试上限、SMTP 失败零残留、短密码不消费验证码。

## 回滚

回退迁移 `20261027_0005`，还原本批契约文本与版本号，移除两个 operation，然后重跑
`scripts/relock_contracts.py`；版本回到 `0.20.0`。
