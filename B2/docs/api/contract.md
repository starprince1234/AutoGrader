# B2 API 契约（我提供 / 我消费）

> 契约权威在 `../../../MAIN/contracts/`；版本与摘要见本仓库 `../../contracts.lock`。**本文件不手抄字段定义**，只写"我提供/我消费哪些端点与事件"。字段以 OpenAPI/JSON Schema 为准。

## 1. 我提供的端点

| 方法 + 路径 | 用途 | 认证 | 作用域要求 | 实现状态 |
|---|---|---|---|---|
| `POST /api/v1/submissions` | 接收 B1 提交的待测代码 | 是（JWT） | 课程作用域匹配 `course_id`；学生只能提交本人 | 已实现（`B2Module.router` 实测注册） |
| `GET /api/v1/submissions/{submission_id}` | 提交元数据与状态迁移史（不含源码内容） | 是（JWT） | 本人 / 课程作用域内的教师与助教 / 管理员 | 已实现（同上） |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/events` | SSE 实时进度 | 是（JWT） | 仅本人/授权角色可订阅 | 已实现（`tests/test_b2_routes.py` 断言 `text/event-stream` 与断线语义） |
| `GET/POST /api/v1/evaluation-runs/{evaluation_run_id}/commentary` | 读 / 生成教学评语（`RQ-T-20`）与顾问性参考分（`RQ-T-30`，同一次生成） | 是（JWT） | 读：同提交读权限；生成：**仅教师/助教/管理员** | 已实现（`tests/test_commentary_track.py`；见第 1b 节） |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/appeal-pack` | 导出一个运行的**可移动证据包**（`CHG-0014`） | 是（JWT） | 教师/助教/管理员，**外加本人运行的学生**；`view=teacher` 仅 staff | 已实现（`tests/test_b2_routes.py`；见第 1c 节） |
| `GET /api/v1/evaluation-runs/quality-summary` | 题目质量聚合（`CHG-0015`，**只有计数**） | 是（JWT） | 仅教师/助教/管理员 | 已实现（同上） |
| `GET /api/v1/intake/quarantines` | 隔离区列表（`CHG-0014`）：教师能重新找到"没能变成提交"的邮件 | 是（JWT） | 教师/助教/管理员；staff 只返回**记录 scope 被令牌作用域覆盖**的行（NULL scope 仅管理员可见） | 已实现（`tests/test_intake_api.py`；见第 1d 节） |
| `POST /api/v1/intake/quarantines/{intake_quarantine_id}/resolve` | 人工归属一条隔离记录（`CHG-0014`） | 是（JWT） | 同左；**行锁内**重查记录 scope：staff 对无 scope/跨课记录 403，管理员可处置未关联行 | 已实现（同上） |
| `POST /api/v1/reference-verifications` | 在沙箱执行参考解答并逐用例比对（`CHG-0019`；发布门禁的执行入口） | 是（JWT） | 教师/助教/管理员且令牌携带课程作用域（**按题目的课程判定做不到，见缺口 ⑦**） | 已实现（`tests/test_reference_verification.py`；见第 1f 节） |

补充（受 `module.yaml` routes 前缀 `/api/v1/evaluation-runs` 覆盖，契约已锁定，详见 `../../../MAIN/contracts/openapi/b2.yaml` 与 [../../../MAIN/docs/workstreams/B2.md](../../../MAIN/docs/workstreams/B2.md) 第 4 节）：

| 逻辑资源 | 路径（与 `MAIN/contracts/openapi/b2.yaml` 逐字一致） | 实现状态 |
|---|---|---|
| 评测运行详情 | `GET /api/v1/evaluation-runs/{evaluation_run_id}`（状态、进度） | 已实现（`tests/test_b2_routes.py`） |
| 评测报告查询 | `GET /api/v1/evaluation-runs/{evaluation_run_id}/report`（报告 DTO，受 `evaluation-report` schema 约束） | 已实现（同上；报告只在 `completed` 后存在） |
| 成绩查询 | 教师/学生按作用域查询（`GET /api/v1/grades`，**游标**分页） | 已实现（`tests/test_grades_query_export.py`、`tests/test_b2_routes.py`） |
| 成绩导出 | 按课程/课序/题目版本导出 XLSX/CSV（`GET /api/v1/grades/export`） | 已实现（同上；产物确定性见 [../phases/active/phase-01-implementation.md](../phases/active/phase-01-implementation.md) 第 9 条） |

