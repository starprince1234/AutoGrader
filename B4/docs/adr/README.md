# B4 ADR 索引

本目录记录 **B4 模块内部**的不可逆/重要设计决策。跨模块决策的权威在 `../../MAIN/docs/adr/**`（本模块只引用不复制）。

| 编号 | 标题 | 状态 | 关联 RQ | 实现落点 |
|---|---|---|---|---|
| `ADR-0001` | JWT/JWKS 与会话凭据存放 | 接受 | `RQ-B-4-50` | `security/tokens.py`、`security/refresh.py`、`api/auth.py` |
| `ADR-0002` | 单实例多 schema 的最小权限角色 | 接受 | `RQ-B-4-10`、`RQ-B-4-30` | `ops/provision.py`、`config.assert_not_superuser` |
| `ADR-0003` | 名单导入管道（不确定即报错） | 接受 | `RQ-B-4-50`（配合 `RQ-B-1-10`） | `roster/**` |

> 三条 ADR 覆盖的设计均已实现并通过真实环境测试（见 `../phases/active/phase-01-platform-implementation.md`），
> 因此状态由 `提议` 改为 `接受`。实现过程中新增的两项决定记录在此，避免只存在于代码里：
>
> - **课程作用域的产生方式**：`b4.roles` 同时表达平台级与课程级授予；`createCourse` 为创建者写入课程级
>   `teacher` 授予，另有运维命令用于追加授予。契约中没有对应 operation，缺口已记录（见
>   `../api/contract.md` 复核发现 4）。
> - **DLQ 的载体**：使用 JetStream 自身的 advisory 主题而不自造 `…dlq` domain subject，避免把运维构造
>   混入领域事件词汇（见 `../api/events.md`）。

格式固定：状态（提议/接受/取代/拒绝）、背景、决策、后果、关联。决策被团队接受后把状态改为 `接受`。
