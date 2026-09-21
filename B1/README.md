# AutoGrader B1 —— Web 前端客户端

B1 是 AutoGrader 的**静态 Web 客户端**（TypeScript + React 19 + Vite 7），面向学生 / 教师 / 助教 / 管理员，提供名单导入与编辑、富文本题目展示、代码提交与实时评测进度、成绩查询与导出的**交互界面**。B1 **没有后端、没有权威数据库**，所有权威数据都来自 B4（身份/课程/名单）、B3（题目）、B2（提交/评测/成绩）。

> 当前状态：业务界面与端到端 `已完成`；上一轮新增题库标签覆盖度视图（`CHG-0020`）；
> **本轮（`0.15.0` → `0.16.0`）打通了出题的最后一段：出题页的「验证并发布」入口**——
> `CHG-0019`/`ADR-0015` 的"B2 执行、B3 记录、教师一键触发"，并同时落地 `CHG-0017` 的草稿省略语义
> （`cases`/`reference_answer` 留空即不发该键，服务端保留已存值）、`CHG-0021` 的隔离区语义修复
> （`?resolved=false` 显式、不可归属不显示按钮）与 `CHG-0022`（标签形态约束补齐后删除了对应防御分支）。
> 工具链已实跑：ESLint、`tsc -b`、`api:check`、
> Vitest（32 文件 / 263 用例，exit 0）、`vite build`。
> **真实浏览器复核（宿主 18000 + 真实 B2/B3）**：覆盖度**端到端成立**（`total=102 / tagged=4 /
> untagged=98`，逐标签与独立 SQL 一致）；隔离区与质量两页正常渲染；`/commentary` 此前已真机联通。
> 验证端点可用，但对**草稿**返回 `422`（评测包只对已发布版本编译）——该跨模块缺口已由 `CHG-0028`
> 处理、待 B3 落地后复测。**UI 出题此前"没有发布路径"的结论本轮被推翻**：
> 发布入口由 `ReferenceVerificationPanel` 提供（验证是请求平台验证，不是自我声明），
> 仍受服务端门禁约束（全通过 ∧ 存证已记录 ∧ 摘要未变）。
> **验收后修正**：界面上四条"B2/B3 尚未实现"的常驻横幅**已删除**（那类断言没有过期机制，
> 端点上线后与实时数据自相矛盾）；`503` 的结论改为中性标题 + **原样保留服务端 detail**。
>
> 真实命令、结果、未执行项与契约缺口见
> [`docs/phases/active/phase-05-authoring-publish-path-and-0-15-0.md`](docs/phases/active/phase-05-authoring-publish-path-and-0-15-0.md)、
> [`phase-04-question-tag-coverage.md`](docs/phases/active/phase-04-question-tag-coverage.md)、
> [`phase-03-contract-surface-completion.md`](docs/phases/active/phase-03-contract-surface-completion.md)
> 与 [`phase-02-apple-redesign-and-e2e.md`](docs/phases/active/phase-02-apple-redesign-and-e2e.md)。
>
> 本环境的 `pnpm` 经 DSH runner 启动时用管道 stdio，Node 的 `child_process` 管道被沙箱拒绝
> （`spawn EPERM`），因此验证时直接调用 `node_modules` 下的等价命令（`tsc`/`eslint`/`vitest`/`vite`）；
> `package.json` 中的 `pnpm *` 脚本本身未改动。

## 已实现能力

