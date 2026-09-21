# 术语表（全局）

本文件是跨模块术语的**唯一权威**。模块可以使用并扩展自己的术语（在其 `docs/collaboration/glossary.md`），但不得与本文冲突。

---

## 1. 产品与领域

| 术语 | 定义 | 注意 |
|---|---|---|
| **AutoGrader Beta** | 本系统的名称。课程 B 题"AutoGrader 作业代码自动评分系统"，项目编号 Beta | 导图中 Alpha 是另一个项目（ATC 语音标注），与本项目无关 |
| **提交（submission）** | 一名学生针对某个题目版本的一次代码提交 | 有独立的 `submission_id`；与"一次评测"不是一回事 |
| **评测运行（evaluation run）** | 对一次提交执行评测的过程 | 有独立的 `evaluation_run_id`；一次提交理论上可有多次评测运行（重跑） |
| **评测证据（evidence）** | 支持某个判分结论的可核验材料（规则、用例、输出摘要、失败类别） | **B2 独有概念**；B3 不拥有证据 |
| **总体评价 / 细节评价** | 课程需求的措辞：总体评价 = 分数、通过与否、结论摘要；细节评价 = 逐规则与逐用例的结论 | 对应 `evaluation-report.schema.json` 的 `overall` 与 `static_findings`/`case_evidence` |
| **题目（question）** | 逻辑实体，跨版本稳定 | `question_id`；其内容在版本中 |
| **题目版本（question version）** | 题目某一次发布的**不可变**内容快照 | `question_version_id`；发布后不可修改，改必须出新版本 |
| **作业集合（assignment）** | 布置给某个课序的一组已发布题目版本 | `assignment_id`；只能引用已发布版本 |
| **课序（section）** | 课程下的一个开班 | `section_id`；名单、作业、成绩都按课序组织 |
| **选课关系（enrollment）** | 学生与课序的关联 | `enrollment_id`；名单导入产生 |
| **题目回答次数分布** | 课程需求 `RQ-B-3-30` 的措辞 | 实现为作答次数、通过率、得分分布、失败类别分布（见 `api/events.md` §3.4 的口径说明） |

## 2. 运行时与架构

| 术语 | 定义 | 注意 |
|---|---|---|
| **MAIN** | 设计期契约权威 + 集成 host + Platform SDK + 文档基座 | **不拥有任何业务数据** |
| **composition host** | MAIN 中那个唯一的 FastAPI 应用 | 一个进程装载 B2/B3/B4 插件 |
| **插件 / 模块（module）** | 一个独立打包、通过 entry point 注册的 Python 发行包（B2/B3/B4）；B1 是静态客户端 | 模块之间**禁止 import 彼此** |
| **Platform SDK** | `autograder-platform` 包，定义 `ModuleDescriptor` / `PlatformModule` / `ModuleContext` / `HealthReport` | 模块依赖固定版本（`==0.1.0`） |
| **capability（能力）** | 模块声明提供或需要的抽象能力名，如 `b4-auth`、`b3-questions` | host 用 capability DAG 决定装载顺序 |
| **capability DAG** | 由 `provides`/`requires` 构成的依赖图 | 存在循环或缺失能力时**启动失败** |
| **插件发现** | 通过 Python entry point 组 `autograder.modules` 找到模块工厂 | 开发期可用 `AUTOGRADER_MODULES` 临时指定 |
| **契约（contract）** | `MAIN/contracts/` 下的 OpenAPI / AsyncAPI / JSON Schema | **机器可读**，是接口的唯一权威 |
| **契约摘要（contract digest）** | 对 `contracts/` 全部文件（相对路径 + 字节）计算的 SHA-256 | 四份 `contracts.lock` 必须与实时摘要一致 |
| **manifest（模块清单）** | 模块根目录的 `module.yaml`，声明模块名、契约版本、entry point、能力、路由、事件、schema 归属、迁移 head | 受 `manifest.schema.json` 约束 |
| **schema 归属** | 某个 PostgreSQL schema 由唯一模块拥有 | 不允许两个模块声明同一 schema |
| **迁移 head** | 该模块 Alembic 迁移链的当前末端 | 必须与 `module.yaml: database.migration_head` 一致 |

## 3. 契约与接口

