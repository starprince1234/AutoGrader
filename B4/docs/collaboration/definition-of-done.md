# B4 完成定义（DoD）

一项需求"算完成"必须逐条通过；每条给出**可核验动作**。

| 检查项 | 可核验动作 |
|---|---|
| 代码 | 实现代码存在，且不在 `src/**` 外散落逻辑 |
| 测试 | `uv run pytest` 通过；对应 `T-*` 用例从 `待建` 改为 `存在` 且通过 |
| 迁移 | `uv run alembic upgrade head` 在空库成功；无手工改库 |
| head 一致 | `alembic_version_b4` head 与 `module.yaml: migration_head` 一致 |
| 契约一致 | 不改 `MAIN/contracts/**`；实现与契约语义一致（按 `change-protocol.md` 判断） |
| 文档 | `contract.md`/`events.md`/`schema.md`/`traceability.md`/`phases/active/` 已同步 |
| 追踪表 | `requirements/traceability.md` 对应 RQ 行状态改为 `已完成`，证据位置指向真实测试 |
| 阶段记录 | `phases/active/` 记录真实命令 + 环境 + 结果，无未执行项隐瞒 |
| 安全与脱敏 | 无明文密码/JWT/cookie/PII 入日志；脱敏测试通过 |
| 无 mock 残留 | 无 mock 冒充 PostgreSQL/NATS 集成验证；mock 仅限不可控外部服务 |
| 数据边界 | 无跨 schema 访问；最小权限角色；禁止超级用户 |

未同时满足全部条目的，状态只能是 `待实现`，不得标 `已完成`。
