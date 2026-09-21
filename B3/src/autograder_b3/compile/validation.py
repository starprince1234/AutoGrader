"""Input guards shared by the adapter compilers (untrusted input handling).

Everything here defends against a *teacher-supplied* declaration that has been
crafted to escape the sandbox workspace or to reach the network. The rules are
deliberately structural (regex + segment analysis) rather than "does it look
suspicious", so they can be tested exhaustively:

* a workspace path is **relative**, has no drive/UNC/`~` prefix, no backslash, no
  NUL, no empty / `.` / `..` segment, and matches the contract's path pattern;
* an import target is a dotted identifier - never a path, never a URL;
* an argument vector is a sequence of strings preserved byte-for-byte (B3 never
  joins it into a command line; see ``tests/test_no_shell.py``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence

from ..errors import ManifestInvalid, PathNotAllowed

#: Contract pattern from ``evaluation-package.schema.json`` (adapter.files[].path).
_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_PATH_CHARS = 200
MAX_ARG_CHARS = 4096
MAX_STDIN_CHARS = 65_536
MAX_OUTPUT_CHARS = 65_536
MAX_ARGS = 64

_FORBIDDEN_PATH_PREFIXES = ("/", "\\", "~")
_FORBIDDEN_PATH_TOKENS = ("\\", "\x00", "://")


def validate_workspace_path(path: object, *, field: str = "path") -> str:
    """Validate a workspace-relative path or raise :class:`PathNotAllowed`."""
    if not isinstance(path, str):
        raise PathNotAllowed(f"{field} must be a string", field=field)
    candidate = path.strip()
    if not candidate or candidate != path:
        raise PathNotAllowed(f"{field} must be a non-empty path without padding", field=field)
    if len(candidate) > MAX_PATH_CHARS:
        raise PathNotAllowed(f"{field} exceeds {MAX_PATH_CHARS} characters", field=field)
    if candidate.startswith(_FORBIDDEN_PATH_PREFIXES) or candidate.startswith("\\\\"):
        raise PathNotAllowed(f"{field} must be relative to the workspace", field=field)
    if any(token in candidate for token in _FORBIDDEN_PATH_TOKENS):
        raise PathNotAllowed(f"{field} contains a forbidden character", field=field)
    if re.match(r"^[A-Za-z]:", candidate):
        raise PathNotAllowed(f"{field} must not carry a drive letter", field=field)
    segments = candidate.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise PathNotAllowed(f"{field} contains an empty, '.' or '..' segment", field=field)
    if not _PATH_PATTERN.match(candidate):
        raise PathNotAllowed(f"{field} does not match the contract path pattern", field=field)
    return candidate


def validate_module_identifier(module: object) -> str:
    """Validate a dotted module name resolved *inside* the workspace."""
    if not isinstance(module, str):
        raise ManifestInvalid("callable.module must be a string", field="callable.module")
    candidate = module.strip()
    if not candidate or candidate != module:
        raise ManifestInvalid("callable.module must be a non-empty dotted name", field="callable.module")
    if len(candidate) > 200:
        raise ManifestInvalid("callable.module exceeds 200 characters", field="callable.module")
    if any(token in candidate for token in ("/", "\\", ":", "\x00", "~", " ")):
        raise ManifestInvalid(
            "callable.module must be an importable dotted name, not a path or URL",
            field="callable.module",
        )
    segments = candidate.split(".")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ManifestInvalid("callable.module contains an empty segment", field="callable.module")
    for segment in segments:
        if not _IDENTIFIER_PATTERN.match(segment):
            raise ManifestInvalid(
                f"callable.module segment {segment!r} is not a valid identifier",
                field="callable.module",
            )
    return candidate


def validate_attribute_identifier(attribute: object) -> str:
    if not isinstance(attribute, str):
        raise ManifestInvalid("callable.attribute must be a string", field="callable.attribute")
    if len(attribute) > 200 or not _IDENTIFIER_PATTERN.match(attribute):
        raise ManifestInvalid(
            "callable.attribute must be a single identifier", field="callable.attribute"
        )
    return attribute


def validate_arguments_json(payload: object) -> str | None:
    """``arguments_json`` is opaque to B3 but must be valid JSON, not code."""
    if payload is None:
        return None
    if not isinstance(payload, str):
        raise ManifestInvalid("callable.arguments_json must be a string", field="callable.arguments_json")
    if len(payload) > 8192:
        raise ManifestInvalid("callable.arguments_json exceeds 8192 characters", field="callable.arguments_json")
    try:
        json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ManifestInvalid(
            f"callable.arguments_json is not valid JSON: {exc.msg}", field="callable.arguments_json"
        ) from exc
    return payload


def validate_args(args: object) -> tuple[str, ...]:
    """Validate an argument vector.

    Shell metacharacters are **kept verbatim**: the array is the safety mechanism
    (``RQ-B-3-40`` acceptance 3), so nothing here escapes, quotes or rejects them.
    """
    if args is None:
        return ()
    if isinstance(args, str):
        raise ManifestInvalid(
            "adapter.args must be an array, never a shell string", field="adapter.args"
        )
    if not isinstance(args, (list, tuple)):
        raise ManifestInvalid("adapter.args must be an array of strings", field="adapter.args")
    if len(args) > MAX_ARGS:
        raise ManifestInvalid(f"adapter.args exceeds {MAX_ARGS} entries", field="adapter.args")
    validated: list[str] = []
    for index, item in enumerate(args):
        if not isinstance(item, str):
            raise ManifestInvalid(
                f"adapter.args[{index}] must be a string", field="adapter.args"
            )
        if "\x00" in item:
            raise ManifestInvalid(
                f"adapter.args[{index}] contains a NUL byte", field="adapter.args"
            )
        if len(item) > MAX_ARG_CHARS:
            raise ManifestInvalid(
                f"adapter.args[{index}] exceeds {MAX_ARG_CHARS} characters", field="adapter.args"
            )
        validated.append(item)
    return tuple(validated)


def validate_bounded_text(
    value: object, *, field: str, limit: int = MAX_OUTPUT_CHARS, allow_none: bool = True
) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ManifestInvalid(f"{field} is required", field=field)
    if not isinstance(value, str):
        raise ManifestInvalid(f"{field} must be a string", field=field)
    if "\x00" in value:
        raise ManifestInvalid(f"{field} contains a NUL byte", field=field)
    if len(value) > limit:
        raise ManifestInvalid(f"{field} exceeds {limit} characters", field=field)
    return value


def validate_distinct_paths(paths: Iterable[tuple[str, str]]) -> None:
    seen: set[tuple[str, str]] = set()
    for path, mount in paths:
        key = (path, mount)
        if key in seen:
            raise ManifestInvalid(f"duplicate fixture declaration for {path} ({mount})")
        seen.add(key)


def unique_ordinals(ordinals: Sequence[int]) -> None:
    if len(set(ordinals)) != len(ordinals):
        raise ManifestInvalid("test case ordinals must be unique")
    if any(ordinal < 0 for ordinal in ordinals):
        raise ManifestInvalid("test case ordinals must be >= 0")