- **应用壳与路由**：`@tanstack/react-router` 代码式路由表 + 认证/角色可见性守卫（fail-closed）；`@tanstack/react-query` 数据层。
- **名单与权限（`RQ-B-1-10/20`）**：PDF/XLSX 导入向导（上传 → 解析预览 → **人工列映射（不自动猜测）** → 逐行错误 → 幂等确认 → 结果报告，被拒行不丢弃、敏感列脱敏）；名单编辑表格（动态列 + 并发冲突 409 提示）；用户/角色管理页。
- **题目、出题与提交（`RQ-B-1-40/50`、`RQ-T-10`）**：安全 Markdown 子集渲染器（无 `innerHTML`、URL 白名单、公开 DTO 受保护字段双重校验）；**出题写路径**（新建草稿 `POST /questions?course_id=`、修改草稿 `PATCH /questions/{id}`，已发布版本走 `as_new_version` + `revision_note`），受保护字段（参考答案/用例/适配器细节）**读不回来**因此每次保存都必须重新声明；题目列表的标签列与标签筛选、版本结构差异；Monaco 编辑器（可注入加载器 + 文本降级）；提交前校验与契约信封构造（SHA-256 + base64 + 幂等键）；SSE 进度（`sequence`/事件 id 去重、`Last-Event-ID` 退避重连、终态三态分开展示）；本地草稿恢复。
- **成绩、导出与日志（`RQ-B-1-30/60/99`）**：成绩筛选与**已加载行**汇总、分数分布、逐项证据下钻（学生/教师视图，学生视图不下发期望输出与期望摘要）；**逐用例证据卡片**（资源峰值 / 文件改动 / 确定性说明，`null` 一律渲染"未测量"而不是 0）；调 B2 导出端点下载（**不重新拼接成绩数据**）；结构化 JSON 日志（含 `request_id`、拒绝清单脱敏、上报失败静默丢弃）。
- **出题辅助与诊断（`RQ-T-10`）**：`POST /questions/{id}/authoring-assistance` 的**模型建议**与 `GET /questions/{id}/diagnostics` 的**确定性 `CMP-*` 规则**分两块渲染，`disclaimers` 原文照录；未配置 provider（`409`）显示"未启用"，与"检查过、无问题"是不同状态。
- **教学评语轨与 AI 参考分（`RQ-T-20/T-30`，契约 `0.16.0`）**：成绩页的证据下钻里读 `GET /evaluation-runs/{id}/commentary`，四态（`unavailable`/`pending`/`ready`/`failed`）**分别渲染**，`404`（本部署实测 = "尚未生成"）渲染为信息态而不是错误，`disclaimers` **逐字**显示且永不为空，出处（provider/model/prompt 版本）与**输入白名单**一并列出；教师/助教可 `POST` 生成（`409` → "未启用"）。`advisory_score` 与**权威分并列**但外观权重更低（灰底虚线、`pill--idle`、独立类名），`null` 时整块不渲染，**不参与任何本地计算**（不求和/不排序/不比较/不导出，有反向断言守着）。
- **隔离区复核台与证据包（`CHG-0014`）**：`/intake/quarantines` 读隔离记录（`resolved` 始终显式发送、`reason_code` 筛选、游标分页），可手动归属到学生（体只有 `student_id`/`note`；候选学生来自**教师可见的名单**，拿不到候选时如实说明）；证据下钻里按需加载 `GET /evaluation-runs/{id}/appeal-pack`，`view` 由角色显式推出，`redactions` 逐条列出，下载的是**响应体原字节**。
- **题目质量热力图（`CHG-0015`）**：`/quality?question_version_id=…` 读每版本的判分聚合（题目详情页有教师入口）。`security_not_checked` 与 `security_not_found` **分开**、`unmeasured_runs` **单列**（"没看"不等于"看了没问题"，"没有数据"不等于"用了 0"），`infrastructure_error` 从难度里单列，`attempt_count < 5` 提示样本不足。
- **题库标签覆盖度（`CHG-0020`，契约 `0.14.0`）**：题目列表页的「标签覆盖度」面板读 `GET /questions/coverage`（与列表**共用**同一个课程与 `published_only`，因此两个数字永远来自同一作用域）。`total_questions` / `tagged_questions` / `untagged_questions` 各自成格；页面上**唯一**被展示与校验的恒等式是 `tagged + untagged == total`——逐标签计数按设计重叠（一题最多 20 个标签），所以**不显示任何"标签合计"**，条形也只相对最多的标签。标签按契约顺序原样渲染（不再排一次），点一下即填入既有的标签筛选。`404`（B3 尚未实现）是信息态，**不**回退到"在客户端数一遍"；`403` 原样带出服务端 detail。
- **验证并发布（`CHG-0019` / `ADR-0015`）**：出题页与题目详情页的未发布版本上，`ReferenceVerificationPanel` 请求平台验证参考解答（`POST /reference-verifications`：**B2 执行、B3 记录**，教师用普通令牌触发——这不是自我声明），并把结论分三态呈现：**全通过且存证已记录 → 出现发布按钮**（发布是独立的第二个显式动作，从不自动串起来）；**未通过 → 逐用例失败类别**；**`503` 或逐用例全是 `infrastructure_error` → "验证需要 Linux 沙箱环境"**（本机 Windows 的常态，既不写成"验证失败"也不伪装成功）。**全通过但 `attestation_id` 为 `null`（B3 没记下存证）仍然不可发布**——执行者与记录者分离正是这条门禁的意义。响应里没有期望输出、隐藏用例输入与参考解源码，界面也不提供这类入口。点击都会真的起一次沙箱作业，因此不自动重试。
- **草稿省略语义（`CHG-0017`）**：`cases` 与 `reference_answer` **留空 = 不声明该字段**（请求体里不发这两个键，服务端保留已存值）；填了才覆盖，提交用例会**整体替换**该版本的集合，因此在读不到服务端现值时必须显式确认一次。`title`/`stem_markdown`/`language`/`adapter` 是契约的 `required`，每次照发。
- **契约类型生成**：`node scripts/generate-api-types.mjs` 从 `MAIN/contracts/openapi/*.yaml` 生成 `src/api/generated/*.ts`，`--check` 拦截漂移（ADR-0002，禁止手写 DTO）。
- 契约锁 `contracts.lock`（`contract_version: 0.16.0`，`status: baseline`）与模块清单 `module.yaml`（`provides: [web-ui]`、`entry_point: static:dist`、`routes: []`）。

