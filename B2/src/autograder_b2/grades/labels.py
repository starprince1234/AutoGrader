"""成绩导出的人类可读标签解析（B2 → B4/B3 只读补全）。

导出文件里的 `course_id` / `section_id` / `assignment_id` / `question_version_id` 是
稳定标识，但教师打开表格时需要的是课程名、课序、作业名、题目标题。B2 只拥有 `b2` schema，
因此这里**不跨 schema 查询**，而是用调用者自己的 bearer 头调用已交付的 B4/B3 只读接口：

- B4：课程列表、课程下的课序（返回课程名与课序号/班级名）；
- B3：作业标题、题目版本对应的题目标题。

任何一步失败都**不阻断导出**：回退到原始 ID（文件仍然可用），但不会伪造一个标签。
解析结果按本次导出的唯一 ID 去重，每类只请求一次，行数多时不会产生 N 次 HTTP。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 10.0
RESPONSE_LIMIT_BYTES = 2 * 1024 * 1024
PAGE_LIMIT = 200


class ExportLabelResolver:
    """把导出行的 ID 映射成人读标签；失败时返回空映射，由渲染层回退到 ID。"""

    def __init__(
        self,
        *,
        b4_base_url: str | None,
        b3_base_url: str | None,
        b3_authorization: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._b4 = b4_base_url.rstrip("/") if b4_base_url else None
        self._b3 = b3_base_url.rstrip("/") if b3_base_url else None
        self._b3_authorization = b3_authorization
        self._timeout = timeout_seconds

    def resolve(
        self, rows: Iterable[Any], *, authorization: str | None
    ) -> dict[str, dict[str, str]]:
        if not rows:
            return {}
        try:
            return self._resolve(rows, authorization=authorization)
        except Exception:
            # 标签是增强信息；B4/B3 任一不可用时导出仍应成功（用 ID），不能因此 503。
            return {}

    def _resolve(
        self, rows: Iterable[Any], *, authorization: str | None
    ) -> dict[str, dict[str, str]]:
        row_list = list(rows)
        course_ids = sorted({str(row.course_id) for row in row_list if row.course_id})
        section_ids = sorted({str(row.section_id) for row in row_list if row.section_id})
        assignment_ids = sorted(
            {str(row.assignment_id) for row in row_list if row.assignment_id}
        )
        version_ids = sorted(
            {str(row.question_version_id) for row in row_list if row.question_version_id}
        )
        labels: dict[str, dict[str, str]] = {
            "course_id": {},
            "section_id": {},
            "assignment_id": {},
            "question_version_id": {},
        }
        b4_headers = self._headers(authorization)
        b3_headers = self._headers(self._b3_authorization or authorization)

        for course_id in course_ids:
            if self._b4 is None:
                break
            course = self._get_json(f"{self._b4}/api/v1/courses/{course_id}", b4_headers)
            title = course.get("title") if isinstance(course, Mapping) else None
            if isinstance(title, str) and title:
                labels["course_id"][course_id] = title
        for section_id in section_ids:
            if self._b4 is None:
                break
            sections = self._get_json(
                f"{self._b4}/api/v1/courses/{self._course_of(section_id, row_list)}/sections",
                b4_headers,
            )
            for item in _items(sections):
                if str(item.get("section_id")) != section_id:
                    continue
                label = item.get("label") or item.get("section_code")
                if isinstance(label, str) and label:
                    labels["section_id"][section_id] = label
                break
        for assignment_id in assignment_ids:
            if self._b3 is None:
                break
            assignment = self._get_json(
                f"{self._b3}/api/v1/assignments/{assignment_id}", b3_headers
            )
            title = assignment.get("title") if isinstance(assignment, Mapping) else None
            if isinstance(title, str) and title:
                labels["assignment_id"][assignment_id] = title
        for version_id in version_ids:
            if self._b3 is None:
                break
            version = self._get_json(
                f"{self._b3}/api/v1/question-versions/{version_id}", b3_headers
            )
            question_id = version.get("question_id") if isinstance(version, Mapping) else None
            if not isinstance(question_id, str):
                continue
            question = self._get_json(
                f"{self._b3}/api/v1/questions/{question_id}", b3_headers
            )
            title = question.get("title") if isinstance(question, Mapping) else None
            if isinstance(title, str) and title:
                labels["question_version_id"][version_id] = title
        return labels

    @staticmethod
    def _headers(authorization: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        return headers

    @staticmethod
    def _course_of(section_id: str, rows: Iterable[Any]) -> str:
        for row in rows:
            if str(row.section_id) == section_id:
                return str(row.course_id)
        raise KeyError(section_id)

    def _get_json(self, url: str, headers: Mapping[str, str]) -> Any:
        request = urllib.request.Request(url, method="GET", headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status = int(response.status)
                body = response.read(RESPONSE_LIMIT_BYTES)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RuntimeError("export label lookup failed") from error
        if status < 200 or status >= 300:
            raise RuntimeError("export label lookup failed")
        return json.loads(body.decode("utf-8", errors="replace"))


def _items(document: Any) -> list[Mapping[str, Any]]:
    if not isinstance(document, Mapping):
        return []
    items = document.get("items")
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


__all__ = ["ExportLabelResolver"]
