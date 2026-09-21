"""成绩查询与导出（RQ-B-2-60 的导出一侧 + RQ-B-1-30 / RQ-B-1-60 的 B2 侧）。

七组要求，每组都用**真实 PostgreSQL** 与**真正解析回来的**导出文件断言（不比对渲染输入）：

1. 口径一致：同一筛选条件下，查询结果与 CSV/XLSX 解析结果逐字段一致；
2. 公式注入：`=`/`+`/`-`/`@`/Tab/CR 开头的值在两种格式里都被转义，且先证明"不转义确实是公式"
   （六个触发字符各占一行整文件扫描 + 产物里不存在 `<f>` 公式元素）；
3. 未完成 ≠ 0 分：非终态运行的行 `score` 为 `None`，导出里对应单元格为**空**而不是 `0`；
4. 中文编码：CSV 以 UTF-8 BOM 开头，中文列名与中文值解析无乱码；
5. 越权：学生读他人 / 教师跨课程 / 学生改 `student_id` 放大范围 → `AuthorizationError`（路由层 403）；
6. 游标分页：分页不重不漏，且是 keyset 而非 offset；
7. 确定性产物：同输入 → 同字节（跨秒边界也是），不同输入 → 不同字节（XLSX 曾把墙钟写进
   `docProps/core.xml` 与 ZIP 条目，导致按内容比对的用例天然 flaky）。

契约字段名**从 `MAIN/contracts/openapi/b2.yaml` 读取**（`_contract_grade_row_fields`），不在测试里
手抄一遍字段表——契约改了而代码没跟上时，这里必须失败。
"""

from __future__ import annotations

import codecs
import csv
import io
import time
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

import pytest
import yaml
from openpyxl import Workbook, load_workbook
from sqlalchemy import Engine, delete, select

from autograder_b2 import contracts
from autograder_b2.auth.authorization import AuthorizationError
from autograder_b2.auth.tokens import CourseScope, Principal
from autograder_b2.db.schema import evaluation_results, evaluation_runs, submissions
from autograder_b2.grades.export import (
    ESCAPE_PREFIX,
    FORMULA_TRIGGERS,
    GRADE_EXPORT_COLUMNS,
    GradesExportService,
    escape_spreadsheet_cell,
    render_csv,
    render_xlsx,
)
from autograder_b2.grades.query import (
    GradeQueryService,
    GradeRow,
    InvalidCursorError,
    InvalidFilterError,
    grade_page_body,
    grade_row_body,
)
from tests.conftest import requires_postgres

pytestmark = requires_postgres

#: 数据集基准时刻（UTC；微秒位在毫秒内，便于验证"毫秒截断"而不是"四舍五入"）。
BASE = datetime(2026, 9, 15, 8, 0, 0, 100_000, tzinfo=UTC)
DIGEST = "a" * 64
_HEADER = {field: header for field, header in GRADE_EXPORT_COLUMNS}

#: 以公式触发字符开头的值（契约明文列举的六类）。
HOSTILE_VALUES = (
    "=1+1",
    "=cmd|' /C calc'!A0",
    "+1+1",
    "-2+3+cmd|' /C calc'!A0",
    "@SUM(A1)",
    "\t=1+1",
    "\r=1+1",
)


# --- 夹具与构造器 -------------------------------------------------------------------------


@pytest.fixture()
def query(engine: Engine) -> GradeQueryService:
    return GradeQueryService(engine)


@pytest.fixture()
def exporter(engine: Engine) -> GradesExportService:
    return GradesExportService(engine)


def _teacher(course: uuid.UUID, *, section: uuid.UUID | None = None) -> Principal:
    scope = CourseScope(
        course_id=str(course), section_id=None if section is None else str(section)
    )
    return Principal(
        user_id=str(uuid.uuid4()),
        roles=("teacher",),
        scopes=(scope,),
        token_id=str(uuid.uuid4()),
    )


def _student(user_id: uuid.UUID, course: uuid.UUID, section: uuid.UUID) -> Principal:
    return Principal(
        user_id=str(user_id),
        roles=("student",),
        scopes=(CourseScope(course_id=str(course), section_id=str(section)),),
        token_id=str(uuid.uuid4()),
    )


def _seed_submission(
    engine: Engine,
    *,
    course_id: uuid.UUID,
    section_id: uuid.UUID,
    student_id: uuid.UUID,
    question_version_id: uuid.UUID,
    submitted_at: datetime,
    state: str = "queued",
) -> uuid.UUID:
    """写入一条提交行（形状 = 真实的 `intake='api'` 提交，含 0008 要求的 provenance）。

    迁移 `20260915_0008_b2_submission_provenance` 给 `b2.submissions` 加了 `correlation_id NOT NULL`
    与 provenance 五列，并由 `ck_submissions_provenance_shape` 按 `intake` 取值锁形状：这里写
    `intake='api'`，因此 `request_id` 非空、邮件四列（`mailbox`/`uidvalidity`/`uid`/`message_id`）
    **留空**（不写入即为 NULL）。
    """
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            submissions.insert().values(
                submission_id=identifier,
                student_id=student_id,
                course_id=course_id,
                section_id=section_id,
                question_version_id=question_version_id,
                intake="api",
                idempotency_key=f"key-{identifier}",
                idempotency_scope=str(course_id),
                submission_digest=DIGEST,
                state=state,
                created_at=submitted_at,
                updated_at=submitted_at,
                correlation_id=uuid.uuid4(),
                request_id=str(uuid.uuid4()),
            )
        )
    return identifier


def _seed_run(
    engine: Engine,
    *,
    submission_id: uuid.UUID,
    question_version_id: uuid.UUID,
    state: str,
    sequence: int = 1,
    occurred_at: datetime | None = None,
    score: str | None = None,
    max_score: str = "100",
    is_pass: bool | None = None,
    report: dict[str, Any] | None = None,
) -> uuid.UUID:
    """写入一次评测运行（终态另写一行结果）。

    终态语义（迁移 `20260915_0007_b2_terminal_reason_semantics` 起）：
    `completed` 必须有 `finished_at` 且 **`terminal_reason` 为 NULL**；`rejected`/`failed` 必须有
    `finished_at` **且** `terminal_reason` 非空。0006 曾要求 `completed` 也带原因，而契约
    `EvaluationStreamFrame.terminal_reason` 的枚举里只有四种**失败**原因、没有"成功"取值——
    0007 放宽了这条过紧的约束（夹具此前只能借用 `internal_error` 占位，现在照契约写 NULL）。
    """
    moment = BASE if occurred_at is None else occurred_at
    terminal = state in {"completed", "rejected", "failed"}
    reason = "infrastructure_error" if state in {"rejected", "failed"} else None
    identifier = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            evaluation_runs.insert().values(
                evaluation_run_id=identifier,
                submission_id=submission_id,
                state=state,
                stage="terminal" if terminal else "dynamic_execution",
                sequence=sequence,
                cases_total=None,
                cases_done=0,
                terminal_reason=reason,
                submission_digest=DIGEST,
                question_version_id=question_version_id,
                started_at=moment,
                finished_at=moment if terminal else None,
                created_at=moment,
                updated_at=moment,
            )
        )
        if score is not None:
            connection.execute(
                evaluation_results.insert().values(
                    evaluation_run_id=identifier,
                    score=Decimal(score),
                    max_score=Decimal(max_score),
                    is_pass=bool(is_pass),
                    result_digest=DIGEST,
                    report={} if report is None else report,
                    persisted_at=moment,
                )
            )
    return identifier


