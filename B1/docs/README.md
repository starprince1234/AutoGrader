# B1 文档索引（唯一入口）

本文件是 B1 文档体系的**唯一索引**。新增 / 移动 / 归档任何 `docs/` 下的文档都必须同步更新本文件两张表；不在索引中的正式文档视为治理缺陷。

## 权威表

| 主题 | 权威文件 | 作用 |
|---|---|---|
| 需求编号/原文/验收 | `../../MAIN/docs/requirements/rq-catalog.md` | 7 条 B1 需求的唯一权威 |
| HTTP/事件/JSON 字段 | `../../MAIN/contracts/**` | 唯一机器可读契约；禁止手抄字段 |
| 契约版本与摘要 | `../contracts.lock` | 契约版本与 SHA-256 |
| 路由/事件/能力声明 | `../module.yaml` | `provides`/`requires`/`routes`/`events`/`database` |
| 本模块范围 | `requirements/scope.md` | 交付物、非目标、角色 |
| 需求追踪 | `requirements/traceability.md` | RQ → 实现 → 工作包 → 测试 → 证据 |
| 验收标准 | `requirements/acceptance.md` | 逐 RQ 可执行验收 |
| 架构与组件 | `architecture/overview.md`、`architecture/components.md` | 分层、组件职责、扩展点 |
| 状态机/时序 | `architecture/models.md` | 提交状态机、名单导入流程、SSE 时序 |
| 信任/数据边界 | `architecture/boundaries.md` | 能碰什么、不能碰什么 |
| 接口消费 | `api/contract.md`、`api/events.md` | 我提供/我消费的端点与事件（关系，非字段） |
| 数据库边界 | `database/schema.md`、`database/migrations.md` | 无权威数据库、浏览器本地能存/不能存 |
| 测试策略/用例 | `testing/strategy.md`、`testing/cases.md` | 分层门禁、用例目录 |
| 决策 | `adr/` | 本模块不可逆决策 |
| 运行手册 | `runbooks/local-development.md`、`runbooks/troubleshooting.md` | 可复制执行的命令与排障 |
| 阶段记录 | `phases/active/`、`phases/archive/` | 真实完成证据 |
| 协作 | `collaboration/*` | 冷启动、工作包、接口冻结、DoD、术语 |
| 知识笔记 | `knowledge/README.md` | 可复用结论索引 |

## 状态表

