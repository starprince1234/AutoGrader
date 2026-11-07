"""`email_delivery` 共享规则引擎的纯函数测试（无需数据库）。

覆盖：主题解析/匹配、四类 check 的通过与失败类别、缺邮件时"四条全 missing_output"、
脱敏（消息/明细不含主题、地址、附件名）、模板缺陷的拒绝。这些断言同时钉住
"学生评测与参考答案验证调用同一个入口对象"这一契约的实现侧（调用点见
`tests/test_email_verification_flow.py::test_receipt_loading_feeds_the_shared_rule_engine`）。
"""

from __future__ import annotations

import dataclasses

import pytest

from autograder_b2.intake.email_rules import (
    EMAIL_CHECKS,
    REQUIRED_ATTACHMENT_TEMPLATE,
    SUBJECT_TEMPLATE,
    EmailReceipt,
    EmailRequirements,
    attachment_matches,
    evaluate_email_receipt,
    parse_subject,
    subject_matches,
)

STUDENT_NUMBER = "2024141460192"
SENDER = "alice@example.edu"
SUBJECT = f"{STUDENT_NUMBER}_1"
ATTACHMENT = f"{STUDENT_NUMBER}.txt"


def _receipt(**overrides: object) -> EmailReceipt:
    """一份"全部事实都正确"的回执；用例只改动它关心的字段。"""
    values: dict[str, object] = {
        "student_number": STUDENT_NUMBER,
        "subject": SUBJECT,
        "sender": SENDER,
        "account_email": SENDER,
        "attachment_names": (ATTACHMENT,),
        "received": True,
        "account_email_verified": True,
        "sender_binding_matches": True,
    }
    values.update(overrides)
    return EmailReceipt(**values)  # type: ignore[arg-type]


def _outcome(outcomes: tuple, check: str):
    matches = [outcome for outcome in outcomes if outcome.check == check]
    assert len(matches) == 1, f"{check} 必须恰好出现一次"
    return matches[0]


# --- 主题 -----------------------------------------------------------------------


def test_parse_subject_parses_the_default_template() -> None:
    assert parse_subject(SUBJECT) == (STUDENT_NUMBER, 1)
    assert parse_subject(f"  {STUDENT_NUMBER}_99 ") == (STUDENT_NUMBER, 99)


@pytest.mark.parametrize(
    "subject",
    [
        None,
        "",
        STUDENT_NUMBER,
        f"{STUDENT_NUMBER}_",
        f"{STUDENT_NUMBER}_1_extra",
        f"prefix{STUDENT_NUMBER}_1",
        f"{STUDENT_NUMBER}_abc",
        f"{STUDENT_NUMBER}_-1",
        f"{STUDENT_NUMBER}_1000",
    ],
)
def test_parse_subject_rejects_anything_outside_the_template(subject: object) -> None:
    assert parse_subject(subject) is None  # type: ignore[arg-type]


def test_parse_subject_accepts_a_custom_template() -> None:
    assert parse_subject("hw-2024-42", template="hw-{student_number}-{attempt}") == ("2024", 42)


def test_subject_matches_requires_both_facts() -> None:
    assert subject_matches(SUBJECT, STUDENT_NUMBER, 1) is True
    assert subject_matches(f"{STUDENT_NUMBER}_2", STUDENT_NUMBER, 1) is False
    assert subject_matches(None, STUDENT_NUMBER, 1) is False
    assert subject_matches(SUBJECT, None, 1) is False
    # 模板渲染是精确字符串相等：多一个空格就不同（不猜测"看起来像"）。
    assert subject_matches(f" {SUBJECT} ", STUDENT_NUMBER, 1) is True
    assert subject_matches(f"{SUBJECT} ", STUDENT_NUMBER, 1) is True
    assert subject_matches(f"{SUBJECT}_", STUDENT_NUMBER, 1) is False


# --- 附件名 ---------------------------------------------------------------------


def test_attachment_matches_is_membership_not_substring() -> None:
    assert attachment_matches((ATTACHMENT,), STUDENT_NUMBER) is True
    assert attachment_matches(("notes.txt", ATTACHMENT), STUDENT_NUMBER) is True
    assert attachment_matches((f"x{ATTACHMENT}",), STUDENT_NUMBER) is False
    assert attachment_matches((ATTACHMENT,), None) is False
    # 大小写不归一：文件名是事实，不能"看起来像"就放过。
    assert attachment_matches((ATTACHMENT.upper(),), STUDENT_NUMBER) is False


# --- 要求对象 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempt": 0},
        {"attempt": 100},
        {"attempt": True},
        {"attempt": "1"},
        {"subject_template": ""},
        {"subject_template": "{unknown}_{attempt}"},
        {"subject_template": "{student_number}_{attempt!r}"},
        {"subject_template": "{student_number}_{attempt:03d}"},
        {"subject_template": "{student_number_"},
        {"required_attachment_template": "{student_number"},
    ],
)
def test_requirements_reject_invalid_package_declarations(kwargs: dict[str, object]) -> None:
    """模板/attempt 非法是 package 缺陷，必须在构造期拒绝，而不是判成学生错。"""
    with pytest.raises(ValueError):
        EmailRequirements(**kwargs)  # type: ignore[arg-type]


def test_requirements_defaults_match_the_design_document() -> None:
    requirements = EmailRequirements(attempt=2)
    assert requirements.subject_template == SUBJECT_TEMPLATE
    assert requirements.required_attachment_template == REQUIRED_ATTACHMENT_TEMPLATE


# --- 四类 check -----------------------------------------------------------------


