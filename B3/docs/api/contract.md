# B3 API 契约（Contract）

**契约权威**：`../../../MAIN/contracts/`（本地集成时，B3 与 MAIN 同级）；版本与摘要见根 [contracts.lock](../../contracts.lock)。**本文件不抄字段表**——字段/请求体/响应体一律打开契约文件。

状态（2026-10-24，契约 0.18.0）：下表**全部端点已实现**并有真实 HTTP 契约测试。此前的"三个未成文操作"（题干图片上传/受控读取、fixture 上传）已经写进 `b3.yaml`，本轮把最后一条也从 `internal + serviceToken` 改成教师 bearer JWT。

## 我提供的端点

| 方法 | 路径 | 用途 | 认证 | 作用域 | 契约状态 |
|---|---|---|---|---|---|
| GET | `/api/v1/questions` | 公开题目列表（公开 DTO，无受保护内容）；`tags` 参数按**全部满足**过滤 | 是 | 按课程作用域过滤；学生只见已发布 | 已成文；`tags` 由 CHG-0013 成文 |
| GET | `/api/v1/questions/coverage` | 按标签的**计数**分布（`RQ-B-3-20`）；无任何 id 列表 | 是 | 课程作用域（`course_id` 必填）；学生只统计已发布 | 已成文（CHG-0020） |
| POST | `/api/v1/questions?course_id=` | 创建题目与首个草稿版本 | 是 | 教师，课程作用域 | 已成文；`course_id` 参数**未成文** |
| GET / PATCH | `/api/v1/questions/{question_id}` | 题目详情 / 更新草稿（已发布版本原地修改 → `409`） | 是 | 教师，课程作用域 | 已成文；`as_new_version` 由 CHG-0008、`revision_note` 由 CHG-0013 成文 |
| GET | `/api/v1/questions/{question_id}/statistics` | 去标识化答题统计（消费 `evaluation.completed`） | 是 | 教师/助教，课程作用域 | 已成文 |
| GET | `/api/v1/questions/{question_id}/diagnostics` | 比较策略的**确定性**诊断（`CMP-001..004`）；不含任何期望输出 | 是 | 教师/助教，课程作用域 | 已成文（CHG-0013） |
| POST | `/api/v1/questions/{question_id}/authoring-assistance` | 出题期模型建议（`RQ-T-10`）；**只读，从不改题** | 是 | 教师/助教，课程作用域 | 已成文（CHG-0011） |
| POST | `/api/v1/questions/{question_id}/assets` | 上传题干图片（返回内容摘要；客户端声明的摘要从不被采信） | 是 | 教师；仅当前草稿版本 | 已成文；SVG 不在白名单 |
| GET | `/api/v1/questions/{question_id}/assets/{asset_id}` | 受控读取题干图片（未授权 `403`；带 `nosniff`/CSP/`ETag`） | 是 | 作用域内；学生仅已发布题目 | 已成文 |
| GET | `/api/v1/question-draft-generations/capabilities` | provider 状态、部署配置的输入上限、**调用者自己的**剩余预算 | 是 | 教师/管理员，课程写权限 | 已成文（0.18.0，AI-FLOW-01A） |
| POST | `/api/v1/question-draft-generations?course_id=` | 按教学目标生成**可编辑候选**（教师专用 DTO；不写题库） | 是 | 教师/管理员，课程写权限 | 已成文（0.18.0） |
| GET | `/api/v1/question-draft-generations/{generation_id}` | 刷新后恢复**服务端原始候选**（不是教师的未保存编辑） | 是 | 仅生成者且仍有课程写权限 | 已成文（0.18.0） |
| DELETE | `/api/v1/question-draft-generations/{generation_id}` | 丢弃未被接受的候选（清正文、留去内容化用量记录） | 是 | 仅生成者 | 已成文（0.18.0） |
| GET | `/api/v1/question-versions/{question_version_id}` | 版本详情（公开 DTO；草稿也带 `content_digest`，另带 `revision_note`/`revision_diff`） | 是 | 课程作用域 | 已成文；两个修订字段由 CHG-0013 成文 |
| POST | `/api/v1/question-versions/{id}/publish` | 发布并冻结版本（完整性 + **凭证**校验后不可变） | 是 | 教师 | 已成文 |
| POST | `/api/v1/question-versions/{id}/archive` | 归档（被评测引用时不可删，仅可归档） | 是 | 教师 | 已成文 |
| POST | `/api/v1/question-versions/{id}/fixtures` | 上传 fixture（返回 B3 计算的摘要）。**教师 bearer JWT + 草稿所属课程写权限**：浏览器可以完整创建 file 题型 | 是 | 教师；仅草稿版本 | 已成文；0.18.0 从 `internal + serviceToken` 改为教师令牌 |
| POST | `/api/v1/question-versions/{id}/attestations` | **内部**：记录"外部验证器跑过该版本参考答案"的凭证（**只接受草稿**，契约已写明） | **服务间认证** | 仅服务；仅草稿版本 | 已成文（CHG-0008；"仅草稿"由 CHG-0028 写明） |
| GET | `/api/v1/question-versions/{id}/evaluation-package` | **内部**：可执行输入（隐藏用例/适配器/比较策略/沙箱策略）。**草稿与已发布都服务**；包内**不含**参考解答（它有自己的通道） | 服务间认证 | 仅 B2 | 已成文；草稿可服务由 CHG-0028 成文 |
| GET | `/api/v1/question-versions/{id}/reference-answer` | **内部**：该版本**声明语言**的参考解答正文 + 存证口径的 `content_digest`（`CHG-0019` 的必需输入） | 服务间认证 | 仅 B2；教师/学生一律 `403` | 已成文（CHG-0023） |
| GET | `/api/v1/question-versions/{id}/learning-context` | **内部**：供 B2 生成学生学习提示的**公开题目上下文**（title/stem/language/tags/adapter_kind），独立 schema | 服务间认证 | 仅 B2 service identity；教师/学生 `403`；草稿 `409` | 已成文（0.18.0，AI-FLOW-01B） |
| GET | `/api/v1/assignments` | assignment 列表 | 是 | 课程/课序作用域 | 已成文 |
| POST | `/api/v1/assignments` | 创建 assignment（只能引用已发布版本，草稿 → `409`） | 是 | 教师 | 已成文 |

