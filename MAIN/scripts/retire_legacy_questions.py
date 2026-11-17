"""旧题下线：把两门课里不属于新题库（20+20）的题目全部归档（草稿直接删除）。

契约只允许删除**草稿**版本，已发布版本只能归档；归档之后的物理删除由部署工作流的
`purge-retired-questions` 操作完成（它只删"所有版本都已归档"的题）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, r"D:\github本地仓库\AutoGrader\MAIN\scripts")
import seed_dual_track_python as api  # noqa: E402

ROOT = pathlib.Path(r"D:\github本地仓库\AutoGrader\MAIN\scripts")
COURSES = {
    "Python编程与实践作业": ROOT / "python-dual-bank.json",
    "Unix操作系统作业": ROOT / "unix-dual-bank.json",
}


def main() -> int:
    password = os.environ["AUTOGRADER_ADMIN_PASSWORD"]
    status, session = api.call(
        "POST", "/api/v1/auth/session", payload={"username": "admin", "password": password}
    )
    if status != 200:
        raise SystemExit(f"sign-in failed: {status}")
    token = session["access_token"]

    _, courses = api.call("GET", "/api/v1/courses?limit=200", token)
    report = []
    for title, bank_path in COURSES.items():
        keep = {item["title"] for item in json.loads(bank_path.read_text(encoding="utf-8"))}
        course_id = next(c["course_id"] for c in courses["items"] if c["title"] == title)
        _, page = api.call(
            "GET", f"/api/v1/questions?course_id={course_id}&limit=300&published_only=false", token
        )
        for item in page.get("items", []):
            if item["title"] in keep:
                continue
            actions = []
            for version in item.get("versions") or []:
                version_id = version.get("question_version_id")
                if version.get("status") == "draft":
                    code, _ = api.call("DELETE", f"/api/v1/question-versions/{version_id}", token)
                    actions.append(f"delete-draft:{code}")
                elif version.get("status") == "published":
                    code, body = api.call(
                        "POST", f"/api/v1/question-versions/{version_id}/archive", token, {}
                    )
                    actions.append(f"archive:{code}")
            report.append({"course": title, "title": item["title"], "actions": actions})
            safe = item["title"].encode("ascii", "backslashreplace").decode("ascii")
            print(title, "|", safe, "|", " ".join(actions))

    target = ROOT / "cleanup-report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"retired {len(report)} question(s) -> {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
