"""Column mapping: propose, never guess.

`MAIN/contracts/openapi/b4.yaml` states the rule for `createRosterImport`: "Parsing never
guesses: when a column cannot be mapped unambiguously the import stops in
`mapping_required` and waits for a human decision."

So a target field is proposed only when **exactly one** source header matches its alias
set. Zero matches and multiple matches both leave the field unset, and the importer
reports which fields it could not decide rather than picking the first candidate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .table import (
    FIELD_COURSE_CODE,
    FIELD_DISPLAY_NAME,
    FIELD_SECTION_CODE,
    FIELD_STUDENT_NUMBER,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    TARGET_FIELDS,
    RosterTable,
    normalize_header,
)

#: Alias sets are intentionally short and exact. A long synonym list is how "never
#: guesses" quietly becomes "usually guesses right".
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    FIELD_STUDENT_NUMBER: (
        "学号",
        "学生学号",
        "学籍号",
        "studentnumber",
        "studentno",
        "studentid",
        "sno",
        "number",
    ),
    FIELD_DISPLAY_NAME: (
        "姓名",
        "学生姓名",
        "名字",
        "displayname",
        "studentname",
        "name",
    ),
    FIELD_COURSE_CODE: ("课程编号", "课程代码", "课程号", "coursecode", "course"),
    FIELD_SECTION_CODE: ("课序号", "教学班", "班号", "sectioncode", "section", "class"),
}

_ALIAS_LOOKUP: dict[str, str] = {
    normalize_header(alias): field for field, aliases in HEADER_ALIASES.items() for alias in aliases
}


@dataclass(frozen=True)
class MappingProposal:
    """What the parser could decide on its own, and what it could not."""

    mapping: dict[str, str]
    ambiguous_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.ambiguous_fields and not self.missing_fields

    @property
    def detail(self) -> str:
        parts = []
        if self.missing_fields:
            parts.append(f"no column matched: {', '.join(self.missing_fields)}")
        if self.ambiguous_fields:
            parts.append(f"several columns matched: {', '.join(self.ambiguous_fields)}")
        return "; ".join(parts)


def propose_mapping(table: RosterTable) -> MappingProposal:
    """Match headers by exact folded alias, and only when the match is unique."""
    candidates: dict[str, list[str]] = {field: [] for field in TARGET_FIELDS}
    for column in table.columns:
        field = _ALIAS_LOOKUP.get(normalize_header(column))
        if field is not None:
            candidates[field].append(column)

    mapping: dict[str, str] = {}
    ambiguous: list[str] = []
    missing: list[str] = []
    for field in TARGET_FIELDS:
        matches = candidates[field]
        if len(matches) == 1:
            mapping[field] = matches[0]
        elif len(matches) > 1:
            ambiguous.append(field)
        elif field in REQUIRED_FIELDS:
            missing.append(field)
    return MappingProposal(
        mapping=mapping,
        ambiguous_fields=tuple(ambiguous),
        missing_fields=tuple(missing),
    )


class MappingError(ValueError):
    """A submitted mapping is not usable as given."""


def validate_submitted_mapping(mapping: dict[str, str], table: RosterTable) -> dict[str, str]:
    """Check a human-supplied mapping against the columns the document actually has.

    Rejecting an unknown field name, an unknown column and a duplicate assignment keeps
    the recorded mapping honest: it is what was applied, not a near-miss of it.
    """
    if not isinstance(mapping, dict):
        raise MappingError("columns must be an object mapping field names to column headers")
    unknown_fields = sorted(set(mapping) - set(TARGET_FIELDS))
    if unknown_fields:
        raise MappingError(f"unknown target field(s): {', '.join(unknown_fields)}")
    missing_required = [
        field for field in REQUIRED_FIELDS if not str(mapping.get(field, "")).strip()
    ]
    if missing_required:
        raise MappingError(f"required field(s) not mapped: {', '.join(missing_required)}")
    known = set(table.columns)
    used: dict[str, str] = {}
    for field, column in mapping.items():
        name = str(column).strip()
        if not name:
            raise MappingError(f"field {field} is mapped to an empty column header")
        if name not in known:
            raise MappingError(f"column {name!r} mapped to {field} is not present in the document")
        if name in used:
            raise MappingError(f"column {name!r} is mapped to both {used[name]} and {field}")
        used[name] = field
    return {field: str(column).strip() for field, column in mapping.items()}


def mapping_is_usable(mapping: dict[str, str]) -> bool:
    return all(str(mapping.get(field, "")).strip() for field in REQUIRED_FIELDS)


def optional_fields_present(mapping: dict[str, str]) -> tuple[str, ...]:
    return tuple(field for field in OPTIONAL_FIELDS if str(mapping.get(field, "")).strip())