def _synthetic_row(**overrides: object) -> GradeRow:
    """直接构造一行（用于渲染层用例：查询结果里今天没有可由外部控制的文本字段）。"""
    defaults: dict[str, object] = {
        "submission_id": str(uuid.uuid4()),
        "student_id": str(uuid.uuid4()),
        "student_number": "20260001",
        "display_name": "张三",
        "course_id": str(uuid.uuid4()),
        "section_id": str(uuid.uuid4()),
        "question_version_id": str(uuid.uuid4()),
        "state": "completed",
        "score": 86.5,
        "max_score": 100.0,
        "is_pass": True,
        "submitted_at": BASE,
        "evaluation_run_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return GradeRow(**defaults)  # type: ignore[arg-type]


# --- 导出文件解析（真的解析字节，不比渲染输入） --------------------------------------------


def _read_csv(payload: bytes) -> tuple[list[str], list[dict[str, object]]]:
    text = payload.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header = [str(cell) for cell in rows[0]]
    return header, [dict(zip(header, row, strict=True)) for row in rows[1:]]


def _read_xlsx(payload: bytes) -> tuple[list[str], list[dict[str, object]]]:
    sheet = load_workbook(io.BytesIO(payload), read_only=True).active
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    header = [str(cell) for cell in rows[0]]
    return header, [dict(zip(header, row, strict=True)) for row in rows[1:]]


def _as_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value))


def _as_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _as_timestamp(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
    return str(value)


def _text_or_none(value: object) -> object:
    """空单元格（CSV 的 `""` 与 XLSX 的 `None`）统一为 `None`，其余取文本。"""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else str(value)


def _normalised(record: dict[str, object]) -> dict[str, object]:
    """把一行解析结果规范化为契约 `GradeRow` 的 JSON 形状，便于与查询结果逐字段比对。"""

    def cell(field: str) -> object:
        return _text_or_none(record[_HEADER[field]])

    return {
        "submission_id": cell("submission_id"),
        "student_id": cell("student_id"),
        "student_number": cell("student_number"),
        "display_name": cell("display_name"),
        "course_id": cell("course_id"),
        "section_id": cell("section_id"),
        "question_version_id": cell("question_version_id"),
        "state": cell("state"),
        "score": _as_number(record[_HEADER["score"]]),
        "max_score": _as_number(record[_HEADER["max_score"]]),
        "is_pass": _as_bool(record[_HEADER["is_pass"]]),
        "submitted_at": _as_timestamp(record[_HEADER["submitted_at"]]),
        "evaluation_run_id": cell("evaluation_run_id"),
    }


def _by_submission(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(record[_HEADER["submission_id"]]): record for record in records}


def _contract_grade_row_fields() -> list[str]:
    """契约 `GradeRow` 的字段名与顺序（**从契约文件读**，不在测试里手抄字段表）。"""
    path = contracts.MAIN_CONTRACTS_ROOT / "openapi" / "b2.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    properties = document["components"]["schemas"]["GradeRow"]["properties"]
    assert isinstance(properties, dict)
    return list(properties)


def _ms_floor(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


# --- 1. 口径一致 ---------------------------------------------------------------------------


def test_dto_and_export_columns_match_contract_grade_row() -> None:
    """行 DTO 与导出列都**恰好**是契约 `GradeRow` 的字段（字段名以契约为准，不手抄）。"""
    declared = _contract_grade_row_fields()
    assert list(GradeRow.__dataclass_fields__) == declared
    assert [field for field, _ in GRADE_EXPORT_COLUMNS] == declared
    assert len({header for _, header in GRADE_EXPORT_COLUMNS}) == len(declared)


def test_query_and_export_agree_field_by_field(
    engine: Engine, query: GradeQueryService, exporter: GradesExportService
) -> None:
    """同一筛选条件下：查询结果 == CSV 解析结果 == XLSX 解析结果（逐字段）。"""
    course, section = uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    question = uuid.uuid4()
    finished = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE,
        state="completed",
    )
    _seed_run(
        engine,
        submission_id=finished,
        question_version_id=question,
        state="completed",
        score="86.5",
        is_pass=True,
    )
    running = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE + timedelta(seconds=1),
        state="running",
    )
    _seed_run(engine, submission_id=running, question_version_id=question, state="running")
    _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE + timedelta(seconds=2),
        state="queued",
    )

    page = query.list_grades(principal=teacher, course_id=str(course))
    assert len(page.items) == 3
    expected = [grade_row_body(row) for row in page.items]

    header, csv_records = _read_csv(
        exporter.export(principal=teacher, course_id=str(course), fmt="csv").content
    )
    assert header == [h for _, h in GRADE_EXPORT_COLUMNS]
    assert [_normalised(record) for record in csv_records] == expected

    header_xlsx, xlsx_records = _read_xlsx(
        exporter.export(principal=teacher, course_id=str(course), fmt="xlsx").content
    )
    assert header_xlsx == header
    assert [_normalised(record) for record in xlsx_records] == expected

    # 三种来源的行序一致（同一游标顺序），且导出行数 == 查询行数
    assert [record[_HEADER["submission_id"]] for record in csv_records] == [
        row.submission_id for row in page.items
    ]


def test_export_walks_every_cursor_page(
    engine: Engine, query: GradeQueryService
) -> None:
    """导出内部按游标逐页取数：批大小 2、共 5 行时导出仍等于完整查询结果（跨页不丢行）。"""
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    for index in range(5):
        _seed_submission(
            engine,
            course_id=course,
            section_id=section,
            student_id=uuid.uuid4(),
            question_version_id=question,
            submitted_at=BASE + timedelta(seconds=index),
            state="validated",
        )
    exporter = GradesExportService(engine, batch_size=2)
    whole = query.list_grades(principal=teacher, course_id=str(course), limit=200)
    assert len(whole.items) == 5
    _, records = _read_csv(exporter.export(principal=teacher, course_id=str(course), fmt="csv").content)
    assert [record[_HEADER["submission_id"]] for record in records] == [
        row.submission_id for row in whole.items
    ]


