"""Scoring and normalization tests (pure functions, no database)."""

from app.mapping.normalize import (
    normalize_text,
    ordered_tokens,
    significant_tokens,
    size_hints,
)
from app.mapping.scoring import score_pair


def test_normalize_strips_accents_punctuation_and_case():
    assert normalize_text("Crème de Violette*") == "creme de violette"
    assert normalize_text("  BEER   - Hopry Hazy ") == "beer hopry hazy"


def test_size_hints_canonicalize_units_and_spacing():
    assert size_hints("Titos 1L") == frozenset({"1l"})
    assert size_hints("Titos 1 l") == frozenset({"1l"})
    assert size_hints("Vodka 750 ML") == frozenset({"750ml"})
    assert size_hints("6 pk Hopewell Parson's Beer") == frozenset({"6pk"})
    assert size_hints("Keg 1/6 BBL") == frozenset({"1/6bbl"})
    assert size_hints("Deep Eddy Grapefruit") == frozenset()


def test_significant_tokens_drop_organizational_noise():
    # R365 type prefixes, containers, and vendor numbers disappear; so do
    # Toast serve styles and price prefixes.
    assert significant_tokens("VODKA Deep Eddy Grapefruit") == {"deep", "eddy", "grapefruit"}
    assert significant_tokens("BEER - Twisted Tea 12oz Can") == {"twisted", "tea"}
    assert significant_tokens("$4 Hopewell Parson's Beer") == {"hopewell", "parsons"}
    assert significant_tokens("Bowman Brothers Virginia Straight Bourbon 9145341") == {
        "bowman",
        "brothers",
        "virginia",
        "straight",
        "bourbon",
    }


def test_ordered_tokens_preserve_order_for_brand_heuristics():
    assert ordered_tokens("Deep Eddy Grapefruit") == ["deep", "eddy", "grapefruit"]


def test_exact_identity_scores_high():
    score = score_pair(
        "Deep Eddy Grapefruit",
        "VODKA Deep Eddy Grapefruit",
        toast_category="spirit",
        r365_categories=("Bar", "Liquor"),
    )
    assert score.total >= 0.75


def test_unrelated_names_score_below_any_threshold():
    score = score_pair(
        "Tecate",
        "WINE - Scarpetta Prosecco",
        toast_category="beer",
        r365_categories=("Bar", "Wine"),
    )
    assert score.total < 0.3
    assert score.token_dice == 0.0


def test_size_conflict_penalizes_same_brand_wrong_size():
    right = score_pair(
        "Titos 1L", "VODKA Titos 1L", toast_category="spirit", r365_categories=("Bar", "Liquor")
    )
    wrong = score_pair(
        "Titos 1L", "VODKA Titos 750ml", toast_category="spirit", r365_categories=("Bar", "Liquor")
    )
    assert right.total > wrong.total
    assert wrong.size_affinity == 0.0


def test_category_conflict_penalizes_cross_category_match():
    agree = score_pair(
        "Coors Banquet",
        "BEER Coors Banquet",
        toast_category="beer",
        r365_categories=("Bar", "Beer"),
    )
    conflict = score_pair(
        "Coors Banquet",
        "BEER Coors Banquet",
        toast_category="beer",
        r365_categories=("Bar", "Wine"),
    )
    assert agree.total > conflict.total
    assert conflict.category_affinity == 0.0


def test_unknown_categories_score_neutral_not_zero():
    score = score_pair("Old Fashioned", "WHISKEY George Dickel No 12")
    assert score.category_affinity == 0.5


def test_word_order_and_prefix_do_not_defeat_match():
    # "House Red Wine" vs R365's "WINE - House Red" style inversion.
    score = score_pair(
        "House Red Wine",
        "WINE - House Red",
        toast_category="wine",
        r365_categories=("Bar", "Wine"),
    )
    assert score.total >= 0.6
