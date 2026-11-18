# 题目 IO 类型（RQ-B-3-40 / 50 / 60）任务目标与实施计划

> 状态：待实施　|　来源：老师补充要求 + 对仓库现状的核查（2026-11-18）　|　配套提示词：[coding-prompt.md](./coding-prompt.md)

## 0. 一页结论

1. **老师的三类就是"待测代码如何拿到测试数据"的 IO 规范**，每道题必属其一：

   | 类型 | 需求号 | 待测代码如何接收测试数据 | 建议枚举值 |
   |---|---|---|---|
   | 命令行型 | RQ-B-3-40 | 命令行参数 + 标准输入 | `stdin` |
   | 文件型 | RQ-B-3-50 | 从工作区文件读取输入、（可选）写出产出文件 | `file` |
   | 接口型 | RQ-B-3-60 | 代码只提供函数/接口，由平台 `import`/`source` 后调用 | `callable` |

2. **核查发现：平台"能力"已基本存在，缺的是"概念、约束与产品化"。**
   - B3 的 `adapter_manifest.kind` ∈ `stdin|file|callable`，B2 已有三种执行形态和 `callable` 固定 harness、`mount=input` fixture 物化进沙箱（`B2/.../runner/adapters.py`、`fixtures.py`；`B3/.../compile/adapters.py`）。
   - `b3.questions` **在代码与迁移里并没有 `type` 列**（只有 title/stem/language/status/tags…）；`language` 受 `python|bash` 约束。你说的"预留的 TYPE 列"需要先在线上库只读核实（见 P0）。若线上确有该列，复用它，**不要再加第二列**。
   - 类型只活在"版本级"`adapter_manifest.kind` 里，是**可随版本变更**的（修订差异里还有 `adapter_kind_changed`），不是题目的稳定属性；题库列表、作业、统计都没有这个维度。
3. **三个真实缺口（不是改个字段能解决的）：**
   - **文件型**：fixture 现在是"受保护内容"（学生看不到），没有"学生可下载的附件"；题干只有图片资源（`QuestionAsset` 仅图）；fixture 是版本级而不是用例级（不同用例需要不同输入文件时不够用，需核实）。
   - **接口型**：`callable` harness 是 **Python 专用**（`__b2_harness__.py`）。UNIX 课程（bash）没有对应的"`source` 后调用函数"的 harness。
   - **你的理解与需求原文有出入**：需求原文是"待测代码**实现**某接口，经 import/include 后被测试"（学生写函数，平台调用）；你的描述是"云端提前准备**库函数/API 供学生调用**"（平台提供库，学生调用）。两者方向相反。见决策 D5。

## 1. 现状 vs 目标差距

| 层 | 现状 | 缺口 |
|---|---|---|
| 数据模型（B3） | 无题目级类型；类型仅在版本 `adapter_manifest.kind` | 题目级 `io_type`（NOT NULL、CHECK、不可变），与版本 kind 一致性校验，旧数据回填 |
| 契约（MAIN） | `b3.yaml` 版本 DTO 有 `adapter_kind`；题目/列表/统计无类型 | 题目 DTO、列表过滤、统计分组、作业条目带类型；锁摘要同步 |
| 出题（B3+B1） | 向导里手选 adapter；各类型必填项无差异化门禁 | 按类型的向导步骤与发布门禁；AI 出题按类型生成 |
| 题干附件 | 仅图片资产；fixture 学生不可见 | "学生可见输入文件"+"评测隐藏文件"，下载接口，纳入内容摘要 |
| 评测（B2） | stdin/file/callable(Python) 已有 | bash 接口型；用例级文件；类型感知的提交校验与错误归类；参考答案验证走同一路径 |
| 展示（B1） | 题目详情渲染 Markdown+图 | 类型徽标、IO 规范面板、附件下载、提交指引；作业/评分/统计处同样展示 |
| 存量数据 | Python 题库、UNIX 作业题库（`MAIN/scripts/*`） | 回填类型；导入脚本带类型 |

## 2. 设计决策（带默认建议，D5/D6 请老师确认）

