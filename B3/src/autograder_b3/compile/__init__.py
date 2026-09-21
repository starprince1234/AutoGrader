"""Package compilation layer: manifest validation, adapters, compiler.

Import surface (frozen, ADR-0005):

* :func:`compile_evaluation_package`, :func:`compute_package_digest`
* :func:`validate_adapter_manifest`, :func:`manifest_from_stored`,
  :func:`manifest_to_storage`
* :func:`register_adapter_compiler`, :func:`adapter_compiler_for`,
  :func:`registered_adapter_kinds`
"""

from __future__ import annotations

from .adapters import (
    BUILTIN_ADAPTER_KINDS,
    CallableAdapterCompiler,
    FileIoAdapterCompiler,
    StdinArgvAdapterCompiler,
    register_builtin_adapters,
)
from .canonical import canonical_json_bytes, digest_of, digest_without, sha256_hex
from .compiler import case_to_package_entry, compile_evaluation_package, compute_package_digest
from .manifest import (
    assert_fixture_binding,
    manifest_from_stored,
    manifest_to_storage,
    validate_adapter_manifest,
)
from .policy import DEFAULT_POLICY, build_policy
from .registry import (
    AdapterCompiler,
    adapter_compiler_for,
    register_adapter_compiler,
    registered_adapter_kinds,
    unregister_adapter_compiler,
)

__all__ = [
    "AdapterCompiler",
    "BUILTIN_ADAPTER_KINDS",
    "CallableAdapterCompiler",
    "DEFAULT_POLICY",
    "FileIoAdapterCompiler",
    "StdinArgvAdapterCompiler",
    "adapter_compiler_for",
    "assert_fixture_binding",
    "build_policy",
    "canonical_json_bytes",
    "case_to_package_entry",
    "compile_evaluation_package",
    "compute_package_digest",
    "digest_of",
    "digest_without",
    "manifest_from_stored",
    "manifest_to_storage",
    "register_adapter_compiler",
    "register_builtin_adapters",
    "registered_adapter_kinds",
    "sha256_hex",
    "unregister_adapter_compiler",
    "validate_adapter_manifest",
]
