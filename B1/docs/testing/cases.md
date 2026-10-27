# B1 测试用例目录

本表是"需求能否被证明满足"的检查单。`层次` 取值：单元 / 组件 / 契约 / 安全 / 端到端。
`当前状态` 取值：`存在`（有真实测试且通过）／`部分`（B1 侧已覆盖，另一半须由提供方断言或需真实后端）／`待建`。

测试实跑结果：**46 个测试文件 / 398 个用例全部通过**（`vitest run`，见
`../phases/active/phase-08-password-reset.md` 的"真实执行的命令与结果"一节；
上一阶段 P13 为 43 文件 / 374 用例，本批新增找回密码与密码可见性用例）。
浏览器视觉验收（1440/768/390px 截图、深色模式与真实账号）**本轮未执行**，见 phase-08 的未验证项。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望 | 当前状态 | 测试位置 |
|---|---|---|---|---|---|---|---|
| TC-B1-001 | 1-10 | 组件 | 教师登录，契约夹具 | 依次上传合法 PDF 与 XLSX | 进入解析预览，逐行显示姓名/学号/课程编号/课序号，错误行标注 | 存在 | `src/features/roster-import/RosterImportPage.test.tsx` |
| TC-B1-002 | 1-10 | 组件 | 列名不匹配的名单 | 解析 | 进入列映射，不自动猜测；无法唯一映射时报错并允许人工指派 | 存在 | 同上 + `mapping.test.ts` |
| TC-B1-003 | 1-10 | 组件 | 含重复学号/缺失必填的名单 | 预览 + 确认 | 逐行给原因；被拒行出现在结果报告 | 存在 | 同上 |
| TC-B1-004 | 1-10 | 契约 | 已到预览未确认 | 断言请求序列 + 确认请求头 | 确认前零写入；确认带 `Idempotency-Key` | 部分 | 同上（服务端原子性/幂等由 B4 断言） |
| TC-B1-005 | 1-10 | 安全 | 名单含邮箱/身份证 | 预览 | 不渲染学号以外的敏感信息 | 存在 | 同上 + `src/ui/masking.ts` |
| TC-B1-006 | 1-20 | 组件+契约 | 学生登录 | 查看名单页 + 直接调编辑接口 | 入口不可见；直接调用 403 | 部分 | `RosterPage.test.tsx`、`visibility.test.ts`（403 由 B4 断言） |
| TC-B1-007 | 1-20 | 契约 | 教师仅授权课程 A | 编辑课程 B 名单 | 403 且 B4 审计产生记录 | 待建 | 需真实 B4（B1 无权断言审计表） |
| TC-B1-008 | 1-20 | 契约 | 教师成功编辑 | 查 B4 审计 | 含操作者/目标/变更前后/时间/请求 ID | 待建 | 同上 |
| TC-B1-009 | 1-20 | 组件 | 两个教师同 `updated_at` 编辑同行 | 后提交者保存 | 冲突提示，不静默覆盖 | 存在 | `RosterPage.test.tsx` |
| TC-B1-010 | 1-30 | 组件 | 教师登录 | 选课程+课序 | 列表 + 汇总（提交数/平均分/通过率） | 部分 | `GradesPage.test.tsx`、`aggregate.test.ts`（汇总只覆盖已加载行，缺口 4） |
| TC-B1-011 | 1-30 | 契约 | 学生登录 | 改 `student_id` 参数查成绩 | 越权 403 | 待建 | 由 B2 断言 |
| TC-B1-012 | 1-30 | 组件 | 某评测已完成 | 点成绩行 | 下钻逐项证据（规则/用例→得分/失分） | 存在 | `GradesPage.test.tsx`（教师/学生两种视图） |
| TC-B1-013 | 1-30 | 组件 | 存在 queued/running/failed | 看成绩列表 | 显示状态，不以 0 分伪装完成 | 存在 | 同上 |
| TC-B1-014 | 1-30 | 组件 | 存在同名同姓学生 | 看成绩 | 以学号区分，不合并 | 存在 | `aggregate.test.ts` |
| TC-B1-015 | 1-40 | 组件 | 题干含加粗/列表/代码块/表格/图片 | 渲染 | 全部正确渲染 | 存在 | `markdown/parse.test.ts`、`SafeMarkdown.test.tsx` |
| TC-B1-016 | 1-40 | 契约 | 未授权用户 | 请求图片 | 403；响应不伪装 text/html | 部分 | `useQuestionAsset.test.ts` 固定受控地址边界与 403/401 → "不可得"；**响应头与授权判定由 B3 断言**（缺口 1 已关闭） |
| TC-B1-017 | 1-40 | 安全 | 题干含脚本/内联事件 | 渲染 | 不执行，XSS 被拦截 | 存在 | `SafeMarkdown.test.tsx`、`urlPolicy.test.ts` |
| TC-B1-018 | 1-40 | 契约+安全 | 获取题目详情 | 校验响应字段 + 渲染 | 无参考答案/隐藏用例/expected output | 存在 | `protectedFields.test.ts`、`QuestionDetailPage.test.tsx` |
| TC-B1-019 | 1-40 | 组件 | 多个已发布版本 | 切换历史版本 | 各版本渲染稳定 | 存在 | `QuestionDetailPage.test.tsx`：版本下拉来自 `QuestionPublic.versions`，渲染**该版本自己的** `stem_markdown`；内容与当前题干相同时只渲染一次并说明（契约缺口 2 已关闭） |
| TC-B1-035 | 1-40 | 组件+安全 | 契约已提供受控资源端点（CHG-B3-0002 裁决后） | 题干引用 `![alt](/api/v1/questions/{qid}/assets/{aid})` | 带令牌取字节、以自建 `blob:` URL 渲染；跨源或非资产地址一律拒绝；403 原样显示 | 存在 | `useQuestionAsset.test.ts`（地址解析边界）+ `SafeMarkdown.test.tsx` |
| TC-B1-036 | 1-40 | 组件 | 题库大于一页（契约 `limit` 上限 200） | 打开题目列表并点「加载更多」 | 逐页追加、显示已加载数量、到末页给出明确信号（**不静默截断**） | 存在 | `QuestionListPage.test.tsx`（4 个用例） |
| TC-B1-020 | 1-50 | 契约 | 学生提交 | 同幂等键重复提交 | 同一 submission_id，不重复评测 | 部分 | `SubmitPage.test.tsx` 断言幂等键生成/复用与请求头；同一 id 由 B2 保证 |
| TC-B1-021 | 1-50 | 组件+契约 | 提交超限 | 前端提交 + 直调后端 | 前端拦截并提示；后端 422 | 部分 | `validation.test.ts`（前端拦截）；422 由 B2 断言 |
| TC-B1-022 | 1-50 | 组件 | 提交后 | 观察状态迁移 | 前端进度与后端状态一致 | 存在 | `SubmitPage.test.tsx`、`progress.test.ts` |
| TC-B1-023 | 1-50 | 组件 | 编辑中断网 | 重开页面 | 恢复本地草稿；草稿≠已提交 | 存在 | `draft.test.ts`、`SubmitPage.test.tsx` |
| TC-B1-024 | 1-50 | 组件 | SSE 中断重连 | 继续接收 | 按 event_id/序号去重，不重复历史进度 | 存在 | `progress.test.ts`、`SubmitPage.test.tsx`（真实重连+重放） |
| TC-B1-025 | 1-50 | 契约 | 学生提交他人课程/题目 | 提交 | 403 | 待建 | 由 B2 断言 |
| TC-B1-026 | 1-60 | 契约 | 教师导出 | 下载并打开 | 列齐全 | 部分 | `GradesPage.test.tsx` 断言触发导出与文件由响应体提供；列齐全由 B2 保证 |
| TC-B1-027 | 1-60 | 契约 | 公式注入字符开头 | 导出 | 被转义 | 待建 | 由 B2 导出实现断言（B1 不生成单元格） |
| TC-B1-028 | 1-60 | 契约 | 同筛选条件 | 对比导出与页面查询 | 完全一致 | 部分 | B1 用同一筛选参数请求两者（`useGrades` / `downloadGradesExport` 共用 `GradeFilter`）；>200 行时页面不再截断（TC-B1-038/039） |
| TC-B1-029 | 1-60 | 契约 | 学生登录 | 调导出接口 | 403 | 部分 | `GradesPage.test.tsx` 断言入口不可见 + 403 原样展示 |
| TC-B1-030 | 1-60 | 契约 | 中文数据 | 导出下载 | 文件名 UTF-8，中文不乱码 | 部分 | `useGrades.filenameFromDisposition` 解析 RFC 5987 文件名；文件内容由 B2 保证 |
| TC-B1-031 | 1-99 | 单元 | 执行成功操作 | 检查日志 | JSON 行含 timestamp/level/event/request_id | 存在 | `src/logging/logger.test.ts` |
| TC-B1-032 | 1-99 | 单元 | 未捕获异常 | 上报 | 含 request_id 可关联后端 | 存在 | `logger.test.ts`、`src/app/ErrorBoundary.tsx`；错误态另可一键复制诊断信息（TC-B1-042） |
| TC-B1-033 | 1-99 | 单元 | 生产构建 | 检查日志 | 无调试级/源码/token/答案 | 部分 | `logger.test.ts` 断言脱敏与 `debug` 丢弃分支；生产构建行为需在真实构建下复核 |
| TC-B1-034 | 1-99 | 单元 | 上报通道不可用 | 继续操作 | 本地缓冲+静默丢弃，主流程异常不被吞 | 存在 | `logger.test.ts` |
| TC-B1-037 | 1-10 | 组件 | 确认请求失败后用户重试 | 连点两次确认 | 两次请求带**同一个** `Idempotency-Key`（可重放的前提） | 存在 | `RosterImportPage.test.tsx`（首次 503 → 重试，断言键相同） |
| TC-B1-038 | 1-20 | 组件 | 名单超过一页 | 打开名单并点「加载更多」 | 逐页追加、显示已加载行数、到末页给出终点；契约未声明的字段（如 email）不渲染 | 存在 | `RosterPage.test.tsx`（2 个用例） |
| TC-B1-039 | 1-30 | 组件 | 成绩超过一页 | 点「加载更多」 | 逐页追加到末页，不静默丢行 | 存在 | `GradesPage.test.tsx` |
| TC-B1-040 | 1-30 | 组件 | 教师选定课程+课序 | 打开「谁还没交」 | 名单 × 成绩按 `student_id` 外连接；未提交/技术失败/评测未完成/已拒绝/已评分/未绑定账号各自成类；未提交不显示 0 分；姓名学号取自名单侧 | 存在 | `GradesPage.test.tsx`（3 个用例：分类、未绑定账号、学生不可见） |
| TC-B1-041 | 1-50 | 组件+安全 | 共用机器上的第二个用户 | 登录同一浏览器打开同一题 | 看不到前一个人的草稿；退出登录清空本机草稿；令牌 `sub` 作为身份来源可解析 | 存在 | `draft.test.ts`、`sessionStore.test.ts`、`SubmitPage.test.tsx`（真实 JWT 形状） |
| TC-B1-042 | 1-99 | 组件 | 任意错误态 | 点「复制诊断信息」 | 复制出 request_id + ProblemDetails + 契约版本，PII 被脱敏；剪贴板不可用时给出可手动选择的文本 | 存在 | `src/ui/components.test.tsx` |
| TC-B1-043 | — | 组件 | 弹窗 / 排序表 / 页面标题 | 键盘与读屏检查 | Escape 关闭、焦点进入并归还、Tab 被关在弹窗内；排序列有 `aria-sort`；每页恰好一个 `h1` | 存在 | `src/ui/components.test.tsx`（4 个用例）+ `GradesPage.test.tsx` 的 h1 断言 |
| TC-B1-044 | 1-40/50 | 组件 | 学生所在课程有作业 | 打开「作业」 | 列出作业（标题/状态/截止时间/题目数），进度来自**本人成绩行**；动作指向还没提交的题目版本，链接进入既有作答页 | 存在 | `src/features/assignments/AssignmentsPage.test.tsx` |
| TC-B1-045 | 1-40/50 | 组件 | 本人成绩行尚未返回 | 打开「作业」 | 进度列显示"加载中"，**不**显示"未提交"（分页/加载未完成不得下判定） | 存在 | 同上 |
| TC-B1-046 | 1-30/40 | 组件 | 教师选定课程+课序 | 打开「提交情况」 | 逐作业显示已提交/未提交（复用名单×成绩连接）；未绑定账号的行不参与判定；可展开逐人名单 | 存在 | 同上 |
| TC-B1-047 | 1-40/50 | 组件 | 已知 `assignment_id` | 打开作业详情 | 列出题目版本（可进题干页、可作答）；学生看到本人在每个版本上的提交状态；按 id 找不到时如实说明（契约无单取端点） | 存在 | 同上 |
| TC-B1-048 | 1-20 | 组件 | 账号超过一页 | 打开用户与角色 | 逐页追加、显示已加载行数、到末页给出终点（不静默截断 200 行） | 存在 | `src/features/users/UsersPage.test.tsx` |
| TC-B1-049 | T-10 | 单元 | 一次合法的出题表单 | 映射为 `QuestionDraftInput` | 标签归一化为小写+短横线；用例 `ordinal` 取数组下标；`revision_note` 只在 `as_new_version` 路径上发送；期望输出留空发 `null`（不是空串） | 存在 | `src/features/authoring/draft.test.ts`（13 个用例） |
| TC-B1-050 | T-10 | 单元+安全 | 受保护内容为空 / 比较策略未声明 / `kind=file` / 超限字段 | 构建请求体 | **不发请求**并逐条给出原因（契约没有说明"省略 `cases`"是保留还是清空，B1 不猜） | 存在 | `src/features/authoring/draft.test.ts`、`AuthoringPage.test.tsx` |
| TC-B1-051 | T-10 | 组件+契约 | 教师新建题目 | 选课程 → 填表 → 保存 | `course_id` 走**查询参数**；请求体含 `reference_answer`/`cases[]`/归一化 `tags`；新建不带 `revision_note`；返回后显示草稿不在默认列表里的原因 | 存在 | `src/features/authoring/AuthoringPage.test.tsx` |
| TC-B1-052 | T-10 | 组件+契约 | 编辑草稿 / 编辑已发布版本 | 保存 | 草稿就地改（不传 `as_new_version`、不发 `revision_note`）；已发布版本带 `as_new_version=true` 与 `revision_note`；受保护字段为空时**不发任何请求** | 存在 | 同上 |
| TC-B1-053 | T-10 | 组件+安全 | 受保护字段读不回来 | 打开编辑页 | 只预填公开 DTO 里存在的字段；其余为空并显式说明"空 = 读不到服务端现值" | 存在 | 同上 |
| TC-B1-054 | T-10 | 组件 | 未发布版本 | 打开出题页/题目详情 | 显示"待验证后才能发布" + 缺什么 + 由谁在 UI 之外补；**没有发布按钮**；学生看不到该面板 | 存在 | 同上 + `QuestionDetailPage.test.tsx` |
| TC-B1-055 | T-10 | 组件+安全 | 诊断无命中 / provider 未配置（409）/ 有建议 | 出题页两个面板 | 三种状态三种渲染；`disclaimers` 原文照录；模型建议与 `CMP-*` 规则外观与文案分离；"未启用"时没有建议表 | 存在 | `AuthoringPage.test.tsx`（4 个用例） |
| TC-B1-056 | 1-30 | 组件 | 报告含 CHG-0012 的三个证据块 | 下钻 | 资源峰值按单位显示；`limit_hit` 读作"哪个上限被触发"；文件改动列出期望/实际摘要与新建/修改/删除清单；`explanation` 标注为确定性规则 | 存在 | `GradesPage.test.tsx` |
| TC-B1-057 | 1-30 | 组件+安全 | 报告缺 `resource`/`artifacts`/`explanation`（B2 尚未下发） | 下钻 | 显示"未测量 / 无文件级结论 / 未给出说明"；**不出现任何"数值+单位"读数**（未测量 ≠ 0） | 存在 | 同上 |
| TC-B1-058 | 1-30 | 组件+安全 | 学生视图 | 下钻 | 期望摘要、期望输出与"是否一致"的结论全部不渲染、也不由前端推导 | 存在 | 同上 |
| TC-B1-059 | 1-40 | 组件+契约 | `listQuestions` 的 `tags` 与 `published_only` | 打开题目列表并筛选 | 标签按 `explode` 形式发重复参数；非法标签不入参且被列出；出题角色可切「包含草稿」（默认仍 `published_only=true`） | 存在 | `QuestionListPage.test.tsx` |
| TC-B1-060 | 1-40 | 组件 | 版本含 `revision_note`/`revision_diff` | 打开版本面板 | 显示修订说明与结构差异；`null` 条目留空（不是"无变化"）；首个版本说明"没有可比较的上一版" | 存在 | `QuestionDetailPage.test.tsx` |
| TC-B1-061 | 1-30/40 | 组件+诚实性 | 游标永远返回下一页（到 `MAX_AUTO_PAGES`） | 打开「谁还没交」/ 作业中心 / 作业详情 | 自动翻页真的走到 40 页上限；显示"分页被截断"的说明；**没有 spinner**；不渲染任何"未提交 / 已提交"判定 | 存在 | `GradesPage.test.tsx`、`AssignmentsPage.test.tsx`（4 个用例） |
| TC-B1-062 | 1-99 | 单元 | 数组型查询参数 | `buildQueryString` | 序列化为重复同名参数（`?tags=a&tags=b`），空数组等于不加该条件 | 存在 | `src/api/client.test.ts` |
| TC-B1-063 | T-10 | 组件+契约 | 修改草稿（CHG-0016 的响应类型） | 保存并检查审计日志 | PATCH 的响应按 `QuestionPublic` 消费；日志里的 `question_version_id`/`version` 是**服务端返回的** `versions[0]` 真值，不是 `undefined`（已做变异验证：还原旧写法即失败） | 存在 | `src/features/authoring/AuthoringPage.test.tsx` |
| TC-B1-064 | T-20 | 组件+契约 | 评语 `status=unavailable`（未配置 provider） | 打开下钻 | 显示"本部署未启用…这不是错误"；**不出现**通用错误框；`disclaimers` 仍然逐字渲染并说明"不参与判分" | 存在 | `src/features/commentary/CommentarySection.test.tsx` |
| TC-B1-065 | T-20 | 组件+契约 | 评语 `status=ready` | 打开下钻 | 渲染正文；`disclaimers` 三条**逐字**（精确匹配整段）；渲染出处与输入白名单 | 存在 | 同上 |
| TC-B1-066 | T-20 | 组件 | 评语 `status=failed` | 打开下钻 | 给出可读原因并明说"权威分与逐用例证据都不受它影响"；不渲染正文、不补出参考分 | 存在 | 同上 + `GradeDrilldown.test.tsx` |
| TC-B1-067 | T-20 | 组件 | 评语 `status=pending` | 打开下钻 | 显示"已受理、尚未完成"；不编造正文 | 存在 | 同上 |
| TC-B1-068 | T-20 | 组件+契约 | `GET` 返回 `404`（**真机实测**：尚未生成） | 打开下钻 | 渲染为**信息态**并把服务端 detail 原文带出来（`This is not the same as a missing run.`）；**不走**通用错误态 | 存在 | 同上（真机读数见 `../phases/active/phase-03-contract-surface-completion.md`） |
| TC-B1-069 | T-20 | 组件 | `GET` 403（学生策略关闭）/ 500 | 打开下钻 | 403 走"课程级策略由服务端判定"说明；500 走通用错误态并带 request_id；两者都不影响分数展示 | 存在 | 同上 |
| TC-B1-070 | T-20 | 组件+契约 | 教师/助教 vs 学生 | 点「生成评语」 | 生成请求只发一次，`202` 的响应体写回缓存（不再补一次 `GET`）；`409` 显示"未启用"并带出缺失配置项；学生看不到生成入口但阅读路径不受影响 | 存在 | 同上 |
| TC-B1-071 | T-30 | 组件 | `advisory_score` 非空 | 打开下钻 | 参考分与权威分在**同一区域并列**、权威分在前（`compareDocumentPosition`）；参考分带"顾问性/不参与判分/不作为申诉依据"标注 | 存在 | `GradeDrilldown.test.tsx`、`CommentarySection.test.tsx` |
| TC-B1-072 | T-30 | 组件+视觉 | `advisory_score` 非空 | 对比两块的视觉标记 | 参考分**不复用**权威分的 `score-authoritative`、**不含** `pill--ok`、无进度条主色；两个数值各自独立（夹具 86 vs 62，不合并） | 存在 | `GradeDrilldown.test.tsx` |
| TC-B1-073 | T-30 | 组件 | `advisory_score = null` | 打开下钻 | 参考分区块**不存在**（无 0 分占位、无空壳元素）；`ready` 时另有一句"本次没有产生参考分" | 存在 | 同上 |
| TC-B1-074 | T-30 | 组件+安全 | 服务端返回参考分但 `disclaimers` 为空（契约违规） | 打开下钻 | **拒绝渲染参考分**并报告契约违规事实（契约：渲染参考分却不带标注 = 把建议当成绩） | 存在 | `CommentarySection.test.tsx` |
| TC-B1-075 | P13-A | 组件+契约 | 教师登录，隔离区有多条记录 | 打开 `/review?tab=mail` | 请求显式带 `resolved=false`（契约未声明默认值）与 `limit`，并带 URL 的课程/课序；逐行显示识别性元数据、记录 scope 与已脱敏原因；`403` 显示权限说明而不是空列表 | 存在 | `src/features/review/MailReviewTab.test.tsx` |
| TC-B1-076 | P13-A | 组件+契约 | 点击某条未处理且可归属的记录 → 归属 | 从名单选学生 + 说明 → 确认 | 确认前**不发** POST；请求体**精确等于** `{student_id, note}`（`note` 留空发 `null`）；成功后该记录离开「未处理」列表；409 提示刷新且不假装成功 | 存在 | 同上 |
| TC-B1-077 | P13-A | 组件+诚实性 | 当前课序名单为空 / 有已绑定账号的行 | 打开归属抽屉 | 候选来自 **B4 名单**；空名单显示"当前课序没有可选择的学生"并禁用确认；`student_id` 为空的行不进候选（不伪造候选人）；换课序时强制重新取候选 | 存在 | 同上 |
| TC-B1-078 | T-20 | 组件+契约 | `failed` 运行（**真机实测**：`/report` 返回 404） | 打开下钻 | 报告错误照常展示，**评语轨仍然可达**（两轨分离）；证据包因内嵌报告而不再提供入口 | 存在 | `GradeDrilldown.test.tsx` |
| TC-B1-079 | T-30 | 单元+结构 | 成绩轨道的计算/导出模块 | 读取 `aggregate.ts`/`cohort.ts`/`useGrades.ts` 源码 | **反向断言**：三处都不出现 `advisory`（不求和、不排序、不参与导出）；并先断言"确实读到了这三个文件"以防重命名让检查变空 | 存在 | `CommentarySection.test.tsx` |
| TC-B1-080 | T-30 | 组件+契约 | 参考分只被显示 | 打开下钻并观察请求/日志 | 参考分不进入任何请求参数、不进日志数值（日志只记一个布尔"是否存在"） | 存在 | `src/features/commentary/useCommentary.ts` + 同上 |
| TC-B1-081 | CHG-0014 | 组件+契约 | 教师 / 学生 / 角色未知 | 打开证据包面板并下载 | `view` **显式**发送（`teacher` / `student`）；角色未知时不发请求并说明；`redactions` 两条逐字；`result_digest` 可见 | 存在 | `src/features/appeals/AppealPackPanel.test.tsx` |
| TC-B1-082 | CHG-0014 | 组件+安全 | 证据包已加载 | 点下载 | 下载的是**响应体原字节**（`Blob.size` 与 `text()` 双断言，夹具带缩进；只发一次请求）；403/409 不产生可下载文件 | 存在 | 同上 |
| TC-B1-083 | P13-B | 组件+契约 | 无 / 非法 `question_version_id`；深链带参数 | 打开 `/review?tab=quality` | 空参数**零请求**并说明；非法 UUID 不发请求；深链先经 B3 取版本再查询；本地时间转成 RFC 3339 `Timestamp` | 存在 | `src/features/review/QualityReviewTab.test.tsx` |
| TC-B1-084 | CHG-0015 | 组件+诚实性 | `security_found/not_found/not_checked` 各不同 | 打开质量页 | 三个数**各自成块**、各自带值；**反向断言**"合并值 41（27+14）在页面上不存在"；注明"没检查 ≠ 检查过没问题" | 存在 | 同上 |
| TC-B1-085 | CHG-0015 | 组件+诚实性 | `unmeasured_runs = 6` | 打开质量页 | 单独一块显示 6；**反向断言**该块内不出现安全检查的任何数字（"没有资源数据" ≠ "没有占用"） | 存在 | 同上 |
| TC-B1-086 | CHG-0015 | 组件 | `attempt_count = 4` / `= 5` / `= 0` | 打开质量页 | `<5` 提示"样本太少、不足以判断区分度"并说明门槛是本页阅读辅助；`=5` 不提示；`=0` 给"没有可读的信号" | 存在 | 同上 |
| TC-B1-087 | CHG-0015 | 组件 | `infrastructure_error` 与 `attempts=0` 的用例 | 打开质量页 | 系统原因从题目难度里单列；`0` 次尝试显示"无样例"而不是 `0%` | 存在 | 同上 |
| TC-B1-088 | CHG-0020 | 组件+契约 | 未选课程 / 已选课程 / 切换「包含草稿」 | 打开题目列表并选课程 | 无课程作用域时**零请求**并说明 `course_id` 是必需参数；有课程时**只发** `course_id` + `published_only`（契约未声明的参数一个不发）；覆盖度与列表的 `published_only` **同时**变化（两个数字必须来自同一作用域） | 存在 | `src/features/questions/QuestionCoveragePanel.test.tsx`、`QuestionListPage.test.tsx` |
| TC-B1-089 | CHG-0020 | 组件+诚实性 | `QuestionTagCoverage` 夹具（`12 + 4 == 16`，逐标签 9+9+3=21） | 打开覆盖度面板 | 展示并校验 `tagged + untagged == total` 这一条恒等式；**反向断言**逐标签计数之和 `21` 不出现在页面上（页面上不存在任何"标签合计"）；`untagged_questions` **单独一格**且该格内不出现任何标签计数 | 存在 | `QuestionCoveragePanel.test.tsx` |
| TC-B1-090 | CHG-0020 | 组件+诚实性 | 服务端返回不自洽的三项计数（`total=17`） | 打开覆盖度面板 | 如实报告不一致（`12 + 4 = 16 ≠ 17`），照原样显示服务端给的数，**不**挑一个数当真、**不**悄悄改成一致 | 存在 | 同上 |
| TC-B1-091 | CHG-0020 | 组件 | `tags=[]` 且 `untagged == total`（空库） | 打开覆盖度面板 | 渲染为**信息态**（"一个标签都没有…不是错误，也不是没有题目"）；不是错误态、没有空表；恒等式 `0 + 7 == 7` 照常展示 | 存在 | 同上 |
| TC-B1-092 | CHG-0020 | 组件+契约 | `404`（本部署取不到该端点） | 打开覆盖度面板 | 渲染为**数据驱动**的信息态（只说"本部署取不到这个端点"，不断言任何模块的实现进度）；**只发一次请求**；不渲染任何计数；明说**不**回退到"用列表在客户端数一遍" | 存在 | 同上 |
| TC-B1-093 | CHG-0020 | 组件+契约 | `403` | 打开覆盖度面板 | **逐字**带出服务端 detail，并明说"这不是「没有数据」"；**不**渲染成空态/错误态（反向断言没有"暂无数据"、没有计数格） | 存在 | 同上 |
| TC-B1-094 | CHG-0020 | 组件+契约 | 契约顺序的标签（含同数并列） | 打开覆盖度面板并点标签 | 渲染顺序**等于契约给的顺序**（同数按 tag 升序，非字母序；断言"不再排一次"）；每个标签的题目数可读、条形只相对最多的标签；点标签即填入**既有**标签筛选并以重复参数 `tags` 请求；**每个**标签都有按钮（CHG-0022 后形状与 `listQuestions.tags` 一致，防御分支已删除） | 存在 | `QuestionCoveragePanel.test.tsx`、`QuestionListPage.test.tsx` |
| TC-B1-095 | CHG-0019 | 组件+契约 | 未验证 / 全通过+存证已记录 | 打开出题页的「验证并发布」 | 未验证前**零请求**且请求体精确等于 `{question_version_id}`；一次点击只发一次（不自动重试）；全通过时出现发布按钮而**发布请求数为 0**（不自动发布），点击后才发 | 存在 | `src/features/authoring/ReferenceVerificationPanel.test.tsx` |
| TC-B1-096 | CHG-0019 | 组件+诚实性 | `attestation_id: null`（B3 未记录存证）而用例全通过 | 打开出题页的「验证并发布」 | 明说"B3 没有记录存证…**还不能发布**"；**不存在**发布按钮（全通过 ≠ 可发布） | 存在 | 同上 |
| TC-B1-097 | CHG-0019 | 组件+诚实性 | 逐用例 `failure_category`（内容失败 / 全 `infrastructure_error` / 混合） | 打开出题页的「验证并发布」 | 内容失败 → 逐用例呈现类别并说明"不是平台对题目质量的判断"；**全 `infrastructure_error` → "验证需要 Linux 沙箱环境"**，该文案**不含"成功"也不含"验证失败"**且不出现"未通过"结论；混合失败 → 不整体归因为环境 | 存在 | 同上 |
| TC-B1-098 | CHG-0019 | 组件+契约 | `503`（无沙箱平台）/ 403 / 404 / 发布 422 | 打开出题页的「验证并发布」 | `503` 与逐用例 `infrastructure_error` 走同一条环境结论并带出服务端说明；403 原样带出 detail；404 列出"版本不存在 / 端点未实现"两种读法**不猜**；发布 422 原样展示不伪装成功 | 存在 | 同上 |
| TC-B1-099 | CHG-0008/0019 | 组件+诚实性 | 结论摘要与当前版本不一致 / 版本无 `content_digest` / 切换版本 | 打开出题页的「验证并发布」 | 摘要不一致 → 结论失效、无发布按钮；拿不到摘要 → 不放行；**同一实例换版本 → 上一次的结论不再出现**（同一 Provider 下的 Harness，避免用 `rerender` 假通过）；已发布版本 → 面板不渲染 | 存在 | 同上 |
| TC-B1-100 | CHG-0017 | 单元+契约 | 两个受保护字段留空 / 只有空白 / 填了 | 保存草稿 | 留空 ⇒ 请求体里**不发** `reference_answer` 与 `cases`（省略 = 服务端保留已存值）；只有空白同样不发；填了就发且用例**整体替换**；`title`/`stem_markdown`/`language`/`adapter` 四项照发 | 存在 | `src/features/authoring/draft.test.ts`、`AuthoringPage.test.tsx` |
| TC-B1-101 | CHG-0017 | 组件+安全 | 编辑已有题目并提交用例（服务端那集合读不回来） | 勾选/不勾选整体替换确认后保存 | 未确认 ⇒ 报"会整体替换…本页读不到那份集合"且**不发请求**；确认后才发 PATCH；编辑路径以**零个用例**开场（不凭空渲染用例行），新建路径给一行邀请 | 存在 | `draft.test.ts`、`AuthoringPage.test.tsx` |
| TC-B1-102 | P13-A | 组件+诚实性 | `resolved` 的三态 / 可归属与不可归属的原因码 | 打开 `/review?tab=mail` | 默认请求显式 `resolved=false`；URL `resolved=all` 时请求**省略**该参数（契约不能表达"两者都要"）；可归属（`not_uniquely_matched`/`ambiguous_match`）显示归属按钮，不可归属（如 `archive_bomb`）**不显示** | 存在 | `src/features/review/search.test.ts` + `MailReviewTab.test.tsx` |
| TC-B1-103 | P13-B | 组件+诚实性 | `static_rule_hits` 含 `count=0`；`attempt_count=0`；`infrastructure_error` 存在 | 打开 `/review?tab=quality` | **0 命中行不被过滤**（`STAT-PY-013` 在场且为 `0`）；`attempt_count=0` 显示"没有评测记录"且**不**再叠"样本较少"；`infrastructure_error` 单列"系统问题"；三态安全与 `unmeasured_runs` 不合并、不转 0 | 存在 | `src/features/review/QualityReviewTab.test.tsx` |

