"""发件地址 → 账号的查询端口（B2 只定义端口，不读 B4 的名册）。

为什么是端口而不是查询：B2 的声明依赖只有 B4 的 JWT/JWKS 与 B3 的 evaluation package，
**不得** import B4/B3 的代码、也**不得**读它们的 schema。因此"这封邮件是谁发的"只能由部署时
配置的 B4 internal identity 端点回答，B2 这里只固定**契约与失败语义**。

两种结局必须分清：

- `None`：**确定没有这个账号**（B4 回 404；停用账号按"没有"处理）→ 调用方隔离，不猜；
- `MatchingUnavailable`：**查不了**（未配置/网络/令牌/畸形响应）→ 调用方让本轮拉取失败并重试；
  把故障当成"没有这个账号"会把一次配置/连通性问题伪装成一批无人认领的邮件。

## 旧"固定目标 / 匹配规则"路径已整体删除

设计 §1.5/§6.4 明确：邮件不再有"这次收的作业挂到哪门课/哪个题目版本"的全局配置，也不再有
按发件人/附件名查表的路由表（`B2_IMAP_MATCHING_JSON`、`B2_IMAP_TARGET_JSON` 一并移除）。
作业归属只来自**一次性核验码挑战**（`(student, course, section, assignment_id)`），本模块因此
只剩 `lookup_account`：它提供"发件地址属于哪个账号"这一件不可伪造的服务端事实。

`ScopeContext` 仍保留：隔离记录的行级课程范围来自**核验码挑战**（挑战本身绑定课程/课序，
是服务端签发的事实，不是邮件内容），`QuarantineStore.record(scope=...)` 用它。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class ScopeContext:
    """隔离记录可持久化的课程/课序关联（唯一来源：核验码挑战行，不是邮件内容）。

    两个字段成对存在：数据库约束保证"同时为空或同时非空"，服务层据此判定"这条记录能否按
    课程/课序授权"。
    """

    course_id: str
    section_id: str


@dataclass(frozen=True, slots=True)
class MatchedAccount:
    """按发件地址解析出的**账号**事实（只含所有权证明与正文核对所需的字段）。

    `student_number`/`display_name` 来自 B4 internal identity 响应（注册/名单流程里的事实）。
    它们用于邮件正文"姓名/学号"的机器核对：正文声明必须与账号事实**精确一致**（去首尾空白、
    折叠内部空白后比较），因为正文本身是可伪造输入，可信的是账号事实。
    """

    user_id: str
    student_number: str | None = None
    display_name: str | None = None


class MatchingUnavailable(RuntimeError):
    """身份查询不可用（令牌错误、服务不可达、响应异常）。

    这与 `None`（确定没有该账号）有本质区别：前者是"我们现在判断不了"，此时把每一封邮件都
    隔离起来会把故障伪装成正常业务。调用方应当让本次拉取失败（不写 checkpoint），下一次重试。
    """


@runtime_checkable
class SenderDirectory(Protocol):
    """发件地址 → 账号 的只读查询端口（所有权校验与正文核对用）。"""

    def lookup_account(self, sender: str) -> MatchedAccount | None: ...


class DirectoryEmailMatcher:
    """按发件人向 B4 查询账号的实现（只提供 `lookup_account`，不再做提交目标匹配）。"""

    def __init__(
        self,
        *,
        base_url: str,
        authorization: str,
        timeout_seconds: float = 10.0,
        opener: Callable[[str, Mapping[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._timeout = timeout_seconds
        self._opener = opener or _http_get

    def lookup_account(self, sender: str) -> MatchedAccount | None:
        """发件地址 → 账号；`None` = 确定没有该账号，`MatchingUnavailable` = 查不了。

        停用账号按"没有这个账号"处理：停用账号不应通过任何核验。
        """
        normalized = sender.strip().lower()
        if not normalized:
            return None
        document = self._fetch_identity(normalized)
        if document is None or not document.get("is_active", True):
            return None
        raw_number = document.get("student_number")
        student_number = (
            raw_number.strip()
            if isinstance(raw_number, str) and raw_number.strip()
            else None
        )
        raw_name = document.get("display_name")
        display_name = (
            raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
        )
        return MatchedAccount(
            user_id=str(document["user_id"]),
            student_number=student_number,
            display_name=display_name,
        )

    def _fetch_identity(self, normalized_sender: str) -> dict[str, object] | None:
        """查一次 B4 internal identity 端点：`None` = 404（确定没有该账号）。

        所有"查不了"的情形（非 200/非 JSON/缺 user_id）都抛 `MatchingUnavailable`——把故障当成
        "没有这个账号"会把一次配置/连通性问题伪装成一批无人认领的邮件。
        """
        url = (
            f"{self._base_url}/api/v1/internal/identities/by-email"
            f"?email={quote(normalized_sender, safe='')}"
        )
        status, payload = self._opener(
            url, {"authorization": self._authorization}, self._timeout
        )
        if status == 404:
            return None
        if status != 200:
            raise MatchingUnavailable(f"identity lookup returned HTTP {status}")
        try:
            document = json.loads(payload)
        except Exception as error:
            raise MatchingUnavailable("identity lookup returned invalid JSON") from error
        if not isinstance(document, dict) or not document.get("user_id"):
            raise MatchingUnavailable("identity lookup returned no user id")
        return document


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


__all__ = [
    "DirectoryEmailMatcher",
    "MatchedAccount",
    "MatchingUnavailable",
    "ScopeContext",
    "SenderDirectory",
]
