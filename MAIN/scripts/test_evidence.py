"""Record what each module's test suite actually did, so the numbers in the docs are checkable.

Why this exists: this project's phase documents quote test totals ("1145 passed", "23 files /
133 tests"), and those numbers are the main evidence that anything works. They are also
**hand-copied prose**. Nothing detects the case that actually happens: the contract moves, the
suite is never re-run, and the quoted number silently describes a revision that no longer
exists. The number stays plausible, which is worse than being wrong.

So each run is recorded as a machine-readable artefact bound to the contract version and digest
it was measured against, and `autograder_host.checks.check_test_evidence()` fails when a module's
evidence is missing or was recorded against a contract the repository has since moved past.

That gate is a correction, not the original design. This script was written *for* the gate and
then wired into nothing, so for the whole interval from contract 0.7.0 to 0.16.0 `--check` reported
"B1 recorded at 0.7.0; B2/B3/B4/B5 no evidence" to no one, and the documents meanwhile quoted B1 at
133 tests against a repository whose suite had reached 263. A mechanism with no enforcement point
is indistinguishable from no mechanism, which is why the gate now lives in `checks.py` and calls
`evidence_problems()` below rather than reimplementing it.

Usage:
    python MAIN/scripts/test_evidence.py --module B2          # run one suite and record it
    python MAIN/scripts/test_evidence.py --all                # the delivery sweep (all five)
    python MAIN/scripts/test_evidence.py --check              # the same verdict checks.py gates on

Deliberately, there is **no way to write an evidence file by hand**: the only writer parses a
real test-runner run (pytest for B2-B5, vitest for B1). A hand-writable evidence file would be a
second place to state a claim, which is the failure mode this whole project keeps finding.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

MAIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAIN_ROOT.parent
EVIDENCE_DIR = MAIN_ROOT / ".runtime" / "evidence"

#: Scratch directory handed to each suite in place of the machine's temp directory, which is
#: outside the workspace and not writable here. `.runtime` is already excluded from the
#: documentation gates, so nothing this produces can look like a project artefact.
WORKSPACE_TEMP = MAIN_ROOT / ".runtime" / "pytest-tmp"

sys.path.insert(0, str(MAIN_ROOT / "sdk" / "src"))
from autograder_platform import contract_digest, declared_contract_version  # noqa: E402

#: pytest's own summary line, e.g. "1145 passed, 9 skipped, 1 xfailed in 325.64s".
_COUNT = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
_DURATION = re.compile(r"in\s+([\d.]+)s")


#: Each Python suite uses its own locked module environment. B1 is a Node project, so
#: `uv run pytest` is not merely wrong there -- it would fail and get recorded as a failing
#: suite. Kept as data rather than an if-chain so adding a module is one line.
COMMANDS: dict[str, list[str]] = {
    "B1": ["node", "node_modules/vitest/vitest.mjs", "run"],
    "B2": ["uv", "run", "--locked", "pytest", "-q"],
    "B3": ["uv", "run", "--locked", "pytest", "-q"],
    "B4": ["uv", "run", "--locked", "pytest", "-q"],
    "B5": ["uv", "run", "--locked", "pytest", "-q"],
}

#: vitest's summary lines differ from pytest's; both are parsed so the ledger covers all five
#: modules rather than four and a special case.
_VITEST_COUNT = re.compile(r"(\d+)\s+(passed|failed|skipped|todo)\b")


def parse_summary(output: str) -> dict[str, int]:
    """Extract outcome counts from the runner's summary lines."""
    counts: dict[str, int] = {}
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        found = _COUNT.findall(stripped) or _VITEST_COUNT.findall(stripped)
        if not found:
            continue
        for number, label in found:
            normalised = {"error": "errors"}.get(label, label)
            counts[normalised] = int(number)
        duration = _DURATION.search(stripped)
        if duration:
            counts["duration_ms"] = int(float(duration.group(1)) * 1000)
        break
    if "passed" not in counts and "failed" not in counts:
        raise SystemExit(
            "could not find a test-runner summary line in the output; refusing to record an "
            "evidence file that does not correspond to a real run"
        )
    return counts


