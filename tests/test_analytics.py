"""Analytics metric tests against a small, hand-computed dataset."""

from datetime import date
from decimal import Decimal

import pytest

from app.analytics import (
    BUCKET_LABELS,
    category_bucket,
    category_mix,
    cost_summary,
    daily_trend,
    discount_watch,
    item_movers,
    resolve_window,
    sales_summary,
    venue_rankings,
)
from app.extensions import db
from app.models.catalog import Ingredient, PosItem
from app.models.mapping import PosItemIngredientMap
from app.models.purchasing import Invoice, InvoiceLine, Vendor
from app.models.sales import DailySales
from app.models.venue import Venue

END = date(2026, 3, 10)  # anchor; days=2 → current Mar 9–10, prior Mar 7–8


@pytest.fixture()
def scope(app):  # app fixture ensures context + tables
    venue_a = Venue(name="Venue A", code="VA")
    venue_b = Venue(name="Venue B", code="VB")
    db.session.add_all([venue_a, venue_b])
    db.session.flush()

    def item(venue, name, category, price):
        pos_item = PosItem(
            venue_id=venue.id,
            pos_guid=f"g-{name}",
            name=name,
            item_type="menu_item",
            category=category,
            unit_price=Decimal(str(price)),
        )
        db.session.add(pos_item)
        db.session.flush()  # assign pos_item.id before sales rows reference it
        return pos_item

    ipa = item(venue_a, "Draft IPA", "beer", 8.00)
    wine = item(venue_a, "Wine Glass", "wine", 10.00)
    old_fashioned = item(venue_a, "Old Fashioned", "cocktail", 14.00)
    ipa_b = item(venue_b, "Bottle IPA", "beer", 8.00)

    def sale(pos_item, day, quantity, gross, discount=0):
        db.session.add(
            DailySales(
                venue_id=pos_item.venue_id,
                business_date=day,
                pos_item_id=pos_item.id,
                category=pos_item.category,
                quantity_sold=Decimal(quantity),
                gross_sales=Decimal(str(gross)),
                discount_total=Decimal(str(discount)),
                net_sales=Decimal(str(gross)) - Decimal(str(discount)),
            )
        )

    # Prior window (Mar 7–8)
    sale(ipa, date(2026, 3, 7), 8, 64.00)
    # Current window (Mar 9–10)
    sale(ipa, date(2026, 3, 9), 10, 80.00)
    sale(ipa, date(2026, 3, 10), 12, 96.00)
    sale(wine, date(2026, 3, 9), 5, 50.00)
    sale(old_fashioned, date(2026, 3, 10), 4, 56.00, discount=6.00)
    sale(ipa_b, date(2026, 3, 9), 20, 160.00)
    db.session.commit()

    return {"va": venue_a, "vb": venue_b, "ipa": ipa, "wine": wine, "of": old_fashioned}


def test_category_bucket_normalization():
    assert category_bucket("beer") == "beer"
    assert category_bucket("Wine") == "wine"
    assert category_bucket("cocktail") == "spirits"
    assert category_bucket("LIQUOR") == "spirits"
    assert category_bucket("non-alc") == "na"
    assert category_bucket("food") == "other"
    assert category_bucket(None) == "other"
    assert BUCKET_LABELS["spirits"] == "Spirits"


def test_resolve_window_anchors_on_newest_business_date(app, scope):
    with app.app_context():
        window = resolve_window(db.session, [scope["va"].id], 2)
        assert window is not None
        assert window.current.end == END
        assert window.current.start == date(2026, 3, 9)
        assert window.prior.start == date(2026, 3, 7)
        assert window.prior.end == date(2026, 3, 8)


def test_resolve_window_none_without_data(app, scope):  # noqa: ARG001
    empty_venue = Venue(name="Empty", code="EM")
    db.session.add(empty_venue)
    db.session.commit()
    assert resolve_window(db.session, [empty_venue.id], 14) is None
    assert resolve_window(db.session, [], 14) is None


def test_sales_summary_group_totals(app, scope):
    with app.app_context():
        ids = [scope["va"].id, scope["vb"].id]
        window = resolve_window(db.session, ids, 2)
        summary = sales_summary(db.session, ids, window.current)
        assert summary.net_sales == Decimal("436.00")
        assert summary.gross_sales == Decimal("442.00")
        assert summary.discount_total == Decimal("6.00")
        assert summary.prior_net_sales == Decimal("64.00")
        assert summary.net_change == Decimal("372.00")
        assert summary.net_change_pct == pytest.approx(581.25)
        assert summary.discount_rate == pytest.approx(6 / 442 * 100)