def test_student_number_and_display_name_are_null_not_invented(
    engine: Engine, query: GradeQueryService, exporter: GradesExportService
) -> None:
    """`student_number`/`display_name` 属 B4：B2 不读 `b4` schema，故恒为 `null`（不伪造、不拼凑）。

    书面观察：契约 `GradeRow` 允许二者为 `null`，字段照常出现以保持行形状不变。若将来有人把
    `b4.users` 的姓名/学号 join 进 B2 的成绩行，那就是跨 schema 读取（`AGENTS.md` 硬规则 2 禁止）；
    正确做法是 B4 提供批量查询或由网关层补全。
    """
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE,
        state="validated",
    )
    row = query.list_grades(principal=teacher, course_id=str(course)).items[0]
    assert row.student_number is None
    assert row.display_name is None
    body = grade_row_body(row)
    assert set(body) == set(_contract_grade_row_fields())
    assert body["student_number"] is None and body["display_name"] is None

    _, csv_records = _read_csv(
        exporter.export(principal=teacher, course_id=str(course), fmt="csv").content
    )
    assert csv_records[0][_HEADER["student_number"]] == ""
    assert csv_records[0][_HEADER["display_name"]] == ""


def test_export_filename_and_media_types(
    engine: Engine, exporter: GradesExportService
) -> None:
    """文件名与媒体类型（路由层据此设置 `Content-Disposition` / `Content-Type`）。"""
    course = uuid.uuid4()
    teacher = _teacher(course)
    csv_file = exporter.export(principal=teacher, course_id=str(course), fmt="csv")
    xlsx_file = exporter.export(principal=teacher, course_id=str(course), fmt="xlsx")
    assert csv_file.media_type == "text/csv"
    assert xlsx_file.media_type.endswith("spreadsheetml.sheet")
    assert csv_file.filename == f"grades-{course}.csv"
    assert csv_file.content_disposition == f'attachment; filename="grades-{course}.csv"'
    assert csv_file.row_count == 0
    with pytest.raises(ValueError, match="format must be one of"):
        exporter.export(principal=teacher, course_id=str(course), fmt="pdf")


# --- 2. 公式注入 ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_formula_injection_is_escaped_in_csv_and_xlsx(hostile: str) -> None:
    """以触发字符开头的值在 CSV 与 XLSX 里都被前缀 `'` 转义，并先证明"不转义确实成公式"。"""
    row = _synthetic_row(student_number=hostile, display_name=hostile)
    escaped = ESCAPE_PREFIX + hostile

    # 前提 1：首字符属于契约列举的公式触发集合（未转义时 Excel/Calc 会把它当公式）
    assert hostile[0] in FORMULA_TRIGGERS
    # 前提 2（可演示）：不经转义时 openpyxl 直接把它写成**公式单元格**
    if hostile.startswith("="):
        probe = Workbook().active
        probe["A1"] = hostile
        assert probe["A1"].data_type == "f", "未转义的导出会把单元格值变成公式"

    # 转义后：不再是触发字符开头，且文本以转义前缀开始
    assert escape_spreadsheet_cell(hostile) == escaped
    assert not escaped.startswith(FORMULA_TRIGGERS)

    _, csv_records = _read_csv(render_csv([row]))
    assert csv_records[0][_HEADER["student_number"]] == escaped
    assert csv_records[0][_HEADER["display_name"]] == escaped

    payload = render_xlsx([row])
    _, xlsx_records = _read_xlsx(payload)
    # XLSX 是 XML（1.0 §2.11）：单元格文本里的 CR 会被行尾规范化成 LF。转义前缀仍在，
    # 安全性不受影响；CSV 则原样保留 CR（上一条断言）。
    expected_xlsx = escaped.replace("\r", "\n")
    assert xlsx_records[0][_HEADER["student_number"]] == expected_xlsx
    assert xlsx_records[0][_HEADER["display_name"]] == expected_xlsx
    assert str(xlsx_records[0][_HEADER["student_number"]]).startswith(ESCAPE_PREFIX)

    # XLSX 单元格类型必须是字符串（'s'）而不是公式（'f'）
    sheet = load_workbook(io.BytesIO(payload)).active
    column = [h for _, h in GRADE_EXPORT_COLUMNS].index(_HEADER["student_number"]) + 1
    cell = sheet.cell(row=2, column=column)
    assert cell.data_type == "s"
    assert cell.value == expected_xlsx
    assert all(item.data_type != "f" for cells in sheet.iter_rows() for item in cells)


def test_escape_is_idempotent_and_leaves_plain_text_alone() -> None:
    """转义幂等（不会叠加前缀），普通文本与中文不受影响。"""
    assert escape_spreadsheet_cell(escape_spreadsheet_cell("=1+1")) == "'=1+1"
    assert escape_spreadsheet_cell("张三") == "张三"
    assert escape_spreadsheet_cell("20260001") == "20260001"


#: 契约明文列举的六个触发字符，每个各占**一行**（`=` 另有一条 DDE 载荷，见 `HOSTILE_VALUES`）。
TRIGGERS_ONE_EACH: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def _trigger_rows() -> list[GradeRow]:
    """六个触发字符各一行：`student_number` 与 `display_name`（两个自由文本列）都带载荷。"""
    return [
        _synthetic_row(
            student_number=f"{trigger}STUDENT{index}",
            display_name=f"{trigger}NAME{index}",
        )
        for index, trigger in enumerate(TRIGGERS_ONE_EACH)
    ]


