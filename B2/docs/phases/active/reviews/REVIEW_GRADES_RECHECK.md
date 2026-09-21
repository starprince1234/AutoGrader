# 独立复核记录：B2「成绩查询与导出」（冷视角 / 对抗式）

- 复核者：独立复核 Agent（未参与实现；**只读实现**，全部探针写入 `.pytest-tmp/`）
- 复核时间：2026-09-16
- 判据：任务书盘档规格（A/B/C 三条验收 + 负面声明）+ `../MAIN/contracts/openapi/b2.yaml`、`../MAIN/contracts/schemas/evaluation-report.schema.json` + 仓库硬规则（`AGENTS.md`）
- 环境：Windows + PowerShell；真实 PostgreSQL 17 容器 `autograder-b2-postgres`（55433，**未启动/未停止任何容器**）；Python 3.12.3 / openpyxl 3.1.5 / SQLAlchemy 2.0.53 / FastAPI 0.141.1 / ruff 0.16.7

## 0. 一句话计划（复核前写下）

读权威判据定位不变式 → 自造数据集（自造 UUID/中文值/恶意载荷，不抄实现者）走真实 HTTP 拉全分页并逐字段比对 XLSX/CSV（A）→ 以"不转义确实生成公式"为反向对照，再断言真实导出 `<f>` 零命中、单元格为字符串、数值列未被文本化（B）→ 真实 HTTP 跑越权/未完成态/401/422 矩阵并扫描 403 响应体是否回显他人标识（C）→ 解包 XLSX 证伪"字节确定性"并用**故障注入**给既有测试称牙 → 实测偏差 ④⑤。

## 1. 我跑了什么（可复算）

统一前置（每个命令前设置）：

```powershell
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/autograder"
$env:PYTHONIOENCODING="utf-8"
```

| # | 命令 | 结果 |
|---|---|---|
| 1 | `.\.venv\Scripts\python.exe -m pytest tests/test_grades_query_export.py tests/test_b2_routes.py -q --no-header -p no:cacheprovider` | `104 passed, 2 warnings in 19.67s`（复算实现者声称的 104） |
| 2 | `.\.venv\Scripts\python.exe -m pytest -q --no-header -p no:cacheprovider`（**串行单独跑**，未与其他套件并行） | `1115 passed, 3 skipped, 1 xfailed, 25 warnings in 156.35s`（与声称一致） |
| 3 | `.\.venv\Scripts\python.exe -m ruff check .` | `All checks passed!`（ruff 0.16.7） |
| 4 | `.\.venv\Scripts\python.exe .pytest-tmp\recheck_grades.py` | `=== 196/196 checks not-FAIL; FAIL=0 ===`（日志 `.pytest-tmp/recheck_run.log`，结构化结果 `recheck_grades_result.json`） |
| 5 | `.\.venv\Scripts\python.exe .pytest-tmp\recheck_concurrency.py` | `=== 12/12 not-FAIL; FAIL=0 ===`（日志 `recheck_conc.log`） |
| 6 | 故障注入（`PYTHONPATH=.pytest-tmp`，`-p mut_plugin`，`RECHECK_MUT=<name>`） | 见 §5，6 个 mutation **全部**被既有测试抓到 |
| 7 | 真实 Excel（COM late binding，只读打开导出文件） | 见 §3-B；宿主机有 Excel，**LibreOffice 未安装**（`soffice` 不存在） |

我的探针文件（自写，均在 `.pytest-tmp/`，`git status` 里 `pytest-tmp` 出现 0 次 ⇒ 未污染仓库）：
`recheck_grades.py`、`recheck_concurrency.py`、`mut_plugin.py`、`make_excel_probe_files.py`、`attribute_leftovers.py`。

**没跑成 / 没做的**：

- LibreOffice 打开：环境无 `soffice`（`Test-Path "C:\Program Files\LibreOffice\program\soffice.exe"` → `False`）。Excel 侧做到了（§3-B）。
- 未做人工"看表"式检查（不截屏）；Excel 检查是 **COM 程序化读取**（`HasFormula`/`Value2`/`NumberFormat`），非人眼观感。
- 未做跨机/跨 zlib 版本的字节确定性（只做了同机跨进程）。
- 不改任何被测文件；**没有**比对"改写前后 diff"（`git log` 为空、无提交、无备份，`git status` 全部为 `??`，diff 客观不可得——实现者第 3 条自述在这一点上属实）。

## 2. 我自造的数据集（不复用实现者夹具）

自造 UUID；基准时刻 `2027-03-04T05:06:07.123456Z`（带微秒，验证毫秒截断）。C1 课程 10 行 + C2 课程 1 行：

| key | 课序 | 题目 | state（最新运行） | score | 设计意图 |
|---|---|---|---|---|---|
| sub1 | S1 | Q1 | completed | 86.5 | 正常完成；与 sub8 的 `created_at` **完全相同**（游标并列） |
| sub2 | S2 | Q1 | running | null | `.999900` 微秒行（截断 vs 进位） |
| sub3 | S1 | Q1 | queued | null | 未完成 |
| sub4 | S1 | Q1 | failed | null | 未完成（技术失败） |
| sub5 | S1 | Q1 | rejected | null | 未完成（拒收） |
| sub6 | S1 | Q1 | completed | **0** | **真实 0 分**（与"未完成"必须可区分） |
| sub7 | S1 | Q2 | validated（**无任何运行**） | null | state 回落到提交状态 |
| sub8 | S2 | Q2 | completed | 100 | 与 sub1 并列时刻 |
| sub9 | S1 | Q2 | running（seq2 盖过 seq1 的 50 分） | null | 重跑不得显示旧分 |
| sub10 | S1 | Q1 | completed（**结果行缺失**） | null | 不得臆造 0 |
| sub11 | S9(C2) | Q9 | completed | 70 | 跨课程隔离 |

