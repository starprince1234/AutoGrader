# B1 接口契约冻结清单

## 与 MAIN

- 我提供：`web-ui`（静态资产，Vercel 在 `www.91idc.me/app/` 托管；部署拓扑见 MAIN ADR-0017）。
- 我消费：`../../../MAIN/contracts/openapi/*.yaml` 中 B4/B3/B2 的端点（关系见 `../api/contract.md`）。
- 锁定：`../../contracts.lock`（版本 `0.16.0`，摘要 sha256，见该文件）。
- `module.yaml` 的 `contract_version` 同为 `0.16.0`（由 MAIN 同步）。
- 摘要漂移时：**不**手改 lock，按 `../../../MAIN/docs/contracts/change-protocol.md` 由 MAIN 重算并同步。

## 与每个邻居模块

| 邻居 | 我依赖它什么 | 它依赖我什么 | 失败时的行为 |
|---|---|---|---|
| B4 | 会话/刷新/退出、JWKS、课程/课序/选课、名单导入（预览+确认） | 无（B1 不提供服务） | 界面显示鉴权/加载失败状态，**不吞错误**，前端可见性降级但不越权 |
| B3 | 已发布题目、版本、作业集合、题干资源、**题库标签覆盖度（CHG-0020）**；**出题写路径**（新建/修改草稿）、确定性诊断、出题辅助 | 无 | 题目页显示加载失败/重试；写路径失败原样展示 4xx/5xx；不渲染参考答案/隐藏用例；辅助端点 `409` 显示"未启用"；覆盖度端点未实现（`404`）时显示"尚未在 B3 实现"的信息态，**不**回退到用列表在客户端数题 |
| B2 | 提交受理、评测运行与 SSE、成绩查询、导出、**逐用例证据（含 CHG-0012 的资源/文件/说明）**、**教学评语轨与 AI 参考分（RQ-T-20/T-30）**、**隔离区读写与证据包（CHG-0014）**、**题目质量聚合（CHG-0015）**、**参考解答验证（CHG-0019，发布门禁的触发路径）** | 无 | 提交失败明确提示；SSE 断线重连并按 event_id/sequence 去重；未完成评测显示状态；证据字段缺失显示"未测量/无数据"而不是 0；评语未配置 provider 显示"未启用"、未生成显示"还没生成"（**都不是错误**）；隔离区/证据包/质量三个端点未实现时原样展示服务端错误，**不**用夹具冒充；验证端点 `503`（无沙箱）与逐用例 `infrastructure_error` 都显示"验证需要 Linux 沙箱环境"，**不**写成"验证失败"也**不**伪装成功 |

## 接口契约冻结清单

> **状态语义**：`已冻结` = 路径、`operationId` 与请求/响应（或 SSE 帧）结构已在 `../../../MAIN/contracts/**` 中成文，B1 可据此实现；**不等于**提供方已实现。
> **复核基线**：`contract_version = 0.16.0`（摘要 `372cfcab…`，见 `../../contracts.lock`）。复核对象：`MAIN/contracts/openapi/{common,b2,b3,b4}.yaml` 与 `MAIN/contracts/asyncapi/events.yaml`。
> **最近一次复核**：契约 → `0.16.0`（`CHG-0026` 参考解答的 `409` 语义、`CHG-0027` `reason_code` 的
> 19 项 `enum`、`CHG-0028` 评测包与存证对**草稿**开放；`CHG-0025` 的 `draft-input` 读出口未随批落地）。
> 上一批 `0.15.0` 的构成见下方"版本轨迹"。
> 本批 B1 新消费**一个**端点：`verifyReferenceAnswer`（`POST /api/v1/reference-verifications`），
> 并**首次调用** `publishQuestionVersion`——它此前对 B1 不可用（要求与 `content_digest` 绑定的存证），
> 这正是 `ADR-0015` 解掉的死结。`CHG-0023` 的 `GET /question-versions/{id}/reference-answer` 是
> `serviceToken` 内部端点，**B1 永不消费**；`CHG-0024` 对 B1 **无代码改动**（生产代码从不读 `published_at`）；
> `CHG-0027` 的 `enum` 落地后 B1 的 `ReasonCode`（从生成类型派生）**已经变成字面量联合类型**，
> 词表核对因此回到编译期——**不再复制字段表**。
> 更早的 0.8.0–0.14.0 依次带来 CHG-0008/0012/0013（写路径、逐用例证据、`revision_note`/`tags`/诊断）、
> CHG-0014/0015（隔离区与证据包、质量热力图）、CHG-0016（`PATCH` 响应类型修正）、CHG-0017、CHG-0020（覆盖度）。
> 结论与证据见 [`../phases/active/phase-05-authoring-publish-path-and-0-15-0.md`](../phases/active/phase-05-authoring-publish-path-and-0-15-0.md)、
> [`../phases/active/phase-04-question-tag-coverage.md`](../phases/active/phase-04-question-tag-coverage.md)、
> [`../phases/active/phase-03-contract-surface-completion.md`](../phases/active/phase-03-contract-surface-completion.md)
> 与 [`../phases/active/phase-02-apple-redesign-and-e2e.md`](../phases/active/phase-02-apple-redesign-and-e2e.md) §10/§11。

