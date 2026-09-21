"""Run every gate independently and report PASS/FAIL for each.

`checks.py` is deliberately fail-fast: it raises on the first problem, because the gates build on
each other and a later failure on top of an earlier one is noise. That is right for gating, but it
is unhelpful when you need to know *how much* is currently green -- during active development the
first red gate hides every gate behind it, and "the gate is red" then says nothing about whether
one thing is broken or ten.

This runs the same gates in the same order as `main()` (read from `main()`'s own source with
`ast`, so adding a gate to `main()` cannot leave this list stale), catching each failure and
printing the first line plus a bounded excerpt.

Usage:
    python MAIN/scripts/gate_status.py            # all gates
    python MAIN/scripts/gate_status.py --quiet    # one line per gate
"""

from __future__ import annotations

import ast
import sys
import traceback
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MAIN_ROOT / "host" / "src"))


def gate_order() -> list[str]:
    """The gates `main()` calls, in the order it calls them."""
    source = (MAIN_ROOT / "host" / "src" / "autograder_host" / "checks.py").read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            names: list[str] = []
            for statement in ast.walk(node):
                if isinstance(statement, ast.Call) and isinstance(statement.func, ast.Name):
                    if statement.func.id.startswith("check_"):
                        names.append(statement.func.id)
            return names
    raise SystemExit("could not find main() in checks.py to read the gate order from")


def main() -> int:
    from autograder_host import checks

    quiet = "--quiet" in sys.argv
    names = gate_order()
    # Gates defined outside checks.py (e.g. the inline schema meta-validation) have no callable.
    callable_names = [name for name in names if callable(getattr(checks, name, None))]

    failures: list[str] = []
    for name in callable_names:
        try:
            getattr(checks, name)()
        except Exception as error:  # noqa: BLE001 - reporting the failure is the point
            failures.append(name)
            print(f"FAIL  {name}")
            if not quiet:
                detail = str(error).splitlines()
                for line in detail[:6]:
                    print(f"        {line}")
                if len(detail) > 6:
                    print(f"        ... {len(detail) - 6} more line(s)")
                if not detail or detail == [""]:
                    print("        " + traceback.format_exc().strip().splitlines()[-1])
        else:
            print(f"PASS  {name}")

    print()
    print(f"{len(callable_names) - len(failures)}/{len(callable_names)} gates pass")
    if failures:
        print("red: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
