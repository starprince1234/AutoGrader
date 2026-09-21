# Phase 03 契约已声明面的补齐：教学评语轨、AI 参考分、隔离区、证据包、题目质量（当前阶段）

本文件记录本轮**真实执行的命令、原始读数、未验证项与契约歧义**。状态词只用
`已完成` / `部分` / `阻塞` / `未验证`；未跑过的命令不写成通过。

## 目标与范围

契约里有一批**已经成文、界面却完全没有出口**的能力。被点名的五处：

1. `RQ-T-20` 教学评语轨（`/commentary`）——B2 的生成、存储、端点、测试都已实现，
   而 B1 的 `src` 下对 `/commentary` **零调用点**：学生和教师看不到任何东西。
2. `RQ-T-30` AI 评分参考（契约 `0.13.0` / CHG-0018 新增 `EvaluationCommentary.advisory_score`）。
3. `CHG-0014` 隔离区复核台（`/intake/quarantines`）——该表自邮件接收路径建成起就在写，
   却一直没有读取路径：未匹配的邮件从教师角度看是**无声无息地消失**。
4. `CHG-0014` 申诉证据包（`/appeal-pack`）——成绩争议需要**可移动**的证据。
5. `CHG-0015` 题目质量热力图（`/quality-summary`）——只有计数，但两个字段极易被不诚实地渲染。

范围外：`MAIN/contracts/**`（未修改）、`B1/module.yaml`（未修改）、`B1/contracts.lock`（未手改）、
`B1/package.json` 的依赖（未变）、服务端行为。未引入任何 UI 组件库或状态管理库。

## 完成条件

1. 五个能力都有真实的界面出口，且都挂在**既有角色可见性模型**上（fail-closed）。
2. 契约的展示约束逐条落实并**有断言**：`disclaimers` 逐字、参考分与权威分并列但权重更低、
   参考分不参与任何本地计算、`security_not_checked` 与 `unmeasured_runs` 分开呈现、
   `redactions` 逐条列出、`view` 显式发送。
3. 门禁全绿：`eslint --max-warnings 0` / `tsc -b` / `generate-api-types --check` / `vitest` / `vite build`。
4. 文档如实区分"对着契约构建"与"真联调过"，`/commentary` 单独对待。

## 起点事实（工作区现状，与本轮的关系）

开工时工作区里**已经存在**一份约 16 小时前留下的、针对**同一张票据**的半成品：`src/api/types.ts`
的隔离区/证据包/质量聚合派生类型、`src/auth/visibility.ts` 的三个可见性函数、
`src/app/queryClient.ts` 的三个查询键、`src/i18n/index.ts` 的约 60 条词条、
`src/testing/contractFixtures.ts` 的三个夹具、`src/ui/format.ts` 的 `isUuid`。
它**没有**任何组件、路由、测试，文档里也没有登记。处置：**复用并接着写**（不另起一套命名、
不重复定义），补齐缺失的部分。它引用的 `features/intake/useQuarantines.ts` 当时并不存在
（一条指向不存在文件的注释本身就是缺陷），本轮已按该路径实现。

`contracts.lock` 与 `module.yaml` 开工时已是 `0.13.0`；`node scripts/generate-api-types.mjs`
实跑通过，`src/api/generated/b2.ts` 里出现 `advisory_score`。

## 实际变更（落点）

