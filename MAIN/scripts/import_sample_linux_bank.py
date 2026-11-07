"""Import / create the 「示例题目」 Linux homework question bank (course created on demand).

Same contract flow as ``import_python_bank.py``: create the question, upload fixtures when the
question declares them, verify every version through the platform's own reference-verification
endpoint (so "the reference answer reproduces its own expected output" is a checked fact, not a
claim), and leave everything unpublished.

Since ``CHG-0031`` none of these questions is a "manual review" placeholder:

* problem 1 uses ``execution.mode=email_delivery`` and its reference answer is a synthetic
  receipt that the same rule engine evaluates (it does not pretend a real message was received);
* problem 2 uses ``execution.mode=ssh_session`` and is executed in the real isolated practice
  environment;
* problems 6-9 use ``execution.mode=single_run``: one sandbox run, then per-case comparisons of
  stdout segments or of final file bytes/permission bits.

File questions carry their initial files inline as ``fixtures[]`` (``content_text``). The digest
the manifest must declare is computed here from those bytes and then cross-checked against the
stored fixture B3 computed, so the two can never disagree silently.

Usage::

    python MAIN/scripts/import_sample_linux_bank.py \
        --base-url https://api.91idc.me

Credentials come from ``BANK_TEACHER_USERNAME`` / ``BANK_TEACHER_PASSWORD``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import import_python_bank as base  # type: ignore  # noqa: E402

COURSE_TITLE = "示例题目"
COURSE_CODE = "07"
BANK_FILE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "seed"
    / "question-bank"
    / "sample-linux"
    / "sample-linux-homework.json"
)


def ensure_course(base_url: str, token: str) -> str:
    status, page = base.request("GET", base_url, "/api/v1/courses?limit=200", token=token)
    assert status == 200, page
    for course in page["items"]:
        if course["title"] == COURSE_TITLE:
            print(f"course exists: {course['course_id']} (code={course.get('course_code')})")
            return course["course_id"]
    status, created = base.request(
        "POST",
        base_url,
        "/api/v1/courses",
        token=token,
        payload={"title": COURSE_TITLE, "course_code": COURSE_CODE},
    )
    if status != 201:
        raise SystemExit(f"create course failed: HTTP {status} {created}")
    print(f"course created: {created['course_id']}")
    return created["course_id"]


def _fixture_bytes(problem: dict) -> list[dict]:
    """Resolve inline fixture declarations into ``{path, mount, content}`` entries."""
    resolved: list[dict] = []
    for index, fixture in enumerate(problem.get("fixtures", [])):
        path = fixture.get("path")
        mount = fixture.get("mount", "input")
        text = fixture.get("content_text")
        if not isinstance(path, str) or not path:
            raise SystemExit(f"{problem['title']}: fixtures[{index}].path is required")
        if mount not in ("input", "expected_output"):
            raise SystemExit(f"{problem['title']}: fixtures[{index}].mount is invalid")
        if not isinstance(text, str):
            raise SystemExit(f"{problem['title']}: fixtures[{index}].content_text is required")
        resolved.append({"path": path, "mount": mount, "content": text.encode("utf-8")})
    return resolved


def _prepare_problem(problem: dict) -> tuple[dict, list[dict]]:
    """Inject server-compatible fixture digests into the adapter manifest.

    The digest is computed from the bytes this script will upload; B3 recomputes it from the
    stored bytes and the compile path cross-checks the two, so a typo cannot become a silently
    different fixture.
    """
    prepared = copy.deepcopy(problem)
    fixtures = _fixture_bytes(problem)
    if fixtures:
        existing = list(prepared.get("adapter", {}).get("files", []))
        by_path = {(item["path"], item.get("mount", "input")): item for item in existing}
        declared: list[dict] = []
        for fixture in fixtures:
            digest = hashlib.sha256(fixture["content"]).hexdigest()
            key = (fixture["path"], fixture["mount"])
            entry = by_path.get(key)
            if entry is not None and entry.get("content_sha256") not in (None, digest):
                raise SystemExit(
                    f"{problem['title']}: declared digest for {fixture['path']} does not match "
                    "the inline fixture content"
                )
            declared.append(
                {"path": fixture["path"], "content_sha256": digest, "mount": fixture["mount"]}
            )
        prepared["adapter"]["files"] = declared
    elif prepared.get("adapter", {}).get("files"):
        raise SystemExit(
            f"{problem['title']}: adapter declares fixtures but no inline fixture content is "
            "available; the importer would send a digest nothing can satisfy"
        )
    return prepared, fixtures


def _upload_fixtures(
    base_url: str, token: str, version_id: str, fixtures: list[dict], title: str
) -> None:
    for fixture in fixtures:
        digest = hashlib.sha256(fixture["content"]).hexdigest()
        status, stored = base.request(
            "POST",
            base_url,
            f"/api/v1/question-versions/{version_id}/fixtures",
            token=token,
            payload={
                "path": fixture["path"],
                "mount": fixture["mount"],
                "content_base64": base64.b64encode(fixture["content"]).decode("ascii"),
            },
        )
        if status not in (200, 201) or not isinstance(stored, dict):
            raise base.ApiError(status, stored)
        if stored.get("content_sha256") != digest:
            raise base.ApiError(
                500,
                f"{title}: stored fixture digest does not match the uploaded bytes",
            )


def main() -> int:
    base_url = os.getenv("BANK_BASE_URL", "https://api.91idc.me").rstrip("/")
    token = base.login(
        base_url,
        os.getenv("BANK_TEACHER_USERNAME", "admin"),
        os.getenv("BANK_TEACHER_PASSWORD", ""),
    )
    course_id = ensure_course(base_url, token)
    problems = json.loads(BANK_FILE.read_text(encoding="utf-8"))

    created: list[dict] = []
    failures: list[str] = []
    for raw in problems:
        title = raw["title"]
        try:
            problem, fixtures = _prepare_problem(raw)
            question_id, version_id = base.create_question(base_url, token, course_id, problem)
            if fixtures:
                _upload_fixtures(base_url, token, version_id, fixtures, title)
            passed, total, attested = base.verify(base_url, token, version_id)
            if passed != total or not attested:
                raise base.ApiError(422, f"verification {passed}/{total}, attestation={attested}")
            created.append(
                {
                    "title": title,
                    "question_id": question_id,
                    "version_id": version_id,
                    "state": f"draft（校验 {passed}/{total}）",
                }
            )
            print(f"  [ok]   {title}  校验 {passed}/{total}")
        except base.ApiError as error:
            failures.append(f"{title}: {error}")
            print(f"  [FAIL] {title}: {error}")

    print()
    print(f"created {len(created)} draft(s) in course {course_id}")
    if failures:
        print(f"{len(failures)} failure(s):")
        for item in failures:
            print(f"  {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
