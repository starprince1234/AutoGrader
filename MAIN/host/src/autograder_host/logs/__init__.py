"""Unified log center package inside MAIN/host."""

from .context import (
    bind_request_context,
    current_correlation_id,
    current_request_id,
    normalize_uuid,
    reset_request_context,
)
from .sink import (
    SinkHandler,
    attach_log_sink,
    flush_log_sink,
    get_active_sink,
    get_sink_stats,
    reset_log_sink_for_tests,
)

__all__ = [
    "SinkHandler",
    "attach_log_sink",
    "bind_request_context",
    "current_correlation_id",
    "current_request_id",
    "flush_log_sink",
    "get_active_sink",
    "get_sink_stats",
    "normalize_uuid",
    "reset_log_sink_for_tests",
    "reset_request_context",
]
