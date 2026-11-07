"""Update the existing 「示例题目」 Linux homework drafts from the seed file (authorized API only).

Why this exists next to ``import_sample_linux_bank.py``: that script creates a **new** question
per seed entry. After the first import the course already holds those 10 drafts, and creating a
second copy every time a stem or a case is corrected would leave the course full of duplicates
(and would silently split any assignment that referenced the original ids). This script updates
the existing draft in place through the same teacher-authorized HTTP API, then re-runs reference
verification so an attestation exists for the corrected content.

Nothing here reads or writes the database directly, and nothing is published: the version stays a
draft until a teacher reviews the verification result.

Usage::

    set BANK_TEACHER_USERNAME=...
    set BANK_TEACHER_PASSWORD=...
    python MAIN/scripts/update_sample_linux_bank.py --base-url https://api.91idc.me

  --dry-run   only report what would be updated / whether the draft was found
  --only ID   limit to one seed title (substring match), useful for a single fix

Exit codes: 0 when every targeted question was found and verified, 1 otherwise.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import import_python_bank as base  # type: ignore  # noqa: E402
import import_sample_linux_bank as seed  # type: ignore  # noqa: E402


def _find_course(base_url: str, token: str) -> str | None:
    status, page = base.request("GET", base_url, "/api/v1/courses?limit=200", token=token)
    assert status == 200, page
    for course in page["items"]:
        if course["title"] == seed.COURSE_TITLE:
            return str(course["course_id"])
    return None


def _drafts_by_title(base_url: str, token: str, course_id: str) -> dict[str, dict]:
    """Every question in the course, with its draft version id (newest draft first)."""
    status, page = base.request(
        "GET",
        base_url,
        f"/api/v1/questions?course_id={course_id}&published_only=false&limit=200",
        token=token,
    )
    if status != 200 or not isinstance(page, dict):
        raise base.ApiError(status, page)
    found: dict[str, dict] = {}
    for question in page.get("items", []):
        versions = question.get("versions") or []
        drafts = [item for item in versions if item.get("status") == "draft"]
        if drafts:
            found[question["title"]] = {
                "question_id": str(question["question_id"]),
                "question_version_id": str(drafts[0]["question_version_id"]),
            }
    return found


def _prepare(problem: dict) -> tuple[dict, list[dict]]:
    prepared, fixtures = seed._prepare_problem(problem)
    body = base.draft_body(prepared)
    # A full draft write replaces the protected content on purpose: this script exists to
    # correct a stem, a reference answer or the cases, so it must send them (not rely on
    # the "omitted preserves" rule, which would keep the old incorrect content).
    return body, fixtures


def _upload_fixtures(
    base_url: str, token: str, version_id: str, fixtures: list[dict], title: str
) -> None:
    seed._upload_fixtures(base_url, token, version_id, fixtures, title)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("BANK_BASE_URL", "https://api.91idc.me"))
    parser.add_argument("--only", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    token = base.login(
        base_url,
        os.getenv("BANK_TEACHER_USERNAME", "admin"),
        os.getenv("BANK_TEACHER_PASSWORD", ""),
    )
    course_id = _find_course(base_url, token)
    if course_id is None:
        print(f"course {seed.COURSE_TITLE!r} not found", file=sys.stderr)
        return 1
    existing = _drafts_by_title(base_url, token, course_id)
    problems = json.loads(seed.BANK_FILE.read_text(encoding="utf-8"))

    failures: list[str] = []
    updated: list[dict] = []
    for raw in problems:
        title = raw["title"]
        if args.only and args.only not in title:
            continue
        target = existing.get(title)
        if target is None:
            failures.append(f"{title}: no draft with this title exists in the course")
            print(f"  [MISS] {title}")
            continue
        if args.dry_run:
            print(f"  [dry-run] {title} -> question {target['question_id']}")
            continue
        try:
            body, fixtures = _prepare(copy.deepcopy(raw))
            status, patched = base.request(
                "PATCH",
                base_url,
                f"/api/v1/questions/{target['question_id']}",
                token=token,
                payload=body,
            )
            if status not in (200, 201) or not isinstance(patched, dict):
                raise base.ApiError(status, patched)
            if fixtures:
                _upload_fixtures(
                    base_url, token, target["question_version_id"], fixtures, title
                )
            passed, total, attested = base.verify(
                base_url, token, target["question_version_id"]
            )
            if passed != total or not attested:
                raise base.ApiError(
                    422, f"verification {passed}/{total}, attestation={attested}"
                )
            updated.append({"title": title, **target, "passed": passed, "total": total})
            print(f"  [ok]   {title}  校验 {passed}/{total}  draft")
        except base.ApiError as error:
            failures.append(f"{title}: {error}")
            print(f"  [FAIL] {title}: {error}")

    print()
    print(f"updated {len(updated)} draft(s) in course {course_id}")
    if failures:
        print(f"{len(failures)} failure(s):", file=sys.stderr)
        for item in failures:
            print(f"  {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