### CHG-0016：响应形状修正（对 B1 的影响）

| 路径 | 修正前（0.9.0–0.10.0 声明） | 修正后（0.11.0） | B1 的处置 |
|---|---|---|---|
| `PATCH /api/v1/questions/{question_id}` | `QuestionVersionPublic` | `QuestionPublic` | B1 此前按版本 DTO 读响应，`question_version_id`/`version` 两个**审计字段在真实运行中恒为 `undefined`**（类型来自错的声明，编译器发现不了）。改为从 `versions[0]` 取真值，并新增断言真值的回归用例（TC-B1-063，已做变异验证） |

> **教训（写进 B1 的规则）**：契约是字段定义的唯一权威，因此**契约声明错了会把编译期错误变成运行时错误**。
> 防御只有两条：每个 `apiRequest<T>` 的泛型写的时候对着契约核一遍；凡是进入日志/审计的字段都要有
> "它是真值"的断言。更早一批已按这两条把 `src/` 下 26 处调用点全量复核一遍（只有 PATCH 那处不匹配，已修）；
> 上一批新增的 5 处调用点逐个对着契约的响应声明核过——其中 `/commentary` 的两处**真机跑通**，
> 另外三处（隔离区/证据包/质量聚合）因 B2 未实现，只能核到生成类型这一层；
> **本批新增 1 处**（`getQuestionTagCoverage`）同样对着响应声明核过，B3 未实现，只能核到生成类型这一层。