def _worksheet_xml(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


def test_every_trigger_char_has_its_own_row_and_is_escaped_in_both_formats() -> None:
    """六个触发字符各一行：读回来的值 **≠** 原始危险值，去掉转义前缀后 **==** 原始值。

    XLSX 与 CSV 都断言；并且对**整份文件**逐单元格扫描"以触发字符开头却没有转义前缀"的值。
    这里只用字节/单元格级断言，不 `eval`、不执行任何东西、也不落地可执行文件。
    """
    rows = _trigger_rows()
    raw_csv = render_csv(rows)
    raw_xlsx = render_xlsx(rows)
    _, csv_records = _read_csv(raw_csv)
    _, xlsx_records = _read_xlsx(raw_xlsx)
    assert len(csv_records) == len(TRIGGERS_ONE_EACH) == len(xlsx_records)

    for index, trigger in enumerate(TRIGGERS_ONE_EACH):
        assert trigger in FORMULA_TRIGGERS
        for field, label in (("student_number", "STUDENT"), ("display_name", "NAME")):
            original = f"{trigger}{label}{index}"
            csv_cell = str(csv_records[index][_HEADER[field]])
            xlsx_cell = str(xlsx_records[index][_HEADER[field]])
            # ① 不是原值（否则等于没转义） ② 恰好是"转义前缀 + 原值"
            assert csv_cell != original, ("csv", field, index)
            assert xlsx_cell != original, ("xlsx", field, index)
            assert csv_cell == ESCAPE_PREFIX + original
            # XLSX 是 XML 1.0：单元格文本里的 CR 被行尾规范化成 LF（既有的书面口径），其余逐字符相同
            assert xlsx_cell == ESCAPE_PREFIX + original.replace("\r", "\n")
            for cell in (csv_cell, xlsx_cell):
                assert cell.startswith(ESCAPE_PREFIX)
                assert not cell.startswith(FORMULA_TRIGGERS)

    # 整份文件：**没有**任何"未转义即以触发字符开头"的单元格（两种格式都扫）
    for fmt, records in (("csv", csv_records), ("xlsx", xlsx_records)):
        for record in records:
            for header, value in record.items():
                text = "" if value is None else str(value)
                if text.startswith(FORMULA_TRIGGERS):
                    raise AssertionError(
                        f"{fmt} 里出现未转义的触发值 {text!r}（列 {header!r}）"
                    )


def test_exported_xlsx_has_no_formula_element_while_the_unescaped_probe_does() -> None:
    """字节级"不可注入"：不转义时 XLSX 里真的会出现 `<f>` 公式元素，而真实导出里一个都没有。

    这是"导出文件不可注入"最直接的可核验形式：不看单元格类型，直接看产物里有没有公式元素。
    `openpyxl` 把以 `=` 开头的字符串写成公式（`<f>`），所以这条前提是可演示的，不是假想。
    """
    probe = Workbook()
    probe.active["A1"] = "=1+1"
    probe_buffer = io.BytesIO()
    probe.save(probe_buffer)
    assert "<f>" in _worksheet_xml(probe_buffer.getvalue()), "前提不成立：未转义写法应产生公式元素"

    payload = render_xlsx(_trigger_rows())
    worksheet = _worksheet_xml(payload)
    assert "<f>" not in worksheet, "导出产物里出现了公式元素——公式注入防线失效"
    assert "</f>" not in worksheet
    # 自由文本以**内联字符串**写入（与公式单元格是两种不同的表示）
    assert 't="inlineStr"' in worksheet


# --- 3. 未完成 ≠ 0 分 ----------------------------------------------------------------------


def _unfinished_dataset(engine: Engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    identifiers: dict[str, uuid.UUID] = {}
    identifiers["completed"] = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE,
        state="completed",
    )
    _seed_run(
        engine,
        submission_id=identifiers["completed"],
        question_version_id=question,
        state="completed",
        score="86.5",
        is_pass=True,
    )
    identifiers["running"] = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE + timedelta(seconds=1),
        state="running",
    )
    _seed_run(engine, submission_id=identifiers["running"], question_version_id=question, state="running")
    identifiers["queued"] = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE + timedelta(seconds=2),
        state="queued",
    )
    return course, section, question, identifiers


def test_unfinished_run_reports_state_and_null_score(
    engine: Engine, query: GradeQueryService
) -> None:
    """非终态运行（与"尚无运行"）的行：`score` 为 `None` 且带 `state`；已完成的行照常有分数。"""
    course, _section, _question, identifiers = _unfinished_dataset(engine)
    teacher = _teacher(course)
    page = query.list_grades(principal=teacher, course_id=str(course))
    rows = {row.submission_id: row for row in page.items}

    running = rows[str(identifiers["running"])]
    assert running.state == "running"
    assert running.score is None
    assert running.max_score is None
    assert running.is_pass is None
    assert running.evaluation_run_id is not None

    queued = rows[str(identifiers["queued"])]
    assert queued.state == "queued"
    assert queued.score is None
    assert queued.evaluation_run_id is None

    completed = rows[str(identifiers["completed"])]
    assert completed.state == "completed"
    assert completed.score == 86.5
    assert completed.max_score == 100.0
    assert completed.is_pass is True

    body = {item["submission_id"]: item for item in grade_page_body(page)["items"]}
    assert body[str(identifiers["running"])]["score"] is None
    assert body[str(identifiers["running"])]["state"] == "running"
    assert body[str(identifiers["completed"])]["score"] == 86.5


def test_export_leaves_unfinished_score_cells_empty_not_zero(
    engine: Engine, exporter: GradesExportService
) -> None:
    """未完成的行在 CSV/XLSX 里是**空单元格**——不是 `0`、也不是 `0.0`。"""
    course, _section, _question, identifiers = _unfinished_dataset(engine)
    teacher = _teacher(course)

    _, csv_records = _read_csv(
        exporter.export(principal=teacher, course_id=str(course), fmt="csv").content
    )
    unfinished = _by_submission(csv_records)[str(identifiers["running"])]
    assert unfinished[_HEADER["score"]] == ""
    assert unfinished[_HEADER["max_score"]] == ""
    assert unfinished[_HEADER["is_pass"]] == ""
    assert unfinished[_HEADER["state"]] == "running"
    assert unfinished[_HEADER["score"]] != "0"
    assert csv_records[0][_HEADER["score"]] != "0"  # 已完成行确有分数，排除"整列皆空"的假通过
    assert _by_submission(csv_records)[str(identifiers["completed"])][_HEADER["score"]] == "86.5"

    _, xlsx_records = _read_xlsx(
        exporter.export(principal=teacher, course_id=str(course), fmt="xlsx").content
    )
    xlsx_unfinished = _by_submission(xlsx_records)[str(identifiers["running"])]
    assert xlsx_unfinished[_HEADER["score"]] is None
    assert xlsx_unfinished[_HEADER["is_pass"]] is None
    assert xlsx_unfinished[_HEADER["state"]] == "running"
    assert _by_submission(xlsx_records)[str(identifiers["completed"])][_HEADER["score"]] == 86.5


