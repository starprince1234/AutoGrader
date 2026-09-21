"""Derive the public JWKS document from B4's Ed25519 signing key.

    python MAIN/host/write_jwks.py <private-key.pem> <jwks.json>

Why this exists: B2 verifies access tokens locally and takes the public key set from
`B2_JWKS_PATH` (a file) or `B2_JWKS_JSON` (inline). B4 publishes the same document at
`GET /api/v1/auth/jwks`, but B2 is wired while the host is still constructing the
application, so it cannot fetch from an endpoint that its own process has not finished
starting. A container therefore has to materialise the file before the host starts.

It deliberately imports B4's `autograder_b4.security.keys` rather than rebuilding the JWK
by hand. The `kid` is a SHA-256 thumbprint of the raw public key in a specific encoding,
and B2 matches tokens by that `kid`: a second implementation of the format would be a
second source of truth for a value that has to agree byte for byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: write_jwks.py <private-key.pem> <jwks.json>", file=sys.stderr)
        return 2
    from autograder_b4.security.keys import KeyConfigurationError, load_signing_key

    key_path = Path(argv[1])
    target = Path(argv[2])
    try:
        key = load_signing_key(key_path)
    except KeyConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if not key.can_sign():
        # A public-only key would publish a JWKS that no token can match.
        print(f"error: {key_path} holds no private key", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(key.jwks(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(f"wrote {target} (kid={key.kid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
