# AutoGrader 软件需求规格说明（SRS）

本文件规定**系统级、跨模块**的需求：横切功能需求、非功能需求、状态机、接口要求、数据要求、约束与验收。

逐条课程需求（32 条 `RQ-B-*`）的规范解释与验收标准见 [rq-catalog.md](rq-catalog.md)；追踪矩阵见 [traceability.md](traceability.md)。本文件不重复那些内容。

---

## 1. 系统边界

### 1.1 系统做什么

一个课程作业评测平台，包含：Web 界面（B1）、评测引擎（B2）、题库（B3）、平台底座（B4），由 MAIN composition host 组装为一个可运行的 Web 应用。

### 1.2 系统不做什么

见 [prd.md](prd.md) 第 5.2 节"明确非目标"。摘要：不支持 C/C++/Java、不做多租户、不做高可用、不用 LLM 参与评分、不保留兼容层。

### 1.3 外部系统与依赖

| 外部系统 | 交互方式 | 我方边界 | 不可用时的行为 |
|---|---|---|---|
| 课程方的名单文件 | 教师上传 PDF / XLSX | 我方解析并预览，**不自动猜测** | 解析失败 → 明确报错，不写入任何数据 |
| 邮件服务器 | IMAP 拉取附件 | 我方归一化为内部提交信封 | 隔离待处理，不阻塞 API 提交路径 |
| 学生浏览器 | 同源 HTTP / SSE | 静态资源 + API | — |
| Linux 内核特性 | cgroup v2 / seccomp / namespaces | nsjail 沙箱 | 不可用时**不允许**执行动态评测（不得退化为无沙箱执行） |

---

## 2. 功能需求（横切）

### FR-01 认证与授权

- 采用短期 **Ed25519 JWT** + JWKS 公钥分发；签发方为 B4。
- 各模块**本地验签**，不回调 B4（避免认证成为单点串行依赖）。
- 刷新凭据仅存放于 `HttpOnly` + `Secure` + `SameSite` cookie；**不得**出现在响应体、URL、日志或前端存储。
- 授权三层：认证（401）→ 角色（403）→ 课程作用域（403）；资源不存在用 404。三者不得混用。
- 前端隐藏不构成授权（`RQ-B-1-20`）。

### FR-02 评测全链路

- 提交接收（API / 邮件）→ 信封校验 → 内容寻址存储 → 静态合法性 → 安全性确认 → （沙箱）动态执行 → 逐项证据 → 报告 → 持久化 → 查询/导出。
- 每个阶段都有明确的状态与失败语义（见第 4 节状态机）。
- 静态高危结果**阻断**动态执行，状态为 `rejected`。
- 评测过程与结果都必须可持久化查询，不允许"生成过但查不到"。

### FR-03 可解释性与可重放

- 报告 = 总体评价（分数、满分、通过与否、结论摘要）+ 细节评价（逐规则与逐用例）。
- **合计校验**：细节得分的加总必须等于总分；不等即为缺陷。
- **绑定摘要**：提交摘要、题目版本绑定、测试包摘要、runner 策略摘要、执行环境摘要（见 `api/identifiers.md` 第 4 节）。
- **可重放判据**：五项摘要相同 ⇒ 分数与逐项证据相同。这是自动化测试断言。

### FR-04 题库与版本不可变

- 题目由题干（富文本 + 图片）、参考答案、测试用例组成。
- `question_version` 一旦发布即**不可变**；修改必须产生新版本。
- 已发布版本被历史评测引用时不可删除（只能归档）。
- 公开 DTO 永不包含参考答案、隐藏用例、expected output。

### FR-05 三类测试框架

题目测试用例分三类：`stdin/argv`、`file I/O`、`callable(import/include)`。

- 三者共用**同一 package 结构**与同一校验入口；新增题型**不修改** B2 runner 核心。
- 比较策略（精确 / 忽略行尾空白 / 数值容差 / 退出码 / 文件产出）由题目声明，**不得由学生代码影响**。

