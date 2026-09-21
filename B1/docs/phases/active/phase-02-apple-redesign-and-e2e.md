# Phase 02 Apple 设计语言重做与端到端回归（当前阶段）

## 目标与范围

Phase 01 交付了功能完整的业务 UI，但视觉评审（截图 + 独立视觉复核）给出的结论是"能用、
不像产品"：登录按钮在禁用态是 40% 透明度的蓝（看起来像坏了）、卡片撑满 1080px 内容列、
表格行高随内容跳动、图表用的是深色主题遗留的硬编码颜色、多处面板标题重复。
本阶段把这批问题一次改完，并在**真实运行的四模块系统**上做端到端回归。

范围外：任何契约改动（本轮 B1 未改 `MAIN/contracts/**` 与 `contracts.lock`）；服务端行为。

## 完成条件

1. 设计语言统一为 Apple 式（light-first + 自动深色），且**不新增/不重命名任何既有 class**，
   组件 `.tsx` 的业务结构不动。
2. 视觉评审列出的缺陷逐条消失，并在浏览器里逐页复验。
3. 端到端：登录 → 题目 → 详情/版本 → 作答 → 提交 → SSE 终态 → 成绩 → 名单 → 名单导入 →
   用户与角色，全部在真实 MAIN host + B2/B3/B4 上跑通。
4. 门禁：`tsc -b` / `eslint --max-warnings 0` / `api:check` / `vitest` / `vite build` 全绿。

## 实际变更

### 设计系统（`src/styles.css`）

| 项 | 改法 | 为什么 |
|---|---|---|
| 禁用主按钮 | 去掉 `opacity: .4`，改为 `background: var(--color-surface-3)` + 弱化文字色 | 半透明蓝叠在白卡片上读起来像"加载中/坏了"，灰色才读得出"还不能点" |
| 控件高度 | `.button` 与 `.field input/select` 统一 40px；登录卡片内按钮 44px | 混排控件落在同一基线 |
| 表格 | 行高 48px、`td` 不换行、行分隔用 `--color-surface-3`（比表头线更淡）、表内按钮 32px | 行高随内容跳动是"表格看起来业余"的主因；换行把短值折成两行，行高直接从 48 变 68 |
| 标识符 | 新增 `.mono-id`（等宽、12px、弱化色、`nowrap`）+ `shortenId()`（UUID 显示前 8 位，全文留在 `title`） | 一列 36 字符十六进制会压过真正要读的列，两列就能把表格挤到横向滚动 |
| 面板内边距 | `20px 24px` | 横向留白让表格贴到卡片边缘而不显局促 |
| 导航对齐 | 顶栏用 `padding-inline: max(24px, calc((100% - 1080px)/2 + 24px))` | 品牌/导航/身份与下方卡片列对齐；顶栏仍全宽，毛玻璃背景覆盖整页 |
| 登录页 | 新增 `.auth-gate`（420px 居中）+ 全宽 44px 按钮 | 两个字段的表单横跨整屏会让输入框和按钮失去关联 |
| 步骤条 | 新增 `.steps`（横排） | 竖排 5 行文字像清单，横排才读得出"这是流程" |
| 表内控件 | 新增 `.table select/input` 一套 token 化外观 + 自绘箭头 | 表格里的角色下拉不在 `.field` 里，退回浏览器默认外观，同页两种控件 |
| 图表颜色 | `GradesChart` 改为 `getComputedStyle` 读 `--color-accent` / `--color-text-faint` / `--color-border` | 原来硬编码深色主题的 `#5b9dff` / `#2b303b`，改版后变成"深色网格线上的亮蓝柱子"；读 token 后主题（含自动深色）只有一个真源 |

### 功能与诚实性缺陷（本轮实跑发现并修复）

| # | 缺陷 | 现象 | 修复 |
|---|---|---|---|
| 1 | 匿名访客首页 hero 显示 **403 文案** | `session.rolesKnown === false` 时落到 `common.forbidden`，于是落地页对"还没登录"的人说"无权限访问该页面（403）" | 未认证走新的 `home.signInHint`；`common.forbidden` 只留给"已登录但角色未知"的 fail-closed 分支 |
| 2 | 顶栏对匿名访客显示"登录 · 无 · 契约 0.1.0" | 角色段在未登录时输出 `无` | 未登录只输出 `未登录 · 契约 …` |
| 3 | 题目详情对教师渲染"开始作答"，点进去立刻是 403 页面 | 自指向死路的 CTA | 仅对学生渲染按钮；教师/助教看到说明文案（服务端同样只接受学生提交） |
| 4 | 守卫拒绝页是一个 `<h2>` 长句子 | 无标题、无原因、无角色说明 | 改为面板：`没有访问权限` + fail-closed 说明 + `该页面仅对以下角色开放：…`（`allowedRoles` 由路由表传入） |
| 5 | 题目详情把同一段题干渲染两遍 | 单版本题目下"题目详情"与"题目版本"两张卡片内容逐字相同，看起来像渲染 bug | 版本冻结题干与当前题干逐字相同时只渲染一次并说明；**内容不同（历史版本）时仍完整渲染两份**——那是"看起来对、其实错"的防线 |
| 6 | 用户列表"用户名"列渲染的是 `user_id` | 列标题与数据不是一回事 | 渲染契约里的 `username`，`user_id` 另开一列（`.mono-id`） |
| 7 | 同一页同时写着两个"单文件上限" | 1048576 与 2097152 并存（前者是过期的字面量） | 两处都用 `ENVELOPE_MAX_BASE64_LENGTH`（信封 schema 的 `content_base64.maxLength`） |
| 8 | 名单导入步骤条渲染成"1. 1. 上传文件" | i18n 文案自带序号，外面又套了 `<ol>` | 文案去掉序号，编号交给 `<ol>` |
| 9 | 名单表头是数据库列名（`UPDATED_AT` / `IS_ACTIVE` / `ENROLLMENT_ID`） | 端点确实没有响应 schema，但已知键没必要以列名示人 | 已知键映射到中文表头，**未知键仍按原样显示**（保留"无 schema"的诚实）；布尔渲染 是/否，ISO 时间格式化 |
| 10 | 成绩页"学号"列在 `student_number` 为 null 时回退显示 `student_id` | 把平台用户 ID 贴上了"学号"标签 | 表头改为"学生"，回退值加 `title` 说明它是学生 ID。**根因**是 B2 的 `student_number`/`display_name` 恒为 null（B2 只拥有 `b2`，不读 `b4`，且明确拒绝伪造占位值）——这是架构边界，不是缺陷，B1 只有义务不贴错标签 |
| 11 | 成绩分布图在 0 行时仍画一张空坐标系 | 占满 140px 的装饰 | 全零分桶时显示空态文案 |
| 12 | 筛选下拉的空选项用 `import.unmapped`（"（未指派）"） | "未指派"是列映射的措辞，不是筛选措辞 | 可选筛选用 `common.any`（"（不限）"），必选下拉用 `common.selectPrompt`（"请选择…"）；成绩页的课程下拉直接去掉空选项（课程是必需作用域） |

