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

import re
import string
from typing import Any, get_args
from collections.abc import Mapping, Sequence
from uuid import UUID

from ..dto.internal import (
    AdapterKind,
    AdapterManifestInternal,
    CallableTarget,
    CaseExecution,
    ComparisonPolicy,
    DeclaredFile,
    EmailCheck,
    ExecutionEmail,
    ExecutionManifest,
    ExecutionMode,
    ExecutionSsh,
    FileCheck,
    FixtureInternal,
    SshCheck,
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

# --- execution declarations (Unix homework extension) ---------------------
#
# The rules below are the *B3 half* of the double-sided guard the design baseline asks for
# ("B3 编译期拒绝 + B2 解析期再拒绝"): B2 re-rejects at package parse time, so a package that
# somehow bypasses B3 still cannot select a mode the runner does not support.

EXECUTION_MODES: tuple[str, ...] = get_args(ExecutionMode)
SSH_CHECKS: tuple[str, ...] = get_args(SshCheck)
EMAIL_CHECKS: tuple[str, ...] = get_args(EmailCheck)
#: Adapter kinds a `single_run` package may use. `file` is here because a one-shot
#: file-editing question (fixtures in, workspace files checked afterwards) is exactly one
#: sandbox execution; `callable` is not, because its invocation model is per call.
SINGLE_RUN_ADAPTER_KINDS: tuple[str, ...] = ("stdin", "file")
MAX_FILE_CHECKS_PER_CASE = 20
SEGMENT_DELIMITER_MIN_CHARS = 8
SEGMENT_DELIMITER_MAX_CHARS = 120
#: The contract's pattern: printable ASCII, no control characters, and **no space at either
#: end** -- the marker must occupy a whole output line by itself, so trailing spaces would
#: make "the same marker" ambiguous.
_SEGMENT_DELIMITER_PATTERN = re.compile(r"^[!-~](?:[ -~]*[!-~])?$")
#: Canonical four-digit octal text (contract `FileCheckInput.mode`). The three-digit form was
#: tightened in the contract because the evaluator formats actual permission bits canonically;
#: B2's package parser already refuses "644", so B3 must too.
_FILE_MODE_PATTERN = re.compile(r"^0[0-7]{3}$")
_SSH_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SSH_HOST = "127.0.0.1"
MAX_SSH_PORT = 65_535
MAX_SSH_PASSWORD_CHARS = 128
MAX_EXECUTION_TEMPLATE_CHARS = 200
#: Placeholder vocabulary of the email templates. B2's package parser (the other half of the
#: double-sided guard) accepts exactly these two, so B3 refuses anything B2 would reject at
#: evaluation time instead of letting a publishable question fail later as a technical error.
EMAIL_TEMPLATE_PLACEHOLDERS: frozenset[str] = frozenset({"student_number", "attempt"})
MAX_TIMEOUT_SECONDS = 30.0
#: Lower bound kept in step with B2's package parser: the contract says "> 0" but B2 refuses
#: anything under 0.1s, and a declaration B2 refuses must not become publishable in B3.
MIN_TIMEOUT_SECONDS = 0.1
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_SUBJECT_TEMPLATE = "{student_number}_{attempt}"
DEFAULT_ATTACHMENT_TEMPLATE = "{student_number}.txt"


def _field(raw: Any, name: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(name, default)
    return getattr(raw, name, default)


def validate_adapter_manifest(
    raw: Mapping[str, Any] | Any,
    *,
    cases: Sequence[TestCaseInternal] = (),
    fixtures: Sequence[FixtureInternal] = (),
    require_runnable_comparison: bool = True,
) -> AdapterManifestInternal:
    """Validate and normalize an adapter manifest, or raise :class:`ManifestInvalid`.

    ``require_runnable_comparison`` exists for **one** caller: comparison diagnostics, whose
    job is to *report* a declaration the write path would refuse (``CMP-004``: stdout compares
    numerically while no tolerance was set). Every write/compile path keeps the default, so a
    declaration B2 would reject at package parse time cannot be stored or published through
    B3 either.
    """
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
        comparison=_validate_comparison(
            _field(raw, "comparison"), require_runnable=require_runnable_comparison
        ),
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
    require_runnable_comparison: bool = True,
) -> AdapterManifestInternal:
    """Validate a manifest that was persisted in ``b3.question_versions``.

    Same caveat as :func:`validate_adapter_manifest`: the lenient mode belongs to the
    diagnostics reader only.
    """
    if not stored:
        raise ManifestInvalid("question version has no adapter manifest", field="adapter")
    return validate_adapter_manifest(
        stored,
        cases=cases,
        fixtures=fixtures,
        require_runnable_comparison=require_runnable_comparison,
    )


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


def _validate_comparison(raw: Any, *, require_runnable: bool = True) -> ComparisonPolicy:
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
    elif stdout == "numeric_tolerance" and require_runnable:
        # B2's package parser refuses a numerically-compared package with no tolerance (the
        # comparison would have no tolerance to apply), so B3 must refuse it while the author
        # can still fix it. The one reader allowed to see this declaration is comparison
        # diagnostics, whose job is to report exactly this finding (CMP-004) on stored content.
        raise ManifestInvalid(
            "comparison.numeric_tolerance is required when comparison.stdout is "
            "'numeric_tolerance': without it the comparison has no tolerance to apply",
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
    # Path-only uniqueness: B2's package parser keys its fixture map by path and rejects the
    # same path under two mounts, so the stricter reading is enforced here too.
    validate_distinct_paths((entry.path for entry in declared), field="adapter.files")
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


# ==========================================================================
# Execution declarations (Unix homework extension)
# ==========================================================================
#
# One validation entry point for the whole execution vocabulary, mirroring
# ``validate_adapter_manifest``'s role: the draft write path, the publish path, the
# attestation path and the internal package path all validate here, so "the declaration
# the author saved", "the declaration the content digest covers" and "the declaration the
# runner parses" cannot disagree by construction.
#
# The compatibility rule these functions exist to protect: a default declaration
# (``per_case``) has **no wire form**. ``execution_manifest_to_storage`` returns ``None``
# for it, and the compiler/content-digest code only emits the ``execution`` key when the
# storage form is not ``None``. That is what keeps every already-published version's
# ``content_digest`` and ``package_digest`` reproducible (design baseline §8, "已有已发布
# 版本的内容摘要保持可复现"), and it is asserted by tests/test_execution.py.


def _is_record(raw: object) -> bool:
    """True for anything with named fields: a mapping, a pydantic model or a dataclass."""
    return (
        isinstance(raw, Mapping)
        or hasattr(raw, "model_fields")
        or hasattr(raw, "__dataclass_fields__")
    )


def _require_record(raw: object, *, field: str) -> None:
    if not _is_record(raw):
        raise ManifestInvalid(f"{field} must be an object", field=field)


def _require_int(value: object, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestInvalid(f"{field} must be an integer", field=field)
    if not minimum <= value <= maximum:
        raise ManifestInvalid(
            f"{field} must be between {minimum} and {maximum}", field=field
        )
    return value


def _require_timeout(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestInvalid(f"{field} must be a number of seconds", field=field)
    if not MIN_TIMEOUT_SECONDS <= float(value) <= MAX_TIMEOUT_SECONDS:
        raise ManifestInvalid(
            f"{field} must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS} seconds",
            field=field,
        )
    return float(value)


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        raise ManifestInvalid(
            f"{field} must be a lowercase sha256 hex digest", field=field
        )
    return value


def _require_enum(
    value: object, *, field: str, allowed: Sequence[str], allow_none: bool = False
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ManifestInvalid(
            f"{field} must be one of {tuple(allowed)}", field=field
        )
    return value


def _validate_segment_delimiter(value: object) -> str | None:
    """Validate the whole-line marker that splits one bounded stdout.

    "Occupies a whole line by itself" is enforced structurally: the length is bounded, the
    characters are printable ASCII, and neither end may be a space -- so a delimiter can
    never be a prefix/suffix of nearby output that merely ends or starts with spaces.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestInvalid(
            "execution.segment_delimiter must be a string", field="execution.segment_delimiter"
        )
    if not SEGMENT_DELIMITER_MIN_CHARS <= len(value) <= SEGMENT_DELIMITER_MAX_CHARS:
        raise ManifestInvalid(
            "execution.segment_delimiter must be "
            f"{SEGMENT_DELIMITER_MIN_CHARS}..{SEGMENT_DELIMITER_MAX_CHARS} characters",
            field="execution.segment_delimiter",
        )
    if "\x00" in value or not _SEGMENT_DELIMITER_PATTERN.match(value):
        raise ManifestInvalid(
            "execution.segment_delimiter must be printable ASCII with no leading or "
            "trailing whitespace",
            field="execution.segment_delimiter",
        )
    return value


def _validate_email_template(value: object, *, field: str) -> str:
    """A template B2 can render: printable text plus ``{student_number}``/``{attempt}`` only.

    The vocabulary and the "no format specifier" rule mirror B2's package parser exactly; a
    template B2 cannot render would make a published question fail at evaluation time as a
    technical error, which is the one class of failure this double-sided guard exists to
    prevent. An empty template is refused for the same reason: it would compare against an
    empty subject/attachment instead of the student's fact.
    """
    template = validate_bounded_text(
        value, field=field, limit=MAX_EXECUTION_TEMPLATE_CHARS, allow_none=False
    )
    assert template is not None
    if not template or any(not ch.isprintable() and ch != "\t" for ch in template):
        raise ManifestInvalid(
            f"{field} must be a non-empty printable single-line template", field=field
        )
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ManifestInvalid(f"{field} is not a valid format template", field=field) from exc
    seen_fields: set[str] = set()
    for _literal, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in EMAIL_TEMPLATE_PLACEHOLDERS:
            raise ManifestInvalid(
                f"{field} uses an unsupported placeholder: {field_name!r} "
                f"(allowed: {sorted(EMAIL_TEMPLATE_PLACEHOLDERS)})",
                field=field,
            )
        if format_spec or conversion:
            raise ManifestInvalid(
                f"{field} must not use format specifiers or conversions", field=field
            )
        if field_name in seen_fields:
            # The email rule engine renders the template one placeholder at a time, so a
            # repeated placeholder is ambiguous there even though Python's formatter allows it.
            raise ManifestInvalid(
                f"{field} repeats a placeholder: {field_name!r}", field=field
            )
        seen_fields.add(field_name)
    return template


def _validate_execution_email(raw: object) -> ExecutionEmail:
    _require_record(raw, field="execution.email")
    attempt = _require_int(
        _field(raw, "attempt"), field="execution.email.attempt", minimum=1, maximum=99
    )
    subject_template = _validate_email_template(
        _field(raw, "subject_template", DEFAULT_SUBJECT_TEMPLATE),
        field="execution.email.subject_template",
    )
    attachment_template = _validate_email_template(
        _field(raw, "required_attachment_template", DEFAULT_ATTACHMENT_TEMPLATE),
        field="execution.email.required_attachment_template",
    )
    return ExecutionEmail(
        attempt=attempt,
        subject_template=subject_template,
        required_attachment_template=attachment_template,
    )


def _validate_execution_ssh(raw: object) -> ExecutionSsh:
    _require_record(raw, field="execution.ssh")
    host = _field(raw, "host")
    if host != _SSH_HOST:
        # A fixed value, not a default: any other address would leave the isolated network
        # namespace the design baseline puts student code in (§7).
        raise ManifestInvalid(
            f"execution.ssh.host must be {_SSH_HOST!r}", field="execution.ssh.host"
        )
    port = _require_int(
        _field(raw, "port"), field="execution.ssh.port", minimum=1, maximum=MAX_SSH_PORT
    )
    username = _field(raw, "username")
    if not isinstance(username, str) or not _SSH_USERNAME_PATTERN.match(username):
        raise ManifestInvalid(
            "execution.ssh.username must match ^[a-z_][a-z0-9_-]{0,31}$",
            field="execution.ssh.username",
        )
    password = _field(raw, "password")
    if (
        not isinstance(password, str)
        or not password
        or len(password) > MAX_SSH_PASSWORD_CHARS
        or not password.isprintable()
    ):
        # Printable-only is B2's parser rule: a password with control characters can never be
        # typed into the practice prompt, so a package carrying one would fail at evaluation
        # rather than at authoring time.
        raise ManifestInvalid(
            "execution.ssh.password must be a non-empty printable string of at most "
            f"{MAX_SSH_PASSWORD_CHARS} characters",
            field="execution.ssh.password",
        )
    login_timeout = _require_timeout(
        _field(raw, "login_timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        field="execution.ssh.login_timeout_seconds",
    )
    session_timeout = _require_timeout(
        _field(raw, "session_timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        field="execution.ssh.session_timeout_seconds",
    )
    return ExecutionSsh(
        host=_SSH_HOST,
        port=port,
        username=username,
        password=password,
        login_timeout_seconds=login_timeout,
        session_timeout_seconds=session_timeout,
    )


def validate_execution_manifest(raw: Mapping[str, Any] | Any | None) -> ExecutionManifest:
    """Validate and normalize a version's execution declaration.

    ``None`` (a NULL column, or an explicit ``null`` on the write path) is the default
    ``per_case`` declaration. Each mode declares exactly which optional sections are
    required and which are forbidden, so a declaration cannot carry configuration nothing
    will read -- the design baseline's "模式字段互斥/必需" rule.
    """
    if raw is None:
        return ExecutionManifest()
    _require_record(raw, field="execution")
    mode = _field(raw, "mode")
    if mode not in EXECUTION_MODES:
        raise ManifestInvalid(
            f"execution.mode must be one of {EXECUTION_MODES}", field="execution.mode"
        )
    segment_delimiter = _validate_segment_delimiter(_field(raw, "segment_delimiter"))
    email_raw = _field(raw, "email")
    ssh_raw = _field(raw, "ssh")
    email = None if email_raw is None else _validate_execution_email(email_raw)
    ssh = None if ssh_raw is None else _validate_execution_ssh(ssh_raw)

    if mode == "per_case":
        if segment_delimiter is not None or email is not None or ssh is not None:
            raise ManifestInvalid(
                "execution.mode 'per_case' does not accept segment_delimiter/email/ssh; "
                "omit the declaration to keep the per-case behaviour",
                field="execution",
            )
    elif mode == "single_run":
        if email is not None or ssh is not None:
            raise ManifestInvalid(
                "execution.mode 'single_run' does not accept email or ssh configuration",
                field="execution",
            )
    elif mode == "ssh_session":
        if ssh is None:
            raise ManifestInvalid(
                "execution.mode 'ssh_session' requires the ssh section", field="execution.ssh"
            )
        if email is not None or segment_delimiter is not None:
            raise ManifestInvalid(
                "execution.mode 'ssh_session' does not accept email or segment_delimiter",
                field="execution",
            )
    else:  # email_delivery
        if email is None:
            raise ManifestInvalid(
                "execution.mode 'email_delivery' requires the email section",
                field="execution.email",
            )
        if ssh is not None or segment_delimiter is not None:
            raise ManifestInvalid(
                "execution.mode 'email_delivery' does not accept ssh or segment_delimiter",
                field="execution",
            )
    return ExecutionManifest(
        mode=mode,  # type: ignore[arg-type]
        segment_delimiter=segment_delimiter,
        email=email,
        ssh=ssh,
    )


def execution_from_stored(stored: Mapping[str, Any] | None) -> ExecutionManifest:
    """Validate a declaration read back from ``b3.question_versions.execution_manifest``."""
    return validate_execution_manifest(stored)


def is_default_execution(execution: ExecutionManifest) -> bool:
    """True for the legacy ``per_case`` declaration, which has no wire form."""
    return (
        execution.mode == "per_case"
        and execution.segment_delimiter is None
        and execution.email is None
        and execution.ssh is None
    )


def execution_manifest_to_storage(execution: ExecutionManifest) -> dict[str, Any] | None:
    """Serialize a validated declaration, or ``None`` when it is the default.

    ``None`` is the whole compatibility mechanism: it is what a NULL column means and it is
    why neither the content digest nor the evaluation package grows an ``execution`` key for
    content authored before the field existed.
    """
    if execution.mode == "per_case":
        if not is_default_execution(execution):  # pragma: no cover - validation forbids
            raise ManifestInvalid(
                "a per_case execution declaration must not carry configuration",
                field="execution",
            )
        return None
    payload: dict[str, Any] = {"mode": execution.mode}
    if execution.segment_delimiter is not None:
        payload["segment_delimiter"] = execution.segment_delimiter
    if execution.email is not None:
        payload["email"] = {
            "attempt": execution.email.attempt,
            "subject_template": execution.email.subject_template,
            "required_attachment_template": execution.email.required_attachment_template,
        }
    if execution.ssh is not None:
        payload["ssh"] = {
            "host": execution.ssh.host,
            "port": execution.ssh.port,
            "username": execution.ssh.username,
            "password": execution.ssh.password,
            "login_timeout_seconds": execution.ssh.login_timeout_seconds,
            "session_timeout_seconds": execution.ssh.session_timeout_seconds,
        }
    return payload


# --- per-case declarations ------------------------------------------------


def _validate_file_checks(raw: object, *, field: str) -> tuple[FileCheck, ...]:
    if raw is None or raw == ():
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ManifestInvalid(f"{field} must be an array", field=field)
    if len(raw) > MAX_FILE_CHECKS_PER_CASE:
        raise ManifestInvalid(
            f"{field} exceeds {MAX_FILE_CHECKS_PER_CASE} entries", field=field
        )
    checks: list[FileCheck] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(raw):
        _require_record(item, field=f"{field}[{index}]")
        path = validate_workspace_path(
            _field(item, "path"), field=f"{field}[{index}].path"
        )
        digest_raw = _field(item, "content_sha256")
        digest = (
            None
            if digest_raw is None
            else _require_sha256(digest_raw, field=f"{field}[{index}].content_sha256")
        )
        mode_raw = _field(item, "mode")
        if mode_raw is None:
            mode = None
        elif not isinstance(mode_raw, str) or not _FILE_MODE_PATTERN.match(mode_raw):
            raise ManifestInvalid(
                f"{field}[{index}].mode must be canonical four-digit octal permission bits "
                "like '0744'",
                field=f"{field}[{index}].mode",
            )
        else:
            mode = mode_raw
        if digest is None and mode is None:
            raise ManifestInvalid(
                f"{field}[{index}] asserts nothing: content_sha256 or mode is required",
                field=f"{field}[{index}]",
            )
        check = FileCheck(path=path, content_sha256=digest, mode=mode)
        if check.path in seen_paths:
            raise ManifestInvalid(
                f"{field} declares {check.path!r} more than once", field=field
            )
        seen_paths.add(check.path)
        checks.append(check)
    return tuple(checks)


def parse_case_execution(raw: Mapping[str, Any] | CaseExecution | None) -> CaseExecution:
    """Shape-check a stored/persisted per-case declaration (mode-independent).

    Mode-dependent admissibility is :func:`validate_case_execution`'s job; this function is
    what the repository uses to read ``b3.test_cases.execution``, so a malformed stored row
    becomes a loud ``ManifestInvalid`` instead of an ``AttributeError`` later.
    """
    if raw is None:
        return CaseExecution()
    _require_record(raw, field="tests[].execution")
    segment_raw = _field(raw, "segment")
    segment = (
        None
        if segment_raw is None
        else _require_int(
            segment_raw, field="tests[].execution.segment", minimum=0, maximum=1_000_000
        )
    )
    file_checks = _validate_file_checks(
        _field(raw, "file_checks", ()), field="tests[].execution.file_checks"
    )
    ssh_check = _require_enum(
        _field(raw, "ssh_check"),
        field="tests[].execution.ssh_check",
        allowed=SSH_CHECKS,
        allow_none=True,
    )
    email_check = _require_enum(
        _field(raw, "email_check"),
        field="tests[].execution.email_check",
        allowed=EMAIL_CHECKS,
        allow_none=True,
    )
    return CaseExecution(
        segment=segment,
        file_checks=file_checks,
        ssh_check=ssh_check,  # type: ignore[arg-type]
        email_check=email_check,  # type: ignore[arg-type]
    )


def validate_case_execution(
    raw: Mapping[str, Any] | CaseExecution | None, *, mode: str
) -> CaseExecution:
    """Shape-check one case's declaration **and** its admissibility for ``mode``."""
    execution = parse_case_execution(raw)
    field = "tests[].execution"
    if mode == "per_case":
        if execution.segment is not None:
            raise ManifestInvalid(
                "a 'per_case' case cannot declare `segment`: one case is one run, so there "
                "is no shared stdout to split",
                field=field,
            )
        if execution.ssh_check is not None or execution.email_check is not None:
            raise ManifestInvalid(
                "a 'per_case' case cannot declare ssh_check/email_check",
                field=field,
            )
    elif mode == "single_run":
        if execution.ssh_check is not None or execution.email_check is not None:
            raise ManifestInvalid(
                "a 'single_run' case cannot declare ssh_check/email_check",
                field=field,
            )
        has_segment = execution.segment is not None
        has_file_checks = bool(execution.file_checks)
        if has_segment == has_file_checks:
            raise ManifestInvalid(
                "a 'single_run' case must declare exactly one of `segment` or a non-empty "
                "`file_checks`",
                field=field,
            )
    elif mode == "ssh_session":
        if execution.segment is not None or execution.file_checks:
            # Both sides of the guard (B2's package parser included) treat this mode's
            # judgements as the three real server-side facts only; a workspace file check has
            # no meaning in the chrooted practice session and would be silently ignored.
            raise ManifestInvalid(
                "an 'ssh_session' case cannot declare segment/file_checks", field=field
            )
        if execution.email_check is not None:
            raise ManifestInvalid(
                "an 'ssh_session' case cannot declare `email_check`", field=field
            )
        if execution.ssh_check is None:
            raise ManifestInvalid(
                "an 'ssh_session' case must declare exactly one `ssh_check`", field=field
            )
    else:  # email_delivery
        if execution.segment is not None or execution.ssh_check is not None:
            raise ManifestInvalid(
                "an 'email_delivery' case cannot declare segment/ssh_check", field=field
            )
        if execution.file_checks:
            raise ManifestInvalid(
                "an 'email_delivery' case cannot declare `file_checks`: nothing is executed, "
                "so there is no workspace to check",
                field=field,
            )
        if execution.email_check is None:
            raise ManifestInvalid(
                "an 'email_delivery' case must declare exactly one `email_check`", field=field
            )
    return execution


def validate_case_executions(
    cases: Sequence[TestCaseInternal],
    *,
    execution: ExecutionManifest,
    adapter_kind: str,
    language: str,
) -> None:
    """Cross-case and adapter-level rules for a declaration plus its case set.

    This is the function both the draft write path and the compile path call: a declaration
    only means something relative to the cases it will judge, so "segment values are exactly
    0..N-1" and "each ssh/email check appears at most once" cannot be checked one case at a
    time.
    """
    mode = execution.mode
    for case in cases:
        validate_case_execution(case.execution, mode=mode)

    if mode == "per_case":
        return

    if mode == "single_run":
        if adapter_kind not in SINGLE_RUN_ADAPTER_KINDS:
            raise ManifestInvalid(
                "execution.mode 'single_run' requires adapter.kind "
                f"'stdin' or 'file' (got {adapter_kind!r})",
                field="execution.mode",
            )
        segment_cases = [case for case in cases if case.execution.segment is not None]
        if not segment_cases:
            # A *complete* single-run question that declares a marker no case uses would have
            # dead configuration, so an authoring mistake is refused. A draft with no cases at
            # all is incomplete by definition rather than wrong: it exists so the author can
            # save a declaration before filling in cases, and `publish` refuses it anyway.
            if cases and execution.segment_delimiter is not None:
                raise ManifestInvalid(
                    "execution.segment_delimiter was declared but no case splits output into "
                    "segments",
                    field="execution.segment_delimiter",
                )
            return
        if adapter_kind != "stdin":
            raise ManifestInvalid(
                "segment splitting compares stdout, so it requires adapter.kind 'stdin' "
                f"(got {adapter_kind!r})",
                field="execution.mode",
            )
        if execution.segment_delimiter is None:
            raise ManifestInvalid(
                "declaring `segment` on a case requires execution.segment_delimiter",
                field="execution.segment_delimiter",
            )
        values = sorted(case.execution.segment for case in segment_cases)
        expected = list(range(len(values)))
        if values != expected:
            raise ManifestInvalid(
                "case `segment` values must be the complete set 0..N-1 exactly once "
                f"(declared: {values})",
                field="tests[].execution.segment",
            )
        delimiter = execution.segment_delimiter
        for case in cases:
            if case.expected_stdout is None:
                continue
            if delimiter in case.expected_stdout.splitlines():
                raise ManifestInvalid(
                    "the segment delimiter appears as a whole line in a case's "
                    "expected_stdout; the marker must be unique to the split points",
                    field="execution.segment_delimiter",
                )
        return

    # ssh_session / email_delivery: a real shell session / a delivered shell command.
    if adapter_kind != "stdin" or language != "bash":
        raise ManifestInvalid(
            f"execution.mode {mode!r} requires adapter.kind 'stdin' and language 'bash' "
            f"(got kind={adapter_kind!r}, language={language!r})",
            field="execution.mode",
        )
    attribute = "ssh_check" if mode == "ssh_session" else "email_check"
    values = [
        getattr(case.execution, attribute)
        for case in cases
        if getattr(case.execution, attribute) is not None
    ]
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ManifestInvalid(
            f"{attribute} values appear more than once: {duplicates}; each real server-side "
            "fact may be judged by at most one case",
            field=f"tests[].execution.{attribute}",
        )


def case_execution_payload(execution: CaseExecution) -> dict[str, Any]:
    """The wire form of a case's declaration: only declared keys, nothing defaulted.

    Used for ``tests[]`` entries, for the ``execution`` object in ``b3.test_cases`` and for
    the case entries of the content digest, so the three representations of "what this case
    declares" are one shape rather than three that happen to agree.
    """
    payload: dict[str, Any] = {}
    if execution.segment is not None:
        payload["segment"] = execution.segment
    if execution.file_checks:
        payload["file_checks"] = [
            {
                "path": check.path,
                "content_sha256": check.content_sha256,
                "mode": check.mode,
            }
            for check in execution.file_checks
        ]
    if execution.ssh_check is not None:
        payload["ssh_check"] = execution.ssh_check
    if execution.email_check is not None:
        payload["email_check"] = execution.email_check
    return payload


def case_execution_to_storage(execution: CaseExecution) -> dict[str, Any] | None:
    """Serialize a case declaration for ``b3.test_cases.execution``; ``None`` when empty."""
    payload = case_execution_payload(execution)
    return payload or None


def case_execution_from_stored(
    stored: Mapping[str, Any] | None,
) -> CaseExecution:
    """Read ``b3.test_cases.execution`` back into the validated internal record."""
    return parse_case_execution(stored)


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
