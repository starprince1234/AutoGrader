# B3 验收标准（Acceptance）

逐 RQ 的可验证验收（比 MAIN 目录更贴近实现）。判定依据是**自动化测试**，不是审阅结论。

> 状态基线：2026-09-20（[Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md) 增补 3-20-14（`CHG-0028` 草稿也可取执行输入）与两处拒绝文案修正；
> 同日早些时候 [Phase 09](../phases/active/phase-09-reference-answer-channel.md) 增补 3-20-13（`CHG-0023` 参考解答交付通道）、
> [Phase 08](../phases/active/phase-08-omission-preserves.md) 增补 3-20-12（`CHG-0017` 省略即保留）、
> [Phase 07](../phases/active/phase-07-question-tag-coverage.md) 增补 3-20-11（`CHG-0020` 标签覆盖度）
> 并更正 3-20-7 下"筛选与覆盖度是一件事"的记录错误；Phase 04 增补 3-20-8~10；Phase 03 曾增补 3-20-6、3-20-7、3-30-6、T-10-1~3）。
> 每条给出"验证方式"（测试函数）与"判定结果"；真实命令与原始输出见
> [`../phases/active/phase-10-draft-package-and-current-pointers.md`](../phases/active/phase-10-draft-package-and-current-pointers.md)（此前各阶段见其同名记录）。
>
> 标注 **⚠ B2 联调** 的条目：B3 侧能力已完成并有测试，但"端到端证据链"需要 B2 runner 执行，属跨模块联调项，本仓库内无法单独验证——**不写成已通过**。

通用前置（每项验收前均需满足）：

- 已按 [../runbooks/local-development.md](../runbooks/local-development.md) 拉起 PostgreSQL 并执行迁移（`uv run alembic upgrade head`；测试套件的会话夹具每次运行都会 drop `b3` 后从零迁移）。
- 已获得 B4 签发的测试 JWT（含角色与课程作用域）；契约夹具可用（测试用真实 RS256 签名 + 仅替换 JWKS 端点抓取）。
- 权威字段/结构一律以 `../../../MAIN/contracts/**` 为准，不在本文件复述字段表。

---

## RQ-B-3-10 题目包含题干、参考答案、测试用例

**验收 3-10-1（公开 DTO 不含受保护字段，自动化断言）**

- Given：题库中存在一条已发布题目，含参考答案、隐藏用例、expected output。
- When：调用 `GET /api/v1/questions` 与题目详情接口（学生角色）。
- Then：响应 JSON 中**不存在** `reference_answer`、`hidden_cases`、`expected_output` 等字段（逐字段断言缺失，而非人工审阅）。
- 失败判定：任一公开响应出现上述任一字段，或断言改为"值为空"而非"字段缺失"，判失败。
- **验证**：`tests/test_questions_api.py::test_list_questions_has_no_protected_field`、`::test_question_detail_has_no_protected_field`、`::test_question_version_dto_is_public_only`、`tests/test_dto_boundary.py::test_public_models_share_no_field_with_the_protected_vocabulary`。断言方式：递归收集响应全部键 → 与受保护键集合求交集为空；并把参考答案正文与隐藏期望值作为**子串**在响应原文里搜索。
- **结果**：通过。

**验收 3-10-2（内部 package 接口受控）**

- Given：一条已发布题目版本存在。
- When：以 B2 服务间凭据调用 `GET /api/v1/question-versions/{id}/evaluation-package`。
- Then：返回完整用例（含隐藏用例与比较规则）；无服务间凭据或学生凭据调用返回 `401`/`403`。
- 失败判定：内部接口能响应学生凭据，或响应泄露参考答案文本。
- **验证**：`tests/test_questions_api.py::test_evaluation_package_requires_service_credentials`（teacher/student/assistant 三种 JWT → `403`；无凭据 → `401`）、`::test_evaluation_package_is_served_to_services_and_carries_hidden_cases`（服务凭据 → `200`，隐藏用例带 `expected_stdout`；同时断言响应不含 `reference_answer`，参考答案正文不出现在响应原文）。
- **结果**：通过。参考答案**不进 package**——冻结 schema 无该字段，这既是契约事实也是本项失败判定的要求。

**验收 3-10-3（受保护内容独立权限）**

- Given：数据库层对参考答案与隐藏用例施加独立权限。
- When：以"普通题库查询"身份（无受保护内容权限，即 `autograder_b3_app`）直接读取。
- Then：无法读到参考答案 / 隐藏用例 / expected output。
- 失败判定：普通查询路径可读到任一受保护内容。
- **验证**：`tests/test_migrations.py::test_app_role_cannot_read_protected_tables`（对 `reference_answers`/`test_suites`/`test_cases`/`blobs`/`fixtures` 逐个 `SELECT` → PostgreSQL `permission denied`）、`::test_internal_role_can_read_protected_tables`、`::test_app_role_cannot_write_the_question_bank`、`::test_no_superuser_connection_is_used_by_the_application`。
- **结果**：通过（真实 PostgreSQL 上的权限拒绝，不是应用层判断）。

**验收 3-10-4（图片受控访问）**

- Given：题干图片资源存在且题目未发布或无权访问。
- When：未授权用户直接请求图片 URL。
- Then：返回 `403`；响应头禁止 `text/html` 伪装与脚本执行。
- 失败判定：越权可获取图片，或响应可被当作 HTML 执行。
- **验证**：`tests/test_questions_api.py::test_asset_requires_authorization`（跨课程教师 → `403`）、`::test_unpublished_asset_is_not_served_to_students`（学生请求未发布题目图片 → `403`）、`::test_published_asset_is_served_with_hardening_headers`（`image/png` + `X-Content-Type-Options: nosniff` + CSP `sandbox` + `Content-Disposition: inline` + `ETag:<sha256>`）、`::test_asset_content_type_allowlist_refuses_svg`（SVG 被拒：可在同源执行脚本）、`::test_asset_bytes_must_match_the_declared_type`（魔数校验）。
- **结果**：通过。
- 备注：图片上传/读取端点未在 `b3.yaml` 成文 → [CHG-B3-0002](../contracts/changes/CHG-B3-0002-asset-and-fixture-endpoints.md)。

---

## RQ-B-3-20 题库维护，按课程管理题目

**验收 3-20-1（课程作用域）**