| 能力 | 文件:行 | 说明 |
|---|---|---|
| 评语读取/生成 | `src/features/commentary/useCommentary.ts:21/25/34` | `GET` 是普通查询（`unavailable` 是正常答案）；`POST` 是 mutation（出网、计费，不能被重渲染自动重发）；`202` 的响应体直接写回缓存 |
| 评语与参考分渲染 | `src/features/commentary/CommentarySection.tsx:100` | 三态 + `404` 信息态 + `403` 策略说明 + 参考分低权重并列 + `disclaimers` 逐字 + 出处/输入白名单 |
| 参考分与权威分并列 | `src/features/grades/GradeDrilldown.tsx:71-91` | `score-region` 同时含 `authoritative-score`（`score-authoritative` + 强调 pill）与 `CommentarySection`（参考分用 `advisory-score` + `pill--idle`） |
| 报告缺失时不吞掉评语轨 | `src/features/grades/GradeDrilldown.tsx:41-56` | `failed` 运行没有报告（实测 404），评语轨仍然可达 |
| 证据包 | `src/features/appeals/useAppealPack.ts:30/38/73`、`AppealPackPanel.tsx:34` | 一次请求取 `blob`，显示用 `text()`、下载用同一个 `blob`（响应体原字节）；`view` 由角色推出且始终显式发送；按需加载 |
| 隔离区列表/归属 | `src/features/intake/useQuarantines.ts:23/28/53`、`QuarantinePage.tsx:37/136` | `resolved` 始终显式发送；游标分页；归属体只有 `student_id`/`note`；`StudentPicker` 用教师可见的**名单**做候选 |
| 题目质量 | `src/features/quality/useQualitySummary.ts:23/43/57/74`、`QualitySummaryPage.tsx:37/61/95/223` | 必填版本参数、时间本地↔UTC 双向转换、`security_*` 三块分离、`unmeasured_runs` 单列、小样本提示、`infrastructure_error` 单列 |
| 参考分类型 | `src/api/types.ts:117-131` | `EvaluationCommentary` / `CommentaryStatus` / `CommentaryProvenance` / `AdvisoryScore`，全部来自生成类型 |
| 可见性 | `src/auth/visibility.ts:69/78/87/101` | `canGenerateCommentary` / `canReviewQuarantine` / `canViewQuestionQuality` / `appealPackView`，全部 fail-closed |
| 查询键 | `src/app/queryClient.ts:47/52/55/61` | 评语不带 `view`（端点上没有这个维度）；证据包带 `view`（学生/教师是两份内容） |
| 路由 | `src/app/router.tsx:111/122` | 新增 `/intake/quarantines`、`/quality`（`?question_version_id&from&to`） |
| 路由守卫 | `src/app/routeComponents.tsx:167/184` | 两个路由各自挂 `RequireAuth` + `RequirePermission` |
| 导航 | `src/app/AppShell.tsx:31/35` | 隔离区、题目质量两项按可见性过滤 |
| 质量入口 | `src/features/questions/QuestionDetailPage.tsx:89` | 题目详情 → 选中版本的教师质量视图（深链） |
| 样式 | `src/styles.css` 的 `.score-authoritative` / `.advisory-score` / `.count-grid` / `.count-card` | 参考分刻意更弱：灰底、虚线边、无强调色 |

**未改动**：`MAIN/contracts/**`、`B1/module.yaml`、`B1/contracts.lock`、`B1/package.json`。

## 真联调实测（对着运行中的 host `http://127.0.0.1:18000`）

登录：`POST /api/v1/auth/session`（seed 里的教师 `t.zhang`）→ `roles=assistant,teacher`。
下列读数全部来自 `curl.exe`（PowerShell 的 `Invoke-WebRequest` 在 401 挑战下会尝试交互式索要凭据而失败）。

| 探测 | 原始读数 |
|---|---|
| `GET /api/v1/evaluation-runs/733610ca-.../commentary` | `HTTP 200`，`status=ready`，真实中文评语，`provenance.provider=deepseek` / `model=deepseek-flash` / `prompt_version=b2-commentary/1`，`disclaimers` 2 条，**无 `advisory_score` 字段** |
| `GET .../commentary`（未生成评语的运行 `90360f4d-...`） | `HTTP 404`，detail 原文：`No commentary has been generated for this evaluation run yet. Generate it with POST on this path. This is not the same as a missing run.` |
| `POST .../commentary`（同一运行） | `HTTP 202`，耗时 5882 ms，响应体 `status=ready` 且带完整评语（即 `202` 就已经给出结果，客户端无需补一次 `GET`） |
| `GET .../commentary`（POST 之后） | `HTTP 200`，`status=ready`，`advisory_present=False`，`disclaimers=2` |
| `GET /api/v1/evaluation-runs/90360f4d-.../report?view=teacher` | `HTTP 404`，detail：`The evaluation run exists but has no report yet (state: failed，终态原因 infrastructure_error). A report exists only after a successful run.` |
| `GET /api/v1/intake/quarantines`（经页面 `/app/intake/quarantines`） | 当时页面渲染 + 证据边界横幅 + `ErrorState`：`资源不存在（404）`（B2 尚未实现该端点）。**该横幅与这段读数均已被后续批次取代**：现在真机是 `200`（见追加一节），横幅也已按"静态文本不得断言另一个模块的实现进度"删除 |
| `GET /api/v1/evaluation-runs/quality-summary?question_version_id=53f8ac1a-...`（经页面 `/app/quality?...`） | 深链把版本填进输入框并发出请求；返回 `404` → 页面原样展示失败，不编造数据 |

