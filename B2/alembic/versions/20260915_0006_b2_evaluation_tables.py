"""b2 评测链业务表（WP-B2-B/C + WP-B2-A 收尾）：运行、进度、证据、报告、判决、防重。

为什么一次建齐：Alembic 的版本链是**线性**的，多个并行工作项各自新建迁移会产生多个 head，
直接违反 G-DB-03（`module.yaml: database.migration_head` 必须等于唯一 head）。因此由一人一次性
建好全部剩余表，其余工作项只写代码、不写迁移。

表意图：
- `evaluation_runs`：一次评测运行及其**五项绑定摘要**（提交/题目版本/测试包/runner 策略/执行环境）
  与状态、阶段、单调 `sequence`（SSE 去重排序用）。绑定摘要在编译期未知时先留空，产出报告前必须补齐。
- `evaluation_run_state_transitions`：运行状态迁移史（与提交迁移史同构，用 `sequence` 保证顺序确定）。
- `progress_events`：SSE 帧的**持久化来源**。`(evaluation_run_id, sequence)` 唯一 => 单调且可断线重连去重；
  帧内只允许标识与计数，不得含隐藏用例输入/期望输出/源码。
- `evaluation_results`：一次运行的汇总结果 + **完整报告 JSON**。报告与结果同表同事务写入，
  因此"报告可读"必然意味着"结果已持久化"（RQ-B-2-60 验收 #5）。
- `static_findings` / `security_findings`：静态明细与安全发现（报告的两段直接来源）。
- `case_evidence`：逐用例证据（`(run, ordinal)` 唯一，逐分可追溯）。
- `inbox_events`：消费者按 `event_id` 去重（at-least-once 的幂等端）。

状态机说明（与本模块 `docs/architecture/models.md` 的差异，待 5.2 同步）：运行状态取
`queued -> {running, rejected, failed}`、`running -> {completed, rejected, failed}`。之所以允许
`queued` 直达终态：契约的 SSE 把 `rejected` 定为终态帧，而静态/安全阻断发生在进入沙箱之前——
没有运行实体就没有 SSE 帧可发，因此运行必须在静态阶段之前创建，并在被阻断时从 `queued` 直接落终态。

注意：本文件是**迁移源码**，Alembic 在离线模式（`--sql`）下会把 docstring 作为注释回显到 stdout，
因此文档字符串里**不要使用 GBK/ASCII 之外的符号**（例如数学蕴含箭头 U+21D2），否则在 GBK 控制台的 Windows 上
`alembic upgrade head --sql` 会因编码失败而退出（已实测）。

可回滚：`downgrade()` 按依赖逆序删除。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260915_0006"
down_revision = "20260915_0005"
branch_labels = None
depends_on = None

SCHEMA = "b2"
_DIGEST = "^[0-9a-f]{64}$"
_RUN_STATES = "'queued','running','completed','rejected','failed'"
_RUN_STAGES = (
    "'queued','fetching_package','static_analysis','security_check','dynamic_execution',"
    "'scoring','reporting','terminal'"
)
_TERMINAL_REASONS = "'static_blocked','security_blocked','infrastructure_error','internal_error'"
_FAILURE_CATEGORIES = (
    "'timeout','crash','output_mismatch','resource_limit','missing_output','import_error',"
    "'security_blocked','infrastructure_error'"
)
_SECURITY_CATEGORIES = (
    "'destructive_command','network_attempt','privilege_escalation','sensitive_path_read',"
    "'fork_bomb','resource_limit','archive_bomb','sandbox_violation'"
)
_SECURITIES = "'high','medium','low'"


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
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
        sa.Column("started_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("finished_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=True),
        sa.Column("created_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.Column("updated_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint(f"state IN ({_RUN_STATES})", name="ck_evaluation_runs_state"),
        sa.CheckConstraint(f"stage IN ({_RUN_STAGES})", name="ck_evaluation_runs_stage"),
        sa.CheckConstraint(
            f"terminal_reason IS NULL OR terminal_reason IN ({_TERMINAL_REASONS})",
            name="ck_evaluation_runs_terminal_reason",
        ),
        sa.CheckConstraint("sequence >= 0 AND cases_done >= 0", name="ck_evaluation_runs_counters"),
        sa.CheckConstraint(
            "cases_total IS NULL OR cases_done <= cases_total",
            name="ck_evaluation_runs_cases_done_within_total",
        ),
        sa.CheckConstraint(f"submission_digest ~ '{_DIGEST}'", name="ck_evaluation_runs_submission_digest"),
        sa.CheckConstraint(
            "question_version_binding IS NULL OR question_version_binding ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_question_version_binding",
        ),
        sa.CheckConstraint(
            "package_digest IS NULL OR package_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_package_digest",
        ),
        sa.CheckConstraint(
            "runner_policy_digest IS NULL OR runner_policy_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_runner_policy_digest",
        ),
        sa.CheckConstraint(
            "environment_digest IS NULL OR environment_digest ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_runs_environment_digest",
        ),
        # 终态必须有终态时刻与终态原因，非终态不得有——避免"完成了但没记为什么"。
        sa.CheckConstraint(
            "(state IN ('completed','rejected','failed') AND finished_at IS NOT NULL AND terminal_reason IS NOT NULL)"
            " OR (state IN ('queued','running') AND finished_at IS NULL AND terminal_reason IS NULL)",
            name="ck_evaluation_runs_terminal_consistency",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            [f"{SCHEMA}.submissions.submission_id"],
            name="fk_evaluation_runs_submission_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evaluation_run_id", name="pk_evaluation_runs"),
        schema=SCHEMA,
    )
    op.create_index("ix_evaluation_runs_submission_id", "evaluation_runs", ["submission_id"], schema=SCHEMA)
    op.create_index("ix_evaluation_runs_state_created_at", "evaluation_runs", ["state", "created_at"], schema=SCHEMA)
    op.create_index(
        "ix_evaluation_runs_question_version_id", "evaluation_runs", ["question_version_id"], schema=SCHEMA
    )

    op.create_table(
        "evaluation_run_state_transitions",
        sa.Column("evaluation_run_state_transition_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_evaluation_run_state_transitions_sequence_positive"),
        sa.CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_RUN_STATES})",
            name="ck_evaluation_run_state_transitions_from_state",
        ),
        sa.CheckConstraint(f"to_state IN ({_RUN_STATES})", name="ck_evaluation_run_state_transitions_to_state"),
        sa.CheckConstraint(
            "reason IS NULL OR char_length(reason) <= 500",
            name="ck_evaluation_run_state_transitions_reason_length",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_evaluation_run_state_transitions_run_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "evaluation_run_id", "sequence", name="uq_evaluation_run_state_transitions_run_id_sequence"
        ),
        sa.PrimaryKeyConstraint(
            "evaluation_run_state_transition_id", name="pk_evaluation_run_state_transitions"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "progress_events",
        sa.Column("progress_event_id", sa.Uuid(), nullable=False),
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
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_progress_events_sequence_non_negative"),
        sa.CheckConstraint(
            "event IN ('progress','completed','rejected','failed')", name="ck_progress_events_event"
        ),
        sa.CheckConstraint(f"state IN ({_RUN_STATES})", name="ck_progress_events_state"),
        sa.CheckConstraint(f"stage IN ({_RUN_STAGES})", name="ck_progress_events_stage"),
        sa.CheckConstraint(
            f"terminal_reason IS NULL OR terminal_reason IN ({_TERMINAL_REASONS})",
            name="ck_progress_events_terminal_reason",
        ),
        sa.CheckConstraint(
            "message IS NULL OR char_length(message) <= 1000", name="ck_progress_events_message_length"
        ),
        # 端子帧必须带终态原因；progress 帧不得带分数（分数只在 completed 帧出现）。
        sa.CheckConstraint(
            "event = 'progress' OR terminal_reason IS NOT NULL", name="ck_progress_events_terminal_frames"
        ),
        sa.CheckConstraint(
            "event IN ('progress','failed','rejected') OR score IS NOT NULL",
            name="ck_progress_events_completed_has_score",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_progress_events_run_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("evaluation_run_id", "sequence", name="uq_progress_events_run_id_sequence"),
        sa.PrimaryKeyConstraint("progress_event_id", name="pk_progress_events"),
        schema=SCHEMA,
    )

    op.create_table(
        "evaluation_results",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=False),
        sa.Column("max_score", sa.Numeric(), nullable=False),
        sa.Column("is_pass", sa.Boolean(), nullable=False),
        sa.Column("result_digest", sa.Text(), nullable=False),
        sa.Column("report", JSONB(), nullable=False),
        sa.Column("persisted_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("score >= 0 AND max_score > 0", name="ck_evaluation_results_scores"),
        sa.CheckConstraint(f"result_digest ~ '{_DIGEST}'", name="ck_evaluation_results_result_digest"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_evaluation_results_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("evaluation_run_id", name="pk_evaluation_results"),
        schema=SCHEMA,
    )

    op.create_table(
        "static_findings",
        sa.Column("static_finding_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("file", sa.Text(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("column_number", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("severity IN ('error','warning','info')", name="ck_static_findings_severity"),
        sa.CheckConstraint("char_length(rule_id) <= 100", name="ck_static_findings_rule_id_length"),
        sa.CheckConstraint("char_length(file) <= 200", name="ck_static_findings_file_length"),
        sa.CheckConstraint("char_length(message) <= 1000", name="ck_static_findings_message_length"),
        sa.CheckConstraint("line IS NULL OR line >= 1", name="ck_static_findings_line_positive"),
        # 列名用 `column_number` 而不是 `column`：后者是 PostgreSQL 保留字，作为列名必须处处加引号，
        # 极易踩坑。报告对外仍映射为契约字段 `static_findings[].column`（名字差异只在这一层做）。
        sa.CheckConstraint(
            "column_number IS NULL OR column_number >= 1", name="ck_static_findings_column_positive"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_static_findings_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("static_finding_id", name="pk_static_findings"),
        schema=SCHEMA,
    )
    op.create_index("ix_static_findings_run_id", "static_findings", ["evaluation_run_id"], schema=SCHEMA)

    op.create_table(
        "security_findings",
        sa.Column("security_finding_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("detected_in", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint(f"category IN ({_SECURITY_CATEGORIES})", name="ck_security_findings_category"),
        sa.CheckConstraint(f"severity IN ({_SECURITIES})", name="ck_security_findings_severity"),
        sa.CheckConstraint("detected_in IN ('static','runtime')", name="ck_security_findings_detected_in"),
        sa.CheckConstraint("char_length(message) <= 1000", name="ck_security_findings_message_length"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_security_findings_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("security_finding_id", name="pk_security_findings"),
        schema=SCHEMA,
    )
    op.create_index("ix_security_findings_run_id", "security_findings", ["evaluation_run_id"], schema=SCHEMA)

    op.create_table(
        "case_evidence",
        sa.Column("case_evidence_id", sa.Uuid(), nullable=False),
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
        sa.Column("occurred_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_case_evidence_ordinal_non_negative"),
        sa.CheckConstraint("weight > 0", name="ck_case_evidence_weight_positive"),
        sa.CheckConstraint("awarded >= 0", name="ck_case_evidence_awarded_non_negative"),
        sa.CheckConstraint(
            f"failure_category IS NULL OR failure_category IN ({_FAILURE_CATEGORIES})",
            name="ck_case_evidence_failure_category",
        ),
        sa.CheckConstraint(
            "actual_output_excerpt IS NULL OR char_length(actual_output_excerpt) <= 2000",
            name="ck_case_evidence_actual_excerpt_length",
        ),
        sa.CheckConstraint(
            "expected_output_excerpt IS NULL OR char_length(expected_output_excerpt) <= 2000",
            name="ck_case_evidence_expected_excerpt_length",
        ),
        sa.CheckConstraint(
            "message IS NULL OR char_length(message) <= 1000", name="ck_case_evidence_message_length"
        ),
        sa.CheckConstraint(f"evidence_digest ~ '{_DIGEST}'", name="ck_case_evidence_evidence_digest"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            [f"{SCHEMA}.evaluation_runs.evaluation_run_id"],
            name="fk_case_evidence_run_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("evaluation_run_id", "ordinal", name="uq_case_evidence_run_id_ordinal"),
        sa.PrimaryKeyConstraint("case_evidence_id", name="pk_case_evidence"),
        schema=SCHEMA,
    )

    op.create_table(
        "inbox_events",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("received_at", sa.dialects.postgresql.TIMESTAMP(timezone=True, precision=3), nullable=False),
        sa.PrimaryKeyConstraint("consumer", "event_id", name="pk_inbox_events"),
        schema=SCHEMA,
    )
    op.create_index("ix_inbox_events_received_at", "inbox_events", ["received_at"], schema=SCHEMA)

    for table, comment in (
        ("evaluation_runs", "一次评测运行：五项绑定摘要 + 状态/阶段/单调 sequence；终态必带原因与时刻"),
        ("evaluation_run_state_transitions", "运行状态迁移史；sequence 保证顺序确定（毫秒时间戳不够）"),
        ("progress_events", "SSE 帧的持久化来源；(run, sequence) 唯一 => 单调且可断线重连去重"),
        ("evaluation_results", "汇总结果 + 完整报告 JSON；与结果同事务 => 报告可读即结果已持久化"),
        ("static_findings", "静态明细（报告 static_findings 段的来源）"),
        ("security_findings", "安全发现（静态/执行期；报告 security_conclusion 段的来源）"),
        ("case_evidence", "逐用例证据；(run, ordinal) 唯一 => 每一分可追溯到具体用例"),
        ("inbox_events", "消费者按 event_id 去重（at-least-once 的幂等端）"),
    ):
        op.execute(f"COMMENT ON TABLE {SCHEMA}.{table} IS '{comment}'")


def downgrade() -> None:
    op.drop_index("ix_inbox_events_received_at", table_name="inbox_events", schema=SCHEMA)
    op.drop_table("inbox_events", schema=SCHEMA)
    op.drop_table("case_evidence", schema=SCHEMA)
    op.drop_index("ix_security_findings_run_id", table_name="security_findings", schema=SCHEMA)
    op.drop_table("security_findings", schema=SCHEMA)
    op.drop_index("ix_static_findings_run_id", table_name="static_findings", schema=SCHEMA)
    op.drop_table("static_findings", schema=SCHEMA)
    op.drop_table("evaluation_results", schema=SCHEMA)
    op.drop_table("progress_events", schema=SCHEMA)
    op.drop_table("evaluation_run_state_transitions", schema=SCHEMA)
    op.drop_index("ix_evaluation_runs_question_version_id", table_name="evaluation_runs", schema=SCHEMA)
    op.drop_index("ix_evaluation_runs_state_created_at", table_name="evaluation_runs", schema=SCHEMA)
    op.drop_index("ix_evaluation_runs_submission_id", table_name="evaluation_runs", schema=SCHEMA)
    op.drop_table("evaluation_runs", schema=SCHEMA)
