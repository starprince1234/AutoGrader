# B3 需求追踪（Traceability）

权威需求：`../../../MAIN/docs/requirements/rq-catalog.md` 第 4 节。本表覆盖 B3 全部 7 条责任需求，编号**精确匹配**，无遗漏无多余。状态词仅用 `骨架` / `待实现` / `已完成` / `阻塞`。

> 状态基线：2026-09-20（[Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md)：`CHG-0028` 草稿也可取执行输入；
> 同日早些时候 [Phase 09](../phases/active/phase-09-reference-answer-channel.md)：`CHG-0023` 参考解答交付通道、
> [Phase 08](../phases/active/phase-08-omission-preserves.md)：`CHG-0017` 省略即保留，
> 以及 [Phase 07](../phases/active/phase-07-question-tag-coverage.md)：`CHG-0020` 标签覆盖度）。
> 证据列指向**当前仓库里存在且被验证执行过**的测试文件与函数；验证记录见 Phase 10、Phase 09、Phase 08、Phase 07、
> Phase 04 与 [`../phases/active/phase-01-question-bank-and-packages.md`](../phases/active/phase-01-question-bank-and-packages.md)。
>
> "已知限制"列记录**契约所限**的范围（不修改 `MAIN/contracts/**` 前提下不可实现的部分），逐条对应 `../contracts/changes/CHG-*`。

## 逐 RQ 追踪表