> `module.yaml` 声明四个路由前缀：`/api/v1/questions`、`/api/v1/question-versions`、
> `/api/v1/question-draft-generations`、`/api/v1/assignments`。**每个路由路径都落在其中一个前缀之下**
> （`MAIN/host/src/autograder_host/checks.py::check_route_declarations` 双向断言），因此没有引入孤立前缀。
>
> 0.18.0 的契约变化、以及它们为什么不是"新增隐藏入口"：(a) 草稿生成三个操作 + 能力查询，写的是
> **候选**而不是题目；(b) fixture 上传从服务令牌改为教师令牌——此前那条声明让浏览器在结构上无法
> 完成 file 题型，而摘要权威仍留在服务端；(c) `learning-context` 是给 B2 服务身份的**窄读**，
> 用独立 schema 排除全部受保护内容。

## 发布前置：参考答案凭证（CHG-0008）

`publish` 现在要求存在一条 `content_digest` 与**服务端重算值**一致的 `reference_attestations` 记录：

| 情形 | 响应 |
|---|---|
| 无匹配凭证 | `422` `reference-attestation-missing`，detail 给出补救动作（跑验证器 → POST 到 `/attestations`） |
| 上报摘要与服务端重算不符 | `409` `attestation-mismatch` |
| 上报的用例数与版本实际用例数不符 | `409` `attestation-mismatch` |
| 版本非草稿 | `409` `invalid-state-transition`（内容已冻结，补记凭证不改变学生会看到什么） |
| 内容被编辑 | 无需失效逻辑：`content_digest` 变了，旧凭证自然不再匹配 → 回到第一行 |

`B3` **不执行任何东西**：执行方是外部验证器（`MAIN/scripts/verify_question_bank.py`），B3 只记录与强制。
上报的摘要**只被交叉核对**，从不被信任——与 `CHG-B3-0002` 的"客户端声明的摘要不被信任"是同一条原则。
没有覆盖开关、没有"跳过验证"、没有兼容路径。

凭证写入需要**服务认证**，因为记录它必须读参考答案与隐藏用例来重算摘要（内部角色）。

## 公开 DTO 的字段存在性（最高危约束）

