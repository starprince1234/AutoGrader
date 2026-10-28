# 契约变更历史

本文件记录 `MAIN/contracts/**` 的每一次变更。变更规程见 `../docs/contracts/change-protocol.md`。

**规则**

1. 契约内容变更**必须**在此追加一条，且必须在**同一改动批次**内完成。
2. 所有 `openapi/*.yaml` 与 `asyncapi/events.yaml` 的 `info.version` 必须一致。
3. 摘要必须由 `python -m autograder_host.checks` 计算得出，**不得手填**。
4. 任何契约变更都必须同步更新四个模块的 `contracts.lock`（版本 + 摘要）。遗漏即集成失败。
5. **不允许**保留兼容层、字段别名、双写或降级路径。

---

## 0.1.0 — 初始化基线（baseline）

- **级别**：baseline（契约首次建立，尚无任何模块实现）
- **状态**：当前生效
- **摘要**：见 `contracts.lock`（由检查工具计算，不在此处复制以避免漂移）
- **内容**：
  - `openapi/common.yaml`：通用约定（ProblemDetails、UUID/时间/摘要/语言/角色/游标页、公共参数与公共错误响应）。
  - `openapi/b2.yaml`：提交接收、评测运行与 SSE 进度、报告、成绩查询与导出。
  - `openapi/b3.yaml`：题目、不可变版本与发布/归档、内部 evaluation package、assignment、去标识化统计。
  - `openapi/b4.yaml`：认证与 JWKS、用户、课程与课序、选课编辑、名单导入（上传/预览/列映射/确认）、审计、运行期模块注册表。
  - `asyncapi/events.yaml`：七个事件 channel + send/receive operation + payload 示例。
  - `schemas/`：语言枚举、提交信封、评测包、评测报告、名单导入件、事件信封、模块清单。
  - `examples/`：五份示例载荷，且**每份都必须通过其归属 schema 的实例校验**。

### 0.1.0 基线修订说明（初始化阶段内的一次收敛）

初始化阶段内在**尚无任何模块实现**的前提下，对初始占位契约做了一次收敛。这不是"改需求"，而是把语义空洞的占位符补成可据以并行开发的结构。列在此处以保证可审计：

| 契约 | 初始状态 | 收敛后 | 原因 |
|---|---|---|---|
| `openapi/*.yaml` | 仅路径与 operationId，无参数、无请求体、无响应 schema、无错误响应 | 全部补全：参数引用、请求体 schema、响应 schema、`401/403/404/409/413/422/429/503` 引用公共 ProblemDetails | 模块开发者无法据占位契约实现，必然各自发明字段，导致集成期冲突 |
| `openapi/common.yaml` | 只有 `ProblemDetails` | 增加 UUID / Timestamp / Sha256 / Language / Role / CourseScope / CursorPage、公共参数、十种公共错误响应 | 消除每个模块重复定义基础类型 |
| `evaluation-package.schema.json` | `adapter` 为字符串枚举；`tests` 为 `array<object>`；`security_policy` 为无约束 `object` | `adapter` 改为结构化对象（含比较策略、fixture、callable 入口）；`tests` 逐字段定义；`security_policy` 定义常量与数值上限 | 这是 B2↔B3 **最高风险耦合点**：B2 的 runner 核心依赖该结构，不能靠口头约定 |
| `evaluation-report.schema.json` | 只有 `score`/`details`/`result_digest` | 增加 `state`、`overall`、`static_findings`、`security_conclusion`（三态）、`case_evidence`（含固定失败类别枚举）、`binding`（五元摘要）、`total_check`（合计校验） | 落实 ADR-0005 的可解释性与可重放性；`rejected`/`failed` 与低分必须可区分 |
| `roster-import.schema.json` | 三个字段 | 增加 `size_bytes`、`uploaded_at` 与目标字段说明 | 说明"解析不猜测、确认前零写入"的边界 |
| `asyncapi/events.yaml` | 只有 channel address，无 messages、无 operation | 补齐 channel messages、send/receive operation、payload `$ref` 与示例；补充每个事件的语义说明 | 消费者无法据占位契约实现去重与幂等 |
| `schemas/language.schema.json` | 不存在 | 新增：`python` \| `bash` | 落地 `RQ-B-0-10`：语言必须是受控枚举，不能是自由字符串 |
| `examples/` | 1 份（事件） | 5 份（事件、提交信封、评测包、评测报告、名单导入件），并纳入**实例校验**门禁 | 示例可静默漂移；现在是自动化门禁的一部分 |
| `openapi/b2.yaml` 的 SSE 响应 | 只有一句描述 | 新增 `EvaluationStreamFrame` schema，把 `progress`/`completed`/`rejected`/`failed` 四种帧、单调 `sequence`、`terminal_reason` 枚举写进契约 | B1 的终态呈现依赖该通道；`rejected` 无对应领域事件，需在契约层说清楚，否则四组人对"被拒如何到达前端"的理解会不一致 |
| `openapi/b2.yaml` 的状态迁移 | 只在 `srs.md`/`models.md` 用文字与图描述 | 新增 `SubmissionTransitions` schema，把**允许的迁移穷举**写入契约 | B2 提出疑问：静态高危阻断会走 `validated → rejected` 的**短路**，需要明确这是合法的。契约现已成为该问题的权威答案 |

**受影响模块**：B1、B2、B3、B4（四份 `contracts.lock` 同步更新为 `0.1.0` + 新摘要）。
**破坏兼容**：不适用（此前无实现）。
**回滚方式**：回退本批次提交并重算摘要与四份锁。

---

## 0.2.0 — 补全鉴权声明（CHG-0006）

- **级别**：P2 语义
- **状态**：已生效
- **动机**：`governance.md` 强制 Bearer 鉴权，但契约里**没有任何 `securitySchemes`**，且 43 个
  operation 中 42 个没有 `security`。后果是真实的：契约消费方无法区分受保护与公开端点；
  B2 → B3 的服务间凭据形状（完整 `Authorization` header，含 `Bearer ` 前缀）在契约里无处可查，
  而这一条已经实际导致整条评测链在取题包阶段 401 断掉（表现为 `infrastructure_error`）。
- **变更文件**：
  - `openapi/common.yaml`：新增 `securitySchemes.bearerAuth`（http/bearer/JWT）与
    `securitySchemes.serviceToken`；版本 0.1.0 → 0.2.0
  - `openapi/b2.yaml` / `openapi/b3.yaml` / `openapi/b4.yaml`：各自新增**文档级默认**
    `security: [{bearerAuth: []}]`；`b4.createSession`、`b4.refreshSession` 声明 `security: []`；
    `b3.uploadVersionFixture`、`b3.getEvaluationPackage` 声明 `security: [{serviceToken: []}]`；
    版本同步 0.2.0
  - `asyncapi/events.yaml`：版本同步 0.2.0（内容未变，仅为保持"所有文件同版本"的规则）
- **受影响模块与需要做的改动**：
  - B1：重新生成 API 类型（类型会带上鉴权元数据）；运行时行为不变
  - B2：无代码改动；运维侧确认 `B2_B3_AUTHORIZATION` 含 `Bearer ` 前缀
  - B3 / B4：无代码改动（实现本就在要求相应凭据）
- **是否破坏兼容**：否。补记既有事实，无请求/响应结构变化；MINOR 升版
- **迁移方式**：一次性切换，无兼容层
- **验证**：`python -m autograder_host.checks` 三条门禁；另附匿名调用实测（受保护端点 401、
  公开端点 200）
- **摘要**：由检查工具计算后写入四份 `contracts.lock`，不在此复制
- **回滚方式**：回退本批次并重新运行 `scripts/relock_contracts.py`；不得只回滚一部分

**详细裁决**：[changes/CHG-0006-auth-declarations.md](../docs/contracts/changes/CHG-0006-auth-declarations.md)

---

## 0.2.0（同批次续）— 补全响应 schema：`listAssignments` / `listEnrollments`（CHG-0007）

- **级别**：P2 语义 + P1 增补（`student_id` 为纯新增字段）
- **状态**：已生效
- **动机**：审计发现 10 个 operation 的 `200` 只有一句 `description`、没有 schema。两条真实后果：
  ① 客户端只能对这几张表做"动态键"渲染；② B1 的"谁还没交"**做不出来**——名单端点不返回任何
  用户 id，而 B2 的成绩行按数据所有权只认平台用户 id（B2 的 `student_number` 永远是 null，这是
  正确的），两端没有可用连接键。B1 实现者按规程停下上报，未自行替代。
- **变更文件**：
  - `openapi/b3.yaml`：新增 `AssignmentPublic`（`status` 为 `open|archived`），接到 `listAssignments` 的 `200`
  - `openapi/b4.yaml`：新增 `EnrollmentPublic`，接到 `listEnrollments` 的 `200`；**新增 `student_id`
    （可空）**，来源 `b4.enrollments.user_id`（该列 `nullable=True`）
- **受影响模块与需要做的改动**：
  - **B4 需要一处实现改动**：`list_enrollments` 响应项加入 `student_id`（字段已在库中，无迁移）
  - B3 无代码改动（`AssignmentPublic` 已与契约一致）
  - B1 重新生成 API 类型；`C1 作业中心`、`C3 谁还没交` 依赖本变更
  - **B2 无改动，且不得**为连接而给成绩行填 `student_number`
- **是否破坏兼容**：否（新增字段；此前无 schema 被替换）
- **迁移方式**：一次性切换
- **验证**：三条门禁；并用**契约自己的 schema 校验线上真实响应**（4/4 通过，见 CHG 文件 §5）
- **摘要**：由检查工具计算后写入四份 `contracts.lock`
- **回滚方式**：回退本批次并重新运行 `scripts/relock_contracts.py`；不得只回退 `student_id`
- **未包含**：另有 8 个 operation 仍无响应 schema，已在 CHG 文件 §7 逐条列出，避免被当成已完成

**详细裁决**：[changes/CHG-0007-response-schemas.md](../docs/contracts/changes/CHG-0007-response-schemas.md)

---

## 0.3.0 — B3 写路径补全与参考解答验证存证（CHG-0008）

- **级别**：P1 增补（`tags`、`as_new_version`）+ P2 语义（`publish` 新增存证前置）
- **状态**：已生效
- **动机**：三处都是"契约没写 → 能力不可达"：
  ① `tags` 在 `b3` 列、B3 DTO、`QuestionPublic` 里都有，**唯独 `QuestionDraftInput` 没有**，
  而它是 `additionalProperties: false` 的 → 读路径声明了一个结构上不可能产生的值，库里 106 道题
  标签全空且无人能设置；
  ② `as_new_version` 早已实现（`B3/.../api/router.py`）却未声明，而契约 summary 又写着"改已发布
  版本返回 409"，**契约与实现对同一行为的描述相反**，生成的客户端无法调用；
  ③ **没有任何机制检查参考答案真的能产出期望输出** —— 全平台唯一知道这件事的
  `MAIN/scripts/verify_question_bank.py` 跑完不留痕迹，于是"参考答案从没跑过、或跑过后内容被改过"
  的题目可以正常发布并到达学生。
