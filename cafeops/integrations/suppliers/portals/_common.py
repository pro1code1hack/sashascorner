"""Shared Playwright helpers for the portal adapters, and `HeuristicPortal`, the base
most adapters build on.

Everything here is *scripted* half (docs/agents/BROWSER-ORDERING.md §1 finding 2):
plain Playwright with role-based locators and short timeouts. A helper that is not
sure raises `PortalStepFailed` so the model gets that one step; a helper that sees a
sign-in / one-time code / CAPTCHA raises `PortalNeedsHuman`. Nothing here signs in
and nothing here navigates to a URL the adapter's policy forbids.

Conventions:

* Money parses to integer pence (`parse_pence`). A price that does not parse is
  `None`, never zero.
* Locators are role/label/placeholder based (`get_by_role("button", name=...)`);
  a CSS selector appears only where a site has kept the same id for years (Amazon's
  `#add-to-cart-button`) or where no role exists (a bare `input[name=qty]`).
* Timeouts are short: 3-5 s per locator, 20 s per navigation. A slow step should fail
  and go to the model, not hang the worker.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from cafeops.integrations.suppliers.portals.base import (
    BasketLine,
    PortalNeedsHuman,
    PortalPolicy,
    PortalPolicyRefusal,
    PortalStepFailed,
    QuickOrderResult,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

LOCATOR_TIMEOUT_MS = 4_000
NAV_TIMEOUT_MS = 20_000
SETTLE_TIMEOUT_MS = 6_000

# ---------------------------------------------------------------------------
# Money and text
# ---------------------------------------------------------------------------

_MONEY_POUNDS = re.compile(r"(-)?(?:£|GBP)\s*(\d[\d,]*)(?:\.(\d{1,2}))?")
_MONEY_PENCE = re.compile(r"(-)?(\d+)\s*p\b", re.IGNORECASE)
_MONEY_DECIMAL = re.compile(r"(-)?(\d[\d,]*)\.(\d{2})\b")
MONEY_ANY = re.compile(r"(?:£|GBP\s?)\s*\d[\d,]*(?:\.\d{1,2})?|\b\d+\s*p\b", re.IGNORECASE)


def parse_pence(text: str | None) -> int | None:
    """Integer pence from a price string, or None when there is no price in it.

    `"£1,234.56" -> 123456`, `"£0.80" -> 80`, `"80p" -> 80`, `"1.20 each" -> 120`,
    `"GBP 2.50" -> 250`, `"-£1.00" -> -100`. A bare integer with no unit (`"2"`) is
    ambiguous and returns None rather than guessing pounds or pence.
    """
    if not text:
        return None
    s = " ".join(str(text).split())
    m = _MONEY_POUNDS.search(s)
    if m:
        sign, pounds, pence = m.groups()
        value = int(pounds.replace(",", "")) * 100 + int((pence or "0").ljust(2, "0"))
        return -value if sign else value
    m = _MONEY_PENCE.search(s)
    if m:
        sign, pence = m.groups()
        value = int(pence)
        return -value if sign else value
    m = _MONEY_DECIMAL.search(s)
    if m:
        sign, pounds, pence = m.groups()
        value = int(pounds.replace(",", "")) * 100 + int(pence)
        return -value if sign else value
    return None


def parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    m = re.search(r"\d+", str(text))
    return int(m.group(0)) if m else None


def mask_label(text: str | None) -> str | None:
    """`"sasha@gmail.com" -> "s***@gmail.com"`, `"Hello, Sasha" -> "S***a"`. None
    when there is nothing worth keeping."""
    if not text:
        return None
    s = " ".join(text.split())
    s = re.sub(r"^(hello|hi|hey|welcome( back)?|account)[,:]?\s*", "", s, flags=re.I).strip()
    if not s:
        return None
    if "@" in s:
        local, _, domain = s.partition("@")
        return f"{local[:1]}***@{domain}"
    if len(s) <= 2:
        return s[:1] + "***"
    return f"{s[0]}***{s[-1]}"


# ---------------------------------------------------------------------------
# Page state
# ---------------------------------------------------------------------------

_NEEDS_HUMAN_TEXT = re.compile(
    r"verification code|one[- ]time (code|passcode|password)|captcha|unusual traffic"
    r"|are you a robot|you'?re not a robot|not a robot|characters you see"
    r"|enter the characters|security check|two[- ]factor|2fa\b|authenticator app"
    r"|access denied|pardon our interruption|request blocked|verify you are human"
    r"|accept (the )?(new )?terms|terms (and|&) conditions have (changed|been updated)",
    re.IGNORECASE,
)


def page_text(page: Page, *, timeout_ms: int = LOCATOR_TIMEOUT_MS) -> str:
    try:
        return page.locator("body").inner_text(timeout=timeout_ms)
    except (PlaywrightTimeout, PlaywrightError):
        return ""


def detect_needs_human(page: Page, *, password_field: bool = True) -> None:
    """Raise `PortalNeedsHuman` when the page is asking for something only a person
    can give: a password field (when `password_field`), a verification / one-time
    code, a CAPTCHA, a bot check, a terms acceptance. Otherwise return."""
    if password_field:
        try:
            pw = page.locator("input[type='password']")
            if pw.count() and pw.first.is_visible():
                raise PortalNeedsHuman(f"the portal is asking for a sign-in ({page.url})")
        except (PlaywrightTimeout, PlaywrightError):
            pass
    text = page_text(page, timeout_ms=2_000)
    m = _NEEDS_HUMAN_TEXT.search(text)
    if m:
        raise PortalNeedsHuman(f"the portal is asking for a person: {m.group(0)!r} ({page.url})")


def wait_settled(page: Page, *, timeout_ms: int = SETTLE_TIMEOUT_MS) -> None:
    """Wait for network idle with a cap. Analytics beacons keep some shops from ever
    going idle; the cap keeps that from hanging a step."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeout:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        pass


