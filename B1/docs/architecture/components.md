# B1 组件清单与扩展点

组件已实际落地（不再是规划）：三条泳道的页面/组件分别在
`src/features/roster-import|roster|questions|submission|grades|users|home|auth|authoring|commentary|appeals|intake|quality`，
共享面在 `src/app`、`src/api`、`src/auth`、`src/ui`、`src/logging`、`src/i18n`。
共享文件的改动登记规则见 `../phases/active/phase-01-business-ui.md`。

## 组件清单（实现态）

| 组件/模块 | 实现文件 | 职责 | 依赖 |
|---|---|---|---|
| 路由与路由组件 | `src/app/router.tsx`、`src/app/routeComponents.tsx` | 路由表 + 守卫组合（认证 + 角色可见性） | `src/auth/guards.tsx` |
| 应用外壳 | `src/app/AppShell.tsx`、`src/app/contractVersion.ts` | 顶栏、按角色过滤的导航、身份与契约版本（版本由构建期从 `contracts.lock` 注入） | `src/auth/visibility.ts` |
| 查询客户端与键 | `src/app/queryClient.ts` | 重试策略（4xx 不重试）、统一查询键 | `@tanstack/react-query` |
| 渲染异常边界 | `src/app/ErrorBoundary.tsx` | 渲染异常上报（带 `request_id`）+ 降级 UI | `src/logging/logger.ts` |
| API 客户端 | `src/api/client.ts`、`problem.ts` | 契约路径原样请求、`X-Request-ID`、401 刷新重放、ProblemDetails → `ApiError` | 生成类型 |
| 契约类型出口 | `src/api/types.ts`、`src/api/generated/**` | 生成类型的唯一别名出口（禁止手写字段） | `pnpm api:generate` |
| 目录与不透明行 | `src/api/directory.ts`、`src/api/opaque.ts` | 课程/课序目录（课程走游标分页）；仍无 schema 的响应按服务端键动态消费 + 标识符判定 | `CursorPage` 信封 |
| 自动翻页 | `src/api/pagination.ts` | `useLoadAllPages`：判定类视图取完全部页，并按上限（40 页）防止游标异常拖死浏览器 | `CursorPage` 信封 |
| 会话状态机 | `src/auth/sessionStore.ts`、`sessionContext.ts`、`SessionProvider.tsx` | 仅内存令牌、刷新恢复、`rolesKnown` fail-closed、令牌 `sub`（身份来源之一）、退出清空本机草稿 | `POST /auth/*` |
| 可见性规则与守卫 | `src/auth/visibility.ts`、`guards.tsx` | 角色→入口可见性（非安全边界） | 会话状态 |
| 名单导入向导 | `src/features/roster-import/RosterImportPage.tsx`、`mapping.ts`、`useRosterImport.ts` | 上传→预览→人工列映射→确认（幂等键按导入任务复用）→结果报告 | B4 roster-imports |
| 名单编辑 | `src/features/roster/RosterPage.tsx`、`useEnrollments.ts` | 按契约 `EnrollmentPublic` 列渲染 + `expected_updated_at` 冲突提示 + 游标分页 | B4 enrollments |
| 用户与角色 | `src/features/users/UsersPage.tsx`、`useUsers.ts` | 管理员用户/角色管理（游标分页） | B4 users |
| 作业中心 | `src/features/assignments/AssignmentsPage.tsx`、`AssignmentDetailPage.tsx`、`useAssignments.ts` | 学生的"我的作业/进度/去作答"与教师的"提交情况"；作业 → 题目版本 → 题干/作答 | B3 assignments + `cohort.ts` |
| 安全富文本渲染器 | `src/features/questions/SafeMarkdown.tsx`、`markdown/parse.ts`、`urlPolicy.ts` | 结构化节点渲染（无 `innerHTML`）+ URL 白名单 | 无 |
| 公开 DTO 守卫 | `src/features/questions/protectedFields.ts` | 渲染前扫描受保护字段，命中即拒绝渲染 | 无 |
| 代码编辑器 | `src/features/submission/CodeEditor.tsx`、`monaco.ts` | Monaco 封装（可注入加载器）+ 文本降级 | monaco-editor |
| 提交编排 | `src/features/submission/SubmitPage.tsx`、`validation.ts`、`useSubmission.ts`、`identity.ts` | 校验（路径/数量/大小）+ 摘要与 base64 + 幂等键 + 身份解析 | B2 submissions |
| SSE 客户端与进度 | `src/features/submission/progress.ts`、`useEvaluationStream.ts`、`ProgressPanel.tsx` | fetch+ReadableStream 解析、`id`/`sequence` 去重、退避重连、终态渲染 | B2 evaluation-runs |
| 草稿存储 | `src/features/submission/draft.ts` | 未提交草稿（仅本地；键含**用户 ID**，退出登录清空） | 浏览器存储 |
| 成绩总表 | `src/features/grades/GradesPage.tsx`、`aggregate.ts`、`useGrades.ts` | 筛选 + 游标分页 + 已加载行汇总 + 分布 + 下钻 + 导出 | B2 grades |
| 名单 × 成绩连接 | `src/features/grades/cohort.ts`、`useCohort.ts`、`SubmissionMatrix.tsx` | 按 `student_id` 外连接；未提交 / 技术失败 / 未绑定账号分类；判定前取完全部页 | B4 enrollments + B2 grades |
| 证据下钻 | `src/features/grades/GradeDrilldown.tsx` | 报告渲染；学生视图不下发/不渲染期望输出；**并列渲染权威分与 AI 参考分**（参考分权重更低）；报告不可得时仍保留评语轨 | `evaluation-report` + `/commentary` |
| 教学评语轨 | `src/features/commentary/CommentarySection.tsx`、`useCommentary.ts` | 四态 + 404「尚未生成」信息态 + `disclaimers` 逐字 + 出处/输入白名单；`advisory_score` 只显示（不计算）；生成是 mutation | B2 `/commentary` |
| 申诉证据包 | `src/features/appeals/AppealPackPanel.tsx`、`useAppealPack.ts` | 按需加载；`view` 由角色显式推出；`redactions` 逐条；下载响应体原字节（同一个 `Blob`） | B2 `/appeal-pack` |
| 隔离区复核台 | `src/features/intake/QuarantinePage.tsx`、`useQuarantines.ts` | 列表（`resolved` 显式、`reason_code`、游标分页）+ 归属（体只有 `student_id`/`note`）+ 学生候选来自名单 | B2 `/intake/quarantines` |
| 题目质量热力图 | `src/features/quality/QualitySummaryPage.tsx`、`useQualitySummary.ts` | 计数卡各占一格：安全三态分离、`unmeasured_runs` 单列、系统原因单列、小样本提示、时间本地↔UTC 转换 | B2 `/quality-summary` |
| 分布图 | `src/features/grades/GradesChart.tsx`、`echartsLoader.ts` | ECharts 分布图（可注入加载器，失败降级为表格） | echarts |
| 前端日志器 | `src/logging/logger.ts`、`redaction.ts` | 结构化 JSON 行 + 拒绝清单脱敏 + 有界缓冲 | 无 |
| UI 原语与脱敏 | `src/ui/components.tsx`、`src/ui/masking.ts`、`src/styles.css` | 表格/面板/弹窗（焦点陷阱）/选项卡/分页控件/页面标题/诊断复制 + 展示层脱敏 + 设计 token | 无 |

