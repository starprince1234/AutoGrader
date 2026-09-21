# B4 测试用例目录

> 列"存在"取值：`存在` / `待建`。全部用例均已落到真实测试文件，并在**真实 PostgreSQL 17 与
> 真实 NATS 2.10 JetStream** 上执行（本轮真实结果见
> `../phases/active/phase-03-ops-lint-and-ledger.md`）。ID 前缀与
> `requirements/traceability.md` 的"关联测试"列一致。

| 用例 ID | 目标 RQ | 层次 | 测试文件 / 关键断言 | 期望结果 | 存在 |
|---|---|---|---|---|---|
| `T-PLUGIN-01` | — | 单元 | `tests/test_registry.py::test_the_declared_descriptor_matches_what_the_entry_point_builds` | `descriptor` 与入口点构造结果一致 | `存在` |
| `T-DB-01` | 4-10 | 集成 | `tests/test_database.py::test_cross_schema_select_is_denied_by_the_database`、`…create_is_denied…` | `permission denied for schema b2/b3` | `存在` |
| `T-DB-02` | 4-10 | 集成 | `tests/test_database.py::test_superuser_connection_is_refused_at_startup` | `ConfigurationError`：角色过度授权 | `存在` |
| `T-DB-03` | 4-10 | 集成 | `tests/test_health.py::test_readiness_reports_postgres_down_with_a_named_dependency` | 不健康且 detail 指明 `postgres`，不含 DSN | `存在` |
| `T-DB-04` | 4-10 | 集成 | `phases/active/phase-01-platform-implementation.md`（pg_dump → 删除 → pg_restore） | 数据回到备份点，命令留档 | `存在` |
| `T-DB-05` | 4-30 | 集成 | `tests/test_database.py::test_empty_database_migrates_from_zero`、`test_migration_head_matches_module_manifest` | 空库从零成功；head 双通道一致 | `存在` |
| `T-DB-06` | 4-30 | 集成 | `tests/test_database.py::test_cross_schema_foreign_key_cannot_be_created` | 跨 schema FK 被拒 | `存在` |
| `T-DB-07` | 4-30 | 单元/CI | `tests/test_schema_standard.py`（三个**故意违规**的 catalog 用例） | 违规命名/缺主键/裸 timestamp 被发现 | `存在` |
| `T-DB-08` | 4-10 | 集成 | `tests/test_users_courses.py::test_update_enrollment_honours_optimistic_concurrency`、`…test_update_user_rejects_a_stale_expected_updated_at` | 过期写入 `409`，不静默覆盖 | `存在` |
| `T-DB-09` | 4-30 | 单元/CI | `tests/test_schema_standard.py::test_migration_linter_detects_non_compliant_sql`、`…_sqlalchemy_ddl`、`…_reports_a_module_it_cannot_read`、`…_covers_the_repository_and_b4_conforms`、`test_declared_archetypes_are_not_reported`、`test_undeclared_lookalikes_are_still_reported`、`test_a_declaration_cannot_launder_a_table`、`test_every_archetype_declaration_is_keyed_by_module_and_satisfied` | 迁移 DDL 文本违规被发现（含"扫描不到 DDL 也算违规"）；**三类声明原型不得误报**、长得像但未声明仍报、声明了却不符合照样报；全仓语句数下限，B4 自身 0 违规 | `存在` |
| `T-DB-10` | 4-10 / 4-99 | 集成 | `tests/test_database.py::test_backup_writes_a_dump_and_records_the_result`、`…test_a_failed_backup_leaves_no_file_and_is_recorded` | 归档魔数 `PGDMP`、日志含字节数/耗时、失败不留半成品且留记录 | `存在` |
| `T-MSG-01` | 4-20 | 集成 | `tests/test_messaging.py::test_provisioning_creates_the_declared_topology` | subject/配额/`max_deliver` 与声明一致 | `存在` |
| `T-MSG-02` | 4-20 | 集成 | `tests/test_messaging.py::test_a_duplicate_delivery_produces_one_business_effect` | 两次投递 → 一次业务效果 | `存在` |
| `T-MSG-03` | 4-20 | 集成 | `tests/test_messaging.py::test_an_exhausted_delivery_lands_in_the_dead_letter_store` | `max_deliver` 耗尽 → 可查 → 可重放 | `存在` |
| `T-MSG-04` | 4-20 | 集成 | `tests/test_messaging.py::test_an_unreachable_broker_leaves_the_event_queued`；`ops/outage_drill.py` | 同步路径可用；事件保留；readiness 真实 | `存在` |
| `T-MSG-05` | 4-20 | 集成/资源 | `tests/test_messaging.py::test_jetstream_storage_stays_inside_the_resource_ledger`、`…test_jetstream_storage_is_measured_not_assumed` | 声明配额合计 == 账本行且每 stream > 0；relay 后 `state.bytes > 0` 且 ≤ 配额（把"配置"变成"测量"） | `存在` |
| `T-MSG-06` | 4-20 | 集成 | `tests/test_messaging.py::test_the_shared_domain_stream_binds_every_contract_subject_exactly_once`、`test_provisioning_creates_the_shared_domain_stream` | 契约 `events.yaml` 的每个 address 恰好绑定到一条 stream（无未绑定/无重复绑定）；共享流按声明创建 | `存在` |
| `T-MSG-07` | 4-20 | 集成 | `tests/test_messaging.py::test_a_publish_to_a_contract_subject_lands_in_the_domain_stream` | 向契约 subject 发布 → 落在 `DOMAIN_EVENTS` 并可读回（发布路径真的通） | `存在` |
| `T-AUTH-01` | 4-50 | 单元/集成 | `tests/test_auth.py::test_access_token_is_ed25519_and_verifiable_from_the_jwks` | JWKS 本地验签成功，含 `iss`/`aud`/`role`/作用域 | `存在` |
| `T-AUTH-02` | 4-50 | 单元 | `tests/test_auth.py::test_a_tampered_token_is_rejected`、`…signed_by_another_key…` | `401` | `存在` |
| `T-AUTH-03` | 4-50 | 单元 | `tests/test_auth.py::test_an_expired_token_is_rejected` | `401` | `存在` |
| `T-AUTH-04` | 4-50 | 集成 | `tests/test_authorization.py`（越权矩阵） | `403` 且留审计 | `存在` |
| `T-AUTH-05` | 4-50 | 单元 | `tests/test_auth.py::test_passwords_are_stored_with_argon2id` | `$argon2id$`，无明文 | `存在` |
| `T-AUTH-06` | 4-50 | 集成 | `tests/test_auth.py::test_refresh_credential_never_appears_in_the_response_body`、`test_logging.py::test_credentials_never_reach_a_log_line_through_a_handler` | 响应体/日志无刷新凭据 | `存在` |
| `T-AUTH-07` | 4-50 | 集成 | `tests/test_auth.py::test_unknown_user_and_wrong_password_are_indistinguishable`、`test_failed_logins_are_throttled`、`test_a_throttled_attempt_is_recorded` | 不可枚举、限流生效且留痕（即 AC7 的"速率限制/锁定策略"） | `存在` |
| `T-ENR-01` | 4-50 | 集成 | `tests/test_users_courses.py::test_enrollment_listing_exposes_the_platform_student_id` | 名单行含 `student_id`（未匹配为 `null`），且响应键集合精确等于 `EnrollmentPublic` | `存在` |
| `T-ROSTER-01` | 4-50 | 集成 | `tests/test_roster_import.py::test_xlsx_roster_parses_and_previews_without_writing` | 确认前 `enrollments` 零写入 | `存在` |
| `T-ROSTER-02` | 4-50 | 集成 | `tests/test_roster_import.py::test_confirmation_is_idempotent` | 幂等，不重复写入 | `存在` |
| `T-ROSTER-03` | 4-50 | 集成 | `tests/test_roster_import.py::test_ambiguous_columns_stop_for_a_human_decision`、`…missing_required_column…` | 进入 `mapping_required`，不猜测 | `存在` |
| `T-ROSTER-04` | 4-50 | 集成 | `tests/test_roster_import.py::test_rejected_rows_are_reported_with_a_reason` | 逐行标红、带原因，不静默丢弃 | `存在` |
| `T-ROSTER-05` | 4-50 | 端到端 | `tests/test_roster_import.py::test_pdf_roster_round_trips`、`test_confirmation_writes_roster_rows_atomically` | PDF/XLSX 各走通并发布事件 | `存在` |
| `T-ROSTER-06` | 4-50 | 安全 | `tests/test_roster_import.py::test_a_tampered_stored_artifact_is_refused_at_confirmation`、`test_an_oversized_upload_is_rejected`、`test_a_non_pdf_or_xlsx_upload_is_rejected` | 摘要不符/超限/类型不符被拒 | `存在` |
| `T-LOG-01` | 4-99 | 单元 | `tests/test_logging.py::test_log_line_is_json_with_the_producer_and_correlation_fields` | JSON、`producer=B4`、含关联 ID | `存在` |
| `T-LOG-02` | 4-99 | 单元 | `tests/test_logging.py`（脱敏 5 例 + 端到端 1 例） | 无密码/JWT/cookie/超范围 PII | `存在` |
| `T-LOG-03` | 4-99 | 单元 | `tests/test_logging.py::test_credentials_never_reach_a_log_line_through_a_handler`；运维命令输出见 phase 记录 | 运维动作有可追溯输出 | `存在` |
| `T-LOG-04` | 4-99 | 集成 | `tests/test_users_courses.py::test_audit_rows_carry_the_request_and_correlation_identifiers`、`tests/test_logging.py::test_traceparent_becomes_the_correlation_id` | 可凭 `correlation_id` 串联 | `存在` |
| `T-LOG-05` | 4-99 | 集成 | `tests/test_database.py`（迁移 `ops.migration.applied` + 备份 `ops.backup.completed/failed` 记录，且不含 DSN）、`tests/test_health.py::test_health_records_only_transitions` | 迁移/备份/健康检查结果被记录；健康只在状态变化时记录 | `存在` |
| `T-REG-01` | 4-40 | 集成 | `tests/test_registry.py::test_registry_reports_every_declared_field` | 返回全部声明字段 | `存在` |
| `T-REG-02` | 4-40 | 集成 | `tests/test_registry.py::test_a_descriptor_that_contradicts_its_manifest_fails`、`test_contract_digest_mirrors_what_main_locked` | 漂移 → 明确失败并指明模块 | `存在` |
| `T-REG-03` | 4-40 | 集成 | `tests/test_registry.py::test_a_missing_capability_fails`、`test_a_duplicate_route_between_modules_fails`、`test_a_contract_digest_drift_fails` | 启动失败，信息含模块与原因 | `存在` |
| `T-REG-04` | 4-40 | 集成 | `tests/test_registry.py::test_the_full_registry_requires_an_administrator`、`test_the_per_module_health_probe_is_public_but_minimal` | 公开仅存活；全量仅管理员 | `存在` |
| `T-REG-05` | 4-40 | 单元 | `tests/test_registry.py::test_two_entry_points_with_the_same_name_are_refused`、`test_a_fifth_module_name_is_accepted_by_the_pattern`、`test_registry_module_names_are_validated_by_the_contract_pattern` | 同名 entry point 重复 → 启动失败（ADR-0013 决策 5）；模块名按契约 pattern 而非封闭集合校验 | `存在` |

## 用例之外的验证

| 验证 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `uv run ruff check .` | `All checks passed!`（见 phase-03） |
| 全量测试 | `uv run pytest -q` | 见 phase-03（真实数字） |
| 空库迁移 | `uv run alembic upgrade head` | `20260915_0002`，16 张表 |
| 表结构规范（b4 真实 schema） | `uv run python -m autograder_b4.ops.table_standards --scope catalog` | 0 违规 |
| 表结构规范（全仓迁移文本） | `uv run python -m autograder_b4.ops.table_standards --scope migrations` | 137 条 DDL / **57 条真违规**（35 未命名主键 + 22 精度），B4 0 条；原型误判 0 条 |
| JetStream 占用实测 | `uv run python -m autograder_b4.ops.messaging metrics` | 见 phase-03 |
| 核心备份路径 | `uv run python -m autograder_b4.ops.backup --container b4-postgres-1` | 见 phase-03 |
| 停机演练 | `uv run python -m autograder_b4.ops.outage_drill` | 4/4 PASS（phase-01） |
| 备份恢复演练 | 见 `runbooks/database-operations.md` §5 | 数据回到备份点（phase-01） |
