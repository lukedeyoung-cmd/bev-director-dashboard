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
