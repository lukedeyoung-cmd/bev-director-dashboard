"""JSON API (session-authenticated; see README for programmatic access notes)."""

import sqlalchemy as sa
from flask import Blueprint, Response, current_app, request
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.venue import Venue

api_bp = Blueprint("api", __name__)


@api_bp.get("/healthz")
def healthz():
    try:
        db.session.execute(sa.text("SELECT 1"))
    except SQLAlchemyError:
        db.session.rollback()
        return {"status": "degraded", "database": "unavailable"}, 503
    return {"status": "ok", "database": "ok"}


@api_bp.get("/venues")
@login_required
def list_venues():
    venues = db.session.scalars(
        sa.select(Venue).where(Venue.is_active).order_by(Venue.name)
    ).all()
    return {"venues": [{"id": str(v.id), "name": v.name, "code": v.code} for v in venues]}


@api_bp.post("/digest/send")
@login_required
def digest_send():
    """Manual digest trigger: build and send the morning email now.

    Optional JSON body: {"business_date": "YYYY-MM-DD"}. Default anchor is the
    newest business date present in sales data — the same as the 7:00 AM cron.
    CSRF applies (browser callers send X-CSRFToken).
    """
    from app.digest import DigestError, DigestSendError, parse_iso_date, send_morning_digest

    business_date = None
    payload = request.get_json(silent=True) or {}
    if payload.get("business_date"):
        try:
            business_date = parse_iso_date(str(payload["business_date"]))
        except ValueError as exc:
            return {"error": str(exc)}, 400

    try:
        summary = send_morning_digest(current_app, business_date=business_date)
    except DigestSendError as exc:
        return {"error": f"Delivery failed: {exc}"}, 502
    except DigestError as exc:
        return {"error": str(exc)}, 400

    summary["business_date"] = (
        summary["business_date"].isoformat() if summary["business_date"] else None
    )
    return {"status": "sent", **summary}


@api_bp.get("/digest/preview")
@login_required
def digest_preview():
    """Render the digest HTML without sending (visual check on demand)."""
    from app.digest import render_digest_html
    from app.digest.builder import build_digest

    data = build_digest(db.session, dashboard_url=current_app.config.get("DASHBOARD_URL", ""))
    if data is None:
        return {"error": "No sales data in the database yet — sync a venue first."}, 400
    html = render_digest_html(
        data, timezone_name=current_app.config.get("DIGEST_TIMEZONE", "America/Chicago")
    )
    return Response(html, mimetype="text/html")