受保护内容：把 `RECHECK_SECRET_SOURCE_MARKER` / `RECHECK_HIDDEN_EXPECTED_MARKER` / `RECHECK_REFERENCE_ANSWER_MARKER` 写进 `evaluation_results.report`(JSONB)、`static_findings.message`、`case_evidence.expected_output_excerpt`；另写 5 个令牌（教师C1/教师C2/只覆盖 S1 的助教/学生A/学生B/学生D/管理员/无角色）+ 一个"学生但作用域是整门课"的令牌。

## 3. 三条验收标准

### A. 导出与查询口径一致 → **PASS**

**做法**：8 个筛选场景各用 `limit=2` 逐页拉全 `GET /api/v1/grades`，再用同参数取 XLSX 与 CSV，三者按 `submission_id` 对齐全字段比较。

**归一规则（逐字段口径，明确写出）**：
- `submission_id`/`student_id`/`student_number`/`display_name`/`course_id`/`section_id`/`question_version_id`/`state`/`evaluation_run_id`：三者都取字符串**精确相等**；空值统一为 `None`（CSV 的 `""` 与 XLSX 的空单元格都归一为 `None`）。
- `score`/`max_score`：`Decimal(str(值))` 比较 ⇒ `86`（XLSX 读回 int）== `86.0`（API float）== `"86"`（CSV 文本）视为相同；空值 → `None`。
- `is_pass`：XLSX 原生 bool、API JSON bool、CSV `true`/`false` 解析为 bool 后比较。
- `submitted_at`：三者统一归一到毫秒 RFC 3339（XLSX 是**朴素 UTC** datetime ⇒ 补 `Z`）。

**结果**（`recheck_run.log`，逐场景 10 条断言 × 8 场景）：

```
PASS A.count[course-wide]   rows=10 pages=5 expected=10 trace=[(2,T),(2,T),(2,T),(2,T),(2,F)]
PASS A.disjoint[course-wide] unique=10
PASS A.rowcount[course-wide] api=10 xlsx=10 csv=10
PASS A.keyseq[course-wide]   10 keys
PASS A.fieldwise[course-wide] mismatches=[] (n=0)
PASS A.count[assistant S1]  rows=8 pages=4        # 只覆盖 S1 的助教看不到 S2 的 sub2/sub8
PASS A.fieldwise[section S1] mismatches=[] (n=0)  # section_id=S1 → 8 行
PASS A.fieldwise[section S2] mismatches=[] (n=0)  # section_id=S2 → 2 行
PASS A.fieldwise[question Q2] mismatches=[] (n=0) # question_version_id=Q2 → 3 行
PASS A.fieldwise[S1+Q2]      mismatches=[] (n=0)  # 二者同时收窄 → 2 行
PASS A.fieldwise[student A]  mismatches=[] (n=0)  # 学生自称 6 行
PASS A.fieldwise[course C2]  mismatches=[] (n=0)
PASS A.time.truncate  2027-03-04T05:06:08.999Z   # .999900 → .999（截断，不四舍五入进位）
PASS A.zero  score=0.0 is_pass=False              # 0 分渲染为 0
PASS A.null  score=None state='running'           # 未完成 score 为 null（不是 0）
```

**中文不乱码（字节级）**：

```
PASS A.header.csv[course-wide] first=b'\xef\xbb\xbf\xe6\x8f\x90\xe4\xba\xa4ID,\xe5\xad\xa6\xe7\x94\x9fID,...' bom=b'\xef\xbb\xbf'
PASS A.header.xlsx[course-wide] ['提交ID','学生ID','学号','姓名','课程ID','课序ID','题目版本ID','状态','得分','满分','是否通过','提交时间(UTC)','评测运行ID']
PASS A.header.bytes[course-wide]  # 以上 13 个表头的 UTF-8 字节全部出现在解压后的 xl/worksheets/sheet1.xml，且无 U+FFFD
PASS A.csv.crlf[course-wide] lf=11 crlf=11        # RFC 4180：无裸 LF
PASS A.csv.columns[course-wide] 13 cols x 10
```

**契约形状**（`GradeRow` 字段集与顺序**从 b2.yaml 读取**，不手抄）：

```
PASS A.contract.page_keys  ['items','next_cursor']
PASS A.contract.row_keys   contract=['submission_id',...,'evaluation_run_id']（13 字段，逐行键序相同）
PASS A.contract.export_columns export 列序 == 契约 properties
```

**链路闭合**（HTTP 字节 == 渲染器字节，我自己的断言，见 §6）：

```
PASS CHAIN.xlsx_bytes  http=5983B render=5983B
PASS CHAIN.csv_bytes   http=1704B render=1704B
PASS CHAIN.service_agrees service == renderer
```

