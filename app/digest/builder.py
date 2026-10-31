"""Digest data assembly: pure queries over the same tables the dashboard reads.

Same anchor convention as app/analytics/periods — the digest covers the newest
business date present in the data, not "yesterday" by the clock, so a delayed
nightly sync produces a digest of the real latest day instead of an empty one.
The rendered email always states the date it covers.

Cost metrics follow the dashboard's None-means-no-data discipline: COGS% cells
and cost alerts simply do not appear until invoice ingestion (R365) lands.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.analytics.metrics import (
    Mover,
    category_mix,
    cost_summary,
    item_movers,
    sales_summary,
)
from app.analytics.periods import Period, Window
from app.models.catalog import Ingredient
from app.models.inventory import InventoryCount, InventoryCountLine
from app.models.purchasing import Invoice, InvoiceLine
from app.models.sales import DailySales, DailyVenueTotals
from app.models.venue import Venue
from app.web.filters import money, money_cents, pct_plain, qty

ZERO = Decimal("0")

# Alert knobs. Overridable per call so config stays in the caller (service).
DEFAULT_SWING_PCT = 40.0  # day net vs trailing-28-day daily mean
DEFAULT_SWING_FLOOR = Decimal("200")  # min trailing daily mean before judging
DEFAULT_VARIANCE_PCT = 10.0  # actual vs theoretical pour-cost variance
DEFAULT_PRICE_PCT = 10.0  # invoice unit-cost move vs the prior invoice
PRICE_LOOKBACK_DAYS = 14  # how far back to hunt for unit-cost moves

# Caps so one noisy category cannot flood the alerts section.
MAX_MOVERS = 5
MAX_PRICE_ALERTS = 5
MAX_INVENTORY_ALERTS = 5

TREND_WINDOWS = (7, 14, 28)  # trailing 1/2/4 weeks
SWING_BASELINE_DAYS = 28


def _dec(value: object) -> Decimal:
    """Coerce a Numeric/float aggregate to Decimal (sqlite returns floats)."""
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class AlertThresholds:
    swing_pct: float = DEFAULT_SWING_PCT
    swing_floor: Decimal = DEFAULT_SWING_FLOOR
    variance_pct: float = DEFAULT_VARIANCE_PCT
    price_pct: float = DEFAULT_PRICE_PCT


@dataclass(frozen=True)
class VenueDay:
    """One venue's day: sales, covers, average check, voids/comps."""

    venue_id: uuid.UUID
    name: str
    code: str
    net_sales: Decimal
    gross_sales: Decimal
    void_comp_total: Decimal
    prior_net_sales: Decimal | None
    covers: int | None  # None = no check-count data yet (pre-dates covers ingest)

    @property
    def change_pct(self) -> float | None:
        if self.prior_net_sales is None or self.prior_net_sales == 0:
            return None
        return float((self.net_sales - self.prior_net_sales) / self.prior_net_sales * 100)

    @property
    def avg_check(self) -> Decimal | None:
        if self.covers is None or self.covers == 0:
            return None
        return (self.net_sales / self.covers).quantize(Decimal("0.01"))

    @property
    def void_comp_rate(self) -> float:
        if self.gross_sales == 0:
            return 0.0
        return float(self.void_comp_total / self.gross_sales * 100)


@dataclass(frozen=True)
class TrendRow:
    """One trailing window: sales, mix shares, and COGS% (None pre-cost data)."""

    label: str
    net_sales: Decimal
    prior_net_sales: Decimal | None
    mix: list  # list[metrics.CategorySlice] — kept loose to avoid the import cycle
    cogs_pct: float | None

    @property
    def change_pct(self) -> float | None:
        if self.prior_net_sales is None or self.prior_net_sales == 0:
            return None
        return float((self.net_sales - self.prior_net_sales) / self.prior_net_sales * 100)

    @property
    def mix_text(self) -> str:
        """Compact mix shares for the email cell: "Beer 34% · Wine 22%"."""
        return " · ".join(f"{slice_.label} {slice_.share:.0%}" for slice_ in self.mix)


ALERT_LABELS = {
    "sales_swing": "Sales swing",
    "cost_variance": "Pour variance",
    "price_change": "Price change",
    "low_inventory": "Low inventory",
}


@dataclass(frozen=True)
class Alert:
    kind: str  # key of ALERT_LABELS
    message: str

    @property
    def display_label(self) -> str:
        return ALERT_LABELS.get(self.kind, self.kind.replace("_", " ").title())


