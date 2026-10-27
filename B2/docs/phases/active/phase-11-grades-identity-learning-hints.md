# Phase 11 — 成绩身份关联、时间范围导出与学生分层提示（成员四，CHG-0030）

- **状态**：代码已完成；**真实 PostgreSQL 迁移/集成、真实 B4/B3 联调与 provider 调用未执行**
- **契约**：`MAIN/contracts` 从 `0.22.0` 升到 `0.23.0`；`B2/module.yaml` 与 descriptor 同步；迁移头 `20260915_0016`

## 1. 成绩读/导出

- `GET /api/v1/grades`：新增 `from` / `to`（RFC 3339，含两端，作用在提交时刻）与
  `evaluation_run_id`（按运行反查提交；授权作用域独立生效）。`from > to` → 422。
- `GradeRow.security_status`：三态安全结论（可空）；不参与平均分/通过率/排序。
- `GET /api/v1/grades/export`：`from`/`to`/`evaluation_run_id`/`columns`；`columns` 只允许**增选**
  可选列，必需列始终存在，未知字段 422（不静默忽略）。
- **身份关联**（`grades.identity`）：B2 转交调用者自己的 bearer token 请求 B4 的
  `GET /sections/{section_id}/enrollments`，按 `student_id` 合并姓名/学号。B2 **不读 b4 schema、
  不存身份副本**。三种失败都拒绝生成文件：
  - 未配置 `B2_B4_BASE_URL` → `IdentityNotConfigured`（503）；
  - HTTP/解析失败 → `IdentityLookupError`（503）；
  - 名单缺行/未绑定账号 → `IdentityMissingForRows`（409）。
- 空结果不需要身份，仍可导出只含表头的文件（该课还没有提交不是依赖故障）。

## 2. 学生分层学习提示（AI-FLOW-01B）

- 新表 `b2.evaluation_learning_hints`（迁移 `20260915_0016`）：一 run 一行、`student_id` 落库、
  1–3 条 `hint_steps`、provenance、`context_scope`、幂等键与脱敏失败类别。**没有**分数/证据/源码列。
- `GET/POST /api/v1/evaluation-runs/{id}/learning-hints`：
  - **仅 run owner 学生**；教师/助教/管理员 403（刻意不提供教师逐条查看学生提示的接口）。
  - 生成前置：`completed` 且有失败证据；未完成/被安全拒绝/基础设施失败 409；无失败证据
    `200 not_applicable`；预算 429；provider 未配置 503；传输失败 503（行标 failed）；
    输出结构非法/安全闸门拒绝 `200 failed`（不返回半截提示）。
  - `Idempotency-Key` 必填（缺失 422）；同一 run 的 `ready` 结果对任何 key 都返回同一行、不重复调用；
    `failed` 同 key 重放返回已有失败、不同 key 才重试；`pending` 未过期 409。
  - 输入白名单沿用 `commentary.scope.INPUT_SCOPE` + **可选** B3 公开上下文
    （`B2_LEARNING_CONTEXT_PATH` 配置后才读；只取题面/语言/标签/adapter kind，超长截断）。
  - 输出闸门是结构性的：完整程序形状（多行定义+函数体/输出）、分数断言、隐藏字段标记 → 整组拒绝。
  - 日志只记类别/状态，不记 prompt、响应原文或证据。

## 3. 实际执行的检查

```text
uv run ruff check src/autograder_b2 tests     # All checks passed
uv run pytest -q                              # 664 passed, 688 skipped（skip = 需要真实 PostgreSQL）
```

- 新增 `tests/test_learning_hints_policy.py`（24 个纯单元用例：协议/顺序/安全闸门/上下文白名单/
  配置/身份合并/导出列/时间解析/表形状/迁移头一致性）。
- 更新 `tests/test_grades_query_export.py`：导出身份改由测试名单替身提供；新增“缺身份拒绝导出”、
  “未配置目录拒绝导出”、“没有调用者令牌拒绝导出”、“`columns` 不能删除必需列”等用例。
- 更新 `tests/test_migrations.py`：新表加入业务表/主键/约束/索引/时间精度断言。
- 契约治理：`uv run python -m autograder_host.checks` **四条门禁全部 ok**（契约摘要 `fb4915e9…`、
  manifest/迁移头/文档与链接/测试证据）；五份 `contracts.lock` 摘要一致。

## 4. 未验证（不得写成已通过）

- **真实 PostgreSQL 上的 `alembic upgrade head` / `downgrade`**：本机无可用 PostgreSQL，684 个 DB 用例
  与迁移验收全部 skip。必须由有库环境重跑 `B2/tests/test_migrations.py` 与
  `tests/test_grades_query_export.py`。
- **真实 B4 名单接口**：`HttpEnrollmentDirectory` 只由单元测试传输替身覆盖；未对真实 B4 验证
  token 转交、分页、403/404 语义与 UTF-8 姓名。
- **真实 B3 learning-context**：operation 已在契约 `0.18.0` 提供（`QuestionLearningContext`）；
  B2 消费者默认使用该路径（`DEFAULT_LEARNING_CONTEXT_PATH`），但本环境未配置 B3 通道，
  因此真实联调仍未执行。
- **真实模型 provider**：未配置，`POST` 只验证到“未配置/失败”分支；生成质量、时延、预算与
  安全闸门对真实模型输出的召回率未测。
- **导出文件人工核对**：未打开真实 XLSX/CSV 核对 UTF-8 文件名、中文姓名与公式注入（渲染层测试已覆盖）。
- **Linux + nsjail 沙箱**：与本批无关，但评测链路整体仍未在 Linux 验收；不得以 Windows 裸进程代替。

## 5. 跨模块待办

- 成员三：`GET /question-versions/{id}/learning-context` 已落地；需在联调环境配置 B3 基址/服务凭据，
  核对 `context_scope` 与题面截断行为。
- 成员一/二：B4 课程/课序/题目的人类可读编号若要进入导出，需要新的授权读取路径；当前导出列
  使用 B2 拥有的 ID（身份字段除外）。