**结论**：A **PASS**。附带如实说明：**HTTP 行的中文"值"今天结构上不可达**（见 §4-B 与 §7-U1），所以"中文值不乱码"是在**渲染层 + 真实 Excel**上证到的，不是在 HTTP 行值上证到的——这与实现者自述一致，不构成缺陷。

### B. 公式注入 → **PASS**（附"可达性"说明）

**反向对照（先证明危险是真的）**：

```
PASS B.control.premise  data_type='f' has_f=True   # 普通 openpyxl 写 "=1+1" ⇒ 公式单元格 + <f>
```

**真实导出（我自造的 13 条载荷，含 `=HYPERLINK(...)`、`-2+3+cmd|' /C calc'!A0`、`@SUM(1+9)*cmd|...`、Tab/CR 前导、含逗号引号、已转义项、前导空格、中文）**：

```
PASS B.escape.all      # 6 个触发字符各自成行：CSV/XLSX 都恰好是 "'"+原值；非触发值不被误伤
PASS B.xlsx.no_formula count=0                      # 导出**全部 XML 部件**里 <f>/</f> 零命中
PASS B.xlsx.celltype   data_types={'s'}             # 被转义单元格是字符串（不是公式）
PASS B.numeric.score   ref=I2 data_type='n' value=86.5 xml=<c r="I2" t="n"><v>86.5</v></c>
PASS B.numeric.csv     '86.5'                       # CSV 得分列是裸数字，未被转义成文本
PASS B.numeric.timestamp data_type='d' format='yyyy-mm-dd hh:mm:ss'
PASS B.csv.quoting     "'=1,2\"3DN8"                # RFC 4180 引号正确，转义前缀在引号内
```

⇒ **数字列未被文本化**（可求和/排序），**时间列仍是日期单元格**。

**真实 Excel（COM，只读打开我生成的文件）**：

```
row=2 scoreFormula=False scoreValue=86.5 nameFormula=False nameValue=张三 tsValue=46450.2125824421 tsFormat=yyyy-mm-dd hh:mm:ss
row=3 scoreFormula=False scoreValue=            nameFormula=False nameValue=      # running：得分为空，不是 0
row=4 scoreFormula=False scoreValue=0           nameFormula=False nameValue=李四  # 真实 0 分
row=5 scoreFormula=False scoreValue=0 nameFormula=False nameValue='=1+1NAME0
row=6..10 同样 HasFormula=False，值分别为 '+1+1…' / '-1-1…' / '@SUM(A1)…' / '\t=1+1…' / '\n=1+1…'
```

⇒ 在**真实电子表格引擎**里，6 类载荷一律 `HasFormula=False`（不是公式），且未完成行得分为空。

**可达性（重要口径，不是缺陷）**：

```
PASS B.reachability  text_fields=['course_id','evaluation_run_id','question_version_id','section_id','state','student_id','submission_id','submitted_at'] hostile=[]
PASS B.db.state_guard rejected=True   # INSERT state='=1+1' 被 ck_submissions_state 拒绝
PASS NOPII.api_null  rows=7           # student_number/display_name 恒 null
```

`GradeRow` 里唯一可能承载自由文本的两列（`student_number`/`display_name`）恒为 `null`（`grades/query.py:470-471` 硬编码）；其余文本列都是 UUID 或状态枚举，而状态枚举受库层 CHECK 约束（`alembic/versions/20260915_0002_b2_reception_storage.py:84`、`20260915_0006_b2_evaluation_tables.py:81`）。⇒ **HTTP 层当前没有可注入的载体**——这不是"防住了"，而是"打不到"。防线本身由渲染层用例覆盖，并被我的故障注入证明**有牙**（§5：拆掉转义 ⇒ 10 条失败）。

**结论**：B **PASS**。遗留观察（低，非规格违反）：契约只说"**开头**"是 `=`/`+`/`-`/`@`/Tab/CR，因此前导空格（` =1+1`）与 LF 前导（`\n=1+1`）**不转义**——按契约字面合规，我也**未**验证 Excel 对这两种形态的行为（不在本次口径内）。若要收紧属可选加固。

### C. 越权测试 → **PASS**

**查询侧（20 例，全部真实 HTTP）**：

```
PASS C.query[student A own]                200 rows=6 own_only=True
PASS C.query[student A target self]        200 rows=6
PASS C.query[student A target B]           403 foreign_hits=[]
PASS C.query[student A target ghost]       403 foreign_hits=[]     # 不存在的他人也是 403
PASS C.query[student A foreign section S2] 403 foreign_hits=[]
PASS C.query[student A ghost section]      403 foreign_hits=[]
PASS C.query[student A foreign course]     403
PASS C.query[student A uppercase own id]   200 rows=6              # 大写 UUID 被规范化，不误杀
PASS C.query[student A uppercase B id]     403
PASS C.query[student B own]                200 rows=2
PASS C.query[student A wide-scope token]   200 rows=6 own_only=True # 学生拿到"整门课"作用域仍只看自己
PASS C.query[assistant S1 course-wide]     200 rows=8              # 未指名课序不得扩到整门课
PASS C.query[assistant S1 foreign section] 403
PASS C.query[teacher C2 on C1]             403
PASS C.query[teacher C2 on C2]             200 rows=1
PASS C.query[admin on C1]                  200 rows=10
PASS C.query[admin on C2]                  200 rows=1
PASS C.query[no role]                      403
PASS C.query[outsider student]             403
```

