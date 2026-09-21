"""评语轨的**输入白名单**——本功能的安全边界（`RQ-T-20` 验收 1 / `ADR-0009` 决策 2）。

## 这条边界的实质

评语生成**只能**读已落库的结构化证据：逐用例结论与失败类别、静态规则命中、三态安全结论、
分数构成（用例权重与得分）、以及本次运行的计数与状态。**禁止**读学生源码、参考答案、隐藏用例、
期望输出与任何受保护字段。这正是"模型永远看不到受保护内容"的实现方式，因此它必须能被**证明**，
而不是写在注释里。本模块用三件事让它可证：

1. **字段级 SELECT**：每个查询都由一个显式列清单构造（`RUN_COLUMNS` 等），不 `SELECT *`、
   不加载整行。`case_evidence` 的 `actual_output_excerpt`/`expected_output_excerpt`/`message`
   与两张 finding 表的 `message` 因此**根本不在查询里**——它们在证据行里，但读不到。
2. **`INPUT_SCOPE` 与实际读到的字段集必须相等**，并由 `input_scope` 随响应返回：
   读者能看见白名单的实际内容，而不是只能相信它。测试断言的是
   `evidence_paths(collect_evidence(...)) == INPUT_SCOPE`（既不许少读也不许偷偷多读）。
3. **不 import 任何能拿到源码/期望输出的模块**：`tests/test_commentary_track.py` 断言本包的
   import 闭包与 `FORBIDDEN_EVIDENCE_MODULES` 无交集，并用"把那些访问器换成会抛异常的替身"
   跑通一次真实生成，证明它们不可达。

## 两个**读不到**的证据面（已上报，不在这里假装读过）

契约把"三态安全结论"与"题目的公开元数据"也列进白名单。B2 的现状是：

- **三态安全结论**落地在 `b2.evaluation_runs.security_status`（迁移 0011 追加；由安全判定阶段写入，
  包括 `not_checked`）。**未执行安全判定的运行**该列为 NULL——此时评语看到的是"没有结论"，
  而不是被推断出来的 `not_found`（从 `security_findings` 的行数**推不出**这两态的区别）。
- **题目的公开元数据（语言 / 适配器类型 / 比较策略）B2 从不落库**，因此**不在** `input_scope` 里。
  要补上它需要一个题目元数据快照（新列 + `PackageView` 暴露 adapter kind），属需决策的改动：
  白名单是**许可**而不是义务，少读是严格更安全的，所以这里如实列出"没读"而不是假装读过。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection

from autograder_b2.db.schema import (
    case_evidence,
    evaluation_results,
    evaluation_runs,
    security_findings,
    static_findings,
)

#: 每个查询的**显式列清单**（字段级 SELECT 的唯一来源：列清单改了，`INPUT_SCOPE` 自动跟着改）。
RUN_COLUMNS: tuple[str, ...] = ("state", "cases_total", "cases_done", "security_status")
SCORE_COLUMNS: tuple[str, ...] = ("score", "max_score", "is_pass")
CASE_COLUMNS: tuple[str, ...] = (
    "ordinal",
    "passed",
    "failure_category",
    "weight",
    "awarded",
    "resource_limit_hit",
)
STATIC_COLUMNS: tuple[str, ...] = ("rule_id", "severity", "file", "line", "blocking")
SECURITY_COLUMNS: tuple[str, ...] = ("category", "severity", "detected_in")

#: 允许读取的证据字段清单（**生成时实际读取的字段**；随 `provenance.input_scope` 返回）。
#:
#: **刻意写成字面量而不是从上面的列清单推导**：白名单是**声明**，它必须能独立于实现被审阅；
#: 由实现推导出来的白名单只会"跟着实现变"，那等于没有声明。两者是否一致由
#: `tests/test_commentary_track.py::test_input_scope_matches_the_columns_the_queries_select` 断言——
#: 改动列清单而忘了改这里（或反过来）都会当场失败。
#:
#: 命名规则：`<证据表>.<列名>`，三态安全结论按它在契约报告里的位置命名
#: （`security_conclusion.status`），其来源是 `b2.evaluation_runs.security_status`。
INPUT_SCOPE: tuple[str, ...] = (
    "run.state",
    "run.cases_total",
    "run.cases_done",
    "security_conclusion.status",
    "score.score",
    "score.max_score",
    "score.is_pass",
    "case_evidence.ordinal",
    "case_evidence.passed",
    "case_evidence.failure_category",
    "case_evidence.weight",
    "case_evidence.awarded",
    "case_evidence.resource_limit_hit",
    "static_findings.rule_id",
    "static_findings.severity",
    "static_findings.file",
    "static_findings.line",
    "static_findings.blocking",
    "security_findings.category",
    "security_findings.severity",
    "security_findings.detected_in",
)

#: `INPUT_SCOPE` 应当等于的**投影**（由查询列清单生成；只用于对账，不用于生成白名单）。
INPUT_SCOPE_PROJECTION: tuple[str, ...] = (
    *(f"run.{name}" for name in RUN_COLUMNS if name != "security_status"),
    "security_conclusion.status",
    *(f"score.{name}" for name in SCORE_COLUMNS),
    *(f"case_evidence.{name}" for name in CASE_COLUMNS),
    *(f"static_findings.{name}" for name in STATIC_COLUMNS),
    *(f"security_findings.{name}" for name in SECURITY_COLUMNS),
)

#: 生成路径**不得可达**的模块：能拿到源码、期望输出或完整包的对象都在这里。
#:
#: 这不是风格约束：它们每一个都能把受保护内容带进生成路径（CAS 能读提交字节、
#: `submission_loader` 能重建信封、`runner.package` 持有隐藏用例与期望输出、
#: `integration.adapters` 能把工作区物化到盘上）。
FORBIDDEN_EVIDENCE_MODULES: tuple[str, ...] = (
    "autograder_b2.domain.envelope",
    "autograder_b2.integration.adapters",
    "autograder_b2.integration.submission_loader",
    "autograder_b2.runner.adapters",
    "autograder_b2.runner.package",
    "autograder_b2.runner.run_case",
    "autograder_b2.storage.cas",
)


class CommentaryEvidenceMissing(LookupError):
    """运行不存在（评语生成没有输入可言）。"""


@dataclass(frozen=True, slots=True)
class CommentaryEvidence:
    """评语生成的**全部**输入：一个只含白名单字段的结构化载荷 + 它自己的字段清单。

    `case_weight_total` 是**从取证的 SQL 行直接求和**得来的（不是从 JSON 载荷里的 float 反推）：
    `AdvisoryScore.max_score` 必须与 `b2.case_evidence` 里的权重之和**精确相等**，
    走一遍 JSON 会让 NUMERIC → float 的表示误差有机会混进这个数。
    """

    payload: Mapping[str, Any]
    input_scope: tuple[str, ...]
    case_weight_total: Decimal

    def as_prompt_json(self) -> dict[str, Any]:
        """进 prompt 的载荷（就是白名单载荷本身，不做任何增补）。"""
        return dict(self.payload)


def _rows(connection: Connection, statement: Any) -> list[Mapping[str, Any]]:
    return [dict(row._mapping) for row in connection.execute(statement).fetchall()]


def collect_evidence(connection: Connection, evaluation_run_id: str) -> CommentaryEvidence:
    """读取一次运行的白名单证据（字段级 SELECT；见模块 docstring）。

    `evaluation_results` 只有 `completed` 的运行才有行——没有行时 `score` 是 `None`，
    而不是 0 分（"没有分数"与"0 分"是两件事）。
    """
    run_row = connection.execute(
        sa.select(*(evaluation_runs.c[name] for name in RUN_COLUMNS)).where(
            evaluation_runs.c.evaluation_run_id == sa.cast(evaluation_run_id, sa.Uuid())
        )
    ).fetchone()
    if run_row is None:
        raise CommentaryEvidenceMissing(f"运行不存在：{evaluation_run_id}")
    run = dict(run_row._mapping)

    score_row = connection.execute(
        sa.select(*(evaluation_results.c[name] for name in SCORE_COLUMNS)).where(
            evaluation_results.c.evaluation_run_id == sa.cast(evaluation_run_id, sa.Uuid())
        )
    ).fetchone()

    cases = _rows(
        connection,
        sa.select(*(case_evidence.c[name] for name in CASE_COLUMNS))
        .where(case_evidence.c.evaluation_run_id == sa.cast(evaluation_run_id, sa.Uuid()))
        .order_by(case_evidence.c.ordinal),
    )
    static = _rows(
        connection,
        sa.select(*(static_findings.c[name] for name in STATIC_COLUMNS))
        .where(static_findings.c.evaluation_run_id == sa.cast(evaluation_run_id, sa.Uuid()))
        .order_by(static_findings.c.rule_id, static_findings.c.file, static_findings.c.line),
    )
    security = _rows(
        connection,
        sa.select(*(security_findings.c[name] for name in SECURITY_COLUMNS))
        .where(security_findings.c.evaluation_run_id == sa.cast(evaluation_run_id, sa.Uuid()))
        .order_by(
            security_findings.c.category,
            security_findings.c.severity,
            security_findings.c.detected_in,
        ),
    )

    payload: dict[str, Any] = {
        "run": {
            "state": run["state"],
            "cases_total": run["cases_total"],
            "cases_done": run["cases_done"],
        },
        "security_conclusion": {"status": run["security_status"]},
        "score": (
            None
            if score_row is None
            else {
                "score": float(score_row.score),
                "max_score": float(score_row.max_score),
                "is_pass": bool(score_row.is_pass),
            }
        ),
        "case_evidence": [_json_row(item) for item in cases],
        "static_findings": [_json_row(item) for item in static],
        "security_findings": [_json_row(item) for item in security],
    }
    #: 满分 = 各用例权重之和（`RQ-T-30` 验收 5 / `ADR-0014` 决策 2）。**从 SQL 行求和**，
    #: 与 `evaluation.scoring` 里 `max_score += award.weight` 是同一个算法、同一份输入。
    #: 没有用例证据时是 0：调用方据此拒绝产生参考分（契约要求 `max_score > 0`）。
    return CommentaryEvidence(
        payload=payload,
        input_scope=INPUT_SCOPE,
        case_weight_total=sum((Decimal(item["weight"]) for item in cases), Decimal(0)),
    )


def _json_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """把一行取证结果转成 JSON 可序列化形态（`Decimal`/`bool`/`None` 都归一）。"""
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
            result[key] = value
        else:  # Decimal → float（权重与得分；仅在 JSON 载荷里，不参与任何计算）
            result[key] = float(value)
    return result


def evidence_paths(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """载荷里实际出现的字段路径（`<区段>.<字段>`，列表按元素取并集）。

    这是"到底读了哪些字段"的**可复算读数**：测试拿它与 `INPUT_SCOPE` 比对，
    因此白名单不是声明，而是被测量的东西。列表为空时其字段不会出现——调用方（测试）应当用
    一次**真的有用例证据**的运行来断言相等。
    """
    return tuple(sorted({path for path in _walk(payload, "")}))


def _walk(node: Any, prefix: str) -> Iterator[str]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (Mapping, Sequence)) and not isinstance(value, str):
                yield from _walk(value, child)
            else:
                yield child
        return
    if isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for item in node:
            yield from _walk(item, prefix)
        return
    if prefix:
        yield prefix


__all__ = [
    "CASE_COLUMNS",
    "FORBIDDEN_EVIDENCE_MODULES",
    "INPUT_SCOPE",
    "INPUT_SCOPE_PROJECTION",
    "RUN_COLUMNS",
    "SCORE_COLUMNS",
    "SECURITY_COLUMNS",
    "STATIC_COLUMNS",
    "CommentaryEvidence",
    "CommentaryEvidenceMissing",
    "collect_evidence",
    "evidence_paths",
]
