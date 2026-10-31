#!/usr/bin/env bash
# Morning digest email for the L&SD Beverage Director Dashboard.
#
# Builds and sends the daily beverage digest (yesterday by venue, trailing
# 1/2/4-week trends, alerts) at 07:00 America/Chicago — after the nightly
# 06:15 Toast sync, so the previous business date is loaded.
#
# Production crontab entry (server where the app is deployed):
#   0 7 * * * /opt/bev-director-dashboard/scripts/morning_digest.sh >> /var/log/morning-digest.log 2>&1
#
# Note: cron uses server-local time. Configure the host's timezone as
# America/Chicago (or schedule 13:00 UTC) so this fires at 7:00 AM CT.
#
# Credentials come from the deployment environment (or .env) — never edit them
# into this script. Requires SENDGRID_API_KEY and DIGEST_RECIPIENTS.
#
# On failure (non-zero exit) the digest was NOT sent; the log captures the
# reason. Pipe the log through your alerting of choice.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bev-director-dashboard}"
cd "$APP_DIR"

# Use the deployment venv if present, else fall back to PATH.
if [ -x ".venv/bin/flask" ]; then
    FLASK=".venv/bin/flask"
else
    FLASK="flask"
fi

exec "$FLASK" --app wsgi:app send-digest
