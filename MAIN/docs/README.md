# MAIN 文档索引

本文件是 MAIN 文档导航。开发执行规则见仓库根 AGENTS.md；历史治理表、状态表与阶段模板用于参考，不增加日常开发或发布的前置步骤。

- 全局术语：[glossary.md](glossary.md)
- 课程知识应用矩阵：[knowledge-matrix.md](knowledge-matrix.md)
- 模块文档参考模板：[collaboration/module-doc-spec.md](collaboration/module-doc-spec.md)

---

## 1. 权威表（每类事实的唯一来源）

| 主题 | 权威文件 | 作用 | 其他文档应如何引用 |
|---|---|---|---|
| 需求编号、原文、逐条验收 | [requirements/rq-catalog.md](requirements/rq-catalog.md) | 32 条 `RQ-B-*` 的规范解释与可测验收 | 只引用编号，**不复制**原文 |
| 全局需求追踪 | [requirements/traceability.md](requirements/traceability.md) | 编号 → 责任模块 → 工作包 → 契约 → 验收 → 状态 | 引用编号 |
| 产品定位、用户、创新点、非目标 | [requirements/prd.md](requirements/prd.md) | 产品需求文档 | 引用小节 |
| 系统级功能与非功能需求、状态机、接口要求 | [requirements/srs.md](requirements/srs.md) | 软件需求规格 | 引用小节号 |
| 架构、模块边界、数据流、失败处理 | [architecture/overview.md](architecture/overview.md) | 上下文/容器/组件视图与技术选型理由 | 引用小节 |
| 状态机、时序、实体关系、证据模型、权限模型 | [architecture/models.md](architecture/models.md) | 行为模型的权威位置 | 引用状态名 |
| 信任边界、STRIDE 威胁、安全门禁 | [architecture/security.md](architecture/security.md) | 安全架构 | 引用 G-SEC 编号 |
| 内存/CPU/磁盘预算与实测方法 | [architecture/resource-budget.md](architecture/resource-budget.md) | 资源账本 | 引用预算值 |
| HTTP/事件契约的**设计约定与所有权** | [api/governance.md](api/governance.md) | 接口治理 | 字段看契约文件 |
| 统一标识、时间、作用域、绑定摘要 | [api/identifiers.md](api/identifiers.md) | 跨模块标识登记表 | 引用标识名 |
| 事件语义、去重、禁止内容 | [api/events.md](api/events.md) | 事件目录与语义 | 引用 subject |
| 契约变更流程与摘要算法 | [contracts/change-protocol.md](contracts/change-protocol.md) | 更改契约的**唯一合法路径** | 引用步骤号 |
| 契约变更记录 | [contracts/changes/](contracts/changes/README.md) | 单次变更的动机与影响分析 | 引用 CHG 编号 |
| 契约版本的累积历史 | [`../contracts/CHANGELOG.md`](../contracts/CHANGELOG.md) | 契约内容按版本的历史 | — |
| **机器可读契约** | [`../contracts/`](../contracts/) | OpenAPI / AsyncAPI / JSON Schema / 示例 | **字段定义的唯一权威** |
| 表结构规范、数据所有权、读写矩阵、生命周期 | [database/ownership.md](database/ownership.md) | 谁拥有哪些表、谁可写、保留策略 | 引用 schema 名 |
| 测试分层、门禁命令、禁止的伪装 | [testing/strategy.md](testing/strategy.md) | 测试与质量策略 | 引用层级 |
| 阶段归档门禁与初始化验收结论 | [testing/acceptance.md](testing/acceptance.md) | 验收门禁 | 引用门禁号 |
| 跨模块用例目录（集成/E2E/安全/资源/文档） | [testing/cases.md](testing/cases.md) | 79 条用例 | 引用用例 ID |
| 跨模块不可逆决策 | [adr/](adr/README.md) | 架构决策记录 | 引用 ADR 编号 |
| 启动、组装、本地容器集成、生产交付、Linux 验收、排障 | [runbooks/](runbooks/local-development.md) | 六份运行手册 | 引用手册与小节 |
| 阶段进度与完成证据 | [phases/](phases/README.md) | 阶段记录与归档 | 引用阶段号 |
| 同名根因的可复用结论 | [knowledge/](knowledge/README.md) | 知识笔记 | 引用日期与标题 |
| 各模块的任务书（需求、边界、泳道、DoD） | [workstreams/](workstreams/B1.md) | 四份工作包说明 | 引用工作包 ID |
| 并行协作规范 | [collaboration/](collaboration/README.md) | 流程、DoD、模块文档规范 | — |

**规则**：一个事实只能有一个权威位置。其他文档只做**摘要 + 链接**，不得复制字段表、DDL 或原文清单。

---

## 2. 完整文件清单

