"""The adapter registry - B3's extension point (ADR-0005 §5).

``compile_evaluation_package`` and ``validate_adapter_manifest`` never branch on
the adapter kind; they dispatch through this registry. Adding a question type
therefore means implementing one :class:`AdapterCompiler` and registering it -
the compiler core and B2's runner core are both untouched.

A newly registered kind is still subject to the frozen contract: if the kind is
outside ``adapter.kind``'s enum in ``evaluation-package.schema.json`` the produced
package fails schema validation. That is a *contract* gate, not a compiler
coupling, and it is exactly the gate that must be negotiated through
``MAIN/docs/contracts/change-protocol.md``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from collections.abc import Sequence

from ..dto.internal import AdapterManifestInternal, FixtureInternal, TestCaseInternal
from ..errors import ManifestInvalid


@runtime_checkable
class AdapterCompiler(Protocol):
    """Per-kind validation and package-section construction."""

    kind: str

    def validate(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> None: ...

    def to_package_section(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> dict[str, Any]: ...


_REGISTRY: dict[str, AdapterCompiler] = {}


def register_adapter_compiler(compiler: AdapterCompiler) -> None:
    """Register (or replace) the compiler for ``compiler.kind``."""
    kind = getattr(compiler, "kind", None)
    if not isinstance(kind, str) or not kind:
        raise ManifestInvalid("adapter compiler must declare a non-empty kind")
    if not callable(getattr(compiler, "validate", None)) or not callable(
        getattr(compiler, "to_package_section", None)
    ):
        raise ManifestInvalid(f"adapter compiler {kind!r} must implement validate/to_package_section")
    _REGISTRY[kind] = compiler


def adapter_compiler_for(kind: str) -> AdapterCompiler:
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise ManifestInvalid(
            f"adapter kind {kind!r} is not registered (registered: {known})",
            field="adapter.kind",
        ) from exc


def registered_adapter_kinds() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def unregister_adapter_compiler(kind: str) -> None:
    """Test helper: drop a registration (used by the extension-point test)."""
    _REGISTRY.pop(kind, None)
