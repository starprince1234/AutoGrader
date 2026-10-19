# B1 接口契约（我提供 / 我消费）

契约唯一权威：`../../../MAIN/contracts/**`（本地集成时）。版本与摘要见 `../../contracts.lock`。本文件只写"我提供/我消费哪些端点与事件"，**不写字段表**；需要字段时打开契约文件。

> **契约现状（已复核）**：契约 `0.16.0` 的 OpenAPI 已覆盖下表**全部**端点（B1 消费的每一个端点都能在
> `MAIN/contracts/openapi/{b2,b3,b4}.yaml` 中找到路径与 operationId）。因此 B1 的三条泳道**均具备开工条件**，
> 不存在"等待 MAIN 冻结才能开工"的端点。
>
> 版本推进轨迹：`0.9.0` → `0.10.0`（CHG-0014 隔离区与证据包、CHG-0015 题目质量热力图）→
> `0.11.0`（CHG-0016 修正 `PATCH /questions/{id}` 的响应类型为 `QuestionPublic`）→ … → `0.13.0`
> （CHG-0018 引入 `EvaluationCommentary.advisory_score`，RQ-T-30）→ `0.14.0`（CHG-0020 引入
> `GET /api/v1/questions/coverage`，即题库标签覆盖度）→ **`0.15.0`**（同批：`CHG-0019`/`CHG-0023`
> 参考解答验证与它的交付通道、`CHG-0017` 草稿省略语义、`CHG-0021` 隔离区语义修复、`CHG-0022` 标签形态、
> `CHG-0024` 草稿 `published_at` 可空；批内摘要两度变动 `9ae4ebfe…` → `511f27ad…` → `29271ee2…`，
> 版本号始终 `0.15.0`）→ **`0.16.0`**（`CHG-0026` 参考解答的 `409` 语义、`CHG-0027`
> `IntakeQuarantineView.reason_code` 的 19 项 `enum`、`CHG-0028` 评测包与存证对**草稿**开放——
> 正是"先验证再发布"能成立的前提；摘要 `372cfcab…`。`CHG-0025` 的 `draft-input` 读出口**未随本批落地**）。
> B1 的类型按当前版本重新生成，`contracts.lock` 与 `B1/module.yaml` 由 MAIN 同步，B1 未手改。
>
> **本批新增消费一个端点，并首次调用一个此前不可用的端点**：`verifyReferenceAnswer`
> （`POST /api/v1/reference-verifications`，`CHG-0019`）与 `publishQuestionVersion`
> （`POST /api/v1/question-versions/{id}/publish`）。后者此前对 B1 不可达：它要求与 `content_digest`
> 绑定的参考解答验证存证，而记录存证的端点是 `internal` + `serviceToken`；`ADR-0015` 把这条死结裁决为
> **B2 执行、B3 记录、教师一键触发**（`CHG-0023` 补上 B2 取参考解答的内部通道，B1 不消费）。
> `CHG-0024` 对 B1 **无代码改动**（生产代码从不读 `published_at`）。
> **本轮已在真实浏览器里复核**（2026-09-20，宿主 `18000` + 真实 B2/B3）：覆盖度**端到端成立**
> （`total=102 / tagged=4 / untagged=98`，逐标签与 MAIN 的独立 SQL 一致）；隔离区与质量两个页面
> 正常渲染（不再是 404）；验证端点可用但对**草稿**返回 `422`（缺口 12）。
> 下面各行的"验证到哪为止"逐条写明**哪些是真实读数、哪些仍只有夹具证据**。
> 同时，界面上**已删除**四条"B2/B3 尚未实现"的常驻横幅——那类断言没有过期机制，端点上线后即自相矛盾
> （证据边界只写在文档里；端点不可用改由**数据驱动**的状态说明）。
> 上一批真联调发现的两处实现事实（404 = "尚未生成评语"、`failed` 运行没有报告）仍见下方"实测行为"。

## 我提供的端点

B1 是静态客户端，`module.yaml` 声明 `routes: []`，**不提供任何服务端端点**。`provides: [web-ui]` 表示它提供浏览器 UI 资产，由 Vercel 发布到 `www.91idc.me/app/`；API 则由 `api.91idc.me` 提供。

## 我消费的端点（按提供方）

### 来自 B4（身份 / 课程 / 名单）

