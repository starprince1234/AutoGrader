"""B2 内容寻址存储层：路径派生、CAS 读写、孤儿回收。"""

from __future__ import annotations

from autograder_b2.storage.cas import (
    ArtifactContentMissingError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ContentAddressedStore,
    StoredArtifact,
)
from autograder_b2.storage.gc import GcReport, collect_orphans, referenced_artifact_ids
from autograder_b2.storage.paths import (
    InvalidDigestError,
    absolute_path_under,
    storage_path_for,
)

__all__ = [
    "ArtifactContentMissingError",
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ContentAddressedStore",
    "GcReport",
    "InvalidDigestError",
    "StoredArtifact",
    "absolute_path_under",
    "collect_orphans",
    "referenced_artifact_ids",
    "storage_path_for",
]
