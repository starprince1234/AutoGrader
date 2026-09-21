"""Refresh credentials: opaque 256-bit secrets that live only in an HttpOnly cookie.

The value is generated here, returned to the caller exactly once (to be placed in the
`Set-Cookie` header), and stored server-side only as a SHA-256 digest -- a database
dump therefore yields no usable refresh credential. Rotation is unconditional on
refresh, and reuse of an already-rotated token revokes the whole session family so a
stolen token cannot outlive the legitimate client's next refresh.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def generate_refresh_token() -> str:
    """A URL-safe, high-entropy opaque refresh credential."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """Digest stored in `b4.sessions.refresh_token_hash`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_matches(candidate: str, stored_digest: str) -> bool:
    """Constant-time comparison of a presented credential against the stored digest."""
    return hmac.compare_digest(hash_refresh_token(candidate), stored_digest)


def cookie_attributes(*, name: str, value: str, max_age: int, secure: bool, path: str) -> str:
    """Build the Set-Cookie value for the refresh credential.

    `HttpOnly` keeps it away from JavaScript, `SameSite=Strict` blocks cross-site
    replay, and `Secure` is mandatory outside local development. The credential is
    never exposed in the response body.
    """
    attributes = [
        f"{name}={value}",
        f"Path={path}",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Strict",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def expired_cookie(name: str, path: str, *, secure: bool) -> str:
    """Set-Cookie value that clears the refresh credential on logout."""
    return cookie_attributes(name=name, value="", max_age=0, secure=secure, path=path)