- 公开响应（`QuestionPublic` / `QuestionVersionPublic` / `QuestionStatistics` / `AssignmentPublic` / `ReferenceAttestationPublic` / 两个上传结果）**永不含**参考答案、隐藏用例、expected output、fixture 内容、学生身份或内部存储路径。
- 出站唯一出口是 `api/serializers.py: serialize_public`：按 DTO 字段做白名单投影，并对任何受保护键名**抛错**（不静默丢弃）。
- 端点的实现形状（`assignment` 响应体在契约中只有"Page of assignments"一句，未给 schema）由 B3 定义为最小有用形状（`AssignmentPublic`），同样受白名单与"键缺失"测试约束。
- `ReferenceAttestationPublic` 只有事实、计数与摘要；**没有学生可见面**——"这道题验证过"不是学生需要的信息。

## 消费健康面（B3 内部，无新端点）

`b3.consumption_counters` 记录每次投递结果，`plugin.health()` 把状态并入既有的 `HealthReport.detail`
（B4 的 `/api/v1/modules/{module}/health` 原样透出），另有 `python -m autograder_b3.consumer_cli --health`
输出 JSON 并以退出码表达状态。四态：`idle`（从未收到事件，**也是"消费者没在跑"的样子**）、
`consuming`、`degraded`（有拒收或未处理行）、`unknown`（计数读不出来，**不报成健康**）。

## 我消费的端点 / 事件

- 事件：`autograder.v1.evaluation.completed`（B2 发布，去标识化，见 [events.md](events.md)）。
- 依赖 B4：`course_id`/`section_id` 作用域校验与 JWT/JWKS 本地验签（`GET /api/v1/auth/jwks`；非对称，仅存公钥）。**不读 `b4` schema**。
- 依赖 B2：无（B3 不拉 B2 的 HTTP；只消费事件）。

## 权威链接

- OpenAPI：`../../../MAIN/contracts/openapi/b3.yaml`
- package schema：`../../../MAIN/contracts/schemas/evaluation-package.schema.json`
- 事件 envelope：`../../../MAIN/contracts/schemas/event-envelope.schema.json`
- 事件定义：`../../../MAIN/contracts/asyncapi/events.yaml`
- 锁：`contracts.lock`（版本与摘要以该文件为准，本文件不复述字面值）

## 出题期模型建议（CHG-0011 / `RQ-T-10`）

`POST /api/v1/questions/{question_id}/authoring-assistance` 对教师**正在编写**的草稿给建议。
它不是判分能力，也不是题目正确性证明——`ADR-0009` 决策 3 的三条边界在 B3 侧这样落地：

| 边界 | 落地方式 |
|---|---|
| **只给建议，从不改题** | `domain/assistance.py` 里没有任何写入路径；响应 DTO 没有可"应用"的字段；契约也刻意没有 `apply` 参数。接受建议意味着教师自己走 `updateQuestionDraft`，审计里留下的是人的动作（测试 `test_authoring_assistance_writes_nothing` 断言调用前后版本对象逐字段相同） |
| **建议不进入学生可见面** | 出站 payload 由 `assistance.build_provider_payload` 组装并**当场按受保护词汇断言**（`assert_no_protected_keys`），响应模型 `AuthoringAssistance` 里没有任何能承载参考答案/隐藏用例/期望输出的字段。测试断言 payload 键集合精确等于 12 个公开项，且参考答案正文、隐藏期望值、隐藏用例名都不出现在序列化结果里 |
| **默认关闭** | 未配置 provider 时 `409 authoring-assistance-unavailable`，detail **指名**要设哪两个环境变量；不产生任何出网调用。授权检查**先于**配置检查，因此"这个部署有没有配 provider"不会被无权限调用者从状态码读出来 |

- `topics` 是**枚举**（`quality` / `comparison_policy` / `wording` / `difficulty` / `coverage`），
  因此 prompt 面保持可枚举：调用方无法要求模型"随便再做点别的"。
- provider 回包同样受约束：只接受被请求过的 topic；消息里出现受保护字段名、或出现换行/控制字符，**整份响应被拒**
  （`502 authoring-provider-failed`），而不是把自由文本块贴上 "advisory" 标签递到教师屏幕上。
- `suggestions[].confidence` 目前只有 `advisory`，它与本轮同时交付的**确定性**比较策略诊断
  （`CMP-001..004`，见下）刻意分开：诊断是纯规则、可复现，模型建议不是。两者不混在同一个列表里——
  这正是该字段存在的理由。
- 记录的限制（`ADR-0009` 后果段）：模型仍可能在措辞上引用它被展示过的内容。结构性事实是
  "B3 从不发送受保护内容、从不存储模型输出、从不把建议变成写入"，不是"模型保证不复述"。

## 结构化草稿生成（AI-FLOW-01A / 契约 0.18.0）

