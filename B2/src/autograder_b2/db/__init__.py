"""B2 数据访问层（只读写 `b2` schema）。"""

from __future__ import annotations

from autograder_b2.db.engine import (
    DATABASE_URL_ENV,
    DatabaseConfigurationError,
    create_db_engine,
    database_url_from_env,
)
from autograder_b2.db.schema import (
    SCHEMA,
    artifact_gc_audits,
    artifact_gc_runs,
    artifacts,
    email_checkpoints,
    intake_quarantines,
    metadata,
    submission_files,
    submissions,
)

__all__ = [
    "DATABASE_URL_ENV",
    "SCHEMA",
    "DatabaseConfigurationError",
    "artifact_gc_audits",
    "artifact_gc_runs",
    "artifacts",
    "create_db_engine",
    "database_url_from_env",
    "email_checkpoints",
    "intake_quarantines",
    "metadata",
    "submission_files",
    "submissions",
]