```text
docs/
├── README.md                        本索引
├── glossary.md                      全局术语表
├── knowledge-matrix.md               课程知识（SEPA 30 章）→ 本项目产物的映射
├── ../seed/question-bank/           ★ 100 道已验证题目（6 个主题分片 + 校验器自检题）
├── ../scripts/verify_question_bank.py   题库真实性门（真实执行参考答案）
├── ../scripts/import_question_bank.py   题库导入门（只走公开 HTTP API）
├── ../scripts/install-mihomo-autograder.sh  安装 AutoGrader 独立的本机 Mihomo 出站代理
├── requirements/
│   ├── prd.md                       产品需求（定位/用户/创新/非目标/成功判据）
│   ├── srs.md                       软件需求规格（横切功能/非功能/状态机/接口/数据/约束/验收）
│   ├── rq-catalog.md                ★ 32 条课程需求逐条规范解释与验收
│   └── traceability.md              ★ 全局需求追踪矩阵
├── architecture/
│   ├── overview.md                  上下文/容器/组件视图、数据流、失败矩阵、技术选型
│   ├── models.md                    状态机、时序、实体关系、证据模型、权限模型
│   ├── security.md                  信任边界与 STRIDE 威胁模型、16 条安全门禁
│   └── resource-budget.md           512MB/1vCPU/10GB 账本与实测方法
├── api/
│   ├── governance.md                HTTP 设计约定、端点所有权、兼容策略、客户端生成
│   ├── identifiers.md               标识/时间/作用域/绑定摘要登记表
│   └── events.md                    事件目录、逐事件语义、禁止内容、消费者/生产者清单
├── contracts/
│   ├── change-protocol.md           ★ 契约变更规程（P1–P4 分级、8 步流程）
│   └── changes/README.md            CHG 记录索引与模板
├── database/
│   └── ownership.md                 数据所有权、读写矩阵、跨模块引用、生命周期、表结构规范
├── testing/
│   ├── strategy.md                  分层、覆盖风险、基础设施硬规则、门禁命令
│   ├── acceptance.md                归档门禁、Phase 00 验收结论、后续阶段门禁、冷启动验收
│   └── cases.md                     跨模块用例目录（79 条）
├── innovation/
│   └── inventory.md                 ★ 创新点清单（审计产出，含红线、编号、成本与待裁决项）
├── adr/
│   ├── README.md                    ADR 索引、阅读建议、尚未决策议题
│   ├── ADR-0001-plugin-composition-and-schema-ownership.md
│   ├── ADR-0002-current-xlsx-requirements-baseline.md          （含导图与 XLSX 的冲突证据）
│   ├── ADR-0003-api-authority-and-runtime-registry.md
│   ├── ADR-0004-sandbox-technology-selection.md
│   ├── ADR-0005-deterministic-replayable-scoring.md
│   ├── ADR-0006-event-driven-decoupling.md
│   ├── ADR-0007-b1-static-client-provided-by-host.md
│   └── ADR-0008…ADR-0014                模块设置视图、AI 两轨、沙箱分层、跨模块组合、职责提案、模块数开放、AI 评分参考
├── runbooks/
│   ├── local-development.md         Windows 开发与最小验证路径
│   ├── container-deployment.md      本地容器集成：PostgreSQL、NATS 与 composition host
│   ├── production-deployment.md     ★ Vercel、Cloudflare Tunnel 与 Linux systemd 生产交付
│   ├── integration.md               后端模块组装、失败诊断矩阵、即插即用验证规程
│   ├── linux-acceptance.md          Linux 安全与资源验收（仅此环境有效）
│   └── troubleshooting.md           五层分诊与逐项处置
├── phases/
│   ├── README.md                    阶段模板与归档门禁
│   ├── active/phase-00-initialization.md      契约与文档治理（active）
│   ├── active/phase-01-integration-runtime.md ★ 运行时集成与端到端验证（active）
│   ├── active/phase-02-ai-activation-and-integration-repair.md ★ AI 能力激活与集成缺陷修复（active）
│   └── archive/README.md            归档索引（暂无归档）
├── knowledge/
│   ├── README.md                    收录标准、命名、模板、索引
│   └── 2026-09-15-orphaned-bytecode-after-module-removal.md
├── workstreams/
│   ├── B1.md  B2.md  B3.md  B4.md   四份模块任务书
└── collaboration/
    ├── README.md                    协作规范导航与并行形态
    ├── module-doc-spec.md           ★ 模块文档规范（对四个模块强制）
    ├── contribution-workflow.md     需求生命周期、分支约定、共享文件冲突预防
    └── definition-of-done.md        项目级"完成"定义与提交前清单
```

★ = 新成员最应先读的文件。

---

## 3. 状态表

**状态词只有四种**：`骨架` / `待实现` / `已完成` / `阻塞`。其他措辞（"基本完成""应该可以"）视为缺陷。

### 3.1 文档本身的成熟度

