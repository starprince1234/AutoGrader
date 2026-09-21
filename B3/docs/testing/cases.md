# B3 测试用例目录（Cases）

本表是"能否证明需求被满足"的检查单。`当前是否存在` 取值：`存在` / `待建`。用例 ID 与 [../requirements/traceability.md](../requirements/traceability.md) 的"关联测试"列一致。

> 状态基线：2026-09-20（[Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md)：`CHG-0028` 草稿也可取执行输入；
> 同日早些时候 [Phase 09](../phases/active/phase-09-reference-answer-channel.md) `CHG-0023` 参考解答交付通道、
> [Phase 08](../phases/active/phase-08-omission-preserves.md) `CHG-0017` 省略即保留、
> [Phase 07](../phases/active/phase-07-question-tag-coverage.md) `CHG-0020` 标签覆盖度）。
> 全部用例已实现并**在本环境真实执行通过**（`uv run pytest` → `393 passed`），原始命令与输出见 Phase 10 §3。

## 用例目录表

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| TC-DTO-01 | 3-10 | 静态 + 单元 | — | 检查 `dto/__init__.py` 导出、`dto/public.py` 的 import 与标识符、公开 DTO 字段集合 | `dto` 包零导出；`public.py` 不 import `internal`、不含受保护标识符；公开字段 ∩ 受保护词汇 = ∅ | `存在`（`tests/test_dto_boundary.py`，8 例） |
| TC-QST-01 | 3-10 | 契约 | 一条含参考答案/隐藏用例/expected output 的已发布题目 | 学生角色调 `GET /api/v1/questions` + 详情 + 版本详情 | **自动化断言**：响应 JSON 逐字段**缺失** `reference_answer`/`hidden_cases`/`expected_output` 等（非"值为空"），并在响应原文中搜索答案正文与期望输出子串 | `存在`（`tests/test_questions_api.py`，34 例） |
| TC-QST-02 | 3-10 | 安全 | 题干图片存在且题目未发布/跨课程 | 未授权请求图片 URL | `403`；响应头 `nosniff` + CSP `sandbox` + `Content-Disposition: inline`；`Content-Type` 恒为图片类型（SVG 上传被拒） | `存在` |
| TC-QST-03 | 3-20 | 集成 | 教师 A 有课程 X，题目属 X | 用课程 Y 作用域读/改 | `403`（非 `404` 掩盖） | `存在` |
| TC-QST-04 | 3-20 | 契约（回归） | 教师 A 在同一会话里刚 `POST` 建出的草稿 | 立刻 `GET /questions`（无参数 / 带 `course_id` / 带 `status=draft` / 带 `published_only=false`），并对比学生视角 | 默认集**不含**该草稿（契约默认 `published_only=true`）；`?status=draft` 因该参数未声明而被忽略，结果与默认集相同；`published_only=false` 能看到；学生无论传什么都看不到（领域层强制已发布），而详情路径对学生 `403`。用例钉住"默认值导致教师看不到自己草稿"这一静默错答形状，并记录为**契约待决问题**（见 [../api/contract.md](../api/contract.md)），而不是当成 B3 缺陷静默改掉 | `存在`（`tests/test_questions_api.py`，2 例） |
| TC-VER-01 | 3-20 | 单元 + 集成 | 已发布版本 V1 | 原地修改 V1 题干/用例/参考答案；再绕过应用层直接 `UPDATE` | 领域层 `409` 且提示新建版本；HTTP `409`；PostgreSQL 触发器 SQLSTATE `23001` | `存在`（`tests/test_versions.py`，24 例） |
| TC-VER-02 | 3-20 | 单元 + 集成 | 缺参考答案 / 零用例 / adapter 非法 | 调 `publish` | `422`；不发事件、不生成 package | `存在` |
| TC-VER-03 | 3-20 | 契约 | 发布成功 | 观察事件流 | `question.version.published` 符合 MAIN envelope schema，payload 键集合精确 = 标识/摘要/时间，不含受保护内容 | `存在`（`tests/test_versions.py` + `tests/test_events.py`，13 例） |
| TC-VER-04 | 3-20 | 集成 | 草稿 D、已发布 V | D 入 assignment；删除 V；归档 V | D 被拒（`409`）；删 V → `409`（触发器亦拒绝）；只能归档 | `存在` |
| TC-VER-05 | 3-20 | 集成 | 已发布 V1 | `PATCH ?as_new_version=true` | 生成 V2（草稿），V1 的 `content_digest`/`package_digest`/题干不变；V2 落结构化差异与修订理由 | `存在` |
| TC-VER-06 | 3-20 | 集成 + 安全 | 草稿 D（含参考答案与隐藏用例） | 无凭证/凭证摘要不符/凭证描述别的用例数时 `publish`；记完凭证后改内容再 `publish`；检查无凭证时的 `detail` 文案 | 无凭证 `422`（`reference-attestation-missing`，detail **指向当前补救办法**：出题流程里的"验证并发布"，且**不再**点名已被 `CHG-0019` 取代的验证脚本，同时保留"编辑内容使存证失效"这句）；摘要不符 `409`；用例数不符 `409`；内容改动后旧凭证自动失效，重验后恢复；**不存在**任何跳过/覆盖开关 | `存在`（`tests/test_versions.py`、`tests/test_questions_api.py`） |
| TC-VER-07 | 3-10/20 | 集成（守卫） | 用带微秒的时钟发布一个新版本 | 发布后**重新从数据库读回**该版本，再走 `load_evaluation_package` | 取包成功且 `package_digest` 与库中记录一致。这条走的是"发布 → DB 往返 → 取包"整条路——它曾对**每个**题目都返回 409（集成库 103/103）。**它不隔离摘要规则**（亚毫秒差经毫秒渲染后消失），规则本身由 `TC-ADP-06` 把守 | `存在`（`tests/test_versions.py`） |
| TC-TAG-01 | 3-20 | 契约 | 一篇草稿题目 | 创建/更新时提交合法标签；提交大小写、空格、下划线、前导连字符、空串、超长、重复、超量、非数组 9 类非法值 | 合法标签原样往返（create + update + 公开读）；非法值 `422` 且**不被静默改写**；规则**恰好**是契约正则 + 条数，B3 不加额外主张 | `存在`（`tests/test_questions_api.py`） |
| TC-DIA-01 | 3-20/3-50 | 单元（纯函数） | 比较策略声明 + 用例期望输出 | 跑 4 条诊断规则（CRLF、非 ASCII、exact + 尾随空白、numeric_tolerance 缺容差）与一组合格输入 | 每条规则命中正确的 `case_ordinal` 与 severity；error 阻断、warning/info 不阻断；合格输入零 finding；每条规则都写明它防的是哪次真实故障 | `存在`（`tests/test_adapters.py`） |
| TC-DIA-02 | 3-20 | 契约 + 安全 | 已发布题目（期望输出含 CRLF / 非 ASCII / 尾随空白）、未发布草稿（`numeric_tolerance` 无容差） | `GET /questions/{id}/diagnostics`：教师 / 助教 / 学生 / 跨课程 / 不存在；再对一个干净题目调用 | 响应形状 `{question_id, findings[]}`，findings 键集合 = `{rule_id, severity, message, ordinal}`；severity ⊆ 契约 enum；用例级 finding 只带 ordinal（不带用例名与内容）；响应原文不含隐藏用例名、参考答案、期望输出；`401`/`403`/`404` 分支正确；**干净题目返回 `findings: []`**（检查过、没问题），与"没检查"可区分（见 Phase 04 的三态表） | `存在`（`tests/test_questions_api.py`，4 例） |
| TC-VER-07 | 3-20 | 契约 | 已发布 V1 + 一次 `PATCH ?as_new_version=true` | 提交 `revision_note`；读版本 DTO 的 `revision_note`/`revision_diff`；再对首版、对不带 `as_new_version` 的写入、对创建请求分别提交 `revision_note` | `revision_diff` 键集合**恰好** = 契约 `RevisionDiff` 的 11 键（测试直接读契约文件，代码或契约单边增键都会失败）；受保护词汇不出现在键名与序列化文本中；首版 `revision_diff` 与 `revision_note` 均为 `null`；作者手写理由优先于派生理由；无 `as_new_version` 时 `422 revision-note-misuse`（**含显式 `null`**），创建请求同样 `422` | `存在`（`tests/test_versions.py` + `tests/test_questions_api.py`） |
| TC-TAG-02 | 3-20 | 契约 | 四道已发布题目，标签分别为 `{processes,paging}` / `{processes}` / `{paging}` / `{text-tools}` | `GET /questions?tags=processes&tags=paging`，与两次单标签查询对比；再提交 6 类非法查询标签 | 两标签结果集合 **等于**两次单标签结果的**交集**（并断言单标签集合互不包含，使"更少行"、并集、空集三种错误答案都不成立）；无人持有的标签返回空页；非法标签 `422` 且不被忽略 | `存在`（`tests/test_questions_api.py`） |
| TC-TAG-03 | 3-20 | 契约 + 集成 | 三个专用作用域：`COURSE_B` 放 3 道题（2 道各带**两个**标签、1 道无标签）；`COURSE_C` 放 2 道无标签题；`COURSE_A` 放 1 道已发布 + 1 道草稿（各带一个独有标签） | `GET /questions/coverage?course_id=`：默认与 `published_only=false`；以教师（本课程/跨课程）、学生、无令牌、缺 `course_id` 分别调用；与 `GET /questions?tags=<t>&published_only=` 的返回条数逐标签比对 | **合计校验**：`tagged + untagged == total`；**并显式断言 `sum(tags[].question_count) + untagged != total`**（夹具让 `sum=4` 而 `total=3`，多标签重叠在错误实现下也会露出来）；`question_count >= 1`；跨课程 `403`（非 `404`）；学生只统计已发布（草稿标签缺席），且带 `published_only=false` 也拿不到；`published_only=false` 计入草稿且与列表可见集合一致；空标签库 `tags == []`、`untagged == total`，是读数不是报错；带作用域时无令牌 `401`、缺作用域 `422` | `存在`（`tests/test_questions_api.py`，5 例：`test_coverage_identity_...`、`test_coverage_is_forbidden_...`、`test_coverage_agrees_with_the_list_visibility_rule`、`test_a_bank_where_no_question_is_tagged_...`、`test_coverage_requires_authentication_and_a_course_scope`） |
| TC-TAG-04 | 3-20 | 安全 + 契约 | `COURSE_A` 中带独有标签的题目（题目 id 已知） | `GET /questions/coverage?published_only=false` | 四条独立判据：①顶层键集合**从契约文件 `b3.yaml` 读出**并逐字相等；②`tags[]` 项键集合 `== {tag, question_count}`；③任何键都不以 `_id` 结尾（唯一例外是回显的作用域 `course_id`）；④刚创建的 `question_id` 不出现在响应原文，且除作用域外响应中不含任何 UUID 形态取值（"分布"不得退化为可逐题翻查的旁路） | `存在`（`tests/test_questions_api.py`，1 例：`test_coverage_response_carries_counts_only`） |
| TC-TAG-05 | 3-20 | 契约（路由顺序守卫） | — | 断言 `router.routes` 中 `/api/v1/questions/coverage` 的声明位置**在** `/api/v1/questions/{question_id}` 之前；再实际请求该路径，并请求一个不存在的题目 id | 该路径返回聚合体（`course_id` 回显）而不是被当作 `question_id` 解析的校验错误；参数化兄弟路由仍正常 `404`（未被字面路由遮蔽）。同类陷阱见 B2 的 `quality-summary` | `存在`（`tests/test_questions_api.py`） |
| TC-DRF-01 | 3-20 | 契约 + 集成 | 一道含参考答案与隐藏用例的题（草稿与已发布两种起点）；另有一道**没有**用例的草稿 | 省略 `cases` 做 PATCH（就地与 `as_new_version=true` 两条路径）；提供 `cases` 做 PATCH；提交 `cases: []` 与 `cases: null`；对无用例草稿调 `publish` | 省略 → `200` 且用例集**逐字段不变**（就地：连 `case_id` 一起相等、`content_digest` 不变；分支：内容逐字段相等、新行新 id 且与旧集合不相交）；提供 → **整体替换**（旧行按 id 与按名字都不再存在）；`[]`/`null` → `422` 且**在写任何东西之前**就拒绝（版本号未推进、受保护内容不变）；无用例草稿 `content_digest is None` 且 `publish` 抛 `ProtectedContentMissing`；另断言领域层对"声明省略却带着用例/声明提供却是空集"报错而不是挑一种解释 → **不存在"清空全部用例"的路径** | `存在`（`tests/test_versions.py` 5 例 + `tests/test_questions_api.py` 2 例） |
| TC-DRF-02 | 3-20 | 契约 + 安全 | 一道已发布题目（参考答案与隐藏用例存在） | 只改标题 / 只改标签的 PATCH：请求体**不含** `cases` 与 `reference_answer`；再对同一路径提交"显式空白参考答案"；最后提交 `{"tags": [...]}` 单独一个字段 | 前两者 `200`，且参考答案行（**含 `reference_answer_id`**）与用例集逐字段不变（证明受保护内容没有经调用方往返）；空白参考答案同样保留；`{"tags": [...]}` 仍 `422`，detail 列出四个**公开必填**字段且**不再出现** `body.cases`——记录 `CHG-0017` 没有移动的边界 | `存在`（`tests/test_questions_api.py` 1 例 + `tests/test_versions.py` 1 例） |
| TC-REF-01 | 3-20 | 契约 + 集成 | 同一版本上存有两份参考解答（python + bash），版本最终声明 bash；另有已发布版本 | `serviceToken` 调 `GET /question-versions/{id}/reference-answer` | `200`；顶层键集合**从契约 `ReferenceAnswer` 段读出**并逐字相等；返回的是**声明语言那一份**（另一份的正文不出现在响应里）；`language`/`adapter_kind` 与版本 DTO 一致；`content_digest` 是 64 位十六进制；`tests`/`cases`/`fixtures`/`expected_*`/`package_digest`/`stdin` 都不在响应里，隐藏用例名与隐藏期望输出也不在响应原文 | `存在`（`tests/test_questions_api.py`，1 例） |
| TC-REF-02 | 3-20 | 安全 + 契约 | 一道**没有**参考解答的草稿（新建）；一道**有答案但没有用例**的草稿（`cases` 省略，`CHG-0017` 之后可达） | 以服务令牌 / 教师 / 助教 / 学生 / 跨课程教师 / 无令牌 / 未知令牌调用；对不存在的版本调用；对两个"无法验证"的版本调用 | **教师与学生都读不到**（`403`，且被拒响应里搜不到答案正文），无令牌与未知令牌 `401`，服务令牌 `200`（证明拒绝是授权规则而非端点故障）；不存在版本 `404`；无参考解答 `409 reference-answer-missing`（**不是 `404`**，且该版本自己的 DTO 仍 `200`，证明资源确实在）；无用例版本同样 `409` 但 detail 指明缺的是摘要/用例 | `存在`（`tests/test_questions_api.py`，5 例） |
| TC-REF-03 | 3-20 | 契约 + 安全 | 一道刚写入一份可识别答案正文的草稿 | 调该端点并检查原始日志流；另比较响应摘要与公开 DTO、与 `POST /attestations` 记录的摘要 | §8.5：三处 `content_digest` **逐字相等**，且发布**前后**相等（B2 的真实顺序是验证→存证→发布）；§8.7：答案正文**不出现**在日志流里，同时断言该事件行确实产生（不是空断言），行内只有版本/语言/适配器与**已脱敏**的摘要 | `存在`（`tests/test_questions_api.py` 1 例 + `tests/test_logging.py` 1 例） |
| TC-PKG-01 | 3-20 | 契约 + 集成 | 一道**草稿**版本（用例齐备） | `serviceToken` 调 `GET /question-versions/{id}/evaluation-package`；同一草稿取两次；再用教师令牌取一次 | `200`，包内 `question_version_id` 是该草稿、`tests` 数量与用例数一致；两次取值 `created_at` **相同**、`package_digest` **相同**；`created_at` 与该版本内容时间戳**精确相等**，且**推进应用时钟而内容不动时它不变**（`now()` 实现会红），编辑内容后它随之改变；教师令牌仍 `403`（服务草稿 ≠ 放宽鉴权） | `存在`（`tests/test_questions_api.py`，2 例） |
| TC-PKG-02 | 3-20 | 契约 + 安全（回归） | 一道**已发布**版本；一道**零用例**的草稿 | 取已发布版本的包并与库里记录的 `package_digest` 比对；用一次性触发器禁用改掉一条用例的期望输出后再取；取零用例版本的包 | 已发布 → `200` 且返回值**等于**记录的摘要；改内容后 → `409`（**证明放宽草稿没有把旧检查一起丢掉**）；零用例 → `409 package-content-missing` 且 detail 含 "no compilable content"（`409` 的新含义：没有可编译内容，不是"这是草稿"） | `存在`（`tests/test_questions_api.py`，2 例） |
| TC-PKG-03 | 3-20 | 契约 + 集成（闭环） | 一道有答案与用例的草稿 | 依次取**包**与**参考解答**（都走服务令牌）、记录存证、`publish`；再断言发布后取到的包与草稿时同一个摘要（"编辑内容使存证失效 → 发布被拒"由既有 `TC-VER-06` 覆盖） | 两件输入都 `200`；两者的 `content_digest` 与版本 DTO 口径一致；存证 `201`；`publish` `200`；发布后包摘要**不变**（草稿路径没有成为输出的第二条实现）。**未执行**：B2 真的执行参考解答那半（本环境无 nsjail） | `存在`（`tests/test_questions_api.py`，1 例） |
| TC-AI-01 | `RQ-T-10` | 契约 + 安全 | 一篇含参考答案与隐藏用例的草稿；未配置 provider / 已配置（注入测试 provider） | 以学生/无凭据/教师调用出题辅助；检查出站 payload 与响应 | 未配置 → `409` 且 detail 指名缺哪两个环境变量；学生 `403`、无凭据 `401`（**授权先于配置**）；出站 payload 键集合精确 = 12 个公开项，参考答案正文/隐藏期望值/隐藏用例名均不出现；响应无任何受保护字段；调用前后版本对象逐字段不变（**不改题**）；topic 越界或回包含换行 / 受保护词 → 整份拒绝；`disclaimers` 非空、`confidence=advisory` | `存在`（`tests/test_questions_api.py`，12 例） |
| TC-ADP-01 | 3-40 | 单元 + 契约 | stdin/argv 示例题目（bash） | 编译 package；`args` 含 shell 特殊字符 | manifest 声明 `args` 数组/比较策略/退出码；特殊字符逐字保留；package 通过 MAIN schema 校验 | `存在`（`tests/test_adapters.py`，40 例） |
| TC-ADP-02 | 3-50 | 单元 + 契约 | file I/O 示例题目（含目录树） | 编译；声明绝对路径/`..`/盘符等 10 种越界路径 | fixture 摘要绑定进 package（含 `mount=expected_output`）；越界路径被拒；声明摘要与存储内容不一致被拒 | `存在` |
| TC-ADP-03 | 3-60 | 单元 + 契约 | callable 示例题目 | 受控 import；6 种非法入口 | import 目标必须是点分标识符（拒路径/URL/`;`/空段）；`arguments_json` 必须是 JSON；package 通过契约校验 | `存在` |
| TC-ADP-04 | 3-60 | 单元（扩展点） | 三型示例 | 编译三型并比较结构；对编译器核心做 AST 断言；运行时注册新 adapter | 三型 package 顶层结构相同；核心无 kind 分支；新 adapter 由同一入口分派 | `存在` |
| TC-ADP-05 | 3-40/50/60 | 单元 | 同一内容编译两次；微改内容后再编译 | 比较 `package_digest` | 同内容同摘要；内容变化摘要变化；`created_at != published_at` 被拒 | `存在` |
| TC-ADP-06 | 3-10/20 | 单元（守卫） | 同一内容、`created_at` 相差**整 1 毫秒**的两个快照 | 编译并比较 `package_digest` | 摘要**相同**（`created_at` 是元数据、不是内容），而 `created_at` 本身仍不同。**这条守卫是被变异测试逼出来的**：第一版用亚毫秒差（`123_456` vs `123_000`），而 `to_wire()` 把时间戳渲染到毫秒，两者序列化后相同，**在旧规则下也通过**——修复前它抓不到任何东西。见 [../phases/active/phase-06-package-digest-scope.md](../phases/active/phase-06-package-digest-scope.md) §5 | `存在`（`tests/test_adapters.py`） |
| TC-STT-01 | 3-30 | 集成 | 一批去标识化事件 | 同 `event_id` 重复投递 5 次；同 `evaluation_run_id` 换 `event_id` 再投 | 计数只增加一次；重复投递被 ack 但不重复计数 | `存在`（`tests/test_statistics.py`，29 例） |
| TC-STT-02 | 3-30 | 集成 | 已消费事件流 | 清空统计表后按 `occurred_at` 重放；再**倒序**重放一次 | 统计值与重放前一致（含倒序，证明聚合可交换、无浮点累加） | `存在` |
| TC-STT-03 | 3-30 | 安全 | 无权限用户 / 学生 / 跨课程教师 | 查询统计 | 学生 `403`；跨课程 `403`；助教与教师 `200` 且响应不含身份/分数原值 | `存在`（`tests/test_auth.py`，23 例） |
| TC-STT-04 | 3-30 | 安全 | 事件 payload 含越界字段（9 种） | 消费 | 白名单拒绝、记日志、**不 ack**（进入 DLQ 路径）、不入库 | `存在` |
| TC-STT-05 | 3-30 | 集成 | 事件流 + 时间窗 | 窗口查询与全量预聚合对比；窗口跨多页 | 窗口内计数正确；`from > to` → `422`；页数 > 1 时结果与单页一致 | `存在` |
| TC-STT-06 | 3-30 | 集成（有界性） | 10 万事件量级的本地库 | 以固定 `page_size` 走窗口扫描；把上限调到低于事件数 | 每页 ≤ `page_size`；窗口内每行恰好产出一次且顺序稳定；超上限 → `422`（**不静默截断**）；实测峰值内存与耗时见 Phase 03 | `存在`（`tests/test_statistics.py` + `scripts/benchmark_statistics.py`） |
| TC-STT-07 | 3-30 | 集成（运维） | 消费器与 `b3.consumption_counters` | 空库读健康；投一条合法事件；投一条越界字段事件；重复投递同 `event_id`；DB 不可读 | `idle` / `consuming` / `degraded` / `unknown` 四态可区分；`degraded` 时 CLI `--health` 退出码 1；被拒事件（从不进 inbox）也计入 | `存在`（`tests/test_statistics.py`、`tests/test_consumer_cli.py`） |
| TC-LOG-01 | 3-99 | 单元 + 集成 | 发布/消费/统计操作 | 检查日志 | 单行 JSON 含 `producer=B3`；13 类受保护键值被 `[redacted]`；真实发布链路日志含 `request_id`/`question_version_id` 且无受保护内容；失败不静默 | `存在`（`tests/test_logging.py`，23 例） |
| TC-SEC-01 | 3-10 | 安全（数据库） | 普通题库角色连接 | 直接 `SELECT` 受保护表 | PostgreSQL `permission denied`；内部角色可读；应用连接 `is_superuser=off` | `存在`（`tests/test_migrations.py`，17 例） |
| TC-DB-01 | 3-20 | 集成 | 空库 | 会话夹具 drop `b3` 后 `alembic upgrade head`；`downgrade` 再 `upgrade` | 迁移可从零执行；head 与 `module.yaml` 一致；回滚往返成功；无跨 schema FK；无旧 scoring/evidence/report 对象 | `存在` |
| TC-HYG-01 | 全 RQ | 静态 | — | 逐 src 文件断言无 shell/子进程/动态执行/跨模块 import；命名规范；无 TODO/NotImplementedError | 全部通过（61 例） | `存在`（`tests/test_hygiene.py`） |
| TC-PLG-01 | （骨架） | 单元 | SDK 可导入 | `create_module()` | `descriptor.module=="B3"`、`b3-questions` 在 `provides`、`b2-evaluation` 不在；路由前缀与 `module.yaml` 一致；每条路由都落在已声明前缀下 | `存在`（`tests/test_plugin.py`，8 例） |
| TC-AUTH-01 | 3-10/3-20 | 安全 | 本地生成的 RSA 密钥对 | 真实 RS256 签名与篡改、过期、错误 iss/aud、未知 kid、非 RS256、畸形 token、畸形 scope | 合法 token 通过；其余全部 `401`；scope 解析出课程/课序；JWKS 抓取被缓存 | `存在`（`tests/test_auth.py`） |
| TC-CLI-01 | 3-30 | 集成 | 已消费事件 | `python -m autograder_b3.consumer_cli --rebuild` | 退出码 0 并重建统计；未配置 NATS 时**明确报错**而非静默降级 | `存在`（`tests/test_consumer_cli.py`，4 例） |
| TC-CLI-02 | 3-30 | 集成（运维） | 空库 / 有被拒事件 | `python -m autograder_b3.consumer_cli --health` | 空库 → JSON `state=idle`、退出码 0；有拒收 → `state=degraded`、退出码 1 | `存在` |