### 新增 / 调整的测试

| 文件 | 变更 |
|---|---|
| `src/features/questions/QuestionDetailPage.test.tsx` | 断言两个 stem 节点（内容不同时）；新增"内容相同时不重复渲染"用例 |
| `src/features/grades/GradesPage.test.tsx` | 按学号定位未完成行并断言得分列无数字、状态列为中文标签、提交时间为定宽 `YYYY-MM-DD HH:mm:ss`；课程下拉只剩真实课程 |
| `src/features/roster/RosterPage.test.tsx` | 未改（守卫仍可脱离路由上下文单独渲染——这是本轮特意保留的守卫性质） |

## 3. 后续追加：受控资产渲染、列表分页与题库导入（同日）

导入 100 道题时又暴露出两处只有"内容真的多起来"才会显形的问题：

| # | 缺陷 | 现象 | 修复 |
|---|---|---|---|
| 13 | 题目列表只取**一页**且不给出"还有更多"的信号 | `useQuestionList` 写死 `limit: 100`，而契约的 `limit` 上限是 200、题库可以更大。超过 100 道之后，后面的题目**静默消失**——教师看到 100 行会以为那就是全部。这是"数据量小的时候永远测不出来"的一类缺陷 | `useQuestionList` 改为 `useInfiniteQuery`：按 `next_cursor` 逐页加载；面板标题显示"已加载 N 道"，未到末页时给出「加载更多」，到末页显示"已到末页"。新增 `QuestionListPage.test.tsx`（4 个用例固定翻页行为：首屏一页、追加下一页、单页到底、筛选参数不被分页破坏） |
| 14 | 题干图片的受控读取（`RQ-B-1-40` 验收 2） | 契约此前没有资源端点，页面显示"契约缺口"横幅。CHG-B3-0002 裁决后端点已存在，横幅的陈述随即变成**假话** | 新增 `useQuestionAsset.ts`：用带令牌的 `fetch` 取字节，再造一个**由本模块创建**的 `blob:` URL 交给 `<img>`。为什么不用 `<img src="/api/...">`：浏览器不会为 `<img>` 附加 `Authorization` 头，而 B1 的访问令牌只在内存里。白名单因此**不需要**放开 `blob:`（题干里出现的任何 `blob:` 仍被拒绝），能渲染的 blob 只可能来自本模块 |

`parseAssetUrl` 的单元测试在编写过程中拦下一个真实缺陷：`apiBaseUrl()` 为空（同源部署）时，
原实现跳过了同源校验，于是一个**跨源**绝对地址只要路径形状对就会被接受，接下来那次带令牌的
`fetch` 会把 Bearer 令牌发给另一个源。已改为：基址为空时基线取当前页面 origin，配置了基址时
还要求路径落在基址前缀下。`useQuestionAsset.test.ts` 逐条固定"什么算受控资产地址"。

## 4. 验证（真实执行）

### 门禁

```powershell
node node_modules/typescript/bin/tsc -b                 # exit 0
node node_modules/eslint/bin/eslint.js . --max-warnings 0   # exit 0
node scripts/generate-api-types.mjs --check             # API types match MAIN/contracts.
node node_modules/vitest/vitest.mjs run                 # 18 files / 96 tests passed
node node_modules/vite/bin/vite.js build                # ✓ built in 25.24s
```

> 上表是**本轮追加改动之前**的读数（保持不变以保留当时的记录）；最新读数见 §6。

> 说明：本环境 `pnpm` 不可用（DSH 的 pnpm-runner 用管道 stdio，Node 的 `child_process` 管道在
> 沙箱内被拒，`spawn EPERM`），因此上表直接调用 `node_modules` 里的工具，与 `pnpm lint` 等价的
> 命令逐条对应。
>
> `vitest` 偶发以退出码 1 结束而 summary 显示全部通过（forks worker 拆卸超时 + jsdom 的
> `scrollTo` stderr 噪声）。判定以 summary 为准，本轮最后一次运行为 exit 0。

### 端到端（Playwright MCP，真实 MAIN host `http://127.0.0.1:18000`）

