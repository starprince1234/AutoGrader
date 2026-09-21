# Phase 04 质量能力的出口（CHG-0013 落地，当前阶段）

## 目标与范围

上一批（[Phase 03](phase-03-quality-attestation-and-bounded-statistics.md)）实现了五项题库质量能力，
其中三项**没有可用读写面**，教师无法触达。本阶段把 MAIN 已裁定的出口接上，**不新增能力**：

| # | 能力 | 上一批的状态 | 本阶段做的事 |
|---|---|---|---|
| 1 | 比较策略诊断（`CMP-001..004`） | 规则引擎 + 单测，无端点 | 新增 `GET /api/v1/questions/{id}/diagnostics`（`ComparisonDiagnostics`） |
| 2 | 修订说明与结构化差异 | 已落库，`QuestionVersionPublic` 无字段可读 | `QuestionDraftInput.revision_note` 写路径 + 版本 DTO 的 `revision_note`/`revision_diff` |
| 3 | 标签检索 | `tags` 可写可读，**不可检索** | `GET /api/v1/questions` 新增 `tags` 查询参数（**全部满足**语义） |

契约基线：`contracts.lock` → **`0.8.0`**，摘要 `39fcfd312886d999a63af4e3e0a81b072b3ec16ed72510d39660cfe45e7a73f6`
（本轮开始即为此值；CHG-0013 由 MAIN 先行落地，B3 未改 `MAIN/contracts/**`）。

范围外：`MAIN/contracts/**`、其他模块、任何执行（B3 不执行参考答案，也不执行学生代码）。

## 输入与决策

