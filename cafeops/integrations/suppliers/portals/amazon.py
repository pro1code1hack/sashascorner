"""Amazon UK (www.amazon.co.uk).

What is scripted:

* sign-in check from the header account link (`#nav-link-accountList`, an id
  Amazon has kept for over a decade): "Hello, sign in" is signed out, "Hello, <name>"
  is signed in;
* add by product URL (`/dp/<ASIN>` or `/gp/product/<ASIN>`): if the cart already
  holds the ASIN, correct its quantity on the cart page; otherwise open the product
  page, pick `#quantity` when the page has one, press "Add to Basket"
  (`#add-to-cart-button`), then verify on the cart page by ASIN;
* basket: `.sc-list-item[data-asin]` rows with their `data-quantity` / `data-price`
  attributes and the product link; subtotal from `#sc-subtotal-amount-activecart`.

What is delegated: lines without a product URL (search), product pages whose buy
box the script does not recognise (variations, "See all buying options", sold-out),
and cart pages that do not show `.sc-list-item` rows.

Amazon shows a CAPTCHA ("Enter the characters you see below") to automation it does
not like; that raises `PortalNeedsHuman` and a person clears it in the headed
profile. Buying paths are refused by policy: `/gp/buy/`, `/checkout`, `/buy-now`,
`/gp/aw/ohp`, and the controls "Buy now", "Proceed to checkout", "Subscribe",
"Set up now" and "Place your order".
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cafeops.integrations.suppliers.portals._common import (
    EMPTY_BASKET_TEXT,
    LOCATOR_TIMEOUT_MS,
    HeuristicPortal,
    basket_rows_generic,
    click_increment,
    detect_needs_human,
    dismiss_cookie_banner,
    ensure_on_portal,
    first_visible,
    goto,
    mask_label,
    page_text,
    parse_int,
    parse_pence,
    read_quantity,
    set_quantity,
    wait_settled,
)
from cafeops.integrations.suppliers.portals.base import (
    DEFAULT_FORBIDDEN_CONTROLS,
    DEFAULT_FORBIDDEN_URLS,
    BasketLine,
    PortalPolicy,
    PortalStepFailed,
    register_portal,
)

ASIN_IN_URL = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?=[/?#]|$)", re.I)
_HELLO_SIGN_IN = re.compile(r"hello,?\s*sign in|sign in", re.I)
_INCREASE = re.compile(r"increase quantity", re.I)


@register_portal("amazon")
class AmazonPortal(HeuristicPortal):
    slug = "amazon"
    label = "Amazon UK"
    supports_scripted_add = True
    policy = PortalPolicy(
        allowed_hosts=("amazon.co.uk",),
        start_url="https://www.amazon.co.uk/",
        basket_url="https://www.amazon.co.uk/gp/cart/view.html",
        login_url="https://www.amazon.co.uk/ap/signin",
        forbidden_control_patterns=(
            *DEFAULT_FORBIDDEN_CONTROLS,
            r"\bproceed to checkout\b",
            r"\bsubscribe\b",
            r"\bset up now\b",
            r"\bplace your order\b",
            r"\b1-click\b",
            r"\bbuy again\b",
            r"\badd (a )?gift card\b",
        ),
        forbidden_url_patterns=(
            *DEFAULT_FORBIDDEN_URLS,
            r"/gp/buy/",
            r"/buy-now",
            r"/gp/aw/ohp",
            r"/checkout",
            r"/gp/huc/",
            r"/subscribe-and-save",
        ),
    )
    subtotal_labels = (r"sub[- ]?total", r"\btotal\b")

    # -- sign-in -------------------------------------------------------------

    def _account_text(self, page: Page) -> str | None:
        loc = first_visible(
            page.locator("#nav-link-accountList-nav-line-1"),
            page.locator("#nav-link-accountList"),
            timeout_ms=LOCATOR_TIMEOUT_MS,
        )
        if loc is None:
            return None
        try:
            return " ".join(loc.inner_text(timeout=LOCATOR_TIMEOUT_MS).split())
        except (PlaywrightTimeout, PlaywrightError):
            return None

    def is_signed_in(self, page: Page) -> bool:
        ensure_on_portal(page, self.policy)
        dismiss_cookie_banner(page)
        detect_needs_human(page, password_field=False)
        text = self._account_text(page)
        if text is None:
            return super().is_signed_in(page)
        if _HELLO_SIGN_IN.search(text):
            return False
        return bool(re.match(r"hello,?\s+\S", text, re.I))

    def account_label(self, page: Page) -> str | None:
        ensure_on_portal(page, self.policy)
        text = self._account_text(page)
        if not text or _HELLO_SIGN_IN.search(text):
            return None
        m = re.match(r"hello,?\s+(.+?)(\s+account & lists.*)?$", text, re.I)
        return mask_label(m.group(1) if m else text)

    # -- cart ----------------------------------------------------------------

    @staticmethod
    def _asin(url: str | None) -> str | None:
        if not url:
            return None
        m = ASIN_IN_URL.search(url)
        return m.group(1).upper() if m else None

    def _cart_items(self, page: Page) -> Locator:
        return page.locator(
            "#activeCartViewForm .sc-list-item[data-asin], .sc-list-item[data-asin]"
        )

    def _rows_from_items(self, page: Page) -> tuple[dict[str, Any], ...]:
        items = self._cart_items(page)
        rows: list[dict[str, Any]] = []
        try:
            n = items.count()
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("could not count cart rows") from exc
        for i in range(n):
            item = items.nth(i)
            try:
                asin = item.get_attribute("data-asin", timeout=LOCATOR_TIMEOUT_MS) or ""
                qty = parse_int(item.get_attribute("data-quantity", timeout=LOCATOR_TIMEOUT_MS))
                unit = parse_pence(item.get_attribute("data-price", timeout=LOCATOR_TIMEOUT_MS))
                title = first_visible(
                    item.locator(".sc-product-title, .a-truncate-full, .sc-product-link"),
                    item.get_by_role("link").filter(has_text=re.compile(r"\S{3,}")),
                    timeout_ms=1_000,
                )
                name = (
                    " ".join(title.inner_text(timeout=LOCATOR_TIMEOUT_MS).split()) if title else ""
                )
                link = item.locator("a[href*='/dp/'], a[href*='/gp/product/']").first
                href = link.get_attribute("href", timeout=1_000) if link.count() else None
            except (PlaywrightTimeout, PlaywrightError) as exc:
                raise PortalStepFailed(f"cart row {i} did not read cleanly") from exc
            if qty is None:
                control = self._row_quantity_control(item)
                qty = read_quantity(control) if control is not None else None
            if not name or qty is None:
                raise PortalStepFailed(f"cart row {i} ({asin}) has no name or quantity")
            if href and href.startswith("/"):
                href = "https://www.amazon.co.uk" + href
            rows.append(
                {
                    "name": name,
                    "qty": qty,
                    "unit_price_pence": unit,
                    "line_total_pence": None,
                    "product_url": href or f"https://www.amazon.co.uk/dp/{asin}",
                    "asin": asin.upper(),
                }
            )
        return tuple(rows)

    def _row_quantity_control(self, item: Locator) -> Locator | None:
        return first_visible(
            item.locator("select[name='quantity']"),
            item.get_by_role("combobox", name=re.compile(r"quantity", re.I)),
            item.locator("input[name='quantityBox'], input[type='number']"),
            item.get_by_role("spinbutton"),
            timeout_ms=1_500,
        )

    def _read_subtotal(self, page: Page) -> int | None:
        loc = first_visible(page.locator("#sc-subtotal-amount-activecart"), timeout_ms=2_000)
        if loc is None:
            return None
        try:
            return parse_pence(loc.inner_text(timeout=LOCATOR_TIMEOUT_MS))
        except (PlaywrightTimeout, PlaywrightError):
            return None

    def read_basket(self, page: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        goto(page, self.policy, self.policy.basket_url)
        dismiss_cookie_banner(page)
        if EMPTY_BASKET_TEXT.search(page_text(page)):
            return (), 0
        rows = self._rows_from_items(page)
        if not rows:
            generic_rows, empty = basket_rows_generic(page)
            if empty:
                return (), 0
            if not generic_rows:
                raise PortalStepFailed("cart page shows no .sc-list-item rows and no empty message")
            rows = generic_rows
        return rows, self._read_subtotal(page)

    # -- add -----------------------------------------------------------------

    def _set_cart_quantity(self, page: Page, asin: str, packs: int) -> None:
        item = page.locator(f".sc-list-item[data-asin='{asin}']").first
        control = self._row_quantity_control(item)
        if control is None:
            raise PortalStepFailed("cart row has no quantity control")
        try:
            set_quantity(page, control, packs, commit="enter")
        except PortalStepFailed:
            current = read_quantity(control) or 0
            more = packs - current
            plus = first_visible(item.get_by_role("button", name=_INCREASE), timeout_ms=1_500)
            if more <= 0 or plus is None:
                raise
            click_increment(page, plus, more, max_clicks=6, readback=control)

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        asin = self._asin(line.product_url)
        if not asin:
            raise PortalStepFailed("no product URL; search needed")
        assert line.product_url is not None
        host = urlparse(line.product_url).hostname or ""
        if not self.policy.host_allowed(host):
            raise PortalStepFailed(f"product URL host {host!r} is not amazon.co.uk")

        # Already in the cart? Correct it there rather than adding on top.
        rows, _ = self.read_basket(page)
        row = next((r for r in rows if r.get("asin") == asin), None)
        if row is not None:
            already = int(row["qty"]) == line.packs_wanted
            if not already:
                self._set_cart_quantity(page, asin, line.packs_wanted)
                rows, _ = self.read_basket(page)
                row = next((r for r in rows if r.get("asin") == asin), None)
                if row is None or int(row["qty"]) != line.packs_wanted:
                    raise PortalStepFailed("changed the cart quantity but the readback disagrees")
            return replace(
                line,
                packs_in_basket=int(row["qty"]),
                unit_price_seen_pence=row.get("unit_price_pence"),
                product_name_seen=row["name"],
                status="already" if already else "added",
                added_by="script",
                note="cart already held the wanted quantity"
                if already
                else "quantity corrected in the cart",
            )

        goto(page, self.policy, line.product_url)
        dismiss_cookie_banner(page)
        title = first_visible(page.locator("#productTitle"), page.get_by_role("heading", level=1))
        if title is None:
            raise PortalStepFailed("Amazon product page has no title; not a product page?")
        add = first_visible(
            page.locator("#add-to-cart-button"),
            page.get_by_role("button", name=re.compile(r"^add to (basket|cart)$", re.I)),
            timeout_ms=LOCATOR_TIMEOUT_MS,
        )
        if add is None:
            raise PortalStepFailed(
                "no 'Add to Basket' button (variations, sold out, or 'See all buying options')"
            )
        qty_select = first_visible(page.locator("#quantity"), timeout_ms=1_500)
        if qty_select is not None:
            set_quantity(page, qty_select, line.packs_wanted, commit="blur")
        elif line.packs_wanted != 1:
            raise PortalStepFailed(
                "no quantity selector on the product page; cannot add more than one"
            )
        try:
            add.click(timeout=LOCATOR_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("'Add to Basket' did not respond") from exc
        wait_settled(page)
        detect_needs_human(page)
        # Amazon sometimes interposes a protection-plan / "add accessories" page.
        # Nothing on it is needed; the cart readback below is the confirmation.
        if self.policy.url_forbidden(page.url):
            raise PortalStepFailed(f"landed on {page.url} after adding; refused to continue")

        rows, _ = self.read_basket(page)
        row = next((r for r in rows if r.get("asin") == asin), None)
        if row is None:
            raise PortalStepFailed("pressed 'Add to Basket' but the cart has no row for this ASIN")
        if int(row["qty"]) != line.packs_wanted:
            self._set_cart_quantity(page, asin, line.packs_wanted)
            rows, _ = self.read_basket(page)
            row = next((r for r in rows if r.get("asin") == asin), None)
            if row is None or int(row["qty"]) != line.packs_wanted:
                raise PortalStepFailed("cart quantity does not match after adding")
        return replace(
            line,
            packs_in_basket=int(row["qty"]),
            unit_price_seen_pence=row.get("unit_price_pence"),
            product_name_seen=row["name"],
            status="added",
            added_by="script",
        )

    # -- hints ---------------------------------------------------------------

    def agent_hints(self) -> str:
        return (
            "Amazon UK (www.amazon.co.uk), signed in with the cafe's account.\n"
            "Search: the search box at the top of every page; type the product name from "
            "the order line and press Enter; prefer the exact product (check pack size and "
            "seller) and open it.\n"
            "Add: on the product page choose the quantity in the 'Quantity' dropdown next "
            "to the price, then press 'Add to Basket'. Ignore any 'Add protection plan' / "
            "'Frequently bought together' offers that appear after adding.\n"
            "Quantity: in the basket each row has a quantity dropdown or a -/+ stepper; "
            "set it and read the number back. Never add the same product twice.\n"
            "Basket: called 'Basket', at https://www.amazon.co.uk/gp/cart/view.html; the "
            "'Subtotal' is on that page. Finish there.\n"
            "Never touch: 'Buy now', 'Proceed to checkout', 'Subscribe & Save' / 'Set up "
            "now', 'Place your order', 1-Click, saved cards, addresses, or account "
            "settings. If Amazon asks you to sign in, enter a code, or shows a CAPTCHA "
            "('Enter the characters you see'), stop and say needs_human.\n"
            "The job ends on the basket page with every line and the subtotal visible."
        )


__all__ = ["ASIN_IN_URL", "AmazonPortal"]
