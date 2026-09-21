"""Sandbox policy defaults (``evaluation-package.schema.json`` -> ``security_policy``).

The three boolean constants are non-negotiable (ADR-0004): no network, read-only
root, never root. A question author cannot opt out, so this module exposes no
per-version override for them; only the numeric limits are adjustable inside the
contract's stated ranges.
"""

from __future__ import annotations

from ..dto.internal import SecurityPolicy
from ..errors import ManifestInvalid

#: Contract ranges from ``evaluation-package.schema.json``.
LIMITS: dict[str, tuple[float, float]] = {
    "max_memory_bytes": (16_777_216, 100_663_296),
    "max_cpu_seconds": (0.001, 30.0),
    "max_wall_seconds": (0.001, 60.0),
    "max_output_bytes": (1_024, 1_048_576),
    "max_processes": (1, 64),
    "max_files_created": (0, 10_000),
}

DEFAULT_POLICY = SecurityPolicy()


def build_policy(**overrides: float | int | None) -> SecurityPolicy:
    """Return the default policy with validated numeric overrides applied."""
    unknown = set(overrides) - set(LIMITS)
    if unknown:
        raise ManifestInvalid(
            "unknown security policy field(s): " + ", ".join(sorted(unknown))
        )
    values: dict[str, float | int | None] = {
        "max_memory_bytes": DEFAULT_POLICY.max_memory_bytes,
        "max_cpu_seconds": DEFAULT_POLICY.max_cpu_seconds,
        "max_wall_seconds": DEFAULT_POLICY.max_wall_seconds,
        "max_output_bytes": DEFAULT_POLICY.max_output_bytes,
        "max_processes": DEFAULT_POLICY.max_processes,
        "max_files_created": DEFAULT_POLICY.max_files_created,
    }
    for name, raw in overrides.items():
        if raw is None:
            values[name] = None
            continue
        low, high = LIMITS[name]
        if not low <= float(raw) <= high:
            raise ManifestInvalid(
                f"security policy {name}={raw} is outside the contract range [{low}, {high}]"
            )
        values[name] = raw
    return SecurityPolicy(
        max_memory_bytes=int(values["max_memory_bytes"] or DEFAULT_POLICY.max_memory_bytes),
        max_cpu_seconds=float(values["max_cpu_seconds"] or DEFAULT_POLICY.max_cpu_seconds),
        max_wall_seconds=float(values["max_wall_seconds"] or DEFAULT_POLICY.max_wall_seconds),
        max_output_bytes=int(values["max_output_bytes"] or DEFAULT_POLICY.max_output_bytes),
        max_processes=int(values["max_processes"] or DEFAULT_POLICY.max_processes),
        max_files_created=(
            None if values["max_files_created"] is None else int(values["max_files_created"])
        ),
    )
