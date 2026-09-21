"""Configuration for B5.

B5 owns no database and subscribes to nothing, so it has exactly two configuration
concerns: where MAIN's contract files are, and how to verify a B4-issued access token.

Environment variables follow ADR-0008: a module reads the *generic* name, and the
composition host re-exposes `B5_`-prefixed variables under that generic name inside this
module's own settings view. `B4_JWKS_URL` is the one deliberately B4-named variable: the
key set is B4's published surface (`GET /api/v1/auth/jwks`) and B3 consumes it under
exactly this name, so renaming it here would invent a second convention for one fact.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from autograder_platform.contracts import deployment_root

#: The directory holding `MAIN/contracts` and the `B*/` module roots, resolved through the SDK.
#:
#: Resolving it from `__file__` (`parents[3] / "MAIN" / "contracts"`) assumed this file is part
#: of a source checkout. It normally is not: the composition host imports the installed copy,
#: where `parents[3]` is `<venv>` and the derived path is `<venv>/MAIN/contracts`. That is why
#: this module reported `contract tree unreadable (ContractTreeUnavailable)` and held
#: `/health/ready` at `ready=false` even with every contract present on disk.
_DEPLOYMENT_ROOT = deployment_root()

DEFAULT_CONTRACTS_DIR = (
    _DEPLOYMENT_ROOT / "MAIN" / "contracts"
    if _DEPLOYMENT_ROOT is not None
    else Path(__file__).resolve().parents[3] / "MAIN" / "contracts"
)

#: Where this deployment publishes B4's key set. Matches `MAIN/.env.integration` and
#: `MAIN/docs/runbooks/integration.md`; an integrated deployment sets `B4_JWKS_URL`.
DEFAULT_JWKS_URL = "http://127.0.0.1:18000/api/v1/auth/jwks"

#: What B4 actually issues in this deployment (`MAIN/.env.integration`:
#: `B4_JWT_ISSUER=autograder`, `B4_JWT_AUDIENCE=autograder-api`).
DEFAULT_ISSUER = "autograder"
DEFAULT_AUDIENCE = "autograder-api"


@dataclass(frozen=True)
class Settings:
    contracts_dir: Path
    jwks_url: str
    jwt_issuer: str
    jwt_audience: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        return cls(
            contracts_dir=Path(source.get("CONTRACTS_DIR") or DEFAULT_CONTRACTS_DIR),
            jwks_url=source.get("B4_JWKS_URL") or DEFAULT_JWKS_URL,
            jwt_issuer=source.get("JWT_ISSUER") or DEFAULT_ISSUER,
            jwt_audience=source.get("JWT_AUDIENCE") or DEFAULT_AUDIENCE,
        )


_settings: Settings | None = None


def set_settings(settings: Settings) -> None:
    """Install the settings this module serves requests with.

    Called from `B5Module.router(context)`. The host builds routers exactly once per
    process, before any request is served, so a module-level value is the same thing a
    request-scoped lookup would produce -- without threading a context object through
    every helper.
    """
    global _settings
    _settings = settings


def current_settings() -> Settings:
    if _settings is None:
        raise RuntimeError(
            "B5 has no settings: build the router through "
            "autograder_b5.plugin.B5Module.router(context) before serving requests"
        )
    return _settings
