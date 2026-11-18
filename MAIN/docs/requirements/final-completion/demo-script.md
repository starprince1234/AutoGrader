# AutoGrader 老师现场演示剧本（32 条需求全覆盖）

> **线上地址**：前端 `https://www.91idc.me`｜API `https://api.91idc.me`  
> **演示账号**：管理员 `admin`（全课程+运维日志+用户管理）；学生 `testuser`（课程 `08`、`07` 作业与本人成绩）。

---

## 0. 开场口径与已知限制（向老师口头说明）

1. **「班级」= 课序号**：教务导入和成绩维度的“班级”在本平台对应 `section`（课序号），系统内部 `class` 仅作默认分组容器，不参与成绩与权限隔离维度。
2. **课程↔语言与三类题型矩阵**：
   - `08 · Python编程与实践作业` 仅允许 `python` 题；`07 · Unix操作系统作业` 仅允许 `bash` 题（已在创建/改版/发布三处强制校验）。
   - 两门课均开放三类题型（`adapter.kind` / `io_type`，题目级不可变）：
     - `stdin`（命令行输入）：通过命令行参数与标准输入传参，比对标准输出与退出码；
     - `file`（文件读取）：平台物化测试输入文件（含用例级覆盖）进沙箱，学生程序读写文件；
     - `callable`（接口调用）：学生实现约定函数，Python 由平台 `import` 调用，bash 由平台 `source` 调用（不做平台预置库）。
3. **统一日志中心**：所有模块（B1–B5、MAIN）的运行日志与链路追踪统一在「运维日志」（`/app/ops/logs`）查看；教师仅能看授权课程内事件，平台级事件仅管理员可见；邮件仅作摘要与告警。
4. **已知限制**：
   - 存量 57 条无版本历史旧题的 `io_type` 为 `NULL`（等待裁定归档或补齐后再加 `NOT NULL`）；
   - 真实 `courseStudentList.xls`、真实中文 PDF、真实外部邮箱发信与服务器 `free -m` 内存占用需在演示机/服务器现场触发；
   - 管理员在浏览器登录页需使用 `admin`（学生端用 `testuser`）。

---

## 1. 32 条需求逐条演示剧本

### 1.1 全体模块（0-10 ~ 0-30）

| RQ | 打开页面 / 接口 | 点击 / 操作步骤 | 预期看到现象 | 代码位置 |
|---|---|---|---|---|
| **0-10** | `/app/questions` 或作业页 | 筛选 `08`（Python）与 `07`（Unix），各提交一次代码 | Python 与 bash 题均正常出分；若在 Python 课建 bash 题会返回 422 阻断 | [manifest.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/compile/manifest.py)、[python_checker.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/static/python_checker.py)、[bash_checker.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/static/bash_checker.py) |
| **0-20** | 文档 + 服务器终端 | 展示 `requirement-coverage-audit.md` 导图核对表；服务器跑 `free -m` | React 19+Vite+pnpm、FastAPI+nsjail、PG17、NATS 与导图逐项一致 | [requirement-coverage-audit.md](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/requirement-coverage-audit.md)、[resource-budget.md](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/architecture/resource-budget.md) |
| **0-30** | `/app/assignments`（学生 `testuser`） | 打开 `[样例作业] IO 三类型验收（课程 07）` 提交 bash 代码 | 显示评测状态变化并给出逐用例得分 | [AssignmentWorkspace.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/submission/AssignmentWorkspace.tsx) |

### 1.2 B1 用户界面（1-10 ~ 1-99）

