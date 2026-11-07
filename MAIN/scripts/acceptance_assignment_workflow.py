"""End-to-end acceptance for the assignment workflow (CHG-0032).

Runs against a **composed host** (B2+B3+B4 in one process) backed by a real PostgreSQL and
a real NATS instance, with synthetic accounts/course/roster seeded directly into `b4` (the
identity API deliberately cannot create accounts), real HTTP login/tokens, and a real TCP
IMAP fixture server for the mail leg. It proves the cross-module contract that unit suites
cannot: teacher creates a multi-question assignment for multiple sections / selected
students, students submit via web and via mail, the same question version used by two
assignments stays isolated, and the teacher can read authorized answers while a student
cannot read another's.

What is intentionally NOT covered here: sandbox evaluation (no nsjail on the CI runner) and
TLS/external IMAP. Evaluation/worker coverage lives in B2's DB suites; a "failed run is not a
zero" is covered there too. This script fails loudly rather than skipping: an acceptance run
that silently skips is indistinguishable from a passing one.

Usage:
    python MAIN/scripts/acceptance_assignment_workflow.py

Environment (CI supplies them; no secrets are printed):
    ACCEPTANCE_BASE_URL                   composed host, e.g. http://127.0.0.1:18000
    PLATFORM_DATABASE_URL                 platform DSN used only to seed fixtures
    ACCEPTANCE_SERVICE_TOKEN              shared service token (B3/B4 internal channels)
    ACCEPTANCE_IMAP_PORT / USER / PASSWORD  the fixture mailbox (script starts the server)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.message import EmailMessage
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PASSWORD = "Acceptance-Pass-1"
TEACHER = "acc.teacher"
STUDENT_ONE = "acc.student.one"
STUDENT_TWO = "acc.student.two"
COURSE_ID = uuid.UUID("0a000000-0000-4000-8000-00000000c001")
SECTION_A = uuid.UUID("0a000000-0000-4000-8000-00000000a001")
SECTION_B = uuid.UUID("0a000000-0000-4000-8000-00000000b001")
TEACHER_ID = uuid.UUID("0a000000-0000-4000-8000-00000000d001")
STUDENT_ONE_ID = uuid.UUID("0a000000-0000-4000-8000-00000000d002")
STUDENT_TWO_ID = uuid.UUID("0a000000-0000-4000-8000-00000000d003")
STUDENT_ONE_EMAIL = "accept.one@example.edu"
STUDENT_TWO_EMAIL = "accept.two@example.edu"
SERVICE_TOKEN = os.environ.get("ACCEPTANCE_SERVICE_TOKEN", "ci-service-token")
BASE_URL = os.environ.get("ACCEPTANCE_BASE_URL", "http://127.0.0.1:18000").rstrip("/")

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok   {message}")
        return
    failures.append(message)
    print(f"  FAIL {message}")


class HttpError(AssertionError):
    def __init__(self, method: str, path: str, status: int, body: bytes) -> None:
        snippet = body[:600].decode("utf-8", "replace")
        self.status = status
        super().__init__(f"{method} {path} -> HTTP {status}: {snippet}")


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: object | None = None,
    raw_query: str | None = None,
    expect: tuple[int, ...] = (200, 201, 202, 204),
) -> bytes:
    url = BASE_URL + path + (("?" + raw_query) if raw_query else "")
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    request_object = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request_object, timeout=30) as response:
            payload = response.read()
            if response.status not in expect:
                raise HttpError(method, path, response.status, payload)
            return payload
    except urllib.error.HTTPError as error:
        payload = error.read()
        if error.code in expect:
            return payload
        raise HttpError(method, path, error.code, payload) from error


def json_request(method: str, path: str, **kwargs) -> object:
    payload = request(method, path, **kwargs)
    if not payload:
        return None
    return json.loads(payload)


def login(username: str) -> str:
    payload = json_request(
        "POST", "/api/v1/auth/session", body={"username": username, "password": DEFAULT_PASSWORD}
    )
    assert isinstance(payload, dict), payload
    token = payload["access_token"]
    assert isinstance(token, str) and token
    return token


# ---------------------------------------------------------------- fixture seeding


def _psycopg_dsn(raw: str) -> str:
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


def seed_identity_fixtures() -> None:
    """Synthetic course/sections/accounts/enrollments directly in `b4`.

    Accounts cannot be created through the identity API without an administrator, and the
    point of this script is the assignment/submission flow, so the fixture layer is the
    database. Everything after this point is real HTTP with real signed tokens.
    """
    import psycopg
    from autograder_b4.security.passwords import hash_password

    dsn = os.environ.get("PLATFORM_DATABASE_URL")
    if not dsn:
        raise SystemExit("PLATFORM_DATABASE_URL is required to seed the acceptance fixtures")
    hashed = hash_password(DEFAULT_PASSWORD)
    with psycopg.connect(_psycopg_dsn(dsn)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM b4.enrollments WHERE section_id IN (%s,%s)", (SECTION_A, SECTION_B)
            )
            cursor.execute("DELETE FROM b4.sections WHERE section_id IN (%s,%s)", (SECTION_A, SECTION_B))
            cursor.execute("DELETE FROM b4.courses WHERE course_id = %s", (COURSE_ID,))
            cursor.execute("DELETE FROM b4.roles WHERE user_id IN (%s,%s,%s)", (TEACHER_ID, STUDENT_ONE_ID, STUDENT_TWO_ID))
            cursor.execute("DELETE FROM b4.credentials WHERE user_id IN (%s,%s,%s)", (TEACHER_ID, STUDENT_ONE_ID, STUDENT_TWO_ID))
            cursor.execute("DELETE FROM b4.users WHERE user_id IN (%s,%s,%s)", (TEACHER_ID, STUDENT_ONE_ID, STUDENT_TWO_ID))
            cursor.execute(
                "INSERT INTO b4.users (user_id, username, display_name, student_number, email, is_active)"
                " VALUES (%s,%s,%s,%s,%s,true)",
                (TEACHER_ID, TEACHER, "验收教师", None, "accept.teacher@example.edu"),
            )
            cursor.execute(
                "INSERT INTO b4.users (user_id, username, display_name, student_number, email, is_active)"
                " VALUES (%s,%s,%s,%s,%s,true)",
                (STUDENT_ONE_ID, STUDENT_ONE, "验收学生一", "20990001", STUDENT_ONE_EMAIL),
            )
            cursor.execute(
                "INSERT INTO b4.users (user_id, username, display_name, student_number, email, is_active)"
                " VALUES (%s,%s,%s,%s,%s,true)",
                (STUDENT_TWO_ID, STUDENT_TWO, "验收学生二", "20990002", STUDENT_TWO_EMAIL),
            )
            for user_id in (TEACHER_ID, STUDENT_ONE_ID, STUDENT_TWO_ID):
                cursor.execute(
                    "INSERT INTO b4.credentials (credential_id, user_id, password_hash, hash_algorithm)"
                    " VALUES (%s,%s,%s,%s)",
                    (uuid.uuid4(), user_id, hashed.hash, hashed.algorithm),
                )
            cursor.execute(
                "INSERT INTO b4.courses (course_id, course_code, title, is_active)"
                " VALUES (%s,%s,%s,true)",
                (COURSE_ID, "ACC101", "验收课程"),
            )
            cursor.execute(
                "INSERT INTO b4.sections (section_id, course_id, section_code, term) VALUES (%s,%s,%s,%s)",
                (SECTION_A, COURSE_ID, "1", "2026"),
            )
            cursor.execute(
                "INSERT INTO b4.sections (section_id, course_id, section_code, term) VALUES (%s,%s,%s,%s)",
                (SECTION_B, COURSE_ID, "2", "2026"),
            )
            cursor.execute(
                "INSERT INTO b4.roles (role_id, user_id, role, course_id) VALUES (%s,%s,'teacher',%s)",
                (uuid.uuid4(), TEACHER_ID, COURSE_ID),
            )
            for user_id in (STUDENT_ONE_ID, STUDENT_TWO_ID):
                cursor.execute(
                    "INSERT INTO b4.roles (role_id, user_id, role, course_id) VALUES (%s,%s,'student',NULL)",
                    (uuid.uuid4(), user_id),
                )
            cursor.execute(
                "INSERT INTO b4.enrollments (enrollment_id, section_id, user_id, student_number, display_name, source, is_active)"
                " VALUES (%s,%s,%s,%s,%s,'manual',true)",
                (uuid.uuid4(), SECTION_A, STUDENT_ONE_ID, "20990001", "验收学生一"),
            )
            cursor.execute(
                "INSERT INTO b4.enrollments (enrollment_id, section_id, user_id, student_number, display_name, source, is_active)"
                " VALUES (%s,%s,%s,%s,%s,'manual',true)",
                (uuid.uuid4(), SECTION_B, STUDENT_TWO_ID, "20990002", "验收学生二"),
            )


# ---------------------------------------------------------------- question fixtures


def create_published_version(teacher: str, title: str, expected: str) -> str:
    created = json_request(
        "POST",
        "/api/v1/questions",
        token=teacher,
        raw_query=urllib.parse.urlencode({"course_id": str(COURSE_ID)}),
        body={
            "title": title,
            "stem_markdown": f"## {title}\n\n请提交能够输出 `{expected}` 的 bash 脚本。",
            "language": "bash",
            "reference_answer": f"echo {expected}\n",
            "adapter": {
                "kind": "stdin",
                "args": [],
                "stdin": None,
                "expect_exit_code": 0,
                "comparison": {"stdout": "exact", "stderr": "ignored"},
            },
            "cases": [{"name": "case-1", "ordinal": 0, "expected_stdout": f"{expected}\n"}],
        },
    )
    assert isinstance(created, dict), created
    version_id = str(created["versions"][0]["question_version_id"])
    version = json_request("GET", f"/api/v1/question-versions/{version_id}", token=teacher)
    digest = version.get("content_digest")
    if not digest:
        # The public DTO is allowed to omit the digest for a draft; the attestation fixture
        # still needs the value B3 will compare against. Read it from the owning schema
        # rather than recomputing a second implementation of the canonicalisation here.
        import psycopg

        with psycopg.connect(_psycopg_dsn(os.environ["PLATFORM_DATABASE_URL"])) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT content_digest FROM b3.question_versions WHERE question_version_id = %s",
                    (version_id,),
                )
                digest = cursor.fetchone()[0]
    json_request(
        "POST",
        f"/api/v1/question-versions/{version_id}/attestations",
        token=SERVICE_TOKEN,
        expect=(201,),
        body={
            "content_digest": digest,
            "verifier": "acceptance-fixture",
            "cases_total": 1,
            "cases_passed": 1,
        },
    )
    json_request("POST", f"/api/v1/question-versions/{version_id}/publish", token=teacher)
    return version_id


def create_assignment(teacher: str, title: str, section_ids: list[str], student_ids: list[str], versions: list[str]) -> str:
    body: dict[str, object] = {
        "course_id": str(COURSE_ID),
        "section_ids": section_ids,
        "title": title,
        "question_version_ids": versions,
    }
    if student_ids:
        body["student_ids"] = student_ids
    created = json_request("POST", "/api/v1/assignments", token=teacher, expect=(201,), body=body)
    assert isinstance(created, dict), created
    return str(created["assignment_id"])


# ---------------------------------------------------------------- submission helpers


def envelope(
    *,
    assignment_id: str,
    student_id: str,
    section_id: str,
    question_version_id: str,
    ordinal: int,
    content: str,
) -> dict[str, object]:
    data = content.encode("utf-8")
    return {
        "submission_id": str(uuid.uuid4()),
        "assignment_id": assignment_id,
        "student_id": student_id,
        "course_id": str(COURSE_ID),
        "section_id": section_id,
        "question_version_id": question_version_id,
        "idempotency_key": str(uuid.uuid4()),
        "files": [
            {
                "path": f"{ordinal}.txt",
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "content_base64": base64.b64encode(data).decode("ascii"),
            }
        ],
    }


def submit(token: str, body: dict[str, object]) -> str:
    accepted = json_request("POST", "/api/v1/submissions", token=token, expect=(202,), body=body)
    assert isinstance(accepted, dict), accepted
    return str(accepted["submission_id"])


def grades(token: str, assignment_id: str) -> list[dict[str, object]]:
    page = json_request(
        "GET",
        "/api/v1/grades",
        token=token,
        raw_query=urllib.parse.urlencode(
            {"course_id": str(COURSE_ID), "assignment_id": assignment_id, "limit": 200}
        ),
    )
    assert isinstance(page, dict), page
    return list(page.get("items") or [])


def build_message(*, sender: str, name: str, number: str, code: str, assignment_id: str, answers: dict[int, str]) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "acceptance-inbox@example.edu"
    message["Subject"] = f"{name} {number} 作业提交"
    message["Message-ID"] = f"<{uuid.uuid4()}@example.edu>"
    body = "\n".join(
        [
            f"核验码：{code}",
            f"作业编号：{assignment_id}",
            "课序：1",
            f"姓名：{name}",
            f"学号：{number}",
            "",
            "附件映射：" + "、".join(f"{ordinal}.txt" for ordinal in sorted(answers)),
        ]
    )
    message.set_content(body, charset="utf-8")
    for ordinal, content in sorted(answers.items()):
        message.add_attachment(
            content.encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename=f"{ordinal}.txt",
        )
    return message.as_bytes()


def run_pull_cli(port: int, username: str, password: str) -> None:
    # The composed host already received the complete B2_* environment; the puller runs as
    # the same module identity, so only the mailbox location changes.
    env = dict(os.environ)
    env.update(
        {
            "B2_IMAP_HOST": "127.0.0.1",
            "B2_IMAP_PORT": str(port),
            "B2_IMAP_USERNAME": username,
            "B2_IMAP_PASSWORD": password,
            "B2_IMAP_MAILBOX": "INBOX",
            "B2_IMAP_USE_SSL": "false",
            "B2_IDENTITY_URL": BASE_URL,
            "B2_IDENTITY_AUTHORIZATION": f"Bearer {SERVICE_TOKEN}",
            "B2_B3_BASE_URL": BASE_URL,
            "B2_B3_AUTHORIZATION": f"Bearer {SERVICE_TOKEN}",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "autograder_b2.intake.pull", "--once"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "mail pull failed: rc="
            f"{completed.returncode}\nstdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )


# ---------------------------------------------------------------- main


def main() -> int:
    print("== assignment workflow acceptance (composed host, real DB, real IMAP socket) ==")
    seed_identity_fixtures()
    teacher = login(TEACHER)
    student_one = login(STUDENT_ONE)
    student_two = login(STUDENT_TWO)

    print("\n-- teacher: published questions / multi-section assignment --")
    version_one = create_published_version(teacher, "验收题一：输出 alpha", "alpha")
    version_two = create_published_version(teacher, "验收题二：输出 beta", "beta")
    assignment_one = create_assignment(
        teacher, "验收作业 A（两个课序）", [str(SECTION_A), str(SECTION_B)], [], [version_one, version_two]
    )
    # Same question versions, different assignment, audience narrowed to section A and one
    # named student: the isolation case that the assignment_id threading exists for.
    assignment_two = create_assignment(
        teacher, "验收作业 B（课序1，仅指定学生一）", [str(SECTION_A)], [str(STUDENT_ONE_ID)], [version_one, version_two]
    )
    listed = json_request(
        "GET",
        "/api/v1/assignments",
        token=teacher,
        raw_query=urllib.parse.urlencode({"course_id": str(COURSE_ID), "limit": 200}),
    )
    assignment_ids = {str(item["assignment_id"]) for item in (listed or {}).get("items", [])}
    check(assignment_one in assignment_ids and assignment_two in assignment_ids, "teacher lists both assignments")
    check(
        str(SECTION_A) in json_request("GET", f"/api/v1/assignments/{assignment_one}", token=teacher)["section_ids"],
        "assignment keeps multiple target sections",
    )

    print("\n-- student web submission: partial, retake, cross-assignment isolation --")
    submission_a1 = submit(
        student_one,
        envelope(
            assignment_id=assignment_one,
            student_id=str(STUDENT_ONE_ID),
            section_id=str(SECTION_A),
            question_version_id=version_one,
            ordinal=1,
            content="echo alpha\n",
        ),
    )
    submission_a2_v2 = submit(
        student_one,
        envelope(
            assignment_id=assignment_one,
            student_id=str(STUDENT_ONE_ID),
            section_id=str(SECTION_A),
            question_version_id=version_two,
            ordinal=2,
            content="echo beta\n",
        ),
    )
    submission_b1 = submit(
        student_one,
        envelope(
            assignment_id=assignment_two,
            student_id=str(STUDENT_ONE_ID),
            section_id=str(SECTION_A),
            question_version_id=version_one,
            ordinal=1,
            content="echo alpha for B\n",
        ),
    )
    check(len({submission_a1, submission_a2_v2, submission_b1}) == 3, "same version in two assignments creates distinct submissions")
    rows_a = grades(teacher, assignment_one)
    rows_b = grades(teacher, assignment_two)
    check(
        {str(row["submission_id"]) for row in rows_a} == {submission_a1, submission_a2_v2},
        "assignment A grades contain exactly its two web submissions",
    )
    check(
        {str(row["submission_id"]) for row in rows_b} == {submission_b1},
        "assignment B grades do not leak assignment A submissions",
    )
    check(
        all(str(row.get("assignment_id")) == assignment_one for row in rows_a),
        "every grade row carries its assignment_id",
    )
    check(
        all(str(row.get("assignment_id")) == assignment_two for row in rows_b),
        "assignment-scoped query returns the matching assignment only",
    )
    retake = submit(
        student_one,
        envelope(
            assignment_id=assignment_one,
            student_id=str(STUDENT_ONE_ID),
            section_id=str(SECTION_A),
            question_version_id=version_one,
            ordinal=1,
            content="echo alpha\n# retake\n",
        ),
    )
    check(retake != submission_a1, "a retake is a new submission record")

    print("\n-- audience: non-target section / student rejection --")
    try:
        request(
            "POST",
            "/api/v1/submissions",
            token=student_two,
            expect=(403,),
            body=envelope(
                assignment_id=assignment_two,
                student_id=str(STUDENT_TWO_ID),
                section_id=str(SECTION_B),
                question_version_id=version_one,
                ordinal=1,
                content="echo nope\n",
            ),
        )
        check(False, "student outside the narrowed assignment audience is refused with 403")
    except HttpError as error:
        check(error.status == 403, f"student outside the narrowed assignment audience is refused with 403 (got {error.status})")
    # A version that is not part of the assignment is a data error (422), not a guess.
    other_version = create_published_version(teacher, "验收题三：输出 gamma", "gamma")
    request(
        "POST",
        "/api/v1/submissions",
        token=student_one,
        expect=(422,),
        body=envelope(
            assignment_id=assignment_one,
            student_id=str(STUDENT_ONE_ID),
            section_id=str(SECTION_A),
            question_version_id=other_version,
            ordinal=1,
            content="echo gamma\n",
        ),
    )
    check(True, "question version outside the assignment is refused with 422")

    print("\n-- answer files: authorized reads --")
    own = request("GET", f"/api/v1/submissions/{submission_a1}/files", token=student_one, raw_query="path=1.txt")
    check(own.decode("utf-8").strip() == "echo alpha", "student can read their own answer file")
    request("GET", f"/api/v1/submissions/{submission_a1}/files", token=student_two, expect=(403,), raw_query="path=1.txt")
    check(True, "another student cannot read the answer file (403)")
    teacher_view = request("GET", f"/api/v1/submissions/{submission_a1}/files", token=teacher, raw_query="path=1.txt")
    check(teacher_view.decode("utf-8").strip() == "echo alpha", "teacher can read the authorized student's answer")
    view = json_request("GET", f"/api/v1/submissions/{submission_a1}", token=teacher)
    check(str(view["assignment_id"]) == assignment_one, "submission view exposes assignment_id")

    print("\n-- mail submission: challenge, multi-attachment mapping, idempotent duplicate --")
    sys.path.insert(0, str(REPO_ROOT / "B2" / "tests"))
    from imap_fixture import FixtureMessage, ImapFixtureServer  # noqa: PLC0415 - test fixture from B2

    imap_username = "acceptance-inbox"
    imap_password = "acceptance-imap-pass"
    challenge = json_request(
        "POST",
        "/api/v1/email-verification/challenges",
        token=student_one,
        expect=(201,),
        body={
            "course_id": str(COURSE_ID),
            "section_id": str(SECTION_A),
            "assignment_id": assignment_one,
        },
    )
    code = challenge["code"]
    challenge_id = challenge["challenge_id"]
    check(str(challenge["assignment_id"]) == assignment_one, "challenge is bound to the assignment")
    message = build_message(
        sender=STUDENT_ONE_EMAIL,
        name="验收学生一",
        number="20990001",
        code=code,
        assignment_id=assignment_one,
        answers={1: "echo alpha via mail\n", 2: "echo beta via mail\n"},
    )
    imap = ImapFixtureServer(
        messages=[FixtureMessage(uid=1, raw=message)],
        username=imap_username,
        password=imap_password,
    ).start()
    try:
        run_pull_cli(imap.port, imap_username, imap_password)
        status = json_request("GET", f"/api/v1/email-verification/challenges/{challenge_id}", token=student_one)
        for _ in range(20):
            if status["state"] != "pending":
                break
            time.sleep(0.5)
            status = json_request("GET", f"/api/v1/email-verification/challenges/{challenge_id}", token=student_one)
        check(status["state"] == "received", f"challenge receives the real mail (state={status['state']})")
        mapped = status.get("submissions") or []
        check(len(mapped) == 2, f"one mail maps two attachments to two per-question submissions (got {len(mapped)})")
        email_submission_ids = {str(item["submission_id"]) for item in mapped}
        email_rows = [row for row in grades(teacher, assignment_one) if str(row["submission_id"]) in email_submission_ids]
        check(len(email_rows) == 2, "mail submissions appear in the same assignment grade query")
        check(
            all(str(row["assignment_id"]) == assignment_one for row in email_rows),
            "mail submissions carry the same assignment_id as web submissions",
        )
        mapped_question_ids = {str(item["question_version_id"]) for item in mapped}
        check(mapped_question_ids == {version_one, version_two}, "mail mapping preserves question identity")
        mail_view = json_request("GET", f"/api/v1/submissions/{sorted(email_submission_ids)[0]}", token=teacher)
        check(mail_view["intake"] == "email", "mail submission is the same record shape with intake=email")
        mail_file = request(
            "GET",
            f"/api/v1/submissions/{sorted(email_submission_ids)[0]}/files",
            token=teacher,
            raw_query="path=1.txt",
        )
        check(b"via mail" in mail_file, "teacher can read the mail-delivered answer file")

        # Duplicate delivery: same Message-ID under a new UID must be idempotent.
        imap.append(2, message)
        run_pull_cli(imap.port, imap_username, imap_password)
        after = json_request("GET", f"/api/v1/email-verification/challenges/{challenge_id}", token=student_one)
        check(
            {str(item["submission_id"]) for item in after.get("submissions") or []} == email_submission_ids,
            "duplicate delivery does not create a second set of submissions",
        )

        # Explainable rejection: an attachment that cannot be mapped to a question is
        # quarantined and the student-facing challenge reports the stable reason code.
        second = json_request(
            "POST",
            "/api/v1/email-verification/challenges",
            token=login(STUDENT_TWO),
            expect=(201,),
            body={
                "course_id": str(COURSE_ID),
                "section_id": str(SECTION_B),
                "assignment_id": assignment_one,
            },
        )
        invalid = build_message(
            sender=STUDENT_TWO_EMAIL,
            name="验收学生二",
            number="20990002",
            code=second["code"],
            assignment_id=assignment_one,
            answers={9: "echo out of range\n"},
        )
        imap.append(3, invalid)
        run_pull_cli(imap.port, imap_username, imap_password)
        rejected = json_request(
            "GET",
            f"/api/v1/email-verification/challenges/{second['challenge_id']}",
            token=login(STUDENT_TWO),
        )
        for _ in range(20):
            if rejected["state"] != "pending":
                break
            time.sleep(0.5)
            rejected = json_request(
                "GET",
                f"/api/v1/email-verification/challenges/{second['challenge_id']}",
                token=login(STUDENT_TWO),
            )
        check(
            rejected["state"] == "rejected" and rejected.get("reason_code") == "attachment_mapping_invalid",
            f"unmappable attachment is rejected with an explainable reason (state={rejected['state']}, code={rejected.get('reason_code')})",
        )
        quarantines = json_request(
            "GET",
            "/api/v1/intake/quarantines",
            token=teacher,
            raw_query=urllib.parse.urlencode({"course_id": str(COURSE_ID), "limit": 200}),
        )
        reasons = [str(row["reason_code"]) for row in (quarantines or {}).get("items", [])]
        check("attachment_mapping_invalid" in reasons, "the unmappable mail is in the explainable quarantine list")
    finally:
        imap.stop()

    print("\n-- done --")
    if failures:
        print(f"ACCEPTANCE FAILED ({len(failures)} check(s)):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
