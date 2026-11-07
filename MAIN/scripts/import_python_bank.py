"""Import the Python question bank (``MAIN/seed/question-bank/python/*.json``).

Three groups, one file each:

* ``01-draft-with-image.json`` -- draft only, stem carries the placeholder image markup with
  ``{question_id}``/``{asset_id}`` tokens. This importer uploads a placeholder PNG for the
  question, substitutes the real ids, and PATCHes the stem. Drafts are never published.
* ``02-draft-plain.json``     -- draft only, no image.
* ``03-published.json``       -- verified against the platform, then published.

What "verified" means here is deliberately **the platform's own check**, not a local run:
``POST /api/v1/reference-verifications`` executes the reference answer inside the sandbox and
records the attestation publish requires. A question whose reference answer does not reproduce
its own expected output therefore fails at import time instead of reaching a student.

Differences from ``import_question_bank.py`` (which imports every file it sees and publishes all
of them):

* it honours each problem's ``publish`` flag;
* it keeps the tags the bank declares (the older script drops them);
* it can attach the stem image, because an image reference needs the ids that only exist after
  the question row is created.

Usage::

    python MAIN/scripts/import_python_bank.py \
        --course-id 5b6466c0-347d-4867-9ea0-47d39341d121 \
        --base-url https://api.91idc.me

Credentials come from ``BANK_TEACHER_USERNAME`` / ``BANK_TEACHER_PASSWORD``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import struct
import sys
import urllib.error
import urllib.request
import zlib

DEFAULT_BANK_DIR = pathlib.Path(__file__).resolve().parents[1] / "seed" / "question-bank" / "python"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: object) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def request(
    method: str,
    base: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict | None = None,
    tolerate: tuple[int, ...] = (),
) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            parsed: object = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body
        if error.code in tolerate:
            return error.code, parsed
        raise ApiError(error.code, parsed) from error


def login(base: str, username: str, password: str) -> str:
    status, document = request(
        "POST", base, "/api/v1/auth/session", payload={"username": username, "password": password}
    )
    token = (document or {}).get("access_token") if isinstance(document, dict) else None
    if status != 200 or not isinstance(token, str):
        raise ApiError(status, "login returned no access_token")
    return token


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def placeholder_png(width: int = 640, height: int = 200) -> bytes:
    """A flat grey rectangle with a darker border: a *placeholder*, not a real diagram.

    Drawn byte by byte instead of with an image library so the importer has no dependency that
    a deployment might not install.
    """
    rows = []
    for y in range(height):
        border = y < 2 or y >= height - 2
        row = bytearray([0])  # filter type 0
        for x in range(width):
            if border or x < 2 or x >= width - 2:
                row.extend((0x9A, 0x9A, 0x9A))
            else:
                row.extend((0xEC, 0xEC, 0xEC))
        rows.append(bytes(row))
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def draft_body(problem: dict) -> dict:
    body = {
        "title": problem["title"],
        "stem_markdown": problem["stem_markdown"],
        "language": problem["language"],
        "reference_answer": problem["reference_answer"],
        "adapter": problem["adapter"],
        "cases": problem["cases"],
        "max_files": problem.get("max_files", 20),
        "max_total_bytes": problem.get("max_total_bytes", 1048576),
        "tags": problem.get("tags", []),
    }
    # CHG-0031: the explicit scoring declaration. Absent means per_case (the legacy default),
    # so banks written before this field stay exactly what they were.
    if "execution" in problem:
        body["execution"] = problem["execution"]
    return body


def create_question(base: str, token: str, course_id: str, problem: dict) -> tuple[str, str]:
    status, created = request(
        "POST",
        base,
        f"/api/v1/questions?course_id={course_id}",
        token=token,
        payload=draft_body(problem),
    )
    if status != 201 or not isinstance(created, dict):
        raise ApiError(status, created)
    versions = created.get("versions") or []
    if not versions:
        raise ApiError(status, "question has no version")
    return str(created["question_id"]), str(versions[0]["question_version_id"])


def attach_placeholder_image(base: str, token: str, problem: dict, question_id: str, version_id: str) -> None:
    """Upload the placeholder PNG, then rewrite the stem with the real ids."""
    status, asset = request(
        "POST",
        base,
        f"/api/v1/questions/{question_id}/assets",
        token=token,
        payload={
            "content_type": "image/png",
            "content_base64": base64.b64encode(placeholder_png()).decode("ascii"),
            "alt_text": "题干配图占位",
        },
    )
    if status not in (200, 201) or not isinstance(asset, dict):
        raise ApiError(status, asset)
    asset_id = str(asset["asset_id"])
    stem = (
        problem["stem_markdown"]
        .replace("{question_id}", question_id)
        .replace("{asset_id}", asset_id)
    )
    body = draft_body(problem)
    body["stem_markdown"] = stem
    # 只改题干：`reference_answer` / `cases` 保持不变（CHG-0017 的省略语义），因此不重发它们。
    body.pop("reference_answer", None)
    body.pop("cases", None)
    status, patched = request(
        "PATCH", base, f"/api/v1/questions/{question_id}", token=token, payload=body
    )
    if status not in (200, 201):
        raise ApiError(status, patched)


def verify(base: str, token: str, version_id: str) -> tuple[int, int, bool]:
    status, document = request(
        "POST",
        base,
        "/api/v1/reference-verifications",
        token=token,
        payload={"question_version_id": version_id},
    )
    if status != 200 or not isinstance(document, dict):
        raise ApiError(status, document)
    return (
        int(document.get("cases_passed", 0)),
        int(document.get("cases_total", 0)),
        bool(document.get("attestation_id")),
    )


def publish(base: str, token: str, version_id: str) -> None:
    status, document = request(
        "POST", base, f"/api/v1/question-versions/{version_id}/publish", token=token
    )
    if status != 200:
        raise ApiError(status, document)


def import_file(
    base: str, token: str, course_id: str, path: pathlib.Path, dry_run: bool
) -> dict:
    problems = json.loads(path.read_text(encoding="utf-8"))
    print(f"== {path.name} ({len(problems)} problems)")
    rows: list[dict] = []
    failures: list[str] = []
    for problem in problems:
        title = problem["title"]
        if dry_run:
            print(f"  [dry-run] {title}")
            continue
        try:
            question_id, version_id = create_question(base, token, course_id, problem)
            if problem.get("image_placeholder"):
                attach_placeholder_image(base, token, problem, question_id, version_id)
            passed, total, attested = verify(base, token, version_id)
            if passed != total or not attested:
                raise ApiError(
                    422,
                    f"verification did not pass: {passed}/{total}, attestation={attested}",
                )
            published = False
            if problem.get("publish"):
                publish(base, token, version_id)
                published = True
            rows.append(
                {
                    "title": title,
                    "question_id": question_id,
                    "version_id": version_id,
                    "cases_passed": passed,
                    "cases_total": total,
                    "published": published,
                }
            )
            state = "已发布" if published else "草稿"
            print(f"  [ok]   {title}  {passed}/{total}  {state}")
        except ApiError as error:
            failures.append(f"{title}: {error}")
            print(f"  [FAIL] {title}: {error}")
    return {"rows": rows, "failures": failures}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.91idc.me")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--bank-dir", default=str(DEFAULT_BANK_DIR))
    parser.add_argument("--only", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv[1:])

    base = args.base_url.rstrip("/")
    bank_dir = pathlib.Path(args.bank_dir)
    files = sorted(bank_dir.glob("*.json"))
    if args.only:
        files = [path for path in files if path.stem == args.only]
    if not files:
        print(f"no bank files in {bank_dir}", file=sys.stderr)
        return 2

    token = login(
        base,
        os.getenv("BANK_TEACHER_USERNAME", "admin"),
        os.getenv("BANK_TEACHER_PASSWORD", ""),
    )

    rows: list[dict] = []
    failures: list[str] = []
    for path in files:
        result = import_file(base, token, args.course_id, path, args.dry_run)
        rows.extend(result["rows"])
        failures.extend(result["failures"])

    if not args.dry_run:
        published = sum(1 for row in rows if row["published"])
        print()
        print(f"imported {len(rows)} question(s): {published} published, {len(rows) - published} draft")
        for row in rows:
            print(
                f"  {row['title']}  {row['cases_passed']}/{row['cases_total']}"
                f"  {'published' if row['published'] else 'draft'}"
                f"  version={row['version_id']}"
            )
    if failures:
        print(f"{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
