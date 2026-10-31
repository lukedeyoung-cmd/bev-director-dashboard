"""POS catalog (menu items + modifiers) and purchased ingredients."""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin


class PosItem(TimestampMixin, db.Model):
    """An item from a venue's POS catalog: either a menu item or a modifier."""

    __tablename__ = "pos_items"

    ITEM_TYPES = ("menu_item", "modifier")

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=False, index=True
    )
    # Stable identifier from the POS system (e.g. Toast item GUID).
    pos_guid: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    item_type: Mapped[str] = mapped_column(
        sa.Enum(*ITEM_TYPES, name="pos_item_type", native_enum=False), nullable=False
    )
    # Beverage category snapshot (beer, wine, cocktail, NA, food, ...).
    category: Mapped[str | None] = mapped_column(sa.String(40))
    unit_price: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    __table_args__ = (sa.UniqueConstraint("venue_id", "pos_guid", name="uq_pos_items_venue_guid"),)


class Ingredient(TimestampMixin, db.Model):
    """A purchased product tracked for costing (spirit bottle, keg, case, produce, ...)."""

    __tablename__ = "ingredients"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False, index=True)
    # spirit, wine, beer, NA, produce, dry_good, ...
    category: Mapped[str | None] = mapped_column(sa.String(40))
    base_unit: Mapped[str] = mapped_column(sa.String(24), nullable=False, default="each")
    # Identifier in the purchasing system (e.g. R365 item), set when connected.
    external_ref: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
