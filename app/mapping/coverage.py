"""Mapping-coverage stats: the share of Toast sales volume flowing through
approved mappings.

This is the trustworthiness signal for variance math: until a venue's
beverage volume is covered by approved mappings, actual-vs-theoretical
numbers for that venue are incomplete and must be disclosed as such.
Coverage is computed over all daily-sales rows (any category — the mapping
applies to food items too), with a per-category breakdown so the beverage
rows and the big unclassified "other" bucket stay visible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa

from app.models.mapping import ToastItemMap
from app.models.sales import DailySales
from app.models.venue import Venue


@dataclass(frozen=True)
class SliceCoverage:
    """Coverage for one slice (a venue's total, or one category within it)."""

    label: str  # venue name, category name, or the group label
    total_net_sales: Decimal
    covered_net_sales: Decimal
    # None when the slice has no sales — coverage is vacuous, not zero.
    coverage_pct: float | None


@dataclass
class VenueCoverage:
    """Per-venue coverage; mutated while aggregating grouped query rows."""

    venue_id: uuid.UUID
    venue_name: str
    total_net_sales: Decimal
    covered_net_sales: Decimal
    coverage_pct: float | None
    categories: list[SliceCoverage] = field(default_factory=list)


_GROUP_LABEL = "All venues (group)"


def _pct(covered: Decimal, total: Decimal) -> float | None:
    if total <= 0:
        return None
    return round(float(covered / total * 100), 1)


def _empty_slice(label: str) -> SliceCoverage:
    return SliceCoverage(
        label=label, total_net_sales=Decimal("0"), covered_net_sales=Decimal("0"), coverage_pct=None
    )


def coverage_for_venues(
    session: sa.Session, venue_ids: list[uuid.UUID] | None = None
) -> list[VenueCoverage]:
    """Per-venue coverage of net sales by approved mappings.

    Approved-only: suggested mappings are proposals, not decisions. Rows come
    back ordered by venue name. Venues without sales are omitted — there is
    nothing to cover.
    """
    approved = (
        sa.select(ToastItemMap.id)
        .where(
            ToastItemMap.pos_item_id == DailySales.pos_item_id,
            ToastItemMap.status == "approved",
        )
        .exists()
    )
    query = (
        sa.select(
            Venue.id,
            Venue.name,
            DailySales.category,
            sa.func.sum(DailySales.net_sales).label("total"),
            sa.func.sum(sa.case((approved, DailySales.net_sales), else_=0)).label("covered"),
        )
        .join(Venue, Venue.id == DailySales.venue_id)
        .where(Venue.is_active.is_(True))
        .group_by(Venue.id, Venue.name, DailySales.category)
    )
    if venue_ids is not None:
        query = query.where(Venue.id.in_(venue_ids))

    venues: dict[uuid.UUID, VenueCoverage] = {}
    for venue_id, venue_name, category, total, covered in session.execute(query).all():
        coverage = venues.setdefault(
            venue_id,
            VenueCoverage(
                venue_id=venue_id,
                venue_name=venue_name,
                total_net_sales=Decimal("0"),
                covered_net_sales=Decimal("0"),
                coverage_pct=None,
            ),
        )
        total_dec = Decimal(total or 0)
        covered_dec = Decimal(covered or 0)
        coverage.total_net_sales += total_dec
        coverage.covered_net_sales += covered_dec
        coverage.categories.append(
            SliceCoverage(
                label=category or "other",
                total_net_sales=total_dec,
                covered_net_sales=covered_dec,
                coverage_pct=_pct(covered_dec, total_dec),
            )
        )

    ordered = sorted(venues.values(), key=lambda v: v.venue_name)
    for venue in ordered:
        venue.coverage_pct = _pct(venue.covered_net_sales, venue.total_net_sales)
        venue.categories.sort(key=lambda c: c.label or "")
    return ordered


def group_coverage(venue_rows: list[VenueCoverage]) -> SliceCoverage:
    """Aggregate the per-venue rows into one group-level slice."""
    if not venue_rows:
        return _empty_slice(_GROUP_LABEL)
    total = sum((v.total_net_sales for v in venue_rows), Decimal("0"))
    covered = sum((v.covered_net_sales for v in venue_rows), Decimal("0"))
    return SliceCoverage(
        label=_GROUP_LABEL,
        total_net_sales=total,
        covered_net_sales=covered,
        coverage_pct=_pct(covered, total),
    )


def category_group_coverage(venue_rows: list[VenueCoverage]) -> list[SliceCoverage]:
    """Category breakdown aggregated across the given venue rows."""
    merged: dict[str, list[Decimal]] = {}
    for venue in venue_rows:
        for cat in venue.categories:
            totals = merged.setdefault(cat.label, [Decimal("0"), Decimal("0")])
            totals[0] += cat.total_net_sales
            totals[1] += cat.covered_net_sales
    rows = [
        SliceCoverage(
            label=label,
            total_net_sales=totals[0],
            covered_net_sales=totals[1],
            coverage_pct=_pct(totals[1], totals[0]),
        )
        for label, totals in sorted(merged.items())
    ]
    return rows