**导出侧（10 例）**：`student A own` 200（6 行、只有 ST_A）/ `target B` 403 / `foreign section` 403 / `foreign course` 403 / `assistant S1` 200（8 行）/ `assistant S1 foreign section` 403 / `teacher C2 on C1` 403 / `admin C1` 200（10 行）/ `no role` 403 / `outsider` 403；**每个 403 的响应体都扫描过他人标识（ST_B/ST_C/ST_D/C2/S2/S9），零命中**。

**未认证**：无头/`Bearer`/`Basic`/垃圾 token/签名损坏 → 全部 `401`（5/5）。

**非法过滤** → `422`（两端点各 7 例，`cursor` 在 export 上未声明故除外）：`course_id` 缺失/空/非 UUID、`section_id`/`question_version_id`/`student_id` 非 UUID、坏游标；`limit=0/201/abc` → 422，`limit=200` → 200（对齐 `common.yaml:100` 的 `minimum:1, maximum:200, default:50`）。

**无枚举预言机**：

```
PASS C.oracle.student_id  equal=True   # 指名学生：存在 vs 不存在，403 响应体逐字节相同
PASS C.oracle.section     equal=True
PASS C.cursor.borrowed    status=200 got=2 own=6 subset=True   # 挪用教师页尾游标：只拿到自己的行
PASS C.cursor.crafted     status=200 got=6 own=6               # 自造游标指向最早位置：仍是自有集合
PASS C.cursor.plus_foreign_student status=403                  # 借游标 + 指名他人 → 仍 403
```

**未完成 ≠ 0 分**：

```
PASS C.state.all [('sub2','running',None,None,True), ('sub3','queued',None,None,True),
                  ('sub4','failed',None,None,True), ('sub5','rejected',None,None,True),
                  ('sub7','validated',None,None,True), ('sub9','running',None,None,True),
                  ('sub10','completed',None,None,True), ('sub6','completed',0.0,False,True)]
PASS C.rerun.latest     run_id=<seq2> expect=<seq2>       # 重跑显示最新运行（不显示旧 50 分）
PASS C.completed_no_result completed None                  # completed 但结果缺失 → null，不臆造 0
PASS C.no_run_state     validated                          # 无运行时 state 回落到提交状态，仍在契约枚举内
PASS C.export.empty_not_zero [('sub2','running',"''",'None',True), ..., ('sub6','completed',"'0'",'0',True)]
```

⇒ API `score` 为 `null`（**不是 0**）且带 `state`；导出对应单元格**为空**（CSV `""`、XLSX 空单元格）；**真实 0 分行仍是 0**（这条正是"未完成≠0 分"的判别式）。

**不含受保护内容**：我的 3 个标记写进了报告 JSONB / 静态发现 / 逐用例证据，导出 CSV、导出 XLSX 的全部解压部件、查询 JSON 三处**零命中**；导出包内只有 OOXML 部件（`[Content_Types].xml`、`_rels/.rels`、`docProps/*`、`xl/*`）。

**结论**：C **PASS**。**未发现任何让学生拿到他人数据的路径**（含导出、含错误响应体回显）：显式指名（403）、跨课序（403）、跨课程（403）、不传参（收敛为自己）、同课序同学（不可见）、课程级作用域的学生令牌（仍收敛为自己）、UUID 大小写/花括号变体（规范化后同判定）、借用/自造游标（每页重新施加作用域谓词）、非法 UUID（422 且在触库前拒绝）。

## 4. 实现者 5 条偏差的裁决

