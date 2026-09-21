# Phase 04 题库标签覆盖度视图（CHG-0020，契约 `0.14.0`）（当前阶段）

本文件记录本轮**真实执行的命令、原始读数、未验证项与契约观察**。状态词只用
`已完成` / `部分` / `阻塞` / `未验证`；未跑过的命令不写成通过。

## 目标与范围

契约 `0.14.0` 的 `CHG-0020` 新增 `GET /api/v1/questions/coverage`
（`operationId: getQuestionTagCoverage`，响应 schema `QuestionTagCoverage`），
把"哪些知识点有多少题、有多少题一个标签都没有"从"只能靠人翻页数"变成可读的事实。
B1 此前的 `src/` 下对该端点**零调用点**：能力在契约里，界面上没有出口。

本轮在**题目列表页**提供这个视图，并按契约把三条读法逐条落实（见下）。

范围外：`MAIN/contracts/**`（未修改）、`B1/module.yaml`（未修改）、`B1/contracts.lock`（未手改）、
`B1/package.json` 的依赖（未变）、服务端行为。未引入任何 UI 组件库或状态管理库。

## 完成条件

1. 覆盖度有真实界面出口，`total_questions` / `tagged_questions` / `untagged_questions` 与每个标签的
   `question_count` 都读得出来。
2. 契约点名的三条读法各有断言：**唯一**可校验的恒等式是 `tagged + untagged == total`、
   `untagged_questions` 单独呈现、标签顺序按服务端给的顺序原样渲染（不再排一次）。
3. `course_id` 缺失时不发请求；`404`（B3 未实现）是信息态且**不伪造数据**；`403` 原样带出服务端 detail。
4. 点标签能复用**既有**的题目列表标签筛选。
5. 门禁全绿：`eslint --max-warnings 0` / `tsc -b` / `generate-api-types --check` / `vitest` / `vite build`。
6. 文档如实区分"对着契约构建"与"真联调过"：本轮**没有任何真机读数**。

## 信息架构：为什么放在题目列表页而不是新路由

候选位置有三个，选择与理由如下（这是本轮唯一一处需要判断的落点）：

| 候选 | 判断 |
|---|---|
| **题目列表页 `/questions`（选中）** | 端点的作用域参数（`course_id` 必需）与默认值（`published_only: true`）**就是** `listQuestions` 的那一对；放在同一页可以让两者**共用**同一份页面状态，于是"覆盖度说 16 道题"与"下方列表能翻出哪些题"永远来自同一作用域。列表页已经有 `tags` 筛选（`parseTags` + 重复参数），点标签即填进它，不需要新造第二套筛选机制 |
| 新路由 `/questions/coverage` | 需要一个**自己的**课程选择器，于是同一屏上会出现两个可能不一致的作用域（"覆盖度按课程 A 统计、列表按课程 B 显示"）。要么把筛选状态搬进 URL 搜索串（改动既有筛选状态模型与已有 9 个列表用例），要么接受这种矛盾。收益只是"多一个顶级导航项"，不划算 |
| 题目详情页附近 | **不放**。覆盖度是**课程级分布**，题目详情页的作用域是单个 `question_id`（该页现有的质量视图入口也是按 `question_version_id`）。把课程级分布挂到题目级页面上会暗示一个不存在的对应关系 |

另一个由此确定的取舍：**没有**为它新增 `src/auth/visibility.ts` 规则。契约（`CHG-0020` §2.1）写明鉴权是
"`bearerAuth` + 该课程的读权限，**与 `listQuestions` 完全一致**"，而 `listQuestions` 的界面就是本页、
守卫是 `RequireAuth`。再写一个 `canViewQuestionCoverage`（返回"所有登录角色都可见"）只会把
"契约没有的角色限制"变成一个看似有约束的开关。服务端仍是判定方，`403` 原样展示（有用例）。

## 实际变更（落点）

