"""Ingest orchestration tests against the test DB (sqlite via conftest)."""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import DailySales, PosItem, Venue
from app.toast.ingest import (
    backfill,
    get_venue_by_code,
    sync_business_date,
    sync_catalog,
    venues_with_pos_refs,
)
from tests.toast_payloads import (
    CONFIG_ITEMS,
    MENUS_PAYLOAD,
    applied_discount,
    check,
    order,
    selection,
)


class StubToastClient:
    """In-memory stand-in for ToastClient with the same surface."""

    def __init__(self, menus=MENUS_PAYLOAD, config_items=None, orders=None):
        self.menus = menus
        self.config_items = config_items or []
        self.orders_by_date: dict[str, list[dict]] = orders or {}

    def fetch_menus_v2(self):
        return self.menus

    def iter_config_menu_items(self):
        yield from self.config_items

    def iter_orders_bulk(self, business_date: str, page_size: int = 100):
        # Shape matches ToastClient's generator contract (items, not pages).
        yield from self.orders_by_date.get(business_date, [])


@pytest.fixture()
def venue(app):  # the app fixture provides a fresh DB per test
    venue = Venue(
        name="Olivers (dev-only)",
        code="OLV-DEV",
        pos_venue_ref="rest-guid",
        timezone="America/Chicago",
    )
    db.session.add(venue)
    db.session.commit()
    return venue


def basic_orders():
    return [
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


# ----------------------------------------------------------------------
# Catalog sync
# ----------------------------------------------------------------------
def test_sync_catalog_creates_and_is_idempotent(venue):
    stub = StubToastClient(config_items=CONFIG_ITEMS)
    first = sync_catalog(venue, stub)
    assert first.items_created == 6  # 4 menus items + Burger + Extra Shot

    second = sync_catalog(venue, stub)
    assert second.items_created == 0

    items = {i.pos_guid: i for i in db.session.scalars(sa.select(PosItem))}
    assert items["item-beer"].category == "beer"
    assert items["item-beer"].name == "La Chouffe"  # menus wins over config name
    assert items["item-config-only"].name == "Burger"


def test_get_venue_by_code_and_pos_ref_filter(venue):
    assert get_venue_by_code("OLV-DEV").id == venue.id
    with pytest.raises(LookupError):
        get_venue_by_code("NOPE")
    assert venues_with_pos_refs() == [venue]


# ----------------------------------------------------------------------
# Sales sync
# ----------------------------------------------------------------------
def test_sync_business_date_writes_daily_sales(venue):
    stub = StubToastClient(orders={"20261029": basic_orders()})
    result = sync_business_date(venue, stub, date(2026, 10, 29))
    assert result.rows_written == 2
    assert result.orders_seen == 1

    items = {i.id: i for i in db.session.scalars(sa.select(PosItem))}
    rows = {
        items[r.pos_item_id].name: r for r in db.session.scalars(sa.select(DailySales))
    }
    assert len(rows) == 2
    old_fashioned = rows["Old Fashioned"]
    assert old_fashioned.quantity_sold == Decimal("2")
    assert old_fashioned.gross_sales == Decimal("30.00")
    assert old_fashioned.net_sales == Decimal("30.00")
    assert old_fashioned.category == "cocktail"  # name-based fallback


def test_sync_business_date_is_idempotent_and_replaces(venue):
    stub = StubToastClient(orders={"20261029": basic_orders()})
    sync_business_date(venue, stub, date(2026, 10, 29))
    assert len(list(db.session.scalars(sa.select(DailySales)))) == 2

    # Re-run with different data: rows are replaced, not duplicated.
    changed = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection("s1", "i1", "Old Fashioned", 1, 15.0),
                        selection("s2", "i2", "La Chouffe", 1, 10.0),
                        selection("s3", "i3", "Coffee", 1, 7.0),
                    ],
                )
            ],
        )
    ]
    stub2 = StubToastClient(orders={"20261029": changed})
    result = sync_business_date(venue, stub2, date(2026, 10, 29))
    assert result.rows_written == 3
    rows = list(db.session.scalars(sa.select(DailySales)))
    assert len(rows) == 3
    of_id = db.session.scalar(sa.select(PosItem.id).where(PosItem.name == "Old Fashioned"))
    old_fashioned = next(r for r in rows if r.pos_item_id == of_id)
    assert old_fashioned.quantity_sold == Decimal("1")
    assert old_fashioned.gross_sales == Decimal("15.00")


def test_sync_applies_discounts_and_voids(venue):
    orders_payload = [
        order(
            "o1",
            [
                check(
                    "c1",
                    [
                        selection("s1", "i1", "Old Fashioned", 1, 15.0),
                        selection("s2", "i2", "La Chouffe", 1, 10.0, voided=True),
                    ],
                    discounts=[applied_discount(15.0)],
                )
            ],
        ),
        order(
            "o2",
            [check("c2", [selection("s3", "i3", "Coffee", 1, 7.0)])],
            voided=True,
        ),
    ]
    stub = StubToastClient(orders={"20261029": orders_payload})
    result = sync_business_date(venue, stub, date(2026, 10, 29))
    assert result.rows_written == 1
    assert result.orders_voided == 1
    assert result.selections_voided == 1
    row = db.session.scalar(sa.select(DailySales))
    assert row.discount_total == Decimal("15.00")
    assert row.net_sales == Decimal("0.00")


def test_backfill_skips_failed_dates(venue, monkeypatch):
    stub = StubToastClient(orders={"20261029": basic_orders()})
    original = stub.iter_orders_bulk

    def flaky(business_date, page_size=100):
        if business_date == "20261028":
            raise RuntimeError("Toast retention error")
        yield from original(business_date, page_size)

    monkeypatch.setattr(stub, "iter_orders_bulk", flaky)
    results, failures = backfill(venue, stub, date(2026, 10, 28), date(2026, 10, 29))
    assert len(results) == 1
    assert set(failures) == {date(2026, 10, 28)}
    # The good date still landed.
    assert db.session.scalar(sa.select(sa.func.count(DailySales.id))) == 2
