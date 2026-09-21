"""Comparison-policy fitness diagnostics (deterministic, no model involved).

``compile/manifest.py:_validate_comparison`` answers "is this policy *legal*". This
module answers a different question: "is this policy likely to *misjudge* the content
it is attached to". Both are pure functions over declarations - no execution, no
reference answer is run here, and no finding ever quotes an expected output.

Every rule below is traced to a failure this project already hit while authoring the
bank (``MAIN/seed/question-bank/README.md``) or to a concrete gap in the declared
policy. A rule that cannot point at a real case does not ship: diagnostics that cry
wolf get ignored, and then they are worse than nothing.

Severity follows the extensible-rule shape of ``ADR-0005``: ``error`` means the policy
is internally inconsistent (the declaration cannot mean what it says), ``warning``
means a declared combination is known to have misjudged real content, ``info`` records
a residual risk the author should know about but which B2's comparator may absorb.

The three values are exactly the ``severity`` enum of ``ComparisonDiagnostics`` in
``MAIN/contracts/openapi/b3.yaml`` (CHG-0013). An internal fourth value (``note``) once
existed here; it was not one of the declared ones, so a report carrying it could not
have been serialized honestly - the finding is the same, the word a teacher reads is now
the word the contract promises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..dto.internal import (
    AdapterManifestInternal,
    TestCaseInternal,
)

#: Modes whose declared intent cannot be satisfied without a tolerance value.
TOLERANCE_MODES: frozenset[str] = frozenset({"numeric_tolerance"})


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Rule:
    """One diagnostic rule: a stable id, a severity and the failure it prevents."""

    rule_id: str
    severity: Severity
    title: str
    rationale: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    detail: str
    case_ordinal: int | None = None


@dataclass(frozen=True)
class DiagnosticsReport:
    """Findings for one version's declared policy, plus what was inspected."""

    adapter_kind: str
    cases_inspected: int
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity is Severity.ERROR)

    @property
    def passed(self) -> bool:
        """No ``error``-severity finding. ``warning``/``info`` never block a publish."""
        return not self.errors


#: The rule catalogue. Ids are stable: a report is only useful if a finding can be
#: looked up a year later.
RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="CMP-001",
        severity=Severity.ERROR,
        title="expected output carries CRLF line endings",
        rationale=(
            "The bank authors on Windows and runs on Linux. An expected output stored "
            "with CRLF was already recorded as a real failure: the reference answer "
            "emits LF, so an `exact` comparison can never match. It also breaks the "
            "`ignore_trailing_whitespace` intent, because a CR is not trailing "
            "whitespace to every comparator."
        ),
    ),
    Rule(
        rule_id="CMP-002",
        severity=Severity.WARNING,
        title="expected output contains non-ASCII characters",
        rationale=(
            "A GBK-vs-UTF-8 stdin decoding mismatch was already recorded as a real "
            "failure while authoring the bank: the reference answer decoded the "
            "fixture with the platform default encoding and produced mojibake, so the "
            "expected output could never be reproduced. The fix is an explicit "
            "encoding in the question, which a comparison policy cannot express - so "
            "this is reported for the author to confirm."
        ),
    ),
    Rule(
        rule_id="CMP-003",
        severity=Severity.INFO,
        title="exact comparison against an expected output with trailing whitespace",
        rationale=(
            "Trailing spaces or blank lines are invisible in review and are the most "
            "common cause of 'the student's output looks identical but fails'. B2's "
            "comparator strips line ends before comparing, so this may be harmless; it "
            "is reported because the *declaration* says exact while the content does "
            "not look intentional."
        ),
    ),
    Rule(
        rule_id="CMP-004",
        severity=Severity.ERROR,
        title="numeric_tolerance declared without a tolerance value",
        rationale=(
            "`numeric_tolerance` without `numeric_tolerance` set has no threshold to "
            "apply: the declaration promises approximate comparison and delivers "
            "undefined behaviour. Either name a tolerance or declare a different mode."
        ),
    ),
)

_RULES_BY_ID = {rule.rule_id: rule for rule in RULES}


def rule_by_id(rule_id: str) -> Rule:
    return _RULES_BY_ID[rule_id]


def analyse_comparison(
    manifest: AdapterManifestInternal,
    cases: tuple[TestCaseInternal, ...] | list[TestCaseInternal],
) -> DiagnosticsReport:
    """Return every finding for ``manifest`` against ``cases`` (deterministic order)."""
    findings: list[Finding] = []
    policy = manifest.comparison

    if policy.stdout in TOLERANCE_MODES and policy.numeric_tolerance is None:
        findings.append(
            Finding(
                rule_id="CMP-004",
                severity=Severity.ERROR,
                detail=(
                    "comparison.stdout is 'numeric_tolerance' but "
                    "comparison.numeric_tolerance is not set, so no tolerance applies"
                ),
            )
        )

    for case in cases:
        output = case.expected_stdout
        if output is None:
            continue
        if _has_crlf(output):
            findings.append(
                Finding(
                    rule_id="CMP-001",
                    severity=Severity.ERROR,
                    detail=(
                        "the expected stdout of this case contains CRLF line endings "
                        "while the harness canonicalises on LF"
                    ),
                    case_ordinal=case.ordinal,
                )
            )
        if _has_non_ascii(output):
            findings.append(
                Finding(
                    rule_id="CMP-002",
                    severity=Severity.WARNING,
                    detail=(
                        "the expected stdout of this case contains non-ASCII "
                        "characters; confirm the question declares its text encoding "
                        "explicitly"
                    ),
                    case_ordinal=case.ordinal,
                )
            )
        if policy.stdout == "exact" and output != output.rstrip():
            findings.append(
                Finding(
                    rule_id="CMP-003",
                    severity=Severity.INFO,
                    detail=(
                        "comparison.stdout is 'exact' and this case's expected stdout "
                        "ends in whitespace; an exact byte comparison will reject the "
                        "same output without it"
                    ),
                    case_ordinal=case.ordinal,
                )
            )

    return DiagnosticsReport(
        adapter_kind=manifest.kind,
        cases_inspected=len(cases),
        findings=tuple(findings),
    )


def _has_crlf(value: str) -> bool:
    return "\r" in value


def _has_non_ascii(value: str) -> bool:
    return not value.isascii()
