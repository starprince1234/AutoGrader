"""B2 对外错误形态（RFC 9457）与格式化工具。"""

from __future__ import annotations

from autograder_b2.api.errors import (
    FORBIDDEN,
    PAYLOAD_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    UNAUTHORIZED,
    UNPROCESSABLE,
    Problem,
    install_problem_handlers,
    problem_response,
)
from autograder_b2.api.formatting import rfc3339_millis

__all__ = [
    "FORBIDDEN",
    "PAYLOAD_TOO_LARGE",
    "SERVICE_UNAVAILABLE",
    "UNAUTHORIZED",
    "UNPROCESSABLE",
    "Problem",
    "install_problem_handlers",
    "problem_response",
    "rfc3339_millis",
]
