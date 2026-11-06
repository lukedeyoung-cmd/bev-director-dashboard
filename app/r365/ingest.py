"""DB orchestration: idempotent R365 OData ingestion and history backfill.

Modes:
- Reference sync (Location, Company→vendors, Item): full pull, upsert by
  the view's natural key. Cheap enough to run any time.
- History backfill: month-by-month (R365's documented recommendation and a
  hard server rule — TransactionDetail rejects windows over 31 days).
  Transaction rows are pulled by invoice-date month (type = 'AP Invoice');
  TransactionDetail rows by createdOn month. Each completed month is
  recorded in r365_sync_state so re-runs never re-pull completed months.
- Incremental: a recent modifiedOn window combined with the stored
  rowVersion checkpoint catches new and edited rows for the nightly job.

Idempotency:
- Headers/reference upsert by key: re-runs converge (updates only on
  actual value changes).
- TransactionDetail rows replace by source_row_key (the view repeats
  transactionDetailId across Detail/Offset/GL-split rows), so re-pulling
  a window is a no-op and corrections land cleanly.
- TransactionDetail carries no transaction-date column, so "history" for
  details means createdOn months; the invoice date joins from the
  Transaction header at query time.
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from app.extensions import db
from app.models.purchasing import (
    R365Item,
    R365Location,
    R365SyncState,
    R365Transaction,
    R365TransactionDetail,
    R365Vendor,
)
from app.r365.client import R365ODataClient
from app.r365.transforms import (
    PURCHASE_TRANSACTION_TYPE,
    parse_item,
    parse_location,
    parse_transaction,
    parse_transaction_detail,
    parse_vendor,
)

logger = logging.getLogger(__name__)

# The 31-day guard the server enforces on TransactionDetail windows.
MAX_WINDOW_DAYS = 31
# Safety margin added to the incremental window's upper bound so rows
# written between the clock check and the query are not skipped.
INCREMENTAL_HEAD_MARGIN = timedelta(minutes=5)
_CHUNK = 500  # IN-clause batching


@dataclass
class SyncResult:
    entity: str
    rows_seen: int = 0
    rows_created: int = 0
    rows_updated: int = 0

    def __add__(self, other: SyncResult) -> SyncResult:
        return SyncResult(
            entity=self.entity,
            rows_seen=self.rows_seen + other.rows_seen,
            rows_created=self.rows_created + other.rows_created,
            rows_updated=self.rows_updated + other.rows_updated,
        )


@dataclass
class HistoryOutcome:
    """Month-by-month backfill result for one entity."""

    entity: str
    synced_months: list[str] = field(default_factory=list)
    skipped_months: list[str] = field(default_factory=list)
    failed_month: str | None = None
    error: str | None = None
    result: SyncResult | None = None


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _month_window(year: int, month: int) -> tuple[datetime, datetime]:
    """UTC inclusive [start, end] window for one calendar month (≤31 days).

    The server counts filter dates inclusively and rejects spans over 31
    days, so the end is the month's own last day at 23:59:59 — ending at
    the first day of the next month would make a 31-day month span 32
    inclusive days (e.g. Dec: 12-01 .. 01-01) and get a HTTP 400.
    """
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
    return start, end


def _month_label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _iter_months(start: date, end: date) -> list[tuple[int, int]]:
    """Calendar months covering [start, end], oldest first."""
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _odata_stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_filter(
    window_property: str, start: datetime, end: datetime, *, inclusive_end: bool = False
) -> str:
    end_op = "le" if inclusive_end else "lt"
    return (
        f"{window_property} ge {_odata_stamp(start)} "
        f"and {window_property} {end_op} {_odata_stamp(end)}"
    )


# ----------------------------------------------------------------------
# State helpers
# ----------------------------------------------------------------------
def _get_state(entity: str) -> R365SyncState:
    state = db.session.get(R365SyncState, entity)
    if state is None:
        state = R365SyncState(entity=entity)
        db.session.add(state)
        db.session.flush()
    return state


def _history_done_through(entity: str) -> date | None:
    state = db.session.get(R365SyncState, entity)
    return state.last_history_period if state is not None else None


def _record_history_done(entity: str, month_end: date) -> None:
    state = _get_state(entity)
    if state.last_history_period is None or month_end > state.last_history_period:
        state.last_history_period = month_end
    # Commit immediately: a later failed month must not lose the marker,
    # or the resume logic re-pulls months that already loaded.
    db.session.commit()


def _advance_checkpoint(entity: str, max_row_version: int | None, when: datetime) -> None:
    state = _get_state(entity)
    if max_row_version is not None and (
        state.max_row_version is None or max_row_version > state.max_row_version
    ):
        state.max_row_version = max_row_version
    state.last_sync_at = when


# ----------------------------------------------------------------------
# Generic keyed upsert
# ----------------------------------------------------------------------
def _upsert_by_key(model: type, key_attr: str, parsed_rows: list[dict[str, Any]]) -> SyncResult:
    """Upsert parsed rows on one key column; update only on value change."""
    result = SyncResult(entity=model.__name__, rows_seen=len(parsed_rows))
    if not parsed_rows:
        return result

    existing_by_key: dict[Any, Any] = {}
    keys = [row[key_attr] for row in parsed_rows]
    for chunk_start in range(0, len(keys), _CHUNK):
        chunk = keys[chunk_start : chunk_start + _CHUNK]
        found = db.session.scalars(
            sa.select(model).where(getattr(model, key_attr).in_(chunk))
        )
        for existing in found:
            existing_by_key[getattr(existing, key_attr)] = existing

    for row in parsed_rows:
        existing = existing_by_key.get(row[key_attr])
        if existing is None:
            db.session.add(model(**row))
            result.rows_created += 1
            continue
        changed = False
        for attr, value in row.items():
            if getattr(existing, attr) != value:
                setattr(existing, attr, value)
                changed = True
        if changed:
            result.rows_updated += 1
    db.session.commit()
    return result


def _replace_details(parsed_rows: list[dict[str, Any]]) -> SyncResult:
    """Replace TransactionDetail rows by source_row_key (window re-runs are no-ops)."""
    result = SyncResult(entity=R365TransactionDetail.__name__, rows_seen=len(parsed_rows))
    if not parsed_rows:
        return result

    keys = [row["source_row_key"] for row in parsed_rows]
    existing_ids: set[Any] = set()
    for chunk_start in range(0, len(keys), _CHUNK):
        chunk = keys[chunk_start : chunk_start + _CHUNK]
        existing_ids.update(
            db.session.scalars(
                sa.select(R365TransactionDetail.id).where(
                    R365TransactionDetail.source_row_key.in_(chunk)
                )
            )
        )
    result.rows_updated = len(existing_ids)
    result.rows_created = len(parsed_rows) - len(existing_ids)
    if existing_ids:
        db.session.execute(
            sa.delete(R365TransactionDetail).where(R365TransactionDetail.id.in_(existing_ids))
        )
    for row in parsed_rows:
        db.session.add(R365TransactionDetail(**row))
    db.session.commit()
    return result


# ----------------------------------------------------------------------
# Reference sync (Location, Company, Item)
# ----------------------------------------------------------------------
def sync_reference(client: R365ODataClient) -> dict[str, SyncResult]:
    """Full pull of Location, Company (vendors) and Item — upsert each."""
    locations = [parse_location(row) for row in client.iter_rows("Location")]
    vendors = [parse_vendor(row) for row in client.iter_rows("Company")]
    items = [parse_item(row) for row in client.iter_rows("Item")]
    return {
        "Location": _upsert_by_key(R365Location, "location_id", locations),
        "Company": _upsert_by_key(R365Vendor, "company_id", vendors),
        "Item": _upsert_by_key(R365Item, "item_id", items),
    }


# ----------------------------------------------------------------------
# Transactions (AP invoices) and TransactionDetail
# ----------------------------------------------------------------------
def sync_transactions_month(
    client: R365ODataClient, year: int, month: int
) -> SyncResult:
    """Pull one invoice-date month of AP-invoice headers; upsert by id."""
    start, end = _month_window(year, month)
    params = {
        "$filter": (
            f"{_window_filter('date', start, end, inclusive_end=True)} "
            f"and type eq '{PURCHASE_TRANSACTION_TYPE}'"
        )
    }
    result = SyncResult(entity=R365Transaction.__name__)
    for page in _paged_rows(client, "Transaction", params):
        page_result = _upsert_by_key(
            R365Transaction, "transaction_id", [parse_transaction(row) for row in page]
        )
        result += page_result
    return result


def sync_transaction_details_window(
    client: R365ODataClient, start: datetime, end: datetime
) -> SyncResult:
    """Pull one createdOn month window (≤31 days) of TransactionDetail rows."""
    if (end - start).days > MAX_WINDOW_DAYS:
        raise ValueError(
            f"TransactionDetail window must be ≤ {MAX_WINDOW_DAYS} days "
            f"(server rule); got {(end - start).days}"
        )
    params = {"$filter": _window_filter("createdOn", start, end, inclusive_end=True)}
    result = SyncResult(entity=R365TransactionDetail.__name__)
    for page in _paged_rows(client, "TransactionDetail", params):
        page_result = _replace_details([parse_transaction_detail(row) for row in page])
        result += page_result
    return result


def _paged_rows(
    client: R365ODataClient, entity: str, params: dict[str, Any]
) -> Iterator[list[dict]]:
    """Group the row stream into commit-sized batches (one client page each)."""
    batch: list[dict] = []
    for row in client.iter_rows(entity, params=params):
        batch.append(row)
        if len(batch) >= client.page_size:
            yield batch
            batch = []
    if batch:
        yield batch


# ----------------------------------------------------------------------
# History backfill
# ----------------------------------------------------------------------
def backfill_history(
    client: R365ODataClient,
    start: date,
    end: date,
    *,
    force: bool = False,
) -> dict[str, HistoryOutcome]:
    """Month-by-month initial load for [start, end], resumable via sync state.

    Transactions sync by invoice-date month; TransactionDetail by createdOn
    month. Completed months are never re-pulled unless force=True. A failed
    month stops that entity's loop so ordering stays honest.
    """
    months = _iter_months(start, end)
    outcomes: dict[str, HistoryOutcome] = {}

    try:
        reference = sync_reference(client)
        logger.info(
            "Reference sync: %s",
            {k: (v.rows_created, v.rows_updated) for k, v in reference.items()},
        )
    except Exception as exc:  # noqa: BLE001 — reported, not swallowed
        db.session.rollback()
        logger.error("Reference sync failed: %s", exc)
        return {
            entity: HistoryOutcome(entity=entity, error=f"reference sync failed: {exc}")
            for entity in (R365Transaction.__name__, R365TransactionDetail.__name__)
        }

    plans = [
        (R365Transaction.__name__, _sync_history_month_transactions),
        (R365TransactionDetail.__name__, _sync_history_month_details),
    ]
    for entity, month_sync in plans:
        outcome = HistoryOutcome(entity=entity)
        done_through = _history_done_through(entity)
        aggregate: SyncResult | None = None
        for year, month in months:
            label = _month_label(year, month)
            if not force and done_through is not None and _month_end(year, month) <= done_through:
                outcome.skipped_months.append(label)
                continue
            try:
                month_result = month_sync(client, year, month)
            except Exception as exc:  # noqa: BLE001 — per-month resilience is the point
                db.session.rollback()
                outcome.failed_month = label
                outcome.error = str(exc)
                logger.warning("History %s %s failed: %s", entity, label, exc)
                break
            _record_history_done(entity, _month_end(year, month))
            outcome.synced_months.append(label)
            aggregate = month_result if aggregate is None else aggregate + month_result
        outcome.result = aggregate or SyncResult(entity=entity)
        outcomes[entity] = outcome
    return outcomes


def _sync_history_month_transactions(client: R365ODataClient, year: int, month: int) -> SyncResult:
    return sync_transactions_month(client, year, month)


def _sync_history_month_details(client: R365ODataClient, year: int, month: int) -> SyncResult:
    start, end = _month_window(year, month)
    return sync_transaction_details_window(client, start, end)


# ----------------------------------------------------------------------
# Incremental
# ----------------------------------------------------------------------
def sync_incremental(
    client: R365ODataClient,
    *,
    since_days: int = 3,
    now: datetime | None = None,
) -> dict[str, SyncResult]:
    """Nightly pull: recent modifiedOn window + rowVersion checkpoint.

    The window anchors on the last successful sync (falling back to
    since_days when no checkpoint exists) and never exceeds the server's
    31-day guard; new or edited rows are matched and replaced/upserted.
    """
    current = now or _now_utc()
    results: dict[str, SyncResult] = {}

    tx_state = db.session.get(R365SyncState, R365Transaction.__name__)
    results[R365Transaction.__name__] = _incremental_transactions(
        client, tx_state, since_days, current
    )
    td_state = db.session.get(R365SyncState, R365TransactionDetail.__name__)
    results[R365TransactionDetail.__name__] = _incremental_details(
        client, td_state, since_days, current
    )
    return results


def _incremental_window(
    state: R365SyncState | None, since_days: int, current: datetime
) -> tuple[datetime, datetime]:
    fallback = current - timedelta(days=since_days)
    last = state.last_sync_at if state is not None else None
    if last is not None and last.tzinfo is None:
        # SQLite (test env) drops tzinfo on read; stored values are UTC.
        last = last.replace(tzinfo=UTC)
    start = max(fallback, last) if last is not None else fallback
    start = min(start, current)
    end = current + INCREMENTAL_HEAD_MARGIN
    if (end - start).days >= MAX_WINDOW_DAYS:
        raise ValueError(
            f"Incremental window {(end - start).days} days exceeds the server's "
            f"{MAX_WINDOW_DAYS}-day guard; shorten the gap between runs."
        )
    return start, end


def _max_row_version(parsed_rows: list[dict[str, Any]], floor: int | None) -> int | None:
    page_max = max((row["row_version"] or 0 for row in parsed_rows), default=0)
    if page_max and (floor is None or page_max > floor):
        return page_max
    return floor


def _incremental_transactions(
    client: R365ODataClient, state: R365SyncState | None, since_days: int, current: datetime
) -> SyncResult:
    start, end = _incremental_window(state, since_days, current)
    row_version = state.max_row_version if state is not None else None
    params = {
        "$filter": (
            f"{_window_filter('modifiedOn', start, end)} "
            f"and type eq '{PURCHASE_TRANSACTION_TYPE}'"
        )
    }
    if row_version is not None:
        params["$filter"] += f" and rowVersion gt {row_version}L"

    result = SyncResult(entity=R365Transaction.__name__)
    max_seen = row_version
    for page in _paged_rows(client, "Transaction", params):
        parsed = [parse_transaction(row) for row in page]
        result += _upsert_by_key(R365Transaction, "transaction_id", parsed)
        max_seen = _max_row_version(parsed, max_seen)
    _advance_checkpoint(R365Transaction.__name__, max_seen, current)
    db.session.commit()
    return result


def _incremental_details(
    client: R365ODataClient, state: R365SyncState | None, since_days: int, current: datetime
) -> SyncResult:
    start, end = _incremental_window(state, since_days, current)
    row_version = state.max_row_version if state is not None else None
    params = {"$filter": _window_filter("modifiedOn", start, end)}
    if row_version is not None:
        params["$filter"] += f" and rowVersion gt {row_version}L"

    result = SyncResult(entity=R365TransactionDetail.__name__)
    max_seen = row_version
    for page in _paged_rows(client, "TransactionDetail", params):
        parsed = [parse_transaction_detail(row) for row in page]
        result += _replace_details(parsed)
        max_seen = _max_row_version(parsed, max_seen)
    _advance_checkpoint(R365TransactionDetail.__name__, max_seen, current)
    db.session.commit()
    return result
