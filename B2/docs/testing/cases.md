# B2 测试用例目录

本表是"能不能证明需求被满足"的检查单。每个用例对应 [../requirements/acceptance.md](../requirements/acceptance.md) 的场景。层次取值：单元 / 契约 / 集成 / 端到端 / 安全 / 资源。

**存在性口径**（2026-09-16 逐行核对，可用 `grep` 复算）：本表**没有"待建"行残留**——每行要么指向真实测试，要么在"当前是否存在"列写明**部分覆盖**及其**缺的具体断言**。指针有两种写法：`文件::测试名`（本行内给全路径），以及**同节已给文件路径时**的短写 `::测试名`（继承本节上文点名的文件，例如 T-RECV-* 全节继承 `tests/test_intake_api.py`）；两种写法里的测试名都是**完整函数名**，可直接 `grep`。核对方式：对每一行读实现与测试源码确认断言覆盖期望，而不是按文件名字面猜测；`tests/linux/**` 的用例在 Windows 上不可执行（模块级 skip），均已在行内注明"仅 Linux/容器内执行"。

## 接收与校验（RQ-B-2-10）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-RECV-001 | 2-10 | 契约 | 端点已注册 | 发缺字段/错误类型/`..`/绝对路径/反斜杠请求 | `422` 且不落库 | **存在**（`tests/test_intake_api.py::test_structurally_invalid_envelope_is_422_and_writes_nothing`、`::test_illegal_path_is_422_and_writes_nothing`、`::test_path_traversal_specifically_reports_path_not_allowed`；落库为零按四表计数断言） |
| T-RECV-002 | 2-10 | 契约 | 端点已注册 | 发 `content_sha256` 与实际内容不符请求 | `422` | **存在**（`::test_digest_mismatch_is_422_and_writes_nothing`，并多断言了"零落库"） |
| T-RECV-003 | 2-10 | 契约 | 本地测试密钥 | 无 token → `401`；作用域不符 → `403` | 错误码精确区分 | **存在**（`::test_missing_token_is_401`、`::test_course_scope_mismatch_is_403_not_404`（显式断言"不得用 404 掩盖"）＋同族 `::test_expired_token_is_401` 等 6 条） |
| T-RECV-004 | 2-10 | 集成 | 端点 + 存储 | 同 `idempotency_key` 重复请求 | 同 `submission_id` + `202`，仅一次评测 | **部分覆盖**（`::test_repeated_idempotency_key_returns_same_submission_without_second_write`、`::test_idempotent_replay_does_not_enqueue_second_event`、`tests/test_group_acceptance_probe.py::test_probe_idempotent_replay_writes_nothing_new_on_disk` 覆盖 202/同 id/四表各一行/不新增事件与文件；**缺**：没有任何断言统计 `evaluation_runs`，"仅一次评测"目前只有"outbox 事件数==1"作间接支撑） |
| T-RECV-005 | 2-10 | 集成 | NATS 订阅 | 提交后监听 `submission.accepted` | payload 不含源码 | **存在**（`::test_submission_accepted_event_has_no_source_code`（payload 键集固定、源码与 base64 原文不在其中）、`tests/test_events_outbox.py::test_publish_to_real_jetstream_and_read_back`（真 NATS 发布+订阅读回）、`::test_published_payloads_carry_no_sensitive_markers`、`tests/test_group_acceptance_probe.py::test_probe_submission_accepted_payload_has_no_source_anywhere_in_the_row`；**流程缺口**：读回用例的事件行由测试直接入队，"HTTP 提交 → 真实 NATS 订阅"没有单条端到端用例） |
| T-RECV-006 | 2-10 | 契约 | 端点已注册 | 超单文件/总大小/文件数请求 | `413`/`422` 拒绝 | **存在**（`::test_too_many_files_is_422`、`::test_oversized_single_file_is_413`、`::test_oversized_total_is_413`、`::test_declared_content_length_over_cap_is_413`；文档的 `413`/`422` 分工与断言一致） |

## 邮件接收（RQ-B-2-20）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-MAIL-001 | 2-20 | 集成 | 本地 IMAP 夹具 | 拉取带附件邮件 | 归一化为同一 `SubmissionEnvelope` 走同路径 | **存在**（`tests/test_email_intake.py::test_email_attachment_produces_the_same_envelope_as_the_api_path`（两条入口同 `persist`、字段与摘要逐项相等）、`::test_imap_pull_normalises_to_the_same_rows_as_the_api_path`（真实 TCP IMAP 夹具）、`tests/test_email_intake_acceptance.py::test_email_attachment_normalises_through_the_same_intake_service`） |
| T-MAIL-002 | 2-20 | 集成 | IMAP 夹具 | 无法唯一匹配的邮件 | 进隔离区、记录原因、不评测 | **存在**（`::test_message_that_matches_nothing_is_quarantined_and_queryable`、`::test_ambiguous_message_is_quarantined_without_guessing`、`tests/test_email_intake_acceptance.py::test_unmatched_sender_is_quarantined_queryable_and_never_evaluated`（显式 `count(evaluation_runs)==0`）） |
| T-MAIL-003 | 2-20 | 集成 | IMAP 夹具 | 重复拉取同一邮箱 | `email_checkpoint` 幂等，不重复提交 | **存在**（`::test_same_uid_and_uidvalidity_are_pulled_once`、`::test_same_message_id_under_a_changed_uidvalidity_is_skipped`、`::test_concurrent_duplicate_is_skipped_instead_of_crashing`、`::test_email_checkpoint_is_written_in_the_submission_transaction`、`tests/test_email_intake_acceptance.py::test_repeated_pull_of_the_same_uid_does_not_create_a_second_submission`） |
| T-MAIL-004 | 2-20 | 安全 | IMAP 夹具 | 可执行附件 / 压缩炸弹 | 拒绝并记录安全事件 | **存在**（拒绝与零落库：`::test_executable_attachment_is_rejected_and_logged`（参数化 ELF/PE/exe）、`::test_zip_bomb_is_rejected_by_its_declared_size`、`::test_gzip_bomb_and_plain_gzip_are_both_rejected`、`::test_polymorphic_zip_bomb_behind_a_text_prefix_is_rejected`；**安全事件**的结构化断言只在 `::test_polymorphic_zip_bomb_behind_a_text_prefix_is_rejected` 与 `tests/test_email_intake_acceptance.py::test_executable_attachment_is_rejected_and_records_a_security_event` 里，压缩炸弹主用例只断言拒绝+隔离原因） |
| T-MAIL-005 | 2-20 | 集成 | 日志脱敏器 + 邮件入口 | 调试日志处理邮件 | 日志无正文/附件原文 | **存在**（决定性用例是**集成级**的 `tests/test_email_intake.py::test_body_and_attachment_bytes_never_reach_the_quarantine_or_the_logs`（DEBUG 级下真实处理两封邮件，正文/附件标记的原文与 base64 四形态零命中，并有"允许字段确实记了"的反证）；脱敏器单元层为 `tests/test_logging_redaction.py::test_byte_values_are_dropped_because_they_are_content_not_identifiers`、`::test_content_credentials_and_paths_never_reach_the_rendered_line`） |

## 内容寻址存储（RQ-B-2-30）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-STORE-001 | 2-30 | 集成 | 存储层 | 两个提交含相同内容 | SHA-256 去重，只存一份 | **存在**（`tests/test_storage_cas.py::test_two_submissions_share_one_artifact_but_keep_two_metadata_rows`（artifact 行数==1、盘上只一个文件）、`::test_same_content_written_twice_stores_one_artifact`、`::test_concurrent_identical_puts_succeed_and_store_one_artifact`（12 并发同内容仍一行）；模块级 `requires_postgres`） |
| T-STORE-002 | 2-30 | 单元 | 存储层 | 客户端指定路径/目录穿越 | 非法路径失败，路径由服务端生成 | **存在**（纯单元层：`tests/test_contract_conformance.py::test_contract_schema_accepts_dotdot_but_b2_allowlist_rejects`、`::test_allowlist_boundaries`；集成层：`tests/test_storage_cas.py::test_storage_rejects_illegal_client_paths`（12 例参数化）、`::test_storage_path_is_derived_from_digest_only`（`storage_ref` 只由摘要派生））。注：`test_storage_cas.py` 整模块走 PG 门禁，无库时连纯词法断言也 skip，故"单元"这一层以 `test_contract_conformance.py` 为准 |
| T-STORE-003 | 2-30 | 集成 | 认证 | 学生 A 读学生 B 的提交资源 | `403`；本人可读 | **存在**（`tests/test_submission_query.py::test_student_reading_another_students_submission_is_403_not_404`、`::test_owner_can_read_own_submission`、`::test_view_shape_and_no_file_content_leaks`、`tests/test_group_acceptance_probe.py::test_probe_cross_student_read_is_403_and_leaks_nothing`）。**口径修正**：系统**没有对外内容下载端点**（只有 `POST /api/v1/submissions` 与 `GET /api/v1/submissions/{id}` 元数据视图），因此证的是"越权读提交资源 403 + 视图绝不内联源码/存储引用"，不是"下载原始字节被拒" |
| T-STORE-004 | 2-30 | 集成 | 存储层 | 先内容后元数据；元数据写入失败；保留窗口 | 孤儿内容可回收，被引用内容不误删，清理有审计；**保留策略可配置且真的被判定** | **存在**（`tests/test_storage_gc.py::test_metadata_write_failure_leaves_recoverable_orphan_content`（故障注入：内容已落盘、零 artifact 行、孤儿被回收且字节数对、审计留 `(digest,"removed")`）、`::test_referenced_content_is_never_collected`、`::test_orphan_content_is_collected_with_audit`、`::test_file_removal_failure_is_recorded_not_swallowed`；**保留策略（验收 5）**：`::test_configured_retention_window_defers_reclamation`（`B2_ARTIFACT_RETENTION_DAYS=30` ⇒ 不删、不写审计、被引用内容仍可读）、`::test_content_older_than_the_window_is_reclaimed`（过窗口照常回收 ⇒ 反向断言，忽略保留配置即失败）、`::test_retention_window_also_defers_stray_file_removal`（相二按文件 mtime）、`::test_collection_is_reference_based_not_time_based`（默认窗口 0）） |

## 静态检查（RQ-B-2-40）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-STAT-001 | 2-40 | 单元 | 静态检查器 | Python 语法错误 / Bash 语法错误 | `ast` / `bash -n` 检出，含行号与错误 | **存在**（`tests/test_static_checks.py::test_python_syntax_error_is_reported_with_line`（`STAT-PY-001`、line/column 非空、message 含 SyntaxError）、`::test_bash_syntax_error_is_reported_with_line`（`STAT-SH-001`、line==3）、`::test_bash_is_invoked_with_fixed_argv_array`（argv 就是 `["bash","-n",path]`，源码不进命令行）、`tests/test_ss_probe_static_layer.py::test_probe_std1_python_syntax_error_carries_line_and_readable_message`）。注：bash 分支的决定性断言用 monkeypatch 的 stderr 形态；不 monkeypatch 的 `::test_real_bash_environment_is_reported_honestly` 在缺 bash 时走 `not_checked` 并断言"未执行必给原因" |
| T-STAT-002 | 2-40 | 单元 | 规则目录 | 合法但有警告代码 | 每条含规则 ID/severity/文件/行列/说明 | **存在**（`::test_warning_finding_fields_are_complete`（字段全集 + severity 取自规则目录）、`::test_every_finding_belongs_to_a_registered_rule`、`tests/test_ss_probe_static_layer.py::test_probe_std1_warning_details_have_rule_severity_file_line_column`）。**口径修正**：**列号在可得时才带**——bash 语法错误的 `column` 就是 `None` 且被显式断言（`test_static_checks.py` 里 `assert errors[0].column is None  # bash 不给列号`） |
| T-STAT-003 | 2-40 | 单元 | 静态检查器 | 同输入跑两次 | 规则集合与摘要一致 | **存在**（`::test_repeated_runs_are_identical_for_python`、`::test_repeated_runs_are_identical_for_bash`（整份结果 + `result_digest` + findings 非空）、`::test_digest_is_stable_across_processes_and_hash_seeds`（三个 PYTHONHASHSEED 下 stdout 集合大小==1）、`::test_rule_catalog_is_deterministic`、`tests/test_ss_probe_static_layer.py::test_probe_std1_digest_discriminates_and_is_stable_across_processes`（另有区分力反证：clean 与 warned 摘要必须不同）） |
| T-STAT-004 | 2-40 | 集成 | 静态+安全 | 静态高危代码 | 置 `rejected`、阻断动态、报告不伪装通过 | **存在**（`tests/test_evaluation_pipeline.py::test_static_block_rejects_without_running_or_runner_calls`（`rejected`/`static_blocked`、运行史无 `running`、runner 零调用、无结果行）、`::test_static_block_terminal_frame_has_reason_and_no_score`、`::test_report_rejected_for_non_completed_state`（报告侧 `ReportRejected`）、`tests/test_b2_routes.py::test_report_is_404_for_rejected_and_failed_runs`（路由侧 404）、`tests/test_ss_probe_pipeline.py::test_probe_std2_malicious_submission_is_rejected_without_any_runner_call` + `::test_probe_std2_clean_submission_really_reaches_the_runner`（正对照，证明"零调用"不是恒真）） |
| T-STAT-005 | 2-40 | 安全 | 不执行待测代码 | 含 import 副作用模块 | 检查不执行待测模块 | **存在**（行为证据：`tests/test_static_checks.py::test_static_check_does_not_execute_module_side_effects`（PWNED 文件不存在 + 反证该样本确实被解析）、`::test_static_check_does_not_import_declared_modules`、`::test_python_checker_never_executes_code`、`tests/test_security_decision.py::TestNoExecution::test_analysis_of_side_effecting_sample_has_no_side_effects`（socket backlog 超时证明样本真没建连）、`::test_module_level_side_effect_never_runs`、`tests/test_ss_probe_static_layer.py::test_probe_std3_declared_module_is_never_imported`、`::test_probe_std3_analysis_writes_nothing_anywhere`（整树哈希快照不变）；机制证据：`python_checker` 只 `ast.parse`、`bash_checker` 只 `["bash","-n",path]`）。**强度差异（如实记）**：静态检查器的"不执行"**源码审计是字符串包含**（`forbidden not in text`），真正的 AST 级审计目前只在 runner 源码上（`tests/test_runner_core.py::test_runner_sources_never_enable_a_shell`）；若要"对静态检查器也做 AST 审计"，目前没有该测试 |

