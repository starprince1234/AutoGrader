"""Canonical contract-digest and lock helpers, shared by the host and every module.

Why this module exists: the digest algorithm was implemented **four times** -- once in
`MAIN/host/src/autograder_host/checks.py`, twice in B2
(`src/autograder_b2/contracts.py` and `verify/contract_lock_check.py`) and once in B4
(`src/autograder_b4/registry.py`). Nothing checked that the four agreed.

That is a real hazard rather than a tidiness complaint. The digest is the mechanism that
proves "the contract bytes this module was built against are the bytes on disk"; if one
implementation iterated the tree differently -- a different sort, a missing separator, a
different path normalisation -- every module would keep reporting "in sync" while comparing
against a value MAIN does not produce. The failure would appear as a module that refuses to
boot for no visible reason, or worse, one that boots against a contract it never saw.

The SDK is the legitimate shared point (it is the one package the host and all modules
already depend on), so the canonical implementation lives here and everyone else should call
it. `MAIN/host/src/autograder_host/checks.py` delegates here; the module-side copies are
verified equal to this one by a gate, and are expected to be migrated to a direct call.

Digest definition (must not be changed without a contract change):

    sha256 over, for each file under `contracts/` in sorted relative-path order:
        <posix relative path> \\0 <raw file bytes>

Sorting is by the **posix** relative path; every file participates, including documentation
such as `contracts/CHANGELOG.md`, so editing the changelog changes the digest. That is
deliberate (ADR-0001 wants the whole tree covered) and is why the required order of a
contract change is: edit the contract -> update the changelog -> relock.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path

#: The field names every `contracts.lock` carries, in file order.
LOCK_FIELDS = ("contract_version", "source", "digest_policy", "digest", "status")

#: The environment variable naming the **deployment root**: the directory that holds
#: `MAIN/contracts` and every `B*/` module root.
#:
#: The name belongs to B4 because B4's runtime registry is what needs the root first -- it
#: globs `B*/module.yaml` beneath it -- and both supported deployment shapes already set the
#: variable (`MAIN/.env.integration` for a local run, `compose.yaml`'s `/app` inside the
#: image). B5 likewise reads a B4-named variable for the same reason (`B4_JWKS_URL`): a
#: second name for one fact is what this project removes, not adds.
DEPLOYMENT_ROOT_VARIABLE = "B4_MODULE_ROOT"


def deployment_root(environ: Mapping[str, str] | None = None) -> Path | None:
    """Locate the directory holding `MAIN/contracts` and the `B*/` module roots.

    Returns `None` when no candidate actually holds a contract tree, so a caller fails
    loudly. "We derived a path and it happened to exist" is not a check.

    A candidate must be **verified**, not merely derived, because this package is normally
    imported from an installed copy in `site-packages`: there `Path(__file__).resolve()
    .parents[4]` is inside the virtualenv rather than the repository. Every module's unit
    tests run from the working tree, where the derivation is correct, so nothing noticed
    that the integrated host resolved `MAIN/contracts` to `<venv>/MAIN/contracts` and read
    zero schemas -- B2 could not validate a single submission envelope and B5's conformance
    endpoint could not read one contract path, while both suites stayed green.
    """
    source = os.environ if environ is None else environ
    candidates: list[Path] = []
    configured = (source.get(DEPLOYMENT_ROOT_VARIABLE) or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser().resolve())
    # A source checkout puts this file at <root>/MAIN/sdk/src/autograder_platform/contracts.py.
    candidates.append(Path(__file__).resolve().parents[4])
    for candidate in candidates:
        if (candidate / "MAIN" / "contracts").is_dir():
            return candidate
    return None


def contract_digest(contracts_root: Path) -> str:
    """SHA-256 over the whole contract tree. See the module docstring for the definition."""
    digest = hashlib.sha256()
    relative_paths = sorted(
        path.relative_to(contracts_root).as_posix()
        for path in contracts_root.rglob("*")
        if path.is_file()
    )
    for relative in relative_paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((contracts_root / relative).read_bytes())
    return digest.hexdigest()


def declared_contract_version(contracts_root: Path) -> str:
    """The version the contract files themselves declare; all of them must agree.

    Every `info.version` in the tree is read and the set must have exactly one member. A
    partial version bump is a real failure mode: bumping one OpenAPI file leaves consumers
    unable to tell which version is in force.
    """
    found: dict[str, str] = {}
    for path in sorted(contracts_root.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^info:\s*$.*?^\s+version:\s*(\S+)\s*$", text, re.MULTILINE | re.DOTALL)
        if match:
            found[path.name] = match.group(1)
    if not found:
        raise ValueError(f"no contract file under {contracts_root} declares info.version")
    versions = set(found.values())
    if len(versions) != 1:
        raise ValueError(f"contract files disagree on info.version: {found}")
    return versions.pop()


def read_lock(lock_path: Path) -> dict[str, str]:
    """Parse a `contracts.lock` into its field map, rejecting a malformed one.

    Kept deliberately strict: a lock that cannot be parsed must not be treated as "in sync",
    and a missing field is a failure rather than a default.
    """
    fields: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator or not value.strip():
            raise ValueError(f"{lock_path}: unparseable lock line {line!r}")
        fields[key.strip()] = value.strip()
    missing = [name for name in LOCK_FIELDS if name not in fields]
    if missing:
        raise ValueError(f"{lock_path}: missing lock fields {missing}")
    return fields


def lock_is_current(lock_path: Path, contracts_root: Path) -> bool:
    """True only when the lock's version *and* digest match the contract tree right now."""
    fields = read_lock(lock_path)
    return (
        fields["contract_version"] == declared_contract_version(contracts_root)
        and fields["digest"] == contract_digest(contracts_root)
    )


__all__ = [
    "DEPLOYMENT_ROOT_VARIABLE",
    "LOCK_FIELDS",
    "contract_digest",
    "declared_contract_version",
    "deployment_root",
    "lock_is_current",
    "read_lock",
]
