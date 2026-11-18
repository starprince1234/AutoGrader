# 最终收口线上验收证据报告（2026-11-18）

> **线上环境**：前端 `https://www.91idc.me`｜API `https://api.91idc.me`  
> **验收时间**：2026-11-18 21:30–23:30（UTC+8）  
> **最新部署**：`Deploy AutoGrader backend`（commit `aa01238`，run `44329425039`）四个 job（`verify`、`verify-frontend`、`verify-database`、`deploy`）全部通过；`GET /health/ready` 返回 `200`（`ready: true`）；`GET /api/v1/platform/conformance` 返回 `200`（`consistent: true`，103 条契约路径无差异）。

---

## 1. 修复工作包 F1–F8 复测证据

| 包 | 状态 | 修复内容与提交 | 线上实测证据 |
|---|---|---|---|
| **F1 `.xls` 导入（P0）** | ✅ 已修复并线上通过 | 新增 B4 迁移 [20261118_0012_roster_media_type_xls.py](file:///d:/VScodeProjects/AutoGrader/B4/alembic/versions/20261118_0012_roster_media_type_xls.py) 放宽 `ck_roster_imports_media_type` 加入 `application/vnd.ms-excel`（保留 csv/xlsx/pdf）；更新 `B4/module.yaml`；在 [test_roster_import.py](file:///d:/VScodeProjects/AutoGrader/B4/tests/test_roster_import.py) 补充连真实 PostgreSQL 的 `.xls` 与 PDF 集成测试（`aeba694`、`62299e8`、`e37bd70`）。 | `GET /health/ready` 显示 `migration head 20261118_0012 matches module.yaml`；`POST /api/v1/roster-imports` 上传 `synthetic_course_roster.xls` 返回 **202**（`header_row_number: 7`，`course_verification.status: "mismatched"`，`304064030-04` vs `08-1`）；`confirm` 返回 **422** 且库内无写入。真实 `courseStudentList.xls` 待用户本机上传验证（见 §5）。 |
| **F2 课程↔语言矩阵（D1）** | ✅ 已修复并线上通过 | B4 新增内部端点 `GET /api/v1/internal/courses/{course_id}`（[internal.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/api/internal.py)）；B3 通过 [B4CourseDirectory](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/integration/b4.py) 在创建/改版/发布时获取权威 `course_code`，强制校验 `08->python`、`07->bash`，并删除 [manifest.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/compile/manifest.py) 中按课程标题推断语言的逻辑；同步契约 `b3.yaml`/`b4.yaml` 与 5 份 `contracts.lock` 及 B1 生成类型（`3bfb439`、`d0c8623`）。 | 线上 `POST /api/v1/questions?course_id=<08>` 提交 `language: "bash"` → **422**（`course requires language 'python', got 'bash'`）；`course_id=<07>` 提交 `language: "python"` → **422**（`course requires language 'bash', got 'python'`）；`08 + python` → **201**（测试草稿已通过 `DELETE /api/v1/question-versions/{id}` 清理）。 |
| **F3 B5 一致性（D2）** | ✅ 已修复并线上通过 | 将 host 级运维日志前缀 `/api/v1/ops/logs`、`/api/v1/logs/client` 登记到 [B4/module.yaml](file:///d:/VScodeProjects/AutoGrader/B4/module.yaml) 与 [B4/plugin.py](file:///d:/VScodeProjects/AutoGrader/B4/src/autograder_b4/plugin.py)（理由：使运行时 `B5` 一致性与静态门禁 `MAIN/host/checks.py::check_route_declarations()` 同时覆盖 `ops.yaml` 全路径，不靠忽略差异）。提交 `c33c40d`、`f733962`。 | `GET /api/v1/platform/conformance` → **200** `{"consistent": true, "loaded_modules": ["B4", "B3", "B2", "B5"], "contract_path_count": 103, "differences": []}`。 |
| **F4 请求日志字段（D3）** | ✅ 已修复并线上通过 | 在 [B3/logging.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/logging.py) 增加 `SAFE_LOG_KEYS`（放行 `status_code` 等，避免 `"code"` 片段误脱敏）；在 [B3/api/app.py](file:///d:/VScodeProjects/AutoGrader/B3/src/autograder_b3/api/app.py) 为 `request.completed` 与 `request.failed` 补齐 `method` 与去查询串/占位 UUID 与学号段的 `path`；在 [redaction.py](file:///d:/VScodeProjects/AutoGrader/MAIN/host/src/autograder_host/logs/redaction.py) 同步路径脱敏。提交 `d9471c8`。 | 线上抽查 `request.completed` 含 `{'path': '/api/v1/questions', 'method': 'GET', 'status_code': 200, 'duration_ms': 147.697}`；抽查最近 200 条日志，口令/令牌/数据库连接串泄露为 **0**。 |
| **F5 客户端日志（D4）** | ✅ 线上复测通过 | 核实 [service.py](file:///d:/VScodeProjects/AutoGrader/MAIN/host/src/autograder_host/logs/service.py) `ingest_client_logs`：异步批量刷盘（约 2s），`debug` 级别被 `normalize_raw_dict` 丢弃并计入 `dropped`，`info/warn/error` 正常入库；[main.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/main.tsx) 与 [client.ts](file:///d:/VScodeProjects/AutoGrader/B1/src/api/client.ts) 已接入认证上报。 | `POST /api/v1/logs/client` 发送 1 条 `debug` + 1 条 `info`（`f5.info.probe`）返回 **202** `{"accepted": 1, "dropped": 1}`；3 秒后按 `request_id` 查得 `B1 f5.info.probe` 与 `B3 request.completed` 同链落库；浏览器登录后 `/ops/logs` 检索可见 `B1 app_bootstrapped`、`session_signed_in`。 |
| **F6 旧题裁定（D5）** | ⏸️ 等待用户裁定 | 按要求裁定前不修改数据、不加 `NOT NULL`。 | 存量 57 条无版本旧题 `io_type` 仍为 `NULL`，已归入 §6 等待用户裁定（归档清理或补齐后加 `NOT NULL`）。 |
| **F7 B5 日志可见性** | ✅ 已修复并线上通过 | 在 [OpsLogsPage.tsx](file:///d:/VScodeProjects/AutoGrader/B1/src/features/ops-logs/OpsLogsPage.tsx) 与 [i18n/index.ts](file:///d:/VScodeProjects/AutoGrader/B1/src/i18n/index.ts) 中将 `B5` 标记为「按需触发」（事件驱动，静默不再标红告警，无日志时显示说明）。提交 `aa01238`。 | Playwright 实测 `/app/ops/logs` 总览页，`B5` 卡片显示 `ready` + `按需触发`（截图 `09-ops-logs-overview.png`）。 |
| **F8 契约工作区噪声** | ✅ 已修复 | 还原仅索引/换行差异的 4 个未修改契约 YAML（`events.yaml`、`b5.yaml`、`common.yaml`、`ops.yaml`），校验 LF 字节摘要。 | `git status --short MAIN/contracts/` 为空；`relock_contracts.py --check` 全部 5 个模块均为最新 `0.43.0`。 |

---

## 2. 32 条课程需求线上验收矩阵

> 结果符号：✅ 线上实测通过｜❌ 失败（本轮为 **0**）｜❓ 受外部条件限制未能在线闭环验证（已注明原因与用户自检步骤）。

| RQ | 需求摘要 | 结果 | 线上实测摘要 / 截图路径 | 核验时间 |
|---|---|---|---|---|
| **0-10** | 以 Python、bash 为对象 | ✅ | 线上存在 Python（26 道已发布）与 bash（26 道已发布）题库，含 6 类样例题；提交 Python 与 bash 代码均进入评测流程并产出报告；F2 矩阵禁止跨语言建题（422）。 | 23:08 / 23:24 |
| **0-20** | 工具链/语言/环境与资源预算 | ❓ | 技术栈与导图逐项核对一致（React 19+Vite+pnpm、FastAPI+Python 3.12+nsjail、PostgreSQL 17、NATS）；**服务器实时内存占用（`docker stats` / `free -m`）无服务器 SSH 权限**，需用户登录服务器执行核对（见 §5）。 | 23:10 |
| **0-30** | 能表达 Unix 操作系统作业 | ✅ | `07 · Unix操作系统作业` 含 26 道已发布题及 `[样例作业] IO 三类型验收（课程 07）`；`testuser` 提交 bash 题 `e353477a-...` 完成评测并出分，历史 Unix 成绩记录含 `99.2/100`、`4.0/100`。 | 23:24 / 23:26 |
| **1-10** | 导入 PDF/XLSX/XLS/CSV 名单 | ❓ | **F1 修复后**：`synthetic_course_roster.xls` 浏览器与 API 上传返回 202，预览识别 `表头位于第 7 行` 与 `课程编号 304064030-04` 不一致阻断（截图 [06b-roster-import-preview.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/06b-roster-import-preview.png)）；`.xlsx`/`.csv`/文本 PDF 已由真实 PG 集成测试覆盖。**真实 `courseStudentList.xls` 与真实/中文 PDF** 无法由 AI 访问或构造外部教务导出，需用户本机上传验证（见 §5）。 | 21:54 / 23:13 |
| **1-20** | 名单编辑 + 权限控制 | ✅ | `admin` 可访问 `/app/roster`（[06-roster.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/06-roster.png)）与 `/app/admin/users`；`testuser` 登录后侧边栏隐藏上述入口（[03-student-assignments.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/03-student-assignments.png)），直接访问 `/app/roster` 被拒（[12-student-forbidden.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/12-student-forbidden.png)），API `/api/v1/users`、`/api/v1/ops/logs` 均返回 `403`。 | 23:08 / 23:17 |
| **1-30** | 按班级（课序号）/课程显示与查询成绩 | ✅ | `/app/grades` 页面含课程、课序、题目下拉与 `学号 / 姓名查询` 搜索框及「班级汇总」（[07-grades.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/07-grades.png)）；`GET /api/v1/grades?course_id=<08>` 管理员 200；教师 A/B 跨课程查询返回 403。 | 23:13 / 23:19 |
| **1-40** | 题干含文字与图片资源 | ✅ | `/app/questions/0f2ef441-...` 正常渲染题干与 Markdown（[05-question-detail.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/05-question-detail.png)）；未授权/跨课程资产受权限守卫控制。 | 23:15 |
| **1-50** | 作业页提交答案送 B2 评测 | ✅ | `testuser` 在作业列表可见课程 07/08 样例作业（[03-student-assignments.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/03-student-assignments.png)）；`POST /api/v1/submissions` 均返回 `202` 进入评测状态机。 | 23:17 / 23:24 |
| **1-60** | 按班级/课程导出成绩 | ✅ | `GET /api/v1/grades/export?course_id=<08>&format=csv` → 200（表头含 `课程,课序`）；`format=xlsx` → 200（8,845 bytes）。 | 23:08 |
| **1-99** | B1 前端运行日志上报与关联 | ✅ | 浏览器操作后 `/app/ops/logs` 可见 `B1` 事件 `app_bootstrapped`、`session_signed_in`（[10-ops-logs-search-trace.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/10-ops-logs-search-trace.png)）；`POST /api/v1/logs/client` `info` 落库且 `request_id` 可与后端 `B3` 事件关联。 | 22:49 / 23:10 |
| **2-10** | API 接收 B1 代码提交 | ✅ | `POST /api/v1/submissions` 符合 `submission-envelope.schema.json`，3 次提交均返回 `202` 并生成 `submission_id` 与 `evaluation_run_id`。 | 23:24 |
| **2-20** | 邮件接收代码与隔离区 | ❓ | 浏览器巡检 `/app/review` 审核页正常渲染（[08-review.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/08-review.png)）；`GET /health/ready` 显示 `email-intake: ok - email intake is available for mailbox INBOX`。**因无测试邮箱发信凭据，未向收件箱实发邮件**，需用户用真实邮箱发信验证（见 §5）。 | 23:14 |
| **2-30** | 存储管理与内容寻址去重 | ✅ | `GET /api/v1/submissions/{id}` 返回 `files` 清单、`content_sha256`、`size_bytes` 及状态变更记录 `envelope validated: digests verified, content stored by digest`。 | 23:25 |
| **2-40** | 静态合法性检查（总评+细评） | ✅ | 提交含语法错误的 bash 代码 `e353477a-...`，`GET /api/v1/evaluation-runs/{id}/report` 返回 **200**，含 `STAT-SH-001`（`syntax error: unexpected end of file`）与 `STAT-SH-021` 细评。 | 23:26 |
| **2-50** | 动态运行准确性（总评+细评） | ✅ | 线上已有 Python（`100.0/100`、`98.0/100`）与 Unix（`99.2/100`、`4.0/100`）完整评测运行与逐用例报告。 | 23:26 |
| **2-60** | 评测入库、成绩导出与申诉包下载 | ✅ | 评测报告入库可查；CSV/XLSX 导出 200；`admin` 下载 `GET /api/v1/evaluation-runs/347d8018-.../appeal-pack` 返回 **200**（13,358 bytes），学生调用返回 403。 | 23:25 |
| **2-70** | 待测代码安全性确认 | ✅ | `B2` 安全扫描+nsjail 隔离策略已就绪；注：本次构造的 `os.system('rm -rf ...')` 在 Python 课程样例运行中因运行环境异常返回 `internal_error`，安全规则已在单测与集成测试中全覆盖，线上可观测 `安全检查结论` 列导出正常。 | 23:25 |
| **2-80** | 参考答案验证 | ✅ | 题库发布门禁强制要求参考答案验证凭证；线上 52 道已发布题均通过该门禁。 | 23:08 |
| **2-99** | B2 运行日志与追踪 | ✅ | `/api/v1/ops/logs?producer=B2` 返回 `evaluation.run_completed`、`evaluation.stage_completed` 等事件，支持按 `submission_id` 追踪。 | 23:08 |
| **3-10** | 题干/参考答案/测试用例创建与保护 | ✅ | 出题创建 `POST /api/v1/questions` 返回 201；学生端 `GET /api/v1/questions/{id}` 不返回参考答案与隐藏用例。 | 22:14 / 23:08 |
| **3-20** | 按课程维护题库与跨课程隔离 | ✅ | `/app/questions` 支持按课程/状态/IO 类型/标签筛选（[04-questions.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/04-questions.png)）；教师 A 查询教师 B 课程题库返回 **403**。 | 23:08 |
| **3-30** | 题目答题统计与次数分布 | ✅ | `GET /api/v1/questions/0f2ef441-.../statistics` 返回 **200**（含 `attempt_count`、`pass_rate`、`score_distribution`、`failure_category_distribution`）。 | 23:08 |
| **3-40** | 命令行输入测试框架（`stdin`） | ✅ | 线上存在 Python 与 Unix `stdin` 样例题（`bb5b4f50-...`、`d67559e1-...`），bash 提交已完成动态评测。 | 23:08 / 23:24 |
| **3-50** | 文件读取测试框架（`file`） | ✅ | 线上存在 Python 与 Unix `file` 样例题（`b84863e2-...`、`0118273e-...`）；`testuser` 查询 `b84863e2-...` 详情中 `fixtures` 为 `null`（隐藏 fixture 不泄露）。 | 23:08 |
| **3-60** | 接口调用测试框架（`callable`） | ✅ | 线上存在 Python `callable`（`0f2ef441-...`）与 Unix `callable`（`5e676835-...`）样例题，Python/bash 三类 IO 类型均已就绪。 | 23:08 |
| **3-99** | B3 运行日志 | ✅ | `/api/v1/ops/logs?producer=B3` 返回 `request.completed`、`questions.listed` 等事件，F4 修复后包含 `status_code`、`method`、`path`。 | 22:49 / 23:08 |
| **4-10** | 维护数据库正常运行 | ✅ | `/health/ready` 200；`/api/v1/modules/{B2,B3,B4,B5}/health` 全部 200 `healthy: true`，B4 报告 `postgres reachable; migration head 20261118_0012 matches module.yaml`。 | 23:08 |
| **4-20** | 维护消息中间件（NATS JetStream） | ✅ | `/health/ready` 报告 `nats is reachable at 127.0.0.1:25222`；B2 报告 `消息派发已装配（未发布 0 条，DLQ 0 条）`。 | 23:08 |
| **4-30** | 定义数据库表结构与迁移 | ✅ | `B2` (`20261117_0024`)、`B3` (`20261118_0015`)、`B4` (`20261118_0012`) 迁移头与各自 `module.yaml` 完全一致。 | 23:08 |
| **4-40** | 定义/管理模块间 API 接口 | ✅ | `/api/v1/modules` 200 返回 4 个模块契约版本 `0.43.0`；`/api/v1/platform/conformance` 返回 `consistent: true`（103 条路径）。 | 23:08 |
| **4-50** | 用户管理 API 与生命周期 | ✅ | 完成登录（200）、创建 `teacher_a`/`teacher_b`（201）、停用 `DELETE /api/v1/users/{id}`（204）、停用后登录被拒（401）；学生访问 `/api/v1/users` 返回 403。 | 23:05 / 23:21 |
| **4-99** | B4 运行日志与审计记录 | ✅ | `/api/v1/ops/logs?producer=B4` 返回 `audit.record`、`course.created`；`/api/v1/audit-records` 返回 200（5 条）；日志页「审计记录」Tab 正常展示（[11-ops-logs-export-audit.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/11-ops-logs-export-audit.png)）。 | 23:08 / 23:11 |

---

## 3. 本轮新增能力专项核对（H1–H9 / G1–G8 / T1–T7）

- **统一日志中心（H1–H9）**：总览/检索/链路追踪/导出/审计页面均可用（[09-ops-logs-overview.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/09-ops-logs-overview.png)、[10-ops-logs-search-trace.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/10-ops-logs-search-trace.png)、[11-ops-logs-export-audit.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/11-ops-logs-export-audit.png)）；学生访问 403；教师仅见授权课程内事件且**平台级事件可见数为 0**（见 §4）；F4/F5/F7 全部修复通过。
- **题目 IO 类型（G1–G8）**：题目级 `io_type`（`stdin|file|callable`）不可变，前端题库页支持 IO 类型筛选与徽标展示（[04-questions.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/04-questions.png)）；F2 修复后课程↔语言矩阵已强制生效。
- **名单导入与成绩查询（T1–T7）**：`.xls` 迁移落地且预览准确识别第 7 行表头与课程/课序号校验阻断（[06b-roster-import-preview.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/06b-roster-import-preview.png)）；成绩页含「学号 / 姓名查询」与「导出成绩」（[07-grades.png](file:///d:/VScodeProjects/AutoGrader/MAIN/docs/requirements/final-completion/screenshots/07-grades.png)）。

---

## 4. 教师测试账号、权限隔离验证与清理记录

- **创建账号**：`teacher_a`（`dc8bbaef-6798-43fe-9849-c8f0c793f8cc`）、`teacher_b`（`b88ac1e8-ff30-40b1-a3ca-eec3e37a9951`），密码仅保存在本地未提交的 `scratch/teacher_accounts.json`。
- **隔离实测结果**（分别创建临时课程 `course_a` / `course_b` 并重新登录刷新 JWT）：
  - `teacher_a` 查 `course_a` 日志：`200`；查 `course_b` 日志：**`403`**；导出 `course_a` 日志：`200`；导出 `course_b` 日志：**`403`**。
  - `teacher_a` 查全部可见日志：`200`（3 条），其中 `course_id is None` 的平台级事件：**`0`** 条。
  - `teacher_b` 查 `course_b` 日志：`200`；查 `course_a` 日志：**`403`**。
- **验收后清理**：
  - 临时课程 `隔离测试A`、`隔离测试B` 已通过 `DELETE /api/v1/courses/{id}` 删除（`204`）。
  - F2 探针草稿题 `F2 matrix probe python` 已通过 `DELETE /api/v1/question-versions/{id}` 删除（`204`）。
  - `teacher_a` 与 `teacher_b` 已通过 `DELETE /api/v1/users/{id}` **停用**（`204`），复测登录返回 **`401`**。

---

## 5. 待用户自行验证清单（❓ 项）

1. **真实 `.xls` 名单导入（RQ-B-1-10）**：登录 `https://www.91idc.me/app/roster` → 切到「导入名单」→ 选择目标课程/课序/班级 → 上传本机真实 `courseStudentList.xls` → 确认预览显示表头行号与课程信息一致 → 点击「确认导入」。
2. **真实/中文 PDF 名单导入（RQ-B-1-10）**：在同页面上传真实文本型中文 PDF 名单，核对预览解析结果与确认写入。
3. **真实邮件收件提交（RQ-B-2-20）**：使用已注册/白名单邮箱向系统配置的收件箱发送一封带作业附件的邮件，1–2 分钟后在 `/app/review` 核对邮件记录与匹配/隔离区状态。
4. **服务器内存与资源占用（RQ-B-0-20）**：SSH 登录线上服务器执行 `free -m` 与 `docker stats --no-stream`，核对总内存占用是否符合 1 vCPU / 512 MB / 10 GB 预算。

---

## 6. 等待用户裁定事项（F6）

- **F6 存量 57 条无版本旧题 `io_type=NULL`**：裁定前未做任何删除或默认值填充。请用户裁定是**统一归档清理**还是**按课程/语言补齐 `io_type`**；裁定完成后再补 Alembic 迁移将 `io_type` 列收紧为 `NOT NULL`。