### FR-06 事件与解耦

- 模块间异步通信统一使用 NATS JetStream，命名空间 `autograder.v1.<domain>.<event>`。
- 投递语义 **at-least-once**；消费者必须按 `event_id` 去重（inbox 唯一键）。
- 发布使用 **outbox** 模式（与数据库事务一致），避免"业务成功但事件丢失"。
- 超过最大投递次数进入 **DLQ**，可查询、可人工重放。
- 事件 payload **不得**携带：源码、参考答案、隐藏测试、凭据、超范围 PII。

### FR-07 名单导入

- 流程固定为：解析 → 列映射 → 逐行校验 → 预览 → 人工确认 → 原子写入。
- 解析不确定时**报错并要求人工映射**，不自动猜测。
- 校验失败的行**必须在结果报告中列出**，不得静默丢弃。
- 确认前**零写入**；确认操作**幂等**（重复确认不重复写入）。

### FR-08 可观测性

- 结构化 JSON 日志（`timestamp`、`level`、`event`、`producer`、`request_id`，有上下文时含 `correlation_id` 与业务 ID）。
- 请求传播 W3C `traceparent` 与 `X-Request-ID`。
- 每个模块提供 live 与 readiness；readiness 反映**真实**依赖状态。
- 审计记录覆盖：认证、权限拒绝、管理操作、名单导入、题目发布、评测触发。

### FR-09 模块装载与即插即用

- B2/B3/B4 通过 entry point 组 `autograder.modules` 注册；MAIN host 发现并装载。
- 按 **capability DAG** 决定装载顺序；`requires` 未满足时启动失败并指出缺失能力。
- 启动时拒绝：重复路由、重复 schema 归属、契约摘要漂移、循环依赖。
- 运行期可通过 B4 注册表查询已装载模块的能力/路由/事件清单（`RQ-B-4-40`）。

---

## 3. 非功能需求

### NFR-01 安全

| 编号 | 要求 | 验证方式 |
|---|---|---|
| SEC-01 | 沙箱执行：nsjail + cgroup v2 + seccomp；禁网、非 root、只读根、临时工作区 | 在沙箱内尝试联网/写根路径/提权，必须失败并被记录 |
| SEC-02 | 资源限制：进程数、CPU、内存、时间、输出大小、文件数上限 | 超限样本必须被限制且归类，不得拖垮宿主 |
| SEC-03 | 超时后清理**整个进程树**，无残留进程 | 超时样本执行后检查进程表 |
| SEC-04 | 命令执行使用固定参数数组，禁止拼接 shell 字符串 | 注入样本必须失败 |
| SEC-05 | 数据库最小权限：每模块独立 role，仅本 schema；禁止超级用户运行应用 | 跨 schema 访问必须被数据库拒绝 |
| SEC-06 | 密码使用强哈希（Argon2id 或 bcrypt）；绝不明文/可逆 | 代码审查 + 存储格式测试 |
| SEC-07 | 导出防公式注入（`=`、`+`、`-`、`@`、Tab、CR 开头转义） | 恶意名单导出用例 |
| SEC-08 | 日志与事件脱敏：token、源码、答案、PII、内部路径最小化 | 脱敏规则单测 + 样本审查 |
| SEC-09 | 公开契约与示例中不存在答案/隐藏测试/凭据/PII | 自动化扫描 + 人工审查 |
| SEC-10 | 富文本渲染不执行脚本（XSS 防护） | XSS 用例必须被拦截 |

### NFR-02 可重现性

- 题目版本、测试包、提交、runner 策略全部使用 SHA-256 摘要。
- 同一输入 + 同一环境 ⇒ 同一分数、同一逐项证据、同一摘要。
- 该性质是**测试断言**，不是尽力而为。

### NFR-03 可靠性与失败语义