| 端点 | 用途 | operationId | RQ |
|---|---|---|---|
| `POST /api/v1/auth/session` | 登录建会话 | `createSession` | `RQ-B-4-50` |
| `POST /api/v1/auth/session/refresh` | 刷新凭据并轮换 | `refreshSession` | `RQ-B-4-50` |
| `DELETE /api/v1/auth/session/logout` | 退出 | `deleteSession` | `RQ-B-4-50` |
| `GET /api/v1/auth/jwks` | 公钥集合（B1 一般无需直接调用） | `getJwks` | `RQ-B-4-50` |
| `GET /api/v1/courses` | 课程列表 | `listCourses` | `RQ-B-1-30` |
| `GET /api/v1/courses/{course_id}/sections` | 课序列表 | `listSections` | `RQ-B-1-10/30` |
| `GET /api/v1/sections/{section_id}/enrollments` | 名单读取 | `listEnrollments` | `RQ-B-1-10` |
| `PATCH /api/v1/enrollments/{enrollment_id}` | 名单编辑（含并发保护 `expected_updated_at`） | `updateEnrollment` | `RQ-B-1-20` |
| `DELETE /api/v1/enrollments/{enrollment_id}` | 名单移除 | `deleteEnrollment` | `RQ-B-1-20` |
| `POST /api/v1/roster-imports` | 上传名单并开始解析 | `createRosterImport` | `RQ-B-1-10` |
| `GET /api/v1/roster-imports/{roster_import_id}` | 读取解析预览与逐行校验结果 | `getRosterImport` | `RQ-B-1-10` |
| `PUT /api/v1/roster-imports/{roster_import_id}/mapping` | 人工确认/修正列映射 | `setRosterColumnMapping` | `RQ-B-1-10` |
| `POST /api/v1/roster-imports/{roster_import_id}/confirm` | 确认写入（幂等 + 原子） | `confirmRosterImport` | `RQ-B-1-10` |

### 来自 B3（题目）

| 端点 | 用途 | operationId | RQ |
|---|---|---|---|
| `GET /api/v1/questions` | 题目列表（公开 DTO）；支持 `course_id`/`language`/`published_only`/`tags`（`tags` 为 **all-of** 匹配的重复参数） | `listQuestions` | `RQ-B-1-40` |
| `GET /api/v1/questions/{question_id}` | 题目详情（公开 DTO，含题干图形资源引用与版本历史） | `getQuestion` | `RQ-B-1-40` |
| `POST /api/v1/questions` | **新建题目草稿**；`course_id` 是**必需查询参数**（`QuestionDraftInput` 是闭合对象，作用域只能与请求体并列） | `createQuestion` | `RQ-T-10` |
| `PATCH /api/v1/questions/{question_id}` | **修改草稿**；已发布版本需 `as_new_version=true`（此时 `revision_note` 才有意义）。响应是 **`QuestionPublic`**（CHG-0016）：`QuestionDraftInput` 能改 `title`/`tags`，而版本 DTO 没有这两个字段，因此新版本的标识只能从 `versions[0]` 取 | `updateQuestionDraft` | `RQ-T-10` |
| `POST /api/v1/questions/{question_id}/authoring-assistance` | 出题辅助：**模型生成的建议**（`confidence: advisory`）；未配置 provider 返回 `409` | `requestAuthoringAssistance` | `RQ-T-10` |
| `GET /api/v1/questions/{question_id}/diagnostics` | 确定性比较策略检查（`CMP-*` 规则）；空列表 = 检查过无问题 | `getQuestionDiagnostics` | `RQ-T-10` |
| `POST /api/v1/question-versions/{question_version_id}/publish` | **发布并冻结一个版本**（`CHG-0008` 的门禁在服务端：要求与 `content_digest` 绑定的参考解答验证存证）。B1 本批**首次调用**它——此前没有可用的存证路径（见 `../phases/active/phase-05-...`） | `publishQuestionVersion` | `RQ-B-3-20` |
| `GET /api/v1/question-versions/{question_version_id}` | 版本读取（公开 DTO，含该版本冻结题干、`revision_note`/`revision_diff`） | `getQuestionVersion` | `RQ-B-1-40` |
| `GET /api/v1/assignments` | 作业集合列表（`AssignmentPublic`：`assignment_id` + `question_version_ids`；契约**没有**按 id 取单个作业的端点） | `listAssignments` | `RQ-B-1-40/50` |
| `GET /api/v1/questions/coverage` | **题库标签覆盖度**（CHG-0020）：按标签聚合调用者可见的题目数。参数只有 `course_id`（**必需**）与 `published_only`（默认 `true`，与列表同一默认）；响应是 `QuestionTagCoverage`，**只有计数与标签、没有 `question_id`**。B1 在题目列表页消费它，并与列表共用同一个作用域 | `getQuestionTagCoverage` | `RQ-B-3-20` 的可用性补全（CHG-0020） |

