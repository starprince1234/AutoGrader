"""发件地址→账号目录（`DirectoryEmailMatcher.lookup_account`）的单元测试（无需数据库）。

旧的"固定目标 / 匹配规则"路径已整体删除（设计 §1.5/§6.4 第 10 步）：本模块只保留
"这个发件地址属于哪个账号"这一件服务端事实。这里钉住：

- 身份配置只接受 `B2_IDENTITY_URL` + `B2_IDENTITY_AUTHORIZATION`（成对；没有 target）；
- 200 → 账号事实（含姓名/学号，供正文机器核对）；404/停用 → `None`（确定没有该账号）；
- 非 200 / 畸形 JSON / 缺 `user_id` → `MatchingUnavailable`（查不了 ≠ 没有）。
"""

from __future__ import annotations

import json

import pytest

from autograder_b2.config import ConfigurationError, load_identity_settings, load_imap_settings
from autograder_b2.intake.matching import DirectoryEmailMatcher, MatchingUnavailable

IDENTITY = {
    "user_id": "44444444-4444-4444-8444-444444444444",
    "student_number": "2024141460192",
    "display_name": "张三",
    "is_active": True,
}


def _matcher(document: dict[str, object] | None = None, *, status: int = 200):
    calls: list[tuple[str, dict[str, str], float]] = []

    def opener(url: str, headers: dict[str, str], timeout: float):
        calls.append((url, dict(headers), timeout))
        if document is None:
            return status, b""
        return status, json.dumps(document).encode("utf-8")

    matcher = DirectoryEmailMatcher(
        base_url="http://127.0.0.1:18000",
        authorization="Bearer svc-token",
        timeout_seconds=3.0,
        opener=opener,
    )
    return matcher, calls


def test_identity_settings_only_need_url_and_authorization() -> None:
    settings = load_identity_settings(
        {
            "B2_IDENTITY_URL": "http://127.0.0.1:18000/",
            "B2_IDENTITY_AUTHORIZATION": "Bearer svc",
        }
    )
    assert settings is not None
    assert settings.base_url == "http://127.0.0.1:18000"
    assert settings.authorization == "Bearer svc"
    # 旧路由字段彻底移除：既不存在 target，也不接受 B2_IMAP_TARGET_JSON。
    assert not hasattr(settings, "target")


def test_no_identity_variables_means_not_configured() -> None:
    assert load_identity_settings({}) is None


def test_a_half_configured_identity_lookup_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        load_identity_settings({"B2_IDENTITY_URL": "http://127.0.0.1:18000"})
    with pytest.raises(ConfigurationError):
        load_identity_settings({"B2_IDENTITY_AUTHORIZATION": "Bearer svc"})


def test_lookup_resolves_the_account_with_name_and_student_number() -> None:
    matcher, calls = _matcher(IDENTITY)
    account = matcher.lookup_account(" 2024141460192@QQ.com ")
    assert account is not None
    assert account.user_id == IDENTITY["user_id"]
    assert account.student_number == "2024141460192"
    assert account.display_name == "张三"
    # 地址已规范化小写并编码进查询串；请求只带服务令牌头。
    url, headers, timeout = calls[0]
    assert "email=2024141460192%40qq.com" in url
    assert headers["authorization"] == "Bearer svc-token"
    assert timeout == 3.0
    assert "svc-token" not in repr(matcher)


def test_blank_identity_fields_become_none() -> None:
    matcher, _calls = _matcher(
        {"user_id": IDENTITY["user_id"], "student_number": "  ", "display_name": None}
    )
    account = matcher.lookup_account("student@example.edu")
    assert account is not None
    assert account.student_number is None
    assert account.display_name is None


def test_unknown_sender_is_none_not_an_error() -> None:
    matcher, _calls = _matcher(status=404)
    assert matcher.lookup_account("nobody@example.edu") is None


def test_deactivated_account_is_treated_as_missing() -> None:
    matcher, _calls = _matcher({**IDENTITY, "is_active": False})
    assert matcher.lookup_account("gone@example.edu") is None


def test_empty_sender_does_not_call_the_directory() -> None:
    matcher, calls = _matcher(IDENTITY)
    assert matcher.lookup_account("   ") is None
    assert calls == []


@pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
def test_lookup_failures_are_unavailable_not_missing(status: int) -> None:
    """查不了 ≠ 没有这个账号：调用方必须让本轮拉取失败重试，而不是隔离整批邮件。"""
    matcher, _calls = _matcher(status=status)
    with pytest.raises(MatchingUnavailable):
        matcher.lookup_account("student@example.edu")


def test_malformed_responses_are_unavailable() -> None:
    def opener(_url: str, _headers: dict[str, str], _timeout: float):
        return 200, b"not-json"

    matcher = DirectoryEmailMatcher(
        base_url="http://127.0.0.1:18000",
        authorization="Bearer svc-token",
        opener=opener,
    )
    with pytest.raises(MatchingUnavailable):
        matcher.lookup_account("student@example.edu")

    def opener_missing_user(_url: str, _headers: dict[str, str], _timeout: float):
        return 200, b'{"student_number": "1"}'

    matcher = DirectoryEmailMatcher(
        base_url="http://127.0.0.1:18000",
        authorization="Bearer svc-token",
        opener=opener_missing_user,
    )
    with pytest.raises(MatchingUnavailable):
        matcher.lookup_account("student@example.edu")


def test_imap_settings_expose_identity_and_no_matching_rules() -> None:
    imap = load_imap_settings(
        {
            "B2_IMAP_HOST": "imap.example.edu",
            "B2_IMAP_USERNAME": "inbox@example.edu",
            "B2_IMAP_PASSWORD": "test",
            "B2_IMAP_MAILBOX": "INBOX",
            "B2_IDENTITY_URL": "http://127.0.0.1:18000",
            "B2_IDENTITY_AUTHORIZATION": "Bearer svc",
        }
    )
    assert imap is not None
    assert imap.identity is not None
    assert imap.identity.base_url == "http://127.0.0.1:18000"
    # 旧字段完全消失：没有匹配规则表，也没有固定目标。
    assert not hasattr(imap, "matching")
    assert not hasattr(imap.identity, "target")