| TC-B1-104 | P01 | 组件 | 匿名打开受保护深链 | 等待会话恢复 | 先显示骨架、不渲染受保护内容；去登录页并在 `?redirect=` 保留原站内路径（含筛选） | 存在 | `src/app/AppShell.test.tsx` |
| TC-B1-105 | P01 | 组件 | 已登录打开 `/` | 观察导航与页头 | 直接进入 `/assignments`；侧栏只出现角色允许的入口；页头没有契约版本、欢迎语或课程作用域 UUID | 存在 | `src/app/AppShell.test.tsx`、`src/app/navigation.test.ts` |
| TC-B1-106 | P02 | 组件 | 空字段 / 错误凭据 / 有效凭据 | 提交登录 | 空字段就地报错且不发请求并聚焦；401 统一文案；成功后回原深链或作业页；提交中只发一次 | 存在 | `src/features/auth/LoginPage.test.tsx` |
| TC-B1-107 | P01/P02 | 组件+安全 | `//host`、绝对 URL、反斜线/多层编码、`/login`、根路径 | 登录回跳 | 一律拒绝并落到站内默认页；basepath 下最终 URL 为 `/app/...` | 存在 | `src/auth/redirect.test.ts`、`LoginPage.test.tsx`、`src/app/routerBasepath.test.ts` |
| TC-B1-108 | P03 | 组件 | 学号格式错误 / 名单外身份 / 验证码错误 / 用户名冲突 / 注册成功 | 发码与创建账号 | 格式错误不发请求；服务端 409/422 映射到字段；失败保留身份字段、清空密码与验证码；成功后去登录并显示「账号已创建，请登录。」 | 存在 | `src/features/auth/RegisterPage.test.tsx`、`registrationValidation.test.ts`、`registrationErrors.test.ts` |
| TC-B1-109 | P01 | 组件 | 学生 / 教师 / 管理员 / 角色未知 | 打开应用外壳 | 学生 2 项、教师与管理员 4 项；角色未知 fail-closed；「用户管理」只在管理员账户菜单 | 存在 | `src/app/navigation.test.ts`、`AppShell.test.tsx` |
| TC-B1-110 | P01 | 组件+安全 | 抽屉打开/选页、主动退出登录 | 小屏导航与账户菜单 | 焦点进入抽屉，Escape/遮罩/选页都能关闭并归还焦点；退出只发一次 logout、清空本地查询缓存并进入不带 redirect 的登录页 | 存在 | `src/ui/components.test.tsx`、`AppShell.test.tsx` |
| TC-B1-111 | — | 组件 | `Field` 带 hint 与错误 / 无错误 | 读屏属性检查 | hint 与 error 经 `aria-describedby` 关联到控件；`aria-invalid` 只在出错时出现 | 存在 | `src/ui/components.test.tsx` |
| TC-B1-112 | P02/P03 | 组件 | 已登录用户访问 `/login` 或 `/register` | 直接打开 | 被送回作业页；不渲染第二套认证表单，也不显示已登录身份/退出按钮 | 存在 | `src/app/AppShell.test.tsx` |
| TC-B1-113 | P02 | 组件+安全 | 登录失败前后点击密码显示按钮 | 输入密码并触发 401 | 输入框 `type` 在 `password`/`text` 间切换；失败后按钮与可见状态仍保留，可继续查看/隐藏 | 存在 | `src/features/auth/LoginPage.test.tsx` |
| TC-B1-114 | P02 | 组件 | 邮箱格式错误 / 邮箱合法 | 点击「发送验证码」 | 格式错误就地报错且零请求；合法邮箱显示通用提示「如果该邮箱已注册…」并进入冷却 | 存在 | `src/features/auth/ForgotPasswordPage.test.tsx` |
| TC-B1-115 | P02 | 组件+安全 | 验证码错误 / 新密码过短 / 两次不一致 | 提交重置 | 验证码错误落到验证码字段；密码规则在本地拦截；不把服务端细节或账号存在性暴露给用户 | 存在 | `src/features/auth/ForgotPasswordPage.test.tsx` |
| TC-B1-116 | P02 | 组件 | 完整重置成功 | 邮箱 + 验证码 + 新密码提交 | 只发一次 `password-reset` 请求；成功后回 `/login?reset=done`，登录页显示「密码已重置，请使用新密码登录。」 | 存在 | `src/features/auth/ForgotPasswordPage.test.tsx`、`LoginPage.test.tsx` |

