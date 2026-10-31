"""Morning email digest: build, render, deliver.

Daily 07:00 America/Chicago via SendGrid (cron script + the same CLI command
the cron runs). Trigger paths:

- `flask --app wsgi:app send-digest` — the cron path (scripts/morning_digest.sh)
- `POST /api/digest/send` — the manual on-demand path (session + CSRF token)
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import render_template

from app.digest.builder import AlertThresholds, build_digest
from app.digest.mailer import DigestError, DigestSendError, send_digest_email
from app.extensions import db


def digest_subject(business_date: dt.date) -> str:
    """Email subject: Beverage Digest — Fri, Oct 30."""
    return f"L&SD Beverage Digest — {business_date:%a, %b %d}"


def render_digest_html(data, *, timezone_name: str = "America/Chicago") -> str:
    """Render the email HTML; requires an app context (template loader)."""
    generated_at = dt.datetime.now(ZoneInfo(timezone_name))
    return render_template("email/digest.html", data=data, generated_at=generated_at)


def _thresholds_from_config(app) -> AlertThresholds:
    cfg = app.config
    return AlertThresholds(
        swing_pct=float(cfg.get("DIGEST_SWING_PCT", 40.0)),
        swing_floor=Decimal(str(cfg.get("DIGEST_SWING_FLOOR", "200"))),
        variance_pct=float(cfg.get("DIGEST_VARIANCE_PCT", 10.0)),
        price_pct=float(cfg.get("DIGEST_PRICE_PCT", 10.0)),
    )


def send_morning_digest(app, business_date: dt.date | None = None) -> dict:
    """Build, render, and send the digest. Returns a summary dict for callers.

    Raises DigestError when there is no data to send or delivery fails —
    the CLI turns that into a non-zero exit (cron alerting), the API endpoint
    maps it to an error status.
    """
    cfg = app.config
    with app.app_context():
        data = build_digest(
            db.session,
            dashboard_url=cfg.get("DASHBOARD_URL", ""),
            business_date=business_date,
            thresholds=_thresholds_from_config(app),
        )
        if data is None:
            raise DigestError(
                "No sales data in the database yet — sync a venue first "
                "(flask --app wsgi:app toast-sync)."
            )
        assert data.business_date is not None  # implied by build_digest contract
        html = render_digest_html(
            data, timezone_name=cfg.get("DIGEST_TIMEZONE", "America/Chicago")
        )
        subject = digest_subject(data.business_date)

    send_digest_email(
        api_key=cfg.get("SENDGRID_API_KEY"),
        from_email=cfg.get("DIGEST_FROM_EMAIL", ""),
        recipients=list(cfg.get("DIGEST_RECIPIENTS", [])),
        subject=subject,
        html=html,
    )
    return {
        "subject": subject,
        "business_date": data.business_date,
        "recipients": len(cfg.get("DIGEST_RECIPIENTS", [])),
        "venues": len(data.venues),
        "alerts": len(data.alerts),
    }


def parse_iso_date(value: str) -> dt.date:
    """Strict YYYY-MM-DD parse; ValueError with a clear message otherwise."""
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid date {value!r} — expected YYYY-MM-DD") from exc


# Re-exports so callers can import the whole digest surface from one package.
__all__ = [
    "DigestError",
    "DigestSendError",
    "digest_subject",
    "parse_iso_date",
    "render_digest_html",
    "send_morning_digest",
]
