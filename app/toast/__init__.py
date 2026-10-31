"""Toast POS ingestion: sales and catalog sync into the dashboard schema.

The pipeline is config-driven end to end: the org-level machine client
(TOAST_CLIENT_ID / TOAST_CLIENT_SECRET) comes from the environment, and each
venue's restaurant GUID lives on its Venue row (pos_venue_ref). Switching
from dev credentials to production L&SD credentials is a config change only.
"""

from app.toast.client import ToastAPIError, ToastAuthError, ToastClient
from app.toast.ingest import (
    backfill,
    client_for_venue,
    get_venue_by_code,
    sync_business_date,
    sync_catalog,
    venues_with_pos_refs,
)

__all__ = [
    "ToastAPIError",
    "ToastAuthError",
    "ToastClient",
    "backfill",
    "client_for_venue",
    "get_venue_by_code",
    "sync_business_date",
    "sync_catalog",
    "venues_with_pos_refs",
]