- 依赖不可用（DB、NATS、沙箱）时：readiness 失败并**指明具体依赖**；不得返回健康假象。
- 系统故障不得伪装为学生错误：区分 `rejected` / `failed` / 低分。
- 部分失败（个别用例超时）不影响其余用例的判定，也不得伪造通过。
- 迁移失败必须中止启动，不允许带着半迁移状态运行。

### NFR-04 性能与容量

| 指标 | 目标 | 说明 |
|---|---|---|
| 提交接收响应 | P95 < 500 ms（不含评测） | 接收即返回 `202` |
| SSE 进度推送延迟 | < 2 s | 阶段变化即推送 |
| 题目列表查询 | P95 < 800 ms（千题量级） | 分页 |
| 统计查询 | P95 < 1.5 s（10 万事件量级） | 预聚合 |
| 动态评测并发 | **1**（初始） | 受资源账本限制 |

### NFR-05 资源账本（硬约束）

总量上限：**1 vCPU / 512 MB RSS / 10 GB 持久空间**。分配见 [resource-budget.md](../architecture/resource-budget.md)。

| 组成部分 | 内存 | CPU | 磁盘 |
|---|---|---|---|
| composition host | ≤160 MB | 0.35 | 镜像/工具 1.5 GB |
| PostgreSQL | ≤96 MB | 0.15 | 数据 ≤1.5 GB |
| NATS JetStream | ≤32 MB | 0.05 | ≤0.25 GB |
| 单并发 nsjail 作业 | ≤96 MB | 0.45 | — |
| **保留余量** | **≥128 MB** | — | **≥1 GB** |

- 覆盖应用、数据库、NATS 与一个活动沙箱；**不含**宿主 OS 内核。
- 最终安全与资源验收在 **Linux cgroup v2** 环境完成。

### NFR-06 可维护性与可测试性

- 模块间只能通过 **Platform SDK + 版本化契约**交互；禁止跨模块 import、禁止直接读对方表。
- 契约有机器可读权威（OpenAPI 3.1 / AsyncAPI 3.0 / JSON Schema 2020-12），文档不复制字段。
- 每个模块可独立运行其检查（lint / 类型 / 测试 / 迁移）而无需其他模块的实现。
- 测试使用与生产语义一致的 PostgreSQL，**不得**用 SQLite 替代数据库验收。

### NFR-07 可用性（UX）

- 学生端：提交后必须看到明确的下一步与进度，不能出现无反馈等待。
- 教师端：破坏性操作（名单确认、发布题目版本）必须二次确认并说明后果。
- 错误信息面向用户可读，不暴露内部路径、堆栈、SQL。
- 关键流程（导入、提交、导出）在中文环境下不乱码。

### NFR-08 合规与学术诚信

- 学生只能看到自己的提交与成绩。
- 教师只能看到授权课程作用域内的数据。
- 参考答案与隐藏测试对**所有**学生角色不可见（含 API 层）。
- 审计记录不可被普通用户修改。

---

## 4. 状态机（系统级）

### 4.1 提交（submission）状态机

```text
received ──validate──▶ validated ──enqueue──▶ queued ──start──▶ running ──┬─▶ completed
    │                      │                                                ├─▶ rejected
    └──invalid──▶ rejected └──invalid──▶ rejected                           └─▶ failed
```

| 状态 | 含义 | 终态 |
|---|---|---|
| `received` | 已收到，尚未校验 | 否 |
| `validated` | 信封、摘要、权限、大小校验通过 | 否 |
| `queued` | 已入队等待执行资源 | 否 |
| `running` | 正在评测 | 否 |
| `completed` | 评测完成，报告可用 | **是** |
| `rejected` | **被拒**：信封非法、静态高危、安全判定不通过 | **是** |
| `failed` | **技术失败**：沙箱/依赖/内部错误 | **是** |

**语义约束**：`rejected` 与 `failed` 不得互相替代；不得用 `completed` 表示失败；不得为终态实体再发新事件。