`RQ-T-10` 现在有两轨，刻意是**两个端点、两种响应类型**，不混成一段聊天记录：

| 操作 | 何时用 | 产出 |
|---|---|---|
| `POST /api/v1/question-draft-generations?course_id=` | 新建题目：教师有教学目标但还没有题面/用例 | **可编辑候选字段**（`QuestionDraftGeneration.proposal`），教师专用 DTO |
| `POST /api/v1/questions/{id}/authoring-assistance` | 编辑已保存的草稿 | **建议**（`confidence: advisory`），没有可应用的字段 |

**状态与状态码**（`status` 是唯一的结论位，`unavailable` 也是它的一部分）：

| status | 含义 | 存储 |
|---|---|---|
| `ready` | 解析干净、可进入编辑器 | 保存私有候选（`expires_at`） |
| `needs_review` | 可读但有字段问题，`warnings[].field` 指向控件 | 同上 |
| `failed` | 没能形成编辑器（例如没有题干） | 保存记录但 `proposal = NULL`，教师刷新后仍能看到原因 |
| `unavailable` | 本部署没配 provider，**没有出网、没有计费** | 不落记录（没有可恢复的东西），`generation_id`/`provenance`/`expires_at` 全为 `null` |

`unavailable` 用 200 表达而不是 `503`：它是一句部署事实，需要和"生成失败"分开显示在按钮旁；
**配置了** provider 却失败才是 `502`（`draft-generation-provider-failed`），预算用尽才是 `429`。

**边界（每条都能被测试断言）**：

1. 只写候选，不写题库：生成路径不创建 question/question_version/test_case。
2. 候选是教师专用：`proposal` 可含参考答案与候选隐藏用例，因此走独立 schema（不进 `dto/public.py`），
   读取要求**同一个 actor** 且仍持有该课程写权限；另一个账号拿同一个 id 得到 `403`。
3. 保存即接受：`createQuestion?generation_id=` 在**同一事务**里回填 `accepted_question_version_id`
   并把候选正文置空；回滚则两者都不发生。同一份候选不能产生第二个版本（`409`）。
4. 过期即拒绝：`expires_at` 之后读取返回 `404` 并删除该行；用量账本（`ai_generation_usage`）保留，
   它**没有**能放 prompt 或响应的列。
5. 预算是 actor 级、从账本计数：换课程不能重置；到限返回 `429` 并在 `Retry-After` 给出窗口释放时间，
   **不调用 provider**。
6. 上限来自部署配置：契约**不写死**字符数，客户端从
   `GET /api/v1/question-draft-generations/capabilities` 读取并在控件旁显示。
7. 生成不是发布：候选仍需 CMP 诊断、B2 参考解答验证与教师显式发布。

## 学习提示所需的题目上下文（AI-FLOW-01B / 契约 0.18.0）

`GET /api/v1/question-versions/{id}/learning-context` 是 B2 生成学生学习提示时**唯一**可读的题目内容：
`title`、`stem_markdown`、`language`、`tags`、`adapter_kind`。

- 独立 schema（不是从 `QuestionVersionPublic` 过滤），因此参考答案、用例、期望输出、夹具、教师备注
  与学生数据都在结构上不可能出现。
- 仅服务身份（`serviceToken`）；教师与学生调用得到 `403`，无凭据 `401`，且都在读取版本**之前**判定。
- 只服务已发布（或已归档）的版本；草稿返回 `409`——评测运行只会引用已发布版本，这里不为未发布内容
  开第二条读路径。

## 执行输入（evaluation package）：草稿也可取（CHG-0028）

`GET /api/v1/question-versions/{id}/evaluation-package` 是 B2 的**执行输入**通道：用例、适配器、比较策略、沙箱策略。
**包内不含参考解答**（它在 `reference-answer` 通道上，`CHG-0023`）。

| 版本状态 | 编译输入 | `created_at` | 与记录的 `package_digest` 比对 |
|---|---|---|---|
| 已发布 | 冻结内容 | `published_at` | **是**：不一致即 `409`（发布后受保护内容被改动） |
| **草稿** | 该版本**当前**内容 | 内容自身时间戳（行的 `updated_at`；**不是** `now()`，**也不是** `published_at`——实现根本不读这一列，草稿带不带它都不影响：DB 的 CHECK 只要求"非草稿必须有"，并不禁止草稿带它） | **否**：草稿没有已记录的摘要可比。响应仍给出 `package_digest`，它是"本次编译出的内容的摘要"，诚实但尚未被绑定；`publish` 才是绑定它的那一刻 |

