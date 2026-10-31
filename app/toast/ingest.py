"""DB orchestration: idempotent Toast upserts into pos_items and daily_sales.

Idempotency:
- Catalog: upsert by the pos_items unique key (venue_id, pos_guid). Menus-v2
  records are authoritative; config-v2 and order-feed records only fill gaps,
  so repeated runs converge instead of flip-flopping classifications.
- Sales: one transaction per business date — aggregates are recomputed from
  the orders payload and the date's daily_sales rows are replaced, so any
  date can be re-synced safely at any time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.extensions import db
from app.models import DailySales, PosItem, Venue
from app.toast.client import ToastClient
from app.toast.transforms import (
    MENU_ITEM,
    MODIFIER,
    CatalogRecord,
    SaleLineAggregate,
    aggregate_orders,
    extract_catalog_records,
    extract_config_records,
)

logger = logging.getLogger(__name__)


@dataclass
class CatalogSyncResult:
    items_created: int = 0
    items_updated: int = 0
    items_seen: int = 0

    def __add__(self, other: CatalogSyncResult) -> CatalogSyncResult:
        return CatalogSyncResult(
            items_created=self.items_created + other.items_created,
            items_updated=self.items_updated + other.items_updated,
            items_seen=self.items_seen + other.items_seen,
        )


@dataclass
class SalesSyncResult:
    business_date: date
    orders_seen: int = 0
    orders_voided: int = 0
    selections_seen: int = 0
    selections_voided: int = 0
    open_items_without_guid: int = 0
    items_created: int = 0
    rows_written: int = 0
    dropped_discount_excess: Decimal = Decimal("0")


def get_venue_by_code(venue_code: str) -> Venue:
    """Resolve a venue by its code; raises LookupError when unknown."""
    venue = db.session.scalar(sa.select(Venue).where(Venue.code == venue_code.strip()))
    if venue is None:
        known = ", ".join(v.code for v in db.session.scalars(sa.select(Venue)))
        raise LookupError(f"Unknown venue code {venue_code!r} (known: {known or 'none'})")
    return venue


def venues_with_pos_refs() -> list[Venue]:
    """Active venues that have a POS reference configured."""
    return list(
        db.session.scalars(
            sa.select(Venue).where(
                Venue.is_active.is_(True), Venue.pos_venue_ref.is_not(None)
            )
        )
    )


def client_for_venue(venue: Venue, client_id: str, client_secret: str) -> ToastClient:
    """Build a client bound to the venue's restaurant GUID.

    Credentials come from config via the caller — the venue row only holds
    the non-secret restaurant GUID (pos_venue_ref).
    """
    return ToastClient(
        client_id=client_id,
        client_secret=client_secret,
        restaurant_guid=venue.pos_venue_ref or "",
    )


# ----------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------
def upsert_catalog_records(venue: Venue, records: list[CatalogRecord]) -> CatalogSyncResult:
    """Upsert catalog records for one venue (single commit at the end)."""
    result = CatalogSyncResult(items_seen=len(records))
    for record in records:
        item = db.session.scalar(
            sa.select(PosItem).where(
                PosItem.venue_id == venue.id, PosItem.pos_guid == record.pos_guid
            )
        )
        if item is None:
            db.session.add(
                PosItem(
                    venue_id=venue.id,
                    pos_guid=record.pos_guid,
                    name=record.name,
                    item_type=record.item_type,
                    category=record.category,
                    unit_price=record.unit_price,
                    is_active=True,
                )
            )
            result.items_created += 1
            continue
        if record.authoritative:
            updated = False
            if record.name and record.name != item.name:
                item.name = record.name
                updated = True
            if record.category and record.category != item.category:
                item.category = record.category
                updated = True
            if record.item_type != item.item_type:
                item.item_type = record.item_type
                updated = True
            if record.unit_price is not None and record.unit_price != item.unit_price:
                item.unit_price = record.unit_price
                updated = True
            if not item.is_active:
                item.is_active = True
                updated = True
            if updated:
                result.items_updated += 1
        elif not item.name and record.name:
            # Gap-filler records only ever complete an empty name.
            item.name = record.name
            result.items_updated += 1
    db.session.commit()
    return result


def sync_catalog(venue: Venue, client: ToastClient) -> CatalogSyncResult:
    """Sync one venue's catalog from menus v2 (authoritative) + config v2 (fill)."""
    menus_payload = client.fetch_menus_v2()
    menu_result = upsert_catalog_records(venue, extract_catalog_records(menus_payload))
    config_result = upsert_catalog_records(
        venue, extract_config_records(client.iter_config_menu_items())
    )
    return menu_result + config_result


