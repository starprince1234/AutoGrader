"""事件 payload 的**结构性准入**（I32）与失败原因脱敏：事件里只允许标识与去标识化值。

不变量与失败语义见 [docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 6.3/6.4 节；
禁止清单的权威表述在 `MAIN/contracts/asyncapi/events.yaml` 的 `info.description` 与
[../api/events.md](../api/events.md) 第 1 节。本模块**不复制**契约的字段定义，只回答两件事：

1. **准许键集合**（`PAYLOAD_KEYS`）：每个发布 subject 允许哪些键。来源是 `events.outbox` 已有的
   构造器（`submission_accepted_payload`）与 `MAIN/contracts/asyncapi/events.yaml` 的示例；
   `evaluation.started` / `evaluation.failed` 在契约里**没有**示例，其键集合取自
   [../api/events.md](../api/events.md) 第 1 节的语义摘要（"`evaluation_run_id` + 环境摘要"、
   "失败类别"），并在 `PAYLOAD_KEY_SOURCES` 里逐 subject 标注来源，便于复核。
2. **取值形态**：每个键只接受受约束的形态（UUID / `^[0-9a-f]{64}$` 摘要 / 非负计数 / 分数 /
   封闭枚举）。I32 允许的恰是"各类 UUID、`intake`、`stage`、`sequence`、计数、分数、摘要"，
   因此事件 payload **没有自由文本键**——"把源码/参考答案/隐藏用例/凭据/路径塞进某个字段"
   在形态校验处即被拒，而不是靠事后扫描。

`stage` 取值域与迁移 `20260915_0006` 的 `stage` CHECK（`progress_events.stage` 同域）一致；
`failure_category` / `terminal_reason` 同源。这里只引用取值集合，不复制契约字段表。

envelope 的权威结构在 `MAIN/contracts/schemas/event-envelope.schema.json`。`build_envelope` 在返回
**之前**逐条校验该 schema（复用 `autograder_b2.contracts.contract_schema_errors`），未通过即抛
`EventEnvelopeInvalid`——**绝不**把未经契约校验的字节交给发布器。

脱敏（`sanitize_error`）：规则来源与 `docs/architecture/boundaries.md` 第 4 节、
`MAIN/docs/api/identifiers.md` 第 2.1 节一致（内部绝对路径、主机端口、凭据、DSN），占位符词汇也
与日志侧同名（`<redacted-path>` / `<redacted-host>` / `<redacted-credential>`）。本模块**自持实现**
而不是 import `autograder_b2.logging.redaction`：后者由并行工作项编辑（其包 `__init__` 一度引用尚不
存在的模块），事件派发的可用性不押在它上面。这是**候选合并点**（见交付说明的 DEVIATIONS）。
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.contracts import (
    EVENT_ENVELOPE_SCHEMA,
    contract_schema_errors,
    load_contract_schema,
)
from autograder_b2.domain.events import DomainEvent
from autograder_b2.events.outbox import (
    EVALUATION_COMPLETED,
    EVALUATION_FAILED,
    EVALUATION_PROGRESS,
    EVALUATION_STARTED,
    SUBMISSION_ACCEPTED,
)
from autograder_b2.logging.redaction import sanitize_text

#: B2 订阅的唯一 subject（[../api/events.md](../api/events.md) 第 2 节；发布方是 B3）。
QUESTION_VERSION_PUBLISHED = "autograder.v1.question.version.published"
SUBSCRIBED_SUBJECTS: tuple[str, ...] = (QUESTION_VERSION_PUBLISHED,)

#: `b2.outbox_events.last_error` 的 CHECK 上限（迁移 0004）；脱敏结果必须落在其上界内。
MAX_LAST_ERROR_LENGTH = 500
#: 拒绝原因里回显**键名**时的长度上限：键名也是外部可控文本，必须截断+转义。
MAX_KEY_NAME_LENGTH = 60

# --- 取值形态 ---------------------------------------------------------------------------

UUID_SHAPE = "uuid"
DIGEST_SHAPE = "digest"
COUNT_SHAPE = "count"
SCORE_SHAPE = "score"
ENUM_SHAPE = "enum"
BOOL_SHAPE = "bool"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

#: `intake` 取值域（`domain.envelope.SubmissionEnvelope.intake` 的 Literal）。
INTAKE_VALUES: tuple[str, ...] = ("api", "email")
#: `stage` 取值域（迁移 0006 的 `stage` CHECK；`MAIN/contracts/asyncapi/events.yaml` 示例用 `dynamic_execution`）。
STAGE_VALUES: tuple[str, ...] = (
    "queued",
    "fetching_package",
    "static_analysis",
    "security_check",
    "dynamic_execution",
    "scoring",
    "reporting",
    "terminal",
)
#: `failure_category` 取值域（迁移 0006 的 `case_evidence.failure_category` CHECK）。
FAILURE_CATEGORY_VALUES: tuple[str, ...] = (
    "timeout",
    "crash",
    "output_mismatch",
    "resource_limit",
    "missing_output",
    "import_error",
    "security_blocked",
    "infrastructure_error",
)
#: `terminal_reason` 取值域（迁移 0006 的 `evaluation_runs.terminal_reason` CHECK）。
TERMINAL_REASON_VALUES: tuple[str, ...] = (
    "static_blocked",
    "security_blocked",
    "infrastructure_error",
    "internal_error",
)
#: `evaluation.failed` 的 `failure_category` 取值域：**运行级技术失败**与**用例级失败**的并集。
#:
#: 为什么不直接用 `FAILURE_CATEGORY_VALUES`：那是**用例级**集合（迁移 0006 的
#: `case_evidence.failure_category` CHECK），而本事件的语义是"技术失败"，`events.md` §3.5 明确把
#: **内部错误**与"沙箱不可用、依赖故障"并列为触发原因，并要求该事件"必须携带失败类别"。
#: 生产的唯一写入端把**运行终态原因**作为失败类别上报（`orchestrator._finish_failure`），于是
#: `internal_error` 落在集合之外 ⇒ `PayloadRejected` ⇒ 该事件**永远发不出去**、重试到上限后进 DLQ——
#: 而"DB 写入失败 / 库结构与契约冲突"恰在这条分支上，正是最需要告警的技术失败。
#: 取舍：**合并取值域**（承认运行级类别也是合法类别），而不是让写入端谎报一个不准确的类别
#: （那会把错误的失败归类送到 B3，且与 `terminal_reason` 自相矛盾）。
EVALUATION_FAILED_CATEGORY_VALUES: tuple[str, ...] = FAILURE_CATEGORY_VALUES + ("internal_error",)


@dataclass(frozen=True, slots=True)
class KeyRule:
    """一个允许键的取值形态；`values` 只对 `ENUM_SHAPE` 有意义。"""

    shape: str
    values: tuple[str, ...] = ()


_UID = KeyRule(UUID_SHAPE)
_DIGEST = KeyRule(DIGEST_SHAPE)
_COUNT = KeyRule(COUNT_SHAPE)
_SCORE = KeyRule(SCORE_SHAPE)
_BOOL = KeyRule(BOOL_SHAPE)


def _enum(*values: str) -> KeyRule:
    return KeyRule(ENUM_SHAPE, values)


#: 每个发布 subject 的准许键集合（键集合是**白名单**，未知键一律拒绝）。
PAYLOAD_KEYS: Mapping[str, Mapping[str, KeyRule]] = {
    SUBMISSION_ACCEPTED: {
        "submission_id": _UID,
        "student_id": _UID,
        "course_id": _UID,
        "section_id": _UID,
        "question_version_id": _UID,
        "intake": _enum(*INTAKE_VALUES),
    },
    EVALUATION_STARTED: {
        "submission_id": _UID,
        "evaluation_run_id": _UID,
        "question_version_id": _UID,
        "stage": _enum(*STAGE_VALUES),
        "package_digest": _DIGEST,
        "runner_policy_digest": _DIGEST,
        "environment_digest": _DIGEST,
    },
    EVALUATION_PROGRESS: {
        "evaluation_run_id": _UID,
        "stage": _enum(*STAGE_VALUES),
        "sequence": _COUNT,
        "cases_total": _COUNT,
        "cases_done": _COUNT,
    },
    EVALUATION_COMPLETED: {
        "submission_id": _UID,
        "evaluation_run_id": _UID,
        "question_version_id": _UID,
        "score": _SCORE,
        # `max_score` 与 `is_pass` 是 B3 统计口径的前提（见 MAIN 对 CHG-B3-0003 的裁决）：
        # 没有 `max_score` 分数无法分桶，没有 `is_pass` 通过率恒为空。两者都**不是身份信息**，
        # 不违反 events.md §3.4 的去标识化要求；B3 此前只能在白名单里留一个永远收不到的键。
        "max_score": _SCORE,
        "is_pass": _BOOL,
        "result_digest": _DIGEST,
    },
    EVALUATION_FAILED: {
        "evaluation_run_id": _UID,
        "submission_id": _UID,
        "question_version_id": _UID,
        "stage": _enum(*STAGE_VALUES),
        "failure_category": _enum(*EVALUATION_FAILED_CATEGORY_VALUES),
        "terminal_reason": _enum(*TERMINAL_REASON_VALUES),
    },
}

#: 键集合的来源标注（复核用；不参与校验）。
PAYLOAD_KEY_SOURCES: Mapping[str, str] = {
    SUBMISSION_ACCEPTED: "events.outbox.submission_accepted_payload + asyncapi 示例",
    EVALUATION_STARTED: "asyncapi 无示例；取 docs/api/events.md 第 1 节语义摘要（运行标识 + 环境摘要）",
    EVALUATION_PROGRESS: "asyncapi 示例（evaluationProgress/dynamic-execution）",
    EVALUATION_COMPLETED: "asyncapi 示例（evaluationCompleted/completed）",
    EVALUATION_FAILED: "asyncapi 无示例；取 docs/api/events.md 第 1 节语义摘要（失败类别，脱敏）",
}

#: 每个发布 subject 的**必填**键（缺一个即拒；其余准许键按语义可选）。
#:
#: 为什么必须有它：契约里 payload 只是 `{"type": "object"}`（`event-envelope.schema.json`），
#: **没有** per-event 的 payload schema；只做白名单时，`{"failure_category": "internal_error"}`
#: 这种残缺 payload 会一路通过 envelope schema 并真被投递出去——写入端漏一个键不会有任何告警，
#: 而消费者（B3 统计）拿到的是缺字段的事件。
#:
#: 取值口径是"**这个键在该 subject 的每一次真实发布里都存在吗**"，不是"示例里出现过"：
#: - 4 个 subject 的必填集 = 其准许键全集。依据：`MAIN/contracts/asyncapi/events.yaml` 的官方示例
#:   给的是完整键集（`submissionAccepted` / `evaluationCompleted` 两例逐一核对），另两个无示例的
#:   subject 取本模块生产写入端的实际键集（复核探针逐调用点核对）。
#: - `EVALUATION_PROGRESS` 例外：`cases_total` / `cases_done` 是**按阶段可缺**的计数——
#:   写入端在"还不知道总数"的阶段（`_enter_stage(..., cases_total=None)`）会**有意省略**这两个键
#:   （`evaluation/orchestrator.py`）。若把它们也列为必填，该形状会过不了门 ⇒ 事件永不投递、重试到
#:   上限进 DLQ（复核者用真实生产方法复现过这条链）。因此 progress 的必填集收窄为
#:   `evaluation_run_id` / `sequence` / `stage` 这三个**恒在**的键，计数按语义可选；
#:   两种形状（带计数 5 键 / 不带计数 3 键）都必须过门，且缺任一恒在键都必须被拒
#:   （用例 T-EVT-021）。
REQUIRED_KEYS: Mapping[str, tuple[str, ...]] = {
    subject: tuple(sorted(rules)) for subject, rules in PAYLOAD_KEYS.items()
} | {
    EVALUATION_PROGRESS: ("evaluation_run_id", "sequence", "stage"),
}


class PayloadRejected(ValueError):
    """payload 违反 I32 的结构性禁止（未知 subject / 未知键 / 取值形态不符）。

    拒绝原因**只回显键名与期望形态，绝不回显取值**——取值可能就是源码或凭据本身。
    """


class EventEnvelopeInvalid(ValueError):
    """envelope 未通过 `MAIN/contracts/schemas/event-envelope.schema.json` 校验。"""


def allowed_keys(subject: str) -> tuple[str, ...]:
    """某发布 subject 的准许键（排序后的元组）；未知 subject 抛 `PayloadRejected`。"""
    rules = PAYLOAD_KEYS.get(subject)
    if rules is None:
        raise PayloadRejected(f"unknown published subject: {_safe_key_name(subject)}")
    return tuple(sorted(rules))


def required_keys(subject: str) -> tuple[str, ...]:
    """某发布 subject 的必填键（排序后的元组）；未知 subject 抛 `PayloadRejected`。

    必填键一定是准许键的子集（`REQUIRED_KEYS ⊆ PAYLOAD_KEYS[subject]`），除
    `evaluation.progress` 的计数键外等于准许键全集——口径见 `REQUIRED_KEYS` 的注释。
    """
    rules = PAYLOAD_KEYS.get(subject)
    if rules is None:
        raise PayloadRejected(f"unknown published subject: {_safe_key_name(subject)}")
    return tuple(sorted(REQUIRED_KEYS[subject]))


def payload_rejections(subject: str, payload: Mapping[str, object]) -> list[str]:
    """返回 payload 的拒绝原因（空列表 = 准许）；原因已脱敏，可用于落库的 `last_error`。

    三条规则：未知/未发布 subject 拒；未知键拒（白名单）；**必填键缺失拒**（`REQUIRED_KEYS`）。
    """
    rules = PAYLOAD_KEYS.get(subject)
    if rules is None:
        return [f"unknown published subject: {_safe_key_name(subject)}"]
    if not isinstance(payload, Mapping):
        return ["payload must be a JSON object"]
    reasons: list[str] = []
    for name, value in payload.items():
        key = _safe_key_name(name)
        rule = rules.get(name) if isinstance(name, str) else None
        if rule is None:
            reasons.append(f"unknown key {key}")
            continue
        problem = _value_problem(rule, value)
        if problem is not None:
            reasons.append(f"key {key}: {problem}")
    required = REQUIRED_KEYS.get(subject)
    if required is None:
        # 已发布 subject 却没登记必填集 = **我们的漏登记**。此时**不得**退化成"零必填"
        # （fail-open：新 subject 忘登记会让门对它形同虚设），改为拒绝并点名原因——
        # 拒绝是 fail-closed，且原因可检索（不静默、不假装通过）。
        return sorted([*reasons, f"no required-key rule registered for subject {subject}"])
    for name in required:
        if name not in payload:
            reasons.append(f"missing key {_safe_key_name(name)}")
    return sorted(reasons)


def assert_payload_allowed(subject: str, payload: Mapping[str, object]) -> None:
    """不通过即抛 `PayloadRejected`（原因已脱敏、已排序）。"""
    reasons = payload_rejections(subject, payload)
    if reasons:
        raise PayloadRejected(f"{subject}: " + "; ".join(reasons))


def build_envelope(event: DomainEvent) -> dict[str, Any]:
    """把领域事件序列化成契约 envelope（**校验通过之前不返回**）。

    先做 payload 结构准入（I32），再逐条校验 envelope schema；
    任何一步失败都抛异常，调用方据此保留行并计入 `failed`。
    """
    assert_payload_allowed(event.subject, event.payload)
    envelope: dict[str, Any] = {
        "event_id": _canonical_uuid(event.event_id),
        "event_type": event.event_type,
        "schema_version": int(event.schema_version),
        "occurred_at": _occurred_at(event.occurred_at),
        "correlation_id": _canonical_uuid(event.correlation_id),
        "causation_id": (
            None if event.causation_id is None else _canonical_uuid(event.causation_id)
        ),
        "producer": event.producer,
        "payload": {str(key): value for key, value in event.payload.items()},
    }
    errors = envelope_errors(envelope)
    if errors:
        raise EventEnvelopeInvalid(f"{event.subject}: " + "; ".join(errors))
    return envelope


def envelope_bytes(event: DomainEvent) -> bytes:
    """契约 envelope 的 UTF-8 字节（键排序 + 紧凑分隔符 ⇒ 同事件同字节）。"""
    return json.dumps(
        build_envelope(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def envelope_errors(envelope: object) -> list[str]:
    """契约 schema 校验错误（复用 `contract_schema_errors`；空列表 = 通过）。"""
    return contract_schema_errors(EVENT_ENVELOPE_SCHEMA, envelope)


def assert_envelope_valid(envelope: object) -> None:
    """**发布侧**校验：未通过契约 schema 即抛 `EventEnvelopeInvalid`（不放松任何一条）。"""
    errors = envelope_errors(envelope)
    if errors:
        raise EventEnvelopeInvalid("; ".join(errors))


@cache
def _received_validator() -> Any:
    """消费侧校验器：契约 schema 的副本，**去掉 `additionalProperties: false` 这一条**。

    `MAIN/docs/api/events.md` 第 5 节要求消费者"未识别字段容忍（前向兼容：新字段不导致崩溃）"，
    而契约 envelope 自身是 `additionalProperties: false`。两者只在"消费者是否因多出一个字段就
    丢弃消息"这一点上冲突，因此消费侧**只**放开未知字段：其余约束（必填、类型、`format`、
    `pattern`）逐条照旧。发布侧仍走上一个函数（严格，不放松）。
    """
    from jsonschema import Draft202012Validator, FormatChecker

    schema = dict(load_contract_schema(EVENT_ENVELOPE_SCHEMA))
    schema.pop("additionalProperties", None)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def received_envelope_errors(envelope: object) -> list[str]:
    """消费侧校验错误（容忍未知字段；空列表 = 可以处理）。"""
    errors = sorted(_received_validator().iter_errors(envelope), key=lambda e: list(e.path))
    return [f"{'/'.join(str(part) for part in error.path)}: {error.message}" for error in errors]


def assert_received_envelope_valid(envelope: object) -> None:
    """消费侧入口校验：容忍未知字段，其余违约即抛 `EventEnvelopeInvalid`。"""
    errors = received_envelope_errors(envelope)
    if errors:
        raise EventEnvelopeInvalid("; ".join(errors))


# --- 取值形态判定 -----------------------------------------------------------------------


def _value_problem(rule: KeyRule, value: object) -> str | None:
    """返回取值问题描述（`None` = 形态合法）；**不回显取值**。"""
    if rule.shape == UUID_SHAPE:
        return None if _canonical_uuid(value) is not None else "expected uuid"
    if rule.shape == DIGEST_SHAPE:
        return None if isinstance(value, str) and _DIGEST_RE.match(value) else "expected 64 hex digest"
    if rule.shape == COUNT_SHAPE:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "expected non-negative integer"
        return None
    if rule.shape == SCORE_SHAPE:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "expected number"
        if not math.isfinite(float(value)) or float(value) < 0:
            return "expected finite non-negative number"
        return None
    if rule.shape == ENUM_SHAPE:
        if isinstance(value, str) and value in rule.values:
            return None
        return "expected one of: " + ",".join(rule.values)
    if rule.shape == BOOL_SHAPE:
        # `isinstance(True, int)` 为真，所以布尔必须先判，且这里只接受真正的 bool——
        # 让 `1`/`"true"` 过门会把"通过"的判定交给一个未定义的转换。
        return None if isinstance(value, bool) else "expected boolean"
    return "unsupported rule"  # pragma: no cover - 规则表是本模块私有的封闭集合


def _canonical_uuid(value: object) -> str | None:
    """规范化 UUID（带连字符小写）；形态不合法返回 `None`。

    名字形态不合法即拒绝，而不是"截断后照样发"——这正是"把源码塞进 `submission_id`"的挡板。
    """
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _occurred_at(value: object) -> str:
    if not isinstance(value, datetime):
        raise EventEnvelopeInvalid("occurred_at must be a datetime")
    return rfc3339_millis(value)


def _safe_key_name(name: object) -> str:
    """键名回显：截断 + 控制字符转义（键名本身也是外部可控文本）。"""
    text = name if isinstance(name, str) else f"<{type(name).__name__}>"
    escaped = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in text)
    if len(escaped) <= MAX_KEY_NAME_LENGTH:
        return escaped
    return escaped[: MAX_KEY_NAME_LENGTH - 3] + "..."


# --- 失败原因脱敏 -----------------------------------------------------------------------

# 规则本体不在这里：`sanitize_error` 委托 `autograder_b2.logging.redaction.sanitize_text`，那是脱敏
# 规则的**唯一**定义处（`REDACTED_CREDENTIAL`/`REDACTED_HOST`/`REDACTED_PATH` 等标记也定义在那里）。
# 本模块原来自持一份规则副本，结果与日志侧真实漂移（一侧有"裸令牌值"规则、另一侧没有），故删除副本。


def sanitize_error(value: object, *, max_length: int = MAX_LAST_ERROR_LENGTH) -> str:
    """把失败原因收敛成"可落库、可展示"的单行文本（**绝不**含凭据/内部路径/主机端口）。

    实现**委托** `logging.redaction.sanitize_text`（脱敏规则的唯一定义处）。曾经本模块自持一份规则
    副本，理由是当时 `autograder_b2.logging` 包因并行工作项而导入失败；该理由已不存在，而"同一件事
    两处实现"已经真实漂移过一次：本模块有"裸令牌值"规则而日志侧没有，于是同一个令牌在事件里被脱敏、
    在日志里原样落盘。规则只允许有一份。

    保留本函数名是因为调用方（派发器/消费者）已经依赖它；它现在只是"日志侧规则 + 本模块的字段上限"。
    """
    return sanitize_text(value, max_length=max_length)
