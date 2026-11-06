"""Assisted Toast↔R365 item mapping: suggestions, review state, coverage.

The costing bridge between sales (Toast) and purchasing (R365). Suggestion
scoring is pure text math (`normalize`, `scoring`); `engine` persists the
proposals for human review; `coverage` reports what share of sales volume
flows through approved mappings.
"""

from app.mapping.coverage import (
    SliceCoverage,
    VenueCoverage,
    category_group_coverage,
    coverage_for_venues,
    group_coverage,
)
from app.mapping.engine import (
    DEFAULT_MIN_CONFIDENCE,
    SuggestStats,
    best_candidate,
    build_r365_index,
    suggest_mappings,
)
from app.mapping.scoring import ScoreComponents, score_pair

__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "ScoreComponents",
    "SliceCoverage",
    "SuggestStats",
    "VenueCoverage",
    "best_candidate",
    "build_r365_index",
    "category_group_coverage",
    "coverage_for_venues",
    "group_coverage",
    "score_pair",
    "suggest_mappings",
]
