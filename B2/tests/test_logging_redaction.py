"""结构化日志与脱敏（RQ-B-2-99）的**纯单元**测试（无数据库、无网络）。

验收标准 → 测试（逐条都真的跑过）：

- 单行合法 JSON + 必备字段 + `producer == "B2"`：
  `test_mandatory_fields_are_present_and_the_line_is_one_json_object`、
  `test_unstructured_standard_logging_records_still_render_as_json`
- 关联标识贯穿接收 → 静态 → 安全 → 动态 → 报告：
  `test_entry_identifiers_are_normalised_into_a_correlation_id`、
  `test_one_operation_keeps_one_correlation_id_across_all_stages`、
  `test_context_binding_nests_and_is_restored`
- 源码 / 参考答案 / 凭据 / 绝对路径不出现，而允许字段确实出现（反证）：
  `test_content_credentials_and_paths_never_reach_the_rendered_line`、
  `test_free_text_keeps_allowed_content_patterns_redacted`、
  `test_code_shaped_free_text_is_dropped_whole`
- 控制字符 / 换行注入被转义、整行仍单行且受限：
  `test_control_characters_are_escaped_and_the_line_stays_single_and_bounded`
- 代理码位不抛异常、不丢日志：
  `test_surrogate_code_points_do_not_raise_or_lose_the_log`、
  `test_ascii_only_stream_still_gets_the_log`
- 字段上限 200 / 整行上限 800（可配、有截断标注）：
  `test_every_field_value_is_bounded`、`test_line_cap_is_configurable_and_marks_truncation`
- 三类依赖故障各有事件名与字段、不吞异常、不记堆栈：
  `test_three_dependency_failures_have_distinct_events_and_do_not_swallow`
- 白名单性质（未知字段名不被原样写入）：
  `test_unknown_payload_field_names_are_never_written`、
  `test_known_forbidden_names_and_nested_values_are_counted_not_written`、
  `test_identifier_fields_reject_non_uuid_values`

所有断言都基于**真的渲染出来的日志行**（`JsonLogFormatter` + `SafeStreamHandler` 写进 `StringIO`），
不是对内部变量的猜测。测试不需要数据库：`tests/conftest.py` 的 PostgreSQL 夹具在这里不参与。
"""

from __future__ import annotations

import io
import itertools
import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from autograder_b2.logging import (
    ALLOWED_FIELDS,
    B2_SECURITY_EVENT_KEY,
    BROKER_UNAVAILABLE_EVENT,
    DATABASE_UNAVAILABLE_EVENT,
    DEFAULT_MAX_FIELD,
    DEFAULT_MAX_LINE,
    DEPENDENCY_STATUS_UNAVAILABLE,
    FORBIDDEN_FIELD_NAMES,
    MANDATORY_FIELDS,
    MAX_LOG_FIELD,
    MAX_LOG_LINE,
    REDACTED_CREDENTIAL,
    REDACTED_PATH,
    RENDER_FAILED_EVENT,
    SANDBOX_UNAVAILABLE_EVENT,
    TRUNCATION_MARKER,
    UUID_FIELDS,
    JsonLogFormatter,
    SafeStreamHandler,
    bind_context,
    bind_operation,
    build_logger,
    correlation_id_from_headers,
    current_context,
    current_correlation_id,
    dependency_guard,
    log_dependency_unavailable,
    log_event,
    looks_like_code,
    new_correlation_id,
    normalize_correlation_id,
    render_json_line,
    sanitize_fields,
    sanitize_identifier,
    sanitize_text,
)

RFC3339_MILLIS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TRACE_ID_AS_UUID = "4bf92f35-77b3-4da6-a3ce-929d0e0e4736"

#: 上游/攻击者可控样本：源码原文、参考答案样文、凭据、绝对路径。
SOURCE_SAMPLE = "def solve(n):\n    return n * 2  # AC-9911\n"
REFERENCE_SAMPLE = "REFERENCE-ANSWER-42 output: [1, 2, 3]"
JWT_SAMPLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLWJ5dGVz"
WINDOWS_PATH = r"C:\Users\x\secret.py"
POSIX_PATH = "/srv/b2/artifacts/2026/09/15/answer_key.py"
#: 样本里的可辨认碎片（不含反斜杠：反斜杠会被转义，用碎片避免"靠转义侥幸通过"）。
LEAK_NEEDLES = (
    "AC-9911",
    "def solve",
    "return n * 2",
    "REFERENCE-ANSWER-42",
    JWT_SAMPLE,
    "sup3r-s3cret",
    "hunter2",
    "secret.py",
    "srv/b2",
    "answer_key",
)

_logger_counter = itertools.count()