| RQ | 打开页面 / 接口 | 点击 / 操作步骤 | 预期看到现象 | 代码位置 |
|---|---|---|---|---|
| **1-10** | `/app/roster` →「导入名单」 | 选课程/课序/班级，上传 `.xls`/`.xlsx`/`.csv`/文本 PDF →「上传并解析」 | 预览显示「表头位于第 N 行」与课程信息一致/不一致阻断提示，确认后入库 | [roster-import](file:///d:/VScodeProjects/AutoGrader/B1/src/features/roster-import)、[parsers.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/roster/parsers.py) |
| **1-20** | `/app/roster`、`/app/admin/users` | 管理员查看/编辑名单；换 `testuser` 登录观察侧边栏并直访 `/app/roster` | 管理员可编辑；学生端侧边栏隐藏入口，直访被拒且 API 返回 403 | [users.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/api/users.py)、[courses.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/api/courses.py) |
| **1-30** | `/app/grades` | 选课程→课序→在「学号 / 姓名查询」框输入关键字，清空后恢复；切学生看本人 | 教师见所辖课程全体成绩并可检索；学生仅见本人且可下钻详细报告 | [GradesPage.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/grades/GradesPage.tsx)、[query.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/grades/query.py) |
| **1-40** | `/app/questions/{id}` | 打开含文字与图片的题目详情页；未授权请求资产 URL | 安全渲染 Markdown 与受控图片；未授权取图返回 403 | [SafeMarkdown.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/questions/SafeMarkdown.tsx) |
| **1-50** | `/app/assignments/{id}`（`testuser`） | 输入代码点击「提交」，观察 SSE 状态流 | 实时推送队列/运行/完成状态并显示得分 | [useEvaluationStream.ts](file:///d:/VScodeProjects/AutoGrader/B1/src/features/submission/useEvaluationStream.ts) |
| **1-60** | `/app/grades` | 点击「导出成绩」，分别按当前课序/整门课程/带筛选导出 CSV 与 XLSX | 下载文件包含 `课程`、`课序`、`学号`、`姓名` 等完整列 | [export.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/grades/export.py) |
| **1-99** | `/app/ops/logs` | 在「日志检索」筛选 `模块=B1` | 可见 `app_bootstrapped`、`session_signed_in` 等前端事件，带 `request_id` | [logger.ts](file:///d:/VScodeProjects/AutoGrader/B1/src/logging/logger.ts) |

### 1.3 B2 代码管理（2-10 ~ 2-99）

| RQ | 打开页面 / 接口 | 点击 / 操作步骤 | 预期看到现象 | 代码位置 |
|---|---|---|---|---|
| **2-10** | `POST /api/v1/submissions` | 在作答页点提交（或抓 Network） | 请求符合 `submission-envelope.schema.json`，返回 `202` | [submission-envelope.schema.json](file:///d:/VScodeProjects/AutoGrader/MAIN/contracts/schemas/submission-envelope.schema.json) |
| **2-20** | `/app/review` | 切换到「邮件记录」标签，查看已匹配与隔离区邮件 | 展示发件人、匹配状态与隔离原因；可人工处理隔离邮件 | [MailRecordsTab.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/review/MailRecordsTab.tsx) |
| **2-30** | `GET /api/v1/submissions/{id}` | 查看提交详情文件列表与 `content_sha256` | 内容寻址存储，相同内容二次提交复用同一对象摘要 | [service.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/intake/service.py) |
| **2-40** | 作业作答页 | 提交带语法错误的 Python/bash 代码，查看报告 | 输出静态检查总评与逐行 `rule_id` 细评（如 `STAT-SH-001`） | [checker.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/static/checker.py) |
| **2-50** | `/app/grades` 下钻报告 | 点开任意已完成评测记录的详情 | 展示总得分、是否通过及逐用例输入/期望/实际输出证据 | [orchestrator.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/evaluation/orchestrator.py) |
| **2-60** | `/app/grades` | 导出成绩 + 点击「下载申诉包」 | 评测过程与结果已持久化入 `b2`；导出与申诉包 ZIP 正常下载 | [appeals](file:///d:/VScodeProjects/AutoGrader/B1/src/features/appeals) |
| **2-70** | 作业作答页 | 提交含危险系统调用/外连代码 | 安全扫描与 nsjail 沙箱拦截并记录 `security_finding` | [decision.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/security/decision.py) |
| **2-80** | `/app/questions/{id}`（教师） | 点击「参考答案验证」 | 按评测同链路运行参考答案，给出总评与逐用例细评 | [ReferenceVerificationPanel.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/authoring/ReferenceVerificationPanel.tsx) |
| **2-99** | `/app/ops/logs` | 筛选 `模块=B2`，点开某条记录或切换「链路追踪」 | 可见 `evaluation.run_completed` 等事件并按 `submission_id`/`trace_id` 追踪 | [OpsLogsPage.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/ops-logs/OpsLogsPage.tsx) |

### 1.4 B3 题库管理（3-10 ~ 3-99）

| RQ | 打开页面 / 接口 | 点击 / 操作步骤 | 预期看到现象 | 代码位置 |
|---|---|---|---|---|
| **3-10** | `/app/questions/new` | 填写题干、选定 IO 类型、写参考答案与测试用例并保存/发布 | 创建草稿 201；学生端不可见参考答案与隐藏用例 | [questions.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/domain/questions.py) |
| **3-20** | `/app/questions` | 使用「课程」「发布状态」「IO 类型」「标签」筛选 | 只返回对应课程题目，教师跨课程查询被 403 隔离 | [router.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/api/router.py) |
| **3-30** | `/app/questions/{id}` | 查看题目详情页「答题统计」区块 | 展示作答人数、通过率与分数/错误分布 | [service.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/stats/service.py) |
| **3-40** | `/app/assignments` | 打开 `[样例-Python-命令行]` 与 `[样例-Unix-命令行]` 提交对/错代码 | 命令行参数与 stdin 传参，通过/不通过符合预期 | [adapters.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/compile/adapters.py) |
| **3-50** | `/app/assignments` | 打开 `[样例-Python-文件]`，下载可见附件并提交读文件代码 | 可见附件可下载（200），隐藏 fixture 不可访问（403），用例级输入生效 | [fixtures.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/runner/fixtures.py) |
| **3-60** | `/app/assignments` | 打开 `[样例-Python-接口]` 与 `[样例-Unix-接口]` 提交函数实现 | Python 通过 `import` 调用、bash 通过 `source` 调用并出分 | [adapters.py](file:///d:/VScodeProjects/AutoGrader/B2/src/autograder_b2/runner/adapters.py) |
| **3-99** | `/app/ops/logs` | 筛选 `模块=B3` | 可见 `question.created`、`request.completed`（含 `status_code`/`method`/`path`） | [logging.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/logging.py) |

### 1.5 B4 数据库与平台维护（4-10 ~ 4-99）

| RQ | 打开页面 / 接口 | 点击 / 操作步骤 | 预期看到现象 | 代码位置 |
|---|---|---|---|---|
| **4-10** | `GET /health/ready`、`/api/v1/modules/{m}/health` | 浏览器或 curl 访问健康检查接口 | 全模块 `healthy: true`，Postgres 可达且迁移头匹配 | [main.py](file:///d:/VScodeProjects/AutoGrader/MAIN/host/src/autograder_host/main.py) |
| **4-20** | `GET /health/ready` | 查看 B4 与 B2 健康详情 | 显示 NATS 可达、消息派发未发布 0 条、DLQ 0 条 | [messaging.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/ops/messaging.py) |
| **4-30** | 仓库文档 `B*/docs/database/schema.md` | 展示表结构文档与 `B*/alembic/versions/` 迁移清单 | 迁移版本与数据库表规范一致 | [table_standards.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/ops/table_standards.py) |
| **4-40** | `GET /api/v1/platform/conformance`、`/api/v1/modules` | 访问一致性审计接口与展示 `MAIN/contracts/` | `consistent: true`，103 条契约路径无差异，5 份锁摘要为 `0.43.0` | [conformance.py](file:///d:/VScodeProjects/AutoGrader/B5/src/autograder_b5/conformance.py) |
| **4-50** | `/app/admin/users` | 管理员创建、启停、修改角色/密码；测试登录/登出 | 停用账号立即拒绝登录（401），学生访问返回 403 | [users.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/api/users.py) |
| **4-99** | `/app/ops/logs` →「审计记录」 | 切换「审计记录」标签，或筛选 `模块=B4` | 展示账号变更、导入等业务审计记录与 B4 运行日志 | [audit.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/api/audit.py) |