| # | 偏差 | 裁决 | 性质与路径 |
|---|---|---|---|
| ① | `student_id` 实现接受但 `listGrades.parameters` 未声明 | **成立**，且**范围比自述更宽**：`exportGrades.parameters` 也未声明（实测 `A.contract.student_id_undeclared: list=False export=False`；b2.yaml:169-174 与 201-205） | **契约声明缺口**，非实现缺陷。description（b2.yaml:163-164）明文要求"widening `student_id` → 403 而非静默收窄"，实现按 description 做是对的（静默忽略才是被禁止的）。**路径：CHG**，把 `student_id` 补进两条端点的 `parameters`（`Uuid`, required:false）。 |
| ② | 两端点未声明 `422`，实现按 422 | **成立**（实测：7 类非法过滤 × 2 端点全部 422；b2.yaml:175-189 与 206-216 无 422） | **契约声明缺口**。仓库自身约定 `docs/api/contract.md:48` 就是"校验失败 422"，实现与约定一致。**路径：CHG** 补 `422`。 |
| ③ | `module.yaml.routes` 未覆盖 `/api/v1/grades` | **成立**，我补强了两条证据：(a) `b2.yaml:17` 明文声称 "Every path below is declared in B2/module.yaml `routes`" —— 对 `/api/v1/grades`、`/api/v1/grades/export` **为假**（`module.yaml` 只有 `[/api/v1/submissions, /api/v1/evaluation-runs]`）；(b) `../B4/src/autograder_b4/registry.py:351-368` 的 `_assert_unique_routes` **只比较已声明前缀** ⇒ 成绩前缀的"重复归属"保护看不见 B2 的归属 | **声明缺口（跨模块）**，实现侧**不可独自修复**（`module.yaml` 属禁改清单）。**路径：CHG**，且必须**同时**改 `module.yaml.routes` 与 `plugin.py` 的 `descriptor.routes`（`tests/test_plugin.py` 断言两者逐项相等），或改 b2.yaml 那句声明。 |
| ④ | `exportGrades` 声明 `404` 但实现从不返回 | **成立**（实测：不存在的课程 → admin 导出 `200` + 5205B 仅表头文件、admin 查询 `200 {"items":[],"next_cursor":null}`、教师对未覆盖课程 `403`；`api/grades_routes.py` 里 `404`/`NotFound` 出现 **0** 次） | **不是实现缺陷，是"声明无法被实现兑现"**：B2 不拥有课程，且 b2 schema 内"课程不存在"与"课程无提交"不可区分（两者都是零行）；`requires=("host","b4-auth","b3-questions")` 里没有任何"课程存在性"端口。另外它**与仓库自身约定冲突**：`docs/api/contract.md:48` 写"有权限但资源不存在 `404`"。**路径：跨模块裁定**——(a) CHG 删掉 `404` 并写明"不可区分 ⇒ 200 + 空结果"（信息最小化，我倾向此项），或 (b) 由 B4 提供课程存在性接口后实现 404（新依赖，应另立工作项）。**严重度：低-中**（文档/契约一致性，不影响 A/B/C）。 |
| ⑤ | 未声明查询参数（含 `offset`）被忽略而非拒绝 | **成立**，且**满足规格**：负面声明是"不用 offset 分页"。实测 `offset=2` 与不带 offset **响应逐字节相同**、非法 `offset=abc` 不报错且响应同上、`offset+cursor` 同时出现时 `cursor` 仍生效（keyset 未被污染）；`grades/query.py` **代码行**里 `OFFSET` 出现 0 次（剥掉三引号 docstring 后统计，`:253-255` 的说明文字除外） | **判定：规格被满足**。判据是"不生效"（已证），而"被拒绝"不是任何一条验收标准的组成部分；OpenAPI 未声明参数属接口外，FastAPI 忽略是常规行为。**残余风险**：以为能按 offset 翻页的调用方会静默拿到第一页。**路径**：若 MAIN 要求严格拒绝未声明参数，那是**新行为**（改实现 + 补契约 + 补用例），应另立工作项，不应塞进本工作项。注意与偏差①的区别：`student_id` 在 description 里有**强制的语义要求**（→403），`offset` 没有任何语义要求，所以"接受前者、忽略后者"是有理据的取舍，不是双标。 |

## 5. 故障注入：既有测试有没有牙（我加的，不是实现者的）

做法：**不改被测文件**，用 `pytest` 插件在进程内把实现替换成缺陷版本，看对应断言是否变红。

| mutation | 改法 | 结果 |
|---|---|---|
| `noescape` | `escape_spreadsheet_cell` → 恒等 | `test_grades_query_export.py`：**10 failed**（抓到）；`test_b2_routes.py` 相关 1 条 **passed**——该条只断言"路由原样透出渲染器字节"（其文件 docstring:17-19 已声明这个定位），对"转义本身"不敏感，属设计如此，不是漏网 |
| `zero` | `_row_from_record` → 未完成一律 `score=0.0/max=100/is_pass=False` | grades **3 failed** + routes **2 failed**（抓到） |
| `wallclock` | `_document_moment` → `datetime.now(UTC)` | **2 failed**（连续 3 次运行均失败）：`test_render_xlsx_is_byte_identical_...`、`test_document_moment_is_derived_from_the_rows_not_from_a_constant` |
| `zipstamp` | `_repack_deterministically` → 恢复 `writestr` 默认本地时间戳 | **1 failed**（连续 3 次均失败）：`test_render_xlsx_is_byte_identical_...`（内含 `_zip_stamps == {(1980,1,1,0,0,0)}` 断言） |
| `drop_tail` | `_fetch_page` → 每页丢掉最后一行（游标仍由被丢行派生 ⇒ 边界丢行） | grades **3 failed**（`test_export_walks_every_cursor_page`、`test_cursor_pagination_is_complete_and_disjoint`、`test_pagination_is_keyset_not_offset`）+ routes **2 failed**（抓到） |
| `open_scope` | `require_grade_authority` → 不做学生收敛 | grades **3 failed** + routes **3 failed**（抓到，含 `/api/v1/grades` 与 `/grades/export` 两条越权用例） |

⇒ 六条关键不变量（转义、未完成≠0、docProps 确定性、ZIP 戳确定性、分页完整性、越权收敛）**都有可证伪的守卫**。

一次实测意外（记录在案）：`ignore_cursor`（把 `after` 恒置 `None`）会让 `_walk` **永远翻页**，pytest 挂死到我自己超时 kill（这也是 §9-N4 的由来），所以我改用会终止的 `drop_tail`。项目**没有** `pytest-timeout`。

## 6. 对抗"确定性"修复（解包 + 证伪尝试）

