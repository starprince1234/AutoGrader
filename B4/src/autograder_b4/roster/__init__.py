"""Roster import: parsing, column mapping, validation and the confirmation state machine."""

from .mapping import MappingError, propose_mapping, validate_submitted_mapping
from .parsers import (
    MEDIA_TYPE_CSV,
    MEDIA_TYPE_XLSX,
    SUPPORTED_MEDIA_TYPES,
    UnsupportedDocument,
    detect_media_type,
    parse_roster,
)
from .service import (
    STATUS_COMMITTED,
    STATUS_FAILED_PARSE,
    STATUS_MAPPING_REQUIRED,
    STATUS_PREVIEWED,
    STATUS_REJECTED,
    STATUS_UPLOADED,
    STATUS_VALIDATING,
    ConfirmResult,
    RosterImportView,
    confirm_import,
    create_import,
    load_view,
    set_mapping,
)
from .table import RosterParseError, RosterTable

__all__ = [
    "MEDIA_TYPE_CSV",
    "MEDIA_TYPE_XLSX",
    "STATUS_COMMITTED",
    "STATUS_FAILED_PARSE",
    "STATUS_MAPPING_REQUIRED",
    "STATUS_PREVIEWED",
    "STATUS_REJECTED",
    "STATUS_UPLOADED",
    "STATUS_VALIDATING",
    "SUPPORTED_MEDIA_TYPES",
    "ConfirmResult",
    "MappingError",
    "RosterImportView",
    "RosterParseError",
    "RosterTable",
    "UnsupportedDocument",
    "confirm_import",
    "create_import",
    "detect_media_type",
    "load_view",
    "parse_roster",
    "propose_mapping",
    "set_mapping",
    "validate_submitted_mapping",
]
