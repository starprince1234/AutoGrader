"""Is this tree restart-ready? Assemble the composition host and interrogate the result.

The live host is started once and then runs for a long time, so "does the host still assemble?"
normally gets answered only when someone restarts it -- which is the worst moment to find out.
`create_app()` performs the entire wiring (module discovery and capability-ordered load,
per-module settings views, route inclusion, the static mount) and binds nothing, so running it
is safe while the live system keeps serving.

It is deliberately a **readiness probe, not a gate**: it imports the *installed* packages, which
is what a real restart loads. A stale install therefore fails here exactly as it would at
startup, and the fix is `uv sync --reinstall-package <name>` -- not an edit.

Two readings, and the second is the one that matters:

1. **Route surface**, read from `app.openapi()` rather than `app.routes`. FastAPI implements
   `include_router` lazily: a module's routes are held as an `_IncludedRouter` placeholder and
   only flattened when the schema is built. Reading `app.routes` showed the host's own seven
   paths and nothing else, so this probe used to print "routes from CHG-0011..0015: none
   (installed code predates them)" against a tree where every one of them was registered --
   a wrong diagnosis pointing at reinstalls, which cannot change the answer.

2. **Per-module health**. A host that assembles while a module cannot reach its own dependencies
   is not restart-ready. This is where B5's `contract tree unreadable` and B2 resolving
   `MAIN/contracts` to a virtualenv path both surfaced, and nowhere else: every unit suite runs
   from the working tree, where those derivations happen to be correct.

Health needs the dependencies running (PostgreSQL, NATS). That is intentional: readiness is the
question being asked. Use `--no-health` for an assembly-only check on a machine without them.

Usage:
    python MAIN/scripts/probe_host_assembly.py [--no-health]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]

# The same loading rule as scripts/run_host.ps1: the integration env file drives the host.
for raw in (MAIN_ROOT / ".env.integration").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ[key.strip()] = value.strip()

sys.path.insert(0, str(MAIN_ROOT / "host" / "src"))

#: Routes the host serves that are deliberately **not** in the contract: liveness/readiness are
#: platform plumbing (ADR-0007 keeps the static client and the probes out of the module
#: capability DAG), `/app` is a static mount rather than an operation, and `/docs`, `/redoc` and
#: `/openapi.json` are FastAPI's own.
HOST_OWNED_PREFIXES = ("/health", "/app", "/docs", "/redoc")
HOST_OWNED_EXACT = ("/openapi.json",)


def contract_paths() -> dict[str, str]:
    """Every path declared in `MAIN/contracts/openapi/*.yaml`, with the file that declares it.

    Reading the contract rather than a hand-maintained list is the point: this probe used to
    check a hardcoded tuple of markers, so a **newly declared** endpoint that nobody had
    implemented yet was silently absent from its output -- a reader would conclude "everything
    is registered" from a line that only meant "the seven paths I know about are registered".
    """
    import yaml

    declared: dict[str, str] = {}
    for document_path in sorted((MAIN_ROOT / "contracts" / "openapi").glob("*.yaml")):
        document = yaml.safe_load(document_path.read_text(encoding="utf-8")) or {}
        for path in (document.get("paths") or {}):
            declared[path] = document_path.name
    return declared


def main(argv: list[str]) -> int:
    check_health = "--no-health" not in argv
    try:
        from autograder_host.main import create_app

        app = create_app()
    except Exception as error:  # noqa: BLE001 - surfacing it is the entire point
        print(f"host assembly FAILED: {type(error).__name__}: {error}")
        print(
            "\nIf this is a contract or descriptor disagreement, the installed packages are "
            "behind the working tree: run `uv sync --all-groups --extra modules "
            "--reinstall-package autograder-b2 ...` for each module the gate names."
        )
        return 1

    paths = sorted(app.openapi().get("paths", {}))
    mounts = sorted(
        getattr(route, "path", "") for route in app.routes if type(route).__name__ == "Mount"
    )
    print(f"host assembled: {len(paths)} route paths (from the OpenAPI schema, which flattens routers)")
    print(f"  static mounts: {mounts or 'none'}")
    print(f"  health routes: {[p for p in paths if p.startswith('/health')]}")

    declared = contract_paths()
    served = set(paths)
    unserved = sorted(path for path in declared if path not in served)
    undeclared = sorted(
        path
        for path in served
        if path not in declared
        and path not in HOST_OWNED_EXACT
        and not path.startswith(HOST_OWNED_PREFIXES)
    )
    print()
    print(f"contract vs composition ({len(declared)} declared path(s)):")
    print(f"  declared but NOT served ({len(unserved)}): {unserved or 'none'}")
    print(f"  served but NOT declared ({len(undeclared)}): {undeclared or 'none'}")

    if not check_health:
        return 0

    print()
    print("module readiness:")
    unhealthy: list[str] = []
    for module in app.state.modules:
        name = module.descriptor.module
        report = module.health(app.state.contexts[name])
        print(f"  {'ok  ' if report.healthy else 'FAIL'} {name}: {report.detail}")
        if not report.healthy:
            unhealthy.append(name)
    if unhealthy:
        print(f"\nnot restart-ready: {unhealthy}")
        return 1
    print("\nrestart-ready: every loaded module reports healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
