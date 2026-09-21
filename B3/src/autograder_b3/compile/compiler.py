"""The package compiler: one evaluation package per version, published or draft.

Frozen by ADR-0005. Properties this module is responsible for:

* **deterministic** - the same content always yields the same ``package_digest``. Two values
  are deliberately **outside** that digest: the digest itself, and ``created_at``. The latter
  is metadata, not content (the contract says so), and covering it was a real defect -- see
  ``canonical.DIGEST_EXCLUDED_KEYS``. ``created_at`` is still required to equal the timestamp
  carried by the snapshot, but as a consistency property of the package rather than as
  something the digest's stability rests on. That timestamp is ``published_at`` for a published
  version and the content's own timestamp for a draft (``CHG-0028``), which is what makes two
  fetches of an unchanged draft compile to the same package;
* **single entry point** - every package in the system comes out of
  :func:`compile_evaluation_package`;
* **no kind branching** - dispatch happens through the adapter registry, which is
  what makes "adding a question type does not change the core" testable;
* **pure** - no database access, no filesystem access, no event emission. The
  caller (``domain.versions``, ``domain.packages``) does the I/O and hands in internal DTOs.

``EvaluationPackage.to_wire()`` output is validated against
``MAIN/contracts/schemas/evaluation-package.schema.json`` by the contract test, so
this module never restates the contract's field list.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from collections.abc import Mapping, Sequence

from ..dto.internal import (
    EvaluationPackage,
    PublishedVersionSnapshot,
    SecurityPolicy,
    TestCaseInternal,
)
from ..errors import ManifestInvalid
from .adapters import register_builtin_adapters
from .canonical import digest_without
from .manifest import assert_fixture_binding
from .policy import DEFAULT_POLICY
from .registry import adapter_compiler_for

register_builtin_adapters()

#: Keys of a ``tests[]`` entry, in the order the contract declares them.
TEST_ENTRY_KEYS: tuple[str, ...] = (
    "case_id",
    "name",
    "ordinal",
    "weight",
    "visible_to_student",
    "stdin",
    "args",
    "expected_stdout",
    "expected_stderr",
    "expected_exit_code",
)


def compute_package_digest(package: Mapping[str, Any]) -> str:
    """``sha256`` over the canonical JSON of ``package`` minus its metadata keys.

    Excludes ``package_digest`` (a digest cannot cover itself) and ``created_at`` (metadata,
    not content -- the contract says the digest covers "the package content", and B2's
    `result_digest` already drops its own timestamp). See
    ``canonical.DIGEST_EXCLUDED_KEYS`` for why this is *not* the explanation of any past
    invalidation: that was measured and traced elsewhere.
    """
    return digest_without(package)


def case_to_package_entry(case: TestCaseInternal) -> dict[str, Any]:
    """Map a protected test case onto a contract ``tests[]`` entry."""
    return {
        "case_id": str(case.case_id),
        "name": case.name,
        "ordinal": case.ordinal,
        "weight": case.weight,
        "visible_to_student": case.visible_to_student,
        "stdin": case.stdin,
        "args": list(case.args),
        "expected_stdout": case.expected_stdout,
        "expected_stderr": case.expected_stderr,
        "expected_exit_code": case.expected_exit_code,
    }


def compile_evaluation_package(
    snapshot: PublishedVersionSnapshot,
    manifest: Any,
    cases: Sequence[TestCaseInternal],
    fixtures: Sequence[Any],
    *,
    created_at: datetime,
    policy: SecurityPolicy | None = None,
) -> EvaluationPackage:
    """Compile ``snapshot`` + ``manifest`` + ``cases`` into a package."""
    if created_at != snapshot.published_at:
        raise ManifestInvalid(
            "created_at must equal the timestamp the snapshot carries: the package reports "
            "when the content it describes is from, and a package whose timestamp disagreed "
            "with its version would describe a moment that never happened"
        )
    if manifest.kind != snapshot.manifest.kind:
        raise ManifestInvalid(
            "manifest kind does not match the published version's adapter kind",
            field="adapter.kind",
        )
    if not cases:
        raise ManifestInvalid("a package needs at least one test case", field="tests")

    version_id = snapshot.question_version_id
    for case in cases:
        if case.question_version_id != version_id:
            raise ManifestInvalid("test case belongs to a different question version")
    for fixture in fixtures:
        if fixture.question_version_id != version_id:
            raise ManifestInvalid("fixture belongs to a different question version")

    # Registry dispatch (no kind branching here, by design).
    compiler = adapter_compiler_for(manifest.kind)
    compiler.validate(manifest, cases=cases, fixtures=fixtures)
    if fixtures:
        assert_fixture_binding(manifest, fixtures)

    adapter_section = compiler.to_package_section(manifest, cases=cases, fixtures=fixtures)
    tests = tuple(case_to_package_entry(case) for case in sorted(cases, key=lambda c: c.ordinal))

    draft = EvaluationPackage(
        question_version_id=version_id,
        package_digest="",
        language=snapshot.language,
        adapter=adapter_section,
        tests=tests,
        security_policy=policy or DEFAULT_POLICY,
        max_files=snapshot.max_files,
        max_total_bytes=snapshot.max_total_bytes,
        created_at=created_at,
    )
    digest = compute_package_digest(draft.to_wire())
    if len(digest) != 64:
        raise ManifestInvalid("package digest is not a sha256 hex string")
    return replace(draft, package_digest=digest)