`recheck_run.log` D 段（同一批固定行，`render_xlsx` 直接调用 + 真实 HTTP 导出）：

```
PASS D.same_input     15dee6b5... vs 15dee6b5...   # 跨 1.15s（跨秒）逐字节相同
PASS D.empty          d0ed7129...                  # 空批次两次相同
PASS D.one_row        aa0c9f96...                  # 单行两次相同
NOTE D.row_order      same=False                   # 行序不同 ⇒ 字节不同
NOTE D.case_sensitivity same=False                 # 仅大小写不同 ⇒ 字节不同
NOTE D.submillisecond same=True                    # 仅微秒位（同一毫秒内）不同 ⇒ 字节相同
PASS D.docmoment      {'created':'2027-03-04T05:06:08Z','modified':...}  # 本批最新 submitted_at 截到秒
PASS D.docmoment.empty {'created':'1980-01-01T00:00:00Z',...}
PASS D.docmoment.not_constant 更晚的行 → '2027-03-09T05:06:07Z'
PASS D.zip_stamps     {((1980,1,1,0,0,0), 0, 8)}    # 全部条目 DOS 戳下界 + create_system=0 + DEFLATED
PASS D.no_wallclock   hits=[]                       # 产物里无墙钟的日期/秒/epoch 三种形态
PASS D.cross_process  child=['15dee6b5...','15dee6b5...'] parent=15dee6b5...  # 两个新解释器（PYTHONHASHSEED 随机）
PASS D.readable       testzip=None missing=[] title='成绩'
PASS D.visible_settings freeze='A2' bold=True fmt='yyyy-mm-dd hh:mm:ss'
PASS D.timestamp.roundtrip datetime(2027,3,4,5,6,8,123000)   # .123456 → .123000，不回卷成 .124/.000
PASS D.http_same_bytes 019d6d75... vs 019d6d75...  # 真实数据 + 真实路由，跨秒两次相同
PASS D.http_empty_course 200, 0 rows               # 无提交的课程 → 仅表头文件
```

**逐条判断"不同"是否缺陷**：

- `D.row_order`（同集合不同到达顺序 ⇒ 字节不同）：**不是缺陷**。导出走 `ORDER BY created_at ASC, submission_id ASC`（`grades/query.py:430`）⇒ 同一查询的行序是全序且稳定。
- `D.case_sensitivity`（`ABC` vs `abc` ⇒ 字节不同）：**不是缺陷**，正确行为（确定性 ≠ 恒等；既有测试 `test_determinism_is_not_identity...` 也钉了这条）。
- `D.submillisecond`（仅微秒位不同 ⇒ 字节相同）：**已知且有意的精度损失**（`_xlsx_moment` 截到毫秒 `grades/export.py:308-316`，与对外 `rfc3339_millis` 一致）。真实场景下不会造成"两行不可区分"，因为 `submission_id` 本身就在单元格里；即便同为 0 分、同秒、同学生，两行的 `submission_id` 不同 ⇒ 字节不同。**判定：可接受，非缺陷**。
- 另外我构造的**跨进程**（新解释器 × 2，哈希随机化下）与**跨秒**两种场景都字节相同 ⇒ "字节确定"不是同进程缓存造成的假象。

**结论：确定性修复成立**，且 OOXML 结构/数字格式/列宽/冻结窗格/加粗表头全部保留、`zipfile.testzip() is None`、openpyxl 可读回、真实 Excel 可打开。

## 7. 实现者 6 条"未证实项"的裁决

| # | 自述 | 裁决 | 我的证据 |
|---|---|---|---|
| U1 | 中文**值**无法经 DB 进 `GradeRow` ⇒ HTTP 只覆盖中文列名 | **成立** | 10 行的文本字段集合只有 8 个 UUID/枚举列（`B.reachability`）；`NOPII.api_null` 两字段恒 null；`student_number`/`display_name` 在 `grades/query.py:470-471` 硬编码为 `None`。我另在**渲染层 + 真实 Excel** 上补到了中文值（`CN.xlsx_value D2='张三丰' C2='二〇二七〇〇一'`、`CN.csv_bytes`、`CN.xlsx_xml_bytes`、Excel `nameValue=张三`） |
| U2 | 公式注入链条是"渲染层字节 + HTTP 字节 == 渲染器字节 + 整文件扫描"两步闭合 | **成立**（我独立闭合） | `CHAIN.xlsx_bytes`/`CHAIN.csv_bytes`/`CHAIN.service_agrees`（HTTP 字节 == `render_*`，路由未二次编码）+ `B.xlsx.no_formula`（整包 `<f>` 零命中）+ `B.control.premise`（反向对照） |
| U3 | 并发（导出期间新提交）无用例 | **成立**（原实现无该用例） | 我补了 4 条真实并发探针（遍历中途在**另一条连接**提交）：`CONC.no_duplicates rows=6 unique=6`、`CONC.new_row_included newer_in=True`、`CONC.older_row_skipped older_in=False`、`CONC.exact_set walked=6 expected=6` ⇒ 新提交被包含、无重复、无错位；**比游标更早的行不回补**（只可能出现在历史导入，属预期语义） |
| U4 | 无 Excel/LibreOffice 人工打开 | **不再成立——我做到了（Excel 侧）** | 宿主机有 Excel；用 COM late binding 只读打开导出文件（§3-B）：6 条载荷 `HasFormula=False`、中文姓名完好、未完成行得分为空、真实 0 分行 = 0。**LibreOffice 未安装**（`soffice` 不存在），所以"跨办公套件"仍未验；且这是程序化读取，不是人眼看表 |
| U5 | `Content-Disposition` 仅 ASCII 文件名 | **成立** | `PASS E.headers`：`attachment; filename="grades-<uuid>.xlsx"` / `.csv`，头部 ASCII（`header[1].isascii()`）；媒体类型 `application/vnd...sheet` 与 `text/csv; charset=utf-8`。因 `course_id` 必为 UUID，无 RFC 5987 `filename*` 也不构成风险 |
| U6（顺带） | 数据/依赖不 mock | **成立** | 全程真实 PostgreSQL（临时库迁移到 `EXPECTED_SCHEMA_REVISION`）+ 真实 Ed25519 验签 + 与宿主相同的 `B2Module.router(context)` 注册路径 |