- **变更文件**：
  - `openapi/b3.yaml`：`QuestionDraftInput` 新增 `tags`；`updateQuestionDraft` 声明 `as_new_version`
    并补 `200` 响应 schema；新增 `POST .../attestations`（`tags: [internal]` + `serviceToken`）
    与 `ReferenceAttestationInput`/`ReferenceAttestationPublic`；`publish` 描述新增存证前置
  - 五个契约文件同步 0.3.0
- **受影响模块与需要做的改动**：
  - **B3**：`tags` 写入与校验；存证表 + 端点 + `publish` 前置；`RQ-B-3-30` 验收 5 的分页与实测
  - **MAIN**：题库脚本新增"产出存证"能力；导入时写入存证
  - B1 重新生成类型；B2 无改动（已发布的 103 个版本不受影响，新题目须先过校验器）
- **是否破坏兼容**：`publish` **有意**变严——没有存证的版本将无法发布。这是本变更的目的：
  "参考答案跑过"从此是发布条件，而不是一句期望。**不提供先发布后补存证的路径。**
- **迁移方式**：一次性切换，无兼容层、无跳过开关
- **验证**：严格 YAML 门禁拦下过一次重复键；版本同步门禁两次拦下"锁/清单落后于契约"；
  三条门禁在 0.3.0 下全绿。**0.3.0 新增字段的线上 schema 校验尚未执行**（待 B3 实现落地），
  不得写成已通过
- **摘要**：由检查工具计算后写入四份 `contracts.lock`
- **回滚方式**：回退本批次并重新运行 `scripts/relock_contracts.py`；不得只回退 `publish` 前置而保留端点

**详细裁决**：[changes/CHG-0008-b3-write-path-and-attestation.md](../docs/contracts/changes/CHG-0008-b3-write-path-and-attestation.md)

---

## 0.4.0 — 模块数量开放（CHG-0009）

- **级别**：P2 语义（纯放宽）
- **状态**：已生效
- **依据**：[ADR-0013](../docs/adr/ADR-0013-open-module-count.md)
- **动机**：PRD §8.6 把"新增一个遵守规范的模块、无需修改 MAIN 代码即可装载"写成可测判据，但
  `manifest.schema.json` 的 `module.pattern` 是 `^B[1-4]$`，**第五个模块在契约层就被拒**；
  同一 pattern 还被复制到 B4 的 `registry.py` 与 `api/modules.py`；`MAIN/pyproject.toml` 又硬依赖
  三个业务模块（没有那三个目录，MAIN 连 venv 都装不出来）。更糟的是**治理门禁**里模块列表是字面量
  `("B1","B2","B3","B4")`（3 处），而契约/路由校验用 `glob("B*/module.yaml")` —— 新模块会被装载
  却**逃过治理**，静默比拒绝危险。
- **变更文件**：
  - `manifest.schema.json`：`module.pattern` → `^B[0-9]{1,3}$`；`database.schema` 由枚举改为
    `pattern: ^(none|b[0-9]{1,3})$`；加 `$comment` 指向 ADR-0013，防止被改回去
  - 五个契约文件同步 0.4.0
- **受影响模块与需要做的改动**：
  - MAIN：`checks.py` 的 3 处字面量改为 `discovered_modules()`；`pyproject.toml` 打包分层；
    host 装载期校验 `module.yaml` 并对同名 entry point 响亮失败
  - B4：`MODULE_PATTERN` 与契约对齐（不得自造第二份口径）
  - B1/B2/B3：无代码改动；锁与 manifest 版本同步
- **是否破坏兼容**：否（纯放宽，现有四个清单仍满足新 pattern）
- **迁移方式**：一次性切换
- **验证**：门禁覆盖摘要/版本一致性；`discovered_modules()` 与 `module_schema_owners()` 与原先
  字面量等价（已实测）。**"新增合成模块同时被装载与被治理"尚未验证**，属独立工作项，不得写成已通过
- **摘要**：由检查工具计算后写入四份 `contracts.lock`
- **回滚方式**：回退本批次并重新运行 `scripts/relock_contracts.py`；同时需把 ADR-0013 状态改为"拒绝"

**详细裁决**：[changes/CHG-0009-open-module-count.md](../docs/contracts/changes/CHG-0009-open-module-count.md)

---

## 0.5.0 — 新增 B5：平台一致性审计模块（CHG-0010）

- **级别**：P1 增补
- **状态**：已生效
- **依据**：[ADR-0013](../docs/adr/ADR-0013-open-module-count.md)、PRD §8.6
- **动机**：PRD §8.6 把"新增一个遵守规范的模块，无需修改 MAIN 代码即可装载"写成可测判据，但在 B5
  之前**它没有任何证据**：四个模块都是与 MAIN 同步长出来的。要证明规范够用，只能**在规范冻结之后
  照着规范写一个新模块**。审计另发现三处硬编码把这条路封死（`^B[1-4]$` 三处、MAIN 核心依赖含业务
  模块、治理门禁模块列表是字面量），ADR-0013 已逐条处置，B5 是该 ADR 的**验收物**。
- **变更文件**：
  - 新增 `openapi/b5.yaml`：`GET /api/v1/platform/conformance` + `ConformanceReport` schema
  - 版本同步 0.5.0（五个契约文件、四份锁、五个 module.yaml、三个描述符）
- **受影响模块与需要做的改动**：
  - **B5**（新）：完整实现并遵守文档治理规范；提供 capability 且**声明对既有能力的依赖**，
    以验证 capability DAG 的真实排序
  - MAIN：契约与锁；B1–B4 仅版本号同步
- **是否破坏兼容**：否（纯增补）
- **迁移方式**：一次性切换
- **验证**：门禁覆盖版本/摘要一致性。**B5 落地的四项验证尚未完成**（发现式治理纳入第五个模块、
  `module_schema_owners()` 不包含 B5、host 装载五个模块、以及一个必定失败的负例），
  未完成前不得声称 PRD §8.6 已被证明
- **摘要**：由检查工具计算后写入四份 `contracts.lock`
- **回滚方式**：删除 `b5.yaml` 与 `B5/` 并重锁；回滚须同时修改 PRD §8.6，不能只删代码

**详细裁决**：[changes/CHG-0010-b5-conformance-module.md](../docs/contracts/changes/CHG-0010-b5-conformance-module.md)

---

## 0.6.0 — 落地 RQ-T-10/20：出题辅助与评语轨的对外接口（CHG-0011）

- **级别**：P1 增补
- **状态**：已生效
- **依据**：[ADR-0009](../docs/adr/ADR-0009-ai-assistance-two-track.md)、`rq-catalog.md` §5B
- **动机**：ADR-0009 已把 AI 能力的边界裁决清楚、§5B 已定义 `RQ-T-10/20` 的验收标准，但
  **契约里没有任何端点**，两个模块都无法开工。若各自先实现再补契约，就会重演 `as_new_version`
  那类"实现先行、声明缺失"——而那一处已经导致契约对同一行为的描述与实现相反。
- **变更文件**：
  - `openapi/b2.yaml`：`GET`/`POST /api/v1/evaluation-runs/{id}/commentary` +
    `EvaluationCommentary`、`CommentaryProvenance`
  - `openapi/b3.yaml`：`POST /api/v1/questions/{id}/authoring-assistance` +
    `AuthoringAssistanceRequest`、`AuthoringAssistance`、`AuthoringProvenance`
  - 五个契约文件同步 0.6.0
- **受影响模块与需要做的改动**：
  - B2 实现评语轨（白名单输入、provenance、默认关闭）；B3 实现出题辅助（枚举 topic、响应无受保护字段）
  - B1 展示并**原样显示 `disclaimers`**；未配置时显示"未启用"而非错误
- **是否破坏兼容**：否（两个端点均新增，且落在既有路由前缀下，`module.yaml` 无需改动）
- **迁移方式**：一次性切换
- **验证**：门禁覆盖版本/摘要一致性。**四项实现侧验证尚未完成**（白名单结构性证据、
  删除评语后重放分数不变、默认关闭且无出网调用、建议不改题），未完成前不得声称已通过
- **摘要**：由检查工具计算后写入五份 `contracts.lock`
- **回滚方式**：删除端点与 schema 并重锁；回滚须同时把 ADR-0009 改为"拒绝"并撤销 §5B

**详细裁决**：[changes/CHG-0011-ai-assistance-endpoints.md](../docs/contracts/changes/CHG-0011-ai-assistance-endpoints.md)

---

## 0.7.0 — 逐用例证据补全：资源实测、文件证据、确定性解释（CHG-0012）

- **级别**：P1 增补
- **状态**：已生效
- **依据**：ADR-0005（可解释/可重放）、ADR-0010（隔离可证明）
- **动机**：证据链断在半路，三处：① 逐用例资源使用从未落证据，于是 1 vCPU / 512 MB 只是**限额声明**
  而账本没有实测值（ADR-0010 决策 3 要求回读 cgroup 峰值，但 `case_evidence` 没有字段可放）；
  ② B2 的 `FixtureOutcome` 已带 expected/actual 摘要，`build_evidence` **只拿它挑一段文本**，
  摘要不进证据/报告 —— 于是 `RQ-B-0-30` 验收 2「被修改文件清单与内容摘要」**四个模块无人实现**；
  ③ 报告没有面向人的解释字段（ADR-0005 的后果节点本就允许结构化规则说明文本）。
- **变更文件**：`schemas/evaluation-report.schema.json` —— `case_evidence.items` 新增
  `resource` / `artifacts` / `explanation`（均可空）+ `$defs` 中的 `case_resource` /
  `case_artifacts` / `file_change_list`；六个契约文件同步 0.7.0
- **受影响模块**：B2 实现（回读 cgroup、落摘要与文件变更清单、规则式解释）；B1 呈现；
  MAIN 契约与五份锁
- **是否破坏兼容**：否（字段可选，既有报告仍合法）
- **迁移方式**：一次性切换
- **验证**：schema 自检通过；**带新字段的报告实例通过校验**；并实测
  **在 `case_evidence` 里插入模型输出字段会被 schema 拒绝** —— 即"模型输出进不了报告"是结构性的