**为什么草稿必须可取**：`publish` 要求先有存证，而存证**只接受草稿**，所以验证必须发生在发布之前——
"可执行输入只有已发布版本才有"会让教师永远卡在发布（`CHG-0019`/`ADR-0015` 的目的因此无法达成）。
这不是放宽鉴权：仍要 `serviceToken`，教师与学生照旧 `403`。

**`409` 在这条路径上只有一个含义**：该版本**没有可编译的内容**（例如一个用例都没有，或声明的 fixture 还没有字节）。
它**不再**表示"这是草稿"——那个读法正是让发布路径不可用的原因。

草稿包**不落库、不缓存**：不写摘要，因此不会变成第二份事实源。

## 参考解答交付通道（CHG-0023）

`GET /api/v1/question-versions/{id}/reference-answer` 是 `CHG-0019` 的**必需输入**：没有它，
"B2 执行参考解答并回报存证"在契约层面不成立（evaluation package 刻意不含参考解答，公开 DTO 禁止回显）。

| 事项 | 落地方式 |
|---|---|
| **鉴权** | `security: [{serviceToken: []}]`，与 `getEvaluationPackage` 同一个 scheme：**没有新增凭据、没有新增信任关系**。`require_service()` 是处理函数的第一件事，发生在读任何内容之前；无令牌 `401`、任何用户令牌（教师/助教/学生/跨课程教师）`403`。教师接触参考解答的路径是**出题写路径**，不是这里 |
| **返回哪一份** | 该版本**自己声明的语言**（`version.language`）对应的那一份。一个版本可以同时存有多种语言的答案（每次写入 upsert 自己那一种），所以"按声明语言取"与"取唯一一行"是两件事，测试用**两份答案的夹具**把二者区分开 |
| **只返回五件** | `question_version_id` / `content_digest` / `language` / `adapter_kind` / `reference_answer`。隐藏用例期望输出、用例内容、fixture 内容**刻意不含**——它们已有各自通道（package、`adapter.files` 摘要），这个端点只补缺的那一件 |
| **`content_digest` 的口径** | 取版本行上已存的值，即 `QuestionVersionPublic.content_digest` 与 `CHG-0008` 存证绑定的**同一个值**：同一个版本不会出现两个摘要，且答案与内容在构造上绑定。真实读数为三者逐字相等（[Phase 09](../phases/active/phase-09-reference-answer-channel.md) §4） |
| **`404` vs `409`** | 版本不存在 → `404`；版本存在但**无法验证** → `409`（slug `reference-answer-missing`），**不用 `404` 掩盖**。两种情形共用 `409`、按 `detail` 区分：①该版本没有声明语言的参考解答（或存的是空白串）——detail 点名"参考解答 + 语言"；②该版本还没有 `content_digest`——detail 点名**缺的是摘要**及其对存证的意义，**不断言原因**（旧文案结尾是"a version needs at least one test case first"，而真实库里**每一条**能触发它的行都不缺用例：4 个 NULL 摘要的草稿、0 个零用例版本——见 [Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md) §4.3）。**已由 [`CHG-0026`](../../../MAIN/docs/contracts/changes/CHG-0026-reference-answer-conflict-semantics.md) 裁决**（排入 `0.16.0`）：保留 `409` 覆盖两种情形并逐条点名，理由是情形 ② 下验证必然无法完成，返回 `200` 只会把失败推后到取包/存证那一步且更难归因；`409` 的语义正是"与资源当前状态冲突"。B3 **无代码改动**（措辞修正与断言见 Phase 10），两条 `detail` 的可区分性由 `tests/test_questions_api.py` 的**镜像排除断言**钉住（见该 CHG §8.1/§8.2） |
| **响应为什么不经 `serialize_public`** | 公开出口会对键名 `reference_answer` **直接抛错**（`assert_no_protected_keys`）——那正是保护学生的那道闸门。响应由内部 DTO 自己的 `to_wire()` 产出（与 evaluation package 同一做法），并且它**不能**放在 `dto/wire.py`：那个模块被逐字典键扫描，因为 evaluation package 绝不允许长出 `reference_answer` 键 |
| **日志与事件** | 答案正文不进日志（键名 `reference_answer` 本就在脱敏名单里，但实现**根本不传**它）；日志行只有版本、语言、适配器与摘要（摘要按既有脱敏规则显示为 `[redacted]`）。这个端点**不发布任何事件** |

