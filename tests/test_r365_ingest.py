"""Ingest orchestration tests against the test DB (sqlite via conftest)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.extensions import db
from app.models import (
    R365Item,
    R365Location,
    R365SyncState,
    R365Transaction,
    R365TransactionDetail,
    R365Vendor,
)
from app.r365.ingest import (
    _incremental_window,
    _month_end,
    _month_window,
    backfill_history,
    sync_incremental,
    sync_reference,
    sync_transaction_details_window,
    sync_transactions_month,
)
from tests.r365_payloads import (
    DICEY_LOCATION_ID,
    ITEM_KEG_ID,
    TXN3_ID,
    TXN_ID,
    VENDOR_1_ID,
    VENDOR_2_ID,
    company,
    gl_detail,
    item,
    item_detail,
    location,
    non_ap_transaction,
    transaction,
)

NOV_START = date(2025, 11, 1)
NOV_END = date(2025, 12, 1)  # two calendar months: Nov + Dec
INCREMENTAL_NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


class StubR365Client:
    """In-memory stand-in for R365ODataClient with the same surface."""

    def __init__(self, views: dict | None = None):
        # entity -> raw rows (or an Exception instance to raise)
        self.views = views or {}
        self.page_size = 1000
        self.calls: list[tuple[str, str | None]] = []

    def iter_rows(self, entity, *, params=None, page_size=None):
        self.calls.append((entity, (params or {}).get("$filter")))
        rows = self.views.get(entity, [])
        if isinstance(rows, Exception):
            raise rows
        yield from rows


@pytest.fixture()
def reference_stub():
    return StubR365Client(
        {
            "Location": [location()],
            "Company": [company(), company(VENDOR_2_ID, name="RNDC")],
            "Item": [item()],
        }
    )


# ----------------------------------------------------------------------
# Window helpers
# ----------------------------------------------------------------------
def test_month_window_bounds():
    start, end = _month_window(2025, 11)
    assert start == datetime(2025, 11, 1, tzinfo=UTC)
    assert end == datetime(2025, 11, 30, 23, 59, 59, tzinfo=UTC)
    assert _month_end(2025, 11) == date(2025, 11, 30)

    start, end = _month_window(2025, 12)  # December rolls the year
    assert end == datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)


def test_month_window_span_within_31_day_limit():
    """Regression: R365 rejects filters spanning more than 31 days.

    Ending a 31-day month at the first day of the next month made the
    inclusive span 32 days (Dec: 12-01 .. 01-01) → HTTP 400. The window
    must end on the month's own last day.
    """
    for year, month in [
        (2025, 2),  # 28-day month
        (2024, 2),  # leap-year February
        (2025, 4),  # 30-day month
        (2025, 11),  # 30-day month (previously passing)
        (2025, 12),  # 31-day month (previously failing)
        (2025, 1),  # 31-day month
        (2026, 10),  # backfill end month
    ]:
        start, end = _month_window(year, month)
        assert start == datetime(year, month, 1, tzinfo=UTC)
        assert end.date() == _month_end(year, month)  # month's own last day
        span_days = (end.date() - start.date()).days + 1  # inclusive span
        assert span_days <= 31, f"{year}-{month:02d} spans {span_days} days"


def test_incremental_window_fallback_and_head_margin():
    start, end = _incremental_window(None, 3, INCREMENTAL_NOW)
    assert start == datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
    assert end == INCREMENTAL_NOW + timedelta(minutes=5)  # head margin included

    state = R365SyncState(entity="R365TransactionDetail", max_row_version=5)
    state.last_sync_at = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    start, end = _incremental_window(state, 3, INCREMENTAL_NOW)
    assert start == datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)  # anchors on last sync


# ----------------------------------------------------------------------
# Reference sync (Location / Company / Item)
# ----------------------------------------------------------------------
def test_sync_reference_creates_and_is_idempotent(app, reference_stub):
    results = sync_reference(reference_stub)
    assert results["Location"].rows_created == 1
    assert results["Company"].rows_created == 2
    assert results["Item"].rows_created == 1
    assert R365Location.query.count() == 1
    assert R365Vendor.query.count() == 2
    assert R365Item.query.count() == 1

    again = sync_reference(reference_stub)
    assert again["Location"].rows_created == 0
    assert again["Company"].rows_created == 0
    assert again["Item"].rows_created == 0
    assert R365Location.query.count() == 1


def test_sync_reference_preserves_location_source_fields(app, reference_stub):
    sync_reference(reference_stub)
    loc = db.session.get(R365Location, UUID(DICEY_LOCATION_ID))
    assert loc is not None
    assert loc.name == "11.2 Dicey's West Town"  # R365 name kept verbatim
    assert loc.legal_entity_name == "Parson's West Town"
    assert loc.legal_entity_number == "11"
    assert loc.location_number == "11.2"


# ----------------------------------------------------------------------
# Monthly transactions + detail windows
# ----------------------------------------------------------------------
def _month_stub():
    return StubR365Client(
        {
            "Transaction": [transaction()],
            "TransactionDetail": [item_detail(), gl_detail()],
        }
    )


def test_sync_transactions_month_creates_headers(app):
    result = sync_transactions_month(_month_stub(), 2025, 11)
    assert result.rows_created == 1

    txn = db.session.get(R365Transaction, UUID(TXN_ID))
    assert txn is not None
    assert txn.transaction_type == "AP Invoice"
    assert txn.is_approved is True
    assert str(txn.location_id) == DICEY_LOCATION_ID
    assert str(txn.company_id) == VENDOR_1_ID
    assert txn.transaction_date == date(2026, 3, 12)


def test_sync_transactions_month_filter_pulls_all_types(app):
    stub = _month_stub()
    sync_transactions_month(stub, 2025, 11)
    entity, filter_ = stub.calls[0]
    assert entity == "Transaction"
    # Every transaction type is mirrored — no type clause in the filter.
    assert filter_ == "date ge 2025-11-01T00:00:00Z and date le 2025-11-30T23:59:59Z"


def test_sync_transactions_month_upserts_non_ap_header_intact(app):
    stub = StubR365Client({"Transaction": [transaction(), non_ap_transaction()]})
    result = sync_transactions_month(stub, 2025, 11)
    assert result.rows_created == 2

    txn = db.session.get(R365Transaction, UUID(TXN3_ID))
    assert txn is not None
    assert txn.transaction_type == "Tips Payable"  # non-AP type preserved
    assert txn.transaction_number is None  # vendor/number fields absent → NULL
    assert txn.company_id is None
    assert txn.name is None
    assert txn.transaction_date == date(2026, 3, 15)
    assert str(txn.location_id) == DICEY_LOCATION_ID


def test_sync_transaction_details_window_creates_rows_and_links(app):
    stub = _month_stub()
    result = sync_transaction_details_window(
        stub, datetime(2025, 11, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC)
    )
    assert result.rows_created == 2
    details = R365TransactionDetail.query.order_by(
        R365TransactionDetail.transaction_detail_id
    ).all()
    assert len(details) == 2
    assert all(d.transaction_id == UUID(TXN_ID) for d in details)  # linked to header
    assert len({d.source_row_key for d in details}) == 2  # sub-rows not collapsed

    item_row = next(d for d in details if d.item_id is not None)
    assert str(item_row.item_id) == ITEM_KEG_ID
    assert item_row.debit == Decimal("167.4")
    gl_row = next(d for d in details if d.gl_account_id is not None)
    assert gl_row.item_id is None
    assert gl_row.debit == Decimal("42.18")


def test_sync_transaction_details_repull_replaces_not_duplicates(app):
    stub = _month_stub()
    window = (datetime(2025, 11, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC))
    sync_transaction_details_window(stub, *window)
    sync_transaction_details_window(stub, *window)
    assert R365TransactionDetail.query.count() == 2  # replaced, not duplicated


def test_sync_transaction_details_window_guard(app):
    stub = _month_stub()
    with pytest.raises(ValueError, match="31"):
        sync_transaction_details_window(
            stub, datetime(2025, 11, 1, tzinfo=UTC), datetime(2025, 12, 15, tzinfo=UTC)
        )


# ----------------------------------------------------------------------
# Month-by-month backfill
# ----------------------------------------------------------------------
def test_backfill_history_pulls_each_month(app):
    stub = _month_stub()
    outcomes = backfill_history(stub, NOV_START, NOV_END)

    detail_months = [
        f for e, f in stub.calls if e == "TransactionDetail" and f
    ]
    assert any("createdOn ge 2025-11-01T00:00:00Z" in f for f in detail_months)
    assert any("createdOn ge 2025-12-01T00:00:00Z" in f for f in detail_months)
    assert not any(o.failed_month for o in outcomes.values())
    assert outcomes["R365TransactionDetail"].synced_months == ["2025-11", "2025-12"]
    assert outcomes["R365Transaction"].synced_months == ["2025-11", "2025-12"]


def test_backfill_history_marks_months_complete(app):
    backfill_history(_month_stub(), NOV_START, NOV_END)
    state = db.session.get(R365SyncState, "R365TransactionDetail")
    assert state is not None
    assert state.last_history_period == date(2025, 12, 31)  # last month END


def test_backfill_history_skips_completed_months(app):
    stub = _month_stub()
    backfill_history(stub, NOV_START, NOV_END)

    stub.calls.clear()
    outcomes = backfill_history(stub, NOV_START, NOV_END)
    assert {e for e, _ in stub.calls} == {"Location", "Company", "Item"}  # ref only
    assert outcomes["R365TransactionDetail"].skipped_months == ["2025-11", "2025-12"]


def test_backfill_history_force_repulls_completed_months(app):
    stub = _month_stub()
    backfill_history(stub, NOV_START, NOV_END)

    stub.calls.clear()
    outcomes = backfill_history(stub, NOV_START, NOV_END, force=True)
    assert any(e == "TransactionDetail" for e, _ in stub.calls)
    assert not any(o.failed_month for o in outcomes.values())


def test_backfill_history_entities_scoped_header_only(app):
    """force + entities=['R365Transaction'] re-pulls headers, skips details."""
    stub = _month_stub()
    backfill_history(stub, NOV_START, NOV_END)
    detail_state_before = db.session.get(R365SyncState, "R365TransactionDetail")
    assert detail_state_before is not None
    assert detail_state_before.last_history_period == date(2025, 12, 31)

    stub.calls.clear()
    outcomes = backfill_history(
        stub, NOV_START, NOV_END, force=True, entities=["R365Transaction"]
    )
    # Detail entity never ran: no API calls and no outcome row.
    assert not any(e == "TransactionDetail" for e, _ in stub.calls)
    assert set(outcomes) == {"R365Transaction"}
    assert not any(o.failed_month for o in outcomes.values())
    # Detail sync state untouched.
    detail_state_after = db.session.get(R365SyncState, "R365TransactionDetail")
    assert detail_state_after.last_history_period == date(2025, 12, 31)
    # Headers were actually re-pulled for both months.
    header_filters = [f for e, f in stub.calls if e == "Transaction"]
    assert any("date ge 2025-11-01T00:00:00Z" in f for f in header_filters)
    assert any("date ge 2025-12-01T00:00:00Z" in f for f in header_filters)


def test_backfill_history_stops_at_failed_month(app):
    class FlakyDetail(StubR365Client):
        def iter_rows(self, entity, *, params=None, page_size=None):
            # Only December's window STARTS at 2025-12-01 (November's window
            # merely ends there, so match the lower bound).
            filter_ = (params or {}).get("$filter")
            if entity == "TransactionDetail" and filter_ and "createdOn ge 2025-12-01" in filter_:
                raise RuntimeError("api down")
            return super().iter_rows(entity, params=params, page_size=page_size)

    stub = FlakyDetail({"Transaction": [transaction()], "TransactionDetail": [item_detail()]})
    outcomes = backfill_history(stub, NOV_START, NOV_END)
    assert outcomes["R365TransactionDetail"].failed_month == "2025-12"
    assert outcomes["R365TransactionDetail"].synced_months == ["2025-11"]
    # November marked done (month END), December left open for resume.
    state = db.session.get(R365SyncState, "R365TransactionDetail")
    assert state.last_history_period == date(2025, 11, 30)


# ----------------------------------------------------------------------
# Incremental sync
# ----------------------------------------------------------------------
def as_aware(value):
    """SQLite (test env) drops tzinfo on read; stored values are UTC."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def test_sync_incremental_checkpoints_row_version(app):
    stub = _month_stub()
    results = sync_incremental(stub, since_days=3, now=INCREMENTAL_NOW)
    assert results["R365TransactionDetail"].rows_created == 2
    state = db.session.get(R365SyncState, "R365TransactionDetail")
    assert state.max_row_version == 142716  # max across synced detail rows
    assert as_aware(state.last_sync_at) == INCREMENTAL_NOW


