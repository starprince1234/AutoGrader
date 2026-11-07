"""邮件接收的**单元/配置**测试（无需数据库）。

数据库与真实 TCP IMAP 的端到端矩阵在 `tests/test_email_verification_flow.py`（真实 PostgreSQL + 真实
TCP 上的进程内 IMAP 夹具）与 `tests/test_email_intake_acceptance.py`。本文件只测**纯判定**与
**配置装配**：

- 附件安全闸门：只接受 `.txt`；可执行内容/归档容器/非纯文本/名称越界/大小写重名一律拒绝；
- MIME 解析：正文摘录有界、折叠成单行、不含附件原文；`Message-ID` 落库形态单射；
- 配置：未配置 vs 半配置；旧 `B2_IMAP_MATCHING_JSON`/`B2_IMAP_TARGET_JSON` 已彻底移除；
  邮件入口需要身份目录；`pull` CLI 读 B2_ 前缀的数据库配置。

真实外部邮箱与 TLS **未**验证（本环境没有可用的外部 IMAP 服务）。
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence

import pytest

from autograder_b2.config import (
    ConfigurationError,
    ImapSettings,
    load_imap_settings,
)
from autograder_b2.intake.attachments import (
    _BIDI_CONTROL_CHARACTERS,
    MAX_MESSAGE_ID_LENGTH,
    AttachmentRejected,
    EmailMessage,
    RawAttachment,
    message_id_for_storage,
    parse_message,
    plain_text_problem,
    screen_attachments,
    sniff_content,
)
from autograder_b2.intake.service import IntakeLimits

LIMITS = IntakeLimits(max_files=4, max_file_bytes=64 * 1024, max_total_bytes=128 * 1024)


# --- 夹具 ---------------------------------------------------------------------


def raw_attachment(
    filename: str = "1.txt",
    content: bytes = b"echo hello\n",
    *,
    content_type: str = "application/octet-stream",
    transfer_encoding: str = "",
) -> RawAttachment:
    return RawAttachment(
        filename=filename,
        content_type=content_type,
        transfer_encoding=transfer_encoding,
        encoded=content,
        estimated_size=len(content),
    )


def email_message(
    *attachments: RawAttachment,
    body_excerpt: str | None = None,
    subject: str | None = None,
) -> EmailMessage:
    return EmailMessage(
        message_id="<message-1@example.edu>",
        sender="student@example.edu",
        attachments=tuple(attachments),
        body_excerpt=body_excerpt,
        subject=subject,
    )


def screened(*attachments: RawAttachment) -> tuple:
    return screen_attachments(email_message(*attachments), limits=LIMITS)


def rejection(*attachments: RawAttachment) -> AttachmentRejected:
    with pytest.raises(AttachmentRejected) as caught:
        screened(*attachments)
    return caught.value


def _zip_bomb(*, declared_bytes: int) -> bytes:
    """手写一个 ZIP：声明未压缩大小远大于预算（判定读归档自报长度，不解压）。"""
    name = b"payload.bin"
    local = (
        b"PK\x03\x04"
        + (20).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + declared_bytes.to_bytes(4, "little")
        + declared_bytes.to_bytes(4, "little")
        + len(name).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + name
    )
    central = (
        b"PK\x01\x02"
        + (20).to_bytes(2, "little")
        + (20).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + declared_bytes.to_bytes(4, "little")
        + declared_bytes.to_bytes(4, "little")
        + len(name).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + name
    )
    eocd = (
        b"PK\x05\x06"
        + (0).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + len(central).to_bytes(4, "little")
        + len(local).to_bytes(4, "little")
        + (0).to_bytes(2, "little")
    )
    return local + central + eocd


#: 真实 ELF 头（内容是可执行体；文件名与内容不一致也必须按内容拒绝）。
ELF_PAYLOAD = b"\x7fELF" + b"\x02\x01\x01\x00" + b"\x00" * 32


# --- 附件闸门：只接受 .txt -------------------------------------------------------


def test_only_txt_attachments_are_accepted() -> None:
    accepted = screened(raw_attachment("1.txt", b"echo hello\n"))
    assert len(accepted) == 1
    assert accepted[0].filename == "1.txt"
    assert accepted[0].content == b"echo hello\n"


@pytest.mark.parametrize("filename", ["1.py", "1.sh", "1.md", "answer"])
def test_other_extension_is_rejected_with_an_explicit_message(filename: str) -> None:
    rejected = rejection(raw_attachment(filename))
    assert rejected.reason == "unsupported_attachment_type"
    assert "only .txt answer files are accepted" in rejected.detail


def test_executable_content_is_rejected_even_behind_a_txt_name() -> None:
    rejected = rejection(raw_attachment("1.txt", ELF_PAYLOAD))
    assert rejected.reason == "executable_attachment"


def test_archive_container_is_rejected_as_a_bomb() -> None:
    rejected = rejection(raw_attachment("1.txt", _zip_bomb(declared_bytes=64 * 1024 * 1024)))
    assert rejected.reason in {"archive_bomb", "archive_attachment"}


def test_plain_text_with_container_literals_is_still_accepted() -> None:
    source = b"MAGIC = b'PK\\x03\\x04'\nnot a real archive\n"
    accepted = screened(raw_attachment("1.txt", source))
    assert accepted[0].content == source


@pytest.mark.parametrize(
    "filename",
    ["../1.txt", "/1.txt", "a/../1.txt", "1.txt\\", " 1.txt", "1.txt "],
)
def test_attachment_name_path_rules_are_enforced(filename: str) -> None:
    with pytest.raises(AttachmentRejected) as caught:
        screened(raw_attachment(filename))
    assert caught.value.reason in {
        "attachment_name_not_allowed",
        "unsupported_attachment_type",
    }


def test_trailing_dot_filename_is_rejected_by_design() -> None:
    # Windows 会剥掉路径末尾的点：声明名与实际落盘名会不一致。
    rejected = rejection(raw_attachment("1.txt."))
    assert rejected.reason == "unsupported_attachment_type"


def test_duplicate_attachment_names_are_case_insensitive() -> None:
    rejected = rejection(
        raw_attachment("1.txt", b"echo one\n"),
        raw_attachment("1.TXT", b"echo two\n"),
    )
    assert rejected.reason == "duplicate_attachment_name"


def test_no_attachments_is_rejected() -> None:
    rejected = rejection()
    assert rejected.reason == "no_attachments"


def test_attachment_over_the_per_file_limit_is_rejected_before_decoding() -> None:
    rejected = rejection(raw_attachment("1.txt", b"x" * (LIMITS.max_file_bytes + 1)))
    assert rejected.reason == "attachment_too_large"


def test_empty_txt_attachment_is_accepted() -> None:
    """空文件是合法的学生输入（由评测判对错），接收层不因"空"拒绝。"""
    accepted = screened(raw_attachment("1.txt", b""))
    assert accepted[0].content == b""


# --- 纯文本判定 -----------------------------------------------------------------


def test_zero_width_typographic_characters_are_accepted() -> None:
    source = "zero\u200bwidth\u200dtext\ufeffsoft\u00adhyphen\n".encode("utf-8")
    assert plain_text_problem(source) is None
    assert screened(raw_attachment("1.txt", source))


@pytest.mark.parametrize("control", sorted(_BIDI_CONTROL_CHARACTERS))
def test_bidirectional_control_characters_are_rejected(control: str) -> None:
    rejected = rejection(raw_attachment("1.txt", f"a{control}b\n".encode()))
    assert rejected.reason == "not_plain_text"


def test_binary_content_is_not_plain_text() -> None:
    rejected = rejection(raw_attachment("1.txt", b"\x00\x01\x02binary"))
    assert rejected.reason in {"not_plain_text", "executable_attachment"}


def test_sniff_content_distinguishes_text_and_containers() -> None:
    assert sniff_content(b"echo hello\n", declared_size_limit=LIMITS.max_total_bytes).kind == "text"
    assert (
        sniff_content(_zip_bomb(declared_bytes=64 * 1024 * 1024), declared_size_limit=LIMITS.max_total_bytes).kind
        == "archive"
    )
    assert (
        sniff_content(ELF_PAYLOAD, declared_size_limit=LIMITS.max_total_bytes).kind
        == "executable"
    )


# --- MIME 解析 -----------------------------------------------------------------


def _mime_message(
    *,
    body: str = "line one\nline two\n",
    attachments: Sequence[tuple[str, bytes]] = (("1.txt", b"echo hello\n"),),
    message_id: str | None = "<m1@example.edu>",
) -> bytes:
    from email.message import EmailMessage as MIMEMessage

    message = MIMEMessage()
    message["From"] = "student@example.edu"
    message["To"] = "inbox@example.edu"
    if message_id is not None:
        message["Message-ID"] = message_id
    message["Subject"] = "submission"
    message.set_content(body)
    for name, payload in attachments:
        message.add_attachment(
            payload, maintype="application", subtype="octet-stream", filename=name
        )
    return message.as_bytes()


def test_parse_message_bounds_and_collapses_the_body_excerpt() -> None:
    raw = _mime_message(body="核验码：AG-ABCDEFGHJKLM\n作业编号：x\n课序：第1课序\n")
    message = parse_message(raw, max_attachments=LIMITS.max_files)
    assert message.body_excerpt is not None
    # 正文摘录被折叠成**单行**（`_body_excerpt` 的既有行为）：正文解析必须能处理这一点。
    assert "\n" not in message.body_excerpt
    assert "核验码：AG-ABCDEFGHJKLM" in message.body_excerpt
    assert len(message.body_excerpt) <= 4000
    assert [item.filename for item in message.attachments] == ["1.txt"]


def test_parse_message_never_carries_the_body() -> None:
    """解析结果的字段集固定：标识 + 具名部件 + 有界主题/正文摘录。

    正文只以 `body_excerpt`（≤4000 字、脱敏）出现，附件仍只带文件名与编码字节，不物化整封报文。
    """
    from dataclasses import fields

    raw = _mime_message(body="BODYCONTENTMARKER\n")
    message = parse_message(raw, max_attachments=LIMITS.max_files)
    assert [field.name for field in fields(message)] == [
        "message_id",
        "sender",
        "attachments",
        "nested_attachments",
        "attachments_overflow",
        "subject",
        "body_excerpt",
        "body_truncated",
    ]
    assert message.nested_attachments == () and message.attachments_overflow is False
    assert message.body_excerpt is not None and "BODYCONTENTMARKER" in message.body_excerpt
    # 附件保持"未解码 + 有界大小"的形态，不是整封报文的副本。
    assert message.attachments[0].encoded == b"echo hello\n"


def test_parse_message_rejects_an_empty_message() -> None:
    with pytest.raises(AttachmentRejected) as caught:
        parse_message(b"   \n")
    assert caught.value.reason == "malformed_message"


def test_missing_message_id_is_preserved_as_none() -> None:
    message = parse_message(_mime_message(message_id=None), max_attachments=LIMITS.max_files)
    assert message.message_id is None


def test_message_id_storage_form_is_injective_and_bounded() -> None:
    first = message_id_for_storage("<a b@example.edu>")
    second = message_id_for_storage("<a\tb@example.edu>")
    assert first != second, "控制字符必须转义而不是删除：不同 Message-ID 不能撞成同一把幂等键"
    long_value = "<" + "x" * (MAX_MESSAGE_ID_LENGTH + 50) + "@example.edu>"
    stored = message_id_for_storage(long_value)
    assert stored is not None and len(stored) > MAX_MESSAGE_ID_LENGTH
    assert stored != message_id_for_storage("<" + "x" * (MAX_MESSAGE_ID_LENGTH + 49) + "@e>")


def test_subject_is_a_bounded_display_label() -> None:
    message = parse_message(
        _mime_message(body="x", message_id="<m2@example.edu>"),
        max_attachments=LIMITS.max_files,
    )
    assert message.subject == "submission"


# --- 配置：未配置 vs 半配置，且旧路由字段彻底移除 --------------------------------


def test_imap_settings_are_explicit_or_absent() -> None:
    assert load_imap_settings({}) is None
    with pytest.raises(ConfigurationError):
        load_imap_settings({"B2_IMAP_HOST": "imap.example.edu"})  # 半配置
    with pytest.raises(ConfigurationError):
        load_imap_settings(
            {
                "B2_IMAP_HOST": "imap.example.edu",
                "B2_IMAP_USERNAME": "inbox@example.edu",
                "B2_IMAP_PASSWORD": "secret",
                # 缺 MAILBOX：它是 checkpoint 主键的一部分，不给隐式默认
            }
        )
    settings = load_imap_settings(
        {
            "B2_IMAP_HOST": "imap.example.edu",
            "B2_IMAP_USERNAME": "inbox@example.edu",
            "B2_IMAP_PASSWORD": "secret",
            "B2_IMAP_MAILBOX": "INBOX",
        }
    )
    assert settings is not None
    assert settings.identity is None
    # 旧路由字段（匹配规则表/固定目标）已经不存在，且不再被读取。
    assert not hasattr(settings, "matching")
    assert load_imap_settings({"B2_IMAP_MATCHING_JSON": "{}"}) is None
    assert load_imap_settings({"B2_IMAP_TARGET_JSON": "{}"}) is None


def test_identity_config_is_optional_but_must_be_complete() -> None:
    with pytest.raises(ConfigurationError):
        load_imap_settings(
            {
                "B2_IMAP_HOST": "imap.example.edu",
                "B2_IMAP_USERNAME": "inbox@example.edu",
                "B2_IMAP_PASSWORD": "secret",
                "B2_IMAP_MAILBOX": "INBOX",
                "B2_IDENTITY_URL": "http://127.0.0.1:18000",
            }
        )
    settings = load_imap_settings(
        {
            "B2_IMAP_HOST": "imap.example.edu",
            "B2_IMAP_USERNAME": "inbox@example.edu",
            "B2_IMAP_PASSWORD": "secret",
            "B2_IMAP_MAILBOX": "INBOX",
            "B2_IDENTITY_URL": "http://127.0.0.1:18000",
            "B2_IDENTITY_AUTHORIZATION": "Bearer svc",
        }
    )
    assert settings is not None and settings.identity is not None


def test_password_never_appears_in_the_settings_repr() -> None:
    settings = imap_settings(port=143)
    assert "fixture-secret" not in repr(settings)


def test_password_may_come_from_a_file(tmp_path: pathlib.Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("file-secret\r\n", encoding="utf-8")
    settings = load_imap_settings(
        {
            "B2_IMAP_HOST": "imap.example.edu",
            "B2_IMAP_USERNAME": "inbox@example.edu",
            "B2_IMAP_PASSWORD_FILE": str(secret),
            "B2_IMAP_MAILBOX": "INBOX",
        }
    )
    assert settings is not None and settings.password == "file-secret"
    assert "file-secret" not in repr(settings)


def imap_settings(*, port: int) -> ImapSettings:
    return ImapSettings(
        host="imap.example.edu",
        port=port,
        username="inbox@example.edu",
        password="fixture-secret",
        mailbox="INBOX",
        use_ssl=False,
        max_messages_per_pull=20,
        max_message_bytes=8 * 1024 * 1024,
        timeout_seconds=10.0,
    )


# --- 运行时装配（不需要数据库连接） ----------------------------------------------


def _base_environment(tmp_path: pathlib.Path) -> dict[str, str]:
    import base64
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
                .rstrip(b"=")
                .decode("ascii"),
                "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
                "use": "sig",
                "alg": "EdDSA",
            }
        ]
    }
    return {
        "DATABASE_URL": "postgresql+psycopg://example:example@127.0.0.1:5432/example",
        "B2_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "B2_JWKS_JSON": json.dumps(jwks),
    }


def base64_url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_runtime_keeps_the_http_entry_available_without_imap(tmp_path: pathlib.Path) -> None:
    from autograder_b2.intake.runtime import build_runtime

    runtime = build_runtime(_base_environment(tmp_path))
    assert runtime.ready is True
    assert runtime.email is None
    assert runtime.email_unavailable_reason is None  # 未配置，不是配错
    available, detail = runtime.email_health()
    assert available is False and "not configured" in detail


def test_runtime_isolates_a_broken_imap_configuration(tmp_path: pathlib.Path) -> None:
    from autograder_b2.intake.runtime import build_runtime

    environment = {**_base_environment(tmp_path), "B2_IMAP_HOST": "imap.example.edu"}
    runtime = build_runtime(environment)
    # 邮件入口不可用，但 HTTP 入口不受连累。
    assert runtime.ready is True
    assert runtime.email is None
    assert runtime.email_unavailable_reason
    available, detail = runtime.email_health()
    assert available is False and "not available" in detail


def test_runtime_wires_the_email_entry_when_configured(tmp_path: pathlib.Path) -> None:
    from autograder_b2.intake.runtime import build_runtime

    environment = {
        **_base_environment(tmp_path),
        "B2_IMAP_HOST": "imap.example.edu",
        "B2_IMAP_USERNAME": "inbox@example.edu",
        "B2_IMAP_PASSWORD": "secret",
        "B2_IMAP_MAILBOX": "INBOX",
        "B2_IDENTITY_URL": "http://127.0.0.1:18000",
        "B2_IDENTITY_AUTHORIZATION": "Bearer svc",
    }
    runtime = build_runtime(environment)
    assert runtime.email is not None
    assert runtime.email_verification is not None
    available, detail = runtime.email_health()
    assert available is True and "INBOX" in detail
    # 没配 B2_B3_*：作业目录为 None（带作业的提交会以 503 拒绝，而不是跳过校验）。
    assert runtime.assignment_directory is None


def test_runtime_refuses_the_mail_entry_without_an_identity_directory(
    tmp_path: pathlib.Path,
) -> None:
    """没有身份目录就无法证明"这封邮件属于谁"：邮件入口明确不可用，而不是隔离全部邮件。"""
    from autograder_b2.intake.runtime import build_runtime

    environment = {
        **_base_environment(tmp_path),
        "B2_IMAP_HOST": "imap.example.edu",
        "B2_IMAP_USERNAME": "inbox@example.edu",
        "B2_IMAP_PASSWORD": "secret",
        "B2_IMAP_MAILBOX": "INBOX",
    }
    runtime = build_runtime(environment)
    assert runtime.email is None
    assert runtime.email_unavailable_reason is not None
    assert "identity directory" in runtime.email_unavailable_reason


def test_mail_puller_uses_b2_scoped_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from autograder_b2.intake import pull

    observed: dict[str, str] = {}
    monkeypatch.setenv("B2_DATABASE_URL", "postgresql://b2.example/test")
    monkeypatch.setenv("B2_NATS_URL", "nats://b2.example:4222")
    monkeypatch.setattr(
        pull,
        "build_b2_runtime",
        lambda settings: (
            observed.update(settings)
            or SimpleNamespace(email=None, email_unavailable_reason="not configured")
        ),
    )
    assert pull.main(["--once", "--allow-unconfigured"]) == 0
    assert observed["DATABASE_URL"] == "postgresql://b2.example/test"
    assert observed["NATS_URL"] == "nats://b2.example:4222"
