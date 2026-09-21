"""Authentication and authorization.

B3 trusts exactly one source of identity: a JWT signed by B4 and verified locally
against B4's JWKS public keys (``MAIN/contracts/openapi/b4.yaml`` ->
``GET /api/v1/auth/jwks``). The token carries ``role`` (a list of role strings),
``scope`` (``[{course_id, section_id?}]``) and the standard registered claims. No
symmetric secret is shared, nothing is read from the ``b4`` schema, and the client is
never trusted for scope.

Authorization outcomes follow ``MAIN/docs/api/identifiers.md`` §3 and are asserted
by TC-QST-03:

* missing/invalid credentials -> ``401``;
* authenticated but outside the course scope -> ``403`` (never masked as ``404``);
* in scope but the resource does not exist -> ``404``.

The cryptography is real (Ed25519 / EdDSA via ``cryptography``); the JWKS *endpoint*
is the only thing tests replace, which ``docs/testing/strategy.md`` allows.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from collections.abc import Callable, Iterable, Mapping
from uuid import UUID

from .errors import Forbidden, Unauthenticated

ROLES = frozenset({"student", "teacher", "assistant", "admin"})
READ_ROLES = frozenset({"student", "teacher", "assistant", "admin"})
WRITE_ROLES = frozenset({"teacher", "admin"})
STATISTICS_ROLES = frozenset({"teacher", "assistant", "admin"})
CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str] = frozenset()
    course_ids: frozenset[UUID] = frozenset()
    section_ids: frozenset[UUID] = frozenset()
    is_service: bool = False
    token_id: str | None = None

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def in_course(self, course_id: UUID) -> bool:
        return self.is_admin or course_id in self.course_ids

    def in_section(self, section_id: UUID) -> bool:
        return self.is_admin or section_id in self.section_ids

    def readable_courses(self) -> frozenset[UUID] | None:
        """``None`` means "no course restriction" (admin)."""
        return None if self.is_admin else self.course_ids


SERVICE_PRINCIPAL = Principal(subject="service:B2", is_service=True, roles=frozenset({"admin"}))

#: Roles allowed to ask for authoring advice (``RQ-T-10``, ``CHG-0011``): the people who
#: write questions. A student is not refused for lack of scope but for lack of the role,
#: and that check runs before any configuration is inspected.
AUTHORING_ROLES = frozenset({"teacher", "assistant", "admin"})


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


# --- scope policy ---------------------------------------------------------


def require_authenticated(principal: Principal | None) -> Principal:
    if principal is None:
        raise Unauthenticated("a bearer token is required")
    return principal


def require_course_read(principal: Principal | None, course_id: UUID) -> Principal:
    actor = require_authenticated(principal)
    if actor.is_service:
        return actor
    if not actor.has_role(*READ_ROLES):
        raise Forbidden("the caller's role may not read question banks")
    if not actor.in_course(course_id):
        raise Forbidden("the requested course is outside the caller's authorization scope")
    return actor


def require_course_write(principal: Principal | None, course_id: UUID) -> Principal:
    actor = require_authenticated(principal)
    if not actor.has_role(*WRITE_ROLES):
        raise Forbidden("only a teacher or administrator may modify the question bank")
    if not actor.in_course(course_id):
        raise Forbidden("the requested course is outside the caller's authorization scope")
    return actor


def require_statistics_read(principal: Principal | None, course_id: UUID) -> Principal:
    actor = require_authenticated(principal)
    if actor.is_service:
        return actor
    if not actor.has_role(*STATISTICS_ROLES):
        raise Forbidden("question statistics require a teacher or assistant role")
    if not actor.in_course(course_id):
        raise Forbidden("the requested course is outside the caller's authorization scope")
    return actor


def require_service(principal: Principal | None) -> Principal:
    actor = require_authenticated(principal)
    if not actor.is_service:
        raise Forbidden(
            "this endpoint is restricted to service-to-service callers; "
            "internal is not a substitute for authorization"
        )
    return actor


def require_authoring_reader(principal: Principal | None) -> Principal:
    """Who may ask for authoring advice, before any scope or config is looked at.

    Course scope needs the question's ``course_id``, which is a database read; the role
    check does not, so it comes first. That ordering is what keeps "is a model provider
    configured here" from being readable by a caller who may not use the operation.
    """
    actor = require_authenticated(principal)
    if actor.is_service or actor.has_role(*AUTHORING_ROLES):
        return actor
    raise Forbidden("only a teacher or assistant may request authoring assistance")


# --- JWT / JWKS -----------------------------------------------------------


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


@dataclass
class JwksTokenVerifier:
    """EdDSA (Ed25519) JWT verification against B4's JWKS.

    EdDSA is the algorithm the issuer actually uses: ``b4.yaml`` declares short-lived
    Ed25519 tokens, ``autograder_b4.security.tokens.ALGORITHM == "EdDSA"``, and
    ``autograder_b2.auth.tokens.ALGORITHM == "EdDSA"``. This verifier previously
    implemented RS256 and selected keys with ``kty == "RSA"``, so it silently loaded
    **zero** keys from B4's JWKS (which publishes ``kty: OKP``) and rejected every
    valid token. B3's own suite did not catch it because the tests generated their own
    RSA key pair -- a test that agrees with itself proves nothing about the issuer.
    Integration caught it (host bring-up, 2026-09-16).

    ``fetch_jwks`` is injectable so a test can serve a locally generated key pair
    without a network call; the verification logic itself is never stubbed.
    """

    jwks_url: str
    issuer: str = "autograder"
    audience: str = "autograder-api"
    clock: Callable[[], float] = time.time
    cache_seconds: int = 300
    fetch_jwks: Callable[[str], Mapping[str, Any]] | None = None
    _keys: Mapping[str, Any] = field(default_factory=dict, init=False, repr=False)
    _fetched_at: float = field(default=0.0, init=False, repr=False)

    def _keys_for(self) -> Mapping[str, Any]:
        now = self.clock()
        if not self._keys or now - self._fetched_at > self.cache_seconds:
            fetcher = self.fetch_jwks or _http_fetch_jwks
            document = fetcher(self.jwks_url)
            keys: dict[str, Any] = {}
            for entry in document.get("keys", []):
                if (
                    entry.get("kty") == "OKP"
                    and entry.get("crv") == "Ed25519"
                    and entry.get("kid")
                ):
                    keys[str(entry["kid"])] = entry
            self._keys = keys
            self._fetched_at = now
        return self._keys

    def verify(self, token: str) -> Principal:
        parts = token.split(".")
        if len(parts) != 3:
            raise Unauthenticated("malformed bearer token")
        header_segment, payload_segment, signature_segment = parts
        try:
            header = json.loads(_b64url_decode(header_segment))
            claims = json.loads(_b64url_decode(payload_segment))
            signature = _b64url_decode(signature_segment)
        except (ValueError, json.JSONDecodeError) as exc:
            raise Unauthenticated("malformed bearer token") from exc

        algorithm = header.get("alg")
        if algorithm != "EdDSA":
            raise Unauthenticated("unsupported token algorithm")
        key = self._keys_for().get(str(header.get("kid")))
        if key is None:
            raise Unauthenticated("token signing key is not published by the issuer")
        if not _verify_eddsa(
            key, f"{header_segment}.{payload_segment}".encode("ascii"), signature
        ):
            raise Unauthenticated("token signature verification failed")

        self._check_registered_claims(claims)
        roles = _roles_claim(claims)
        unknown = set(roles) - ROLES
        if unknown:
            raise Unauthenticated("token carries an unknown role")

        course_ids, section_ids = _parse_scope(claims.get("scope"))
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise Unauthenticated("token subject is missing")
        return Principal(
            subject=subject,
            roles=frozenset(roles),
            course_ids=course_ids,
            section_ids=section_ids,
            token_id=claims.get("jti") if isinstance(claims.get("jti"), str) else None,
        )

    def _check_registered_claims(self, claims: Mapping[str, Any]) -> None:
        now = self.clock()
        if claims.get("iss") != self.issuer:
            raise Unauthenticated("token issuer is not accepted")
        audience = claims.get("aud")
        if isinstance(audience, list):
            accepted = self.audience in audience
        else:
            accepted = audience == self.audience
        if not accepted:
            raise Unauthenticated("token audience is not accepted")
        expires_at = claims.get("exp")
        if not isinstance(expires_at, (int, float)):
            raise Unauthenticated("token has no expiry")
        if float(expires_at) + CLOCK_SKEW_SECONDS < now:
            raise Unauthenticated("token has expired")
        not_before = claims.get("nbf")
        if isinstance(not_before, (int, float)) and float(not_before) - CLOCK_SKEW_SECONDS > now:
            raise Unauthenticated("token is not valid yet")


def _parse_scope(raw: Any) -> tuple[frozenset[UUID], frozenset[UUID]]:
    course_ids: set[UUID] = set()
    section_ids: set[UUID] = set()
    if raw is None:
        return frozenset(), frozenset()
    if not isinstance(raw, list):
        raise Unauthenticated("token scope claim is malformed")
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise Unauthenticated("token scope claim is malformed")
        course_id = entry.get("course_id")
        if course_id is not None:
            try:
                course_ids.add(UUID(str(course_id)))
            except ValueError as exc:
                raise Unauthenticated("token scope carries a malformed course_id") from exc
        section_id = entry.get("section_id")
        if section_id is not None:
            try:
                section_ids.add(UUID(str(section_id)))
            except ValueError as exc:
                raise Unauthenticated("token scope carries a malformed section_id") from exc
    return frozenset(course_ids), frozenset(section_ids)


def _roles_claim(claims: Mapping[str, Any]) -> list[str]:
    """Read the issuer's role claim.

    B4 publishes the claim as ``role`` -- **a list of strings** -- and issues no
    ``roles`` claim at all (``autograder_b4.security.tokens.issue_access_token``).
    ``autograder_b2.auth.tokens`` reads the same shape, so it is the agreed form.
    A bare string is still accepted because the contract fix that pins the claim name
    is recent; a malformed value is rejected rather than coerced to "no roles", which
    would turn a token bug into a silent authorization downgrade.
    """
    raw = claims.get("roles", claims.get("role"))
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return list(raw)
    raise Unauthenticated("token role claim is malformed")


def _verify_eddsa(key: Mapping[str, Any], signing_input: bytes, signature: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            _b64url_decode(str(key["x"]))
        )
        public_key.verify(signature, signing_input)
    except (KeyError, ValueError, InvalidSignature):
        return False
    return True


def _http_fetch_jwks(url: str) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - configured issuer URL
        return json.loads(response.read().decode("utf-8"))


@dataclass
class ServiceTokenVerifier:
    """Service-to-service bearer credential (B2 -> B3 internal package endpoint)."""

    service_token: str

    def matches(self, token: str) -> bool:
        return hmac.compare_digest(token, self.service_token)


@dataclass
class StaticTokenVerifier:
    """Test verifier standing in for B4's JWKS endpoint (allowed mock)."""

    claims_by_token: Mapping[str, Mapping[str, Any]]
    _verifier: JwksTokenVerifier | None = None

    def verify(self, token: str) -> Principal:
        claims = self.claims_by_token.get(token)
        if claims is None:
            raise Unauthenticated("unknown test token")
        roles = claims.get("roles", [])
        course_ids, section_ids = _parse_scope(claims.get("scope"))
        subject = claims.get("sub", "test-subject")
        return Principal(
            subject=str(subject),
            roles=frozenset(roles),
            course_ids=course_ids,
            section_ids=section_ids,
            is_service=bool(claims.get("service", False)),
        )


def parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


def all_roles() -> Iterable[str]:
    return sorted(ROLES)
