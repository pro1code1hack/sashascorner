"""Cups Direct (cupsdirect.co.uk), the packaging web shop. It is a **Shopify** store.

The live smoke test (2026-09-29) settled that: the front page redirects
`www.cupsdirect.co.uk` to the apex host and carries Shopify's cookie consent. A
Shopify store gives the scripted half two things a scraped page cannot:

* `GET /cart.js` -- the basket as JSON with prices already in pence
  (`items[].quantity`, `price`, `line_price`, `url`, `variant id`) and
  `items_subtotal_price`. `read_basket` navigates to `/cart` (the job ends there,
  with a screenshot) and reads the numbers from `/cart.js` through the same
  browser session, so the rows are exact and never parsed from text;
* `GET /account` with redirects disabled -- `302 -> /account/login` means signed
  out, `200` means signed in. No page navigation to the login page, no guessing
  from header links (a bare "Account" link is shown to guests, which is what
  fooled the generic heuristic on the first smoke run).

What is scripted: sign-in check, add by product URL (`/products/<handle>`,
optionally `?variant=<id>`; the product form's `Quantity` box and "Add to cart" /
"Add to basket" button, then `/cart.js` confirms), quantity correction on the cart
page (`input[name="updates[]"]`), basket read. Delegated: lines without a product
URL, product pages with unchosen variants, anything the script cannot confirm.

`ShopifyPortal` is written so another Shopify supplier can subclass it with a
different `policy`; it is kept in this module because Cups Direct is the only one
today.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cafeops.integrations.suppliers.portals._common import (
    LOCATOR_TIMEOUT_MS,
    HeuristicPortal,
    detect_needs_human,
    dismiss_cookie_banner,
    find_add_button,
    find_quantity_control,
    first_visible,
    goto,
    parse_int,
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

PRODUCT_HANDLE = re.compile(r"/products/([a-z0-9][a-z0-9-]*)", re.I)
_VARIANT_PICKERS = (
    "variant-selects, variant-radios, select[name^='options'], fieldset.product-form__input"
)


class ShopifyPortal(HeuristicPortal):
    """A Shopify storefront. Subclasses set `slug`, `label`, `policy`."""

    supports_scripted_add = True
    subtotal_labels = (r"sub[- ]?total", r"estimated total", r"\btotal\b")

    # -- plumbing ------------------------------------------------------------

    def _origin(self) -> str:
        u = urlparse(self.policy.basket_url)
        return f"{u.scheme}://{u.netloc}"

    def _get_json(self, page: Page, path: str) -> Any:
        # Shopify serves /cart.js as text/javascript, so the body decides, not the
        # content type.
        try:
            resp = page.request.get(self._origin() + path, timeout=LOCATOR_TIMEOUT_MS * 2)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed(f"{path} did not answer") from exc
        if resp.status != 200:
            raise PortalStepFailed(f"{path} returned {resp.status}; not a Shopify store?")
        try:
            data = resp.json()
        except (PlaywrightError, ValueError) as exc:
            raise PortalStepFailed(f"{path} was not JSON; not a Shopify store?") from exc
        if not isinstance(data, dict) or "items" not in data:
            raise PortalStepFailed(f"{path} has no items list; not a Shopify cart")
        return data

    # -- sign-in -------------------------------------------------------------

    def is_signed_in(self, page: Page) -> bool:
        goto(page, self.policy, self.policy.start_url, check_human=False)
        dismiss_cookie_banner(page)
        detect_needs_human(page, password_field=False)
        try:
            resp = page.request.get(
                self._origin() + "/account", max_redirects=0, timeout=LOCATOR_TIMEOUT_MS * 2
            )
        except (PlaywrightTimeout, PlaywrightError):
            return super().is_signed_in(page)
        if resp.status == 200:
            return True
        if resp.status in (301, 302, 303, 307, 308):
            return "/account/login" not in (resp.headers.get("location") or "")
        return super().is_signed_in(page)

    # -- basket --------------------------------------------------------------

    @staticmethod
    def _rows_from_cart(cart: dict[str, Any], origin: str) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        for item in cart.get("items", []):
            name = " ".join(str(item.get("product_title") or item.get("title") or "").split())
            variant = str(item.get("variant_title") or "").strip()
            if variant and variant.lower() != "default title" and variant not in name:
                name = f"{name} ({variant})"
            qty = parse_int(str(item.get("quantity", "")))
            if not name or qty is None:
                raise PortalStepFailed("cart.js item without a title or quantity")
            url = item.get("url")
            price = item.get("price")
            line_price = item.get("line_price")
            rows.append(
                {
                    "name": name,
                    "qty": qty,
                    "unit_price_pence": int(price) if price is not None else None,
                    "line_total_pence": int(line_price) if line_price is not None else None,
                    "product_url": origin + str(url) if url and str(url).startswith("/") else url,
                    "handle": item.get("handle"),
                    "variant_id": item.get("id"),
                }
            )
        return tuple(rows)

    def read_basket(self, page: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        goto(page, self.policy, self.policy.basket_url)
        dismiss_cookie_banner(page)
        cart = self._get_json(page, "/cart.js")
        rows = self._rows_from_cart(cart, self._origin())
        subtotal = cart.get("items_subtotal_price")
        return rows, (int(subtotal) if subtotal is not None else None)

    # -- add -----------------------------------------------------------------

    @staticmethod
    def _handle_and_variant(url: str) -> tuple[str, str | None]:
        m = PRODUCT_HANDLE.search(url)
        if not m:
            raise PortalStepFailed("not a /products/<handle> URL; search needed")
        variant = parse_qs(urlparse(url).query).get("variant", [None])[0]
        return m.group(1).lower(), variant

    @staticmethod
    def _match(
        rows: tuple[dict[str, Any], ...], handle: str, variant: str | None
    ) -> dict[str, Any] | None:
        for row in rows:
            if str(row.get("handle") or "").lower() != handle:
                continue
            if variant and str(row.get("variant_id")) != str(variant):
                continue
            return row
        return None

    def _correct_on_cart(self, page: Page, row: dict[str, Any], packs: int) -> None:
        needle = re.compile(re.escape(str(row["name"])[:40].split(" (")[0]), re.I)
        containers = page.locator("tr, li, div[class*='cart-item' i], div[class*='line-item' i]")
        containers = containers.filter(has_text=needle)
        try:
            n = containers.count()
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("could not locate the cart row") from exc
        for i in range(n - 1, -1, -1):
            try:
                control = find_quantity_control(containers.nth(i), timeout_ms=800)
            except PortalStepFailed:
                continue
            set_quantity(page, control, packs, commit="enter")
            wait_settled(page)
            return
        raise PortalStepFailed("cart row has no quantity control")

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        if not line.product_url:
            raise PortalStepFailed("no product URL; search needed")
        host = urlparse(line.product_url).hostname or ""
        if not self.policy.host_allowed(host):
            raise PortalStepFailed(f"product URL host {host!r} is not this shop")
        handle, variant = self._handle_and_variant(line.product_url)

        rows, _ = self.read_basket(page)
        row = self._match(rows, handle, variant)
        if row is not None:
            already = int(row["qty"]) == line.packs_wanted
            if not already:
                self._correct_on_cart(page, row, line.packs_wanted)
                rows, _ = self.read_basket(page)
                row = self._match(rows, handle, variant)
                if row is None or int(row["qty"]) != line.packs_wanted:
                    raise PortalStepFailed("changed the cart quantity but cart.js disagrees")
            return replace(
                line,
                packs_in_basket=int(row["qty"]),
                unit_price_seen_pence=row.get("unit_price_pence"),
                line_total_seen_pence=row.get("line_total_pence"),
                product_name_seen=row["name"],
                status="already" if already else "added",
                added_by="script",
                note="cart already held the wanted quantity"
                if already
                else "quantity corrected in the cart",
            )

        goto(page, self.policy, line.product_url)
        dismiss_cookie_banner(page)
        form = page.locator("form[action*='/cart/add']").first
        try:
            has_form = form.count() > 0
            has_pickers = variant is None and page.locator(_VARIANT_PICKERS).count() > 0
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("could not inspect the product page") from exc
        if not has_form:
            raise PortalStepFailed("no add-to-cart form on the page; not a product page?")
        if has_pickers:
            raise PortalStepFailed(
                "product has variants and the URL names none; a person or the model must choose"
            )
        qty = first_visible(
            form.get_by_label(re.compile(r"quantity", re.I)),
            page.get_by_label(re.compile(r"^\s*quantity\s*$", re.I)),
            form.locator("input[name='quantity']"),
            page.locator("input[name='quantity']"),
            timeout_ms=LOCATOR_TIMEOUT_MS,
        )
        if qty is None:
            if line.packs_wanted != 1:
                raise PortalStepFailed(
                    "no quantity box on the product page; cannot add more than one"
                )
        else:
            set_quantity(page, qty, line.packs_wanted, commit="blur")
        add = find_add_button(form)
        try:
            add.click(timeout=LOCATOR_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("the add button did not respond") from exc
        wait_settled(page)
        detect_needs_human(page)

        rows, _ = self.read_basket(page)
        row = self._match(rows, handle, variant)
        if row is None:
            raise PortalStepFailed("pressed add but cart.js has no line for this product")
        if int(row["qty"]) != line.packs_wanted:
            raise PortalStepFailed(
                f"cart holds {row['qty']} of this product, wanted {line.packs_wanted}"
            )
        return replace(
            line,
            packs_in_basket=int(row["qty"]),
            unit_price_seen_pence=row.get("unit_price_pence"),
            line_total_seen_pence=row.get("line_total_pence"),
            product_name_seen=row["name"],
            status="added",
            added_by="script",
        )


class CupsDirectPortal(ShopifyPortal):
    slug = "cups_direct"
    label = "Cups Direct"
    policy = PortalPolicy(
        allowed_hosts=("cupsdirect.co.uk",),
        start_url="https://cupsdirect.co.uk/",
        basket_url="https://cupsdirect.co.uk/cart",
        login_url="https://cupsdirect.co.uk/account/login",
        forbidden_control_patterns=(
            *DEFAULT_FORBIDDEN_CONTROLS,
            r"\bgo to checkout\b",
            r"\bpaypal\b",
            r"\bshop pay\b",
            r"\bg pay\b|\bgoogle pay\b|\bapple pay\b",
            r"\bpay with\b",
        ),
        forbidden_url_patterns=(*DEFAULT_FORBIDDEN_URLS, r"/checkouts?\b", r"/wallets/"),
    )

    def agent_hints(self) -> str:
        return (
            "Cups Direct web shop (cupsdirect.co.uk), a Shopify store signed in with the "
            "cafe's account.\n"
            "Search: the search icon / 'Search' box in the header; type the product name "
            "from the order line, press Enter; open the matching product and pick the "
            "variant (size, colour, case size) the order line names.\n"
            "Add: on the product page set the 'Quantity' box to the packs wanted (one pack "
            "= one case as the product page describes it) and press 'Add to cart' / "
            "'Add to basket'. A cart drawer or notification confirms it.\n"
            "Quantity: type into the Quantity box. On the cart page each row has a quantity "
            "box with -/+; change it and the cart updates itself.\n"
            "Basket: called 'Cart' or 'Basket', at https://cupsdirect.co.uk/cart. Finish "
            "on that page.\n"
            "Never touch: 'Check out' / 'Checkout', Shop Pay, PayPal, Google Pay, Apple "
            "Pay, any /checkouts/ page, saved cards, or account settings. If asked to "
            "sign in, enter a code or solve a CAPTCHA, stop and say needs_human.\n"
            "The job ends on the cart page with every line and the subtotal visible."
        )


register_portal("cups direct", "cupsdirect", "cups_direct")(CupsDirectPortal)

__all__ = ["PRODUCT_HANDLE", "CupsDirectPortal", "ShopifyPortal"]
