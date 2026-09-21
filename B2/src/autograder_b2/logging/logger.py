"""结构化 JSON 日志：**单行 JSON** 渲染 + 白名单脱敏 + 长度上限 + 依赖故障事件。

每条日志都是一个 JSON 对象，一行一条，必备字段 `timestamp`（UTC RFC 3339，毫秒）、`level`、
`event`、`producer`（恒为 `B2`）；`request_id`/`correlation_id`/`submission_id`/
`evaluation_run_id` 等只在**确实存在**时出现（缺失用省略，绝不伪造空串）。

三条硬性质：

1. **一定是合法 JSON**：整行超限时不是把 JSON 文本砍掉（那会产生非法 JSON），而是"先缩字段值、
   再丢可选字段"（`render_json_line`），必备字段与字段前缀始终保留，并加 `truncated: true` 标注。
2. **渲染绝不抛异常、绝不吞整条日志**：`JsonLogFormatter.format` 兜住一切渲染异常并退化成一条
   合法 JSON（`event: logging_render_failed`，只带异常**类型名**）；`SafeStreamHandler` 再把
   目标流编码不了字符降级（`?`），于是"代理码位/多字节字符导致写日志失败"不会发生。
3. **只记录允许的字段**：字段名的准入在 `redaction.sanitize_fields`（白名单，不是黑名单），
   自由文本另有模式替换 + 长度上限 + 控制字符转义。

范围与既有约束的引用见 `redaction.py` 模块文档；字段上限 200 / 整行上限 800 与
`intake/imap.py` 的既有安全日志同值、同风格（但**不**去改那个文件）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO

from autograder_b2.api.formatting import rfc3339_millis
from autograder_b2.domain.events import B2_PRODUCER
from autograder_b2.logging import redaction
from autograder_b2.logging.context import LogContext, current_context

#: 默认整行上限 / 单字段上限（部署可配；`intake/imap.py` 用的是同一组数值）。
DEFAULT_MAX_LINE = redaction.MAX_LOG_LINE
DEFAULT_MAX_FIELD = redaction.MAX_LOG_FIELD

#: 必备字段（任何日志都有；整行压缩时**不**缩、**不**丢，保证前缀可识别）。
MANDATORY_FIELDS: tuple[str, ...] = ("timestamp", "level", "event", "producer")

#: 关联字段的渲染顺序（显式 `extra` 优先于上下文绑定）。
_CONTEXT_FIELD_ORDER: tuple[str, ...] = (
    "request_id",
    "correlation_id",
    "submission_id",
    "evaluation_run_id",
    "stage",
)
#: `event` / logger 名的长度上限（它们是渲染信封的一部分，不能由外部输入撑爆）。
EVENT_MAX_LENGTH = 100
LOGGER_NAME_MAX_LENGTH = 60
STAGE_MAX_LENGTH = 64

#: 没有结构化 payload 的日志（别人用标准 `logging` 写法打的）用这个事件名，原文进自由文本字段。
UNSTRUCTURED_EVENT = "unstructured_log_record"
#: 渲染兜底事件：日志**没有丢**，只是渲染失败了。
RENDER_FAILED_EVENT = "logging_render_failed"

#: 三类依赖不可用各自的事件名（明确、可订阅、可告警）。
SANDBOX_UNAVAILABLE_EVENT = "runner.sandbox_unavailable"
DATABASE_UNAVAILABLE_EVENT = "persistence.database_unavailable"
BROKER_UNAVAILABLE_EVENT = "messaging.broker_unavailable"
#: 未知依赖名时的兜底事件（依赖名进 `dependency` 字段，不另造事件名）。
GENERIC_DEPENDENCY_EVENT = "dependency_unavailable"
DEPENDENCY_STATUS_UNAVAILABLE = "unavailable"
DEPENDENCY_EVENTS: Mapping[str, str] = {
    "sandbox": SANDBOX_UNAVAILABLE_EVENT,
    "database": DATABASE_UNAVAILABLE_EVENT,
    "message_broker": BROKER_UNAVAILABLE_EVENT,
}

#: 结构化 payload 的 `extra` 键：本模块的写法（`fields` 已按白名单脱敏并加过上限）。
B2_EVENT_KEY = "b2_event"
#: `intake/imap.py` 既有的结构化安全日志键（保持可渲染，不改那个文件）。
B2_SECURITY_EVENT_KEY = "b2_security_event"
#: "已脱敏"哨兵：用对象身份而不是 `True`，手写字典无法伪造成"已脱敏"。
_BOUNDED = object()

#: 加 `truncated` 标注的额外字符开销（先预留，避免"标完又超上限"）。
_TRUNCATION_COST = len(', "truncated": true')
#: 整行压缩的档位：值先缩到这些上限，仍超限才丢字段。
_SHRINK_CAPS: tuple[int, ...] = (100, 48, 16)
#: 整行上限的下限：低于它连必备字段都装不下，此时宁可超出配置值也要输出**合法 JSON**。
MIN_MAX_LINE = 240
#: 渲染兜底的最后一行（理论上到不了这里；到了也必须是合法 JSON）。
_LAST_RESORT_LINE = (
    '{"timestamp":"1970-01-01T00:00:00.000Z","level":"CRITICAL",'
    '"event":"' + RENDER_FAILED_EVENT + '","producer":"' + B2_PRODUCER + '"}'
)


def render_json_line(
    data: Mapping[str, Any], *, max_line: int = DEFAULT_MAX_LINE, max_field: int = DEFAULT_MAX_FIELD
) -> str:
    """把字段字典渲染成**单行合法 JSON**，整行长度受 `max_line` 约束。

    超限时的顺序是"缩字段值 → 丢可选字段（自由文本先丢、长值先丢）"，必备字段与字段顺序前缀
    不动；发生任何压缩都加 `truncated: true`。
    """
    limit = max(max_line, MIN_MAX_LINE)
    payload, truncated = _fit(dict(data), limit=limit, max_field=max_field)
    if truncated:
        payload["truncated"] = True
    return _dump(payload)


class JsonLogFormatter(logging.Formatter):
    """把 `LogRecord` 渲染成单行 JSON（白名单脱敏 + 上限 + 近似永不失败）。"""

    def __init__(
        self,
        *,
        max_line: int = DEFAULT_MAX_LINE,
        max_field: int = DEFAULT_MAX_FIELD,
        producer: str = B2_PRODUCER,
    ) -> None:
        super().__init__()
        self._max_line = max_line
        self._max_field = max_field
        self._producer = producer

    @property
    def max_line(self) -> int:
        return self._max_line

    @property
    def max_field(self) -> int:
        return self._max_field

    def format(self, record: logging.LogRecord) -> str:
        """渲染一条日志。**任何异常都不外泄**：渲染失败也产出合法 JSON（日志不丢）。"""
        try:
            payload = self.build_payload(record)
            return render_json_line(
                payload, max_line=self._max_line, max_field=self._max_field
            )
        except Exception as error:  # noqa: BLE001 - 渲染路径必须吞掉一切，日志绝不能因此丢
            return self._render_failure_line(record, error)

    def build_payload(self, record: logging.LogRecord) -> dict[str, Any]:
        """组装待渲染字段（必备字段在前，随后是关联标识、结构化字段、脱敏计数）。"""
        structured = _record_payload(record)
        payload: dict[str, Any] = {
            "timestamp": _timestamp_of(record),
            "level": _level_of(record),
            "event": redaction.sanitize_text(
                structured.event, max_length=EVENT_MAX_LENGTH
            ),
            "producer": self._producer,
        }
        logger_name = redaction.sanitize_text(
            record.name, max_length=LOGGER_NAME_MAX_LENGTH
        )
        if logger_name:
            payload["logger"] = logger_name
        payload.update(_correlation_fields(record, current_context()))
        if structured.structured:
            for name, value in structured.fields.items():
                payload[name] = value
        else:
            original = _safe_message(record)
            if original:
                payload["message"] = redaction.sanitize_text(
                    original, max_length=self._max_field
                )
        if structured.dropped:
            payload["redacted_fields"] = structured.dropped
        if structured.dropped_forbidden:
            payload["redacted_forbidden_fields"] = structured.dropped_forbidden
        return payload

    def _render_failure_line(self, record: logging.LogRecord, error: BaseException) -> str:
        """渲染兜底：只带异常**类型名**（异常文本可能含绝对路径或源码片段），绝不记堆栈。"""
        payload: dict[str, Any] = {
            "timestamp": _timestamp_of(record),
            "level": _level_of(record),
            "event": RENDER_FAILED_EVENT,
            "producer": self._producer,
            "error_type": type(error).__name__,
        }
        try:
            return render_json_line(
                payload, max_line=self._max_line, max_field=self._max_field
            )
        except Exception:  # noqa: BLE001 - 兜底再兜底：连兜底渲染都失败时给出一行合法 JSON
            return _LAST_RESORT_LINE


class SafeStreamHandler(logging.StreamHandler):
    """按目标流编码兜一层的流处理器：编码不了的字符降级为 `?`，**不**让整条日志丢失。

    与默认 `StreamHandler.emit` 的差别只有一处：写之前先确认消息能用目标流的编码表示。真实世界的
    触发点是"代理码位/多字节字符 + 非 UTF-8 控制台"——默认写法会抛 `UnicodeEncodeError`，被
    `handleError` 打成一行 stderr 堆栈，日志内容就没了。降级只影响不可表示的字符，JSON 结构
    （全部 ASCII）与必备字段始终完好，因此写出的每一行仍然是合法 JSON。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            stream = self.stream
            encoding = getattr(stream, "encoding", None) or "utf-8"
            try:
                message.encode(encoding)
            except (LookupError, UnicodeEncodeError):
                message = message.encode(encoding, "replace").decode(encoding, "replace")
            stream.write(message + self.terminator)
            self.flush()
        except Exception:  # noqa: BLE001 - 写日志失败只许走 handleError，绝不许向外抛
            self.handleError(record)


