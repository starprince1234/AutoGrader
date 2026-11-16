"""邮件提交成功后的学生端回执发送器（best-effort）。

当 `EmailIntakeService` 成功解析并落库一份学生提交邮件（通过核验码与五行正文校验）后，
复用部署环境中已有的 `SMTP_*` 环境变量向发件学生发送“提交成功”确认邮件，告知：
1. 哪些题目附件（如 `1.txt`、`2.txt`）已成功解析并创建提交；
2. 提交内容已同步至网页端作答区（学生打开网页工作台即可看到自动回填的代码）且已触发 AI 评测。

回执邮件是“已确认落库后的通知反馈”，任何 SMTP 抖动只记日志、绝不回滚已落库提交或阻碍
IMAP checkpoint 推进。
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from autograder_b2.logging.logger import log_event

logger = logging.getLogger("autograder_b2.intake.receipt_mailer")


@dataclass(frozen=True, slots=True)
class ReceiptSmtpSettings:
    """从环境变量读取的 SMTP 发送配置（口令不进入 repr）。"""

    host: str
    port: int
    user: str
    password: str
    sender_address: str
    sender_name: str
    tls_mode: str
    timeout_seconds: float

    def __repr__(self) -> str:  # pragma: no cover - 避免凭据进日志
        return (
            f"ReceiptSmtpSettings(host={self.host!r}, port={self.port!r}, "
            f"sender_address={self.sender_address!r}, tls_mode={self.tls_mode!r})"
        )

    @property
    def uses_implicit_tls(self) -> bool:
        mode = self.tls_mode.lower()
        if mode in {"implicit_tls", "ssl", "smtps", "implicit"}:
            return True
        if mode == "tls":
            return self.port == 465
        return False

    @property
    def uses_starttls(self) -> bool:
        return self.tls_mode.lower() in {"starttls", "tls"} and not self.uses_implicit_tls


def load_receipt_smtp_settings(
    environ: Mapping[str, str] | None = None,
) -> ReceiptSmtpSettings | None:
    """从环境变量加载 SMTP 配置；核心字段缺失时返回 ``None``（跳过发送）。"""
    source = os.environ if environ is None else environ
    host = (source.get("SMTP_HOST") or "").strip()
    user = (source.get("SMTP_USER") or "").strip()
    password = source.get("SMTP_PASS") or ""
    sender_address = (source.get("SMTP_FROM") or user).strip()
    if not host or not sender_address:
        return None
    raw_port = (source.get("SMTP_PORT") or "465").strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = 465
    raw_timeout = (source.get("SMTP_TIMEOUT_SECONDS") or "15").strip()
    try:
        timeout_seconds = max(1.0, float(raw_timeout))
    except ValueError:
        timeout_seconds = 15.0
    sender_name = (source.get("SMTP_FROM_NAME") or "AutoGrader 作业评测平台").strip()
    tls_mode = (source.get("SMTP_TLS_MODE") or "tls").strip().lower()
    return ReceiptSmtpSettings(
        host=host,
        port=port,
        user=user,
        password=password,
        sender_address=sender_address,
        sender_name=sender_name,
        tls_mode=tls_mode,
        timeout_seconds=timeout_seconds,
    )


def deliver_smtp_message(settings: ReceiptSmtpSettings, message: EmailMessage) -> None:
    """实际通过 SMTP 投递单封邮件（拆出以便单测替换）。"""
    if settings.uses_implicit_tls:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.host,
            settings.port,
            timeout=settings.timeout_seconds,
            context=ssl.create_default_context(),
        )
    else:
        client = smtplib.SMTP(
            settings.host,
            settings.port,
            timeout=settings.timeout_seconds,
        )
    try:
        client.ehlo()
        if settings.uses_starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.user:
            client.login(settings.user, settings.password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):  # pragma: no cover - best effort
            client.close()


def build_submission_receipt_message(
    *,
    settings: ReceiptSmtpSettings,
    recipient_email: str,
    student_name: str,
    student_number: str,
    assignment_id: str,
    assignment_title: str | None,
    attachment_names: Sequence[str],
    submission_ids: Sequence[str],
) -> EmailMessage:
    """构造“邮件提交成功确认”邮件。"""
    title_label = assignment_title.strip() if assignment_title else assignment_id
    lines = [
        f"{student_name or '同学'}（学号：{student_number or '未提供'}），您好：",
        "",
        f"您通过邮件提交的作业《{title_label}》已通过核验并成功解析入库！",
        f"作业编号：{assignment_id}",
        f"本次接收题目数：{len(attachment_names)}",
        "",
        "已接收附件与提交记录清单：",
    ]
    for idx, attachment_name in enumerate(attachment_names):
        submission_id = submission_ids[idx] if idx < len(submission_ids) else "-"
        lines.append(f"  - 附件 {attachment_name}（提交编号：{submission_id}）")
    lines.extend(
        [
            "",
            "平台已自动为上述题目触发 AI 评测，同时您提交的附件内容会自动同步填充到网页端对应题目的代码编辑器中。",
            "您可以随时登录 AutoGrader 网页端查看代码内容、AI 实时评价与最终得分。",
            "",
            "（本邮件由系统自动发送，请勿直接回复）",
            "—— AutoGrader 作业评测平台",
        ]
    )

    msg = EmailMessage()
    msg["Subject"] = f"【AutoGrader】作业邮件提交成功确认（{title_label}）"
    msg["From"] = formataddr((settings.sender_name, settings.sender_address))
    msg["To"] = recipient_email
    msg.set_content("\n".join(lines))
    return msg


def build_submission_rejection_message(
    *,
    settings: ReceiptSmtpSettings,
    recipient_email: str,
    note: str | None = None,
) -> EmailMessage:
    """构造"邮件提交失败（打回）"通知邮件。

    只说**失败**与**怎么重交**，不写失败原因：原因码是教师复核的事实，学生拿不到动作，
    清单式的原因只会让他去猜哪些能绕过（与 B1 学生端只显示「提交成功 / 提交失败」一致）。
    """
    lines = [
        "您好：",
        "",
        "您发送的作业提交邮件未能通过核验，本次提交未成功。",
        "",
        "请回到 AutoGrader 的作业页面重新申请核验码，并按工作区给出的格式重新提交：",
        "  - 正文五行：核验码 / 作业编号 / 课序 / 姓名 / 学号",
        "  - 每题一个附件，文件名按题号为 N.txt",
    ]
    if note:
        lines.extend(["", f"教师备注：{note}"])
    lines.extend(
        [
            "",
            "（本邮件由系统自动发送，请勿直接回复）",
            "—— AutoGrader 作业评测平台",
        ]
    )

    msg = EmailMessage()
    msg["Subject"] = "【AutoGrader】作业邮件提交未通过"
    msg["From"] = formataddr((settings.sender_name, settings.sender_address))
    msg["To"] = recipient_email
    msg.set_content("\n".join(lines))
    return msg


def send_submission_rejection_email(
    *,
    recipient_email: str | None,
    note: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """向**原发件地址**发送"提交失败"通知（best-effort，异常只记日志不抛出）。

    返回 `False` 的两种情形都等于契约里的 `notification=unavailable`：没有记录到来件地址，
    或本部署没有 SMTP 配置；投递失败同样只影响这一封信，不回滚已经记录的处置。
    """
    recipient = (recipient_email or "").strip()
    if not recipient:
        return False
    settings = load_receipt_smtp_settings(environ)
    if settings is None:
        log_event(
            logger,
            "intake.rejection_email_skipped",
            level=logging.INFO,
            fields={"reason": "smtp_not_configured"},
        )
        return False

    message = build_submission_rejection_message(
        settings=settings, recipient_email=recipient, note=note
    )
    try:
        deliver_smtp_message(settings, message)
    except (smtplib.SMTPException, OSError, ssl.SSLError, ValueError) as error:
        log_event(
            logger,
            "intake.rejection_email_failed",
            level=logging.WARNING,
            fields={"error_type": type(error).__name__},
        )
        return False

    log_event(logger, "intake.rejection_email_sent", level=logging.INFO, fields={})
    return True


def send_submission_receipt_email(
    *,
    recipient_email: str,
    student_name: str,
    student_number: str,
    assignment_id: str,
    assignment_title: str | None,
    attachment_names: Sequence[str],
    submission_ids: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> bool:
    """向学生发送“提交成功”确认回执（best-effort，异常只记日志不抛出）。"""
    recipient = recipient_email.strip()
    if not recipient or not attachment_names:
        return False
    settings = load_receipt_smtp_settings(environ)
    if settings is None:
        log_event(
            logger,
            "intake.receipt_email_skipped",
            level=logging.INFO,
            fields={"assignment_id": assignment_id, "reason": "smtp_not_configured"},
        )
        return False

    message = build_submission_receipt_message(
        settings=settings,
        recipient_email=recipient,
        student_name=student_name,
        student_number=student_number,
        assignment_id=assignment_id,
        assignment_title=assignment_title,
        attachment_names=attachment_names,
        submission_ids=submission_ids,
    )
    try:
        deliver_smtp_message(settings, message)
    except (smtplib.SMTPException, OSError, ssl.SSLError, ValueError) as error:
        log_event(
            logger,
            "intake.receipt_email_failed",
            level=logging.WARNING,
            fields={
                "assignment_id": assignment_id,
                "error_type": type(error).__name__,
                "submissions": len(submission_ids),
            },
        )
        return False

    log_event(
        logger,
        "intake.receipt_email_sent",
        level=logging.INFO,
        fields={
            "assignment_id": assignment_id,
            "submissions": len(submission_ids),
        },
    )
    return True


__all__ = [
    "ReceiptSmtpSettings",
    "build_submission_receipt_message",
    "build_submission_rejection_message",
    "deliver_smtp_message",
    "load_receipt_smtp_settings",
    "send_submission_receipt_email",
    "send_submission_rejection_email",
]
