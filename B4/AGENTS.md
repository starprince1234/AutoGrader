# AutoGrader B4 Agent 规则

面向在本仓库 `B4/` 下工作的 AI Agent。执行任何改动前先读完本文件。

## 开始任务前

1. 读 `docs/README.md`（权威表 + 状态表）、`docs/requirements/traceability.md`、`docs/architecture/boundaries.md`。
2. 读 MAIN 侧：`../MAIN/docs/workstreams/B4.md`、`../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md`、`../MAIN/docs/database/ownership.md`。
3. 确认契约版本与摘要：本仓库 `contracts.lock`（`contract_version=0.1.0` 与 `digest`）；**不得改动**它。
4. 检查 Git 状态；本次任务只改文档（`*.md`），不碰 `contracts.lock` / `module.yaml` / `pyproject.toml` / `src/**` / `tests/**` / `alembic/**`。

## 我负责什么 / 不负责什么

- 我拥有 `b4` schema：`user` / `credential` / `session` / `role` / `course` / `section` / `enrollment` / `audit`。
- 我拥有 PostgreSQL 实例/角色/schema/健康/备份，NATS JetStream 运行期维护，运行期模块注册表。
- 我不拥有 B2/B3 的物理表与表结构（属 B2/B3）；不拥有题目/提交/分数。
- 我不是 `MAIN/contracts/**` 的作者，也不修改它。

## 硬规则

- 禁止跨 schema 外键 / SQL / ORM / 视图；只读写 `b4`。
- 禁止代管或修改 `b2`/`b3` 物理表。
- 禁止应用使用超级用户连接（一律最小权限角色）。
- 禁止明文或可逆存储密码（Argon2id 或 bcrypt）。
- 刷新凭据仅 `HttpOnly`+`Secure`+`SameSite` cookie；访问令牌不出现在 URL、响应体（除登录一次性访问令牌）、日志、事件。
- 日志与事件不含明文密码 / JWT / cookie 值 / 超范围 PII；事件 payload 不含 PII。
- 禁止 mock 冒充已验证能力；禁止兼容层/别名/降级路径；禁止 shell 字符串拼接执行命令。
- 状态词只用：`骨架` / `待实现` / `已完成` / `阻塞`。

## 文档同步

改代码时同步：`docs/api/contract.md`、`docs/api/events.md`、`docs/database/schema.md`、`docs/requirements/traceability.md`、`docs/phases/active/`。`docs/README.md` 有增删/移动时同步索引。

## 验证

```powershell
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run alembic upgrade head   # 需可用的 PostgreSQL（DATABASE_URL 覆盖宿主端口）
```

## 契约变更

发现契约与实现不一致 → 不偷改契约、不偷改实现对齐 → 走 `../MAIN/docs/contracts/change-protocol.md` 提交 CHG，由 MAIN 判定哪边为准。
