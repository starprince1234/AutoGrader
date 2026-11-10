"""Seed the 「Python编程与实践作业」 question from the course PDF (authorized API only).

Why a separate script: this is *content*, not product code. It speaks the public B3 API with a
teacher credential, exactly like `seed_unix_homework.py` and `import_sample_linux_bank.py`, so it
cannot be reached from the running service and cannot write the database directly.

The PDF (`Python编程与实践作业.pdf`) holds ten exercises numbered 第 1–10 次 and a mail-submission
rule (subject `学号_第x次`, attachment `学号.py`, address python_pnp@163.com). One question is
created — titled with the file name, as asked — whose stem carries the whole assignment text and
the submission rule, so a student reads one page and knows what to do for all ten rounds.

Every question is created as a **draft**: the questions are graded by hand (mail submission), and a
draft never reaches the assignment picker by accident.

Usage::

    set BANK_TEACHER_USERNAME=...
    set BANK_TEACHER_PASSWORD=...
    python MAIN/scripts/seed_python_homework.py --base-url https://api.91idc.me --dry-run
    ... drop --dry-run to create

Exit codes: 0 when the question already exists or was created, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

TITLE = "Python编程与实践作业"
COURSE_TITLE = "Python编程与实践作业"
COURSE_CODE = "08"
STEM_PATH = pathlib.Path(__file__).parent / "data" / "python-homework-stem.md"


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, *, payload: dict | None = None) -> tuple[int, dict]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("authorization", f"Bearer {self.token}")
        request.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return error.code, {"detail": raw.decode("utf-8", "replace")[:400]}


def sign_in(base_url: str, username: str, password: str) -> str:
    """Exchange a teacher account for an access token (same flow the UI uses)."""
    url = f"{base_url.rstrip('/')}/api/v1/auth/session"
    body = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"sign-in failed: HTTP {error.code} {error.read()[:200]!r}") from error
    token = payload.get("access_token")
    if not token:
        raise SystemExit(f"sign-in returned no access_token: {payload}")
    return token


def ensure_course(api: Api, *, dry_run: bool) -> str:
    status, page = api.request("GET", "/api/v1/courses?limit=200")
    if status != 200:
        raise SystemExit(f"list courses failed: HTTP {status}")
    for course in page.get("items", []):
        if course.get("title") == COURSE_TITLE:
            print(f"course exists: {course['course_id']} (code={course.get('course_code')})")
            return course["course_id"]
    if dry_run:
        print(f"[dry-run] would create course {COURSE_TITLE!r} (code {COURSE_CODE})")
        return "00000000-0000-4000-8000-000000000000"
    status, created = api.request(
        "POST", "/api/v1/courses", payload={"title": COURSE_TITLE, "course_code": COURSE_CODE}
    )
    if status not in (200, 201):
        raise SystemExit(f"create course failed: HTTP {status} {created}")
    print(f"course created: {created['course_id']}")
    return created["course_id"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.91idc.me")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--language", default="python")
    arguments = parser.parse_args(argv)

    username = os.environ.get("BANK_TEACHER_USERNAME", "").strip()
    password = os.environ.get("BANK_TEACHER_PASSWORD", "").strip()
    if not username or not password:
        print("BANK_TEACHER_USERNAME / BANK_TEACHER_PASSWORD are required", file=sys.stderr)
        return 2

    stem = STEM_PATH.read_text(encoding="utf-8")
    token = sign_in(arguments.base_url, username, password)
    api = Api(arguments.base_url, token)
    course_id = ensure_course(api, dry_run=arguments.dry_run)

    status, page = api.request("GET", f"/api/v1/questions?course_id={course_id}&limit=200")
    if status == 200:
        for question in page.get("items", []):
            if question.get("title") == TITLE:
                print(f"question already exists: {question['question_id']} - nothing to do")
                return 0

    payload = {
        "title": TITLE,
        "stem_markdown": stem,
        "language": arguments.language,
        "adapter": {"kind": "manual_review"},
    }
    if arguments.dry_run:
        print(f"[dry-run] would create question {TITLE!r} in course {course_id}")
        print(f"[dry-run] stem: {len(stem)} chars")
        return 0

    status, created = api.request(
        "POST", f"/api/v1/questions?course_id={course_id}", payload=payload
    )
    if status not in (200, 201):
        raise SystemExit(f"create question failed: HTTP {status} {created}")
    print(f"question created: {created.get('question_id')} (draft)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
