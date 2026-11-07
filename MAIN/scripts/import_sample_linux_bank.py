"""Import the 「示例题目」 Linux homework drafts (course created on demand).

Same contract flow as ``import_python_bank.py``: create the question, verify the auto-graded
ones through the platform (so "the reference answer reproduces its own expected output" is a
checked fact, not a claim), and leave everything unpublished. Problems marked
``"manual": true`` are never sent to the sandbox: their single case is a 人工核验 marker, and
the platform has no "manual" adapter kind to express that, so skipping verification is the
honest option (a fake empty case that "passes" would be worse).
"""

from __future__ import annotations

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
    for problem in problems:
        title = problem["title"]
        try:
            question_id, version_id = base.create_question(base_url, token, course_id, problem)
            if problem.get("manual"):
                created.append(
                    {"title": title, "question_id": question_id, "version_id": version_id,
                     "state": "draft（人工批阅，未送沙箱）"}
                )
                print(f"  [ok]   {title}  人工批阅草稿")
                continue
            passed, total, attested = base.verify(base_url, token, version_id)
            if passed != total or not attested:
                raise base.ApiError(422, f"verification {passed}/{total}, attestation={attested}")
            created.append(
                {"title": title, "question_id": question_id, "version_id": version_id,
                 "state": f"draft（校验 {passed}/{total}）"}
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
