"""Outbound e-mail for the self-service registration verification code.

The registration flow proves that the claimant owns the mailbox, so a working sender is part
of the feature rather than an optional integration. Consequences that shape this module:

* **No silent skip.** An unconfigured channel raises :class:`MailDeliveryError`; the endpoint
  turns that into `503`. Falling back to "accept the address unverified" would quietly remove
  the only proof the flow has.
* **No secrets in logs.** Neither the message body (it contains the code) nor the SMTP
  credentials are ever logged; a failure is reported by exception type only.
* **One seam for tests.** :func:`deliver` is the single place that touches the network, so a
  test can replace that one function and exercise the real code path around it.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Final

from ..config import Settings
from ..logging import get_logger

logger = get_logger("mailer")

SUBJECT: Final = "AutoGrader 注册验证码"

BODY: Final = """你好:

你正在注册 AutoGrader。本次注册的验证码是:

    {code}

该验证码 {minutes} 分钟内有效,且只能使用一次。验证码用于确认这个邮箱属于你,
请勿转发给他人。

如果这不是你发起的注册,请忽略本邮件;没有验证码,任何人无法用这个邮箱完成注册。

—— AutoGrader
"""


class MailDeliveryError(RuntimeError):
    """The message could not be delivered. The detail never contains a credential."""


def deliver(settings: Settings, message: EmailMessage) -> None:
    """Hand the message to the configured SMTP server.

    Split out so tests can replace exactly this function; everything else in this module is
    deterministic formatting and error translation.
    """
    if settings.smtp_uses_implicit_tls:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=ssl.create_default_context(),
        )
    else:
        client = smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        )
    try:
        client.ehlo()
        if settings.smtp_uses_starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.smtp_user:
            client.login(settings.smtp_user, settings.smtp_password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):  # pragma: no cover - best effort
            client.close()


def send_verification_code(settings: Settings, *, to: str, code: str, ttl_seconds: int) -> None:
    """Compose and send the verification mail.

    Raises :class:`MailDeliveryError` when the channel is not configured or the server refuses
    the message. The caller reports `503`; nothing is created and no code is consumed.
    """
    if not settings.smtp_configured:
        raise MailDeliveryError(
            "the registration mail channel is not configured "
            "(SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM)"
        )

    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from))
    message["To"] = to
    message.set_content(BODY.format(code=code, minutes=max(1, ttl_seconds // 60)))

    try:
        deliver(settings, message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        # Type only: a transport error string can echo the server banner, the recipient or a
        # half-typed credential, none of which belongs in a log line.
        raise MailDeliveryError(
            f"the mail server refused the message ({type(error).__name__})"
        ) from error

    # Identifiers only: the address itself is PII and must not reach a log line.
    logger.info("registration.code.sent", extra={"event": "registration.code.sent"})
