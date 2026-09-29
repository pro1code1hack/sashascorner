"""Tesco groceries (www.tesco.com/groceries), where the basket is the "trolley".

What is scripted:

* sign-in check: no "Sign in" link in the header and an account / "My account" /
  "My orders" control present (a Tesco header signed out shows "Sign in" and
  "Register");
* add by product URL (`/groceries/en-GB/products/<id>`): open the product page,
  press its "Add" button, set the quantity in the control that replaces it (a
  number box with "Remove 1"/"Add 1 more" buttons), then verify on the trolley
  page by product id. A product already in the trolley is corrected to the wanted
  quantity, never added on top;
* basket: the trolley page through the generic row reader, subtotal from the
  "Guide price" / "Subtotal" text.

What is delegated: any line without a product URL (search), any product page the
script does not recognise, and any trolley row the reader cannot make sense of.

Policy notes. Booking a delivery slot is a person's job and costs money on its own:
`/slots`, `/book-a-slot`, `/checkout` and the "Book a slot" control are refused.
Tesco's bot protection may serve a "Pardon our interruption" / "Access denied"
page to automation; that reads as `PortalNeedsHuman` (a person opens the profile
in a headed browser and gets past it), not as a selector failure.

Tier 2 (`prefers_headed = True`): the 2026-09-29 smoke test
(docs/agents/BROWSER-ORDERING.md §9) got "Access Denied" from Akamai on the very
first request from a fresh **headless** Chromium context, before any sign-in --
Tesco fingerprints the headless build itself, not the session. A headed window with
the connected profile gets through. The worker therefore launches Tesco headed when
a display is available (Xvfb in the container, the real screen on a laptop) and
otherwise reports NEEDS_HUMAN with that reason instead of pretending. Tesco has no
cart-link or quick-order mechanism, so the per-product scripted add is the only
scripted tier; `cart_link` and `quick_order_url` are deliberately not defined.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cafeops.integrations.suppliers.portals._common import (
    LOCATOR_TIMEOUT_MS,
    HeuristicPortal,
    basket_rows_generic,
    click_increment,
    detect_needs_human,
    dismiss_cookie_banner,
    find_row,
    first_visible,
    goto,
    page_text,
    parse_pence,
    read_quantity,
    read_subtotal_text,
    set_quantity,
    wait_settled,
)
from cafeops.integrations.suppliers.portals.base import (
    DEFAULT_FORBIDDEN_CONTROLS,
    DEFAULT_FORBIDDEN_URLS,
    BasketLine,
    PortalNeedsHuman,
    PortalPolicy,
    PortalStepFailed,
    register_portal,
)

PRODUCT_URL = re.compile(r"tesco\.com/groceries/en-GB/products/(\d+)", re.I)

_BLOCKED_TEXT = re.compile(
    r"pardon our interruption|access denied|reference #\s*[0-9a-f.]+|request unsuccessful",
    re.I,
)

# The quantity box Tesco shows once a product is in the trolley.
_QTY_LABEL = re.compile(r"quantity", re.I)
_ADD_ONE_MORE = re.compile(r"^\s*(add (1|one) more|increase( quantity)?|\+)\b", re.I)
_ADD_NAME = re.compile(r"^\s*add\b(?!.*\b(favourite|favorite|list|to list)\b)", re.I)


@register_portal("tesco")
class TescoPortal(HeuristicPortal):
    slug = "tesco"
    label = "Tesco groceries"
    supports_scripted_add = True
    #: Akamai refuses headless Chromium outright (module docstring).
    prefers_headed = True
    policy = PortalPolicy(
        allowed_hosts=("tesco.com",),
        start_url="https://www.tesco.com/groceries/en-GB/",
        basket_url="https://www.tesco.com/groceries/en-GB/trolley",
        login_url="https://www.tesco.com/account/login/en-GB",
        forbidden_control_patterns=(
            *DEFAULT_FORBIDDEN_CONTROLS,
            r"\bbook (a )?(delivery |collection )?slot\b",
            r"\bchange slot\b",
            r"\bcontinue to checkout\b",
            r"\bcheckout now\b",
            r"\bclubcard pay\b",
            r"\bempty (the )?trolley\b",
        ),
        forbidden_url_patterns=(
            *DEFAULT_FORBIDDEN_URLS,
            r"/slots\b",
            r"/book-a-slot",
            r"/groceries/en-GB/checkout",
        ),
    )
    subtotal_labels = (r"guide price", r"sub[- ]?total", r"trolley total", r"\btotal\b")

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _product_id(url: str | None) -> str | None:
        if not url:
            return None
        m = PRODUCT_URL.search(url)
        return m.group(1) if m else None

    @staticmethod
    def _blocked(page: Page) -> None:
        text = page_text(page, timeout_ms=2_000)
        m = _BLOCKED_TEXT.search(text[:4000])
        if m:
            raise PortalNeedsHuman(
                f"Tesco is blocking automation ({m.group(0)!r}); "
                "open the profile in a headed browser"
            )

    def _open(self, page: Page, url: str) -> None:
        goto(page, self.policy, url)
        dismiss_cookie_banner(page)
        self._blocked(page)
        detect_needs_human(page)

    # -- sign-in -------------------------------------------------------------

    def is_signed_in(self, page: Page) -> bool:
        signed = super().is_signed_in(page)
        if not signed:
            self._blocked(page)
        return signed

    # -- add -----------------------------------------------------------------

    def _quantity_control(self, page: Page, *, timeout_ms: int = LOCATOR_TIMEOUT_MS) -> Any:
        return first_visible(
            page.locator("main").get_by_label(_QTY_LABEL),
            page.locator("main").get_by_role("spinbutton"),
            page.locator("main").locator("input[type='number'], input[name*='quantity' i]"),
            timeout_ms=timeout_ms,
        )

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        product_id = self._product_id(line.product_url)
        if not product_id:
            raise PortalStepFailed("no product URL; search needed")
        assert line.product_url is not None
        self._open(page, line.product_url)

        main = page.locator("main")
        h1 = first_visible(main.get_by_role("heading", level=1))
        if h1 is None:
            raise PortalStepFailed("Tesco product page has no product heading")
        try:
            name = " ".join(h1.inner_text(timeout=LOCATOR_TIMEOUT_MS).split())
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("could not read the product heading") from exc

        unit_price = None
        try:
            price_text = main.get_by_text(re.compile(r"£\s*\d")).first.inner_text(timeout=2_000)
            unit_price = parse_pence(price_text)
        except (PlaywrightTimeout, PlaywrightError):
            pass

        qty = self._quantity_control(page, timeout_ms=1_500)
        if qty is None:
            # Not in the trolley yet: the buy box shows a plain "Add".
            add = first_visible(
                main.get_by_role("button", name=_ADD_NAME), timeout_ms=LOCATOR_TIMEOUT_MS
            )
            if add is None:
                raise PortalStepFailed("no 'Add' button in the product's buy box")
            try:
                add.click(timeout=LOCATOR_TIMEOUT_MS)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                raise PortalStepFailed("the 'Add' button did not respond") from exc
            wait_settled(page, timeout_ms=3_000)
            detect_needs_human(page)
            qty = self._quantity_control(page)
            if qty is None:
                raise PortalStepFailed("pressed 'Add' but no quantity control appeared")

        current = read_quantity(qty)
        if current is not None and current != line.packs_wanted:
            try:
                set_quantity(page, qty, line.packs_wanted, commit="enter")
            except PortalStepFailed:
                more = line.packs_wanted - (read_quantity(qty) or 0)
                if more <= 0:
                    raise
                plus = first_visible(
                    main.get_by_role("button", name=_ADD_ONE_MORE), timeout_ms=1_500
                )
                if plus is None:
                    raise
                click_increment(page, plus, more, max_clicks=6, readback=qty)

        # Verify on the trolley: the readback that counts is the one the person sees.
        rows, _subtotal = self.read_basket(page)
        row = find_row(rows, name=name, url_key=f"/products/{product_id}")
        if row is None:
            raise PortalStepFailed("product is not in the trolley after adding")
        if int(row["qty"]) != line.packs_wanted:
            raise PortalStepFailed(
                f"trolley shows {row['qty']} of this product, wanted {line.packs_wanted}"
            )
        status = "already" if current == line.packs_wanted else "added"
        return replace(
            line,
            packs_in_basket=int(row["qty"]),
            unit_price_seen_pence=row.get("unit_price_pence") or unit_price,
            line_total_seen_pence=row.get("line_total_pence"),
            product_name_seen=row["name"],
            status=status,
            added_by="script",
            note="trolley already held the wanted quantity" if status == "already" else "",
        )

    # -- basket --------------------------------------------------------------

    def read_basket(self, page: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        self._open(page, self.policy.basket_url)
        if urlparse(page.url).path.startswith("/account/"):
            raise PortalNeedsHuman(
                "Tesco sent the trolley request to sign-in; the session has expired"
            )
        rows, empty = basket_rows_generic(page)
        if empty:
            return (), 0
        if not rows:
            raise PortalStepFailed("trolley page shows neither rows nor an empty-trolley message")
        return rows, read_subtotal_text(page, self.subtotal_labels)

    # -- hints ---------------------------------------------------------------

    def agent_hints(self) -> str:
        return (
            "Tesco groceries (www.tesco.com/groceries/en-GB), signed in with the cafe's "
            "account. Tesco calls the basket the 'trolley'.\n"
            "Search: the 'Search' box at the top of every groceries page; type the product "
            "name from the order line and press Enter; open the product whose name and pack "
            "size match (check the size, e.g. 2.272L vs 1.136L, before adding).\n"
            "Add: on a product tile or product page the buy box has one button called "
            "'Add'. Press it once; it becomes a quantity box with 'Remove 1' and "
            "'Add 1 more' buttons.\n"
            "Quantity: type the packs wanted into the quantity box and press Enter, or press "
            "'Add 1 more' one at a time; read the number back. Do not add the same product "
            "twice from different pages.\n"
            "Basket: the trolley at https://www.tesco.com/groceries/en-GB/trolley; its total "
            "is shown as 'Guide price'. Finish on that page.\n"
            "Never touch: 'Book a slot', 'Change slot', 'Checkout', 'Continue to checkout', "
            "Clubcard Pay, 'Empty trolley', saved cards, or the account pages. If Tesco asks "
            "you to sign in, verify your identity, or shows 'Pardon our interruption' / "
            "'Access denied', stop and say needs_human.\n"
            "The job ends on the trolley page with every line and the guide price visible."
        )


__all__ = ["PRODUCT_URL", "TescoPortal"]
