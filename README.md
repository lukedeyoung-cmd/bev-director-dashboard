# L&SD Beverage Director Dashboard

Standalone analytics for Land & Sea Dept's multi-venue beverage program: group-wide
sales and category mix, purchasing, inventory, recipes, and actual-vs-theoretical
pour cost. This project is fully independent of all Bar Cost / CocktailCost systems —
separate repository, database, credentials, and hosting.

## Stack

- **Backend:** Python 3.12+, Flask 3 (application-factory pattern), Gunicorn
- **Database:** PostgreSQL (SQLAlchemy 2, Alembic migrations)
- **Auth:** Flask-Login session auth, Werkzeug password hashing, Flask-WTF CSRF
- **Frontend:** Server-rendered Jinja2 + the Obvious design system (semantic CSS
  tokens, light/dark), no build step
- **CI:** GitHub Actions — ruff, Postgres migration cycle, pytest

## Project layout

```
app/                 Flask application
  __init__.py        create_app() factory
  config.py          environment-driven configuration
  extensions.py      db, login_manager
  models/            SQLAlchemy models (15 tables)
  routes/            health/venues API, auth, dashboard
  templates/         Jinja2 templates
  static/            design-system CSS + theme JS
migrations/          Alembic environment + versions/
tests/               pytest suite (fixtures, auth, health, CLI, migrations)
wsgi.py              Gunicorn entry point (wsgi:app)
alembic.ini
```

## Local setup

Requires Python 3.12+ and PostgreSQL 13+ running locally.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Configure (or copy .env.example to .env — same variables)
export DATABASE_URL="postgresql+psycopg://bev:bev@localhost:5432/bev"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SESSION_COOKIE_SECURE="true"   # false for plain-HTTP local dev

# Create the schema
.venv/bin/alembic upgrade head

# Create the first admin user
.venv/bin/flask --app wsgi:app create-admin --email you@example.com --name "Your Name"

# Optional: seed clearly-marked synthetic demo data (fx- prefix, not real sales)
.venv/bin/flask --app wsgi:app seed-fixtures            # skip if fixture venues exist
.venv/bin/flask --app wsgi:app seed-fixtures --reset    # replace fixture rows

# Run
.venv/bin/flask --app wsgi:app run --debug          # development
.venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 wsgi:app  # production-style
```

Sign in at http://127.0.0.1:8000/login.

### Environment variables

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://user:pass@host:5432/db` |
| `SECRET_KEY` | yes | Session signing key; app refuses to boot without it |
| `SESSION_COOKIE_SECURE` | no | Default `true` (set `false` for local HTTP) |
| `FLASK_DEBUG` | no | Set `1` for development autoreload |

## Migrations

```bash
.venv/bin/alembic upgrade head                       # apply
.venv/bin/alembic downgrade -1                       # step back
# After editing models in app/models/:
DATABASE_URL="sqlite:///./instance/autogen.sqlite3" \
  .venv/bin/alembic revision --autogenerate -m "describe change"
```

The Alembic environment picks the database from `DATABASE_URL` and falls back to
`alembic.ini`. Migrations run against Postgres in CI on every push.

## Tests

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
```

The suite covers boot/config, the health endpoint, auth (login/logout/redirects),
the `create-admin` CLI, and an end-to-end `alembic upgrade head` → all 15 tables check.

## Deployment notes

- No infrastructure is provisioned yet; deployment comes later.
- Serve with Gunicorn behind a TLS-terminating reverse proxy (nginx), mirroring
  the org's other apps.
- Run `alembic upgrade head` as part of release.
- Keep this system's data, credentials, and hosting strictly separate from all
  Bar Cost / CocktailCost systems.

## Status

Foundation complete: bootable app, auth, health/venues API, 15-table schema with a
verified initial migration, tests, and CI. Sales/cost/purchasing/inventory modules
and data ingestion (Toast, Restaurant365) are planned next.