- ⚠️ `GET /api/v1/question-versions/{id}/evaluation-package`、`POST /api/v1/question-versions/{id}/fixtures`
  与 `POST /api/v1/question-versions/{id}/attestations` 都是 **`tags: [internal]` + `serviceToken`**，B1 **不消费**：
  前两者含参考答案/夹具字节，后者是"执行者与记录者必须是不同组件"的那条约束本体。这也解释了为什么
  UI 出题**没有发布路径**（见 `../phases/active/phase-02-apple-redesign-and-e2e.md` §11 第 1 条）。

### 来自 B2（提交 / 评测 / 成绩）

| 端点 | 用途 | operationId | RQ |
|---|---|---|---|
| `POST /api/v1/submissions` | 提交作业（幂等键） | `createSubmission` | `RQ-B-1-50` |
| `GET /api/v1/submissions/{submission_id}` | 提交状态 | `getSubmission` | `RQ-B-1-50` |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}` | 评测状态快照（非 SSE） | `getEvaluationRun` | `RQ-B-1-50` |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/events` | **SSE 实时进度与终态** | `streamEvaluationEvents` | `RQ-B-1-50` |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/report` | 逐项证据报告（含 CHG-0012 的 `resource`/`artifacts`/`explanation`；学生视图已脱敏） | `getEvaluationReport` | `RQ-B-1-30` |
| `GET /api/v1/grades` | 成绩查询（课程/课序筛选） | `listGrades` | `RQ-B-1-30` |
| `GET /api/v1/grades/export` | 成绩导出（XLSX/CSV，防公式注入） | `exportGrades` | `RQ-B-1-60` |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/commentary` | **教学评语轨**（非确定性；`disclaimers` 必须逐字显示；未配置 provider 时 `200 + status=unavailable`） | `getEvaluationCommentary` | `RQ-T-20`、`RQ-T-30` |
| `POST /api/v1/evaluation-runs/{evaluation_run_id}/commentary` | 生成/重新生成评语（教师/助教；无 provider 时 `409`，**永不改变分数**） | `requestEvaluationCommentary` | `RQ-T-20` |
| `GET /api/v1/intake/quarantines` | **隔离区读取**（`cursor`/`limit`/`reason_code`/`resolved`；`resolved` 无声明默认值，B1 始终显式传值） | `listIntakeQuarantines` | `RQ-B-1-30` 的运营侧（CHG-0014） |
| `POST /api/v1/intake/quarantines/{intake_quarantine_id}/resolve` | 手动归属到学生（体只有 `student_id` 必填 / `note` 可选） | `resolveIntakeQuarantine` | 同上 |
| `GET /api/v1/evaluation-runs/{evaluation_run_id}/appeal-pack` | **可移动的证据包**（`view` 默认 `teacher`，B1 始终显式发送；`redactions` 必须逐条列出） | `getAppealPack` | `RQ-B-1-30` 的争议侧（CHG-0014） |
| `GET /api/v1/evaluation-runs/quality-summary` | **题目质量聚合**（仅计数；`security_not_checked` 与 `unmeasured_runs` 必须分开呈现） | `getEvaluationQualitySummary` | `RQ-B-1-30` 的教师侧（CHG-0015） |
| `POST /api/v1/reference-verifications` | **参考解答验证**（CHG-0019 / `ADR-0015`）：B2 在沙箱执行该版本的参考解答并逐用例比对。响应只有计数、逐用例结局与失败类别——**没有**期望输出、隐藏用例输入与参考解源码。`attestation_id` 为 `null` 表示 B3 没记下存证（此时即使全通过也不能发布）；没有沙箱的平台返回 `503` 且不记录任何东西 | `verifyReferenceAnswer` | `RQ-T-10` 与 `RQ-B-3-20` 的可用性补全 |

**实测行为（对着运行中的 host `http://127.0.0.1:18000` 真调所得，不是从契约推断）**：

