# Phase 05 隔离区复核与题目质量出口（`CHG-0014` / `CHG-0015`）

- **来源**：`MAIN/docs/contracts/changes/CHG-0014-quarantine-and-appeal-pack.md`（0.9.0 生效）与
  `CHG-0015-quality-heatmap.md`（0.10.0 生效）；`MAIN/docs/phases/active/phase-01-integration-runtime.md`
  §12.14 把两侧（B1 消费、B2 实现）**并行**派发，§12.15 第 5 项记"已排入 B2 队列（本轮），**未执行**"。
- **授权范围**：本工作项获授权改 `alembic/**` 与 `module.yaml` 的 `database.migration_head`
  （其余批次禁用这两处）。**未动** `MAIN/**`（契约、四份锁）、`B2/contracts.lock`、`B2/pyproject.toml`、
  `descriptor.contract_version`（MAIN 正在推进契约版本变更）、`descriptor.routes`（与 `module.yaml` 一致，
  本批无需改）。
- **契约权威**：`MAIN/contracts/openapi/b2.yaml` 的 `listIntakeQuarantines` /
  `resolveIntakeQuarantine` / `getAppealPack` / `getEvaluationQualitySummary` 与
  `IntakeQuarantineView` / `AppealPack` / `EvaluationQualitySummary`。本批**未改契约一行**。

## 改了什么

| 文件 | 变更 |
|---|---|
| `src/autograder_b2/intake/quarantine.py`（新增） | 隔离区读路径与人工归属的服务层：字段映射、`resolved`/`reason_code` 过滤、倒序 keyset 游标、归属的三道判定（角色/作用域、内容类闸门、请求体形态） |
| `src/autograder_b2/auth/authorization.py` | 新增 `require_intake_quarantine_authority`（教师/助教/管理员 + 令牌必须携带课程作用域）与 `require_question_quality_authority`（教师/助教/管理员） |
| `src/autograder_b2/intake/api.py` | 两条新路由（同一 intake 路由族）：查询参数与请求体**手工解析**，错误体一律 Problem Details |
| `src/autograder_b2/intake/runtime.py` | 装配 `IntakeQuarantineService`；`ready` 纳入它（只依赖库与认证器，**不**依赖邮箱是否配置） |
| `src/autograder_b2/evaluation/quality.py`（新增） | 聚合服务层：三态安全结论、逐用例失败类别/失败率、逐规则命中（含零命中规则）、`unmeasured_runs`（JSONPath 判据） |
| `src/autograder_b2/evaluation/appeal.py`（新增） | 证据包组装：五项绑定摘要的逐字段映射、视图裁剪（复用 `report.for_student`）、宿主路径脱敏、重现说明、组装后自检 |
| `src/autograder_b2/api/evaluation_routes.py` | 两条新路由；`quality-summary` **声明在 `{evaluation_run_id}` 之前**（契约 §3 的路由顺序要求） |
| `src/autograder_b2/runtime.py` | 装配 `EvaluationQualityService`（与成绩同口径：只依赖库与认证器）；把评测链的工作区根记进运行时（证据包按配置剔除宿主路径时要用它） |
| `alembic/versions/20260915_0013_b2_intake_quarantine_resolution.py`（新增） | 人工归属三列 + 两条 CHECK；纯加列、可逆 |
| `src/autograder_b2/db/schema.py` | 表映像补三列（列顺序与 DDL 追加顺序一致）；`EXPECTED_SCHEMA_REVISION` → `20260915_0013` |
| `module.yaml` | `database.migration_head` → `20260915_0013`（**只改这一行**） |
| `tests/test_intake_api.py`、`tests/test_b2_routes.py` | 见"验证"一节的用例清单；`_PENDING_CONTRACT_PATHS` 归零 |
| 文档 | 本文件 + `docs/api/contract.md`（1c/1d/1e 节）、`docs/testing/cases.md`（新一节）、`docs/database/{schema,migrations}.md`、`docs/requirements/traceability.md`、`docs/README.md`、`docs/runbooks/local-development.md`（SDK 重装一条） |

## 三处不变量（本批的实现要点，逐条对应契约的"不可协商"措辞）

1. **人工归属不触发评测**：`resolve()` 的全部写入只有一条 `UPDATE b2.intake_quarantines`；用例以
   "提交 / outbox / 评测运行三张表计数为零"把它钉住（不是靠注释）。
