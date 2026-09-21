"""PDF and XLSX readers.

The accepted formats and their media types are fixed by
`MAIN/contracts/schemas/roster-import.schema.json`: only `application/pdf` and the XLSX
media type, "anything else is rejected with 422". The media type is decided from the
file's magic bytes, not from the client's `Content-Type`, because the client controls
that header.

Both readers are bounded: page, row, column and cell limits, plus a decompression-bomb
check on the XLSX zip before any cell is materialized. A roster upload is attacker
controlled input (`MAIN/docs/architecture/security.md` §2) and must not be able to
exhaust the host on its own.
"""

from __future__ import annotations

import io
import re
import zipfile

from openpyxl import load_workbook
from pypdf import PdfReader

from .mapping import _ALIAS_LOOKUP
from .table import (
    MAX_CELL_LENGTH,
    MAX_COLUMNS,
    MAX_ROWS,
    ParsedRow,
    RosterParseError,
    RosterTable,
    clean_cell,
    normalize_header,
)

MEDIA_TYPE_PDF = "application/pdf"
MEDIA_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_MEDIA_TYPES = (MEDIA_TYPE_PDF, MEDIA_TYPE_XLSX)

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"

MAX_PDF_PAGES = 50
#: Declared uncompressed size ceiling for the XLSX container (zip bomb guard).
MAX_XLSX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
CELL_DELIMITER = re.compile(r"\s{2,}|\t")


class UnsupportedDocument(ValueError):
    """The uploaded bytes are not a PDF or XLSX roster.

    A plain exception rather than a dataclass: a frozen dataclass cannot have
    `__traceback__` assigned, so raising one fails inside the exception machinery itself.
    """


def detect_media_type(filename: str, content: bytes) -> str:
    """Decide the format from content, then confirm it agrees with the filename."""
    if content.startswith(PDF_MAGIC):
        detected = MEDIA_TYPE_PDF
    elif content.startswith(ZIP_MAGIC):
        detected = MEDIA_TYPE_XLSX
    else:
        raise UnsupportedDocument(
            "Only PDF and XLSX rosters are accepted; the upload is neither "
            "(no PDF or ZIP container signature)."
        )
    lowered = (filename or "").lower()
    expected_suffix = ".pdf" if detected == MEDIA_TYPE_PDF else ".xlsx"
    if not lowered.endswith(expected_suffix):
        raise UnsupportedDocument(
            f"The upload looks like {detected} but the filename does not end in "
            f"{expected_suffix}; the filename is not used to choose a parser."
        )
    return detected


def parse_roster(content: bytes, media_type: str) -> RosterTable:
    if media_type == MEDIA_TYPE_XLSX:
        return parse_xlsx(content)
    if media_type == MEDIA_TYPE_PDF:
        return parse_pdf(content)
    raise UnsupportedDocument(f"unsupported media type: {media_type}")


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
        sheet = workbook.worksheets[0]
        header: list[str] | None = None
        rows: list[ParsedRow] = []
        blank = 0
        for index, raw_row in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = [clean_cell(value) for value in raw_row]
            while cells and cells[-1] == "":
                cells.pop()
            if header is None:
                if not any(cells):
                    continue
                if len(cells) > MAX_COLUMNS:
                    raise RosterParseError(
                        f"the roster has {len(cells)} columns; the limit is {MAX_COLUMNS}"
                    )
                header = cells
                continue
            if not any(cells):
                blank += 1
                continue
            if len(rows) >= MAX_ROWS:
                raise RosterParseError(f"the roster has more than {MAX_ROWS} rows")
            padded = cells + [""] * (len(header) - len(cells))
            rows.append(
                ParsedRow(
                    # The spreadsheet's own row index, so a rejected row can be found in
                    # the source document even when blank rows were skipped.
                    row_number=index,
                    cells={
                        column: _truncate(value)
                        for column, value in zip(header, padded, strict=False)
                    },
                )
            )
        if header is None:
            raise RosterParseError("the workbook is empty: no header row was found")
        return RosterTable(
            columns=tuple(header),
            rows=tuple(rows),
            blank_rows_excluded=blank,
            notes=_blank_note(blank),
        )
    finally:
        workbook.close()


def parse_pdf(content: bytes) -> RosterTable:
    """Extract a table from a text-based PDF roster.

    Column detection is deliberately conservative. PDF has no notion of a table, so the
    reader looks for a header line whose cells match known field aliases when split on
    two-or-more spaces (or a tab). When that fails the table comes back with no columns
    and the import stops in `mapping_required` -- which is the documented behaviour, not
    a fallback: a wrong guess here would silently mis-assign a whole column.
    """
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise RosterParseError(f"the PDF could not be opened: {type(error).__name__}") from error
    if reader.is_encrypted:
        raise RosterParseError("encrypted PDFs are not supported")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise RosterParseError(
            f"the PDF has {len(reader.pages)} pages; the limit is {MAX_PDF_PAGES}"
        )

    lines: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise RosterParseError(
                f"page text extraction failed: {type(error).__name__}"
            ) from error
        lines.extend(line.strip() for line in text.splitlines())
    if not any(lines):
        raise RosterParseError(
            "no extractable text: the PDF is probably a scan, which this pipeline does not OCR"
        )

    header_index = _find_header_line(lines)
    if header_index is None:
        return RosterTable(
            columns=(),
            rows=(),
            detail=(
                "no header line matched a known roster column; supply the column mapping explicitly"
            ),
            notes=("pdf-text-extracted", f"{len(lines)} lines scanned"),
        )
    header = CELL_DELIMITER.split(lines[header_index].strip())
    header = [clean_cell(cell) for cell in header if clean_cell(cell)]
    if len(header) < 2:
        return RosterTable(
            columns=(),
            rows=(),
            detail="the PDF header line does not separate into distinct cells",
            notes=("pdf-text-extracted",),
        )

    rows: list[ParsedRow] = []
    blank = 0
    for line in lines[header_index + 1 :]:
        if not line:
            blank += 1
            continue
        cells = [clean_cell(cell) for cell in CELL_DELIMITER.split(line)]
        if not any(cells):
            blank += 1
            continue
        if len(rows) >= MAX_ROWS:
            raise RosterParseError(f"the roster has more than {MAX_ROWS} rows")
        padded = (cells + [""] * len(header))[: len(header)]
        rows.append(
            ParsedRow(
                row_number=len(rows) + 2,
                cells={
                    column: _truncate(value) for column, value in zip(header, padded, strict=False)
                },
            )
        )
    return RosterTable(
        columns=tuple(header),
        rows=tuple(rows),
        blank_rows_excluded=blank,
        notes=("pdf-text-extracted", *_blank_note(blank)),
    )


def _find_header_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[:80]):
        if not line:
            continue
        cells = [normalize_header(cell) for cell in CELL_DELIMITER.split(line)]
        if any(cell in _ALIAS_LOOKUP for cell in cells if cell):
            return index
    return None


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
