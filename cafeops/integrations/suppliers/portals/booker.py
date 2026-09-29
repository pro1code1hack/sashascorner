"""Booker (booker.co.uk), the cash-and-carry wholesaler's online trade ordering.

What is scripted: the sign-in check from the header and a best-effort basket read.
What is delegated: every add. Booker's product listing is a trade catalogue behind
a login whose markup we have not measured, so `supports_scripted_add` is False and
the model adds each line with the hints below. When the basket reader is not sure
it raises `PortalStepFailed` and the model reads the basket instead.

Tier 1 (`quick_order`): Booker's website guide
(https://www.booker.co.uk/content/pages/service/website-guide) describes ordering
by product code -- "add Midas product codes on each line and click Find Products"
-- which is one form for the whole order instead of one product page per line.
`quick_order_url` is our best reading of where that page lives and is
**unverified**; set `channel_config["quick_order_url"]` on the supplier row when
the first job shows the real path (the registry binds it, `HeuristicPortal.bind`).
The pad is driven by the shared `quick_order_generic`: codes and quantities typed
into the rows, "Find products" pressed, then -- when the found products come back
with their own "Add to basket" -- that pressed once, and the basket read to confirm.

URLs: the site root and login are real; the basket path `/basket` is the usual
one but **unverified** against a signed-in trade account. Fix it here if the first
CHECK_SESSION job shows a different one.
"""

from __future__ import annotations

from collections.abc import Sequence

from playwright.sync_api import Page

from cafeops.integrations.suppliers.portals._common import (
    QUICK_ORDER_SUBMIT_NAMES,
    HeuristicPortal,
    quick_order_generic,
    quick_order_hints_text,
)
from cafeops.integrations.suppliers.portals.base import (
    DEFAULT_FORBIDDEN_CONTROLS,
    DEFAULT_FORBIDDEN_URLS,
    BasketLine,
    PortalPolicy,
    QuickOrderResult,
    register_portal,
)


@register_portal("booker")
class BookerPortal(HeuristicPortal):
    slug = "booker"
    label = "Booker"
    supports_scripted_add = False
    prefers_headed = False
    #: The product-code entry page ("Find Products" in the website guide). Unverified.
    quick_order_url = "https://www.booker.co.uk/quick-order"
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

    # -- tier 1: the product-code pad ----------------------------------------

    def quick_order(self, page: Page, lines: Sequence[BasketLine]) -> QuickOrderResult:
        return quick_order_generic(
            page,
            self.policy,
            self.quick_order_url,
            lines,
            submit_names=(r"^\s*find products?\s*$", *QUICK_ORDER_SUBMIT_NAMES),
            second_stage_names=(
                r"^\s*add (all )?((found |selected |these )?(products|items|lines) )?to basket\s*$",
                r"^\s*add all( to basket)?\s*$",
            ),
            read_basket=self.read_basket,
        )

    def quick_order_hints(self) -> str:
        return quick_order_hints_text(
            label="Booker trade site (www.booker.co.uk), signed in with the cafe's account",
            pad_url=self.quick_order_url,
            basket_url=self.policy.basket_url,
            columns="one row per line with a 'Product code' (Midas code) box and a "
            "'Quantity' (cases) box",
            submit="'Find Products'; the found products are listed with their own "
            "'Add to basket' -- press that once for the whole list, never per row twice",
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
