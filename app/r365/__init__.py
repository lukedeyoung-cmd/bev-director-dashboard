"""Restaurant 365 OData ingestion: vendors, items, AP invoices and lines.

Credentials (a normal R365 login, domain-prefixed) come from the
environment only — R365_ODATA_USERNAME / R365_ODATA_PASSWORD. Read-only
OData access under L&SD's Accounting Clerk role; nothing here writes to
R365. Item mapping to Toast POS items and normalization into the
Vendor/Invoice tables are separate future tasks.
"""

from app.r365.client import R365APIError, R365AuthError, R365ODataClient
from app.r365.ingest import (
    backfill_history,
    sync_incremental,
    sync_reference,
    sync_transaction_details_window,
    sync_transactions_month,
)

__all__ = [
    "R365APIError",
    "R365AuthError",
    "R365ODataClient",
    "backfill_history",
    "sync_incremental",
    "sync_reference",
    "sync_transaction_details_window",
    "sync_transactions_month",
]
