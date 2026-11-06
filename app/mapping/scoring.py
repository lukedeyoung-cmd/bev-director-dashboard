"""Pure scoring for Toast↔R365 name pairs.

Nothing here touches the database — the engine feeds candidate pairs in and
gets a component breakdown plus a total in [0, 1] back. Token overlap
(Sørensen–Dice on significant tokens) carries most of the weight; sequence
similarity catches near-misses and word-order shuffles. Size hints, the
brand lead token, and category compatibility are modifiers that break ties
and penalize plausible-looking wrong answers (same brand, wrong size).
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.mapping.normalize import (
    normalize_text,
    ordered_tokens,
    significant_tokens,
    size_hints,
)

# Relative weight of each signal in the total score.
_W_TOKEN = 0.50
_W_SEQUENCE = 0.20
_W_SIZE = 0.15
_W_BRAND = 0.05
_W_CATEGORY = 0.10

# Score when a signal is absent/undecidable (unknown category, size on one
# side only) — penalize nothing, reward nothing.
NEUTRAL = 0.5

# Toast category -> R365 category1/category2 keywords that confirm a match,
# and keywords that rule it out. In R365 the "Bar" group hosts spirits, beer,
# wine, and NA together, so "Bar" alone neither confirms nor denies — only a
# positively contradicting group (e.g. "Food" for a beer item) scores conflict.
_CATEGORY_KEYWORDS = {
    "beer": ("beer", "cider"),
    "wine": ("wine",),
    "spirit": ("liquor",),
    "na": ("na bev",),
    "food": ("food",),
}

_CATEGORY_CONFLICTS = {
    "beer": ("liquor", "wine", "food"),
    "wine": ("liquor", "beer", "food"),
    "spirit": ("wine", "beer", "food", "na bev"),
    "na": ("liquor", "wine", "food"),
    "food": ("liquor", "beer", "wine", "na bev"),
}


@dataclass(frozen=True)
class ScoreComponents:
    """Breakdown of one pair's score; `total` is the weighted sum, capped at 1."""

    token_dice: float
    sequence_ratio: float
    size_affinity: float
    brand_affinity: float
    category_affinity: float
    total: float


def category_affinity(toast_category: str | None, r365_categories: tuple[str, ...]) -> float:
    """1.0 when structured categories agree, 0.0 when they conflict, else neutral."""
    keywords = " ".join(c.lower() for c in r365_categories if c)
    if not toast_category or not keywords:
        return NEUTRAL
    expected = _CATEGORY_KEYWORDS.get(toast_category)
    if not expected:
        return NEUTRAL
    if any(word in keywords for word in expected):
        return 1.0
    forbidden = _CATEGORY_CONFLICTS.get(toast_category, ())
    if any(word in keywords for word in forbidden):
        return 0.0
    return NEUTRAL


def size_affinity(toast_sizes: frozenset[str], r365_sizes: frozenset[str]) -> float:
    """1.0 when both sides state sizes and they agree, 0.0 when they conflict."""
    if toast_sizes and r365_sizes:
        return 1.0 if toast_sizes == r365_sizes else 0.0
    return NEUTRAL


def brand_affinity(toast_tokens: list[str], r365_tokens: frozenset[str]) -> float:
    """Lead-token heuristic: the first significant Toast token is the brand."""
    if not toast_tokens:
        return NEUTRAL
    return 1.0 if toast_tokens[0] in r365_tokens else 0.0


def score_pair(
    toast_name: str,
    r365_name: str,
    *,
    toast_category: str | None = None,
    r365_categories: tuple[str, ...] = (),
) -> ScoreComponents:
    """Score one Toast name against one R365 name."""
    toast_sizes = size_hints(toast_name)
    r365_sizes = size_hints(r365_name)
    toast_ordered = ordered_tokens(toast_name)
    toast_tokens = frozenset(toast_ordered)
    r365_tokens = significant_tokens(r365_name)

    if toast_tokens and r365_tokens:
        overlap = len(toast_tokens & r365_tokens)
        dice = 2 * overlap / (len(toast_tokens) + len(r365_tokens))
    else:
        dice = 0.0

    sequence = SequenceMatcher(None, normalize_text(toast_name), normalize_text(r365_name)).ratio()
    brand = brand_affinity(toast_ordered, r365_tokens)
    category = category_affinity(toast_category, r365_categories)
    size = size_affinity(toast_sizes, r365_sizes)

    total = min(
        1.0,
        _W_TOKEN * dice
        + _W_SEQUENCE * sequence
        + _W_SIZE * size
        + _W_BRAND * brand
        + _W_CATEGORY * category,
    )
    return ScoreComponents(
        token_dice=dice,
        sequence_ratio=sequence,
        size_affinity=size,
        brand_affinity=brand,
        category_affinity=category,
        total=total,
    )