## 覆盖自检

- 每条 RQ 至少一个用例：3-10→TC-DTO-01/QST-01/02/SEC-01；3-20→TC-QST-03/VER-01~07/PKG-01~03/TAG-01~05/DRF-01~02/REF-01~03/DIA-01~02/DB-01；3-30→TC-STT-01~07/CLI-01/02；3-40→TC-ADP-01；3-50→TC-ADP-02；3-60→TC-ADP-03/04；3-99→TC-LOG-01。
- 任务书 §7 DoD 关键项全部有对应用例：公开响应不含答案/隐藏用例（TC-QST-01）、版本不可变（TC-VER-01）、三类 adapter 各一例（TC-ADP-01/02/03）、事件去重（TC-STT-01）、统计可重放（TC-STT-02）、发布前置自证（TC-VER-06）。
- 契约受限项与 B2 联调项在 [../requirements/acceptance.md](../requirements/acceptance.md) 显式标注，**不以通过用例冒充**。`RQ-B-3-30` 验收 5 的性能部分**不达标**，见 Phase 03 实测。
- TC-DIA-02/VER-07/TAG-02 是 [Phase 04](../phases/active/phase-04-quality-surfaces.md) 为 `CHG-0013` 三个新出口补的用例：
  它们证明的是**出口存在且语义正确**，不是"能力可用"——后者还需要真实宿主联调（见下）。