def test_a_correct_receipt_passes_all_four_checks() -> None:
    outcomes = evaluate_email_receipt(_receipt(), EmailRequirements(attempt=1))
    assert [outcome.check for outcome in outcomes] == list(EMAIL_CHECKS)
    assert all(outcome.passed for outcome in outcomes), outcomes
    assert all(outcome.category is None for outcome in outcomes)
    assert all(outcome.message for outcome in outcomes)


def test_no_received_mail_fails_every_check_as_missing_output() -> None:
    """缺邮件绝不折算成"字段不符"：四条结论都是 missing_output（评测侧据此给每个用例证据）。"""
    outcomes = evaluate_email_receipt(EmailReceipt.not_received(), EmailRequirements(attempt=1))
    assert len(outcomes) == 4
    assert [outcome.check for outcome in outcomes] == list(EMAIL_CHECKS)
    assert all(not outcome.passed for outcome in outcomes)
    assert {outcome.category for outcome in outcomes} == {"missing_output"}


def test_not_received_builder_is_empty_and_frozen() -> None:
    receipt = EmailReceipt.not_received()
    assert receipt == EmailReceipt(
        student_number=None,
        subject=None,
        sender=None,
        account_email=None,
        attachment_names=(),
        received=False,
        account_email_verified=False,
        sender_binding_matches=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        receipt.received = True  # type: ignore[misc]


def test_subject_mismatch_is_output_mismatch() -> None:
    outcomes = evaluate_email_receipt(
        _receipt(subject=f"{STUDENT_NUMBER}_2"), EmailRequirements(attempt=1)
    )
    subject = _outcome(outcomes, "subject")
    assert subject.passed is False
    assert subject.category == "output_mismatch"
    # 另外三条仍然按事实判定（只有 subject 一条失败，不是"一错全错"）。
    assert [outcome.check for outcome in outcomes if not outcome.passed] == ["subject"]


def test_missing_student_number_fails_subject_and_attachment_deterministically() -> None:
    outcomes = evaluate_email_receipt(
        _receipt(student_number=None), EmailRequirements(attempt=1)
    )
    assert _outcome(outcomes, "subject").category == "output_mismatch"
    assert _outcome(outcomes, "attachment").category == "output_mismatch"
    assert _outcome(outcomes, "sender").passed is True


def test_unverified_account_email_fails_sender() -> None:
    outcomes = evaluate_email_receipt(
        _receipt(account_email_verified=False), EmailRequirements(attempt=1)
    )
    sender = _outcome(outcomes, "sender")
    assert sender.passed is False
    assert sender.category == "output_mismatch"


def test_sender_must_equal_the_account_email_when_case_differs_ok_but_value_must_match() -> None:
    outcomes = evaluate_email_receipt(
        _receipt(sender=" ALICE@Example.EDU "), EmailRequirements(attempt=1)
    )
    assert _outcome(outcomes, "sender").passed is True, "地址比较应规范化大小写与首尾空白"

    outcomes = evaluate_email_receipt(
        _receipt(sender="mallory@example.edu"), EmailRequirements(attempt=1)
    )
    assert _outcome(outcomes, "sender").category == "output_mismatch"


def test_sender_binding_mismatch_fails_sender() -> None:
    outcomes = evaluate_email_receipt(
        _receipt(sender_binding_matches=False), EmailRequirements(attempt=1)
    )
    sender = _outcome(outcomes, "sender")
    assert sender.passed is False
    assert sender.category == "output_mismatch"


def test_missing_attachment_is_output_mismatch() -> None:
    outcomes = evaluate_email_receipt(
        _receipt(attachment_names=("notes.txt",)), EmailRequirements(attempt=1)
    )
    attachment = _outcome(outcomes, "attachment")
    assert attachment.passed is False
    assert attachment.category == "output_mismatch"
    assert attachment.detail["attachment_count"] == 1


def test_attempt_comes_from_the_requirements_not_the_receipt() -> None:
    """主题里的 attempt 由 package 决定；同一封回执在 attempt=2 的题目下必须判错。"""
    outcomes = evaluate_email_receipt(_receipt(), EmailRequirements(attempt=2))
    assert _outcome(outcomes, "subject").category == "output_mismatch"


# --- 脱敏 -----------------------------------------------------------------------


def test_outcomes_never_echo_the_subject_sender_or_attachment_names() -> None:
    """证据会进报告/数据库：消息与明细不得回显被判定值（那可能是 PII 或内容）。"""
    marker_sender = "leaky-sender-marker@example.edu"
    marker_subject = "LEAKYSUBJECTMARKER_1"
    marker_attachment = "LEAKYATTACHMENTMARKER.txt"
    receipt = _receipt(
        subject=marker_subject,
        sender=marker_sender,
        account_email=SENDER,
        attachment_names=(marker_attachment,),
        sender_binding_matches=False,
    )
    outcomes = evaluate_email_receipt(receipt, EmailRequirements(attempt=7))
    rendered = "\n".join(
        f"{outcome.message}\n{outcome.detail!r}" for outcome in outcomes
    )
    for marker in (marker_sender, marker_subject, marker_attachment, "LEAKY"):
        assert marker not in rendered
    # detail 里的键值也都是有界事实（布尔/计数），不是被判定值。
    for outcome in outcomes:
        for value in outcome.detail.values():
            assert isinstance(value, (bool, int, str))
            if isinstance(value, str):
                assert "@" not in value


def test_detail_only_carries_bounded_facts() -> None:
    outcomes = evaluate_email_receipt(_receipt(), EmailRequirements(attempt=1))
    assert _outcome(outcomes, "subject").detail == {"attempt": 1}
    assert _outcome(outcomes, "email_received").detail == {"received": True}
    assert _outcome(outcomes, "sender").detail == {
        "sender_matches_account_email": True,
        "sender_binding_matches": True,
    }
    assert _outcome(outcomes, "attachment").detail == {"attachment_count": 1}
