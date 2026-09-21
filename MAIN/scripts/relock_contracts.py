"""Recompute the contract digest and version, and write both into all four module locks.

This is step 5 of `MAIN/docs/contracts/change-protocol.md`, made executable so neither the
digest nor the version is ever typed by hand. Editing a `contracts.lock` by hand -- or
pasting a digest copied from somewhere -- defeats the whole mechanism: the lock exists to
prove that the contract *bytes* a module was built against are the bytes currently on disk.

`contract_version` used to be left alone by this script, which made it a hand-maintained
field. It promptly drifted: CHG-0006 bumped every contract file to 0.2.0 while all four
locks and all four `module.yaml` files kept saying 0.1.0, and the gate did not notice
because it compared those two hand-written copies against each other instead of against the
contracts. Both are now read from `info.version` and written together.

Usage:
    python MAIN/scripts/relock_contracts.py            # write the locks
    python MAIN/scripts/relock_contracts.py --check    # fail if any lock is stale

The four locks move together on purpose. A module whose lock was forgotten must fail
loudly, which is why this script always updates all four and never takes a module list.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAIN_ROOT.parent


def _checks_module():
    sys.path.insert(0, str(MAIN_ROOT / "host" / "src"))
    from autograder_host import checks

    return checks


def modules() -> tuple[str, ...]:
    """Every module that must carry a lock, discovered rather than listed.

    This used to be the literal `("B1", "B2", "B3", "B4")`, which meant B5 was invisible to the
    only tool allowed to write locks -- the new module would have been left with a hand-written
    digest, which is precisely what ADR-0013 and the "digest is computed, never typed" rule
    exist to prevent. Discovery is the same call the gates use, so the two cannot disagree.
    """
    return tuple(_checks_module().discovered_modules())


def contract_digest() -> str:
    return _checks_module().contract_digest()


def contract_version() -> str:
    return _checks_module().declared_contract_version()


def read_lock(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    digest_match = re.search(r"^digest:\s*(\S+)\s*$", text, re.MULTILINE)
    if digest_match is None:
        raise SystemExit(f"{path}: no digest line")
    version_match = re.search(r"^contract_version:\s*(\S+)\s*$", text, re.MULTILINE)
    if version_match is None:
        raise SystemExit(f"{path}: no contract_version line")
    return text, digest_match.group(1), version_match.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute and write the four contract locks.")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    arguments = parser.parse_args(argv)

    digest = contract_digest()
    version = contract_version()
    stale: list[str] = []
    for module in modules():
        path = REPO_ROOT / module / "contracts.lock"
        text, recorded, recorded_version = read_lock(path)
        if recorded == digest and recorded_version == version:
            print(f"  {module}: up to date ({version}, {digest[:12]}...)")
            continue
        if arguments.check:
            stale.append(module)
            print(
                f"  {module}: STALE (version {recorded_version} != {version},"
                f" digest {recorded[:12]}... != {digest[:12]}...)"
            )
            continue
        updated = re.sub(r"^digest:.*$", f"digest: {digest}", text, count=1, flags=re.MULTILINE)
        updated = re.sub(
            r"^contract_version:.*$",
            f"contract_version: {version}",
            updated,
            count=1,
            flags=re.MULTILINE,
        )
        path.write_text(updated, encoding="utf-8")
        print(
            f"  {module}: updated ({recorded_version} {recorded[:12]}... ->"
            f" {version} {digest[:12]}...)"
        )

    print(f"live contract version: {version}")
    print(f"live contract digest: {digest}")
    if stale:
        print(
            "drift detected; run without --check to rewrite, then commit all four locks together",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