## 夹具政策（必须遵守）

- `src/testing/**` 的夹具**只**用于隔离不可控的 HTTP 边界与 jsdom 缺失的浏览器能力
  （Monaco/ECharts 的加载器以依赖注入方式替换，测试同时覆盖加载失败时的降级分支）。
- 生产代码**不引用** `src/testing/**`（已用全仓检索确认）；夹具不作为"功能已验证"的交付证明。
- 夹具的对象使用**生成的契约类型**标注，契约变化会让夹具先编译失败，避免测试停留在过期形状上。

## 覆盖自检

- 7 条课程需求全部覆盖：1-10→TC-001…005 与 037，1-20→006…009、038、048，1-30→010…014、039/040、046、056…058、061，
  1-40→015…019、035/036、044/045/047、059/060/061，1-50→020…025、041、044/045/047，1-60→026…030，1-99→031…034、042、062；
  043 是跨 RQ 的交互质量项。
- **团队追加需求 `RQ-T-10`（出题期 AI 辅助）**：TC-049…055。它是 `RQ-T-*` 编号，**不**计入上面 7 条课程
  需求，也不出现在答辩材料里（`rq-catalog.md` 第 6 节的编号规则）。
- **本批新增（契约已声明、界面此前没有出口）**：`RQ-T-20`（教学评语轨）→ TC-064…070、078/079；
  `RQ-T-30`（AI 评分参考）→ TC-071…074、079/080；`CHG-0014`（隔离区 + 证据包）→ TC-075…077、081/082；
  `CHG-0015`（题目质量）→ TC-083…087。`RQ-T-20/T-30` 是 `RQ-T-*` 编号，`CHG-*` 是**变更编号**
  （MAIN 把这两个界面指派给 B1，但没有新需求编号），两者都不计入 7 条课程需求。