| 接口名 | Owner | 当前状态 | 契约出处（可核对） |
|---|---|---|---|
| `POST /api/v1/auth/session`（`createSession`） | B4 | 已冻结 | `openapi/b4.yaml` → `paths./api/v1/auth/session.post` |
| `POST /api/v1/auth/session/refresh`（`refreshSession`） | B4 | 已冻结 | `openapi/b4.yaml` 同名路径 |
| `DELETE /api/v1/auth/session/logout`（`deleteSession`） | B4 | 已冻结 | `openapi/b4.yaml` 同名路径 |
| `GET /api/v1/auth/jwks`（`getJwks`） | B4 | 已冻结 | `openapi/b4.yaml` 同名路径 |
| `GET /api/v1/courses`（`listCourses`） | B4 | 已冻结 | `openapi/b4.yaml`（`CourseSummary`） |
| `GET /api/v1/courses/{course_id}/sections`（`listSections`） | B4 | 已冻结（**响应无 schema**，见"已知契约缺口"第 3 条） | `openapi/b4.yaml` 同名路径 |
| `GET /api/v1/sections/{section_id}/enrollments`（`listEnrollments`） | B4 | 已冻结（响应 schema `EnrollmentPublic`，CHG-0007） | `openapi/b4.yaml` 同名路径 |
| `PATCH /api/v1/enrollments/{enrollment_id}`（`updateEnrollment`） | B4 | 已冻结 | `openapi/b4.yaml`（请求体含 `expected_updated_at`） |
| `DELETE /api/v1/enrollments/{enrollment_id}`（`deleteEnrollment`） | B4 | 已冻结 | `openapi/b4.yaml` 同名路径 |
| `POST /api/v1/roster-imports`（`createRosterImport`） | B4 | 已冻结 | `openapi/b4.yaml`（`RosterImportPreview`） |
| `GET /api/v1/roster-imports/{id}`（`getRosterImport`） | B4 | 已冻结 | `openapi/b4.yaml`（`RosterImportPreview`） |
| `PUT /api/v1/roster-imports/{id}/mapping`（`setRosterColumnMapping`） | B4 | 已冻结 | `openapi/b4.yaml` 同名路径 |
| `POST /api/v1/roster-imports/{id}/confirm`（`confirmRosterImport`） | B4 | 已冻结 | `openapi/b4.yaml`（`Idempotency-Key` 头） |
| `GET /api/v1/users`、`POST /api/v1/users`、`PATCH/DELETE /api/v1/users/{user_id}` | B4 | 已冻结 | `openapi/b4.yaml`（`UserSummary`、`Role` 枚举） |
| `GET /api/v1/questions`、`/questions/{id}`（公开 DTO，含 `versions` 版本历史） | B3 | 已冻结 | `openapi/b3.yaml`（`QuestionPublic`、`QuestionVersionSummary`） |
| `POST /api/v1/questions`（`createQuestion`，`course_id` 为必需查询参数） | B3 | 已冻结 | `openapi/b3.yaml`（`QuestionDraftInput` → `QuestionPublic`） |
| `PATCH /api/v1/questions/{id}`（`updateQuestionDraft`，`as_new_version`） | B3 | 已冻结（响应为 `QuestionPublic`，**CHG-0016 修正**） | `openapi/b3.yaml` 同名路径 |
| `POST /api/v1/questions/{id}/authoring-assistance`（`requestAuthoringAssistance`） | B3 | 已冻结（`409` = 未配置 provider） | `openapi/b3.yaml`（`AuthoringAssistance`，`confidence: advisory`） |
| `GET /api/v1/questions/{id}/diagnostics`（`getQuestionDiagnostics`） | B3 | 已冻结（空列表 = 检查过无问题） | `openapi/b3.yaml`（`ComparisonDiagnostics`，`CMP-*`） |
| `GET /api/v1/question-versions/{id}`（公开 DTO，含该版本自己的题干与 `revision_diff`） | B3 | 已冻结 | `openapi/b3.yaml`（`QuestionVersionPublic` 的 `stem_markdown`/`stem_assets`/`revision_note`/`revision_diff`） |
| `GET /api/v1/assignments`（`listAssignments`） | B3 | 已冻结（响应 schema `AssignmentPublic`，CHG-0007） | `openapi/b3.yaml` 同名路径 |
| `GET /api/v1/questions/coverage`（`getQuestionTagCoverage`） | B3 | 已冻结，**已实现**（真实路由 + 真实数据；参数 `course_id` 必需 + `published_only` 默认 `true`；响应 `QuestionTagCoverage` 只有计数与标签；`tags[].tag` 的形态约束已由 CHG-0022 补齐） | `openapi/b3.yaml` → `paths./api/v1/questions/coverage.get`（注意它声明在 `/questions/{question_id}` **之前**） |
| `POST /api/v1/reference-verifications`（`verifyReferenceAnswer`） | B2 | 已冻结，**已实现**（真实浏览器里对草稿返回 `422`；体只有 `question_version_id`；`200` = 逐用例结论、`503` = 依赖不可用且不记录任何东西） | `openapi/b2.yaml`（`ReferenceVerification`，CHG-0019；`ADR-0015`） |
| `POST /api/v1/question-versions/{id}/publish`（`publishQuestionVersion`） | B3 | 已冻结（**B1 本批首次调用**：此前没有可用的存证路径，见 `phase-02 §11` 与 `phase-05`） | `openapi/b3.yaml` 同名路径（`CHG-0008` 的门禁在服务端） |
| `POST /api/v1/submissions`（`createSubmission`） | B2 | 已冻结 | `openapi/b2.yaml` + `schemas/submission-envelope.schema.json` |
| `GET /api/v1/submissions/{id}`（`getSubmission`） | B2 | 已冻结 | `openapi/b2.yaml`（`SubmissionView`） |
| `GET /api/v1/evaluation-runs/{id}`（`getEvaluationRun`） | B2 | 已冻结 | `openapi/b2.yaml`（`EvaluationRunView`） |
| `GET /api/v1/evaluation-runs/{id}/events`（SSE，`streamEvaluationEvents`） | B2 | 已冻结 | `openapi/b2.yaml`（`EvaluationStreamFrame`） |
| `GET /api/v1/evaluation-runs/{id}/report`（`getEvaluationReport`） | B2 | 已冻结 | `openapi/b2.yaml` + `schemas/evaluation-report.schema.json` |
| `GET /api/v1/grades`（`listGrades`） | B2 | 已冻结 | `openapi/b2.yaml`（`CursorPage` + `GradeRow`） |
| `GET /api/v1/grades/export`（`exportGrades`） | B2 | 已冻结 | `openapi/b2.yaml`（`xlsx`/`csv` 二进制响应） |
| `GET`/`POST /api/v1/evaluation-runs/{id}/commentary`（`getEvaluationCommentary`/`requestEvaluationCommentary`） | B2 | 已冻结（**B2 已实现**；`GET` 未生成时返回 `404`，语义见下） | `openapi/b2.yaml`（`EvaluationCommentary`、`AdvisoryScore`） |
| `GET /api/v1/intake/quarantines`、`POST .../{id}/resolve`（`listIntakeQuarantines`/`resolveIntakeQuarantine`） | B2 | 已冻结，**已实现**（真实浏览器渲染出空列表） | `openapi/b2.yaml`（`IntakeQuarantineView`） |
| `GET /api/v1/evaluation-runs/{id}/appeal-pack`（`getAppealPack`） | B2 | 已冻结，**已实现**（路由探测 `401`；面板本轮未在浏览器里打开） | `openapi/b2.yaml`（`AppealPack`） |
| `GET /api/v1/evaluation-runs/quality-summary`（`getEvaluationQualitySummary`） | B2 | 已冻结，**已实现**（真实浏览器渲染出筛选面板） | `openapi/b2.yaml`（`EvaluationQualitySummary`） |
| `GET /api/v1/question-versions/{id}/evaluation-package`、`/fixtures`、`/attestations` | B3 | 已冻结，但**B1 永不消费**（`tags: [internal]` + `serviceToken`：内含参考答案/夹具字节，attestation 存在的意义就是执行者与记录者分离） | `openapi/b3.yaml` → `tags: [internal]` |
| 事件 `autograder.v1.evaluation.progress` / `autograder.v1.evaluation.completed` | B2 | subject 已冻结 | `asyncapi/events.yaml` → `channels.evaluationProgress` / `channels.evaluationCompleted` |