- Given：教师 A 拥有课程 X，题目 Q 属于课程 X。
- When：教师 A 用课程 Y 的 `course_id` 读取/修改 Q，或跨课程调用 assignment 接口。
- Then：返回 `403`（不返回 `404` 掩盖）。
- 失败判定：跨课程操作成功，或以 `404` 掩盖越权。
- **验证**：`tests/test_questions_api.py::test_cross_course_read_is_403_not_404`、`::test_create_question_in_another_course_is_forbidden`、`tests/test_auth.py::test_cross_course_operations_are_403_not_404`、`tests/test_versions.py::test_assignment_referencing_another_course_is_not_found`（引用了**别课程的题目版本**时按"该资源不属于本课程"处理 → `404`，因为调用方对本课程有权限，这与"跨课程探测"不同）。
- **结果**：通过。

**验收 3-20-2（已发布版本不可变）**

- Given：题目版本 V1 已发布。
- When：尝试原地修改 V1 的题干/用例/参考答案。
- Then：返回 `409`；任何内容变更必须创建新版本 V2。
- 失败判定：V1 内容被原地改写，或修改被静默接受。
- **验证**：`tests/test_versions.py::test_editing_a_published_version_in_place_is_refused`（领域层 `ImmutableVersionConflict` 且提示 `as_new_version=true`）、`test_questions_api.py::test_draft_edit_then_publish_then_conflict`（HTTP `409`，`type` 以 `/immutable-question-version` 结尾；随后 `as_new_version=true` 成功建 V2 且 V1 摘要不变）、`test_versions.py::test_postgres_refuses_an_in_place_edit_of_published_content` / `::test_postgres_refuses_new_protected_content_on_a_published_version`（绕过应用层直接 `UPDATE` → SQLSTATE `23001`）。
- **结果**：通过（应用层 + 数据库触发器双重拦截）。

**验收 3-20-3（发布前完整性校验）**

- Given：一题缺题干 / 零用例 / 缺参考答案 / adapter 声明非法（任一）。
- When：调用 `publish`。
- Then：返回校验失败（`422`/`409`），不产生发布事件，不生成 package。
- 失败判定：不完整题目被发布。
- **验证**：`tests/test_versions.py::test_publish_without_a_reference_answer_is_refused`、`::test_publish_without_any_case_is_refused`（直接清空用例以证明发布门禁独立于写入端）、`::test_publish_with_an_invalid_manifest_is_refused`、`tests/test_questions_api.py::test_publish_a_draft_without_a_reference_answer_is_422`、`tests/test_questions_api.py::test_stem_with_executable_markup_is_rejected`（4 例参数化）。
- **结果**：通过；失败时 `b3.event_outbox` 无对应行（`test_versions.py` 断言只对成功发布计数）。
- 备注：**零用例**在 HTTP 写路径上已被契约的 `minItems: 1` 挡住，因此该分支用领域层构造（防御纵深）。

**验收 3-20-6（发布前置参考答案自证，CHG-0008 新增）**

- Given：一题内容完整（题干、≥1 用例、参考答案、合法 adapter）。
- When：直接 `publish`；或先上报一条与内容不符的凭证；或上报后编辑内容再发布。
- Then：无匹配凭证 → `422`（detail 指出补救动作）；摘要不符 → `409`；内容改动后旧凭证自动失效；**不存在**任何跳过/覆盖开关。
- 失败判定：无凭证的题目被发布，或声明摘要被当作权威值接受。
- **验证**：`tests/test_versions.py::test_publish_without_an_attestation_for_the_current_content_is_refused`、`::test_attestation_of_other_content_does_not_unlock_publish`、`::test_editing_content_invalidates_the_stored_attestation`、`tests/test_questions_api.py::test_publish_without_an_attestation_is_422_and_names_the_fix`、`::test_attestation_for_a_different_digest_is_refused`、`::test_editing_the_draft_invalidates_the_attestation`、`::test_record_reference_attestation_requires_a_service_token`（teacher/student → `403`，无凭据 → `401`）、`::test_attestation_response_carries_no_protected_content`（键集合精确断言 + 参考答案正文与隐藏用例名不出现在响应原文）、`::test_attestation_is_only_accepted_for_a_draft`、`::test_attestation_case_count_must_describe_this_version`。
- **结果**：通过。B3 **不执行任何东西**；执行方是外部验证器，B3 只记录与强制，且上报的摘要只被**交叉核对**（与 fixture 摘要绑定同一条原则）。
- 备注：已有 103 个已发布版本没有凭证——它们是在本门禁生效前发布的，契约也未要求回溯补录；**新发布一律要求**，没有"从某个版本号起生效"的静默豁免。

**验收 3-20-7（标签写路径，CHG-0008 新增）**

- Given：一篇草稿题目。
- When：创建/更新时提交标签；提交大小写、空格、下划线、前导连字符、空串、超长、重复、超量、非数组 9 类非法值。
- Then：合法标签在 create/update/公开读三处原样往返；非法值 `422`，**不被静默改写**。
- 失败判定：非法标签被接受或被"修正"成别的值。
- **验证**：`tests/test_questions_api.py::test_tags_round_trip_through_create_and_update`、`::test_question_without_tags_reports_an_empty_list`、`::test_invalid_tags_are_rejected_not_rewritten`（9 例参数化）、`::test_tag_validation_is_exactly_the_declared_pattern`。
- **结果**：通过。规则**恰好**是契约的正则与条数（`^[a-z0-9][a-z0-9-]*$`、≤40 字符、≤20 项、唯一）；B3 **不引入受控词表**，也不把非 ASCII 主题名当成错误之外的任何东西——"规范化"只等于"符合契约声明的形态"。
- ~~**未做（需契约）**：按标签筛选与覆盖度视图~~ → **已做，但分两次**：按标签**筛选**由 `CHG-0013` 新增 `tags` 查询参数（见验收 3-20-9）；**覆盖度视图**当时并未随之落地，由 `CHG-0020` 新增端点补齐（见验收 3-20-11）。此前本行把两件事写成一件事，是记录错误。

**验收 3-20-8（比较策略诊断出口，CHG-0013 新增）**

