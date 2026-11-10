"""Rename one course and retire every other question in the bank (authorized API only).

Two operations, both driven by the public API with a teacher credential:

1. **rename**: `PATCH /api/v1/courses/{id}` changes the title (the card the question list groups by).
2. **retire**: every question outside the kept course is removed —
   `DELETE /api/v1/question-versions/{id}` for a draft, and for a published/archived version
   `POST /api/v1/question-versions/{id}/archive`, because B3 refuses to delete content that past
   evaluation evidence may point at (contract: "only a draft can be deleted"). The script reports
   both counts, so "deleted" and "archived" are never conflated.

Dry run is the default; passing `--yes` performs the writes. It never touches questions of the
kept course.

Usage::

    set BANK_TEACHER_USERNAME=...
    set BANK_TEACHER_PASSWORD=...
    python MAIN/scripts/maintain_question_bank.py --keep "示例题目" --rename-to "Unix操作系统作业"
    ... add --yes to write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, *, payload: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, method=method)
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
                return error.code, {"detail": raw.decode("utf-8", "replace")[:300]}


def sign_in(base_url: str, username: str, password: str) -> str:
    url = f"{base_url.rstrip('/')}/api/v1/auth/session"
    request = urllib.request.Request(
        url,
        data=json.dumps({"username": username, "password": password}).encode(),
        method="POST",
    )
    request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"sign-in failed: HTTP {error.code}") from error
    token = payload.get("access_token")
    if not token:
        raise SystemExit("sign-in returned no access_token")
    return token


def list_courses(api: Api) -> list[dict]:
    status, page = api.request("GET", "/api/v1/courses?limit=200")
    if status != 200:
        raise SystemExit(f"list courses failed: HTTP {status}")
    return page.get("items", [])


def list_questions(api: Api, course_id: str | None) -> list[dict]:
    suffix = f"&course_id={course_id}" if course_id else ""
    status, page = api.request("GET", f"/api/v1/questions?limit=200{suffix}")
    if status != 200:
        raise SystemExit(f"list questions failed: HTTP {status}")
    return page.get("items", [])


def rename_course(api: Api, course: dict, new_title: str, *, write: bool) -> None:
    payload = {"title": new_title, "expected_updated_at": course["updated_at"]}
    if not write:
        print(f"[dry-run] would rename course {course['title']!r} -> {new_title!r}")
        return
    status, body = api.request("PATCH", f"/api/v1/courses/{course['course_id']}", payload=payload)
    if status != 200:
        raise SystemExit(f"rename failed: HTTP {status} {body}")
    print(f"renamed course -> {new_title!r}")


def retire_question(api: Api, question: dict, *, write: bool) -> tuple[int, int]:
    """Return (deleted_versions, archived_versions) for one question."""
    deleted = archived = 0
    for version in question.get("versions", []):
        version_id = version["version_id"]
        status_name = version.get("status")
        if not write:
            if status_name == "draft":
                deleted += 1
            else:
                archived += 1
            continue
        if status_name == "draft":
            status, _ = api.request("DELETE", f"/api/v1/question-versions/{version_id}")
            if status in (200, 204):
                deleted += 1
                continue
        status, _ = api.request("POST", f"/api/v1/question-versions/{version_id}/archive")
        if status in (200, 204):
            archived += 1
    return deleted, archived


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.91idc.me")
    parser.add_argument("--keep", required=True, help="course title to keep (also rename source)")
    parser.add_argument("--rename-to", default=None, help="new title for the kept course")
    parser.add_argument("--yes", action="store_true", help="write; without it nothing is changed")
    arguments = parser.parse_args(argv)

    username = os.environ.get("BANK_TEACHER_USERNAME", "").strip()
    password = os.environ.get("BANK_TEACHER_PASSWORD", "").strip()
    if not username or not password:
        print("BANK_TEACHER_USERNAME / BANK_TEACHER_PASSWORD are required", file=sys.stderr)
        return 2

    api = Api(arguments.base_url, sign_in(arguments.base_url, username, password))
    write = arguments.yes

    courses = list_courses(api)
    kept = next((course for course in courses if course.get("title") == arguments.keep), None)
    if kept is None:
        raise SystemExit(f"course {arguments.keep!r} not found")
    print(f"kept course: {kept['course_id']} {kept['title']!r}")

    if arguments.rename_to:
        rename_course(api, kept, arguments.rename_to, write=write)

    total_deleted = total_archived = kept_questions = 0
    for course in courses:
        questions = list_questions(api, course["course_id"])
        if course["course_id"] == kept["course_id"]:
            kept_questions = len(questions)
            print(f"kept {len(questions)} question(s) in {course['title']!r}")
            continue
        if not questions:
            continue
        deleted = archived = 0
        for question in questions:
            d, a = retire_question(api, question, write=write)
            deleted += d
            archived += a
        total_deleted += deleted
        total_archived += archived
        print(f"{course['title']!r}: {len(questions)} question(s) -> delete draft versions {deleted}, archive published {archived}")

    print(
        f"\nsummary: kept {kept_questions} question(s); "
        f"removed draft versions {total_deleted}, archived versions {total_archived}"
        + ("" if write else "  [dry-run: nothing was written]")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
