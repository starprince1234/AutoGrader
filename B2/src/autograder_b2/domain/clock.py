"""跨领域小工具：时间与内容摘要的**唯一**取法。

时间与摘要必须处处一致，否则"相同绑定摘要 ⇒ 相同分数与证据"不成立：
- 时间：UTC、毫秒精度（`MAIN/docs/api/identifiers.md` 第 1 节）；
- 摘要：小写十六进制 SHA-256。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

DIGEST_PATTERN = r"^[0-9a-f]{64}$"

_DIGEST_RE = re.compile(DIGEST_PATTERN)


def utc_now() -> datetime:
    """当前 UTC 时间，截断到毫秒（跨模块比较不得有精度歧义）。"""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def sha256_hex(content: bytes) -> str:
    """内容摘要（小写十六进制）。"""
    return hashlib.sha256(content).hexdigest()


def is_digest(value: object) -> bool:
    """是否为合法的 64 位小写十六进制摘要。"""
    return isinstance(value, str) and bool(_DIGEST_RE.match(value))
