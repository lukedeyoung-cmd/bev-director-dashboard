"""Recipes (preparation specs) and their ingredient requirements."""

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin


class Recipe(TimestampMixin, db.Model):
    """How a drink (or prep item) is built: ingredient quantities per portion.

    venue_id is nullable so group-level spec recipes can exist before they
    are tied to a specific venue's POS item.
    """

    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    # The POS menu item this recipe produces, once known.
    pos_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("pos_items.id", ondelete="set null"), nullable=True, index=True
    )
    portion_quantity: Mapped[Decimal | None] = mapped_column(sa.Numeric(10, 3))
    portion_unit: Mapped[str | None] = mapped_column(sa.String(24))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeIngredient.line_number",
    )


class RecipeIngredient(db.Model):
    __tablename__ = "recipe_ingredients"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("recipes.id", ondelete="cascade"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("ingredients.id", ondelete="restrict"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    # Unit of `quantity` (oz, each, dash, ...); interpreted against the
    # ingredient's base_unit by the costing layer.
    unit: Mapped[str | None] = mapped_column(sa.String(24))

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")

    __table_args__ = (
        sa.UniqueConstraint("recipe_id", "ingredient_id", name="uq_recipe_ingredients_pair"),
    )
