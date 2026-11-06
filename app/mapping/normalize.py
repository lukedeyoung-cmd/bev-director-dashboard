"""Pure text normalization for Toast↔R365 name matching.

R365 item names carry organizational prefixes ("VODKA", "WINE -", "NA BEV",
"MIXER"), vendor item numbers ("Bowman Brothers ... 9145341"), and pack
sizes ("375ML"); Toast names carry price prefixes ("$4 ..."), pack counts
("6 pk ..."), and star suffixes ("St. Bernardus Wit*"). Normalization strips
both sides down to comparable significant tokens, and pulls size hints out
separately so "1L" vs "750ML" informs the score instead of polluting token
overlap. Category words ("beer", "vodka", ...) drop from the token stream:
the structured category columns score that affinity separately, and dropping
them on both sides neutralizes the naming-convention mismatch.
"""

from __future__ import annotations

import re
import unicodedata

# Organizational prefixes, serve styles, containers, and filler words that
# carry no identity signal once both sides drop them ("Twisted Tea Draft"
# vs "BEER - Twisted Tea 12oz Can" reduces to {twisted, tea} both ways).
_STOP_TOKENS = frozenset(
    {
        "na",
        "bev",
        "dnu",
        "zzz",
        "no",
        "each",
        "pack",
        "pk",
        "case",
        "cases",
        "can",
        "cans",
        "bottle",
        "bottles",
        "btl",
        "bt",
        "keg",
        "draft",
        "draught",
        "pitcher",
        "pitchers",
        "pint",
        "glass",
        "the",
        "and",
        "with",
        "of",
        "beer",
        "wine",
        "vodka",
        "gin",
        "rum",
        "whiskey",
        "whisky",
        "tequila",
        "mezcal",
        "liqueur",
        "liquor",
        "sake",
    }
)

# Numeric size hints: 750ml, 1l, 12oz, 6pk, 1/6 bbl, 1.75l ...
_SIZE_PATTERN = re.compile(
    r"\b\d+/\d+\s*-?\s*bbl\b|\b\d+(?:\.\d+)?\s*-?\s*(?:ml|l|oz|pk|pack|bbl|gal|liter|litre)\b"
)

_PUNCT = re.compile(r"[^a-z0-9\s/]+")

# Possessives/contractions join back onto the word ("Parson's" -> "parsons",
# matching R365's apostrophe-free "Parsons") — drop them before the generic
# punctuation-to-space pass, which would otherwise split the token.
_APOSTROPHES = re.compile(r"['\u2019]")


def _deaccent(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii")


def _prepare(name: str) -> str:
    """Lowercase, de-accentuate, drop apostrophes, punct -> space."""
    text = _deaccent(name.lower())
    text = _APOSTROPHES.sub("", text)
    return _PUNCT.sub(" ", text)


def normalize_text(name: str) -> str:
    """Lowercase, de-accentuate, strip punctuation, collapse whitespace."""
    return " ".join(_prepare(name).split())


def _canonical_size(raw: str) -> str:
    compact = re.sub(r"\s+", "", raw)
    return (
        compact.replace("pack", "pk")
        .replace("liter", "l")
        .replace("litre", "l")
        .replace("-", "")
    )


def size_hints(name: str) -> frozenset[str]:
    """Canonical size tokens ("750ml", "1l", "12oz", "6pk", "1/6bbl")."""
    return frozenset(_canonical_size(m.group(0)) for m in _SIZE_PATTERN.finditer(name.lower()))


def ordered_tokens(name: str) -> list[str]:
    """Significant tokens in name order (brand-token heuristics use the lead)."""
    without_sizes = _SIZE_PATTERN.sub(" ", name.lower())
    return [
        token
        for token in _prepare(without_sizes).split()
        if len(token) >= 2 and not token.isdigit() and token not in _STOP_TOKENS
    ]


def significant_tokens(name: str) -> frozenset[str]:
    """Tokens left after removing sizes, bare numbers, stopwords, fragments."""
    return frozenset(ordered_tokens(name))
