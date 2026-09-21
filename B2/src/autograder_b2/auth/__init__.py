"""B2 认证与授权（依赖 B4 签发的令牌，本地验签，不 import B4 代码）。"""

from __future__ import annotations

from autograder_b2.auth.authorization import AuthorizationError, require_submission_authority
from autograder_b2.auth.tokens import (
    ALGORITHM,
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    ROLES,
    AuthenticationError,
    CourseScope,
    JwksAuthenticator,
    Principal,
)

__all__ = [
    "ALGORITHM",
    "DEFAULT_AUDIENCE",
    "DEFAULT_ISSUER",
    "ROLES",
    "AuthenticationError",
    "AuthorizationError",
    "CourseScope",
    "JwksAuthenticator",
    "Principal",
    "require_submission_authority",
]