**浏览器端（Playwright 对着 `http://127.0.0.1:18000/app`，使用的是本轮 `vite build` 的产物）**：

- 教师登录后主导航出现「隔离区」「题目质量」，匿名时两项都不出现（fail-closed）。
- 成绩 → 第一行「逐项证据」→ 弹窗内**同时**渲染报告错误（该运行没有报告）与教学评语面板：
  `commentary-text` 为真实 deepseek 评语全文；`commentary-disclaimers` 两条与服务端逐字一致；
  `commentary-provenance` 显示 deepseek / deepseek-flash / `b2-commentary/1` 与 21 条 `input_scope`；
  `advisory-score` **不存在**（该 host 不下发 `advisory_score`），页面显示"本次没有产生参考分"；
  `appeal-pack` 不存在（报告缺失）。
- 打开**未生成评语**的运行 → `commentary-not-generated` 信息横幅，并带出服务端 404 的原文；
  同时存在「生成评语」按钮（教师），**没有**通用错误框。

**两次由真机发现并修复的缺陷**（都不是从契约能推断出来的）：

1. `GET .../commentary` 未生成时返回 `404` 而不是 `200 + status=unavailable`。原实现按"读取出错"
   渲染成红色错误框，等于把"还没生成"说成"出错了"。现在 404 走信息态并带出服务端原文。
2. `failed` 运行没有报告，而下钻视图原先在报告出错时直接 `return <ErrorState/>`，
   于是**评语轨被报告的错误吞掉**——B2 明明已经为这些运行生成了评语。现在报告不可得时
   仍渲染评语轨（证据包因内嵌报告而不可得，故不渲染）。

## 门禁（本轮最终代码上的原始输出）

```text
$ node node_modules/eslint/bin/eslint.js . --max-warnings 0
eslint_exit=0

$ node node_modules/typescript/bin/tsc -b --pretty false
tsc_exit=0

$ node scripts/generate-api-types.mjs --check
API types match MAIN/contracts.
apicheck_exit=0

$ node node_modules/vitest/vitest.mjs run
 Test Files  30 passed (30)
      Tests  225 passed (225)
   Duration  26.39s (environment 75%, setup 16%, tests 5%, import 3%, transform 1%)
vitest_exit=0

$ node node_modules/vite/bin/vite.js build
dist/index.html                                  1.36 kB │ gzip:   0.82 kB
dist/assets/index-Cte5rf7p.js                  561.45 kB │ gzip: 170.15 kB
✓ built in 39.91s
vite_exit=0
```

（上述读数是**最终代码**上的一次完整实跑；`eslint`/`tsc` 无任何输出且退出码为 0。）

- 测试规模：**30 个文件 / 225 个用例**（上一阶段为 25 文件 / 175 用例），新增 **50** 个用例。
- `vite build` 仍有既有的 chunk 体积警告（Monaco `editor.main` 3.77 MB，见 README 已知限制第 4 条），
  不是本轮引入。
- 构建产物里的顶栏显示"契约 0.13.0"（`vite.config.ts` 从 `contracts.lock` 注入），浏览器实测一致。

## 新增测试（50 个用例）