- **未完成**：实现侧验证（真实 cgroup 峰值落库、文件清单出现在报告、解释同输入同输出）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删字段并重锁；回滚须同步修改 ADR-0010 与 `rq-catalog.md`

**详细裁决**：[changes/CHG-0012-per-case-evidence.md](../docs/contracts/changes/CHG-0012-per-case-evidence.md)

---

## 0.8.0 — B3 质量能力的出口（CHG-0013）

- **级别**：P1 增补
- **状态**：已生效
- **动机**：B3 上一批实现了五项题库质量能力，其中三项**没有可用出口**，对教师不可见——功能存在
  而无法使用，交付上等同于没做。B3 按规程停下上报，没有自行加未声明的参数或字段。
  ① 比较策略诊断（4 条确定性规则）无读取端点；
  ② 修订闭环已落库 `revision_diff`/修订原因，但 `QuestionVersionPublic` 无字段可读、
  `QuestionDraftInput` 无字段可写（且该 schema 是 `additionalProperties: false`）；
  ③ `tags` 自 `CHG-0008` 起可写可读，但 `GET /questions` 只声明三个参数——教师能给 106 道题打标签，
  **却无法问"进程调度那几道是哪些"**，而检索正是标签的全部价值。
- **变更文件**：
  - `openapi/b3.yaml`：新增 `GET /api/v1/questions/{question_id}/diagnostics` +
    `ComparisonDiagnostics`；`QuestionVersionPublic` 新增 `revision_note`/`revision_diff` +
    `RevisionDiff`；`QuestionDraftInput` 新增 `revision_note`；`GET /questions` 新增 `tags` 查询参数
  - 六个契约文件同步 0.8.0
- **受影响模块**：B3 接线与落库；B1 出题/筛选界面（诊断结论必须与 `RQ-T-10` 的带
  `confidence: advisory` 的模型建议**区分开**）；MAIN 契约与锁
- **是否破坏兼容**：否（新增端点 + 三个可选字段 + 一个可选参数）
- **迁移方式**：一次性切换
- **验证**：门禁覆盖版本/摘要一致性；**三条实现侧验证未完成**（`revision_diff` 键集合断言与受保护
  词汇不出现、`tags` 全匹配语义、诊断空列表 ≠ 未检查）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除端点/字段/参数并重锁；回滚须同时在 B3 追踪表里把三项能力标为不可用

**详细裁决**：[changes/CHG-0013-b3-quality-surfaces.md](../docs/contracts/changes/CHG-0013-b3-quality-surfaces.md)

---

## 0.9.0 — 隔离区复核台与证据包（CHG-0014）

- **级别**：P1 增补
- **状态**：已生效
- **动机**：两处**数据一直在写、却没有读出口**（与 CHG-0013 同类）。
  ① `b2.intake_quarantines` 自邮件接收路径建成起就在记录无法解析作业标识或被拒的来件，但**没有
  任何读取路径** —— 审计的措辞很准确：从教师角度看，未匹配的邮件**无声无息地不见了**。学生说
  "我交了"、教师查不到、系统里也没地方查得到，这不是少一个页面，是**交付链断了一环**。
  ② 成绩争议要用证据回答，而证据必须**可移动**：一个没登录本部署的复核者应当能仅凭文件判断判分
  是否成立，而现有报告端点要求在线且不含重现方式。
- **变更文件**：`openapi/b2.yaml` 新增 `GET /api/v1/intake/quarantines`、
  `POST /api/v1/intake/quarantines/{id}/resolve`、`GET /api/v1/evaluation-runs/{id}/appeal-pack`
  与 `IntakeQuarantineView`、`AppealPack`；六个契约文件同步 0.9.0
- **受影响模块**：
  - B2 实现三个端点（手动归属**重跑相同校验**；证据包**移除宿主绝对路径**且**不扩大可见范围**）
  - B1 教师侧隔离区复核界面 + 证据包下载
  - MAIN 契约与锁；`B2/module.yaml` 与 descriptor 补 `/api/v1/intake` 前缀（已在本次变更中同步）
- **是否破坏兼容**：否（三个端点均新增）
- **迁移方式**：一次性切换
- **验证**：门禁覆盖版本/摘要与路由前缀。**四条实现侧验证未完成**（手动归属仍被校验拒绝、
  证据包不含宿主路径、导出不扩大可见范围、未匹配邮件可被找到）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除端点与 schema 并重锁；**同时**移除 `/api/v1/intake` 前缀，否则路由门禁会报
  "声明的前缀不覆盖任何契约路径"

**详细裁决**：[changes/CHG-0014-quarantine-and-appeal-pack.md](../docs/contracts/changes/CHG-0014-quarantine-and-appeal-pack.md)

---

## 0.10.0 — 题目质量热力图（CHG-0015）

- **级别**：P1 增补
- **状态**：已生效
- **动机**：`RQ-B-3-30` 的统计只能消费去标识化事件，因此 B3 拿到的是**分数分桶**，看不到逐用例与逐规则的细节 —— 而这两样正是判断「这道题有没有区分度」所需要的：某个用例是不是**所有人都过不了**；某条静态规则是否**从未命中过任何提交**；失败里有多少是 `infrastructure_error`（**系统的问题，不是题目的难度**）。这些数据属于 B2，因此聚合也归 B2；两者**输入不同**，能回答的问题也不同。
- **变更文件**：`openapi/b2.yaml` 新增 `GET /api/v1/evaluation-runs/quality-summary` 与
  `EvaluationQualitySummary`；六个契约文件同步 0.10.0
- **受影响模块**：B2 实现聚合（**先注册字面路由**，否则会被 `{evaluation_run_id}` 吞掉，表现为令人困惑的 404/422）；B1 教师侧质量视图；MAIN 契约与锁
- **是否破坏兼容**：否
- **迁移方式**：一次性切换
- **验证**：门禁覆盖版本/摘要。**四条实现侧验证未完成**（路由顺序、只输出计数、三态不被合并、`unmeasured_runs` 可见）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除端点与 schema 并重锁，同时在 B2 追踪表与创新清单里标回未交付

**详细裁决**：[changes/CHG-0015-quality-heatmap.md](../docs/contracts/changes/CHG-0015-quality-heatmap.md)

---

## 0.11.0 — `updateQuestionDraft` 的响应声明与实现不符（CHG-0016）

- **级别**：P2 语义（改正错误的响应声明，**线缆行为不变**）
- **状态**：已生效
- **动机**：同一个 URL 声明了两种实体形状，而其中一种是错的。`PATCH /api/v1/questions/{question_id}` 的 200 声明为 `QuestionVersionPublic`，同路径的 `GET` 声明为 `QuestionPublic`，而实现自 Phase 01 起两条路径都返回 `QuestionPublic`。**判定依据不是"谁改起来省事"，而是声明是否可表达**：`QuestionDraftInput` 可写 `title`（必填）与 `tags`，而 `QuestionVersionPublic`（`additionalProperties: false`）**两者都没有** —— 一个无法表达"请求刚刚改动的字段"的响应 schema 就是错的。该错误**已经造成了真实缺陷**：B1 的出题写路径按生成的类型读取 `version.question_version_id` 与 `version.version`，二者在运行期恒为 `undefined` 并被写进审计日志，而 TypeScript 因契约声明错误而无法发现。
- **变更文件**：`openapi/b3.yaml` 中 `updateQuestionDraft` 的 200 引用
  `QuestionVersionPublic` → `QuestionPublic`；六个契约文件同步 0.11.0
- **受影响模块与需要做的改动**：**B3 无需改动**（实现本就是这个形状）；**B1 需要改** `useAuthoring.ts` 的 `useUpdateQuestionDraft`，改为消费 `QuestionPublic` 并从 `versions[0]` 取 `question_version_id`/`version`；MAIN 契约与五份锁
- **是否破坏兼容**：否。服务端线缆行为完全不变，本变更只是让声明追上实现。生成的客户端类型会变化，因此**这是把运行期静默 `undefined` 变成编译期可见错误**，是收紧而非破坏。
- **迁移方式**：一次性切换。**不**给响应加形状开关或 `oneOf`——两种形状并列会强迫每个消费者处理一个永不出现的分支，那是把契约错误转嫁给所有客户端。
- **同时判定为不成立**：B3 上报的"契约描述把 `severity` 第三值写成 `note`"经全量检索**不成立**（三处 severity 枚举全部是 `[error, warning, info]`，`note` 只出现在无关散文中），因此**不修改**严重度枚举或描述。详见 CHG-0016 §5。
- **验证**：门禁覆盖版本/摘要一致性。**两条实现侧验证未完成**（B3 断言端点真实返回键集合等于 `QuestionPublic`；B1 修好消费方后日志字段为真实值而非 `undefined`）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：把引用改回 `QuestionVersionPublic` 并重锁；回滚会重新引入 B1 的静默 `undefined` 缺陷，因此必须同时在 B1 追踪表里记回该缺陷，而不是只回滚契约

**详细裁决**：[changes/CHG-0016-question-patch-response-shape.md](../docs/contracts/changes/CHG-0016-question-patch-response-shape.md)

---

## 0.12.0 — 草稿写入的省略语义：省略即保留（CHG-0017）

- **级别**：P2 语义（声明并收紧既有行为；线缆格式不变）
- **状态**：已生效
- **动机**：`QuestionDraftInput` 的 `cases` 与 `reference_answer` 都是可选字段，但契约**从未说明省略意味着"保持原样"还是"清空"**，而实现与契约相反：契约的 `required` 不含 `cases`，实现的 `cases: tuple[TestCaseInputModel, ...] = Field(min_length=1)` 无默认值因而**必填**。实测省略 `cases` → `ValidationError: Field required`（即 422）。**所以这不是"契约沉默"，是"契约写错了"**——与 CHG-0016 同类、方向相反：那次实现比契约宽松，这次实现比契约严格。B1 因此拒绝提交"只改标题"的编辑（不肯在两种相反语义之间赌），这是对的。
- **为什么不做更小的改动**：把 `cases` 标成必填只需一行，也能让契约与实现一致。放弃它是因为它把**受保护内容的一次往返固化成契约要求**：若 `cases` 必填，则每次编辑（含只改标题）都必须把**隐藏用例与参考答案**送到浏览器再送回来。本项目专门做了"执行者 ≠ 记录者"的拆分来避免这类暴露，让受保护内容为了改一个标题而出一次门与那条设计意图直接冲突。**这是两种正确性之间的取舍，不是拿方便换严谨。**
- **变更文件**：`openapi/b3.yaml` 的 `QuestionDraftInput` 描述、`reference_answer` 描述、`cases` 描述；六个契约文件同步 0.12.0
- **受影响模块与需要做的改动**：**B3** 把 `cases` 改为可选，并只在字段**被提供**时调用 `replace_cases`（用 `model_fields_set` 判定"是否提供"，与 `revision_note` 同一套做法）；`reference_answer` 现有行为已正确，保持不动。**B1** 在 B3 落地**之后**才可放开"只改标题"的保存——提前放开会被 422 拒绝，界面上看起来像保存失败。MAIN 契约、五份锁、五份 `module.yaml`、四个描述符
- **是否破坏兼容**：否。线缆格式、`minItems`、`required` 列表均未改变；变的是**语义被写明**，且实现向契约靠拢（放宽，而非收紧）
- **迁移方式**：一次性切换。**不提供**"清空全部用例"的操作：没有用例的版本无法被评测，那是一个其余系统都得当作"坏了"而非"空"的状态，与其为它设入口不如让它不可达
- **验证**：门禁覆盖版本/摘要一致性；新增的静态描述符检查确认四个 `plugin.py` 与五份清单同步。**五条实现侧验证未完成**（省略 `cases` 返回 200 且用例集合逐字节不变；省略时受保护内容未被读取；提供时整体替换；`cases: []` 在写入前被拒；只改标题不改变参考答案）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除语义声明、把 `cases` 标回必填并重锁；回滚会让"只改标题"重新不可表达，并让受保护内容重新成为每次编辑的必经内容，因此必须同时在 B1 追踪表里记回该限制

