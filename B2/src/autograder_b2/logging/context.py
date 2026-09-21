"""关联标识的规范化与上下文绑定（贯穿接收 → 静态 → 安全 → 动态 → 报告）。

依据：`MAIN/docs/api/identifiers.md` 第 5 节「请求关联」（入口必须接受并传播 `traceparent`
与 `X-Request-ID`；缺失则入口生成）与第 1 节（标识一律 UUID v4）。

两层用法：

1. **入口**：`bind_operation(headers=...)`（或先 `normalize_correlation_id` 再 `bind_context`）
   把 `X-Request-ID`/`traceparent` 规范化成 `correlation_id`——`traceparent` 取 trace-id 段
   （与 `intake/api.py` 的既有取法一致），非法/缺失一律生成 UUID v4。**规范化**很关键：
   头部是外部可控文本，直接回写就是日志注入面。
2. **各阶段**：同一业务操作内 `bind_context(stage=...)` 只换阶段名，标识沿用，于是"接收/静态/
   安全/动态/报告"五个阶段的日志都带同一个 `correlation_id`，可凭它串起全链路。

`ContextVar` 只在**同一次上下文**内传播：若用了跨线程/跨进程的异步日志处理器，格式化发生在别的
上下文里，此时必须把标识**显式**传给 `log_event`（见 `logger.py` 的 `fields`）。这一限制写在
模块文档里而不是靠"大概能行"。
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace

from autograder_b2.logging import redaction

#: `traceparent` 的形态：`00-<32hex trace-id>-<16hex parent-id>-<2hex flags>`（W3C Trace Context）。
_TRACEPARENT_PARTS = 4


def new_correlation_id() -> str:
    """生成一个新的关联标识（UUID v4）。"""
    return str(uuid.uuid4())


def parse_traceparent(value: str | None) -> str | None:
    """从 `traceparent` 取 trace-id 并规范化成 UUID；形态不合法返回 `None`（**不猜**）。"""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != _TRACEPARENT_PARTS:
        return None
    version, trace_id, parent_id, flags = parts
    if (len(version), len(trace_id), len(parent_id), len(flags)) != (2, 32, 16, 2):
        return None
    return redaction.sanitize_identifier("correlation_id", trace_id)


def parse_request_id(value: str | None) -> str | None:
    """`X-Request-ID` 只有本身就是 UUID 时才采用；其余（含任何外部注入文本）返回 `None`。"""
    if not value:
        return None
    return redaction.sanitize_identifier("correlation_id", value.strip())


def normalize_correlation_id(
    *, x_request_id: str | None = None, traceparent: str | None = None
) -> str:
    """把入口标识规范化成 `correlation_id`；缺失或不合法即生成 UUID v4。

    优先级与 `intake/api.py::_correlation_id` 一致（先 `X-Request-ID`，再 `traceparent`）。
    """
    return parse_request_id(x_request_id) or parse_traceparent(traceparent) or new_correlation_id()


def header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    """大小写不敏感地取一个头部值（`Mapping` 不保证大小写不敏感，所以自己找）。"""
    if not headers:
        return None
    direct = headers.get(name)
    if direct is not None:
        return direct
    wanted = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == wanted:
            return value
    return None


def correlation_id_from_headers(headers: Mapping[str, str] | None) -> str:
    """从头部映射取 `X-Request-ID`/`traceparent` 并规范化（缺失即生成）。"""
    return normalize_correlation_id(
        x_request_id=header_value(headers, "x-request-id"),
        traceparent=header_value(headers, "traceparent"),
    )


@dataclass(frozen=True, slots=True)
class LogContext:
    """一次业务操作的日志上下文：只放**允许记录**的标识与阶段名。"""

    request_id: str | None = None
    correlation_id: str | None = None
    submission_id: str | None = None
    evaluation_run_id: str | None = None
    stage: str | None = None

    def merged(self, **overrides: str | None) -> LogContext:
        """`None` 表示"沿用当前值"（各阶段只换阶段名，不丢标识）。"""
        effective = {key: value for key, value in overrides.items() if value is not None}
        return replace(self, **effective) if effective else self


_EMPTY_CONTEXT = LogContext()
_CONTEXT: ContextVar[LogContext] = ContextVar("autograder_b2_log_context", default=_EMPTY_CONTEXT)


def current_context() -> LogContext:
    """当前绑定的上下文（未绑定时为空上下文，**不**伪造任何标识）。"""
    return _CONTEXT.get()


def current_correlation_id() -> str | None:
    """当前关联标识（未绑定时为 `None`）。"""
    return _CONTEXT.get().correlation_id


@contextlib.contextmanager
def bind_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    submission_id: str | None = None,
    evaluation_run_id: str | None = None,
    stage: str | None = None,
) -> Iterator[LogContext]:
    """绑定/叠加日志上下文，退出时**原样恢复**（嵌套安全）。

    传入的标识会先经 `sanitize_identifier` 校验（形状不合法即**不绑定**，而不是绑一个可疑值）。
    """
    checks = {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "submission_id": submission_id,
        "evaluation_run_id": evaluation_run_id,
    }
    validated: dict[str, str | None] = {}
    for name, value in checks.items():
        if value is None:
            continue
        validated[name] = redaction.sanitize_identifier(name, value)
    if stage is not None:
        validated["stage"] = redaction.sanitize_text(stage, max_length=64) or None
    bound = current_context().merged(**validated)
    token = _CONTEXT.set(bound)
    try:
        yield bound
    finally:
        _CONTEXT.reset(token)


@contextlib.contextmanager
def bind_operation(
    *,
    headers: Mapping[str, str] | None = None,
    x_request_id: str | None = None,
    traceparent: str | None = None,
    correlation_id: str | None = None,
    submission_id: str | None = None,
    evaluation_run_id: str | None = None,
    stage: str | None = None,
) -> Iterator[LogContext]:
    """入口绑定：规范化入口标识 → 绑定上下文 → 交给各阶段沿用。

    `request_id` 与 `correlation_id` 默认相同（一次入口请求即一次业务操作）；显式传入
    `correlation_id`（例如异步任务承接上游关联）时以它为准。
    """
    if x_request_id is None:
        x_request_id = header_value(headers, "x-request-id")
    if traceparent is None:
        traceparent = header_value(headers, "traceparent")
    entry_id = normalize_correlation_id(x_request_id=x_request_id, traceparent=traceparent)
    explicit = redaction.sanitize_identifier("correlation_id", correlation_id)
    with bind_context(
        request_id=entry_id,
        correlation_id=explicit or entry_id,
        submission_id=submission_id,
        evaluation_run_id=evaluation_run_id,
        stage=stage,
    ) as bound:
        yield bound


__all__ = [
    "LogContext",
    "bind_context",
    "bind_operation",
    "correlation_id_from_headers",
    "current_context",
    "current_correlation_id",
    "header_value",
    "new_correlation_id",
    "normalize_correlation_id",
    "parse_request_id",
    "parse_traceparent",
]