def test_daily_trend_alignment(app, scope):
    with app.app_context():
        ids = [scope["va"].id, scope["vb"].id]
        window = resolve_window(db.session, ids, 2)
        trend = daily_trend(db.session, ids, window)
        assert [p.label for p in trend] == ["3/9", "3/10"]
        assert trend[0].current == Decimal("290.00")  # 80 + 50 + 160
        assert trend[1].current == Decimal("146.00")  # 96 + 50
        assert trend[0].prior == Decimal("64.00")
        assert trend[1].prior is None  # no Mar 8 data


def test_category_mix_shares(app, scope):
    with app.app_context():
        ids = [scope["va"].id, scope["vb"].id]
        window = resolve_window(db.session, ids, 2)
        mix = category_mix(db.session, ids, window.current)
        by_bucket = {s.bucket: s for s in mix}
        assert set(by_bucket) == {"beer", "wine", "spirits"}
        assert by_bucket["beer"].net_sales == Decimal("336.00")
        assert by_bucket["beer"].share == pytest.approx(336 / 436)
        assert by_bucket["wine"].net_sales == Decimal("50.00")
        assert by_bucket["spirits"].net_sales == Decimal("50.00")


def test_venue_rankings_order_and_top_bucket(app, scope):
    with app.app_context():
        window = resolve_window(db.session, [scope["va"].id, scope["vb"].id], 2)
        ranks = venue_rankings(db.session, window.current)
        assert [r.code for r in ranks] == ["VA", "VB"]
        assert ranks[0].net_sales == Decimal("276.00")
        assert ranks[0].share == pytest.approx(276 / 436)
        assert ranks[0].top_bucket_label == "Beer"
        assert ranks[0].change_pct == pytest.approx((276 - 64) / 64 * 100)
        assert ranks[1].prior_net_sales is None  # VB has no prior data


def test_item_movers_top_and_bottom(app, scope):
    with app.app_context():
        ids = [scope["va"].id, scope["vb"].id]
        window = resolve_window(db.session, ids, 2)
        top, bottom = item_movers(db.session, ids, window)
        assert top[0].item_name == "Bottle IPA"  # +160, largest increase
        assert top[0].delta == Decimal("160.00")
        assert top[1].item_name == "Draft IPA"  # +112
        assert bottom == []  # nothing declined, so no bottom board


def test_discount_watch_rate(app, scope):
    with app.app_context():
        ids = [scope["va"].id]
        window = resolve_window(db.session, ids, 2)
        rows = discount_watch(db.session, ids, window.current)
        assert len(rows) == 1
        assert rows[0].item_name == "Old Fashioned"
        assert rows[0].discount_total == Decimal("6.00")
        assert rows[0].rate == pytest.approx(6 / 56 * 100)


def test_cost_summary_gates_and_computes(app, scope):
    with app.app_context():
        va = scope["va"]
        window = resolve_window(db.session, [va.id], 2)
        period = window.current

        # No invoices or maps yet -> cost KPIs stay dark.
        assert cost_summary(db.session, [va.id], period) is None

        bourbon = Ingredient(
            name="Bourbon", category="spirit", base_unit="oz", external_ref="ing-1"
        )
        db.session.add(bourbon)
        db.session.flush()
        db.session.add(
            PosItemIngredientMap(
                pos_item_id=scope["of"].id,
                ingredient_id=bourbon.id,
                quantity=Decimal("2.0"),
                unit="oz",
            )
        )
        db.session.flush()
        # Maps alone still aren't cost data without a purchase price.
        assert cost_summary(db.session, [va.id], period) is None

        vendor = Vendor(name="Test Vendor", external_ref="v-1")
        db.session.add(vendor)
        db.session.flush()
        invoice = Invoice(
            venue_id=va.id,
            vendor_id=vendor.id,
            invoice_number="INV-1",
            invoice_date=date(2026, 3, 9),
        )
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=1,
                ingredient_id=bourbon.id,
                quantity=Decimal("10"),
                unit_of_measure="oz",
                unit_cost=Decimal("1.00"),
                line_total=Decimal("10.00"),
            )
        )
        db.session.commit()

        cost = cost_summary(db.session, [va.id], period)
        assert cost is not None
        assert cost.actual_cost == Decimal("10.00")
        # 4 Old Fashioneds sold × 2 oz × $1.00/oz
        assert cost.theoretical_cost == Decimal("8.00")
        assert cost.cogs_pct == pytest.approx(10 / 276 * 100)
        assert cost.pour_cost_pct == pytest.approx(8 / 276 * 100)
        assert cost.variance_dollars == Decimal("2.00")
        assert cost.net_sales == Decimal("276.00")  # VA is the only costed venue
        assert cost.costed_venues == 1 and cost.scope_venues == 1
        assert cost.partial_coverage is False