- Given：一条题目，其已声明比较策略或期望输出命中 `CMP-001..004` 中的若干条；另有若干题完全不命中。
- When：教师/助教调用 `GET /api/v1/questions/{id}/diagnostics`；学生、无凭据、跨课程教师分别调用。
- Then：`200` 且响应形状为 `{question_id, findings[]}`，每项键集合**精确**为 `{rule_id, severity, message, ordinal}`；`severity` ⊆ 契约 enum；用例级 finding 只带 `ordinal`（不带用例名/内容）；响应原文不含隐藏用例名、参考答案正文或期望输出；学生 `403`、无凭据 `401`、跨课程 `403`、不存在 `404`；**完全干净的题目返回 `findings: []`**。
- 失败判定：响应出现任何期望输出片段；`severity` 出现契约未声明的取值；干净题目与"未检查"无法区分（例如把未命中写成错误状态码，或返回一个"未知"标记）。
- **验证**：`tests/test_questions_api.py::test_diagnostics_are_reachable_and_carry_no_expected_output`、`::test_diagnostics_report_the_content_rules_with_declared_severities`（CMP-001/002/003 各命中且 severity 为 `error`/`warning`/`info`）、`::test_diagnostics_report_a_tolerance_that_was_never_declared`（CMP-004，在草稿上诊断）、`::test_diagnostics_require_the_authoring_role_and_course_scope`、`::test_an_empty_diagnostics_list_means_checked_and_clean`、`tests/test_adapters.py`（规则纯函数 5 例）。
- **结果**：通过。"空列表 ≠ 未检查"的依据是结构性的：端点在同一次请求里同步读完版本内容并跑完全部规则，没有缓存也没有异步任务，因此不存在"报告尚未计算"的状态；取不到报告时返回 `401`/`403`/`404` 而不是 `200` + 空列表。
- **未做**：真实宿主（MAIN composition host + 真实 JWT + B1 客户端）上的端到端调用。本项证据是 HTTP 契约级。

**验收 3-20-9（标签检索：全部满足，CHG-0013 新增）**

- Given：四道已发布题目，标签分别为 `{processes, paging}`、`{processes}`、`{paging}`、`{text-tools}`。
- When：用两个标签查询 `GET /api/v1/questions?tags=processes&tags=paging`，并与两次单标签查询比较；再提交 6 类非法查询标签。
- Then：两标签结果集合**等于**两次单标签结果的**交集**（且单标签集合互不包含，因此"更少行"、并集、空集三种错误答案都无法通过该断言）；无人持有的标签返回空页；非法标签 `422`，不被忽略。
- 失败判定：语义变成任一满足（结果等于并集）；或非法标签被忽略而返回未过滤的页（看起来像"没有题目带这个标签"）。
- **验证**：`tests/test_questions_api.py::test_question_list_filters_by_tags_with_all_of_semantics`、`::test_invalid_query_tags_are_rejected_not_ignored`（6 例参数化）。
- **结果**：通过。实现是 SQL 层的 `jsonb @>`（走 `ix_questions_tags` GIN 索引），不是在应用层过滤已加载的行。

**验收 3-20-10（修订说明与结构化差异，CHG-0013）**

- Given：已发布版本 V1；调用 `PATCH /questions/{id}?as_new_version=true` 生成 V2；另有一个首版题目。
- When：提交 `revision_note` 并读取版本 DTO；再分别对首版、对不带 `as_new_version` 的写入（含显式 `null`）、对创建请求提交 `revision_note`。
- Then：V2 的 `revision_note` 是作者手写的那句；`revision_diff` 的键集合**恰好**是契约 `RevisionDiff` 的 11 个键，且键名与序列化文本都不含受保护词汇；首版两个字段均为 `null`；无 `as_new_version` → `422 revision-note-misuse`（显式 `null` 同样）；创建请求 → `422`。
- 失败判定：`revision_note` 被接受却未落库（静默丢弃）；`revision_diff` 出现契约未声明的键或任何受保护内容；首版返回一个"全是 false"的对象（把"未比较"说成"比较后相同"）。
- **验证**：`tests/test_versions.py::test_revision_diff_records_what_changed_and_never_contains_protected_content`（键集合断言**直接读 `MAIN/contracts/openapi/b3.yaml` 的 `RevisionDiff` 段**）、`::test_revision_diff_on_the_first_version_is_not_comparable_rather_than_unchanged`、`::test_a_revision_note_can_only_be_recorded_by_superseding_a_version`、`tests/test_questions_api.py::test_revision_note_and_diff_round_trip_through_http`、`::test_a_revision_note_without_as_new_version_is_refused`。
- **结果**：通过。该键集合断言同时约束两侧：只改代码会多键，只改契约会少键，两种都失败（已实测：临时给契约加一个属性后该用例立刻失败，恢复后通过）。

**验收 3-20-11（标签覆盖度：只有计数，CHG-0020 新增）**

- Given：一个课程里有两道各带**两个**标签的题、一道无标签题（`COURSE_B`，会话内无其他测试写入）；`COURSE_A` 里有一道已发布题与一道草稿题各带独有标签；`COURSE_C` 里有两道无标签题。
- When：以教师、学生、跨课程教师身份调用 `GET /api/v1/questions/coverage?course_id=`（默认与 `published_only=false`），并与 `GET /api/v1/questions?tags=<t>` 的返回条数逐标签比对。
- Then：`tagged_questions + untagged_questions == total_questions`；`sum(tags[].question_count) + untagged_questions != total_questions`（多标签导致逐标签计数重叠，**显式断言该式不成立**）；`question_count >= 1`，没有题目的标签不出现；跨课程 `403`（非 `404`）；学生只统计已发布，且带 `published_only=false` 也拿不到草稿；`published_only=false` 的逐标签计数与列表端点可见集合一致；无标签的题库返回 `tags: []` 且 `untagged == total`（读数，不是报错）。
- 失败判定：响应出现任何 `question_id` / 学号（键或取值）；把 `sum(tags) + untagged == total` 当成恒等式；`question_count` 出现 0；空标签库被当成错误或返回 `404`；覆盖度与列表在可见性上分歧。
- **验证**：`tests/test_questions_api.py::test_coverage_identity_is_tagged_plus_untagged_and_the_tag_sum_is_larger`、`::test_coverage_is_forbidden_across_courses_and_students_only_count_published`、`::test_coverage_agrees_with_the_list_visibility_rule`、`::test_coverage_response_carries_counts_only`（顶层键集合**从契约文件读出**、嵌套键集合、无 `*_id`、`question_id` 与任何 UUID 取值都不在响应原文里）、`::test_a_bank_where_no_question_is_tagged_reports_untagged_not_an_error`、`::test_coverage_route_precedes_the_question_path_parameter`、`::test_coverage_requires_authentication_and_a_course_scope`。
- **结果**：通过。作用域与可见性**与 `listQuestions` 共用同一个解析函数**（`domain/catalog.py:_read_scope`），不是两份各自维护的判定。范围与未验证项见 [Phase 07](../phases/active/phase-07-question-tag-coverage.md) §5：真实题库（106 题）上的读数与规模性能**未测**。

