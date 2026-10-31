"""Dashboard metrics: every number on the group and venue pages.

Pure query functions scoped to a set of venue ids and a reporting window.
They return small dataclasses (or None) so templates never touch the ORM.
Cost metrics (`cost_summary`) read the same schema the R365 ingestion will
populate; with no cost data yet they resolve to None and the KPI header
hides those cards without any template rework.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.analytics.periods import Period, Window
from app.models.catalog import Ingredient, PosItem
from app.models.mapping import PosItemIngredientMap
from app.models.purchasing import Invoice, InvoiceLine
from app.models.sales import DailySales
from app.models.venue import Venue

ZERO = Decimal("0")

BUCKET_LABELS = {
    "beer": "Beer",
    "wine": "Wine",
    "spirits": "Spirits",
    "na": "NA",
    "other": "Other",
}

# Ingredient categories that count toward beverage COGS (actual cost side).
INGREDIENT_COST_CATEGORIES = (
    "beer",
    "wine",
    "spirit",
    "cocktail",
    "na",
    "mixer",
    "garnish",
    "coffee",
    "tea",
)


def category_bucket(category: str | None) -> str:
    """Normalize a free-form category snapshot onto the four mix buckets.

    Beverage grouping is beer / wine / spirits / NA; anything else (food,
    retail, uncategorized) lands in Other so the mix always sums.
    """
    key = (category or "").strip().lower()
    if key == "beer":
        return "beer"
    if key == "wine":
        return "wine"
    if key in {"spirit", "spirits", "cocktail", "cocktails", "liquor"}:
        return "spirits"
    if key in {"na", "non-alc", "non_alc", "nonalcoholic"}:
        return "na"
    return "other"


def _dec(value: object) -> Decimal:
    """Coerce a Numeric/float aggregate to Decimal (sqlite returns floats)."""
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _change(current: Decimal, prior: Decimal | None) -> Decimal | None:
    """Absolute change vs prior, or None when there is no prior data."""
    if prior is None:
        return None
    return current - prior


def _change_pct(current: Decimal, prior: Decimal | None) -> float | None:
    if prior is None or prior == 0:
        return None
    return float((current - prior) / prior * 100)


def _sales_scope(venue_ids: list[uuid.UUID]):
    return DailySales.venue_id.in_(venue_ids) if venue_ids else sa.false()


@dataclass(frozen=True)
class SalesSummary:
    gross_sales: Decimal
    discount_total: Decimal
    net_sales: Decimal
    prior_net_sales: Decimal | None

    @property
    def net_change(self) -> Decimal | None:
        return _change(self.net_sales, self.prior_net_sales)

    @property
    def net_change_pct(self) -> float | None:
        return _change_pct(self.net_sales, self.prior_net_sales)

    @property
    def discount_rate(self) -> float:
        if self.gross_sales == 0:
            return 0.0
        return float(self.discount_total / self.gross_sales * 100)


def sales_summary(session: Session, venue_ids: list[uuid.UUID], period: Period) -> SalesSummary:
    """Net/gross/discount totals for the period, with prior-period net sales."""

    def _sums(p: Period) -> tuple[Decimal, Decimal, Decimal]:
        row = session.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(DailySales.gross_sales), 0),
                sa.func.coalesce(sa.func.sum(DailySales.discount_total), 0),
                sa.func.coalesce(sa.func.sum(DailySales.net_sales), 0),
            ).where(
                _sales_scope(venue_ids), DailySales.business_date.between(p.start, p.end)
            )
        ).one()
        return _dec(row[0]), _dec(row[1]), _dec(row[2])

    gross, discounts, net = _sums(period)
    prior_net = _sums(period.prior())[2]
    return SalesSummary(
        gross_sales=gross,
        discount_total=discounts,
        net_sales=net,
        prior_net_sales=prior_net if prior_net != 0 else None,
    )


@dataclass(frozen=True)
class TrendPoint:
    label: str
    current: Decimal | None
    prior: Decimal | None


def daily_trend(session: Session, venue_ids: list[uuid.UUID], window: Window) -> list[TrendPoint]:
    """Net sales per day across the window, with the prior period overlaid.

    Points are aligned by position in the window (day 1 vs day 1) so the
    prior series stays comparable even though the weekdays differ.
    """
    current: dict[date, Decimal] = {}
    prior: dict[date, Decimal] = {}
    for p, bucket in ((window.current, current), (window.prior, prior)):
        rows = session.execute(
            sa.select(DailySales.business_date, sa.func.sum(DailySales.net_sales))
            .where(_sales_scope(venue_ids), DailySales.business_date.between(p.start, p.end))
            .group_by(DailySales.business_date)
        ).all()
        for d, net in rows:
            bucket[d] = _dec(net)

    points: list[TrendPoint] = []
    for offset in range(window.current.days):
        day = window.current.start + timedelta(days=offset)
        prior_day = window.prior.start + timedelta(days=offset)
        points.append(
            TrendPoint(
                label=f"{day.month}/{day.day}",
                current=current.get(day),
                prior=prior.get(prior_day),
            )
        )
    return points


@dataclass(frozen=True)
class CategorySlice:
    bucket: str
    label: str
    net_sales: Decimal
    share: float


def category_mix(
    session: Session, venue_ids: list[uuid.UUID], period: Period
) -> list[CategorySlice]:
    """Net sales share by beverage bucket over the period, fixed order."""
    rows = session.execute(
        sa.select(DailySales.category, sa.func.sum(DailySales.net_sales))
        .where(
            _sales_scope(venue_ids), DailySales.business_date.between(period.start, period.end)
        )
        .group_by(DailySales.category)
    ).all()
    totals: dict[str, Decimal] = {}
    for cat, net in rows:
        bucket = category_bucket(cat)
        totals[bucket] = totals.get(bucket, ZERO) + _dec(net)
    grand = sum(totals.values(), ZERO)
    order = ["beer", "wine", "spirits", "na", "other"]
    return [
        CategorySlice(
            bucket=b,
            label=BUCKET_LABELS[b],
            net_sales=totals[b],
            share=float(totals[b] / grand) if grand else 0.0,
        )
        for b in order
        if b in totals and totals[b] != 0
    ]


@dataclass(frozen=True)
class VenueRank:
    venue_id: uuid.UUID
    name: str
    code: str
    net_sales: Decimal
    share: float
    prior_net_sales: Decimal | None
    change: Decimal | None
    change_pct: float | None
    top_bucket_label: str | None


def venue_rankings(session: Session, period: Period) -> list[VenueRank]:
    """Per-venue net sales for the period, ranked by sales descending."""

    def _by_venue(p: Period) -> dict[uuid.UUID, Decimal]:
        rows = session.execute(
            sa.select(DailySales.venue_id, sa.func.sum(DailySales.net_sales))
            .where(DailySales.business_date.between(p.start, p.end))
            .group_by(DailySales.venue_id)
        ).all()
        return {vid: _dec(net) for vid, net in rows}

    current = _by_venue(period)
    if not current:
        return []
    prior = _by_venue(period.prior())

    top_bucket: dict[uuid.UUID, tuple[str, Decimal]] = {}
    for vid, cat, net in session.execute(
        sa.select(
            DailySales.venue_id, DailySales.category, sa.func.sum(DailySales.net_sales)
        )
        .where(DailySales.business_date.between(period.start, period.end))
        .group_by(DailySales.venue_id, DailySales.category)
    ).all():
        known = top_bucket.get(vid)
        if known is None or _dec(net) > known[1]:
            top_bucket[vid] = (category_bucket(cat), _dec(net))

    venues = session.scalars(sa.select(Venue).where(Venue.id.in_(current.keys()))).all()
    names = {v.id: v for v in venues}
    grand = sum(current.values(), ZERO)
    ranks: list[VenueRank] = []
    for vid, net in current.items():
        venue = names.get(vid)
        if venue is None:  # rows reference a venue deleted mid-window
            continue
        prior_net = prior.get(vid)
        ranks.append(
            VenueRank(
                venue_id=vid,
                name=venue.name,
                code=venue.code,
                net_sales=net,
                share=float(net / grand) if grand else 0.0,
                prior_net_sales=prior_net if prior_net else None,
                change=_change(net, prior_net if prior_net else None),
                change_pct=_change_pct(net, prior_net if prior_net else None),
                top_bucket_label=(
                    BUCKET_LABELS.get(top_bucket[vid][0]) if vid in top_bucket else None
                ),
            )
        )
    ranks.sort(key=lambda r: r.net_sales, reverse=True)
    return ranks


@dataclass(frozen=True)
class Mover:
    item_name: str
    venue_code: str
    current: Decimal
    prior: Decimal | None
    delta: Decimal
    qty_delta: Decimal


def item_movers(
    session: Session, venue_ids: list[uuid.UUID], window: Window, limit: int = 5
) -> tuple[list[Mover], list[Mover]]:
    """Items with the largest net-sales increase (top) and decrease (bottom)
    vs the prior period."""

    def _by_item(p: Period) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
        rows = session.execute(
            sa.select(
                DailySales.pos_item_id,
                sa.func.sum(DailySales.net_sales),
                sa.func.sum(DailySales.quantity_sold),
            )
            .where(_sales_scope(venue_ids), DailySales.business_date.between(p.start, p.end))
            .group_by(DailySales.pos_item_id)
        ).all()
        return {pid: (_dec(net), _dec(qty)) for pid, net, qty in rows}

    current = _by_item(window.current)
    prior = _by_item(window.prior)
    item_ids = set(current) | set(prior)
    if not item_ids:
        return [], []

    name_rows = session.execute(
        sa.select(PosItem.id, PosItem.name, Venue.code)
        .join(Venue, Venue.id == PosItem.venue_id)
        .where(PosItem.id.in_(item_ids))
    ).all()
    names = {pid: (name, code) for pid, name, code in name_rows}

    movers: list[Mover] = []
    for pid in item_ids:
        label = names.get(pid)
        if label is None:
            continue
        cur_net, cur_qty = current.get(pid, (ZERO, ZERO))
        pri_net, pri_qty = prior.get(pid, (ZERO, ZERO))
        delta = cur_net - pri_net
        if delta == 0:
            continue
        movers.append(
            Mover(
                item_name=label[0],
                venue_code=label[1],
                current=cur_net,
                prior=pri_net if pri_net != 0 else None,
                delta=delta,
                qty_delta=cur_qty - pri_qty,
            )
        )
    movers.sort(key=lambda m: m.delta, reverse=True)
    top = movers[:limit]
    if len(movers) <= limit:
        return top, []
    bottom = [m for m in reversed(movers) if all(m != t for t in top)][:limit]
    return top, bottom


@dataclass(frozen=True)
class DiscountRow:
    item_name: str
    venue_code: str
    gross_sales: Decimal
    discount_total: Decimal

    @property
    def rate(self) -> float:
        if self.gross_sales == 0:
            return 0.0
        return float(self.discount_total / self.gross_sales * 100)


def discount_watch(
    session: Session, venue_ids: list[uuid.UUID], period: Period, limit: int = 8
) -> list[DiscountRow]:
    """Items carrying the most discount dollars in the period.

    At the daily-aggregate grain, comps and void value surface as discount
    totals; order-level void/comps reasons arrive with order-grain Toast data.
    """
    rows = session.execute(
        sa.select(
            PosItem.name,
            Venue.code,
            sa.func.sum(DailySales.gross_sales),
            sa.func.sum(DailySales.discount_total),
        )
        .select_from(DailySales)
        .join(PosItem, PosItem.id == DailySales.pos_item_id)
        .join(Venue, Venue.id == DailySales.venue_id)
        .where(
            _sales_scope(venue_ids), DailySales.business_date.between(period.start, period.end)
        )
        .group_by(PosItem.name, Venue.code)
        .having(sa.func.sum(DailySales.discount_total) > 0)
        .order_by(sa.func.sum(DailySales.discount_total).desc())
        .limit(limit)
    ).all()
    return [
        DiscountRow(
            item_name=name,
            venue_code=code,
            gross_sales=_dec(gross),
            discount_total=_dec(disc),
        )
        for name, code, gross, disc in rows
    ]


@dataclass(frozen=True)
class CostSummary:
    """Actual vs theoretical beverage cost for the period.

    actual_cost sums beverage-category invoice lines dated in the period;
    theoretical_cost applies each mapped ingredient's latest exact-unit purchase
    cost to what was poured (sales × map quantity × unit cost). Ingredients
    invoiced in a unit that doesn't match their base unit are excluded from the
    theoretical basis until the costing layer handles conversions.

    Percentages divide by the net sales of the venues that have invoices in the
    period (`net_sales`), never the whole scope: otherwise one un-costed venue
    would dilute the group ratio into a number that flatters the program.
    """

    actual_cost: Decimal | None
    theoretical_cost: Decimal | None
    net_sales: Decimal
    costed_venues: int
    scope_venues: int

    @property
    def partial_coverage(self) -> bool:
        return self.costed_venues < self.scope_venues

    @property
    def cogs_pct(self) -> float | None:
        if self.actual_cost is None or self.net_sales == 0:
            return None
        return float(self.actual_cost / self.net_sales * 100)

    @property
    def pour_cost_pct(self) -> float | None:
        if self.theoretical_cost is None or self.net_sales == 0:
            return None
        return float(self.theoretical_cost / self.net_sales * 100)

    @property
    def variance_dollars(self) -> Decimal | None:
        if self.actual_cost is None or self.theoretical_cost is None:
            return None
        return self.actual_cost - self.theoretical_cost


def cost_summary(
    session: Session,
    venue_ids: list[uuid.UUID],
    period: Period,
) -> CostSummary | None:
    """Cost KPIs, or None when the scope has no invoices in the period.

    The None path is what keeps the KPI header honest before R365 ingestion
    connects: no cost data, no cost cards.
    """
    invoice_scope = Invoice.venue_id.in_(venue_ids) if venue_ids else sa.false()

    # Venues with at least one invoice dated in the period: the only venues
    # whose activity can be costed. Their net sales is the ratio denominator.
    costed_ids = session.scalars(
        sa.select(Invoice.venue_id)
        .where(invoice_scope, Invoice.invoice_date.between(period.start, period.end))
        .distinct()
    ).all()
    if not costed_ids:
        return None

    costed_net = session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(DailySales.net_sales), 0)).where(
            DailySales.venue_id.in_(costed_ids),
            DailySales.business_date.between(period.start, period.end),
        )
    )

    actual_row = session.execute(
        sa.select(sa.func.coalesce(sa.func.sum(InvoiceLine.line_total), 0))
        .select_from(InvoiceLine)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(Ingredient, Ingredient.id == InvoiceLine.ingredient_id)
        .where(
            invoice_scope,
            Invoice.invoice_date.between(period.start, period.end),
            sa.func.lower(Ingredient.category).in_(INGREDIENT_COST_CATEGORIES),
        )
    ).scalar_one()
    actual = _dec(actual_row)

    # Latest exact-unit purchase cost per ingredient, as of the period end.
    ranked = (
        sa.select(
            InvoiceLine.ingredient_id.label("ingredient_id"),
            InvoiceLine.unit_cost.label("unit_cost"),
            sa.func.row_number()
            .over(
                partition_by=InvoiceLine.ingredient_id,
                order_by=(Invoice.invoice_date.desc(), InvoiceLine.line_number.desc()),
            )
            .label("rn"),
        )
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(Ingredient, Ingredient.id == InvoiceLine.ingredient_id)
        .where(
            invoice_scope,
            InvoiceLine.unit_of_measure == Ingredient.base_unit,
            Invoice.invoice_date <= period.end,
        )
        .cte("unit_cost_basis")
    )
    latest = (
        sa.select(ranked.c.ingredient_id, ranked.c.unit_cost)
        .where(ranked.c.rn == 1)
        .cte("latest_unit_cost")
    )
    theo_row = session.execute(
        sa.select(
            sa.func.coalesce(
                sa.func.sum(
                    DailySales.quantity_sold
                    * PosItemIngredientMap.quantity
                    * latest.c.unit_cost
                ),
                0,
            )
        )
        .select_from(DailySales)
        .join(PosItemIngredientMap, PosItemIngredientMap.pos_item_id == DailySales.pos_item_id)
        .join(latest, latest.c.ingredient_id == PosItemIngredientMap.ingredient_id)
        .where(
            # Price only the costed venues' pours: without a purchase price in
            # scope an ingredient has no basis, and pricing the rest of the
            # group against a costed-only denominator would skew pour cost.
            _sales_scope(costed_ids),
            DailySales.business_date.between(period.start, period.end),
        )
    ).scalar_one()

    actual = actual if actual != 0 else None
    theoretical = _dec(theo_row)
    theoretical = theoretical if theoretical != 0 else None
    if actual is None and theoretical is None:
        return None
    return CostSummary(
        actual_cost=actual,
        theoretical_cost=theoretical,
        net_sales=_dec(costed_net),
        costed_venues=len(costed_ids),
        scope_venues=len(venue_ids),
    )


__all__ = [
    "BUCKET_LABELS",
    "CategorySlice",
    "CostSummary",
    "DiscountRow",
    "Mover",
    "SalesSummary",
    "TrendPoint",
    "VenueRank",
    "category_bucket",
    "category_mix",
    "cost_summary",
    "daily_trend",
    "discount_watch",
    "item_movers",
    "sales_summary",
    "venue_rankings",
]