- **D1 单一词表**：题目类型 = `adapter.kind`，枚举 `stdin|file|callable`，不引入第二套命名。展示中文名：命令行输入 / 文件读取 / 接口调用。列名建议 `questions.io_type`（若线上已有 `type` 列则复用其名并加 CHECK）。
- **D2 类型不可变**：创建题目时确定；后续版本 `adapter.kind` 必须等于题目 `io_type`，否则拒绝（取代 `adapter_kind_changed` 的"允许变更并标记"）。理由：作业、统计、学生体验、附件形态都依赖类型。想换类型 = 新建题。
- **D3 迁移**：Alembic 迁移，先加可空列 → 按最新版本 `adapter_manifest->>'kind'` 回填 → 置 NOT NULL + CHECK。**保留业务数据，不重建库**。无版本或 kind 异常的行列出清单交人工处理，不静默默认。
- **D4 附件模型**：在 fixture 上增加可见性（`student_visible` 布尔，默认 false）。可见者：学生在题目页可下载、同时挂载进沙箱；隐藏者：只挂载（评测数据）。二者同属版本内容，纳入 `content_digest`/`package_digest`；已发布版本不可增删（沿用 ADR-0001/0016）。路径约定：相对工作区根目录，题干必须写明读哪个路径。
  - 需核实并在必要时扩展：**用例级输入文件**（每个用例各自的输入文件/期望产出摘要）。若现状只有版本级，则新增 `case.files`，且单用例沙箱只挂载该用例文件。
- **D5 接口型语义（已确认：按需求原文）**：学生实现函数，平台 import/source 后调用并比对返回值。"平台预置库供学生调用"**不在本期范围**（不做 `mount: support`，不留半成品接口）。
- **D6 课程×语言×类型矩阵（已定）**：PYTHON 课程 → `python`；UNIX 课程 → `bash`；两门课三类全开（UNIX 的 `callable` = `source` 后调用函数，需 bash harness）。创建/发布时由 B3 校验"课程↔语言↔类型"，不放行组合在代码里以常量矩阵表达并写入契约说明。
- **D7 类型与执行模式正交**：`execution.mode`（`per_case|single_run|ssh_session|email_delivery`）保持不变；`single_run` 仅允许 `stdin|file`（现有规则）。存量 `ssh_session/email_delivery` 题的 `adapter.kind` 已存在，按其值回填；若有不属于三类的，P0 列出并请老师裁定。
- **D8 类型驱动**：所有"必须按类型不同"的行为只读 `io_type`（显式字段），不得从标题/标签/路径推断。

## 3. 各类型的出题、评测、展示规范

### 3.1 `stdin` 命令行型
- 出题：每用例 `args[]`、`stdin`、期望 `stdout`/退出码；文本上限沿用现有边界（stdin ≤ 64 KiB）。
- 评测：B2 现状已支持，保持。
- 展示：IO 规范面板列出"如何运行（`python main.py <args>` / `bash main.sh <args>`）、输入来自 argv/stdin、输出到 stdout"，并给 1 个公开样例（来自可公开的用例）。

### 3.2 `file` 文件型
- 出题：上传输入文件；声明每个文件 `path`、`student_visible`；声明期望产出文件（`mount=expected_output`，只存摘要）或 `file_checks`；用例级文件如 D4。
- 评测：B2 物化 `mount=input` 进工作区并校验摘要（已有）；学生代码按相对路径读取；评测后按摘要比较产出文件；**失败归类**区分"学生没读到/读错文件"与"平台交付失败（`infrastructure_error`）"。
- 展示：附件区（文件名、大小、sha256、下载按钮，仅 `student_visible`）；明确"文件位于工作区相对路径 X"；提交页提示需读取的路径与产出文件名。
- 安全：路径穿越拒绝（已有 `validate_workspace_path`）、大小上限（`max_fixture_bytes`）、内容类型白名单、隐藏 fixture 不得出现在任何学生可见接口/日志/证据摘录里。

