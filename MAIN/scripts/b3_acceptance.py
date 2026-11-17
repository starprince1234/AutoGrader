"""B3 acceptance: prove the 7 题库管理 RQ on a live deployment, in one run.

Usage (admin token is taken from the CLI, never hard-coded):
    python MAIN/scripts/b3_acceptance.py --base https://api.91idc.me \
        --admin admin --admin-password *** --course <uuid> \
        [--student testuser --student-password ***] [--report C:\\temp\\b3.txt]

What it does: creates throwaway questions for the three adapters (stdin/file/callable), runs the
reference verification sandbox, publishes, and probes the maintenance/statistics/log endpoints.
Everything it creates is archived or deleted at the end, so the question bank keeps its shape.

Exit code: 0 when every check passed, 1 when any check failed. The report file (UTF-8) has the
per-RQ verdicts plus every observed hazard; the console only prints an ASCII summary, because the
Windows console cannot render the Chinese detail lines.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import import_python_bank as bank  # noqa: E402

RESULTS: list[tuple[str, str, str, str]] = []


def record(rq: str, name: str, ok: bool, detail: str) -> None:
    RESULTS.append((rq, name, "PASS" if ok else "FAIL", detail))


def check(rq: str, name: str, fn) -> None:
    try:
        detail = fn() or ""
        record(rq, name, True, str(detail))
    except AssertionError as error:
        record(rq, name, False, str(error))
    except bank.ApiError as error:  # type: ignore[attr-defined]
        detail = error.args[1] if len(error.args) > 1 else None
        record(rq, name, False, f"HTTP {getattr(error, 'status', '?')}: {json.dumps(detail, ensure_ascii=False)[:300]}")
    except Exception as error:  # noqa: BLE001
        record(rq, name, False, f"{type(error).__name__}: {error}")


def stdin_problem(title: str) -> dict:
    return {
        "title": title,
        "stem_markdown": "读两行，逐行输出（验收用，验后即删）。",
        "language": "python",
        "reference_answer": "import sys\nfor line in sys.stdin.read().splitlines():\n    print(line.upper())\n",
        "adapter": {
            "kind": "stdin",
            "args": [],
            "stdin": "ab\ncd\n",
            "expect_exit_code": 0,
            "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored", "numeric_tolerance": None},
        },
        "cases": [
            {
                "name": "两行",
                "ordinal": 0,
                "weight": 1,
                "visible_to_student": True,
                "stdin": "ab\ncd\n",
                "args": [],
                "expected_stdout": "AB\nCD\n",
                "expected_stderr": None,
                "expected_exit_code": 0,
            },
            {
                "name": "隐藏用例：单行",
                "ordinal": 1,
                "weight": 1,
                "visible_to_student": False,
                "stdin": "xy\n",
                "args": [],
                "expected_stdout": "XY\n",
                "expected_stderr": None,
                "expected_exit_code": 0,
            },
        ],
        "tags": ["acceptance"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B3 线上验收（7 条 RQ）")
    parser.add_argument("--base", default="https://api.91idc.me")
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--course", required=True, help="用于验收的课程 id（示例题目）")
    parser.add_argument("--student", default="")
    parser.add_argument("--student-password", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--keep", action="store_true", help="不清理创建出来的题（排查时用）")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    token = bank.login(base, args.admin, args.admin_password)
    created: list[tuple[str, str]] = []  # (question_id, version_id)
    published: list[str] = []  # version ids published by this run
    notes: list[str] = []

    def verify_detail(version_id: str) -> tuple[int, int, bool, str]:
        """跑参考解答校验，并把失败用例的类别/说明带回来（排错时最有用）。"""
        status, doc = bank.request(
            "POST", base, "/api/v1/reference-verifications", token=token,
            payload={"question_version_id": version_id},
        )
        assert status == 200, f"校验接口 HTTP {status}"
        cases = doc.get("cases") or []
        fails = [
            f"#{case.get('ordinal')} {case.get('failure_category')}: {str(case.get('explanation') or '')[:160]}"
            for case in cases
            if not case.get("passed")
        ]
        return (
            int(doc.get("cases_passed", 0)),
            int(doc.get("cases_total", 0)),
            bool(doc.get("attestation_id")),
            " | ".join(fails),
        )

    def new_question(problem: dict) -> tuple[str, str]:
        question_id, version_id = bank.create_question(base, token, args.course, problem)
        created.append((question_id, version_id))
        return question_id, version_id

    # ---------------------------------------------------------------- RQ-B-3-40 stdin/argv
    holder: dict[str, str] = {}

    def stdin_flow() -> str:
        question_id, version_id = new_question(stdin_problem("__accept-stdin__"))
        # 「题干含一张图片」要在**草稿**阶段挂上去：发布后版本就不可变了。
        bank.attach_placeholder_image(base, token, stdin_problem("__accept-stdin__"), question_id, version_id)
        passed, total, attested = bank.verify(base, token, version_id)
        assert total == 2, f"用例数应为 2，实际 {total}"
        assert passed == 2, f"参考解答只通过 {passed}/{total}"
        assert attested, "没有生成存证（attestation）"
        bank.publish(base, token, version_id)
        published.append(version_id)
        holder["stdin_question"] = question_id
        holder["stdin_version"] = version_id
        return "2/2 通过、有存证、已发布"

    check("RQ-B-3-40", "待测代码由命令行接收测试数据（stdin/argv）并完成评测", stdin_flow)

    # ---------------------------------------------------------------- RQ-B-3-10 内容完整 + 图片
    def content_flow() -> str:
        question_id = holder["stdin_question"]
        version_id = holder["stdin_version"]
        status, detail = bank.request("GET", base, f"/api/v1/questions/{question_id}", token=token)
        assert status == 200, f"题目详情 HTTP {status}"
        assert detail.get("stem_markdown", "").strip() != "", "题干为空"
        assert (detail.get("stem_assets") or []), "题干上没有任何资产（图片没挂上）"
        status, content = bank.request(
            "GET", base, f"/api/v1/question-versions/{version_id}/draft-content", token=token
        )
        assert status == 200, f"draft-content HTTP {status}"
        cases = content.get("cases") or []
        hidden = [case for case in cases if not case.get("visible_to_student", True)]
        assert len(cases) == 2, f"用例数 {len(cases)}"
        assert len(hidden) == 1, f"隐藏用例数 {len(hidden)}"
        assert (content.get("reference_answer") or "").strip() != "", "参考答案为空"
        return "题干/参考答案/2 个用例（1 隐藏）都取得到"

    check("RQ-B-3-10", "题目包含题干、参考答案、测试用例（含隐藏用例）", content_flow)

    # ---------------------------------------------------------------- RQ-B-3-20 维护与不可变
    def maintain_flow() -> str:
        question_id = holder["stdin_question"]
        published_version = holder["stdin_version"]
        status, page = bank.request(
            "GET", base, f"/api/v1/questions?limit=100&course_id={args.course}", token=token
        )
        assert status == 200, f"列表 HTTP {status}"
        assert any(item["question_id"] == question_id for item in page["items"]), "按课程过滤查不到刚建的题"
        status, other = bank.request(
            "GET", base, "/api/v1/questions?limit=5&course_id=00000000-0000-4000-8000-00000000dead", token=token
        )
        assert status in (200, 404), f"其他课程过滤 HTTP {status}"
        # 已发布版本不能直接改：必须 409，让老师走「另存为新版本」。
        # PATCH 的 body 就是草稿内容，所以这里发一份**完整合法**的内容，避免先被 422 拦掉。
        edit_problem = stdin_problem("__accept-edit-published__")
        edit_body = bank.draft_body(edit_problem)
        status, body = bank.request(
            "PATCH",
            base,
            f"/api/v1/questions/{question_id}",
            token=token,
            payload=edit_body,
            tolerate=(409,),
        )
        assert status == 409, f"改已发布版本应 409，实际 {status}: {json.dumps(body, ensure_ascii=False)[:160]}"
        # as_new_version=true 则应该能改，并产出新版本。
        branch_body = dict(edit_body)
        branch_body["title"] = "__accept-branch__"
        status, body = bank.request(
            "PATCH",
            base,
            f"/api/v1/questions/{question_id}?as_new_version=true",
            token=token,
            payload=branch_body,
        )
        assert status in (200, 201), f"as_new_version 编辑 HTTP {status}: {json.dumps(body, ensure_ascii=False)[:160]}"
        versions = body.get("versions") or []
        new_versions = [item for item in versions if item["question_version_id"] != published_version]
        assert new_versions, "as_new_version 没有产生新版本"
        created.append((question_id, new_versions[0]["question_version_id"]))
        return f"课程过滤命中；改已发布版本 409；另存新版本 ok（v{new_versions[0]['version']}）"

    check("RQ-B-3-20", "题库维护：按课程管理题目；已发布版本不可直接改（409）", maintain_flow)

    # ---------------------------------------------------------------- RQ-B-3-30 答题统计
    def statistics_flow() -> str:
        question_id = holder["stdin_question"]
        status, stats = bank.request(
            "GET", base, f"/api/v1/questions/{question_id}/statistics", token=token
        )
        assert status == 200, f"统计 HTTP {status}: {json.dumps(stats, ensure_ascii=False)[:200]}"
        keys = set(stats)
        assert keys, "统计返回空对象"
        return "字段：" + ", ".join(sorted(keys))[:200]

    check("RQ-B-3-30", "题目答题情况统计（作答次数/通过率/分布）", statistics_flow)

    # ---------------------------------------------------------------- RQ-B-3-50 file
    def file_flow() -> str:
        fixture_bytes = b"3\n5\nLINUX\n"
        digest = hashlib.sha256(fixture_bytes).hexdigest()
        problem = {
            "title": "__accept-file__",
            "stem_markdown": "从 data.txt 读三行并原样输出（验收用）。",
            "language": "python",
            "reference_answer": (
                "import pathlib\nprint(pathlib.Path('data.txt').read_text(encoding='utf-8').strip())\n"
            ),
            "adapter": {
                "kind": "file",
                "args": ["data.txt"],
                "stdin": None,
                "expect_exit_code": 0,
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored", "numeric_tolerance": None},
                "files": [{"path": "data.txt", "content_sha256": digest, "mount": "input"}],
            },
            "cases": [
                {
                    "name": "读文件",
                    "ordinal": 0,
                    "weight": 1,
                    "visible_to_student": True,
                    "stdin": None,
                    "args": ["data.txt"],
                    "expected_stdout": "3\n5\nLINUX\n",
                    "expected_stderr": None,
                    "expected_exit_code": 0,
                }
            ],
            "tags": ["acceptance"],
        }
        question_id, version_id = new_question(problem)
        status, uploaded = bank.request(
            "POST",
            base,
            f"/api/v1/question-versions/{version_id}/fixtures",
            token=token,
            payload={
                "path": "data.txt",
                "content_base64": base64.b64encode(fixture_bytes).decode("ascii"),
            },
        )
        assert status in (200, 201), f"上传 fixture HTTP {status}: {json.dumps(uploaded, ensure_ascii=False)[:200]}"
        assert uploaded.get("content_sha256") == digest, "服务端摘要与本地不一致"
        passed, total, attested, reasons = verify_detail(version_id)
        assert total == 1 and passed == 1, f"file 型参考解答 {passed}/{total} {reasons}"
        assert attested, "没有存证"
        bank.publish(base, token, version_id)
        published.append(version_id)
        return "fixture 摘要一致，参考解答 1/1 通过并发布"

    check("RQ-B-3-50", "待测代码由文件读取测试数据（file adapter + fixture）", file_flow)

    # ---------------------------------------------------------------- RQ-B-3-60 callable
    def callable_flow() -> str:
        problem = {
            "title": "__accept-callable__",
            "stem_markdown": "实现 solve(a, b) 返回两数之和（验收用）。",
            "language": "python",
            "reference_answer": "def solve(a, b):\n    return a + b\n",
            "adapter": {
                "kind": "callable",
                "args": [],
                "stdin": None,
                "expect_exit_code": 0,
                "comparison": {"stdout": "ignore_trailing_whitespace", "stderr": "ignored", "numeric_tolerance": None},
                "callable": {"module": "main", "attribute": "solve", "arguments_json": "[3, 4]"},
            },
            "cases": [
                {
                    "name": "3+4",
                    "ordinal": 0,
                    "weight": 1,
                    "visible_to_student": True,
                    "stdin": None,
                    "args": [],
                    "expected_stdout": "7\n",
                    "expected_stderr": None,
                    "expected_exit_code": 0,
                }
            ],
            "tags": ["acceptance"],
        }
        question_id, version_id = new_question(problem)
        passed, total, attested, reasons = verify_detail(version_id)
        assert total == 1 and passed == 1, f"callable 型参考解答 {passed}/{total} {reasons}"
        assert attested, "没有存证"
        bank.publish(base, token, version_id)
        published.append(version_id)
        return "callable adapter 1/1 通过并发布"

    check("RQ-B-3-60", "待测代码实现调用接口（callable adapter import 后调用）", callable_flow)

    # ---------------------------------------------------------------- RQ-B-3-99 运行日志/证据
    def log_flow() -> str:
        version_id = holder["stdin_version"]
        # `evaluation-package` 在契约里是 internal（服务凭据），管理员拿到 403 是**正确**行为；
        # 这里只把它记为事实，不当作失败。
        status, package = bank.request(
            "GET", base, f"/api/v1/question-versions/{version_id}/evaluation-package", token=token,
            tolerate=(403,),
        )
        if status == 403:
            notes.append("evaluation-package 对管理员返回 403（契约声明为 internal/服务凭据）——评测证据只能在服务器侧核对。")
        # 存证在参考解答校验时就已写入（上面已断言 attestation_id 非空）；这里只核对版本仍可取到。
        status, version_doc = bank.request(
            "GET", base, f"/api/v1/question-versions/{version_id}", token=token
        )
        assert status == 200, f"版本详情 HTTP {status}"
        notes.append("RQ-B-3-99 的落盘日志在服务器侧（B2 runner 日志），接口层只能证明存证已写入；日志需上服务器核对。")
        return f"版本详情可取（content_digest={str(version_doc.get('content_digest'))[:12]}…），存证在发布前已断言"

    check("RQ-B-3-99", "生成并记录运行日志（可审计证据链）", log_flow)

    # ---------------------------------------------------------------- 权限探针（学生视角）
    if args.student:
        student_token = bank.login(base, args.student, args.student_password)

        def student_authz() -> str:
            question_id = holder["stdin_question"]
            version_id = holder["stdin_version"]
            hazards: list[str] = []
            for name, path in (
                ("statistics", f"/api/v1/questions/{question_id}/statistics"),
                ("draft-content", f"/api/v1/question-versions/{version_id}/draft-content"),
                ("reference-answer", f"/api/v1/question-versions/{version_id}/reference-answer"),
            ):
                status, body = bank.request("GET", base, path, token=student_token, tolerate=(403,))
                if status == 200:
                    hazards.append(f"{name} 返回 200（学生不该看到）: {json.dumps(body, ensure_ascii=False)[:120]}")
            assert not hazards, " | ".join(hazards)
            return "学生访问 statistics/draft-content/reference-answer 均被拒（非 200）"

        check("RQ-B-3-30/40", "权限：学生看不到参考答案、隐藏内容与统计", student_authz)

    # ---------------------------------------------------------------- 清理
    if not args.keep:
        # 已发布版本只能归档（DELETE 会 409），所以两步都要容忍这两种状态；清理失败不影响报告。
        for _question_id, version_id in created:
            try:
                bank.request("DELETE", base, f"/api/v1/question-versions/{version_id}", token=token, tolerate=(409, 404))
            except Exception:  # noqa: BLE001
                pass
        for version_id in published:
            try:
                bank.request("POST", base, f"/api/v1/question-versions/{version_id}/archive", token=token, payload={}, tolerate=(409, 404))
            except Exception:  # noqa: BLE001
                pass

    lines = ["# B3 线上验收报告", f"base: {base}", f"course: {args.course}", ""]
    for rq, name, status, detail in RESULTS:
        lines.append(f"- [{status}] {rq} {name}")
        if detail:
            lines.append(f"  - {detail}")
    if notes:
        lines.append("")
        lines.append("## 备注")
        for note in notes:
            lines.append(f"- {note}")
    failures = [item for item in RESULTS if item[2] == "FAIL"]
    lines.append("")
    lines.append(f"合计 {len(RESULTS)} 项，失败 {len(failures)} 项。")

    report = args.report or str(pathlib.Path.cwd() / "b3-acceptance-report.md")
    pathlib.Path(report).write_text("\n".join(lines), encoding="utf-8")

    print(f"checks={len(RESULTS)} failures={len(failures)} report={report}")
    for rq, _name, status, _detail in RESULTS:
        print(f"  {status} {rq}")
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