**验收 3-20-12（草稿写入的省略语义：省略即保留，CHG-0017 新增）**

- Given：一道含参考答案与隐藏用例的题（草稿与已发布两种起点）；另有一道**首次写入时就省略 `cases`** 的草稿。
- When：PATCH 省略 `cases`（就地编辑与 `as_new_version=true` 分支两条路径）；PATCH 提供 `cases`；PATCH 提交 `cases: []` 与 `cases: null`；对无用例草稿调 `publish`；再提交"只改标题/只改标签"（两个受保护字段都不在请求体里）与"显式空白参考答案"。
- Then：省略 → `200`，用例集**逐字段不变**（就地编辑连 `case_id` 一起相等且 `content_digest` 不变；分支版本内容逐字段相等、自己持有新签发的行）；提供 → **整体替换**（旧行按 id 与按名字都不再存在）；`[]` 与 `null` → `422` 且**写任何东西之前**就拒绝；只用例草稿 `content_digest is None` 且 `publish` 抛 `ProtectedContentMissing`；只改标题/标签时参考答案行（含 id）与用例集逐字段不变，空白参考答案同样保留；`{"tags": [...]}` 单独一个字段仍 `422`，但 detail 里**不再出现** `body.cases`。
- 失败判定：省略被当成"清空"（用例或参考答案消失）；提供被当成"追加"；出现任何把用例集降到 0 的写路径；无用例的版本可被发布；"是否提供"在领域层靠值猜测而不是由传输层声明。
- **验证**：`tests/test_versions.py::test_omitting_cases_keeps_the_stored_case_set_unchanged`、`::test_supplying_cases_replaces_the_whole_set_rather_than_adding_to_it`、`::test_a_title_only_edit_leaves_the_reference_answer_row_untouched`、`::test_a_branched_version_that_omits_cases_records_no_case_change`、`::test_a_version_created_without_cases_cannot_be_published`、`::test_declaring_cases_omitted_while_sending_them_is_refused`、`tests/test_questions_api.py::test_a_correction_that_omits_both_protected_fields_is_accepted`、`::test_null_and_empty_case_sets_are_refused_without_writing_anything`、`::test_a_body_that_omits_the_required_public_fields_is_still_refused`。
- **结果**：通过。另在真实 HTTP + 真实 JWT + 真实题库上取了修复前后读数（`422 body.cases` → `200`，
  分支版本的 `revision_diff` 报用例集与参考答案摘要均未变）：[Phase 08](../phases/active/phase-08-omission-preserves.md) §4。
  **composition host 上的复测已补做**（新建草稿题目：PATCH `200`，`content_digest` 逐字不变 ⇒ 用例与参考答案都还在，
  `tagged` +1），读数见 Phase 08 §4.1 与 [Phase 09](../phases/active/phase-09-reference-answer-channel.md) §4.1。

**验收 3-20-13（参考解答交付通道，CHG-0023 新增）**

- Given：一道已发布版本（`b3.reference_answers` 里存有它声明语言的答案）；同一版本上另存有**另一种语言**的答案；一道没有参考解答的草稿；一道有答案但**没有用例**的草稿。
- When：以 `serviceToken`、教师、助教、学生、跨课程教师、无令牌、未知令牌分别调用 `GET /api/v1/question-versions/{id}/reference-answer`；再对不存在的版本与上述两个"无法验证"的版本调用。
- Then：服务令牌 `200` 且键集合恰好是契约 `ReferenceAnswer` 的五个键；返回的是**声明语言**那一份（另一种语言的正文不出现）；`language`/`adapter_kind` 与版本 DTO 一致；`content_digest` 与公开 DTO、与 `CHG-0008` 存证记录的摘要**逐字相等**（发布前后都相等）；教师与学生一律 `403`、无令牌与未知令牌 `401`；版本不存在 `404`；版本存在但无法验证 `409`（不用 `404` 掩盖，且该版本自身 DTO 仍 `200`）；两种"无法验证"的情形**由 `detail` 区分**——①无该语言的参考解答 ②有答案但没有 `content_digest`（[`CHG-0026`](../../../MAIN/docs/contracts/changes/CHG-0026-reference-answer-conflict-semantics.md) §8.2 要求可区分，两条文案互为镜像断言）；情形 ② 的文案只点名**缺的是摘要**，**不断言原因**（旧文案的"a version needs at least one test case first"在真实库上不成立：4 个 NULL 摘要的草稿里没有一个是零用例——Phase 10 §4.3）；响应正文不出现隐藏用例期望输出、用例内容或 fixture 内容；答案正文不出现在日志流里。
- 失败判定：任何用户令牌（尤其教师）读到答案；`404` 与 `409` 被混为一谈；两种 `409` 的 `detail` 无法区分（"点名"只做了一半）；响应多带任何受保护内容；摘要与存证口径不一致；日志出现答案正文。
- **验证**：`tests/test_questions_api.py::test_reference_answer_is_served_to_services_for_the_declared_language`、`::test_reference_answer_is_never_readable_by_a_user_token`、`::test_a_version_without_a_reference_answer_is_409_not_404`、`::test_an_unknown_version_has_no_reference_answer_to_serve`、`::test_a_version_with_no_test_cases_cannot_supply_its_answer`、`::test_the_reference_answer_digest_is_the_one_the_attestation_binds`、`tests/test_logging.py::test_serving_the_reference_answer_does_not_log_it`。
- **结果**：通过。**真实 composition host + 真实 JWT（教师 `t.zhang`、学生 `s.li`）+ 真实题库**上的读数见 [Phase 09](../phases/active/phase-09-reference-answer-channel.md) §4：服务 `200`/教师 `403`/学生 `403`/无令牌 `401`/未知版本 `404`/无答案版本 `409`，摘要与版本 DTO 逐字相等（答案正文不打印）。
- **未做**：B2 侧对该端点的消费与 `CHG-0019` 的端到端验证（属 B2）。

---

**验收 3-20-14（草稿也能取到执行输入，CHG-0028 新增）**