**详细裁决**：[changes/CHG-0017-omission-preserves-draft-fields.md](../docs/contracts/changes/CHG-0017-omission-preserves-draft-fields.md)

---

## 0.13.0 — AI 评分参考：`EvaluationCommentary.advisory_score`（CHG-0018）

- **级别**：P2 语义增补（新增一个可空子对象与一个 schema；无既有形状改变）
- **状态**：已生效
- **动机**：负责人（2026-09-20）要求增加"AI 可以提供评分参考"。这与 `CHG-0011` 的一条**刻意设计**正面冲突——那次为 `EvaluationCommentary` 写了整段描述说明它**没有 `score` 字段**，理由是"有了它，消费者迟早会把评语当成评分输入"。
- **为什么是加字段而不是维持原样**：那条理由仍然成立，但它指向的结论不是"不要建模"，而是"**不要建模成一个看起来像分的形状**"。不建模它并不会让它消失——评语里写一句"按宽松策略大致能得 8 分"已经是一个分数主张，只是完全没有结构可以断言。让它消失是做不到的，让它可审计是做得到的。
- **变更文件**：`openapi/b2.yaml` 新增 `AdvisoryScore`；`EvaluationCommentary` 新增可空 `advisory_score` 并改写描述（**正面回应**原句，而不是删掉它）；`disclaimers` 的描述扩到覆盖分数参考；六个契约文件同步 0.13.0
- **关键设计**：① 它是**子对象**，消费者只能写 `commentary.advisory_score.score`——访问路径本身在代码里说明它不是权威分；② 命名用 `advisory` 而非 `reference`，因为 `reference_answer` / `reference_attestations` 已占用"reference"；③ **`max_score` 由 B2 从证据填入**（各用例权重之和，是可复算的事实），模型只给 `score`/`rationale`/`confidence`；④ 越界（负或超满分）判为**生成失败而非截断**——截断会把"模型说了什么"悄悄改成"我们允许它说什么"
- **受影响模块与需要做的改动**：**B2** prompt 改为要求 JSON 对象并解析出两个产物、`max_score` 由证据填入、落库新列、响应返回新字段，并补"参考分进不了分数轨"的断言；**B1** 与权威分**并列**展示且样式不得让参考分显得更权威、原样显示 `disclaimers`；MAIN 契约、五份锁、五份 `module.yaml`、四个描述符、`rq-catalog.md` 新增 `RQ-T-30`
- **安全与隐私检查**：输入白名单与 `RQ-T-20` **完全相同**（同一次生成、同一份结构化证据，永不读学生源码/参考答案/隐藏用例/期望输出）。因此**没有新增数据出境面**：模型看到的仍只是已落库的脱敏证据。
- **是否破坏兼容**：否。`advisory_score` 缺省为 `null`，既有消费者读到的对象形状不变
- **迁移方式**：一次性切换，不保留兼容层。**不提供**"只生成参考分、不生成评语"的开关——那会产生一个没人能说清含义的状态组合
- **验证**：门禁覆盖版本/摘要一致性。**实现侧验证未完成**（`CHG-0018` §7 的五条：参考分进不了报告/成绩/导出、删评语重放分数不变、默认关闭无出网、越界即失败、`max_score` 来自证据）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除 `AdvisoryScore` 与 `advisory_score`，重新运行 `scripts/relock_contracts.py`，版本回到 0.12.0；同时把 `ADR-0014` 改为"拒绝"并撤销 `RQ-T-30`，而不是只删字段

**详细裁决**：[changes/CHG-0018-ai-advisory-score.md](../docs/contracts/changes/CHG-0018-ai-advisory-score.md)

---

## 0.14.0 — 题库标签覆盖度：让"哪些知识点有多少题"成为可读的事实（CHG-0020）

- **级别**：P1 增补（一个新端点 + 一个 schema）
- **状态**：已生效
- **动机**：`tags` 能写（`CHG-0008`）能查（`CHG-0013`），但它们的**分布没有出口**。`CursorPage` 只有 `next_cursor`、**没有总数**（`common.yaml:63`），而分页下"把题库数一遍"不是读者能做的动作。于是"这个知识点是两道题还是二十道"这个问题，只能靠人一页页翻。
- **变更文件**：`openapi/b3.yaml` 新增 `GET /api/v1/questions/coverage` 与 `QuestionTagCoverage`；六个契约文件同步 0.14.0
- **关键设计**：① **只返回计数**，刻意不含 `question_id` 列表——带上 id 它就从"分布"变成"逐题翻查题库的旁路"（与 `CHG-0015` 同一条理由）；② 可校验的恒等式是 **`tagged_questions + untagged_questions == total_questions`**，而**不是** `sum(tags[].question_count) + untagged == total` —— 一道题可带最多 20 个标签，逐标签计数**按设计重叠**。起草本变更时第一版写错了这个式子，已在契约描述里写明正确读法；③ 端点**声明在 `/questions/{question_id}` 之前**，否则会被路径参数吞掉（`quality-summary` 已经踩过一次）；④ `published_only` 默认 `true`，与 `listQuestions` 同一默认同一理由：忘了传参数必须少看见、不能多看见
- **受影响模块与需要做的改动**：**B3** 实现端点（合计用 `count(*) FILTER` **各自计数而非相减**，逐标签用 `jsonb_array_elements_text` 展开后一次 `GROUP BY`；**不建预聚合表**——那会引入一个需要维护的第二事实源，而当前数据量下没有必要）；**B1** 覆盖度视图（可选，未开工）；MAIN 契约、五份锁、五份 `module.yaml`、四个描述符。**更正（2026-09-20）**：本行原写"走既有 `ix_questions_tags` GIN 索引"，**该断言已被实施侧的 `EXPLAIN ANALYZE` 推翻**——jsonb 上的 GIN 只服务包含/存在类谓词（`@>`、`?`），而集合返回函数展开没有可用操作符类；实测计划为 `Index Scan using ix_questions_course_id → Function Scan → HashAggregate → Sort`（10 行，0.160 ms）。结论不变，详见 `CHG-0020` §5
- **明确否定并写明理由的三件相邻想法**：**知识点图谱**（B3 没有知识点模型，标签是自由字符串、只有形态校验、**无受控词表**——图谱需要一张人工维护的本体，那是课程内容工作）；**重复题目检测**（需要题目间相似度，B3 不存任何可比向量，也不该为此引入依赖，且"重复"的判定标准没有权威答案）；**难度自适应推荐**（B3 只消费去标识化事件、只有分数分桶，没有逐题难度参数，也没有学生个体历史）。记下这些是让后来者看到"被考虑过并否定"，而不是"没人想到"
- **安全与隐私检查**：响应只含计数与标签字符串（标签是教师自己写的公开元数据，已在公开 DTO 中）；不调用模型、不新增依赖、不引入新的数据出境面
- **是否破坏兼容**：否。新增一个端点与一个 schema
- **迁移方式**：一次性切换，不保留兼容层
- **验证**：门禁覆盖版本/摘要一致性。**实现侧验证未完成**（`CHG-0020` §8 的五条，含合计校验、跨课程 `403`、学生只见已发布、响应无任何 id、空标签库不报错）
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除端点与 schema，重新运行 `scripts/relock_contracts.py`，版本回到 0.13.0。纯读，无数据影响

**详细裁决**：[changes/CHG-0020-question-tag-coverage.md](../docs/contracts/changes/CHG-0020-question-tag-coverage.md)

---

## 0.15.0 — 参考解答验证的对外接口（含它缺失的输入通道）、三处契约修复与一处只能宣布的平局（CHG-0019 / CHG-0021 / CHG-0022 / CHG-0023 / CHG-0024）

- **级别**：P1 增补（两个新端点 + 两个新 schema）+ P2 语义（三处修复）
- **状态**：已生效（契约）；**实现侧排期**（`CHG-0019` 的 B2 端点、`CHG-0023` 的 B3 端点、B1 的入口）
- **动机**：四件事在同一批落地，因为它们都要动同一组文件。
  ① `CHG-0008` 的发布存证门禁是真的，而它"执行者 ≠ 记录者"的前提让**出题界面无法完成发布**——
  教师不能自我声明，浏览器不能持有 `serviceToken`。`ADR-0015` 裁决为 **B2 执行、B3 记录、教师一键触发**。
  ② `resolveIntakeQuarantine` 的契约正文"重跑与自动路径相同的校验"在数据模型下**不可判定**
  （隔离表不存报文与摘要，而响应集合里没有 `503`，反证契约不打算让它依赖邮箱），且契约对
  `detail` 的长度与 `resolved` 的省略语义写的是**与实现相反的事实**。
  ③ 同一个"标签"值集在契约里有**四种写法**，其中两个**输出**字段什么都没说。
  ④ **`CHG-0019` 起草时漏了一个必需输入**：端点要 B2"执行参考解答"，而契约里**没有任何通道**
  把参考解答交付给 B2。B2 实施时按规程停下上报，`CHG-0023` 记下并补上。
  另有 B3 上报、MAIN 用真实端口复测确认的第五件：**同一请求既未认证又不合法时，B2/B4 给 `401`、B3 给 `422`**。
  ⑤ **用契约校验真实响应时抓到第四处"契约写不出实现合法产出的值"**：`QuestionVersionSummary.published_at`
  与 `QuestionVersionPublic.published_at` 都声明为非空 `Timestamp`，而**草稿版本没有发布时间**——
  实测库里 7 个草稿全部为 `NULL`（103 个已发布全部非空），于是**任何带草稿的题目都无法被严格消费者读取**，
  而带草稿是出题流程的常态（`CHG-0024`）。
