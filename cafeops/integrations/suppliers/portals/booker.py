"""Booker (booker.co.uk), the cash-and-carry wholesaler's online trade ordering.

What is scripted: the sign-in check from the header and a best-effort basket read.
What is delegated: every add. Booker's product listing is a trade catalogue behind
a login whose markup we have not measured, so `supports_scripted_add` is False and
the model adds each line with the hints below. When the basket reader is not sure
it raises `PortalStepFailed` and the model reads the basket instead.

URLs: the site root and login are real; the basket path `/basket` is the usual
one but **unverified** against a signed-in trade account. Fix it here if the first
CHECK_SESSION job shows a different one.
"""

from __future__ import annotations

from cafeops.integrations.suppliers.portals._common import HeuristicPortal
from cafeops.integrations.suppliers.portals.base import (
    DEFAULT_FORBIDDEN_CONTROLS,
    DEFAULT_FORBIDDEN_URLS,
    PortalPolicy,
    register_portal,
)


@register_portal("booker")
class BookerPortal(HeuristicPortal):
    slug = "booker"
    label = "Booker"
    supports_scripted_add = False
    policy = PortalPolicy(
        allowed_hosts=("booker.co.uk",),
        start_url="https://www.booker.co.uk/",
        basket_url="https://www.booker.co.uk/basket",
        login_url="https://www.booker.co.uk/login",
        forbidden_control_patterns=(
            *DEFAULT_FORBIDDEN_CONTROLS,
            r"\bbook (a )?(collection|delivery )?slot\b",
            r"\bconfirm delivery\b",
            r"\bsubmit order\b",
            r"\bconfirm order\b",
            r"\bconfirm collection\b",
        ),
        forbidden_url_patterns=(
            *DEFAULT_FORBIDDEN_URLS,
            r"/delivery-slot",
            r"/collection-slot",
            r"/order-confirm",
        ),
    )

    def agent_hints(self) -> str:
        return (
            "Booker trade site (www.booker.co.uk), signed in with the cafe's trade account.\n"
            "Search: the search box at the top of every page; type the product name or the "
            "Booker product code from the order line, press Enter.\n"
            "Add: each product card has a quantity stepper (a number box with '-' and '+') "
            "and an 'Add' / 'Add to basket' button. Set the number of packs (cases) first, "
            "then press Add. Pack = one case as the product card describes it.\n"
            "Quantity: prefer typing into the number box; otherwise press '+' one at a time "
            "and read the number back.\n"
            "Basket: called 'Basket', at https://www.booker.co.uk/basket. Finish on that page.\n"
            "Never touch: Checkout, Book slot, Confirm delivery, Confirm collection, Submit "
            "order, payment, saved cards, or account settings. If asked to sign in, enter a "
            "code or solve a CAPTCHA, stop and say needs_human.\n"
            "The job ends on the basket page with every line and the subtotal visible."
        )
