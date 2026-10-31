"""Fixture-seeder tests: determinism, markings, idempotency, and reset."""

from datetime import date

import sqlalchemy as sa

from app.extensions import db
from app.fixtures import seed_fixtures
from app.models.catalog import PosItem
from app.models.sales import DailySales
from app.models.venue import Venue


def test_seeder_creates_marked_deterministic_data(app):
    with app.app_context():
        counts = seed_fixtures(end_date=date(2026, 3, 10))
        assert counts["venues"] == 4
        assert counts["items"] == 31
        assert counts["maps"] == 40
        assert counts["invoices"] == 4

        # Every fixture POS guid carries the fx- marker.
        guids = db.session.scalars(sa.select(PosItem.pos_guid)).all()
        assert guids and all(g.startswith("fx-") for g in guids)

        venue_names = db.session.scalars(sa.select(Venue.name)).all()
        assert "Harborline Public House" in venue_names

        # Sales volume: 28 days × 31 items, minus any tiny-venue-day skips.
        rows = db.session.scalar(sa.select(sa.func.count()).select_from(DailySales))
        assert rows >= 800


def test_seeder_is_idempotent(app):
    with app.app_context():
        first = seed_fixtures(end_date=date(2026, 3, 10))
        assert not first.get("skipped")
        venues_before = db.session.scalar(sa.select(sa.func.count()).select_from(Venue))

        second = seed_fixtures(end_date=date(2026, 3, 10))
        assert second == {"skipped": True}
        venues_after = db.session.scalar(sa.select(sa.func.count()).select_from(Venue))
        assert venues_before == venues_after


def test_reset_replaces_fixture_rows(app):
    with app.app_context():
        seed_fixtures(end_date=date(2026, 3, 10))
        old_ids = set(db.session.scalars(sa.select(Venue.id)).all())

        counts = seed_fixtures(end_date=date(2026, 3, 11), reset=True)
        assert counts["venues"] == 4
        new_ids = set(db.session.scalars(sa.select(Venue.id)).all())
        assert old_ids.isdisjoint(new_ids)
        assert len(new_ids) == 4