- **变更文件**：`openapi/b2.yaml`（新增 `POST /api/v1/reference-verifications` 与 `ReferenceVerification`；
  `listIntakeQuarantines` 的 `resolved` 描述；`resolveIntakeQuarantine` 的正文与 `422` 语义；
  `IntakeQuarantineView` 的 `reason_code` 词表与 `detail.maxLength` 500 → 1000）；
  `openapi/b3.yaml`（**新增 `GET /api/v1/question-versions/{id}/reference-answer` 与 `ReferenceAnswer`**；
  `QuestionTagCoverage.tags[].tag` 补 `pattern`；`QuestionPublic.tags[]` 补长度、`maxItems` 与 `pattern`；
  **`QuestionVersionSummary`/`QuestionVersionPublic` 的 `published_at` 放宽为可空**）；
  `openapi/common.yaml`（`ProblemDetails` 说明各状态码语义是硬约束、**多个同时适用时顺序不受保证**，
  并写明两条稳定事实）；六份契约文件（含 `asyncapi/events.yaml`）同步 0.15.0
- **关键设计**：① 新端点**不落库**、**不返回受保护内容**（只有计数、逐用例结局与失败类别），
  粒度与报告端点的 `view=teacher` 同级；② 逐用例用 `passed: boolean` + `failure_category`，**复用**报告
  schema 的 8 值枚举而**不新造** `outcome` 词表——`CHG-0019` 初稿写的 `outcome` 与 `adapter_kind: string`
  都会新造词表，实施时改为复用 `adapter_kind` 的既有 3 值枚举（`check_shared_vocabularies` 现守 4 对副本）；
  ③ `detail.maxLength` 判为**契约写错**（数据库 CHECK 与 `MAX_QUARANTINE_DETAIL` 都是 1000，
  服务器会合法产出自己 schema 判为不合法的值）；④ `resolved` 省略语义判为**散文写错**，判据是**表达力**
  ——`boolean` 无法表达"两者都要"，省略若等于 `false` 就永久删除了"列出全部"的能力；
  ⑤ `401`/`422` 的平局裁决为**不受保证**而非强行统一：收敛会让 B2 丢掉"错误体形状由一处保证"这一性质，
  而 B3 要改约 17 条路由换来零可观察收益；⑥ **参考解答走独立端点而不是塞进 evaluation package**：
  只有验证路径需要它，而取包发生在**每一次评测**；放进包里等于每次评测都多带一份参考解源码，
  且会改编译器输出 ⇒ 改变每个已发布版本的 `package_digest` ⇒ 使已绑定进历史结果的摘要全部失配
  （`phase-02 §2.5.2` 记录过那 103 条摘要为何曾全部陈旧）。两处代价都不该由"多一个字段"引起
- **受影响模块与需要做的改动**：**B3** 新增参考解答端点（`b3.reference_answers` 与 `has_reference_answer()`
  已在仓库层就位，**无迁移、无新表**；`serviceToken` 复用既有 scheme）并落地 `CHG-0017`（`cases` 改为可选、
  读 `model_fields_set`）；**B2** 实现 `CHG-0019` 端点（复用 `HttpPackageSource` 取包、`runner/` 执行与比对、
  在 `B3Endpoint` 上加两个同源调用：取参考解答与 `recordReferenceAttestation`；**不新增凭据**），
  在 `B2/module.yaml` 声明新路由前缀，并按 `CHG-0021` 更新 `docs/api/contract.md`、按 MAIN 补上的
  `WP-B2-D` 定义同步其追踪表；**B1** 出题页增加"验证并发布"入口与三态呈现
  （未通过 → 展示逐用例失败类别；`infrastructure_error` → 明说"验证需要 Linux 沙箱环境"，**不得伪装成功**），
  并按 `?resolved=false` 表达"只看未归属"；MAIN 契约、五份锁、五份 `module.yaml`、四个描述符
- **安全与隐私检查**：`CHG-0019` 的响应字段逐条列出，**无**期望输出、隐藏用例输入或参考解答源码；
  调用者须先通过课程写权限判定（判定在服务端，按钮可见性不构成边界）。`CHG-0023` 的响应**是**受保护内容，
  因此收窄到 `serviceToken`（教师与学生不可调用），且**不新增出境面**：B2 本就能取到隐藏用例的期望输出。
  两处对 B3 的调用都复用**已存在**的服务令牌通道，**没有新增数据出境面，也没有新增凭据存放位置**。
  契约只声明字段与类型，**不放任何真实参考解答正文**（MAIN 硬规则 7）。`CHG-0021` 的可归属收紧
  （内容类拒绝一律 `422`）是**缩小**攻击面：它明确关掉"用人工归属绕过自动信封校验"这条路
- **明确否定并写明理由的想法**：把参考解答并入 evaluation package（权限与摘要两处代价，见上）；
  先实现"除取参考解答以外"的全部（端点将永远无法返回 200，验证项 2/3/4 无法验收，属部分实现）；
  把 `401`/`422` 强行定序；为覆盖度建预聚合表；给标签引入受控词表
- **是否破坏兼容**：否。均为新增或补约束；`CHG-0019` 收紧可归属集合，但该端点此前**没有实现**，
  不存在依赖旧语义的消费者。**`0.15.0` 被修订而不是另开 `0.16.0`**：该版本尚未被任何模块实现消费，
  且这不是两次变更，是**一次变更没写完**——补上它比留一个"已生效但不可实现"的端点在语义上诚实
- **迁移方式**：一次性切换，不保留兼容层
- **验证**：门禁覆盖版本/摘要一致性（`check_shared_vocabularies` 现登记 **4 对**词表：`language`、
  `failure_category`、`adapter_kind`×2）。**实现侧验证未完成**，逐条见各 CHG 的验证节
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：删除两个端点与两个 schema、还原四处文本、重跑 `scripts/relock_contracts.py`，版本回到 0.14.0。
  **回滚后必须把 `CHG-0021` §1、`CHG-0022` §1 与 `CHG-0023` §1 的缺口重新登记回 `phase-02`**，
  而不是让契约恢复成"看起来完备、实现只能猜"的状态

**详细裁决**：[CHG-0019](../docs/contracts/changes/CHG-0019-reference-verification-endpoint.md) ·
[CHG-0021](../docs/contracts/changes/CHG-0021-quarantine-contract-repairs.md) ·
[CHG-0022](../docs/contracts/changes/CHG-0022-tag-shape-declarations.md) ·
[CHG-0023](../docs/contracts/changes/CHG-0023-reference-answer-channel.md) ·
[CHG-0024](../docs/contracts/changes/CHG-0024-draft-published-at-nullability.md)

---

## 0.16.0 — 让发布闭环可执行：三处声明补全（CHG-0026 / CHG-0027 / CHG-0028）

- **级别**：P2 语义（全部是"把已成立或必须成立的事实写成声明"；不改任何字段、类型或状态码集合）
- **状态**：已生效（契约）；**`CHG-0028` 需要 B3 改实现**，另两条无实现改动
- **动机**：三件事同批，**因为它们是同一类**——契约没有把决定实现行为的前置条件写下来。
  ① `getEvaluationPackage` 对**草稿**返回 `409`、而 `recordReferenceAttestation` **只接受草稿**
  ⇒ "验证草稿 → 存证 → 发布"**在真实环境里依然不可能**，教师依旧卡在发布——**而那正是 `CHG-0019` 存在的唯一理由**。
  ② `reference-answer` 的 `409` 说明**同段里给了两种读法**（"没有参考解答" vs "状态使验证不可能"），
  实施侧必须猜哪一句是判据。③ `reason_code` 的 19 项闭合词表只写在 **description 表格**里，
  于是消费者只能手抄字段表——违反"字段定义只在 `contracts/**`"。
- **变更文件**：`openapi/b3.yaml`（`getEvaluationPackage` 的 summary/description/`409` 语义——**草稿也服务**、
  收窄 "immutable"、并**更正一句假话**："The response carries reference answers" 与事实不符，
  参考解答有自己的通道 `CHG-0023`；`recordReferenceAttestation` 写明**只接受草稿**及 `409` 含义；
  `getReferenceAnswer` 的 `409` **点名两种情形**并要求 `detail` 可区分）；
  `openapi/b2.yaml`（`IntakeQuarantineView.reason_code` 增加 19 项 `enum`）；
  `schemas/evaluation-package.schema.json`（`created_at` 说明草稿与已发布的区别）；六份契约文件同步 0.16.0
- **关键设计**：① **草稿也服务评测包**，`created_at` 用**内容自身的时间戳**（草稿没有 `published_at`）、
  **跳过**摘要比对（草稿没有已记录的摘要），但响应**仍然给出** `package_digest`——它是"本次编译出的内容的摘要"，
  诚实且只是尚未被任何记录绑定；② **明确否决**"让存证也能写在已发布版本上"：那会把门禁放到冻结**之后**，
  等于取消 `CHG-0008` 的门禁；③ `reason_code` 只给**响应字段**加 `enum`，**查询参数刻意不加**——
  那会把"用不存在的码筛选"从返回空页变成 `422`，是**行为变更**而非补声明，需要单独裁决；
  ④ 可归属的那一对值**留在散文里**：做成 `x-` 扩展会得到一份**没有任何工具核对的"机器可读"副本**，
  而**假的机器可读比散文更危险**（散文会让人去读实现，假机器可读会让人信任它）
- **受影响模块与需要做的改动**：**B3** 让 `load_evaluation_package` 接受草稿（`load_published_snapshot`
  需要一条不要求 `published_at` 的兄弟路径），**无迁移**；**B2** 无代码改动（其草稿 `409 → 422` 映射保留，
  它仍覆盖"没有可编译内容"的情形）；**B1** 无改动；MAIN 契约、五份锁、五份 `module.yaml`、四个描述符
- **安全与隐私检查**：草稿的包含隐藏用例与期望输出，与已发布版本**同级**，调用方不变（`serviceToken`，
  当前只有 B2）⇒ **不新增出境面**。唯一新增的暴露是"未冻结内容的受保护部分会经该通道外发"，
  而那是**必需的**：验证的定义就是"在一道题发给学生**之前**，用它的真实用例检查它的参考解答"。
  草稿的包**不进入任何记录**（不写摘要、不落库），因此不会变成第二份事实源
- **明确否定并写明理由的想法**：把存证改为可写在已发布版本上（见上）；给查询参数 `reason_code` 加 `enum`；
  用 `x-` 扩展承载"可归属"这一对值