| RQ | 需求摘要 | 本模块实现方式 | 关联工作包 | 关联测试 | 状态 | 已知限制 / 证据 |
|---|---|---|---|---|---|---|
| `RQ-B-3-10` | 题目含题干、参考答案、测试用例 | 题目聚合（`domain/questions.py`）+ 不可变版本快照；公开/内部 DTO 物理分离（`dto/public.py` / `dto/internal.py`，ADR-0004）；参考答案/隐藏用例/expected output 只落 `b3.reference_answers` / `b3.test_cases` / `b3.blobs`（普通角色无授权）；题干图片受控读写 + 内容摘要 | `WP-B3-A` | `test_questions_api.py::test_list_questions_has_no_protected_field`、`::test_question_detail_has_no_protected_field`、`::test_published_asset_is_served_with_hardening_headers`、`::test_unpublished_asset_is_not_served_to_students`、`test_migrations.py::test_app_role_cannot_read_protected_tables`、`test_dto_boundary.py`（8 例） | `已完成` | 图片上传/读取与 fixture 上传三个端点在 `b3.yaml` 中**未成文**（`CHG-B3-0002`）；参考答案按契约**无任何出站路径**（package schema 无该字段、公开 DTO 无该字段），教师查看参考答案需 MAIN 决策新端点 |
| `RQ-B-3-20` | 按课程维护题库，含 assignment 与发布 | question/version/assignment CRUD + 课程作用域校验；`publish` 显式动作 + 完整性校验（题干非空 / ≥1 用例 / 参考答案存在 / adapter 合法 / fixture 摘要一致）；已发布版本不可变（应用层 409 + PostgreSQL 触发器 `23001`）；assignment 只引用已发布版本；发布事件经 outbox 同事务写出；**修订闭环**：分支新版本时计算并落结构化差异（`revision_diff`，键集合 = 契约 `RevisionDiff`）与修订理由（作者手写优先，无则按差异派生）；**标签检索**：`GET /questions?tags=` 为"全部满足"语义；**比较策略诊断**：`GET /questions/{id}/diagnostics` 返回 `CMP-001..004` 的确定性结论；**标签覆盖度**：`GET /questions/coverage` 按调用者可见的题目聚合逐标签计数与合计（可校验恒等式 `tagged_questions + untagged_questions == total_questions`；逐标签计数按设计重叠，`sum(tags) != total`），与列表**共用同一个作用域/可见性判定**（`domain/catalog.py:_read_scope`），没有预聚合表；**省略即保留**（`CHG-0017`）：`QuestionDraftInput` 的 `cases` 可选，省略则该版本保留原用例集（分支新版本时把上一版内容重新签发成新行），提供则整体替换，`reference_answer` 省略或空白同样保留；"是否提供"由传输层读 `model_fields_set` 判定，领域层对矛盾声明报错；**不存在清空全部用例的路径**（省略保留 + `minItems: 1`），首次省略产生的无用例草稿**不可发布**；**参考解答交付通道**（`CHG-0023`）：`GET /question-versions/{id}/reference-answer` 以 `serviceToken` 交付该版本**声明语言**的参考解答与存证口径的 `content_digest`（`CHG-0019` 的必需输入），教师与学生一律 `403`，版本存在但无法验证时 `409`（不用 `404` 掩盖）；**草稿的取包前置**（`CHG-0028`）：`GET /question-versions/{id}/evaluation-package` 对**草稿与已发布都返回 `200`**——草稿按**当前**内容编译、`created_at` 用内容时间戳（**不是** `now()`）、且不比对本就还不存在的记录摘要；已发布路径的摘要比对**保持不变**（改内容 → `409`）；`409` 因此只剩一个含义：没有可编译的内容。这一块是"验证草稿 → 存证 → 发布"整条链的第一步，缺它时 `CHG-0019`/`ADR-0015` 的目的在真实系统里不可达 | `WP-B3-A` | `test_versions.py`（38 例，含 `test_editing_a_published_version_in_place_is_refused`、`test_postgres_refuses_an_in_place_edit_of_published_content`、`test_assignment_accepts_only_published_versions`、`test_publish_without_a_reference_answer_is_refused`、`test_revision_diff_records_what_changed_and_never_contains_protected_content`、`test_a_revision_note_can_only_be_recorded_by_superseding_a_version`、`test_omitting_cases_keeps_the_stored_case_set_unchanged`、`test_supplying_cases_replaces_the_whole_set_rather_than_adding_to_it`、`test_a_title_only_edit_leaves_the_reference_answer_row_untouched`、`test_a_branched_version_that_omits_cases_records_no_case_change`、`test_a_version_created_without_cases_cannot_be_published`、`test_declaring_cases_omitted_while_sending_them_is_refused`）、`test_questions_api.py`（98 例，含 `test_cross_course_read_is_403_not_404`、`test_draft_edit_then_publish_then_conflict`、`test_question_list_filters_by_tags_with_all_of_semantics`、`test_diagnostics_report_the_content_rules_with_declared_severities`、`test_coverage_identity_is_tagged_plus_untagged_and_the_tag_sum_is_larger`、`test_coverage_is_forbidden_across_courses_and_students_only_count_published`、`test_coverage_agrees_with_the_list_visibility_rule`、`test_coverage_response_carries_counts_only`、`test_a_bank_where_no_question_is_tagged_reports_untagged_not_an_error`、`test_coverage_route_precedes_the_question_path_parameter`、`test_a_correction_that_omits_both_protected_fields_is_accepted`、`test_null_and_empty_case_sets_are_refused_without_writing_anything`、`test_a_body_that_omits_the_required_public_fields_is_still_refused`、`test_reference_answer_is_served_to_services_for_the_declared_language`、`test_reference_answer_is_never_readable_by_a_user_token`、`test_a_version_without_a_reference_answer_is_409_not_404`、`test_an_unknown_version_has_no_reference_answer_to_serve`、`test_a_version_with_no_test_cases_cannot_supply_its_answer`、`test_the_reference_answer_digest_is_the_one_the_attestation_binds`）、`test_logging.py::test_serving_the_reference_answer_does_not_log_it` | `已完成` | `POST /questions` 的 `course_id` 采用 query 参数（契约请求体无该字段，`CHG-B3-0002`）；删除已发布版本**没有 HTTP 入口**（契约未声明 DELETE），不可删除性由领域函数与触发器双重保证；`PATCH /questions/{id}` 的响应体是 `QuestionPublic` 而契约声明 `QuestionVersionPublic`（自 Phase 01 起，见 [../api/contract.md](../api/contract.md)）；`title`/`stem_markdown`/`language`/`adapter` 仍是必填，`CHG-0017` 只让两个受保护字段可省略；`adapter` 写必填却无读出口（见 [../api/contract.md](../api/contract.md) 的待决问题） |
| `RQ-B-3-30` | 题目答题情况与回答次数分布统计 | durable consumer（inbox 按 `event_id` 主键 + `evaluation_run_id` 唯一键去重）→ 整数预聚合表 → 统计查询；窗口查询从 inbox 按 **keyset 分页**重算（每页 1000 行，硬上限 100 万行，超限 `422` 而非静默截断）；`rebuild` 清表后按 `occurred_at` 重放；消费端字段白名单拒绝越界字段；每次投递结果（accepted/duplicate/rejected）落 `b3.consumption_counters` | `WP-B3-C` | `test_statistics.py`（39 例，含 `test_duplicate_event_id_is_counted_once`、`test_rebuild_from_the_inbox_reproduces_incremental_values`、`test_replay_out_of_order_produces_the_same_numbers`、`test_forbidden_payload_fields_are_rejected_and_not_counted`、`test_the_window_scan_is_paged_and_bounded`、`test_a_window_wider_than_one_page_aggregates_every_row`、`test_health_counts_rejected_deliveries_that_never_reach_the_inbox`）、`test_auth.py::test_statistics_requires_a_privileged_role`、`test_consumer_cli.py::test_health_command_exits_non_zero_when_deliveries_are_rejected` | `已完成`（**验收 5 未达标**：窗口路径未实测通过） | **验收 5（"10 万事件量级可用，分页 + 预聚合"）只满足一半**，实测见 [Phase 03](../phases/active/phase-03-quality-attestation-and-bounded-statistics.md)：分页已实现（keyset，峰值内存 256.6 MiB → 5.1 MiB，50×），预聚合路径 10 万事件 **0.044 s / 0.66 MiB**；但**带时间窗的路径在同一量级下仍是 23.8 s**，属"可用性未达标"，未做预聚合下推到 SQL。其余限制不变：独立作答人数与平均分不在 B3 口径（`CHG-B3-0003`）；分桶需 `max_score`，缺失计入 `unbucketed_attempt_count` 而不猜测 |
| `RQ-B-3-40` | 测试框架①：命令行接收测试数据（stdin/argv） | `StdinArgvAdapterCompiler`；`args` 为**数组**且逐字保留（shell 元字符原样进入 package，B3 从不拼接命令字符串）；声明 stdin / 比较策略 / 期望退出码；Bash 端到端示例题目 | `WP-B3-B` | `test_adapters.py::test_stdin_adapter_compiles_a_contract_valid_package`、`::test_stdin_adapter_keeps_shell_metacharacters_literal`、`::test_stdin_adapter_rejects_a_shell_string_instead_of_an_array`、`test_hygiene.py::test_no_shell_execution_anywhere_in_the_module`（对 src 全量逐文件断言） | `已完成` | 实际执行属 B2；B3 只产出确定性 package（含 `package_digest`） |
| `RQ-B-3-50` | 测试框架②：文件读取测试数据（file I/O） | `FileIoAdapterCompiler`；fixture 字节入内容寻址存储（`b3.blobs`），摘要由 B3 计算并与 client 声明值**比对**；`mount=input` 为输入、`mount=expected_output` 为期望产出（带摘要）；路径限工作区（拒绝绝对路径 / `..` / 盘符 / 反斜杠 / 空段 / `~`） | `WP-B3-B` | `test_adapters.py::test_file_adapter_binds_fixture_digests_into_the_package`、`::test_workspace_paths_reject_traversal_and_absolute_paths`（10 例参数化）、`::test_file_adapter_declared_digest_must_match_stored_content`、`test_versions.py::test_file_example_publishes_with_bound_fixture_digests`、`::test_fixture_digest_mismatch_blocks_publication` | `已完成` | **"文本差异"比较粒度在冻结 schema 中不可表达**（`adapter.comparison` 只允许 stdout/stderr/数值容差，`additionalProperties: false`）→ `CHG-B3-0001`；B3 实现存在性 + 内容摘要比较，未自行扩展契约 |
| `RQ-B-3-60` | 测试框架③：import/include 后调用（callable） | `CallableAdapterCompiler`；`callable.module` / `attribute` 必须是**点分标识符**（拒绝路径、URL、`..`、`;`）；`arguments_json` 必须是合法 JSON；与另两型共用同一 package 结构与唯一编译入口 | `WP-B3-B` | `test_adapters.py::test_callable_adapter_compiles_and_confines_the_import_target`、`::test_callable_target_must_be_a_workspace_import`（6 例参数化）、`::test_all_three_adapters_produce_the_same_package_shape`、`::test_compiler_core_dispatches_through_the_registry_only`（扩展点） | `已完成` | import 副作用（写文件/联网/`sys.exit`）的**归类**发生在 B2 runner 内（沙箱属 B2，ADR-0004）；B3 的职责是声明受控入口与比较策略 |
| `RQ-B-3-99` | 生成并记录运行日志 | `logging.py`：单行 JSON、`producer=B3`、按 key 脱敏（受保护键值一律 `[redacted]`，异常只记类型与消息）、`log_event` 结构化入口；请求中间件贯穿 `request_id`，领域/消费路径带 `question_version_id` | `WP-B3-C` | `test_logging.py`（23 例，含 `test_protected_values_are_redacted`（13 例参数化）、`test_publish_request_logs_are_clean`、`test_failures_are_logged_not_swallowed`）、`test_events.py`（拒收事件有日志且不进 DLQ 沉默丢弃） | `已完成` | 事件消费失败写 ERROR 日志并**不 ack**（交给 JetStream 重投 → DLQ）；DLQ 巡查属 B4（`RQ-B-4-20`） |

