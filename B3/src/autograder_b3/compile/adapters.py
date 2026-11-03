"""The three frozen adapters: stdin/argv, file I/O and callable (``RQ-B-3-40/50/60``).

Each class implements the same :class:`~autograder_b3.compile.registry.AdapterCompiler`
protocol, so all three produce the **same** package shape and differ only in the
``adapter`` section's kind-specific keys. No compiler here decides how a package
is assembled - that is ``compile_evaluation_package``'s job alone.

Conversion rules (frozen, ADR-0005 §3):

* every kind emits ``kind``, ``args``, ``stdin``, ``expect_exit_code`` and
  ``comparison`` so that B2 can treat the section uniformly;
* ``files`` carries *declared* digests (the stored digest is authoritative and is
  checked by the compiler before the section is built);
* ``callable`` is emitted only for ``kind=callable``.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Sequence

from ..dto.internal import (
    AdapterManifestInternal,
    FixtureInternal,
    TestCaseInternal,
)
from ..errors import ManifestInvalid
from .registry import register_adapter_compiler
from .validation import validate_args, validate_bounded_text


def _common_section(manifest: AdapterManifestInternal) -> dict[str, Any]:
    return {
        "kind": manifest.kind,
        "args": list(manifest.args),
        "stdin": manifest.stdin,
        "expect_exit_code": manifest.expect_exit_code,
        "comparison": {
            "stdout": manifest.comparison.stdout,
            "stderr": manifest.comparison.stderr,
            "numeric_tolerance": manifest.comparison.numeric_tolerance,
        },
    }


def _files_section(manifest: AdapterManifestInternal) -> list[dict[str, Any]]:
    return [
        {
            "path": declared.path,
            "content_sha256": declared.content_sha256,
            "mount": declared.mount,
        }
        for declared in manifest.files
    ]


def _only_input_mounts(manifest: AdapterManifestInternal, kind: str) -> None:
    if any(declared.mount != "input" for declared in manifest.files):
        raise ManifestInvalid(
            f"adapter kind {kind!r} may only mount fixtures as input",
            field="adapter.files",
        )


def _require_cases(cases: Sequence[TestCaseInternal]) -> None:
    if not cases:
        raise ManifestInvalid("a package needs at least one test case", field="tests")


class StdinArgvAdapterCompiler:
    """``RQ-B-3-40``: the submission reads its data from argv and/or stdin."""

    kind = "stdin"

    def validate(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> None:
        if manifest.callable is not None:
            raise ManifestInvalid(
                "adapter kind 'stdin' must not declare a callable entry point",
                field="adapter.callable",
            )
        _only_input_mounts(manifest, self.kind)
        validate_args(manifest.args)
        validate_bounded_text(manifest.stdin, field="adapter.stdin", limit=65_536)
        # 命令行题型的三个全局项（命令参数 / 全局标准输入 / 期望退出码）允许全部留空：
        # 每个用例自带输入就能评测，全局项只是"所有用例共用的默认值"，不该成为保存草稿的门槛。
        for case in cases:
            validate_args(case.args)
            validate_bounded_text(case.stdin, field=f"tests[{case.ordinal}].stdin", limit=65_536)

    def to_package_section(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> dict[str, Any]:
        _require_cases(cases)
        return _common_section(manifest)


class FileIoAdapterCompiler:
    """``RQ-B-3-50``: fixtures land in the workspace; produced files are compared.

    Comparison granularity is bounded by the frozen contract: produced files are
    declared as ``mount=expected_output`` entries carrying a content digest, so
    existence and content-digest comparison are expressible while a text-diff
    mode is not (``CHG-B3-0001``).

    **"At least one input fixture" is a package requirement, not a manifest requirement.**
    A file question cannot be *saved* without first existing: the browser uploads fixture
    bytes to ``POST /question-versions/{id}/fixtures``, which needs a ``question_version_id``,
    which only exists after the draft is created. Demanding a declared fixture at draft time
    therefore made a real file question impossible to author -- the write path refused the
    very first save that would have produced the id the upload needs. The requirement lives in
    :meth:`to_package_section` instead, where it is checked at compile time (so both
    ``publish`` and the verification package refuse an incomplete file question), and an
    ``expected_output`` entry still has to bind a digest as soon as it is declared.
    """

    kind = "file"

    def validate(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> None:
        if manifest.callable is not None:
            raise ManifestInvalid(
                "adapter kind 'file' must not declare a callable entry point",
                field="adapter.callable",
            )
        for declared in manifest.files:
            if declared.mount == "expected_output" and not declared.content_sha256:
                raise ManifestInvalid(
                    f"expected output {declared.path} must bind a content digest",
                    field="adapter.files",
                )
        validate_args(manifest.args)
        validate_bounded_text(manifest.stdin, field="adapter.stdin", limit=65_536)

    def to_package_section(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> dict[str, Any]:
        _require_cases(cases)
        if not any(declared.mount == "input" for declared in manifest.files):
            raise ManifestInvalid(
                "adapter kind 'file' must declare at least one input fixture before the "
                "question can be compiled or published",
                field="adapter.files",
            )
        section = _common_section(manifest)
        section["files"] = _files_section(manifest)
        return section


class CallableAdapterCompiler:
    """``RQ-B-3-60``: import inside the workspace, then invoke."""

    kind = "callable"

    def validate(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> None:
        if manifest.callable is None:
            raise ManifestInvalid(
                "adapter kind 'callable' must declare module and attribute",
                field="adapter.callable",
            )
        _only_input_mounts(manifest, self.kind)
        validate_args(manifest.args)
        validate_bounded_text(manifest.stdin, field="adapter.stdin", limit=65_536)

    def to_package_section(
        self,
        manifest: AdapterManifestInternal,
        *,
        cases: Sequence[TestCaseInternal],
        fixtures: Sequence[FixtureInternal],
    ) -> dict[str, Any]:
        _require_cases(cases)
        assert manifest.callable is not None  # guaranteed by validate()
        section = _common_section(manifest)
        section["callable"] = {
            "module": manifest.callable.module,
            "attribute": manifest.callable.attribute,
            "arguments_json": manifest.callable.arguments_json,
        }
        if manifest.files:
            section["files"] = _files_section(manifest)
        return section


def register_builtin_adapters() -> None:
    """Idempotently register the three frozen adapters."""
    register_adapter_compiler(StdinArgvAdapterCompiler())
    register_adapter_compiler(FileIoAdapterCompiler())
    register_adapter_compiler(CallableAdapterCompiler())


BUILTIN_ADAPTER_KINDS: tuple[str, ...] = ("stdin", "file", "callable")

register_builtin_adapters()