- TC-TAG-03/04/05 是 [Phase 07](../phases/active/phase-07-question-tag-coverage.md) 为 `CHG-0020`
  的 `GET /questions/coverage` 补的用例，逐条覆盖该变更 §8 的五条验证与 §2.1 的路由顺序要求。
- TC-DRF-01/02 是 [Phase 08](../phases/active/phase-08-omission-preserves.md) 为 `CHG-0017`
  "省略即保留"补的用例，逐条覆盖该变更 §5 的五条验证，并额外固定两件事：
  "无用例草稿不可发布"（新的可达状态仍被门禁挡住）与"只带 `tags` 的 PATCH 仍然 `422`"（该 CHG 未移动的边界）。
- TC-REF-01/02/03 是 [Phase 09](../phases/active/phase-09-reference-answer-channel.md) 为 `CHG-0023`
  的参考解答交付通道补的用例，逐条覆盖该变更 §8 的七条验证。除 pytest 外，本轮还在
  **真实 composition host + 真实 JWT（教师与学生各一个真实会话）+ 真实题库**上取了读数（Phase 09 §4）。
- TC-PKG-01/02/03 是 [Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md) 为 `CHG-0028`
  "草稿也能取到执行输入"补的用例，逐条覆盖该变更 §9 的六条（§9.6 由既有 `TC-VER-06` 覆盖），
  其中 §9.3 的"改内容 → `409`"用**一次性触发器禁用**制造摘要失配——它证明放宽草稿**没有**把已发布路径的检查一起丢掉。
  本轮另把 `TC-VER-06` 的拒绝文案断言改向"指向当前补救办法"（旧文案把教师指向已被取代的验证脚本）。