| 步骤 | 结果 |
|---|---|
| 匿名首页 `/app/` | hero 显示登录提示（不再是 403）；顶栏 `未登录 · 契约 0.1.0`；导航只剩公开项 |
| `/app/login` | 420px 居中卡片、40px 输入框、禁用态**灰色** 44px 按钮 |
| 教师登录 → 首页 | 4 张磁贴（题目/成绩/名单/名单导入），角色=助教/教师；无"用户与角色"入口 |
| `/app/questions` | 「筛选」+「题目」两张卡片；表头 `题目/语言/状态/图形资源` |
| 题目详情 | 版本下拉默认选中最新已发布版本；冻结题干与当前题干一致时只渲染一次 |
| `/app/roster` | 「查询条件」+「名单与选课（2）」；中文表头、单行行高、编辑/移除并排、无横向滚动 |
| `/app/roster/import` | 「上传与解析」；步骤条 `1. 上传文件 … 5. 结果报告`（不再双编号）；未选文件时按钮禁用 |
| 教师访问 `/app/admin/users` | 拒绝页：`没有访问权限` + `该页面仅对以下角色开放：管理员。` |
| 学生登录 → 作答页 | Monaco 正常；上限文案两处一致；草稿时间 `2026-09-18 20:18:27` |
| 提交 | `提交已受理：submission_id = 7dbc0642-…`（55 B）；SSE 面板 `已结束 / 阶段 terminal / sequence 3`，终态 `评测技术失败（与代码质量无关）· 基础设施错误` |
| `/app/grades`（学生） | 1 行；状态列 `技术失败`（中文）；得分 `未完成`（**不是 0 分**）；提交时间 `2026-09-18 20:18:48`；分布图一根蓝柱 |
| `/app/grades`（管理员） | 汇总卡片 + 图表正常；颜色随 `prefers-color-scheme` 切换（深色下网格线与柱子都取自 token） |
| 管理员 `/app/admin/users` | 「筛选」/「新建用户」/「用户与角色」；**用户名列显示真实 username**；表内角色下拉有边框与箭头 |
| 深色模式 | 整站（含图表）随系统切换，无白底残留 |

### 交互元素逐个实测（不只是渲染）

"页面能打开"与"元素能用"是两件事，因此每个可交互元素都被真实触发过一次：

| 元素 | 操作 | 结果（服务端证据） |
|---|---|---|
| 名单行「编辑」 | 打开弹窗（`#edit-display-name` 预填 `李小明`）→ 保存 | 页面 `已保存（服务端已记录审计）。`；`b4.audit_records` 新增 `enrollment.updated / success / detail="changed fields: none"`，带 `request_id`——乐观并发与审计都是真的 |
| 「导出成绩」 | 点按 | 浏览器下载 `grades-8cf8259c-….xlsx`；页面 `导出已触发…（5487 B）`。文件由 **B2** 生成，B1 只负责下载 |
| 名单导入：选文件 | 选择 `MAIN/.runtime/seed/roster.xlsx` | 文件名回显，`上传并解析` 变为可用 |
| 名单导入：「上传并解析」 | 提交 | `已预览`；识别出 姓名/学号/课程编号/课序号；通过校验的行 2 条（含电子表格真实行号 2/3）；被拒行 0 条。**未点确认**——`b4.enrollments` 仍是 2 行、该导入停在 `previewed`，即"确认前零写入"在真实库上成立 |
| 「新建用户」 | 填表 → 提交 | 页面 `用户已创建。`；新行出现在表格首位（`qa.probe` / `20269999` / 学生）。**该行是本次验证留下的探针数据，未删除** |
| 成绩行「逐项证据」 | 点按 | 弹窗打开并显示真实结果：`操作失败 / 资源不存在（404） / 请求 ID: 0c3fb6d6-…`。这是**如实上报**：本次评测以 `infrastructure_error` 终止，B2 从未持久化报告，因此没有可读证据；页面不伪造一份空报告 |
| 「退出登录」 | 点按 | 会话清除，导航收回公开项，顶栏回到 `未登录` |

### 控制台

对 9 个页面逐个硬导航（`networkidle` + 700ms 等待）并收集 `console.error` 与 `pageerror`：

```text
anonymous home / login / questions / question detail / grades / roster /
roster import / admin users / submit       → 每页 consoleErrors = 0，页面上均渲染出内容
```

唯一的错误条目是每次导航各一条 `GET /api/v1/auth/session/refresh` 返回 401 —— 无 cookie 时
的预期探测，页面随即向登录态收敛。**无未捕获异常、无 React 警告、无 pageerror。**

## 5. 后续追加（缺陷清单轮）：真实分页、"谁还没交"、草稿身份隔离与可访问性

本轮的输入是一份只读审计（`MAIN/.runtime/innovation/B1.md`）给出的排序缺陷清单。逐条如下。

