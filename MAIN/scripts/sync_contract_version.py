"""Keep every declaration of the contract version in step with `MAIN/contracts`.

The contract version is one fact that lives in **fourteen** places: six `info.version` lines under
`contracts/` (four `openapi/*.yaml`, `common.yaml`, and `asyncapi/events.yaml`), five
`contracts.lock` files, five `module.yaml` manifests, and four `ModuleDescriptor` literals in the
backend modules' `plugin.py`. Moving the contract by hand has already gone wrong once: CHG-0016
updated the locks and the manifests and missed the four descriptors, and **nothing failed until a
reinstall**, at which point the host simply refused to assemble (see `phase-01` §12.11).

This tool covers the parts that are pure derivation:

  * `--to VERSION` rewrites the six `info.version` declarations. Authoring a version number is a
    deliberate act that belongs to a change record, so this is opt-in rather than automatic.
  * it then always syncs the five manifests and the four descriptors to whatever the contracts
    tree currently declares, which is the step that was missed.
  * `--check` reports what is out of step and writes nothing, for use before a release.

It deliberately does **not** touch `contracts.lock`: those carry a digest that only
`relock_contracts.py` may compute (`AGENTS.md`: never fill a digest in by hand).

Usage:
    python MAIN/scripts/sync_contract_version.py --to 0.12.0
    python MAIN/scripts/sync_contract_version.py            # sync manifests + descriptors
    python MAIN/scripts/sync_contract_version.py --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]
REPO = MAIN_ROOT.parent
CONTRACTS = MAIN_ROOT / "contracts"

sys.path.insert(0, str(MAIN_ROOT / "sdk" / "src"))
from autograder_platform import contracts as platform_contracts  # noqa: E402

try:  # the console on this host uses a legacy code page
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover - depends on the host console
    pass

MODULES = ("B1", "B2", "B3", "B4", "B5")
#: `info.version` sits two spaces in, under `info:`. Anchoring on the exact indentation keeps
#: this away from the indented `version:` schema properties, which mean something else entirely.
_VERSION_LINE = re.compile(r"^  version: (\S+)(\r?)$", re.MULTILINE)


def contract_files() -> list[Path]:
    return sorted((CONTRACTS / "openapi").glob("*.yaml")) + [
        CONTRACTS / "asyncapi" / "events.yaml"
    ]


def descriptor_path(module: str) -> Path:
    return REPO / module / "src" / f"autograder_{module.lower()}" / "plugin.py"


def set_info_version(version: str) -> None:
    for path in contract_files():
        text = path.read_text(encoding="utf-8")
        updated, count = _VERSION_LINE.subn(
            lambda match: f"  version: {version}{match.group(2)}", text
        )
        if count != 1:
            raise SystemExit(f"{path.name}: expected exactly one `info.version`, found {count}")
        path.write_text(updated, encoding="utf-8")


def sync(check_only: bool, version: str) -> int:
    changes: list[str] = []
    pending: list[tuple[Path, str]] = []

    for module in MODULES:
        manifest_path = REPO / module / "module.yaml"
        manifest = manifest_path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"(?m)^contract_version: (\S+)(\r?)$",
            lambda match: f"contract_version: {version}{match.group(2)}",
            manifest,
        )
        if count != 1:
            raise SystemExit(f"{module}/module.yaml: no top-level contract_version found")
        declared = re.search(r"(?m)^contract_version: (\S+)$", manifest)
        assert declared is not None
        if declared.group(1) != version:
            changes.append(f"{module}/module.yaml: {declared.group(1)} -> {version}")
            pending.append((manifest_path, updated))

        plugin_path = descriptor_path(module)
        if not plugin_path.is_file():
            continue  # B1 is a static SPA and ships no descriptor
        plugin = plugin_path.read_text(encoding="utf-8")
        current = re.search(r'contract_version="([^"]+)"', plugin)
        if current is None:
            raise SystemExit(f"{plugin_path}: no contract_version literal found")
        if current.group(1) != version:
            updated_plugin, count = re.subn(
                r'contract_version="[^"]+"', f'contract_version="{version}"', plugin
            )
            if count != 1:
                raise SystemExit(f"{plugin_path}: expected one literal, found {count}")
            changes.append(f"{module}/plugin.py: {current.group(1)} -> {version}")
            pending.append((plugin_path, updated_plugin))

    if not changes:
        print(f"manifests and descriptors already declare {version}")
        return 0
    print(f"{len(changes)} declaration(s) out of step with contracts ({version}):")
    for item in changes:
        print(f"  {item}")
    if check_only:
        return 1
    for path, text in pending:
        path.write_text(text, encoding="utf-8")
    print("synced; now run `python MAIN/scripts/relock_contracts.py` to refresh the locks")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", metavar="VERSION", help="rewrite the six info.version lines")
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    arguments = parser.parse_args(argv)

    if arguments.to:
        set_info_version(arguments.to)
        print(f"contracts info.version set to {arguments.to}")

    version = platform_contracts.declared_contract_version(CONTRACTS)
    print(f"contracts declare {version}")
    return sync(arguments.check, version)


if __name__ == "__main__":
    raise SystemExit(main())
