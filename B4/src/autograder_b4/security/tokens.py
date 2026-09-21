"""Short-lived Ed25519 access tokens (JWT, `EdDSA`).

The token is self-contained on purpose: `B2`/`B3` verify it locally against the JWKS
and never call back into B4 on the request path (`docs/architecture/boundaries.md` §1).
That makes the claim set a security boundary, so it carries exactly what an
authorization decision needs -- issuer, audience, subject, validity window, roles and
course scope -- and nothing else. No name, number, e-mail or other PII is embedded.

Access tokens are returned only in the body of the two session operations that create
them. They never appear in a URL, a log line or an event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from .keys import SigningKey

ALGORITHM = "EdDSA"
TOKEN_TYPE = "Bearer"


class TokenError(RuntimeError):
    """Raised for a token that is missing, malformed, expired or not ours."""


@dataclass(frozen=True)
class CourseScope:
    """Authorization scope resolved server-side; never trusted from the client."""

    course_id: str
    section_id: str | None = None

    def as_claim(self) -> dict[str, str]:
        claim = {"course_id": self.course_id}
        if self.section_id is not None:
            claim["section_id"] = self.section_id
        return claim

    @classmethod
    def from_claim(cls, claim: dict[str, object]) -> CourseScope:
        course_id = claim.get("course_id")
        section_id = claim.get("section_id")
        if not isinstance(course_id, str):
            raise TokenError("scope entry is missing course_id")
        if section_id is not None and not isinstance(section_id, str):
            raise TokenError("scope entry has a non-string section_id")
        return cls(course_id=course_id, section_id=section_id)


@dataclass(frozen=True)
class AccessTokenClaims:
    subject: str
    roles: tuple[str, ...]
    scope: tuple[CourseScope, ...]
    token_id: str
    issued_at: datetime
    expires_at: datetime
    issuer: str
    audience: str


def issue_access_token(
    *,
    signing_key: SigningKey,
    subject: str,
    roles: tuple[str, ...],
    scope: tuple[CourseScope, ...],
    issuer: str,
    audience: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> tuple[str, AccessTokenClaims]:
    """Sign a short-lived access token and return it with its decoded claims.

    The claims are returned alongside the compact token so the caller can render the
    login response (`roles`, `scope`, `expires_in`) without decoding its own output.
    """
    if not signing_key.can_sign():
        raise TokenError("no private key is configured; cannot issue access tokens")
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    claims = AccessTokenClaims(
        subject=subject,
        roles=roles,
        scope=scope,
        token_id=str(uuid.uuid4()),
        issued_at=issued_at,
        expires_at=expires_at,
        issuer=issuer,
        audience=audience,
    )
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "jti": claims.token_id,
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "role": list(roles),
        "scope": [entry.as_claim() for entry in scope],
    }
    token = jwt.encode(
        payload, signing_key.private_key, algorithm=ALGORITHM, headers={"kid": signing_key.kid}
    )
    return token, claims


def verify_access_token(
    token: str,
    *,
    signing_key: SigningKey,
    issuer: str,
    audience: str,
    now: datetime | None = None,
) -> AccessTokenClaims:
    """Verify signature, issuer, audience and validity window; then decode the claims."""
    try:
        payload = jwt.decode(
            token,
            signing_key.public_key,
            algorithms=[ALGORITHM],
            issuer=issuer,
            audience=audience,
            options={"require": ["iss", "aud", "sub", "jti", "iat", "nbf", "exp"]},
            leeway=0,
        )
    except jwt.ExpiredSignatureError as error:
        raise TokenError("access token has expired") from error
    except jwt.InvalidTokenError as error:
        raise TokenError(f"access token rejected: {error}") from error

    subject = payload.get("sub")
    token_id = payload.get("jti")
    if not isinstance(subject, str) or not isinstance(token_id, str):
        raise TokenError("access token is missing sub or jti")
    roles = payload.get("role", [])
    if not isinstance(roles, list) or not all(isinstance(item, str) for item in roles):
        raise TokenError("access token role claim must be a list of strings")
    raw_scope = payload.get("scope", [])
    if not isinstance(raw_scope, list) or not all(isinstance(item, dict) for item in raw_scope):
        raise TokenError("access token scope claim must be a list of objects")
    return AccessTokenClaims(
        subject=subject,
        roles=tuple(roles),
        scope=tuple(CourseScope.from_claim(item) for item in raw_scope),
        token_id=token_id,
        issued_at=datetime.fromtimestamp(int(payload["iat"]), UTC),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), UTC),
        issuer=str(payload["iss"]),
        audience=str(payload["aud"]),
    )


def decode_without_verification(token: str) -> dict[str, object]:
    """Read the `jti` of a token whose signature was already verified.

    Only used by logout, where the caller has already authenticated the request.
    """
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError as error:  # pragma: no cover - defensive
        raise TokenError(f"access token is unreadable: {error}") from error
