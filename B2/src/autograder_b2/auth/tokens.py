"""B2 本地 JWT 验签（B4 签发的 Ed25519 短期令牌；**不回调 B4**）。

claim 形态**不是** B2 自创的：它与 B4 的实现逐字段对齐（B4 是身份权威）
`B4/src/autograder_b4/security/tokens.py` + `security/keys.py`：

- 算法 `EdDSA`（Ed25519），header 带 `kid`；
- claims：`iss`、`aud`、`sub`、`jti`、`iat`、`nbf`、`exp`、`role`（字符串数组）、
  `scope`（`[{course_id, section_id?}]` 数组）；
- JWKS：`{"keys":[{"kty":"OKP","crv":"Ed25519","kid":...,"x":...,"use":"sig","alg":"EdDSA"}]}`，
  `kid` 为公钥原始字节 SHA-256 的 base64url。

本模块**只做验签与声明解析**，角色/作用域判定在 `authorization`；不 import B4 的任何代码。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import jwt

ALGORITHM = "EdDSA"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "jti", "iat", "nbf", "exp")
ROLES = ("student", "teacher", "assistant", "admin")
DEFAULT_ISSUER = "autograder-b4"
DEFAULT_AUDIENCE = "autograder"


class AuthenticationError(Exception):
    """令牌缺失、格式错误、签名不符、过期或不属于本系统 → 对外 401（不透露主体是否存在）。"""


@dataclass(frozen=True, slots=True)
class CourseScope:
    """令牌中的课程作用域条目（服务端解析结果，绝不从客户端信任）。"""

    course_id: str
    section_id: str | None = None

    def covers(self, course_id: str, section_id: str | None) -> bool:
        """课程必须相符；作用域条目未限定课序时视为该课程全域（教师/助教）。"""
        if self.course_id != course_id:
            return False
        if self.section_id is None:
            return True
        return section_id is None or self.section_id == section_id


@dataclass(frozen=True, slots=True)
class Principal:
    """验签后的调用者身份：只有身份与授权判定所需的最小信息，无 PII。"""

    user_id: str
    roles: tuple[str, ...]
    scopes: tuple[CourseScope, ...]
    token_id: str

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def is_course_staff(self) -> bool:
        return bool({"teacher", "assistant"} & set(self.roles))

    @property
    def has_recognized_role(self) -> bool:
        """是否持有至少一个**已登记**的平台角色。

        `identifiers.md` §3 要求"认证 / 角色 / 课程作用域"三层同时成立：一个没有任何已登记角色的
        令牌（`role: []`、`["guest"]`）只完成了认证，**不构成**任何授权依据——包括读自己的数据。
        未知角色一律不授予权限（向前兼容：将来新增角色只需扩展 `ROLES`）。
        """
        return bool(set(self.roles) & set(ROLES))

    def covers_course(self, course_id: str, section_id: str | None) -> bool:
        return any(scope.covers(course_id, section_id) for scope in self.scopes)


class JwksAuthenticator:
    """按 `kid` 从 JWKS 取出公钥并本地验签；私钥与 B4 均不参与请求路径。"""

    def __init__(
        self,
        jwks: Mapping[str, object],
        *,
        issuer: str = DEFAULT_ISSUER,
        audience: str = DEFAULT_AUDIENCE,
    ) -> None:
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError("JWKS must contain a non-empty 'keys' array")
        self._keys: dict[str, object] = {}
        for entry in keys:
            if not isinstance(entry, Mapping) or "kid" not in entry:
                raise ValueError("each JWK must be an object carrying 'kid'")
            kid = str(entry["kid"])
            self._keys[kid] = jwt.PyJWK.from_dict(dict(entry), algorithm=ALGORITHM)
        self._issuer = issuer
        self._audience = audience

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._keys))

    def authenticate(self, authorization_header: str | None) -> Principal:
        """验签并解析为 `Principal`；任何失败都抛 `AuthenticationError`（→ 401）。"""
        token = self._extract_token(authorization_header)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as error:
            raise AuthenticationError("malformed bearer token") from error
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            raise AuthenticationError("unknown signing key")
        try:
            payload = jwt.decode(
                token,
                self._keys[kid].key,  # type: ignore[attr-defined]
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": list(REQUIRED_CLAIMS)},
                leeway=0,
            )
        except jwt.InvalidTokenError as error:
            raise AuthenticationError("access token rejected") from error
        return _principal_from_payload(payload)

    @staticmethod
    def _extract_token(authorization_header: str | None) -> str:
        if not authorization_header:
            raise AuthenticationError("missing Authorization header")
        scheme, _, credentials = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not credentials.strip():
            raise AuthenticationError("Authorization header must be a Bearer token")
        return credentials.strip()


def _principal_from_payload(payload: Mapping[str, object]) -> Principal:
    subject = payload.get("sub")
    token_id = payload.get("jti")
    if not isinstance(subject, str) or not isinstance(token_id, str):
        raise AuthenticationError("access token is missing sub or jti")
    roles = payload.get("role", [])
    if not isinstance(roles, list) or not all(isinstance(item, str) for item in roles):
        raise AuthenticationError("role claim must be a list of strings")
    raw_scope = payload.get("scope", [])
    if not isinstance(raw_scope, list) or not all(isinstance(item, Mapping) for item in raw_scope):
        raise AuthenticationError("scope claim must be a list of objects")
    scopes: list[CourseScope] = []
    for entry in raw_scope:
        scopes.append(
            CourseScope(
                course_id=_canonical_uuid(entry.get("course_id"), "scope.course_id"),
                section_id=(
                    None
                    if entry.get("section_id") is None
                    else _canonical_uuid(entry.get("section_id"), "scope.section_id")
                ),
            )
        )
    return Principal(
        user_id=_canonical_uuid(subject, "sub"),
        roles=tuple(roles),
        scopes=tuple(scopes),
        token_id=token_id,
    )


def _canonical_uuid(value: object, field: str) -> str:
    """把标识规范化为小写连字符形式。

    为什么必须规范化：契约的 `Uuid` 是 `format: uuid`，大写、花括号、`urn:uuid:`、无连字符的
    32 位十六进制**都是合法写法**；而授权判定是字符串比较。不规范化会让一份合规令牌因为大小写
    差异而被拒（fail-closed 但错误）。规范化后同一标识只有一种内部表示。
    """
    if not isinstance(value, str):
        raise AuthenticationError(f"{field} must be a UUID string")
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise AuthenticationError(f"{field} is not a UUID") from error
