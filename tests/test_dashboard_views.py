"""Dashboard view tests: auth gating, KPI rendering, cost gating, empty states."""

import uuid
from datetime import date, timedelta

import sqlalchemy as sa

from app.extensions import db
from app.fixtures import seed_fixtures
from app.models.catalog import PosItem
from app.models.sales import DailySales
from app.models.venue import Venue


def _login(client, user):
    return client.post(
        "/login",
        data={"email": user.email, "password": "test-password-123"},
        follow_redirects=False,
    )


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_dashboard_empty_state(client, user):
    assert _login(client, user).status_code == 302
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "No sales data yet." in response.text


def test_dashboard_renders_fixture_data(client, user, app):
    with app.app_context():
        seed_fixtures(end_date=date(2026, 3, 10))
    assert _login(client, user).status_code == 302

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Group overview" in response.text
    assert "Harborline Public House" in response.text  # venue ranking
    assert "Bev sales · net" in response.text
    assert "Venue ranking" in response.text
    assert "Top movers" in response.text
    assert "Voids &amp; comps watch" in response.text
    # Group scope has cost data (two venues carry invoices) -> cost KPIs live.
    assert "Bev COGS%" in response.text
    assert "Pour cost" in response.text
    assert "Variance (A − T)" in response.text
    # SVG charts rendered server-side.
    assert "<svg" in response.text


def test_group_page_hides_cost_kpis_without_cost_data(client, user, app):
    """Sales data with no invoices/maps: sales KPIs render, cost KPIs don't."""
    with app.app_context():
        venue = Venue(name="Sales Only", code="SLO")
        db.session.add(venue)
        db.session.flush()
        item = PosItem(
            venue_id=venue.id,
            pos_guid="slo-1",
            name="Draft IPA",
            item_type="menu_item",
            category="beer",
            unit_price=8,
        )
        db.session.add(item)
        db.session.flush()  # assign item.id before the sales row references it
        db.session.add(
            DailySales(
                venue_id=venue.id,
                business_date=date(2026, 3, 10),
                pos_item_id=item.id,
                category="beer",
                quantity_sold=10,
                gross_sales=80,
                discount_total=0,
                net_sales=80,
            )
        )
        db.session.commit()

    assert _login(client, user).status_code == 302
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Bev sales · net" in response.text
    assert "$80" in response.text
    assert "Cost KPIs pending R365 connection" in response.text
    # Live-only cost strings: the pour-cost card's sub and the variance sub.
    assert "theoretical cost" not in response.text
    assert "over theoretical" not in response.text


def test_venue_drill_down_with_and_without_cost_data(client, user, app):
    with app.app_context():
        seed_fixtures(end_date=date(2026, 3, 10))
        rows = db.session.execute(sa.select(Venue.code, Venue.id)).all()
        venues = dict(rows)

    assert _login(client, user).status_code == 302

    # Harborline carries fixture cost data -> all four KPI cards.
    response = client.get(f"/venues/{venues['HLP']}")
    assert response.status_code == 200
    assert "Harborline Public House" in response.text
    assert "Bev COGS%" in response.text
    assert "Toast connected" in response.text

    # Fern & Fiddle has sales but no cost data -> cost KPIs stay dark.
    response = client.get(f"/venues/{venues['FFD']}")
    assert response.status_code == 200
    assert "Fern &amp; Fiddle" in response.text
    assert "Cost KPIs pending R365 connection" in response.text
    # Live-only cost strings absent: no pour-cost sub, no variance sub.
    assert "theoretical cost" not in response.text
    assert "over theoretical" not in response.text


def test_venue_drill_down_range_links(client, user, app):
    with app.app_context():
        seed_fixtures(end_date=date(2026, 3, 10))
        venue_id = db.session.scalar(sa.select(Venue.id).where(Venue.code == "HLP"))
    assert _login(client, user).status_code == 302
    response = client.get(f"/venues/{venue_id}?days=7")
    assert response.status_code == 200
    assert 'href="/venues/' in response.text  # venue-scoped pills


def test_unknown_venue_404(client, user):
    _login(client, user)
    response = client.get(f"/venues/{uuid.uuid4()}")
    assert response.status_code == 404


def test_days_param_normalized(client, user, app):
    with app.app_context():
        seed_fixtures(end_date=date(2026, 3, 10))
    _login(client, user)
    assert client.get("/dashboard?days=7").status_code == 200
    assert client.get("/dashboard?days=999").status_code == 200  # falls back to 14
    assert client.get("/dashboard?days=bogus").status_code == 200


def test_seeded_data_anchors_at_requested_end_date(app):
    """The seeder anchors history at its end_date, so windows are never stale."""
    with app.app_context():
        end = date.today() - timedelta(days=1)
        seed_fixtures(end_date=end)
        anchor = db.session.scalar(sa.select(sa.func.max(DailySales.business_date)))
        assert anchor == end
