"""Ed25519 signing keys and the JWKS document derived from them.

The private key lives in a file outside version control and is referenced by
`JWT_PRIVATE_KEY_FILE`; it is read once, never logged, and never placed in an event or
response. Other modules verify tokens locally against `GET /api/v1/auth/jwks`, so the
public half must be derivable at any time without the private half being touched again.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class KeyConfigurationError(RuntimeError):
    """Raised when the signing key is missing, unreadable or of the wrong type."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def public_key_thumbprint(public_key: Ed25519PublicKey) -> str:
    """Stable `kid`: SHA-256 over the raw public key bytes, base64url encoded."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return _b64url(hashlib.sha256(raw).digest())


@dataclass(frozen=True)
class SigningKey:
    """An Ed25519 key pair, or a public-only key when no private half is configured."""

    private_key: Ed25519PrivateKey | None
    public_key: Ed25519PublicKey

    @property
    def kid(self) -> str:
        return public_key_thumbprint(self.public_key)

    @property
    def algorithm(self) -> str:
        return "EdDSA"

    def can_sign(self) -> bool:
        return self.private_key is not None

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        """The public JWK Set, in the shape `MAIN/contracts/openapi/b4.yaml` declares."""
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        return {
            "keys": [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": self.kid,
                    "x": _b64url(raw),
                    "use": "sig",
                    "alg": "EdDSA",
                }
            ]
        }


def load_signing_key(path: Path | None) -> SigningKey:
    """Load the signing key from a PEM file; raise when it is unusable.

    A public-only PEM is accepted so that read-only replicas can publish JWKS, but
    token issuance then fails loudly instead of signing with a substitute key.
    """
    if path is None:
        raise KeyConfigurationError(
            "JWT_PRIVATE_KEY_FILE is not configured; run `python -m autograder_b4.ops.generate_key`"
        )
    if not path.is_file():
        raise KeyConfigurationError(f"JWT_PRIVATE_KEY_FILE does not exist: {path}")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise KeyConfigurationError(f"JWT_PRIVATE_KEY_FILE is unreadable: {error}") from error
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        loaded = serialization.load_pem_public_key(data)
        if not isinstance(loaded, Ed25519PublicKey):
            raise KeyConfigurationError("JWT_PRIVATE_KEY_FILE is not an Ed25519 key") from None
        return SigningKey(private_key=None, public_key=loaded)
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyConfigurationError("JWT_PRIVATE_KEY_FILE must hold an Ed25519 private key")
    return SigningKey(private_key=key, public_key=key.public_key())


def generate_signing_key(path: Path) -> SigningKey:
    """Create a new Ed25519 key pair on disk with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise KeyConfigurationError(f"refusing to overwrite an existing key: {path}")
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Write with restrictive permissions from the first byte: the file must never be
    # group/world readable, not even transiently.
    descriptor = _open_private(path)
    try:
        # `os.fdopen` wraps the descriptor opened with owner-only mode; opening by path
        # again would re-apply the umask and could widen the permissions.
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(pem)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return SigningKey(private_key=private_key, public_key=private_key.public_key())


def _open_private(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    return os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
