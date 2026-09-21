"""Per-row validation.

Every row produces a verdict. A row that fails is recorded with its row number and a
reason and appears in the preview's `rejected_rows`; nothing is dropped, because a
silently discarded student is indistinguishable from a student who was never on the
roster, and that difference matters at grading time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .table import (
    FIELD_COURSE_CODE,
    FIELD_DISPLAY_NAME,
    FIELD_SECTION_CODE,
    FIELD_STUDENT_NUMBER,
    RosterTable,
)

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"

MAX_STUDENT_NUMBER_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 200


@dataclass(frozen=True)
class ValidatedRow:
    row_number: int
    status: str
    values: dict[str, str]
    reason: str | None

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED


def validate_rows(
    table: RosterTable,
    mapping: dict[str, str],
    *,
    course_code: str,
    section_code: str,
) -> list[ValidatedRow]:
    """Validate every row against the mapped columns and the target section."""
    validated: list[ValidatedRow] = []
    seen: dict[str, int] = {}
    for row in table.rows:
        values = {
            field: (table.value(row, column) if (column := mapping.get(field)) else "")
            for field in (FIELD_STUDENT_NUMBER, FIELD_DISPLAY_NAME)
        }
        reason = _row_reason(
            table, row, mapping, values, seen, course_code=course_code, section_code=section_code
        )
        if reason is None:
            seen[values[FIELD_STUDENT_NUMBER]] = row.row_number
            validated.append(ValidatedRow(row.row_number, STATUS_ACCEPTED, values, None))
        else:
            validated.append(ValidatedRow(row.row_number, STATUS_REJECTED, values, reason))
    return validated


def _row_reason(
    table: RosterTable,
    row,
    mapping: dict[str, str],
    values: dict[str, str],
    seen: dict[str, int],
    *,
    course_code: str,
    section_code: str,
) -> str | None:
    student_number = values[FIELD_STUDENT_NUMBER]
    display_name = values[FIELD_DISPLAY_NAME]

    if not student_number:
        return "missing student number"
    if len(student_number) > MAX_STUDENT_NUMBER_LENGTH:
        return f"student number longer than {MAX_STUDENT_NUMBER_LENGTH} characters"
    if not display_name:
        return "missing display name"
    if len(display_name) > MAX_DISPLAY_NAME_LENGTH:
        return f"display name longer than {MAX_DISPLAY_NAME_LENGTH} characters"
    if student_number in seen:
        return f"duplicate student number, first seen on row {seen[student_number]}"

    course_column = mapping.get(FIELD_COURSE_CODE)
    if course_column:
        found = table.value(row, course_column)
        if found and found.strip().casefold() != course_code.strip().casefold():
            return f"course code {found!r} does not match the target course {course_code!r}"
    section_column = mapping.get(FIELD_SECTION_CODE)
    if section_column:
        found = table.value(row, section_column)
        if found and found.strip().casefold() != section_code.strip().casefold():
            return f"section code {found!r} does not match the target section {section_code!r}"
    return None
