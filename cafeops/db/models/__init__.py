"""All models. Import this module to populate Base.metadata."""

from __future__ import annotations

from cafeops.db.base import Base
from cafeops.db.models.batch import Season, StockBatch
from cafeops.db.models.channel import (
    AgentActionLog,
    ChannelItemMetric,
    ChannelMetric,
    TescoRouting,
)
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
    AgentToolOutcome,
    ChannelSourceKind,
    ChecklistStatus,
    ComponentRole,
    ModifierAction,
    MovementType,
    OrderChannel,
    POStatus,
    PriceSource,
    SaleChannel,
    SalesChannelName,
    SizeCode,
    Storage,
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
    "AgentActionLog",
    "AgentToolOutcome",
    "Base",
    "ChannelItemMetric",
    "ChannelMetric",
    "ChannelSourceKind",
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
    "SalesChannelName",
    "Season",
    "SizeCode",
    "SizeProfile",
    "StockBatch",
    "StockCount",
    "StockMovement",
    "Storage",
    "Supplier",
    "SupplierProduct",
    "TemplateComponent",
    "TescoRouting",
    "Tier",
    "Unit",
    "VariantAxis",
    "VariantOption",
]
