"""Domain layer: question aggregation, versions, uploads, assignments, statistics.

Dependency direction is one-way (``docs/architecture/overview.md``): ``api`` may
import ``domain``, and ``domain`` may import ``compile``/``stats``/``infra``, never
the reverse.
"""

from __future__ import annotations

from .assignments import create_assignment, list_assignment_versions
from .assets import delete_fixture, upload_fixture, upload_stem_asset
from .assistance import authoring_assistance
from .attestations import record_reference_attestation
from .catalog import (
    TagCoverage,
    get_question,
    get_question_with_versions,
    get_version,
    list_questions,
    list_stem_assets,
    load_asset_for_delivery,
    question_tag_coverage,
)
from .diagnostics import question_diagnostics
from .outbox import DrainReport, drain_outbox
from .packages import load_evaluation_package, load_reference_answer
from .questions import create_question, update_question
from .statistics import question_statistics
from .versions import archive_version, delete_version, publish_version

__all__ = [
    "DrainReport",
    "TagCoverage",
    "archive_version",
    "authoring_assistance",
    "create_assignment",
    "create_question",
    "delete_fixture",
    "delete_version",
    "drain_outbox",
    "get_question",
    "get_question_with_versions",
    "get_version",
    "list_assignment_versions",
    "list_questions",
    "list_stem_assets",
    "load_asset_for_delivery",
    "load_evaluation_package",
    "load_reference_answer",
    "publish_version",
    "question_diagnostics",
    "question_statistics",
    "question_tag_coverage",
    "record_reference_attestation",
    "update_question",
    "upload_fixture",
    "upload_stem_asset",
]
