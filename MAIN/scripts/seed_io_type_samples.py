"""Idempotent online seeder for P6b sample question bank (2 courses x 3 io_types x 2 = 12 questions).

Each grid cell (PYTHON/UNIX x stdin/file/callable) gets 2 questions (1 easy, 1 medium),
tagged with `sample`, published after real B2 reference-answer verification, plus one
sample assignment containing 1 question per cell (6 questions total).

Usage:
    python MAIN/scripts/seed_io_type_samples.py --base-url https://api.91idc.me
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.91idc.me"
DEFAULT_ADMIN_USER = os.getenv("AUTOGRADER_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASS = os.getenv("AUTOGRADER_ADMIN_PASSWORD", "yojAyGbCfeMMkOErnB4gRi7Pcf4Oe5XO")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pad_hidden_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure exactly 1 public + 10 hidden cases for B3 per_case publish gate."""
    public = [c for c in cases if c.get("visible_to_student")]
    hidden = [c for c in cases if not c.get("visible_to_student")]
    if len(public) != 1:
        raise ValueError(f"expected 1 public case, got {len(public)}")
    if not hidden:
        raise ValueError("expected at least 1 hidden case")
    idx = 0
    while len(hidden) < 10:
        template = dict(hidden[idx % len(hidden)])
        template["name"] = f"hidden-extra-{len(hidden) + 1}"
        hidden.append(template)
        idx += 1
    combined = [public[0], *hidden[:10]]
    for ordinal, item in enumerate(combined):
        item["ordinal"] = ordinal
    return combined


