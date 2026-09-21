"""Real example questions for the three test frameworks (``RQ-B-3-40/50/60``).

These are the "端到端示例题目" the acceptance criteria ask for, expressed as the
manifests B3 actually compiles. Kept in one module so the same examples are used by
the compiler tests and by the API-level publish tests - an example that only exists
in a doc proves nothing.

Fixture digests below are computed from the literal bytes in this file, so they are
the real digests of real content rather than made-up hex strings.
"""

from __future__ import annotations

import hashlib
import uuid

from autograder_b3.dto.internal import DeclaredFile, FixtureInternal, TestCaseInternal

# --- example 1: Unix line counter (bash, stdin/argv) ----------------------

LINE_COUNTER_BASH = {
    "title": "Count the lines of the input (bash)",
    "language": "bash",
    "stem_markdown": "Write a bash script that prints the number of lines it reads on stdin.",
    "reference_answer": "#!/usr/bin/env bash\nwc -l < /dev/stdin | tr -d ' '\n",
}

STDIN_MANIFEST = {
    "kind": "stdin",
    "args": ["--mode", "count"],
    "stdin": "a\nb\nc\n",
    "expect_exit_code": 0,
    "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored", "numeric_tolerance": None},
}

STDIN_BASH_EXAMPLE = {
    **LINE_COUNTER_BASH,
    "adapter": STDIN_MANIFEST,
    "cases": [
        {
            "name": "three lines",
            "ordinal": 0,
            "weight": 1,
            "visible_to_student": True,
            "stdin": "a\nb\nc\n",
            "args": [],
            "expected_stdout": "3\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
        {
            "name": "hidden edge case: empty input",
            "ordinal": 1,
            "weight": 2,
            "visible_to_student": False,
            "stdin": "",
            "args": [],
            "expected_stdout": "0\n",
            "expected_stderr": None,
            "expected_exit_code": 0,
        },
    ],
}

# --- example 2: directory-tree report (file I/O) --------------------------

INPUT_FIXTURE_BYTES = b"alpha,2\nbeta,5\ngamma,1\n"
EXPECTED_FIXTURE_BYTES = b"alpha=2;beta=5;gamma=1;\n"
INPUT_FIXTURE_SHA = hashlib.sha256(INPUT_FIXTURE_BYTES).hexdigest()
EXPECTED_FIXTURE_SHA = hashlib.sha256(EXPECTED_FIXTURE_BYTES).hexdigest()

FILE_MANIFEST = {
    "kind": "file",
    "args": ["data/input.csv", "out/report.txt"],
    "stdin": None,
    "expect_exit_code": 0,
    "comparison": {"stdout": "ignored", "stderr": "ignored", "numeric_tolerance": None},
    "files": [
        {"path": "data/input.csv", "content_sha256": INPUT_FIXTURE_SHA, "mount": "input"},
        {"path": "out/report.txt", "content_sha256": EXPECTED_FIXTURE_SHA, "mount": "expected_output"},
    ],
}

FILE_IO_EXAMPLE = {
    "title": "Rewrite a CSV as a report file",
    "language": "python",
    "stem_markdown": "Read `data/input.csv` and write `out/report.txt` in the documented format.",
    "reference_answer": (
        "from pathlib import Path\n"
        "rows = [line.split(',') for line in Path('data/input.csv').read_text().splitlines()]\n"
        "Path('out/report.txt').write_text(''.join(f'{k}={v};' for k, v in rows) + '\\n')\n"
    ),
    "adapter": FILE_MANIFEST,
    "cases": [
        {
            "name": "produces the report file",
            "ordinal": 0,
            "weight": 1,
            "visible_to_student": False,
            "stdin": None,
            "args": [],
            "expected_stdout": None,
            "expected_stderr": None,
            "expected_exit_code": 0,
        }
    ],
}

# --- example 3: Python callable ------------------------------------------

CALLABLE_EXAMPLE = {
    "title": "Implement add(a, b)",
    "language": "python",
    "stem_markdown": "Implement `add(a, b)` in `solution/arithmetic.py` returning the sum.",
    "reference_answer": "def add(a, b):\n    return a + b\n",
}

CALLABLE_MANIFEST = {
    "kind": "callable",
    "args": [],
    "stdin": None,
    "expect_exit_code": None,
    "comparison": {"stdout": "ignored", "stderr": "ignored", "numeric_tolerance": None},
    "callable": {
        "module": "solution.arithmetic",
        "attribute": "add",
        "arguments_json": "[1, 4]",
    },
}

CALLABLE_FIXTURE_CASES = [
    {
        "name": "adds two positive numbers",
        "ordinal": 0,
        "weight": 1,
        "visible_to_student": True,
        "stdin": None,
        "args": [],
        "expected_stdout": "5\n",
        "expected_stderr": None,
        "expected_exit_code": 0,
    }
]


def build_cases(question_version_id: uuid.UUID, rows: list[dict]) -> tuple[TestCaseInternal, ...]:
    """Materialize example cases as protected internal DTOs."""
    cases = []
    for row in rows:
        cases.append(
            TestCaseInternal(
                case_id=uuid.uuid4(),
                question_version_id=question_version_id,
                name=row.get("name", "case"),
                ordinal=row.get("ordinal", 0),
                weight=row.get("weight", 1.0),
                visible_to_student=row.get("visible_to_student", False),
                stdin=row.get("stdin"),
                args=tuple(row.get("args", ())),
                expected_stdout=row.get("expected_stdout"),
                expected_stderr=row.get("expected_stderr"),
                expected_exit_code=row.get("expected_exit_code"),
            )
        )
    return tuple(cases)


def build_fixtures(
    question_version_id: uuid.UUID, declared: tuple[DeclaredFile, ...]
) -> tuple[FixtureInternal, ...]:
    """Stored fixtures matching the declared digests (as the repository would return)."""
    sizes = {
        INPUT_FIXTURE_SHA: len(INPUT_FIXTURE_BYTES),
        EXPECTED_FIXTURE_SHA: len(EXPECTED_FIXTURE_BYTES),
    }
    return tuple(
        FixtureInternal(
            fixture_id=uuid.uuid4(),
            question_version_id=question_version_id,
            path=item.path,
            mount=item.mount,
            content_sha256=item.content_sha256,
            size_bytes=sizes.get(item.content_sha256, 1),
        )
        for item in declared
    )


def fixture_content(content_sha256: str) -> bytes:
    return {
        INPUT_FIXTURE_SHA: INPUT_FIXTURE_BYTES,
        EXPECTED_FIXTURE_SHA: EXPECTED_FIXTURE_BYTES,
    }[content_sha256]
