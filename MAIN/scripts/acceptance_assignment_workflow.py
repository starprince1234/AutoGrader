"""End-to-end acceptance for the v0.32.0 course -> sections -> classes model.

Runs against a **composed host** (B2+B3+B4 in one process) backed by a real PostgreSQL and
NATS, a real TCP **SMTP sink** started by this script (so the host's B4 SMTP configuration can
point at it) and the repository's real TCP IMAP fixture for the inbound mail leg. Nothing here
is a mock of a business decision: accounts are created through the real admin/registration
APIs (verification codes are read from the sink), rosters go through the real import and
attach APIs, notifications are materialised by B3 calling B4's internal endpoint and delivered
by the real `python -m autograder_b4.ops.notifications` single pass.

What the run proves (design section 6):

* one course with two sections and two classes per section, plus a second course that shares a
  student with the first one;
* both enrolment orderings: roster/registered class first and registration later (the
  account binds the waiting roster rows), and registration first with the roster imported
  later (the imported row binds immediately);
* "a student may hold at most one active section per course" as a **database** invariant:
  class attach answers 409, roster confirm rejects the row with `conflict_other_section`, a
  duplicate direct INSERT is refused, and concurrent attach/import requests are arbitrated
  without a 5xx and without writing two active rows;
* one course-scoped publication is visible to every section/class student, is invisible to a
  student of another course (list, detail and submit are refused), and stays visible to
  students and whole new sections/classes added after publication;
* the teacher's course-wide roster pages through **all** rows, including unregistered students
  (`student_id` null), and its counts agree with the server-side `roster-summary`;
* removing a student from the course takes effect immediately for reads, web submissions,
  challenge issuance and grades (the B4 authorization is authoritative, not the token
  snapshot), while the course they remain in keeps working;
* web and mail intake enforce the same (student, assignment, question) authorization and the
  same authoritative section; an existing token cannot claim a different section;
* the publication notification is materialised for the whole active audience, delivered over
  real SMTP to exactly the expected recipients, and the three skipped reasons are distinct
  from a real delivery failure and from an idempotent repeat pass.

What is intentionally NOT covered here: sandbox evaluation (no nsjail on the CI runner), the
production notification timer/deploy path, TLS/external SMTP or IMAP, the migration-time
precheck for *pre-existing* duplicate enrollments (a fresh schema cannot contain them; this
acceptance instead proves the resulting constraint and every request path that must refuse a
conflict), and the browser UI.

Usage:
    python MAIN/scripts/acceptance_assignment_workflow.py

Environment (CI supplies them; no secret is printed):
    ACCEPTANCE_BASE_URL      composed host, e.g. http://127.0.0.1:18000
    PLATFORM_DATABASE_URL    platform DSN used to seed the administrator and read invariants
    ACCEPTANCE_SERVICE_TOKEN shared service token (B3/B4 internal channels)
    B4_DATABASE_URL          least-privilege DSN the notification sender CLI runs with
    SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM/SMTP_FROM_NAME/SMTP_TLS_MODE
                             the host's mail configuration; it must point at this script's
                             sink (`ACCEPTANCE_SMTP_PORT`) with TLS disabled
    ACCEPTANCE_SMTP_PORT     TCP port this script's sink binds (the host must use it too)

The schemas must be freshly migrated (the workflow recreates b2/b3/b4 before starting the
host); the acceptance fixtures use the identifiable prefix `验收032-` / `acc032.`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_email_policy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PASSWORD = "Acceptance-Pass-032"
SERVICE_TOKEN = os.environ.get("ACCEPTANCE_SERVICE_TOKEN", "ci-service-token")
BASE_URL = os.environ.get("ACCEPTANCE_BASE_URL", "http://127.0.0.1:18000").rstrip("/")
SINK_HOST = "127.0.0.1"

TITLE_A = "验收032-课程级作业A"
TITLE_B = "验收032-他课作业B"
COURSE_A_TITLE = "验收032-课程A"
COURSE_B_TITLE = "验收032-课程B"
COURSE_C_TITLE = "验收032-课程C并发与注册"
TEACHER_USERNAME = "acc032.teacher"
ADMIN_USERNAME = "acc032.admin"
ACCEPTANCE_INBOX = "acceptance-inbox@example.edu"

failures: list[str] = []


class AcceptanceFailure(RuntimeError):
    """A check that later steps depend on failed; the run cannot continue honestly."""


# --------------------------------------------------------------------------- SMTP sink


@dataclass(frozen=True)
class SinkMessage:
    mail_from: str
    recipients: tuple[str, ...]
    data: bytes


class SmtpSink:
    """Minimal threaded ESMTP server that records what it was asked to deliver.

    It speaks just enough SMTP for `smtplib` in the host and in the notification CLI: EHLO,
    AUTH PLAIN/LOGIN (the B4 configuration requires a user and password, so the client always
    authenticates), MAIL, RCPT, DATA, RSET and QUIT. A test can mark recipient addresses as
    refused; the server then answers a real `550`, which is how the acceptance run produces a
    genuine `failed` delivery instead of fabricating one.
    """

    def __init__(self, port: int) -> None:
        self._port = port
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._messages: list[SinkMessage] = []
        self._rejected: list[str] = []
        self._connections: list[socket.socket] = []
        self._workers: list[threading.Thread] = []
        self.reject_recipients: set[str] = set()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> SmtpSink:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((SINK_HOST, self._port))
        except OSError as error:
            raise AcceptanceFailure(
                f"cannot bind the SMTP sink on {SINK_HOST}:{self._port}: {error}"
            ) from error
        listener.listen(16)
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._accept_loop, name="smtp-sink", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        for connection in list(self._connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    # --- state -------------------------------------------------------------
    def snapshot(self) -> list[SinkMessage]:
        with self._lock:
            return list(self._messages)

    def rejected_recipients(self) -> list[str]:
        with self._lock:
            return list(self._rejected)

    def messages_for_title(self, title: str) -> list[SinkMessage]:
        return [message for message in self.snapshot() if title in decoded_text(message.data)]

    def wait_for_code(self, address: str, timeout: float = 60.0) -> str:
        """Return the six-digit verification code the sink received for `address`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in reversed(self.snapshot()):
                if address not in message.recipients:
                    continue
                match = re.search(r"(?<!\d)(\d{6})(?!\d)", decoded_text(message.data))
                if match is not None:
                    return match.group(1)
            time.sleep(0.25)
        raise AcceptanceFailure(f"the SMTP sink did not receive a verification code for {address}")

    # --- server ------------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _address = listener.accept()
            except OSError:
                continue
            with self._lock:
                self._connections.append(connection)
            worker = threading.Thread(
                target=self._serve, args=(connection,), name="smtp-connection", daemon=True
            )
            with self._lock:
                self._workers.append(worker)
            worker.start()

    def _serve(self, connection: socket.socket) -> None:
        try:
            with connection, connection.makefile("rb") as reader, connection.makefile("wb") as writer:
                mail_from = ""
                recipients: list[str] = []

                def send(line: str) -> None:
                    writer.write(line.encode("ascii", "replace") + b"\r\n")
                    writer.flush()

                send("220 autograder-acceptance-smtp ESMTP sink")
                while True:
                    raw = reader.readline(10_000_000)
                    if not raw:
                        break
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    upper = line.upper()
                    if upper.startswith("EHLO"):
                        for capability in (
                            "autograder-acceptance-smtp",
                            "AUTH PLAIN LOGIN",
                            "8BITMIME",
                        ):
                            send(f"250-{capability}")
                        send("250 SIZE 20971520")
                    elif upper.startswith("HELO"):
                        send("250 autograder-acceptance-smtp")
                    elif upper.startswith("AUTH PLAIN"):
                        send("235 2.7.0 Authentication successful")
                    elif upper.startswith("AUTH LOGIN"):
                        send("334 VXNlcm5hbWU6")
                        reader.readline(10_000_000)
                        send("334 UGFzc3dvcmQ6")
                        reader.readline(10_000_000)
                        send("235 2.7.0 Authentication successful")
                    elif upper.startswith("MAIL FROM:"):
                        mail_from = address_of(line)
                        recipients = []
                        send("250 2.1.0 sender accepted")
                    elif upper.startswith("RCPT TO:"):
                        address = address_of(line)
                        with self._lock:
                            refused = address in self.reject_recipients
                            if refused:
                                self._rejected.append(address)
                            else:
                                recipients.append(address)
                        if refused:
                            send("550 5.1.1 recipient address rejected")
                        else:
                            send("250 2.1.5 recipient accepted")
                    elif upper.startswith("DATA"):
                        send("354 end data with <CR><LF>.<CR><LF>")
                        chunks: list[bytes] = []
                        while True:
                            data_line = reader.readline(20_000_000)
                            if not data_line:
                                break
                            if data_line in (b".\r\n", b".\n"):
                                break
                            chunks.append(data_line)
                        with self._lock:
                            self._messages.append(
                                SinkMessage(mail_from, tuple(recipients), b"".join(chunks))
                            )
                        send("250 2.0.0 message accepted")
                    elif upper.startswith("RSET"):
                        mail_from = ""
                        recipients = []
                        send("250 2.0.0 reset")
                    elif upper.startswith("NOOP"):
                        send("250 2.0.0 ok")
                    elif upper.startswith("STARTTLS"):
                        send("502 5.5.1 STARTTLS is not supported by the acceptance sink")
                    elif upper.startswith("QUIT"):
                        send("221 2.0.0 bye")
                        break
                    else:
                        send("502 5.5.1 command not implemented")
        except OSError:
            pass
        finally:
            with self._lock:
                if connection in self._connections:
                    self._connections.remove(connection)


def address_of(line: str) -> str:
    start = line.find("<")
    end = line.find(">", start + 1)
    if start >= 0 and end > start:
        return line[start + 1 : end].strip()
    return line.split(":", 1)[1].strip() if ":" in line else ""


def decoded_text(raw: bytes) -> str:
    """Decode the readable text of a MIME message (plain parts only)."""
    try:
        message = message_from_bytes(raw, policy=default_email_policy)
    except Exception:  # pragma: no cover - defensive: a malformed message is a real failure
        return raw.decode("utf-8", "replace")
    chunks: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        try:
            content = part.get_content()
        except Exception:  # pragma: no cover - malformed transfer encoding
            continue
        if isinstance(content, str):
            chunks.append(content)
    return "\n".join(chunks)


SINK: SmtpSink | None = None


# --------------------------------------------------------------------------- HTTP helpers


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self) -> object:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))


def snippet(body: bytes) -> str:
    return body[:500].decode("utf-8", "replace")


