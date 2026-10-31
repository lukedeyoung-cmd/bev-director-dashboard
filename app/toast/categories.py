"""Beverage category classification from POS names.

Heuristic and deliberately small: the category snapshot on pos_items /
daily_sales rows groups the dashboard's beverage views. Classification runs
against ordered context names — deepest menu group first, then ancestors,
then the item name — so a wine item under the "BTG" group matches "wine"
via its parent while a pour named "House White Wine" matches on its own name.
Anything unmatched lands in "other" rather than guessing a beverage category.

Refinements (per-venue overrides, salesCategory lookups) can replace this
later; the category columns are snapshots, so corrections only affect rows
ingested after the change.
"""

from __future__ import annotations

import re

COCKTAIL = "cocktail"
WINE = "wine"
BEER = "beer"
SPIRIT = "spirit"
NA = "na"
FOOD = "food"
OTHER = "other"

# Ordered: the first rule matching a context name wins. Within one context
# name, beverage rules precede food rules so "wine cocktail" reads cocktail
# and "sour" without beer context reads cocktail.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        COCKTAIL,
        (
            "cocktail",
            "martini",
            "old fashioned",
            "negroni",
            "boulevardier",
            "margarita",
            "spritz",
            "spritzer",
            "daiquiri",
            "mojito",
            "mule",
            "julep",
            "paloma",
            "gimlet",
            "highball",
            "sazerac",
            "manhattan",
            "sidecar",
            "collins",
            "smash",
            "toddy",
            "cosmopolitan",
            "sour",
        ),
    ),
    (
        WINE,
        (
            "wine",
            "btg",
            "btl",
            "bottle",
            "sparkling",
            "champagne",
            "prosecco",
            "cava",
            "rose",
            "rosé",
            "riesling",
            "chardonnay",
            "sauvignon",
            "chenin",
            "chablis",
            "cabernet",
            "pinot",
            "merlot",
            "malbec",
            "gamay",
            "nebbiolo",
            "sangiovese",
            "tempranillo",
            "syrah",
            "grenache",
            "moscato",
            "sauternes",
            "gruner",
            "albarino",
            "albariño",
            "burgundy",
            "bordeaux",
        ),
    ),
    (
        BEER,
        (
            "beer",
            "ale",
            "lager",
            "ipa",
            "stout",
            "porter",
            "pilsner",
            "draft",
            "draught",
            "cider",
        ),
    ),
    (
        SPIRIT,
        (
            "spirits",
            "bourbon",
            "rye",
            "whisky",
            "whiskey",
            "scotch",
            "vodka",
            "gin",
            "tequila",
            "mezcal",
            "agave",
            "rum",
            "brandy",
            "cognac",
            "armagnac",
            "amaro",
            "liqueur",
            "botanical",
        ),
    ),
    (
        NA,
        (
            "n/a",
            "non-alcoholic",
            "temperance",
            "mocktail",
            "coffee",
            "espresso",
            "latte",
            "cappuccino",
            "americano",
            "tea",
            "soda",
            "coke",
            "cola",
            "sprite",
            "lemonade",
            "limeade",
            "juice",
            "kombucha",
            "red bull",
            "gatorade",
            "seltzer",
            "sparkling water",
            "hot chocolate",
            "cocoa",
        ),
    ),
    (
        FOOD,
        (
            "food",
            "starter",
            "appetizer",
            "side",
            "steak",
            "entree",
            "entrée",
            "salad",
            "sandwich",
            "burger",
            "fries",
            "oyster",
            "snack",
            "dessert",
            "pie",
            "cake",
            "tart",
            "brunch",
            "lunch",
            "dinner",
        ),
    ),
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    # `s?` covers simple plurals ("Cocktails", "Starters", "Oysters"); Toast
    # group names are usually plural while the keywords are singular.
    (category, re.compile(r"\b(?:" + r"|".join(re.escape(k) for k in keywords) + r")s?\b"))
    for category, keywords in _RULES
)


def classify_category(*context_names: str | None) -> str:
    """Classify from ordered context names (deepest group → … → item name)."""
    for name in context_names:
        if not name:
            continue
        lowered = name.lower()
        for category, pattern in _PATTERNS:
            if pattern.search(lowered):
                return category
    return OTHER
