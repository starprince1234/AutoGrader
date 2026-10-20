# B4 开发约定

遵守仓库根 [AGENTS.md](../AGENTS.md)，模块文档入口为 [docs/README.md](docs/README.md)。

- 负责身份、课程、选课、会话、审计、数据库角色、消息基础设施与运行期注册表，业务数据放在 `b4` schema。
- 应用使用最小权限角色；业务代码不跨 schema 操作其他模块的表。
- 密码使用现有不可逆哈希；刷新凭据使用安全 HttpOnly cookie；JWT、cookie、密码与超范围 PII 不写日志或事件。
- 可按用户任务修改源码、测试、配置及契约消费者；不存在“本次只改文档”的长期限制。
- 按改动执行 `uv run ruff check` 和相关 pytest；只在实际需要变更数据库结构时执行 Alembic。
