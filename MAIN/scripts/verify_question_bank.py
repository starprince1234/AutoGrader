"""Verify a question bank by really executing every reference answer.

Why this exists: a question whose own reference answer does not reproduce its expected
output is a broken question, and nothing in the platform catches that before a student
sees it. This gate runs each case's input through its problem's reference answer and
compares the result under the problem's declared comparison policy.

**The comparison and the execution environment are faithful ports, not approximations**,
because "passes this script" has to mean the same thing as "passes the platform's grader":

* `normalize_trailing_whitespace` mirrors
  `B2/src/autograder_b2/runner/comparator.py` (split on "\\n", drop trailing empty lines,
  rstrip **each** line). An earlier version rstripped the whole string, which is harsher
  than the authority and produced phantom failures.
* The child runs with `B2/.../runner/policy.py::SANDBOX_ENV_FIXED` mirrored
  (`LANG`/`LC_ALL=C.UTF-8`, `PYTHONIOENCODING=utf-8`). Without it this box's GBK Python
  computes different answers than the C.UTF-8 sandbox: `len("中文输入")` is 4 under the
  sandbox and 7 under GBK, so a non-ASCII problem could pass here and fail on the runner.

Known limitation of the local harness (not of the platform): `bash` is reached through
`bash.exe` (WSL), whose Windows->Linux interop layer re-parses the command line, so an
argument containing `|`, `;`, `&` or `$` is mangled. The Linux runner passes argv directly
and has no such limit. Bank authors therefore keep shell arguments inside an inert
character set; that is a narrower surface than the platform allows.

Usage:
    python verify_question_bank.py <file.json> [...]     # explicit files
    python verify_question_bank.py --bank-dir <dir>       # every *.json in a directory
    python verify_question_bank.py                        # the default bank

Exit code 0 only when every case of every problem passes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_MAIN = Path(__file__).resolve().parents[1]
DEFAULT_BANK_DIR = REPO_MAIN / "seed" / "question-bank"
#: Case workspaces are scratch, so they live under the gitignored runtime directory rather
#: than next to the bank (a bank that accumulates `bankcase-*` litter looks like a leak).
WORK_ROOT = REPO_MAIN / ".runtime" / "question-bank-work"
PYTHON = REPO_MAIN / ".venv" / "Scripts" / "python.exe"

#: Mirror of `B2/src/autograder_b2/runner/policy.py::SANDBOX_ENV_FIXED`.
SANDBOX_ENV_MIRROR = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "utf-8",
    # Python's UTF-8 mode, so `open()` and the filesystem encoding also behave as they do
    # under a C.UTF-8 locale rather than following this box's code page.
    "PYTHONUTF8": "1",
}

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover - depends on the host console
    pass


def wsl_path(path: Path) -> str:
    """Windows path -> WSL path, so `bash.exe` can read it."""
    drive = path.drive.rstrip(":").lower()
    return f"/mnt/{drive}" + str(path)[len(path.drive):].replace("\\", "/")


def normalize_trailing_whitespace(text: str) -> str:
    """Mirror of `B2/src/autograder_b2/runner/comparator.py::normalize_trailing_whitespace`."""
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(line.rstrip() for line in lines)


def _as_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def compare(actual: str, expected: str | None, mode: str, tolerance: float | None) -> str | None:
    """Return a mismatch description, or None when the value satisfies the policy."""
    if mode == "ignored":
        return None
    if expected is None:
        expected = ""

    if mode == "exact":
        return None if actual == expected else "exact mismatch"

    if mode == "ignore_trailing_whitespace":
        matched = normalize_trailing_whitespace(actual) == normalize_trailing_whitespace(expected)
        return None if matched else "mismatch after ignoring trailing whitespace"

    if mode == "numeric_tolerance":
        if tolerance is None:
            return "numeric_tolerance requires a tolerance value"
        actual_lines = normalize_trailing_whitespace(actual).split("\n")
        expected_lines = normalize_trailing_whitespace(expected).split("\n")
        if len(actual_lines) != len(expected_lines):
            return (
                f"line count differs (actual {len(actual_lines)}"
                f" vs expected {len(expected_lines)})"
            )
        for line_index, (actual_line, expected_line) in enumerate(
            zip(actual_lines, expected_lines, strict=True)
        ):
            actual_tokens = actual_line.split()
            expected_tokens = expected_line.split()
            if len(actual_tokens) != len(expected_tokens):
                return f"line {line_index}: token count differs"
            for token_index, (actual_token, expected_token) in enumerate(
                zip(actual_tokens, expected_tokens, strict=True)
            ):
                actual_number = _as_float(actual_token)
                expected_number = _as_float(expected_token)
                if actual_number is not None and expected_number is not None:
                    if abs(actual_number - expected_number) > float(tolerance):
                        return (
                            f"line {line_index} token {token_index}:"
                            f" |{actual_number} - {expected_number}| > {tolerance}"
                        )
                elif actual_token != expected_token:
                    return f"line {line_index} token {token_index}: literal mismatch"
        return None

    return f"unknown comparison mode {mode!r}"


def run_case(problem: dict, case: dict, workdir: Path) -> tuple[str, str, int]:
    language = problem["language"]
    stdin_text = case["stdin"] if case.get("stdin") is not None else problem["adapter"].get("stdin")
    args = case.get("args") or problem["adapter"].get("args") or []

    if language == "python":
        script = workdir / "solution.py"
        script.write_text(problem["reference_answer"], encoding="utf-8", newline="\n")
        command = [str(PYTHON), str(script), *args]
    elif language == "bash":
        script = workdir / "solution.sh"
        script.write_text(problem["reference_answer"], encoding="utf-8", newline="\n")
        command = ["bash", wsl_path(script), *args]
    else:
        raise ValueError(f"unsupported language {language!r}")

    completed = subprocess.run(
        command,
        input=(stdin_text or "").encode("utf-8"),
        capture_output=True,
        cwd=str(workdir),
        timeout=60,
        env={**os.environ, **SANDBOX_ENV_MIRROR},
    )
    return (
        completed.stdout.decode("utf-8", "replace"),
        completed.stderr.decode("utf-8", "replace"),
        completed.returncode,
    )


def remove_tree(path: Path) -> None:
    """Remove a case workspace, tolerating Windows' lagging handles."""
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 4:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.15 * (attempt + 1))


