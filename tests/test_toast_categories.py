"""Category classification heuristics."""

from app.toast.categories import BEER, COCKTAIL, FOOD, NA, OTHER, SPIRIT, WINE, classify_category


def test_classic_cocktail_by_name():
    assert classify_category("Espresso Martini") == COCKTAIL
    assert classify_category("Aperol Spritz") == COCKTAIL
    assert classify_category("Old Fashioned") == COCKTAIL


def test_wine_by_group_path_deepest_first():
    # "BTG" alone doesn't say wine; the parent group does.
    assert classify_category("BTG", "Wine", "House White Wine") == WINE
    assert classify_category("Rose/Orange", "Wine") == WINE
    assert classify_category("Sparkling", "Wine", "Raventos Sparkling Rose BTL") == WINE


def test_beer_by_group():
    assert classify_category("Beer", "La Chouffe") == BEER


def test_spirits_subgroups():
    assert classify_category("Bourbon", "Spirits", "Redwood Empire Pipe Dream") == SPIRIT
    assert classify_category("Agave", "Spirits", "Arette Blanco") == SPIRIT
    # Vermouth under their Spirits menu stays spirit (no vermouth keyword).
    assert classify_category("Vermouth", "Spirits", "House Vermouth Shot") == SPIRIT


def test_na_group():
    assert classify_category("Coffee & Tea", "N/A", "Coffee") == NA
    assert classify_category("Soda", "N/A", "Coca Cola") == NA
    assert classify_category("Temperance Beverages", "N/A", "De Soi Tres Rose") == NA


def test_food_groups():
    assert classify_category("Starters", "Oysters") == FOOD
    assert classify_category("Steak", "New York Strip") == FOOD
    assert classify_category("Dessert", "Coconut Key Lime Pie") == FOOD


def test_unmatched_falls_to_other():
    assert classify_category("Room Fee") == OTHER
    assert classify_category(None) == OTHER


def test_wine_cocktail_reads_cocktail():
    assert classify_category("Wine Cocktail") == COCKTAIL
