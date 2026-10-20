# MAIN 开发约定

遵守仓库根 [AGENTS.md](../AGENTS.md)。

- 负责公共契约、Platform SDK、composition host 与部署组装；业务实现放在对应模块。
- 相关知识从 [docs/README.md](docs/README.md) 进入，按任务读取相关部分。
- `autograder_host.checks` 是专项契约与文档审计工具，不是日常开发或上线的前置条件。
- 后端发布和生产状态以 [生产手册](docs/runbooks/production-deployment.md) 为准。正常发布保留业务数据，重建单独授权。
- 安装集成后端使用 `uv sync --extra modules`；完成修改后按影响执行 ruff 和相关 pytest。
