"""JSON API (session-authenticated; see README for programmatic access notes)."""

import sqlalchemy as sa
from flask import Blueprint
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