| 文档/能力 | 状态 | 最后验证方式 |
|---|---|---|
| 应用壳（`src/main.tsx` + `src/app/**`） | `已完成`（入口与外壳本轮重做，无独立首页） | `vite build` 实跑通过；`AppShell.test.tsx`（10 用例）+ `navigation.test.ts` + `routerBasepath.test.ts` 覆盖会话恢复门、根入口重定向、角色导航、账户菜单、退出与 basepath 回跳 |
| 契约锁 `contracts.lock` | `已完成` | 与 `../../MAIN/contracts` 摘要一致（**`0.22.0`**，摘要 `5ada1a87…`；含密码重置两条公开操作与 P13 的查询范围字段）；`generate-api-types.mjs --check` 实跑通过 |
| 找回密码（P02 补充） | `部分`（B1 已完成；B4 集成测试未跑） | `ForgotPasswordPage.test.tsx`（6 用例）+ `LoginPage.test.tsx`（忘记入口、失败后密码显示按钮保留、重置成功提示）：字段校验、发码冷却、统一错误文案、成功后回登录；`uv run --project MAIN python -m autograder_host.checks` 通过。**B4 pytest 需要 PostgreSQL/NATS，本机未执行**；真实 SMTP 与线上改密未验证 |
| 密码可见性 | `已完成` | `Field` 行内 `trailing` 控件 + 页面自维护显示/隐藏状态；`LoginPage.test.tsx` 固定"失败后仍可查看/隐藏密码" |
| `module.yaml` | `已完成` | 与 MAIN `manifest.schema.json` 一致 |
| 业务路由/角色守卫 | `已完成` | `src/app/router.tsx` + `src/auth/guards.tsx`；可见性单测通过 |
| 名单导入/编辑（RQ-B-1-10/20） | `已完成` | `pnpm test`：`RosterImportPage.test.tsx`、`RosterPage.test.tsx`、`mapping.test.ts`；确认幂等键按导入任务复用（`RosterImportPage.test.tsx` 固定"重试复用同一键"） |
| 作业中心（RQ-B-1-40/50 的入口侧） | `已完成` | `AssignmentsPage.test.tsx`：学生「我的作业」（截止时间 + 本人进度 + 挂到作答页）、教师「提交情况」（逐作业已提交/未提交，复用名单×成绩连接）、作业详情（题目版本 + 逐版本状态） |
| 出题写路径（RQ-T-10：新建/修改草稿） | `已完成` | `AuthoringPage.test.tsx`（12 用例）、`draft.test.ts`（18 用例）：`course_id` 走查询参数、`as_new_version` + `revision_note`、标签按契约形状归一化；受保护字段按 **CHG-0017 的省略语义**处理（留空 = 不发该键，服务端保留已存值），编辑时提交用例需一次显式"整体替换"确认。**端到端未验证**（没有对着运行中的 B3 写过题）。**UI 发布路径已由 CHG-0019 打通**（见下一行）；`phase-02 §11` 那条"无发布路径"的旧结论已推翻 |
| 出题辅助与确定性诊断（RQ-T-10） | `已完成` | `AuthoringPage.test.tsx`：模型建议与 `CMP-*` 规则分两块渲染；`409`/`status=unavailable` 显示「未启用」，与「检查过、无问题」区分；`disclaimers` 原文照录 |
| 逐用例证据可视化（RQ-B-1-30 验收 3） | `已完成` | `GradesPage.test.tsx`：资源峰值（未测量 ≠ 0）、`limit_hit` 读作"哪个上限被触发"、文件改动清单、确定性说明；学生视图不渲染也不推导受保护的期望摘要/期望输出 |
| 分页截断的诚实状态 | `已完成` | `GradesPage.test.tsx`、`AssignmentsPage.test.tsx`（4 用例）：`MAX_AUTO_PAGES` 触顶时显示截断说明、**不显示 spinner**、不下任何"未提交"判定；同时修掉"只走 2 页就停"的推进缺陷 |
| 题目/提交界面（RQ-B-1-40/50） | `已完成` | `SafeMarkdown/QuestionDetailPage/SubmitPage/progress/draft/validation` 测试通过；题干图形受控端点已落地（`useQuestionAsset.ts`），没有阻塞项 |
| 成绩/导出（RQ-B-1-30/60） | `已完成` | `GradesPage.test.tsx`、`aggregate.test.ts`；成绩按游标分页加载（「加载更多 / 已到末页」），汇总口径只覆盖已加载行 |
| 谁还没交（RQ-B-1-30 验收 4 的操作化） | `已完成` | `GradesPage.test.tsx`：名单 × 成绩按 `student_id` 外连接（未提交 / 技术失败 / 未绑定账号各自成类） |
| 用户与角色（RQ-B-4-50 的呈现侧） | `已完成` | `UsersPage.test.tsx`：账号列表按游标分页，不再静默截断 200 行 |
| 运行日志（RQ-B-1-99） | `已完成` | `src/logging/logger.test.ts`（8 用例）+ 错误态「复制诊断信息」（`src/ui/components.test.tsx`：request_id + ProblemDetails + 契约版本，PII 走 `redaction.ts`） |
| 教学评语轨（RQ-T-20） | `已完成`（**已真实联通**） | `CommentarySection.test.tsx`（14 用例）四态 + `disclaimers` 逐字 + 出处/输入白名单；真机 `GET`→200/`ready`、未生成→404（信息态）、`POST`→202（5882 ms，deepseek），浏览器里逐条核对，见 `../phases/active/phase-03-contract-surface-completion.md` |
| AI 评分参考（RQ-T-30，契约 `0.16.0`） | `部分` | `CommentarySection.test.tsx` + `GradeDrilldown.test.tsx`：参考分与权威分并列（权威分在先）、外观权重更低、`null` 不渲染、**不参与任何本地计算**（反向断言成绩轨道模块不含 `advisory`）。**`null` 路径已真实联通**（`0.16.0` 复核：`/commentary` 响应带 `advisory_score` 键且为 `null`）；**非空参考分的真机渲染未出现**（provider 对这几次 `failed` 运行返回 none） |
| 复核工作区 · 邮件提交（P13-A，含 B2 行级 scope） | `部分` | `features/review/MailReviewTab.test.tsx`（9 用例）+ `search.test.ts`：请求显式带 `resolved` 与课程/课序、403 显示权限说明而非空列表、未关联行仅管理员可见、详情只读契约字段、不可归属无入口、归属确认前不发 POST 且体只有 `student_id/note`、409 冲突、空候选禁用。**页面未部署**；跨课程隔离由 B2 的 DB 用例证明（本机未执行），B1 侧只有组件证据 |
| 复核工作区 · 题目质量（P13-B） | `部分` | `features/review/QualityReviewTab.test.tsx`（10 用例）：无版本/非法版本不发请求、B3 403/404 不拉 B2 聚合、三态安全与未测量分开、`infrastructure_error` 单列、零命中规则保留、0 次评测与样本不足的区分、时间校验与 RFC 3339、清除时间。**新页面未部署**；非空聚合仍只有夹具证据 |
| 证据包下载（CHG-0014） | `部分` | `AppealPackPanel.test.tsx`（6 用例）：`view` 显式发送、`redactions` 逐字、下载响应体原字节（字节数与 `text()` 双重断言）。**200 路径未触达**：真机 `GET .../appeal-pack?view=teacher` → `404`（failed 运行没有报告），本部署没有 `completed` 运行 |
| 题目质量热力图（CHG-0015） | `部分` | 已并入 `features/review/QualityReviewTab.tsx`（见上方"复核工作区 · 题目质量"行）；旧 `QualitySummaryPage.test.tsx` 随旧页面删除 |
| 题库标签覆盖度（CHG-0020 + CHG-0022） | `部分` | `QuestionCoveragePanel.test.tsx`（13 用例）+ `QuestionListPage.test.tsx`（2 用例）：`tagged + untagged == total` 是唯一被展示与校验的恒等式（反向断言逐标签之和 21 不在页面上）、`untagged_questions` 单独一格、`tags` 按契约顺序原样渲染、点标签复用既有筛选（CHG-0022 后每个标签都可筛，防御分支已删除）、`404` → "尚未在 B3 实现"信息态（只请求一次、不客户端兜底）、`403` 原样带出 detail。**对着已声明契约构建、未经真实 B3 验证**（本批未对运行中的 host 发过请求） |
| 「验证并发布」（CHG-0019 / ADR-0015） | `部分` | `ReferenceVerificationPanel.test.tsx`（15 用例）：两步显式动作（验证 ≠ 发布）+ 不自动重试/不自动发布；三态互不混淆（全通过可发布 / 未通过逐用例 `failure_category` / `503` 或全 `infrastructure_error` → "验证需要 Linux 沙箱环境"，文案不含"成功"也不含"验证失败"）；**全通过但 B3 未记录存证（`attestation_id: null`）→ 仍不可发布**；结论摘要与当前版本不一致 → 失效；换版本 → 结论不跨实例。**对着已声明契约构建、未经真实 B2 验证**（B2 尚未实现该端点） |
| 测试框架（vitest） | `已完成` | `vitest run`：**46 文件 / 398 用例**全部通过（exit 0）；本批新增找回密码、密码显示与公开页外壳用例共 10 个 |
| 真实 ESLint | `已完成` | `eslint . --max-warnings 0`：0 error / 0 warning |
| API 类型生成（ADR-0002） | `已完成` | `node scripts/generate-api-types.mjs` / `--check`（契约 **`0.22.0`**，`API types match MAIN/contracts.`） |
| 端到端（真实后端） | `部分`（覆盖度**已打通**；其余按页说明） | 真实 MAIN host `http://127.0.0.1:18000` + B2/B3/B4。**本轮真实浏览器复核**：`/app/questions` 的覆盖度面板渲染真实 B3 数据（`total=102 / tagged=4 / untagged=98`，逐标签与独立 SQL 一致）⇒ **覆盖度端到端成立**；旧隔离区与旧质量页当时正常渲染（空列表/筛选面板），但**已随 P13 合并删除，新 `/review` 尚未部署**；`/app/questions/<id>/edit` 的验证面板可用，但对**草稿**得到 B2 `422`（评测包只对已发布版本编译 → 该跨模块缺口已由 `CHG-0028` 处理，待 B3 落地后复测）。**证据包面板未在浏览器里打开**。**AI 参考分的非空渲染、`unavailable` 态、学生视图仍未做端到端**（原因见 phase-03 的"未验证项"） |