def unique_logger_name(label: str) -> str:
    """每个用例一个独立 logger 名，避免处理器串联导致跨用例串日志。"""
    return f"autograder_b2.test.{label}.{next(_logger_counter)}"


def capture_logger(label: str, **options: Any) -> tuple[logging.Logger, io.StringIO]:
    """装好 JSON 格式化器 + 安全流处理器的 logger 与它的缓冲区。"""
    buffer = io.StringIO()
    return build_logger(unique_logger_name(label), stream=buffer, **options), buffer


def rendered(buffer: io.StringIO) -> tuple[str, list[dict[str, Any]]]:
    """返回原始文本与逐行解析结果。

    `json.loads` 成功即证明"每行都是合法 JSON"；行数 == 换行符数即证明"没有被拆成多行"。
    """
    raw = buffer.getvalue()
    assert raw, "没有写出任何日志"
    assert raw.endswith("\n")
    rows = [json.loads(line) for line in raw.split("\n") if line]
    assert len(rows) == raw.count("\n"), "日志里出现了换行注入：一行变成了多行"
    return raw, rows


def assert_absent(text: str, needles: Sequence[str]) -> None:
    """既查原文也查"反转义后"的形态：转义不能成为漏检的理由。"""
    variants = (text, text.replace("\\\\", "\\"), text.replace("\\n", "\n"))
    for needle in needles:
        assert needle, "空针没有证明力"
        for variant in variants:
            assert needle not in variant, f"日志里出现了禁止内容: {needle!r}"