- Given：一道用例齐备的**草稿**版本；一道**已发布**版本；一道**零用例**的草稿。
- When：以 `serviceToken` 取三者的 `GET /question-versions/{id}/evaluation-package`；对草稿连取两次并推进应用时钟；取已发布版本的包并与库里记录的 `package_digest` 比对；用一次性触发器禁用把一条用例的期望输出改掉后再取；再对草稿依次取包与参考解答、记录存证、`publish`。
- Then：草稿与已发布都 `200`；草稿包内 `question_version_id` 是该草稿；两次取值 `created_at` 与 `package_digest` **相同**，`created_at` **等于该版本内容自身的 `updated_at`**，且**推进时钟而内容不动时它不变**（`now()` 实现会红），编辑内容后它随内容改变；已发布路径的返回值**等于记录的摘要**，改内容后 `409`（**放宽草稿没有丢掉旧检查**）；零用例 → `409 package-content-missing` 且 detail 说明"没有可编译的内容"；闭环里存证 `201`、`publish` `200`、发布后包摘要与草稿时相同；教师令牌取草稿包仍 `403`。
- 失败判定：草稿仍被 `409` 拒绝（发布闭环第一步断裂）；`created_at` 用 `now()` 或 `published_at`（前者使同一草稿两次取包漂移；后者是"发布那一刻"，与草稿当前内容的时间戳无关——实现根本不读这一列）；已发布路径不再比对摘要；`409` 仍被用来表示"这是草稿"；服务草稿顺带放宽了鉴权。
- **验证**：`tests/test_questions_api.py::test_evaluation_package_for_a_draft_is_served`、`::test_a_draft_package_reports_the_content_timestamp_and_is_stable`、`::test_the_published_path_still_compares_the_recorded_package_digest`（用一次性触发器禁用制造摘要失配）、`::test_a_version_with_no_test_cases_has_no_compilable_content`、`::test_a_draft_can_be_verified_and_published_without_publishing_it_first`；§9.6（编辑内容使存证失效）由既有 `::test_editing_the_draft_invalidates_the_attestation` 覆盖。
- **结果**：通过（B3 侧）。真实 composition host 读数见 [Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md) §4：草稿包 `200`、两次取值 `created_at` 与摘要相同、`created_at` == 该版本 `updated_at`；已发布包 `200` 且等于记录摘要。
- **未做**：B2 真的执行参考解答并逐用例比对（本平台无 nsjail）；§9.3 的"改内容 → `409`"与 §9.4 的零用例 `409` 在真实库上未取（前者需篡改共享集成库，后者集成库里没有零用例版本且按 MAIN 的提示不新建探针题目），两者均由 pytest 覆盖。

---

## `RQ-T-10` 出题期模型建议（CHG-0011，本轮新增）

**验收 T-10-1（默认关闭，且授权先于配置）**

- Given：未配置 provider 的部署；一篇属于教师 A 课程 X 的草稿。
- When：教师 A / 学生 / 无凭据分别调用 `POST /api/v1/questions/{id}/authoring-assistance`。
- Then：教师 `409` 且 detail **指名**缺哪两个环境变量；学生 `403`；无凭据 `401`。
- 失败判定：无权限调用者能从状态码读出"本部署配没配 provider"，或未配置被当成 500。
- **验证**：`tests/test_questions_api.py::test_authoring_assistance_is_off_by_default_and_names_the_missing_configuration`、`::test_authoring_assistance_requires_a_write_capable_role`。
- **结果**：通过。角色检查在前、配置检查在后（这条顺序是修出来的：初版先查配置，学生拿到的是 `409` 而不是 `403`，等于把部署配置泄漏给无权限调用者）。

**验收 T-10-2（出站 payload 不含受保护内容，且是结构性事实）**

- Given：一篇含参考答案、隐藏用例与 expected output 的草稿。
- When：以注入的测试 provider 调用出题辅助。
- Then：出站 payload 键集合精确等于 12 个公开项；参考答案正文、隐藏期望值、隐藏用例名都不出现；响应无任何受保护字段。
- 失败判定：出站对象里出现任一受保护内容，或断言退化为"注释里写了不会发"。
- **验证**：`tests/test_questions_api.py::test_authoring_payload_carries_no_protected_content_and_no_content_change`（同时断言 `assistance.assert_no_protected_keys` 被实际调用）、`::test_the_prompt_surface_is_enumerable`、`::test_a_provider_response_outside_the_declared_shape_is_refused`（6 例参数化：topic 越界 / 命中受保护词 / 含换行 / 空消息 / 非列表 / 缺字段）。
- **结果**：通过。边界是**两个方向**都断言的：出去的对象按受保护词汇当场检查，回来的自由文本块整份拒绝。

**验收 T-10-3（建议不改题）**

- Given：一篇草稿。
- When：调用出题辅助。
- Then：版本对象逐字段不变。
- 失败判定：调用使草稿内容或摘要发生变化。
- **验证**：`tests/test_questions_api.py::test_authoring_assistance_writes_nothing`（调用前后 `GET /question-versions/{id}` 响应完全相同）。
- **结果**：通过。
- **未做（如实）**：`HttpAuthoringProvider` 的真实 HTTP 路径**未对任何真实模型端点发出过请求**；本环境用注入 provider 验证边界与形状。这一点不写成"端到端已验证"。

**验收 3-20-4（发布事件）**

- Given：V1 发布成功。
- When：观察事件流。
- Then：发出 `autograder.v1.question.version.published`，payload 仅含 `question_version_id` 等标识，**不含**参考答案 / 隐藏用例。
- 失败判定：事件携带受保护内容。
- **验证**：`tests/test_versions.py::test_publish_emits_an_outbox_event_without_protected_content`（payload 键集合精确断言 = 5 个标识/摘要/时间字段）、`tests/test_events.py::test_published_event_satisfies_the_envelope_contract`（用 `jsonschema` 对 MAIN 的 `event-envelope.schema.json` 校验真实事件）、`::test_published_event_carries_no_protected_content`（参考答案正文、期望输出、用例名均不出现在序列化后的 payload 中）。
- **结果**：通过。事件与业务变更在**同一事务**经 outbox 写出（ADR-0006 §5）。

**验收 3-20-5（assignment 只引用已发布版本 / 被引用不可删）**

- Given：存在草稿版本 D、已发布版本 V。
- When：把 D 加入 assignment；或删除 V。
- Then：D 引用被拒（`409`）；删除 V 返回 `409`，只能归档。
- 失败判定：草稿可入 assignment，或已发布版本被删除。
- **验证**：`tests/test_versions.py::test_assignment_accepts_only_published_versions`、`tests/test_questions_api.py::test_assignment_with_a_draft_version_is_conflict`、`tests/test_versions.py::test_archive_published_version_and_then_delete_is_refused`、`::test_postgres_refuses_deleting_a_published_version`（触发器拒绝 `DELETE`）、`::test_deleting_a_draft_is_allowed`（草稿可删）。
- **结果**：通过。
- 备注：契约未声明 `DELETE` 操作，删除由领域函数 + 数据库触发器保证；B3 对已发布版本**一律**拒绝删除（B3 无法知道 B2 是否引用过，读 `b2` 被禁止——拒绝是唯一安全解）。

