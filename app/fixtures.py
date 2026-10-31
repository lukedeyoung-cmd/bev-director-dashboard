"""Fixture data: clearly-marked synthetic sales, catalog, and cost data.

Everything this module creates is synthetic and deterministic (fixed RNG
seed), for developing and demoing the dashboard before live ingestion
lands. Fixture rows are marked with the `fx-` prefix on external refs
(pos_guids, invoice refs) and fictional venue names, and the CLI command
prints a visible notice. `seed-fixtures --reset` removes exactly the rows
this module created — nothing else.

Venue names, items, prices, and volumes are fictional and do not represent
any actual Land & Sea Dept location.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

import click
import sqlalchemy as sa

from app.extensions import db
from app.models.catalog import Ingredient, PosItem
from app.models.mapping import PosItemIngredientMap
from app.models.purchasing import Invoice, InvoiceLine, Vendor
from app.models.sales import DailySales
from app.models.venue import Venue

MARKER = "fx-"
SEED = 20261031
DAYS_OF_HISTORY = 28

# (name, category, unit price, share of venue beverage sales, pour map).
# Pour maps list (ingredient name, quantity per sale, unit) — used only where
# cost data is ingested for the venue.
VENUES: list[dict] = [
    {
        "name": "Harborline Public House",
        "code": "HLP",
        "base_daily": 5200,
        "drift": -0.0011,  # softening week over week
        "items": [
            ("Draft IPA", "beer", 8.00, 0.30, [("Draft Beer", "16", "oz")]),
            ("Draft Pilsner", "beer", 7.00, 0.24, [("Draft Beer", "16", "oz")]),
            ("Bottled Lager", "beer", 6.00, 0.12, [("Draft Beer", "12", "oz")]),
            ("House Red · Glass", "wine", 9.00, 0.10, [("House Red Wine", "5", "oz")]),
            ("House White · Glass", "wine", 9.00, 0.08, [("House White Wine", "5", "oz")]),
            ("Old Fashioned", "cocktail", 14.00, 0.08,
             [("Bourbon", "2.0", "oz"), ("Demerara Syrup", "0.25", "oz"),
              ("Angostura Bitters", "0.03", "oz")]),
            ("House Margarita", "cocktail", 13.00, 0.05,
             [("Tequila Blanco", "2.0", "oz"), ("Lime Juice", "0.75", "oz"),
              ("Agave Syrup", "0.5", "oz")]),
            ("Cold Brew · NA", "na", 5.00, 0.03, None),
        ],
        "has_cost_data": True,
    },
    {
        "name": "Cellar & Vine",
        "code": "CVN",
        "base_daily": 4100,
        "drift": 0.0004,
        "items": [
            ("House Red · Glass", "wine", 10.00, 0.34, [("House Red Wine", "5", "oz")]),
            ("Reserve Red · Glass", "wine", 15.00, 0.14, [("House Red Wine", "5", "oz")]),
            ("House White · Glass", "wine", 10.00, 0.18, [("House White Wine", "5", "oz")]),
            ("Skin Contact Orange", "wine", 14.00, 0.06, [("House White Wine", "5", "oz")]),
            ("Draft Pilsner", "beer", 8.00, 0.12, [("Draft Beer", "16", "oz")]),
            ("Vodka Martini", "cocktail", 15.00, 0.09,
             [("Vodka", "2.5", "oz"), ("Dry Vermouth", "0.5", "oz")]),
            ("Espresso Martini", "cocktail", 16.00, 0.04,
             [("Vodka", "1.5", "oz"), ("Cold Brew Concentrate", "1.5", "oz"),
              ("Demerara Syrup", "0.5", "oz")]),
            ("NA Sparkling", "na", 7.00, 0.03, None),
        ],
        "has_cost_data": True,
    },
    {
        "name": "Fern & Fiddle",
        "code": "FFD",
        "base_daily": 3400,
        "drift": 0.0013,  # growing — drives the top-movers board
        "items": [
            ("Gin & Tonic", "cocktail", 12.00, 0.26,
             [("London Dry Gin", "2.0", "oz"), ("Tonic Syrup", "1.0", "oz")]),
            ("Paper Plane", "cocktail", 14.00, 0.18,
             [("Bourbon", "0.75", "oz"), ("London Dry Gin", "0.75", "oz"),
              ("Lemon Juice", "0.75", "oz")]),
            ("Rye Manhattan", "cocktail", 15.00, 0.12,
             [("Rye Whiskey", "2.0", "oz"), ("Sweet Vermouth", "1.0", "oz")]),
            ("Draft IPA", "beer", 8.00, 0.18, [("Draft Beer", "16", "oz")]),
            ("House Red · Glass", "wine", 10.00, 0.14, [("House Red Wine", "5", "oz")]),
            ("House White · Glass", "wine", 10.00, 0.08, [("House White Wine", "5", "oz")]),
            ("Sparkling Lemonade · NA", "na", 6.00, 0.04, None),
        ],
        "has_cost_data": False,
    },
    {
        "name": "Copper Lark",
        "code": "CLK",
        "base_daily": 3000,
        "drift": 0.0,
        "items": [
            ("Draft IPA", "beer", 8.00, 0.22, [("Draft Beer", "16", "oz")]),
            ("Michelada", "beer", 9.00, 0.12, [("Draft Beer", "12", "oz")]),
            ("House Red · Glass", "wine", 9.00, 0.18, [("House Red Wine", "5", "oz")]),
            ("House White · Glass", "wine", 9.00, 0.12, [("House White Wine", "5", "oz")]),
            ("Paloma", "cocktail", 13.00, 0.20,
             [("Tequila Blanco", "2.0", "oz"), ("Lime Juice", "0.75", "oz"),
              ("Grapefruit Soda", "2.0", "oz")]),
            ("Daiquiri", "cocktail", 12.00, 0.08,
             [("Lime Juice", "0.75", "oz"), ("Demerara Syrup", "0.25", "oz")]),
            ("NA IPA", "na", 7.00, 0.05, None),
            ("Cold Brew · NA", "na", 5.00, 0.03, None),
        ],
        "has_cost_data": False,
    },
]

# Ingredient purchase costs per base unit (fixture pricing, not real).
INGREDIENTS: list[tuple[str, str, str, float]] = [
    ("Bourbon", "spirit", "oz", 0.95),
    ("Rye Whiskey", "spirit", "oz", 1.05),
    ("London Dry Gin", "spirit", "oz", 0.80),
    ("Vodka", "spirit", "oz", 0.55),
    ("Tequila Blanco", "spirit", "oz", 1.05),
    ("Sweet Vermouth", "wine", "oz", 0.45),
    ("Dry Vermouth", "wine", "oz", 0.40),
    ("House Red Wine", "wine", "oz", 0.85),
    ("House White Wine", "wine", "oz", 0.80),
    ("Draft Beer", "beer", "oz", 0.28),
    ("Tonic Syrup", "mixer", "oz", 0.08),
    ("Demerara Syrup", "mixer", "oz", 0.08),
    ("Agave Syrup", "mixer", "oz", 0.12),
    ("Grapefruit Soda", "mixer", "oz", 0.10),
    ("Lime Juice", "mixer", "oz", 0.10),
    ("Lemon Juice", "mixer", "oz", 0.10),
    ("Angostura Bitters", "mixer", "oz", 0.25),
    ("Cold Brew Concentrate", "coffee", "oz", 0.30),
    ("NA Sparkling", "na", "oz", 0.22),
]


def _fixture_venue_codes() -> list[str]:
    return [v["code"] for v in VENUES]


def _reset(session) -> None:
    """Delete only rows created by this seeder (fx- markers / fixture codes)."""
    venue_ids = list(
        session.scalars(sa.select(Venue.id).where(Venue.code.in_(_fixture_venue_codes()))).all()
    )
    if venue_ids:
        session.execute(sa.delete(DailySales).where(DailySales.venue_id.in_(venue_ids)))
        invoice_ids = list(
            session.scalars(sa.select(Invoice.id).where(Invoice.venue_id.in_(venue_ids))).all()
        )
        if invoice_ids:
            session.execute(sa.delete(InvoiceLine).where(InvoiceLine.invoice_id.in_(invoice_ids)))
        session.execute(sa.delete(Invoice).where(Invoice.venue_id.in_(venue_ids)))
        item_ids = list(
            session.scalars(sa.select(PosItem.id).where(PosItem.venue_id.in_(venue_ids))).all()
        )
        if item_ids:
            session.execute(
                sa.delete(PosItemIngredientMap).where(PosItemIngredientMap.pos_item_id.in_(item_ids))
            )
        session.execute(sa.delete(PosItem).where(PosItem.venue_id.in_(venue_ids)))
        session.execute(sa.delete(Venue).where(Venue.id.in_(venue_ids)))
    session.execute(sa.delete(Ingredient).where(sa.func.lower(Ingredient.external_ref).like("fx-%")))
    session.execute(sa.delete(Vendor).where(sa.func.lower(Vendor.external_ref).like("fx-%")))


def seed_fixtures(end_date: date | None = None, reset: bool = False) -> dict:
    """Seed the database with clearly-marked fixture data. Idempotent: a
    second run is a no-op unless `reset` is passed."""
    session = db.session
    existing = session.scalar(sa.select(Venue.id).where(Venue.code.in_(_fixture_venue_codes())))
    if existing is not None and not reset:
        return {"skipped": True}
    if reset:
        _reset(session)

    end_date = end_date or (date.today() - timedelta(days=1))
    rng = random.Random(SEED)
    counts = {"venues": 0, "items": 0, "sales_days": 0, "invoices": 0, "maps": 0}

    ingredients: dict[str, Ingredient] = {}
    for name, category, base_unit, _unit_cost in INGREDIENTS:
        ingredient = Ingredient(
            name=name,
            category=category,
            base_unit=base_unit,
            external_ref=f"{MARKER}ing-{name.lower().replace(' ', '-')}",
        )
        ingredients[name] = ingredient
        db.session.add(ingredient)

    vendor = Vendor(name="Fixture Beverage Distributor", external_ref=f"{MARKER}vendor-1")
    db.session.add(vendor)

    # One shared ingredient id table flushed so maps/invoices can reference it.
    session.flush()

    for spec in VENUES:
        venue = Venue(
            name=spec["name"],
            code=spec["code"],
            pos_venue_ref=f"{MARKER}pos-{spec['code']}",
        )
        session.add(venue)
        session.flush()
        counts["venues"] += 1

        items: list[tuple[PosItem, float, list | None]] = []
        for name, category, price, weight, pour_map in spec["items"]:
            slug = name.lower().replace(" ", "-").replace("·", "").replace("--", "-")
            item = PosItem(
                venue_id=venue.id,
                pos_guid=f"{MARKER}item-{spec['code'].lower()}-{slug}",
                name=name,
                item_type="menu_item",
                category=category,
                unit_price=Decimal(str(price)),
            )
            session.add(item)
            session.flush()  # assign item.id before pour maps reference it
            items.append((item, weight, pour_map))
            if pour_map:
                for ing_name, qty, unit in pour_map:
                    if Decimal(qty) <= 0:
                        continue  # placeholder pours with no mapped volume
                    session.add(
                        PosItemIngredientMap(
                            pos_item_id=item.id,
                            ingredient_id=ingredients[ing_name].id,
                            quantity=Decimal(qty),
                            unit=unit,
                        )
                    )
                    counts["maps"] += 1
        counts["items"] += len(items)
        session.flush()

        # Daily sales: venue baseline × weekend factor × trend drift × noise.
        day_factor: dict[date, Decimal] = {}
        for offset in range(DAYS_OF_HISTORY):
            day = end_date - timedelta(days=DAYS_OF_HISTORY - 1 - offset)
            weekday = day.weekday()  # 0=Mon ... 6=Sun
            weekend = {4: 1.35, 5: 1.45, 6: 1.10}.get(weekday, 1.0)
            drift = 1.0 + spec["drift"] * offset
            noise = rng.uniform(0.88, 1.12)
            day_factor[day] = Decimal(str(round(spec["base_daily"] * weekend * drift * noise, 2)))

        for day, day_total in day_factor.items():
            for item, weight, _pour_map in items:
                gross = day_total * Decimal(str(weight)) * Decimal(str(rng.uniform(0.9, 1.1)))
                if gross < 1:
                    continue
                price = Decimal(str(_price_of(item, spec["items"])))
                quantity = int(gross / price)
                if quantity <= 0:
                    quantity = 1
                gross = price * quantity
                spike = rng.random() < 0.04  # occasional heavy-discount day
                rate = rng.uniform(0.10, 0.18) if spike else rng.uniform(0.005, 0.03)
                discount = (gross * Decimal(str(rate))).quantize(Decimal("0.01"))
                session.add(
                    DailySales(
                        venue_id=venue.id,
                        business_date=day,
                        pos_item_id=item.id,
                        category=item.category,
                        quantity_sold=Decimal(quantity),
                        gross_sales=gross,
                        discount_total=discount,
                        net_sales=gross - discount,
                    )
                )
            counts["sales_days"] += 1

        # Cost data: invoices exist only for venues flagged has_cost_data, so
        # the drill-down demonstrates both cost-KPI states.
        if spec["has_cost_data"]:
            for line_spec in _invoice_lines(spec["code"]):
                invoice = Invoice(
                    venue_id=venue.id,
                    vendor_id=vendor.id,
                    invoice_number=f"{MARKER}inv-{spec['code']}-{line_spec['offset']}",
                    invoice_date=end_date - timedelta(days=line_spec["offset"]),
                    total=Decimal("0"),
                    external_ref=f"{MARKER}inv-{spec['code']}-{line_spec['offset']}",
                )
                session.add(invoice)
                session.flush()
                total = Decimal("0")
                for line_number, (ing_name, quantity, unit_cost) in enumerate(
                    line_spec["lines"], start=1
                ):
                    ingredient = ingredients[ing_name]
                    line_total = (Decimal(str(quantity)) * Decimal(str(unit_cost))).quantize(
                        Decimal("0.01")
                    )
                    session.add(
                        InvoiceLine(
                            invoice_id=invoice.id,
                            line_number=line_number,
                            ingredient_id=ingredient.id,
                            quantity=Decimal(str(quantity)),
                            unit_of_measure=ingredient.base_unit,
                            unit_cost=Decimal(str(unit_cost)),
                            line_total=line_total,
                        )
                    )
                    total += line_total
                invoice.total = total
                counts["invoices"] += 1

    session.commit()
    return counts


def _price_of(item: PosItem, specs) -> float:
    for name, _cat, price, _w, _m in specs:
        if name == item.name:
            return price
    return 10.0


def _invoice_lines(code: str) -> list[dict]:
    """Two invoices per costed venue, sized like a real delivery cycle.

    Quantities are oz per delivery (a 1,200 oz bourbon order is ~40 bottles);
    at these volumes plus keg/case unit costs, actual-vs-theoretical lands in
    a plausible 10–15% pour-cost band instead of rounding-error territory.
    """
    if code == "HLP":
        return [
            {"offset": 12, "lines": [
                ("Bourbon", 2500, 0.94), ("Draft Beer", 63000, 0.11),
                ("Demerara Syrup", 1300, 0.08),
            ]},
            {"offset": 5, "lines": [
                ("Bourbon", 1900, 0.97), ("Tequila Blanco", 2100, 1.04), ("Lime Juice", 1300, 0.10),
            ]},
        ]
    return [
        {"offset": 11, "lines": [("House Red Wine", 6300, 0.55), ("Vodka", 7400, 0.55)]},
        {"offset": 4, "lines": [
            ("House Red Wine", 5000, 0.55),
            ("House White Wine", 5900, 0.52),
            ("Cold Brew Concentrate", 1050, 0.31),
        ]},
    ]


@click.command("seed-fixtures")
@click.option("--reset", is_flag=True, help="Delete existing fixture rows first, then reseed.")
def seed_fixtures_command(reset: bool) -> None:
    """Seed clearly-marked synthetic demo data (fixtures, not real sales)."""
    counts = seed_fixtures(reset=reset)
    if counts.get("skipped"):
        click.echo("Fixture data already present; pass --reset to reseed.")
        return
    click.echo(
        f"FIXTURE DATA — synthetic, clearly marked ({MARKER} prefix), not real sales: "
        f"{counts['venues']} venues, {counts['items']} items, "
        f"{counts['sales_days']} venue-days of sales, {counts['maps']} pour maps, "
        f"{counts['invoices']} invoices."
    )


def register_fixture_commands(app) -> None:
    app.cli.add_command(seed_fixtures_command)