| 术语 | 定义 | 注意 |
|---|---|---|
| **RFC 9457 Problem Details** | 统一错误响应形状（`type`/`title`/`status`/`detail`/`instance`） | 禁止用 `404` 掩盖 `403` |
| **幂等键（idempotency key）** | 客户端生成的字符串，用于让重复请求返回同一结果 | 作用域为 `(key, 操作类型, 作用域)` |
| **游标分页（cursor pagination）** | 用 `cursor` 而非 offset 翻页 | 大表禁用 offset |
| **traceparent** | W3C Trace Context 头 | 贯穿请求 → 日志 → 事件 |
| **X-Request-ID** | 请求关联标识 | 缺省时由入口生成 |
| **correlation_id** | 标识**同一次业务操作**，跨进程保持一致 | 与 `causation_id` 不可混用 |
| **causation_id** | 标识**直接因果**的上一事件 | 允许为 null |
| **事件信封（event envelope）** | 事件的统一外层结构 | 见 `event-envelope.schema.json` |
| **at-least-once** | 投递语义：消息至少被投递一次（可能重复） | 因此消费者**必须**幂等 |
| **outbox** | 与业务变更同事务写入的事件暂存表，由投递器异步发出 | 防止"业务成功但事件丢失" |
| **inbox** | 消费者侧以 `event_id` 为唯一键的去重表 | 防止重复投递产生重复业务效果 |
| **DLQ（死信队列）** | 超过最大投递次数的消息的落点 | 可查询、可人工重放；**不得静默丢弃** |
| **SSE（Server-Sent Events）** | 服务端向浏览器单向推送 | **会话级**，不替代系统级事件 |
| **服务间接口（service-to-service）** | 只应被其他模块调用的端点 | 例如 B3 的 evaluation package 端点；**仍需鉴权** |

## 4. 评测与安全

| 术语 | 定义 | 注意 |
|---|---|---|
| **evaluation package** | B3 从已发布题目版本编译出的**不可变**评测包，含 adapter、测试用例、比较策略、fixture 引用与摘要 | B2↔B3 的**最高风险耦合点**；受 `evaluation-package.schema.json` 约束 |
| **adapter（评测适配器）** | 三类题型的统称：`stdin`（命令行输入）、`file`（文件读写）、`callable`（import 后调用） | 三者共用同一 package 结构 |
| **比较策略（comparison policy）** | 题目声明的输出比较方式（精确 / 忽略行尾空白 / 数值容差 / 忽略） | **不得由学生代码影响** |
| **fixture** | 评测时放入沙箱工作区的输入或期望产出文件 | 以内容摘要绑定进 package |
| **runner policy** | 沙箱执行策略（参数、解释器、限额、比较策略）的摘要化描述 | 是五元摘要之一 |
| **五元摘要（binding digests）** | 提交摘要、题目版本绑定、测试包摘要、runner 策略摘要、执行环境摘要 | 五项相同 ⇒ 分数与逐项证据必须相同 |
| **可重放（replayable）** | 同摘要条件下重复评测得到同一结果的性质 | 是**测试断言**，不是愿望 |
| **合计校验（total check）** | 细节得分之和必须等于总分 | `total_check.matches_total` 强制为 true |
| **失败类别（failure category）** | 用例失败的固定分类：`timeout`/`crash`/`output_mismatch`/`resource_limit`/`missing_output`/`import_error`/`security_blocked`/`infrastructure_error` | `security_blocked` 与 `infrastructure_error` **不得**计入"学生答错" |
| **`rejected`** | 提交**被拒**：信封非法、静态高危、安全违规 | 是**终态**；**不是** `failed`，**不是** 0 分 |
| **`failed`** | **技术失败**：沙箱/依赖/内部错误 | 是**终态**；**不是**学生的问题 |
| **安全结论三态** | `found`（发现风险）/ `not_found`（未发现）/ `not_checked`（未检查） | **禁止**把 `not_checked` 表述为安全 |
| **受保护内容（protected content）** | 参考答案、隐藏测试用例、`expected_output` | **永不**出现在公开 DTO、事件、日志或学生可访问存储 |
| **静态高危（high-risk static finding）** | 静态检查发现的高危模式（如 `rm -rf /`、反弹 shell、`curl \| bash`） | 结果：**阻断动态执行**，终态 `rejected` |
| **nsjail** | 本项目采用沙箱执行器，配合 cgroup v2 与 seccomp-bpf | 见 ADR-0004；**仅 Linux** |
| **禁网（network isolation）** | 沙箱内无网络接口 | 是**不可协商**的常量，package 不能选择关闭 |
| **只读根（read-only root）** | 沙箱根文件系统只读 | 同上，不可协商 |
| **进程树清理** | 超时后清理**整个**进程树，而非仅直接子进程 | 有专门回归用例（SEC-08） |
| **内容寻址存储** | 以内容 SHA-256 为键的存储方式 | 同一内容只存一份；`RQ-B-2-30` |

