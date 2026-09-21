"""Seed the integrated environment through the public HTTP APIs.

Run this after the composition host is up. It deliberately uses the *API* rather than
direct SQL: seeding then doubles as an end-to-end check of B4 (identity, courses,
roster), B3 (question bank, publish, assignment) and the auth chain, and it cannot
create a state the product itself could not create.

Prerequisite: an administrator must already exist --
`uv run python -m autograder_b4.ops.bootstrap_admin --username admin`.

Usage:
    python MAIN/scripts/seed_integration.py --base-url http://127.0.0.1:18000 \
        --admin-password 'Admin-Local-2026!'

The roster is uploaded as a real .xlsx with Chinese headers (姓名 / 学号 / 课程编号 /
课序号), because that is the shape `RQ-B-1-10` promises to accept.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

#: The sibling importer owns the only code in the project that executes reference answers, and
#: therefore the attestation that makes a question publishable. Reused rather than reimplemented:
#: a second copy of a security-relevant sequence is a second copy that can drift. Imported
#: explicitly by directory so the seed works regardless of how it is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_question_bank as imported  # noqa: E402  (path must be set first)

PASSWORD_TEACHER = "Teacher-Local-2026!"
PASSWORD_STUDENT = "Student-Local-2026!"

COURSE_CODE = "CS-OS-2026"
COURSE_TITLE = "操作系统（Unix 作业）"
SECTION_CODE = "01"
STUDENT_NUMBER = "20260001"
STUDENT_NAME = "李小明"
TEACHER_NAME = "张老师"


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


def request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: object | None = None,
    raw: tuple[bytes, str] | None = None,
    tolerate: tuple[int, ...] = (),
) -> object:
    """Perform one API call. `tolerate` lists statuses that mean "already in this state"."""
    headers: dict[str, str] = {"Accept": "application/json"}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if raw is not None:
        payload, content_type = raw
        data = payload
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        if error.code in tolerate:
            return None
        raise ApiError(error.code, detail) from error
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def login(base: str, username: str, password: str) -> str:
    document = request(
        "POST", f"{base}/api/v1/auth/session", json_body={"username": username, "password": password}
    )
    assert isinstance(document, dict)
    token = document.get("access_token")
    if not isinstance(token, str):
        raise ApiError(500, f"login for {username} returned no access_token: {document}")
    return token


def ensure_user(
    base: str,
    admin: str,
    *,
    username: str,
    display_name: str,
    roles: list[str],
    password: str,
    student_number: str | None = None,
) -> str:
    document = request(
        "POST",
        f"{base}/api/v1/users",
        token=admin,
        json_body={
            "display_name": display_name,
            "username": username,
            "student_number": student_number,
            "email": None,
            "roles": roles,
            "password": password,
        },
        tolerate=(409,),
    )
    if isinstance(document, dict) and document.get("user_id"):
        return str(document["user_id"])
    # Already present: look the user up so the script stays re-runnable.
    listing = request("GET", f"{base}/api/v1/users?limit=200", token=admin)
    assert isinstance(listing, dict)
    for item in listing.get("items", []):
        if isinstance(item, dict) and item.get("username") == username:
            return str(item["user_id"])
    raise ApiError(500, f"user {username} neither created nor found")


def _require_course_scope(base: str, token: str, course_id: str) -> None:
    """Fail with a runnable remediation when the caller cannot see the seeded course."""
    listing = request("GET", f"{base}/api/v1/courses?limit=200", token=token)
    assert isinstance(listing, dict)
    visible = {str(item["course_id"]) for item in listing.get("items", [])}
    if course_id in visible:
        return
    raise ApiError(
        403,
        f"course {course_id} exists but is not visible to the seeding teacher, so every "
        f"question write would fail with 403. The contract has no endpoint that grants a "
        f"course-scoped role on an existing course, so grant it with B4's operator CLI:\n"
        f"  cd B4 && uv run python -m autograder_b4.ops.roles grant "
        f"--user-id <teacher_id> --course-id {course_id} --role teacher --assistant "
        f"--granted-by <admin_id>",
    )


def build_roster_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "名单"
    sheet.append(["姓名", "学号", "课程编号", "课序号"])
    sheet.append([STUDENT_NAME, STUDENT_NUMBER, COURSE_CODE, SECTION_CODE])
    sheet.append(["王二", "20260002", COURSE_CODE, SECTION_CODE])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def upload_roster(base: str, token: str, path: Path, course_id: str, section_id: str) -> str:
    boundary = f"----autograder{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )

    field("course_id", course_id)
    field("section_id", section_id)
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{path.name}\"\r\nContent-Type: "
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n".encode()
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    document = request(
        "POST",
        f"{base}/api/v1/roster-imports",
        token=token,
        raw=(b"".join(parts), f"multipart/form-data; boundary={boundary}"),
    )
    assert isinstance(document, dict)
    return str(document["roster_import_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the integrated AutoGrader environment.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default="Admin-Local-2026!")
    parser.add_argument(
        "--work-dir",
        default=str(Path(__file__).resolve().parents[1] / ".runtime" / "seed"),
    )
    arguments = parser.parse_args(argv)
    base = arguments.base_url.rstrip("/")
    work = Path(arguments.work_dir)

    print(f"seeding {base}")
    admin = login(base, arguments.admin_username, arguments.admin_password)
    print("  admin login ok")

    teacher_id = ensure_user(
        base,
        admin,
        username="t.zhang",
        display_name=TEACHER_NAME,
        roles=["teacher", "assistant"],
        password=PASSWORD_TEACHER,
    )
    student_id = ensure_user(
        base,
        admin,
        username="s.li",
        display_name=STUDENT_NAME,
        roles=["student"],
        password=PASSWORD_STUDENT,
        student_number=STUDENT_NUMBER,
    )
    print(f"  teacher={teacher_id}")
    print(f"  student={student_id}")

    teacher = login(base, "t.zhang", PASSWORD_TEACHER)

    course = request(
        "POST",
        f"{base}/api/v1/courses",
        token=teacher,
        json_body={"course_code": COURSE_CODE, "title": COURSE_TITLE},
        tolerate=(409,),
    )
    if isinstance(course, dict) and course.get("course_id"):
        course_id = str(course["course_id"])
    else:
        listing = request("GET", f"{base}/api/v1/courses?limit=200", token=admin)
        assert isinstance(listing, dict)
        course_id = next(
            str(item["course_id"])
            for item in listing.get("items", [])
            if item.get("course_code") == COURSE_CODE
        )
    print(f"  course={course_id}")

    # The course is created **by the teacher**, not by the administrator. Creating a
    # course is what establishes the creator's course scope (B4 `create_course` inserts
    # the `teacher` grant for a non-admin caller), and course scope is exactly what B3
    # checks when the teacher publishes a question. Seeding it as the admin -- which is
    # what this script used to do -- produced an environment where every `POST
    # /api/v1/questions` returned 403 "the requested course is outside the caller's
    # authorization scope", with nothing in the failure pointing at the missing grant.
    #
    # On a re-run the course already exists and `POST` answers 409, so the grant has to be
    # verified rather than assumed: the contract exposes no endpoint that grants a
    # course-scoped role on an existing course, so a database that predates this ordering
    # cannot be repaired over HTTP. Say so instead of failing later with a 403.
    _require_course_scope(base, teacher, course_id)

    section = request(
        "POST",
        f"{base}/api/v1/courses/{course_id}/sections",
        token=admin,
        json_body={"section_code": SECTION_CODE, "term": "2026-Fall"},
        tolerate=(409,),
    )
    if isinstance(section, dict) and section.get("section_id"):
        section_id = str(section["section_id"])
    else:
        listing = request("GET", f"{base}/api/v1/courses/{course_id}/sections", token=admin)
        items = listing.get("items", []) if isinstance(listing, dict) else listing
        section_id = next(
            str(item["section_id"]) for item in items if item.get("section_code") == SECTION_CODE
        )
    print(f"  section={section_id}")

    # Enrolling requires the roster pipeline: there is no direct enrollment-create
    # operation, by design (RQ-B-1-10 owns the roster path).
    roster = work / "roster.xlsx"
    build_roster_xlsx(roster)
    import_id = upload_roster(base, admin, roster, course_id, section_id)
    preview = request("GET", f"{base}/api/v1/roster-imports/{import_id}", token=admin)
    assert isinstance(preview, dict)
    print(f"  roster-import={import_id} status={preview.get('status')}")
    if preview.get("status") == "mapping_required":
        columns = {
            "display_name": "姓名",
            "student_number": "学号",
            "course_code": "课程编号",
            "section_code": "课序号",
        }
        request(
            "PUT",
            f"{base}/api/v1/roster-imports/{import_id}/mapping",
            token=admin,
            json_body={"columns": columns},
        )
        preview = request("GET", f"{base}/api/v1/roster-imports/{import_id}", token=admin)
        assert isinstance(preview, dict)
        print(f"  after mapping: status={preview.get('status')}")
    request("POST", f"{base}/api/v1/roster-imports/{import_id}/confirm", token=admin)
    print("  roster confirmed")

    payload = {
        "title": "统计输入的行数",
        "stem_markdown": (
            "## 题目\n\n"
            "从标准输入读取文本，输出其**行数**（以 `\\n` 结尾的行）。\n\n"
            "- 输入可能为空\n"
            "- 输出一个整数并以换行结束\n"
        ),
        "language": "python",
        "reference_answer": "import sys\nprint(len(sys.stdin.read().splitlines()))\n",
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": "a\nb\nc\n",
            "expect_exit_code": 0,
            "comparison": {
                "stdout": "ignore_trailing_whitespace",
                "stderr": "ignored",
                "numeric_tolerance": None,
            },
        },
        "cases": [
            {
                "name": "三行输入",
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
                "name": "隐藏用例：空输入",
                "ordinal": 1,
                "weight": 1,
                "visible_to_student": False,
                "stdin": "",
                "args": [],
                "expected_stdout": "0\n",
                "expected_stderr": None,
                "expected_exit_code": 0,
            },
        ],
        "max_files": 20,
        "max_total_bytes": 1048576,
    }
    question = request(
        "POST", f"{base}/api/v1/questions?course_id={course_id}", token=teacher, json_body=payload
    )
    assert isinstance(question, dict)
    question_id = str(question["question_id"])
    print(f"  question={question_id}")

    version_id = question.get("latest_version_id") or question.get("question_version_id")
    if version_id is None:
        detail = request("GET", f"{base}/api/v1/questions/{question_id}", token=teacher)
        assert isinstance(detail, dict)
        versions = detail.get("versions") or []
        if versions:
            version_id = versions[-1].get("question_version_id")
    if version_id is None:
        raise ApiError(500, f"cannot locate a draft version for question {question_id}: {question}")
    print(f"  version={version_id}")

    # Publishing requires an attestation naming the version's recomputed content (`CHG-0008`).
    # This step used to POST `publish` directly and the seed therefore ended in
    # `422 reference-attestation-missing` -- an independent container end-to-end run hit exactly
    # that, which is how the gap surfaced. Rather than reimplement the sequence here and risk the
    # two copies diverging, reuse the importer's proven one: it is the only place in the project
    # that executes reference answers, and it does so **before** any network call.
    imported.attest_and_publish(base, teacher, version_id, payload)
    print(f"  attested and published version={version_id}")

    assignment = request(
        "POST",
        f"{base}/api/v1/assignments",
        token=teacher,
        json_body={
            "course_id": course_id,
            "section_id": section_id,
            "title": "第一次作业：行数统计",
            "question_version_ids": [version_id],
        },
    )
    print(f"  assignment={json.dumps(assignment, ensure_ascii=False)[:160]}")

    summary = {
        "course_id": course_id,
        "section_id": section_id,
        "question_id": question_id,
        "question_version_id": str(version_id),
        "teacher": {"username": "t.zhang", "password": PASSWORD_TEACHER},
        "student": {
            "username": "s.li",
            "password": PASSWORD_STUDENT,
            "student_number": STUDENT_NUMBER,
            "display_name": STUDENT_NAME,
        },
        "admin": {
            "username": arguments.admin_username,
            "password": arguments.admin_password,
        },
    }
    target = work / "seed-summary.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