| 文件 | 用例数 | 关键断言 |
|---|---|---|
| `src/features/commentary/CommentarySection.test.tsx` | 14 | `commentary-unavailable` 含"不是错误"且无正文；`disclaimers` 三条逐字；`failed` 说明"分数轨道不受影响"；`pending` 不编造正文；403 走课程策略说明而非通用错误；**404 走信息态并带出服务端原文**；参考分非空时含"不参与判分、不参与申诉"；参考分为 `null` 时 `advisory-score` 不存在；`disclaimers` 缺失时参考分整块不渲染；**反向断言：成绩轨道模块（`aggregate.ts`/`cohort.ts`/`useGrades.ts`）源码里不出现 `advisory`**；生成请求只发一次且写回缓存不再额外 `GET`；409 →「未启用」；学生看不到生成入口 |
| `src/features/grades/GradeDrilldown.test.tsx` | 5 | `score-region` 内同时含权威分与参考分；**权威分在前**（`compareDocumentPosition`）；参考分不含 `score-authoritative` 也不含 `pill--ok`；两个数值各自独立（86 / 62）；参考分为 `null` 时无占位；`failed` 时权威分与逐用例证据照常；证据包默认不发请求；**报告 404 时评语轨仍可达** |
| `src/features/appeals/AppealPackPanel.test.tsx` | 6 | `view=teacher` / `view=student` 显式发送；角色未知时不发请求；`redactions` 两条逐字；`result_digest` 可见；**下载的 `Blob` 字节数与原响应体一致且 `text()` 逐字相等**（夹具带缩进，重新序列化会立刻露馅）；403/409 不产生可下载文件 |
| `src/features/intake/QuarantinePage.test.tsx` | 12 | 请求带 `resolved=false&limit=200` 且不带未声明的参数；切筛选 → 新查询；`reason_code` 随请求发出；逐页追加到末页终点；空态提示"先确认筛选"；归属体**精确等于** `{student_id, note}`、留空发 `null`；非法 UUID 时**不发请求**；409 原样展示；已处理行没有归属入口；学生选择器展开前不发课程请求、逐级取候选并填入平台用户 ID、"未绑定账号"的行不进候选并如实说明 |
| `src/features/quality/QualitySummaryPage.test.tsx` | 13 | 无版本参数 → 零请求；非法 UUID → 不发请求；本地时间→UTC `Timestamp`；深链立即查询并带 `question_version_id`/`from`；403 原样展示且证据边界横幅仍在；**三个 `security_*` 各自成块 + 反向断言"27+14=41 这个合并值在页面上不存在"**；`unmeasured_runs` 单列且块内不含安全数字；`<5` 时提示样本不足并说明门槛是本页辅助；`=5` 时不提示；`attempt_count=0` 给"没有可读信号"；`infrastructure_error` 单列；`0` 次尝试显示"无样例"而非 0%；规则表说明"从未命中"的清单不在本页 |

生产代码**没有**引用 `src/testing/**`（新测试全部通过 `src/testing/**` 的契约夹具与 HTTP 边界桩驱动）。

## 未验证项（诚实边界，**不得**读成已验证）

> **本表是 2026-09-20 早些时候（契约 `0.13.0`）的读数。** 当天晚些时候工作区推进到契约 **`0.16.0`**，
> B2 也补齐了其中三个端点，因此前三行已被下面的「追加（0.16.0 复核）」**取代**：那三处现在是真实联通，
> 只有"写入/非空数据"这些分支仍停在夹具证据。历史读数保留在此以免抹掉当时的证据边界。

| 能力 | 证据到哪为止 | 原因 |
|---|---|---|
| 隔离区复核台（`listIntakeQuarantines`/`resolveIntakeQuarantine`） | ~~生成类型 + 契约夹具 + 组件测试；真机 `404`~~ → **见追加一节：读路径真机 `200`** | 当时 B2 尚未实现该端点 |
| 申诉证据包（`getAppealPack`） | 同上（该页在报告缺失时不渲染，真机未能触达加载路径）→ **见追加一节：真机 `404`（无报告），200 路径仍未触达** | 当时 B2 尚未实现该端点；本轮实测该端点已存在，只是没有 `completed` 运行 |
| 题目质量热力图（`getEvaluationQualitySummary`） | ~~真机深链发出请求后得到 `404`~~ → **见追加一节：真机 `200`，15 条目录规则全为 0 命中** | 当时 B2 尚未实现该端点 |
| **AI 参考分的非空渲染** | 契约 + 夹具（真机现在能读到 `advisory_score` 键，但值为 `null`，见追加一节） | 本部署的 provider 对这几次 `failed` 运行返回 none；非空值仍未出现 |
| 评语 `status: unavailable`（未配置 provider） | 契约 + 夹具 | 本部署配置了 provider，实测走的是 `ready`；`409`（POST 无 provider）同样只有夹具证据 |
| 评语的学生视图 | 契约 + 夹具（403 分支） | 真机上用教师令牌验证；学生可见性是课程级策略，未在本部署打开 |
| 评语生成的真实端到端闭环 | **部分**：`POST` 真实生成（202 / 5.9 s / deepseek），并且浏览器里读到了同一份评语 | 但成绩页无法在浏览器里演示"点击生成按钮"的全链路：本部署所有运行都是 `failed`（Windows 沙箱，ADR-0004 禁止降级），报告中不存在 `completed` 运行 |

