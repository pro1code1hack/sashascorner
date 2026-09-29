"""Brakes (brake.co.uk), the foodservice wholesaler's trade portal.

Same shape as Booker: the sign-in check and a best-effort basket read are
scripted; every add is delegated to the model with the hints below, because the
signed-in catalogue markup has not been measured. Doubt raises `PortalStepFailed`.

Tier 1 (`quick_order`): the mybrakes help centre
(https://help.brake.co.uk/s/topic/0TO4L000000hNUtWAM/about-ordering) documents a
Quick Order Pad (product code + quantity per row, "Add to basket") and a CSV
upload. The pad is what `quick_order` drives, through the shared
`quick_order_generic`; the CSV upload is not used (file upload stays disabled for
the model too). `quick_order_url` is our best reading of the pad's path and is
**unverified**; set `channel_config["quick_order_url"]` on the supplier row when
the first job shows the real one (the registry binds it, `HeuristicPortal.bind`).

URLs: the site root and `/login` are real; `/basket` is the usual path but
**unverified** against a signed-in trade account. Fix it here if the first
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


@register_portal("brakes", "brake")
class BrakesPortal(HeuristicPortal):
    slug = "brakes"
    label = "Brakes"
    supports_scripted_add = False
    prefers_headed = False
    #: The Quick Order Pad from the mybrakes help centre. Unverified path.
    quick_order_url = "https://www.brake.co.uk/quick-order-pad"
    policy = PortalPolicy(
        allowed_hosts=("brake.co.uk", "brakes.co.uk"),
        start_url="https://www.brake.co.uk/",
        basket_url="https://www.brake.co.uk/basket",
        login_url="https://www.brake.co.uk/login",
        forbidden_control_patterns=(
            *DEFAULT_FORBIDDEN_CONTROLS,
            r"\bbook (a )?(delivery )?slot\b",
            r"\bconfirm delivery\b",
            r"\bsubmit order\b",
            r"\bconfirm order\b",
            r"\bsend order\b",
        ),
        forbidden_url_patterns=(*DEFAULT_FORBIDDEN_URLS, r"/delivery-slot", r"/order-confirm"),
    )

    # -- tier 1: the product-code pad ----------------------------------------

    def quick_order(self, page: Page, lines: Sequence[BasketLine]) -> QuickOrderResult:
        return quick_order_generic(
            page,
            self.policy,
            self.quick_order_url,
            lines,
            submit_names=(
                r"^\s*add (all )?(items |products |lines )?to basket\s*$",
                *QUICK_ORDER_SUBMIT_NAMES,
            ),
            read_basket=self.read_basket,
        )

    def quick_order_hints(self) -> str:
        return quick_order_hints_text(
            label="Brakes foodservice portal (www.brake.co.uk), signed in with the cafe's "
            "trade account",
            pad_url=self.quick_order_url,
            basket_url=self.policy.basket_url,
            columns="one row per line with a 'Product code' box and a 'Quantity' (cases) "
            "box; some rows offer case/each -- keep the unit the order line names",
            submit="'Add to basket' below the pad (one press for every row). Do not use "
            "the CSV upload",
        )

    def agent_hints(self) -> str:
        return (
            "Brakes foodservice portal (www.brake.co.uk), signed in with the cafe's trade "
            "account.\n"
            "Search: the search box at the top of the page; type the product name or the "
            "Brakes product code from the order line, press Enter.\n"
            "Add: product cards and product pages show a quantity box (cases) with '-'/'+' "
            "and an 'Add' / 'Add to basket' button. Set the number of packs first, then Add. "
            "Some lines offer 'case' or 'each': choose the unit the order line names.\n"
            "Quantity: type into the number box where there is one; otherwise press '+' one "
            "at a time and read the number back.\n"
            "Basket: called 'Basket', at https://www.brake.co.uk/basket. Finish on that page.\n"
            "Never touch: Checkout, Book slot, Confirm delivery, Submit/Confirm/Send order, "
            "payment, saved cards, standing orders, or account settings. If asked to sign in, "
            "enter a code or solve a CAPTCHA, stop and say needs_human.\n"
            "The job ends on the basket page with every line and the subtotal visible."
        )
