# B3 工作包拆分（Workpackages）

与 `../../../MAIN/docs/workstreams/B3.md` §5 一致。3 条泳道，2~3 人并行认领。

## 并行前提（先冻结，冻结后他人不得再改）

| 接口 / 决策 | Owner | 状态 |
|---|---|---|
| `evaluation-package.schema.json` 结构 | MAIN | 契约已锁定（以 MAIN 为准） |
| 公开 DTO 与内部 DTO 的模块划分 | B3 团队（A 先定） | **已冻结 2026-09-15**（`ADR-0004`；`frozen-interfaces.md` §1） |
| package 编译器接口签名（A↔B 交界） | B3 团队 | **已冻结 2026-09-15**（`ADR-0005`；`frozen-interfaces.md` §2/§3） |
| `b3` schema 初始迁移 + 版本不可变约束 | A | **已完成**（`20260915_0002` 完整业务迁移，含不可变触发器） |
| `evaluation.completed` 去标识化 payload 字段集合 | MAIN/B2 | 契约已锁定（消费端白名单见 `ADR-0005` 与 `docs/api/events.md`） |
| file adapter 的"文本差异"比较粒度 | MAIN（缺口） | **待裁决**（`contracts/changes/CHG-B3-0001`） |
| 质量出口（诊断端点 / 修订字段 / 标签检索） | MAIN（`CHG-0013`）+ B3 落地 | **已完成**（B3 侧实现见 [Phase 04](../phases/active/phase-04-quality-surfaces.md)；仅 HTTP 契约级验证） |

**串行点**：① `b3` 初始迁移与版本不可变约束（A 先建）→ ② package 编译器签名（A↔B 交界）→ ③ 公开/内部 DTO 模块划分。契约变更一律走 MAIN 规程。

## WP-B3-A 题库与版本

- **工作包 ID**：`WP-B3-A`
- **覆盖 RQ**：`RQ-B-3-10`、`RQ-B-3-20`
- **目标**：题目聚合、版本不可变、assignment、发布流程、公开/内部 DTO 分离、发布事件。
- **输入**：B4 课程作用域（可先用契约夹具）；`../../../MAIN/contracts/openapi/b3.yaml`。
- **产出**：question / question_version / assignment 模型与迁移、富文本与图片资源存储、参考答案受保护存储、版本不可变约束、发布校验、公开/内部 DTO 分离、发布事件。
- **依赖**：B4 课程作用域（契约已锁定，可并行）；`b3` 初始迁移（本包内先建）。
- **验收**：3-10 的 5 条 + 3-20 的 10 条（见 [../requirements/acceptance.md](../requirements/acceptance.md)；其中 3-20-8~10 由 `CHG-0013` 于 Phase 04 增补）。
- **风险**：公开/内部 DTO 泄漏是最高危缺陷 → 用契约测试断言字段缺失，不是靠审阅。
- **预估工作量**：中大；**建议认领人数**：1 人（可先独立推进）。

## WP-B3-B 三类测试框架适配

- **工作包 ID**：`WP-B3-B`
- **覆盖 RQ**：`RQ-B-3-40`、`RQ-B-3-50`、`RQ-B-3-60`
- **目标**：adapter manifest（三型）、fixture 管理与摘要、package 编译器、统一校验入口、扩展点设计。
- **输入**：`../../../MAIN/contracts/schemas/evaluation-package.schema.json`（MAIN 权威）。
- **产出**：三型 adapter manifest 校验、fixture 摘要绑定、package 编译器（题目版本 → 不可变 package）、三型各自校验规则、扩展点（新增题型不改 B2 核心）。
- **依赖**：`evaluation-package.schema.json`（已锁定）；package 编译器签名（与 A 交界，需先冻结）。
- **验收**：3-40 的 4 条 + 3-50 的 4 条 + 3-60 的 5 条。
- **风险**：三套并行 package 结构会破坏"新增题型不改核心" → 必须共用统一结构与统一校验入口。
- **预估工作量**：大；**建议认领人数**：1 人。

## WP-B3-C 统计与日志

- **工作包 ID**：`WP-B3-C`
- **覆盖 RQ**：`RQ-B-3-30`、`RQ-B-3-99`
- **目标**：事件消费器（durable consumer + inbox 去重）、统计聚合表与查询、重放重建、结构化日志与脱敏。
- **输入**：B2 的 `evaluation.completed` 去标识化 payload（契约已锁定）；NATS（B4 运维）。
- **产出**：事件消费器、去重 inbox、预聚合表、统计查询、重放重建能力、`producer=B3` 脱敏日志。
- **依赖**：事件 schema（已锁定，可立即并行）；NATS durable consumer（B4）。
- **验收**：3-30 的 6 条 + 3-99 的 4 条。
- **风险**：事件 payload 含可识别信息会违反隐私边界 → 消费端字段白名单校验 + 文档明确禁止字段。
- **预估工作量**：中；**建议认领人数**：1 人。

## 认领表（初始留空）

| 工作包 | 认领人 | 状态 | 预计完成 |
|---|---|---|---|
| WP-B3-A | B3 实现者（AI Agent） | 已完成（见 `../phases/active/phase-01-question-bank.md`） | 2026-09-15 |
| WP-B3-B | B3 实现者（AI Agent） | 已完成（同上，含 `CHG-B3-0001` 缺口） | 2026-09-15 |
| WP-B3-C | B3 实现者（AI Agent） | 已完成（同上） | 2026-09-15 |

## 共享文件冲突预防

`plugin.py` 的 descriptor、`alembic/`、DTO 模块、错误码目录为共享文件；修改前在认领表 + 团队沟通，避免并行冲突。