def test_rerun_supersedes_the_previous_score(engine: Engine, query: GradeQueryService) -> None:
    """最新一次运行还没跑完时，行必须报"进行中 + 无分数"，不得沿用上一轮的分数。"""
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    submission = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE,
        state="completed",
    )
    _seed_run(
        engine,
        submission_id=submission,
        question_version_id=question,
        state="completed",
        sequence=1,
        occurred_at=BASE,
        score="86.5",
        is_pass=True,
    )
    rerun = _seed_run(
        engine,
        submission_id=submission,
        question_version_id=question,
        state="running",
        sequence=2,
        occurred_at=BASE + timedelta(minutes=5),
    )

    row = query.list_grades(principal=teacher, course_id=str(course)).items[0]
    assert row.evaluation_run_id == str(rerun)
    assert row.state == "running"
    assert row.score is None, "重跑未完成时沿用旧分数会把'进行中'伪装成成绩"


# --- 4. 中文编码 ---------------------------------------------------------------------------


def test_csv_starts_with_utf8_bom_and_chinese_is_intact(
    engine: Engine, exporter: GradesExportService
) -> None:
    """CSV 以 UTF-8 BOM 开头；中文列名/中文值解析无乱码（缺 BOM 时 Excel 按 GBK 读会乱码）。"""
    course = uuid.uuid4()
    teacher = _teacher(course)
    payload = exporter.export(principal=teacher, course_id=str(course), fmt="csv").content

    assert payload.startswith(codecs.BOM_UTF8), "CSV 必须带 UTF-8 BOM，否则中文在 Excel 里乱码"
    text = payload.decode("utf-8-sig")
    assert text.startswith("提交ID,学生ID,学号,姓名,")
    assert "是否通过" in text and "提交时间(UTC)" in text
    # BOM 是承重的：去掉 BOM 后按 GBK 解码（中文 Windows 上 Excel 的默认假设）得到的是乱码
    assert payload[len(codecs.BOM_UTF8) :].decode("gbk", errors="replace") != text
    assert text.splitlines()[0].split(",")[0] == "提交ID"


def test_chinese_values_survive_both_formats() -> None:
    """含中文值的一行：CSV 与 XLSX 解析回来都与原值逐字符相同。"""
    row = _synthetic_row(display_name="张三", student_number="20260001")
    csv_payload = render_csv([row])
    _, csv_records = _read_csv(csv_payload)
    assert csv_records[0][_HEADER["display_name"]] == "张三"
    assert csv_records[0][_HEADER["student_number"]] == "20260001"
    assert "张三".encode() in csv_payload
    _, xlsx_records = _read_xlsx(render_xlsx([row]))
    assert xlsx_records[0][_HEADER["display_name"]] == "张三"


def test_the_stored_instant_is_reported_identically_by_json_csv_and_xlsx(
    engine: Engine, query: GradeQueryService, exporter: GradesExportService
) -> None:
    """三个面必须报告**同一个**瞬时值；而这个值由**数据库**决定。

    种子写 `…123999`，而 `submissions.created_at` 是 `timestamptz(3)`（迁移 0012 的表标准）：
    Postgres 在**写入时**就把它四舍五入到毫秒（本机实测 `to_char(…, '.US')` → `.124000`），
    此后任何一层都再也看不到 `.123999`。因此 JSON / CSV / XLSX 都报 `.124Z`。

    **这条用例此前期望 `.123`**（"截断而不是四舍五入"），那是迁移 0012 **之前**的数据模型：
    当时列是微秒精度、对外格式化时截断，`.123999` 才会变成 `.123`。列收窄到毫秒之后，
    截断的对象已经不存在了——期望值改成 `.124` 不是"迁就实现"，而是把它钉回真实语义：
    **对外是"库里那个毫秒"，不是"调用方原始微秒的截断"**。本用例先把库里存的值读出来断言，
    再要求三个面与它逐字符一致，因此它证明的是"没有任何一面**各自**再取整一次"。
    """
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    submission_id = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=datetime(2026, 9, 15, 8, 0, 0, 123_999, tzinfo=UTC),
        state="validated",
    )
    with engine.connect() as connection:
        stored = connection.execute(
            select(submissions.c.created_at).where(
                submissions.c.submission_id == submission_id
            )
        ).scalar_one()
    # 库列的四舍五入是**前提**，先证明它（写库时 `.123999` → `.124000`，`.123499` → `.123000`）。
    assert stored.astimezone(UTC) == datetime(2026, 9, 15, 8, 0, 0, 124_000, tzinfo=UTC), (
        f"库列不再把亚毫秒四舍五入到毫秒（读到 {stored!r}）——本用例的期望值要跟着改"
    )
    expected = "2026-09-15T08:00:00.124Z"

    row = query.list_grades(principal=teacher, course_id=str(course)).items[0]
    assert grade_row_body(row)["submitted_at"] == expected

    _, csv_records = _read_csv(
        exporter.export(principal=teacher, course_id=str(course), fmt="csv").content
    )
    assert csv_records[0][_HEADER["submitted_at"]] == expected
    _, xlsx_records = _read_xlsx(
        exporter.export(principal=teacher, course_id=str(course), fmt="xlsx").content
    )
    assert _as_timestamp(xlsx_records[0][_HEADER["submitted_at"]]) == expected


def test_xlsx_truncates_like_the_api_when_the_source_carries_microseconds() -> None:
    """导出层的**口径一致**不变量：同一行在 JSON / CSV 与 XLSX 里必须逐字符相同。

    用**合成行**（不经数据库）注入一个带微秒的瞬时值。这必须用合成行：迁移 0012 之后库列是
    `timestamptz(3)`，**库里已经不可能有微秒**，所以"写入前先截断、不让 openpyxl 的四舍五入
    决定结果"这条不变量只能在导出层直接验证——而它正是 `grades.export._xlsx_moment` 存在的理由
    （若删掉那一步，openpyxl 会把 `.123999` 写成 `.124`，于是同一时刻在 CSV 是 `.123`、
    在 XLSX 是 `.124`，正是"导出口径一致"要防的那类不一致）。
    """
    row = _synthetic_row(submitted_at=datetime(2026, 9, 15, 8, 0, 0, 123_999, tzinfo=UTC))
    expected = "2026-09-15T08:00:00.123Z"

    assert grade_row_body(row)["submitted_at"] == expected

    _, csv_records = _read_csv(render_csv([row]))
    assert csv_records[0][_HEADER["submitted_at"]] == expected
    _, xlsx_records = _read_xlsx(render_xlsx([row]))
    assert _as_timestamp(xlsx_records[0][_HEADER["submitted_at"]]) == expected


# --- 5. 越权（一律 AuthorizationError → 路由层 403） -----------------------------------------