> 状态词只有两个：`已冻结` / `未冻结`。**上表当前无任何"未冻结"项**，B1 三条泳道均无"等待冻结"的阻塞。

### CHG-0014 / CHG-0015 / CHG-0019 / CHG-0020 指派给 B1 的界面（均已实现消费）

> **复核基线**：`contract_version = 0.16.0`（摘要 `372cfcab…`，见 `../../contracts.lock`）。复核对象：
> `MAIN/contracts/openapi/{common,b2,b3,b4}.yaml` 与 `MAIN/contracts/asyncapi/events.yaml`。
> **版本轨迹**：`0.9.0` → `0.10.0`（CHG-0014/0015）→ `0.11.0`（CHG-0016 修正
> `PATCH /api/v1/questions/{id}` 的响应类型为 `QuestionPublic`）→ … → `0.13.0`
> （CHG-0018 引入 `EvaluationCommentary.advisory_score`，即 `RQ-T-30`）→ `0.14.0`
> （CHG-0020 引入 `getQuestionTagCoverage`，即题库标签覆盖度）→ **`0.15.0`**
> （CHG-0019/0023 参考解答验证与它的交付通道、CHG-0017 草稿省略语义、CHG-0021 隔离区语义修复、
> CHG-0022 标签形态、CHG-0024 草稿 `published_at` 可空；批内摘要两度变动，版本号始终 `0.15.0`）→
> **`0.16.0`**（`CHG-0026`/`CHG-0027`/`CHG-0028`；摘要 `372cfcab…`）。
> 结论与证据见 [`../phases/active/phase-05-authoring-publish-path-and-0-15-0.md`](../phases/active/phase-05-authoring-publish-path-and-0-15-0.md)、
> [`../phases/active/phase-03-contract-surface-completion.md`](../phases/active/phase-03-contract-surface-completion.md)
> 与 [`../phases/active/phase-04-question-tag-coverage.md`](../phases/active/phase-04-question-tag-coverage.md)。