| # | 输入 / 现象 | 决策 |
|---|---|---|
| 1 | 诊断的 severity：实现里有 `error/warning/note`，契约 `ComparisonDiagnostics.severity` 只声明 `error/warning/info` | **改实现**：第四条规则（CMP-003）的严重度值由 `note` 改为 `info`。理由：`note` 不在声明内，含该值的响应无法被契约序列化；B3 无权自行扩大枚举，而"改一个字符串常量"不改变规则的语义与判定。**这是实现偏离契约，不是契约有错**——我最初把它描述成"契约笔误"，`CHG-0016` §5 已驳回该说法（详见 [未完成 / 需 MAIN 裁决](#未完成--需-main-裁决)） |
| 2 | `revision_note` 只在 `as_new_version=true` 时有意义，但契约没有规定"带了却没这个 flag"怎么办 | **拒绝**（`422 revision-note-misuse`），不静默忽略。静默忽略等于"报告 200、却什么都没记"，正是本项目拒绝的安静行为；另外**显式传 `null` 也算传了**（`model_fields_set`），因为二者在 wire 上可区分，混为一谈是猜测 |
| 3 | 版本 DTO 上要暴露 `revision_diff`，而库里存的是旧内部形状（`diff_version`/`stem.changed`/`answer_shape`…） | **让落库的形状就是契约的形状**（`RevisionDiff` 的 11 个键），删掉旧内部结构，而不是加一层投影。两套结构 = 两个真相，投影层正是受保护内容可能漏出的位置；`revision_note` 的自动派生（无手写理由时的兜底）改为读契约形状 |
| 4 | 首个版本的 `revision_diff` 必须是 `null`；但"比较得出的差异"也可以算出来 | 首次版本**不写** `revision_diff`/`revision_note`（库中为 `NULL`，DTO 为 `null`）；`compute_revision_diff(None, current)` 仍定义为首版语义（before 与全部 flag 均为 `null`，而非 `false`——"未比较"与"比较后相同"是两句话），并有测试固定该语义 |
| 5 | 诊断要读隐藏用例的期望输出，而端点对学生可见面无定义 | 诊断端点复用**出题角色集**（`AUTHORING_ROLES`）：学生 `403`、无凭据 `401`、跨课程 `403`、不存在 `404`。响应只含规则 id/severity/说明/用例序号，**从不回显期望输出**，因此可以安全地展示在受保护版本旁边 |
| 6 | 诊断针对哪个版本 | 与题目详情展示的版本**同一条规则**：有已发布版本时诊断该版本，否则诊断最新草稿。诊断一个调用方看不到的版本，结论无法据以行动 |
| 7 | `tags` 过滤放在应用层还是 SQL | SQL：`jsonb @> requested`（GIN 索引 `ix_questions_tags` 已存在）。把 106 道题全载进内存再过滤不是"检索" |
| 8 | 查询参数里的非法标签 | 与写路径**同一个校验器**（`domain/tags.validate_tags`），非法即 `422`。不校验的话过滤条件匹配不到任何行，看起来像"没有题目带这个标签" |

## 完成条件

1. 三个出口都可通过真实 HTTP 调用，响应形状与契约声明逐字段一致。
2. `revision_diff` 的键集合**恰好**是契约声明的 11 个键，且键名与序列化结果都不出现受保护词汇。
3. `tags` 的匹配语义是"全部满足"：两标签查询的结果集合 **等于** 两次单标签查询的交集。
4. 干净题目的诊断为 `findings: []`，且与"没有检查"可区分。
5. 现存断言不因改动而放宽；套件转绿、`ruff` 干净、`alembic` head 不变。

## 验证（真实执行）

```powershell
cd B3
uv run ruff check .
uv run pytest -q
uv run alembic upgrade head
```

### 结果

```text
uv run ruff check .            → All checks passed!
uv run pytest -q               → 362 passed, 6 warnings in 37.99s
uv run alembic upgrade head    → 无新迁移（head 仍为 20260915_0003）
uv run alembic heads / current → 20260915_0003 (head)
```

套件例数变化：345 → 362（`test_questions_api.py` 66 → 80、`test_versions.py` 29 → 31、
`test_hygiene.py` 69 → 70 —— 后者是**逐文件参数化**，本轮新增 `domain/diagnostics.py` 使参数 +1，不是新用例）。

本轮**没有新增迁移**：三项能力所需的列（`questions.tags`、`question_versions.revision_note`/`revision_diff`）
在 `20260915_0003` 已存在；`revision_diff` 的形状变化只影响写入的 JSON 内容，列类型仍是 `jsonb` + 对象 CHECK。

## 三项"绿套件不能证明"的验证

### 1. `revision_diff` 键集合 = 契约声明的键集合

`tests/test_versions.py::test_revision_diff_records_what_changed_and_never_contains_protected_content`
现在**直接读 `MAIN/contracts/openapi/b3.yaml` 的 `RevisionDiff` 段**取出声明的键名集合，与库里存的 delta 键集合断言相等；
同一条断言还检查模块常量 `REVISION_DIFF_KEYS` 与契约一致；同时断言键名与序列化文本中不出现 `PROTECTED_FIELD_NAMES` 的任何词。

- 只加代码不加契约 → 失败（多键）；只加契约不加代码 → 失败（少键）。
- **实测过**：临时给 `b3.yaml` 的 `RevisionDiff` 加一个属性 `invisible_extra`，两个用例立刻失败（`Extra items in the left/right set: 'invisible_extra'`）；
  用备份还原后 hash 回到 `fa298d26…`、`git status contracts/` 无改动、用例重新通过。这一步是为了证明该门禁真的会响，而不是"看起来会响"。
- 受保护内容方面：delta 里只有计数、长度与两枚摘要（`reference_answer_digest_*`，64 位十六进制），
  题干正文、隐藏用例名、期望输出均不出现在序列化结果里（沿用 Phase 03 的探针断言，未放宽），
  另加 `not set(delta) & PROTECTED_FIELD_NAMES`。

### 2. `tags` 是"全部满足"

`tests/test_questions_api.py::test_question_list_filters_by_tags_with_all_of_semantics`：
建四道题（`processes+paging` / 仅 `processes` / 仅 `paging` / 仅 `text-tools`），
断言 `listed(tags=[processes, paging]) == listed(tags=processes) & listed(tags=paging) == {两道都带的题}`。

- 断言的是**交集等式**，不是"行数更少"：any-of 过滤、丢标签的过滤、返回空集的过滤都会让等式失败。
- 另加前提断言，使等式不可能**空洞成立**：单标签集合互不包含（`only_processes ∈ processes \ paging` 等），
  且交集严格小于任一单标签集合、也不等于并集。若夹具退化成"所有题都带两个标签"，交集会等于任一集合，
  这些前提先失败。
- 另断言单标签查询互不相等（证明过滤真的生效），以及无人持有的标签返回空页。

### 3. 空诊断 ≠ 没检查

三态在本模块是这样保证的（不是靠约定）：

| 态 | 表现 | 依据 |
|---|---|---|
| 有发现 | `200`，`findings` 非空（`CMP-001..004` 各自有 HTTP 用例） | 规则命中 |
| **检查过、没发现问题** | `200`，`findings == []` | `test_an_empty_diagnostics_list_means_checked_and_clean`（同时覆盖已发布题目与未发布草稿） |
| 没检查（现有答案？） | **不存在这个态** | 端点在同一次请求里同步读版本内容并跑完四条规则，**没有缓存、没有持久化报告、没有异步任务**——因此不存在"报告还没算"的状态。取不到报告时返回错误状态码：`401`（无凭据）/`403`（非出题角色或跨课程）/`404`（题目不存在），三者都不返回 `200` |

关键点：正因为"检查过但干净"与"未检查"不可能同时表现为 `findings: []`，
本端点**不需要**契约再提供一个 `checked_at`/`status` 字段。这一点与 `POST …/authoring-assistance` 不同——
那个端点有 `status: unavailable|ready|failed`，因为"provider 没配"是一个真实存在的运行态；诊断没有这种态。

**这里"没有三态"是结论，不是遗漏**：契约 `ComparisonDiagnostics` 只有 `question_id` 与 `findings`，
B3 没有自行加字段（那会是 V14 类缺陷），而是把"不存在第三态"变成结构性事实并写进测试。

## 附一：本轮变更的对外可见面

| 面 | 变化 | 契约状态 |
|---|---|---|
| `GET /api/v1/questions/{id}/diagnostics` | 新增端点（`ComparisonDiagnostics`） | 已成文（CHG-0013） |
| `QuestionDraftInput.revision_note` | 新增写路径；`as_new_version=true` 以外一律 `422` | 已成文（CHG-0013） |
| `QuestionVersionPublic.revision_note` / `.revision_diff` | 新增读取字段；首版为 `null` | 已成文（CHG-0013） |
| `GET /api/v1/questions?tags=` | 新增查询参数（全部满足） | 已成文（CHG-0013） |
| `ComparisonDiagnostics.findings[].severity` | 实现侧取值由 `note` 改为 `info`（**实现偏离契约**，非契约有错） | 契约 `enum` 本就是唯一权威取值；曾误报为"契约笔误"，`CHG-0016` §5 驳回 |

## 附二：改动文件

| 文件 | 改动 |
|---|---|
| `src/autograder_b3/compile/diagnostics.py` | `Severity.NOTE` → `Severity.INFO`（两处，规则目录与命中点）；docstring 记录了契约 enum 是唯一取值来源 |
| `src/autograder_b3/domain/revisions.py` | 重写：`snapshot_content`（从库里内容取可比事实）、`compute_revision_diff(previous, current)`（首版语义：before 与 flag 为 `null`）、`reference_answer_digest`、`revision_note`（派生兜底）、`REVISION_DIFF_KEYS`。删除旧的 `DELTA_KEYS`/`DIFF_VERSION` 与嵌套形状 |
| `src/autograder_b3/domain/diagnostics.py` | 新增：诊断读路径（版本选择、出题角色 + 课程作用域、受保护读、日志） |
| `src/autograder_b3/domain/questions.py` | 提交内容 → `snapshot_content`；首版与分支版本都算 diff；`revision_note` 优先作者、否则派生；`_validate_revision_note`（无 `as_new_version` / 空串 / 超长 → `422`） |
| `src/autograder_b3/domain/catalog.py` | `list_questions` 透传 `tags` |
| `src/autograder_b3/infra/repositories.py` | `list_questions` 新增 `tags`（`jsonb @>`）；`create_version` 接收调用方算好的 `revision_note`/`revision_diff`，删除 `supersedes` 与内部派生函数 |
| `src/autograder_b3/dto/internal.py` | `QuestionDraftInputModel.revision_note` |
| `src/autograder_b3/dto/public.py` | `RevisionDiffPublic`、`ComparisonFindingPublic`、`ComparisonDiagnosticsPublic`、`ComparisonSeverity`；`QuestionVersionPublic.revision_note`/`revision_diff`；两个新模型进 `PUBLIC_DTO_TYPES` |
| `src/autograder_b3/api/router.py` | 诊断端点；`tags` 查询参数（走 `validate_tags`）；创建/更新传 `revision_note_supplied` |
| `src/autograder_b3/api/serializers.py` | `version_to_public` 投影两个修订字段；`diagnostics_to_public` |
| `src/autograder_b3/errors.py` | `RevisionNoteMisuse`（`422`，slug `revision-note-misuse`） |
| `src/autograder_b3/domain/__init__.py` | 导出 `question_diagnostics` |
| `tests/test_versions.py` | 键集合断言改为**读契约文件**；新增首版不可比较、`revision_note` 三态拒绝两个用例；更新既有断言 |
| `tests/test_questions_api.py` | 诊断端点 4 例、标签检索 2 例、修订往返与拒绝 2 例；版本 DTO 键集合加两个字段 |
| `tests/test_adapters.py` | CMP-003 的 severity 断言 `note` → `info`；规则目录断言同步 |
| `docs/**`、`README.md` | 追踪表、用例目录、api/contract、acceptance、components/models/overview、schema、strategy、README 索引与本阶段记录 |

未改动：`MAIN/contracts/**`（只读取）、`contracts.lock`（已是 0.8.0）、`module.yaml`、
`alembic/versions/**`（无新迁移）、`plugin.py`。

## 未完成 / 需 MAIN 裁决

- 无 `阻塞` 项；无待裁决项。
- ~~**契约内部不一致（1 处，已上报）**~~ → **已驳回并关闭（`CHG-0016` §5）**。
  我曾报告 `b3.yaml` 的 `ComparisonDiagnostics.description` 把第三个 severity 取值称为 `note`，与
  `enum: [error, warning, info]` 矛盾。MAIN 全仓检索后确认**契约里不存在这句话**：所有 `severity`
  枚举都是 `[error, warning, info]`，该 description 根本没有列举取值。正确的结论是
  **实现偏离了契约**（代码里多出一个未声明的 `note`），已按契约改实现。
  这一条保留在此，是为了让后来者不会再去"修"一个本来就正确的声明；`CHG-0016` §5 与
  `contracts/CHANGELOG.md` 也记录了该驳回。
- ~~**`PATCH /api/v1/questions/{id}` 响应体与契约不符**~~ → **已裁决（`CHG-0016`，契约 0.11.0）**：
  MAIN 判定**契约是错的一侧**（`QuestionDraftInput` 可改 `title`/`tags`，而 `QuestionVersionPublic`
  没有这两个字段且 `additionalProperties: false`），契约已改为 `QuestionPublic`。
  **B3 无需改代码**：实现返回的一直是 `QuestionPublic`。附带发现 B1 曾把该响应当版本对象读取，
  B1 侧自行修复。
- **跨模块仍未联调**（沿用 Phase 03 的表述，不因本轮而改变）：B2 runner 端到端执行、
  NATS durable consumer / DLQ 的真实运行、B4 真实 JWKS 轮换、Linux 冷启动。
- **本轮的三个出口只做了 HTTP 契约级验证**（`TestClient` + 真实 PostgreSQL），
  **没有**在真实宿主（MAIN composition host + B1 前端 + 真实 JWT）上端到端跑过。
  证据强度：请求-响应形状、鉴权分支、SQL 过滤语义、库中键集合均已验证；
  未验证的是"宿主装载后行为一致"，因为那需要一次跨模块集成运行。
- `MAIN/contracts/**` 与 `contracts.lock` 由 MAIN 修改与重新锁定；B3 未改这两个位置。
  本轮期间契约经历 `0.8.0 → 0.10.0 → 0.11.0`（`CHG-0014`/`CHG-0015` 只动 `openapi/b2.yaml`；
  `CHG-0016` 只把 `updateQuestionDraft` 的 200 响应 `$ref` 从 `QuestionVersionPublic` 改为
  `QuestionPublic`）。B3 的实现与套件在 `0.11.0` 下重新验证通过。
