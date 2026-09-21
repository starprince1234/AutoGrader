# B3 ADR 索引

本目录记录 B3 模块级决策。跨模块不可逆决策见 `../../../MAIN/docs/adr/**`。状态：`提议` / `接受` / `取代` / `拒绝`。

| ADR | 标题 | 状态 | 关联需求 |
|---|---|---|---|
| [ADR-0001](ADR-0001-immutable-question-versions.md) | 不可变题目版本与"改必新版本" | `接受` | `RQ-B-3-20` |
| [ADR-0002](ADR-0002-evaluation-package-compilation.md) | 题目版本 → 不可变 package 的编译与摘要绑定 | `接受` | `RQ-B-3-40/50/60`、`RQ-B-0-10` |
| [ADR-0003](ADR-0003-public-internal-dto-separation.md) | 公开/内部 DTO 分离与受保护字段永不外泄 | `接受` | `RQ-B-3-10` |
| [ADR-0004](ADR-0004-public-internal-dto-module-boundary.md) | 公开/内部 DTO 模块边界冻结与序列化白名单 | `接受`（2026-09-15 冻结） | `RQ-B-3-10`、`RQ-B-3-20` |
| [ADR-0005](ADR-0005-package-compiler-and-adapter-interface-freeze.md) | package 编译器签名与 adapter 转换接口冻结 | `接受`（2026-09-15 冻结） | `RQ-B-3-40/50/60` |

- ADR-0001/0002/0003 原为 `提议`，2026-09-15 随 WP-B3-A/B 实现落地转为 `接受`：三条决策均**不使用** MAIN 未授权的扩展（package 结构、事件 subject、DTO 字段一律以 `MAIN/contracts/**` 为准），因此不需要契约变更即可接受。
- ADR-0004/0005 是 `workpackages.md` 三个"待冻结"串行点的冻结结果，具体签名见 [../collaboration/frozen-interfaces.md](../collaboration/frozen-interfaces.md)。
- 涉及 MAIN 契约的**缺口**（不是决策）单独走 `CHG-*`：见 [../contracts/changes/](../contracts/changes/)。
