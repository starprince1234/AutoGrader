"""The single adapter-manifest validation entry point (ADR-0005 §4).

Any caller - the draft write path, the publish path, the internal package endpoint
- validates a manifest here and nowhere else. The function accepts either the
pydantic write model (:class:`~autograder_b3.dto.internal.AdapterManifestInput`)
or the raw JSON mapping as it comes back out of ``jsonb``, so a round-tripped
manifest is validated under exactly the same rules.

``cases``/``fixtures`` are optional: when supplied (publish time, compile time)
declared fixtures are cross-checked against stored content, which is what makes
"fixture 摘要绑定进 package" more than a claim.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence
from uuid import UUID

from ..dto.internal import (
    AdapterKind,
    AdapterManifestInternal,
    CallableTarget,
    ComparisonPolicy,
    DeclaredFile,
    FixtureInternal,
    TestCaseInternal,
)
from ..errors import ManifestInvalid
from . import adapters as _adapters  # noqa: F401  (import registers the built-ins)
from .registry import adapter_compiler_for, registered_adapter_kinds
from .validation import (
    validate_args,
    validate_arguments_json,
    validate_attribute_identifier,
    validate_bounded_text,
    validate_distinct_paths,
    validate_module_identifier,
    validate_workspace_path,
)

COMPARE_STDOUT_MODES = ("exact", "ignore_trailing_whitespace", "numeric_tolerance", "ignored")
COMPARE_STDERR_MODES = ("exact", "ignore_trailing_whitespace", "ignored")
MAX_FILES_PER_VERSION = 50
MAX_EXIT_CODE = 255
MIN_EXIT_CODE = -128


def _field(raw: Any, name: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(name, default)
    return getattr(raw, name, default)


def validate_adapter_manifest(
    raw: Mapping[str, Any] | Any,
    *,
    cases: Sequence[TestCaseInternal] = (),
    fixtures: Sequence[FixtureInternal] = (),
) -> AdapterManifestInternal:
    """Validate and normalize an adapter manifest, or raise :class:`ManifestInvalid`."""
    kind = _field(raw, "kind")
    if not isinstance(kind, str) or not kind:
        raise ManifestInvalid("adapter.kind is required", field="adapter.kind")
    if kind not in registered_adapter_kinds():
        raise ManifestInvalid(
            f"adapter kind {kind!r} is not supported "
            f"(supported: {', '.join(registered_adapter_kinds())})",
            field="adapter.kind",
        )

    manifest = AdapterManifestInternal(
        kind=kind,  # type: ignore[arg-type]
        args=validate_args(_field(raw, "args", ())),
        stdin=validate_bounded_text(_field(raw, "stdin"), field="adapter.stdin", limit=65_536),
        expect_exit_code=_validate_exit_code(_field(raw, "expect_exit_code"), "adapter.expect_exit_code"),
        comparison=_validate_comparison(_field(raw, "comparison")),
        files=_validate_files(_field(raw, "files", ())),
        callable=_validate_callable(_field(raw, "callable")),
    )

    adapter_compiler_for(manifest.kind).validate(manifest, cases=cases, fixtures=fixtures)
    if fixtures:
        assert_fixture_binding(manifest, fixtures)
    _assert_case_ownership(cases)
    return manifest


def manifest_from_stored(
    stored: Mapping[str, Any] | None,
    *,
    cases: Sequence[TestCaseInternal] = (),
    fixtures: Sequence[FixtureInternal] = (),
) -> AdapterManifestInternal:
    """Validate a manifest that was persisted in ``b3.question_versions``."""
    if not stored:
        raise ManifestInvalid("question version has no adapter manifest", field="adapter")
    return validate_adapter_manifest(stored, cases=cases, fixtures=fixtures)


def assert_fixture_binding(
    manifest: AdapterManifestInternal, fixtures: Sequence[FixtureInternal]
) -> None:
    """Declared fixtures must match stored fixtures exactly, digest included.

    B3 computes fixture digests from bytes; a client-declared digest is only ever
    cross-checked, never trusted as the authoritative value (``RQ-B-3-50``
    acceptance 2).
    """
    stored = {(fixture.path, fixture.mount): fixture for fixture in fixtures}
    declared = {(entry.path, entry.mount): entry for entry in manifest.files}

    undeclared = sorted(set(stored) - set(declared))
    if undeclared:
        raise ManifestInvalid(
            "stored fixtures are missing from the manifest: "
            + ", ".join(f"{path} ({mount})" for path, mount in undeclared),
            field="adapter.files",
        )
    for key, entry in declared.items():
        fixture = stored.get(key)
        if fixture is None:
            raise ManifestInvalid(
                f"declared fixture {entry.path} ({entry.mount}) has no stored content",
                field="adapter.files",
            )
        if fixture.content_sha256 != entry.content_sha256:
            raise ManifestInvalid(
                f"declared digest for {entry.path} does not match the stored content digest",
                field="adapter.files",
            )


def _assert_case_ownership(cases: Sequence[TestCaseInternal]) -> None:
    version_ids = {case.question_version_id for case in cases}
    if len(version_ids) > 1:
        raise ManifestInvalid("test cases belong to more than one question version")


def _validate_exit_code(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestInvalid(f"{field} must be an integer", field=field)
    if not MIN_EXIT_CODE <= value <= MAX_EXIT_CODE:
        raise ManifestInvalid(
            f"{field} must be between {MIN_EXIT_CODE} and {MAX_EXIT_CODE}", field=field
        )
    return value


def _validate_comparison(raw: Any) -> ComparisonPolicy:
    if raw is None:
        return ComparisonPolicy()
    stdout = _field(raw, "stdout", "exact")
    stderr = _field(raw, "stderr", "ignored")
    tolerance = _field(raw, "numeric_tolerance")
    if stdout not in COMPARE_STDOUT_MODES:
        raise ManifestInvalid(
            f"comparison.stdout {stdout!r} is not one of {COMPARE_STDOUT_MODES}",
            field="adapter.comparison.stdout",
        )
    if stderr not in COMPARE_STDERR_MODES:
        raise ManifestInvalid(
            f"comparison.stderr {stderr!r} is not one of {COMPARE_STDERR_MODES}",
            field="adapter.comparison.stderr",
        )
    if tolerance is not None:
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
            raise ManifestInvalid(
                "comparison.numeric_tolerance must be a number", field="adapter.comparison.numeric_tolerance"
            )
        if tolerance < 0:
            raise ManifestInvalid(
                "comparison.numeric_tolerance must be >= 0",
                field="adapter.comparison.numeric_tolerance",
            )
        if stdout != "numeric_tolerance":
            raise ManifestInvalid(
                "comparison.numeric_tolerance is only meaningful when stdout is 'numeric_tolerance'",
                field="adapter.comparison",
            )
    return ComparisonPolicy(stdout=stdout, stderr=stderr, numeric_tolerance=tolerance)


def _validate_files(raw: Any) -> tuple[DeclaredFile, ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ManifestInvalid("adapter.files must be an array", field="adapter.files")
    if len(raw) > MAX_FILES_PER_VERSION:
        raise ManifestInvalid(
            f"adapter.files exceeds {MAX_FILES_PER_VERSION} entries", field="adapter.files"
        )
    declared: list[DeclaredFile] = []
    for index, item in enumerate(raw):
        path = validate_workspace_path(_field(item, "path"), field=f"adapter.files[{index}].path")
        digest = _field(item, "content_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            ch not in "0123456789abcdef" for ch in digest
        ):
            raise ManifestInvalid(
                f"adapter.files[{index}].content_sha256 must be a lowercase sha256 hex digest",
                field="adapter.files",
            )
        mount = _field(item, "mount", "input")
        if mount not in ("input", "expected_output"):
            raise ManifestInvalid(
                f"adapter.files[{index}].mount must be 'input' or 'expected_output'",
                field="adapter.files",
            )
        declared.append(DeclaredFile(path=path, content_sha256=digest, mount=mount))
    validate_distinct_paths((entry.path, entry.mount) for entry in declared)
    return tuple(declared)


def _validate_callable(raw: Any) -> CallableTarget | None:
    if raw is None:
        return None
    if not isinstance(raw, (Mapping,)) and not hasattr(raw, "module"):
        raise ManifestInvalid("adapter.callable must be an object", field="adapter.callable")
    return CallableTarget(
        module=validate_module_identifier(_field(raw, "module")),
        attribute=validate_attribute_identifier(_field(raw, "attribute")),
        arguments_json=validate_arguments_json(_field(raw, "arguments_json")),
    )


def manifest_to_storage(manifest: AdapterManifestInternal) -> dict[str, Any]:
    """Serialize a validated manifest for ``b3.question_versions.adapter_manifest``."""
    payload: dict[str, Any] = {
        "kind": manifest.kind,
        "args": list(manifest.args),
        "stdin": manifest.stdin,
        "expect_exit_code": manifest.expect_exit_code,
        "comparison": {
            "stdout": manifest.comparison.stdout,
            "stderr": manifest.comparison.stderr,
            "numeric_tolerance": manifest.comparison.numeric_tolerance,
        },
        "files": [
            {"path": entry.path, "content_sha256": entry.content_sha256, "mount": entry.mount}
            for entry in manifest.files
        ],
    }
    if manifest.callable is not None:
        payload["callable"] = {
            "module": manifest.callable.module,
            "attribute": manifest.callable.attribute,
            "arguments_json": manifest.callable.arguments_json,
        }
    return payload


def declared_kind(raw: Mapping[str, Any] | Any) -> AdapterKind | None:
    kind = _field(raw, "kind")
    return kind if isinstance(kind, str) else None


def case_owner(cases: Sequence[TestCaseInternal]) -> UUID | None:
    if not cases:
        return None
    return cases[0].question_version_id