| # | 缺陷 | 现象 | 修复 |
|---|---|---|---|
| 15 | **成绩/名单/课程只取一页（`limit: 200`），超出的行静默消失** | 300 人的课序里，教师只看到前 200 行并以为那就是全班；导出却包含全部（页面与导出口径不一致）。名单与课程筛选下拉同理 | `useGrades`/`useEnrollments`/`useCourses` 全部改为 `useInfiniteQuery` 跟随 `next_cursor`；新增共享控件 `LoadMoreActions`（「加载更多 / 已到末页」），成绩页、名单页、题目页与四处课程下拉统一使用。旧的单页查询路径已删除 |
| 16 | **教师无法回答"谁还没交"** | 名单与成绩是两个互不相连的列表，教师只能人工比对 | 新增 `SubmissionMatrix.tsx`：名单（`EnrollmentPublic`）× 成绩（`GradeRow`）按 `student_id` 外连接，分「未提交 / 技术失败 / 评测未完成 / 已拒绝 / 已评分 / 未绑定账号」六类，带状态筛选与计数；挂在成绩页的选项卡（`Tabs`）里，教师/助教/管理员可见。**判定前先把名单与成绩的全部页取完**——否则分页本身会变成"某人未提交"的错误结论。`student_id` 为空的行显示"未绑定账号"，不拿学号或 `enrollment_id` 冒充用户 ID |
| 17 | **共用机器上的草稿泄漏** | 草稿键只有 `question_version_id`，且 `signOut` 不清草稿：A 未提交就走，B 登录同一台机器打开同一题会看到 A 的代码 | 草稿键改为 `b1.draft.v1.<用户 ID>.<question_version_id>`，记录内也带 `ownerId` 并在读取时校验（不符即丢弃）；`signOut` 在 `finally` 里清空本浏览器全部草稿；提交页说明"退出登录会清除本机未提交草稿" |
| 18 | **`identity.ts` 在渲染期读令牌，`token_subject` 路径不可达** | 令牌在 `restoreSession()` 完成前是 `null`，此后不会重算 → 身份只能靠"本人成绩行"回退，从未提交过的学生因此拿不到 `student_id`、提交被禁用。测试用非 JWT 桩掩盖了这一点 | `sub` 解析进入会话状态（`readClaimsFromToken` 返回 `subject`，`SessionState.subject`），`useStudentIdentity` 改为读会话状态（响应式）；测试改用**真实形状的 JWT**（`sub` 是 UUID），该路径因此真的被执行 |
| 19 | **错误态无法带走** | `request_id` 只能靠用户手工选中复制，求助时说不清是哪一次请求 | `ErrorState` 增加「复制诊断信息」：`request_id` + ProblemDetails + 契约版本 + 页面路径，经 `redaction.ts` 脱敏后作为 JSON 复制；剪贴板不可用时给出可手动选择的文本（不假装成功）。`CONTRACT_VERSION` 改为**构建期**从 `contracts.lock` 注入（`vite.config.ts` 的 `define`），不再有会漂移的字面量 |
| 20 | **弹窗没有焦点管理，表格没有排序语义，页面没有 `h1`** | 键盘用户 Tab 会走到弹窗背后的内容上、Escape 关不掉、关闭后焦点丢回页面开头；排序后的表格不声明顺序；每页最外层标题是 `h2` | `Modal` 增加焦点陷阱 + Escape + 关闭后焦点归还（`aria-labelledby` 取代"仅当标题是字符串才有名字"）；`Table` 支持 `Column.sort` → `aria-sort`，并支持 `aria-label`；新增 `PageTitle` 与 `Panel.titleLevel`，保证每页恰好一个 `h1`（面板 `h2`、卡片内 `h3`）；`Tabs` 按 WAI-ARIA Tabs 模式实现（方向键、roving tabindex） |
| 21 | **名单确认的幂等键每次点击都重新生成** | `useConfirmRosterImport` 在 `mutationFn` 内 `newIdempotencyKey()`：失败重试会带不同的键，"确认可重放且幂等"（RQ-B-1-10 验收 4）在客户端不成立；原测试只断言键"存在" | 键改为按**导入任务**生成一次并在重试中复用（`useRef` 记录 `{importId, key}`）；测试改为制造一次失败再重试，断言两次确认带**同一个键** |

同批的文档修正（此前文档在**低估**自己的能力）：

| # | 位置 | 修正 |
|---|---|---|
| 22 | `docs/collaboration/interfaces.md` | 缺口 1（题干图形无端点）移入"已关闭的缺口"（契约与实现都已就位）；缺口 3 收敛为 `listSections`/`listAuditRecords`/`getModuleHealth`（`listEnrollments`/`listAssignments` 已由 CHG-0007 补 schema）；契约版本同步到 `0.3.0` |
| 23 | `docs/README.md`、`docs/requirements/traceability.md`、`docs/testing/cases.md`、`docs/testing/strategy.md` | `RQ-B-1-40/50` 由 `阻塞` 改为 `已完成`（与 traceability 一致）；测试读数改为实测值；新增本轮用例 |

**删除**：`src/auth/visibility.ts` 的 `canEditRoster`（无任何生产引用，仅测试引用，提供虚假的权限粒度）
与 `RosterPage.tsx` 的 `HIDDEN_COLUMNS`（恒为空集合，挂在安全相关位置却不隐藏任何列）。
`canViewRoster` **保留**并说明理由：它被 `AppShell`、`routeComponents`、`HomePage` 三处生产代码引用。
`canViewCohortGrades` 由本轮的「谁还没交」消费，不再是死代码。

## 6. 本轮验证（真实执行）

```powershell
node node_modules/typescript/bin/tsc -b                     # exit 0
node node_modules/eslint/bin/eslint.js . --max-warnings 0   # exit 0
node scripts/generate-api-types.mjs --check                 # API types match MAIN/contracts.
node node_modules/vitest/vitest.mjs run                     # 21 files / 123 tests passed
node node_modules/vite/bin/vite.js build                    # ✓ built in 24.57s
```

- `vitest` 以退出码 1 结束是**既有噪声**（jsdom `Not implemented: Window's scrollTo()` 与 worker 拆卸），
  summary 全绿；判定以 summary 为准。
- `vite build` 产出的 `dist/assets/index-*.js` 中 `0.3.0` 只出现**一次**（由 `vite.config.ts` 从
  `contracts.lock` 注入的契约版本），证明构建期注入生效且代码里没有第二份字面量。
  构建的 chunk 体积警告是既有项（Monaco ~3.7 MB），未处理。
- 生成类型已按契约 `0.3.0`（CHG-0006/0007/0008）重新生成：`src/api/generated/*.ts` 四个文件均更新。
- 未执行：Playwright 端到端（需要运行中的四模块系统与浏览器会话）。

## 7. 第三批：作业中心、账号列表分页与死代码清理（契约 0.4.0 → 0.5.0）