def http(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: object | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
    query: list[tuple[str, object]] | None = None,
    timeout: float = 60.0,
) -> Response:
    url = BASE_URL + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    payload: bytes | None = None
    headers = {"accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    elif data is not None:
        payload = data
        headers["content-type"] = content_type or "application/octet-stream"
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Response(response.status, response.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read())
    except urllib.error.URLError as error:
        raise AcceptanceFailure(f"{method} {path}: transport error: {error.reason}") from error


def check(condition: bool, label: str) -> bool:
    if condition:
        print(f"  ok   {label}")
        return True
    failures.append(label)
    print(f"  FAIL {label}")
    return False


def must(response: Response, expected: tuple[int, ...], label: str) -> Response:
    if response.status not in expected:
        detail = f"{label} -> HTTP {response.status}: {snippet(response.body)}"
        failures.append(detail)
        print(f"  FAIL {detail}")
        raise AcceptanceFailure(detail)
    print(f"  ok   {label}")
    return response


def must_json(response: Response, expected: tuple[int, ...], label: str) -> object:
    payload = must(response, expected, label).json()
    if payload is None:
        raise AcceptanceFailure(f"{label}: empty response body")
    return payload


def paginate(
    path: str,
    token: str,
    *,
    query: list[tuple[str, object]] | None = None,
    limit: int = 2,
    label: str,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    cursor: str | None = None
    for _ in range(200):
        params: list[tuple[str, object]] = list(query or []) + [("limit", limit)]
        if cursor:
            params.append(("cursor", cursor))
        page = must_json(http("GET", path, token=token, query=params), (200,), f"{label} page")
        assert isinstance(page, dict), page
        page_items = page.get("items") or []
        items.extend(page_items)
        cursor = page.get("next_cursor")
        if not cursor:
            return items
    raise AcceptanceFailure(f"{label}: pagination did not terminate")


def multipart(
    fields: dict[str, str], files: list[tuple[str, str, bytes, str]]
) -> tuple[bytes, str]:
    boundary = f"----acceptance-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        chunks.append(value.encode("utf-8") + b"\r\n")
    for name, filename, content, media_type in files:
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {media_type}\r\n\r\n"
            ).encode("ascii")
        )
        chunks.append(content + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def csv_bytes(rows: list[tuple[str, str]]) -> bytes:
    lines = ["姓名,学号"]
    lines.extend(f"{display_name},{student_number}" for display_name, student_number in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- fixtures


@dataclass(frozen=True)
class StudentSpec:
    key: str
    username: str
    display_name: str
    student_number: str
    email: str | None


def student_specs() -> dict[str, StudentSpec]:
    def number(index: int) -> str:
        return f"2099032{index:06d}"

    specs = [
        StudentSpec(
            "reg", "acc032.student.reg", "验收032-先导入后注册生", number(1),
            "acc032.reg@example.edu",
        ),
        StudentSpec(
            "unbound", "acc032.student.unbound", "验收032-未注册生", number(2), None
        ),
        StudentSpec(
            "multi", "acc032.student.multi", "验收032-两课学生", number(3),
            "acc032.multi@example.edu",
        ),
        StudentSpec(
            "no_contact", "acc032.student.nocontact", "验收032-无邮箱生", number(4), None
        ),
        StudentSpec(
            "inactive", "acc032.student.inactive", "验收032-停用生", number(5),
            "acc032.inactive@example.edu",
        ),
        StudentSpec(
            "web", "acc032.student.web", "验收032-网页提交生", number(6),
            "acc032.web@example.edu",
        ),
        StudentSpec(
            "mail", "acc032.student.mail", "验收032-邮件提交生", number(7),
            "acc032.mail@example.edu",
        ),
        StudentSpec(
            "cross", "acc032.student.cross", "验收032-他课学生", number(8),
            "acc032.cross@example.edu",
        ),
        StudentSpec(
            "pre", "acc032.student.pre", "验收032-先注册后导入生", number(9),
            "acc032.pre@example.edu",
        ),
        StudentSpec(
            "mismatch", "acc032.student.mismatch", "验收032-姓名一致生", number(10),
            "acc032.mismatch@example.edu",
        ),
        StudentSpec(
            "new", "acc032.student.new", "验收032-发布后新生", number(11),
            "acc032.new@example.edu",
        ),
        StudentSpec(
            "conc", "acc032.student.conc", "验收032-并发生", number(12),
            "acc032.conc@example.edu",
        ),
        StudentSpec(
            "idem", "acc032.student.idem", "验收032-并发班级生", number(13), None
        ),
    ]
    return {spec.key: spec for spec in specs}


@dataclass
class Context:
    admin_token: str = ""
    teacher_token: str = ""
    courses: dict[str, str] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    classes: dict[str, str] = field(default_factory=dict)
    batches: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    assignments: dict[str, str] = field(default_factory=dict)
    specs: dict[str, StudentSpec] = field(default_factory=lambda: student_specs())

    def spec(self, key: str) -> StudentSpec:
        return self.specs[key]

    def require(self, kind: str, key: str) -> str:
        collection = {
            "course": self.courses,
            "section": self.sections,
            "class": self.classes,
            "user": self.users,
            "token": self.tokens,
            "version": self.versions,
            "assignment": self.assignments,
        }[kind]
        value = collection.get(key)
        if not value:
            raise AcceptanceFailure(f"fixture '{kind}:{key}' is missing; an earlier step failed")
        return value


# --------------------------------------------------------------------------- SQL helpers


def platform_dsn() -> str:
    raw = os.environ.get("PLATFORM_DATABASE_URL", "").strip()
    if not raw:
        raise AcceptanceFailure("PLATFORM_DATABASE_URL is required to seed the administrator")
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


def sql_rows(statement: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    import psycopg

    with psycopg.connect(platform_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return list(cursor.fetchall())


def seed_admin() -> str:
    """Create (or recreate) the one account the public APIs cannot bootstrap themselves.

    Everything else in this acceptance run goes through real APIs. The administrator is the
    single SQL-seeded principal because the platform deliberately has no "create the first
    administrator" HTTP endpoint; with it, teachers and ordinary accounts are created through
    `POST /api/v1/users` and students register through the real allowlist + SMTP-code flow.
    """
    from autograder_b4.security.passwords import hash_password

    import psycopg

    hashed = hash_password(DEFAULT_PASSWORD)
    admin_id = uuid.uuid4()
    with psycopg.connect(platform_dsn()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT user_id FROM b4.users WHERE username = %s", (ADMIN_USERNAME,))
            for user_id in [row[0] for row in cursor.fetchall()]:
                cursor.execute("DELETE FROM b4.sessions WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM b4.roles WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM b4.credentials WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM b4.users WHERE user_id = %s", (user_id,))
            cursor.execute(
                "INSERT INTO b4.users (user_id, username, display_name, student_number, email,"
                " is_active) VALUES (%s,%s,%s,NULL,NULL,true)",
                (admin_id, ADMIN_USERNAME, "验收032-管理员"),
            )
            cursor.execute(
                "INSERT INTO b4.credentials (credential_id, user_id, password_hash,"
                " hash_algorithm) VALUES (%s,%s,%s,%s)",
                (uuid.uuid4(), admin_id, hashed.hash, hashed.algorithm),
            )
            cursor.execute(
                "INSERT INTO b4.roles (role_id, user_id, role, course_id) VALUES (%s,%s,'admin',NULL)",
                (uuid.uuid4(), admin_id),
            )
    return str(admin_id)


# --------------------------------------------------------------------------- API helpers


def login(username: str) -> str:
    payload = must_json(
        http("POST", "/api/v1/auth/session", body={"username": username, "password": DEFAULT_PASSWORD}),
        (200,),
        f"login {username}",
    )
    assert isinstance(payload, dict), payload
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise AcceptanceFailure(f"login {username}: no access token in the response")
    return token


def create_user(ctx: Context, key: str, roles: tuple[str, ...] = ("student",)) -> str:
    spec = ctx.spec(key)
    body: dict[str, object] = {
        "display_name": spec.display_name,
        "username": spec.username,
        "roles": list(roles),
        "password": DEFAULT_PASSWORD,
    }
    if spec.student_number:
        body["student_number"] = spec.student_number
    if spec.email:
        body["email"] = spec.email
    payload = must_json(
        http("POST", "/api/v1/users", token=ctx.admin_token, body=body),
        (201,),
        f"admin creates {spec.username}",
    )
    assert isinstance(payload, dict), payload
    return str(payload["user_id"])


def create_course(ctx: Context, key: str, title: str) -> str:
    payload = must_json(
        http("POST", "/api/v1/courses", token=ctx.teacher_token, body={"title": title}),
        (201,),
        f"teacher creates course {key}",
    )
    assert isinstance(payload, dict), payload
    return str(payload["course_id"])


def create_section(ctx: Context, key: str, course_key: str, code: str) -> str:
    payload = must_json(
        http(
            "POST",
            f"/api/v1/courses/{ctx.require('course', course_key)}/sections",
            token=ctx.teacher_token,
            body={"section_code": code, "term": "2026"},
        ),
        (201,),
        f"teacher creates section {key}",
    )
    assert isinstance(payload, dict), payload
    return str(payload["section_id"])


def create_class(ctx: Context, key: str, section_key: str, name: str) -> str:
    payload = must_json(
        http(
            "POST",
            f"/api/v1/sections/{ctx.require('section', section_key)}/classes",
            token=ctx.teacher_token,
            body={"class_name": name},
        ),
        (201,),
        f"teacher creates class {key}",
    )
    assert isinstance(payload, dict), payload
    return str(payload["class_id"])


def upload_allowlist(ctx: Context, key: str, filename: str, rows: list[tuple[str, str]]) -> str:
    body, content_type = multipart({}, [("file", filename, csv_bytes(rows), "text/csv")])
    payload = must_json(
        http(
            "POST",
            "/api/v1/registration-allowlists",
            token=ctx.admin_token,
            data=body,
            content_type=content_type,
        ),
        (200,),
        f"admin uploads registration allowlist {filename}",
    )
    assert isinstance(payload, dict), payload
    if len(rows):
        check(
            int(payload.get("row_count", -1)) == len(rows),
            f"allowlist {filename} stores all {len(rows)} students",
        )
    return str(payload["batch_id"])


def attach_registered_class(
    ctx: Context, section_key: str, batch_id: str, class_name: str
) -> dict[str, object]:
    payload = must_json(
        http(
            "POST",
            f"/api/v1/sections/{ctx.require('section', section_key)}/classes",
            token=ctx.teacher_token,
            body={"class_name": class_name, "batch_id": batch_id},
        ),
        (200, 201),
        f"teacher attaches a registered class to section {section_key}",
    )
    assert isinstance(payload, dict), payload
    return payload


def upload_roster(
    ctx: Context,
    *,
    course_key: str,
    section_key: str,
    class_key: str,
    filename: str,
    rows: list[tuple[str, str]],
) -> dict[str, object]:
    body, content_type = multipart(
        {
            "course_id": ctx.require("course", course_key),
            "section_id": ctx.require("section", section_key),
            "class_id": ctx.require("class", class_key),
        },
        [("file", filename, csv_bytes(rows), "text/csv")],
    )
    payload = must_json(
        http(
            "POST",
            "/api/v1/roster-imports",
            token=ctx.teacher_token,
            data=body,
            content_type=content_type,
        ),
        (202,),
        f"teacher uploads roster {filename}",
    )
    assert isinstance(payload, dict), payload
    check(
        payload.get("status") == "previewed",
        f"roster {filename} parses into a preview (status={payload.get('status')})",
    )
    return payload


def confirm_roster(
    ctx: Context, import_id: str, *, expected: tuple[int, ...] = (202,), label: str
) -> Response:
    return must(
        http(
            "POST",
            f"/api/v1/roster-imports/{import_id}/confirm",
            token=ctx.teacher_token,
            body=None,
        ),
        expected,
        label,
    )


def import_and_confirm(
    ctx: Context,
    *,
    course_key: str,
    section_key: str,
    class_key: str,
    filename: str,
    rows: list[tuple[str, str]],
) -> None:
    preview = upload_roster(
        ctx,
        course_key=course_key,
        section_key=section_key,
        class_key=class_key,
        filename=filename,
        rows=rows,
    )
    confirm_roster(
        ctx,
        str(preview["roster_import_id"]),
        label=f"roster {filename} commits atomically",
    )


def register_student(ctx: Context, key: str) -> dict[str, object]:
    spec = ctx.spec(key)
    if not spec.email:
        raise AcceptanceFailure(f"{key} has no e-mail address and cannot register")
    must(
        http(
            "POST",
            "/api/v1/auth/registrations/code",
            body={
                "username": spec.username,
                "email": spec.email,
                "student_number": spec.student_number,
                "display_name": spec.display_name,
            },
        ),
        (202,),
        f"registration code mailed for {spec.username}",
    )
    if SINK is None:  # pragma: no cover - guarded by main()
        raise AcceptanceFailure("the SMTP sink is not running")
    code = SINK.wait_for_code(spec.email)
    payload = must_json(
        http(
            "POST",
            "/api/v1/auth/registrations",
            body={"email": spec.email, "code": code, "password": DEFAULT_PASSWORD},
        ),
        (201,),
        f"self-service registration completes for {spec.username}",
    )
    assert isinstance(payload, dict), payload
    return payload


def current_user(token: str) -> dict[str, object]:
    payload = must_json(http("GET", "/api/v1/me", token=token), (200,), "current user")
    assert isinstance(payload, dict), payload
    return payload


def course_of_membership(membership: dict[str, object]) -> str:
    return str(membership["course_id"])


def list_assignments(token: str) -> list[dict[str, object]]:
    return paginate("/api/v1/assignments", token, limit=50, label="assignment list")


def find_enrollment(
    ctx: Context, course_key: str, student_number: str
) -> dict[str, object] | None:
    rows = paginate(
        f"/api/v1/courses/{ctx.require('course', course_key)}/enrollments",
        ctx.teacher_token,
        limit=100,
        label="course roster",
    )
    for row in rows:
        if str(row.get("student_number")) == student_number:
            return row
    return None


def notification_document(
    ctx: Context, course_key: str, assignment_key: str
) -> dict[str, object]:
    path = (
        f"/api/v1/courses/{ctx.require('course', course_key)}"
        f"/assignments/{ctx.require('assignment', assignment_key)}/notifications"
    )
    page = must_json(
        http(
            "GET",
            path,
            token=ctx.require("token", "teacher"),
            query=[("limit", 3)],
        ),
        (200,),
        "teacher reads the notification summary",
    )
    assert isinstance(page, dict), page
    summary = page.get("summary")
    deliveries_page = page.get("deliveries")
    if not isinstance(summary, dict) or not isinstance(deliveries_page, dict):
        raise AcceptanceFailure("notification document lacks summary or deliveries")
    items = list(deliveries_page.get("items") or [])
    cursor = deliveries_page.get("next_cursor")
    while cursor:
        next_page = must_json(
            http(
                "GET",
                path,
                token=ctx.require("token", "teacher"),
                query=[("limit", 3), ("cursor", cursor)],
            ),
            (200,),
            "teacher reads the next notification delivery page",
        )
        assert isinstance(next_page, dict), next_page
        deliveries = next_page.get("deliveries") or {}
        items.extend(deliveries.get("items") or [])
        cursor = deliveries.get("next_cursor")
    return {"summary": summary, "deliveries": items}


def run_notification_sender() -> dict[str, object]:
    """Run the real single-pass notification sender and return its JSON counters."""
    environment = dict(os.environ)
    dsn = environment.get("B4_DATABASE_URL") or environment.get("DATABASE_URL")
    if not dsn:
        raise AcceptanceFailure("B4_DATABASE_URL is required to run the notification sender")
    environment["B4_DATABASE_URL"] = dsn
    environment["DATABASE_URL"] = dsn
    environment.setdefault("B4_MODULE_ROOT", str(REPO_ROOT))
    completed = subprocess.run(
        [sys.executable, "-m", "autograder_b4.ops.notifications"],
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (
            f"notification sender failed: rc={completed.returncode}\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )
        failures.append(detail)
        raise AcceptanceFailure(detail)
    for line in reversed(completed.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                return payload
    raise AcceptanceFailure(
        f"notification sender did not print a JSON counter line: {completed.stdout[-500:]!r}"
    )


def internal_authorization(user_id: str) -> dict[str, object]:
    payload = must_json(
        http(
            "GET",
            f"/api/v1/internal/users/{user_id}/authorization",
            token=SERVICE_TOKEN,
        ),
        (200,),
        "internal authorization of one account",
    )
    assert isinstance(payload, dict), payload
    return payload


# --------------------------------------------------------------------------- fixtures


def build_fixtures() -> Context:
    ctx = Context()
    ctx.specs = student_specs()
    ctx.admin_token = login(ADMIN_USERNAME)
    # Teachers are created through the admin API (there is no self-service teacher flow).
    teacher_spec = StudentSpec(
        "teacher", TEACHER_USERNAME, "验收032-教师", "2099032000000", "acc032.teacher@example.edu"
    )
    ctx.specs["teacher"] = teacher_spec
    payload = must_json(
        http(
            "POST",
            "/api/v1/users",
            token=ctx.admin_token,
            body={
                "display_name": teacher_spec.display_name,
                "username": teacher_spec.username,
                "email": teacher_spec.email,
                "student_number": teacher_spec.student_number,
                "roles": ["teacher"],
                "password": DEFAULT_PASSWORD,
            },
        ),
        (201,),
        "admin creates the teacher account",
    )
    assert isinstance(payload, dict), payload
    ctx.teacher_token = login(TEACHER_USERNAME)

    # --- course -> sections (课序) -> classes (班级), the v0.32.0 hierarchy -------
    ctx.courses["a"] = create_course(ctx, "a", COURSE_A_TITLE)
    ctx.courses["b"] = create_course(ctx, "b", COURSE_B_TITLE)
    ctx.courses["c"] = create_course(ctx, "c", COURSE_C_TITLE)
    ctx.sections["a1"] = create_section(ctx, "a1", "a", "1")
    ctx.sections["a2"] = create_section(ctx, "a2", "a", "2")
    ctx.sections["b1"] = create_section(ctx, "b1", "b", "1")
    ctx.sections["c1"] = create_section(ctx, "c1", "c", "1")
    ctx.sections["c2"] = create_section(ctx, "c2", "c", "2")
    ctx.classes["a1b"] = create_class(ctx, "a1b", "a1", "验收032-A1-2班")
    ctx.classes["a2a"] = create_class(ctx, "a2a", "a2", "验收032-A2-1班")
    ctx.classes["a2b"] = create_class(ctx, "a2b", "a2", "验收032-A2-2班")
    ctx.classes["b1a"] = create_class(ctx, "b1a", "b1", "验收032-B1-1班")
    ctx.classes["c1a"] = create_class(ctx, "c1a", "c1", "验收032-C1-1班")
    ctx.classes["c2a"] = create_class(ctx, "c2a", "c2", "验收032-C2-1班")
    ctx.classes["cmismatch"] = create_class(ctx, "cmismatch", "c1", "验收032-C1-姓名不一致班")

    # One course, two sections, two classes each; section A1 gets a second class from a
    # registered (allowlist) class, section A2 keeps its two manual classes.
    a1a_batch = upload_allowlist(
        ctx,
        "a1a",
        "acc032-a1a.csv",
        [
            (ctx.spec("reg").display_name, ctx.spec("reg").student_number),
            (ctx.spec("unbound").display_name, ctx.spec("unbound").student_number),
        ],
    )
    ctx.batches["a1a"] = a1a_batch
    attached = attach_registered_class(ctx, "a1", a1a_batch, "验收032-A1-注册班")
    ctx.classes["a1a"] = str(attached["class_id"])
    check(attached.get("source") == "registered_class", "attached class records its source")
    check(
        int(attached.get("enrollment_count", -1)) == 2,
        "attached class copies both registered students",
    )
    check(int(attached.get("bound_count", -1)) == 0, "attached class starts unbound")

    # Both enrolment orderings. First: the roster row exists before the account.
    registration = register_student(ctx, "reg")
    check(
        int(registration.get("bound_course_count", -1)) == 1,
        "先导入后注册：registration binds the waiting roster row of one course",
    )
    check(
        int(registration.get("identity_mismatch_count", -1)) == 0,
        "先导入后注册：matching display name binds cleanly",
    )
    ctx.users["reg"] = str(registration["user_id"])
    ctx.tokens["reg"] = login(ctx.spec("reg").username)
    reg_me = current_user(ctx.tokens["reg"])
    memberships = list(reg_me.get("memberships") or [])
    check(
        any(
            course_of_membership(item) == ctx.courses["a"]
            and str(item.get("section_id")) == ctx.sections["a1"]
            and str(item.get("class_id")) == ctx.classes["a1a"]
            for item in memberships
        ),
        "/me reports the registered student's course, section and class",
    )
    attached_classes = must_json(
        http(
            "GET",
            f"/api/v1/sections/{ctx.sections['a1']}/classes",
            token=ctx.teacher_token,
        ),
        (200,),
        "teacher re-reads section A1 classes after registration",
    )
    assert isinstance(attached_classes, dict), attached_classes
    registered_class = next(
        (
            item
            for item in attached_classes.get("items") or []
            if str(item.get("class_id")) == ctx.classes["a1a"]
        ),
        None,
    )
    check(
        registered_class is not None and int(registered_class.get("bound_count", -1)) == 1,
        "registration binds the waiting roster row (class bound_count becomes 1)",
    )

    # Second ordering: the account is created before any roster row mentions it.
    ctx.batches["pre"] = upload_allowlist(
        ctx,
        "pre",
        "acc032-pre.csv",
        [(ctx.spec("pre").display_name, ctx.spec("pre").student_number)],
    )
    pre_registration = register_student(ctx, "pre")
    check(
        int(pre_registration.get("bound_course_count", -1)) == 0,
        "先注册后导入：registration binds nothing when no roster row waits",
    )
    ctx.users["pre"] = str(pre_registration["user_id"])
    # This token is deliberately issued *before* the roster import; the follow-up checks prove
    # the assignment read path consults B4 instead of the token's scope snapshot.
    ctx.tokens["pre"] = login(ctx.spec("pre").username)

    # Ordinary accounts are created by the administrator (the second supported path).
    for key in ("multi", "no_contact", "inactive", "web", "mail", "cross", "conc"):
        ctx.users[key] = create_user(ctx, key)

    # Rosters: the class hierarchy above, one row per expected student.
    import_and_confirm(
        ctx,
        course_key="a",
        section_key="a1",
        class_key="a1b",
        filename="acc032-a1b.csv",
        rows=[
            (ctx.spec(key).display_name, ctx.spec(key).student_number)
            for key in ("multi", "no_contact", "inactive")
        ],
    )
    import_and_confirm(
        ctx,
        course_key="a",
        section_key="a2",
        class_key="a2a",
        filename="acc032-a2a.csv",
        rows=[(ctx.spec("web").display_name, ctx.spec("web").student_number)],
    )
    import_and_confirm(
        ctx,
        course_key="a",
        section_key="a2",
        class_key="a2b",
        filename="acc032-a2b.csv",
        rows=[(ctx.spec("mail").display_name, ctx.spec("mail").student_number)],
    )
    import_and_confirm(
        ctx,
        course_key="b",
        section_key="b1",
        class_key="b1a",
        filename="acc032-b1a.csv",
        rows=[
            (ctx.spec("multi").display_name, ctx.spec("multi").student_number),
            (ctx.spec("cross").display_name, ctx.spec("cross").student_number),
        ],
    )
    # The same student in a second course is legal (one active section *per course*).
    multi_a = find_enrollment(ctx, "a", ctx.spec("multi").student_number)
    multi_b = find_enrollment(ctx, "b", ctx.spec("multi").student_number)
    check(
        multi_a is not None and multi_b is not None,
        "the same student holds one active enrollment in each of two courses",
    )
    if multi_a is not None:
        check(
            str(multi_a.get("student_id")) == ctx.users["multi"],
            "a roster row imported after account creation binds the existing account",
        )
    ctx.tokens["multi"] = login(ctx.spec("multi").username)

    # A roster row whose display name differs from the (allowlisted) registration name must not
    # be bound by student number alone; the registration response reports it.
    mismatch_batch = upload_allowlist(
        ctx,
        "mismatch",
        "acc032-mismatch.csv",
        [(ctx.spec("mismatch").display_name, ctx.spec("mismatch").student_number)],
    )
    ctx.batches["mismatch"] = mismatch_batch
    import_and_confirm(
        ctx,
        course_key="c",
        section_key="c1",
        class_key="cmismatch",
        filename="acc032-cmismatch.csv",
        rows=[("验收032-名单错名学生", ctx.spec("mismatch").student_number)],
    )
    mismatch_registration = register_student(ctx, "mismatch")
    check(
        int(mismatch_registration.get("bound_course_count", -1)) == 0,
        "an identity-mismatched roster row is not bound",
    )
    check(
        int(mismatch_registration.get("identity_mismatch_count", -1)) == 1,
        "the registration response counts the identity-mismatched row",
    )
    ctx.users["mismatch"] = str(mismatch_registration["user_id"])
    ctx.tokens["mismatch"] = login(ctx.spec("mismatch").username)
    mismatch_row = find_enrollment(ctx, "c", ctx.spec("mismatch").student_number)
    check(
        mismatch_row is not None and mismatch_row.get("student_id") is None,
        "the mismatched roster row remains unbound (the roster value is not rewritten)",
    )
    check(
        mismatch_row is not None
        and str(mismatch_row.get("display_name")) == "验收032-名单错名学生",
        "the roster display name is preserved, not silently replaced",
    )

    # The inactive account is deactivated before publication so it is a "skipped_inactive"
    # recipient rather than a delivery failure.
    must(
        http("DELETE", f"/api/v1/users/{ctx.users['inactive']}", token=ctx.admin_token),
        (204,),
        "admin deactivates the inactive-recipient account",
    )

    # A teacher's token is a *scope snapshot*; refresh it after the courses exist so the staff
    # paths (course-scoped question/assignment writes) carry the grants.
    ctx.teacher_token = login(TEACHER_USERNAME)
    ctx.tokens["teacher"] = ctx.teacher_token
    for key in ("web", "mail", "cross"):
        ctx.tokens[key] = login(ctx.spec(key).username)

    # Two more allowlists used later: one for a conflicting class attach (a student who is
    # already enrolled in this course) and one for the concurrent idempotent attach.
    ctx.batches["conflict"] = upload_allowlist(
        ctx,
        "conflict",
        "acc032-conflict.csv",
        [(ctx.spec("web").display_name, ctx.spec("web").student_number)],
    )
    ctx.batches["idem"] = upload_allowlist(
        ctx,
        "idem",
        "acc032-idem.csv",
        [(ctx.spec("idem").display_name, ctx.spec("idem").student_number)],
    )
    return ctx


def create_question_and_assignment(ctx: Context) -> None:
    ctx.versions["q1"] = create_published_version(ctx, "a", "验收032-题一 alpha", "alpha")
    ctx.versions["q2"] = create_published_version(ctx, "a", "验收032-题二 beta", "beta")
    ctx.versions["qb"] = create_published_version(ctx, "b", "验收032-题B gamma", "gamma")

    payload = must_json(
        http(
            "POST",
            "/api/v1/assignments",
            token=ctx.teacher_token,
            body={
                "course_id": ctx.courses["a"],
                "title": TITLE_A,
                "question_version_ids": [ctx.versions["q1"], ctx.versions["q2"]],
            },
        ),
        (201,),
        "teacher publishes the course-level assignment",
    )
    assert isinstance(payload, dict), payload
    ctx.assignments["a"] = str(payload["assignment_id"])
    check(
        str(payload.get("course_id")) == ctx.courses["a"],
        "the assignment belongs to exactly one course",
    )
    check(
        [str(item) for item in payload.get("question_version_ids") or []]
        == [ctx.versions["q1"], ctx.versions["q2"]],
        "the assignment preserves question order",
    )
    check(
        payload.get("notification_state") == "scheduled",
        "publishing schedules the notification synchronously",
    )
    for absent in ("section_ids", "student_ids"):
        check(absent not in payload, f"the assignment no longer carries {absent}")

    # The internal endpoint is idempotent by assignment: a repeat returns the existing summary.
    internal = must_json(
        http(
            "POST",
            "/api/v1/internal/assignment-notifications",
            token=SERVICE_TOKEN,
            body={
                "assignment_id": ctx.assignments["a"],
                "course_id": ctx.courses["a"],
                "title": TITLE_A,
                "published_at": rfc3339_now(),
            },
        ),
        (200,),
        "B4 reports an existing notification for a repeated assignment id",
    )
    assert isinstance(internal, dict), internal
    check(
        int(internal.get("recipient_total", -1)) == 7,
        "notification audience is the whole course roster at publication time",
    )
    must(
        http(
            "POST",
            "/api/v1/internal/assignment-notifications",
            body={
                "assignment_id": ctx.assignments["a"],
                "course_id": ctx.courses["a"],
                "title": TITLE_A,
                "published_at": rfc3339_now(),
            },
        ),
        (401,),
        "the internal notification endpoint rejects an unauthenticated caller",
    )

    b_payload = must_json(
        http(
            "POST",
            "/api/v1/assignments",
            token=ctx.teacher_token,
            body={
                "course_id": ctx.courses["b"],
                "title": TITLE_B,
                "question_version_ids": [ctx.versions["qb"]],
            },
        ),
        (201,),
        "teacher publishes the second course's assignment",
    )
    assert isinstance(b_payload, dict), b_payload
    ctx.assignments["b"] = str(b_payload["assignment_id"])
    check(
        b_payload.get("notification_state") == "scheduled",
        "the second course's notification is scheduled too",
    )


def rfc3339_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def create_published_version(
    ctx: Context, course_key: str, title: str, expected_stdout: str
) -> str:
    created = must_json(
        http(
            "POST",
            "/api/v1/questions",
            token=ctx.teacher_token,
            query=[("course_id", ctx.require("course", course_key))],
            body={
                "title": title,
                "stem_markdown": f"## {title}\n\n请提交能够输出 `{expected_stdout}` 的 bash 脚本。",
                "language": "bash",
                "reference_answer": f"echo {expected_stdout}\n",
                "adapter": {
                    "kind": "stdin",
                    "args": [],
                    "stdin": None,
                    "expect_exit_code": 0,
                    "comparison": {"stdout": "exact", "stderr": "ignored"},
                },
                "cases": [
                    {
                        "name": "case-1",
                        "ordinal": 0,
                        "expected_stdout": f"{expected_stdout}\n",
                    }
                ],
            },
        ),
        (201,),
        f"teacher creates question {title}",
    )
    assert isinstance(created, dict), created
    version_id = str(created["versions"][0]["question_version_id"])
    version = must_json(
        http("GET", f"/api/v1/question-versions/{version_id}", token=ctx.teacher_token),
        (200,),
        f"teacher reads version of {title}",
    )
    assert isinstance(version, dict), version
    digest = version.get("content_digest")
    if not digest:
        rows = sql_rows(
            "SELECT content_digest FROM b3.question_versions WHERE question_version_id = %s",
            (version_id,),
        )
        if not rows:
            raise AcceptanceFailure(f"question version {version_id} is not in b3")
        digest = rows[0][0]
    must(
        http(
            "POST",
            f"/api/v1/question-versions/{version_id}/attestations",
            token=SERVICE_TOKEN,
            body={
                "content_digest": digest,
                "verifier": "acceptance-fixture",
                "cases_total": 1,
                "cases_passed": 1,
            },
        ),
        (201,),
        f"reference attestation recorded for {title}",
    )
    must(
        http("POST", f"/api/v1/question-versions/{version_id}/publish", token=ctx.teacher_token),
        (200, 201, 202, 204),
        f"question version published for {title}",
    )
    return version_id


# --------------------------------------------------------------------------- checks


def check_visibility(ctx: Context) -> None:
    assignment_a = ctx.require("assignment", "a")
    assignment_b = ctx.require("assignment", "b")
    for key, label in (
        ("reg", "section A1 / class A1a"),
        ("multi", "section A1 / class A1b"),
        ("web", "section A2 / class A2a"),
        ("mail", "section A2 / class A2b"),
    ):
        items = list_assignments(ctx.tokens[key])
        ids = {str(item["assignment_id"]) for item in items}
        check(assignment_a in ids, f"student in {label} sees the published assignment")
        detail = must(
            http("GET", f"/api/v1/assignments/{assignment_a}", token=ctx.tokens[key]),
            (200,),
            f"student in {label} reads the published assignment",
        )
        body = detail.json()
        assert isinstance(body, dict), body
        check(body.get("notification_state") == "scheduled", "the student DTO exposes notification state")

    cross_items = list_assignments(ctx.tokens["cross"])
    check(
        assignment_a not in {str(item["assignment_id"]) for item in cross_items},
        "a student of another course does not see the assignment in the list",
    )
    check(
        assignment_b in {str(item["assignment_id"]) for item in cross_items},
        "the other-course student does see their own course's assignment",
    )
    must(
        http("GET", f"/api/v1/assignments/{assignment_a}", token=ctx.tokens["cross"]),
        (403,),
        "reading the other course's assignment detail is refused with 403",
    )
    must(
        http(
            "POST",
            "/api/v1/email-verification/challenges",
            token=ctx.tokens["cross"],
            body={
                "course_id": ctx.courses["a"],
                "section_id": ctx.sections["a1"],
                "assignment_id": assignment_a,
            },
        ),
        (403,),
        "a student of another course cannot obtain a challenge for it (403)",
    )

    multi_items = list_assignments(ctx.tokens["multi"])
    multi_ids = {str(item["assignment_id"]) for item in multi_items}
    check(
        assignment_a in multi_ids and assignment_b in multi_ids,
        "the shared student sees both courses' assignments",
    )
    multi_me = current_user(ctx.tokens["multi"])
    member_courses = {course_of_membership(item) for item in multi_me.get("memberships") or []}
    check(
        member_courses == {ctx.courses["a"], ctx.courses["b"]},
        "/me lists exactly the two courses of the shared student",
    )


def check_post_publication_additions(ctx: Context) -> None:
    # Ordering 2 of the registration test: the account already exists and is enrolled after
    # publication; the roster import must bind it immediately and the assignment stays visible.
    import_and_confirm(
        ctx,
        course_key="a",
        section_key="a2",
        class_key="a2a",
        filename="acc032-a2a-late.csv",
        rows=[(ctx.spec("pre").display_name, ctx.spec("pre").student_number)],
    )
    pre_row = find_enrollment(ctx, "a", ctx.spec("pre").student_number)
    check(pre_row is not None, "先注册后导入：the late roster import writes the row")
    if pre_row is not None:
        check(
            str(pre_row.get("student_id")) == ctx.users["pre"],
            "先注册后导入：the imported row binds the existing account immediately",
        )
    pre_items = list_assignments(ctx.tokens["pre"])
    check(
        ctx.assignments["a"] in {str(item["assignment_id"]) for item in pre_items},
        "a student enrolled after publication sees the still-open assignment "
        "(token scope snapshot is not authoritative)",
    )
    # The same stale token must also be allowed to submit: student admission is identity +
    # B4's current membership, never the token's course/section scope. The claimed section is
    # deliberately the *other* section of the same course; the stored value must be B4's.
    late_submission = submit_web(
        ctx.tokens["pre"],
        envelope(
            assignment_id=ctx.assignments["a"],
            student_id=ctx.users["pre"],
            course_id=ctx.courses["a"],
            section_id=ctx.sections["a1"],
            question_version_id=ctx.require("version", "q1"),
            filename="1.txt",
            content="echo alpha from a late enrollment\n",
        ),
    )
    late_rows = [
        row for row in grade_rows(ctx, "a", "a") if str(row["submission_id"]) == late_submission
    ]
    check(len(late_rows) == 1, "the stale-token submission is stored and graded under the assignment")
    if late_rows:
        check(
            str(late_rows[0]["section_id"]) == ctx.sections["a2"],
            "the late submission stores B4's authoritative section, not the claimed one",
        )
        check(
            str(late_rows[0]["student_id"]) == ctx.users["pre"],
            "the late submission belongs to the student whose token was issued earlier",
        )

    # A whole section (课序) and class created after publication: its new student sees it too.
    ctx.sections["a3"] = create_section(ctx, "a3", "a", "3")
    ctx.classes["a3a"] = create_class(ctx, "a3a", "a3", "验收032-A3-1班")
    ctx.users["new"] = create_user(ctx, "new")
    import_and_confirm(
        ctx,
        course_key="a",
        section_key="a3",
        class_key="a3a",
        filename="acc032-a3a.csv",
        rows=[(ctx.spec("new").display_name, ctx.spec("new").student_number)],
    )
    ctx.tokens["new"] = login(ctx.spec("new").username)
    new_items = list_assignments(ctx.tokens["new"])
    check(
        ctx.assignments["a"] in {str(item["assignment_id"]) for item in new_items},
        "a student in a section created after publication sees the assignment",
    )
    must(
        http("GET", f"/api/v1/assignments/{ctx.assignments['a']}", token=ctx.tokens["new"]),
        (200,),
        "the new section's student reads the assignment detail",
    )

    # Publication audience is a snapshot: late roster additions do not retroactively receive
    # the notification, and the summary must not grow.
    document = notification_document(ctx, "a", "a")
    summary = document["summary"]
    assert isinstance(summary, dict), summary
    check(
        int(summary.get("recipient_total", -1)) == 7,
        "the notification audience stays the publication-time roster (7 recipients)",
    )


def check_roster_pagination(ctx: Context) -> None:
    course_a = ctx.require("course", "a")
    rows = paginate(
        f"/api/v1/courses/{course_a}/enrollments",
        ctx.teacher_token,
        limit=2,
        label="course-wide roster",
    )
    expected_numbers = {
        ctx.spec(key).student_number
        for key in ("reg", "unbound", "multi", "no_contact", "inactive", "web", "mail", "pre", "new")
    }
    numbers = [str(row.get("student_number")) for row in rows]
    check(len(rows) >= 5, f"the course roster spans several pages (limit 2, {len(rows)} rows)")
    check(len(set(numbers)) == len(numbers), "pagination returns every row exactly once")
    check(set(numbers) == expected_numbers, "pagination reaches every student, including new ones")
    check(
        len({str(row.get("enrollment_id")) for row in rows}) == len(rows),
        "pagination does not repeat an enrollment",
    )
    unbound = [row for row in rows if row.get("student_id") is None]
    check(len(unbound) == 1, "the roster contains exactly one unregistered student")
    check(
        unbound and str(unbound[0]["student_number"]) == ctx.spec("unbound").student_number,
        "the unregistered roster row is the expected student, with student_id null",
    )
    check(
        all(str(row.get("class_name") or "") for row in rows),
        "every roster row carries its class name",
    )

    summary = must_json(
        http("GET", f"/api/v1/courses/{course_a}/roster-summary", token=ctx.teacher_token),
        (200,),
        "teacher reads the server-computed roster summary",
    )
    assert isinstance(summary, dict), summary
    check(int(summary["student_total"]) == len(rows), "roster-summary student_total matches the pages")
    check(int(summary["bound_count"]) == len(rows) - 1, "roster-summary bound_count is correct")
    check(int(summary["unbound_count"]) == 1, "roster-summary unbound_count is correct")
    check(int(summary["section_count"]) == 3, "roster-summary counts three sections")
    check(int(summary["class_count"]) == 5, "roster-summary counts five classes")
    sections = {str(item["section_id"]): item for item in summary.get("sections") or []}
    check(set(sections) == {ctx.sections["a1"], ctx.sections["a2"], ctx.sections["a3"]},
          "roster-summary lists every section of the course")
    a1_classes = {str(item["class_name"]) for item in sections[ctx.sections["a1"]]["classes"]}
    check(len(a1_classes) == 2, "the first section reports its two classes")

    filtered = paginate(
        f"/api/v1/courses/{course_a}/enrollments",
        ctx.teacher_token,
        query=[("section_id", ctx.sections["a3"])],
        limit=50,
        label="section-filtered roster",
    )
    check(len(filtered) == 1, "the section filter narrows the course roster")


def check_notification_materialisation(ctx: Context) -> None:
    document = notification_document(ctx, "a", "a")
    summary = document["summary"]
    deliveries = document["deliveries"]
    assert isinstance(summary, dict), summary
    check(int(summary["recipient_total"]) == 7, "seven active roster rows are recipients")
    check(int(summary["pending_count"]) == 4, "four reachable students are pending")
    check(int(summary["sent_count"]) == 0, "nothing is reported as delivered before sending")
    check(int(summary["failed_count"]) == 0, "nothing failed before the first sending pass")
    check(int(summary["skipped_unbound_count"]) == 1, "one unregistered student is skipped")
    check(int(summary["skipped_no_contact_count"]) == 1, "one student without e-mail is skipped")
    check(int(summary["skipped_inactive_count"]) == 1, "one deactivated student is skipped")
    check(summary["status"] == "pending", "the campaign starts pending")
    check(len(deliveries) == 7, "one delivery row exists per intended recipient")
    statuses = {str(row["status"]) for row in deliveries}
    check(
        statuses <= {"pending", "skipped_unbound", "skipped_no_contact", "skipped_inactive"},
        f"materialisation only produces pending/skipped rows ({sorted(statuses)})",
    )
    reasons = {
        str(row["student_number"]): str(row.get("reason_code"))
        for row in deliveries
        if row.get("reason_code")
    }
    check(reasons.get(ctx.spec("unbound").student_number) == "no_account",
          "the unregistered row's reason code is no_account")
    check(reasons.get(ctx.spec("no_contact").student_number) == "no_email",
          "the no-e-mail row's reason code is no_email")
    check(reasons.get(ctx.spec("inactive").student_number) == "inactive_account",
          "the deactivated row's reason code is inactive_account")
    check(
        all(int(row.get("attempts", 0)) == 0 for row in deliveries if row["status"] != "pending"),
        "skipped rows never record a sending attempt",
    )
    must(
        http(
            "GET",
            f"/api/v1/courses/{ctx.courses['a']}/assignments/{uuid.uuid4()}/notifications",
            token=ctx.teacher_token,
        ),
        (404,),
        "an assignment without a notification answers 404",
    )

    document_b = notification_document(ctx, "b", "b")
    summary_b = document_b["summary"]
    assert isinstance(summary_b, dict), summary_b
    check(int(summary_b["recipient_total"]) == 2, "the second course has its own audience")
    check(int(summary_b["pending_count"]) == 2, "the second course's recipients are pending")


def delivery_by_number(document: dict[str, object]) -> dict[str, dict[str, object]]:
    deliveries = document["deliveries"]
    assert isinstance(deliveries, list), deliveries
    return {str(row["student_number"]): row for row in deliveries}


def expected_roster_email(ctx: Context, key: str) -> str:
    spec = ctx.spec(key)
    if not spec.email:
        raise AcceptanceFailure(f"{key} has no e-mail address")
    return spec.email


def check_notification_delivery(ctx: Context) -> None:
    if SINK is None:  # pragma: no cover - guarded by main()
        raise AcceptanceFailure("the SMTP sink is not running")
    assignment_a = ctx.require("assignment", "a")

    # A real SMTP refusal for one address: this is a genuine delivery failure, distinct from
    # the three "not deliverable by design" skipped reasons.
    refused_address = expected_roster_email(ctx, "web")
    SINK.reject_recipients = {refused_address}
    first_pass = run_notification_sender()
    check(int(first_pass.get("claimed", -1)) == 6,
          "first sending pass claims both courses' six pending messages")
    check(int(first_pass.get("sent", -1)) == 5, "first sending pass delivers five messages")
    check(int(first_pass.get("failed", -1)) == 1, "first sending pass fails exactly one message")

    a_messages = SINK.messages_for_title(TITLE_A)
    b_messages = SINK.messages_for_title(TITLE_B)
    check(len(a_messages) == 3, "SMTP accepted three messages for the course A assignment")
    check(len(b_messages) == 2, "SMTP accepted two messages for the course B assignment")
    check(refused_address in SINK.rejected_recipients(), "the refused address really got a 550")

    document = notification_document(ctx, "a", "a")
    summary = document["summary"]
    assert isinstance(summary, dict), summary
    check(int(summary["sent_count"]) == 3, "sent_count matches the three accepted messages")
    check(int(summary["failed_count"]) == 1, "failed_count records the refused delivery")
    check(int(summary["pending_count"]) == 0, "no pending delivery remains after the pass")
    check(summary["status"] == "partial",
          "a campaign with sent, failed and skipped rows is partial")
    deliveries = delivery_by_number(document)
    failed = deliveries.get(ctx.spec("web").student_number)
    check(
        failed is not None and failed.get("status") == "failed"
        and failed.get("reason_code") == "smtp_error"
        and int(failed.get("attempts", 0)) >= 1
        and failed.get("sent_at") is None,
        "the refused delivery is failed/smtp_error with an attempt and no sent_at",
    )
    sent_rows = [row for row in deliveries.values() if row["status"] == "sent"]
    sent_addresses = {
        expected_roster_email(ctx, key)
        for key in ("reg", "multi", "mail")
    }
    check(
        {str(row["student_number"]) for row in sent_rows}
        == {
            ctx.spec(key).student_number
            for key in ("reg", "multi", "mail")
        },
        "sent deliveries are exactly the accepted students",
    )
    check(
        {message.recipients[0] for message in a_messages if message.recipients} == sent_addresses,
        "the sink's accepted recipients match the sent deliveries",
    )
    skipped = {
        str(row["student_number"]): str(row["status"])
        for row in deliveries.values()
        if str(row["status"]).startswith("skipped_")
    }
    check(
        skipped
        == {
            ctx.spec("unbound").student_number: "skipped_unbound",
            ctx.spec("no_contact").student_number: "skipped_no_contact",
            ctx.spec("inactive").student_number: "skipped_inactive",
        },
        "the three skipped reasons remain distinguishable after sending",
    )
    check(
        all(int(row.get("attempts", 0)) == 0 for row in deliveries.values()
            if str(row["status"]).startswith("skipped_")),
        "skipped deliveries are never counted as attempts",
    )

    document_b = notification_document(ctx, "b", "b")
    summary_b = document_b["summary"]
    assert isinstance(summary_b, dict), summary_b
    check(summary_b["status"] == "completed", "the second course's campaign completes")
    check(int(summary_b["sent_count"]) == 2, "both second-course recipients were delivered")
    check(
        {message.recipients[0] for message in b_messages if message.recipients}
        == {expected_roster_email(ctx, "multi"), expected_roster_email(ctx, "cross")},
        "the second course's messages went to exactly its two students",
    )

    # A failed delivery can be re-armed; the retry must not touch skipped rows.
    retry_summary = must_json(
        http(
            "POST",
            f"/api/v1/courses/{ctx.courses['a']}/assignments/{assignment_a}/notifications/retry",
            token=ctx.teacher_token,
        ),
        (200,),
        "teacher re-arms the failed delivery",
    )
    assert isinstance(retry_summary, dict), retry_summary
    check(int(retry_summary["failed_count"]) == 0, "retry clears the failed count")
    check(int(retry_summary["pending_count"]) == 1, "retry arms exactly one pending delivery")
    check(int(retry_summary["skipped_unbound_count"]) == 1,
          "retry leaves skipped deliveries untouched")
    rearmed = delivery_by_number(notification_document(ctx, "a", "a"))
    failed = rearmed[ctx.spec("web").student_number]
    check(
        failed.get("status") == "pending" and failed.get("reason_code") is None,
        "the retried delivery is pending again with its reason cleared",
    )

    SINK.reject_recipients = set()
    second_pass = run_notification_sender()
    check(int(second_pass.get("sent", -1)) == 1, "second pass delivers the retried message")
    check(int(second_pass.get("failed", -1)) == 0, "second pass fails nothing")
    document = notification_document(ctx, "a", "a")
    summary = document["summary"]
    assert isinstance(summary, dict), summary
    check(int(summary["sent_count"]) == 4, "all reachable students are now sent")
    check(int(summary["failed_count"]) == 0, "no delivery remains failed")
    check(summary["status"] == "partial",
          "the campaign stays partial because three students were skipped")
    check(len(SINK.messages_for_title(TITLE_A)) == 4,
          "the sink received four accepted messages for the assignment")

    # A repeat pass is a no-op: no duplicate recipients, no duplicate mail.
    snapshot_document = notification_document(ctx, "a", "a")
    before = {
        str(row["delivery_id"]): (
            row["status"],
            int(row.get("attempts", 0)),
            row.get("sent_at"),
        )
        for row in snapshot_document["deliveries"]
    }
    third_pass = run_notification_sender()
    check(int(third_pass.get("claimed", -1)) == 0, "a repeat sending pass claims nothing")
    check(int(third_pass.get("sent", -1)) == 0, "a repeat sending pass sends nothing")
    after_document = notification_document(ctx, "a", "a")
    after = {
        str(row["delivery_id"]): (
            row["status"],
            int(row.get("attempts", 0)),
            row.get("sent_at"),
        )
        for row in after_document["deliveries"]
    }
    check(before == after, "a repeat pass changes no delivery row")
    check(len(after) == 7, "the notification still has exactly seven delivery rows")
    check(len(SINK.messages_for_title(TITLE_A)) == 4,
          "the sink receives no duplicate message on a repeat pass")

    # Both re-materialisation entry points are idempotent too.
    repeated = must_json(
        http(
            "POST",
            "/api/v1/internal/assignment-notifications",
            token=SERVICE_TOKEN,
            body={
                "assignment_id": assignment_a,
                "course_id": ctx.courses["a"],
                "title": TITLE_A,
                "published_at": rfc3339_now(),
            },
        ),
        (200,),
        "the internal endpoint returns the existing notification on a repeat",
    )
    assert isinstance(repeated, dict), repeated
    check(int(repeated["recipient_total"]) == 7, "the repeated materialisation keeps seven recipients")
    rescheduled = must_json(
        http(
            "POST",
            f"/api/v1/assignments/{assignment_a}/notifications",
            token=ctx.teacher_token,
        ),
        (200,),
        "B3 re-schedules the notification idempotently",
    )
    assert isinstance(rescheduled, dict), rescheduled
    check(rescheduled.get("notification_state") == "scheduled",
          "re-scheduling reports the scheduled state")
    check(len(delivery_by_number(notification_document(ctx, "a", "a"))) == 7,
          "re-scheduling creates no additional delivery rows")


# --------------------------------------------------------------------------- submissions


def envelope(
    *,
    assignment_id: str,
    student_id: str,
    course_id: str,
    section_id: str,
    question_version_id: str,
    filename: str,
    content: str,
) -> dict[str, object]:
    encoded = content.encode("utf-8")
    return {
        "submission_id": str(uuid.uuid4()),
        "assignment_id": assignment_id,
        "student_id": student_id,
        "course_id": course_id,
        "section_id": section_id,
        "question_version_id": question_version_id,
        "idempotency_key": str(uuid.uuid4()),
        "files": [
            {
                "path": filename,
                "content_sha256": hashlib.sha256(encoded).hexdigest(),
                "content_base64": base64.b64encode(encoded).decode("ascii"),
            }
        ],
    }


def submit_web(token: str, body: dict[str, object]) -> str:
    payload = must_json(
        http("POST", "/api/v1/submissions", token=token, body=body),
        (202,),
        "web submission accepted",
    )
    assert isinstance(payload, dict), payload
    return str(payload["submission_id"])


def submission_view(token: str, submission_id: str) -> dict[str, object]:
    payload = must_json(
        http("GET", f"/api/v1/submissions/{submission_id}", token=token),
        (200,),
        "submission view read",
    )
    assert isinstance(payload, dict), payload
    return payload


def grade_rows(ctx: Context, course_key: str, assignment_key: str) -> list[dict[str, object]]:
    return paginate(
        "/api/v1/grades",
        ctx.teacher_token,
        query=[
            ("course_id", ctx.require("course", course_key)),
            ("assignment_id", ctx.require("assignment", assignment_key)),
        ],
        limit=100,
        label="assignment grades",
    )


def check_web_and_mail_submissions(ctx: Context) -> None:
    assignment_a = ctx.require("assignment", "a")
    q1 = ctx.require("version", "q1")
    # The client claims section A1 although B4 says this student's authoritative section is A2.
    claimed_section = ctx.sections["a1"]
    web_body = envelope(
        assignment_id=assignment_a,
        student_id=ctx.users["web"],
        course_id=ctx.courses["a"],
        section_id=claimed_section,
        question_version_id=q1,
        filename="1.txt",
        content="echo alpha\n",
    )
    web_submission = submit_web(ctx.tokens["web"], web_body)
    view = submission_view(ctx.tokens["web"], web_submission)
    check(str(view.get("assignment_id")) == assignment_a, "web submission carries the assignment")
    check(str(view.get("intake")) == "api", "web submission records intake=api")
    check(
        str(view.get("section_id")) == ctx.sections["a2"],
        "web submission stores B4's authoritative section, not the claimed one",
    )
    web_rows = [
        row for row in grade_rows(ctx, "a", "a") if str(row["submission_id"]) == web_submission
    ]
    check(len(web_rows) == 1, "the web submission appears in the assignment grade query")
    if web_rows:
        row = web_rows[0]
        check(str(row["assignment_id"]) == assignment_a, "the grade row carries the assignment")
        check(str(row["course_id"]) == ctx.courses["a"], "the grade row carries the course")
        check(str(row["section_id"]) == ctx.sections["a2"],
              "the grade row carries the authoritative section")
        check(str(row["student_id"]) == ctx.users["web"], "the grade row carries the student")

    # A question from another assignment/course is refused on the same path.
    must(
        http(
            "POST",
            "/api/v1/submissions",
            token=ctx.tokens["web"],
            body=envelope(
                assignment_id=assignment_a,
                student_id=ctx.users["web"],
                course_id=ctx.courses["a"],
                section_id=ctx.sections["a2"],
                question_version_id=ctx.versions["qb"],
                filename="1.txt",
                content="echo gamma\n",
            ),
        ),
        (422,),
        "a question outside the assignment is refused with 422",
    )

    challenge = must_json(
        http(
            "POST",
            "/api/v1/email-verification/challenges",
            token=ctx.tokens["mail"],
            body={
                "course_id": ctx.courses["a"],
                "section_id": claimed_section,
                "assignment_id": assignment_a,
            },
        ),
        (201,),
        "the mail student obtains a challenge bound to the assignment",
    )
    assert isinstance(challenge, dict), challenge
    check(str(challenge["assignment_id"]) == assignment_a, "the challenge is assignment-scoped")
    message = build_mail_message(
        ctx,
        sender=expected_roster_email(ctx, "mail"),
        display_name=ctx.spec("mail").display_name,
        student_number=ctx.spec("mail").student_number,
        code=str(challenge["code"]),
        assignment_id=assignment_a,
        answers={1: "echo alpha via mail\n"},
    )
    sys.path.insert(0, str(REPO_ROOT / "B2" / "tests"))
    from imap_fixture import FixtureMessage, ImapFixtureServer  # noqa: PLC0415 - real fixture

    imap_username = "acceptance-inbox"
    imap_password = "acceptance-imap-pass"
    imap = ImapFixtureServer(
        messages=[FixtureMessage(uid=1, raw=message)],
        username=imap_username,
        password=imap_password,
    ).start()
    try:
        run_pull_cli(imap.port, imap_username, imap_password)
        status = wait_for_challenge(ctx.tokens["mail"], str(challenge["challenge_id"]))
        check(status.get("state") == "received",
              f"the real mail is received (state={status.get('state')})")
        mapped = list(status.get("submissions") or [])
        check(len(mapped) == 1, "one attachment maps to one per-question submission")
        if mapped:
            check(str(mapped[0]["question_version_id"]) == q1,
                  "the attachment maps to the assignment's first question")
        mail_submission = str(mapped[0]["submission_id"]) if mapped else ""
        if mail_submission:
            mail_view = submission_view(ctx.tokens["mail"], mail_submission)
            check(str(mail_view.get("assignment_id")) == assignment_a,
                  "the mail submission carries the same assignment")
            check(str(mail_view.get("intake")) == "email", "the mail submission records intake=email")
            check(str(mail_view.get("section_id")) == ctx.sections["a2"],
                  "the mail submission stores the authoritative section")
            mail_rows = [row for row in grade_rows(ctx, "a", "a")
                         if str(row["submission_id"]) == mail_submission]
            check(len(mail_rows) == 1, "the mail submission appears in the same grade query")
            if mail_rows:
                check(str(mail_rows[0]["assignment_id"]) == assignment_a,
                      "the mail grade row carries the assignment")
                check(str(mail_rows[0]["section_id"]) == ctx.sections["a2"],
                      "the mail grade row carries the authoritative section")

            own_file = must(
                http(
                    "GET",
                    f"/api/v1/submissions/{mail_submission}/files",
                    token=ctx.tokens["mail"],
                    query=[("path", "1.txt")],
                ),
                (200,),
                "the student reads their own answer file",
            )
            check(b"via mail" in own_file.body, "the answer file content is the mailed answer")
            must(
                http(
                    "GET",
                    f"/api/v1/submissions/{mail_submission}/files",
                    token=ctx.tokens["web"],
                    query=[("path", "1.txt")],
                ),
                (403,),
                "another student cannot read the answer file",
            )
            teacher_file = must(
                http(
                    "GET",
                    f"/api/v1/submissions/{mail_submission}/files",
                    token=ctx.teacher_token,
                    query=[("path", "1.txt")],
                ),
                (200,),
                "the teacher reads the authorized answer file",
            )
            check(b"via mail" in teacher_file.body, "the teacher sees the mailed answer")
    finally:
        imap.stop()

    # Web and mail intake agree on refusal for a student of another course.
    must(
        http(
            "POST",
            "/api/v1/submissions",
            token=ctx.tokens["cross"],
            body=envelope(
                assignment_id=assignment_a,
                student_id=ctx.users["cross"],
                course_id=ctx.courses["a"],
                section_id=claimed_section,
                question_version_id=q1,
                filename="1.txt",
                content="echo nope\n",
            ),
        ),
        (403,),
        "web submission by another course's student is refused with 403",
    )


def build_mail_message(
    ctx: Context,
    *,
    sender: str,
    display_name: str,
    student_number: str,
    code: str,
    assignment_id: str,
    answers: dict[int, str],
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ACCEPTANCE_INBOX
    message["Subject"] = f"{display_name} {student_number} 作业提交"
    message["Message-ID"] = f"<{uuid.uuid4()}@example.edu>"
    body = "\n".join(
        [
            f"核验码：{code}",
            f"作业编号：{assignment_id}",
            "课序：2",
            f"姓名：{display_name}",
            f"学号：{student_number}",
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


def wait_for_challenge(token: str, challenge_id: str, timeout: float = 60.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    status: dict[str, object] = {}
    while time.monotonic() < deadline:
        payload = must_json(
            http("GET", f"/api/v1/email-verification/challenges/{challenge_id}", token=token),
            (200,),
            "challenge status read",
        )
        assert isinstance(payload, dict), payload
        status = payload
        if status.get("state") != "pending":
            return status
        time.sleep(0.5)
    return status


def run_pull_cli(port: int, username: str, password: str) -> None:
    environment = dict(os.environ)
    environment.update(
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
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise AcceptanceFailure(
            "mail pull failed: rc="
            f"{completed.returncode}\nstdout={completed.stdout[-2000:]}\n"
            f"stderr={completed.stderr[-2000:]}"
        )


# --------------------------------------------------------------------------- authority


def check_removal_revokes_authority(ctx: Context) -> None:
    assignment_a = ctx.require("assignment", "a")
    assignment_b = ctx.require("assignment", "b")
    multi_enrollment = find_enrollment(ctx, "a", ctx.spec("multi").student_number)
    if multi_enrollment is None:
        raise AcceptanceFailure("the shared student has no enrollment in course A to remove")
    if "multi" not in ctx.tokens:
        raise AcceptanceFailure("the shared student has no token to test revocation with")
    old_token = ctx.tokens["multi"]
    must(
        http(
            "DELETE",
            f"/api/v1/enrollments/{multi_enrollment['enrollment_id']}",
            token=ctx.teacher_token,
        ),
        (204,),
        "teacher removes the student from the course",
    )

    me = current_user(old_token)
    check(
        {course_of_membership(item) for item in me.get("memberships") or []}
        == {ctx.courses["b"]},
        "/me immediately drops the removed course",
    )
    items = list_assignments(old_token)
    ids = {str(item["assignment_id"]) for item in items}
    check(assignment_a not in ids, "the removed student no longer lists the assignment")
    check(assignment_b in ids, "the removed student still sees the course they remain in")
    must(
        http("GET", f"/api/v1/assignments/{assignment_a}", token=old_token),
        (403,),
        "reading the removed course's assignment is refused with 403",
    )
    must(
        http(
            "POST",
            "/api/v1/submissions",
            token=old_token,
            body=envelope(
                assignment_id=assignment_a,
                student_id=ctx.users["multi"],
                course_id=ctx.courses["a"],
                section_id=ctx.sections["a1"],
                question_version_id=ctx.require("version", "q1"),
                filename="1.txt",
                content="echo removed\n",
            ),
        ),
        (403,),
        "web submission after removal is refused with 403",
    )
    must(
        http(
            "POST",
            "/api/v1/email-verification/challenges",
            token=old_token,
            body={
                "course_id": ctx.courses["a"],
                "section_id": ctx.sections["a1"],
                "assignment_id": assignment_a,
            },
        ),
        (403,),
        "mail challenge issuance after removal is refused with 403",
    )
    must(
        http(
            "GET",
            "/api/v1/grades",
            token=old_token,
            query=[("course_id", ctx.courses["a"]), ("limit", 10)],
        ),
        (403,),
        "grades of the removed course are refused with 403",
    )
    must(
        http(
            "GET",
            "/api/v1/grades",
            token=old_token,
            query=[("course_id", ctx.courses["b"]), ("limit", 10)],
        ),
        (200,),
        "grades of the course the student remains in are still readable",
    )

    authorization = internal_authorization(ctx.users["multi"])
    enrolled_courses = {
        str(item["course_id"]) for item in authorization.get("enrollments") or []
    }
    check(
        enrolled_courses == {ctx.courses["b"]},
        "the B4 authorization endpoint reports the removal immediately",
    )
    check(
        set(authorization) == {"user_id", "platform_roles", "course_grants", "enrollments"},
        "the internal authorization response contains identifiers only",
    )
    for item in authorization.get("enrollments") or []:
        check(
            set(item) == {"course_id", "section_id", "class_id"},
            "an internal enrollment exposes only ids",
        )
        break

    # Historical delivery rows survive roster changes: the notification remains evidence.
    document = notification_document(ctx, "a", "a")
    check(len(document["deliveries"]) == 7,
          "removing a student does not delete their historical delivery row")
    check(
        delivery_by_number(document)[ctx.spec("multi").student_number]["status"] == "sent",
        "the removed student's historical delivery still reports sent",
    )

    summary = must_json(
        http(
            "GET",
            f"/api/v1/courses/{ctx.courses['a']}/roster-summary",
            token=ctx.teacher_token,
        ),
        (200,),
        "roster summary after removal",
    )
    assert isinstance(summary, dict), summary
    check(int(summary["student_total"]) == 8, "the course roster loses the removed student")


# --------------------------------------------------------------------------- conflict


def check_conflicts_and_concurrency(ctx: Context) -> None:
    assignment_a = ctx.require("assignment", "a")

    # (1) The class attach path refuses a student who is already active in this course.
    before_classes = must_json(
        http(
            "GET",
            f"/api/v1/sections/{ctx.sections['a2']}/classes",
            token=ctx.teacher_token,
        ),
        (200,),
        "list classes of section A2 before the conflicting attach",
    )
    assert isinstance(before_classes, dict), before_classes
    before_ids = {str(item["class_id"]) for item in before_classes.get("items") or []}
    check(len(before_ids) == 2, "section A2 starts with its two manual classes")
    must(
        http(
            "POST",
            f"/api/v1/sections/{ctx.sections['a2']}/classes",
            token=ctx.teacher_token,
            body={"class_name": "验收032-抢课序冲突班", "batch_id": ctx.batches["conflict"]},
        ),
        (409,),
        "attaching a class whose student is already enrolled in this course is 409",
    )
    after_classes = must_json(
        http(
            "GET",
            f"/api/v1/sections/{ctx.sections['a2']}/classes",
            token=ctx.teacher_token,
        ),
        (200,),
        "list classes of section A2 after the refused attach",
    )
    assert isinstance(after_classes, dict), after_classes
    after_ids = {str(item["class_id"]) for item in after_classes.get("items") or []}
    check(
        after_ids == before_ids,
        "a refused attach writes no class (the request rolled back)",
    )
    web_enrollment = find_enrollment(ctx, "a", ctx.spec("web").student_number)
    check(
        web_enrollment is not None
        and str(web_enrollment.get("section_id")) == ctx.sections["a2"],
        "the conflicting student's existing enrollment is unchanged",
    )

    # (2) The roster confirm path rejects only the conflicting row and keeps the rest.
    conflicting = upload_roster(
        ctx,
        course_key="a",
        section_key="a2",
        class_key="a2b",
        filename="acc032-conflict-row.csv",
        rows=[(ctx.spec("reg").display_name, ctx.spec("reg").student_number)],
    )
    response = http(
        "POST",
        f"/api/v1/roster-imports/{conflicting['roster_import_id']}/confirm",
        token=ctx.teacher_token,
    )
    check(
        response.status in (202, 422),
        f"confirming an all-conflicting roster is refused with a clear status (got {response.status})",
    )
    if response.status not in (202, 422):
        failures.append(f"unexpected roster conflict status {response.status}")
    preview = must_json(
        http(
            "GET",
            f"/api/v1/roster-imports/{conflicting['roster_import_id']}",
            token=ctx.teacher_token,
        ),
        (200,),
        "the conflicting roster import keeps its per-row reason",
    )
    assert isinstance(preview, dict), preview
    rejected = list(preview.get("rejected_rows") or [])
    reasons = " ".join(str(row.get("reason") or "") for row in rejected)
    check("conflict_other_section" in reasons,
          "the rejected roster row reports conflict_other_section")
    check(preview.get("status") != "committed",
          "the conflicting roster import did not commit the duplicate")
    reg_enrollment = find_enrollment(ctx, "a", ctx.spec("reg").student_number)
    check(
        reg_enrollment is not None
        and str(reg_enrollment.get("section_id")) == ctx.sections["a1"],
        "the conflicting student still holds exactly the original section",
    )

    # (3) The database itself is the final authority for "one active section per course".
    index_rows = sql_rows(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'b4'"
        " AND tablename = 'enrollments'"
    )
    index_defs = " ".join(str(row[0]) for row in index_rows)
    check(
        "course_id" in index_defs
        and "student_number" in index_defs
        and "WHERE" in index_defs.upper()
        and "is_active" in index_defs,
        "the partial unique index on (course_id, student_number) WHERE is_active exists",
    )
    enrollments = sql_rows(
        "SELECT course_id, student_number, count(*) FROM b4.enrollments"
        " WHERE is_active GROUP BY course_id, student_number HAVING count(*) > 1"
    )
    check(not enrollments, "the database contains no duplicate active course/student pair")

    import psycopg

    target = sql_rows(
        "SELECT enrollment_id, course_id, section_id, class_id, user_id, student_number,"
        " display_name, source FROM b4.enrollments"
        " WHERE course_id = %s AND student_number = %s AND is_active LIMIT 1",
        (ctx.courses["a"], ctx.spec("web").student_number),
    )
    if not target:
        raise AcceptanceFailure("the target enrollment for the duplicate-insert probe is missing")
    row = target[0]
    duplicate_refused = False
    with psycopg.connect(platform_dsn()) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO b4.enrollments (enrollment_id, course_id, section_id,"
                    " class_id, user_id, student_number, display_name, source, is_active)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true)",
                    (
                        uuid.uuid4(),
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                    ),
                )
        except psycopg.errors.UniqueViolation:
            duplicate_refused = True
    check(duplicate_refused,
          "a direct INSERT of a second active course/student row is refused by the database")
    still_one = sql_rows(
        "SELECT count(*) FROM b4.enrollments WHERE course_id = %s AND student_number = %s"
        " AND is_active",
        (ctx.courses["a"], ctx.spec("web").student_number),
    )
    check(int(still_one[0][0]) == 1,
          "the refused duplicate insert leaves exactly one active row (no silent repair)")

    # (4) Concurrent roster imports for the same student in two sections of one course. The
    # roster writer upserts on the course-level partial unique index, so a race either loses
    # the row to the row-level conflict check (202/409/422, never a 500) or updates the one
    # existing row; what must never happen is two active sections for the same student.
    first = upload_roster(
        ctx,
        course_key="c",
        section_key="c1",
        class_key="c1a",
        filename="acc032-conc-1.csv",
        rows=[(ctx.spec("conc").display_name, ctx.spec("conc").student_number)],
    )
    second = upload_roster(
        ctx,
        course_key="c",
        section_key="c2",
        class_key="c2a",
        filename="acc032-conc-2.csv",
        rows=[(ctx.spec("conc").display_name, ctx.spec("conc").student_number)],
    )
    statuses = concurrent_post(
        [
            f"/api/v1/roster-imports/{first['roster_import_id']}/confirm",
            f"/api/v1/roster-imports/{second['roster_import_id']}/confirm",
        ],
        ctx.teacher_token,
    )
    check(all(status in (202, 409, 422) for status in statuses),
          f"concurrent imports produce only clear outcomes ({statuses})")
    check(all(status < 500 for status in statuses),
          "concurrent imports never produce a 5xx")
    check(202 in statuses, "at least one concurrent import commits its roster row")
    active_conc = sql_rows(
        "SELECT count(*) FROM b4.enrollments WHERE course_id = %s AND student_number = %s"
        " AND is_active",
        (ctx.courses["c"], ctx.spec("conc").student_number),
    )
    check(int(active_conc[0][0]) == 1,
          "the concurrent race leaves exactly one active section for the student")
    conc_row = sql_rows(
        "SELECT class_id FROM b4.enrollments WHERE course_id = %s AND student_number = %s"
        " AND is_active",
        (ctx.courses["c"], ctx.spec("conc").student_number),
    )
    check(
        bool(conc_row)
        and str(conc_row[0][0]) in {ctx.classes["c1a"], ctx.classes["c2a"]},
        "the surviving enrollment sits in exactly one of the two competing classes",
    )

    # (5) Concurrent identical class attach is idempotent: one class, no duplicated roster and
    # no 5xx (the class/batch unique indexes are the arbiter; an IntegrityError must be a clear
    # answer, matching the endpoint's documented 409 translation).
    attach_statuses = concurrent_post(
        [
            f"/api/v1/sections/{ctx.sections['c1']}/classes",
            f"/api/v1/sections/{ctx.sections['c1']}/classes",
            f"/api/v1/sections/{ctx.sections['c1']}/classes",
            f"/api/v1/sections/{ctx.sections['c1']}/classes",
        ],
        ctx.teacher_token,
        body={
            "class_name": "验收032-并发幂等班",
            "batch_id": ctx.batches["idem"],
        },
    )
    check(all(status in (200, 201, 409) for status in attach_statuses),
          f"concurrent identical attach answers clearly ({attach_statuses})")
    check(all(status < 500 for status in attach_statuses),
          "concurrent identical attach never produces a 5xx")
    classes = must_json(
        http(
            "GET",
            f"/api/v1/sections/{ctx.sections['c1']}/classes",
            token=ctx.teacher_token,
        ),
        (200,),
        "list classes after the concurrent attach",
    )
    assert isinstance(classes, dict), classes
    matching = [
        item
        for item in classes.get("items") or []
        if str(item.get("allowlist_batch_id")) == ctx.batches["idem"]
    ]
    check(len(matching) == 1, "the concurrent attach created exactly one class for the batch")
    if matching:
        check(int(matching[0]["enrollment_count"]) == 1,
              "the idempotent class holds exactly one roster row")
    idem_rows = sql_rows(
        "SELECT count(*) FROM b4.enrollments WHERE course_id = %s AND student_number = %s"
        " AND is_active",
        (ctx.courses["c"], ctx.spec("idem").student_number),
    )
    check(int(idem_rows[0][0]) == 1,
          "the idempotent attach did not duplicate the student's active row")

    final_duplicates = sql_rows(
        "SELECT course_id, student_number FROM b4.enrollments WHERE is_active"
        " GROUP BY course_id, student_number HAVING count(*) > 1"
    )
    check(not final_duplicates,
          "after every conflict/concurrency probe the course/student invariant still holds")

    # The assignment created before all this is still readable by the teacher.
    must(
        http("GET", f"/api/v1/assignments/{assignment_a}", token=ctx.teacher_token),
        (200,),
        "the teacher still reads the assignment after the conflict probes",
    )


def concurrent_post(
    paths: list[str], token: str, *, body: object | None = None
) -> list[int]:
    barrier = threading.Barrier(len(paths))
    statuses: list[int] = [0] * len(paths)
    errors: list[str] = []

    def worker(index: int, path: str) -> None:
        try:
            barrier.wait(timeout=15)
            statuses[index] = http("POST", path, token=token, body=body).status
        except Exception as error:  # pragma: no cover - surfaced below
            errors.append(f"{path}: {error}")

    threads = [
        threading.Thread(target=worker, args=(index, path), daemon=True)
        for index, path in enumerate(paths)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    if errors:
        raise AcceptanceFailure("concurrent request failed at the transport level: " + "; ".join(errors))
    return statuses


# --------------------------------------------------------------------------- main


def validate_environment() -> int:
    """Check every environment fact this script needs, then return the sink port."""
    missing = [
        name
        for name in (
            "PLATFORM_DATABASE_URL",
            "B4_DATABASE_URL",
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASS",
            "SMTP_FROM",
        )
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        print(f"missing required environment variables: {', '.join(missing)}")
        return -1
    host = os.environ["SMTP_HOST"].strip()
    if host not in {"127.0.0.1", "localhost"}:
        print(f"SMTP_HOST must point at the local acceptance sink, got {host!r}")
        return -1
    tls_mode = os.environ.get("SMTP_TLS_MODE", "").strip().lower()
    if tls_mode not in {"none", "null", ""}:
        print(f"SMTP_TLS_MODE must disable TLS for the plaintext sink, got {tls_mode!r}")
        return -1
    raw_port = os.environ.get("ACCEPTANCE_SMTP_PORT", "").strip() or os.environ["SMTP_PORT"].strip()
    if not raw_port.isdigit():
        print("ACCEPTANCE_SMTP_PORT / SMTP_PORT must be a TCP port number")
        return -1
    port = int(raw_port)
    configured = os.environ["SMTP_PORT"].strip()
    if configured != str(port):
        print(
            "SMTP_PORT and ACCEPTANCE_SMTP_PORT must agree so the host and the sink share one "
            f"port (host={configured}, sink={port})"
        )
        return -1
    return port


def main() -> int:
    global SINK
    print("== course-level assignment workflow acceptance (composed host, real DB/SMTP/IMAP) ==")
    port = validate_environment()
    if port < 0:
        print("ACCEPTANCE FAILED (environment is not the acceptance topology)")
        return 1
    sink = SmtpSink(port).start()
    SINK = sink
    try:
        try:
            admin_id = seed_admin()
            print(f"  ok   SQL-seeded administrator {admin_id} (the only seeded principal)")
            ctx = build_fixtures()
            print("\n-- teacher publishes course-scoped assignments --")
            create_question_and_assignment(ctx)
            print("\n-- audience and visibility --")
            check_visibility(ctx)
            print("\n-- post-publication additions --")
            check_post_publication_additions(ctx)
            print("\n-- teacher roster: completeness and counts --")
            check_roster_pagination(ctx)
            print("\n-- notification materialisation --")
            check_notification_materialisation(ctx)
            print("\n-- notification delivery over real SMTP --")
            check_notification_delivery(ctx)
            print("\n-- web and mail submissions --")
            check_web_and_mail_submissions(ctx)
            print("\n-- removal revokes access immediately (B4 is authoritative) --")
            check_removal_revokes_authority(ctx)
            print("\n-- conflicts, legacy-data probe and concurrency --")
            check_conflicts_and_concurrency(ctx)
        except AcceptanceFailure as error:
            print(f"\nacceptance aborted: {error}")
    finally:
        sink.stop()
        SINK = None
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
