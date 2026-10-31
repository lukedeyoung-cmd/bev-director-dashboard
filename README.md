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
the `create-admin` CLI, an end-to-end `alembic upgrade head` → all 15 tables check,
and the Toast ingestion pipeline (category classification, client auth/pagination,
catalog + sales transforms, and idempotent DB upserts).

## Toast ingestion (POS)

Toast data lands in the same schema the dashboard reads: `pos_items` (catalog) and
`daily_sales` (one row per venue / business date / POS item). The pipeline lives in
`app/toast/`:

- `client.py` — machine-client auth (`POST /authentication/v1/authentication/login`,
  `userAccessType: TOAST_MACHINE_CLIENT`), per-request `Toast-Restaurant-External-ID`
  header, client-side throttling, 429 backoff, 401 re-auth, both pagination styles
  (numeric pages for orders v2 bulk, `Toast-Next-Page-Token` for config v2).
- `transforms.py` — pure functions: catalog extraction (menus v2 walks nested
  menu groups; config v2 fills gaps) and per-check order aggregation (modifiers as
  their own rows, voided orders/checks/selections excluded, selection-level
  discounts applied directly, check-level discounts allocated proportionally by
  line gross, item net floored at zero — comp amounts that include tax are
  tracked, not silently absorbed).
- `categories.py` — beverage category heuristics (`cocktail`, `wine`, `beer`,
  `spirit`, `na`, `food`, `other`) from menu-group path and item name.
- `ingest.py` — idempotent DB upserts. Catalog: upsert by `(venue_id, pos_guid)`;
  menus-v2 records are authoritative, config-v2/order-feed records only fill gaps.
  Sales: one transaction per business date — that date's `daily_sales` rows are
  replaced on every sync, so any date can be re-synced safely.

### Configuration

- `TOAST_CLIENT_ID` / `TOAST_CLIENT_SECRET` — the org-level Toast machine client
  (environment only, never the repo).
- Each venue's Toast restaurant GUID is stored on its `Venue` row
  (`pos_venue_ref`), set through the admin surface or a migration/seed.
- `TOAST_BACKFILL_DAYS` (default 365) — default depth when `toast-backfill` is
  run without `--start`.

> **Dev credential note:** current end-to-end verification uses a dev-only venue
> credential (Olivers), clearly labeled dev-only in the database. Production
> L&SD credentials replace them by environment/venue-config change only — no
> code changes.

### Operating the pipeline

```bash
# One venue's catalog (menus v2 + config v2)
flask --app wsgi:app toast-sync-catalog --venue OLV

# One venue, one business date (default: previous day in venue tz)
flask --app wsgi:app toast-sync-sales --venue OLV --business-date 2026-10-29

# Historical backfill (re-runnable; failed dates are skipped and reported)
flask --app wsgi:app toast-backfill --venue OLV --start 2025-11-01 --end 2026-10-30

# Nightly job across every connected venue (catalog + yesterday)
scripts/nightly_toast_sync.sh
```

`toast-sync` (the nightly job) syncs each venue independently — one venue failing
never blocks the others — and exits non-zero listing failures so cron alerting
catches them. Schedule it after closeout and after Toast's business-date
rollover, e.g. `15 6 * * *` America/Chicago (see
`scripts/nightly_toast_sync.sh`).

## Deployment notes

- No infrastructure is provisioned yet; deployment comes later.
- Serve with Gunicorn behind a TLS-terminating reverse proxy (nginx), mirroring
  the org's other apps.
- Run `alembic upgrade head` as part of release.
- Keep this system's data, credentials, and hosting strictly separate from all
  Bar Cost / CocktailCost systems.

## Status

Foundation complete: bootable app, auth, health/venues API, 15-table schema with a
verified initial migration, tests, and CI. Toast POS ingestion (catalog + sales,
backfill, nightly sync) is implemented and dev-verified end-to-end against a
dev-only Olivers credential: a full-year backfill (2025-10-31 → 2026-10-29) landed
30k+ `daily_sales` rows across 300 trading days with zero negative-net rows, and
the nightly wrapper runs clean. Sales/cost/purchasing/inventory modules and R365
ingestion are next.
