"""`b2` schema 的 SQLAlchemy Core 表映像（**只用于查询**，DDL 权威在 `alembic/versions/**`）。

为什么需要它：仓储代码要能组合查询/插入/删除，而 DDL 只能有一处权威（迁移）。
为避免"映像与 DDL 漂移"，`tests/test_storage_cas.py::test_core_metadata_matches_database`
用真实 PostgreSQL 的 `information_schema` 逐表逐列比对两者——任何一方改了而另一方没跟上，
测试即失败。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

SCHEMA = "b2"

#: 本代码期望的迁移版本（必须与 `module.yaml: database.migration_head` 和 Alembic head 三者一致）。
#: 用途：readyness 探测据此判断"库结构与代码是否匹配"——连接得上但版本落后同样是**不可用**，
#: 否则 health 会谎报健康而受权读请求全线 500。三者一致性由 `tests/test_migrations.py` 断言。
EXPECTED_SCHEMA_REVISION = "20260915_0015"

metadata = sa.MetaData(schema=SCHEMA)

artifacts = sa.Table(
    "artifacts",
    metadata,
    sa.Column("artifact_id", sa.Uuid(), primary_key=True),
    sa.Column("content_sha256", sa.Text(), nullable=False),
    sa.Column("size_bytes", sa.BigInteger(), nullable=False),
    sa.Column("storage_path", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

submissions = sa.Table(
    "submissions",
    metadata,
    sa.Column("submission_id", sa.Uuid(), primary_key=True),
    sa.Column("student_id", sa.Uuid(), nullable=False),
    sa.Column("course_id", sa.Uuid(), nullable=False),
    sa.Column("section_id", sa.Uuid(), nullable=False),
    sa.Column("question_version_id", sa.Uuid(), nullable=False),
    sa.Column("intake", sa.Text(), nullable=False),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("idempotency_scope", sa.Text(), nullable=False),
    sa.Column("submission_digest", sa.Text(), nullable=False),
    sa.Column("state", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    # 迁移 0008：冻结信封的 correlation_id 与 intake provenance（两列组按 intake 取值互斥，
    # 由 ck_submissions_provenance_shape 在库层面保证）。列顺序必须与 DDL 的追加顺序一致
    # （`test_storage_cas.py::test_core_metadata_matches_database` 逐列按 ordinal_position 比对）。
    sa.Column("correlation_id", sa.Uuid(), nullable=False),
    sa.Column("request_id", sa.Text(), nullable=True),
    sa.Column("mailbox", sa.Text(), nullable=True),
    sa.Column("uidvalidity", sa.BigInteger(), nullable=True),
    sa.Column("uid", sa.BigInteger(), nullable=True),
    sa.Column("message_id", sa.Text(), nullable=True),
    sa.CheckConstraint(
        "(intake = 'api' AND request_id IS NOT NULL"
        " AND mailbox IS NULL AND uidvalidity IS NULL AND uid IS NULL AND message_id IS NULL)"
        " OR (intake = 'email' AND request_id IS NULL"
        " AND mailbox IS NOT NULL AND uidvalidity IS NOT NULL AND uid IS NOT NULL"
        " AND message_id IS NOT NULL)",
        name="ck_submissions_provenance_shape",
    ),
)

submission_files = sa.Table(
    "submission_files",
    metadata,
    sa.Column("submission_id", sa.Uuid(), primary_key=True),
    sa.Column("path", sa.Text(), primary_key=True),
    sa.Column("artifact_id", sa.Uuid(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

email_checkpoints = sa.Table(
    "email_checkpoints",
    metadata,
    sa.Column("email_checkpoint_id", sa.Uuid(), primary_key=True),
    sa.Column("mailbox", sa.Text(), nullable=False),
    sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
    sa.Column("uid", sa.BigInteger(), nullable=False),
    sa.Column("message_id", sa.Text(), nullable=True),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("reason_code", sa.Text(), nullable=True),
    sa.Column("submission_id", sa.Uuid(), nullable=True),
    sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
)

intake_quarantines = sa.Table(
    "intake_quarantines",
    metadata,
    sa.Column("intake_quarantine_id", sa.Uuid(), primary_key=True),
    sa.Column("intake", sa.Text(), nullable=False),
    sa.Column("reason_code", sa.Text(), nullable=False),
    sa.Column("detail", sa.Text(), nullable=False),
    sa.Column("mailbox", sa.Text(), nullable=True),
    sa.Column("uidvalidity", sa.BigInteger(), nullable=True),
    sa.Column("uid", sa.BigInteger(), nullable=True),
    sa.Column("message_id", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    # 迁移 0013（CHG-0014 的人工归属落点）：列顺序必须与 DDL 的追加顺序一致
    # （`test_storage_cas.py::test_core_metadata_matches_database` 逐列按 ordinal_position 比对）。
    sa.Column("resolved", sa.Boolean(), nullable=False),
    sa.Column("resolved_student_id", sa.Uuid(), nullable=True),
    sa.Column("resolution_note", sa.Text(), nullable=True),
    # 迁移 0015（P13 的行级课程范围）：匹配规则候选共识产生的课程/课序，成对为空或非空。
    # 历史行保持 NULL（无法证明课程归属）⇒ 只对平台管理员可见，原始数据不动。
    sa.Column("scope_course_id", sa.Uuid(), nullable=True),
    sa.Column("scope_section_id", sa.Uuid(), nullable=True),
)

artifact_gc_runs = sa.Table(
    "artifact_gc_runs",
    metadata,
    sa.Column("gc_run_id", sa.Uuid(), primary_key=True),
    sa.Column("trigger", sa.Text(), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("scanned_artifacts", sa.Integer(), nullable=False),
    sa.Column("removed_artifacts", sa.Integer(), nullable=False),
    sa.Column("skipped_referenced", sa.Integer(), nullable=False),
    sa.Column("scanned_files", sa.Integer(), nullable=False),
    sa.Column("removed_stray_files", sa.Integer(), nullable=False),
    sa.Column("skipped_unrecognized", sa.Integer(), nullable=False),
    sa.Column("file_removal_failures", sa.Integer(), nullable=False),
    sa.Column("bytes_reclaimed", sa.BigInteger(), nullable=False),
)

artifact_gc_audits = sa.Table(
    "artifact_gc_audits",
    metadata,
    sa.Column("gc_audit_id", sa.Uuid(), primary_key=True),
    sa.Column("gc_run_id", sa.Uuid(), nullable=False),
    sa.Column("content_sha256", sa.Text(), nullable=False),
    sa.Column("storage_path", sa.Text(), nullable=False),
    sa.Column("size_bytes", sa.BigInteger(), nullable=False),
    sa.Column("outcome", sa.Text(), nullable=False),
    sa.Column("detail", sa.Text(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

submission_state_transitions = sa.Table(
    "submission_state_transitions",
    metadata,
    sa.Column("submission_state_transition_id", sa.Uuid(), primary_key=True),
    sa.Column("submission_id", sa.Uuid(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("from_state", sa.Text(), nullable=True),
    sa.Column("to_state", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

outbox_events = sa.Table(
    "outbox_events",
    metadata,
    sa.Column("event_id", sa.Uuid(), primary_key=True),
    sa.Column("subject", sa.Text(), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.Integer(), nullable=False),
    sa.Column("producer", sa.Text(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("correlation_id", sa.Uuid(), nullable=False),
    sa.Column("causation_id", sa.Uuid(), nullable=True),
    sa.Column("deduplication_key", sa.Text(), nullable=True),
    sa.Column("payload", JSONB(), nullable=False),
    sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("last_error", sa.Text(), nullable=True),
    sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    #: 入队顺序（单调，按插入发生次序；迁移 0009）。派发器据此排序，使**同事务内先写的事件先发**，
    #: 不依赖 `enqueued_at` 的时间戳精度，也不依赖 `event_id` 的哈希。
    sa.Column("insert_seq", sa.BigInteger, sa.Identity(always=False), nullable=False),
)

evaluation_runs = sa.Table(
    "evaluation_runs",
    metadata,
    sa.Column("evaluation_run_id", sa.Uuid(), primary_key=True),
    sa.Column("submission_id", sa.Uuid(), nullable=False),
    sa.Column("state", sa.Text(), nullable=False),
    sa.Column("stage", sa.Text(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("cases_total", sa.Integer(), nullable=True),
    sa.Column("cases_done", sa.Integer(), nullable=False),
    sa.Column("terminal_reason", sa.Text(), nullable=True),
    sa.Column("submission_digest", sa.Text(), nullable=False),
    sa.Column("question_version_binding", sa.Text(), nullable=True),
    sa.Column("package_digest", sa.Text(), nullable=True),
    sa.Column("runner_policy_digest", sa.Text(), nullable=True),
    sa.Column("environment_digest", sa.Text(), nullable=True),
    sa.Column("question_version_id", sa.Uuid(), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    # 迁移 0011：三态安全结论快照（`found`/`not_found`/`not_checked`；NULL = 没走到安全判定阶段）。
    # 列顺序必须与 DDL 的追加顺序一致（`test_storage_cas.py::test_core_metadata_matches_database`
    # 逐列按 ordinal_position 比对）。
    sa.Column("security_status", sa.Text(), nullable=True),
)

evaluation_run_state_transitions = sa.Table(
    "evaluation_run_state_transitions",
    metadata,
    sa.Column("evaluation_run_state_transition_id", sa.Uuid(), primary_key=True),
    sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("from_state", sa.Text(), nullable=True),
    sa.Column("to_state", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

progress_events = sa.Table(
    "progress_events",
    metadata,
    sa.Column("progress_event_id", sa.Uuid(), primary_key=True),
    sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("event", sa.Text(), nullable=False),
    sa.Column("state", sa.Text(), nullable=False),
    sa.Column("stage", sa.Text(), nullable=False),
    sa.Column("cases_total", sa.Integer(), nullable=True),
    sa.Column("cases_done", sa.Integer(), nullable=True),
    sa.Column("score", sa.Numeric(), nullable=True),
    sa.Column("is_pass", sa.Boolean(), nullable=True),
    sa.Column("terminal_reason", sa.Text(), nullable=True),
    sa.Column("message", sa.Text(), nullable=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

evaluation_results = sa.Table(
    "evaluation_results",
    metadata,
    sa.Column("evaluation_run_id", sa.Uuid(), primary_key=True),
    sa.Column("score", sa.Numeric(), nullable=False),
    sa.Column("max_score", sa.Numeric(), nullable=False),
    sa.Column("is_pass", sa.Boolean(), nullable=False),
    sa.Column("result_digest", sa.Text(), nullable=False),
    sa.Column("report", JSONB(), nullable=False),
    sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
)

static_findings = sa.Table(
    "static_findings",
    metadata,
    sa.Column("static_finding_id", sa.Uuid(), primary_key=True),
    sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
    sa.Column("rule_id", sa.Text(), nullable=False),
    sa.Column("severity", sa.Text(), nullable=False),
    sa.Column("file", sa.Text(), nullable=False),
    sa.Column("line", sa.Integer(), nullable=True),
    sa.Column("column_number", sa.Integer(), nullable=True),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("blocking", sa.Boolean(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

security_findings = sa.Table(
    "security_findings",
    metadata,
    sa.Column("security_finding_id", sa.Uuid(), primary_key=True),
    sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
    sa.Column("category", sa.Text(), nullable=False),
    sa.Column("severity", sa.Text(), nullable=False),
    sa.Column("detected_in", sa.Text(), nullable=False),
    sa.Column("rule_id", sa.Text(), nullable=True),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("blocking", sa.Boolean(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

case_evidence = sa.Table(
    "case_evidence",
    metadata,
    sa.Column("case_evidence_id", sa.Uuid(), primary_key=True),
    sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
    sa.Column("case_id", sa.Uuid(), nullable=False),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("weight", sa.Numeric(), nullable=False),
    sa.Column("passed", sa.Boolean(), nullable=False),
    sa.Column("failure_category", sa.Text(), nullable=True),
    sa.Column("awarded", sa.Numeric(), nullable=False),
    sa.Column("input_digest", sa.Text(), nullable=False),
    sa.Column("stdout_digest", sa.Text(), nullable=False),
    sa.Column("stderr_digest", sa.Text(), nullable=False),
    sa.Column("actual_output_excerpt", sa.Text(), nullable=True),
    sa.Column("expected_output_excerpt", sa.Text(), nullable=True),
    sa.Column("exit_code", sa.Integer(), nullable=True),
    sa.Column("duration_ms", sa.Integer(), nullable=True),
    sa.Column("resource_limit_hit", sa.Text(), nullable=True),
    sa.Column("message", sa.Text(), nullable=True),
    sa.Column("evidence_digest", sa.Text(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)

inbox_events = sa.Table(
    "inbox_events",
    metadata,
    sa.Column("consumer", sa.Text(), primary_key=True),
    sa.Column("event_id", sa.Uuid(), primary_key=True),
    sa.Column("subject", sa.Text(), nullable=False),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
)

evaluation_commentaries = sa.Table(
    "evaluation_commentaries",
    metadata,
    sa.Column("evaluation_commentary_id", sa.Uuid(), primary_key=True),
    sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("text", sa.Text(), nullable=True),
    sa.Column("provider", sa.Text(), nullable=False),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("prompt_version", sa.Text(), nullable=False),
    sa.Column("input_scope", JSONB(), nullable=False),
    sa.Column("failure_reason", sa.Text(), nullable=True),
    sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    # 迁移 0014：`AdvisoryScore`（`RQ-T-30` / `ADR-0014`）。四列同生同死（`ck_..._shape`），
    # `advisory_max_score` 由 `case_evidence.weight` 求和填入，**不由模型生成**。
    # 列顺序必须与 DDL 的追加顺序一致（`test_storage_cas.py::test_core_metadata_matches_database`
    # 逐列按 ordinal_position 比对）。
    sa.Column("advisory_score", sa.Numeric(), nullable=True),
    sa.Column("advisory_max_score", sa.Numeric(), nullable=True),
    sa.Column("advisory_rationale", sa.Text(), nullable=True),
    sa.Column("advisory_confidence", sa.Text(), nullable=True),
)