## 未成文的实现（不得被其他模块依赖）

**当前为空。** `CHG-0013`（2026-09-18）补齐了此前两项"已实现、无出口"能力的契约面，它们现在是
契约路径，条目随之删除——本表只描述**当前**缺口，不保留"曾经缺过什么"的兼容性叙述；
历史记录在 [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md) 与
[Phase 04](../phases/active/phase-04-quality-surfaces.md)。

仍有两条**不属于本表**的历史差异，均已在 `CHG-0016` 关闭：

1. `PATCH /questions/{id}` 的响应体：契约判错并已改为 `QuestionPublic`（契约 0.11.0），B3 实现未动；
2. `ComparisonDiagnostics.severity`：B3 曾误报为"契约笔误"，实际是**实现偏离契约**，已改实现。`CHG-0016` §5 记录了驳回。

`POST /questions` 的 `course_id` 仍是 query 参数而不是 body 字段（契约请求体无该字段，属 `CHG-B3-0002`）。

## 质量出口（CHG-0013）

三项能力在 Phase 03 已实现但无出口，本轮接上。各自的**语义**不在契约里，在这里：

| 出口 | 语义与边界 |
|---|---|
| `GET /questions/{id}/diagnostics` | 规则、非模型建议：同一输入永远同一结论，因此可作为题目质量记录的一部分（模型建议在另一个端点且带 `confidence: advisory`）。诊断对象是**声明**——规则不执行任何东西。响应只含 `rule_id`/`severity`/`message`/`ordinal`，**从不回显期望输出**，所以可以安全展示在受保护版本旁边。诊断的版本 = 题目详情展示的版本（有已发布版本则诊断它，否则诊断最新草稿），否则结论无法据以行动。空 `findings` 的含义是"检查过、没发现问题"：端点在同一次请求里同步跑完全部规则，没有缓存也没有异步任务，所以不存在"报告还没算"的状态；取不到报告时返回 `401`/`403`/`404`，不返回 `200` |
| `revision_note`（写） | 只在 `as_new_version=true` 时被接受。**带了却没有这个 flag → `422 revision-note-misuse`**，包括显式传 `null`（"省略"与"显式 null"在 wire 上可区分，混为一谈是猜测）。创建题目时提交同样 `422`（首版不是修订）。空串或超 500 字符 → `422`。字段存在性由 HTTP 层从解析后的 body（`model_fields_set`）读出，因此领域层不必猜 |
| `revision_note` / `revision_diff`（读） | 首版两个字段都是 `null`。有作者手写理由时用作者的，否则用从差异派生的兜底（`supersedes <id>: stem, case weights`），保证两个版本不会只留给读者一个谜。`revision_diff` 的键集合**恰好**是契约 `RevisionDiff` 的 11 个键，全是计数、长度、摘要或存在性标志——`reference_answer_digest_*` 是摘要，参考答案正文永不出现。`null` 的 before 值/flag 表示"未比较"（首版），与 `false`（比较后相同）是两句不同的话 |
| `GET /questions?tags=a&tags=b` | **全部满足**，不是任一满足：覆盖度视图要能收窄（"既是进程调度又是页面置换"），想要并集的调用方重复请求即可。实现是 `jsonb @>`（走 `ix_questions_tags` GIN 索引），不是在应用层过滤已加载的行。非法标签与写路径用**同一个校验器**并返回 `422`——不校验会让过滤条件匹配不到任何行，看起来像"没有题目带这个标签" |
| `GET /questions`（默认集） | **默认 `published_only=true`（契约声明）**，且**没有** `status` 参数，因此教师刚建完的草稿不在默认列表里。详见下面"待决问题" |
| `PATCH /questions/{id}` 的响应体 | **已裁决（`CHG-0016`，契约 0.11.0）**：契约原先声明 `QuestionVersionPublic`，实现返回 `QuestionPublic`。MAIN 判定契约是错的一侧（`QuestionDraftInput` 能改 `title`/`tags`，而 `QuestionVersionPublic` 没有这两个字段且 `additionalProperties: false`），契约已改为 `QuestionPublic`。**B3 未改代码** |

## 草稿写入的省略语义（CHG-0017）

`QuestionDraftInput` 的两个受保护字段是**可选**的，且省略表示"我什么都没说"，不是"清空"：