- **是否破坏兼容**：否。`CHG-0028` 只是**放宽**（草稿从 `409` 变 `200`），`CHG-0027` 只收紧到实现**已经保证**的集合，
  `CHG-0026` 只改描述。**但必须写明**：`CHG-0019` 的验证项 7.2/7.3/7.4 在此之前**无法执行**
- **迁移方式**：一次性切换，不保留兼容层
- **验证**：门禁覆盖版本/摘要一致性。**实现侧验证未完成**：`CHG-0028` §9 的 6 条、`CHG-0026` §8 的 4 条、
  `CHG-0027` §9 的 4 条
- **摘要**：由检查工具计算后写入五份 `contracts.lock`（B1–B5）
- **回滚方式**：还原上述文本、重跑 `scripts/relock_contracts.py`，版本回到 0.15.0。
  **回滚后必须把 `CHG-0028` §1 的矛盾重新登记回 `phase-02`，并把 `CHG-0019` 的状态改回"目的未达成"**
  ——不要让它退回成一个"看起来能做、实际卡在同一处"的功能

**详细裁决**：[CHG-0026](../docs/contracts/changes/CHG-0026-reference-answer-conflict-semantics.md) ·
[CHG-0027](../docs/contracts/changes/CHG-0027-reason-code-enum.md) ·
[CHG-0028](../docs/contracts/changes/CHG-0028-draft-package-preconditions.md)

> **`CHG-0025`（草稿内容的读出口）不在本批**：它是**新增一个能力**，而本批三条都是"把已成立的事实写成声明"。
> **一批变更只做一类事**——这条规则就是为了避免 `§3.16` 那种"变更文件清单照计划写"的错误重演。

---

## 0.17.0 — 自助注册、邮箱验证码与注册白名单（CHG-0029）

- **级别**：P1 增补（三条新路径）；其中 409/422/429 的语义沿用 common.yaml 既有定义，不新增错误语义
- **状态**：已生效
- **动机**：用户要求账号“注册即可登录（无审核激活）”，注册界面强制收集 用户名 / 密码 / 姓名 / 学号 / 邮箱 / 验证码，
  并使用管理员导入的 姓名+学号 名单校验身份；**登录只保留用户名 + 密码**（现有 createSession 一字未改）。
  这三条路径是 B4 此前完全不存在的能力：没有注册端点、没有发信组件、没有平台级注册白名单。
- **变更文件**：
  - openapi/b4.yaml：新增 POST /api/v1/auth/registrations/code（
equestRegistrationCode）、
    POST /api/v1/auth/registrations（createRegistration）、
    POST /api/v1/registration-allowlists（
eplaceRegistrationAllowlist，管理员）、
    GET /api/v1/registration-allowlists（getRegistrationAllowlist，管理员）
  - 六份契约文件同步 info.version（含 syncapi/events.yaml）
- **受影响模块与需要做的改动**：
  - **B4**：新增迁移 20261020_0003（
egistration_allowlist、
egistration_allowlist_batches、
    
egistration_codes 三张表、users.created_via 列、users 的 lower(btrim(email)) 部分唯一索引）、
    发信模块与 SMTP 配置、三条端点与路由声明
  - **B1**：新增 /register 页、未登录强制跳转 /login（含回跳）、登录页返回原页
  - **B2 / B3 / B5**：**无改动**。登录请求体、令牌声明与 JWKS 形状均未变化，本地验签不受影响
- **是否破坏兼容**：否。全部为新增路径与新增表；createSession 的请求体仍是 {username, password}。
  唯一需要写明的“收紧”是：users.email 在数据库层获得**非 NULL 时唯一**的约束（大小写与首尾空格归一后），
  这会让“同一邮箱注册两个管理员账号”从可行变成 409
- **迁移方式**：一次性切换，不保留兼容层。若存量 users 中存在归一化后重复的邮箱，迁移会**明确失败**
  并要求先治理数据，而不是静默挑选一条
- **验证**：**未执行**。本机没有 uv/模块虚拟环境/PostgreSQL，也没有 B1 的 
ode_modules，
  因此 checks.py、
elock_contracts.py、
uff、pytest、pnpm * 都无法运行。
  摘要由 SDK 的 contract_digest()（MAIN/sdk/src/autograder_platform/contracts.py）直接计算后写入五份锁；
  该文件是 checks.py 委托的同一实现，但**仍应在有环境的机器上重跑** python MAIN/scripts/relock_contracts.py --check
  与 uv run pytest、pnpm verify
- **摘要**：见五份 contracts.lock（由 contract_digest() 计算，此处不复制）
- **回滚方式**：还原本批文本与版本号、回退迁移 20261020_0003、移除 B4 的注册路由与 B1 的注册页，
  然后重跑 scripts/relock_contracts.py；版本回到 0.16.0

### 0.17.0 修订 — 让 P14 管理员工作区可据契约实现（无 CHG 编号）

- **级别**：P2 语义（补全 `UserSummary`、`createUser` 响应体与 `deleteUser` 的一个错误码；
  不新增路径、不改请求体、不删除既有字段）
- **状态**：已生效
- **动机**：P14（`/admin/users`）的两个标签要求"展示账号状态并在停用后如实反映""多管理员并发
  改角色不静默覆盖""停用自己不被当成普通成功"。三条在 0.17.0 契约里都没有依据：
  ① `UserSummary` 没有 `is_active`，而 `deleteUser` 是**软停用**，于是"账号是否已停用"在契约里
  无处可读——页面只能把"点过按钮"当成停用成功，这与 `RQ-B-4-50` 要求的账号状态可见直接冲突；
  ② `UserSummary` 没有 `updated_at`，而 `updateUser` 的唯一并发保护是 `expected_updated_at`：
  客户端拿不到当前值，就只能省略它（并发编辑互相覆盖）或猜一个（永远 409）；
  ③ `deleteUser` 的 `204` 对"停用自己"是**不可报告**的——该请求本身会撤销发起者的会话。
- **变更文件**：`openapi/b4.yaml`
  - `UserSummary`：新增 `is_active`（必需）、`updated_at`（必需），并写明两者各自由谁产生、
    `updated_at` 与 `updateUser.expected_updated_at` 的对应关系；
  - `createUser`：`201` 增加响应体 `{user_id}`（B4 早已返回该值，契约此前无 schema）；
  - `deleteUser`：增加 `409`，语义为**拒绝停用调用者自己的账号**（防自锁），并把
    "停用 ≠ 物理删除""重新激活不在本契约"写进 operation description；
  - `listUsers`：`role` 查询参数的说明明确它匹配**平台**角色（课程作用域授权不算），
    并写明契约没有姓名/用户名搜索。
- **受影响模块与需要做的改动**：**B4** 在 `_user_document` 中返回 `is_active`/`updated_at`、
  角色筛选只匹配平台角色、`delete_user` 拒绝自停用并返回 `409`（有测试）；
  **B1** 生成类型重新生成后 `UserSummary` 多两个字段，P14 页面据此展示状态、发送
  `expected_updated_at`；`B2/B3/B5` 无改动（不消费 `UserSummary`）。
- **是否破坏兼容**：否（纯增量）。**但需写明**：`UserSummary` 的两个新字段是 `required`，
  任何自行构造该对象的生产者都必须补上它们；`deleteUser` 新增的 `409` 只出现在
  "管理员停用自己"这一条路径上，其他既有调用不受影响。
- **迁移方式**：一次性切换，不保留兼容层（无数据库结构变化，无迁移文件）。
- **验证**：见本批次提交说明；`pnpm api:check`、`pnpm typecheck`、相关 Vitest 与
  `python MAIN/scripts/relock_contracts.py --check` 的结果以**实际执行**为准，
  B4 的 ruff/pytest 需要 uv 环境与 PostgreSQL 容器，本机未具备。
- **摘要**：见五份 `contracts.lock`（由 `contract_digest()` 计算，此处不复制）
- **回滚方式**：还原上述四段文本，重跑 `scripts/relock_contracts.py`；模块版本仍为 0.17.0。

---

## 0.22.0 - 人工评分（老师亲自打分）与邮件正文可见

- **级别**：P1（新增 operation 与字段）
- **状态**：已生效
- **动机**：部分作业无法由执行判定（Unix 作业以邮件提交命令行，who/uname/ls -i 的输出依赖机器）。
  这类作业只能由教师判定，而此前分数只来自确定性评测，系统里没有"老师给分"的位置，也没有
  "待评分"状态——"交了但没评"于是被显示成"未交"。
- **新增 operation**：setManualGrade、
eleaseManualGrade、downloadSubmissionFile、
  getSubmissionIntakeMessage。
- **字段**：GradeRow.grade_status（deterministic|awaiting_manual|graded）、GradeRow.score_source。
- **原则**：人工分与机器分分表存储，带作者/时间/理由/历史；导出必须能回答"这个分谁给的"；
  waiting_manual 不计入未交；正文只留前 4000 字并脱敏。
- **影响模块**：B2（存储与写路径）、B1（待评分列表与打分表单）

## 0.21.0 — 合并两条 0.20.0 分支：P13 复核范围 + 名单导入 CSV

- **级别**：P1（**版本号冲突后的合并**，无新增字段）
- **状态**：已生效
- **动机**：两条分支各自以 `0.20.0` 发布了不同内容——P13 复核工作区的行级课程范围，与本次
  名单导入改为 CSV/XLSX——合并后的工作树是两者并集，摘要与任何一侧都不同。同一个版本号对应
  两份不同契约会让"锁摘要"失去意义，因此统一升为 `0.21.0`，内容为两侧并集。
- **内容**：`b2.yaml` 的复核隔离行级范围（成员五）+ `roster-import.schema.json` 与 `b4.yaml`
  的 CSV/XLSX 媒体类型（成员二）；其余文件仅版本号变化。
- **影响模块**：B1–B5 的锁与描述符，B1 生成类型

## 0.20.0 — 名单导入改为 CSV + XLSX（去掉 PDF）

- **级别**：P1 变更（媒体类型枚举收紧并替换）；无字段增删
- **状态**：已生效
- **动机**：名单来源是教务导出的 CSV/XLSX 表格，而 PDF 名单只能靠"两空格切列"猜测，猜错会把整列
  映射错，属于不可接受的风险。按产品要求：**接受 `text/csv` 与 XLSX，去掉 `application/pdf`**。
  CSV 没有容器签名，因此按 `.csv` 后缀识别、在解析时校验可解码（UTF-8/BOM → GB18030 回退）；
  XLSX 仍按 zip 签名识别。
- **影响模块**：B4（解析器与上传入口）、B1（上传控件接受 `.csv,.xlsx`）

