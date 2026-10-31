"""Web UI routes."""

import sqlalchemy as sa
from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required

from app.extensions import db
from app.models.venue import Venue

web_bp = Blueprint("web", __name__)

# Dashboard modules the data pipeline will light up as each source connects.
MODULES = [
    {
        "title": "Sales & mix",
        "source": "Toast",
        "description": "Daily beverage sales, category mix, top movers, voids and comps by venue.",
    },
    {
        "title": "Cost & variance",
        "source": "R365 + mapping",
        "description": "Actual vs theoretical pour cost across the portfolio.",
    },
    {
        "title": "Purchasing",
        "source": "R365",
        "description": "AP invoices and purchase orders with supplier price movement.",
    },
    {
        "title": "Inventory",
        "source": "R365 / counts",
        "description": "Inventory value, turns, and days on hand.",
    },
    {
        "title": "Item mapping",
        "source": "Internal",
        "description": "POS item → ingredient links that power costing.",
    },
]


@web_bp.route("/")
@login_required
def index():
    return redirect(url_for("web.dashboard"))


@web_bp.route("/dashboard")
@login_required
def dashboard():
    venues = db.session.scalars(sa.select(Venue).order_by(Venue.name)).all()
    return render_template("dashboard.html", venues=venues, modules=MODULES)