---

## RQ-B-3-30 答题情况与回答次数分布统计

**验收 3-30-1（统计口径）**

- Given：注入一批去标识化 `evaluation.completed` 事件。
- When：查询题目统计。
- Then：可得到作答次数、通过率、得分分布、失败类别分布。
- 失败判定：任一统计口径缺失或计算错误。
- **验证**：`tests/test_statistics.py::test_attempt_count_pass_rate_and_distributions`（4 次作答 → `attempt_count=4`、`pass_rate=0.5`、`score_distribution={"0-19":1,"20-39":1,"60-79":1,"80-100":1}`、`failure_category_distribution={"wrong_output":1,"timeout":1}`）、`::test_pass_rate_ignores_events_without_an_outcome`、`::test_attempts_without_a_max_score_are_unbucketed_not_guessed`、`::test_explicit_score_bucket_from_the_event_wins`。
- **结果**：通过（限契约可表达口径）。
- **口径差异（已上报）**：**独立作答人数**不计算——事件禁止 `student_id`，`QuestionStatistics` 也无该字段 → [CHG-B3-0003](../contracts/changes/CHG-B3-0003-distinct-student-statistic.md)；平均分同理无处承载。

**验收 3-30-2（仅消费去标识化事件）**

- Given：代码审查 + 数据流检查。
- When：核对 B3 全部读取路径。
- Then：不存在对提交内容 / 源码 / 学生身份 / 参考答案的读取路径；消费端对事件 payload 做字段白名单校验并拒绝越界字段。
- 失败判定：存在读取学生源码的代码路径，或 payload 越界字段被接受。
- **验证**：`tests/test_hygiene.py`（61 例，逐文件断言 src 无 `subprocess`/`os.system`/`eval`/`exec`/跨模块 import；`::test_only_the_b3_schema_is_referenced` 断言无跨 schema SQL/ORM）、`tests/test_statistics.py::test_forbidden_payload_fields_are_rejected_and_not_counted`（9 例参数化：`student_id`/`source_code`/`reference_answer`/`expected_output`/`expected_stdout`/`hidden_cases`/`student_name`/`email`/`token` 全部拒收）、`::test_unknown_payload_field_is_rejected`、`::test_whitelist_and_forbidden_sets_are_disjoint`。
- **结果**：通过。隐私边界在**类型层**成立：`EvaluationCompletedEvent` 没有身份字段可读。

**验收 3-30-3（事件去重）**

- Given：同一 `event_id` 的事件重复投递 N 次。
- When：消费完成。
- Then：统计计数只增加一次（按 `event_id`/`evaluation_run_id` 唯一键去重）。
- 失败判定：重复投递导致计数重复。
- **验证**：`tests/test_statistics.py::test_duplicate_event_id_is_counted_once`（同 `event_id` 投 5 次 → `attempt_count=1`，其余 4 次为 `duplicate_event`）、`::test_same_run_with_a_new_event_id_is_still_one_attempt`（不同 `event_id`、同 `evaluation_run_id` → 第二次为 `duplicate_run`）、`tests/test_events.py::test_duplicate_message_is_acked_without_double_counting`（NATS 语义：重复消息**要 ack**，否则会无限重投）。
- **结果**：通过。

**验收 3-30-4（可重放）**

- Given：清空统计表后从事件流按序重放。
- When：重放完成。
- Then：统计值与重放前一致。
- 失败判定：两次结果不一致。
- **验证**：`tests/test_statistics.py::test_rebuild_from_the_inbox_reproduces_incremental_values`、`::test_replay_out_of_order_produces_the_same_numbers`（清表后**倒序**重放 → 数值一致，证明聚合可交换且不含浮点累加器）、`::test_accumulator_is_order_independent`、`tests/test_consumer_cli.py::test_rebuild_command_reports_the_rebuilt_totals`（运维入口 `python -m autograder_b3.consumer_cli --rebuild`）。
- **结果**：通过。

**验收 3-30-5（不暴露个人成绩）**

- Given：无权限用户。
- When：查询统计。
- Then：只能得到聚合值，无法关联到单个学生的成绩。
- 失败判定：聚合结果可反推出个人成绩。
- **验证**：`tests/test_auth.py::test_statistics_requires_a_privileged_role`（学生 → `403`；助教/教师 → `200`，响应键集合 ⊆ 契约字段）、`::test_statistics_of_another_course_is_403`、`tests/test_statistics.py::test_statistics_never_expose_identity_or_source`（响应中不含 `student_id`/`submission_id`/`score`/`evaluation_run_id`/`result_digest`）。
- **结果**：通过。分数只以**分布计数**形式存在，原始分数不落库（`b3.question_version_stats` 无分数列）。

**验收 3-30-6（10 万事件量级可用：分页 + 预聚合）**

- Given：模块本地库中 100,000 条去标识化 `evaluation.completed` 事件（100 个版本，30 天均匀分布）。
- When：分别读取（a）无时间窗的预聚合路径、（b）带 `from`/`to` 的窗口路径（keyset 分页，1000 行/页）、（c）对照：同一窗口的单条无界语句。
- Then：分页生效（峰值内存与窗口宽度解耦），预聚合路径在亚秒级；窗口路径也应"可用"。
- 失败判定：窗口查询把整窗载入内存，或在该量级下不可用。
- **验证**：`scripts/benchmark_statistics.py`（真实执行，原始输出见 [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md) 与 `phases/active/evidence/phase-03-statistics-benchmark.txt`）；分页契约本身由 `tests/test_statistics.py::test_the_window_scan_is_paged_and_bounded`、`::test_a_window_wider_than_one_page_aggregates_every_row`、`::test_window_too_large_is_a_stated_422` 断言。
- **结果**：**部分达标，如实记录**：

  | 路径 | 中位耗时 | 峰值内存 |
  |---|---|---|
  | 预聚合（无时间窗） | **0.044 s** | **0.66 MiB** |
  | 窗口（keyset 分页，1000 行/页） | **23.764 s** | **5.10 MiB** |
  | 窗口（单条无界语句 = 改动前形状） | 8.327 s | **256.59 MiB** |

  - **分页达标**：峰值内存 256.59 MiB → 5.10 MiB（**50.3×**），与窗口宽度无关。
  - **预聚合达标**：10 万事件 0.044 s / 0.66 MiB。
  - **窗口路径不达标**：23.8 s 不可用于交互式查询。诚实结论是"分页与预聚合都已实现并实测，但窗口路径在该量级下仍不可用"，因此 [traceability.md](traceability.md) 的 `RQ-B-3-30` 状态词带限定，不记无条件 `已完成`。补救方向（把窗口求和下推到 SQL 或按时间桶预聚合）不需要契约变更，属后续工作。
  - 方法学限制：`tracemalloc` 只计 Python 侧分配，因此内存差距是**下界**；基准脚本直接向 inbox 插行（绕过消费器），payload 与真实事件同形，故数字反映的是声明规模下的**合成**负载，不是生产流量。