## 0.19.0 — 作业单条读取、课序响应与创建响应成形（P04/P05/P12）

- **级别**：P1 增补（两处新增操作响应、一条新路径）；无字段删除，无兼容别名
- **状态**：已生效
- **动机**：作业详情页原先"取全部作业列表再按 id 本地查找"，一旦列表分页就得出错误结论；
  课序列表与课程/课序创建响应在契约里没有 schema，客户端只能靠候选键猜字段。三处缺口都由
  真实调用场景暴露，因此在本批次一并成形：
  1. `POST /api/v1/assignments` 补 `201` 响应体 `AssignmentPublic`（B3 早已返回该结构，契约只是没写）。
  2. 新增 `GET /api/v1/assignments/{assignment_id}`：`200 AssignmentPublic` / `401` / `403`（越权，
     不伪装成 `404`）/ `404`（不存在）。详情页据此单条读取。
  3. `b4.yaml` 以 `CoursePublic` 取代同义的 `CourseSummary`，并为
     `GET /courses/{course_id}/sections`、`POST /courses/{course_id}/sections`、`POST /courses`
     补 `SectionPublic` / `CoursePublic` 响应体。客户端不再读不透明行。
- **影响模块**：B3（新增 GET、201 响应）、B4（创建响应补全字段）、B1（生成类型与调用点）

## 0.18.0 — 结构化题目草稿、教师令牌夹具上传、学习提示上下文

- **级别**：P1 增补（新增操作与 schema；一处**收紧**并改正授权声明）
- **状态**：已生效
- **动机**：`RQ-T-10` 的扩展把"出题期辅助"从"给建议"扩到"按教学目标生成**结构化可编辑草稿**"，
  而这条能力此前在契约里没有承载入口；同时两个真实阻塞被记录：
  1. `POST /question-versions/{id}/fixtures` 被声明为 `internal` + `serviceToken`，而浏览器不可能
     持有服务令牌，于是 **file 题型在结构上无法从浏览器创建**（教师能选题型，却永远补不上夹具）；
  2. `AI-FLOW-01B` 需要 B2 在生成学生学习提示时读取**公开题目上下文**，而当时的契约里没有任何
     "只给 title/stem/language/tags/adapter" 的窄读，任何实现都只能复用会带出受保护内容的版本 DTO。
- **变更文件**：
  - `openapi/b3.yaml`：新增 `POST/GET/DELETE /api/v1/question-draft-generations*`、
    `GET /api/v1/question-draft-generations/capabilities`、
    `GET /api/v1/question-versions/{id}/learning-context`；`createQuestion` 增加可选查询参数
    `generation_id`；fixture 上传改为 `bearerAuth` + 课程写作用域。
  - `openapi/b3.yaml` 新 schema：`QuestionDraftGenerationRequest`、`QuestionDraftGeneration`、
    `QuestionDraftProposal`、`QuestionDraftProposalCase`、`QuestionDraftProposalFixture`、
    `QuestionDraftProposalAdapter`、`DraftGenerationWarning`、
    `QuestionDraftGenerationCapabilities`、`QuestionLearningContext`。
  - `openapi/common.yaml`：新增公共 `BadGateway`（`502`）响应——"上游答了但不可用"与
    "没有上游"（`503`）需要分开，否则配置缺失与模型乱答无法区分。
  - 其余契约文件的 `info.version` 同步到 `0.18.0`（`sync_contract_version.py`），五份
    `contracts.lock` 由 `relock_contracts.py` 重算。
- **受影响模块与需要做的改动**：
  - **B3**（生产者）：新增 `draft_generation`（模型边界与归一化）、`domain/draft_generations`
    （授权、预算、持久化、接受/丢弃）、`domain/catalog.load_learning_context`；迁移
    `20260915_0005`（`question_draft_generations` 受保护 + `ai_generation_usage` 去标识化账本）；
    fixture 上传先鉴权后处理负载；file 题型的"至少一个输入夹具"从 manifest 校验移到编译期。
  - **B1**（消费者）：重新生成 `src/api/generated/b3.ts`；P08/P09 增加生成面板、图片上传与夹具
    上传；P06/P07 按角色收口；`contracts.lock` 随版本更新。
  - **B2**（消费者）：学习提示改为调用 `learning-context` 取公开题目上下文（独立 schema，
    不含参考解/用例/期望输出）。
- **是否破坏兼容**：fixture 上传的**认证方式**变了（服务令牌 → 教师 bearer）。仓库内没有其他调用方
  （唯一消费者是 B1 出题页，本轮同步改）；**没有**保留双认证、代理或浏览器服务令牌。
- **迁移方式**：B3 `alembic upgrade head`；B1 重新生成类型。无数据迁移、不改已发布版本的冻结规则。
- **示例变更**：`POST /api/v1/questions?course_id=…&generation_id=…` —— 请求体仍是教师确认后的
  `QuestionDraftInput`，`generation_id` 只声明来源；B3 在同一事务里回填
  `accepted_question_version_id` 并清空候选正文。
- **验证**：`MAIN/host` 的契约/治理检查（`check_contract_parses_strictly`、
  `check_route_declarations`、`check_shared_vocabularies`、`check_contract_examples`、
  `check_requirement_baseline`、`check_document_encoding_and_links`）全部通过；
  `relock_contracts.py` 重算五份锁；B1 `api:check` 报告生成物与契约一致；B1 `tsc`/`eslint`/`vitest`
  全绿；B3 `ruff` 全绿，pytest 需 PostgreSQL（本地无实例，按任务书列为未验证）。
- **摘要**：见五份 `contracts.lock`（由 `relock_contracts.py` 计算，此处不复制）
- **回滚方式**：还原本批契约文本与版本号，回退 B3 迁移 `20260915_0005`，移除 B1 生成面板与
  B3 的四个新操作，然后重跑 `relock_contracts.py`；版本回到 `0.17.0`。

---

## 0.20.0 — 隔离区行级课程范围（P13 越权修复）与复核工作区（无 CHG 编号）

- **级别**：P1 增补 + P2 语义（新增两个响应字段与两个查询参数；把"按令牌不按行"改成行级授权）
- **状态**：已生效
- **动机**：P13 的强制验收门槛是"跨课程隔离真正成立"。现行契约在
  `listIntakeQuarantines` 的 description 里明确写着 **"Scoping is per token, not per row"**，
  而实现与测试也确实允许另一门课的教师读到本部署的全部隔离记录（`CHG-0021` 曾把它登记为
  缺口）。只要记录没有可验证的课程关联，"拥有任意课程权限"就等于"能读全局邮件隔离数据"。
  P13 需要把课程/课序变成可持久化、可查询的事实，并让 `resolve` 在写入前对**锁住的那一行**
  重新授权。题目质量侧同时需要一个可分享的、不要求教师手填 UUID 的版本入口（由 P07 带入
  `question_version_id`），因此本批也把该页面的筛选语义写进契约描述层。
- **变更文件**：`openapi/b2.yaml`
  - `IntakeQuarantineView`：新增**必需**的 `scope_course_id` / `scope_section_id`
    （`anyOf: [Uuid, null]`，成对为空或非空）。description 写明唯一来源是**匹配规则候选共识**，
    `not_matched`、跨课程/课序的 `ambiguous`、信封/附件拒绝一律 `null`，且不得从 mailbox、
    发件人、附件名或请求参数推测。
  - `listIntakeQuarantines`：新增可选 `course_id` / `section_id`（后者必须伴随前者）；
    删除"Scoping is per token, not per row"整段，改成行级语义（staff 只看被令牌作用域覆盖的行、
    未关联行只对管理员可见、query 只缩小范围且越界 403、SQL 谓词先于排序/limit/游标）；
    补 `422` 响应（实现对非法 `limit`/游标/`resolved`/`reason_code`/UUID 早已返回它）。
  - `resolveIntakeQuarantine`：description 增补"在行锁内按记录 scope 重新授权"；staff 对
    未关联/跨课记录 403；管理员可处置未关联行，其显式课程/课序选择只用于加载候选、不写回
    scope；并如实写明 B2 没有到 B4 名单的通道，因此不重跑名单成员关系校验。
  - 六份契约文件 `info.version` 同步到 `0.20.0`（`0.19.0` 已由 P04/P05/P12 的批次占用）。
- **受影响模块与需要做的改动**：
  - **B2**：迁移 `20260915_0015`（`intake_quarantines` 两列 + 成对 CHECK + 索引）；
    `intake/matching.py` 产出候选共识 `ScopeContext`；`intake/imap.py` 在同一事务持久化 scope；
    `intake/quarantine.py` 行级谓词与 `resolve` 行锁重查；`auth/authorization.py` 新增
    `require_quarantine_row_authority`；`EXPECTED_SCHEMA_REVISION`/`module.yaml` 推进到 0015；
    相关 pytest 改写（跨 scope 矩阵、管理员未关联行、查询越界 403）。
  - **B1**：重新生成类型；`/intake/quarantines` 与 `/quality` 合并为 `/review?tab=mail|quality`，
    删除旧页面/旧路由/旧导航入口；隔离列表增加课程/课序筛选、详情与安全归属（候选来自 B4 名单）；
    质量标签由 P07 带入 `question_version_id`，不再手填 UUID。
  - **B3/B4/B5**：无代码改动，仅同步版本声明（本批不新增跨模块端点）。
- **是否破坏兼容**：否（纯增量字段与参数）。需要写明两处**收紧**：① staff 不再能读未关联或
  跨课程的行（这是本批的目的，旧行为是已登记的缺口）；② `IntakeQuarantineView` 的两个新字段是
  `required`，任何自造该响应的生产者都必须补上（仓库内只有 B2 生产它）。
- **迁移方式**：一次性切换，不保留兼容层。历史隔离行没有、也无法证明课程归属，迁移**只加列**、
  把它们留成 NULL（保守：部署后仅管理员可见），不回填、不改写、不删除任何生产数据。
- **示例变更**：
  `GET /api/v1/intake/quarantines?resolved=false&course_id=…&section_id=…` →
  `{"items":[{"intake_quarantine_id":"…","reason_code":"ambiguous_match","scope_course_id":"…","scope_section_id":"…","resolved":false,…}],"next_cursor":null}`；
  未关联行 `{"scope_course_id":null,"scope_section_id":null}` 只出现在管理员响应里。
- **验证**：见本批提交说明与 `B2/docs/phases/active/phase-10-quarantine-row-scope.md`、
  `B1/docs/phases/active/phase-06-review-workspace.md` 的实际执行清单；本机无 Node/uv/PostgreSQL，
  因此 B2 pytest、B1 `tsc/eslint/vitest`、`pnpm api:check` 与真实迁移的 upgrade/downgrade 未执行，
  已逐条列出命令与风险。