# ----------------------------------------------------------------------
# Sales
# ----------------------------------------------------------------------
def _ensure_pos_item(venue: Venue, aggregate: SaleLineAggregate) -> tuple[PosItem, bool]:
    """Find or create the pos_items row an aggregate needs for its FK."""
    item = db.session.scalar(
        sa.select(PosItem).where(
            PosItem.venue_id == venue.id, PosItem.pos_guid == aggregate.pos_guid
        )
    )
    if item is not None:
        return item, False
    item = PosItem(
        venue_id=venue.id,
        pos_guid=aggregate.pos_guid,
        name=aggregate.display_name or aggregate.pos_guid,
        item_type=(
            aggregate.item_type if aggregate.item_type in {MENU_ITEM, MODIFIER} else MENU_ITEM
        ),
        category=aggregate.category,
        is_active=True,
    )
    db.session.add(item)
    db.session.flush()  # assign the PK before daily_sales references it
    return item, True


def sync_business_date(
    venue: Venue, client: ToastClient, business_date: date
) -> SalesSyncResult:
    """Ingest one business date: aggregate orders, then replace that date's rows."""
    orders = list(client.iter_orders_bulk(business_date.strftime("%Y%m%d")))
    aggregates, stats = aggregate_orders(orders)

    try:
        item_pairs = [_ensure_pos_item(venue, agg) for agg in aggregates.values()]
        created = sum(1 for _, is_new in item_pairs if is_new)
        db.session.execute(
            sa.delete(DailySales).where(
                DailySales.venue_id == venue.id,
                DailySales.business_date == business_date,
            )
        )
        for (item, _), aggregate in zip(item_pairs, aggregates.values(), strict=True):
            db.session.add(
                DailySales(
                    venue_id=venue.id,
                    business_date=business_date,
                    pos_item_id=item.id,
                    # Snapshot the item's catalog category (or the name-based
                    # fallback for items the catalog doesn't know yet).
                    category=item.category or aggregate.category,
                    quantity_sold=aggregate.quantity,
                    gross_sales=aggregate.gross_sales,
                    discount_total=aggregate.discount_total,
                    net_sales=aggregate.net_sales,
                )
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return SalesSyncResult(
        business_date=business_date,
        orders_seen=stats.orders_seen,
        orders_voided=stats.orders_voided,
        selections_seen=stats.selections_seen,
        selections_voided=stats.selections_voided,
        open_items_without_guid=stats.open_items_without_guid,
        items_created=created,
        rows_written=len(aggregates),
        dropped_discount_excess=stats.dropped_discount_excess,
    )


def backfill(
    venue: Venue, client: ToastClient, start: date, end: date
) -> tuple[list[SalesSyncResult], dict[date, str]]:
    """Ingest every business date in [start, end], continuing past failures.

    Returns (successful syncs, failures keyed by date with the error text).
    Old dates beyond the venue's Toast retention surface HTTP errors; a bad
    date must not sink the rest of the backfill.
    """
    results: list[SalesSyncResult] = []
    failures: dict[date, str] = {}
    current = start
    while current <= end:
        try:
            results.append(sync_business_date(venue, client, current))
        except Exception as exc:  # noqa: BLE001 — per-date resilience is the point
            db.session.rollback()
            failures[current] = str(exc)
            logger.warning("Backfill failed for %s %s: %s", venue.code, current, exc)
        current += timedelta(days=1)
    return results, failures
