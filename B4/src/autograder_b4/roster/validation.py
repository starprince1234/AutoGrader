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
    DocumentMetadata,
    RosterTable,
)

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"

VERIFICATION_MATCHED = "matched"
VERIFICATION_MISMATCHED = "mismatched"
VERIFICATION_UNAVAILABLE = "unavailable"

NUMERIC_STUDENT_NUMBER_WARNING = "学号为数值型可能丢失前导零"

MAX_STUDENT_NUMBER_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 200


@dataclass(frozen=True)
class CourseVerification:
    status: str
    file_course_code: str | None
    file_section_code: str | None
    selected_course_code: str
    selected_section_code: str
    message: str


def normalize_code(value: str, *, strip_leading_zeros: bool = False) -> str:
    folded = value.strip().casefold()
    if strip_leading_zeros and folded.isdigit():
        return folded.lstrip("0") or "0"
    return folded


def verify_course_metadata(
    metadata: DocumentMetadata | None,
    *,
    course_code: str,
    section_code: str,
) -> CourseVerification:
    if metadata is None or not metadata.course_code or not metadata.section_code:
        return CourseVerification(
            status=VERIFICATION_UNAVAILABLE,
            file_course_code=metadata.course_code if metadata else None,
            file_section_code=metadata.section_code if metadata else None,
            selected_course_code=course_code,
            selected_section_code=section_code,
            message="无法从文件核对课程信息",
        )
    file_course = normalize_code(metadata.course_code)
    file_section = normalize_code(metadata.section_code, strip_leading_zeros=True)
    target_course = normalize_code(course_code)
    target_section = normalize_code(section_code, strip_leading_zeros=True)
    if file_course == target_course and file_section == target_section:
        return CourseVerification(
            status=VERIFICATION_MATCHED,
            file_course_code=metadata.course_code,
            file_section_code=metadata.section_code,
            selected_course_code=course_code,
            selected_section_code=section_code,
            message=f"文件课程信息 ({metadata.course_code}-{metadata.section_code}) 与所选一致",
        )
    return CourseVerification(
        status=VERIFICATION_MISMATCHED,
        file_course_code=metadata.course_code,
        file_section_code=metadata.section_code,
        selected_course_code=course_code,
        selected_section_code=section_code,
        message=(
            f"文件课程信息 ({metadata.course_code}-{metadata.section_code}) "
            f"与所选 ({course_code}-{section_code}) 不一致"
        ),
    )


def collect_mapping_warnings(table: RosterTable, mapping: dict[str, str]) -> tuple[str, ...]:
    student_col = mapping.get(FIELD_STUDENT_NUMBER)
    if not student_col:
        return ()
    for row in table.rows:
        if student_col in row.numeric_columns or student_col in row.non_integer_columns:
            return (NUMERIC_STUDENT_NUMBER_WARNING,)
    return ()


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

    student_column = mapping.get(FIELD_STUDENT_NUMBER)
    if student_column and student_column in row.non_integer_columns:
        return "student number is a non-integer numeric value"
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
        if found and normalize_code(found) != normalize_code(course_code):
            return f"course code {found!r} does not match the target course {course_code!r}"
    section_column = mapping.get(FIELD_SECTION_CODE)
    if section_column:
        found = table.value(row, section_column)
        if found and normalize_code(found, strip_leading_zeros=True) != normalize_code(
            section_code, strip_leading_zeros=True
        ):
            return f"section code {found!r} does not match the target section {section_code!r}"
    return None
