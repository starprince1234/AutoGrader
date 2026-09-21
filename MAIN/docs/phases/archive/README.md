# 已归档阶段

已归档阶段表示**其完成条件已被验证满足**。归档文件原则上不再改写；若需纠正历史事实，请追加修订说明，不要静默重写结论。

归档门禁的完整判据见 [../README.md](../README.md)。

## 索引

| 阶段 | 结论 | 完成日期 | 后续阶段 |
|---|---|---|---|
| （暂无归档阶段） | — | — | — |

> **当前无归档阶段**：
>
> - Phase 00（初始化与文档治理）仍在 `active/`：完成条件 **11（资源实测）** 尚未达成。
>   条件 6（host 真实装载）、9（各模块静态检查与测试真实通过）与 10（PostgreSQL/NATS/host
>   统一启动 + readiness）已在 [Phase 01](../active/phase-01-integration-runtime.md) 达成并留有读数。
> - Phase 01（运行时集成与端到端验证）在 `active/`：Linux 安全/资源门禁与真实 `completed`
>   分数路径未执行——本环境是 Windows，没有 nsjail / cgroup v2，沙箱阶段如实返回
>   `infrastructure_error`（ADR-0004 与 SEC-20 **禁止**降级为裸进程）。
>
> 详见 [../active/phase-00-initialization.md](../active/phase-00-initialization.md) 第 5.2 节与
> [../active/phase-01-integration-runtime.md](../active/phase-01-integration-runtime.md) 第 6 节。

## 归档时需要记录的信息

| 字段 | 说明 |
|---|---|
| 阶段名 | 与文件名一致 |
| 结论 | 完成了什么、达成了哪些完成条件 |
| 完成日期 | 归档日期 |
| 后续阶段 | 下一阶段的文件链接 |
| 承接的遗留项 | 若前一阶段有遗留项转入本阶段，链接其来源 |