| 端点 | 契约出处 | B1 状态 | 验证到哪为止 |
|---|---|---|---|
| `GET`/`POST /api/v1/evaluation-runs/{id}/commentary` | `openapi/b2.yaml` | **已消费**（`CommentarySection` + `useCommentary`） | **已真实联通**：真机 `GET`→200/`ready`（deepseek 真实评语 + 2 条 disclaimers）、未生成→`404`（渲染为信息态）、`POST`→`202`（5882 ms；`0.16.0` 复核 7497 ms）。**`advisory_score` 键已出现、值为 `null`** ⇒ "null 不渲染 + 说明本次没有产生"已真实联通。**未覆盖**：`unavailable` 态（本部署配置了 provider）、学生视角、**非空 `advisory_score`** |
| `GET /api/v1/intake/quarantines`、`POST .../{id}/resolve` | `openapi/b2.yaml` | **已消费**（`QuarantinePage` + `useQuarantines`） | **读路径已真实联通**（2026-09-20 `0.16.0` 复核）：`GET ...?resolved=false&limit=5` → **200**，`{"items":[],"next_cursor":null}`，页面渲染"当前筛选下没有隔离记录"（空列表，不是错误）；界面不再有任何"B2 尚未实现"的常驻断言。**带数据的**渲染与归属写路径仍只有组件测试证据（库里没有隔离记录）。`resolved` 显式发送与"不可归属不显示按钮"按 `CHG-0021` 实现 |
| `GET /api/v1/evaluation-runs/{id}/appeal-pack` | `openapi/b2.yaml` | **已消费**（`AppealPackPanel` + `useAppealPack`） | **端点已实现，200 路径未触达**：真机 `GET ...?view=teacher` → **404**（detail：`A report exists only after a successful run.`，本部署所有运行都是 `failed`）。该面板在报告缺失时不渲染，因此浏览器里也未打开；证据为组件测试 |
| `GET /api/v1/evaluation-runs/quality-summary` | `openapi/b2.yaml` | **已消费**（`QualitySummaryPage` + `useQualitySummary`） | **已真实联通**（2026-09-20 `0.16.0` 复核）：真机 `GET ...?question_version_id=34d07765-…` → **200**，`attempt_count=0`、`static_rule_hits` **15 条全为 `count=0`**（冻结目录逐条给出，契约"surface never-firing rules"的落地）、`security_*` 三项皆 0；浏览器里逐条核对。**借这次复核修正了静态规则那一段的文案**：原先写"只含命中过的规则／拿不到规则全集"，与它正在渲染的 15 条 0 命中记录直接矛盾（详见 `../phases/active/phase-03-contract-surface-completion.md` 的追加节）。**非空数据分支**仍只有夹具证据 |
| `GET /api/v1/questions/coverage` | `openapi/b3.yaml` | **已消费**（`QuestionCoveragePanel` + `useQuestionCoverage`，挂在题目列表页） | **已真实联通（真实浏览器 + 真实 B3）**：课程 `CS-OS-2026` 上 `total=102 / tagged=4 / untagged=98`，逐标签 `file-systems=3 / virtual-memory=3 / unix=1`，恒等式行 `4 + 98 = 102`；与 MAIN 的独立 SQL 交叉核对一致，且同数并列按 `tag` 升序（前端未重排）。`404` 分支改为**数据驱动**：只有服务端真的返回 404 时才显示"本部署取不到该端点"，且不再断言任何模块的实现进度 |
| `POST /api/v1/reference-verifications` + `POST /api/v1/question-versions/{id}/publish` | `openapi/b2.yaml` + `openapi/b3.yaml` | **已消费**（`ReferenceVerificationPanel` + `useVerifyReferenceAnswer`/`usePublishQuestionVersion`） | **端点已可用，但整链在真实环境里走不通（新缺口 12）**：真实浏览器里对一道**草稿**点击"请求平台验证参考答案"，B2 返回 **`422`** + detail「the version has no evaluation package yet (B3 compiles one only for a published version), so its reference answer cannot be verified against the cases」；而**已发布**版本按设计不再渲染该面板。因此"先验证再发布"这条链在草稿上被 `422` 挡住、在已发布版本上没有入口。面板对该 `422` 的做法是**原样展示服务端 detail + request_id**（没有改写成任何结论）。三态与"全通过 ≠ 可发布（`attestation_id: null`）"仍有夹具断言 |

> **真机发现、需要 MAIN 表态的两处**（详见 phase-03 的"契约歧义"）：
> 1. `GET /commentary` 用 `404` 表示"该运行还没有评语"（detail 原文：
>    "This is not the same as a missing run."）——契约只在 `responses` 里列了 `404` 而未描述语义，
>    也没写它与 `200 + status=unavailable`（未配置 provider）的分工。B1 按实现的事实渲染为**信息态**，
>    请 MAIN 决定是否补进契约正文。
> 2. `quality-summary` 没有 Authorization 段，B1 按"教师侧"（与成绩证据视图同一集合）推断；
>    契约也没有给"样本很少"的数值门槛，B1 取 5 并在界面上明说这是本页的阅读辅助。
>
> **B1 侧推断、需要 MAIN 表态的一处（本批）**：`getQuestionTagCoverage` 的契约只声明
> "`bearerAuth` + 该课程的读权限，与 `listQuestions` 完全一致"（在 CHG-0020 正文，不在 OpenAPI 正文），
> 因此 B1 **没有**为它新造可见性规则，而是让它与题目列表页落在**同一个** `RequireAuth` 上；
> 契约也没有为它声明 `404`，B1 因此把 `404` 读作"这条路由还不存在"（缺口 8 记录的是另一件事）。

