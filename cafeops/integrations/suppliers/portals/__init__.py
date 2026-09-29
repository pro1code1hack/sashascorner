"""Supplier portal adapters. Importing the package registers every adapter.

Add a portal: one module here with a class decorated `@register_portal("name hint")`,
then import it below. Nothing else needs to change: the worker, the API listing and
the CLI all read `REGISTRY`.
"""

# Adapters register on import. Keep this list explicit so a missing import is a
# visible diff, not a silent "no integration". (cups_direct and monolith build on
# generic and import it themselves, so the order here does not matter.)
from cafeops.integrations.suppliers.portals import (
    amazon,
    booker,
    brakes,
    cakesmiths,
    cups_direct,
    generic,
    monolith,
    tesco,
)
from cafeops.integrations.suppliers.portals.base import (
    REGISTRY,
    BasketLine,
    BasketSnapshot,
    CartLinkPlan,
    PortalNeedsHuman,
    PortalPolicy,
    PortalPolicyRefusal,
    PortalStepFailed,
    QuickOrderResult,
    SupplierPortal,
    portal_for,
    portal_for_supplier,
    register_portal,
)
from cafeops.integrations.suppliers.portals.generic import (
    GenericPortal,
    configured_portal_for_supplier,
    generic_portal_from_config,
)

ADAPTER_MODULES = (generic, tesco, amazon, booker, brakes, cups_direct, cakesmiths, monolith)

__all__ = [
    "ADAPTER_MODULES",
    "REGISTRY",
    "BasketLine",
    "BasketSnapshot",
    "CartLinkPlan",
    "GenericPortal",
    "PortalNeedsHuman",
    "PortalPolicy",
    "PortalPolicyRefusal",
    "PortalStepFailed",
    "QuickOrderResult",
    "SupplierPortal",
    "configured_portal_for_supplier",
    "generic_portal_from_config",
    "portal_for",
    "portal_for_supplier",
    "register_portal",
]
