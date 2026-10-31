"""Daily beverage sales aggregates, at the POS-item grain."""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin


class DailySales(TimestampMixin, db.Model):
    """One row per venue, business date, and POS item sold.

    Venue- and category-level rollups are queries over this table rather than
    separate rows. `category` snapshots the item's beverage category so
    historical rows keep their grouping when catalog items change.
    """

    __tablename__ = "daily_sales"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=False, index=True
    )
    # Venue-local business date (e.g. Toast business date).
    business_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    pos_item_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("pos_items.id", ondelete="restrict"), nullable=False
    )
    category: Mapped[str | None] = mapped_column(sa.String(40))
    quantity_sold: Mapped[Decimal] = mapped_column(
        sa.Numeric(12, 3), nullable=False, default=Decimal("0")
    )
    gross_sales: Mapped[Decimal] = mapped_column(
        sa.Numeric(14, 2), nullable=False, default=Decimal("0")
    )
    discount_total: Mapped[Decimal] = mapped_column(
        sa.Numeric(14, 2), nullable=False, default=Decimal("0")
    )
    net_sales: Mapped[Decimal] = mapped_column(
        sa.Numeric(14, 2), nullable=False, default=Decimal("0")
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "venue_id", "business_date", "pos_item_id", name="uq_daily_sales_venue_date_item"
        ),
    )


class DailyVenueTotals(TimestampMixin, db.Model):
    """One row per venue and business date: order-level counts the item-grain
    rows cannot carry.

    `check_count` is the digest's cover count — the number of non-voided
    checks that contained at least one non-voided selection. Written by the
    Toast sales sync in the same transaction as that date's daily_sales rows,
    so the totals row is always consistent with the item rows it complements.
    """

    __tablename__ = "daily_venue_totals"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    check_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    __table_args__ = (
        sa.UniqueConstraint(
            "venue_id", "business_date", name="uq_daily_venue_totals_venue_date"
        ),
    )