> 归档门禁与完成定义见 `collaboration/definition-of-done.md` 与 `phases/archive/README.md`。
> 当前阶段记录：`phases/active/phase-08-password-reset.md`（找回密码与密码可见性：B4 新增
> 两条公开认证操作与迁移 `20261027_0004`，B1 新增 `/forgot-password`、登录入口与页面自维护的
> 密码显示按钮；契约 0.22.0；含真实命令、门禁结果与未验证项）；上一阶段：
> `phases/active/phase-07-review-workspace.md`（P13 复核工作区：旧隔离区/质量两页
> 合并为 `/review?tab=mail|quality`，删除旧路由与旧测试；邮件标签带课程/课序筛选、详情与安全归属，
> 质量标签由 P07 带版本、先经 B3 再拉 B2 聚合；B2 同时补齐行级课程范围，见 B2 的 phase-10；
> 含真实执行命令与未验证项）；再上一阶段：
> `phases/active/phase-06-entry-shell-and-auth-redesign.md`（根入口、登录、注册
> 与应用外壳重做：删除独立首页，认证页独立布局，角色导航收敛到 2/4 项，深链回跳只接受
> 站内路径；含真实命令与未做的浏览器视觉验收）；再上一阶段：
> `phases/active/phase-05-authoring-publish-path-and-0-15-0.md`（出题发布路径打通
> `CHG-0019`/`CHG-0023` + `CHG-0017`/`CHG-0021`/`CHG-0022`/`CHG-0024`，以及**验收后修正**：
> 删除四条"断言对方实现进度"的常驻横幅、把过宽的 `503` 结论改为中性标题 + 原样 detail，
> 含真实浏览器读数与缺口 10/11/12 的收口；再上一阶段：
> `phases/active/phase-04-question-tag-coverage.md`（题库标签覆盖度视图，CHG-0020）；
> 再上一阶段：`phases/active/phase-03-contract-surface-completion.md`（教学评语轨、AI 参考分、
> 隔离区、证据包、题目质量））。
