"""The generic portal: any web shop described by `supplier.channel_config`.

Design choice (the base contract is unchanged): `PortalPolicy` lives on the adapter
instance and the registry holds one instance per slug, so a single registered
"generic" entry cannot carry eight suppliers' URLs. This module therefore does two
things:

1. Registers a **default** `GenericPortal` under slug `generic` whose allowlist is
   the reserved host `generic.invalid` and whose every step raises
   `PortalStepFailed("generic portal not configured ...")`. It exists so that
   `portal_for_supplier` resolves a supplier with `channel_config.portal == "generic"`
   and the integrations listing can show it; it can never reach a real site.
2. Exposes `generic_portal_from_config(config)` which returns a **new, bound**
   `GenericPortal` for one supplier. The worker calls it when
   `portal_for_supplier(...)` returns the unconfigured default:

       portal = portal_for_supplier(supplier)
       if isinstance(portal, GenericPortal) and not portal.configured:
           portal = generic_portal_from_config(supplier.channel_config)

   (`configured_portal_for_supplier` below does exactly that, for callers that want
   one call.)

`channel_config` keys:

    portal        "generic"
    hosts         ["shop.example.co.uk"]          allowed hosts (suffix match)
    start_url     "https://shop.example.co.uk/"
    basket_url    "https://shop.example.co.uk/basket"
    login_url     "https://shop.example.co.uk/login"
    label         "Example Shop"                  optional
    slug          "example"                       optional (default "generic")
    forbidden_urls      [..regex..]               optional, ADDED to the defaults
    forbidden_controls  [..regex..]               optional, ADDED to the defaults
    subtotal_labels     [..regex..]               optional, tried before the defaults
    hints         "free text for the model"       optional, appended to agent_hints()
    prefers_headed      true|false                optional (default false), Tier 2

Tier 0 without code (`cart_link`), for a shop whose basket can be built from a URL:

    cart_link_template  "https://shop.example/cart/{items}"   `{items}` is expanded
    cart_item_key       "sku" | "variant_id_from_url"          what identifies a line:
                                                               its SKU, or the
                                                               `?variant=<id>` in its
                                                               product URL
    cart_item_format    "{id}:{qty}"                           optional, per item
    cart_item_sep       ","                                    optional, between items

Tier 1 without code (`quick_order`), for a shop with a product-code order pad:

    quick_order_url          "https://shop.example/quick-order"   enables the pad
    quick_order_submit_names [..regex..]      optional, tried before the defaults
    quick_order_hints        "free text"      optional, replaces the generated hints

Both are optional; without them the generic adapter has no Tier 0 / Tier 1: it is not
a `base.QuickOrderCapable` (no `quick_order_url` attribute) and `cart_link` returns None.

What the generic adapter scripts: the sign-in check from the header, an add by
product URL (quantity input + "Add to basket" button, verified by reading the
basket), and the basket via the shared best-effort reader. Every doubt raises
`PortalStepFailed` and the model takes that step.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cafeops.integrations.suppliers.portals._common import (
    LOCATOR_TIMEOUT_MS,
    QUICK_ORDER_SUBMIT_NAMES,
    SUBTOTAL_LABELS,
    HeuristicPortal,
    dismiss_cookie_banner,
    find_add_button,
    find_quantity_control,
    find_row,
    first_visible,
    goto,
    quick_order_generic,
    quick_order_hints_text,
    set_quantity,
    wait_settled,
)
from cafeops.integrations.suppliers.portals.base import (
    DEFAULT_FORBIDDEN_CONTROLS,
    DEFAULT_FORBIDDEN_URLS,
    BasketLine,
    CartLinkPlan,
    PortalPolicy,
    PortalStepFailed,
    QuickOrderResult,
    SupplierPortal,
    portal_for_supplier,
    register_portal,
)

UNCONFIGURED_HOST = "generic.invalid"  # RFC 2606: guaranteed never to resolve

_UNCONFIGURED_POLICY = PortalPolicy(
    allowed_hosts=(UNCONFIGURED_HOST,),
    start_url=f"https://{UNCONFIGURED_HOST}/",
    basket_url=f"https://{UNCONFIGURED_HOST}/basket",
    login_url=f"https://{UNCONFIGURED_HOST}/login",
)

_REQUIRED_KEYS = ("hosts", "start_url", "basket_url", "login_url")


def policy_from_config(config: dict[str, Any]) -> PortalPolicy:
    """A `PortalPolicy` from a `channel_config` dict. Raises `ValueError` when a
    required key is missing or a URL is off the allowlist -- a misconfigured portal
    must fail at configuration time, not on the first navigation."""
    missing = [k for k in _REQUIRED_KEYS if not config.get(k)]
    if missing:
        raise ValueError(f"generic portal config is missing {', '.join(missing)}")
    hosts_raw = config["hosts"]
    hosts = tuple(
        str(h).lower().strip() for h in ([hosts_raw] if isinstance(hosts_raw, str) else hosts_raw)
    )
    if not hosts:
        raise ValueError("generic portal config has an empty hosts list")
    policy = PortalPolicy(
        allowed_hosts=hosts,
        start_url=str(config["start_url"]),
        basket_url=str(config["basket_url"]),
        login_url=str(config["login_url"]),
        forbidden_control_patterns=DEFAULT_FORBIDDEN_CONTROLS
        + tuple(str(p) for p in config.get("forbidden_controls", ())),
        forbidden_url_patterns=DEFAULT_FORBIDDEN_URLS
        + tuple(str(p) for p in config.get("forbidden_urls", ())),
    )
    for key in ("start_url", "basket_url"):
        host = urlparse(getattr(policy, key)).hostname or ""
        if not policy.host_allowed(host):
            raise ValueError(f"{key} host {host!r} is not in hosts {hosts}")
        forbidden = policy.url_forbidden(getattr(policy, key))
        if forbidden:
            raise ValueError(f"{key} matches forbidden pattern {forbidden!r}")
    return policy


class GenericPortal(HeuristicPortal):
    """See the module docstring. `configured` is False on the registered default."""

    slug = "generic"
    label = "Web shop (generic)"
    supports_scripted_add = True

    def __init__(
        self, config: dict[str, Any] | None = None, *, policy: PortalPolicy | None = None
    ) -> None:
        """`policy` overrides the one derived from `config` (a subclass with a
        hand-written policy, e.g. one whose basket lives under `/checkout/cart/`)."""
        self.config: dict[str, Any] = dict(config or {})
        self.configured = bool(config) or policy is not None
        if policy is not None:
            self.policy = policy
            self.slug = str(self.config.get("slug") or type(self).slug)
            self.label = str(self.config.get("label") or type(self).label)
            extra = tuple(str(p) for p in self.config.get("subtotal_labels", ()))
            self.subtotal_labels = extra + SUBTOTAL_LABELS
        elif self.configured:
            self.policy = policy_from_config(self.config)
            self.slug = str(self.config.get("slug") or "generic")
            self.label = str(
                self.config.get("label") or f"Web shop ({self.policy.allowed_hosts[0]})"
            )
            extra = tuple(str(p) for p in self.config.get("subtotal_labels", ()))
            self.subtotal_labels = extra + SUBTOTAL_LABELS
        else:
            self.policy = _UNCONFIGURED_POLICY
        self._configure_tiers()

    #: Tier 0 template (module docstring); empty when the shop has none.
    cart_link_template: str = ""
    cart_item_key: str = "sku"
    cart_item_format: str = "{id}:{qty}"
    cart_item_sep: str = ","

    def _configure_tiers(self) -> None:
        """Read the optional Tier 0 / Tier 1 / Tier 2 keys. Misconfiguration raises
        `ValueError` here, at bind time, never on the first job."""
        cfg = self.config
        self.prefers_headed = bool(cfg.get("prefers_headed", False))
        template = str(cfg.get("cart_link_template") or "").strip()
        if template:
            if "{items}" not in template:
                raise ValueError("cart_link_template must contain '{items}'")
            host = urlparse(template).hostname or ""
            if not self.policy.host_allowed(host):
                raise ValueError(f"cart_link_template host {host!r} is not in hosts")
            key = str(cfg.get("cart_item_key") or "sku")
            if key not in ("sku", "variant_id_from_url"):
                raise ValueError("cart_item_key must be 'sku' or 'variant_id_from_url'")
            self.cart_link_template = template
            self.cart_item_key = key
            self.cart_item_format = str(cfg.get("cart_item_format") or "{id}:{qty}")
            self.cart_item_sep = str(cfg.get("cart_item_sep") or ",")
        pad = str(cfg.get("quick_order_url") or "").strip()
        if pad:
            host = urlparse(pad).hostname or ""
            if not self.policy.host_allowed(host):
                raise ValueError(f"quick_order_url host {host!r} is not in hosts")
            forbidden = self.policy.url_forbidden(pad)
            if forbidden:
                raise ValueError(f"quick_order_url matches forbidden pattern {forbidden!r}")
            # Set only when configured, so `isinstance(portal, QuickOrderCapable)` is
            # the capability check (base.QuickOrderCapable).
            self.quick_order_url = pad

    # -- guards --------------------------------------------------------------

    def bind(self, supplier: Any) -> GenericPortal:
        """Registry hook: a per-supplier instance from `channel_config` when this is
        the unconfigured default and the config is complete; else this instance."""
        if self.configured:
            return self
        config = getattr(supplier, "channel_config", None) or {}
        if isinstance(config, dict) and all(config.get(k) for k in _REQUIRED_KEYS):
            return GenericPortal(config)
        return self

    def _require_configured(self) -> None:
        if not self.configured:
            raise PortalStepFailed(
                "generic portal not configured: set channel_config.hosts/start_url/"
                "basket_url/login_url on the supplier and build it with "
                "generic_portal_from_config()"
            )

    def is_signed_in(self, page: Page) -> bool:
        self._require_configured()
        return super().is_signed_in(page)

    def account_label(self, page: Page) -> str | None:
        self._require_configured()
        return super().account_label(page)

    def read_basket(self, page: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        self._require_configured()
        return super().read_basket(page)

    # -- tier 0: the basket as a URL -----------------------------------------

    def _cart_item_id(self, line: BasketLine) -> tuple[str | None, str]:
        if self.cart_item_key == "variant_id_from_url":
            if not line.product_url:
                return None, "no product URL to take ?variant= from"
            variant = parse_qs(urlparse(line.product_url).query).get("variant", [None])[0]
            if not variant:
                return None, "product URL has no ?variant=<id>"
            return str(variant).strip(), "variant from the product URL"
        sku = (line.sku or "").strip()
        if not sku:
            return None, "no SKU"
        return sku, "SKU"

    def cart_link(self, lines: Sequence[BasketLine]) -> CartLinkPlan | None:
        """`channel_config.cart_link_template` with `{items}` expanded to the lines
        (module docstring). None when the shop has no template or there are no
        lines; a line without its identifier is `uncovered` with the reason."""
        if not self.cart_link_template or not lines:
            return None
        items: dict[str, int] = {}
        covered: list[int] = []
        uncovered: dict[int, str] = {}
        refs: dict[int, str] = {}
        for line in lines:
            item_id, note = self._cart_item_id(line)
            if item_id is None:
                uncovered[line.po_line_id] = note
                continue
            if line.packs_wanted < 1:
                uncovered[line.po_line_id] = "quantity is zero"
                continue
            items[item_id] = items.get(item_id, 0) + line.packs_wanted
            covered.append(line.po_line_id)
            refs[line.po_line_id] = f"{note} {item_id}" if note == "SKU" else f"variant {item_id}"
        if items:
            expanded = self.cart_item_sep.join(
                self.cart_item_format.format(id=i, qty=q) for i, q in items.items()
            )
            url = self.cart_link_template.replace("{items}", expanded)
        else:
            url = self.policy.basket_url
        count = len(items)
        return CartLinkPlan(
            url=url,
            covered=tuple(covered),
            uncovered=uncovered,
            label=f"{self.label} cart link, {count} item{'' if count == 1 else 's'}",
            refs=refs,
        )

    # -- tier 1: the product-code pad ----------------------------------------

    def quick_order(self, page: Page, lines: Sequence[BasketLine]) -> QuickOrderResult:
        """The shared pad driver at `channel_config.quick_order_url`. Raises
        `PortalStepFailed` when no pad is configured (the runner should not have
        called it: `quick_order_url` is the capability check)."""
        self._require_configured()
        pad = getattr(self, "quick_order_url", None)
        if not pad:
            raise PortalStepFailed("no quick-order pad configured for this shop")
        extra = tuple(str(p) for p in self.config.get("quick_order_submit_names", ()))
        return quick_order_generic(
            page,
            self.policy,
            str(pad),
            lines,
            submit_names=(*extra, *QUICK_ORDER_SUBMIT_NAMES),
            read_basket=self.read_basket,
        )

    def quick_order_hints(self) -> str:
        custom = str(self.config.get("quick_order_hints") or "").strip()
        if custom:
            return custom
        pad = getattr(self, "quick_order_url", None)
        if not pad:
            return "This shop has no quick-order pad configured; add each line by product."
        return quick_order_hints_text(
            label=f"{self.label} ({self.policy.allowed_hosts[0]})",
            pad_url=str(pad),
            basket_url=self.policy.basket_url,
            columns="a product code box and a quantity box per row",
            submit="the button under the pad called 'Add to basket' / 'Add to cart' / "
            "'Find products' / 'Add all'",
        )

    # -- add -----------------------------------------------------------------

    def _product_name(self, page: Page) -> str:
        h1 = first_visible(page.get_by_role("heading", level=1), timeout_ms=LOCATOR_TIMEOUT_MS)
        if h1 is None:
            raise PortalStepFailed("product page has no level-1 heading; not a product page?")
        try:
            name = " ".join(h1.inner_text(timeout=LOCATOR_TIMEOUT_MS).split())
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("could not read the product heading") from exc
        if len(name) < 3:
            raise PortalStepFailed("product heading is empty")
        return name

    def _url_key(self, url: str) -> str | None:
        path = urlparse(url).path.rstrip("/")
        return path.rsplit("/", 1)[-1] or None

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        self._require_configured()
        if not line.product_url:
            raise PortalStepFailed("no product URL; search needed")
        host = urlparse(line.product_url).hostname or ""
        if not self.policy.host_allowed(host):
            raise PortalStepFailed(f"product URL host {host!r} is not this portal")

        goto(page, self.policy, line.product_url)
        dismiss_cookie_banner(page)
        name = self._product_name(page)
        url_key = self._url_key(line.product_url)

        # What does the basket already hold for this product?
        rows, _ = self.read_basket(page)
        row = find_row(rows, name=name, url_key=url_key)
        if row is not None:
            if int(row["qty"]) == line.packs_wanted:
                return replace(
                    line,
                    packs_in_basket=int(row["qty"]),
                    unit_price_seen_pence=row.get("unit_price_pence"),
                    line_total_seen_pence=row.get("line_total_pence"),
                    product_name_seen=row["name"],
                    status="already",
                    added_by="script",
                    note="basket already held the wanted quantity",
                )
            # Already there at the wrong quantity: correct it on the basket page
            # rather than adding on top.
            control = self._basket_row_control(page, row["name"])
            set_quantity(page, control, line.packs_wanted, commit="enter")
            rows, _ = self.read_basket(page)
            row = find_row(rows, name=name, url_key=url_key)
            if row is None or int(row["qty"]) != line.packs_wanted:
                raise PortalStepFailed("changed the basket quantity but the readback disagrees")
            return replace(
                line,
                packs_in_basket=int(row["qty"]),
                unit_price_seen_pence=row.get("unit_price_pence"),
                line_total_seen_pence=row.get("line_total_pence"),
                product_name_seen=row["name"],
                status="added",
                added_by="script",
                note="quantity corrected on the basket page",
            )

        # Not in the basket: product page, quantity, Add.
        goto(page, self.policy, line.product_url)
        qty = find_quantity_control(page)
        set_quantity(page, qty, line.packs_wanted, commit="blur")
        add = find_add_button(page)
        if self.policy.control_forbidden(self._name_of(add)):
            raise PortalStepFailed("the only add-like control is forbidden by policy")
        try:
            add.click(timeout=LOCATOR_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("the add button did not respond") from exc
        wait_settled(page)

        rows, _ = self.read_basket(page)
        row = find_row(rows, name=name, url_key=url_key)
        if row is None:
            raise PortalStepFailed("clicked add but the basket shows no line for this product")
        if int(row["qty"]) != line.packs_wanted:
            raise PortalStepFailed(
                f"basket shows {row['qty']} pack(s) of this product, wanted {line.packs_wanted}"
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

    @staticmethod
    def _name_of(control: Any) -> str | None:
        try:
            return str(control.inner_text(timeout=LOCATOR_TIMEOUT_MS))
        except (PlaywrightTimeout, PlaywrightError):
            return None

    def _basket_row_control(self, page: Page, row_name: str) -> Any:
        """The quantity control of the basket row whose text contains `row_name`."""
        needle = re.compile(re.escape(row_name[:40]), re.I)
        candidates = page.locator("li, tr, article, div").filter(has_text=needle)
        try:
            n = candidates.count()
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("could not locate the basket row") from exc
        # Innermost matching container is the last one Playwright returns for a
        # depth-first document order; walk from the end.
        for i in range(n - 1, -1, -1):
            try:
                return find_quantity_control(candidates.nth(i), timeout_ms=800)
            except PortalStepFailed:
                continue
        raise PortalStepFailed("basket row has no quantity control")

    # -- hints ---------------------------------------------------------------

    def agent_hints(self) -> str:
        if not self.configured:
            return (
                "This portal is not configured. Do nothing: report that the supplier's "
                "channel_config lacks hosts/start_url/basket_url/login_url."
            )
        p = self.policy
        lines = [
            f"Web shop at {p.allowed_hosts[0]}. Start page: {p.start_url}",
            "Search: the search box at the top of the page (role 'searchbox' or a 'Search' "
            "text field); type the product name, press Enter.",
            "Add: on a product page set the 'Qty'/'Quantity' input to the packs wanted, then "
            "press the button called 'Add to basket' / 'Add to cart' / 'Add'.",
            "Quantity: prefer the number input; if the basket has '+'/'-' buttons, click "
            "'+' one at a time and read the number back.",
            f"Basket: called 'Basket' or 'Cart', at {p.basket_url}. Finish on that page.",
            "Never touch: Checkout, Proceed to checkout, Place order, Pay, saved cards, "
            "delivery slots, subscriptions, account settings, or sign-in. If the site asks "
            "you to sign in, enter a code or solve a CAPTCHA, stop and say needs_human.",
            "The job ends on the basket page with the lines and the subtotal visible.",
        ]
        extra = str(self.config.get("hints") or "").strip()
        if extra:
            lines.append(extra)
        return "\n".join(lines)


# The default, unconfigured entry. No name hints: a supplier reaches it only by
# setting channel_config.portal = "generic".
register_portal()(GenericPortal)


def generic_portal_from_config(config: dict[str, Any]) -> SupplierPortal:
    """A `GenericPortal` bound to one supplier's `channel_config`. Raises
    `ValueError` when the config is incomplete."""
    return GenericPortal(config)


def configured_portal_for_supplier(supplier: Any) -> SupplierPortal | None:
    """Kept for callers that spelled it out; `portal_for_supplier` now binds through
    `GenericPortal.bind`, so the two are the same."""
    return portal_for_supplier(supplier)


__all__ = [
    "UNCONFIGURED_HOST",
    "GenericPortal",
    "configured_portal_for_supplier",
    "generic_portal_from_config",
    "policy_from_config",
]
