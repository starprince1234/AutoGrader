# 课程知识应用矩阵

本文件把课程教材（`MAIN/references/Pressman_SEPA_9e_Ch001…Ch030`）的每个章节**映射到本项目中的具体产物**。

**写作原则**：不罗列章节标题，只说明"这一章的哪个概念，用在了本项目的哪个文件/机制上，怎么验证它真的被用上了"。若某章在本项目中**没有**落点，就写"未落到具体产物"——**不编造应用**。

教材：Pressman, *Software Engineering: A Practitioner's Approach*, 9th ed.（课程 PPT 章节标题取自 `references/` 中的实际文档）。

---

## 1. 第一部分：软件过程（Ch1–Ch5）

| 章 | 主题 | 本项目落点 | 怎么证明用上了 |
|---|---|---|---|
| Ch1 | Software and Software Engineering | 产品定位与工程化边界：[prd.md](requirements/prd.md) 的"它解决什么问题"（三类真实痛点）、非目标清单 | PRD 中每条非目标都在 `srs.md` 有对应"系统不做什么"；不是泛泛而谈 |
| Ch2 | Process Models | **过程模型显式选择为"契约冻结后并行的增量过程"**：[workstreams/](workstreams/) 四份任务书 + [phases/](phases/) 阶段制 | 四条并行开发线的存在本身；契约变更是唯一串行点，见 [change-protocol.md](contracts/change-protocol.md) |
| Ch3 | Agility and Process | 可认领工作包与短周期交付：各模块 `docs/collaboration/workpackages.md`（3 泳道）+ [module-doc-spec.md](collaboration/module-doc-spec.md) | 每个工作包有 ID、覆盖 RQ、产出、依赖、验收；认领表可填 |
| Ch4 | Recommended Process Model | 阶段门禁与归档规则：[phases/README.md](phases/README.md) 的 7 条归档门禁 | 归档门禁被**真实执行**过：Phase 00 因条件 9/10/11 未满足而**拒绝归档** |
| Ch5 | Human Aspects of Software Engineering | 冷启动可验证（onboarding）+ 文档三条铁律 + 明确"前端隐藏不是授权" | [testing/acceptance.md](testing/acceptance.md) §4 规定"只读三个文件即可开始认领"为验收项；module-doc-spec 要求 onboarding 是分步可执行路径 |

## 2. 第二部分：建模（Ch6–Ch14）

| 章 | 主题 | 本项目落点 | 怎么证明用上了 |
|---|---|---|---|
| Ch6 | Principles that Guide Practice | 三条铁律（单一权威 / 不声称未实现 / 失败留在文档）：[collaboration/module-doc-spec.md](collaboration/module-doc-spec.md) §1 | 铁律被**自动化强制**：`checks.py` 的文档门禁与摘要门禁 |
| Ch7 | Understanding Requirements | 需求工程全流程：[rq-catalog.md](requirements/rq-catalog.md)（32 条逐字原文 + 规范解释 + 可测验收）、[prd.md](requirements/prd.md)（用户旅程） | 需求优先级与冲突裁决被写成可引用决策（ADR-0002 的冲突证据表） |
| Ch8 | Requirements Modeling – A Recommended Approach | 用例、状态机、数据流：[architecture/models.md](architecture/models.md)（三张状态机 + sequence + 实体关系 + 证据模型） | 状态取值与代码常量、契约枚举**三方一致**；`rejected`/`failed`/低分的语义区分被写成约束 |
| Ch9 | Design Concepts | 抽象、模块化、信息隐藏、耦合与内聚：[ADR-0001](adr/ADR-0001-plugin-composition-and-schema-ownership.md) 用"解耦程度 vs 资源开销"四方案对比做选择 | **信息隐藏的物理化**：protecting 参考答案/隐藏用例靠公开 DTO 与内部 DTO 的**物理分离**，不靠"约定不要返回" |
| Ch10 | Architectural Design – A Recommended Approach | 多视图建模：[architecture/overview.md](architecture/overview.md) 的上下文图 / 容器图 / 组件图 / 数据流 / 失败矩阵 | 每个视图都有对应的**可执行验证**（readiness、装载失败矩阵、集成用例 INT-01…17） |
| Ch11 | Component-Level Design | 组件职责与**扩展点**：[architecture/overview.md](architecture/overview.md) §3 + 各模块 `docs/architecture/components.md`；SDK 协议 `sdk/src/autograder_platform/protocol.py` | "新增题型不改 runner 核心"是**设计目标 + 验收项**：三类 adapter 共用同一 package 结构（`evaluation-package.schema.json`） |
| Ch12 | User Experience Design | UX 需求与一致性：[srs.md](requirements/srs.md) NFR-07（反馈、破坏性操作二次确认、错误信息不泄露内部信息） | 每条 UX 要求都绑定了可验证行为（如"未完成评测不得显示 0 分"） |
| Ch13 | Design for Mobility | **未落到具体产物**：本项目为桌面 Web（响应式），课程需求未要求移动端 | 已在 PRD 非目标中明确"不做移动端原生 App" |
| Ch14 | Pattern-Based Design | 本项目实际使用的模式：[ADR-0001](adr/ADR-0001-plugin-composition-and-schema-ownership.md)（微内核/插件）、[ADR-0006](adr/ADR-0006-event-driven-decoupling.md)（outbox/inbox、publish-subscribe）、公共错误响应（Problem Details 作为统一契约形状）、schema 版本化 | 每个模式都指明**为什么选它**并列出被否方案；`ADR-0006` 的备选对比表含 Kafka/RabbitMQ/Redis Streams 及否决策略 |

