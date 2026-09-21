# ADR 0003 接口权威的双层切分：设计期归 MAIN，运行期归 B4

- **状态**：接受
- **日期**：初始化阶段
- **背景**：
  - 课程需求 `RQ-B-4-40` 要求"定义管理各模块间 API 调用接口"，归属 B4。
  - 同时 MAIN 的总目标是"统筹组合各个模块的连接契约"，且必须能在集成时拒绝契约漂移。
  - 若两方都认为自己在"定义接口"，必然出现两套接口描述、互相覆盖、以及"改了但对方不知道"的并行开发事故。
- **决策**：
  将"接口"职责按**时间维度**切分为两层，各有唯一 owner：
  1. **设计期（MAIN 拥有）**
     - `MAIN/contracts/**` 下的 OpenAPI / AsyncAPI / JSON Schema 是**唯一机器可读权威**。
     - 契约版本号、SemVer 规则、变更审批、摘要门禁（`contracts.lock`）由 MAIN 执行。
     - 任何模块**不得**自行新增、重命名或删除契约中的路径、operationId、事件 subject 或 schema 字段。
  2. **运行期（B4 拥有）**
     - B4 实现"模块运行期注册表"：发现已装载模块、暴露其 `descriptor`、聚合健康、暴露能力/路由/事件/schema 归属/迁移 head 的查询端点。
     - B4 保证注册表内容与各模块 `module.yaml` 及 SDK `descriptor` **三方一致**；不一致时以"明确失败"处理，不静默容忍。
     - 注册表使"即插即用"在运行时可被观测与验证：新增一个遵守规范的模块应无需修改 MAIN 代码即可被装载并出现在注册表中。
- **后果**：
  - **获得**：接口变更只有一个入口（MAIN 契约），运行期一致性有可观测出口（B4 注册表）；两个 owner 的边界可由"是否修改了 `MAIN/contracts/`"机械判定，无需争论。
  - **获得**：B4 可以独立实现注册表而不必等待 MAIN 定契约；MAIN 可以独立演进契约而不必实现运维功能。
  - **付出**：契约变更对四个模块都是串行点，必须按 `docs/contracts/change-protocol.md` 执行；变更延迟会影响并行度。
  - **付出**：需要在 B4 与 MAIN 各维护一份"一致性校验"的实现（MAIN 在 `host/src/autograder_host/checks.py`，B4 在注册表校验中），两者都不复制 DDL 或字段表。
  - **遗留风险**：当前 SDK 的 `ModuleDescriptor.events` 是**扁平元组**，而 `module.yaml` 区分 `publishes`/`subscribes`，因此 host 目前无法校验事件声明的发布/订阅方向。这是一个已知缺口，不影响启动校验的其他部分；修复需要 SDK 破坏性变更（`events` → `publishes`/`subscribes`）并同步四个模块与 manifest schema，应在独立的契约变更中处理。
- **关联**：
  - 需求：`RQ-B-4-40`、`RQ-B-4-30`
  - 契约：`MAIN/contracts/openapi/*.yaml`、`MAIN/contracts/manifest.schema.json`
  - ADR：`ADR-0001`（插件式 host 与 schema 所有权）、`ADR-0002`（需求基线）
  - 规程：`MAIN/docs/contracts/change-protocol.md`
  - 实现：`MAIN/host/src/autograder_host/checks.py`、`MAIN/sdk/src/autograder_platform/protocol.py`
