"""The environment the conformance suite runs in.

Two things have to be true before a single assertion means anything:

1. **The composition must be installed.** B5's dev dependency group installs the host's own
   loader (`autograder-host`) and the modules it is expected to compose (B2, B3, B4) from
   their source trees, so `importlib.metadata` resolves the real `autograder.modules` entry
   points. Nothing is faked here: the loader discovers what the environment actually has.
2. **The integration environment must be loaded.** `autograder_host.main` builds its
   application at import time, so the same variables `MAIN/scripts/run_host.ps1` exports are
   loaded first. Without them the import fails -- and failing loudly is the point: a suite
   that silently skips when the composition is not configured would prove nothing at all.

The only external service replaced is B4's JWKS *endpoint* (`issuer` below), which
`MAIN/docs/testing/strategy.md` allows to be substituted for an uncontrollable external
service. B5's verification code itself -- `kid` selection, Ed25519 signature check and
registered-claim validation -- runs unmodified against it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

TESTS_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = TESTS_DIR.parents[1]
MAIN_ROOT = REPOSITORY_ROOT / "MAIN"
INTEGRATION_ENV = MAIN_ROOT / ".env.integration"

#: `AUTOGRADER_MODULES` names importable factories, so the probe composition the negative
#: cases declare has to be importable by the *loader's* interpreter -- this one.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _load_integration_environment(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(
            f"{path} is missing. The conformance suite assembles the real composition, so it "
            "needs the real host environment: copy MAIN/.env.integration and fill in the "
            "local values (MAIN/docs/runbooks/integration.md). This suite fails rather than "
            "skipping, because a skipped conformance check is indistinguishable from a "
            "passing one."
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ[name.strip()] = value.strip()


_load_integration_environment(INTEGRATION_ENV)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class _JwksRequestHandler(BaseHTTPRequestHandler):
    document = b"{}"

    def do_GET(self) -> None:  # noqa: N802 - the name is BaseHTTPRequestHandler's contract
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.document)))
        self.end_headers()
        self.wfile.write(self.document)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - ditto
        """Stay quiet: the fixture server is not the subject of the test."""


@dataclass(frozen=True)
class Issuer:
    """An Ed25519 key pair publishing a JWKS, plus the ability to sign tokens with it.

    `private_key` is excluded from `repr` so that a failing assertion can never print the
    key material into a test report.
    """

    jwks_url: str
    key_id: str
    private_key: ed25519.Ed25519PrivateKey = field(repr=False, compare=False)
    issuer: str = "autograder"
    audience: str = "autograder-api"

    def token(self, **overrides) -> str:
        """A token shaped exactly as B4 issues it (`MAIN/.env.integration`, b4 tokens)."""
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        }
        claims.update(overrides)
        return jwt.encode(
            claims, self.private_key, algorithm="EdDSA", headers={"kid": self.key_id}
        )


@pytest.fixture(scope="session")
def issuer() -> Issuer:
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    # The `kid` convention B4 publishes: base64url of the SHA-256 of the raw public key.
    key_id = _b64url(hashlib.sha256(public_raw).digest())
    document = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "kid": key_id,
                "x": _b64url(public_raw),
                "use": "sig",
                "alg": "EdDSA",
            }
        ]
    }
    handler = type("_BoundJwksHandler", (_JwksRequestHandler,), {})
    handler.document = json.dumps(document).encode("utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    built = Issuer(
        jwks_url=f"http://127.0.0.1:{server.server_address[1]}/api/v1/auth/jwks",
        key_id=key_id,
        private_key=private_key,
    )
    try:
        yield built
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def assembled_app(issuer: Issuer):
    """The real host application: `autograder_host.main.create_app()`.

    Built through the host's own factory -- the same call `MAIN/scripts/run_host.ps1` ends
    up serving -- so `app.state.modules` holds the modules the real loader produced and every
    module's router is included exactly as it is in the running deployment. No port is bound:
    the tests drive it with FastAPI's `TestClient`.
    """
    os.environ["B4_JWKS_URL"] = issuer.jwks_url
    os.environ.pop("AUTOGRADER_MODULES", None)
    from autograder_host.main import create_app

    return create_app()
