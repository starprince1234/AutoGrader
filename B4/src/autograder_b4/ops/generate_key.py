"""Generate the Ed25519 signing key pair used for access tokens.

    uv run python -m autograder_b4.ops.generate_key

Writes `JWT_PRIVATE_KEY_FILE` (default `secrets/jwt-ed25519.pem`) with owner-only
permissions and prints the public `kid` so the JWKS endpoint can be cross-checked
without reading the private half.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..security.keys import KeyConfigurationError, generate_signing_key, load_signing_key

DEFAULT_KEY_PATH = Path("secrets/jwt-ed25519.pem")


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_environment_file

    load_environment_file()
    parser = argparse.ArgumentParser(
        description="Generate the B4 Ed25519 access-token signing key."
    )
    parser.add_argument(
        "--path",
        default=None,
        help="destination PEM path; defaults to JWT_PRIVATE_KEY_FILE or secrets/jwt-ed25519.pem",
    )
    arguments = parser.parse_args(argv)
    target = Path(arguments.path or os.getenv("JWT_PRIVATE_KEY_FILE") or DEFAULT_KEY_PATH)
    try:
        key = generate_signing_key(target)
    except KeyConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    reloaded = load_signing_key(target)
    if reloaded.kid != key.kid:  # pragma: no cover - a mismatch means a broken filesystem
        print("error: the written key does not round-trip", file=sys.stderr)
        return 1
    print(f"wrote {target} (kid={key.kid})")
    print("the private key is not in version control; back it up outside the repository")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