def exploding_payload() -> Mapping[str, object]:
    """一个 `__iter__` 直接抛异常的 Mapping（用来确认渲染兜底路径真的会被走到）。"""

    class Exploding(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise KeyError(key)

        def __iter__(self):
            raise RuntimeError("payload 迭代失败")

        def __len__(self) -> int:
            return 1

    return Exploding()


def make_record(*, event: str, fields: object, level: int = logging.INFO) -> logging.LogRecord:
    """手工造一条带结构化 payload 的 `LogRecord`（不用 logger，便于测渲染本身）。"""
    record = logging.LogRecord(
        name=unique_logger_name("manual"),
        level=level,
        pathname="<redacted-path>",
        lineno=1,
        msg=event,
        args=(),
        exc_info=None,
    )
    record.__dict__["b2_event"] = {"event": event, "fields": fields}
    return record


class RecordKeeper(logging.Handler):
    """只收不写的处理器：用来断言"结构化消费者到底看到了什么"。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# --- 1. 单行合法 JSON 与必备字段 -------------------------------------------------


def test_mandatory_fields_are_present_and_the_line_is_one_json_object() -> None:
    """每条日志是单行 JSON，必含 timestamp/level/event/producer，且 `producer == "B2"`。"""
    logger, buffer = capture_logger("mandatory")
    log_event(logger, "static.analysis_completed", fields={"rule_id": "RULE-001", "count": 4})

    raw, rows = rendered(buffer)
    assert len(rows) == 1
    row = rows[0]
    assert set(MANDATORY_FIELDS) <= set(row)
    assert row["producer"] == "B2"
    assert row["level"] == "INFO"
    assert row["event"] == "static.analysis_completed"
    assert RFC3339_MILLIS.match(row["timestamp"]), row["timestamp"]
    assert row["timestamp"].endswith("Z"), "时间必须是 UTC（RFC 3339）"
    assert row["rule_id"] == "RULE-001"
    assert row["count"] == 4
    # 未提供的关联标识不伪造空串（省略，而不是 ""）。
    assert "correlation_id" not in row
    assert "request_id" not in row
    assert '""' not in raw
    assert raw.count("\n") == 1


def test_default_line_and_field_caps_are_800_and_200() -> None:
    """默认上限就是既有安全日志的那一组数值（整行 800 / 单字段 200）。"""
    assert (DEFAULT_MAX_LINE, MAX_LOG_LINE) == (800, 800)
    assert (DEFAULT_MAX_FIELD, MAX_LOG_FIELD) == (200, 200)
    assert JsonLogFormatter().max_line == 800
    assert JsonLogFormatter().max_field == 200


def test_unstructured_standard_logging_records_still_render_as_json() -> None:
    """别人用标准 `logging` 写法打的日志也必须渲染成合法 JSON（没结构化不能崩）。"""
    logger, buffer = capture_logger("unstructured")
    logger.warning("legacy text message without structure")

    _raw, rows = rendered(buffer)
    row = rows[0]
    assert set(MANDATORY_FIELDS) <= set(row)
    assert row["level"] == "WARNING"
    assert row["event"] == "unstructured_log_record"
    assert row["message"] == "legacy text message without structure"


def test_existing_intake_security_event_style_is_rendered_structured() -> None:
    """`intake/imap.py` 既有的 `extra={"b2_security_event": {...}}` 写法被识别为结构化事件。"""
    record = logging.LogRecord(
        name="autograder_b2.intake.security",
        level=logging.WARNING,
        pathname="<redacted-path>",
        lineno=1,
        msg="email_intake_security ...",
        args=(),
        exc_info=None,
    )
    record.__dict__[B2_SECURITY_EVENT_KEY] = {
        "event": "email_intake_rejected",
        "class": "archive_container",
        "reason_code": "archive_bomb",
        "uidvalidity": 7,
        "uid": 3,
        "attachment_name": r"C:\temp\main.py",
        "detail": "declared expansion exceeds the limit; token=sup3r-s3cret",
    }

    row = json.loads(JsonLogFormatter().format(record))
    assert row["event"] == "email_intake_rejected"
    assert row["reason_code"] == "archive_bomb"
    assert (row["uidvalidity"], row["uid"]) == (7, 3)
    assert row["producer"] == "B2"
    # 既有写法里没脱敏的字段在这里补上：文件名里的绝对路径与凭据都不许留。
    assert row["attachment_name"] == REDACTED_PATH
    assert_absent(json.dumps(row, ensure_ascii=False), ["sup3r-s3cret", "temp", "main.py"])


def test_render_failure_still_produces_a_valid_single_line_json() -> None:
    """渲染失败**不丢日志**：退化成合法 JSON，只带异常类型名（不记异常文本/堆栈）。"""
    line = JsonLogFormatter().format(
        make_record(event="static.analysis_completed", fields=exploding_payload())
    )

    assert "\n" not in line
    row = json.loads(line)
    assert row["event"] == RENDER_FAILED_EVENT
    assert row["error_type"] == "RuntimeError"
    assert row["producer"] == "B2"
    assert set(MANDATORY_FIELDS) <= set(row)
    assert RFC3339_MILLIS.match(row["timestamp"])


# --- 2. 关联标识规范化与传播 -----------------------------------------------------


def test_entry_identifiers_are_normalised_into_a_correlation_id() -> None:
    """`traceparent`/`X-Request-ID` 规范化成 `correlation_id`；缺失/非法即生成 UUID v4。"""
    explicit = str(uuid.uuid4())
    assert normalize_correlation_id(traceparent=TRACEPARENT) == TRACE_ID_AS_UUID
    assert normalize_correlation_id(x_request_id=explicit) == explicit
    # 优先级与 `intake/api.py::_correlation_id` 一致：X-Request-ID 先，traceparent 后。
    assert normalize_correlation_id(x_request_id=explicit, traceparent=TRACEPARENT) == explicit

    assert uuid.UUID(normalize_correlation_id()).version == 4
    assert uuid.UUID(new_correlation_id()).version == 4

    for hostile in ("not-a-uuid", "a" * 4096, "x\r\nX-Injected: yes", "", "   ", "00-zz-zz-zz"):
        candidate = normalize_correlation_id(x_request_id=hostile, traceparent=hostile)
        assert uuid.UUID(candidate).version == 4, hostile
        assert "Injected" not in candidate, "头部原文不得被当成标识写进日志"

    assert correlation_id_from_headers({"X-Request-ID": explicit}) == explicit
    assert correlation_id_from_headers({"TraceParent": TRACEPARENT}) == TRACE_ID_AS_UUID
    assert uuid.UUID(correlation_id_from_headers(None)).version == 4
    assert uuid.UUID(correlation_id_from_headers({})).version == 4
    assert correlation_id_from_headers({"x-request-id": "not-a-uuid"}) != "not-a-uuid"


def test_one_operation_keeps_one_correlation_id_across_all_stages() -> None:
    """接收 → 静态 → 安全 → 动态 → 报告：五条日志同一 `correlation_id`，可凭它串起全链路。"""
    logger, buffer = capture_logger("chain")
    submission_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    stages: list[tuple[str, str, dict[str, object]]] = [
        ("intake", "intake.submission_received", {"count": 1}),
        ("static", "static.analysis_completed", {"rule_id": "RULE-SQLI-001", "count": 3}),
        ("security", "security.decision_recorded", {"status": "not_found", "violation_count": 0}),
        (
            "dynamic",
            "dynamic.evaluation_completed",
            {"evaluation_run_id": run_id, "passed": 2, "failed": 0, "score": 87},
        ),
        ("report", "report.rendered", {"size_bytes": 2048}),
    ]

    with bind_operation(
        headers={"traceparent": TRACEPARENT}, submission_id=submission_id, stage="intake"
    ) as bound:
        correlation_id = bound.correlation_id
        for stage, event, fields in stages:
            with bind_context(stage=stage):
                log_event(logger, event, fields=fields)

    _raw, rows = rendered(buffer)
    assert len(rows) == len(stages)
    assert [row["event"] for row in rows] == [event for _stage, event, _fields in stages]
    assert [row["stage"] for row in rows] == [stage for stage, _event, _fields in stages]
    assert {row["correlation_id"] for row in rows} == {TRACE_ID_AS_UUID}
    assert {row["request_id"] for row in rows} == {TRACE_ID_AS_UUID}
    assert correlation_id == TRACE_ID_AS_UUID
    # 提交标识绑定在整段业务操作上；运行标识只在动态阶段出现（缺失即省略，不伪造）。
    assert [row.get("submission_id") for row in rows] == [submission_id] * len(stages)
    assert [row.get("evaluation_run_id") for row in rows] == [None, None, None, run_id, None]
    for row in rows:
        assert set(MANDATORY_FIELDS) <= set(row)
    # 退出绑定后不残留上下文。
    assert current_correlation_id() is None


def test_context_binding_nests_and_is_restored() -> None:
    """嵌套绑定只换阶段名不丢标识，退出即恢复；非法标识**不绑定**。"""
    outer = str(uuid.uuid4())
    assert current_context().correlation_id is None
    with bind_context(correlation_id=outer, stage="intake"):
        assert current_correlation_id() == outer
        with bind_context(stage="static"):
            assert current_correlation_id() == outer
            assert current_context().stage == "static"
        assert current_context().stage == "intake"
        with bind_context(correlation_id="not-a-uuid"):
            assert current_correlation_id() == outer, "形状不合法不得顶替既有标识"
    assert current_correlation_id() is None


def test_explicit_record_fields_win_over_the_bound_context() -> None:
    """显式字段优先于上下文（异步阶段直接把标识交给 `log_event` 时不该被上下文覆盖）。"""
    logger, buffer = capture_logger("explicit")
    bound_id = str(uuid.uuid4())
    explicit_id = str(uuid.uuid4())
    with bind_context(correlation_id=bound_id):
        log_event(logger, "report.rendered", fields={"correlation_id": explicit_id, "count": 1})

    _raw, rows = rendered(buffer)
    assert rows[0]["correlation_id"] == explicit_id


# --- 3. 脱敏：源码 / 参考答案 / 凭据 / 绝对路径 ------------------------------------


def test_content_credentials_and_paths_never_reach_the_rendered_line() -> None:
    """注入源码原文、参考答案样文、token、`Bearer xxx`、绝对路径：都不许出现在日志行里。

    反证同样重要：允许记录的字段（UUID、规则 ID、计数、分数、状态）**确实**写进去了，
    否则"因为什么都没记所以扫不到"会伪装成通过。
    """
    logger, buffer = capture_logger("leaks")
    submission_id = str(uuid.uuid4())
    log_event(
        logger,
        "static.analysis_completed",
        fields={
            # 允许记录的字段
            "submission_id": submission_id,
            "rule_id": "RULE-SQLI-001",
            "count": 3,
            "score": 87,
            "state": "evaluating",
            "status": "ok",
            # 禁止出现的字段名（白名单外，含已知禁止名）
            "source_code": SOURCE_SAMPLE,
            "content": SOURCE_SAMPLE,
            "reference_answer": REFERENCE_SAMPLE,
            "expected_output": REFERENCE_SAMPLE,
            "token": JWT_SAMPLE,
            "storage_path": WINDOWS_PATH,
            "student_email": "student@example.edu",
            # 禁止出现的内容（白名单内的自由文本）
            "detail": SOURCE_SAMPLE,
            "reason": f"Authorization: Bearer {JWT_SAMPLE} token=sup3r-s3cret password=hunter2",
            "message": f"cannot read {WINDOWS_PATH} or {POSIX_PATH}",
        },
    )

    raw, rows = rendered(buffer)
    row = rows[0]
    assert_absent(raw, LEAK_NEEDLES)
    for value in row.values():
        assert_absent(str(value), LEAK_NEEDLES)

    # 反证：允许字段确实在
    assert row["submission_id"] == submission_id
    assert row["rule_id"] == "RULE-SQLI-001"
    assert row["count"] == 3
    assert row["score"] == 87
    assert row["state"] == "evaluating"
    assert row["status"] == "ok"
    # 已知模式替换留下了可辨认占位符（证明是"替换"而不是"整条没记"）
    assert REDACTED_PATH in raw
    assert REDACTED_CREDENTIAL in raw
    # 丢弃被记成计数（不记名字、不记取值）
    assert row["redacted_fields"] >= 8
    assert row["redacted_forbidden_fields"] >= 4


def test_free_text_keeps_allowed_content_patterns_redacted() -> None:
    """自由文本里的绝对路径与凭据换成占位符，周边可读文本保留（不是整条丢弃）。"""
    logger, buffer = capture_logger("patterns")
    log_event(
        logger,
        "intake.submission_received",
        fields={
            "detail": f"artifact {WINDOWS_PATH} stored as {POSIX_PATH}",
            "reason": f"db at postgresql://b2:pw@db.internal:5432/b2 refused; Bearer {JWT_SAMPLE}",
        },
    )

    raw, rows = rendered(buffer)
    row = rows[0]
    assert row["detail"] == f"artifact {REDACTED_PATH} stored as {REDACTED_PATH}"
    assert "refused" in row["reason"]
    assert REDACTED_CREDENTIAL in row["reason"]
    assert_absent(raw, ["secret.py", "srv/b2", "answer_key", JWT_SAMPLE, "b2:pw", "db.internal"])


def test_code_shaped_free_text_is_dropped_whole() -> None:
    """源码形态的自由文本整段丢弃（fail-closed），而不是"转义后照样记"。"""
    body = "\n".join(f"    return values[{index}]  # hidden-case {index}" for index in range(5))
    payload = {
        "detail": f"def hidden(n):\n{body}\n",
        "reason": "the package import failed",
        "message": "plain single line failure reason",
    }

    sanitized = sanitize_fields(payload)
    assert "detail" not in sanitized.fields, "函数定义/缩进块形态必须整段丢弃"
    assert "reason" not in sanitized.fields, "以 import 开头的代码形态同样丢弃（宁可少记）"
    assert sanitized.fields["message"] == "plain single line failure reason"
    assert sanitized.dropped == 2
    assert sanitized.dropped_forbidden == 2
    assert looks_like_code("def solve(n):\n    return n") is True
    assert looks_like_code("static analysis completed in 12 ms") is False


def test_byte_values_are_dropped_because_they_are_content_not_identifiers() -> None:
    """`bytes` 值（源码/附件原文的常见形态）一律丢弃：它不可能是标识或计数。"""
    sanitized = sanitize_fields(
        {"detail": b"print('student source')", "size_bytes": 1024, "count": 3}
    )
    assert "detail" not in sanitized.fields
    assert sanitized.fields == {"size_bytes": 1024, "count": 3}
    assert sanitized.dropped_forbidden == 1


def test_unknown_payload_field_names_are_never_written() -> None:
    """白名单性质：未知字段名既不写名字、也不写取值（只记丢弃计数）。"""
    logger, buffer = capture_logger("whitelist")
    submission_id = str(uuid.uuid4())
    log_event(
        logger,
        "security.decision_recorded",
        fields={
            "submission_id": submission_id,
            "status": "found",
            "unknown_thing": "UNKNOWN-MARKER-77",
            "another_unknown": 12345,
        },
    )

    raw, rows = rendered(buffer)
    row = rows[0]
    assert_absent(raw, ["UNKNOWN-MARKER-77", "unknown_thing", "another_unknown"])
    assert row["submission_id"] == submission_id
    assert row["status"] == "found"
    assert row["redacted_fields"] == 2
    assert "redacted_forbidden_fields" not in row


def test_known_forbidden_names_and_nested_values_are_counted_not_written() -> None:
    """已知禁止字段名与嵌套结构都只计数不落笔；允许与禁止字段名集合互不相交。"""
    payload = {
        "submission_id": str(uuid.uuid4()),
        "expected_output": REFERENCE_SAMPLE,
        "reference_answer": REFERENCE_SAMPLE,
        "detail": {"source": SOURCE_SAMPLE, "expected": REFERENCE_SAMPLE},
        "traceback": "Traceback (most recent call last): ...",
    }

    sanitized = sanitize_fields(payload)
    assert set(sanitized.fields) == {"submission_id"}
    assert sanitized.dropped == 4
    assert sanitized.dropped_forbidden == 3
    assert not (ALLOWED_FIELDS & FORBIDDEN_FIELD_NAMES), "允许与禁止的字段名集合必须互不相交"


def test_identifier_fields_reject_non_uuid_values() -> None:
    """标识字段只接受 UUID：塞源码进去即丢弃（不做"截断后照样记"）。"""
    sanitized = sanitize_fields(
        {
            "submission_id": SOURCE_SAMPLE,
            "evaluation_run_id": "not-a-uuid",
            "correlation_id": str(uuid.uuid4()),
            "request_id": None,
        }
    )
    assert "submission_id" not in sanitized.fields
    assert "evaluation_run_id" not in sanitized.fields
    assert sanitized.dropped == 2
    assert sanitized.dropped_forbidden == 2
    assert sanitize_identifier("submission_id", "0F8FAD5B-D9CB-469F-A165-70867728950E") == (
        "0f8fad5b-d9cb-469f-a165-70867728950e"
    )
    assert sanitize_identifier("submission_id", 42) is None


def test_prose_free_text_is_recorded_so_callers_still_owe_safe_reasons() -> None:
    """如实记录本模块的**残余边界**：非代码形态的单行散文会被记录。

    这正是"自由文本只放已知安全的短原因短语"这条调用约定存在的原因；本用例把边界固定下来，
    以免它悄悄漂移成"我们以为能识别任意内容"。
    """
    sanitized = sanitize_fields({"detail": "the reference answer is forty two"})
    assert sanitized.fields["detail"] == "the reference answer is forty two"
    assert sanitized.dropped == 0


# --- 4. 注入防护：控制字符 / 超长文本 / 代理码位 ----------------------------------


def test_control_characters_are_escaped_and_the_line_stays_single_and_bounded() -> None:
    """`\\n`/`\\r`/制表符 + 20 万字符注入自由文本：仍是单行、仍合法 JSON、长度受限。"""
    logger, buffer = capture_logger("injection", max_line=300)
    hostile = "line-one\nline-two\rwith\ttabs\x00and-nul " + "y" * 200_000
    log_event(logger, "static.analysis_completed", fields={"detail": hostile, "count": 1})

    raw, rows = rendered(buffer)
    assert raw.count("\n") == 1, "日志被拆成了多行（换行注入）"
    assert len(raw) <= 301, f"整行超出上限: {len(raw)}"
    row = rows[0]
    assert row["event"] == "static.analysis_completed"
    assert row["count"] == 1
    assert row["truncated"] is True
    detail = row["detail"]
    assert len(detail) <= MAX_LOG_FIELD
    assert "\\n" in detail, "换行必须被转义成两字符文本"
    assert "\\t" in detail and "\\r" in detail and "\\x00" in detail
    assert "line-one" in detail, "保留可识别前缀"
    assert "\x00" not in detail
    assert sanitize_text("a\nb") == "a\\nb"


def test_every_field_value_is_bounded() -> None:
    """结构化 `extra` 的每个字段值都有上限（默认 200）；文本与结构化消费者看到同一份值。

    三个 5000 字符字段会把整行顶到 800 以上，于是**整行上限**再往下压一档——这是设计行为，
    所以断言写成"渲染值 ≤ 结构化值"；结构化侧的上限由捕获到的 `LogRecord` 直接证明。
    """
    logger, buffer = capture_logger("bounded")
    keeper = RecordKeeper()
    logger.addHandler(keeper)
    written = log_event(
        logger,
        "dynamic.evaluation_completed",
        fields={"detail": "z" * 5_000, "reason": "r" * 5_000, "message": "m" * 5_000},
    )
    logger.removeHandler(keeper)

    assert len(keeper.records) == 1
    structured = keeper.records[0].__dict__["b2_event"]["fields"]
    for name in ("detail", "reason", "message"):
        assert len(structured[name]) <= DEFAULT_MAX_FIELD, f"{name} 未受字段上限约束"
        assert structured[name].endswith(TRUNCATION_MARKER), "截断必须有标注"
        assert structured[name] == written[name], "命令式返回值与结构化 extra 必须一致"

    _raw, rows = rendered(buffer)
    for name in ("detail", "reason", "message"):
        assert len(rows[0][name]) <= len(structured[name]), "整行上限只许更严，不许更松"


def test_structured_values_and_text_values_agree_when_the_line_fits() -> None:
    """行内放得下时，文本消费者与结构化消费者看到**逐字相同**的已截断值。"""
    logger, buffer = capture_logger("bounded-one")
    written = log_event(
        logger, "dynamic.evaluation_completed", fields={"detail": "z" * 5_000, "count": 1}
    )

    _raw, rows = rendered(buffer)
    assert len(rows[0]["detail"]) == DEFAULT_MAX_FIELD
    assert rows[0]["detail"] == written["detail"]
    assert rows[0]["detail"].endswith(TRUNCATION_MARKER)
    assert rows[0].get("truncated") is None, "行内放得下就不该标注整行截断"


def test_line_cap_is_configurable_and_marks_truncation() -> None:
    """整行上限可配（默认 800）：超限时缩字段/丢字段，必备字段与前缀保留并标注截断。"""
    logger, buffer = capture_logger("linecap", max_line=400)
    log_event(
        logger,
        "report.rendered",
        fields={
            "submission_id": str(uuid.uuid4()),
            "rule_id": "R" * 300,
            "rule_ids": ["R" * 180 for _ in range(16)],
            "detail": "D" * 300,
            "reason": "E" * 300,
            "message": "F" * 300,
            "count": 7,
        },
    )

    raw, rows = rendered(buffer)
    assert len(raw.rstrip("\n")) <= 400, f"整行超出配置上限: {len(raw)}"
    row = rows[0]
    assert row["event"] == "report.rendered"
    assert row["level"] == "INFO"
    assert row["producer"] == "B2"
    assert RFC3339_MILLIS.match(row["timestamp"]), "必备字段必须原样保留（可识别前缀）"
    assert row["truncated"] is True
    assert row.get("count") == 7, "短字段不该被牺牲"


def test_default_line_cap_applies_without_configuration() -> None:
    """默认 800：把白名单字段塞满也不允许超出整行上限。"""
    logger, buffer = capture_logger("defaultcap")
    log_event(
        logger,
        "report.rendered",
        fields={
            "detail": "a" * 200,
            "reason": "b" * 200,
            "message": "c" * 200,
            "rule_id": "R" * 200,
            "counts": ["C" * 200 for _ in range(16)],
        },
    )

    raw, rows = rendered(buffer)
    assert len(raw.rstrip("\n")) <= DEFAULT_MAX_LINE
    assert rows[0]["truncated"] is True
    assert rows[0]["producer"] == "B2"


def test_tiny_line_cap_still_renders_valid_json() -> None:
    """即便配置上限极小，渲染结果仍是合法 JSON（必备字段优先于上限，绝不让 JSON 变非法）。"""
    line = render_json_line(
        {
            "timestamp": "2026-09-15T08:30:00.000Z",
            "level": "INFO",
            "event": "report.rendered",
            "producer": "B2",
            "detail": "x" * 10_000,
        },
        max_line=10,
    )
    row = json.loads(line)
    assert set(MANDATORY_FIELDS) <= set(row)
    assert row["producer"] == "B2"
    assert len(line) <= 240, "低于下限的上限按下限执行（必备字段装得下）"
    if "detail" in row:
        assert len(row["detail"]) <= 100


def test_surrogate_code_points_do_not_raise_or_lose_the_log() -> None:
    """代理码位（`\\udce4`）不得让渲染抛异常、也不得丢掉那条日志。"""
    logger, buffer = capture_logger("surrogate")
    log_event(
        logger,
        "report.rendered",
        fields={"detail": "before \udce4 after \udcff tail", "count": 2},
    )

    raw, rows = rendered(buffer)
    row = rows[0]
    assert row["count"] == 2, "日志没有丢"
    assert "\udce4" not in row["detail"], "代理码位必须被替换掉（否则无法 UTF-8 编码）"
    assert "before" in row["detail"] and "after" in row["detail"]
    assert "\ufffd" in row["detail"]
    assert sanitize_text("x \udce4 y") == "x \ufffd y"
    raw.encode("utf-8")  # 渲染结果一定能被 UTF-8 编码（写日志不会因编码失败而丢）


def test_ascii_only_stream_still_gets_the_log() -> None:
    """目标流编码不了多字节/代理码位时降级写入，而不是抛异常丢日志（JSON 结构保持完好）。"""
    binary = io.BytesIO()
    logger = logging.getLogger(unique_logger_name("asciistream"))
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    wrapper = io.TextIOWrapper(binary, encoding="ascii", newline="")
    handler = SafeStreamHandler(wrapper)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    log_event(logger, "report.rendered", fields={"detail": "中文 surrogate \udce4", "count": 5})
    handler.flush()
    wrapper.flush()
    logger.removeHandler(handler)

    written = binary.getvalue().decode("ascii")
    assert written.endswith("\n")
    assert written.count("\n") == 1
    row = json.loads(written)
    assert row["event"] == "report.rendered"
    assert row["count"] == 5
    assert row["producer"] == "B2"


# --- 5. 异常路径：依赖不可用 ------------------------------------------------------


def test_three_dependency_failures_have_distinct_events_and_do_not_swallow() -> None:
    """沙箱/数据库/消息中间件三类故障各有事件名与字段；异常原样抛出；不记堆栈路径。

    异常消息里故意放绝对路径与一行源码：它们**不得**出现在日志里（只记异常类型名）。
    """
    logger, buffer = capture_logger("dependencies")
    cases = [
        ("sandbox", SANDBOX_UNAVAILABLE_EVENT, RuntimeError),
        ("database", DATABASE_UNAVAILABLE_EVENT, TimeoutError),
        ("message_broker", BROKER_UNAVAILABLE_EVENT, ConnectionError),
    ]
    hostile = f"failed on {WINDOWS_PATH}: def solve(n): return n * 2"

    for dependency, _event, error_type in cases:
        # 不吞异常：类型原样抛出。
        with (
            pytest.raises(error_type),
            dependency_guard(logger, dependency=dependency, stage="dynamic"),
        ):
            raise error_type(hostile)

    raw, rows = rendered(buffer)
    assert len(rows) == len(cases)
    assert [row["event"] for row in rows] == [event for _dep, event, _err in cases]
    assert [row["dependency"] for row in rows] == [dep for dep, _event, _err in cases]
    assert {row["status"] for row in rows} == {DEPENDENCY_STATUS_UNAVAILABLE}
    assert {row["level"] for row in rows} == {"ERROR"}
    assert [row["error_type"] for row in rows] == [error.__name__ for _dep, _event, error in cases]
    assert {row["stage"] for row in rows} == {"dynamic"}
    assert_absent(raw, ["secret.py", "def solve", "return n * 2", "Traceback", 'File "'])
    for row in rows:
        assert set(MANDATORY_FIELDS) <= set(row)


def test_dependency_reason_is_optional_and_sanitized() -> None:
    """调用方显式给出的 `reason` 会过模式替换与长度上限；未给出时不留 `reason` 字段。"""
    logger, buffer = capture_logger("dependency-reason")
    log_dependency_unavailable(
        logger,
        dependency="database",
        error=TimeoutError("connect timeout"),
        stage="intake",
        reason=f"pool exhausted at {POSIX_PATH}; token=sup3r-s3cret",
    )
    log_dependency_unavailable(
        logger, dependency="sandbox", error=RuntimeError("boom"), stage="dynamic"
    )

    raw, rows = rendered(buffer)
    assert rows[0]["reason"] == f"pool exhausted at {REDACTED_PATH}; token={REDACTED_CREDENTIAL}"
    assert "reason" not in rows[1]
    assert_absent(raw, ["answer_key", "srv/b2", "sup3r-s3cret"])
    assert rows[1]["event"] == SANDBOX_UNAVAILABLE_EVENT


def test_unknown_dependency_name_falls_back_to_one_generic_event() -> None:
    """未知依赖名不编造事件名：落一个通用事件，依赖名进 `dependency` 字段。"""
    logger, buffer = capture_logger("dependency-generic")
    log_dependency_unavailable(
        logger, dependency="vector_store", error=RuntimeError("down"), stage="static"
    )

    _raw, rows = rendered(buffer)
    assert rows[0]["event"] == "dependency_unavailable"
    assert rows[0]["dependency"] == "vector_store"


def test_keyboard_interrupt_is_not_reported_as_a_dependency_failure() -> None:
    """中断不是依赖故障：不记日志、照样传播（避免把 Ctrl-C 报成"沙箱不可用"）。"""
    logger, buffer = capture_logger("interrupt")
    with (
        pytest.raises(KeyboardInterrupt),
        dependency_guard(logger, dependency="sandbox", stage="dynamic"),
    ):
        raise KeyboardInterrupt
    assert buffer.getvalue() == ""


# --- 6. 取值形状与集合不变量 ------------------------------------------------------


def test_uuid_fields_are_a_subset_of_allowed_fields() -> None:
    """形状校验只作用于白名单字段（不存在"能校验但记不了"的字段）。"""
    assert UUID_FIELDS <= ALLOWED_FIELDS
    assert FORBIDDEN_FIELD_NAMES.isdisjoint(ALLOWED_FIELDS)


def test_non_finite_floats_do_not_break_json() -> None:
    """`NaN`/`Infinity` 不是合法 JSON：转成文本，绝不让整行变成非法 JSON。"""
    logger, buffer = capture_logger("floats")
    log_event(logger, "dynamic.evaluation_completed", fields={"score": float("nan"), "count": 3})

    _raw, rows = rendered(buffer)
    assert rows[0]["count"] == 3
    assert rows[0]["score"] == "nan"


def test_sanitize_text_truncates_with_a_marker_and_keeps_the_prefix() -> None:
    """自由文本的长文本截断：保留前缀 + 标注，长度含标注不超上限。"""
    text = sanitize_text("k" * 1_000)
    assert len(text) <= DEFAULT_MAX_FIELD
    assert text.startswith("k" * 10)
    assert text.endswith(TRUNCATION_MARKER)
    assert sanitize_text("short") == "short"