## 契约歧义 / 缺口 / 需要 MAIN 裁决项（**未自行裁决**）

1. **`GET /commentary` 的 `404` 语义未在契约正文里定义**。契约只在该端点的 `responses` 里列了 `404`，
   正文只说"未配置 provider 时返回 `200 + status=unavailable`"。B2 的实现用 `404` 表示
   "该运行还没有评语"，并在 detail 里写明"不等于运行不存在"（真机原文见上）。
   B1 按**实现的事实**渲染（信息态 + 带出原文），但这属于"契约未定义、实现已定义"的差异，
   请 MAIN 决定是否把该语义补进契约正文（否则下一个消费者会把它当错误处理）。
2. **"未配置 provider" 与 "未生成" 的区分方式不对称**：前者是 `200 + unavailable`，后者是 `404`。
   两者都是"现在没有评语可读"，但一个走成功响应、一个走错误响应。若 B2 后续把未生成也改成
   `200 + unavailable`（或反之），B1 的两条分支都会失效——已按现状实现，未做兼容层。
3. **`quality-summary` 的授权未成文**：契约没有为它单列 Authorization 段，而 `listIntakeQuarantines`
   明确写了 "teacher/assistant/admin within the course scope"。B1 按"教师侧"（与成绩证据视图同一集合）
   处理，属于**推断**，不是契约原话。
4. **`quality-summary` 没有"很少"的数值门槛**，而票据要求"`attempt_count` 很小时提示样本太少"。
   B1 取 `SMALL_SAMPLE_THRESHOLD = 5` 并在界面上明说"这是本页的阅读辅助，不是契约规则"。
   这是本轮唯一一处 B1 自己定的数值口径，登记在此以便复核。
5. **`advisory_score` 与权威分的"并列"程度**：契约要求"不得让参考分显得更权威"，
   但没有规定版式。B1 的做法是同一区域、权威分在先、参考分用独立类名与灰色 `pill--idle`、
   且永不进入任何计算（含导出）。若 MAIN 有更强的展示要求（例如必须同排同高），需要明确。
6. **`student_id` 的来源在隔离区场景同样缺失**（与既有缺口 5 同源）：教师侧只能从**名单**
   （`EnrollmentPublic.student_id`，可空）得到候选，`GET /api/v1/users` 仅管理员。
   B1 因此不提供"全量学生选择器"，候选为空时如实说明。若 MAIN 认为教师应当能直接按学号检索
   用户，需要一个契约端点（当前不存在）。

## 与既有阶段的关系

- 本阶段**不替代** `phase-01-business-ui.md` 与 `phase-02-apple-redesign-and-e2e.md` 中已记录的结论；
  上一阶段登记的"UI 出题没有发布路径"（§11 第 1 条）与本轮无关，仍然成立。**其中一条已经被后续阶段
  取代**：`CHG-0019`/`ADR-0015` 让"验证并发布"在 UI 上可执行，见 `phase-05-authoring-publish-path-and-0-15-0.md`。
- 上一阶段 §11 记录"CHG-0014/0015 的四个端点未消费"，本轮已消费并在此更新。

## 追加（0.16.0 复核）：静态规则那一段的文案与它自己渲染的数据不一致

### 缺陷与三段证据

