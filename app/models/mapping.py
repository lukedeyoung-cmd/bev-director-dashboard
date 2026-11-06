"""POS-item-to-ingredient mapping: the bridge between sales and cost."""

import uuid
from datetime import UTC, datetime
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



class ToastItemMap(TimestampMixin, db.Model):
    """A reviewed (or proposed) identity link: one Toast item -> one R365 item.

    The costing bridge. Every pour-cost and variance number joins Toast sales
    through this table to R365 purchasing data, so an unmapped or wrong link
    silently distorts the math — which is why review state is part of the
    model, not an afterthought.

    `r365_item_id` stores the R365 Item view's itemId verbatim with no FK
    constraint (same philosophy as the r365_* mirrors): re-syncing reference
    data can never orphan or cascade-delete review decisions. A stale target
    (item deleted in R365) surfaces in the review UI as a missing label.
    One non-rejected mapping per Toast item is enforced by a partial unique
    index; rejected rows remain as the audit trail of declined candidates.
    """

    __tablename__ = "toast_item_maps"

    STATUSES = ("suggested", "approved", "rejected")
    SOURCES = ("auto", "manual")
    # Statuses that occupy the item's single active slot (see partial index).
    ACTIVE_STATUSES = ("suggested", "approved")

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    pos_item_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("pos_items.id", ondelete="cascade"), nullable=False, index=True
    )
    r365_item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        sa.Enum(*STATUSES, name="toast_item_map_status", native_enum=False),
        nullable=False,
        default="suggested",
        index=True,
    )
    # Engine score at suggestion time, 0..1; None for hand-set mappings.
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4))
    # "auto" = proposed by the suggestion engine, "manual" = set by a reviewer.
    source: Mapped[str] = mapped_column(
        sa.Enum(*SOURCES, name="toast_item_map_source", native_enum=False),
        nullable=False,
        default="auto",
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="set null")
    )
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(sa.String(500))

    ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
        "suggested": frozenset({"approved", "rejected"}),
        "approved": frozenset({"rejected", "suggested"}),
        "rejected": frozenset({"approved", "suggested"}),
    }

    __table_args__ = (
        sa.UniqueConstraint("pos_item_id", "r365_item_id", name="uq_toast_item_maps_pair"),
        sa.Index(
            "ux_toast_item_maps_active_target",
            "pos_item_id",
            unique=True,
            sqlite_where=sa.text("status IN ('suggested', 'approved')"),
            postgresql_where=sa.text("status IN ('suggested', 'approved')"),
        ),
    )

    def transition(self, new_status: str, *, user_id: uuid.UUID | None = None) -> None:
        """Move the mapping to a new review status, recording the decision.

        Raises ValueError on an unknown or disallowed status change; the
        caller decides how to surface that (routes flash, tests assert).
        """
        if new_status not in self.STATUSES:
            raise ValueError(f"Unknown mapping status: {new_status!r}")
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise ValueError(f"Cannot move mapping from {self.status!r} to {new_status!r}")
        self.status = new_status
        self.decided_at = datetime.now(UTC)
        self.decided_by_user_id = user_id

    def __repr__(self) -> str:  # pragma: no cover — debugging aid only
        return f"<ToastItemMap {self.status} pos={self.pos_item_id} r365={self.r365_item_id}>"
