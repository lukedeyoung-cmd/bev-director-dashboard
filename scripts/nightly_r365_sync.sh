#!/usr/bin/env bash
# Nightly Restaurant 365 OData ingestion for the L&SD Beverage Director
# Dashboard.
#
# Pulls new/edited AP invoices and invoice lines since the last sync
# (rowVersion-based incrementals). Run BEFORE the Toast sales sync so cost
# data is fresh when the daily digest is composed. Also schedule
# `flask r365-sync-reference` weekly to pick up vendor/item changes.
#
# Production crontab entry (server where the app is deployed):
#   30 4 * * * /opt/bev-director-dashboard/scripts/nightly_r365_sync.sh >> /var/log/r365-sync.log 2>&1
#
# Credentials come from the deployment environment (or .env) — never edit
# them into this script.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bev-director-dashboard}"
cd "$APP_DIR"

# Use the deployment venv if present, else fall back to PATH.
if [ -x ".venv/bin/flask" ]; then
    FLASK=".venv/bin/flask"
else
    FLASK="flask"
fi

"$FLASK" --app wsgi:app r365-sync-incremental --since-days=3
"$FLASK" --app wsgi:app r365-sync-reference
