"""Pure transforms from Toast API payloads into ingest records.

Money model (validated against a live tenant's business date, 2026-10-29):
- gross = quantity x preDiscountPrice (falls back to price when Toast omits
  preDiscountPrice), per non-voided selection and per modifier line.
- discount = the line's own appliedDiscounts[].discountAmount (comps land in
  the same array — observed "Buy 1 get 1 free" and 100% comps), plus
  check-level discounts allocated across the check's lines in proportion to
  each line's gross.
- net = gross - discount, floored at zero per line: a comp can exceed pre-tax
  gross because it may also cover tax (observed: 77.00 gross / 102.00
  discounts / check.amount 0.00); the unallocatable excess is dropped.
- check.amount is NOT the reconciliation target: it is tax-inclusive
  (observed uniform ~x1.035 on non-discounted checks) and reflects refunds.
  daily_sales deliberately tracks pre-tax beverage dollars.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.toast.categories import OTHER, classify_category

CENT = Decimal("0.01")


def to_money(value: object) -> Decimal:
    """Parse Toast's float money/quantity values into cents-quantized Decimals."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# ----------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------
MENU_ITEM = "menu_item"
MODIFIER = "modifier"


@dataclass
class CatalogRecord:
    """One pos_items upsert candidate extracted from a Toast catalog source."""

    pos_guid: str
    name: str
    item_type: str
    category: str
    unit_price: Decimal | None = None
    # Menus-v2 records are authoritative for name/type/category; config-v2
    # records only fill gaps so they never overwrite a menus classification.
    authoritative: bool = True


def extract_catalog_records(menus_payload: dict) -> list[CatalogRecord]:
    """Walk the menus-v2 tree into catalog records.

    Real payload shape (probed): {menus: [{menuGroups: [{name, menuItems:
    [{guid, name, price, ...}], menuGroups: [...]}]}]}. Group nesting is
    recursive; each item inherits its group path (deepest first) for
    category classification.
    """
    records: dict[str, CatalogRecord] = {}

    def visit_group(group: dict, ancestors: list[str]) -> None:
        context = [group.get("name"), *ancestors]  # deepest group first
        for item in group.get("menuItems") or []:
            guid = item.get("guid")
            if not guid:
                continue
            name = (item.get("name") or "").strip()
            existing = records.get(guid)
            # The same guid can appear under several menus; keep the first
            # sighting, but never let an empty name win.
            if existing is None or (not existing.name and name):
                raw_price = item.get("price")
                records[guid] = CatalogRecord(
                    pos_guid=guid,
                    name=name,
                    item_type=MENU_ITEM,
                    category=classify_category(*context, item.get("name")),
                    unit_price=to_money(raw_price) if raw_price is not None else None,
                )
        for subgroup in group.get("menuGroups") or []:
            visit_group(subgroup, context)

    for menu in menus_payload.get("menus") or []:
        for group in menu.get("menuGroups") or []:
            visit_group(group, [])

    return list(records.values())


def extract_config_records(config_items: Iterable[dict]) -> list[CatalogRecord]:
    """Config-v2 menuItems (all items and modifiers, no prices) as gap-fillers."""
    return [
        CatalogRecord(
            pos_guid=item["guid"],
            name=(item.get("name") or "").strip(),
            item_type=MENU_ITEM,
            category=classify_category(item.get("name")),
            authoritative=False,
        )
        for item in config_items
        if item.get("guid")
    ]


# ----------------------------------------------------------------------
# Sales aggregation
# ----------------------------------------------------------------------
@dataclass
class SaleLineAggregate:
    """Running per-item sums for one venue + business date."""

    pos_guid: str
    display_name: str
    item_type: str
    category: str
    quantity: Decimal = Decimal("0")
    gross_sales: Decimal = Decimal("0")
    discount_total: Decimal = Decimal("0")

    @property
    def net_sales(self) -> Decimal:
        return self.gross_sales - self.discount_total


@dataclass
class AggregationStats:
    """Observed counts from one business date's orders payload."""

    orders_seen: int = 0
    orders_voided: int = 0
    # Non-voided checks containing at least one non-voided selection — the
    # digest's cover count (persisted as daily_venue_totals.check_count).
    checks_seen: int = 0
    selections_seen: int = 0
    selections_voided: int = 0
    open_items_without_guid: int = 0
    dropped_discount_excess: Decimal = Decimal("0")


@dataclass
class _CheckLine:
    """One non-voided line on one check, for proportional discount allocation."""

    aggregate: SaleLineAggregate
    gross: Decimal  # this line's gross only, not the date-level running total
    selection_discount: Decimal


