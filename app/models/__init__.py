"""SQLAlchemy models; importing this package registers every table on db.metadata."""

from app.models.catalog import Ingredient, PosItem
from app.models.inventory import InventoryCount, InventoryCountLine
from app.models.mapping import PosItemIngredientMap, ToastItemMap
from app.models.purchasing import (
    Invoice,
    InvoiceLine,
    PurchaseOrder,
    PurchaseOrderLine,
    R365Item,
    R365Location,
    R365SyncState,
    R365Transaction,
    R365TransactionDetail,
    R365Vendor,
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
    "R365Item",
    "R365Location",
    "R365SyncState",
    "R365Transaction",
    "R365TransactionDetail",
    "R365Vendor",
    "Recipe",
    "RecipeIngredient",
    "ToastItemMap",
    "User",
    "Vendor",
    "Venue",
]