def test_database(module: str) -> str | None:
    """The module's own declared test instance, as a DSN -- or None if it declares no postgres.

    Read out of `B<n>/compose.yaml` rather than kept as a second copy here: the module's compose
    file is the authority on its test instance, and a hand-copied DSN is exactly the kind of
    duplicate that drifts.

    This exists because of a measured defect (2026-09-20). `run_suite` claimed to run each suite
    "the way its own runbook does", but it inherited the ambient environment. B2's conftest skips
    every database test when neither `B2_TEST_DATABASE_URL` nor `DATABASE_URL` is set, so two runs
    of the same command at the same contract produced:

        ambient DATABASE_URL present -> 1293 passed,    9 skipped   (real suite)
        ambient DATABASE_URL absent  ->  632 passed,  677 skipped   (only the 39 s of DB-free tests)

    and both were recorded as `exit_code 0`. The gate only looked at `exit_code`, so the second --
    a green reading that means almost nothing -- was written into the ledger as evidence.
    """
    compose = REPO_ROOT / module / "compose.yaml"
    if not compose.is_file():
        return None
    document = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
    for service in (document.get("services") or {}).values():
        if not str(service.get("image", "")).startswith("postgres"):
            continue
        environment = service.get("environment") or {}
        database = environment.get("POSTGRES_DB")
        user = environment.get("POSTGRES_USER")
        password = environment.get("POSTGRES_PASSWORD")
        port = None
        for mapping in service.get("ports") or []:
            host_side = str(mapping).partition(":")[0].strip()
            if host_side.isdigit():
                port = host_side
                break
        if not (database and user and password and port):
            return None
        return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{port}/{database}"
    return None


def database_label(dsn: str | None) -> str | None:
    """`host:port/db` for the record, with the credentials left out of the artefact."""
    if dsn is None:
        return None
    tail = dsn.rpartition("@")[2]
    return tail or None


def suite_command(module: str, environment: dict[str, str]) -> list[str]:
    """Use the privileged Linux test image for B2's real nsjail suite in CI."""
    image = environment.get("AUTOGRADER_B2_NSJAIL_IMAGE")
    if module != "B2" or not image:
        return COMMANDS[module]
    if not re.fullmatch(r"[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+", image):
        raise ValueError("AUTOGRADER_B2_NSJAIL_IMAGE must be an image name with a tag")
    return [
        "docker",
        "run",
        "--rm",
        "--privileged",
        "--cgroupns=host",
        "--init",
        "--network=host",
        "--volume",
        f"{REPO_ROOT}:/work",
        "--workdir",
        "/work/B2",
        "--tmpfs",
        "/work/B2/.venv",
        "--tmpfs",
        "/work/B2/.pytest-tmp",
        "--tmpfs",
        "/work/B2/.pytest_cache",
        "--env",
        "DATABASE_URL",
        "--env",
        "B2_TEST_DATABASE_URL",
        "--env",
        "B2_TEST_NATS_URL",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        image,
        "uv",
        "run",
        "--locked",
        "--all-groups",
        "--with",
        "httpx==0.28.1",
        "pytest",
        "-q",
    ]


def run_suite(module: str) -> tuple[int, str, str | None, list[str]]:
    """Run the module's suite the way its own runbook does, and return (exit, output, dsn).

    Two deliberate deviations from a bare `subprocess.run`, both forced by measured defects:
    the child's temp directory is redirected into the workspace, and the module's declared test
    database is supplied as `DATABASE_URL`. Each is documented where it happens below.

    This machine's system temp directory is outside the workspace and not writable by this
    account (`D:\\dev\\temp` -> `PermissionError: [WinError 5]`), and pytest's `tmp_path` inherits
    it. B5's five view tests therefore reported five *setup errors* that measured the machine's
    ACL rather than B5: with the scratch directory writable the same suite is 28 passed, 0 errors.
    Recording that as "B5's run failed" would have put a false statement about B5 into the ledger
    that this file exists to keep true.

    Redirecting TMP/TEMP/TMPDIR fixes it for every tool the suite spawns, rather than adding a
    `--basetemp` flag that would only cover pytest's own temporaries. The underlying ACL is still
    a real environment defect (B4 works around it with its own `workspace_tmp` fixture, and any
    module suite run by hand still hits it); see the phase document.
    """
    WORKSPACE_TEMP.mkdir(parents=True, exist_ok=True)
    dsn = test_database(module)
    environment = {
        **os.environ,
        "TMP": str(WORKSPACE_TEMP),
        "TEMP": str(WORKSPACE_TEMP),
        "TMPDIR": str(WORKSPACE_TEMP),
    }
    if dsn is not None:
        # The suite skips its database tests when no DSN is present, so providing the module's own
        # declared test instance is what makes this run *the module's run* instead of a DB-free
        # subset that still exits 0.
        environment["DATABASE_URL"] = dsn
    if module == "B2" and dsn is not None:
        environment["B2_TEST_DATABASE_URL"] = dsn
        environment["B2_TEST_NATS_URL"] = "nats://127.0.0.1:54223"
    command = suite_command(module, environment)
    result = subprocess.run(
        command,
        cwd=REPO_ROOT / module,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or ""), dsn, command


