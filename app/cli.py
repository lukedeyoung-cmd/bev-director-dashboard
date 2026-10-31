"""Management CLI commands (flask <command>)."""

import datetime as dt

import click
import sqlalchemy as sa
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models.user import User


def _parse_date(value: str, option_name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise click.BadOptionUsage(
            option_name, f"{option_name} must be YYYY-MM-DD: {exc}"
        ) from exc


def _previous_business_date(timezone_name: str) -> dt.date:
    """Yesterday in the venue's timezone (the cron runs after closeout)."""
    from zoneinfo import ZoneInfo

    now_local = dt.datetime.now(ZoneInfo(timezone_name))
    return (now_local - dt.timedelta(days=1)).date()


def _toast_credentials(app) -> tuple[str, str]:
    """Toast client settings from config; errors before touching the API."""
    client_id = app.config.get("TOAST_CLIENT_ID")
    client_secret = app.config.get("TOAST_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise click.ClickException(
            "TOAST_CLIENT_ID / TOAST_CLIENT_SECRET are required in the environment "
            "(see .env.example)."
        )
    return client_id, client_secret


def _echo_sales_result(result) -> None:
    click.echo(
        f"  {result.business_date}: orders={result.orders_seen} "
        f"(voided {result.orders_voided}), checks={result.checks_seen}, "
        f"selections={result.selections_seen} "
        f"(voided {result.selections_voided}), items_created={result.items_created}, "
        f"daily_sales rows={result.rows_written}"
    )
    if result.open_items_without_guid:
        click.echo(
            f"  note: {result.open_items_without_guid} open items without a POS guid "
            "were skipped (no catalog identity)"
        )
    if result.dropped_discount_excess:
        click.echo(
            f"  note: {result.dropped_discount_excess} of comped tax could not be "
            "allocated to items and was dropped"
        )


def register_cli(app) -> None:
    from app.fixtures import register_fixture_commands

    register_fixture_commands(app)

    @app.cli.command("create-admin")
    @click.option("--email", required=True, help="Login email for the new admin.")
    @click.option("--name", required=True, help="Display name.")
    @click.password_option(help="Password (prompted interactively if omitted).")
    def create_admin(email: str, name: str, password: str) -> None:
        """Create an administrator account (run once per deployment)."""
        normalized = email.strip().lower()
        existing = db.session.scalar(sa.select(User).where(User.email == normalized))
        if existing is not None:
            raise click.ClickException(f"A user with email {normalized} already exists.")
        user = User(email=normalized, name=name.strip(), role="admin")
        user.password_hash = generate_password_hash(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created admin user {normalized}")

    # ------------------------------------------------------------------
    # Toast ingestion
    # ------------------------------------------------------------------
    @app.cli.command("toast-sync-catalog")
    @click.option("--venue", "venue_code", required=True, help="Venue code.")
    def toast_sync_catalog(venue_code: str) -> None:
        """Sync one venue's POS catalog (menus v2 + config v2)."""
        from app.toast import client_for_venue, get_venue_by_code, sync_catalog

        client_id, client_secret = _toast_credentials(app)
        venue = get_venue_by_code(venue_code)
        if not venue.pos_venue_ref:
            raise click.ClickException(f"Venue {venue.code} has no pos_venue_ref set.")
        result = sync_catalog(venue, client_for_venue(venue, client_id, client_secret))
        click.echo(
            f"Catalog sync for {venue.code}: seen={result.items_seen} "
            f"created={result.items_created} updated={result.items_updated}"
        )

    @app.cli.command("toast-sync-sales")
    @click.option("--venue", "venue_code", required=True, help="Venue code.")
    @click.option(
        "--business-date",
        "business_date_str",
        default=None,
        help="YYYY-MM-DD (default: previous day in the venue's timezone).",
    )
    def toast_sync_sales(venue_code: str, business_date_str: str | None) -> None:
        """Sync one venue's sales for one business date."""
        from app.toast import client_for_venue, get_venue_by_code, sync_business_date

        client_id, client_secret = _toast_credentials(app)
        venue = get_venue_by_code(venue_code)
        if not venue.pos_venue_ref:
            raise click.ClickException(f"Venue {venue.code} has no pos_venue_ref set.")
        business_date = (
            _parse_date(business_date_str, "--business-date")
            if business_date_str
            else _previous_business_date(venue.timezone)
        )
        client = client_for_venue(venue, client_id, client_secret)
        click.echo(f"Syncing {venue.code} business date {business_date}...")
        result = sync_business_date(venue, client, business_date)
        _echo_sales_result(result)

    @app.cli.command("toast-backfill")
    @click.option("--venue", "venue_code", required=True, help="Venue code.")
    @click.option("--start", "start_str", default=None, help="YYYY-MM-DD (inclusive).")
    @click.option("--end", "end_str", default=None, help="YYYY-MM-DD (inclusive).")
    def toast_backfill(venue_code: str, start_str: str | None, end_str: str | None) -> None:
        """Backfill one venue's sales over a date range, skipping failed dates."""
        from app.toast import backfill, client_for_venue, get_venue_by_code

        client_id, client_secret = _toast_credentials(app)
        venue = get_venue_by_code(venue_code)
        if not venue.pos_venue_ref:
            raise click.ClickException(f"Venue {venue.code} has no pos_venue_ref set.")
        end = (
            _parse_date(end_str, "--end")
            if end_str
            else _previous_business_date(venue.timezone)
        )
        start = (
            _parse_date(start_str, "--start")
            if start_str
            else end - dt.timedelta(days=app.config.get("TOAST_BACKFILL_DAYS", 365) - 1)
        )
        if start > end:
            raise click.BadOptionUsage("--start", "--start must not be after --end")

        client = client_for_venue(venue, client_id, client_secret)
        click.echo(f"Backfilling {venue.code} {start} .. {end}")
        results, failures = backfill(venue, client, start, end)
        for result in results:
            _echo_sales_result(result)
        click.echo(
            f"Backfill done: {len(results)} dates synced, {len(failures)} failed"
        )
        for failed_date, error in sorted(failures.items()):
            click.echo(f"  FAILED {failed_date}: {error}")
        if failures:
            raise click.ClickException(f"{len(failures)} date(s) failed; see above.")

    @app.cli.command("toast-sync")
    @click.option(
        "--business-date",
        "business_date_str",
        default=None,
        help="YYYY-MM-DD (default: previous day in each venue's timezone).",
    )
    def toast_sync(business_date_str: str | None) -> None:
        """Nightly job: catalog + sales sync for every connected venue.

        Schedule after closeout (e.g. 6:15 AM venue-local) via cron — see
        scripts/nightly_toast_sync.sh and the README's operations section.
        """
        from app.toast import (
            client_for_venue,
            sync_business_date,
            sync_catalog,
            venues_with_pos_refs,
        )

        client_id, client_secret = _toast_credentials(app)
        venues = venues_with_pos_refs()
        if not venues:
            raise click.ClickException(
                "No active venue has pos_venue_ref set — nothing to sync."
            )

        failed_venues: list[str] = []
        for venue in venues:
            client = client_for_venue(venue, client_id, client_secret)
            try:
                catalog = sync_catalog(venue, client)
                click.echo(
                    f"{venue.code}: catalog created={catalog.items_created} "
                    f"updated={catalog.items_updated}"
                )
                business_date = (
                    _parse_date(business_date_str, "--business-date")
                    if business_date_str
                    else _previous_business_date(venue.timezone)
                )
                result = sync_business_date(venue, client, business_date)
                _echo_sales_result(result)
            except Exception as exc:  # noqa: BLE001 — one venue must not block others
                db.session.rollback()
                failed_venues.append(venue.code)
                click.echo(f"{venue.code}: FAILED: {exc}", err=True)

        if failed_venues:
            raise click.ClickException(
                f"{len(failed_venues)} venue(s) failed: {', '.join(failed_venues)}"
            )
        click.echo(f"toast-sync complete for {len(venues)} venue(s).")

    @app.cli.command("send-digest")
    @click.option(
        "--business-date",
        "business_date_str",
        default=None,
        help="YYYY-MM-DD (default: newest business date present in sales data).",
    )
    def send_digest(business_date_str: str | None) -> None:
        """Build and send the morning digest email (schedule: 7:00 AM CT).

        Cron path — scripts/morning_digest.sh runs this daily. Manual trigger:
        POST /api/digest/send (session + CSRF) or this command on demand.
        """
        from app.digest import DigestError, send_morning_digest

        business_date = (
            _parse_date(business_date_str, "--business-date") if business_date_str else None
        )
        try:
            summary = send_morning_digest(app, business_date=business_date)
        except DigestError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            f"Digest sent: {summary['subject']} -> {summary['recipients']} recipient(s), "
            f"{summary['venues']} venue(s), {summary['alerts']} alert(s)."
        )
