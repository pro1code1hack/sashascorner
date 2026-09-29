"""Supplier portal adapters: the deterministic half of basket staging.

docs/agents/BROWSER-ORDERING.md §3. The rule that shapes this module, taken from how
vendor-portal automation is done in practice: **script the known flow; pay a model
only for the steps that break.** A portal adapter knows the supplier's web shop well
enough to sign-in-check, add a line by product URL or SKU and read the basket back
with plain Playwright. When a scripted step fails (`PortalStepFailed`), the job hands
that one step to the model through the browser toolset, with this adapter's
`agent_hints()` in the prompt, and carries on.

Every adapter carries a `PortalPolicy`, and the policy is enforced twice: the loop
refuses a model action before it runs, and the executor refuses off-allowlist
navigations at the network layer. A control whose accessible name matches
`forbidden_control_patterns` is never clicked by anyone but a person. That is
invariant 1 as code, not as a prompt.

Adapters are registered with `@register_portal` and looked up by slug. A supplier
names its portal in `supplier.channel_config["portal"]`; `portal_for_supplier` also
falls back to a name match so the seeded suppliers work without editing rows.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Accessible names that submit or pay. Case-insensitive, matched against the trimmed
# accessible name of the control the model (or a script) wants to click. Shared by
# every adapter; an adapter may add to it, never remove from it.
DEFAULT_FORBIDDEN_CONTROLS: tuple[str, ...] = (
    r"\bplace (your )?order\b",
    r"\bpay( now| securely)?\b",
    r"\bbuy now\b",
    r"\bconfirm (and pay|order|purchase)\b",
    r"\bcomplete (order|purchase|checkout)\b",
    r"\bsubmit order\b",
    r"\bcheckout\b",
    r"\bcheck out\b",
    r"\bproceed to (payment|checkout)\b",
    r"\bbook (a )?(slot|delivery)\b",
    r"\bapply (for )?credit\b",
    r"\badd (a )?(new )?(card|payment method)\b",
)

# URL path fragments that mean "checkout has begun". Navigating here is refused.
DEFAULT_FORBIDDEN_URLS: tuple[str, ...] = (
    r"/checkout",
    r"/payment",
    r"/pay\b",
    r"/order-confirmation",
    r"/place-order",
    r"/buy/",
    r"/gp/buy/",
)


@dataclass(frozen=True, slots=True)
class PortalPolicy:
    """What a job may do on this portal. Enforced by the loop and the executor."""

    #: Hosts the browser may navigate to (exact or suffix match, e.g. "tesco.com"
    #: covers "www.tesco.com"). Subresource requests (CDNs) are not filtered; only
    #: top-level navigations are.
    allowed_hosts: tuple[str, ...]
    #: Where to open the shop (front page or basket). The first navigation of a job.
    start_url: str
    #: The basket page. The job ends here, with a screenshot.
    basket_url: str
    #: Where a person signs in. Used by the `connect` flow only; the model never
    #: visits it and never types a password anywhere.
    login_url: str
    forbidden_control_patterns: tuple[str, ...] = DEFAULT_FORBIDDEN_CONTROLS
    forbidden_url_patterns: tuple[str, ...] = DEFAULT_FORBIDDEN_URLS

    def host_allowed(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(host == h or host.endswith("." + h) for h in self.allowed_hosts)

    def url_forbidden(self, url: str) -> str | None:
        for pat in self.forbidden_url_patterns:
            if re.search(pat, url, flags=re.IGNORECASE):
                return pat
        return None

    def control_forbidden(self, accessible_name: str | None) -> str | None:
        if not accessible_name:
            return None
        name = " ".join(accessible_name.split())
        for pat in self.forbidden_control_patterns:
            if re.search(pat, name, flags=re.IGNORECASE):
                return pat
        return None


@dataclass(frozen=True, slots=True)
class BasketLine:
    """One order line as the job wants it, and what the portal showed for it.

    Money is integer pence; quantities are packs (integers). `status` is the story
    of the line: `pending` before anything ran, `added` when the scripted or model
    step put it in the basket, `already` when the basket already held it at the
    wanted quantity, `substituted` when a different product was added (the note
    says which), `not_found`, `skipped` (policy or budget) or `failed`.
    """

    po_line_id: int
    ingredient_name: str
    sku: str
    product_url: str | None
    packs_wanted: int
    #: What the order expects to pay per pack, from `supplier_product.price_pence`.
    unit_price_expected_pence: int
    packs_in_basket: int | None = None
    unit_price_seen_pence: int | None = None
    line_total_seen_pence: int | None = None
    product_name_seen: str | None = None
    status: str = "pending"
    #: Which tier put it in the basket: "cart_link" | "quick_order" | "script" | "model" | None.
    added_by: str | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "po_line_id": self.po_line_id,
            "ingredient_name": self.ingredient_name,
            "sku": self.sku,
            "product_url": self.product_url,
            "packs_wanted": self.packs_wanted,
            "unit_price_expected_pence": self.unit_price_expected_pence,
            "packs_in_basket": self.packs_in_basket,
            "unit_price_seen_pence": self.unit_price_seen_pence,
            "line_total_seen_pence": self.line_total_seen_pence,
            "product_name_seen": self.product_name_seen,
            "status": self.status,
            "added_by": self.added_by,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class BasketSnapshot:
    """What the basket held when the job stopped. This is the proposal's payload."""

    supplier_id: int
    supplier_name: str
    portal_slug: str
    po_id: int
    basket_url: str
    lines: tuple[BasketLine, ...]
    #: The subtotal the portal displayed, when it could be read. None is "not read",
    #: never zero (invariant 8 spirit).
    subtotal_seen_pence: int | None
    total_expected_pence: int
    captured_at: datetime
    screenshot_asset_id: int | None = None
    warnings: tuple[str, ...] = ()
    #: Lines the basket showed that the order did not ask for (left over from a
    #: previous visit). Reported, never removed by the job.
    unexpected_lines: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return all(line.status in ("added", "already") for line in self.lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "portal_slug": self.portal_slug,
            "po_id": self.po_id,
            "basket_url": self.basket_url,
            "lines": [line.as_dict() for line in self.lines],
            "subtotal_seen_pence": self.subtotal_seen_pence,
            "total_expected_pence": self.total_expected_pence,
            "captured_at": self.captured_at.isoformat(),
            "screenshot_asset_id": self.screenshot_asset_id,
            "warnings": list(self.warnings),
            "unexpected_lines": list(self.unexpected_lines),
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class CartLinkPlan:
    """Tier 0: the basket as a URL (docs/agents/BROWSER-ORDERING.md §10).

    Amazon's add-to-cart form and Shopify's cart permalinks build a pre-filled basket
    from product ids and quantities. Opening `url` in the owner's own browser IS the
    staged basket: no automation, no stored session, no bot detection. `covered` are
    the po_line_ids the link carries; `uncovered` maps the rest to the reason (no
    ASIN in the URL, variant lookup failed...). A plan with nothing uncovered needs
    no browser at all; a partial plan is opened in the worker's browser first and the
    remaining lines are added by the other tiers.
    """

    url: str
    covered: tuple[int, ...]
    uncovered: dict[int, str] = field(default_factory=dict)
    #: One line for the proposal body, e.g. "Amazon add-to-cart link, 3 items".
    label: str = ""
    #: Per-line identifiers the link used (po_line_id -> "ASIN B0…" / "variant 4471…").
    refs: dict[int, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.uncovered


@dataclass(frozen=True, slots=True)
class QuickOrderResult:
    """Tier 1: one product-code form filled and submitted (Booker, Brakes)."""

    lines: tuple[BasketLine, ...]
    #: What the pad reported for codes it did not recognise, verbatim.
    rejected: dict[int, str] = field(default_factory=dict)
    note: str = ""


class PortalStepFailed(Exception):
    """A scripted step could not do its job (selector gone, layout changed, product
    page unexpected). The loop catches this and hands the step to the model. It is
    NOT for policy refusals or for "the portal wants a person" -- see the two below.
    """


class PortalNeedsHuman(Exception):
    """The portal is asking for something only a person can give: a sign-in, a
    one-time code, a CAPTCHA, a terms acceptance. The job stops as NEEDS_HUMAN with
    this message and nothing else is attempted.
    """


class PortalPolicyRefusal(Exception):
    """An action would cross the policy (submit, pay, leave the allowlist). The job
    logs a REFUSED step and continues without doing it."""


@runtime_checkable
class SupplierPortal(Protocol):
    """One supplier's web shop, scripted.

    Every method receives a Playwright `Page` that is already on the portal (the loop
    navigated to `policy.start_url`). Methods must not sign in, must not click
    anything the policy forbids, and must raise `PortalStepFailed` rather than guess
    when the page is not what they expect: a wrong guess puts the wrong thing in the
    basket, and a raised step only costs a model call.
    """

    slug: str
    label: str
    policy: PortalPolicy
    #: False when the adapter has no scripted add and every line goes to the model.
    supports_scripted_add: bool

    def is_signed_in(self, page: Page) -> bool:
        """True when the page shows a signed-in account. Must not navigate to the
        login page. May navigate to `policy.start_url`."""
        ...

    def account_label(self, page: Page) -> str | None:
        """A masked name/email for the session row ("s***@gmail.com"), or None."""
        ...

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        """Put `line.packs_wanted` packs of the product in the basket and return the
        line with `packs_in_basket`, `status="added"` (or "already"), `added_by="script"`
        and any price seen. Raise `PortalStepFailed` when the product page or the
        quantity control is not recognisable."""
        ...

    def read_basket(self, page: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        """Navigate to the basket and return (rows, subtotal_pence). Each row is
        {"name": str, "qty": int, "unit_price_pence": int | None,
        "line_total_pence": int | None, "product_url": str | None}. Subtotal None when
        it could not be read. Raise `PortalStepFailed` if the basket page is not
        recognisable."""
        ...

    def agent_hints(self) -> str:
        """Portal-specific guidance for the model fallback: where search is, what a
        quantity control looks like, what "Add" is called, what NOT to touch."""
        ...

    # ---- optional capabilities --------------------------------------------------
    #
    # Declared below as their own runtime-checkable protocols (`Bindable`,
    # `CartLinkCapable`, `QuickOrderCapable`) and tested with `isinstance`; an adapter
    # without the members simply is not an instance. `prefers_headed: bool` (Tier 2:
    # the site refuses headless browsers, Tesco/Akamai) is carried by every
    # `HeuristicPortal`; the worker launches a visible window when a display is
    # available and otherwise reports NEEDS_HUMAN with that reason.


@runtime_checkable
class Bindable(Protocol):
    """An adapter whose registered instance is a template: `bind(supplier)` returns
    the per-supplier instance built from `supplier.channel_config` (generic, Monolith,
    or a singleton with a per-supplier override). None or `self` when there is
    nothing to bind."""

    def bind(self, supplier: Any) -> SupplierPortal | None: ...


@runtime_checkable
class CartLinkCapable(Protocol):
    """Tier 0: the basket as a URL, built without a browser.

    Returns None when the portal has no such mechanism at all; a plan with
    `uncovered` entries when only some lines could be encoded. Must not raise for a
    line it cannot encode -- the reason goes in `uncovered`. May do a few
    unauthenticated HTTP fetches (Shopify's public product JSON); never a sign-in."""

    def cart_link(self, lines: Sequence[BasketLine]) -> CartLinkPlan | None: ...


@runtime_checkable
class QuickOrderCapable(Protocol):
    """Tier 1: the product-code pad at `quick_order_url`.

    `quick_order` fills every line's `sku` and quantity in ONE form, submits, and
    reads back which codes were accepted. It raises `PortalStepFailed` when the pad is
    not where or what it should be (the model then drives the pad with
    `quick_order_hints()`), never for a single rejected code (that goes in
    `rejected`). An adapter that only sets `quick_order_url` when one is configured
    (generic) is not an instance until it is."""

    quick_order_url: str

    def quick_order(self, page: Page, lines: Sequence[BasketLine]) -> QuickOrderResult: ...

    def quick_order_hints(self) -> str: ...


@dataclass(slots=True)
class PortalRegistry:
    _by_slug: dict[str, SupplierPortal] = field(default_factory=dict)
    #: Lower-cased substrings of supplier names, so seeded suppliers resolve.
    _name_hints: dict[str, str] = field(default_factory=dict)

    def register(self, portal: SupplierPortal, *, name_hints: tuple[str, ...] = ()) -> None:
        if portal.slug in self._by_slug:
            raise ValueError(f"portal {portal.slug!r} registered twice")
        self._by_slug[portal.slug] = portal
        for hint in name_hints:
            self._name_hints[hint.lower()] = portal.slug

    def get(self, slug: str) -> SupplierPortal | None:
        return self._by_slug.get(slug)

    def slugs(self) -> list[str]:
        return sorted(self._by_slug)

    def all(self) -> list[SupplierPortal]:
        return [self._by_slug[s] for s in self.slugs()]

    def for_supplier(self, supplier: Any) -> SupplierPortal | None:
        """`channel_config["portal"]` first; then a name hint; else None (no
        integration for this supplier, and nothing is queued for it)."""
        config = getattr(supplier, "channel_config", None) or {}
        slug = config.get("portal") if isinstance(config, dict) else None
        portal: SupplierPortal | None = None
        if slug:
            portal = self._by_slug.get(str(slug))
        else:
            name = str(getattr(supplier, "name", "") or "").lower()
            for hint, hinted_slug in self._name_hints.items():
                if hint in name:
                    portal = self._by_slug[hinted_slug]
                    break
        if portal is None:
            return None
        # Adapters whose URLs come from the supplier row (generic, Monolith) expose
        # `bind(supplier)` and return a per-supplier instance; the registered singleton
        # is only a template. Everything else is returned as is.
        if isinstance(portal, Bindable):
            bound = portal.bind(supplier)
            if bound is not None:
                return bound
        return portal


REGISTRY = PortalRegistry()


def register_portal(*name_hints: str) -> Any:
    """Class decorator: instantiate the adapter and register it under its slug."""

    def deco(cls: type) -> type:
        REGISTRY.register(cls(), name_hints=name_hints)
        return cls

    return deco


def portal_for(slug: str) -> SupplierPortal:
    portal = REGISTRY.get(slug)
    if portal is None:
        raise LookupError(f"no portal adapter registered as {slug!r}")
    return portal


def portal_for_supplier(supplier: Any) -> SupplierPortal | None:
    return REGISTRY.for_supplier(supplier)


__all__ = [
    "DEFAULT_FORBIDDEN_CONTROLS",
    "DEFAULT_FORBIDDEN_URLS",
    "REGISTRY",
    "BasketLine",
    "BasketSnapshot",
    "Bindable",
    "CartLinkCapable",
    "CartLinkPlan",
    "PortalNeedsHuman",
    "PortalPolicy",
    "PortalPolicyRefusal",
    "PortalRegistry",
    "PortalStepFailed",
    "QuickOrderCapable",
    "QuickOrderResult",
    "SupplierPortal",
    "portal_for",
    "portal_for_supplier",
    "register_portal",
]