### 3.3 `callable` 接口型
- 出题：声明 `callable.module/attribute/arguments_json`；用例以参数→期望返回值/输出表达；学生提交的入口文件名必须与 `module` 一致。
- 评测：Python 沿用 `__b2_harness__.py`；**bash 新增固定 harness**（`source ./<module>.sh` 后调用函数，参数传递与退出码/输出采集规则写进契约）；harness 文件名保留，提交侧不得提供同名文件。
- 展示：给出必须实现的**函数签名**、文件名、示例调用；导入失败/属性不可调用的错误要对学生友好（现有 `import_error` 归类）。

## 4. 影响面清单（含你已想到的 + 补充）

**你提到的：** ① 出题流程按类型准备不同材料（附件进评测环境）；② 页面显示类型（题目页、作业内的题目同样显示）。

**补充：**
1. **契约与 SDK**：`MAIN/contracts/openapi/b3.yaml`（题目/版本/列表过滤/统计/作业条目）、`language.schema.json` 旁新增类型 schema、`evaluation-package.schema.json` 如需新增 `student_visible`/`case.files`；同步 `contracts.lock`、B1 `pnpm api:generate`、CHANGELOG。
2. **B3**：模型+迁移+回填、创建/新建版本/发布校验、列表筛选 `io_type`、统计按类型分组（RQ-B-3-30）、AI 出题（`draft_generation.py`/`assistance.py`）按类型约束输出、参考答案验证、事件白名单（`events/whitelist.py`）是否带类型、运行日志带类型（RQ-B-3-99）。
3. **B2**：包解析（`runner/package.py`）、物化（`fixtures.py`）、harness（`adapters.py`）、静态检查（`static/`）按类型的规则、沙箱策略对文件/库只读挂载、提交校验（文件名/入口）、证据与解释（`evaluation/explanation.py`）按类型输出人话、参考答案验证（`reference/`）。
4. **B1**：出题向导（`features/authoring/*`，现有 `adapterKind` 表单）改为"先选类型"、列表/详情（`features/questions/*`）、作业编排（`AssignmentComposer`）题目卡片、学生作答页（`submission/AssignmentWorkspace`）指引、评分/成绩证据（`grades/CaseEvidence`）、统计图表。
5. **B4**：作业条目→题目类型的展示读取、课程↔语言矩阵所需的课程元数据（课程代码/语言），学生对附件下载的权限（选课+作业已发布）。
6. **B5**：一致性/质量报告按类型分层（不同类型的评分稳定性不同）。
7. **存量与运维**：`MAIN/scripts/import_python_bank.py`、`import_question_bank.py`、`import_sample_linux_bank.py`、`maintain_question_bank.py`、`data/unix-homework-10.json` 全部带类型；线上迁移顺序与回滚说明。
8. **安全**：附件下载鉴权、隐藏 fixture 不泄露、上传类型/大小/文件名校验、nsjail 只读挂载、AI 出题不得把隐藏数据写进题干。
9. **边界**：题目已被作业引用/已有提交时禁止改类型（D2 已覆盖）；版本不可变（ADR-0001）下附件的新增需走新版本。

## 5. 任务目标与验收标准

| 编号 | 目标 | 验收（线上） |
|---|---|---|
| G1 | 每道题有且仅有一个 `io_type`，存量全部回填，库内无空值 | 题库列表每题显示类型；查询无空类型 |
| G2 | 创建/改版/发布强制类型与课程语言矩阵、类型与版本 kind 一致 | 违规请求得到明确 4xx 与可读原因 |
| G3 | 出题向导按类型引导，发布门禁校验该类型必备材料 | 三类各出一题并发布成功；缺材料不能发布 |
| G4 | 文件型：学生可下载可见附件，评测环境含全部输入文件，学生读取后可得分 | 用 `testuser` 下载附件、提交读文件的代码、得到正确评分；隐藏文件不可下载 |
| G5 | 接口型：Python 与 bash 均可 import/source 后被测试 | 两门课各一道接口型题评测通过/失败均符合预期 |
| G6 | 题库、作业内题目、作答页、评分页均显示类型与 IO 规范 | 四处页面可见且一致 |
| G7 | 统计/筛选/AI 出题/导入脚本/日志均带类型 | 列表可筛类型；统计按类型分组；日志含 `io_type` |

## 6. 分阶段计划（每阶段独立提交并线上验证，顺序有依赖）