对方在真实浏览器里复核质量页时发现：小标题写 **"静态规则命中（只含命中过的规则）"**，
说明写 **"契约没有给出规则目录，因此本页不替你列「从未命中」的清单——那需要一份 B1 拿不到的规则全集"**，
而**同一张表里列着 15 条 `count = 0` 的规则**。也就是说：说明里的"拿不到目录"与表里正在渲染的目录互相矛盾。

| 来源 | 原话 / 事实 |
|---|---|
| 契约 `MAIN/contracts/openapi/b2.yaml:825-835`（`0.16.0` 未改动此段） | "Which static rules **actually fired**, and how often. A rule that never fires on any submission is either useless or mis-specified -- **that is the point of surfacing it.**" |
| B2 实现 `B2/src/autograder_b2/evaluation/quality.py:315-334`（`_rule_hits`） | docstring：**"规则命中：以 B2 规则目录逐条为准（未命中即 0）"**；先按冻结目录 `RULES` 逐条给出（未命中填 0），再把目录之外的 `rule_id` **按字典序追加**在后面——顺序确定，同输入同输出 |
| 真机 `GET /api/v1/evaluation-runs/quality-summary?question_version_id=34d07765-49ea-4171-997b-dd25693716ff` | `HTTP 200`；`attempt_count=0`；`static_rule_hits` **15 条，`zero_rules=15`**（`STAT-PY-001`/`STAT-PY-010`/`STAT-PY-011` … `STAT-SH-021`）；`security_found=0 / not_found=0 / not_checked=0`；`failure_categories=0`、`case_failure_rates=0` |

### 处置：改文案，**不过滤**

**不选择"只发命中过的规则"**，因为契约紧接着的那句明说 never-firing 的规则**正是要 surface 的东西**
（"要么没用、要么写错了"），而 B2 返回完整冻结目录是**有意设计**（上面 docstring 原文）。过滤会把这条意图
连同"目录是否完整"这个信息一起丢掉。因此改的是标签与说明，让它们与数据一致：

| 位置 | 改前 | 改后 |
|---|---|---|
| `quality.rules`（小标题） | 静态规则命中（只含命中过的规则） | **静态规则目录与命中次数** |
| 说明一（`quality.rulesCatalog`，新增，带 `data-testid="quality-rules-catalog"`） | —（原文在 `quality.rulesAbsent`，见下） | 服务端按**冻结的规则目录**逐条给出，`count = 0` 表示该规则在这个范围内一次都没有命中过；目录之外的历史 `rule_id` 会被**追加在目录之后**；**本页不过滤 0 命中行** |
| 说明二（`quality.hitsHint`） | 契约的用意：一条从不在任何提交上命中的规则，要么没用，要么写错了 | 契约要的正是把从不命中的规则也摆出来：一条从不在任何提交上命中的规则，要么没用，要么写错了 |
| `quality.rulesAbsent` | "契约没有给出规则目录…B1 拿不到规则全集" | **已删除**（该句不成立：那份目录正是本页渲染的东西） |

同时给 `src/testing/contractFixtures.ts` 的 `evaluationQualitySummary` 补了两条：目录内 0 命中的
`STAT-PY-013`（否则"0 也要显示并解释"这条断言会退化成永远成立的空检查）与目录外的 `ZZ-LEGACY-001`。

### 复核后新增/修改的断言（TC-B1-103 + 一条七性质回归，+2 用例 → 该文件 15 例）

- `STAT-PY-013` 那一行**在场**，且计数列**恰为 `0`**（不是被过滤、也不是"无数据"）；
- 说明里出现"冻结的规则目录""一次都没有命中过""不过滤""追加在目录之后"；
- 目录外的 `ZZ-LEGACY-001` 也被呈现；契约的用意（"要么没用，要么写错了"）仍在页面上；
- `static_rule_hits: []` 时给空态，**不编造目录条目**；
- 新增一条把复核时读到的**七条诚实性**钉成断言：去标识化由契约强制且不下钻、样本量在计数**上方**
  （`compareDocumentPosition`）并转述 `attempt_count` 定义、`unmeasured_runs` 单列且写明"不并入"、
  三态安全各自成列且第三项直写"从未检查（不等于安全）"、`infrastructure_error` 单列"不是难度"、
  色带与"样本太少"两条**阅读辅助**明确标注（"不是契约规则"）、`attempt_count = 0` 给
  "没有可读的信号"而**不给结论**；
