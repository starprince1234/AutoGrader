"""Test-only document builders for the roster pipeline.

The XLSX is written by `openpyxl` and the PDF is assembled byte by byte with a correct
cross-reference table, so both are genuine documents parsed by the real readers rather
than fixtures that bypass them.
"""

from __future__ import annotations

import io

from openpyxl import Workbook

DEFAULT_HEADER = ["学号", "姓名", "课程编号", "课序号"]


def xlsx_bytes(
    rows: list[list[object]],
    *,
    header: list[str] | None = None,
    sheet_title: str = "名单",
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    if header is not None:
        sheet.append(header)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def simple_pdf_bytes(lines: list[str]) -> bytes:
    """A single-page PDF whose text is laid out in Courier at 12pt.

    Courier is monospaced, which is what lets the reader split table cells on runs of two
    or more spaces -- the same way it would for a text-based PDF produced by a word
    processor.
    """
    content = ["BT", "/F1 12 Tf", "14 TL", "40 800 Td"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        if index:
            content.append("T*")
        content.append(f"({escaped}) Tj")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(output)


def multi_page_pdf_bytes(pages: list[list[str]]) -> bytes:
    """A genuine multi-page Courier PDF for pagination/repeated-header tests."""
    page_count = len(pages)
    # Object numbering:
    # 1: Catalog, 2: Pages, 3: Font, then per page (page_obj, content_obj)
    font_obj_num = 3
    page_obj_nums = [4 + idx * 2 for idx in range(page_count)]
    kids = " ".join(f"{num} 0 R" for num in page_obj_nums).encode()
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + f"] /Count {page_count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    for page_lines in pages:
        content = ["BT", "/F1 12 Tf", "14 TL", "40 800 Td"]
        for index, line in enumerate(page_lines):
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            if index:
                content.append("T*")
            content.append(f"({escaped}) Tj")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1")
        page_num = len(objects) + 1
        content_num = page_num + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Contents {content_num} 0 R "
                f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> >>"
            ).encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(output)


PDF_HEADER = "student_number  display_name  course_code  section_code"


def roster_pdf_bytes(
    rows: list[tuple[str, str]],
    *,
    course_code: str,
    section_code: str,
    header: str = PDF_HEADER,
) -> bytes:
    lines = [header]
    for number, name in rows:
        lines.append(f"{number}  {name}  {course_code}  {section_code}")
    return simple_pdf_bytes(lines)
