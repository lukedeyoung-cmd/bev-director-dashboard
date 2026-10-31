"""SQLAlchemy models; importing this package registers every table on db.metadata."""

from app.models.catalog import Ingredient, PosItem
from app.models.inventory import InventoryCount, InventoryCountLine
from app.models.mapping import PosItemIngredientMap
from app.models.purchasing import (
    Invoice,
    InvoiceLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Vendor,
)
from app.models.recipe import Recipe, RecipeIngredient
from app.models.sales import DailySales, DailyVenueTotals
from app.models.user import User
from app.models.venue import Venue

__all__ = [
    "DailySales",
    "DailyVenueTotals",
    "Ingredient",
    "InventoryCount",
    "InventoryCountLine",
    "Invoice",
    "InvoiceLine",
    "PosItem",
    "PosItemIngredientMap",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Recipe",
    "RecipeIngredient",
    "User",
    "Vendor",
    "Venue",
]
