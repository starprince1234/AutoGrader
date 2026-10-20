#!/usr/bin/env python3
"""Merge a rendered fragment into the host's root-only environment file, in place.

Why a merge instead of replacing the file: `/etc/autograder/runtime.env` also holds the
database passwords and is rendered exactly once by `bootstrap-host`. Replacing it to add a
mail channel would either rewrite those secrets or require re-running the whole bootstrap --
and that path refuses when the file already exists. So this script touches **only the keys in
the fragment**: it appends the missing ones and rewrites the lines of the ones that changed,
leaving every other byte of the file alone.

Invariants it enforces:

* the target must be a regular file (not a symlink), because this runs as root;
* the write is atomic (`os.replace` on a same-directory temp file) so a crash cannot leave a
  half-written environment file that would stop the service from starting;
* the file keeps mode 0600 and root ownership;
* nothing but key **names** is printed -- values never reach a log line.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

KEY_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")


def parse_fragment(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = KEY_PATTERN.match(line)
        if match is None:
            raise SystemExit(f"{path}: line {number} is not a KEY=VALUE assignment")
        key = match.group("key")
        if any(existing == key for existing, _ in entries):
            raise SystemExit(f"{path}: duplicate key {key}")
        entries.append((key, raw.rstrip("\n")))
    if not entries:
        raise SystemExit(f"{path}: the fragment contains no assignments")
    return entries


def merge(target: Path, entries: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = KEY_PATTERN.match(line)
        if match is not None:
            positions.setdefault(match.group("key"), index)

    added: list[str] = []
    updated: list[str] = []
    for key, assignment in entries:
        line = assignment + "\n"
        index = positions.get(key)
        if index is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(line)
            positions[key] = len(lines) - 1
            added.append(key)
        elif lines[index].rstrip("\n") != assignment:
            lines[index] = line
            updated.append(key)
    return lines, added, updated


def write_atomically(target: Path, lines: list[str]) -> None:
    directory = target.parent
    handle, temporary = tempfile.mkstemp(prefix=".runtime.env.", dir=str(directory))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.writelines(lines)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        # Root-owned on the host; skipped where the call does not exist (Windows) or the
        # caller is not root (a developer running this against a copy).
        if hasattr(os, "chown") and hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(temporary, 0, 0)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--fragment", required=True)
    arguments = parser.parse_args()

    target = Path(arguments.target)
    fragment = Path(arguments.fragment)

    if target.is_symlink() or not target.is_file():
        print(f"refusing: {target} is not a regular file", file=sys.stderr)
        return 2
    if not fragment.is_file():
        print(f"refusing: {fragment} is not a regular file", file=sys.stderr)
        return 2

    lines, added, updated = merge(target, parse_fragment(fragment))
    if added or updated:
        write_atomically(target, lines)
    print("added:", ",".join(added) if added else "-")
    print("updated:", ",".join(updated) if updated else "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
