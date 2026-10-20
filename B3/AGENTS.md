# B3 开发约定

遵守仓库根 [AGENTS.md](../AGENTS.md)，模块文档入口为 [docs/README.md](docs/README.md)。

- 负责题库、不可变题目版本、作业、evaluation package 与去标识化统计，运行数据放在 `b3` schema。
- 评测执行和分数属于 B2；身份、课程属于 B4。通过接口协作，避免跨 schema SQL/ORM。
- 参考答案、隐藏用例、expected output 与 fixture 原始内容不得进入公开 DTO、事件、日志或学生可访问存储。
- 统计按 event_id 去重；UTC 时间与现有 UUID、错误码、数据库命名保持一致。
- 按改动执行 `uv run ruff check` 和相关 pytest；数据库行为在 PostgreSQL 验证，不用 SQLite 替代。