| 字段 | 省略 | 提供 | 显式 `null` / 空 |
|---|---|---|---|
| `cases` | 该版本**保留**原有用例集；分支新版本时把上一版的内容**重新签发**成新版本自己的行（同内容、新 id） | **整体替换**（先删该 suite 的行再插入，不叠加） | `null` 与 `[]` 都 `422`（契约声明的是 `type: array, minItems: 1`）；`[]` 的拒绝发生在写任何东西之前 |
| `reference_answer` | **保留**已存答案 | upsert 覆盖 | 空白串同样**保留**（空白不是删除手段；契约未声明该字段可为 `null`，实现按"省略"处理，与本模块既有行为一致） |

- **判断"是否提供"由传输层做**：`"cases" in payload.model_fields_set` / `revision_note` 同一套做法，
  领域层不猜。领域层另有一条守卫：声明省略却带着用例、或声明提供却是空集，都**报错而不是挑一种解释**
  （两种解释都是关于受保护内容的静默错答）。
- **不存在"清空全部用例"的路径**：省略保留、提供则至少一条，两者合起来无法把用例集降到 0。
  唯一没有用例的状态来自**首次写入时省略**——那是起始状态，且 `publish` 会拒绝它
  （版本没有用例就无法评测），所以学生永远看不到它。
- **没有移动的边界**：`title` / `stem_markdown` / `language` / `adapter` 在契约里**仍然必填**，
  因此 `{"tags": ["unix"]}` 单独一个字段依旧 `422`——但拒绝信息里不再出现 `body.cases`。
  给这四个字段也加省略语义是**另一个契约变更**（`language`/`adapter` 还参与内容摘要），
  不在 `CHG-0017` 范围内。
- 动机写在契约自己的描述里：让调用方"改标题或改标签时不必把隐藏用例与参考答案读出来再发回去"。
  落地前后的真实宿主读数见 [Phase 08](../phases/active/phase-08-omission-preserves.md) §4。

### 待决问题：`adapter` 写得进去、读不出来

`QuestionDraftInput` 要求 `adapter`，而公开的 `QuestionVersionPublic` 只暴露 `adapter_kind` /
`max_files` / `max_total_bytes`：`args`/`stdin`/`expect_exit_code`/`comparison`/`files`/`callable`
**没有任何读出口**。于是纯 API 客户端无法为一次"只改标签"的编辑重建请求体
（Phase 08 的复测只能从 `b3.question_versions.adapter_manifest` 直读才凑出合法请求体）。
`CHG-0017` 只处理两个受保护字段，因此本模块**未**自行扩展契约；若要真正让 B1 之外的客户端也能改标签，
需要 MAIN 决策一个新的读出口。

### 待决问题：`GET /questions` 的默认集不含教师自己的草稿

这不是 B3 的实现缺陷——`published_only: {default: true}` 由契约声明（`CHG-0013` 没有动它，新增的 `tags` 参数是正交的，已用同一次运行的对照实验排除）。但它是一个**静默错答**形状，值得写清楚：

- 教师 `POST` 建题 → `201`；`GET /questions/{id}` → `200`；**`GET /questions` → `items: []`**。
  响应里没有任何信号说明是默认值把草稿滤掉了。
- `?status=draft` **不是合法请求形状**（该操作没有 `status` 参数），FastAPI 静默忽略未声明参数，调用方拿到的仍是默认集——这正是"加了 `status=draft` 也一样"看起来无法解释的原因。
- 只有显式 `published_only=false` 能看到草稿；学生对它无能为力（非教职员角色在领域层被强制为已发布）。
  而**详情**路径对同一资源是角色感知的（教师 `200`、学生 `403`）：两条路径规则不一致是有意的，但列表侧的默认值不利于出题流程。
- **建议（需 MAIN 裁决）**：要么把契约默认值改为 `false` 并让学生侧继续由领域层强制已发布，要么引入与详情一致的角色规则。B3 **不**在实现里静默改默认值。

当前行为由 `tests/test_questions_api.py::test_a_freshly_created_draft_is_invisible_to_the_default_list`
（钉住"默认看不到、`published_only=false` 看得到、`status=draft` 被忽略"三件事）与
`::test_the_draft_list_default_is_the_contract_default`（钉住教师与学生默认集相同、学生无法用参数绕过）固定。
| `ComparisonDiagnostics.findings[].severity` | **已驳回一处 B3 的误报（`CHG-0016` §5）**：B3 曾报告契约 description 把第三个取值写成 `note`。契约里不存在这句话——所有 `severity` 枚举都是 `[error, warning, info]`。真实经过是**实现偏离契约**（`Severity` 多出一个未声明的 `note`），已改实现对齐 |