> 上表四行此前均记 `待实现`，**已按实际状态刷新**（2026-09-16）：四个端点都由 `B2Module.router` 注册，
> 各有真实依赖测试（真实 PostgreSQL + 真实 JWT 验签 + 真实路由装配）。逐 RQ 追踪表的刷新见
> [../requirements/traceability.md](../requirements/traceability.md) 文首"状态列的读法"。
>
> **路径一致性核对（2026-09-16，本小类文档同步；2026-09-18 复核；2026-09-20 四条端点补齐）**：
> `MAIN/contracts/openapi/b2.yaml` 声明的路径集合与代码里 `B2Module.router` 实际注册的路径由
> `tests/test_b2_routes.py::test_registered_paths_match_the_contract` 逐条对账，**差集必须等于
> `_PENDING_CONTRACT_PATHS`**。CHG-0014/0015 的四条端点已于 2026-09-20 实现，因此该集合现在是
> **空集**（断言保留：契约一旦新增没人实现的端点，差集立刻不为空并失败）。差集因此永远显式且逐条列名，
> 不会退化成"实现是契约的子集就算过"。
> **修正**：本表此前把报告端点写成"报告 DTO…"而**没写路径**、详情端点写成 `{id}`
> （MAIN 与代码都是 `{evaluation_run_id}`），现已改为逐字一致。

## 1a. 成绩端点的契约侧缺口（已提 CHG，B2 未自行改契约、也未改实现迁就契约）