## 依赖方向

`页面/组件 → feature hooks → api 客户端 → 生成类型`。共享层（`api`/`auth`/`ui`/`logging`/`i18n`）不反向依赖 feature。
生产代码**不引用** `src/testing/**`（夹具只服务测试）。

## 扩展点（核心不改、扩展可加）

1. **富文本渲染器**：白名单以"节点类型 + URL 判定"为边界；新增支持的元素只需在 `parse.ts`/`SafeMarkdown.tsx` 增加节点类型，安全核心（不产出原始 HTML、不做 `innerHTML`）不变。
2. **成绩列**：`GradeRow` 的列以列定义数组驱动；新增列只改 `GradesPage` 的列数组，不改表格原语。
3. **表单校验规则**：`validation.ts` 的规则集合来自契约（路径模式、信封上限）与题目声明上限；新增规则只增加判定项。
4. **前端日志 sink**：`setLogSink()` 可插拔（控制台 / HTTP / 自定义）；新增 sink 不改日志器核心。
5. **编辑器与图表加载器**：`MonacoLoader` / `EChartsLoader` 依赖注入，替换实现（例如换编辑器内核）不改页面编排。

## 边界声明

- 上述组件均已实现并有测试；`RQ-B-1-40` 的两个验收项此前因契约缺口阻塞，缺口 1/2 均已关闭
  （见 `../collaboration/interfaces.md` 的"已关闭的缺口"）。
- 三条泳道各自持有独立页面与组件目录；**共享文件**（路由表、API 客户端、设计 token、i18n）改动前须在 `../phases/active/` 登记 + 小步提交。
