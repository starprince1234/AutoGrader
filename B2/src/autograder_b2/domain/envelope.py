"""`SubmissionEnvelope`：API 与邮件两条入口归一化后的**唯一**内部形态。

字段、不变量编号（I1–I8）与失败语义见
[docs/architecture/interfaces.md](../../docs/architecture/interfaces.md) 第 2 节；本模块是该节的
代码落地，**不得**增删字段、**不得**引入别名或双写。契约字段定义不在此复制（契约权威在
`MAIN/contracts/schemas/submission-envelope.schema.json`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

#: 信封被拒的稳定原因码。与 `interfaces.md` 第 2.4 节表格逐条一致。
RejectionReason = Literal[
    "schema_invalid",
    "path_not_allowed",
    "digest_mismatch",
    "empty_fileset",
    "duplicate_path",
    "file_too_large",
    "total_too_large",
    "too_many_files",
    "language_mismatch",
]

#: I2 路径白名单上限：长度（字符）与段数。契约的 `path` 模式是**下限**，这里更严。
MAX_PATH_LENGTH = 200
MAX_PATH_SEGMENTS = 8
_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class EnvelopeRejected(Exception):
    """信封在进入任何业务处理前被拒。

    不变量：**不产生部分归一化的信封**、**不落库**、`detail` 已脱敏（不含文件内容）。
    """

    def __init__(self, reason: RejectionReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: RejectionReason = reason
        self.detail = detail


def redact_path_for_message(value: str, limit: int = 120) -> str:
    """把客户端提供的路径安全地放进错误明细：去控制字符并截断。"""
    cleaned = "".join(ch if ch.isprintable() else "\ufffd" for ch in value)
    return cleaned if len(cleaned) <= limit else f"{cleaned[:limit]}…"


def is_allowed_relative_path(value: object) -> bool:
    """I2 路径白名单判定（只回答"是否允许"，不做 IO、不解析绝对路径）。

    拒绝：非字符串/空串、超长、超段数、绝对路径、反斜杠、控制字符、空段、`.`/`..` 段、
    段字符越界。契约模式允许 `..` 段，因此**本函数是路径穿越的唯一有效防线**。
    """
    if not isinstance(value, str) or not value:
        return False
    if len(value) > MAX_PATH_LENGTH or value.startswith("/"):
        return False
    if "\\" in value or any(not ch.isprintable() for ch in value):
        return False
    segments = value.split("/")
    if len(segments) > MAX_PATH_SEGMENTS:
        return False
    for segment in segments:
        if not _SEGMENT_PATTERN.match(segment):
            return False
        if segment in {".", ".."}:
            return False
    return True


def assert_allowed_relative_path(value: object) -> None:
    """白名单不通过即抛 `EnvelopeRejected("path_not_allowed", ...)`。"""
    if not is_allowed_relative_path(value):
        shown = redact_path_for_message(value) if isinstance(value, str) else repr(type(value).__name__)
        raise EnvelopeRejected("path_not_allowed", f"path rejected: {shown}")


@dataclass(frozen=True, slots=True)
class EnvelopeFile:
    path: str              # 白名单校验后的工作区相对路径
    content_sha256: str    # 服务端按字节重算得到的小写 hex
    size_bytes: int
    storage_ref: str       # 服务端按摘要派生的内容寻址引用（非客户端路径）


@dataclass(frozen=True, slots=True)
class ApiProvenance:
    request_id: str


@dataclass(frozen=True, slots=True)
class EmailProvenance:
    mailbox: str           # 服务端配置的邮箱标识，非邮件头
    uidvalidity: int
    uid: int
    message_id: str


IntakeProvenance = ApiProvenance | EmailProvenance


@dataclass(frozen=True, slots=True)
class SubmissionEnvelope:
    submission_id: str
    student_id: str
    course_id: str
    section_id: str
    question_version_id: str
    idempotency_key: str
    intake: Literal["api", "email"]
    provenance: IntakeProvenance
    correlation_id: str
    files: tuple[EnvelopeFile, ...]
    submission_digest: str   # 五项绑定摘要之一：全部文件路径 + 各文件 SHA-256
    received_at: datetime    # UTC，毫秒精度