def test_student_sees_only_own_records_and_naming_another_student_is_403(
    engine: Engine, query: GradeQueryService, exporter: GradesExportService
) -> None:
    """学生：默认只见自己；显式指名自己 → 通过；指名他人 → 403（**不是**静默收窄）。"""
    course, section = uuid.uuid4(), uuid.uuid4()
    question = uuid.uuid4()
    owner, intruder = uuid.uuid4(), uuid.uuid4()
    own = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=owner,
        question_version_id=question,
        submitted_at=BASE,
        state="validated",
    )
    other = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=intruder,
        question_version_id=question,
        submitted_at=BASE + timedelta(seconds=1),
        state="validated",
    )
    assert own != other
    principal = _student(owner, course, section)

    page = query.list_grades(principal=principal, course_id=str(course))
    assert [row.submission_id for row in page.items] == [str(own)]
    assert str(other) not in {row.submission_id for row in page.items}

    explicit = query.list_grades(principal=principal, course_id=str(course), student_id=str(owner))
    assert [row.submission_id for row in explicit.items] == [str(own)]

    with pytest.raises(AuthorizationError, match="only read their own"):
        query.list_grades(principal=principal, course_id=str(course), student_id=str(intruder))
    with pytest.raises(AuthorizationError, match="only read their own"):
        exporter.export(principal=principal, course_id=str(course), student_id=str(intruder))


def test_student_in_another_course_is_403(engine: Engine, query: GradeQueryService) -> None:
    course, section = uuid.uuid4(), uuid.uuid4()
    student_id = uuid.uuid4()
    _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=student_id,
        question_version_id=uuid.uuid4(),
        submitted_at=BASE,
        state="validated",
    )
    stranger = _student(student_id, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(AuthorizationError, match="does not cover"):
        query.list_grades(principal=stranger, course_id=str(course))


def test_teacher_course_scope_is_enforced(
    engine: Engine, query: GradeQueryService, exporter: GradesExportService
) -> None:
    """教师本课程 → 通过；跨课程 → 403（查询与导出**都**拦）。"""
    course, section = uuid.uuid4(), uuid.uuid4()
    _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=uuid.uuid4(),
        submitted_at=BASE,
        state="validated",
    )
    teacher = _teacher(course)
    own_course = query.list_grades(principal=teacher, course_id=str(course))
    assert len(own_course.items) == 1

    foreign = uuid.uuid4()
    with pytest.raises(AuthorizationError, match="does not cover"):
        query.list_grades(principal=teacher, course_id=str(foreign))
    with pytest.raises(AuthorizationError, match="does not cover"):
        exporter.export(principal=teacher, course_id=str(foreign))


def test_section_scoped_assistant_cannot_widen_to_the_whole_course(
    engine: Engine, query: GradeQueryService
) -> None:
    """课序受限的作用域：不传 `section_id` 时只能看自己那一节，显式越界课序 → 403。

    这条是本次实现的安全要点：`CourseScope.covers(course, None)` 对课序受限的令牌返回 `True`
    （把 `None` 读作"未指名"），而成绩查询的 `section_id` 是**过滤参数**、`None` 意为整门课。
    若不做课序收敛，一个只覆盖 S1 的助教会用 `?course_id=C` 拿到整门课的成绩。
    """
    course, s1, s2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    question = uuid.uuid4()
    in_s1 = _seed_submission(
        engine,
        course_id=course,
        section_id=s1,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE,
        state="validated",
    )
    in_s2 = _seed_submission(
        engine,
        course_id=course,
        section_id=s2,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE + timedelta(seconds=1),
        state="validated",
    )
    assistant = Principal(
        user_id=str(uuid.uuid4()),
        roles=("assistant",),
        scopes=(CourseScope(course_id=str(course), section_id=str(s1)),),
        token_id=str(uuid.uuid4()),
    )
    page = query.list_grades(principal=assistant, course_id=str(course))
    assert [row.submission_id for row in page.items] == [str(in_s1)]
    assert str(in_s2) not in {row.submission_id for row in page.items}
    assert page.next_cursor is None

    # 显式指名课序也收敛：只覆盖 S1 的作用域请求 S2 → 403（不是"静默返回空页"）
    with pytest.raises(AuthorizationError, match="does not cover"):
        query.list_grades(principal=assistant, course_id=str(course), section_id=str(s2))


def test_assistant_asking_for_a_foreign_section_is_403(
    engine: Engine, query: GradeQueryService
) -> None:
    course, s1, s2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _seed_submission(
        engine,
        course_id=course,
        section_id=s2,
        student_id=uuid.uuid4(),
        question_version_id=uuid.uuid4(),
        submitted_at=BASE,
        state="validated",
    )
    with pytest.raises(AuthorizationError, match="does not cover"):
        query.list_grades(
            principal=_teacher(course, section=s1),
            course_id=str(course),
            section_id=str(s2),
        )


def test_admin_reads_any_course(engine: Engine, query: GradeQueryService) -> None:
    course, section = uuid.uuid4(), uuid.uuid4()
    _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=uuid.uuid4(),
        submitted_at=BASE,
        state="validated",
    )
    admin = Principal(
        user_id=str(uuid.uuid4()), roles=("admin",), scopes=(), token_id=str(uuid.uuid4())
    )
    assert len(query.list_grades(principal=admin, course_id=str(course)).items) == 1


@pytest.mark.parametrize("roles", [(), ("guest",), ("ADMIN",)])
def test_token_without_recognized_role_is_403(
    engine: Engine, query: GradeQueryService, roles: tuple[str, ...]
) -> None:
    """角色是独立一层：没有**已登记**角色的令牌连成绩也不能读（认证 ≠ 授权）。"""
    course, section = uuid.uuid4(), uuid.uuid4()
    student_id = uuid.uuid4()
    _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=student_id,
        question_version_id=uuid.uuid4(),
        submitted_at=BASE,
        state="validated",
    )
    principal = Principal(
        user_id=str(student_id),
        roles=roles,
        scopes=(CourseScope(course_id=str(course), section_id=str(section)),),
        token_id=str(uuid.uuid4()),
    )
    with pytest.raises(AuthorizationError, match="no recognized platform role"):
        query.list_grades(principal=principal, course_id=str(course), student_id=str(student_id))


# --- 6. 游标分页 ---------------------------------------------------------------------------


def _seed_pages(engine: Engine) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """7 行提交：前 3 行的毫秒相同（微秒不同），用于验证游标精度。"""
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    offsets = [0, 120, 350, 1_000_000, 2_000_001, 3_000_002, 4_000_003]
    identifiers = [
        _seed_submission(
            engine,
            course_id=course,
            section_id=section,
            student_id=uuid.uuid4(),
            question_version_id=question,
            submitted_at=BASE + timedelta(microseconds=offset),
            state="validated",
        )
        for offset in offsets
    ]
    return course, identifiers