## 明确非目标（B1 不做）

- 不解析、不落库名单数据（解析与写入由 B4 承担），B1 只做上传、列映射、预览与确认交互。
- 不持有任何权威学生 / 课程 / 成绩 / 题目 / 评测数据；不做成绩汇总的权威口径（页面统计只覆盖已加载行并如实标注）。
- 不直接连接 PostgreSQL / NATS，不导入 B2/B3/B4 的任何代码或 ORM。
- 不自行定义 API 字段；不把前端隐藏按钮当作安全边界。
- 不把令牌写入 `localStorage` / `sessionStorage`（访问令牌仅在内存，刷新凭据由 B4 以 `HttpOnly` cookie 下发）。

## 在系统中的位置

```text
浏览器 (B1 static UI)
   │  同源 HTTP / SSE（经 MAIN composition host 代理到 /api）
   ▼
MAIN composition host ── B4 身份/课程 ── B3 题目 ── B2 提交/评测/成绩
```

B1 构建产物 `dist/` 由 MAIN host 在 `/app` 静态提供。

## 前置条件

- Node.js 24（pnpm 的运行时）
- pnpm（通过 `corepack enable` 启用）
- 环境变量：复制 `.env.example` 为 `.env`。`VITE_API_BASE_URL` 是 **API 源**（缺省=同源），不是路径前缀——契约路径本身已含 `/api/v1`，详见 [`docs/api/contract.md`](docs/api/contract.md)。

## 安装 / 启动 / 验证

```powershell
corepack enable
pnpm install

pnpm dev          # 开发服务器（HMR，默认 5173）
pnpm lint         # 真实 ESLint，0 warning 门槛
pnpm typecheck    # tsc -b（含生成的契约类型）
pnpm api:check    # 生成类型与 MAIN/contracts 的漂移门禁
pnpm test         # Vitest（jsdom + Testing Library）
pnpm build        # tsc -b && vite build → dist/
pnpm verify       # 一键串联上面五步
```

> 未执行：`pnpm dev`（需交互式浏览器会话；端到端改用 Playwright MCP 驱动已构建产物，见 README 顶部的阶段记录链接）。

## 用户入口

- 本地开发：`http://localhost:5173`（Vite 默认，以 `pnpm dev` 输出为准）
- 集成部署：由 MAIN host 在 `/app` 提供 `dist/` 构建产物（本轮已在 `http://127.0.0.1:18000/app` 实跑）

