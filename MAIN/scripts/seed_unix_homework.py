"""Seed the ten Unix homework questions into the B3 question bank.

This is a **content script**, not product code: it talks to the public B3 API with a teacher
token, so nothing here is reachable from the running service.

What it does per question:

* always creates a **draft** version (``POST /api/v1/questions``) - drafts never appear in the
  assignment picker, so seeding cannot accidentally hand students an unfinished question;
* only questions declared ``"gradable": true`` carry cases and a reference answer, and only
  those are eligible for ``--publish``. The other nine are saved with their real stem and a
  note explaining what is missing (environment-dependent output, or a fixture file we do not
  have yet); inventing expected output for them would produce questions that pass anything.

Usage::

    python MAIN/scripts/seed_unix_homework.py --base-url http://127.0.0.1:18000 \\
        --token "$B4_TEACHER_TOKEN" --course-id <uuid> --dry-run

    ... remove --dry-run to write; add --publish to publish the gradable ones (needs the
    reference attestation to be in place, otherwise the server answers 409 and the script
    reports it instead of pretending success).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

DATA = pathlib.Path(__file__).parent / "data" / "unix-homework-10.json"

SUBMIT_RULE = """
---

**作业提交要求**

1. 邮件标题见第 1 题。
2. 附件文件名为 `学号.txt`（例：学号为 2020141440777 的同学提交 `2020141440777.txt`）。
3. 附件里只能包含 linux 命令、命令选项以及参数，每行只能写入一条命令。
4. 附件文本文件使用「记事本」/「TextEdit」编辑，并以 ANSI/ASCII 编码保存后提交。
"""


def request(
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
) -> tuple[int, dict | str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_object = urllib.request.Request(url, data=body, method=method)
    request_object.add_header("authorization", f"Bearer {token}")
    # Cloudflare 会拦掉 stdlib 默认 UA（error 1010），显式声明客户端身份。
    request_object.add_header("user-agent", "autograder-seed/1.0")
    if body is not None:
        request_object.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request_object, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def draft_payload(entry: dict) -> dict:
    stem = entry["stem_markdown"]
    note = entry.get("note")
    if note:
        stem = f"{stem}\n\n> **本题暂为草稿。** {note}"
    if entry.get("gradable") is not True:
        stem = f"{stem}\n{SUBMIT_RULE}"
    payload: dict = {
        "title": entry["title"],
        "stem_markdown": stem,
        "language": "bash",
        "adapter": entry.get("adapter")
        or {
            "kind": "stdin",
            "args": [],
            "stdin": None,
            "expect_exit_code": 0,
            "comparison": {"stdout": "exact", "stderr": "ignored"},
        },
    }
    if entry.get("reference_answer"):
        payload["reference_answer"] = entry["reference_answer"]
    if entry.get("cases"):
        # 逐字段透传：`TestCaseInput` 要求 name/ordinal，`expect_exit_code` 不属于用例
        # （退出码在 adapter 上声明），传错字段整个请求会被 422 拒掉。
        allowed = ("name", "ordinal", "stdin", "args", "expected_stdout", "expected_stderr")
        payload["cases"] = [
            {key: case[key] for key in allowed if key in case} for case in entry["cases"]
        ]
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True, help="teacher/admin access token")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--only-index",
        type=int,
        default=None,
        help="seed only the question at this 0-based index (re-runs stay idempotent)",
    )
    arguments = parser.parse_args(argv)

    data = json.loads(DATA.read_text(encoding="utf-8"))
    base = arguments.base_url.rstrip("/")
    created: list[tuple[str, str, bool]] = []

    entries = data["questions"]
    if arguments.only_index is not None:
        entries = [entries[arguments.only_index]]

    for entry in entries:
        payload = draft_payload(entry)
        if arguments.dry_run:
            print(f"[dry-run] {entry['title']}: {len(payload.get('cases', []))} case(s)")
            continue
        status, body = request(
            "POST",
            f"{base}/api/v1/questions?course_id={arguments.course_id}",
            arguments.token,
            payload,
        )
        if status != 201:
            print(f"FAILED {entry['title']}: HTTP {status} {body}", file=sys.stderr)
            return 1
        question_id = body["question_id"]
        version_id = (body.get("versions") or [{}])[0].get("question_version_id", "")
        print(f"created {entry['title']}: question={question_id} version={version_id}")
        created.append((question_id, version_id, entry.get("gradable") is True))

    if arguments.publish and not arguments.dry_run:
        for question_id, version_id, gradable in created:
            if not gradable:
                print(f"skip publish (not gradable yet): {question_id}")
                continue
            status, body = request(
                "POST",
                f"{base}/api/v1/question-versions/{version_id}/publish",
                arguments.token,
            )
            if status != 200:
                print(
                    f"publish refused for {question_id}: HTTP {status} {body} "
                    "(a reference attestation is required before a version can be published)",
                    file=sys.stderr,
                )
                continue
            print(f"published {question_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