## 3. 第三部分：质量与安全（Ch15–Ch23）

| 章 | 主题 | 本项目落点 | 怎么证明用上了 |
|---|---|---|---|
| Ch15 | Quality Concepts | 质量的定义落在**可测量的性质**上：可解释、可重放、可审计（[ADR-0005](adr/ADR-0005-deterministic-replayable-scoring.md)） | 三条性质各有**自动化断言**：合计校验、五元摘要重放一致、报告可追溯 |
| Ch16 | Reviews – A Recommended Approach | 形式化评审点：契约变更的影响分析（[change-protocol.md](contracts/change-protocol.md) 第 2 步，含安全与隐私检查、资源检查、需求一致性检查）；模块文档规范的自检清单 | 变更流程把"评审"变成**有明确检查项的步骤**，而不是"开会看看" |
| Ch17 | Software Quality Assurance | SQA 门禁自动化：[testing/strategy.md](testing/strategy.md) + `checks.py` 六类检查 | 门禁**真实拦截过三次**缺陷（见 [phase-00](phases/active/phase-00-initialization.md) §5.1 的过程记录） |
| Ch18 | Software Security Engineering | STRIDE 威胁建模：[architecture/security.md](architecture/security.md)（六类威胁逐项 → 缓解 → 验证），配套 16 条安全门禁 | 每条威胁都有对应的**可执行验证**（G-SEC-01…16），并明确标注哪些**只能在 Linux** 验证 |
| Ch19 | Software Testing – Component Level | 单元与契约测试：[testing/strategy.md](testing/strategy.md) 分层表 | **契约测试断言"字段缺失"**（公开 DTO 无答案字段），而不是断言"字段存在" |
| Ch20 | Software Testing – Integration Level | 集成与端到端：[testing/cases.md](testing/cases.md) INT-01…17、E2E-01…30 | 用例绑定到具体 RQ；17 条集成用例专门覆盖**即插即用**（缺失/重复/漂移都失败） |
| Ch21 | Software Testing – Specialized Testing | 专项测试：安全（SEC-01…20）、资源（RES-01…05）、可重放性 | 专项测试与常规测试**分开组织**，因为它们的执行环境不同（Linux vs 跨平台） |
| Ch22 | Software Configuration Management | 配置管理：SemVer 契约版本、SHA-256 摘要锁（四份 `contracts.lock`）、变更记录 CHG、分支命名以需求号开头、阶段归档不变更历史 | 摘要门禁是**机器强制的 SCM**：任何契约字节变化都会使四方锁失败 |
| Ch23 | Software Metrics and Analytics | 度量的态度与选择：[testing/strategy.md](testing/strategy.md) §8 —— **需求覆盖率为主指标，明确拒绝以行覆盖率汇报** | 5 类度量各自绑定用途（发现盲区），而不是用于汇报好看的数字 |

## 4. 第四部分：项目管理（Ch24–Ch27）