## 文档入口

全部文档索引见 [`docs/README.md`](docs/README.md)。冷启动路径见 [`docs/collaboration/onboarding.md`](docs/collaboration/onboarding.md)。

## 已知限制与阻塞

1. **契约缺口（需 MAIN 判定 CHG，B1 未修改 `MAIN/contracts/**`）**：若干 `200` 响应无 schema（课序/审计记录/模块健康）、成绩无汇总端点、缺"当前用户资料"端点（提交信封必填 `student_id`）。**"题目图形无下载端点"与"版本 DTO 无题干字段"两条已随契约变更关闭。** 完整影响面见 [`docs/collaboration/interfaces.md`](docs/collaboration/interfaces.md)。
2. **带分数的成绩证据未走通**：本环境是 Windows，B2 的沙箱阶段如实返回 `infrastructure_error`（ADR-0004 / SEC-20 禁止降级为裸进程），因此端到端只验证到"技术失败"终态与"未完成不显示 0 分"这条路径；真实 `completed` + 分数需要 Linux 特权容器。**CHG-0012 的逐用例证据字段（`resource`/`artifacts`/`explanation`）B2 尚未下发**，界面按"未测量 / 无数据"渲染，真实读数需 B2 落地后复核。
3. **成绩行没有姓名/学号**：B2 的 `student_number` / `display_name` 恒为 `null`（B2 只拥有 `b2`，不读 `b4`，并明确拒绝伪造占位值）。页面因此显示"学生 ID"，表头也相应改为"学生"而不是"学号"。
4. **构建体积警告**：Monaco 的 `editor.main` 约 3.77 MB，未做按语言裁剪（原因见 phase-01 记录）。
5. **ADR-0007 的 `VITE_API_BASE_URL=/api` 与本模块取值不同**（契约路径已含 `/api/v1`），已在文档中记录待 MAIN 判定。
6. **发布路径已打通（本批推翻旧结论）**：`CHG-0008` 的存证门禁要求"执行者 ≠ 记录者"，B1 既不能自证、
   也不能持有 `serviceToken`；`ADR-0015` 把它裁决为 **B2 执行、B3 记录、教师一键触发**，契约是
   `CHG-0019` 的 `POST /api/v1/reference-verifications`（`CHG-0023` 补上 B2 取参考解答的内部通道）。
   因此出题页现在有「验证并发布」入口，且**仍然受服务端门禁约束**：可发布 = 全通过 ∧ 存证已记录
   ∧ 摘要未变。旧记录（`phase-02 §11` 第 1 条"没有发布路径"）已同步改写，不是并排留两条。
   **真实浏览器读数**：对**草稿**点击验证 → B2 返回 `422`「no evaluation package yet
   (B3 compiles one only for a published version)…」，页面原样展示该 detail + `request_id`；
   即**这条链今天还走不通**（草稿被 `422` 挡住、已发布版本按设计没有入口）。该跨模块缺口已由
   **`CHG-0028`** 正面处理（评测包与存证对草稿开放），**B1 无需改动**，待 B3 落地后复测。
7. **受保护字段的读回与写回（本批按 `CHG-0017` 改了行为）**：`reference_answer`/`cases[]` 仍**读不回来**，
   但现在**留空 = 不声明该字段**（请求体里不发这两个键），服务端保留已存值；填了才覆盖，
   且提交用例会**整体替换**该版本的集合（因此编辑时需要显式确认一次）。
   **适配器 manifest 是另一回事**：`adapter` 是必需字段却**没有读出口**（只读得到 `adapter_kind`
   与两个上限），所以保存是**重新声明**整个 manifest，而不是继承已存那份——界面已常驻说明
   （见 [`docs/collaboration/interfaces.md`](docs/collaboration/interfaces.md) 缺口 10）。
8. **`kind=file` 的题目无法从 UI 创建**：夹具字节必须经 `internal` + `serviceToken` 的 fixtures 端点上传，
   摘要由服务端计算；页面明确拒绝而不是编造摘要。**顺带一条推论**：B1 因此也无法对声明了 `adapter.files`
   的版本"先分支再发布"（它在保存那一步就被拦住），所以 B3 上报的"分支版本不继承 fixture"对 B1 界面不可达。