## 动态执行（RQ-B-2-50）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-RUN-001 | 2-50 | 集成 | runner+比较器 | 多用例 package | 每例含输入/实际(截断)/期望/判定/失败类别 | **存在**（`tests/test_runner_core.py::TestEvidence::test_evidence_has_every_frozen_field`（输入/期望/实际/判定/失败类别字段全集）、`::test_output_is_truncated_at_max_output_bytes`（实际输出截断）、`::TestProcessTreeCleanup::test_one_failing_case_does_not_skip_the_others`（多用例逐例成条）、`tests/test_evaluation_pipeline.py::test_case_evidence_is_persisted_per_ordinal_and_unique`（逐例落库 ordinal/weight/awarded/摘要）） |
| T-RUN-002 | 2-50 | 单元 | 比较器 | 精确/忽略行尾/数值容差策略 | 策略由 package 声明，学生不可影响 | **存在**（`tests/test_runner_core.py::TestComparators`：`test_registry_covers_contract_strategies`、`test_text_strategies`、`test_numeric_tolerance`、`test_stderr_does_not_accept_numeric_tolerance`；`TestStrategyComesFromPackage`：`test_result_follows_the_package_strategy`、`test_submission_side_adapter_cannot_override_the_package`、`test_submission_side_policy_cannot_relax_the_package`、`test_tampered_case_cannot_change_expected_output`） |
| T-RUN-003 | 2-50 | 安全 | nsjail(Linux) | fork 子进程后超时 | 整个进程树被清理，不残留、不跳用例 | **存在**（单元侧：`tests/test_runner_core.py::TestProcessTreeCleanup::test_timeout_kills_the_tree_exactly_once`（`kill_calls==1`、`killer.calls==[(pid,pgid)]`、`timeout`/`wall`、消息含 process tree cleanup）、`::test_tree_still_alive_after_kill_is_a_sandbox_violation`、`::test_posix_killer_sends_kill_to_the_process_group`；真实侧（**仅 Linux/容器内执行**）：`tests/linux/test_nsjail_acceptance.py::test_06_timeout_cleans_whole_process_tree`、`tests/linux/test_nsjail_runner_acceptance.py::test_i4_timeout_leaves_no_process_tree_residue`（存活 nsjail==[]、引用本工作区的存活进程==[]）。**弱项（如实记）**：`::test_one_failing_case_does_not_skip_the_others` 制造的是 `output_mismatch`，"**超时**也不跳用例"没有直接断言） |
| T-RUN-004 | 2-50 | 资源 | nsjail(Linux) | 超 CPU/内存/输出/文件/进程限制 | 归入 `failed` 或用例失败并记录限制项 | **存在**（输出：`tests/test_runner_core.py::TestEvidence::test_output_is_truncated_at_max_output_bytes`、`::TestClassification::test_output_limit_beats_output_mismatch`；文件数：`::test_created_files_limit_is_enforced` + 反例 `::test_created_files_limit_not_hit_below_the_cap`；进程/信号：`::test_failure_categories_are_prioritized`、`::test_negative_exit_code_is_a_resource_limit_with_a_finding`、`::test_sigkill_convention_128_plus_9`；CPU/内存（**仅 Linux/容器内执行**）：`tests/linux/test_nsjail_acceptance.py::test_05b_cpu_time_limit_is_enforced`、`::test_05a_memory_limit_is_enforced`、`::test_05a2_cgroup_v2_memory_max_kills`（本内核 xfail），真实归类见 `tests/linux/test_nsjail_runner_acceptance.py::test_i3_limit_and_hostile_samples_are_classified`。**口径**：限制项只作"已判定失败"的补充说明，见本文件 `T-RUN-011` 与 phase-01 第 26 条） |
| T-RUN-005 | 2-50 | 端到端 | 全链路 | 同提交重复评测 | 同分数、同证据摘要（可重放） | **存在**（`test_replay_same_bindings_same_awards_and_evidence`、`test_report_digest_discriminates_different_bindings`） |
| T-RUN-007 | 2-50 | 集成 | 真实 PostgreSQL | SSE 帧流的终态语义：全部读完；以及"已在终态 + 客户端已读到终态帧"的重连 | 序号严格单调、终态帧恰好一个且是最后一帧；**终态之后的重连立即结束**（不空等 `idle_timeout`），`after = 终态序号-1` 时补发那一帧 | **存在**（`test_progress_frames_are_monotonic_with_single_terminal_frame`、`test_frame_stream_returns_at_once_for_already_terminal_runs`） |
| T-RUN-008 | 2-50 | 安全 | Linux 容器（真 nsjail） | 合法提交经**真实 `run_case` + 真实 nsjail** 执行 | `passed=True`、`exit_code=0`、真实 stdout 被比较（证据来自真实进程执行） | **存在**（[`tests/linux/test_nsjail_runner_acceptance.py`](../../tests/linux/test_nsjail_runner_acceptance.py)::`test_i2_legal_submission_passes_inside_the_real_sandbox`；**仅 Linux/容器内执行**） |
| T-RUN-009 | 2-50 | 安全 | Linux 容器（真 nsjail） | 禁网 / 超时 / fork bomb / 内存 / 输出洪泛五个样本经真实 runner | 各自被拦截并留下证据：`security_blocked`+`runner.seccomp`（exit 159）/ `timeout`+`wall` / 程序观测到 fork 失败（EAGAIN）/ 内存申请失败且非零退出 / `resource_limit`+`output`；**超时后无存活的 nsjail、无引用本工作区的存活进程**（僵尸不在断言内，仅在容器无 `--init` 时打印记录） | **存在**（同上 `test_i3_limit_and_hostile_samples_are_classified`、`test_i4_timeout_leaves_no_process_tree_residue`；**仅 Linux/容器内执行**） |
| T-RUN-010 | 2-50 | 端到端 | Linux 容器 + 真实 PostgreSQL | 真实编排层 + 真实沙箱：同一提交跑两次 | 两次都 `completed`、分数相同、`result_digest` 相同 | **存在**（同上 `test_i5_real_pipeline_reaches_completed_with_a_score`；**仅 Linux/容器内执行**，不连库时该用例 skip 并写明原因） |
| T-RUN-011 | 2-50 | 安全 | Linux 容器（真 nsjail）+ 单元 | 沙箱侧归因的可信边界：`nsjail --log` 是否把沙箱自有诊断与学生流分开；学生能否伪造限制/违规归因 | ① 集成项真跑：学生字节**不落日志**、nsjail 自身失败的 `[E]`/`[F]` 诊断**不落**被采集的进程流（stderr 0 字节）；② 真跑伪造样张：学生打印 `nsjail: out of memory` / `nsjail: operation not permitted` 后归类仍是学生侧 `output_mismatch`、`resource_limit_hit=None`，且**从沙箱内往日志路径追加写入被拒**；③ 单测：同一标记串写在日志里算命中、写进学生流不算（`RunFacts.sandbox_log_text` 是唯一归因来源） | **存在**（[`tests/linux/test_nsjail_acceptance.py`](../../tests/linux/test_nsjail_acceptance.py) `test_i2_nsjail_log_separates_diagnostics_from_student_streams`、[`tests/linux/test_nsjail_runner_acceptance.py`](../../tests/linux/test_nsjail_runner_acceptance.py) `test_i8_student_cannot_forge_a_sandbox_limit_attribution`、`tests/test_runner_core.py::test_sandbox_diagnostics_are_read_from_the_log_file_not_the_student_stream`、`::test_student_text_cannot_forge_a_limit_attribution`、`::test_student_streams_are_recorded_verbatim_and_carry_no_sandbox_diagnostics`、`::test_sandbox_log_must_live_in_the_config_directory`、`::test_sandbox_log_is_a_per_run_artifact`） |
| T-RUN-006 | 2-50 | 单元 | runner 代码 | 审计启动参数 | 固定数组，无 shell 字符串拼接；且每次启动都声明 `--log`（沙箱自有日志在工作区之外），argv 不含 `--pass_fd`/`--log_fd` | **存在**（`test_argv_is_a_plain_list_of_strings`、`test_launcher_passes_list_argv_and_no_shell`、`test_sandbox_log_must_live_in_the_config_directory`（校验本体）、`test_sandbox_log_is_a_per_run_artifact`（调用侧不变量：日志在 `workspace.parent`，读后即删）） |

## 持久化与导出（RQ-B-2-60）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-PERSIST-001 | 2-60 | 集成 | PostgreSQL | 检查 run/result 记录 | 五项绑定摘要 + 时间戳 + 状态迁移史完整 | **存在**（五项绑定摘要：`tests/test_evaluation_pipeline.py::test_replay_same_bindings_same_awards_and_evidence`（五元组绑定、两次相等）＋`::test_all_cases_pass_scores_full_marks_and_completes`（逐列等于期望值 + `started_at`/`finished_at` 非空）；状态迁移史：`::test_run_transition_history_is_contiguous_and_reason_redacted`（`sequence==[1,2,3]`、`to_state==["queued","running","failed"]`）、`::test_illegal_run_transition_is_rejected_before_write`（非法迁移写入前被拒）；报告侧五项：`tests/test_b2_routes.py::test_teacher_report_view_contains_the_hidden_expected_output`。**弱项（如实记）**：没有单条测试同时断言"五列齐全 + 非空 + 等于期望值"——`submission_digest` 目前只在"两次相等"里被覆盖） |
| T-PERSIST-002 | 2-60 | 集成 | PostgreSQL | 制造失败迁移 | 每次迁移有记录，失败类别+脱敏原因 | **存在**（`::test_terminal_state_and_reason_must_be_a_contract_pair`（代码层/帧层/原始 SQL 三层都拒非法配对）＋迁移史与终态原因：`::test_run_transition_history_is_contiguous_and_reason_redacted`、`::test_illegal_run_transition_is_rejected_before_write`。**修正**：此前这里指向 `test_progress_frames_are_monotonic_with_single_terminal_frame`，那只读进度帧、不碰迁移史表，**不能**作为本项证据，已替换） |
| T-PERSIST-003 | 2-60 | 端到端 | 导出器 + 真实 PostgreSQL | XLSX/CSV 与 `GET /api/v1/grades` 对比（同筛选、逐页拉全） | 逐字段一致、中文列名与值不乱码 | **存在**（逐字段一致：`test_grades_pages_and_both_export_formats_agree_field_by_field`、`test_filters_narrow_the_export_too`、`test_query_and_export_agree_field_by_field`；中文不乱码：`tests/test_grades_query_export.py::test_csv_starts_with_utf8_bom_and_chinese_is_intact`、`::test_chinese_values_survive_both_formats`（后者即在下方 `B2-GRD-07`）） |
| T-PERSIST-004 | 2-60 | 单元 | 导出器 | `=`/`+`/`-`/`@`/Tab/CR 开头值 | 转义防公式注入（产物无 `<f>` 公式元素） | **存在**（`test_every_trigger_char_has_its_own_row_and_is_escaped_in_both_formats`、`test_exported_xlsx_has_no_formula_element_while_the_unescaped_probe_does`；见下方 `B2-GRD-08`） |
| T-PERSIST-005 | 2-60 | 集成 | 报告器 | DB 写入失败 | 不产生"报告已生成"假象 | **存在**（`tests/test_evaluation_pipeline.py::test_report_is_not_claimed_when_result_write_fails`，docstring 内即标注 `T-PERSIST-005`：注入 `persist_result` 抛错 ⇒ `failed`/`internal_error`、`report is None`、无 `evaluation_results` 行、`read_report` 返回 `None`、帧事件集只有 `FAILED`、outbox 无 `EVALUATION_COMPLETED`） |

