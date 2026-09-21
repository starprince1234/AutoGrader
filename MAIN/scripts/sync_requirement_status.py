"""Generate MAIN's requirement statuses from the owning modules' own matrices.

MAIN states requirement statuses in **two** documents, and both were hand-filled snapshots:

  * `traceability.md` -- the global matrix. Filled during Phase 00 and never touched again, so
    by the time every module had delivered, 31 of the 32 rows still said `骨架`/`待实现` while
    the four module matrices said `已完成`.
  * `rq-catalog.md` §0 -- the requirement catalogue. Its own header calls it the authoritative
    catalogue, so it is the *first* document a reviewer opens, and it kept the same Phase 00
    snapshot: all 32 rows read `待实现`/`骨架`, and its three `RQ-B-0-*` rows used
    `契约已锁定`, which is not one of the four permitted status words at all.

The gate originally covered only the matrix, which is exactly why the catalogue was able to
drift unnoticed. It now covers both (`checks.main_matrices()`), so:

  * the parsing and the comparison live in `autograder_host.checks` -- one implementation,
    used by both this script and the gate;
  * `check_requirement_baseline()` fails when either document disagrees with the owners, which
    is what makes running this script mandatory instead of optional.

Usage:
    python MAIN/scripts/sync_requirement_status.py           # rewrite the status columns
    python MAIN/scripts/sync_requirement_status.py --check    # fail if either is stale

Only status cells are ever written. Every other column, and all prose, is preserved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = MAIN_ROOT / "docs" / "requirements"

sys.path.insert(0, str(MAIN_ROOT / "host" / "src"))
from autograder_host import checks  # noqa: E402  (path must be set first)

try:  # the console on this host uses a legacy code page; requirement titles are Chinese
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover - depends on the host console
    pass


#: Which cell holds the status in each document. Stated rather than guessed: the global matrix
#: has the status among other cells, while the catalogue's §0 table ends with it. Neither
#: document is allowed to have a row we cannot place a status in, so the mode is explicit and a
#: mismatch is an error instead of a silent skip.
DOCUMENTS: tuple[tuple[Path, str], ...] = (
    (REQUIREMENTS / "traceability.md", "status-word"),
    (REQUIREMENTS / "rq-catalog.md", "last-cell"),
)


def _status_index(cells: list[str], mode: str, requirement: str, line: str) -> int:
    if mode == "status-word":
        for index, cell in enumerate(cells):
            if checks._status_word(cell) is not None:
                return index
        raise SystemExit(f"no status cell to replace for {requirement}: {line!r}")
    # `last-cell`: the trailing cell after the final `|` is empty, so take the last non-empty.
    for index in range(len(cells) - 1, -1, -1):
        if cells[index].strip():
            return index
    raise SystemExit(f"row has no cells to write a status into for {requirement}: {line!r}")


def rewrite(check_only: bool) -> int:
    statuses = checks.aggregated_requirement_statuses()
    # `RQ-B-0-*` rows have no module owner (see `checks.main_declared_statuses`); MAIN declares
    # them in the matrix, and the catalogue must agree with that same value.
    targets = {**checks.main_declared_statuses(), **statuses}

    changed: list[str] = []
    pending: list[tuple[Path, str]] = []
    for path, mode in DOCUMENTS:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        updated: list[str] = []
        for line in lines:
            requirement = checks._requirement_row(line)
            if requirement is None or requirement not in targets:
                updated.append(line)
                continue
            target = targets[requirement]
            cells = line.split("|")
            index = _status_index(cells, mode, requirement, line)
            current = checks._status_word(cells[index])
            if current != target:
                # `current is None` means the cell held something that is not a legal status
                # word at all (the catalogue's `契约已锁定`). Capture it before overwriting so
                # the report names what was actually there.
                previous = cells[index].strip().strip("`* ") if current is None else current
                # Preserve an annotation the owner attached (`已完成（…）`); only the status
                # word is generated. Dropping a caveat would make the matrix less honest.
                annotation = "" if current is None else cells[index].strip().strip("`* ")[len(current):]
                cells[index] = f" {target}{annotation} "
                changed.append(f"{path.name}: {requirement}: {previous} -> {target}")
            updated.append("|".join(cells))
        pending.append((path, "".join(updated)))

    if not changed:
        print("statuses already match the owning modules in every MAIN matrix")
        return 0
    print(f"{len(changed)} status cell(s) differ:")
    for item in changed:
        print(f"  {item}")
    if check_only:
        print("run without --check to regenerate", file=sys.stderr)
        return 1
    for path, text in pending:
        path.write_text(text, encoding="utf-8")
        print(f"rewrote {path.relative_to(MAIN_ROOT.parent)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    arguments = parser.parse_args(argv)
    return rewrite(arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