2. **证据包两处强制脱敏是主动做的**：宿主根（工件根/工作区根）逐字前缀剔除 + 绝对路径形态
   （Windows 盘符 / POSIX 根路径）正则剔除，沙箱内路径（`/workspace/...`）例外；`view=student` 走
   **报告端点的同一个** `report.for_student`。组装后自检：包内报告仍须过 `evaluation-report` schema。
   脱敏返回新对象，库里那份报告逐字节不变。
3. **质量汇总只有计数**：`QualitySummary` 里除了 `question_version_id` 没有任何可承载个人标识的字段；
   用例另外做了键名扫描与**取值扫描**（响应里除题目版本外不得出现任何 UUID）。

## 验证（真实执行）

### 1. 迁移三步可逆（**只在 B2 测试实例 55433 上执行**，未触碰 55432）

```powershell
cd B2
$env:DATABASE_URL = "postgresql+psycopg://autograder_platform:***@127.0.0.1:55433/autograder"
uv run alembic current      # 起始读数
uv run alembic downgrade -1 # 回滚 0013
uv run alembic current
uv run alembic upgrade head # 再升回来
uv run alembic current
uv run alembic heads
```

原始读数（每一步都配一个只读结构探针；Alembic 的 `upgrade`/`downgrade` 在本仓库配置下不打印迁移行，
因此用 `current` 与库内读数作为凭据）：

```text
### step 0: probe before
revision: 20260915_0013
resolution columns present: ['resolved', 'resolved_student_id', 'resolution_note']
resolution constraints present: ['ck_intake_quarantines_resolution', 'ck_intake_quarantines_resolution_note']
### step 1: downgrade -1
20260915_0012
revision: 20260915_0012
resolution columns present: []
resolution constraints present: []
### step 2: upgrade head
20260915_0013 (head)
revision: 20260915_0013
resolution columns present: ['resolved', 'resolved_student_id', 'resolution_note']
resolution constraints present: ['ck_intake_quarantines_resolution', 'ck_intake_quarantines_resolution_note']
```

即：**可逆**不是"有一个 `downgrade()` 函数"的声明，而是实测的两种结构状态（列与约束在 0012 上真的没有了）。
head 上的列/约束定义（同一实例，只读）：

```text
  col: ('resolved', 'boolean', 'NO', 'false')
  col: ('resolved_student_id', 'uuid', 'YES', None)
  col: ('resolution_note', 'text', 'YES', None)
  con: ('ck_intake_quarantines_resolution', 'CHECK ((resolved = (resolved_student_id IS NOT NULL)))')
  con: ('ck_intake_quarantines_resolution_note', 'CHECK (((resolution_note IS NULL) OR ((char_length(resolution_note) >= 1) AND (char_length(resolution_note) <= 1000))))')
  con: ('pk_intake_quarantines', 'PRIMARY KEY (intake_quarantine_id)')
```

**离线模式（`--sql`）也走通**（迁移链的全部 DDL 文本可生成，本批的 0013 在其中）：

```text
$ uv run alembic upgrade head --sql   # -> exit 0，1117 行
ALTER TABLE b2.intake_quarantines ADD COLUMN resolved BOOLEAN DEFAULT false NOT NULL;
ALTER TABLE b2.intake_quarantines ADD COLUMN resolved_student_id UUID;
ALTER TABLE b2.intake_quarantines ADD COLUMN resolution_note TEXT;
ALTER TABLE b2.intake_quarantines ADD CONSTRAINT ck_intake_quarantines_resolution CHECK (resolved = (resolved_student_id IS NOT NULL));
ALTER TABLE b2.intake_quarantines ADD CONSTRAINT ck_intake_quarantines_resolution_note CHECK (resolution_note IS NULL OR char_length(resolution_note) BETWEEN 1 AND 1000);
```

（同一路径也由 `tests/test_migrations.py::test_offline_sql_mode_also_creates_schema_first` 覆盖。）

### 2. `ruff check .`（原始输出）

```text
All checks passed!
```

### 3. `pytest`（原始 summary 行）

```powershell
$env:B2_TEST_DATABASE_URL = "postgresql+psycopg://autograder_platform:***@localhost:55433/autograder"
$env:B2_TEST_NATS_URL = "nats://localhost:54223"
uv run pytest
```

三次全量运行的原始 summary 行（同一天、同一环境、同一树；后两次之间只多了 1 条新用例）：

