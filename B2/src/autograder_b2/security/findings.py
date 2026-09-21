"""安全发现的持久化（`b2.security_findings`，迁移 0006 已建表——本模块**不写迁移**）。

分层：`build_*` 是**纯函数**（不出 IO），`record_security_findings` 是唯一的写库入口，签名为
`(connection, ...)`——不持有 engine、不开事务，提交与回滚由编排层决定。

约束（与契约枚举、表列、报告 schema 对齐，违反即抛 `ValueError`，不静默修正）：

- `evaluation_run_id` / `security_finding_id` 必须是 UUID 串（表列为 `Uuid`，报告 schema 为 `format: uuid`）；
- `category` / `severity` / `detected_in` 只取契约枚举（见 `docs/architecture/interfaces.md` 第 4 节）；
- `message` 非空且 ≤1000 字符（脱敏由 finding 构造方负责）；
- 行级 `blocking` = `decision.blocking and finding.severity == "high"`（"该发现是否阻断了动态执行"）；
- 落库前先 `assert_decision_invariants(decision)`——不写一份自相矛盾的安全结论。

`detected_in == "runtime"` 的发现同样可落库（列允许 `rule_id` 为空）；运行期与静态发现共用一张表。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import get_args

import sqlalchemy as sa
from sqlalchemy import Connection

from autograder_b2.db.schema import security_findings
from autograder_b2.domain.clock import utc_now
from autograder_b2.domain.security import (
    DetectedIn,
    SecurityCategory,
    SecurityDecision,
    SecurityFinding,
    SecuritySeverity,
)
from autograder_b2.security.decision import assert_decision_invariants

#: 表的列名（供测试与调用方核对，避免手抄列清单漂移）。
SECURITY_FINDING_COLUMNS: tuple[str, ...] = tuple(security_findings.columns.keys())

#: `message` 列对应契约上限（`MAIN/contracts/schemas/evaluation-report.schema.json`）。
MESSAGE_LIMIT = 1000


def _require_uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"{field} 必须是 UUID 串：{type(value).__name__}") from None


def build_security_finding_row(
    finding: SecurityFinding,
    *,
    evaluation_run_id: str,
    decision_blocking: bool,
    occurred_at: datetime,
) -> dict[str, object]:
    """把一条 finding 折算成 `security_findings` 的一行（纯函数，不接触数据库）。"""
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at 必须带时区（UTC）")
    if finding.category not in get_args(SecurityCategory):
        raise ValueError(f"category 越界：{finding.category!r}")
    if finding.severity not in get_args(SecuritySeverity):
        raise ValueError(f"severity 越界：{finding.severity!r}")
    if finding.detected_in not in get_args(DetectedIn):
        raise ValueError(f"detected_in 越界：{finding.detected_in!r}")
    if not finding.message:
        raise ValueError("message 不得为空")
    if len(finding.message) > MESSAGE_LIMIT:
        raise ValueError(f"message 超过 {MESSAGE_LIMIT} 字符上限")
    if finding.detected_in == "static" and not finding.rule_id:
        raise ValueError("静态发现必须携带 rule_id")
    return {
        "security_finding_id": _require_uuid(finding.finding_id, "finding_id"),
        "evaluation_run_id": _require_uuid(evaluation_run_id, "evaluation_run_id"),
        "category": finding.category,
        "severity": finding.severity,
        "detected_in": finding.detected_in,
        "rule_id": finding.rule_id,
        "message": finding.message,
        "blocking": bool(decision_blocking and finding.severity == "high"),
        "occurred_at": occurred_at,
    }


def build_security_finding_rows(
    decision: SecurityDecision,
    *,
    evaluation_run_id: str,
    occurred_at: datetime | None = None,
) -> tuple[Mapping[str, object], ...]:
    """把整份结论折算成待插入的行（纯函数）。无 finding 时返回空元组。"""
    assert_decision_invariants(decision)
    stamp = occurred_at or utc_now()
    return tuple(
        build_security_finding_row(
            finding,
            evaluation_run_id=evaluation_run_id,
            decision_blocking=decision.blocking,
            occurred_at=stamp,
        )
        for finding in decision.findings
    )


def record_security_findings(
    connection: Connection,
    *,
    evaluation_run_id: str,
    decision: SecurityDecision,
    occurred_at: datetime | None = None,
) -> int:
    """把结论里的 finding 写入 `b2.security_findings`，返回写入行数（薄封装，事务由调用方掌控）。

    同一份结论重复落库会在主键 `security_finding_id`（由规则/文件/行确定性派生）上冲突——这是
    有意的：安全发现**不双写**，需要重放时请以既有行为准。
    """
    rows = build_security_finding_rows(
        decision, evaluation_run_id=evaluation_run_id, occurred_at=occurred_at
    )
    if not rows:
        return 0
    connection.execute(sa.insert(security_findings), [dict(row) for row in rows])
    return len(rows)