## 5. 数据与治理

| 术语 | 定义 | 注意 |
|---|---|---|
| **schema（PostgreSQL）** | 数据库内的命名空间，同时是权限边界 | `b2`/`b3`/`b4`，各有唯一 owner |
| **最小权限角色** | 每模块一个 login role，仅拥有本 schema 权限 | **禁止**应用使用超级用户 |
| **跨 schema 引用** | 用 UUID 值引用他模块数据 | **禁止**跨 schema FK / SQL / JOIN / ORM 关系 |
| **悬空引用** | 指向不存在实体的 UUID | 因无 FK 而可能发生；读取时必须返回明确业务错误 |
| **去标识化（de-identified）** | 不含可识别个人身份或源码内容的数据 | B3 统计的唯一输入；由事件 payload 结构强制 |
| **PII** | 个人可识别信息（姓名、学号、邮箱等） | 最小化采集；日志与事件中脱敏 |
| **幂等（idempotent）** | 重复执行产生同一结果 | 名单确认、提交创建、事件消费都必须幂等 |
| **原子（atomic）** | 全成或全不成 | 名单确认必须原子 |
| **公式注入（formula injection）** | 导出的 XLSX/CSV 单元格被解析为公式 | 对 `=`/`+`/`-`/`@`/Tab/CR 前缀转义 |

## 6. 流程与文档

| 术语 | 定义 | 注意 |
|---|---|---|
| **ADR** | 架构决策记录 | 必须写被评估的备选方案与被否原因 |
| **阶段（phase）** | 一次有明确完成条件与归档门禁的交付周期 | `docs/phases/` |
| **归档门禁** | 阶段可否移入 `archive/` 的 7 条判据 | 不得仅因"代码写完"归档 |
| **工作包（work package）** | 可被一人认领的最小交付单元，ID 形如 `WP-B2-A` | 各模块 `docs/collaboration/workpackages.md` |
| **泳道（lane）** | 一组相关工作包的集合 | 每模块 3 条泳道，对应 2～3 人分工 |
| **CHG** | 契约变更记录，形如 `CHG-0001` | `docs/contracts/changes/` |
| **契约先行** | 先改契约并升版本，再改实现 | 反过来的做法会导致集成门禁失败 |
| **兼容层** | 旧接口/别名/双写/开关/降级路径 | **明确禁止**；破坏性变更一次性切换 |
| **P1 / P2 / P3 / P4 变更级别** | 增补 / 语义 / 破坏 / 纯文档 | 见 `contracts/change-protocol.md` |
| **铁律（三条）** | 单一权威 / 不声称未实现 / 失败留在文档 | 见 `collaboration/module-doc-spec.md` |
| **状态词** | 只允许 `骨架` / `待实现` / `已完成` / `阻塞` | 其他措辞（"基本完成"）视为缺陷 |
| **冷启动验收** | 新人只读少数文件即可上手 | 见 `testing/acceptance.md` §4 |
| **知识笔记** | 同一根因出现≥2 次且有稳定预防措施的可复用结论 | `knowledge/` |
| **即插即用** | 模块遵守规范即可被装载，无需修改 MAIN 代码 | 有 8 项验证规程（`runbooks/integration.md` §6） |

## 7. 容易混淆的对照

| 容易混淆 | 区别 |
|---|---|
| 提交 vs 评测运行 | 提交是"学生交了什么"；评测运行是"系统跑了一次"。一个提交可有多次运行 |
| `rejected` vs `failed` vs 低分 | 被拒（你的提交有问题）/ 技术失败（系统有问题）/ 代码没通过测试 |
| 事件 vs SSE | 事件是系统级广播（去标识化）；SSE 是会话级推送（可含更多 UI 细节） |
| correlation_id vs causation_id | 同一次业务操作 vs 直接因果 |
| 契约（MAIN）vs manifest（模块） | 契约定义**接口形状**；manifest 声明**模块事实**（能力/路由/事件/迁移 head） |
| 参考答案 vs 测试用例 | 都是受保护内容，但用途不同：参考实现 vs 判定输入输出 |
| 去标识化 vs 匿名化 | 本项目是**去标识化**（不含身份字段，但存在伪名 ID）；不是不可逆匿名化 |
| 校验（validation）vs 验证（verification） | 本项目语境："校验"多指 schema/格式检查；"验证"指真实执行并留证据 |