def _expected_order(engine: Engine, course: uuid.UUID) -> list[str]:
    """产品**声明**的全序：`created_at` 升序，同值按 `submission_id` 升序。

    定序规则见 `grades.query._statement`（"提交时刻升序 + `submission_id` 兜底"）与游标谓词
    （`created_at > m OR (created_at = m AND submission_id > id)`）——两者必须同源，否则 keyset
    会漏行或重行。这里从库里把 `(submission_id, created_at)` 读回来、**按声明的规则排序**：

    - 期望值因此是"按声明顺序排出来的"，**不是插入顺序** —— 插入顺序从来不是承诺；
    - `timestamptz(3)` 会把同一毫秒内的多次插入压成**并列键**（实测 `.123999` 舍成 `.124000`），
      并列时必须有确定的 tie-breaker，产品用的是主键。过去这条用例按插入顺序断言，
      于是"随机 UUID 是否恰好升序"（前三条并列，1/6 概率）决定了成败——那正是它 flaky 的原因。

    规范化小写 UUID 文本的字典序与 Postgres `uuid` 的字节序一致（连字符位置固定），
    因此在 Python 里排序与数据库排序同解。
    """

    with engine.connect() as connection:
        rows = connection.execute(
            select(submissions.c.submission_id, submissions.c.created_at).where(
                submissions.c.course_id == course
            )
        ).fetchall()
    return [str(row[0]) for row in sorted(rows, key=lambda row: (row[1], str(row[0])))]