## 8. 偏差 ④ / ⑤ 的性质（单独展开，回答任务书第 5 问）

- **④（404 声明但永不返回）**：**"声明缺口 + 与仓库自身约定冲突"，不是实现缺陷。** 理由三条：(1) b2 schema 内"课程不存在"与"课程无提交"**不可区分**（都返回零行），B2 也没有课程存在性数据源（`requires` 里只有 `b4-auth`）；(2) 声明一个响应并不强制实现返回它（OpenAPI `responses` 是可用集合，不是必返集合）；(3) 返回 200 + 空结果反而是**信息最小化**的（不泄露"该课程是否存在"，与"不用 404 掩盖 403"的硬规则同向）。但它确实与 `docs/api/contract.md:48`「有权限但资源不存在 `404`」相抵。⇒ 需要 MAIN 裁定：**倾向 CHG 删掉 `404`** 并在 `exportGrades`/`listGrades` 里写明"空结果 = 200 + 仅表头文件"；若要真 404，则必须引入 B4 课程存在性接口（新依赖，另立工作项）。
- **⑤（`offset` 被忽略）**：**满足规格**。负面声明是"**不用** offset 分页"，实测 offset 完全不生效、非法值不报错、与 cursor 同现时 cursor 仍生效、代码里无 `OFFSET`。**"被忽略"与"被拒绝"都能满足该负面声明**（后者更强但非必需）。若 MAIN 想要"未声明参数一律拒绝"，那是**新行为**：改实现（白名单校验）+ 改契约（声明该策略）+ 补用例，走新工作项；本工作项按现状合规。

## 9. 新缺陷 / 观察清单（按严重度）

> 均**不在**实现者自列的 5 偏差 / 6 未证实项内；前两条是文档与契约一致性，后面是容量/健壮性/边界。

**N1（中；文档与证据归档未同步 —— 违反 `AGENTS.md`「文档同步」硬规则，但实现者已显式声明 docs 范围外）**

- `docs/api/contract.md:19-20` 仍写"成绩查询/成绩导出 = 待实现"；`:56` 写"其余端点（SSE、报告、成绩查询与导出）仍为**契约声明**，尚未注册" —— **对注册状态的事实描述已为假**（我用与宿主相同的 `B2Module.router` 实测两条端点均 200/403/422 正常），且该文件没有"状态表待刷新"的免责说明（`docs/README.md:23-30` 的免责只覆盖 README 的状态表）。
- `docs/testing/cases.md:64-65`：`T-PERSIST-003`（口径一致、中文不乱码）与 `T-PERSIST-004`（转义防公式注入）仍标"待建"，而 `tests/test_grades_query_export.py` 正是这两条（我按 6 个触发字符逐字符验证）。
- `docs/requirements/traceability.md:24`：`RQ-B-2-60` 仍为 `待实现`、证据列 `—`。
- `docs/README.md:41`：仍 `待实现`，且"最后验证方式"指向 `phase-01-implementation.md` 第 9 条——而第 9 条正是本工作项修掉的缺陷。
- `docs/phases/active/phase-01-implementation.md:79-84` 的"最近一次全量 = `1102 passed, 3 skipped, 1 xfailed`"未更新为本次的 `1115`；`:135-139` 的 N4（`render_xlsx` 墙钟）仍未标注"已修复"，只留"恢复验证/修法"。
- 归因说明：实现者报告（`.pytest-tmp/GRADES_IMPL_REPORT.md:225,247`）明确写了"建议的用例清单（给 docs/testing/cases.md，**文档由 MAIN 统一同步**）"并把 `docs/**` 列为范围外 ⇒ **是已声明的范围外，不是隐瞒**；但按仓库规则，本工作项收口时至少要改：`contract.md`（第 11/19/20/56 行的一处事实性错误必须修）、`cases.md` 两条状态、`traceability.md` 一行、`README.md` 一行、`phase-01` §9 结案 + 新的全量数字。

**N2（低-中；契约里不可兑现/不可证伪的声明）**：`b2.yaml:17`（"Every path below is declared in module.yaml routes"）与 `b2.yaml:216`（exportGrades 的 404）两条声明在 B2 侧都无法成立，见偏差 ③④。建议与 ③④ 的 CHG 一并裁定，避免"契约写了但没人能实现/验证"。