## 标签覆盖度（CHG-0020）

`GET /api/v1/questions/coverage?course_id=&published_only=` 回答"这个课程的题库分布成什么样"。语义与边界：

| 事项 | 落地方式 |
|---|---|
| **只有计数** | 响应只有 `course_id` / `total_questions` / `tagged_questions` / `untagged_questions` / `tags[{tag, question_count}]`。**刻意没有** `question_id` 列表：带上 id 它就从"分布"变成"逐题翻查题库的旁路"。测试从四条独立判据断言这一点（键集合按契约文件核对、嵌套项键集合、无 `*_id` 键、刚创建的 `question_id` 与任何 UUID 取值都不出现在原文里） |
| **恒等式** | 可校验的是 `tagged_questions + untagged_questions == total_questions`。`sum(tags[].question_count)` **不是** `total_questions`：一题最多带 20 个标签，逐标签计数**按设计重叠**。`untagged_questions` 单独给出，于是"没有标签"读起来是*未分类*而不是一个空响应 |
| **`question_count >= 1`** | 没有题目的标签**不出现**在列表里，而不是出现且为 0 —— 由 `GROUP BY` 结构性保证（只产出存在的分组） |
| **排序** | `question_count` 降序、同数按 `tag` 升序，在 SQL 里定死，是确定性而不只是"排过序" |
| **默认 `published_only=true`** | 与 `listQuestions` 同一默认、同一理由：忘了传参数必须少看见。非教职员调用方**无论如何都被强制为已发布**，与列表路径同一个判定实现（`domain/catalog.py:_read_scope`） |
| **路由顺序** | `/api/v1/questions/coverage` 是 `/questions/{question_id}` 的**同级字面段**，声明在参数化路由之前；被吞掉的表现是把 `coverage` 当 `question_id` 解析后返回校验错误，而不是聚合体。由结构断言（`router.routes` 的声明顺序）与行为断言（该路径确实走聚合、兄弟路由仍 404）双重固定 |
| **聚合代价** | 两条语句：合计走 `count(*) FILTER`，逐标签走 `jsonb_array_elements_text` 展开后一次 `GROUP BY`。**不建预聚合表**（会引入需要维护的第二事实源）。注意 `ix_questions_tags` 这个 GIN 索引**不服务**集合返回函数展开——它服务的是 `listQuestions?tags=` 的 `jsonb @>` 谓词；实测 plan 与理由见 [Phase 07](../phases/active/phase-07-question-tag-coverage.md) §3 |

## 错误与关联约定（不可协商）

- 错误格式：RFC 9457 Problem Details，`Content-Type: application/problem+json`，**只有** `type`/`title`/`status`/`detail`/`instance` 五个键（`additionalProperties: false`）；offending 字段名只进日志，不进响应体。
- 时间：UTC RFC 3339（含时区偏移，毫秒精度）；出站由 `api/serializers.to_wire` 统一格式化。
- 标识：UUID v4，由数据 owner 生成（`../../../MAIN/docs/api/identifiers.md`）。
- 请求关联：接受并传播 `traceparent` 与 `X-Request-ID`；缺失时入口生成并回写响应头，贯穿日志 → 数据库 → 事件（作为 `correlation_id`）。
- 作用域语义：未认证 `401`；无权限/跨课程 `403`（不用 `404` 掩盖）；有权限但资源不存在 `404`；学生访问未发布内容 `403`。
- **参数校验先于鉴权（实测，如实记录）**：缺失必填 query 参数的请求由 FastAPI 的签名校验拒绝，得到
  `422`，此时端点函数体（取 token 的那一步）尚未运行——因此"无令牌 **且** 缺 `course_id`"得到的是
  `422` 而不是 `401`。契约对 `GET /questions/coverage` 只声明了这两种状态码，未规定先后；B3 未为单个
  端点自造"鉴权优先"的特殊顺序（那会与既有 `POST /questions?course_id=` 的行为不一致）。带作用域时
  无令牌仍是 `401`，由 `test_coverage_requires_authentication_and_a_course_scope` 固定。
- 分页：keyset 游标（`CursorPage.next_cursor`），禁止 offset 分页；非法游标 → `422`。
- 数据库不可变性冲突（SQLSTATE `23001`）在异常处理器中映射为 `409`（`immutable-question-version`）。
