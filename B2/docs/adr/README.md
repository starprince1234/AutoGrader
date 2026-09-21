# B2 ADR 索引

B2 的架构决策记录。格式遵循 [../../../MAIN/docs/collaboration/module-doc-spec.md](../../../MAIN/docs/collaboration/module-doc-spec.md) 第 3 节 ADR 模板。

| 编号 | 标题 | 状态 | 关联需求 |
|---|---|---|---|
| [ADR-0001](ADR-0001-static-analysis-tooling.md) | 静态检查工具选型与静态高危阻断动态运行 | 接受 | `RQ-B-2-40`、`RQ-B-2-70` |
| [ADR-0002](ADR-0002-sandbox-execution-policy.md) | 沙箱执行策略（nsjail + cgroup v2 + seccomp） | 接受 | `RQ-B-2-50`、`RQ-B-2-70` |
| [ADR-0003](ADR-0003-content-addressed-submission-storage.md) | 内容寻址提交存储与元数据一致性 | 接受 | `RQ-B-2-30`、`RQ-B-2-60` |

跨模块不可逆决策（插件式 host、接口权威双层切分、需求基线）见 [../../../MAIN/docs/adr/](../../../MAIN/docs/adr/)，B2 只引用不重述。