## 当前测试实现（如实）

例数取 `uv run pytest --collect-only -q` 的实际收集结果：

| 测试文件 | 例数 | 层次 |
|---|---|---|
| `tests/test_adapters.py` | 47 | 单元 + 契约（对 MAIN schema 实时校验） |
| `tests/test_auth.py` | 23 | 安全（真实 Ed25519） |
| `tests/test_consumer_cli.py` | 4 | 集成（`--rebuild`、`--health`） |
| `tests/test_dto_boundary.py` | 8 | 静态 + 单元 |
| `tests/test_events.py` | 13 | 契约 + 集成 |
| `tests/test_hygiene.py` | 70 | 静态（逐文件参数化） |
| `tests/test_logging.py` | 24 | 单元 + 集成 |
| `tests/test_migrations.py` | 17 | 集成（真实 PostgreSQL） |
| `tests/test_plugin.py` | 8 | 单元 |
| `tests/test_questions_api.py` | 102 | 契约 + 安全（真实 HTTP；Phase 07 +7 覆盖度、Phase 08 +3 省略语义、Phase 09 +6 参考解答通道、Phase 10 +4 草稿取包与闭环） |
| `tests/test_statistics.py` | 39 | 集成（含有界性与健康面） |
| `tests/test_versions.py` | 38 | 单元 + 集成（含凭证、修订闭环与省略语义 6 例） |
| **合计** | **393** | `uv run pytest` → `393 passed, 6 warnings in 53.93s`；`uv run ruff check .` → `All checks passed!` |