def build_logger(
    name: str = "autograder_b2",
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
    formatter: logging.Formatter | None = None,
    max_line: int = DEFAULT_MAX_LINE,
    max_field: int = DEFAULT_MAX_FIELD,
    propagate: bool = False,
) -> logging.Logger:
    """给一个 B2 logger 装上 JSON 格式化器 + 安全流处理器（重复调用幂等）。"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        if isinstance(handler, SafeStreamHandler):
            logger.removeHandler(handler)
            handler.close()
    handler = SafeStreamHandler(stream if stream is not None else sys.stderr)
    if formatter is None:
        formatter = JsonLogFormatter(max_line=max_line, max_field=max_field)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = propagate
    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    fields: Mapping[str, object] | None = None,
    **extra: object,
) -> dict[str, Any]:
    """记一条结构化事件：`event` 是事件名，`fields`/`extra` 是候选字段（只留白名单里的）。

    返回真正写入的字段字典（已脱敏、已加上限），便于调用方与测试断言"到底记了什么"。
    名字与 `event`/`fields`/`level` 冲突的字段请放进 `fields=`。
    """
    payload: dict[str, object] = dict(fields or {})
    payload.update(extra)
    sanitized = redaction.sanitize_fields(payload)
    logger.log(
        level,
        event,
        extra={
            B2_EVENT_KEY: {
                "event": event,
                "fields": sanitized.fields,
                "bounded": _BOUNDED,
                "dropped": sanitized.dropped,
                "dropped_forbidden": sanitized.dropped_forbidden,
            }
        },
    )
    return sanitized.fields


def log_dependency_unavailable(
    logger: logging.Logger,
    *,
    dependency: str,
    error: BaseException,
    stage: str | None = None,
    reason: str | None = None,
    fields: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """依赖不可用（沙箱 / 数据库 / 消息中间件）统一故障日志，**不吞异常**（调用方继续抛）。

    刻意**不**记录 `str(error)`：异常文本可能带绝对路径，`SyntaxError` 之类的消息甚至会把出错那行
    源码抄进来。这里只记机器可读的 `error_type` + `dependency` + `status`；需要人读的原因文本由
    调用方通过 `reason=` 显式给出（仍会过模式替换与长度上限）。
    """
    payload: dict[str, object] = {
        "dependency": dependency,
        "status": DEPENDENCY_STATUS_UNAVAILABLE,
        "error_type": type(error).__name__,
    }
    if stage is not None:
        payload["stage"] = stage
    if reason is not None:
        payload["reason"] = reason
    if fields:
        payload.update(fields)
    event = DEPENDENCY_EVENTS.get(dependency, GENERIC_DEPENDENCY_EVENT)
    return log_event(logger, event, level=logging.ERROR, fields=payload)


@contextlib.contextmanager
def dependency_guard(
    logger: logging.Logger, *, dependency: str, stage: str | None = None
) -> Iterator[None]:
    """把一段可能因依赖不可用而失败的操作包起来：记一条故障日志，然后**原样抛出**。

    "不吞异常"是硬要求：本上下文管理器只加一条日志，绝不改变控制流（`raise` 原样重抛，类型与
    堆栈都不动）。`KeyboardInterrupt`/`SystemExit` 不当作依赖故障（不记日志、照样传播）。
    """
    try:
        yield
    except Exception as error:
        log_dependency_unavailable(logger, dependency=dependency, error=error, stage=stage)
        raise


# --- 渲染内部 ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RecordPayload:
    """从一条 `LogRecord` 抽出的结构化内容。"""

    event: str
    fields: dict[str, Any]
    dropped: int
    dropped_forbidden: int
    structured: bool


def _record_payload(record: logging.LogRecord) -> _RecordPayload:
    """识别结构化 payload：先本模块的 `b2_event`，再 `intake/imap.py` 的 `b2_security_event`。"""
    own = record.__dict__.get(B2_EVENT_KEY)
    if isinstance(own, Mapping):
        event = _safe_event(own.get("event"))
        raw_fields = own.get("fields")
        if isinstance(raw_fields, Mapping):
            if own.get("bounded") is _BOUNDED:
                # 已在 `log_event` 内脱敏并加过上限：这里只做字段名准入，**不再**二次转义
                # （重复转义会把 `\\n` 叠成 `\\\\n`，让结构化消费者与文本消费者看到不同的值）。
                kept = {
                    name: value
                    for name, value in raw_fields.items()
                    if redaction.is_allowed_field(name)
                }
            else:
                sanitized = redaction.sanitize_fields(raw_fields)
                kept = sanitized.fields
            return _RecordPayload(
                event=event,
                fields=kept,
                dropped=_as_int(own.get("dropped")),
                dropped_forbidden=_as_int(own.get("dropped_forbidden")),
                structured=True,
            )
        return _RecordPayload(event, {}, _as_int(own.get("dropped")), 0, True)
    legacy = record.__dict__.get(B2_SECURITY_EVENT_KEY)
    if isinstance(legacy, Mapping):
        sanitized = redaction.sanitize_fields(
            {name: value for name, value in legacy.items() if name != "event"}
        )
        return _RecordPayload(
            event=_safe_event(legacy.get("event")),
            fields=sanitized.fields,
            dropped=sanitized.dropped,
            dropped_forbidden=sanitized.dropped_forbidden,
            structured=True,
        )
    return _RecordPayload(UNSTRUCTURED_EVENT, {}, 0, 0, False)


def _correlation_fields(record: logging.LogRecord, context: LogContext) -> dict[str, Any]:
    """关联标识：记录上的显式值优先，其次上下文绑定；形状不合法即丢弃（绝不原样记录）。"""
    fields: dict[str, Any] = {}
    for name in _CONTEXT_FIELD_ORDER:
        value = getattr(record, name, None)
        if value is None:
            value = getattr(context, name, None)
        if not isinstance(value, str) or not value:
            continue
        if name in redaction.UUID_FIELDS:
            identifier = redaction.sanitize_identifier(name, value)
            if identifier is not None:
                fields[name] = identifier
            continue
        fields[name] = redaction.sanitize_text(value, max_length=STAGE_MAX_LENGTH)
    return fields


def _safe_event(value: object) -> str:
    """事件名：缺失/非字符串即退化成 `UNSTRUCTURED_EVENT`（不伪造别的事件名）。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNSTRUCTURED_EVENT