| # | 缺陷 | 现象 | 修复 |
|---|---|---|---|
| 24 | **B1 没有"作业"这个概念** | 平台已有 8 个作业（`AssignmentPublic`），而 B1 的路由里只有"题目"，学生与教师都只能从上百道题里自己认；教师也无法回答"这个作业多少人交了" | 新增作业中心：`/assignments`（学生「我的作业」：标题/状态/截止时间/题目数 + 本人进度 + 直接进作答；教师「提交情况」：逐作业已提交/未提交，可展开逐人名单）与 `/assignments/{id}`（作业详情：题目版本 + 学生逐版本提交状态；教师另有名单矩阵）。导航与首页磁贴各加一个入口。**只使用契约字段**：`assignment_id` + `question_version_ids`，没有"按 id 取作业"的端点，因此详情页从列表查找并如实报告找不到 |
| 25 | **判定逻辑会重复实现** | 「谁还没交」的连接写在 `SubmissionMatrix.tsx` 内部，作业中心要按作业再算一次同样的连接 | 抽出 `src/features/grades/cohort.ts`（纯外连接 + 判定口径）与 `useCohort.ts`（取数 + "取完全部页再判定"）。成绩页与作业中心共用同一套；逐作业计数只是把成绩行按作业的题目版本过滤后再调一次 `buildCohort` |
| 26 | **自动翻页没有上限** | 判定类视图会一直跟随 `next_cursor`；服务端游标异常（例如永远返回同一个游标）会把浏览器拖进无限请求 | 新增 `src/api/pagination.ts` 的 `useLoadAllPages`：自动翻页上限 40 页（40×200=8000 行），到上限**按"未加载完"处理**（调用方不判定），四处调用点（作业、成绩、名单、班级）统一走它 |
| 27 | **`useUsers` 仍单页 `limit: 200`** | 与另外三处同一缺陷类：第 200 个之后的账号在管理页上静默消失，管理员据此判断"这个账号不存在" | 改 `useInfiniteQuery` + 共享 `LoadMoreActions`，删除单页路径；新增 `UsersPage.test.tsx` |
| 28 | **死代码** | `directory.ts` 的 `sectionColumns` 无任何引用（含测试）；i18n 目录里有本轮重命名后失去调用者的词条 | 删除 `sectionColumns`（先按真实 import graph 核对；`collectColumns` 仍被名单导入预览使用，保留）；删除本轮产生的无引用词条（`users.title`、`assignments.countLoaded`、`assignments.viewQuestion`、`assignments.col.submittedAt`）。新增 `scripts/audit-i18n.mjs` 一次性审计脚本：把 `t("…")` 字面量与 ``t(`prefix.${…}`)`` 动态前缀都算作引用，列出真正没有调用者的词条（不属于门禁，`node scripts/audit-i18n.mjs` 手动跑） |

判定口径（写进 UI 与文档，不留给读者猜）：

- 作业的「已提交」= 该作业的**任一**题目版本有提交记录。契约只给 `question_version_ids`，
  B1 不发明"必须全部完成才算提交"这种没有契约依据的规则。
- 「未提交」不等于 0 分；**未绑定账号**（`EnrollmentPublic.student_id` 为空）的行既不参与
  "已提交"也不参与"未提交"，分母是"可判定人数"（`cohortJudged`）。
- 学生侧的进度来自**本人成绩行**（`listGrades` 对学生只返回本人记录）；成绩行没取完时
  进度列只显示"加载中"，不给判定。

## 8. 本轮验证（真实执行）

```powershell
node node_modules/typescript/bin/tsc -b                     # exit 0
node node_modules/eslint/bin/eslint.js . --max-warnings 0   # exit 0
node scripts/generate-api-types.mjs --check                 # API types match MAIN/contracts.
node node_modules/vitest/vitest.mjs run                     # 23 files / 133 tests passed
node node_modules/vite/bin/vite.js build                    # ✓ built in 24.57s
```

**§6 的读数是第二批结束时的**（21/123），保持不变以保留当时的记录；上表是第三批的读数。
生成类型已按**当时的契约 0.5.0**（CHG-0009 模块数量开放、CHG-0010 新增 B5）重新生成——
B1 消费的端点没有语义变化，`vite build` 产出的 bundle 中注入的契约版本也随之变成 `0.5.0`（出现一次）。

## 9. 第四批：出题写路径、逐用例证据可视化、辅助/诊断两轨与分页截断（契约 0.9.0 → 0.11.0）

本轮的输入是三件"平台有能力、界面没有出口"的事实，加上一份来自 MAIN 的只读缺陷复核。