- TC-B1-087 中"规则表说明「从未命中」的清单不在本页"那半句已删除。

### 同一轮复核中被本次真机读数**升级**的边界

| 端点 | 0.13.0 时点 | 0.16.0 复核（真机，`curl.exe` + 教师令牌） |
|---|---|---|
| `GET .../commentary` | 200/`ready`，**响应里没有** `advisory_score` 键 | 200/`ready`，键**已出现**、值为 **`null`**；`POST` → **202 / 7497 ms**，响应体同样 `advisory_score: null` ⇒ "`null` 不渲染 + 说明本次没有产生"这条路径**已真实联通**；**非空参考分仍未出现** |
| `GET /intake/quarantines?resolved=false&limit=5` | 404（未实现） | **200**，`{"items":[],"next_cursor":null}`（库中无隔离记录）⇒ 读路径联通；**页面渲染真实行与归属写路径仍只有夹具证据** |
| `GET .../quality-summary?question_version_id=…` | 404（未实现） | **200**（读数见上表）⇒ 已真实联通；**非空数据分支仍只有夹具证据** |
| `GET .../appeal-pack?view=teacher` | 未触达 | **404**：`The evaluation run exists but has no report yet (state: failed，终态原因 infrastructure_error). A report exists only after a successful run.` ⇒ 该端点已实现，但本部署所有运行都是 `failed`，**200 路径仍未触达** |

页面上原先那些"端点尚未实现/证据只到夹具"的**常驻横幅**已由后续批次删除（理由：静态文本不得断言
另一个模块的实现进度，它没有过期机制，端点一上线就会与旁边的实时数据直接矛盾）。因此本文件正文里
"页面挂着证据边界横幅"的表述只描述当时状态，现在由 `docs/` 记录这类边界。

### 本轮门禁（最终代码，真实读数）

```text
$ node node_modules/eslint/bin/eslint.js . --max-warnings 0
eslint_exit=0

$ node node_modules/typescript/bin/tsc -b --pretty false
tsc_exit=0

$ node scripts/generate-api-types.mjs --check
API types match MAIN/contracts.
apicheck_exit=0

$ node node_modules/vitest/vitest.mjs run
 Test Files  32 passed (32)
      Tests  265 passed (265)

$ node node_modules/vite/bin/vite.js build
dist/assets/index-BRprZkxb.js                  583.93 kB │ gzip: 176.63 kB
✓ built in 37.94s
vite_exit=0
```

**浏览器复核（同一轮，对着 `http://127.0.0.1:18000/app`，服务的正是这次 `vite build` 的产物）**：
顶栏显示 `契约 0.16.0`；`/app/quality?question_version_id=34d07765-49ea-4171-997b-dd25693716ff` 上
小标题为 **"静态规则目录与命中次数"**，**15 行全部渲染、`zeroRows = 15`**（`STAT-PY-001`…`STAT-SH-021`，
计数列皆为 `0`），说明里含"冻结的规则目录""一次都没有命中过""不过滤""追加在目录之后"，
**页面上不再出现"拿不到规则全集"**（`oldClaimPresent = false`）；同屏仍齐全：去标识化说明、
`unmeasured_runs` 单列、"从未检查（不等于安全）"、`attempt_count = 0` 的
"没有任何评测运行…没有可读的信号"与"样本太少"提示。

### 未纳入本次改动、需要 MAIN 裁决的一处观察

**契约 `CHG-0021` §2.5 把"列出全部"的唯一表达定为"省略 `resolved`"，而 B1 的筛选器只有
「未处理 / 已处理」两个选项，因此"全部"这一种请求形式在界面上不可达**（页面每次请求都显式带
`resolved`，这条本身有反向断言并有 `CHG-0021` 的依据）。它是"契约可表达、界面不可达"的同类缺口，
但**不在本次复核要求的范围内**，且与另一批次已落地的 TC-B1-102 的取舍直接相关，因此这里只登记、不改动。
