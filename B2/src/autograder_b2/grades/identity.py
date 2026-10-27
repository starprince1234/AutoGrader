"""成绩导出所需的**授权身份关联**（姓名/学号），经 B4 的名单 HTTP 接口取得。

## 为什么不是 join

`b2` schema 里没有姓名与学号，它们属于 B4（`b4.users` / `b4.enrollments`）。B2 的硬规则是
"只读写 `b2` schema；禁止跨 schema 外键/SQL/ORM"（`B2/AGENTS.md`），因此这里既不能
`JOIN b4.enrollments`，也不能在 `b2` 里存第二份身份表。

## 为什么转交调用者的 token，而不是发明一个服务身份

导出是**教师**发起的动作，而"这名教师能否看这个课序的名单"已经由 B4 判定过一次。
本模块把调用者自己的 `Authorization` 头原样转交给 B4 的
`GET /api/v1/sections/{section_id}/enrollments`，因此授权判定仍然只有 B4 一处：

- 不新增跨模块凭据，也不扩大 B2 的权限（B2 拿到的是它本来就被调用者授权范围之内的数据）；
- 转交的是**同一个不透明 bearer token**，B2 不解析、不缓存、不写日志；
- 只按 `section_id` 拉取，响应里也只有该课序的名单——与"返回全校名单"无关。

## 失败必须让导出失败

`TC-P11-055` 的判据是"关联失败时导出失败，且不生成缺姓名的成功文件"。因此：

- 没有配置 B4 地址 → `IdentityNotConfigured`（路由 503）；
- HTTP/解析失败 → `IdentityLookupError`（路由 503）；
- 名单里找不到某行 `student_id`（或名单行未绑定账号）→ `IdentityMissingForRows`（路由 409）。

三种都是**拒绝**，没有"先导出一份空的姓名列"的降级路径：一份看起来成功、实则缺身份的成绩表
比一次明确的失败危险得多（它会被人当成完整的成绩单发出去）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from autograder_b2.grades.query import GradeRow

#: 单页名单条数（B4 `Limit` 上限 200）。
DEFAULT_PAGE_LIMIT = 200
#: 防御性页数上限：一个课序的名单不可能有这么多页，出现即视为上游异常而不是继续翻。
MAX_PAGES = 50
#: 响应体读取上限（防御性：不把无界内容读进内存）。
RESPONSE_LIMIT_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0

#: B4 `listEnrollments` 的路径（契约原文，含 `/api/v1` 前缀）。
ENROLLMENTS_PATH = "/api/v1/sections/{section_id}/enrollments"


class IdentityLookupError(RuntimeError):
    """名单查询失败（网络/HTTP/响应形状/翻页异常）→ 导出失败（503），不生成缺身份文件。"""


class IdentityNotConfigured(RuntimeError):
    """本部署没有配置 B4 地址：身份关联不可用 → 导出 503（而不是悄悄输出空姓名）。"""


class IdentityMissingForRows(LookupError):
    """名单中没有某些成绩行的 `student_id`（或该名单行未绑定平台账号）→ 导出 409。

    消息只说明"哪一节、多少行没匹配上"，不回显姓名/学号或学生 id：那是一份不该出现在错误
    详情与日志里的名单片段。
    """


@dataclass(frozen=True, slots=True)
class EnrollmentIdentity:
    """一条名单身份（字段名照抄 B4 `EnrollmentPublic`，只取本功能需要的三个）。"""

    student_id: str
    student_number: str
    display_name: str


@dataclass(frozen=True, slots=True)
class IdentityLookup:
    """一次名单查询的结果，附带"查了几页"这个可审计读数。"""

    identities: Mapping[str, EnrollmentIdentity]
    pages: int


class EnrollmentTransport(Protocol):
    """HTTP 传输的窄协议（可注入 ⇒ 测试不需要网络；与 `commentary.provider` 同一形态）。"""

    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]: ...


class UrllibEnrollmentTransport:
    """标准库 `urllib.request` 实现（唯一的默认传输；不引入生产依赖）。"""

    def get_json(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, str]:
        request = urllib.request.Request(url, method="GET")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read(RESPONSE_LIMIT_BYTES).decode(
                    "utf-8", errors="replace"
                )
        except urllib.error.HTTPError as error:
            return int(error.code), error.read(RESPONSE_LIMIT_BYTES).decode(
                "utf-8", errors="replace"
            )
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise IdentityLookupError(
                f"enrollment lookup failed: {type(error).__name__}"
            ) from error


class EnrollmentDirectory(Protocol):
    """按课序读取名单（`listEnrollments` 的服务端消费者）。"""

    def identities(
        self, *, section_id: str, authorization: str, request_id: str | None = None
    ) -> IdentityLookup: ...


class HttpEnrollmentDirectory:
    """B4 `listEnrollments` 的 HTTP 客户端（转交调用者 token；游标翻页）。

    `base_url` 是 B4 的 API 源（协议 + 主机）。配置非法（非 http(s)）在装配期就拒绝，
    不把"配错了"表现成运行时的一次空名单。
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: EnrollmentTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        page_limit: int = DEFAULT_PAGE_LIMIT,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("B4 base URL must be an absolute http(s) URL")
        if not 1 <= page_limit <= DEFAULT_PAGE_LIMIT:
            raise ValueError("page limit must be within 1..200")
        self._base_url = base_url.rstrip("/")
        self._transport = transport if transport is not None else UrllibEnrollmentTransport()
        self._timeout = timeout_seconds
        self._page_limit = page_limit

    def identities(
        self, *, section_id: str, authorization: str, request_id: str | None = None
    ) -> IdentityLookup:
        """读取一个课序的完整名单（跟随 `next_cursor`，最多 `MAX_PAGES` 页）。

        只保留绑定平台账号（`student_id` 非空）的行：未绑定账号的名单行无法与成绩行关联，
        把它们塞进映射只会制造一个永远匹配不上的键。
        """
        headers = {"accept": "application/json", "authorization": authorization}
        if request_id:
            headers["X-Request-ID"] = request_id
        identities: dict[str, EnrollmentIdentity] = {}
        cursor: str | None = None
        pages = 0
        while True:
            pages += 1
            if pages > MAX_PAGES:
                raise IdentityLookupError(
                    f"enrollment lookup exceeded {MAX_PAGES} pages for one section"
                )
            query = f"?limit={self._page_limit}"
            if cursor:
                query += f"&cursor={urllib.parse.quote(cursor, safe='')}"
            url = f"{self._base_url}{ENROLLMENTS_PATH.format(section_id=section_id)}{query}"
            status, body = self._transport.get_json(url, headers, self._timeout)
            if status < 200 or status >= 300:
                # 不回显响应正文：它可能包含名单片段。
                raise IdentityLookupError(f"enrollment lookup returned HTTP {status}")
            document = _load(body)
            for item in _items(document):
                identity = _identity(item)
                if identity is not None:
                    identities[identity.student_id] = identity
            cursor = document.get("next_cursor") if isinstance(document, Mapping) else None
            if not isinstance(cursor, str) or cursor == "":
                return IdentityLookup(identities=identities, pages=pages)