| 能力 | 文件:行 | 说明 |
|---|---|---|
| 契约类型 | `src/api/types.ts:66`/`:75` | `QuestionTagCoverage` + `TagCoverageRow`，全部来自生成类型；注释写明"逐标签计数不等于总数" |
| 查询键 | `src/app/queryClient.ts:38` | `questionCoverage(courseId, publishedOnly)`：作用域进键（同一门课的两种 `published_only` 是两个分布） |
| 读取 | `src/features/questions/useQuestions.ts:95` | `useQuestionCoverage`：`enabled: Boolean(courseId)`（无作用域不发请求）；显式发送契约声明的**两个**参数 |
| 覆盖度面板 | `src/features/questions/QuestionCoveragePanel.tsx:232` | 三态 + `404` 信息态 + `403` 原文态 + 证据边界横幅（**总是**渲染） |
| 恒等式 | `src/features/questions/QuestionCoveragePanel.tsx:207` | 唯一被展示与校验的恒等式 `tagged + untagged == total`；不自洽时如实报告（不"修正"服务端给的数） |
| 逐标签表 | `src/features/questions/QuestionCoveragePanel.tsx:76` | 顺序原样渲染；每个标签的题目数 + 只相对最多标签的条形；点标签复用既有筛选 |
| 标签可否筛选 | `src/features/questions/QuestionCoveragePanel.tsx:69` | 复用 `parseTags` 判断；归一化后与原值不同则不提供筛入口（见"契约观察"） |
| 403 / 404 分支 | `src/features/questions/QuestionCoveragePanel.tsx:266`/`:274` | `403` 逐字带出 `problem.detail`；`404` → "尚未在 B3 实现"信息态且明说**不**做客户端兜底 |
| 空库信息态 | `src/features/questions/QuestionCoveragePanel.tsx:91` | `tags: []` + `untagged == total` 是正常结果，不是错误 |
| 挂载与标签跳转 | `src/features/questions/QuestionListPage.tsx:132` | 与列表共用 `courseId` / `includeDrafts`；`onSelectTag` 直接写既有的 `tagFilter` |
| 文案 | `src/i18n/index.ts:212`（`questions.coverage*`）、`:662`（`common.declaredContractB3`） | 证据边界声明按提供方分条：B2 用既有词条，B3 用新词条（旧词条的正文写着 B2，用在 B3 端点上就是假话） |
| 样式 | `src/styles.css:785`/`:788` | `.coverage-bar__track`/`.coverage-bar`（条形只在标签之间比较）、`.identity-broken`（不自洽时的视觉提示，不是错误态） |
| 契约夹具 | `src/testing/contractFixtures.ts:274` | `questionTagCoverage()`：`12 + 4 == 16` 而逐标签 `9 + 9 + 3 = 21 > 16`，顺序为契约顺序且**非**字母序 |
| 测试 | `src/features/questions/QuestionCoveragePanel.test.tsx`（13 用例）、`QuestionListPage.test.tsx`（11 用例，新增 2） | 见下 |

**未改动**：`MAIN/contracts/**`、`B1/module.yaml`、`B1/contracts.lock`、`B1/package.json` 依赖、
既有 3 处 `common.declaredContract` 调用点、`src/auth/visibility.ts`。

## 契约的三条读法如何落实（对应 `CHG-0020` §2.2 与 §8）

1. **恒等式**：页面只展示 `tagged_questions + untagged_questions == total_questions` 这一条，
   并在夹具上断言 `12 + 4 = 16` 与"只有这一条"。**反向断言**：夹具的逐标签之和 `21`（> `total_questions`）
   在页面上**不存在**（`queryByText("21")` 与整个面板的 `textContent` 双重检查），标签表只有
   3 列（标签 / 题目数 / 条形），页面上没有任何"标签合计"单元格。条形长度相对**最多的那个标签**，
   因此它不承载"占题目总数的比例"（那种比例合计会超过 100%）。
2. **`untagged_questions` 单独一格**：独立卡片 + 明说"必须单独看这一格"，并有反向断言
   （该卡片内不出现任何标签计数 `9`/`3`，标签表内不出现 `4`）。与质量热力图里 `unmeasured_runs` 同一条纪律。
3. **顺序原样**：`rows={coverage.tags}` 一行都不排；用例断言渲染顺序**等于**夹具顺序
   （`process-scheduling`, `unix`, `scheduling`）且**不等于**字母序排列，并断言页面文字里有"不再排一次"。

另外两条诚实性：`404` 与 `403` 都不渲染成空态（反向断言没有 `coverage-body`、没有"暂无数据"）；
`course_id` 缺失时不发请求（`stub.requests` 长度为 0）。

## 门禁（本轮最终代码上的原始输出）

