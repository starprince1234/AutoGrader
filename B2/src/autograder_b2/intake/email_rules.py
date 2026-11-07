"""`email_delivery` 题目的**共享**邮件判定规则引擎（纯函数：无 DB、无 IO、无日志、无时钟）。

两条判定链必须走**同一个**函数，否则"参考答案检验"与"学生提交评测"会各自漂移：

| 调用方 | 输入来源 |
|---|---|
| 评测编排（`evaluation` 的 `email_delivery` 分支） | `b2.email_verifications` 里记录的真实 IMAP 邮件事实（`load_email_receipt`） |
| 参考答案验证（`reference`） | 受保护的「合成邮件回执 JSON」（调用方解析后直接构造 `EmailReceipt`） |

因此本模块只回答"给定这些邮件事实与这些要求，四个 check 各自过不过"，**不**决定依赖是否可用：
邮件收件依赖不可用（心跳过期/未配置）属于 `infrastructure_error`，由调用方用
`intake.email_verification.intake_health` 判定，绝不折算成学生答错。

## 判定语义（`MAIN/docs/architecture/unix-homwork-evaluation.md` §3.5）

- `email_received`：存在与本次提交绑定、来自真实 IMAP 拉取的验证记录；
- `subject`：实际主题等于 `subject_template.format(student_number=..., attempt=...)`（学号来自已核验账号，
  `attempt` 来自 package）；
- `sender`：实际发件地址等于该账号已验证邮箱，**且**等于该学生在该课程/课序绑定的首次发件地址；
- `attachment`：实际附件名包含 `required_attachment_template.format(student_number=...)`。

失败类别只有两个：字段/事实不符 = `output_mismatch`；没有收到邮件 = `missing_output`。
**收到的邮件永远返回四条结论**（固定顺序），即使 package 只声明了其中一部分——调用方按
`outcome.check` 过滤，从而"每个 email_check 用例一条证据"的对应关系不依赖本模块猜测 package 内容。

## 脱敏纪律

`message` 是**固定文案**，不含实际主题、发件地址、账号邮箱、附件名或正文；`detail` 只放布尔值与计数。
本模块从不读取正文（`EmailReceipt` 根本没有正文字段），因此不可能把正文带进证据或日志。
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Literal

#: `execution.email.subject_template` 的默认值（与设计文档 §2 一致）。
SUBJECT_TEMPLATE = "{student_number}_{attempt}"
#: `execution.email.required_attachment_template` 的默认值（与设计文档 §2 一致）。
REQUIRED_ATTACHMENT_TEMPLATE = "{student_number}.txt"
#: 模板里允许出现的占位符名字：写别的名字是 package 缺陷，不是学生失败。
_ALLOWED_TEMPLATE_FIELDS = frozenset({"student_number", "attempt"})
#: `execution.email.attempt` 的取值范围（设计文档 §2 的校验规则：1..99）。
MIN_ATTEMPT = 1
MAX_ATTEMPT = 99

#: 四个 check 名（与 package `email_check` 的取值域逐字一致）。
EmailCheck = Literal["email_received", "subject", "sender", "attachment"]
#: 判定顺序：固定顺序让证据顺序可复算，也便于调用方按声明过滤。
EMAIL_CHECKS: tuple[EmailCheck, ...] = ("email_received", "subject", "sender", "attachment")

#: 失败类别（评测报告的 `failure_category` 取值域中的两个）。
EmailFailureCategory = Literal["missing_output", "output_mismatch"]


@dataclass(frozen=True, slots=True)
class EmailRequirements:
    """一封 `email_delivery` 邮件必须满足的要求（来自 package 的 `execution.email`）。

    `attempt` 与两个模板都由 B3 在编译期校验；这里再做一次独立校验（解析期拒绝），
    因为"package 合法"与"这份 package 真的能判"是两件事。
    """

    attempt: int = 1
    subject_template: str = SUBJECT_TEMPLATE
    required_attachment_template: str = REQUIRED_ATTACHMENT_TEMPLATE

    def __post_init__(self) -> None:
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            # ValueError, not TypeError: the contract/tests treat every invalid declaration
            # (wrong type or out-of-range value) as one ValueError from the constructor, and
            # the package loader maps that to a single PackageRejected reason.
            raise ValueError("execution.email.attempt must be an integer")  # noqa: TRY004
        if not MIN_ATTEMPT <= self.attempt <= MAX_ATTEMPT:
            raise ValueError(
                f"execution.email.attempt must be between {MIN_ATTEMPT} and {MAX_ATTEMPT}"
            )
        _require_template(self.subject_template, "subject_template")
        _require_template(self.required_attachment_template, "required_attachment_template")


@dataclass(frozen=True, slots=True)
class EmailReceipt:
    """一次投递的**邮件事实**视图（来自真实 IMAP 记录，或参考答案验证的合成回执）。

    字段全部公开且不可变：参考答案验证直接构造它，学生评测由 `load_email_receipt` 从
    `b2.email_verifications` 组装它。两者调用同一个 `evaluate_email_receipt`。

    - `student_number`：**已核验账号**的学号（不是从邮件里猜的）；`None` = 账号没有学号，
      此时 subject/attachment 无法判定而失败（`output_mismatch`），而不是"猜一个学号"。
    - `account_email_verified`：账号邮箱在注册流程里已证明归属（设计 §4.1 的既有事实）。
    - `sender_binding_matches`：本次发件地址等于该学生在该课程/课序的首个绑定地址。
    """

    student_number: str | None
    subject: str | None
    sender: str | None
    account_email: str | None
    attachment_names: tuple[str, ...] = ()
    received: bool = False
    account_email_verified: bool = False
    sender_binding_matches: bool = False

    @classmethod
    def not_received(cls) -> EmailReceipt:
        """没有收到任何与提交绑定的邮件时的空回执（四个 check 都会是 `missing_output`）。"""
        return cls(
            student_number=None,
            subject=None,
            sender=None,
            account_email=None,
            attachment_names=(),
            received=False,
            account_email_verified=False,
            sender_binding_matches=False,
        )


@dataclass(frozen=True, slots=True)
class EmailCheckOutcome:
    """单个 `email_check` 的判定结论（`detail` 只含布尔/计数，绝不含被判定值本身）。"""

    check: EmailCheck
    passed: bool
    category: EmailFailureCategory | None
    message: str
    detail: dict[str, object] = field(default_factory=dict)


def parse_subject(subject: str, *, template: str = SUBJECT_TEMPLATE) -> tuple[str, int] | None:
    """按模板解析主题为 `(student_number, attempt)`；不匹配返回 `None`（不猜）。

    只用于"这封邮件**自称**是哪个学号的第几次"的诊断与测试；判定学号是否可信的永远是
    `EmailReceipt.student_number`（来自已核验账号），不是主题。
    """
    if not isinstance(subject, str):
        return None
    pattern = _template_pattern(template)
    match = pattern.fullmatch(subject.strip())
    if match is None:
        return None
    attempt = int(match.group("attempt"))
    return match.group("student_number"), attempt


def subject_matches(
    subject: str | None,
    student_number: str | None,
    attempt: int,
    *,
    template: str = SUBJECT_TEMPLATE,
) -> bool:
    """实际主题是否等于模板渲染结果（缺任一必需事实即 `False`，不猜）。"""
    if subject is None or student_number is None:
        return False
    expected = _render(template, student_number=student_number, attempt=attempt)
    return subject.strip() == expected


def attachment_matches(
    attachment_names: tuple[str, ...],
    student_number: str | None,
    *,
    attempt: int | None = None,
    template: str = REQUIRED_ATTACHMENT_TEMPLATE,
) -> bool:
    """实际附件名里是否包含模板渲染出的必需附件名（精确成员判定，不做大小写归一）。"""
    if student_number is None:
        return False
    required = _render(template, student_number=student_number, attempt=attempt)
    return required in attachment_names


def evaluate_email_receipt(
    receipt: EmailReceipt,
    requirements: EmailRequirements,
) -> tuple[EmailCheckOutcome, ...]:
    """按 package 要求逐项判定，**永远**返回四条结论（`EMAIL_CHECKS` 的固定顺序）。

    调用方（评测编排）按 package 声明的 `email_check` 过滤这四条；参考答案验证用同一函数、
    不含邮件事实的合成回执也走这里，因此两条路径的判定不可能分叉。

    没有收到邮件时四条全部 `missing_output`（**不**折算成字段不符）；收到邮件后，任何事实不符
    都是 `output_mismatch`。本函数不产生 `infrastructure_error`——那属于依赖可用性判定。
    """
    return (
        _email_received_outcome(receipt),
        _subject_outcome(receipt, requirements),
        _sender_outcome(receipt),
        _attachment_outcome(receipt, requirements),
    )


# --- 各 check 的判定（顺序固定，消息固定文案） --------------------------------------


def _email_received_outcome(receipt: EmailReceipt) -> EmailCheckOutcome:
    if receipt.received:
        return EmailCheckOutcome(
            check="email_received",
            passed=True,
            category=None,
            message="a message from this submission was received through the mail intake",
            detail={"received": True},
        )
    return EmailCheckOutcome(
        check="email_received",
        passed=False,
        category="missing_output",
        message="no message from this submission has been received through the mail intake",
        detail={"received": False},
    )


def _subject_outcome(
    receipt: EmailReceipt, requirements: EmailRequirements
) -> EmailCheckOutcome:
    if not receipt.received:
        return _missing("subject", "no message was received, so the subject cannot be checked")
    if receipt.student_number is None:
        return EmailCheckOutcome(
            check="subject",
            passed=False,
            category="output_mismatch",
            message=(
                "the verified account carries no student number,"
                " so the subject cannot be compared"
            ),
            detail={"student_number_recorded": False},
        )
    if receipt.subject is None:
        return EmailCheckOutcome(
            check="subject",
            passed=False,
            category="output_mismatch",
            message="the received message carries no subject",
            detail={"subject_present": False},
        )
    if subject_matches(receipt.subject, receipt.student_number, requirements.attempt):
        return EmailCheckOutcome(
            check="subject",
            passed=True,
            category=None,
            message="the received subject matches the required template",
            detail={"attempt": requirements.attempt},
        )
    return EmailCheckOutcome(
        check="subject",
        passed=False,
        category="output_mismatch",
        message="the received subject does not match the required template",
        detail={"attempt": requirements.attempt},
    )


def _sender_outcome(receipt: EmailReceipt) -> EmailCheckOutcome:
    if not receipt.received:
        return _missing("sender", "no message was received, so the sender cannot be checked")
    if not receipt.account_email_verified:
        return EmailCheckOutcome(
            check="sender",
            passed=False,
            category="output_mismatch",
            message="the account's e-mail address is not verified",
            detail={"account_email_verified": False},
        )
    if receipt.sender is None or receipt.account_email is None:
        return EmailCheckOutcome(
            check="sender",
            passed=False,
            category="output_mismatch",
            message="the message or the account carries no sender address",
            detail={"sender_present": receipt.sender is not None},
        )
    if _normalized_email(receipt.sender) != _normalized_email(receipt.account_email):
        return EmailCheckOutcome(
            check="sender",
            passed=False,
            category="output_mismatch",
            message="the message sender does not match the account's verified e-mail address",
            detail={"sender_matches_account_email": False},
        )
    if not receipt.sender_binding_matches:
        return EmailCheckOutcome(
            check="sender",
            passed=False,
            category="output_mismatch",
            message=(
                "the message sender does not match the first sender address bound"
                " for this course and section"
            ),
            detail={"sender_binding_matches": False},
        )
    return EmailCheckOutcome(
        check="sender",
        passed=True,
        category=None,
        message="the message sender matches the verified and bound address",
        detail={"sender_matches_account_email": True, "sender_binding_matches": True},
    )


def _attachment_outcome(
    receipt: EmailReceipt, requirements: EmailRequirements
) -> EmailCheckOutcome:
    if not receipt.received:
        return _missing(
            "attachment", "no message was received, so the attachment cannot be checked"
        )
    if receipt.student_number is None:
        return EmailCheckOutcome(
            check="attachment",
            passed=False,
            category="output_mismatch",
            message=(
                "the verified account carries no student number,"
                " so the required attachment name cannot be rendered"
            ),
            detail={"student_number_recorded": False},
        )
    if attachment_matches(
        receipt.attachment_names,
        receipt.student_number,
        attempt=requirements.attempt,
        template=requirements.required_attachment_template,
    ):
        return EmailCheckOutcome(
            check="attachment",
            passed=True,
            category=None,
            message="the received message carries the required attachment name",
            detail={"attachment_count": len(receipt.attachment_names)},
        )
    return EmailCheckOutcome(
        check="attachment",
        passed=False,
        category="output_mismatch",
        message="the received message does not carry the required attachment name",
        detail={"attachment_count": len(receipt.attachment_names)},
    )


def _missing(check: EmailCheck, message: str) -> EmailCheckOutcome:
    return EmailCheckOutcome(
        check=check, passed=False, category="missing_output", message=message, detail={}
    )


# --- 模板 ------------------------------------------------------------------------


def _require_template(template: object, name: str) -> None:
    """校验模板：字符串、可格式化、只含允许的占位符（每个至多一次）且没有格式说明/转换。"""
    if not isinstance(template, str) or not template:
        raise ValueError(f"execution.email.{name} must be a non-empty string")
    parts = _template_parts(template, name)
    fields = [field for _literal, field in parts if field is not None]
    if len(fields) != len(set(fields)):
        raise ValueError(f"execution.email.{name} repeats a placeholder")


def _template_parts(template: str, name: str = "template") -> list[tuple[str, str | None]]:
    """把模板切成 `(literal, field_name)` 序列；非法模板抛 `ValueError`（不含模板内容回显）。"""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as error:
        raise ValueError(f"execution.email.{name} is not a valid format template") from error
    parts: list[tuple[str, str | None]] = []
    for literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            parts.append((literal, None))
            continue
        if field_name not in _ALLOWED_TEMPLATE_FIELDS:
            raise ValueError(f"execution.email.{name} uses an unsupported placeholder")
        if format_spec or conversion:
            raise ValueError(
                f"execution.email.{name} must not use format specs or conversions"
            )
        parts.append((literal, field_name))
    return parts


def _render(template: str, **values: object) -> str:
    parts = _template_parts(template)
    rendered: list[str] = []
    for literal, field_name in parts:
        rendered.append(literal)
        if field_name is not None:
            rendered.append(str(values[field_name]))
    return "".join(rendered)


def _template_pattern(template: str) -> re.Pattern[str]:
    """把模板编译成正则：字面量精确匹配，`student_number`/`attempt` 变成命名捕获组。"""
    parts = _template_parts(template)
    fields = [field for _literal, field in parts if field is not None]
    if len(fields) != len(set(fields)):
        raise ValueError("parse_subject does not accept a repeated placeholder")
    groups = set(fields)
    if groups != _ALLOWED_TEMPLATE_FIELDS:
        raise ValueError("parse_subject requires both student_number and attempt placeholders")
    fragments: list[str] = []
    for literal, field_name in parts:
        # Every part carries its preceding literal, including parts that carry a placeholder.
        # Dropping the literal when a field is present silently compiles the default template to
        # `(?P<student_number>...)(?P<attempt>...)` and every real subject then fails to parse.
        fragments.append(re.escape(literal))
        if field_name is None:
            continue
        if field_name == "student_number":
            # 学号按题面要求是半角阿拉伯数字；允许字母会让 `prefix2024141460192_1` 这种
            # "主题里带前缀"的输入被解析成一个合法学号（学号段吃掉前缀），因此这里收紧为数字。
            fragments.append(r"(?P<student_number>[0-9]+)")
        else:
            fragments.append(r"(?P<attempt>[0-9]{1,2})")
    return re.compile("".join(fragments))


def _normalized_email(value: str) -> str:
    return value.strip().lower()


__all__ = [
    "EMAIL_CHECKS",
    "MAX_ATTEMPT",
    "MIN_ATTEMPT",
    "REQUIRED_ATTACHMENT_TEMPLATE",
    "SUBJECT_TEMPLATE",
    "EmailCheck",
    "EmailCheckOutcome",
    "EmailFailureCategory",
    "EmailReceipt",
    "EmailRequirements",
    "attachment_matches",
    "evaluate_email_receipt",
    "parse_subject",
    "subject_matches",
]