| 章 | 主题 | 本项目落点 | 怎么证明用上了 |
|---|---|---|---|
| Ch24 | Project Management Concepts | 职责、边界与所有权矩阵：[prd.md](requirements/prd.md) §3 角色表、[database/ownership.md](database/ownership.md) 读写矩阵、四份 workstream brief | 每项工作有唯一 owner；越界有明确判定（如 B4 不得代管 `b2`/`b3` 表） |
| Ch25 | Creating a Viable Software Plan | 资源账本与工作量分解：[architecture/resource-budget.md](architecture/resource-budget.md)（三张预算表 + 超预算处置顺序）、各模块工作包 | 资源预算**反向决定了架构**：明确写了"4 个 PostgreSQL 实例≈400 MB，直接否掉微服务方案" |
| Ch26 | Risk Management | 风险登记：每份 MAIN 文档的"遗留风险/已知缺口"小节 + [adr/README.md](adr/README.md) 的"已识别但尚未决策的议题" | 风险不是套话：ADR 中列出的是**具体未决项**（如 SDK `events` 字段拆分需 P3 变更），并注明何时决策 |
| Ch27 | A Strategy for Software Support | 支持与演进策略：[adr/ADR-0003](adr/ADR-0003-api-authority-and-runtime-registry.md)（运行期注册表使模块可观测）、[runbooks/](runbooks/) 四份手册 | 变更后如何支持有明确路径：契约变更规程 + 一次性切换 + 无兼容层 |

## 5. 第五部分：高级主题（Ch28–Ch30）

| 章 | 主题 | 本项目落点 | 怎么证明用上了 |
|---|---|---|---|
| Ch28 | Software Process Improvement | **过程改进的真实闭环**：[knowledge/](knowledge/) 知识笔记（收录标准 + 首条笔记）、Phase 00 的缺陷清单 R-01…R-05 | R-01 的处置不只是"修好了"，而是**改进了检查工具本身**（把实现面纳入扫描）；R-03 进一步修正了检查的**误报**。这就是过程改进 |
| Ch29 | Emerging Trends in Software Engineering | 技术选型的现代性但**受约束**：确定性评分明确拒绝 LLM（[ADR-0005](adr/ADR-0005-deterministic-replayable-scoring.md) 逐方案对比）、微内核插件化、事件驱动、内容寻址存储 | 每个"新"技术的采纳都附**被否方案与理由**；拒绝 LLM 的理由是可复现性要求，属工程判断而非保守 |
| Ch30 | Concluding Comments | 架构的双面记账：每个 ADR 的"后果"同时写**获得、付出、遗留风险**三栏 | 没有任何 ADR 只写好处；例如 ADR-0001 明确写出"单进程插件无隔离""单实例数据库是单点" |

---

## 6. 覆盖统计

| 部分 | 章节 | 有具体落点 | 明确标注"未落点" |
|---|---|---|---|
| 一 软件过程 | 5 | 5 | 0 |
| 二 建模 | 9 | 8 | 1（Ch13 移动设计） |
| 三 质量与安全 | 9 | 9 | 0 |
| 四 项目管理 | 4 | 4 | 0 |
| 五 高级主题 | 3 | 3 | 0 |
| **合计** | **30** | **29** | **1** |

**唯一未落点说明**：Ch13（Design for Mobility）——课程需求（XLSX）与导图均未要求移动端；本项目为响应式桌面 Web，已在 PRD 非目标中明确记录。**不为了"覆盖章节"而虚构需求**。

---

## 7. 反向检查：本项目哪些设计**不是**来自课堂

诚实记录，避免把工程判断伪装成"课堂知识的应用"：

| 设计 | 来源 | 说明 |
|---|---|---|
| 五元摘要与可重放性 | 工程判断 | 课程未要求可重放；这是为满足"评分可解释、可申诉"而自行引入的机制 |
| 契约摘要门禁（四方锁） | 工程判断 | 为解决"四组人并行改接口"的实际协作风险而设计 |
| nsjail 沙箱 | 部分来自课程（导图提 Firejail/Docker） | 具体选型是资源账本驱动的工程决策，见 [ADR-0004](adr/ADR-0004-sandbox-technology-selection.md) |
| 运行期模块注册表 | 工程判断 | 为让"即插即用"可被**观测**而不是只被声明；承接 `RQ-B-4-40` 的运行期一半 |
| 三态安全结论（发现/未发现/未检查） | 工程判断 | 源于"不允许把未检查伪装成安全"这一原则 |

> 区分"课堂知识"与"工程判断"本身也是一种诚实性要求：把两者混为一谈，会让评审无法判断真正的学习成果在哪里。