| # | 缺陷 | 现象 | 修复 |
|---|---|---|---|
| 29 | **B1 没有任何题目写能力（D6）** | 平台能建题（`POST /api/v1/questions`）能改草稿（`PATCH /api/v1/questions/{id}`），界面里一条路都没有：教师能读 106 道题，改不了任何一道。这是**作业中心**关闭过的同一类洞（能力存在而不可达） | 新增 `src/features/authoring/`：`AuthoringPage`（新建 `/questions/new`、编辑 `/questions/{id}/edit`）、`draft.ts`（表单↔`QuestionDraftInput` 的纯映射与校验）、`CaseEditor`、`useAuthoring.ts`。题干预览复用**同一个** `SafeMarkdown`（不写第二个渲染器）。`tags` 按契约形状归一化（小写、短横线、≤20 个、≤40 字符）后再提交，页面把将提交的标签逐条显示出来 |
| 30 | **受保护字段"写进去就读不回来"，而界面假装能读** | `reference_answer` 与 `cases[]` 只在写路径上存在（公开 DTO 里没有这两个字段）；适配器的 `args`/`stdin`/`expect_exit_code`/`comparison`/`callable` 同样不可读回，`QuestionVersionPublic` 只给 `adapter_kind`/`max_files`/`max_total_bytes` | 本页只预填**公开 DTO 里真的存在**的字段；其余字段留空并在页面上写明"空 = 本页读不到服务端现值，不是服务端没有"。`buildDraftInput` 在受保护内容为空时**返回问题清单而不发请求**——契约没有说明"省略 `cases`"会被解释为保留还是清空，B1 不替用户赌这个语义（与名单页"拿不到 `updated_at` 就禁止保存"同一立场） |
| 31 | **`kind=file` 无法从 UI 出题**（登记为限制，不是 TODO） | `AdapterManifest.files[]` 只带 `{path, content_sha256, mount}`，字节必须经 `POST /api/v1/question-versions/{id}/fixtures` 上传，而该端点是 `internal` + `serviceToken` 且摘要由服务端计算 | 适配器下拉里 `file` 可见（`adapter_kind` 可读，已存在的版本必须能如实显示），但选中它会被明确拒绝保存并说明原因；B1 不替服务端编造摘要 |
| 32 | **发布按钮会是一个必然失败的按钮（D6 的决策项）** | `publishQuestionVersion` 要求与该版本 `content_digest` 绑定的参考答案 attestation（CHG-0008），记录它的端点是 `internal` + `serviceToken`；该约束存在的意义就是"执行者与记录者必须是不同组件" | **不渲染发布按钮**。新增 `PublishRequirement`（出题页与题目详情页共用同一个渲染器，仅出题角色可见）：显示"待验证后才能发布"、缺什么、由谁在 UI 之外补、以及该版本的 `content_digest`。等级 A 的说明见 §11 |
| 33 | **B1 不渲染任何逐用例证据（C6）** | `case_evidence[]` 早就有 `resource`/`artifacts`/`explanation`（CHG-0012），界面只把每个用例压成表格里的一行英文失败类别 | 重写为用例卡片（`CaseEvidence.tsx`，删除旧的表格渲染）：资源峰值按人读单位显示、`limit_hit` 读作"哪个上限被触发"、文件改动列出期望/实际摘要与新建/修改/删除清单、`explanation` 标注为"确定性规则推导" |
| 34 | **"未测量"会被渲染成 0** | `resource` 为 `null` 或整个字段缺失时，任何"0 B / 0.00 s"的读数都把"沙箱没报告峰值"说成"用了零内存" | 字段级 `null` 一律渲染"未测量"；`resource`/`artifacts`/`explanation` 缺失时分别给"未测量 / 无文件级结论 / 未给出说明"，并说明原因。回归用例断言缺失时**不出现"数值 + 单位"**的读数 |
| 35 | **受保护证据有被前端推导的风险** | `expected_sha256` 与 `expected_output_excerpt` 是 PROTECTED（学生视图不下发） | 期望摘要、期望输出、"是否一致"的结论**整块只存在于教师视图**；学生视图连"两者是否一致"也不计算（那本身就是受保护信息）。回归用例断言学生视图里这三个元素一个都没有 |
| 36 | **模型建议与确定性规则会混成一种结论** | `authoring-assistance`（模型生成、`confidence: advisory`）与 `diagnostics`（`CMP-*` 确定性规则）是两个端点，但页面若用同一张表渲染，读者会以为建议同样权威 | 两个面板外观与文案都不同：`panel--advisory` 外框 + 显式"模型生成、非确定性、不构成权威判定、不参与判题" + `provenance` + `disclaimers` 原文照录；诊断面板说明"同输入同输出"，`rule_id` 用等宽字显示 |
| 37 | **两种"空"长得一样** | 诊断 `findings` 为空是"检查过、无问题"，而 provider 未配置（`409`，或 `200 + status=unavailable`）是"未启用" | 三种状态三种渲染：`diagnostics-clean`（成功态）/ `assistance-unavailable`（未启用，含服务端 `detail` 原文）/ `assistance-failed`（模型调用失败）。回归用例断言"未启用"时**不出现**任何建议表 |
| 38 | **自动翻页会停在第 2 页（比截断更糟）** | `useLoadAllPages` 只依赖 `hasNextPage`/`isFetchingNextPage`：React 把"这一页已到达"与"开始取下一页"合并成一次渲染，依赖项因此没有变化，循环停在第 2 页。实测（注入永远有下一页的分页夹具）只发出 **2** 个请求——连 40 页的上限都没到就静默截断了 | `pageCount` 加入 effect 依赖：每成功取到一页都会严格增加，循环必然推进到"没有下一页"或"到达上限"。修复后同一夹具稳定发出 **40** 个请求并给出截断说明 |
| 39 | **`capped` 被并进"还在加载"，用户看到永不结束的 spinner** | 三个 hook 把上限折叠进"未就绪"标志（`incomplete: !complete \|\| capped`、`complete: complete && !capped`），三个消费方对合并态渲染 `<Spinner/>`。`MAX_AUTO_PAGES` 是**终局**，重试不会继续——"分页静默丢数据"于是换了个说法继续存在 | `useAllGrades`/`useCohort`/`useAllAssignments` 各自多暴露一个 `truncated`；截断时渲染明确说明（"已达到自动翻页上限（40 页）：加载在这里停止，重试不会继续，因此下面的结果可能不完整"）而**不渲染 spinner**；`gradesAvailability()` 统一"ready / loading / truncated"三态，作业中心的进度列与作业详情的版本行在截断时显示"成绩未取完"而不是"未提交"。**不下结论的保证不变**：截断态仍然不渲染任何"未提交"判定 |
| 40 | **学生视角的题目列表默认看不到新建的草稿** | 契约 `published_only` 默认 `true`，教师建完题在列表里找不到自己刚建的那道 | 出题角色多一个「包含草稿」开关（`published_only=false`）；创建成功后页面直接给出详情/编辑链接，并说明"默认不在列表里"的原因 |
| 41 | **标签可写之后仍然不可检索** | CHG-0013 为 `listQuestions` 加了 `tags` 参数，理由正是"教师能给 106 道题打标签，却无法问『进程调度那几道是哪些』" | 题目列表新增标签列 + 标签筛选输入；筛选按契约的 `explode` 形式发送**重复同名参数**（`?tags=a&tags=b`），`buildQueryString` 因此支持数组值；非法标签不入参并被列出（不静默丢弃） |
| 42 | **`revision_note`/`revision_diff` 落了库但读不出来** | 版本面板只有版本号与状态，教师看不到"为什么会有这一版" | 版本面板新增 `RevisionSummary`：修订说明 + 结构差异（计数/标志/摘要）；`null` 条目渲染为空而不是"无变化"，首个版本给出"没有可比较的上一版"的说明 |
| 43 | **契约把 PATCH 的响应声明错了，于是两个审计字段在运行时恒为 `undefined`**（CHG-0016 修复的正是契约那一侧） | `PATCH /api/v1/questions/{id}` 被声明为返回 `QuestionVersionPublic`，而实现自 Phase 01 起返回的是 `QuestionPublic`。`QuestionDraftInput` 能改 `title`/`tags`，而版本 DTO（`additionalProperties: false`）**没有**这两个字段——声明与能力自相矛盾。B1 的 `useUpdateQuestionDraft` 因此把响应当成版本 DTO 读，`question_version_id` 与 `version` 两个审计字段**在真实运行中都是 `undefined`**，而类型检查发现不了（类型来自错的声明） | 契约升到 0.11.0 后按 `QuestionPublic` 消费：新版本标识取自 `versions[0]`（契约规定版本历史新→旧，该字段存在的原因之一就是让客户端能发现版本 id），版本号取自 `versions[0].version`。审计日志字段改为可空并如实记录"服务端没给"（`?? null`），不再假装有值。**回归用例断言日志里的 `question_version_id` 等于服务端返回的 `versions[0].question_version_id`**，并做了变异验证（见 §10） |