### 成绩查询 / 导出 / SSE 的冻结状态（本次复核结论）

这三项曾被旧版本本表误标为"未冻结"。当前状态以契约文件为准，逐条核对如下：

- **成绩查询与导出：已冻结。** `GET /api/v1/grades`（`listGrades`）与 `GET /api/v1/grades/export`（`exportGrades`）均在 `MAIN/contracts/openapi/b2.yaml` 的 `paths` 中成文：查询参数 `course_id`（必填）、`section_id`、`question_version_id`；导出另接受 `format ∈ {xlsx, csv}`；响应分别为 `CursorPage`+`GradeRow` 与 XLSX/CSV 文件。→ `WP-B1-C` 无冻结阻塞。
- **SSE 实时进度：已冻结。** 帧结构是 `MAIN/contracts/openapi/b2.yaml` 的 `components.schemas.EvaluationStreamFrame`：`event ∈ {progress, completed, rejected, failed}`、单调 `sequence`、`stage` 枚举、`terminal_reason` 枚举。**SSE 属于 HTTP 契约，因此定义在 OpenAPI 而非 AsyncAPI**；`asyncapi/events.yaml` 中不存在也不需要"SSE 事件类型表"。→ `WP-B1-B` 无冻结阻塞。
- **终态到达路径：已确认。** B1 的全部终态（`completed`/`rejected`/`failed`）经 SSE 到达；`rejected` 刻意**没有**领域事件（只对提交会话有意义），B1 不需要为它增加订阅。
- **核对方式**：逐条比对上述契约文件的 `paths`、`operationId` 与 `components.schemas` 名称；结论与 `../api/contract.md` 一致。

### 已知契约缺口（阻塞项，**不得**由 B1 修改 MAIN）

以下缺口是本次复核中发现的**真实契约缺口**。按 `../../../MAIN/docs/contracts/change-protocol.md`，B1 只能记录并（必要时）提请 CHG，**不修改** `MAIN/contracts/**`，也不以"猜字段"的方式绕过。

