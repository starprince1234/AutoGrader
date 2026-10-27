# Phase 07 复核工作区（P13：邮件隔离 + 题目质量）

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
| `node scripts/generate-api-types.mjs --check` | `API types match MAIN/contracts.`（契约 `0.23.0`） |
| `node node_modules/vitest/vitest.mjs run` | **48 文件 / 417 用例全部通过**（其中 `src/features/review` 4 文件 / 29 用例；其余为成员页面与共享组件） |

新增测试覆盖：URL 三态与跨标签参数清理（6）；邮件标签的请求参数/403/空态/详情/不可归属/归属体/
409/空候选/管理员未关联行（9）；质量标签的深链聚合/无版本不发请求/非法版本/B3 403 与 404 不拉聚合/
0 次评测/小样本/5xx/时间校验/RFC 3339 与清除时间（10）。

## 4. 验收反馈修正（2026-10-27，老师可读性 + 排版）

上线后按老师反馈修了两类问题，均只改 P13 自己的页面与文案，没有改共享组件、全局 CSS 或 AppShell：

1. **去掉字段名/实现说明**：范围提示改为「只显示你有权查看的课程与课序的邮件记录。」；详情说明改为
   「这里只显示邮件的基本信息和已记录的原因，不会打开邮件正文或附件。」；时间说明去掉 RFC 3339 与
   URL 措辞；计数说明改为「只显示汇总数字，不包含学生、提交内容或代码。」；小样本提示去掉"契约规则"。
2. **表格与控件对齐**：
   - 邮件表改为 6 列（记录时间 / 课程课序 / 邮件来源 / 原因 / 处理状态 / 操作），邮箱与
     Message-ID/UID 合并为「邮件来源」两行，原因与简短说明合并为「原因」两行；表格在 1280 宽下
     不再横向滚动，操作按钮固定为「详情 + 归属」同一行（`.row-actions`，不换行）。
   - 时间列到分钟；Message-ID 只显示前缀（完整值仍在详情与 `title`）；说明文字截断（完整值在详情抽屉）。
   - 质量页筛选去掉字段下方长提示（改为一行说明），全部字段等高对齐；五个汇总卡改为自适应
     165px 下限，桌面一行排满；安全口径改成三条列表、失败类别里的系统原因改成一行紧凑展示，
     不再出现"4+1"的卡片台阶。
3. **本机视觉复核**：用本地 mock API 提供夹具数据，Edge headless 在 1280 与 500 两种宽度截图核对
   （不是生产环境，也不替代线上验收）；390 宽度受 headless 最小窗口 500 限制，未单独截图。

## 5. 未验证 / 明确边界

1. **未部署、未真机**：`/review` 尚未在生产出现。行级隔离、归属写路径、质量非空聚合都只有
   组件/契约夹具与 B2 单测证据；生产浏览器的登录态、真实数据量与 390 宽度复核未执行。
2. **浏览器端**：P04 的入口链接由成员二维护、P07 的入口由成员三维护；本批只做了"旧路由被删除"
   所必需的两处目标地址修改，入口文案/位置以他们的页面为准。
3. **名单成员关系**：B2 无法复核"该学生是否仍在该课序"（无 B2→B4 名单通道）；页面每次从 B4 现取
   候选，服务端重查记录行 scope 与调用者权限。过期候选的最终拒绝仍依赖 B4 侧变更，这是登记缺口。
4. **质量聚合的跨课程授权**：B2 quality-summary 仍只判角色（题目→课程属 B3）。B1 先经 B3 校验
   版本可见性，正常 UI 路径不会向 B2 拉无权版本；**直接调用 B2 的向量未关闭**，不声称已隔离。
5. **共享文件**：`router.tsx` 自带"改动前在 docs/phases/active 登记"的约定——本文件即登记；
   `i18n/index.ts` 的旧 `quarantine.*`/`quality.*` 键已替换为 `review.*`。

## 6. 老师可用性与权限补充（2026-10-27，契约 0.23.0）

上线后老师提出三点：作业页两个按钮不齐、邮件页只有查询框没有处理列表、课程下拉可能出现无权课程。
本批的修正（只改 P13 页面与 P04 入口的布局，不动 AppShell/共享 CSS/其他成员页面逻辑）：

1. **作业页按钮**：标题与「新建作业 / 邮件提交」改为 `.panel__head` + `.row-actions`，
   两个按钮同一基线与同一行；窄屏也不会只剩一个按钮单独换行。
2. **邮件页默认就是待处理列表**：`listEnabled` 不再要求先选课程（staff 只要令牌带作用域就查，
   服务端仍按行级 scope 过滤）；列表字段按老师要求改为「邮件摘要（含记录时间）/ 识别失败原因 /
   课程课序 / 处理状态（含处理人+时间）/ 操作」。
3. **处置**：每行提供「归属学生」与「驳回」。归属弹窗仍只提交 `student_id/note`，候选来自
   B4 当前课序名单；驳回弹窗提交 `note`（可空），不写学生、不触发评测；两者成功后统一刷新列表。
   已处理行显示「已归属/已驳回」与「处理人：… · 时间」；0016 之前的历史行显示「未记录」。
4. **课程/课序下拉按令牌 scope 过滤**：邮件与质量两个标签都只展示 scope 覆盖的课程；课序若
   scope 只给了部分课序，则只列这些课序（`section_id=null` 的课程级 scope 才列全部）。
   URL 指向无权课程时回落到第一门有权课程，不把无权 ID 发出去再展示 403。
5. **契约 0.23.0 / 迁移 0016**：B2 新增 `dismissIntakeQuarantine`、视图 `subject`/`resolution`/
   `resolved_at`/`resolved_by`；B1 重新生成类型并新增 `useDismissQuarantine`。B2 侧细节见
   `B2/docs/phases/active/phase-11-quarantine-decision-trail.md`。

**限制**：处理人只显示令牌主体（本人显示"你"，他人显示短 id）——B2 不知道用户名，B1 也不跨模块
调管理员用户目录；这不是"显示不出来"，而是"只能显示可核对的标识"。
