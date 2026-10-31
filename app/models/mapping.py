"""POS-item-to-ingredient mapping: the bridge between sales and cost."""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin


class PosItemIngredientMap(TimestampMixin, db.Model):
    """How much of an ingredient one sale of a POS item consumes.

    This is the costing bridge: daily_sales (what sold) joined through this
    table to ingredients and their purchase prices gives actual consumption
    and theoretical cost. A POS item may map to several ingredients
    (e.g. a garnished cocktail maps to spirit, mixer, and garnish).
    """

    __tablename__ = "pos_item_ingredient_maps"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    pos_item_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("pos_items.id", ondelete="cascade"), nullable=False, index=True
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("ingredients.id", ondelete="restrict"), nullable=False, index=True
    )
    # Ingredient quantity consumed per one sale of the POS item.
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(sa.String(24))
    # False for secondary links (e.g. garnish) when a recipe is owned by
    # another mapping; kept for flexible costing overrides.
    is_primary: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(sa.String(500))

    __table_args__ = (
        sa.UniqueConstraint(
            "pos_item_id", "ingredient_id", name="uq_pos_item_ingredient_maps_pair"
        ),
    )