---

## RQ-B-3-40 测试框架①：命令行接收测试数据（stdin/argv）

**验收 3-40-1（adapter manifest 能力）**

- Given：一条 stdin/argv 题目。
- When：编译 package。
- Then：adapter manifest 能声明 `language`、`args`（**数组**，非 shell 字符串）、`stdin`、stdout/stderr 比较策略、期望退出码；package 含 adapter 类型与参数摘要。
- 失败判定：参数以 shell 字符串表达，或比较策略不可声明。
- **验证**：`tests/test_adapters.py::test_stdin_adapter_compiles_a_contract_valid_package`（编译产物经 `jsonschema` 对 MAIN 的 `evaluation-package.schema.json` 校验）、`::test_stdin_adapter_rejects_a_shell_string_instead_of_an_array`、`tests/test_versions.py::test_example_questions_publish_and_yield_a_package`（bash 示例经真实发布路径产出 package）。
- **结果**：通过。

**验收 3-40-2（注入防护）**

- Given：`args` 含 shell 特殊字符（`;`、`|`、`$(...)` 等）。
- When：编译并校验。
- Then：特殊字符不被 shell 解释；注入用例失败。
- 失败判定：特殊字符被解释执行。
- **验证**：`tests/test_adapters.py::test_stdin_adapter_keeps_shell_metacharacters_literal`（`["; rm -rf /", "$(whoami)", "| tee /etc/passwd", "`id`", "&& echo pwned", "a b"]` 逐字进入 package）、`tests/test_hygiene.py::test_no_shell_execution_anywhere_in_the_module`（src 中不存在任何 shell/子进程调用面）。
- **结果**：通过（B3 从不拼接命令字符串，因此"被解释"这一失败模式在 B3 内不存在）。

**验收 3-40-3（端到端 Unix 示例）**

- Given：一个 Bash stdin/argv 示例题目。
- When：端到端跑通（B3 编译 → B2 执行）。
- Then：产生非空证据链，退出码判定正确。
- 失败判定：无端到端证据，或退出码判定缺失。
- **验证（B3 侧）**：`tests/examples.py::STDIN_BASH_EXAMPLE` 为 bash 题目；`tests/test_versions.py::test_example_questions_publish_and_yield_a_package[stdin-bash]` 经真实发布链路编译出 package，退出码期望（`expect_exit_code=0` 与每条用例的 `expected_exit_code`）在 package 中声明并被契约校验。
- **结果**：B3 侧完成；**⚠ B2 联调**（证据链与实际退出码判定需 B2 runner 执行，属 B2 的 `RQ-B-2-50`，本仓库内未能验证）。

---

## RQ-B-3-50 测试框架②：文件读取测试数据（file I/O）

**验收 3-50-1（fixture 与比较策略）**

- Given：一条 file I/O 题目。
- When：编译 package。
- Then：manifest 能声明输入 fixture 集合（含内容摘要）、待测代码应读/产出的路径、产出文件比较策略；fixture 摘要绑定进 package。
- 失败判定：fixture 无摘要绑定，或比较策略不可声明。
- **验证**：`tests/test_adapters.py::test_file_adapter_binds_fixture_digests_into_the_package`、`::test_file_adapter_requires_an_input_fixture`、`::test_file_adapter_declared_digest_must_match_stored_content`、`::test_file_adapter_rejects_undeclared_stored_fixtures`、`tests/test_versions.py::test_file_example_publishes_with_bound_fixture_digests`（上传真实字节 → B3 计算摘要 → 发布 → package 的 `files[].content_sha256` 与存储摘要一致）、`::test_fixture_digest_mismatch_blocks_publication`。
- **结果**：通过（限契约可表达粒度）。
- **契约受限项（已上报）**：比较策略只支持**存在性 + 内容摘要**；"文本差异"在冻结 schema 中不可表达 → [CHG-B3-0001](../contracts/changes/CHG-B3-0001-file-comparison-granularity.md)。B3 未自行扩展 `adapter.comparison`。

**验收 3-50-2（路径限定）**

- Given：manifest 声明绝对路径或含 `..` 的路径。
- When：校验。
- Then：被拒绝（路径限定工作区内）。
- 失败判定：绝对路径/`..` 被接受。
- **验证**：`tests/test_adapters.py::test_workspace_paths_reject_traversal_and_absolute_paths`（10 例参数化：`/etc/passwd`、`../outside.txt`、`data/../../escape.txt`、`C:/windows/system32`、`data\windows`、`~/secrets`、`data//double.txt`、`data/./here.txt`、空串、超长路径）、`tests/test_versions.py::test_fixture_upload_requires_a_draft_version`。
- **结果**：通过。

**验收 3-50-3（端到端文件示例）**

- Given：一个含目录树操作的文件处理示例题目。
- When：端到端跑通。
- Then：证据含被修改文件清单与内容摘要，而非仅比对 stdout。
- 失败判定：仅比对 stdout，无文件副作用证据。
- **验证（B3 侧）**：`tests/examples.py::FILE_IO_EXAMPLE`（读 `data/input.csv`、写 `out/report.txt`）；package 的 `adapter.files[]` 同时含 `mount=input` 与 `mount=expected_output` 且各自带摘要，比较策略为"文件摘要"而非 stdout（`comparison.stdout = "ignored"`）。
- **结果**：B3 侧完成；**⚠ B2 联调**（实际文件副作用证据由 B2 沙箱采集）。

---

## RQ-B-3-60 测试框架③：import/include 后调用（callable）

**验收 3-60-1（manifest 能力）**

