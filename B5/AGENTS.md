# B5 开发约定

遵守仓库根 [AGENTS.md](../AGENTS.md)，模块文档入口为 [docs/README.md](docs/README.md)。

- 提供平台一致性报告，读取 MAIN 契约与宿主装载状态；当前没有数据库和迁移，不代管其他模块业务数据。
- 使用 B4 签发的 JWT 本地验签，不记录 token、cookie 或完整 PII。
- 一致性检查保留能识别实际差异的负例；不为通过检查放宽断言，也不把报告结论当作完整业务验收。
- 按改动执行 `uv run ruff check` 和相关 pytest；组合测试需要真实模块与集成配置，不静默跳过。