def _build_samples() -> list[dict[str, Any]]:
    py_in_sample = b"10\n20\n30\n"
    py_in_hidden = b"5\n15\n25\n35\n"
    py_case_a = b"2\n4\n6\n"
    py_case_b = b"1\n3\n5\n7\n"

    unix_in_sample = b"INFO ok\nERROR fail1\nWARN slow\nERROR fail2\n"
    unix_in_hidden = b"ERROR a\nINFO b\nERROR c\nERROR d\n"

    return [
        # --- PYTHON x stdin (2) ---
        {
            "course_code": "08",
            "title": "[样例-Python-命令行] 两数之和",
            "language": "python",
            "io_type": "stdin",
            "tags": ["sample", "python", "stdin"],
            "stem_markdown": "从标准输入读取一行两个整数 `a b`，输出它们的和 `a + b`。\n\n- IO 规范：命令行 / 标准输入（`stdin`）\n- 入口文件：`main.py`",
            "reference_answer": "a, b = map(int, input().split())\nprint(a + b)\n",
            "adapter": {"kind": "stdin", "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}},
            "cases": _pad_hidden_cases([
                {"name": "样例-1", "visible_to_student": True, "stdin": "3 5\n", "expected_stdout": "8\n", "expected_exit_code": 0},
                {"name": "隐藏-负数", "visible_to_student": False, "stdin": "-4 9\n", "expected_stdout": "5\n", "expected_exit_code": 0},
                {"name": "隐藏-零", "visible_to_student": False, "stdin": "0 0\n", "expected_stdout": "0\n", "expected_exit_code": 0},
                {"name": "隐藏-大数", "visible_to_student": False, "stdin": "1000 2345\n", "expected_stdout": "3345\n", "expected_exit_code": 0},
            ]),
        },
        {
            "course_code": "08",
            "title": "[样例-Python-命令行] 命令行参数统计词频",
            "language": "python",
            "io_type": "stdin",
            "tags": ["sample", "python", "stdin"],
            "stem_markdown": "通过命令行参数接收目标词 `sys.argv[1]`，统计标准输入中该词（按空白分词）出现的次数并输出。\n\n- IO 规范：命令行参数 + 标准输入（`stdin`）",
            "reference_answer": "import sys\ntarget = sys.argv[1]\ntext = sys.stdin.read().split()\nprint(sum(1 for w in text if w == target))\n",
            "adapter": {"kind": "stdin", "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}},
            "cases": _pad_hidden_cases([
                {"name": "样例-1", "visible_to_student": True, "args": ["ok"], "stdin": "ok test ok\n", "expected_stdout": "2\n", "expected_exit_code": 0},
                {"name": "隐藏-零次", "visible_to_student": False, "args": ["none"], "stdin": "a b c\n", "expected_stdout": "0\n", "expected_exit_code": 0},
                {"name": "隐藏-多行", "visible_to_student": False, "args": ["py"], "stdin": "py\npy py\n", "expected_stdout": "3\n", "expected_exit_code": 0},
            ]),
        },
        # --- PYTHON x file (2) ---
        {
            "course_code": "08",
            "title": "[样例-Python-文件] 读取数字文件求和",
            "language": "python",
            "io_type": "file",
            "tags": ["sample", "python", "file"],
            "stem_markdown": "读取工作区相对路径 `numbers.txt`（每行一个整数），计算总和并写入 `sum.txt`。\n\n- IO 规范：文件读取（`file`）\n- 可下载样例附件：`sample_numbers.txt`",
            "reference_answer": "with open('numbers.txt', 'r', encoding='utf-8') as f:\n    total = sum(int(line.strip()) for line in f if line.strip())\nwith open('sum.txt', 'w', encoding='utf-8') as out:\n    out.write(f'{total}\\n')\n",
            "adapter": {
                "kind": "file",
                "comparison": {"stdout": "ignored", "stderr": "ignored"},
            },
            "fixtures": [
                {"path": "sample_numbers.txt", "mount": "input", "student_visible": True, "content": py_in_sample},
                {"path": "numbers.txt", "mount": "input", "student_visible": False, "content": py_in_hidden},
                {"path": "sum.txt", "mount": "expected_output", "student_visible": False, "content": b"80\n"},
            ],
            "cases": _pad_hidden_cases([
                {"name": "样例-1", "visible_to_student": True, "expected_exit_code": 0},
                {"name": "隐藏-文件求和", "visible_to_student": False, "expected_exit_code": 0},
            ]),
        },
        {
            "course_code": "08",
            "title": "[样例-Python-文件] 用例级多文件平均值",
            "language": "python",
            "io_type": "file",
            "tags": ["sample", "python", "file"],
            "stem_markdown": "读取工作区相对路径 `data.txt` 中的整数列表，输出平均值（保留整数部分）。不同用例会挂载不同的 `data.txt`。\n\n- 可下载样例附件：`sample_data.txt`",
            "reference_answer": "with open('data.txt', 'r', encoding='utf-8') as f:\n    nums = [int(x) for x in f.read().split()]\nprint(sum(nums) // len(nums))\n",
            "adapter": {
                "kind": "file",
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
            },
            "fixtures": [
                {"path": "sample_data.txt", "mount": "input", "student_visible": True, "content": py_case_a},
                {"path": "data.txt", "mount": "input", "student_visible": False, "content": py_case_b},
            ],
            "cases": _pad_hidden_cases([
                {"name": "样例-1", "visible_to_student": True, "expected_stdout": "4\n", "expected_exit_code": 0},
                {"name": "隐藏-均值", "visible_to_student": False, "expected_stdout": "4\n", "expected_exit_code": 0},
            ]),
        },
        # --- PYTHON x callable (2) ---
        {
            "course_code": "08",
            "title": "[样例-Python-接口] 实现阶乘函数",
            "language": "python",
            "io_type": "callable",
            "tags": ["sample", "python", "callable"],
            "stem_markdown": "在 `main.py` 中实现函数 `def factorial(n: int) -> int`，返回 `n!`（`n >= 0`）。\n\n- IO 规范：接口调用（`callable`）",
            "reference_answer": "def factorial(n: int) -> int:\n    res = 1\n    for i in range(2, n + 1):\n        res *= i\n    return res\n",
            "adapter": {
                "kind": "callable",
                "callable": {"module": "main", "attribute": "factorial", "arguments_json": "[5]"},
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
            },
            "cases": _pad_hidden_cases([
                {"name": "样例-5", "visible_to_student": True, "args": ["5"], "expected_stdout": "120\n", "expected_exit_code": 0},
                {"name": "隐藏-5", "visible_to_student": False, "args": ["5"], "expected_stdout": "120\n", "expected_exit_code": 0},
            ]),
        },
        {
            "course_code": "08",
            "title": "[样例-Python-接口] 字符串回文判断",
            "language": "python",
            "io_type": "callable",
            "tags": ["sample", "python", "callable"],
            "stem_markdown": "在 `main.py` 中实现函数 `def is_palindrome(s: str) -> bool`，忽略大小写判断是否为回文。",
            "reference_answer": "def is_palindrome(s: str) -> bool:\n    t = s.lower()\n    return t == t[::-1]\n",
            "adapter": {
                "kind": "callable",
                "callable": {"module": "main", "attribute": "is_palindrome", "arguments_json": '["Level"]'},
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
            },
            "cases": _pad_hidden_cases([
                {"name": "样例-Level", "visible_to_student": True, "expected_stdout": "true\n", "expected_exit_code": 0},
                {"name": "隐藏-Level", "visible_to_student": False, "expected_stdout": "true\n", "expected_exit_code": 0},
            ]),
        },
        # --- UNIX x stdin (2) ---
        {
            "course_code": "07",
            "title": "[样例-Unix-命令行] 统计标准输入行数",
            "language": "bash",
            "io_type": "stdin",
            "tags": ["sample", "bash", "stdin"],
            "stem_markdown": "编写 `main.sh` 从标准输入读取文本，输出总行数（仅输出数字）。",
            "reference_answer": "#!/usr/bin/env bash\nwc -l | tr -d ' '\n",
            "adapter": {"kind": "stdin", "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}},
            "cases": _pad_hidden_cases([
                {"name": "样例-2行", "visible_to_student": True, "stdin": "a\nb\n", "expected_stdout": "2\n", "expected_exit_code": 0},
                {"name": "隐藏-3行", "visible_to_student": False, "stdin": "1\n2\n3\n", "expected_stdout": "3\n", "expected_exit_code": 0},
                {"name": "隐藏-1行", "visible_to_student": False, "stdin": "hello\n", "expected_stdout": "1\n", "expected_exit_code": 0},
            ]),
        },
        {
            "course_code": "07",
            "title": "[样例-Unix-命令行] 按参数过滤文本行",
            "language": "bash",
            "io_type": "stdin",
            "tags": ["sample", "bash", "stdin"],
            "stem_markdown": "编写 `main.sh`，接收命令行参数 `$1` 作为关键字，从标准输入过滤包含该关键字的行并输出。",
            "reference_answer": "#!/usr/bin/env bash\ngrep -F \"$1\" || true\n",
            "adapter": {"kind": "stdin", "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}},
            "cases": _pad_hidden_cases([
                {"name": "样例-ERROR", "visible_to_student": True, "args": ["ERROR"], "stdin": "INFO ok\nERROR bad\n", "expected_stdout": "ERROR bad\n", "expected_exit_code": 0},
                {"name": "隐藏-WARN", "visible_to_student": False, "args": ["WARN"], "stdin": "WARN x\nINFO y\n", "expected_stdout": "WARN x\n", "expected_exit_code": 0},
            ]),
        },
        # --- UNIX x file (2) ---
        {
            "course_code": "07",
            "title": "[样例-Unix-文件] 提取日志错误行到文件",
            "language": "bash",
            "io_type": "file",
            "tags": ["sample", "bash", "file"],
            "stem_markdown": "编写 `main.sh`，读取当前目录 `app.log`，将所有包含 `ERROR` 的行写入 `errors.txt`。\n\n- 可下载样例附件：`sample_app.log`",
            "reference_answer": "#!/usr/bin/env bash\ngrep '^ERROR' app.log > errors.txt\n",
            "adapter": {"kind": "file", "comparison": {"stdout": "ignored", "stderr": "ignored"}},
            "fixtures": [
                {"path": "sample_app.log", "mount": "input", "student_visible": True, "content": unix_in_sample},
                {"path": "app.log", "mount": "input", "student_visible": False, "content": unix_in_hidden},
                {"path": "errors.txt", "mount": "expected_output", "student_visible": False, "content": b"ERROR a\nERROR c\nERROR d\n"},
            ],
            "cases": _pad_hidden_cases([
                {"name": "样例-1", "visible_to_student": True, "expected_exit_code": 0},
                {"name": "隐藏-提取ERROR", "visible_to_student": False, "expected_exit_code": 0},
            ]),
        },
        {
            "course_code": "07",
            "title": "[样例-Unix-文件] 统计 CSV 记录数",
            "language": "bash",
            "io_type": "file",
            "tags": ["sample", "bash", "file"],
            "stem_markdown": "编写 `main.sh`，读取 `records.log` 并输出总行数到 stdout。\n\n- 可下载样例附件：`sample_records.log`",
            "reference_answer": "#!/usr/bin/env bash\nwc -l < records.log | tr -d ' '\n",
            "adapter": {"kind": "file", "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"}},
            "fixtures": [
                {"path": "sample_records.log", "mount": "input", "student_visible": True, "content": unix_in_sample},
                {"path": "records.log", "mount": "input", "student_visible": False, "content": unix_in_hidden},
            ],
            "cases": _pad_hidden_cases([
                {"name": "样例-1", "visible_to_student": True, "expected_stdout": "4\n", "expected_exit_code": 0},
                {"name": "隐藏-行数", "visible_to_student": False, "expected_stdout": "4\n", "expected_exit_code": 0},
            ]),
        },
        # --- UNIX x callable (2) ---
        {
            "course_code": "07",
            "title": "[样例-Unix-接口] 实现问候函数 greet",
            "language": "bash",
            "io_type": "callable",
            "tags": ["sample", "bash", "callable"],
            "stem_markdown": "在 `main.sh` 中定义 Shell 函数 `greet()`，接收 `$1` 并输出 `Hello, <$1>!`。",
            "reference_answer": "greet() {\n  echo \"Hello, $1!\"\n}\n",
            "adapter": {
                "kind": "callable",
                "callable": {"module": "main", "attribute": "greet", "arguments_json": '["AutoGrader"]'},
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
            },
            "cases": _pad_hidden_cases([
                {"name": "样例-AutoGrader", "visible_to_student": True, "expected_stdout": "Hello, AutoGrader!\n", "expected_exit_code": 0},
                {"name": "隐藏-AutoGrader", "visible_to_student": False, "expected_stdout": "Hello, AutoGrader!\n", "expected_exit_code": 0},
            ]),
        },
        {
            "course_code": "07",
            "title": "[样例-Unix-接口] 实现整数加法函数 add_nums",
            "language": "bash",
            "io_type": "callable",
            "tags": ["sample", "bash", "callable"],
            "stem_markdown": "在 `main.sh` 中定义 Shell 函数 `add_nums()`，接收两个整数 `$1` 和 `$2`，输出它们的和。",
            "reference_answer": "add_nums() {\n  echo $(( $1 + $2 ))\n}\n",
            "adapter": {
                "kind": "callable",
                "callable": {"module": "main", "attribute": "add_nums", "arguments_json": '["7", "15"]'},
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored"},
            },
            "cases": _pad_hidden_cases([
                {"name": "样例-7+15", "visible_to_student": True, "expected_stdout": "22\n", "expected_exit_code": 0},
                {"name": "隐藏-7+15", "visible_to_student": False, "expected_stdout": "22\n", "expected_exit_code": 0},
            ]),
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_ADMIN_USER)
    parser.add_argument("--password", default=DEFAULT_ADMIN_PASS)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60.0) as client:
        auth = client.post(
            "/api/v1/auth/session",
            json={"username": args.username, "password": args.password},
        )
        auth.raise_for_status()
        headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

        courses = client.get("/api/v1/courses?limit=100", headers=headers).json()["items"]
        course_by_code = {c["course_code"]: c["course_id"] for c in courses}

        # Existing questions index by (course_id, title) for idempotency
        existing: dict[tuple[str, str], dict[str, Any]] = {}
        cursor = None
        while True:
            path = "/api/v1/questions?limit=100&published_only=false" + (
                f"&cursor={cursor}" if cursor else ""
            )
            page = client.get(path, headers=headers).json()
            for q in page.get("items", []):
                existing[(q["course_id"], q["title"])] = q
            cursor = page.get("next_cursor")
            if not cursor or not page.get("items"):
                break

        selected_for_assignment: dict[str, list[str]] = {"08": [], "07": []}
        seen_cells: set[tuple[str, str]] = set()

        for spec in _build_samples():
            cid = course_by_code[spec["course_code"]]
            key = (cid, spec["title"])
            if key in existing:
                q_obj = existing[key]
                vid = q_obj["versions"][0]["question_version_id"]
                print(f"[SKIP-EXISTS] {spec['title']} -> {vid}")
            else:
                adapter = dict(spec["adapter"])
                fixtures = spec.get("fixtures", [])
                if fixtures:
                    adapter["files"] = [
                        {
                            "path": f["path"],
                            "content_sha256": _sha256_hex(f["content"]),
                            "mount": f["mount"],
                        }
                        for f in fixtures
                    ]
                body = {
                    "title": spec["title"],
                    "stem_markdown": spec["stem_markdown"],
                    "language": spec["language"],
                    "io_type": spec["io_type"],
                    "reference_answer": spec["reference_answer"],
                    "adapter": adapter,
                    "cases": spec["cases"],
                    "tags": spec["tags"],
                }
                created = client.post(
                    f"/api/v1/questions?course_id={cid}",
                    headers=headers,
                    json=body,
                )
                created.raise_for_status()
                q_obj = created.json()
                vid = q_obj["versions"][0]["question_version_id"]
                for f in fixtures:
                    up = client.post(
                        f"/api/v1/question-versions/{vid}/fixtures",
                        headers=headers,
                        json={
                            "path": f["path"],
                            "mount": f["mount"],
                            "student_visible": f.get("student_visible", False),
                            "content_base64": base64.b64encode(f["content"]).decode("ascii"),
                        },
                    )
                    up.raise_for_status()
                print(f"[CREATED] {spec['title']} -> {vid}")

            # Verify via real B2 if draft
            v_detail = client.get(f"/api/v1/question-versions/{vid}", headers=headers).json()
            if v_detail["status"] == "draft":
                ver = client.post(
                    "/api/v1/reference-verifications",
                    headers=headers,
                    json={"question_version_id": vid},
                )
                ver.raise_for_status()
                v_res = ver.json()
                print(f"  [VERIFIED] {v_res['cases_passed']}/{v_res['cases_total']} passed")
                if v_res["cases_passed"] != v_res["cases_total"]:
                    print(f"  [WARN] verification incomplete for {spec['title']}: {v_res}")
                pub = client.post(f"/api/v1/question-versions/{vid}/publish", headers=headers)
                pub.raise_for_status()
                print(f"  [PUBLISHED] {vid}")

            cell = (spec["course_code"], spec["io_type"])
            if cell not in seen_cells:
                seen_cells.add(cell)
                selected_for_assignment[spec["course_code"]].append(vid)

        # Create idempotent sample assignments (1 per course covering all 3 io_types)
        existing_assignments = client.get("/api/v1/assignments?limit=100", headers=headers).json().get("items", [])
        existing_titles = {(a["course_id"], a["title"]) for a in existing_assignments}
        for code, vids in selected_for_assignment.items():
            cid = course_by_code[code]
            title = f"[样例作业] IO 三类型验收（课程 {code}）"
            if (cid, title) in existing_titles:
                print(f"[SKIP-ASSIGNMENT] {title}")
                continue
            resp = client.post(
                "/api/v1/assignments",
                headers=headers,
                json={"course_id": cid, "title": title, "question_version_ids": vids},
            )
            resp.raise_for_status()
            print(f"[CREATED-ASSIGNMENT] {title} -> {resp.json()['assignment_id']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
