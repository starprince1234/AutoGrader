"""测试用的 B4 权威授权目录替身（**端口替身**，不是生产降级路径）。

生产实现是 `integration.b4_authorization.B4UserAuthorizationDirectory`（HTTP + 服务令牌）。
测试替换的只是**传输**：提交/挑战/邮件/成绩各入口对"当前有效归属"的判定语义走真实服务代码。

替身建模的是 B4 契约会返回的事实：
- 一个用户可以有平台角色（`student`/`teacher`/`admin`）、课程级 staff grant 与当前有效选课；
- 未登记的用户等价于"存在但没有任何角色与选课"（B4 对停用账号也返回空角色/空选课）；
- `unavailable=True` 模拟 B4 不可达：`get` 抛 `AuthorizationUnavailable`（调用方必须 503/重试，
  绝不当成"没有归属"）。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace

from autograder_b2.integration.b4_authorization import (
    AuthorizationUnavailable,
    Enrollment,
    UserAuthorization,
)


def _uuid(value: object) -> str:
    return str(uuid.UUID(str(value)))


def enrollment(
    course_id: object, section_id: object, class_id: object | None = None
) -> Enrollment:
    """构造一条选课事实（`class_id` 缺省时生成一个确定性无关的 UUID）。"""
    return Enrollment(
        course_id=_uuid(course_id),
        section_id=_uuid(section_id),
        class_id=_uuid(class_id or uuid.uuid4()),
    )


class FakeAuthorizationDirectory:
    """内存版 `UserAuthorizationDirectory`：测试按需设置每个用户的角色/授权/选课。"""

    def __init__(self) -> None:
        self._users: dict[str, UserAuthorization] = {}
        self.unavailable = False
        self.calls: list[str] = []

    # --- 设置事实 -----------------------------------------------------------------

    def enroll(
        self,
        user_id: object,
        *,
        course_id: object,
        section_id: object,
        class_id: object | None = None,
        roles: Sequence[str] = ("student",),
        course_grants: Sequence[object] = (),
    ) -> None:
        """登记/更新一个用户的选课（同一课程只保留一条，与 B4 的数据库约束一致）。"""
        canonical = _uuid(user_id)
        existing = self._users.get(canonical) or UserAuthorization(user_id=canonical)
        kept = tuple(item for item in existing.enrollments if item.course_id != _uuid(course_id))
        self._users[canonical] = UserAuthorization(
            user_id=canonical,
            platform_roles=tuple(dict.fromkeys((*existing.platform_roles, *roles))),
            course_grants=tuple(
                dict.fromkeys((*existing.course_grants, *(_uuid(grant) for grant in course_grants)))
            ),
            enrollments=(*kept, enrollment(course_id, section_id, class_id)),
        )

    def unenroll(self, user_id: object, course_id: object) -> None:
        """移除一个用户在某门课的当前选课（模拟"被移出课程"）。"""
        canonical = _uuid(user_id)
        existing = self._users.get(canonical)
        if existing is None:
            return
        self._users[canonical] = replace(
            existing,
            enrollments=tuple(
                item for item in existing.enrollments if item.course_id != _uuid(course_id)
            ),
        )

    def grant_staff(
        self,
        user_id: object,
        *,
        course_id: object,
        role: str = "teacher",
    ) -> None:
        """授予某门课的 staff 权限（平台角色 + 该课程 grant），不附带学生选课。"""
        canonical = _uuid(user_id)
        existing = self._users.get(canonical) or UserAuthorization(user_id=canonical)
        self._users[canonical] = UserAuthorization(
            user_id=canonical,
            platform_roles=tuple(dict.fromkeys((*existing.platform_roles, role))),
            course_grants=tuple(
                dict.fromkeys((*existing.course_grants, _uuid(course_id)))
            ),
            enrollments=existing.enrollments,
        )

    def set_unavailable(self, unavailable: bool = True) -> None:
        self.unavailable = unavailable

    # --- 端口 -----------------------------------------------------------------------

    def get(self, user_id: str) -> UserAuthorization:
        self.calls.append(str(user_id))
        if self.unavailable:
            raise AuthorizationUnavailable("fake B4 authorization directory is unreachable")
        try:
            canonical = _uuid(user_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise AuthorizationUnavailable("the requested user id is not a UUID") from error
        return self._users.get(canonical, UserAuthorization(user_id=canonical))


__all__ = ["FakeAuthorizationDirectory", "enrollment"]
