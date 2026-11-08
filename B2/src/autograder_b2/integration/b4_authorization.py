"""B4 权威授权/归属查询（service-to-service, 设计 §2.3 / §4）。

作业受众改为**课程级**之后，"这个学生现在是否属于这门课、属于哪个课序"不再能由提交信封里的
`section_id`（客户端可填）或令牌里的 `scope`（登录时签发、会过期）回答。唯一的权威事实是 B4：

    GET {B4_IDENTITY_URL}/api/v1/internal/users/{user_id}/authorization

返回 ``{user_id, platform_roles, course_grants, enrollments}``：平台角色、课程级 staff 授权与
**当前有效**选课（每门课最多一条有效 enrollment）。B2 不读 `b4` schema，因此这是唯一正确的
跨模块通道；URL 与凭据复用现有的 ``B2_IDENTITY_URL`` / ``B2_IDENTITY_AUTHORIZATION``
（与按发件地址查账号的邮件身份目录同一份服务间配置，不新增凭据）。

失败语义只有一种、且必须是**基础设施**失败：不可达、超时、非 200、响应畸形、响应里的 user_id
与请求不符、同一课程出现多条当前选课 —— 一律 ``AuthorizationUnavailable``（对外 503）。
**绝不**把它折成"没有归属"（403）或"空选课"：那会把一次平台故障伪装成学生被移出课程，
而且会让"移出课程后 403"这条安全结论在 B4 挂掉时也恒成立，等于没有结论。

HTTP 打开器可注入（与 ``integration.assignments.B3AssignmentDirectory``、``intake.matching``
同一形态），测试因此可以精确控制"B4 此刻返回什么"，而不需要网络。服务令牌只保存在实例的私有
属性里：它不参与 URL、不出现在 repr、也不进日志或问题详情。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

#: 响应上限：一个用户的授权是少量 UUID；接近这个量级的响应不是我们以为的那个端点。
#: 超限按**基础设施失败**处理，绝不截断后当成部分结果使用。
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 10.0

#: B4 内部授权端点路径（`b4.yaml`；`base_url` 由部署给出，与 B3 的 internal 端点同一约定）。
AUTHORIZATION_PATH = "/api/v1/internal/users/{user_id}/authorization"

#: ``opener(url, headers, timeout) -> (status, body_bytes)`` —— 与 `assignments`/`matching` 同形。
Opener = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]

_TOP_LEVEL_FIELDS = frozenset({"user_id", "platform_roles", "course_grants", "enrollments"})
_ENROLLMENT_FIELDS = frozenset({"course_id", "section_id", "class_id"})


class AuthorizationUnavailable(RuntimeError):
    """B4 授权目录不可用（未配置/不可达/非 200/响应畸形）→ 对外 503 语义。

    这是**基础设施**失败：调用方必须让本轮请求（或整轮邮件拉取）失败并给出可读原因，
    绝不能把它当成"没有归属"（403）或"空选课"。
    """


@dataclass(frozen=True, slots=True)
class Enrollment:
    """一条**当前有效**选课（B4 `b4.enrollments` 的 ID 投影；不含姓名/学号/邮箱）。"""

    course_id: str
    section_id: str
    class_id: str


@dataclass(frozen=True, slots=True)
class UserAuthorization:
    """一个用户的权威归属事实：角色、课程级 staff 授权与当前有效选课。

    只含标识：姓名/学号/邮箱属于另一个只读视图（`grades.identity` / 邮件身份目录），
    本类型存在的目的是授权判定，不承载 PII。
    """

    user_id: str
    platform_roles: tuple[str, ...] = ()
    course_grants: tuple[str, ...] = ()
    enrollments: tuple[Enrollment, ...] = ()

    def is_course_staff_for(self, course_id: object) -> bool:
        """该用户是否是这门课的 staff。

        判据是 **`course_grants` 包含该课程**：契约把 `course_grants` 定义为"该账号是 staff
        （teacher）的课程 id"，而 B4 把课程级教师角色存放在 `b4.roles` 的课程作用域行里
        （`platform_roles` 只返回平台级角色）。因此 grant 存在即该课程的 staff 授权；
        平台级 teacher/admin 若没有课程 grant 并不授予任何具体课程的教学权（管理员由各入口的
        token 级 `is_admin` 豁免处理，与既有口径一致）。
        """
        canonical = _canonical(course_id)
        return canonical is not None and canonical in self.course_grants

    def enrollment_for_course(self, course_id: object) -> Enrollment | None:
        """该用户在这门课的**当前有效**选课；没有即 `None`（不是错误）。"""
        canonical = _canonical(course_id)
        if canonical is None:
            return None
        for enrollment in self.enrollments:
            if enrollment.course_id == canonical:
                return enrollment
        return None


class UserAuthorizationDirectory(Protocol):
    """端口：按 user_id 读取权威授权/归属。

    正常返回 ``UserAuthorization``；查不了（未配置/不可达/响应畸形）抛
    ``AuthorizationUnavailable``。没有"查无此人 → None"的形态：B4 对停用账号返回空角色/空选课，
    因此 200 就是"这个用户存在"的证明，而不是 404。
    """

    def get(self, user_id: str) -> UserAuthorization: ...


class B4UserAuthorizationDirectory:
    """``GET /api/v1/internal/users/{id}/authorization`` 的 HTTP 客户端（服务令牌）。

    失败一律 ``AuthorizationUnavailable``；消息里**不含**主机名、端口、URL 或响应体内容
    （它们可能出现在日志/问题详情里）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        authorization: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Opener | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        if not authorization.strip():
            raise ValueError("authorization must not be empty")
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._opener = opener or _http_get

    @property
    def endpoint_base(self) -> str:
        """端点基址（凭据不在其中；健康/诊断可读，不含任何 secret）。"""
        return self._base_url

    def get(self, user_id: str) -> UserAuthorization:
        identifier = _canonical(user_id)
        if identifier is None:
            # 调用方给的不是标识：无法组成查询（契约层已保证 UUID），按基础设施故障处理，
            # 而不是返回"空授权"让上层静默放行/拒绝。
            raise AuthorizationUnavailable("the requested user id is not a UUID")
        url = f"{self._base_url}{AUTHORIZATION_PATH.format(user_id=identifier)}"
        try:
            status, body = self._opener(
                url,
                {"accept": "application/json", "authorization": self._authorization},
                self._timeout,
            )
        except OSError as error:
            raise AuthorizationUnavailable("user authorization directory is unreachable") from error
        if status != 200:
            # 非 200 一律基础设施失败：401/403 是**凭据/装配**问题，404 可能是端点缺失或用户未知，
            # 它们都不构成"这个学生没有归属"的授权结论（那是 200 + 空选课）。
            raise AuthorizationUnavailable(f"user authorization directory answered HTTP {status}")
        if len(body) > self._max_bytes:
            raise AuthorizationUnavailable("user authorization response is too large")
        return _parse_authorization(body, requested_user_id=identifier)