```text
$ node scripts/generate-api-types.mjs
generated src\api\generated\common.ts
generated src\api\generated\b2.ts
generated src\api\generated\b3.ts
generated src\api\generated\b4.ts
gen_exit=0
# 确认：src/api/generated/b3.ts:588 QuestionTagCoverage / :1133 getQuestionTagCoverage

$ node node_modules/eslint/bin/eslint.js . --max-warnings 0
eslint_exit=0

$ node node_modules/typescript/bin/tsc -b --pretty false
tsc_exit=0

$ node scripts/generate-api-types.mjs --check
API types match MAIN/contracts.
apicheck_exit=0

$ node node_modules/vitest/vitest.mjs run
 ✓ src/features/questions/QuestionCoveragePanel.test.tsx (13 tests) 643ms
 ✓ src/features/questions/QuestionListPage.test.tsx (11 tests) 1455ms
 Test Files  31 passed (31)
      Tests  240 passed (240)
   Start at  18:01:27
   Duration  32.04s (environment 74%, setup 19%, tests 4%, import 3%, transform 1%)
vitest_exit=0

$ node node_modules/vite/bin/vite.js build
dist/index.html                                  1.36 kB │ gzip:   0.82 kB
dist/assets/index-Q6_L646q.css                  16.63 kB │ gzip:   3.93 kB
dist/assets/index-DDhP1Cgg.js                  570.05 kB │ gzip: 172.56 kB
dist/assets/index-C1rdNg7P.js                1,134.53 kB │ gzip: 381.47 kB
dist/assets/editor.main-Bp1u7A_w.js          3,772.66 kB │ gzip: 973.88 kB
(!) Some chunks are larger than 500 kB after minification. Consider:
✓ built in 41.72s
vite_exit=0
```

（`eslint`/`tsc` 无任何输出且退出码为 0；`vite build` 仍有既有的 chunk 体积警告——Monaco
`editor.main` 3.77 MB，见 [`../../README.md`](../../README.md) 已知限制第 4 条，不是本轮引入。）

构建产物侧的核对（`dist/assets/index-DDhP1Cgg.js` 逐串计数，不是从源码推断）：
`/api/v1/questions/coverage` 出现 1 次、`标签覆盖度` 出现 1 次、`0.14.0` 出现 1 次
（顶栏的契约版本由 `vite.config.ts` 从 `contracts.lock` 注入，模板是 `契约 {version}`）。

- 测试规模：**31 个文件 / 240 个用例**（上一阶段为 30 文件 / 225 用例），新增 **15** 个用例。

## 新增测试（15 个用例）

| 文件 | 用例数 | 关键断言 |
|---|---|---|
| `src/features/questions/QuestionCoveragePanel.test.tsx` | 13 | 无课程作用域 → **零请求**并说明 `course_id` 是必需参数；只发 `course_id` + `published_only` 两个参数（键集合断言）且 `published_only` 随调用方变化；**404 → 信息态**（"尚未在 B3 实现"、**只请求一次**、不渲染任何计数、明说"不会回退到用列表在客户端数一遍"、证据边界横幅仍可见）；**403 → 逐字带出服务端 detail** 且不渲染成空态/错误态；500 走通用错误态；恒等式 `已标注 12 + 未标注 4 = 16` 与"只有这一条"；三项不自洽（`total=17`）时如实报告且照原样显示 `17`；**反向断言逐标签之和 `21` 不在页面上**；`untagged` 单独一格且格内不含标签计数；空库（`tags=[]`、`untagged==total`）是信息态且恒等式 `0 + 7 = 7` 仍在；渲染顺序 == 契约顺序且 ≠ 字母序 + "不再排一次" + 每个标签的题目数 + 条形相对最多标签；点标签回调 `onSelectTag("unix")`；形状不符的标签**没有**筛入口、计数照原样显示 |
| `src/features/questions/QuestionListPage.test.tsx` | 11（新增 2） | 选课程后覆盖度与列表**落在同一作用域**（首次 `published_only=true`；切「包含草稿」后覆盖度与列表请求**同时**变成 `false`）；未选课程时覆盖度**一格请求都不发**；点覆盖度里的标签即填入既有标签筛选输入框，且列表请求以重复参数 `tags=unix` 发出 |

生产代码**没有**引用 `src/testing/**`（新测试全部通过 `src/testing/**` 的契约夹具与 HTTP 边界桩驱动）。