@dataclass(frozen=True)
class DigestData:
    business_date: date | None
    dashboard_url: str
    venues: list[VenueDay]
    movers_up: list  # list[metrics.Mover]
    movers_down: list
    trends: list[TrendRow]
    alerts: list[Alert]
    cost_data_present: bool

    @property
    def group_net(self) -> Decimal:
        return sum((v.net_sales for v in self.venues), ZERO)

    @property
    def group_gross(self) -> Decimal:
        return sum((v.gross_sales for v in self.venues), ZERO)

    @property
    def group_void_comp_total(self) -> Decimal:
        return sum((v.void_comp_total for v in self.venues), ZERO)

    @property
    def group_covers(self) -> int | None:
        counts = [v.covers for v in self.venues if v.covers is not None]
        return sum(counts) if counts else None

    @property
    def group_avg_check(self) -> Decimal | None:
        covers = self.group_covers
        if not covers:
            return None
        return (self.group_net / covers).quantize(Decimal("0.01"))

    @property
    def group_void_comp_rate(self) -> float:
        gross = self.group_gross
        if gross == 0:
            return 0.0
        return float(self.group_void_comp_total / gross * 100)


def resolve_digest_date(session: Session) -> date | None:
    """The business date the digest covers: newest business date in the data."""
    return session.scalar(sa.select(sa.func.max(DailySales.business_date)))


def _day_sums(session: Session, day: date) -> dict[uuid.UUID, tuple[Decimal, Decimal, Decimal]]:
    """Per-venue (gross, discounts, net) for one business date."""
    rows = session.execute(
        sa.select(
            DailySales.venue_id,
            sa.func.coalesce(sa.func.sum(DailySales.gross_sales), 0),
            sa.func.coalesce(sa.func.sum(DailySales.discount_total), 0),
            sa.func.coalesce(sa.func.sum(DailySales.net_sales), 0),
        )
        .where(DailySales.business_date == day)
        .group_by(DailySales.venue_id)
    ).all()
    return {vid: (_dec(g), _dec(d), _dec(n)) for vid, g, d, n in rows}


def _covers(session: Session, day: date) -> dict[uuid.UUID, int]:
    rows = session.execute(
        sa.select(DailyVenueTotals.venue_id, DailyVenueTotals.check_count).where(
            DailyVenueTotals.business_date == day
        )
    ).all()
    return {vid: count for vid, count in rows}


def _venue_days(session: Session, day: date) -> list[VenueDay]:
    """Yesterday-by-venue rows, ranked by net sales descending.

    A venue appears when it has sales rows or a check-count row for the day —
    a synced-but-zero day still shows (covers 0, $0), while venues with no
    data at all are simply absent.
    """
    current = _day_sums(session, day)
    covers_map = _covers(session, day)
    if not current and not covers_map:
        return []
    prior = _day_sums(session, day - timedelta(days=1))

    venues = session.scalars(
        sa.select(Venue).where(Venue.id.in_(set(current) | set(covers_map)))
    ).all()
    by_id = {v.id: v for v in venues}

    rows = [
        VenueDay(
            venue_id=vid,
            name=venue.name,
            code=venue.code,
            net_sales=net,
            gross_sales=gross,
            void_comp_total=discount,
            prior_net_sales=(prior[vid][2] if vid in prior else None),
            covers=covers_map.get(vid),
        )
        for vid, (gross, discount, net) in current.items()
        if (venue := by_id.get(vid)) is not None
    ]
    # Venues with a check-count row but no item rows (synced zero-sales day).
    rows.extend(
        VenueDay(
            venue_id=vid,
            name=venue.name,
            code=venue.code,
            net_sales=ZERO,
            gross_sales=ZERO,
            void_comp_total=ZERO,
            prior_net_sales=(prior[vid][2] if vid in prior else None),
            covers=count,
        )
        for vid, count in covers_map.items()
        if vid not in current and (venue := by_id.get(vid)) is not None
    )
    rows.sort(key=lambda r: r.net_sales, reverse=True)
    return rows


def _trends(
    session: Session, venue_ids: list[uuid.UUID], anchor: date
) -> tuple[list[TrendRow], bool]:
    """Trailing 1/2/4-week rows plus whether any cost data exists in scope."""
    rows: list[TrendRow] = []
    cost_data_present = False
    for days in TREND_WINDOWS:
        period = Period(start=anchor - timedelta(days=days - 1), end=anchor)
        summary = sales_summary(session, venue_ids, period)
        cost = cost_summary(session, venue_ids, period)
        if cost is not None:
            cost_data_present = True
        rows.append(
            TrendRow(
                label=f"Last {days} days",
                net_sales=summary.net_sales,
                prior_net_sales=summary.prior_net_sales,
                mix=category_mix(session, venue_ids, period),
                cogs_pct=cost.cogs_pct if cost else None,
            )
        )
    return rows, cost_data_present


