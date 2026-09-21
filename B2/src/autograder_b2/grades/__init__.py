"""成绩查询与导出（RQ-B-2-60 的导出一侧 + RQ-B-1-30 / RQ-B-1-60 的 B2 侧）。

两个服务层模块，均**不含路由**（`GET /api/v1/grades`、`GET /api/v1/grades/export` 的注册由 lead
在统一的路由装配点完成）：

- `grades.query`：`GradeQueryService.list_grades(...) -> GradePage`（游标分页）、
  `GradeQueryService.iter_grades(...) -> Iterator[GradeRow]`（导出复用）、
  `grade_page_body/grade_row_body`（契约 JSON 形状）；
- `grades.export`：`GradesExportService.export(..., fmt="xlsx"|"csv") -> ExportFile`。

路由层要接的三件事（签名见各自模块）：

```python
page = query.list_grades(principal=principal, course_id=..., section_id=..., limit=..., cursor=...)
return JSONResponse(grade_page_body(page))

file = exporter.export(principal=principal, course_id=..., fmt=format)
return Response(file.content, media_type=file.media_type,
                headers={"Content-Disposition": file.content_disposition})
```

异常映射：`AuthorizationError` → 403（`api.errors.FORBIDDEN`）；`InvalidFilterError` /
`InvalidCursorError` / `UnsupportedExportFormat` → 422（`api.errors.UNPROCESSABLE`）。
"""

from autograder_b2.grades.export import (
    CSV_MEDIA_TYPE,
    ESCAPE_PREFIX,
    EXPORT_FORMATS,
    FORMULA_TRIGGERS,
    GRADE_EXPORT_COLUMNS,
    XLSX_MEDIA_TYPE,
    ExportFile,
    ExportFormat,
    GradesExportService,
    UnsupportedExportFormat,
    escape_spreadsheet_cell,
    render_csv,
    render_xlsx,
)
from autograder_b2.grades.query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    GradePage,
    GradeQueryService,
    GradeRow,
    InvalidCursorError,
    InvalidFilterError,
    covered_sections,
    decode_cursor,
    encode_cursor,
    grade_page_body,
    grade_row_body,
    require_grade_authority,
)

__all__ = [
    "CSV_MEDIA_TYPE",
    "DEFAULT_LIMIT",
    "ESCAPE_PREFIX",
    "EXPORT_FORMATS",
    "FORMULA_TRIGGERS",
    "GRADE_EXPORT_COLUMNS",
    "MAX_LIMIT",
    "XLSX_MEDIA_TYPE",
    "ExportFile",
    "ExportFormat",
    "GradePage",
    "GradeQueryService",
    "GradeRow",
    "GradesExportService",
    "InvalidCursorError",
    "InvalidFilterError",
    "UnsupportedExportFormat",
    "covered_sections",
    "decode_cursor",
    "encode_cursor",
    "escape_spreadsheet_cell",
    "grade_page_body",
    "grade_row_body",
    "render_csv",
    "render_xlsx",
    "require_grade_authority",
]