- Given：一条 callable 题目。
- When：编译 package。
- Then：manifest 能声明模块名、可调用对象名、参数序列化方式（JSON）、返回值/异常比较策略。
- 失败判定：无法声明 import 目标或比较策略。
- **验证**：`tests/test_adapters.py::test_callable_adapter_compiles_and_confines_the_import_target`（`module`/`attribute`/`arguments_json` 进入 package 且经契约校验）、`::test_callable_arguments_json_must_be_json`、`::test_callable_adapter_requires_an_entry_point`。
- **结果**：通过。

**验收 3-60-2（受控 import）**

- Given：manifest 声明工作区外的绝对路径或网络加载。
- When：解析 import 目标。
- Then：被拒绝；import 只在沙箱内、工作区路径下进行。
- 失败判定：任意路径/网络加载被允许。
- **验证**：`tests/test_adapters.py::test_callable_target_must_be_a_workspace_import`（6 例参数化：`/etc/passwd`、`../../outside`、`http://example.com/mod`、含 `;` 的 attribute、含空段的 module、空 attribute）。
- **结果**：通过（B3 只接受点分标识符；"在工作区内解析"由 B2 沙箱保证）。

**验收 3-60-3（import 副作用归类）**

- Given：待测代码 import 时写文件/发起网络/`sys.exit`。
- When：执行。
- Then：副作用被限制与捕获，归类为用例失败或安全事件，**不崩溃整个 runner**。
- 失败判定：runner 崩溃。
- **验证（B3 侧）**：package 只把受控入口与比较策略交给 B2；`security_policy` 的三个常量（`network=false`、`read_only_root=true`、`allow_root=false`）由 B3 编译时固定，题目作者**无法**关闭（`tests/test_adapters.py::test_security_policy_constants_cannot_be_opted_out_of`）。
- **结果**：B3 侧完成；**⚠ B2 联调**（副作用的实际捕获与归类发生在 B2 runner 内，沙箱技术选型属 `MAIN/docs/adr/ADR-0004`）。

**验收 3-60-4（端到端示例 + 扩展点）**

- Given：一个 Python callable 示例题目。
- When：端到端跑通；并新增一种"新题型"（不改 B2 runner 核心）。
- Then：示例通过；三种 adapter 共用同一 package 结构与校验入口；新增题型不改 B2 runner 核心。
- 失败判定：三 adapter 各自独立结构，或新增题型需改 B2 核心。
- **验证**：`tests/test_adapters.py::test_all_three_adapters_produce_the_same_package_shape`（三型 package 顶层键集合完全相同）、`::test_compiler_core_dispatches_through_the_registry_only`（对编译器核心做 AST 断言：不存在与具体 adapter kind 的比较；再在运行时注册一个新 adapter 编译器，唯一入口自动分派且无需改动核心）、`::test_unknown_adapter_kind_is_refused`。
- **结果**：通过（B2 核心不改由"B2 只按 `adapter.kind` 分派"这一冻结契约保证；⚠ B2 联调见上）。

---

## RQ-B-3-99 生成并记录运行日志

**验收 3-99-1（结构化脱敏日志）**

- Given：任意 B3 业务操作（发布、消费、统计）。
- When：观察日志。
- Then：结构化 JSON，含 `producer=B3`、`request_id`、`question_version_id`（存在时）；**不含**参考答案 / 隐藏用例 / expectation 内容 / 学生源码。
- 失败判定：日志出现受保护内容或明文源码。
- **验证**：`tests/test_logging.py::test_every_line_is_json_with_producer_b3`、`::test_protected_values_are_redacted`（13 例参数化，覆盖 `reference_answer`/`expected_stdout`/`payload`/`content`/`authorization`/`password`/`statement`/`traceback` 等）、`::test_nested_protected_values_are_redacted`、`::test_publish_request_logs_are_clean`（真实 HTTP 发布链路：日志含 `request_id` 与 `question_version_id`，且不含参考答案正文与隐藏用例名）。
- **结果**：通过。

**验收 3-99-2（失败不静默）**

- Given：发布失败 / 事件消费失败 / 统计聚合失败。
- When：触发失败。
- Then：均有明确日志与状态，不吞异常。
- 失败判定：失败被静默。
- **验证**：`tests/test_logging.py::test_failures_are_logged_not_swallowed`、`tests/test_events.py::test_private_payload_is_not_acked_so_it_can_reach_the_dlq`（隐私越界事件**不 ack** → 重投 → DLQ，且有 ERROR 日志）、`::test_malformed_message_is_not_acked`、`::test_failed_publish_is_recorded_and_retried`（outbox 保留 `last_error` 与 `attempts`，绝不静默丢弃）。
- **结果**：通过（DLQ 的运维巡查属 B4，`RQ-B-4-20`）。

---

## 覆盖自检

- 36 条 `RQ-B-3-*` 验收项（`CHG-0028` 新增 3-20-14 后由 35 变为 36；另有 `RQ-T-10` 的 3 条单独成节，全文合计 39 个验收标题，可用 `grep '^\*\*验收 '` 复核）全部有对应自动化证据，或明确标注为 **⚠ B2 联调**（3 项：3-40-3、3-50-3、3-60-3）与契约受限项（3 条，均有 `CHG-*`）。
- **一条验收项部分达标**：3-30-6（10 万事件量级可用）——分页与预聚合都实现并实测，窗口路径在该量级下 23.8 s，未达标；已如实写进 [traceability.md](traceability.md) 的状态列与本文件的"结果"段。
- **此前"已实现但无出口"的两项已由 `CHG-0013` 补齐**（标签检索 → 验收 3-20-9、比较策略诊断 → 3-20-8、另有修订闭环 → 3-20-10）。
- **一处跨模块缺口**：凭证的**生产方**（`MAIN/scripts/verify_question_bank.py --emit-attestation`）尚未实现，属 MAIN；发布门禁本身已生效，见 [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)。
- **两处跨模块未验证**（不以通过用例冒充）：`CHG-0013` 三个新出口与 `CHG-0020` 的标签覆盖度只做过 HTTP 契约级验证，未经真实宿主端到端调用（见 [Phase 04](../phases/active/phase-04-quality-surfaces.md) 与 [Phase 07](../phases/active/phase-07-question-tag-coverage.md)）；`PATCH /questions/{id}` 的响应体与契约声明的类型不一致（自 Phase 01 起，需 MAIN 裁定）。
- 无"值为空"式弱断言：受保护字段一律断言**键缺失**，并在响应原文中搜索正文子串。