def _sales_swing_alerts(
    session: Session, venue_days: list[VenueDay], anchor: date, thresholds: AlertThresholds
) -> list[Alert]:
    """Venues whose day landed far from their trailing-28-day daily mean."""
    if not venue_days:
        return []
    baseline = Period(
        start=anchor - timedelta(days=SWING_BASELINE_DAYS),
        end=anchor - timedelta(days=1),
    )
    totals = dict(
        session.execute(
            sa.select(DailySales.venue_id, sa.func.sum(DailySales.net_sales))
            .where(
                DailySales.venue_id.in_([v.venue_id for v in venue_days]),
                DailySales.business_date.between(baseline.start, baseline.end),
            )
            .group_by(DailySales.venue_id)
        ).all()
    )

    alerts: list[Alert] = []
    for venue in venue_days:
        mean = _dec(totals.get(venue.venue_id)) / SWING_BASELINE_DAYS
        if mean < thresholds.swing_floor:
            continue
        swing_pct = float((venue.net_sales - mean) / mean * 100)
        if abs(swing_pct) < thresholds.swing_pct:
            continue
        direction = "above" if swing_pct > 0 else "below"
        alerts.append(
            Alert(
                kind="sales_swing",
                message=(
                    f"{venue.code} net sales {money(venue.net_sales)} — "
                    f"{abs(swing_pct):.0f}% {direction} the {money(mean)}/day "
                    f"trailing-{SWING_BASELINE_DAYS}-day average"
                ),
            )
        )
    return alerts


def _cost_variance_alerts(
    session: Session, venue_ids: list[uuid.UUID], anchor: date, thresholds: AlertThresholds
) -> list[Alert]:
    """Pour-cost variance beyond threshold for the trailing week."""
    if not venue_ids:
        return []
    period = Period(start=anchor - timedelta(days=6), end=anchor)
    cost = cost_summary(session, venue_ids, period)
    if cost is None or cost.theoretical_cost is None or cost.theoretical_cost == 0:
        return []
    variance_pct = float(cost.variance_dollars / cost.theoretical_cost * 100)
    if abs(variance_pct) < thresholds.variance_pct:
        return []
    over_under = "over" if variance_pct > 0 else "under"
    return [
        Alert(
            kind="cost_variance",
            message=(
                f"Pour cost ran {pct_plain(variance_pct)} {over_under} theoretical "
                f"({money(cost.variance_dollars)}) over the last 7 days"
            ),
        )
    ]


def _price_change_alerts(
    session: Session, venue_ids: list[uuid.UUID], anchor: date, thresholds: AlertThresholds
) -> list[Alert]:
    """Ingredients whose invoiced unit cost moved vs their prior invoice."""
    if not venue_ids:
        return []
    lookback_start = anchor - timedelta(days=PRICE_LOOKBACK_DAYS)
    scope = Invoice.venue_id.in_(venue_ids)

    ranked = (
        sa.select(
            InvoiceLine.ingredient_id.label("ingredient_id"),
            InvoiceLine.unit_cost.label("unit_cost"),
            Invoice.invoice_date.label("invoice_date"),
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
            scope,
            # Same-unit lines only: converting case→bottle prices would lie.
            InvoiceLine.unit_of_measure == Ingredient.base_unit,
            Invoice.invoice_date >= lookback_start,
        )
        .cte("digest_price_basis")
    )
    rows = session.execute(
        sa.select(
            ranked.c.ingredient_id, ranked.c.rn, ranked.c.unit_cost, ranked.c.invoice_date
        ).where(ranked.c.rn.in_((1, 2)))
    ).all()

    latest: dict[uuid.UUID, tuple[Decimal, date]] = {}
    previous: dict[uuid.UUID, Decimal] = {}
    for ingredient_id, rn, unit_cost, invoice_date in rows:
        if rn == 1:
            latest[ingredient_id] = (_dec(unit_cost), invoice_date)
        else:
            previous[ingredient_id] = _dec(unit_cost)

    names = dict(
        session.execute(
            sa.select(Ingredient.id, Ingredient.name).where(Ingredient.id.in_(set(latest)))
        ).all()
    )

    alerts: list[Alert] = []
    for ingredient_id, (current_cost, invoice_date) in latest.items():
        prior_cost = previous.get(ingredient_id)
        if prior_cost is None or prior_cost == 0:
            continue
        move_pct = float((current_cost - prior_cost) / prior_cost * 100)
        if abs(move_pct) < thresholds.price_pct:
            continue
        name = names.get(ingredient_id, "Unknown ingredient")
        direction = "up" if move_pct > 0 else "down"
        alerts.append(
            Alert(
                kind="price_change",
                message=(
                    f"{name} unit cost {direction} {abs(move_pct):.0f}% — "
                    f"{money_cents(prior_cost)} → {money_cents(current_cost)} "
                    f"(invoice {invoice_date:%b %-d})"
                ),
            )
        )
    alerts.sort(key=lambda a: a.message)
    return alerts[:MAX_PRICE_ALERTS]


