# B2 开发约定

遵守仓库根 [AGENTS.md](../AGENTS.md)，模块文档入口为 [docs/README.md](docs/README.md)。

- 负责提交、评测、分数与证据，运行数据放在 `b2` schema；通过接口获取题目与身份信息。
- 学生代码只在 nsjail 中以非 root 身份运行，保持禁网、资源限制与超时清理；启动命令使用参数数组。
- 不在公开响应、事件、日志、导出中泄漏源码、参考答案、隐藏用例或凭据。存储路径由服务端生成。
- 按改动执行 `uv run ruff check` 和相关 pytest。真实 runner 的测试需要 Linux、cgroup v2 和 nsjail，不用模拟结果替代。
