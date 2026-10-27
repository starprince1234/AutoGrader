# Phase 06 复核工作区（P13：邮件隔离 + 题目质量）

- **口径**：P13 是唯一复核页 `/review?tab=mail|quality`。旧 `/intake/quarantines` 与 `/quality`
  **已删除**（不保留别名或隐藏路由）；不占主导航，入口来自 P04（邮件，带课程/课序）与 P07
  （质量，带 `question_version_id`）。
- **本批同时修复了服务端越权**：B2 隔离记录新增行级课程范围（`scope_course_id/scope_section_id`，
  迁移 `0015`），staff 只能看到并归属自己作用域内的行，未关联行只对管理员可见。B1 的课程/课序
  **只是筛选**，越权由服务端 403，页面原样展示。服务端细节见
  `B2/docs/phases/active/phase-10-quarantine-row-scope.md`。

## 1. 文件与职责

| 文件 | 职责 |
|---|---|
| `src/features/review/search.ts` | URL 查询串的解析/回写（纯函数）：tab 归一、丢弃契约外的 `reason_code`/非法 `resolved`、切标签移除另一标签参数、改筛选丢弃游标 |
| `src/features/review/reasonCodes.ts` | 19 项原因码的稳定中文标签 + 可归属集合（fail-closed）；类型派生自生成类型 |
| `src/features/review/useReviewMail.ts` | 隔离列表（`resolved` 显式/省略、课程/课序筛选、游标分页）与归属 mutation；身份进查询键 |
| `src/features/review/useQualityVersions.ts` | 课程 → 题目（含版本历史）→ 版本选项；深链版本的元信息（先经 B3） |
| `src/features/review/useReviewQuality.ts` | 质量聚合查询（身份 + 版本 + 时间范围进键）与时间互转、样本门槛 |
| `src/features/review/MailReviewTab.tsx` | 邮件提交标签：筛选、结果表、详情抽屉、归属抽屉 + 嵌套确认、403/409/空态/加载更多 |
| `src/features/review/QualityReviewTab.tsx` | 题目质量标签：课程/版本/时间筛选、顶部五元计数、用例/安全三态/资源/失败类别/静态规则、返回题目版本 |
| `src/features/review/ReviewPage.tsx` | h1「复核」+ 两个标签；切换标签只切视图 |
| `src/app/router.tsx` / `routeComponents.tsx` | 新增 `/review`（`validateSearch: parseReviewSearch`）并删除两条旧路由 |
| `src/app/queryClient.ts` | `reviewQuarantines(identity, filter)` / `reviewQualitySummary(identity, filter)` |
| `src/features/questions/QuestionDetailPage.tsx` | 质量入口改为 `/review?tab=quality&question_version_id=…`（编译必需的最小改动，P07 仍属成员三） |
| `src/features/assignments/AssignmentsPage.tsx` | P04 的"待处理邮件"入口改为 `/review?tab=mail&course_id&section_id`（编译必需的最小改动，P04 仍属成员二） |

已删除：`src/features/intake/**`、`src/features/quality/**` 及其测试。

## 2. 关键决定（与任务书对齐）

1. **URL 承载筛选，不承载内容**：`tab/course_id/section_id/resolved/reason_code/cursor/question_version_id/from/to`；
   不放姓名、学号、student_id、备注正文。`resolved` 的 URL 三态是 `true|false|all`，请求时 `all`
   省略参数（契约没有"两者都要"的取值）；URL 省略时按默认「未处理」处理并显式发送 `false`。
2. **`cursor` 是起始位置**：刷新从该位置重开；"加载更多"追加下一页但**不**把游标改成最后一页
   （单一游标无法表示多页状态，声称"已加载数=总数"是假话）。改筛选必然丢游标。
3. **未关联 ≠ 未提供**：`scope_course_id === null` 显示「未关联」（只可能出现在管理员视图）；
   字段缺失（旧版服务端）显示「未提供」。两者不能混读。
4. **归属安全**：只有未处理且 `reason_code ∈ {not_uniquely_matched, ambiguous_match}` 有入口；
   候选来自 B4 当前名单（换课序时 `invalidateQueries` 强制现取）；确认前不发 POST；体只有
   `student_id/note`；409 提示刷新且不显示成功；成功后才提示「邮件已归属」。
5. **质量页先经 B3 再经 B2**：深链版本先 `GET /question-versions/{id}`（B3 对无权版本 403），
   失败时**不发** quality-summary 请求；没有版本时也**不发**任何题库/聚合请求。
6. **顶部计数与正文口径不重复**：顶部固定显示 `attempt_count`/`unmeasured_runs`/三态安全计数；
   正文的"安全检查/资源测量"是**口径说明**（同一数字画两遍会被读成两个指标）。
7. **共享组件边界**：`ConfirmDialog`/`Toast` 尚不存在，本批用现有 `Modal` 做嵌套确认、用内联
   `Banner` 做成功提示（不新增共享组件、不改 AppShell/全局 CSS）。成员一提供后替换即可。

## 3. 本机真实执行的验证（读数）

| 命令（等价于 README 中的 pnpm 脚本，直调 node_modules 以绕开沙箱管道限制） | 结果 |
|---|---|
| `node node_modules/typescript/bin/tsc -b --pretty false` | **通过**（exit 0） |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | **通过**（0 error / 0 warning） |
| `node scripts/generate-api-types.mjs --check` | `API types match MAIN/contracts.`（契约 `0.19.0`） |
| `node node_modules/vitest/vitest.mjs run` | **43 文件 / 374 用例全部通过**（其中 `src/features/review` 新增 3 文件 / 25 用例） |

新增测试覆盖：URL 三态与跨标签参数清理（6）；邮件标签的请求参数/403/空态/详情/不可归属/归属体/
409/空候选/管理员未关联行（9）；质量标签的深链聚合/无版本不发请求/非法版本/B3 403 与 404 不拉聚合/
0 次评测/小样本/5xx/时间校验/RFC 3339 与清除时间（10）。

## 4. 未验证 / 明确边界

1. **未部署、未真机**：`/review` 尚未在生产出现。行级隔离、归属写路径、质量非空聚合都只有
   组件/契约夹具与 B2 单测证据；浏览器 390/768/1280 复核未执行。
2. **浏览器端**：P04 的入口链接由成员二维护、P07 的入口由成员三维护；本批只做了"旧路由被删除"
   所必需的两处目标地址修改，入口文案/位置以他们的页面为准。
3. **名单成员关系**：B2 无法复核"该学生是否仍在该课序"（无 B2→B4 名单通道）；页面每次从 B4 现取
   候选，服务端重查记录行 scope 与调用者权限。过期候选的最终拒绝仍依赖 B4 侧变更，这是登记缺口。
4. **质量聚合的跨课程授权**：B2 quality-summary 仍只判角色（题目→课程属 B3）。B1 先经 B3 校验
   版本可见性，正常 UI 路径不会向 B2 拉无权版本；**直接调用 B2 的向量未关闭**，不声称已隔离。
5. **共享文件**：`router.tsx` 自带"改动前在 docs/phases/active 登记"的约定——本文件即登记；
   `i18n/index.ts` 的旧 `quarantine.*`/`quality.*` 键已替换为 `review.*`（共 120 条）。
