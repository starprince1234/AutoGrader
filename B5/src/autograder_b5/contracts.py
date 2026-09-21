"""View 1 of the conformance report: the paths `MAIN/contracts/openapi/*.yaml` declares.

The contract tree is MAIN's authority (ADR-0003), so it is read from disk rather than
reconstructed from the running application: "what the contract promises" and "what the
assembled application serves" are exactly the two things whose disagreement the report
exists to expose, and deriving one from the other would make the comparison vacuous.

Parsing uses PyYAML -- the same parser `autograder_host.checks` uses on the same files --
because MAIN's gate refuses contract files that PyYAML resolves silently but js-yaml (B1's
generator) rejects. A validator more permissive than its consumers certifies files the
consumers cannot read, so the two sides must not diverge on how they read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import ContractTreeUnavailable

OPENAPI_DIRECTORY = "openapi"


@dataclass(frozen=True)
class ContractPath:
    """One declared path and the contract file that declares it.

    The owning file travels with the path because every difference the report emits has to
    name *which side* is missing something; "3 paths are unserved" cannot be acted on.
    """

    path: str
    document: str


def contract_paths(contracts_dir: Path) -> tuple[ContractPath, ...]:
    """Every distinct path under `contracts_dir/openapi/*.yaml`, sorted by path.

    A path declared by two contract files is one path served once; the first document in
    sorted order is recorded as its source so the report stays deterministic.
    """
    documents = sorted((contracts_dir / OPENAPI_DIRECTORY).glob("*.yaml"))
    if not documents:
        raise ContractTreeUnavailable(
            f"no OpenAPI contract documents under {contracts_dir / OPENAPI_DIRECTORY}"
        )
    found: dict[str, ContractPath] = {}
    for document in documents:
        try:
            payload = yaml.safe_load(document.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as error:
            raise ContractTreeUnavailable(
                f"{document.name} could not be read: {type(error).__name__}"
            ) from error
        declared = payload.get("paths")
        if declared is None:
            continue
        if not isinstance(declared, dict):
            raise ContractTreeUnavailable(f"{document.name}: `paths` is not a mapping")
        for path in declared:
            found.setdefault(str(path), ContractPath(str(path), document.name))
    return tuple(found[key] for key in sorted(found))
