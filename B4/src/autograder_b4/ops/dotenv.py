"""Minimal `.env` loader for local tooling and tests.

The deployed host injects real environment variables (see `MAIN/compose.yaml`), so this
loader is deliberately scoped to operator entry points and the test suite: it never
runs inside the module's request path, and it never overrides an existing variable, so
an explicitly exported value always wins.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_environment_file(path: Path | None = None) -> dict[str, str]:
    """Load `KEY=VALUE` lines into `os.environ` without overwriting existing keys.

    Returns the values that were applied, which is safe to assert on in tests and never
    contains a value that was already present in the environment.
    """
    target = path or DEFAULT_ENV_FILE
    applied: dict[str, str] = {}
    if not target.is_file():
        return applied
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied
