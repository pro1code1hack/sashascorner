"""Monolith, the Eastern-European foods wholesaler the cafe buys from.

**The ordering site is unverified.** The seeded supplier has `order_channel EMAIL`,
no `order_url` and an empty `channel_config`, and nobody has confirmed whether
Monolith's Scottish depot takes web orders at all (CLAUDE.md §15 lists the
supplier terms as placeholders). This adapter therefore:

* registers under the name hint "monolith" with a **placeholder** policy on the
  host `monolithuk.co.uk` (start `/`, basket `/basket`, login `/login`) -- the
  hostname is a guess and must be replaced before a job is queued;
* scripts nothing but the sign-in check and the generic basket read
  (`supports_scripted_add = False`), and says so in its hints;
* offers `MonolithPortal.for_supplier(supplier)`, which rebinds the same adapter to
  the URLs in `supplier.channel_config` (`hosts`, `start_url`, `basket_url`,
  `login_url`) or, failing that, to `supplier.order_url` as start and basket.

Where to fix the URLs: set them on the supplier row (`channel_config`, via the
Suppliers page or `cafeops supplier ...`) rather than editing `_PLACEHOLDER` here;
the placeholder exists only so the registry entry has a well-formed policy.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page

from cafeops.integrations.suppliers.portals.base import (
    BasketLine,
    PortalStepFailed,
    register_portal,
)
from cafeops.integrations.suppliers.portals.generic import GenericPortal

PLACEHOLDER_HOST = "monolithuk.co.uk"

_PLACEHOLDER: dict[str, Any] = {
    "slug": "monolith",
    "label": "Monolith",
    "hosts": [PLACEHOLDER_HOST],
    "start_url": f"https://www.{PLACEHOLDER_HOST}/",
    "basket_url": f"https://www.{PLACEHOLDER_HOST}/basket",
    "login_url": f"https://www.{PLACEHOLDER_HOST}/login",
}

_URL_KEYS = ("hosts", "start_url", "basket_url", "login_url")


class MonolithPortal(GenericPortal):
    """Generic adapter bound to Monolith's (unverified) URLs. Adds are delegated."""

    slug = "monolith"
    label = "Monolith"
    supports_scripted_add = False
    #: True on the registered instance; False once rebound from a supplier row.
    urls_are_placeholders: bool = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(_PLACEHOLDER)
        cfg.update(config or {})
        super().__init__(cfg)
        self.slug = "monolith"
        self.label = "Monolith"
        self.urls_are_placeholders = PLACEHOLDER_HOST in self.policy.allowed_hosts

    @classmethod
    def for_supplier(cls, supplier: Any) -> MonolithPortal:
        """Rebind to the supplier row's URLs when it has any; else the placeholder."""
        config = getattr(supplier, "channel_config", None) or {}
        cfg: dict[str, Any] = {}
        if isinstance(config, dict):
            cfg = {k: config[k] for k in _URL_KEYS if config.get(k)}
        order_url = getattr(supplier, "order_url", None)
        if order_url and not cfg:
            host = urlparse(str(order_url)).hostname or ""
            if host:
                cfg = {
                    "hosts": [host.removeprefix("www.")],
                    "start_url": str(order_url),
                    "basket_url": str(order_url),
                    "login_url": str(order_url),
                }
        return cls(cfg)

    def bind(self, supplier: Any) -> MonolithPortal:
        """Registry hook (see base.PortalRegistry.for_supplier)."""
        return self.for_supplier(supplier)

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        why = "the ordering site is unverified" if self.urls_are_placeholders else "no scripted add"
        raise PortalStepFailed(f"Monolith: {why}; the model adds this line with the hints")

    def agent_hints(self) -> str:
        p = self.policy
        warning = (
            "WARNING: these URLs are placeholders; nobody has verified that Monolith takes "
            "web orders or where its shop is. If the start page is not a wholesale food shop "
            "belonging to Monolith, stop and say needs_human.\n"
            if self.urls_are_placeholders
            else ""
        )
        return (
            f"{warning}"
            f"Monolith wholesale shop at {p.allowed_hosts[0]}, signed in with the cafe's account.\n"
            "Search: the search box at the top of the page; type the product name from the "
            "order line, press Enter.\n"
            "Add: on a product card or page set the quantity (cases) then press "
            "'Add to basket' / 'Add to cart' / 'Add'.\n"
            "Quantity: type into the number box; otherwise press '+' one at a time and read "
            "the number back.\n"
            f"Basket: at {p.basket_url}. Finish on that page.\n"
            "Never touch: Checkout, Place order, Pay, delivery slots, saved cards, or account "
            "settings. If asked to sign in, enter a code or solve a CAPTCHA, stop and say "
            "needs_human.\n"
            "The job ends on the basket page with every line and the subtotal visible."
        )


register_portal("monolith")(MonolithPortal)

__all__ = ["PLACEHOLDER_HOST", "MonolithPortal"]