## 全体需求中 B3 承接的部分（附注，非 B3 责任编号）

| RQ | B3 承接点 | 状态 | 证据 |
|---|---|---|---|
| `RQ-B-0-10`（Python/bash 语言对象） | package 的 `language` 与提交输入都只接受 `python`/`bash`（pydantic `Literal` + DB CHECK），与 B2 runner policy 同枚举 | `已完成` | `test_adapters.py::test_language_enum_is_restricted_to_the_contract_values`、`test_migrations.py`（CHECK 约束） |
| `RQ-B-0-30`（Unix 作业表达能力） | 三型 adapter 各有真实示例（bash stdin/argv、目录树文件 I/O、Python callable），全部经同一编译入口并通过 MAIN schema 校验 | `已完成` | `tests/examples.py`、`test_adapters.py`（40 例，逐例 `jsonschema.validate` MAIN 契约） |

## 覆盖自检

- 7 条 B3 RQ 全部在表，与 MAIN 目录逐条比对无差集；6 条 `已完成`，`RQ-B-3-30` 为
  `已完成`（**验收 5 未达标**）——本轮之前它被记为无条件 `已完成`，而验收 5 的两半
  （分页、10 万量级实测）当时都不存在；实测后其中一半达标、一半不达标，因此状态词加了限定。