- **CHG-0020（题库标签覆盖度）→ TC-088…094**：同样是**变更编号**，不新造需求编号
  （它是 `RQ-B-3-20` 的可用性补全）。7 个用例覆盖读取条件、恒等式、逐标签计数的读法、空库、404/403、顺序与筛选入口。
- **本批（契约 `0.15.0`）新增 → TC-095…102**：`CHG-0019`/`ADR-0015` 的"验证并发布"（TC-095…099）、
  `CHG-0017` 的省略语义与整体替换确认（TC-100/101）、`CHG-0021` 的 `resolved` 与可归属集合（TC-102）。
  同样是**变更编号**，不新造需求编号。
- **TC-B1-103（契约 `0.16.0` 复核后新增）**：静态规则目录的**呈现代码与文案一致**（0 命中行不被过滤）。
  起因是一次坐标复核：页面小标题写着"只含命中过的规则"、说明写着"契约没有给出规则目录…B1 拿不到规则全集"，
  而它当时正在渲染 B2 按**冻结目录**逐条给出的 15 条记录（**15 条 `count=0`**，真机 `GET` 读到）。
  TC-B1-087 里"规则表说明「从未命中」的清单不在本页"那半句随之删除。**未选择过滤**，理由见
  `../phases/active/phase-03-contract-surface-completion.md` 的"追加（0.16.0 复核）"。
