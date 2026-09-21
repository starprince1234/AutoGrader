"""Content-addressed asset/fixture storage helpers.

Two responsibilities:

1. **Digest authority.** Digests are always computed here from the bytes B3
   received. A digest supplied by a client is only ever compared against the
   computed value (``RQ-B-3-10`` acceptance 1, ``RQ-B-3-50`` acceptance 2).
2. **Upload validation.** Content type allow-listing, magic-number check and size
   bounds for stem graphics - the only bytes B3 ever serves back to a browser.
   SVG is refused because it can carry script and would then run on B3's origin.
"""

from __future__ import annotations

import base64
import binascii

from ..config import (
    ALLOWED_ASSET_CONTENT_TYPES,
    ASSET_MAGIC_PREFIXES,
    Settings,
)
from ..compile.canonical import sha256_hex
from ..errors import AssetRejected, PayloadTooLarge
from ..logging import REDACTED


def decode_base64(payload: str, *, field: str = "content_base64") -> bytes:
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        # The message deliberately does not echo the payload.
        raise AssetRejected(f"{field} is not valid base64") from exc


def validated_asset_bytes(
    *, content_type: str, content: bytes, settings: Settings
) -> tuple[str, str]:
    """Validate a stem graphic and return ``(content_type, sha256)``."""
    if content_type not in ALLOWED_ASSET_CONTENT_TYPES:
        raise AssetRejected(
            "stem graphics must be one of "
            + ", ".join(sorted(ALLOWED_ASSET_CONTENT_TYPES))
            + " (SVG is refused: it can execute script on the API origin)"
        )
    if not content:
        raise AssetRejected("stem graphic is empty")
    if len(content) > settings.max_asset_bytes:
        raise PayloadTooLarge(
            f"stem graphic exceeds the {settings.max_asset_bytes} byte limit"
        )
    prefixes = ASSET_MAGIC_PREFIXES.get(content_type, ())
    if prefixes and not any(content.startswith(prefix) for prefix in prefixes):
        raise AssetRejected("stem graphic bytes do not match the declared content type")
    return content_type, sha256_hex(content)


def validated_fixture_bytes(*, content: bytes, settings: Settings) -> tuple[str, int]:
    """Validate fixture bytes and return ``(sha256, size)``."""
    if len(content) > settings.max_fixture_bytes:
        raise PayloadTooLarge(
            f"fixture exceeds the {settings.max_fixture_bytes} byte limit"
        )
    return sha256_hex(content), len(content)


def redacted_content_marker() -> str:
    """Placeholder used when protected bytes must be referenced in a log field."""
    return REDACTED
