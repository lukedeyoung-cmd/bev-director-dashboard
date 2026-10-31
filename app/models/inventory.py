"""Inventory counts (header + counted lines)."""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin


class InventoryCount(TimestampMixin, db.Model):
    """A physical inventory count session at a venue."""

    __tablename__ = "inventory_counts"

    STATUSES = ("draft", "final")

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=False, index=True
    )
    count_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        sa.Enum(*STATUSES, name="count_status", native_enum=False), nullable=False, default="draft"
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)

    lines: Mapped[list["InventoryCountLine"]] = relationship(
        back_populates="count",
        cascade="all, delete-orphan",
        order_by="InventoryCountLine.line_number",
    )


class InventoryCountLine(db.Model):
    __tablename__ = "inventory_count_lines"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    count_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("inventory_counts.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("ingredients.id", ondelete="restrict"), nullable=False
    )
    # Counted quantity in the ingredient's base unit (e.g. bottles, oz).
    quantity_on_hand: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    # Cost snapshot at count time, in the ingredient's base unit; nullable so
    # a count is never blocked on missing cost data.
    unit_cost: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))

    count: Mapped[InventoryCount] = relationship(back_populates="lines")