## 安全判定（RQ-B-2-70）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-SEC-001 | 2-70 | 安全 | 静态规则集 | `rm -rf /`、反弹 shell、`curl\|bash`、`/etc/shadow`、无限 fork | 产生 `security_finding` 并阻断 | **存在**（`tests/test_security_decision.py::TestMaliciousCorpus::test_malicious_sample_is_found_and_blocking`（23 条参数化样本，五种模式各有正例与变体；断言 `status=="found"`、`blocking is True`、规则 ID 指名、`terminal_reason=="static_blocked"`）、`::test_corpus_covers_every_contract_category`、`tests/test_ss_probe_pipeline.py::test_probe_std2_real_rules_emit_security_findings_for_each_sample`、`::test_probe_std2_malicious_submission_is_rejected_without_any_runner_call`）。**口径（沿用 `REVIEW_L1_static_security.md` 的记录）**：测试断言的是 `decide_static` 返回的内存 `SecurityFinding`；端到端落库到的是 `b2.static_findings`，`b2.security_findings` 在该路径上只被打印不断言） |
| T-SEC-002 | 2-70 | 安全 | 编排层 + nsjail(Linux) | 联网/写只读路径/提权代码 | 捕获为安全事件（`b2.security_findings`，`detected_in=runtime`），评测 `rejected` 区分 `failed`，**且不给分** | **部分覆盖**（静态面三类各有真规则命中：`tests/test_security_decision.py` 的 `SEC-NETWORK-CALL`/`SEC-READONLY-WRITE`/`SEC-PRIVILEGE-ESCALATION` 样本；`rejected` 与 `failed` 的区分：`::TestBlockingGate::test_rejected_and_failed_are_distinguishable`、`tests/test_evaluation_pipeline.py::test_security_block_rejects_without_running_or_runner_calls`（静态阻断：rejected/security_blocked）对 `::test_unchecked_security_fails_closed_instead_of_scoring`（failed/infrastructure_error）。**运行期一侧 2026-09-18 补齐**：`tests/test_evaluation_pipeline.py::test_runtime_sandbox_violation_rejects_the_run_instead_of_scoring_it`（runner 侧 `security_blocked` 证据 ⇒ 真实编排器 `running → rejected + security_blocked`、违规落 `b2.security_findings`（runtime/high/blocking）、无结果行与报告、第 3 个用例未执行、不发布 `completed`/`failed`），并有第二道闸 `::test_scoring_refuses_a_security_blocked_case_instead_of_awarding_zero`。**缺**：真实沙箱内"写只读路径/提权"被捕获为**运行期**安全事件没有断言；`tests/linux/test_nsjail_acceptance.py::test_03_root_filesystem_is_read_only` 与 `::test_04_jailed_process_is_not_root` 只证明隔离生效，不断言产生事件/落行；真实 nsjail 里目前只有**禁网**被捕获（`tests/linux/test_nsjail_runner_acceptance.py::test_i3_limit_and_hostile_samples_are_classified`，**仅 Linux/容器内执行**）） |
| T-SEC-003 | 2-70 | 安全 | nsjail(Linux) | 发起外网请求 | 网络失败且被记录（不静默成功） | **存在**（`tests/linux/test_nsjail_acceptance.py::test_02_network_is_disabled`（TCP/UDP/DNS 三条都失败且"=OK"零命中）、`tests/linux/test_nsjail_runner_acceptance.py::test_i3_limit_and_hostile_samples_are_classified` 的禁网段（`security_blocked`+`runner.seccomp`，或程序自观测 `BLOCKED` 两种合法形态）、`tests/test_security_decision.py::TestNoExecution::test_analysis_of_side_effecting_sample_has_no_side_effects`；**仅 Linux/容器内执行**） |
| T-SEC-004 | 2-70 | 单元 | 安全结论模型 | 检查结论字段 | 区分"未发现"与"未检查" | **存在**（`tests/test_security_decision.py::TestThreeState::test_missing_checker_yields_not_checked_not_not_found`（`status=="not_checked"`、`!= "not_found"`、`unchecked==REQUIRED_CHECKS["python"]`、理由文本禁词"安全"/"通过"）、`tests/test_ss_probe_static_layer.py::test_probe_std3_not_found_and_not_checked_are_distinguishable`（两态并排 + `terminal_reason(unchecked)=="infrastructure_error"`）、`tests/test_evaluation_pipeline.py::test_unchecked_security_fails_closed_instead_of_scoring`、`tests/test_ss_probe_pipeline.py::test_probe_std3_not_found_completes_while_not_checked_fails`、`tests/test_frozen_interfaces.py` 的三态枚举） |
| T-SEC-005 | 2-70 | 安全 | 恶意样本集 | 跑完整回归集 | 全部被阻断；"真实"的含义按两段样本分别成立（见下） | **部分覆盖**（"全部被阻断"成立：23 条 corpus 全部 `found`+`blocking` 且覆盖 8 个契约类别，5 条规范样本过**真实编排器**全部 rejected、runner 零调用、`b2.static_findings` 有 blocking 行 —— `tests/test_ss_probe_pipeline.py::test_probe_std2_malicious_submission_is_rejected_without_any_runner_call`。**口径更正（2026-09-18）**：此前写"证据来自真实执行"是**夸大**——静态语料由 23 条规则**真的跑在样本上**判定（regex + `ast`），并**按设计不执行**样本（断言 `calls == 0`：RQ-B-2-40 验收 4 与 ADR-0001 要求高危样本在进入沙箱前被拒，"不执行"正是要验证的行为，不是缺口）。真实**执行**过的敌意样本集是另一段：`tests/linux/test_nsjail_runner_acceptance.py::test_i3_limit_and_hostile_samples_are_classified` 的 5 条（禁网 / 超时 / fork bomb / 内存 / 输出洪泛，其中禁网归 `security_blocked`），**仅 Linux/容器内执行**） |
| T-SEC-006 | 2-70 | 集成 | 真实 PostgreSQL 编排层 | runner 侧返回 `security_blocked` 证据（含运行期 finding） | 运行升格为 `rejected + security_blocked`：违规落 `b2.security_findings`（runtime/high/blocking）、无结果行/无报告、后续用例不执行、不发布 `completed`/`failed` | **存在**（`tests/test_evaluation_pipeline.py::test_runtime_sandbox_violation_rejects_the_run_instead_of_scoring_it`；第二道闸 `::test_scoring_refuses_a_security_blocked_case_instead_of_awarding_zero`。**本机可执行**：违规由替身 runner 以证据形态给出，判定的后半截（运行级终态 + 落库）跑真实数据库；前半截"真实沙箱判定出违规"仅 Linux 容器（T-RUN-009）） |

## 报告生成（RQ-B-2-80）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-REPORT-001 | 2-80 | 契约 | 报告器 | 查看完整报告 | 总评+静态+安全+逐用例+环境+绑定摘要 | **存在**（`tests/test_evaluation_pipeline.py::test_report_passes_main_contract_and_total_check`（`report_schema_errors==[]` + `validate_report` 过 MAIN schema 的六段 `required`，并显式断言 overall/case_evidence/binding/state）、`tests/test_b2_routes.py::test_teacher_report_view_contains_the_hidden_expected_output`（教师视图）、`::test_completed_path_is_fully_functional_on_a_freshly_migrated_database`（落库读回的报告仍过 schema）。**口径修正**：报告**没有独立的 `environment` 段**——环境信息由 `binding.environment_digest` + `binding.interpreter_version` 承载（MAIN schema 与环境键集均如此），"环境面"应按这两列读） |
| T-REPORT-002 | 2-80 | 单元 | 报告器 | 核对分数与证据 | 每分可追溯，合计校验成立 | **存在**（合计：`::test_report_passes_main_contract_and_total_check`（`sum(awards)==overall.score`、`matches_total is True`）+ 负例 `::test_report_total_check_refuses_a_mismatched_total`（`matches is False`、篡改 breakdown 抛 `ScoreTotalMismatch`）；可追溯：`::test_completed_path_is_fully_functional_on_a_freshly_migrated_database`（逐项 awarded ↔ 落库证据 ↔ 报告读回）、`::test_case_evidence_is_persisted_per_ordinal_and_unique`。**弱项（如实记）**：`report.py` 里"用例没有对应逐项得分/证据与得分指向不同用例"两条 `ReportRejected` **没有测试触发**） |
| T-REPORT-003 | 2-80 | 端到端 | 全链路 | 凭绑定摘要重跑 | 同分数 | **存在**（`::test_replay_same_bindings_same_awards_and_evidence`（docstring 即标注 T-REPORT-003/T-RUN-005：同 binding、同 awards、同证据摘要、同 `result_digest`）、`::test_report_digest_is_deterministic_for_same_inputs`、`::test_report_digest_discriminates_different_bindings`（区分力反证）、`tests/linux/test_nsjail_runner_acceptance.py::test_i5_real_pipeline_reaches_completed_with_a_score`（真沙箱内的重放，**仅 Linux/容器内执行**）） |
| T-REPORT-004 | 2-80 | 集成 | 认证 | 学生查询报告 | 不含参考答案/隐藏测试/他人代码 | **部分覆盖**（隐藏用例：`tests/test_b2_routes.py::test_student_report_view_hides_hidden_case_expected_output`（标记零命中、逐用例无 expected/actual 摘要、仍保留摘要）、`tests/test_evaluation_pipeline.py::test_student_view_hides_hidden_case_outputs`（默认 fail-closed、教师视图保留、裁剪后仍过 schema）；他人：`tests/test_b2_routes.py::test_read_routes_forbid_a_foreign_scope`（跨作用域读运行报告 403）＋成绩侧 `::test_students_cannot_query_or_export_another_students_grades`。**缺**：报告 JSON 面没有源码零命中断言（`SOURCE_MARKER` 零命中只出现在 SSE 帧与导出面），"参考答案"零命中只在导出面断言） |
| T-REPORT-005 | 2-80 | 契约 | schema 校验器 | 报告 JSON 过 schema | 校验通过 | **存在**（`::test_report_passes_main_contract_and_total_check`、`::test_completed_path_is_fully_functional_on_a_freshly_migrated_database`、`::test_all_cases_pass_scores_full_marks_and_completes`；**是真 schema 不是自写校验器**：`report_schema_errors` → `contract_schema_errors` → `jsonschema.Draft202012Validator(..., FormatChecker())` 直读 `MAIN/contracts/schemas/evaluation-report.schema.json`。**缺**：只有正向校验，**没有"坏报告被 schema 拒"的负例**） |

## 日志（RQ-B-2-99，已存在）

用例文件：[../../tests/test_logging_pipeline.py](../../tests/test_logging_pipeline.py)（真实链路：真实路由 + 真实 PostgreSQL +
真实 Ed25519 验签）、[../../tests/test_logging_redaction.py](../../tests/test_logging_redaction.py)（脱敏规则与渲染）、
[../../tests/test_events_outbox.py](../../tests/test_events_outbox.py)（事件侧依赖故障日志）。
契约依据：`MAIN/docs/api/identifiers.md` 第 5 节（`traceparent`/`X-Request-ID` 的接受与生成）、
[../architecture/interfaces.md](../architecture/interfaces.md) 第 6.4 节（失败语义）。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-LOG-001 | 2-99 | 单元 | 日志器 | 一条结构化日志经生产渲染链输出 | 单行**合法 JSON**，含 `timestamp`/`level`/`event`/`producer=B2`，且显式给出的 `request_id`/`correlation_id`/`submission_id`/`evaluation_run_id`/`stage` 不被丢弃 | **存在**（`test_log_lines_are_json_with_mandatory_fields`） |
| T-LOG-001b | 2-99 | 单元 | 日志器 | 传入非白名单/禁名字段（`source_code`/`expected_output`/`password`） | 字段**整段丢弃**，其值不进输出（`test_unknown_and_forbidden_fields_are_dropped_not_rendered`） | **存在** |
| T-LOG-002 | 2-99 | 安全 | 真实链路 | 把源码标记、token、内部绝对路径、请求头原值、文件内容分别注入五个入口 | 输出中**零命中**；`X-Request-ID`/`traceparent` 的非 UUID 形态被替换为新生成的 v4（不回写外部文本） | **存在**（`test_reception_log_carries_the_entry_ids`、`test_correlation_id_normalization_is_external_input_safe`） |
| T-LOG-003 | 2-99 | 集成 | 真实 PostgreSQL + 真实路由 | 同一次业务操作跑完整链路，按 `correlation_id` 检索 | 日志覆盖 `reception`→`static_analysis`→`security_decision`→`dynamic_execution`→`scoring`→`reporting`，且**共用同一个** `correlation_id` | **存在**（`test_single_correlation_id_links_every_stage`） |
| T-LOG-004 | 2-99 | 集成 | 故障注入（真实） | 沙箱不可用 / DB 不可用（两种形态）/ NATS 不可用 | 每条路径都有**明确日志 + 状态**，异常未被吞：`runner.sandbox_unavailable`（+ `failed`/`infrastructure_error`）、`persistence.database_unavailable`（+ 503 + 累计失败计数，见 T-HLT-01）、`grades.unavailable`（进入查询前被拒的 503）、`messaging.broker_unavailable`（+ 原样抛出 `EventBusUnavailable`，`error_count≥1`） | **存在**（`test_sandbox_unavailable_has_log_and_state_and_is_not_swallowed`、`test_database_unavailable_has_log_and_status`、`test_database_query_failure_has_log_and_status`、`test_broker_unavailable_has_log_and_status_and_is_not_swallowed`） |