def test_cost_scope_respects_venue(app, scope):
    """Another venue's invoices must not leak into this venue's COGS."""
    with app.app_context():
        va, vb = scope["va"], scope["vb"]
        ingredient = Ingredient(
            name="Vodka", category="spirit", base_unit="oz", external_ref="ing-2"
        )
        db.session.add(ingredient)
        db.session.flush()
        vendor = Vendor(name="Test Vendor 2", external_ref="v-2")
        db.session.add(vendor)
        db.session.flush()
        invoice = Invoice(
            venue_id=vb.id,
            vendor_id=vendor.id,
            invoice_number="INV-VB",
            invoice_date=date(2026, 3, 9),
        )
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=1,
                ingredient_id=ingredient.id,
                quantity=Decimal("50"),
                unit_of_measure="oz",
                unit_cost=Decimal("2.00"),
                line_total=Decimal("100.00"),
            )
        )
        db.session.commit()

        window = resolve_window(db.session, [va.id], 2)
        assert cost_summary(db.session, [va.id], window.current) is None


def test_cost_summary_group_denominator_excludes_uncosted_venues(app, scope):
    """Group COGS% divides by costed venues' sales, not the whole group."""
    with app.app_context():
        va, vb = scope["va"], scope["vb"]
        ingredient = Ingredient(name="Rum", category="spirit", base_unit="oz", external_ref="ing-3")
        db.session.add(ingredient)
        db.session.flush()
        vendor = Vendor(name="Test Vendor 3", external_ref="v-3")
        db.session.add(vendor)
        db.session.flush()
        invoice = Invoice(
            venue_id=va.id,
            vendor_id=vendor.id,
            invoice_number="INV-VA",
            invoice_date=date(2026, 3, 9),
        )
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=1,
                ingredient_id=ingredient.id,
                quantity=Decimal("5"),
                unit_of_measure="oz",
                unit_cost=Decimal("1.00"),
                line_total=Decimal("5.00"),
            )
        )
        db.session.commit()

        ids = [va.id, vb.id]
        window = resolve_window(db.session, ids, 2)
        cost = cost_summary(db.session, ids, window.current)
        assert cost is not None
        # Denominator is VA's 276 (costed), never the group's 436.
        assert cost.net_sales == Decimal("276.00")
        assert cost.cogs_pct == pytest.approx(5 / 276 * 100)
        assert cost.costed_venues == 1 and cost.scope_venues == 2
        assert cost.partial_coverage is True


def test_movers_never_overlap(app, scope):
    """With more movers than the limit, top and bottom never share an item."""
    with app.app_context():
        va = scope["va"]
        for i in range(12):
            pos_item = PosItem(
                venue_id=va.id,
                pos_guid=f"g-mover-{i}",
                name=f"Item {i}",
                item_type="menu_item",
                category="beer",
            )
            db.session.add(pos_item)
            db.session.flush()  # assign pos_item.id before sales rows reference it
            # Current window: ascending sales; prior window: descending, and
            # scaled so these dominate the scope-fixture items (+112 max),
            # keeping top/bottom membership deterministic.
            db.session.add(
                DailySales(
                    venue_id=va.id,
                    business_date=date(2026, 3, 9),
                    pos_item_id=pos_item.id,
                    category="beer",
                    quantity_sold=Decimal(i + 1),
                    gross_sales=Decimal((i + 1) * 100),
                    discount_total=Decimal(0),
                    net_sales=Decimal((i + 1) * 100),
                )
            )
            db.session.add(
                DailySales(
                    venue_id=va.id,
                    business_date=date(2026, 3, 7),
                    pos_item_id=pos_item.id,
                    category="beer",
                    quantity_sold=Decimal(12 - i),
                    gross_sales=Decimal((12 - i) * 100),
                    discount_total=Decimal(0),
                    net_sales=Decimal((12 - i) * 100),
                )
            )
        db.session.commit()
        window = resolve_window(db.session, [va.id], 2)
        top, bottom = item_movers(db.session, [va.id], window, limit=5)
        assert len(top) == 5 and len(bottom) == 5
        assert {m.item_name for m in top}.isdisjoint({m.item_name for m in bottom})
        # Venue A only: deltas are Item 11 (+1100) down to Item 7 (+300) on top,
        # Items 0–4 (−1100…−300) on the bottom; the scope-fixture items peak at
        # +112, so they never reach either board.
        assert [m.item_name for m in top] == [
            "Item 11", "Item 10", "Item 9", "Item 8", "Item 7"
        ]
        assert [m.item_name for m in bottom] == [
            "Item 0", "Item 1", "Item 2", "Item 3", "Item 4"
        ]
        assert all(m.delta < 0 for m in bottom)