def _timestamp_of(record: logging.LogRecord) -> str:
    """日志时间：UTC、RFC 3339、毫秒（与对外时间序列化同一取法）。"""
    created = float(getattr(record, "created", 0.0)) or datetime.now(UTC).timestamp()
    return rfc3339_millis(datetime.fromtimestamp(created, tz=UTC))


def _level_of(record: logging.LogRecord) -> str:
    level = getattr(record, "levelname", None)
    return level if isinstance(level, str) and level else "INFO"


def _safe_message(record: logging.LogRecord) -> str:
    """非结构化日志的原文（`getMessage` 参数不匹配时也不抛异常）。"""
    try:
        return record.getMessage()
    except Exception:  # noqa: BLE001 - 参数不匹配的日志不许把整条记录变成异常
        return ""


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _fit(
    payload: dict[str, Any], *, limit: int, max_field: int
) -> tuple[dict[str, Any], bool]:
    """把 payload 压到 `limit - 标注开销` 以内（只缩值/丢字段，绝不砍 JSON 文本）。"""
    if len(_dump(payload)) <= limit:
        return payload, False
    target = max(limit - _TRUNCATION_COST, 1)
    squeezed = dict(payload)
    for cap in _SHRINK_CAPS:
        if cap >= max_field:
            continue
        for name, value in squeezed.items():
            if name in MANDATORY_FIELDS:
                continue
            squeezed[name] = _shorten(value, cap)
        if len(_dump(squeezed)) <= target:
            return squeezed, True
    for name in _drop_order(squeezed):
        squeezed.pop(name, None)
        if len(_dump(squeezed)) <= target:
            return squeezed, True
    return squeezed, True