> **如实标注的边界**：链路日志的"中文/文本内容"不进日志是靠**字段白名单**（只有标识、枚举与计数类字段可写）
> 实现的，不是靠事后扫描；DB 不可用只覆盖两种真实形态（库结构缺失 ⇒ 进入查询前拒绝；查询期缺表 ⇒
> `SQLAlchemyError`），**不**覆盖"连接整体不可达"——那条路会先撞上运行时探活的长超时（实测 ~130 s），
> 代价与判据无关，已在 phase-01 记录。

## readiness 的分项健康与失败计数（2026-09-20 新增）

用例文件：[../../tests/test_b2_routes.py](../../tests/test_b2_routes.py) 第 8 节（分项健康）与
[../../tests/test_reference_verification.py](../../tests/test_reference_verification.py)。
判定规则的权威说明在 `src/autograder_b2/runtime.py` 模块 docstring 的"失败计数与健康判定的关系"。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-HLT-01 | 2-99 / 运维可读性 | 集成 | 真实 PostgreSQL + 真实装配 | 装配好的运行时调 `record_failure()` 两次 | `failure_count==2`；`evaluation-chain` 分项仍是 `ok`、`healthy` 仍是 `True`；detail 里出现该计数且**同时**含"累计"与"不参与健康判定"（三件事必须一起断言：只断言 detail 抓不到"翻转标志"的改动，只断言 `healthy` 抓不到"计数被静默丢弃"） | **存在**（`test_b2_routes.py::test_recorded_endpoint_failures_are_reported_without_flipping_readiness`） |
| T-HLT-02 | 2-99 / 平台条件 | 集成 | 真实 PostgreSQL + 沙箱不可用的 `CaseRunner` 替身 | 参考解答验证返回 `503`（沙箱不可用） | `503` + 零存证 + **`failure_count == 0`**（已文档化的平台条件不计入"未预期失败"） | **存在**（`test_reference_verification.py::test_sandbox_unavailable_is_503_with_no_attestation`；真实链路的同一断言在 `::test_on_a_platform_without_a_sandbox_the_real_runner_returns_503`，仅 Windows 运行） |
| T-HLT-03 | 2-99 / 未预期故障 | 集成 | 真实 PostgreSQL + 两处上游声明不一致 | 参考解答验证返回 `503`（上游自相矛盾：**未预期**依赖故障） | `503` + **`failure_count == 1`**——T-HLT-02 的反向半边，防"路由压根不计数"让那条断言恒真 | **存在**（`test_reference_verification.py::test_language_disagreement_between_upstreams_is_503`） |
| T-HLT-04 | 2-99 | 单元/契约 | — | 三态聚合规则（`ok`/`not-configured`/`unavailable`）与消息代理的"未配置 vs DLQ 非空" | `healthy = 不存在 unavailable 项`；未配置项逐项出现在 detail 里且与"不可用"措辞区分 | **存在**（`test_b2_routes.py::test_health_state_ok_when_configured_and_usable`、`::test_health_state_not_configured_does_not_make_the_module_unhealthy`、`::test_health_state_unavailable_when_a_configured_dependency_is_broken`、`::test_message_broker_health_distinguishes_unconfigured_from_broken`） |

## 插件骨架（已存在）

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-PLUGIN-001 | 基线 | 单元 | 依赖已装 | 断言 descriptor | `module=="B2"`、`contract_version=="0.1.0"` | **存在**（[../../tests/test_plugin.py](../../tests/test_plugin.py)） |

## 组合层装配（集成期）

四个窄端口（`PackageSource` / `WorkspaceProvider` / `SecurityDecider` / `CaseRunner`）的生产实现，
以及两项由组合层负责的绑定摘要。用例文件：[../../tests/test_integration_adapters.py](../../tests/test_integration_adapters.py)。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-INT-001 | 2-50/2-60 | 契约 | 真实本地 HTTP 服务 | 取包成功路径 | URL 只由规范化 UUID 构成、凭据进 `Authorization` 头、`PackageView` 三项摘要与 package 一致 | **存在** |
| T-INT-002 | 2-50 | 契约 | 真实 HTTP 服务 | 返回 400/401/403/404/409/500/503 | 一律 `PackageUnavailableError`，且消息**不含**主机名/端口/URL | **存在** |
| T-INT-003 | 2-50 | 安全 | 真实 HTTP 服务 | 响应体超上限 / 非 JSON / 非 UUID 的题目版本 | 拒绝；非 UUID 时**不发请求**（不把输入拼进 URL） | **存在** |
| T-INT-004 | 2-50 | 安全 | 真实 HTTP 服务 | 上游返回**别的**题目版本的包 | 拒绝（不可信响应不得当作本次版本） | **存在** |
| T-INT-005 | 2-60 | 契约 | 契约形状 package | 校验三个不透明句柄 | `handle`/`adapter_handle`/`policy_handle` 与解析结果**同一对象**（runner 做严格相等校验） | **存在** |
| T-INT-006 | 2-60/2-80 | 单元 | — | 两项组合层摘要在各输入维度上的取值 | 同输入恒定、任一输入不同即不同；非法输入（未知语言/相对解释器路径/超字符集标签）**拒绝计算**而非取默认值 | **存在** |
| T-INT-007 | 2-50 | 安全 | 本机无 nsjail | 用真实 package 调 `CaseRunner` | 抛 `SandboxUnavailableError`（**绝无**裸进程执行路径）；沙箱可用时则必须真产出证据 | **存在** |
| T-INT-008 | 2-50 | 集成 | 真实 PostgreSQL + 真实 CAS | 物化工作区 | 内容逐字节一致、含子目录；每个用例各得**独立**工作区（不复用） | **存在** |
| T-INT-009 | 2-30 | 安全 | 真实 PostgreSQL + 真实 CAS | `release` 根目录本身/根之外路径；工作区根与 CAS 根互相包含 | 一律拒绝（不删任意路径；工作区根落在 CAS 根内会在构造期被拒，避免孤儿 GC 删掉正在执行的工作区） | **存在** |
| T-INT-010 | 2-30/2-50 | 安全 | 真实 PostgreSQL + 真实 CAS | 库中被写坏的路径（`../`、绝对、反斜杠、空串）与缺失内容 | 二次防线拒绝写出工作区之外；内容缺失明确报错而非静默跳过 | **存在** |
| T-INT-011 | 2-70 | 单元 | — | 安全判定端口的转发语义 | 高危样本 ⇒ `blocking` 且阻断门关闭；干净样本 ⇒ `not_found` 且门开启 | **存在** |

## 事件 outbox 与 inbox（已存在）