| 文档 | 状态 | 最后验证方式 |
|---|---|---|
| `requirements/rq-catalog.md` | 已完成 | 与 XLSX 逐行比对；32 条编号由 `checks.py` 断言 |
| `requirements/traceability.md` | 已完成 | `checks.py` 断言 32 条编号齐备（含 `RQ-B-4-99`） |
| `requirements/prd.md`、`srs.md` | 已完成 | 人工审查；非目标与 DDL 无重复定义 |
| `architecture/*` | 已完成 | 状态名/事件名与契约枚举交叉核对 |
| `api/*` | 已完成 | 示例通过实例校验；端点所有权与 `module.yaml` 一致 |
| `contracts/change-protocol.md` | 已完成 | 流程步骤与实际执行（重算摘要 + 更新四份锁）一致 |
| `database/ownership.md` | 已完成 | 与 `module.yaml` 的 schema 归属一致 |
| `testing/*` | 已完成 | 文档门禁自动化；用例状态如实标注为"无" |
| `adr/*` | 已完成 | 每条 ADR 均含备选方案与被否原因 |
| `runbooks/*` | 已完成（含未验证声明） | 本地容器路径有真实读数；生产部署手册记录外部 DNS、Vercel 与 Tunnel 验收项 |
| `phases/active/phase-00-initialization.md` | **active** | 完成条件 11（资源实测）未达成；6/9/10 已在 Phase 01 达成 |
| `phases/active/phase-01-integration-runtime.md` | **active** | 见该文件第 6 节"未执行"（Linux 沙箱与资源门禁） |
| `knowledge/*` | 已完成 | 预防措施已落地为 `checks.py` 中的检查 |
| `workstreams/*` | 已完成 | 与 `rq-catalog.md` 编号一致 |
| `collaboration/*` | 已完成 | 文档门禁强制各模块满足 `module-doc-spec.md` |

### 3.2 系统能力的当前状态

| 能力 | 状态 | 说明 |
|---|---|---|
| 契约权威（OpenAPI/AsyncAPI/JSON Schema/示例） | **已完成** | 示例通过实例校验 |
| 契约摘要门禁（四份锁） | **已完成** | 已实际拦截过漂移 |
| 需求基线门禁（32 条） | **已完成** | 已发现并修复 `RQ-B-4-99` 遗漏 |
| 模块文档完整性门禁 | **已完成** | 已强制四个模块补齐文档集合 |
| 文档编码与链接门禁 | **已完成** | 已实际拦截并修复死链 |
| 迁移 head 与本地端口一致性门禁 | **已完成** | 已发现并修复 head 与端口不一致 |
| Platform SDK 协议 | 骨架 | 代码存在；`events` 字段不分发布/订阅（ADR-0003 遗留） |
| composition host 装载 | 骨架 | 逻辑存在；**未在真实环境验证装载** |
| 本地容器集成路径（`docker compose up`） | 已完成 | 空卷首次启动实测：三插件 readiness、令牌签发与真实 API 数据读写；见 `runbooks/container-deployment.md` 与 `phases/active/phase-01-integration-runtime.md` §13 |
| 生产部署（Vercel + Cloudflare Tunnel + systemd） | 待实现 | DNS 与 Vercel 前端域名由用户确认完成；服务器、Tunnel、API readiness 与真实业务流程尚未验收 |
| B2 评测引擎 | 待实现 | 插件骨架 |
| B3 题库 | 待实现 | 插件骨架 |
| B4 平台底座 | 待实现 | 插件骨架 |
| 数据库角色与 schema 供给 | 待实现 | `infra/postgres/init.sql` 只建 schema |
| nsjail 沙箱执行 | 待实现 | **仅 Linux** |
| 安全门禁（16 项） | 待实现 | **未执行** |
| 资源实测 | 待实现 | **无数据** |
| 集成与端到端用例 | 待实现 | 79 条用例中 7 条（文档类）已自动化 |

---

## 4. 阅读路径建议

| 你的目的 | 建议顺序 |
|---|---|
| 5 分钟了解项目 | `../README.md` → `requirements/prd.md` §1–§3 → `adr/README.md` |
| 了解"为什么这样设计" | `adr/README.md` → 逐条 ADR（尤其 0002 的冲突证据与 0004/0005 的取舍） |
| 开始做某个模块 | `collaboration/module-doc-spec.md` → `workstreams/B?.md` → 本模块 `README.md` + `AGENTS.md` + `onboarding.md` |
| 要改接口 | `contracts/change-protocol.md` → `api/governance.md` → `api/identifiers.md` |
| 要动数据库 | `database/ownership.md` → `adr/ADR-0001` → 本模块 `docs/database/migrations.md` |
| 要评审安全 | `architecture/security.md` → `testing/cases.md` §3 → `runbooks/linux-acceptance.md` |
| 要跑起来 | `runbooks/production-deployment.md`（生产）或 `runbooks/local-development.md`（开发）→ `runbooks/integration.md` → `runbooks/troubleshooting.md` |
| 要做课设汇报 | `requirements/prd.md` §6（创新点）→ `knowledge-matrix.md`（课程知识映射）→ `testing/acceptance.md`（证据） |

---

## 5. 维护规则

1. 新增/移动/归档/删除文档 → **同步更新本索引**。
2. 新增事实类别 → 在本索引 §1 权威表中登记，并指定唯一权威位置。
3. 文档状态变化 → 更新 §3.1。
4. 契约或能力状态变化 → 更新 §3.2。
5. `checks.py` 会断言各模块文档集合完整、Markdown 为有效 UTF-8 且无死链；MAIN 侧同样受该检查覆盖。