def test_cursor_pagination_is_complete_and_disjoint(
    engine: Engine, query: GradeQueryService
) -> None:
    """分页不重不漏：每页 2 行的并集 == 全集、行序一致，且同毫秒的行不重复。"""
    course, identifiers = _seed_pages(engine)
    expected = _expected_order(engine, course)
    teacher = _teacher(course)
    whole = query.list_grades(principal=teacher, course_id=str(course), limit=200)
    assert [row.submission_id for row in whole.items] == expected

    # 前提：前 3 行的毫秒部分相同——定序必须在**并列键**上仍然确定（否则分页必漏或必重）
    assert len({_ms_floor(row.submitted_at) for row in whole.items[:3]}) == 1
    # 并列组内按 `submission_id` 升序：这是产品的 tie-breaker，也是"插入顺序不是承诺"的直接断言
    assert [row.submission_id for row in whole.items[:3]] == sorted(expected[:3])

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    # 上限是**测试自身的保险**：游标实现一旦退化（例如按毫秒截断），第二页会重复返回第一页
    # 的最后一行，翻页将永不结束。有上限就变成一条失败断言，而不是挂死整个测试进程。
    while pages < 10:
        page = query.list_grades(principal=teacher, course_id=str(course), limit=2, cursor=cursor)
        pages += 1
        seen.extend(row.submission_id for row in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None, "游标分页未在上限内结束——游标可能重复返回同一批行"
    assert pages == 4
    assert len(seen) == len(set(seen)), "游标分页出现重复行"
    assert seen == [row.submission_id for row in whole.items], "游标分页漏行或行序不一致"
    assert seen[-1] == str(identifiers[-1])


def test_pagination_is_keyset_not_offset(engine: Engine, query: GradeQueryService) -> None:
    """删除已经翻过的一行后继续翻页：keyset 不错位（OFFSET 会漏掉原第 3 行）。"""
    course, _identifiers = _seed_pages(engine)
    expected = _expected_order(engine, course)
    teacher = _teacher(course)
    first = query.list_grades(principal=teacher, course_id=str(course), limit=2)
    assert [row.submission_id for row in first.items] == expected[:2]
    assert first.next_cursor is not None

    with engine.begin() as connection:
        connection.execute(
            delete(submissions).where(
                submissions.c.submission_id == uuid.UUID(expected[0])
            )
        )

    second = query.list_grades(
        principal=teacher, course_id=str(course), limit=2, cursor=first.next_cursor
    )
    # keyset：游标说的是"上一页最后一行之后"，删掉**已经翻过**的行不会移动窗口；
    # OFFSET 会整体前移一位，于是这里会返回 `expected[3:5]`（漏掉 `expected[2]`）而失败。
    assert [row.submission_id for row in second.items] == expected[2:4]


def test_invalid_cursor_and_out_of_range_limit_are_rejected(
    engine: Engine, query: GradeQueryService
) -> None:
    """坏游标 → `InvalidCursorError`（不静默回到第一页）；`limit` 越界 → `InvalidFilterError`。"""
    course = uuid.uuid4()
    teacher = _teacher(course)
    with pytest.raises(InvalidCursorError):
        query.list_grades(principal=teacher, course_id=str(course), cursor="not-a-cursor")
    with pytest.raises(InvalidCursorError):
        query.list_grades(principal=teacher, course_id=str(course), cursor="eyJ2Ijo5fQ")
    for limit in (0, 201, -1):
        with pytest.raises(InvalidFilterError, match="limit"):
            query.list_grades(principal=teacher, course_id=str(course), limit=limit)
    with pytest.raises(InvalidFilterError, match="course_id"):
        query.list_grades(principal=teacher, course_id="not-a-uuid")


def test_filters_narrow_the_export_too(
    engine: Engine, query: GradeQueryService, exporter: GradesExportService
) -> None:
    """`section_id`/`question_version_id` 过滤对查询与导出同时生效（同一 `_resolve`）。"""
    course, s1, s2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    q1, q2 = uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    wanted = _seed_submission(
        engine,
        course_id=course,
        section_id=s1,
        student_id=uuid.uuid4(),
        question_version_id=q1,
        submitted_at=BASE,
        state="validated",
    )
    _seed_submission(
        engine,
        course_id=course,
        section_id=s2,
        student_id=uuid.uuid4(),
        question_version_id=q1,
        submitted_at=BASE + timedelta(seconds=1),
        state="validated",
    )
    _seed_submission(
        engine,
        course_id=course,
        section_id=s1,
        student_id=uuid.uuid4(),
        question_version_id=q2,
        submitted_at=BASE + timedelta(seconds=2),
        state="validated",
    )
    page = query.list_grades(
        principal=teacher, course_id=str(course), section_id=str(s1), question_version_id=str(q1)
    )
    assert [row.submission_id for row in page.items] == [str(wanted)]
    _, records = _read_csv(
        exporter.export(
            principal=teacher,
            course_id=str(course),
            section_id=str(s1),
            question_version_id=str(q1),
            fmt="csv",
        ).content
    )
    assert [record[_HEADER["submission_id"]] for record in records] == [str(wanted)]


# --- 导出不泄漏报告/源码/参考答案/隐藏用例 --------------------------------------------------


def test_export_never_leaks_report_or_hidden_cases(
    engine: Engine, exporter: GradesExportService
) -> None:
    """`evaluation_results.report` 里的参考答案/隐藏用例/源码片段绝不进导出文件。"""
    course, section, question = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    teacher = _teacher(course)
    report = {
        "reference_answer": "REFERENCE_ANSWER_7f3a",
        "hidden_cases": ["HIDDEN_INPUT_9c1b"],
        "source": "SOURCE_SNIPPET_abc",
        "internal_path": "/srv/b2/artifacts/deadbeef",
    }
    submission = _seed_submission(
        engine,
        course_id=course,
        section_id=section,
        student_id=uuid.uuid4(),
        question_version_id=question,
        submitted_at=BASE,
        state="completed",
    )
    _seed_run(
        engine,
        submission_id=submission,
        question_version_id=question,
        state="completed",
        score="42",
        is_pass=False,
        report=report,
    )
    markers = [
        "REFERENCE_ANSWER_7f3a",
        "HIDDEN_INPUT_9c1b",
        "SOURCE_SNIPPET_abc",
        "/srv/b2/artifacts",
        "reference_answer",
        "hidden_cases",
    ]

    csv_payload = exporter.export(principal=teacher, course_id=str(course), fmt="csv").content
    csv_text = csv_payload.decode("utf-8-sig")
    for marker in markers:
        assert marker not in csv_text

    _, xlsx_records = _read_xlsx(
        exporter.export(principal=teacher, course_id=str(course), fmt="xlsx").content
    )
    joined = " ".join(str(value) for record in xlsx_records for value in record.values())
    for marker in markers:
        assert marker not in joined
    # 导出列恰为契约 GradeRow 的字段：报告/证据/路径没有入口可进
    assert [field for field, _ in GRADE_EXPORT_COLUMNS] == _contract_grade_row_fields()


# --- 7. 确定性产物（回归：XLSX 曾把墙钟时间写进字节） ----------------------------------------


def _core_properties(payload: bytes) -> dict[str, str | None]:
    """从产物里读出 `docProps/core.xml` 的文档时间（真的解包解析，不猜）。"""
    namespaces = {
        "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
        "dcterms": "http://purl.org/dc/terms/",
    }
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        root = ElementTree.fromstring(archive.read("docProps/core.xml"))
    return {
        name: root.findtext(f"dcterms:{name}", namespaces=namespaces)
        for name in ("created", "modified")
    }


def _zip_stamps(payload: bytes) -> set[tuple[int, ...]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {info.date_time for info in archive.infolist()}


def test_render_xlsx_is_byte_identical_for_the_same_rows_across_a_second_boundary() -> None:
    """同输入 → 逐字节相同的产物；两次渲染之间**跨过一秒**也必须相同。

    `sleep(1.05)` 是刻意的：`openpyxl` 默认把 `datetime.now(utc)` 写进 `docProps/core.xml`，并把
    `time.localtime()` 写进每个 ZIP 条目——两次调用只要跨过一秒就会产出不同字节（已实测：修复前
    同一批行 `sha256` 逐次不同）。不跨秒的"两次相等"因此是**假通过**，抓不到这个缺陷。
    """
    rows = [_synthetic_row(), _synthetic_row(submitted_at=BASE + timedelta(seconds=5))]
    first = render_xlsx(rows)
    time.sleep(1.05)
    second = render_xlsx(rows)
    assert first == second, "同一批行渲染出不同字节——产物里混进了墙钟时间"

    # 两个可疑来源逐一钉死：ZIP 条目时间戳固定；文档时间来自**行数据**
    assert _zip_stamps(first) == {(1980, 1, 1, 0, 0, 0)}
    assert len(_zip_stamps(first)) == 1
    expected_moment = "2026-09-15T08:00:05Z"  # 这批行里最新的 submitted_at，截到秒
    assert _core_properties(first) == {
        "created": expected_moment,
        "modified": expected_moment,
    }


def test_document_moment_is_derived_from_the_rows_not_from_a_constant() -> None:
    """文档时间从行数据派生：换一批更晚的行，文档时间跟着变（常量产物做不到这一点）。"""
    assert _core_properties(render_xlsx([_synthetic_row(submitted_at=BASE)]))["created"] == (
        "2026-09-15T08:00:00Z"
    )
    later = BASE + timedelta(days=3, hours=1)
    assert _core_properties(render_xlsx([_synthetic_row(submitted_at=later)]))["created"] == (
        "2026-09-18T09:00:00Z"
    )
    # 空批次（某门课还没有提交）也有确定值：固定基准，不是"现在"
    assert _core_properties(render_xlsx([]))["created"] == "1980-01-01T00:00:00Z"


def test_determinism_is_not_identity_different_rows_produce_different_bytes() -> None:
    """反向断言：确定性 ≠ 恒等。不同输入必须产出不同字节（否则上一条会被一个常量产物骗过）。"""
    row = _synthetic_row()
    baseline = render_xlsx([row])
    assert baseline != render_xlsx([replace(row, score=0.0)])
    assert baseline != render_xlsx([replace(row, state="running", score=None)])
    assert baseline != render_xlsx([replace(row, submitted_at=BASE + timedelta(seconds=1))])
    assert baseline != render_xlsx([])
    assert baseline != render_xlsx([row, row])
    csv_payload = render_csv([row])
    assert csv_payload != render_csv([replace(row, state="running", score=None)])


def test_repacked_xlsx_is_still_a_valid_workbook_with_the_same_visible_settings() -> None:
    """重打 ZIP 只改时间戳：工作表名/冻结窗格/加粗表头/时间数字格式/列宽一概不变（Excel 可打开）。"""
    rows = [_synthetic_row(), _synthetic_row(submitted_at=BASE + timedelta(seconds=1))]
    payload = render_xlsx(rows)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.testzip() is None
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
    sheet = load_workbook(io.BytesIO(payload)).active
    assert sheet.title == "成绩"
    assert sheet.freeze_panes == "A2"
    assert sheet["A1"].font.bold is True
    assert [cell.value for cell in sheet[1]] == [header for _, header in GRADE_EXPORT_COLUMNS]
    assert sheet.cell(row=2, column=12).number_format == "yyyy-mm-dd hh:mm:ss"
    # XLSX 的时间单元格是**朴素** UTC（Excel 序列号没有时区），毫秒与对外 RFC 3339 一致
    assert sheet.cell(row=2, column=12).value == BASE.replace(tzinfo=None)
    widths = [sheet.column_dimensions[letter].width for letter in ("A", "H", "M")]
    assert all(width is not None and width >= 10 for width in widths)
