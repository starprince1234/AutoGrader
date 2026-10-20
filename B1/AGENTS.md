# B1 开发约定

遵守仓库根 [AGENTS.md](../AGENTS.md)，模块文档入口为 [docs/README.md](docs/README.md)。

- React、TypeScript、Vite 前端，业务数据通过 API 获取。API 类型来自 MAIN 契约，不连接数据库或 NATS。
- 富文本按白名单渲染；不把 token 存入 localStorage，不公开参考答案和隐藏用例。SSE 按事件标识去重。
- 按改动选择 `pnpm lint`、`pnpm typecheck`、`pnpm api:check` 或相关 Vitest；只有需要交付构建时才运行 `pnpm build`。`pnpm verify` 用于完整前端验收。