- `GET .../commentary` 在**尚未生成**评语时返回 `404`，detail 原文为
  "No commentary has been generated for this evaluation run yet. Generate it with POST on this
  path. This is not the same as a missing run." → B1 把它渲染成**信息态**（`commentary-not-generated`）
  而不是通用错误，并把服务端原文带出来。契约的 `getEvaluationCommentary` 只列了 `404` 响应而未描述其语义，
  也没有把"未生成"与"未配置 provider（`200 + unavailable`）"的区别写进正文——**已作为契约表述缺口登记**。
- `POST .../commentary` 实测返回 **`202` + 完整的 `ready` 评语体**（deepseek / deepseek-flash /
  `b2-commentary/1`，约 5.9 s），因此客户端不需要再补一次 `GET`（B1 直接把它写回查询缓存）。
- `/commentary` 的 `advisory_score` **键在 `0.16.0` 复核时已出现，但值为 `null`**（`GET` → 200/`ready`，
  以及一次真实 `POST` → `202 / 7497 ms`，两次读到的都是 `null`）。因此 B1 的"`null` 不渲染 + 说明
  本次没有产生参考分"这条路径**已真实联通**；**非空参考分仍未在真机上出现过**（本部署的 provider
  对这几次 `failed` 运行返回 none），非空渲染只有契约与夹具证据。
- `GET .../report` 对 `failed` 运行返回 `404`（"A report exists only after a successful run."）。
  由于评语在 `failed` 运行上同样可生成可读，B1 的下钻视图**不再因为报告缺失就吞掉评语轨**。

## 我订阅的事件

见 `events.md`。

## 终态的到达方式（重要）

B1 的**全部终态**（`completed` / `rejected` / `failed`）经 **SSE 流**到达，而不是经 NATS 订阅。权威定义在 `MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame`：帧的 `event` 取值恰为 `progress` / `completed` / `rejected` / `failed`，并携带单调 `sequence`、`terminal_reason` 与（完成时）`score`。

**设计含义**：`rejected` **没有**对应的领域事件——"被拒"只对提交它的那个会话有意义，广播到系统级会产出一个没有消费者的 payload。B1 因此**不需要**为 `rejected` 增加事件订阅。

## 消费规则（实现口径）

- **路径取自契约原文**：`MAIN/contracts/openapi/*.yaml` 中的路径已含 `/api/v1` 前缀，B1 原样使用，
  不做二次拼接（否则会得到 `/api/api/v1/...`）。
- **`VITE_API_BASE_URL` 是"源"，不是"前缀"**：生产构建读取 `https://api.91idc.me`；本地开发连接 `http://127.0.0.1:18000`。
  契约路径原样带 `/api/v1`，不会再拼接路径前缀。
- 浏览器请求设置 `credentials: include`，以便访问 API 子域下的 HttpOnly 刷新 cookie；后端 CORS 只允许 `https://www.91idc.me`。
- **每个请求带 `X-Request-ID`**（与前端结构化日志同源）；`401` 刷新一次后重放，`403/404/409/422` 不重试（确定性结论）。
- **重复同名查询参数**：`QueryParams` 的值可以是数组，序列化为 `?tags=a&tags=b`（契约里 `tags` 是
  `style: form, explode: true`，逗号拼接会得到一个服务端读不到的值）。空数组等于不加该条件。
- **类型来自生成物**：`node scripts/generate-api-types.mjs` 从契约生成 `src/api/generated/*.ts`，`--check` 拦截漂移。

## 错误与通用约定（继承 MAIN，不在 B1 复制定义）

- 错误格式：RFC 9457 Problem Details（`../../../MAIN/contracts/openapi/common.yaml` 的 `ProblemDetails`）。
- 授权三层：`401`（未认证）/ `403`（角色或课程作用域不符）/ `404`（有权限但不存在）。**前端隐藏不构成授权**（`RQ-B-1-20`）。
- 标识与时间：UUID v4、UTC RFC 3339（`../../../MAIN/docs/api/identifiers.md`）。
- 请求关联：`traceparent`、`X-Request-ID`；B1 对每个用户操作生成/透传 `request_id`，用于日志与后端关联。

## 状态与缺口