### 4.2 评测运行（evaluation run）进度阶段

```text
started ─▶ (progress)* ─▶ completed | failed
```

阶段序列：`fetching_package → static_analysis → security_check → dynamic_execution → scoring → reporting`。

- 每个阶段完成发一次 `autograder.v1.evaluation.progress`。
- 阶段可跳过（如静态高危时跳过 dynamic_execution 并直接进入 rejected 终态）。
- 进度事件必须带**单调递增**的序号，供前端去重与排序。

### 4.3 名单导入状态机

```text
uploaded ─▶ parsed ─▶ mapping_required ─▶ validating ─▶ previewed ─▶ confirmed(committed)
                 │                                              │
                 └─unparsable─▶ failed(报错)          rejected ◀─┘ (人工放弃)
```

- `mapping_required`：列名无法唯一映射，**必须人工指派**，不得自动猜测。
- `previewed` 阶段数据库**零写入**。
- `confirmed` 是**原子**且**幂等**操作。
- 校验失败行必须出现在结果中（不得静默丢弃）。

### 4.4 题目版本状态机

```text
draft ──publish(校验通过)──▶ published ──▶ archived
   │                              │
   └──publish(校验失败)──▶ draft   └──被评测引用──▶ (不可删除，仅可归档)
```

- `published` 后内容**不可变**；尝试修改返回 `409`。
- 引用草稿版本布置作业 → `409`。

---

## 5. 接口要求

### 5.1 HTTP

- 规范：**OpenAPI 3.1**；权威文件在 `MAIN/contracts/openapi/`。
- 版本化前缀：`/api/v1/...`。
- 错误响应：**RFC 9457 Problem Details**（`type`、`title`、`status`、`detail`、`instance`）。
- 时间：UTC RFC 3339；标识：UUID v4（见 `api/identifiers.md`）。
- 分页：游标式（cursor），不用 offset 翻页大表。
- 关联：请求与响应携带 `traceparent` 与 `X-Request-ID`。
- 幂等：所有创建类写操作接受幂等键。

### 5.2 事件

- 规范：**AsyncAPI 3.0**；权威文件 `MAIN/contracts/asyncapi/events.yaml`。
- 主题：`autograder.v1.<domain>.<event>`。
- 信封：`event_id`、`event_type`、`schema_version`、`occurred_at`、`correlation_id`、`causation_id`、`producer`、`payload`。
- 语义：at-least-once + outbox/inbox + DLQ。

### 5.3 模块清单

- 每个模块提供 `module.yaml`，遵循 `MAIN/contracts/manifest.schema.json`。
- 每个模块提供 `contracts.lock`（版本 + 来源 + 算法 + 摘要 + 状态）。
- 摘要算法与门禁见 `contracts/change-protocol.md` 第 2 节第 4 步。

### 5.4 平台 SDK

- 模块通过 `autograder-platform`（`==0.1.0`）实现 `PlatformModule` 协议：
  - `descriptor: ModuleDescriptor`（module、contract_version、provides、requires、routes、events）
  - `router(context) -> APIRouter`
  - `health(context) -> HealthReport`
- entry point 组：`autograder.modules`。

> **已知缺口**：`ModuleDescriptor.events` 目前是扁平元组，不区分 publishes/subscribes，而 `module.yaml` 区分二者，因此 host 尚不能校验事件方向。详见 ADR-0003。

---

## 6. 数据要求

### 6.1 所有权

| schema | owner | 主要实体 |
|---|---|---|
| `b4` | B4 | user、credential、session、role、course、section、enrollment、audit |
| `b3` | B3 | question、question_version、assignment、test suite/case、fixture、统计聚合 |
| `b2` | B2 | submission、artifact、evaluation run/result、security finding、email checkpoint |

- 物理表结构以**各模块自己的迁移**为唯一事实源。
- MAIN 只维护所有权目录与 migration head，**不复制 DDL**。
- 禁止跨 schema 外键、跨 schema SQL、跨 schema ORM 关系。
- 禁止应用使用超级用户连接。