def _drop_order(payload: Mapping[str, Any]) -> list[str]:
    """丢弃顺序：自由文本先丢；其余按"渲染后最长"先丢（必备字段永不丢）。"""
    optional = [name for name in payload if name not in MANDATORY_FIELDS]
    free_text = [name for name in optional if name in redaction.FREE_TEXT_FIELDS]
    rest = [name for name in optional if name not in redaction.FREE_TEXT_FIELDS]
    rest.sort(key=lambda name: len(_dump({name: payload[name]})), reverse=True)
    return [*free_text, *rest]


def _shorten(value: object, cap: int) -> object:
    """按新上限缩短一个字段值（字符串截断标注；标量列表逐元素缩短）。"""
    if isinstance(value, str):
        return redaction.truncate_text(value, cap)
    if isinstance(value, list):
        return [_shorten(item, cap) for item in value]
    return value


def _dump(payload: Mapping[str, Any]) -> str:
    """紧凑单行 JSON（分隔符含空格便于 grep；非 ASCII 不转义，保持可读性）。"""
    return json.dumps(payload, ensure_ascii=False, default=_unsupported_value)


def _unsupported_value(value: object) -> str:
    """兜底序列化：任何"漏网"对象都变成类型名占位，而不是让渲染失败或泄漏其内容。"""
    return redaction.sanitize_text(f"<unsupported:{type(value).__name__}>", max_length=64)


__all__ = [
    "B2_EVENT_KEY",
    "B2_SECURITY_EVENT_KEY",
    "BROKER_UNAVAILABLE_EVENT",
    "DATABASE_UNAVAILABLE_EVENT",
    "DEFAULT_MAX_FIELD",
    "DEFAULT_MAX_LINE",
    "DEPENDENCY_EVENTS",
    "DEPENDENCY_STATUS_UNAVAILABLE",
    "EVENT_MAX_LENGTH",
    "GENERIC_DEPENDENCY_EVENT",
    "LOGGER_NAME_MAX_LENGTH",
    "MANDATORY_FIELDS",
    "MIN_MAX_LINE",
    "RENDER_FAILED_EVENT",
    "SANDBOX_UNAVAILABLE_EVENT",
    "UNSTRUCTURED_EVENT",
    "JsonLogFormatter",
    "SafeStreamHandler",
    "build_logger",
    "dependency_guard",
    "log_dependency_unavailable",
    "log_event",
    "render_json_line",
]
