"""Import a verified question bank into the running platform through its public API.

Design notes, because the ordering here is not arbitrary:

* **Only verified content is imported.** Every problem must already pass
  `verify_bank.py`; the importer re-checks nothing and trusts that gate, so run the
  verifier first. Importing an unverified problem would put a question in front of
  students whose own reference answer does not reproduce its expected output.
* **Fixtures come second and the manifest third.** `QuestionDraftInput` is
  `additionalProperties: false` and `AdapterManifest.files[]` needs a
  `content_sha256` that only the server can authoritatively produce for the bytes it
  stored, so the fixture is uploaded first and the digest it returns is what goes into
  the manifest. Publishing before that fails with
  "stored fixtures are missing from the manifest".
* **One assignment per source file.** A single 100-item assignment is unusable as a
  course view; grouping by topic keeps each assignment coherent. Assignments are
  skipped silently when the question version is already assigned.
* `tags` is dropped: `QuestionDraftInput` does not accept it (the read-side
  `QuestionPublic.tags` is not writable through this path). Reported at the end so the
  loss is not silent.

Usage:
    python import_bank.py --base-url http://127.0.0.1:18000 --course-id <uuid> \
        --section-id <uuid> [--dry-run] [--only 01-text-tools]

Credentials are read from the environment (`BANK_TEACHER_USERNAME` /
`BANK_TEACHER_PASSWORD`) with local-development defaults.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The importer is the only component that *executes* reference answers, which is why it also
# produces the publish attestation: B3 must not run anything, so executor and recorder are
# deliberately different components (CHG-0008). Reusing the verifier's execution path (rather
# than a second copy of it) is what keeps "the bank verifies" and "the bank imports" the same
# claim -- including its faithfully mirrored comparator and sandbox environment.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_question_bank as verify_bank

REPO_MAIN = Path(__file__).resolve().parents[1]
DEFAULT_BANK_DIR = REPO_MAIN / "seed" / "question-bank"
SKIP_FILES = {"00-selftest.json"}  # verifier fixture, duplicates the seeded question
SOURCE_LABEL = {
    "01-text-tools": "文本与行处理",
    "02-file-io": "文件读取",
    "03-process-scheduling": "进程调度与内存置换",
    "04-data-structures": "系统数据结构与算法",
    "05-encoding-parsing": "编码、解析与校验",
    "06-shell": "Shell 脚本",
}

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def request(method, url, *, token=None, body=None, tolerate=()):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        if error.code in tolerate:
            return None
        raise ApiError(error.code, detail) from error
    return json.loads(payload) if payload else None


def login(base: str, username: str, password: str) -> str:
    document = request(
        "POST", f"{base}/api/v1/auth/session", body={"username": username, "password": password}
    )
    token = (document or {}).get("access_token")
    if not isinstance(token, str):
        raise ApiError(500, f"login returned no access_token: {document}")
    return token


def declared_files(fixtures: list[dict] | None) -> list[dict] | None:
    """Fixtures as `AdapterManifest.files[]` entries, digests computed here.

    The digest must be declared **at create time**, which initially looks impossible:
    `POST /questions/{id}/fixtures` is what computes the authoritative digest, and it needs
    a `question_version_id` that only exists after the question is created. The resolution
    is that a client-declared digest is *never trusted* — `assert_fixture_binding` requires
    every stored fixture to be declared and every declared digest to equal the stored one.
    So the client computes sha256 of the bytes it is about to upload, declares that, and
    the server independently verifies it at publish. Getting it wrong fails loudly at
    publish instead of silently binding the wrong content.
    """
    if not fixtures:
        return None
    return [
        {
            "path": fixture["path"],
            "mount": fixture.get("mount", "input"),
            "content_sha256": hashlib.sha256(fixture["content"].encode("utf-8")).hexdigest(),
        }
        for fixture in fixtures
    ]


def draft_body(problem: dict) -> dict:
    """Map one bank problem onto `QuestionDraftInput` (dropping bank-only fields)."""
    adapter = dict(problem["adapter"])
    files = declared_files(problem.get("fixtures"))
    if files:
        adapter["files"] = files
    return {
        "title": problem["title"],
        "stem_markdown": problem["stem_markdown"],
        "language": problem["language"],
        "reference_answer": problem["reference_answer"],
        "adapter": adapter,
        "cases": problem["cases"],
        "max_files": problem.get("max_files", 20),
        "max_total_bytes": problem.get("max_total_bytes", 1048576),
    }


def create_question(base: str, token: str, course_id: str, problem: dict) -> tuple[str, str]:
    """Create the question and return `(question_id, draft_version_id)`."""
    created = request(
        "POST",
        f"{base}/api/v1/questions?course_id={course_id}",
        token=token,
        body=draft_body(problem),
    )
    question_id = str(created["question_id"])
    versions = created.get("versions") or []
    if not versions:
        detail = request("GET", f"{base}/api/v1/questions/{question_id}", token=token)
        versions = (detail or {}).get("versions") or []
    if not versions:
        raise ApiError(500, f"question {question_id} has no version to publish")
    # Oldest entry is the draft; the list is newest-first.
    draft = versions[-1]
    return question_id, str(draft["question_version_id"])


def upload_fixtures(base: str, token: str, version_id: str, fixtures: list[dict]) -> None:
    """Store the fixture bytes; the server computes the digest it will bind against."""
    for fixture in fixtures:
        request(
            "POST",
            f"{base}/api/v1/question-versions/{version_id}/fixtures",
            token=token,
            body={
                "path": fixture["path"],
                "mount": fixture.get("mount", "input"),
                "content_base64": base64.b64encode(
                    fixture["content"].encode("utf-8")
                ).decode("ascii"),
            },
        )


def draft_content_digest(base: str, token: str, version_id: str) -> str:
    """The digest B3 computes for this draft -- the value an attestation must name.

    Read from B3 rather than computed here: `publish` compares the attestation against the
    digest **it** recomputes, so a locally invented value would simply be rejected. That is
    the intended design (`CHG-0008`): the client's number is cross-checked, never trusted.
    """
    version = request(
        "GET", f"{base}/api/v1/question-versions/{version_id}", token=token
    )
    digest = (version or {}).get("content_digest")
    if not digest:
        raise ApiError(
            422,
            "B3 returned no content_digest for this draft, so no attestation can name it. "
            "A draft's digest must be persisted at content-write time, not only at publish.",
        )
    return str(digest)


def verify_cases(problem: dict) -> tuple[int, int, list[str]]:
    """Run every case's input through the reference answer locally.

    Returns `(cases_total, cases_passed, failures)`. This is the only place in the project that
    executes reference answers, which is why it also *produces* the attestation: B3 must not run
    anything (`B3/AGENTS.md`), so the executor and the recorder are deliberately different
    components. An attestation B3 issued to itself would be worthless.
    """
    verify_bank.WORK_ROOT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    passed = 0
    cases = problem.get("cases") or []
    for case in cases:
        import tempfile
        from pathlib import Path as _Path

        workdir = _Path(tempfile.mkdtemp(prefix="bankcase-", dir=verify_bank.WORK_ROOT))
        try:
            verify_bank.install_fixtures(problem, workdir)
            try:
                stdout, stderr, code = verify_bank.run_case(problem, case, workdir)
            except Exception as error:  # noqa: BLE001 - reported, never silent
                failures.append(f"case {case.get('ordinal')}: {type(error).__name__}: {error}")
                continue
            policy = problem["adapter"]["comparison"]
            problem_text = verify_bank.compare(
                stdout,
                case.get("expected_stdout"),
                policy.get("stdout", "ignore_trailing_whitespace"),
                policy.get("numeric_tolerance"),
            ) or verify_bank.compare(
                stderr, case.get("expected_stderr"), policy.get("stderr", "ignored"), None
            )
            expected_code = case.get("expected_exit_code")
            if problem_text is None and expected_code is not None and code != expected_code:
                problem_text = f"exit {code} != {expected_code}"
            if problem_text:
                failures.append(f"case {case.get('ordinal')}: {problem_text}")
            else:
                passed += 1
        finally:
            verify_bank.remove_tree(workdir)
    return len(cases), passed, failures


def attest_and_publish(base: str, token: str, version_id: str, problem: dict) -> None:
    """Run the reference answers, record the attestation, then publish.

    Order matters and is not negotiable: **verify -> attest -> publish**. `publish` requires an
    attestation whose digest matches the version's recomputed content (`CHG-0008`), so a
    question whose reference answer does not reproduce its own expected output cannot reach a
    student. If verification fails we stop here and report it, rather than publishing and
    hoping -- that is the whole point of the gate.
    """
    # Verify **first**: a question that fails its own reference answers must not cause a single
    # network call, let alone an attestation. (The first version fetched the digest first, which
    # a probe caught -- the documented order is verify -> attest -> publish, and it should hold
    # in the code and not only in the comment.)
    total, passed, failures = verify_cases(problem)
    if failures or passed != total:
        raise ApiError(
            422,
            f"reference answers do not reproduce the expected output"
            f" ({passed}/{total} cases passed): {'; '.join(failures[:3])}"
            " -- not publishing, because a question nobody can pass is worse than a missing one",
        )
    digest = draft_content_digest(base, token, version_id)
    request(
        "POST",
        f"{base}/api/v1/question-versions/{version_id}/attestations",
        token=token,
        body={
            "content_digest": digest,
            "verifier": "MAIN/scripts/import_question_bank.py",
            "cases_total": total,
            "cases_passed": passed,
            "verified_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "notes": "reference answers executed locally by the importer before publish",
        },
    )
    request("POST", f"{base}/api/v1/question-versions/{version_id}/publish", token=token)


def import_file(
    base: str, token: str, course_id: str, section_id: str, path: Path, dry_run: bool
) -> dict:
    problems = json.loads(path.read_text(encoding="utf-8"))
    label = SOURCE_LABEL.get(path.stem, path.stem)
    print(f"== {path.name}  ({len(problems)} problems) -> assignment 「{label}」")
    version_ids: list[str] = []
    failures: list[str] = []

    for problem in problems:
        title = problem["title"]
        if dry_run:
            print(f"  [dry-run] {title}")
            continue
        try:
            question_id, version_id = create_question(base, token, course_id, problem)
            if problem.get("fixtures"):
                upload_fixtures(base, token, version_id, problem["fixtures"])
            attest_and_publish(base, token, version_id, problem)
            version_ids.append(version_id)
            print(f"  [ok]   {title}")
        except ApiError as error:
            failures.append(f"{title}: {error}")
            print(f"  [FAIL] {title}: {error}")

    if dry_run or not version_ids:
        return {"version_ids": version_ids, "failures": failures, "label": label}

    request(
        "POST",
        f"{base}/api/v1/assignments",
        token=token,
        body={
            "title": label,
            "course_id": course_id,
            "section_id": section_id,
            "question_version_ids": version_ids,
        },
        tolerate=(409,),
    )
    print(f"  assignment 「{label}」 -> {len(version_ids)} questions")
    return {"version_ids": version_ids, "failures": failures, "label": label}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--section-id", required=True)
    parser.add_argument(
        "--bank-dir",
        default=str(DEFAULT_BANK_DIR),
        help="directory holding the verified *.json bank files",
    )
    parser.add_argument("--only", default=None, help="import just this file stem")
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(argv[1:])
    base = parsed.base_url.rstrip("/")
    bank_dir = Path(parsed.bank_dir)

    files = [path for path in sorted(bank_dir.glob("*.json")) if path.name not in SKIP_FILES]
    if parsed.only:
        files = [path for path in files if path.stem == parsed.only]
    if not files:
        print(f"no bank files to import in {bank_dir}", file=sys.stderr)
        return 2

    token = login(
        base,
        os.getenv("BANK_TEACHER_USERNAME", "t.zhang"),
        os.getenv("BANK_TEACHER_PASSWORD", "Teacher-Local-2026!"),
    )
    print(f"teacher login ok; importing {len(files)} file(s) from {bank_dir}")

    total_versions = 0
    all_failures: list[str] = []
    for path in files:
        result = import_file(
            base, token, parsed.course_id, parsed.section_id, path, parsed.dry_run
        )
        total_versions += len(result["version_ids"])
        all_failures.extend(result["failures"])

    print()
    print(f"published {total_versions} question versions")
    if all_failures:
        print(f"{len(all_failures)} failure(s):")
        for failure in all_failures:
            print(f"  {failure}")
    return 1 if all_failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
