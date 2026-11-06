"""Each module declares its contract version twice: `module.yaml` and the runtime descriptor.

The registry refuses to start when they disagree -- which is correct, but the disagreement only
surfaced *during a production deploy* twice in a row (0.26->0.27 and 0.27->0.28), each time
costing a full release cycle. This test reads both declarations as text (no imports, so it does
not need the module's dependencies) and fails with the same message the registry would print.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
MODULES = ("B2", "B3", "B4", "B5")


def _declared_in_manifest(module: str) -> str:
    path = ROOT / module / "module.yaml"
    match = re.search(r"^contract_version:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.M)
    assert match is not None, f"{path} declares no contract_version"
    return match.group(1)


def _declared_in_descriptor(module: str) -> str:
    path = ROOT / module / "src" / f"autograder_{module.lower()}" / "plugin.py"
    match = re.search(r'contract_version="([^"]+)"', path.read_text(encoding="utf-8"))
    assert match is not None, f"{path} declares no contract_version"
    return match.group(1)


@pytest.mark.parametrize("module", MODULES)
def test_descriptor_matches_module_manifest(module: str) -> None:
    descriptor = _declared_in_descriptor(module)
    manifest = _declared_in_manifest(module)
    assert descriptor == manifest, (
        f"{module}: module.yaml and the runtime descriptor disagree -- "
        f"descriptor.contract_version={descriptor!r} but module.yaml declares {manifest!r}"
    )