**删除**：`GradeDrilldown` 里逐用例证据的表格渲染（连同"每行一个用例"的列定义）被用例卡片整体取代，没有保留旧分支。
`AssignmentDetailPage` 的 `progressReady: boolean` 折叠态被三态 `myProgress` 取代；`StudentAssignmentList` 的 `progressReady` 同理。

## 10. 本轮验证（真实执行，契约 0.11.0）

```powershell
node scripts/generate-api-types.mjs                        # 四个文件按 0.11.0 重新生成
node node_modules/typescript/bin/tsc -b                    # exit 0
node node_modules/eslint/bin/eslint.js . --max-warnings 0  # exit 0
node scripts/generate-api-types.mjs --check                # API types match MAIN/contracts.
node node_modules/vitest/vitest.mjs run                    # 25 files / 175 tests passed（exit 0）
node node_modules/vite/bin/vite.js build                   # ✓ built in 24.31s
```

- 生成物在 `dist/assets/index-*.js` 中：`0.11.0` 出现 **1** 次（`vite.config.ts` 从 `contracts.lock`
  注入），`0.10.0` 出现 **0** 次——构建期注入生效，代码里没有第二份字面量。
- **`vitest` 本轮 exit 0**（此前几轮以退出码 1 结束，原因是 jsdom `scrollTo` stderr 噪声与 worker
  拆卸；判定仍以 summary 为准）。
- **契约在本轮执行期间被 MAIN 连续推进两次**：`0.9.0` → `0.10.0`（CHG-0014 隔离区/证据包、
  CHG-0015 质量热力图）→ `0.11.0`（CHG-0016 修正 `PATCH /questions/{id}` 的响应声明）。
  三份锁与 `B1/module.yaml` 均由 MAIN 同步，**B1 未手改**；每次发现漂移后都按新版本重新生成类型并
  重跑全部门禁。B1 消费的端点语义只在 CHG-0016 发生变化（响应类型修正，见缺陷 43）。
- **CHG-0016 的回归做了变异验证**（证明这条用例不是"断言调用成功"）：把 `useAuthoring.ts` 的审计
  字段临时改回"从响应上直接读 `question_version_id`"（即旧行为）后，该用例失败并给出
  `expected undefined to be '00000000-0000-4000-8000-0000000000d1'`；恢复实现后通过。这是缺陷 43
  在修复前的真实症状。
- chunk 体积警告是既有项（Monaco `editor.main` ≈ 3.77 MB），未处理。
- **未执行**：Playwright 端到端（需要运行中的四模块系统与浏览器会话）。因此本轮**没有**任何
  "对着真实 B3/B2/B4 跑通"的证据：出题写路径、辅助/诊断端点、CHG-0012 的证据字段都只经过
  契约夹具与生成类型的校验（见 §11 的诚实边界）。
- **响应类型的逐点复核**（CHG-0016 的教训）：把 `src/` 下 26 处 `apiRequest<T>` 的泛型逐个对照
  `MAIN/contracts/openapi/*.yaml` 的响应声明，结论是**只有** PATCH 那处不匹配（已修）。其余
  写路径的 `200/202/204` 在契约里**没有** `content`（无响应体），因此 `apiRequest<void>` 与声明一致，
  不存在"读了没声明的字段"的第二处。

## 11. 本轮的真实边界与上报事项（不是 TODO）

1. **UI 出题目前没有通往发布的路径（需要 MAIN 决策）**。`publishQuestionVersion` 要求一份与该版本
   `content_digest` 绑定的参考答案 attestation（CHG-0008），而记录 attestation 的
   `POST /api/v1/question-versions/{id}/attestations` 是 `internal` + `serviceToken`；该拆分的全部
   意义就是"执行者与记录者必须是不同组件"，B1 由浏览器自证会让这条约束作废。今天只有 MAIN 的题库
   工具会执行参考答案，因此**UI 建出来的草稿在界面上没有发布路径**。B1 的处置是把它做成诚实的
   状态（"待验证后才能发布" + 缺什么 + 由谁在 UI 之外补 + `content_digest`），**不**渲染必然失败的
   按钮，也不发明客户端 workaround。是"记录在案的操作步骤"还是"新增一个平台端点"，属 MAIN 的裁决。

   > **本条第 1 项已被取代（2026-09-20，`CHG-0019` / `ADR-0015`）**：MAIN 选了"新增一个平台端点"
   > ——`POST /api/v1/reference-verifications`，由 **B2 执行、B3 记录、教师一键触发**。出题页现在有
   > 「验证并发布」入口（`ReferenceVerificationPanel`），`PublishRequirement` 组件与其"没有发布路径"
   > 的文案**已删除**。当前状态、真实读数与未验证项见
   > [`phase-05-authoring-publish-path-and-0-15-0.md`](phase-05-authoring-publish-path-and-0-15-0.md)。
   > 本条保留原文，因为它是当时的真实状态与决策依据。
2. **编辑草稿只能整体重写受保护内容**。`reference_answer`、`cases[]` 与适配器细节都读不回来，而
   `QuestionDraftInput.cases` 是可选的、契约**没有说明**省略它会被解释为保留还是清空。因此本页要求
   每次保存都重新声明这些字段（否则拒绝提交）。副作用是"只改标题"这类编辑也必须重填用例。
   若 B3 明确"省略即保留"，可以放宽为局部更新——需要契约或 B3 的明文答复，B1 不自行假定。

   > **本条第 2 项已部分取代（2026-09-20，`CHG-0017`）**：B3 明确了"省略即保留"（`cases` 省略保留、
   > `reference_answer` 省略或空白保留），B1 因此放开了这条硬拦并改为"留空即不发该键"。**适配器细节
   > 仍然读不回来**（`adapter` 写必填、无读出口），保存时仍是重新声明整个 manifest——见
   > [`phase-05-authoring-publish-path-and-0-15-0.md`](phase-05-authoring-publish-path-and-0-15-0.md)
   > 与 `../collaboration/interfaces.md` 缺口 10。