def first_visible(*locators: Locator, timeout_ms: int = LOCATOR_TIMEOUT_MS) -> Locator | None:
    """The first locator (in order) whose first match is visible within the budget,
    polling every 150 ms. None when none turned up."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for loc in locators:
            try:
                if loc.first.is_visible():
                    return loc.first
            except (PlaywrightTimeout, PlaywrightError):
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.15)


def goto(page: Page, policy: PortalPolicy, url: str, *, check_human: bool = True) -> None:
    """Navigate inside the policy: refuse a forbidden URL or an off-allowlist host
    before the request is made, settle, then look for a sign-in / CAPTCHA wall."""
    host = urlparse(url).hostname or ""
    if not policy.host_allowed(host):
        raise PortalPolicyRefusal(f"host {host!r} is not on the allowlist for this portal")
    pat = policy.url_forbidden(url)
    if pat:
        raise PortalPolicyRefusal(f"URL {url!r} matches forbidden pattern {pat!r}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeout as exc:
        raise PortalStepFailed(f"navigation to {url} timed out") from exc
    wait_settled(page)
    # A shop may redirect a top-level navigation into checkout or sign-in itself.
    landed = policy.url_forbidden(page.url)
    if landed:
        raise PortalPolicyRefusal(f"portal redirected to {page.url!r} ({landed!r}); stopped")
    if check_human:
        detect_needs_human(page)


def ensure_on_portal(page: Page, policy: PortalPolicy) -> None:
    """Navigate to `start_url` unless the page is already on an allowed host."""
    host = urlparse(page.url).hostname or ""
    if not host or not policy.host_allowed(host):
        goto(page, policy, policy.start_url, check_human=False)


def dismiss_cookie_banner(page: Page, *, timeout_ms: int = 1_500) -> bool:
    """Best-effort click on an "Accept all cookies" button. Harmless, never forbidden,
    and a persistent profile only sees it once. Returns whether one was clicked."""
    btn = first_visible(
        page.get_by_role("button", name=re.compile(r"^\s*(accept|allow) all( cookies)?\s*$", re.I)),
        page.get_by_role("button", name=re.compile(r"accept (all )?cookies", re.I)),
        timeout_ms=timeout_ms,
    )
    if btn is None:
        return False
    try:
        btn.click(timeout=LOCATOR_TIMEOUT_MS)
        wait_settled(page, timeout_ms=2_000)
        return True
    except (PlaywrightTimeout, PlaywrightError):
        return False


# ---------------------------------------------------------------------------
# Quantity controls
# ---------------------------------------------------------------------------

QTY_CONTROL_CSS = ", ".join(
    (
        "input[type='number']",
        "[role='spinbutton']",
        "input[name*='qty' i]",
        "input[name*='quant' i]",
        "input[id*='qty' i]",
        "input[id*='quantity' i]",
        "input[aria-label*='quantity' i]",
        "select[name*='qty' i]",
        "select[name*='quant' i]",
        "select[aria-label*='quantity' i]",
        "select[id*='quantity' i]",
    )
)


def _tag(control: Locator) -> str:
    try:
        return str(control.evaluate("e => e.tagName.toLowerCase()", timeout=LOCATOR_TIMEOUT_MS))
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise PortalStepFailed("quantity control vanished") from exc


def read_quantity(control: Locator) -> int | None:
    """The integer a quantity control shows: an input's value, a select's value, or
    the first integer in the element's text. None when there is none."""
    try:
        tag = _tag(control)
        if tag in ("input", "select", "textarea"):
            return parse_int(control.input_value(timeout=LOCATOR_TIMEOUT_MS))
        return parse_int(control.inner_text(timeout=LOCATOR_TIMEOUT_MS))
    except (PlaywrightTimeout, PlaywrightError, PortalStepFailed):
        return None


def set_quantity(page: Page, control: Locator, packs: int, *, commit: str = "blur") -> int:
    """Set a quantity control to `packs` and verify it took. Strategy: a number/text
    input is filled and committed (`commit="enter"` submits, e.g. a basket's "update"
    form; `"blur"` tabs away, for a product page where Enter would submit the add
    form); a select picks the option; anything else raises. Returns the value read
    back; raises `PortalStepFailed` when it does not equal `packs`."""
    if packs < 0:
        raise ValueError("packs must be >= 0")
    tag = _tag(control)
    try:
        if tag == "select":
            try:
                control.select_option(str(packs), timeout=LOCATOR_TIMEOUT_MS)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                raise PortalStepFailed(f"quantity select has no option {packs}") from exc
        elif tag in ("input", "textarea"):
            control.click(timeout=LOCATOR_TIMEOUT_MS)
            control.fill(str(packs), timeout=LOCATOR_TIMEOUT_MS)
            if commit == "enter":
                control.press("Enter", timeout=LOCATOR_TIMEOUT_MS)
            else:
                control.press("Tab", timeout=LOCATOR_TIMEOUT_MS)
        else:
            raise PortalStepFailed(f"quantity control is a <{tag}>, not an input or select")
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise PortalStepFailed(f"could not set quantity: {exc.__class__.__name__}") from exc
    wait_settled(page, timeout_ms=3_000)
    seen = read_quantity(control)
    if seen != packs:
        raise PortalStepFailed(f"set quantity to {packs} but the control reads {seen!r}")
    return seen


