"""Coverage math: share of net sales behind approved mappings."""

from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.extensions import db
from app.mapping.coverage import category_group_coverage, coverage_for_venues, group_coverage
from app.models.catalog import PosItem
from app.models.mapping import ToastItemMap
from app.models.sales import DailySales
from app.models.venue import Venue

_start = date(2026, 1, 1)


def _seed_venue_sales(app, rows, *, approve=None):
    """rows: [(pos_name, category, net_sales)]; approve: subset of pos names.

    Returns the venue id.
    """
    with app.app_context():
        venue = Venue(name="Test Venue", code="TV")
        db.session.add(venue)
        db.session.flush()
        items = {}
        for offset, (pos_name, category, net_sales) in enumerate(rows):
            item = PosItem(
                venue_id=venue.id, pos_guid=f"guid-{pos_name}", name=pos_name, item_type="menu_item"
            )
            db.session.add(item)
            db.session.flush()
            items[pos_name] = item
            db.session.add(
                DailySales(
                    venue_id=venue.id,
                    business_date=_start + timedelta(days=offset),
                    pos_item_id=item.id,
                    category=category,
                    quantity_sold=Decimal("1"),
                    gross_sales=Decimal(net_sales),
                    discount_total=Decimal("0"),
                    net_sales=Decimal(net_sales),
                )
            )
        for pos_name in approve or []:
            db.session.add(
                ToastItemMap(
                    pos_item_id=items[pos_name].id,
                    r365_item_id=items[pos_name].id,  # target id is opaque here
                    status="approved",
                )
            )
        db.session.commit()
        return venue.id


def test_coverage_counts_approved_only(app):
    _seed_venue_sales(
        app,
        [
            ("Titos Soda", "cocktail", "100"),
            ("Coors", "beer", "100"),
        ],
        approve=["Titos Soda"],
    )
    with app.app_context():
        rows = coverage_for_venues(db.session)
        assert len(rows) == 1
        (row,) = rows
        assert row.total_net_sales == Decimal("200")
        assert row.covered_net_sales == Decimal("100")
        assert row.coverage_pct == 50.0


def test_suggested_mappings_do_not_count(app):
    _seed_venue_sales(app, [("Coors", "beer", "100")])
    with app.app_context():
        db.session.scalars(sa.select(Venue)).one()
        item = db.session.scalars(sa.select(PosItem)).one()
        db.session.add(
            ToastItemMap(pos_item_id=item.id, r365_item_id=item.id, status="suggested")
        )
        db.session.commit()

        (row,) = coverage_for_venues(db.session)
        assert row.covered_net_sales == Decimal("0")
        assert row.coverage_pct == 0.0


def test_empty_slice_has_no_sales_means_none_not_zero(app):
    # A venue with zero sales has vacuous coverage, not 0%.
    with app.app_context():
        db.session.add(Venue(name="Quiet Venue", code="QV"))
        db.session.commit()
        rows = coverage_for_venues(db.session)
        assert rows == []


def test_group_coverage_sums_venue_rows():
    from app.mapping.coverage import VenueCoverage

    a = VenueCoverage(
        venue_id=None, venue_name="A", total_net_sales=Decimal("100"),
        covered_net_sales=Decimal("50"), coverage_pct=50.0,
    )
    b = VenueCoverage(
        venue_id=None, venue_name="B", total_net_sales=Decimal("0"),
        covered_net_sales=Decimal("0"), coverage_pct=None,
    )
    group = group_coverage([a, b])
    assert group.total_net_sales == Decimal("100")
    assert group.covered_net_sales == Decimal("50")
    assert group.coverage_pct == 50.0


def test_category_group_coverage_merges_categories_across_venues():
    from app.mapping.coverage import SliceCoverage, VenueCoverage

    a = VenueCoverage(
        venue_id=None, venue_name="A", total_net_sales=Decimal("100"),
        covered_net_sales=Decimal("100"), coverage_pct=100.0,
        categories=[SliceCoverage("beer", Decimal("100"), Decimal("100"), 100.0)],
    )
    b = VenueCoverage(
        venue_id=None, venue_name="B", total_net_sales=Decimal("100"),
        covered_net_sales=Decimal("0"), coverage_pct=0.0,
        categories=[SliceCoverage("beer", Decimal("100"), Decimal("0"), 0.0)],
    )
    rows = category_group_coverage([a, b])
    assert len(rows) == 1
    assert rows[0].label == "beer"
    assert rows[0].coverage_pct == 50.0
