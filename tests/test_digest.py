"""Digest tests: mailer payload handling, builder math, template, endpoints, CLI."""

import datetime as dt
from decimal import Decimal

import pytest

from app.digest import DigestSendError, send_digest_email
from app.digest.builder import build_digest
from app.extensions import db
from app.models.catalog import PosItem
from app.models.sales import DailySales, DailyVenueTotals
from app.models.venue import Venue

ANCHOR = dt.date(2026, 3, 10)
PRIOR = dt.date(2026, 3, 9)


def _login(client, user):
    return client.post(
        "/login",
        data={"email": user.email, "password": "test-password-123"},
        follow_redirects=False,
    )


def _seed_sales(venue, pos_item, business_date, qty, gross, discount, net):
    db.session.add(
        DailySales(
            venue_id=venue.id,
            business_date=business_date,
            pos_item_id=pos_item.id,
            category=pos_item.category,
            quantity_sold=Decimal(str(qty)),
            gross_sales=Decimal(str(gross)),
            discount_total=Decimal(str(discount)),
            net_sales=Decimal(str(net)),
        )
    )


def _seed_covers(venue, business_date, checks):
    db.session.add(
        DailyVenueTotals(venue_id=venue.id, business_date=business_date, check_count=checks)
    )


@pytest.fixture()
def two_venues(app):
    """Harborline (moderate swing) + Dockside (huge swing) with two days of sales.

    Anchor 3/10: Harborline IPA net 950 (gross 1000, disc 50), wine net 500;
    Dockside net 1000. Prior 3/9: Harborline IPA 500, wine 800; Dockside 100.
    """
    harbor = Venue(name="Harborline Public House", code="HAR")
    dock = Venue(name="Dockside Taproom", code="DOC")
    db.session.add_all([harbor, dock])
    db.session.flush()

    ipa = PosItem(
        venue_id=harbor.id,
        pos_guid="g-ipa",
        name="Draft IPA",
        item_type="menu_item",
        category="beer",
        unit_price=Decimal("8.00"),
    )
    wine = PosItem(
        venue_id=harbor.id,
        pos_guid="g-wine",
        name="House Wine",
        item_type="menu_item",
        category="wine",
        unit_price=Decimal("12.00"),
    )
    dock_pint = PosItem(
        venue_id=dock.id,
        pos_guid="g-pint",
        name="Pilsner Pint",
        item_type="menu_item",
        category="beer",
        unit_price=Decimal("7.00"),
    )
    db.session.add_all([ipa, wine, dock_pint])
    db.session.flush()

    _seed_sales(harbor, ipa, ANCHOR, 100, 1000, 50, 950)
    _seed_sales(harbor, wine, ANCHOR, 40, 500, 0, 500)
    _seed_sales(harbor, ipa, PRIOR, 60, 500, 0, 500)
    _seed_sales(harbor, wine, PRIOR, 65, 800, 0, 800)
    _seed_sales(dock, dock_pint, ANCHOR, 143, 1000, 0, 1000)
    _seed_sales(dock, dock_pint, PRIOR, 14, 100, 0, 100)

    _seed_covers(harbor, ANCHOR, 120)
    _seed_covers(harbor, PRIOR, 80)
    _seed_covers(dock, ANCHOR, 80)
    _seed_covers(dock, PRIOR, 10)
    db.session.commit()
    return harbor, dock


# --- mailer ----------------------------------------------------------------


def test_mailer_requires_api_key():
    with pytest.raises(DigestSendError, match="SENDGRID_API_KEY"):
        send_digest_email(
            api_key=None,
            from_email="luke.deyoung@gmail.com",
            recipients=["luke.deyoung@gmail.com"],
            subject="t",
            html="<p>x</p>",
        )


def test_mailer_requires_recipients():
    with pytest.raises(DigestSendError, match="recipients"):
        send_digest_email(
            api_key="k",
            from_email="luke.deyoung@gmail.com",
            recipients=[],
            subject="t",
            html="<p>x</p>",
        )