- 状态分布（实测统计本表 103 行）：`存在` 87 条、`部分` 11 条、`待建` 5 条。`待建` 全部是**只能由提供方断言**
  或**被契约缺口阻塞**的场景，原因逐条写在表内，未用夹具冒充。
- **"能力有界面 ≠ 已端到端验证"的记录（本批已按真实浏览器读数改写）**：TC-075…077、TC-081/082、
  TC-083…087、TC-103、TC-088…094、TC-095…102 的夹具是契约形状；**2026-09-20 的真实浏览器复核**把其中两条
  变成了真实读数：**TC-088…094（覆盖度）已真实联通**（`total=102 / tagged=4 / untagged=98`，
  逐标签与独立 SQL 逐项一致，见 `../phases/active/phase-05-…` 的"真实浏览器复验"），
  TC-075/076（隔离区读取）在浏览器里渲染出真实空列表；TC-095…099（验证并发布）在浏览器里
  得到真实 `422`（草稿无评测包）并由 `CHG-0028` 处理。**2026-09-20 的 0.16.0 复核又升级了两条**：
  **TC-083…087/103（质量聚合）已真实联通**——真机 `GET` → `200`，`attempt_count=0`、
  `static_rule_hits` **15 条全为 `count=0`**、`security_*` 三项皆 0；**TC-071…073 的 `null` 分支已真实联通**
  （`/commentary` 响应现在带 `advisory_score` 键且为 `null`）。**仍未触达**：证据包面板
  （TC-081/082，嵌在成绩下钻里、报告缺失时不渲染；真机 `/appeal-pack` 得到 `404`）、
  带数据的隔离区归属（TC-076/077，库中无隔离记录）、带命中的质量聚合与**非空参考分**
  （TC-071/073、TC-084 的非零分支），这些只有契约与夹具证据。这些证明的是"界面按契约形状正确工作"，
  不是"服务端已经这么返回"。
  **更早真联调过的是 `/commentary`**（TC-064…070、078）：真机 `GET`/`POST` 与浏览器内逐条核对，读数见
  `../phases/active/phase-03-contract-surface-completion.md`。