def _accumulate(
    aggregates: dict[str, SaleLineAggregate],
    stats: AggregationStats,
    selection: dict,
    item_type: str,
    fallback_category: str | None = None,
) -> tuple[SaleLineAggregate, Decimal] | None:
    """Fold one selection (or modifier) into the aggregates.

    Returns the item's aggregate plus this line's gross (they differ once an
    item sells on several checks in the same date); None if unkeyable.
    """
    guid = ((selection.get("item") or {}).get("guid")) or selection.get("itemGuid")
    if not guid:
        # Open items have no POS catalog identity; counted and skipped rather
        # than given fabricated ids.
        stats.open_items_without_guid += 1
        return None
    stats.selections_seen += 1
    quantity = to_money(selection.get("quantity"))
    unit_price = (
        selection["preDiscountPrice"]
        if selection.get("preDiscountPrice") is not None
        else selection.get("price")
    )
    gross = (quantity * to_money(unit_price)).quantize(CENT, rounding=ROUND_HALF_UP)
    display_name = (selection.get("displayName") or "").strip()
    line = aggregates.get(guid)
    if line is None:
        category = classify_category(display_name)
        if category == OTHER and fallback_category:
            # Modifiers ("Tito's splash") usually have no classifiable name of
            # their own — inherit the parent selection's category.
            category = fallback_category
        line = SaleLineAggregate(
            pos_guid=guid,
            display_name=display_name,
            item_type=item_type,
            category=category,
        )
        aggregates[guid] = line
    elif display_name and not line.display_name:
        line.display_name = display_name
    line.quantity += quantity
    line.gross_sales += gross
    return line, gross


def _discount_amount(applied_discounts: Iterable[dict] | None) -> Decimal:
    return sum(
        (to_money(d.get("discountAmount")) for d in applied_discounts or []),
        Decimal("0"),
    )


def _allocate_discounts(
    lines: list[_CheckLine],
    check_discounts: Iterable[dict],
    stats: AggregationStats,
) -> None:
    """Apply discounts to one check's lines, floored at zero net per line.

    Selection-level discounts apply to their own line first (capped at that
    line's gross; the overflow joins the check-level pool). Check-level
    discounts then spread proportionally to gross, but each line can only
    absorb what its gross has left after its own discount — a comp that also
    covers tax therefore pools on other lines or is dropped and reported
    (stats.dropped_discount_excess) rather than pushing any line negative.
    """
    if not lines:
        return
    total_gross = sum((line.gross for line in lines), Decimal("0"))
    if total_gross <= 0:
        return  # nothing priced to allocate against

    applied: list[Decimal] = []
    pool = Decimal("0")
    for line in lines:
        capped = min(line.selection_discount, line.gross)
        applied.append(capped)
        pool += line.selection_discount - capped
    pool += _discount_amount(check_discounts)

    allocated: list[Decimal] = [Decimal("0")] * len(lines)
    if pool > 0:
        allocated = [
            (pool * line.gross / total_gross).quantize(CENT, rounding=ROUND_HALF_UP)
            for line in lines
        ]
        residue = pool - sum(allocated, Decimal("0"))
        if residue:
            largest = max(range(len(lines)), key=lambda i: lines[i].gross)
            allocated[largest] += residue

        order = sorted(range(len(lines)), key=lambda i: lines[i].gross, reverse=True)
        for i in order:
            # Capacity is what the line's own discount hasn't already consumed
            # (observed: a comped line whose comp also covers tax).
            capacity = lines[i].gross - applied[i]
            excess = allocated[i] - capacity
            if excess <= 0:
                continue
            allocated[i] = capacity
            for j in order:
                if j == i:
                    continue
                headroom = lines[j].gross - applied[j] - allocated[j]
                if headroom <= 0:
                    continue
                take = min(headroom, excess)
                allocated[j] += take
                excess -= take
                if excess == 0:
                    break
            if excess > 0:
                stats.dropped_discount_excess += excess

    for line, own, share in zip(lines, applied, allocated, strict=True):
        line.aggregate.discount_total += own + share


def aggregate_orders(
    orders: list[dict],
) -> tuple[dict[str, SaleLineAggregate], AggregationStats]:
    """Aggregate one business date's orders into per-item sums.

    Returns aggregates keyed by Toast item guid. Voided orders, checks, and
    selections are excluded entirely (Toast's reporting guidance); modifiers
    aggregate as their own rows keyed by the modifier item's guid.
    """
    stats = AggregationStats()
    aggregates: dict[str, SaleLineAggregate] = {}

    for order in orders:
        stats.orders_seen += 1
        if order.get("voided") or order.get("deleted"):
            stats.orders_voided += 1
            continue
        for check in order.get("checks") or []:
            if check.get("voided") or check.get("deleted"):
                continue
            check_lines: list[_CheckLine] = []
            for selection in check.get("selections") or []:
                if selection.get("voided"):
                    stats.selections_voided += 1
                    continue
                accumulated = _accumulate(aggregates, stats, selection, MENU_ITEM)
                if accumulated is not None:
                    line, gross = accumulated
                    check_lines.append(
                        _CheckLine(
                            aggregate=line,
                            gross=gross,
                            selection_discount=_discount_amount(
                                selection.get("appliedDiscounts")
                            ),
                        )
                    )
                for modifier in selection.get("modifiers") or []:
                    if modifier.get("voided"):
                        stats.selections_voided += 1
                        continue
                    mod_accumulated = _accumulate(
                        aggregates, stats, modifier, MODIFIER,
                        fallback_category=line.category if line is not None else None,
                    )
                    if mod_accumulated is not None:
                        mod_line, mod_gross = mod_accumulated
                        check_lines.append(
                            _CheckLine(
                                aggregate=mod_line,
                                gross=mod_gross,
                                selection_discount=_discount_amount(
                                    modifier.get("appliedDiscounts")
                                ),
                            )
                        )
            _allocate_discounts(check_lines, check.get("appliedDiscounts") or [], stats)

    return aggregates, stats
