"""Environment-driven configuration."""

import os


def _env_flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    """Resolve the database URL, normalizing common postgres:// shorthand."""
    url = os.environ.get("DATABASE_URL", "sqlite:///bev-dashboard-dev.sqlite3")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Production serves over TLS (nginx); local HTTP dev opts out via env.
    SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE")
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 hours

    # --- Toast POS integration -------------------------------------------
    # Org-level machine client; each venue's restaurant GUID is stored on its
    # Venue row (pos_venue_ref). Rotating to L&SD's own client is a config
    # change only — nothing in the pipeline reads anything else.
    TOAST_API_HOST = os.environ.get("TOAST_API_HOST", "https://ws-api.toasttab.com")
    TOAST_CLIENT_ID = os.environ.get("TOAST_CLIENT_ID")
    TOAST_CLIENT_SECRET = os.environ.get("TOAST_CLIENT_SECRET")
    TOAST_REQUESTS_PER_SECOND = float(os.environ.get("TOAST_REQUESTS_PER_SECOND", "4"))
    TOAST_TIMEOUT_SECONDS = float(os.environ.get("TOAST_TIMEOUT_SECONDS", "30"))
    # Default depth for the historical sales backfill (CLI --start overrides).
    TOAST_BACKFILL_DAYS = int(os.environ.get("TOAST_BACKFILL_DAYS", "365"))

    # --- SendGrid digest email ---------------------------------------------
    # Daily 07:00 America/Chicago via scripts/morning_digest.sh (cron) or the
    # manual trigger POST /api/digest/send. The From address must be a verified
    # sender in the SendGrid account.
    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
    DIGEST_FROM_EMAIL = os.environ.get("DIGEST_FROM_EMAIL", "luke.deyoung@gmail.com")
    DIGEST_RECIPIENTS = [
        entry.strip()
        for entry in os.environ.get("DIGEST_RECIPIENTS", "luke.deyoung@gmail.com").split(",")
        if entry.strip()
    ]
    DIGEST_TIMEZONE = os.environ.get("DIGEST_TIMEZONE", "America/Chicago")
    # Placeholder until hosting lands; the email renders whatever is configured.
    DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://bev-director-dashboard.example")
    # Alert sensitivity (see app/digest/builder.py).
    DIGEST_SWING_PCT = float(os.environ.get("DIGEST_SWING_PCT", "40"))
    DIGEST_SWING_FLOOR = os.environ.get("DIGEST_SWING_FLOOR", "200")
    DIGEST_VARIANCE_PCT = float(os.environ.get("DIGEST_VARIANCE_PCT", "10"))
    DIGEST_PRICE_PCT = float(os.environ.get("DIGEST_PRICE_PCT", "10"))
