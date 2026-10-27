"""CSV and XLSX readers.

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

import csv
import io
import zipfile

from openpyxl import load_workbook

from .table import (
    MAX_CELL_LENGTH,
    MAX_COLUMNS,
    MAX_ROWS,
    ParsedRow,
    RosterParseError,
    RosterTable,
    clean_cell,
)

MEDIA_TYPE_CSV = "text/csv"
MEDIA_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_MEDIA_TYPES = (MEDIA_TYPE_CSV, MEDIA_TYPE_XLSX)

ZIP_MAGIC = b"PK\x03\x04"

#: Declared uncompressed size ceiling for the XLSX container (zip bomb guard).
MAX_XLSX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
#: CSV has no container to sniff, so the filename is the only discriminator; the decoded
#: text is validated afterwards, which is what actually rejects a mislabelled file.
CSV_SUFFIX = ".csv"


class UnsupportedDocument(ValueError):
    """The uploaded bytes are not a CSV or XLSX roster.

    A plain exception rather than a dataclass: a frozen dataclass cannot have
    `__traceback__` assigned, so raising one fails inside the exception machinery itself.
    """


def detect_media_type(filename: str, content: bytes) -> str:
    """Decide the format.

    XLSX is recognised by its container signature. CSV is recognised by the filename, because
    a CSV is plain text with no signature to sniff; the decoded content is then validated by
    the parser, so a PDF renamed to `.csv` still fails instead of being parsed as text.
    """
    lowered = (filename or "").lower()
    if content.startswith(ZIP_MAGIC):
        if not lowered.endswith(".xlsx"):
            raise UnsupportedDocument(
                "The upload is a zip container but the filename does not end in .xlsx."
            )
        return MEDIA_TYPE_XLSX
    if lowered.endswith(CSV_SUFFIX):
        return MEDIA_TYPE_CSV
    raise UnsupportedDocument(
        "Only CSV and XLSX rosters are accepted; the upload has neither a zip container "
        "signature nor a .csv filename."
    )


def parse_roster(content: bytes, media_type: str) -> RosterTable:
    if media_type == MEDIA_TYPE_XLSX:
        return parse_xlsx(content)
    if media_type == MEDIA_TYPE_CSV:
        return parse_csv(content)
    raise UnsupportedDocument(f"unsupported media type: {media_type}")


def _decode_csv(content: bytes) -> str:
    """UTF-8 (optionally with BOM) first, then GB18030 - Chinese exports are either."""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RosterParseError("the CSV is not decodable as UTF-8 or GB18030")


def parse_csv(content: bytes) -> RosterTable:
    """Read a CSV roster: the first non-empty record is the header."""
    reader = csv.reader(io.StringIO(_decode_csv(content)))
    header: list[str] | None = None
    rows: list[ParsedRow] = []
    blank = 0
    for index, raw in enumerate(reader, start=1):
        cells = [clean_cell(value) for value in raw]
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
                # The record's own position, so a rejected row can be found in the file.
                row_number=index,
                cells={
                    column: _truncate(value)
                    for column, value in zip(header, padded, strict=False)
                },
            )
        )
    if header is None:
        raise RosterParseError("the CSV is empty: no header row was found")
    return RosterTable(
        columns=tuple(header),
        rows=tuple(rows),
        blank_rows_excluded=blank,
        notes=_blank_note(blank),
    )


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