### 6.2 数据保留与生命周期

| 数据 | 保留策略 | 清理责任 |
|---|---|---|
| 提交内容 | 按课程配置保留期；被评测记录引用期间不可删 | B2 |
| 评测证据与报告 | 长期保留（成绩依据） | B2 |
| 题目版本 | 不可删除（仅归档）；被引用则永久保留 | B3 |
| 统计聚合 | 长期保留（已去标识化） | B3 |
| 审计记录 | 长期保留，不可由普通用户修改 | B4 |
| 事件（JetStream） | 按配额与保留策略滚动 | B4 |
| 邮件隔离件 | 人工处理或按策略过期 | B2 |

### 6.3 敏感数据

- 学生 PII：姓名、学号、邮箱。**最小化采集**，日志与事件中脱敏。
- 受保护内容：参考答案、隐藏测试用例、expected output。
- 凭据：密码哈希、JWT 私钥、刷新凭据。**绝不**进入日志、事件、导出、仓库。
- 源码：属于学生的作品，除本人与授权教师外不可见；**不进入事件与统计**。

---

## 7. 约束

| 类别 | 约束 |
|---|---|
| 语言与运行时 | Python 3.12（后端）、Node 24（前端构建）、Bash 5（被评测对象） |
| 技术基线 | 见 `rq-catalog.md` 的 `RQ-B-0-20` 冻结表 |
| 数据库 | PostgreSQL 17，单实例多 schema |
| 消息 | NATS JetStream 2.10 |
| 部署目标 | Linux（验收必须）；Windows 仅开发 |
| 沙箱 | nsjail 需要非特权用户命名空间与 cgroup v2 |
| 资源 | 1 vCPU / 512 MB / 10 GB（见 NFR-05） |
| 协作 | 四个模块独立 Git 项目；无远端、无 CI、无 PR |

---

## 8. 验收

### 8.1 分层验收

| 层级 | 内容 | 责任 | 何时执行 |
|---|---|---|---|
| 需求门禁 | 32 条 RQ 各有唯一 owner、验收与工作包 | MAIN | 持续（自动化） |
| 契约门禁 | 契约可解析、示例有效、无路由/subject 冲突、四锁摘要一致 | MAIN | 每次契约变更（自动化） |
| 模块门禁 | 各模块 lint / 类型 / 测试 / 迁移 | 各模块 | 每次改动 |
| 集成门禁 | host 装载插件、readiness 反映依赖；生产 B1 由 Vercel 提供 | MAIN | 每次集成 |
| 端到端 | 登录 → 导入名单 → 发布题目 → 提交 → 评测 → 查分 → 导出 | MAIN + 全体 | 功能阶段完成后 |
| 安全门禁 | 沙箱禁网/资源限制/进程树清理、跨 schema 拒绝、脱敏 | B2/B4 | 功能阶段完成后（Linux） |
| 资源门禁 | 统一启动后实测 CPU / RSS / 磁盘 | MAIN | 功能阶段完成后（Linux） |

### 8.2 阶段完成判据

一个阶段只有在下列全部成立时才能归档（见 `phases/README.md` 的归档门禁）：

1. 阶段完成条件全部满足且**有真实证据**。
2. 静态检查、必要的测试与构建**真实通过**。
3. API、数据库、架构、测试、README、运行手册**已同步**。
4. 没有隐藏、吞并或降级处理的失败。
5. 遗留风险已记录且不构成该阶段完成条件的一部分。

### 8.3 明确不允许的验收伪装

- 用 mock 结果证明真实集成已通过。
- 用 SQLite 结果代替 PostgreSQL 验收。
- 用静态检查/测试收集/计划描述成"运行时功能已验证"。
- 在 Windows 上宣称 Linux 专属能力（沙箱、资源账本）已验收。
- 把 `待实现` 写成"已基本完成"。