def click_increment(
    page: Page, plus: Locator, times: int, *, max_clicks: int = 6, readback: Locator | None = None
) -> int | None:
    """Click a "+" / "Add 1 more" control `times` times (bounded by `max_clicks`,
    so a broken readback can never run away). Returns the readback quantity when a
    readback locator is given."""
    if times > max_clicks:
        raise PortalStepFailed(f"{times} clicks wanted; the script stops at {max_clicks}")
    for _ in range(times):
        try:
            plus.click(timeout=LOCATOR_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed("the increment control stopped responding") from exc
        wait_settled(page, timeout_ms=2_000)
    return read_quantity(readback) if readback is not None else None


def find_quantity_control(
    scope: Page | Locator, *, timeout_ms: int = LOCATOR_TIMEOUT_MS
) -> Locator:
    """A quantity control inside `scope`, by label first, then by role, then by the
    usual names. Raises `PortalStepFailed` when there is none."""
    loc = first_visible(
        scope.get_by_label(re.compile(r"^\s*(qty|quantity)\b", re.I)),
        scope.get_by_role("spinbutton"),
        scope.locator(QTY_CONTROL_CSS),
        timeout_ms=timeout_ms,
    )
    if loc is None:
        raise PortalStepFailed("no quantity control recognised on the page")
    return loc


ADD_BUTTON_NAME = re.compile(
    r"^\s*add(\s+to\s+(basket|trolley|cart|bag|order))?\s*$|^\s*add to (basket|trolley|cart|bag)\b",
    re.I,
)


def find_add_button(scope: Page | Locator, *, timeout_ms: int = LOCATOR_TIMEOUT_MS) -> Locator:
    loc = first_visible(
        scope.get_by_role("button", name=ADD_BUTTON_NAME),
        scope.get_by_role("link", name=ADD_BUTTON_NAME),
        scope.locator("button[type='submit'], input[type='submit']").filter(
            has_text=re.compile(r"add", re.I)
        ),
        timeout_ms=timeout_ms,
    )
    if loc is None:
        raise PortalStepFailed("no 'Add to basket' control recognised on the page")
    return loc


# ---------------------------------------------------------------------------
# Basket reading
# ---------------------------------------------------------------------------

_BASKET_ROWS_JS = r"""
(args) => {
  const { qtySel, maxUp } = args;
  const money = /(?:£|GBP\s?)\s*\d[\d,]*(?:\.\d{1,2})?|\b\d+\s*p\b/i;
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const controls = Array.from(document.querySelectorAll(qtySel)).filter(visible);
  const seen = new Set();
  const rows = [];
  for (const c of controls) {
    let el = c;
    let found = null;
    for (let i = 0; i < maxUp && el; i++) {
      el = el.parentElement;
      if (!el || el === document.body) break;
      const text = el.innerText || '';
      if (!money.test(text)) continue;
      const nameEl = Array.from(el.querySelectorAll('h1,h2,h3,h4,h5,a[href]'))
        .find((n) => (n.innerText || '').trim().length > 2);
      if (nameEl) { found = el; break; }
    }
    if (!found || seen.has(found)) continue;
    seen.add(found);
    const text = found.innerText || '';
    const headings = Array.from(found.querySelectorAll('h1,h2,h3,h4,h5'))
      .map((n) => (n.innerText || '').trim()).filter((t) => t.length > 2);
    const links = Array.from(found.querySelectorAll('a[href]'))
      .filter((n) => (n.innerText || '').trim().length > 2);
    const name = headings[0] || (links[0] ? links[0].innerText.trim() : '');
    const href = links[0] ? links[0].href : null;
    const qty = c.tagName === 'SELECT' || c.tagName === 'INPUT' ? c.value
      : (c.getAttribute('aria-valuenow') || c.innerText || '');
    const prices = text.match(new RegExp(money.source, 'gi')) || [];
    rows.push({ name, qty, prices, href, text: text.slice(0, 400), controls: 1 });
  }
  return { rows, controls: controls.length };
}
"""

EMPTY_BASKET_TEXT = re.compile(
    r"(your )?(basket|trolley|cart|bag) is (currently )?empty"
    r"|no items in your (basket|trolley|cart)"
    r"|you have no items|nothing in your (basket|trolley|cart)",
    re.IGNORECASE,
)

SUBTOTAL_LABELS: tuple[str, ...] = (
    r"sub[- ]?total",
    r"guide price",
    r"basket total",
    r"trolley total",
    r"cart total",
    r"goods total",
    r"order total",
    r"\btotal\b",
)


def read_subtotal_text(page: Page, labels: tuple[str, ...] = SUBTOTAL_LABELS) -> int | None:
    """The first price that follows a subtotal-like label in the page text, trying
    `labels` in order. None when no label has a price near it."""
    text = page_text(page)
    if not text:
        return None
    for label in labels:
        m = re.search(
            label + r"[^£\n]{0,40}\n?[^£\n]{0,20}(£\s*\d[\d,]*(?:\.\d{1,2})?)", text, re.I
        )
        if m:
            return parse_pence(m.group(1))
    return None


def basket_rows_generic(
    page: Page, *, qty_css: str = QTY_CONTROL_CSS, max_up: int = 8
) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Best-effort basket rows: every visible quantity control on the page, walked
    up to the nearest ancestor that also shows a price and a product name. Returns
    (rows, empty) where `empty` is True when the page says the basket is empty.

    Row shape as `SupplierPortal.read_basket` wants it. `unit_price_pence` is the
    first price in the row, `line_total_pence` the last one when there are two or
    more; a row with one price gets it as the line total and no unit price, because
    which of the two it is cannot be known from the text alone. `text` is the row's
    collapsed text (trade portals print the product code there; the quick-order
    readback matches lines by it).
    """
    text = page_text(page)
    if EMPTY_BASKET_TEXT.search(text):
        return (), True
    try:
        result = page.evaluate(_BASKET_ROWS_JS, {"qtySel": qty_css, "maxUp": max_up})
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise PortalStepFailed("could not read the basket rows") from exc
    rows: list[dict[str, Any]] = []
    for raw in result.get("rows", []):
        qty = parse_int(str(raw.get("qty", "")))
        name = " ".join(str(raw.get("name", "")).split())
        if not name or qty is None:
            continue
        prices = [parse_pence(p) for p in raw.get("prices", [])]
        prices = [p for p in prices if p is not None]
        unit = prices[0] if len(prices) >= 2 else None
        total = prices[-1] if prices else None
        rows.append(
            {
                "name": name,
                "qty": qty,
                "unit_price_pence": unit,
                "line_total_pence": total,
                "product_url": raw.get("href") or None,
                "text": " ".join(str(raw.get("text", "")).split())[:300],
            }
        )
    if not rows and int(result.get("controls", 0)) > 0:
        raise PortalStepFailed(
            f"{result.get('controls')} quantity control(s) but no row with a name and price"
        )
    return tuple(rows), False


def names_match(a: str | None, b: str | None) -> bool:
    """Loose product-name match: case-insensitive containment either way after
    collapsing whitespace, or a 60%+ word overlap."""
    if not a or not b:
        return False
    x = " ".join(a.lower().split())
    y = " ".join(b.lower().split())
    if x in y or y in x:
        return True
    wx = {w for w in re.findall(r"[a-z0-9]+", x) if len(w) > 2}
    wy = {w for w in re.findall(r"[a-z0-9]+", y) if len(w) > 2}
    if not wx or not wy:
        return False
    return len(wx & wy) / min(len(wx), len(wy)) >= 0.6


def find_row(
    rows: tuple[dict[str, Any], ...], *, name: str | None = None, url_key: str | None = None
) -> dict[str, Any] | None:
    """The basket row for a product, by URL fragment (an ASIN, a product id) first,
    then by name."""
    if url_key:
        for row in rows:
            if row.get("product_url") and url_key in str(row["product_url"]):
                return row
    if name:
        for row in rows:
            if names_match(name, str(row.get("name"))):
                return row
    return None


# ---------------------------------------------------------------------------
# HeuristicPortal: what the trade portals and the generic portal share
# ---------------------------------------------------------------------------

SIGN_IN_NAME = re.compile(r"^\s*(sign|log)\s?in\b|^\s*login\b", re.I)
SIGN_OUT_NAME = re.compile(r"\b(sign|log)\s?out\b|^\s*logout\b", re.I)
# "Hello, Sasha" / "Welcome, s***" / "Hi Sasha": a greeting followed by a name, and
# never "sign in" after it (Amazon's "Hello, sign in").
GREETING_NAME = re.compile(r"^\s*(hello|hi|welcome( back)?),?\s+(?!(sign|log)\s?in)\S", re.I)
# Anchored negative lookahead: "Hello, sign in" (Amazon signed out) must not count.
# A bare "Account" link is deliberately NOT a signal: Shopify and others show it to
# guests, and the live smoke test read Cups Direct as signed in because of it.
ACCOUNT_NAME = re.compile(
    r"^(?!.*\b(sign|log)\s?in\b).*"
    r"(\b(my|your) account\b|\bhello,|\bhi,|\bwelcome,|\bmy orders\b"
    r"|\bsign out\b|\blog\s?out\b)",
    re.I | re.S,
)


class HeuristicPortal:
    """A portal known only by its shape: sign-in state from the header, basket rows
    from the generic reader, and no scripted add unless a subclass provides one.

    Subclasses set `slug`, `label`, `policy`, and may set `supports_scripted_add`
    and override any method. `is_signed_in` and `account_label` are deliberately
    conservative: "not sure" is False / None, and the job then asks a person to
    reconnect, which costs a minute and never a wrong basket.
    """

    slug: str = ""
    label: str = ""
    policy: PortalPolicy
    supports_scripted_add: bool = False
    #: Tier 2 (base.SupplierPortal): True only where a headless browser is refused
    #: outright (Tesco/Akamai). Every adapter carries the attribute so the runner
    #: can read it without a getattr default.
    prefers_headed: bool = False
    #: Labels to try, in order, for the basket subtotal.
    subtotal_labels: tuple[str, ...] = SUBTOTAL_LABELS

    # -- per-supplier overrides ------------------------------------------------

    #: `channel_config` keys a supplier row may override on a singleton adapter.
    bindable_keys: tuple[str, ...] = ("quick_order_url", "prefers_headed")

    def bind(self, supplier: Any) -> HeuristicPortal:
        """Registry hook (base.PortalRegistry.for_supplier): when the supplier row
        overrides one of `bindable_keys` in `channel_config` (the operator found the
        real quick-order URL, or a site started refusing headless), return a shallow
        copy carrying it; otherwise this instance. Generic and Monolith override this
        with their own config binding."""
        config = getattr(supplier, "channel_config", None) or {}
        if not isinstance(config, dict):
            return self
        overrides = {k: config[k] for k in self.bindable_keys if config.get(k) is not None}
        if not overrides:
            return self
        bound = copy.copy(self)
        for key, value in overrides.items():
            setattr(bound, key, bool(value) if key == "prefers_headed" else str(value))
        return bound

    # -- sign-in -----------------------------------------------------------

    def _sign_in_control(self, page: Page) -> Locator | None:
        return first_visible(
            page.get_by_role("link", name=SIGN_IN_NAME),
            page.get_by_role("button", name=SIGN_IN_NAME),
            timeout_ms=2_500,
        )

    def _account_control(self, page: Page) -> Locator | None:
        return first_visible(
            page.get_by_role("link", name=ACCOUNT_NAME),
            page.get_by_role("button", name=ACCOUNT_NAME),
            timeout_ms=2_500,
        )

    def _in_dom(self, page: Page, name: re.Pattern[str]) -> bool:
        """Whether a link/button with this name exists at all, visible or not (a
        header dropdown keeps "Sign out" hidden until opened)."""
        try:
            return (
                page.get_by_role("link", name=name, include_hidden=True).count() > 0
                or page.get_by_role("button", name=name, include_hidden=True).count() > 0
            )
        except (PlaywrightTimeout, PlaywrightError):
            return False

    def is_signed_in(self, page: Page) -> bool:
        """Evidence-ranked. Signed out: a visible password box, a visible "Sign in",
        or a "Sign in"/"Log in" link anywhere in the DOM with no "Sign out". Signed
        in: a "Sign out"/"Log out" control in the DOM, or a visible greeting with a
        name. "My account" / "My orders" alone prove nothing -- the Cups Direct
        header shows "My Account" to guests -- so they read as signed out."""
        ensure_on_portal(page, self.policy)
        dismiss_cookie_banner(page)
        # A password box on screen is "signed out", not "needs a person": the job
        # reports the expired session itself. CAPTCHA / code walls still raise.
        detect_needs_human(page, password_field=False)
        try:
            if page.locator("input[type='password']").first.is_visible():
                return False
        except (PlaywrightTimeout, PlaywrightError):
            pass
        if self._sign_in_control(page) is not None:
            return False
        if self._in_dom(page, SIGN_OUT_NAME):
            return True
        if self._in_dom(page, SIGN_IN_NAME):
            return False
        greeting = first_visible(
            page.get_by_role("link", name=GREETING_NAME),
            page.get_by_role("button", name=GREETING_NAME),
            page.get_by_text(GREETING_NAME),
            timeout_ms=1_500,
        )
        return greeting is not None

    def account_label(self, page: Page) -> str | None:
        ensure_on_portal(page, self.policy)
        if self._sign_in_control(page) is not None:
            return None
        control = self._account_control(page)
        if control is None:
            return None
        try:
            text = control.inner_text(timeout=LOCATOR_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError):
            return None
        text = " ".join(text.split())
        if re.fullmatch(r"(my |your )?account|my orders|sign out|log ?out", text, re.I):
            return None
        return mask_label(text)

    # -- lines ---------------------------------------------------------------

    def add_line_scripted(self, page: Page, line: BasketLine) -> BasketLine:
        raise PortalStepFailed(
            f"{self.label} has no scripted add; the model adds this line with the hints"
        )

    # -- basket --------------------------------------------------------------

    def read_basket(self, page: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        goto(page, self.policy, self.policy.basket_url)
        dismiss_cookie_banner(page)
        rows, empty = basket_rows_generic(page)
        if empty:
            return (), 0
        subtotal = read_subtotal_text(page, self.subtotal_labels)
        if not rows:
            raise PortalStepFailed("basket page shows neither rows nor an empty-basket message")
        return rows, subtotal

    def agent_hints(self) -> str:  # pragma: no cover - subclasses override
        return ""


# ---------------------------------------------------------------------------
# Tier 0: product identifiers for cart links (no browser, no sign-in)
# ---------------------------------------------------------------------------

ASIN_IN_URL = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?=[/?#]|$)", re.I)
_BARE_ASIN = re.compile(r"^[A-Z0-9]{10}$", re.I)

#: What the unauthenticated product-JSON fetch identifies itself as. A plain
#: browser UA, because some Shopify fronts (and every Cloudflare in front of one)
#: answer `python-httpx` with a challenge page instead of JSON.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def asin_from_url(url: str | None, sku: str | None = None) -> str | None:
    """The ASIN in an Amazon product URL (`/dp/<ASIN>`, `/gp/product/<ASIN>`,
    `/gp/aw/d/<ASIN>`), else a bare ten-character ASIN given as `sku`, else None.
    Upper-cased, because Amazon's add-to-cart form is case-sensitive."""
    if url:
        m = ASIN_IN_URL.search(url)
        if m:
            return m.group(1).upper()
    if sku:
        s = sku.strip()
        if _BARE_ASIN.match(s):
            return s.upper()
    return None


def product_json_url(product_url: str) -> str:
    """The public product JSON for a Shopify product page: the page URL without
    query or fragment, plus `.js` (`/products/<handle>` -> `/products/<handle>.js`;
    `/collections/x/products/<handle>` works the same way on Shopify). A page named
    `<x>.html` becomes `<x>.js`, which is what the fake shop serves."""
    u = urlparse(product_url)
    path = u.path.rstrip("/")
    if path.lower().endswith((".html", ".htm")):
        path = path.rsplit(".", 1)[0]
    if not path.lower().endswith(".js"):
        path += ".js"
    return urlunparse((u.scheme, u.netloc, path, "", "", ""))


def fetch_product_json(url: str) -> Any:
    """`GET url` with httpx: 8 s, redirects followed, browser UA. Raises on any
    failure (network, non-2xx, not JSON); `shopify_variant_id` turns that into a
    reason rather than an exception."""
    import httpx

    resp = httpx.get(
        url,
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json, text/javascript"},
    )
    resp.raise_for_status()
    return resp.json()


def _variant_label(v: dict[str, Any]) -> str:
    title = str(v.get("title") or "").strip()
    sku = str(v.get("sku") or "").strip()
    return f"{title} [{sku}]" if sku else title or str(v.get("id"))


def shopify_variant_id(
    product_url: str | None,
    sku: str | None,
    *,
    fetch: Callable[[str], Any] | None = None,
) -> tuple[str | None, str]:
    """The Shopify variant id a cart permalink needs for one line, as
    `(variant_id, note)`; `variant_id` is None when the line cannot be encoded and
    the note then says why (it goes into `CartLinkPlan.uncovered`).

    Order of preference: a `?variant=<id>` in the product URL wins without any
    fetch; otherwise the product JSON is fetched (`fetch(url)`, default httpx) and
    the variant whose `sku` equals `sku` (case-insensitive) is chosen; else the
    product's only variant; else the only *available* one; else the caller must
    choose (the note lists the variant titles). Never raises: a fetch that fails
    is a reason.
    """
    if not product_url:
        return None, "no product URL"
    from_query = parse_qs(urlparse(product_url).query).get("variant", [None])[0]
    if from_query and str(from_query).strip().isdigit():
        return str(from_query).strip(), "variant from the product URL"
    url = product_json_url(product_url)
    try:
        data = (fetch or fetch_product_json)(url)
    except Exception as exc:  # a reason, never a raise (the base contract)
        detail = " ".join(str(exc).split()).split(" For more information")[0][:120]
        return None, f"product JSON {url} failed: {exc.__class__.__name__}: {detail}".rstrip(": ")
    variants = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(variants, list) or not variants:
        return None, f"product JSON {url} has no variants; not a Shopify product?"
    variants = [v for v in variants if isinstance(v, dict) and v.get("id") is not None]
    if not variants:
        return None, f"product JSON {url} lists no variant ids"
    wanted = (sku or "").strip().lower()
    if wanted:
        for v in variants:
            if str(v.get("sku") or "").strip().lower() == wanted:
                return str(v["id"]), f"variant matched by SKU {sku}"
    if len(variants) == 1:
        return str(variants[0]["id"]), "the product's only variant"
    available = [v for v in variants if v.get("available") is True]
    if len(available) == 1:
        return str(available[0]["id"]), "the only available variant"
    titles = ", ".join(_variant_label(v) for v in variants[:8])
    more = f" (+{len(variants) - 8} more)" if len(variants) > 8 else ""
    why = f"SKU {sku!r} matches none" if wanted else "the line names no SKU and the URL no variant"
    return None, f"choose a variant: {titles}{more} -- {why}"


# ---------------------------------------------------------------------------
# Tier 1: the product-code quick-order pad (Booker, Brakes, generic)
# ---------------------------------------------------------------------------

#: Controls that submit a quick-order pad. Matched against the trimmed accessible
#: name; the policy's forbidden patterns are checked first, always.
QUICK_ORDER_SUBMIT_NAMES: tuple[str, ...] = (
    r"^\s*find (products?|items?)\s*$",
    r"^\s*add (all )?((items|products|lines|codes) )?to (basket|trolley|cart|order)\s*$",
    r"^\s*add all( (items|products|lines))?\s*$",
    r"^\s*add\s*$",
    r"^\s*(submit|go|update (basket|trolley|cart))\s*$",
)
#: A control that appends rows to the pad when it has fewer than the order needs.
QUICK_ORDER_ADD_ROW_NAME = re.compile(
    r"^\s*(add|more)\s+((more|another|a|an|extra|\d+)\s+)?(rows?|lines?|products?|items?)\s*$"
    r"|^\s*(more rows|more lines)\s*$",
    re.I,
)
#: How a pad says a code meant nothing to it.
QUICK_ORDER_REJECT_WORDS = (
    r"(?:not (?:been )?(?:found|recognised|recognized|available|valid|known|matched)"
    r"|unknown|invalid|no (?:such |matching )?(?:product|item|match)"
    r"|could ?n[o']t (?:be )?(?:found|find)|does ?n[o']t exist|discontinued|unavailable)"
)

_QUICK_ORDER_INPUTS_JS = r"""
() => {
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const labelOf = (el) => {
    const parts = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.title,
                   el.getAttribute('data-label')];
    if (el.labels) for (const l of el.labels) parts.push(l.innerText);
    const wrap = el.closest('label'); if (wrap) parts.push(wrap.innerText);
    const th = el.closest('td, th'); if (th && th.parentElement) {
      const idx = Array.from(th.parentElement.children).indexOf(th);
      const table = th.closest('table');
      const head = table ? table.querySelector('thead tr, tr') : null;
      if (head && head.children[idx]) parts.push(head.children[idx].innerText);
    }
    return parts.filter(Boolean).join(' ').replace(/\s+/g, ' ').toLowerCase();
  };
  document.querySelectorAll('[data-cafeops-qo]')
    .forEach((el) => el.removeAttribute('data-cafeops-qo'));
  const out = [];
  let n = 0;
  for (const el of Array.from(document.querySelectorAll('input, textarea'))) {
    if (!visible(el) || el.disabled || el.readOnly) continue;
    const tag = el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text').toLowerCase();
    if (tag !== 'textarea' && !['text', 'number', 'tel', 'search'].includes(tag)) continue;
    const label = labelOf(el);
    if (tag === 'search' && !/code|sku/.test(label)) continue;
    el.setAttribute('data-cafeops-qo', String(n));
    out.push({ i: n, tag, label });
    n++;
  }
  return out;
}
"""

_CODE_LABEL = re.compile(r"\b(code|sku|product|item|ref(erence)?|part|midas|article)\b", re.I)
_QTY_LABEL = re.compile(r"\b(qty|quantity|quant|cases?|units?|amount|packs?|number)\b", re.I)
_CODE_LIST_TEXTAREA = re.compile(
    r"\b(codes?|skus?|products?|items?|lines?|paste|list|bulk|multiple)\b", re.I
)


def _pad_inputs(page: Page) -> list[dict[str, Any]]:
    try:
        raw = page.evaluate(_QUICK_ORDER_INPUTS_JS)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise PortalStepFailed("could not inspect the quick-order pad's inputs") from exc
    return [dict(r) for r in raw] if isinstance(raw, list) else []


def _pad_rows(inputs: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(code_input, qty_input) index pairs in document order. Labelled inputs are
    paired code -> next qty; when nothing is labelled, plain text/number inputs
    are paired in alternation (the shape of a bare table of boxes)."""
    fields = [i for i in inputs if i["tag"] != "textarea"]
    pairs: list[tuple[int, int]] = []
    code: int | None = None
    labelled = False
    for f in fields:
        is_qty = f["tag"] == "number" or bool(_QTY_LABEL.search(f["label"]))
        is_code = not is_qty and bool(_CODE_LABEL.search(f["label"]))
        if is_code:
            labelled = True
            code = int(f["i"])
        elif is_qty and code is not None:
            labelled = True
            pairs.append((code, int(f["i"])))
            code = None
    if pairs or labelled:
        return pairs
    # Unlabelled: text, number, text, number ... or text, text, text, text.
    for a, b in zip(fields[0::2], fields[1::2], strict=False):
        if a["tag"] in ("text", "tel", "search") and b["tag"] in ("text", "number", "tel"):
            pairs.append((int(a["i"]), int(b["i"])))
    return pairs


def _pad_textarea(inputs: list[dict[str, Any]]) -> int | None:
    areas = [i for i in inputs if i["tag"] == "textarea"]
    if not areas:
        return None
    for a in areas:
        if _CODE_LIST_TEXTAREA.search(a["label"]):
            return int(a["i"])
    return int(areas[0]["i"]) if len(areas) == 1 else None


def _control_name(control: Locator) -> str | None:
    getters: tuple[Callable[[], str | None], ...] = (
        lambda: control.get_attribute("aria-label", timeout=800),
        lambda: control.inner_text(timeout=800),
        lambda: control.get_attribute("value", timeout=800),
    )
    for getter in getters:
        try:
            text = getter()
        except (PlaywrightTimeout, PlaywrightError):
            continue
        if text and text.strip():
            return " ".join(text.split())
    return None


def _find_submit(page: Page, names: re.Pattern[str], *, timeout_ms: int) -> Locator | None:
    # `input[type=submit]` has the button role and its value as name, so the role
    # locator covers it.
    return first_visible(
        page.get_by_role("button", name=names),
        page.get_by_role("link", name=names),
        timeout_ms=timeout_ms,
    )


def _click_named(page: Page, policy: PortalPolicy, control: Locator, what: str) -> str:
    name = _control_name(control)
    pat = policy.control_forbidden(name)
    if pat:
        raise PortalPolicyRefusal(f"{what} control {name!r} matches forbidden pattern {pat!r}")
    try:
        control.click(timeout=LOCATOR_TIMEOUT_MS)
    except (PlaywrightTimeout, PlaywrightError) as exc:
        raise PortalStepFailed(f"the {what} control {name!r} did not respond") from exc
    wait_settled(page)
    detect_needs_human(page)
    landed = policy.url_forbidden(page.url)
    if landed:
        raise PortalStepFailed(f"landed on {page.url} after {what} ({landed!r}); stopped")
    return name or what


def _rejections(text: str, skus: Sequence[str]) -> dict[str, str]:
    """`{sku: verbatim line}` for every code the page text says it did not
    recognise. The whole text line is kept so the note reads as the portal put it."""
    out: dict[str, str] = {}
    lines = [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]
    for sku in skus:
        esc = re.escape(sku)
        after = re.compile(rf"\b{esc}\b[^\n]{{0,80}}?{QUICK_ORDER_REJECT_WORDS}", re.I)
        before = re.compile(rf"{QUICK_ORDER_REJECT_WORDS}[^\n]{{0,80}}?\b{esc}\b", re.I)
        for ln in lines:
            if after.search(ln) or before.search(ln):
                out[sku] = ln[:200]
                break
    return out


def _fill_pad_rows(page: Page, rows: list[tuple[int, int]], batch: Sequence[BasketLine]) -> None:
    for (code_i, qty_i), line in zip(rows, batch, strict=False):
        code = page.locator(f"[data-cafeops-qo='{code_i}']").first
        qty = page.locator(f"[data-cafeops-qo='{qty_i}']").first
        try:
            code.click(timeout=LOCATOR_TIMEOUT_MS)
            code.fill(line.sku.strip(), timeout=LOCATOR_TIMEOUT_MS)
            code.press("Tab", timeout=LOCATOR_TIMEOUT_MS)
            qty.click(timeout=LOCATOR_TIMEOUT_MS)
            qty.fill(str(line.packs_wanted), timeout=LOCATOR_TIMEOUT_MS)
            qty.press("Tab", timeout=LOCATOR_TIMEOUT_MS)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            raise PortalStepFailed(
                f"could not fill the pad row for {line.sku}: {exc.__class__.__name__}"
            ) from exc
        seen_code = read_pad_value(code)
        seen_qty = parse_int(read_pad_value(qty))
        if (seen_code or "").strip().lower() != line.sku.strip().lower():
            raise PortalStepFailed(f"typed code {line.sku} but the box reads {seen_code!r}")
        if seen_qty != line.packs_wanted:
            raise PortalStepFailed(f"typed {line.packs_wanted} but the box reads {seen_qty!r}")


def read_pad_value(control: Locator) -> str | None:
    try:
        return control.input_value(timeout=LOCATOR_TIMEOUT_MS)
    except (PlaywrightTimeout, PlaywrightError):
        return None


def _grow_pad(page: Page, policy: PortalPolicy, needed: int) -> list[dict[str, Any]]:
    """Click "Add row"-like controls until the pad has `needed` rows or the control
    stops adding; returns the re-scanned inputs. Bounded: at most `needed` clicks."""
    inputs = _pad_inputs(page)
    for _ in range(needed):
        if len(_pad_rows(inputs)) >= needed:
            break
        more = first_visible(
            page.get_by_role("button", name=QUICK_ORDER_ADD_ROW_NAME),
            page.get_by_role("link", name=QUICK_ORDER_ADD_ROW_NAME),
            timeout_ms=800,
        )
        if more is None:
            break
        before = len(_pad_rows(inputs))
        _click_named(page, policy, more, "add-row")
        inputs = _pad_inputs(page)
        if len(_pad_rows(inputs)) <= before:
            break
    return inputs


def _match_basket_row(rows: tuple[dict[str, Any], ...], line: BasketLine) -> dict[str, Any] | None:
    sku = line.sku.strip().lower()
    if sku:
        for row in rows:
            hay = " ".join(str(row.get(k) or "") for k in ("name", "product_url", "sku", "text"))
            if re.search(rf"(?<![a-z0-9]){re.escape(sku)}(?![a-z0-9])", hay.lower()):
                return row
    return find_row(rows, name=line.ingredient_name)


def quick_order_generic(
    page: Page,
    policy: PortalPolicy,
    url: str,
    lines: Sequence[BasketLine],
    *,
    submit_names: Sequence[str] = QUICK_ORDER_SUBMIT_NAMES,
    second_stage_names: Sequence[str] = (),
    read_basket: Callable[[Page], tuple[tuple[dict[str, Any], ...], int | None]] | None = None,
) -> QuickOrderResult:
    """Tier 1 (base.SupplierPortal): fill the portal's product-code pad at `url` with
    every line's `sku` and `packs_wanted` in ONE form, submit it, and read back what
    the portal did. Shared by Booker, Brakes and the generic portal, which pass their
    own `submit_names` (accessible-name regexes, tried in order) and, for a pad whose
    first button only *finds* the products, `second_stage_names` for the button that
    then adds the found ones.

    Strategies, in order: rows of code/quantity inputs (labelled by name, id,
    placeholder, label or table header; unlabelled boxes pair alternately), else a
    single textarea taking `code,qty` per line. Neither recognisable ->
    `PortalStepFailed` and the model drives the pad with `quick_order_hints()`. A
    pad shorter than the order grows through an "Add row" control when there is
    one, else it is submitted in batches.

    Line statuses returned (the runner reads these):

    * `skipped`   -- empty `sku`, note "no product code" (the model adds it);
    * `already`   -- the basket already held the wanted packs (read before typing);
    * `added`     -- the basket shows the product after the pad ran; `packs_in_basket`
      and prices are from the basket, and the note says when the count differs from
      `packs_wanted` (a pad adds on top of what was there);
    * `not_found` -- the pad said so (verbatim in `note` and in `rejected`);
    * `failed`    -- everything else: present at another count before the pad ran
      (typing it again would add on top), or absent from the basket afterwards, or
      the basket could not be read after submitting (`result.note`).

    Nothing here touches checkout or slot booking: every click is policy-checked by
    accessible name and every navigation goes through `goto`.
    """
    result_lines: dict[int, BasketLine] = {}
    rejected: dict[int, str] = {}
    notes: list[str] = []

    to_type: list[BasketLine] = []
    for line in lines:
        if not line.sku or not line.sku.strip():
            result_lines[line.po_line_id] = replace(
                line, status="skipped", added_by="script", note="no product code"
            )
        else:
            to_type.append(line)
    if not to_type:
        return QuickOrderResult(
            lines=tuple(result_lines[ln.po_line_id] for ln in lines),
            note="no line carries a product code; nothing typed",
        )

    def _read_basket(p: Page) -> tuple[tuple[dict[str, Any], ...], int | None]:
        if read_basket is not None:
            return read_basket(p)
        goto(p, policy, policy.basket_url)
        dismiss_cookie_banner(p)
        rows, empty = basket_rows_generic(p)
        return ((), 0) if empty else (rows, read_subtotal_text(p))

    # What does the basket hold already? A pad adds on top, so a line that is
    # already there is not typed again.
    try:
        before_rows, _ = _read_basket(page)
    except PortalStepFailed as exc:
        before_rows = ()
        notes.append(f"basket not read before the pad ({exc}); every line was typed")
    still: list[BasketLine] = []
    for line in to_type:
        row = _match_basket_row(before_rows, line)
        if row is None:
            still.append(line)
            continue
        qty = int(row["qty"])
        if qty == line.packs_wanted:
            result_lines[line.po_line_id] = replace(
                line,
                packs_in_basket=qty,
                unit_price_seen_pence=row.get("unit_price_pence"),
                line_total_seen_pence=row.get("line_total_pence"),
                product_name_seen=row.get("name"),
                status="already",
                added_by="script",
                note="basket already held the wanted quantity",
            )
        else:
            result_lines[line.po_line_id] = replace(
                line,
                packs_in_basket=qty,
                unit_price_seen_pence=row.get("unit_price_pence"),
                line_total_seen_pence=row.get("line_total_pence"),
                product_name_seen=row.get("name"),
                status="failed",
                added_by="script",
                note=(
                    f"basket already holds {qty} pack(s); the pad would add on top, "
                    "so the quantity must be corrected on the basket page"
                ),
            )
    to_type = still
    if not to_type:
        wrong = sum(1 for ln in result_lines.values() if ln.status == "failed")
        summary = "every coded line was already in the basket; nothing typed"
        if wrong:
            summary += f" ({wrong} at another quantity, to correct on the basket page)"
        return QuickOrderResult(
            lines=tuple(result_lines[ln.po_line_id] for ln in lines),
            note="; ".join([summary, *notes]),
        )

    submit_re = re.compile("|".join(f"(?:{p})" for p in submit_names), re.I)
    second_re = (
        re.compile("|".join(f"(?:{p})" for p in second_stage_names), re.I)
        if second_stage_names
        else None
    )
    page_texts: list[str] = []
    submitted_with: str | None = None

    remaining = list(to_type)
    batches = 0
    while remaining:
        batches += 1
        if batches > 10:
            raise PortalStepFailed("the pad took more than 10 submissions; giving up")
        goto(page, policy, url)
        dismiss_cookie_banner(page)
        detect_needs_human(page)
        inputs = _pad_inputs(page)
        rows = _pad_rows(inputs)
        if rows and len(rows) < len(remaining):
            inputs = _grow_pad(page, policy, len(remaining))
            rows = _pad_rows(inputs)
        if rows:
            batch = remaining[: len(rows)]
            _fill_pad_rows(page, rows, batch)
        else:
            area_i = _pad_textarea(inputs)
            if area_i is None:
                raise PortalStepFailed(
                    "quick-order pad not recognised: no code/quantity rows and no "
                    f"code-list textarea at {url}"
                )
            batch = remaining
            area = page.locator(f"[data-cafeops-qo='{area_i}']").first
            body = "\n".join(f"{ln.sku.strip()},{ln.packs_wanted}" for ln in batch)
            try:
                area.click(timeout=LOCATOR_TIMEOUT_MS)
                area.fill(body, timeout=LOCATOR_TIMEOUT_MS)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                raise PortalStepFailed("could not fill the code-list textarea") from exc
            if (read_pad_value(area) or "").strip() != body:
                raise PortalStepFailed("filled the code-list textarea but it reads differently")
        submit = _find_submit(page, submit_re, timeout_ms=LOCATOR_TIMEOUT_MS)
        if submit is None:
            raise PortalStepFailed("no submit control recognised on the quick-order pad")
        submitted_with = _click_named(page, policy, submit, "pad submit")
        page_texts.append(page_text(page))
        if second_re is not None:
            second = _find_submit(page, second_re, timeout_ms=2_500)
            if second is not None:
                _click_named(page, policy, second, "pad add")
                page_texts.append(page_text(page))
        remaining = remaining[len(batch) :]

    # What did the pad say? Then what does the basket say?
    said = _rejections("\n".join(page_texts), [ln.sku.strip() for ln in to_type])
    try:
        after_rows, _ = _read_basket(page)
        basket_read = True
    except PortalStepFailed as exc:
        after_rows = ()
        basket_read = False
        notes.append(f"pad submitted but the basket could not be read: {exc}")

    for line in to_type:
        sku = line.sku.strip()
        row = _match_basket_row(after_rows, line) if basket_read else None
        if row is not None:
            qty = int(row["qty"])
            result_lines[line.po_line_id] = replace(
                line,
                packs_in_basket=qty,
                unit_price_seen_pence=row.get("unit_price_pence"),
                line_total_seen_pence=row.get("line_total_pence"),
                product_name_seen=row.get("name"),
                status="added",
                added_by="script",
                note=""
                if qty == line.packs_wanted
                else f"basket shows {qty} pack(s), wanted {line.packs_wanted}",
            )
        elif sku in said:
            rejected[line.po_line_id] = said[sku]
            result_lines[line.po_line_id] = replace(
                line, status="not_found", added_by="script", note=said[sku]
            )
        elif not basket_read:
            result_lines[line.po_line_id] = replace(
                line,
                status="failed",
                added_by="script",
                note="pad submitted but the basket could not be read; unverified",
            )
        else:
            result_lines[line.po_line_id] = replace(
                line,
                status="failed",
                added_by="script",
                note="not in the basket after the pad was submitted, and the pad gave no reason",
            )

    typed = len(to_type)
    added = sum(1 for ln in result_lines.values() if ln.status == "added")
    summary = f"{typed} code(s) typed, submitted with {submitted_with!r}: {added} in the basket"
    if rejected:
        summary += f", {len(rejected)} rejected by the pad"
    return QuickOrderResult(
        lines=tuple(result_lines[ln.po_line_id] for ln in lines),
        rejected=rejected,
        note="; ".join([summary, *notes]),
    )


def quick_order_hints_text(
    *,
    label: str,
    pad_url: str,
    basket_url: str,
    columns: str,
    submit: str,
    extra: str = "",
) -> str:
    """The model-facing description of a quick-order pad, one shape for every
    portal that has one."""
    parts = [
        f"{label}: the quick-order pad is at {pad_url} (open it from the site's 'Quick "
        "order' / 'Order by code' link if that URL is wrong; it is unverified).",
        f"Columns: {columns}. One row per order line: type the product code from the "
        "order line in the code box and the number of packs (cases) in the quantity box. "
        "Use every row you need; press 'Add row' / 'More lines' if the pad is short.",
        f"Submit: press {submit}. Codes the pad does not recognise are shown next to "
        "their row as 'not found' / 'unknown' -- report those, do not guess a different "
        "product.",
        f"Finish on the basket page at {basket_url} with every line and the subtotal visible.",
        "Never touch: Checkout, Book slot, Confirm/Submit/Send order, delivery slots, "
        "payment, saved cards, CSV/file upload, or account settings. If asked to sign "
        "in, enter a code or solve a CAPTCHA, stop and say needs_human.",
    ]
    if extra.strip():
        parts.append(extra.strip())
    return "\n".join(parts)


__all__ = [
    "ACCOUNT_NAME",
    "ADD_BUTTON_NAME",
    "ASIN_IN_URL",
    "BROWSER_USER_AGENT",
    "EMPTY_BASKET_TEXT",
    "GREETING_NAME",
    "LOCATOR_TIMEOUT_MS",
    "MONEY_ANY",
    "NAV_TIMEOUT_MS",
    "QTY_CONTROL_CSS",
    "QUICK_ORDER_ADD_ROW_NAME",
    "QUICK_ORDER_REJECT_WORDS",
    "QUICK_ORDER_SUBMIT_NAMES",
    "SIGN_IN_NAME",
    "SIGN_OUT_NAME",
    "SUBTOTAL_LABELS",
    "HeuristicPortal",
    "asin_from_url",
    "basket_rows_generic",
    "click_increment",
    "detect_needs_human",
    "dismiss_cookie_banner",
    "ensure_on_portal",
    "fetch_product_json",
    "find_add_button",
    "find_quantity_control",
    "find_row",
    "first_visible",
    "goto",
    "mask_label",
    "names_match",
    "page_text",
    "parse_int",
    "parse_pence",
    "product_json_url",
    "quick_order_generic",
    "quick_order_hints_text",
    "read_pad_value",
    "read_quantity",
    "read_subtotal_text",
    "set_quantity",
    "shopify_variant_id",
    "wait_settled",
]
