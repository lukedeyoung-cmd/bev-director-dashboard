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
