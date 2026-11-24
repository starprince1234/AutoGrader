"""CSV, XLSX, XLS, and text-based PDF roster readers.

The accepted formats and their media types are fixed by
`MAIN/contracts/schemas/roster-import.schema.json`: `text/csv`,
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (XLSX),
`application/vnd.ms-excel` (XLS/BIFF), and `application/pdf` (text-based PDF). Anything
else is rejected with 422. Binary formats are decided from magic bytes, not from the
client's `Content-Type` or filename alone, because the client controls those headers.

All readers are bounded: page, row, column, cell, and character limits, plus a
decompression-bomb check on XLSX containers before any cell is materialized.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import zipfile
from collections.abc import Iterable
from dataclasses import replace

import xlrd
from openpyxl import load_workbook
from pypdf import PdfReader

from .mapping import is_header_candidate
from .table import (
    MAX_CELL_LENGTH,
    MAX_COLUMNS,
    MAX_ROWS,
    DocumentMetadata,
    ParsedRow,
    RosterParseError,
    RosterTable,
    clean_cell,
    normalize_header,
)

MEDIA_TYPE_CSV = "text/csv"
MEDIA_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEDIA_TYPE_XLS = "application/vnd.ms-excel"
MEDIA_TYPE_PDF = "application/pdf"
SUPPORTED_MEDIA_TYPES = (MEDIA_TYPE_CSV, MEDIA_TYPE_XLSX, MEDIA_TYPE_XLS, MEDIA_TYPE_PDF)

ZIP_MAGIC = b"PK\x03\x04"
XLS_MAGIC = b"\xd0\xcf\x11\xe0"
PDF_MAGIC = b"%PDF-"

#: Declared uncompressed size ceiling for the XLSX container (zip bomb guard).
MAX_XLSX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
#: Maximum rows scanned from the top of a document when locating the header row.
HEADER_SCAN_LIMIT = 30
#: Bounds for text-based PDF extraction.
MAX_PDF_PAGES = 100
MAX_PDF_CHARS = 500_000

CSV_SUFFIX = ".csv"

_COURSE_INFO_RE = re.compile(
    r"课程信息[\uff1a:]\s*(.+?)\s*[(\uff08]\s*([A-Za-z0-9_-]+?)\s*-\s*([A-Za-z0-9_-]+?)\s*[)\uff09]"
)
_SEMESTER_RE = re.compile(r"(\d{4}-\d{4}学年[^\s选名]*)")
_INSTRUCTOR_RE = re.compile(r"任课教师[\uff1a:]\s*([^\s]+(?:\s+[^\s]+)*?)(?:\s{2,}|上课|$)")
_PDF_COL_SPLIT_RE = re.compile(r"\t+|\s{2,}")
_PDF_PAGE_FOOTER_RE = re.compile(
    r"^(?:第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?|page\s+\d+(?:\s*(?:/|of)\s*\d+)?|\d+\s*/\s*\d+)$",
    re.IGNORECASE,
)
_PDF_METADATA_LINE_RE = re.compile(
    r"^(?:(?:课程信息|上课时间|上课地点|任课教师)[\uff1a:]|\d{4}-\d{4}学年[^\n]*名单)"
)
_PDF_STUDENT_NUMBER_HINT_RE = re.compile(r"\d{8,}")


class UnsupportedDocument(ValueError):
    """The uploaded bytes are not a supported CSV, XLSX, XLS, or PDF roster."""


def detect_media_type(filename: str, content: bytes) -> str:
    """Decide the format from magic bytes first, rejecting spoofed extensions."""
    lowered = (filename or "").strip().lower()
    if content.startswith(ZIP_MAGIC):
        if lowered and not lowered.endswith(".xlsx"):
            raise UnsupportedDocument(
                "The upload is a zip/XLSX container but the filename does not end in .xlsx."
            )
        return MEDIA_TYPE_XLSX
    if content.startswith(XLS_MAGIC):
        if lowered and not lowered.endswith(".xls"):
            raise UnsupportedDocument(
                "The upload is an OLE2/XLS workbook but the filename does not end in .xls."
            )
        return MEDIA_TYPE_XLS
    if content.startswith(PDF_MAGIC):
        if lowered and not lowered.endswith(".pdf"):
            raise UnsupportedDocument(
                "The upload is a PDF document but the filename does not end in .pdf."
            )
        return MEDIA_TYPE_PDF
    if lowered.endswith((".xlsx", ".xls", ".pdf")):
        raise UnsupportedDocument(
            "The file content does not match the expected binary signature for its extension."
        )
    if lowered.endswith(CSV_SUFFIX):
        return MEDIA_TYPE_CSV
    raise UnsupportedDocument(
        "Only CSV, XLSX, XLS, and PDF rosters are accepted; unsupported file signature."
    )


def parse_roster(content: bytes, media_type: str) -> RosterTable:
    if media_type == MEDIA_TYPE_XLSX:
        return parse_xlsx(content)
    if media_type == MEDIA_TYPE_XLS:
        return parse_xls(content)
    if media_type == MEDIA_TYPE_CSV:
        return parse_csv(content)
    if media_type == MEDIA_TYPE_PDF:
        return parse_pdf(content)
    raise UnsupportedDocument(f"unsupported media type: {media_type}")


def _decode_csv(content: bytes) -> str:
    """UTF-8 (optionally with BOM) first, then GB18030 - Chinese exports are either."""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RosterParseError("the CSV is not decodable as UTF-8 or GB18030")


def _extract_document_metadata(preamble_rows: list[list[str]]) -> DocumentMetadata | None:
    """Extract only non-PII course metadata from rows above the detected header."""
    course_name: str | None = None
    course_code: str | None = None
    section_code: str | None = None
    semester: str | None = None
    instructor: str | None = None

    for cells in preamble_rows:
        line = " ".join(cell for cell in cells if cell).strip()
        if not line:
            continue
        if course_code is None:
            match = _COURSE_INFO_RE.search(line)
            if match:
                course_name = match.group(1).strip() or None
                course_code = match.group(2).strip() or None
                section_code = match.group(3).strip() or None
        if semester is None:
            sem_match = _SEMESTER_RE.search(line)
            if sem_match:
                semester = sem_match.group(1).strip() or None
        if instructor is None:
            inst_match = _INSTRUCTOR_RE.search(line)
            if inst_match:
                instructor = inst_match.group(1).strip() or None

    if not any((course_name, course_code, section_code, semester, instructor)):
        return None
    return DocumentMetadata(
        course_name=course_name,
        course_code=course_code,
        section_code=section_code,
        semester=semester,
        instructor=instructor,
    )


def _trim_row(cells: list[str]) -> list[str]:
    trimmed = list(cells)
    while trimmed and trimmed[-1] == "":
        trimmed.pop()
    return trimmed


def _build_table_from_rows(
    raw_rows: Iterable[tuple[int, list[str], set[int], set[int]]],
) -> RosterTable:
    """Locate the header row within the first HEADER_SCAN_LIMIT rows and build RosterTable."""
    buffered: list[tuple[int, list[str], set[int], set[int]]] = []
    header: list[str] | None = None
    header_row_number: int | None = None
    preamble: list[list[str]] = []
    rows: list[ParsedRow] = []
    blank = 0
    notes: list[str] = []

    def _append_data_row(
        row_number: int,
        cells: list[str],
        num_idx: set[int],
        non_int_idx: set[int],
    ) -> None:
        nonlocal blank
        assert header is not None
        if not any(cells):
            blank += 1
            return
        if len(rows) >= MAX_ROWS:
            raise RosterParseError(f"the roster has more than {MAX_ROWS} rows")
        padded = cells + [""] * max(0, len(header) - len(cells))
        row_cells = {
            column: _truncate(value)
            for column, value in zip(header, padded, strict=False)
        }
        numeric_cols = frozenset(
            header[i] for i in num_idx if 0 <= i < len(header) and header[i]
        )
        non_int_cols = frozenset(
            header[i] for i in non_int_idx if 0 <= i < len(header) and header[i]
        )
        rows.append(
            ParsedRow(
                row_number=row_number,
                cells=row_cells,
                numeric_columns=numeric_cols,
                non_integer_columns=non_int_cols,
            )
        )

    for row_number, raw_cells, num_idx, non_int_idx in raw_rows:
        cells = _trim_row(raw_cells)
        if header is None:
            if not any(cells):
                if len(buffered) < HEADER_SCAN_LIMIT:
                    buffered.append((row_number, cells, num_idx, non_int_idx))
                continue
            if len(buffered) < HEADER_SCAN_LIMIT:
                if is_header_candidate(cells):
                    if len(cells) > MAX_COLUMNS:
                        raise RosterParseError(
                            f"the roster has {len(cells)} columns; the limit is {MAX_COLUMNS}"
                        )
                    header = cells
                    header_row_number = row_number
                    preamble = [b_cells for _, b_cells, _, _ in buffered if any(b_cells)]
                    continue
                buffered.append((row_number, cells, num_idx, non_int_idx))
                continue
            # Reached scan limit without finding both required aliases: fall back to first
            # non-empty row so the import stops in `mapping_required` for manual mapping.
            first_non_empty = next(
                (item for item in buffered if any(item[1])),
                (row_number, cells, num_idx, non_int_idx),
            )
            fb_num, fb_cells, _, _ = first_non_empty
            if len(fb_cells) > MAX_COLUMNS:
                raise RosterParseError(
                    f"the roster has {len(fb_cells)} columns; the limit is {MAX_COLUMNS}"
                )
            header = fb_cells
            header_row_number = fb_num
            notes.append(
                f"no header row with both student number and name found in first "
                f"{HEADER_SCAN_LIMIT} rows; manual column mapping required"
            )
            passed = False
            for b_num, b_cells, b_ni, b_nii in buffered:
                if not passed:
                    if b_num == fb_num:
                        passed = True
                    continue
                _append_data_row(b_num, b_cells, b_ni, b_nii)
            if first_non_empty[0] != row_number:
                _append_data_row(row_number, cells, num_idx, non_int_idx)
        else:
            _append_data_row(row_number, cells, num_idx, non_int_idx)

    if header is None:
        non_empty_buffered = [item for item in buffered if any(item[1])]
        if not non_empty_buffered:
            raise RosterParseError("the roster is empty: no header row was found")
        fb_num, fb_cells, _, _ = non_empty_buffered[0]
        if len(fb_cells) > MAX_COLUMNS:
            raise RosterParseError(
                f"the roster has {len(fb_cells)} columns; the limit is {MAX_COLUMNS}"
            )
        header = fb_cells
        header_row_number = fb_num
        notes.append(
            f"no header row with both student number and name found in first "
            f"{HEADER_SCAN_LIMIT} rows; manual column mapping required"
        )
        for b_num, b_cells, b_ni, b_nii in non_empty_buffered[1:]:
            _append_data_row(b_num, b_cells, b_ni, b_nii)

    metadata = _extract_document_metadata(preamble)
    all_notes = tuple(notes) + _blank_note(blank)
    return RosterTable(
        columns=tuple(header),
        rows=tuple(rows),
        blank_rows_excluded=blank,
        notes=all_notes,
        header_row_number=header_row_number,
        metadata=metadata,
    )


def parse_csv(content: bytes) -> RosterTable:
    """Read a CSV roster and locate the header row within the leading rows."""
    reader = csv.reader(io.StringIO(_decode_csv(content)))

    def _iter_rows() -> Iterable[tuple[int, list[str], set[int], set[int]]]:
        for index, raw in enumerate(reader, start=1):
            yield index, [clean_cell(value) for value in raw], set(), set()

    return _build_table_from_rows(_iter_rows())


def _classify_python_cells(
    raw_row: tuple[object, ...],
) -> tuple[list[str], set[int], set[int]]:
    cells: list[str] = []
    num_idx: set[int] = set()
    non_int_idx: set[int] = set()
    for idx, value in enumerate(raw_row):
        if isinstance(value, float) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise RosterParseError("the workbook contains a non-finite numeric cell")
            if value.is_integer():
                num_idx.add(idx)
            else:
                non_int_idx.add(idx)
        elif isinstance(value, int) and not isinstance(value, bool):
            num_idx.add(idx)
        cells.append(clean_cell(value))
    return cells, num_idx, non_int_idx


def parse_xlsx(content: bytes) -> RosterTable:
    """Read the first worksheet of an XLSX roster."""
    _assert_not_zip_bomb(content)
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as error:
        raise RosterParseError(
            f"the XLSX workbook could not be opened: {type(error).__name__}"
        ) from error
    try:
        if not workbook.worksheets:
            raise RosterParseError("the XLSX workbook has no worksheets")
        sheet = workbook.worksheets[0]

        def _iter_rows() -> Iterable[tuple[int, list[str], set[int], set[int]]]:
            for index, raw_row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells, num_idx, non_int_idx = _classify_python_cells(raw_row)
                yield index, cells, num_idx, non_int_idx

        return _build_table_from_rows(_iter_rows())
    finally:
        workbook.close()


def _xls_cell_to_text(
    cell: xlrd.sheet.Cell,
    datemode: int,
) -> tuple[str, bool, bool]:
    """Return (text, is_numeric_int, is_numeric_non_int) for an xlrd cell."""
    ctype = cell.ctype
    if ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return "", False, False
    if ctype == xlrd.XL_CELL_NUMBER:
        value = float(cell.value)
        if not math.isfinite(value):
            raise RosterParseError("the XLS workbook contains a non-finite numeric cell")
        if value.is_integer():
            return str(int(value)), True, False
        return str(value), False, True
    if ctype == xlrd.XL_CELL_BOOLEAN:
        return ("true" if cell.value else "false"), False, False
    if ctype == xlrd.XL_CELL_DATE:
        try:
            dt_val = xlrd.xldate_as_datetime(cell.value, datemode)
            if isinstance(dt_val, dt.datetime):
                return dt_val.date().isoformat(), False, False
        except (xlrd.XLDateError, ValueError, TypeError):
            return clean_cell(cell.value), False, False
        return clean_cell(cell.value), False, False
    if ctype == xlrd.XL_CELL_ERROR:
        return "", False, False
    return clean_cell(cell.value), False, False


def parse_xls(content: bytes) -> RosterTable:
    """Read the first worksheet of an OLE2/BIFF (.xls) roster using xlrd."""
    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    except Exception as error:
        raise RosterParseError(
            f"the XLS workbook could not be opened: {type(error).__name__}"
        ) from error
    try:
        if workbook.nsheets == 0:
            raise RosterParseError("the XLS workbook has no worksheets")
        sheet = workbook.sheet_by_index(0)

        def _iter_rows() -> Iterable[tuple[int, list[str], set[int], set[int]]]:
            for r_idx in range(sheet.nrows):
                cells: list[str] = []
                num_idx: set[int] = set()
                non_int_idx: set[int] = set()
                for c_idx in range(sheet.ncols):
                    cell = sheet.cell(r_idx, c_idx)
                    text, is_num_int, is_num_non_int = _xls_cell_to_text(
                        cell, workbook.datemode
                    )
                    if is_num_int:
                        num_idx.add(c_idx)
                    elif is_num_non_int:
                        non_int_idx.add(c_idx)
                    cells.append(text)
                yield r_idx + 1, cells, num_idx, non_int_idx

        return _build_table_from_rows(_iter_rows())
    finally:
        workbook.release_resources()


def parse_pdf(content: bytes) -> RosterTable:
    """Read a text-based PDF roster using pypdf layout extraction."""
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise RosterParseError(
            f"the PDF document could not be opened: {type(error).__name__}"
        ) from error

    if reader.is_encrypted:
        raise RosterParseError("the PDF is encrypted and cannot be read")

    page_count = len(reader.pages)
    if page_count == 0:
        raise RosterParseError("the PDF document has no pages")
    if page_count > MAX_PDF_PAGES:
        raise RosterParseError(
            f"the PDF has {page_count} pages; the limit is {MAX_PDF_PAGES}"
        )

    lines: list[str] = []
    total_chars = 0
    for page in reader.pages:
        if "/Contents" not in page:
            continue
        try:
            text = page.extract_text(extraction_mode="layout")
        except (TypeError, KeyError):
            text = page.extract_text()
        except Exception as error:
            raise RosterParseError(
                f"the PDF text could not be extracted: {type(error).__name__}"
            ) from error
        text = text or ""
        total_chars += len(text)
        if total_chars > MAX_PDF_CHARS:
            raise RosterParseError(
                f"the PDF text exceeds the {MAX_PDF_CHARS} character limit"
            )
        for line in text.splitlines():
            lines.append(line.strip())

    if not any(lines):
        raise RosterParseError(
            "the PDF contains no extractable text (scanned or image-only PDFs are not supported)"
        )

    detected_norm_header: list[str] | None = None
    extra_metadata_rows: list[list[str]] = []
    pre_header_seen = 0

    def _norm_cells(cells: list[str]) -> list[str]:
        return [normalize_header(cell) for cell in _trim_row(cells)]

    def _is_repeated_header(cells: list[str], target_norm: list[str]) -> bool:
        trimmed = _trim_row(cells)
        if len(trimmed) < 2:
            return False
        norm = [normalize_header(cell) for cell in trimmed]
        if norm == target_norm:
            return True
        non_empty = {cell for cell in norm if cell}
        return bool(non_empty) and non_empty <= set(target_norm) and is_header_candidate(trimmed)

    def _iter_rows() -> Iterable[tuple[int, list[str], set[int], set[int]]]:
        nonlocal detected_norm_header, pre_header_seen
        for index, line in enumerate(lines, start=1):
            if not line:
                if detected_norm_header is None:
                    pre_header_seen += 1
                yield index, [], set(), set()
                continue
            if _PDF_PAGE_FOOTER_RE.match(line):
                continue

            parts = [clean_cell(part) for part in _PDF_COL_SPLIT_RE.split(line)]
            if detected_norm_header is None:
                if pre_header_seen < HEADER_SCAN_LIMIT:
                    if len(parts) == 1 and not _PDF_METADATA_LINE_RE.match(line):
                        ws_parts = [clean_cell(part) for part in line.split()]
                        if len(ws_parts) >= 2 and is_header_candidate(ws_parts):
                            parts = ws_parts
                    if is_header_candidate(parts):
                        detected_norm_header = _norm_cells(parts)
                    pre_header_seen += 1
                yield index, parts, set(), set()
                continue

            if _PDF_METADATA_LINE_RE.match(line):
                extra_metadata_rows.append([line])
                continue
            if _is_repeated_header(parts, detected_norm_header):
                continue
            if len(parts) == 1:
                ws_parts = [clean_cell(part) for part in line.split()]
                if _is_repeated_header(ws_parts, detected_norm_header):
                    continue
                if len(ws_parts) >= 2 and (
                    len(ws_parts) >= min(len(detected_norm_header), 3)
                    or any(_PDF_STUDENT_NUMBER_HINT_RE.search(token) for token in ws_parts)
                ):
                    parts = ws_parts
                elif len(detected_norm_header) >= 3 and not _PDF_STUDENT_NUMBER_HINT_RE.search(
                    parts[0]
                ):
                    continue
            yield index, parts, set(), set()

    table = _build_table_from_rows(_iter_rows())
    if extra_metadata_rows:
        merged = _merge_metadata(table.metadata, _extract_document_metadata(extra_metadata_rows))
        if merged != table.metadata:
            table = replace(table, metadata=merged)
    return table


def _merge_metadata(
    primary: DocumentMetadata | None, secondary: DocumentMetadata | None
) -> DocumentMetadata | None:
    if primary is None:
        return secondary
    if secondary is None:
        return primary
    return DocumentMetadata(
        course_name=primary.course_name or secondary.course_name,
        course_code=primary.course_code or secondary.course_code,
        section_code=primary.section_code or secondary.section_code,
        semester=primary.semester or secondary.semester,
        instructor=primary.instructor or secondary.instructor,
    )


def _blank_note(blank: int) -> tuple[str, ...]:
    # Reported rather than dropped in silence: "no roster row is discarded without a
    # trace" applies to spacing rows too.
    return (f"{blank} blank row(s) ignored",) if blank else ()


def _truncate(value: str) -> str:
    return value[:MAX_CELL_LENGTH]


def _assert_not_zip_bomb(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            declared = sum(entry.file_size for entry in archive.infolist())
    except zipfile.BadZipFile as error:
        raise RosterParseError("the XLSX container is not a readable zip archive") from error
    if declared > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise RosterParseError(
            f"the XLSX expands to {declared} bytes, above the "
            f"{MAX_XLSX_UNCOMPRESSED_BYTES} byte limit; refusing to decompress it"
        )
