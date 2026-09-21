"""题目质量汇总（`CHG-0015` 的 `getEvaluationQualitySummary`）：**只有计数**的聚合。

契约权威：`MAIN/contracts/openapi/b2.yaml` 的 `getEvaluationQualitySummary` 与
`EvaluationQualitySummary`。本模块是服务层（路由在 `api.evaluation_routes`），只读 B2 自己的四张表：
`evaluation_runs` / `case_evidence` / `static_findings` / `evaluation_results`。

## 去标识化是结构性事实，不是"记得别写 id"

响应形状由 `QualitySummary` 决定，而它的字段**全是计数或计数数组**：没有 `student_id`、
没有 `submission_id`、没有源码、没有期望输出。`case_failure_rates` 按 `ordinal` 聚合——
`ordinal` 是**不可变题目版本内的位置**，不是谁的标识。因此"这个聚合走不回某个人"不是靠代码审查，
而是靠这个类型里根本不存在能承载标识的槽位（`tests/test_b2_routes.py` 用"字段名与取值双重扫描"
把这条钉住）。

## 三个容易被不诚实地渲染的字段，逐个说明口径

1. **`security_not_checked` 与 `security_not_found` 分开**：取值来自
   `evaluation_runs.security_status` 的三态快照（迁移 0011）。`security_not_checked` 同时包含
   **显式 `not_checked`** 与**从未走到安全判定阶段的运行**（该列为 `NULL`）——两者都是"我们没看过"，
   而契约只给了三个桶。迁移 0011 的 CHECK 把该列限制成"三态或 NULL"，因此三者构成对
   `attempt_count` 的**完整划分**（`found + not_found + not_checked == attempt_count`）。
2. **`unmeasured_runs` 单列**：契约的定义是"沙箱**无法报告**资源峰值的运行数"。判据取自
   **已持久化的报告**：一条运行的报告里至少要有一个用例带**真实的资源读数**
   （`resource` 为对象，且 `memory_peak_bytes`/`cpu_user_seconds`/`cpu_system_seconds`/`pids_peak`
   至少一项是数字）才算"测到了"。`wall_seconds` **不算**——它是 B2 自己量的墙钟，不是沙箱回读的峰值。
   没有报告（`queued`/`running`/`rejected`/`failed`）或报告里一个峰值读数都没有的运行，都计入
   `unmeasured_runs`。于是"没有资源数据"读起来就是"没有数据"，而不是"没占用"。
3. **逐用例失败类别** 取自 `case_evidence.failure_category`（**单位是"用例"**）。
   `infrastructure_error` 是 `FailureCategory` 的成员，因此**自成一条**，不会与 `output_mismatch`
   之类的难度信号混在一起。**如实标注**：运行级的 `terminal_reason='infrastructure_error'`
   （在进入沙箱前就技术失败、因此没有任何 `case_evidence` 行）不会出现在本数组里——它体现为
   `attempt_count` 与 `case_failure_rates` 的 `attempts` 之和之间的差。契约没有为运行级技术失败
   提供字段，本模块**不发明**一个（见 `docs/api/contract.md` 的契约缺口一节）。

## 静态规则命中为什么包含"零命中"的规则

契约说这一栏的用途是发现"某条静态规则从未命中过任何提交（它要么没用，要么写错了）"——
只报出现过的规则永远发现不了这件事。因此本模块以 B2 **自己的规则目录**（`static.rules.RULES`）
为准逐条列出，未命中即 `count: 0`。数据里若出现目录之外的 `rule_id`（旧目录写入的历史行），
也照样列出而不是丢掉：隐藏数据比多一行更坏。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from autograder_b2.auth.authorization import require_question_quality_authority
from autograder_b2.auth.tokens import Principal
from autograder_b2.db.schema import (
    case_evidence,
    evaluation_results,
    evaluation_runs,
    static_findings,
)
from autograder_b2.static.rules import RULES

#: 三态安全结论的取值域（迁移 0011 的 CHECK 同域）。
SECURITY_FOUND = "found"
SECURITY_NOT_FOUND = "not_found"
SECURITY_NOT_CHECKED = "not_checked"

#: "测到过资源峰值"的判据：这四个都是**沙箱回读**的峰值读数。
#: `wall_seconds` 不在其中（B2 自己量的墙钟，与"沙箱能否回读峰值"无关）。
_MEASURED_RESOURCE_KEYS = (
    "memory_peak_bytes",
    "cpu_user_seconds",
    "cpu_system_seconds",
    "pids_peak",
)
#: JSONPath 谓词：报告里至少一个用例带一项真实的峰值读数。路径是**字面常量**（无外部输入拼接）。
_MEASURED_JSONPATH = (
    "$.case_evidence[*].resource ? ("
    + " || ".join(f"@.{key} != null" for key in _MEASURED_RESOURCE_KEYS)
    + ")"
)
#: 报告里"用例列表"的 JSON 键（`_MEASURED_JSONPATH` 的前缀必须与它一致）。
_MEASURED_SQL = sa.text(
    f"jsonb_path_exists(evaluation_results.report, '{_MEASURED_JSONPATH}')"
)


class InvalidQualityFilterError(ValueError):
    """查询参数不合契约（`question_version_id` 非 UUID、`from`/`to` 非时间戳）→ 对外 422。"""


class QualityInvariantError(RuntimeError):
    """三态安全结论之和对不上 `attempt_count`（数据异常，不是客户端错误）。

    迁移 0011 的 CHECK 让"三态或 NULL"成为结构性事实，因此这条在本代码下**打不到**；
    它的存在是为了让"将来有人放宽那个 CHECK"表现为一次明确的失败，而不是静默把未知取值折进
    `security_not_checked`。
    """


@dataclass(frozen=True, slots=True)
class FailureCategoryCount:
    category: str
    count: int


@dataclass(frozen=True, slots=True)
class StaticRuleHit:
    rule_id: str
    severity: str
    count: int


@dataclass(frozen=True, slots=True)
class CaseFailureRate:
    ordinal: int
    attempts: int
    failures: int


@dataclass(frozen=True, slots=True)
class QualitySummary:
    """契约 `EvaluationQualitySummary` 的载荷：**没有任何可承载个人标识的字段**。

    唯一的标识是 `question_version_id`——它标识**题目版本**，不是人。
    """

    question_version_id: str
    attempt_count: int
    failure_categories: tuple[FailureCategoryCount, ...] = field(default_factory=tuple)
    static_rule_hits: tuple[StaticRuleHit, ...] = field(default_factory=tuple)
    case_failure_rates: tuple[CaseFailureRate, ...] = field(default_factory=tuple)
    unmeasured_runs: int = 0
    security_found: int = 0
    security_not_found: int = 0
    security_not_checked: int = 0

    def as_contract_body(self) -> dict[str, Any]:
        """契约 JSON 形状（字段集合固定，`additionalProperties: false`）。"""
        return {
            "question_version_id": self.question_version_id,
            "attempt_count": self.attempt_count,
            "failure_categories": [
                {"category": item.category, "count": item.count}
                for item in self.failure_categories
            ],
            "static_rule_hits": [
                {"rule_id": item.rule_id, "severity": item.severity, "count": item.count}
                for item in self.static_rule_hits
            ],
            "case_failure_rates": [
                {"ordinal": item.ordinal, "attempts": item.attempts, "failures": item.failures}
                for item in self.case_failure_rates
            ],
            "unmeasured_runs": self.unmeasured_runs,
            "security_found": self.security_found,
            "security_not_found": self.security_not_found,
            "security_not_checked": self.security_not_checked,
        }


def parse_question_version_id(raw: str | None) -> str:
    """`question_version_id` 必填且必须是 UUID；缺失/非法 → `InvalidQualityFilterError`（422）。

    为什么不用 404：B2 **不拥有题目**（B3 拥有），"这个题目版本不存在"与"没有任何次尝试"在 `b2`
    schema 内不可区分（与成绩端点记录的契约缺口同类）。因此非法标识按**请求校验失败**处理。
    """
    if raw is None or raw == "":
        raise InvalidQualityFilterError("question_version_id is required")
    try:
        return str(uuid.UUID(raw))
    except (AttributeError, TypeError, ValueError) as error:
        raise InvalidQualityFilterError("question_version_id must be a UUID") from error


def parse_timestamp(raw: str | None, field_name: str) -> datetime | None:
    """解析 `from`/`to`（契约 `Timestamp`，RFC 3339）；空串=未提供，非法 → 422。"""
    if raw is None or raw == "":
        return None
    text = raw.strip()
    if text[-1:] in {"Z", "z"}:
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise InvalidQualityFilterError(f"{field_name} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise InvalidQualityFilterError(f"{field_name} must carry a timezone")
    return parsed.astimezone(UTC)


class EvaluationQualityService:
    """题目质量聚合（`getEvaluationQualitySummary` 的服务层）。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def summarize(
        self,
        *,
        principal: Principal,
        question_version_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> QualitySummary:
        """聚合一个题目版本的质量信号。

        范围 = `evaluation_runs.question_version_id == 题目版本` 且
        `since <= created_at <= until`（**两端都含**；缺省即不限）。异常：
        `AuthorizationError` → 路由层 403。

        "两端都含"是显式选择（契约没有规定边界语义）：教师按日期挑一个区间时，直觉是"这一天也算在内"；
        代价是相邻两天会重复计入交界那一天的行。口径写在这里，以免读者按半开区间去解释数字。
        """
        require_question_quality_authority(principal)
        with self._engine.connect() as connection:
            # REPEATABLE READ：几个读数必须来自**同一个**快照，否则并发提交会让
            # `found + not_found + not_checked == attempt_count` 这类恒等式在读数之间失效。
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            scope = _scope_cte(question_version_id=question_version_id, since=since, until=until)
            attempt_count = int(
                connection.execute(sa.select(sa.func.count()).select_from(scope)).scalar_one()
            )
            status_counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    sa.select(evaluation_runs.c.security_status, sa.func.count())
                    .select_from(_joined(scope, evaluation_runs))
                    .group_by(evaluation_runs.c.security_status)
                ).fetchall()
            }
            categories = connection.execute(
                sa.select(case_evidence.c.failure_category, sa.func.count())
                .select_from(_joined(scope, case_evidence))
                .where(case_evidence.c.failure_category.is_not(None))
                .group_by(case_evidence.c.failure_category)
                .order_by(case_evidence.c.failure_category)
            ).fetchall()
            rule_counts = connection.execute(
                sa.select(static_findings.c.rule_id, static_findings.c.severity, sa.func.count())
                .select_from(_joined(scope, static_findings))
                .group_by(static_findings.c.rule_id, static_findings.c.severity)
            ).fetchall()
            case_rows = connection.execute(
                sa.select(
                    case_evidence.c.ordinal,
                    sa.func.count(),
                    sa.func.count().filter(sa.not_(case_evidence.c.passed)),
                )
                .select_from(_joined(scope, case_evidence))
                .group_by(case_evidence.c.ordinal)
                .order_by(case_evidence.c.ordinal)
            ).fetchall()
            measured_runs = int(
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(_joined(scope, evaluation_results))
                    .where(_MEASURED_SQL)
                ).scalar_one()
            )

        found = status_counts.pop(SECURITY_FOUND, 0)
        not_found = status_counts.pop(SECURITY_NOT_FOUND, 0)
        # 显式 `not_checked` 与"从未走到安全判定阶段"（NULL）同为"我们没看过"，契约只有三个桶。
        not_checked = status_counts.pop(SECURITY_NOT_CHECKED, 0) + status_counts.pop(None, 0)
        if status_counts:
            raise QualityInvariantError(
                "unexpected security_status value(s): "
                + ", ".join(sorted(str(key) for key in status_counts))
            )
        if found + not_found + not_checked != attempt_count:
            raise QualityInvariantError(
                "the three security states do not partition attempt_count:"
                f" {found} + {not_found} + {not_checked} != {attempt_count}"
            )
        return QualitySummary(
            question_version_id=question_version_id,
            attempt_count=attempt_count,
            failure_categories=tuple(
                FailureCategoryCount(category=str(row[0]), count=int(row[1])) for row in categories
            ),
            static_rule_hits=_rule_hits(rule_counts),
            case_failure_rates=tuple(
                CaseFailureRate(ordinal=int(row[0]), attempts=int(row[1]), failures=int(row[2]))
                for row in case_rows
            ),
            unmeasured_runs=max(attempt_count - measured_runs, 0),
            security_found=found,
            security_not_found=not_found,
            security_not_checked=not_checked,
        )


