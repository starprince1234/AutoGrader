"""Pure unit tests for B4 roster parsing, header location, course verification, and PII."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from autograder_b4.roster.mapping import propose_mapping
from autograder_b4.roster.parsers import (
    MAX_PDF_PAGES,
    MEDIA_TYPE_CSV,
    MEDIA_TYPE_PDF,
    MEDIA_TYPE_XLS,
    MEDIA_TYPE_XLSX,
    UnsupportedDocument,
    detect_media_type,
    parse_roster,
)
from autograder_b4.roster.table import MAX_COLUMNS, MAX_ROWS, RosterParseError
from autograder_b4.roster.validation import (
    NUMERIC_STUDENT_NUMBER_WARNING,
    VERIFICATION_MATCHED,
    VERIFICATION_MISMATCHED,
    VERIFICATION_UNAVAILABLE,
    collect_mapping_warnings,
    validate_rows,
    verify_course_metadata,
)
from tests.documents import simple_pdf_bytes, xlsx_bytes

FIXTURE_XLS = Path(__file__).parent / "fixtures" / "synthetic_course_roster.xls"

SYNTHETIC_PREAMBLE = [
    ["2026-2027学年秋选课学生名单"],
    [""],
    ["课程信息\uff1a软件工程测试\uff08双语\uff09(304064030-04)"],
    ["上课时间\uff1a周二 1-2节"],
    ["上课地点\uff1a测试楼101    任课教师\uff1a测试教师"],
    [""],
]
SYNTHETIC_HEADER = [
    "序号",
    "学号",
    "姓名",
    "性别",
    "院系",
    "专业",
    "班级",
    "QQ号",
    "Email",
    "手机号",
    "修读方式",
    "课程属性",
    "备注",
    "",
]
SYNTHETIC_DATA_ROWS = [
    [
        1, "20260001", "测试甲", "男", "计算机学院", "软件工程", "软工2601",
        "100001", "jia@example.edu", "13800000001", "正常", "必修", "", "",
    ],
    [
        2, "20260002", "测试乙", "女", "计算机学院", "软件工程", "软工2601",
        "", "yi@example.edu", "13800000002", "正常", "必修", "", "",
    ],
    [
        3, "00260003", "测试丙", "男", "计算机学院", "软件工程", "软工2602",
        "", "", "", "重修", "选修", "旁听", "",
    ],
]


def _csv_bytes_from_rows(rows: list[list[object]]) -> bytes:
    lines = [",".join("" if cell is None else str(cell) for cell in row) for row in rows]
    return "\n".join(lines).encode("utf-8")


def test_four_formats_produce_equivalent_students_and_metadata():
    xls_content = FIXTURE_XLS.read_bytes()
    combined = [*SYNTHETIC_PREAMBLE, SYNTHETIC_HEADER, *SYNTHETIC_DATA_ROWS]
    xlsx_content = xlsx_bytes(combined)
    csv_content = _csv_bytes_from_rows(combined)
    pdf_content = simple_pdf_bytes(
        [
            "2026-2027 Fall Roster",
            " ",
            "Course: Software Engineering (304064030-04)",
            "Time: Tue 1-2",
            "Room: 101    Teacher: Tester",
            " ",
            "No  student_number  display_name  gender  phone",
            "1  20260001  Alice  F  13800000001",
            "2  20260002  Bob  M  13800000002",
            "3  00260003  Carol  F  13800000003",
        ]
    )

    zh_students = [("20260001", "测试甲"), ("20260002", "测试乙"), ("00260003", "测试丙")]
    en_students = [("20260001", "Alice"), ("20260002", "Bob"), ("00260003", "Carol")]
    for filename, content, expected_media, expected_students in (
        ("roster.xls", xls_content, MEDIA_TYPE_XLS, zh_students),
        ("roster.xlsx", xlsx_content, MEDIA_TYPE_XLSX, zh_students),
        ("roster.csv", csv_content, MEDIA_TYPE_CSV, zh_students),
        ("roster.pdf", pdf_content, MEDIA_TYPE_PDF, en_students),
    ):
        media_type = detect_media_type(filename, content)
        assert media_type == expected_media
        table = parse_roster(content, media_type)
        assert table.header_row_number == 7
        proposal = propose_mapping(table)
        assert proposal.is_complete, proposal.detail
        validated = validate_rows(
            table, proposal.mapping, course_code="304064030", section_code="04"
        )
        assert [
            (item.values["student_number"], item.values["display_name"])
            for item in validated
            if item.accepted
        ] == expected_students
        # Minimal PII: validated row values contain ONLY student_number and display_name.
        for item in validated:
            assert set(item.values.keys()) == {"student_number", "display_name"}
            serialized = repr(item.values)
            for forbidden in ("13800000001", "jia@example.edu", "100001", "计算机学院", "旁听"):
                assert forbidden not in serialized


def test_course_metadata_verification_matched_mismatched_and_unavailable():
    xls_content = FIXTURE_XLS.read_bytes()
    table = parse_roster(xls_content, MEDIA_TYPE_XLS)
    assert table.metadata is not None
    assert table.metadata.course_code == "304064030"
    assert table.metadata.section_code == "04"
    assert table.metadata.course_name == "软件工程测试\uff08双语\uff09"
    assert table.metadata.instructor == "测试教师"

    # Normalized match: "04" == "4"
    matched = verify_course_metadata(table.metadata, course_code="304064030", section_code="4")
    assert matched.status == VERIFICATION_MATCHED

    # Mismatch blocks confirmation
    mismatched = verify_course_metadata(table.metadata, course_code="304064030", section_code="03")
    assert mismatched.status == VERIFICATION_MISMATCHED
    assert "304064030" in mismatched.message and "04" in mismatched.message

    # Unavailable does not block
    unavailable = verify_course_metadata(None, course_code="304064030", section_code="04")
    assert unavailable.status == VERIFICATION_UNAVAILABLE


def test_numeric_student_number_normalization_and_non_integer_rejection():
    # Fixture .xls row 8 has numeric float 20260001.0
    xls_table = parse_roster(FIXTURE_XLS.read_bytes(), MEDIA_TYPE_XLS)
    xls_proposal = propose_mapping(xls_table)
    warnings = collect_mapping_warnings(xls_table, xls_proposal.mapping)
    assert warnings == (NUMERIC_STUDENT_NUMBER_WARNING,)

    # XLSX with non-integer numeric student number -> row rejected
    xlsx_content = xlsx_bytes(
        [[20260001.5, "测试小数"], [20260002.0, "测试整数"]],
        header=["学号", "姓名"],
    )
    xlsx_table = parse_roster(xlsx_content, MEDIA_TYPE_XLSX)
    xlsx_proposal = propose_mapping(xlsx_table)
    validated = validate_rows(
        xlsx_table, xlsx_proposal.mapping, course_code="CS101", section_code="01"
    )
    assert not validated[0].accepted
    assert "non-integer" in (validated[0].reason or "")
    assert validated[1].accepted
    assert validated[1].values["student_number"] == "20260002"
    assert collect_mapping_warnings(xlsx_table, xlsx_proposal.mapping) == (
        NUMERIC_STUDENT_NUMBER_WARNING,
    )


def test_header_not_found_falls_back_to_mapping_required():
    csv_content = _csv_bytes_from_rows([["col_a", "col_b"], ["20260001", "Alice"]])
    table = parse_roster(csv_content, MEDIA_TYPE_CSV)
    proposal = propose_mapping(table)
    assert not proposal.is_complete
    assert "student_number" in proposal.missing_fields
    assert "display_name" in proposal.missing_fields


def test_duplicate_student_number_and_missing_fields_are_rejected():
    csv_content = _csv_bytes_from_rows(
        [
            ["学号", "姓名"],
            ["20260001", "张三"],
            ["20260001", "张三重复"],
            ["", "无学号"],
            ["20260002", ""],
        ]
    )
    table = parse_roster(csv_content, MEDIA_TYPE_CSV)
    proposal = propose_mapping(table)
    validated = validate_rows(table, proposal.mapping, course_code="CS101", section_code="01")
    assert validated[0].accepted
    assert not validated[1].accepted and "duplicate" in (validated[1].reason or "")
    assert not validated[2].accepted and "missing student number" in (validated[2].reason or "")
    assert not validated[3].accepted and "missing display name" in (validated[3].reason or "")


def test_spoofed_extensions_are_rejected():
    xls_bytes = FIXTURE_XLS.read_bytes()
    xlsx_data = xlsx_bytes([["20260001", "Alice"]], header=["学号", "姓名"])
    pdf_data = simple_pdf_bytes(["student_number  display_name", "20260001  Alice"])

    with pytest.raises(UnsupportedDocument):
        detect_media_type("fake.xlsx", xls_bytes)
    with pytest.raises(UnsupportedDocument):
        detect_media_type("fake.xls", xlsx_data)
    with pytest.raises(UnsupportedDocument):
        detect_media_type("fake.csv", pdf_data)
    with pytest.raises(UnsupportedDocument):
        detect_media_type("fake.pdf", b"plain text not pdf")


def test_bounds_and_pdf_security_rejections():
    # Too many columns
    wide_csv = (",".join(f"c{i}" for i in range(MAX_COLUMNS + 1)) + "\n").encode("utf-8")
    with pytest.raises(RosterParseError, match="columns"):
        parse_roster(wide_csv, MEDIA_TYPE_CSV)

    # Too many rows
    many_rows = ["学号,姓名"] + [f"2026{i:04d},Student{i}" for i in range(MAX_ROWS + 1)]
    with pytest.raises(RosterParseError, match="rows"):
        parse_roster("\n".join(many_rows).encode("utf-8"), MEDIA_TYPE_CSV)

    # Zip bomb XLSX
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.xml", b"0" * (65 * 1024 * 1024))
    with pytest.raises(RosterParseError, match="limit"):
        parse_roster(buf.getvalue(), MEDIA_TYPE_XLSX)

    # Encrypted PDF
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    enc_buf = io.BytesIO()
    writer.write(enc_buf)
    with pytest.raises(RosterParseError, match="encrypted"):
        parse_roster(enc_buf.getvalue(), MEDIA_TYPE_PDF)

    # Blank / scanned PDF (no extractable text)
    blank_writer = PdfWriter()
    blank_writer.add_blank_page(width=200, height=200)
    blank_buf = io.BytesIO()
    blank_writer.write(blank_buf)
    with pytest.raises(RosterParseError, match=r"scanned|no extractable text"):
        parse_roster(blank_buf.getvalue(), MEDIA_TYPE_PDF)

    # Too many PDF pages
    page_writer = PdfWriter()
    for _ in range(MAX_PDF_PAGES + 1):
        page_writer.add_blank_page(width=200, height=200)
    page_buf = io.BytesIO()
    page_writer.write(page_buf)
    with pytest.raises(RosterParseError, match="pages"):
        parse_roster(page_buf.getvalue(), MEDIA_TYPE_PDF)
