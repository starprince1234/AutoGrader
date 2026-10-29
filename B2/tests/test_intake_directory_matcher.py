"""Directory matcher: sender address -> account, plus the configuration contract around it.

These tests need no database: the matcher's only dependency is the HTTP lookup, injected as a
callable. That is the point of the seam -- "does an unknown sender become a quarantine instead of
a submission" is decidable without PostgreSQL running.
"""

from __future__ import annotations

import json

import pytest

from autograder_b2.config import ConfigurationError, load_identity_settings
from autograder_b2.intake.matching import DirectoryEmailMatcher, MatchingUnavailable

TARGET = {
    "course_id": "11111111-1111-4111-8111-111111111111",
    "section_id": "22222222-2222-4222-8222-222222222222",
    "question_version_id": "33333333-3333-4333-8333-333333333333",
}


def _matcher(responses: dict[str, tuple[int, bytes]]) -> DirectoryEmailMatcher:
    def opener(url: str, headers, timeout: float) -> tuple[int, bytes]:
        assert headers["authorization"] == "Bearer svc"
        for marker, response in responses.items():
            if marker in url:
                return response
        raise AssertionError(f"unexpected lookup: {url}")

    return DirectoryEmailMatcher(
        base_url="http://127.0.0.1:18000",
        authorization="Bearer svc",
        target=TARGET,
        opener=opener,
    )


def test_a_registered_sender_resolves_to_the_configured_target() -> None:
    payload = json.dumps(
        {
            "user_id": "44444444-4444-4444-8444-444444444444",
            "username": "stu-qq",
            "display_name": "黄俊杰",
            "student_number": "2024141460192",
            "roles": ["student"],
            "is_active": True,
        }
    ).encode()
    result = _matcher({"2024141460192%40qq.com": (200, payload)}).match(
        mailbox="INBOX", sender=" 2024141460192@QQ.com ", attachment_names=("a.txt",)
    )
    assert result.outcome == "matched"
    assert result.target is not None
    assert result.target.student_id == "44444444-4444-4444-8444-444444444444"
    assert result.target.course_id == TARGET["course_id"]
    assert result.scope_context is not None
    assert result.scope_context.section_id == TARGET["section_id"]


def test_an_unregistered_sender_is_not_matched_so_it_can_be_quarantined() -> None:
    result = _matcher({"qq.com": (404, b"")}).match(
        mailbox="INBOX", sender="nobody@qq.com", attachment_names=()
    )
    assert result.outcome == "not_matched"
    assert result.target is None


def test_a_deactivated_account_is_not_matched() -> None:
    payload = json.dumps({"user_id": "x", "is_active": False}).encode()
    result = _matcher({"qq.com": (200, payload)}).match(
        mailbox="INBOX", sender="gone@qq.com", attachment_names=()
    )
    assert result.outcome == "not_matched"
    assert result.target is None


def test_a_rejected_token_is_an_outage_not_an_unknown_sender() -> None:
    """401 必须是例外：把它当成"没匹配到"会把配置故障伪装成一批无人认领的邮件。"""
    with pytest.raises(MatchingUnavailable):
        _matcher({"qq.com": (401, b"{}")}).match(
            mailbox="INBOX", sender="any@qq.com", attachment_names=()
        )


def test_a_sender_without_an_address_is_not_matched() -> None:
    result = _matcher({}).match(mailbox="INBOX", sender="   ", attachment_names=())
    assert result.outcome == "not_matched"


# ------------------------------------------------------------------ configuration


def test_identity_settings_are_read_from_three_variables() -> None:
    settings = load_identity_settings(
        {
            "B2_IDENTITY_URL": "http://127.0.0.1:18000/",
            "B2_IDENTITY_AUTHORIZATION": "Bearer svc",
            "B2_IMAP_TARGET_JSON": json.dumps(TARGET),
        }
    )
    assert settings is not None
    assert settings.base_url == "http://127.0.0.1:18000"
    assert settings.target["question_version_id"] == TARGET["question_version_id"]


def test_no_identity_variables_means_not_configured() -> None:
    assert load_identity_settings({}) is None


def test_a_half_configured_identity_lookup_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        load_identity_settings(
            {"B2_IDENTITY_URL": "http://127.0.0.1:18000", "B2_IDENTITY_AUTHORIZATION": ""}
        )


def test_a_target_without_a_version_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        load_identity_settings(
            {
                "B2_IDENTITY_URL": "http://127.0.0.1:18000",
                "B2_IDENTITY_AUTHORIZATION": "Bearer svc",
                "B2_IMAP_TARGET_JSON": json.dumps({"course_id": "c", "section_id": "s"}),
            }
        )