- **两条"能力有界面 ≠ 已端到端验证"的记录**：TC-051…055 与 TC-056…058 的夹具是契约形状，
  但**没有**对着运行中的 B3/B2 实际写入过题目或读过带 `resource` 的真实报告（见 `../phases/active/phase-02-apple-redesign-and-e2e.md` §11）。
  它们证明的是"界面按契约形状正确工作"，不是"服务端已经这么返回"。
- **发布入口不再是缺口（本批推翻旧结论）**：`phase-02 §11 第 1 条` 当时记录的"UI 出题没有通往发布的路径"
  由 `ADR-0015` 与 `CHG-0019`/`CHG-0023` 解掉：TC-095…099 固定的是"请求验证 → 全通过且存证已记录 → 才可发布"，
  以及"全通过但 B3 没记下存证 → 仍不可发布"。**端到端仍未验证**（B2 端点未实现），
  因此这不等于"发布已经在真机上跑通"。
- **本批新增两条需要 MAIN 表态的缺口**：缺口 10（`adapter` 写必填、无读出口 → B1 只能重新构造 manifest）、
  缺口 11（可归属 `reason_code` 没有机器可读形式）；缺口 8/9 已关闭。见 `../collaboration/interfaces.md`。
- 契约缺口与阻塞的完整清单见 `../collaboration/interfaces.md`。
- TC-B1-016 由 `待建（阻塞）` 转 `部分` 的原因：题干图形的受控资源端点已由契约提供（缺口 1 关闭），
  B1 侧的实现与地址边界测试在位；剩余的一半（响应头与授权判定）只能由 B3 断言。
- TC-B1-019 由 `部分` 转 `存在` 的原因：契约缺口 2 已由 MAIN 关闭（版本 DTO 提供冻结题干 +
  题目 DTO 提供版本历史），B1 不再需要"显式说明缺口"这条降级路径，该路径与对应 i18n 词条
  `questions.versionStemUnavailable` 已随该轮一并删除。
- TC-B1-054 已随本批改写：它此前固定的是"诚实的未发布状态 + 没有发布按钮"，现在由 TC-B1-095…099 取代
  （`PublishRequirement.tsx` 与其 i18n 词条已删除，未保留双分支）。
