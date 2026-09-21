"""Statistics layer: bucketing, aggregation, replay and the read model."""

from __future__ import annotations

from .aggregator import (
    Accumulator,
    RebuildReport,
    accumulate,
    accumulate_one,
    apply_event_increment,
    read_totals,
    write_absolute_totals,
)
from .buckets import BAND_LABELS, UNBUCKETED_LABEL, band_label_for_percentage, score_bucket_label
from .service import (
    StatisticsResult,
    apply_consumed_event,
    build_result,
    rebuild_statistics,
    replay_from_envelopes,
    statistics_for_versions,
)

__all__ = [
    "BAND_LABELS",
    "UNBUCKETED_LABEL",
    "Accumulator",
    "RebuildReport",
    "StatisticsResult",
    "accumulate",
    "accumulate_one",
    "apply_consumed_event",
    "apply_event_increment",
    "band_label_for_percentage",
    "build_result",
    "read_totals",
    "rebuild_statistics",
    "replay_from_envelopes",
    "score_bucket_label",
    "statistics_for_versions",
    "write_absolute_totals",
]