```text
第一次：  4 failed, 1213 passed, 9 skipped, 2 xfailed, 2 warnings in 378.52s (0:06:18)
第二次：  3 failed, 1214 passed, 9 skipped, 2 xfailed, 2 warnings in 355.26s (0:05:55)
最终次：  4 failed, 1214 passed, 9 skipped, 2 xfailed, 2 warnings in 359.29s (0:05:59)
```

失败集合的差异**不是不稳定的代码**，而是下面第 1 条里那对**flaky** 的成绩分页用例在随机 UUID 下
时通时不通（实测 6 次：`2 passed` / `1 failed, 1 passed` / `1 failed, 1 passed` / `1 failed, 1 passed` /
`1 failed, 1 passed` / `2 failed`）。本批新增的 **25** 条用例在三次运行中**全部通过**。

**那 3～4 条失败与本批无关，且已用"还原实验"证明**（见下"未执行 / 未验收"第 1 条）。

### 4. 新增/更新的用例

- `tests/test_intake_api.py`（新增 **11** 条）：T-QUAR-001..011（列表字段与写入侧一一对应、两个过滤、
  倒序游标分页、鉴权矩阵、归属不触发评测、内容类拒绝不可归属、重复归属 409、请求体/标识校验、
  未迁移库 503、**已知课程作用域缺口**的记录用例、契约长度上限）。
- `tests/test_b2_routes.py`（新增 **14** 条 + 更新 1 条）：B2-QS-01..06、B2-APL-01..06、B2-NEW-01；
  `test_registered_paths_match_the_contract` 的 `_PENDING_CONTRACT_PATHS` 归零（差集断言保留）。
- 逐条对照见 [../../testing/cases.md](../../testing/cases.md) 的"隔离区复核与质量出口"一节。
- 收集总数（本树）：**1229** 条 = 1214 passed + 4 failed + 9 skipped + 2 xfailed。

## 未执行 / 未验收（**不写成通过**）

1. **3～4 条既有失败与本批无关（已证明）**：把新迁移文件暂时移出链、并把 `EXPECTED_SCHEMA_REVISION`
   还原成 `20260915_0012`（即本批之前的库结构状态）后，同样这几条**照着失败**：

   ```text
   4 failed in 6.50s
   ```

   根因（三条，全部落在**迁移 0012 的毫秒四舍五入**与一条环境解析）：

   - `test_grades_query_export.py::test_pagination_is_keyset_not_offset` 与
     `::test_cursor_pagination_is_complete_and_disjoint`：`_seed_pages` 故意让前三行的微秒不同
     （`0/120/350` µs）以证明游标保留微秒，而 `timestamptz(3)` 会把它们舍成**同一毫秒**，排序退化为
     `submission_id`；用例却按"种子顺序"断言，于是**随机 UUID 决定成败**。这一对因此是 flaky 的
     （实测 6 次：`2 passed`、`1 failed, 1 passed`×4、`2 failed`）——这也解释了两次全量运行里
     失败条数 4 与 3 的差异。
   - `test_grades_query_export.py::test_xlsx_timestamp_truncates_like_the_api_not_rounds`：期望
     `.123999 → .123`（截断），而库列四舍五入到 `.124`（确定性失败）。
   - `test_migrations.py::test_every_timestamp_column_has_millisecond_precision`：
     `ModuleNotFoundError: No module named 'alembic.versions'`——本仓库的 `alembic/` 目录没有
     `__init__.py`，因此 `import alembic` 解析到 site-packages 里的 Alembic
     （实测 `alembic.__file__` 即 site-packages；确定性失败）。

   **本批没有顺手修改这 4 条**（属别的批次/别的缺陷）：它们与 `CHG-0014`/`CHG-0015` 无关，
   而"修 flaky 的判据"会改动成绩分页用例的期望——那是另一个决定。

   > **后续（已解决）**：这 4 条已由 MAIN 指定的整改批次修掉，逐条根因/归属/改法/前后读数见
   > [phase-08-known-failure-remediation.md](phase-08-known-failure-remediation.md)；本树最新读数为
   > **`1298 passed, 9 skipped, 2 xfailed`（收集 1309，退出码 0）**。
   > **上面这一段的措辞需按 phase-08 修正一处**：那一对并非"**缺** tie-breaker/排序退化"——
   > `grades/query.py` 的 `_statement` 一直是 `ORDER BY (created_at ASC, submission_id ASC)`，
   > `submission_id` **就是**声明的 tie-breaker；真实缺陷是用例按"种子顺序"断言，而种子顺序**不是**
   > 声明顺序（12 次连续运行的确定性实测见 phase-08，`12 passed / 0 failed`）。