def merge_identities(
    rows: list[GradeRow], rosters: Mapping[str, Mapping[str, EnrollmentIdentity]]
) -> list[GradeRow]:
    """按 `section_id` + `student_id` 把姓名/学号合并进成绩行；缺任何一行即拒绝。

    `rosters` 以课序为键：没有 `section_id` 过滤时导出可能跨多个课序，各课序的名单分别读取、
    分别授权。缺失集合只统计"行数 + 课序"，错误消息里不放学生 id 或姓名。
    """
    missing: dict[str, int] = {}
    merged: list[GradeRow] = []
    for row in rows:
        roster = rosters.get(row.section_id, {})
        identity = roster.get(row.student_id)
        if identity is None:
            missing[row.section_id] = missing.get(row.section_id, 0) + 1
            merged.append(row)
            continue
        merged.append(
            replace(
                row,
                student_number=identity.student_number,
                display_name=identity.display_name,
            )
        )
    if missing:
        total = sum(missing.values())
        raise IdentityMissingForRows(
            f"{total} grade row(s) have no matching enrollment in"
            f" {len(missing)} section(s): the roster does not contain their student id"
            " (or the roster row has no linked account)"
        )
    return merged


class StaticEnrollmentDirectory:
    """测试/离线装配用的内存目录（**不是**生产降级路径：生产装配只构造 HTTP 实现）。

    它存在的理由与 `runner.run_case` 的 `probe` 注入相同：让"缺身份时导出拒绝"可以在
    没有 B4 的环境里被断言。
    """

    def __init__(self, rosters: Mapping[str, Mapping[str, EnrollmentIdentity]]) -> None:
        self._rosters = {
            section: dict(entries) for section, entries in rosters.items()
        }

    def identities(
        self, *, section_id: str, authorization: str, request_id: str | None = None
    ) -> IdentityLookup:
        if authorization == "":
            raise IdentityLookupError("enrollment lookup requires an access token")
        return IdentityLookup(identities=self._rosters.get(section_id, {}), pages=1)


def _load(body: str) -> Mapping[str, Any]:
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise IdentityLookupError("enrollment response is not valid JSON") from error
    if not isinstance(document, Mapping):
        raise IdentityLookupError("enrollment response is not a JSON object")
    return document


def _items(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = document.get("items")
    if not isinstance(items, list):
        raise IdentityLookupError("enrollment response is missing its items array")
    rows: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise IdentityLookupError("enrollment items must be JSON objects")
        rows.append(item)
    return rows


def _identity(item: Mapping[str, Any]) -> EnrollmentIdentity | None:
    """一条名单行 → 身份；`student_id` 为空表示该行未绑定账号，跳过（不是错误）。"""
    student_id = item.get("student_id")
    number = item.get("student_number")
    name = item.get("display_name")
    if student_id is None:
        return None
    if not isinstance(student_id, str) or not student_id:
        raise IdentityLookupError("enrollment student_id must be a non-empty string")
    if not isinstance(number, str) or not number:
        raise IdentityLookupError("enrollment student_number must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise IdentityLookupError("enrollment display_name must be a non-empty string")
    return EnrollmentIdentity(student_id=student_id, student_number=number, display_name=name)


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_TIMEOUT_SECONDS",
    "ENROLLMENTS_PATH",
    "EnrollmentDirectory",
    "EnrollmentIdentity",
    "EnrollmentTransport",
    "HttpEnrollmentDirectory",
    "IdentityLookup",
    "IdentityLookupError",
    "IdentityMissingForRows",
    "IdentityNotConfigured",
    "StaticEnrollmentDirectory",
    "UrllibEnrollmentTransport",
    "merge_identities",
]