def _low_inventory_alerts(session: Session, venue_ids: list[uuid.UUID]) -> list[Alert]:
    """Ingredients counted at or below zero on their most recent final count."""
    if not venue_ids:
        return []
    ranked = (
        sa.select(
            InventoryCount.venue_id.label("venue_id"),
            InventoryCountLine.ingredient_id.label("ingredient_id"),
            InventoryCountLine.quantity_on_hand.label("quantity_on_hand"),
            InventoryCount.count_date.label("count_date"),
            sa.func.row_number()
            .over(
                partition_by=(InventoryCount.venue_id, InventoryCountLine.ingredient_id),
                order_by=(InventoryCount.count_date.desc(), InventoryCountLine.id.desc()),
            )
            .label("rn"),
        )
        .join(InventoryCount, InventoryCount.id == InventoryCountLine.count_id)
        .where(InventoryCount.status == "final", InventoryCount.venue_id.in_(venue_ids))
        .cte("digest_inventory_basis")
    )
    rows = session.execute(
        sa.select(
            ranked.c.venue_id,
            ranked.c.ingredient_id,
            ranked.c.quantity_on_hand,
            ranked.c.count_date,
        ).where(ranked.c.rn == 1, ranked.c.quantity_on_hand <= 0)
    ).all()
    if not rows:
        return []

    names = dict(
        session.execute(
            sa.select(Ingredient.id, Ingredient.name).where(
                Ingredient.id.in_({r[1] for r in rows})
            )
        ).all()
    )
    codes = dict(
        session.execute(
            sa.select(Venue.id, Venue.code).where(Venue.id.in_({r[0] for r in rows}))
        ).all()
    )

    alerts = [
        Alert(
            kind="low_inventory",
            message=(
                f"{names.get(ingredient_id, 'Unknown ingredient')} counted at "
                f"{qty(quantity_on_hand)} at {codes.get(venue_id, '?')} "
                f"(count {count_date:%b %-d})"
            ),
        )
        for venue_id, ingredient_id, quantity_on_hand, count_date in rows
    ]
    alerts.sort(key=lambda a: a.message)
    return alerts[:MAX_INVENTORY_ALERTS]


def build_alerts(
    session: Session, venue_days: list[VenueDay], anchor: date, thresholds: AlertThresholds
) -> list[Alert]:
    """All alert kinds for the digest; each degrades to [] without its data."""
    venue_ids = [v.venue_id for v in venue_days]
    return [
        *(_sales_swing_alerts(session, venue_days, anchor, thresholds)),
        *(_cost_variance_alerts(session, venue_ids, anchor, thresholds)),
        *(_price_change_alerts(session, venue_ids, anchor, thresholds)),
        *(_low_inventory_alerts(session, venue_ids)),
    ]


def _split_movers(
    raw: tuple[list[Mover], list[Mover]],
) -> tuple[list[Mover], list[Mover]]:
    """Re-bucket analytics movers by sign for the email's ▲/▼ lists.

    item_movers returns a sign-mixed "top" list when few items move; the
    digest renders up and down as distinct sections, so split and cap here.
    """
    movers = [*raw[0], *raw[1]]
    up = sorted((m for m in movers if m.delta > 0), key=lambda m: m.delta, reverse=True)
    down = sorted((m for m in movers if m.delta < 0), key=lambda m: m.delta)
    return up[:MAX_MOVERS], down[:MAX_MOVERS]


def build_digest(
    session: Session,
    *,
    dashboard_url: str,
    business_date: date | None = None,
    thresholds: AlertThresholds | None = None,
) -> DigestData | None:
    """Assemble everything the digest email renders.

    Returns None when the database holds no sales data at all — callers
    surface that as an error rather than emailing an empty shell.
    """
    thresholds = thresholds or AlertThresholds()
    day = business_date or resolve_digest_date(session)
    if day is None:
        return None

    venues = _venue_days(session, day)
    venue_ids = [v.venue_id for v in venues]
    movers_window = Window(
        current=Period(start=day, end=day),
        prior=Period(start=day - timedelta(days=1), end=day - timedelta(days=1)),
    )
    movers_up, movers_down = _split_movers(item_movers(session, venue_ids, movers_window))
    trends, cost_data_present = _trends(session, venue_ids, day)

    return DigestData(
        business_date=day,
        dashboard_url=dashboard_url,
        venues=venues,
        movers_up=movers_up,
        movers_down=movers_down,
        trends=trends,
        alerts=build_alerts(session, venues, day, thresholds),
        cost_data_present=cost_data_present,
    )


__all__ = [
    "Alert",
    "AlertThresholds",
    "DigestData",
    "TrendRow",
    "VenueDay",
    "build_alerts",
    "build_digest",
    "resolve_digest_date",
]
