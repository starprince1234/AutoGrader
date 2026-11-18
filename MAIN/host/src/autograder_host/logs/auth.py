"""JWT verification and role/course-scope authorization for ops log endpoints."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from fastapi import Header
from fastapi.responses import JSONResponse

from .context import normalize_uuid

ALGORITHM = "EdDSA"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "exp")
KEY_SET_CACHE_SECONDS = 300

_lock = threading.Lock()
_jwks_cache: tuple[float, dict[str, Any]] | None = None


class OpsLogAuthError(Exception):
    """RFC 9457 error raised by ops log auth/validation."""

    def __init__(self, status: int, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail

    def to_response(self, instance: str) -> JSONResponse:
        return JSONResponse(
            status_code=self.status,
            content={
                "type": f"https://autograder.local/problems/{self.title.lower().replace(' ', '-')}",
                "title": self.title,
                "status": self.status,
                "detail": self.detail,
                "instance": instance,
            },
            media_type="application/problem+json",
        )


@dataclass(frozen=True)
class LogPrincipal:
    user_id: str
    roles: tuple[str, ...]
    scoped_course_ids: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def is_teacher(self) -> bool:
        return "teacher" in self.roles and len(self.scoped_course_ids) > 0

    @property
    def can_read_logs(self) -> bool:
        return self.is_admin or self.is_teacher


def _read_jwks_document() -> Mapping[str, Any]:
    jwks_path = os.environ.get("B2_JWKS_PATH", "").strip()
    if jwks_path:
        path = Path(jwks_path)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    jwks_url = os.environ.get("B4_JWKS_URL", "").strip()
    if jwks_url:
        req = urllib.request.Request(jwks_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    raise OpsLogAuthError(401, "Unauthorized", "JWKS key set is not configured or unreachable.")


def _load_keys() -> dict[str, Any]:
    now = time.time()
    with _lock:
        if _jwks_cache is not None and now - _jwks_cache[0] <= KEY_SET_CACHE_SECONDS:
            return _jwks_cache[1]
    try:
        doc = _read_jwks_document()
    except OpsLogAuthError:
        raise
    except Exception as error:
        raise OpsLogAuthError(401, "Unauthorized", "Unable to read issuer JWKS key set.") from error
    entries = doc.get("keys")
    if not isinstance(entries, list):
        raise OpsLogAuthError(401, "Unauthorized", "Issuer JWKS document is malformed.")
    keys: dict[str, Any] = {}
    for entry in entries:
        if isinstance(entry, Mapping) and "kid" in entry:
            try:
                keys[str(entry["kid"])] = jwt.PyJWK.from_dict(dict(entry), algorithm=ALGORITHM)
            except Exception:
                continue
    if not keys:
        raise OpsLogAuthError(401, "Unauthorized", "No usable Ed25519 signing key in JWKS.")
    with _lock:
        globals()["_jwks_cache"] = (now, keys)
    return keys


def clear_jwks_cache_for_tests() -> None:
    with _lock:
        globals()["_jwks_cache"] = None


def verify_bearer_token(authorization: str | None) -> LogPrincipal:
    if not authorization:
        raise OpsLogAuthError(401, "Unauthorized", "Authorization bearer token is required.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise OpsLogAuthError(401, "Unauthorized", "Authorization header must be Bearer <token>.")
    token = token.strip()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as error:
        raise OpsLogAuthError(401, "Unauthorized", "Bearer token is malformed.") from error
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise OpsLogAuthError(401, "Unauthorized", "Bearer token is missing kid header.")
    key = _load_keys().get(kid)
    if key is None:
        clear_jwks_cache_for_tests()
        key = _load_keys().get(kid)
    if key is None:
        raise OpsLogAuthError(401, "Unauthorized", "Bearer token signing key is unknown.")
    issuer = os.environ.get("B4_JWT_ISSUER", "autograder").strip() or "autograder"
    audience = os.environ.get("B4_JWT_AUDIENCE", "autograder-api").strip() or "autograder-api"
    try:
        payload = jwt.decode(
            token,
            key.key,  # type: ignore[attr-defined]
            algorithms=[ALGORITHM],
            issuer=issuer,
            audience=audience,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.InvalidTokenError as error:
        raise OpsLogAuthError(401, "Unauthorized", "Bearer token verification failed.") from error

    user_id = normalize_uuid(payload.get("sub"))
    if user_id is None:
        raise OpsLogAuthError(401, "Unauthorized", "Bearer token sub claim is invalid.")
    raw_roles = payload.get("role", [])
    roles = tuple(str(r) for r in raw_roles) if isinstance(raw_roles, list) else ()
    raw_scope = payload.get("scope", [])
    courses: set[str] = set()
    if isinstance(raw_scope, list):
        for entry in raw_scope:
            if isinstance(entry, Mapping):
                cid = normalize_uuid(entry.get("course_id"))
                if cid is not None:
                    courses.add(cid)
    return LogPrincipal(
        user_id=user_id,
        roles=roles,
        scoped_course_ids=frozenset(courses),
    )


def require_authenticated_principal(
    authorization: str | None = Header(default=None),
) -> LogPrincipal:
    return verify_bearer_token(authorization)


def require_ops_log_principal(
    authorization: str | None = Header(default=None),
) -> LogPrincipal:
    principal = verify_bearer_token(authorization)
    if not principal.can_read_logs:
        raise OpsLogAuthError(
            403,
            "Forbidden",
            "Operational logs are restricted to administrators and course teachers.",
        )
    return principal
