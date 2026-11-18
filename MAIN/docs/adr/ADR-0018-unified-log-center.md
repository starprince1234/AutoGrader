# ADR 0018 统一日志中心：MAIN host 进程内 Sink 写 PostgreSQL

- **状态**：接受
- **背景**：
  B1–B4 各自输出结构化日志（B1 仅进浏览器 console，B2/B3/B4 写 stdout），B5 仅使用标准 logging，缺乏集中存储、跨模块链路追踪、统一脱敏校验、导出与权限控制。同时平台运行在 **1 vCPU / 512 MB 内存 / 10 GB 磁盘**（其中日志与临时文件总预算 ≤0.25 GB）的硬约束下，无法容纳 ELK、Loki、Kafka 等外部日志组件。

- **决策**：
  1. **归属 MAIN host**：在 `MAIN/host/src/autograder_host/logs/` 实现统一日志汇集、二次脱敏、查询/追踪/总览/导出 API、保留期/容量清理及告警邮件，不新增独立微服务或业务模块。
  2. **进程内 SinkHandler**：各入口（`host`、`worker`、`intake`、`notifier`）通过 `attach_log_sink(process=...)` 挂载非阻塞有界内存队列，后台批量 `INSERT` 到 PostgreSQL `logs.log_events`；队列满时丢弃最旧记录并累计丢弃计数，写库失败自动降级（保留 stdout 兜底），绝不阻塞或中断主流程。
  3. **最小权限与非破坏性迁移**：`logs` schema 划分只写入（`autograder_logs_writer`，仅 `INSERT`）、只读（`autograder_logs_reader`，仅 `SELECT`）与清理运维角色（`autograder_logs_ops`），由 `production-prepare.sh` 幂等供给，不重建生产数据库。
  4. **容量与保留期**：依据 Q0 估算，默认保留 info 7 天、warn/error 30 天，容量硬上限 128 MB（超出优先清理最旧 `info`，再 `warn`，最后 `error`）。
  5. **访问控制与脱敏**：入库前执行白名单字段过滤与二次脱敏（统一占位 `[REDACTED]`），控制字符转义防注入；管理员全量可读，教师仅通过 SQL 查询过滤读取授权课程范围，学生 403 拒绝；查看与导出均记审计。