def test_mailer_builds_sendgrid_payload(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 202
        text = ""

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("app.digest.mailer.requests.post", fake_post)
    send_digest_email(
        api_key="secret",
        from_email="luke.deyoung@gmail.com",
        recipients=["a@example.com", "b@example.com"],
        subject="Digest",
        html="<p>hi</p>",
    )
    assert calls["url"] == "https://api.sendgrid.com/v3/mail/send"
    assert calls["headers"]["Authorization"] == "Bearer secret"
    assert calls["json"]["from"]["email"] == "luke.deyoung@gmail.com"
    assert calls["json"]["subject"] == "Digest"
    assert [r["email"] for r in calls["json"]["personalizations"][0]["to"]] == [
        "a@example.com",
        "b@example.com",
    ]
    assert calls["json"]["content"] == [{"type": "text/html", "value": "<p>hi</p>"}]


def test_mailer_surfaces_api_error(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = '{"errors": [{"message": "sender not verified"}]}'

    monkeypatch.setattr(
        "app.digest.mailer.requests.post", lambda *a, **k: FakeResponse()
    )
    with pytest.raises(DigestSendError, match="sender not verified"):
        send_digest_email(
            api_key="k",
            from_email="luke.deyoung@gmail.com",
            recipients=["a@example.com"],
            subject="t",
            html="<p>x</p>",
        )


def test_mailer_surfaces_connection_error(monkeypatch):
    import requests as requests_lib

    def fake_post(*a, **k):
        raise requests_lib.ConnectionError("no route to host")

    monkeypatch.setattr("app.digest.mailer.requests.post", fake_post)
    with pytest.raises(DigestSendError, match="no route"):
        send_digest_email(
            api_key="k",
            from_email="luke.deyoung@gmail.com",
            recipients=["a@example.com"],
            subject="t",
            html="<p>x</p>",
        )


# --- builder ---------------------------------------------------------------


def test_build_digest_no_data(app):
    assert build_digest(db.session, dashboard_url="https://x") is None


def test_build_digest_venue_rows(app, two_venues):
    data = build_digest(db.session, dashboard_url="https://x")
    assert data is not None
    assert data.business_date == ANCHOR
    assert data.cost_data_present is False

    by_code = {v.code: v for v in data.venues}
    harbor_row = by_code["HAR"]
    assert harbor_row.net_sales == Decimal("1450")
    assert harbor_row.covers == 120
    assert harbor_row.avg_check == (Decimal("1450") / 120).quantize(Decimal("0.01"))
    assert harbor_row.void_comp_total == Decimal("50")
    assert harbor_row.change_pct == pytest.approx((1450 - 1300) / 1300 * 100)

    # Group KPIs aggregate both venues.
    assert data.group_net == Decimal("2450")
    assert data.group_covers == 200

    # Movers sorted by delta: Pilsner +900 up, wine -300 down; new-item spikes
    # (no prior sales) are excluded.
    assert [(m.item_name, m.delta) for m in data.movers_up] == [
        ("Pilsner Pint", Decimal("900")),
        ("Draft IPA", Decimal("450")),
    ]
    assert [(m.item_name, m.delta) for m in data.movers_down] == [
        ("House Wine", Decimal("-300"))
    ]

    # Three trend windows, newest first, COGS pending cost data.
    assert [row.label for row in data.trends] == [
        "Last 7 days",
        "Last 14 days",
        "Last 28 days",
    ]
    week = data.trends[0]
    # HAR 1450 + 1300, DOC 1000 + 100 — all seeded rows fall in the last 7 days.
    assert week.net_sales == Decimal("3850")
    # Prior window has no rows at all -> None (email omits the delta line).
    assert week.prior_net_sales is None
    assert week.change_pct is None
    assert week.cogs_pct is None
    assert week.mix_text == "Beer 66% · Wine 34%"


def test_build_digest_sales_swing_alert(app, two_venues):
    """Swing = venue day far from its trailing-28-day daily mean (above the $ floor).

    Dockside's baseline mean is ~$293/day (floor $200 clears) and its anchor day
    is +241% — flagged. Harborline's mean is ~$1,107/day and its anchor day is
    +31% (under the 40% threshold) — not flagged.
    """
    harbor, dock = two_venues
    baseline_item = PosItem(
        venue_id=dock.id,
        pos_guid="g-baseline-doc",
        name="Baseline Pour",
        item_type="menu_item",
        category="beer",
        unit_price=Decimal("7.00"),
    )
    harbor_baseline = PosItem(
        venue_id=harbor.id,
        pos_guid="g-baseline-har",
        name="Baseline Pour",
        item_type="menu_item",
        category="beer",
        unit_price=Decimal("8.00"),
    )
    db.session.add_all([baseline_item, harbor_baseline])
    db.session.flush()

    # 27 baseline days (Feb 10 - Mar 8); Mar 9 is already seeded by the fixture.
    for offset in range(1, 28):
        day = ANCHOR - dt.timedelta(days=offset)
        _seed_sales(dock, baseline_item, day, 43, 300, 0, 300)
        _seed_sales(harbor, harbor_baseline, day, 137, 1100, 0, 1100)
    db.session.commit()

    data = build_digest(db.session, dashboard_url="https://x")
    assert data is not None
    kinds = [a.kind for a in data.alerts]
    assert "sales_swing" in kinds
    dock_alerts = [a for a in data.alerts if "DOC" in a.message]
    assert dock_alerts, "Dockside's +241% swing should be flagged"
    assert "Harborline" not in " ".join(a.message for a in data.alerts)


# --- template --------------------------------------------------------------


def test_render_digest_html_contains_sections(app, two_venues):
    from app.digest import render_digest_html

    data = build_digest(db.session, dashboard_url="https://dash.example")
    assert data is not None
    html = render_digest_html(data)
    assert "L&SD Beverage Digest" in html
    assert "Harborline Public House" in html
    assert "Draft IPA" in html
    assert "Trailing trends" in html
    assert "Beer 66% · Wine 34%" in html
    assert "COGS%" in html
    assert "Alerts" in html
    assert 'href="https://dash.example"' in html


def test_render_digest_html_empty_alerts(app, two_venues):
    from app.digest import render_digest_html
    from app.digest.builder import AlertThresholds

    data = build_digest(
        db.session,
        dashboard_url="",
        thresholds=AlertThresholds(swing_pct=10_000.0, swing_floor=Decimal("999999")),
    )
    assert data is not None
    html = render_digest_html(data)
    assert "No anomalies flagged." in html
    assert "once cost data lands" in html  # graceful cost-data notice


# --- endpoints -------------------------------------------------------------


def test_digest_endpoints_require_login(client):
    assert client.post("/api/digest/send").status_code == 302
    assert client.get("/api/digest/preview").status_code == 302


def test_digest_send_without_data(client, user):
    assert _login(client, user).status_code == 302
    response = client.post("/api/digest/send")
    assert response.status_code == 400
    assert "No sales data" in response.get_json()["error"]


def test_digest_send_sends_email(client, user, app, two_venues, monkeypatch):
    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr("app.digest.send_digest_email", fake_send)
    assert _login(client, user).status_code == 302
    response = client.post("/api/digest/send", json={"business_date": "2026-03-10"})

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["status"] == "sent"
    assert body["business_date"] == "2026-03-10"
    assert body["venues"] == 2
    assert sent["from_email"] == "luke.deyoung@gmail.com"
    assert sent["recipients"] == ["luke.deyoung@gmail.com"]
    assert "Mar 10" in sent["subject"]


def test_digest_send_rejects_bad_date(client, user):
    assert _login(client, user).status_code == 302
    response = client.post("/api/digest/send", json={"business_date": "03/10/2026"})
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.get_json()["error"]


def test_digest_preview_renders_html(client, user, two_venues):
    assert _login(client, user).status_code == 302
    response = client.get("/api/digest/preview")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "Harborline Public House" in response.get_data(as_text=True)


# --- CLI -------------------------------------------------------------------


def test_send_digest_cli_without_data(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["send-digest"])
    assert result.exit_code != 0
    assert "No sales data" in result.output