| # | 缺口 | 影响的 RQ / 验收 | B1 的处置 |
|---|---|---|---|
| 3 | **部分 200 响应仍无 schema**：`GET /courses/{course_id}/sections`、`GET /audit-records`、`GET /modules/{module}/health` 仅有 `description`（`listEnrollments`/`listAssignments` 已由 CHG-0007 补上 schema） | `RQ-B-1-10`（选课行）、`RQ-B-1-20`（编辑对象）、`RQ-B-1-40/50`（作业集合） | 生成类型对这些响应为 `unknown`；B1 以"服务端返回键动态渲染 + 契约定义的路径标识符（`section_id`/`course_id`）"消费，缺标识符时显式报错，**不**发明字段名 |
| 4 | **成绩无汇总端点**：`listGrades` 只返回分页行，无提交数/平均分/通过率聚合 | `RQ-B-1-30` 验收 1（全班汇总统计） | 页面提供"基于**已加载 N 行**的本地统计"并如实标注口径；**不**把它标为权威汇总（导出与权威口径仍归 B2） |
| 5 | **无"当前用户资料"端点**：契约没有返回调用者自身 `student_id` 的路径，而提交信封的 `student_id` 是必填 | `RQ-B-1-50` 验收 1（提交信封）、`CHG-0014` 的归属 | `useStudentIdentity` 依次读取访问令牌的 `sub` 声明（经会话状态，响应式）与"本人成绩行"；两者都取不到时**禁用提交**并说明原因，**不**猜测、**不**填占位值。隔离区归属侧同样没有全量用户列表（`GET /api/v1/users` 仅管理员），改从**教师可见的名单**取候选，拿不到候选时如实说明 |
| 6 | **`GET /commentary` 的 `404` 语义未成文**：契约列了 `404` 响应，但正文只描述 `200 + status=unavailable`（未配置 provider），没有定义"该运行尚未生成评语"这种情形该走哪个状态码 | `RQ-T-20` | 本部署实测 B2 用 `404` 表示"尚未生成"，detail 明确写"This is not the same as a missing run."。B1 按实现的事实渲染为**信息态**并带出原文；**未**自行推断其他状态码的语义，请 MAIN 决定是否把该语义补进契约正文 |
| 7 | **`quality-summary` 无 Authorization 段、无样本门槛**：`listIntakeQuarantines` 写了 "teacher/assistant/admin within the course scope"，而 `quality-summary` 只有 "an aggregate a teacher reads"；契约也没有给 `attempt_count` "很少"的数值门槛 | `CHG-0015` | 授权按"教师侧"**推断**（与成绩证据视图同一集合，即 `canViewQuestionQuality`）；门槛取 5 并在界面上明说"这是本页的阅读辅助，不是契约规则"。两处都属推断，登记待 MAIN 确认 |
| 8 | ~~`QuestionTagCoverage.tags[].tag` 的形状约束弱于它要喂给的查询参数~~ | **已关闭**（`CHG-0022`）：`QuestionTagCoverage.tags[].tag` 与 `QuestionPublic.tags[]` 现在都声明了 `pattern: '^[a-z0-9][a-z0-9-]*$'` 与 `minLength/maxLength`（后者另有 `maxItems: 20`）。B1 因此**删除**了覆盖度面板里那段"标签可能不是列表筛选取值"的防御分支（它按其自身逻辑已不可达），理由与"残留情形由列表页既有的非法标签横幅兜住"写在 `QuestionCoveragePanel.tsx` 的注释里与 `phase-05` |
| 9 | ~~`CHG-0021` 已生效但四处文本未落进 `b2.yaml`~~ | **已关闭**（批内由 MAIN 补齐）：`resolved` 的省略语义、`resolveIntakeQuarantine` 的可判定正文与可归属集合、`reason_code` 的 19 项分组表、`detail.maxLength: 1000` 均已落地。B1 当时按 CHG 正文实现，随后核对契约一致；两度漂移解释见 `phase-05` 的门禁小节 |
| 10 | **`adapter` 写必填但没有任何读出口**（B3 上报；B1 侧同样成立）：`QuestionVersionPublic` 只暴露 `adapter_kind`/`max_files`/`max_total_bytes`，`RevisionDiff` 只给布尔与长度，`args`/`stdin`/`expect_exit_code`/`comparison`/`files`/`callable` 都读不回来 | `RQ-T-10`（出题写路径）、`CHG-0017` 省略语义的可达范围 | **B1 是重新构造 manifest 的**（不是保留）：保存会把表单里当下的值当 `adapter` 发出去。同会话内保留用户输入；打开一道在 UI 之外创建的题目时那些输入框是空的，必须重新填，否则会用一份不完整的 manifest 覆盖它。界面新增常驻说明（`data-testid="adapter-not-readable"`）。**MAIN 已裁决（`CHG-0025`，但该 CHG 未随 `0.16.0` 落地）**：新增"写形状的读形状" `GET /api/v1/questions/{question_id}/draft-input`（课程写权限，返回与 `QuestionDraftInput` 同形、**减去** `cases`/`reference_answer`），并在同一 CHG 中**否决了"客户端自缓存"**。因此 B1 **不**把当前说明升级成缓存；该端点落地 + 类型再生成后改为从它预填 |
| 11 | **可归属的 `reason_code` 没有机器可读形式**：`IntakeQuarantineView.reason_code` 的类型是 `string`，词表与"哪些可归属"只在 `description` 的表格里 | `CHG-0021`（不可归属不显示归属按钮） | **MAIN 已裁决（`CHG-0027`，已随 `0.16.0` 落地）**：<br>① **响应字段加了 19 项 `enum`**（与 `CHG-0022` 同族：实现已对、契约少写）。B1 把原因码的类型改为**从生成类型派生**（`export type ReasonCode = IntakeQuarantineView["reason_code"]`）：`0.16.0` 再生成后它**已经是**字面量联合类型，那一对可归属值因此在**编译期**被核对——**B1 不再复制词表**。<br>② **可归属的那一对值继续手写，且属明确接受**：它是**行为谓词**而不是字段形状；做成 `x-attributable-reason-codes` 会得到一份**没有任何工具核对的"机器可读"副本**（权威在 B2 的 Python 元组里，`check_shared_vocabularies()` 够不着它），而"假的机器可读比散文更危险——散文会让人去读实现，假机器可读会让人信任它"。将来若要机器可读，**正确机制是加一道门禁**（AST 读 `B2/.../intake/quarantine.py` 的 `ATTRIBUTABLE_REASON_CODES` 再与契约比对 = "两份副本 + 一个核对者"），`CHG-0027` 记为选项但不引入。<br>③ **查询参数 `reason_code` 刻意不加 `enum`**：那会把"用不存在的码筛选"从**返回空页**变成 **`422`**，属行为变更而非补声明，需要 B2 改实现，已记为**待决**（需独立裁决）。B1 不依赖该参数的任何校验语义 |