def _optional_uuid_text(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _canonical(value: object) -> str | None:
    return _optional_uuid_text(value)


def _parse_authorization(body: bytes, *, requested_user_id: str) -> UserAuthorization:
    """严格解析 `InternalUserAuthorization`；形状不符 ⇒ `AuthorizationUnavailable`。

    严格是刻意的：解析出一个"看起来像授权"的半成品比明确失败危险得多。响应里的 `user_id`
    必须与请求一致——否则就是问错了端点（或上游把别人的授权回给了我们），不能当成本次结论。
    """
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise AuthorizationUnavailable("user authorization returned malformed JSON") from error
    if not isinstance(document, Mapping):
        raise AuthorizationUnavailable("user authorization returned a non-object body")
    unknown = sorted(set(document) - _TOP_LEVEL_FIELDS)
    missing = sorted(_TOP_LEVEL_FIELDS - set(document))
    if unknown or missing:
        raise AuthorizationUnavailable("user authorization does not match the contract shape")
    user_id = _canonical(document.get("user_id"))
    if user_id is None or user_id != requested_user_id:
        raise AuthorizationUnavailable("user authorization does not answer the requested user")
    platform_roles = _text_tuple(document.get("platform_roles"), "platform_roles")
    grants = _uuid_tuple(document.get("course_grants"), "course_grants")
    raw_enrollments = document.get("enrollments")
    if not isinstance(raw_enrollments, list):
        raise AuthorizationUnavailable("user authorization enrollments must be an array")
    enrollments: list[Enrollment] = []
    seen_courses: set[str] = set()
    for item in raw_enrollments:
        enrollment = _parse_enrollment(item)
        if enrollment.course_id in seen_courses:
            # 同一课程最多一个有效选课是 B4 的数据库约束；出现两条说明上游数据/响应不可信，
            # 此刻无法判定"权威 section"是哪一条 —— 明确失败，绝不随便挑一条。
            raise AuthorizationUnavailable(
                "user authorization contains more than one active enrollment for a course"
            )
        seen_courses.add(enrollment.course_id)
        enrollments.append(enrollment)
    return UserAuthorization(
        user_id=user_id,
        platform_roles=platform_roles,
        course_grants=grants,
        enrollments=tuple(enrollments),
    )


def _parse_enrollment(item: object) -> Enrollment:
    if not isinstance(item, Mapping):
        raise AuthorizationUnavailable("user authorization enrollment is not an object")
    if set(item) != _ENROLLMENT_FIELDS:
        raise AuthorizationUnavailable("user authorization enrollment does not match the contract")
    course = _canonical(item.get("course_id"))
    section = _canonical(item.get("section_id"))
    class_id = _canonical(item.get("class_id"))
    if course is None or section is None or class_id is None:
        raise AuthorizationUnavailable("user authorization enrollment contains a non-UUID value")
    return Enrollment(course_id=course, section_id=section, class_id=class_id)


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AuthorizationUnavailable(f"user authorization {name} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise AuthorizationUnavailable(f"user authorization {name} contains a non-string value")
        result.append(item)
    return tuple(result)


def _uuid_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AuthorizationUnavailable(f"user authorization {name} must be an array")
    result: list[str] = []
    for item in value:
        canonical = _canonical(item)
        if canonical is None:
            raise AuthorizationUnavailable(f"user authorization {name} contains a non-UUID value")
        result.append(canonical)
    return tuple(result)


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except OSError as error:
        raise AuthorizationUnavailable("user authorization request failed") from error


__all__ = [
    "AUTHORIZATION_PATH",
    "AuthorizationUnavailable",
    "B4UserAuthorizationDirectory",
    "Enrollment",
    "UserAuthorization",
    "UserAuthorizationDirectory",
]