3. **`kind=file` 的题目无法从 UI 创建**（原因见缺陷 31）。已存在的 `file` 版本可以正常显示，但本页
   拒绝保存它，以免静默改写。
4. **"期望恰好为空输出"无法从表单表达**。`expected_stdout` 的可空类型里 `null`（不声明）与 `""`
   （期望空输出）是两回事，文本框留空只能表达前者。已在字段说明里写明，未发明额外的控件。
5. **CHG-0014/CHG-0015 给 B1 指派了新的教师侧界面**（隔离区复核 + 证据包下载、题目质量视图）。
   这两项**不在本轮工作项内**，因此本轮没有实现它们，也没有消费对应的三个新端点。需要 MAIN 决定是否
   作为下一批下达。
6. **分页截断的"缩小范围"提示是唯一的出路**：B1 没有"指定页码"的契约参数（`CursorPage` 只有
   `cursor`/`limit`），因此撞上 40 页上限后用户只能缩小课程/课序范围，页面如实这么写。
7. **契约声明错误会把编译期错误变成运行时错误**（缺陷 43 的教训）：`PATCH` 的响应类型错了以后，
   B1 的 `useUpdateQuestionDraft` 读了两个不存在的字段，`tsc` 与 ESLint 都不会报——因为类型来自
   错的声明。B1 的防线只有两条：① 每个 `apiRequest<T>` 的泛型必须在写的时候对着契约核一遍；
   ② 凡是**进入日志或审计**的字段，都要有断言"它是真值"的用例。本轮按这两条做了全量复核（§10 末条）。

## 未完成 / 阻塞
- 无 `阻塞` 项。**§11 另列了六条真实边界**，其中第 1 条（UI 出题没有通往发布的路径）是需要 MAIN
  裁决的产品级缺口：B1 把它做成界面上诚实的状态，而不是渲染一个必然失败的按钮。
- **本轮的新界面没有端到端证据**：出题写路径、辅助/诊断端点与 CHG-0012 的证据字段都只经过
  契约夹具 + 生成类型校验，**没有**对着运行中的 B3/B2 实际写过一道题或读过一份带 `resource` 的报告
  （B2 尚未下发这些字段，契约先行不等于实现已到）。
- **真实 `completed` + 分数仍不可得**：本环境是 Windows，没有 nsjail/cgroup v2，B2 的沙箱阶段
  如实返回 `infrastructure_error`（ADR-0004、SEC-20 禁止降级为裸进程）。因此端到端只能验证到
  "技术失败"终态与"未完成不显示 0 分"这条路径；带分数的证据下钻需要 Linux 特权容器。
- 契约缺口 **3、4、5** 仍在（若干 200 响应无 schema 的剩余部分、成绩无汇总端点、无"当前用户资料"端点），
  见 [../../collaboration/interfaces.md](../../collaboration/interfaces.md)「已知契约缺口」；
  缺口 1、2 与缺口 3 的 `listEnrollments`/`listAssignments` 部分已关闭。
- 题目详情的"当前题干 + 版本题干"两段结构在单版本题目下仍会读起来略重（本轮已改为一致时不重
  复渲染）；多版本题目下这是刻意保留的对照。
- **等待 B4 落地**：「谁还没交」与作业中心的提交情况都依赖 `EnrollmentPublic.student_id`
  （契约 CHG-0007 已声明）。B4 的 `list_enrollments` 目前尚未返回该字段，因此在真实运行的
  系统上这些人会显示"未绑定账号"且不计入判定，直到 B4 侧改动上线——这是预期的中间状态，
  不是 B1 的缺陷（**未**用 admin-only 的 `/api/v1/users` 绕过）。
- **契约能力边界（不是缺陷，是不做的理由）**：作业只有 `assignment_id`/`course_id`/`section_id`/
  `title`/`status`/`due_at`/`question_version_ids`，没有"每题分值""是否必做""我的得分"等字段，
  因此作业中心不显示"作业总分/作业得分"这类派生数字；需要它们就得先有契约字段（走 CHG）。
- **幂等键的作用域（已知限制）**：名单确认的幂等键按"导入任务"复用（同一次确认流程内的重试带
  同一个键），但预览与确认之间**整页刷新**会重新生成键。没有把键写进 `sessionStorage`——那会
  扩大 ADR-0007 允许的本地存储面。若 B4 把"对已提交的导入再次确认"当成第二次写入，这条路径仍
  暴露，**已请 MAIN 向 B4 确认**（见 `../../runbooks/troubleshooting.md`）。
- **顺带观察（未处理）**：i18n 目录里仍有 **21 个本批之前就没有调用者**的词条
  （`common.confirm`、`common.refresh`、`common.page`、`common.next`、`common.previous`、
  `home.scopeHint`、`import.idempotent`、`questions.versionUnknown`、`questions.distribution`、
  `submit.emptyFiles`、`submit.draftCleared`、`grades.apply`、`grades.evaluationRun`、
  `grades.exportRun`、`users.updateRoles`、`log.event.*` × 6，完整清单用
  `node scripts/audit-i18n.mjs` 复现）。
  它们不是本次改动产生的，删除属于与本次需求无关的清理，登记而不顺手修改。
  **本批新增的词条没有一个是孤儿**：`authoring.tagsTooMany`/`authoring.adapterKind.stdin|callable`/
  `authoring.diagnostics.notEnabled`/`questions.tagFilterAll` 在实现过程中被更贴切的键取代后**已删除**，
  `grades.caseIndex`/`grades.caseCategory` 随旧的用例表格一起删除；
  原本无引用的 `grades.caseOrdinal`/`grades.caseWeight`/`grades.duration` 被新的用例卡片重新用上
  （词条数因此从 24 降到 21，而不是只增不减）。