> **成绩两个端点的契约侧缺口**：
> ① `listGrades` 的 `description` 要求"指名他人 `student_id` 必须 403 而非静默收窄"，但 `parameters`
> 未声明 `student_id`（`exportGrades` 声称"同 listGrades 的过滤语义"，同样未声明）；
> ② 两个 operation 都未声明 `422`，而本模块按 422 实现（[第 4 节](#4-错误与标识约定跨模块统一不重复定义)
> 本就是 422 约定）；③ `/api/v1/grades` 未被 `module.yaml` 的 `routes` 覆盖；
> ④ `exportGrades` 声明了 `404`，但 B2 **不拥有课程**——"课程不存在"与"课程无提交"在 `b2` schema 内
> 不可区分，故实现从不返回 404（空结果 200 + 仅表头文件），且**不用 404 掩盖越权**（越权仍是 403）。
> 四条见 [../../../MAIN/docs/contracts/changes/CHG-0005-grades-endpoint-declarations.md](../../../MAIN/docs/contracts/changes/CHG-0005-grades-endpoint-declarations.md)。

> **本节标题此前重复了两次（第 40 行与第 51 行）**：第 40 行那个下面挂的是上面这段成绩端点的缺口说明，
> 与该标题无关。2026-09-20 拆成独立一节（`1a`），只改标题、不动任何内容。

## 1b. 教学评语轨（`RQ-T-20`，`CHG-0011` 新增）

| 方法 + 路径 | 语义 | 认证 / 角色 | 未配置 provider 时 | 实现状态 |
|---|---|---|---|---|
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/commentary` | 读教学评语（**非确定性**、不参与判分）与可空的参考分 | 是（JWT）；本人 / 课程作用域内的教师与助教 / 管理员可读 | `200` + `status: "unavailable"`（契约明文：这是配置状态，不是错误） | 已实现（`tests/test_commentary_track.py`） |
| `POST /api/v1/evaluation-runs/{evaluation_run_id}/commentary` | 生成或**重新生成**（覆盖；永不改变分数）评语**与参考分**（同一次生成的两个产物，`CHG-0018` §4 明确没有开关） | 是（JWT）；**仅教师/助教/管理员**——生成是编辑动作 | `409`，`detail` 指名缺哪些 `B2_COMMENTARY_*` | 已实现（同上） |

`POST` 的失败码分工（契约声明的集合之内）：provider 调用失败（网络/HTTP）、响应形状不符约定、
参考分越界，一律 `503` + 指名原因，并把该条评语落成 `status: failed`——
契约没有为"模型没按协议回答"声明第四个状态码，而这三者都属"一个必需依赖没能给出可用结果"。
`GET` 随后能读到 `failed`（连同原因），因此失败不是不可见的。

四条实现在代码里落地、由测试钉住的边界（`ADR-0009` 决策 2 的规范性要求）：

1. **输入是白名单**：只读 `b2` 表里字段级白名单的内容（逐用例结论与失败类别、静态规则命中、
   三态安全结论、分数构成、运行计数）。学生源码、参考答案、隐藏用例、期望输出**读不到**——
   它们所在的列根本不在查询里，且评语包的 import 闭包不含任何源码访问器（有测试）。
   参考分与评语**共用这一份白名单**（`ADR-0014` 决策 4：不因为它是分数而放宽）。
2. **永不进入分数轨**：契约里 `EvaluationCommentary` 没有平级 `score`、没有 `evidence`；
   实现里评语只写 `b2.evaluation_commentaries`，清空该表后重放同一提交的分数与逐项证据逐字相同。
3. **provenance 随响应返回**，含 `input_scope`（生成时**实际读取**的字段清单）——
   白名单因此可被外部审计，而不是只能相信。
4. **默认关闭**：不给任何 `B2_COMMENTARY_*` 时行为与"没有这条轨"完全一致，**且没有出网调用点**
   （不是"调用了但忽略"：provider 对象根本不存在）。

### 参考分（`RQ-T-30`，`CHG-0018` 新增；B2 侧只做生成与存储）

响应里 `advisory_score` 是**子对象**（`commentary.advisory_score.score`，可为 `null`），不是平级数字。
B2 侧的四条纪律（每条都有测试，见 [../testing/cases.md](../testing/cases.md) 的 T-ADV-*）：

1. **`max_score` 由 B2 从证据填入**（`b2.case_evidence.weight` 求和，与分数轨满分同源同值），
   **不由模型生成**。模型输出里出现同名键 ⇒ 整次生成 `failed`：实现里不存在读模型满分的分支。
2. **越界即失败，不截断**：`score < 0` 或 `score > max_score` ⇒ `failed`（含库级 CHECK，
   越界值根本写不进去）。截断会把"模型说了什么"悄悄改成"我们允许它说什么"。
3. **协议推进为 JSON 对象**（`prompt_version` 由 `b2-commentary/1` 升到 `b2-commentary/2`）：
   响应形状不符时 `failed`，**不退化**成"把整段响应当评语"。
4. **显式标注共用 `disclaimers`**：`disclaimers` 恒定含一条针对分数参考的说明，
   `advisory_score` 为 `null` 时**仍然存在**（`ADR-0014` 决策 5）。

参考分**不出现**在 `evaluation_results` / `case_evidence` / `result_digest` / 五项绑定摘要 /
`evaluation-report` schema / `/api/v1/grades` / `/api/v1/grades/export` / 任何事件 payload 里——
这一条在 `ADR-0014` 决策 3 里被列成六项可执行断言，在 B2 侧由
`tests/test_commentary_track.py` 的 T-ADV-001/002/003 覆盖（schema 拒收、成绩与导出零命中、
`advisory` 形状的列只存在于评语表）。

两处**契约声明了、B2 目前读不到**的证据面，已在
[../phases/active/phase-03-commentary-and-per-case-evidence.md](../phases/active/phase-03-commentary-and-per-case-evidence.md)
逐条上报：题目的公开元数据（语言/适配器类型/比较策略）B2 从不落库（故**不在** `input_scope` 里；
白名单是许可而非义务，少读严格更安全）；三态安全结论已由迁移 0011 补上落点。
`GET` 的 `404` 用于"运行存在但还没有生成过评语"（契约的状态枚举里没有"从未生成"这一态），
`detail` 明说这与"运行不存在"不是一回事。

## 1c. 申诉证据包与题目质量汇总（`CHG-0014` / `CHG-0015`，2026-09-20 新增）

| 方法 + 路径 | 语义 | 认证 / 角色 | 边界与取舍 |
|---|---|---|---|
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/appeal-pack` | 自包含的可移动证据包：报告 + 五项绑定摘要 + runner 策略摘要 + 可读重现说明，由 `result_digest` 钉住 | 是（JWT）；教师/助教/管理员，**外加本人运行的学生** | `view` 默认 `teacher`；学生索要 `view=teacher`（含**不传** `view`）一律 403，**不静默降级**——与报告端点同一纪律 |
| `GET /api/v1/evaluation-runs/quality-summary` | 一个题目版本的逐用例/逐规则质量聚合 | 是（JWT）；**仅教师/助教/管理员** | **只有计数**；`security_not_checked` 与 `security_not_found` 分开；`unmeasured_runs` 单列 |

两条端点的实现落点与不变量：

1. **证据包的两处强制脱敏都是主动做的**（`evaluation/appeal.py`）：①宿主根（工件根/工作区根，
   逐字前缀）与**绝对路径形态**的子串（Windows 盘符 / POSIX 根路径）替换成 `[host path removed]`，
   **沙箱内路径例外**（`/workspace/...` 不是宿主路径，保留）；②`view=student` 用报告端点的
   **同一个**裁剪函数 `report.for_student`，因此隐藏用例的期望/实际输出摘录与 `expected_sha256`
   都不会进包。`redactions` 列出"移除了什么、为什么"（至少一项）。
   组装前的自检：包内报告仍须过 `evaluation-report` schema，必填字段与 `result_digest` 形态逐项检查，
   失败即抛（**不**返回一份"看起来像证据包"的东西）。脱敏返回新对象：库里那份报告逐字节不变
   （有专门用例）。
2. **`result_digest` 是已持久化报告的摘要**，不是当场重算的：学生视图是完整报告的**子集**，
   若拿裁剪后的内容重算就会得到一个不同的摘要。教师视图下读者可用同一投影复算（有用例断言
   `report_digest(pack["report"]) == pack["result_digest"]`）。
3. **`runner_policy_summary` 的诚实边界**：具体数值限额随 package 声明而来，B2 只把
   `runner_policy_digest` 落库，**不落可打印的策略副本**。因此摘要报告的是"B2 拒绝放宽的那几个常量"
   （与 `runner.policy.RunnerPolicy` 的默认值同源，有测试对着策略对象断言，避免本地字面量漂移）
   + 那个摘要，并明说数值限额只能由摘要指认。摘要按构造不含宿主路径。
4. **质量汇总的口径**：范围 = `evaluation_runs.question_version_id` 且 `created_at ∈ [from, to]`
   （**两端都含**，契约没规定边界语义，口径写在这里）；`failure_categories` 与 `case_failure_rates`
   的单位是**用例**（来自 `case_evidence`）；`static_rule_hits` 以 B2 **自己的规则目录**逐条列出，
   **未命中的规则以 `count: 0` 出现**（否则"某条规则从未命中"永远发现不了）；
   `unmeasured_runs` = 报告里**没有任何沙箱回读峰值**（`memory_peak_bytes`/`cpu_*`/`pids_peak` 四者
   之一为数字）的运行数，`wall_seconds` 不算（那是 B2 自测的墙钟）；三态之和**完整划分**
   `attempt_count`（显式 `not_checked` 与"从未走到安全判定阶段"的 NULL 同属"我们没看过"）。

## 1d. 隔离区复核（`CHG-0014` 的两条 intake 端点；P13 补行级课程范围）

| 方法 + 路径 | 语义 | 认证 / 角色 | 边界与取舍 |
|---|---|---|---|
| `GET /api/v1/intake/quarantines` | 分页列出**调用者可见**的隔离记录（按记录时间倒序），可按 `course_id`/`section_id`/`reason_code`/`resolved` 过滤 | 是（JWT）；教师/助教/管理员，且 staff 令牌必须携带**至少一个**课程作用域 | 行级谓词先于排序/limit/游标；`resolved` 缺省**不过滤**（契约没给它 `default`，见缺口 ④）；`course_id/section_id` 只缩小范围，越界 403；`section_id` 必须伴随 `course_id`（422） |
| `POST .../{intake_quarantine_id}/resolve` | 人工归属；体 `{student_id, note?}`（`additionalProperties: false`） | 同上；行级判定在 `SELECT ... FOR UPDATE` 之后 | **只记录归属**：不建提交、不发事件、不评测；staff 对 NULL scope/跨课记录 403；管理员可处置未关联行（课程/课序选择只用于加载候选，不写回 scope） |

**行的课程范围从哪来（P13 的核心）**：`b2.intake_quarantines.scope_course_id/scope_section_id`
（迁移 `20260915_0015`，成对为空或非空）。写入侧只接受**匹配规则候选共识**——全部命中规则的
`(course_id, section_id)` 完全一致时记录该对值；`not_matched`、跨课程/课序的 `ambiguous`、以及
信封/附件拒绝一律 NULL。**不从 mailbox/发件人/附件名/请求参数推测**（那些是可伪造输入）。
历史行没有、也无法证明课程归属，迁移把它们留成 NULL：部署后只对平台管理员可见（保守收紧，
原始数据不动）。

1. **人工路径重跑哪些校验**（`intake/quarantine.py` 的模块文档逐条说明）：①角色与作用域；②**行级
   scope**（staff 只看被令牌覆盖的行；归属时在行锁内重查）；③**内容类闸门**——只有
   `reason_code ∈ {not_uniquely_matched, ambiguous_match}` 的记录可归属，其余一律 422
   并指名原因码（报文本身被拒的信封，命名一个学生修不好它）。**可归属原因码全集就是这两项**；
   其余全部原因码见 `intake.attachments.EmailIntakeReason`（`message_too_large`/`missing_message_id`/
   `no_attachments`/`too_many_attachments`/`attachment_name_not_allowed`/`duplicate_attachment_name`/
   `unsupported_attachment_encoding`/`malformed_attachment`/`attachment_too_large`/
   `attachment_total_too_large`/`executable_attachment`/`archive_bomb`/`archive_attachment`/
   `unsupported_attachment_type`/`not_plain_text`/`nested_message_attachment`/`malformed_message`）。
2. **归属不触发评测是结构性的**：`resolve()` 的全部写入只有一条 `UPDATE b2.intake_quarantines`；
   用例以"提交/outbox/评测运行三张表计数逐条为零"把它钉住。
3. **重复归属 ⇒ 409**（不覆盖前一个人的决定，也不静默返回旧值）；未知/非法标识 ⇒ 404；请求体违规 ⇒ 422。
4. **名单成员关系不在 B2 复核**：B2 没有到 B4 名单的通道（无客户端、无 serviceToken 端点），
   因此 resolve 不重查"该学生是否仍在该课序"。客户端每次从 B4 现取候选；服务端重查的是**记录行
   scope + 调用者角色/作用域**，而不是信任客户端提交的候选列表。这是本轮登记在案的残留缺口。

## 1e. 已上报的契约缺口（`CHG-0014`/`CHG-0015` 落地时发现，**B2 不自行裁决**）

| # | 缺口 | 事实 |
|---|---|---|
| ① | `resolveIntakeQuarantine` 要求"重跑与自动路径相同的信封/摘要校验" | 隔离记录**不存报文与附件字节、不存任何摘要**（平台有意不做第二个邮件仓库），因此 resolve 阶段**没有可重算的摘要**。要真正做到只能让 resolve 回邮箱重取报文，而契约给它的响应集合是 401/403/404/409/422——**没有 503**——反证它不依赖外部邮箱 |
| ② | 同上，"归属后交给正常提交路径评测" | 构造提交信封还需要 `course_id`/`section_id`/`question_version_id`，而自动路径正是从匹配规则取这三项；`not_matched` 时规则表没有给出任何身份。只给 `student_id` **不足以闭环**，且该 UID 的 checkpoint 已按 `quarantined` 落库、下次拉取会跳过。本模块**不发明**"归属即建提交"的路径 |
| ③ | 课程作用域（**隔离区已由 P13 修复；质量聚合仍只判角色**） | 隔离区已有可持久化的行级 scope（迁移 `0015`，来源是匹配规则候选共识），staff 的列表/归属都按行判定，`tests/test_intake_api.py` 用跨 scope 矩阵钉住。**质量汇总**仍只判"教师/助教/管理员"：`question_version_id → course_id` 是 B3 的事实，B2 不读 B3 schema；P13 的前端先用 B3 `GET /question-versions/{id}` 取标题/版本（B3 对无权版本 403），因此正常 UI 路径不会向 B2 拉无权版本的聚合，但**直接调用 B2 的 quality-summary 仍只受角色约束**——这是本轮登记、未跨模块新增通道的残留缺口 |
| ④ | `listIntakeQuarantines` 的 `resolved` 缺省语义 | 契约参数描述写"the default view a teacher wants"，但 schema **没有** `default`（同一份文件里 `Limit`/`view` 都显式写了默认值）。本实现取"缺省不过滤"，并把这条记为含糊项。P13 页面把 URL 的 `resolved=all` 翻译成"省略该参数"，默认视图显式发送 `false` |
| ⑤ | 未声明但被使用的状态码 | `quality-summary` 未声明 422（本模块按第 4 节统一约定对非法 `question_version_id`/`from`/`to` 返回 422，与 SSE 端点对坏 `Last-Event-ID` 的既有做法一致）；`quality-summary`/`exportGrades` 声明了 404，但 B2 **不拥有题目/课程**，"不存在"与"没有数据"在 `b2` schema 内不可区分，故实现从不返回 404；`appeal-pack`/`getEvaluationReport` 声明了 409 而实现用 404 + 如实的 `detail`（与报告端点记的 `G-API-1` 同一类） |
| ⑥ | 视图上限与库列上限不一致 | 契约 `IntakeQuarantineView.detail` 的 `maxLength` 是 **1000**（`CHG-0021` §2.4 已订正），库列 CHECK 也是 1000；服务层仍按 **500** 截断（`MAX_VIEW_DETAIL`），即视图比契约更保守。P13 未改动这一行为（不在授权范围内），此处更正旧表述 |

## 1f. 参考解答验证（`CHG-0019` + `CHG-0023`，2026-09-20 新增）

| 方法 + 路径 | 语义 | 认证 / 角色 | 依赖不可用时 | 实现状态 |
|---|---|---|---|---|
| `POST /api/v1/reference-verifications` | 在沙箱按该版本自己声明的适配器与比较策略执行**参考解答**、逐用例比对；**不落库**；全部通过才请 B3 记录存证 | 是（JWT）；课程作用域内的教师/助教/管理员（**不是** `serviceToken`） | `503` 且**零存证**（含"本平台没有沙箱"） | 已实现（`tests/test_reference_verification.py`） |

**它是 `ADR-0015` 的"B2 执行、B3 记录、教师一键触发"**：B2 取参考解答（`CHG-0023` 新增的 B3 内部端点）
与 evaluation package（既有端口），在 `runner.run_case` 里执行，然后用**已有的** B3 服务通道
（`B2_B3_AUTHORIZATION`，与取包同一个 `B3Endpoint` 对象）提交存证。**没有新增凭据、配置项或跨模块机制**。

五条实现级不变量（每条都有测试，见 [../testing/cases.md](../testing/cases.md) 的 T-REF-*）：

1. **不落库**：服务层连引擎都拿不到（结构性），一次全通过的验证跑完后 `b2` 全部业务表**零行**、
   outbox 零事件、工作区目录用完即删。存证是 B3 的记录，B2 不留第二份。
2. **未通过不产生存证**：只有 `cases_passed == cases_total` 才调 `POST .../attestations`；
   未通过时一次调用都不发（有断言），因此不会留下任何可被误读为"通过"的记录。
3. **记录失败 ≠ 报错**：B3 拒绝或不可达 ⇒ `200` + `attestation_id: null` + **逐用例结论照旧返回**
   （契约明文）。但上游声称 `201` 却给不出 `attestation_id` ⇒ `503`：`null` 的契约含义是"**没有**记录"，
   把"记了但读不回来"写成 `null` 是一句关于存证状态的假话。
4. **不返回受保护内容**：响应键集合就是契约声明的九个；参考解答正文、隐藏用例输入与期望输出
   一处都不出现。参考解答正文是**受保护内容**（`CHG-0023` §4），在 `ReferenceAnswerView` 里是
   `repr=False`，且 `reference_answer` 本就在 `logging.redaction.FORBIDDEN_FIELD_NAMES` 里。
5. **失败不被伪装**：沙箱不可用 ⇒ `503`（不返回伪成功，也不把环境问题写成"用例失败"）；
   上游 `404` ⇒ `404`；上游 `409` ⇒ `422`；其它上游故障 ⇒ `503`。
   内部一致性缺陷（例如 runner 给出"通过了却带失败类别"）**不归一**，冒泡成 500。
6. **`409` 的原因只转述、不自造**：`409` 说的是"版本状态让这次验证不成立"，**为什么**推不出来
   （B3 实测至少三类：草稿没有 evaluation package / 没有 `content_digest` / 该语言没有参考解答；
   取包侧还有"摘要与发布时记录不符"）。B2 把 B3 Problem Details 的 `detail` 原样转述，
   取不到时明说"上游没有给出原因"——**不得**说出 B3 没说过的原因：本模块曾经自造过
   "carries no reference answer"，而它对一个有参考解答的草稿是**假的**，会让教师去做一件错的事
   （真实端口发现的第四个缺陷，见 [../phases/active/phase-07-reference-verification.md](../phases/active/phase-07-reference-verification.md) §2d）。
7. **阻塞调用必须离开事件循环**：本端点会同步 HTTP 回调 `B2_B3_BASE_URL`，而在组合宿主里那**就是
   正在服务本请求的同一个进程**。在事件循环里直接调它会让宿主无法服务嵌套请求 ⇒ 自我等待到超时
   （真实端口实测修复前 `503 B3 request failed: TimeoutError`，耗时 10.03s）。实现用
   `run_in_threadpool` 下线，理由与取舍写在 handler 的注释里（同前，§2c）。

### 两处已上报的缺口（**B2 不自行裁决**，属 `1e` 同一类）

| # | 缺口 | 事实 |
|---|---|---|
| ⑦ | 契约要求"该题目的**课程写权限**（与 `publish` 同一套判定）" | "题目→课程"是 B3 的事实（`b3.question.course_id`）。请求里只有 `question_version_id`，而本端点调 B3 用的是**服务令牌**（B3 因此不知道是哪位教师）。B2 不读 B3 schema、也不复制一份映射，因此**第三层判定做不了**：当前只判到"staff + 令牌携带至少一个课程作用域"。另一门课的教师**会被放行**，由 `tests/test_reference_verification.py::test_course_scope_gap_is_recorded_not_hidden` 钉成可执行的记录（与缺口 ③ 同一处） |
| ⑧ | `CHG-0019` §7.2/7.3 的"验证草稿 → 存证 → `publish` 成功" | B3 的 `GET .../evaluation-package` 对**草稿**返回 `409`（`B3/domain/packages.py` 只在版本已发布时编译包，且 `load_published_snapshot` 要求 `published_at`），而 B3 的 `recordReferenceAttestation` **只接受草稿**（`status != draft` 即 409/422）。两者相遇时"验证草稿"拿不到用例与比较策略 ⇒ **发布门禁的出题闭环在当前 B3 上仍不成立**。本实现把草稿的 `409` 如实映射成 `422` 并给出指明原因的 `detail`，**不**假装成功；修复属 B3 侧（让草稿也能取到执行输入），已上报 |


## 2. 我消费的端点 / 事件

| 提供方 | 我消费什么 | 用途 |
|---|---|---|
| B3 | `GET /api/v1/question-versions/{id}/evaluation-package`（内部接口） | 获取不可变 evaluation package（runner 核心依赖此结构） |
| B3 | 事件 `autograder.v1.question.version.published` | 缓存失效 / 题目可用性 |
| B4 | JWT/JWKS（`iss`/`aud`/`role`/课程作用域） | 本地验签与授权判定 |

## 3. 权威链接

- OpenAPI：`../../../MAIN/contracts/openapi/b2.yaml`
- Schema：`submission-envelope`、`evaluation-package`、`evaluation-report`、`event-envelope`（`../../../MAIN/contracts/schemas/`）
- 事件：`../../../MAIN/contracts/asyncapi/events.yaml`
- 契约锁：`../../contracts.lock`
- 变更规程：`../../../MAIN/docs/contracts/change-protocol.md`

## 4. 错误与标识约定（跨模块统一，不重复定义）

- 错误格式：RFC 9457 Problem Details。
- 时间：UTC RFC 3339（毫秒精度）。
- 标识：UUID v4（由 owner 模块生成，见 [../../../MAIN/docs/api/identifiers.md](../../../MAIN/docs/api/identifiers.md)）。
- 关联：传播 `traceparent` 与 `X-Request-ID`；未携带时入口生成并贯穿日志/事件（作为 `correlation_id`）。
- 错误码语义：未认证 `401`；无权限 `403`（**不用** `404` 掩盖）；有权限但资源不存在 `404`；校验失败 `422`；超大小 `413`。

## 5. 当前实现状态说明（避免与代码不符的声称）

- `router()` **已注册全部契约端点**：提交接收与查询（`tests/test_intake_api.py`、`tests/test_submission_query.py`）、
  SSE 与评测运行/报告（`tests/test_b2_routes.py`）、成绩查询与导出（`tests/test_grades_query_export.py`、
  `tests/test_b2_routes.py`）、隔离区复核两条与 `appeal-pack`/`quality-summary`（2026-09-20 补齐，
  `tests/test_intake_api.py` + `tests/test_b2_routes.py`，差集已归零）。`module.yaml` 的 `routes`
  只覆盖了前四组前缀，成绩前缀的归属声明缺口见上方说明（已提 CHG-0005）。
- `health()` 不再返回占位文案：它检查**配置完整性 + 数据库连通性 + 库结构版本与代码期望一致**
  （`intake/runtime.py`），库未迁移或版本不符时如实报告不可用。
- 其余端点（SSE、报告、成绩查询与导出、隔离区、证据包、质量汇总）此前记为"仍为契约声明、尚未注册"，
  **该陈述已过期**：它们已注册并有真实依赖测试（见第 1 节状态列与
  [../phases/active/phase-05-quarantine-and-quality-surfaces.md](../phases/active/phase-05-quarantine-and-quality-surfaces.md)）。
  任何"已可用"的声称都必须有对应契约测试与阶段证据。需求级状态见 [../requirements/traceability.md](../requirements/traceability.md)。