- **摘要**：见五份 `contracts.lock`（由 `contract_digest()` 计算，此处不复制）
- **回滚方式**：还原本批契约文本与版本号、回退 B2 迁移 `20260915_0015`（会丢失 scope 关联）、
  移除 B1 的 `/review` 页面并恢复旧路由，然后重跑 `relock_contracts.py`；版本回到 `0.19.0`。

---

## 0.22.0 — 邮箱验证码找回密码（无 CHG 编号）

- **级别**：P1 增补（两个新的公开认证操作与一张 B4 表）
- **状态**：已生效
- **动机**：登录页此前**没有**找回密码入口，因为后端没有真实重置流程；用户一旦忘记密码，
  只能由管理员重建账号。本次补上完整闭环：注册邮箱 → 邮件验证码 → 设置新密码。两条新操作
  都不返回任何账号是否存在的信息，避免把"找回密码"变成账号枚举接口。
- **变更文件**：
  - `openapi/b4.yaml`：新增
    `POST /api/v1/auth/password-reset/code`（`requestPasswordResetCode`）与
    `POST /api/v1/auth/password-reset`（`completePasswordReset`）；
  - 六份契约文件 `info.version` 同步到 `0.22.0`（`0.21.0` 已被上游 P13/CSV 并集批次占用）。
- **语义**：
  - 发码 `202` 对任意合法邮箱都返回同一结构；只有属于**启用中账号**的邮箱才会真正收到邮件。
    TTL、重发窗口、小时上限复用注册邮件通道的既有配置；SMTP 未配置或投递失败为 `503`，
    且不留下可用验证码行。
  - 完成重置 `204`：消费验证码、替换 Argon2id 口令、撤销该账号全部 refresh 会话，
    并清理该账号的失败登录计数；已签发的 access token 只在剩余短 TTL 内仍有效。
  - 所有验证码失败（邮箱不存在、无可用码、错误、尝试次数耗尽）统一返回同一个 `422` detail。
  - 只保存绑定 `code_id` 的 SHA-256 摘要；明文验证码与口令不写数据库、日志或审计。
- **受影响模块与需要做的改动**：
  - **B4**：新增 `b4.password_reset_codes`（迁移 `20261027_0005`）、
    `POST /api/v1/auth/password-reset/code` / `POST /api/v1/auth/password-reset`、
    新邮件模板 `send_password_reset_code`、审计动作 `auth.password_reset.*`；
    `module.yaml` 迁移头推进到 `20261027_0005`。
  - **B1**：登录页增加「忘记密码？」入口与 `/forgot-password` 页面；注册 API 类型重新生成；
    登录密码框增加可持久显示的「显示/隐藏密码」按钮（修复失败后浏览器原生小眼睛消失）。
  - **B2/B3/B5**：无代码改动，仅同步契约版本声明与锁。
- **是否破坏兼容**：否（纯新增路径与表；既有认证、注册、会话契约不变）。
- **迁移方式**：新增表，一次性 `alembic upgrade head`；不修改既有行，不保留兼容层。
- **验证**：见本批提交说明与 `B1/docs/phases/active/phase-08-password-reset.md`；
  B4 的接口与迁移说明见 `B4/docs/phases/active/phase-04-password-reset.md`；
  本机无 PostgreSQL/NATS，B4 pytest 与真实迁移 upgrade/downgrade 未执行，已逐条列明；
  B1 的 `tsc` / ESLint / Vitest（46 文件 / 398 用例）/ `api:check` / `vite build` 与
  MAIN 契约/清单/文档门禁实际通过；Vercel 与 `Deploy AutoGrader backend` 对提交 `de4a0ee`
  均成功，线上公开发码接口对未知邮箱返回 `202`，B4 health 为 healthy。
- **摘要**：见五份 `contracts.lock`（由 `contract_digest()` 计算，此处不复制）
- **回滚方式**：还原本批契约文本与版本号、回退 B4 迁移 `20261027_0005`、移除 B1 的
  `/forgot-password` 页面与登录入口，然后重跑 `scripts/relock_contracts.py`；版本回到 `0.21.0`。
## 0.23.0 — 隔离区人工处置留痕与邮件摘要

- **级别**：P1 增补（新增 4 个响应字段与 1 个 operation；不改既有字段语义）
- **状态**：已生效
- **动机**：老师复核隔离邮件时只有邮箱/UID，认不出是哪封邮件；"驳回"没有出口，结构/附件类拒绝
  的记录永远留在待处理列表；处置后也没有"谁在何时处理"的留痕。此前的 `IntakeQuarantineView`
  只有 `resolved` 布尔、`resolved_student_id` 与 `resolution_note`，表达不了"已驳回"，也表达不了
  操作人与时间。
- **变更文件**：
  - `openapi/b2.yaml`：`IntakeQuarantineView` 增加 `subject`/`resolution`/`resolved_at`/`resolved_by`
    （均为 required，null 表示未记录）；新增 `POST /api/v1/intake/quarantines/{id}/dismiss`
    （`dismissIntakeQuarantine`）；`resolveIntakeQuarantine` 补充处置结果与留痕语义；列表
    `resolved` 描述改为"已处理（归属或驳回）/未处理"。
  - 六份契约 `info.version`、五份 `contracts.lock` 与模块清单同步到 `0.23.0`（`0.22.0` 已被找回密码批次占用）。
- **受影响模块与需要做的改动**：
  - **B2**：迁移 `20260916_0016`（四列 + 三个 CHECK，历史行保持空）；邮件入口记录主题头
    （可打印、折叠空白、≤200 字）；`resolve` 写 `attribution` 留痕；新增 `dismiss` 服务与路由
    （任何原因码都可驳回，行锁 + 行级授权 + 409 原子性保持不变）。
  - **B1**：生成类型重新生成；复核列表显示邮件摘要/失败原因/课程课序/状态/处理人+时间，
    操作列提供「归属学生」「驳回」；归属弹窗仍只提交 `student_id/note`，驳回提交 `note`。
  - **B3/B4/B5**：无行为改动，仅锁摘要与版本号随契约升级。
- **是否破坏兼容**：否（新增字段为 nullable、新增 operation；旧客户端忽略新字段仍可工作）。
- **迁移方式**：一次性加列/加约束；历史已处理行的处置结果与留痕为 `null`，界面显示"未记录"，
  不做回填与猜测。
- **示例变更**：
  `POST /api/v1/intake/quarantines/{id}/dismiss` `{"note":"附件是误报"}` →
  `{"intake_quarantine_id":"…","subject":"作业一提交","resolution":"dismissed","resolved":true,"resolved_at":"2026-10-27T08:00:00.000Z","resolved_by":"…","resolved_student_id":null,…}`。
- **验证**：B2 迁移/接口/邮件主题的 pytest 与 B1 `tsc/eslint/api:check/vitest/build` 见本次提交
  说明；真实 PostgreSQL 迁移 upgrade/downgrade 与 B2 API 行为在本机临时实例上执行。
- **摘要**：见五份 `contracts.lock`（由官方 `relock_contracts.py` 计算，此处不复制）。
- **回滚方式**：还原本批契约文本与版本号、回退 B2 迁移 `20260916_0016`（删除四列与三个约束，
  会丢失主题与处置留痕）、移除 B1 的驳回入口，然后重跑 `relock_contracts.py`；版本回到 `0.22.0`。

---

### 0.23.0 修订 — 新增 `activateUser`（无 CHG 编号）

- **级别**：P1 增补（新增一条管理员路径；不改既有请求体、响应体与错误语义）
- **状态**：已生效
- **动机**：P14 用户标签此前只能停用账号，停用后**没有恢复入口**：契约里 `deleteUser`
  是软停用，却没有任何"反向"操作，管理员误停用或学生复学后只能等下一次注册，页面也无法
  如实提供「启用账号」。本次补上完整闭环：停用 ⇄ 启用。
- **变更文件**：`openapi/b4.yaml`
  - 新增 `POST /api/v1/users/{user_id}/activate`（`activateUser`）：`204`；清空停用时间、
    `is_active` 恢复 `true`、`updated_at` 前进；不恢复已撤销的会话（需重新登录），
    不改历史成绩、角色与课程名单；
  - 启用**自己**返回 `409`，与 `deleteUser` 的自停用拒绝对称，避免单个管理员改自己的状态；
  - `deleteUser` 的说明删除"重新激活不在本契约"一句，改为指向 `activateUser`；
  - `UserSummary.is_active` 的说明补上两个方向的软状态变更。
- **受影响模块与需要做的改动**：**B4** 增加端点、`user.activated` 审计动作与测试；
  **B1** 重新生成类型，P14 用户行对已停用账号提供「启用账号」按钮与确认弹窗；
  **B2/B3/B5** 无改动（不消费 `UserSummary`，也不调用该端点）。
- **是否破坏兼容**：否（纯新增路径；`deleteUser` 的请求与响应未变）。
- **迁移方式**：一次性切换，不保留兼容层（无数据库结构变化，无迁移文件）。
- **验证**：`tsc`（B1）、ESLint（B1）、`pnpm api:check`、B1 全量 Vitest（48 文件 / 420 用例）、
  B4 `ruff`、`MAIN/scripts/relock_contracts.py --check` 与 `MAIN/host/tests`（7 用例）均通过；
  B4 `tests/test_users_courses.py` 在本机临时 PostgreSQL 17 实例上 **39 用例全部通过**
  （含新增的启用成功、启用自己 409、启用不存在用户 404 三条）。生产部署后的线上复核见提交说明。
- **摘要**：见五份 `contracts.lock`（由 `contract_digest()` 计算，此处不复制）
- **回滚方式**：删除 `activateUser` 段落、还原 `deleteUser` 与 `UserSummary` 的说明文字，
  移除 B1 的启用按钮与弹窗，然后重跑 `scripts/relock_contracts.py`；模块版本仍为 `0.23.0`。

---

## 记录模板（后续变更复制此块）

```md
## <版本> — <标题>（CHG-<序号>）

- 级别：P1 增补 / P2 语义 / P3 破坏
- 状态：提议 / 已生效 / 已回滚
- 动机：<要解决的真实问题；不接受"顺手改一下">
- 变更文件：<逐个列出>
- 受影响模块与需要做的改动：B1 … / B2 … / B3 … / B4 …
- 是否破坏兼容：是 / 否
- 迁移方式：<一次性切换；不保留兼容层>
- 示例变更：<前后对比>
- 验证：<执行了哪些命令、结果如何>
- 摘要：<由检查工具计算后填入 contracts.lock，不在此复制>
- 回滚方式：<具体步骤>
```