| 12 | ~~"验证并发布"这条链在真实环境里走不通（跨模块语义）~~ | **已由 `CHG-0028` 正面处理（B3 侧，B1 无需改动）**。B1 在真实浏览器里的读数是这条 CHG 的动机证据：对**草稿**点击验证 → B2 `422` + detail 原文 `the version has no evaluation package yet (B3 compiles one only for a published version), so its reference answer cannot be verified against the cases`。`CHG-0028` 裁决：`getEvaluationPackage` **对草稿也提供服务**、`recordReferenceAttestation` **只接受草稿**——"verify the draft, record the attestation, then publish" 因此成立。B1 的处置不变：按钮、三态与门禁照契约实现，对 `422` **原样展示 detail + request_id**。**待 B3 落地后复测**（本轮无法复测：运行中的 B3 还是旧语义） |

> 缺口 3、4、5、6、7、10、11 都不阻塞三条泳道的**其余**验收项开工；每条都在 `docs/phases/active/` 与
> `docs/requirements/traceability.md` 中有对应记录。缺口 6/7 是上一批真机联调与契约复核新发现的；
> 缺口 8/9 已由 MAIN 在 `CHG-0022`/`CHG-0021` 中关闭；缺口 10/11 已被 MAIN 采纳（`CHG-0025`/`CHG-0027`）：
> **`CHG-0027` 已随 `0.16.0` 落地**（B1 的 `ReasonCode` 因此变成字面量联合类型，词表核对回到编译期），
> `CHG-0025` 的 `draft-input` 读出口**未随批落地**，B1 保持"常驻说明"现状；
> **缺口 12 已由 `CHG-0028` 处理**（见上表）。
> **B1 未修改** `MAIN/contracts/**`，也未用猜字段的方式绕过。
>
> **关于"界面不得断言对方实现进度"（本轮教训，已写进代码）**：B1 此前为四个"契约已声明"页面挂了
> 常驻横幅（"B2/B3 尚未实现…证据只到夹具"）。那类文案**由静态文本断言另一个模块的实现进度**，
> 没有过期机制——端点一上线就会与它旁边的实时数据直接矛盾（真实发生：覆盖度面板渲染着 B3 的实时计数，
> 横幅却写着"B3 尚未实现"）。四个横幅**已全部删除**；端点不可用改由**数据驱动**的状态说明
> （服务端返回 404 时才出现，措辞只说"本部署取不到这个端点"）。**证据边界写在 `docs/` 里，不写在界面上。**

### 已关闭的缺口

| # | 缺口 | 关闭方式 |
|---|---|---|
| 1 | ~~题目图形无可下载端点~~：`QuestionPublic.stem_assets[]` 只给 `asset_id` + `content_type`，没有读取资产内容的路径 | **已关闭**（MAIN 契约变更 CHG-B3-0002 + B1 实现）：`b3.yaml` 提供受控资产读取（`getQuestionAsset`/`uploadQuestionAsset`，含 401/403/404 与 `nosniff`/CSP 响应头语义）。B1 的 `src/features/questions/useQuestionAsset.ts` 用带令牌的 `fetch` 取字节并自建 `blob:` URL，403/401 显示"资源不可得"且不伪造图片；`useQuestionAsset.test.ts` 逐条固定"什么算受控资产地址"。此前文档把它记为**阻塞项**是 B1 自己在低估已有能力，已改正 |
| 2 | ~~版本题干不可得~~：`QuestionVersionPublic` 无 `stem_markdown`/`stem_assets`，`QuestionPublic` 无版本列表 | **已关闭**（MAIN 契约变更）：`QuestionVersionPublic.stem_markdown`（必需）与 `stem_assets` 已加入，`QuestionPublic.versions` 给出完整版本历史（新→旧）。B1 因此渲染**该版本自己的冻结题干**，并在选中版本与当前题干逐字相同时说明"内容未变、不重复渲染"；页面上的"契约缺口"提示与不再可达的 i18n 词条已一并移除 |
| 3（部分） | ~~`listEnrollments` / `listAssignments` 响应无 schema~~ | **已关闭**（MAIN 契约变更 CHG-0007）：`b4.yaml` 新增 `EnrollmentPublic`（含连接名单与成绩所需的 `student_id`，可空）、`b3.yaml` 新增 `AssignmentPublic`。B1 的名单读路径因此改走生成类型，不再经 `src/api/opaque.ts`（该文件仅保留给仍未闭合的 `listSections`/`listAuditRecords`/`getModuleHealth`） |