| 项 | 状态 | 说明 |
|---|---|---|
| 上表全部消费端点 | **契约已锁定** | 均在 `MAIN/contracts/openapi/` 中成文；"契约已锁定"指路径与语义已确定，**不代表 B4/B3/B2 已实现** |
| 契约版本 | `0.16.0` | 见 `../../contracts.lock`；B1 的 `CONTRACT_VERSION` 由构建期从该文件注入（`vite.config.ts` 的 `define`），代码里不再有手写字面量 |
| 教学评语轨（RQ-T-20/T-30） | **已实现，已真实联通** | `getEvaluationCommentary`/`requestEvaluationCommentary`；三态 + `disclaimers` 逐字 + 出处/输入白名单 + 参考分低权重并列。**真机证据**：`http://127.0.0.1:18000` 上 `GET`→200/`ready`（真实中文评语 + 2 条 disclaimers）、未生成→404（信息态）、`POST`→202（5.9 s，另一次 `0.16.0` 复核为 7497 ms）。**`advisory_score` 键已出现、值为 `null`** ⇒ "null 不渲染"这条路径已真实联通；**非空参考分**仍只有契约夹具证据（provider 对这几次 `failed` 运行返回 none） |
| 隔离区复核台（CHG-0014 + CHG-0021） | **已实现，读路径已真实联通（空库）** | `listIntakeQuarantines`/`resolveIntakeQuarantine`；`resolved` **永不省略**（省略 = "全部"，只看未归属只能由 `?resolved=false` 表达）；只有 `not_uniquely_matched`/`ambiguous_match` 显示归属按钮，其余原因码说明会 `422`（`CHG-0021` §2.1）。**真机证据**：`GET /api/v1/intake/quarantines?resolved=false&limit=5` → **200**，`{"items":[],"next_cursor":null}`（库里没有隔离记录）⇒ 端点可用、页面渲染"当前筛选下没有隔离记录"；**带数据的渲染与归属写路径仍只有组件测试证据** |
| 证据包下载（CHG-0014） | **已实现；200 路径未触达** | `getAppealPack`；按需加载、`view` 显式发送、`redactions` 逐条列出、下载响应体原字节（同一个 `Blob`）。**真机证据**：`GET .../appeal-pack?view=teacher` → **404**，detail 为 "The evaluation run exists but has no report yet (state: failed，终态原因 infrastructure_error). A report exists only after a successful run." ⇒ 端点已实现，但本部署所有运行都是 `failed`（Windows 沙箱，`ADR-0004` 禁止降级），**成功响应体从未被读到**；该面板在报告缺失时不渲染，因此浏览器里也未打开 |
| 题目质量热力图（CHG-0015） | **已实现，已真实联通** | `getEvaluationQualitySummary`；`security_not_checked` 与 `unmeasured_runs` 分开呈现；**静态规则按冻结目录逐条给出、`count=0` 不过滤**（见下）。**真机读数**（`question_version_id=34d07765-…`）：`200`，`attempt_count=0`、`static_rule_hits` **15 条且全部 `count=0`**、`security_found/not_found/not_checked` 皆 `0`、`failure_categories`/`case_failure_rates` 为空；浏览器里逐条核对（15 行 0 命中、三条安全计数、样本不足提示、"没有可读的信号"）。**非空数据分支**仍只有契约夹具证据 |
| 题库标签覆盖度（CHG-0020） | **已实现，已真实联通（真实浏览器 + 真实 B3）** | `getQuestionTagCoverage`；`course_id` 缺失时不发请求，`tags` 顺序原样渲染，`tagged + untagged == total` 是页面上唯一被校验的恒等式（逐标签计数按设计重叠、不与总数相加），`untagged_questions` 单独一格。**真实读数**：`total=102 / tagged=4 / untagged=98`，`file-systems=3 / virtual-memory=3 / unix=1`，与 MAIN 的独立 SQL 逐项一致。`404` 分支是**数据驱动**的（只说"本部署取不到该端点"）。`CHG-0022` 补齐标签形态约束后，那段"标签可能不可筛"的防御分支已删除（缺口 8 关闭） |
| 参考解答验证与发布（CHG-0019 / `ADR-0015`） | **已实现；链路的跨模块前提由 `CHG-0028` 补上（待 B3 落地后复测）** | `verifyReferenceAnswer` + `publishQuestionVersion`：两步显式动作（验证 ≠ 发布）、不做自动重试与自动发布；三态（全通过可发布 / 未通过逐用例失败类别 / `503` 或全 `infrastructure_error` → **平台依赖不可用**，不推断是哪一类依赖）；**全通过但 `attestation_id: null` → 不可发布**；结论的 `content_digest` 与当前版本不一致 → 结论失效。**真实读数（`0.15.0` 时）**：对**草稿**点击验证 → B2 `422`「the version has no evaluation package yet (B3 compiles one only for a published version)…」，页面原样展示该 detail + `request_id`。该缺口（原缺口 12）已由 **`CHG-0028`** 正面处理：评测包与存证都对**草稿**开放（"verify the draft, record the attestation, then publish"），B1 **无需改动**，等 B3 落地后复测 |
| API 类型生成 | **已完成** | `node scripts/generate-api-types.mjs`（openapi-typescript）生成 `src/api/generated/*.ts`；`--check` 实跑通过（"API types match MAIN/contracts."） |
| 无 schema 的响应 | **不透明消费（缺口已收窄）** | `listSections`/`listAuditRecords`/`getModuleHealth` 的 `200` 仍无 schema：按服务端返回键动态渲染，只依赖契约路径标识符（`section_id`），缺标识符即显式报错（`src/api/opaque.ts`）。`listEnrollments`（`EnrollmentPublic`）与 `listAssignments`（`AssignmentPublic`）已由 CHG-0007 补上 schema，改走生成类型 |
| 题目图形端点 | **已提供** | `b3.yaml` 的 `getQuestionAsset`/`uploadQuestionAsset`；B1 用带令牌的 `fetch` 取字节并自建 `blob:` URL（`src/features/questions/useQuestionAsset.ts`） |
| 版本题干字段 | **已提供** | `QuestionVersionPublic.stem_markdown`/`stem_assets` 与 `QuestionPublic.versions` 已在契约中 |
| 出题写路径 | **已提供并使用** | `createQuestion`/`updateQuestionDraft` + `QuestionDraftInput`（`tags`/`revision_note`）；B1 的 `/questions/new` 与 `/questions/{id}/edit` 消费它们 |
| 出题辅助与诊断 | **已提供并使用** | `requestAuthoringAssistance`（模型建议，`confidence: advisory`）与 `getQuestionDiagnostics`（确定性 `CMP-*`）；两块面板外观与文案分离 |
| **发布端点** | **存在但 B1 不能调用** | `publishQuestionVersion` 需要与 `content_digest` 绑定的 attestation，而记录 attestation 的端点是 `internal` + `serviceToken`。这是**有意为之的组件分离**（CHG-0008），不是缺字段：UI 因此不出发布按钮，只显示"待验证后才能发布"。已上报 MAIN 裁决（§11 第 1 条） |
| 夹具上传端点 | **存在但 B1 不能调用** | `uploadVersionFixture` 同为 `internal` + `serviceToken` 且摘要由服务端计算，因此 `kind=file` 的题目无法从 UI 创建（页面明确拒绝而不是编造摘要） |
| 成绩汇总端点 | **缺失** | 页面只能给"基于已加载 N 行"的本地统计并如实标注口径；`quality-summary`（CHG-0015）是题目质量聚合，**不解决**成绩汇总 |
| 当前用户资料端点 | **缺失（阻塞提交 / 限缩学生选择器）** | 会话响应不含 `user_id/student_id`，而提交信封与隔离区归属都要求平台用户 ID。提交侧依次取**会话状态里的令牌 `sub`**、本人成绩行；隔离区归属侧改为"从教师可见的**名单**里选学生"，拿不到候选时如实说明（`GET /api/v1/users` 仅管理员，不作为降级路径） |
| 隔离区"未生成/不存在"语义 | **契约表述缺口（已上报）** | `getEvaluationCommentary` 只列了 `404` 而未描述其语义；B2 实现用 404 表示"该运行还没有评语"并在 detail 里明确"不等于运行不存在"。B1 按实现的事实渲染为信息态，并在此登记，请 MAIN 决定是否把该语义写进契约正文 |
| `version` 字段变更 | 需走契约变更 | 上表 operationId 是生成客户端的基础，**不得随意改名** |
| 冻结清单 | 见 `../collaboration/interfaces.md` | 该表记录"接口语义已冻结但实现未开始"的依赖关系与全部缺口 |

> 上述缺口的完整影响面（对应 RQ / 验收 / B1 处置）见 `../collaboration/interfaces.md` 的
> "已知契约缺口"。**未修改** `MAIN/contracts/**`，也**未**用猜字段的方式绕过。
