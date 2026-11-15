"""Unit tests for B2 SMTP receipt mailer on processed email submissions."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import pytest

from autograder_b2.intake import receipt_mailer


def test_load_receipt_smtp_settings_modes() -> None:
    assert receipt_mailer.load_receipt_smtp_settings({}) is None

    ssl_465 = receipt_mailer.load_receipt_smtp_settings(
        {
            "SMTP_HOST": "smtp.qq.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "bot@qq.com",
            "SMTP_PASS": "secret",
            "SMTP_TLS_MODE": "tls",
        }
    )
    assert ssl_465 is not None
    assert ssl_465.sender_address == "bot@qq.com"
    assert ssl_465.uses_implicit_tls is True
    assert ssl_465.uses_starttls is False
    assert "secret" not in repr(ssl_465)

    starttls_587 = receipt_mailer.load_receipt_smtp_settings(
        {
            "SMTP_HOST": "smtp.example.edu",
            "SMTP_PORT": "587",
            "SMTP_FROM": "noreply@example.edu",
            "SMTP_TLS_MODE": "tls",
        }
    )
    assert starttls_587 is not None
    assert starttls_587.uses_implicit_tls is False
    assert starttls_587.uses_starttls is True


def test_send_submission_receipt_email_constructs_and_delivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[EmailMessage] = []

    def fake_deliver(settings: receipt_mailer.ReceiptSmtpSettings, message: EmailMessage) -> None:
        sent.append(message)

    monkeypatch.setattr(receipt_mailer, "deliver_smtp_message", fake_deliver)

    ok = receipt_mailer.send_submission_receipt_email(
        recipient_email="2242698100@qq.com",
        student_name="金智超",
        student_number="2024141460017",
        assignment_id="11111111-1111-1111-1111-111111111111",
        assignment_title="第一次上机作业",
        attachment_names=["1.txt", "2.txt"],
        submission_ids=["sub-1", "sub-2"],
        environ={
            "SMTP_HOST": "smtp.qq.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "bot@qq.com",
            "SMTP_PASS": "secret",
            "SMTP_TLS_MODE": "tls",
        },
    )
    assert ok is True
    assert len(sent) == 1
    msg = sent[0]
    assert "第一次上机作业" in msg["Subject"]
    assert msg["To"] == "2242698100@qq.com"
    body = msg.get_content()
    assert "金智超" in body
    assert "2024141460017" in body
    assert "1.txt" in body
    assert "2.txt" in body
    assert "sub-1" in body


def test_send_submission_receipt_email_is_best_effort_on_smtp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_deliver(settings: receipt_mailer.ReceiptSmtpSettings, message: EmailMessage) -> None:
        raise smtplib.SMTPException("connection refused")

    monkeypatch.setattr(receipt_mailer, "deliver_smtp_message", fail_deliver)

    ok = receipt_mailer.send_submission_receipt_email(
        recipient_email="student@qq.com",
        student_name="测试学生",
        student_number="20240001",
        assignment_id="11111111-1111-1111-1111-111111111111",
        assignment_title=None,
        attachment_names=["1.txt"],
        submission_ids=["sub-1"],
        environ={
            "SMTP_HOST": "smtp.qq.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "bot@qq.com",
            "SMTP_PASS": "secret",
        },
    )
    assert ok is False