用例文件：[../../tests/test_events_outbox.py](../../tests/test_events_outbox.py)（真实 PostgreSQL + 真实 NATS JetStream；
不伪造 broker：broker 不可达时相关用例 skip 并写明原因，**逐用例**声明依赖，避免纯逻辑断言被一起静默跳过）。
契约依据：`MAIN/contracts/asyncapi/events.yaml`、`MAIN/contracts/schemas/event-envelope.schema.json`、
`MAIN/docs/api/events.md` §4/§5，以及本模块 [../architecture/interfaces.md](../architecture/interfaces.md) 第 6 节的
I29（先写后发）/ I30（at-least-once + `event_id` 去重）/ I31（同 run 顺序）/ I32（payload 结构性禁止）/ I33（DLQ 不静默）。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-EVT-001 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | outbox 行投递后从真实订阅端读回 | 投递进真实流，envelope 过 `event-envelope.schema.json` | **存在** |
| T-EVT-002 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | 按**逻辑顺序**入队 `started`/`progress`/`completed`，`enqueued_at` 刻意与插入顺序相反 | 投递顺序 = **入队顺序**（`insert_seq`），不按 `enqueued_at` 重排（原语义"按 enqueued_at 排序"已被 2026-09-16 组级复核的 50.2% 倒序实测推翻） | **存在**（`test_dispatch_preserves_insertion_order`） |
| T-EVT-002b | 2-60 | 集成 | 真实 PostgreSQL | 同一事务、同一时间戳连续入队两行（生产形态），重复 20 轮 | 每轮顺序都确定（`[started, progress]`），`insert_seq` 严格递增且互不相同 | **存在**（`test_same_transaction_enqueues_keep_their_order_despite_identical_timestamps`） |
| T-EVT-003 | 2-99 | 单元 | — | 5 类事件的规范 payload 序列化后扫描敏感标记；逐键投毒/加未知键/**逐个键各缺一次** | 不含源码/参考答案/隐藏用例/凭据/内部路径/PII；投毒、未知键、缺任一必填键都被拒（拒绝原因点名键、不回显取值） | **存在** |
| T-EVT-004 | 2-99 | 集成 | 真实 PostgreSQL + JetStream | 写入端 bug 把敏感内容写进 payload | 派发器**拒绝发布**、留行、记脱敏原因；日志按类型分派为 `events.outbox.payload_rejected`（**不得**报成 `messaging.broker_unavailable` 而稀释真故障） | **存在** |
| T-EVT-005 | 2-60/2-70 | 集成 | 真实 PostgreSQL + JetStream | 同一 `event_id` 的重复投递 | 业务效果只发生一次、inbox 只有一行 | **存在** |
| T-EVT-006 | 2-60/2-99 | 集成 | 真实 PostgreSQL + 真实不可达 NATS | 投递失败 → 恢复后重投 | 失败留行、`attempts` 递增、`last_error` 脱敏、**留一条依赖故障日志**（`messaging.broker_unavailable`，不含凭据/内部主机）；恢复后投递成功且 **`last_error` 清空** | **存在** |
| T-EVT-007 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | 达到投递上限 | 进 DLQ（可查询、不再自动重投）；人工重放后真实投递成功 | **存在** |
| T-EVT-008 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | `run` 中 handler 抛错 | 记进 `ConsumerReport.errors` + 回调 `on_error`；业务效果与去重记录一起回滚；同步 `consume` 直接抛出（不吞） | **存在** |
| T-EVT-009 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | handler 失败后同 durable 重投 | 消息未被 ack、`num_delivered` 递增、重试后成功 | **存在** |
| T-EVT-010 | 2-10 | 集成 | 真实 PostgreSQL | 不通过契约 schema 的消息 | 拒绝：不写 inbox、不产生业务效果 | **存在** |
| T-EVT-011 | 2-99 | 单元 | — | 脱敏器对真实输入/输出 | 凭据/内部主机/绝对路径/换行被替换；长度落在 `last_error` 的 CHECK 上限内 | **存在** |
| T-EVT-012 | 2-99 | 单元 | 本机无 broker | 真实连接失败 | 异常文本不含凭据、错误被记录（不丢弃）、状态如实为未连接 | **存在** |
| T-EVT-013 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | 无事件循环入口 `dispatch_sync` | 与 `dispatch` 语义一致（真实投递 + 真实读回），且**跨轮可重复调用** | **存在**（`tests/test_events_outbox.py::test_dispatch_sync_entry_point_publishes`、`::test_dispatch_sync_can_be_called_repeatedly_on_the_same_dispatcher`（同一 dispatcher 连投两轮、按 `insert_seq` 顺序读回两条，且返回后连接已关闭——连接留到下一轮就是"第 2 轮起永远发不出去"）） |
| T-EVT-014 | 2-99 | 单元 | 故障注入 | 异常文本带凭据 + 内部绝对路径 | 落库前必须被脱敏（`last_error` 与报告两侧） | **存在** |
| T-EVT-015 | 2-60 | 集成 | 真实 PostgreSQL + 不可达 NATS | 业务行与 outbox 行**同事务**写入后投递失败 | 业务事实不回滚；outbox 行保持未发布 | **存在** |
| T-EVT-016 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | 同 `(subject, deduplication_key)` 入队两次 | 只入队一次；已发布行不再被选中、流内无重复消息 | **存在** |
| T-EVT-017 | 2-60 | 集成 | 真实 PostgreSQL + JetStream | 未知字段 + 未识别 `event_type` 的消息 | 未知字段容忍；未识别类型**记一条结构化日志**后忽略（ack、不写 inbox、无业务效果） | **存在** |
| T-EVT-018 | 2-60 | 单元 | — | 从源码推导 `_finish_failure` 的可达终态原因，逐个过派发门 | 每个可达原因都能通过（原缺陷：`internal_error` 不在 `failure_category` 取值域内 ⇒ 该行永不投递、重试到上限进 DLQ） | **存在** |
| T-EVT-019 | 基线 | 单元 | 依赖已装 | 读 `module.yaml` 的 publishes/subscribes，与 `descriptor.events` 比较 | 集合相等、数量为 5 + 1、无重复（registry 的比较口径） | **存在**（[../../tests/test_plugin.py](../../tests/test_plugin.py)） |
| T-EVT-020 | 2-99 | 集成 | 真实 PostgreSQL + JetStream | 消费者**不传** `on_error` 钩子时 handler 抛错 | 失败仍进 `ConsumerReport`，且留下恰好一条结构化日志（`events.inbox.handler_failed`，原因已脱敏、不含 envelope 内容）；传了钩子时**不**重复记日志 | **存在** |
| T-EVT-021 | 2-60 | 单元 | — | `evaluation.progress` 的两种真实形状（带计数 5 键 / 不带计数 3 键）、逐个恒在键各缺一次，以及三条不变量（`REQUIRED_KEYS`/`PAYLOAD_KEYS`/已发布 subject 一一对应；必填 ⊆ 准许；除 progress 外相等） | 两种形状都能过门；缺 `evaluation_run_id`/`sequence`/`stage` 任一被点名拒绝；漏登记必填集的 subject 被 **fail-closed** 拒绝（不退化成"零必填"） | **存在** |
| T-EVT-022 | 2-10 | 集成 | 真实 PostgreSQL + JetStream + worker | worker 一轮（`run_once`）在有/无待发行两种情形 | 待发行真的被投递（`published_at` 非空且 broker 上能读回同一 `event_id`）；空 outbox 不连 broker；broker 不可达时行不丢、业务行不动 | **存在**（`tests/test_worker.py::test_run_once_publishes_pending_events_and_marks_them_published`、`::test_empty_outbox_does_not_touch_the_broker`、`::test_publish_failure_keeps_the_row_and_leaves_business_rows_untouched`、`::test_accepted_submission_is_evaluated_and_published_end_to_end`。**回归性质**：把投递改回"创建协程后丢弃"（旧实现）即失败） |

> `T-EVT-*` 是**真实**外部依赖的用例：PostgreSQL 与 JetStream 均按本模块 `docs/testing/strategy.md` 的分层要求真实启动，
> 不用 mock 充当交付。Windows 上无法验收的部分（Linux/nsjail）另见 [../../tests/linux/README.md](../../tests/linux/README.md)。

## 成绩查询与导出（RQ-B-2-60/2-80，已存在）

用例文件：[../../tests/test_grades_query_export.py](../../tests/test_grades_query_export.py)（服务层 + 渲染层，真实 PostgreSQL）与
[../../tests/test_b2_routes.py](../../tests/test_b2_routes.py)（**HTTP 层**：真实路由装配 + 真实 Ed25519 验签）。
契约依据：`MAIN/contracts/openapi/b2.yaml` 的 `listGrades`/`exportGrades`（含 `description` 与 `GradeRow`）、
[../api/contract.md](../api/contract.md) 第 4 节的错误码约定。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| B2-GRD-01 | 2-60 | 端到端 | 真实 PostgreSQL + 真实路由 | 按课程/课序/题目版本过滤，查询逐页拉全与导出比对 | 导出与查询**逐字段一致**（三种收窄都成立） | **存在**（`test_grades_pages_and_both_export_formats_agree_field_by_field`、`test_filters_narrow_the_export_too`） |
| B2-GRD-02 | 2-60 | 集成 | 真实 PostgreSQL | 游标分页走完全部数据（页大小 1/2/3） | 不重、不漏、行序与页大小无关 | **存在**（`test_cursor_pagination_is_complete_and_disjoint`、`test_grades_pagination_cursor_boundaries`；页序断言按**声明的全序** `(created_at ASC, submission_id ASC)` 用 `_expected_order()` 从库里重算，并对同毫秒的种子行显式断言 `submission_id` 的平局次序——不再假设"种子顺序"） |
| B2-GRD-03 | 2-60 | 负面 | 真实 PostgreSQL | 分页是 keyset 而非 offset；传 `offset` | `offset` **不生效**（与不传时响应一致） | **存在**（`test_pagination_is_keyset_not_offset`、`test_offset_is_not_a_paging_parameter`；同上按声明全序比对） |
| B2-GRD-04 | 2-60 | 负面 | 真实 PostgreSQL | 非法 `cursor`/`limit`/过滤；空串参数 | `422`；空串视为"未提供" | **存在**（`test_invalid_grade_filters_and_cursors_are_422`、`test_empty_query_parameters_mean_not_provided`） |
| B2-GRD-05 | 2-60 | 负面 | 真实 PostgreSQL | `queued`/`running`/`failed` 等未完成运行的呈现 | `score` 为 `null` 且带 `state`，**绝不是 0**；导出对应单元格为空 | **存在**（`test_unfinished_runs_are_null_score_with_state_and_never_zero`、`test_export_leaves_unfinished_score_cells_empty_not_zero`） |
| B2-GRD-06 | 2-60 | 负面 | 真实 PostgreSQL | 重跑后未完成 | 不得沿用上一轮分数 | **存在**（`test_rerun_supersedes_the_previous_score`） |
| B2-GRD-07 | 2-60 | 端到端 | 真实 PostgreSQL + 两种格式 | XLSX/CSV 与查询逐字段比对；中文列名与值 | 逐字段一致；CSV 首行 = `EF BB BF` + 13 个中文表头字节 + CRLF，无替换字符 | **存在**（`test_query_and_export_agree_field_by_field`、`test_csv_starts_with_utf8_bom_and_chinese_is_intact`、`test_chinese_values_survive_both_formats`） |
| B2-GRD-08 | 2-60 | 安全 | 导出器 + 真实产物字节 | 六个触发字符各占一行（两个自由文本列都带载荷） | 转义为 `'` + 原值；整包 `<f>` **零命中**、单元格 `data_type='s'`；**反向对照**证明不转义时确会成公式 | **存在**（`test_every_trigger_char_has_its_own_row_and_is_escaped_in_both_formats`、`test_exported_xlsx_has_no_formula_element_while_the_unescaped_probe_does`） |
| B2-GRD-09 | 2-80 | 安全 | 真实 PostgreSQL | 把源码/答案/隐藏用例标记写进 `report`/静态发现/逐用例证据 | 查询 JSON、CSV、XLSX 全部部件**零命中** | **存在**（`test_export_http_payload_never_carries_source_or_hidden_cases`、`test_export_never_leaks_report_or_hidden_cases`） |
| B2-GRD-10 | 2-60 | 安全 | 真实路由 + 真实 JWT | 学生指名他人（查询/导出）、教师越界课序、管理员/覆盖教师 | 越权 `403`（响应体不回显他人标识、存在的他人与不存在的他人响应形状相同）；授权者 `200` | **存在**（`test_students_cannot_query_or_export_another_students_grades`、`test_export_enforces_the_same_scope_rules_as_the_query`、`test_cross_course_and_cross_section_are_403`） |
| B2-GRD-11 | 2-60 | 回归 | 导出器 | 跨秒两次导出同一批行；不同行 | 同输入**逐字节相同**；不同输入字节不同；重打后的包仍是合法工作簿（冻结窗格/数字格式/列宽不变） | **存在**（`test_render_xlsx_is_byte_identical_for_the_same_rows_across_a_second_boundary`、`test_repacked_xlsx_is_still_a_valid_workbook_with_the_same_visible_settings`） |
| B2-GRD-12 | 2-60 | 负面 | 真实路由 | 缺 `course_id`、未认证、依赖缺失、库未迁移 | 分别 `422` / `401` / `503` | **存在**（`test_grades_require_authentication_and_course_id`、`test_read_routes_are_503_on_an_unmigrated_database`） |
| B2-GRD-13 | 2-60 | 回归 | 真实 PostgreSQL + 真实路由（导出层时间不变量） | 一条**真实入库**的行（`timestamptz(3)` 已把 `.123999` 舍成 `.124000`）与一条**合成**微秒行（`.123999`），分别比对 JSON / CSV / XLSX 三处时刻 | 同一条记录的三个出口**给出同一个时刻字符串**；导出层的截断对库值幂等，且对非毫秒对齐的源值仍截断（不四舍五入） | **存在**（`test_the_stored_instant_is_reported_identically_by_json_csv_and_xlsx`、`test_xlsx_truncates_like_the_api_when_the_source_carries_microseconds`） |

> **如实标注的两处边界**（不写成"通过"）：
> ① 中文**值**无法经 HTTP 到达（`student_number`/`display_name` 由 B4 提供、B2 侧恒为 `null`；其余文本列是
> UUID/枚举），因此 HTTP 层只覆盖中文**列名**（字节级），中文值只在渲染层与真实 Excel 上验证；
> ② 公式注入的防线在**渲染层**（DB 无可承载触发字符的列：`state` 等有 CHECK 约束），故端到端链条为
> "渲染层真实字节 + HTTP 响应字节 == 渲染器字节 + 整包扫描"，未在生产代码里为测试开注入点。

## 评语轨（RQ-T-20，2026-09-18 新增）

用例文件：[../../tests/test_commentary_track.py](../../tests/test_commentary_track.py)（真实 PostgreSQL + 真实
Ed25519 验签 + 真实路由装配；provider 侧用一个返回 OpenAI 兼容响应的**传输替身**，因此全程无网络）。
契约依据：`MAIN/contracts/openapi/b2.yaml` 的 `getEvaluationCommentary`/`requestEvaluationCommentary` 与
`EvaluationCommentary`/`CommentaryProvenance`；裁决 [../../../MAIN/docs/adr/ADR-0009-ai-assistance-two-track.md](../../../MAIN/docs/adr/ADR-0009-ai-assistance-two-track.md)。

**这批断言的意义在于"绿套件本身证明不了什么"**：白名单不是靠"我们没读源码"的注释，而是靠
"实际读到的字段集 == `INPUT_SCOPE`"；分数独立不是靠"评语表不参与计算"的说明，而是靠
"清空评语表后重放，分数与逐项证据逐字相同"。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-CMT-001 | T-20.1 | 安全 | 真实 PostgreSQL | 跑完一次真实编排后取证，枚举载荷里**实际出现**的字段路径 | 字段路径集合**逐项等于** `INPUT_SCOPE`，一个不多一个不少 | **存在**（`test_collected_evidence_reads_exactly_the_declared_whitelist`；纯结构侧 `test_input_scope_matches_the_columns_the_queries_select`） |
| T-CMT-002 | T-20.1 | 安全 | 真实 PostgreSQL | 把标记串种进受保护列（摘录、消息、期望输出），再取证并渲染 prompt | 四个标记在证据载荷与 prompt 里**零命中**，同时先断言标记**确实在库里**（否则断言是恒真） | **存在**（`test_protected_content_never_reaches_the_evidence_or_the_prompt`） |
| T-CMT-003 | T-20.1 | 安全 | 真实 PostgreSQL | 把 CAS 读、信封装载、package 解析全部换成"一调用就抛异常"的替身，再生成 | 生成**成功**：这些访问器在生成路径上不可达 | **存在**（`test_source_accessors_are_unreachable_from_the_generation_path`）；静态侧另有 `test_commentary_import_closure_excludes_every_source_accessor` 与 `test_generate_signature_takes_no_source_like_input` |
| T-CMT-004 | T-20.2 | 集成 | 真实 PostgreSQL | 生成一条评语 → `DELETE FROM b2.evaluation_commentaries` → 用同一提交重放一次 | 分数、`result_digest`、逐项证据、绑定摘要、合计校验**逐字相同** | **存在**（`test_deleting_commentary_leaves_the_score_and_per_item_evidence_identical`） |
| T-CMT-005 | T-20.2 | 集成 | 真实 PostgreSQL | 把 `CommentaryService.generate` 换成抛异常的替身，跑完整编排 | 评测照常 `completed` 且给出分数；评语表零行（评测链路不调用评语轨） | **存在**（`test_evaluation_pipeline_never_calls_the_commentary_track`） |
| T-CMT-006 | T-20.6 | 集成 | 真实 PostgreSQL + 传输替身 | 未配置 provider：`GET` + `POST` 各一次 | `GET` → `200` + `unavailable`（无分数/无证据字段）；`POST` → `409` 且 `detail` 指名 `B2_COMMENTARY_*`；**传输替身从未被调用** | **存在**（`test_unconfigured_deployment_makes_no_outbound_call`、`test_half_configured_deployment_still_makes_no_call_and_names_the_gap`） |
| T-CMT-007 | T-20.3/4 | 集成 | 真实 PostgreSQL + 传输替身 | 生成评语 | `202` + `status: ready` + provenance（provider/model/prompt_version/`input_scope` == 白名单）；随后 `GET` 读回同一份；prompt 里只有白名单证据 | **存在**（`test_generation_returns_provenance_with_the_audited_input_scope`） |
| T-CMT-008 | T-20.5 | 集成 | 真实 PostgreSQL + 传输替身 | 连续生成两次（第二次 provider 返回不同文本） | 覆盖同一行（表里仍只有 1 行）、文本更新、`evaluation_results` 的分数与摘要**未变** | **存在**（`test_regeneration_overwrites_and_never_touches_the_score`） |
| T-CMT-009 | T-20.5 | 集成 | 真实 PostgreSQL + 失败传输 | provider 返回 502 | `POST` → `503` 且原因含 `HTTP 502`（不回显响应正文）；`GET` → `status: failed`；分数行**逐字段未变** | **存在**（`test_provider_failure_marks_only_the_commentary`、`test_provider_error_never_echoes_the_response_body`） |
| T-CMT-010 | T-20 | 契约/授权 | 真实 PostgreSQL + 真实 JWT | 401 / 学生 `POST` / 跨作用域 / 不存在的运行 / 非终态运行；响应体过契约 schema | 分别 `401` / `403` / `403`（**不是 404**）/ `404` / `409`；`ready`（**含参考分的那一份**）与 `unavailable` 两种响应体都逐字过 `EvaluationCommentary` | **存在**（`test_commentary_requires_authentication_and_teacher_role_for_generation`、`test_generation_on_a_non_terminal_run_is_409`、`test_commentary_responses_validate_against_the_contract_schema`、`test_commentary_reports_pending_state_honestly_before_generation`） |

## AI 评分参考（RQ-T-30 / CHG-0018 §7，2026-09-20 新增）

用例文件：同一个 [../../tests/test_commentary_track.py](../../tests/test_commentary_track.py)（真实 PostgreSQL +
真实路由装配 + 传输替身，全程无网络）。契约依据：`MAIN/contracts/openapi/b2.yaml` 的 `AdvisoryScore` 与
`EvaluationCommentary.advisory_score`；裁决 [../../../MAIN/docs/adr/ADR-0014-ai-advisory-score.md](../../../MAIN/docs/adr/ADR-0014-ai-advisory-score.md)、
变更 [../../../MAIN/docs/contracts/changes/CHG-0018-ai-advisory-score.md](../../../MAIN/docs/contracts/changes/CHG-0018-ai-advisory-score.md)。

**这批用例逐条对应 `CHG-0018` §7 的五条实现侧验证**，并补上"协议就是协议"（§5）与验收 7（标注）。

| 用例 ID | 目标 RQ | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-ADV-001 | T-30.1 / §7.1 | 契约 | 真实 PostgreSQL | 取真实报告 → 过 `evaluation-report` schema；再注入 `advisory_score`（对象与 `null` 两种） | 原始报告零错误；注入后**必须被拒**；落库报告里也没有该键 | **存在**（`test_advisory_score_is_rejected_by_the_evaluation_report_schema`） |
| T-ADV-002 | T-30.1 / §7.1 | 集成 | 真实 PostgreSQL + 传输替身 | 生成一条**带参考分**的评语（`rationale` 里带标记串），随后读报告、`/api/v1/grades`、`/api/v1/grades/export`、落库报告与全部 outbox payload | 五个面上"评语文本、参考分依据、`advisory` 键名"**零命中**；且先断言 outbox 里确实有事件（否则断言恒真） | **存在**（`test_report_never_carries_a_commentary_value`） |
| T-ADV-003 | T-30.1 / §7.1 | 结构 | — | 扫描 SQLAlchemy 映像里所有表，找列名含 `advisory` 的 | 只有 `evaluation_commentaries` 有四列；`evaluation_results`/`case_evidence` 等连列名都没有 | **存在**（`test_no_table_other_than_the_commentary_can_carry_an_advisory_value`） |
| T-ADV-004 | T-30.3 / §7.2 | 集成 | 真实 PostgreSQL | 真实生成出参考分 → 重放同一提交（评语行仍在）→ `DELETE FROM b2.evaluation_commentaries` → 再重放 | 三次运行的分数、`result_digest`、`overall`、逐项证据、绑定摘要、合计校验**逐字相同**；参考分依据不出现在报告里 | **存在**（`test_advisory_score_row_does_not_change_the_replayed_score_or_digest`） |
| T-ADV-005 | T-30.8 / §7.3 | 集成 | 真实 PostgreSQL + 抛异常的传输替身 | 未配置 provider：`GET` + `POST` 各一次 | `GET` → `200` + `unavailable` 且 `advisory_score is None`（参考分标注仍在）；`POST` → `409`；**传输替身从未被调用** | **存在**（`test_unconfigured_deployment_makes_no_outbound_call`，本批已扩到参考分与标注） |
| T-ADV-006 | T-30.6 / §7.4 | 集成 | 真实 PostgreSQL + 传输替身（5 组越界值） | 模型给 `-0.5` / `-1` / `10.001` / `11` / `999`（满分 10） | `POST` → `503` 且原因含"越界…不做截断"；`GET` → `failed`；落库四列**全空**（既没有 10 也没有 0 被悄悄写入）；`evaluation_results` 行逐字未变 | **存在**（`test_out_of_range_advisory_score_fails_the_generation_instead_of_clamping`，5 个参数） |
| T-ADV-007 | T-30.5 / §7.5 | 集成 | 真实 PostgreSQL + 传输替身 | 生成 `score=7.5` 的参考分 | `max_score == b2.case_evidence.weight 之和 == 10`，也等于 `evaluation_results.max_score`；prompt 里明确"不要给出满分" | **存在**（`test_advisory_max_score_is_filled_from_the_recorded_case_weights`） |
| T-ADV-008 | T-30.5 / §7.5 | 集成 | 真实 PostgreSQL + 传输替身 | 模型在 `advisory_score` 里**自带** `max_score: 999` | 整次生成 `failed`；四列全空——模型给的满分没有进入数据的路径 | **存在**（`test_a_model_supplied_max_score_is_refused_rather_than_used`） |
| T-ADV-009 | T-30.6 / §5 | 集成 | 真实 PostgreSQL + 传输替身（6 组畸形响应） | 旧协议纯文本 / JSON 数组 / 缺 `commentary` / 平级 `score` / `NaN` / 非法 `confidence` | 一律 `POST 503` + `GET status: failed` + `text is None`（**没有**退化成"把整段响应当评语"）；标记串不出现在响应与失败原因里 | **存在**（`test_malformed_model_response_is_a_failure_not_a_text_fallback`，6 个参数） |
| T-ADV-010 | T-30.7 | 集成 | 真实 PostgreSQL + 传输替身 | 两次生成都不产生参考分（一次不给该键、一次明确 `null`） | `status: ready` 且 `advisory_score is None`，`disclaimers` **仍含**针对分数参考的那条 | **存在**（`test_advisory_disclaimer_is_present_even_when_no_advisory_score_was_produced`） |
| T-ADV-011 | T-30.6 / §5 | 结构 | — | 直接调用 `parse_generation`（不需要数据库） | 约定形状通过（含"只有评语"与"参考分为 `null`"两种合法形态）；10 种违例各自抛 `CommentaryResponseError` | **存在**（`test_advisory_protocol_helpers_reject_shape_violations_without_a_database`） |

本批同时更新了三条既有用例，使 `RQ-T-20` 的行也覆盖新协议：T-CMT-004（重放时评语行**带上参考分四列**）、
T-CMT-007（断言 `prompt_version` 等于 `b2-commentary/2` 且 prompt 含 JSON 协议）、T-CMT-008（重新生成覆盖参考分）。

## 参考解答验证（`CHG-0019` + `CHG-0023`，2026-09-20 新增）

用例文件：[../../tests/test_reference_verification.py](../../tests/test_reference_verification.py)（真实 PostgreSQL +
真实 JWT + **真实路由与装配**；B3 两个调用与取包端口是明确标注的替身，因此全程无网络）。
契约依据：`b2.yaml` 的 `verifyReferenceAnswer` / `ReferenceVerification`、`b3.yaml` 的
`getReferenceAnswer`（`CHG-0023`）与 `recordReferenceAttestation`（`CHG-0008`）；
裁决 [../../../MAIN/docs/adr/ADR-0015-reference-verification-orchestration.md](../../../MAIN/docs/adr/ADR-0015-reference-verification-orchestration.md)。

**这批断言逐条对应 `CHG-0019` §7 的六条**，并补上"不落库"与"内部缺陷不伪装"两条。

| 用例 ID | 目标 | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-REF-001 | §7.5 键集合 | 契约 | — | 读 `b2.yaml` 的 `ReferenceVerification` | `required` 恰为九个键；`cases[]` 恰为 `ordinal/passed/failure_category`；**没有** `reference_answer`/`expected_stdout`/`test_cases`/`stdin` 槽位 | **存在**（`test_response_key_set_matches_the_contract_verbatim`） |
| T-REF-002 | §2.6 词表 | 契约 | — | 比对 `cases[].failure_category` 的枚举、`evaluation-report` 的枚举与 `FailureCategory` | 三者相等（8 值；`null` 只是可空写法差异） | **存在**（`test_failure_category_vocabulary_is_the_report_vocabulary`） |
| T-REF-003 | `CHG-0023` §4 | 安全 | — | 断言 `reference_answer` 在脱敏名单里；再让 `sanitize_fields` 处理一条含它的记录 | 字段被整条丢弃，取值不出现在结果里 | **存在**（`test_reference_answer_is_a_forbidden_log_field`） |
| T-REF-004 | 执行路径唯一 | 结构 | — | 扫 `reference/*.py` 的 import | 不含 `subprocess`/`multiprocessing`/`ctypes`/`pty`/`posix_spawn`（不存在第二条执行路径） | **存在**（`test_reference_package_never_spawns_a_process`） |
| T-REF-005 | 不落库 | 结构 | — | 检查服务层构造参数 | 只有参考解答客户端/取包/runner/工作区/闸门/时钟——**拿不到引擎或连接** | **存在**（`test_service_has_no_storage_dependency`） |
| T-REF-006 | §7.1 授权 | 单元 | — | 学生、无角色、无课程作用域的教师、正规教师、管理员 | 前三种抛 `AuthorizationError`；后两种通过 | **存在**（`test_authorization_helper_rejects_students_and_scope_less_staff`） |
| T-REF-007 | §7.1 | 接口 | 真实路由 | 无令牌 `POST` | `401` + Problem Details | **存在**（`test_unauthenticated_request_is_401`） |
| T-REF-008 | §7.1 | 接口 | 真实 JWT | 学生 `POST`、无课程作用域的 staff `POST` | 都 `403` | **存在**（`test_students_and_scope_less_staff_are_forbidden`） |
| T-REF-009 | 缺口 ⑦ | 接口 | 真实 JWT | **另一门课**的教师 `POST` | 当前返回 `200`——**已知缺口的记录用例**（题目→课程是 B3 的事实，B2 调 B3 用服务令牌）；不写成"已收窄" | **存在**（`test_course_scope_gap_is_recorded_not_hidden`） |
| T-REF-010 | 请求体 | 接口 | 6 组坏体 | 非 JSON / 数组 / 多字段 / 空对象 / 非字符串 / 非 UUID | 一律 `422`，且不回显取值 | **存在**（`test_request_body_is_validated`） |
| T-REF-011 | §7.2 前半 | 集成 | 替身 200/201 | 两个用例全通过 | `200`；`cases_total == cases_passed == 2`；`attestation_id` 非空；`verified_at` 与送给 B3 的**逐字相同**；`content_digest` 原样转交；取参考解答与存证**同一个 base_url** | **存在**（`test_all_cases_passing_records_the_attestation`） |
| T-REF-012 | 执行复用 | 集成 | runner 替身 | 观察每次 `run_case` 的工作区 | 每个用例一次调用、各一份新工作区、入口脚本内容**就是参考解答正文**；结束后工作区已删除 | **存在**（`test_each_case_goes_through_the_injected_runner`） |
| T-REF-013 | §7.3 前半 | 集成 | 替身 | 一个通过、一个 `output_mismatch` | `200`；`cases_passed=1`；`attestation_id: null`；**零存证调用** | **存在**（`test_a_failed_case_produces_no_attestation`） |
| T-REF-014 | 安全结论不落库 | 集成 | 替身 + 真实库 | 参考答案被沙箱判 `security_blocked` | 逐用例如实报告该类别；零存证；`b2.security_findings` 零行（**不落库**对安全结论同样适用）；其余用例照常执行 | **存在**（`test_security_blocked_case_is_reported_without_persisting`） |
| T-REF-015 | §7.3 契约 | 集成 | 3 组记录失败 | 上游 `409` / `500` / 网络不可达 | `200` + `attestation_id: null` + **逐用例结论照旧返回** | **存在**（`test_attestation_recording_failure_returns_null_but_still_returns_the_cases`） |
| T-REF-016 | 记录"读不回来" | 集成 | 3 组坏 `201` | 上游 `201` 但响应非 JSON / 非对象 / 无 `attestation_id` | `503`——**不**写成 `null`（`null` 的契约含义是"没有记录"，而我们并不知道） | **存在**（`test_an_unreadable_attestation_response_is_a_dependency_failure`） |
| T-REF-017 | §7.6 | 集成 | runner 抛 `SandboxUnavailableError` | 无沙箱平台 | `503` + `detail` 指名沙箱 + **零存证** | **存在**（`test_sandbox_unavailable_is_503_with_no_attestation`） |
| T-REF-018 | 上游分流 | 集成 | 11 组 | 取参考解答：`404`/`409`/`500`/`403`/网络/坏 JSON/数组/坏 UUID/坏摘要/未知语言/未知 adapter | 分别 `404`/`422`/`503`×9；**零存证** | **存在**（`test_reference_answer_upstream_failures_map_to_contract_statuses`） |
| T-REF-019 | 缺口 ⑧ | 集成 | 4 组取包状态码 | 取包 `404`/`409`/`500`/`403` | `404`/`422`/`503`/`503`；`409` 的 `detail` 指名"没有 evaluation package"——**草稿即如此，因此"验证草稿"当前不可行** | **存在**（`test_evaluation_package_failures_map_to_contract_statuses`） |
| T-REF-020 | 两侧声明一致 | 集成 | 替身 | 包声明 `bash`、参考解答声明 `python` | `503`（挑一个继续会跑错解释器）+ 零存证 | **存在**（`test_language_disagreement_between_upstreams_is_503`） |
| T-REF-021 | §7.5 | 集成 | 真实路由 | 全通过后检查响应文本与 schema | 参考解答标记与期望输出标记**零命中**；键集合 == 九项；逐字过 `ReferenceVerification` | **存在**（`test_response_carries_no_protected_content`） |
| T-REF-022 | **不落库** | 集成 | 真实库 | 全通过跑完后逐表计数 | `b2` 全部业务表**零行**；工作区根下无残留目录 | **存在**（`test_verification_writes_nothing_to_the_database`） |
| T-REF-023 | 缺陷不伪装 | 集成 | runner 替身给出"通过却带失败类别" | 内部不一致 | 宿主 `500`（**不**归一成 `failure_category: null`） | **存在**（`test_an_inconsistent_evidence_row_is_a_defect_not_a_case_failure`） |
| T-REF-024 | `ADR-0015` 复用 | 装配 | 真实装配 | 比对通道与闸门对象 | `references.b3_endpoint is b3_endpoint`、`references.gate is evaluator.gate`（容量 1）⇒ 无新增凭据、并发上限共用 | **存在**（`test_wiring_shares_the_b3_channel_and_the_sandbox_gate`） |
| T-REF-025 | 缺配置 | 接口 | 去掉 B3 变量 | 装配后调端点 | `503` + 可读原因（不返回"看起来跑过"的空结果） | **存在**（`test_runtime_without_the_chain_reports_503_for_the_verification_route`） |
| T-REF-026 | §7.6 真实链路 | 集成（**Windows 专属**） | 真实取包解析 + 真实 `run_case` + 真实沙箱探测（只换两个 HTTP 边界） | 全通过前的最后一步 | `503` + `detail` 指名沙箱 + 零存证；Linux 上 skip 并给出理由 | **存在**（`test_on_a_platform_without_a_sandbox_the_real_runner_returns_503`） |
| T-REF-027 | 替身守卫 | 结构 | — | 直接用脚本替身 | 脚本化异常真的抛、脚本化响应真的回；四类异常的继承关系 | **存在**（`test_the_script_double_raises_or_returns_as_scripted`） |
| T-REF-028 | **自调用死锁回归**（真实端口发现的第三个缺陷） | 接口 | 真实装配 + 中间件线程探针 | 中间件记下事件循环线程，B3 传输替身记下它被调用的线程 | 两者**必须不同**（阻塞调用已下线）；相同即失败并给出"在真实宿主上这里会自我等待到超时"的诊断。**先红后绿已实跑**（把那一行临时改回直接调用即失败） | **存在**（`test_the_blocking_b3_call_runs_off_the_event_loop`） |
| T-REF-029 | **`409` 原因转述**（真实端口发现的第四个缺陷） | 集成 | 替身返回 B3 的 `409` + Problem Details | 上游原因 = `this version has no content digest yet…` | `422` 的 `detail` **包含上游原话**；**反向断言**不得出现 `carries no reference answer` / `no reference answer to verify`；零存证 | **存在**（`test_a_reference_answer_refusal_relays_the_upstream_reason`） |
| T-REF-030 | 无原因不猜 | 集成 | 替身返回 `409` 但体里没有 `detail` | — | `422` 明说"上游没有给出原因"，且**不**声称任何具体原因（不含 `no reference answer` / `not been published`） | **存在**（`test_a_reference_answer_refusal_without_a_reason_does_not_guess`） |
| T-REF-031 | 取包 `409` 转述 | 集成 | 替身携带 `upstream_detail` | 上游原因 = `a draft version has no evaluation package; publish it first` | `422` 的 `detail` 含该原话；零存证 | **存在**（`test_a_package_refusal_relays_the_upstream_reason`） |
| T-REF-032 | 取包错误保真（消息侧） | 单元（**真 HTTP 服务**） | 文件：`tests/test_integration_adapters.py`；服务返回 `409` + Problem Details | 两种体：完整 Problem Details / 只有 `detail` | `upstream_detail` 等于体里的 `detail`，且**异常消息里不出现它**（日志侧仍只记状态码） | **存在**（`test_problem_details_detail_is_carried_but_not_echoed_in_the_message`） |
| T-REF-033 | 取包错误保真（形状侧） | 单元（**真 HTTP 服务**） | 6 组非契约形状（`{}` / 非 JSON / 空串 / 数字 / 数组 / 超长） | — | `upstream_detail is None`（超长**丢弃而不是截断**：半句话会被当成上游的原因） | **存在**（`test_non_problem_details_bodies_yield_no_upstream_detail`） |

**未覆盖（如实列出）**：`publish` 成功/被拒、编辑内容后旧存证失效（`CHG-0019` §7.2 后半 / §7.3 后半 / §7.4）
——需要真实 B3，且被缺口 ⑧（草稿取不到 evaluation package）挡住；真实沙箱下的"全通过"读数需要 Linux。
逐条见 [phase-07](../phases/active/phase-07-reference-verification.md) 的"未执行 / 未验收"。

## 逐用例证据补全（CHG-0012 / RQ-B-0-30 验收 2，2026-09-18 新增）

用例文件：[../../tests/test_case_evidence_details.py](../../tests/test_case_evidence_details.py)（纯逻辑部分不依赖数据库，
因此在没有 PostgreSQL 的环境里也**真的在跑**，而不是整文件被 skip）。
契约依据：`MAIN/contracts/schemas/evaluation-report.schema.json` 的 `case_resource`/`case_artifacts`/`file_change_list`；
裁决 [../../../MAIN/docs/adr/ADR-0010-sandbox-layering-and-proof.md](../../../MAIN/docs/adr/ADR-0010-sandbox-layering-and-proof.md)。

| 用例 ID | 目标 | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-EVID-001 | 2-50 资源实测 | 资源 | 合成 cgroup v2 目录树 | 造出"有 `cpu.stat`、缺 `memory.peak`/`pids.peak`"的宿主形态并读取 | 缺的那一项是 `null`，**不是 0**；读到的项按真实数值填入 | **存在**（`test_cgroup_reader_parses_peaks_and_reports_missing_files_as_none`） |
| T-EVID-002 | 2-50 资源实测 | 资源 | 无 cgroup 的宿主 | 目录不存在时读取并合成 `CaseResource` | 四个 cgroup 字段全 `null`、`wall_seconds` 有值（B2 自测）；契约载荷里也是 `null` 而非 0 | **存在**（`test_cgroup_reader_never_invents_a_zero`、`test_limit_hit_mapping_covers_the_contract_enum`） |
| T-EVID-003 | 2-50 资源实测 | 单元 | — | 定位逐用例 cgroup（`/proc/<pid>/.../children` + `NSJAIL.<child_pid>` 必须存在）；改资源读数 | 找不到目录即 `None`（不猜）；**改资源读数不得改变证据摘要与报告摘要**（环境量不进可重放等式） | **存在**（`test_case_cgroup_directory_uses_the_child_pid_and_requires_the_directory`、`test_resource_is_excluded_from_the_replay_digests`、`test_cgroup_presence_check_is_honest_on_a_host_without_cgroup`） |
| T-EVID-004 | 0-30.2 文件证据 | 集成 | 真实文件系统 | 前后两次快照：新建/修改/删除各一个文件 | 三张清单路径正确；**新建与修改带内容摘要与字节数；删除的摘要取自运行前快照**（事后不可能再算） | **存在**（`test_workspace_changes_record_paths_digests_and_sizes`） |
| T-EVID-005 | 0-30.2 文件证据 | 安全/回归 | 真实文件系统 | 运行期间产生 `__pycache__/*.pyc`；超过契约上限；超出哈希预算 | 字节码缓存**不进**清单（其头里带 mtime ⇒ 纳入即毁掉可重放性）；超上限时截断并置 `truncated`；超预算的文件只记大小、摘要为 `null` | **存在**（`test_workspace_changes_skip_interpreter_bytecode`、`test_workspace_change_lists_are_bounded_and_flag_truncation`、`test_hash_budget_marks_unhashed_files_instead_of_lying`） |
| T-EVID-006 | 受保护值 | 安全 | — | 教师视图 vs 学生视图 | 学生视图里 `artifacts.expected_sha256` 为 `null`（受保护），`actual_sha256` 与三张清单保留；`truncated` 不进契约载荷 | **存在**（`test_artifacts_payload_and_student_view_protection`、`test_artifacts_are_absent_when_the_case_never_executed`） |
| T-EVID-007 | 2-80 解释 | 单元 | — | 五种失败形态各生成解释；同一证据两次生成 | 输出不符（**指名首个分歧行**）/ 非零退出 / 超时 / 缺产出文件 / 安全阻断都有可读说明；同输入同输出；文本里只有行号/路径/计数 | **存在**（`test_explanation_covers_every_required_failure_shape`、`test_explanation_is_deterministic_and_content_free`、`test_first_differing_line_is_one_based_and_handles_length_differences`） |
| T-CANARY-001 | ADR-0010.2 自证 | 诚实性 | 本机（无 nsjail） | `run_canary` 在本机跑一遍 | 六项全部 `not_checked`；**不创建任何进程**（启动器被替换成会抛异常的替身，一次都没被调用）；退出码 `3`；结论文本显式说"未被证明" | **存在**（`test_canary_on_a_host_without_nsjail_reports_not_checked_and_runs_nothing`） |
| T-CANARY-002 | ADR-0010.2 自证 | 单元 | — | 三态判定：逃逸标记 / 阻断标记 / 被信号杀死 / 无法归因 | 分别 `found` / `not_found` / `not_found`（信号属隔离生效）/ `not_checked`（**不四舍五入成"安全"**） | **存在**（`test_canary_verdicts_map_to_the_three_state_semantics`、`test_canary_exit_codes_never_render_not_checked_as_success`） |
| T-CANARY-003 | ADR-0010.2 自证 | 单元 | — | 检查六项金丝雀程序自身 | 六项 id 齐全、源码可被 `ast.parse`、两个标记都在 | **存在**（`test_canary_sources_are_syntactically_valid_and_carry_both_markers`） |
| T-CANARY-004 | ADR-0010.2 自证 | 安全 | **仅 Linux/容器** | 真内核上跑一次 `python -m autograder_b2.canary` | 六项都 `not_found`（都被挡住）、退出码 `0` | **不存在**（本机无法执行 nsjail；`ADR-0010` 遗留风险节已写明金丝雀只能在 Linux 上给出结论。**未验收，不写成通过**） |

## 隔离区复核与质量出口（`CHG-0014` / `CHG-0015`，2026-09-20 新增）

用例文件：[../../tests/test_intake_api.py](../../tests/test_intake_api.py)（两条 intake 端点：真实路由 + 真实
PostgreSQL + 真实 Ed25519 验签；隔离记录由**生产写入路径** `intake.imap.QuarantineStore` 落库）与
[../../tests/test_b2_routes.py](../../tests/test_b2_routes.py)（`appeal-pack` / `quality-summary`：真实编排器产出的
报告 + 真实路由）。P13 的行级课程范围另有两份**不需要数据库**的用例：
[../../tests/test_matching_scope.py](../../tests/test_matching_scope.py)（scope 只来自规则候选共识）与
[../../tests/test_quarantine_scope_sql.py](../../tests/test_quarantine_scope_sql.py)（谓词在 SQL 里、且在
`LIMIT` 之前）。契约依据：`MAIN/contracts/openapi/b2.yaml` 的四个 operation 与
`IntakeQuarantineView`/`AppealPack`/`EvaluationQualitySummary`；变更
[`CHG-0014`](../../../MAIN/docs/contracts/changes/CHG-0014-quarantine-and-appeal-pack.md) /
[`CHG-0015`](../../../MAIN/docs/contracts/changes/CHG-0015-quality-heatmap.md) /
`0.19.0`（隔离区行级 scope）。口径缺口见
[../api/contract.md](../api/contract.md) 第 1e 节。

| 用例 ID | 目标 | 层次 | 前置 | 步骤要点 | 期望结果 | 当前是否存在 |
|---|---|---|---|---|---|---|
| T-QUAR-001 | CHG-0014 §6-4 | 集成 | 真实 PostgreSQL | 用 `QuarantineStore` 写一条隔离记录再列出 | 字段与写入侧一一对应（原因码/邮箱/UID/`Message-ID`）、**不含任何邮件内容字段**、`resolved=false` | **存在**（`test_quarantine_list_exposes_what_the_email_path_recorded`） |
| T-QUAR-002 | CHG-0014 §6-4 | 集成 | 真实 PostgreSQL | `reason_code`/`resolved` 过滤 + 归属后复查 + 非法过滤 | 三个过滤各自生效（含"缺省不过滤"的口径）、非法取值 422 | **存在**（`test_quarantine_list_filters_by_reason_and_resolved_state`） |
| T-QUAR-003 | 契约分页 | 集成 | 真实 PostgreSQL | 3 条记录、`limit=2`，按倒序游标翻两页 | 最新的在前、不重不漏、`next_cursor` 只在还有下一页时非空；坏游标/越界 limit 422（**不**当第一页） | **存在**（`test_quarantine_list_paginates_newest_first_without_gaps`） |
| T-QUAR-004 | 鉴权矩阵 | 契约 | 真实路由 + 真实 JWT | 无令牌；学生；带角色但**无课程作用域**的 staff | 分别 401 / 403 / 403（错误体一律 Problem Details） | **存在**（`test_quarantine_list_rejects_unauthenticated_and_non_staff`、`test_quarantine_resolve_validates_the_body_and_the_identifier` 的末两条断言） |
| T-QUAR-005 | CHG-0014 §2.2-2 | 集成 | 真实 PostgreSQL | 归属一条隔离记录 | 200 + 三列落库正确；**提交/outbox/评测运行三张表计数为零**（归属不触发评测） | **存在**（`test_quarantine_resolve_records_the_attribution_without_evaluating`） |
| T-QUAR-006 | CHG-0014 §6-1 | 安全 | 真实 PostgreSQL | 对两种匹配失败原因码归属；对 `executable_attachment` 归属 | 前者 200；后者 **422**（`detail` 指名原因码）且 `resolved` 仍为 `false`——人工路径不得绕过信封校验 | **存在**（`test_quarantine_resolve_reruns_the_checks_the_manual_path_must_not_skip`） |
| T-QUAR-007 | 幂等/冲突 | 集成 | 真实 PostgreSQL | 对同一条记录归属两次 | 第二次 409；第一次的决定与备注**逐字未变** | **存在**（`test_quarantine_resolve_is_conflict_for_an_already_resolved_record`） |
| T-QUAR-008 | 请求校验 | 契约 | 真实路由 | 缺 `student_id`/非 UUID/超长 `note`/未知字段/未知或非法路径标识 | 前四种 422、后两种 404（**不**用 404 掩盖校验失败） | **存在**（`test_quarantine_resolve_validates_the_body_and_the_identifier`） |
| T-QUAR-009 | 依赖不可用 | 集成 | 未迁移的库 | 两条端点各请求一次 | 503 + 可读原因（不是 500、也不是空页） | **存在**（`test_quarantine_routes_are_503_on_an_unmigrated_database`） |
| T-QUAR-010 | **跨课程隔离（P13 强制验收）** | 契约 | 真实 PostgreSQL | 三类记录（本课序 / 另一门课 / 未关联）+ 只覆盖本课序的 staff 令牌：不带 query、带本课序、带另一门课分别请求；管理员不带过滤请求 | staff 只看到本课序那一行（未关联不可见）；带另一门课 → 403；admin 三类全见，未关联行 `scope_*=null` | **存在**（`test_quarantine_list_is_filtered_by_row_scope_before_pagination`） |
| T-QUAR-011 | 契约长度上限 | 契约 | 真实 PostgreSQL | 写入 700 字的 `detail`（库列允许到 1000）后列出；归属备注 500 字 | 视图里的 `detail` 落在服务端保守上限 500 内（契约 `maxLength` 为 1000）；500 字的备注可写入（501 字在 T-QUAR-008 被 422 拒） | **存在**（`test_quarantine_list_respects_the_contract_length_bounds`） |
| T-QUAR-012 | 行级过滤先于分页 | 集成 | 真实 PostgreSQL | 未授权行比授权行更新，staff 以 `limit=1` 请求 | 第一页仍是授权行；`next_cursor` 为 null（可见集合只有一行）——证明过滤发生在排序/limit/cursor 之前 | **存在**（同 T-QUAR-010 的分页断言） |
| T-QUAR-013 | 查询参数校验 | 契约 | 真实路由 | 非 UUID 的 `course_id`；非 UUID 的 `section_id`+合法课程；只有 `section_id` | 均 422（Problem Details），且不泄露任何记录 | **存在**（`test_quarantine_list_query_scope_validation`） |
| T-QUAR-014 | 越权归属 | 安全 | 真实 PostgreSQL | staff 对未关联 / 另一门课 / 已处理的未关联记录直接 POST resolve | 三者都 403；未关联那条的 `resolved` 状态不被改写（不能靠 409/422 差异探测状态） | **存在**（`test_quarantine_staff_cannot_resolve_unscoped_or_foreign_records`） |
| T-QUAR-015 | 管理员处置未关联记录 | 集成 | 真实 PostgreSQL | 管理员对未关联记录 POST resolve | 200；`resolved_student_id` 写入；`scope_*` 仍为 null（归属不凭空补课程关联） | **存在**（`test_quarantine_admin_may_resolve_unscoped_record`） |
| T-QUAR-016 | scope 只来自规则候选共识 | 单元 | 无数据库 | 同一 course/section 的不同学生候选 / 跨课序候选 / 跨课程候选 / 未匹配 / 唯一命中 / 重复规则同身份 | 只有"全部候选同一对 `(course_id, section_id)`"才带出 scope；其余为 None；唯一命中如实带出供审计 | **存在**（`tests/test_matching_scope.py`，6 用例，已在本机以真实依赖执行） |
| T-QUAR-017 | 谓词在 SQL 里且在 LIMIT 前 | 单元 | 无数据库（PostgreSQL 方言编译） | 编译 `_page_statement`：课序级 scope / 课程级 scope / 管理员 + 课程筛选 / 两个作用域 | staff 的 WHERE 含 `scope_course_id IS NOT NULL` 与行 scope 条件；课程级作用域不附加课序相等；管理员无 scope 谓词但筛选保留；多作用域并集；`WHERE` 在 `LIMIT` 之前 | **存在**（`tests/test_quarantine_scope_sql.py`，4 用例，已在本机执行） |
| B2-QS-01 | CHG-0015 §6-1 | 契约 | 真实路由 | 直接请求 `quality-summary` | 200（聚合），而**不是**被 `{evaluation_run_id}` 吞掉后的 404/422 | **存在**（`test_quality_summary_is_not_swallowed_by_the_run_path_parameter`） |
| B2-QS-02 | CHG-0015 §6-2 | 安全 | 真实 PostgreSQL | 跑完一次真实编排后读聚合 | 键集合逐字等于契约属性集；键名不含 student/submission/source/expected/user；**取值里除题目版本外没有任何 UUID**；`QualitySummary` 类型里没有其他 `*_id` | **存在**（`test_quality_summary_returns_counts_only`） |
| B2-QS-03 | CHG-0015 §2 | 集成 | 真实 PostgreSQL | 一次真实运行 + 一条没跑过的运行（同题目版本） | `attempt_count=2`；逐用例失败率与失败类别正确；逐规则命中数与库内静态明细**逐条相等**（且未命中的规则以 0 出现） | **存在**（`test_quality_summary_aggregates_per_case_and_per_rule`） |
| B2-QS-04 | CHG-0015 §6-3 | 安全 | 真实 PostgreSQL | 只挂"没跑过"的运行 vs 跑完的真实运行 | 前者 `not_checked=1`/`not_found=0`；后者 `not_found=1`/`not_checked=0`；两者三态之和 == `attempt_count` | **存在**（`test_quality_summary_never_merges_not_checked_into_not_found`） |
| B2-QS-05 | CHG-0015 §6-4 | 资源 | 真实 PostgreSQL | 报告里 `resource` 为空 vs 写入一项峰值读数 vs 只有 `wall_seconds` | 分别 `unmeasured_runs` = 1 / 0 / 1（墙钟不算"测到峰值"） | **存在**（`test_quality_summary_makes_unmeasured_runs_visible`；读数是**合成**写入已落库的报告，真实沙箱读数属 Linux 验收） |
| B2-QS-06 | 契约参数 | 契约 | 真实路由 | 时间窗（两端都含）与非法 `from`/`to`/`question_version_id`/缺参 | 窗口内外计数正确；四种非法输入 422 | **存在**（`test_quality_summary_time_window_is_inclusive_and_validated`） |
| B2-APL-01 | CHG-0014 §2.3 | 集成 | 真实 PostgreSQL | 教师读证据包 | `result_digest` == 已落库报告摘要，且教师视图下 `report_digest(pack["report"])` 复算出同一值；五项绑定摘要与报告逐字段一致（且**不含** `interpreter_version`）；`replay`/`redactions` 非空且长度合规 | **存在**（`test_appeal_pack_is_pinned_by_the_report_digest`） |
| B2-APL-02 | CHG-0014 §6-2 | 安全 | 真实 PostgreSQL | 往报告里注入工件根路径、Windows 盘符路径、POSIX 宿主路径，并保留 `/workspace/main.py` | 三种宿主形态**零命中**、占位串出现、**沙箱内路径保留**、`redactions` 非空 | **存在**（`test_appeal_pack_removes_host_paths_and_lists_what_it_removed`） |
| B2-APL-03 | 副作用 | 回归 | 真实 PostgreSQL | 组装前后比对已持久化报告 | 逐字节不变（脱敏返回新对象） | **存在**（`test_appeal_pack_does_not_mutate_the_stored_report`） |
| B2-APL-04 | CHG-0014 §6-3 | 安全 | 真实 PostgreSQL + 真实 JWT | 学生请求 `view=student`；教师请求 `view=teacher`；学生请求教师视图（含不传 `view`）；未知视图 | 学生包零命中隐藏/可见期望输出与源码、无 `expected_sha256`、脱敏清单说明学生视图被收窄；教师包含隐藏标记；学生两次 403（**不静默降级**）；未知视图 422 | **存在**（`test_appeal_pack_student_view_never_widens_visibility`） |
| B2-APL-05 | 状态码与依赖 | 契约 | 真实 PostgreSQL / 未迁移库 | 未完成的运行取包；两条端点对未迁移库请求 | 404 + `detail` 明说"没有报告"；503 | **存在**（`test_appeal_pack_needs_a_report_and_is_503_on_an_unmigrated_database`） |
| B2-APL-06 | 策略摘要诚实性 | 单元/集成 | 真实 PostgreSQL | 读 `runner_policy_summary` | 不含宿主路径；所列常量与 `runner.policy` **实际拒绝放宽**的默认值一致（对着策略对象断言，防止本地字面量漂移）；摘要里的策略摘要 == 绑定的 `runner_policy_digest` | **存在**（`test_appeal_pack_policy_summary_is_path_free_and_matches_the_enforced_constants`） |
| B2-NEW-01 | 鉴权矩阵（四条端点） | 契约 | 真实路由 + 真实 JWT | 四条新端点各发一次：无令牌；跨课程学生令牌 | 分别 401 / 403（错误体一律 Problem Details） | **存在**（`test_new_routes_reject_unauthenticated_requests`、`test_new_routes_are_forbidden_for_students_and_foreign_scopes`） |
| T-DB-13 | `RQ-B-4-30` / CHG-0014 | 集成 | 真实 PostgreSQL | 迁移 0013 的 upgrade → downgrade -1 → upgrade（三步实测在 B2 测试实例上执行） | 三步都成功；head 上三个新列与两条 CHECK 存在（`information_schema`/`pg_constraint` 实测）、降级后消失；`alembic heads` 只剩一个并与 `module.yaml`/`EXPECTED_SCHEMA_REVISION` 一致 | **存在**（phase-05 的三步实测 + `tests/test_migrations.py` 的既有闸 + `tests/test_storage_cas.py::test_core_metadata_matches_database` 的逐列比对；**未新增 0013 专项用例**：库结构由上述既有闸覆盖，而"新列被端点正确读写"由 T-QUAR-005/007 覆盖） |
| T-DB-14 | CHG-0012（`timestamptz(3)` 毫秒精度） | 集成 | 真实 PostgreSQL 的 `information_schema` | 枚举库里全部时间戳列，核对 0012 声明的 `MILLISECOND_COLUMNS` 逐一为毫秒精度 | 声明集合与库内实际精度集合**相等**（多列/少列/精度不符都算失败） | **存在**（`test_migrations.py::test_every_timestamp_column_has_millisecond_precision`；**phase-08 修**：原用例 `from alembic.versions import …` 的**路径假设错误**（本仓库 `alembic/` 无 `__init__.py`，`import alembic` 解析到 site-packages ⇒ `ModuleNotFoundError`；**不是收集期错误**，`--collect-only` 得 23 条）；改为用 `importlib.util.spec_from_file_location` **按文件路径**加载 0012，断言语义未放宽） |