def _scope_cte(
    *, question_version_id: str, since: datetime | None, until: datetime | None
) -> Any:
    """范围 = 该题目版本的运行（可带时间窗）；几个读数共用同一个 CTE。"""
    conditions = [evaluation_runs.c.question_version_id == uuid.UUID(question_version_id)]
    if since is not None:
        conditions.append(evaluation_runs.c.created_at >= since)
    if until is not None:
        conditions.append(evaluation_runs.c.created_at <= until)
    return (
        sa.select(evaluation_runs.c.evaluation_run_id)
        .where(sa.and_(*conditions))
        .cte("quality_scope")
    )


def _joined(scope: Any, table: Any) -> Any:
    """`table ⋈ 范围`（每个读数都只统计范围内运行的行）。"""
    return table.join(scope, table.c.evaluation_run_id == scope.c.evaluation_run_id)


def _rule_hits(rows: list[Any]) -> tuple[StaticRuleHit, ...]:
    """规则命中：以 B2 规则目录逐条为准（未命中即 0），并补上目录之外的 `rule_id`。

    目录顺序是 `rule_id` 字典序（`static.rules` 已冻结），目录之外的按字典序追加在后面——
    顺序确定，因此同输入同输出。
    """
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        counts[(str(row[0]), str(row[1]))] = int(row[2])
    hits: list[StaticRuleHit] = [
        StaticRuleHit(
            rule_id=rule.rule_id,
            severity=rule.severity,
            count=counts.pop((rule.rule_id, rule.severity), 0),
        )
        for rule in RULES
    ]
    for (rule_id, severity), count in sorted(counts.items()):
        hits.append(StaticRuleHit(rule_id=rule_id, severity=severity, count=count))
    return tuple(hits)


__all__ = [
    "SECURITY_FOUND",
    "SECURITY_NOT_CHECKED",
    "SECURITY_NOT_FOUND",
    "CaseFailureRate",
    "EvaluationQualityService",
    "FailureCategoryCount",
    "InvalidQualityFilterError",
    "QualityInvariantError",
    "QualitySummary",
    "StaticRuleHit",
    "parse_question_version_id",
    "parse_timestamp",
]
