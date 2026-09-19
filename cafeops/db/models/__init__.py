"""All models. Import this module to populate Base.metadata."""

from __future__ import annotations

from cafeops.db.base import Base
from cafeops.db.models.composition import (
    DrinkTemplate,
    LegacyStagedRecipe,
    Modifier,
    SizeProfile,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.db.models.enums import (
    ChecklistStatus,
    ComponentRole,
    ModifierAction,
    MovementType,
    OrderChannel,
    POStatus,
    PriceSource,
    SaleChannel,
    SizeCode,
    Tier,
    Unit,
)
from cafeops.db.models.ingredient import Ingredient, IngredientPrice
from cafeops.db.models.menu import ManualRecipeLine, MenuItem, MenuItemCost
from cafeops.db.models.par import ParLevel
from cafeops.db.models.purchase_order import ChecklistResponse, POLine, PurchaseOrder
from cafeops.db.models.sale import Sale
from cafeops.db.models.stock import DriftObservation, StockCount, StockMovement
from cafeops.db.models.supplier import Supplier, SupplierProduct

__all__ = [
    "Base",
    "ChecklistResponse",
    "ChecklistStatus",
    "ComponentRole",
    "DriftObservation",
    "DrinkTemplate",
    "Ingredient",
    "IngredientPrice",
    "LegacyStagedRecipe",
    "ManualRecipeLine",
    "MenuItem",
    "MenuItemCost",
    "Modifier",
    "ModifierAction",
    "MovementType",
    "OrderChannel",
    "POLine",
    "POStatus",
    "ParLevel",
    "PriceSource",
    "PurchaseOrder",
    "Sale",
    "SaleChannel",
    "SizeCode",
    "SizeProfile",
    "StockCount",
    "StockMovement",
    "Supplier",
    "SupplierProduct",
    "TemplateComponent",
    "Tier",
    "Unit",
    "VariantAxis",
    "VariantOption",
]
