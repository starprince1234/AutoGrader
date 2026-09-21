# B5 ADR 索引

本目录记录 **B5 自己的**决策。跨模块或不可逆的决策属 MAIN（`../../../MAIN/docs/adr/`），
本模块只引用编号，不复述内容。

| 编号 | 标题 | 状态 | 影响范围 |
|---|---|---|---|
| [ADR-0001](ADR-0001-read-only-conformance-auditor.md) | B5 是只读审计器：无 schema、无事件、无写入 | 接受 | B5、MAIN 契约、运维 |

## 与 MAIN ADR 的关系

| MAIN ADR | 与本模块的关系 |
|---|---|
| [ADR-0001](../../../MAIN/docs/adr/ADR-0001-plugin-composition-and-schema-ownership.md) | B5 是其"插件式组合"主张的第五个实例；也正因它，B5 不拥有 schema |
| [ADR-0003](../../../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md) | 设计期权威归 MAIN、运行期注册表归 B4；B5 报的是**第三个问题**（运行组合 vs 契约），不越界 |
| [ADR-0007](../../../MAIN/docs/adr/ADR-0007-b1-static-client-provided-by-host.md) | B1 不在 capability DAG 内；B5 的报告因此把 `/app` 视为**宿主平台路由**而非模块路由 |
| [ADR-0008](../../../MAIN/docs/adr/ADR-0008-module-settings-view.md) | B5 的 `B5_` 前缀变量由宿主映射为无前缀名 |
| [ADR-0011](../../../MAIN/docs/adr/ADR-0011-cross-module-composition-via-api.md) | B5 不读别的模块的 schema/代码，只读 SDK 描述符与公开 JWKS |
| [ADR-0013](../../../MAIN/docs/adr/ADR-0013-open-module-count.md) | B5 存在的理由；本模块是它的验收物 |

变更记录：[CHG-0010](../../../MAIN/docs/contracts/changes/CHG-0010-b5-conformance-module.md)（新增 B5 与 `b5.yaml`）。
