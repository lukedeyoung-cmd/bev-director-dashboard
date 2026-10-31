"""Pure transform tests: catalog extraction and order aggregation."""

from decimal import Decimal

from app.toast.categories import OTHER
from app.toast.transforms import (
    MENU_ITEM,
    MODIFIER,
    aggregate_orders,
    extract_catalog_records,
    extract_config_records,
    to_money,
)
from tests.toast_payloads import (
    CONFIG_ITEMS,
    MENUS_PAYLOAD,
    applied_discount,
    check,
    order,
    selection,
)


def by_guid(aggregates):
    return {agg.pos_guid: agg for agg in aggregates.values()}


# ----------------------------------------------------------------------
# to_money
# ----------------------------------------------------------------------
def test_to_money_parses_floats():
    assert to_money(28.0) == Decimal("28.00")
    assert to_money(0.5) == Decimal("0.50")
    assert to_money(None) == Decimal("0")


# ----------------------------------------------------------------------
# Catalog extraction
# ----------------------------------------------------------------------
def test_extract_catalog_records_walks_nested_groups():
    records = {r.pos_guid: r for r in extract_catalog_records(MENUS_PAYLOAD)}
    assert set(records) == {"item-btg", "item-bottle", "item-btg2", "item-beer"}
    assert records["item-beer"].category == "beer"
    assert records["item-btg"].category == "wine"
    assert records["item-btg2"].category == "wine"  # BTG under Wine
    assert records["item-btg2"].unit_price == Decimal("13.00")


def test_extract_config_records_are_gap_fillers():
    records = extract_config_records(CONFIG_ITEMS)
    assert all(not r.authoritative for r in records)
    by_guid_map = {r.pos_guid: r for r in records}
    # Config records carry no menu-group path, so classification falls back to
    # the item name alone — "La Chouffe POS NAME" has no beer keyword, so it
    # lands in "other" until menus-v2 marks it authoritative on the next sync.
    assert by_guid_map["item-beer"].category == OTHER


# ----------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------
def test_basic_quantities_and_money():
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection("s1", "i1", "Old Fashioned", 2, 15.0),
                        selection("s2", "i2", "La Chouffe", 1, 10.0),
                    ],
                )
            ],
        )
    ]
    aggregates, stats = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert rows["i1"].quantity == Decimal("2")
    assert rows["i1"].gross_sales == Decimal("30.00")
    assert rows["i1"].net_sales == Decimal("30.00")
    assert rows["i1"].item_type == MENU_ITEM
    assert stats.orders_seen == 1
    assert stats.selections_seen == 2


def test_modifiers_aggregate_as_own_rows():
    modifier = selection("m1", "i-mod", "Tito's Splash", 1, 2.0)
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [selection("s1", "i1", "Old Fashioned", 1, 15.0, modifiers=[modifier])],
                )
            ],
        )
    ]
    aggregates, stats = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert rows["i-mod"].item_type == MODIFIER
    assert rows["i-mod"].gross_sales == Decimal("2.00")
    assert rows["i-mod"].category == "cocktail"  # name-based fallback
    assert stats.selections_seen == 2


def test_voided_entities_excluded():
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection("s1", "i1", "Old Fashioned", 1, 15.0),
                        selection("s2", "i2", "La Chouffe", 1, 10.0, voided=True),
                    ],
                )
            ],
        ),
        order("o2", [check("c2", [selection("s3", "i3", "Coffee", 1, 7.0)])], voided=True),
        order(
            "o3",
            [check("c3", [selection("s4", "i4", "Espresso", 1, 4.0)], voided=True)],
        ),
    ]
    aggregates, stats = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert set(rows) == {"i1"}  # every void flavor (order/check/selection) excluded
    # orders_voided counts order-level void flags; the check-level void is
    # verified excluded above rather than double-counted here.
    assert stats.orders_voided == 1
    assert stats.selections_voided == 1


def test_open_items_without_guid_are_counted_and_skipped():
    bare = {"guid": "s9", "displayName": "Kitchen Special", "quantity": 1, "price": 20.0}
    orders = [order("o1", [check("c1", [bare])])]
    aggregates, stats = aggregate_orders(orders)
    assert aggregates == {}
    assert stats.open_items_without_guid == 1


def test_selection_discount_applies_to_its_line():
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection(
                            "s1", "i1", "Old Fashioned", 1, 15.0, discounts=[applied_discount(15.0)]
                        ),
                        selection("s2", "i2", "La Chouffe", 1, 10.0),
                    ],
                )
            ],
        )
    ]
    aggregates, _ = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert rows["i1"].discount_total == Decimal("15.00")
    assert rows["i1"].net_sales == Decimal("0.00")
    assert rows["i2"].discount_total == Decimal("0.00")


def test_check_discount_allocates_proportionally():
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection("s1", "i1", "Old Fashioned", 2, 15.0),  # 30
                        selection("s2", "i2", "La Chouffe", 1, 10.0),  # 10
                    ],
                    discounts=[applied_discount(20.0)],
                )
            ],
        )
    ]
    aggregates, _ = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert rows["i1"].discount_total == Decimal("15.00")  # 30/40 * 20
    assert rows["i2"].discount_total == Decimal("5.00")  # 10/40 * 20


def test_comp_exceeding_gross_never_nets_negative():
    # Real-world case: 100% comp whose amount also covers tax.
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection("s1", "i1", "Steak", 1, 77.0),
                        selection("s2", "i2", "Side", 1, 5.0),
                    ],
                    discounts=[applied_discount(102.0)],
                )
            ],
        )
    ]
    aggregates, stats = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert all(agg.net_sales >= 0 for agg in rows.values())
    assert rows["i1"].net_sales == Decimal("0.00")
    assert rows["i2"].net_sales == Decimal("0.00")
    assert stats.dropped_discount_excess == Decimal("20.00")


def test_mixed_selection_and_check_discounts_never_go_negative():
    # Real-world shape (observed on a comped check from a live tenant): a selection-level
    # comp larger than the line's gross while a check-level discount also
    # applies. The pool must respect capacity the line's own discount already
    # consumed instead of double-capping it.
    orders = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection(
                            "s1",
                            "i1",
                            "Arancini",
                            1,
                            16.0,
                            discounts=[applied_discount(21.19)],
                        ),
                        selection("s2", "i2", "Other", 1, 10.0),
                    ],
                    discounts=[applied_discount(0.0)],
                )
            ],
        )
    ]
    aggregates, stats = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert all(agg.net_sales >= 0 for agg in rows.values())
    # The comp covers its line's gross fully; the 5.19 overflow (tax) flows to
    # the check's other lines up to their gross.
    assert rows["i1"].discount_total == Decimal("16.00")
    assert rows["i1"].net_sales == Decimal("0.00")
    assert rows["i2"].discount_total == Decimal("5.19")
    assert rows["i2"].net_sales == Decimal("4.81")
    assert stats.dropped_discount_excess == Decimal("0.00")


def test_same_item_on_two_checks_aggregates_with_per_check_allocation():
    # Allocation uses each check's line gross, not the date-level total.
    orders = [
        order(
            "o1",
            [
                check("c1", [selection("s1", "i1", "Wine", 1, 30.0)]),
                check("c2", [selection("s2", "i1", "Wine", 1, 10.0)]),
            ],
        )
    ]
    aggregates, _ = aggregate_orders(orders)
    rows = by_guid(aggregates)
    assert rows["i1"].gross_sales == Decimal("40.00")
    assert rows["i1"].quantity == Decimal("2")