def sweep_stale_workspaces(max_age_seconds: float = 1800.0) -> None:
    """Delete `bankcase-*` directories left by earlier runs (never a live one)."""
    if not WORK_ROOT.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    for stale in WORK_ROOT.glob("bankcase-*"):
        try:
            if stale.is_dir() and stale.stat().st_mtime < cutoff:
                remove_tree(stale)
        except OSError:  # pragma: no cover - concurrent run
            continue


def install_fixtures(problem: dict, workdir: Path) -> None:
    for fixture in problem.get("fixtures", []):
        target = workdir / fixture["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the JSON-declared bytes exactly as written.
        target.write_text(fixture["content"], encoding="utf-8", newline="")
        assert len(hashlib.sha256(fixture["content"].encode("utf-8")).hexdigest()) == 64


def verify_problem(problem: dict, index: int) -> list[str]:
    failures: list[str] = []
    label = problem.get("title") or f"#{index}"
    cases = problem.get("cases") or []
    if not cases:
        return [f"{label}: no cases"]
    if cases[0].get("visible_to_student") is not True:
        failures.append(f"{label}: case 0 must be visible_to_student=true")
    for position, case in enumerate(cases):
        if case.get("ordinal") != position:
            failures.append(f"{label}: case {position} ordinal is {case.get('ordinal')!r}")
        if position > 0 and case.get("visible_to_student") is not False:
            failures.append(f"{label}: case {position} must be hidden")

    for case in cases:
        workdir = Path(tempfile.mkdtemp(prefix="bankcase-", dir=WORK_ROOT))
        try:
            install_fixtures(problem, workdir)
            try:
                stdout, stderr, code = run_case(problem, case, workdir)
            except subprocess.TimeoutExpired:
                failures.append(f"{label}: case {case['ordinal']} {case['name']!r}: TIMEOUT")
                continue
            policy = problem["adapter"]["comparison"]
            stdout_problem = compare(
                stdout,
                case.get("expected_stdout"),
                policy.get("stdout", "ignore_trailing_whitespace"),
                policy.get("numeric_tolerance"),
            )
            if stdout_problem:
                failures.append(
                    f"{label}: case {case['ordinal']} {case['name']!r}: stdout {stdout_problem}\n"
                    f"         expected: {case.get('expected_stdout')!r}\n"
                    f"         actual:   {stdout!r}"
                )
                continue
            stderr_problem = compare(
                stderr, case.get("expected_stderr"), policy.get("stderr", "ignored"), None
            )
            if stderr_problem:
                failures.append(
                    f"{label}: case {case['ordinal']} {case['name']!r}: stderr {stderr_problem}\n"
                    f"         expected: {case.get('expected_stderr')!r}\n"
                    f"         actual:   {stderr!r}"
                )
                continue
            expected_code = case.get("expected_exit_code")
            if expected_code is not None and code != expected_code:
                failures.append(
                    f"{label}: case {case['ordinal']} {case['name']!r}: exit {code} != {expected_code}"
                    f"\n         stderr: {stderr[:200]!r}"
                )
        finally:
            remove_tree(workdir)
    return failures


def collect_files(arguments: list[str]) -> list[Path]:
    if not arguments:
        return sorted(DEFAULT_BANK_DIR.glob("*.json"))
    if arguments[0] == "--bank-dir":
        return sorted(Path(arguments[1]).glob("*.json"))
    return [Path(item) for item in arguments]


def main(argv: list[str]) -> int:
    files = collect_files(argv[1:])
    if not files:
        print("usage: verify_question_bank.py [--bank-dir <dir> | <file.json> ...]", file=sys.stderr)
        return 2

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    sweep_stale_workspaces()

    total = 0
    passed = 0
    all_failures: list[str] = []
    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, list):
            print(f"== {path.name}: NOT A JSON ARRAY")
            all_failures.append(f"{path.name}: top level must be an array")
            continue
        print(f"== {path.name}: {len(document)} problems")
        for index, problem in enumerate(document):
            total += 1
            failures = verify_problem(problem, index)
            label = problem.get("title") or f"#{index}"
            if failures:
                print(f"  [FAIL] {label}")
                all_failures.extend(failures)
            else:
                passed += 1
                print(f"  [PASS] {label}  ({len(problem.get('cases') or [])} cases)")

    print()
    for failure in all_failures:
        print(f"  {failure}")
    print(f"{passed}/{total} passed")
    return 0 if passed == total and total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