| 阶段 | 内容 | 主要落点 | 完成定义 |
|---|---|---|---|
| **P0 核查（只读）** | 只读查线上/测试库：`b3.questions` 实际列、存量版本 `kind` 分布、课程与语言对应、`ssh_session/email_delivery` 题现状；确认 fixture 是否支持用例级；输出核查结论追加到本文末尾 | B3 DB、B4 courses | 结论写回文档；未决问题提给老师 |
| **P1 契约** | 类型枚举 schema；b3/b2 openapi 变更；`student_visible`/`case.files` 的包 schema；更新锁摘要与 CHANGELOG；生产者/消费者同改 | `MAIN/contracts/**`、各 `contracts.lock` | 契约校验通过；各模块锁摘要一致 |
| **P2 B3 类型落库** | 迁移+回填+约束；创建/改版/发布校验；矩阵校验；列表筛选；统计分组；导入脚本带类型；事件/日志 | `B3/src`、`B3/alembic`、`MAIN/scripts` | B3 受影响测试通过；线上回填后无空值 |
| **P3 B3 附件** | `student_visible`、下载接口与鉴权、内容摘要纳入、用例级文件（如需） | `B3/domain/assets.py/fixtures.py`、`infra/storage.py`、router | 上传/下载/鉴权/已发布不可改测试通过 |
| **P4 B2 评测** | 类型感知提交校验；用例级物化；bash callable harness；失败归类与解释；参考答案验证覆盖三类 | `B2/src/runner,evaluation,reference,static` | 三类×两语言的 B2 测试通过（含 nsjail 相关既有测试） |
| **P5 B1 前端** | 向导按类型；列表/详情/IO 规范面板/附件区；作业编排与作答页；评分证据；统计；重新生成 API 类型 | `B1/src/features/**` | 受影响 Vitest/类型检查/lint 通过 |
| **P6 B4/B5 联动** | 作业条目带类型读取；附件下载授权；B5 报告分层 | `B4`、`B5` | 对应测试通过 |
| **P6b 样例题库** | 见第 9 节：每门课×每种类型的线上样例题，幂等种子脚本，发布并编排样例作业 | `MAIN/scripts/**` | 6 题（以上）线上可见、可提交、参考答案验证通过 |
| **P7 线上验收** | 部署→Actions 全绿→用 `admin`/`testuser` 走完三类题全链路（出题→发布→编排作业→学生提交→评分→统计） | 线上环境 | 报告真实结果与未验证项 |

> 依赖：P0 → P1 → P2 → (P3, P4 可并行) → P5 → P6 → P7。契约变更先行，避免生产者/消费者漂移。

## 7. 风险与待老师确认

| # | 风险/问题 | 默认处理 |
|---|---|---|
| R1 | 平台预置库供学生调用的诉求 | 已确认本期不做 |
| R2 | UNIX 接口型依赖 bash harness | 已定开放，P4 实现 |
| R3 | 存量题类型异常/缺版本 | 列清单，人工裁定，不静默默认 |
| R4 | 用例级文件需求若现状不支持，会触及包 schema 与 B2 物化 | P0 核实后在 P1 一并改 |
| R5 | 已发布题类型回填错误会影响统计 | 回填只读 `adapter_manifest.kind`，迁移前后做计数对账 |
| R6 | 日常规则"禁止本地构建，线上验收" | 各阶段只跑受影响静态检查/测试，推送后跟进 Actions |

## 9. 样例题目要求（线上）

线上样例必须覆盖 **2 门课 × 3 类型 = 至少 6 类格子，每格至少 2 题**（一易一中，共 ≥12 题），且全部带 `io_type`、已发布、参考答案验证通过：

| 课程 | stdin | file | callable |
|---|---|---|---|
| PYTHON | 命令行参数/标准输入计算 | 读 `input.txt`，写 `output.txt` 或打印结果；提供学生可见样例文件 | 实现 `def f(...)`，由 harness import 调用 |
| UNIX | 脚本接收参数+stdin 的文本处理 | 读文件做统计/过滤，产出文件 | 在 `main.sh` 中实现 shell 函数，harness `source` 后调用 |

