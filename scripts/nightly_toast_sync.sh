#!/usr/bin/env bash
# Nightly Toast ingestion for the L&SD Beverage Director Dashboard.
#
# Syncs the catalog and yesterday's sales for every venue with pos_venue_ref
# set. Schedule after venue closeout and after Toast's business-date rollover
# (e.g. 06:15 America/Chicago) so the previous day is complete.
#
# Production crontab entry (server where the app is deployed):
#   15 6 * * * /opt/bev-director-dashboard/scripts/nightly_toast_sync.sh >> /var/log/toast-sync.log 2>&1
#
# Credentials come from the deployment environment (or .env) — never edit them
# into this script.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bev-director-dashboard}"
cd "$APP_DIR"

# Use the deployment venv if present, else fall back to PATH.
if [ -x ".venv/bin/flask" ]; then
    FLASK=".venv/bin/flask"
else
    FLASK="flask"
fi

exec "$FLASK" --app wsgi:app toast-sync