9. **出题写路径的诚实性修复没有端到端证据**：出题写路径、辅助/诊断端点与证据字段只经过契约夹具与生成类型校验，
   **没有**对着运行中的 B3/B2 实际写题或读报告。
10. **`/intake/quarantines`、`/appeal-pack`、`/quality-summary` 的端点都已可用，但证据强度不同**：
   端点都真实存在，`0.16.0` 复核的真机读数是：隔离区 `GET` → **200**（空列表，读路径联通）、
   质量聚合 `GET ?question_version_id=…` → **200**（`attempt_count=0`、15 条规则全为 `0` 命中）、
   证据包 `GET ?view=teacher` → **404**（`A report exists only after a successful run.`，
   本部署所有运行都是 failed，**成功响应体从未被读到**）。因此**带数据的**隔离区渲染与归属写路径、
   证据包的 200 路径、质量聚合的非空数据分支仍只有契约夹具与组件测试证据。
   页面**不再**挂"B2 尚未实现"的证据边界说明——那类由静态文案断言别人实现进度的横幅
   **已按裁决全部删除**（端点不可用改由**数据驱动**的信息态说明）。
   `/commentary` 此前已真机跑通（`GET`→200/`ready`、未生成→404、`POST`→202，见
   [`docs/phases/active/phase-03-contract-surface-completion.md`](docs/phases/active/phase-03-contract-surface-completion.md)）。
11. **AI 参考分的非空渲染只有契约证据**：`0.16.0` 复核时 `/commentary` 响应里**已有** `advisory_score`
   键，但 `GET` 与一次真实 `POST`（`202 / 7497 ms`）读到的都是 **`null`**——本部署的 provider 对
   这几次 `failed` 运行返回 none。因此"`null` 不渲染 + 说明本次没有产生"已真实联通，**非空值从未在
   真机出现**。同样只有夹具证据的还有 `status: unavailable`（本部署配置了模型 provider）与评语的学生视角。
12. **`GET /commentary` 的 `404` 语义未写进契约正文**（本部署实测 = "尚未生成评语"，detail 明确
   "不等于运行不存在"）。B1 按实现的事实渲染为信息态，并已登记为契约表述缺口等待 MAIN 裁定；
   `quality-summary` 的授权集合与"样本很少"的门槛也属 B1 的推断（后者在界面上明说是本页的阅读辅助）。
13. **覆盖度已真实联通；"验证并发布"有真实读数但链未通**：
   `/api/v1/questions/coverage` 在真实浏览器 + 真实 B3 上成立（`total=102 / tagged=4 / untagged=98`，
   逐标签与独立 SQL 一致），页面把 `404` 渲染成**数据驱动**的"本部署取不到这个端点"
   且**不**回退到客户端数题；验证面板把 `503`/`infrastructure_error` 渲染成
   **中性的"平台依赖不可用"并原样保留服务端 detail**（不推断是哪一类依赖）。`CHG-0022` 已把标签
   形态约束补进契约，B1 随之删除了那段防御分支（缺口 8 关闭）；缺口 10（`adapter` 无读出口）与
   缺口 11（可归属 `reason_code` 没有机器可读形式）**均已被 MAIN 采纳**：`CHG-0027` 给**响应字段**
   `reason_code` 加 19 项 `enum`（**已随 `0.16.0` 落地**：B1 的 `ReasonCode` 别名现在就是 19 项
   字面量联合，词表核对回到编译期），但**可归属的那一对值继续手写且属明确接受**——它是行为谓词，
   而"假的机器可读比散文更危险"；`CHG-0025` 的 `draft-input` 读出口**未随 `0.16.0` 落地**，
   因此 `adapter-not-readable` 说明保持不变（它陈述的是**本页当前的行为**，不是别人的进度）；
   另有 B1 真实浏览器发现的跨模块缺口（草稿无法验证）已由 `CHG-0028` 正面处理。