规则：
1. 复用并改造现有存量题（`import_python_bank.py`、`unix-homework-10.json` 等），不足的格子新增；每题含题干、IO 规范说明、参考答案、测试用例（含边界与错误用例）。
2. 文件型必须带学生可见样例附件 + 隐藏评测文件；至少 1 题用例级不同输入。
3. 种子脚本幂等（重复运行不产生重复题，按稳定键识别），经 API 创建（不直写库），不碰业务数据；样例题打 `sample` 标签便于识别和筛选。
4. 新建一份"样例作业"，每格各选 1 题，供线上验收学生账号完整走流程。
5. 不用 mock 冒充：参考答案必须真实通过 B2 评测。

## 8. P0 核查结论（由 CODING AI 填写）

> 核查时间：2026-11-18 | 核查方式：源码/迁移静态核查 + 线上只读 API 巡检（`https://api.91idc.me`）

1. **`b3.questions` 实际列**：
   - 代码模型（`B3/src/autograder_b3/infra/models.py`）、全部 Alembic 迁移（`0001`–`0013`）及线上 API 返回字段中均**不存在** `type` 或 `io_type` 列。
   - 注：未直连线上 PostgreSQL 读取 `information_schema.columns`，P2 迁移脚本将先检查 `b3.questions` 是否已存在 `type` 列，若存在则复用，否则新增 `io_type` 列（默认按 D1 新增 `io_type`）。
2. **存量题目与版本 `kind` 分布（线上共 54 题，全部已有版本，0 异常）**：
   - 全部 54 题均无跨版本 `adapter_kind` 冲突（`Version kind mismatches = 0`），无缺版本或非法 `kind` 题目（`ANOMALIES = 0`）。
   - 分布明细：
     - 课程 `07`（`Unix操作系统作业`，`a5e34f88-d39d-4574-bd21-a5d3b5adbed8`）：`bash` + `stdin` = **20 题**
     - 课程 `08`（`Python编程与实践作业`，`b4d17eb6-90bf-4f08-87f4-92e6ff05a6fe`）：`python` + `stdin` = **20 题**
     - 历史/非活跃课程 `483e8ceb-33f8-48c8-94be-a1a0b8d714da`：`bash` + `stdin` = 10 题，`python` + `stdin` = 2 题
     - 历史/非活跃课程 `6e0ae4e0-4292-40cd-b11f-eebe45fc0087`：`bash` + `file` = 1 题，`python` + `stdin` = 1 题
3. **课程与语言对应**：
   - 线上活跃课程 `07`（Unix）对应 `bash`，`08`（Python）对应 `python`，与 D6 矩阵完全一致。
   - 历史课程 `483e8ceb-...` 下同时存在 `python` 和 `bash` 存量题，因此 P2 迁移回填只按版本 `adapter_manifest->>'kind'` 回填 `io_type`，不在迁移期因课程语言矩阵拒绝存量数据；矩阵校验仅作用于新建/改版/发布。
4. **`ssh_session` / `email_delivery` 现状**：
   - `execution.mode` 与 `adapter.kind` 正交，存量 54 题均属于 `stdin`（53）或 `file`（1），均可直接按 `adapter.kind` 回填。
5. **Fixture 与用例级文件现状**：
   - `b3.fixtures` 仅支持版本级 `(question_version_id, path, mount)`，无 `student_visible` 字段，也无 `test_case_id` 用例级关联。
   - `evaluation-package.schema.json` 中 `adapter.files` 为版本级，`tests[]` 仅含 `file_checks`，无用例级输入文件 `files`。
   - **结论**：需要在 P1 契约中为 fixture 增加 `student_visible`（默认 `false`），并为 `tests[]` 增加可选用例级 `files`，在 P3/P4 实现用例级存储与沙箱物化。
6. **需老师裁定的问题**：
   - R3（存量异常题）：本次线上巡检 **0 条异常题**（全部 54 题均有版本且 `kind` ∈ `{stdin, file}`，无跨版本冲突）。若本地/其他测试库存在无版本或非法 `kind` 题目，迁移脚本将输出异常清单并中止，交人工处理，不静默默认。