**未覆盖（如实）**：

- NATS JetStream 的真实 durable consumer 行为（`NatsEvaluationConsumer.run` / `NatsEventPublisher`）在本环境无 NATS 服务，仅有其消息处理逻辑（`handle_message`）与拒收语义的测试。
- B4 的真实 JWKS 端点（测试用本地密钥对 + 注入的 JWKS 抓取替换）。
- B2 runner 的实际执行（端到端证据链，见 acceptance 的 ⚠ B2 联调项）。
- **真实宿主上的端到端调用**：`CHG-0013` 的三个新出口（诊断端点、修订字段、标签检索）与 `CHG-0020`
  的标签覆盖度只在 `TestClient` + 真实 PostgreSQL 上验证过形状与语义，**没有**经 composition host 复测。
  `CHG-0017`（Phase 08 §4.1）与 `CHG-0023`（Phase 09 §4）已在真实 composition host + 真实 JWT + 真实题库上取过读数；
  仍未做的跨模块部分是 **B1 客户端**（浏览器侧）与 **B2 对这些端点的消费**。
- 真实题库（106 道题 / 103 个已发布版本）上的覆盖度读数与性能：本环境只有测试库，夹具是自造的
  2~3 道题量级；覆盖度的查询计划也只在个位数行数下实测过（见 Phase 07 §5）。
- 10 万事件量级的**窗口路径性能**不作为 pytest 用例（会拖慢套件）；它是
  `scripts/benchmark_statistics.py` 的实测，结果记在 [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)，且**不达标**。