- 每条 RQ 在 [acceptance.md](acceptance.md) 有可验证验收、在 [../testing/cases.md](../testing/cases.md) 有对应用例项且均已实现。
- 无 `RQ-B-3-70` 或任何旧编号（仅作为"已删除"叙述出现在 [scope.md](scope.md) 非目标与 MAIN 检查器说明中；本表不声明该编号）。
- 契约缺口 3 条，全部已提交 `CHG-*`，且**均不阻塞**已实现范围：[CHG-B3-0001](../contracts/changes/CHG-B3-0001-file-comparison-granularity.md)（文件比较粒度）、[CHG-B3-0002](../contracts/changes/CHG-B3-0002-asset-and-fixture-endpoints.md)（资产/fixture 端点未成文）、[CHG-B3-0003](../contracts/changes/CHG-B3-0003-distinct-student-statistic.md)（独立人数不可计算、`is_pass` 字段名待确认）。
- 本轮之前记载的**能力已实现、出口未成文**两项，已由 MAIN 的 `CHG-0013` 补齐出口并在本轮落地（[Phase 04](../phases/active/phase-04-quality-surfaces.md)）：
  修订理由 `revision_note` / 结构化差异 `revision_diff`、比较策略诊断端点、以及标签检索。三项现在都有契约声明的读写面；
  **"有出口"不等于"已在真实宿主上端到端跑过"**——本轮的证据是 HTTP 契约测试 + 真实 PostgreSQL，跨模块宿主联调仍未做。
- 本轮发现**契约内部不一致 1 处**：`b3.yaml` 的 `ComparisonDiagnostics` 字段 `enum` 是 `error|warning|info`，
  但同一段叙述句把第三个取值称为 `note`。B3 按 enum 实现，并已上报（[Phase 04](../phases/active/phase-04-quality-surfaces.md) 未完成节）。