## 未验证项（诚实边界，**不得**读成已验证）

| 能力 | 证据到哪为止 | 原因 |
|---|---|---|
| 题库标签覆盖度视图（`getQuestionTagCoverage`） | 生成类型 + 契约夹具 + 组件测试（15 用例） | **对着已声明契约构建、未经真实 B3 验证**：B3 正在实现该端点，本轮**未对运行中的 host 发过任何请求**（连一次 `curl` 都没有）。页面上挂着"B3 尚未实现"的常驻证据边界横幅，`404` 渲染为信息态 |
| 标签跳转筛选的端到端 | 组件测试（复用既有 `tags` 重复参数） | 同上：跳转落到的 `/api/v1/questions?tags=…` 是既有已联通路径，但"点标签 → 服务端返回该标签的题"这条链路依赖覆盖度端点先返回数据 |
| 多标签重叠的真实分布 | 契约 + 夹具（刻意让逐标签之和大于总数） | 夹具是按契约形状构造的，不是真实题库的读数；真实分布要等 B3 落地 |

## 契约观察 / 需要 MAIN 表态项（**未自行裁决**）

1. **`QuestionTagCoverage.tags[].tag` 的形状约束弱于它要喂给的查询参数**（已登记为
   `../collaboration/interfaces.md` 缺口 8）。`CHG-0020` §2.2 写"`tags[].tag` 复用契约已有的标签
   形态约束"，但契约文件里该字段只有 `minLength: 1, maxLength: 40`，**没有** `pattern`；
   而 `listQuestions.tags`（查询参数）与 `QuestionDraftInput.tags`（写路径）都声明了
   `^[a-z0-9][a-z0-9-]*$`（`QuestionPublic.tags` 连长度都没约束）。三处不一致。
   B1 的处置：**原样渲染**服务端返回的 tag 与它的计数，只有当该值**归一化后与原值完全相同**
   （即能原样作为 `listQuestions.tags` 的取值）时才提供"点标签看这些题"的入口，否则只显示计数并
   在 `title` 里说明原因。**不**替服务端改写这个值去查询——改写会让"我点的标签"与"实际筛选的标签"
   变成两个不同的东西，而用户只会看到零结果。请 MAIN 决定是否把 pattern 补进该字段。
2. **`getQuestionTagCoverage` 的授权口径写在 CHG 正文而非 OpenAPI 正文**：`CHG-0020` §2.1 写
   "`bearerAuth` + 该课程的读权限，与 `listQuestions` 完全一致"，但 `b3.yaml` 里该端点只有
   `401`/`403`/`422` 响应，没有 `description` 说明授权范围。B1 按"与题目列表同一守卫
   （`RequireAuth`，不新增角色限制）"处理——这是**照 CHG 正文执行**，不是自行推断，但下一个消费者
   只看 OpenAPI 时读不到这一点，因此登记待 MAIN 确认是否补进契约正文。
3. **该端点未声明 `404`**：`b3.yaml` 的响应只有 `200/401/403/422`。路径里没有任何资源标识符
   （`course_id` 是查询参数，缺失/非法走 `422`），因此 B1 把 `404` 读作"这条路由还不存在"，
   即端点尚未实现——这正是本部署当前的情形。这与 `GET /commentary` 的 `404`（B2 实现定义的
   "该运行还没有评语"）**语义不同**，两者不可互相套用；B1 的两处文案也刻意不同。
4. 一处**页面级**推断：`published_only` 由 B1 **显式**发送（契约已声明默认值 `true`，因此这不是
   依赖未声明的默认行为），目的是让它与同一页面的列表开关始终同值。若 MAIN 认为客户端应当省略该参数
   以走服务端默认，需要明确（B1 的选择是"两个数字必须同作用域"优先）。

## 与既有阶段的关系

- 本阶段**不替代** `phase-01`/`phase-02`/`phase-03` 中已记录的结论；`phase-03` 记录的是
  `CHG-0014`/`CHG-0015` 与 `RQ-T-20/T-30`（B2 侧），本轮是 `CHG-0020`（**B3** 侧），
  两者的证据边界相同（生成类型 + 契约夹具 + 组件测试），但**提供方不同**，因此页面上的
  证据边界文案也分成了两条（`common.declaredContract` / `common.declaredContractB3`）。
- `docs/README.md` 的"当前阶段记录"已指向本文件。
