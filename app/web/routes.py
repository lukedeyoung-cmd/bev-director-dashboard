"""Web UI routes: group rollup and per-venue drill-down."""

import uuid

import sqlalchemy as sa
from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import login_required

from app.analytics import (
    category_mix,
    cost_summary,
    daily_trend,
    discount_watch,
    item_movers,
    resolve_window,
    sales_summary,
    venue_rankings,
)
from app.analytics.charts import line_chart, mix_bar
from app.extensions import db
from app.models.venue import Venue

web_bp = Blueprint("web", __name__)

# Range choices for the window selector (?days=7|14|28).
RANGE_CHOICES = (7, 14, 28)


def _requested_days() -> int:
    raw = request.args.get("days", "14")
    try:
        days = int(raw)
    except ValueError:
        return 14
    return days if days in RANGE_CHOICES else 14


def _active_venue_ids() -> list[uuid.UUID]:
    return list(
        db.session.scalars(sa.select(Venue.id).where(Venue.is_active)).all()
    )


def _range_links(days: int) -> list[tuple[int, str, bool]]:
    return [
        (option, url_for("web.dashboard", days=option), option == days)
        for option in RANGE_CHOICES
    ]


@web_bp.route("/")
@login_required
def index():
    return redirect(url_for("web.dashboard"))


@web_bp.route("/dashboard")
@login_required
def dashboard():
    days = _requested_days()
    venue_ids = _active_venue_ids()
    venues = db.session.scalars(sa.select(Venue).where(Venue.is_active).order_by(Venue.name)).all()
    data = _build_scope(venue_ids, days, range_links=_range_links(days))
    return render_template("dashboard.html", data=data, venues=venues, title="Group overview")


@web_bp.route("/venues/<uuid:venue_id>")
@login_required
def venue_detail(venue_id: uuid.UUID):
    venue = db.session.get(Venue, venue_id)
    if venue is None:
        abort(404)
    days = _requested_days()
    data = _build_scope([venue.id], days, range_links=None)
    venues = db.session.scalars(sa.select(Venue).where(Venue.is_active).order_by(Venue.name)).all()
    return render_template(
        "venue.html", data=data, venue=venue, venues=venues, title=venue.name
    )


def _build_scope(
    venue_ids: list[uuid.UUID], days: int, *, range_links: list | None
) -> dict:
    """Assemble every dashboard section for one scope (group or single venue)."""
    window = resolve_window(db.session, venue_ids, days)
    if window is None:
        return {"has_data": False, "days": days, "range_links": range_links}

    session = db.session
    summary = sales_summary(session, venue_ids, window.current)
    trend = daily_trend(session, venue_ids, window)
    mix = category_mix(session, venue_ids, window.current)
    cost = cost_summary(session, venue_ids, window.current)
    top_movers, bottom_movers = item_movers(session, venue_ids, window)

    return {
        "has_data": True,
        "days": days,
        "range_links": range_links,
        "window": window,
        "summary": summary,
        "trend": trend,
        "trend_svg": line_chart(trend),
        "mix": mix,
        "mix_svg": mix_bar(mix),
        "cost": cost,
        "rankings": venue_rankings(session, window.current) if len(venue_ids) > 1 else None,
        "top_movers": top_movers,
        "bottom_movers": bottom_movers,
        "discounts": discount_watch(session, venue_ids, window.current),
    }
