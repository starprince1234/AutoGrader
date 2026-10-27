"""邮件 → 提交身份的匹配端口（B2 只定义端口，不读 B4 的名册）。

为什么是端口而不是查询：B2 的声明依赖只有 B4 的 JWT/JWKS 与 B3 的 evaluation package，
**不得** import B4/B3 的代码、也**不得**读它们的 schema。因此"这封邮件是谁的哪个作业"这件事
只能由外部（部署时的显式配置，将来由 B4 的适配器）回答，B2 这里只固定**契约与失败语义**：

- 命中**唯一身份** → 放行；
- 0 个身份（`not_matched`）或 ≥2 个**不同**身份（`ambiguous`）→ 由调用方隔离，**绝不猜**。

"唯一"的判定口径有意写清楚：**去重后的身份数量为 1 才算唯一**。同一封邮件命中两条规则、
但两条规则指向同一个身份时不算歧义——没有任何猜测发生；指向两个不同身份时才算歧义。
（若按"命中规则条数"判定，`{"*": X, "alice@example.edu": X}` 这种最常见的兜底配置会永远歧义。）

端口只接收 `(mailbox, sender, attachment names)`：发件人与附件名都是**可伪造**输入，因此它们只
参与"查表"，不参与身份构造；本模块的任何返回文本都**不含**发件人地址（PII）与邮件内容。

## 隔离记录的 `scope_context`：只从"规则候选共识"产生

隔离记录必须能用课程/课序做**行级授权**，但"匹配失败时课程本身就不存在"。唯一可信的来源是
**命中的规则本身**：每条规则都显式写了 `course_id/section_id`，它们是运营配置而不是邮件内容。
因此 `MatchResult.scope_context` 的判定口径固定为：

- 命中规则指向的 `(course_id, section_id)` **完全一致**（去重后只有一个组合）→ 带出这一对值；
- 没有命中任何规则（`not_matched`）→ `null`；
- 候选跨课程或跨课序（`ambiguous`）→ `null`（宁可让管理员处理，也不猜）。

本模块**不**从 `mailbox`、发件人、附件名或请求参数推测 scope：那些都是可伪造输入，用它们补
课程关联等于伪造授权依据。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from autograder_b2.config import ConfigurationError

#: 匹配结论三态。`matched` 之外的两种都由调用方隔离（不是"猜一个最像的"）。
MatchOutcome = Literal["matched", "not_matched", "ambiguous"]

#: 规则键里"任意发件人"的写法。
ANY_SENDER = "*"
#: 规则键里"发件人 | 必备附件名"的分隔符。
RULE_SEPARATOR = "|"

_IDENTITY_FIELDS = ("student_id", "course_id", "section_id", "question_version_id")


class MatchingRuleError(ConfigurationError):
    """匹配规则配置非法（缺失字段、非法 UUID、空键……）。启动即失败，不做静默跳过。"""


@dataclass(frozen=True, slots=True)
class SubmissionTarget:
    """一个提交应当归属的身份四元组（跨模块引用只存 UUID，B2 不校验它们是否存在）。"""

    student_id: str
    course_id: str
    section_id: str
    question_version_id: str


@dataclass(frozen=True, slots=True)
class ScopeContext:
    """隔离记录可持久化的课程/课序关联（**只由规则候选共识产生**，见模块文档）。

    两个字段成对存在：数据库约束保证"同时为空或同时非空"，服务层据此判定"这条记录能否按
    课程/课序授权"。
    """

    course_id: str
    section_id: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    """匹配结论；`detail` 已脱敏（只含规则计数，不含发件人/正文/附件原文）。"""

    outcome: MatchOutcome
    target: SubmissionTarget | None
    candidates: int
    identities: int
    detail: str
    #: 命中规则共同指向的课程/课序；`not_matched`、跨 scope 的 `ambiguous` 为 `None`。
    #: 只有"可审计的候选共识"才会到这里——这是隔离记录 scope 字段的**唯一**产生方式。
    scope_context: ScopeContext | None = None


@runtime_checkable
class EmailMatcher(Protocol):
    """匹配端口：把 `(mailbox, sender, attachment names)` 解析成唯一身份或"不唯一"。"""

    def match(
        self, *, mailbox: str, sender: str, attachment_names: Sequence[str]
    ) -> MatchResult: ...


@dataclass(frozen=True, slots=True)
class _Rule:
    sender: str | None  # None = 任意发件人
    required_attachment: str | None
    target: SubmissionTarget


class MappingEmailMatcher:
    """配置驱动的实现：一张显式映射表，测试与单邮箱部署都用它。

    规则键形态（全部大小写不敏感，除附件名外）：

    | 键 | 含义 |
    |---|---|
    | `alice@example.edu` | 该发件人的任何邮件 |
    | `alice@example.edu\\|main.py` | 该发件人且**含**该文件名附件的邮件 |
    | `*` | 任意发件人（兜底） |

    规则值必须是含 `student_id`/`course_id`/`section_id`/`question_version_id` 四个 UUID 字符串的
    对象；缺失或非法即 `MatchingRuleError`（配置错误必须当场暴露，不能变成"这封邮件没人认领"）。
    """

    def __init__(self, rules: Mapping[str, Mapping[str, str]]) -> None:
        if not rules:
            raise MatchingRuleError("at least one matching rule is required")
        self._rules = tuple(_parse_rule(key, value) for key, value in rules.items())

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def match(
        self, *, mailbox: str, sender: str, attachment_names: Sequence[str]
    ) -> MatchResult:
        normalized = sender.strip().lower()
        names = frozenset(attachment_names)
        matched = [
            rule
            for rule in self._rules
            if (rule.sender is None or rule.sender == normalized)
            and (rule.required_attachment is None or rule.required_attachment in names)
        ]
        identities = {rule.target for rule in matched}
        scope = _scope_of(matched)
        if not identities:
            return MatchResult(
                outcome="not_matched",
                target=None,
                candidates=len(matched),
                identities=0,
                detail=f"no rule matched (rules={len(self._rules)})",
                scope_context=None,
            )
        if len(identities) > 1:
            return MatchResult(
                outcome="ambiguous",
                target=None,
                candidates=len(matched),
                identities=len(identities),
                detail=f"{len(identities)} distinct identities matched",
                scope_context=scope,
            )
        return MatchResult(
            outcome="matched",
            target=next(iter(identities)),
            candidates=len(matched),
            identities=1,
            detail=f"one identity matched (rules={len(matched)})",
            scope_context=scope,
        )


def _scope_of(matched: Sequence[_Rule]) -> ScopeContext | None:
    """命中规则的课程/课序**共识**：只有一个 `(course_id, section_id)` 组合时返回它。

    没有命中（空序列）或候选跨课程/跨课序时返回 `None`。判定基于**去重后的组合数**而不是
    命中规则条数：同一门课、同一个课序的多条规则（含 `*` 兜底）不构成歧义。
    """
    pairs = {(rule.target.course_id, rule.target.section_id) for rule in matched}
    if len(pairs) != 1:
        return None
    course_id, section_id = next(iter(pairs))
    return ScopeContext(course_id=course_id, section_id=section_id)


def _parse_rule(key: str, value: Mapping[str, str]) -> _Rule:
    raw_key = key.strip()
    if not raw_key:
        raise MatchingRuleError("matching rule key must not be empty")
    sender, separator, attachment = raw_key.partition(RULE_SEPARATOR)
    sender = sender.strip().lower()
    attachment = attachment.strip()
    if separator and not attachment:
        raise MatchingRuleError(f"matching rule {raw_key!r} names an empty attachment")
    if not sender:
        raise MatchingRuleError(f"matching rule {raw_key!r} names an empty sender")

    missing = [name for name in _IDENTITY_FIELDS if not str(value.get(name, "")).strip()]
    if missing:
        raise MatchingRuleError(f"matching rule {raw_key!r} is missing {', '.join(missing)}")
    unknown = sorted(set(value) - set(_IDENTITY_FIELDS))
    if unknown:
        raise MatchingRuleError(f"matching rule {raw_key!r} has unknown fields: {', '.join(unknown)}")
    return _Rule(
        sender=None if sender == ANY_SENDER else sender,
        required_attachment=attachment or None,
        target=SubmissionTarget(
            student_id=_canonical_uuid(raw_key, "student_id", value["student_id"]),
            course_id=_canonical_uuid(raw_key, "course_id", value["course_id"]),
            section_id=_canonical_uuid(raw_key, "section_id", value["section_id"]),
            question_version_id=_canonical_uuid(
                raw_key, "question_version_id", value["question_version_id"]
            ),
        ),
    )


def _canonical_uuid(rule: str, field_name: str, value: str) -> str:
    """规则里的标识必须是 UUID；这里**规范化**成小写规范串（I7），非法即配置错误。"""
    try:
        parsed = uuid.UUID(value.strip())
    except (AttributeError, ValueError) as error:
        raise MatchingRuleError(f"matching rule {rule!r} field {field_name} is not a UUID") from error
    return str(parsed)