**N3（低-中；容量/资源，非规格违反）**：导出把**全部匹配行一次读进内存**（`grades/export.py:260-268` 的 `list(self._query.iter_grades(...))`），XLSX 也在内存里整包构建，且没有行数上限/超时——大课程（十万行量级）会同时吃内存与请求时长。契约未规定上限，故不是违反；但应作为容量风险登记（如 MAIN 要上限，需先定契约）。

**N4（低；测试健壮性）**：项目无 `pytest-timeout`。实测：把 `_fetch_page` 改成忽略游标，`iter_grades` 的 `_walk`（`grades/query.py:320-330`）会**永远翻页**，`tests/test_grades_query_export.py::test_export_walks_every_cursor_page` 相关断言会**挂死而不是失败**。建议给导出/分页用例加显式页数上限断言或引入超时插件。

**N5（低；元数据边界）**：`_document_moment`（`grades/export.py:180-193`）以 `max(1980-01-01, 行)` 取文档时间：若某课程的行**全部早于 1980-01-01**（理论上可能的迁移/历史导入），文档时间会**晚于**真实最新行；且该批行与空批次共享同一 docProps（字节仍因单元格不同而不同）。仅元数据不精确，不影响内容。

**N6（低；潜在契约级不一致 —— 今天不可达，B4 接线后会变成真问题）**：XLSX 是 XML 1.0，单元格文本里的 **CR 被行尾规范化为 LF**（实测 `'\r=1+1'` → XLSX 读回 `'\n=1+1'`，而 CSV 保留 `\r`）。即**含 CR 的值在导出与查询之间不可能"逐字段逐字节一致"**。今天不可达（`display_name`/`student_number` 恒 null），实现者测试里也书面标注了这一点（`tests/test_grades_query_export.py:567-568`）⇒ 属**已知且已记录**的差异。建议：B4 接线补全姓名/学号时明确归一（拒绝 CR 或导出时统一替换），否则 A 的"逐字段一致"会在该列破掉。

**N7（低；观感）**：转义是把 `'` **写进值**，因此 Excel 会**显示**这个撇号（COM 实测 `nameValue='=1+1NAME0`）。OWASP 口径可接受、契约也未要求隐藏；若产品上不接受，可改用单元格 `quotePrefix` 样式（openpyxl 支持），属可选打磨。

**N8（很低；环境洁净度）**：Postgres 上有 13 个残留临时库 `b2_test_*`，全部含实现者夹具标记（`submission_digest` 前缀 `aaaa`、`idempotency_key` 无 `recheck-` 前缀）⇒ **不是我的探针留下的**（我的两个探针正常退出，`temporary_database()` 的 finally 会 drop）：其中至少 1 个来自我被迫 kill 的 `ignore_cursor` 挂死运行，其余来自更早的实现者/复核者被中断的运行。**我没有删除任何库**（避免销毁他人证据）。另外我的 EXCEL 进程已全部清理（`Get-Process EXCEL` → 0）。

**我自己探针的 bug（如实记录，避免把"我的错"记成"实现的错"）**：首轮有 4 处探针自身缺陷——(1) 触发载荷期望值漏了行后缀导致 `B.escape.all` 假 FAIL；(2) 把 `cursor` 传给不声明它的 export 却期望 422；(3) 误以为 XLSX 未压缩字节里能直接搜到中文（实为 DEFLATE 压缩，须先解压部件）；(4) `slots=True` 的 dataclass 无 `__dict__`。均已修正，修正后 `FAIL=0`；修正过程未改动任何被测文件。

## 10. 结论

**A / B / C 三条验收：全部 PASS**（196 + 12 = 208 条独立断言，0 FAIL；两组测试文件 104 passed、全量 1115 passed/3 skipped/1 xfailed、ruff 全部复算一致）。

**5 条偏差**：① ② ③ **成立**（都是**声明缺口**，路径 = CHG，不顺着实现改契约也不偷改实现）；④ **成立但性质是"声明无法兑现 + 与仓库 404 约定相抵"**，不是实现缺陷，需 MAIN 跨模块裁定（倾向 CHG 删 404）；⑤ **成立且满足规格**（"不生效"已证，"被拒绝"非必需；若要更强属新工作项）。

**6 条未证实项**：U1 U2 U3 U5 **成立**（U3 我补了并发探针，通过）；**U4 被我实测推翻**（Excel COM 只读打开验证通过；LibreOffice 仍缺）。

**新缺陷**：无阻断级缺陷；N1（文档未同步，含一处事实性错误陈述）为最需处理项，其余为容量/健壮性/边界观察。

**一句话总评**：**本工作项可以算"通过"**（三条验收标准均有我独立复现的可复算证据，确定性修复经证伪尝试后成立，越权边界无缺口），但收口前有**一行级必办**：

> **把 `docs/api/contract.md:56`（"成绩查询与导出……尚未注册"）与 `:19-20`、`docs/testing/cases.md:64-65`、`docs/requirements/traceability.md:24`、`docs/README.md:41`、`docs/phases/active/phase-01-implementation.md:79-84/§9` 按实际状态刷新（它们现在与代码/测试不符），并把 ①②③④ 四条契约侧缺口一次性提 CHG 交 MAIN 裁定。**
