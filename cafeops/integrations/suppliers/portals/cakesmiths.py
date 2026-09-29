"""Cakesmiths (cakesmiths.com), wholesale cakes with a trade ordering account.

Scripted: the sign-in check and a best-effort basket read. Delegated: every add,
because the trade catalogue markup behind the wholesale login has not been
measured. Doubt raises `PortalStepFailed` and the model takes that step.

URLs: the root is real; `/basket` and `/account/login` are the usual paths for
this kind of shop but **unverified**. The seeded supplier row still carries the
placeholder `order_url` `https://cakesmiths.example/order`; that column is
informational and this adapter's policy is what the worker uses. Fix the paths
here if the first CHECK_SESSION job shows different ones.
"""

from __future__ import annotations

from cafeops.integrations.suppliers.portals._common import HeuristicPortal
from cafeops.integrations.suppliers.portals.base import (
    DEFAULT_FORBIDDEN_CONTROLS,
    DEFAULT_FORBIDDEN_URLS,
    PortalPolicy,
    register_portal,
)


@register_portal("cakesmiths", "cakesmith")
class CakesmithsPortal(HeuristicPortal):
    slug = "cakesmiths"
    label = "Cakesmiths"
    supports_scripted_add = False
    policy = PortalPolicy(
        allowed_hosts=("cakesmiths.com", "cakesmiths.co.uk"),
        start_url="https://www.cakesmiths.com/",
        basket_url="https://www.cakesmiths.com/basket",
        login_url="https://www.cakesmiths.com/account/login",
        forbidden_control_patterns=(
            *DEFAULT_FORBIDDEN_CONTROLS,
            r"\bsubmit order\b",
            r"\bconfirm order\b",
            r"\bsend order\b",
            r"\bchoose (a )?delivery (date|day)\b",
        ),
        forbidden_url_patterns=(*DEFAULT_FORBIDDEN_URLS, r"/order-confirm"),
    )

    def agent_hints(self) -> str:
        return (
            "Cakesmiths wholesale shop (www.cakesmiths.com), signed in with the cafe's "
            "trade account.\n"
            "Search: the search box in the header, or browse the 'Cakes' / 'Traybakes' "
            "menus; type the product name from the order line, press Enter.\n"
            "Add: on a product page choose the size/variant the order line names, set the "
            "'Quantity' box to the packs wanted, then press 'Add to basket'.\n"
            "Quantity: type into the number box; if only '+'/'-' exist, click '+' one at a "
            "time and read the number back.\n"
            "Basket: called 'Basket', at https://www.cakesmiths.com/basket. Finish there.\n"
            "Never touch: Checkout, delivery date pickers, Submit/Confirm/Send order, "
            "payment, saved cards, or account settings. If asked to sign in, enter a code or "
            "solve a CAPTCHA, stop and say needs_human.\n"
            "The job ends on the basket page with every line and the subtotal visible."
        )
