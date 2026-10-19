# ADR 0001 插件式 composition host 与 schema 所有权

- **状态**：接受
- **背景**：

  四条开发线（B1～B4）由不同小组并行开发，最终必须组装成一个可运行的软件；同时整套系统受 **1 vCPU / 512 MB / 10 GB** 的硬资源预算约束（导图"目标运行环境"分支确认）。

  需要同时满足两个看似矛盾的目标：

  1. **功能完全解耦**：各模块遵守统一规范即可即插即用，模块之间不产生编译期与运行期耦合。
  2. **资源极度受限**：不能靠"每个模块一套独立服务 + 独立数据库 + 独立消息代理"来换解耦——那会直接超出内存与 CPU 预算。

  可选方案对比：

  | 方案 | 解耦程度 | 资源开销 | 结论 |
  |---|---|---|---|
  | A. 单体应用，四个模块作为同一进程内的 Python 包 | 低（容易互相 import、耦合） | 最低 | 否 |
  | B. 四个独立微服务 + 四个独立数据库 | 高 | 极高（PostgreSQL ×4 ≈ 400 MB，无法容纳） | 否 |
  | C. **微内核 host + 独立打包插件 + 单实例多 schema 数据库** | **高（靠契约而非进程/库隔离）** | **可控（单实例 PG ≈ 96 MB）** | **采纳** |
  | D. 单体 + 运行时插件（本项目方案） | 高 | 可控 | 见 C |

- **决策**：

  1. **B2、B3、B4 各自是独立的 Python 发行包**，拥有独立的源码树、依赖锁、测试、Alembic 迁移与 `module.yaml` 清单；**B1 是独立的静态前端项目**（构建产物为静态文件）。
  2. **MAIN 提供唯一一个 composition host**（FastAPI 应用），它：
     - 通过 Python entry point 组 `autograder.modules` **发现并装载**插件；
     - 按 **capability DAG** 解析装载顺序（`requires` 必须已被 `provides` 满足）；
     - 在启动时**拒绝**：重复路由、重复 schema 归属、缺失 capability、循环依赖、契约摘要漂移；
     - 聚合各模块的健康检查，并提供 live / readiness 端点；
     - 提供 B2/B3/B4 API。B1 的生产静态部署已由 ADR-0017 改为 Vercel。
  3. **单 PostgreSQL 实例，三 schema**：`b2`、`b3`、`b4`。
     - 每个模块拥有**独立的 login role**（最小权限）、独立的 `search_path`、独立的 Alembic 版本表。
     - **禁止**跨 schema 外键、跨 schema SQL、跨 schema ORM 关系、`public` 共享 ORM。
     - **物理表结构以各模块自己的迁移为唯一事实源**；MAIN 只维护所有权目录与 migration head，**不复制 DDL**。
     - **禁止**应用使用超级用户连接（超级用户仅供运维迁移/备份使用）。
  4. **模块之间只能通过版本化契约交互**：HTTP（OpenAPI 3.1）与事件（AsyncAPI 3.0 + 共享 JSON Schema）。
     - **禁止**跨模块 import（含 import 对方的 Pydantic 模型、枚举、常量）。
     - **禁止**直接读取对方的表。
  5. **契约摘要门禁**：`MAIN/contracts/` 全部文件字节参与 SHA-256；四个模块各自在 `contracts.lock` 中记录版本与摘要；集成检查要求四方一致。

- **后果**：

  - **获得（解耦）**：模块之间没有代码级依赖，只有契约级依赖。删除任一模块，host 会因为"缺失 capability"而**明确失败**，而不是静默降级——这正是"即插即用"的可验证形态。
  - **获得（资源）**：相比方案 B，PostgreSQL 只需一个实例；host 与所有插件共享一个 Python 进程（≈160 MB 预算），CPU 0.35 核。
  - **获得（可测）**：每个模块可**独立**运行自己的 lint / 类型检查 / 测试 / 迁移，不需要其他模块的实现存在。
  - **获得（可审计）**：数据边界由数据库权限**物理强制**，而不是靠代码约定。
  - **付出**：契约变更是串行点（四个模块都要跟），并行度受契约审批约束 → 用"契约先行 + 变更分级 + 冻结清单"缓解（见 `../contracts/change-protocol.md`）。
  - **付出**：单进程意味着一个插件的**严重崩溃可能影响整个 host**（Python 单进程无隔离）。缓解：插件不得在导入期做重 I/O；繁重工作（沙箱评测）通过**子进程**执行而非在 host 进程内运行；插件异常由 host 捕获并反映为 readiness 失败而非进程退出。
  - **付出**：单实例 PostgreSQL 是**单点**。本项目明确不做高可用（见 `../requirements/prd.md` 非目标），备份恢复由 B4 的 runbook 覆盖。
  - **遗留风险**：
    - 单进程内存预算是估算值，需在功能阶段**实测**并记录；超出则需重新分配账本（可能下调 nsjail 并发或 PostgreSQL 连接数）。
    - capability 命名空间目前是自由字符串（如 `b4-auth`、`b3-questions`），没有集中登记表，存在拼写漂移风险 → 由 `module.yaml` 校验 + 装载失败共同兜底；后续可考虑集中登记。
    - `ModuleDescriptor.events` 未区分 publishes/subscribes，无法校验事件方向（见 ADR-0003 遗留风险）。

- **关联**：
  - 需求：`RQ-B-0-10`、`RQ-B-0-20`、`RQ-B-4-10`、`RQ-B-4-20`、`RQ-B-4-30`、`RQ-B-4-40`
  - ADR：`ADR-0002`（需求基线）、`ADR-0003`（接口权威双层）、`ADR-0004`（沙盒选型）、`ADR-0006`（事件驱动解耦）
  - 实现：`MAIN/host/src/autograder_host/main.py`（装载与顺序解析）、`MAIN/sdk/src/autograder_platform/protocol.py`（`ModuleDescriptor` / `PlatformModule`）、`MAIN/contracts/manifest.schema.json`
  - 文档：`../architecture/overview.md`、`../database/ownership.md`