- 本轮（`CHG-0020`，[Phase 07](../phases/active/phase-07-question-tag-coverage.md)）落地标签覆盖度，
  `RQ-B-3-20` 的"标签能写能查、分布读不出来"这一可用性缺口关闭；**未新增契约缺口**（3 条仍为
  [CHG-B3-0001](../contracts/changes/CHG-B3-0001-file-comparison-granularity.md) /
  [CHG-B3-0002](../contracts/changes/CHG-B3-0002-asset-and-fixture-endpoints.md) /
  [CHG-B3-0003](../contracts/changes/CHG-B3-0003-distinct-student-statistic.md)）。
  但实测发现 `CHG-0020` §5 "聚合走既有 GIN 索引 `ix_questions_tags`"的措辞不成立：
  该 GIN 索引服务的是 `jsonb @>` 包含谓词，而集合返回函数展开没有可用的 GIN 操作符类；
  B3 只如实记录（Phase 07 §3），**未改 MAIN 的变更记录**。
- 本轮（`CHG-0017`，[Phase 08](../phases/active/phase-08-omission-preserves.md)）落地草稿写入的省略语义：
  `cases` 可选、省略即保留、提供即整体替换，分支版本继承上一版内容，且不存在清空用例集的路径。
  这关闭的是"**每一次**编辑都必须把隐藏用例与参考答案读出来再发回去"这条往返——`RQ-B-3-20` 里
  "按课程维护题库"的可用性前提。**未新增契约缺口**；两条移交 MAIN 的观察记在 Phase 08 §6：
  `adapter` 写必填却无读出口、分支版本不继承 fixture。
- 本轮（`CHG-0023`，[Phase 09](../phases/active/phase-09-reference-answer-channel.md)）落地参考解答的交付通道：
  `CHG-0019`（B2 执行参考解答并回报存证）在契约层面此前**不成立**——没有任何通道把参考解答交给 B2。
  现在有一个 `serviceToken` 专用端点交付它，并复用 `getEvaluationPackage` 的同一个 scheme（无新凭据、无新信任关系）。
  **未新增契约缺口**；两条需要 MAIN 关注的事项均已收口：①"有用例但无摘要"也返回 `409` 由 `CHG-0026`
  裁决为**保留并逐条点名**（判定为契约措辞含糊，非实现越界；B3 无代码改动，只补了 detail 可区分性的镜像断言）；
  ②该响应的投影必须在内部模块，公开出口按设计拒绝 `reference_answer` 键（Phase 09 §2.1）。
- 本轮（`CHG-0028`，[Phase 10](../phases/active/phase-10-draft-package-and-current-pointers.md)）落实草稿的取包前置：
  发布闭环此前**在第一步就断**（`getEvaluationPackage` 拒草稿 × `recordReferenceAttestation` 只收草稿 ×
  `publish` 要求先有存证）。现在草稿按当前内容编译并可取，已发布路径的摘要比对原样保留。
  同一轮还修了两处**指向过时机制**的拒绝文案（`publish` 无存证时指向教师侧"验证并发布"而非已被取代的验证脚本；
  `reference-answer` 情形 ② 只点名缺失的摘要、不再断言一个在真实数据上不成立的原因）。
  **未新增契约缺口**；`CHG-0025` 明确不在本包（改排 `0.17.0`），未做。
- ~~**一处跨模块缺口**：参考答案凭证的**生产方**未实现（`MAIN/scripts/verify_question_bank.py` 没有
  `--emit-attestation`）~~ → **已关闭**：`CHG-0019`/`ADR-0015` 把生产方定为 **B2**（教师侧"验证并发布" →
  B2 执行 → B3 记录），不再依赖那个脚本的开关；B3 这侧的两件输入（`CHG-0028` 的草稿包、`CHG-0023` 的参考解答）
  与存证端点都已就绪。本轮把 `publish` 的拒绝文案里那个已被取代的指针一并去掉（Phase 10 §4.3）。
  **仍未做的是跨模块那半**：B2 真的执行参考解答并逐用例比对，需要 Linux 沙箱。
- `RQ-T-10`（出题期辅助）不在上表：它是团队自选需求，编号与验收权威在 `MAIN/docs/requirements/rq-catalog.md` §5B；
  B3 的落地与验收见 [acceptance.md](acceptance.md) 的 T-10-1~3 与 [../api/contract.md](../api/contract.md)。
