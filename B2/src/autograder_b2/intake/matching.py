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

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import quote

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


class MatchingUnavailable(RuntimeError):
    """身份查询不可用（令牌错误、服务不可达、响应异常）。

    这与 `not_matched` 有本质区别：后者是"这封信确实不属于任何账号"，应当隔离并等待人工认领；
    前者是"我们现在判断不了"，此时把每一封邮件都隔离起来会把故障伪装成正常业务。调用方应当让
    本次拉取失败（不写 checkpoint），下一次重试。
    """


class DirectoryEmailMatcher:
    """按发件人向 B4 查询账号，再与本次作业的统一目标拼成提交身份。"""

    def __init__(
        self,
        *,
        base_url: str,
        authorization: str,
        target: Mapping[str, str],
        timeout_seconds: float = 10.0,
        opener: Callable[[str, Mapping[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._target = dict(target)
        self._timeout = timeout_seconds
        self._opener = opener or _http_get

    def match(
        self, *, mailbox: str, sender: str, attachment_names: Sequence[str]
    ) -> MatchResult:
        normalized = sender.strip().lower()
        if not normalized:
            return MatchResult(
                outcome="not_matched",
                target=None,
                candidates=0,
                identities=0,
                detail="the message carries no sender address",
                scope_context=None,
            )
        url = (
            f"{self._base_url}/api/v1/internal/identities/by-email"
            f"?email={quote(normalized, safe='')}"
        )
        status, payload = self._opener(
            url, {"authorization": self._authorization}, self._timeout
        )
        if status == 404:
            return MatchResult(
                outcome="not_matched",
                target=None,
                candidates=0,
                identities=0,
                detail="the sender address is not registered to any account",
                scope_context=None,
            )
        if status != 200:
            raise MatchingUnavailable(f"identity lookup returned HTTP {status}")
        try:
            document = json.loads(payload)
        except Exception as error:
            raise MatchingUnavailable("identity lookup returned invalid JSON") from error
        if not isinstance(document, dict) or not document.get("user_id"):
            raise MatchingUnavailable("identity lookup returned no user id")
        if not document.get("is_active", True):
            return MatchResult(
                outcome="not_matched",
                target=None,
                candidates=0,
                identities=0,
                detail="the sender's account is deactivated",
                scope_context=None,
            )

        target = SubmissionTarget(
            student_id=str(document["user_id"]),
            course_id=self._target["course_id"],
            section_id=self._target["section_id"],
            question_version_id=self._target["question_version_id"],
        )
        return MatchResult(
            outcome="matched",
            target=target,
            candidates=1,
            identities=1,
            detail="one identity resolved from the sender address",
            scope_context=ScopeContext(
                course_id=target.course_id, section_id=target.section_id
            ),
        )


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """最小 HTTP GET：只用标准库，与模块"无第三方依赖"的约定一致。"""
    request = urllib.request.Request(url, method="GET")
    for name, value in headers.items():
        request.add_header(name, value)
    request.add_header("user-agent", "autograder-b2-intake/1.0")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
