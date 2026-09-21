"""Score buckets for the de-identified distribution (``RQ-B-3-30``).

The bucketing is pure aggregation of a score B2 already computed: B3 never grades
anything and never derives a pass/fail verdict from a score (that would be
computing a score, which B3 is forbidden to do).

* an explicit ``score_bucket`` on the event wins;
* otherwise the bucket is the score as a percentage of ``max_score``, in 20-point
  bands - exact for percentages, and stable regardless of event order;
* when ``max_score`` is missing (the official example omits it) the attempt is
  counted in ``unbucketed_attempt_count`` instead of being guessed at.
"""

from __future__ import annotations

BAND_LABELS: tuple[str, ...] = ("0-19", "20-39", "40-59", "60-79", "80-100")
BAND_WIDTH = 20
UNBUCKETED_LABEL = "unknown"


def band_label_for_percentage(percentage: float) -> str:
    if percentage < 0:
        percentage = 0.0
    index = int(percentage // BAND_WIDTH)
    if index >= len(BAND_LABELS):
        index = len(BAND_LABELS) - 1
    return BAND_LABELS[index]


def score_bucket_label(
    *, score: float | None, max_score: float | None, declared_bucket: str | None = None
) -> str | None:
    """Return the distribution label for one completed evaluation, or ``None``."""
    if declared_bucket:
        return declared_bucket
    if score is None or max_score is None or max_score <= 0:
        return None
    return band_label_for_percentage(score / max_score * 100.0)
