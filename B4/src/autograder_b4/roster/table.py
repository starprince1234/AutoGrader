"""The intermediate representation of a parsed roster, shared by the CSV and XLSX readers.

Both readers must produce the same shape so that mapping, validation, previewing and
committing are format-independent. Row numbers are 1-based positions inside the detected
table, counting the header, so a rejected row can be pointed at in the source document
without the operator re-counting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Fields the pipeline can fill from a roster column. The names match
#: `MAIN/contracts/schemas/roster-import.schema.json`'s `$comment` mapping
#: (姓名 -> display_name, 学号 -> student_number, 课程编号 -> course_code,
#: 课序号 -> section_code).
FIELD_DISPLAY_NAME = "display_name"
FIELD_STUDENT_NUMBER = "student_number"
FIELD_COURSE_CODE = "course_code"
FIELD_SECTION_CODE = "section_code"

#: Without these two a roster is not usable, so an unmapped required field stops the
#: import for a human decision instead of guessing.
REQUIRED_FIELDS = (FIELD_STUDENT_NUMBER, FIELD_DISPLAY_NAME)
OPTIONAL_FIELDS = (FIELD_COURSE_CODE, FIELD_SECTION_CODE)
TARGET_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

MAX_COLUMNS = 64
MAX_ROWS = 5000
MAX_CELL_LENGTH = 200

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedRow:
    """One data row, keyed by the source header text."""

    row_number: int
    cells: dict[str, str]


@dataclass(frozen=True)
class RosterTable:
    """A parsed roster before any column has been mapped."""

    columns: tuple[str, ...]
    rows: tuple[ParsedRow, ...]
    blank_rows_excluded: int = 0
    detail: str = ""
    #: Human-readable notes about the parse that are useful for an operator but are not
    #: validation verdicts (for example "3 trailing blank rows ignored").
    notes: tuple[str, ...] = field(default=())

    def value(self, row: ParsedRow, column: str) -> str:
        return row.cells.get(column, "")


class RosterParseError(RuntimeError):
    """The document could not be read at all (as opposed to "columns are ambiguous")."""


def normalize_header(text: str) -> str:
    """Fold a header for comparison: case, whitespace and decorative punctuation.

    Never used to *invent* a mapping -- normalization only widens exact equality, it does
    not add synonyms. If two distinct headers fold to the same alias the mapping stays
    unset, because picking one would be a guess.
    """
    folded = _WHITESPACE.sub("", str(text or "")).strip().lower()
    # Full-width Chinese punctuation and word separators are stripped deliberately: they
    # are what a spreadsheet header copied out of a course document actually contains, and
    # folding `student_number` onto the same key as `student number` is what keeps a
    # duplicate pair *ambiguous* rather than arbitrarily resolved.
    for character in (
        "*",
        ":",
        "\uff1a",
        "\uff08",
        "\uff09",
        "(",
        ")",
        "[",
        "]",
        "_",
        "-",
        "\u3000",
    ):
        folded = folded.replace(character, "")
    return folded


def clean_cell(value: object) -> str:
    """Convert a raw cell to trimmed text, refusing to coerce unexpected structures."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        # Spreadsheets store student numbers as numbers; 20230001.0 must read as "20230001".
        return str(int(value))
    if isinstance(value, (int, str)):
        return _WHITESPACE.sub(" ", str(value)).strip()
    return _WHITESPACE.sub(" ", str(value)).strip()
