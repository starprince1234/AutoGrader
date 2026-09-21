"""对外时间序列化：UTC RFC 3339、毫秒精度（`MAIN/docs/api/identifiers.md` 第 1 节）。"""

from __future__ import annotations

from datetime import UTC, datetime


def rfc3339_millis(value: datetime) -> str:
    """格式化为 `YYYY-MM-DDTHH:MM:SS.mmmZ`（一律 UTC，毫秒截断）。"""
    moment = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