2. **父任务引用的基线读数（`1145 passed, 9 skipped, 1 xfailed`）在本树复现不出来**：本树本轮收集
   **1228** 条（其中本批新增 24 条）。差额 49 条与 xfail 由 1 变 2 都**不是本批造成的**（本批只新增
   24 条、未删改任何既有用例的失败/跳过标记）；B2 仓库没有 git 历史，无法在本机做逐文件对照。
3. **真实沙箱下的资源读数**：`unmeasured_runs` 的"已测量"分支用**合成**读数写进已落库报告来验证
   （T-REQ：本机 Windows 上 nsjail 一律不可执行，真实读数只能在 Linux 验收里产生）。
4. **真实 IMAP 重取邮件**：`resolve` **不做**这件事（见下"契约缺口"①），因此本批没有任何"连真实
   邮箱"的验证。
5. **B1 侧消费**：四个端点的界面属 B1；本批只证明 B2 的响应形状与语义，未做浏览器端到端。

## 契约缺口（已上报，**B2 未自行裁决**；完整表见 `docs/api/contract.md` 第 1e 节）

1. **`resolveIntakeQuarantine` 的"重跑与自动路径相同的信封/摘要校验"在当前数据模型下不可判定**：
   `b2.intake_quarantines` 不存报文与附件字节、不存任何摘要（平台有意不做第二个邮件仓库），
   因此 resolve 阶段**没有可重算的摘要**。唯一能真正做到的方式是让 resolve 回邮箱重取报文，
   而契约给它的响应集合（401/403/404/409/422）**没有 503**——反证它不依赖外部邮箱。
   **本实现只重跑当前数据下能重跑的校验**：角色/作用域 + 内容类闸门（可归属原因码全集 =
   `not_uniquely_matched` / `ambiguous_match`；其余 17 个原因码一律 422）。
2. **"归属后交给正常提交路径评测"无法闭环**：构造提交信封还需要 `course_id`/`section_id`/
   `question_version_id`，而自动路径正是从匹配规则取这三项；`not_matched` 时规则表没有给出任何身份。
   只给 `student_id` 不足以重建信封，且该 UID 的 checkpoint 已按 `quarantined` 落库、下次拉取会跳过。
   **本模块没有发明"归属即建提交"的路径**（那会绕开自动路径的身份判定）。
3. **课程作用域**（**后续：隔离区部分已由 P13 解决**）：两条隔离区端点与质量汇总当时都无法按行/
   按题目判定课程——`intake_quarantines` 没有 `course_id`（匹配失败时也不存在课程），"题目→课程"
   是 B3 的事实（B2 不读 B3 schema）。实现只判到"staff 角色（隔离区额外要求令牌携带课程作用域）"；
   另一门课的教师当时能读到本部署的隔离记录。**P13 已给隔离行补上可持久化的
   `scope_course_id/scope_section_id`（迁移 `0015`）并把授权做到行级**，见
   [phase-10-quarantine-row-scope.md](phase-10-quarantine-row-scope.md)；质量聚合仍只判角色
   （缺口保留）。本条记录的旧用例
   `test_quarantine_scope_gap_is_recorded_not_hidden` 已被
   `test_quarantine_list_is_filtered_by_row_scope_before_pagination` 等取代。
4. 另有两条较小者（`resolved` 缺省语义的含糊、若干未声明但被使用的状态码）同见该表。

## 环境事实（写给下一个人）

- **`uv run` 不会因为路径依赖的版本号没变而重装 SDK**：本树 `B2/.venv` 里曾留着 2026-09-15 的
  `autograder_platform`（缺 MAIN 新增的 `contracts.py`），导致 `pytest` **收集期**就报
  `ModuleNotFoundError: No module named 'autograder_platform.contracts'`，任何测试都跑不起来。
  只跑 `uv sync` 不够（它认为 `0.1.0` 已满足），必须显式
  `uv sync --all-groups --reinstall-package autograder-platform`。因此**任何基线数字都必须注明
  SDK 是否已刷新**，否则下一个人会得到一个不存在的失败。
- 本机 `B2/.pytest-tmp/` 下有 `pytest-of-root` 留下来的**符号链接**（Linux 容器验收的产物），
  Windows 上 `stat` 会抛 `WinError 1920`；扫描工作区的脚本必须先判目录再 `is_file()`（并捕获 `OSError`）。