def failure_diagnostics(output: str) -> list[str]:
    """Return pytest's concise failed-test identifiers without dumping captured output."""
    return [
        line.strip()
        for line in output.splitlines()
        if line.lstrip().startswith(("FAILED tests/", "ERROR tests/"))
    ]


def record(module: str) -> int:
    digest = contract_digest(MAIN_ROOT / "contracts")
    version = declared_contract_version(MAIN_ROOT / "contracts")
    print(f"{module}: running the suite at contract {version} ({digest[:12]}…)")
    exit_code, output, dsn, command = run_suite(module)
    try:
        counts = parse_summary(output)
    except SystemExit:
        diagnostics = failure_diagnostics(output)
        for line in diagnostics[:20]:
            print(f"  diagnostic: {line[:240]}")
        raise
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    document = {
        "module": module,
        "command": " ".join(command),
        "exit_code": exit_code,
        "contract_version": version,
        "contract_digest": digest,
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "summary": counts,
        "database": database_label(dsn),
    }
    path = EVIDENCE_DIR / f"{module}.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  recorded {path.relative_to(REPO_ROOT)}")
    print(f"  exit={exit_code}  {counts}")
    for line in failure_diagnostics(output)[:20]:
        print(f"  failed test: {line[:240]}")
    tail = [line for line in output.splitlines() if line.strip()][-3:]
    for line in tail:
        print(f"    | {line[:160]}")
    return 0 if exit_code == 0 else 1


def evidence_problems() -> list[str]:
    """Every reason the recorded evidence is not current, as one message per reason.

    Pure: it prints nothing and raises nothing, so `checks.py`'s gate and this script's `--check`
    share one implementation. Two implementations of "is the evidence current" would be two
    verdicts on the same repository, and the drift between them would be invisible -- the exact
    shape of defect this file was written to catch.
    """
    digest = contract_digest(MAIN_ROOT / "contracts")
    version = declared_contract_version(MAIN_ROOT / "contracts")
    problems: list[str] = []
    # Iterating COMMANDS rather than a hardcoded five keeps its "adding a module is one line"
    # promise true here too.
    for module in COMMANDS:
        path = EVIDENCE_DIR / f"{module}.json"
        if not path.is_file():
            problems.append(
                f"{module}: no recorded test evidence (run test_evidence.py --module {module})"
            )
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["contract_version"] != version or document["contract_digest"] != digest:
            problems.append(
                f"{module}: evidence was recorded at contract {document['contract_version']}"
                f" ({document['contract_digest'][:12]}…) but the repository is at {version}"
                f" ({digest[:12]}…) -- re-run the suite"
            )
        if document["exit_code"] != 0:
            problems.append(
                f"{module}: the recorded run failed (exit {document['exit_code']},"
                f" {document['summary']})"
            )
        declared = test_database(module)
        if declared is not None and not document.get("database"):
            # `exit_code 0` is not enough on its own: B2's suite exits 0 while skipping every
            # database test when no DSN is present (measured: 632 passed / 677 skipped against
            # 1298 passed / 9 skipped). A record like that is a green reading of a subset.
            problems.append(
                f"{module}: recorded without its declared test database"
                f" ({database_label(declared)}) -- the suite skips its database tests when none is"
                f" given, so this record describes a subset and its totals are not the module's"
            )
    return problems


def check() -> int:
    """Fail when a module's evidence is missing, stale, or records a failing run."""
    problems = evidence_problems()
    if problems:
        raise ValueError("test evidence is missing or stale:\n  " + "\n  ".join(problems))
    version = declared_contract_version(MAIN_ROOT / "contracts")
    digest = contract_digest(MAIN_ROOT / "contracts")
    print(f"test evidence is current for all modules at contract {version} ({digest[:12]}…)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", help="run this module's suite and record the result")
    parser.add_argument(
        "--all",
        action="store_true",
        help="run every module's suite in order and record each (the delivery sweep)",
    )
    parser.add_argument("--check", action="store_true", help="verify the recorded evidence is current")
    arguments = parser.parse_args(argv)
    if arguments.all:
        # The delivery sweep. Records what it can and reports the rest rather than stopping at
        # the first failure: a module whose suite is red is information about that module, and
        # hiding the other four results behind it would make the sweep less useful than running
        # them by hand.
        failures: list[str] = []
        for module in COMMANDS:
            try:
                if record(module) != 0:
                    failures.append(module)
            except SystemExit as error:
                failures.append(f"{module} ({error})")
                print(f"  !! {module}: {error}")
        if failures:
            print(f"\nsuites that did not pass: {', '.join(failures)}")
            return 1
        return check()
    if arguments.module:
        return record(arguments.module)
    if arguments.check:
        return check()
    parser.error("pass --module <name>, --all, or --check")


if __name__ == "__main__":
    raise SystemExit(main())
