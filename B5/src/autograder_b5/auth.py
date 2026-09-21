"""Local verification of B4-issued Ed25519 access tokens.

`MAIN/docs/api/governance.md` §2.4 fixes the rule B5 follows here: tokens are issued by B4
and verified **locally by every module**, never by calling B4 back per request. B5 needs
one bit from the token -- "this caller is an authenticated principal" -- so it validates the
signature and the registered claims and discards everything else. It reads no course scope,
stores no session and never sees a password, a cookie or a refresh credential.

The verification is `pyjwt` against B4's published JWKS (`GET /api/v1/auth/jwks`, keyed by
`kid`), which is the same library and the same key shape B2 uses. B5 does not re-implement
signature checking: a hand-rolled verifier is a second chance to get EdDSA wrong, and B3's
recorded incident (`docs/architecture/overview.md`) is what that costs.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

import jwt

from .config import Settings
from .errors import Unauthenticated

ALGORITHM = "EdDSA"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "exp")
KEY_SET_CACHE_SECONDS = 300

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def fetch_key_set(url: str) -> Mapping[str, Any]:
    """Read the issuer's JWKS document."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - configured issuer URL
        return json.loads(response.read().decode("utf-8"))


def _keys_for(settings: Settings) -> dict[str, Any]:
    """`kid -> public key`, cached per JWKS URL.

    Cached because the caller's token changes per request while the issuer's key set does
    not, and an uncached verifier would turn every API call into a second HTTP round trip.
    Keyed by URL so a reconfiguration is never answered from the previous issuer's keys.
    """
    now = time.time()
    with _lock:
        cached = _cache.get(settings.jwks_url)
        if cached is not None and now - cached[0] <= KEY_SET_CACHE_SECONDS:
            return cached[1]
    document = _read_key_set(settings.jwks_url)
    entries = document.get("keys")
    if not isinstance(entries, list):
        raise Unauthenticated("the issuer's key set is not a JWKS document")
    keys: dict[str, Any] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or "kid" not in entry:
            continue
        try:
            keys[str(entry["kid"])] = jwt.PyJWK.from_dict(dict(entry), algorithm=ALGORITHM)
        except (jwt.PyJWKError, ValueError, KeyError, TypeError):
            # A key B5 cannot load is not a key it can verify with; the remaining keys are
            # still usable and an unusable set fails closed below.
            continue
    if not keys:
        raise Unauthenticated("the issuer published no usable Ed25519 signing key")
    with _lock:
        _cache[settings.jwks_url] = (now, keys)
    return keys


def _read_key_set(url: str) -> Mapping[str, Any]:
    try:
        return fetch_key_set(url)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as error:
        # Fail closed: an unverifiable token is not an authenticated caller. The cause is
        # named for the operator, never for the caller's benefit.
        raise Unauthenticated(
            "the issuer's key set is unreachable, so the token cannot be verified"
        ) from error


def _bearer_token(authorization_header: str | None) -> str:
    if not authorization_header:
        raise Unauthenticated("an Authorization header with a bearer token is required")
    scheme, _, credentials = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise Unauthenticated("the Authorization header must carry a bearer token")
    return credentials.strip()


def authenticate(settings: Settings, authorization_header: str | None) -> None:
    """Verify the caller's bearer token, or raise `Unauthenticated`.

    Nothing is returned on purpose. B5's contract grants the same report to *any*
    authenticated principal, so the only thing the endpoint needs from the token is the
    proof that it is valid; extracting roles or course scope it never consults would be
    pretending to an authorization decision it does not make.
    """
    token = _bearer_token(authorization_header)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as error:
        raise Unauthenticated("the bearer token is malformed") from error
    key_id = header.get("kid")
    if not isinstance(key_id, str):
        raise Unauthenticated("the bearer token does not name a signing key")
    key = _keys_for(settings).get(key_id)
    if key is None:
        raise Unauthenticated("the bearer token names a key the issuer does not publish")
    try:
        jwt.decode(
            token,
            key.key,  # type: ignore[attr-defined]
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": list(REQUIRED_CLAIMS)},
            leeway=0,
        )
    except jwt.InvalidTokenError as error:
        raise Unauthenticated("the bearer token was rejected") from error