def test_sync_incremental_pulls_non_ap_headers_and_advances_checkpoint(app):
    stub = StubR365Client(
        {
            "Transaction": [transaction(), non_ap_transaction()],
            "TransactionDetail": [item_detail(), gl_detail()],
        }
    )
    results = sync_incremental(stub, since_days=3, now=INCREMENTAL_NOW)
    assert results["R365Transaction"].rows_created == 2  # AP + non-AP header

    # No type clause: the incremental pull mirrors every transaction type.
    filters = {e: f for e, f in stub.calls}
    assert "type eq" not in filters["Transaction"]

    # Checkpoint advanced to the max row version across all header types.
    state = db.session.get(R365SyncState, "R365Transaction")
    assert state.max_row_version == 142720
    non_ap = db.session.get(R365Transaction, UUID(TXN3_ID))
    assert non_ap is not None
    assert non_ap.transaction_type == "Tips Payable"


def test_sync_incremental_uses_row_version_checkpoint(app):
    stub = _month_stub()
    sync_incremental(stub, since_days=3, now=INCREMENTAL_NOW)

    stub.calls.clear()
    sync_incremental(stub, since_days=3, now=INCREMENTAL_NOW)
    filters = {e: f for e, f in stub.calls}
    assert "rowVersion gt 142716L" in filters["TransactionDetail"]
    txn_filter = filters.get("R365Transaction") or filters.get("Transaction")
    assert "rowVersion gt 142710L" in txn_filter


def test_sync_incremental_picks_up_new_rows(app):
    stub = _month_stub()
    sync_incremental(stub, since_days=3, now=INCREMENTAL_NOW)

    new_row = item_detail(detail_id="dddd4444-0000-4000-8000-000000000777", debit=99.0)
    new_row["rowVersion"] = 999999
    stub.views["TransactionDetail"] = [item_detail(), gl_detail(), new_row]
    sync_incremental(stub, since_days=3, now=INCREMENTAL_NOW)
    assert db.session.get(R365SyncState, "R365TransactionDetail").max_row_version == 999999
    # The edited key was replaced, not duplicated.
    assert R365TransactionDetail.query.filter_by(
        source_row_key=tx_key_for(new_row)
    ).count() == 1


def tx_key_for(raw):
    from app.r365.transforms import source_row_key

    return source_row_key(raw["transactionDetailId"], raw["rowType"], raw["glAccountId"])
